# Memory — Short-term, Long-term, Semantic, Episodic, Memory Retrieval

## 1. System Topology & Data Flow

Production memory is a layered subsystem, not a single transcript bucket. The clean enterprise split is:

- `short-term / working memory`: the hot tail of recent turns, tool results, and workflow-local state
- `episodic memory`: thread or run history with checkpoint and resume semantics
- `semantic memory`: durable cross-session facts such as preferences, entitlements, and policies
- `retrieval memory`: large mutable corpora queried at read time
- `cache memory`: exact-prefix or semantic reuse layers that reduce repeated inference cost

```text
┌────────────────────────────── Control Plane ───────────────────────────────┐
│  API Gateway -> AuthN/Z -> Memory Policy Router -> Orchestrator            │
│       │             │                 │                  │                  │
│       │             │                 │                  ├─ session recall  │
│       │             │                 │                  ├─ semantic recall │
│       │             │                 │                  ├─ episodic replay │
│       │             │                 │                  └─ retrieval fanout│
│       └────────────────────────────> Correlation ID / Tenant / Deadline     │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  v
┌──────────────────────────────── Data Plane ────────────────────────────────┐
│  Session Store -> Compactor -> Prompt Builder -> Model Runtime             │
│       │              │                ▲                 │                   │
│       │              │                │                 ├─ exact cache      │
│       │              │                │                 ├─ semantic cache   │
│       │              │                │                 └─ fallback models  │
│       v              v                │                                     │
│  Episodic Checkpoints   Semantic Store │                                     │
│       │                 user/org facts │                                     │
│       └──────────────┐                │                                     │
│                      v                │                                     │
│                Retrieval Gateway -> Hybrid Search / Graph / Rerank         │
└──────────────────────────────────────────────────────────────────────────────┘
          │                         │                         │
          v                         v                         v
┌──────────────────┐     ┌────────────────────┐    ┌─────────────────────────┐
│ Persistence      │     │ Tool Proxies       │    │ Telemetry / Audit       │
│ checkpoints      │     │ MCP gateway        │    │ logs / traces / metrics │
│ profiles         │     │ RBAC / redaction   │    │ memory write ledger     │
│ vector / graph   │     │ approval hooks     │    │ retention evidence      │
└──────────────────┘     └────────────────────┘    └─────────────────────────┘
```

### Request-flow narrative

1. `API Gateway` authenticates the tenant, assigns a `correlation_id`, and starts a request deadline.
2. `Memory Policy Router` decides which memory tiers are allowed for the request. A read-only chat turn might use session plus semantic memory, while a regulated workflow may require permission-aware retrieval and an auditable episodic checkpoint.
3. `Session Store` loads the recent raw tail. If the thread is large, `Compactor` summarizes older turns and preserves only the recent event window needed for coherence.
4. `Semantic Store` loads validated durable facts such as language preference, account plan, escalation rules, or policy exceptions.
5. `Retrieval Gateway` runs retrieval only when the question depends on mutable corpus knowledge rather than user profile facts. Hybrid or graph retrieval stays out of the prompt unless the router explicitly authorizes it.
6. `Prompt Builder` constructs a bounded context window from the hot tail, semantic facts, and retrieved evidence, while preserving provenance and token budgets.
7. `Model Runtime` checks cache memory first. Stable prefixes use exact cache reads; near-duplicate high-volume workloads may additionally consult a semantic cache.
8. The answer and any approved memory writes flow through `Telemetry / Audit`, which records what memory was read, what facts were proposed for persistence, what was redacted, and what degraded during the run.

The architectural invariant is that each memory tier has a different correctness contract. Working memory optimizes coherence, episodic memory optimizes replayability, semantic memory optimizes reuse, retrieval memory optimizes freshness, and cache memory optimizes cost and latency. Treating them as one store usually weakens all five properties at once.

## 2. Core Mechanics & Algorithms

### Memory taxonomy as state scopes

The most useful production distinction is not "short-term versus long-term" alone, but `scope + mutability + trust`:

- `working memory`: request-thread scope, high mutation rate, medium trust, low durability requirement
- `episodic memory`: workflow scope, append-oriented, high audit value, replay-safe durability requirement
- `semantic memory`: user/org scope, lower mutation rate, high reuse impact, highest validation requirement
- `retrieval memory`: corpus scope, externally refreshed, freshness-sensitive, authorization-sensitive
- `cache memory`: implementation scope, best-effort durability, low correctness authority

That scope split explains why a single giant transcript is a weak design. It mixes high-churn conversational fragments with low-churn durable facts, causing token bloat, stale-fact reuse, and poor recovery semantics.

### Memory as a bounded state machine

