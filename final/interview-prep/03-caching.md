# Caching in LLM Systems

## What Is This?

Caching in LLM systems stores intermediate computation results to avoid redundant work. Think of it as a "cheat sheet" that remembers previous answers.

At its core, LLM inference has two phases:
- **Prefill**: Process the input prompt, compute key-value (KV) pairs for each token, store them in KV cache
- **Decode**: Generate output tokens one at a time, reusing cached KV pairs from prefill

Caching exploits reuse opportunities across these phases:
- **Exact reuse**: Identical prefix appears multiple times (e.g., system prompt, tool definitions)
- **Approximate reuse**: Semantically similar queries (e.g., "refund policy" vs "how do I get a refund")

Five-layer taxonomy:

| Layer | What's Cached | Scope | Analogy |
|-------|---------------|-------|---------|
| **L1: KV Cache** | Key-value tensors for tokens | Single request | CPU L1 cache - fastest, smallest |
| **L2: Prefix/APC** | KV cache for stable prompt prefixes | Across requests with shared prefix | CPU L2 cache - shared across cores |
| **L3: Hosted Prompt Cache** | Provider-managed prefix cache | API-level, managed by OpenAI/Anthropic | CDN edge cache |
| **L4: Semantic Cache** | Embedding-based similar query results | Application-level | Database query cache |
| **L5: Application/Result Cache** | Final LLM outputs | Application-level | HTTP response cache |

## Why It Matters

**Cost savings**: Cached tokens cost 90% less (0.1x) than fresh inference. For a system processing 1M tokens/day with 50% cache hit rate:
- Uncached: 1M tokens × $0.01/1k = $10/day
- With cache: 500k × $0.01 + 500k × $0.001 = $5.50/day (45% savings)

**Latency reduction**: Cached prefill is 4-5x faster than cold prefill. KVGov benchmark shows cached/cold TTFT ratio ~0.22 (78% latency drop).

**Throughput gains**: PagedAttention achieves 2-4x higher throughput vs naive KV management. SGLang RadixAttention hits ~16,200 tok/s vs vLLM ~12,500 tok/s (29% advantage).

**Production impact**:
- Multi-tier cache (L1+L2+L3) can exceed 80% cost savings
- Combined L1+L2 typically yields ~54% savings
- Break-even on first reuse at 1.25x write / 0.1x read pricing

## Architecture / System Design

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────┐
│                      CONTROL PLANE                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Router     │  │ Cache Policy │  │   Telemetry  │      │
│  │  (llm-d,     │  │  (TTL, evict)│  │  (Prometheus)│      │
│  │   Dynamo)    │  │              │  │              │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                       DATA PLANE                            │
│                                                             │
│  Request → Hash → L1 Exact (Redis) ────────→ Hit? Return   │
│              ↓ Miss                                         │
│           Embed → L2 Semantic (HNSW) ──────→ Hit? Return   │
│              ↓ Miss                                         │
│           LLM with L3 Prefix Cache ────────→ Generate       │
│              ↓                                              │
│           Backfill L1, L2 ←────────────────────┘           │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                 PERSISTENCE & STATE                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Redis Cluster│  │ Vector Store │  │ KV Checkpoints│      │
│  │  (L1 exact)  │  │ (HNSW index) │  │  (S3/GCS)    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      TELEMETRY                              │
│  Metrics: hit_rate, latency_p99, cache_size, eviction_count │
│  Alerts: hit_rate < 30%, p99 > SLA, stampede detection      │
└─────────────────────────────────────────────────────────────┘
```

### Detailed Control Plane vs Data Plane

```
┌─────────────────────────────────────────────────────────────┐
│                      CONTROL PLANE                          │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ROUTING & POLICY                                     │  │
│  │  - Cache-aware routing (llm-d, NVIDIA Dynamo)        │  │
│  │  - TTL management (OpenAI 5min, Anthropic 1hr)       │  │
│  │  - Eviction policies (LRU, LFU, TTL)                 │  │
│  │  - Rate limiting (ITPM multipliers)                  │  │
│  │  - Tenant isolation (namespace, RBAC)                │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ MONITORING & OBSERVABILITY                           │  │
│  │  - Prometheus metrics (hit rate, latency, size)      │  │
│  │  - Grafana dashboards (p50/p95/p99 SLAs)             │  │
│  │  - Alerts (stampede, degradation, eviction storms)   │  │
│  │  - Audit logs (cache writes, PII access)             │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                       DATA PLANE                            │
│                                                             │
│  ┌─────────────────┐                                        │
│  │ L1: Exact Match │                                        │
│  │  - Hash: SHA-256│                                        │
│  │  - Store: Redis │ ──→ Hit (p50 < 5ms) → Return          │
│  │  - TTL: 1-24hr  │                                        │
│  └─────────────────┘                                        │
│         ↓ Miss                                              │
│  ┌─────────────────┐                                        │
│  │ L2: Semantic    │                                        │
│  │  - Embed: text- │                                        │
│  │    embedding-3  │                                        │
│  │  - Index: HNSW  │ ──→ Hit (p95 < 50ms) → Return         │
│  │  - Threshold:   │                                        │
│  │    cosine > 0.95│                                        │
│  └─────────────────┘                                        │
│         ↓ Miss                                              │
│  ┌─────────────────┐                                        │
│  │ L3: Prefix/KV   │                                        │
│  │  - vLLM APC or  │                                        │
│  │    SGLang Radix │                                        │
│  │  - Hosted: OAPI │ ──→ Generate (prefix cached)          │
│  │    prompt_cache │                                        │
│  │  - TTL: provider│                                        │
│  │    managed      │                                        │
│  └─────────────────┘                                        │
│         ↓                                                   │
│  ┌─────────────────┐                                        │
│  │ Backfill        │                                        │
│  │  - Write L1, L2 │                                        │
│  │  - Update stats │                                        │
│  │  - Log access   │                                        │
│  └─────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
```

### Multi-Tier Cache Decision Table

| Tier | Mechanism | Hit Savings | Latency (p95) | Best For |
|------|-----------|-------------|---------------|----------|
| **L1: Exact** | SHA-256 hash, Redis | 90% cost | < 5ms | Identical repeated queries |
| **L2: Semantic** | Embedding + HNSW | 60-80% cost | < 50ms | Similar queries, FAQ |
| **L3: Prefix/KV** | PagedAttention, RadixAttention, APC | 80-90% prefill cost | 50-200ms | Shared system/tool prompts |

### Component Choices

| Component | Latency-Optimized | Quality-Optimized |
|-----------|-------------------|-------------------|
| **L1 Store** | Redis (in-memory) | Redis Cluster |
| **L2 Embedding** | text-embedding-3-small | text-embedding-3-large |
| **L2 Index** | HNSW (FAISS/hnswlib) | HNSW + reranker |
| **L2 Threshold** | 0.90 cosine | 0.95 cosine |
| **L3 Engine** | vLLM APC | SGLang RadixAttention |

## Core Concepts & Algorithms

### KV Cache Fundamentals

Every token processed during prefill produces a key-value pair stored in GPU memory:

**Memory formula**:
```
KV_bytes = 2 × n_layers × n_kv_heads × d_head × seq_len × bytes_per_param
```

For Llama-3.1-8B (32 layers, 8 KV heads, 128 head dim, FP16):
```
KV_bytes = 2 × 32 × 8 × 128 × seq_len × 2
         = 131,072 bytes/token
         ≈ 128 KB/token

32k context → 32,768 × 128 KB ≈ 4 GB KV cache
```

**Worked examples**:

| Model | n_layers | n_kv_heads | d_head | bytes/param | bytes/token |
|-------|----------|------------|--------|-------------|-------------|
| Llama-3.1-8B | 32 | 8 | 128 | 2 (FP16) | 131,072 (~128 KB) |
| Llama-3.1-70B | 80 | 8 | 128 | 2 (FP16) | 327,680 (~320 KB) |
| Llama-3.1-405B | 126 | 8 | 128 | 2 (FP16) | 516,096 (~504 KB) |
| DeepSeek-V3 | 61 | 1 × 128 MLA heads | 128 | 2 (FP16) | 70,272 (~69 KB) |

**GQA (Grouped Query Attention)**: Reduces KV heads from n_heads to n_kv_heads (typically 8). Llama-3 uses GQA, cutting KV memory 8x vs full multi-head attention.

**MLA (Multi-Latent Attention)**: DeepSeek-V3 uses 128 latent heads compressed to 1 effective KV head per layer, achieving 93.3% KV memory reduction.

### PagedAttention (vLLM, SOSP 2023)

Treats KV cache like virtual memory with paging. Blocks of size 16 tokens stored non-contiguously, eliminating fragmentation.

**Benefits**:
- 2-4x throughput vs HuggingFace Transformers
- Up to 24x in specific benchmarks
- Near-zero memory waste

**Limitations**:
- Page table overhead (~1-2% memory)
- Assumes uniform block size
- No prefix sharing across sequences

### RadixAttention (SGLang)

Extends PagedAttention with a radix tree for automatic prefix sharing.

**How it works**:
1. Build radix tree of prompt prefixes
2. Shared nodes store KV cache once
3. New request reuses longest matching prefix
4. Only compute KV for unique suffix

**Benchmarks**:
- 5x throughput with high prefix overlap
- 6.4x on NeurIPS shared-prefix workloads
- SGLang: ~16,200 tok/s vs vLLM ~12,500 tok/s (29% faster)

**Degradation**: Longest Prefix Matching (LPM) lookup degrades at ~128 queue depth (tree traversal overhead).

### APC (Automatic Prefix Caching) - vLLM

Hashes prefixes, caches KV blocks, reuses on match.

**Key parameters** (vLLM):
```python
--enable-prefix-caching
--kv-cache-dtype fp8_e5m2  # Quantize to save memory
--cache-salt <string>       # Namespace for multi-tenant
--hash-algo sha256          # or sha256_cbor, xxhash
```

**Hash algorithms**:
- `sha256`: Standard, 32 bytes
- `sha256_cbor`: CBOR-encoded before hash (handles structured inputs)
- `xxhash`: Faster, 8 bytes, collision risk at scale

**Invariants** (design principles):
- **I1: Determinism**: Same prefix hash → same KV blocks
- **I2: Isolation**: Tenants cannot read each other's cache (use `--cache-salt`)
- **I3: Freshness**: TTL enforced (vLLM relies on LRU eviction)
- **I4: Correctness**: Hash collisions must be impossible or detected

### HiCache (LMSYS 2025)

Three-tier hierarchical cache:
- **L1**: Recent KV blocks (GPU memory)
- **L2**: Evicted blocks (CPU memory)
- **L3**: Cold blocks (SSD/NVMe)

**Features**:
- Prefetch: Predict next blocks based on access pattern
- Write policies: Write-through (L1→L2→L3) or write-back (lazy flush)
- Eviction: LRU per tier

**Use case**: Long-context workloads (128k+ tokens) where full KV cache exceeds GPU memory.

### TensorRT-LLM KV Offload

`enableBlockReuse=True` with 45 GiB offload to CPU/disk. Targets long-context inference on consumer GPUs.

### Hosted Prompt Cache (OpenAI, Anthropic, Gemini)

Provider-managed prefix caching at API level. You mark cacheable sections; provider handles storage, eviction, billing.

**OpenAI GPT-5.6+ (Sol, Terra, Luna, Cyber models)**:
- Minimum cacheable: 1,024 tokens
- TTL: ~5 minutes (inferred from docs)
- Pricing: 1.25x write, 0.1x read
- Enable: Set `prompt_cache_key` or use structured prompt format

**Anthropic (Claude 3.5 Sonnet, Opus 4, Haiku 4)**:
- Minimum cacheable: 512-4,096 tokens (model-dependent)
- TTL: 5 minutes (standard) or 1 hour (extended)
- Pricing:
  - 5-minute: 1.25x write, 0.1x read
  - 1-hour: 2.0x write, 0.1x read
- Enable: Use `system` blocks with `cache_control: {type: "ephemeral"}`

**Example (Anthropic)**:
```python
response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    system=[
        {
            "type": "text",
            "text": "You are a customer support agent...",  # Cacheable
            "cache_control": {"type": "ephemeral"}
        }
    ],
    messages=[{"role": "user", "content": "How do I return an item?"}]
)
```

**Gemini (1.5 Pro, 1.5 Flash, 2.0 Flash)**:
- Minimum cacheable: 2,048-4,096 tokens
- TTL: ~10 minutes (inferred)
- Pricing: 1.25x write, 0.1x read
- Enable: Use `cachedContent` API

**Azure OpenAI**:
- Follows OpenAI pricing (1.25x/0.1x)
- TTL: Provider-managed
- Requires explicit opt-in via API parameter

**Fireworks, Together, DeepSeek**:
- Fireworks: 1.25x/0.1x, TTL ~5min
- Together: No explicit pricing (included in base rate)
- DeepSeek: 0.1x read for cached prefill (no write premium)

### Semantic Cache

Embeds query, finds similar cached responses via vector search.

**Architecture**:
1. Query arrives
2. Embed query: `text-embedding-3-small` or `text-embedding-3-large`
3. Search vector store (HNSW index) for top-k neighbors
4. If max cosine similarity > threshold (e.g., 0.95): return cached response
5. Else: call LLM, embed + store response

**Engines**:
- **Redis Stack**: HNSW index, `FT.SEARCH`, native `VADD`/`VSIM` commands in Redis 8
- **GPTCache**: Framework supporting Redis, Faiss, Milvus backends
- **RedisVL**: Redis Vector Library with semantic search
- **LangCache**: LangChain semantic cache integration
- **LangGraph CachePolicy**: Agent-level semantic cache

**When to use**:
- FAQ systems with paraphrased queries
- Customer support (many ways to ask "refund policy")
- RAG with small, stable corpus (< ~200k tokens - otherwise skip RAG)

**When NOT to use**:
- Queries are already identical (use exact L1 cache instead)
- High precision required (semantic false positives risk incorrect answers)
- Embeddings + search cost > LLM cost (unlikely but possible for tiny prompts)

### Cache-Aware Routing

Route requests to instances with warm cache for that prefix.

**Systems**:

| System | Mechanism | Latency Benefit |
|--------|-----------|-----------------|
| **llm-d (Anthropic)** | Precise Scorer: routes to instance with longest cached prefix | 2-3x TTFT improvement |
| **GKE LLM Gateway** | Hash-based routing: consistent hashing by prompt prefix | Increases hit rate 30-50% |
| **NVIDIA Dynamo** | Score = (cached_tokens / total_tokens) × instance_load | Balances hit rate + load |
| **Ray Serve** | Custom router with cache metadata per replica | Configurable policies |
| **SGLang** | LPM (Longest Prefix Match) degrades at ~128 queue depth | 5x throughput with low queue depth |
| **LMCache** | Distributed KV cache with centralized metadata | Cross-instance sharing |

**NVIDIA Dynamo routing formula**:
```
score_i = (cached_tokens_i / total_tokens) × (1 - load_i)
route to argmax(score_i)
```

**llm-d Precise Scorer**: Tracks per-instance radix tree of cached prefixes. Routes to instance with longest matching prefix. Increases cache hit rate from ~40% to ~70% in production.

**When to use**:
- Multi-instance deployments (3+ replicas)
- High prefix overlap (e.g., shared system prompt)
- Latency-sensitive (p95 < 200ms SLAs)

**When to skip**:
- Single-instance deployment
- Uniform random queries (no prefix sharing)
- Routing overhead > latency benefit (rare)

### Stable Prefix Design

For hosted prompt caches to work, cacheable prefix must be stable (identical across requests).

**Best practices**:
1. **Tools first**: Place function definitions at start of system prompt
2. **System instructions next**: Stable role/policy text
3. **Volatile last**: User-specific context, query, examples

**Bad (volatile prefix)**:
```
System: Today is {current_date}. User: {user_name}. You are an assistant...
Tools: [function defs]
```
Every date/user change breaks cache.

**Good (stable prefix)**:
```
Tools: [function defs]
System: You are an assistant...
User: {user_name}. Today is {current_date}. [query]
```
Tools + system are cacheable (stable). Only user section changes.

**OpenAI recommended structure**:
```
[Tools/Functions]  ← Cacheable
[System prompt]    ← Cacheable
[Few-shot examples (if static)] ← Cacheable
[User context (dynamic)]
[User query]
```

**Anthropic cache_control placement**:
- Can mark multiple blocks
- Newer cache block evicts older if TTL overlaps
- Place `cache_control` on last message of cacheable prefix

### Skip RAG When Corpus Is Small

If your entire knowledge base is < ~200k tokens and static:
- Skip retrieval step
- Include full corpus in cacheable system prompt
- Let hosted cache handle it (costs 0.1x after first load)

**Example**:
- Documentation: 50k tokens
- Product catalog: 30k tokens
- Total: 80k tokens

Instead of RAG (embed query → retrieve chunks → LLM):
```python
system = """
[Full docs]
[Full catalog]
You are a support agent. Use the above documentation to answer questions.
"""  # Mark cacheable, 80k tokens

