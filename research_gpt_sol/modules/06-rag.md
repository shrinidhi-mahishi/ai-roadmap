# 06 - Retrieval-Augmented Generation

**Scope:** Hybrid search, reranking, Agentic RAG, and Graph RAG.  
**Study goal:** Design RAG as a versioned, permission-aware information system whose retrieval and generation stages can be evaluated independently.

RAG separates model behavior from an updateable evidence corpus. The source system remains authoritative; chunks, vectors, graph edges, summaries, and answer caches are derived projections. A fluent answer is not success unless its material claims are supported by authorized, current evidence.

## 1. System Topology & Data Flow

### Reference topology

```text
                                  CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Identity/RBAC │ source catalog │ model/prompt/index versions │ budgets/evals│
│ retention/residency │ deployment aliases/rollback │ secrets │ release gates │
└───────────────────┬──────────────────────────────────────┬───────────────────┘
                    │ ingestion policy                     │ query policy
                    ▼                                      ▼
                              INGESTION DATA PLANE
┌──────────────┐  CDC/Kafka  ┌──────────────┐  staged     ┌──────────────────┐
│ Sources of   ├────────────►│ Parse/OCR +  ├────────────►│ Chunk/classify/ │
│ truth        │             │ normalize    │             │ ACL/PII policy  │
└──────────────┘             └──────┬───────┘             └────────┬─────────┘
                                    │ artifacts                    │ versioned writes
                                    ▼                              ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE: ingestion ledger │ object artifacts │ sparse/vector indexes   │
│ graph/entities/edges/communities │ ACL snapshot │ generation aliases/DLQ  │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     │ atomic published bundle
                                     ▼
                                QUERY DATA PLANE
┌──────────────┐ auth/purpose ┌──────────────┐ parallel  ┌───────────────────┐
│ Client/API   ├─────────────►│ Query router ├──────────►│ BM25 │ ANN │ graph│
└──────────────┘              │ + ACL filter │           └─────────┬─────────┘
                              └──────┬───────┘                     │ candidates
                                     │ agent plan                  ▼
                              ┌──────▼───────┐              ┌──────────────┐
                              │ Tool/MCP    │              │ RRF + rerank │
                              │ source proxy│              │ + diversify  │
                              └──────┬───────┘              └──────┬───────┘
                                     │ sanitized evidence          │ context
                                     └──────────────┬──────────────┘
                                                    ▼
                                           ┌──────────────────┐
                                           │ Generate/abstain │
                                           │ claim/cite check │
                                           └────────┬─────────┘
                                                    │ traces/metrics/audit
                                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ TELEMETRY: OTel spans │ branch ranks │ quality/cost/freshness │ WORM audit  │
└──────────────────────────────────────────────────────────────────────────────┘
```

The tool/MCP proxy is the only path to remote knowledge sources. It authenticates the server, derives tenant and purpose from trusted identity, applies allowlists and egress policy, and returns delimited evidence. Retrieved text never changes controller policy or grants tool authority.

### End-to-end request flow

1. CDC or a scheduled scanner emits a source-version event. A deterministic key derived from tenant, source ID/version, chunker version, and ordinal makes every ingestion stage idempotent.
2. Isolated workers parse/OCR, normalize hidden content, classify PII, attach source ACLs and validity intervals, chunk, embed, and optionally extract graph entities/edges. Artifacts are committed before the event is acknowledged.
3. Sparse, vector, graph, and summary projections are built under one immutable generation. Validation checks counts, ACL coverage, provenance, sampled retrieval, tombstones, and schema/model compatibility before an alias atomically publishes the bundle.
4. The query API authenticates the caller and pins tenant, purpose, ACL snapshot, index generation, embedding/reranker/prompt versions, deadline, and spend limit. The client cannot supply an arbitrary ACL expression.
5. A deterministic route handles simple lookup directly. A bounded Agentic RAG controller may decompose only approved complex classes and may query only registered sources through the proxy.
6. BM25, dense ANN, and optional graph branches execute concurrently with the same mandatory authorization and generation filters. Their ranks are retained; stable chunk IDs are unioned and fused.
7. A reranker scores a bounded candidate pool. Context assembly removes duplicates, enforces per-source limits, restores only useful adjacency, orders decisive evidence deliberately, and preserves source/version/content hashes.
8. The generator returns structured claims and citations or an explicit abstention. A deterministic verifier ensures cited chunks were authorized, selected, current, and supportive before the API reports a supported result.
9. OTel spans record each stage, while an immutable audit record stores hashes, IDs, versions, policy decisions, degradation, cost, and stop reason. Sensitive raw context has separate access and retention controls.

## 2. Core Mechanics & Algorithms

### 2.1 Hybrid search

**Lexical retrieval.** BM25 rewards query-term matches using inverse document frequency, saturating term frequency, and document-length normalization. It is strong for identifiers, error codes, quotations, rare names, and exact terminology. With an inverted index, a conjunctive/disjunctive query visits postings for its terms rather than scanning `N` documents; work is approximately proportional to the visited postings plus top-`k` maintenance.

**Dense retrieval.** A dual encoder maps query and chunk into a shared vector space and compares dot product or cosine similarity. Exact search over `N` vectors of dimension `d` is `O(Nd)`. ANN indexes trade recall for memory and latency. HNSW uses a multilayer proximity graph; its observed search is often near logarithmic on suitable data, but no workload-independent latency/recall guarantee exists. Tune construction effort, query effort, partitions, and `k` against exact-search samples.

**Late interaction.** ColBERT-style retrieval retains token vectors and takes maximum token-level similarities. It captures richer interactions than one vector per chunk but increases index and query work. It is an alternative branch, not automatically a replacement for exact lexical matching.

