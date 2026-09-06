# Module 15: Embeddings & Vector Databases

## What Is This?

An **embedding** is a list of numbers that captures the meaning of a piece of text, image, or audio. Think of it like GPS coordinates for meaning: just as two nearby GPS points are geographically close, two similar embeddings are semantically close. "How do I reset my password?" and "I forgot my login credentials" end up near each other in this number space, even though they share zero words.

A **vector database** is a specialized storage engine that finds the nearest neighbors of a given embedding among millions or billions of stored embeddings -- fast. Traditional databases answer "give me rows where column = X." Vector databases answer "give me the items most similar to this."

**Analogy**: Imagine a library where every book has coordinates on a giant map based on its topic and style. When a reader walks in with a question, the librarian does not scan every shelf -- she looks at the map, finds the neighborhood matching the question, and pulls the closest books. The embedding model draws the map. The vector database is the librarian.

**The vector index is not RAG.** RAG is retrieve-then-generate (chunking, reranking, generation). This layer is the barcode printer and warehouse racking: an embedding model maps text to a vector, an ANN index finds near-neighbors under a pinned metric, and a codec may store those vectors compressed. Generation, RRF fusion, reranking, and GraphRAG are separate concerns.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
                         TELEMETRY / OBSERVABILITY SINKS
         +------------------------------------------------------------------+
         |  recall@k canaries (filter-selectivity slices)                    |
         |  nDCG@10 golden set (in-domain; not MTEB as SLO)                 |
         |  RU / QPS / WU meters   embed TPM/RPM   cache hit-rate           |
         |  invert-surface: include_values audits (must be false for asst.) |
         |  WORM: (cid, index, ns, filter_digest, k, include_values=false)  |
         +-----^--------------------^--------------------^------------------+
               | spans              | meters             | audit
               |                    |                    |
+--------------+--------------------+--------------------+------------------+
| CONTROL PLANE  (schema create -- LLM-free; allow/deny is the predicate)  |
|                                                                           |
|  +----------+ +----------+ +----------+ +----------+ +---------+         |
|  | model_id | | dimension| | metric   | | index    | | codec   |         |
|  | pin      | | 256-4096 | | cos/IP/L2| | type     | | SQ/PQ/  |         |
|  | (Voyage-4| | MRL      | | sparse=  | | HNSW/IVF/| | BQ/BBQ/ |         |
|  | family)  | | prefix   | | dotprod  | | Ananas   | | halfvec |         |
|  +----------+ +----------+ +----------+ +----------+ +---------+         |
|                                                                           |
|  Schema pin = model_id + dimension + metric + index type + codec          |
|  Changing ANY element = full re-embed and/or rebuild                      |
|  Pinecone: you do NOT set M/ef -- slabs pick Ananas / PQFS / IVF         |
+-------------------------------+-------------------------------------------+
                                | pinned schema + alias pointer
                                v
+-----------------------------------------------------------------------+
| DATA PLANE  (untrusted query text -- embed, then ANN)                 |
|                                                                       |
|  q_text -> PII redact -> query embed (or cache hit) -> optional norm  |
|         -> ANN walk + payload bitmap -> oversample -> rescore -> IDs  |
|                                                                       |
|  +--- TOOL PROXIES (least privilege -- not an omnibus dump) ---------+|
|  | embed client (OpenAI/Voyage/Cohere/self-host)  vector-DB SDK      ||
|  | MCP behind gateway PEP:  query_index | upsert | fetch_values      ||
|  |   query_index: k + filter from verified auth -- NOT from tool args||
|  |   upsert: ingest workers only; idempotent chunk_id keys           ||
|  |   fetch_values / include_values: DENY for assistants              ||
|  | Identity = verified token. MCP is not the namespace PDP           ||
|  +-------------------------------------------------------------------+|
+--------+---------------+-----------------+----------------------------+
         |               |                 |
         v               v                 v
