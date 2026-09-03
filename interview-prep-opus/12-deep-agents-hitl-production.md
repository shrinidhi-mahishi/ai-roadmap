# Module 12: Deep Agents -- Human-in-the-Loop & Production Systems

## What Is This?

Human-in-the-loop (HITL) is like having a manager approve expense reports above a certain
threshold -- the agent works autonomously on routine tasks but pauses for human review on
high-risk actions. A sales agent can look up CRM records freely (Tier 1, read-only), but
before it sends a pricing proposal to a customer (Tier 4, irreversible), it stops, shows the
human what it wants to do, and waits for approval, edits, or rejection.

In production, this means the agent's state is checkpointed to a database. The agent can
wait minutes, hours, or days for a human decision. When the human responds, the agent
resumes exactly where it left off -- same state, same context, same thread. The mechanism
is a "resumable exception": execution pauses, state serializes, and a `Command(resume=...)`
re-enters the graph with the human's decision.

This module covers how to build these systems at enterprise scale: the interrupt architecture,
permission models, deployment infrastructure, failure modes that only appear in production,
and the distributed systems problems that masquerade as "AI problems."

## Why It Matters

61% of large enterprises now run at least one production AI agent system (Gartner 2026), up
from 18% in 2024. Multi-agent reliability sits at 56.6% task success across 4.5M production
runs -- meaning retry, recovery, and human oversight design determine whether an agent system
is usable. Director/VP roles require you to design the guardrails, not just the model.

---

## Part 1: System Topology & Data Flow

### 1.1 Production Architecture Diagram

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                        CONTROL PLANE                                │
 │  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────────┐  │
 │  │  LangSmith   │  │  Helm/K8s    │  │  Auth Provider (OAuth2)    │  │
 │  │  Observability│  │  Orchestrator│  │  RBAC + Token Refresh      │  │
 │  └──────┬──────┘  └──────┬───────┘  └────────────┬───────────────┘  │
 │         │                │                       │                  │
 └─────────┼────────────────┼───────────────────────┼──────────────────┘
           │                │                       │
 ┌─────────┼────────────────┼───────────────────────┼──────────────────┐
 │         │          DATA PLANE                    │                  │
 │         ▼                ▼                       ▼                  │
 │  ┌─────────────────────────────────────────────────────────────┐    │
 │  │                   LangGraph Server (uvicorn)                │    │
 │  │  ┌───────────┐  ┌────────────────┐  ┌───────────────────┐  │    │
 │  │  │ Deep Agent │  │ HITL Middleware │  │ Permission Engine │  │    │
 │  │  │  (Graph)   │──│ interrupt_on   │──│ First-Match-Wins  │  │    │
 │  │  └─────┬─────┘  └───────┬────────┘  └───────────────────┘  │    │
 │  │        │                │                                   │    │
 │  │        │    ┌───────────▼───────────┐                       │    │
 │  │        │    │  Checkpoint Manager   │                       │    │
 │  │        │    │  (serialize/resume)   │                       │    │
 │  │        │    └───────────┬───────────┘                       │    │
 │  │        │                │                                   │    │
 │  └────────┼────────────────┼───────────────────────────────────┘    │
 │           │                │                                        │
 │  ┌────────▼────────┐  ┌───▼──────────────┐  ┌──────────────────┐   │
 │  │  Redis (pub-sub) │  │  PostgreSQL      │  │  Object Store    │   │
 │  │  Stream broker   │  │  Checkpoints     │  │  (S3/GCS)        │   │
 │  │  for real-time   │  │  Threads + Runs  │  │  Large payloads  │   │
 │  │  output          │  │  Task queue      │  │  Documents       │   │
 │  └─────────────────┘  │  Long-term memory │  └──────────────────┘   │
 │                        └──────────────────┘                         │
 └─────────────────────────────────────────────────────────────────────┘
           │                │                       │
 ┌─────────▼────────────────▼───────────────────────▼──────────────────┐
 │                       TELEMETRY PLANE                               │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
 │  │  Trace Export │  │  Metrics     │  │  Audit Log               │   │
 │  │  (async, zero │  │  P50/P99    │  │  Every interrupt/resume  │   │
 │  │   app latency)│  │  cost/token │  │  + decision + identity   │   │
 │  └──────────────┘  └──────────────┘  └──────────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request Flow Narrative

1. **Client submits** a message with `thread_id` and `context` (user identity, feature flags).
2. **LangGraph Server** loads or creates the thread, hydrates state from the last PostgreSQL
   checkpoint.
