"""
Deep Agents Delegation, Planning & Subagents -- supervisor-worker pattern,
task decomposition, subagent spawning with isolated context, async fan-out,
handoff patterns, and cost/token budgeting.

The biggest win from delegation is NOT parallelism -- it is CONTEXT QUARANTINE.
A child agent processes heavy work in a fresh window and returns one compact
result to the coordinator, keeping the parent's context clean. A 50-page PDF
stays in the child's 200K window; the parent sees only a 500-token summary.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# =============================================================================
# --- Section 1: Supervisor-Worker Pattern ------------------------------------
# =============================================================================
# Deep Agents' default coordination model. One coordinator dispatches to
# specialized children via the built-in task() tool. Each child gets a
# fresh context window and returns a single ToolMessage to the parent.
#
# Data-plane isolation contract: child messages NEVER pollute the parent
# window. Only the final compact result crosses the boundary.


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskResult:
    """Result returned from a child agent to the parent.

    Only this compact result crosses the parent-child boundary.
    All intermediate messages, tool calls, and reasoning stay
    in the child's context.
    """

    task_id: str
    status: TaskStatus
    output: str           # The compact summary/result
    token_count: int = 0  # Tokens consumed by the child
    duration_ms: float = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class SubagentSpec:
    """Specification for a named subagent.

    Five subagent forms:
    1. GP (general-purpose): auto-added by default, inherits parent tools/model/skills
    2. Declarative SubAgent: named specialization with custom prompt/tools
    3. CompiledSubAgent: pre-compiled LangGraph graph
    4. AsyncSubAgent: non-blocking background work on own thread
    5. Dynamic (QuickJS): created at runtime by interpreter eval()

    CRITICAL INHERITANCE RULES (memorize for interviews):
    | Property       | GP        | Declarative      | Compiled | Async  |
    |----------------|-----------|------------------|----------|--------|
    | tools          | Inherits  | Inherits (add)   | Own      | Own    |
    | model          | Inherits  | Inherits (ovr)   | Own      | Own    |
    | system_prompt  | Inherits  | REPLACED if set  | Own      | Own    |
    | middleware     | NO        | NO               | Own      | Own    |
    | skills         | Inherits  | NO (needs own)   | Own      | Own    |
    | HITL           | Inherits  | REPLACES if set  | NO       | NO     |
    | permissions    | Inherits  | REPLACES if set  | NO       | NO     |
    """

    name: str
    instructions: str = ""
    tools: list[str] | None = None     # None = inherit parent tools
    model: str | None = None           # None = inherit parent model
    skills: list[str] | None = None    # None for GP = inherit; None for custom = empty
    interrupt_on: dict | None = None   # None = inherit; set = REPLACES parent entirely
    permissions: list | None = None    # None = inherit; set = REPLACES parent entirely


class SupervisorAgent:
    """The coordinator in a supervisor-worker delegation pattern.

    The supervisor:
    1. Receives a complex task
    2. Decomposes it into subtasks
    3. Dispatches to specialized children via task()
    4. Collects results (single ToolMessage per child)
    5. Synthesizes the final answer

    Context quarantine ROI:
    | Scenario              | Without Delegation | With Delegation | Savings |
    |-----------------------|-------------------|-----------------|---------|
    | 50-page PDF analysis  | 75K in parent     | 500-token summary | 99.3%  |
    | 4 parallel searches   | 40K tokens        | 4 x 200-token     | 98%    |
    | DB query + analysis   | 35K tokens        | 300-token          | 99.1%  |
    """

    def __init__(
        self,
        name: str = "supervisor",
        model: str = "anthropic:claude-sonnet-4-6",
        subagent_specs: list[SubagentSpec] | None = None,
    ):
        self.name = name
        self.model = model
        self.specs = {s.name: s for s in (subagent_specs or [])}
        self._children: dict[str, "ChildAgent"] = {}
        self._results: list[TaskResult] = []

    def delegate(self, task_name: str, task_description: str) -> TaskResult:
        """Dispatch a task to a child agent (synchronous delegation).

        Under the hood (what task() does):
        1. Router matches task_name against subagent specs
        2. If match: use spec's config. If no match: use GP (default)
        3. Child gets fresh context window with the task description
        4. Child runs full ReAct loop independently
        5. Child's final message becomes a ToolMessage to the parent
        6. ALL child intermediate messages stay in the child's context

        Multiple parallel task() calls in a single turn execute in
        parallel -- this is the primary parallelism mechanism.
        """
        spec = self.specs.get(task_name)
        child = ChildAgent(
            name=task_name,
            spec=spec,
            parent_model=self.model,
        )
        self._children[task_name] = child

        result = child.execute(task_description)
        self._results.append(result)
        return result

    def delegate_parallel(self, tasks: list[dict[str, str]]) -> list[TaskResult]:
        """Dispatch multiple tasks in parallel (fan-out).

        When the model proposes multiple task() calls in a single turn,
        they execute in parallel. This is the primary parallelism mechanism.

        Optimal parallel subagents: 3-5 (diminishing returns beyond 5).
        Multi-agent achieves 90.2% success with 4x chat tokens (Anthropic).
        """
        results = []
        for task in tasks:
            result = self.delegate(task["name"], task["description"])
            results.append(result)
        return results

    def synthesize(self, results: list[TaskResult]) -> str:
        """Synthesize child results into a final answer.

        The parent only sees the compact ToolMessage from each child.
        All intermediate reasoning stays quarantined in the child.
        """
        parts = []
        for r in results:
            status = "OK" if r.status == TaskStatus.COMPLETED else r.status.value
            parts.append(f"[{r.task_id}] {status}: {r.output}")
        return "\n".join(parts)

    def get_cost_summary(self) -> dict:
        """Summarize token costs across all delegations.

        Cost benchmarks (Sonnet 4.6, 10-call parent + GP children):
          Parent only:          $0.2229/run  ($223/1k)
          Parent + 1 GP child:  $0.4032/run  ($403/1k)
          Parent + 2 children:  $0.5835/run  ($584/1k)
          Parent + 3 children:  $0.7638/run  ($764/1k)
          ~$0.18/child/run marginal cost (8-call child, cached)
        """
        total_child_tokens = sum(r.token_count for r in self._results)
        return {
            "total_delegations": len(self._results),
            "completed": sum(1 for r in self._results
                             if r.status == TaskStatus.COMPLETED),
            "failed": sum(1 for r in self._results
                          if r.status == TaskStatus.FAILED),
            "total_child_tokens": total_child_tokens,
            "estimated_child_cost_usd": round(total_child_tokens * 3.0 / 1_000_000, 4),
        }


class ChildAgent:
    """A child agent with isolated context.

    The child gets a FRESH context window. It processes the task
    independently and returns only a compact result to the parent.
    This is the context quarantine guarantee.
    """

    def __init__(
        self,
        name: str,
        spec: SubagentSpec | None = None,
        parent_model: str = "anthropic:claude-sonnet-4-6",
    ):
        self.name = name
        self.spec = spec
        self.model = (spec.model if spec and spec.model else parent_model)
        self.task_id = str(uuid.uuid4())[:8]

    def execute(self, task_description: str) -> TaskResult:
        """Execute the task in an isolated context.

        In production, this runs a full ReAct loop with the child's own
        model, tools, and context window. Here we simulate the pattern.

        Failure handling: child failures propagate as error strings in
        the ToolMessage back to the parent. The parent decides: retry,
        try differently, or report failure.
        """
        start = time.time()

        try:
            # Simulate child work (in production: full ReAct loop)
            instructions = self.spec.instructions if self.spec else "general-purpose"
            output = (
                f"[{self.name}] Analyzed: {task_description[:80]}... "
                f"Using: {instructions[:50]}... "
                f"Result: Task completed successfully."
            )
            duration_ms = (time.time() - start) * 1000
            return TaskResult(
                task_id=self.task_id,
                status=TaskStatus.COMPLETED,
                output=output,
                token_count=800,  # Simulated
                duration_ms=duration_ms,
            )
        except Exception as e:
            return TaskResult(
                task_id=self.task_id,
                status=TaskStatus.FAILED,
                output=f"Error: {e}",
                duration_ms=(time.time() - start) * 1000,
            )


# =============================================================================
# --- Section 2: Task Decomposition (Plan -> Delegate -> Collect) -------------
# =============================================================================
# TodoListMiddleware adds a write_todos tool for structured planning.
# IMPORTANT: No measured improvement in task accuracy. +15-30% more tokens.
# Use only for UX visibility (stream.values.todos), not for accuracy.


@dataclass
class TodoItem:
    """A single item in the task plan."""

    id: str
    description: str
    status: str = "pending"  # "pending" | "in_progress" | "completed"
    assigned_to: str | None = None
    result: str | None = None


class TaskPlanner:
    """Structured task planning and decomposition.

    TodoListMiddleware (opt-in since v0.7):
    - Adds write_todos tool
    - Statuses: pending, in_progress, completed
    - Streaming: stream.values.todos
    - CRITICAL: No measured improvement in task accuracy
    - CRITICAL: +15-30% more tokens (plan + status updates)
    - Planning ONLY -- write_todos does NOT execute delegation.
      The model still needs task() calls to actually delegate work.
    """

    def __init__(self):
        self.todos: list[TodoItem] = []

    def decompose(self, task: str, subtasks: list[str]) -> list[TodoItem]:
        """Decompose a complex task into subtasks.

        Good decomposition principles:
        - Each subtask should be independently executable
        - Subtasks should have clear success criteria
        - Minimize dependencies between subtasks (enables parallelism)
        - 3-5 parallel subtasks is optimal (diminishing returns beyond 5)
        """
        self.todos = [
            TodoItem(id=f"task-{i}", description=sub)
            for i, sub in enumerate(subtasks)
        ]
        return self.todos

    def update_status(self, task_id: str, status: str,
                      result: str | None = None) -> None:
        """Update a todo item's status."""
        for item in self.todos:
            if item.id == task_id:
                item.status = status
                if result:
                    item.result = result
                return

    def get_pending(self) -> list[TodoItem]:
        """Get tasks ready for delegation."""
        return [t for t in self.todos if t.status == "pending"]

    def get_summary(self) -> dict:
        """Get plan execution summary."""
        total = len(self.todos)
        completed = sum(1 for t in self.todos if t.status == "completed")
        return {
            "total": total,
            "completed": completed,
            "pending": sum(1 for t in self.todos if t.status == "pending"),
            "in_progress": sum(1 for t in self.todos if t.status == "in_progress"),
            "progress": f"{completed}/{total}",
        }