+-----------------------------------------------------------------------+
| PERSISTENCE LAYER  (files != query path; restore = NEW index + flip)  |
|                                                                       |
| Pinecone WAL on S3  | Qdrant/Weaviate WAL+HNSW | pgvector Postgres   |
| immutable slabs/ns  | snapshots, RF on shards   | PITR, hnsw/ivfflat  |
| Alias/name swap = promotion. Dual-index during model migrate.         |
+-----------------------------------------------------------------------+
```

### Request Flow Narrative

1. **Control / construction (once).** Application creates the index with pinned `model_id`, `dimension`, `metric`, index type, codec, tenancy scheme, and alias pointer. Payload fields used in filters get payload indexes before ingest.
2. **Ingest (write path, independently scaled).** PII detect-redact-audit, then embed batch (up to 2048 inputs / 300k tokens), then idempotent upsert keyed by `chunk_id`. Pinecone write ack is under 100ms to WAL; visibility to queries is seconds (memtable then slabs). This is not the query SLO.
3. **Query admit.** Gateway PEP binds `tenant_id` from the verified JWT, not from tool JSON. Filter predicate is composed from auth context. `k` and `include_values=false` are policy, not model suggestions.
4. **Query embed.** Cache key = `(model_id, dim, metric, normalized_query_hash)`. Hit: skip embed cost and latency. Miss: embed API call with `input_type=search_query` for asymmetric models (Cohere/Voyage).
5. **ANN + filter.** Bitmap intersection of eligible IDs, then ANN walk in compressed space. HNSW products use in-walk skip (Qdrant ACORN, ES). Pinecone uses adaptive scan as selectivity drops.
6. **Quantization rescore.** ANN ran in compressed space. Oversample top-cK (BBQ default 3x; BQ examples 4x). Rescore with residuals/float. Return IDs + scores, never raw vectors, to the assistant.
7. **Observe.** Span logs: cache hit, embed ms, ANN ms, rescore ms, RU, filter selectivity, `include_values=false`. Golden-set nDCG canary on schedule.

---

## Part 2: Core Mechanics & Algorithms

### 2.1 Embedding Model Landscape (MTEB/MMTEB 2026)

**Do not mix leaderboards.** MTEB v1, MTEB Eng v2, MMTEB, and RTEB are different task sets with different scales. Comparing OpenAI 64.6 (v1 overall) against Qwen 75.22 (Eng v2) is invalid.

| Model | Board | Score | Retrieval | Dims | Context | Cost/1M | Type |
|---|---|---|---|---|---|---|---|
| **NV-Embed-v2** | MTEB v1 | 72.31 | -- | 4096 | 32K | Self-host | Open |
| **Qwen3-Embedding-8B** | MMTEB | 70.58 | 70.88 | 4096 (MRL) | 32K | Self-host | Open |
| **Qwen3-Embedding-8B** | Eng v2 | 75.22 | 69.44 | 4096 | 32K | Self-host | Open |
| **Gemini Embedding 001** | MMTEB | 68.32 | 67.71 | 3072 | 8K | $0.005 | API |
| **Voyage 4-large** | RTEB | -- | +3.87 vs Gemini | 2048 (MRL) | 32K | $0.12 | API |
| **Cohere embed-v4** | -- | 65-66 | -- | 1024 (MRL) | 128K | ~$0.10 | API |
| **OpenAI 3-large** | MTEB v1 | 64.60 | -- | 3072 | 8K | $0.13 | API |
| **OpenAI 3-small** | MTEB v1 | 62.30 | -- | 1536 | 8K | $0.02 | API |
| **BGE-M3** | MMTEB | 59.56 | 54.60 | 1024 | 8K | Self-host | Open |

**Key trend**: Open-source has surpassed commercial APIs. Qwen3-8B (70.6 MMTEB) beats every API model.

**Selection heuristic**:
- Existing OpenAI stack, general-purpose: **text-embedding-3-large** (battle-tested, native MRL)
- Retrieval quality bottleneck: **Voyage 4-large** (MoE, 40% lower serving cost than comparable dense)
- Multilingual / multimodal / long docs: **Cohere embed-v4** (128K context, 100+ languages, text+image+PDF)
- Self-hosted multilingual: **BGE-M3** (dense + sparse + ColBERT in one forward pass)
- Maximum quality: **Qwen3-Embedding-8B** or **NV-Embed-v2** self-hosted

**Voyage 4 shared embedding space**: `voyage-4-large`, `voyage-4`, `voyage-4-lite`, and open `voyage-4-nano` produce compatible vectors. Embed corpus with large, queries with lite. This only works within that family -- not across vendors.

**Production rule**: Shortlist on language, dim, license, context length. Bake three models on your own queries and measure in-domain nDCG@10. MTEB retrieval tasks are largely BEIR-descended; contamination is the expected failure mode.

### 2.2 Indexing Algorithms

**HNSW (Hierarchical Navigable Small World)**

A hierarchical graph where every vector is a node. Top layers hold sparse "highway" edges for long-range jumps; lower layers hold dense local connections. Queries enter at the top, greedily traverse to the nearest region, then descend for fine-grained search.

- **Build**: O(N * log(N)) with M connections per node
- **Query**: O(log(N)) with ef_search controlling recall/latency
- **Space**: Graph edges = ~4 * M * N * 1.1 bytes. At M=32 and 50M vectors, neighbor lists alone = ~7 GB

**Key parameters**:
- **M** (connections/node): 12-48. Higher = better recall + more RAM. Use 48-64 for high-dim.
- **ef_construction** (build-time depth): Frozen at build time -- changing requires full rebuild.
- **ef_search** (query-time depth): Must be >= k. Cheapest tuning knob -- adjustable per query with zero rebuild cost.

**IVF (Inverted File)**

Partitions vectors into Voronoi cells via k-means. Searches only nprobe nearest cells at query time.

- **Build**: O(N * K * I) where K = nlist, I = k-means iterations
- **Query**: O(nprobe * N/nlist * d) -- linear within searched cells
- **nlist**: Rule of thumb: sqrt(N) to 4*sqrt(N)
- **nprobe**: Higher = better recall, more latency

**Pinecone does NOT use HNSW.** This is a critical interview point. Pinecone serverless uses per-slab algorithms: **Ananas** (SimHash/FJLT + sign bit) for small slabs (~10k vectors), **PQFS** for medium (~10k-100k), and **IVF+PQFS** for large (~100k+). You cannot tune M or ef on Pinecone. BBQ-HNSW is **Elasticsearch**, not Pinecone.

**Benchmark: 10M 768-dim vectors**

| Index | Config | p50 (ms) | p95 (ms) | Recall@10 | RAM |
|---|---|---|---|---|---|
| HNSW | M=32, efC=200, efS=64 | 0.31 | 0.42 | 0.964 | 3x base |
| IVF | nlist=2048, nprobe=64 | 0.62 | 0.83 | 0.962 | 1x base |
| Flat | brute force | 33.1 | 44.7 | 1.000 | 1x base |

**HNSW vs IVF Decision Matrix**:

| Factor | HNSW | IVF |
|---|---|---|
| Recall out-of-box | 95%+ easily | Lower, needs probe tuning |
| Memory | 2-5x higher | Lower footprint |
| Dynamic inserts | Absorbs without rebuild | Recall drifts; periodic rebuild needed |
| Filtered search | Filters can break graph pruning | Two-level filtering handles filters better |
| Scale sweet spot | Under ~10M vectors/node | Billion-scale where HNSW memory is prohibitive |

**Tuning protocol**: Plot Recall vs ef_search to find the elbow. Tune ef_search first (cheapest knob), then M (requires rebuild).

**HNSW RAM formula**: `RAM = N * (d * bytes_per_dim + M * 2 * 4 + overhead)`. For 10M vectors at 768-d float32 with M=32: vectors = 28.6 GB, edges = 2.5 GB, total ~34 GB with overhead.

### 2.3 Similarity Metrics

**Golden rule**: Use whichever metric the embedding model was trained with.

| Metric | Formula | Best For | Caveat |
|---|---|---|---|
| **Cosine** | sim = (a . b) / (\|a\| \|b\|) in [-1,1] | Text embeddings, semantic search | Default for NLP; length-invariant |
| **Dot Product** | a . b = \|a\| \|b\| cos(angle) | Recommendations, Pinecone sparse/hybrid | Fastest (skips sqrt/div). Identical to cosine for L2-normalized vectors |
| **Euclidean (L2)** | d = \|a - b\| | Clustering, anomaly detection | Sensitive to magnitude. Pinecone: lower = closer |

**Critical equivalence**: For L2-normalized vectors, cosine, inner product, and negated L2-squared produce **identical rankings**. OpenAI text-embedding-3-* outputs normalized vectors, so all three metrics are interchangeable. Never mix normalized and non-normalized vectors in the same index.

**Pinecone constraint**: Sparse and hybrid (dense+sparse) indexes require **dotproduct only**. Cosine dense + unbounded sparse without query-side `hybrid_score_norm` drowns the dense signal -- a product trap.

### 2.4 Quantization

Quantization is a **codec in front of ANN**, not a different ANN algorithm. The production path: encode vectors, ANN search in compressed space, then **oversample + rescore** with full precision. Skipping the rescore step steals recall.

| Method | Compression | Recall Retained | When to Use |
|---|---|---|---|
| **FP16** | 2x | ~99.9% | Always safe, negligible loss |
| **Int8 (SQ)** | 4x | ~99%+ | Production default |
| **PQ** (m=32, k=256) | 64x | ~97% | Memory-constrained, large scale |
| **Binary (BQ)** | 32x | 88-92% | Only with models trained for it (Cohere v4) |
| **BBQ** (ES) | 32x + rescore | ~96% | Elasticsearch with 3x oversample + rescore |
| **MRL** (1024->256) + Int8 | 16x | ~95% | Best balance of compression and quality |

**Matryoshka Representation Learning (MRL)**: Trains models so prefix dimensions contain the most information. Truncating a 1024-d MRL vector to 256-d preserves ~95% quality. MRL + quantization are perpendicular optimizations that stack: MRL 1024->128 dims + binary = ~256x speedup with ~10% recall loss.

**BBQ (Better Binary Quantization, Elasticsearch)**: 1-bit quantization + automatic 3x oversampling + float32 rescoring. Produces half the disk and 75% less memory versus HNSW float32 with <5% recall loss. BBQ-HNSW is Elasticsearch-specific.

**Production retrieval pipeline**: Binary search (coarse) -> Int8 rescoring -> Cross-encoder reranking.

### 2.5 Sparse Encodings and Hybrid Search

BGE-M3 produces dense, sparse, and ColBERT vectors in one forward pass. Pinecone supports sparse vectors natively. The sparse lane lives in the same product as the dense index.

**Why hybrid wins**: Pure semantic search misses exact terms (product IDs, error codes). Pure keyword search misses synonyms. Hybrid (BM25/SPLADE + dense) fused via Reciprocal Rank Fusion gives both precision and understanding. Hybrid outperforms pure semantic by 15-30% on mixed queries.

**Pinecone hybrid trap**: Sparse scores are unbounded; dense cosine is in [-1,1]. Without `hybrid_score_norm`, sparse dominates. Alpha/RRF fusion policy belongs in the retrieve layer, not the index layer.

### 2.6 Vec2Text and the PII Inversion Risk

Vec2Text (Morris et al., 2023) can reconstruct original text from embeddings: **92% exact recovery** on 32-token sequences, **89% recovery of MIMIC clinical names**. This means embedding vectors are **not anonymized data**. PII must be detected, redacted, and audited **before embedding**, not after. Redacting in the prompt after embedding is too late -- the vector itself is the leak.

### 2.7 ColPali and Multimodal Embeddings

ColPali applies late-interaction retrieval to document page images directly, bypassing OCR pipelines entirely. Page screenshots are embedded as token-level patch vectors, enabling visual retrieval on tables, diagrams, and formatted documents that defeat text-only chunking.

---

## Part 3: Token Economics & NFR Analysis

### 3.1 Embedding Cost Comparison

| Model | Cost/1M tokens | 1M docs x 500 tokens | Notes |
|---|---|---|---|
| OpenAI 3-small | $0.02 | $10 | Cheapest API |
| Voyage 4-lite | $0.02 | $10 | Shared space with large |
| Voyage 4-large | $0.12 | $60 | MoE, best retrieval |
| OpenAI 3-large | $0.13 | $65 | Battle-tested |
| Cohere embed-v4 | ~$0.10 | ~$50 | Multimodal |
| Gemini Embedding 001 | $0.005 | $2.50 | Cheapest quality API |
| Self-hosted Qwen3-8B | GPU cost | ~$20-50/month | Best quality |

**The real cost is not embedding.** It is the **index infrastructure** for serving queries at scale.

### 3.2 The RU/Namespace Cost Cliff (Pinecone)

This is the single most important cost number in this module. Pinecone bills by **Read Units (RU)** per GB of targeted namespace:

**Worked example**: 20M vectors x 1536-d at 50 QPS.

| Topology | RUs | Monthly Cost |
|---|---|---|
| **One fat namespace** (all 20M vectors) | ~533 RU | **~$263,000/mo** |
| **Tenant namespaces** (20 tenants x 1M each) | ~17 RU total | **~$8,300/mo** |

**Factor: 32x cost difference.** The fat namespace means every query scans all 20M vectors. Tenant namespaces scope each query to 1M vectors. This is the dominant cost lever -- not embed prices.

**Plan comparison (Pinecone)**:

| Plan | Base | RU included | Extra RU | SLA |
|---|---|---|---|---|
| Starter | Free | 100 RU | -- | None |
| Builder | $25/mo | -- | $1/RU | None |
| Standard | $175/mo | -- | $0.80/RU | 99.95% |
| Enterprise | Custom | -- | Custom | 99.99% |

### 3.3 Latency SLA Targets

No vendor publishes a global p50/p95/p99 SLO for vector search. Pinecone "sub-100ms" is a design target, not an SLO. These are architecture-derived policy targets:

| Metric | p50 | p95 | p99 | Notes |
|---|---|---|---|---|
| **ANN query** (warm) | 5-15 ms | 20-40 ms | 80-120 ms | HNSW/IVF in-memory |
| **ANN + payload filter** | 10-30 ms | 40-80 ms | 120-250 ms | Filter selectivity matters |
| **Embed API call** | 30-50 ms | 80-150 ms | 200-400 ms | Network-bound |
| **End-to-end query** (embed + ANN + rescore) | 40-80 ms | 120-250 ms | 250-500 ms | Policy target |
| **Embed + ANN + rerank + generate** | 800-1500 ms | 2000-4000 ms | 4000-8000 ms | Full RAG pipeline |

### 3.4 Capacity Planning

**HNSW RAM formula**: `RAM_bytes = N * (d * bytes_per_dim + M * 2 * sizeof(int) + overhead_per_node)`

| Scenario | Vectors | Dims | Quant | M | RAM Estimate |
|---|---|---|---|---|---|
| Small RAG | 1M | 1536 | FP32 | 16 | ~6.5 GB |
| Medium search | 10M | 768 | Int8 | 32 | ~12 GB |
| Large enterprise | 100M | 1024 | PQ-64 | 32 | ~8 GB (compressed) |
| Billion-scale | 1B | 256 (MRL) | BQ | IVF | ~40 GB disk |

### 3.5 Availability & RPO/RTO

| Component | Availability | RPO | RTO | Strategy |
|---|---|---|---|---|
| Pinecone Standard | 99.95% | WAL on S3 | Minutes (alias flip) | Immutable slabs; dual-index during migration |
| Qdrant Cloud | 99.9% | Snapshot | Hours (rebuild) | RF=2-3 on shards |
| pgvector | Postgres SLA | Postgres WAL | Minutes (PITR) | Standby replicas |
| Weaviate Cloud | 99.9% | Snapshot | Hours | Multi-node, RF=3 |

**Vector indexes are derived data.** They can be rebuilt from source documents. RPO for the vector store is less critical than RPO for primary document storage. The index alias flip pattern: build new index in background, validate recall, swap alias, drop old.

---

## Part 4: Distributed Resilience & Security

### 4.1 Circuit Breaker Pattern

Independent breakers for the **embedding API** and the **vector index**. An embedding API outage must not prevent BM25-only fallback. A vector index outage must not skip the ACL filter.

**Fallback chain**: Dense ANN -> BM25-only -> Last-good cached results -> `retrieval_degraded` refusal (if ungrounded responses are forbidden by policy).

### 4.2 PII Pipeline: Detect-Redact-Audit Before Embed

Vec2Text proves embeddings are invertible. The pipeline must run **before** text enters the embedding model:

1. **Detect**: Regex (email, PAN, SSN) + ML NER (names, addresses). Dual-gate: regex always runs, NER as available.
2. **Redact**: Replace with stable tokens (`[EMAIL_<hash12>]`) so downstream dedup works. Block PAN from reaching embed entirely.
3. **Audit**: Log content hash pre/post, entity types + counts, action (redact/block/allow), detector used. Never log the PII value itself.

**Fail-closed on PAN**: If PAN is detected in ingest text, block the embed entirely. A redacted PAN token still occupies embedding dimensions that may leak context.

### 4.3 Multi-Tenant Isolation

- **Namespace-per-tenant** (Pinecone): Each tenant's vectors in a separate namespace. Queries automatically scoped. The 32x cost difference makes this mandatory at scale.
- **Metadata filter + RLS** (pgvector, Qdrant): `tenant_id` in payload, filter predicate composed from JWT claims. ACL belongs in the index predicate, never in the prompt.
- **Per-collection isolation** (Weaviate): Separate collections per tenant for strict compliance requirements.

**Anti-pattern**: "Filter out unauthorized results after ANN search." This is both a recall bug (you may get fewer than k results) and an authorization bug (unauthorized vectors entered the candidate set and influenced the search).

### 4.4 Zero-Trust MCP

Every tool call to the vector database goes through a gateway PEP with these controls:

| Control | Implementation |
|---|---|
| **OAuth 2.1 + PKCE** | Per-request authentication |
| **RFC 8707** | Resource indicator = this vector DB server |
| **RFC 8693** | No token passthrough to upstream APIs |
| **Hash-pin** | Verify tool JSON + server digest on every call |
| **Namespace from JWT** | Never from tool arguments |
| **include_values=false** | Deny for assistant principals (invert surface protection) |

**MCP tool verbs behind PEP**:
- `query_index`: k + filter from verified auth. Deny `include_values` for assistants.
- `upsert`: Ingest workers only; idempotent chunk_id keys.
- `fetch_values`: Deny for assistants entirely. Only for authorized data export workflows.

### 4.5 Drift Detection

Embedding model drift, corpus drift, and query distribution shift all degrade recall silently. Monitor:
- **Canary queries**: Golden set of queries with known relevant documents. Run nDCG@10 on schedule.
- **Cosine mean-shift**: Track the average cosine similarity of new embeddings vs. the corpus centroid. Alert on drift beyond 2 standard deviations.
- **Model version mismatch**: If the embedding model changes but the index is not rebuilt, all new queries produce vectors in a different geometry than stored vectors. This is a silent recall disaster.

---

## Part 5: Production Enterprise Code

```python
"""Vector database runtime: circuit breaker, PII redact, BM25 fallback, MCP deny.
Demonstrates the critical patterns: schema pin, namespace from auth (not args),
include_values denial, PII before embed, independent breakers.
Run: python embeddings_runtime.py
"""
import hashlib, json, logging, random, re, time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# --- PII Detection (before embed, not after) ---
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

