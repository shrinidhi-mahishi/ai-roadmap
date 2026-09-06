"""
Retrieval-Augmented Generation (RAG) -- Interview Prep Code Snippets.

Covers the core RAG pipeline (embed, retrieve, generate), chunking strategies,
hybrid search with BM25 + dense + RRF fusion, cross-encoder reranking,
agentic RAG loops, and evaluation metrics (faithfulness, relevance, recall).
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# --- Basic RAG Pipeline: Embed -> Retrieve -> Generate ---

@dataclass(frozen=True)
class Chunk:
    """A chunk of text with metadata for retrieval."""
    chunk_id: str
    text: str
    doc_id: str
    score: float = 0.0


def embed_text(text: str, model: str = "text-embedding-3-small") -> list[float]:
    """Stub: embed text into a dense vector.

    In production: call OpenAI, Voyage, Cohere, or a local model.
    Key: pin model_id + dimension + metric in the index schema.
    Changing any one requires a full re-embed -- silent recall collapse otherwise.
    """
    # Deterministic fake embedding for demo (hash-based, 8-dim)
    h = hashlib.sha256(f"{model}:{text}".encode()).hexdigest()
    return [int(h[i:i+2], 16) / 255.0 for i in range(0, 16, 2)]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class DenseRetriever:
    """Simple in-memory dense retriever using cosine similarity.

    Production: use pgvector, Pinecone, Qdrant, Weaviate, etc.
    Critical: authz filter must be applied BEFORE ANN, not after.
    Post-filter ANN fills top-k with forbidden neighbors -> authorized recall -> 0.
    """
    def __init__(self, model: str = "text-embedding-3-small"):
        self.model = model
        self.index: list[tuple[Chunk, list[float]]] = []

    def add(self, chunk: Chunk):
        vec = embed_text(chunk.text, self.model)
        self.index.append((chunk, vec))

    def search(self, query: str, k: int = 5, tenant_filter: Optional[str] = None) -> list[Chunk]:
        q_vec = embed_text(query, self.model)
        scored = []
        for chunk, vec in self.index:
            # ACL filter BEFORE scoring -- this is the invariant
            if tenant_filter and not chunk.doc_id.startswith(tenant_filter):
                continue
            sim = cosine_similarity(q_vec, vec)
            scored.append(Chunk(chunk.chunk_id, chunk.text, chunk.doc_id, sim))
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:k]


def generate_answer(question: str, context_chunks: list[Chunk]) -> str:
    """Stub: generate a grounded answer from retrieved chunks.

    Key rules:
    - Cite ONLY IDs from the retrieved set (constrained decode).
    - Place highest-score chunks at edges (U-shaped attention -- lost-in-the-middle).
    - Never generate ungrounded if retrieval returns empty on sensitive corpora.
    """
    if not context_chunks:
        return "Retrieval unavailable. Cannot provide a grounded answer."

    # U-shape: best chunks at edges (position 0 and -1)
    ranked = sorted(context_chunks, key=lambda c: c.score, reverse=True)
    edge_ordered = [ranked[0]] + ranked[2:] + ([ranked[1]] if len(ranked) > 1 else [])

    ctx = "\n".join(f"[{c.chunk_id}] {c.text}" for c in edge_ordered)
    allowed_ids = sorted(c.chunk_id for c in context_chunks)

    # In production: this is an LLM call with these instructions
    prompt = (
        f"Answer ONLY from the passages below. "
        f"Cite ids from {allowed_ids}.\n\n"
        f"Passages:\n{ctx}\n\n"
        f"Question: {question}"
    )
    # Stub response
    return f"[Generated answer using chunks {allowed_ids}] {prompt[:200]}..."


def basic_rag_pipeline(question: str, retriever: DenseRetriever, k: int = 5) -> str:
    """The simplest RAG pipeline: embed query -> retrieve -> generate.

    This is "naive RAG." Production adds hybrid search, reranking,
    an agent loop, and grading -- see sections below.
    """
    chunks = retriever.search(question, k=k)
    return generate_answer(question, chunks)


# --- Chunking Strategies ---

def chunk_fixed_size(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Fixed-size chunking with overlap.

    Simplest strategy. Predictable vector count.
    Weakness: splits mid-sentence, orphans pronouns.

    Production starting point: 400-800 tokens, 10-20% overlap, sentence snap.
    Context cliff at ~2,500 tokens where quality degrades.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def chunk_recursive(text: str, max_size: int = 500, separators: list[str] = None) -> list[str]:
    """Recursive chunking: try paragraph -> sentence -> word boundaries.

    Top scorer in Vecta 2026 benchmark (69% accuracy).
    Fewer mid-sentence breaks than fixed-size.
    This is what LangChain's RecursiveCharacterTextSplitter does.
    """
    if separators is None:
        separators = ["\n\n", "\n", ". ", " "]

    if len(text) <= max_size:
        return [text]

    # Try each separator in order (most to least preferred)
    for sep in separators:
        parts = text.split(sep)
        if len(parts) > 1:
            chunks = []
            current = ""
            for part in parts:
                candidate = current + sep + part if current else part
                if len(candidate) > max_size and current:
                    chunks.append(current)
                    current = part
                else:
                    current = candidate
            if current:
                chunks.append(current)
            # Recurse on any chunks still too large
            result = []
            for chunk in chunks:
                if len(chunk) > max_size:
                    result.extend(chunk_recursive(chunk, max_size, separators[1:]))
                else:
                    result.append(chunk)
            return result

    # Fallback: hard split
    return chunk_fixed_size(text, max_size, overlap=0)


def chunk_semantic(sentences: list[str], embeddings: list[list[float]],
                   threshold: float = 0.5) -> list[list[str]]:
    """Semantic chunking: split at embedding breakpoints.

    Embed each sentence, find where cosine similarity drops below threshold.
    Good for topic shifts. Weakness: ~14x slower than recursive, unstable boundaries.
    Vecta 2026: 54-70% accuracy (variable).
    """
    if len(sentences) <= 1:
        return [sentences]

    chunks = []
    current_chunk = [sentences[0]]

    for i in range(1, len(sentences)):
        sim = cosine_similarity(embeddings[i - 1], embeddings[i])
        if sim < threshold:
            # Topic shift detected -- start new chunk
            chunks.append(current_chunk)
            current_chunk = [sentences[i]]
        else:
            current_chunk.append(sentences[i])

    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def contextualize_chunk(chunk_text: str, doc_summary: str) -> str:
    """Anthropic Contextual Retrieval: prepend document context to each chunk.

    In production: LLM call per chunk, prompt-cache the full document.
    Cost: ~$1.02/1M document tokens (one-time).
    Result: cuts top-20 retrieval failures ~67% when combined with BM25 + rerank.
    Key: redact PII BEFORE contextualizing, or names/revenue copy into every chunk.
    """
    # In production this is an LLM call:
    # "Given this document: {doc}... Provide a short context for this chunk: {chunk}"
    return f"[Context: {doc_summary[:100]}] {chunk_text}"


# --- Hybrid Search: BM25 + Dense + RRF Fusion ---

class BM25Retriever:
    """Simple BM25 retriever for lexical matching.

    Why BM25 alongside dense: dense misses exact IDs (TS-999, SKUs, statute
    numbers). BM25 misses paraphrase. Hybrid with RRF reaches NDCG 0.7497
    on WANDS -- 7.5% above either retriever alone.
    """
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: list[Chunk] = []
        self.doc_freqs: dict[str, int] = defaultdict(int)
        self.doc_lens: list[int] = []
        self.avg_dl: float = 0.0
        self.vocab: set[str] = set()

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r'\w+', text.lower())

    def add(self, chunk: Chunk):
        tokens = self._tokenize(chunk.text)
        self.docs.append(chunk)
        self.doc_lens.append(len(tokens))
        seen = set()
        for t in tokens:
            self.vocab.add(t)
            if t not in seen:
                self.doc_freqs[t] += 1
                seen.add(t)
        self.avg_dl = sum(self.doc_lens) / len(self.doc_lens)

    def search(self, query: str, k: int = 5) -> list[Chunk]:
        query_tokens = self._tokenize(query)
        n = len(self.docs)
        scores = []

        for idx, chunk in enumerate(self.docs):
            doc_tokens = self._tokenize(chunk.text)
            doc_len = self.doc_lens[idx]
            score = 0.0

            for qt in query_tokens:
                if qt not in self.vocab:
                    continue
                tf = doc_tokens.count(qt)
                df = self.doc_freqs.get(qt, 0)
                # IDF with smoothing
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
                # BM25 TF normalization
                tf_norm = (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_dl))
                score += idf * tf_norm

            scores.append(Chunk(chunk.chunk_id, chunk.text, chunk.doc_id, score))

        scores.sort(key=lambda c: c.score, reverse=True)
        return scores[:k]


def reciprocal_rank_fusion(
    *result_lists: list[Chunk],
    k: int = 60,
    top_n: int = 20,
) -> list[Chunk]:
    """Reciprocal Rank Fusion (RRF) -- Cormack, Clarke, Buettcher, SIGIR 2009.

    RRF(d) = SUM over all retrievers r of: 1 / (k + rank_r(d))

    Why RRF: BM25 scores are unbounded, cosine is [-1, 1]. They do NOT share
    a numeric space. RRF is rank-only and scale-free -- no normalization needed.

    Default k=60 used by Elasticsearch, OpenSearch, Weaviate, Qdrant, pgvector CTEs.
    Documents in BOTH lists naturally outrank single-list documents.

    Complexity: O(k * |R|) accumulate + O(n log n) sort. Dominated by ANN + BM25
    latency, not the fusion step.
    """
    fused_scores: dict[str, float] = defaultdict(float)
    chunk_map: dict[str, Chunk] = {}

    for result_list in result_lists:
        for rank, chunk in enumerate(result_list, start=1):
            # Core RRF formula
            fused_scores[chunk.chunk_id] += 1.0 / (k + rank)
            chunk_map[chunk.chunk_id] = chunk

    # Sort by fused score
    ranked = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    return [
        Chunk(cid, chunk_map[cid].text, chunk_map[cid].doc_id, score)
        for cid, score in ranked[:top_n]
    ]


def hybrid_search(query: str, dense: DenseRetriever, bm25: BM25Retriever,
                  k_each: int = 50, final_k: int = 20) -> list[Chunk]:
    """Hybrid search: run BM25 and dense in PARALLEL, then fuse with RRF.

    This is the Anthropic Contextual Retrieval production sketch:
    chunk -> TF-IDF + embeddings -> BM25 top + dense top -> rank fusion -> top-K.

    Both retrievers must apply the ACL filter on every arm -- not just one.
    """
    dense_results = dense.search(query, k=k_each)
    bm25_results = bm25.search(query, k=k_each)
    return reciprocal_rank_fusion(dense_results, bm25_results, top_n=final_k)


# --- Reranking with Cross-Encoder ---

def cross_encoder_rerank(query: str, chunks: list[Chunk], top_k: int = 5) -> list[Chunk]:
    """Stub: cross-encoder reranking for precision.

    Two-stage ranking is the single largest precision gain in the pipeline:
    - Recall@5 jumps from 0.695 to 0.816 (+17.4%)
    - MRR@3 from 0.433 to 0.605 (+39.7%)

    Bi-encoder: encode query once, docs offline, cosine/IP. O(1) + ANN.
    Cross-encoder: jointly attend (query, doc) -- one forward pass PER candidate.
    That's why we rerank 50-150 down to 5-20, not the full corpus.

    Production: Voyage rerank-2.5, Cohere Rerank 3.5, bge-reranker, Jina.
    Never send pre-rerank noise to the generator (lost-in-the-middle + hallucination).
    """
    # Stub: simulate reranking with a hash-based score
    reranked = []
    for chunk in chunks:
        # In production: model.predict([(query, chunk.text)])
        h = hashlib.sha256(f"{query}:{chunk.text}".encode()).hexdigest()
        score = int(h[:4], 16) / 65535.0
        reranked.append(Chunk(chunk.chunk_id, chunk.text, chunk.doc_id, score))

    reranked.sort(key=lambda c: c.score, reverse=True)
    return reranked[:top_k]


def production_retrieve_pipeline(
    query: str,
    dense: DenseRetriever,
    bm25: BM25Retriever,
    fuse_k: int = 50,
    rerank_n: int = 150,
    final_k: int = 8,
) -> list[Chunk]:
    """Full production retrieval pipeline.

    1. Dense + BM25 in parallel (both with ACL filter)
    2. RRF fusion (k=60, rank-only)
    3. Cross-encoder rerank 50-150 -> 5-20
    4. Top chunks to generator (U-shape placement)

    Default stack: Hybrid BM25+dense -> RRF (k=60) -> cross-encoder rerank
    50-150 to 5-20 -> citation-aware generation.
    """
    # Step 1+2: Hybrid + RRF
    fused = hybrid_search(query, dense, bm25, k_each=fuse_k, final_k=rerank_n)
    # Step 3: Rerank
    reranked = cross_encoder_rerank(query, fused, top_k=final_k)
    return reranked


# --- Agentic RAG Loop ---

class AgenticRAG:
    """Agentic RAG: the model decides WHETHER to retrieve, grades results,
    and rewrites the query if results are insufficient.

    State machine:
    generate_query_or_respond -> (tool call?) -> ToolNode retrieve
      -> grade_docs -> (all irrelevant?) -> rewrite_question -> loop back
                    -> (some relevant?) -> generate_answer -> END

    Critical: cap hops at 3 (official LangGraph tutorial has NO hop cap
    and will loop until runtime timeout). Wall-clock timeout too.
    """
    def __init__(self, retriever: DenseRetriever, max_hops: int = 3):
        self.retriever = retriever
        self.max_hops = max_hops

    def _needs_retrieval(self, question: str) -> bool:
        """Adaptive routing: classify whether retrieval is needed.

        Adaptive-RAG (Jeong et al., NAACL 2024):
        - chitchat -> no retrieve
        - factoid -> hybrid + rerank
        - multi-hop -> agent 2-3 hops
        - global -> LazyGraphRAG / community reports
        """
        # Stub: simple heuristic. Production: classifier or cheap LLM.
        greetings = {"hello", "hi", "hey", "thanks", "bye"}
        words = set(question.lower().split())
        if words & greetings and len(words) <= 3:
            return False
        return True

    def _grade_documents(self, question: str, chunks: list[Chunk]) -> list[Chunk]:
        """Grade retrieved documents for relevance.

        Use a cheap model for binary grade_documents, not a frontier model.
        Self-RAG reflection tokens: Retrieve {yes, no, continue},
        IsRel {relevant, irrelevant}, IsSup {fully, partial, none}.
        Production teams almost always PROMPT a separate grader rather than
        training special tokens.
        """
        # Stub: filter by score threshold
        relevant = [c for c in chunks if c.score > 0.3]
        return relevant

    def _rewrite_query(self, question: str, attempt: int) -> str:
        """Rewrite the query for a better retrieval attempt.

        HyDE (Gao et al.): LLM writes a hypothetical answer, embed that,
        retrieve neighbors. Two documented failures: mis-interprets queries
        without corpus context, biases open-ended queries. Use include_original=True.
        """
        # Stub: simple prefix addition
        return f"Detailed information about: {question}"

    def answer(self, question: str) -> dict:
        """Run the agentic RAG loop with hop cap.

        Returns the answer and metadata about the retrieval process.
        """
        if not self._needs_retrieval(question):
            return {
                "answer": f"[Chitchat response to: {question}]",
                "hops": 0,
                "retrieved_chunks": [],
                "route": "chitchat",
            }

        current_query = question
        all_chunks = []

        for hop in range(self.max_hops):
            chunks = self.retriever.search(current_query, k=10)
            relevant = self._grade_documents(question, chunks)
            all_chunks.extend(relevant)

            if relevant:
                # Sufficient evidence -- generate
                answer = generate_answer(question, relevant)
                return {
                    "answer": answer,
                    "hops": hop + 1,
                    "retrieved_chunks": relevant,
                    "route": "agentic",
                }

            # Insufficient -- rewrite and retry
            current_query = self._rewrite_query(question, hop)

        # Exhausted hops -- refuse rather than hallucinate
        # On ACL-sensitive corpora: NEVER fall back to parametric knowledge
        return {
            "answer": "Insufficient evidence after retrieval attempts. Cannot provide a grounded answer.",
            "hops": self.max_hops,
            "retrieved_chunks": all_chunks,
            "route": "insufficient_evidence",
        }


# --- RAG Evaluation Metrics ---

def faithfulness_score(answer: str, context_chunks: list[Chunk]) -> float:
    """RAGAS Faithfulness: is each claim in the answer supported by context?

    Walkthrough (Einstein example): answer has 2 claims, 1 is entailed -> 0.5.
    Decompose answer into atomic claims, check each via NLI, average.

    RAGAS WikiEval: faithfulness aligned with humans at 0.95 accuracy
    vs 0.72 for direct GPT scoring.

    Critical footgun: faithfulness can be 1.0 on the WRONG documents.
    Always pair with context_recall. Faithfulness is entailment vs RETRIEVED
    context, not world truth (that's SimpleQA).

    DeepEval trap: default FaithfulnessMetric treats "I don't know" as supported
    and empty verdicts as 1.0 unless you set penalize_ambiguous_claims=True.
    """
    if not answer or not context_chunks:
        return 0.0

    # Stub: check what fraction of answer sentences have keyword overlap with context
    # Production: use NLI model or LLM-as-judge for entailment checking
    context_text = " ".join(c.text.lower() for c in context_chunks)
    sentences = [s.strip() for s in answer.split(".") if s.strip()]
    if not sentences:
        return 0.0

    supported = 0
    for sent in sentences:
        words = set(sent.lower().split())
        context_words = set(context_text.split())
        overlap = len(words & context_words) / max(len(words), 1)
        if overlap > 0.3:  # Threshold for "supported"
            supported += 1

    return supported / len(sentences)


def context_recall(reference_answer: str, context_chunks: list[Chunk]) -> float:
    """Context Recall: what fraction of reference answer claims appear in context?

    Complement to faithfulness -- catches the "faithful to wrong docs" failure.
    If retriever pulls irrelevant chunks, faithfulness can still be 1.0
    while the answer is factually wrong. Context recall detects this.
    """
    if not reference_answer or not context_chunks:
        return 0.0

    context_text = " ".join(c.text.lower() for c in context_chunks)
    ref_sentences = [s.strip() for s in reference_answer.split(".") if s.strip()]
    if not ref_sentences:
        return 0.0

    recalled = 0
    for sent in ref_sentences:
        words = set(sent.lower().split())
        context_words = set(context_text.split())
        overlap = len(words & context_words) / max(len(words), 1)
        if overlap > 0.3:
            recalled += 1

    return recalled / len(ref_sentences)


def answer_relevancy(question: str, answer: str) -> float:
    """Answer Relevancy: is the answer relevant to the question? (cosine-based)

    RAGAS WikiEval agreement: ~78% -- too noisy for ship-gating.
    Use as a monitoring signal, not a deployment gate.
    """
    q_emb = embed_text(question)
    a_emb = embed_text(answer)
    return cosine_similarity(q_emb, a_emb)


def citation_validation(answer: str, retrieved_ids: set[str]) -> dict:
    """Citation validation: are cited IDs a subset of retrieved IDs?

    This is a SCHEMA/CONSTRAINT problem, not an LLM-judge problem.
    RAGAS will not catch a bare invented [doc 17].
    Check deterministically -- constrain decode to retrieved chunk_ids.

    ALCE (Gao et al., EMNLP 2023): even strong models lacked complete
    citation support ~50% of the time on ELI5.
    """
    # Extract citation IDs from answer (pattern: [chunk_id])
    cited = set(re.findall(r'\[([^\]]+)\]', answer))
    valid = cited & retrieved_ids
    invalid = cited - retrieved_ids
    missing = retrieved_ids - cited

    return {
        "cited": cited,
        "valid_citations": valid,
        "hallucinated_citations": invalid,  # These are invented IDs
        "uncited_sources": missing,
        "citation_precision": len(valid) / max(len(cited), 1),
        "all_valid": len(invalid) == 0,
    }


def evaluate_rag_response(
    question: str,
    answer: str,
    context_chunks: list[Chunk],
    reference_answer: str,
) -> dict:
    """Run all RAG evaluation metrics on a single response.

    Production eval should run these as layered scoring:
    1. Hard oracle: citation validation (deterministic, milliseconds)
    2. Soft oracle: faithfulness + context recall (NLI/LLM, seconds)
    3. Monitoring: answer relevancy (too noisy for gating)
    """
    retrieved_ids = {c.chunk_id for c in context_chunks}

    return {
        "faithfulness": round(faithfulness_score(answer, context_chunks), 3),
        "context_recall": round(context_recall(reference_answer, context_chunks), 3),
        "answer_relevancy": round(answer_relevancy(question, answer), 3),
        "citation_check": citation_validation(answer, retrieved_ids),
    }


# --- Cost Calculator ---

def rag_cost_per_1k_queries(
    query_tokens: int = 50,
    rerank_chunks: int = 80,
    chunk_tokens: int = 500,
    context_chunks: int = 8,
    output_tokens: int = 400,
    embed_price_per_m: float = 0.02,        # OpenAI 3-small
    rerank_price_per_m: float = 0.05,        # Voyage rerank-2.5
    gen_input_price_per_m: float = 0.20,     # gpt-5.6-luna
    gen_output_price_per_m: float = 1.20,    # gpt-5.6-luna
) -> dict:
    """Calculate RAG cost per 1,000 queries.

    Key insight: rerank dominates on cheap generators (luna + Voyage).
    On expensive generators (terra/Sonnet 5), generation dominates.
    Budget rerank + SUM(LLM loops), not embedding pennies.
    """
    # Embed cost: 1k queries x query_tokens
    embed_cost = 1000 * query_tokens * embed_price_per_m / 1_000_000

    # Rerank cost: (query x chunks + chunks x chunk_tokens) per query
    rerank_tokens_per_query = query_tokens * rerank_chunks + rerank_chunks * chunk_tokens
    rerank_cost = 1000 * rerank_tokens_per_query * rerank_price_per_m / 1_000_000

    # Generate cost: context_chunks * chunk_tokens input + output_tokens
    gen_input = context_chunks * chunk_tokens
    gen_input_cost = 1000 * gen_input * gen_input_price_per_m / 1_000_000
    gen_output_cost = 1000 * output_tokens * gen_output_price_per_m / 1_000_000

    total = embed_cost + rerank_cost + gen_input_cost + gen_output_cost

    return {
        "embed_cost": round(embed_cost, 3),
        "rerank_cost": round(rerank_cost, 3),
        "gen_input_cost": round(gen_input_cost, 3),
        "gen_output_cost": round(gen_output_cost, 3),
        "total_per_1k": round(total, 3),
        "dominant_cost": max(
            [("embed", embed_cost), ("rerank", rerank_cost),
             ("gen_input", gen_input_cost), ("gen_output", gen_output_cost)],
            key=lambda x: x[1],
        )[0],
    }


# --- Demo ---

if __name__ == "__main__":
    print("=" * 60)
    print("RAG Interview Prep -- Runnable Demos")
    print("=" * 60)

    # 1. Build a small corpus
    docs = [
        Chunk("c1", "The TS-999 error code indicates a timeout in the payment gateway.", "tenant_a_doc1"),
        Chunk("c2", "Returns must be initiated within 30 days of purchase.", "tenant_a_doc1"),
        Chunk("c3", "Refunds are processed within 5-7 business days to the original payment method.", "tenant_a_doc1"),
        Chunk("c4", "The warranty covers manufacturing defects for 12 months.", "tenant_a_doc2"),
        Chunk("c5", "Contact support at support@example.com for urgent issues.", "tenant_a_doc2"),
    ]

    # 2. Basic RAG pipeline
    print("\n--- Basic RAG Pipeline ---")
    dense = DenseRetriever()
    for doc in docs:
        dense.add(doc)
    answer = basic_rag_pipeline("What is the TS-999 error?", dense, k=3)
    print(f"Answer: {answer[:150]}...")

    # 3. Chunking strategies
    print("\n--- Chunking Strategies ---")
    sample_text = (
        "Machine learning models require careful evaluation. "
        "The training data must be representative of production traffic.\n\n"
        "Fine-tuning adapts a pretrained model to a specific task. "
        "LoRA reduces memory requirements by training low-rank matrices.\n\n"
        "RAG augments generation with retrieved context. "
        "This reduces hallucination and grounds answers in evidence."
    )
    fixed_chunks = chunk_fixed_size(sample_text, chunk_size=100, overlap=20)
    recursive_chunks = chunk_recursive(sample_text, max_size=100)
    print(f"Fixed-size chunks:   {len(fixed_chunks)}")
    print(f"Recursive chunks:    {len(recursive_chunks)}")

    # 4. Hybrid search with RRF
    print("\n--- Hybrid Search + RRF Fusion ---")
    bm25 = BM25Retriever()
    for doc in docs:
        bm25.add(doc)

    dense_results = dense.search("TS-999 payment error", k=3)
    bm25_results = bm25.search("TS-999 payment error", k=3)
    fused = reciprocal_rank_fusion(dense_results, bm25_results, top_n=3)

    print(f"Dense top-1:  {dense_results[0].chunk_id} (score={dense_results[0].score:.4f})")
    print(f"BM25  top-1:  {bm25_results[0].chunk_id} (score={bm25_results[0].score:.4f})")
    print(f"RRF   top-1:  {fused[0].chunk_id} (score={fused[0].score:.4f})")

    # 5. Agentic RAG
    print("\n--- Agentic RAG Loop ---")
    agent = AgenticRAG(dense, max_hops=3)

    # Chitchat -- no retrieval
    result = agent.answer("Hello!")
    print(f"Chitchat route: {result['route']}, hops: {result['hops']}")

    # Factoid -- retrieval
    result = agent.answer("How long do refunds take?")
    print(f"Factoid route: {result['route']}, hops: {result['hops']}, "
          f"chunks: {len(result['retrieved_chunks'])}")

    # 6. Evaluation metrics
    print("\n--- RAG Evaluation Metrics ---")
    test_answer = "Refunds take 5-7 business days [c3]. Returns must be within 30 days [c2]."
    test_chunks = [docs[1], docs[2]]  # c2 and c3
    reference = "Refunds are processed in 5-7 business days."
    metrics = evaluate_rag_response(
        "How long do refunds take?", test_answer, test_chunks, reference
    )
    print(f"Faithfulness:      {metrics['faithfulness']}")
    print(f"Context Recall:    {metrics['context_recall']}")
    print(f"Answer Relevancy:  {metrics['answer_relevancy']}")
    print(f"Citations Valid:   {metrics['citation_check']['all_valid']}")

    # 7. Cost calculator
    print("\n--- Cost per 1k Queries ---")
    luna_cost = rag_cost_per_1k_queries()
    print(f"Luna + Voyage rerank: ${luna_cost['total_per_1k']:.2f}/1k "
          f"(dominant: {luna_cost['dominant_cost']})")

    sonnet_cost = rag_cost_per_1k_queries(
        gen_input_price_per_m=2.00, gen_output_price_per_m=10.00
    )
    print(f"Sonnet 5:            ${sonnet_cost['total_per_1k']:.2f}/1k "
          f"(dominant: {sonnet_cost['dominant_cost']})")
