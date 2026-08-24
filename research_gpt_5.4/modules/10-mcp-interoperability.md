# MCP & Interoperability — Tools, resources, MCP servers/clients

## 1. System Topology & Data Flow

`MCP` is best understood as the interoperability contract between a host runtime and bounded external capabilities, not as a replacement for orchestration, memory, or workflow durability. The host still owns user intent, approval policy, deadline propagation, and final synthesis. MCP standardizes how `tools`, `resources`, and `prompts` are discovered and invoked across heterogeneous runtimes using `JSON-RPC 2.0`, while the backing systems continue to own their own data, auth, and operational state (`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`, `10-mcp-interoperability.md`).

```text
┌────────────────────────────── Control Plane ───────────────────────────────┐
│ User / App -> API Gateway -> AuthN/Z -> Host Agent Runtime                 │
│      │                              │               │                       │
│      │                              │               ├─ Capability Router    │
│      │                              │               ├─ Approval Gate        │
│      │                              │               ├─ Prompt/Tool Policy   │
│      │                              │               └─ Response Synthesizer │
│      └────────────────────────────────────────────> Correlation ID / Tenant │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  v
┌────────────────────────────── Data Plane ───────────────────────────────────┐
│ MCP Client / Gateway                                                        │
│   ├─ Capability discovery                                                   │
│   ├─ Tool invocation                                                        │
│   ├─ Resource reads                                                         │
│   └─ Prompt retrieval                                                       │
│          │                     │                     │                       │
│          v                     v                     v                       │
│   ┌───────────────┐     ┌───────────────┐    ┌────────────────────┐         │
│   │ MCP Server A  │     │ MCP Server B  │    │ Remote A2A Agent   │         │
│   │ tools         │     │ resources     │    │ optional peer mesh │         │
│   │ prompts       │     │ prompts       │    │ not MCP-native     │         │
│   └──────┬────────┘     └──────┬────────┘    └─────────┬──────────┘         │
└──────────┼─────────────────────┼───────────────────────┼─────────────────────┘
           │                     │                       │
           v                     v                       v
┌──────────────────────┐ ┌──────────────────────┐ ┌───────────────────────────┐
│ Persistence Layer    │ │ Tool / Resource      │ │ Telemetry / Audit         │
│ workflow events      │ │ Boundary             │ │ traces / metrics          │
│ session state        │ │ OAuth / PKCE / RBAC  │ │ token ledger              │
│ cache / checkpoints  │ │ PII filter / scopes  │ │ immutable decision log    │
│ idempotency store    │ │ approval decisions   │ │ SIEM / alerting           │
└──────────────────────┘ └──────────────────────┘ └───────────────────────────┘
```

### Request-flow narrative

1. `API Gateway` authenticates the caller, attaches tenant and correlation metadata, and sets an end-to-end deadline.
2. `Host Agent Runtime` decides whether the request can be answered in-text or needs an external capability.
3. `Capability Router` narrows the candidate set to allowed MCP capabilities: `tools` for actions, `resources` for governed reads, and `prompts` for reusable server-defined templates (`03-tool-use.md`, `10-mcp-interoperability.md`).
4. `MCP Client / Gateway` performs discovery and authorization. In the older protocol shape, this can involve connection-scoped capability negotiation; in the newer stateless shape, each request is more self-contained and easier to load-balance (`04-agent-architecture.md`, `10-mcp-interoperability.md`).
5. `Tool / Resource Boundary` enforces least privilege, approval requirements, schema validation, and PII filtering before the call reaches the backing system.
6. The selected `MCP Server` executes a bounded action or read. The backing system, not the protocol, remains the source of truth for authorization, business rules, and storage (`06-rag.md`, `07-memory.md`, `10-mcp-interoperability.md`).
7. The host reinjects the result into the model context, synthesizes the final answer, and records decision metadata, retries, auth scopes, and degraded branches in telemetry.

The architectural boundary that matters most is `governance above the protocol, capabilities below the protocol`. If that split is blurred, MCP servers become ad hoc workflow engines and policy enforcement fragments across every tool server. The local research consistently points in the opposite direction: keep session state, approvals, and replay in the host or workflow layer; keep interoperable capability access in MCP (`04-agent-architecture.md`, `05-agent-frameworks.md`, `09-multi-agent-systems.md`, `10-mcp-interoperability.md`).