3. **Deep Agent graph** executes nodes. Each superstep writes a checkpoint. The HITL middleware
   inspects every tool call against the `interrupt_on` configuration.
4. **If interrupt triggers**: execution raises a resumable exception, serializes full state to
   PostgreSQL, and returns an `interrupts` payload to the caller with the proposed action(s).
5. **Human reviews** via UI, Slack, email queue, or API. The system waits indefinitely -- there
   is no built-in timeout (you must build one externally).
6. **Human sends** `Command(resume={"decisions": [...]})` with the same `thread_id`.
7. **Agent resumes** from the exact checkpoint. If decision is `approve`, the tool executes
   with original args. If `edit`, args are modified first. If `reject`, rejection feedback
   becomes the tool result. If `respond`, the human message becomes a synthetic tool result.
8. **Stream events** flow through Redis pub-sub to the frontend in real time. Token-level
   streaming (`messages` mode) fires every LLM token at ~0.4s first-token latency.
9. **Traces export** asynchronously to LangSmith with zero application latency impact.

---

## Part 2: Core Mechanics & Algorithms

### 2.1 HITL Interrupt Patterns

**Four decision types and their semantics:**

| Decision  | What Happens                              | Danger If Misused                              |
|-----------|-------------------------------------------|------------------------------------------------|
| `approve` | Tool executes with original arguments     | None -- this is the safe default               |
| `edit`    | Arguments modified, then tool executes    | Large edits can trigger model re-evaluation    |
| `reject`  | Tool skipped, rejection feedback returned | Vague rejections cause 2-3 retry LLM calls     |
| `respond` | Human message becomes tool result         | If used to "deny" a side-effecting tool, the agent treats the denial as success |

**Conditional interrupts** avoid over-gating. A `when` predicate receives the `ToolCallRequest`
and returns `True` (interrupt) or `False` (auto-approve):

```python
def writes_outside_workspace(request: ToolCallRequest) -> bool:
    path = request.tool_call["args"].get("file_path", "")
    return not path.startswith("/workspace/")

interrupt_on = {
    "write_file": {
        "allowed_decisions": ["approve", "edit", "reject"],
        "when": writes_outside_workspace,
    },
}
```

**Subagent interrupt inheritance**: subagents inherit parent `interrupt_on` by default.
Setting `interrupt_on` on a subagent spec completely replaces parent rules -- no merge.
This enables principle-of-least-privilege per delegation level.

### 2.2 Permission Model (First-Match-Wins)

Permissions are declared as ordered `FilesystemPermission` rules. Evaluation stops at the
first matching rule. If no rule matches, the operation is **allowed** (permissive default).

**Critical ordering requirement** -- specific denies must precede general allows:

```python
permissions = [
    # Rule 1: Block secrets (matches first)
    FilesystemPermission(operations=["read", "write"], paths=["/workspace/.env"], mode="deny"),
    # Rule 2: Allow workspace (matches second)
    FilesystemPermission(operations=["read", "write"], paths=["/workspace/**"], mode="allow"),
    # Rule 3: Block everything else
    FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
]
```

**Scope limitation**: permissions apply only to built-in filesystem tools. Custom tools
and MCP tools are NOT covered. Sandbox backends bypass permissions entirely.

**Interrupt mode** (deepagents >= 0.6.8): `mode="interrupt"` pauses for human approval
instead of hard blocking. Best practice: anchor patterns with a literal leading segment
(`/secrets/**`) to avoid over-firing.

### 2.3 Four-Tier Action Risk Classification

| Tier | Category              | Examples                          | Approval Policy               |
|------|-----------------------|-----------------------------------|-------------------------------|
| 1    | Read-only             | Queries, retrievals, analysis     | Fully autonomous              |
| 2    | Reversible writes     | Draft creation, internal state    | Autonomous with audit logging |
| 3    | External side effects | Third-party API calls, emails     | Staging queue or confidence-based review |
| 4    | High-risk/Irreversible| Production deploys, payments, data deletion | Mandatory human approval, no exceptions |

Enforcement must happen at the **workflow execution layer**, not negotiated by the AI at
runtime. The agent should never decide its own oversight level.

### 2.4 Production Deployment Patterns

**Docker**: Use Debian-based slim images, not Alpine. musl libc causes C-extension
compilation failures for Python AI workloads. Pin LangGraph version explicitly -- `latest`
tag introduces breaking changes.

