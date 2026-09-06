# Module 01: Embeddings & Vector Databases

---

## What Is This?

An **embedding** is a list of numbers that captures the meaning of a piece of text (or an image, or audio). Think of it like GPS coordinates for meaning: just as two nearby GPS points are geographically close, two similar embeddings are semantically close. "How do I reset my password?" and "I forgot my login credentials" end up near each other in this number space, even though they share zero words.

A **vector database** is a specialized storage engine built to find the nearest neighbors of a given embedding among millions or billions of stored embeddings -- fast. Traditional databases answer "give me rows where column = X." Vector databases answer "give me the items most similar to this."

**Analogy**: Imagine a library where every book has coordinates on a giant map based on its topic and style. When a reader walks in with a question, the librarian does not scan every shelf -- she looks at the map, finds the neighborhood matching the question, and pulls the closest books. The embedding model draws the map. The vector database is the librarian.

## Why It Matters

Embeddings and vector databases are the backbone of Retrieval-Augmented Generation (RAG), semantic search, recommendation systems, and multimodal AI. Choosing the wrong embedding model silently degrades recall. Choosing the wrong index algorithm blows your memory budget or latency SLA. At Director/VP level, you are expected to make these infrastructure decisions and defend them with numbers.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CONTROL PLANE                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────┐    │
│  │ Model Registry│  │ Index Alias  │  │ Drift Monitor              │    │
│  │ (version,dims,│  │ Manager      │  │ (MMD, canary queries,      │    │
│  │  metric)      │  │ (blue-green) │  │  cosine mean-shift)        │    │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬───────────────┘    │
│         │                 │                        │                     │
│         ▼                 ▼                        ▼                     │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    TELEMETRY BUS                                  │   │
│  │  Recall@10 | p95 latency | cache hit rate | cluster balance      │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                          DATA PLANE                                     │
│                                                                         │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────────────────┐   │
│  │  Client   │───▶│  API Gateway  │───▶│  Embedding Service           │   │
│  │  (query)  │    │  (rate limit, │    │  (batch/single, model ver,  │   │
│  └──────────┘    │   auth)       │    │   circuit breaker)           │   │
│                  └──────────────┘    └─────────────┬────────────────┘   │
│                                                    │                     │
│                                                    ▼                     │
│                                     ┌──────────────────────────────┐    │
│                                     │       HYBRID SEARCH           │    │
│                                     │  ┌────────┐    ┌──────────┐  │    │
│                                     │  │ Dense   │    │ Sparse   │  │    │
│                                     │  │ (HNSW/  │    │ (BM25)   │  │    │
│                                     │  │  IVF)   │    │          │  │    │
│                                     │  └────┬───┘    └─────┬────┘  │    │
│                                     │       └──────┬───────┘       │    │
│                                     │              ▼               │    │
│                                     │    Reciprocal Rank Fusion    │    │
│                                     └──────────────┬───────────────┘    │
│                                                    │                     │
│                                                    ▼                     │
│                                     ┌──────────────────────────────┐    │
│                                     │  Cross-Encoder Reranker       │    │
│                                     │  (BGE-reranker-v2 / Cohere)  │    │
│                                     └──────────────┬───────────────┘    │
│                                                    │                     │
│                                                    ▼                     │
│                                     ┌──────────────────────────────┐    │
│                                     │  Response (top-k results +    │    │
│                                     │  scores + metadata)           │    │
│                                     └──────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                       PERSISTENCE LAYER                                 │
│  ┌────────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │ Vector Index        │  │ Metadata Store    │  │ WAL / Snapshots    │  │
│  │ (HNSW graph or IVF  │  │ (payload index,   │  │ (point-in-time     │  │
│  │  centroids + PQ     │  │  tenant filters,  │  │  recovery, backup  │  │
│  │  codes, on NVMe)    │  │  RLS policies)    │  │  to S3/GCS)        │  │
│  └────────────────────┘  └──────────────────┘  └────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Request Flow Narrative

1. **Client** sends a natural-language query to the API Gateway.
2. **API Gateway** authenticates, rate-limits, and routes to the Embedding Service.
3. **Embedding Service** converts the query to a dense vector using the version-pinned model. A circuit breaker wraps the embedding API call; on failure, the system falls back to BM25-only search.
4. **Hybrid Search** runs the dense vector through the HNSW/IVF index and simultaneously runs BM25 keyword search. Results are fused via Reciprocal Rank Fusion (RRF).
5. **Cross-Encoder Reranker** rescores the top-N candidates for precision.
6. **Telemetry** records latency, recall metrics, and cache hit rates. The Drift Monitor checks canary query results on a scheduled basis and alerts when NDCG@10 drops.
7. **Index Alias Manager** handles blue-green deployments when embedding models are upgraded. The old index stays live until the new one is validated.

---

## Part 2: Core Mechanics & Algorithms

### 2.1 Embedding Model Landscape (MTEB 2026)

