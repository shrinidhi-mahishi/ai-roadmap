# Tool Use - APIs, Function Calling, Browser, Code Execution

Tool use turns an LLM from a pure text generator into a control-plane component that can invoke APIs, browse web applications, execute code, and orchestrate multi-step workflows. In production, the hard part is not enabling a tool surface; it is controlling token overhead, side effects, retries, approvals, persistence, and degraded behavior when one part of the loop fails.

## 1. System Topology & Data Flow

The enterprise shape is a split system: the model and policy engine live in the control plane, while execution lives in provider-hosted runtimes, internal API adapters, browser workers, and code sandboxes.

```text
┌──────────────────────────────── Control Plane ────────────────────────────────┐
│ User / App                                                                    │
│   │                                                                           │
│   ▼                                                                           │
│ API Gateway -> AuthN/Z -> Orchestrator -> Policy Engine -> LLM Runtime        │
│                                 │                 │                            │
│                                 │                 ├─ decides tool_choice       │
│                                 │                 ├─ emits function args       │
│                                 │                 └─ requests approval         │
└─────────────────────────────────┼─────────────────┼────────────────────────────┘
                                  │                 │
                                  │                 │ telemetry + traces
                                  │                 ▼
┌────────────────────────────── Data Plane / Tool Proxies ──────────────────────┐
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐ │
│  │ API Proxy      │  │ MCP Gateway    │  │ Browser Worker │  │ Code Runner  │ │
│  │ CRM/ERP/Jira   │  │ OAuth + scopes │  │ CDP/Playwright │  │ sandboxed VM │ │
│  └──────┬─────────┘  └──────┬─────────┘  └──────┬─────────┘  └──────┬───────┘ │
│         │                   │                   │                   │         │
└─────────┼───────────────────┼───────────────────┼───────────────────┼─────────┘
          │                   │                   │                   │
          ▼                   ▼                   ▼                   ▼
┌──────────────────────── Persistence / Eventing / Observability ───────────────┐
│ Workflow state store | Checkpoints | Kafka topics | DLQ | Object store        │
│ Audit log            | Metrics      | Distributed traces | SIEM / log sink     │
└────────────────────────────────────────────────────────────────────────────────┘
```

### Request-flow narrative

1. A caller submits a task plus identity, tenant, and correlation metadata.
2. The API gateway authenticates the request and forwards it to an orchestrator that attaches policy, tool catalog, and budget limits.
3. The LLM runtime decides whether the task can complete in-text or requires a tool. For function calling, the model emits JSON arguments against a declared schema. For browser or computer use, it emits one or more actions that the application executes. For server-side code execution, the provider may execute inside its own sandbox.
4. Tool proxies enforce least privilege before any side effect: tenant scoping, RBAC, destination allowlists, rate budgets, and idempotency keys.
5. The tool result returns to the orchestrator, which either:
   - completes the request,
   - loops for another tool step,
   - pauses for human approval,
   - or degrades to a fallback path.
6. Each super-step writes checkpoints, structured logs, metrics, and immutable audit events so the workflow can resume after retry, worker restart, or approval delay.

### Topology implications by tool type

- `Function calling` is the cleanest API-first topology: low data-plane ambiguity, strong schema governance, and the easiest audit trail.
- `Browser use` is best when the target is web-native but lacks an API. The browser worker becomes a stateful executor that must manage screenshots, DOM refs, and prompt-injection risk.
- `Computer use` expands reach to legacy desktop apps and cross-app flows, but the execution surface is larger and harder to secure deterministically.
- `Code execution` moves bounded computation into a sandbox. It reduces client-side ops burden, but introduces container/session economics and runtime isolation trade-offs.

## 2. Core Mechanics & Algorithms

### Tool-selection mechanics

The model is solving a constrained action-selection problem:

- Inputs: user goal, current workflow state, declared tool schemas, policy constraints, and remaining token/time budget.
- Output: either a natural-language answer or a structured action.
- Constraint: emitted arguments must satisfy the schema, authorization policy, and side-effect rules.

At the orchestration layer, tool use behaves like a finite-state machine:

```text
RECEIVED
  -> CLASSIFY
  -> PLAN
  -> SELECT_TOOL
  -> VALIDATE_ARGS
  -> AUTHORIZE
  -> EXECUTE
  -> OBSERVE_RESULT
  -> {COMPLETE | RETRY | FALLBACK | APPROVAL_WAIT | FAIL}
```

### Key algorithms and state transitions

#### A. Function-calling loop

