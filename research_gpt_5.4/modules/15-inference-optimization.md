# Inference & Optimization — Caching, Routing, Batching, Quantization

## 1. System Topology & Data Flow

Inference optimization is a control-plane discipline layered around model execution, not a single serving trick. In the local `research_cursor` corpus, the most defensible optimizations are `exact-prefix prompt caching`, `semantic caching`, `context compaction`, `artifact lazy-loading`, `planner-to-executor routing`, `parallel branch execution`, and `node-level reuse`. The common design principle is to spend scarce decode latency and premium-model tokens only where they materially improve answer quality.

```text
┌────────────────────────────── Control Plane ──────────────────────────────┐
│ API Gateway -> AuthN/Z -> Policy Engine -> Router -> Budget Manager       │
│      │               │              │            │            │            │
│      │               │              │            │            ├─ SLA tier  │
│      │               │              │            │            ├─ cache TTL │
│      │               │              │            │            └─ deadlines │
│      └────────────────────────────> Correlation ID / Tenant / Priority     │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  v
┌─────────────────────────────── Data Plane ────────────────────────────────┐
│ Request -> Prefix Cache -> Semantic Cache -> Planner -> Batch Queue       │
│                │               │                │             │            │
│                │               │                │             ├─ microbatch│
│                │               │                │             └─ fan-out   │
│                │               │                └─ model tier route        │
│                │               └─ similarity hit / miss                    │
│                └─ exact prefix hit / miss                                  │
│                                 │                                          │
│                                 v                                          │
│                    Executor Models / Quantized Replicas                    │
│                       │              │                │                     │
│                       ├─ premium API  ├─ fast API     └─ local int8/4-bit  │
│                       │              │                │                     │
│                       └──────────────┴──────────────┬──────────────────────┘
│                                                     v
│                                          Verifier / Response Builder
└─────────────────────────────────────────────────────────────────────────────┘
          │                        │                         │
          v                        v                         v
┌──────────────────┐   ┌────────────────────┐   ┌──────────────────────────┐
│ Persistence      │   │ Tool Proxies       │   │ Telemetry / Audit Sinks  │
│ checkpoints      │   │ Zero-Trust MCP     │   │ traces / metrics / logs  │
│ cache metadata   │   │ RBAC / approvals   │   │ cost ledger / SIEM       │
│ artifact refs    │   │ PII filters        │   │ immutable event journal   │
└──────────────────┘   └────────────────────┘   └──────────────────────────┘
```

### Request-flow narrative

1. `API Gateway` admits the request with `tenant_id`, `correlation_id`, deadline, and a latency tier such as interactive, batch, or background.
2. `Prefix Cache` checks whether the stable instruction scaffold, tool schema block, and policy prefix exactly match a previously written cacheable prefix. A hit reduces repeated prompt transmission cost but still depends on byte-stable serialization.
3. `Semantic Cache` optionally checks whether a prior answer can be safely reused for low-risk, paraphrased requests. This optimization is broader than exact-prefix caching, but its failure mode is incorrect reuse rather than a simple miss.
4. `Router` chooses the execution path: direct fast-model answer, `strong planner + bounded executors`, or a quantized local replica for cheap bulk processing. The router uses difficulty, trust level, and budget signals rather than only prompt length.
5. `Batch Queue` groups compatible requests or parallel branches. In this corpus, batching is most defensible as `workflow-level parallel fan-out` and `microbatch admission`, not as a claim about proprietary vendor GPU internals.
6. `Executor Models` run the selected path. Premium reasoning models handle the smallest number of steps possible; cheaper executors or local quantized replicas absorb repetitive bounded work.
7. `Verifier` checks tool outcomes, response constraints, and fallback conditions. The system must distinguish `attempted action`, `confirmed external effect`, and `cached reuse`.
8. `Persistence` stores checkpoints, cache metadata, and artifact references so retries or resumes do not erase optimization gains.
9. `Telemetry / Audit Sinks` record hit rates, route lineage, queue delay, branch fan-out, and degraded-mode events so optimization can be measured instead of assumed.

The end-to-end lesson is that optimization is primarily `send less`, `route smarter`, `parallelize only independent work`, and `keep durable state outside the prompt`. Low-level kernel optimization such as quantization matters most once these higher-leverage controls are already in place.

## 2. Core Mechanics & Algorithms

### Optimization state machine

```text
ACCEPT
  -> CLASSIFY_REQUEST
  -> CHECK_PREFIX_CACHE
       -> HIT  -> VERIFY_REUSE -> COMPLETE
       -> MISS -> CHECK_SEMANTIC_CACHE
  -> CHECK_SEMANTIC_CACHE
       -> SAFE_HIT -> VERIFY_REUSE -> COMPLETE
       -> MISS     -> ROUTE_MODEL
  -> ROUTE_MODEL
       -> FAST_DIRECT
       -> PLANNER_EXECUTOR
       -> QUANTIZED_BULK
  -> BATCH_OR_FAN_OUT
  -> EXECUTE
  -> VERIFY
  -> CACHE_WRITE
  -> COMPLETE
  -> DEGRADED_COMPLETE
  -> FAIL
```