| Model | MTEB Score | Dims | Context | Cost/1M tokens | Type |
|-------|-----------|------|---------|----------------|------|
| NV-Embed-v2 | 72.31 | 4096 | 32K | Self-host | Open-source |
| Qwen3-Embedding-8B | 70.58 | 4096 | 128K | Self-host | Open-source |
| Gemini Embedding 001 | 68.32 | 3072 | 8K | $0.005 | API |
| Voyage 3-large | 67.10 | 1024 | 32K | $0.18 | API |
| Cohere embed-v4 | 65.2-66.3 | 1024 | 128K | $0.10 | API |
| OpenAI text-embedding-3-large | 64.60 | 3072 | 8K | $0.13 | API |
| BGE-M3 | 63.00 | 1024 | 8K | Self-host | Open-source |

**Key trend**: Open-source has surpassed commercial APIs. Qwen3-Embedding-8B (70.6) beats every API model. The gap reversed in 2025-2026.

**Selection heuristic**:
- Existing OpenAI stack, general-purpose: **text-embedding-3-large** (battle-tested, native MRL, but stale since Jan 2024)
- Retrieval quality is the bottleneck: **Voyage 3-large** (leads retrieval-specific NDCG@10)
- Multilingual / multimodal / long docs: **Cohere embed-v4** (128K context, 100+ languages, native binary quantization, text + images)
- Self-hosted multilingual: **BGE-M3** (replaces dense encoder + BM25 + reranker in one model)
- Maximum quality, cost no constraint: **Qwen3-Embedding-8B** or **NV-Embed-v2** self-hosted

**Critical caveat**: MTEB is a proxy. A model dominating classification may underperform on retrieval. Always evaluate on your own data and query patterns.

### 2.2 Indexing Algorithms

**HNSW (Hierarchical Navigable Small World)**

A hierarchical graph where every vector is a node. Top layers hold sparse "highway" edges for long-range jumps; lower layers hold dense local connections. Queries enter at the top, greedily traverse to the nearest region, then descend for fine-grained search.

- **Build complexity**: O(N * log(N)) with M connections per node
- **Query complexity**: O(log(N)) with ef_search controlling the recall/latency knob
- **Space complexity**: O(N * M) for the graph structure, plus O(N * d) for vectors

Key parameters:
- **M** (max connections/node): Memory = ~4 * M * N * 1.1 bytes for edge lists. At M=32 and 50M vectors, neighbor lists alone = ~7 GB. Range: 12-48; use 48-64 for high-dim.
- **ef_construction** (build-time search depth): Higher = better index quality, slower build. Frozen at build time -- changing requires full rebuild.
- **ef_search** (query-time search depth): Must be >= k. Cheapest tuning knob -- adjustable per query with zero rebuild cost.

**IVF (Inverted File)**

Partitions vectors into Voronoi cells via k-means. Searches only nprobe nearest cells at query time.

- **Build complexity**: O(N * K * I) where K = nlist (clusters) and I = k-means iterations
- **Query complexity**: O(nprobe * N/nlist * d) -- linear within searched cells
- **Space complexity**: O(N * d) base, plus O(K * d) for centroids

Parameters:
- **nlist**: Number of partitions. Rule of thumb: sqrt(N) to 4*sqrt(N).
- **nprobe**: Cells searched per query. Higher = better recall, more latency.

Variants: IVFFlat (raw vectors), IVFPQ (+ product quantization), IVF+RaBitQ (random bitwise quantization).

**Benchmark: 10M 768-dim vectors**

| Index | Config | p50 (ms) | p95 (ms) | Recall@10 | RAM |
|-------|--------|----------|----------|-----------|-----|
| HNSW | M=32, efC=200, efS=64 | 0.31 | 0.42 | 0.964 | 3x base |
| IVF | nlist=2048, nprobe=64 | 0.62 | 0.83 | 0.962 | 1x base |
| Flat | brute force | 33.1 | 44.7 | 1.000 | 1x base |

**HNSW vs IVF Decision Matrix**:

| Factor | HNSW | IVF |
|--------|------|-----|
| Recall out-of-box | 95%+ | Lower, needs probe tuning |
| Memory | 2-5x more | Lower footprint |
| Dynamic inserts | Absorbs without rebuild | Recall drifts; periodic rebuild |
| Filtered search | Filters break graph pruning | Two-level filtering handles filters better |
| Scale sweet spot | Under ~10M vectors/node | Billion-scale where HNSW memory is prohibitive |

**Tuning protocol**: Plot Recall vs ef_search to find the elbow. Tune ef_search first (cheapest knob), then M (requires rebuild).

### 2.3 Similarity Metrics

**Golden rule**: Use whichever metric the embedding model was trained with.

| Metric | Measures | Best For | Caveat |
|--------|----------|----------|--------|
| Cosine | Angle (direction only) | Text embeddings, semantic search | Default for NLP; length-invariant |
| Dot Product | Direction + magnitude | Recommendations, collaborative filtering | Fastest (skips sqrt/div). Identical to cosine for L2-normalized vectors |
| Euclidean (L2) | Straight-line distance | Clustering, anomaly detection | Sensitive to magnitude |