1. Inject tool definitions into prompt context.
2. Model chooses `none`, a specific tool, or automatic selection.
3. Validate JSON against schema.
4. Execute the tool through a bounded adapter.
5. Return `tool_result` or equivalent observation to the model.
6. Repeat until the model terminates.

Primary complexity driver: prompt growth is `O(sum(schema_tokens))`, so large tool catalogs increase both cost and context pressure linearly. This is why deferred tool loading or `tool_search` materially matters.

#### B. Browser/computer-use loop

1. Model emits action plan for the next visible state.
2. Executor applies actions serially against browser/desktop context.
3. System captures a fresh observation, usually screenshot plus accessibility/DOM state.
4. Model replans from the new state.

The loop is partially observable and stateful. Convergence depends on bounded step counts, timeout budgets, and anti-loop heuristics such as:

- max repeated action count,
- max unchanged screenshot count,
- max approval stalls,
- per-step semantic progress scoring.

#### C. Code-execution loop

1. Model emits code or requests execution.
2. Sandbox runs in a constrained container or server-side environment.
3. Output files, stdout/stderr, and exceptions become the next observation.
4. The model revises code or concludes.

When a container can be reused, setup cost amortizes across steps. When it cannot, each execution behaves like a cold-start job.

### Invariants

- Every side-effecting tool call must carry a stable `correlation_id`, `tenant_id`, and `idempotency_key`.
- The orchestrator, not the model, owns retry policy, timeout ceilings, and authorization.
- A workflow step is not considered committed until both the result and audit metadata are durably persisted.
- A browser/computer loop must never trust page text or screenshots as policy instructions; they are untrusted observations.

### Convergence and correctness

- `Function calling` converges best when schemas are narrow and strict mode is enabled.
- `Browser use` converges best when the environment exposes structured page state rather than only pixels.
- `Code execution` converges best when the task is bounded, deterministic, and mostly local to provided files/data.

> ⚠️ Gap: Public vendor docs describe tool loops and schema behavior well, but they do not publish enough internal detail to rigorously prove convergence properties for long-running browser/computer workflows. In practice, teams must define their own termination invariants and loop-budget alarms.

## 3. Token Economics & NFR Analysis

### Cost formulas (`$ per 1k runs`)

Assumptions used below:

- `Iu` = uncached input tokens per run
- `Ic` = cached input tokens per run
- `Iw` = cache writes per run
- `O` = output tokens per run
- `Pi` = uncached input price per token
- `Po` = output price per token
- Hosted-tool charges are additive
- Prices cited are from the researched vendor docs on 2026-08-21

#### OpenAI function calling

`$ per 1k runs = 1000 * ((Iu * Pi) + (0.1 * Ic * Pi) + (1.25 * Iw * Pi) + (O * Po)) + hosted_tool_fees_per_1k`

Interpretation:

- Tool definitions count as input tokens.
- Prompt-cache hits reduce input cost to `0.1x`.
- Cache writes cost `1.25x`.
- Hosted tool fees stack on top, such as web search at `$10 / 1k calls` or file search at `$2.50 / 1k calls`.

#### OpenAI code execution floor

If every short-lived task creates a fresh eligible `1 GB` container:

`$ per 1k runs = 1000 * (5 minutes * ($0.03 / 20 minutes)) = $7.50 / 1k runs`

That is a floor for fresh short runs, not a ceiling. Reusing the same active container across many steps lowers effective cost per business task.

#### Anthropic browser-toolset overhead

Using Sonnet 5 browser toolset overhead only, before prompt, screenshots, and output:

`$ per 1k runs = (6,670 tokens/request * 1000 requests * $2 / 1,000,000 tokens) = $13.34 / 1k runs`

If all four optional browser members are enabled, add roughly:

`$ per 1k runs = (880 * 1000 * $2 / 1,000,000) = $1.76 / 1k runs`

#### Anthropic computer-toolset overhead

Using Sonnet 5 computer toolset overhead only:

`$ per 1k runs = (4,590 tokens/request * 1000 requests * $2 / 1,000,000 tokens) = $9.18 / 1k runs`

### Cost design guidance

- Function calling is usually the cheapest high-reliability path when the target system already has stable APIs.
- Browser/computer tools carry hidden cost multipliers: screenshot/image payloads, larger tool definitions, more loop turns, and more frequent cache invalidation.
- Code execution cost is governed by session reuse and memory tier more than by single-turn prompt size.
- The highest-leverage optimization is to keep large, stable tool definitions at the front of the prompt so cache hit rates stay high.