```text
ACCEPT
  -> LOAD_SESSION_TAIL
  -> LOAD_SEMANTIC_FACTS
  -> OPTIONAL_RETRIEVE_CORPUS
  -> BUILD_CONTEXT
  -> GENERATE
  -> EVALUATE
     -> APPEND_EPISODE          if the turn must be replayable
     -> PROPOSE_SEMANTIC_WRITE  if a durable fact candidate was derived
     -> COMPACT_SESSION         if the thread crossed a token threshold
     -> COMPLETE                if response and writes are committed
     -> DEGRADE                 if retrieval/cache/model dependencies are unhealthy
```

Key invariants:

- Every run has stable `tenant_id`, `thread_id`, `run_id`, and monotonic `step_index`.
- Every side-effecting memory write carries an idempotency key such as `(tenant_id, subject_id, source_event_id, fact_hash)`.
- Every semantic memory write must carry provenance: who asserted it, from what source, at what time, under what policy.
- Every retrieval candidate must stay attached to document IDs and permission context; otherwise later audit cannot prove why the model saw it.
- Session compaction must be monotonic: compaction may shorten history, but it must not change already-recorded durable facts or workflow checkpoints.

### Retrieval algorithms by memory type

#### Working-memory compaction

Short-term memory quality degrades before the context window is technically full. A practical compaction policy is:

```text
if session_tokens <= raw_tail_budget:
    keep raw turns
else:
    summarize oldest turns
    keep recent raw tail
    carry forward unresolved entities, tasks, and approvals
```

Approximate prompt budget:

```text
working_memory_prompt
  = raw_tail_tokens
  + summary_tokens
  + semantic_fact_tokens
  + retrieved_memory_tokens
  + tool_schema_tokens
```

The value of compaction is not only lower cost. It also mitigates the "lost in the middle" problem by keeping high-salience unresolved state near the end of the effective prompt.

#### Exact-prefix cache memory

Exact caches are deterministic but brittle. They hit only when the prefix is byte-for-byte stable at an eligible breakpoint. The main operational rule is to keep the shared prefix canonical:

- stable ordering of tool schemas
- stable serialization of system policies
- stable whitespace and field names
- explicit cacheable prefix boundaries

Break-even for the `1.25x` write and `0.1x` read model documented in the source set:

```text
cache_cost(use_count)
  = 1.25 * prefix_tokens * P_in
  + (use_count - 1) * 0.10 * prefix_tokens * P_in

no_cache_cost(use_count)
  = use_count * prefix_tokens * P_in
```

For any `use_count >= 2`, cached reuse is cheaper than replaying the same prefix in full.

#### Semantic and episodic retrieval

Semantic memory retrieval is usually top-`k` nearest-neighbor or hybrid search over validated fact objects. Episodic retrieval is often simpler: filter by thread or run, then rank by recency, step index, and optionally semantic similarity to the current query.

Useful heuristic:

```text
retrieval_memory_token_load
  = subqueries * retrieved_candidates * avg_tokens_per_candidate
```

Practical complexity:

- episodic recall by thread or run key: `O(log N + k)` on indexed metadata plus scoring
- semantic memory ANN lookup: sublinear average-case search with recall-quality trade-offs
- hybrid retrieval with reranking: first-stage retrieval plus `O(k)` second-stage scoring over retained candidates

#### Semantic cache memory

Semantic caches increase hit rate by tolerating paraphrase, but they weaken the meaning of "cache hit." The control problem becomes threshold tuning:

```text
reuse_if similarity(query, cached_query) >= threshold
```

If the threshold is too high, the cache acts like an expensive miss engine. If it is too low, wrong answers are reused because the surface wording is similar while hidden constraints differ.

### Correctness and convergence constraints

- Working memory converges only when raw-tail length, summary size, and tool schema budgets are capped.
- Episodic memory converges only when checkpoint boundaries are explicit and replay does not repeat already-successful side effects.
- Semantic memory converges only when writes are deduplicated, versioned, and expire or downgrade when evidence becomes stale.
- Retrieval memory converges only when candidate counts are bounded and permission filters are applied before rerank and synthesis.
- Cache memory converges operationally only when hit-rate, write-rate, and stale-reuse metrics are monitored together.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: Public sources document cache ratios, thresholds, and retrieval token mechanics much better than they document stable end-to-end `p50/p95/p99` production latencies for memory-heavy agent systems. The percentile targets below are recommended internal SLO envelopes, not vendor guarantees.

### Cost formulas

Assumptions for the formulas below:

- `runs = 1000`
- `turns_per_run = 6`
- `stable_prefix_tokens = 3000`
- `fresh_turn_input_tokens = 500`
- `output_tokens = 250`
- `retrieval_subqueries = 3`
- `retrieved_candidates = 24`
- `avg_tokens_per_candidate = 350`
- `P_in`, `P_out`, `P_rerank`, and `P_search_req` are service-specific prices

#### Replay-heavy session memory

```text
$ per 1k runs =
1000 * (
  ((turns_per_run * (stable_prefix_tokens + fresh_turn_input_tokens)) / 1_000_000) * P_in +
  ((turns_per_run * output_tokens) / 1_000_000) * P_out
)
```

