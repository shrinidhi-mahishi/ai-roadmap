"""Advanced Autonomous Agents -- Bounded Autonomy, Checkpointing, Safety Controls.
Autonomous agents work for hours without intervention; they need safety boundaries,
kill switches, spending limits, checkpoints, and human-in-the-loop gates."""

import time, json, uuid
from dataclasses import dataclass, field
from enum import Enum

# ================================================================
# Section 1: Bounded Autonomy Envelope
# ================================================================
# Autonomy is not on/off -- it is an explicit data structure defining what
# the agent may do, how much it may spend, and when it must stop.


@dataclass
class AutonomyEnvelope:
    """Authority boundary for one agent run. Each dimension is independent."""
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    objective: str = ""
    allowed: list = field(default_factory=lambda: ["read", "write_sandbox", "test"])
    denied: list = field(default_factory=lambda: ["deploy", "merge", "delete_prod"])
    max_budget: float = 50.0
    max_calls: int = 500
    max_wall_s: float = 14400
    spent: float = 0.0
    calls_used: int = 0
    start: float = field(default_factory=time.time)

    def check_action(self, action: str) -> tuple[bool, str]:
        if action in self.denied:
            return False, f"'{action}' denied"
        if action not in self.allowed:
            return False, f"'{action}' not allowed"
        return True, "ok"

    def check_budget(self, cost: float = 0.0) -> tuple[bool, str]:
        if self.spent + cost > self.max_budget:
            return False, f"Budget: ${self.spent:.2f}+${cost:.2f} > ${self.max_budget:.2f}"
        if self.calls_used >= self.max_calls:
            return False, f"Call limit: {self.calls_used}/{self.max_calls}"
        if time.time() - self.start > self.max_wall_s:
            return False, "Wall clock exceeded"
        return True, "ok"

    def charge(self, cost: float):
        self.spent += cost
        self.calls_used += 1


def demo_autonomy_envelope():
    print("--- Bounded Autonomy Envelope ---")
    env = AutonomyEnvelope(
        objective="Upgrade to Python 3.12", max_budget=10.0, max_calls=20,
        allowed=["read", "write_sandbox", "test", "open_pr"],
        denied=["merge", "deploy"],
    )
    ok, _ = env.check_action("write_sandbox")
    print(f"  write_sandbox: allowed={ok}")
    ok, r = env.check_action("deploy")
    print(f"  deploy: allowed={ok}, reason={r}")
    env.spent = 9.50
    ok, r = env.check_budget(cost=1.00)
    print(f"  budget ($9.50+$1): allowed={ok}, reason={r}")


# ================================================================
# Section 2: Checkpoint and Resume
# ================================================================
# Save structured state so the agent survives crashes, deploys, or
# context-window rotations (Temporal Continue-As-New).


