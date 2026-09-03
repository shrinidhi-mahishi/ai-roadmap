"""
Multi-Agent Systems: coordination patterns for splitting work across specialized
agents. Covers message passing, worker isolation, supervisor routing, fan-out/fan-in,
and consensus voting.
"""

from __future__ import annotations
import random
from dataclasses import dataclass
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed


# ═══ Section 1: Structured Message Passing ═══

@dataclass
class AgentMessage:
    """Typed message for inter-agent communication. Every hop carries trace_id
    so the full delegation chain is auditable (OWASP ASI07)."""
    sender: str
    receiver: str
    content: Any
    msg_type: str = "task"   # task | result | error
    trace_id: str = ""
    hop: int = 0             # track depth to prevent ping-pong

    def reply(self, content: Any, msg_type: str = "result") -> "AgentMessage":
        return AgentMessage(self.receiver, self.sender, content, msg_type,
                            self.trace_id, self.hop + 1)


# ═══ Section 2: Worker Agent with Tool Access ═══

@dataclass
class Tool:
    """A tool that a worker can invoke -- scoped to one verb."""
    name: str
    func: Any  # callable

    def call(self, **kwargs) -> str:
        return self.func(**kwargs)


class WorkerAgent:
    """Worker that does one thing well. Each worker has its own tool allowlist
    (OWASP LLM06) and isolated context (just local state here)."""

    def __init__(self, name: str, specialty: str, tools: list[Tool]):
        self.name = name
        self.specialty = specialty
        self.tools = {t.name: t for t in tools}

    def handle(self, msg: AgentMessage) -> AgentMessage:
        results = [f"[{self.name}] {t.call(query=msg.content)}"
                   for n, t in self.tools.items() if n in msg.content.lower()]
        if not results:
            results.append(f"[{self.name}] Processed: {msg.content[:60]}")
        return msg.reply(content="\n".join(results))


def demo_worker():
    """Show a worker agent handling a task with tool access."""
    search_tool = Tool("search", lambda query: f"Found 3 results for '{query}'")
    worker = WorkerAgent("researcher", "web_search", [search_tool])
    msg = AgentMessage("supervisor", "researcher", "search for AI agent patterns",
                       trace_id="trace-001")
    reply = worker.handle(msg)
    print(f"Worker reply (hop {reply.hop}): {reply.content}")


# ═══ Section 3: Supervisor Pattern ═══

class SupervisorAgent:
    """Supervisor routes tasks to workers and synthesizes results.
    Key invariant: supervisor decides WHO runs next (control plane),
    but never executes tools directly (data plane separation)."""

    def __init__(self, name: str, workers: list[WorkerAgent], max_hops: int = 5):
        self.name = name
        self.workers = {w.specialty: w for w in workers}
        self.max_hops = max_hops

    def route(self, task: str) -> str:
        """Classify task and pick a specialist (production: LLM call)."""
        matched = [s for s, w in self.workers.items()
                   if s in task.lower() or any(t in task.lower() for t in w.tools)]
        return matched[0] if matched else list(self.workers.keys())[0]

    def run(self, task: str, trace_id: str = "trace-001") -> str:
        specialty = self.route(task)
        worker = self.workers[specialty]
        msg = AgentMessage(self.name, worker.name, task, trace_id=trace_id)
        if msg.hop >= self.max_hops:
            return f"[{self.name}] Max hops reached -- escalating to human."
        result = worker.handle(msg)
        return f"[{self.name}] Routed to '{specialty}' -> {result.content}"


def demo_supervisor():
    """Demonstrate supervisor routing tasks to specialized workers."""
    search_tool = Tool("search", lambda query: f"3 results for '{query}'")
    write_tool = Tool("write", lambda query: f"Draft written for '{query}'")

    researcher = WorkerAgent("researcher", "search", [search_tool])
    writer = WorkerAgent("writer", "write", [write_tool])

    supervisor = SupervisorAgent("lead", [researcher, writer])

    tasks = [
        "search for recent AI safety papers",
        "write a summary of the findings",
    ]
    for task in tasks:
        result = supervisor.run(task)
        print(result)


# ═══ Section 4: Fan-Out / Fan-In (Parallel Execution) ═══

