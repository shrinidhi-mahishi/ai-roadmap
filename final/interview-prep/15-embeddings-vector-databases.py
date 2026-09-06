"""Embeddings & Vector Databases -- core patterns for interview prep.

Covers embedding generation, cosine similarity from scratch, vector store CRUD,
ANN index comparison (HNSW vs IVF), hybrid search with RRF fusion, and
embedding dimension reduction / quantization. The vector index is not RAG --
it is the barcode printer and warehouse racking.
"""

import hashlib
import json
import math
import random
import re
import struct
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ============================================================================
# --- Section 1: Generate Embeddings ---
# ============================================================================
# Two approaches: API-based (OpenAI, Voyage, Cohere) and local (sentence-transformers).
# Key: always use input_type="search_query" for asymmetric models on the query side.
# Pin model_id -- changing the model without rebuilding the index is a silent recall disaster.

def generate_embedding_openai(
    text: str,
    model: str = "text-embedding-3-large",
    dimensions: int = 1536,
) -> list[float]:
    """Generate embedding via OpenAI API. Stub for self-contained demo.

    Production notes:
    - OpenAI 3-* outputs L2-normalized vectors, so cosine == dot product == -L2^2
    - Max 2048 inputs/request, 300k tokens summed
    - Native MRL: pass dimensions= to truncate server-side (only if model is MRL-trained)
    - Cost: $0.13/1M tokens (3-large), $0.02/1M tokens (3-small)
    """
    # Stub: return a deterministic pseudo-embedding based on text hash
    # In production: openai.embeddings.create(model=model, input=text, dimensions=dimensions)
    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dimensions)]
    # L2-normalize (OpenAI outputs are normalized)
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def generate_embedding_sentence_transformers(
    text: str,
    model_name: str = "BAAI/bge-m3",
    dimensions: int = 1024,
) -> list[float]:
    """Generate embedding via sentence-transformers (local). Stub for demo.

    Production notes:
    - BGE-M3 produces dense + sparse + ColBERT in one forward pass
    - Self-hosted: best quality (Qwen3-8B: 70.6 MMTEB) at GPU cost
    - Use encode(text, prompt_name="s2p_query") for asymmetric retrieval
    """
    # Stub: deterministic pseudo-embedding
    seed = int(hashlib.sha256((model_name + text).encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dimensions)]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


# ============================================================================
# --- Section 2: Cosine Similarity from Scratch ---
# ============================================================================
# Golden rule: use the metric the embedding model was trained with.
# For L2-normalized vectors (OpenAI 3-*), cosine == dot product == -L2^2.

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Formula: sim = (a . b) / (|a| * |b|)
    Range: [-1, 1] where 1 = identical direction, 0 = orthogonal, -1 = opposite

    For L2-normalized vectors, this simplifies to just the dot product
    (skip the division since |a| = |b| = 1).
    """
    if len(a) != len(b):
        raise ValueError(f"Dimension mismatch: {len(a)} vs {len(b)}")

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def dot_product(a: list[float], b: list[float]) -> float:
    """Dot product: a . b = |a| * |b| * cos(angle).
    Fastest metric (no sqrt/div). Identical to cosine for normalized vectors.
    Required for Pinecone sparse/hybrid indexes."""
    return sum(x * y for x, y in zip(a, b))


def euclidean_distance(a: list[float], b: list[float]) -> float:
    """L2 distance: d = |a - b|. Lower = more similar.
    Sensitive to magnitude. Good for clustering, anomaly detection."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ============================================================================
# --- Section 3: Vector Store CRUD ---
# ============================================================================
# Schema pin: model_id + dimension + metric + index_type + codec.
# Changing ANY element = full re-embed and/or rebuild.

class SimilarityMetric(Enum):
    COSINE = "cosine"
    DOT_PRODUCT = "dotproduct"
    EUCLIDEAN = "euclidean"


@dataclass
class VectorRecord:
    id: str
    vector: list[float]
    metadata: dict = field(default_factory=dict)
    namespace: str = "default"


@dataclass
class SearchResult:
    id: str
    score: float
    metadata: dict