def plan_and_delegate(
    supervisor: SupervisorAgent,
    task: str,
    subtasks: list[dict[str, str]],
) -> dict:
    """End-to-end: plan -> delegate -> collect -> synthesize.

    Pattern: Supervisor decomposes the task, delegates each subtask
    to specialized children, collects results, synthesizes final answer.
    """
    planner = TaskPlanner()
    plan = planner.decompose(task, [s["description"] for s in subtasks])

    results = []
    for i, subtask in enumerate(subtasks):
        planner.update_status(f"task-{i}", "in_progress")
        result = supervisor.delegate(subtask["name"], subtask["description"])
        planner.update_status(
            f"task-{i}",
            "completed" if result.status == TaskStatus.COMPLETED else "failed",
            result=result.output,
        )
        results.append(result)

    synthesis = supervisor.synthesize(results)
    return {
        "plan": planner.get_summary(),
        "results": [{"id": r.task_id, "status": r.status.value, "output": r.output}
                     for r in results],
        "synthesis": synthesis,
    }


# =============================================================================
# --- Section 3: Subagent Spawning with Isolated Context ----------------------
# =============================================================================
# Each subagent form has different inheritance rules. Getting this wrong
# is a common interview trap: declarative specs REPLACE (not merge)
# parent HITL gates and permissions.