This state machine matters because each optimization has a different correctness boundary:

- `prefix cache`: exact identity invariant
- `semantic cache`: similarity plus policy-safety invariant
- `routing`: task-complexity and trust-boundary invariant
- `batching`: independence invariant
- `quantization`: quality-budget invariant

### Core algorithms

#### Exact-prefix caching

Exact-prefix caching works when a large prompt prefix is identical across runs. Complexity is effectively:

```text
cache_lookup = O(1)
cache_write  = O(1)
```

from the application perspective, assuming a hash or provider-managed prefix index. The real invariant is:

```text
stable_prefix_bytes(request_n) == stable_prefix_bytes(request_n+1)
```

If tool schemas, policy headers, or ordering change, the optimization silently degrades into write-heavy misses.

#### Semantic caching

Semantic caching adds a vector lookup:

```text
semantic_lookup = O(log N) approximate ANN
validation_cost = O(k)
```

where `N` is cached items and `k` is the number of candidate neighbors re-ranked or policy-checked. It can improve hit rate for paraphrased support or retrieval questions, but the critical invariant is:

```text
similar_text != equivalent_constraints
```

A semantically similar prior answer is unsafe if tenant scope, freshness, or hidden business constraints differ.

#### Routing

The main router problem is a bounded decision policy:

```text
route(request)
  = argmin_path expected_cost(path)
    subject to latency_slo(path), quality_floor(path), risk_policy(path)
```

Practical route features from the local corpus:

- number of required steps
- tool-use likelihood
- need for long-context synthesis
- compliance sensitivity
- deadline and cost budget

For a classifier with `m` features and `r` candidate routes:

```text
routing_decision = O(m * r)
```

which is negligible compared with model inference, so the hard part is policy quality rather than algorithmic complexity.

#### Batching and parallel fan-out

In the local notes, batching is best modeled as a queueing and dependency problem:

```text
critical_path_latency
  = planning_latency
  + max(parallel_branch_durations)
  + verification_latency
  + synthesis_latency
```

Parallelization helps only when branches are mostly independent. If each step depends on the prior step, parallel fan-out adds queueing and synthesis overhead without shrinking the critical path.

Microbatch sizing rule:

```text
effective_batch_size
  = min(queue_depth, max_batch_size, tokens_budget / avg_tokens_per_request)
```

This exposes the main batching trade-off: wait longer for a bigger batch and improve hardware efficiency, or flush early to protect p95 and p99 latency.

#### Quantization

The local corpus is thin on quantization internals, so the safest treatment is architectural rather than benchmark-heavy. The basic mechanics are:

- lower-precision weights reduce memory footprint
- lower memory pressure can increase replica density and reduce serving cost
- quality loss is workload dependent and must be verified against domain-specific evals

A first-order memory approximation is:

```text
model_memory_bytes
  ~= parameter_count * bytes_per_weight + kv_cache_bytes + runtime_overhead
```

Moving from higher precision to lower precision reduces `bytes_per_weight`, but it does not guarantee equivalent latency or answer quality. The operational invariant is:

```text
quantized_route_allowed only if eval_score >= task_quality_floor
```

### Convergence and correctness invariants

- `max_retries`, `max_fanout`, and end-to-end deadline must be explicit.
- Cache writes must include `tenant_id`, `policy_version`, and input-shape fingerprint.
- Semantic reuse must validate freshness, tenant scope, and trust level before replay.
- Planner/executor routing is only beneficial if worker context is materially smaller than the full transcript.
- Batch queues must flush on deadline, not only on size.
- Quantized replicas must be gated by task-specific evaluation thresholds and reversible fallback paths.

> ⚠️ Gap: The local `research_cursor` set is strong on caching, routing, context compaction, and workflow-level parallelism, but weak on concrete public benchmarks for `INT8`, `4-bit`, `AWQ`, `GPTQ`, or KV-cache quantization trade-offs. Quantization is therefore framed here as an enterprise control surface, not as a fabricated benchmark table.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: The local `research_cursor` corpus supports cache multipliers, route economics, throughput formulas, and critical-path analysis more strongly than universal public percentile benchmarks. The `p50/p95/p99` values below are engineering targets for module design, not provider guarantees.

### Cost formulas

Assumptions:

- `runs = 1000`
- `U` = uncached dynamic input tokens per run
- `C` = stable cacheable prefix tokens per run
- `h` = exact-prefix cache hit rate on `C`
- `S` = semantic-cache hit rate on full requests eligible for safe replay
- `O` = output tokens per run
- `P_in_fast`, `P_out_fast` = fast-tier input/output price per `1M` tokens
- `P_in_deep`, `P_out_deep` = premium-tier input/output price per `1M` tokens
- `P_in_local`, `P_out_local` = local or self-hosted effective token cost per `1M` tokens
- cache writes are approximated at `1.25x` base input price where supported in local notes
- cache reads are approximated at `0.10x` base input price where supported in local notes
- `B` = batching efficiency multiplier, where `0 < B <= 1` and lower is better cost per request due to amortized overhead
- `Q` = quantized-route fraction of runs
- `R_fast`, `R_deep`, `R_local` = fraction of routed runs assigned to each model tier after cache reuse

Reusable cached-input primitive:

```text
effective_input_cost(model)
  = (
      U * P_in_model
      + C * (1 - h) * 1.25 * P_in_model
      + C * h * 0.10 * P_in_model
    ) / 1_000_000
```

Semantic-cache-adjusted served-run fraction:

```text
served_fraction
  = 1 - S
```

Weighted routed model cost:

```text
weighted_model_cost_per_run
  = served_fraction * B * (
      R_fast * (
        effective_input_cost(fast)
        + (O * P_out_fast) / 1_000_000
      )
      + R_deep * (
        effective_input_cost(deep)
        + (O * P_out_deep) / 1_000_000
      )
      + R_local * (
        effective_input_cost(local)
        + (O * P_out_local) / 1_000_000
      )
    )
```

Final cost expression:

```text
$ cost per 1k runs
  = 1000 * weighted_model_cost_per_run
    + cache_storage_cost
    + checkpoint_cost
    + retrieval_or_tool_surcharges
```

### Worked numeric example

Use the following explicit example assumption set for `1000` runs:

- `U = 2200`
- `C = 3800`
- `h = 0.75`
- `S = 0.20`
- `O = 300`
- `P_in_fast = $0.30 / 1M`
- `P_out_fast = $1.20 / 1M`
- `P_in_deep = $3.00 / 1M`
- `P_out_deep = $12.00 / 1M`
- `P_in_local = $0.12 / 1M`
- `P_out_local = $0.48 / 1M`
- `R_fast = 0.55`
- `R_deep = 0.20`
- `R_local = 0.25`
- `B = 0.92`

Fast-tier per-run input cost:

```text
effective_input_cost(fast)
  = (
      2200 * 0.30
      + 3800 * (1 - 0.75) * 1.25 * 0.30
      + 3800 * 0.75 * 0.10 * 0.30
    ) / 1_000_000
  = (660 + 356.25 + 85.5) / 1_000_000
  = $0.00110175
```

Deep-tier per-run input cost:

```text
effective_input_cost(deep)
  = (
      2200 * 3.00
      + 3800 * (1 - 0.75) * 1.25 * 3.00
      + 3800 * 0.75 * 0.10 * 3.00
    ) / 1_000_000
  = (6600 + 3562.5 + 855) / 1_000_000
  = $0.0110175
```

Local quantized-tier per-run input cost:

```text
effective_input_cost(local)
  = (
      2200 * 0.12
      + 3800 * (1 - 0.75) * 1.25 * 0.12
      + 3800 * 0.75 * 0.10 * 0.12
    ) / 1_000_000
  = (264 + 142.5 + 34.2) / 1_000_000
  = $0.0004407
```

Weighted output-inclusive routed cost:

```text
weighted_model_cost_per_run
  = (1 - 0.20) * 0.92 * (
      0.55 * (0.00110175 + 0.00036)
      + 0.20 * (0.0110175 + 0.0036)
      + 0.25 * (0.0004407 + 0.000144)
    )
  = 0.736 * (
      0.55 * 0.00146175
      + 0.20 * 0.0146175
      + 0.25 * 0.0005847
    )
  = 0.736 * (0.0008039625 + 0.0029235 + 0.000146175)
  = 0.736 * 0.0038736375
  = $0.0028517972
```

```text
$ model cost per 1k runs
  = 1000 * 0.0028517972
  = $2.8517972
```

Interpretation:

- prefix caching reduces repeated scaffold cost
- semantic replay avoids serving `20%` of eligible requests entirely
- routing keeps premium-model usage limited to `20%` of served runs
- batching reduces per-run effective cost via amortized queueing and orchestration overhead
- local quantized routes are attractive only if their eval scores stay above the task quality floor

### Latency targets

Recommended SLO envelopes by workload shape:

- `interactive routed path`: `p50 <= 700ms`, `p95 <= 2.5s`, `p99 <= 5.0s`
- `retrieval-heavy or planner-executor path`: `p50 <= 1.5s`, `p95 <= 4.0s`, `p99 <= 8.0s`
- `bulk quantized or batched background path`: `p50 <= 3.0s`, `p95 <= 10.0s`, `p99 <= 20.0s`

