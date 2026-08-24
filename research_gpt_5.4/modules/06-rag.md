# RAG — Hybrid Search, Reranking, Agentic RAG, Graph RAG

## 1. System Topology & Data Flow

Production RAG is no longer a single "embed then retrieve" path. The practical enterprise shape is a split control plane and data plane: the control plane decides whether the request should stay on a fast hybrid path, enter an agentic decomposition loop, or invoke graph-aware retrieval for corpus-level synthesis. The data plane executes lexical retrieval, vector retrieval, reranking, answer generation, and evidence persistence.

```text
┌────────────────────────────── Control Plane ───────────────────────────────┐
│  API Gateway -> AuthN/Z -> Policy Router -> Query Planner / RAG Runtime    │
│       │             │              │                    │                   │
│       │             │              │                    ├─ Hybrid Path      │
│       │             │              │                    ├─ Agentic Path     │
│       │             │              │                    └─ Graph Path       │
│       └──────────────────────────> Correlation ID / Deadline / Tenant       │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  v
┌──────────────────────────────── Data Plane ────────────────────────────────┐
│  BM25 Search ─┐                                                            │
│               ├─> RRF Fusion ─> Reranker ─> Context Builder ─> Generator   │
│  Vector ANN ──┘                                                            │
│       │                            ▲                         │              │
│       └─────────────── Graph Store / Community Summaries ───┘              │
│                                 (local/global/drift)                       │
└──────────────────────────────────────────────────────────────────────────────┘
          │                         │                         │
          v                         v                         v
┌──────────────────┐     ┌────────────────────┐    ┌─────────────────────────┐
│ Persistence      │     │ Tool Proxies       │    │ Telemetry / Audit       │
│ chunk store      │     │ MCP gateway        │    │ logs / traces / metrics │
│ vector index     │     │ RBAC / PII filter  │    │ SIEM / cost ledger      │
│ graph artifacts  │     │ approval hooks     │    │ immutable decision log  │
└──────────────────┘     └────────────────────┘    └─────────────────────────┘
```

### Request-flow narrative

1. `API Gateway` authenticates the tenant, assigns a `correlation_id`, and starts an end-to-end latency budget.
2. `Policy Router` classifies the question:
   - exact-ID or prose lookup -> `hybrid path`
   - multi-part investigative question -> `agentic path`
   - corpus-wide theme/sensemaking question -> `graph path`
3. On the `hybrid path`, `BM25` and vector `ANN` retrieval run in parallel and merge with `RRF`.
4. `Reranker` rescoring narrows the fused candidates to a high-precision context window; if reranking is unavailable, the system can still continue with fused first-stage results.
5. On the `agentic path`, the planner decomposes the request into bounded subqueries, executes them in parallel, and merges the evidence before answer synthesis.
6. On the `graph path`, the runtime chooses `local`, `global`, or `drift` search over graph artifacts and community summaries instead of relying only on nearest-neighbor lookup.
7. `Context Builder` constructs a citation-preserving prompt, enforces token limits, and redacts policy-protected spans before generation.
8. `Generator` returns an answer plus references, while `Telemetry / Audit` records retrieved documents, ranking scores, fallback decisions, token usage, and user-visible degradations.

The architectural boundary that matters most is `index-time state` versus `query-time state`. Hybrid retrieval mainly stores chunks, postings, and embeddings. Agentic RAG adds orchestration state such as subquery plans and execution logs. GraphRAG adds heavier persisted artifacts: entities, relations, community hierarchy, and precomputed summaries. That split determines refresh cost, failure recovery, and audit depth.

## 2. Core Mechanics & Algorithms

### Hybrid retrieval

Hybrid retrieval solves a real production failure mode: vector search is good at semantic similarity, but lexical search is better for exact identifiers such as SKU names, dates, people, and jargon. The standard pattern is parallel `BM25` plus vector search, followed by a rank-fusion step.

Reciprocal Rank Fusion is typically approximated as:

```text
RRF(d) = Σ_i 1 / (k + rank_i(d))
```

Where:

- `i` is each retrieval branch
- `rank_i(d)` is document `d`'s rank in branch `i`
- `k` is a smoothing constant

Practical complexity:

- `BM25`: dominated by posting-list traversal and scoring over matched terms
- `ANN vector search`: sublinear average-case lookup with graph-based or partitioned indices, but quality depends on recall configuration
- `RRF`: `O(b * n)` for `b` branches and `n` retained candidates

Key invariant: hybrid retrieval improves recall only if each branch is independently healthy. If the lexical index is stale or the vector index was built with poor chunking, `RRF` cannot invent missing evidence.

### Reranking

Reranking is a second-stage precision layer. The first stage retrieves a candidate pool; the reranker scores those candidates more deeply and reorders them. This is where hybrid systems typically trade a moderate latency increase for materially better grounding quality.

Important operational constraint: reranking is recall-bounded. If the crucial bridge passage never enters the candidate pool, second-stage ranking cannot recover it. That makes `candidate starvation` one of the most important RAG failure modes.

Useful sizing heuristic:

```text
rerank_token_load
  ~= subqueries * candidate_docs_per_subquery * avg_tokens_per_doc
```

This token load is often the dominant cost driver in agentic retrieval.

### Late interaction retrieval

`ColBERTv2` is the most important "middle ground" between single-vector dense retrieval and expensive cross-encoder reranking. It stores multiple token-level vectors per passage and scores with `MaxSim`, preserving finer-grained lexical-semantic interactions than a single embedding can capture.

Approximate scoring shape:

```text
score(query, doc)
  = Σ_q max_j sim(q_token_vector, doc_token_vector_j)
```

Trade-off:

- better recall and interpretability than single-vector bi-encoders
- lower online cost than full cross-encoder reranking
- larger index and more complex serving path than standard dense retrieval

### Agentic RAG as a bounded state machine

Agentic RAG should be modeled as a guarded loop, not as "let the LLM keep searching until it feels done."

```text
ACCEPT
  -> PLAN_SUBQUERIES
  -> PARALLEL_RETRIEVE
  -> GRADE_EVIDENCE
     -> GENERATE_ANSWER   if confidence is sufficient
     -> REWRITE_QUERY     if evidence is weak and retry budget remains
     -> ESCALATE_FALLBACK if budget, policy, or latency threshold trips
```

This control plane is strictly more expressive than classic retrieve-then-generate because it can decompose questions, route by source, and retry with a reformulated query. It is also strictly more dangerous operationally because cost and latency variance now grow with fan-out.

Key invariants:

- `max_subqueries`, `max_rewrites`, and total deadline must be explicit
- every subquery needs a stable `subquery_id` for auditability
- tool calls and document fetches must be idempotent under replay

### GraphRAG mechanics

GraphRAG changes the retrieval substrate itself. Instead of storing only chunks and embeddings, it builds:

- `TextUnits`
- extracted `entities`
- extracted `relationships`
- clustered `communities`
- bottom-up `community summaries`

This unlocks materially different query modes:

- `Local Search`: entity-centric neighborhood reasoning
- `Global Search`: corpus-wide synthesis from community summaries
- `DRIFT Search`: local exploration with community context
- `Basic Search`: vector-like fallback

Query-time complexity shifts from nearest-neighbor lookup toward a map-reduce style reduction over precomputed abstractions. That is why GraphRAG is often better for questions like "what themes connect these incidents?" and not automatically better for narrow fact lookup.

### Convergence and correctness constraints

- Hybrid systems converge when top-`k` candidate sizes, reranker budgets, and prompt windows are bounded.
- Agentic systems converge only if rewrite loops, source fan-out, and reasoning depth are capped.
- Graph pipelines converge operationally only if graph extraction and community summarization are versioned, replayable, and decoupled from online serving.
- All RAG variants require stable chunk IDs and citation-preserving lineage; otherwise auditability collapses once indexes are refreshed.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: Public sources document limits, billing primitives, and benchmark quality much better than they document stable end-to-end `p50/p95/p99` production latencies. The percentile targets below are recommended SLO envelopes, not vendor guarantees.