class SubagentFactory:
    """Factory for creating subagents with correct inheritance.

    The five forms and their inheritance (the interview table):

    GP (general-purpose):
      - Auto-added by default
      - Inherits: tools, model, skills, HITL, permissions
      - Does NOT inherit: middleware

    Declarative SubAgent:
      - Named specialization with custom config
      - Inherits: tools (can add), model (can override)
      - REPLACES if set: system_prompt, HITL, permissions (PR #2334)
      - Does NOT inherit: middleware, skills

    CompiledSubAgent:
      - Pre-compiled LangGraph graph
      - Inherits: NOTHING (wire everything in the graph)

    AsyncSubAgent:
      - Non-blocking background work
      - Inherits: NOTHING (own deployment)

    Dynamic (QuickJS):
      - Created at runtime by interpreter eval()
      - DANGER: eval() bypasses parent interrupt_on (the interpreter hole)
    """

    def __init__(self, parent_config: dict):
        self.parent_config = parent_config

    def create_gp_child(self, task_description: str) -> dict:
        """Create a general-purpose child (inherits most parent config)."""
        return {
            "type": "gp",
            "model": self.parent_config["model"],       # Inherited
            "tools": self.parent_config["tools"],        # Inherited
            "skills": self.parent_config.get("skills", []),  # Inherited
            "interrupt_on": self.parent_config.get("interrupt_on", {}),  # Inherited
            "permissions": self.parent_config.get("permissions", []),    # Inherited
            "middleware": [],  # NOT inherited -- this is the gap
            "task": task_description,
        }

    def create_declarative_child(
        self, spec: SubagentSpec, task_description: str
    ) -> dict:
        """Create a declarative child with spec overrides.

        CRITICAL: interrupt_on and permissions REPLACE entirely if set.
        They do NOT merge with parent rules.

        Example of accidental security gap:
          Parent has interrupt_on={"deploy": True}
          Spec sets interrupt_on={"delete": True}
          Result: child has ONLY delete gate, deploy runs without HITL!
        """
        child = {
            "type": "declarative",
            "model": spec.model or self.parent_config["model"],
            "tools": spec.tools or self.parent_config["tools"],
            "skills": spec.skills or [],  # NOT inherited for declarative
            "middleware": [],  # NOT inherited
            "task": task_description,
        }

        # HITL: REPLACES if set (not merge)
        if spec.interrupt_on is not None:
            child["interrupt_on"] = spec.interrupt_on  # Parent gates DROPPED
        else:
            child["interrupt_on"] = self.parent_config.get("interrupt_on", {})

        # Permissions: REPLACES if set (not merge)
        if spec.permissions is not None:
            child["permissions"] = spec.permissions  # Parent permissions DROPPED
        else:
            child["permissions"] = self.parent_config.get("permissions", [])

        # System prompt: REPLACED if spec sets it
        if spec.instructions:
            child["system_prompt"] = spec.instructions
        else:
            child["system_prompt"] = self.parent_config.get("system_prompt", "")

        return child