## 2. Core Mechanics & Algorithms

### MCP as a guarded capability state machine

The safest production model is a bounded state machine:

```text
ACCEPT
  -> DISCOVER_CAPABILITIES
  -> FILTER_BY_POLICY
  -> AUTHORIZE
  -> SELECT_CAPABILITY
  -> VALIDATE_SCHEMA
  -> EXECUTE
  -> OBSERVE_RESULT
     -> COMPLETE
     -> RETRY_TRANSIENT
     -> FALLBACK_SERVER
     -> APPROVAL_WAIT
     -> FAIL_CLOSED
```

This is more precise than "the model calls a tool." Discovery, authorization, and validation are distinct control steps, and each is independently failure-prone (`03-tool-use.md`, `04-agent-architecture.md`, `08-planning-reasoning.md`).

### Protocol roles and capability surfaces

- `Host`: owns user-facing workflow, model loop, and final answer.
- `Client`: implements the MCP transport and request/response contract toward one or more servers.
- `Server`: exposes `tools`, `resources`, and `prompts` behind stable schemas.
- `Backing system`: the real protected resource such as search, ticketing, file storage, or CRM.

That distinction matters because `MCP` and `A2A` are not interchangeable. MCP exposes bounded capabilities into a host runtime. `A2A` delegates work to another remote agent that owns its own reasoning, transport, auth, and lifecycle (`05-agent-frameworks.md`, `09-multi-agent-systems.md`, `10-mcp-interoperability.md`).

### Discovery, routing, and complexity

If a host knows `S` servers exposing `T` tools, `R` resources, and `P` prompts, a first-order discovery pass is:

```text
discovery_cost ~= O(S + T + R + P)
```

but the real runtime routing cost is over the eligible subset:

```text
eligible_capabilities
  = capabilities
    filtered_by(tenant_policy, auth_scope, data_region, side_effect_risk)

route_decision_cost ~= O(|eligible_capabilities|)
```

Without policy prefiltering, the model sees too many capabilities, token cost rises linearly with schema surface, and selection quality degrades (`03-tool-use.md`, `10-mcp-interoperability.md`).

### Critical-path latency

Interoperability does not erase the tool loop:

```text
critical_path_latency
  ~= routing
   + auth_and_discovery
   + max(parallel_mcp_branch_latency)
   + approvals
   + result_reinjection
   + synthesis
```

Stateless MCP requests improve horizontal scaling because a load balancer no longer needs strong connection affinity, but that does not remove approval stalls, model reinjection cost, or remote-server tail latency (`04-agent-architecture.md`, `10-mcp-interoperability.md`).

### Invariants and convergence properties

- `Single policy owner invariant`: the host, not the server, owns approval and user-facing policy.
- `Stable identity invariant`: every call carries `correlation_id`, `tenant_id`, `idempotency_key`, and `target_server`.
- `Bounded loop invariant`: `max_mcp_calls`, `max_resource_reads`, and total deadline must be finite.
- `Schema invariant`: the host must validate arguments and result envelopes before side effects or reinjection.
- `Separation invariant`: MCP capability access stays separate from workflow durability and conversation memory.

Convergence is achieved only if every retry burns remaining budget and every fallback narrows capability or quality rather than expanding search width:

```text
converges if:
  max_calls is finite
  retry_budget is finite
  each retry decreases remaining deadline
  fallback path is cheaper or narrower than primary path
```

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: The local research set is much stronger on prompt-cost mechanics, cache behavior, and protocol structure than on vendor-neutral end-to-end MCP percentile benchmarks. The `p50/p95/p99` numbers below are recommended SLO envelopes to engineer toward, not protocol guarantees.

### Cost formulas

Assumptions:

- `runs = 1000`
- `H_u` = uncached host input tokens per run
- `H_c` = cache-eligible host prefix tokens per run
- `h` = cache-hit rate on `H_c`
- `R_in` = tool/resource result tokens reinjected into host context
- `O` = host output tokens
- `P_in`, `P_cache`, `P_out` = host model prices per `1M` tokens
- `N_t`, `N_r` = average MCP tool calls and resource reads per run
- `C_t`, `C_r` = non-token request charges per tool or resource call, if any
- `C_auth` = amortized auth/discovery cost per run
- `M_remote` = any remote model-backed server cost per run

