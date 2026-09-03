"""
Retrieval-Augmented Generation (RAG): fetch relevant documents at query time, stuff them
into the prompt, and generate grounded answers. This file covers the core pipeline from
chunking through embedding, hybrid search, reranking, and end-to-end RAG generation.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field


# ═══ Text Chunking Strategies ═══
# Chunking is an ingest-plane compiler. Retrieval quality is often more sensitive to
# chunk policy than to embedding model choice. Production default: 400-800 tokens.

def fixed_size_chunks(text: str, chunk_size: int = 200, overlap: int = 40) -> list[str]:
    """Fixed character window with overlap. Simple but splits mid-sentence."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap  # overlap prevents boundary information loss
    return chunks


def recursive_split(text: str, max_size: int = 200) -> list[str]:
    """Recursive splitting: try paragraph -> newline -> sentence -> word boundaries.
    This mirrors LangChain's RecursiveCharacterTextSplitter hierarchy."""
    separators = ["\n\n", "\n", ". ", " "]
    if len(text) <= max_size:
        return [text]

    for sep in separators:
        parts = text.split(sep)
        if len(parts) > 1:
            chunks = []
            current = ""
            for part in parts:
                candidate = (current + sep + part).strip() if current else part
                if len(candidate) <= max_size:
                    current = candidate
                else:
                    if current:
                        chunks.append(current)
                    # If single part exceeds max, try next separator
                    current = part
            if current:
                chunks.append(current)
            if all(len(c) <= max_size for c in chunks):
                return chunks
    # Fallback: hard split
    return fixed_size_chunks(text, max_size, max_size // 5)


def semantic_boundary_chunks(text: str) -> list[str]:
    """Structure-aware chunking: split on markdown headings and paragraph breaks.
    Best for documents with clear section structure (legal, technical docs)."""
    # Split on markdown headings or double newlines
    sections = re.split(r'(?=^#{1,3}\s)', text, flags=re.MULTILINE)
    chunks = []
    for section in sections:
        section = section.strip()
        if section:
            # Further split large sections on paragraph breaks
            if len(section) > 500:
                chunks.extend(section.split("\n\n"))
            else:
                chunks.append(section)
    return [c.strip() for c in chunks if c.strip()]


def demo_chunking():
    sample = (
        "# Introduction\n\nRAG solves the knowledge cutoff problem. "
        "It fetches relevant docs at query time.\n\n"
        "# How It Works\n\nFirst, embed the query. Then search the vector DB. "
        "Finally, generate an answer from retrieved context.\n\n"
        "The pipeline has two phases: ingest and query. "
        "Ingest runs offline. Query runs in real-time."
    )
    print("  Fixed-size chunks:", [c[:40] + "..." for c in fixed_size_chunks(sample, 80, 15)])
    print("  Recursive chunks:", [c[:40] + "..." for c in recursive_split(sample, 100)])
    print("  Semantic chunks:", [c[:40] + "..." for c in semantic_boundary_chunks(sample)])


# ═══ Embedding and Cosine Similarity Search ═══
# Bi-encoder: independently embed query and docs, compare via cosine.
# Fast (O(1) lookup after indexing) but sees query and doc in isolation.

def fake_embed(text: str, dim: int = 8) -> list[float]:
    """Deterministic pseudo-embedding for demo. Real: call OpenAI/Voyage/Cohere API."""
    # Hash characters to produce a stable vector -- NOT a real embedding
    vals = [0.0] * dim
    for i, ch in enumerate(text.lower()):
        vals[i % dim] += ord(ch) * 0.001
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Core of dense retrieval."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def vector_search(query: str, corpus: list[str], top_k: int = 3) -> list[tuple[str, float]]:
    """Dense retrieval: embed query, compare against all doc embeddings."""
    q_vec = fake_embed(query)
    scored = [(doc, cosine_similarity(q_vec, fake_embed(doc))) for doc in corpus]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def demo_vector_search():
    corpus = [
        "RAG retrieves documents and augments the LLM prompt",
        "Fine-tuning changes model weights on domain data",
        "Vector databases store embeddings for similarity search",
        "BM25 is a keyword-based retrieval algorithm",
        "Prompt engineering crafts instructions for LLMs",
    ]
    results = vector_search("How does retrieval augmented generation work?", corpus)
    for doc, score in results:
        print(f"  [{score:.3f}] {doc}")


# ═══ Hybrid Search: BM25 + Vector with RRF Fusion ═══
# Dense retrieval misses exact IDs; BM25 misses paraphrases. Run both, then fuse.
# RRF is scale-free: ranks are always comparable regardless of score distributions.

def bm25_score(query: str, doc: str, avg_dl: float = 50.0, k1: float = 1.5, b: float = 0.75) -> float:
    """Simplified BM25 scoring for a single document."""
    query_terms = query.lower().split()
    doc_terms = doc.lower().split()
    doc_freq = Counter(doc_terms)
    dl = len(doc_terms)
    score = 0.0
    for term in query_terms:
        tf = doc_freq.get(term, 0)
        # IDF approximation (assumes small corpus, so use smoothed log)
        idf = math.log(2.0)  # simplified -- real BM25 uses corpus-wide IDF
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * dl / avg_dl)
        score += idf * numerator / denominator
    return score