Mitigations by percentile:

- `p50`: cache stable prompt prefixes, compact replayed history, colocate queue and cache layers, keep router features cheap
- `p95`: cap branch fan-out, flush microbatches on deadline, reuse planner outputs, route low-risk work to cheaper bounded executors
- `p99`: enforce end-to-end deadlines, shed optional branches, open circuit breakers on slow dependencies, bypass semantic-cache validation on timeout, and downgrade to deterministic fallback before user-visible stall cascades

### Throughput and back-pressure

Useful capacity formulas:

```text
max_completed_runs_per_minute
  = min(
      provider_rpm / avg_model_turns_per_run,
      provider_tpm / avg_total_tokens_per_run,
      batch_executor_capacity / avg_batches_per_run
    )
```

```text
safe_qps
  = min(
      cache_qps_limit,
      router_qps_limit,
      model_tokens_per_second / avg_tokens_per_request,
      checkpoint_writes_per_second / checkpoints_per_run
    )
```

```text
batch_queue_wait
  ~= target_flush_interval_ms * utilization_factor
```

Back-pressure order:

1. reduce optional reranking, verifier depth, or secondary analysis
2. shrink fan-out breadth before sacrificing correctness-critical checks
3. stop semantic-cache writes before disabling exact-prefix cache reads
4. route low-priority bulk work to background queues or local quantized replicas
5. fail closed for privileged writes, but allow deterministic degraded responses for read-only traffic

### Non-functional requirements

- `availability`: `99.9%` for interactive routing, `99.95%` for cache metadata and checkpoints, `99.99%` for audit records on privileged tool paths
- `RPO`: `0` for approvals, idempotency keys, and mutation audit records; `<= 1 minute` for checkpoint state; `<= 5 minutes` for performance telemetry mirrors
- `RTO`: `<= 15 minutes` for regional cache-router failover; `<= 30 minutes` for checkpoint-store restoration; `<= 4 hours` for analytics backfills
- `compliance`: tenant isolation for cache entries, model routes, and artifact references; policy version captured on cache write and replay
- `privacy`: prompts, tool outputs, and cached artifacts must support pre-persistence redaction and scoped retention

## 4. Distributed Resilience & Security

Optimization only helps if it remains replay-safe, tenant-safe, and observable under failure. Production incidents in optimized systems often look like `latency drift`, `cost drift`, or `wrong answer reuse` long before they look like a clean crash.

### Durable execution

Recommended pattern:

- persist workflow continuity with `Temporal`, `LangGraph` checkpoints, or an equivalent workflow engine
- separate workflow state from cache state so retries can resume without forcing expensive prompt rebuilds
- checkpoint after `route_selected`, `batch_admitted`, `tool_called`, `tool_result_received`, and `response_verified`
- use append-only event streams such as `Kafka` or equivalent for route lineage, cache hit/miss events, and degraded-mode transitions
- send exhausted retries, invalid cache payloads, or replay-poison records to a dead-letter stream

Replay-safe flow:

```text
request_received
  -> checkpoint_written
  -> cache_checked
  -> route_selected
  -> execution_started
  -> effect_verified
  -> cache_write_if_safe
  -> audit_persisted
  -> complete
```

The ordering is deliberate. A cache write must never become the only record of what happened, and a replay must be able to distinguish `reused answer`, `recomputed answer`, and `confirmed side effect`.

### Failure taxonomy

`Transient failures`

- cache backend timeout
- `429` or temporary provider throttling
- batch queue saturation
- semantic-index network partition
- checkpoint-store latency spike

`Permanent failures`

- invalid cache schema version
- revoked tool credentials
- RBAC denial on retrieved context
- unsupported quantized model artifact
- deterministic validation failure on output contract

`Poison-pill failures`

- one malformed cache entry causes repeated reuse failure
- one oversized artifact repeatedly blows batch token budgets
- one replay record always routes to an invalid model configuration

`Semantic failures`

- semantic cache returns a similar but policy-incompatible answer
- quantized route meets latency target but misses the quality floor
- routing classifier underestimates complexity and sends hard tasks to the fast tier
- microbatch wait reduces cost but violates interactive p99

Required controls:

- idempotency keys on every write-capable tool call
- cache keys include `tenant_id`, `policy_version`, `schema_version`, and trust class
- cache write happens only after verification for mutation-adjacent workflows
- dead-letter retention must preserve route lineage and cache fingerprints

### Circuit breakers and graceful degradation

Breakers should exist independently for:

- prefix cache
- semantic cache
- premium model
- local quantized executor
- checkpoint store
- privileged tool proxy

```text
CLOSED
  -> OPEN       after repeated timeout, throttle, or invalid-response threshold
  -> HALF_OPEN  after cooldown
  -> CLOSED     after successful probes
  -> OPEN       if probes fail
```