# =============================================================================
# --- Section 4: Async Fan-Out with Result Aggregation ------------------------
# =============================================================================
# AsyncSubAgent launches non-blocking background work. The supervisor gets
# a task ID immediately and can continue chatting with the user.
#
# Five lifecycle tools on the supervisor:
#   start_async_task  -- launch background work, get task ID
#   check_async_task  -- poll status and get partial/final results
#   update_async_task -- send additional context to running task
#   cancel_async_task -- stop a running task
#   list_async_tasks  -- show all active/completed tasks
#
# Worker-pool sizing: 1 supervisor + N async = N+1 worker slots


class AsyncTaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AsyncTask:
    """A background task managed by the async delegation system."""

    task_id: str
    spec_name: str
    description: str
    status: AsyncTaskStatus = AsyncTaskStatus.QUEUED
    result: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    updates: list[str] = field(default_factory=list)


class AsyncDelegationManager:
    """Manages async subagent delegation with fan-out and aggregation.

    Anti-pattern to avoid: launching start_async_task and immediately
    polling check_async_task in a loop. This turns async back into
    blocking and wastes model calls. The docs explicitly call this out.

    Transport options:
    - ASGI: co-deployed graphs in same process (url omitted)
    - HTTP: remote Agent Protocol server (url="https://worker.example.com")
    """

    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self._tasks: dict[str, AsyncTask] = {}
        self._worker_count = 0

    def start_async_task(self, spec_name: str, description: str) -> str:
        """Launch a background task. Returns task ID immediately.

        The supervisor continues its conversation while the background
        work runs. Check results later with check_async_task.
        """
        if self._worker_count >= self.max_concurrent:
            raise RuntimeError(
                f"Worker pool exhausted ({self.max_concurrent} slots). "
                f"Wait for tasks to complete or cancel some."
            )

        task_id = str(uuid.uuid4())[:8]
        task = AsyncTask(
            task_id=task_id,
            spec_name=spec_name,
            description=description,
            status=AsyncTaskStatus.RUNNING,
            started_at=time.time(),
        )
        self._tasks[task_id] = task
        self._worker_count += 1

        # Simulate background execution (in production: separate thread/process)
        self._simulate_background_work(task)

        return task_id

    def check_async_task(self, task_id: str) -> dict:
        """Poll status and get partial/final results."""
        task = self._tasks.get(task_id)
        if not task:
            return {"error": f"Unknown task: {task_id}"}
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "result": task.result,
            "duration_s": (
                (task.completed_at or time.time()) - (task.started_at or time.time())
            ),
        }

    def update_async_task(self, task_id: str, context: str) -> dict:
        """Send additional context to a running task."""
        task = self._tasks.get(task_id)
        if not task:
            return {"error": f"Unknown task: {task_id}"}
        if task.status != AsyncTaskStatus.RUNNING:
            return {"error": f"Task {task_id} is not running"}
        task.updates.append(context)
        return {"status": "updated", "task_id": task_id}

    def cancel_async_task(self, task_id: str) -> dict:
        """Cancel a running task."""
        task = self._tasks.get(task_id)
        if not task:
            return {"error": f"Unknown task: {task_id}"}
        task.status = AsyncTaskStatus.CANCELLED
        task.completed_at = time.time()
        self._worker_count -= 1
        return {"status": "cancelled", "task_id": task_id}

    def list_async_tasks(self) -> list[dict]:
        """List all tasks with their statuses."""
        return [
            {"task_id": t.task_id, "spec": t.spec_name,
             "status": t.status.value, "result": t.result}
            for t in self._tasks.values()
        ]

    def fan_out(self, tasks: list[dict[str, str]]) -> list[str]:
        """Launch multiple async tasks in parallel.

        Optimal parallel count: 3-5 subagents (diminishing returns beyond 5).
        Worker-pool sizing: 1 supervisor + N async = N+1 slots.
        """
        task_ids = []
        for task in tasks:
            tid = self.start_async_task(task["spec"], task["description"])
            task_ids.append(tid)
        return task_ids

    def gather_results(self, task_ids: list[str]) -> list[dict]:
        """Collect results from all fan-out tasks."""
        return [self.check_async_task(tid) for tid in task_ids]

    def _simulate_background_work(self, task: AsyncTask) -> None:
        """Simulate background work (in production: separate thread)."""
        task.result = f"Background analysis of '{task.description[:50]}...' completed."
        task.status = AsyncTaskStatus.COMPLETED
        task.completed_at = time.time()
        self._worker_count -= 1


