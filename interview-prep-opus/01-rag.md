# Module 01: Retrieval-Augmented Generation (RAG)

## What Is This?

RAG is like an open-book exam. Instead of memorizing every fact, the model looks up relevant information from your documents before answering. You give the LLM a search engine for your private data, and it reads the top results before generating a response.

Without RAG, an LLM only knows what was in its training data. With RAG, it can answer questions about your company's internal docs, yesterday's support tickets, or this morning's policy update -- things no pre-trained model could possibly know.

The basic flow: user asks a question, the system searches a database of your documents for relevant passages, feeds those passages alongside the question to the LLM, and the LLM writes an answer grounded in the retrieved evidence.

## Why It Matters

RAG is the dominant pattern for enterprise AI in 2026 because it solves the two hardest LLM problems simultaneously: knowledge staleness (the model doesn't know your data) and hallucination (the model invents plausible-sounding nonsense). Enterprise intent to adopt hybrid retrieval tripled from 10.3% to 33.3% in Q1 2026. If you're interviewing for a Director/VP AI role, you will be asked to design, scale, or debug a RAG system -- it's the most deployed GenAI pattern in production today.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CONTROL PLANE                                  │
│  ┌────────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────────┐  │
│  │   Query     │  │  Orchestrator │  │  Eval Loop │  │ Observability │  │
│  │   Router    │──│  (LangGraph)  │──│  (RAGAS /  │──│  (Langfuse /  │  │
│  │             │  │              │  │  DeepEval) │  │  OTel)        │  │
│  └─────┬──────┘  └──────┬───────┘  └────────────┘  └───────────────┘  │
│        │                │                                               │
├────────┼────────────────┼───────────────────────────────────────────────┤
│        │         DATA PLANE                                             │
│        ▼                ▼                                               │
│  ┌────────────┐  ┌──────────────┐                                      │
│  │ Embedding  │  │   Reranker   │                                      │
│  │ Service    │  │ (Cross-Enc.) │                                      │
│  └─────┬──────┘  └──────┬───────┘                                      │
│        │                │                                               │
│        ▼                ▼                                               │
│  ┌────────────────────────────────────────────┐                        │
│  │         HYBRID RETRIEVER                    │                        │
│  │  ┌──────────────┐  ┌────────────────────┐  │                        │
│  │  │ Dense Vector  │  │  Sparse (BM25 /   │  │                        │
│  │  │ Index         │  │  SPLADE)           │  │                        │
│  │  └──────┬───────┘  └────────┬───────────┘  │                        │
│  │         └────────┬──────────┘              │                        │
│  │                  ▼                          │                        │
│  │          RRF Fusion (k=60)                  │                        │
│  └─────────────────────────────────────────────┘                        │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                     PERSISTENCE LAYER                                   │
│  ┌────────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────────┐  │
│  │ Vector DB  │  │  Document    │  │  Metadata  │  │  Audit Log    │  │
│  │ (Qdrant /  │  │  Store       │  │  + RBAC    │  │  (Immutable)  │  │
│  │  pgvector) │  │  (S3/GCS)    │  │  Tags      │  │               │  │
│  └────────────┘  └──────────────┘  └────────────┘  └───────────────┘  │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                     INGESTION PIPELINE                                  │
│  ┌────────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────────┐  │
│  │ Source      │  │  Chunker     │  │  Embedding │  │  Indexer      │  │
│  │ Connectors │──│  (Recursive  │──│  Model     │──│  (Batch)      │  │
│  │            │  │   512-1024t) │  │            │  │               │  │
│  └────────────┘  └──────────────┘  └────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Request-Flow Narrative

**Ingestion (offline)**:
1. Source connectors pull documents from S3, Confluence, SharePoint, databases.
2. Chunker splits each document using recursive splitting at 512-1024 tokens with 10-20% overlap. Metadata tags (source, timestamp, RBAC permissions, data subject IDs for GDPR) are attached to each chunk.
3. Embedding model (e.g., text-embedding-3-large or self-hosted BGE-M3) converts each chunk into a dense vector.
4. Indexer writes vectors + metadata to the vector DB and BM25-compatible text to the sparse index.

**Query (online)**:
1. User query arrives. The query router classifies complexity (simple factoid vs. multi-hop vs. synthesis) and routes to the appropriate retrieval strategy.
2. Embedding service converts the query to a dense vector.
3. Hybrid retriever fires two parallel searches: dense vector similarity (top 50-100) and BM25/SPLADE sparse search (top 50-100).
4. RRF fusion merges candidate lists: `score(d) = SUM(1/(k + rank(d)))`, k=60. This consistently outperforms either retriever alone (NDCG 0.7068 vs BM25 0.6983 vs KNN 0.6953 on WANDS benchmark).
5. Cross-encoder reranker jointly scores the top 50 fused candidates. This is the single largest precision gain in the pipeline: Recall@5 jumps from 0.695 to 0.816 (+17.4%), MRR@3 from 0.433 to 0.605 (+39.7%).
6. RBAC + freshness filter removes any chunks the user lacks permission to see and any chunks past their staleness threshold.
7. Top 5-10 reranked passages plus the query go to the LLM with citation metadata.
8. LLM generates an answer with inline citations.
9. Eval loop (async) scores faithfulness, answer relevancy, and context recall on a sample of production traffic.

---

## Part 2: Core Mechanics & Algorithms

### Chunking Strategies

| Strategy | Throughput | Accuracy (Vecta 2026) | Best For |
|---|---|---|---|
| Fixed-size (200-word) | ~4.82 MB/s | Matches semantic on many tasks | Simple docs, fast indexing |
| Recursive (512-token) | Moderate | 69% (top in benchmark) | Production default |
| Semantic (similarity) | ~0.33 MB/s (14x slower) | 54-70% (variable) | Topic-dense documents |
| Late chunking | Embedding-only cost | Cuts top-20 retrieval failures ~67% with reranking | Context preservation |
| Agentic (LLM-decided) | 10-50x indexing cost | Highest retrieval quality | High-value corpora |
| Hierarchical/Parent-Child | Moderate | Surgical search + rich context | Long documents |

**2026 consensus**: Recursive splitting is the production default. Semantic chunking only when eval proves it justifies the 10x processing cost. Optimal chunk size: 512-1024 tokens. A "context cliff" at ~2,500 tokens where response quality degrades.

### Embedding Models

| Model | MTEB Score | $/1M Tokens | Dimensions | Context |
|---|---|---|---|---|
| Qwen3-Embedding-8B | ~70.6 | Self-hosted | Variable | 32K |
| Jina v5-text | 71.7 (MTEB v2) | TBD | -- | -- |
| Cohere embed-v4 | 65.2 | $0.01-$0.10 | 1,024 | 128K |
| OpenAI text-embedding-3-large | 64.6 | $0.13 | 3,072 (Matryoshka) | 8,192 |
| OpenAI text-embedding-3-small | ~62 | $0.02 | 1,536 (Matryoshka) | 8,192 |
| BGE-M3 | ~63.0 | Free (MIT) | 1,024 | 8,192 |

**Critical caveat**: MTEB is a useful prior, not a decision oracle. One legal retrieval system found the MTEB top-3 models ranked 5th, 7th, and 2nd on their in-domain eval while BGE-large-en-v1.5 (MTEB rank 11th) won. Always run in-domain evaluation.

### Hybrid Search: RRF Fusion Algorithm

Reciprocal Rank Fusion merges results from multiple retrievers:

```
score(d) = SUM over all retrievers r of: 1 / (k + rank_r(d))
```

- k = 60 is the standard default (reduces the influence of high-ranking outliers).
- Tuned hybrid RRF reaches NDCG 0.7497 on WANDS -- 7.5% above either retriever alone.
- Time complexity: O(n * m) where n = candidates per retriever, m = number of retrievers. In practice, n=100, m=2, so negligible.

### Cross-Encoder Reranking

Unlike bi-encoders (encode query and document independently), cross-encoders process (query, document) pairs jointly through a transformer. This enables token-level interaction between query and document tokens.

- Precision-recall tradeoff: much higher precision, but O(n) inference calls (one per candidate), so applied only to top 50 fused results.
- ColBERT (late interaction): per-token embeddings with MaxSim scoring. Sits between bi-encoder and cross-encoder on latency/quality tradeoff. Qdrant supports it natively.

### Agentic RAG State Machine

```
                    ┌──────────────┐
                    │   INITIAL    │
                    │   QUERY      │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
               ┌───│   RETRIEVE   │───┐
               │   └──────┬───────┘   │
               │          │           │
         insufficient   sufficient   no results
         evidence       evidence
               │          │           │
               ▼          ▼           ▼
        ┌────────────┐ ┌──────┐ ┌──────────┐
        │  REWRITE   │ │ANSWER│ │  DECLINE  │
        │  QUERY     │ │      │ │           │
        └──────┬─────┘ └──────┘ └───────────┘
               │
               ▼
        (loop back to RETRIEVE, max 3 rounds)
```

The model retrieves inside its reasoning loop -- rewrites queries, asks for more evidence, or stops early. Carnegie Mellon (June 2026): hallucinations fell from 14.1% to 4.9% on a 9,000-question financial-compliance dataset at ~220ms extra latency per round.

### Graph RAG

Microsoft's GraphRAG uses LLM-driven entity extraction + Leiden algorithm for hierarchical community detection. Payoff: cross-document questions where flat semantic search fails. Cost: expensive indexing (every document goes through entity extraction). Graph-O1 uses Monte Carlo Tree Search to explore graph nodes without exceeding context limits.

**Key invariant**: Agentic RAG with knowledge graphs cut hallucination by ~62% across 47 production deployments (May 2026 MLOps Community benchmark).

### Vector Database Selection

| Database | QPS (1M) | p99 Latency (10M) | Best For | Cost |
|---|---|---|---|---|
| pgvector | ~640 | 5-8ms | <10M vectors, existing Postgres | $0 (existing infra) |
| Qdrant | ~1,840 | ~12ms | Latency-critical, complex filters | $600-$1,200/mo |
| Weaviate | ~1,620 | ~16ms | Native hybrid search, multimodal | Moderate |
| Pinecone | ~1,620 | Varies | Zero-ops, quick scaling | $1,500-$3,000/mo |
| Milvus | High | Low | Billion-vector scale | Self-hosted |

**Scale reversal**: At 50M vectors, pgvectorscale (471 QPS) outperforms Qdrant (41.47 QPS). Above 1B vectors, only Vespa and Milvus distributed deployments are production-grade.

---

## Part 3: Token Economics & NFR Analysis

### Cost Per 1K Queries

| Component | Cost/1K Queries | Notes |
|---|---|---|
| Embedding (query) | $0.001 - $0.009 | 50 tokens/query; negligible |
| Vector search | $0.01 - $0.05 | pgvector near-zero; Pinecone ~$0.05 |
| Reranking | $0.10 - $0.50 | Cross-encoder on 50 candidates |
| LLM inference | $1.00 - $15.00 | 8K input + 400 output; 60-75% of total |
| **Total (simple RAG)** | **~$1.50 - $5.00** | |
| **Total (agentic, 4 rounds)** | **~$30 - $200** | 20-40x simple RAG |

**Cost formula**:
```
Monthly cost = (queries/day) * 30 * cost_per_query
             + vector_db_hosting
             + embedding_refresh_cost / refresh_interval_months
```

**Corpus embedding cost**: 100M-token knowledge base costs ~$13K with text-embedding-3-large or $0 with self-hosted BGE-M3. Switching from 3072-dim to 768-dim reduced total system cost by 55% while maintaining 92% retrieval accuracy (Matryoshka dimensionality reduction).

### Production Cost by Scale

| Scale | Monthly Cost | Key Driver |
|---|---|---|
| Small (<10K queries/mo) | $150-$400 | LLM inference |
| Mid-size (10K-100K) | $600-$1,500 | LLM inference + vector DB |
| Enterprise (1M queries/mo) | $5,000-$15,000 | All components at scale |
| Demo vs production | $340/mo vs $61,000/mo | Documented case study |

**Build cost**: Basic prototype $10K-$25K. Production hybrid retrieval $25K-$60K. Enterprise agentic RAG $60K-$150K+.

### Latency SLA Targets

| Stage | p50 | p95 | p99 | Mitigation |
|---|---|---|---|---|
| Embedding generation | ~70ms | ~100ms | ~120ms | Batch embeddings, local model |
| Hybrid retrieval | ~6ms added | ~10ms | ~15ms | BM25 + vector in parallel |
| Cross-encoder rerank (50) | ~100ms | ~200ms | ~300ms | Lighter reranker, reduce candidates |
| LLM generation | ~1000ms | ~2000ms | ~3000ms | Prompt caching, streaming |
| **Simple RAG total** | **~700ms** | **~2s** | **~3s** | |
| **Agentic RAG (3 rounds)** | **~8s** | **~15s** | **~20s** | Parallel retrieval, early stopping |

### Throughput & Availability

- **Throughput**: pgvector handles ~640 QPS at 1M vectors on a single node. For >1K QPS, use Qdrant (1,840 QPS) or horizontal sharding.
- **Availability target**: 99.9% (8.7 hours downtime/year). Achieved via multi-AZ vector DB deployment + LLM provider failover (OpenAI -> Anthropic -> self-hosted).
- **RPO/RTO**: RPO = 0 for vector index (replicated). RTO = <5 minutes with pre-warmed standby. For corpus re-embedding after model upgrade: budget 4-8 hours for 100M tokens.

---

## Part 4: Distributed Resilience & Security

### Durable Execution Patterns

**Platform landscape (mid-2026)**:
- **LangGraph persistence**: Saves graph state at each superstep, organizes runs by thread. Strongest agent-native checkpointing.
- **Temporal**: Durable execution with automatic retries, timeouts, state persistence. Ideal for multi-step RAG pipelines (ingest -> chunk -> embed -> index).
- **DBOS**: Database-backed workflow persistence with AI stack integrations.
- **AWS Lambda Durable Functions**: Steps, waits, checkpoints, replay, retries (December 2025).

### Failure Taxonomy

**When RAG fails, the failure point is retrieval 73% of the time, not generation.**

| Failure Mode | Type | Detection | Mitigation |
|---|---|---|---|
| Retrieval drift | Gradual | Context recall metric dropping | Async eval loop on production traffic |
| Stale embeddings | Permanent | Model version mismatch in metadata | Pin embedding model; re-embed on upgrade |
| Content freshness | Gradual | Source timestamp vs retrieval timestamp | Staleness dashboard, TTL on chunks |
| Context poisoning | Adversarial | Injection detection in retrieved chunks | Input sanitization, source provenance |
| Access control leakage | Permanent | Unauthorized content in responses | RBAC at retrieval layer, not prompt layer |
| Conflicting sources | Semantic | Accuracy drops on reconciliation queries | Source ranking, temporal precedence rules |

**Critical insight**: Nested retries create self-inflicted outages. An LLM loop retries a tool call, the SDK retries the API request, the workflow engine retries the step, and the provider retries internally. Production systems need **global retry budgets** across the entire run.

### Poison-Pill Detection

- **EchoLeak (late 2025)**: Unclicked email manipulated Microsoft 365 Copilot's RAG pipeline, exfiltrating corporate data.
- **March 2026 mass poisoning**: Flooded external knowledge bases with manipulated data, forcing AIs to push false information to millions.
- Detection: hash-based integrity checks on ingested content, source reputation scoring, anomaly detection on chunk content distributions.

### Zero-Trust RAG Architecture

Security layers across the full pipeline:

1. **User Layer**: Authentication, authorization, identity verification.
2. **Input Layer**: Sanitization filters for prompt injection.
3. **Retrieval Layer**: RBAC + ABAC enforced at both index-time and query-time. Document-level permissions during retrieval is the most effective defense against data leakage.
4. **Model Layer**: LLM generation with guardrails.
5. **Output Layer**: Response scanning for PII, secrets, and unauthorized content.

**Key principle**: A model cannot be trusted to "unsee" unauthorized context. Access control must sit inside retrieval, not only around the final answer.

### PII Filtering & Compliance

| Framework | Key RAG Requirements |
|---|---|
| HIPAA | AES-256 encrypted PHI storage, RBAC, immutable audit logging, de-identification, MFA |
| SOC 2 | Type II audit trails for all model interactions, data access controls |
| GDPR | Data subject vector tracking for deletion requests, right to erasure across all chunks |

**GDPR deletion challenge**: Every fragmented, embedded vector chunk related to a data subject must be destroyed. Requires rigorous metadata tagging during ingestion with data subject IDs on every chunk.

### Audit Trails

Mandatory: immutable, hash-chained logs for every query, denial, and label change, with inline PII/PHI masking in snippets and prompts. Use a centralized gateway that mediates all model calls.

---

## Part 5: Production Enterprise Code

```python
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

# ─── Structured Logging ────────────────────────────────────────────────

logger = structlog.get_logger()


def create_correlation_id() -> str:
    return str(uuid.uuid4())[:12]


# ─── Circuit Breaker ───────────────────────────────────────────────────

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


# ─── Retry with Exponential Backoff + Jitter ──────────────────────────

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


# ─── Fallback Chain ───────────────────────────────────────────────────

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


# ─── RAG Pipeline ─────────────────────────────────────────────────────

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

        # Step 4: RBAC filtering
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
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Multi-Tenant Enterprise Knowledge Assistant

**Problem Statement**: A B2B SaaS company needs a RAG-powered assistant that serves 200 enterprise customers, each with 50K-500K proprietary documents. Documents include HR policies, engineering runbooks, and customer contracts. Strict data isolation is non-negotiable (SOC 2 Type II, some customers require HIPAA). Target: <2s p95 latency, 99.9% availability, <$15K/month total infrastructure.

**Proposed Architecture**:

```
┌──────────────────────────────────────────────────────────┐
│                   API GATEWAY (Auth + Rate Limiting)      │
│                          │                                │
│                          ▼                                │
│  ┌────────────────────────────────────────────────────┐  │
│  │  TENANT ROUTER  (tenant_id -> namespace mapping)   │  │
│  └──────────┬─────────────────────────────────────────┘  │
│             │                                             │
│             ▼                                             │
│  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │ pgvector          │  │ Qdrant (large tenants only)  │  │
│  │ (per-tenant       │  │ (>100K docs, latency-        │  │
│  │  schema)          │  │  critical SLAs)               │  │
│  └──────────────────┘  └──────────────────────────────┘  │
│             │                                             │
│             ▼                                             │
│  ┌──────────────────────────────────────────────────────┐│
│  │  LLM FALLBACK CHAIN:                                 ││
│  │  Claude Sonnet 4.6 -> GPT-4.1 -> Self-hosted Llama  ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **A: Shared pgvector, namespace isolation** | Cheapest ($0 extra DB cost), simple ops | Noisy neighbor risk, RBAC complexity, SOC 2 auditors prefer physical isolation | Use for small tenants (<50K docs) |
| **B: Dedicated Qdrant per tenant** | Strong isolation, best latency | $600-$1,200/mo per tenant, 200 tenants = $120K-$240K/mo | Only for largest tenants on premium tier |
| **C: pgvector per-schema + Qdrant for top 10** | Cost-effective isolation, latency SLA for top accounts | Two systems to operate | **Selected** |

**Decision Rationale**: pgvector with per-tenant schemas provides logical isolation auditable for SOC 2 at near-zero marginal cost. The top 10 tenants (by document count and latency SLA) get dedicated Qdrant namespaces. At 50M total vectors across all tenants, pgvectorscale (471 QPS) outperforms Qdrant at this scale. The LLM fallback chain ensures 99.9% availability even during provider outages. Total cost: ~$8K/month (pgvector on existing RDS + 2 Qdrant nodes + LLM inference).

---

### Scenario 2: Real-Time Compliance RAG for Financial Services

**Problem Statement**: A financial services firm needs a RAG system that answers compliance questions from 500 analysts. The regulatory corpus (SEC filings, FINRA rules, internal policies) changes weekly. Answers must cite exact source paragraphs. Temporal reasoning is critical ("What was the margin requirement for crypto ETFs as of March 2026?"). Zero tolerance for stale answers. HIPAA not required, but SOC 2 and internal audit trail mandatory.

**Proposed Architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│                     INGESTION (WEEKLY + EVENT-DRIVEN)            │
│  ┌──────────┐  ┌─────────────┐  ┌──────────┐  ┌────────────┐  │
│  │ SEC EDGAR │  │ FINRA Rules │  │ Internal │  │ Freshness  │  │
│  │ Crawler   │  │ Feed        │  │ Policies │  │ Tracker    │  │
│  └─────┬────┘  └──────┬──────┘  └────┬─────┘  └─────┬──────┘  │
│        └───────────────┴──────────────┴───────────────┘         │
│                          │                                       │
│                          ▼                                       │
│        ┌─────────────────────────────────┐                      │
│        │ TEMPORAL METADATA ENRICHMENT     │                      │
│        │ (effective_date, expiry_date,    │                      │
│        │  supersedes_doc_id)              │                      │
│        └──────────────┬──────────────────┘                      │
│                       ▼                                          │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ HYBRID INDEX (Qdrant + Elasticsearch BM25)              │    │
│  │ + Knowledge Graph (Neo4j: regulation -> supersedes ->   │    │
│  │   cites -> amends relationships)                        │    │
│  └─────────────────────────────────────────────────────────┘    │
│                       │                                          │
│                       ▼                                          │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ AGENTIC RAG (max 3 retrieval rounds)                    │    │
│  │ Round 1: Direct retrieval                               │    │
│  │ Round 2: Graph traversal for superseding docs           │    │
│  │ Round 3: Temporal reconciliation (conflicting sources)  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                       │                                          │
│                       ▼                                          │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ CITED GENERATION with temporal qualifiers               │    │
│  │ ("As of [date], per [source], the requirement is...")   │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **A: Flat hybrid RAG (no temporal awareness)** | Simpler, cheaper, 1.5s latency | ERAA-2026 shows 29% hallucination on temporal queries vs 9% atemporal. Conflicting source accuracy drops from 95% to 61%. Unacceptable for compliance. | Rejected |
| **B: Agentic RAG + knowledge graph** | Handles temporal queries, resolves superseded docs, 62% hallucination reduction | 5-15s latency, 20-40x cost of simple RAG, complex ops | **Selected** |
| **C: Full Graph RAG (Microsoft GraphRAG)** | Best at cross-document synthesis | Very expensive indexing, overkill for structured regulatory corpus with clear relationships | Rejected for this use case |

**Decision Rationale**: Compliance cannot tolerate the 29% temporal hallucination rate of flat RAG. The knowledge graph captures explicit regulatory relationships (supersedes, amends, cites) that semantic similarity alone cannot model. The LinkedIn case study showed 28.6% faster issue resolution with knowledge graph augmentation. Agentic RAG with max 3 rounds provides the retrieval depth needed for multi-hop compliance questions ("What current rules apply to X, given that Rule Y was superseded by Rule Z in March 2026?"). The 5-15s latency is acceptable for analyst-facing compliance queries where accuracy matters more than speed. Audit trail with hash-chained immutable logs satisfies SOC 2. Total cost: ~$25K/month (agentic RAG at ~$60-200 per 1K queries, 500 analysts averaging 10 queries/day).