This is the simplest design and often the worst one economically. Every extra turn pays again for replaying old state.

#### Exact-prefix cache + compacted session memory

Using the `1.25x` cache-write and `0.1x` cache-read ratios documented for the source-set providers:

```text
$ per 1k runs =
1000 * (
  ((turns_per_run * fresh_turn_input_tokens) / 1_000_000) * P_in +
  (stable_prefix_tokens / 1_000_000) * (1.25 * P_in) +
  (((turns_per_run - 1) * stable_prefix_tokens) / 1_000_000) * (0.10 * P_in) +
  ((turns_per_run * output_tokens) / 1_000_000) * P_out
)
```

Compared with replay-heavy memory, this shifts cost from repeated input billing toward one write plus discounted reads. The savings improve as the reusable prefix grows and the per-run turn count rises.

#### Semantic or retrieval memory with reranking

```text
$ per 1k runs =
1000 * (
  ((turns_per_run * fresh_turn_input_tokens) / 1_000_000) * P_in +
  ((retrieval_subqueries * retrieved_candidates * avg_tokens_per_candidate) / 1_000_000) * P_rerank +
  (retrieval_subqueries * P_search_req) +
  ((turns_per_run * output_tokens) / 1_000_000) * P_out
)
```

This formula makes the dominant lever explicit: retrieval cost scales linearly with `subqueries`, `candidate count`, and `document length`, not only with final answer length.

#### Published worked example from the retrieval source set

Azure's cited agentic retrieval example assumes:

- `retrieval_subqueries = 3`
- `retrieved_candidates = 50`
- `avg_tokens_per_candidate = 500`
- published planning plus reranking total of `$4.32` for `2,000` retrievals

That yields:

```text
$ per 1k retrieval-heavy runs
  = $4.32 / 2
  = $2.16
```

That number is not a full application bill, but it is a useful retrieval-side benchmark because it shows how quickly multi-branch memory lookup can dominate cost once a single user request fans out.

### Latency targets

Recommended enterprise targets for a memory-augmented assistant:

- `session-only turn`: `p50 <= 700ms`, `p95 <= 1.8s`, `p99 <= 3.0s`
- `session + semantic memory turn`: `p50 <= 1.1s`, `p95 <= 2.8s`, `p99 <= 4.5s`
- `session + retrieval memory turn`: `p50 <= 1.8s`, `p95 <= 4.0s`, `p99 <= 6.5s`

Mitigations by percentile:

- `p50`: stable prompt prefixes, compaction before generation, warm HTTP pools, colocated memory services, cached profile reads
- `p95`: parallel retrieval branches, candidate caps, asynchronous semantic writes, per-branch deadlines, cached reranker warmup
- `p99`: admission control, shed semantic-cache lookups first, degrade to session-only or profile-only mode, skip secondary retrieval branches, return a safe partial answer instead of waiting for a collapsing dependency

### Throughput and back-pressure

The main scaling mistake is budgeting only on request rate. Memory systems saturate on write amplification and retrieved-token volume.

Useful planning formulas:

```text
memory_read_tokens_per_second
  = qps * (semantic_docs_per_request + episodic_docs_per_request) * avg_tokens_per_doc
```

```text
checkpoint_writes_per_second
  = qps * avg_turns_per_request * checkpoints_per_turn
```

```text
semantic_write_backlog_seconds
  = queued_semantic_writes / sustainable_semantic_writes_per_second
```

Back-pressure policy should be explicit and ordered:

1. Stop nonessential cache writes before rejecting cache reads.
2. Queue semantic writes through an outbox or Kafka topic instead of blocking the user-facing path.
3. Cap `top_k`, `subqueries`, and reranker candidates when memory-read tokens per second approach saturation.
4. Serve session plus existing semantic facts if retrieval is slow.
5. Reject new cross-tenant or policy-expensive recall requests before allowing core thread continuity to fail.

### Availability, RPO, RTO, and compliance

Memory tiers should not share one NFR target because they do different jobs:

- `session / episodic checkpoint store`: target `99.95%` availability, `RPO <= 5 minutes`, `RTO <= 15 minutes`; required for pause/resume continuity
- `semantic memory store`: target `99.99%` availability, `RPO <= 1 minute`, `RTO <= 30 minutes`; durable facts should not be silently lost or duplicated
- `retrieval index serving plane`: target `99.9%` availability, `RPO <= 15 minutes` for ingest lag, `RTO <= 60 minutes`; stale-but-authorized search is often preferable to total outage
- `cache memory`: best-effort only; no correctness-critical `RPO` objective, because misses must degrade cost and latency, not correctness

Compliance and governance controls:

- tenant-scoped encryption keys and row-level or document-level authorization
- retention and deletion policies for GDPR/CCPA data-subject requests
- region pinning for data residency
- immutable access and mutation logs for SOC 2 / ISO 27001 evidence
- stricter retention and review workflows when the deployment falls under HIPAA, PCI, or other sector-specific controls

