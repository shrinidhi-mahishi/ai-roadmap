"""
Module 01: Retrieval-Augmented Generation (RAG) -- Code Examples

Extracted from 01-rag.md. Contains two complete production RAG implementations:

  A. Production RAG Pipeline with structlog (Opus-style)
     - Pipeline composition with fallback chains
     - Hybrid retrieval with RRF fusion
     - Circuit breaker, retry with exponential backoff + jitter
     - RBAC filtering, semantic caching, structured logging

  B. Stdlib-Only RAG Runtime with Protocol-Based Ports (Grok-style)
     - Protocol-based retriever/generator swapping
     - Degradation tracking and fallback chains
     - TransientError/PermanentError distinction
     - Lost-in-the-middle mitigation (edge placement)
     - Hop cap (max_hops=3)

Key patterns demonstrated across both implementations:
  - Per-dependency circuit breakers (vector index, reranker, generator)
  - Hybrid -> BM25 -> cache -> refuse retrieval fallback
  - Primary FM -> secondary FM -> extractive deterministic generation fallback
  - ACL on Authz dataclass (NEVER from model JSON)
  - Full-jitter retries (AWS-style)
  - JSON logs with correlation_id + tenant on every line
"""


# --- Section: Implementation A: Production RAG Pipeline with structlog ---

"""
Production RAG pipeline with resilience patterns.
Demonstrates: retries with exponential backoff + jitter, circuit breaker,
fallback chains, structured logging with correlation IDs.
"""

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

# --- Structured Logging -------------------------------------------------------

logger = structlog.get_logger()


def create_correlation_id() -> str:
    return str(uuid.uuid4())[:12]