class VectorStore:
    """In-memory vector store demonstrating CRUD + filtered search.

    Production notes:
    - Pinecone: namespace-per-tenant for 32x cost reduction
    - pgvector: use ivfflat or hnsw index, not sequential scan
    - Always use idempotent chunk_id keys for upsert (not insert)
    - Deny include_values for assistant principals (Vec2Text inversion risk)
    """

    def __init__(
        self,
        model_id: str,
        dimension: int,
        metric: SimilarityMetric = SimilarityMetric.COSINE,
    ):
        # Schema pin -- these are immutable after creation
        self.model_id = model_id
        self.dimension = dimension
        self.metric = metric
        self._records: dict[str, dict[str, VectorRecord]] = defaultdict(dict)  # ns -> id -> record

    def upsert(self, record: VectorRecord) -> None:
        """Idempotent upsert keyed by record.id.
        Ingest workers only; never from assistant tool calls."""
        if len(record.vector) != self.dimension:
            raise ValueError(
                f"Dimension mismatch: expected {self.dimension}, "
                f"got {len(record.vector)}"
            )
        self._records[record.namespace][record.id] = record

    def query(
        self,
        vector: list[float],
        k: int = 10,
        namespace: str = "default",
        metadata_filter: Optional[dict] = None,
        include_values: bool = False,  # DENY for assistants (inversion risk)
    ) -> list[SearchResult]:
        """Query nearest neighbors. Namespace scoped (from JWT, not args in prod).

        Critical: include_values=False for assistant principals to prevent
        Vec2Text embedding inversion attacks (92% text recovery on 32 tokens).
        """
        if include_values:
            # In production, check auth role before allowing this
            pass

        records = self._records.get(namespace, {})
        if not records:
            return []

        # Compute similarities
        scored = []
        for record in records.values():
            # Apply metadata filter (pre-filter, not post-filter)
            if metadata_filter:
                if not self._matches_filter(record.metadata, metadata_filter):
                    continue  # Filter in the search, not after

            score = self._compute_similarity(vector, record.vector)
            scored.append((record, score))

        # Sort by similarity (higher = more similar for cosine/dot, lower for L2)
        reverse = self.metric != SimilarityMetric.EUCLIDEAN
        scored.sort(key=lambda x: x[1], reverse=reverse)

        return [
            SearchResult(id=r.id, score=s, metadata=r.metadata)
            for r, s in scored[:k]
        ]

    def delete(self, record_id: str, namespace: str = "default") -> bool:
        """Delete a record by ID."""
        ns = self._records.get(namespace, {})
        if record_id in ns:
            del ns[record_id]
            return True
        return False

    def count(self, namespace: str = "default") -> int:
        return len(self._records.get(namespace, {}))

    def _compute_similarity(self, a: list[float], b: list[float]) -> float:
        if self.metric == SimilarityMetric.COSINE:
            return cosine_similarity(a, b)
        elif self.metric == SimilarityMetric.DOT_PRODUCT:
            return dot_product(a, b)
        elif self.metric == SimilarityMetric.EUCLIDEAN:
            return euclidean_distance(a, b)
        raise ValueError(f"Unknown metric: {self.metric}")

    @staticmethod
    def _matches_filter(metadata: dict, filter_spec: dict) -> bool:
        """Simple metadata filter: all filter keys must match."""
        return all(metadata.get(k) == v for k, v in filter_spec.items())


# ============================================================================
# --- Section 4: ANN Index Comparison (HNSW vs IVF) ---
# ============================================================================
# HNSW: hierarchical graph, O(log N) query, 2-5x RAM, great for < 10M vectors.
# IVF: Voronoi partitions, O(nprobe * N/nlist * d), lower RAM, billion-scale.
# Pinecone does NOT use HNSW (Ananas/PQFS/IVF per slab).