For L2-normalized vectors (e.g., OpenAI text-embedding-3-*), all three metrics produce identical rankings. Never mix normalized and non-normalized vectors in the same index.

### 2.4 Quantization

| Method | Compression | Recall Retained | When to Use |
|--------|------------|-----------------|-------------|
| FP16 | 2x | ~99.9% | Always safe, negligible loss |
| Int8 (SQ) | 4x | ~99%+ | Production default |
| PQ (m=32, k=256) | 64x | ~97% | Memory-constrained, large scale |
| Binary | 32x | 88-92% | Only with models trained for it (Cohere v4) |
| MRL (1024->256) + Int8 | 16x | ~95% | Best balance of compression and quality |

**Production retrieval pipeline**: Binary search (coarse) -> Int8 rescoring -> Cross-encoder reranking.

### 2.5 Matryoshka Embeddings (MRL)

MRL trains models to pack the most important information into the earliest dimensions. Truncating preserves most quality. Supported by all major models in 2026.

MRL + quantization are perpendicular optimizations that stack:
- MRL 1024->128 dims (~2% recall loss) + binary quantization = ~256x speedup, ~10% recall loss
- MRL 1024->128 dims + int8 = ~32x speedup, ~3% recall loss

### 2.6 Multimodal Embeddings

**ColPali** (ICLR 2025): Vision Language Model producing multi-vector embeddings from document page images. Eliminates document parsing entirely. Each page produces ~1024 patch vectors -- 100x more storage than single-vector models. Best for visually complex documents (infographics, tables, charts).

**Cohere embed-v4**: First commercial model vectorizing text + images + interleaved documents in one model. Key enabler for multimodal RAG without separate encoders.

---

## Part 3: Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Per-query embedding cost** = tokens_per_query * price_per_token + vector_DB_query_cost

Example at 100 QPS, 500-token avg query, OpenAI text-embedding-3-large ($0.13/1M tokens):
- Embedding: 100 * 500 * $0.00000013 * 86,400 = ~$0.56/day
- At scale, vector DB infrastructure cost dominates embedding API cost

**Storage cost** (5M vectors, 1536-dim, float32):

| Quantization | Storage | Savings |
|-------------|---------|---------|
| FP32 (raw) | 28.6 GB | -- |
| Int8 SQ | 7.2 GB | 75% |
| PQ (m=32) | 0.45 GB | 98% |

**Total Cost of Ownership** (5M vectors, 1536-dim, 100 QPS):

| Platform | Monthly Cost | Notes |
|----------|-------------|-------|
| pgvector (existing PG) | $250-400 | Lowest if you already run Postgres |
| Qdrant Cloud | $350-600 | Good balance of cost and features |
| Weaviate Cloud | $400-700 | Best hybrid search |
| Pinecone Serverless | $500-900 | Zero ops, highest lock-in |
| Zilliz Cloud (Milvus) | $700-1,100 | Justified only at billion-scale |

### 3.2 Latency SLA Targets

| Tier | p50 | p95 | p99 | Config |
|------|-----|-----|-----|--------|
| Real-time search | <5ms | <15ms | <50ms | HNSW, M=16, ef=64, in-memory |
| Standard RAG | <20ms | <50ms | <100ms | HNSW, M=16, ef=128, SSD-backed |
| Batch/analytics | <100ms | <500ms | <1s | IVF+PQ, large nprobe |

**Recall-latency tradeoff**: 0.80 to 0.95 recall increases HNSW latency ~31%. From 0.95 to 0.99, latency grows 3-5x. Target 0.95 recall and use reranking to close the gap.

### 3.3 Capacity Planning

**Memory formula** (HNSW):
```
Total RAM = N * (d * bytes_per_dim + 4 * M * 1.1) + overhead
```

Example: 50M vectors, 768-dim, Int8, M=16:
- Vectors: 50M * 768 * 1 byte = 36.0 GB
- Graph: 50M * 4 * 16 * 1.1 = 3.5 GB
- Total: ~39.5 GB + ~20% overhead = ~47 GB

Plan for 2x headroom: provision ~94 GB to handle growth and compaction.

### 3.4 Availability & RPO/RTO

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| Vector index | Minutes | Minutes | Replica failover (Qdrant/Weaviate) |
| pgvector | Seconds | Minutes | Postgres streaming replication + WAL |
| Embedding API | N/A | Seconds | Circuit breaker -> BM25 fallback |
| Full re-index | N/A | Hours | Snapshot restore or re-embed from source |

---

## Part 4: Distributed Resilience & Security

### 4.1 Embedding Drift & Versioning

