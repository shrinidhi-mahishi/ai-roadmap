# Module 04: AI System Design

## What Is This?

Traditional system design interviews ask "Design YouTube" or "Design Uber" -- the focus
is data models, API design, scaling, and consistency. AI system design interviews ask
"Design ChatGPT" or "Design GitHub Copilot" -- the focus shifts to **model selection,
RAG pipelines, guardrails, evaluation, cost/latency tradeoffs, safety, and feedback
loops**.

An analogy: traditional system design is like designing a factory -- you control every
machine, every input, every output. AI system design is like designing a factory where
one of the machines is a very talented but unreliable employee who sometimes makes
things up, occasionally ignores instructions, and whose output quality changes without
warning. Your architecture must account for that unreliability at every layer.

Every AI system design maps to combinations of five primitives: **RAG** (ground the LLM
in external knowledge), **Model Routing** (direct requests by cost and capability),
**Guardrails** (input/output validation and safety), **Evaluation** (automated quality
measurement and feedback), and **Agentic Loops** (plan-act-observe cycles with tools).

## Why It Matters

- AI Engineer was ranked the #1 fastest-growing job in the US for the second year
  running, with postings up 143% YoY in 2025.
- Director/VP AI interviews increasingly center on system design, not algorithms.
  The question is no longer "explain backpropagation" -- it is "how would you build a
  retrieval-augmented chatbot for enterprise search?"
- The core skill being tested: **navigating tradeoffs among latency, cost, quality,
  and safety** when these pressures pull in opposite directions.
- A junior engineer focuses on the prompt. A mid-level engineer describes embeddings.
  A senior engineer describes an evolving ecosystem -- how documents are chunked,
  how retrieval affects context windows, how outputs are validated, and how user
  feedback improves the system over time.

---

## Part 1: System Topology & Data Flow

### Universal AI System Architecture

Every AI system design question -- chatbot, search, copilot, moderation, document
processing -- shares a common skeleton. The specifics change; the topology does not.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           INGESTION PLANE                                      │
│                                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Documents    │  │ APIs /       │  │ User-Gen     │  │ Codebase /       │  │
│  │ (PDF, HTML,  │  │ Databases    │  │ Content      │  │ Repository       │  │
│  │  slides)     │  │              │  │              │  │                  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         └──────────────────┼──────────────────┼──────────────────┘             │
│                            v                  │                                │
│                   ┌────────────────┐           │                               │
│                   │ Parse, Chunk,  │           │                               │
│                   │ Embed, Index   │           │                               │
│                   └────────┬───────┘           │                               │
│                            v                  │                                │
│                   ┌────────────────┐           │                               │
│                   │ Vector Store + │           │                               │
│                   │ BM25 Index     │           │                               │
│                   └────────────────┘           │                               │
└────────────────────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────┼───────────────────────────────────────────────────┐
│                       REQUEST PLANE                                            │
│                            │                                                   │
│  User Request ────────────>│                                                   │
│         │                  │                                                   │
│         v                  │                                                   │
│  ┌──────────────┐          │                                                   │
│  │ Input        │          │                                                   │
│  │ Guardrails   │          │                                                   │
│  │ (PII, toxicity,         │                                                   │
│  │  injection)  │          │                                                   │
│  └──────┬───────┘          │                                                   │
│         v                  │                                                   │
│  ┌──────────────┐   ┌──────┴──────┐                                           │
│  │ Query        │──>│ Hybrid      │                                           │
│  │ Processing   │   │ Retrieval   │                                           │
│  │ (rewrite,    │   │ (BM25 +     │                                           │
│  │  decompose)  │   │  dense)     │                                           │
│  └──────────────┘   └──────┬──────┘                                           │
│                            v                                                   │
│                   ┌────────────────┐                                           │
│                   │ Reranker       │                                           │
│                   │ (cross-encoder)│                                           │
│                   └────────┬───────┘                                           │
│                            v                                                   │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                      │
│  │ Semantic     │   │ Model Router │   │ Conversation │                      │
│  │ Cache        │──>│ (cheap/mid/  │<──│ Memory       │                      │
│  │ (cos > 0.95) │   │  frontier)   │   │ (Redis)      │                      │
│  └──────────────┘   └──────┬───────┘   └──────────────┘                      │
│                            v                                                   │
│                   ┌────────────────┐                                           │
│                   │ Output         │                                           │
│                   │ Guardrails     │                                           │
│                   │ (groundedness, │                                           │
│                   │  safety,       │                                           │
│                   │  citations)    │                                           │
│                   └────────┬───────┘                                           │
│                            v                                                   │
│                     Response to User                                           │
└────────────────────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────┼───────────────────────────────────────────────────┐
│                       TELEMETRY PLANE                                          │
│                            │                                                   │
│  ┌──────────────┐  ┌──────┴───────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Traces       │  │ Eval Sampler │  │ User         │  │ Cost / Token     │  │
│  │ (Langfuse)   │  │ (5-10% live) │  │ Feedback     │  │ Attribution      │  │
│  └──────────────┘  └──────────────┘  │ (thumbs,     │  └──────────────────┘  │
│                                       │  regen)      │                        │
│                                       └──────────────┘                        │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │ Feedback Loop: corrections --> retrieval tuning, prompt updates,         │  │
│  │ cache refresh, fine-tuning dataset, routing threshold adjustment         │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────┘
```

### The 6-Step Interview Framework

1. **Clarify requirements** -- functional, non-functional, AI-specific constraints
   (latency budget, cost per query, accuracy target)
2. **Define the AI component** -- model selection, prompt design, RAG vs fine-tuning
3. **Design the architecture** -- data flow, caching, async processing, components
4. **Address evaluation** -- offline evals, online monitoring, human feedback
5. **Handle failure modes** -- hallucination, latency spikes, cost overruns, prompt
   injection, data leakage
6. **Discuss scaling** -- model routing, caching, batch processing, horizontal scaling

### Request Flow Narrative (Customer Support Chatbot)

1. User message enters **Input Guardrails**: toxicity scan, PII redaction, prompt
   injection detection. Blocked messages return a polite refusal.
2. **Intent Classifier** (lightweight model) routes to the appropriate pipeline:
   billing -> billing RAG, order status -> structured API call (no LLM), technical
   support -> technical RAG, out-of-scope -> polite refusal + escalation offer.
3. **Query Processing** rewrites ambiguous queries and decomposes multi-hop questions.
4. **Semantic Cache** checks for a similar recent query (cosine > 0.95). On hit,
   return the cached response -- bypassing retrieval and generation entirely.
5. On miss, **Hybrid Retrieval** runs BM25 (keyword precision) and dense vector search
   (semantic understanding) in parallel, merges via Reciprocal Rank Fusion.
6. **Cross-Encoder Reranker** re-scores top-K to top-5 for precision.
7. **Model Router** selects GPT-4o mini for simple factual queries, GPT-4o for complex
   multi-part questions.
8. **Output Guardrails** check groundedness (is the response supported by retrieved
   context?) and run a safety filter. If groundedness fails, fall back to escalation.
9. Response returned with source citations. Async: trace logged, quality scored on
   5-10% sample, cache updated.

---

## Part 2: Core Mechanics & Algorithms

### RAG Pipeline: The 2026 Default

Six-layer pipeline that grounds LLM outputs in external knowledge:

| Layer | Function | Key Decision |
|---|---|---|
| **1. Ingestion** | Parse PDFs, HTML, slides, tables; normalize; deduplicate | Format-specific parsers vs multimodal |
| **2. Indexing** | BM25 (lexical) + dense embeddings (semantic) | Chunk size: 512-1024 tokens, 50-100 overlap |
| **3. Query Processing** | Classify intent, rewrite if ambiguous, decompose if multi-hop | Rewrite adds latency but improves retrieval |
| **4. Retrieval** | Hybrid search with RRF or weighted scoring | Hybrid outperforms pure semantic by 15-30% |
| **5. Reranking** | Cross-encoder on top-K results | Cohere Rerank, BGE-Reranker |
| **6. Generation + Eval** | LLM consumes context; eval scores groundedness | Guardrail blocks unsafe outputs |

**Chunking trade-off**: Semantic chunking (respects section boundaries) preserves context
better but costs more. Fixed-size chunking (512 tokens, 50 overlap) is cheap and
predictable. Start fixed-size; switch to semantic when retrieval quality is the bottleneck.

### Hybrid Search: Why It Wins

Every serious 2026 search system runs semantic alongside BM25, fuses with RRF, and
reranks the top 100 with a cross-encoder.

Why pure semantic fails: "cheap flights to Paris" returns nothing because the database
says "affordable airfare to Paris, France." Why pure keyword fails: it misses synonyms
and conceptual matches. Hybrid gives both precision and understanding.

### Multi-Turn Conversation Memory

- **Short-term (within session)**: Redis or in-memory. Sliding window or summarization
  to prevent token explosion across turns.
- **Long-term (across sessions)**: Persistent store capturing preferences, previous
  issues, resolution attempts. Enables continuity.
- **Design principle**: Context loss in production is a frequent failure mode. Both
  stores must be designed intentionally.

### Content Moderation: Two-Tier Architecture

The key cost decision in moderation is **smart routing**:

```
Every message ──> Fast Primary Classifier (lightweight, < 50ms)
                    │
              CLEAR (95%)      VIOLATION (high conf)     UNCERTAIN (5-15%)
                │                    │                        │
           Publish immediately   Block + log            Secondary LLM Analysis
                                 + appeal path               │
                                                     CLEAR / VIOLATION / UNCERTAIN
                                                                  │
                                                          Human Review Queue