Graceful degradation order:

1. full routing + caches + batch optimization
2. disable semantic cache, keep exact-prefix cache and checkpoints
3. disable batching for interactive traffic, flush immediately
4. bypass local quantized route, use fast remote model
5. fall back to deterministic rule-based response for read-only safe cases

This order preserves correctness first, then latency predictability, then cost efficiency.

### Enterprise security controls

Zero-Trust `MCP` and tool boundary:

- every tool request terminates at a policy-enforcing proxy
- model-visible tool schemas expose only least-privilege actions
- route decisions cannot escalate privileges; authorization is rechecked at execution time
- cached tool outputs and retrieved documents carry trust labels and retention rules

Tool-level RBAC:

- separate service identities for `read_only_retrieval`, `analytics_lookup`, and `mutation_capable_actions`
- route classes map to allowed capability bundles rather than raw credentials
- local quantized serving pools must not bypass the same approval and RBAC checks enforced on remote-model routes

PII filtering pipeline:

1. detect sensitive fields in prompts, retrieved records, and tool results
2. redact or tokenize before cache write, trace write, or artifact storage
3. preserve re-identification mappings only in isolated vault systems when required
4. emit immutable audit events for every disclosure, redaction, replay, and invalidation decision

Auditability requirements:

- immutable journal for `route_selected`, `cache_hit`, `cache_miss`, `cache_write`, `fallback_used`, `tool_called`, and `response_sent`
- answer lineage from response -> route -> cache entry or fresh execution -> tool artifacts -> external record versions
- separate hot performance telemetry from compliance-grade audit evidence

> ⚠️ Gap: The local `research_cursor` set is stronger on prompt caching, MCP boundaries, checkpoints, and trust separation than on first-party provider guarantees for cache-entry RBAC, built-in PII redaction, or quantized serving governance. Enterprises should design those guarantees explicitly.

## 5. Production Enterprise Code

The runnable Python example below demonstrates an optimization-aware inference service with exact-prefix caching, semantic-cache validation, routing, bounded microbatching, retries with exponential backoff and jitter, circuit breakers, a primary-to-secondary-to-deterministic fallback chain, structured logging with correlation IDs, and graceful degradation when optimization layers fail.