**Drift sources**:
1. **Data drift**: Domain language evolves beyond training data. Legal RAG on 2020-2024 case law struggles with 2025 regulatory terminology.
2. **Model upgrade drift**: New model produces different vector positions. Cross-model cosine similarity ranges from 0.85 to 0.97 depending on architecture distance.
3. **Silent provider updates**: Provider ships model update without notice. Index becomes a Frankenstein of mixed vector spaces.

**Detection**:
- **MMD (Maximum Mean Discrepancy)**: Compares distribution of embeddings between reference and current windows. Works well in high dimensions.
- **Canary queries**: Fixed set of queries with known ideal results. Monitor NDCG@10 daily. Alert on >5% degradation.
- **Cosine distance mean-shift**: Over 6-hour sliding windows between production and reference embeddings.

**Blue-Green Index Pattern**: Name indexes with model version and date (`docs_v2_2026-03-01`). Applications reference an alias (`docs_current`). Build new index in parallel, validate with eval suite, atomically swap alias. Zero downtime, instant rollback.

**Critical rule**: Never mix vectors from different embedding models in the same store. When you swap models, re-embed the entire corpus.

### 4.2 Failure Taxonomy

| Failure | Type | Detection | Mitigation |
|---------|------|-----------|------------|
| Silent recall degradation | Permanent | Canary query suite, hourly | Version-lock model, tune ef_search |
| IVF recall collapse after ingestion | Transient | Cluster size ratio >10x | Rebuild index after >10% corpus change |
| HNSW memory exhaustion | Permanent | RSS vs available RAM at 80% | Reduce M, apply quantization, shard |
| Filtered search graph trapping | Transient | Filtered vs unfiltered recall gap >15% | Use integrated filtering (Qdrant payload, Weaviate inverted) |
| Frankenstein index (mixed models) | Permanent | Model version metadata audit | Never mix; blue-green deploys |
| Embedding service down | Transient | Circuit breaker trips | Fall back to BM25 keyword search |

### 4.3 Security

**Vectors leak information**: Embedding inversion attacks can reconstruct approximate original text. This is demonstrated, not theoretical.

- **Encryption at rest**: All major vector DBs support it. pgvector inherits Postgres TDE. Pinecone uses AES-256 by default.
- **Encryption in transit**: TLS 1.3 mandatory for all embedding API calls and DB connections.
- **Multi-tenancy**: Pinecone namespaces, Qdrant collection-level JWT, Weaviate native multi-tenancy (hot/cold/frozen), pgvector row-level security (RLS).
- **PII in embeddings**: Strip PII before embedding (regex + NER). Store metadata separately with access control. Log all vector DB queries with user identity for audit trails.

#### Zero-Trust Vector Infrastructure

Assume-breach posture for the entire vector search stack:

- **Encrypt at rest**: AES-256 for all vector data and metadata (Pinecone default, pgvector via Postgres TDE, Qdrant via filesystem encryption).
- **Encrypt in transit**: mTLS between application tier and vector DB -- not just TLS, mutual authentication. Every service proves its identity.
- **Authenticate every query**: JWT or API key validation at the API gateway. No anonymous vector queries, even from internal services.
- **Network segmentation**: Vector DB in a private subnet with no public IP. Access only via VPC peering or private endpoints. Security groups allow only the embedding service and query service.
- **Signed embedding artifacts**: Verify model provenance before deployment. Hash the model weights and pin the hash in the deployment manifest. Reject indexes built with unverified models.

#### RBAC for Vector Operations

| Role | Permissions |
|------|------------|
| Reader | Query vectors, read metadata |
| Ingestion Engineer | Batch upsert, create collections, manage indexes |
| Admin | Delete collections, modify RBAC policies, configure replication |
| Auditor | Read-only access to query logs, drift reports, access audit trail |

Enforce via API key scoping (Pinecone), collection-level ACLs (Qdrant), or RLS policies (pgvector). Map roles to your identity provider (Okta, Azure AD) and require MFA for Admin and Auditor roles.

#### PII Filtering Pipeline

End-to-end pipeline ensuring PII never reaches the vector store:

1. **Detection**: Scan source documents for PII before embedding. Use Microsoft Presidio or regex patterns for structured PII (emails, SSNs, phone numbers, credit card numbers). NER models catch unstructured PII (names, addresses).
2. **Redaction**: Replace detected PII with typed placeholders (e.g., `[EMAIL_1]`, `[SSN_2]`) before chunking and embedding. PII must never reach the embedding model -- embeddings of PII-containing text leak information via inversion attacks.
3. **Metadata separation**: Store the PII-to-placeholder mapping in an encrypted, access-controlled vault (e.g., AWS Secrets Manager, HashiCorp Vault) -- never in vector DB metadata fields.
4. **Query-time filtering**: After retrieval, redact any residual PII from retrieved chunks before injecting into LLM context. Defense-in-depth against ingestion pipeline misses.
5. **Audit trail**: Log every embedding operation with fields: `doc_id`, `PII_detected` (boolean + count by type), `PII_action` (redacted/skipped/flagged), `user`, `timestamp`. Retain logs per your data retention policy (minimum 1 year for SOC 2).

