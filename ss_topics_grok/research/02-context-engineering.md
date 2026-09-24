# Research: Context Engineering

**Date researched**: 2026-09-23
**Sources consulted**: 98

Vendor list prices, model IDs, tokenizer differences, and SDK retry defaults live in [`01-python-llm-foundations.md`](01-python-llm-foundations.md). This file does **not** recopy those tables. It adds only context-engineering economics: **prefix-stability design**, **tool-schema tax on every turn**, **thinking/reasoning billed as output**, **compaction as a second sampling pass**, and **long-context price cliffs** (GPT-5.4 prompts **>272K** input).

Anthropic defines context engineering as curating “the smallest possible set of high-signal tokens that maximize the likelihood of some desired outcome,” treating the window as a finite **attention budget** with n² pairwise attention and diminishing returns as length grows ([Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). LangChain’s operational definition: “providing the right information and tools in the right format so the LLM can accomplish a task” — agent failures are more often **wrong context** than an incapable model ([LangChain context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)).

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane

| Plane | Owns | Does not own |
| --- | --- | --- |
| **Control plane** | Prompt assembler (roles, few-shots, XML/Markdown delimiters, tool schemas, RAG packing, history trim), cache-key / `cache_control` breakpoint placement, token budgeter, compaction policy, middleware (`create_agent` / Deep Agents) | Transformer weights, KV tensors |
| **Data plane** | Tokenizer → prefill (writes KV) → decode; **prefix KV reuse** on exact match; thinking/reasoning token stream | Which tokens were packed |

Hosted APIs hide the data plane. The application must keep prefixes **byte-identical** across turns or the KV prefix is recomputed. Anthropic: cache hashes the rendered prefix `tools` → `system` → `messages` up to the `cache_control` block ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). OpenAI GPT-5.6+: exact match at eligible breakpoints; implicit mode plants a breakpoint on the latest user/tool message, which **does not** fall back to the longest unmarked prefix ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

**Assembler graph (production)**

1. Load **durable events** (full transcript, tool results, memory files) separately from **request-scoped** packing (this turn’s tools, RAG hits, user text).
2. Render in provider order (below).
3. Count tokens (`count_tokens` / tiktoken — see 01 for which tokenizer). If over trigger: clear tool results, trim, compact, or offload **before** the model call.
4. Place cache breakpoints on the last **stable** block, not the varying suffix.
5. On tool loop: append `tool_result` / `function_call_output` without mutating the cached prefix.

**Thinking as a separate token stream.** Anthropic thinking blocks and OpenAI reasoning items are **not** ordinary assistant text. They are produced in a hidden (or summarized) channel, billed as **output**, then — if you echo signatures / `encrypted_content` — re-injected as **input** on the next prefill. In-band CoT (`Let's think step by step` inside the assistant message) is the opposite topology: one sampled sequence, fully visible, fully in the transcript. Mixing both (adaptive thinking **plus** a zero-shot-CoT instruction) pays twice and can confuse stop-reason parsers.

**Anthropic cache hierarchy (data-plane prefix).** Cache prefixes are created in order `tools`, then `system`, then `messages`. A change at one level invalidates that level and everything after it ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)):

| Change | Invalidates tools cache | system | messages |
| --- | --- | --- | --- |
| Tool names / descriptions / `input_schema` | yes | yes | yes |
| Toggle web search or citations | no | yes | yes |
| `tool_choice` or `disable_parallel_tool_use` | no | no | yes |
| Images present/absent | no | no | yes |
| Thinking / `output_config.effort` | model-specific | model-specific | yes |
| Speed `fast` vs standard | no | yes | yes |

Lookback is **20 blocks per breakpoint**. Consecutive `tool_use` blocks count as **one** position (same for consecutive `tool_result`), so a parallel tool burst does not by itself evict the prior write. Growing a conversation **≥20 blocks** past the last write **without** a second interior breakpoint is a silent 100% miss ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Automatic caching moves the breakpoint to the last cacheable block each turn and consumes **1 of 4** slots; 4 explicit breakpoints already present → **400** ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

**OpenAI GPT-5.6+ matching.** Implicit mode: one managed breakpoint at the latest eligible user/tool message; **no** 128-token longest-prefix fallback (that was pre-5.6). Explicit mode: you mark `prompt_cache_breakpoint` on a content part; content after it is uncached (no write charge on the suffix). Each request ≤ **4** cache writes. Matching considers first **2** and latest **50** explicit breakpoints (implicit mode also considers up to **20** earlier eligible message endings) ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Hidden OpenAI system tokens do **not** count toward the 1,024 minimum.

### 1.2 System prompts: role, constraints, output contracts

A system prompt is a **policy artifact**: role (who the model is), constraints (what it must not do), and an **output contract** (schema, XML tags, or tool-forced JSON). It is not “instructions plus a vibe.”