@dataclass
class AgentCheckpoint:
    """Semantic checkpoint -- structured state, not just token history."""
    run_id: str
    step: int
    objective: str
    milestones: list = field(default_factory=list)
    plan: list = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    budget_left: float = 0.0
    env_digest: str = ""

    def serialize(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def load(cls, data: str) -> "AgentCheckpoint":
        return cls(**json.loads(data))


def demo_checkpoint_resume():
    print("\n--- Checkpoint and Resume ---")
    cp = AgentCheckpoint(run_id="run_abc", step=5, objective="Migrate to Py 3.12",
                         milestones=["syntax_fixes", "type_hints"],
                         plan=["update_tests", "fix_imports", "open_pr"],
                         artifacts={"branch": "migrate-py312"}, budget_left=7.50,
                         env_digest="sha256:deadbeef")
    saved = cp.serialize()
    print(f"  Saved at step {cp.step}")
    restored = AgentCheckpoint.load(saved)
    print(f"  Restored: step={restored.step}, done={restored.milestones}")
    print(f"  Resume: verify env={restored.env_digest[:16]}..., continue step {restored.step + 1}")


# ================================================================
# Section 3: Kill Switch / Safety Monitor
# ================================================================
# Monitor every action. Halt on budget exhaust, scope violation, or stall.
# Control plane stops data plane without model cooperation.


class StopReason(Enum):
    RUNNING = "RUNNING"
    BUDGET = "BUDGET_EXHAUSTED"
    SCOPE = "SCOPE_VIOLATION"
    STALL = "STALL_DETECTED"
    KILLED = "KILL_SWITCH"


class SafetyMonitor:
    """Deterministic policy enforcement -- outside model text."""

    def __init__(self, envelope: AutonomyEnvelope, max_idle: int = 5):
        self.env = envelope
        self.max_idle = max_idle
        self.recent: list[str] = []
        self.killed = False
        self.reason = StopReason.RUNNING

    def kill(self):
        self.killed = True
        self.reason = StopReason.KILLED

    def check(self, action: str, cost: float = 0.1) -> tuple[bool, str]:
        if self.killed: return False, self.reason.value
        ok, r = self.env.check_budget(cost)
        if not ok: self.reason = StopReason.BUDGET; return False, r
        ok, r = self.env.check_action(action)
        if not ok: self.reason = StopReason.SCOPE; return False, r
        self.recent.append(action)
        if len(self.recent) >= self.max_idle and len(set(self.recent[-self.max_idle:])) == 1:
            self.reason = StopReason.STALL
            return False, f"Stall: '{action}' repeated {self.max_idle}x"
        return True, "ok"


def demo_safety_monitor():
    print("\n--- Kill Switch / Safety Monitor ---")
    mon = SafetyMonitor(AutonomyEnvelope(max_budget=1.0, max_calls=10), max_idle=3)
    ok, _ = mon.check("read", 0.05)
    print(f"  read: allowed={ok}")
    ok, r = mon.check("deploy", 0.05)
    print(f"  deploy: allowed={ok}, reason={r}")
    mon.reason = StopReason.RUNNING
    for i in range(4):
        ok, r = mon.check("read", 0.05)
        if not ok: print(f"  Stall at iter {i+1}: {r}"); break
    mon2 = SafetyMonitor(AutonomyEnvelope(), max_idle=5)
    mon2.kill()
    ok, r = mon2.check("read")
    print(f"  After kill: allowed={ok}, reason={r}")


# ================================================================
# Section 4: Human-in-the-Loop Gate
# ================================================================
# Pause for approval on high-risk actions. Deny-on-timeout is safe default.

HIGH_RISK = {"external_write", "purchase", "send_email", "delete", "merge"}


class Approval(Enum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    TIMED_OUT = "TIMED_OUT"


def request_approval(action: str, args: dict, auto: bool = False) -> Approval:
    if action not in HIGH_RISK:
        return Approval.APPROVED
    if auto:
        print(f"    [HITL] {action}({args}) -> auto-approved")
        return Approval.APPROVED
    print(f"    [HITL] {action}({args}) -> timed out, DENIED")
    return Approval.TIMED_OUT


def demo_hitl():
    print("\n--- Human-in-the-Loop Gate ---")
    print(f"  read: {request_approval('read', {}).value}")
    print(f"  email: {request_approval('send_email', {'to': 'x@co'}, auto=True).value}")
    print(f"  purchase: {request_approval('purchase', {'amt': 500}).value}")


# ================================================================
# Section 5: Autonomous Agent Loop with All Safety Controls
# ================================================================
# Integrates envelope + checkpoint + monitor + HITL into one loop.


def autonomous_loop(objective: str, tasks: list[dict], budget: float = 5.0):
    env = AutonomyEnvelope(objective=objective, max_budget=budget, max_calls=20,
                           allowed=["read", "write_sandbox", "test", "open_pr", "send_email"],
                           denied=["deploy", "merge"])
    mon = SafetyMonitor(env, max_idle=3)
    cp = AgentCheckpoint(run_id=env.run_id, step=0, objective=objective, budget_left=budget)
    print(f"  Run {env.run_id}: {objective}")
    for i, t in enumerate(tasks):
        action, cost = t["action"], t.get("cost", 0.1)
        ok, r = mon.check(action, cost)
        if not ok: print(f"  Step {i+1} BLOCKED: {r}"); break
        if action in HIGH_RISK:
            appr = request_approval(action, t.get("args", {}), t.get("auto", False))
            if appr != Approval.APPROVED: continue
        env.charge(cost)
        print(f"  Step {i+1} OK: {action} (${cost:.2f}, total=${env.spent:.2f}/${env.max_budget:.2f})")
        cp.step, _ = i + 1, cp.milestones.append(action)
    status = mon.reason.value if mon.reason != StopReason.RUNNING else "SUCCESS"
    print(f"  Terminal: {status} | Done: {cp.milestones} | Spent: ${env.spent:.2f}")


def demo_full_loop():
    print("\n--- Autonomous Agent Loop (All Controls) ---")
    autonomous_loop("Fix bug #1234 and notify team", [
        {"action": "read", "cost": 0.10}, {"action": "write_sandbox", "cost": 0.20},
        {"action": "test", "cost": 0.30},
        {"action": "send_email", "cost": 0.15, "args": {"to": "team@co"}, "auto": True},
        {"action": "deploy", "cost": 0.50}, {"action": "open_pr", "cost": 0.25},
    ], budget=2.0)


# ================================================================
# Main -- run all demos
# ================================================================

if __name__ == "__main__":
    demo_autonomy_envelope()
    demo_checkpoint_resume()
    demo_safety_monitor()
    demo_hitl()
    demo_full_loop()
