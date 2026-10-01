# Module 01: Spec-Driven Development for AI Agents

**Objective**: Master the architecture, economics, resilience patterns, and security posture of Spec-Driven Development (SDD) for production AI agent systems. Prepare for Principal/Director-level system design interviews where the question is: "Design an enterprise AI agent platform that ships reliably."

---

## 1. System Topology & Data Flow

### Architecture Diagram

```
                              CONTROL PLANE
 ┌──────────────────────────────────────────────────────────────────────┐
 │                                                                      │
 │  ┌─────────────┐    ┌──────────────┐    ┌──────────────────────┐    │
 │  │   Spec       │───>│  Planner     │───>│  Task Decomposer    │    │
 │  │   Registry   │    │  Agent       │    │  (DAG Generator)     │    │
 │  │  (Git-backed │<───│              │<───│                      │    │
 │  │   Markdown)  │    └──────────────┘    └──────────┬───────────┘    │
 │  └──────┬───────┘                                   │               │
 │         │            ┌──────────────┐               │               │
 │         └───────────>│  Coordinator │<──────────────┘               │
 │                      │  (Supervisor)│                               │
 │                      └──────┬───────┘                               │
 │                             │                                       │
 │              ┌──────────────┼──────────────┐                        │
 │              v              v              v                        │
 │  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐             │
 │  │ Implementor A │ │ Implementor B │ │  Verifier     │             │
 │  │ (Worker Agent)│ │ (Worker Agent)│ │  Agent        │             │
 │  └───────┬───────┘ └───────┬───────┘ └───────┬───────┘             │
 │          │                 │                 │                       │
 └──────────┼─────────────────┼─────────────────┼──────────────────────┘
            │                 │                 │
            v                 v                 v
 ┌──────────────────────────────────────────────────────────────────────┐
 │                          DATA PLANE                                  │
 │                                                                      │
 │  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐    │
 │  │  LLM Router  │   │  Tool Proxy  │   │  Context Window     │    │
 │  │  (Model Tier │   │  (MCP Server)│   │  Manager            │    │
 │  │   Selector)  │   │              │   │  (Hot/Warm/Cold)    │    │
 │  └──────┬───────┘   └──────┬───────┘   └──────────┬──────────┘    │
 │         │                  │                       │               │
 │         v                  v                       v               │
 │  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐    │
 │  │ Prompt Cache │   │  PII Filter  │   │  Semantic Cache     │    │
 │  │ (KV Tensor   │   │  Gateway     │   │  (Embedding-based)  │    │
 │  │  Reuse)      │   │              │   │                      │    │
 │  └──────┬───────┘   └──────┬───────┘   └──────────┬──────────┘    │
 │         │                  │                       │               │
 └─────────┼──────────────────┼───────────────────────┼───────────────┘
           │                  │                       │
           v                  v                       v
 ┌──────────────────────────────────────────────────────────────────────┐
 │                     PERSISTENCE LAYER                                │
 │                                                                      │
 │  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐    │
 │  │  Temporal     │   │  Event Store │   │  Knowledge Graph    │    │
 │  │  (Durable     │   │  (Append-only│   │  (Cross-repo        │    │
 │  │   Execution)  │   │   Event Log) │   │   Relationships)    │    │
 │  └──────────────┘   └──────────────┘   └──────────────────────┘    │
 │                                                                      │
 └──────────────────────────────────────────────────────────────────────┘
           │                  │                       │
           v                  v                       v
 ┌──────────────────────────────────────────────────────────────────────┐
 │                   TELEMETRY & OBSERVABILITY                          │
 │                                                                      │
 │  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐    │
 │  │  Structured  │   │  Distributed │   │  Alert Engine       │    │
 │  │  Audit Log   │   │  Tracing     │   │  (Step count, Token │    │
 │  │  (Immutable) │   │  (Corr. IDs) │   │   burn, Fail rate)  │    │
 │  └──────────────┘   └──────────────┘   └──────────────────────┘    │
 │                                                                      │
 └──────────────────────────────────────────────────────────────────────┘
```

### Request-Flow Narrative

**Step 1 -- Spec Ingestion**: A developer commits a Markdown spec to the Spec Registry (Git-backed). The spec defines objective, scope, requirements, edge cases, and acceptance criteria using EARS notation for testable statements.

**Step 2 -- Planning**: The Planner Agent reads the spec and generates a technical plan. This is the `specify -> plan` transition in GitHub Spec Kit. No phase advances until the human validates the current one. The plan may request multiple variations for comparison.

**Step 3 -- Task Decomposition**: The Task Decomposer breaks the plan into a DAG of independently testable chunks. Each task node carries its sub-spec, acceptance criteria, and dependency edges.

**Step 4 -- Coordination**: The Coordinator (Supervisor pattern) assigns tasks to Implementor workers. It manages concurrency, dependency ordering, and fan-out/fan-in parallelism. The Coordinator also enforces global retry budgets across the entire run.

**Step 5 -- Execution**: Each Implementor Agent executes its task via the Data Plane:
- The **LLM Router** selects the model tier (Haiku for simple, Sonnet for standard, Opus for complex) based on classification or cascade strategy.
- The **Prompt Cache** intercepts repeated prefixes, serving KV tensors at 90% cost reduction.
- The **Semantic Cache** bypasses the model entirely on near-match queries (31% of queries exhibit semantic similarity).
- The **Tool Proxy** (MCP Server) mediates all tool calls through PII filtering and per-invocation authorization.
- The **Context Window Manager** maintains hot (last 10 turns verbatim), warm (rolling summary), and cold (broad summary) tiers.

**Step 6 -- Verification**: The Verifier Agent checks each Implementor's output against the sub-spec. This is the most underused pattern in SDD -- assigning a separate agent to verify rather than trusting self-verification.

**Step 7 -- Persistence**: Every state transition is recorded in the Event Store (append-only log). Temporal manages durable execution: if any worker crashes, replay-based recovery resumes from the last checkpoint without repeating side effects.

**Step 8 -- Observability**: Structured audit logs capture agent ID, delegated permissions, model version, prompt content, response, latency, guardrail invocations, and policy violations. Distributed tracing with correlation IDs enables end-to-end request tracking. Alert thresholds fire on step count > 50, failure rate > 10%/hour, or token burn exceeding budget.