**Kubernetes**: Official Helm chart (v0.2.6+). Use PostgreSQL for checkpoints to share
context across replicas. MongoDB bundled for dev only. Readiness probes intercept traffic
until the container is healthy. Set `--timeout-keep-alive 65` in uvicorn to survive
30-60s LLM calls that would otherwise hit AWS API Gateway's 29s timeout.

**Scaling**: Single container bottlenecks on Python GIL at 1,000+ concurrent requests.
Scale horizontally with multiple LangGraph containers behind a reverse proxy. For deep
agent workflows spawning many subagents, set `recursionLimit` high (e.g., 10,000).

### 2.5 LangSmith Observability

Full agent tracing with nested call trees, per-step latency and token cost, custom
dashboards (P50/P99 latency, error rates, cost breakdowns, feedback scores). Traces
export via async callback handler -- zero application latency overhead. SmithDB serves
P50 trace tree loads in ~92ms.

**LangSmith Engine** (2026): AI-assisted debugging that clusters production failures into
prioritized issues, finds root cause in traces and code, and proposes fixes.

**Decision heuristic for tooling**: If you are on LangGraph, use LangSmith. If
framework-agnostic, use Langfuse (open-source, self-hostable, better economics at scale).
If eval rigor is the priority, use Arize Phoenix.

---

## Part 3: Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Per-run cost with HITL:**

```
C_run = C_llm + C_checkpoint + C_rejection_overhead

Where:
  C_llm         = (input_tokens * price_per_input_token) + (output_tokens * price_per_output_token)
  C_checkpoint  = num_supersteps * checkpoint_write_cost  (~1-5ms DB write, negligible $)
  C_rejection   = num_rejections * avg_retry_llm_calls * C_llm_per_call
```

**State size drives checkpoint cost**: A research agent storing a 40-page PDF in state creates
megabyte-scale checkpoints. Storing S3 references instead keeps checkpoints at kilobyte-scale
-- a 100-1000x difference in serialization cost and DB storage.

**Framework comparison at 1,000 daily runs (3-step task, 2026 benchmarks):**

| Framework | Monthly Cost | Primary Cost Driver                         |
|-----------|-------------|---------------------------------------------|
| LangGraph | ~$63        | Explicit node structure eliminates redundant LLM calls |
| CrewAI    | $78-102     | Moderate overhead from crew delegation       |
| AutoGen   | $84-171     | Unbounded conversation loops can consume 5-10x expected tokens |

**Rejection overhead**: Vague rejections ("no, do something else") trigger 2-3 additional LLM
calls as the model explores alternatives. Well-crafted rejections with domain-specific guidance
("Do not retry. Ask the user which file to archive.") resolve in a single retry.

### 3.2 Latency SLA Targets

| Metric | Target (autonomous) | Target (HITL path)    | Notes                              |
|--------|---------------------|-----------------------|------------------------------------|
| p50    | 2-5s                | Human response time + 2-5s | Human is the bottleneck          |
| p95    | 8-15s               | 4 hours (Tier 1 SLA)  | Async queue model                  |
| p99    | 25-30s              | 24 hours              | Complex approval chains            |
| TTFT   | 0.4s (streaming)    | 0.4s after resume     | First token to UI                  |

**Gateway timeout risk**: AWS API Gateway closes connections at 29s. LLM calls routinely take
30-60s. Without streaming or `--timeout-keep-alive 65`, requests drop silently at p95+.

### 3.3 Human Response Time Impact on Agent Throughput

~66% of production agents already tolerate minute-plus approval latency. But without defined
SLAs, approval queues grow unbounded and agents stall indefinitely.

**Tiered escalation with SLAs:**

| Tier | Trigger                        | SLA        | Routing                    |
|------|--------------------------------|------------|----------------------------|
| 1    | Moderate-confidence actions    | 4 hours    | Async queue                |
| 2    | Low-confidence / high-blast    | 1 hour     | Priority queue + escalation|
| 3    | Compliance-sensitive           | 15 minutes | Sync with on-call paging   |

**Stale execution risk**: If an agent waits days for approval, its context may be invalid.
OAuth tokens expire (HubSpot ~30min, Google ~1hr, Salesforce ~2hr), pagination cursors go
stale. Verify action hash on resume to prevent executing against changed state.

### 3.4 Availability & Recovery Targets