**Rank fusion.** BM25 scores and cosine similarities are not calibrated to the same scale. Reciprocal Rank Fusion avoids score normalization:

```text
RRF(d) = Σ_r 1 / (K + rank_r(d))
```

For branch lists totaling `m` hits and `u` unique chunks, accumulation is `O(m)` and sorting all fused candidates is `O(u log u)`; a size-`k` heap reduces selection to `O(u log k)`. The common `K=60` is a starting point, not a universal optimum. Preserve branch rank and score for stage diagnosis.

### 2.2 Reranking and context assembly

A first-stage retriever cheaply narrows millions of items. A cross encoder jointly attends to query and candidate and is typically more precise, but transformer attention is approximately `O(kL^2)` for `k` candidates of sequence length `L`. A hosted reranker trades serving toil for data-boundary and provider risk. A listwise LLM reranker can apply richer instructions but adds tokens, tail latency, and nondeterminism.

The candidate set is a hard ceiling: reranking cannot recover a relevant chunk omitted by retrieval. Measure `Recall@k` before reranking, then measure `nDCG@k` or MRR lift on exactly the same candidate sets. Track truncation because a service may score only a bounded number of candidates or tokens.

Context selection is a constrained optimization over support, token budget, diversity, authority, recency, and redundancy. A practical greedy selector scores marginal evidence gain per token while enforcing source and classification limits. Add neighboring chunks only after a relevant anchor is found. More context can reduce performance through distraction and lost-in-the-middle effects.

### 2.3 Agentic RAG

Agentic RAG makes retrieval a bounded decision process. It can classify retrieval need, decompose a question, choose sources, rewrite subqueries, grade evidence, correct one weak attempt, ask for clarification, or abstain. Self-RAG trains reflection behavior into a model; CRAG adds retrieval evaluation and corrective paths; Adaptive-RAG routes by estimated complexity; HyDE embeds a generated hypothetical document that must never be treated as evidence.

Use an explicit state machine:

```text
RECEIVED ─► CLASSIFIED ─► PLANNED ─► RETRIEVING ─► GRADING
    │            │             ▲          │             │
    │            └─no need─────┤          └─retry───────┘
    │                          │                         │
    └─policy denied────────────┴─────────────────────────┤
                                                       ▼
                        SUPPORTED │ INSUFFICIENT │ CONFLICT │ PARTIAL │ BUDGET
```

With at most `a` attempts, `s` sources per attempt, and `k` candidates per source, retrieval work is bounded by `O(a·s·search(k))`; planner/grader model calls are separately bounded. Store concise plan hashes, queries, filters, evidence IDs, grades, costs, and stop reasons, not hidden reasoning. Termination follows from monotonically consumed attempt, token, cost, and deadline budgets. A no-progress detector stops repeated retrieval of the same evidence set.

### 2.4 Graph RAG

Graph RAG explicitly represents entities, claims, relationships, provenance, time, and sometimes communities. It is not HNSW merely because HNSW is a graph data structure.

- **Local search** seeds entities, performs bounded typed traversal, and resolves returned edges to source chunks.
- **Multi-hop retrieval** uses constrained BFS/path search. Unbounded BFS is `O(|V|+|E|)` and branching-factor cost grows as `O(b^h)` with hop depth `h`; enforce label, tenant, time, degree, and hop bounds.
- **Global search** maps a question over `c` community reports and reduces partial answers, roughly `O(c)` model tasks before retries. It is resource intensive and freshness sensitive.
- **DRIFT-style search** starts from community context and develops bounded follow-up questions.
- **Hierarchical alternatives** such as RAPTOR cluster and summarize text at multiple abstraction levels without asserting entity relationships.

Graph extraction and summaries are fallible derived data. Every node, edge, claim, and community summary carries tenant, source chunk IDs, extractor/prompt/model version, time interval, confidence, and graph generation. Consequential claims resolve back to authoritative text or structured records.

### 2.5 Quality and convergence invariants

| Stage | Primary metrics | Required invariant |
|---|---|---|
| Ingestion | parse/OCR coverage, duplicates, ACL/delete lag | No published item lacks source/version, tenant, ACL, content hash, and generation. |
| Candidate retrieval | Recall@k, MRR, ANN-vs-exact recall, filter correctness | Every branch sees one compatible generation and authorization snapshot. |
| Reranking | nDCG/MRR lift, truncation, stability | Reranked IDs are a subset of authorized candidate IDs. |
| Context | precision/recall, redundancy, source diversity | Every context span resolves to selected immutable evidence. |
| Generation | task correctness, claim support, citation completeness, abstention | Unsupported or conflicting material claims cannot become `SUPPORTED`. |
| Agent path | source selection, retries, stop accuracy, budget breaches | Attempts/calls/sources/tokens/cost/time strictly decrease a finite budget. |
| Graph | entity/edge accuracy, provenance, temporal correctness | No graph claim survives source tombstone or authorization failure. |

RAG quality does not converge by adding `k`, context, or retries indefinitely. Tune each stage on judged domain queries, segment results by query class and tenant policy, and calibrate model-graded metrics against blinded human review.

## 3. Token Economics & NFR Analysis

### 3.1 Lifecycle cost per 1,000 runs

Separate offline index cost from online query cost:

```text
C_index(v) = parse/OCR + embedding + graph extraction/summaries
           + sparse/vector/graph build + storage/replicas/backups

C_1000 = Σ(U·P_in + H·P_cache + W·P_write + O·P_out)/1,000,000
       + 1,000·(query embedding + retrieval + rerank + graph/tool charges)
       + worker/state/trace cost

cost_per_verified_success = (allocated index cost + online cost + repair) /
                            verified successful runs
```