The practical rule is simple: semantic memory behaves like customer data, episodic memory behaves like workflow evidence, and cache memory behaves like disposable optimization state. Their compliance posture should reflect that difference.

## 4. Distributed Resilience & Security

### Durable execution patterns

The resilient pattern is to split memory concerns across workflow, write, and retrieval channels:

- `Temporal` or equivalent durable workflow runtime owns episodic checkpoints, timers, and replay
- `Kafka` or transactional outbox owns asynchronous semantic-memory write propagation
- relational or document stores own validated semantic facts
- vector, hybrid, or graph indices own retrieval artifacts

Recommended write path:

```text
request
  -> validate candidate fact
  -> persist episode + outbox record atomically
  -> publish semantic write event
  -> policy/redaction service approves projection
  -> semantic store upserts fact version
  -> audit ledger records before/after hashes
```

This is stronger than writing directly from the model into long-term memory because it prevents "answer succeeded, memory write vanished" and "memory write succeeded, answer path crashed" split-brain failures.

### Failure taxonomy

#### Transient failures

- vector index timeout
- reranker saturation
- cache service unavailable
- temporary lock conflict on a session or profile row

Handling:

- exponential backoff with jitter
- circuit breaker protection
- bounded retry counts
- degrade to lower-cost or lower-precision memory paths

#### Permanent failures

- unauthorized retrieval request
- invalid semantic-memory schema
- retention policy violation
- corrupted or expired source reference for a durable fact

Handling:

- fail fast
- do not retry
- record audit evidence
- surface a deterministic user-visible explanation when appropriate

#### Poison-pill failures

These are repeated bad inputs that would keep failing if blindly replayed:

- malformed memory-write payloads from a producer bug
- prompt-injection strings attempting to upgrade low-trust content into semantic memory
- cross-tenant identifiers embedded in a retrieved snippet

Handling:

- move the offending event to a dead-letter queue
- stamp the source record with a `quarantined` state
- require operator or policy-engine review before replay

### Idempotency, locking, and replay

Replay-safe memory systems need more than retries:

- session updates for the same `thread_id` need optimistic versioning or row-level locking
- semantic writes need deterministic idempotency keys derived from source and normalized fact content
- retrieval calls should be treated as pure reads, but the decision to persist retrieved evidence or derived facts must be replay-safe
- checkpoint restore must load the last successful observation rather than reissuing an external action

An effective idempotency key for semantic memory is:

```text
semantic_write_key
  = hash(tenant_id, subject_id, fact_type, normalized_value, source_event_id)
```

That prevents duplicate durable facts when a workflow or queue consumer replays.

### Zero-Trust memory security

Memory is where low-trust data becomes high-leverage state, so the write path must be stricter than the read path.

Enterprise controls:

- `Zero-Trust MCP`: every tool or retrieval proxy authenticates each request independently and receives least-privilege credentials scoped to tenant, dataset, and action
- `Tool-level RBAC`: read profile, read corpus, write episode, and write semantic fact are separate permissions
- `Permission-aware retrieval`: authorization filters apply before retrieval fusion and before rerank, not only in UI post-processing
- `Instruction isolation`: untrusted retrieval content remains data, never promoted into privileged developer or policy instructions

### PII filtering and auditability

A production write pipeline for semantic memory should look like:

```text
detect -> classify -> redact/tokenize -> policy check -> persist approved projection -> audit
```

Important audit fields:

- `correlation_id`
- `tenant_id`
- `thread_id`
- `source_event_id`
- `memory_tier`
- `before_hash` and `after_hash`
- `policy_decision`
- `redaction_actions`
- `operator_or_model_identity`

This creates chain-of-custody for agent decisions. Without that ledger, semantic memory poisoning and stale-fact disputes are almost impossible to explain after the fact.

## 5. Production Enterprise Code

The runnable example below shows a memory runtime with:

- bounded session compaction
- separate semantic and retrieval memory
- retries with exponential backoff and jitter
- circuit breakers with `closed -> open -> half_open`
- fallback models `primary -> secondary -> deterministic`
- structured logging with correlation IDs
- graceful degradation when retrieval is unavailable