### 4.4 Compliance

- **GDPR right to erasure**: Must support point deletes. IVF does not reclaim cluster space until rebuild.
- **SOC 2**: Pinecone and Weaviate Cloud are SOC 2 Type II certified. Self-hosted requires your own certification.
- **Data residency**: Pinecone offers region selection (US, EU, AP). Self-hosted gives full control.

---

## Part 5: Production Enterprise Code

### 5.1 Embedding Service with Circuit Breaker, Quantization, and Drift Detection

```python
"""
Production embedding service: model-versioned embeddings, circuit breaker,
MRL truncation, scalar quantization, and cosine drift detection.
"""
import time
import hashlib
import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CircuitState(Enum):
    CLOSED = "closed"        # normal operation
    OPEN = "open"            # failing, reject calls
    HALF_OPEN = "half_open"  # testing recovery


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    failure_count: int = field(default=0, init=False)
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    last_failure_time: float = field(default=0.0, init=False)

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: allow one probe


@dataclass
class EmbeddingConfig:
    model_name: str = "text-embedding-3-large"
    model_version: str = "2024-01-25"
    full_dims: int = 3072
    truncated_dims: int = 768       # MRL truncation target
    quantize_to_int8: bool = True
    similarity_metric: str = "cosine"  # must match model training


class DriftDetector:
    """Tracks cosine distance between current and reference embeddings."""

    def __init__(self, reference_embeddings: np.ndarray, alert_threshold: float = 0.08):
        self.reference_mean = reference_embeddings.mean(axis=0)
        self.reference_mean /= np.linalg.norm(self.reference_mean)
        self.alert_threshold = alert_threshold
        self.window: list[float] = []
        self.window_size = 500

    def check(self, embedding: np.ndarray) -> dict:
        normed = embedding / np.linalg.norm(embedding)
        cosine_dist = 1.0 - float(np.dot(normed, self.reference_mean))
        self.window.append(cosine_dist)
        if len(self.window) > self.window_size:
            self.window = self.window[-self.window_size:]

        mean_drift = sum(self.window) / len(self.window)
        return {
            "current_distance": round(cosine_dist, 6),
            "rolling_mean_drift": round(mean_drift, 6),
            "alert": mean_drift > self.alert_threshold,
            "samples_in_window": len(self.window),
        }


class EmbeddingService:
    """
    Production embedding service with MRL truncation, int8 quantization,
    circuit-breaker-protected API calls, and drift monitoring.
    """

    def __init__(self, config: EmbeddingConfig, drift_detector: Optional[DriftDetector] = None):
        self.config = config
        self.breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        self.drift_detector = drift_detector

    def _call_embedding_api(self, texts: list[str]) -> np.ndarray:
        """
        Replace this with your actual API call (OpenAI, Voyage, Cohere, etc.).
        Returns raw float32 embeddings of shape (len(texts), full_dims).
        """
        rng = np.random.default_rng(
            int(hashlib.sha256(texts[0].encode()).hexdigest()[:8], 16)
        )
        raw = rng.standard_normal((len(texts), self.config.full_dims)).astype(np.float32)
        norms = np.linalg.norm(raw, axis=1, keepdims=True)
        return raw / norms

    def _truncate_mrl(self, embeddings: np.ndarray) -> np.ndarray:
        """Matryoshka truncation: keep first truncated_dims dimensions, re-normalize."""
        truncated = embeddings[:, :self.config.truncated_dims]
        norms = np.linalg.norm(truncated, axis=1, keepdims=True)
        return truncated / norms

    def _quantize_int8(self, embeddings: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Scalar quantization to int8. Returns (quantized, scales, offsets) for dequant."""
        mins = embeddings.min(axis=1, keepdims=True)
        maxs = embeddings.max(axis=1, keepdims=True)
        scales = (maxs - mins) / 255.0
        scales = np.where(scales == 0, 1.0, scales)
        quantized = np.round((embeddings - mins) / scales).astype(np.int8)
        return quantized, scales.squeeze(), mins.squeeze()

    def embed(self, texts: list[str]) -> dict:
        """
        Full pipeline: API call -> MRL truncation -> optional int8 quantization.
        Returns dict with embeddings, metadata, and drift status.
        """
        if not self.breaker.allow_request():
            return {"error": "circuit_open", "fallback": "bm25"}

        start = time.time()
        try:
            raw = self._call_embedding_api(texts)
            self.breaker.record_success()
        except Exception as e:
            self.breaker.record_failure()
            return {"error": str(e), "fallback": "bm25"}

        truncated = self._truncate_mrl(raw)

        drift_status = None
        if self.drift_detector and len(texts) == 1:
            drift_status = self.drift_detector.check(truncated[0])

        if self.config.quantize_to_int8:
            quantized, scales, offsets = self._quantize_int8(truncated)
            result_embeddings = quantized
        else:
            result_embeddings = truncated

        return {
            "embeddings": result_embeddings,
            "model": self.config.model_name,
            "model_version": self.config.model_version,
            "dims": self.config.truncated_dims,
            "quantized": self.config.quantize_to_int8,
            "latency_ms": round((time.time() - start) * 1000, 2),
            "drift": drift_status,
        }


# --- Canary Query Monitor ---

class CanaryMonitor:
    """
    Runs fixed queries periodically and checks NDCG@10 against known-good results.
    Alerts when retrieval quality degrades beyond threshold.
    """

    def __init__(self, canary_queries: dict[str, list[str]], alert_threshold: float = 0.05):
        """
        canary_queries: {"query_text": ["expected_doc_id_1", "expected_doc_id_2", ...]}
        """
        self.canary_queries = canary_queries
        self.alert_threshold = alert_threshold
        self.history: list[dict] = []

    @staticmethod
    def _dcg(relevances: list[float], k: int = 10) -> float:
        relevances = relevances[:k]
        return sum(rel / np.log2(i + 2) for i, rel in enumerate(relevances))

    def ndcg_at_k(self, expected: list[str], retrieved: list[str], k: int = 10) -> float:
        relevances = [1.0 if doc in expected else 0.0 for doc in retrieved[:k]]
        ideal = sorted(relevances, reverse=True)
        dcg = self._dcg(relevances, k)
        idcg = self._dcg(ideal, k)
        return dcg / idcg if idcg > 0 else 0.0

    def run_check(self, search_fn) -> dict:
        """
        search_fn: callable taking query string, returning list of doc_ids.
        Returns aggregate health status.
        """
        scores = []
        per_query = {}
        for query, expected_docs in self.canary_queries.items():
            retrieved = search_fn(query)
            score = self.ndcg_at_k(expected_docs, retrieved)
            scores.append(score)
            per_query[query] = round(score, 4)

        mean_ndcg = sum(scores) / len(scores) if scores else 0.0
        baseline = self.history[-1]["mean_ndcg"] if self.history else mean_ndcg
        drop = baseline - mean_ndcg

        result = {
            "mean_ndcg_at_10": round(mean_ndcg, 4),
            "per_query": per_query,
            "drop_from_baseline": round(drop, 4),
            "alert": drop > self.alert_threshold,
            "timestamp": time.time(),
        }
        self.history.append(result)
        return result


# --- Usage Example ---

if __name__ == "__main__":
    # 1. Build reference embeddings for drift detection
    config = EmbeddingConfig(
        model_name="voyage-3-large",
        model_version="2026-01-15",
        full_dims=1024,
        truncated_dims=256,
        quantize_to_int8=True,
    )

    # Simulate reference corpus embeddings
    rng = np.random.default_rng(42)
    reference = rng.standard_normal((200, 256)).astype(np.float32)
    reference /= np.linalg.norm(reference, axis=1, keepdims=True)

    detector = DriftDetector(reference, alert_threshold=0.08)
    service = EmbeddingService(config, drift_detector=detector)

    # 2. Embed a query
    result = service.embed(["How do I configure HNSW parameters for pgvector?"])
    print(f"Model: {result['model']} v{result['model_version']}")
    print(f"Dims: {result['dims']}, Quantized: {result['quantized']}")
    print(f"Latency: {result['latency_ms']}ms")
    if result.get("drift"):
        print(f"Drift alert: {result['drift']['alert']}, "
              f"rolling mean: {result['drift']['rolling_mean_drift']}")

    # 3. Canary monitor
    canaries = {
        "password reset": ["doc_auth_001", "doc_auth_002", "doc_faq_015"],
        "billing invoice": ["doc_billing_001", "doc_billing_003"],
    }
    monitor = CanaryMonitor(canaries, alert_threshold=0.05)

    def mock_search(query: str) -> list[str]:
        if "password" in query:
            return ["doc_auth_001", "doc_auth_002", "doc_faq_015", "doc_other_01"]
        return ["doc_billing_001", "doc_unrelated", "doc_billing_003"]

    health = monitor.run_check(mock_search)
    print(f"\nCanary NDCG@10: {health['mean_ndcg_at_10']}, Alert: {health['alert']}")
```

