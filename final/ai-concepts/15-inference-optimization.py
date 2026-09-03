"""
Inference Optimization -- Caching, Routing, Batching, and KV Math.

Inference cost is the dominant expense in production AI. Prompt caching (reuse KV for
repeated prefixes), semantic caching (skip the LLM entirely), model routing (cheap model
for easy queries), and batching (amortize GPU overhead) can cut costs by 50-90%.
"""

import time, math, hashlib, random
from dataclasses import dataclass, field

# ================================================================
# Section 1: Prompt / Prefix Cache Simulation
# ================================================================
# Exact-prefix caching reuses KV blocks when two requests share the same leading
# tokens. One-token shift = full miss. Timestamp in system prompt = thrash.
# Pricing: Anthropic Sonnet -- write 1.25x, read 0.1x, base $3/MTok input

CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10
BASE_INPUT_PRICE = 3.0  # $/MTok


class PrefixCache:
    """Simulates hosted prompt caching with TTL and prefix matching."""

    def __init__(self, ttl_seconds: float = 300, min_tokens: int = 512):
        self.store: dict[str, float] = {}
        self.ttl = ttl_seconds
        self.min_tokens = min_tokens
        self.stats = {"hits": 0, "misses": 0, "writes": 0}

    def lookup(self, prefix_tokens: list[str]) -> bool:
        if len(prefix_tokens) < self.min_tokens:
            return False  # below min = silent no-op (Anthropic behavior)
        h = hashlib.sha256("".join(prefix_tokens).encode()).hexdigest()[:16]
        if h in self.store and self.store[h] > time.time():
            self.store[h] = time.time() + self.ttl  # refresh on hit
            self.stats["hits"] += 1
            return True
        self.stats["misses"] += 1
        return False

    def write(self, prefix_tokens: list[str]):
        h = hashlib.sha256("".join(prefix_tokens).encode()).hexdigest()[:16]
        self.store[h] = time.time() + self.ttl
        self.stats["writes"] += 1


def compute_savings(n_reqs: int, prefix_tok: int, var_tok: int, out_tok: int) -> dict:
    """Cost with vs without prompt caching (Anthropic pricing)."""
    out_price = 15.0
    no_cache = n_reqs * ((prefix_tok + var_tok) * BASE_INPUT_PRICE + out_tok * out_price) / 1e6
    first = (prefix_tok * BASE_INPUT_PRICE * CACHE_WRITE_MULT
             + var_tok * BASE_INPUT_PRICE + out_tok * out_price) / 1e6
    rest = (n_reqs - 1) * (prefix_tok * BASE_INPUT_PRICE * CACHE_READ_MULT
                           + var_tok * BASE_INPUT_PRICE + out_tok * out_price) / 1e6
    with_cache = first + rest
    return {"no_cache": round(no_cache, 4), "with_cache": round(with_cache, 4),
            "savings_pct": round((1 - with_cache / no_cache) * 100, 1)}


def demo_prefix_cache():
    print("--- Prompt / Prefix Cache ---")
    cache = PrefixCache(ttl_seconds=300, min_tokens=100)
    system = ["token"] * 504  # stable system prompt prefix
    for i in range(5):
        hit = cache.lookup(system)
        if not hit:
            cache.write(system)
        print(f"  Request {i+1}: {'HIT' if hit else 'MISS'}")
    print(f"  Stats: {cache.stats}")
    s = compute_savings(1000, prefix_tok=8000, var_tok=500, out_tok=400)
    print(f"  1k reqs: no_cache=${s['no_cache']}, cached=${s['with_cache']}, "
          f"savings={s['savings_pct']}%")


# ================================================================
# Section 2: Semantic Cache
# ================================================================
# Embed query -> kNN against cache -> return prior response if similarity >= threshold.
# Risk: false positives serve wrong answers. Always TAG-filter by tenant/model.


def mock_embed(text: str) -> list[float]:
    """Deterministic fake embedding. Real system: text-embedding-3-small."""
    random.seed(hash(text) % 2**32)
    vec = [random.gauss(0, 1) for _ in range(8)]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


class SemanticCache:
    def __init__(self, threshold: float = 0.92):
        self.threshold = threshold
        self.entries: list[dict] = []

    def lookup(self, query: str, tenant: str) -> str | None:
        q_emb = mock_embed(query)
        best_score, best_resp = -1.0, None
        for e in self.entries:
            if e["tenant"] != tenant:  # tenant isolation prevents cross-talk
                continue
            score = sum(a * b for a, b in zip(q_emb, e["embedding"]))
            if score > best_score:
                best_score, best_resp = score, e["response"]
        return best_resp if best_score >= self.threshold else None

    def store(self, query: str, response: str, tenant: str):
        self.entries.append({"embedding": mock_embed(query), "query": query,
                             "response": response, "tenant": tenant})


def demo_semantic_cache():
    print("\n--- Semantic Cache ---")
    cache = SemanticCache(threshold=0.85)
    cache.store("How do I reset my password?", "Go to Settings > Security > Reset.", "acme")
    print(f"  Similar query: {cache.lookup('How can I reset my password?', 'acme') or 'MISS'}")
    print(f"  Wrong tenant:  {cache.lookup('How do I reset my password?', 'other') or 'MISS'}")
    print(f"  Diff question: {cache.lookup('What is the weather today?', 'acme') or 'MISS'}")