### Latency targets

These are engineering targets for an enterprise platform, not vendor-published benchmarks:

- `Function/API path`: `p50 <= 1.5s`, `p95 <= 4s`, `p99 <= 8s`
- `Browser path`: `p50 <= 8s`, `p95 <= 25s`, `p99 <= 60s`
- `Code execution path`: `p50 <= 5s`, `p95 <= 20s`, `p99 <= 90s`

Mitigations by percentile tier:

- `p50`: prompt caching, smaller tool descriptions, hot connection pools, warm browser workers, reused code containers.
- `p95`: bounded fan-out, pre-authorized tool scopes, streaming partial results, queue-based load smoothing.
- `p99`: admission control, workflow checkpoint resume, degraded fallback response, automatic breaker-open under dependent outage.

> ⚠️ Gap: The researched public docs contain pricing, cache semantics, and rate limits, but not comprehensive p50/p95/p99 measurements for end-to-end tool loops. The targets above are therefore architecture SLAs to engineer toward, not externally verified benchmarks.

### Throughput and back-pressure

Capacity planning must be done in both requests/sec and tokens/sec because tool loops are token-amplifying workflows.

- Admission control should evaluate:
  - concurrent workflow count,
  - total input/output token budget,
  - browser worker pool saturation,
  - code container pool saturation,
  - downstream API rate limits.
- Back-pressure should escalate in this order:
  1. queue new low-priority work,
  2. shed optional enrichments,
  3. disable expensive tools for best-effort traffic,
  4. return deterministic degraded responses for non-critical tenants.
- For browser fleets, define separate concurrency pools because a single slow screenshot-heavy workload can consume far more wall-clock time than an API-only workflow.

### Availability, RPO, RTO, compliance

Recommended enterprise targets:

- `Availability`: `99.9%` for API/function orchestration, `99.5%` for browser-heavy automation, because visual loops depend on more stateful executors.
- `RPO`: `<= 5 minutes` for workflow state and audit events; `0` for approved side-effect commits by using idempotent write-ahead events.
- `RTO`: `<= 30 minutes` for control-plane failover; `<= 60 minutes` for full browser worker fleet restoration.

Compliance discussion:

- `SOC 2` and `ISO 27001` need durable audit logging, least privilege, secret separation, and change control around tool registries.
- `GDPR` and `CCPA` require data minimization, redaction pipelines, retention policies, and replay-safe deletion semantics.
- `HIPAA` requires stricter PII/PHI segregation, sandbox isolation review, and explicit BAAs where provider-hosted tools process regulated data.

## 4. Distributed Resilience & Security

### Durable execution patterns

Tool loops are long-lived workflows, so durable orchestration matters more than raw single-request latency.

- `Temporal pattern`: model/tool steps are workflow activities; approval waits are timers/signals; retries are centrally defined; replay reconstructs exact control flow.
- `Kafka pattern`: each tool step emits an event, reducers persist workflow state, and poison messages route to DLQ after retry budgets expire.
- `LangGraph-style checkpointing`: persist state snapshots and per-step writes so successful sibling branches are not recomputed after a partial failure.

Recommended durable state per step:

- workflow id
- correlation id
- tenant id
- tool name and version
- tool args hash
- idempotency key
- authorization decision
- redaction status
- result hash
- retry count
- human approval state

### Failure taxonomy

#### Transient failures

- `429` rate limits
- browser navigation timeouts
- sandbox cold starts
- brief network partitions
- provider `5xx`

Handling: exponential backoff with jitter, breaker-aware retry ceilings, and preservation of workflow state before retry.

#### Permanent failures

- schema mismatch
- missing OAuth scope
- invalid tool arguments
- unsupported page state
- deterministic sandbox import/runtime errors

Handling: stop retrying, record classified error, route to fallback chain or human review.

#### Poison-pill failures

- repeated prompt injection on a page
- the same malformed `tool_result` triggering identical re-plans
- desktop/browser loops revisiting the same screen without progress

Handling: detect repeated signatures, mark the workflow as poisoned, quarantine to DLQ, and require operator or reviewer intervention.

### Retries, idempotency, and circuit breaking

- Retries belong in the orchestration layer, not inside prompts.
- Every side effect should be keyed by an idempotency token derived from workflow id plus semantic action.
- Circuit breaker states should be explicit:
  - `closed`: normal execution
  - `open`: short-circuit calls and immediately invoke fallback
  - `half-open`: allow a limited probe count before closing again
