"""
Deep Agents Architecture & Harness -- Interview Prep Code Snippets

Covers the agent harness pattern (LLM + tools + loop), state graph definitions
(LangGraph style), checkpoint/resume, tool registration and dispatch, loop
fuses (max iterations, cost cap), and error handling in the agent loop.
All examples are self-contained with stubs for LLM and tool backends.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# =============================================================================
# --- Section 1: Basic Agent Harness (LLM + Tools + Loop) --------------------
# =============================================================================
# The 2026 industry consensus: the model is commodity; the harness is moat.
# Two teams using the identical model can see a 40-point difference in task
# completion rates based purely on harness design.
#
# Three-layer hierarchy (Deep Agents):
#   Layer 3: create_deep_agent() -- full harness (VFS, sub-agents, skills)
#   Layer 2: create_agent()      -- minimal harness (loop + tools + middleware)
#   Layer 1: LangGraph           -- graph runtime (nodes, edges, checkpoints)

@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_name: str
    output: Any
    success: bool = True
    error: str = ""
    execution_ms: float = 0.0


@dataclass
class LLMResponse:
    """Structured response from the LLM."""
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"  # "stop" | "tool_use" | "length" | "content_filter"
    input_tokens: int = 0
    output_tokens: int = 0


class StubLLM:
    """Stub LLM that simulates tool-calling behavior.

    In production, this is a ChatModel from langchain, openai SDK, or
    anthropic SDK.  The harness cares about the interface, not the provider.

    Multi-provider model strings (Deep Agents pattern):
      "anthropic:claude-sonnet-4-6"
      "openai:gpt-5.5"
      "google_genai:gemini-3.6-flash"
      "ollama:north-mini-code-1.0"
    """

    def __init__(self, plan: list[LLMResponse] | None = None):
        self._plan = plan or []
        self._call_count = 0

    def invoke(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        """Invoke the LLM with messages and available tools."""
        self._call_count += 1
        if self._plan and self._call_count <= len(self._plan):
            return self._plan[self._call_count - 1]
        # Default: return a final answer
        return LLMResponse(content="Final answer.", finish_reason="stop",
                           input_tokens=500, output_tokens=100)


class AgentHarness:
    """Minimal agent harness: LLM + tools + loop.

    This is the Layer 2 (create_agent) equivalent.  The loop is:
      1. LLM sees messages + tool schemas
      2. LLM returns text (done) or tool_calls (continue)
      3. Execute tool calls, append results
      4. Back to step 1

    The harness owns the loop, NOT the model.  The model proposes;
    deterministic code disposes.
    """

    def __init__(
        self,
        llm: StubLLM,
        tools: dict[str, Callable],
        *,
        max_turns: int = 10,
        max_cost_usd: float = 1.0,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_turns = max_turns
        self.max_cost_usd = max_cost_usd
        self.messages: list[dict] = []
        self.total_cost_usd = 0.0
        self.turn_count = 0

    def _build_tool_schemas(self) -> list[dict]:
        """Build tool schemas for the LLM.  In production these are JSON
        Schema definitions; here we use simple stubs."""
        return [{"name": name, "description": f"Tool: {name}"}
                for name in self.tools]

    def _execute_tool(self, tool_call: dict) -> ToolResult:
        """Execute a single tool call through the registered dispatch."""
        name = tool_call["name"]
        args = tool_call.get("args", {})
        start = time.monotonic()

        if name not in self.tools:
            return ToolResult(
                tool_name=name,
                output=None,
                success=False,
                error=f"Unknown tool: {name}",
                execution_ms=0.0,
            )

        try:
            output = self.tools[name](**args)
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(tool_name=name, output=output, execution_ms=elapsed)
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(
                tool_name=name, output=None, success=False,
                error=str(e), execution_ms=elapsed,
            )

    def _estimate_cost(self, response: LLMResponse) -> float:
        """Estimate cost for a single LLM call.  In production, use the
        actual model pricing (e.g., Sonnet 4.6: $3 input, $15 output /MTok)."""
        return (response.input_tokens * 3.0 + response.output_tokens * 15.0) / 1_000_000

    def run(self, user_message: str) -> dict:
        """Run the agent loop to completion.

        Returns the final state including the answer, turn count, and cost.
        """
        self.messages.append({"role": "user", "content": user_message})
        schemas = self._build_tool_schemas()

        while True:
            # --- Loop fuse: max turns ---
            if self.turn_count >= self.max_turns:
                return {
                    "status": "max_turns_exceeded",
                    "answer": "Exceeded maximum turns.",
                    "turns": self.turn_count,
                    "cost_usd": self.total_cost_usd,
                }

            self.turn_count += 1
            response = self.llm.invoke(self.messages, schemas)

            # --- Loop fuse: cost cap ---
            call_cost = self._estimate_cost(response)
            self.total_cost_usd += call_cost
            if self.total_cost_usd > self.max_cost_usd:
                return {
                    "status": "cost_cap_exceeded",
                    "answer": "Exceeded cost budget.",
                    "turns": self.turn_count,
                    "cost_usd": self.total_cost_usd,
                }

            # Check if the model is done (no tool calls)
            if response.finish_reason == "stop" and not response.tool_calls:
                self.messages.append({"role": "assistant", "content": response.content})
                return {
                    "status": "complete",
                    "answer": response.content,
                    "turns": self.turn_count,
                    "cost_usd": self.total_cost_usd,
                }

            # Execute tool calls
            self.messages.append({
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            })

            for tc in response.tool_calls:
                result = self._execute_tool(tc)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", str(uuid.uuid4())),
                    "name": result.tool_name,
                    "content": json.dumps(result.output) if result.success
                               else f"ERROR: {result.error}",
                })


# =============================================================================
# --- Section 2: State Graph Definition (LangGraph Style) --------------------
# =============================================================================
# LangGraph is the runtime: nodes (functions), edges (routing), state
# (TypedDict/dataclass), checkpointing, streaming, interrupts.
#
# Key concepts:
#   - Nodes: functions that take state and return partial state updates
#   - Edges: conditional routing between nodes
#   - State: shared data structure, updated via reducers
#   - Super-step: one node execution (ReAct tool cycle ~ 2 super-steps)

class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class GraphState:
    """State object for the agent graph.

    In LangGraph, this is a TypedDict with Annotated reducers.
    Messages use a DeltaChannel reducer (langgraph>=1.2) so growth
    stays linear, not quadratic.
    """
    messages: list[dict] = field(default_factory=list)
    current_node: str = "start"
    plan: list[str] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    iteration: int = 0
    status: str = "running"


@dataclass
class Node:
    """A node in the state graph -- a function that transforms state."""
    name: str
    fn: Callable[[GraphState], GraphState]
    status: NodeStatus = NodeStatus.PENDING


@dataclass
class Edge:
    """An edge connecting two nodes, optionally with a condition."""
    source: str
    target: str
    condition: Callable[[GraphState], bool] | None = None


class StateGraph:
    """Minimal state graph implementation (LangGraph-style).

    In production, use langgraph.graph.StateGraph directly.
    This demonstrates the core pattern: define nodes + edges,
    then compile and invoke.

    Deep Agents sits on top of this:
      create_deep_agent() -> CompiledStateGraph
    """

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: list[Edge] = []
        self._entry_point: str = ""

    def add_node(self, name: str, fn: Callable[[GraphState], GraphState]) -> None:
        self._nodes[name] = Node(name=name, fn=fn)

    def add_edge(self, source: str, target: str,
                 condition: Callable[[GraphState], bool] | None = None) -> None:
        self._edges.append(Edge(source=source, target=target, condition=condition))

    def set_entry_point(self, name: str) -> None:
        self._entry_point = name

    def _get_next_node(self, current: str, state: GraphState) -> str | None:
        """Route to next node based on edges and conditions."""
        for edge in self._edges:
            if edge.source == current:
                if edge.condition is None or edge.condition(state):
                    return edge.target
        return None

    def invoke(self, state: GraphState, *, max_steps: int = 100) -> GraphState:
        """Execute the graph from entry point to completion.

        Each step = one super-step.  Bare LangGraph default is 25 super-steps.
        Deep Agents binds 9,999 (sentinel dodge vs merge_configs dropping 10,000).
        Production hop caps belong in application state, not recursion_limit.
        """
        state.current_node = self._entry_point

        for step in range(max_steps):
            node = self._nodes.get(state.current_node)
            if node is None:
                state.status = "error"
                state.error = f"Unknown node: {state.current_node}"
                break

            # Remember which node ran (node functions should not mutate
            # current_node -- routing is the graph's job)
            ran_node = state.current_node

            # Execute node (one super-step)
            node.status = NodeStatus.RUNNING
            try:
                state = node.fn(state)
                node.status = NodeStatus.COMPLETED
            except Exception as e:
                node.status = NodeStatus.FAILED
                state.error = str(e)
                state.status = "error"
                break

            # Check for terminal state
            if state.status != "running":
                break

            # Route to next node based on the node that just ran
            next_node = self._get_next_node(ran_node, state)
            if next_node is None:
                state.status = "complete"
                break
            state.current_node = next_node
            state.iteration += 1

        return state


# Build an example graph: plan -> execute -> evaluate -> (loop or done)
def plan_node(state: GraphState) -> GraphState:
    """Planner: decompose the task into steps."""
    state.plan = ["search", "analyze", "synthesize"]
    state.messages.append({"role": "system", "content": f"Plan: {state.plan}"})
    return state


def execute_node(state: GraphState) -> GraphState:
    """Executor: run the current step in the plan."""
    if not state.plan:
        state.status = "complete"
        return state
    step = state.plan.pop(0)
    state.results[step] = f"{step}_result_ok"
    state.messages.append({"role": "tool", "content": f"Executed: {step}"})
    return state


def evaluate_node(state: GraphState) -> GraphState:
    """Evaluator: decide if we need more steps or are done.

    Does NOT set current_node -- edge routing handles the loop-back.
    The graph's conditional edge from evaluate -> execute fires when
    the plan still has steps.
    """
    if not state.plan:
        state.status = "complete"
    return state


def build_example_graph() -> StateGraph:
    """Build a plan-and-execute graph with evaluation loop."""
    graph = StateGraph()
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("evaluate", evaluate_node)

    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "evaluate")
    # Conditional: loop back to execute if plan has more steps
    graph.add_edge("evaluate", "execute", condition=lambda s: len(s.plan) > 0)

    graph.set_entry_point("plan")
    return graph


# =============================================================================
# --- Section 3: Checkpoint / Resume Pattern ---------------------------------
# =============================================================================
# Checkpointing saves state at each super-step boundary.  Resume only from
# a checkpoint, not mid-node.
#
# Checkpointer options:
#   MemorySaver:   $0, total loss on crash (dev/test only)
#   PostgresSaver: ACID, point-in-time recovery (~$50/mo, production)
#   DynamoDBSaver: auto-scaling, multi-region (AWS-native)
#
# Checkpointing is NOT true durable execution.  True durable execution
# (Temporal) guarantees exactly-once + side-effect deduplication.

@dataclass
class Checkpoint:
    """A state snapshot at a super-step boundary."""
    checkpoint_id: str
    thread_id: str
    state: dict          # serialized GraphState
    step: int
    timestamp: float = field(default_factory=time.time)
    parent_id: str = ""  # for time-travel / fork


class CheckpointStore:
    """In-memory checkpoint store (MemorySaver equivalent).

    WARNING: MemorySaver dies on restart -- not production.
    Use PostgresSaver for production (thread_id max 255 chars).

    Modes:
      exit:  persist only on graph exit / interrupt (intermediate lost)
      async: async while next step runs (small loss window)
      sync:  before next step (highest durability, extra latency)
    """

    def __init__(self) -> None:
        self._store: dict[str, list[Checkpoint]] = {}  # thread_id -> checkpoints

    def save(self, thread_id: str, state: GraphState, step: int) -> Checkpoint:
        """Save a checkpoint at a super-step boundary."""
        parent_id = ""
        if thread_id in self._store and self._store[thread_id]:
            parent_id = self._store[thread_id][-1].checkpoint_id

        cp = Checkpoint(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:8]}",
            thread_id=thread_id,
            state={
                "messages": state.messages.copy(),
                "current_node": state.current_node,
                "plan": state.plan.copy(),
                "results": state.results.copy(),
                "iteration": state.iteration,
                "status": state.status,
            },
            step=step,
            parent_id=parent_id,
        )
        self._store.setdefault(thread_id, []).append(cp)
        return cp

    def load_latest(self, thread_id: str) -> Checkpoint | None:
        """Load the most recent checkpoint for a thread."""
        checkpoints = self._store.get(thread_id, [])
        return checkpoints[-1] if checkpoints else None

    def load_by_id(self, thread_id: str, checkpoint_id: str) -> Checkpoint | None:
        """Load a specific checkpoint (for time-travel / fork)."""
        for cp in self._store.get(thread_id, []):
            if cp.checkpoint_id == checkpoint_id:
                return cp
        return None

    def list_checkpoints(self, thread_id: str) -> list[Checkpoint]:
        """List all checkpoints for a thread (for debugging/audit)."""
        return self._store.get(thread_id, [])


def restore_state(checkpoint: Checkpoint) -> GraphState:
    """Restore a GraphState from a checkpoint.

    Resume re-executes nodes after the checkpoint.  LLM/tools may return
    different results -- this is a debugger, NOT an audit replay.
    Legal proof is recorded span I/O + never-sampled action hashes.
    """
    data = checkpoint.state
    return GraphState(
        messages=data["messages"],
        current_node=data["current_node"],
        plan=data["plan"],
        results=data["results"],
        iteration=data["iteration"],
        status="running",  # reset to running for resume
    )


# =============================================================================
# --- Section 4: Tool Registration and Dispatch ------------------------------
# =============================================================================
# Tools are the agent's hands.  Registration defines what's available;
# dispatch routes tool calls to implementations.
#
# Deep Agents built-in tools:
#   FS: ls, read_file, write_file, edit_file, glob, grep, delete
#   execute (sandbox protocol only; else error string)
#   eval (QuickJS -- opt-in)
#   task (GP + declarative SubAgent)
#   MCP/custom on tools= (additive -- never removes a built-in)

@dataclass
class ToolSpec:
    """Tool specification for registration."""
    name: str
    description: str
    fn: Callable
    input_schema: dict = field(default_factory=dict)
    requires_sandbox: bool = False
    max_calls: int = 100  # per-session cap


class ToolRegistry:
    """Registry for tool registration and dispatch.

    Key invariants (Deep Agents):
      - tools= is additive (never removes built-in tools)
      - permissions= is fail-open and FS-only (not MCP, not execute)
      - excluded_tools blocks visibility AND execution (>=0.7.9)
      - FilesystemMiddleware and SubAgentMiddleware cannot be excluded
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._call_counts: dict[str, int] = {}

    def register(self, spec: ToolSpec) -> None:
        """Register a tool.  Additive -- does not remove existing tools."""
        self._tools[spec.name] = spec

    def unregister(self, name: str) -> None:
        """Remove a tool (equivalent to excluded_tools)."""
        self._tools.pop(name, None)

    def list_tools(self) -> list[dict]:
        """Return tool schemas for the LLM context."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def dispatch(self, tool_name: str, args: dict) -> ToolResult:
        """Dispatch a tool call to its implementation.

        Checks registration, per-session call caps, and sandbox requirements.
        """
        if tool_name not in self._tools:
            return ToolResult(
                tool_name=tool_name, output=None, success=False,
                error=f"Tool not registered: {tool_name}",
            )

        spec = self._tools[tool_name]

        # Per-session call cap (loop fuse at tool level)
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1
        if self._call_counts[tool_name] > spec.max_calls:
            return ToolResult(
                tool_name=tool_name, output=None, success=False,
                error=f"Tool call limit exceeded: {spec.max_calls}",
            )

        # Sandbox check
        if spec.requires_sandbox:
            # In production: lease sandbox, execute inside, release
            pass

        start = time.monotonic()
        try:
            output = spec.fn(**args)
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(tool_name=tool_name, output=output,
                              execution_ms=elapsed)
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(tool_name=tool_name, output=None, success=False,
                              error=str(e), execution_ms=elapsed)


# Example tools
def search_tool(query: str) -> dict:
    """Simulated search tool."""
    return {"results": [f"Result for: {query}"], "count": 1}


def calculator_tool(expression: str) -> dict:
    """Simulated safe calculator (no eval of arbitrary code)."""
    # In production: use a sandboxed interpreter or sympy
    allowed = set("0123456789+-*/(). ")
    if not all(c in allowed for c in expression):
        raise ValueError("Invalid characters in expression")
    return {"result": "42", "expression": expression}


def read_file_tool(path: str) -> dict:
    """Simulated file read (in production: VFS backend with permissions)."""
    return {"content": f"Contents of {path}", "size": 1024}


# =============================================================================
# --- Section 5: Loop Fuse (Max Iterations, Cost Cap) ------------------------
# =============================================================================
# The harness MUST enforce resource limits.  The model will not self-limit.
#
# Key caps:
#   recursion_limit = 9,999 (Deep Agents sentinel; bare LangGraph = 25)
#   max_turns = 10 (OpenAI Agents SDK default)
#   maxTurns / maxBudgetUsd = None (Claude defaults -- MUST set both)
#   same_action warn 3 / hard 5 (DeerFlow)
#   Temporal event history: warn 10,240; terminate 51,200 events or 50 MB

@dataclass
class LoopFuse:
    """Resource limiter for the agent loop.

    These are control-plane fuses, not prompts.  The harness enforces
    them deterministically.  Hitting the fuse is a hard error, not a
    degradation.
    """
    max_iterations: int = 25           # per-run iteration cap
    max_cost_usd: float = 4.0         # per-run cost cap (Claude SWE = $4)
    max_wall_clock_s: float = 300.0   # 5 minute timeout
    same_action_warn: int = 3         # DeerFlow: warn threshold
    same_action_hard: int = 5         # DeerFlow: hard stop (strip tool_calls)
    max_tool_calls: int = 200         # ToolCallLimitMiddleware

    _iterations: int = 0
    _cost_usd: float = 0.0
    _start_time: float = field(default_factory=time.monotonic)
    _action_counts: dict[str, int] = field(default_factory=dict)
    _total_tool_calls: int = 0

    def check_iteration(self) -> tuple[bool, str]:
        """Check if another iteration is allowed."""
        self._iterations += 1
        if self._iterations > self.max_iterations:
            return False, f"max_iterations ({self.max_iterations}) exceeded"
        return True, ""

    def check_cost(self, additional_cost: float) -> tuple[bool, str]:
        """Check if additional spend is within budget."""
        self._cost_usd += additional_cost
        if self._cost_usd > self.max_cost_usd:
            return False, f"max_cost_usd (${self.max_cost_usd}) exceeded at ${self._cost_usd:.4f}"
        return True, ""

    def check_wall_clock(self) -> tuple[bool, str]:
        """Check if we're within the wall-clock timeout."""
        elapsed = time.monotonic() - self._start_time
        if elapsed > self.max_wall_clock_s:
            return False, f"wall_clock ({self.max_wall_clock_s}s) exceeded at {elapsed:.1f}s"
        return True, ""

    def check_same_action(self, tool_name: str, args_hash: str) -> tuple[bool, str]:
        """Detect repeated identical actions (DeerFlow pattern).

        same_action_k catches agents stuck in a loop calling the same
        tool with the same args.  warn at 3, hard stop at 5.
        """
        key = f"{tool_name}:{args_hash}"
        self._action_counts[key] = self._action_counts.get(key, 0) + 1
        count = self._action_counts[key]

        if count >= self.same_action_hard:
            return False, f"same_action_hard ({self.same_action_hard}): {tool_name}"
        if count >= self.same_action_warn:
            # Log warning but allow
            pass
        return True, ""

    def check_tool_calls(self) -> tuple[bool, str]:
        """Check total tool call count."""
        self._total_tool_calls += 1
        if self._total_tool_calls > self.max_tool_calls:
            return False, f"max_tool_calls ({self.max_tool_calls}) exceeded"
        return True, ""

    def check_all(self, *, cost: float = 0.0, tool_name: str = "",
                  args_hash: str = "") -> tuple[bool, str]:
        """Run all fuse checks.  Returns (ok, reason)."""
        for check_fn, kwargs in [
            (self.check_iteration, {}),
            (self.check_cost, {"additional_cost": cost}),
            (self.check_wall_clock, {}),
        ]:
            ok, reason = check_fn(**kwargs)
            if not ok:
                return False, reason

        if tool_name:
            ok, reason = self.check_same_action(tool_name, args_hash)
            if not ok:
                return False, reason
            ok, reason = self.check_tool_calls()
            if not ok:
                return False, reason

        return True, ""