### Cost formulas

Assumptions for the formulas below:

- `runs = 1000`
- Hybrid search keeps `k_first_stage = 50` candidates for reranking
- Agentic retrieval uses `subqueries = 3`
- `avg_doc_tokens = 500`
- `answer_input_tokens = 3500`
- `answer_output_tokens = 350`
- `P_*` terms are service-specific unit prices

#### Hybrid + rerank

```text
$ per 1k runs =
1000 * (
  P_lexical_req +
  P_vector_req +
  ((k_first_stage * avg_doc_tokens) / 1_000_000) * P_rerank_token +
  (answer_input_tokens / 1_000_000) * P_answer_in +
  (answer_output_tokens / 1_000_000) * P_answer_out
)
```

This formula makes the real budget lever visible: holding `k_first_stage` at `50` keeps reranker quality high, but it also makes reranker cost linear in both candidate count and document length.

#### Agentic RAG

```text
$ per 1k runs =
1000 * (
  (planner_input_tokens / 1_000_000) * P_plan_in +
  (planner_output_tokens / 1_000_000) * P_plan_out +
  ((subqueries * k_first_stage * avg_doc_tokens) / 1_000_000) * P_rerank_token +
  (answer_input_tokens / 1_000_000) * P_answer_in +
  (answer_output_tokens / 1_000_000) * P_answer_out
)
```

Using Azure's published worked example assumptions:

- `subqueries = 3`
- `k_first_stage = 50`
- `avg_doc_tokens = 500`
- published example total for `2,000` retrievals = `$4.32` for planning plus reranking

The retrieval-side budget becomes:

```text
$ per 1k runs = $4.32 / 2 = $2.16
```

That figure is useful because it shows how quickly cost rises once a single user request becomes a planned fan-out workflow.

#### GraphRAG / LazyGraphRAG

Graph systems have two budgets: `index refresh cost` and `query cost`.

```text
$ per 1k runs =
1000 * (
  query_generation_tokens / 1_000_000 * P_answer_in_out
) + (
  1000 * index_refresh_cost / queries_between_rebuilds
)
```

For full GraphRAG, `index_refresh_cost` can dominate. For `LazyGraphRAG`, the public claim is qualitative rather than a stable public price sheet: indexing cost is reported at `0.1%` of full GraphRAG and query cost is reported at `>700x` lower than GraphRAG Global Search in one compared setup.

> ⚠️ Gap: The source set does not provide a broadly reusable public absolute price table for full GraphRAG indexing, so the safest enterprise budgeting model is to amortize index refresh over expected query volume and treat graph extraction as a separate capital-like workload.

### Latency targets

Recommended user-facing targets by retrieval mode:

- `Hybrid + rerank`: `p50 <= 900ms`, `p95 <= 2.0s`, `p99 <= 3.5s`
- `Agentic RAG`: `p50 <= 2.5s`, `p95 <= 6.0s`, `p99 <= 10.0s`
- `Graph/global synthesis`: `p50 <= 4.0s`, `p95 <= 12.0s`, `p99 <= 20.0s`

Mitigations by percentile:

- `p50`: warm HTTP pools, colocated search and rerank services, prebuilt prompt prefixes, streaming first token
- `p95`: bounded subquery fan-out, parallel retrieval branches, document truncation before rerank, async citation hydration
- `p99`: admission control, per-branch deadlines, fallback to non-reranked hybrid retrieval, degrade from `global` to `local` graph mode when the reducer threatens the deadline

### Throughput and back-pressure

The reranker is often the first saturation point because cost and latency both scale with candidate count. Capacity planning should therefore budget on `rerank_tokens_per_second`, not only `requests_per_second`.

Useful planning heuristic:

```text
required_rerank_tps
  = qps * subqueries * candidate_docs_per_subquery * avg_doc_tokens
```

Back-pressure policy should be explicit:

1. cap concurrent rerank calls per tenant
2. shed low-priority requests once queue depth exceeds a threshold
3. reduce `k_first_stage` for bronze-tier traffic
4. skip reranking and serve fused first-stage results when the circuit breaker is open