---

## 2. Core Mechanics & Algorithms

### 2.1 The SDD State Machine

SDD enforces a strict phase-gate state machine. No transition fires without human validation at the gate.

```
                 ┌──────────┐
                 │          │
        ┌───────>│  SPECIFY │
        │        │          │
        │        └────┬─────┘
        │             │ human validates
        │             v
        │        ┌──────────┐
        │        │          │
  reject│   ┌───>│   PLAN   │
        │   │    │          │
        │   │    └────┬─────┘
        │   │         │ human validates
        │   │         v
        │   │    ┌──────────┐
        │   │    │          │
        │   │    │  TASKS   │
        │   │    │          │
        │   │    └────┬─────┘
        │   │         │ human validates
        │   │         v
        │   │    ┌──────────┐       ┌──────────┐
        │   │    │          │       │          │
        │   └────│IMPLEMENT │──────>│ VALIDATE │
        │        │          │ fail  │          │
        │        └──────────┘       └────┬─────┘
        │                                │ pass
        │                                v
        │                          ┌──────────┐
        └──────────────────────────│ COMPLETE │
          spec-code drift detected │          │
                                   └──────────┘
```

**Key invariant**: The spec is never stale. If implementation changes diverge from the spec, the system transitions back to SPECIFY. This prevents the Assumption Propagation Problem -- where a bad assumption multiplies across files and tasks exponentially.

**Gate function**: At each gate, the validation check is:

```
valid(phase_output, spec) -> {PASS, REJECT(reason)}
```

REJECT returns control to the current or an earlier phase with the reason, creating a feedback loop that converges on correctness.

### 2.2 Orchestration Pattern Selection Algorithm

The choice of orchestration pattern is not arbitrary. It follows a decision tree based on task characteristics:

```
Is the task decomposable into independent subtasks?
├── YES: Are subtasks genuinely parallel (no shared state)?
│   ├── YES: Fan-Out/Fan-In
│   │   Complexity: O(max(T_i)) latency, O(sum(T_i)) cost
│   └── NO: DAG-Based
│       Complexity: O(critical_path) latency
├── NO: Does it require dynamic replanning?
│   ├── YES: Is there a natural supervisor role?
│   │   ├── YES: Supervisor-Worker with ReAct inner loop
│   │   └── NO: Handoff / Peer-to-Peer
│   └── NO: Plan-and-Execute (single agent)
│       Complexity: O(n) latency, cheapest model for execution
```

**Convergence property**: The Reflexion pattern (structured self-critique + retry) reduces repeated failure modes by 30-50%. The convergence mechanism: after each failure, the agent stores a short reflection (e.g., "tests failed because path assumptions were wrong"). Subsequent attempts condition on accumulated reflections, creating a monotonically improving success probability per retry up to a plateau.

### 2.3 The Reliability Multiplication Problem

For a pipeline of n sequential steps, each with independent reliability p:

```
P(end-to-end success) = p^n
```

At p = 0.85 and n = 10: `0.85^10 = 0.197` -- roughly 20% end-to-end success.

This is the fundamental argument for the Coordinator-Implementor-Verifier pattern: by adding a verification step after each implementation step, the effective per-step reliability increases from p to `1 - (1-p)(1-p_v)` where p_v is the verifier's detection rate.

With p = 0.85 and p_v = 0.90 (verifier catches 90% of errors):

```
p_effective = 1 - (0.15)(0.10) = 0.985
P(e2e, n=10) = 0.985^10 = 0.860
```

The system goes from 20% to 86% end-to-end reliability by adding verification agents.

### 2.4 Context Window Degradation Model

Factory AI finding: agents lose coherent access to original task objectives at approximately the **60% context utilization mark**. The degradation is not linear -- it follows a cliff pattern:

```
Coherence
  100% ┤████████████████████
       │                    ████
       │                        ████
       │                            ██
   50% ┤                              ██
       │                                █
       │                                 ██
       │                                   ██████
    0% ┤────────────────────────────────────────────
       0%    20%    40%    60%    80%    100%
                  Context Utilization
```

The three-tier mitigation (hot/warm/cold) applies hierarchical summarization:
- **Hot**: Last 10 turns, verbatim. Full fidelity.
- **Warm**: Turns 11-40, rolling summary. Key facts preserved, phrasing compressed.
- **Cold**: Everything earlier, broad summary. Only objectives and critical constraints.

Benchmarks show 26-54% reduction in peak token usage. But aggressive compression backfires: Factory AI found that compressing to the 99th percentile causes agents to re-fetch forgotten information, triggering additional tool calls. **The cost of forgetting exceeds the cost of remembering.**

### 2.5 Dynamic Knowledge Graph (GraphRAG)

Bidirectional graph traversal for context retrieval:
- **Forward traversal**: Follow call graph outward to understand downstream impact. Used when modifying a function to see what breaks.
- **Reverse traversal**: Move backward to find all callers/dependents. Used when understanding why a component exists.

This is structurally superior to flat RAG (cosine similarity over chunks) because it preserves **relationship semantics** -- inheritance, call graphs, module dependencies -- that cosine similarity destroys.

### 2.6 EARS Notation for Acceptance Criteria

Five sentence patterns that make requirements parseable by both humans and LLMs:

| Pattern | Template | Example |
|---------|----------|---------|
| **Ubiquitous** | The system shall [action] | The system shall log every tool invocation |
| **Event-driven** | When [event], the system shall [action] | When a tool call fails, the system shall retry with backoff |
| **State-driven** | While [state], the system shall [action] | While circuit is OPEN, the system shall reject requests |
| **Unwanted** | If [condition], the system shall [action] | If PII is detected, the system shall redact before forwarding |
| **Optional** | Where [feature], the system shall [action] | Where caching is enabled, the system shall check semantic cache |

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Base cost per request:**

```
C_request = (T_input * P_input + T_output * P_output) / 1,000,000
```

Where T = token count, P = price per 1M tokens.

**Claude pricing (mid-2026):**

| Model | P_input ($/1M) | P_output ($/1M) | Use Case |
|-------|----------------|-----------------|----------|
| Opus | $15.00 | $75.00 | Complex reasoning, spec generation |
| Sonnet | $3.00 | $15.00 | Standard implementation, planning |
| Haiku | $0.80 | $4.00 | Classification, routing, simple tasks |