# First request: pay 1.25x × 80k + 1x × query
# Subsequent: pay 0.1x × 80k + 1x × query (87% savings on context)
```

Works if corpus is static (no real-time updates) and fits in model context window.

## Code Examples

### Production Multi-Tier Cache with Circuit Breakers (Grok Source)

```python
import hashlib
import hmac
import json
import redis
import anthropic
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import openai

@dataclass
class CacheMetrics:
    """Track cache performance."""
    l1_hits: int = 0
    l1_misses: int = 0
    l2_hits: int = 0
    l2_misses: int = 0
    llm_calls: int = 0
    total_requests: int = 0
    
    def hit_rate(self) -> float:
        hits = self.l1_hits + self.l2_hits
        return hits / self.total_requests if self.total_requests > 0 else 0.0

class CircuitBreaker:
    """Simple circuit breaker for cache failures."""
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half_open
    
    def call(self, func, *args, **kwargs):
        if self.state == "open":
            if datetime.now() - self.last_failure_time > timedelta(seconds=self.timeout):
                self.state = "half_open"
            else:
                raise Exception("Circuit breaker OPEN")
        
        try:
            result = func(*args, **kwargs)
            if self.state == "half_open":
                self.state = "closed"
                self.failures = 0
            return result
        except Exception as e:
            self.failures += 1
            self.last_failure_time = datetime.now()
            if self.failures >= self.failure_threshold:
                self.state = "open"
            raise e