def pii_detect_redact_audit(text: str, *, audit: list, cid: str, sink: str) -> str:
    """Three steps, all required: detect, redact, audit. Block PAN from embed."""
    kinds = [k for k, rx in (("email", EMAIL_RE), ("pan", PAN_RE)) if rx.search(text)]
    pre_hash = hashlib.sha256(text.encode()).hexdigest()
    if "pan" in kinds and sink == "embed_ingest":
        audit.append({"cid": cid, "sink": sink, "action": "block", "kinds": kinds})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text)
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({"cid": cid, "sink": sink, "action": "redact" if redacted != text else "allow",
                  "kinds": kinds, "pre": pre_hash[:16], "post": hashlib.sha256(redacted.encode()).hexdigest()[:16]})
    return redacted

# --- Circuit Breaker (independent per service) ---
class CircuitState(Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    name: str; threshold: int = 5; cooldown_s: float = 30.0
    _state: CircuitState = CircuitState.CLOSED; _failures: int = 0; _opened: float = 0.0
    def allow(self):
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
            else: raise RuntimeError(f"circuit_open:{self.name}")
    def record_ok(self): self._failures = 0; self._state = CircuitState.CLOSED
    def record_fail(self):
        self._failures += 1
        if self._failures >= self.threshold:
            self._state = CircuitState.OPEN; self._opened = time.monotonic()

# --- BM25 Fallback Index ---
class BM25Index:
    def __init__(self):
        self.df, self.postings, self.doclen = defaultdict(int), defaultdict(lambda: defaultdict(int)), {}
    def upsert(self, doc_id: str, text: str):
        toks = text.lower().split(); self.doclen[doc_id] = max(1, len(toks))
        for tok in set(toks): self.df[tok] += 1
        for tok in toks: self.postings[tok][doc_id] += 1
    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        toks, scores, n = query.lower().split(), defaultdict(float), max(1, len(self.doclen))
        avgdl = sum(self.doclen.values()) / n
        for tok in toks:
            df = self.df.get(tok, 0); idf = max(0.0, (n - df + 0.5) / (df + 0.5))
            for did, tf in self.postings.get(tok, {}).items():
                dl = self.doclen[did]
                scores[did] += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * dl / avgdl))
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]