Effective host input cost:

```text
host_input_cost
  = (
      H_u * P_in +
      H_c * ((1 - h) * P_in + h * P_cache)
    ) / 1_000_000
```

Total interoperable run cost:

```text
$ cost per 1k runs
  = 1000 * (
      host_input_cost +
      (R_in * P_in) / 1_000_000 +
      (O * P_out) / 1_000_000 +
      N_t * C_t +
      N_r * C_r +
      C_auth +
      M_remote
    )
```

This formula makes the key point explicit: `MCP` adds portability, not free execution. Tool schemas still occupy prompt context, result payloads still get reinjected, and authorization or transport still adds tail cost (`03-tool-use.md`, `10-mcp-interoperability.md`).

### Worked numeric example

Use the following auditable assumption set derived from the local research set:

- `H_u = 1800`
- `H_c = 4200`
- `h = 0.80`
- `R_in = 1200`
- `O = 300`
- `N_t = 1`
- `N_r = 1`
- `C_t = $0.0008`
- `C_r = $0.0002`
- `C_auth = $0.0004`
- `M_remote = $0`

#### Deep host tier: `gpt-5.6-terra`

Using local pricing assumptions from `04-agent-architecture.md`:

- `P_in = $2.00 / 1M`
- `P_cache = $0.20 / 1M`
- `P_out = $12.00 / 1M`

```text
host_input_cost
  = (
      1800 * 2.00 +
      4200 * ((1 - 0.80) * 2.00 + 0.80 * 0.20)
    ) / 1_000_000
  = (3600 + 2352) / 1_000_000
  = $0.005952 per run
```

```text
$ cost per 1k runs
  = 1000 * (
      0.005952 +
      (1200 * 2.00) / 1_000_000 +
      (300 * 12.00) / 1_000_000 +
      1 * 0.0008 +
      1 * 0.0002 +
      0.0004
    )
  = 1000 * 0.013352
  = $13.35 per 1k runs
```

#### Fast host tier: `gpt-5.6-luna`

Using local pricing assumptions from `04-agent-architecture.md`:

- `P_in = $0.20 / 1M`
- `P_cache = $0.02 / 1M`
- `P_out = $1.20 / 1M`

```text
$ cost per 1k runs
  = 1000 * (
      (
        1800 * 0.20 +
        4200 * ((1 - 0.80) * 0.20 + 0.80 * 0.02)
      ) / 1_000_000 +
      (1200 * 0.20) / 1_000_000 +
      (300 * 1.20) / 1_000_000 +
      0.0008 + 0.0002 + 0.0004
    )
  = 1000 * 0.0025952
  = $2.60 per 1k runs
```

Cache savings on the deep tier are also explicit:

```text
uncached_deep_cost
  = 1000 * (
      ((1800 + 4200 + 1200) * 2.00) / 1_000_000 +
      (300 * 12.00) / 1_000_000 +
      0.0008 + 0.0002 + 0.0004
    )
  = $19.40 per 1k runs

cache_savings
  = $19.40 - $13.35
  = $6.05 per 1k runs
```

The practical lesson is that stable server metadata, tool schemas, and approval instructions should remain cacheable across turns. Transport elegance matters less than schema stability and reinjection discipline (`03-tool-use.md`, `10-mcp-interoperability.md`).

### Latency targets

Recommended user-facing SLO envelopes:

- `Local stdio or in-cluster MCP`: `p50 <= 700ms`, `p95 <= 2.0s`, `p99 <= 4.0s`
- `Regional HTTP MCP with approval-capable writes`: `p50 <= 1.5s`, `p95 <= 4.5s`, `p99 <= 8.0s`
- `Multi-hop interoperable workflow with resource read + tool call + synthesis`: `p50 <= 3.0s`, `p95 <= 8.0s`, `p99 <= 15.0s`

Mitigations by percentile:

- `p50`: cache stable tool catalogs, keep schemas narrow, reuse auth metadata, and colocate the host with frequently used servers.
- `p95`: parallelize independent resource reads, cap result payload size before reinjection, and pre-authorize low-risk read-only tools.
- `p99`: enforce per-server deadlines, open circuit breakers on degraded servers, switch to cached read-only responses, and fail closed for blocked privileged operations.

### Throughput and back-pressure