**Illustrative point-in-time assumptions, 2026-08-21:** 1,000 hybrid-reranked generation runs consume 4M uncached input tokens, 6M cached evidence-instruction prefix reads, 25,000 cache-write tokens, and 1.2M output tokens. The cache hit rate is therefore 60% of read input by tokens. Retrieval/reranking infrastructure is assumed to cost `$2.80/1K runs`; embedding, graph indexing, human review, and retries are excluded. Rates use the [current pricing reference](https://developers.openai.com/api/docs/pricing).

| Model tier | No prompt cache / 1K | Cached model cost / 1K | Total with $2.80 retrieval |
|---|---:|---:|---:|
| `gpt-5.6-sol` | `(10M×$5)+(1.2M×$30)` = **$86.00** | `$20+$3+$0.16+$36` = **$59.16** | **$61.96** |
| `gpt-5.6-terra` | `(10M×$2)+(1.2M×$12)` = **$34.40** | `$8+$1.20+$0.06+$14.40` = **$23.66** | **$26.46** |
| `gpt-5.6-luna` | `(10M×$0.20)+(1.2M×$1.20)` = **$3.44** | `$0.80+$0.12+$0.01+$1.44` = **$2.37** | **$5.17** |

Caching is valid only when the provider's exact prefix/cache contract is met; index generation, ACL, and per-user evidence must remain outside a shared cacheable prefix. Key result caches by tenant, principal-policy digest, normalized query, retrieval bundle, and response-policy version. Never serve a cached answer after source tombstone or ACL change.

Architecture changes the multiplier. If 15% of 1,000 queries enter an Agentic RAG path with two additional `terra` calls of 2,000 input and 300 output tokens, the incremental model cost is `150×2×[(2,000×$2 + 300×$12)/1M] = $2.28/1K overall`. A Graph RAG global path mapping 20 community reports at 1,000 input and 150 output tokens each costs `20×[($0.002)+($0.0018)] = $0.076` per query before reduce/retrieval; at 50 global queries per 1K, map cost alone is `$3.80`. These are workload calculations, not benchmark claims.

Allocate index cost by useful lifetime and verified query volume. For example, a `$12,000` graph generation serving 4M verified queries contributes `$3/1K`; if weekly churn forces a rebuild after only 400k queries, it contributes `$30/1K`. Freshness and invalidation can dominate Graph RAG economics.

### 3.2 Latency SLOs

```text
T_total = T_auth + max(T_BM25, T_ANN, T_graph, T_remote)
        + T_fusion + T_rerank + ΣT_agent_steps
        + T_context + T_generation + T_verification
```

These are design targets for an internal production-shaped load test, not public cross-product benchmarks:

| Workload | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| Hybrid ranked results, no generation | ≤ 80 ms | ≤ 250 ms | ≤ 600 ms | parallel branches, warm pools, bounded `k`, lexical fallback |
| Hybrid + rerank + generation | ≤ 1.2 s | ≤ 4 s | ≤ 8 s | stream generation, cache stable prefix, rerank deadline |
| Bounded Agentic RAG | ≤ 3 s | ≤ 12 s | ≤ 25 s | complexity route, parallel sources, max one correction |
| Graph local + source verification | ≤ 2 s | ≤ 8 s | ≤ 18 s | typed/hop limits, hub caps, hybrid fallback |
| Graph global | ≤ 15 s | ≤ 60 s | ≤ 120 s | precomputed reports, map cap, async/status response |

Measure queue time, time to first useful event, terminal verification time, cancellation, cold starts, per-source latency, and degraded-mode rate. Report human wait separately. Batching increases throughput for embeddings/rerankers but adds queue latency; use deadline-aware micro-batches. Hedging is appropriate only for idempotent reads and must not duplicate billable model work uncontrollably.

### 3.3 Throughput and back-pressure

At 500 queries/s, suppose 95% take one two-branch hybrid retrieval, 5% take three two-branch Agentic RAG retrievals, every query reranks 25 candidates, 60% generate, and 1% run a 20-community graph map:

```text
branch searches/s = 500×0.95×2 + 500×0.05×3×2 = 1,100
rerank docs/s     = 500×25 = 12,500
generation/s      = 500×0.60 = 300
graph map tasks/s = 500×0.01×20 = 100
```

Capacity each dependency independently at p99 payload size and with concurrent ingestion. Use weighted admission by predicted branches, candidates, context tokens, graph communities, and model calls. Separate interactive, graph-global, ingestion, tombstone/ACL, rerank, generation, and telemetry queues. Reserve capacity for deletion and permission events; bulk reindexing must not delay revocation.

Back-pressure flows toward admission: cap per-tenant concurrency, bound stream buffers, propagate deadlines/cancellation, and return `202 + status URL`, a narrower query request, or an explicit degraded result. Shed graph-global maps, optional reranking, agent correction, and verbose traces before ACL, tombstone, status, or lexical capacity. Do not retry at the framework, SDK, mesh, and queue simultaneously.

### 3.4 NFR scorecard

| NFR | Target/evidence | Design trade-off |
|---|---|---|
| Availability | 99.9% supported query path; 99.99% auth/status/delete path | More fallback paths require quality labels and tests. |
| RPO | 0 for source events, ACL/tombstones, ledger, publication metadata; ≤ 5 min aggregate telemetry | Derived indexes may rebuild, but deletion history cannot be lost. |
| RTO | ≤ 15 min query/auth path; ≤ 4 h full active-generation rebuild/restore | More replicas/snapshots cost money; source rebuild is slower but auditable. |
| Freshness | ACL/delete p99 ≤ 60 s; governed content p99 ≤ 5 min; graph summaries ≤ agreed hours | Aggressive graph refresh increases extraction and invalidation cost. |
| Quality | query-class Recall@k, nDCG, claim support, citation completeness, calibrated abstention | Higher recall/context can hurt latency and generation quality. |
| Security | zero unauthorized candidate exposure across all branches/caches/fallbacks | Isolated tenancy costs more than filter-only multi-tenancy. |
| Compliance | lineage, residency, retention/deletion verification, vendor DPA, purpose limitation | Hosted reranking/web search may be prohibited. |
| Operability | generation rollback, stage metrics, poison quarantine, quarterly restore drill | Versioned projections consume storage and migration effort. |

There is no stable audited benchmark covering all four RAG modes on one corpus, policy set, hardware, quality judgment, and cost model. Public results guide screening; production sizing requires internal replay, failure injection, ACL tests, and judged queries.

## 4. Distributed Resilience & Security

### 4.1 Durable ingestion and publication

```text
DISCOVERED ─► PARSED ─► POLICY_TAGGED ─► CHUNKED ─► EMBEDDED
     │                                                    │
     │                                                    ▼
     └─► DLQ/REPAIR ◄── VALIDATION_FAILED ◄── SPARSE/VECTOR/GRAPH
                                                      │
                                                      ▼
                                      VALIDATED ─► PUBLISHED
                                                      │
                                        SUPERSEDED / TOMBSTONED
```

Kafka partitions by tenant/source preserve source ordering; a Temporal workflow or equivalent coordinates long OCR, embedding, and graph extraction. Each stage writes a content-addressed artifact and ingestion-ledger transition before acknowledging the message. A deterministic chunk ID makes at-least-once delivery an upsert. Poison items enter a DLQ after a bounded retry budget with stage, digest, and error classification.

Only a publisher with a fenced lease may change a generation alias. It validates expected item counts, ACL/source coverage, graph provenance, compatible embedding dimensions, and canary queries, then atomically points readers to the immutable bundle. In-flight requests retain their pinned old bundle. Rollback changes the alias; it does not mutate the failed generation.

Deletes and ACL changes use the same durable channel and higher-priority queues. Tombstone jobs remove sparse chunks, vectors, graph edges/nodes, summaries, rerank/result caches, and derived artifacts, then record verification. A periodic manifest reconciliation finds orphaned projections. Backups include source manifests, tombstones/ACL history, ingestion ledger, configurations, judgments, and version registry, not opaque vector snapshots alone.

### 4.2 Query failure taxonomy

| Class | Examples | Recovery/degradation |
|---|---|---|
| Transient | ANN timeout, reranker 429, remote-source 503 | deadline-aware full-jitter retry, per-dependency breaker, cancel losers |
| Permanent | invalid filter/schema, embedding dimension mismatch, prohibited source | fail closed; quarantine configuration or request |
| Poison data | parser crash loop, adversarial chunk, corrupt graph event | digest-based attempt count, DLQ, source quarantine |
| Consistency | sparse/vector aliases differ, stale ACL, cache generation mismatch | reject bundle; use prior compatible generation |
| Quality | candidate starvation, truncation, unsupported synthesis | lexical fallback, fused order, abstain; never fabricate |
| Agent | loop/no progress, wrong source, partial failure, budget exhausted | typed terminal state and explicit coverage label |
| Graph | entity collision, false/stale edge, path explosion, orphan provenance | bounded traversal, source resolution, quarantine/rebuild |

Use breakers for encoder, each index, reranker, generator, graph store, remote source, and telemetry sink. Fallback order is dependency-specific: dense failure -> lexical; reranker failure -> RRF order; planner failure -> deterministic single pass; graph-global budget -> local/hybrid or clarification; generator failure -> ranked evidence. Authorization, policy, and incompatible-generation failures never fall open.

### 4.3 Zero-Trust MCP, ACLs, and prompt injection

```text
┌──────────────┐ evidence request ┌────────────────┐ mTLS/OAuth ┌──────────────┐
│ RAG planner  ├────────────────►│ Policy/MCP     ├───────────►│ Approved     │
│ untrusted I/O│                 │ source gateway │            │ source server│
└──────┬───────┘                 └───────┬────────┘            └──────┬───────┘
       │ retrieved text                  │ trusted identity            │ scoped read
       ▼                                 ▼                             ▼
┌──────────────┐                 ┌──────────────┐              ┌──────────────┐
│ Delimited    │                 │ Policy log + │              │ Search/graph │
│ evidence only│                 │ ACL snapshot │              │ filtered data│
└──────────────┘                 └──────────────┘              └──────────────┘
```

Authorize before exposure and ranking. Every lexical, vector, graph, cache, remote-source, reranker, and source-resolution call receives a server-compiled tenant/resource filter. Post-filtering ANN results is both unsafe and recall-damaging because unauthorized candidates were already exposed and may crowd out authorized evidence.

Retrieved content is untrusted data. Delimit and label it; normalize hidden/OCR text; detect instruction-like content; prevent it from changing system policy, source registry, budgets, or tool permissions. The controller proposes source reads, but the gateway re-authorizes concrete `(subject, action, resource, purpose, geography)` and mints short-lived audience-scoped credentials. Browser/code parsing runs in egress-restricted sandboxes.

Tool/source RBAC maps each controller state to the minimum permitted source, operation, tenant scope, and data class; broad planner credentials are prohibited. For high-assurance tenancy, use separate namespaces/indexes and encryption keys where feasible. Graph authorization applies to seed nodes, traversed edges, summaries, and resolved source chunks; an edge can disclose sensitive membership even without returning its text. Bound each source's context contribution to resist corpus flooding and require independent evidence for high-impact claims.

### 4.4 PII, audit, and supply chain

The PII path is `classify -> detect -> redact/tokenize -> authorize use -> model/reranker -> rehydrate only for authorized display -> audit/delete`. Apply it to source text, OCR, metadata, chunks, embeddings, graph properties, prompts, results, caches, and traces. Reversible token maps live in a segregated vault. A hosted reranker or web source is allowed only when residency, retention, training, deletion, encryption, subprocessor, and incident terms satisfy policy.

An immutable answer audit records pseudonymous subject, purpose, policy decision, normalized request hash, exact queries/filters, retrieval bundle, branch candidates/ranks, selected evidence IDs, graph paths, model/prompt versions, claims/citation checks, stop/degradation reason, latency, tokens, and cost. Store content hashes by default; raw evidence has stricter access. Hash-chain/sign WORM batches and log audit access.

Pin parser, OCR, chunker, embedding, reranker, graph extractor, framework, and container dependencies. Generate SBOMs, scan/sign images, validate licenses, and test serialized/index migration. Release gates include offline replay, authorization/deletion tests, poisoning/injection cases, claim/citation/abstention checks, canary traffic, cost/tail thresholds, and rollback drills.

## 5. Production Enterprise Code

The following Python 3.11 standard-library program implements the query core: authorization before retrieval, parallel lexical/dense branches, RRF, bounded deterministic reranking, stable evidence IDs, an Agentic RAG correction budget, structured correlation logs, full-jitter retries, closed/open/half-open circuit breakers, a primary -> secondary -> deterministic extractive generator chain, and dense -> lexical -> abstention retrieval degradation. Replace the in-memory retrievers with pinned service adapters; retain the contracts and invariants. Run with `python rag_pipeline.py`.

```python
from __future__ import annotations

import concurrent.futures
import json
import logging
import math
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Protocol, Sequence


class TransientError(RuntimeError):
    """A retryable dependency failure."""


class PermanentError(RuntimeError):
    """A policy, schema, or contract failure."""


class CircuitOpen(TransientError):
    """The dependency is temporarily disabled."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for key in ("trace_id", "tenant_id", "stage", "degraded", "attempt"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("rag")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class Breaker:
    def __init__(self, threshold: int = 2, recovery_s: float = 5.0):
        self._threshold = threshold
        self._recovery_s = recovery_s
        self._failures = 0
        self._opened_at = 0.0
        self._probe = False
        self._state = "closed"
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if self._state == "open":
                if time.monotonic() - self._opened_at < self._recovery_s:
                    raise CircuitOpen("circuit open")
                self._state = "half_open"
            if self._state == "half_open":
                if self._probe:
                    raise CircuitOpen("half-open probe busy")
                self._probe = True

    def success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._probe = False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe = False
            if self._state == "half_open" or self._failures >= self._threshold:
                self._state = "open"
                self._opened_at = time.monotonic()


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    groups: frozenset[str]
    purpose: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    tenant_id: str
    generation: str
    groups: frozenset[str]
    source_version: str
    text: str


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    branch: str


class Outcome(Enum):
    SUPPORTED = "supported"
    INSUFFICIENT = "insufficient_evidence"
    POLICY_DENIED = "policy_denied"


@dataclass(frozen=True)
class Answer:
    outcome: Outcome
    text: str
    citations: tuple[str, ...]
    degraded: tuple[str, ...]


@dataclass(frozen=True)
class Generated:
    text: str
    citations: tuple[str, ...]
    model: str


class GeneratorModel(Protocol):
    name: str

    def generate(self, query: str, evidence: Sequence[Hit],
                 timeout_s: float) -> str: ...


def authorized(chunk: Chunk, principal: Principal, generation: str) -> bool:
    return (chunk.tenant_id == principal.tenant_id
            and chunk.generation == generation
            and bool(chunk.groups & principal.groups))


class LexicalIndex:
    def __init__(self, chunks: Sequence[Chunk]):
        self._chunks = tuple(chunks)

    def search(self, query: str, principal: Principal,
               generation: str, limit: int) -> list[Hit]:
        terms = set(re.findall(r"[a-z0-9-]+", query.lower()))
        hits = []
        for chunk in self._chunks:
            if not authorized(chunk, principal, generation):
                continue
            words = re.findall(r"[a-z0-9-]+", chunk.text.lower())
            score = sum(1.0 + math.log1p(words.count(term)) for term in terms
                        if term in words)
            if score:
                hits.append(Hit(chunk, score, "lexical"))
        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.chunk_id))[:limit]


class DenseIndex:
    """Deterministic demo adapter; production uses a pinned embedding/ANN service."""

    def __init__(self, chunks: Sequence[Chunk], available: bool = True):
        self._chunks = tuple(chunks)
        self._available = available

    @staticmethod
    def _features(text: str) -> set[str]:
        tokens = re.findall(r"[a-z0-9-]+", text.lower())
        return {token[:5] for token in tokens if len(token) >= 3}

    def search(self, query: str, principal: Principal,
               generation: str, limit: int) -> list[Hit]:
        if not self._available:
            raise TimeoutError("dense index unavailable")
        q = self._features(query)
        hits = []
        for chunk in self._chunks:
            if not authorized(chunk, principal, generation):
                continue
            d = self._features(chunk.text)
            score = len(q & d) / max(1, len(q | d))
            if score:
                hits.append(Hit(chunk, score, "dense"))
        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.chunk_id))[:limit]


def call_with_retry(fn: Callable[[], list[Hit]], breaker: Breaker,
                    deadline: float, trace_id: str, tenant_id: str,
                    stage: str) -> list[Hit]:
    for attempt in range(1, 4):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{stage} deadline")
        try:
            breaker.before()
            value = fn()
            breaker.success()
            return value
        except CircuitOpen:
            raise
        except (TimeoutError, ConnectionError, TransientError):
            breaker.failure()
            logger.warning("retrieval dependency failure",
                           extra={"trace_id": trace_id, "tenant_id": tenant_id,
                                  "stage": stage, "attempt": attempt})
            if attempt == 3:
                raise
            delay = random.uniform(0.0, 0.02 * (2 ** (attempt - 1)))
            if time.monotonic() + delay >= deadline:
                raise TimeoutError(f"{stage} retry exceeds deadline")
            time.sleep(delay)
    raise AssertionError("unreachable")


def rrf(branches: Iterable[Sequence[Hit]], rank_constant: int = 60) -> list[Hit]:
    scores: dict[str, float] = {}
    canonical: dict[str, Hit] = {}
    for branch in branches:
        for rank, hit in enumerate(branch, start=1):
            key = hit.chunk.chunk_id
            scores[key] = scores.get(key, 0.0) + 1.0 / (rank_constant + rank)
            canonical.setdefault(key, hit)
    return [Hit(canonical[key].chunk, score, "rrf")
            for key, score in sorted(scores.items(),
                                     key=lambda item: (-item[1], item[0]))]


def rerank(query: str, candidates: Sequence[Hit], limit: int) -> list[Hit]:
    terms = set(re.findall(r"[a-z0-9-]+", query.lower()))
    scored = []
    for hit in candidates[:50]:
        text_terms = set(re.findall(r"[a-z0-9-]+", hit.chunk.text.lower()))
        relevance = len(terms & text_terms) / max(1, len(terms))
        scored.append(Hit(hit.chunk, relevance + hit.score, "rerank"))
    return sorted(scored, key=lambda hit: (-hit.score, hit.chunk.chunk_id))[:limit]


class GenerationChain:
    def __init__(self, models: Sequence[GeneratorModel]):
        if len(models) < 2:
            raise ValueError("primary and secondary generators are required")
        self._models = tuple(models)
        self._breakers = {model.name: Breaker() for model in models}

    @staticmethod
    def _parse(raw: str, model: str, allowed_ids: frozenset[str]) -> Generated:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PermanentError("generator returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != {"text", "citations"}:
            raise PermanentError("generator output violates exact schema")
        citations = value["citations"]
        if (not isinstance(value["text"], str) or not value["text"].strip()
                or not isinstance(citations, list) or not citations
                or any(not isinstance(item, str) for item in citations)):
            raise PermanentError("generator fields are invalid")
        if not set(citations).issubset(allowed_ids):
            raise PermanentError("generator cited unselected evidence")
        return Generated(value["text"].strip(), tuple(citations), model)

    def generate(self, query: str, evidence: Sequence[Hit], deadline: float,
                 trace_id: str, tenant_id: str) -> Generated | None:
        allowed = frozenset(hit.chunk.chunk_id for hit in evidence)
        for model in self._models:
            breaker = self._breakers[model.name]
            for attempt in range(1, 3):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    breaker.before()
                    raw = model.generate(query, evidence, min(remaining, 3.0))
                    generated = self._parse(raw, model.name, allowed)
                    breaker.success()
                    return generated
                except CircuitOpen:
                    break
                except PermanentError:
                    breaker.failure()
                    break
                except (TimeoutError, ConnectionError, TransientError):
                    breaker.failure()
                    logger.warning("generator dependency failure",
                                   extra={"trace_id": trace_id,
                                          "tenant_id": tenant_id,
                                          "stage": model.name,
                                          "attempt": attempt})
                    if attempt == 2:
                        break
                    delay = random.uniform(0.0, 0.02 * (2 ** (attempt - 1)))
                    if time.monotonic() + delay >= deadline:
                        return None
                    time.sleep(delay)
        return None


class RagService:
    def __init__(self, lexical: LexicalIndex, dense: DenseIndex,
                 generators: GenerationChain):
        self._lexical = lexical
        self._dense = dense
        self._generators = generators
        self._breakers = {"lexical": Breaker(), "dense": Breaker()}

    def answer(self, query: str, principal: Principal, generation: str,
               timeout_s: float = 1.0, max_retrievals: int = 2) -> Answer:
        trace_id = str(uuid.uuid4())
        if not principal.purpose.startswith("support"):
            return Answer(Outcome.POLICY_DENIED, "Request purpose is not allowed.", (), ())
        deadline = time.monotonic() + timeout_s
        degraded: list[str] = []
        previous_ids: frozenset[str] = frozenset()
        query_variant = query

        for retrieval_no in range(max_retrievals):
            calls = {
                "lexical": lambda: call_with_retry(
                    lambda: self._lexical.search(query_variant, principal, generation, 20),
                    self._breakers["lexical"], deadline, trace_id,
                    principal.tenant_id, "lexical"),
                "dense": lambda: call_with_retry(
                    lambda: self._dense.search(query_variant, principal, generation, 20),
                    self._breakers["dense"], deadline, trace_id,
                    principal.tenant_id, "dense"),
            }
            results: dict[str, list[Hit]] = {}
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
            futures = {pool.submit(fn): name for name, fn in calls.items()}
            try:
                for future, name in futures.items():
                    try:
                        results[name] = future.result(
                            timeout=max(0.001, deadline-time.monotonic())
                        )
                    except (TimeoutError, CircuitOpen, concurrent.futures.TimeoutError):
                        degraded.append(f"{name}_unavailable")
            finally:
                for future in futures:
                    future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)

            candidates = rrf(results.values())
            ranked = rerank(query, candidates, limit=4)
            current_ids = frozenset(hit.chunk.chunk_id for hit in ranked)
            supported = [hit for hit in ranked if hit.score >= 0.3]
            if supported:
                generated = self._generators.generate(
                    query, supported, deadline, trace_id, principal.tenant_id
                )
                if generated is None:
                    degraded.append("generators_unavailable")
                    citations = tuple(hit.chunk.chunk_id for hit in supported)
                    text = "Verified evidence: " + " ".join(
                        hit.chunk.text for hit in supported
                    )
                else:
                    citations = generated.citations
                    text = generated.text
                logger.info("rag terminal", extra={"trace_id": trace_id,
                            "tenant_id": principal.tenant_id, "stage": "verified",
                            "degraded": sorted(set(degraded))})
                return Answer(Outcome.SUPPORTED, text, citations,
                              tuple(sorted(set(degraded))))
            if current_ids == previous_ids or retrieval_no + 1 == max_retrievals:
                break
            previous_ids = current_ids
            query_variant = query + " policy documentation"

        return Answer(Outcome.INSUFFICIENT,
                      "Insufficient authorized evidence; escalate for review.", (),
                      tuple(sorted(set(degraded))))


class DemoGenerator:
    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def generate(self, query: str, evidence: Sequence[Hit],
                 timeout_s: float) -> str:
        if not self._available or timeout_s <= 0:
            raise TimeoutError("generator unavailable")
        first = evidence[0]
        return json.dumps({"text": "Policy evidence: " + first.chunk.text,
                           "citations": [first.chunk.chunk_id]})


def main() -> None:
    chunks = (
        Chunk("policy-7#2", "tenant-a", "gen-42", frozenset({"employees"}),
              "policy-7@2026-08", "Travel claims require receipts within 30 days."),
        Chunk("private-9#1", "tenant-b", "gen-42", frozenset({"admins"}),
              "private-9@1", "Confidential unrelated tenant record."),
    )
    generators = GenerationChain((DemoGenerator("primary", False),
                                  DemoGenerator("secondary", True)))
    service = RagService(LexicalIndex(chunks), DenseIndex(chunks, available=False),
                         generators)
    answer = service.answer("When are travel claim receipts required?",
                            Principal("tenant-a", frozenset({"employees"}),
                                      "support.policy"),
                            "gen-42")
    print(json.dumps({"outcome": answer.outcome.value, "text": answer.text,
                      "citations": answer.citations, "degraded": answer.degraded},
                     separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The demo intentionally fails dense retrieval, opens its circuit after bounded retries, and still returns only authorized lexical evidence with `dense_unavailable`. Its primary generator fails before the secondary succeeds. If both generators fail, it returns the selected evidence extractively with `generators_unavailable`; if retrieval also fails or no authorized evidence supports the query, the deterministic outcome is `INSUFFICIENT_EVIDENCE`. A production generator should accept only selected chunk IDs and must emit a schema that the claim/citation verifier can reject.

## 6. Architectural System Design Scenarios

### Scenario 1 - Regulated global policy assistant

**Problem statement.** Design an employee policy assistant for 80,000 staff in 30 jurisdictions at 400 queries/s. Answers must apply role, country, legal entity, and effective date; cite exact policy clauses; propagate revocation within 60 seconds; keep p95 under 4 seconds and p99 under 8 seconds; achieve RPO 0 for ACL/delete events and a 15-minute query-path RTO.

**Proposed architecture.** Use hybrid BM25 + dense retrieval with mandatory temporal/ACL filters in both branches, RRF, and a self-hosted cross encoder over at most 40 chunks. Policies remain authoritative in the document system. Kafka/Temporal builds immutable sparse/vector generations and publishes one compatible alias. Context assembly is extractive and citation-first. A deterministic comparison workflow permits at most two jurisdiction-specific subqueries; unrestricted web and graph extraction are disabled. PostgreSQL stores ingestion/audit ledgers, object storage keeps versioned source artifacts, and WORM keeps answer lineage.

```text
┌──────────────┐ OIDC/purpose ┌──────────────┐ generation ┌──────────────┐
│ Employee UI  ├─────────────►│ Policy API + ├───────────►│ BM25 + ANN   │
│ exact cites  │◄─supported───┤ ACL compiler │            │ pre-filtered │
└──────────────┘              └──────┬───────┘            └──────┬───────┘
                                     │                           │ candidates
                          compare ≤2 │                           ▼
                                     │                    ┌──────────────┐
                                     └───────────────────►│ RRF/rerank  │
                                                          └──────┬───────┘
                                                                 │ evidence IDs
        ┌──────────────┐ Kafka/Temporal ┌──────────────┐          ▼
        │ Policy source├───────────────►│ Versioned    │   ┌──────────────┐
        │ + ACL/delete │                │ index bundle │   │ Claim/cite   │
        └──────────────┘                └──────────────┘   │ verify/abstain│
                                                           └──────────────┘
```

At 400 queries/s with two retrieval branches and 40 rerank candidates, size for 800 branch searches/s and 16,000 reranked chunks/s before headroom. Reserve a high-priority ingestion partition for revocations and tombstones. A reranker outage returns labeled RRF order; a dense outage uses lexical results; policy/ACL outage fails closed.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| **Hybrid + local reranker** | Medium index/serving cost | Predictable bounded path | Medium: two indexes/model | Strong prefilter and private data plane | High with sharding/replicas |
| Dense-only + hosted reranker | Lower lexical operations, provider fees | Good until identifier miss/provider tail | Low-medium | Weaker if protected chunks leave boundary | High but provider/quota dependent |
| Bounded Agentic RAG for every query | Higher planner/retry tokens | Worse and more variable tail | High trajectory/eval burden | More source-selection/injection surface | Lower at same model quota |

**Decision rationale.** Hybrid retrieval matches both exact clause identifiers and semantic questions, while bounded reranking improves precision without allowing an agent loop to dominate latency. The self-hosted reranker satisfies the protected-data boundary. Agentic decomposition is reserved for measured cross-jurisdiction comparisons; Graph RAG adds no demonstrated value to clause lookup.

### Scenario 2 - Global incident investigation

**Problem statement.** Design an analyst system over 12 million narrative incident reports and 5 billion extracted relationships. It must answer entity, multi-hop, and corpus-wide theme questions with source evidence; ingest 2 million updates/day; restrict investigations by case and geography; provide local-search p95 under 8 seconds and asynchronous global reports within 2 minutes; preserve seven-year lineage.

**Proposed architecture.** Build a versioned Graph RAG projection with canonical entity resolution, typed temporal edges, bounded community detection, and precomputed community reports. Retain hybrid chunk retrieval for IDs, quotations, and claim verification. Route entity questions to local typed traversal plus source rerank; route theme questions to a capped map-reduce job through a durable queue. Every graph item links to authorized source versions. Kafka carries source/ACL/tombstone events; Temporal checkpoints extraction and global map tasks; object storage holds artifacts; a graph database and search cluster publish as one bundle; WORM stores lineage.

```text
┌──────────────┐ case scope  ┌──────────────┐ route      ┌──────────────┐
│ Analyst UI   ├────────────►│ Investigation├───────────►│ Local graph  │
│ source drill │◄─status/cite┤ API/policy   │            │ + hybrid text│
└──────────────┘             └──────┬───────┘            └──────┬───────┘
                                    │ global async               │ evidence
                                    ▼                            ▼
                             ┌──────────────┐              ┌──────────────┐
                             │ Temporal map│              │ Source/claim │
                             │ community   │              │ verification │
                             └──────┬───────┘              └──────────────┘
                                    │
┌──────────────┐ Kafka       ┌──────▼───────┐ publish ┌───────────────────┐
│ Reports/ACL/ ├────────────►│ Extract/entity├────────►│ Graph + community │
│ tombstones   │             │ resolve/index│          │ + search bundle   │
└──────────────┘             └──────────────┘          └───────────────────┘
```

At 2 million updates/day, average ingestion is 23/s, but capacity for the measured burst and reprocessing, not the mean. A 100-community cap at 20 concurrent global requests creates up to 2,000 map tasks; admit these on a separate queue. Traversal enforces hop/degree/label/time/case bounds before resolving evidence. A stale or unsupported edge is excluded, not phrased as a fact.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| Hybrid chunks only | Lowest offline cost | Fast local lookup; weak global synthesis | Medium | Mature ACL filtering | High search scale, limited relation semantics |
| RAPTOR-style hierarchy | Medium summarization cost | Good document/global abstraction | High invalidation/versioning | Summary authorization required | High if tree partitions cleanly |
| **Graph RAG + hybrid verification** | Highest extraction/storage cost | Local bounded; global async | Highest entity/community operations | Strong only with edge/source ACL and provenance | High with partitioned graph/map queues |

**Decision rationale.** The workload explicitly requires typed relationships, multi-hop paths, and corpus-wide themes, so Graph RAG earns its added cost. Hybrid evidence verification prevents inferred edges or summaries from becoming unsupported conclusions. RAPTOR remains the bake-off baseline for theme questions; adoption depends on measured analyst task success, entity/edge accuracy, update invalidation, p95/p99, and cost per verified investigation.

## Interview Review

1. **Why hybrid search?** Lexical and dense signals fail differently; fuse ranks under identical ACL and generation filters, then diagnose each branch.
2. **Why cannot reranking fix low recall?** A reranker only reorders candidates it receives.
3. **What makes Agentic RAG production-safe?** Typed states, approved sources, finite attempt/token/cost/deadline budgets, no-progress detection, durable evidence records, and explicit abstention.
4. **When is Graph RAG justified?** Measured relationship, multi-hop, or corpus-global work where hybrid/hierarchical baselines underperform enough to pay extraction and invalidation costs.
5. **What is the key security invariant?** Authorization occurs inside every retrieval branch before content reaches fusion, reranking, generation, caches, or remote services.
6. **What makes an answer reproducible?** Pinned source versions, ACL snapshot, index bundle, candidates/ranks, graph paths, selected evidence, and model/prompt/policy versions.

## Primary References

- [Original RAG formulation](https://arxiv.org/abs/2005.11401)
- [BM25 and probabilistic relevance](https://doi.org/10.1561/1500000019)
- [Dense Passage Retrieval](https://arxiv.org/abs/2004.04906)
- [HNSW](https://arxiv.org/abs/1603.09320)
- [Reciprocal Rank Fusion](https://research.google/pubs/reciprocal-rank-fusion-outperforms-condorcet-and-individual-rank-learning-methods/)
- [BERT passage reranking](https://arxiv.org/abs/1901.04085)
- [Self-RAG](https://arxiv.org/abs/2310.11511)
- [Corrective RAG](https://arxiv.org/abs/2401.15884)
- [Adaptive-RAG](https://arxiv.org/abs/2403.14403)
- [Microsoft GraphRAG paper](https://www.microsoft.com/en-us/research/publication/from-local-to-global-a-graph-rag-approach-to-query-focused-summarization/)
- [GraphRAG query modes](https://microsoft.github.io/graphrag/query/overview/)
- [RAPTOR](https://arxiv.org/abs/2401.18059)
- [BEIR](https://arxiv.org/abs/2104.08663)
- [RAGChecker](https://arxiv.org/abs/2408.08067)
- [OWASP vector and embedding weaknesses](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)
- [Indirect prompt injection](https://arxiv.org/abs/2302.12173)