def bm25_search(query: str, corpus: list[str], top_k: int = 5) -> list[tuple[str, float]]:
    """Keyword search using BM25 scoring."""
    scored = [(doc, bm25_score(query, doc)) for doc in corpus]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def reciprocal_rank_fusion(
    *ranked_lists: list[tuple[str, float]], k: int = 60
) -> list[tuple[str, float]]:
    """RRF: fuse multiple ranked lists using reciprocal rank. k=60 is the standard default.
    Documents appearing in BOTH lists outrank single-list winners."""
    fused: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (doc, _score) in enumerate(ranked, start=1):
            fused[doc] = fused.get(doc, 0) + 1.0 / (k + rank)
    result = sorted(fused.items(), key=lambda x: x[1], reverse=True)
    return result


def demo_hybrid_search():
    corpus = [
        "RAG retrieves documents and augments the LLM prompt",
        "BM25 is a keyword-based retrieval algorithm using term frequency",
        "Vector databases store embeddings for similarity search",
        "Retrieval augmented generation combines search with LLMs",
        "Error code TS-999 requires restarting the BM25 index service",
    ]
    query = "retrieval augmented generation BM25"
    dense = vector_search(query, corpus, top_k=5)
    sparse = bm25_search(query, corpus, top_k=5)
    fused = reciprocal_rank_fusion(dense, sparse)

    print("  Dense top-3:", [d[:40] for d, _ in dense[:3]])
    print("  BM25 top-3:", [d[:40] for d, _ in sparse[:3]])
    print("  RRF fused top-3:")
    for doc, score in fused[:3]:
        print(f"    [{score:.4f}] {doc[:60]}")


# ═══ Reranking (Cross-Encoder Style) ═══
# Stage 1 (bi-encoder + BM25) casts a wide net. Stage 2 (cross-encoder) scores
# each (query, doc) pair jointly -- much better relevance but O(N) per candidate.
# Production pattern: 50-150 candidates -> rerank -> top 5-20 to generator.