| Metric | Target          | Rationale                                           |
|--------|-----------------|-----------------------------------------------------|
| Availability | 99.9% (3-nines) | Standard for internal enterprise tooling        |
| RPO    | 0 (zero data loss) | Every superstep checkpointed to PostgreSQL      |
| RTO    | < 5 minutes     | Restart container, reconnect to PostgreSQL, resume from last checkpoint |

**LangSmith pricing (observability budget):**

| Plan       | Price            | Trace Volume     |
|------------|------------------|------------------|
| Developer  | Free             | 5,000/month      |
| Plus       | $39/seat/month   | Scaled volume    |
| Enterprise | Custom           | SLA guarantees   |

At scale, self-hosted Langfuse is more economical. Trace volume is the primary bill driver.

---

## Part 4: Distributed Resilience & Security

### 4.1 Durable Execution with Checkpointing

**What LangGraph checkpointing provides:**
- State saved at each superstep boundary
- Resume from last checkpoint after crash
- Indefinite interrupt pauses (HITL can wait days)
- Time travel to any checkpointed step
- Complete state history for audit compliance

**What checkpointing does NOT provide (the durable execution gap):**

| Gap                        | Consequence                                          | Mitigation                           |
|----------------------------|------------------------------------------------------|--------------------------------------|
| No failure detection       | Process crashes silently -- no supervisor, no heartbeat | External health checks, K8s liveness probes |
| No duplicate prevention    | Two processes can resume same `thread_id` simultaneously | External distributed locking (Redis/Postgres advisory locks) |
| Single-process architecture| No task queue, no worker pool, no placement logic    | External job scheduler (Celery, K8s Jobs) |
| Manual recovery            | Developers must detect failures and trigger resumption | Dead-letter queue with monitoring     |
| Replay non-determinism     | `datetime.now()` or live API reads differ on replay  | Idempotency keys, deterministic node design |

**The structural insight** (Diagrid): "The gap is between saving state and guaranteeing
completion. Adding a better checkpointer doesn't close the gap." True durable execution
(Temporal, Dapr) provides automatic failure recovery with heartbeat detection, replay-based
resumption with cached results, and zero recovery code in the workflow definition.

### 4.2 Failure Taxonomy

**Transient failures** (retry-safe):
- LLM API rate limits (429)
- Network timeouts
- Temporary service unavailability
- Response includes `retryable: true` and `retry_after_seconds`

**Permanent failures** (require intervention):
- Invalid credentials / revoked tokens
- Schema violations in tool output
- Budget exhaustion
- Regulatory block (PII detected, compliance gate failed)

**Silent failures** (most dangerous):
- `MemorySaver` state corruption under concurrent access -- no error raised, just wrong results
- Confidence miscalibration: model claims 90% confidence, actual accuracy ~75% (15pp gap)
- Compound chain failure: three agents at 90% confidence each yield ~42% actual reliability

**The distributed systems insight**: "Teams blame models for failures that are actually
architectural: the agent 'hallucinated' because it was missing state after a restart; the
agent 'looped' because retries weren't bounded. These aren't AI problems -- they're
distributed systems problems wearing AI clothes."

### 4.3 Tiered Recovery Strategy

```
1. RETRY    ── Bounded, with idempotency keys. Never unbounded.
     │
     ▼ (exhausted)
2. FALLBACK ── Switch to simpler model or cached response.
     │
     ▼ (unavailable)
3. RESUME   ── Reload from checkpoint with state verification.
     │
     ▼ (state invalid)
4. COMPENSATE ── Saga-style backward walk. Hard-to-reverse actions at end
     │           of saga so early failures only undo cheap operations.
     ▼ (compensation fails)
5. DEAD-LETTER ── Human review queue. This is the final safety net.
```

**Circuit breakers for agents**: Must watch beyond HTTP failures. Agent dependencies can
return HTTP 200 with unusable output -- malformed JSON, schema violations, repeated invalid
responses. Circuit breaker must inspect response quality, not just status codes.

### 4.4 Zero-Trust, RBAC, and PII Filtering

**Three authentication layers for multi-tenancy:**

| Layer                    | Purpose                              | Mechanism                          |
|--------------------------|--------------------------------------|------------------------------------|
| End-user auth            | Establish identity                   | OAuth2 / OIDC                      |
| Agent-acting-as-user     | Per-user credentials for external APIs | OAuth via Agent Auth, auto-refresh |
| Team RBAC                | Control deployment/monitoring access | Role-based access control          |