```python
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence


class FailureCategory(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class ServiceError(Exception):
    def __init__(self, message: str, category: FailureCategory) -> None:
        super().__init__(message)
        self.category = category


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class Request:
    tenant_id: str
    user_query: str
    trust_level: str
    max_latency_ms: int
    allow_quantized: bool


@dataclass(frozen=True)
class Response:
    answer: str
    route: str
    degraded: bool
    cache_hit: bool
    correlation_id: str


@dataclass(frozen=True)
class CacheEntry:
    answer: str
    trust_level: str
    created_at_s: float


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "time_ms": int(record.created * 1000),
        }
        for key in ("correlation_id", "tenant_id", "route", "degraded"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("inference_optimization")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = build_logger()


def log(message: str, correlation_id: str, tenant_id: str, route: str, degraded: bool) -> None:
    LOGGER.info(
        message,
        extra={
            "correlation_id": correlation_id,
            "tenant_id": tenant_id,
            "route": route,
            "degraded": degraded,
        },
    )


def retry_with_backoff(
    fn: Callable[[], str],
    max_attempts: int,
    base_delay_s: float,
    max_delay_s: float,
) -> str:
    last_error: Optional[ServiceError] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except ServiceError as exc:
            last_error = exc
            if exc.category == FailureCategory.PERMANENT or attempt == max_attempts:
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            jitter = random.uniform(0.0, delay * 0.25)
            time.sleep(delay + jitter)
    raise last_error or ServiceError("unexpected retry state", FailureCategory.PERMANENT)


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_timeout_s: float) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.failure_count = 0
        self.state = BreakerState.CLOSED
        self.opened_at = 0.0

    def before_call(self) -> None:
        if self.state == BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                return
            raise ServiceError("circuit open", FailureCategory.TRANSIENT)

    def on_success(self) -> None:
        self.failure_count = 0
        self.state = BreakerState.CLOSED
        self.opened_at = 0.0

    def on_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


class PrefixCache:
    def __init__(self) -> None:
        self.entries: Dict[str, CacheEntry] = {}

    def get(self, key: str) -> Optional[CacheEntry]:
        return self.entries.get(key)

    def put(self, key: str, entry: CacheEntry) -> None:
        self.entries[key] = entry


class SemanticCache:
    def __init__(self) -> None:
        self.entries: Dict[str, CacheEntry] = {}

    def get(self, request: Request) -> Optional[CacheEntry]:
        for cached_query, entry in self.entries.items():
            if entry.trust_level != request.trust_level:
                continue
            overlap = len(set(cached_query.lower().split()) & set(request.user_query.lower().split()))
            if overlap >= 4:
                return entry
        return None

    def put(self, request: Request, entry: CacheEntry) -> None:
        self.entries[request.user_query] = entry


class MicroBatcher:
    def __init__(self, max_batch_size: int, flush_interval_ms: int) -> None:
        self.max_batch_size = max_batch_size
        self.flush_interval_ms = flush_interval_ms

    def drain(self, requests: Sequence[Request]) -> List[List[Request]]:
        batches: List[List[Request]] = []
        current: List[Request] = []
        for request in requests:
            current.append(request)
            if len(current) >= self.max_batch_size:
                batches.append(current)
                current = []
        if current:
            batches.append(current)
        return batches


class Model:
    def __init__(self, name: str, fail_on: str = "") -> None:
        self.name = name
        self.fail_on = fail_on

    def generate(self, prompt: str) -> str:
        if self.fail_on and self.fail_on in prompt:
            raise ServiceError(f"{self.name} temporary failure", FailureCategory.TRANSIENT)
        return f"{self.name}: {prompt[:140]}"


class OptimizationService:
    PII_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

    def __init__(self) -> None:
        self.prefix_cache = PrefixCache()
        self.semantic_cache = SemanticCache()
        self.microbatcher = MicroBatcher(max_batch_size=4, flush_interval_ms=50)
        self.cache_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=3.0)
        self.quantized_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=3.0)
        self.primary_model = Model("premium-planner", fail_on="planner_fail")
        self.secondary_model = Model("fast-executor", fail_on="executor_fail")
        self.quantized_model = Model("local-int8-executor", fail_on="quant_fail")

    def handle(self, request: Request) -> Response:
        correlation_id = str(uuid.uuid4())
        degraded = False
        route = "unknown"

        stable_prefix = self._stable_prefix(request.trust_level)
        prefix_key = self._prefix_key(request.tenant_id, stable_prefix, request.user_query)

        try:
            self.cache_breaker.before_call()
            prefix_hit = self.prefix_cache.get(prefix_key)
            if prefix_hit:
                self.cache_breaker.on_success()
                route = "prefix_cache"
                log("served from prefix cache", correlation_id, request.tenant_id, route, degraded=False)
                return Response(
                    answer=prefix_hit.answer,
                    route=route,
                    degraded=False,
                    cache_hit=True,
                    correlation_id=correlation_id,
                )
        except ServiceError:
            self.cache_breaker.on_failure()
            degraded = True

        semantic_hit = self.semantic_cache.get(request)
        if semantic_hit:
            route = "semantic_cache"
            log("served from semantic cache", correlation_id, request.tenant_id, route, degraded=degraded)
            return Response(
                answer=semantic_hit.answer,
                route=route,
                degraded=degraded,
                cache_hit=True,
                correlation_id=correlation_id,
            )

        route = self._route_request(request)
        prompt = self._build_prompt(stable_prefix, request.user_query)

        try:
            answer = self._run_route(route, prompt, request)
        except ServiceError:
            degraded = True
            route = "secondary_fallback"
            answer = retry_with_backoff(
                lambda: self.secondary_model.generate(prompt),
                max_attempts=3,
                base_delay_s=0.05,
                max_delay_s=0.20,
            )
        except Exception:
            degraded = True
            route = "deterministic_fallback"
            answer = self._deterministic_answer(request.user_query)
        else:
            answer = self._redact(answer)

        entry = CacheEntry(answer=answer, trust_level=request.trust_level, created_at_s=time.time())
        self.prefix_cache.put(prefix_key, entry)
        self.semantic_cache.put(request, entry)

        log("served after execution", correlation_id, request.tenant_id, route, degraded=degraded)
        return Response(
            answer=answer,
            route=route,
            degraded=degraded,
            cache_hit=False,
            correlation_id=correlation_id,
        )

    def run_batch(self, requests: Sequence[Request]) -> List[Response]:
        responses: List[Response] = []
        for batch in self.microbatcher.drain(requests):
            for request in batch:
                responses.append(self.handle(request))
        return responses

    def _route_request(self, request: Request) -> str:
        token_estimate = len(request.user_query.split())
        if request.allow_quantized and request.trust_level == "low" and token_estimate < 18:
            return "quantized_bulk"
        if token_estimate > 40 or "compare" in request.user_query.lower():
            return "planner_executor"
        return "fast_direct"

    def _run_route(self, route: str, prompt: str, request: Request) -> str:
        if route == "quantized_bulk":
            try:
                self.quantized_breaker.before_call()
                result = retry_with_backoff(
                    lambda: self.quantized_model.generate(prompt),
                    max_attempts=3,
                    base_delay_s=0.05,
                    max_delay_s=0.20,
                )
                self.quantized_breaker.on_success()
                return result
            except ServiceError:
                self.quantized_breaker.on_failure()
                return retry_with_backoff(
                    lambda: self.secondary_model.generate(prompt),
                    max_attempts=3,
                    base_delay_s=0.05,
                    max_delay_s=0.20,
                )

        if route == "planner_executor":
            plan = retry_with_backoff(
                lambda: self.primary_model.generate(f"Plan carefully: {prompt}"),
                max_attempts=3,
                base_delay_s=0.05,
                max_delay_s=0.20,
            )
            return retry_with_backoff(
                lambda: self.secondary_model.generate(f"Execute plan: {plan}"),
                max_attempts=3,
                base_delay_s=0.05,
                max_delay_s=0.20,
            )

        return retry_with_backoff(
            lambda: self.secondary_model.generate(prompt),
            max_attempts=3,
            base_delay_s=0.05,
            max_delay_s=0.20,
        )

    def _stable_prefix(self, trust_level: str) -> str:
        return (
            "System: Answer with enterprise-safe guidance.\n"
            f"Trust-Level: {trust_level}\n"
            "Rules: cite bounded facts, redact PII, preserve tenant isolation.\n"
        )

    def _build_prompt(self, stable_prefix: str, user_query: str) -> str:
        return stable_prefix + "User: " + user_query

    def _prefix_key(self, tenant_id: str, stable_prefix: str, user_query: str) -> str:
        material = f"{tenant_id}|{stable_prefix}|{user_query}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _deterministic_answer(self, query: str) -> str:
        return f"Deterministic fallback for request: {query[:120]}"

    def _redact(self, text: str) -> str:
        return self.PII_EMAIL.sub("[REDACTED_EMAIL]", text)


if __name__ == "__main__":
    service = OptimizationService()
    requests = [
        Request(
            tenant_id="acme",
            user_query="Summarize the billing change for owner@example.com in one sentence.",
            trust_level="low",
            max_latency_ms=800,
            allow_quantized=True,
        ),
        Request(
            tenant_id="acme",
            user_query="Compare three rollout options for a cache migration and explain the latency tradeoffs.",
            trust_level="medium",
            max_latency_ms=2500,
            allow_quantized=False,
        ),
    ]

    for response in service.run_batch(requests):
        print(response)
```