### Availability, RPO, RTO, and compliance

Recommended enterprise targets:

- `Availability`: `99.9%` for standard internal copilots, `99.95%` for customer-facing assistants with contractual SLAs
- `RPO`: `<= 15 min` for index metadata and orchestration state; `0` for immutable audit events once acknowledged to the caller
- `RTO`: `<= 30 min` for retrieval tier failover; `<= 4 hr` for full graph rebuild workflows

Compliance discussion:

- enforce data residency boundaries between search, rerank, and model providers
- treat third-party planner or rerank calls as separate compliance hops
- persist only redacted snippets in telemetry
- retain source-document lineage long enough to support legal hold, right-to-audit, and post-incident reconstruction

## 4. Distributed Resilience & Security

### Durable execution

RAG becomes operationally reliable only when ingest, refresh, and multi-step query orchestration are externalized into durable systems.

A practical pattern:

- `Kafka` or CDC stream captures document changes
- `Temporal` workflow orchestrates chunking, embedding, entity extraction, graph updates, and index swaps
- each activity writes checkpoints keyed by `document_version`
- failed documents move to a `DLQ` with poison-pill metadata
- online query flows stay synchronous, but long-running graph rebuilds, bulk backfills, and expensive summarization execute durably

This separation matters because query-time retries and index-time retries are not the same problem. A failed answer request may retry in hundreds of milliseconds. A failed graph rebuild may require checkpoint resume, shard replay, or operator intervention.

### Failure taxonomy

`Transient failures`

- search timeouts
- `429` and provider rate limits
- temporary index shard imbalance
- network flaps between retriever, reranker, and generator

`Permanent failures`

- malformed source documents
- unsupported file types
- RBAC-denied retrieval
- schema drift between chunk metadata and prompt builder

`Poison-pill failures`

- a specific document repeatedly breaks entity extraction
- a tenant corpus repeatedly exceeds graph-clustering memory limits
- a corrupted chunk payload replays forever unless quarantined

`Correctness failures`

- reranker starvation from poor first-stage recall
- stale graph summaries after partial rebuild
- citation mismatch when chunk IDs change across refreshes
- prompt truncation removing the decisive evidence span

### Retry and circuit-breaker policy

Retries belong only on transient boundaries and only for idempotent operations. A safe policy is:

- search: `2-3` retries with exponential backoff and jitter
- rerank: `1-2` retries, then degrade to fused first-stage ordering
- answer generation: retry once on transport errors, then fail over to a cheaper or smaller model
- graph indexing activities: retry with workflow checkpoint resume, not blind process restarts

Circuit breaker state model:

```text
CLOSED
  -> OPEN       after error-rate or timeout threshold breach
  -> HALF_OPEN  after cooldown
  -> CLOSED     after healthy probe window
  -> OPEN       if probes fail
```

### Fallback chains and graceful degradation

A resilient RAG system should degrade in layers rather than fail as a monolith:

1. `hybrid + rerank + generator`
2. `hybrid + fused ranking + generator`
3. `lexical-only` for exact-ID or compliance-constrained queries
4. `deterministic citation bundle` when generation is unavailable

For graph-heavy paths:

1. `global graph search`
2. `local/drift search`
3. `hybrid retrieval over raw chunks`
4. cached last-known-good summary if policy permits

The key design goal is preserving usefulness under partial outages while surfacing a truthful degradation flag to the caller.

### Enterprise security controls

Zero-Trust `MCP` and tool proxy pattern:

- all tool calls terminate at a policy-enforcing proxy
- proxies inject least-privilege credentials instead of exposing raw secrets to the model
- per-tool RBAC maps user role, tenant, and document scope to allowed operations
- deny decisions are logged with reason codes and correlation IDs

PII handling pipeline:

1. detect sensitive spans before indexing
2. redact or tokenize sensitive fields before retrieval where possible
3. re-check retrieved snippets before prompt construction
4. record every redaction and disclosure decision in an immutable audit ledger