- Browser and computer-use flows need stricter retry budgets because repeated actions can create duplicate side effects in external systems.

### Fallback chains

A safe enterprise fallback order is:

1. primary tool path
2. secondary model or secondary region
3. narrower deterministic API path
4. read-only answer with stale-but-safe data
5. human escalation

The key design principle is semantic monotonicity: each fallback should preserve safety even if capability is reduced.

### Zero-Trust MCP and enterprise security

For MCP-style tool access:

- Use OAuth 2.1 with PKCE for HTTP transports.
- Bind tokens to the target resource server.
- Advertise scopes narrowly and fail closed on missing scope.
- Prefer environment-sourced credentials for STDIO transports, but keep them isolated per worker identity.
- Treat every MCP server as a distinct trust zone, not a generic "tool network."

Additional security controls:

- Tool-level RBAC: separate read-only tools, mutating tools, and high-impact tools behind different approval classes.
- PII filtering pipeline: `detect -> redact -> classify -> persist audit event -> dispatch`.
- Auditability: immutable append-only logs, decision chain hashes, and trace redaction controls for sensitive payloads.
- Browser/computer isolation: dedicated VM or hardened browser profile, no ambient credentials, domain allowlists, no extension sprawl.

> ⚠️ Gap: Public docs reviewed here describe approvals, tracing, OAuth-based MCP auth, and sandbox isolation, but they do not provide a canonical enterprise audit schema or a standard vendor-neutral PII-redaction event model. A strict audit would expect the platform team to define those artifacts explicitly.

## 5. Production Enterprise Code

The snippet below shows a production-style orchestration core for tool execution. It is self-contained, runnable with Python 3.11+, and demonstrates exponential backoff with jitter, circuit breakers, fallback chains, structured logging with correlation IDs, and graceful degradation.