**Cost per 1k agent runs (naive vs. optimized):**

Assume an average agent run consumes 2M tokens (1.5M input, 0.5M output) -- the midpoint of the 1-3.5M range Gartner reports.

```
Naive (all Opus):
  C_1k = 1000 * ((1.5M * $15 + 0.5M * $75) / 1M) = 1000 * $60 = $60,000

Three-tier routing (70% Haiku, 20% Sonnet, 10% Opus):
  C_haiku  = 0.70 * 1000 * ((1.5M * $0.80 + 0.5M * $4.00) / 1M) = $2,240
  C_sonnet = 0.20 * 1000 * ((1.5M * $3.00 + 0.5M * $15.0) / 1M) = $2,400
  C_opus   = 0.10 * 1000 * ((1.5M * $15.0 + 0.5M * $75.0) / 1M) = $6,000
  C_1k = $10,640   (82% reduction)

With prompt caching (90% reduction on cached input, assume 60% cache hit rate):
  Effective P_input = P_input * (1 - 0.60 * 0.90) = P_input * 0.46
  C_1k_cached ~ $5,500   (91% reduction from naive)

With semantic caching (31% of queries bypass model entirely):
  C_1k_final ~ $3,800   (94% reduction from naive)
```

**The 2026 cost paradox**: Token prices fell 80% YoY, but enterprise bills rose because agentic sessions consume 50-500x more tokens per task than chat. Enterprise LLM API spend passed $8.4B in 2025, on track to double.

### 3.2 Latency SLA Targets

| Metric | p50 | p95 | p99 | Mitigation |
|--------|-----|-----|-----|------------|
| Checkpoint write | 0.3ms | 0.8ms | ~1ms | In-memory event log with async flush |
| Hot cache read | 4us | -- | -- | L1 KV tensor cache, co-located |
| Policy decision | -- | -- | <=20ms | Pre-computed policy bundles (OPA) |
| LLM proxy overhead | -- | 8ms @ 1K RPS | -- | 2-4 LiteLLM instances, connection pooling |
| Real-time e2e | <250ms | <500ms | <1s | Streaming, prompt caching, model cascade |
| Batch analysis e2e | <2s | <5s | <10s | Async queue, parallel fan-out |

**Mitigation strategies by percentile:**
- **p50**: Prompt caching (85% latency reduction on long prompts), semantic caching (model bypass), model cascade starting with Haiku.
- **p95**: Connection pooling at the LLM proxy, pre-warmed inference endpoints, request coalescing for identical prompts.
- **p99**: Fallback model chains (if primary times out at 5s, switch to faster model), circuit breakers to prevent cascading timeouts, deadline propagation across agent hops.

**Regression gating**: No more than 10% increase in p95 per release. Gate CI pipelines on these thresholds.

### 3.3 Throughput & Capacity Planning

**Back-pressure design:**

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ Ingress  │────>│ Request  │────>│ LLM      │────>│ Response │
│ Gateway  │     │ Queue    │     │ Worker   │     │ Assembler│
│          │     │ (Kafka)  │     │ Pool     │     │          │
└──────────┘     └────┬─────┘     └──────────┘     └──────────┘
                      │
                      v
                 ┌──────────┐
                 │ Dead      │
                 │ Letter    │
                 │ Queue     │
                 └──────────┘
```

- **Rate limiting**: Token bucket per tenant, sliding window per model tier.
- **Admission control**: If queue depth exceeds threshold, return 429 with retry-after header.
- **Worker autoscaling**: Scale LLM worker pool on queue depth, not CPU (LLM calls are I/O-bound, not compute-bound on the client side).
- **Capacity formula**: `Required_workers = (RPS * avg_latency_seconds) / concurrency_per_worker`

### 3.4 NFR Targets

| NFR | Target | Rationale |
|-----|--------|-----------|
| Availability | 99.9% (8.7h downtime/year) | Matches upstream LLM provider SLAs |
| RPO | 0 (zero data loss) | Append-only event log with sync replication |
| RTO | < 5 min | Temporal replay-based recovery; stateless workers restart instantly |
| Compliance | SOC 2 Type II, HIPAA, GDPR, ISO 27001, EU AI Act | Immutable audit logs, PII redaction, sandbox isolation |
| Cost ceiling | $X/hour hard limit per tenant | Circuit breaker on token burn rate, kills runaway loops |

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution with Temporal

**Why Temporal dominates**: 3,000+ paying customers (Nvidia, Netflix). The key insight: **checkpointers are not durable execution.** Durable execution flips the model -- the runtime owns retry, resume, and dedup; the developer writes ordinary code.

**Architecture:**

```
┌─────────────────────────────────────────────────────────┐
│                    Temporal Cluster                       │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Event History                        │   │
│  │  (Append-only log of every workflow step)         │   │
│  │                                                    │   │
│  │  Event 1: WorkflowStarted(spec_id=ABC)            │   │
│  │  Event 2: ActivityScheduled(generate_plan)         │   │
│  │  Event 3: ActivityCompleted(plan={...})            │   │
│  │  Event 4: ActivityScheduled(send_email, idem=X7)   │   │
│  │  Event 5: ActivityCompleted(email_sent=true)       │   │
│  │  ... (workflow can run for seconds or months)      │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌────────────────┐  ┌────────────────┐                  │
│  │   Workflow      │  │   Activity     │                  │
│  │   (Deterministic│  │   (Side-effect │                  │
│  │    functions)   │  │    functions,  │                  │
│  │                 │  │    auto-retry) │                  │
│  └────────────────┘  └────────────────┘                  │
│                                                          │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          v              v              v
   ┌────────────┐ ┌────────────┐ ┌────────────┐
   │  Worker 1  │ │  Worker 2  │ │  Worker N  │
   │ (Stateless)│ │ (Stateless)│ │ (Stateless)│
   └────────────┘ └────────────┘ └────────────┘