### 5.2 Vector DB Selection and Quantization Impact Calculator

```python
"""
Capacity planning calculator: estimate memory, storage, and cost
for different vector DB configurations and quantization strategies.
"""
from dataclasses import dataclass


@dataclass
class VectorConfig:
    num_vectors: int
    dimensions: int
    hnsw_m: int = 16
    bytes_per_dim: float = 4.0  # 4=FP32, 2=FP16, 1=Int8
    quantization: str = "none"  # "none", "fp16", "int8", "pq", "binary", "mrl_int8"
    pq_subvectors: int = 32
    mrl_target_dims: int = 256


def estimate_memory(cfg: VectorConfig) -> dict:
    """Estimate RAM requirements for HNSW index."""
    effective_dims = cfg.dimensions
    bpd = cfg.bytes_per_dim

    if cfg.quantization == "fp16":
        bpd = 2.0
    elif cfg.quantization == "int8":
        bpd = 1.0
    elif cfg.quantization == "pq":
        bpd = cfg.pq_subvectors / cfg.dimensions  # 1 byte per subvector code
        effective_dims = cfg.dimensions
    elif cfg.quantization == "binary":
        bpd = 1.0 / 8.0  # 1 bit per dimension
    elif cfg.quantization == "mrl_int8":
        effective_dims = cfg.mrl_target_dims
        bpd = 1.0

    vector_bytes = cfg.num_vectors * effective_dims * bpd
    graph_bytes = cfg.num_vectors * 4 * cfg.hnsw_m * 1.1  # edge list overhead
    overhead_factor = 1.2  # allocator, metadata, padding
    total = (vector_bytes + graph_bytes) * overhead_factor

    return {
        "vector_storage_gb": round(vector_bytes / (1024**3), 2),
        "graph_overhead_gb": round(graph_bytes / (1024**3), 2),
        "total_ram_gb": round(total / (1024**3), 2),
        "compression_ratio": round(
            (cfg.num_vectors * cfg.dimensions * 4) / max(vector_bytes, 1), 1
        ),
        "quantization": cfg.quantization,
        "effective_dims": effective_dims,
    }


def compare_quantization(num_vectors: int, dimensions: int) -> None:
    """Print comparison table of quantization strategies."""
    strategies = [
        ("FP32 (baseline)", "none", 4.0),
        ("FP16", "fp16", 2.0),
        ("Int8 (SQ)", "int8", 1.0),
        ("PQ (m=32)", "pq", 4.0),
        ("Binary", "binary", 4.0),
        ("MRL(256)+Int8", "mrl_int8", 1.0),
    ]

    print(f"\nQuantization Comparison: {num_vectors/1e6:.0f}M vectors, {dimensions}-dim")
    print(f"{'Strategy':<20} {'RAM (GB)':>10} {'Compress':>10} {'Recall*':>10}")
    print("-" * 55)

    recall_estimates = {
        "none": "1.000", "fp16": "0.999", "int8": "0.996",
        "pq": "0.940", "binary": "0.900", "mrl_int8": "0.950",
    }

    for label, quant, bpd in strategies:
        cfg = VectorConfig(
            num_vectors=num_vectors, dimensions=dimensions,
            bytes_per_dim=bpd, quantization=quant,
        )
        est = estimate_memory(cfg)
        recall = recall_estimates.get(quant, "N/A")
        print(f"{label:<20} {est['total_ram_gb']:>10.1f} {est['compression_ratio']:>9.1f}x {recall:>10}")


if __name__ == "__main__":
    compare_quantization(num_vectors=10_000_000, dimensions=768)
    compare_quantization(num_vectors=50_000_000, dimensions=1536)
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: 10M-Document Enterprise RAG System

**Problem Statement**: A Fortune 500 company needs semantic search over 10M internal documents (50M chunks). Requirements: 100 QPS sustained, p95 < 100ms, hybrid search (semantic + keyword), multi-tenant (200+ teams), SOC 2 compliance, and ability to upgrade embedding models without downtime.

**Architecture**:

```
┌───────────────────────────────────────────────────────────────────┐
│                        INGESTION PIPELINE                         │
│                                                                   │
│  Source Docs ──▶ Chunker (512 tokens, 50 overlap)                │
│              ──▶ PII Scrubber (NER + regex)                      │
│              ──▶ Embedding Service (Voyage 3-large, int8 SQ)     │
│              ──▶ Qdrant (HNSW M=16, ef_c=200)                   │
│              ──▶ BM25 Sparse Index (Qdrant native)               │
│              ──▶ Metadata Store (tenant_id, doc_type, timestamp) │
└───────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────┐
│                        QUERY PIPELINE                             │
│                                                                   │
│  Query ──▶ Auth (tenant_id extraction)                           │
│        ──▶ Embedding Service (same model, circuit-breaker)       │
│        ──▶ Hybrid Search:                                        │
│            ├── Dense: HNSW ef_search=128, top-50, tenant filter  │
│            └── Sparse: BM25, top-50, tenant filter               │
│        ──▶ RRF Fusion (k=60)                                    │
│        ──▶ BGE-reranker-v2 (top-20 ──▶ top-5)                   │
│        ──▶ LLM (Claude Sonnet) with top-5 chunks                │
│        ──▶ Response + source citations                           │
└───────────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Decision | Option A (Chosen) | Option B (Rejected) | Rationale |
|----------|-------------------|---------------------|-----------|
| Vector DB | Qdrant self-hosted | Pinecone Serverless | Cost ($400/mo vs $800/mo), no lock-in, full HNSW tuning control |
| Embedding | Voyage 3-large (API) | Qwen3-Embedding-8B (self-hosted) | Ops simplicity wins at launch; migrate to self-hosted when ROI justifies GPU infra |
| Multi-tenancy | Payload filter (small), collection-per-tenant (large) | Namespace-only | Large tenants (>500K docs) need isolated HNSW graphs to avoid filter-induced recall degradation |
| Quantization | Int8 SQ | PQ | Int8 retains 99%+ recall with 4x compression; PQ's 97% recall risks user-facing quality complaints |
| Model upgrade | Blue-green index swap via alias | In-place re-embedding | Zero-downtime requirement; rollback in seconds via alias revert |