MCP systems do not saturate on `requests/sec` alone. They usually saturate on the narrowest downstream boundary:

```text
effective_qps
  <= min(
       host_qps,
       server_qps / avg_mcp_calls_per_run,
       auth_qps / avg_auth_round_trips_per_run,
       approval_qps / avg_privileged_actions_per_run
     )
```

```text
branch_arrival_rate
  = ingress_qps * avg_mcp_calls_per_run
```

```text
queue_pressure
  = branch_arrival_rate / branch_service_rate
```

If `queue_pressure > 1`, p95 and p99 explode before the system looks fully unavailable. Production back-pressure should therefore:

1. shed optional enrichment tools before core read paths
2. reduce result payload size before disabling whole servers
3. downgrade to read-only resource paths when write approvals backlog
4. deny new privileged actions when approval or auth services threaten the global deadline

### Availability, RPO, RTO, and compliance

Recommended enterprise targets:

- `Availability`: `99.9%` for internal governed-read paths, `99.95%` for tier-1 customer-facing tool workflows
- `RPO`: `<= 1 minute` for workflow events and approval state; `0` for immutable audit events after acknowledgment
- `RTO`: `<= 15 minutes` for same-region host failover; `<= 60 minutes` for cross-region server failover

Compliance posture:

- `SOC 2` / `ISO 27001`: durable audit logs, secret separation, least privilege, and change control over server registries
- `GDPR` / `CCPA`: data minimization, residency-aware routing, deletion workflows, and auditability of cross-server reads
- sector-specific regulated use: dual approval for writes, explicit separation between recommendation and execution paths, and immutable decision lineage

## 4. Distributed Resilience & Security

### Durable execution patterns

MCP itself is not the durable state layer. Durable execution belongs above it.

Recommended pattern:

```text
User Request
  -> Workflow Engine (Temporal or equivalent)
  -> Host Runtime
  -> MCP Call Activity
  -> Checkpoint result / approval / retry state
  -> Synthesis
  -> Final response or resumable pause
```

Evented alternative:

```text
Ingress
  -> Kafka topic
  -> Host consumer
  -> MCP boundary worker
  -> result topic
  -> reducer / synthesizer
  -> DLQ on poison payloads
```

Why this matters:

- `workflow replay`: reuses prior activity outcomes instead of repeating already-committed side effects
- `distributed locking`: prevents duplicate writes against the same business object
- `checkpointing`: persists completed branches even if one server fails
- `dead-letter handling`: quarantines incompatible server versions or permanently invalid payloads instead of looping forever

### Failure taxonomy

Transient failures:

- short-lived HTTP or `stdio` transport failure
- temporary auth-server latency spike
- rate limits on one MCP server
- timeouts on a resource read that is otherwise healthy

Permanent failures:

- schema mismatch between host and server
- missing capability after version drift
- RBAC denial for the requested tool
- resource no longer exists or is out of scope

Poison-pill signals:

- same normalized payload fails across `N` retries with the same schema error
- a server repeatedly returns incompatible capability metadata
- replayed write requests keep hitting identical permanent denial reasons

Required controls:

- idempotency keys on every side-effecting tool call
- exponential backoff with jitter only for transient failures
- circuit breakers per target server or capability family
- DLQ promotion after retry-budget exhaustion
- fallback from `tool` to `resource` or cached deterministic answer when policy allows

### Zero-Trust MCP security model

The local research is clear that Zero-Trust MCP starts with auth scoped to the server, not broad bearer access to the whole tool mesh (`03-tool-use.md`, `04-agent-architecture.md`, `08-planning-reasoning.md`, `10-mcp-interoperability.md`).

Minimum enterprise stance:

1. Treat every MCP server as a separate protected resource.
2. Use `OAuth 2.1`, Protected Resource Metadata discovery, resource-bound tokens, and `PKCE` with `S256` for HTTP transports.
3. Keep `stdio` credentials outside model context and inject them from the runtime environment.
4. Apply tool-level `RBAC` and tenant scopes before the model can execute a side effect.
5. Require explicit approval for mutating tools even when read-only resources are auto-approved.

### PII filtering and immutable auditability

A defensible PII pipeline is:

```text
request_or_result
  -> classify sensitive fields
  -> redact or tokenize
  -> validate policy scope
  -> execute or reinject minimal safe payload
  -> append immutable audit event
```