```python
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import random
import threading
import time
import uuid
from typing import Callable, Iterable, Sequence


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


@dataclass(frozen=True)
class Event:
    role: str
    text: str
    created_at_ms: int


@dataclass(frozen=True)
class MemoryFact:
    key: str
    value: str
    source: str
    updated_at_ms: int


@dataclass(frozen=True)
class Document:
    doc_id: str
    text: str
    score: float


@dataclass(frozen=True)
class Request:
    tenant_id: str
    user_id: str
    thread_id: str
    query: str


@dataclass(frozen=True)
class Response:
    text: str
    provider: str
    degraded: bool
    citations: tuple[str, ...]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "time_ms": int(record.created * 1000),
            "logger": record.name,
        }
        for key in (
            "correlation_id",
            "tenant_id",
            "thread_id",
            "event",
            "provider",
            "degraded",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("memory_runtime")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = build_logger()


def log(level: int, message: str, **extra: object) -> None:
    LOGGER.log(level, message, extra=extra)


def retry(
    operation: Callable[[], Sequence[Document]],
    *,
    max_attempts: int,
    initial_delay_s: float,
    max_delay_s: float,
) -> Sequence[Document]:
    delay_s = initial_delay_s
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except TransientError:
            if attempt == max_attempts:
                raise
            jitter = 1.0 + random.uniform(-0.25, 0.25)
            time.sleep(min(delay_s, max_delay_s) * jitter)
            delay_s = min(delay_s * 2.0, max_delay_s)
    raise RuntimeError("unreachable")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int
    open_interval_s: float
    half_open_max_calls: int = 1
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    opened_at: float = 0.0
    half_open_calls: int = 0

    def before_call(self) -> None:
        now = time.monotonic()
        if self.state == CircuitState.OPEN:
            if now - self.opened_at >= self.open_interval_s:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
            else:
                raise TransientError("circuit open")
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.half_open_max_calls:
                raise TransientError("half-open probe budget exhausted")
            self.half_open_calls += 1

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_calls = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()
            self.half_open_calls = 0


class SessionStore:
    def __init__(self, raw_tail_limit: int = 4) -> None:
        self.raw_tail_limit = raw_tail_limit
        self._events: dict[str, list[Event]] = defaultdict(list)
        self._summary: dict[str, str] = {}
        self._lock = threading.Lock()

    def append(self, thread_id: str, role: str, text: str) -> None:
        with self._lock:
            self._events[thread_id].append(
                Event(role=role, text=text, created_at_ms=int(time.time() * 1000))
            )
            self._compact_locked(thread_id)

    def load_context(self, thread_id: str) -> str:
        with self._lock:
            summary = self._summary.get(thread_id, "")
            tail = self._events.get(thread_id, [])
        parts = []
        if summary:
            parts.append(f"summary: {summary}")
        parts.extend(f"{event.role}: {event.text}" for event in tail)
        return "\n".join(parts)

    def _compact_locked(self, thread_id: str) -> None:
        events = self._events[thread_id]
        if len(events) <= self.raw_tail_limit:
            return
        older = events[:-self.raw_tail_limit]
        tail = events[-self.raw_tail_limit :]
        prior_summary = self._summary.get(thread_id, "")
        older_summary = " | ".join(f"{event.role}:{event.text[:48]}" for event in older)
        merged = " ".join(part for part in (prior_summary, older_summary) if part)
        self._summary[thread_id] = merged[:600]
        self._events[thread_id] = tail


class SemanticStore:
    def __init__(self) -> None:
        self._facts: dict[str, list[MemoryFact]] = defaultdict(list)

    def upsert_fact(self, user_id: str, key: str, value: str, source: str) -> None:
        now_ms = int(time.time() * 1000)
        facts = [fact for fact in self._facts[user_id] if fact.key != key]
        facts.append(MemoryFact(key=key, value=value, source=source, updated_at_ms=now_ms))
        self._facts[user_id] = facts

    def lookup(self, user_id: str) -> Sequence[MemoryFact]:
        return tuple(sorted(self._facts[user_id], key=lambda fact: fact.key))


class RetrievalIndex:
    def __init__(self, documents: Iterable[Document]) -> None:
        self.documents = list(documents)

    def search(self, query: str, top_k: int) -> Sequence[Document]:
        if "retrieval_fail" in query:
            raise TransientError("retrieval backend timeout")
        query_terms = {term.lower() for term in query.split()}
        scored = []
        for doc in self.documents:
            overlap = len(query_terms.intersection(doc.text.lower().split()))
            if overlap > 0:
                scored.append(Document(doc.doc_id, doc.text, float(overlap)))
        scored.sort(key=lambda doc: doc.score, reverse=True)
        return tuple(scored[:top_k])


class ModelEndpoint:
    def __init__(
        self,
        *,
        name: str,
        generate_fn: Callable[[str], str],
        breaker: CircuitBreaker,
    ) -> None:
        self.name = name
        self.generate_fn = generate_fn
        self.breaker = breaker

    def generate(self, prompt: str) -> str:
        self.breaker.before_call()
        try:
            text = self.generate_fn(prompt)
            self.breaker.record_success()
            return text
        except PermanentError:
            self.breaker.record_failure()
            raise
        except Exception as exc:
            self.breaker.record_failure()
            raise TransientError(str(exc)) from exc


def deterministic_fallback(request: Request, degraded_reason: str) -> str:
    return (
        "The memory platform is partially degraded. "
        f"Request for user {request.user_id} was handled in safe mode because: "
        f"{degraded_reason}. Please retry for a fully grounded answer."
    )


class MemoryRuntime:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        semantic_store: SemanticStore,
        retrieval_index: RetrievalIndex,
        models: Sequence[ModelEndpoint],
    ) -> None:
        self.session_store = session_store
        self.semantic_store = semantic_store
        self.retrieval_index = retrieval_index
        self.models = list(models)
        self.retrieval_breaker = CircuitBreaker(failure_threshold=2, open_interval_s=5.0)

    def answer(self, request: Request) -> Response:
        correlation_id = str(uuid.uuid4())
        log(
            logging.INFO,
            "request started",
            correlation_id=correlation_id,
            tenant_id=request.tenant_id,
            thread_id=request.thread_id,
            event="request_start",
        )

        session_context = self.session_store.load_context(request.thread_id)
        semantic_facts = self.semantic_store.lookup(request.user_id)
        degraded = False
        degraded_reason = ""
        docs: Sequence[Document] = ()

        if self.retrieval_breaker.state != CircuitState.OPEN:
            try:
                docs = retry(
                    lambda: self._search_with_breaker(request.query),
                    max_attempts=2,
                    initial_delay_s=0.05,
                    max_delay_s=0.20,
                )
            except TransientError:
                degraded = True
                degraded_reason = "retrieval unavailable"
                log(
                    logging.WARNING,
                    "retrieval degraded; serving without corpus recall",
                    correlation_id=correlation_id,
                    tenant_id=request.tenant_id,
                    thread_id=request.thread_id,
                    event="retrieval_degraded",
                    degraded=True,
                )
        else:
            degraded = True
            degraded_reason = "retrieval circuit open"

        prompt = self._build_prompt(request, session_context, semantic_facts, docs)
        answer_text, provider, provider_degraded = self._generate_with_fallback(
            prompt=prompt,
            request=request,
            degraded_reason=degraded_reason or "model unavailable",
            correlation_id=correlation_id,
        )

        self.session_store.append(request.thread_id, "user", request.query)
        self.session_store.append(request.thread_id, "assistant", answer_text)

        if "preference:" in request.query.lower():
            key, value = self._extract_preference(request.query)
            self.semantic_store.upsert_fact(
                request.user_id,
                key=key,
                value=value,
                source=f"thread:{request.thread_id}",
            )

        degraded = degraded or provider_degraded
        citations = tuple(doc.doc_id for doc in docs[:3])

        log(
            logging.INFO,
            "request completed",
            correlation_id=correlation_id,
            tenant_id=request.tenant_id,
            thread_id=request.thread_id,
            event="request_complete",
            provider=provider,
            degraded=degraded,
        )
        return Response(
            text=answer_text,
            provider=provider,
            degraded=degraded,
            citations=citations,
        )

    def _search_with_breaker(self, query: str) -> Sequence[Document]:
        self.retrieval_breaker.before_call()
        try:
            docs = self.retrieval_index.search(query, top_k=3)
            self.retrieval_breaker.record_success()
            return docs
        except Exception as exc:
            self.retrieval_breaker.record_failure()
            raise TransientError(str(exc)) from exc

    def _generate_with_fallback(
        self,
        *,
        prompt: str,
        request: Request,
        degraded_reason: str,
        correlation_id: str,
    ) -> tuple[str, str, bool]:
        for index, endpoint in enumerate(self.models):
            try:
                text = endpoint.generate(prompt)
                return text, endpoint.name, index > 0
            except (TransientError, PermanentError):
                log(
                    logging.WARNING,
                    "model failed; trying fallback",
                    correlation_id=correlation_id,
                    tenant_id=request.tenant_id,
                    thread_id=request.thread_id,
                    event="model_fallback",
                    provider=endpoint.name,
                    degraded=True,
                )
                continue
        return deterministic_fallback(request, degraded_reason), "deterministic_fallback", True

    def _build_prompt(
        self,
        request: Request,
        session_context: str,
        semantic_facts: Sequence[MemoryFact],
        docs: Sequence[Document],
    ) -> str:
        fact_lines = [f"{fact.key}={fact.value}" for fact in semantic_facts]
        doc_lines = [f"{doc.doc_id}: {doc.text}" for doc in docs]
        return "\n".join(
            [
                "system: answer with grounded memory only",
                f"tenant: {request.tenant_id}",
                f"user: {request.user_id}",
                f"query: {request.query}",
                "semantic_facts:",
                *fact_lines,
                "session_context:",
                session_context,
                "retrieved_docs:",
                *doc_lines,
            ]
        )

    def _extract_preference(self, query: str) -> tuple[str, str]:
        _, value = query.split(":", 1)
        return "user_preference", value.strip()


def flaky_primary(prompt: str) -> str:
    if "primary_fail" in prompt:
        raise TimeoutError("primary model timeout")
    if random.random() < 0.35:
        raise TimeoutError("primary transient timeout")
    return f"primary answer grounded on memory for: {prompt.splitlines()[3]}"


def stable_secondary(prompt: str) -> str:
    if "secondary_fail" in prompt:
        raise TimeoutError("secondary model timeout")
    return f"secondary answer grounded on memory for: {prompt.splitlines()[3]}"


def main() -> None:
    random.seed(7)

    session_store = SessionStore(raw_tail_limit=4)
    semantic_store = SemanticStore()
    semantic_store.upsert_fact("user-7", "plan", "enterprise", "crm")
    semantic_store.upsert_fact("user-7", "locale", "en-US", "profile")

    retrieval_index = RetrievalIndex(
        [
            Document("doc-1", "Enterprise plan includes SSO and audit logs.", 1.0),
            Document("doc-2", "Support hours for enterprise are twenty four seven.", 1.0),
            Document("doc-3", "Billing exports are retained for seven years.", 1.0),
        ]
    )

    runtime = MemoryRuntime(
        session_store=session_store,
        semantic_store=semantic_store,
        retrieval_index=retrieval_index,
        models=[
            ModelEndpoint(
                name="primary-model",
                generate_fn=flaky_primary,
                breaker=CircuitBreaker(failure_threshold=2, open_interval_s=5.0),
            ),
            ModelEndpoint(
                name="secondary-model",
                generate_fn=stable_secondary,
                breaker=CircuitBreaker(failure_threshold=2, open_interval_s=5.0),
            ),
        ],
    )

    request = Request(
        tenant_id="tenant-a",
        user_id="user-7",
        thread_id="thread-42",
        query="What support and audit features come with the enterprise plan?",
    )
    response = runtime.answer(request)

    print(
        json.dumps(
            {
                "provider": response.provider,
                "degraded": response.degraded,
                "citations": list(response.citations),
                "text": response.text,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
```