class CacheRuntime:
    """Production-grade multi-tier LLM cache with fallbacks."""
    
    def __init__(
        self,
        redis_client: redis.Redis,
        anthropic_client: anthropic.Anthropic,
        openai_client: openai.OpenAI,
        cache_salt: str,
        semantic_threshold: float = 0.95,
        l1_ttl: int = 3600,  # 1 hour
        l2_ttl: int = 7200,  # 2 hours
    ):
        self.redis = redis_client
        self.anthropic = anthropic_client
        self.openai = openai_client
        self.cache_salt = cache_salt
        self.semantic_threshold = semantic_threshold
        self.l1_ttl = l1_ttl
        self.l2_ttl = l2_ttl
        self.metrics = CacheMetrics()
        self.l1_breaker = CircuitBreaker()
        self.l2_breaker = CircuitBreaker()
    
    def _canonical_hash(self, prompt: str, model: str) -> str:
        """Compute HMAC-SHA256 hash with salt for cache key."""
        # Canonical JSON ensures consistent ordering
        canonical = json.dumps(
            {"prompt": prompt, "model": model},
            sort_keys=True,
            separators=(',', ':')
        )
        return hmac.new(
            self.cache_salt.encode(),
            canonical.encode(),
            hashlib.sha256
        ).hexdigest()
    
    def _l1_get(self, key: str) -> Optional[str]:
        """L1: Exact match cache (Redis)."""
        try:
            return self.l1_breaker.call(self.redis.get, key)
        except Exception:
            return None
    
    def _l1_set(self, key: str, value: str):
        """Store in L1 with TTL."""
        try:
            self.l1_breaker.call(self.redis.setex, key, self.l1_ttl, value)
        except Exception:
            pass  # Silent fail on cache write
    
    def _l2_search(self, prompt: str) -> Optional[tuple[str, float]]:
        """L2: Semantic search (embedding + vector search)."""
        try:
            # Get embedding
            embed_response = self.l2_breaker.call(
                self.openai.embeddings.create,
                model="text-embedding-3-small",
                input=prompt
            )
            query_embedding = embed_response.data[0].embedding
            
            # Search Redis vector index (assumes FT.SEARCH configured)
            # Simplified: In production, use RedisVL or HNSW library
            search_result = self.l2_breaker.call(
                self.redis.execute_command,
                "FT.SEARCH",
                "semantic_cache_idx",
                f"*=>[KNN 1 @embedding $vector AS score]",
                "PARAMS", "2", "vector", query_embedding,
                "RETURN", "2", "response", "score",
                "DIALECT", "2"
            )
            
            if search_result and len(search_result) > 1:
                score = float(search_result[2])  # Cosine similarity
                if score >= self.semantic_threshold:
                    response = search_result[1]
                    return response, score
            return None
        except Exception:
            return None
    
    def _l2_set(self, prompt: str, response: str):
        """Store in L2 semantic cache."""
        try:
            # Get embedding
            embed_response = self.openai.embeddings.create(
                model="text-embedding-3-small",
                input=prompt
            )
            embedding = embed_response.data[0].embedding
            
            # Store in Redis with vector (simplified)
            key = f"semantic:{hashlib.sha256(prompt.encode()).hexdigest()}"
            self.redis.hset(key, mapping={
                "prompt": prompt,
                "response": response,
                "embedding": json.dumps(embedding)
            })
            self.redis.expire(key, self.l2_ttl)
        except Exception:
            pass
    
    def _llm_call_with_prefix_cache(self, prompt: str, model: str) -> str:
        """L3: Call LLM with prefix caching enabled."""
        if "claude" in model:
            # Anthropic: Use cache_control
            response = self.anthropic.messages.create(
                model=model,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": prompt,  # Simplified: should split stable prefix
                        "cache_control": {"type": "ephemeral"}
                    }
                ],
                messages=[{"role": "user", "content": "Continue"}]
            )
            return response.content[0].text
        else:
            # OpenAI: Use prompt_cache_key or structured format
            response = self.openai.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                # Note: OpenAI auto-caches if prefix > 1024 tokens
            )
            return response.choices[0].message.content
    
    def generate(
        self,
        prompt: str,
        model: str,
        use_semantic: bool = True,
        use_exact: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate response with multi-tier cache fallback.
        
        Returns:
            {
                "response": str,
                "cache_level": "l1" | "l2" | "llm",
                "latency_ms": float,
            }
        """
        start = datetime.now()
        self.metrics.total_requests += 1
        
        # L1: Exact match
        if use_exact:
            cache_key = self._canonical_hash(prompt, model)
            cached = self._l1_get(cache_key)
            if cached:
                self.metrics.l1_hits += 1
                latency = (datetime.now() - start).total_seconds() * 1000
                return {
                    "response": cached,
                    "cache_level": "l1",
                    "latency_ms": latency,
                }
            self.metrics.l1_misses += 1
        
        # L2: Semantic match
        if use_semantic:
            semantic_result = self._l2_search(prompt)
            if semantic_result:
                response, score = semantic_result
                self.metrics.l2_hits += 1
                latency = (datetime.now() - start).total_seconds() * 1000
                return {
                    "response": response,
                    "cache_level": "l2",
                    "semantic_score": score,
                    "latency_ms": latency,
                }
            self.metrics.l2_misses += 1
        
        # L3: LLM call with prefix cache
        self.metrics.llm_calls += 1
        response = self._llm_call_with_prefix_cache(prompt, model)
        latency = (datetime.now() - start).total_seconds() * 1000
        
        # Backfill caches
        if use_exact:
            self._l1_set(cache_key, response)
        if use_semantic:
            self._l2_set(prompt, response)
        
        return {
            "response": response,
            "cache_level": "llm",
            "latency_ms": latency,
        }

# Usage
cache = CacheRuntime(
    redis_client=redis.Redis(host="localhost", port=6379),
    anthropic_client=anthropic.Anthropic(api_key="..."),
    openai_client=openai.OpenAI(api_key="..."),
    cache_salt="production-v1",
)

result = cache.generate(
    prompt="What is your refund policy?",
    model="claude-3-5-sonnet-20241022"
)
print(f"Response: {result['response']}")
print(f"Cache level: {result['cache_level']}")
print(f"Latency: {result['latency_ms']:.2f}ms")
print(f"Cache hit rate: {cache.metrics.hit_rate():.2%}")
```

### Multi-Tier Cache with Request Coalescing (Opus Source)

```python
import asyncio
import hashlib
from typing import Optional, Dict
from dataclasses import dataclass
import time

@dataclass
class CacheEntry:
    value: str
    timestamp: float
    ttl: int

class XFetchCache:
    """
    Cache with XFetch pattern to prevent stampedes.
    Only one request fetches; others wait for result.
    """
    def __init__(self):
        self.cache: Dict[str, CacheEntry] = {}
        self.in_flight: Dict[str, asyncio.Future] = {}
        self.lock = asyncio.Lock()
    
    async def get_or_fetch(
        self,
        key: str,
        fetch_fn,
        ttl: int = 3600
    ) -> str:
        """Get from cache or fetch, coalescing concurrent requests."""
        now = time.time()
        
        # Check cache
        if key in self.cache:
            entry = self.cache[key]
            if now - entry.timestamp < entry.ttl:
                return entry.value
            else:
                del self.cache[key]
        
        # Check if another request is already fetching
        async with self.lock:
            if key in self.in_flight:
                # Wait for in-flight request
                return await self.in_flight[key]
            
            # Start new fetch
            future = asyncio.create_task(self._fetch_and_cache(key, fetch_fn, ttl))
            self.in_flight[key] = future
        
        try:
            return await future
        finally:
            async with self.lock:
                if key in self.in_flight:
                    del self.in_flight[key]
    
    async def _fetch_and_cache(self, key: str, fetch_fn, ttl: int) -> str:
        """Fetch value and update cache."""
        value = await fetch_fn()
        self.cache[key] = CacheEntry(
            value=value,
            timestamp=time.time(),
            ttl=ttl
        )
        return value

class MultiTierLLMCache:
    """
    Production multi-tier cache with XFetch stampede prevention.
    """
    def __init__(
        self,
        redis_client,
        llm_client,
        l1_ttl: int = 3600,
    ):
        self.redis = redis_client
        self.llm = llm_client
        self.l1_ttl = l1_ttl
        self.xfetch = XFetchCache()
        self.metrics = {
            "l1_hits": 0,
            "l1_misses": 0,
            "llm_calls": 0,
            "coalesced_requests": 0,
        }
    
    def _hash_key(self, prompt: str, model: str) -> str:
        """Generate cache key."""
        canonical = f"{model}:{prompt}"
        return hashlib.sha256(canonical.encode()).hexdigest()
    
    async def _fetch_from_llm(self, prompt: str, model: str) -> str:
        """Fetch from LLM (L3 with prefix cache)."""
        self.metrics["llm_calls"] += 1
        # Simplified: call LLM API
        response = await self.llm.generate(prompt, model)
        return response
    
    async def generate(self, prompt: str, model: str) -> str:
        """Generate with multi-tier cache."""
        cache_key = self._hash_key(prompt, model)
        
        # L1: Redis exact match
        cached = self.redis.get(cache_key)
        if cached:
            self.metrics["l1_hits"] += 1
            return cached.decode()
        
        self.metrics["l1_misses"] += 1
        
        # L2: XFetch with request coalescing
        async def fetch():
            return await self._fetch_from_llm(prompt, model)
        
        response = await self.xfetch.get_or_fetch(cache_key, fetch, self.l1_ttl)
        
        # Backfill L1
        self.redis.setex(cache_key, self.l1_ttl, response)
        
        return response

# Usage
async def main():
    cache = MultiTierLLMCache(redis_client, llm_client)
    
    # Simulate concurrent requests (stampede scenario)
    tasks = [
        cache.generate("What is AI?", "gpt-4")
        for _ in range(100)  # 100 concurrent identical requests
    ]
    
    results = await asyncio.gather(*tasks)
    
    print(f"LLM calls: {cache.metrics['llm_calls']}")  # Should be 1
    print(f"Requests coalesced: {len(tasks) - cache.metrics['llm_calls']}")
```

### Cache-Aware Router (Opus Source)

```python
from typing import List, Dict
from dataclasses import dataclass
import random

@dataclass
class Instance:
    id: str
    endpoint: str
    load: float  # 0.0 to 1.0
    cached_prefixes: set  # Set of cached prefix hashes

class CacheAwareRouter:
    """
    Route requests to instances with warm cache.
    Based on NVIDIA Dynamo approach.
    """
    def __init__(self, instances: List[Instance]):
        self.instances = instances
    
    def _hash_prefix(self, prompt: str, prefix_len: int = 1024) -> str:
        """Hash first N tokens of prompt as prefix."""
        # Simplified: use first N chars as proxy for tokens
        prefix = prompt[:prefix_len]
        return hashlib.sha256(prefix.encode()).hexdigest()
    
    def route(self, prompt: str) -> Instance:
        """
        Route to instance with best cache hit + load score.
        
        Score = (cache_hit_score) × (1 - load)
        """
        prefix_hash = self._hash_prefix(prompt)
        
        scores = []
        for instance in self.instances:
            # Cache hit score: 1.0 if cached, 0.0 if not
            cache_hit = 1.0 if prefix_hash in instance.cached_prefixes else 0.0
            
            # Combined score
            score = cache_hit * (1 - instance.load)
            scores.append((score, instance))
        
        # Route to highest score
        scores.sort(reverse=True, key=lambda x: x[0])
        
        # If all scores are 0 (no cache hits), round-robin
        if scores[0][0] == 0:
            return random.choice(self.instances)
        
        return scores[0][1]

# Usage
instances = [
    Instance(id="a", endpoint="http://a:8000", load=0.3, cached_prefixes={"abc123"}),
    Instance(id="b", endpoint="http://b:8000", load=0.7, cached_prefixes={"def456"}),
    Instance(id="c", endpoint="http://c:8000", load=0.5, cached_prefixes=set()),
]

router = CacheAwareRouter(instances)

prompt_with_cached_prefix = "..." # Hashes to "abc123"
instance = router.route(prompt_with_cached_prefix)
print(f"Routed to: {instance.id}")  # Should route to instance 'a'
```

### Build Cacheable Prompt (Opus Source)

```python
from typing import List, Dict, Any

def build_cacheable_prompt(
    tools: List[Dict[str, Any]],
    system_instructions: str,
    user_context: str,
    query: str,
    provider: str = "anthropic",
) -> Dict[str, Any]:
    """
    Build prompt with stable prefix for caching.
    
    Structure:
    [Tools] ← Cacheable
    [System] ← Cacheable
    [User context + query] ← Volatile
    """
    if provider == "anthropic":
        # Anthropic: Use system blocks with cache_control
        return {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "system": [
                {
                    "type": "text",
                    "text": f"Available tools:\n{json.dumps(tools, indent=2)}",
                    "cache_control": {"type": "ephemeral"}
                },
                {
                    "type": "text",
                    "text": system_instructions,
                    "cache_control": {"type": "ephemeral"}
                },
            ],
            "messages": [
                {
                    "role": "user",
                    "content": f"{user_context}\n\nQuery: {query}"
                }
            ]
        }
    elif provider == "openai":
        # OpenAI: Structure with tools first, then system, then user
        # Auto-caches if prefix > 1024 tokens
        return {
            "model": "gpt-4-turbo",
            "messages": [
                {
                    "role": "system",
                    "content": f"Available tools:\n{json.dumps(tools, indent=2)}\n\n{system_instructions}"
                },
                {
                    "role": "user",
                    "content": f"{user_context}\n\nQuery: {query}"
                }
            ],
            "tools": tools,  # Also pass tools in structured format
        }
    else:
        raise ValueError(f"Unknown provider: {provider}")

# Usage
tools = [
    {
        "name": "search_docs",
        "description": "Search documentation",
        "parameters": {"query": {"type": "string"}},
    },
    # ... 50 more tools (to reach 1024+ tokens)
]

system = "You are a helpful assistant. Use tools to answer queries."

prompt = build_cacheable_prompt(
    tools=tools,
    system_instructions=system,
    user_context="User: Alice, Tier: Premium",
    query="How do I reset my password?",
    provider="anthropic"
)

# Tools + system are cacheable (stable)
# User context + query are volatile (change per request)
```

## Token Economics & Cost Analysis

### Provider Pricing (Detailed Comparison)

**OpenAI GPT-5.6+ Models**:

| Model | Input (per 1M tokens) | Cached Read (per 1M tokens) | Cache Write Multiplier | TTL |
|-------|----------------------|----------------------------|------------------------|-----|
| GPT-4o (sol) | $10.00 | $1.00 | 1.25x ($12.50) | ~5 min |
| GPT-4o-mini (terra) | $0.50 | $0.05 | 1.25x ($0.625) | ~5 min |
| o1 (luna) | $15.00 | $1.50 | 1.25x ($18.75) | ~5 min |
| o1-mini (cyber) | $3.00 | $0.30 | 1.25x ($3.75) | ~5 min |

**Anthropic Claude Models**:

| Model | Input (per 1M tokens) | Cache Write 5min | Cache Read 5min | Cache Write 1hr | Cache Read 1hr |
|-------|----------------------|------------------|-----------------|-----------------|----------------|
| Claude 3.5 Sonnet | $3.00 | $3.75 (1.25x) | $0.30 (0.1x) | $6.00 (2.0x) | $0.30 (0.1x) |
| Claude Opus 4 | $15.00 | $18.75 (1.25x) | $1.50 (0.1x) | $30.00 (2.0x) | $1.50 (0.1x) |
| Claude Sonnet 4 | $3.00 | $3.75 (1.25x) | $0.30 (0.1x) | $6.00 (2.0x) | $0.30 (0.1x) |
| Claude Haiku 4 | $0.25 | $0.3125 (1.25x) | $0.025 (0.1x) | $0.50 (2.0x) | $0.025 (0.1x) |

**Google Gemini Models**:

| Model | Input (per 1M tokens) | Cache Write | Cache Read | TTL | Min Cacheable |
|-------|----------------------|-------------|------------|-----|---------------|
| Gemini 1.5 Pro | $1.25 | $1.5625 (1.25x) | $0.125 (0.1x) | ~10 min | 2,048 tokens |
| Gemini 1.5 Flash | $0.075 | $0.09375 (1.25x) | $0.0075 (0.1x) | ~10 min | 2,048 tokens |
| Gemini 2.0 Flash | $0.10 | $0.125 (1.25x) | $0.01 (0.1x) | ~10 min | 4,096 tokens |

**Other Providers**:

| Provider | Model | Input | Cache Write | Cache Read | TTL | Min Cacheable |
|----------|-------|-------|-------------|------------|-----|---------------|
| **Azure OpenAI** | GPT-4o | $10.00 | $12.50 (1.25x) | $1.00 (0.1x) | Provider-managed | 1,024 |
| **Fireworks** | Llama-3.1-405B | $3.00 | $3.75 (1.25x) | $0.30 (0.1x) | ~5 min | 1,024 |
| **Together** | Llama-3.1-405B | $3.00 | Included | Included | N/A | N/A |
| **DeepSeek** | DeepSeek-V3 | $0.27 | $0.27 (no premium) | $0.027 (0.1x) | ~5 min | 1,024 |

### Break-Even Analysis

With W = write multiplier (1.25x), R = read multiplier (0.1x):

**Break-even formula**:
```
n >= (W - R) / (1 - R)
n >= (1.25 - 0.1) / (1 - 0.1)
n >= 1.15 / 0.9
n >= 1.28
```

**First reuse pays back the write premium.** After 2 reads, you're saving money.

### Worked Cost Examples

#### Example A: Multi-Tenant Agent (8k Tool Definitions)

**Setup**:
- Tools/system: 8,000 tokens (cacheable)
- User query: 200 tokens (volatile)
- Model: Claude 3.5 Sonnet
- Traffic: 1,000 requests/day

**Scenario 1: No caching**:
```
Cost per request = (8,000 + 200) × $3.00 / 1M = $0.0246
Daily cost = 1,000 × $0.0246 = $24.60
Monthly cost = $24.60 × 30 = $738
```

**Scenario 2: 5-minute cache (50% hit rate)**:
```
First request (cache write):
  = 8,000 × $3.75/1M + 200 × $3.00/1M
  = $0.030 + $0.0006
  = $0.0306

Cache hit (500 requests):
  = 8,000 × $0.30/1M + 200 × $3.00/1M
  = $0.0024 + $0.0006
  = $0.0030

Cache miss (500 requests, rewrite):
  = 500 × $0.0306
  = $15.30

Total daily:
  = $15.30 (misses) + 500 × $0.0030 (hits)
  = $15.30 + $1.50
  = $16.80

Monthly cost = $16.80 × 30 = $504
Savings = ($738 - $504) / $738 = 31.7%
```

**Scenario 3: 1-hour cache (80% hit rate)**:
```
Cache write (200 requests):
  = 200 × (8,000 × $6.00/1M + 200 × $3.00/1M)
  = 200 × $0.0486
  = $9.72

Cache hit (800 requests):
  = 800 × $0.0030
  = $2.40

Total daily = $9.72 + $2.40 = $12.12
Monthly cost = $12.12 × 30 = $363.60
Savings = ($738 - $363.60) / $738 = 50.7%
```

#### Example B: Gemini Explicit Cache (Stable 10k Context)

**Setup**:
- Cached content: 10,000 tokens (docs)
- Query: 100 tokens
- Model: Gemini 1.5 Flash
- Traffic: 10,000 requests/day
- Hit rate: 90%

**No cache**:
```
Cost = 10,000 × (10,000 + 100) × $0.075 / 1M
     = 10,000 × $0.7575
     = $7,575/day
```

**With cache**:
```
Cache writes (1,000 cold):
  = 1,000 × (10,000 × $0.09375/1M + 100 × $0.075/1M)
  = 1,000 × $0.9450
  = $945

Cache reads (9,000 warm):
  = 9,000 × (10,000 × $0.0075/1M + 100 × $0.075/1M)
  = 9,000 × $0.0825
  = $742.50

Total = $945 + $742.50 = $1,687.50/day
Savings = ($7,575 - $1,687.50) / $7,575 = 77.7%
```

#### Example C: DeepSeek Self-Hosted Cost Comparison

**Setup**:
- Prefix: 5,000 tokens
- Completion: 500 tokens
- Requests: 100,000/day

**DeepSeek API with cache**:
```
Write (20k cold): 20,000 × 5,000 × $0.27/1M = $27.00
Read (80k warm): 80,000 × 5,000 × $0.027/1M = $10.80
Completion: 100,000 × 500 × $1.10/1M = $55.00
Total = $27 + $10.80 + $55 = $92.80/day
```

**OpenAI GPT-4o with cache**:
```
Write: 20,000 × 5,000 × $12.50/1M = $1,250
Read: 80,000 × 5,000 × $1.00/1M = $400
Completion: 100,000 × 500 × $30/1M = $1,500
Total = $1,250 + $400 + $1,500 = $3,150/day
```

DeepSeek is 34x cheaper ($92.80 vs $3,150).

#### Example D: Fireworks vs OpenAI (Llama-3.1-405B)

**Setup**:
- Cached: 20,000 tokens
- Dynamic: 500 tokens
- Requests: 1,000/day
- Hit rate: 70%

**Fireworks**:
```
Write (300): 300 × 20,000 × $3.75/1M = $22.50
Read (700): 700 × 20,000 × $0.30/1M = $4.20
Dynamic: 1,000 × 500 × $3.00/1M = $1.50
Total = $22.50 + $4.20 + $1.50 = $28.20/day
```

**OpenAI GPT-4o** (similar capability):
```
Write: 300 × 20,000 × $12.50/1M = $75.00
Read: 700 × 20,000 × $1.00/1M = $14.00
Dynamic: 1,000 × 500 × $10/1M = $5.00
Total = $75 + $14 + $5 = $94/day
```

Fireworks is 3.3x cheaper ($28.20 vs $94).

### Rate Limits (ITPM Multipliers)

Cached reads count toward rate limits but at reduced weight:

| Provider | Cached Read Impact | Example |
|----------|-------------------|---------|
| **OpenAI** | 1x ITPM (same as uncached) | 10k ITPM limit applies equally |
| **Anthropic** | 0.1x ITPM | 10k cached reads = 1k ITPM usage |
| **Gemini** | 0.1x ITPM (inferred) | 10k cached reads = 1k ITPM usage |

This means with Anthropic, you can serve 10x more requests within the same rate limit if they're cache hits.

### Latency Targets (Inferred from Production SLAs)

| Tier | p50 | p95 | p99 | Notes |
|------|-----|-----|-----|-------|
| **L1 Exact (Redis)** | < 2ms | < 5ms | < 10ms | In-region Redis |
| **L2 Semantic (local HNSW)** | < 10ms | < 30ms | < 50ms | Embedding + search |
| **L3 Prefix (self-hosted)** | < 100ms | < 200ms | < 500ms | vLLM/SGLang APC |
| **L3 Prefix (hosted hot)** | < 150ms | < 300ms | < 600ms | OpenAI/Anthropic warm |
| **L3 Prefix (hosted cold)** | 500ms-2s | 2s-5s | 5s-10s | Cold start, model load |
| **Full LLM (no cache)** | 1s-5s | 5s-15s | 15s-30s | Depends on output length |

### Throughput Comparisons

| System | Throughput (tok/s) | Configuration | Benchmark |
|--------|-------------------|---------------|-----------|
| **SGLang + RadixAttention** | ~16,200 | High prefix overlap | NeurIPS shared-prefix |
| **vLLM + APC** | ~12,500 | High prefix overlap | Same workload |
| **vLLM (no cache)** | ~8,000 | Baseline | - |
| **HuggingFace Transformers** | ~3,500 | Naive KV management | SOSP 2023 |
| **TGI v3** | ~13,000 | Prefix caching enabled | TGI benchmarks |

SGLang has 29% throughput advantage over vLLM due to RadixAttention's efficient prefix matching.

### Key Numbers to Memorize

**Pricing**:
- Write multiplier: 1.25x (5-min TTL) or 2.0x (1-hour TTL)
- Read multiplier: 0.1x (90% savings)
- Break-even: ~1.3 reads (first reuse pays back)

**Minimum Cacheable**:
- OpenAI: 1,024 tokens
- Anthropic: 512-4,096 tokens (model-dependent)
- Gemini: 2,048-4,096 tokens

**TTLs**:
- OpenAI: ~5 minutes (inferred)
- Anthropic: 5 minutes (default) or 1 hour (extended)
- Gemini: ~10 minutes (inferred)

**KV Memory**:
- Llama-3.1-8B: ~128 KB/token
- Llama-3.1-405B: ~504 KB/token
- 32k context @ 8B: ~4 GB KV cache

**Latency**:
- L1 exact: < 5ms p95
- L2 semantic: < 50ms p95
- L3 prefix (hot): < 300ms p95
- Cached TTFT ratio: ~0.22 (78% faster)

**Throughput**:
- SGLang: ~16,200 tok/s (RadixAttention)
- vLLM: ~12,500 tok/s (APC)
- PagedAttention: 2-4x vs naive

## Trade-offs & Failure Modes

### Common Failure Modes

| Failure Mode | Symptom | Root Cause | Mitigation |
|--------------|---------|------------|------------|
| **Cache stampede** | Sudden latency spike, LLM overload | Many requests for expired key hit simultaneously | XFetch, TTL jitter, stale-while-revalidate |
| **Stale responses** | Outdated answers served | TTL too long, no invalidation on data update | Shorter TTL, explicit invalidation API |
| **False semantic hits** | Wrong answer from similar query | Threshold too low (< 0.90) | Raise threshold to 0.95+, use reranker |
| **Memory exhaustion** | OOM, cache eviction storms | KV cache size exceeds GPU memory | Offload to CPU/disk (HiCache), quantize KV (FP8) |
| **Tenant isolation breach** | One tenant reads another's cache | No cache salt, shared Redis namespace | Use HMAC salt, tenant-specific namespaces |
| **Timing side channels** | Attacker infers cached content via latency | Cache hit faster than miss | Constant-time padding, KVGov defenses |
| **Hash collisions** | Wrong response served | xxhash collision at scale | Use SHA-256, monitor collision metrics |
| **LPM degradation** | RadixAttention slows down | Queue depth > 128, tree traversal overhead | Limit queue depth, hybrid Radix + hash |
| **TTL mismatch** | Cache miss despite recent request | Provider TTL expired (5min), client assumes 1hr | Monitor cache hit rate, align TTLs |
| **Replica cold start** | High latency after deployment | New replica has empty cache | Pre-warm with common prefixes, rolling deploy |
| **Back-pressure overload** | Upstream timeout, request drop | LLM saturated, cache misses pile up | Circuit breaker, rate limiting, queue depth limits |
| **PII leakage** | Sensitive data cached/logged | No redaction pipeline | PII detection + redaction, audit trail |
| **Idempotency violation** | Duplicate LLM calls for same request | No request deduplication | Idempotency keys, XFetch |
| **Eviction storm** | Sudden cache flush | Redis memory limit, LRU evicts too many | Increase memory, tune eviction policy |
| **Rate limit hit** | 429 errors despite caching | ITPM counted at 1x for cached reads (OpenAI) | Use provider with 0.1x ITPM for cached (Anthropic) |

### Failure Taxonomy (Detailed)

| Category | Examples | Transient? | Recovery Strategy |
|----------|----------|-----------|-------------------|
| **Transient** | Network timeout, Redis connection reset | Yes | Retry with exponential backoff |
| **Permanent** | Invalid API key, model not found | No | Fail fast, alert operator |
| **Adversarial** | Timing attack, hash collision attack | No | Zero-trust architecture, constant-time ops |
| **Capacity** | OOM, disk full, rate limit | Depends | Circuit breaker, back-pressure, scale out |
| **Correctness** | Wrong answer cached, stale data | No | Invalidation, version cache keys |
| **Performance** | LPM degradation, eviction storm | Yes | Throttle, tune parameters |

### Circuit Breaker Design

```
States:
  CLOSED → Normal operation
  OPEN   → Fail fast (skip cache, direct to LLM)
  HALF_OPEN → Test with 1 request

Thresholds:
  - Failure rate > 50% over 10 requests → OPEN
  - Timeout after 60 seconds → HALF_OPEN
  - 1 success in HALF_OPEN → CLOSED
  - 1 failure in HALF_OPEN → OPEN

Flow:
  Request → Check state
    CLOSED: Try cache → Success: return | Failure: increment counter
    OPEN: Skip cache, call LLM
    HALF_OPEN: Try cache → Success: CLOSED | Failure: OPEN
```

ASCII diagram:
```
         ┌─────────┐
         │ CLOSED  │ ◄────┐
         └─────────┘      │
              │           │
       failures > 50%     │ 1 success
              │           │
              ▼           │
         ┌─────────┐      │
         │  OPEN   │      │
         └─────────┘      │
              │           │
        timeout 60s       │
              │           │
              ▼           │
         ┌─────────┐      │
         │HALF_OPEN│ ─────┘
         └─────────┘
              │
         1 failure
              │
              ▼
         (back to OPEN)
```

### Stampede Prevention Techniques

| Technique | How It Works | Pros | Cons |
|-----------|--------------|------|------|
| **TTL Jitter** | Add random ±10% to TTL | Simple, no coordination | Still allows mini-stampedes |
| **Stale-While-Revalidate** | Serve stale, fetch in background | Low latency, no stampede | Serves stale data briefly |
| **Request Coalescing (XFetch)** | First request fetches, others wait | Eliminates duplicate LLM calls | Adds queueing latency |
| **Distributed Locking** | Redis SETNX for fetch lock | Strong consistency | Lock contention, deadlock risk |
| **Probabilistic Early Expiration** | Randomly refresh before TTL with p = (now - create) / TTL | Spreads refresh load | Complex to tune |

**Recommended**: XFetch for read-heavy, Stale-While-Revalidate for latency-sensitive.

### Security: Timing Side Channels

**Attack vectors**:
- **KVGov** (ICML 2024): Infer if prompt prefix is cached by measuring TTFT variance
- **EarlyBird**: Detect cached tokens via generation speed
- **PROMPTPEEK**: Reconstruct cached content via timing oracle
- **InputSnatch**: Steal input via cache timing + prompt injection
- **CVE-2025-46570**: vLLM side channel allowing prefix inference

**Defenses**:
1. **Constant-time padding**: Add random delay to cache hits to match miss latency distribution
2. **Tenant isolation**: Separate Redis namespaces, HMAC salt per tenant
3. **Audit logging**: Log all cache accesses with tenant ID
4. **Rate limiting**: Throttle timing probe attempts
5. **SafeKV**: Noise injection in timing signals

### Multi-Tenant Isolation Models

| Isolation Level | Mechanism | Security | Cost | Use Case |
|----------------|-----------|----------|------|----------|
| **Namespace** | Redis key prefix per tenant | Low (shared instance) | Low | Trusted tenants, dev |
| **Database** | Separate Redis DB per tenant | Medium | Medium | Small-scale SaaS |
| **Cluster** | Dedicated Redis cluster per tier | High | High | Enterprise, compliance |
| **Process** | Separate vLLM instance per tenant | Very high | Very high | Government, healthcare |

**Provider isolation**:

| Provider | Isolation Model | Cache Sharing |
|----------|----------------|---------------|
| **vLLM** | Shared instance, namespace via `--cache-salt` | Across tenants (if salt reused) |
| **SGLang** | Shared RadixAttention tree | Across all requests |
| **OpenAI** | Per-account isolation (API key) | No cross-account sharing |
| **Anthropic** | Per-account isolation | No cross-account sharing |
| **Azure OpenAI** | Per-deployment isolation | Configurable |
| **Gemini** | Per-project isolation | No cross-project sharing |
| **Fireworks** | Per-account isolation | No cross-account sharing |
| **Together** | Shared infrastructure | Unknown |

**Recommendation**: Use hosted (OpenAI, Anthropic) for strong isolation. Self-hosted requires careful salt management + monitoring.

### Zero-Trust Cache Architecture

| Component | Security Control |
|-----------|------------------|
| **L1 Store** | TLS in transit, encryption at rest (Redis 6+), mTLS between services |
| **L2 Embedding** | Signed embedding requests, validate response signatures |
| **Cache Entries** | HMAC signature on (key, value, timestamp, tenant_id) |
| **Tenant Keys** | Per-tenant HMAC key, rotate every 90 days |
| **Audit Log** | Append-only log (S3 + Glacier), includes (tenant, key hash, hit/miss, timestamp) |
| **Access Control** | RBAC: read-cache, write-cache, invalidate-cache, read-metrics |

**RBAC Roles**:

| Role | Permissions | Use Case |
|------|-------------|----------|
| **CacheReader** | Get from L1/L2 | Application service |
| **CacheWriter** | Write to L1/L2, backfill | LLM response handler |
| **CacheInvalidator** | Delete keys, flush namespace | Admin, data update pipeline |
| **CacheAuditor** | Read-only access to logs, metrics | Security, compliance |

### PII Pipeline for Cached Responses

```
Request → PII Detection (Presidio, AWS Comprehend)
           ↓
       Redaction (replace with [REDACTED])
           ↓
       Cache Redacted Response
           ↓
       Audit Log (original query hash, redacted fields, timestamp)
```

**PII detection tools**:
- **Presidio** (Microsoft): Open-source, regex + NER
- **AWS Comprehend**: Managed, 12+ PII types
- **Google DLP**: GDPR compliance, 150+ info types

**Trade-off**: PII detection adds 10-50ms latency. Skip for non-sensitive use cases.

### Durable Execution Patterns

For long-running agentic workflows, checkpoint cache state:

**Temporal + Kafka**:
```
Workflow:
  1. Load from cache (checkpoint 1)
  2. Call LLM (checkpoint 2)
  3. Backfill cache (checkpoint 3)

Each checkpoint → Kafka event
On crash → Replay from last checkpoint
```

**Checkpoint table**:

| Checkpoint | Self-Hosted | Hosted API |
|-----------|-------------|------------|
| **Cache Load** | Redis GET + SHA-256 hash | API call with cache_control |
| **LLM Call** | vLLM request ID + output | OpenAI request_id + response |
| **Cache Write** | Redis SETEX timestamp | N/A (provider-managed) |

### Non-Functional Requirements (NFRs)

| NFR | Target | Measurement |
|-----|--------|-------------|
| **Availability** | 99.9% (3 nines) | Uptime monitoring (Pingdom, Datadog) |
| **RPO** (Recovery Point Objective) | < 5 minutes | Redis AOF persistence, backup frequency |
| **RTO** (Recovery Time Objective) | < 1 minute | Failover time (Redis Sentinel, Cluster) |
| **Compliance** | GDPR, HIPAA, SOC2 | PII redaction, audit logs, encryption |
| **Security** | Zero-trust, tenant isolation | mTLS, HMAC, RBAC |
| **Correctness** | No stale data > TTL | Cache invalidation on data update |

### Replica Locality & Rolling Deploys

**Cold start problem**: New replica has empty cache → high latency spike.

**Solutions**:
1. **Pre-warm**: Fetch top-k common prefixes on startup
2. **Rolling deploy**: Deploy 1 replica at a time, wait for cache warm-up
3. **Sticky routing**: Route users to same replica (session affinity)

**TTL vs Stream Length**:
- Short TTL (5min): Good for bursty traffic, frequent updates
- Long TTL (1hr): Good for stable content, lower write cost
- Stream length: Hosted cache eviction may be triggered by total cached tokens across all users, not just TTL

## Production Patterns & Best Practices

### Cache Hit Rate Monitoring

| Metric | Target | Alert Threshold | Action |
|--------|--------|----------------|--------|
| **L1 Hit Rate** | > 40% | < 30% | Increase TTL, check prefix stability |
| **L2 Hit Rate** | > 20% | < 10% | Lower threshold (0.90), check embedding quality |
| **Overall Hit Rate** | > 60% | < 50% | Audit query patterns, optimize prefix design |
| **Cache Size** | < 80% Redis memory | > 90% | Scale up, tune eviction policy |
| **Eviction Count** | < 5% of writes | > 10% | Increase memory, lower TTL |
| **p99 Latency (L1)** | < 10ms | > 50ms | Check Redis load, network latency |
| **p99 Latency (L2)** | < 50ms | > 200ms | Optimize HNSW index, check embedding API |

### Latency SLA Targets

| Tier | p50 | p95 | p99 | Budget Allocation |
|------|-----|-----|-----|-------------------|
| **L1 Exact** | < 2ms | < 5ms | < 10ms | Redis latency < 5ms |
| **L2 Semantic** | < 10ms | < 30ms | < 50ms | Embed (20ms) + HNSW (10ms) |
| **L3 Prefix (hot)** | < 100ms | < 200ms | < 500ms | Provider API latency |
| **L3 Miss** | 1s-3s | 3s-8s | 8s-15s | Full LLM generation |

### Decision Matrix by Traffic Pattern

| Pattern | Recommended Tiers | Reasoning |
|---------|------------------|-----------|
| **Identical queries (chatbot FAQ)** | L1 exact only | 90% cost savings, < 5ms latency |
| **Similar queries (support tickets)** | L1 + L2 semantic | Handles paraphrasing, 60-80% savings |
| **Shared prefix (multi-tenant agents)** | L1 + L3 prefix | Reuse tool defs, 80% savings on prefill |
| **Long-context RAG** | L3 prefix + skip RAG | Include full corpus in cacheable prefix |
| **Low traffic (< 100 req/day)** | L3 hosted only | Simplicity > cost optimization |
| **High traffic (> 10k req/day)** | L1 + L2 + L3 self-hosted | Full control, lowest per-request cost |

### Tooling Landscape

| Tool | Type | Best For | Notable Feature |
|------|------|----------|-----------------|
| **vLLM** | Self-hosted inference | High throughput, prefix caching | APC, PagedAttention, FP8 KV quant |
| **SGLang** | Self-hosted inference | Shared prefixes, RadixAttention | 5x throughput with overlap |
| **TensorRT-LLM** | Self-hosted inference | NVIDIA GPUs, long context | 45 GiB KV offload |
| **TGI (HuggingFace)** | Self-hosted inference | HF ecosystem integration | Prefix caching, 13x speedup claims |
| **LMCache** | Distributed KV cache | Multi-node sharing | Centralized metadata, cross-instance cache |
| **CacheBlend** | Multi-tier cache | Hybrid exact + semantic | Pluggable backends |
| **Mooncake** | KV cache store | Persistent storage | S3-backed, checkpoint/restore |
| **Redis Stack** | L1/L2 store | In-memory + vector | HNSW, native VADD/VSIM |
| **GPTCache** | Semantic cache framework | Quick semantic cache setup | Supports Redis, Faiss, Milvus |
| **RedisVL** | Vector search | Redis vector library | Python SDK for HNSW |
| **LangCache** | Application cache | LangChain integration | Semantic + exact caching |

### Design Checklist

Before implementing caching:
- [ ] Identify stable vs volatile prompt sections
- [ ] Measure baseline cache hit rate (log identical queries)
- [ ] Choose cache tiers based on traffic pattern (see decision matrix)
- [ ] Set TTL based on data freshness requirements
- [ ] Implement stampede prevention (XFetch recommended)
- [ ] Add circuit breakers for cache failures
- [ ] Configure tenant isolation (namespace + HMAC salt)
- [ ] Set up monitoring (hit rate, latency, eviction count)
- [ ] Define SLAs (p95 latency targets per tier)
- [ ] Plan for cache invalidation (on data updates)
- [ ] Add PII redaction if handling sensitive data
- [ ] Document cache key schema (for debugging)
- [ ] Load test with realistic traffic (simulate stampedes)
- [ ] Set up audit logging (cache access, PII redaction)
- [ ] Review security (timing attacks, tenant isolation)

### Observability Stack

**Metrics** (Prometheus):
```promql
# Hit rate by tier
sum(rate(cache_hits{tier="l1"}[5m])) / sum(rate(cache_requests{tier="l1"}[5m]))

# p99 latency
histogram_quantile(0.99, rate(cache_latency_seconds_bucket[5m]))

# Eviction rate
rate(cache_evictions_total[5m])

# Memory usage
cache_memory_bytes / cache_memory_limit_bytes
```

**Alerts** (Alertmanager):
```yaml
- alert: LowCacheHitRate
  expr: cache_hit_rate < 0.30
  for: 10m
  annotations:
    summary: "Cache hit rate below 30% for 10 minutes"

- alert: HighCacheLatency
  expr: histogram_quantile(0.99, cache_latency_seconds_bucket) > 0.050
  for: 5m
  annotations:
    summary: "p99 cache latency above 50ms"

- alert: CacheMemoryHigh
  expr: cache_memory_bytes / cache_memory_limit_bytes > 0.90
  for: 5m
  annotations:
    summary: "Cache memory usage above 90%"
```

**Dashboards** (Grafana):
- Hit rate by tier (L1, L2, L3) over time
- Latency heatmap (p50, p95, p99)
- Cache size and eviction count
- Cost savings (estimated based on hit rate × pricing)
- Top cache keys (most frequently accessed)
- Stampede detection (concurrent requests for same key)

## Interview Q&A

### Q1: Explain the difference between KV cache, prefix cache, and semantic cache.

**Answer**:

**KV cache** is the fundamental mechanism: during prefill, we compute key-value tensors for each token and store them in GPU memory. This is per-request and enables efficient decoding (no need to recompute attention for previous tokens).

**Prefix cache** (also called Automatic Prefix Caching or APC) reuses KV cache across requests. If two requests share a common prefix (e.g., same system prompt), we compute KV once and reuse it. This is exact matching - the prefix must be byte-for-byte identical.

**Semantic cache** works at the application level. It embeds the query, searches for semantically similar past queries (using vector search with cosine similarity), and if found above a threshold (e.g., 0.95), returns the cached response without calling the LLM. This handles paraphrasing but risks false positives.

**When to use each**:
- KV cache: Always (fundamental to LLM inference)
- Prefix cache: When you have stable system prompts or tool definitions (80-90% cost savings on prefill)
- Semantic cache: FAQ systems, customer support where queries are paraphrased (60-80% savings)

### Q2: How would you design a cache system for a multi-tenant chatbot with 10k requests/day?

**Answer**:

I'd use a three-tier cache:

**L1: Exact match (Redis)**
- Hash (prompt + model + tenant_id) with HMAC salt for isolation
- TTL: 1 hour
- Store final LLM responses
- Expected: 40-50% hit rate for repeated identical queries

**L2: Semantic match (Redis + HNSW)**
- Embed query with text-embedding-3-small
- Search HNSW index for similar queries (cosine > 0.95)
- Expected: 15-20% hit rate for paraphrased queries

**L3: Prefix cache (Anthropic hosted)**
- Mark system prompt + tool definitions as cacheable (probably 2k-5k tokens)
- Let Anthropic handle KV caching
- 5-minute TTL, 1.25x write / 0.1x read pricing

**Tenant isolation**:
- Use per-tenant HMAC salt for cache keys
- Separate Redis namespaces: `tenant:{tenant_id}:cache:{key_hash}`
- Audit log all cache accesses

**Stampede prevention**:
- XFetch pattern: first request fetches, others wait
- TTL jitter: ±10% random variation

**Monitoring**:
- Prometheus metrics: hit_rate, latency_p99, eviction_count
- Alerts: hit_rate < 30%, latency > SLA
- Grafana dashboard: hit rate by tier, cost savings estimate

**Cost estimate** (using Claude 3.5 Sonnet):
- No cache: 10k × (5k tokens × $3/1M) = $150/day
- With L1+L2+L3 at 65% hit rate: ~$52/day (65% savings)

### Q3: Your cache hit rate dropped from 60% to 20% overnight. How do you debug?

**Answer**:

**Step 1: Check metrics by tier**
- Did L1, L2, or L3 hit rate drop? This narrows the issue.
- L1 drop: Prefix stability issue (dynamic content moved to front?)
- L2 drop: Embedding service down? Threshold too high?
- L3 drop: Provider TTL expired? New model version?

**Step 2: Sample recent queries**
- Pull last 1000 requests from logs
- Check: Are prompts still identical (L1)? Are they still similar (L2)?
- Look for: New query patterns, changed prompt template, randomness added to prefix

**Step 3: Check TTLs and evictions**
- Redis: `INFO stats` → evicted_keys
- If evictions spiked, memory limit hit → scale up or reduce TTL
- Check provider cache TTL (OpenAI 5min, Anthropic 5min/1hr)

**Step 4: Inspect a miss**
- Take a query that should hit but missed
- Hash it, check if key exists in Redis
- If exists: Was it evicted? Check timestamp vs TTL
- If not exists: Was it ever written? Check backfill logs

**Step 5: Recent changes**
- Did we deploy a new prompt template? (This invalidates all L1/L3 cache)
- Did we change the cache key schema? (Old keys no longer match)
- Did traffic pattern shift? (New users, different queries)

**Common root causes**:
1. Prompt template changed (added timestamp, random seed to prefix)
2. Redis memory exhausted (eviction storm)
3. Provider cache expired (5-min TTL, bursty traffic means rewrites)
4. Deployment without pre-warming (new replicas have empty cache)

**Fix**:
- If (1): Revert to stable prefix design
- If (2): Scale Redis, tune eviction policy
- If (3): Switch to 1-hour Anthropic cache if acceptable
- If (4): Pre-warm new replicas before routing traffic

### Q4: Explain PagedAttention and RadixAttention. When would you choose one over the other?

**Answer**:

**PagedAttention** (vLLM, SOSP 2023):
- Treats KV cache like virtual memory with paging
- KV blocks (size 16 tokens) stored non-contiguously in GPU memory
- Eliminates fragmentation (HuggingFace wastes ~40% memory due to over-allocation)
- 2-4x throughput improvement vs naive KV management
- Limitation: No automatic prefix sharing across requests

**RadixAttention** (SGLang):
- Builds on PagedAttention + adds radix tree for prefix sharing
- Each node in tree = cached KV block
- New request: traverse tree, find longest matching prefix, reuse those KV blocks
- 5x throughput with high prefix overlap (e.g., all requests share same 8k-token system prompt)
- Limitation: LPM (Longest Prefix Match) lookup degrades at ~128 queue depth

**Benchmarks**:
- SGLang: ~16,200 tok/s with high prefix overlap
- vLLM APC: ~12,500 tok/s (same workload)
- vLLM no cache: ~8,000 tok/s

**When to use**:
- **PagedAttention (vLLM APC)**: 
  - Low prefix overlap (diverse queries)
  - Need hash-based caching (better for multi-tenant with namespace isolation)
  - Queue depth > 128 (RadixAttention degrades)
  
- **RadixAttention (SGLang)**:
  - High prefix overlap (shared system prompt across many users)
  - Low queue depth (< 100 concurrent requests)
  - Latency-sensitive (longest prefix match reduces TTFT)

**Hybrid approach**: Use SGLang for high-overlap workloads, fallback to vLLM for diverse queries or high queue depth.

### Q5: How do you handle cache invalidation when underlying data changes?

**Answer**:

**Strategies**:

**1. TTL-based invalidation** (simplest):
- Set short TTL (5-15 minutes) for data that changes
- Trade-off: May serve stale data briefly, but simple to implement
- Good for: Non-critical staleness (product catalog updates)

**2. Explicit invalidation**:
- On data update, delete affected cache keys
- Requires mapping data IDs to cache keys
- Example: User profile updated → delete `cache:user:{user_id}:*`
- Good for: Critical freshness (user permissions, account balance)

**3. Versioned cache keys**:
- Include data version in cache key: `cache:v{version}:{prompt_hash}`
- On update, increment version → old keys naturally unused
- Trade-off: No cleanup of old versions (relies on LRU eviction)
- Good for: Immutable data with clear versions

**4. Event-driven invalidation**:
- Data update triggers event (Kafka, SQS)
- Cache service subscribes, invalidates on event
- Good for: Distributed systems, microservices

**5. Stale-While-Revalidate**:
- Serve stale cached response
- Trigger background refresh
- Next request gets fresh data
- Good for: Non-critical staleness, high availability

**For LLM caching specifically**:

**Hosted prompt cache** (OpenAI, Anthropic):
- Can't manually invalidate (provider-managed)
- Rely on TTL expiration (5min-1hr)
- If data changes, accept stale responses until TTL expires OR change the cacheable prefix (add version marker)

**Self-hosted prefix cache** (vLLM, SGLang):
- Can flush cache manually: restart service or API call
- For partial invalidation: version cache keys by data timestamp

**Semantic cache**:
- Invalidate by query pattern: delete all embeddings similar to "product X"
- Requires reverse lookup (embed query, find neighbors, delete)

**Example**:
```python
# Product catalog updated
def on_product_update(product_id):
    # Invalidate L1 exact matches
    redis.delete(f"cache:product:{product_id}:*")
    
    # Invalidate L2 semantic matches
    query_embedding = embed(f"Tell me about product {product_id}")
    similar_keys = vector_search(query_embedding, threshold=0.90)
    for key in similar_keys:
        redis.delete(key)
    
    # L3 prefix cache: Can't invalidate hosted cache
    # Option: Add product version to cacheable prefix
    # "Product catalog version: {version}" in system prompt
```

### Q6: What are the security risks of caching LLM responses, and how do you mitigate them?

**Answer**:

**Risk 1: Timing side channels**
- **Attack**: Measure TTFT variance to infer if a prompt prefix is cached
- **Examples**: KVGov (ICML 2024), PROMPTPEEK, InputSnatch, CVE-2025-46570
- **Mitigation**: 
  - Add constant-time padding (random delay to match cache miss latency)
  - Audit logs for suspicious timing probe patterns
  - SafeKV: Noise injection in timing signals

**Risk 2: Tenant isolation breach**
- **Attack**: Tenant A crafts request to read Tenant B's cached data
- **Scenario**: Shared Redis, no namespace isolation
- **Mitigation**:
  - Per-tenant HMAC salt for cache keys
  - Separate Redis namespaces: `tenant:{id}:cache:{key}`
  - Use hosted cache (OpenAI, Anthropic) with per-account isolation

**Risk 3: PII leakage**
- **Attack**: Sensitive data (SSN, credit card) cached and logged
- **Mitigation**:
  - PII detection pipeline (Presidio, AWS Comprehend)
  - Redact before caching: replace with `[REDACTED]`
  - Audit trail: log which fields redacted, when, by whom

**Risk 4: Stale permissions**
- **Attack**: User's access revoked, but cached response still served
- **Mitigation**:
  - Short TTL for permission-sensitive data (< 5 min)
  - Explicit invalidation on permission change
  - Include user permissions in cache key (invalidates on change)

**Risk 5: Hash collision attacks**
- **Attack**: Craft input to collide with existing cache key → wrong response served
- **Scenario**: Using xxhash (64-bit) with billions of requests
- **Mitigation**:
  - Use SHA-256 (256-bit, collision-resistant)
  - Monitor collision metrics
  - HMAC with secret salt (prevents attacker from pre-computing collisions)

**Risk 6: Prompt injection via cache**
- **Attack**: Inject malicious prompt, cache it, serve to other users
- **Scenario**: Semantic cache returns cached injection response for similar query
- **Mitigation**:
  - Input validation before caching
  - Prompt injection detection (LLM Guard, Rebuff)
  - Per-user semantic cache (no cross-user sharing)

**Zero-Trust Architecture**:
```
- Encryption: TLS in transit, at-rest (Redis 6+)
- Authentication: mTLS between services
- Authorization: RBAC (read, write, invalidate, audit)
- Signing: HMAC on (key, value, timestamp, tenant_id)
- Audit: Append-only log (S3 + Glacier)
```

### Q7: How do you calculate the break-even point for caching with 1.25x write / 0.1x read pricing?

**Answer**:

**Formula derivation**:

Let:
- Base cost per token: `C`
- Write multiplier: `W = 1.25`
- Read multiplier: `R = 0.1`
- Number of reads: `n`

**No cache**:
```
Total cost = (n + 1) × C  (initial request + n subsequent requests)
```

**With cache**:
```
Total cost = W × C + n × R × C  (1 write, n reads)
```

**Break-even** when costs are equal:
```
W × C + n × R × C = (n + 1) × C
W + n × R = n + 1
n × R - n = 1 - W
n × (R - 1) = 1 - W
n = (1 - W) / (R - 1)
n = (W - 1) / (1 - R)  (flip signs)
```

**Plug in values**:
```
n = (1.25 - 1) / (1 - 0.1)
n = 0.25 / 0.9
n = 0.278
```

Wait, this gives < 1, which seems wrong. Let me recalculate:

Actually, the correct comparison is:
- **No cache**: `n` requests × `C` per request = `n × C`
- **With cache**: First request pays `W × C`, next `(n-1)` requests pay `R × C` each
  ```
  Total = W × C + (n - 1) × R × C
  ```

Break-even:
```
W × C + (n - 1) × R × C = n × C
W + (n - 1) × R = n
W + n × R - R = n
W - R = n - n × R
W - R = n × (1 - R)
n = (W - R) / (1 - R)
```

**Plug in**:
```
n = (1.25 - 0.1) / (1 - 0.1)
n = 1.15 / 0.9
n = 1.28
```

**Interpretation**: After ~1.3 reads (so on the 2nd request), you break even. First read is slightly below break-even, second read starts saving money.

**Savings after N reads**:
```
No cache cost: n × C
Cache cost: W × C + (n - 1) × R × C
Savings: n × C - [W × C + (n - 1) × R × C]
       = n × C - W × C - (n - 1) × R × C
       = C × [n - W - (n - 1) × R]
       = C × [n - W - n × R + R]
       = C × [n × (1 - R) - W + R]
```

**Example** (n = 10 reads):
```
No cache: 10 × C
Cache: 1.25 × C + 9 × 0.1 × C = 1.25 × C + 0.9 × C = 2.15 × C
Savings: 10 × C - 2.15 × C = 7.85 × C (78.5% savings)
```

### Q8: When would you NOT use caching for LLMs?

**Answer**:

**1. Highly dynamic prompts (no reuse)**
- Every query is unique (e.g., creative writing, image generation prompts with random seeds)
- Cache hit rate would be < 5%
- Overhead of hashing + cache lookup exceeds benefit

**2. Real-time data requirements**
- Stock prices, breaking news, live sports scores
- Even 5-min TTL is too stale
- Explicit invalidation is complex (many data sources)

**3. Highly sensitive data (compliance risk)**
- HIPAA, PCI-DSS, GDPR with strict right-to-deletion
- Cached data is harder to guarantee deleted (distributed, replicated)
- Audit requirements exceed cost savings

**4. Low traffic (< 100 requests/day)**
- Fixed overhead of cache infrastructure (Redis, monitoring) exceeds savings
- Hosted LLM API without caching is simpler and cheaper

**5. Output must be non-deterministic**
- User expects different answer each time (e.g., "give me 3 random ideas")
- Caching violates expectation
- Alternative: Cache with low TTL (1 min) or per-user cache

**6. Latency is not a concern**
- Batch processing, offline analytics
- 5-second LLM latency is acceptable
- Simplicity of no cache > cost savings

**7. Semantic cache false positives too risky**
- Medical advice, legal guidance, financial recommendations
- "How do I treat a headache?" vs "How do I treat a migraine?" might be similar (cosine > 0.95) but require different answers
- Exact cache only, or skip semantic tier

**8. Provider caching is sufficient**
- Using OpenAI/Anthropic with stable prefix
- 90% of cost is in the prefix (tool definitions)
- Hosted cache handles it, no need for L1/L2

**Rule of thumb**: Use caching if:
- Hit rate > 30% (proven via logs)
- Cost savings > infrastructure overhead
- Staleness (TTL) is acceptable
- Security/compliance risks are manageable

Otherwise, stick with simple uncached LLM calls.

### Q9: How does GQA (Grouped Query Attention) reduce KV cache size?

**Answer**:

**Standard Multi-Head Attention** (e.g., GPT-3):
- 96 attention heads
- Each head has its own Key and Value projections
- KV cache stores: `n_layers × n_heads × d_head × seq_len × bytes`

For Llama-3.1-405B (no GQA):
```
Layers: 126
Heads: 128
d_head: 128
bytes: 2 (FP16)

KV per token = 2 × 126 × 128 × 128 × 2 = 8,257,536 bytes ≈ 8 MB/token
```

**GQA** (Grouped Query Attention, Llama-3, Mistral):
- Query still has 128 heads
- Key/Value reduced to 8 heads (16:1 ratio)
- Each KV head is shared across a group of Query heads

For Llama-3.1-405B (with GQA):
```
Layers: 126
KV heads: 8 (not 128)
d_head: 128
bytes: 2 (FP16)

KV per token = 2 × 126 × 8 × 128 × 2 = 516,096 bytes ≈ 504 KB/token
```

**Reduction**: 8 MB → 504 KB (16x smaller, matching the 128/8 = 16 ratio)

**Why it works**:
- Empirically, Key/Value projections are less critical than Query diversity
- 8 KV heads + 128 Query heads retain most of full multi-head quality
- Llama-3 papers show < 1% perplexity increase vs full MHA

**Multi-Latent Attention (MLA)** - DeepSeek-V3:
- Even more aggressive: 128 latent heads compressed to 1 effective KV head per layer
- 93.3% KV reduction vs full MHA
- 61 layers × 1 KV head × 128 d_head × 2 bytes = 70,272 bytes/token ≈ 69 KB

**Implications for caching**:
- GQA makes KV cache 8-16x smaller → can cache more requests in same GPU memory
- Hosted providers (OpenAI, Anthropic) likely use GQA or MLA → lower cache costs

### Q10: Explain the XFetch pattern for stampede prevention.

**Answer**:

**Problem**: Cache stampede (thundering herd)
- Popular cache key expires (TTL reached)
- 1000 concurrent requests arrive
- All see cache miss
- All call LLM simultaneously
- LLM overloaded → latency spike, potential outage

**XFetch solution**: Request coalescing
- First request to miss the cache becomes the "leader"
- Leader fetches from LLM
- Other requests (followers) wait for leader's result
- Leader writes result to cache
- All requests return the same result (no duplicate LLM calls)

**Implementation**:
```python
class XFetchCache:
    def __init__(self):
        self.cache = {}  # key -> (value, expiry)
        self.in_flight = {}  # key -> asyncio.Future
        self.lock = asyncio.Lock()
    
    async def get_or_fetch(self, key, fetch_fn, ttl):
        # Check cache
        if key in self.cache and not expired(self.cache[key]):
            return self.cache[key].value
        
        # Check if another request is already fetching
        async with self.lock:
            if key in self.in_flight:
                # Wait for in-flight request
                return await self.in_flight[key]
            
            # Become the leader
            future = asyncio.create_task(self._fetch(key, fetch_fn, ttl))
            self.in_flight[key] = future
        
        try:
            return await future
        finally:
            # Clean up
            async with self.lock:
                del self.in_flight[key]
    
    async def _fetch(self, key, fetch_fn, ttl):
        value = await fetch_fn()  # Call LLM
        self.cache[key] = CacheEntry(value, time.time() + ttl)
        return value
```

**Benefits**:
- 1000 concurrent requests → 1 LLM call (999 avoided)
- Latency: Followers wait for leader (adds queueing delay but avoids LLM overload)
- Cost: 1x LLM cost instead of 1000x

**Trade-offs**:
- **Latency**: Followers wait for leader (could be 1-3 seconds for LLM)
- **Single point of failure**: If leader request fails, all followers fail
- **Lock contention**: Lock acquisition for each miss (mitigated with fine-grained locks per key)

**Alternatives**:

| Approach | Pros | Cons |
|----------|------|------|
| **TTL Jitter** | Simple, no coordination | Mini-stampedes still possible |
| **Stale-While-Revalidate** | Low latency (serve stale) | Serves outdated data briefly |
| **XFetch** | Eliminates duplicate calls | Adds queueing latency |
| **Distributed Lock** (Redis SETNX) | Strong consistency | Lock contention, deadlock risk |
| **Probabilistic Early Refresh** | Spreads load | Complex to tune |

**Recommendation**: 
- Use XFetch for read-heavy, cost-sensitive workloads (LLM calls are expensive)
- Use Stale-While-Revalidate for latency-sensitive workloads (serve stale briefly is acceptable)

### Q11: You're serving 100k requests/day. Should you self-host (vLLM) or use hosted cache (OpenAI)?

**Answer**:

**Decision factors**:
1. Cost
2. Latency requirements
3. Customization needs
4. Team expertise
5. Traffic pattern

**Cost comparison** (Llama-3.1-405B, 5k prefix, 500 token completion):

**Hosted (Fireworks with cache)**:
```
Writes (20k/day, 80% hit): 20k × 5k × $3.75/1M = $375
Reads (80k/day): 80k × 5k × $0.30/1M = $120
Completion: 100k × 500 × $3/1M = $150
Daily: $645
Monthly: $19,350
```

**Self-hosted (vLLM on A100 80GB)**:
```
Instance: 8x A100 80GB = $24/hr = $576/day = $17,280/month
Baseline cheaper BUT:
- Need DevOps (0.5 FTE, $8k/month)
- Monitoring, storage, networking: $2k/month
Total: $17,280 + $8,000 + $2,000 = $27,280/month
```

Hosted wins at 100k/day.

**Break-even point**: ~500k requests/day
```
Hosted: $19,350 × 5 = $96,750/month
Self-hosted: $27,280/month (fixed)

Break-even when hosted cost > self-hosted:
Requests/day × $0.645 × 30 = $27,280
Requests/day = 1,410/day

Wait, that's wrong. Let me recalculate per-request cost:
Hosted: $645 / 100k = $0.00645/request
Self-hosted: $576/day for GPU (variable cost per request is near-zero)

At X requests/day:
Hosted = X × $0.00645
Self-hosted = $576 (fixed) + DevOps/monitoring

Hosted monthly: X × $0.00645 × 30
Self-hosted monthly: $576 × 30 + $10k = $17,280 + $10,000 = $27,280

Break-even:
X × $0.00645 × 30 = $27,280
X × $0.1935 = $27,280
X = 140,981 requests/month ≈ 4,700 requests/day
```

Actually, at 100k requests/day = 3M requests/month:
- Hosted: $19,350/month
- Self-hosted: $27,280/month

**Hosted still wins**.

Self-hosted wins when:
- Traffic > 5M requests/month (~167k/day)
- Or need custom models not available via API
- Or have latency requirements < 100ms (local GPU)
- Or regulatory constraints (data cannot leave VPC)

**Latency**:
- Self-hosted vLLM: 50-150ms TTFT (local GPU, optimized)
- Hosted (Fireworks, Anthropic): 150-500ms TTFT (API latency + network)

If p95 latency SLA < 200ms → self-hosted required.

**Recommendation for this scenario**:
- **Use hosted** (Fireworks or Anthropic) at 100k/day
- Monitor cost and latency
- Switch to self-hosted if:
  - Traffic grows > 200k/day
  - Latency SLA tightens (< 200ms p95)
  - Need custom fine-tuned model

### Q12: Design a cache invalidation strategy for a documentation chatbot where docs update weekly.

**Answer**:

**Requirements**:
- Docs update every Monday at 3 AM
- 50k tokens of documentation (fits in model context)
- 1,000 requests/day
- Acceptable staleness: up to 7 days

**Recommended strategy**: Versioned cache with weekly rotation

**Architecture**:

**1. Include docs version in cacheable prefix**:
```python
def build_prompt(query, docs_version):
    return {
        "model": "claude-3-5-sonnet-20241022",
        "system": [
            {
                "type": "text",
                "text": f"""
Documentation (version {docs_version}):
{load_docs(docs_version)}

You are a helpful assistant. Answer questions using the above docs.
                """,
                "cache_control": {"type": "ephemeral"}
            }
        ],
        "messages": [{"role": "user", "content": query}]
    }
```

**2. On docs update (Monday 3 AM)**:
```python
# Increment version
new_version = get_current_version() + 1
set_current_version(new_version)

# Old cache entries (version N) are now unused
# They'll naturally evict via LRU or TTL expiration
```

**3. Cache tiers**:

**L1: Exact match (Redis)**:
- Key includes docs version: `cache:v{version}:{query_hash}`
- TTL: 7 days (matches docs update cycle)
- On version increment, old keys (`v{N}`) are orphaned → evicted by LRU

**L2: Semantic match (Redis HNSW)**:
- Same versioning: `semantic:v{version}:{embedding_id}`
- TTL: 7 days
- On version increment, reindex embeddings for new version

**L3: Prefix cache (Anthropic)**:
- Docs version in system prompt → prefix hash changes → new cache entry
- Old prefix expires via Anthropic TTL (5 min or 1 hour)

**Cold start problem**: First request on Monday 3 AM after version bump:
- L1 miss (new version, no cached exact queries)
- L2 miss (need to reindex embeddings)
- L3 miss (new prefix, Anthropic hasn't cached yet)

**Solution**: Pre-warm cache
```python
# Monday 3 AM, after docs update
top_queries = get_top_100_queries_from_last_week()
for query in top_queries:
    # This triggers L1, L2, L3 cache population
    generate_response(query, docs_version=new_version)
```

**Cost** (Claude 3.5 Sonnet):
```
Pre-warm: 100 queries × 50k docs × $3.75/1M (cache write) = $18.75
Daily (after warm-up):
  - 70% hit rate (L1+L2)
  - 30% cache write (300 requests)
  
  Writes: 300 × 50k × $3.75/1M = $56.25
  Reads: 700 × 50k × $0.30/1M = $10.50
  Dynamic (query): 1000 × 100 × $3/1M = $0.30
  
  Daily: $67.05
  Weekly: $67.05 × 7 = $469.35
  + Pre-warm: $18.75
  = $488.10/week
```

**No cache** baseline:
```
1000 requests/day × (50k docs + 100 query) × $3/1M × 7 days
= 7,000 × 50,100 × $3/1M
= $1,052.10/week
```

**Savings**: ($1,052 - $488) / $1,052 = 53.6%

**Alternative**: Skip RAG, include full docs in every request
- Since docs (50k tokens) fit in context and are static for a week
- Just use L3 prefix cache (Anthropic)
- No L1/L2 needed
- Cost: Same as above (dominated by 50k × $3.75/1M cache write)

**Recommendation**: Versioned cache + pre-warming. Simple, cost-effective, no complex invalidation logic.

## System Design Scenarios

### Scenario A: Multi-Tenant Agentic Platform (8k Tool Definitions)

**Problem**:
Design caching for a multi-tenant platform where each tenant has access to 50 tools (~8,000 tokens of OpenAPI definitions). Traffic: 10,000 requests/day across 100 tenants.

**Requirements**:
- Tenant isolation (no cross-tenant cache reads)
- Cost optimization (tools are identical across tenants, should cache efficiently)
- Latency SLA: p95 < 500ms
- Security: Prevent timing side channels

**Analysis**:

**Traffic pattern**:
- Tools: 8,000 tokens (stable, identical across tenants)
- System prompt: 500 tokens (stable)
- User context: 200 tokens (tenant-specific)
- Query: 100 tokens (unique per request)

**Cache tiers**:

**L3: Hosted prefix cache (Anthropic 1-hour)**
- Cache tools + system prompt (8,500 tokens)
- TTL: 1 hour (tools rarely change)
- Pricing: 2.0x write, 0.1x read
- Per-tenant isolation (Anthropic handles this via API key)

**L1: Exact match (Redis)**
- Cache final LLM responses
- Key: `tenant:{tenant_id}:cache:sha256(prompt + model)`
- TTL: 1 hour
- Separate namespace per tenant for isolation

**L2: Skip semantic cache**
- Low value: Queries are diverse, paraphrasing unlikely in agent context

**Tenant isolation**:
```python
def build_cache_key(tenant_id, prompt, model):
    # Include tenant_id in key to prevent cross-tenant reads
    canonical = json.dumps({
        "tenant_id": tenant_id,
        "prompt": prompt,
        "model": model
    }, sort_keys=True)
    
    # HMAC with per-tenant salt
    tenant_salt = get_tenant_salt(tenant_id)
    return hmac.new(
        tenant_salt.encode(),
        canonical.encode(),
        hashlib.sha256
    ).hexdigest()
```

**Cost calculation** (Claude 3.5 Sonnet):

**Scenario 1: No cache**:
```
Total per request: (8,000 + 500 + 200 + 100) × $3/1M = $0.0267
Daily: 10,000 × $0.0267 = $267
Monthly: $267 × 30 = $8,010
```

**Scenario 2: L3 only (60% hit rate)**:
```
Cold (4,000 requests):
  Prefix write: 8,500 × $6/1M = $0.051
  Dynamic: 300 × $3/1M = $0.0009
  Per request: $0.0519
  Total: 4,000 × $0.0519 = $207.60

Warm (6,000 requests):
  Prefix read: 8,500 × $0.30/1M = $0.00255
  Dynamic: 300 × $3/1M = $0.0009
  Per request: $0.00345
  Total: 6,000 × $0.00345 = $20.70

Daily: $207.60 + $20.70 = $228.30
Monthly: $228.30 × 30 = $6,849
Savings: 14.5%
```

**Scenario 3: L1 + L3 (L1: 40% hit, L3: 80% hit on L1 miss)**:
```
L1 hit (4,000 requests): Redis cost ≈ $0
L1 miss + L3 warm (4,800 requests): $0.00345 × 4,800 = $16.56
L1 miss + L3 cold (1,200 requests): $0.0519 × 1,200 = $62.28

Daily: $16.56 + $62.28 = $78.84
Monthly: $78.84 × 30 = $2,365.20
Savings: ($8,010 - $2,365) / $8,010 = 70.5%
```

**Architecture**:

```
Request
  ↓
┌─────────────────────────────────────────┐
│ Cache-Aware Router                      │
│ - Tenant ID extraction                  │
│ - Rate limiting (per-tenant)            │
└─────────────────────────────────────────┘
  ↓
┌─────────────────────────────────────────┐
│ L1: Exact Match (Redis)                 │
│ - Namespace: tenant:{id}:cache:{hash}   │
│ - TTL: 1 hour                           │
│ - Hit? → Return (p50 < 5ms)             │
└─────────────────────────────────────────┘
  ↓ Miss
┌─────────────────────────────────────────┐
│ L3: Anthropic Prefix Cache              │
│ - Tools + system (8,500 tok) cacheable  │
│ - 1-hour TTL                            │
│ - Per-tenant API key (isolation)        │
└─────────────────────────────────────────┘
  ↓
┌─────────────────────────────────────────┐
│ Backfill L1                             │
│ - Write to tenant namespace             │
│ - Audit log: tenant, key, timestamp     │
└─────────────────────────────────────────┘
```

**Security measures**:
1. **Constant-time cache reads**: Add random 0-10ms delay to cache hits to prevent timing attacks
2. **Audit logging**: Log (tenant_id, cache_key_hash, hit/miss, timestamp) to S3
3. **RBAC**: Tenants can only read/write their namespace
4. **Rate limiting**: 1,000 requests/hour per tenant

**Decision matrix**:

| Requirement | Solution |
|-------------|----------|
| Cost optimization | L1 + L3 (70% savings) |
| Tenant isolation | Per-tenant Redis namespace + Anthropic API key |
| Latency SLA (p95 < 500ms) | L1 hit: < 5ms, L3 warm: < 300ms (within SLA) |
| Security | Constant-time, audit logs, RBAC |
| Scalability | Redis Cluster (horizontal scaling), Anthropic auto-scales |

**Monitoring**:
```promql
# Hit rate by tenant
sum(rate(cache_hits{tenant=~".*"}[5m])) by (tenant) / 
sum(rate(cache_requests{tenant=~".*"}[5m])) by (tenant)

# p95 latency by tier
histogram_quantile(0.95, 
  rate(cache_latency_seconds_bucket{tier=~"l1|l3"}[5m])
) by (tier)

# Cross-tenant cache access attempts (security)
sum(rate(cache_access_denied_total[5m]))
```

### Scenario B: FAQ Customer Support Bot (50k Requests/Day)

**Problem**:
Design caching for a customer support chatbot handling 50,000 requests/day. Common questions like "refund policy", "shipping time", "return process" are asked in many paraphrased forms.

**Requirements**:
- High cache hit rate (target: 70%+)
- Latency SLA: p95 < 200ms
- Cost-conscious (current uncached cost: $500/day)
- Accuracy: No false positive semantic matches (wrong answer)

**Analysis**:

**Traffic pattern**:
- 20 FAQ topics, each asked ~2,500 times/day in 50+ paraphrased forms
- Example: "How do I return an item?" = "return policy" = "I want a refund"
- System prompt: 2,000 tokens (FAQ context)
- Query: 50 tokens (average)

**Cache tiers**:

**L1: Exact match (Redis)**
- For truly identical queries (rare, ~10%)
- TTL: 24 hours
- Cost: Negligible (Redis only)

**L2: Semantic match (Redis + HNSW)**
- Embed query with text-embedding-3-small
- HNSW index for fast nearest neighbor search
- Threshold: 0.95 cosine similarity
- Expected hit rate: 60%

**L3: Hosted prefix cache (OpenAI GPT-4o)**
- Cache system prompt (2,000 tokens)
- 5-minute TTL (FAQ content rarely changes)

**Why semantic cache is critical here**:
- Paraphrasing is common: "How do I return?" vs "What's your return policy?"
- These should return same answer but exact cache would miss
- Semantic cache: cosine("How do I return?", "What's your return policy?") ≈ 0.97 → HIT

**Architecture**:

```
Request
  ↓
┌─────────────────────────────────────────┐
│ L1: Exact Match (Redis)                 │
│ - SHA-256 hash of query                 │
│ - Hit rate: ~10%                        │
│ - Latency: < 5ms                        │
└─────────────────────────────────────────┘
  ↓ Miss
┌─────────────────────────────────────────┐
│ L2: Semantic Match                      │
│ - Embed query (text-embedding-3-small)  │
│ - HNSW search (Redis Stack)             │
│ - Threshold: cosine > 0.95              │
│ - Hit rate: ~60%                        │
│ - Latency: < 50ms (embed 20ms + search) │
└─────────────────────────────────────────┘
  ↓ Miss
┌─────────────────────────────────────────┐
│ L3: OpenAI GPT-4o + Prefix Cache        │
│ - System prompt (2k tok) cacheable      │
│ - Hit rate on prefix: ~95% (stable)     │
│ - Latency: 100-300ms                    │
└─────────────────────────────────────────┘
  ↓
┌─────────────────────────────────────────┐
│ Backfill L1 + L2                        │
│ - Write exact match to L1               │
│ - Store embedding + response in L2      │
│ - XFetch to prevent stampede            │
└─────────────────────────────────────────┘
```

**Cost calculation** (GPT-4o):

**Scenario 1: No cache**:
```
Per request: (2,000 + 50) × $10/1M = $0.0205
Daily: 50,000 × $0.0205 = $1,025
Monthly: $1,025 × 30 = $30,750
```

**Scenario 2: L3 prefix cache only (no semantic)**:
```
Prefix write (5,000 requests, 5-min TTL): 
  5,000 × 2,000 × $12.50/1M = $125

Prefix read (45,000 requests):
  45,000 × 2,000 × $1.00/1M = $90

Dynamic (all):
  50,000 × 50 × $10/1M = $25

Daily: $125 + $90 + $25 = $240
Monthly: $240 × 30 = $7,200
Savings: 76.6%
```

**Scenario 3: L1 + L2 semantic + L3 (full stack)**:
```
L1 hit (5,000 requests): $0 (Redis cost negligible)

L2 hit (30,000 requests):
  Embedding lookup cost: 30,000 × $0.00002 = $0.60 (Redis HNSW search is free)
  
L2 miss (15,000 requests):
  Embed: 15,000 × (50 tok × $0.1/1M) = $0.075
  LLM call with L3:
    - Prefix write (1,500 @ 5-min TTL): 1,500 × 2,000 × $12.50/1M = $37.50
    - Prefix read (13,500): 13,500 × 2,000 × $1.00/1M = $27
    - Dynamic: 15,000 × 50 × $10/1M = $7.50
  
L2 backfill writes (15,000 new embeddings):
  Store: 15,000 × embedding storage cost ≈ $0 (Redis)

Daily: $0 + $0.60 + $0.075 + $37.50 + $27 + $7.50 = $72.68
Monthly: $72.68 × 30 = $2,180.40
Savings: ($30,750 - $2,180) / $30,750 = 92.9%
```

**Semantic cache accuracy**:
- **Risk**: False positive (cosine > 0.95 but different answer needed)
- **Example**: "How do I return shoes?" vs "How do I return electronics?" might be 0.96 similar but have different policies

**Mitigation**:
1. **Reranker**: After HNSW retrieval, rerank top-5 with cross-encoder
2. **Higher threshold**: 0.97 instead of 0.95 (fewer false positives, lower hit rate)
3. **Human-in-the-loop**: Flag ambiguous matches (0.93-0.97) for review
4. **Category tags**: Include product category in embedding: "return policy [shoes]"

**Comparison: Semantic vs Exact-Only**:

| Metric | Exact Only (L1 + L3) | Semantic (L1 + L2 + L3) |
|--------|----------------------|-------------------------|
| Hit Rate | 10% (L1 only) | 70% (L1 + L2) |
| Monthly Cost | $7,200 | $2,180 |
| p95 Latency | 200ms (mostly L3) | 50ms (mostly L2) |
| False Positives | 0% | < 1% (with reranker) |
| Complexity | Low | Medium |

**Recommendation**: Use L1 + L2 + L3 for FAQ use case
- 92.9% cost savings
- 70% hit rate
- False positive risk mitigated with reranker + threshold tuning

### Scenario C: Multi-Agent Research System (100 Parallel Agents)

**Problem**:
Design caching for a research system that spawns 100 agents in parallel to research a topic. Each agent has identical system prompt (5k tokens) + tools (10k tokens), but different sub-questions.

**Requirements**:
- Minimize redundant LLM calls (agents often ask similar questions)
- Low latency (all 100 agents complete within 30 seconds)
- Cost optimization (current run costs $50 per research task)
- Correctness (no stale data)

**Analysis**:

**Traffic pattern**:
- 100 agents spawn simultaneously
- Each agent: 15k token prefix (system + tools), 200 token query
- 30% of queries are similar across agents (e.g., "What is X?" asked by 10 agents)
- Single research task, no cross-task caching needed (TTL: 5 minutes)

**Cache tiers**:

**L3: Self-hosted prefix cache (SGLang RadixAttention)**
- Cache 15k prefix (shared across all 100 agents)
- RadixAttention automatically shares prefix
- Latency: < 100ms TTFT for cached prefix
- Throughput: 16,200 tok/s (handles 100 concurrent agents)

**L2: Semantic cache (in-memory HNSW)**
- For similar sub-questions across agents
- In-memory only (5-minute TTL, single task)
- Threshold: 0.93 (higher recall, some false positives OK for research)

**L1: Exact match (in-memory dict)**
- Hash-based, no Redis (single-task only)
- Detects duplicate questions

**Why self-hosted over hosted API**:
- 100 concurrent agents would hit rate limits on hosted APIs
- Need < 30 second total time → need high throughput
- SGLang: 16,200 tok/s can generate 100 × 500 tokens = 50k tokens in ~3 seconds
- Hosted API: Rate limits (e.g., 10k ITPM) would throttle

**Architecture**:

```
Research Task Start
  ↓
┌────────────────────────────────────────────┐
│ Spawn 100 Agents                           │
│ - Each agent: 15k prefix + unique query    │
│ - Concurrent execution                     │
└────────────────────────────────────────────┘
  ↓
┌────────────────────────────────────────────┐
│ L1: In-Memory Exact Cache                  │
│ - Dict: query_hash → response              │
│ - XFetch: Coalesce duplicate queries       │
│ - Hit: ~5% (few exact duplicates)          │
└────────────────────────────────────────────┘
  ↓ Miss
┌────────────────────────────────────────────┐
│ L2: In-Memory Semantic Cache               │
│ - FAISS HNSW index                         │
│ - Threshold: 0.93                          │
│ - Hit: ~25% (similar questions)            │
└────────────────────────────────────────────┘
  ↓ Miss
┌────────────────────────────────────────────┐
│ L3: SGLang RadixAttention                  │
│ - 15k prefix cached (first agent)          │
│ - Remaining 99 agents reuse prefix         │
│ - Latency: 50ms TTFT (cached) vs 500ms cold│
│ - Generate 500 tok response                │
└────────────────────────────────────────────┘
  ↓
┌────────────────────────────────────────────┐
│ Backfill L1 + L2                           │
│ - Store response in in-memory cache        │
│ - Embed and add to FAISS index             │
└────────────────────────────────────────────┘
  ↓
All 100 Agents Complete
```

**Cost calculation**:

**Hosted API (Anthropic, no cache)**:
```
100 agents × (15k prefix + 200 query) × $3/1M = $4.56 (prefill)
100 agents × 500 output × $15/1M = $0.75 (completion)
Total: $5.31 per research task
```

**Hosted API (Anthropic, with 1-hour cache)**:
```
First agent writes prefix: 15k × $6/1M = $0.09
99 agents read prefix: 99 × 15k × $0.30/1M = $0.45
All dynamic: 100 × 200 × $3/1M = $0.06
Completion: 100 × 500 × $15/1M = $0.75
Total: $0.09 + $0.45 + $0.06 + $0.75 = $1.35 per task
Savings: 74.6%
```

**Self-hosted (SGLang) + L1 + L2**:
```
GPU: A100 80GB = $3/hr
Research task: 30 seconds = $0.025

L1/L2 hits (30 agents): No LLM call (return cached)
L3 calls (70 agents):
  First: 15k prefill (cold) + 500 gen
  Remaining 69: 15k prefill (cached) + 500 gen
  
Throughput: 16,200 tok/s
  Cold prefill: 15k / 16,200 = 0.93 seconds
  Cached prefill: 15k / 16,200 × 0.2 (5x faster) = 0.19 seconds
  Generation: 500 / 16,200 × 100 (decode slower) = 3.1 seconds per agent
  
Parallel (100 agents, 10 concurrent): ~31 seconds total
Cost: $0.025 per task

Savings vs hosted no-cache: ($5.31 - $0.025) / $5.31 = 99.5%
```

**Latency breakdown**:

| Scenario | TTFT (first agent) | TTFT (subsequent agents) | Total time (100 agents) |
|----------|-------------------|-------------------------|-------------------------|
| Hosted, no cache | 1-2 seconds | 1-2 seconds | Rate limited, ~5 minutes |
| Hosted, prefix cache | 1 second | 200ms | Rate limited, ~2 minutes |
| Self-hosted, SGLang | 500ms (cold) | 50ms (cached) | 31 seconds (parallel) |

**Recommendation**: Self-hosted SGLang + in-memory L1/L2
- 99.5% cost savings
- Meets 30-second SLA
- No rate limit issues
- Fresh data (in-memory cache, 5-min TTL)

### Scenario D: Real-Time News Summarizer (High Freshness Requirement)

**Problem**:
Design caching for a news summarizer that pulls latest articles every 5 minutes and generates summaries. Users ask "What's happening in {topic}?" multiple times per day.

**Requirements**:
- Freshness: No stale data > 5 minutes
- Cost optimization (1,000 users, 20 queries/day each = 20k requests/day)
- Latency: p95 < 1 second
- Deduplication: Multiple users ask same question within 5-min window

**Analysis**:

**Traffic pattern**:
- Articles update every 5 minutes
- Users ask similar questions: "What's happening in tech?" during each 5-min window
- ~100 unique questions per 5-min window
- ~50 users ask each question → 50x duplication

**Cache tiers**:

**L1: Exact match with 5-min TTL (Redis)**
- Key: `cache:{topic}:{timestamp_5min_bucket}`
- TTL: 5 minutes (matches article freshness)
- Hit rate: ~98% (within 5-min window, queries are identical)

**L3: Hosted prefix cache (skip)**
- Articles change every 5 minutes → prefix unstable
- Not effective for this use case

**L2: Semantic cache (skip)**
- Exact questions are already cached via L1
- Semantic matching adds latency without benefit

**Unique approach: Time-bucketed cache keys**

```python
def get_cache_key(topic, timestamp):
    # Round timestamp to 5-min bucket
    bucket = (timestamp // 300) * 300  # 300 seconds = 5 min
    return f"cache:{topic}:{bucket}"

def generate_summary(topic):
    now = time.time()
    key = get_cache_key(topic, now)
    
    # Check cache
    cached = redis.get(key)
    if cached:
        return cached
    
    # Fetch latest articles (last 5 min)
    articles = fetch_articles(topic, since=now - 300)
    
    # Generate summary
    summary = llm.generate(f"Summarize: {articles}")
    
    # Cache for 5 minutes
    redis.setex(key, 300, summary)
    return summary
```

**Cost calculation** (GPT-4o):

**No cache**:
```
20,000 requests/day × 5,000 tok × $10/1M = $1,000/day
Monthly: $30,000
```

**With L1 (98% hit rate)**:
```
Cold (400 requests): 400 × 5,000 × $10/1M = $20
Warm (19,600 requests): Redis only ≈ $0

Daily: $20
Monthly: $20 × 30 = $600
Savings: 98%
```

**Stampede prevention**:
- First user in each 5-min window triggers LLM call
- Remaining 49 users wait (XFetch pattern)
- Total LLM calls: ~100 unique topics × 12 windows/hour × 24 hours = 28,800/day
- But with XFetch: Only ~400 actual calls (remaining served from cache)

**Architecture**:

```
User Request ("What's in tech news?")
  ↓
┌─────────────────────────────────────────┐
│ Time-Bucketed Cache Key                 │
│ - Round timestamp to 5-min bucket       │
│ - Key: cache:tech:1678900500            │
└─────────────────────────────────────────┘
  ↓
┌─────────────────────────────────────────┐
│ L1: Redis Exact Match                   │
│ - TTL: 5 minutes                        │
│ - XFetch: Coalesce concurrent requests  │
│ - Hit rate: 98%                         │
└─────────────────────────────────────────┘
  ↓ Miss (first request in 5-min window)
┌─────────────────────────────────────────┐
│ Fetch Articles (last 5 min)             │
│ - API call to news source               │
│ - Filter by topic                       │
└─────────────────────────────────────────┘
  ↓
┌─────────────────────────────────────────┐
│ LLM Summarization                       │
│ - GPT-4o, no prefix cache (unstable)    │
│ - 5,000 token context                   │
└─────────────────────────────────────────┘
  ↓
┌─────────────────────────────────────────┐
│ Cache with TTL                          │
│ - Write to Redis                        │
│ - TTL: 5 minutes (expires with bucket)  │
└─────────────────────────────────────────┘
```

**Monitoring**:
```promql
# Cache freshness (should align with 5-min buckets)
max(time() - cache_entry_timestamp_seconds) by (topic)

# Hit rate (target > 95%)
sum(rate(cache_hits[5m])) / sum(rate(cache_requests[5m]))

# Stampede detection (concurrent requests for same key)
sum(xfetch_wait_count) by (cache_key)
```

**Trade-offs**:

| Approach | Pros | Cons |
|----------|------|------|
| **Time-bucketed cache (chosen)** | 98% hit rate, guaranteed fresh | First request each bucket waits for LLM |
| **Continuous TTL** | Smoother experience | Risk of stale data (> 5 min) |
| **No cache** | Always fresh | 50x higher cost |
| **Pre-generate summaries** | Zero user latency | Waste if topic not requested |

**Recommendation**: Time-bucketed L1 cache with XFetch
- 98% cost savings
- Guaranteed < 5 min staleness
- Handles stampedes (50 users → 1 LLM call)
- Simple architecture (single Redis tier)