**Secret management**: Use an auth proxy that intercepts outbound requests and injects
credentials. API keys never appear in sandbox code, environment variables, or logs.

**PII filtering**: Middleware-based with strategies -- `redact` (remove), `mask` (partial
hide), `hash` (one-way), `block` (reject entire request). Applied at input and/or output.

**Audit trail requirements**: Every interrupt, resume, decision, identity, and timestamp
must be logged. Session IDs enable correlation across the full agent execution chain.

### 4.5 Regulatory Compliance

| Regulation                      | Effective Date   | Key Requirement                                        |
|---------------------------------|------------------|--------------------------------------------------------|
| EU AI Act Article 14            | August 2, 2026   | Mandates human ability to "intervene, stop, or override" high-risk AI |
| NIST AI Agent Standards         | February 2026    | Moves from experimentation to infrastructure requirements |
| California SB-833              | July 1, 2026     | State-level agent oversight requirements               |
| OWASP LLM Top 10               | Ongoing          | "Excessive Agency" as dedicated risk class; prompt injection ranked #1 |

**Autonomy tier framework (mapped to NIST AI RMF):**

| Tier | Mode                | Oversight Cadence       |
|------|---------------------|-------------------------|
| 1    | Supervised          | Continuous assessment   |
| 2    | Constrained         | Annual assessment       |
| 3    | Monitored           | Quarterly assessment    |
| 4    | Full autonomy       | Monthly + kill-switch   |

---

## Part 5: Production Enterprise Code

### 5.1 Complete HITL Agent with Interrupt Configuration

```python
"""
Production HITL agent with tiered interrupt configuration,
conditional gating, and permission-based filesystem access.
"""

from uuid import uuid4
from dataclasses import dataclass
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import interrupt, Command
from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def read_file(file_path: str) -> str:
    """Read a file from the workspace."""
    with open(file_path, "r") as f:
        return f.read()


@tool
def write_file(file_path: str, content: str) -> str:
    """Write content to a file in the workspace."""
    with open(file_path, "w") as f:
        f.write(content)
    return f"Wrote {len(content)} bytes to {file_path}"


@tool
def delete_file(file_path: str) -> str:
    """Delete a file from the workspace. Irreversible."""
    import os
    os.remove(file_path)
    return f"Deleted {file_path}"


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient."""
    # Production: integrate with SendGrid, SES, etc.
    return f"Email sent to {to}: {subject}"


@tool
def request_human_input(question: str) -> str:
    """Ask the human operator a question and wait for their response."""
    response = interrupt({
        "type": "question",
        "question": question,
    })
    return response.get("answer", "No answer provided")


# ---------------------------------------------------------------------------
# Conditional interrupt predicate
# ---------------------------------------------------------------------------

def writes_outside_workspace(request) -> bool:
    """Only interrupt file writes that target paths outside /workspace/."""
    path = request.tool_call["args"].get("file_path", "")
    return not path.startswith("/workspace/")


# ---------------------------------------------------------------------------
# Agent configuration
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    user_id: str
    org_id: str
    environment: str  # "staging" or "production"


DB_URI = "postgresql://user:pass@localhost:5432/langgraph_prod"

with PostgresSaver.from_conn_string(DB_URI) as checkpointer:
    from langchain_deepagents import create_deep_agent, FilesystemPermission

    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=[read_file, write_file, delete_file, send_email, request_human_input],

        # Tiered interrupt configuration
        interrupt_on={
            "read_file": False,                           # Tier 1: autonomous
            "write_file": {                               # Tier 2/3: conditional
                "allowed_decisions": ["approve", "edit", "reject"],
                "when": writes_outside_workspace,
            },
            "delete_file": True,                          # Tier 4: always interrupt
            "send_email": {                               # Tier 3: external side effect
                "allowed_decisions": ["approve", "edit", "reject"],
            },
        },

        # Filesystem permissions (first-match-wins)
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/workspace/.env", "/workspace/.secrets/**"],
                mode="deny",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/workspace/**"],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["write"],
                paths=["/config/**"],
                mode="interrupt",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],

        checkpointer=checkpointer,
        context_schema=AgentContext,
    )
```

### 5.2 Approval Handler with Escalation