# --- MCP Gateway Deny ---
MCP_DENY = frozenset({"include_values", "host_shell", "fetch_values", "set_alias"})

def mcp_deny_check(verb: str, args: dict, auth_roles: frozenset):
    if verb in MCP_DENY or args.get("include_values") is True:
        raise PermissionError(f"mcp_deny:{verb}")
    if verb == "upsert" and "ingest" not in auth_roles:
        raise PermissionError("pdp_deny:upsert")

# --- Demo ---
if __name__ == "__main__":
    audit = []
    # PII detection before embed
    clean = pii_detect_redact_audit("Contact ada@example.com for MFA reset", audit=audit, cid="c1", sink="embed_ingest")
    assert "[EMAIL_" in clean
    try:
        pii_detect_redact_audit("card 4111 1111 1111 1111", audit=audit, cid="c2", sink="embed_ingest")
    except PermissionError: pass  # PAN blocked from embed -- correct behavior
    # MCP deny
    try: mcp_deny_check("query_index", {"include_values": True}, frozenset({"search"}))
    except PermissionError: pass  # include_values denied -- correct
    print(f"ok: {len(audit)} audit rows, PII blocked before embed, include_values denied")
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: 10M-Document Enterprise RAG Search

**Problem**: Enterprise with 500 tenants, 10M total documents, 50 QPS sustained, p95 query latency under 200ms for retrieval, SOC2 compliance, tenant isolation required.