Auditability requirements:

- immutable event log for retrieval choices, rerank scores, fallback decisions, and user-visible outputs
- source lineage from answer -> chunk -> document version -> ingestion run
- signed or append-only storage for regulated investigations

> ⚠️ Gap: Public RAG and GraphRAG sources are strong on relevance and cost, but thin on exact built-in checkpoint semantics, field-level authorization propagation, and compliance-grade audit schemas. Production teams must design those controls explicitly.

## 5. Production Enterprise Code

The example below is a runnable Python service skeleton that demonstrates parallel hybrid retrieval, retries with exponential backoff and jitter, circuit breakers, structured logging with correlation IDs, a generator fallback chain, and graceful degradation when the reranker or generator is unavailable.

```python
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, List, Sequence


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


@dataclass(frozen=True)
class Document:
    doc_id: str
    text: str
    source: str
    score: float


@dataclass(frozen=True)
class Answer:
    text: str
    citations: List[str]
    degraded: bool
    reason: str | None = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": int(record.created * 1000),
        }
        for key in ("correlation_id", "tenant_id", "event", "degraded"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("rag_service")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = build_logger()


def log(event: str, message: str, correlation_id: str, tenant_id: str, **extra: object) -> None:
    LOGGER.info(
        message,
        extra={
            "event": event,
            "correlation_id": correlation_id,
            "tenant_id": tenant_id,
            **extra,
        },
    )


def retry(
    fn: Callable[[], Sequence[Document]],
    retries: int,
    base_delay_s: float,
    max_delay_s: float,
) -> Sequence[Document]:
    attempt = 0
    while True:
        try:
            return fn()
        except TransientError:
            if attempt >= retries:
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** attempt))
            jitter = random.uniform(0.0, delay * 0.25)
            time.sleep(delay + jitter)
            attempt += 1


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_timeout_s: float) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.failures = 0
        self.state = CircuitState.CLOSED
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.state == CircuitState.OPEN:
                if (time.time() - self.opened_at) >= self.recovery_timeout_s:
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.time()


class SearchBackend:
    def search(self, query: str, top_k: int) -> Sequence[Document]:
        raise NotImplementedError


class InMemoryKeywordSearch(SearchBackend):
    def __init__(self, documents: Iterable[Document]) -> None:
        self.documents = list(documents)

    def search(self, query: str, top_k: int) -> Sequence[Document]:
        query_terms = {term.lower() for term in query.split()}
        ranked = []
        for doc in self.documents:
            score = sum(term in doc.text.lower() for term in query_terms)
            if score:
                ranked.append(Document(doc.doc_id, doc.text, doc.source, float(score)))
        ranked.sort(key=lambda d: d.score, reverse=True)
        return ranked[:top_k]


class InMemoryVectorSearch(SearchBackend):
    def __init__(self, documents: Iterable[Document]) -> None:
        self.documents = list(documents)

    def search(self, query: str, top_k: int) -> Sequence[Document]:
        if "vector_fail" in query:
            raise TransientError("temporary vector index timeout")
        query_terms = {term.lower() for term in query.split()}
        ranked = []
        for doc in self.documents:
            overlap = len(query_terms.intersection(set(doc.text.lower().split())))
            ranked.append(Document(doc.doc_id, doc.text, doc.source, float(overlap)))
        ranked.sort(key=lambda d: d.score, reverse=True)
        return ranked[:top_k]


class SimpleReranker:
    def rerank(self, query: str, docs: Sequence[Document], top_k: int) -> Sequence[Document]:
        if "rerank_fail" in query:
            raise TransientError("temporary reranker overload")
        query_terms = {term.lower() for term in query.split()}
        rescored = []
        for doc in docs:
            exact_boost = sum(term in doc.text.lower() for term in query_terms) * 0.5
            rescored.append(Document(doc.doc_id, doc.text, doc.source, doc.score + exact_boost))
        rescored.sort(key=lambda d: d.score, reverse=True)
        return rescored[:top_k]


class Generator:
    def generate(self, query: str, docs: Sequence[Document]) -> str:
        raise NotImplementedError


class PrimaryGenerator(Generator):
    def generate(self, query: str, docs: Sequence[Document]) -> str:
        if "primary_fail" in query:
            raise TransientError("primary model transport error")
        summary = " | ".join(f"{doc.source}: {doc.text[:80]}" for doc in docs[:3])
        return f"Primary answer for '{query}'. Evidence: {summary}"


class SecondaryGenerator(Generator):
    def generate(self, query: str, docs: Sequence[Document]) -> str:
        if "secondary_fail" in query:
            raise TransientError("secondary model transport error")
        summary = " | ".join(f"{doc.source}: {doc.text[:60]}" for doc in docs[:2])
        return f"Secondary answer for '{query}'. Evidence: {summary}"


def deterministic_fallback(query: str, docs: Sequence[Document]) -> str:
    if not docs:
        return f"No grounded answer available for '{query}'."
    bullets = "; ".join(f"{doc.source}={doc.text[:50]}" for doc in docs[:3])
    return f"Grounded extracts for '{query}': {bullets}"


def reciprocal_rank_fusion(result_sets: Sequence[Sequence[Document]], k: int = 60) -> List[Document]:
    scores: dict[str, float] = {}
    best_doc: dict[str, Document] = {}
    for results in result_sets:
        for rank, doc in enumerate(results, start=1):
            scores[doc.doc_id] = scores.get(doc.doc_id, 0.0) + (1.0 / (k + rank))
            best_doc.setdefault(doc.doc_id, doc)
    fused = [
        Document(doc_id=doc_id, text=best_doc[doc_id].text, source=best_doc[doc_id].source, score=score)
        for doc_id, score in scores.items()
    ]
    fused.sort(key=lambda d: d.score, reverse=True)
    return fused


class HybridRAGService:
    def __init__(
        self,
        keyword_search: SearchBackend,
        vector_search: SearchBackend,
        reranker: SimpleReranker,
        primary_generator: Generator,
        secondary_generator: Generator,
    ) -> None:
        self.keyword_search = keyword_search
        self.vector_search = vector_search
        self.reranker = reranker
        self.primary_generator = primary_generator
        self.secondary_generator = secondary_generator
        self.rerank_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=10.0)
        self.gen_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=10.0)

    def answer(self, query: str, tenant_id: str) -> Answer:
        correlation_id = str(uuid.uuid4())
        log("request_start", "starting rag request", correlation_id, tenant_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            keyword_future = pool.submit(lambda: retry(lambda: self.keyword_search.search(query, 8), 2, 0.05, 0.20))
            vector_future = pool.submit(lambda: retry(lambda: self.vector_search.search(query, 8), 2, 0.05, 0.20))

            keyword_docs = list(keyword_future.result())
            try:
                vector_docs = list(vector_future.result())
            except TransientError:
                vector_docs = []
                log("vector_degraded", "vector search unavailable, using keyword-only path", correlation_id, tenant_id, degraded=True)

        fused = reciprocal_rank_fusion([keyword_docs, vector_docs])[:8]
        if not fused:
            return Answer(
                text=f"No documents matched '{query}'.",
                citations=[],
                degraded=True,
                reason="no_candidates",
            )

        ranked = fused
        degraded = False
        reason: str | None = None

        if self.rerank_breaker.allow():
            try:
                ranked = list(retry(lambda: self.reranker.rerank(query, fused, 5), 1, 0.05, 0.10))
                self.rerank_breaker.record_success()
            except TransientError:
                self.rerank_breaker.record_failure()
                degraded = True
                reason = "reranker_unavailable"
                log("rerank_degraded", "reranker unavailable, serving fused ranking", correlation_id, tenant_id, degraded=True)
        else:
            degraded = True
            reason = "rerank_circuit_open"
            log("rerank_circuit_open", "reranker circuit open, serving fused ranking", correlation_id, tenant_id, degraded=True)

        answer_text, gen_degraded, gen_reason = self._generate_with_fallback(
            query, ranked, correlation_id, tenant_id
        )
        if gen_degraded:
            degraded = True
            reason = reason or gen_reason

        citations = [doc.source for doc in ranked[:3]]
        log("request_complete", "completed rag request", correlation_id, tenant_id, degraded=degraded)
        return Answer(text=answer_text, citations=citations, degraded=degraded, reason=reason)

    def _generate_with_fallback(
        self,
        query: str,
        docs: Sequence[Document],
        correlation_id: str,
        tenant_id: str,
    ) -> tuple[str, bool, str | None]:
        if self.gen_breaker.allow():
            try:
                result = self.primary_generator.generate(query, docs)
                self.gen_breaker.record_success()
                return result, False, None
            except TransientError:
                self.gen_breaker.record_failure()
                log("primary_model_failed", "primary generator failed", correlation_id, tenant_id, degraded=True)

        try:
            result = self.secondary_generator.generate(query, docs)
            return result, True, "secondary_model_used"
        except TransientError:
            log("secondary_model_failed", "secondary generator failed, using deterministic fallback", correlation_id, tenant_id, degraded=True)
            return deterministic_fallback(query, docs), True, "deterministic_fallback"


if __name__ == "__main__":
    corpus = [
        Document("1", "Invoice retention policy is 7 years for enterprise accounts.", "policy.md", 0.0),
        Document("2", "SKU ZX-42 ships with the premium connector bundle.", "catalog.md", 0.0),
        Document("3", "Incident summaries are clustered by service and customer impact.", "incidents.md", 0.0),
    ]
    service = HybridRAGService(
        keyword_search=InMemoryKeywordSearch(corpus),
        vector_search=InMemoryVectorSearch(corpus),
        reranker=SimpleReranker(),
        primary_generator=PrimaryGenerator(),
        secondary_generator=SecondaryGenerator(),
    )

    response = service.answer("What is the retention policy for invoice records?", tenant_id="acme")
    print(response)
```

