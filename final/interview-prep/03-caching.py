"""
Caching in LLM Systems - Code Examples

Covers: KV cache fundamentals, prefix caching (APC, RadixAttention),
semantic caching, multi-tier cache architectures, cache-aware routing,
hosted prompt caching (OpenAI, Anthropic, Gemini), stampede prevention
(XFetch), cost analysis, tenant isolation, cache invalidation, and
interview Q&A examples.

Source: 03-caching.md
"""

# --- Shared imports across all code blocks ---
import asyncio
import hashlib
import hmac
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List


# --- Section: APC (Automatic Prefix Caching) - vLLM ---
# vLLM key parameters (CLI flags):
# --enable-prefix-caching
# --kv-cache-dtype fp8_e5m2  # Quantize to save memory
# --cache-salt <string>       # Namespace for multi-tenant
# --hash-algo sha256          # or sha256_cbor, xxhash


# --- Section: Hosted Prompt Cache - Example (Anthropic) ---

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


# --- Section: Skip RAG When Corpus Is Small ---

system = """
[Full docs]
[Full catalog]
You are a support agent. Use the above documentation to answer questions.
"""  # Mark cacheable, 80k tokens

# First request: pay 1.25x x 80k + 1x x query
# Subsequent: pay 0.1x x 80k + 1x x query (87% savings on context)


# --- Section: Production Multi-Tier Cache with Circuit Breakers (Grok Source) ---

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


# --- Section: Multi-Tier Cache with Request Coalescing (Opus Source) ---

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


# --- Section: Cache-Aware Router (Opus Source) ---

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

        Score = (cache_hit_score) x (1 - load)
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


# --- Section: Build Cacheable Prompt (Opus Source) ---

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
    [Tools] <- Cacheable
    [System] <- Cacheable
    [User context + query] <- Volatile
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


# --- Section: Q5 - Cache Invalidation When Underlying Data Changes ---

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


# --- Section: Q10 - XFetch Pattern for Stampede Prevention ---

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


# --- Section: Q12 - Cache Invalidation Strategy for Documentation Chatbot ---

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


# --- Section: Q12 - Version Increment on Docs Update ---

# Increment version
new_version = get_current_version() + 1
set_current_version(new_version)

# Old cache entries (version N) are now unused
# They'll naturally evict via LRU or TTL expiration


# --- Section: Q12 - Pre-Warm Cache After Docs Update ---

# Monday 3 AM, after docs update
top_queries = get_top_100_queries_from_last_week()
for query in top_queries:
    # This triggers L1, L2, L3 cache population
    generate_response(query, docs_version=new_version)


# --- Section: Scenario A - Multi-Tenant Agentic Platform (Tenant Isolation) ---

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


# --- Section: Scenario D - Real-Time News Summarizer (Time-Bucketed Cache) ---

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
