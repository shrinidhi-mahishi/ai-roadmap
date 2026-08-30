# Research: Context Engineering

**Date researched**: 2026-08-21
**Sources consulted**: 72

## 1. System Topology & Mechanics

Context engineering is the control-plane discipline of assembling, budgeting, compressing, and caching the token set presented to a model at each inference step. Anthropic defines it as curating “the smallest possible set of high-signal tokens that maximize the likelihood of some desired outcome,” treating context as a finite attention budget with diminishing returns ([Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### 1.1 Control plane vs data plane

| Plane | Components | What it owns |
| --- | --- | --- |
| **Control plane** | Prompt compiler, token budgeter, cache manager, session store, compaction policy | Role assembly, prefix stability, breakpoint placement, trim/summarize triggers, cache-key routing, injection isolation |
| **Data plane** | Tokenizer, transformer prefill, KV cache, sampler | Exact-prefix KV reuse, TTFT, decode ITL; does not decide *what* entered the window |

Hosted APIs hide the data plane. The application owns the agent loop: append `tool_result`, re-call, keep prefixes byte-identical. Anthropic states the model never executes tools; it emits `tool_use` blocks and the client must echo them plus matching `tool_result` blocks ([Claude tool_use / tool_result handshake](https://dev.to/multigrid/claudes-tooluse-and-toolresult-content-blocks-end-to-end-3nli); [Anthropic SDK `ToolResultBlockParam`](https://github.com/anthropics/anthropic-sdk-python/blob/d2f6543e/src/anthropic/types/tool_result_block_param.py)).

**Prompt compiler (typical production graph)**

1. Load **session** state (durable conversation) separately from **request** state (this turn’s tools, RAG hits, user message).
2. Render roles in provider order: Anthropic cache hierarchy is `tools` → `system` → `messages` ([Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). OpenAI instruction hierarchy: `developer`/`system` take precedence over `user` ([OpenAI Responses create](https://developers.openai.com/api/reference/resources/responses/methods/create/); Harmony: `system` > `developer` > `user` > `assistant` > `tool` ([Harmony format](https://developers.openai.com/cookbook/articles/openai-harmony))).
3. Token-budget: count rendered tokens; if over trigger, compact/trim/clear tools *before* the model call.
4. Place cache breakpoints on the last *stable* block, not the varying user/RAG suffix.
5. Stream or sync; on tool loop, append results without mutating the cached prefix.

### 1.2 Prompting: roles, few-shot, XML/markdown, tool-result packing, injection-aware design

**System / developer / user roles**

- **Anthropic Messages API**: `system` is a top-level parameter for role/persona; task instructions and variable data go in `user` turns. Role prompting via `system` is documented as the primary control ([Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)). Mid-conversation system messages on Fable 5 / Opus 5 / Sonnet 5 can be appended as `{"role":"system"}` inside `messages` without invalidating a cached top-level `system` prefix ([Prompt caching — mid-conversation system](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
- **OpenAI Chat Completions**: `developer` messages “replace the previous `system` messages” and are the application’s standing instructions ([Chat Completions message types](https://developers.openai.com/api/reference/resources/chat)). Reasoning models (GPT-5 family / o-series) treat `system` as platform-reserved in Harmony; application prompts belong in `developer` ([Harmony](https://developers.openai.com/cookbook/articles/openai-harmony)).
- **OpenAI Responses API**: `instructions` is a per-call system/developer insertion that applies *only to the current request* and is *not* carried by `previous_response_id`. Permanent rules must live in a `developer` input item, or they vanish next turn and also break cache prefixes if `instructions` change ([OpenAI community, Dec 2025](https://community.openai.com/t/system-and-developer-roles-in-messages-and-instructions-in-responses-create/1370516/13); [Responses items](https://developers.openai.com/api/reference/resources/conversations/subresources/items/)). Changing `instructions` every turn is a prefix-stability failure.

**Few-shot / multishot**

GPT-3 established in-context learning with K typically 10–100 demonstrations fitting `n_ctx = 2048`, no gradient updates ([Brown et al., NeurIPS 2020](https://arxiv.org/abs/2005.14165)). Claude docs treat a few well-crafted examples as “one of the most reliable” format/tone controls; examples should be wrapped in XML and, with thinking on, can include `<thinking>` traces inside shots so the model generalizes that pattern ([Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)). Few-shot blocks belong in the *stable prefix* (after system, before the live user turn) so they participate in prompt cache / KV prefix reuse. Adding a new shot mid-session invalidates every cache hash at or after that block ([Anthropic cache writes only at breakpoints](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

**XML vs markdown structure**

Anthropic trains and documents XML-style tags (`<instructions>`, `<documents>`, `<document index="n">`, `<document_content>`, `<source>`) for mixed prompts. Queries placed *after* longform documents improved quality by **up to 30%** on complex multidocument tests ([Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)). Tags are a convention, not validated XML ([claudexml.com](https://claudexml.com/)). Markdown headings are the default for OpenAI/Gemini developer messages; OpenAI still accepts XML wrappers around untrusted RAG. Azure guardrails tell you to wrap retrieved docs as `""" <documents> … </documents> """` so Prompt Shields can classify them as documents, not user commands ([Azure Foundry guardrails](https://learn.microsoft.com/en-us/azure/foundry/guardrails/how-to-create-guardrails)).

**Tool-result packing**

Canonical Anthropic packing:

```
assistant: [text?] [tool_use {id, name, input}]+
user:     [tool_result {tool_use_id, content, is_error?}]+   // MUST be first in that user message
```

`tool_use_id` must equal `tool_use.id` or the API returns 400. Multiple tools in one assistant turn require all corresponding `tool_result` blocks in a *single* following user message ([Anthropic tool calling](https://docs.parallel.ai/integrations/anthropic-tool-calling)). `content` may be a string or an array of text/image/document/search_result blocks; `cache_control` is a first-class field on `tool_result` so agent loops can cache through the last result ([SDK type](https://github.com/anthropics/anthropic-sdk-python/blob/d2f6543e/src/anthropic/types/tool_result_block_param.py)). Shape of `content` is prompt design: raw stack traces vs “failed at stage X, exit 1, logs: …” change whether the model retries well ([tool_result packing note](https://dev.to/multigrid/claudes-tooluse-and-toolresult-content-blocks-end-to-end-3nli)).

OpenAI Harmony packs tool output as role `tool` (or the tool name as role), below `user` in the instruction hierarchy — tool text must not outrank developer rules ([Harmony](https://developers.openai.com/cookbook/articles/openai-harmony)).

**Injection-aware prompt design**

OWASP LLM01:2025: models cannot reliably separate instructions from data; fool-proof prevention is not claimed. Mitigations: constrain behavior in the system prompt, validate output schemas, filter I/O, *segregate and denote untrusted content* ([OWASP Top 10 for LLMs 2025 PDF](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)). Microsoft Spotlighting (delimiting / datamarking / encoding) reduced attack success from **>50% to <2%** on GPT-family experiments ([Hines et al., arXiv:2403.14720](https://arxiv.org/abs/2403.14720); [MSRC, Jul 2025](https://www.microsoft.com/en-us/msrc/blog/2025/07/how-microsoft-defends-against-indirect-prompt-injection-attacks)). Production pattern: trusted instructions in `system`/`developer`; untrusted RAG/tool/web in tagged blocks *after* a cache breakpoint; classifiers (Azure Prompt Shields user-prompt + document attacks) on ingress ([Prompt Shields GA](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/general-availability-of-prompt-shields-in-azure-ai-content-safety-and-azure-open/4235560)). Anthropic trains Claude with RL against simulated web injections and scans untrusted content with classifiers; Opus 4.5 browser-use ASR reported ~1% with full safeguards ([Anthropic prompt-injection defenses](https://www.anthropic.com/research/prompt-injection-defenses)). Delimiters are defense-in-depth, not a boundary the model can enforce ([MDPI review 2026](https://www.mdpi.com/2078-2489/17/1/54)).

### 1.3 Context management: packing, scratchpads, trimming, session vs request, multi-turn

**Window packing order (cache-stable)**

Recommended physical order, aligned with Anthropic’s cache hierarchy and OpenAI “static first” rule ([OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [AWS Bedrock prompt caching blog](https://aws.amazon.com/blogs/machine-learning/effectively-use-prompt-caching-on-amazon-bedrock/)):

1. Tool schemas (rarely change; changing them invalidates the *entire* Anthropic cache).
2. System / developer instructions + few-shot.
3. Session memory / notes (slow-changing).
4. RAG corpus or pinned documents (daily-changing → own breakpoint).
5. Conversation history (grows; automatic caching or growing breakpoint).
6. Current user turn + fresh tool results (never in the stable prefix).
7. Scratchpad / thinking (model-emitted; cannot be explicitly `cache_control`’d on Anthropic, but is cached as part of subsequent tool-loop prefixes ([caching with thinking](https://platform.claude.com/docs/en/build-with-claude/prompt-caching))).

Long documents go *above* the query (Anthropic 30% quality claim). Lost-in-the-middle implies putting the *needle* (query, constraints, current task) at the end, not burying it under more RAG ([Liu et al., TACL 2024](https://aclanthology.org/2024.tacl-1.9.pdf)).

**Scratchpads**

- **In-window**: extended thinking / Harmony `analysis` channel / Claude thinking blocks. These consume input tokens on later turns when echoed.
- **Out-of-window (ADK)**: `session.state` is the agent scratchpad; `session.events` is the transcript. Prefixes: no prefix = session-only, `user:` = all sessions for that user, `app:` = application-global. Persist via `append_event` / `CallbackContext.state`, not by mutating a retrieved dict ([Google ADK State](https://adk.dev/sessions/state/); [ADK sessions](https://adk.dev/sessions/)).
- **Out-of-window (Anthropic memory tool)**: `memory_20250818` — client implements `view`/`create`/`str_replace`/`insert`/`delete`/`rename` on a filesystem the model treats as durable notes. Survives compaction ([Claude cookbook: memory, compaction, tool clearing](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)).
- **OpenAI Agents SDK**: `RunContextWrapper.context` is app state *not* visible to the model unless injected into `instructions`, `input`, or a tool ([Agents SDK context](https://openai.github.io/openai-agents-python/context/)). Session memory is a separate `Session` object prepended each run ([Agents SDK sessions](https://openai.github.io/openai-agents-python/sessions/)).

**Conversation trimming vs summarization**

LangGraph: `trim_messages` with `max_tokens`, `start_on="human"`, `end_on=("human","tool")` drops oldest tokens (lossy, exact). Summarization replaces a prefix with a running summary in a separate state key (`summary` / `context`) so the UI can still render the full transcript ([LangGraph add-memory](https://docs.langchain.com/oss/python/langgraph/add-memory); [LangMem `SummarizationNode`](https://langchain-ai.github.io/langmem/guides/summarization/)). `SummarizationNode` processes oldest→newest; once cumulative tokens hit `max_tokens_before_summary`, those messages (except system) become `[summary] + remaining`. If the to-summarize span exceeds `max_tokens`, only the last `max_tokens` of that span are summarized — a second lossy gate to protect the summarizer’s own window ([LangMem short-term API](https://langchain-ai.github.io/langmem/reference/short_term/)).

**Session vs request context**

| Mechanism | Where state lives | Next-turn payload |
| --- | --- | --- |
| Client replay (`result.history` / `to_input_list`) | App | Full window |
| Agents SDK `session` | App DB / Redis / SQLite | Same session id; SDK prepends history |
| OpenAI `conversationId` | OpenAI Conversations API | New turn only |
| OpenAI `previous_response_id` | Responses API chain | New turn only |
| ADK `SessionService` | In-memory (lost on restart) / SQL / Vertex AI | Runner loads session |
| Mixing local replay + server IDs | Duplicate context unless reconciled | Avoid ([Running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)) |

CrewAI `Memory` is a unified store (replaces short/long/entity/external). Default embedder OpenAI `text-embedding-3-large` (3072-d) or `text-embedding-3-small` depending on docs revision; recall blends semantic similarity, recency, importance. Native memory is **not** isolated by user/session unless you scope paths (`/scope/path`) or use an external store ([CrewAI memory](https://docs.crewai.com/en/concepts/memory)).

**Multi-turn + cache prefixes in tool loops**

Anthropic: a cache entry becomes available only after the *first response begins*. Parallel fan-out of N identical prefixes on a cold cache yields N writes, not 1 hit ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Tool-loop pattern: breakpoint on last `tool_result`; next call reads the growing prefix. Automatic caching moves the breakpoint to the last cacheable block each turn (uses 1 of 4 breakpoint slots). If 4 explicit breakpoints already exist, top-level `cache_control` returns 400 ([automatic caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

OpenAI GPT-5.6+: default *implicit* breakpoint on the latest user/tool message. A timestamp or unique user text *inside* that implicit breakpoint causes a write every turn. Fix: `prompt_cache_options.mode=explicit` plus a breakpoint after tools+system+docs, and a stable `prompt_cache_key` ([OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Keep traffic per key ≈ **15 req/min** or hit rate drops as routing spreads.

Streaming: TTL clocks on Anthropic start at request *start*, not response end. A 4-minute stream on a 5-minute TTL leaves ~1 minute for the follow-up tool call ([Prompt caching lifetime](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). [inferred] Slow tools (>5 min) need `ttl: "1h"` on the prefix breakpoint.

### 1.4 Compression: summarization, hierarchical, LLMLingua, extractive vs abstractive, lossy vs lossless

**Lossless (bit-identical semantics for the model)**

- **Prefix / KV cache reuse**: same tokens, skip prefill. Output identical to uncached ([Anthropic: “no effect on output token generation”](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [vLLM APC](https://docs.vllm.ai/en/latest/features/automatic%5Fprefix%5Fcaching/)).
- **OpenAI `/responses/compact`**: prior assistant/tool/reasoning replaced by an *encrypted* compaction item; **all prior user messages kept verbatim**. Opaque, ZDR-compatible when `store=false`. Window sent to compact must still fit the model context ([OpenAI Compaction](https://developers.openai.com/api/docs/guides/compaction)). This is lossless for user text, lossy/opaque for assistant traces.

**Lossy — extractive (keep subset of original tokens)**

- **LLMLingua** (EMNLP 2023): coarse-to-fine; small LM (GPT-2-small / LLaMA-7B) perplexity to drop tokens; budget controller; **up to 20×** compression with little loss on GSM8K, BBH, ShareGPT, Arxiv-March23 ([Jiang et al.](https://aclanthology.org/2023.emnlp-main.825/); [llmlingua.com](https://llmlingua.com/); [GitHub](https://github.com/microsoft/LLMLingua)).
- **LongLLMLingua** (ACL 2024): query-aware compression + reorganization; **17.1%** performance *gain* at **4×** compression vs uncompressed long context ([Jiang et al. ACL 2024](https://aclanthology.org/2024.acl-long.91)).
- **LLMLingua-2** (ACL 2024 Findings): GPT-4-distilled token classifier, BERT-size encoder, **3–6× faster** than LLMLingua, better OOD ([Pan et al.](https://llmlingua.com/)). Integrated into LangChain and LlamaIndex ([llmlingua.com](https://llmlingua.com/)).
- **Tool-result clearing** (`clear_tool_uses_20250919`): deletes bulky `tool_result` payloads, keeps `tool_use` records. Default trigger **100k** input tokens, keep last **3** tool uses ([Cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)). Lightest-touch compaction Anthropic recommends ([Engineering blog](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
- **Trim messages**: drop tokens; no substitute text.

**Lossy — abstractive (new tokens that paraphrase)**

- **LangGraph / LangMem running summary**: LLM writes a paragraph; facts survive, wording does not.
- **Anthropic server compaction** `compact_20260112` (beta `compact-2026-01-12`): trigger default **150,000** input tokens, **minimum 50,000**. Returns a typed `compaction` block; next request drops everything before it. `pause_after_compaction` lets you audit the summary. Custom `instructions` *replace* the default summarizer prompt entirely ([Compaction docs](https://platform.claude.com/docs/en/build-with-claude/compaction)).
- **Hierarchical / recursive**: summarize chunks → summarize summaries (map-reduce). Anthropic sub-agents: each sub-agent may spend tens of thousands of tokens and return **1,000–2,000** tokens to the parent ([Engineering blog](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). This is hierarchical compression by architecture.
- **CrewAI `extract_memories`**: splits task output into atomic facts before `remember()`, avoiding one blob ([CrewAI memory](https://docs.crewai.com/en/concepts/memory)).

**When compression fights caching**

Any rewrite of tokens before a breakpoint changes the prefix hash → cache miss + possibly a new write. Compress *outside* the cached prefix (RAG corpus via LLMLingua, then pin the compressed blob), or compress *after* eviction (compaction replaces history; the new compaction block becomes the new prefix). [inferred] Do not LLMLingua a prefix that you also prompt-cache; you pay write premium twice and lose KV identity.

### 1.5 Caching: prompt/prefix, KV reuse, provider APIs, semantic, TTL, breakpoints

**KV / prefix cache (serving engines)**

- **vLLM Automatic Prefix Caching**: hash each KV block by tokens in the block *and* prefix tokens; `enable_prefix_caching=True` (default in recent `CacheConfig`). Hash algos: `sha256` (default, pickle, not cross-version stable), `sha256_cbor` (reproducible), `xxhash` ([vLLM prefix caching design](https://docs.vllm.ai/en/latest/design/prefix%5Fcaching/); [cache.py](https://github.com/vllm-project/vllm/blob/bebfe55b/vllm/config/cache.py)). Helps prefill only, not decode ([APC limits](https://docs.vllm.ai/en/latest/features/automatic%5Fprefix%5Fcaching/)).
- **SGLang RadixAttention**: radix tree of token sequences → KV pages; LRU evicts leaves first so shared ancestors survive. Token-level (or `page_size` aligned, e.g. 16). NeurIPS 2024 paper; compatible with continuous batching, PagedAttention, TP ([SGLang paper](https://proceedings.neurips.cc/paper_files/paper/2024/file/724be4472168f31ba1c9ac630f15dec8-Paper-Conference.pdf); [RadixAttention](https://mintlify.wiki/sgl-project/sglang/concepts/radix-attention)).
- **PagedAttention** (SOSP 2023): KV in 16-token blocks; 2–4× throughput vs FasterTransformer/Orca; near-zero KV waste vs 60–80% fragmentation ([Kwon et al.](https://arxiv.org/abs/2309.06180)).
- **LMCache**: offload/share KV across vLLM/SGLang instances (CPU, disk, Redis, S3, NIXL). MP mode: standalone `lmcache server` so engine crash does not drop KV. Eval: **up to 15× throughput** on multi-round QA / document analysis vs vLLM alone ([LMCache paper](https://arxiv.org/html/2510.09665); [vLLM LMCache examples](https://docs.vllm.ai/en/latest/examples/disaggregated/lmcache/)). CacheBlend: non-prefix KV reuse with selective recompute.

**Anthropic prompt cache API**

- Automatic (`cache_control` at request top) or explicit per-block. **Max 4 breakpoints**. Lookback **20 content blocks** per breakpoint.
- TTL: `ephemeral` default **5 min**, optional `"ttl":"1h"`. Refresh on hit at no extra write charge. Clock starts at request start.
- Multipliers vs base input: 5m write **1.25×**, 1h write **2×**, read **0.1×** ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing); [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
- Minimum tokens (Claude API / Foundry / Vertex / Claude Platform on AWS): Opus 5 / Fable 5 **512**; Sonnet 5 / Sonnet 4.6 **1,024**; Opus 4.5/4.6 and Haiku 4.5 **4,096**. Below min: silent no-cache (`cache_*` usage = 0).
- Longer TTL must appear *before* shorter TTL in the same request (Bedrock docs) ([Bedrock prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)).

**OpenAI prompt cache API**

- Pre-GPT-5.6: automatic best-effort; min **1,024–2,048** tokens; writes free; reads at cached-input rate; `prompt_cache_retention` `in_memory` (5–10 min idle, max 1h historically) or `24h` on listed families ([OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); original launch: 50% off, 5–10 min, always gone by 1h, org-isolated ([API prompt caching 2024](https://openai.com/index/api-prompt-caching/))).
- GPT-5.6+: exact match at breakpoints; implicit breakpoint on latest user/tool message; explicit `prompt_cache_breakpoint`; `prompt_cache_key` required for reliable matching; TTL **`30m` only**; writes **1.25×**, reads **0.1×**; up to **4** new cache writes per request; considers latest **50** breakpoints ([OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [Azure OpenAI prompt caching](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/prompt-caching)).
- Cookbook (older models): up to **80%** latency cut for prompts **>10,000** tokens; org-scoped; ZDR-eligible ([Prompt Caching 101](https://developers.openai.com/cookbook/examples/prompt_caching101)).

**Gemini implicit / explicit**

- **Implicit** (Gemini 2.5+ default): prefix reuse, savings passed through on hit, **no guarantee**. Min tokens: Gemini 2.5 Flash/Pro **2,048**; Gemini 3.5 Flash / 3.1 Pro Preview **4,096** ([Gemini caching](https://ai.google.dev/gemini-api/docs/generate-content/caching)). Vertex: implicit caches deleted within **24 hours**; no storage fee ([Vertex context caching blog](https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-context-caching)).
- **Explicit**: `caches.create` with TTL (default **1 hour**); reference `cached_content`. Billing: discounted cached input **plus** storage **$/MTok/hour**. Gemini 2.5+ explicit discount **90%**; Gemini 2.0 **75%** ([Vertex overview](https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-overview)). Limits: min cache **4,096** (Gemini 3 family), max blob **10 MB**, min TTL **1 minute**, no documented max TTL.

**Semantic cache (application layer, not KV)**

GPTCache: embed query → vector store (Milvus/FAISS/Redis/Qdrant) → similarity evaluator → return prior LLM response. Exact-match caches have low hit rate on paraphrases ([GPTCache](https://gptcache.readthedocs.io/en/stable/index.html); [zilliztech/GPTCache](https://github.com/zilliztech/gptcache)). LangChain `RedisSemanticCache` (`distance_threshold`, `ttl`) ([Category-aware semantic caching](https://gingerlabs.ai/blog/category-aware-semantic-caching)). Semantic hits can return a **wrong** answer if threshold is loose; they also skip the live tool loop — unsafe for agents with side effects.

---

## 2. Token Economics & NFR Metrics

### 2.1 Published latency (not inferred)

| Source | Claim | Scope |
| --- | --- | --- |
| [AWS Bedrock prompt caching product](https://aws.amazon.com/bedrock/prompt-caching/) | Cost **up to 90%** down, latency **up to 85%** down | Supported Bedrock models, marketing max |
| [OpenAI Prompt Caching 101](https://developers.openai.com/cookbook/examples/prompt_caching101) | **Up to 80%** latency reduction for prompts **>10k** tokens | OpenAI API, cached prefix |
| [OpenAI 2024 launch](https://openai.com/index/api-prompt-caching/) | Cached input **50%** off (then-current 4o/o1 prices) | Historical; later models use 90% off on cache reads |
| [nirmalyaghosh/ttft-benchmark](https://github.com/nirmalyaghosh/ttft-benchmark) + [TTFT post, Feb 2026](https://www.nirmalya.net/posts/2026/02/ttft-optimisation-practical-patterns/) | Independent N=20 hits / N=3 misses, shared API | See table below |
| [vLLM PagedAttention](https://arxiv.org/abs/2309.06180) | **2–4×** throughput at matched latency vs FasterTransformer/Orca | Self-hosted |
| [LMCache](https://arxiv.org/html/2510.09665) | **Up to 15×** throughput vs vLLM-only on multi-round QA / doc analysis | Self-hosted + offload |
| [MInference / LLMLingua site](https://llmlingua.com/) | Prefill latency **up to 10×** down on A100 at 1M-token prompts | Research kernel, not hosted API |

**Independent TTFT (shared public API; network dominates small prefixes)** ([TTFT post](https://www.nirmalya.net/posts/2026/02/ttft-optimisation-practical-patterns/)):

| Prefix tokens | Miss mean | Hit P50 | Hit P95 | Measured reduction | Hits |
| --- | --- | --- | --- | --- | --- |
| ~1,500 | 1.015 s | 1.150 s | 2.821 s | −13.3% | 18/20 |
| ~3,000 | 1.404 s | 0.949 s | 1.603 s | 32.4% | 20/20 |
| ~5,000 | 1.732 s | 1.057 s | 1.618 s | 39.0% | 20/20 |
| ~10,000 | 1.379 s | 1.201 s | 1.988 s | 12.9% | 15/20 |
| ~20,000 | 1.486 s | 1.411 s | 1.953 s | 5.0% | 10/20 |

Repo replica (~1.5k–5k): miss 1.657 s → hit P50 1.577 s (4.9%) at 1.5k; miss 1.744 s → hit P50 1.387 s / P95 1.651 s (20.5%) at 5k ([ttft-benchmark README](https://github.com/nirmalyaghosh/ttft-benchmark)). Authors state calculated prefill-only reduction would be 99%+; **measured** reduction is bounded by RTT. [inferred] Dedicated VPC/colocation would move P50 toward the calculated figure; public-API p99 is not a prefill SLA.

> ⚠️ Limited public data available for this dimension. Providers do not publish contractual p50/p95/p99 TTFT SLAs for cache-hit vs miss. Independent measurements above are from a single author’s 2026 benchmark, N≤20, not a multi-region SLO.

Gemini optimization table: caching listed as “faster time-to-first-token”; Flex target **1–15 min**; Batch up to **24 h**; Priority “seconds” ([Gemini optimization](https://ai.google.dev/gemini-api/docs/optimization)).

### 2.2 Current list prices (as of 2026-08-21 fetches)

**Anthropic** ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)):

| Model | Input | 5m write | 1h write | Cache read | Output |
| --- | --- | --- | --- | --- | --- |
| Opus 5 / 4.8 / 4.7 / 4.6 / 4.5 | $5 / MTok | $6.25 | $10 | $0.50 | $25 |
| Sonnet 4.6 / 4.5 | $3 | $3.75 | $6 | $0.30 | $15 |
| Sonnet 5 | $2 | $2.50 | $4 | $0.20 | $10 |
| Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 |
| Fable 5 | $10 | $12.50 | $20 | $1 | $50 |

Sonnet 5 $2/$10 was introductory through 2026-08-31 and is now standard (the planned 2026-09-01 hike to $3/$15 **will not occur**) ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Batch API: **50%** off input and output. Fast mode (Opus 5 / 4.8, first-party only): **$10 / $50** per MTok; cache multipliers stack. US-only `inference_geo` on 4.6+: **1.1×** on all token categories including cache. Claude 4.7+ tokenizer: **~30% more tokens** for the same text vs Sonnet 4.6 and earlier.

Break-even vs uncached (published, not inferred): 5-minute cache pays after **1** hit (1.25 + 0.1 = 1.35 < 2.0); 1-hour pays after **2** hits (2.0 + 0.2 = 2.2 < 3.0) ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

**OpenAI** ([pricing](https://developers.openai.com/api/docs/pricing); [GPT-5.4 launch](https://openai.com/index/introducing-gpt-5-4/); [GPT-4.1 model card](https://developers.openai.com/api/docs/models/gpt-4.1)):

| Model | Input / MTok | Cached input | Cache writes | Output | Notes |
| --- | --- | --- | --- | --- | --- |
| gpt-5.6-sol (short ctx) | $5.00 | $0.50 | $6.25 | $30.00 | Long ctx: $10 / $1 / $12.50 / $45 |
| gpt-5.6-terra (short) | $2.00 | $0.20 | $2.50 | $12.00 | Long: $4 / $0.40 / $5 / $18 |
| gpt-5.6-luna (short) | $0.20 | $0.02 | $0.25 | $1.20 | Long: $0.40 / $0.04 / $0.50 / $1.80 |
| gpt-5.4 | $2.50 | $0.25 | (free write; pre-5.6 family) | $15 | [launch post](https://openai.com/index/introducing-gpt-5-4/) |
| gpt-5.2 | $1.75 | $0.175 | free write | $14 | |
| gpt-4.1 | $2.00 | $0.50 | free write | $8.00 | **1,048,576** context ([model card](https://developers.openai.com/api/docs/models/gpt-4.1)) |

GPT-5.6 write **1.25× is the total rate**, not stacked on a full input charge ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). TrueFoundry: uniform 1.25×/0.10× implies caching stops paying if writes exceed **78.3%** of prefix-touching requests ([TrueFoundry GPT-5.6 cache](https://www.truefoundry.com/blog/gpt-5-6-new-cache-pricing-has-a-break-even-point-and-its-the-same-for-sol-terra-and-luna)). Regional processing: **10%** uplift for eligible models released on/after 2026-03-05 ([OpenAI pricing](https://developers.openai.com/api/docs/pricing)).

**Gemini Developer API** ([pricing](https://ai.google.dev/gemini-api/docs/pricing)):

Gemini 3.6 Flash Standard: input **$1.50**/MTok, output **$7.50**, cached input **$0.15**, storage **$1.00 / 1M tokens / hour**. Priority: input **$2.70**, cached **$0.27**, same $1/h storage. Batch/Flex: input **$0.75**, cached **$0.075**. Gemini 3.5 Flash Standard: input **$1.50**, output **$9.00**, cached **$0.15** + $1/h. Gemini 3.5 Flash-Lite: input **$0.30**, cached **$0.03** + $1/h. Gemini 2.5 Pro context window **1,048,576** in / **65,536** out ([Gemini 2.5 Pro](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro)).

Older forum threads cited Gemini 2.5 Pro storage **$4.50**/MTok/h; Google staff stated it decreased to **$1.00** ([Forum](https://discuss.ai.google.dev/t/context-cache-price-is-too-high-4-50-1-million-tokens-per-hour-storage/6187)). Do not use $4.50 for 2026 planning.

### 2.3 Worked `$ / 1k executions` (calculated from list prices above)

Workload A: **50,000**-token stable prefix + **500**-token unique input + **1,000**-token output. 1,000 sequential turns, prefix reused every time, first turn is a write.

**Sonnet 4.6 (Anthropic 5m cache)**

- Uncached: 1,000 × ((50,500 × $3 + 1,000 × $15) / 1e6) = **$166.50**
- Cached: write (50,000 × $3.75 / 1e6) + unique+out first + 999 × ((50,000 × $0.30 + 500 × $3 + 1,000 × $15) / 1e6) = $0.1875 + $0.0165 + $31.4685 = **$31.67** (≈ **81%** save)
- 1h TTL unused (single write, 999 hits inside 5 min): would cost write $0.30 extra on the first call only → **$31.97**. Use 1h when inter-arrival >5 min.

**gpt-5.6-terra short context**

- Uncached: 1,000 × ((50,500 × $2 + 1,000 × $12) / 1e6) = **$113.00**
- Cached (1 write + 999 reads): (50,000 × $2.50 / 1e6) + first unique/out + 999 × ((50,000 × $0.20 + 500 × $2 + 1,000 × $12) / 1e6) = $0.125 + $0.013 + $21.987 = **$22.13**

**Gemini 3.6 Flash Standard explicit cache, TTL 1 h, 1,000 hits inside that hour**

- Storage: 50,000 / 1e6 × $1.00 × 1 h = **$0.05**
- Create billed at standard input once: 50,000 × $1.50 / 1e6 = **$0.075**
- 1,000 cached reads: 1,000 × (50,000 × $0.15 / 1e6) = **$7.50**
- Unique+output 1,000×: 1,000 × ((500 × $1.50 + 1,000 × $7.50) / 1e6) = **$8.25**
- Total ≈ **$15.88** vs uncached 1,000 × ((50,500 × $1.50 + 1,000 × $7.50) / 1e6) = **$83.25**

**Idle 1h cache that is never read (Gemini)**: you still pay storage. 1M tokens × 24 h × $1 = **$24/day** holding a 1M prefix with zero hits.

### 2.4 Hit rates, TTL, invalidation, prefix stability

| Provider | TTL | Refresh | Isolation | Silent miss causes |
| --- | --- | --- | --- | --- |
| Anthropic | 5m default, 1h opt | On hit, free | Workspace (API/Foundry/Claude-on-AWS); org-level on Bedrock/Vertex ([docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) | Tool schema change; breakpoint 20+ blocks past last write; <min tokens; TTL expiry during long stream; thinking stripped on older models |
| OpenAI GPT-5.6+ | 30m | On reuse, no extra write | Org (historical: “not shared between organizations” ([2024 launch](https://openai.com/index/api-prompt-caching/))) | Missing `prompt_cache_key`; implicit breakpoint on changing suffix; >~15 rpm/key; tool/schema/image change before breakpoint |
| OpenAI pre-5.6 | Best-effort 5–10m idle / up to 1h or 24h retention | Best-effort | Org | Routing to a replica without the prefix |
| Gemini implicit | Load-based, ≤24h Vertex | N/A | Project | No guarantee |
| Gemini explicit | User TTL, default 1h | Update expiration API | Resource name | TTL; delete; model mismatch |
| vLLM APC | Until LRU eviction | N/A | Process / shared block pool | Hash algo change (`sha256` pickle not reproducible); token mismatch |
| Semantic cache | App TTL | N/A | Whatever key you chose | Stale KB; threshold too wide |

Anthropic invalidation table (partial): tool definition changes wipe **tools+system+messages**; `tool_choice` wipes messages only; images in the prompt wipe messages; speed/fast mode wipes system+messages ([invalidation table](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

Bedrock: `CacheReadInputTokens` **do not count toward TPM**; writes do ([Bedrock latency optimization](https://hidekazu-konishi.com/entry/amazon_bedrock_inference_throughput_and_latency_optimization.html); [Bedrock docs](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)). OpenAI GPT-5.6 on Bedrock: cached tokens **do not count** against input-TPM ([Bedrock OpenAI cache](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)). Cross-region inference on Bedrock “may lead to increased cache writes” at high demand.

### 2.5 Throughput multipliers

Prompt caching reduces **prefill** FLOPs, not decode. APC “does not bring performance gain when … the length of the answer is long” ([vLLM APC](https://docs.vllm.ai/en/latest/features/automatic%5Fprefix%5Fcaching/)). [inferred] For a 50k-prefill / 20-token-answer chatbot, cache hit ≈ TPM capacity increase proportional to skipped prefill; for a 2k-prefill / 2k-answer summarizer, decode dominates and cache ROI is mostly **dollar** not latency.

LLMLingua 20× token cut reduces both $ and TTFT linearly with input size, at quality risk (lossy).

---

## 3. Distributed Resilience & State

### 3.1 Durable prompt / session state

Do not store “the prompt” only in GPU KV. Durable layers:

- **Transcript + compaction blocks**: client or Conversations API / ADK `DatabaseSessionService` / `VertexAiSessionService`. In-memory ADK/CrewAI state is **lost on process restart** ([ADK sessions](https://adk.dev/sessions/)).
- **Notes outside the window**: Anthropic memory tool filesystem; OpenAI cookbook profile+notes with reinjection after trim ([Agents SDK personalization](https://developers.openai.com/cookbook/examples/agents_sdk/context_personalization)).
- **Explicit Gemini cache objects**: survive as cloud resources until TTL/delete; implicit Gemini does not.

OpenAI `OpenAIResponsesCompactionSession` wraps a session and calls `responses.compact` after a trigger (default ≥10 non-user items). Do not wrap `OpenAIConversationsSession` with it — two history managers conflict ([Agents SDK sessions](https://openai.github.io/openai-agents-python/sessions/)).

### 3.2 KV offload and prefix cache across replicas

vLLM `OffloadingConnector`: completed GPU blocks DMA (`cudaMemcpyAsync`) to pinned CPU; hits promote back. `prompt_only=true` (default) offloads prefill blocks not decode. Per-request `max_offload_tokens` limits how far into the sequence is worth storing ([KV offloading](https://docs.vllm.ai/en/latest/features/kv_offloading_usage/)). `kv_offloading_backend`: `native` | `lmcache`.

LMCache MP: one `lmcache server` per node, multiple vLLM pods share L1; `STORE`/`RETRIEVE` over ZMQ/CUDA IPC; engine crash does not fate-share the cache ([LMCache MP](https://docs.lmcache.ai/mp/index.html)). Cross-pod reuse is the self-hosted analog of `prompt_cache_key` sticky routing.

OpenAI: requests routed by `prompt_cache_key` then hash of initial prefix ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Without the key, GPT-5.6 matching degrades. [inferred] Multi-region active-active without a shared KV store ≈ cache miss after failover.

SGLang: radix tree is **in-process**; a replica restart is a cold tree unless an external KV connector is added.

### 3.3 Sticky routing and cache stampede

Anthropic: cache not visible to concurrent requests until first response **begins** — thundering herd of N identical first requests = N writes at 1.25× or 2× ([prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Circuit: serialize a “warm” request (`max_tokens: 0` pre-warm is cited by secondary analyses for Anthropic; confirm against current API before relying) then fan out.

OpenAI GPT-5.6: ~15 rpm per `prompt_cache_key` before hit rate falls — partition keys (session-id vs global-system-id) is a capacity control, not just a hit-rate tweak.

Bedrock cross-region: extra writes under load = stampede analog ([Bedrock docs](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)).

### 3.4 Circuit breakers when cache fails

Data-plane fallback is always **full prefill** (correctness-preserving). Control-plane should:

1. Treat `cache_read_input_tokens` / `cached_tokens` = 0 as a signal, not an error.
2. If write amplification (high `cache_write_tokens`, low reads) persists, flip `prompt_cache_options.mode` or strip `cache_control` to stop paying 1.25×/2× for useless writes.
3. Self-hosted: if LMCache/CPU offload errors, vLLM continues from GPU pages only ([inferred] from connector optional design; LMCache paper describes offload as additive).
4. Compaction API 5xx: skip compaction, trim instead, or fail closed if overflow.

> ⚠️ Limited public data available for this dimension. No provider publishes cache-service SLO, replica-level hit-rate dashboards, or mandated circuit-breaker configs. Sticky-routing internals are undocumented beyond `prompt_cache_key` and Anthropic workspace isolation.

---

## 4. Enterprise Security & Governance

### 4.1 Cross-tenant cache isolation

- Anthropic: **never** shared across organizations. Workspace isolation on Claude API, Claude Platform on AWS, Microsoft Foundry. **Bedrock and Google Cloud: organization-level only** — two workspaces in one org/cloud project can share a prefix cache ([prompt caching storage](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Multi-tenant SaaS on Bedrock must not put Tenant A PII in a prefix Tenant B can hit.
- OpenAI 2024: “Prompt caches are not shared between organizations” ([launch](https://openai.com/index/api-prompt-caching/)). `prompt_cache_key` is an affinity hint; do not put secrets in the key if it lands in logs/metrics.
- Semantic caches (Redis/GPTCache) are **your** isolation problem. Default CrewAI memory is not per-user ([CrewAI memory](https://docs.crewai.com/en/concepts/memory)).
- vLLM prefix cache is process-local unless LMCache/Redis is shared — a shared engine for two tenants is a cross-tenant KV leak if prefixes can collide on identical public system prompts (usually acceptable) **or** on tenant documents (not acceptable). Hash includes tokens; identical docs ⇒ shared blocks. That is a feature for cost and a bug for tenancy.

### 4.2 Prompt injection via cached / retrieved context

Cached prefixes are as trusted as the day they were written. If an attacker poisons a RAG document that you then cache for 1 hour, every subsequent session **replays the injection at 0.1× cost**. Controls:

- Classify documents with Prompt Shields **before** cache write ([Azure Prompt Shields](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/quickstart-jailbreak)).
- Spotlighting / XML untrusted tags **inside** the cached blob so the delimiter is part of the prefix (stable) ([Spotlighting](https://arxiv.org/abs/2403.14720)).
- Tool results: pack as `tool_result` data, not concatenated into `system`. Harmony ranks `tool` below `user` and `developer`.
- Anthropic classifier + RL; still “far from a solved problem” ([Anthropic](https://www.anthropic.com/research/prompt-injection-defenses)).
- Semantic cache: a paraphrased jailbreak can hit a cached benign answer (availability) or a cached malicious one (integrity). Key must include policy version and corpus version ([GPTCache guide pattern](https://bhavishyapandit9.substack.com/p/gptcache-a-practical-guide)).

### 4.3 PII in long context

1M-token windows invite dumping tickets, EHRs, or mailboxes. Azure guardrails PII detection requires API **2025-01-01-preview** or later ([guardrails](https://learn.microsoft.com/en-us/azure/foundry/guardrails/how-to-create-guardrails)). Compaction summaries and memory-tool files are **new PII stores**; they outlive the chat UI. OpenAI compact items are encrypted/opaque — better for ZDR logs, worse for DLP inspection ([Compaction](https://developers.openai.com/api/docs/guides/compaction)). Anthropic `pause_after_compaction` exists specifically so you can audit the summary before continuing ([compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)).

Prompt cache is ephemeral (minutes–hours) but still in provider memory. Anthropic points ZDR questions at the data-retention doc from the caching page ([prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). OpenAI cookbook: caching eligible for ZDR because “no data is stored” in the product sense of training retention ([Prompt Caching 101](https://developers.openai.com/cookbook/examples/prompt_caching101)) — this does not mean GPU KV is empty during TTL.

### 4.4 Audit of what entered the window

Minimum audit fields per model call: rendered role list, token counts by segment (tools / system / memory / RAG ids / messages / scratchpad), cache breakpoint hashes or `cache_read` vs `cache_write` vs uncached, compaction trigger, tool schema version, RAG document ids+checksums, Prompt Shield `attackDetected`. LangGraph recommendation: store **full** messages for UI and a separate key for the LLM-facing window ([LangMem summarization](https://langchain-ai.github.io/langmem/guides/summarization/)). Without that split, auditors cannot reconstruct what the model actually saw after trim.

OWASP: constrain role, validate output, filter, segregate untrusted content ([OWASP LLM01](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)).

---

## 5. Production Failure Modes

### 5.1 Lost-in-the-middle

Liu et al.: multi-document QA and JSON key-value retrieval; accuracy is U-shaped — beginning and end beat the middle, including on long-context models ([arXiv:2307.03172](https://arxiv.org/abs/2307.03172); [TACL 2024](https://aclanthology.org/2024.tacl-1.9.pdf); [code](https://github.com/nelson-liu/lost-in-the-middle/)). Mitigation: put the query last (Anthropic **≤30%** quality lift); reduce k in RAG rather than stuffing; LongLLMLingua reorders by query relevance ([ACL 2024](https://aclanthology.org/2024.acl-long.91)).

### 5.2 Context rot

Chroma technical report (14 Jul 2025), Hong / Troynikov / Huber, **18** models (GPT-4.1, Claude 4, Gemini 2.5, Qwen3, …): reliability drops as input length grows even on simple retrieval/replication; lexical NIAH is too easy; semantic needle–question similarity and distractors hurt more; **all 18** models did better on shuffled haystacks than coherent essays; LongMemEval_s filtered to **306** prompts averaging **~113k** tokens, full-history vs focused prompt gap is large ([Chroma Context Rot](https://www.trychroma.com/research/context-rot); [GitHub toolkit](https://github.com/chroma-core/context-rot); [research.trychroma.com](https://research.trychroma.com/context-rot)).

Anthropic: every extra token depletes an “attention budget”; n² pairwise attention; position-interpolation models degrade token-position understanding ([engineering blog](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

Du et al. Findings EMNLP 2025: **even with perfect retrieval**, length alone hurts; GPT-4o + RULER recite-then-reason **up to +4%** ([paper](https://aclanthology.org/anthology-files/pdf/findings/2025.findings-emnlp.1264.pdf)).

### 5.3 Cache stampede / prefix mismatch / stale schema

- Stampede: see §3.3.
- Prefix mismatch: non-deterministic JSON key order in tool schemas; per-request timestamps; locale; floating ISO times; `instructions` that include “today’s date” at the *front*. OpenAI docs: change after breakpoint is OK; change before is a miss ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- **Stale cache after tool schema change**: Anthropic explicitly invalidates the **entire** cache when tool names/descriptions/parameters change ([invalidation](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Agents that hot-reload tools every request never hit. Version tools; pin schema in the prefix; put per-tenant tool *availability* behind `tool_choice` (messages-only invalidation) when possible.
- Lookback miss: growing conversation moves explicit breakpoint **>20 blocks** past last write → sudden 100% miss ([20-block window](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Fix: second breakpoint on a stable interior block from turn 1, or automatic caching.
- GPT-5.6 implicit mode: latest message in the breakpoint → write storm; cost *higher* than no cache (1.25× every turn).

### 5.4 Over-compression information loss

Anthropic: aggressive compaction drops “subtle but critical context whose importance only becomes apparent later”; tune summarizer for recall first, then precision ([engineering blog](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). LangMem: if summarized span > `max_tokens`, older tokens in that span are **never even shown to the summarizer** ([API](https://langchain-ai.github.io/langmem/reference/short_term/)). LLMLingua is extractive but can drop negation/numbers at high ratio; 20× is a research max, not a default SLA. Abstractive summaries hallucinate constraints (wrong IDs, inverted polarity). Encrypted OpenAI compact items cannot be human-QA’d.

### 5.5 Context overflow

Hard failures: API 400 context-length; ADK in-memory growth; CrewAI `respect_context_window` (when enabled) auto-summarizes vs halt. Anthropic compaction min trigger **50k** — windows smaller than that cannot use server compact ([compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)). OpenAI compact input must itself fit the context window — you cannot compact a 1.1M token dump on a 1M model ([compaction guide](https://developers.openai.com/api/docs/guides/compaction)).

Thinking blocks on older Claude: adding a non-tool user message **strips prior thinking** and busts the message cache; Opus 4.5+ / Sonnet 4.6+ keep thinking by default ([thinking + cache](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

### 5.6 Semantic-cache correctness failures

A hit on cosine similarity is not a hit on policy, time, or user. TTL + corpus version + `user_id` in the cache key are mandatory for enterprise. Streaming: GPTCache “partial” streaming support ([Maxim 2026 roundup](https://www.getmaxim.ai/articles/top-semantic-caching-solutions-for-ai-applications-in-2026/)).

---

## 6. Enterprise System Design Scenarios

### 6.1 Scale benchmarks and case studies

| Scenario | Window | Technique mix | Published signal |
| --- | --- | --- | --- |
| Shared system+tools chatbot | 4k–32k prefix, short answers | Prompt cache 5m/30m, static-first packing | Bedrock ≤85% latency, ≤90% input $; OpenAI cookbook ≤80% latency >10k |
| Agentic tool loop | Growing 20k–200k | Automatic cache + tool_result breakpoint; 1h TTL if tools >5 min; `clear_tool_uses` at 100k | Anthropic tool-loop caching examples; compaction at 150k |
| Long-horizon coding/research | Multi-hour | Compaction + memory tool + sub-agents returning 1–2k | Anthropic: Memory+context editing **+39%** agent search vs baseline; context editing alone **+29%**; 100-round web search **−84%** tokens ([The Decoder citing Anthropic](https://the-decoder.com/anthropic-claims-context-engineering-beats-prompt-engineering-when-managing-ai-agents/)) |
| RAG over a pinned corpus | 50k–1M docs | Explicit Gemini cache or Anthropic document prefix; or LLMLingua 4–20× then cache the compressed blob | Vertex 90% cached-input discount + storage; LongLLMLingua +17.1% at 4× |
| Self-hosted multi-tenant | 8k–128k | vLLM APC + sticky sessions; LMCache for replica share; **no** shared KV for tenant docs | PagedAttention 2–4×; LMCache ≤15× |
| Conversational memory 113k | Full vs focused | Do **not** dump LongMemEval-style full history; retrieve | Chroma 18-model study |

### 6.2 Trade-off matrix

| Lever | Latency | $ | Quality | Prefix stability | Security |
| --- | --- | --- | --- | --- | --- |
| Prompt/KV cache | High win on prefill | 0.1× reads; 1.25–2× writes | Neutral (lossless) | Fragile | Isolation + poison persistence |
| Trim | Lowers TTFT | Linear with tokens | Drops facts | Breaks then restabilizes | Smaller leak surface |
| Abstractive compact | Lowers TTFT | Linear | Lossy; needs audit | New prefix | Summary is a PII store |
| LLMLingua extractive | Lowers TTFT | Linear | High ratio risk | New prefix | May drop safety text |
| Semantic cache | Sub-ms on hit | Avoids model | Wrong-answer risk | N/A | Cross-user if mis-keyed |
| Sub-agents | Parallel TTFT | More calls, less parent context | High if summaries faithful | Parent prefix stable | Broader tool blast radius |
| Stuff 1M context | Slow prefill, rot | Full input $ | Chroma/Liu degradation | Easy to cache if static | Max PII in window |

### 6.3 Capacity planning 128k–1M+

**KV bytes (self-hosted)**  
`2 × n_layers × n_kv_heads × head_dim × seq × batch × bytes` (K and V). Llama 3.1 70B GQA example in secondary literature: 8k seq × batch 32 is already multi-tens of GB; **131k context ≈ 43 GB KV per request** at FP16-class width — larger than FP8 weights ([KV cache guides citing Kwon](https://blog.premai.io/kv-cache-optimization-pagedattention-prefix-caching-memory-management/); formula from PagedAttention setting). GQA/MLA exist to shrink this (see module 01). Prefix sharing amortizes the 128k system prompt **once per replica**, not once per user, iff the prefix is identical.

**Hosted 1M windows**

- GPT-4.1: 1M context, cached input **$0.50**/MTok vs $2 input ([model card](https://developers.openai.com/api/docs/models/gpt-4.1)).
- Gemini 2.5 Pro: 1,048,576 / 65,536 ([docs](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro)); Gemini 2.X family “>1 million tokens” ([Gemini 2.5 paper](https://arxiv.org/pdf/2507.06261)).
- Stuffing 1M tokens uncached on Gemini 3.6 Flash Standard: 1e6 × $1.50 / 1e6 = **$1.50/request** input before output. Explicit cache: **$0.15**/request input + **$1.00/hour** storage for that 1M. Break-even vs uncached: ignore storage, 1.50 vs 0.15 → cache wins on the 2nd call; storage of $1/h means you need **≥ ~0.67 hits/hour** to beat “pay full input each time” if you keep the cache up all hour [calculated]: 1.50n vs 1.50 (create) + 0.15n + 1.00 → n > 1.18 for a 1-hour hold with one create. For multi-hour holds, storage dominates unless QPS is high.

**Token budgeter policy (production default [inferred from vendor triggers])**

1. Target **<50%** of advertised window for *working* context (Chroma/Veseli-style rot; Anthropic attention budget). Du et al. argue shorter is better even with perfect retrieval.
2. Server compact at 50–150k on Claude; `clear_tool_uses` at 100k; OpenAI `compact_threshold` similarly below the hard cap.
3. RAG k such that docs sit in a cached prefix **or** a tool fetch, not both stuffed and rotated (rotation kills cache).
4. Few-shot: 3–10 high-quality XML/markdown shots in the stable prefix, not 100 shots that push the query into the U-curve trough (GPT-3’s 10–100 was for 2k windows).
5. Monitor `cache_read / (cache_read + cache_write + uncached)` per `prompt_cache_key` / workspace. Alert on write/read inversion after deploys (schema change).
6. TPM: prefer caches that **zero-rate** cached tokens against quota (Bedrock/OpenAI GPT-5.6 as documented) when the bottleneck is throttling not dollars.

### 6.4 Reference architecture (control plane)

```
Ingress → Prompt Shields / PII
       → Prompt compiler (roles, XML untrusted tags, tool pack)
       → Token budgeter (trim | clear tools | compact | LLMLingua RAG)
       → Cache manager (breakpoints, prompt_cache_key / cache_control / Gemini cache id)
       → Model (stream) → tool runtime → pack tool_result (optional cache_control)
       → Session store (full transcript) + scratchpad/memory (ADK state / memory tool)
       → Metrics: TTFT, cached_tokens, cache_write_tokens, overflow, Shield flags
```

Failover: cache miss → full prefill; compaction fail → trim; Shields attackDetected → do not write cache, do not execute tools.

### 6.5 Coverage check (sub-bullets × dimensions)

| | Topology | Economics | Resilience | Security | Failures | Design |
| --- | --- | --- | --- | --- | --- | --- |
| Prompting (roles, few-shot, XML, tool pack, injection) | §1.2 | few-shot in prefix $ | instructions vs session | §4.2 Spotlighting/OWASP | schema/instructions bust cache | few-shot count vs U-curve |
| Context mgmt (pack, scratch, trim, session/request, multi-turn) | §1.3 | TPM vs cached | durable session vs KV | audit split UI vs LLM window | overflow, thinking strip | budgeter policy |
| Compression (summ, hierarchical, LLMLingua, extract/abstract, lossy/lossless) | §1.4 | 20× / 4× papers | compact 5xx fallback | compact PII / pause_after | over-compression | trade-off matrix |
| Caching (prefix/KV, APIs, semantic, TTL, breakpoints) | §1.5 | §2 prices & $/1k | offload, stampede, sticky | tenant isolation | stampede, 20-block, implicit writes | 128k–1M capacity |

---

## Sources

- [1] https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — Anthropic: attention budget, context rot, compaction, notes, sub-agents (1–2k returns)
- [2] https://platform.claude.com/docs/en/build-with-claude/prompt-caching — Anthropic prompt cache: auto/explicit, 4 breakpoints, 20-block lookback, TTL, invalidation, isolation, minima
- [3] https://platform.claude.com/docs/en/about-claude/pricing — Anthropic 2026 list prices, 1.25×/2×/0.1×, batch 50%, fast mode, 1.1× residency
- [4] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices — XML, few-shot, query-at-end ≤30%, system role
- [5] https://platform.claude.com/docs/en/build-with-claude/compaction — `compact_20260112`, trigger 150k/min 50k, pause_after_compaction
- [6] https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools — Compaction vs `clear_tool_uses_20250919` vs `memory_20250818`
- [7] https://www.anthropic.com/research/prompt-injection-defenses — RL + classifiers; browser-agent ASR ~1% Opus 4.5 with safeguards
- [8] https://developers.openai.com/api/docs/guides/prompt-caching — OpenAI GPT-5.6 breakpoints, 30m TTL, 1.25× writes, 0.1× reads, prompt_cache_key, ~15 rpm
- [9] https://developers.openai.com/cookbook/examples/prompt_caching101 — Auto cache >1024 tokens, ≤80% latency >10k, org isolation, ZDR note
- [10] https://openai.com/index/api-prompt-caching/ — 2024 launch: 50% off, 5–10 min, org isolation, 1024+128 increments
- [11] https://developers.openai.com/api/docs/pricing — gpt-5.6-sol/terra/luna cached and write rates, long-context split
- [12] https://openai.com/index/introducing-gpt-5-4/ — gpt-5.4 $2.50 / $0.25 cached / $15 out
- [13] https://developers.openai.com/api/docs/models/gpt-4.1 — GPT-4.1 1M context, $2 / $0.50 cached / $8
- [14] https://developers.openai.com/api/docs/guides/compaction — Server compact + `/responses/compact`, user text verbatim, encrypted item, ZDR
- [15] https://developers.openai.com/api/reference/resources/responses/methods/create/ — developer/system > user; prompt_cache_breakpoint on input blocks
- [16] https://developers.openai.com/api/reference/resources/chat — developer replaces system; cache breakpoint on developer/system/user parts
- [17] https://developers.openai.com/cookbook/articles/openai-harmony — Role hierarchy system>developer>user>assistant>tool
- [18] https://openai.github.io/openai-agents-python/context/ — RunContextWrapper vs LLM-visible context
- [19] https://openai.github.io/openai-agents-python/sessions/ — Session implementations, compaction wrapper conflict
- [20] https://developers.openai.com/api/docs/guides/agents/running-agents — session vs conversationId vs previous_response_id
- [21] https://developers.openai.com/cookbook/examples/agents_sdk/context_personalization — Trim + memory reinjection
- [22] https://ai.google.dev/gemini-api/docs/generate-content/caching — Implicit vs explicit, min tokens 2048/4096, default TTL 1h
- [23] https://ai.google.dev/gemini-api/docs/pricing — Gemini 3.6/3.5 Flash cached $ and $1/MTok/h storage
- [24] https://ai.google.dev/gemini-api/docs/optimization — Cache as TTFT lever; Flex 1–15 min; batch 24h
- [25] https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro — 1,048,576 / 65,536 limits
- [26] https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-context-caching — 90% discount; implicit ≤24h; no implicit storage fee
- [27] https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-overview — Gemini 2.0 75% vs 2.5+ 90%; 10 MB; min TTL 1 min
- [28] https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html — Checkpoints, per-model minima, 4 checkpoints, cross-region write increase, GPT-5.6 30m
- [29] https://aws.amazon.com/bedrock/prompt-caching/ — Up to 90% cost, 85% latency
- [30] https://aws.amazon.com/blogs/machine-learning/effectively-use-prompt-caching-on-amazon-bedrock/ — Static prefix, exact match, checkpoint after static
- [31] https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/prompt-caching — Azure OpenAI GPT-5.6 breakpoints, 30m, 4 writes, cached_tokens
- [32] https://learn.microsoft.com/en-us/azure/foundry/guardrails/how-to-create-guardrails — Prompt Shields, PII on 2025-01-01-preview, `<documents>` delimiter
- [33] https://learn.microsoft.com/en-us/azure/ai-services/content-safety/quickstart-jailbreak — userPromptAnalysis / documentsAnalysis attackDetected
- [34] https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/general-availability-of-prompt-shields-in-azure-ai-content-safety-and-azure-open/4235560 — Prompt Shields GA
- [35] https://arxiv.org/abs/2403.14720 — Spotlighting; ASR >50% → <2%
- [36] https://www.microsoft.com/en-us/msrc/blog/2025/07/how-microsoft-defends-against-indirect-prompt-injection-attacks — Delimit / datamark / encode in production
- [37] https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf — LLM01:2025 Prompt Injection
- [38] https://www.mdpi.com/2078-2489/17/1/54 — Review: delimiters bypassable; architectural limitation
- [39] https://docs.langchain.com/oss/python/langgraph/add-memory — trim_messages vs summarize vs checkpoints
- [40] https://langchain-ai.github.io/langmem/guides/summarization/ — SummarizationNode; separate UI vs LLM keys
- [41] https://langchain-ai.github.io/langmem/reference/short_term/ — max_tokens_before_summary behavior
- [42] https://adk.dev/sessions/ — Session vs State vs Memory
- [43] https://adk.dev/sessions/state/ — Scratchpad prefixes user:/app:
- [44] https://docs.crewai.com/en/concepts/memory — Unified Memory, extract_memories, scoring weights
- [45] https://docs.vllm.ai/en/latest/features/automatic%5Fprefix%5Fcaching/ — APC enablement and decode-phase limit
- [46] https://docs.vllm.ai/en/latest/design/prefix%5Fcaching/ — Hash-based blocks, eviction
- [47] https://docs.vllm.ai/en/latest/features/kv_offloading_usage/ — CPU/tiered offload, prompt_only, max_offload_tokens
- [48] https://github.com/vllm-project/vllm/blob/bebfe55b/vllm/config/cache.py — enable_prefix_caching default, hash algos, lmcache backend
- [49] https://arxiv.org/abs/2309.06180 — PagedAttention; 2–4× throughput
- [50] https://proceedings.neurips.cc/paper_files/paper/2024/file/724be4472168f31ba1c9ac630f15dec8-Paper-Conference.pdf — SGLang RadixAttention
- [51] https://mintlify.wiki/sgl-project/sglang/concepts/radix-attention — Radix tree, page_size alignment
- [52] https://arxiv.org/html/2510.09665 — LMCache; ≤15× throughput; no fate-share
- [53] https://docs.vllm.ai/en/latest/examples/disaggregated/lmcache/ — LMCacheMPConnector
- [54] https://docs.lmcache.ai/mp/index.html — Shared L1 across pods
- [55] https://aclanthology.org/2023.emnlp-main.825/ — LLMLingua; 20×
- [56] https://aclanthology.org/2024.acl-long.91 — LongLLMLingua; +17.1% at 4×
- [57] https://llmlingua.com/ — Series overview; LLMLingua-2 3–6× faster; LangChain/LlamaIndex
- [58] https://github.com/microsoft/LLMLingua — Implementation
- [59] https://arxiv.org/abs/2307.03172 — Lost in the Middle
- [60] https://www.trychroma.com/research/context-rot — Context Rot; 18 models; LongMemEval ~113k
- [61] https://github.com/chroma-core/context-rot — Replication toolkit
- [62] https://arxiv.org/abs/2005.14165 — Few-shot / in-context learning (GPT-3)
- [63] https://github.com/anthropics/anthropic-sdk-python/blob/d2f6543e/src/anthropic/types/tool_result_block_param.py — tool_result + cache_control
- [64] https://www.nirmalya.net/posts/2026/02/ttft-optimisation-practical-patterns/ — Measured TTFT p50/p95 cache hit vs miss
- [65] https://github.com/nirmalyaghosh/ttft-benchmark — Methodology N=20
- [66] https://www.truefoundry.com/blog/gpt-5-6-new-cache-pricing-has-a-break-even-point-and-its-the-same-for-sol-terra-and-luna — 78.3% write-share break-even
- [67] https://the-decoder.com/anthropic-claims-context-engineering-beats-prompt-engineering-when-managing-ai-agents/ — +39% / +29% / −84% tokens (Anthropic internal)
- [68] https://aclanthology.org/anthology-files/pdf/findings/2025.findings-emnlp.1264.pdf — Length hurts despite perfect retrieval; +4% recite-then-reason
- [69] https://gptcache.readthedocs.io/en/stable/index.html — Semantic cache architecture
- [70] https://github.com/zilliztech/gptcache — GPTCache backends and eviction
- [71] https://arxiv.org/pdf/2507.06261 — Gemini 2.5 >1M context family
- [72] https://docs.parallel.ai/integrations/anthropic-tool-calling — tool_result must come first in the user message