@dataclass
class IndexConfig:
    """Configuration for an ANN index. These parameters have very different
    rebuild costs -- tune ef_search first (cheapest), then M (requires rebuild)."""
    index_type: str           # "hnsw" or "ivf"
    # HNSW params
    m: int = 32               # Connections per node: 12-48, higher = better recall + more RAM
    ef_construction: int = 200  # Build-time depth (frozen at build -- changing requires full rebuild)
    ef_search: int = 64       # Query-time depth (cheapest tuning knob, adjustable per query)
    # IVF params
    nlist: int = 2048         # Number of Voronoi cells (rule of thumb: sqrt(N) to 4*sqrt(N))
    nprobe: int = 64          # Cells to search (higher = better recall, more latency)


# Benchmark reference: 10M 768-dim vectors
INDEX_BENCHMARKS = {
    "HNSW (M=32, efC=200, efS=64)": {
        "p50_ms": 0.31, "p95_ms": 0.42, "recall_at_10": 0.964, "ram": "3x base",
    },
    "IVF (nlist=2048, nprobe=64)": {
        "p50_ms": 0.62, "p95_ms": 0.83, "recall_at_10": 0.962, "ram": "1x base",
    },
    "Flat (brute force)": {
        "p50_ms": 33.1, "p95_ms": 44.7, "recall_at_10": 1.000, "ram": "1x base",
    },
}

# Decision matrix: when to choose which
HNSW_VS_IVF = {
    "recall_out_of_box": ("95%+ easily", "Lower, needs probe tuning"),
    "memory": ("2-5x higher", "Lower footprint"),
    "dynamic_inserts": ("Absorbs without rebuild", "Recall drifts; periodic rebuild needed"),
    "filtered_search": ("Filters can break graph pruning", "Two-level filtering works better"),
    "scale_sweet_spot": ("Under ~10M vectors/node", "Billion-scale where HNSW RAM is prohibitive"),
}


def estimate_hnsw_ram(
    n_vectors: int, dimensions: int, m: int = 32, bytes_per_dim: int = 4
) -> dict:
    """Estimate HNSW RAM usage.

    Formula: RAM = N * (d * bytes_per_dim + M * 2 * sizeof(int) + overhead)
    Example: 10M x 768-d float32, M=32 -> vectors ~28.6 GB, edges ~2.5 GB, total ~34 GB
    """
    vector_bytes = n_vectors * dimensions * bytes_per_dim
    edge_bytes = n_vectors * m * 2 * 4  # 4 bytes per int, bidirectional
    overhead_bytes = n_vectors * 64     # Per-node overhead estimate
    total = vector_bytes + edge_bytes + overhead_bytes

    return {
        "vectors_gb": round(vector_bytes / 1e9, 1),
        "edges_gb": round(edge_bytes / 1e9, 1),
        "overhead_gb": round(overhead_bytes / 1e9, 1),
        "total_gb": round(total / 1e9, 1),
    }


# ============================================================================
# --- Section 5: Hybrid Search (Dense + Sparse + RRF) ---
# ============================================================================
# Pure semantic misses exact terms (product IDs, error codes).
# Pure keyword misses synonyms. Hybrid with RRF gives both.
# Hybrid outperforms pure semantic by 15-30% on mixed queries.

class BM25Index:
    """BM25 sparse retrieval for hybrid search.
    In production, use Elasticsearch or Pinecone native sparse vectors."""

    def __init__(self, k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.df: dict[str, int] = defaultdict(int)        # document frequency
        self.postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.doc_lengths: dict[str, int] = {}
        self.avg_dl: float = 0.0

    def index(self, doc_id: str, text: str) -> None:
        tokens = text.lower().split()
        self.doc_lengths[doc_id] = max(1, len(tokens))
        for token in set(tokens):
            self.df[token] += 1
        for token in tokens:
            self.postings[token][doc_id] += 1
        n = len(self.doc_lengths)
        self.avg_dl = sum(self.doc_lengths.values()) / max(1, n)

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        tokens = query.lower().split()
        scores: dict[str, float] = defaultdict(float)
        n = max(1, len(self.doc_lengths))

        for token in tokens:
            df = self.df.get(token, 0)
            if df == 0:
                continue
            idf = math.log((n - df + 0.5) / (df + 0.5) + 1)  # IDF with smoothing
            for doc_id, tf in self.postings.get(token, {}).items():
                dl = self.doc_lengths[doc_id]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl)
                scores[doc_id] += idf * numerator / denominator

        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]