Production takeaways from the code:

- Session compaction limits prompt growth without discarding the active raw tail.
- Retrieval is isolated behind retries and a breaker, so corpus outages do not automatically become user-facing hard failures.
- Semantic memory writes are explicit and attributable to a source thread.
- Model fallback order is deterministic and auditable.
- Structured logs preserve `correlation_id`, `tenant_id`, and `thread_id` for every request.

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-tenant SaaS copilot with repeat users and stable product policy

**Problem statement**:

Design a customer-facing SaaS copilot serving `30,000` requests/minute across many tenants. Users return frequently, product policy prefixes are stable, and the system must preserve tenant isolation while keeping `p99 <= 4.5s` for memory-augmented turns.

**Proposed architecture**:

```text
┌──────────────┐   ┌──────────────┐   ┌────────────────────┐
│ Web / API    │-> │ Tenant Edge  │-> │ Memory Router      │
└──────────────┘   └──────────────┘   │ session/semantic   │
                                      │ cache/retrieval    │
                                      └─────────┬──────────┘
                                                v
                         ┌────────────────────────────────────────────┐
                         │ Session Store + Exact Prefix Cache         │
                         │ compacted tail / stable policy prefix      │
                         └─────────┬──────────────────────────────────┘
                                   v
                         ┌────────────────────────────────────────────┐
                         │ Semantic Profile Store                     │
                         │ plan / locale / entitlement / preferences  │
                         └─────────┬──────────────────────────────────┘
                                   v
                         ┌────────────────────────────────────────────┐
                         │ Answer Runtime + Audit Ledger              │
                         └────────────────────────────────────────────┘
```