Minimum audit record per interoperable branch:

- `correlation_id`
- `tenant_id`
- `server_id`
- `capability_type` and `capability_name`
- prompt hash and policy version
- auth scope and approval outcome
- redaction actions taken
- retry count and breaker state
- result hash and degraded flag

> ⚠️ Gap: The local research is much stronger on authorization, approvals, and discovery than on first-party immutable audit schemas or built-in PII-redaction internals. Production teams should expect to implement those controls in the platform layer rather than assuming the protocol supplies them.

## 5. Production Enterprise Code

The example below is a runnable Python host-runtime skeleton for interoperable MCP-style capability access. It demonstrates:

- retries with exponential backoff and jitter
- a circuit breaker with `closed -> open -> half_open`
- a fallback chain `primary server -> secondary server -> deterministic fallback`
- structured JSON logging with correlation IDs
- graceful degradation when a server or tool path is impaired

```python
from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Any, Callable, Dict, List, Optional


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts_ms": int(record.created * 1000),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in (
            "event",
            "correlation_id",
            "tenant_id",
            "server_id",
            "capability",
            "degraded",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("mcp_runtime")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = build_logger()


def log_event(event: str, message: str, **extra: Any) -> None:
    LOGGER.info(message, extra={"event": event, **extra})


@dataclass(frozen=True)
class CapabilityRequest:
    tenant_id: str
    capability: str
    args: Dict[str, Any]
    idempotency_key: str
    correlation_id: str


@dataclass(frozen=True)
class CapabilityResponse:
    ok: bool
    payload: Dict[str, Any]
    degraded: bool = False
    reason: Optional[str] = None


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_timeout_s: float) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.opened_at = 0.0
        self._lock = Lock()

    def before_call(self) -> None:
        with self._lock:
            if self.state == CircuitState.OPEN:
                if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise TransientError("circuit_open")

    def record_success(self) -> None:
        with self._lock:
            self.failure_count = 0
            self.state = CircuitState.CLOSED
            self.opened_at = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.monotonic()


def retry(
    fn: Callable[[], CapabilityResponse],
    retries: int,
    base_delay_s: float,
    max_delay_s: float,
) -> CapabilityResponse:
    attempt = 0
    while True:
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError:
            if attempt >= retries:
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** attempt))
            jitter = random.uniform(0.0, delay * 0.25)
            time.sleep(delay + jitter)
            attempt += 1


class PiiFilter:
    BLOCKED_KEYS = {"ssn", "credit_card", "passport"}

    def sanitize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        clean: Dict[str, Any] = {}
        for key, value in payload.items():
            if key in self.BLOCKED_KEYS:
                clean[key] = "[REDACTED]"
            else:
                clean[key] = value
        return clean


class MockMCPServer:
    def __init__(self, server_id: str, fail_keyword: Optional[str] = None) -> None:
        self.server_id = server_id
        self.fail_keyword = fail_keyword
        self.supported_tools = {"search_knowledge", "create_ticket"}

    def call_tool(self, request: CapabilityRequest) -> CapabilityResponse:
        if request.capability not in self.supported_tools:
            raise PermanentError(f"unsupported_capability={request.capability}")
        if self.fail_keyword and self.fail_keyword in json.dumps(request.args, sort_keys=True):
            raise TransientError(f"{self.server_id}_temporary_failure")
        if request.capability == "create_ticket" and "approved" not in request.args:
            raise PermanentError("approval_required")
        if request.capability == "search_knowledge":
            query = request.args.get("query", "")
            return CapabilityResponse(
                ok=True,
                payload={
                    "server": self.server_id,
                    "documents": [
                        f"policy hit for '{query}'",
                        f"kb hit for '{query}'",
                    ],
                },
            )
        return CapabilityResponse(
            ok=True,
            payload={
                "server": self.server_id,
                "ticket_id": f"T-{abs(hash(request.idempotency_key)) % 10000:04d}",
                "status": "created",
            },
        )


class MCPGateway:
    def __init__(self, primary: MockMCPServer, secondary: MockMCPServer) -> None:
        self.primary = primary
        self.secondary = secondary
        self.primary_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=5.0)
        self.secondary_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=5.0)
        self.pii_filter = PiiFilter()

    def invoke(
        self,
        tenant_id: str,
        capability: str,
        args: Dict[str, Any],
        require_approval: bool,
    ) -> CapabilityResponse:
        correlation_id = str(uuid.uuid4())
        idempotency_key = str(uuid.uuid4())
        sanitized_args = self.pii_filter.sanitize(args)
        log_event(
            "request_started",
            "starting interoperable capability call",
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            capability=capability,
        )

        if require_approval and not sanitized_args.get("approved"):
            raise PermanentError("approval_missing")

        request = CapabilityRequest(
            tenant_id=tenant_id,
            capability=capability,
            args=sanitized_args,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

        primary_result = self._call_server(
            request=request,
            server=self.primary,
            breaker=self.primary_breaker,
        )
        if primary_result is not None:
            return primary_result

        secondary_result = self._call_server(
            request=request,
            server=self.secondary,
            breaker=self.secondary_breaker,
        )
        if secondary_result is not None:
            return CapabilityResponse(
                ok=True,
                payload=secondary_result.payload,
                degraded=True,
                reason="secondary_server_used",
            )

        fallback = self._deterministic_fallback(capability, sanitized_args)
        log_event(
            "request_degraded",
            "all MCP servers unavailable, returning deterministic fallback",
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            capability=capability,
            degraded=True,
        )
        return CapabilityResponse(
            ok=True,
            payload=fallback,
            degraded=True,
            reason="deterministic_fallback",
        )

    def _call_server(
        self,
        request: CapabilityRequest,
        server: MockMCPServer,
        breaker: CircuitBreaker,
    ) -> Optional[CapabilityResponse]:
        try:
            breaker.before_call()
        except TransientError:
            log_event(
                "breaker_open",
                "server breaker open, skipping server",
                correlation_id=request.correlation_id,
                tenant_id=request.tenant_id,
                server_id=server.server_id,
                capability=request.capability,
                degraded=True,
            )
            return None

        try:
            response = retry(
                lambda: server.call_tool(request),
                retries=2,
                base_delay_s=0.05,
                max_delay_s=0.25,
            )
            breaker.record_success()
            log_event(
                "server_success",
                "server call succeeded",
                correlation_id=request.correlation_id,
                tenant_id=request.tenant_id,
                server_id=server.server_id,
                capability=request.capability,
            )
            return response
        except PermanentError:
            raise
        except TransientError as exc:
            breaker.record_failure()
            log_event(
                "server_failed",
                "server call failed transiently",
                correlation_id=request.correlation_id,
                tenant_id=request.tenant_id,
                server_id=server.server_id,
                capability=request.capability,
                degraded=True,
            )
            if str(exc) == "circuit_open":
                return None
            return None

    @staticmethod
    def _deterministic_fallback(capability: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if capability == "search_knowledge":
            query = args.get("query", "")
            return {
                "source": "cached_bundle",
                "documents": [f"cached fallback for '{query}'"],
            }
        return {
            "source": "manual_queue",
            "status": "queued_for_human_review",
        }


if __name__ == "__main__":
    random.seed(7)
    gateway = MCPGateway(
        primary=MockMCPServer(server_id="primary", fail_keyword="force_fail"),
        secondary=MockMCPServer(server_id="secondary"),
    )

    search_result = gateway.invoke(
        tenant_id="acme",
        capability="search_knowledge",
        args={"query": "MCP PKCE resource indicators"},
        require_approval=False,
    )
    print(json.dumps(search_result.payload, indent=2, sort_keys=True))

    ticket_result = gateway.invoke(
        tenant_id="acme",
        capability="create_ticket",
        args={"title": "Escalate outage", "approved": True},
        require_approval=True,
    )
    print(json.dumps(ticket_result.payload, indent=2, sort_keys=True))
```

