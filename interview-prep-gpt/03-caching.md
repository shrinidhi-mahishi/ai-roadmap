# Caching

## Why It Matters
Caching is one of the highest-leverage optimizations in LLM systems because it can cut cost, reduce time-to-first-token, and improve effective throughput without changing the model itself. But "cache" is overloaded. Exact KV or prompt caches are mostly infrastructure optimizations with no quality loss. Semantic caches are approximate application shortcuts that can return the wrong answer while looking fast and cheap.

That distinction is what usually separates strong interview answers from weak ones. The question is not "can you turn on caching?" The question is "which cache layer are you talking about, what phase does it accelerate, and what correctness guarantees does it preserve?"

## Mental Model
Start with the two inference phases:

- Prefill reads the whole prompt in parallel. This dominates time-to-first-token.
- Decode generates output token by token. This dominates long generation latency.

Most caching primarily helps prefill, not decode. If the model is writing a long answer, prompt caching may barely change total end-to-end latency even though TTFT improves a lot.

The second mental split is exact versus approximate reuse:

- Exact reuse means the same prefix or token blocks are reused. No inherent quality loss.
- Approximate reuse means the system decides a new request is "similar enough" to an old one. That is a product decision, not a free lunch.

## Architecture / Flow
```text
request arrives
  -> optional semantic/result cache lookup
  -> exact prefix/prompt cache lookup
  -> if miss: prefill prompt
  -> decode response
  -> store cacheable artifacts
```

In self-hosted systems, there is usually an additional routing layer:

```text
gateway -> cache-aware router -> engine with KV/prefix cache -> response
```

That router matters because a prefix hit only helps if the request lands on the worker that already holds the relevant KV blocks, or if you have a shared offload layer such as LMCache or a similar remote KV store.

## Key Concepts
- Cache taxonomy:
  - KV cache: stores a sequence's own keys and values during decoding.
  - Prefix or APC cache: reuses shared prompt prefixes across requests.
  - Hosted prompt cache: provider-managed prefix reuse, usually with billing multipliers.
  - Semantic cache: returns a prior answer based on embedding similarity.
  - Application/result cache: stores deterministic tool or workflow outputs.

- Stable prefix design:
  - Put tools, system prompts, and durable instructions first.
  - Put volatile user turns, retrieval output, and fresh tool results last.
  - Timestamps, random ordering, unsorted JSON keys, and session IDs are common cache killers.

- Exact reuse:
  - Safe when the token sequence is identical.
  - Useful for shared tool schemas, policy blocks, and repeated long prompts.
  - Does not create a correctness trade-off by itself.

- Approximate reuse:
  - Useful for FAQ or support-style workloads.
  - Dangerous for personalized, stateful, or high-stakes requests.
  - Must include tenant, model version, locale, and other scope constraints in the cache key or filter.

- Provider differences:
  - OpenAI, Anthropic, and Gemini all support prompt-caching patterns, but token minimums, TTLs, and billing semantics differ.
  - Hosted caches save input cost and TTFT, but they do not replace app-level routing discipline.

- Self-hosted patterns:
  - vLLM APC uses hashed token blocks for exact prefix reuse.
  - SGLang's RadixAttention reuses KV states across shared prefixes.
  - LMCache externalizes KV into a tiered store so reuse can survive engine restarts or worker churn better.

- Cache-aware routing:
  - `prompt_cache_key`, longest-prefix-match scheduling, and prefix-affinity routing all try to keep similar requests near warm state.
  - A round-robin load balancer can erase much of the benefit.

- Security:
  - Shared caches can leak through timing side channels.
  - Cached unvalidated tool or retrieved content can amplify attacks cheaply.
  - Tenant isolation belongs in the gateway, not in a model-visible prompt field.

## Metrics and Formulas to Memorize
- Hosted cache economics most worth memorizing:
  - OpenAI GPT-5.6+ and Anthropic 5-minute cache both follow the same basic pattern: `1.25x` write, `0.1x` read
  - Anthropic 1-hour cache: `2.0x` write, `0.1x` read

- Break-even intuition:
  - for `1.25x / 0.1x` caches, the first reuse already pays back the write premium