**Decision Rationale**: The hybrid Qdrant architecture balances cost (~$2,000/mo for 3-node cluster + embedding API) against latency requirements. Int8 quantization keeps 50M vectors in ~47 GB RAM across the cluster. The blue-green pattern allows embedding model upgrades every quarter without affecting production traffic. Payload-based tenant filtering works for the 95% of tenants with <100K docs; the 5% of large tenants get dedicated collections to avoid HNSW graph-trapping under heavy filtering.

---

### Scenario 2: Cost-Optimized Billion-Scale Image Similarity

**Problem Statement**: An e-commerce platform needs visual similarity search across 1.2B product images. Requirements: 1000 QPS, p95 < 50ms, cost under $20K/month, handle 2M new images/day, and support filtered search by category and price range.

**Architecture**:

```
┌───────────────────────────────────────────────────────────────────┐
│                     EMBEDDING CLUSTER                             │
│                                                                   │
│  Image Upload ──▶ CLIP ViT-L/14 (self-hosted, 4x A100)          │
│               ──▶ L2 normalize ──▶ MRL truncate 768 ──▶ 256     │
│               ──▶ Write-Ahead Log (Kafka)                        │
└───────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────┐
│                     MILVUS CLUSTER (8 nodes)                      │
│                                                                   │
│  Index: IVF+PQ                                                   │
│    nlist = 65536 (sqrt(1.2B) * 2)                                │
│    nprobe = 128 (tuned for 0.95 recall)                          │
│    PQ: m=32, k=256 (32 bytes/vector)                             │
│                                                                   │
│  Storage:                                                        │
│    PQ codes in RAM: 1.2B * 32 bytes = ~36 GB                    │
│    Raw vectors on NVMe: 1.2B * 256 * 4 = ~1.1 TB               │
│                                                                   │
│  Partitioning: by product_category (natural filter alignment)    │
│  Sharding: Milvus native, 8 shards, consistent hashing          │
└───────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────┐
│                     INCREMENTAL UPDATE                            │
│                                                                   │
│  Kafka WAL ──▶ Batch merge every 4 hours                         │
│            ──▶ Full IVF rebuild weekly (Sunday maintenance)       │
│            ──▶ Cluster balance monitor (alert if max/min > 10x)  │
└───────────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Decision | Option A (Chosen) | Option B (Rejected) | Rationale |
|----------|-------------------|---------------------|-----------|
| Index type | IVF+PQ | HNSW | HNSW at 1.2B vectors needs ~170 GB for graph alone (M=16); IVF+PQ fits 36 GB in RAM |
| Embedding | CLIP self-hosted | Cohere embed-v4 API | At 2M images/day, API cost would be ~$4K/day; self-hosted GPU cluster amortizes to ~$8K/mo |
| MRL truncation | 768 -> 256 dims | Keep 768 | ~3% recall loss is acceptable; 3x storage savings at billion scale = ~$5K/mo infrastructure |
| Rebuild strategy | Weekly full + 4-hour batch merge | Continuous insert only | IVF recall degrades 15-20% after 2M daily inserts without rebalancing centroids |
| Filtered search | Partition by category | Post-filter | IVF partition-based filtering avoids the recall degradation that HNSW post-filtering causes |

**Decision Rationale**: IVF+PQ is the only viable index for billion-scale at this budget. HNSW would require 4-5x more RAM ($50K+/mo on cloud). MRL truncation from 768 to 256 dims loses ~3% recall but saves ~$5K/mo in storage and speeds up distance computation 3x. Category-based partitioning aligns IVF clusters with the most common filter dimension, sidestepping the filtered-search recall problem entirely. The weekly rebuild window handles centroid drift from the 14M new vectors accumulated between rebuilds. Total cost: ~$15K/mo (8-node Milvus cluster + 4x A100 GPUs for embedding), well under the $50K+ a managed Pinecone solution would cost.

---

## Vector DB Selection Quick Reference

| Your Situation | Choose | Why |
|----------------|--------|-----|
| Under 10M vectors, already on Postgres | pgvector | Zero new infrastructure, mature RLS/RBAC |
| Need managed + fastest time to market | Pinecone | Zero ops, but highest lock-in and cost |
| Self-hosted + best latency | Qdrant | Fastest p99 at 10M scale, full HNSW control |
| Hybrid search is primary requirement | Weaviate | Best-in-class native hybrid search |
| Billion-scale + have ops team | Milvus | Only option that handles 1B+ cost-effectively |
