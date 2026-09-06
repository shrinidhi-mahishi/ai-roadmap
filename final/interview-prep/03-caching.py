"""
Caching in LLM Systems -- Interview Prep Code Snippets.

Covers the five cache layers (KV, prefix/APC, hosted prompt cache, semantic,
application), prompt caching patterns for Anthropic/OpenAI, semantic cache with
embeddings, KV cache memory calculations, cache warming strategies, stampede
prevention, invalidation patterns, and cost savings calculators.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import random
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# --- Prompt Caching: Anthropic/OpenAI Patterns ---

@dataclass(frozen=True)
class PromptParts:
    """Structured prompt parts for cache-aware assembly.

    Key design rule: stable content FIRST, volatile content LAST.
    Tools, system prompts, few-shots BEFORE the breakpoint.
    User queries, timestamps, per-request IDs AFTER it.

    NEVER interpolate datetime.now() or UUIDs into the system prompt --
    that invalidates the entire prefix cache on every request.
    """
    model: str
    tools: list              # Tool definitions (stable)
    system: str              # System prompt (stable)
    few_shots: list = field(default_factory=list)  # Few-shot examples (stable)
    user_message: str = ""   # User query (volatile -- goes AFTER breakpoint)


def canonical_json(obj) -> str:
    """Deterministic JSON for cache key computation.

    Sort keys, minimal separators. Tool order must be stable --
    tool reorder changes the prefix hash and busts the cache.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def build_anthropic_prompt(parts: PromptParts) -> dict:
    """Build an Anthropic API request with cache_control breakpoints.

    Anthropic render order: tools -> system -> messages.
    Up to 4 cache_control breakpoints allowed.

    Writes ONLY at the breakpoint. Reads look back at most 20 content blocks.
    Below the floor (512-4096 tokens depending on model): SILENT NO-OP
    (no error, cache fields = 0). Haiku 4.5 floor is 4096.

    Cache read refreshes TTL at no cost. Continuous traffic keeps
    5-min cache warm indefinitely.
    """
    return {
        "model": parts.model,
        "tools": parts.tools,
        "system": [
            {
                "type": "text",
                "text": parts.system,
                # Breakpoint: everything up to here is cached
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            # Few-shots before the user message (also cacheable if stable)
            *[
                {"role": ex["role"], "content": ex["content"]}
                for ex in parts.few_shots
            ],
            # User message is AFTER the breakpoint -- volatile, not cached
            {"role": "user", "content": parts.user_message},
        ],
    }


def build_openai_prompt(parts: PromptParts) -> dict:
    """Build an OpenAI GPT-5.6+ request with explicit breakpoints.

    OpenAI implicit: breakpoint at end of latest eligible user/tool message.
    Implicit mode caches the volatile latest message at 1.25x -- wasteful.

    mode=explicit (recommended): up to 4 breakpoints, you control what's cached.
    Minimum: 1,024 tokens for GPT-5.6+.

    Routing: ~15 RPM per prefix/key before overflow.
    Above ~15 RPM, shard prompt_cache_key across replicas.
    """
    return {
        "model": parts.model,
        "tools": parts.tools,
        "messages": [
            {
                "role": "system",
                "content": parts.system,
            },
            *[
                {"role": ex["role"], "content": ex["content"]}
                for ex in parts.few_shots
            ],
            {"role": "user", "content": parts.user_message},
        ],
        # Explicit breakpoints control what gets cached
        "prompt_cache_options": {"mode": "explicit"},
    }


def prewarm_cache(parts: PromptParts) -> dict:
    """Pre-warm a prompt cache before fan-out.

    Anthropic: N identical prefixes on a cold cache = N writes at 1.25x
    because the entry exists only AFTER the first response begins.

    Serialize a max_tokens: 0 pre-warm, THEN fan out.
    This is the stampede prevention pattern for hosted caches.
    """
    return {
        "model": parts.model,
        "system": [
            {
                "type": "text",
                "text": parts.system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": "warmup"}],
        "max_tokens": 0,  # Pre-warm only, no generation
    }


# --- Semantic Cache with Embeddings ---

@dataclass
class SemanticCacheEntry:
    """A cached response keyed by embedding similarity."""
    query: str
    response: str
    embedding: list[float]
    tenant_id: str
    model: str
    created_at: float
    ttl_s: float = 300.0  # 5 minutes default


class SemanticCache:
    """Semantic cache: embed the query, HNSW kNN, threshold, return prior answer.

    A HIT SKIPS THE LLM ENTIRELY -- both the win and the danger.

    False positive risk: "return policy for electronics" matches
    "return policy for clothing" at threshold 0.85.
    Start cosine similarity 0.88 for FAQ, lower to 0.84 if paraphrases miss.

    NEVER semantic-cache:
    - Tool-using or agent loops (live state)
    - Regulated traffic (financial, medical)
    - Personalized requests ("what's my balance?")
    - Creative generation
    - Multi-turn conversations

    InputSnatch: semantic caches leaked legal-domain prompts at 43-100% ASR.
    """
    def __init__(self, threshold: float = 0.90):
        self.threshold = threshold
        self.entries: list[SemanticCacheEntry] = []

    def _embed(self, text: str) -> list[float]:
        """Stub: embed text for similarity matching.

        Production: use BGE-M3 (512-dim) for latency or
        text-embedding-3-large (3072-dim) for quality.
        """
        h = hashlib.sha256(text.encode()).hexdigest()
        return [int(h[i:i+2], 16) / 255.0 for i in range(0, 16, 2)]

    def _cosine_sim(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na > 0 and nb > 0 else 0.0

    def get(self, query: str, tenant_id: str, model: str) -> Optional[str]:
        """Look up a semantically similar cached response.

        Must filter by tenant_id AND model -- missing tenant TAG = cross-talk.
        In production: Redis FT.SEARCH with tenant/model/policy as TAG filters
        in the SAME query as the kNN vector search.
        """
        q_emb = self._embed(query)
        now = time.time()
        best_match = None
        best_sim = 0.0

        for entry in self.entries:
            # Tenant + model filter (mandatory -- omitting = cross-talk)
            if entry.tenant_id != tenant_id or entry.model != model:
                continue
            # TTL check
            if now - entry.created_at > entry.ttl_s:
                continue

            sim = self._cosine_sim(q_emb, entry.embedding)
            if sim >= self.threshold and sim > best_sim:
                best_sim = sim
                best_match = entry

        return best_match.response if best_match else None

    def put(self, query: str, response: str, tenant_id: str, model: str,
            ttl_s: float = 300.0):
        """Store a response in the semantic cache.

        Scan response for PII BEFORE caching -- a false-positive semantic hit
        returns cached text verbatim. Never store raw PII in semantic answers.
        """
        emb = self._embed(query)
        self.entries.append(SemanticCacheEntry(
            query=query, response=response, embedding=emb,
            tenant_id=tenant_id, model=model,
            created_at=time.time(), ttl_s=ttl_s,
        ))


# --- KV Cache Memory Calculation ---

def kv_cache_memory_per_token(
    n_layers: int,
    n_kv_heads: int,
    d_head: int,
    dtype_bytes: int = 2,  # BF16 = 2 bytes
) -> dict:
    """Calculate KV cache memory per token.

    Formula per token per layer (BF16): 2 * n_kv_heads * d_head * 2 bytes
    - Factor of 2 for K and V tensors
    - GQA (Grouped Query Attention): 64 Q / 8 KV heads = 8x KV reduction vs MHA
    - MLA (Multi-Head Latent Attention, DeepSeek-V2): 93.3% KV reduction vs MHA

    Key numbers:
    - Llama-3.1-405B (GQA):  ~516 KB/token
    - Qwen-2.5-72B (GQA):   ~328 KB/token
    - DeepSeek-V3 (MLA):    ~70 KB/token
    - Llama-3.1-8B (GQA):   ~128 KB/token (32k context = ~4 GB of KV)
    """
    # Per token, per layer
    bytes_per_token_per_layer = 2 * n_kv_heads * d_head * dtype_bytes
    # Across all layers
    bytes_per_token = bytes_per_token_per_layer * n_layers
    kb_per_token = bytes_per_token / 1024

    return {
        "bytes_per_token_per_layer": bytes_per_token_per_layer,
        "bytes_per_token": bytes_per_token,
        "kb_per_token": round(kb_per_token, 3),
        "mb_per_1k_tokens": round(kb_per_token * 1000 / 1024, 2),
    }


def kv_cache_for_prefix(
    prefix_tokens: int,
    kb_per_token: float,
    num_tenants_sharing: int = 1,
) -> dict:
    """Calculate total KV cache memory for a shared prefix.

    APC (Automatic Prefix Caching) shares prefix KV across requests
    with the same salt. 32 same-salt tenants share 4.13 GB once, not x32.
    But HMAC-per-tenant salt makes it x32 (security trade-off).

    Worked example: 8k-token tools prefix on Llama-405B:
    8000 x 516.096 KB = ~4.13 GB of KV for that prefix alone.
    Same prefix on DeepSeek-V3 MLA: ~0.56 GB.
    """
    total_kb = prefix_tokens * kb_per_token
    total_gb = total_kb / (1024 * 1024)

    # With prefix sharing (APC/RadixAttention): stored once
    shared_gb = total_gb
    # Without sharing (per-tenant salt): stored N times
    isolated_gb = total_gb * num_tenants_sharing

    return {
        "prefix_tokens": prefix_tokens,
        "total_gb_shared": round(shared_gb, 3),
        "total_gb_isolated": round(isolated_gb, 3),
        "savings_with_sharing": f"{round((1 - 1/max(num_tenants_sharing, 1)) * 100, 1)}%",
    }


# Well-known model KV configurations for interview reference
MODEL_KV_CONFIGS = {
    "llama-3.1-8b": {"n_layers": 32, "n_kv_heads": 8, "d_head": 128, "label": "GQA 32L/8KV"},
    "llama-3.1-70b": {"n_layers": 80, "n_kv_heads": 8, "d_head": 128, "label": "GQA 80L/8KV"},
    "llama-3.1-405b": {"n_layers": 126, "n_kv_heads": 8, "d_head": 128, "label": "GQA 126L/8KV"},
    "qwen-2.5-72b": {"n_layers": 80, "n_kv_heads": 8, "d_head": 128, "label": "GQA 80L/8KV"},
    "deepseek-v3": {"n_layers": 61, "n_kv_heads": 1, "d_head": 288, "label": "MLA (93.3% KV reduction)"},
}


# --- Cache Warming Strategy ---

@dataclass
class CacheWarmingPlan:
    """Plan for warming caches on deployment or failover.

    Hosted cache is ephemeral: no dump API. Failover region = cold.
    RTO = time to re-warm (one TTL window of write SKUs).

    Regional processing: OpenAI caches cannot cross regional boundaries.
    Anthropic/hosted KV have no dump API.
    """
    prefixes_to_warm: list[dict]    # List of {key, prompt_parts, priority}
    warm_concurrency: int = 4       # Parallel warm requests
    warm_timeout_s: float = 30.0    # Per-request timeout


def create_warming_plan(
    stable_prefixes: list[PromptParts],
    priorities: Optional[list[int]] = None,
) -> CacheWarmingPlan:
    """Create a cache warming plan for deployment.

    Order: warm highest-priority (highest-traffic) prefixes first.
    Use max_tokens=0 for Anthropic to avoid generation cost.
    Single-flight the warm requests to prevent stampede.
    """
    if priorities is None:
        priorities = list(range(len(stable_prefixes)))

    items = sorted(
        zip(priorities, stable_prefixes),
        key=lambda x: x[0],
        reverse=True,  # Highest priority first
    )

    return CacheWarmingPlan(
        prefixes_to_warm=[
            {
                "priority": p,
                "key": hashlib.sha256(
                    canonical_json({"model": parts.model, "system": parts.system}).encode()
                ).hexdigest()[:16],
                "model": parts.model,
                "warm_request": prewarm_cache(parts),
            }
            for p, parts in items
        ]
    )


# --- Cache Invalidation Patterns ---

class CacheInvalidationStrategy:
    """Cache invalidation patterns for LLM systems.

    The five failure modes to know:
    1. Prefix thrash: timestamp in cached span -> infinite 1.25x writes
    2. Sub-floor silent no-op: prefix below minimum -> no error, just no caching
    3. Stale tool/schema: 1h cached tools vs new runtime schema
    4. Rolling-deploy cold: new pods empty, TTFT + write spike
    5. Region failover "breaks" cache: hosted KV cannot cross regions
    """

    @staticmethod
    def ttl_with_jitter(base_ttl_s: float, jitter_pct: float = 0.2) -> float:
        """TTL with jitter to prevent stampede.

        Spreads expiration across a window to prevent all entries
        expiring at the same instant. Simple but effective.
        """
        jitter = base_ttl_s * jitter_pct
        return base_ttl_s + random.uniform(-jitter, jitter)

    @staticmethod
    def version_key(tools_schema: dict, schema_version: str) -> str:
        """Include schema version in cache key.

        When tools/schema change, the cache key changes automatically.
        Old cached entries expire naturally via TTL.
        Prevents stale tool definitions from being served.
        """
        versioned = {**tools_schema, "_schema_version": schema_version}
        return hashlib.sha256(canonical_json(versioned).encode()).hexdigest()[:32]

    @staticmethod
    def should_recompute_early(
        compute_delta_s: float,
        ttl_remaining_s: float,
        beta: float = 1.0,
    ) -> bool:
        """XFetch probabilistic early recomputation.

        Formula: recompute if -beta * delta * ln(random()) > ttl_remaining
        As TTL remaining shrinks, probability of recomputation rises.
        One request regenerates early; the rest continue hitting cache.

        Prevents stampede without distributed locking.
        """
        if ttl_remaining_s <= 0:
            return True
        return -beta * compute_delta_s * math.log(random.random()) > ttl_remaining_s


# --- Single-Flight for Cold Prefix Writes ---

class SingleFlight:
    """Serialize cold prefix writes so N cold requests are not N writes at 1.25x.

    Anthropic: entry exists only after first response begins.
    Without single-flight, N simultaneous cold requests = N writes.
    With single-flight, 1 write + (N-1) wait for result.
    """
    def __init__(self):
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]


# --- Tenant Salt (Security) ---

def compute_tenant_salt(server_secret: bytes, tenant_id: str) -> str:
    """HMAC-based tenant salt for cache isolation.

    Salt is a secret the GATEWAY injects. Never accept client-supplied salt.
    vLLM: omit cache_salt = globally content-addressed sharing (timing attack).

    CVE-2025-46570: ROC AUC 0.571 at 1-token, 0.99 at 8 tokens.
    Patched >= vLLM 0.9.0 via salting.

    Trade-off: per-tenant salt closes timing channel but duplicates KV
    (32 tenants = 32x the KV memory vs shared).
    """
    digest = hmac.new(server_secret, tenant_id.encode(), hashlib.sha256).digest()
    return hashlib.sha256(digest).hexdigest()


# --- Cost Savings Calculator ---

def cache_break_even(write_multiplier: float, read_multiplier: float) -> int:
    """Calculate break-even reuse count.

    n identical-prefix requests cost: W + (n-1)*R vs n uncached.
    Break-even: n >= (W - R) / (1 - R)

    Key numbers:
    - Anthropic 5m / OpenAI 5.6: W=1.25, R=0.1 -> break-even n=2
    - Anthropic 1h:              W=2.0,  R=0.1 -> break-even n=3
    - Fable/Mythos 5.1:         W=1.25, R=0.025 -> break-even n=2
    """
    if read_multiplier >= 1.0:
        return float("inf")  # Never breaks even
    return math.ceil((write_multiplier - read_multiplier) / (1.0 - read_multiplier))


def cache_cost_per_1k_queries(
    prefix_tokens: int = 8000,
    suffix_tokens: int = 400,
    output_tokens: int = 400,
    input_price_per_m: float = 2.00,     # Sonnet 5 base input
    output_price_per_m: float = 10.00,   # Sonnet 5 output
    write_multiplier: float = 1.25,       # 5m cache
    read_multiplier: float = 0.10,        # 5m cache read
    cache_hit_rate: float = 0.999,        # 1 write + 999 reads
    queries: int = 1000,
) -> dict:
    """Calculate cost with and without prompt caching.

    Reference mix: 8k-token tools+system prefix, 400-token user/tool suffix,
    400-token output. 1,000 queries.

    Sonnet 5, 5-minute cache:
    - Uncached: $20.80/1k
    - 1 write + 999 reads: ~$6.42/1k (69% savings)

    gpt-5.6-luna:
    - 1 write + 999 reads: ~$0.72/1k vs uncached $2.16
    """
    total_input = prefix_tokens + suffix_tokens

    # Uncached cost
    uncached_input_cost = queries * total_input * input_price_per_m / 1_000_000
    uncached_output_cost = queries * output_tokens * output_price_per_m / 1_000_000
    uncached_total = uncached_input_cost + uncached_output_cost

    # Cached cost
    writes = int(queries * (1 - cache_hit_rate)) or 1
    reads = queries - writes

    # Prefix cost: writes at write_multiplier, reads at read_multiplier
    prefix_write_cost = writes * prefix_tokens * input_price_per_m * write_multiplier / 1_000_000
    prefix_read_cost = reads * prefix_tokens * input_price_per_m * read_multiplier / 1_000_000

    # Suffix is always uncached (after breakpoint)
    suffix_cost = queries * suffix_tokens * input_price_per_m / 1_000_000

    # Output cost is unchanged by caching
    output_cost = uncached_output_cost

    cached_total = prefix_write_cost + prefix_read_cost + suffix_cost + output_cost
    savings_pct = (1 - cached_total / uncached_total) * 100 if uncached_total > 0 else 0

    return {
        "uncached_total": round(uncached_total, 2),
        "cached_total": round(cached_total, 2),
        "savings_usd": round(uncached_total - cached_total, 2),
        "savings_pct": round(savings_pct, 1),
        "breakdown": {
            "prefix_write": round(prefix_write_cost, 3),
            "prefix_read": round(prefix_read_cost, 3),
            "suffix": round(suffix_cost, 3),
            "output": round(output_cost, 3),
        },
        "writes": writes,
        "reads": reads,
    }


def multi_tier_savings(
    queries_per_month: int,
    avg_input_tokens: int = 10000,
    avg_output_tokens: int = 400,
    input_price_per_m: float = 3.00,     # Sonnet 4.6
    output_price_per_m: float = 15.00,
    l1_exact_hit_rate: float = 0.20,     # FAQ exact repeats
    l2_semantic_hit_rate: float = 0.25,  # Paraphrase variants
    l3_prefix_hit_rate: float = 0.90,    # Prefix cache on remaining
    l3_read_multiplier: float = 0.10,
) -> dict:
    """Multi-tier cache savings (L1 exact + L2 semantic + L3 prefix).

    Scenario B from the module: 50K queries/day, $45K/mo current spend.
    Target: 60% cost reduction.

    L1 handles ~20% exact-repeat queries (password resets, balance checks).
    L2 catches ~25% paraphrased variants (FAQ-style fee questions).
    L3 reduces cost on remaining via shared system prompt + FAQ documents.
    """
    base_cost_per_query = (
        avg_input_tokens * input_price_per_m / 1_000_000
        + avg_output_tokens * output_price_per_m / 1_000_000
    )
    uncached_monthly = queries_per_month * base_cost_per_query

    remaining = queries_per_month

    # L1: Exact hit -- skip LLM entirely, cost ~0
    l1_hits = int(remaining * l1_exact_hit_rate)
    l1_cost = 0  # Redis lookup, negligible
    remaining -= l1_hits

    # L2: Semantic hit -- skip LLM entirely
    l2_hits = int(remaining * l2_semantic_hit_rate)
    l2_cost = l2_hits * avg_input_tokens * 0.02 / 1_000_000  # Embedding cost only
    remaining -= l2_hits

    # L3: Prefix cache on rest
    l3_prefix_tokens = int(avg_input_tokens * 0.8)  # 80% is stable prefix
    l3_suffix_tokens = avg_input_tokens - l3_prefix_tokens
    l3_cached = int(remaining * l3_prefix_hit_rate)
    l3_miss = remaining - l3_cached

    l3_cached_cost = l3_cached * (
        l3_prefix_tokens * input_price_per_m * l3_read_multiplier / 1_000_000
        + l3_suffix_tokens * input_price_per_m / 1_000_000
        + avg_output_tokens * output_price_per_m / 1_000_000
    )
    l3_miss_cost = l3_miss * base_cost_per_query

    cached_monthly = l1_cost + l2_cost + l3_cached_cost + l3_miss_cost
    savings_pct = (1 - cached_monthly / uncached_monthly) * 100 if uncached_monthly > 0 else 0

    return {
        "uncached_monthly": round(uncached_monthly, 2),
        "cached_monthly": round(cached_monthly, 2),
        "savings_usd": round(uncached_monthly - cached_monthly, 2),
        "savings_pct": round(savings_pct, 1),
        "tier_breakdown": {
            "l1_exact_hits": l1_hits,
            "l2_semantic_hits": l2_hits,
            "l3_prefix_cached": l3_cached,
            "l3_miss": l3_miss,
        },
    }


# --- Cache Hit Rate Monitoring ---

@dataclass
class CacheMetrics:
    """Metrics to monitor -- dashboards that lie are a common failure mode.

    A 99% TOKEN hit rate with 100% write rate means you are paying 1.25x
    on every request (prefix thrash).

    Board ALL of these, not just token hit rate:
    - Token hit rate (cached_tokens / total_input_tokens)
    - Request hit rate (requests with cache hit / total requests)
    - Write/read ratio (cache_write_tokens / cache_read_tokens)
    - RPM per prompt_cache_key (alert at ~15 before overflow)
    """
    total_requests: int = 0
    cache_hits: int = 0
    total_input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0

    def record(self, input_tokens: int, cached: int, written: int):
        self.total_requests += 1
        self.total_input_tokens += input_tokens
        self.cached_tokens += cached
        self.cache_write_tokens += written
        if cached > 0:
            self.cache_hits += 1

    @property
    def token_hit_rate(self) -> float:
        return self.cached_tokens / max(self.total_input_tokens, 1)

    @property
    def request_hit_rate(self) -> float:
        return self.cache_hits / max(self.total_requests, 1)

    @property
    def write_read_ratio(self) -> float:
        return self.cache_write_tokens / max(self.cached_tokens, 1)

    def diagnose(self) -> list[str]:
        """Diagnose common caching issues from metrics."""
        issues = []
        if self.token_hit_rate > 0.95 and self.write_read_ratio > 0.5:
            issues.append(
                "High token hit rate but high write ratio -- likely prefix thrash. "
                "Check for timestamps or unsorted JSON in the cached span."
            )
        if self.token_hit_rate > 0.90 and self.request_hit_rate < 0.10:
            issues.append(
                "High token hit but low request hit -- large static prefix "
                "with unique queries. Token hit is misleading; check actual savings."
            )
        if self.token_hit_rate < 0.10:
            issues.append(
                "Very low token hit rate -- verify prompt assembly. "
                "Check: prefix below model minimum? Volatile content in cached span?"
            )
        return issues


# --- Demo ---

if __name__ == "__main__":
    print("=" * 60)
    print("Caching in LLM Systems -- Interview Prep Demos")
    print("=" * 60)

    # 1. Prompt caching patterns
    print("\n--- Prompt Caching (Anthropic) ---")
    parts = PromptParts(
        model="claude-sonnet-5-20260901",
        tools=[{"name": "search", "description": "Search the knowledge base"}],
        system="You are a helpful customer support agent. Use the search tool to find answers.",
        user_message="What is your return policy?",
    )
    anthropic_req = build_anthropic_prompt(parts)
    print(f"System has cache_control: {anthropic_req['system'][0].get('cache_control')}")
    print(f"User message (volatile, after breakpoint): '{parts.user_message}'")

    # 2. KV cache memory calculations
    print("\n--- KV Cache Memory Math ---")
    for model_name, cfg in MODEL_KV_CONFIGS.items():
        mem = kv_cache_memory_per_token(
            n_layers=cfg["n_layers"],
            n_kv_heads=cfg["n_kv_heads"],
            d_head=cfg["d_head"],
        )
        print(f"  {model_name:20s} ({cfg['label']:25s}): {mem['kb_per_token']:>10.3f} KB/token")

    # Worked example: 8k prefix on Llama-405B
    print("\n  Worked example: 8k-token tools prefix")
    llama405b = kv_cache_memory_per_token(126, 8, 128)
    ds_v3 = kv_cache_memory_per_token(61, 1, 288)
    prefix_llama = kv_cache_for_prefix(8000, llama405b["kb_per_token"], num_tenants_sharing=32)
    prefix_ds = kv_cache_for_prefix(8000, ds_v3["kb_per_token"], num_tenants_sharing=32)
    print(f"  Llama-405B: {prefix_llama['total_gb_shared']:.2f} GB shared, "
          f"{prefix_llama['total_gb_isolated']:.2f} GB isolated (32 tenants)")
    print(f"  DeepSeek-V3: {prefix_ds['total_gb_shared']:.2f} GB shared, "
          f"{prefix_ds['total_gb_isolated']:.2f} GB isolated (32 tenants)")

    # 3. Semantic cache
    print("\n--- Semantic Cache ---")
    cache = SemanticCache(threshold=0.90)
    cache.put("What is your return policy?", "Returns accepted within 30 days.",
              tenant_id="acme", model="sonnet-5")

    hit = cache.get("What is the return policy?", tenant_id="acme", model="sonnet-5")
    cross_tenant = cache.get("What is the return policy?", tenant_id="other", model="sonnet-5")
    print(f"Same-tenant hit:  {hit}")
    print(f"Cross-tenant hit: {cross_tenant}  (correctly blocked by tenant filter)")

    # 4. Cache warming
    print("\n--- Cache Warming Plan ---")
    prefixes = [
        PromptParts("sonnet-5", [], "System prompt A", user_message=""),
        PromptParts("sonnet-5", [], "System prompt B", user_message=""),
    ]
    plan = create_warming_plan(prefixes, priorities=[10, 5])
    for item in plan.prefixes_to_warm:
        print(f"  Priority {item['priority']}: key={item['key']}, "
              f"max_tokens={item['warm_request']['max_tokens']}")

    # 5. Break-even analysis
    print("\n--- Break-Even Reuse Count ---")
    schemes = [
        ("Anthropic 5m / OpenAI 5.6", 1.25, 0.10),
        ("Anthropic 1h", 2.00, 0.10),
        ("Fable/Mythos 5.1", 1.25, 0.025),
        ("Fireworks default", 1.00, 0.50),
    ]
    for name, w, r in schemes:
        n = cache_break_even(w, r)
        print(f"  {name:30s}: break-even at n={n} requests")

    # 6. Cost savings
    print("\n--- Cost per 1k Queries (Sonnet 5, 5m cache) ---")
    sonnet_cost = cache_cost_per_1k_queries(
        input_price_per_m=2.00, output_price_per_m=10.00
    )
    print(f"  Uncached: ${sonnet_cost['uncached_total']:.2f}")
    print(f"  Cached:   ${sonnet_cost['cached_total']:.2f}")
    print(f"  Savings:  {sonnet_cost['savings_pct']}% (${sonnet_cost['savings_usd']:.2f})")

    luna_cost = cache_cost_per_1k_queries(
        input_price_per_m=0.20, output_price_per_m=1.20
    )
    print(f"\n  Luna uncached: ${luna_cost['uncached_total']:.2f}")
    print(f"  Luna cached:   ${luna_cost['cached_total']:.2f}")

    # 7. Multi-tier savings (50K/day support bot scenario)
    print("\n--- Multi-Tier Savings (50K queries/day) ---")
    tier_savings = multi_tier_savings(queries_per_month=50000 * 30)
    print(f"  Uncached monthly: ${tier_savings['uncached_monthly']:,.0f}")
    print(f"  Cached monthly:   ${tier_savings['cached_monthly']:,.0f}")
    print(f"  Savings:          {tier_savings['savings_pct']}% "
          f"(${tier_savings['savings_usd']:,.0f}/mo)")

    # 8. Invalidation patterns
    print("\n--- Invalidation Patterns ---")
    inv = CacheInvalidationStrategy()
    ttls = [inv.ttl_with_jitter(300.0) for _ in range(5)]
    print(f"  TTL with jitter (base=300s): {[round(t, 1) for t in ttls]}")

    key_v1 = inv.version_key({"search": {}}, "v1")
    key_v2 = inv.version_key({"search": {}}, "v2")
    print(f"  Schema v1 key: {key_v1[:16]}...")
    print(f"  Schema v2 key: {key_v2[:16]}... (different -> cache auto-invalidates)")

    # 9. Cache metrics diagnosis
    print("\n--- Cache Metrics Diagnosis ---")
    metrics = CacheMetrics()
    # Simulate prefix thrash: high token hit, high writes
    for _ in range(100):
        metrics.record(input_tokens=10000, cached=9500, written=9500)
    print(f"  Token hit rate: {metrics.token_hit_rate:.1%}")
    print(f"  Write/read ratio: {metrics.write_read_ratio:.2f}")
    issues = metrics.diagnose()
    for issue in issues:
        print(f"  Issue: {issue[:100]}...")

    # 10. Tenant salt
    print("\n--- Tenant Salt (Security) ---")
    secret = b"server-secret-key-never-from-client"
    salt_a = compute_tenant_salt(secret, "tenant_a")
    salt_b = compute_tenant_salt(secret, "tenant_b")
    print(f"  Tenant A salt: {salt_a[:16]}...")
    print(f"  Tenant B salt: {salt_b[:16]}... (different -> no shared KV)")