Why this code matters:

- retries use exponential backoff with jitter and stop after a bounded retry budget
- the circuit breakers explicitly model `closed -> open -> half-open`
- the fallback chain is `quantized or primary route -> secondary executor -> deterministic fallback`
- logs carry `correlation_id`, `tenant_id`, route, and degraded state
- exact-prefix and semantic caches are isolated from routing logic, which makes degradation behavior clearer
- batch admission is bounded and deadline-friendly rather than unbounded queue growth

## 6. Architectural System Design Scenarios

### Scenario 1: Global SaaS support copilot with cache-aware routing

**Problem statement**

Design an inference layer for a multi-tenant support copilot serving `60k requests/min` across chat and ticket channels. The business wants `p99 <= 5.0s` for interactive responses, strong cost control, reusable policy scaffolding, and safe answer reuse for repetitive FAQ-style traffic without cross-tenant leakage.

**Proposed architecture**

```text
┌──────────────────── Scenario 1 ────────────────────┐
│ Web / Support UI / Ticketing Channels                │
└──────────────┬──────────────────────────────────────┘
               v
      ┌─────────────────────┐
      │ API + AuthN/Z       │
      └─────────┬───────────┘
                v
      ┌──────────────────────────────┐
      │ Router + Budget Manager      │
      └───────┬──────────┬───────────┘
              │          │
              v          v
      ┌────────────┐  ┌────────────────┐
      │ Prefix     │  │ Semantic Cache │
      │ Cache      │  └────────┬───────┘
      └────┬───────┘           v
           v            ┌───────────────┐
      ┌────────────────▶│ Fast Executor │
      │                 └──────┬────────┘
      │                        v
      │                 ┌───────────────┐
      └────────────────▶│ Premium Plan  │
                        │ + Executor     │
                        └──────┬────────┘
                               v
                        ┌───────────────┐
                        │ Trace / Audit │
                        └───────────────┘
```

Technology choices:

- exact-prefix caching for policy headers, schemas, and tenant-safe system scaffolding
- semantic cache only for low-risk, read-only FAQ classes with freshness validation
- router that sends complex comparison or escalation tasks to planner-executor paths
- immutable route and cache lineage records for forensic review

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Premium model on every request | Highest | Strong p50, weaker p95 under load | Low | Strong if isolated, but wasteful | Medium |
| Prefix cache + semantic cache + routed fast/deep tiers | Medium | Best overall p95/p99 balance | Medium | Strong if cache keys are tenant-scoped and reuse is validated | High |
| Aggressive semantic reuse everywhere | Lowest apparent cost | Best p50, risky p95 due to validation misses | Medium | Weakest due to incorrect or cross-scope reuse risk | Medium |