# --- Circuit Breaker ----------------------------------------------------------

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Standard circuit breaker: Closed -> Open -> Half-Open -> Closed."""

    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 2

    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failure_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0.0, init=False)
    half_open_calls: int = field(default=0, init=False)

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                logger.info("circuit_breaker.half_open", breaker=self.name)
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return self.half_open_calls < self.half_open_max_calls
        return False

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info("circuit_breaker.closed", breaker=self.name)
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning("circuit_breaker.reopened", breaker=self.name)
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker.opened",
                breaker=self.name,
                failures=self.failure_count,
            )


# --- Retry with Exponential Backoff + Jitter ----------------------------------

def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError),
    correlation_id: str = "",
):
    """Execute func with exponential backoff and full jitter."""
    for attempt in range(max_retries + 1):
        try:
            result = func()
            if attempt > 0:
                logger.info(
                    "retry.succeeded",
                    attempt=attempt,
                    correlation_id=correlation_id,
                )
            return result
        except retryable_exceptions as e:
            if attempt == max_retries:
                logger.error(
                    "retry.exhausted",
                    attempts=max_retries + 1,
                    error=str(e),
                    correlation_id=correlation_id,
                )
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            if jitter:
                delay = random.uniform(0, delay)  # full jitter
            logger.warning(
                "retry.backoff",
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                error=str(e),
                correlation_id=correlation_id,
            )
            time.sleep(delay)


# --- Fallback Chain -----------------------------------------------------------

@dataclass
class LLMProvider:
    name: str
    circuit_breaker: CircuitBreaker
    generate_fn: Any  # callable(prompt, context) -> str

    def call(self, prompt: str, context: str, correlation_id: str) -> str:
        if not self.circuit_breaker.can_execute():
            raise RuntimeError(f"{self.name} circuit open")

        def _invoke():
            return self.generate_fn(prompt, context)

        try:
            result = retry_with_backoff(
                _invoke,
                max_retries=2,
                base_delay=0.5,
                retryable_exceptions=(ConnectionError, TimeoutError),
                correlation_id=correlation_id,
            )
            self.circuit_breaker.record_success()
            return result
        except Exception as e:
            self.circuit_breaker.record_failure()
            raise


class FallbackChain:
    """Try providers in order. First success wins. All fail = raise."""

    def __init__(self, providers: list[LLMProvider]):
        self.providers = providers

    def generate(self, prompt: str, context: str, correlation_id: str) -> dict:
        errors = []
        for provider in self.providers:
            try:
                result = provider.call(prompt, context, correlation_id)
                return {
                    "answer": result,
                    "provider": provider.name,
                    "fallback_used": provider != self.providers[0],
                }
            except Exception as e:
                errors.append((provider.name, str(e)))
                logger.warning(
                    "fallback.provider_failed",
                    provider=provider.name,
                    error=str(e),
                    correlation_id=correlation_id,
                )
        raise RuntimeError(
            f"All providers failed: {errors}"
        )


# --- RAG Pipeline -------------------------------------------------------------

@dataclass
class RetrievedChunk:
    text: str
    source: str
    score: float
    metadata: dict


@dataclass
class RAGResult:
    answer: str
    chunks: list[RetrievedChunk]
    provider: str
    fallback_used: bool
    latency_ms: float
    correlation_id: str
    cache_hit: bool


class ProductionRAGPipeline:
    """
    Production RAG with hybrid retrieval, reranking, RBAC filtering,
    circuit breakers, and fallback chains.
    """

    def __init__(
        self,
        dense_retriever,      # callable(query_embedding) -> list[RetrievedChunk]
        sparse_retriever,     # callable(query_text) -> list[RetrievedChunk]
        embedding_fn,         # callable(text) -> list[float]
        reranker_fn,          # callable(query, chunks) -> list[RetrievedChunk]
        llm_chain: FallbackChain,
        semantic_cache=None,  # optional: callable with get/set
        rbac_filter_fn=None,  # callable(chunks, user_id) -> list[RetrievedChunk]
        rrf_k: int = 60,
        top_k_retrieval: int = 50,
        top_k_rerank: int = 10,
    ):
        self.dense_retriever = dense_retriever
        self.sparse_retriever = sparse_retriever
        self.embedding_fn = embedding_fn
        self.reranker_fn = reranker_fn
        self.llm_chain = llm_chain
        self.semantic_cache = semantic_cache
        self.rbac_filter_fn = rbac_filter_fn
        self.rrf_k = rrf_k
        self.top_k_retrieval = top_k_retrieval
        self.top_k_rerank = top_k_rerank

    def _rrf_fusion(
        self,
        dense_results: list[RetrievedChunk],
        sparse_results: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Reciprocal Rank Fusion: score(d) = SUM(1/(k + rank(d)))."""
        scores: dict[str, float] = {}
        chunk_map: dict[str, RetrievedChunk] = {}

        for rank, chunk in enumerate(dense_results):
            key = hashlib.sha256(chunk.text.encode()).hexdigest()[:16]
            scores[key] = scores.get(key, 0) + 1.0 / (self.rrf_k + rank + 1)
            chunk_map[key] = chunk

        for rank, chunk in enumerate(sparse_results):
            key = hashlib.sha256(chunk.text.encode()).hexdigest()[:16]
            scores[key] = scores.get(key, 0) + 1.0 / (self.rrf_k + rank + 1)
            chunk_map[key] = chunk

        sorted_keys = sorted(scores, key=lambda k: scores[k], reverse=True)
        return [
            RetrievedChunk(
                text=chunk_map[k].text,
                source=chunk_map[k].source,
                score=scores[k],
                metadata=chunk_map[k].metadata,
            )
            for k in sorted_keys[:self.top_k_retrieval]
        ]

    def query(self, user_query: str, user_id: str = "anonymous") -> RAGResult:
        correlation_id = create_correlation_id()
        start_time = time.time()

        log = logger.bind(
            correlation_id=correlation_id,
            user_id=user_id,
            query_hash=hashlib.sha256(user_query.encode()).hexdigest()[:8],
        )
        log.info("rag.query.start")

        # L2: Semantic cache check
        if self.semantic_cache:
            cached = self.semantic_cache.get(user_query)
            if cached:
                log.info("rag.cache.hit")
                return RAGResult(
                    answer=cached["answer"],
                    chunks=[],
                    provider="cache",
                    fallback_used=False,
                    latency_ms=(time.time() - start_time) * 1000,
                    correlation_id=correlation_id,
                    cache_hit=True,
                )

        # Step 1: Embed query
        query_embedding = self.embedding_fn(user_query)

        # Step 2: Parallel hybrid retrieval
        dense_results = self.dense_retriever(query_embedding)
        sparse_results = self.sparse_retriever(user_query)

        # Step 3: RRF fusion
        fused = self._rrf_fusion(dense_results, sparse_results)
        log.info("rag.retrieval.complete", fused_count=len(fused))

        # Step 4: RBAC filtering (pre-retrieval is better; this is a safety net)
        if self.rbac_filter_fn:
            fused = self.rbac_filter_fn(fused, user_id)
            log.info("rag.rbac.filtered", remaining=len(fused))

        # Step 5: Reranking
        reranked = self.reranker_fn(user_query, fused)[:self.top_k_rerank]
        log.info("rag.rerank.complete", reranked_count=len(reranked))

        # Step 6: Generate with fallback chain
        context = "\n\n---\n\n".join(
            f"[Source: {c.source}]\n{c.text}" for c in reranked
        )
        llm_result = self.llm_chain.generate(
            prompt=user_query, context=context, correlation_id=correlation_id
        )

        # Step 7: Cache result
        if self.semantic_cache:
            self.semantic_cache.set(user_query, {"answer": llm_result["answer"]})

        elapsed_ms = (time.time() - start_time) * 1000
        log.info(
            "rag.query.complete",
            provider=llm_result["provider"],
            fallback_used=llm_result["fallback_used"],
            latency_ms=round(elapsed_ms, 1),
            chunks_used=len(reranked),
        )

        return RAGResult(
            answer=llm_result["answer"],
            chunks=reranked,
            provider=llm_result["provider"],
            fallback_used=llm_result["fallback_used"],
            latency_ms=elapsed_ms,
            correlation_id=correlation_id,
            cache_hit=False,
        )