- Minimum cacheable prompt lengths:
  - OpenAI GPT-5.6+: `1,024` tokens
  - older OpenAI models: often `2,048`
  - Anthropic varies by model from `512` to `4,096`
  - Gemini 2 family: `2,048`
  - Gemini 3 family: `4,096`

- Semantic cache operating heuristics:
  - bounded-intent FAQ/support workloads fit much better than personalized or stateful agents
  - threshold tuning is the key lever: looser matching raises hit rate but increases false positives

- KV memory formula per layer per token:
  - `2 * n_kv_heads * d_head * bytes`

- Local anchor example:
  - Llama-3.1-8B with GQA is about `128 KB/token`
  - `32k` context is about `4 GB` of KV

- Timing side-channel anchor:
  - KVGov reported a cached/cold TTFT ratio around `0.22`

The right interview move is to explain what those metrics measure. Request hit rate, token hit rate, cache writes, and TTFT savings tell different stories.

## Trade-offs and Failure Modes
- Exact-prefix cache thrash:
  a single token change in the stable prefix can turn "caching enabled" into "paying write multipliers without reuse."

- Cache stampede:
  many cold requests with the same prefix arrive together and all write before a reusable entry exists.

- Semantic false positives:
  the system serves a prior answer that is similar but wrong for the current request.

- Cross-tenant leakage:
  shared-prefix timing or bad cache-key scope can leak information across users or customers.

- Cached malicious content:
  if retrieved or tool-generated content is unsafe before caching, replay just makes the attack cheaper.

- Misreading the win:
  prompt cache helps TTFT; it does not fix long decode-heavy generations.

- Idle explicit-cache cost:
  some cache forms charge storage rent or operational cost even when reuse is weak.

- Routing mismatch:
  the cache exists, but the request lands on the wrong worker, so you pay for warm state that you rarely hit.

## Interview Q&A
**Q: What is the difference between KV cache and prompt cache?**  
A: KV cache is the model's internal state during generation. Prompt cache or prefix cache reuses that state across requests when a prefix matches.

**Q: Which caches are quality-safe?**  
A: Exact prefix and KV caches are quality-safe because they reuse identical computation. Semantic caches are not inherently safe because they approximate similarity.

**Q: Why does caching mostly improve TTFT?**  
A: Because it usually skips prefill work. Decode still happens token by token.

**Q: What belongs in the stable prefix?**  
A: Tool schemas, system prompts, few-shots, and other deterministic instructions. Fresh user content and retrieval results should come later.

**Q: When should you avoid semantic caching?**  
A: Personalized requests, stateful multi-turn interactions, creative generation, and high-stakes decisions where "close enough" is unsafe.

**Q: How do you isolate caches in multi-tenant systems?**  
A: Inject tenant-scoped salts or keys at the gateway, keep tenant filters in semantic cache lookups, and avoid client-supplied cache identity.

**Q: What is the biggest cache anti-pattern?**  
A: Thinking "cache hit rate" alone proves success. You need request hit rate, token hit rate, write/read ratio, and correctness checks.

**Q: Why can a semantic cache be worse than no cache?**  
A: Because it can confidently return the wrong answer while hiding the fact that the model was never re-run.

## Sources
- Local anchors:
  - `ai-roadmap/final/15-inference-optimization.md`
  - `ai-roadmap/final/02-context-engineering.md`
  - `ai-roadmap/final/14-observability.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [OpenAI Prompt Caching Guide](https://developers.openai.com/api/docs/guides/prompt-caching)
  - [OpenAI Prompt Caching 101](https://developers.openai.com/cookbook/examples/prompt_caching101)
  - [Anthropic Prompt Caching Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
  - [Gemini API Caching](https://ai.google.dev/api/caching)
  - [Gemini Enterprise Context Caching Overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-overview)
  - [vLLM Automatic Prefix Caching](https://docs.vllm.ai/en/stable/features/automatic_prefix_caching/)
  - [Redis Semantic Cache Docs](https://redis.io/docs/latest/develop/use-cases/semantic-cache/)
  - [SGLang Paper](https://arxiv.org/abs/2312.07104)
  - [LMCache Docs](https://docs.lmcache.ai/)
  - [LMCache Architecture Overview](https://docs.lmcache.ai/developer_guide/architecture.html)
  - [KVGov Paper](https://export.arxiv.org/pdf/2608.09225)