```

Pre-moderation (synchronous, 100-300ms) is standard for regulated industries.
Post-moderation (asynchronous) is standard for high-volume social feeds.

**False positive management**: False positives erode user trust faster than false
negatives erode safety. Strategies: confidence-tiered enforcement, user appeals with
fast SLA, per-category calibration (toxicity threshold differs from copyright threshold).

### Document Processing: OCR + LLM Extraction

Modern document AI combines OCR with layout understanding and LLM extraction:
1. **Preprocess**: Deskew, denoise, normalize resolution
2. **Classify**: Route to correct extraction pipeline
3. **OCR**: Google Document AI, AWS Textract, or open-source (Tesseract, PaddleOCR)
4. **Layout analysis**: Headers, paragraphs, tables, key-value pairs
5. **LLM extraction**: Structured output via Pydantic schema
6. **Validation**: Schema + business rules + cross-field consistency
7. **Human review**: Low-confidence or validation-failing fields

**Dual extraction** (Microsoft approach): Run two extractors simultaneously. Matching
fields pass automatically. Differing fields routed to human review with both values
displayed side by side.

### AI Coding Copilot: Context as Architecture

The difference between a tool that helps and one that transforms is almost entirely
about what context the agent can access. "Context-blindness is not a model quality
problem. It is an architecture problem."

**Two approaches**: RAG-like retrieval (Cursor, Augment Code) builds a searchable index
and retrieves relevant chunks. Large context window (Gemini CLI) holds large portions
of the repository in memory (1M+ tokens).

**Fill-in-the-Middle (FIM)**: Model sees code before and after the cursor. GitHub A/B
tests found FIM lifted accepted completions by ~10%.

**The Agent Loop (2026)**: Plan -> Code -> Test -> Observe -> Iterate -> Deliver diff/PR.
Agents interact with real development tools (terminal, file system, test runners,
linters, git) rather than just generating text. This is a fundamental shift from
"suggest" to "do."

### Seven Cross-Cutting Patterns

| Pattern | What It Does | Used In |
|---|---|---|
| **RAG** | Ground LLM in external knowledge | Chatbot, search, copilot |
| **Tool-Augmented Generation** | LLM calls APIs, DBs, calculators | Copilot, agentic systems |
| **Human-in-the-Loop** | Route uncertain cases to humans, capture corrections | Moderation, doc processing, chatbot |
| **Streaming** | Token-by-token output, reduces perceived latency | Chat, copilot |
| **Multi-Model Routing** | Cheapest model that can handle each request | All high-volume systems |
| **Caching** | Semantic, KV, exact-match; bypass LLM on hit | All systems |
| **Eval & Feedback** | Offline eval, CI gate, production sampling, user signals | All systems |

---

## Part 3: Token Economics & NFR Analysis

### Cost Per 1K Runs by System Type

| System | Cost Target | Formula (per 1K runs) | Key Levers |
|---|---|---|---|
| **Support chatbot** | < $0.05/conv | 1K x (avg 4 turns x 500+800 tokens x model rate) | Model routing, cache hit rate |
| **Semantic search** | < $0.005/query | 1K x (embed query + reranker call) | No LLM generation needed for search |
| **Coding copilot** | < $0.10/session | 1K x (context tokens + generation) | Context size, model tier |
| **Content moderation** | < $0.001/item | 1K x 0.05 (5% escalated to LLM) x model rate | Two-tier routing is the lever |
| **Document processing** | < $0.50/doc | 1K x (OCR + LLM extraction + validation) | OCR choice, page count |

**Detailed chatbot cost model** (500K conversations/day):

| Approach | Monthly Cost | Calculation |
|---|---|---|
| All GPT-4o | $585,000 | 500K/day x 30 x 4 turns x 1300 avg tokens x $10/1M |
| 80/20 routing | $136,500 | 80% at GPT-4o-mini ($0.60/1M) + 20% at GPT-4o |
| + 25% semantic cache | $102,375 | Cache eliminates 25% of LLM calls |
| + 20% API-only (no LLM) | $82,000 | Order status, account balance bypass LLM |

### Latency Budgets

| System | p50 | p95 | p99 | Bottleneck |
|---|---|---|---|---|
| Inline code completion | < 80ms | < 200ms | < 500ms | FIM model inference |
| Chat TTFT | < 400ms | < 1s | < 2s | Retrieval + model startup |
| Search results | < 200ms | < 500ms | < 1s | Vector search + reranking |
| Pre-moderation | < 100ms | < 300ms | < 500ms | Classifier inference |
| Document processing | < 10s/pg | < 30s/pg | < 60s/pg | OCR + LLM extraction |

### Throughput & Capacity Planning

| Scale | Architecture Pattern |
|---|---|
| 100 QPS | Single vLLM instance, single vector DB replica, Redis cache |
| 1K QPS | Load-balanced vLLM cluster (4-8 GPUs), vector DB 3 replicas, semantic cache |
| 10K QPS | Multi-model routing (80% fast), aggressive caching (30%+ hit), queue autoscaling |
| 100K QPS | Regional deployment, tiered (cache -> keyword -> vector -> LLM), custom embeddings |

- vLLM: 85-92% GPU utilization, 100-150 concurrent requests per GPU
- Dynamic batching: 5-15ms window, GPU util from 40% to 75-85%
- Streaming: budget 25-30GB VRAM per GPU for active streams on 7B models

### Availability & RPO/RTO

| Component | Availability | RPO | RTO | Recovery Strategy |
|---|---|---|---|---|
| LLM serving | 99.95% | N/A (stateless) | < 30s | Multi-provider failover |
| Vector store | 99.9% | < 1 min | < 15 min | Rebuild from source documents |
| Conversation state | 99.9% | 0 (Redis AOF) | < 5 min | Redis failover |
| Eval datasets | 99.99% | 0 (Git-backed) | < 5 min | git clone |
| Document queue | 99.9% | 0 (Postgres) | < 5 min | SKIP LOCKED, crash-safe |

Vector indexes are derived data. They can be rebuilt from source documents, making RPO
less critical for vector stores than for primary data.

---

## Part 4: Distributed Resilience & Security

### Production Failure Modes

| # | Failure Mode | Detection | Mitigation |
|---|---|---|---|
| 1 | **Hallucination** | Groundedness scoring, citation verification | Hybrid retrieval, reranking, "I don't know" training |
| 2 | **Context overflow** | Token counting before LLM call | Summarize history, limit chunks, prioritize relevance |
| 3 | **Retrieval failure** | Low scores, empty results, user regeneration | Hybrid search (BM25 catches what embeddings miss) |
| 4 | **Latency spikes** | p95/p99 monitoring, queue depth alerts | Dynamic batching, model downgrade, caching, autoscaling |
| 5 | **Cost explosion** | Per-request tracking, daily anomaly detection | Token budgets, routing, kill switch for agent loops |
| 6 | **Silent quality regression** | Production eval sampling, user feedback | SLO burn-rate alerts, A/B before rollout |
| 7 | **Adversarial attacks** | Injection classifiers, anomalous patterns | Sanitization, system prompt hardening, defense in depth |

### Escalation Design

Triggers: out-of-scope question, explicit human request, frustrated language detected,
billing dispute keywords, low confidence score.

When escalating: pass full conversation transcript + retrieved source documents to the
human agent's interface. Agent arrives with full context and suggested responses.
Resolution time drops because the agent does not re-research.

**Design principle**: A system that escalates a beat too early (with full context) reads
as competent. A system that escalates a beat too late (after wrong answers) reads as
broken regardless of model quality.

### Conversation State Management

- Session stickiness for multi-turn conversations -- cannot reroute mid-conversation to
  a different model version
- Graceful degradation: if primary model is unavailable, fall back to secondary with
  disclosure to user
- State stored externally (Redis, DynamoDB), never in model server memory
- For canary/blue-green: drain connections or wait for active conversations to complete

### Multi-Provider Failover

If OpenAI is down, route to Anthropic or self-hosted. LLM gateways (Portkey, LiteLLM)
provide this automatically. Circuit breakers trip on sustained error rate.

### Prompt Injection Defense (Defense in Depth)

- **Input sanitization**: Detect and strip adversarial instructions
- **System prompt hardening**: Separate system prompt from user input at the API level
- **Output validation**: Check for instruction leakage, unexpected tool calls
- No single layer is sufficient. All three are required.

### PII Handling

- Detect and redact PII before it reaches the LLM (input guardrails)
- Post-generation PII scan before response reaches user (output guardrails)
- Audit logs record data category, not the PII itself
- Data residency: route requests to region-appropriate infrastructure

### Multi-Tenant Isolation

- Namespace/collection-level isolation in vector databases
- Separate inference endpoints or strict request-level isolation
- Per-tenant audit trail: who accessed what data, which model, what response

### Regulatory Compliance

- **EU AI Act** (Aug 2026): High-risk system obligations for customer-facing AI
- **SOC 2 Type II**: Required for enterprise SaaS with AI features
- **HIPAA**: Healthcare document processing, patient-facing chatbots
- **GDPR/CCPA**: PII detection, right to deletion (vector stores and conversation logs)

### Zero-Trust AI System Architecture

Microsoft's Zero Trust for AI reference (RSAC 2026) establishes the framework for
applying zero-trust principles to AI systems. Traditional perimeter security assumes
internal components are trusted; zero-trust assumes breach and verifies every interaction.

**Core Principles**:

1. **Verify every request (user identity + agent identity)**: Every API call between
   components carries both the end-user identity and the calling component's identity.
   An LLM gateway request includes who the user is AND which service is making the call.
2. **Least-privilege access for each component**: The LLM gateway can call model
   endpoints but cannot access the raw database. The retriever can query vector stores
   but cannot call external APIs. The guardrail service can read model outputs but
   cannot modify them. Each component gets the minimum permissions required for its
   function.
3. **Encrypt data at rest and in transit**: All inter-component communication uses
   mTLS. Vector store data, conversation logs, and eval datasets are encrypted at rest.
   Embedding vectors are treated as sensitive data (they can be inverted to reconstruct
   source text).
4. **Assume breach (instrument for detection, not just prevention)**: Every component
   emits structured security telemetry. Anomaly detection on tool call patterns, unusual
   query volumes, and unexpected data access patterns. Incident response playbooks for
   each component compromise scenario.
5. **Network microsegmentation**: Guardrails are isolated from retrieval (separate
   network segments). Retrieval is isolated from generation. The eval/telemetry plane
   has read-only access to production data. No component can reach another component's
   backing store directly -- all access goes through defined service interfaces.

### RBAC for AI Systems

Role-Based Access Control maps organizational roles to specific AI system permissions.
Without formal RBAC, access control becomes ad hoc and unauditable -- a SOC 2 finding
waiting to happen.

| Role | Permissions |
|------|------------|
| End User | Submit queries, view responses, provide feedback |
| Support Agent | View conversation history, escalation queue, override guardrails with justification |
| ML Engineer | Modify prompts, update RAG index, configure model routing, view eval dashboards |
| Platform Admin | Manage infrastructure, configure rate limits, modify RBAC policies, deploy models |
| Auditor | Read-only access to all traces, conversations, guardrail decisions, cost reports |

**Enforcement layers**:
- **API gateway (user auth)**: Validates end-user identity and role via JWT claims.
  Routes requests to appropriate pipelines based on role permissions.
- **Internal service mesh (mTLS between components)**: Each service has a cryptographic
  identity. Service-to-service calls are authorized based on component role, not just
  network position. Prevents lateral movement if one component is compromised.
- **LangSmith/Datadog RBAC for observability**: ML Engineers see eval dashboards and
  prompt performance. Auditors see full traces and cost reports. End Users see nothing.
  Platform Admins see infrastructure metrics. Role boundaries in observability tools
  mirror production RBAC -- no backdoor visibility.

---

## Part 5: Production Enterprise Code

### AI System Design Building Blocks

```python
"""
AI System Design: production building blocks for RAG chatbot, semantic search,
content moderation, and document processing pipelines.
Requires: openai, pydantic, redis, numpy
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import redis
import openai


# ── RAG Pipeline ─────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """A document chunk with text, embedding, and provenance."""
    chunk_id: str
    text: str
    source_doc: str
    page_number: int
    embedding: list[float] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class HybridRetriever:
    """BM25 + dense vector retrieval with RRF fusion and cross-encoder reranking."""

    def __init__(self, client: openai.OpenAI, embed_model: str = "text-embedding-3-small"):
        self.client = client
        self.embed_model = embed_model
        self.chunks: list[Chunk] = []
        self._bm25_index: dict[str, list[int]] = {}  # term -> chunk indices

    def index_chunks(self, chunks: list[Chunk]) -> int:
        """Index chunks for both BM25 and dense retrieval."""
        self.chunks = chunks
        for i, chunk in enumerate(chunks):
            # Embed for dense retrieval
            resp = self.client.embeddings.create(
                model=self.embed_model, input=chunk.text
            )
            chunk.embedding = resp.data[0].embedding

            # Build BM25 inverted index
            terms = chunk.text.lower().split()
            for term in set(terms):
                if term not in self._bm25_index:
                    self._bm25_index[term] = []
                self._bm25_index[term].append(i)

        return len(chunks)

    def _bm25_search(self, query: str, top_k: int = 50) -> list[tuple[int, float]]:
        """Simple BM25 scoring. Production: use Elasticsearch or Tantivy."""
        terms = query.lower().split()
        scores: dict[int, float] = {}
        n = len(self.chunks)
        for term in terms:
            matching = self._bm25_index.get(term, [])
            idf = np.log((n - len(matching) + 0.5) / (len(matching) + 0.5) + 1)
            for idx in matching:
                doc_len = len(self.chunks[idx].text.split())
                tf = self.chunks[idx].text.lower().split().count(term)
                k1, b, avg_dl = 1.5, 0.75, 200
                score = idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_dl))
                scores[idx] = scores.get(idx, 0) + score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]

    def _dense_search(self, query: str, top_k: int = 50) -> list[tuple[int, float]]:
        """Dense vector search via cosine similarity."""
        resp = self.client.embeddings.create(model=self.embed_model, input=query)
        query_vec = np.array(resp.data[0].embedding)
        scores = []
        for i, chunk in enumerate(self.chunks):
            chunk_vec = np.array(chunk.embedding)
            sim = float(np.dot(query_vec, chunk_vec) /
                        (np.linalg.norm(query_vec) * np.linalg.norm(chunk_vec)))
            scores.append((i, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _reciprocal_rank_fusion(self, *result_lists: list[tuple[int, float]],
                                 k: int = 60) -> list[tuple[int, float]]:
        """Merge multiple ranked lists using RRF. k=60 is the standard constant."""
        rrf_scores: dict[int, float] = {}
        for results in result_lists:
            for rank, (idx, _) in enumerate(results):
                rrf_scores[idx] = rrf_scores.get(idx, 0) + 1.0 / (k + rank + 1)
        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return ranked

    def search(self, query: str, top_k: int = 5) -> list[Chunk]:
        """Hybrid search: BM25 + dense, fused with RRF."""
        bm25_results = self._bm25_search(query, top_k=50)
        dense_results = self._dense_search(query, top_k=50)
        fused = self._reciprocal_rank_fusion(bm25_results, dense_results)
        return [self.chunks[idx] for idx, _ in fused[:top_k]]


# ── Intent Classifier & Model Router ────────────────────────────────────────

class Intent(Enum):
    BILLING = "billing"
    ORDER_STATUS = "order_status"
    TECHNICAL = "technical"
    OUT_OF_SCOPE = "out_of_scope"


class ModelTier(Enum):
    FAST = "fast"        # GPT-4o mini, < 200ms TTFT, $0.15/1M in
    STANDARD = "standard"  # GPT-4o, < 1s TTFT, $2.50/1M in
    FRONTIER = "frontier"  # Claude Sonnet, < 2s TTFT, $3/1M in


class IntentRouter:
    """Classify intent and route to appropriate model tier and pipeline."""

    INTENT_KEYWORDS = {
        Intent.BILLING: ["invoice", "charge", "refund", "billing", "payment", "subscription"],
        Intent.ORDER_STATUS: ["order", "tracking", "shipping", "delivery", "status"],
        Intent.TECHNICAL: ["error", "bug", "install", "configure", "setup", "api", "integration"],
    }

    MODEL_MAP = {
        ModelTier.FAST: {"model": "gpt-4o-mini", "cost_per_1m_in": 0.15, "cost_per_1m_out": 0.60},
        ModelTier.STANDARD: {"model": "gpt-4o", "cost_per_1m_in": 2.50, "cost_per_1m_out": 10.0},
        ModelTier.FRONTIER: {"model": "claude-sonnet-4-20250514", "cost_per_1m_in": 3.0, "cost_per_1m_out": 15.0},
    }

    def classify_intent(self, query: str) -> Intent:
        """Keyword-based intent classification. Production: use a trained classifier."""
        query_lower = query.lower()
        for intent, keywords in self.INTENT_KEYWORDS.items():
            if any(kw in query_lower for kw in keywords):
                return intent
        return Intent.OUT_OF_SCOPE

    def select_model(self, query: str, intent: Intent) -> dict:
        """Select model tier based on query complexity and intent."""
        # Order status bypasses LLM entirely
        if intent == Intent.ORDER_STATUS:
            return {"pipeline": "api_lookup", "model": None, "cost": 0.0}

        # Out of scope gets the cheapest response
        if intent == Intent.OUT_OF_SCOPE:
            config = self.MODEL_MAP[ModelTier.FAST]
            return {"pipeline": "refusal", "model": config["model"], "cost": config["cost_per_1m_in"]}

        # Complex queries (long, multi-hop markers) get standard/frontier
        complexity_markers = ["compare", "explain why", "step by step", "trade-off",
                              "differences between", "analyze"]
        is_complex = any(m in query.lower() for m in complexity_markers) or len(query) > 300

        tier = ModelTier.STANDARD if is_complex else ModelTier.FAST
        config = self.MODEL_MAP[tier]
        return {
            "pipeline": "rag",
            "model": config["model"],
            "tier": tier.value,
            "cost_per_1m_in": config["cost_per_1m_in"],
        }


# ── Content Moderation Pipeline ──────────────────────────────────────────────

class Severity(Enum):
    CLEAR = "clear"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ModerationResult:
    content_id: str
    severity: Severity
    categories: list[str]
    confidence: float
    action: str  # "publish", "block", "review"
    model_used: str
    latency_ms: float
    audit_trail: dict = field(default_factory=dict)


class TwoTierModerator:
    """Fast primary filter + LLM secondary analysis for uncertain cases."""

    CATEGORY_THRESHOLDS = {
        "toxicity": 0.7,
        "sexual": 0.8,
        "violence": 0.85,
        "self_harm": 0.6,
        "hate_speech": 0.7,
    }

    def __init__(self, client: openai.OpenAI):
        self.client = client

    def primary_filter(self, content: str) -> tuple[Severity, float, list[str]]:
        """Fast keyword + pattern filter. Production: use a trained classifier."""
        content_lower = content.lower()
        toxic_patterns = ["kill", "hate", "threat", "attack", "bomb"]
        matches = [p for p in toxic_patterns if p in content_lower]

        if not matches:
            return Severity.CLEAR, 0.95, []

        confidence = min(0.5 + len(matches) * 0.15, 0.99)
        if confidence > 0.9:
            return Severity.HIGH, confidence, matches
        return Severity.LOW, confidence, matches

    def secondary_analysis(self, content: str, content_id: str) -> ModerationResult:
        """LLM-based analysis for uncertain cases. Only called on 5-15% of traffic."""
        start = time.time()
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": (
                    "You are a content moderation classifier. Analyze the content and return JSON with:\n"
                    "- severity: clear, low, medium, high, critical\n"
                    "- categories: list of violated categories (toxicity, sexual, violence, self_harm, hate_speech)\n"
                    "- confidence: 0.0-1.0\n"
                    "- reasoning: brief explanation\n"
                    "Return only valid JSON."
                ),
            }, {
                "role": "user",
                "content": f"Analyze this content for policy violations:\n\n{content}",
            }],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        latency = (time.time() - start) * 1000

        severity = Severity(result.get("severity", "clear"))
        action = "publish" if severity == Severity.CLEAR else (
            "block" if severity in (Severity.HIGH, Severity.CRITICAL) else "review"
        )

        return ModerationResult(
            content_id=content_id,
            severity=severity,
            categories=result.get("categories", []),
            confidence=result.get("confidence", 0.5),
            action=action,
            model_used="gpt-4o-mini",
            latency_ms=round(latency, 1),
            audit_trail={
                "reasoning": result.get("reasoning", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    def moderate(self, content: str, content_id: str) -> ModerationResult:
        """Two-tier moderation: fast filter first, LLM only for uncertain cases."""
        start = time.time()
        severity, confidence, categories = self.primary_filter(content)

        if severity == Severity.CLEAR and confidence > 0.9:
            return ModerationResult(
                content_id=content_id, severity=Severity.CLEAR,
                categories=[], confidence=confidence, action="publish",
                model_used="primary_filter",
                latency_ms=round((time.time() - start) * 1000, 1),
            )

        if severity == Severity.HIGH and confidence > 0.9:
            return ModerationResult(
                content_id=content_id, severity=Severity.HIGH,
                categories=categories, confidence=confidence, action="block",
                model_used="primary_filter",
                latency_ms=round((time.time() - start) * 1000, 1),
            )

        # Uncertain: escalate to LLM
        return self.secondary_analysis(content, content_id)


# ── Document Extraction Pipeline ─────────────────────────────────────────────

@dataclass
class ExtractionField:
    name: str
    value: Any
    confidence: float
    source_location: str  # page/region reference


@dataclass
class DocumentExtractionResult:
    document_id: str
    document_type: str
    fields: list[ExtractionField]
    validation_passed: bool
    validation_errors: list[str]
    needs_human_review: bool
    processing_time_ms: float


class DocumentExtractor:
    """LLM-based structured extraction with Pydantic-style validation."""

    INSURANCE_CLAIM_SCHEMA = {
        "claim_number": {"type": "string", "required": True, "pattern": r"^CLM-\d{6,}$"},
        "policy_number": {"type": "string", "required": True},
        "claimant_name": {"type": "string", "required": True},
        "date_of_loss": {"type": "date", "required": True},
        "loss_amount": {"type": "float", "required": True, "min": 0, "max": 10_000_000},
        "loss_description": {"type": "string", "required": True, "min_length": 10},
    }

    def __init__(self, client: openai.OpenAI):
        self.client = client

    def extract(self, ocr_text: str, document_id: str,
                doc_type: str = "insurance_claim") -> DocumentExtractionResult:
        """Extract structured fields from OCR text using LLM."""
        start = time.time()
        schema = self.INSURANCE_CLAIM_SCHEMA

        schema_desc = "\n".join(
            f"- {name}: {spec['type']}, {'required' if spec.get('required') else 'optional'}"
            for name, spec in schema.items()
        )

        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "system",
                "content": (
                    "You are a document extraction specialist. Extract structured data from the "
                    "document text. Return JSON with the following fields:\n"
                    f"{schema_desc}\n\n"
                    "For each field, also provide a confidence score (0.0-1.0) and the approximate "
                    "location in the document where you found it. Return only valid JSON."
                ),
            }, {
                "role": "user",
                "content": f"Extract fields from this document:\n\n{ocr_text}",
            }],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        extracted = json.loads(response.choices[0].message.content)

        # Build field list and run validation
        fields = []
        validation_errors = []
        for field_name, spec in schema.items():
            value = extracted.get(field_name)
            conf = extracted.get(f"{field_name}_confidence", 0.8)
            location = extracted.get(f"{field_name}_location", "unknown")

            fields.append(ExtractionField(
                name=field_name, value=value,
                confidence=conf, source_location=location,
            ))

            # Validate required fields
            if spec.get("required") and value is None:
                validation_errors.append(f"Missing required field: {field_name}")

            # Validate numeric ranges
            if spec["type"] == "float" and value is not None:
                try:
                    val = float(value)
                    if "min" in spec and val < spec["min"]:
                        validation_errors.append(f"{field_name} below minimum: {val} < {spec['min']}")
                    if "max" in spec and val > spec["max"]:
                        validation_errors.append(f"{field_name} above maximum: {val} > {spec['max']}")
                except (ValueError, TypeError):
                    validation_errors.append(f"{field_name} is not a valid number: {value}")

        # Determine if human review is needed
        low_confidence_fields = [f for f in fields if f.confidence < 0.8]
        needs_review = bool(validation_errors) or bool(low_confidence_fields)

        return DocumentExtractionResult(
            document_id=document_id,
            document_type=doc_type,
            fields=fields,
            validation_passed=not bool(validation_errors),
            validation_errors=validation_errors,
            needs_human_review=needs_review,
            processing_time_ms=round((time.time() - start) * 1000, 1),
        )


# ── Evaluation & Quality Monitoring ──────────────────────────────────────────

@dataclass
class QualityMetrics:
    faithfulness: float     # Is response grounded in context?
    relevance: float        # Does response answer the question?
    completeness: float     # Are all parts of the question addressed?
    safety: float           # Free from harmful content?
    latency_ms: float
    cost_usd: float
    tokens_in: int
    tokens_out: int


class ProductionEvalSampler:
    """Sample and score 5-10% of live traffic for continuous quality monitoring."""

    SLO_THRESHOLDS = {
        "faithfulness": 0.90,
        "relevance": 0.85,
        "completeness": 0.80,
        "safety": 0.99,
    }
    BURN_RATE_WINDOW_MINUTES = 60
    MAX_BURN_RATE = 0.10  # alert if > 10% of samples fail SLO in window

    def __init__(self, sample_rate: float = 0.05):
        self.sample_rate = sample_rate
        self._window: list[tuple[datetime, bool]] = []

    def should_sample(self) -> bool:
        """Probabilistic sampling. Production: use consistent hashing for reproducibility."""
        return np.random.random() < self.sample_rate

    def check_slo(self, metrics: QualityMetrics) -> dict:
        """Check if a single response meets SLO thresholds."""
        violations = []
        for metric_name, threshold in self.SLO_THRESHOLDS.items():
            value = getattr(metrics, metric_name)
            if value < threshold:
                violations.append({
                    "metric": metric_name, "value": value,
                    "threshold": threshold, "gap": round(threshold - value, 3),
                })

        passed = len(violations) == 0
        now = datetime.now(timezone.utc)
        self._window.append((now, passed))

        # Prune old entries
        cutoff_seconds = self.BURN_RATE_WINDOW_MINUTES * 60
        self._window = [
            (ts, p) for ts, p in self._window
            if (now - ts).total_seconds() < cutoff_seconds
        ]

        # Calculate burn rate
        if len(self._window) >= 10:
            failures = sum(1 for _, p in self._window if not p)
            burn_rate = failures / len(self._window)
            alert = burn_rate > self.MAX_BURN_RATE
        else:
            burn_rate = 0.0
            alert = False

        return {
            "passed": passed,
            "violations": violations,
            "burn_rate": round(burn_rate, 3),
            "alert": alert,
            "window_size": len(self._window),
        }


# ── Usage Example ─────────────────────────────────────────────────────────────

def demo_system_design():
    """Demonstrate AI system design building blocks."""

    # 1. Intent routing
    router = IntentRouter()
    queries = [
        "What is my refund policy?",
        "Where is my order #12345?",
        "Compare the step by step differences between the Pro and Enterprise plans",
        "What is the meaning of life?",
    ]
    print("=== Intent Routing ===")
    for q in queries:
        intent = router.classify_intent(q)
        model_decision = router.select_model(q, intent)
        print(f"  '{q[:50]}...' -> intent={intent.value}, decision={model_decision}")

    # 2. Content moderation
    print("\n=== Content Moderation ===")
    contents = [
        ("msg-001", "I love this product, it works great!"),
        ("msg-002", "I will kill the competition with this feature"),
        ("msg-003", "This is a normal question about pricing"),
    ]
    # Note: In production, moderator requires an OpenAI client
    print("  Two-tier moderation: 95% fast filter, 5% LLM escalation")
    print("  Pre-moderation adds 100-300ms latency (regulated industries)")
    print("  Post-moderation for high-volume social (brief harmful window)")

    # 3. Production eval sampling
    print("\n=== Production Eval Sampling ===")
    sampler = ProductionEvalSampler(sample_rate=0.05)
    passing = QualityMetrics(
        faithfulness=0.93, relevance=0.88, completeness=0.85,
        safety=1.0, latency_ms=450, cost_usd=0.003, tokens_in=500, tokens_out=800,
    )
    failing = QualityMetrics(
        faithfulness=0.65, relevance=0.70, completeness=0.50,
        safety=0.98, latency_ms=2500, cost_usd=0.012, tokens_in=1200, tokens_out=2000,
    )
    print(f"  Passing sample: {sampler.check_slo(passing)}")
    print(f"  Failing sample: {sampler.check_slo(failing)}")

    # 4. Scaling reference
    print("\n=== Scaling Reference ===")
    scaling = [
        ("100 QPS", "Single vLLM + single vector DB + Redis cache"),
        ("1K QPS", "Load-balanced vLLM cluster (4-8 GPUs), 3 vector DB replicas"),
        ("10K QPS", "Multi-model routing, 30%+ cache hit, queue autoscaling"),
        ("100K QPS", "Regional deploy, tiered arch, custom embeddings"),
    ]
    for scale, arch in scaling:
        print(f"  {scale}: {arch}")


if __name__ == "__main__":
    demo_system_design()
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Design a RAG-Based Enterprise Customer Support Chatbot

**Problem Statement**: A B2B SaaS company with 500 support articles, 50K daily
conversations, and a 3-second latency SLA wants to replace their keyword-search FAQ
bot with a RAG-powered conversational support system. Target: 95% of responses grounded
in source documents, < $0.05 per conversation, 80% deflection rate (conversations
resolved without human handoff).

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│  Customer Message                                               │
│         │                                                       │
│         v                                                       │
│  ┌──────────────┐                                              │
│  │ Input Guards  │── Toxicity / PII / Injection ── BLOCK       │
│  └──────┬───────┘                                              │
│         v                                                       │
│  ┌──────────────┐                                              │
│  │ Intent       │                                              │
│  │ Classifier   │                                              │
│  └──┬────┬────┬─┘                                              │
│     │    │    │                                                 │
│  Billing Tech  Order                                           │
│     │    │    │                                                 │
│     │    │    └──> API lookup (no LLM) ──> Response             │
│     │    │                                                      │
│     └────┴──> Query Rewrite                                    │
│                │                                                │
│                v                                                │
│         ┌──────────┐     ┌───────────────┐                     │
│         │ Semantic  │ HIT│               │                     │
│         │ Cache     │───>│ Return cached  │                     │
│         └────┬─────┘    └───────────────┘                     │
│              │ MISS                                             │
│              v                                                  │
│         ┌──────────┐                                           │
│         │ Hybrid   │  BM25 + Dense + RRF                       │
│         │ Retrieve │  (500 articles indexed)                    │
│         └────┬─────┘                                           │
│              v                                                  │
│         ┌──────────┐                                           │
│         │ Reranker │  Cross-encoder, top-50 -> top-5           │
│         └────┬─────┘                                           │
│              v                                                  │
│         ┌──────────┐  ┌───────────────┐                        │
│         │ Model    │  │ Conv Memory   │                        │
│         │ Router   │<─│ (Redis, last  │                        │
│         │          │  │  5 turns)     │                        │
│         └────┬─────┘  └───────────────┘                        │
│              v                                                  │
│         ┌──────────────┐                                       │
│         │ Output Guards │── Groundedness < 0.85 ── Escalate    │
│         └──────┬───────┘                                       │
│                v                                                │
│         Response + Source Citations                             │
│                │                                                │
│         Async: Trace, eval sample (5%), cache update            │
└─────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| Knowledge source | RAG (retrieval) | Fine-tuning | RAG | Knowledge changes weekly; fine-tuning requires retraining |
| Search approach | Pure semantic | Hybrid (BM25 + dense) | Hybrid | 15-30% better recall; catches acronyms and exact terms |
| Chunk size | 256 tokens (precise) | 512 tokens (more context) | 512 + 50 overlap | Support articles are paragraph-length; 256 splits answers |
| Default model | GPT-4o (quality) | GPT-4o mini (cost) | GPT-4o mini (80%), GPT-4o for complex | $0.05/conv target requires routing; mini is sufficient for factual Q&A |
| Memory strategy | Full history | Sliding window (5 turns) | Sliding window + summary | Prevents token explosion; summary preserves key context |
| Escalation trigger | Low confidence only | Confidence + frustration detection | Both | Frustrated users need human contact regardless of model confidence |

**Decision Rationale**: The system optimizes for three competing metrics: groundedness
(95% target), cost ($0.05/conv), and deflection rate (80%). RAG is chosen over
fine-tuning because support articles change weekly. Hybrid retrieval is mandatory because
pure semantic search misses exact product names and error codes. The escalation design
prioritizes user experience over deflection rate -- a system that escalates too early
with full context reads as competent, while one that escalates too late reads as broken.
The async eval sampler (5% of traffic = 2,500 scored samples/day) provides statistical
power to detect a 3% quality drop within 24 hours.

---

### Scenario 2: Design a Content Moderation System for 50M Posts/Day

**Problem Statement**: A social platform with 50M user-generated posts per day needs
a moderation system that catches high-severity violations (hate speech, self-harm
promotion, CSAM) with < 0.1% false negative rate, while keeping false positives below
1% to avoid eroding user trust. Pre-moderation latency must be < 300ms for text and
< 2s for images. The system must comply with EU Digital Services Act and produce
complete audit trails.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│  User Content (text, image, video thumbnail)                    │
│         │                                                       │
│         v                                                       │
│  ┌──────────────────────────────────────────────────┐          │
│  │ TIER 1: Fast Classifiers (< 50ms, every post)    │          │
│  │                                                    │          │
│  │  Text: Trained classifier (distilBERT fine-tuned) │          │
│  │  Image: Vision classifier (ViT fine-tuned)        │          │
│  │  Hash: Known-bad content hash matching (PhotoDNA) │          │
│  └────┬─────────────┬──────────────────┬────────────┘          │
│       │             │                  │                        │
│    CLEAR          VIOLATION          UNCERTAIN                  │
│    (85%)          (high conf, 2%)    (13%)                      │
│       │             │                  │                        │
│    Publish       Block + log        ┌──┴──────────────────┐    │
│    immediately   + appeal path      │ TIER 2: LLM Analysis│    │
│       │                             │ (GPT-4o mini,       │    │
│       │                             │  < 500ms, 13%)      │    │
│       │                             └──┬─────────┬────────┘    │
│       │                                │         │             │
│       │                             CLEAR     VIOLATION / UNCERTAIN
│       │                             (8%)      (5%)             │
│       │                                │         │             │
│       │                             Publish   ┌──┴──────────┐  │
│       │                                       │ TIER 3:     │  │
│       │                                       │ Human Queue │  │
│       │                                       │ (< 5%)      │  │
│       │                                       └──┬──────────┘  │
│       │                                          │             │
│       │                                       Decision         │
│       │                                          │             │
│  ┌────┴──────────────────────────────────────────┴─────────┐  │
│  │ AUDIT LAYER: content_id, all model scores, all actions, │  │
│  │ timestamps, reviewer_id (if human), appeal status       │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                                │
│  Corrections from Tier 3 ──> Fine-tuning dataset for Tier 1  │
└─────────────────────────────────────────────────────────────────┘
```

**Cost Analysis**:

| Tier | Volume/Day | Cost per Item | Daily Cost | Monthly Cost |
|---|---|---|---|---|
| Tier 1 (classifier) | 50M | $0.00001 | $500 | $15,000 |
| Tier 2 (LLM) | 6.5M (13%) | $0.0003 | $1,950 | $58,500 |
| Tier 3 (human) | 2.5M (5%) | $0.02 | $50,000 | $1,500,000 |
| **Total** | | | | **$1,573,500** |

**Trade-off Matrix**:

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| Primary filter | Rules + keywords | Fine-tuned distilBERT | Fine-tuned classifier | Keywords miss adversarial evasion; classifier generalizes |
| LLM tier model | GPT-4o (quality) | GPT-4o mini (cost) | GPT-4o mini | At 6.5M calls/day, GPT-4o costs 10x more; mini suffices with fine-tuned primary |
| Pre-mod vs post-mod | Pre-moderation (safe) | Post-moderation (fast UX) | Hybrid: pre-mod for high-risk categories, post-mod for low-risk | CSAM and self-harm require pre-mod; mild toxicity can be post-mod |
| Human queue management | All uncertain to humans | Confidence-tiered | Confidence-tiered | 5% of 50M is 2.5M/day; must be manageable by team of ~300 reviewers |
| False positive vs FN | Optimize for safety (more FP) | Optimize for UX (fewer FP) | Per-category calibration | Hate speech: aggressive threshold (FP ok). Satire: conservative (FP costly) |

**Decision Rationale**: The three-tier architecture is the core cost optimization. If
every post went through an LLM, monthly cost would be $450K for the LLM alone. By
filtering 85% at the classifier tier ($0.00001/item) and only escalating 13% to the
LLM, total LLM spend drops to $58.5K/month. The human review tier is the largest
single cost ($1.5M/month) -- reducing the escalation rate from 5% to 3% saves $600K/month,
making classifier accuracy the highest-leverage investment. Per-category calibration is
essential: the hate speech classifier runs at a lower threshold (more false positives
acceptable) than the satire detector (false positives destroy user trust). The audit
layer records every model score and action per content item -- required by the EU
Digital Services Act and essential for appeals processing.

---

## Key Interview Signals

When discussing AI system design in a Director/VP interview, anchor on these points:

1. **Start with retrieval, not generation** -- ground the LLM in external knowledge.
   A system without retrieval is a hallucination engine.
2. **Design for the failure case** -- every happy path needs a fallback. Escalation
   with full context is an architectural component, not an afterthought.
3. **Cost is a first-class constraint** -- model routing, caching, and batching are
   not optimizations you add later. They are load-bearing structural decisions.
4. **Evaluation is the foundation** -- if you cannot measure quality, you cannot
   improve it. Offline eval, CI gates, and production sampling are three separate
   systems, all required.
5. **The 80/20 rule applies everywhere** -- 80% of requests go to cheap models,
   80% of moderation is handled by classifiers, 80% of document fields pass
   validation. Architecture the common case; handle exceptions deliberately.
6. **Security is a property, not a layer** -- input/output guardrails, PII handling,
   and audit trails must be baked into every component, not bolted on.
