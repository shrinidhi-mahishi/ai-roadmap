# Research: LLM Concepts - A Ultimate Deep Dive

**Date researched**: 2026-09-30
**Sources consulted**: 32

## 1. System Topology & Mechanics

An LLM is an autoregressive next-token predictor: given a token sequence, it repeatedly samples \(P(x_t \mid x_{<t})\) until an end-of-sequence token or a max-output limit ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); [Vaswani et al., 2017](https://arxiv.org/abs/1706.03762)). Text never enters the network as characters; a **tokenizer** maps strings → integer IDs, then an **embedding** layer maps each ID to a dense vector in a learned latent space (conceptual: nearby vectors encode related meaning; vector DBs / ANN indices are out of scope for this topic) ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); [OpenAI tiktoken cookbook](https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb)).

### Transformer core (foundational topology)

Vaswani et al. replace recurrence/convolution with stacked self-attention + position-wise feed-forward layers. The original encoder–decoder Transformer uses \(N=6\) identical layers per stack, \(d_{\text{model}}=512\), residual connections with LayerNorm around each sub-layer, multi-head attention, and sinusoidal positional encodings. Decoder self-attention is causally masked so position \(i\) cannot attend to future positions—preserving autoregression ([Vaswani et al., 2017 — arXiv](https://arxiv.org/abs/1706.03762); [NeurIPS PDF](https://proceedings.neurips.cc/paper_files/paper/2017/file/3f5ee243547dee91fbd053c1c4a845aa-Paper.pdf)). Self-attention over sequence length \(n\) and head dimension \(d\) is \(O(n^2 d)\) compute and memory for the attention matrix—this quadratic dependence is why context length is both a product feature and a serving cost driver ([Vaswani et al., 2017](https://arxiv.org/abs/1706.03762)).

Modern chat LLMs are typically **decoder-only** stacks trained with next-token loss, then adapted (fine-tune / RLHF / preference optimization) from a **base model** into an **instruct/aligned** assistant ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).

### Inference control plane vs. data plane

| Plane | Responsibility | Concrete components |
| --- | --- | --- |
| **Control plane** | Auth, quotas, routing, model selection, prompt-cache affinity, policy | API gateway; org/project rate limits (RPM/TPM); `prompt_cache_key` sticky routing; system-prompt / safety filters ([OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)) |
| **Data plane** | Tokenization → prefill → decode loop → sampling → stream | GPU/TPU workers; KV-cache memory manager; continuous/iteration-level batching ([vLLM PagedAttention paper](https://arxiv.org/abs/2309.06180); [vLLM blog](https://vllm.ai/blog/2023-06-20-vllm)) |

**Prefill (prompt processing)** computes K/V for the full prompt in parallel across prompt tokens. **Decode** generates one token at a time; each step reuses the **KV cache** so prior tokens are not recomputed ([vLLM paper](https://arxiv.org/abs/2309.06180); [System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)). Latency decomposes into **TTFT** (time-to-first-token ≈ prefill + scheduling) and **inter-token latency / tokens-per-second** (decode throughput) ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).

### Prompt assembly topology

Enterprise requests are not a single string: **system** instructions (role, constraints) + **user** content + optional tool schemas + conversation history all share one **context window**—the max tokens the model can attend over in one forward pass, including the tokens it is about to generate ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); [Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)). Prompting patterns—zero-shot, few-shot, chain-of-thought—are control-plane steering of that window, not separate model architectures ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); [Anthropic prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices); [Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### Serving topology (open-source reference)

vLLM’s **PagedAttention** treats KV cache like OS virtual memory: fixed-size blocks, logical→physical block tables, on-demand allocation, and copy-on-write sharing for parallel samples / beam search. On LLaMA-13B, a single sequence’s KV cache can reach ~**1.7 GB**; naive contiguous allocation wastes **60–80%** of KV memory to fragmentation/over-reservation, while paging cuts waste to **&lt;4%** and enables larger batches ([vLLM blog](https://vllm.ai/blog/2023-06-20-vllm); [PagedAttention paper](https://arxiv.org/abs/2309.06180)). Published serving gains: up to **24×** throughput vs HuggingFace Transformers and **~3.5×** vs TGI (blog microbenchmarks); **2–4×** vs FasterTransformer / Orca at comparable latency (SOSP paper) ([vLLM blog](https://vllm.ai/blog/2023-06-20-vllm); [PagedAttention paper](https://arxiv.org/abs/2309.06180)).

### Sampling as the final execution stage

After logits are produced, **temperature** rescales the distribution before sampling; **top-p (nucleus)** keeps the smallest set of tokens whose cumulative probability ≥ \(p\). OpenAI documents `temperature` ∈ [0, 2] and recommends altering **either** temperature **or** `top_p`, not both; Anthropic similarly advises using one of temperature / top_p (default temperature 1.0 on Completions-era docs; even temperature 0.0 is not fully deterministic) ([OpenAI Chat Completions reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create/); [Anthropic Completions API](https://github.com/anthropics/anthropic-sdk-python/blob/49d639a6/src/anthropic/resources/completions.py)).

## 2. Token Economics & NFR Metrics

### Tokenization cost basis (vendor-specific)

- **OpenAI**: count with **tiktoken** (same family as production tokenizers) before packing batches against TPM ([OpenAI tiktoken cookbook](https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb); [OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).
- **Anthropic**: **do not use tiktoken**; Claude’s tokenizer differs and tiktoken undercounts Claude by ~**15–20%** on typical text (worse on code / non-English). Use `POST /v1/messages/count_tokens` with the **same model ID** as inference. Claude 4.7+ / Mythos Preview use a newer tokenizer producing ~**30% more tokens** for the same text vs earlier Claude tokenizers ([Anthropic Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting); [Anthropic pricing — tokenizer note](https://platform.claude.com/docs/en/about-claude/pricing)).

### Published API prices (verified 2026-09-30)

Prices are **USD per 1M tokens (MTok)** unless noted. Always re-check vendor pages before capacity planning—flagship SKUs change.

**OpenAI (selected, Standard tier — from model/pricing docs & Prompt Caching 201):**

| Model | Input / MTok | Cached input / MTok | Output / MTok | Context (docs) |
| --- | --- | --- | --- | --- |
| gpt-4o | $2.50 | $1.25 (50% off) | $10.00 | 128K ([GPT-4o model](https://developers.openai.com/api/docs/models/gpt-4o); [Prompt Caching 201](https://developers.openai.com/cookbook/examples/prompt_caching_201)) |
| gpt-4.1 | $2.00 | $0.50 (75% off) | $8.00 | ~1.05M ([GPT-4.1 model](https://developers.openai.com/api/docs/models/gpt-4.1); [Prompt Caching 201](https://developers.openai.com/cookbook/examples/prompt_caching_201)) |
| gpt-4.1-nano | $0.10 | $0.025 | $0.40 | ~1.05M ([GPT-4.1 nano](https://developers.openai.com/api/docs/models/gpt-4.1-nano)) |
| gpt-6-astra (short context, Standard) | $10.00 | $1.00 | $50.00 (+ cache write $12.50) | long-context rates 2× on listed table ([OpenAI Pricing](https://developers.openai.com/api/docs/pricing)) |
| gpt-6.1-sol / gpt-6-sol (short, Standard) | $2.00 | $0.20 | $10.00 (+ cache write $2.50) | long-context 2× ([OpenAI Pricing](https://developers.openai.com/api/docs/pricing)) |
| gpt-6-luna (short, Standard) | $0.10 | $0.01 | $0.50 (+ cache write $0.125) | long-context 2× ([OpenAI Pricing](https://developers.openai.com/api/docs/pricing)) |

**Anthropic Claude API (selected, Global Standard):**

| Model | Input | 5m cache write | 1h cache write | Cache hit | Output |
| --- | --- | --- | --- | --- | --- |
| Claude Sonnet 5 / 5.5 | $2 / MTok | $2.50 | $4 | $0.20 | $10 / MTok |
| Claude Opus 5 / 4.x (listed Opus 5–4.5) | $5 | $6.25 | $10 | $0.50 | $25 |
| Claude Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 |
| Claude Fable 5.1 | $10 | $12.50 | $20 | $0.25 (0.025×) | $50 |

([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Cache multipliers: **1.25×** base input for 5-minute writes, **2×** for 1-hour writes, **0.1×** for hits (lower on some Fable/Mythos/Opus 5.5 SKUs). Batch API is **50%** off input and output. Fast mode (Opus family, research preview): e.g. Opus 5 at **$10 / $50** per MTok input/output ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

### Worked cost formulas

Let \(I\) = input tokens, \(O\) = output tokens, \(C_i, C_o\) = $/token input/output, \(f_{\text{cache}}\) = fraction of input billed at cached rate, \(C_{\text{cache}}\) = cached input $/token.

\[
\text{Cost} = I\cdot\big((1-f_{\text{cache}})C_i + f_{\text{cache}}C_{\text{cache}}\big) + O\cdot C_o
\]

**Example A — GPT-4.1, 100K input + 10K output, no cache:**  
\(100\text{K}/10^6 \times \$2 + 10\text{K}/10^6 \times \$8 = \$0.20 + \$0.08 = \$0.28\) ([GPT-4.1 model](https://developers.openai.com/api/docs/models/gpt-4.1)).

**Example B — Claude Sonnet 5, same shape, no cache:**  
\(0.1 \times \$2 + 0.01 \times \$10 = \$0.20 + \$0.10 = \$0.30\) ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

**Example C — Sonnet 5 with 80K of 100K input as 5m cache hits (after prior write amortized elsewhere):**  
Uncached 20K @ $2 + hits 80K @ $0.20 + out 10K @ $10 → \(0.02\times2 + 0.08\times0.20 + 0.01\times10 = \$0.04 + \$0.016 + \$0.10 = \$0.156\) ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). [inferred] Payback for a 5m write (1.25×) occurs after roughly **one** full cache read of that prefix; 1h write (2×) after roughly **two** reads—matching Anthropic’s documented break-even guidance.

**Per 1k executions** (same Example A shape): \(1000 \times \$0.28 = \$280\) on GPT-4.1 without caching. [inferred] At 75% cache hit on the 100K prefix ($0.50/MTok cached), input ≈ \(25\text{K}\times\$2 + 75\text{K}\times\$0.50\) / MTok = $0.0875 + output $0.08 → ~$0.1675/call → ~$167.5 / 1k executions.

### Prompt caching NFRs

- **OpenAI**: automatic for prompts ≥ **1,024** tokens (`gpt-4o` and newer); exact prefix matching; place static instructions/tools first; optional `prompt_cache_key`. GPT-5.6+: cache writes **1.25×** uncached input; reads **0.1×**; default TTL **30m**. Cached tokens still count toward **TPM**. ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- **Anthropic**: explicit `cache_control` (automatic or breakpoints); cached prefixes still **consume context window** capacity—caching changes price, not window occupancy ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows); [Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

### Throughput / rate limits (published)

OpenAI enforces org/project **RPM, TPM, RPD, TPD** (first limit hit wins). Example tier table (flagship families as documented):

| Tier | Models (Astra/Sol/Terra) | RPM | TPM |
| --- | --- | --- | --- |
| Build | Astra, Sol, Terra | 5,000 | 1,000,000 |
| Launch | Astra, Sol, Terra | 10,000 | 4,000,000 |
| Grow | Astra, Sol, Terra | 15,000 | 40,000,000 |
| Grow | Luna | 30,000 | 180,000,000 |

([OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). Ramp guidance: after ~**1M input TPM**, increase traffic by ≤ **50% every 15 minutes** or risk `429` / `slow_down` even under nominal RPM/TPM ([OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). Anthropic token-counting API has separate RPM tiers (Start 5k / Build 10k / Scale 20k) independent of message creation ([Anthropic Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)).

### Latency SLAs

> ⚠️ Limited public data available for this dimension. Public **p50/p95/p99 TTFT** for standard chat APIs is not published as a universal SLA. OpenAI Fast/Priority mode documentation has advertised streaming SLAs such as **99% of requests &gt; 80 tokens/sec** (model-dependent; premium pricing) ([OpenAI Fast mode](https://openai.com/api-fast-mode/)). Self-hosted vLLM reports relative throughput, not absolute TTFT SLOs ([vLLM blog](https://vllm.ai/blog/2023-06-20-vllm)).

[inferred] Architect planning should treat TTFT as prefill-bound (\(O(n)\)–\(O(n^2)\) attention over prompt length) and steady-state TPS as decode/batch/KV-memory bound.

## 3. Distributed Resilience & State

### KV cache as the hot state

During decode, **KV cache is the per-request working set**: ~30% of A100-40GB for a 13B model in the vLLM paper’s memory breakdown, vs ~65% model weights ([PagedAttention paper](https://arxiv.org/abs/2309.06180)). Loss of a worker mid-decode means that request’s KV is gone unless the serving stack checkpoints or can re-prefill. [inferred] Re-prefill from the original prompt (+ partial completion) is the usual recovery; it burns TTFT again and re-bills prompt tokens on hosted APIs.

### Checkpointing / replay semantics (inference layer)

| Mechanism | What is persisted | Granularity | Replay |
| --- | --- | --- | --- |
| **KV blocks (vLLM)** | Attention K/V tensors | Fixed token blocks; refcounted; CoW for shared prefixes | Swap/evict under memory pressure; resume by remapping blocks ([PagedAttention paper](https://arxiv.org/abs/2309.06180); [vLLM Paged Attention docs](https://docs.vllm.ai/en/stable/design/paged_attention/)) |
| **Prompt cache (vendor)** | Prefix activations / billed reuse of prefix compute | Exact token prefix (≥1,024 tokens OpenAI; Anthropic breakpoints) | Hit → cheaper/faster prefill; miss → full prefill ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)) |
| **Conversation compaction** | Summarized earlier turns (server-side) | Multi-turn history | Lossy compress to stay under window ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)) |

> ⚠️ Limited public data available for this dimension. Classic **Temporal / Kafka / event-sourced** orchestration is an application concern above the model API; vendor docs do not expose distributed locks or leader election for KV placement. Patterns below are inference-native resilience, not workflow engines.

### Back-pressure & circuit-breaker analogues

- **Token-bucket / sliding-window rate limits** at org/project scope; `Retry-After` + exponential backoff with jitter on `429` ([OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).
- **`slow_down` vs `server_is_overloaded`**: distinguish ramp-rate throttling (`429`) from model overload (`503`) ([OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).
- **Iteration-level / continuous batching**: admit new requests between decode steps rather than waiting for whole-batch completion—raises GPU util but couples tenants’ p99 to batch composition ([PagedAttention paper](https://arxiv.org/abs/2309.06180)).
- **Preemption**: vLLM co-designs preemptive scheduling with paging so long sequences can be swapped without contiguous-buffer failure ([PagedAttention paper](https://arxiv.org/abs/2309.06180)).
- **Bulkheads**: [inferred] separate deployments / projects / `prompt_cache_key` pools per tenant so one tenant’s long-context prefill cannot starve another’s TPM budget.

### Durable execution at product layer

For multi-step agents, durable state belongs **outside** the context window (files, DB, memory tools); Anthropic recommends just-in-time loading of identifiers rather than stuffing full corpora into every turn ([Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). Server-side **compaction** is Anthropic’s documented long-horizon durability tactic when conversations approach 1M-token limits ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)).

## 4. Enterprise Security & Governance

> ⚠️ Limited public data available for this dimension. Pure LLM APIs publish **usage policies, safety filters, and data-residency multipliers**, not a full Zero-Trust MCP / tool-RBAC specification. Security controls below map foundational LLM surfaces (prompt, context, sampling, outputs) onto enterprise controls.

### Trust boundaries

1. **Untrusted user text** enters the same attention pool as system policy—prompt injection / jailbreak is a first-class failure mode of the topology, not only an application bug ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); [Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
2. **System prompts** act as soft policy: role, refusal rules, output schemas. Anthropic advises clear, mid-altitude instructions (neither brittle hardcoding nor vague guidance) and structured sections (XML/Markdown) ([Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Anthropic prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).
3. **Guardrails / safety filters** screen inputs and outputs for violence, self-harm, private data, off-scope content—the production boundary between a raw base model and a deployable assistant ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).

### PII & audit

Anything placed in the context window—including tool results and retrieved docs—is model-visible and typically logged in provider usage telemetry. [inferred] Enterprise designs should redact PII **before** tokenization, retain request IDs / token counts / model versions for audit, and prefer data-residency endpoints when required (Anthropic US-only inference: **1.1×** price multiplier on Claude 4.6+; OpenAI regional/FedRAMP: **10%** uplift for eligible models) ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing); [OpenAI Pricing](https://developers.openai.com/api/docs/pricing)).

### Isolation & capability control

- **Temperature / sampling**: high temperature increases variance of unsafe or off-policy completions—constrain for regulated flows ([OpenAI Chat Completions reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create/); [System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).
- **Tool sandboxing**: when the model emits tool calls, execution must be schema-validated and capability-scoped outside the model; hallucinated parameters are an integrity risk (see §5) ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).
- **Tokenizer boundary**: never mix OpenAI and Anthropic token estimates for quotas or spend caps—billing and window fit are model-specific ([Anthropic Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)).

### Alignment as governance training

RLHF / preference training shapes helpful–honest–harmless behavior but does **not** add a cryptographic enforcement layer; production still needs filters, allowlists, and human review for high-risk domains (medicine, finance, law) ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).

## 5. Production Failure Modes

### Hallucination

Models optimize next-token likelihood, not factual verification—they can invent citations, case law, or biographies with high fluency ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)). Mitigations: **grounding**, RAG (retrieve → augment → generate), tool-backed calculators/interpreters, structured outputs with schema validation, faithfulness metrics, LLM-as-judge eval loops ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).

### Context window degradation / context rot

Anthropic documents **context rot**: as token count grows, accuracy and recall degrade—curating content matters as much as window size ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)). Empirical results:

- Even with **perfect retrieval**, performance can drop **13.9%–85%** as input length grows within claimed windows; degradation persists with whitespace distractors or when irrelevant tokens are masked ([Context Length Alone Hurts… Findings EMNLP 2025](https://aclanthology.org/anthology-files/pdf/findings/2025.findings-emnlp.1264.pdf)).
- Long-horizon search studies observe **premature termination**: models give up or answer uncertainly **before** exhausting the window; rate correlates with context length ([Diagnosing Context Rot, arXiv:2606.29718](https://arxiv.org/abs/2606.29718)).
- **Context poisoning**: distractors create extreme-value attention interference; maintaining accuracy may require evidence margins scaling like \(\Omega(\sqrt{\log N})\) in distractor count \(N\) ([Context Poisoning, arXiv:2609.22101](https://arxiv.org/abs/2609.22101)).

Mitigations: retrieve-then-recite / short-context re-prompting (up to **~4%** gain on RULER for GPT-4o in one study), compaction, tool-result clearing, just-in-time context, quote-then-answer prompting ([EMNLP 2025 findings PDF](https://aclanthology.org/anthology-files/pdf/findings/2025.findings-emnlp.1264.pdf); [Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows); [Anthropic long-context prompting research](https://www.anthropic.com/research/prompting-long-context)).

### Overflow & stop reasons

If input alone exceeds the window → Anthropic `400 invalid_request_error` (“prompt is too long”). On Claude 4.5+, generation can stop with `stop_reason: "model_context_window_exceeded"` when input + generation hits the ceiling ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)).

### Cascading timeouts & cost blowups

Long prefills dominate TTFT; retries without idempotency double bill input tokens. [inferred] Propagate deadlines: budget TTFT + \(O \times\) inter-token time; cancel streams that exceed SLA; prefer cacheable static prefixes to cut both latency and cost ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).

### Hallucinated tool parameters / reasoning failures

Pattern-matching fails on precise math and multi-step logic unless tools enforce correctness ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)). Production pattern: JSON-schema validate tool args → deterministic executor → feed **tool results** back (never trust free-form numbers from the model for ledger-critical work).

### Inherited bias & knowledge cutoff

Training data bias and frozen cutoffs produce skewed or stale answers; RAG / web tools / fine-tunes are the operational patches ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).

### KV / serving failure modes

Fragmentation that blocks batching (pre-PagedAttention: only **20.4–38.2%** of reserved KV memory holding real tokens), OOM from oversized `max_tokens` reservations, and multi-tenant noisy-neighbor effects under continuous batching ([PagedAttention paper](https://arxiv.org/abs/2309.06180)).

## 6. Enterprise System Design Scenarios

### Scenario A — Multi-tenant chat API (hosted models)

**Topology:** Edge API → auth/quota → prompt assembly (system + RAG snippets ≤ budget) → vendor Completions/Messages with streaming; separate projects per environment.  
**Economics:** Route FAQ/classification to Haiku 4.5 ($1/$5) or gpt-4.1-nano ($0.10/$0.40); escalate complex reasoning to Sonnet 5 ($2/$10) or gpt-4.1 ($2/$8). Cache org-wide system+tool prefixes (≥1,024 tokens).  
**NFR:** Enforce TPM headroom; respect OpenAI ramp rule past 1M TPM; monitor `cached_tokens` hit rate.  
**Trade-off:** Lowest ops complexity; data leaves boundary unless residency endpoints (pay **1.1×** / **+10%**) ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing); [OpenAI Pricing](https://developers.openai.com/api/docs/pricing); [OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

### Scenario B — Self-hosted vLLM on A100/H100 fleet

**Topology:** OpenAI-compatible vLLM servers behind an internal gateway; PagedAttention KV manager; horizontal shards by model.  
**Capacity (published anchors):** LLaMA-13B KV up to ~1.7 GB/sequence; paging → &lt;4% waste → higher concurrency; LMSYS production anecdote: ~**30K** req/day average, **60K** peak, **50%** GPU reduction after adopting vLLM ([vLLM blog](https://vllm.ai/blog/2023-06-20-vllm)). Paper: **2–4×** throughput vs prior SOTA at same latency ([PagedAttention paper](https://arxiv.org/abs/2309.06180)).  
**Trade-off:** Higher ops/security burden; full weight + KV control; no vendor prompt-cache billing—but you pay GPU amortization.

### Scenario C — Long-context agent (200K–1M windows)

**Topology:** Agent loop with tools; context awareness tags on eligible Claude Sonnet/Haiku models; compaction + memory artifacts outside the window.  
**Windows:** Claude Sonnet 5 / Opus 5 family: **1M** context, up to **128K** `max_tokens` output; Sonnet 4.5: **200K** ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)). GPT-4.1: ~**1M**; GPT-4o: **128K** ([GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1); [GPT-4o](https://developers.openai.com/api/docs/models/gpt-4o)).  
**Risk:** Context rot / premature termination—design for **recite-then-answer**, subagent summarization, and evidence bottlenecks rather than stuffing full histories ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows); [arXiv:2606.29718](https://arxiv.org/abs/2606.29718); [EMNLP 2025 findings](https://aclanthology.org/anthology-files/pdf/findings/2025.findings-emnlp.1264.pdf)).  
**Cost note:** 900K-token Claude requests bill at the **same per-token rate** as short ones on 1M models (no long-context surcharge on Anthropic’s stated policy); OpenAI flagship tables show **separate higher long-context prices** for gpt-6-* families ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing); [OpenAI Pricing](https://developers.openai.com/api/docs/pricing)).

### Trade-off matrix

| Approach | Cost | Latency | Ops complexity | Security / tenancy | Scalability |
| --- | --- | --- | --- | --- | --- |
| Hosted frontier API | $$$ / token; cache & batch cut spend | Vendor TTFT; Fast mode premium | Low | Residency uplifts; shared infra | RPM/TPM tiers |
| Hosted small / nano | $ (e.g. $0.10/$0.40) | Typically faster decode | Low | Same API trust model | High RPM headroom on light models |
| Self-host vLLM | CapEx/GPU | Tunable; KV-bound | High | VPC isolation | 2–4× vs naive stacks |
| Long-context stuffing | Linear–superlinear token $ | Prefill-heavy TTFT | Medium | Larger PII surface | Hits rot before max window |
| RAG + short context | Retrieval + fewer tokens | Extra hop; lower prefill | Medium | Grounding improves integrity | Scales with index, not \(n^2\) attention |

### Capacity planning checklist (architect)

1. Count tokens with the **vendor’s** tokenizer / count API.  
2. Split budget: system + tools + retrieved evidence + user + **max output**.  
3. Size GPU KV: sequences × layers × heads × dim × precision × 2 (K and V)—validate against vLLM’s empirical 1.7 GB LLaMA-13B figure as order-of-magnitude ([vLLM blog](https://vllm.ai/blog/2023-06-20-vllm)).  
4. Model cost = uncached input + cached input + output (+ cache writes on Anthropic / GPT-5.6+).  
5. Apply rate-limit and ramp constraints before marketing “unlimited” concurrency.  
6. Evaluate faithfulness under **target context length**, not only needle-in-haystack demos.

## Sources

- [1] https://newsletter.systemdesign.one/p/llm-concepts — Primary article: 33 LLM concepts (tokens, inference, sampling, RAG, failure modes)
- [2] https://arxiv.org/abs/1706.03762 — Vaswani et al., Attention Is All You Need (Transformer)
- [3] https://proceedings.neurips.cc/paper_files/paper/2017/file/3f5ee243547dee91fbd053c1c4a845aa-Paper.pdf — NeurIPS 2017 Transformer PDF
- [4] https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb — OpenAI tiktoken token counting
- [5] https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create/ — OpenAI temperature / top_p API reference
- [6] https://developers.openai.com/api/docs/guides/prompt-caching — OpenAI prompt caching mechanics & TTL
- [7] https://developers.openai.com/cookbook/examples/prompt_caching_201 — Cached input prices (gpt-4o, gpt-4.1, …)
- [8] https://developers.openai.com/api/docs/pricing — OpenAI API pricing tables (gpt-6-* flagship, tools)
- [9] https://developers.openai.com/api/docs/models/gpt-4o — GPT-4o context & pricing
- [10] https://developers.openai.com/api/docs/models/gpt-4.1 — GPT-4.1 context & pricing
- [11] https://developers.openai.com/api/docs/models/gpt-4.1-nano — GPT-4.1 nano pricing
- [12] https://developers.openai.com/api/docs/guides/rate-limits — OpenAI RPM/TPM tiers, Retry-After, ramp rules
- [13] https://openai.com/api-fast-mode/ — OpenAI Fast mode latency / premium pricing notes
- [14] https://platform.claude.com/docs/en/about-claude/pricing — Anthropic model, cache, batch, fast-mode pricing
- [15] https://platform.claude.com/docs/en/build-with-claude/token-counting — Claude token counting; tiktoken warning; 30% tokenizer shift
- [16] https://platform.claude.com/docs/en/build-with-claude/context-windows — Context windows, context rot, compaction, overflow
- [17] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices — Anthropic prompting / few-shot / XML / long-context
- [18] https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — Context engineering for agents
- [19] https://www.anthropic.com/research/prompting-long-context — Long-context quote-then-answer study
- [20] https://github.com/anthropics/anthropic-sdk-python/blob/49d639a6/src/anthropic/resources/completions.py — Anthropic temperature / top_p / top_k docs
- [21] https://vllm.ai/blog/2023-06-20-vllm — vLLM / PagedAttention intro; 24× HF; KV 1.7GB; LMSYS traffic
- [22] https://arxiv.org/abs/2309.06180 — PagedAttention / vLLM SOSP paper (2–4× throughput)
- [23] https://docs.vllm.ai/en/stable/design/paged_attention/ — vLLM paged attention kernel design
- [24] https://aclanthology.org/anthology-files/pdf/findings/2025.findings-emnlp.1264.pdf — Context length alone hurts (13.9%–85% drops)
- [25] https://arxiv.org/abs/2606.29718 — Context rot / premature termination in long-horizon search
- [26] https://arxiv.org/abs/2609.22101 — Context poisoning as attention interference
- [27] https://github.com/anthropics/skills/blob/HEAD/skills/claude-api/shared/token-counting.md — Anthropic guidance: do not use tiktoken for Claude
- [28] https://claude.com/blog/best-practices-for-prompt-engineering — Anthropic 2026 prompt vs context engineering
- [29] http://www.d2l.ai/chapter_attention-mechanisms-and-transformers/transformer.html — Dive into Deep Learning Transformer chapter
- [30] https://en.wikipedia.org/wiki/Attention_Is_All_You_Need — Historical context on Transformer impact
- [31] https://www-cdn.anthropic.com/files/4zrzovbb/website/14082576b71dc6b532c2d49094cf4661e957cd69.pdf — Anthropic list prices PDF (cross-check)
- [32] https://arxiv.org/pdf/2309.06180 — PagedAttention PDF (memory waste 20.4–38.2% utilization figures)