The production idea is that degradation is explicit and policy-aware. Read-heavy paths can fall back to cached or secondary-server responses, while privileged write paths fail closed unless approval and authorization conditions are satisfied.

## 6. Architectural System Design Scenarios

### Scenario 1: Enterprise copilot over governed internal systems

**Problem statement**: Design a multi-tenant enterprise copilot that serves knowledge-base reads, ticket creation, and CRM lookup across `12k` requests/min. The system must keep `p99 <= 8s` for write-capable workflows, preserve a centralized approval plane, and allow the host model/runtime to change without rewriting every downstream integration.

**Proposed architecture**:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ User -> Copilot UI -> API Gateway -> Host Runtime                          │
│                                  │                                         │
│                                  ├─ MCP Client -> KB Server (resources)     │
│                                  ├─ MCP Client -> Ticket Server (tools)     │
│                                  └─ MCP Client -> CRM Server (tools/read)   │
│                                                                            │
│ Approval service gates writes before MCP dispatch                          │
│ Policy proxy injects tenant scope, RBAC, and redaction                     │
│ Workflow store keeps run state, approvals, retries, and idempotency keys   │
│ Audit sink stores capability use, auth scope, and degraded-path lineage    │
└────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Vendor-native function tools only | Lowest at first | Strong p50, weaker portability | Low | Good inside one stack, weaker cross-runtime reuse | Medium |
| Host with MCP tool/resource servers | Moderate | Moderate | Moderate | Strong central policy plus reusable capability boundary | High |
| Remote A2A specialists per system | Highest | Slowest tails | Highest | Strong isolation, fragmented control plane | High |