# ================================================================
# Section 3: Model Router (RouteLLM-style)
# ================================================================
# Classify complexity -> route easy queries to cheap model, hard to strong.
# Zero extra LLM calls -- just a tiny classifier. >2x cost cut at CPT(50%).

MODELS = {
    "strong": {"name": "gpt-4o",      "cost_1k": 0.010},
    "weak":   {"name": "gpt-4o-mini", "cost_1k": 0.0006},
}


def classify_complexity(query: str) -> str:
    """Toy classifier. Real: fine-tuned BERT or RouteLLM preference router."""
    hard = ["explain", "compare", "design", "analyze", "why", "trade-off"]
    return "strong" if any(s in query.lower() for s in hard) else "weak"


def demo_model_router():
    print("\n--- Model Router ---")
    queries = [
        "What time is it in Tokyo?",               # easy
        "Explain the CAP theorem trade-offs.",      # hard
        "Summarize this paragraph.",                # easy
        "Design a multi-region failover strategy.", # hard
    ]
    cost_strong, cost_routed = 0.0, 0.0
    for q in queries:
        tier = classify_complexity(q)
        cost_strong += MODELS["strong"]["cost_1k"]
        cost_routed += MODELS[tier]["cost_1k"]
        print(f"  [{tier:6s}] {MODELS[tier]['name']:12s} <- {q[:50]}")
    print(f"  Routing saved {(1 - cost_routed / cost_strong) * 100:.0f}% vs always-strong")


# ================================================================
# Section 4: Request Batching
# ================================================================
# Accumulate requests -> batch -> dispatch -> unbatch results.
# GPU utilization jumps because more tokens processed per forward pass.


@dataclass
class BatchedInference:
    max_batch_size: int = 4
    pending: list = field(default_factory=list)

    def add(self, prompt: str):
        self.pending.append(prompt)

    def ready(self) -> bool:
        return len(self.pending) >= self.max_batch_size

    def dispatch(self) -> list[str]:
        """Simulate batched GPU forward pass."""
        batch = self.pending[:]
        self.pending.clear()
        return [f"Response to: {p[:30]}..." for p in batch]


def demo_batching():
    print("\n--- Request Batching ---")
    b = BatchedInference(max_batch_size=3)
    prompts = ["Hello!", "Summarize X.", "What is 2+2?", "Explain RAG.", "Fix bug."]
    results = []
    for p in prompts:
        b.add(p)
        if b.ready():
            r = b.dispatch()
            results.extend(r)
            print(f"  Dispatched batch of {len(r)}")
    if b.pending:  # flush remainder
        r = b.dispatch()
        results.extend(r)
        print(f"  Flushed final batch of {len(r)}")
    print(f"  Total responses: {len(results)}")


# ================================================================
# Section 5: KV Cache Size Calculator
# ================================================================
# Per token per layer BF16: 2 * n_kv_heads * d_head * 2 bytes
# This determines how many concurrent sequences fit in GPU memory.

MODEL_CONFIGS = {
    "Llama-3.1-8B":  {"layers": 32, "kv_heads": 8, "d_head": 128},
    "Llama-3.1-70B": {"layers": 80, "kv_heads": 8, "d_head": 128},
}


def kv_bytes_per_token(layers: int, kv_heads: int, d_head: int, dtype_bytes: int = 2):
    """KV bytes per token across all layers. K+V = factor of 2."""
    return 2 * kv_heads * d_head * dtype_bytes * layers


def max_concurrent_seqs(model: str, seq_len: int = 4096,
                        kv_budget_gb: float = 40.0, dtype_bytes: int = 2) -> dict:
    cfg = MODEL_CONFIGS[model]
    bpt = kv_bytes_per_token(cfg["layers"], cfg["kv_heads"], cfg["d_head"], dtype_bytes)
    bps = bpt * seq_len
    return {"model": model, "kv_kb_per_token": round(bpt / 1024, 1),
            "kv_gb_per_seq": round(bps / 1024**3, 2),
            "max_seqs": int(kv_budget_gb * 1024**3 / bps),
            "dtype": "BF16" if dtype_bytes == 2 else "FP8"}


def demo_kv_calculator():
    print("\n--- KV Cache Size Calculator ---")
    for model in MODEL_CONFIGS:
        for dtype in [2, 1]:  # BF16 vs FP8
            info = max_concurrent_seqs(model, seq_len=4096, dtype_bytes=dtype)
            print(f"  {info['model']:15s} [{info['dtype']}]  "
                  f"{info['kv_kb_per_token']:6.1f} KB/tok  "
                  f"{info['kv_gb_per_seq']:.2f} GB/seq  max_seqs={info['max_seqs']}")
    print("  ** FP8 KV halves memory per token, doubling max concurrency **")


# ================================================================
# Main -- run all demos
# ================================================================

if __name__ == "__main__":
    demo_prefix_cache()
    demo_semantic_cache()
    demo_model_router()
    demo_batching()
    demo_kv_calculator()