# =============================================================================
# --- Section 5: Handoff Pattern (Agent A -> Agent B) -------------------------
# =============================================================================
# Sequential delegation where the output of one agent becomes the input
# to the next. Useful for multi-stage processing pipelines.


class HandoffPipeline:
    """Sequential handoff: output of child A becomes input to child B.

    Use cases:
    - Document processing: extract -> analyze -> summarize
    - Code review: parse -> check style -> check bugs -> report
    - Research: gather -> verify -> synthesize

    Each stage gets a fresh context window. The compact result from
    one stage becomes the task description for the next.
    """

    def __init__(self, stages: list[SubagentSpec]):
        self.stages = stages
        self._results: list[TaskResult] = []

    def execute(self, initial_input: str) -> list[TaskResult]:
        """Execute the pipeline: stage A -> stage B -> stage C.

        Each stage receives the previous stage's output as its input.
        Context isolation is maintained: each stage sees only its input,
        not the full history of all prior stages.
        """
        current_input = initial_input

        for spec in self.stages:
            child = ChildAgent(name=spec.name, spec=spec)
            result = child.execute(current_input)
            self._results.append(result)

            if result.status != TaskStatus.COMPLETED:
                break  # Pipeline stops on failure

            # Output becomes input for next stage
            current_input = result.output

        return self._results

    def get_pipeline_summary(self) -> dict:
        """Summary of pipeline execution."""
        return {
            "stages_total": len(self.stages),
            "stages_completed": sum(1 for r in self._results
                                     if r.status == TaskStatus.COMPLETED),
            "total_tokens": sum(r.token_count for r in self._results),
            "total_duration_ms": sum(r.duration_ms for r in self._results),
        }


# =============================================================================
# --- Section 6: Cost and Token Budgeting Across Subagents --------------------
# =============================================================================
# Delegation is not free. Each child pays its own model costs.
# Budget enforcement prevents runaway costs from over-delegation.