This snippet is intentionally service-shaped rather than notebook-shaped. It shows where retries belong, where circuit breakers sit, how degradations are surfaced, and how a deterministic fallback preserves grounded output instead of returning a generic model failure.

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-tenant support copilot with exact identifiers and citations

**Problem statement**

Design a customer-facing SaaS support copilot that serves product manuals, ticket history, and product-catalog metadata for `40k` queries/min. The system must support exact SKU and entitlement lookups, return citations, and keep `p99 <= 3.5s`.

**Proposed architecture**

```text
┌──────────────┐   ┌──────────────┐   ┌────────────────────┐
│ Web / API    │-> │ Policy Edge  │-> │ Hybrid Retriever   │
└──────────────┘   └──────────────┘   │ BM25 + Vector +    │
                                      │ tenant filters     │
                                      └─────────┬──────────┘
                                                v
                                      ┌────────────────────┐
                                      │ Semantic Reranker  │
                                      └─────────┬──────────┘
                                                v
                                      ┌────────────────────┐
                                      │ Answer Generator   │
                                      │ citations only     │
                                      └─────────┬──────────┘
                                                v
                                      ┌────────────────────┐
                                      │ Audit / Metrics    │
                                      └────────────────────┘
```

Technology choices:

- search tier with parallel `BM25` and vector retrieval
- semantic reranker over top `50` candidates
- prompt builder that pins source citations and tenant filters
- immutable audit log for answer -> chunk -> document lineage

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Vector-only RAG | Low | Low | Low | Medium | High |
| Hybrid + rerank | Medium | Medium | Medium | High | High |
| Agentic RAG | High | High | High | High | Medium-High |