```python
from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol


class TransientToolError(Exception):
    pass


class PermanentToolError(Exception):
    pass


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class ToolRequest:
    tenant_id: str
    workflow_id: str
    correlation_id: str
    idempotency_key: str
    action: str
    payload: Dict[str, Any]


@dataclass(frozen=True)
class ToolResponse:
    source: str
    ok: bool
    degraded: bool
    data: Dict[str, Any]
    error: Optional[str] = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": int(time.time() * 1000),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for field in ("correlation_id", "workflow_id", "tenant_id", "tool", "event"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"))


logger = logging.getLogger("tool_orchestrator")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class ToolAdapter(Protocol):
    name: str

    def execute(self, request: ToolRequest) -> ToolResponse:
        ...


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 10.0,
        half_open_probe_limit: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.half_open_probe_limit = half_open_probe_limit
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.opened_at = 0.0
        self.half_open_probes = 0

    def before_call(self) -> None:
        now = time.monotonic()
        if self.state == CircuitState.OPEN:
            if now - self.opened_at >= self.recovery_timeout_seconds:
                self.state = CircuitState.HALF_OPEN
                self.half_open_probes = 0
            else:
                raise TransientToolError("circuit breaker is open")

        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_probes >= self.half_open_probe_limit:
                raise TransientToolError("half-open probe limit exhausted")
            self.half_open_probes += 1

    def on_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_probes = 0

    def on_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()


def log_event(
    message: str,
    *,
    request: ToolRequest,
    tool: str,
    event: str,
    level: int = logging.INFO,
) -> None:
    logger.log(
        level,
        message,
        extra={
            "correlation_id": request.correlation_id,
            "workflow_id": request.workflow_id,
            "tenant_id": request.tenant_id,
            "tool": tool,
            "event": event,
        },
    )


def execute_with_retry(
    adapter: ToolAdapter,
    request: ToolRequest,
    breaker: CircuitBreaker,
    *,
    max_attempts: int = 4,
    base_delay_seconds: float = 0.25,
    max_delay_seconds: float = 2.0,
) -> ToolResponse:
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            breaker.before_call()
            log_event(
                f"executing tool attempt={attempt}",
                request=request,
                tool=adapter.name,
                event="tool_attempt",
            )
            response = adapter.execute(request)
            breaker.on_success()
            log_event(
                "tool execution succeeded",
                request=request,
                tool=adapter.name,
                event="tool_success",
            )
            return response
        except PermanentToolError as exc:
            breaker.on_failure()
            log_event(
                f"permanent failure: {exc}",
                request=request,
                tool=adapter.name,
                event="tool_permanent_failure",
                level=logging.ERROR,
            )
            raise
        except TransientToolError as exc:
            breaker.on_failure()
            last_error = exc
            log_event(
                f"transient failure: {exc}",
                request=request,
                tool=adapter.name,
                event="tool_transient_failure",
                level=logging.WARNING,
            )
            if attempt == max_attempts:
                break

            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            jitter = random.uniform(0.0, delay * 0.25)
            time.sleep(delay + jitter)

    raise TransientToolError(f"retry budget exhausted: {last_error}")


def deterministic_fallback(request: ToolRequest, reason: str) -> ToolResponse:
    return ToolResponse(
        source="deterministic_fallback",
        ok=True,
        degraded=True,
        data={
            "action": request.action,
            "status": "degraded",
            "reason": reason,
            "message": "Returned a safe read-only response because all tool paths were unavailable.",
        },
    )


def invoke_with_fallbacks(
    request: ToolRequest,
    adapters: List[ToolAdapter],
    breakers: Dict[str, CircuitBreaker],
) -> ToolResponse:
    errors: List[str] = []

    for adapter in adapters:
        breaker = breakers[adapter.name]
        try:
            return execute_with_retry(adapter, request, breaker)
        except PermanentToolError as exc:
            errors.append(f"{adapter.name}: permanent: {exc}")
        except TransientToolError as exc:
            errors.append(f"{adapter.name}: transient: {exc}")

    log_event(
        "all primary tool paths failed; using graceful degradation",
        request=request,
        tool="fallback_chain",
        event="tool_degraded",
        level=logging.ERROR,
    )
    return deterministic_fallback(request, "; ".join(errors))


class PrimaryApiTool:
    name = "primary_api"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request: ToolRequest) -> ToolResponse:
        self.calls += 1
        if self.calls <= 2:
            raise TransientToolError("upstream 429 rate limit")
        return ToolResponse(
            source=self.name,
            ok=True,
            degraded=False,
            data={"record_id": "acct_123", "result": "updated via API"},
        )


class SecondaryBrowserTool:
    name = "secondary_browser"

    def execute(self, request: ToolRequest) -> ToolResponse:
        if request.payload.get("mode") == "unsafe":
            raise PermanentToolError("browser action blocked by policy")
        return ToolResponse(
            source=self.name,
            ok=True,
            degraded=False,
            data={"record_id": "acct_123", "result": "updated via browser workflow"},
        )


def build_request(action: str, payload: Dict[str, Any]) -> ToolRequest:
    workflow_id = str(uuid.uuid4())
    return ToolRequest(
        tenant_id="tenant-acme",
        workflow_id=workflow_id,
        correlation_id=str(uuid.uuid4()),
        idempotency_key=f"{workflow_id}:{action}",
        action=action,
        payload=payload,
    )


def main() -> None:
    request = build_request("sync_account", {"account_id": "A-42", "mode": "safe"})
    adapters: List[ToolAdapter] = [PrimaryApiTool(), SecondaryBrowserTool()]
    breakers = {
        "primary_api": CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=5.0),
        "secondary_browser": CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=15.0),
    }

    response = invoke_with_fallbacks(request, adapters, breakers)
    print(json.dumps(response.__dict__, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
```

Why this pattern matters:

- Retries are centralized and respect transient vs. permanent classification.
- Circuit breakers prevent unhealthy tools from dominating tail latency.
- The fallback chain preserves service continuity under partial outage.
- Correlation-rich structured logs make replay, audit, and incident reconstruction possible.
- Deterministic degradation ensures the system fails safe instead of silently doing the wrong thing.

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-tenant revenue operations agent for API-rich SaaS

**Problem statement**

Design a multi-tenant revenue operations agent that handles `20k requests/min`, performs CRM and ticketing mutations through stable internal APIs, and must keep `p99 < 8s` for interactive tasks while preserving tenant isolation and auditable side effects.

**Proposed architecture**

Use schema-strict function calling as the default execution path, with MCP/API proxies for internal systems and a Temporal-backed durable workflow layer for approvals and retries.