```

**Replay-based recovery**: If Worker 2 crashes after sending an email (Event 4-5) but before the next step, a new worker picks up the workflow, replays the event history, sees the idempotency key for the email, skips the send, and continues from Event 6.

**Four guarantees**: (1) State survives crashes, pod restarts, region failovers; (2) Exactly-once execution of side-effects via idempotency keys; (3) Suspend and resume across arbitrary delays (human approvals, external callbacks); (4) Deterministic replay enabling time-travel debugging.

### 4.2 Failure Taxonomy

| Category | Examples | Detection | Response |
|----------|----------|-----------|----------|
| **Transient** | Rate limits (429), network timeout, provider 503 | HTTP status code, timeout signal | Retry with exponential backoff + jitter |
| **Permanent** | Invalid API key, malformed request, model not found | HTTP 4xx (not 429), validation error | Fail fast, route to dead-letter queue |
| **Poison pill** | Input that causes model to loop infinitely, hallucinated tool params that corrupt state | Step count > 50, context util > 80%, output validation failure | Circuit break, quarantine input, human escalation |
| **Silent** | Tool returns HTTP 200 with empty payload, model returns plausible but wrong output | Output schema validation, semantic assertion checks, verifier agent | Log anomaly, flag for review, do not propagate |
| **Cascading** | Agent A passes degraded context to Agent B, which corrupts further | Cross-agent assertion contracts, end-to-end integration checks | Halt pipeline, roll back to last known-good checkpoint |

**Poison-pill detection heuristic**: If the same input has caused > 2 failures across retries with the same error signature, classify as poison pill. Move to dead-letter queue. Do not retry.

**Idempotency key pattern**: Every side-effecting operation carries a deterministic key derived from `hash(workflow_id + step_number + operation_type)`. The persistence layer deduplicates on this key before executing.

### 4.3 Zero-Trust MCP Architecture

**The problem**: MCP's native specification mandates neither cryptographic identity verification nor access control bounds. MCP ships with no built-in access controls. Agents connect to live tools -- malicious prompts in external content can trigger real actions (indirect prompt injection).

**The architecture:**

```
┌──────────────┐         ┌──────────────────────┐
│  Agent       │         │  Authorization       │
│  (SPIFFE ID) │────────>│  Service             │
│              │         │  ┌────────────────┐  │
└──────┬───────┘         │  │ Policy Engine  │  │
       │                 │  │ (OPA/Cerbos)   │  │
       │                 │  └────────┬───────┘  │
       │                 │           │          │
       │                 │  ┌────────v───────┐  │
       │                 │  │ Token Issuer   │  │
       │                 │  │ (Short-lived,  │  │
       │                 │  │  scoped)       │  │
       │                 │  └────────┬───────┘  │
       │                 └───────────┼──────────┘
       │                             │
       │     signed token            │
       │<────────────────────────────┘
       │
       v
┌──────────────────────────────────────────────┐
│               MCP Server                      │
│                                               │
│  ┌────────────┐  ┌────────────┐              │
│  │ @require_  │  │  PII       │              │
│  │ permissions│  │  Redaction  │              │
│  │ (PBAC)     │  │  Gateway   │              │
│  └─────┬──────┘  └─────┬──────┘              │
│        │               │                      │
│  ┌─────v───────────────v──────┐              │
│  │        Tool Registry       │              │
│  │  ┌──────┐ ┌──────┐ ┌────┐ │              │
│  │  │Tool A│ │Tool B│ │... │ │              │
│  │  └──────┘ └──────┘ └────┘ │              │
│  └────────────────────────────┘              │
│                                               │
│  Row-level security: returns only authorized  │
│  records.                                     │
│  Column-level masking: redacts sensitive       │
│  fields even from permitted queries.          │
│                                               │
└──────────────────────────────────────────────┘
```

**Per-invocation authorization (Level 4 maturity)**: Every tool invocation carries its own signed token validated against current policy. The token represents "User X, via Agent Y" with scoped permissions. The MCP server does not hardcode permission logic -- it delegates to an external Policy Decision Point (OPA, Cerbos).

**PII redaction pipeline** (three approaches, composable):
1. **Gateway-layer**: Detect and redact PII before any model request executes (TrueFoundry, Gravitee).
2. **Dynamic routing**: If PII detected, reroute to on-premises model instead of cloud-hosted.
3. **Tool-level DLP**: Redaction on every MCP tool call for PII, PHI, PCI, and secrets (Strac).

**Immutable audit logs**: Every agent action logged with structured fields -- agent ID, version, delegated permissions, model version, prompt, response, latency, guardrail invocations, policy violations. Logs are append-only and exportable to SIEMs for multi-year retention. Beyond what was accessed: capture **why** and **what decisions resulted**.

### 4.4 Key Attack Vectors

| Vector | Mechanism | Mitigation |
|--------|-----------|------------|
| Indirect prompt injection | Malicious prompts embedded in tool outputs enter the agent's reasoning loop | Input sanitization, output validation, separate system/user prompt boundaries |
| Tool poisoning | Tool descriptions contain instruction-like text that hijacks agent behavior | Tool description signing, allowlisted tool registries, description hash verification |
| Over-privileged agents | Single token grants access to entire toolset | Per-invocation scoped tokens, principle of least privilege, PBAC |

---

## 5. Production Enterprise Code

### 5.1 Retry with Exponential Backoff and Jitter

```python
import asyncio
import random
import logging
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar, Callable, Awaitable

logger = logging.getLogger(__name__)
T = TypeVar("T")


