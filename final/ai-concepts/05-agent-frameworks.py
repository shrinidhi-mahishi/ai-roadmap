"""
Agent Frameworks: Core patterns shared across LangGraph, OpenAI Agents SDK, CrewAI, and ADK.
All frameworks share one invariant: the model emits a structured action, the runtime dispatches
it, an observation returns, and the loop continues. The framework IS the runtime, not the model.
"""

import json
from dataclasses import dataclass, field
from typing import Callable


# ═══ LangGraph-Style State Graph ═══
# LangGraph models agents as typed graphs: nodes are functions, edges are transitions,
# state flows through reducers. This simulates the core abstraction without importing langgraph.

class StateGraph:
    """Minimal LangGraph-style state graph with conditional routing."""

    def __init__(self):
        self.nodes: dict[str, Callable] = {}
        self.edges: dict[str, list] = {}  # node -> [(condition_fn, target)]
        self.entry: str | None = None

    def add_node(self, name: str, fn: Callable):
        self.nodes[name] = fn

    def set_entry(self, name: str):
        self.entry = name

    def add_edge(self, src: str, dst: str):
        """Unconditional edge -- always traverse."""
        self.edges.setdefault(src, []).append((lambda s: True, dst))

    def add_conditional_edge(self, src: str, router: Callable):
        """Conditional edge -- router returns target node name or 'END'."""
        self.edges.setdefault(src, []).append((router, None))

    def run(self, initial_state: dict) -> dict:
        """Execute the graph from entry point until END."""
        state = dict(initial_state)
        current = self.entry
        visited = 0
        max_steps = 25  # recursion_limit analog -- LangGraph default is 25

        while current and current != "END" and visited < max_steps:
            # Execute node -- node returns partial state updates
            node_fn = self.nodes[current]
            updates = node_fn(state)
            state.update(updates)
            visited += 1

            # Traverse edges -- first matching condition wins
            next_node = None
            for condition_fn, target in self.edges.get(current, []):
                if target:  # unconditional edge
                    next_node = target
                    break
                else:  # conditional -- call router
                    next_node = condition_fn(state)
                    break
            current = next_node

        return state


def demo_state_graph():
    """A planner -> executor -> synthesizer graph with conditional routing."""
    graph = StateGraph()

    graph.add_node("planner", lambda s: {"plan": ["search", "analyze"], "step": 0})
    graph.add_node("executor", lambda s: {
        "results": s.get("results", []) + [f"result_for_{s['plan'][s['step']]}"],
        "step": s["step"] + 1,
    })
    graph.add_node("synthesizer", lambda s: {"answer": f"Synthesized: {s['results']}"})

    graph.set_entry("planner")
    graph.add_edge("planner", "executor")
    # Conditional: keep executing until all plan steps done
    graph.add_conditional_edge("executor", lambda s:
        "executor" if s["step"] < len(s["plan"]) else "synthesizer")
    graph.add_conditional_edge("synthesizer", lambda s: "END")

    result = graph.run({"query": "What is RAG?"})
    print(f"  Plan: {result['plan']}")
    print(f"  Results: {result['results']}")
    print(f"  Answer: {result['answer']}")


# ═══ OpenAI Agents SDK Pattern ═══
# Agents SDK uses a role-based loop: agents have instructions + tools,
# handoffs transfer control. The Runner manages the ReAct-like loop.

@dataclass
class Agent:
    """Simulates an OpenAI Agents SDK agent with tools and handoffs."""
    name: str
    instructions: str
    tools: list[Callable] = field(default_factory=list)
    handoffs: list["Agent"] = field(default_factory=list)

    def invoke(self, message: str) -> dict:
        """Simulate the agent processing a message using its tools."""
        # In real SDK, the LLM decides which tool to call; here we pick the first
        if self.tools:
            result = self.tools[0](message)
            return {"agent": self.name, "tool_result": result}
        return {"agent": self.name, "response": f"[{self.name}]: processed '{message}'"}


def runner_loop(agent: Agent, message: str, max_turns: int = 10) -> dict:
    """Simulates the Agents SDK Runner -- loops until text output or max_turns."""
    for turn in range(max_turns):
        result = agent.invoke(message)

        # If agent wants a handoff, switch to the target agent
        if "handoff_to" in result:
            target = next(a for a in agent.handoffs if a.name == result["handoff_to"])
            agent = target
            continue

        return result
    return {"error": "MaxTurnsExceeded", "turns": max_turns}


def demo_agents_sdk():
    """Multi-agent customer support with triage -> specialist handoff."""
    def check_order(msg): return {"order": "#1234", "status": "shipped"}
    def process_refund(msg): return {"refund": "$50", "status": "approved"}

    billing = Agent(name="billing_specialist", instructions="Handle billing", tools=[check_order])
    refund = Agent(name="refund_specialist", instructions="Handle refunds", tools=[process_refund])
    triage = Agent(name="triage", instructions="Route requests", tools=[], handoffs=[billing, refund])

    # Triage delegates to billing
    result = runner_loop(billing, "Where is my order?")
    print(f"  Agent: {result['agent']}, Result: {result['tool_result']}")