**Decision rationale**

Choose `hybrid + rerank`. Vector-only misses exact identifiers too often for support workflows, while agentic RAG adds cost and latency variance that the workload does not need. Hybrid retrieval covers exact-match product data and semantically related prose at the same time, and reranking improves citation quality without introducing a planning loop on every request.

### Scenario 2: Enterprise investigation assistant for cross-document themes and relationship discovery

**Problem statement**

Design an internal investigation assistant over contracts, emails, incidents, and compliance reports. Users ask global questions such as "what themes connect refund disputes across regions?" and "which entities recur across policy violations?" The system must preserve source lineage, tolerate long-running rebuilds, and support `p95 <= 12s` for global synthesis.

**Proposed architecture**

```text
┌──────────────┐   ┌──────────────────┐   ┌──────────────────────┐
│ Analyst UI   │-> │ Query Classifier │-> │ Graph-aware Runtime   │
└──────────────┘   └──────────────────┘   │ local/global/drift    │
                                          └──────────┬────────────┘
                                                     v
                                 ┌──────────────────────────────────────────┐
                                 │ Graph Artifacts                          │
                                 │ TextUnits / entities / relations /       │
                                 │ communities / summaries                  │
                                 └──────────┬───────────────────────────────┘
                                            v
                                 ┌──────────────────────────────────────────┐
                                 │ Hybrid Fallback Retriever                │
                                 │ raw chunks for exact evidence recovery   │
                                 └──────────┬───────────────────────────────┘
                                            v
                                 ┌──────────────────────────────────────────┐
                                 │ Synthesis + Audit Ledger                 │
                                 └──────────────────────────────────────────┘
```

