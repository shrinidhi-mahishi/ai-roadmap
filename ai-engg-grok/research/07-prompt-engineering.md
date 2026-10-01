# Research: Prompt Engineering

**Date researched**: 2026-09-30
**Sources consulted**: 28

## 1. System Topology & Mechanics

Prompt engineering is the process of designing instructions so a language model produces better, more reliable responses. A prompt is not “search query text”; it is the control surface for next-token prediction over a shared **context window** that holds system/developer instructions, user input, examples, tool schemas, retrieved or tool-returned data, conversation history, and the tokens the model is about to generate ([System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide); [OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering); [Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

### Control plane vs. data plane (prompt assembly)

| Plane | Role in prompting | Concrete artifacts |
| --- | --- | --- |
| **Control plane** | Stable policy: role, constraints, tool-use rules, output schema, safety, cache affinity | System / `developer` / `instructions`; tool definitions; `response_format` / structured-output schemas; `prompt_cache_key` / `cache_control` ([OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering); [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [Anthropic Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) |
| **Data plane** | Per-request payload: user text, documents, tool results, turn history | User/assistant messages; `tool_result` blocks; variable context near the end of the prompt ([OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering); [Anthropic Tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)) |

Text never enters the model as raw characters: it is **tokenized**, then attention selects which context tokens matter most for the next token. Extra tokens compete for a finite attention budget—relevance beats stuffing ([System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide); [Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### Prompt as a component graph (not one blob)

Treat a prompt as composable components: instruction, context, constraints, examples, output format, and (for agents) tool-use decision rules. Diagnose failures by missing/unclear component rather than rewriting everything ([System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)). Anthropic’s agent guidance places system prompts in a “Goldilocks altitude”: specific enough to steer, not brittle if-else trees or vague slogans; structure with XML tags or Markdown sections such as background, tool guidance, and output description ([Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

OpenAI’s recommended **developer-message** shape (order may vary by model): Identity → Instructions → Examples → Context (variable context usually last). `instructions` / `developer` messages outrank `user` content—analogous to a function definition vs. its arguments. Use Markdown + XML delimiters for section boundaries ([OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)). On o1-era and newer Chat Completions APIs, `developer` replaces prior `system` for this privileged role ([OpenAI Chat API reference](https://developers.openai.com/api/reference/resources/chat)).

### In-context learning topology (zero / one / few / many-shot)

**In-context learning (ICL)** steers behavior with demonstrations inside the prompt without weight updates. Brown et al. define zero-shot (instruction only), one-shot (one exemplar), and few-shot (typically ~10–100 exemplars fitting the context, GPT-3 \(n_{\text{ctx}}=2048\)) ([Brown et al., 2020 — arXiv](https://arxiv.org/abs/2005.14165); [System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)). Diverse, representative examples beat long lists of near-duplicates; Anthropic recommends ~**3–5** well-crafted examples for Claude and explicitly warns against laundry-list edge-case stuffing ([Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices); [Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### Reasoning & tool-loop topologies

| Pattern | Mechanism | When it applies |
| --- | --- | --- |
| **Chain-of-thought (CoT)** | Few-shot exemplars that include intermediate reasoning steps before the answer | Multi-step arithmetic / symbolic / some commonsense; emerges ~≥100B-scale in Wei et al. ([Wei et al., 2022](https://arxiv.org/abs/2201.11903); [Google Research blog](https://research.google/blog/language-models-perform-reasoning-via-chain-of-thought/)) |
| **Zero-shot CoT** | Append “Let’s think step by step” (then extract answer) | Same class of tasks without hand-written reasoning exemplars ([Kojima et al., 2022](https://arxiv.org/abs/2205.11916)) |
| **ReAct** | Interleave Thought → Action → Observation with tools/env | Missing external facts; interactive tasks; reduces pure-hallucinated CoT ([Yao et al., 2022](https://arxiv.org/abs/2210.03629); [System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)) |
| **Native tool / function calling** | Model emits structured `tool_use` / function args; app or server executes; results return as `tool_result` | Production agents; schemas replace freeform “call tool X” prose ([Anthropic Tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview); [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)) |
| **Structured Outputs** | Constrained decoding / schema adherence (`json_schema` + `strict`, or `strict` tools) | Typed pipelines, enums, required fields; schema ≠ value correctness ([OpenAI Structured Outputs announcement](https://openai.com/index/introducing-structured-outputs-in-the-api/)) |

**Prompt engineering vs. context engineering**: Anthropic treats the former as how you write/organize instructions; the latter as curating *all* tokens at inference time (tools, MCP, history, retrieved data) under a finite attention budget and \(n^2\) pairwise attention cost ([Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). This research stays on prompting mechanics; retrieval/RAG systems are out of scope except where they appear as prompt failure modes.

### Long-context prompting mechanics

For Claude long documents: put longform data **near the top**, query/instructions/examples below; wrap multi-doc inputs in XML with content + metadata tags; ask the model to quote relevant passages before answering ([Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)). Parallel tool calling is the default on recent Claude models and is steerable via an explicit `<use_parallel_tool_calls>` block (docs claim boost toward ~**100%** independent parallelization when prompted) ([Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

## 2. Token Economics & NFR Metrics

### Accuracy deltas (published prompting benchmarks)

These are **historical paper/blog numbers** on specific models—not transferable as “expected lift on Claude/GPT-2026.” Use them to size the *order of effect* of prompting techniques.

| Technique | Setting | Metric | Result | Source |
| --- | --- | --- | --- | --- |
| Few-shot CoT (8 exemplars) | PaLM 540B, GSM8K | Accuracy | **~57–58%** CoT (blog: **58%** with external calculator for fair compare) vs prior SOTA **55%** (finetuned GPT-3 + verifier); standard prompting far lower—paper: performance **more than doubled** on GSM8K for largest models | [Wei et al., 2022](https://arxiv.org/abs/2201.11903); [Google CoT blog](https://research.google/blog/language-models-perform-reasoning-via-chain-of-thought/) |
| Self-consistency over CoT | PaLM 540B, GSM8K | Accuracy | **74%** (majority vote over sampled reasoning paths) | [Google CoT blog](https://research.google/blog/language-models-perform-reasoning-via-chain-of-thought/) (citing follow-up) |
| Zero-shot CoT (“Let’s think step by step”) | InstructGPT text-davinci-002 | Accuracy | MultiArith **17.7% → 78.7%**; GSM8K **10.4% → 40.7%** | [Kojima et al., 2022](https://arxiv.org/abs/2205.11916) |
| Exemplar-order sensitivity | GPT-3, SST-2 | Accuracy range | **54.3% → 93.4%** under different few-shot permutations (Zhao et al., cited in Wei) | [Wei et al., 2022](https://arxiv.org/abs/2201.11903) |
| ReAct vs Act / IL | PaLM-540B, ALFWorld (2-shot) | Success rate | ReAct **71%** vs Act-only **45%** vs BUTLER IL **37%** (~**+34** pp vs IL baseline) | [Yao et al., 2022](https://arxiv.org/abs/2210.03629); [Google ReAct blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/) |
| ReAct vs Act / IL | WebShop (1-shot) | Success rate | ReAct **40%** vs Act **30.1%** vs IL **29.1%** (~**+10** pp absolute vs IL) | [Google ReAct blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/) |
| ReAct / CoT hybrids | HotpotQA (6-shot EM) / FEVER (3-shot acc.) | EM / Acc | Best ReAct+CoT: **35.1** / **64.6**; ReAct alone **27.4** / **60.9**; Standard **28.7** / **57.1** | [Google ReAct blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/) |
| Structured Outputs | OpenAI complex JSON-schema eval | Schema adherence | `gpt-4o-2024-08-06` + Structured Outputs **100%**; unconstrained improved model **93%**; `gpt-4-0613` **&lt;40%** | [OpenAI Structured Outputs announcement](https://openai.com/index/introducing-structured-outputs-in-the-api/) |

CoT is an **emergent** ability of scale in Wei et al.: below ~**100B** parameters, fluent but illogical chains can *hurt* vs standard prompting ([Wei et al., 2022](https://arxiv.org/abs/2201.11903)). Gains are largest on hard multi-step sets (e.g. GSM8K) and near-zero/negative on easy single-op subsets ([Wei et al., 2022](https://arxiv.org/abs/2201.11903)).

**Cost of reasoning tokens**: CoT, ReAct thoughts, and “thinking”/extended-reasoning modes increase **output** (and sometimes input) tokens. Newsletter guidance: add reasoning only when the task needs it—otherwise you pay latency and $/token for no accuracy gain ([System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)). [inferred] On modern APIs, prefer vendor thinking/`effort` controls over dumping long freeform CoT into user-visible answers when you only need the final structured field.

### Prompt caching multipliers (verified 2026-09-30)

**Anthropic** (same multipliers documented on Pricing and Prompt caching pages):

| Charge type | Multiplier vs base input | Notes |
| --- | --- | --- |
| 5-minute cache **write** | **1.25×** | Default TTL; refreshed on hit at no extra write charge |
| 1-hour cache **write** | **2×** | Longer TTL for bursty traffic |
| Cache **hit / refresh** | **0.1×** standard; **0.025×** Claude Fable 5.1 / Mythos 5.1; **0.05×** Claude Opus 5.5 | See footnotes on pricing table |

Example absolute rates (Global Standard): Claude Sonnet 5 — base input **$2 / MTok**, 5m write **$2.50**, 1h write **$4**, hit **$0.20**, output **$10 / MTok** ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing); [Anthropic Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

Break-even (5m TTL, 0.1× reads): one write + one full read = **1.35×** vs **2×** uncached for two requests; across ten requests (1 write + 9 reads) ≈ **2.15×** vs **10×** uncached ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) documents the same 1.35× / 2.15× arithmetic for its 1.25×/0.1× GPT-5.6+ schedule; Anthropic skill notes match) ([Anthropic prompt-caching skill](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/prompt-caching.md)).

Minimum cacheable prefix (Claude API / Platform): **1,024** tokens for Claude Sonnet 5 / Sonnet 4.6 / Opus 4.8 (selected list); **4,096** tokens for Claude Opus 4.6, Opus 4.5, and Haiku 4.5. Below minimum → processed uncached with no error; check `cache_creation_input_tokens` / `cache_read_input_tokens` ([Anthropic Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Cacheable prefix order: `tools` → `system` → `messages` up to the breakpoint. Up to **4** explicit breakpoints; automatic caching uses one slot. Cookbook reports **~2–10×** wall-clock speedup on large-prefix cache hits in demo workloads ([Anthropic cookbook — prompt_caching](https://github.com/anthropics/anthropic-cookbook/blob/main/misc/prompt_caching.ipynb)).

**OpenAI** (Prompt caching guide, verified 2026-09-30):

| Generation | Write | Read | Lifetime / routing notes |
| --- | --- | --- | --- |
| GPT-5.6 and later | **1.25×** uncached input | **0.1×** (most); **0.05×** GPT-6.1 Sol | `prompt_cache_options.ttl` = **`30m`** (default); refresh on reuse; optional explicit breakpoints |
| Earlier models | No separate write surcharge (model-dependent cached-input rate; historically up to **~50–75%** off on some SKUs—re-check pricing) | Model-dependent | `prompt_cache_retention` (e.g. **`24h`** where allowed); `prompt_cache_key` important for routing |

Minimum cacheable length **1,024** tokens for GPT-5.6+ (hidden system tokens do not count). Cache lives **per machine**; traffic above ~**15 RPM** can overflow routing—use stable `prompt_cache_key` on pre-5.6 models; partition busy keys ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Guide claims discounts **up to 95%** on cached input at the aggressive end of the schedule (0.05× = 95% off) ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

**Prompt-layout rule for cache hit rate**: put **stable** system/developer instructions, tool schemas, and few-shot blocks first; put **volatile** user/query/context last so the longest common prefix remains cacheable ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)).

### Worked cost sketch (prompt-heavy agent turn)

Claude Sonnet 5, shared 20K-token system+tools prefix, 2K volatile user tokens, 1K output; cache warm (0.1× on 20K):

\[
\begin{align*}
C_{\text{uncached}} &= (22\text{K}/10^6)\cdot \$2 + (1\text{K}/10^6)\cdot \$10 = \$0.044 + \$0.010 = \$0.054 \\
C_{\text{warm}} &= (20\text{K}/10^6)\cdot \$0.20 + (2\text{K}/10^6)\cdot \$2 + (1\text{K}/10^6)\cdot \$10 \\
&= \$0.004 + \$0.004 + \$0.010 = \$0.018
\end{align*}
\]

≈ **67%** input+output cost reduction on that turn shape ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). First-turn write at 1.25× on 20K costs \((20\text{K}/10^6)\cdot\$2.50=\$0.050\) for the prefix alone before hits pay back.

### Latency / throughput NFRs

> ⚠️ Limited public data available for this dimension. Vendors publish cache latency benefits qualitatively (OpenAI: “reduce latency”; Anthropic cookbook: ~2–10× on large cached prefixes) but not standardized p50/p95 TTFT for “prompt engineering” as a product. Do not invent SLA numbers.

[inferred] Prefill dominates TTFT on long prompts; cache hits skip recomputing KV for the cached prefix, so TTFT improvements track prefix length and hit rate more than decode TPS.

## 3. Distributed Resilience & State

> ⚠️ Limited public data available for this dimension. Prompt engineering literature does not specify Temporal/Kafka orchestration, distributed locks, or enterprise circuit-breaker libraries. Below are **prompt- and API-level** resilience patterns that act as the control-plane equivalents.

### Ephemeral prompt-cache state

Prompt caches are **not durable application state**. Anthropic default TTL **5 minutes** (optional **1 hour** at 2× write); OpenAI GPT-5.6+ minimum lifetime **`30m`** via `ttl`, with possible longer retention. Entries are machine-local; routing miss ⇒ cache miss even if another replica holds the prefix ([Anthropic Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Design for miss: full uncached prefill cost/latency is always the fallback.

### Tool-loop guards (prompt-encoded circuit breakers)

Unbounded “use the search tool” prompts cause repeat queries and cost blowups. Production-shaped prompts encode: when to call tools, how to judge success, diversify query on failure, **max N** calls, and explicit “say you cannot answer” stop ([System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)). Anthropic agents should specify tool-use rules and avoid redundant verification loops that burn tokens ([Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices); [Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### Schema retry / constrained decoding

Structured Outputs remove the “invalid JSON → retry” class of failures when schema is followed and there is no refusal / truncation (`finish_reason`). Content errors inside valid JSON still need app-level validation and retry-with-correction prompts ([OpenAI Structured Outputs announcement](https://openai.com/index/introducing-structured-outputs-in-the-api/); [OpenAI Structured Outputs docs](https://developers.openai.com/api/docs/guides/structured-outputs)). Anthropic migrates away from assistant prefills toward structured outputs / tools / direct “no preamble” instructions ([Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

### Compaction as soft checkpointing

For long agent runs, Anthropic documents **compaction** (summarize near window limit, restart with summary + recent files) and **structured note-taking** outside the window—prompt/context resilience against context rot, not ACID durability ([Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

[inferred] Pair prompt max-iteration caps with application hard caps (HTTP deadline, max tool RPM, spend ceiling). Prompts alone are not an enforcement boundary.

## 4. Enterprise Security & Governance

### Privilege hierarchy & untrusted input

OpenAI: **never** interpolate untrusted user/web/tool text into `developer` / `instructions`; pass untrusted content as **user** (or tool-result) data so it cannot claim developer privilege ([OpenAI Safety in building agents](https://developers.openai.com/api/docs/guides/agent-builder-safety)). OWASP LLM Prompt Injection Cheat Sheet: separate `SYSTEM_INSTRUCTIONS` from `USER_DATA_TO_PROCESS`; treat user content as data, not commands ([OWASP LLM Prompt Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)).

### Prompt injection surface

Injections aim to override instructions, exfiltrate data via tools, or divert behavior. Defenses are layered: role separation, structured I/O between agent nodes (enums/schemas), least-privilege tools, human confirmation for consequential actions, input length limits, red-teaming—not a single “ignore jailbreaks” line in the system prompt ([OpenAI Designing agents to resist prompt injection](https://openai.com/index/designing-agents-to-resist-prompt-injection/); [OpenAI Safety best practices](https://developers.openai.com/api/docs/guides/safety-best-practices); [System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)).

### Structured auditability

Structured Outputs expose a programmatic `refusal` channel when safety policy blocks schema-shaped answers—distinguish refusal from parse failure ([OpenAI Structured Outputs announcement](https://openai.com/index/introducing-structured-outputs-in-the-api/)). OpenAI recommends `safety_identifier` on user-facing products for abuse monitoring ([OpenAI Safety best practices](https://developers.openai.com/api/docs/guides/safety-best-practices)).

### PII in prompts & caches

> ⚠️ Limited public data available for this dimension. Vendor docs recommend PII redaction/guardrails before model entry ([OpenAI Safety in building agents](https://developers.openai.com/api/docs/guides/agent-builder-safety)) but do not publish enterprise NER accuracy SLAs for prompt pipelines. Prompt caches retain prefix tokens for TTL windows—treat cached system+few-shot content as sensitive if it embeds secrets or customer data ([inferred] from cache TTL mechanics in [Anthropic Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) / [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Separate `prompt_cache_key` per tenant to reduce cross-user cache probing ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

### Tool RBAC via prompting + platform

Prompting can *describe* when tools may be used; enforcement must live in the tool executor (authz, allowlists, confirmations). Anthropic: clear tool descriptions / schemas; avoid overlapping tools that create ambiguous choice ([Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Anthropic Tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)). OpenAI Structured Outputs with `strict: true` on tools constrains **argument shape**, not whether the call is authorized ([OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)).

## 5. Production Failure Modes

| Failure | Symptoms | Prompt / system mitigation |
| --- | --- | --- |
| **Ambiguous prompt / missing components** | Model invents audience, format, or task | Component checklist: instruction + context + constraints + format ([System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)) |
| **Attention / context rot** | Ignores mid-prompt facts; worse as window fills | Fewer high-signal tokens; long docs top + quote-then-answer; compaction ([Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)) |
| **Bad / non-diverse few-shots** | Systematic miss on sarcasm, rare classes, formats | Curate diverse canonical examples; avoid near-duplicates ([System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide); [Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)) |
| **Exemplar-order / prompt brittleness** | Large accuracy swings without code changes | Eval suites; pin model snapshots; version prompts as code ([Wei et al., 2022](https://arxiv.org/abs/2201.11903); [OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering); [OpenAI Prompting](https://developers.openai.com/api/docs/guides/prompting)) |
| **CoT on small / wrong tasks** | Longer wrong answers; higher $ | Use CoT when multi-step; skip on trivial extract/classify ([Wei et al., 2022](https://arxiv.org/abs/2201.11903); [System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)) |
| **Tool loops** | Repeated identical searches; runaway cost | Max calls, success criteria, stop condition in prompt + hard app caps ([System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)) |
| **Invalid / partial JSON** | Downstream parse crashes | Structured Outputs / strict tools; handle `refusal` and truncation ([OpenAI Structured Outputs announcement](https://openai.com/index/introducing-structured-outputs-in-the-api/)) |
| **Hallucinated tool parameters** | 4xx from APIs; invented IDs | Descriptive JSON schemas; `strict: true`; never guess dependent params in parallel ([Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices); [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)) |
| **Prompt injection** | Policy bypass; data exfil via tools | Role isolation; structured handoffs; least privilege; confirmations ([OpenAI Agent builder safety](https://developers.openai.com/api/docs/guides/agent-builder-safety); [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)) |
| **Over-trigger / under-trigger tools** | Wrong aggressiveness after model upgrades | Retune system prompt language; newer Claude may overtrigger if old “CRITICAL: MUST” phrasing remains ([Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices); [Prompting Claude Sonnet 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5)) |
| **Cache miss storms** | Cost/latency spikes under multi-host routing | Stable keys, ≥15 RPM key partitioning (pre-5.6 OpenAI), prefix ≥ minimum tokens ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)) |
| **Benchmark gaming / weak evals** | Prompt looks good on cherry-picked cases | Holdout sets; regression evals on every prompt publish ([OpenAI Prompting](https://developers.openai.com/api/docs/guides/prompting); [System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide)) |

Schema adherence ≠ semantic correctness: Structured Outputs can still emit wrong field *values* (bad math, wrong IDs)—split tasks or add examples ([OpenAI Structured Outputs announcement](https://openai.com/index/introducing-structured-outputs-in-the-api/)).

## 6. Enterprise System Design Scenarios

### Scenario A — Multi-tenant support classifier (high RPM, shared system prompt)

**Design**: Large frozen developer/system block (policy + 3–5 few-shots + label taxonomy) + per-ticket user payload; Structured Outputs enum for labels; Anthropic automatic `cache_control` or OpenAI implicit/explicit cache on the shared prefix; per-tenant `prompt_cache_key` for accounting/isolation.

**Economics**: Sonnet 5 warm-cache sketch above (~**$0.018**/turn for 22K in / 1K out shape) vs **$0.054** cold—scale linearly with RPM. Prefer Haiku 4.5 (**$1 / $5** in/out MTok, hit **$0.10**) if evals meet SLA ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

**Trade-offs**: Cache TTL (5m vs 1h write cost) vs traffic burstiness; few-shot tokens buy accuracy but raise minimum-cache threshold and prefill cost on miss.

### Scenario B — Tool-using research agent

**Design**: ReAct-style decision rules in system prompt (when to search, max 3 searches, stop if insufficient) + native tools with strict schemas + parallel-call policy for independent fetches ([System Design Newsletter — Prompt Engineering Guide](https://newsletter.systemdesign.one/p/prompt-engineering-guide); [Yao et al., 2022](https://arxiv.org/abs/2210.03629); [Anthropic Tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)).

**Trade-offs**: More reasoning/tool tokens vs hallucination reduction (ReAct ALFWorld **71%** vs Act **45%** in paper setting—directional only for modern models). Keep tool set minimal and non-overlapping ([Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### Scenario C — Structured extraction / form fill

**Design**: Natural-language task in developer message; field semantics in JSON Schema `description`s; `strict: true` Structured Outputs or strict tools—avoid “ALWAYS output JSON” prompt spam when the API enforces schema ([OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs); [OpenAI announcement](https://openai.com/index/introducing-structured-outputs-in-the-api/)).

**Trade-off matrix (prompting levers)**:

| Lever | Cost | Latency | Accuracy / reliability | Ops complexity |
| --- | --- | --- | --- | --- |
| Zero-shot clear instruction | Lowest tokens | Lowest | Baseline | Low |
| Few-shot (3–5 diverse) | +example tokens; helps cache min length | +prefill | Format/style lift; order-sensitive | Medium (curate/eval) |
| CoT / thinking | +output tokens | Higher TTFT/TTL | Large on multi-step (historical GSM8K doubles); wasteful on trivial tasks | Medium |
| ReAct / tools | +rounds × tokens | Multi-RTT | External-grounded facts; loop risk | High (tools, caps) |
| Structured Outputs | Similar tokens; less retry waste | Slight decode overhead possible [inferred] | **100%** schema match on OpenAI’s published eval (with caveats) | Low–medium (schema design) |
| Prompt caching | 1.25× first write; 0.1× hits | TTFT↓ on hit | N/A (cost/latency) | Medium (prefix discipline, keys) |

### Capacity planning notes

- Pin **model snapshots** and treat prompts as versioned code with CI evals ([OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering); [OpenAI Prompting](https://developers.openai.com/api/docs/guides/prompting)).
- Plan token budgets as: \(I_{\text{stable}} + I_{\text{volatile}} + O_{\text{answer}} + O_{\text{reasoning/tools}}\); apply cache hit fraction \(f\) at vendor hit rate.
- Re-eval prompts on model upgrades—Claude Sonnet 5 / Opus generations change default thinking, verbosity, and tool aggressiveness ([Prompting Claude Sonnet 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5); [Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

> ⚠️ Limited public data available for this dimension. No vendor-published multi-tenant “prompt engineering cluster” RPM capacity study; scale numbers must come from your own load tests against TPM/RPM quotas.

## Sources

- [1] https://newsletter.systemdesign.one/p/prompt-engineering-guide — System Design Newsletter #169: Prompt Engineering deep dive (primary article)
- [2] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices — Anthropic Claude prompting best practices
- [3] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 — Anthropic Sonnet 5-specific prompting
- [4] https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — Anthropic: prompt vs context engineering
- [5] https://claude.com/blog/best-practices-for-prompt-engineering — Anthropic 2026 prompt engineering blog
- [6] https://developers.openai.com/api/docs/guides/prompt-engineering — OpenAI prompt engineering guide
- [7] https://developers.openai.com/api/docs/guides/prompting — OpenAI prompting / prompts-as-code guidance
- [8] https://developers.openai.com/api/docs/guides/prompt-guidance — OpenAI outcome-first prompt guidance
- [9] https://help.openai.com/en/articles/6654000-best-practices-for-prompt-engineering-with-the-openai-api — OpenAI Help Center prompt best practices
- [10] https://developers.openai.com/api/docs/guides/structured-outputs — OpenAI Structured Outputs docs
- [11] https://openai.com/index/introducing-structured-outputs-in-the-api/ — Structured Outputs launch (100% schema eval)
- [12] https://developers.openai.com/api/docs/guides/prompt-caching — OpenAI prompt caching mechanics & multipliers
- [13] https://platform.claude.com/docs/en/build-with-claude/prompt-caching — Anthropic prompt caching mechanics
- [14] https://platform.claude.com/docs/en/about-claude/pricing — Anthropic pricing (cache write/hit rates)
- [15] https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview — Anthropic tool use / function calling
- [16] https://arxiv.org/abs/2201.11903 — Wei et al., Chain-of-Thought Prompting (2022)
- [17] https://research.google/blog/language-models-perform-reasoning-via-chain-of-thought/ — Google Research CoT blog (58% / 74% GSM8K)
- [18] https://arxiv.org/abs/2205.11916 — Kojima et al., Zero-shot CoT (2022)
- [19] https://arxiv.org/abs/2005.14165 — Brown et al., GPT-3 few-shot learners (2020)
- [20] https://arxiv.org/abs/2210.03629 — Yao et al., ReAct (2022)
- [21] https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/ — Google ReAct results tables
- [22] https://developers.openai.com/api/reference/resources/chat — OpenAI Chat roles (`developer` / `system`)
- [23] https://developers.openai.com/api/docs/guides/agent-builder-safety — Untrusted input vs developer messages
- [24] https://openai.com/index/designing-agents-to-resist-prompt-injection/ — OpenAI agent prompt-injection design
- [25] https://developers.openai.com/api/docs/guides/safety-best-practices — OpenAI safety / red-team / safety_identifier
- [26] https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html — OWASP prompt injection prevention
- [27] https://github.com/anthropics/anthropic-cookbook/blob/main/misc/prompt_caching.ipynb — Anthropic caching cookbook (latency demo)
- [28] https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/prompt-caching.md — Anthropic cache break-even notes