def cross_encoder_rerank(query: str, docs: list[str], top_n: int = 3) -> list[tuple[str, float]]:
    """Simulated cross-encoder reranking. Real: use Cohere/Voyage/bge-reranker API.
    Cross-encoder jointly attends over (query, doc) -- much better than bi-encoder."""
    scored = []
    for doc in docs:
        # Simulate joint attention: reward query-term overlap + semantic proximity
        q_terms = set(query.lower().split())
        d_terms = set(doc.lower().split())
        overlap = len(q_terms & d_terms) / max(len(q_terms), 1)
        # Blend with cosine for a richer signal
        cos = cosine_similarity(fake_embed(query), fake_embed(doc))
        # Cross-encoders typically outperform this -- we approximate for demo
        score = 0.6 * overlap + 0.4 * cos
        scored.append((doc, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


def demo_reranking():
    candidates = [
        "RAG retrieves documents and augments the LLM prompt",
        "Fine-tuning changes model weights on domain data",
        "Retrieval augmented generation combines search with LLMs",
        "BM25 is a keyword-based retrieval algorithm",
        "Vector databases store embeddings for similarity search",
    ]
    reranked = cross_encoder_rerank("How does RAG work?", candidates, top_n=3)
    print("  Reranked top-3:")
    for doc, score in reranked:
        print(f"    [{score:.3f}] {doc}")


# ═══ Full RAG Pipeline ═══
# Chunk -> Embed -> Store -> Query -> Retrieve (hybrid) -> Rerank -> Augment -> Generate.

@dataclass
class RAGPipeline:
    """End-to-end RAG: ingest docs, then answer questions with retrieval."""
    chunks: list[str] = field(default_factory=list)

    def ingest(self, documents: list[str], chunk_size: int = 150):
        """Ingest plane: chunk documents and store. In prod, also embed + index."""
        for doc in documents:
            self.chunks.extend(recursive_split(doc, chunk_size))
        print(f"  Ingested {len(documents)} docs -> {len(self.chunks)} chunks")

    def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Query plane: hybrid search + rerank."""
        # Stage 1: cast wide net with both retrieval methods
        dense_results = vector_search(query, self.chunks, top_k=top_k)
        bm25_results = bm25_search(query, self.chunks, top_k=top_k)
        fused = reciprocal_rank_fusion(dense_results, bm25_results)
        candidates = [doc for doc, _ in fused[:top_k]]

        # Stage 2: rerank for precision (150 -> 20 in production)
        reranked = cross_encoder_rerank(query, candidates, top_n=3)
        return [doc for doc, _ in reranked]

    def generate(self, query: str) -> str:
        """Full RAG: retrieve context, then generate answer."""
        context_chunks = self.retrieve(query)
        # In production, call LLM API with augmented prompt
        context = "\n---\n".join(context_chunks)
        prompt = f"Context:\n{context}\n\nQuestion: {query}\nAnswer:"
        # Mock LLM response -- real: call Claude/GPT with the augmented prompt
        return f"Based on {len(context_chunks)} retrieved chunks: " + context_chunks[0][:80]


def demo_rag_pipeline():
    pipeline = RAGPipeline()
    docs = [
        "RAG solves the knowledge cutoff problem by fetching documents at query time. "
        "The ingest plane chunks and embeds documents offline. The query plane retrieves "
        "relevant chunks using hybrid search and reranking.",
        "BM25 is a probabilistic retrieval model that scores documents using term frequency "
        "and inverse document frequency. It excels at exact keyword matching but misses "
        "paraphrased queries. Hybrid search combines BM25 with dense retrieval.",
        "Cross-encoder reranking jointly attends over query and document pairs. It is more "
        "accurate than bi-encoder cosine similarity but runs O(N) per candidate. Production "
        "systems rerank the top 50-150 candidates down to 5-20 for the generator.",
    ]
    pipeline.ingest(docs)
    answer = pipeline.generate("How does hybrid search improve retrieval?")
    print(f"  Answer: {answer}")


# ═══ Run All Demos ═══

if __name__ == "__main__":
    print("=" * 60)
    print("1. Text Chunking Strategies")
    print("=" * 60)
    demo_chunking()

    print("\n" + "=" * 60)
    print("2. Embedding + Cosine Similarity Search")
    print("=" * 60)
    demo_vector_search()

    print("\n" + "=" * 60)
    print("3. Hybrid Search: BM25 + Vector with RRF Fusion")
    print("=" * 60)
    demo_hybrid_search()

    print("\n" + "=" * 60)
    print("4. Cross-Encoder Style Reranking")
    print("=" * 60)
    demo_reranking()

    print("\n" + "=" * 60)
    print("5. Full RAG Pipeline (Chunk -> Retrieve -> Generate)")
    print("=" * 60)
    demo_rag_pipeline()