**Architecture**: Pinecone Standard with namespace-per-tenant. OpenAI text-embedding-3-large (1536-d, MRL to 512 for ANN first pass, full-dim rescore). PII detect-redact-audit before embed. MCP gateway denying `include_values` for assistants. BM25 fallback via Elasticsearch for exact-match queries (product IDs, error codes).

**Trade-off matrix**:

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| Embedding model | OpenAI 3-large | Voyage 4-large | OpenAI 3-large | Existing stack, native MRL, battle-tested |
| Tenancy | One fat namespace | Namespace-per-tenant | Namespace-per-tenant | $263k/mo vs $8.3k/mo -- 32x cost difference |
| Quantization | FP32 | MRL 512 + Int8 | MRL 512 + Int8 | 16x compression, 95% recall retained |
| Search type | Dense only | Hybrid (dense + BM25) | Hybrid | 15-30% better recall on exact terms |

**Cost**: ~$8,300/mo index + ~$650/mo embedding = ~$9,000/mo total.

### Scenario 2: Billion-Scale Image Similarity

**Problem**: E-commerce with 1B product images, 1000 QPS, p99 under 500ms, cost under $50k/mo.

**Architecture**: Self-hosted Qwen3-Embedding-4B for image embeddings (2560-d, MRL to 256). IVF-PQ on Elasticsearch with BBQ for 32x compression. Two-stage: binary coarse search (1B candidates -> 10k), then float rescore (10k -> 100). Cross-encoder reranker on final 100.