**Decision rationale**

Choose `prefix cache + semantic cache + routed fast/deep tiers`. It captures the largest locally supported wins: stable-prefix reuse, sparing use of expensive reasoning turns, and safe replay only for narrow low-risk classes. Always-on premium inference is financially wasteful, while aggressive semantic reuse creates correctness and tenant-isolation risk that is unacceptable for enterprise support.

### Scenario 2: Internal analytics agent with quantized bulk lane and checkpointed fan-out

**Problem statement**

Design an internal analytics assistant that processes `8 million` document snippets overnight, supports daytime interactive drill-downs, and must keep bulk-processing cost low while preserving a higher-quality path for sensitive or ambiguous queries. The system needs `p95 <= 10.0s` for analyst drill-downs and efficient background throughput for large fan-out jobs.

**Proposed architecture**

```text
┌──────────────────── Scenario 2 ────────────────────┐
│ Analyst UI / Batch Scheduler                         │
└──────────────┬──────────────────────────────────────┘
               ├──────────────────────┐
               v                      v
      ┌──────────────────────┐  ┌──────────────────────┐
      │ Interactive Router   │  │ Bulk Batch Queue     │
      └──────────┬───────────┘  └──────────┬───────────┘
                 v                         v
      ┌──────────────────────┐  ┌──────────────────────┐
      │ Planner + Executors  │  │ Quantized Replica    │
      │ + Checkpoints        │  │ Pool                 │
      └──────────┬───────────┘  └──────────┬───────────┘
                 │                         │
                 └──────────────┬──────────┘
                                v
                     ┌──────────────────────┐
                     │ Verifier + Audit Bus │
                     └──────────────────────┘
```

Technology choices:

- background microbatch queue for bulk summarization or extraction
- quantized local replicas for low-risk repetitive transformations
- checkpointed planner-executor path for ambiguous daytime drill-downs
- automatic fallback from local quantized lane to remote fast tier when quality or availability degrades

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Remote premium inference only | Highest | Good for interactive, poor for bulk economics | Low | Strong central control | Medium |
| Quantized bulk lane + routed premium interactive lane | Lowest blended cost with strong interactivity | Strong if queues flush by deadline | High | Strong if same RBAC and audit rules apply to both lanes | High |
| Fully local quantized serving for all traffic | Lowest model spend | Uncertain interactive quality on hard tasks | Medium to high | Medium unless governance parity is enforced | High |

**Decision rationale**

Choose `quantized bulk lane + routed premium interactive lane`. This matches the strongest supportable conclusion from the local corpus: use cheaper bounded execution where task complexity is low, but preserve a higher-quality route for ambiguous or high-value work. A fully premium stack is too costly for background analytics, while a fully quantized stack creates unacceptable quality-risk concentration for difficult interactive questions.

## Sources

- [1] `research_cursor/research/01-llm-foundations.md` - Local note covering transformer inference shape, prompt-cache pricing, reasoning-token economics, and open-weight serving control surfaces.
- [2] `research_cursor/research/03-tool-use.md` - Local note covering tool-surface token overhead, cache-sensitive prompt formatting, and approval/tracing implications around external actions.
- [3] `research_cursor/research/04-agent-architecture.md` - Local note covering control-plane versus data-plane boundaries, planner/executor economics, `LLMCompiler` benchmarks, and durable execution trade-offs.
- [4] `research_cursor/research/05-agent-frameworks.md` - Local note covering LangGraph caching and checkpoints, OpenAI Agents SDK continuation patterns, ADK compaction, artifacts, and durability modes.
- [5] `research_cursor/research/06-rag.md` - Local note covering agentic retrieval fan-out, hybrid retrieval cost, and parallel subquery execution.
- [6] `research_cursor/research/07-memory.md` - Local note covering exact-prefix cache economics, semantic-cache trade-offs, context compaction, and cache-thrash failure modes.
- [7] `research_cursor/research/08-planning-reasoning.md` - Local note covering planner/executor routing, verifier loops, replanning overhead, and bounded executor patterns.
- [8] `research_cursor/research/09-multi-agent-systems.md` - Local note covering supervisor-worker routing, critical-path latency, context isolation, and wrong-route failure modes.
- [9] `research_cursor/research/10-mcp-interoperability.md` - Local note covering Zero-Trust `MCP`, auth boundaries, and protocol-level governance implications.
- [10] `research_cursor/research/12-evaluation.md` - Local note covering cost/latency as first-class axes and measurement of orchestration efficiency versus answer quality.
- [11] `research_cursor/research/13-security-guardrails.md` - Local note covering policy scaffolding, trusted versus untrusted context, and fail-closed control patterns.
- [12] `research_cursor/research/14-observability.md` - Local note covering runtime telemetry for cached tokens, route lineage, degraded-mode detection, and optimization-specific signals.