**Anthropic Messages API.** `system` is a **top-level** parameter, not a `messages[]` role on classic Messages. Role/persona and standing rules belong there; variable task data belongs in `user` ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices); [Count tokens](https://platform.claude.com/docs/en/api/csharp/beta/messages/count_tokens)). Anthropic’s applied-AI guidance: system prompts at the “right altitude” — specific enough to steer, not brittle if-else trees and not vague Goldilocks-failing prose. Organize with XML or Markdown sections (`<background_information>`, tool guidance, output description) ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). On Fable 5 / Mythos 5 / Opus 4.8 / Opus 5 you can append mid-conversation `{"role":"system"}` **inside** `messages` without invalidating a cached top-level `system`; Sonnet 5 does **not** have this — edit top-level `system` and accept a prefix miss ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). `clear_at: "next_user_message"` on a `role: "system"` message renders it only until the next user turn; `"never"` (default) keeps it in front of the model ([Count tokens API](https://platform.claude.com/docs/en/api/csharp/beta/messages/count_tokens)).

**OpenAI Chat Completions.** `developer` messages “replace the previous `system` messages” and are the application’s standing instructions; with o1-class and newer, use `developer` instead of `system` for app policy ([Chat Completions create](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create/)).

**OpenAI Responses API.** Two different slots:

| Slot | Lifetime | Cache implication |
| --- | --- | --- |
| Top-level `instructions` | **This request only**. Not carried by `previous_response_id` ([Migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses); [Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)) | Changing `instructions` every turn is a prefix miss if they sit in the cached prefix. Docs: `instructions` “take priority over a prompt in the `input` parameter” ([Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)). Top-level `instructions` **cannot** hold an explicit `prompt_cache_breakpoint` — put reusable policy in a developer `input_text` block ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)) |
| `input` item `role: "developer"` | Persists when chaining `previous_response_id` (community-confirmed; treat as part of the stored item list) ([OpenAI community](https://community.openai.com/t/will-developer-role-messages-persist-with-previous-response-id-in-responses-api/1313700)) | Stable if you do not re-append a new developer item each turn |

**Role map (do not mix these in one assembler without a table).**

| Intent | Anthropic Messages | OpenAI Chat Completions | OpenAI Responses | Harmony |
| --- | --- | --- | --- | --- |
| Platform / cutoff / effort | (model + `thinking` / `effort` fields) | (hidden) | (hidden) | `system` |
| App policy (role, constraints, contract) | top-level `system` | `developer` (replaces `system` on o1+) | `instructions` (per request) **or** `role: developer` item | `developer` |
| Untrusted user / RAG | `user`, XML-tagged | `user` | `input` `user` | `user` |
| Model output | `assistant` (+ thinking blocks) | `assistant` | output items | `assistant` + channels |
| Tool I/O | `tool_use` / `tool_result` (result **first** in the following user message) | `tool` role | `function_call` / `function_call_output` | `tool` (lowest authority) |

**Harmony (GPT-oss / reasoning-channel models).** Role hierarchy for instruction conflict: `system` > `developer` > `user` > `assistant` > `tool`. Harmony `system` is **platform** (reasoning effort, knowledge cutoff, built-in tools). The thing other products call “system prompt” is Harmony **`developer`** ([Harmony](https://developers.openai.com/cookbook/articles/openai-harmony)). Channels: `analysis` (hidden reasoning), `commentary`, `final`. Do not leak `analysis` to end users. Structured output in Harmony is a `# Response Formats` block at the **end of the developer message**; the prompt alone does not guarantee schema adherence — you still need a grammar at sample time ([Harmony](https://developers.openai.com/cookbook/articles/openai-harmony)).

**Output contracts (three layers, increasing hardness)**

1. **Prose + XML/Markdown tags** (`<answer>`, `<quotes>`). Claude: wrapping mixed instructions/data/examples in XML reduces misparse; tag names are a convention, not schema-validated XML ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices); [Claude Academy XML](https://academy.claude.com/courses/building-with-the-claude-api/structure-with-xml-tags)). OpenAI: Markdown headers + XML around untrusted docs ([Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)).
2. **JSON mode** (`json_object`): parseable JSON, **not** schema adherence. Chat Completions still requires the substring `json` in a message (see 01).
3. **Structured Outputs / strict tools**: constrained decoding against JSON Schema. OpenAI: `text.format.type = json_schema` (Responses) or `response_format.json_schema` (Chat); `strict: true` requires `additionalProperties: false` and every property in `required` ([Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)). Anthropic: `output_config.format = json_schema` plus `client.messages.parse()`; `"strict": true` on a tool compiles `input_schema` into a grammar (see 01). Prefill / logit-mask (Manus) is a fourth, self-hosted layer ([Manus](https://manus.im/en/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)).

### 1.3 Few-shot patterns

**Static vs dynamic.** Static: the same k demonstrations sit in the **stable prefix** (after system, before the live user turn) so they participate in prompt cache. Dynamic: retrieve k examples per query (embedding kNN, heuristics). Dynamic ICL improves relevance on heterogeneous tasks but **invalidates** the few-shot portion of the prefix every request unless you split the prefix: cache a large **random/diverse** block and append a small **query-similar** suffix ([Agarwal/Bertsch-style many-shot observation in compute-optimal ICL](https://arxiv.org/html/2507.16217v2); [SambaNova many-shot guide](https://sambanova.ai/blog/many-shot-prompting-a-practical-guide-to-icl)).

**k-shot diminishing returns.** GPT-3 established ICL with k typically fitting `n_ctx = 2048` (order 10–100 shots) with no gradient updates ([Brown et al., NeurIPS 2020](https://arxiv.org/abs/2005.14165)). Claude docs: **3–5** well-crafted examples are the practical default; wrap in XML; ask the model to critique diversity rather than dump every edge case ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices); [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — “canonical examples,” not a laundry list). Many-shot: accuracy tapers beyond roughly **50–70 demonstrations per class** in SambaNova’s reported setting; similarity-based selection wins at small N, **random/diverse** selection scales better as N grows (attention dilution / over-concentration) ([SambaNova](https://sambanova.ai/blog/many-shot-prompting-a-practical-guide-to-icl)). Compute-optimal many-shot: keep a large **cacheable random** block (e.g. 80 of 100 shots) and append a small **query-similar** suffix (e.g. 20) so selection still helps without rewriting the whole prefix ([arXiv:2507.16217](https://arxiv.org/html/2507.16217v2)). Information-theoretic analysis: few-shot ICL is near-optimal in sample complexity; many-shot ICL’s efficiency **deteriorates** in long context — often **1.5×** more demonstrations than a Bayes-optimal estimator for the same target ([arXiv:2502.04580](https://arxiv.org/abs/2502.04580)). DYNAICL trains a meta-controller to pick **per-input k** under a token budget instead of a global k ([arXiv:2305.11170](https://arxiv.org/abs/2305.11170)). AICL predicts instance-specific κ(x) ([arXiv:2403.06402](https://arxiv.org/html/2403.06402v1)).

**Selection vs format vs mapping.** Production assemblers fail in this order: (1) wrong **template** (JSON keys, XML tags) — Min: format can retain **75–95%** of gold-ICL gains even with random pairings ([Min et al.](https://arxiv.org/abs/2202.12837)); (2) wrong **k** for this item (DYNAICL/AICL); (3) wrong **neighbors** (embedding kNN). Do not spend a retrieval round-trip until the live output contract and the shots share a byte-level schema.

**Format fidelity.** Min et al. (EMNLP 2022): label space + input distribution matter even when **labels are wrong**; specifying format (input–label pairing) retains a large fraction of gold-ICL gains — e.g. **95%** (Direct MetaICL classification) and **82%** (multi-choice) of improvements by pairing random corpus sentences with the label set; pairing unlabeled inputs with random English words retained **75–87%** of gains depending on model/task. Removing the pairing (no labels / no inputs) is much worse ([Min et al.](https://arxiv.org/abs/2202.12837)). Later work: templates can dominate method deltas; there is **no universally best format**, and best templates **do not transfer** across models ([Mind Your Format](https://arxiv.org/html/2401.06766v3)). Ground-truth mappings **can** matter depending on model/task ([Yoo et al., EMNLP 2022](https://aclanthology.org/2022.emnlp-main.155/)). Production implication: few-shot blocks must match the **live output contract** (same XML tags, same JSON keys, same tool-call shape). Claude with thinking on: put `<thinking>` traces **inside** shots so the model generalizes that pattern ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)). Adding a shot mid-session changes the prefix hash at that block ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

**ReAct as a few-shot context pattern.** Yao et al. interleave Thought / Action / Observation in the packed window. On ALFWorld and WebShop, ReAct beat imitation/RL baselines by **+34** and **+10** absolute success points with **one or two** in-context trajectories ([ReAct, ICLR 2023](https://arxiv.org/abs/2210.03629)). That trajectory **is** context: every observation you leave in-band competes with tools and RAG for the attention budget.

### 1.4 Chain-of-thought: in-band vs hidden vs provider thinking

**Few-shot CoT (Wei et al., NeurIPS 2022).** Demonstrations are (input, reasoning steps, answer) triples. PaLM 540B with **eight** CoT exemplars reached then-SOTA on GSM8K, beating finetuned GPT-3 + verifier; CoT **more than doubled** GSM8K for the largest GPT and PaLM models vs standard few-shot. Ablation: “equation only” did **not** help much on GSM8K (need natural-language steps). Gains are **emergent with scale**. Sensitivity: exemplar permutation on GPT-3 SST-2 ranged **54.3% → 93.4%** (Zhao et al. 2021, cited by Wei) ([Wei et al.](https://arxiv.org/abs/2201.11903)).

**Zero-shot CoT (Kojima et al., NeurIPS 2022).** Append “Let’s think step by step” (two-stage: reason, then extract). InstructGPT `text-davinci-002`: MultiArith **17.7% → 78.7%**, GSM8K **10.4% → 40.7%**. Same template across arithmetic, symbolic, and some logic tasks. Commonsense metrics often **did not** move ([Kojima et al.](https://arxiv.org/abs/2205.11916)).

**When CoT hurts.** Sprague et al. meta-analysis: **110** papers, **1,218** CoT-vs-direct comparisons. Mean CoT delta: symbolic **+14.2**, math **+12.3**, logical **+6.9**; other categories **56.8 vs 56.1** (direct). On MMLU, **as much as 95%** of CoT’s total gain is from items containing “=” in the question or generated output ([Sprague et al., arXiv:2409.12183](https://arxiv.org/abs/2409.12183)). Li et al.: CoT can **reduce** accuracy where verbal deliberation hurts humans — implicit statistical learning: o1-preview **36.3** points worse than GPT-4o zero-shot on a 440-item subset (94.0% → 57.7%); GPT-4o CoT **−23.1** points vs its own zero-shot; exception-classification learning iterations **+331%** under CoT ([arXiv:2410.21333](https://arxiv.org/abs/2410.21333)). Interview takeaway: defaulting every copilot turn to CoT wastes output tokens **and** can degrade classification/perception tasks.

**Hidden vs visible reasoning (2026 APIs).**

| Provider | Mechanism | Visible to client? | Occupies context later? | Billed as |
| --- | --- | --- | --- | --- |
| Anthropic extended | `thinking: {type:"enabled", budget_tokens:N}` on 4.5 and earlier; **deprecated** on Opus/Sonnet 4.6; **400** on 4.7+ / Fable 5 / Sonnet 5 / Opus 5 if you send `budget_tokens` ([Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)) | `display: "summarized"` vs `"omitted"` (default omitted on Fable/Mythos/Sonnet 5, Opus 4.7/4.8) | Echo thinking/signature blocks or 400; billed as **input** on later turns when preserved (Opus 4.5+ / 4.6+ keep prior thinking; 4.5 Haiku/Sonnet strip on non-tool user turns) | **Output** for generated thinking; summarizer tokens **not** billed; billed count ≠ visible count ([Adaptive thinking archive](https://archive.ph/b7FDC); [Bedrock extended thinking](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-extended-thinking.html)) |
| Anthropic adaptive | `thinking: {type:"adaptive"}` + `output_config.effort`; **default on** Sonnet 5 / Opus 5 if `thinking` omitted — migrating from 4.6 without setting `disabled` starts billing thinking ([Bedrock adaptive](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-adaptive-thinking.html)) | Same `display` | Same | Same |
| OpenAI reasoning | `reasoning.effort`: `none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max` (model-dependent). GPT-5.5 default **medium**. `reasoning.summary`: `auto`/`concise`/`detailed` — summaries **no extra charge** ([Reasoning](https://developers.openai.com/api/docs/guides/reasoning); [Responses tools launch](https://openai.com/index/new-tools-and-features-in-the-responses-api/)) | Raw CoT **never** exposed; optional summary | Reasoning tokens occupy the **window**; ZDR: replay `encrypted_content` ([Reasoning](https://developers.openai.com/api/docs/guides/reasoning)) | **Output**; `usage.output_tokens_details.reasoning_tokens` included in `output_tokens` ([Help Center tokens](https://help.openai.com/en/articles/4936856-tokens-count-them)) |

In-band CoT (`<thinking>` in the assistant text) is a **different stream** from provider thinking: it is user-visible unless you strip it, it is sampled as ordinary output, and it is **not** covered by thinking signatures. Claude docs: with thinking **off**, you can still ask for tagged CoT; on Opus 5 with thinking disabled the model can leak internal XML into visible output — prefer low-effort adaptive thinking instead ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

### 1.5 Context budgets and packing order

**Window sizes (API, 2026-09-23).** Claude Fable/Opus/Sonnet 5 and listed 4.6+ : **1M in / 128k out**, standard price (no long-context multiplier) ([1M GA](https://claude.com/blog/1m-context-ga); [Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)). Haiku 4.5: **200k / 64k** (see 01). GPT-5.4: **1,050,000** context, **128,000** max output; prompts **>272K** input: **2× input and 1.5× output for the full session** (standard, batch, flex) ([GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)). Codex OAuth backends may cap the same slug at **272k** — budget the **served** window, not the marketing card ([Hermes/Codex probe](https://github.com/NousResearch/hermes-agent/issues/27918)).

**Lost-in-the-middle.** Liu et al., TACL 2024 (arXiv:2307.03172): multi-document QA and JSON key-value retrieval show a **U-shaped** accuracy curve — primacy + recency beat the middle, including on long-context variants. GPT-3.5-Turbo with the answer in the **middle** of 20–30 documents can fall **below** closed-book **56.1%**; drops **>20** points vs best position. Extended-context twins (4k vs 16k GPT-3.5) overlay when both fit. Encoder-decoders are robust **inside** training length, U-shaped **beyond**. Query-aware contextualization made synthetic KV retrieval near-perfect. Open-domain NQ: performance saturates far before retriever recall (20 vs 50 docs ≈ marginal) ([TACL](https://aclanthology.org/2024.tacl-1.9/); [MIT Press](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00638/119630)). Anthropic: putting the **query after** longform documents improved quality by **up to 30%** on complex multidocument tests ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

**Recommended physical order (cache-stable + U-curve)**

Aligned with Anthropic cache hierarchy and OpenAI “static first” ([Prompt caching Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [Prompt caching OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching); [Bedrock caching blog](https://aws.amazon.com/blogs/machine-learning/effectively-use-prompt-caching-on-amazon-bedrock/)):

1. **Tool schemas** (rarely change; Anthropic: any definition change wipes **entire** cache).
2. **System / developer** + **static few-shots** (format-identical to the live contract).
3. Slow-changing session memory / CLAUDE.md-style notes.
4. Pinned RAG / documents in XML (`<documents><document index><source><document_content>`).
5. Conversation history (grows; automatic cache or growing breakpoint).
6. **Current user query last** (needle at the end).
7. Fresh tool results (never in the stable prefix unless you intend to cache the loop).

**Token allocation heuristic [inferred from vendor triggers + Liu/Chroma].** Target **working** context well below the advertised max. Anthropic server compact default trigger **150,000** (min **50,000**) ([Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)). Tool-result clearing default trigger **100,000**, keep last **3** tool uses ([Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)). Deep Agents: offload tool I/O at **20,000** tokens; summarize at **85%** of `max_input_tokens`, keep **10%** recent; fallback **170,000** trigger / **6** messages if no model profile ([Deep Agents context engineering](https://docs.langchain.com/oss/python/deepagents/context-engineering)). Cursor: MCP tool **names** in the static prompt, full schemas on disk; A/B **−46.9%** total agent tokens on runs that called an MCP tool ([Cursor dynamic context discovery](https://cursor.com/blog/dynamic-context-discovery)).

**Scratchpads.** In-window: Claude thinking blocks / Harmony `analysis` (consume input on later turns). Out-of-window: Anthropic `memory_20250818` (`view`/`create`/`str_replace`/`insert`/`delete`/`rename` on a client filesystem) ([Cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)); OpenAI Agents SDK `RunContextWrapper.context` is **not** model-visible unless injected ([Agents SDK context](https://openai.github.io/openai-agents-python/context/)); LangChain Store vs State vs Runtime Context ([LangChain context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)).

**Budget split (working example, 128k window, coding agent).** Deep Agents default is **85%** trigger / **10%** keep. **[inferred]** on a 128k-in model that is 108.8k packed before summarize, 12.8k retained. A more conservative copilot split used in interviews:

| Segment | Token cap | Rationale |
| --- | --- | --- |
| Tools + hidden tool-use prompt | 2–8k (or names-only + defer) | Schema tax every turn; Cursor −46.9% when deferred |
| System / developer + output contract | 1–3k | Policy; must be cache-stable |
| Few-shots | 1–4k (3–5 canonical) | Format fidelity > k |
| Memory / notes | 1–2k | Pointers, not dumps |
| RAG / files | 8–32k **or** tool-fetch | Query last; k small (Liu 20 vs 50 docs saturates) |
| History | remainder until compact trigger | Append-only for cache |
| Live user + fresh tool_result | last | Needle + recency |
| Thinking / reasoning reserve | 2–8k of **output** budget | Occupies window on later turns if echoed |

Claude 1M does **not** mean you should fill 1M. Chroma’s ~113k full-history vs ~300-token focused prompt is the quality argument against stuffing ([Context Rot](https://research.trychroma.com/context-rot)). GPT-5.4’s **272k** price cliff is the cost argument ([GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)).

**Compaction vs sliding window vs offload.**

| Strategy | Mechanism | Loss | Cache | Recoverability |
| --- | --- | --- | --- | --- |
| Sliding window / `trim_messages` | Drop oldest tokens; keep last N | Extractive (exact tokens gone) | Prefix hash changes at trim | None unless event log kept |
| LangGraph `llm_input_messages` trim | Transient per call | Same | Checkpoint can stay full | Full if checkpointer untrimmed |
| Abstractive compact (Anthropic / LangChain summary) | LLM rewrite | Semantic; IDs can invert | New prefix | Weak unless `pause_after` / history file |
| OpenAI `/responses/compact` | Encrypted item + verbatim users | Opaque on assistant/tool/reasoning | New prefix | Users remain; assistant traces not human-QA |
| Tool-result clear | Drop bulky `tool_result`, keep `tool_use` | Re-fetchable by design | Bust then restabilize; use `clear_at_least` | Re-run tool |
| Filesystem offload | Path + 10-line preview | None if file durable | Parent prefix stable | `read_file` / grep |

### 1.6 Dynamic assembly: middleware, delimiters, cache prefix, tool-schema tax

**Priority packing.** Assembler ranks segments: (policy, tools, shots, memory, RAG, history, user). Drop from the middle of RAG/history first (U-curve), never drop the live query or the output contract. Compaction vs sliding window: sliding window is **extractive trim**; compaction is **abstractive** (lossy) plus a new prefix.

**XML vs Markdown.** Claude: XML for mixed untrusted data; OpenAI: Markdown structure + XML around documents. Format matching: “removing markdown from your prompt can reduce the volume of markdown in the output” ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

**Prompt caching prefix stability.** Anthropic: max **4** breakpoints; lookback **20** content blocks per breakpoint (consecutive `tool_use` / `tool_result` runs count as **one** position on Claude API); TTL **5m** default (`1.25×` write) or **1h** (`2×` write); longer TTL must appear **before** shorter; automatic caching consumes **1** of 4 slots; 4 explicit + automatic → **400** ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Writes occur **only at breakpoints**; lookback finds prior **writes**, not “stable content behind a changing suffix.” Timestamp in system prompt + breakpoint on the last (varying) block → write every turn, **zero** reads ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [Cache diagnostics](https://platform.claude.com/docs/en/build-with-claude/cache-diagnostics) `system_changed` / `tools_changed` / `messages_changed`). OpenAI GPT-5.6+: min **1,024** visible tokens; writes **1.25×**; reads **0.1×**; TTL **`30m` only**; up to **4** cache writes/request; implicit breakpoint on latest user/tool message — Codex reproduced **0%** hits on “stable 9k prefix + changing user” until `prompt_cache_breakpoint` → **98.6%** hits ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [Codex #35300](https://github.com/openai/codex/issues/35300); [Community](https://community.openai.com/t/gpt-5-6-prompt-caching-fails-on-partial-prefixes/1386887)). Pre-warm Anthropic: `max_tokens: 0` writes the cache and returns empty content ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Concurrent: cache visible only after the **first response begins**.

**Tool-schema tax.** Every Anthropic request with `tools` injects a hidden tool-use system prompt **plus** your JSON schemas. Sonnet 5: **354** tokens (`auto`/`none`) or **474** (`any`/`tool`) **on top of** names/descriptions/schemas, every turn ([Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)). `computer_toolset_20260801` default members ≈ **4,590** input tokens on Sonnet 5; `browser_toolset_20260801` ≈ **6,670** ([same pricing page](https://docs.anthropic.com/en/docs/about-claude/pricing)). Manus: do **not** add/remove tools mid-loop (busts KV + confuses dangling `tool_use` names); mask logits / prefill `browser_` prefixes instead. Reported agent I/O ratio ≈ **100:1** input:output ([Manus](https://manus.im/en/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)). Cursor deferred MCP schemas: **−46.9%** tokens ([Cursor](https://cursor.com/blog/dynamic-context-discovery)). Anthropic `defer_loading` / tool search: deferred tools are **not** in the system-prompt prefix; discovered tools appear as `tool_reference` in history so the cached prefix survives ([Tool use with prompt caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching)).

**LangChain `create_agent` middleware.** Model context is **transient** (one call); life-cycle context is **persistent** (state). `SummarizationMiddleware(trigger={"tokens": 4000}, keep=("messages", 20))`: `before_model` rewrites `state["messages"]` permanently ([LangChain context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering); [SummarizationMiddleware](https://reference.langchain.com/python/langchain/agents/middleware/summarization/SummarizationMiddleware)). `ContextEditingMiddleware` is `wrap_model_call` on a **deepcopy** — tool-result `[cleared]` lasts one call; next turn state still has originals. Summarization always runs first because it is a graph node before the model ([Forum](https://forum.langchain.com/t/how-do-contexteditingmiddleware-and-summarizationmiddleware-interact-when-used-together-combining-contexteditingmiddleware-summarizationmiddleware-execution-order-and-behavior-when-both-trigger/3463)). Summary LLM retries **3** times then **propagates** the error (no fake summary) ([summarization.py](https://github.com/langchain-ai/langchain/blob/master/libs/langchain_v1/langchain/agents/middleware/summarization.py)). LangGraph `pre_model_hook`: return `llm_input_messages` to trim **without** mutating checkpointed `messages`; return `RemoveMessage(REMOVE_ALL_MESSAGES)` to compact state ([chat_agent_executor](https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py)).

**Deep Agents.** `create_deep_agent` includes filesystem offload + `SummarizationMiddleware`. Tool results **>20k** tokens → file + **10-line** preview. At **85%** window: structured summary in-context **and** append original messages to `/conversation_history/{session_id}.md`. LangChain summarization **drops** evicted messages; Deep Agents keep a retrieval path via `read_file`. Optional `compact_conversation` tool gated at ~**50%** of the auto trigger so the model cannot compact too early ([Deep Agents](https://docs.langchain.com/oss/python/deepagents/context-engineering); [LangChain blog](https://www.langchain.com/blog/context-management-for-deepagents); [reference](https://reference.langchain.com/python/deepagents/middleware/summarization)).

**Anthropic server compaction / context editing.** `compact_20260112`, trigger default **150k**, min **50k**, `instructions` **replace** the summarizer prompt (max **16,384** chars), `pause_after_compaction` for audit. Subsequent requests drop everything before the `compaction` block ([Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)). `clear_tool_uses_20250919`: default trigger **100k**, keep **3**, optional `clear_at_least` so you do not bust cache for a tiny clear ([Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)). Client keeps the **full** unmodified history; editing is server-side before the model.

**OpenAI compaction.** `/responses/compact` is stateless/ZDR-friendly with `store=false`. Docs/community: **all prior user messages kept verbatim**; assistant/tool/reasoning replaced by an **encrypted** compaction item you must replay unchanged. Input to compact must still **fit** the model window. Server-side: `context_management: [{type:"compaction", compact_threshold:N}]` ([Compaction](https://developers.openai.com/api/docs/guides/compaction); [community](https://community.openai.com/t/compact-a-response-with-previous-response-id/1372502/10)).

---

## 2. Token Economics & NFR Metrics

List prices and cache multipliers: **see 01**. Below is assembler-specific spend.

### 2.1 System prompt + tool schemas on every turn

Uncached, tools+system are billed as **input** on **every** agent iteration (Manus 100:1 I/O makes this the invoice). Anthropic extra: hidden tool-use system prompt **354/474** (Sonnet 5 auto vs forced) even with one empty tool ([Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)). Computer-use / browser-use toolsets add **~4.5k / ~6.7k** definition tokens per request on Sonnet 5 **before** screenshots ([Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)).

**[inferred] Sonnet 5, 20-tool agent, ~2,000 schema tokens + 354 hidden + 1,500 system + 500 user, 800 output, no cache, 1,000 turns:**
input 4,354 × $2 / 1e6 + output 800 × $10 / 1e6 = **$0.01671 / turn** → **$16.71 / 1k turns**.
Same with 5m cache warm (schemas+system 3,854 cached @ $0.20, 500 uncached @ $2, 800 out @ $10): **$0.00957 / turn** → **$9.57 / 1k**. Cache is the difference between “tools are free after turn 1” and “tools are a subscription.”

Cursor’s **46.9%** token cut on MCP-calling runs is a **schema-tax** reduction, not a model-price change ([Cursor](https://cursor.com/blog/dynamic-context-discovery)).

### 2.2 Cache write vs read for stable prefixes

Anthropic published break-even: 5m write pays after **1** subsequent read; 1h after **2** (01; [Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)). OpenAI GPT-5.6+ : one write + one full read = **1.35×** vs **2×** uncached; ten requests (1 write + 9 reads) = **2.15×** vs **10×** ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Writes are **only** worth it if the prefix is reused. Implicit GPT-5.6 breakpoint on the **changing** user message → **1.25× write every turn** with no reads — **more expensive than no cache**. Explicit breakpoint after tools+developer+docs is the assembler rule ([Community](https://community.openai.com/t/gpt-5-6-prompt-caching-fails-on-partial-prefixes/1386887)).

Fable 5.1 / Mythos 5.1 cache **hit = 2.5%** of input ($0.25/MTok at $10 input) vs 10% on most Claude models ([Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)) — prefix design has **4×** more leverage there.

Bedrock: `CacheReadInputTokens` **do not** count toward TPM; writes do (see 01). Anthropic ITPM similarly excludes cache reads except Haiku 3.5 (01). Prefix hits are a **quota** lever, not only a dollar lever.

### 2.3 Thinking / reasoning as output

Confirmed from docs, not inferred: Anthropic thinking tokens are **output**; `usage.output_tokens_details.thinking_tokens` is the raw (not summarized) count; `display: "omitted"` does **not** reduce the bill; you are billed for full thinking, not the summary; summarizer tokens are **not** charged ([Adaptive thinking](https://archive.ph/b7FDC); [Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-extended-thinking.html)). OpenAI: “While reasoning tokens are not visible via the API, they still occupy space in the model’s context window and are billed as output tokens.” Summaries are **free** ([Reasoning](https://developers.openai.com/api/docs/guides/reasoning); [launch](https://openai.com/index/new-tools-and-features-in-the-responses-api/)).

**[inferred] cost of “leave thinking on by default.”** Sonnet 5 output $10/MTok. 2,000 thinking tokens/turn × 1,000 turns = **$20.00** — more than the entire cached-input agent in §2.1. Adaptive thinking on Sonnet 5/Opus 5 when `thinking` is **omitted** is a silent invoice ([Bedrock adaptive](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-adaptive-thinking.html)). Sprague: for non-math copilot Q&A, that $20 often buys **~0** accuracy.

Preserved thinking on later turns is **input**. Long agent loops with “keep all thinking” can dominate the window and the input bill on Opus 4.5+ / 4.6+ ([Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)).

### 2.4 Compaction LLM cost vs long-context price cliffs

**Anthropic compaction** is an extra sampling iteration: billed and rate-limited like a normal request; top-level `usage.input_tokens` / `output_tokens` **exclude** the compaction iteration — you **must** sum `usage.iterations[]` or you under-count ([Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)). On-demand compact (`compact-2026-09-04`): example usage showed compaction iteration `input_tokens: 144, output_tokens: 276` in a docs snippet; the summarizer uses the request’s model, `system`, `tools`, thinking settings, and `max_tokens` ([same](https://platform.claude.com/docs/en/build-with-claude/compaction)). Re-sending an existing `compaction` block does **not** re-bill compaction.

**LangChain SummarizationMiddleware** is a **second LLM call** (often a cheaper model, e.g. `gpt-5.4-mini` in their example) whose tokens must be budgeted separately; errors retry 3× ([docs](https://docs.langchain.com/oss/python/langchain/context-engineering)).

**GPT-5.4 long-context cliff.** Input **>272K** → **2× input and 1.5× output for the full session** ([GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)). **[inferred]** stuffing 300k tokens of history to “avoid compaction” **doubles** the session’s input rate **and** multiplies output (including reasoning) by 1.5. Claude 1M is **flat** standard price ([1M GA](https://claude.com/blog/1m-context-ga)) — the cliff is an OpenAI assembler constraint, not a universal physics.

**Worked 1k-turn agent [inferred from 01 list prices].** Shape: 20k stable prefix (tools+system+shots), 2k growing history (cached after turn 1 via automatic/breakpoint), 500 unique user, 1k visible output, **no thinking**, Sonnet 5 5m cache, 1 write + 999 hits:

- Write: 20,000 × $2.50 / 1e6 = $0.050
- Per subsequent turn: 20,000 × $0.20 + 2,000 × $0.20 + 500 × $2 + 1,000 × $10 per MTok = $0.004 + $0.0004 + $0.001 + $0.010 = $0.0154
- 1k turns ≈ $0.050 + $0.0165 (turn 1 unique+out) + 999 × $0.0154 ≈ **$15.44**

Same shape **uncached**: 1,000 × ((22,500 × $2 + 1,000 × $10) / 1e6) = **$55.00**.

Same shape **GPT-5.4** (cached $0.25 / input $2.50 / out $15, free write on pre-5.6 family per 01): 1k × ((22,000 × $0.25 + 500 × $2.50 + 1,000 × $15) / 1e6) ≈ **$21.75** after warm [inferred]. If history grows past **272k**, apply 2×/1.5× to the **whole** session — do not let a coding agent drift across that cliff; compact or offload first.

If adaptive thinking adds **2k** output tokens/turn on Sonnet 5: +$20 / 1k turns (above). Compaction once per 150k input at ~few-hundred output tokens is **cents**, not dollars — cheaper than crossing GPT-5.4’s 272k cliff.

### 2.5 Latency: TTFT vs packed length; cache-hit TTFT

Prefill is compute-bound in packed length; cache hit skips that FLOPs. AWS Bedrock marketing max: cost **≤90%** down, latency **≤85%** down ([Bedrock prompt caching](https://aws.amazon.com/bedrock/prompt-caching/)). OpenAI cookbook: **≤80%** latency cut for prompts **>10,000** tokens ([Prompt Caching 101](https://developers.openai.com/cookbook/examples/prompt_caching101)). Independent public-API TTFT (N≤20): measured hit reductions **5–39%** at 1.5k–20k prefixes — RTT-dominated; calculated prefill-only savings would be 99%+ ([nirmalya.net Feb 2026](https://www.nirmalya.net/posts/2026/02/ttft-optimisation-practical-patterns/); [ttft-benchmark](https://github.com/nirmalyaghosh/ttft-benchmark)). Anthropic `display: "omitted"`: faster **TTFT for visible text** because thinking deltas are not streamed; **bill unchanged** ([Adaptive thinking](https://archive.ph/b7FDC)).

> ⚠️ Limited public data: no contractual p50/p95 TTFT SLA for cache-hit vs miss on Standard tiers. Fast-mode tok/s SLOs are in 01.

vLLM APC: helps **prefill only**, not long-decode ([vLLM APC](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)). Manus’s 100:1 ratio is why they treat **KV-cache hit rate** as the single most important production metric ([Manus](https://manus.im/en/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)).

OpenAI: traffic **>~15 req/min per `prompt_cache_key`** overflows routing and miss rate rises ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Partition keys by tenant **or** you lose hits under load.

Independent TTFT table (shared public API; network dominates small prefixes) ([TTFT post](https://www.nirmalya.net/posts/2026/02/ttft-optimisation-practical-patterns/)):

| Prefix tokens | Miss mean | Hit P50 | Hit P95 | Measured reduction |
| --- | --- | --- | --- | --- |
| ~1,500 | 1.015 s | 1.150 s | 2.821 s | −13.3% (noise) |
| ~3,000 | 1.404 s | 0.949 s | 1.603 s | 32.4% |
| ~5,000 | 1.732 s | 1.057 s | 1.618 s | 39.0% |
| ~10,000 | 1.379 s | 1.201 s | 1.988 s | 12.9% |
| ~20,000 | 1.486 s | 1.411 s | 1.953 s | 5.0% |

Do not promise “80% TTFT cut” on a public internet path; that cookbook number is a **max** for long prefixes when prefill dominates ([Prompt Caching 101](https://developers.openai.com/cookbook/examples/prompt_caching101)). Dedicated VPC/colocation would move P50 toward calculated prefill savings [inferred].

**Compaction vs cliff, numeric.** **[inferred]** One Anthropic compact at 150k in / ~2k summary out on Sonnet 5: 150k × $2 + 2k × $10 per MTok ≈ **$0.32** (plus the subsequent turn on the small window). One GPT-5.4 turn at 300k input after the cliff: 300k × ($2.50 × 2) / 1e6 = **$1.50 input alone**, and **all later turns in that session** inherit 2×/1.5×. Compact/offload **before** 272k is the NFR control.

---

## 3. Distributed Resilience & State

### 3.1 Deterministic assembler for cache hits

Cache hits require **byte-identical** prefixes. Diagnostics taxonomy ([Cache diagnostics](https://platform.claude.com/docs/en/build-with-claude/cache-diagnostics)):

| `cache_miss_reason` | Cause | Fix |
| --- | --- | --- |
| `system_changed` | Timestamp / request id in `system` | Stable system; dynamic data **after** breakpoint |
| `tools_changed` | Add/remove/**reorder** tools; non-deterministic JSON key order | Fixed order; sort schema keys |
| `messages_changed` | Truncate/edit history; re-serialize `tool_result` | Append-only; echo assistant content **verbatim** |

Manus independently: no second-precision timestamps at the front of system; deterministic JSON serialization; append-only actions/observations ([Manus](https://manus.im/en/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)). Anthropic invalidation: tool definition change → **tools+system+messages**; `tool_choice` → messages only; thinking/`effort` changes invalidate messages (and sometimes tools/system depending on where config is rendered) ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [Tool caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching)).

Stampede: N parallel first requests = **N writes** (Anthropic cache not visible until first response begins) ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Serialize a `max_tokens: 0` warm, then fan out.

TTL clock starts at **request start**, not stream end — a 4-minute stream on 5m TTL leaves ~1 minute for the next tool call ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Slow tools → `ttl: "1h"` on the prefix breakpoint [inferred].

### 3.2 Checkpoint assembled context vs raw events

| Store | What is durable | Replay |
| --- | --- | --- |
| Raw event log (user, assistant, tool_result, thinking signatures) | Ground truth | Re-run **deterministic** assembler → same prefix → cache hit |
| Assembled packed prompt only | Opaque blob | Fast retry; **cannot** re-pack after schema version bump |
| LangGraph checkpointer | Graph state (`messages`, etc.) at super-steps; pending writes for partial super-steps ([Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)) | Resume thread; if summarization wrote `messages`, you resume from the **lossy** window unless you also stored `llm_input_messages` separately |
| Deep Agents `/conversation_history/{session_id}.md` | Evicted messages on filesystem | `read_file` / search; checkpointer can keep full `messages` (Deep Agents summarization tracked in `_summarization_event`, non-mutating option) ([reference](https://reference.langchain.com/python/deepagents/middleware/summarization)) |
| OpenAI `previous_response_id` / Conversations | Server-side items | New turn only; `instructions` **not** in that checkpoint ([Migrate](https://developers.openai.com/api/docs/guides/migrate-to-responses)) |
| OpenAI encrypted reasoning / compaction items | Opaque | Must replay **unmodified**; ZDR `store=false` ([Reasoning](https://developers.openai.com/api/docs/guides/reasoning); [Compaction](https://developers.openai.com/api/docs/guides/compaction)) |
| Anthropic `compaction` block | Summary; API drops pre-block content | Echo the block; client may still keep full local history ([Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)) |

**Production rule:** checkpoint **events + assembler version + tool-schema version + prompt hash**. GPU KV is not a backup. In-memory session stores die with the process (ADK in-memory — see quality-bar/01 patterns).

LangGraph `DeltaChannel` reconstructs state by replaying ancestor writes so checkpoint blobs stay O(1) per step for accumulating `messages` ([Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)) — that is **raw-event-ish**, not packed-prompt-ish.

### 3.3 Compaction as a lossy checkpoint

Abstractive summaries **drop** facts whose importance appears later ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — tune recall first, then precision). Anthropic: if summarizer calls a tool instead of writing text, `compaction.content` can be **null** — set `instructions` to forbid tools ([Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)). `pause_after_compaction` exists so you can **audit** before continuing. LangMem/SummarizationMiddleware: if the to-summarize span exceeds `trim_tokens_to_summarize`, older tokens in that span may **never reach the summarizer** (second lossy gate) ([LangChain summarization.py](https://github.com/langchain-ai/langchain/blob/master/libs/langchain_v1/langchain/agents/middleware/summarization.py); quality-bar LangMem notes). OpenAI compact items cannot be DLP-inspected.

**Restorable compression (Manus):** omit page text if URL remains; omit file body if sandbox path remains ([Manus](https://manus.im/en/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)). Deep Agents offload is this pattern with a default **20k** threshold.

### 3.4 Replay after crash with the same prefix

1. Load event log.
2. Run assembler **vN** (pinned): same tool order, same JSON dumps, same system string, no wall-clock in prefix.
3. If compaction/summary exists, treat it as an event, not a rewrite of earlier events.
4. Echo thinking signatures / encrypted reasoning **verbatim** or the API 400s / quality drops.
5. Cache: first post-crash call may **miss** (TTL 5m/30m/1h). Warm with `max_tokens: 0` if the prefix is huge.

OpenAI WebSocket `store=false`: previous-response state is **connection-local RAM**; disconnect → `previous_response_not_found`; you must replay the full input or compacted window ([WebSocket mode](https://developers.openai.com/api/docs/guides/websocket-mode)).

> ⚠️ Limited public data: no provider cache-service SLO or mandated circuit-breaker. Fallback is always full prefill.

---

## 4. Enterprise Security & Governance

### 4.1 System prompt injection vs user-content delimiters

OWASP **LLM01:2025**: models cannot reliably separate instructions from data; fool-proof prevention is **not** claimed. Mitigations: constrain system behavior, validate outputs, filter I/O, **segregate and denote** untrusted content ([OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/); [PDF](https://owasp.github.io/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)). Spotlighting (delimiting / datamarking / encoding) reduced ASR from **>50% to <2%** on GPT-family experiments ([Hines et al., arXiv:2403.14720](https://arxiv.org/abs/2403.14720)). Azure: wrap retrieved docs as documents so Prompt Shields classify them as **document** attacks, not user commands ([Azure guardrails](https://learn.microsoft.com/en-us/azure/foundry/guardrails/how-to-create-guardrails)). Harmony ranks `tool` **below** `user` and `developer` — do not promote tool text into developer. XML tags are defense-in-depth, **not** an enforceable boundary.

Trusted policy: `system` / `developer` / Responses `instructions`. Untrusted: RAG, web, email, `tool_result` — tagged, **after** a cache breakpoint so a poison document is not frozen into a 1h prefix shared by later sessions.

### 4.2 Tool schema leakage

Tool names, descriptions, and enums are **prompt text**. They leak product internals, unreleased flags, and internal URL patterns to any user who can induce the model to echo tools (or to anyone reading logs). Computer/browser toolsets add **thousands** of tokens of capability surface ([Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)). Least privilege: do not ship `execute_sql` “for convenience.” Cursor/Anthropic deferral: names in context, schemas on demand, reduces both tokens and leakage surface ([Cursor](https://cursor.com/blog/dynamic-context-discovery); [defer_loading](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching)).

### 4.3 PII in few-shots and history

Static few-shots copied from production tickets **are** a training-set-shaped PII store in every cached prefix. Dynamic ICL from a customer index can inject another tenant’s document if retrieval is not ACL’d. Compaction summaries and memory-tool files **outlive** the chat UI and are new DLP targets. OpenAI compact items are encrypted/opaque (better for ZDR logs, worse for inspection) ([Compaction](https://developers.openai.com/api/docs/guides/compaction)). Anthropic `pause_after_compaction` is the audit hook ([Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)). 1M windows invite dumping mailboxes — treat the packed prompt as a **data store** with retention.

### 4.4 What gets cached (cross-tenant prefix isolation)

Anthropic: never shared across **organizations**. Workspace isolation on Claude API, Claude Platform on AWS, Microsoft Foundry. **Bedrock and Google Cloud: organization-level only** — two workspaces in one cloud project can share a prefix cache ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Multi-tenant SaaS on Bedrock must **not** put Tenant A PII in a prefix Tenant B can hash-hit.

OpenAI 2024: caches not shared between organizations ([Launch](https://openai.com/index/api-prompt-caching/)). `prompt_cache_key` is routing/accounting, **not** a confidentiality boundary (01; [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Identical public system prompts sharing KV is usually acceptable; identical **tenant documents** sharing KV is not.

Poisoned RAG cached for 1h is **cheap replay of injection** at 0.1×. Classify **before** cache write.

| Cache | Isolation unit | Multi-tenant SaaS implication |
| --- | --- | --- |
| Anthropic Claude API / Foundry / Claude-on-AWS | Workspace | One workspace per tenant **or** no tenant PII in prefix |
| Anthropic on Bedrock / Vertex | Organization / cloud project | Assume tenants **share** identical-prefix KV |
| OpenAI | Organization; `prompt_cache_key` is affinity not ACL | Key in logs ≠ secret; tenant docs after breakpoint |
| Gemini explicit cache | Resource name + project | TTL storage fee (see 01); delete on tenant offboard |
| vLLM APC / SGLang radix | Process (or shared LMCache) | Shared engine + identical tenant docs = shared blocks |
| App semantic cache (GPTCache) | Whatever key you chose | Default miss-keyed = cross-user answers |

### 4.5 Prompt as policy artifact

Version: `prompt_id`, `assembler_semver`, `tool_schema_hash`, `fewshot_set_id`. Audit per call: token counts by segment (tools / system / shots / RAG ids / history / scratchpad), cache read vs write, compaction trigger, Shield `attackDetected`, SHA-256 of packed prompt (not raw PII). LangChain: store **full** messages for UI and a separate key for the LLM-facing window ([LangChain context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)). Ship prompt diffs through the same review as code. Feature-flag `instructions` the way you flag APIs ([OpenAI prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)).

---

## 5. Production Failure Modes

### 5.1 Lost-in-the-middle and context rot

Liu et al. (TACL 2024): two tasks — multi-document QA and synthetic JSON key-value retrieval. Performance is **U-shaped** in the index of the relevant span (primacy + recency). GPT-3.5-Turbo closed-book **56.1%** / oracle-single-doc **88.3%**; with 20–30 docs, **middle** placement can drop **>20 points** and fall **below closed-book**. GPT-3.5-Turbo vs GPT-3.5-Turbo-16K overlay when both fit — **longer training window ≠ better use of the middle**. Encoder-decoder (Flan-T5/UL2) is flat **inside** training length and U-shaped **beyond**. Query-aware contextualization (put the question in the context, not only at the end of the prompt in some setups) made KV retrieval near-perfect even at 300 pairs. Open-domain NQ: 50 retrieved docs vs 20 is only a **marginal** reader gain while retriever recall still rises — stuffing k is a latency/cost tax with little accuracy ([TACL 2024](https://aclanthology.org/2024.tacl-1.9/); [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)). Llama-2: 7B recency-only; 13B/70B show the full U-curve.

Mitigation: query last (Anthropic **≤30%** quality claim on multidocument tests) ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)); reduce k; retrieve just-in-time (Claude Code glob/grep) instead of pre-stuffing ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

Chroma **Context Rot** (Jul 2025): **18** models (GPT-4.1, Claude 4, Gemini 2.5, Qwen3, …); reliability drops as input length grows even on simple retrieval/replication; lexical NIAH is too easy; semantic needle–question similarity and distractors hurt more; **all 18** models did better on shuffled haystacks than coherent essays; LongMemEval_s filtered to **306** prompts averaging **~113k** tokens — full-history vs focused prompt gap is large ([Chroma](https://research.trychroma.com/context-rot); [GitHub](https://github.com/chroma-core/context-rot)). Anthropic: every extra token depletes an “attention budget”; n² pairwise attention; position-interpolation models degrade token-position understanding ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### 5.2 Cache misses from unstable prefix

Timestamps in system; tool list reorder; non-deterministic `json.dumps`; floating `instructions` (“today is …”); GPT-5.6 implicit breakpoint on the live user message; Anthropic 20-block lookback miss when the conversation adds **≥20** blocks past the last write without a second interior breakpoint ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [Diagnostics](https://platform.claude.com/docs/en/build-with-claude/cache-diagnostics)). Hot-reloading tools every request → **never** hit. Below-minimum prefix (Anthropic 512–4096 depending on model; OpenAI 1024 visible) → silent no-cache, both cache usage fields **0** ([Prompt caching Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching)).

### 5.3 Context overflow

Hard 400 / `model_context_window_exceeded` (Claude 4.5+ behavior; earlier models validation error unless beta header) ([Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)). OpenAI compact **cannot** run if the dump already exceeds the window ([Compaction](https://developers.openai.com/api/docs/guides/compaction); Codex: compacting at **95%+** leaves no room for the summary — rotate at **~70%** ([Codex #10823](https://github.com/openai/codex/issues/10823))). Anthropic compact min trigger **50k** — smaller windows cannot use server compact ([Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)). Deep Agents: `ContextOverflowError` → immediate summarize+retry ([Deep Agents](https://docs.langchain.com/oss/python/deepagents/context-engineering)). Thinking tokens count toward the window **and** `max_tokens` on extended thinking (interleaved thinking can exceed `max_tokens` — see 01).

### 5.4 Compaction deleting a needed fact

Aggressive summaries drop “subtle but critical context” ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). LangChain persistent summarization **replaces** old messages in state — UI must not assume the checkpoint still has the needle. Clearing tool results without `exclude_tools` on `memory` can delete the only copy of a decision. Encrypted OpenAI compact: you cannot grep for the lost id.

### 5.5 CoT leaking to the end user

In-band `<thinking>` in assistant text is user-visible. Harmony `analysis` must stay off the product channel. Anthropic `display: "summarized"` still ships a **sanitized** trace — treat as user-visible unless you strip. `display: "omitted"` still returns thinking **blocks** (empty text + signature) — do not render them. OpenAI summaries are opt-in via `reasoning.summary` ([Reasoning](https://developers.openai.com/api/docs/guides/reasoning)). Shaikh et al. 2023 (cited by Li): CoT can **increase harmful outputs** — do not stream raw CoT in a consumer UI ([arXiv:2410.21333](https://arxiv.org/abs/2410.21333) citing Shaikh).

### 5.6 Over-long few-shots crowding tools

Shots in the stable prefix compete with tool schemas for the **front** of the window (primacy) and for cache min-tokens. 100 noisy shots (GPT-3-era 2k-window habit) push the query into the U-curve trough and can crowd MCP schemas until the model **never sees** the right tool (Cursor’s motivation for file-based discovery). Format mismatch (shots in prose, live contract JSON-schema) burns tokens for **negative** transfer ([Min](https://arxiv.org/abs/2202.12837); [Mind Your Format](https://arxiv.org/html/2401.06766v3)). Claude: 3–5 canonical shots, not every edge case ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

### 5.7 Thinking-strip cache bust (older Claude)

On Sonnet 4.5 / Haiku 4.5 / earlier: a non-tool user message **strips** prior thinking and invalidates the message cache; Opus 4.5+ / 4.6+ keep thinking ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Mixed-model sessions (Codex: switching models injects a new system prompt and can block compact) ([Codex #10823](https://github.com/openai/codex/issues/10823)).

---

## 6. Enterprise System Design Scenarios

### 6.1 Multi-tenant copilot prompt assembler

**Goal:** shared product policy + per-tenant RAG + per-user history; cache hits on the expensive prefix; no cross-tenant KV.

**Control-plane layout**

```
Ingress → Prompt Shields / ACL
       → Assembler vN
            [tools sorted] [system policy vN] [3–5 canonical shots]
            --- cache breakpoint / prompt_cache_key = tenant XOR "global-policy" ---
            [tenant RAG in <documents>]     // own breakpoint if daily-changing
            [user history trim/summary]
            [user query LAST]
       → Model → tools (least privilege) → pack tool_result after breakpoint
```

**Isolation.** Claude API: workspace per tenant **or** never put tenant PII before the breakpoint. Bedrock/Vertex: assume **org-level** cache share — tenant documents **must** sit after a unique prefix (tenant id as **user** text is not enough if two tenants share identical docs; include tenant salt **after** the global cache point, accepting a miss on the tenant suffix). OpenAI: `prompt_cache_key` for accounting + routing; still ACL retrieval. Do not put secrets in the key (logs).

**Policy.** Standing rules in Anthropic `system` / OpenAI **developer input item** (not ephemeral `instructions` unless you resend every turn). Output contract = strict JSON schema for the copilot card; few-shots use **the same schema**. Delimit tenant docs with Spotlighting/XML.

**Economics [inferred].** 8k global prefix (tools+policy+shots) cached; 4k tenant RAG uncached or separately cached; 1k history; 300 user; 400 out; Sonnet 5. Per turn after warm: 8k×$0.20 + 5.3k×$2 + 400×$10 per MTok ≈ **$0.014**. 1k turns/tenant/day ≈ **$14/day** vs uncached 13.3k×$2 + 400×$10 per MTok ≈ **$0.0306/turn** → **$30.6/day**. Thinking-off for FAQ intents (Sprague).

**NFR.** Sticky `prompt_cache_key` partitions at ~15 rpm/key (OpenAI). Anthropic 5m TTL: if copilot turns are sparse, use **1h** TTL or accept writes. Monitor `cache_read / (read+write+uncached)` per tenant; alert on `tools_changed` after deploys.

**Assembler versioning.** Treat `system` + tool JSON + few-shot set as a **semvered artifact**. A canary that reorders tools by `name` alphabetically vs insertion order is a production incident: Anthropic reports `tools_changed` even on reorder ([Cache diagnostics](https://platform.claude.com/docs/en/build-with-claude/cache-diagnostics)). Pin `json.dumps(..., sort_keys=True, separators=(",", ":"))` in the control plane.

**Thinking policy for copilots.** Default `thinking.type: "disabled"` (Sonnet 5/Opus 5) or `reasoning.effort: "minimal"` / `"none"` where supported for classification, retrieval, and “draft this email.” Reserve adaptive/high effort for math, migrations, and multi-file refactors (Sprague: CoT gains concentrated on symbolic/`=` items ([arXiv:2409.12183](https://arxiv.org/abs/2409.12183))). Never stream Harmony `analysis` or Anthropic summarized thinking into the customer transcript without a separate “show reasoning” control.

**Failure drills.** Tool schema deploy → expected 100% miss + write spike. Compaction of a ticket thread deleting the account-id → require IDs in **memory tool** / structured state, not only in the summary.

### 6.2 Long-horizon coding agent: filesystem offload vs full-window stuffing

**Stuffing 1M** (Claude flat price; GPT-5.4 **2×/1.5× above 272k**): simple assembler, maximal rot (Chroma 113k already hurts), maximal PII in-window, TTFT prefill-bound, cache-friendly **only if** the 1M blob is identical (it won’t be — the repo changes).

**Offload architecture (Cursor + Deep Agents + Anthropic memory + Manus)**

1. Static: short system + tool **names** (+ CLAUDE.md / AGENTS.md). Cursor: skills as files; MCP schemas on disk (**−46.9%** tokens) ([Cursor](https://cursor.com/blog/dynamic-context-discovery)).
2. Runtime: grep/read/search; tool results **>20k** → file + 10-line preview ([Deep Agents](https://docs.langchain.com/oss/python/deepagents/context-engineering)).
3. At **85%** window or Anthropic **150k** compact: summary + persist original to `/conversation_history/...md` or memory tool. Claude Code: compressed context + **five most recently accessed files** ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
4. Sub-agents: tens of thousands of tokens isolated; return **1,000–2,000** to parent ([same](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
5. Cache: breakpoint after tools+system; history append-only; **never** timestamp the system prompt (Manus). 1h TTL if tool calls can exceed 5 minutes.

**Published quality signal.** Context editing alone **+29%**, memory+context editing **+39%** vs baseline on Anthropic agent-search evals (reported via Anthropic-adjacent writeups; confirm against current cookbook) ([Hermes issue citing Anthropic](https://github.com/NousResearch/hermes-agent/issues/526); cookbook stack) ([Cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)). Treat +29/+39 as **vendor-eval**, not your SLO.

**Cost [inferred] vs stuffing.** 200-turn coding session, 80k average in, 1k out, Sonnet 5, 70% cache hit on 60k prefix: roughly 60k×0.3×$0.20 + 20k×$2 + 1k×$10 per MTok per turn ≈ **$0.056/turn** × 200 = **$11.2** + thinking. Stuffing toward 400k uncached: 400k×$2 / 1e6 = **$0.80/turn** × 200 = **$160** on Claude (flat). Same on GPT-5.4 after 272k: **2× input** on the **full session** — a single oversized turn poisons the bill. Offload wins on dollars **and** on Liu/Chroma quality.

**Resilience.** Filesystem is the checkpoint; the window is a cache of the filesystem. Crash: restore files + last summary + assembler vN. Do not rely on in-process KV.

**Security.** Memory files and conversation_history.md are PII/source-code stores; tenant-isolate backends. Tool schemas for `bash` / `write_file` are the blast radius — HITL middleware on destructive tools ([LangChain middleware](https://docs.langchain.com/oss/python/langchain/middleware/overview)).

### 6.3 Trade-off matrix (assembler levers)

| Lever | TTFT | $ | Quality | Prefix stability | Security |
| --- | --- | --- | --- | --- | --- |
| Stable system + 3–5 format-true shots | Neutral | Prefix $ amortized by cache | High if format matches | High | Shots may contain PII |
| Dynamic kNN shots | Worse (retrieval) | Misses on shot block | Higher on mixed tasks | Fragile | Retrieval ACL required |
| Provider thinking / reasoning | Worse TTFT (unless omitted display) | Output $ | Math/symbolic up; other tasks ~0 or down | Thinking echo required | Leak if summarized to UI |
| In-band CoT | Extra decode | Output $ | Same as above | In transcript | User-visible leak |
| Prompt cache | Prefill win | 0.1× reads; 1.25–2× writes | Neutral | Fragile | Isolation + poison persistence |
| Tool-schema deferral / masking | Better | Less input $ | Fewer tool collisions | High if names stable | Smaller leak surface |
| Clear tool results | Better | Linear | OK if re-fetchable | Bust then restabilize | Smaller window |
| Abstractive compact | Better | Extra LLM call | Lossy | New prefix | Summary is a PII store |
| Filesystem offload | Better | Linear | High if agent re-reads | Parent prefix stable | Files need ACL |
| Stuff 1M | Worse prefill | Full input; GPT-5.4 cliff | Rot | Easy if static | Max PII |

### 6.4 Coverage check

| Scope item | Topology | Economics | Resilience | Security | Failures | Design |
| --- | --- | --- | --- | --- | --- | --- |
| System prompts / contracts / Anthropic vs OpenAI roles | §1.2 | tools+system every turn | `instructions` not in `previous_response_id` | policy versioning | timestamp in system | copilot assembler |
| Few-shot static/dynamic, k, format | §1.3 | shots in prefix $ | adding shots busts cache | PII in shots | crowding tools | 3–5 canonical |
| CoT / hidden thinking / when it hurts | §1.4 | thinking = output $ | echo signatures | CoT leak | CoT on classification | thinking-off for FAQ |
| Context budgets / LITM / allocation | §1.5 | 272k cliff; 150k compact | overflow vs compact | 1M PII | U-curve, rot | working context << max |
| Dynamic assembly / cache / middleware | §1.6 | schema tax; MCP −46.9% | deterministic pack | tenant cache | 20-block / implicit writes | offload vs stuff |

---

## Sources

1. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — Anthropic definition, attention budget, altitude of system prompts, 3–5 canonical examples, JIT retrieval, compaction, memory, sub-agents 1–2k returns, Claude Code + last 5 files
2. https://docs.langchain.com/oss/python/langchain/context-engineering — LangChain definition; transient vs persistent; `create_agent` + SummarizationMiddleware example
3. https://platform.claude.com/docs/en/build-with-claude/prompt-caching — tools→system→messages, 4 breakpoints, 20-block lookback, TTL 5m/1h, invalidation, isolation, minima, `max_tokens: 0` pre-warm, mid-conversation system
4. https://platform.claude.com/docs/en/build-with-claude/cache-diagnostics — `system_changed` / `tools_changed` / `messages_changed`
5. https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching — last-tool breakpoint, `defer_loading`, invalidation table
6. https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices — XML, 3–5 examples, query-at-end ≤30%, thinking in shots, format matching
7. https://academy.claude.com/courses/building-with-the-claude-api/structure-with-xml-tags — XML delimiters
8. https://platform.claude.com/docs/en/api/csharp/beta/messages/count_tokens — no system role in classic messages; `clear_at`; cache_control TTL 5m/1h
9. https://platform.claude.com/docs/en/build-with-claude/extended-thinking — budget_tokens deprecation, adaptive migration, thinking_tokens field
10. https://archive.ph/b7FDC — Adaptive thinking billing: output = full thinking; omitted display; billed ≠ visible
11. https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-extended-thinking.html — billed full thinking not summary; summarizer unbilled
12. https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-adaptive-thinking.html — Sonnet 5/Opus 5 default adaptive if thinking omitted; `disabled` required to turn off
13. https://platform.claude.com/docs/en/build-with-claude/compaction — trigger 150k/min 50k, pause_after, instructions replace, iterations billing, null content on tool-call summarizer
14. https://platform.claude.com/docs/en/build-with-claude/context-editing — clear_tool_uses defaults 100k/keep 3, clear_at_least vs cache, thinking clear vs cache
15. https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools — compaction vs tool clearing vs memory_20250818
16. https://platform.claude.com/docs/en/build-with-claude/context-windows — 1M/128k vs 200k; context-awareness tags; stop_reason model_context_window_exceeded
17. https://claude.com/blog/1m-context-ga — 1M GA, standard pricing, no long-context premium, 600 images/PDF pages
18. https://docs.anthropic.com/en/docs/about-claude/pricing — tool-use system prompt table (Sonnet 5 354/474), computer ~4590, browser ~6670, cache 1.25×/2×/0.1×, Fable hit 2.5%
19. https://developers.openai.com/api/docs/guides/prompt-caching — 1024 min, GPT-5.6 breakpoints, 1.25× write, 0.1× read, 30m TTL, 15 rpm/key, instructions cannot hold breakpoint
20. https://developers.openai.com/cookbook/examples/prompt_caching101 — ≤80% latency >10k, org isolation, ZDR note
21. https://openai.com/index/api-prompt-caching/ — 2024 launch: org isolation
22. https://developers.openai.com/api/docs/guides/reasoning — effort ladder, summaries, encrypted_content, reasoning billed as output, occupies window
23. https://openai.com/index/new-tools-and-features-in-the-responses-api/ — reasoning summaries no extra cost
24. https://help.openai.com/en/articles/4936856-tokens-count-them — reasoning tokens billed as output
25. https://developers.openai.com/api/docs/guides/prompt-engineering — instructions priority and per-request lifetime; Markdown+XML; static-first for cache
26. https://developers.openai.com/api/docs/guides/migrate-to-responses — instructions not carried by previous_response_id
27. https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create/ — developer replaces system on o1+
28. https://developers.openai.com/cookbook/articles/openai-harmony — system>developer>user>assistant>tool; developer = app system prompt
29. https://developers.openai.com/api/docs/guides/structured-outputs — json_schema vs json_object; strict schema
30. https://developers.openai.com/api/docs/models/gpt-5.4 — 1.05M context, 128k out, >272K 2× input / 1.5× output full session
31. https://developers.openai.com/api/docs/guides/compaction — server-side compact_threshold; /responses/compact; store=false ZDR; do not prune output
32. https://community.openai.com/t/compact-a-response-with-previous-response-id/1372502/10 — all prior user messages kept verbatim; encrypted compaction item
33. https://community.openai.com/t/will-developer-role-messages-persist-with-previous-response-id-in-responses-api/1313700 — developer items persist; instructions do not
34. https://community.openai.com/t/gpt-5-6-prompt-caching-fails-on-partial-prefixes/1386887 — implicit breakpoint; explicit fix
35. https://github.com/openai/codex/issues/35300 — 0% vs 98.6% hits with prompt_cache_breakpoint
36. https://github.com/openai/codex/issues/10823 — compact fails near 95%; rotate ~70%; model switch injects system prompt
37. https://developers.openai.com/api/docs/guides/websocket-mode — store=false connection-local previous_response
38. https://openai.github.io/openai-agents-python/context/ — RunContextWrapper not model-visible
39. https://aclanthology.org/2024.tacl-1.9/ — Liu et al. Lost in the Middle, TACL 2024, pp. 157–173
40. https://arxiv.org/abs/2307.03172 — Lost in the Middle preprint; GPT-3.5 closed-book 56.1%; U-curve
41. https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00638/119630 — MIT Press HTML
42. https://arxiv.org/abs/2201.11903 — Wei et al. CoT; 8 exemplars; GSM8K more than doubled; SST-2 54.3–93.4% citation
43. https://proceedings.neurips.cc/paper_files/paper/2022/file/9d5609613524ecf4f15af0f7b31abca4-Paper-Conference.pdf — Wei NeurIPS PDF
44. https://arxiv.org/abs/2205.11916 — Kojima Zero-shot-CoT
45. https://proceedings.neurips.cc/paper/2022/file/8bb0d291acd4acf06ef112099c16f326-Paper-Conference.pdf — Kojima: MultiArith 17.7→78.7, GSM8K 10.4→40.7
46. https://arxiv.org/abs/2409.12183 — Sprague To CoT or not to CoT; 110 papers; 95% MMLU gain from “=”; +14.2/+12.3/+6.9 vs ~0 elsewhere
47. https://arxiv.org/abs/2410.21333 — Li et al. CoT can hurt; o1-preview −36.3 pp; +331% iterations
48. https://arxiv.org/abs/2210.03629 — Yao ReAct; +34 / +10 success; 1–2 shots
49. https://arxiv.org/abs/2005.14165 — Brown et al. GPT-3 few-shot ICL
50. https://arxiv.org/abs/2202.12837 — Min et al. demonstrations; format fidelity 95%/82% retained gains
51. https://aclanthology.org/2022.emnlp-main.155/ — Yoo et al. ground-truth labels can matter
52. https://arxiv.org/html/2401.06766v3 — Mind Your Format; templates do not transfer
53. https://arxiv.org/abs/2502.04580 — ICL technical debt; many-shot ~1.5× Bayes-optimal samples
54. https://arxiv.org/abs/2305.11170 — DYNAICL per-input k
55. https://arxiv.org/html/2403.06402v1 — AICL instance-specific κ(x)
56. https://arxiv.org/html/2507.16217v2 — compute-optimal many-shot; cache random 80 + similar 20
57. https://sambanova.ai/blog/many-shot-prompting-a-practical-guide-to-icl — taper ~50–70/class; similarity vs random
58. https://manus.im/en/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus — KV-hit as #1 metric; 100:1 I/O; no timestamps; append-only; tool masking
59. https://www.zenml.io/llmops-database/context-engineering-strategies-for-production-ai-agents — Manus 100:1; logit mask
60. https://cursor.com/blog/dynamic-context-discovery — dynamic vs static; MCP schemas on disk; −46.9% tokens
61. https://cursor.com/docs/skills — skills as files; progressive disclosure
62. https://docs.langchain.com/oss/python/deepagents/context-engineering — 20k offload, 10-line preview, 85% summarize, keep 10%, 170k/6 fallback
63. https://www.langchain.com/blog/context-management-for-deepagents — offload then summarize
64. https://reference.langchain.com/python/deepagents/middleware/summarization — conversation_history.md; compact_conversation ~50% gate
65. https://reference.langchain.com/python/langchain/agents/middleware/summarization/SummarizationMiddleware — trigger/keep API
66. https://github.com/langchain-ai/langchain/blob/master/libs/langchain_v1/langchain/agents/middleware/summarization.py — 3 retries; no fake summary
67. https://forum.langchain.com/t/how-do-contexteditingmiddleware-and-summarizationmiddleware-interact-when-used-together-combining-contexteditingmiddleware-summarizationmiddleware-execution-order-and-behavior-when-both-trigger/3463 — before_model vs wrap_model_call
68. https://docs.langchain.com/oss/python/langchain/middleware/overview — create_agent middleware list
69. https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py — llm_input_messages vs RemoveMessage
70. https://docs.langchain.com/oss/python/langgraph/checkpointers — durable checkpoints, pending writes, DeltaChannel
71. https://aws.amazon.com/bedrock/prompt-caching/ — ≤90% cost, ≤85% latency
72. https://aws.amazon.com/blogs/machine-learning/effectively-use-prompt-caching-on-amazon-bedrock/ — static prefix, exact match
73. https://genai.owasp.org/llmrisk/llm01-prompt-injection/ — LLM01:2025
74. https://owasp.github.io/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf — Top 10 PDF
75. https://arxiv.org/abs/2403.14720 — Spotlighting; ASR >50% → <2%
76. https://learn.microsoft.com/en-us/azure/foundry/guardrails/how-to-create-guardrails — document delimiters, Prompt Shields
77. https://research.trychroma.com/context-rot — Context Rot technical report
78. https://github.com/chroma-core/context-rot — 18 models; LongMemEval ~113k
79. https://www.nirmalya.net/posts/2026/02/ttft-optimisation-practical-patterns/ — measured cache-hit TTFT
80. https://github.com/nirmalyaghosh/ttft-benchmark — N=20 methodology
81. https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/ — APC prefill-only
82. https://github.com/NousResearch/hermes-agent/issues/526 — +29% context editing / +39% +memory (Anthropic-eval citation)
83. https://github.com/NousResearch/hermes-agent/issues/27918 — GPT-5.4/5.5 Codex OAuth 272k vs API 1.05M
84. https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html — Bedrock checkpoints, cached tokens vs TPM
85. https://platform.claude.com/docs/en/about-claude/pricing — (alt URL) cache break-even 1-read / 2-read
86. https://developers.openai.com/cookbook/examples/responses_api/reasoning_items — encrypted reasoning replay for ZDR
87. https://navendu.me/posts/lessons-context-engineering/ — Manus notes: restorable compression, controlled randomness
88. https://www.joshbeckman.org/notes/920141445 — Manus tool-prefix prefills (browser_, shell_)
89. https://docs.langchain.com/oss/python/langgraph/add-memory — trim_messages vs summarize (if using LangGraph memory docs)
90. https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses — Azure server-side compaction
91. https://aichangewatch.com/rankings/longest-context — 1.05M GPT-5.4 / 1M Claude survey (secondary)
92. https://www.anthropic.com/research/prompt-injection-defenses — Anthropic injection defenses (RL + classifiers)
93. https://deepwiki.com/anthropics/prompt-eng-interactive-tutorial/5.1-separating-data-from-instructions — XML around variable data
94. https://arxiv.org/html/2301.00234v6 — ICL survey (demonstration format)
95. https://iclr.cc/virtual/2023/poster/11003 — ReAct ICLR poster abstract (+34% / +10%)
96. https://developers.openai.com/api/docs/pricing — do not copy tables; used only to confirm 01 is source of truth for list prices
97. https://platform.claude.com/docs/en/about-claude/models/overview — model overview cross-check with 01
98. https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-compaction.html — Bedrock compaction iterations billing