@dataclass
class TokenBudget:
    """Token and cost budget for an agent and its children.

    Key cost numbers (Sonnet 4.6):
      Parent only, 10 calls:         $0.2229/run ($223/1k)
      Parent + 1 GP child (8 calls): $0.4032/run ($403/1k)
      Parent + 2 children:           $0.5835/run ($584/1k)
      Parent + 3 children:           $0.7638/run ($764/1k)
      ~$0.18/child/run marginal cost

    Multi-agent research (Anthropic):
      4x chat tokens, 15x total tokens for multi-agent vs single
      90.2% task success rate
      3-5 optimal parallel subagents (diminishing returns beyond)
    """

    max_total_tokens: int = 500_000    # Total budget across parent + children
    max_tokens_per_child: int = 100_000  # Per-child cap
    max_children: int = 5              # Max concurrent children
    max_cost_usd: float = 1.0         # Cost cap per run

    # Tracking
    parent_tokens: int = 0
    child_tokens: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.parent_tokens + sum(self.child_tokens.values())

    @property
    def estimated_cost_usd(self) -> float:
        """Rough cost estimate at $3/MTok input (Sonnet 4.6)."""
        return self.total_tokens * 3.0 / 1_000_000

    def can_delegate(self, estimated_child_tokens: int = 50_000) -> bool:
        """Check if delegation is within budget."""
        if len(self.child_tokens) >= self.max_children:
            return False
        if self.total_tokens + estimated_child_tokens > self.max_total_tokens:
            return False
        if self.estimated_cost_usd > self.max_cost_usd:
            return False
        return True

    def record_child(self, child_id: str, tokens: int) -> None:
        """Record tokens consumed by a child."""
        self.child_tokens[child_id] = tokens

    def get_report(self) -> dict:
        return {
            "parent_tokens": self.parent_tokens,
            "children": len(self.child_tokens),
            "child_tokens": dict(self.child_tokens),
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "budget_remaining_tokens": self.max_total_tokens - self.total_tokens,
            "budget_remaining_pct": f"{(1 - self.total_tokens / self.max_total_tokens):.0%}",
        }


class BudgetedSupervisor(SupervisorAgent):
    """Supervisor with token/cost budget enforcement.

    Prevents runaway costs from over-delegation. Each delegation
    checks the budget before spawning a child.

    recursion_limit and call caps (separate concerns):
      recursion_limit: 9,999 (sentinel dodge vs 10,000 which merge_configs drops)
      ModelCallLimitMiddleware.run_limit: model calls this invoke
      ModelCallLimitMiddleware.thread_limit: across all invokes (needs checkpointer)
      ToolCallLimitMiddleware.run_limit: tool calls this invoke

    A confused agent can burn budget INSIDE 9,999 super-steps.
    Always set product-level call caps in addition to recursion_limit.
    """

    def __init__(
        self,
        name: str = "budgeted-supervisor",
        budget: TokenBudget | None = None,
        **kwargs,
    ):
        super().__init__(name=name, **kwargs)
        self.budget = budget or TokenBudget()

    def delegate(self, task_name: str, task_description: str) -> TaskResult:
        """Delegate with budget check."""
        if not self.budget.can_delegate():
            return TaskResult(
                task_id="budget-exceeded",
                status=TaskStatus.FAILED,
                output="Token/cost budget exceeded. Cannot delegate.",
            )

        result = super().delegate(task_name, task_description)
        self.budget.record_child(result.task_id, result.token_count)
        return result


# =============================================================================
# --- Section 7: Delegation Security Model ------------------------------------
# =============================================================================
# Three layers: HITL inheritance, permissions inheritance, MCP gateway.
# The interpreter hole (eval/PTC bypassing HITL) is the key gap.


@dataclass
class DelegationSecurityAudit:
    """Audit a delegation configuration for security gaps.

    Common gaps to check:
    1. Declarative spec replaces parent HITL (PR #2334)
    2. Compiled/async children inherit nothing (must wire own HITL)
    3. QuickJS eval() bypasses parent interrupt_on (interpreter hole)
    4. MCP gateway still required for children calling MCP tools
    5. Middleware does NOT inherit (PII, retry, etc. missing in child)
    """

    @staticmethod
    def audit_spec(spec: SubagentSpec, parent_config: dict) -> list[str]:
        """Check for security gaps in a subagent spec."""
        warnings = []

        # Check HITL inheritance
        parent_hitl = parent_config.get("interrupt_on", {})
        if spec.interrupt_on is not None and parent_hitl:
            dropped = set(parent_hitl.keys()) - set(spec.interrupt_on.keys())
            if dropped:
                warnings.append(
                    f"HITL gates DROPPED by spec override: {dropped}. "
                    f"Declarative specs REPLACE, not merge."
                )

        # Check permissions inheritance
        if spec.permissions is not None and parent_config.get("permissions"):
            warnings.append(
                "Parent permissions REPLACED by spec. "
                "Verify all parent safety rules are replicated."
            )

        # Check skills
        if spec.skills is None and spec.name != "gp":
            warnings.append(
                "Custom subagent has no skills= set. "
                "Only GP inherits parent skills."
            )

        # Middleware gap
        warnings.append(
            "Reminder: middleware does NOT inherit. "
            "If parent has PII/retry middleware, child needs its own."
        )

        return warnings