```python
"""
Production approval handler: processes interrupts, applies tiered
escalation, enforces SLAs, prevents stale execution.
"""

import hashlib
import json
import time
from datetime import datetime, timedelta


def compute_action_hash(action_requests: list[dict]) -> str:
    """Deterministic hash of proposed actions for stale-execution detection."""
    canonical = json.dumps(action_requests, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def classify_escalation_tier(action_requests: list[dict]) -> int:
    """Map action requests to escalation tier based on tool risk."""
    tier_map = {
        "delete_file": 3,
        "send_email": 2,
        "write_file": 1,
        "read_file": 0,
    }
    return max(tier_map.get(ar["tool_name"], 1) for ar in action_requests)


ESCALATION_SLAS = {
    1: timedelta(hours=4),      # Moderate-confidence actions
    2: timedelta(hours=1),      # Low-confidence / high-blast-radius
    3: timedelta(minutes=15),   # Compliance-sensitive
}


def build_escalation_context(action_requests: list[dict], agent_reasoning: str) -> dict:
    """Build the minimum context package for human reviewers."""
    return {
        "actions_plain_language": [
            f"{ar['tool_name']}({ar['args']})" for ar in action_requests
        ],
        "agent_reasoning": agent_reasoning,
        "reversibility": all(
            ar["tool_name"] not in ("delete_file", "send_email")
            for ar in action_requests
        ),
        "action_hash": compute_action_hash(action_requests),
        "approval_deadline": (
            datetime.utcnow() + ESCALATION_SLAS.get(
                classify_escalation_tier(action_requests), timedelta(hours=4)
            )
        ).isoformat(),
        "session_id": None,  # Set by caller
    }


def handle_interrupt(result, config: dict, agent, stored_hash: str | None = None):
    """
    Process an interrupt result. Returns the resumed agent result.

    In production, the 'get_human_decision' step would be an async
    queue (Slack, email, web UI) rather than a synchronous call.
    """
    if not result.interrupts:
        return result

    action_requests = result.interrupts[0].value["action_requests"]
    current_hash = compute_action_hash(action_requests)

    # Stale execution guard
    if stored_hash and current_hash != stored_hash:
        decisions = [{
            "type": "reject",
            "message": "Action context has changed since original proposal. Re-evaluate.",
        }]
    else:
        tier = classify_escalation_tier(action_requests)
        context = build_escalation_context(action_requests, agent_reasoning="")
        context["session_id"] = config["configurable"]["thread_id"]

        # In production: enqueue to approval system, return pending status.
        # Here we simulate immediate approval for Tier 1, rejection for Tier 3.
        if tier <= 1:
            decisions = [{"type": "approve"} for _ in action_requests]
        elif tier >= 3:
            decisions = [{
                "type": "reject",
                "message": "Tier 3 action requires explicit human review. "
                           "Queued for on-call approval.",
            } for _ in action_requests]
        else:
            decisions = [{"type": "approve"} for _ in action_requests]

    return agent.invoke(
        Command(resume={"decisions": decisions}),
        config=config,
        version="v2",
    )
```

### 5.3 Production Docker Compose

```yaml
# docker-compose.prod.yml
# LangGraph production deployment with PostgreSQL + Redis

version: "3.9"

services:
  langgraph-server:
    image: langgraph-app:0.4.2       # Pin version, never use 'latest'
    build:
      context: .
      dockerfile: Dockerfile          # Use Debian-slim, NOT Alpine
    ports:
      - "8123:8000"
    environment:
      - DATABASE_URI=postgresql://lguser:lgpass@postgres:5432/langgraph
      - REDIS_URI=redis://redis:6379
      - LANGSMITH_API_KEY=${LANGSMITH_API_KEY}
      - LANGSMITH_PROJECT=prod-agents
    command: >
      uvicorn langgraph_app.server:app
      --host 0.0.0.0
      --port 8000
      --timeout-keep-alive 65
      --workers 4
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "2.0"

  postgres:
    image: postgres:16-bookworm
    environment:
      - POSTGRES_USER=lguser
      - POSTGRES_PASSWORD=lgpass
      - POSTGRES_DB=langgraph
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U lguser -d langgraph"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-bookworm
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

### 5.4 Checkpoint Cleanup Job

```python
"""
Background job to purge terminal-state checkpoints on a retention schedule.
Without this, checkpoint tables grow unbounded.
"""

import psycopg2
from datetime import datetime, timedelta