Technology choices:

- session memory with compaction for the active conversation
- exact-prefix cache for stable policy, schema, and tool preamble
- validated semantic profile store for durable user and tenant facts
- optional retrieval path only for mutable documentation, not for profile data

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Transcript replay only | High | Weak once threads lengthen | Low | Medium | Medium |
| Compacted session + semantic store + exact cache | Medium | Strong | Medium | High | High |
| Semantic cache as primary memory layer | Medium-Low | Strong on hits, weak on false positives | Medium | Medium | High |

**Decision rationale**:

Choose `compacted session + semantic store + exact cache`. Transcript replay wastes tokens and degrades long threads, while a semantic cache is an optimization layer, not a source of truth for durable user facts. The recommended design keeps correctness-critical memory in explicit session and semantic stores, then uses exact cache reads to reduce repeated cost on stable enterprise prompts.

### Scenario 2: Regulated claims assistant with cross-session facts and auditable workflow history

**Problem statement**:

Design a claims assistant for an insurer handling `100,000` claims/day. The system must remember claimant preferences across sessions, preserve episode history for regulator review, use retrieval memory for policy manuals and claim evidence, and maintain `RPO <= 1 minute` for durable facts with `RTO <= 30 minutes`.

**Proposed architecture**:

```text
┌──────────────┐   ┌─────────────────┐   ┌────────────────────┐
│ Intake / UI  │-> │ Temporal        │-> │ Memory Policy      │
└──────────────┘   │ workflow        │   │ redaction / RBAC   │
                   └────────┬────────┘   └─────────┬──────────┘
                            │                      v
                            │        ┌──────────────────────────────┐
                            │        │ Episodic Checkpoint Store    │
                            │        │ approval pauses / step log   │
                            │        └──────────┬───────────────────┘
                            │                   v
                            │        ┌──────────────────────────────┐
                            │        │ Semantic Facts Store         │
                            │        │ claimant profile / policy    │
                            │        └──────────┬───────────────────┘
                            │                   v
                            │        ┌──────────────────────────────┐
                            └------> │ Permission-Aware Retrieval   │
                                     │ policy docs / evidence       │
                                     └──────────┬───────────────────┘
                                                v
                                     ┌──────────────────────────────┐
                                     │ Audit Ledger / SIEM          │
                                     └──────────────────────────────┘
```