```text
┌──────────────┐    ┌────────────────┐    ┌──────────────────┐
│ Web / Slack  │ -> │ Agent API      │ -> │ LLM Orchestrator │
└──────────────┘    └────────────────┘    └───────┬──────────┘
                                                  │
                                                  ▼
                                        ┌──────────────────┐
                                        │ Tool Registry    │
                                        │ JSON Schemas     │
                                        └───────┬──────────┘
                                                │
                    ┌───────────────────────────┼───────────────────────────┐
                    ▼                           ▼                           ▼
            ┌───────────────┐           ┌───────────────┐           ┌───────────────┐
            │ CRM API Proxy │           │ Jira API Proxy│           │ Approval Queue │
            └──────┬────────┘           └──────┬────────┘           └──────┬────────┘
                   │                           │                           │
                   ▼                           ▼                           ▼
          ┌────────────────┐         ┌────────────────┐         ┌──────────────────┐
          │ Temporal       │         │ Audit / Trace  │         │ State Store / DLQ│
          └────────────────┘         └────────────────┘         └──────────────────┘
```

Technology choices:

- LLM: function-calling model with strict schema validation
- Workflow: Temporal or equivalent durable workflow engine
- Tool access: internal API proxies or MCP servers with OAuth/resource-bound scopes
- Observability: structured logs plus trace spans for every tool call and approval

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Strict function calling over API proxies | Lowest | Best | Moderate | Strongest, because schemas and scopes are explicit | Very high |
| Browser automation against SaaS UI | Medium to high | Worse | High | Moderate, due to prompt injection and DOM drift | Medium |
| Full computer use over VDI | Highest | Worst | Highest | Weakest of the three without heavy approval gates | Low to medium |

**Decision rationale**

Choose strict function calling over API proxies. The target systems already expose stable APIs, so browser or desktop automation would only add token overhead, UI fragility, and security risk. Temporal gives approval pauses, retries, and resume semantics without replaying already-committed side effects. Browser automation remains a tactical fallback only for the few workflows where the vendor UI exposes capability that the API does not.

### Scenario 2: Procurement onboarding agent for portal-heavy vendors with no stable APIs

**Problem statement**

Design an onboarding agent that must collect documents, navigate supplier portals with inconsistent web UX, perform spreadsheet normalization, and process `2k concurrent workflows` with `p95 < 25s` for each browser step. Some vendors expose no usable API, and the platform must degrade safely when a page changes unexpectedly.

**Proposed architecture**

Use browser-first execution with a hardened browser worker pool, CDP/Playwright-style executor, and server-side code execution for file normalization. Keep a function-calling path for internal systems such as identity, storage, and approvals.

```text
┌──────────────┐    ┌────────────────┐    ┌──────────────────┐
│ Case Worker  │ -> │ Workflow API   │ -> │ LLM Orchestrator │
└──────────────┘    └────────────────┘    └───────┬──────────┘
                                                  │
                     ┌────────────────────────────┼────────────────────────────┐
                     ▼                            ▼                            ▼
            ┌─────────────────┐         ┌─────────────────┐          ┌─────────────────┐
            │ Browser Worker  │         │ Code Sandbox    │          │ Internal APIs   │
            │ CDP / DOM / PNG │         │ CSV/PDF cleanup │          │ IAM / Storage   │
            └────────┬────────┘         └────────┬────────┘          └────────┬────────┘
                     │                           │                            │
                     ▼                           ▼                            ▼
            ┌─────────────────┐         ┌─────────────────┐          ┌─────────────────┐
            │ Snapshot Store  │         │ Artifact Store  │          │ Approval / RBAC │
            └────────┬────────┘         └────────┬────────┘          └────────┬────────┘
                     └──────────────┬────────────┴──────────────┬─────────────┘
                                    ▼                           ▼
                           ┌─────────────────┐         ┌─────────────────┐
                           │ Checkpoints     │         │ Audit / SIEM    │
                           └─────────────────┘         └─────────────────┘
```

Technology choices:

- LLM: browser-capable tool-calling model
- Browser executor: isolated browser workers with DOM/screenshot capture
- Computation: server-side sandbox for file normalization and validation
- Workflow durability: checkpoint store plus event stream or workflow engine

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Browser-first plus code sandbox | Medium | Acceptable if pooled | High | Strong if workers are isolated and approvals are enforced | Medium to high |
| Force API-only by building custom vendor connectors first | Lowest at runtime, highest delivery lead time | Best once built | Very high upfront | Strongest | High after long implementation cycle |
| Full desktop computer use for all vendors | Highest | Worst | Highest | Riskiest because the surface area is broad | Low to medium |

**Decision rationale**

Choose browser-first plus code sandbox. API-only would be ideal technically, but the business constraint is that many vendors do not provide usable APIs and onboarding cannot wait for a long connector program. Full desktop computer use is unnecessarily broad because the target workload is mostly web-native. Browser workers provide enough structure for safer automation, while code execution handles document normalization without pushing that logic onto the operator workstation.