# =============================================================================
# --- Section 8: Streaming for Delegated Work ---------------------------------
# =============================================================================
# stream.subagents provides one handle per task() delegation.
# Use this for UI, NOT raw subgraph namespaces.


@dataclass
class SubagentStreamEvent:
    """A streaming event from a child agent's execution."""

    subagent_name: str
    event_type: str  # "started", "tool_call", "message", "completed", "error"
    content: str
    timestamp: float = field(default_factory=time.time)


class DelegationStreamHandler:
    """Handles streaming events from delegated child work.

    Use stream.subagents for product UX:
    - One handle per task() delegation
    - name = subagent_type (e.g., "researcher", "coder")
    - Agent Server aliases as thread.subagents

    Do NOT use:
    - Raw subgraph namespaces (debugging only)
    - stream.subgraphs for product UX

    Event streaming v3 (deepagents >= 0.6) with typed projections:
    .messages, .tool_calls, .values, .subagents, .output
    """

    def __init__(self):
        self._events: list[SubagentStreamEvent] = []

    def on_child_start(self, name: str, task: str) -> None:
        self._events.append(SubagentStreamEvent(
            subagent_name=name, event_type="started",
            content=f"Starting: {task[:80]}...",
        ))

    def on_child_complete(self, name: str, result: str) -> None:
        self._events.append(SubagentStreamEvent(
            subagent_name=name, event_type="completed",
            content=result[:200],
        ))

    def on_child_error(self, name: str, error: str) -> None:
        self._events.append(SubagentStreamEvent(
            subagent_name=name, event_type="error", content=error,
        ))

    def get_events(self, name: str | None = None) -> list[dict]:
        """Get events, optionally filtered by subagent name."""
        events = self._events
        if name:
            events = [e for e in events if e.subagent_name == name]
        return [
            {"name": e.subagent_name, "type": e.event_type, "content": e.content}
            for e in events
        ]


# =============================================================================
# --- Demo / Self-Test --------------------------------------------------------
# =============================================================================