Technology choices:

- durable workflow engine for episodic state and replay
- outbox or Kafka-backed semantic write pipeline for validated fact updates
- permission-aware retrieval for policy and evidence search
- immutable audit ledger for every memory read and write decision

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Long-lived chat session plus direct DB writes | Medium | Medium | Medium | Weak-Medium | Medium |
| Durable workflow + validated semantic store + permission-aware retrieval | Medium-High | Medium | High | Very High | High |
| Retrieval-only memory with no semantic facts | Medium | Medium-High | Medium | High | High |

**Decision rationale**:

Choose `durable workflow + validated semantic store + permission-aware retrieval`. Regulated workflows need replayable episodes, explainable fact updates, and tenant-aware evidence recall. Retrieval-only memory is too weak for stable claimant facts, while direct DB writes from a conversational runtime create unacceptable audit and poisoning risk. The recommended design accepts higher operational complexity because resilience, compliance, and chain-of-custody matter more than minimum raw latency.

## Sources

- [1] https://docs.langchain.com/oss/python/concepts/context - LangChain context categories and context-engineering framing.
- [2] https://docs.langchain.com/oss/python/concepts/memory - LangChain short-term and long-term memory concepts.
- [3] https://docs.langchain.com/oss/python/langgraph/add-memory - LangGraph memory patterns, stores, trimming, and summarization.
- [4] https://docs.langchain.com/oss/python/langgraph/checkpointers - LangGraph checkpointing, thread state, and pending writes.
- [5] https://openai.github.io/openai-agents-python/sessions/ - OpenAI session persistence, history shaping, and continuation behavior.
- [6] https://openai.github.io/openai-agents-python/running_agents/ - OpenAI run loop, continuation models, and durable-execution integrations.
- [7] https://developers.openai.com/api/docs/guides/prompt-caching - OpenAI prompt-cache thresholds, TTL, and billing semantics.
- [8] https://developers.openai.com/api/docs/guides/agent-builder-safety - OpenAI guidance on isolating untrusted data and prompt-injection risk.
- [9] https://platform.claude.com/docs/en/build-with-claude/prompt-caching - Anthropic cache modes, thresholds, pricing, and lookup behavior.
- [10] https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks - Anthropic isolation guidance for tool outputs and untrusted content.
- [11] https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling - Anthropic `allowed_callers` caveat and programmatic tool behavior.
- [12] https://adk.dev/sessions/ - ADK Session, State, and Memory model.
- [13] https://adk.dev/sessions/memory/ - ADK long-term searchable memory abstractions.
- [14] https://adk.dev/sessions/session/ - ADK session-service locking and persistence guidance.
- [15] https://adk.dev/context/compaction/ - ADK token-threshold and sliding-window compaction.
- [16] https://ai.google.dev/gemini-api/docs/generate-content/caching - Gemini cache behavior, TTL, and token thresholds.
- [17] https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-overview - Gemini Enterprise cache modes, discounts, and storage model.
- [18] https://redis.io/docs/latest/develop/ai/context-engine/langcache/ - Redis LangCache exact vs semantic cache behavior.
- [19] https://redis.io/docs/latest/develop/ai/langcache/api-examples/ - LangCache API examples and threshold-based retrieval behavior.
- [20] https://arxiv.org/html/2005.11401v4 - Original RAG paper framing non-parametric memory.
- [21] https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview - Hybrid lexical + vector retrieval and permission-aware knowledge-layer positioning.
- [22] https://learn.microsoft.com/en-us/azure/search/semantic-search-overview - Azure semantic reranking limits and token behavior.
- [23] https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview - Agentic retrieval planning, fan-out, activity log, and cost example.
- [24] https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve - Retrieval and MCP access-control prerequisites for Azure knowledge bases.
- [25] https://microsoft.github.io/graphrag/ - GraphRAG indexing pipeline and global/local/drift query modes.
- [26] https://r.jordan.im/download/language-models/2404.16130v1.pdf - GraphRAG paper with structured graph memory and global summarization.
- [27] https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/ - LazyGraphRAG cost-quality trade-offs.
- [28] https://aclanthology.org/2024.tacl-1.9/ - "Lost in the Middle" long-context degradation benchmark.
- [29] https://arxiv.org/abs/2404.06654 - RULER long-context benchmark.