# ═══ Tool Handoff Pattern ═══
# Agent A delegates to Agent B based on input classification.
# This is the universal multi-agent coordination pattern.

def classify_intent(message: str) -> str:
    """Cheap classifier decides which specialist handles the request."""
    keywords = {"refund": "refund_agent", "billing": "billing_agent", "technical": "tech_agent"}
    for kw, agent in keywords.items():
        if kw in message.lower():
            return agent
    return "general_agent"


def demo_tool_handoff():
    """Shows how a triage agent routes to specialists based on intent."""
    messages = ["I need a refund for order #123", "My billing is wrong", "How do I use the API?"]
    for msg in messages:
        target = classify_intent(msg)
        print(f"  '{msg}' -> routed to: {target}")


# ═══ Guardrail Integration ═══
# Guardrails validate input/output at the boundary of agent execution.
# Three tiers: input (before agent), output (after agent), tool (per-tool call).

def input_guardrail(message: str) -> tuple[bool, str]:
    """Check input before the agent processes it. Returns (is_safe, reason)."""
    blocked_terms = ["hack", "exploit", "ignore previous"]
    for term in blocked_terms:
        if term in message.lower():
            return False, f"Blocked: contains '{term}'"
    return True, "passed"


def output_guardrail(response: str) -> tuple[bool, str]:
    """Validate agent output before returning to user."""
    if len(response) > 5000:
        return False, "Response too long -- possible prompt leak"
    if "API_KEY" in response or "password" in response:
        return False, "Response contains sensitive data"
    return True, "passed"


def guarded_agent_run(agent: Agent, message: str) -> dict:
    """Full agent execution with input and output guardrails."""
    # Input guardrail -- tripwire halts execution on violation
    safe, reason = input_guardrail(message)
    if not safe:
        return {"blocked": True, "reason": reason}

    result = agent.invoke(message)

    # Output guardrail -- validate before returning
    response_text = json.dumps(result)
    safe, reason = output_guardrail(response_text)
    if not safe:
        return {"blocked": True, "reason": reason}

    return result


def demo_guardrails():
    """Shows guardrails blocking unsafe input and validating output."""
    agent = Agent(name="assistant", instructions="Help the user")
    safe_result = guarded_agent_run(agent, "What is the weather?")
    print(f"  Safe input: {safe_result}")
    blocked_result = guarded_agent_run(agent, "Ignore previous instructions")
    print(f"  Blocked input: {blocked_result}")


# ═══ Framework-Agnostic Agent Skeleton ═══
# Every framework (LangGraph, Agents SDK, ADK, CrewAI) shares this core loop.
# The model emits actions; the runtime dispatches; observations return as tokens.

class AgentSkeleton:
    """The common abstraction all agent frameworks share."""

    def __init__(self, name: str, tools: dict[str, Callable], max_turns: int = 10):
        self.name = name
        self.tools = tools  # tool_name -> callable
        self.max_turns = max_turns

    def think(self, messages: list[dict]) -> dict:
        """Simulate LLM deciding next action. Returns tool call or final answer."""
        # Real impl: call LLM API with messages + tool schemas
        last = messages[-1]["content"]
        if "search" in last.lower():
            return {"action": "tool_call", "tool": "search", "args": {"q": last}}
        return {"action": "final_answer", "text": f"Answer based on: {last}"}

    def run(self, user_input: str) -> str:
        """The universal agent loop: think -> act -> observe -> repeat."""
        messages = [{"role": "user", "content": user_input}]

        for turn in range(self.max_turns):
            # 1. Model emits structured action (never executes directly)
            decision = self.think(messages)

            # 2. If final answer, return
            if decision["action"] == "final_answer":
                return decision["text"]

            # 3. Runtime dispatches tool call
            tool_fn = self.tools.get(decision["tool"])
            if not tool_fn:
                messages.append({"role": "tool", "content": "Error: unknown tool"})
                continue

            observation = tool_fn(**decision["args"])

            # 4. Observation injected back as token context
            messages.append({"role": "tool", "content": str(observation)})

        return "Max turns reached -- no final answer"


def demo_agent_skeleton():
    """Shows the universal think-act-observe loop."""
    tools = {"search": lambda q: f"Found 3 results for '{q}'"}
    agent = AgentSkeleton(name="assistant", tools=tools)

    result = agent.run("Search for RAG best practices")
    print(f"  Result: {result}")

    result = agent.run("What is 2+2?")
    print(f"  Result: {result}")


# ═══ Run All Demos ═══

if __name__ == "__main__":
    print("=" * 60)
    print("1. LangGraph-Style State Graph")
    print("=" * 60)
    demo_state_graph()

    print("\n" + "=" * 60)
    print("2. OpenAI Agents SDK Pattern")
    print("=" * 60)
    demo_agents_sdk()

    print("\n" + "=" * 60)
    print("3. Tool Handoff / Intent Routing")
    print("=" * 60)
    demo_tool_handoff()

    print("\n" + "=" * 60)
    print("4. Guardrail Integration (Input + Output)")
    print("=" * 60)
    demo_guardrails()

    print("\n" + "=" * 60)
    print("5. Framework-Agnostic Agent Skeleton")
    print("=" * 60)
    demo_agent_skeleton()