def fan_out_fan_in(subtasks: list[str], workers: list[WorkerAgent],
                   trace_id: str = "trace-002") -> list[str]:
    """Execute subtasks in parallel across workers, then aggregate.

    This mirrors LangGraph's Send() pattern: each subtask gets its own
    isolated context. Wall-clock = max(workers), not sum(workers).
    Anthropic reports -90% wall-clock from parallel subagent waves."""

    results = []

    # ThreadPoolExecutor simulates parallel subagent execution
    with ThreadPoolExecutor(max_workers=len(subtasks)) as pool:
        futures = {}
        for i, subtask in enumerate(subtasks):
            worker = workers[i % len(workers)]
            msg = AgentMessage("supervisor", worker.name, subtask,
                               trace_id=trace_id)
            futures[pool.submit(worker.handle, msg)] = subtask

        for future in as_completed(futures):
            reply = future.result()
            results.append(reply.content)

    return results


def demo_fan_out():
    """Show parallel fan-out to multiple workers, then aggregate."""
    tool = Tool("search", lambda query: f"Result for '{query}'")
    workers = [WorkerAgent(f"sub_{i}", "search", [tool]) for i in range(3)]
    subtasks = [
        "search semiconductor supply chain 2025",
        "search semiconductor pricing trends",
        "search semiconductor demand forecast",
    ]
    results = fan_out_fan_in(subtasks, workers)
    # Fan-in: synthesize all results
    synthesis = f"Synthesized {len(results)} parallel results:\n"
    for r in results:
        synthesis += f"  - {r}\n"
    print(synthesis)


# ═══ Section 5: Consensus / Voting ═══

@dataclass
class Vote:
    """One agent's answer plus confidence score."""
    agent_name: str
    answer: str
    confidence: float  # 0.0 to 1.0


def consensus_vote(question: str, agents: list[WorkerAgent],
                   strategy: str = "majority") -> dict:
    """Multiple agents answer independently, then pick the best.
    majority = count answers; weighted = sum confidence per answer."""
    votes: list[Vote] = []
    for agent in agents:
        reply = agent.handle(AgentMessage("voter", agent.name, question))
        votes.append(Vote(agent.name, reply.content, random.uniform(0.5, 1.0)))

    # Aggregate by unique answer
    agg: dict[str, float] = {}
    for v in votes:
        agg[v.answer] = agg.get(v.answer, 0) + (1 if strategy == "majority" else v.confidence)
    winner = max(agg, key=agg.get)
    return {"winner": winner, "score": round(agg[winner], 2),
            "total_votes": len(votes), "strategy": strategy}


def demo_consensus():
    tool = Tool("analyze", lambda query: random.choice([
        "Python is best for ML", "Python is best for ML", "Julia is faster"]))
    agents = [WorkerAgent(f"expert_{i}", "analyze", [tool]) for i in range(5)]
    for strat in ["majority", "weighted"]:
        r = consensus_vote("Best language for ML?", agents, strat)
        print(f"  {strat}: score={r['score']}/{r['total_votes']} -> {r['winner'][:70]}")


# ═══ Main: Run All Demos ═══

if __name__ == "__main__":
    print("=" * 60)
    print("MULTI-AGENT SYSTEMS -- Interview Prep Demos")
    print("=" * 60)

    print("\n--- 1. Worker Agent with Tool Access ---")
    demo_worker()

    print("\n--- 2. Supervisor Pattern (Route -> Delegate -> Synthesize) ---")
    demo_supervisor()

    print("\n--- 3. Fan-Out / Fan-In (Parallel Subtasks) ---")
    demo_fan_out()

    print("\n--- 4. Consensus / Voting ---")
    demo_consensus()

    print("\n--- 5. Message Passing Recap ---")
    msg = AgentMessage("user", "supervisor", "research AI trends", trace_id="t-99")
    reply = msg.reply("Here are 5 trends", msg_type="result")
    print(f"Original: {msg.sender} -> {msg.receiver} (hop {msg.hop})")
    print(f"Reply:    {reply.sender} -> {reply.receiver} (hop {reply.hop})")
    print(f"Trace ID preserved: {reply.trace_id}")

    print("\n" + "=" * 60)
    print("Key takeaways:")
    print("  - Supervisor = control plane; workers = data plane")
    print("  - Fan-out cuts wall-clock by up to 90% (Anthropic)")
    print("  - Voting/consensus = collaboration as verification")
    print("  - Every message carries trace_id for audit")
    print("  - Max hops prevent infinite ping-pong")