**Trade-off matrix**:

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| Index type | HNSW | IVF-PQ | IVF-PQ | HNSW at 1B vectors needs 500+ GB RAM |
| Quantization | Int8 (4x) | Binary + rescore (32x) | Binary + rescore | 1B * 256-d * 4 bytes = 1TB uncompressed; must compress |
| Embedding | API (Cohere) | Self-hosted | Self-hosted | $0.10/1M * 1B = $100k embed cost; self-host is cheaper |

---

## Common Failure Modes

| Failure | Mechanism | Mitigation |
|---|---|---|
| **Model/index schema mismatch** | Embedding model changed but index not rebuilt | Pin `model_id+dim+metric+codec` in schema; dim mismatch = 4xx |
| **Fat namespace cost cliff** | All tenants in one namespace | Namespace-per-tenant: 32x cost reduction |
| **PII in embeddings** | Vec2Text recovers 92% of text from vectors | Detect-redact-audit BEFORE embed |
| **include_values data leak** | Assistant fetches raw vectors; attacker inverts | Deny include_values for assistant principals |
| **Post-filter ACL bug** | Unauthorized vectors enter ANN candidate set | ACL in index predicate (bitmap pre-filter), not post-filter |
| **Cosine vs dotproduct on hybrid** | Sparse scores unbounded, drown dense signal | Use `hybrid_score_norm` or RRF fusion |
| **MTEB leaderboard shopping** | Comparing v1 scores to MMTEB scores | Different task sets; evaluate on your own data |
| **Serving `latest` index** | Index alias pointing at unvalidated build | Dual-index + recall validation + alias flip |
| **"Tune Pinecone M/ef"** | Pinecone has never used HNSW | Ananas/PQFS/IVF per slab; no M/ef knobs |
| **Skipping quantization rescore** | ANN in BQ space without float rescore | Oversample 3-4x + rescore; BBQ default 3x |