class ErrorClass(Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    POISON_PILL = "poison_pill"


@dataclass
class RetryConfig:
    max_retries: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 60.0
    jitter_factor: float = 0.5  # [0, 1] -- fraction of delay added as jitter


def classify_error(exc: Exception) -> ErrorClass:
    """Classify errors to determine retry behavior."""
    status = getattr(exc, "status_code", getattr(exc, "status", None))
    if status == 429:
        return ErrorClass.TRANSIENT
    if isinstance(status, int) and 500 <= status < 600:
        return ErrorClass.TRANSIENT
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return ErrorClass.TRANSIENT
    return ErrorClass.PERMANENT


async def retry_with_backoff(
    fn: Callable[..., Awaitable[T]],
    *args,
    config: RetryConfig = RetryConfig(),
    correlation_id: str = "",
    **kwargs,
) -> T:
    """Execute fn with exponential backoff + jitter. Only retries transient errors."""
    last_exc: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            error_class = classify_error(exc)

            if error_class == ErrorClass.PERMANENT:
                logger.error(
                    "Permanent error, not retrying",
                    extra={"correlation_id": correlation_id, "error": str(exc)},
                )
                raise

            if attempt == config.max_retries:
                logger.error(
                    "Max retries exhausted",
                    extra={
                        "correlation_id": correlation_id,
                        "attempts": attempt + 1,
                        "error": str(exc),
                    },
                )
                raise

            delay = min(
                config.base_delay_s * (2 ** attempt),
                config.max_delay_s,
            )
            jitter = random.uniform(0, config.jitter_factor * delay)
            total_delay = delay + jitter

            logger.warning(
                "Transient error, retrying",
                extra={
                    "correlation_id": correlation_id,
                    "attempt": attempt + 1,
                    "delay_s": round(total_delay, 2),
                    "error": str(exc),
                },
            )
            await asyncio.sleep(total_delay)

    raise last_exc  # unreachable, but satisfies type checker
```

### 5.2 Circuit Breaker (Closed -> Open -> Half-Open)

```python
import time
import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeVar, Callable, Awaitable

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit is open and rejecting requests."""
    pass


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # failures before opening
    recovery_timeout_s: float = 30.0    # seconds before half-open probe
    half_open_max_calls: int = 1        # probes allowed in half-open state
    success_threshold: int = 2          # successes to close from half-open


@dataclass
class CircuitBreaker:
    name: str
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    async def call(self, fn: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        async with self._lock:
            self._check_state_transition()

            if self._state == CircuitState.OPEN:
                logger.warning(
                    "Circuit OPEN, rejecting call",
                    extra={"circuit": self.name},
                )
                raise CircuitOpenError(
                    f"Circuit '{self.name}' is open. "
                    f"Retry after {self.config.recovery_timeout_s}s."
                )

            if (
                self._state == CircuitState.HALF_OPEN
                and self._half_open_calls >= self.config.half_open_max_calls
            ):
                raise CircuitOpenError(
                    f"Circuit '{self.name}' is half-open, max probes in flight."
                )

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1

        try:
            result = await fn(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as exc:
            await self._on_failure()
            raise

    def _check_state_transition(self) -> None:
        if (
            self._state == CircuitState.OPEN
            and (time.monotonic() - self._last_failure_time)
            >= self.config.recovery_timeout_s
        ):
            logger.info(
                "Circuit transitioning OPEN -> HALF_OPEN",
                extra={"circuit": self.name},
            )
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0
            self._success_count = 0

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    logger.info(
                        "Circuit transitioning HALF_OPEN -> CLOSED",
                        extra={"circuit": self.name},
                    )
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            else:
                self._failure_count = 0

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                logger.warning(
                    "Circuit transitioning HALF_OPEN -> OPEN",
                    extra={"circuit": self.name},
                )
                self._state = CircuitState.OPEN
                self._success_count = 0
            elif self._failure_count >= self.config.failure_threshold:
                logger.warning(
                    "Circuit transitioning CLOSED -> OPEN",
                    extra={
                        "circuit": self.name,
                        "failures": self._failure_count,
                    },
                )
                self._state = CircuitState.OPEN
```

### 5.3 Fallback Model Chain with Structured Logging

```python
import uuid
import time
import asyncio
import logging
import json
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModelEndpoint:
    name: str            # e.g., "claude-opus-4", "claude-sonnet-4", "haiku"
    tier: str            # "primary", "secondary", "tertiary"
    timeout_s: float     # per-call timeout
    client: Any          # the actual API client (Anthropic, OpenAI, etc.)


class DeterministicFallback:
    """Last-resort fallback when all models are unavailable."""

    def respond(self, prompt: str) -> dict:
        return {
            "model": "deterministic-fallback",
            "content": (
                "All AI models are temporarily unavailable. "
                "Your request has been queued for processing. "
                "Reference ID: " + str(uuid.uuid4())[:8]
            ),
            "is_fallback": True,
        }


@dataclass
class CorrelatedLogger:
    """Structured logger that attaches correlation IDs to every log entry."""

    correlation_id: str

    def _log(self, level: int, event: str, **fields: Any) -> None:
        fields["correlation_id"] = self.correlation_id
        fields["event"] = event
        fields["timestamp_ms"] = int(time.time() * 1000)
        logger.log(level, json.dumps(fields, default=str))

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, **fields)


async def call_model(endpoint: ModelEndpoint, prompt: str) -> dict:
    """Call a model endpoint. Replace with actual SDK call in production."""
    response = await asyncio.wait_for(
        endpoint.client.messages.create(
            model=endpoint.name,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        ),
        timeout=endpoint.timeout_s,
    )
    return {
        "model": endpoint.name,
        "content": response.content[0].text,
        "is_fallback": False,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }


async def fallback_model_chain(
    prompt: str,
    endpoints: list[ModelEndpoint],
    circuit_breakers: dict[str, CircuitBreaker],
    correlation_id: str | None = None,
) -> dict:
    """
    Try models in priority order. If all fail, use deterministic fallback.

    Chain: primary (Opus) -> secondary (Sonnet) -> tertiary (Haiku)
           -> deterministic fallback (no model call)
    """
    cid = correlation_id or str(uuid.uuid4())
    log = CorrelatedLogger(correlation_id=cid)
    log.info("fallback_chain_start", prompt_len=len(prompt), chain_depth=len(endpoints))

    for i, endpoint in enumerate(endpoints):
        cb = circuit_breakers.get(endpoint.name)
        try:
            if cb:
                result = await cb.call(call_model, endpoint, prompt)
            else:
                result = await call_model(endpoint, prompt)

            log.info(
                "model_success",
                model=endpoint.name,
                tier=endpoint.tier,
                attempt=i + 1,
            )
            return result

        except CircuitOpenError:
            log.warning(
                "model_circuit_open",
                model=endpoint.name,
                tier=endpoint.tier,
                attempt=i + 1,
            )
            continue

        except asyncio.TimeoutError:
            log.warning(
                "model_timeout",
                model=endpoint.name,
                tier=endpoint.tier,
                timeout_s=endpoint.timeout_s,
                attempt=i + 1,
            )
            continue

        except Exception as exc:
            log.error(
                "model_error",
                model=endpoint.name,
                tier=endpoint.tier,
                error=str(exc),
                error_type=type(exc).__name__,
                attempt=i + 1,
            )
            continue

    # All models failed -- deterministic fallback
    log.error("all_models_failed", chain_depth=len(endpoints))
    fallback = DeterministicFallback()
    result = fallback.respond(prompt)
    log.warning("deterministic_fallback_used", ref_id=result["content"][-8:])
    return result
```

### 5.4 Graceful Degradation Under Partial Outage

```python
import asyncio
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ServiceHealth(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass
class ServiceStatus:
    name: str
    health: ServiceHealth
    last_check: float = field(default_factory=time.monotonic)
    consecutive_failures: int = 0


@dataclass
class GracefulDegradationController:
    """
    Manages system behavior when dependent services are partially unavailable.

    Capabilities are shed in priority order:
    1. Semantic cache miss -> skip cache, call model directly (higher cost, same quality)
    2. Knowledge graph unavailable -> fall back to flat RAG retrieval
    3. Primary model unavailable -> cascade to secondary model
    4. All models unavailable -> serve deterministic response, queue for retry
    5. Temporal unavailable -> switch to synchronous execution with local checkpointing
    """

    services: dict[str, ServiceStatus] = field(default_factory=dict)
    _health_check_interval_s: float = 10.0

    def register_service(self, name: str) -> None:
        self.services[name] = ServiceStatus(name=name, health=ServiceHealth.HEALTHY)

    def report_failure(self, service_name: str) -> None:
        svc = self.services.get(service_name)
        if not svc:
            return
        svc.consecutive_failures += 1
        svc.last_check = time.monotonic()
        if svc.consecutive_failures >= 3:
            svc.health = ServiceHealth.UNAVAILABLE
            logger.error("Service unavailable", extra={"service": service_name})
        elif svc.consecutive_failures >= 1:
            svc.health = ServiceHealth.DEGRADED
            logger.warning("Service degraded", extra={"service": service_name})

    def report_success(self, service_name: str) -> None:
        svc = self.services.get(service_name)
        if not svc:
            return
        svc.consecutive_failures = 0
        svc.health = ServiceHealth.HEALTHY
        svc.last_check = time.monotonic()

    def get_execution_plan(self) -> dict[str, Any]:
        """Return the current execution plan based on service health."""
        plan: dict[str, Any] = {
            "use_semantic_cache": True,
            "use_knowledge_graph": True,
            "model_strategy": "primary",
            "execution_mode": "durable",
            "degraded_services": [],
        }

        for name, svc in self.services.items():
            if svc.health == ServiceHealth.HEALTHY:
                continue

            plan["degraded_services"].append(name)

            if name == "semantic_cache":
                plan["use_semantic_cache"] = False
                logger.info("Shedding semantic cache -- direct model calls")

            elif name == "knowledge_graph":
                plan["use_knowledge_graph"] = False
                logger.info("Shedding knowledge graph -- falling back to flat RAG")

            elif name == "primary_model":
                plan["model_strategy"] = "cascade"
                logger.info("Primary model degraded -- using cascade strategy")

            elif name == "temporal":
                plan["execution_mode"] = "synchronous_with_local_checkpoint"
                logger.info(
                    "Temporal unavailable -- synchronous execution with local checkpointing"
                )

        return plan


# --- Usage ---
async def process_request_with_degradation(
    controller: GracefulDegradationController,
    prompt: str,
    correlation_id: str,
) -> dict:
    """Process a request, adapting behavior based on service health."""
    plan = controller.get_execution_plan()
    log = CorrelatedLogger(correlation_id=correlation_id)

    log.info(
        "execution_plan",
        degraded=plan["degraded_services"],
        model_strategy=plan["model_strategy"],
        execution_mode=plan["execution_mode"],
    )

    result: dict = {}

    # Step 1: Context retrieval (adapts based on service health)
    if plan["use_semantic_cache"]:
        try:
            # Attempt semantic cache lookup
            cached = await semantic_cache_lookup(prompt)
            if cached:
                log.info("semantic_cache_hit")
                return cached
        except Exception:
            controller.report_failure("semantic_cache")

    if plan["use_knowledge_graph"]:
        try:
            context = await knowledge_graph_retrieve(prompt)
        except Exception:
            controller.report_failure("knowledge_graph")
            context = await flat_rag_retrieve(prompt)  # fallback
    else:
        context = await flat_rag_retrieve(prompt)

    # Step 2: Model call (adapts based on model availability)
    # Uses the fallback_model_chain from section 5.3
    result = await fallback_model_chain(
        prompt=f"Context: {context}\n\nQuery: {prompt}",
        endpoints=get_endpoints_for_strategy(plan["model_strategy"]),
        circuit_breakers=get_circuit_breakers(),
        correlation_id=correlation_id,
    )

    return result


# Placeholder implementations for the degradation controller
async def semantic_cache_lookup(prompt: str) -> dict | None:
    """Look up prompt in semantic cache. Returns None on miss."""
    ...

async def knowledge_graph_retrieve(prompt: str) -> str:
    """Retrieve context via GraphRAG (bidirectional traversal)."""
    ...

async def flat_rag_retrieve(prompt: str) -> str:
    """Fallback: cosine similarity over embedded chunks."""
    ...

def get_endpoints_for_strategy(strategy: str) -> list[ModelEndpoint]:
    """Return ordered model endpoints based on strategy."""
    ...

def get_circuit_breakers() -> dict[str, CircuitBreaker]:
    """Return circuit breakers for each model endpoint."""
    ...
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Enterprise Code Generation Platform (Brownfield)

**Problem Statement**: A financial services company with 1,200 developers across 340 repositories wants to deploy an AI-assisted code generation platform. Their codebase is 15 years old, heavily regulated (SOC 2 Type II, PCI-DSS), and contains proprietary trading algorithms. Developers currently spend 35% of time on boilerplate and compliance scaffolding. The target is to reduce that to under 10% while ensuring zero leakage of proprietary logic to external models.

**Proposed Architecture:**

```
┌───────────────────────────────────────────────────────────────────┐
│                     DEVELOPER WORKSTATION                         │
│  ┌────────────┐                                                   │
│  │ IDE Plugin │──── Spec-first workflow ────┐                     │
│  └────────────┘                             │                     │
└─────────────────────────────────────────────┼─────────────────────┘
                                              v
┌───────────────────────────────────────────────────────────────────┐
│                      CONTROL PLANE (VPC)                          │
│                                                                   │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐  │
│  │ Spec Registry│   │ Knowledge    │   │ Policy Engine        │  │
│  │ (Git-backed) │   │ Graph Builder│   │ (OPA + PCI rules)    │  │
│  │              │   │ (340 repos)  │   │                      │  │
│  └──────────────┘   └──────────────┘   └──────────────────────┘  │
│                                                                   │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐  │
│  │ Coordinator  │   │ PII/Secret   │   │ Audit Logger         │  │
│  │ Agent        │   │ Redaction GW │   │ (Immutable, SIEM)    │  │
│  └──────┬───────┘   └──────────────┘   └──────────────────────┘  │
│         │                                                         │
│  ┌──────v────────────────────────────────────────────────────┐   │
│  │                    MODEL ROUTING LAYER                     │   │
│  │  ┌────────────┐  ┌────────────────┐  ┌─────────────────┐ │   │
│  │  │ On-prem    │  │ Self-hosted    │  │ Claude API      │ │   │
│  │  │ Fine-tuned │  │ Open-weights   │  │ (via PII gate)  │ │   │
│  │  │ (Proprietary│ │ (Llama 4 70B) │  │                 │ │   │
│  │  │  patterns) │  │               │  │                 │ │   │
│  │  └────────────┘  └────────────────┘  └─────────────────┘ │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ Temporal Cluster (Durable Execution)                       │   │
│  │ Workflow: Specify -> Plan -> Tasks -> Implement -> Verify  │   │
│  └────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────┘
```

**Trade-off Evaluation Matrix:**

```
┌──────────────────┬──────────────────┬──────────────────┬──────────────────┐
│ Dimension        │ A: All On-Prem   │ B: Hybrid (above)│ C: All Cloud API │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Cost (monthly)   │ $180K (GPU infra │ $85K (mixed)     │ $45K (API only)  │
│                  │ + ops team)      │                  │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Latency (p95)    │ 2s (local net)   │ 3s (routing      │ 4s (external     │
│                  │                  │ overhead)        │ network)         │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Ops Complexity   │ HIGH (GPU fleet  │ MEDIUM (hybrid   │ LOW (managed)    │
│                  │ mgmt, model      │ routing, 2       │                  │
│                  │ updates)         │ environments)    │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Security         │ HIGHEST (air-    │ HIGH (PII gate   │ MEDIUM (data     │
│                  │ gapped)          │ + on-prem for    │ leaves VPC)      │
│                  │                  │ sensitive code)  │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Quality Ceiling  │ MEDIUM (no       │ HIGH (frontier   │ HIGHEST (full    │
│                  │ frontier models) │ for non-secret   │ frontier access) │
│                  │                  │ tasks)           │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Scalability      │ LIMITED (GPU     │ HIGH (cloud      │ HIGHEST (fully   │
│                  │ procurement      │ burst for non-   │ elastic)         │
│                  │ cycles)          │ sensitive)       │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ PCI-DSS          │ PASS             │ PASS (with PII   │ FAIL (without    │
│ Compliance       │                  │ gate)            │ significant      │
│                  │                  │                  │ controls)        │
└──────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

**Decision Rationale**: Option B (Hybrid) wins. Option C fails PCI-DSS without extreme redaction that degrades quality. Option A is operationally expensive and caps quality at open-weights model performance. The hybrid approach routes proprietary trading logic to on-prem fine-tuned models, general boilerplate to open-weights, and non-sensitive tasks through PII-gated cloud API. The Knowledge Graph (340 repos) provides cross-repo awareness that prevents the agent from generating code that conflicts with existing patterns -- critical in a brownfield codebase. The parameterized spec pattern (proven at Microsoft/GitHub) reduces onboarding of new asset types from 2-3 weeks to days.

---

### Scenario 2: Multi-Agent Customer Support Automation (Greenfield)

**Problem Statement**: A B2B SaaS company (500K enterprise users, 120 support agents, 15K tickets/day) wants to automate Tier-1 and Tier-2 support resolution. Current metrics: 4.2-hour average resolution time, 68% first-contact resolution rate. Target: <15-minute resolution for 60% of tickets, 85% first-contact resolution, with zero tolerance for incorrect billing actions or data exposure across tenant boundaries.

**Proposed Architecture:**

```
┌───────────────────────────────────────────────────────────────────┐
│                    INGRESS LAYER                                  │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                  │
│  │ Email      │  │ Chat       │  │ API        │                  │
│  │ Webhook    │  │ Widget     │  │ Tickets    │                  │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘                  │
│        └────────────────┼────────────────┘                        │
│                         v                                         │
│               ┌──────────────────┐                                │
│               │ Intake Queue     │                                │
│               │ (Kafka, tenant-  │                                │
│               │  partitioned)    │                                │
│               └────────┬─────────┘                                │
└────────────────────────┼──────────────────────────────────────────┘
                         v
┌───────────────────────────────────────────────────────────────────┐
│                  AGENT ORCHESTRATION LAYER                        │
│                                                                   │
│  ┌──────────────────┐    ┌─────────────────────────────────────┐  │
│  │ Triage Agent     │    │ Tenant Isolation Boundary           │  │
│  │ (Haiku -- fast,  │    │ (Row-level security, tenant_id     │  │
│  │  classification) │    │  propagated on every query)         │  │
│  └────────┬─────────┘    └─────────────────────────────────────┘  │
│           │                                                       │
│    ┌──────┼──────────────────────┐                                │
│    v      v                     v                                 │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐            │
│  │ Resolver │  │ Billing      │  │ Escalation       │            │
│  │ Agent    │  │ Agent        │  │ Agent            │            │
│  │ (Sonnet) │  │ (Opus, HITL) │  │ (Handoff to      │            │
│  │          │  │              │  │  human agent)    │            │
│  └────┬─────┘  └──────┬───────┘  └──────────────────┘            │
│       │               │                                           │
│       │    ┌──────────────────┐                                   │
│       └───>│ Verifier Agent   │                                   │
│            │ (Checks response │                                   │
│            │  against KB +    │                                   │
│            │  tenant policy)  │                                   │
│            └──────────────────┘                                   │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ Temporal (Durable Execution)                               │   │
│  │ - Each ticket = one workflow                               │   │
│  │ - Billing mutations require HITL approval activity         │   │
│  │ - Workflow survives agent pod restarts                     │   │
│  └────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────┘
                         │
                         v
┌───────────────────────────────────────────────────────────────────┐
│                    TOOL LAYER (MCP)                               │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐ │
│  │ Knowledge  │  │ Billing    │  │ Account    │  │ Jira       │ │
│  │ Base       │  │ System     │  │ Service    │  │ (Escalate) │ │
│  │ (read-only)│  │ (write,    │  │ (read,     │  │            │ │
│  │            │  │  HITL gate)│  │  scoped)   │  │            │ │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘ │
│  Per-invocation tokens. Billing Agent cannot read KB.            │
│  Resolver Agent cannot write to Billing. Principle of least      │
│  privilege enforced via PBAC.                                    │
└───────────────────────────────────────────────────────────────────┘
```

**Trade-off Evaluation Matrix:**

```
┌──────────────────┬──────────────────┬──────────────────┬──────────────────┐
│ Dimension        │ A: Single Agent  │ B: Multi-Agent   │ C: Multi-Agent   │
│                  │ (ReAct loop)     │ Supervisor       │ + Human-on-loop  │
│                  │                  │ (above)          │ for ALL actions   │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Cost/ticket      │ $0.08 (single    │ $0.18 (multi-    │ $0.18 + $2.50    │
│                  │ Sonnet call)     │ model routing)   │ (human labor     │
│                  │                  │                  │ per review)      │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Resolution time  │ 2-8 min          │ 3-12 min         │ 15-45 min        │
│ (automated)      │                  │                  │ (human wait)     │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Ops Complexity   │ LOW              │ MEDIUM (agent    │ HIGH (human      │
│                  │                  │ coordination,    │ queue mgmt +     │
│                  │                  │ Temporal)        │ agent coord)     │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Security Risk    │ HIGH (one agent  │ LOW (least       │ LOWEST (human    │
│                  │ has all tool     │ privilege per     │ approves all     │
│                  │ access)          │ agent, HITL for  │ actions)         │
│                  │                  │ billing)         │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Quality Ceiling  │ MEDIUM (context  │ HIGH (specialized│ HIGHEST (human   │
│                  │ window limit on  │ agents, verifier │ judgment)        │
│                  │ complex tickets) │ checks)          │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Scalability      │ HIGH (simple)    │ HIGH (parallel   │ LIMITED (human   │
│                  │                  │ fan-out)         │ bottleneck)      │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Tenant Isolation │ WEAK (single     │ STRONG (row-     │ STRONG           │
│                  │ context holds    │ level security,  │                  │
│                  │ cross-tenant     │ tenant_id        │                  │
│                  │ risk)            │ propagation)     │                  │
└──────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

**Decision Rationale**: Option B (Multi-Agent Supervisor) wins. Option A has unacceptable security risk -- a single agent with billing write access and broad tool access is an over-privileged agent, a top OWASP agentic attack vector. Option C eliminates the speed and scalability benefits that justify the project. Option B provides the right trade-off: the Triage Agent (Haiku, fast) classifies and routes; the Resolver Agent (Sonnet) handles knowledge-based resolution; the Billing Agent (Opus, highest reasoning) handles financial mutations with mandatory human-in-the-loop approval via a Temporal activity that suspends until a human clicks approve. The Verifier Agent checks every response against the knowledge base and tenant policy before delivery. Each agent has tool access scoped to its function only (PBAC), and tenant_id is propagated on every database query for row-level isolation. Kafka partitioning by tenant_id prevents cross-tenant data leakage at the queue level. At 15K tickets/day, the cost is approximately $2,700/day ($0.18 * 15,000), compared to the current 120-agent human team cost -- an order of magnitude cheaper for Tier-1/Tier-2 resolution while reserving human agents for complex Tier-3 cases.

---

## Interview Quick-Reference

**"Why SDD over vibe coding?"** SDD prevents the Assumption Propagation Problem. A bad assumption in vibe coding multiplies across files exponentially. SDD's phase-gate state machine catches drift at each boundary. The spec is the single source of truth -- not the code.

**"When is multi-agent justified?"** Only when you hit a clear capability ceiling with single-agent. The reliability multiplication problem means a 10-step multi-agent pipeline at 85% per-step reliability succeeds only 20% of the time without verification agents. Add them, and you recover to 86%. Start single-agent; graduate when you must.

**"How do you control costs?"** Five layers: (1) Three-tier model routing (82% reduction), (2) Prompt caching at provider level (90% on cached input), (3) Semantic caching at application level (31% bypass), (4) Context window management (26-54% token reduction), (5) Hard $/hour circuit breakers per tenant to prevent runaway loops.

**"How do you make agents durable?"** Temporal. Not checkpointers -- durable execution. The runtime owns retry, resume, and dedup. The developer writes ordinary code. Idempotency keys prevent duplicate side-effects on replay. Append-only event log enables time-travel debugging.

**"What is Zero-Trust MCP?"** MCP ships with no built-in access controls. Zero-Trust MCP adds: per-invocation signed tokens, external policy engines (OPA/Cerbos), SPIFFE identities for agents, PII redaction at the gateway, row-level and column-level security on tool responses.

---

## Sources

[1] System Design Newsletter: SDD for AI Agents (Blitzy case study) | [2] Microsoft: SDD for AI-Native Engineering | [3] GitHub Blog: Spec Kit OSS | [4] Augment Code: SDD Explained | [6] Towards AI: 7 Agent Design Patterns 2026 | [7] ArXiv: SDD From Code to Contract | [9] Agent Architecture Patterns Taxonomy 2026 | [12] Keeper Security: MCP Zero-Trust | [14] Cerbos: MCP Identity and Policy | [16] ArXiv: ZT-MCP | [19] NeuralTrust: Token Optimization | [20-21] Prompt Caching Engineering Guides | [22] Temporal: Durable Execution for AI | [25-26] Agent Failure Modes | [27] Anthropic: Netflix AI Agents | [28] Anthropic: 2026 State of AI Agents