# --- Section: Implementation B: Stdlib-Only RAG Runtime with Protocol-Based Ports ---

#!/usr/bin/env python3
"""RAG query-plane resilience: retries, circuit breaker, fallbacks, logging.

Stdlib only. Wire real HTTP clients behind Retriever/Generator protocols.
Key features: TransientError/PermanentError distinction, full-jitter retry
(AWS-style), per-dependency circuit breakers, ACL on Authz dataclass (NEVER
from model JSON), hybrid->BM25->cache->refuse retrieval fallback,
primary->secondary->extractive deterministic generation fallback,
lost-in-the-middle mitigation (edge placement), hop cap (max_hops=3).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

# --- Structured JSON Logging (correlation id + tenant on every line) ----------

class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = getattr(record, "correlation_id", "-")
        record.tenant_id = getattr(record, "tenant_id", "-")
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("rag")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"msg":"%(message)s"}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(level: int, msg: str, *, cid: str, tenant: str, **fields: object) -> None:
    extra = {"correlation_id": cid, "tenant_id": tenant}
    LOG.log(level, "%s %s", msg, json.dumps(fields, default=str), extra=extra)


# --- Retries: exponential backoff + full jitter (AWS-style) -------------------

class TransientError(Exception):
    """429, 5xx, timeout -- safe to retry idempotent reads."""


class PermanentError(Exception):
    """4xx auth, schema mismatch -- do not retry, do not rewrite-loop."""


def retry_with_jitter(
    fn: Callable[[], object],
    *,
    cid: str,
    tenant: str,
    op: str,
    attempts: int = 4,
    base_s: float = 0.05,
    cap_s: float = 1.0,
) -> object:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep = min(cap_s, base_s * (2**i))
            sleep = random.uniform(0, sleep)  # full jitter
            slog(
                logging.WARNING, "retry",
                cid=cid, tenant=tenant, op=op, attempt=i + 1, sleep_s=round(sleep, 3),
                err=str(exc),
            )
            time.sleep(sleep)
    assert last is not None
    raise last


# --- Circuit breaker: closed -> open -> half-open ----------------------------

class CircuitState_B(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(TransientError):
    pass


@dataclass
class CircuitBreaker_B:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 15.0
    half_open_probes: int = 1
    _state: CircuitState_B = CircuitState_B.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0

    def allow(self) -> None:
        now = time.monotonic()
        if self._state is CircuitState_B.OPEN:
            if now - self._opened_at >= self.cooldown_s:
                self._state = CircuitState_B.HALF_OPEN
                self._probes_used = 0
            else:
                raise CircuitOpenError(f"circuit_open:{self.name}")
        if self._state is CircuitState_B.HALF_OPEN:
            if self._probes_used >= self.half_open_probes:
                raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
            self._probes_used += 1

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState_B.CLOSED
        self._probes_used = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState_B.HALF_OPEN:
            self._state = CircuitState_B.OPEN
            self._opened_at = time.monotonic()
            return
        if self._failures >= self.failure_threshold:
            self._state = CircuitState_B.OPEN
            self._opened_at = time.monotonic()


# --- Retrieval + generation ports (swap in HTTP; identity is NEVER a tool arg)

@dataclass(frozen=True)
class Authz:
    tenant_id: str
    user_id: str
    acl_filter: dict  # pushed into every retriever arm


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    retriever: str
    score: float


class Retriever(Protocol):
    name: str
    def search(self, query: str, authz: Authz, k: int) -> list[Chunk]: ...


class Generator(Protocol):
    name: str
    def complete(self, prompt: str, allowed_ids: frozenset[str]) -> str: ...


# --- Fallbacks: hybrid -> BM25 -> cache -> refuse ----------------------------
#     Generate: primary -> secondary -> extractive deterministic

@dataclass
class LastGoodCache:
    """Process-local stand-in; production: Redis keyed by
    (index_version, filter, qh, k)."""
    ttl_s: float = 300.0
    _store: dict[str, tuple[float, list[Chunk]]] = field(default_factory=dict)

    def _key(self, query: str, authz: Authz, k: int) -> str:
        raw = f"{authz.tenant_id}|{authz.user_id}|{k}|{query}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, query: str, authz: Authz, k: int) -> list[Chunk] | None:
        rec = self._store.get(self._key(query, authz, k))
        if rec is None:
            return None
        ts, chunks = rec
        if time.monotonic() - ts > self.ttl_s:
            return None
        return chunks

    def put(self, query: str, authz: Authz, k: int, chunks: list[Chunk]) -> None:
        self._store[self._key(query, authz, k)] = (time.monotonic(), chunks)


@dataclass
class DegradedResult:
    chunks: list[Chunk]
    answer: str
    retrieval_degraded: bool
    generation_degraded: bool
    citations: list[str]


class RagRuntime:
    def __init__(
        self,
        hybrid: Retriever,
        bm25: Retriever,
        primary_gen: Generator,
        secondary_gen: Generator,
        cache: LastGoodCache | None = None,
        retrieve_timeout_s: float = 0.4,  # 200-500 ms policy band
        max_hops: int = 3,
    ) -> None:
        self.hybrid = hybrid
        self.bm25 = bm25
        self.primary_gen = primary_gen
        self.secondary_gen = secondary_gen
        self.cache = cache or LastGoodCache()
        self.retrieve_timeout_s = retrieve_timeout_s
        self.max_hops = max_hops
        self.breakers = {
            "hybrid": CircuitBreaker_B("hybrid"),
            "bm25": CircuitBreaker_B("bm25"),
            "primary_gen": CircuitBreaker_B("primary_gen"),
            "secondary_gen": CircuitBreaker_B("secondary_gen"),
        }

    def _call_retriever(
        self, r: Retriever, query: str, authz: Authz, k: int, cid: str
    ) -> list[Chunk]:
        br = self.breakers[r.name]

        def _op() -> list[Chunk]:
            br.allow()
            t0 = time.monotonic()
            try:
                hits = r.search(query, authz, k)
            except PermanentError:
                br.record_failure()
                raise
            except Exception as exc:
                br.record_failure()
                raise TransientError(str(exc)) from exc
            if time.monotonic() - t0 > self.retrieve_timeout_s:
                br.record_failure()
                raise TransientError(f"retrieve_timeout:{r.name}")
            br.record_success()
            return hits

        return retry_with_jitter(
            _op, cid=cid, tenant=authz.tenant_id, op=f"retrieve:{r.name}"
        )  # type: ignore[return-value]

    def retrieve(
        self, query: str, authz: Authz, k: int, cid: str
    ) -> tuple[list[Chunk], bool]:
        """ACL filter is on Authz -- never parsed from model-emitted JSON."""
        degraded = False
        try:
            hits = self._call_retriever(self.hybrid, query, authz, k, cid)
            if hits:
                self.cache.put(query, authz, k, hits)
                return hits, False
            degraded = True
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "hybrid_failed",
                 cid=cid, tenant=authz.tenant_id, err=str(exc))
            degraded = True
        try:
            hits = self._call_retriever(self.bm25, query, authz, k, cid)
            if hits:
                slog(logging.WARNING, "fallback_bm25",
                     cid=cid, tenant=authz.tenant_id)
                return hits, True
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "bm25_failed",
                 cid=cid, tenant=authz.tenant_id, err=str(exc))
        cached = self.cache.get(query, authz, k)
        if cached:
            slog(logging.WARNING, "fallback_cache",
                 cid=cid, tenant=authz.tenant_id)
            return cached, True
        slog(logging.ERROR, "retrieve_exhausted",
             cid=cid, tenant=authz.tenant_id)
        return [], True  # caller must refuse -- never ungrounded generate

    def _generate(
        self, gen: Generator, prompt: str, allowed: frozenset[str],
        cid: str, tenant: str
    ) -> str:
        br = self.breakers[gen.name]

        def _op() -> str:
            br.allow()
            try:
                text = gen.complete(prompt, allowed)
            except PermanentError:
                br.record_failure()
                raise
            except Exception as exc:
                br.record_failure()
                raise TransientError(str(exc)) from exc
            br.record_success()
            return text

        return retry_with_jitter(
            _op, cid=cid, tenant=tenant, op=f"generate:{gen.name}"
        )  # type: ignore[return-value]

    def generate_grounded(
        self, chunks: list[Chunk], question: str, cid: str, tenant: str
    ) -> tuple[str, bool]:
        if not chunks:
            return (
                "I cannot answer: the retrieval index is unavailable and "
                "ungrounded generation is disabled for this corpus.",
                True,
            )
        allowed = frozenset(c.chunk_id for c in chunks)
        # Lost-in-the-middle mitigation: put highest-score chunks at edges.
        ranked = sorted(chunks, key=lambda c: c.score, reverse=True)
        edge = [ranked[0]] + ranked[2:] + ([ranked[1]] if len(ranked) > 1 else [])
        ctx = "\n".join(f"[{c.chunk_id}] {c.text}" for c in edge)
        prompt = (
            f"Answer ONLY from the passages. "
            f"Cite ids in {sorted(allowed)}.\n"
            f"Passages:\n{ctx}\nQuestion: {question}"
        )
        try:
            return self._generate(
                self.primary_gen, prompt, allowed, cid, tenant
            ), False
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "primary_gen_failed",
                 cid=cid, tenant=tenant, err=str(exc))
        try:
            slog(logging.WARNING, "fallback_secondary_gen",
                 cid=cid, tenant=tenant)
            return self._generate(
                self.secondary_gen, prompt, allowed, cid, tenant
            ), True
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "secondary_gen_failed",
                 cid=cid, tenant=tenant, err=str(exc))
        # Deterministic extractive fallback -- no parametric invention.
        titles = ", ".join(c.chunk_id for c in ranked[:3])
        return (
            f"Generation models unavailable. Top retrieved passages: {titles}. "
            "Insufficient evidence to synthesize an answer.",
            True,
        )

    def answer(self, question: str, authz: Authz, k: int = 8) -> DegradedResult:
        cid = str(uuid.uuid4())
        slog(logging.INFO, "query_start",
             cid=cid, tenant=authz.tenant_id, q=question[:200])
        hops = 0
        query = question
        chunks: list[Chunk] = []
        retr_deg = False
        while hops < self.max_hops:
            hops += 1
            chunks, d = self.retrieve(query, authz, k, cid)
            retr_deg = retr_deg or d
            if chunks:
                break
            # Production cap: do not rewrite when retrieve is down.
            if d:
                break
            query = f"{question} (rewrite hop {hops})"
        answer, gen_deg = self.generate_grounded(
            chunks, question, cid, authz.tenant_id
        )
        cites = [c.chunk_id for c in chunks]
        slog(
            logging.INFO, "query_end",
            cid=cid, tenant=authz.tenant_id,
            hops=hops, n_chunks=len(chunks),
            retrieval_degraded=retr_deg,
            generation_degraded=gen_deg,
        )
        return DegradedResult(chunks, answer, retr_deg, gen_deg, cites)


# --- Demo backends (replace with Pinecone/ES/OpenAI clients) -----------------

class StaticRetriever:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def search(self, query: str, authz: Authz, k: int) -> list[Chunk]:
        if self.fail:
            raise TransientError("simulated_outage")
        _ = query
        return [
            Chunk(f"{authz.tenant_id}:policy-1",
                  "Parental leave is 16 weeks.", self.name, 0.9),
            Chunk(f"{authz.tenant_id}:policy-2",
                  "Error TS-999 means payment timeout.", self.name, 0.7),
        ][:k]


class StaticGenerator:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def complete(self, prompt: str, allowed_ids: frozenset[str]) -> str:
        if self.fail:
            raise TransientError("fm_outage")
        _ = prompt
        cid = next(iter(allowed_ids))
        return f"Grounded answer citing [{cid}]."


if __name__ == "__main__":
    authz = Authz(
        tenant_id="acme", user_id="u1",
        acl_filter={"tenant_id": {"$eq": "acme"}}
    )
    runtime = RagRuntime(
        hybrid=StaticRetriever("hybrid", fail=True),
        bm25=StaticRetriever("bm25"),
        primary_gen=StaticGenerator("primary_gen", fail=True),
        secondary_gen=StaticGenerator("secondary_gen"),
    )
    result = runtime.answer("What is TS-999?", authz)
    print(json.dumps({
        "answer": result.answer,
        "citations": result.citations,
        "retrieval_degraded": result.retrieval_degraded,
        "generation_degraded": result.generation_degraded,
    }, indent=2))