---

## Interview Q&A

**Q1: What is the schema invariant for a vector index?**
I pin five elements: `model_id + dimension + similarity metric + index type + quantization codec`. Changing any one of these requires a full re-embed or rebuild. I never mutate these in place -- I build a new index, validate recall against a golden set, and swap the alias.

**Q2: HNSW vs IVF -- when do you choose each?**
HNSW for under ~10M vectors where I can afford 2-5x RAM overhead and need high recall out of the box with dynamic inserts. IVF for billion-scale where HNSW memory is prohibitive. Key difference: HNSW absorbs inserts gracefully; IVF recall drifts and needs periodic rebuilds.

**Q3: "Pinecone uses HNSW" -- true or false?**
False. Pinecone has never used HNSW, not in serverless and not in the old pod architecture. Each slab selects Ananas (small), PQFS (medium), or IVF+PQFS (large). "Tune Pinecone M/ef" is an interview fail. BBQ-HNSW is Elasticsearch.

**Q4: Why does PII matter for embeddings specifically?**
Vec2Text can recover 92% of original text from embeddings on 32-token sequences, and 89% of MIMIC clinical names. Embeddings are not anonymized. I run detect-redact-audit before the text reaches the embedding model. Redacting in the prompt after embedding is too late -- the vector itself is the leak.