def demo():
    """Demonstrate delegation patterns and cost budgeting."""
    print("=" * 60)
    print("DEMO: Deep Agents Delegation & Subagents")
    print("=" * 60)

    # 1. Supervisor-worker pattern
    print("\n--- Supervisor-Worker Pattern ---")
    supervisor = SupervisorAgent(
        subagent_specs=[
            SubagentSpec(
                name="researcher",
                instructions="Research the topic thoroughly. Return key findings.",
            ),
            SubagentSpec(
                name="analyst",
                instructions="Analyze the data and identify patterns.",
                tools=["read_file", "grep"],
            ),
            SubagentSpec(
                name="writer",
                instructions="Write a clear, concise report.",
            ),
        ],
    )

    result = supervisor.delegate("researcher", "Find Q3 revenue trends")
    print(f"Delegation result: {result.status.value} - {result.output[:80]}...")

    # 2. Task decomposition (plan -> delegate -> collect)
    print("\n--- Task Decomposition ---")
    outcome = plan_and_delegate(
        supervisor,
        task="Analyze Q3 performance",
        subtasks=[
            {"name": "researcher", "description": "Gather Q3 revenue data"},
            {"name": "analyst", "description": "Identify trends and anomalies"},
            {"name": "writer", "description": "Draft executive summary"},
        ],
    )
    print(f"Plan progress: {outcome['plan']}")
    print(f"Synthesis:\n{outcome['synthesis']}")

    # 3. Subagent spawning with inheritance audit
    print("\n--- Subagent Inheritance ---")
    parent_config = {
        "model": "anthropic:claude-sonnet-4-6",
        "tools": ["read_file", "write_file", "execute"],
        "skills": ["deploy-to-staging", "code-review"],
        "interrupt_on": {"deploy": True, "delete": True},
        "permissions": [{"paths": ["/workspace/**"], "mode": "allow"}],
    }
    factory = SubagentFactory(parent_config)

    gp_child = factory.create_gp_child("Quick analysis task")
    print(f"GP child inherits HITL: {gp_child['interrupt_on']}")
    print(f"GP child inherits skills: {gp_child['skills']}")

    # Declarative child with HITL override (REPLACES, not merges!)
    risky_spec = SubagentSpec(
        name="deployer",
        instructions="Deploy the service",
        interrupt_on={"execute": True},  # Parent's deploy+delete gates DROPPED!
    )
    decl_child = factory.create_declarative_child(risky_spec, "Deploy to staging")
    print(f"Declarative child HITL (REPLACED): {decl_child['interrupt_on']}")
    print(f"  WARNING: parent 'deploy' and 'delete' gates are GONE!")

    # Security audit
    print("\n--- Security Audit ---")
    warnings = DelegationSecurityAudit.audit_spec(risky_spec, parent_config)
    for w in warnings:
        print(f"  [!] {w}")

    # 4. Async fan-out
    print("\n--- Async Fan-Out ---")
    async_mgr = AsyncDelegationManager(max_concurrent=5)

    task_ids = async_mgr.fan_out([
        {"spec": "web-search", "description": "Search for competitor pricing"},
        {"spec": "db-query", "description": "Pull internal revenue data"},
        {"spec": "api-call", "description": "Fetch market benchmarks"},
    ])
    print(f"Launched {len(task_ids)} async tasks: {task_ids}")

    results = async_mgr.gather_results(task_ids)
    for r in results:
        print(f"  [{r['task_id']}] {r['status']}: {r.get('result', 'pending')[:60]}...")

    print(f"All tasks: {async_mgr.list_async_tasks()}")

    # 5. Handoff pipeline
    print("\n--- Handoff Pipeline (A -> B -> C) ---")
    pipeline = HandoffPipeline(stages=[
        SubagentSpec(name="extractor", instructions="Extract key data points"),
        SubagentSpec(name="analyzer", instructions="Analyze extracted data"),
        SubagentSpec(name="reporter", instructions="Generate final report"),
    ])
    pipeline_results = pipeline.execute("Raw contract document text here...")
    print(f"Pipeline summary: {pipeline.get_pipeline_summary()}")
    for r in pipeline_results:
        print(f"  Stage [{r.task_id}]: {r.status.value}")

    # 6. Cost and token budgeting
    print("\n--- Cost & Token Budgeting ---")
    budget = TokenBudget(
        max_total_tokens=200_000,
        max_tokens_per_child=50_000,
        max_children=3,
        max_cost_usd=0.50,
    )
    budget.parent_tokens = 30_000

    budgeted = BudgetedSupervisor(
        budget=budget,
        subagent_specs=[
            SubagentSpec(name="analyzer", instructions="Analyze data"),
        ],
    )
    r1 = budgeted.delegate("analyzer", "First analysis task")
    print(f"Delegation 1: {r1.status.value}")
    r2 = budgeted.delegate("analyzer", "Second analysis task")
    print(f"Delegation 2: {r2.status.value}")
    print(f"Budget report: {budget.get_report()}")

    # 7. Streaming
    print("\n--- Delegation Streaming ---")
    stream = DelegationStreamHandler()
    stream.on_child_start("researcher", "Find Q3 data")
    stream.on_child_complete("researcher", "Found 5 key data points about Q3 revenue")
    stream.on_child_start("analyst", "Analyze the findings")
    stream.on_child_error("analyst", "Context window overflow")
    print(f"Stream events: {stream.get_events()}")

    print("\n" + "=" * 60)
    print("Key interview numbers:")
    print("  5 subagent forms: GP, declarative, compiled, async, dynamic")
    print("  9,999 recursion_limit (sentinel dodge vs 10,000)")
    print("  25: bare LangGraph default (children without propagated config)")
    print("  5 async lifecycle tools: start/check/update/cancel/list")
    print("  N+1 worker slots: 1 supervisor + N async subagents")
    print("  3-5: optimal parallel subagents (diminishing returns beyond)")
    print("  90.2%: multi-agent task success rate (Anthropic)")
    print("  4x / 15x: chat / total tokens for multi-agent vs single")
    print("  $0.18/child/run: GP child marginal cost (8-call, cached)")
    print("  $223/1k (parent only) -> $764/1k (parent + 3 children)")
    print("  TodoListMiddleware: NO accuracy improvement, +15-30% tokens")
    print("  Declarative specs REPLACE (not merge) parent HITL/permissions")
    print("=" * 60)


if __name__ == "__main__":
    demo()
