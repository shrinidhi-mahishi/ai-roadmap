# Prompt Caching

## Why It Matters
Deep Agents repeatedly resend large static prefixes: system instructions, tool schemas, memory, and skill-related prompt material. On long sessions, that repeated prefix becomes a major cost and latency driver. Prompt caching is the harness-level optimization that targets exactly that problem.

In interviews, the clean framing is that Deep Agents does not ask you to remember to wire caching every time for supported providers. It auto-registers provider-specific caching middleware.

## Mental Model
Prompt caching in Deep Agents is about stable prompt prefixes:

- build the final prompt shape
- mark the reusable static prefix through provider middleware
- reuse it on later turns when the prefix still matches

The important system-design detail is middleware placement. The docs say caching runs after `PatchToolCallsMiddleware` and after your own middleware so the cached prefix matches what is actually sent to the model.

## Architecture / Flow
```text
prompt assembly
  -> system prompt and profile overlays
  -> tool descriptions and tool set
  -> skill metadata / skill content
  -> memory injection

middleware flow
  -> PatchToolCallsMiddleware
  -> user middleware / profile extras
  -> prompt-caching middleware
     -> AnthropicPromptCachingMiddleware
     -> BedrockPromptCachingMiddleware
  -> model call

later turns
  -> same static prefix -> cache hit
  -> changed prefix -> cache rewrite
```

One subtle doc detail: `MemoryMiddleware` is placed after the prompt-caching middleware so memory updates are less likely to invalidate the cache prefix.

## Key Concepts
- `create_deep_agent` automatically wires prompt caching for supported Anthropic and Bedrock models. No extra configuration is required for the default case.

- Deep Agents always registers both `AnthropicPromptCachingMiddleware` and `BedrockPromptCachingMiddleware`. Each middleware no-ops on unsupported models with `unsupported_model_behavior="ignore"`.

- The static prefix includes the repeated prompt material that Deep Agents sends on each turn. The overview page calls out base agent instructions, memory, and skill content as cache-eligible sections.

- If you want a longer cache window on Anthropic, replace the default middleware instance:
  - `AnthropicPromptCachingMiddleware(ttl="1h")`

- The default Anthropic TTL called out in the docs is `5m`. The customization guide's example shows overriding it to `1h`.

- `system_prompt_suffix` matters because prompt caching keys off the effective final prompt prefix, not only the raw `system_prompt=` string you typed.

- Tool descriptions matter too. If you keep unused built-ins around, they still enlarge the repeated prefix and can reduce the practical efficiency of caching.

- Prompt caching is provider-specific. For unsupported providers, the Deep Agents middleware is present but inert.

## Metrics and Formulas to Memorize
- Default Deep Agents behavior: prompt caching auto-enabled for supported Anthropic and Bedrock models
- Default Anthropic TTL in docs: `5m`
- Override example: `AnthropicPromptCachingMiddleware(ttl="1h")`
- Unsupported-provider behavior: `unsupported_model_behavior="ignore"`
- Prefix-stability rule:
  - stable prefix -> reuse
  - changed prefix -> new cache write
- The official Deep Agents docs do not publish harness-level cache hit-rate, p95 latency, or cost-reduction benchmarks across providers

## Trade-offs and Failure Modes
- Single-turn or rarely reused workloads may pay cache-write overhead without enough repeated prefix reuse to justify it.

- Any change to the effective prefix can invalidate caching. In practice that includes prompt edits, profile suffix changes, tool-description changes, skill changes, and other middleware-driven prompt mutations.

- Assuming every provider benefits is incorrect. Deep Agents registers the caching middleware broadly, but unsupported models simply ignore it.

- Large tool catalogs make the prefix heavier. Caching can help, but removing unnecessary tools with `excluded_tools` is still the cleaner fix.

- If memory changes often and is injected into the prefix carelessly, you can erode cache stability. The docs explicitly note middleware ordering as a mitigation.

## Interview Q&A
**Q: What does Deep Agents cache by default?**  
A: Static repeated prompt material, especially base instructions and repeated context sections such as memory and skills, when the selected provider supports prompt caching.

**Q: Which providers get automatic prompt caching in Deep Agents?**  
A: Anthropic models and supported Amazon Bedrock models.

**Q: How do I extend the cache TTL?**  
A: Replace the default Anthropic middleware with `AnthropicPromptCachingMiddleware(ttl="1h")`.

**Q: Why does middleware order matter for caching?**  
A: Because the docs want the cache key to reflect the actual prompt sent to the model after patching and middleware prompt edits.

**Q: What invalidates the cache?**  
A: Any change in the repeated prefix, such as prompt edits, tool-schema changes, skill or memory changes, or model/provider-specific prompt overlays.

**Q: What happens on unsupported providers?**  
A: The middleware no-ops instead of failing.

## Sources
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
- [Customize Deep Agents](https://docs.langchain.com/oss/python/deepagents/customization.md)