**Q5: What is the biggest cost lever in vector search?**
Namespace topology. One fat namespace with 20M vectors at 50 QPS costs ~$263k/mo on Pinecone Standard. Splitting into tenant namespaces drops it to ~$8.3k/mo -- a 32x difference. The embedding cost ($0.02-$0.13/1M tokens) is noise compared to this.

**Q6: How do you handle embedding model upgrades?**
Dual-index migration. Build the new index with the new model in the background while the old index serves queries. Validate recall on a golden set. Swap the alias. Keep the old index alive for rollback. Never in-place mutate dimension or metric.

**Q7: Cosine vs dot product vs L2 -- when does it matter?**
For L2-normalized vectors (like OpenAI 3-*), all three produce identical rankings. It only matters for non-normalized vectors: cosine ignores magnitude (good for text), dot product includes magnitude (good for popularity-weighted recommendations), L2 measures absolute distance (good for clustering). Always use the metric the model was trained with.

**Q8: What is MRL and why does it matter for cost?**
Matryoshka Representation Learning trains models so prefix dimensions contain the most information. I can truncate a 1024-d vector to 256-d and retain ~95% quality -- but only if the model was MRL-trained. This is perpendicular to quantization: MRL 1024->256 + Int8 = 16x compression. Naive truncation of a non-MRL model destroys quality.

**Q9: How do you handle multi-tenant isolation in vector search?**
ACL in the index predicate, never in the prompt. Three options: namespace-per-tenant (Pinecone, best cost profile), metadata filter with RLS (pgvector/Qdrant), or per-collection isolation (Weaviate). The anti-pattern is post-filtering after ANN -- that is both a recall bug and an authorization bug.

**Q10: What does your Zero-Trust MCP setup look like for vector search?**
OAuth 2.1 + PKCE per request. RFC 8707 resource indicator bound to this vector DB. Namespace derived from the verified JWT, never from tool arguments. `include_values=false` enforced for assistant principals to prevent embedding inversion attacks. Hash-pin on tool JSON to detect catalog drift. MCP is not the namespace PDP -- the gateway PEP is.

**Q11: What is your circuit breaker and fallback strategy?**
Independent breakers on the embedding API and the vector index. Embedding API down: fall back to BM25-only search. Vector index down: serve last-good cached results, then BM25, then `retrieval_degraded` refusal if the policy forbids ungrounded responses. Never skip the ACL filter on fallback.

**Q12: How do you detect silent quality degradation?**
Golden-set canary queries with known relevant documents, run nDCG@10 on schedule. Cosine mean-shift monitoring on new embeddings vs corpus centroid. Model version mismatch alerts (embedding model changed but index not rebuilt). In-domain evaluation always beats public leaderboard scores.

---

## Key Numbers to Memorize

| Number | What |
|---|---|
| **92% / 89%** | Vec2Text exact recovery on 32 tokens / MIMIC clinical names |
| **~$263k/mo vs ~$8.3k/mo** | Fat namespace vs tenant namespaces (20M x 1536-d, 50 QPS, Pinecone Standard) |
| **$0.02 / $0.13 / $0.12** | OpenAI 3-small / 3-large / Voyage 4-large per 1M tokens |
| **0.31ms / 0.42ms** | HNSW p50/p95 on 10M 768-d vectors (M=32, efS=64) |
| **3x / 4x** | BBQ default oversample / BQ typical oversample for rescore |
| **70.58 / 75.22** | Qwen3-8B on MMTEB / MTEB Eng v2 (top open-source, 2025) |
| **64.6 / 62.3** | OpenAI 3-large / 3-small on MTEB v1 (do not compare to MMTEB) |
| **8192 / 2048 / 300k** | OpenAI max tokens/input / max inputs/request / max tokens summed |
| **128K context** | Cohere embed-v4 context length (longest API model) |
| **5 elements** | Schema pin: model_id + dimension + metric + index type + codec |

---

## Quick Reference

- **Schema pin**: `model_id + dim + metric + index_type + codec`. Change any = rebuild.
- **Pinecone**: Never HNSW. Ananas/PQFS/IVF per slab. No M/ef knobs.
- **Cost lever**: Namespace topology, not embed price. 32x difference.
- **PII**: Detect-redact-audit BEFORE embed. Vec2Text = 92% recovery.
- **Metrics**: Cosine = IP = -L2 squared iff L2-normalized.
- **MRL**: Truncate prefix dimensions. Only works on MRL-trained models.
- **Quantization**: Codec in front of ANN. Always oversample + rescore.
- **Fallback**: Dense ANN -> BM25 -> cached -> refuse.
- **MCP**: Deny include_values for assistants. Namespace from JWT, not args.
- **Hybrid**: BM25 + dense with RRF. Hybrid outperforms pure semantic by 15-30%.