**Decision rationale**: Choose `host with MCP tool/resource servers`. It preserves one approval and audit plane while keeping each business system behind a reusable interoperable contract. Vendor-native tools are cheaper when the estate is homogeneous, but they become a lock-in and duplication problem once multiple runtimes need the same governed capabilities. A2A is too heavy because the problem is capability reuse, not delegated remote reasoning.

### Scenario 2: Cross-business-unit interoperability hub

**Problem statement**: Design an interoperability hub that lets multiple internal agent runtimes share search, policy, and document-access services across business units with different model vendors and deployment stacks. The hub must support `p95 <= 8s` for read-heavy workflows, tolerate partial server outages, and enforce region-specific data access policies.

**Proposed architecture**:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Multiple Host Runtimes                                                     │
│ OpenAI-centric host   ADK host   LangGraph host   CrewAI host              │
│         │                │            │              │                      │
│         └────────────────┴────────────┴──────────────┘                      │
│                                │                                           │
│                           MCP Gateway Mesh                                  │
│                    ┌───────────┼───────────┐                                │
│                    v           v           v                                │
│              Search Server  Policy Server  Document Server                  │
│                    │           │           │                                │
│                    └──────> regional data services                          │
│                                                                            │
│ Kafka event bus captures retries, DLQ, and server-health events            │
│ Temporal workflows manage replay, failover, and approval wait states       │
│ Audit ledger stores region, scope, server, and output hashes               │
└────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Per-runtime duplicate integrations | High duplicated build cost | Best local p50, worst long-term maintainability | Medium | Inconsistent policy enforcement | Medium |
| Shared MCP gateway and servers | Moderate shared platform cost | Moderate | High initially, lower marginal integration cost | Strong reusable Zero-Trust boundary | Very High |
| Full remote A2A mesh across BUs | Highest | Highest | Highest | Strong per-agent isolation, weakest central simplicity | High |

**Decision rationale**: Choose `shared MCP gateway and servers`. The organization-wide goal is interoperability of bounded capabilities, not free-form collaboration among remote agents. A shared MCP layer gives the best long-term leverage because policy, auth, and observability become reusable infrastructure instead of being reimplemented inside each runtime. Duplicated local integrations may look faster initially but create inconsistent RBAC, fragmented audit trails, and higher total cost of change.

## Sources

- [1] `03-tool-use.md` - Local note covering tool schemas, prompt-cost overhead, caching, MCP transport/auth basics, and validation risks.
- [2] `04-agent-architecture.md` - Local note covering MCP protocol evolution, control-plane/data-plane split, durability patterns, and pricing assumptions for model tiers.
- [3] `05-agent-frameworks.md` - Local note covering framework integration of MCP, approval planes, persistence, and workflow trade-offs.
- [4] `06-rag.md` - Local note covering permission-aware retrieval and `MCP` as an access path for knowledge bases.
- [5] `07-memory.md` - Local note covering governed retrieval, memory boundary separation, and authorization propagation.
- [6] `08-planning-reasoning.md` - Local note covering guarded execution, approval checkpoints, strict schemas, and replanning controls.
- [7] `09-multi-agent-systems.md` - Local note covering supervisor-worker governance, A2A delegation, remote failure domains, and auth surfaces.
- [8] `10-mcp-interoperability.md` - Local research note synthesized into this module.