# =============================================================================
# --- Section 6: Error Handling in Agent Loop --------------------------------
# =============================================================================
# Three error categories in the agent loop:
#   Transient: 429, 503, network timeout -> retry with jitter
#   Permanent: 4xx auth, schema mismatch, Cedar deny -> stop
#   Loop-level: max_turns, cost cap, same_action -> refuse / HITL
#
# The circuit breaker pattern keeps failing components from cascading.

class AgentErrorKind(str, Enum):
    TRANSIENT = "transient"      # retryable (429, 503)
    PERMANENT = "permanent"      # non-retryable (auth, schema)
    LOOP_FUSE = "loop_fuse"      # resource limit hit
    TOOL_ERROR = "tool_error"    # tool returned an error
    LLM_ERROR = "llm_error"     # model returned an error


@dataclass
class AgentError:
    kind: AgentErrorKind
    message: str
    retryable: bool = False
    tool_name: str = ""


class ResilientAgentLoop:
    """Agent loop with comprehensive error handling.

    Error handling strategy:
      1. Transient errors: retry with jitter (max 3 attempts)
      2. Tool errors: append error to messages so the LLM can adapt
      3. LLM errors: retry or degrade (smaller model fallback)
      4. Loop fuse: hard stop, return best effort or refuse
      5. All errors: audit log for post-mortem

    Fallback chain (Deep Agents interview answer):
      Deep Agents (full harness) -> create_agent (thin harness)
      -> deterministic refuse.

    Never: model 429 -> unsandboxed execute
    Never: HITL timeout -> auto-approve
    Never: circuit open -> excluded_middleware the filesystem
    """

    def __init__(
        self,
        llm: StubLLM,
        registry: ToolRegistry,
        checkpointer: CheckpointStore,
        fuse: LoopFuse,
        thread_id: str = "default",
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.checkpointer = checkpointer
        self.fuse = fuse
        self.thread_id = thread_id
        self.errors: list[AgentError] = []

    def run(self, user_message: str) -> dict:
        """Run the agent loop with full error handling and checkpointing."""
        state = GraphState(
            messages=[{"role": "user", "content": user_message}],
            status="running",
        )

        # Save initial checkpoint
        self.checkpointer.save(self.thread_id, state, step=0)

        while state.status == "running":
            # Check all loop fuses
            ok, reason = self.fuse.check_all(cost=0.001)
            if not ok:
                self.errors.append(AgentError(
                    kind=AgentErrorKind.LOOP_FUSE,
                    message=reason,
                ))
                state.status = "fuse_tripped"
                break

            # Invoke LLM
            try:
                response = self.llm.invoke(
                    state.messages,
                    self.registry.list_tools(),
                )
            except Exception as e:
                error = AgentError(
                    kind=AgentErrorKind.LLM_ERROR,
                    message=str(e),
                    retryable="429" in str(e) or "503" in str(e),
                )
                self.errors.append(error)
                if error.retryable and state.iteration < 3:
                    state.iteration += 1
                    continue
                state.status = "llm_error"
                break

            # Check finish condition
            if response.finish_reason == "stop" and not response.tool_calls:
                state.messages.append({"role": "assistant", "content": response.content})
                state.status = "complete"
                break

            if response.finish_reason == "content_filter":
                state.status = "content_filter"
                break

            # Execute tool calls
            state.messages.append({
                "role": "assistant",
                "tool_calls": response.tool_calls,
            })

            for tc in response.tool_calls:
                tool_name = tc["name"]
                args = tc.get("args", {})
                args_hash = hashlib.sha256(
                    json.dumps(args, sort_keys=True).encode()
                ).hexdigest()[:16]

                # Check same-action fuse
                ok, reason = self.fuse.check_same_action(tool_name, args_hash)
                if not ok:
                    self.errors.append(AgentError(
                        kind=AgentErrorKind.LOOP_FUSE,
                        message=reason,
                    ))
                    state.status = "same_action_loop"
                    break

                # Execute tool
                result = self.registry.dispatch(tool_name, args)

                if not result.success:
                    self.errors.append(AgentError(
                        kind=AgentErrorKind.TOOL_ERROR,
                        message=result.error,
                        tool_name=tool_name,
                        retryable="timeout" in result.error.lower(),
                    ))
                    # Append error to messages so LLM can adapt
                    state.messages.append({
                        "role": "tool",
                        "name": tool_name,
                        "content": f"ERROR: {result.error}",
                    })
                else:
                    state.messages.append({
                        "role": "tool",
                        "name": tool_name,
                        "content": json.dumps(result.output),
                    })

            if state.status != "running":
                break

            state.iteration += 1

            # Checkpoint after each iteration
            self.checkpointer.save(self.thread_id, state, step=state.iteration)

        # Final checkpoint
        self.checkpointer.save(self.thread_id, state, step=state.iteration + 1)

        return {
            "status": state.status,
            "answer": state.messages[-1].get("content", "") if state.messages else "",
            "iterations": state.iteration,
            "errors": [{"kind": e.kind.value, "message": e.message} for e in self.errors],
            "checkpoints": len(self.checkpointer.list_checkpoints(self.thread_id)),
            "cost_usd": self.fuse._cost_usd,
        }


# =============================================================================
# --- Section 7: Sub-Agent Pattern (Task Delegation) -------------------------
# =============================================================================
# Deep Agents: `task` tool spawns ephemeral sub-agents with:
#   - Fresh context (no conversation history from parent)
#   - Autonomous execution (runs to completion)
#   - Single handoff (returns only final result ~200 tokens)
#   - Permission replacement (explicit permissions replace parent)

@dataclass
class SubAgentSpec:
    """Specification for a sub-agent."""
    name: str
    system_prompt: str            # required, does NOT inherit from parent
    tools: list[str]              # tool subset for this sub-agent
    max_turns: int = 10


class SubAgentDispatcher:
    """Dispatch tasks to isolated sub-agents.

    Key pattern: sub-agents get fresh context (no parent history).
    This prevents context pollution and keeps the parent's context
    lean.  Heavy outputs are stored in the VFS; parent sees summaries.

    GP (general-purpose) subagent is auto-added by Deep Agents
    unless disabled via GeneralPurposeSubagentProfile(enabled=False).
    Default GP on is roughly +0.8-1.0x the main-agent bill per run.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def dispatch(self, spec: SubAgentSpec, task: str) -> dict:
        """Run a sub-agent on a task in isolation.

        In production, this creates a nested LangGraph invocation
        with its own conversation and tool surface.
        """
        # Sub-agent gets fresh messages (no parent history)
        messages = [
            {"role": "system", "content": spec.system_prompt},
            {"role": "user", "content": task},
        ]

        # Execute (simplified -- in production this is a full graph invoke)
        result = f"Sub-agent '{spec.name}' completed task: {task[:50]}..."

        return {
            "sub_agent": spec.name,
            "result": result,
            "turns_used": 1,
        }


# =============================================================================
# --- Section 8: Double-Texting Strategies -----------------------------------
# =============================================================================
# What happens when a new message arrives while the agent is running.

class DoubleTextStrategy(str, Enum):
    ENQUEUE = "enqueue"       # Queue new input, process after current (default)
    REJECT = "reject"         # Refuse until current completes (critical ops)
    INTERRUPT = "interrupt"   # Halt current, preserve progress, process new
    ROLLBACK = "rollback"     # Halt current, revert all progress, start fresh


# =============================================================================
# --- Demo / Self-Test -------------------------------------------------------
# =============================================================================

if __name__ == "__main__":
    # 1. Basic agent harness
    plan = [
        LLMResponse(
            content="Let me search for that.",
            tool_calls=[{"name": "search", "args": {"query": "refund policy"}, "id": "tc-1"}],
            finish_reason="tool_use",
            input_tokens=500, output_tokens=50,
        ),
        LLMResponse(
            content="Based on the search results, the refund policy is 30 days.",
            finish_reason="stop",
            input_tokens=800, output_tokens=100,
        ),
    ]
    harness = AgentHarness(
        llm=StubLLM(plan=plan),
        tools={"search": lambda query: {"results": [f"Found: {query}"], "count": 1}},
        max_turns=10,
        max_cost_usd=1.0,
    )
    result = harness.run("What is the refund policy?")
    assert result["status"] == "complete"
    assert result["turns"] == 2
    print(f"[Harness] Status: {result['status']}, Turns: {result['turns']}, "
          f"Cost: ${result['cost_usd']:.6f}")

    # 2. State graph
    graph = build_example_graph()
    state = graph.invoke(GraphState())
    assert state.status == "complete"
    assert len(state.results) == 3  # search, analyze, synthesize
    print(f"[Graph] Status: {state.status}, Steps executed: {len(state.results)}")

    # 3. Checkpoint / resume
    store = CheckpointStore()
    test_state = GraphState(messages=[{"role": "user", "content": "test"}], iteration=5)
    cp = store.save("thread-1", test_state, step=5)
    loaded = store.load_latest("thread-1")
    assert loaded is not None
    assert loaded.state["iteration"] == 5
    restored = restore_state(loaded)
    assert restored.status == "running"  # reset for resume
    assert restored.iteration == 5
    print(f"[Checkpoint] Saved and restored, iteration: {restored.iteration}")

    # 4. Tool registration and dispatch
    registry = ToolRegistry()
    registry.register(ToolSpec(name="search", description="Search the web",
                               fn=search_tool, input_schema={"query": "string"}))
    registry.register(ToolSpec(name="calc", description="Calculator",
                               fn=calculator_tool, input_schema={"expression": "string"}))
    registry.register(ToolSpec(name="read", description="Read file",
                               fn=read_file_tool, input_schema={"path": "string"}))

    search_result = registry.dispatch("search", {"query": "test"})
    assert search_result.success
    calc_result = registry.dispatch("calc", {"expression": "2 + 2"})
    assert calc_result.success
    unknown = registry.dispatch("unknown_tool", {})
    assert not unknown.success
    print(f"[Registry] {len(registry.list_tools())} tools registered, "
          f"dispatch OK: search={search_result.success}, unknown={unknown.success}")

    # 5. Loop fuse
    fuse = LoopFuse(max_iterations=5, max_cost_usd=0.1)
    for i in range(5):
        ok, reason = fuse.check_all(cost=0.01)
        assert ok, f"Fuse tripped early at iteration {i}: {reason}"
    # 6th iteration should fail
    ok, reason = fuse.check_all(cost=0.01)
    assert not ok
    print(f"[LoopFuse] Tripped after 5 iterations: {reason}")

    # Same-action detection
    fuse2 = LoopFuse(same_action_hard=3)
    fuse2._start_time = time.monotonic()  # reset timer
    for i in range(2):
        ok, _ = fuse2.check_same_action("search", "same_hash")
        assert ok
    ok, reason = fuse2.check_same_action("search", "same_hash")
    assert not ok
    print(f"[LoopFuse] Same-action detected: {reason}")

    # 6. Resilient agent loop with error handling
    resilient_plan = [
        LLMResponse(
            content="Searching...",
            tool_calls=[{"name": "search", "args": {"query": "test"}, "id": "tc-1"}],
            finish_reason="tool_use",
            input_tokens=300, output_tokens=30,
        ),
        LLMResponse(
            content="Found the answer: 42.",
            finish_reason="stop",
            input_tokens=500, output_tokens=50,
        ),
    ]
    resilient = ResilientAgentLoop(
        llm=StubLLM(plan=resilient_plan),
        registry=registry,
        checkpointer=CheckpointStore(),
        fuse=LoopFuse(max_iterations=20, max_cost_usd=1.0),
        thread_id="thread-resilient",
    )
    result = resilient.run("What is the answer?")
    assert result["status"] == "complete"
    assert result["checkpoints"] >= 2  # initial + at least one iteration
    print(f"[Resilient] Status: {result['status']}, Iterations: {result['iterations']}, "
          f"Checkpoints: {result['checkpoints']}, Errors: {len(result['errors'])}")

    # 7. Sub-agent dispatch
    dispatcher = SubAgentDispatcher(registry)
    sub_result = dispatcher.dispatch(
        SubAgentSpec(name="researcher", system_prompt="You research topics.",
                     tools=["search"]),
        "Find information about quantum computing",
    )
    assert sub_result["sub_agent"] == "researcher"
    print(f"[SubAgent] {sub_result['sub_agent']}: {sub_result['result'][:50]}...")

    print("\nAll checks passed.")