def reciprocal_rank_fusion(
    result_lists: list[list[tuple[str, float]]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion (RRF) to merge multiple ranked lists.

    RRF score = sum over lists of: 1 / (k + rank_i)
    where k=60 is the standard constant that downweights low-ranked results.

    Why RRF over linear combination:
    - Score-agnostic: works when dense (cosine [-1,1]) and sparse (unbounded) scales differ
    - No alpha tuning needed
    - Pinecone hybrid trap: without RRF or hybrid_score_norm, sparse drowns dense
    """
    fused_scores: dict[str, float] = defaultdict(float)

    for results in result_lists:
        for rank, (doc_id, _score) in enumerate(results, start=1):
            fused_scores[doc_id] += 1.0 / (k + rank)

    return sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)


def hybrid_search(
    query: str,
    vector_store: VectorStore,
    bm25_index: BM25Index,
    embed_fn,
    k: int = 10,
    namespace: str = "default",
) -> list[tuple[str, float]]:
    """Hybrid search combining dense (semantic) and sparse (BM25) retrieval.

    Pipeline:
    1. Dense: embed query -> ANN search -> ranked list
    2. Sparse: BM25 search -> ranked list
    3. Fuse with RRF (score-agnostic, no alpha tuning)
    """
    # Dense retrieval
    query_vec = embed_fn(query)
    dense_results = vector_store.query(query_vec, k=k * 2, namespace=namespace)
    dense_ranked = [(r.id, r.score) for r in dense_results]

    # Sparse retrieval
    sparse_ranked = bm25_index.search(query, k=k * 2)

    # Fuse with RRF
    fused = reciprocal_rank_fusion([dense_ranked, sparse_ranked])
    return fused[:k]


# ============================================================================
# --- Section 6: Embedding Dimension Reduction and Quantization ---
# ============================================================================
# MRL (Matryoshka): truncate prefix dimensions, ~95% quality at 4x fewer dims.
# Quantization: codec in front of ANN, not a different algorithm.
# MRL + quantization stack: MRL 1024->256 + Int8 = 16x compression.

def matryoshka_truncate(
    vector: list[float], target_dims: int
) -> list[float]:
    """Matryoshka Representation Learning (MRL) dimension reduction.

    MRL trains models so prefix dimensions contain the most information.
    Truncating a 1024-d MRL vector to 256-d preserves ~95% quality.

    CRITICAL: only works if the model was MRL-trained (OpenAI 3-*, Voyage 4-*, Cohere v4).
    Naive truncation of a non-MRL model destroys quality.
    """
    if target_dims > len(vector):
        raise ValueError(f"Target {target_dims} > original {len(vector)}")

    truncated = vector[:target_dims]
    # Re-normalize after truncation (important for cosine similarity)
    norm = math.sqrt(sum(x * x for x in truncated))
    if norm > 0:
        truncated = [x / norm for x in truncated]
    return truncated


def quantize_int8(vector: list[float]) -> tuple[list[int], float, float]:
    """Scalar quantization to Int8. 4x compression, ~99%+ recall retained.

    Maps float32 values to int8 [-128, 127] range.
    Returns (quantized_vector, scale, zero_point) for dequantization.
    Production default for most use cases.
    """
    v_min = min(vector)
    v_max = max(vector)
    scale = (v_max - v_min) / 255.0 if v_max != v_min else 1.0
    zero_point = v_min

    quantized = [
        max(-128, min(127, round((x - zero_point) / scale - 128)))
        for x in vector
    ]
    return quantized, scale, zero_point


def dequantize_int8(
    quantized: list[int], scale: float, zero_point: float
) -> list[float]:
    """Reverse Int8 quantization for rescoring."""
    return [(q + 128) * scale + zero_point for q in quantized]


def quantize_binary(vector: list[float]) -> list[int]:
    """Binary quantization (BQ). 32x compression, 88-92% recall.

    Each dimension becomes 1 bit: positive -> 1, non-positive -> 0.
    Only practical with models trained for it (Cohere v4) or with
    oversampling + rescore (BBQ: 3x oversample, <5% recall loss).
    """
    return [1 if x > 0 else 0 for x in vector]


def hamming_distance(a: list[int], b: list[int]) -> int:
    """Hamming distance for binary vectors. Used as coarse filter in
    binary search -> float rescore pipeline."""
    return sum(x != y for x, y in zip(a, b))


# Quantization comparison table
QUANTIZATION_METHODS = {
    "FP16": {"compression": "2x", "recall": "~99.9%", "use": "Always safe, negligible loss"},
    "Int8 (SQ)": {"compression": "4x", "recall": "~99%+", "use": "Production default"},
    "PQ (m=32)": {"compression": "64x", "recall": "~97%", "use": "Memory-constrained, large scale"},
    "Binary (BQ)": {"compression": "32x", "recall": "88-92%", "use": "Only with BQ-trained models"},
    "BBQ (ES)": {"compression": "32x + rescore", "recall": "~96%", "use": "Elasticsearch, 3x oversample"},
    "MRL 1024->256 + Int8": {"compression": "16x", "recall": "~95%", "use": "Best balance"},
}


# ============================================================================
# --- Section 7: PII Detection Before Embedding ---
# ============================================================================
# Vec2Text recovers 92% of original text from embeddings.
# PII must be detected, redacted, and audited BEFORE embedding, not after.

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def pii_detect_redact_before_embed(
    text: str, audit_log: list
) -> str:
    """Three steps, all required: detect, redact, audit.
    Block PAN from reaching the embedding model entirely.

    Why this matters for embeddings specifically:
    - Vec2Text recovers 92% of original text from 32-token embeddings
    - 89% recovery of MIMIC clinical names
    - Embeddings are NOT anonymized data
    - Redacting in the prompt after embedding is too late -- the vector is the leak
    """
    pre_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    kinds = []

    if EMAIL_RE.search(text):
        kinds.append("email")
    if PAN_RE.search(text):
        kinds.append("pan")
        # Block PAN from embed entirely (fail-closed)
        audit_log.append({
            "action": "block", "kinds": kinds, "pre_hash": pre_hash,
            "reason": "PAN detected -- blocked from embedding pipeline",
        })
        raise PermissionError("pii_block:pan -- cannot embed text containing PAN")

    # Redact emails with stable tokens (so dedup still works)
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:8]}]",
        text,
    )
    action = "redact" if redacted != text else "allow"
    audit_log.append({"action": action, "kinds": kinds, "pre_hash": pre_hash})
    return redacted


# ============================================================================
# --- Section 8: Namespace Cost Impact ---
# ============================================================================
# The single biggest cost lever in vector search is namespace topology.
# Fat namespace vs tenant namespaces: 32x cost difference.

NAMESPACE_COST_EXAMPLE = {
    "scenario": "20M vectors x 1536-d at 50 QPS on Pinecone Standard",
    "fat_namespace": {"ru": 533, "monthly_cost": "$263,000"},
    "tenant_namespaces_20": {"ru": 17, "monthly_cost": "$8,300"},
    "cost_ratio": "32x",
    "lesson": "Namespace topology, not embed price, is the dominant cost lever",
}


# ============================================================================
# --- Demo ---
# ============================================================================

if __name__ == "__main__":
    # 1. Generate embeddings
    vec_a = generate_embedding_openai("How do I reset my password?", dimensions=256)
    vec_b = generate_embedding_openai("I forgot my login credentials", dimensions=256)
    vec_c = generate_embedding_openai("What is the weather today?", dimensions=256)
    assert len(vec_a) == 256

    # 2. Cosine similarity from scratch
    sim_ab = cosine_similarity(vec_a, vec_b)
    sim_ac = cosine_similarity(vec_a, vec_c)
    # Semantically similar texts should have higher similarity
    print(f"Similarity (password/credentials): {sim_ab:.4f}")
    print(f"Similarity (password/weather):     {sim_ac:.4f}")

    # Verify metric equivalence for normalized vectors
    dot_ab = dot_product(vec_a, vec_b)
    assert abs(sim_ab - dot_ab) < 1e-6, "Cosine == dot product for normalized vectors"

    # 3. Vector store CRUD
    store = VectorStore(model_id="text-embedding-3-large", dimension=256, metric=SimilarityMetric.COSINE)

    docs = [
        ("doc1", "How do I reset my password?", {"category": "auth"}),
        ("doc2", "I forgot my login credentials", {"category": "auth"}),
        ("doc3", "What is the weather today?", {"category": "general"}),
        ("doc4", "Change my account password", {"category": "auth"}),
        ("doc5", "Server error code 500 troubleshooting", {"category": "infra"}),
    ]

    for doc_id, text, meta in docs:
        vec = generate_embedding_openai(text, dimensions=256)
        store.upsert(VectorRecord(id=doc_id, vector=vec, metadata=meta))

    assert store.count() == 5

    # Query with metadata filter (pre-filter, not post-filter)
    query_vec = generate_embedding_openai("password reset help", dimensions=256)
    results = store.query(query_vec, k=3, metadata_filter={"category": "auth"})
    print(f"\nTop 3 results (auth category only):")
    for r in results:
        print(f"  {r.id}: score={r.score:.4f}")

    # Delete
    store.delete("doc3")
    assert store.count() == 4

    # 4. HNSW RAM estimation
    ram = estimate_hnsw_ram(n_vectors=10_000_000, dimensions=768, m=32)
    print(f"\nHNSW RAM for 10M x 768-d: {ram['total_gb']} GB")
    print(f"  Vectors: {ram['vectors_gb']} GB, Edges: {ram['edges_gb']} GB")

    # 5. Hybrid search with RRF
    bm25 = BM25Index()
    for doc_id, text, meta in docs:
        bm25.index(doc_id, text)

    # RRF fusion of dense + sparse
    dense_results = [(r.id, r.score) for r in store.query(query_vec, k=5)]
    sparse_results = bm25.search("password reset", k=5)
    fused = reciprocal_rank_fusion([dense_results, sparse_results])
    print(f"\nHybrid search (RRF) results:")
    for doc_id, score in fused[:3]:
        print(f"  {doc_id}: rrf_score={score:.4f}")

    # 6. Dimension reduction and quantization
    original = generate_embedding_openai("test embedding", dimensions=1024)

    # MRL truncation (only for MRL-trained models)
    truncated = matryoshka_truncate(original, target_dims=256)
    assert len(truncated) == 256
    # Verify still normalized
    norm = math.sqrt(sum(x * x for x in truncated))
    assert abs(norm - 1.0) < 1e-6

    # Int8 quantization
    quantized, scale, zp = quantize_int8(original)
    restored = dequantize_int8(quantized, scale, zp)
    # Check reconstruction error is small
    mse = sum((a - b) ** 2 for a, b in zip(original, restored)) / len(original)
    print(f"\nInt8 quantization MSE: {mse:.8f}")

    # Binary quantization
    binary = quantize_binary(original)
    assert all(b in (0, 1) for b in binary)
    binary2 = quantize_binary(generate_embedding_openai("another test", dimensions=1024))
    hdist = hamming_distance(binary, binary2)
    print(f"Binary Hamming distance: {hdist}/{len(binary)}")

    # 7. PII detection before embedding
    audit = []
    clean = pii_detect_redact_before_embed("Contact alice@example.com for help", audit)
    assert "[EMAIL_" in clean
    assert audit[-1]["action"] == "redact"

    try:
        pii_detect_redact_before_embed("Card: 4111 1111 1111 1111", audit)
        assert False, "Should have blocked PAN"
    except PermissionError:
        assert audit[-1]["action"] == "block"

    print(f"\nPII audit: {len(audit)} entries")
    print(f"Quantization methods: {len(QUANTIZATION_METHODS)}")
    print(f"Namespace cost lesson: {NAMESPACE_COST_EXAMPLE['cost_ratio']} difference")
    print("\nAll embedding & vector DB patterns validated successfully.")