Technology choices:

- graph extraction and community summarization in a durable workflow engine
- `global` graph search for corpus-wide questions
- `local` or `drift` graph search for entity-centric follow-ups
- hybrid raw-chunk fallback for exact evidence recovery and legal review

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Hybrid + rerank only | Medium | Medium | Medium | High | High |
| Full GraphRAG | Very High | High | Very High | Medium-High | Medium |
| LazyGraphRAG + hybrid fallback | Medium-High | Medium-High | High | High | High |

**Decision rationale**

Choose `LazyGraphRAG + hybrid fallback`. Pure hybrid retrieval is weakest on global synthesis, while full GraphRAG often over-invests in preprocessing cost and operational complexity. LazyGraphRAG preserves the graph advantage for theme discovery and relationship reasoning while keeping index economics closer to vector RAG, and the hybrid fallback ensures exact-evidence recovery when legal or audit review requires passage-level precision.

## Sources

- [1] https://arxiv.org/html/2005.11401v4 - Original RAG paper with retriever-generator mechanics and index design.
- [2] https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview - Hybrid search mechanics and `RRF` fusion.
- [3] https://learn.microsoft.com/en-us/azure/search/semantic-search-overview - Semantic reranking behavior, token limits, and candidate cap.
- [4] https://learn.microsoft.com/en-us/azure/search/semantic-how-to-enable-disable - Semantic ranker billing plan details.
- [5] https://azure.microsoft.com/en-us/pricing/details/search/ - Azure AI Search pricing allowances.
- [6] https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview - Agentic retrieval topology, reasoning modes, and cost example.
- [7] https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve - Retrieve action and `MCP` access pattern.
- [8] https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-create - Knowledge-base configuration and encryption hooks.
- [9] https://docs.langchain.com/oss/python/langgraph/agentic-rag - Agentic retrieval loop structure.
- [10] https://docs.cohere.com/reference/rerank.mdx - Rerank API contract and limits.
- [11] https://docs.cohere.com/docs/rate-limits - Public rerank rate limits.
- [12] https://docs.cohere.com/docs/reranking-best-practices.md - Rerank document caps and truncation behavior.
- [13] https://doi.org/10.48550/arxiv.2104.08663 - `BEIR` retrieval benchmark.
- [14] https://doi.org/10.48550/arxiv.2306.07471 - Dense-sparse hybrid benchmark follow-up.
- [15] https://arxiv.org/html/2112.01488v3 - `ColBERTv2` late-interaction retrieval.
- [16] https://microsoft.github.io/graphrag/ - GraphRAG query modes and pipeline overview.
- [17] https://r.jordan.im/download/language-models/2404.16130v1.pdf - GraphRAG paper on global/local search and community summarization.
- [18] https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/ - LazyGraphRAG quality-cost claims.
- [19] https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/ - BenchmarkQED and query-class comparison results.