DB_URI = "postgresql://lguser:lgpass@localhost:5432/langgraph"
RETENTION_DAYS = 30
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def cleanup_stale_checkpoints():
    """Delete checkpoints for threads that reached terminal state
    more than RETENTION_DAYS ago."""
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)

    conn = psycopg2.connect(DB_URI)
    try:
        with conn.cursor() as cur:
            # Find terminal threads past retention
            cur.execute("""
                DELETE FROM checkpoints
                WHERE thread_id IN (
                    SELECT thread_id FROM threads
                    WHERE status IN %s
                      AND updated_at < %s
                )
            """, (TERMINAL_STATUSES, cutoff))

            deleted = cur.rowcount
            conn.commit()
            print(f"Purged {deleted} checkpoints older than {cutoff.isoformat()}")
    finally:
        conn.close()


if __name__ == "__main__":
    cleanup_stale_checkpoints()
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Enterprise Document Processing Pipeline with Tiered Human Review

**Problem Statement**: A financial services firm processes 5,000 loan applications daily.
Each application requires document extraction (W-2s, bank statements, tax returns), data
validation, credit risk scoring, and a final approval decision. Regulatory requirements
(CFPB, ECOA) mandate human review for any denial and for applications above $500K. Current
manual process takes 4-6 hours per application. Target: 80% fully automated, 20% human
review, 30-minute average processing time.

**Architecture**:

```
┌─────────────┐     ┌──────────────────────────────────────────────────┐
│  Document    │     │            Agent Pipeline (LangGraph)            │
│  Ingestion   │────▶│                                                  │
│  (S3 upload) │     │  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
└─────────────┘     │  │ Extract  │─▶│ Validate │─▶│ Risk Score   │  │
                    │  │ Agent    │  │ Agent    │  │ Agent        │  │
                    │  └──────────┘  └──────────┘  └──────┬───────┘  │
                    │                                      │          │
                    │                    ┌─────────────────┤          │
                    │                    ▼                 ▼          │
                    │           ┌──────────────┐  ┌────────────┐     │
                    │           │ Auto-Approve  │  │ HITL Queue │     │
                    │           │ (Tier 1-2)    │  │ (Tier 3-4) │     │
                    │           └──────┬───────┘  └─────┬──────┘     │
                    │                  │                 │            │
                    │                  ▼                 ▼            │
                    │           ┌─────────────────────────────┐      │
                    │           │     Decision + Audit Log     │      │
                    │           └─────────────────────────────┘      │
                    └──────────────────────────────────────────────────┘
```

**Interrupt routing logic**:
- Score > 700 AND amount < $500K: auto-approve (Tier 1)
- Score 600-700 OR amount $500K-$2M: async review queue, 4-hour SLA (Tier 2)
- Score < 600 OR denial: mandatory senior review, 1-hour SLA (Tier 3)
- Any ECOA-flagged demographic correlation: compliance officer, 15-min SLA (Tier 3)

**Trade-off Matrix**:

| Dimension           | Chosen Approach                  | Alternative                    | Rationale                                           |
|----------------------|----------------------------------|--------------------------------|-----------------------------------------------------|
| Checkpoint backend  | PostgreSQL                       | DynamoDB                       | ACID guarantees critical for financial audit trail; team already runs Postgres |
| State storage       | S3 references in state           | Documents inline in state      | 100x smaller checkpoints; documents average 2MB each |
| Review queue        | Async with SLA tiers             | Synchronous blocking           | 66% of reviews take 10+ minutes; sync blocks agent threads |
| Risk model          | Agent calls internal ML API      | Agent runs model in-process    | Separation of concerns; model versioning independent of agent deployment |
| Observability       | LangSmith (managed)              | Langfuse (self-hosted)         | Compliance team requires vendor SLA; volume < 50K traces/month |

**Decision rationale**: The async review queue with tiered SLAs is the critical design
decision. Synchronous review would require maintaining 1,000+ concurrent agent threads
waiting for humans (at $500K+ annual compute cost). Async queue with checkpointed state
lets agents release resources during the wait. The stale-execution guard (action hash
verification) prevents approving a loan application whose credit score changed during the
review period.

---

### Scenario 2: Multi-Agent Customer Support System with Escalation Chain

**Problem Statement**: A SaaS company handles 12,000 support tickets daily across billing,
technical, and account management. Current L1 agents resolve 45% autonomously. Goal:
increase autonomous resolution to 75% while ensuring zero unauthorized account changes
(billing modifications, subscription cancellations, data exports). The system must comply
with GDPR Article 22 (right to human review of automated decisions affecting the user).

**Architecture**:

```
┌──────────┐    ┌────────────────────────────────────────────────────────┐
│ Ticket   │    │              Orchestrator Agent                        │
│ Intake   │───▶│  (classifies intent, routes to specialist subagent)   │
│ (API/UI) │    │                                                        │
└──────────┘    │  ┌─────────────┐ ┌────────────┐ ┌──────────────────┐  │
                │  │ Billing     │ │ Technical  │ │ Account Mgmt     │  │
                │  │ Subagent    │ │ Subagent   │ │ Subagent         │  │
                │  │             │ │            │ │                  │  │
                │  │ interrupt:  │ │ interrupt: │ │ interrupt:       │  │
                │  │  refund>$50 │ │  none      │ │  ALL mutations   │  │
                │  │  plan_change│ │  (Tier 1)  │ │  (Tier 4)        │  │
                │  │  (Tier 3)   │ │            │ │                  │  │
                │  └──────┬──────┘ └─────┬──────┘ └────────┬─────────┘  │
                │         │              │                 │             │
                │         ▼              ▼                 ▼             │
                │  ┌─────────────────────────────────────────────────┐   │
                │  │           Shared Approval Queue                 │   │
                │  │  Tier 1: auto  │ Tier 2: async │ Tier 3: paged │   │
                │  └─────────────────────────────────────────────────┘   │
                └────────────────────────────────────────────────────────┘
                         │
                         ▼
                ┌────────────────────┐
                │  Feedback Loop     │
                │  Rejected actions  │──▶ Retrain / adjust thresholds
                │  become labels     │
                └────────────────────┘
```

**Subagent permission isolation** (no merge -- complete replacement):
- Billing subagent: read CRM + billing API, write restricted to draft invoices
- Technical subagent: read knowledge base + logs, no write access to customer data
- Account subagent: all mutations require interrupt, no autonomous writes

**Trade-off Matrix**:

| Dimension            | Chosen Approach                   | Alternative                      | Rationale                                           |
|-----------------------|-----------------------------------|----------------------------------|-----------------------------------------------------|
| Subagent permissions | Complete replacement per subagent | Inherited from parent            | Principle of least privilege; billing subagent must not inherit technical's read-all-logs permission |
| GDPR compliance      | Mandatory interrupt on all account decisions | Confidence-based gating   | Article 22 requires human review right; confidence thresholds are not legally defensible |
| Escalation timeout   | 24-hour TTL, auto-reject + notify | No timeout (wait forever)        | Prevents queue buildup; SLA breach alerts at 80% of TTL |
| Feedback loop        | Rejected actions become training labels | Manual periodic review     | Active learning from HITL signals reduces rejection rate by ~15% per quarter (directional) |
| State persistence    | PostgreSQL with 90-day retention  | DynamoDB with S3 overflow        | GDPR data subject access requests require queryable audit trail; Postgres SQL is simpler than DynamoDB scans |

**Decision rationale**: The critical design choice is complete permission replacement on
subagents rather than inheritance. Inherited permissions create a "privilege creep" risk
where a newly added tool on the parent agent silently becomes available to all subagents.
With complete replacement, each subagent's access is explicitly declared and auditable --
a requirement for SOC 2 Type II and GDPR accountability. The feedback loop from reviewer
decisions closes the automation-improvement cycle: every rejection is a labeled training
example that improves the agent's future decisions.

---

## Key Interview Signals

**When asked "How do you design human oversight for production agents?":**
Lead with the four-tier risk classification, then the async approval architecture with
SLA tiers. Mention that synchronous approval collides with gateway timeouts and token
expiry. Show you understand the stale-execution problem (action hash verification on
resume). Close with the regulatory drivers -- EU AI Act Article 14 makes HITL legally
mandatory for high-risk systems as of August 2026.

**When asked "What goes wrong with agents in production?":**
Lead with the 56.6% task success rate statistic. Then the distributed-systems framing:
"Most agent failures are architectural, not model failures." Hit the top three: MemorySaver
in production (silent state corruption), confidence miscalibration (90% claimed vs 75%
actual), and the durable execution gap (checkpoints save state but don't guarantee
completion).

**When asked "How do you scale agent systems?":**
LangGraph bottlenecks on Python GIL at 1,000+ concurrent requests. Scale horizontally
behind a reverse proxy. PostgreSQL for shared checkpoint state across replicas. Redis for
real-time streaming pub-sub. The real scaling bottleneck is human review throughput, not
compute -- design the escalation SLAs before the infrastructure.
