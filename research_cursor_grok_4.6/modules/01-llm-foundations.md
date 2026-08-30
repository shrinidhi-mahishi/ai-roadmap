# Module 01 — LLM Foundations

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/01-llm-foundations.md` (researched 2026-08-21, 93 sources).
**Mandatory topics**: Transformers · Reasoning · Function calling · Structured output.

The unit of production is not “a Transformer.” It is a **control plane** that routes, authorizes, and checkpoints around a **data plane** that tokenizes, prefills, decodes, samples, and parses. Hosted APIs hide the data plane; self-host stacks (vLLM, SGLang, Dynamo) expose it. Interview answers that skip this split fail when the follow-up is “where does the KV cache live, and who executes the tool?”

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, policy, routing, schema compilation, the agentic loop, and durable application state. Data plane owns tokenizer → embedding → stacked transformer → sampler → detokenizer/parser. Persistence is two different stores: **application checkpoints** (messages, tool results, interrupts) versus **KV / prompt cache** (soft, prefix-addressed, not a transaction log). Tool proxies execute side effects; the model never does. Telemetry is the only place token usage is authoritative on streaming paths (`response.completed` / final `message_delta`).

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (SSE / WebSocket / sync HTTP / Batch)                                  │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                                   │
│                                                                                 │
│  ┌────────────┐   ┌─────────────┐   ┌──────────────┐   ┌─────────────────────┐  │
│  │ API Gateway│──▶│ Policy      │──▶│ Model Router │──▶│ Schema Compiler     │  │
│  │ auth, quota│   │ PII redact  │   │ tier / SLA   │   │ JSON Schema → CFG   │  │
│  │ circuit brk│   │ tool RBAC   │   │ cache key    │   │ 24h / PDA cache     │  │
│  │ Retry-After│   │ allowlist   │   │ KV-aware     │   │                     │  │
│  └────────────┘   └──────┬──────┘   └──────┬───────┘   └──────────┬──────────┘  │
│                          │                 │                      │             │
│                          │                 ▼                      │             │
│                          │          ┌──────────────┐              │             │
│                          │          │ Orchestrator │◀─────────────┘             │
│                          │          │ ReAct / graph│  stop_reason / tool_use    │
│                          │          │ max rounds N │  previous_response_id      │
│                          │          └──────┬───────┘                            │
└──────────────────────────┼─────────────────┼────────────────────────────────────┘
                           │                 │
                           │                 ▼
┌──────────────────────────┼──────────────────────────────────────────────────────┐
│ DATA PLANE               │   (provider-owned on hosted APIs; vLLM/SGLang/Dynamo)│
│                          │                                                      │
│  ┌───────────┐  ┌────────┴──────┐  ┌─────────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Tokenizer │─▶│ Prefill pool  │─▶│ KV transfer │─▶│ Decode   │─▶│ Sampler  │ │
│  │ + template│  │ compute-bound │  │ NIXL /      │  │ memory-  │  │ temp/p/k │ │
│  │           │  │ writes KV     │  │ Mooncake /  │  │ bound    │  │ grammar  │ │
│  │           │  │ TTFT KPI      │  │ LookupBuffer│  │ TPOT/ITL │  │ bitmask  │ │
│  └───────────┘  └───────────────┘  └─────────────┘  └────┬─────┘  └────┬─────┘ │
│                                                          │             │       │
│                                                          ▼             ▼       │
│                                               ┌─────────────────────────────┐  │
│                                               │ Parser                      │  │
│                                               │ text | tool_use | thinking  │  │
│                                               │ json_schema / function args │  │
│                                               └──────────────┬──────────────┘  │
└──────────────────────────────────────────────────────────────┼─────────────────┘
                                                               │
              ┌────────────────────────────────────────────────┤
              │ if stop_reason = tool_use                      │ if final / text
              ▼                                                ▼
┌─────────────────────────────────┐              ┌──────────────────────────────┐
│ TOOL PROXIES (untrusted planner │              │ Persistence                  │
│  never holds IAM)               │              │                              │
│  ┌──────────┐  ┌─────────────┐  │              │  ┌────────────────────────┐  │
│  │ Ticket   │─▶│ Sandbox     │  │              │  │ App state              │  │
│  │ STS /    │  │ bash/code/  │  │              │  │ PostgresSaver /        │  │
│  │ signed   │  │ HTTP client │  │              │  │ Responses store=true   │  │
│  │ scope    │  │ JSON-encode │──┼── tool_result│  │ thread_id ≤ 255        │  │
│  └──────────┘  │ results     │  │              │  └────────────────────────┘  │
│                └─────────────┘  │              │  ┌────────────────────────┐  │
└─────────────────────────────────┘              │  │ Soft caches            │  │
                                                 │  │ prompt cache (TTL)     │  │
                                                 │  │ PagedAttention blocks  │  │
                                                 │  │ RadixAttention tree    │  │
                                                 │  └────────────────────────┘  │
                                                 └──────────────┬───────────────┘
                                                                │
┌───────────────────────────────────────────────────────────────┴───────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Audit log   │  │ Metrics      │  │ Trace spans │  │ Usage (authoritative│  │
│  │ call_id,    │  │ TTFT p50/95  │  │ gateway →   │  │ on terminal event)  │  │
│  │ hashed args,│  │ TPOT, goodput│  │ prefill →   │  │ thinking_tokens,    │  │
│  │ policy dec. │  │ cache hit %, │  │ decode →    │  │ cached_tokens,      │  │
│  │ chain of    │  │ breaker state│  │ tool proxy  │  │ cache_write_tokens  │  │
│  │ custody     │  │ KV free blks │  │             │  │                     │  │
│  └─────────────┘  └──────────────┘  └─────────────┘  └─────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 End-to-end request flow

1. **Ingress.** Client opens SSE (interactive) or sync HTTP (extract) or Batch (offline). Gateway stamps a correlation id, authenticates, and checks RPM/TPM. A closed circuit breaker on the primary provider is already a routing input.
2. **Policy.** Control plane redacts PII **before tokenize**. Secrets must not sit above a prompt-cache breakpoint: caches are content-addressed; an SSN in the static prefix lives for the TTL. Tool RBAC attaches only the tools this turn is authorized to call (`send_email` stays off the schema unless the user asked to send mail).
3. **Route.** Router picks model tier (Luna / Flash-Lite / Haiku for extract; Sol / Opus / 3.1 Pro for hard reasoning), SLA class (Gemini Priority vs Flex vs Batch; OpenAI `service_tier`), and a **cache key** (`tenant + prompt_version`). Self-host: Dynamo-style KV-overlap score sticky-routes to a prefix-hot prefill worker (~2× TTFT claimed when overlap is high).
4. **Compile.** JSON Schema / regex / EBNF compiles to a CFG then a PDA/FSM. Anthropic caches schema compilation ~24 h (first request pays compile latency). vLLM’s Structured Output Manager pins compiled XGrammar objects for a **small catalog**; unique-per-request schemas neutralize that cache and inflate TTFT (llguidance is the documented alternative).
5. **Prefill (data plane).** Chat template + tokenize. Prefill is compute-bound: all prompt tokens in parallel, full KV write, KPI = TTFT. DistServe / Dynamo / vLLM disagg assign this to a high-FLOP pool. Chunked prefill (SARATHI) is the colocated alternative when GPU count is too small for two pools.
6. **KV handoff.** LookupBuffer `insert`/`drop_select`, NIXL GPU→GPU, or Mooncake hierarchical store. MORI-IO **read mode**: decode pulls after prefill publishes `remote_block_ids`. **Write mode**: prefill pushes layer-by-layer so decode can start as soon as prefill finishes. Decode may reuse token IDs and skip re-tokenization.
7. **Decode + constrain.** Memory-bound, one token per step, KPI = TPOT / ITL. Continuous batching (Orca) admits/evicts sequences every forward. **After logits, before sample**, a grammar bitmask sets illegal tokens to −∞. Reasoning models: constraints stay **off** until `think_end_id` (SGLang `ReasonerGrammarBackend`); optional `max_think_tokens` then forces the end token by masking everything else.
8. **Sample and parse.** Temperature / top-p / top-k / penalties. Parser emits typed SSE (`response.output_text.delta`, `response.function_call_arguments.delta`, `response.reasoning_summary_text.delta`) or Anthropic content blocks (`text` | `tool_use` | `thinking` | `server_tool_use`) or Gemini `functionCall` with a mandatory `id`.
9. **Tool proxy (only if `stop_reason=tool_use`).** Host validates schema, checks the signed ticket, executes in a sandbox, **JSON-encodes** third-party strings, optionally screens with a cheap classifier (`injection_suspected: boolean`), appends `tool_result`, and re-enters decode. Anthropic: the model never executes tools. Gemini: every `functionResponse` must echo the `id`.
10. **Persist and emit.** Orchestrator snapshots application state (LangGraph superstep / Responses `store=true`). KV remains a soft cache. Usage, hashed args, and the policy decision land in the audit sink. Terminal SSE frame is the only authoritative token bill on streaming.

**Interview talking point:** “The model is an untrusted planner. IAM, egress, and tool execution live on the tool host. Constrained decoding guarantees shape, not benign semantics.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Transformers

The 2017 encoder–decoder Transformer is still the data-plane primitive: scaled dot-product attention

\[
\mathrm{Attention}(Q,K,V)=\mathrm{softmax}\!\left(\frac{QK^{\top}}{\sqrt{d_k}}\right)V
\]

plus a position-wise FFN, residuals, and layer norm. Decoder-only causal masking (GPT lineage) is the serving default; encoder–decoder remains in T5-class and some multimodal stacks.

#### Attention variants (KV-cache economics)

| Variant | KV layout | Why it exists | Production footprint |
| --- | --- | --- | --- |
| MHA | 1 KV head per Q head | Original quality | KV bytes explode with heads × sequence |
| MQA | 1 shared KV head for all Q | Decode bandwidth (Shazeer 2019) | Falcon / PaLM-style decode |
| GQA | KV heads = groups of Q heads | Interpolation: MHA quality, near-MQA speed (Ainslie et al., EMNLP 2023) | Llama 3, Qwen, Gemma |
| MLA | Compress K/V into latent \(c_{KV}\) (DeepSeek-V3 \(d_c=576\)) plus a small RoPE-decoupled key | Inference-efficiency counterpart to DeepSeekMoE | DeepSeek-V2/V3/R1, Kimi K2, GLM-5 |

**FlashAttention / FA-2 are kernels, not architectures.** FA tiles softmax in SRAM so HBM traffic is linear in sequence rather than materializing the \(n^2\) score matrix (2–4× vs optimized baselines). FA-2 reaches 50–73% of A100 peak FLOPs/s and up to 225 TFLOPs/s (72% MFU) on GPT training. Serving engines dispatch FA / FlashInfer per batch shape.

**Complexity (single layer, sequence \(n\), head dim \(d\), \(h\) Q heads, \(h_{kv}\) KV heads):**

- Attention FLOPs (prefill): \(\Theta(n^2 d h)\) — quadratic in prompt length; this is why TTFT is a prefill problem.
- Decode step \(t\): \(\Theta(t\, d\, h)\) for scores against cached K, plus a full weight read. Bound is **memory bandwidth**, not FLOPs.
- KV bytes (MHA-ish): \(2 \times \mathrm{layers} \times n \times h_{kv} \times d \times \mathrm{bytes}\). GQA shrinks \(h_{kv}\). MLA shrinks the cached tensor to latent width \(d_c\).
- Softmax numerically: scale by \(\sqrt{d_k}\) keeps logits \(\mathcal{O}(1)\) so the distribution does not collapse as \(d_k\) grows (Vaswani invariant).

#### Mixture-of-Experts

Sparse MoE replaces the dense FFN with a router + \(N\) expert FFNs; \(k\) experts fire per token.

- Switch Transformers: top-1 routing to reach trillion-parameter scale.
- Mixtral 8×7B: 8 experts/layer, top-2; **47B total / 13B active**; 32k dense context.
- DeepSeek-V3: **671B total / 37B activated**; auxiliary-loss-free load balancing (expert-specific bias, update speed 0.001 in most training); MTP training objective also usable as speculative decoding; 14.8T tokens in 2.788M H800 GPU-hours.

Serving implication: decode is **all-to-all expert traffic plus KV**. Dynamo exposes speculative-MoE A2A backends; vLLM MORI-IO disagg examples run TP=4 **with expert parallelism** on both prefill and decode pools. Dense vs MoE is an NFR choice: simpler TP and uniform latency vs 13B–37B-active quality at lower $/token and expert-network failure modes.

**Load-balance invariant (aux-loss-free):** routing scores plus a slow expert bias keep expert utilization from collapsing onto a few hot experts without a separate auxiliary loss term. Convergence is empirical (DeepSeek reports the bias schedule); there is no published closed-form mixing-time bound in the research file.

#### Prefill vs decode

| Phase | Compute | KPI | Batching |
| --- | --- | --- | --- |
| Prefill | Compute-bound; prompt tokens in parallel; writes KV | TTFT | Large batches, high-FLOP GPUs |
| Decode | Memory-bound; 1 token/step; reads growing KV + weights | TPOT / ITL | Small batches, high-bandwidth GPUs, continuous batching |

DistServe (OSDI’24) assigns phases to different GPUs, eliminating prefill–decode interference: **7.4× more requests or 12.6× tighter SLO** vs colocated SOTA while meeting latency for **>90%** of requests. vLLM colocated serving **inflates tail ITL** when prefills insert into a decode batch; disagg is the documented mitigation. Continuous batching + PagedAttention (OS virtual-memory analog for KV blocks, common block size 16 tokens): **2–4× throughput** vs FasterTransformer/Orca at matched latency; near-zero internal KV waste. PagedAttention does **not** solve capacity exhaustion: when pages run out, the scheduler preempts/swaps to CPU and recomputes later (latency cliff). Operator back-pressure must originate from **decode free blocks** (example threshold 10%), not only the prefill queue.

SGLang **RadixAttention** stores KV in a radix tree so shared prefixes (system prompt, tool schemas) reuse across requests. Sticky routing is required for high prefix hit rate; one operator report: ~98% on highly sticky creator traffic, ~8% extra misses when spilling at 85% util.

#### Positional encodings

- **Absolute learned**: length-locked to the training window (GPT-2).
- **RoPE** (Su et al. 2021): rotate Q/K in 2D subspaces by position-dependent angles; relative distance lives in the inner product. Dominant in Llama, Mistral, Gemma, Qwen, DeepSeek, gpt-oss. HF `rope_type`: `default | linear | dynamic | yarn | longrope | llama3`.
- **ALiBi**: head-specific linear distance bias on \(QK^{\top}\); better native extrapolation, lost ecosystem share after Llama standardized on RoPE.
- **YaRN**: piecewise NTK-by-parts + attention temperature; SOTA extension after fine-tune on **<~0.1%** of original pretrain; Dynamic-YaRN claimed **>2×** without fine-tune.
- **Hybrids (2025)**: Gemma 3 uses θ=10k on local sliding-window layers and θ=1M on global layers.

**Lost in the Middle** (Liu et al., TACL 2024): U-shaped retrieval — beginning and end beat the middle, including on GPT-3.5-16k, Claude-100k, MPT-30B-Instruct (ALiBi), LongChat (RoPE). RoPE models **without** YaRN/NTK/LongRoPE degrade when serve length ≫ train length. Put the needle at the edges or retrieve; do not assume “long context” equals uniform attention.

### 2.2 Reasoning

**Chain-of-Thought (Wei et al., 2022)** is a prompting pattern: force intermediate tokens that decompose the task. On **reasoning models** (o1-class, Claude adaptive thinking, Gemini thinking), raw CoT is typically **hidden**; the product shows a summary. Prompting “think step by step” on those models is **unnecessary** and can inflate visible tokens (OpenAI reasoning best practices).

**Thinking tokens are output-billed even when hidden.** OpenAI documents this; Gemini pricing states output includes thinking; Anthropic reports `usage.output_tokens_details.thinking_tokens`. That single fact dominates token economics for Sol/Opus/high-effort Gemini (see §3).

**Test-time compute.** o1-class quality scales with train-time RL **and** with decode-time thinking. Effort knobs (`reasoning.effort`, Anthropic `output_config.effort` / `thinking: {type:"adaptive"}`, Gemini thinking level) are continuous cost/latency controls, not quality toggles you leave at max. Anthropic: `budget_tokens` is **rejected (400)** on Claude 4.7+/Sonnet 5; adaptive thinking is **on by default** on Sonnet 5; non-default sampling params also 400.

Artificial Analysis medians (accessed 2026-08-21; **median**, not vendor p95/p99):

| Setting | Median first chunk | Median tok/s | Implication |
| --- | --- | --- | --- |
| Gemini 2.5 Flash-Lite (non-reasoning) | 0.31 s | — | Extract/classify SLA |
| Gemini 3.7 Flash (low / medium / high) | 0.93 / 4.55 / 12.10–15.42 s | ~333–390 | Effort is a TTFT multiplier |
| Claude Opus 5 (medium / high / xhigh / max) | 7.20 / 20.73 / 29.10 / 60.12 s | ~59–60 | tok/s flat; thinking length drives TTFT |
| GPT-5.6 Sol (xhigh / max) | 39.54–43.11 s / **120.71–209.11 s** | 71–130 | Client HTTP timeout of 60 s is a product outage at `max` |

**Process vs outcome supervision.** PRM800K process supervision solved **78%** of a MATH subset vs weaker outcome RMs; the best-of-N gap **widens** with N (Lightman et al.). Counter-result (Van Hoyweghen et al. 2025, Omni-MATH): o3-mini (m) beats o1-mini **without longer** chains; accuracy can **fall** as chains grow; o3-mini (h) spends extra tokens on already-solved items. Interview answer: test-time compute is not monotonically useful; cap thinking, measure task accuracy vs tokens, do not buy `max` by default.

**Reasoning ∩ structured output.** Grammar must not constrain thinking tokens. SGLang’s reasoner grammar is the open-source state machine: unconstrained until `think_end_id`, then JSON/CFG on. OpenAI Responses: `store=true` + `previous_response_id` keeps **reasoning items adjacent to function calls** in context; Chat Completions is stateless and **re-reasons after every tool call** (more tokens, worse tool quality).

### 2.3 Function calling

**Native (API-constrained):** tools are first-class request fields; sampler + parser emit typed calls. **Prompted (legacy):** “return JSON with keys…” — no logit mask; validity is best-effort. JSON mode (OpenAI `json_object`) guarantees parseable JSON **not** schema adherence, and requires the word “json” in the input.

**OpenAI.** `tools[]` + JSON Schema `parameters`; `strict: true` uses Structured Outputs (all properties required, `additionalProperties: false`). `tool_choice`: `"auto" | "required" | "none" | {function name} | allowed-tools subset`. `parallel_tool_calls: false` forces 0 or 1 call. Responses API tries to **normalize** schemas into strict mode; Chat Completions defaults non-strict. Fine-tuned models: **strict disabled** if multiple functions fire in one turn. GPT-5+ can mix custom functions with built-ins with restrictions (built-ins not in the same parallel batch). `tool_search` (deferred tools) requires `gpt-5.4`+.

**Anthropic.** Client tools: `tool_use` / `tool_result` round-trip. Server tools (`web_search`, `web_fetch`, `code_execution`, `tool_search`) run inside Anthropic until `pause_turn`. `strict: true` = grammar-constrained sampling; **incompatible** with programmatic tool calling, citations, message prefilling. Programmatic calling exposes tools as async Python in a sandbox (`asyncio.gather`); cannot combine with `strict: true` or `disable_parallel_tool_use: true`.

**Gemini.** `functionDeclarations` + `functionCall`/`functionResponse` with mandatory `id`. Gemini 3 tool-combination mode constrains to function-call **or** NL and `allowed_function_names`; reduces `Malformed_Function_Call`. Gemini 3 can combine Structured Outputs with built-in tools and function calling. Python SDK auto-execute is a prototype; production still needs an explicit loop for observability.

**Orchestration topologies.**

| Topology | Mechanics | When |
| --- | --- | --- |
| ReAct loop | Thought + tool call; host executes; append; repeat until final | Default copilot |
| Supervisor–worker | Supervisor routes via tool-shaped handoffs | Specialist fleets (langgraph-supervisor, Agents SDK `handoffs` vs `agent.asTool()`, ADK `sub_agents`, CrewAI hierarchical + `manager_llm`) |
| Plan-and-execute | Planner emits DAG; executors run without re-planning every token | Deterministic CI-like steps (ADK Sequential/Loop/Parallel) |
| Graph runtime | Explicit edges, reducers, interrupts | LangGraph `StateGraph`; ADK 2.0 `BaseAgent` as `BaseNode` |

There is **no native max-tool-rounds** in base Chat Completions — the application while-loop must cap \(N\) (ASI02 recursive tool use). Anthropic server tools have an internal cap then `pause_turn`. Bedrock Converse can emit **all** independent `toolUse` blocks in turn 1. Disable parallel tools when fan-out is a write, a rate-limit, or an injection amplifier.

**Hallucinated parameters.** Non-strict calling is best-effort. Prefer `strict: true` (illegal schemas → 400, not silent drift). Gemini constrained tool-combination reduces malformed calls vs `AUTO`. Prompted JSON remains the highest hallucination rate.

### 2.4 Structured / constrained decoding

| Mechanism | Guarantee | Failure shape |
| --- | --- | --- |
| Prompted JSON | None | Truncation, extra keys, markdown fences |
| JSON mode | Valid JSON syntax | Schema drift |
| Constrained decoding (CFG → PDA/FSM → vocab bitmask) | Every sampled token is schema-legal **if generation completes** | Refusals, `max_tokens` truncation, unsupported schema features, distribution shift onto “safe” enums |
| Provider `strict` / `json_schema` | Same, on the supported subset | 400 on illegal schema; OpenAI `refusal` / Anthropic `stop_reason: refusal` |

Pipeline: compile schema → PDA/FSM → at each decode step mask illegal logits to −∞ → sample. XGrammar: CFG → PDA; near-zero JSON overhead; up to **3.5×** vs Outlines on JSON schema mask generation and **>10×** on CFG in MLC benches; vLLM reports up to **5× better TPOT** vs prior Outlines path under load. Outlines (Willard & Louf 2023) FSM over logits; historically higher compile cost on the batch critical path. Overlapped constrained decoding (SGLang Spec V2) runs CPU grammar updates concurrent with GPU forward.

OpenAI Structured Outputs: schema → **context-free grammar**; tokens allowed only if the partial string stays schema-legal. Use **functions** when bridging to app actions; `text.format` / `json_schema` when structuring the user-facing reply. Anthropic: `output_config.format` + `strict` tools; `client.messages.parse()` validates client-side. Gemini: `response_format` + `mime_type=application/json`; keys emitted in schema order.

**Failure modes even with a mask:** `refusal`; incomplete JSON at `max_tokens`; unsupported JSON Schema (recursion, dynamic keys) → 400 or silent constraint stripping; **do not execute tools on streamed argument deltas**; unique schemas miss XGrammar’s compile cache. **Semantic bypass:** `{"sql":"DROP TABLE users"}` is schema-valid. Pair structured output with a classifier schema (`injection_suspected: boolean`) and host-side authorization. Constrained decode can also **collapse onto a default enum** when the preferred token is masked — valid, wrong.

### 2.5 State machines

**Reasoner + grammar (decode):**

```
                    think_end_id                     EOS / stop
  ┌─────────┐  ─────────────────▶  ┌─────────────┐  ──────────▶  ┌──────────┐
  │ THINKING│  (mask off / CoT)    │ CONSTRAINED │  sample JSON  │  DONE    │
  └────┬────┘                      └──────┬──────┘               └──────────┘
       │ max_think_tokens                 │ illegal token
       │ force think_end_id               │ logit = −∞
       ▼                                  ▼
  (same THINKING→CONSTRAINED)        stay CONSTRAINED
```

**Agent tool loop (control plane):**

```
  ACCEPT ──▶ PREFILL ──▶ DECODE ──┬── FINAL ──▶ PERSIST ──▶ RESPOND
                                  │
                                  └── TOOL_USE ──▶ VALIDATE ──▶ AUTHORIZE
                                         │              │            │
                                         │              │ schema fail│ deny
                                         │              ▼            ▼
                                         │           REJECT       HITL / 403
                                         ▼
                                      EXECUTE (idempotency key)
                                         │
                                         ├── success ──▶ APPEND tool_result ──▶ DECODE
                                         ├── transient ──▶ RETRY (bounded)
                                         └── poison / N≥Nmax ──▶ DEAD-LETTER
```

**Circuit breaker (control plane, per downstream):**

```
           failure_rate ≥ threshold                probe success
  ┌────────┐  ─────────────────────▶  ┌──────┐  ───────────────▶  ┌────────┐
  │ CLOSED │                          │ OPEN │                    │CLOSED  │
  └───┬────┘                          └──┬───┘                    └────────┘
      │                                  │ timer elapsed
      │ success resets count             ▼
      │                             ┌──────────┐
      └─────────────────────────────│ HALF_OPEN│── probe fail ──▶ OPEN
                                    └──────────┘
```

### 2.6 Invariants worth stating in an interview

1. **Causal mask:** position \(i\) attends only to \(\leq i\). Breaking it is a train/serve bug, not a feature.
2. **KV is not a ledger.** Prefix reuse; eviction is allowed; never treat prompt cache as RPO=0.
3. **Goodput ≠ tok/s.** DistServe: throughput **conditional on TTFT+TPOT SLOs**. Over-admission makes dashboards green and users red.
4. **Shape ≠ safety.** Grammar ⊆ syntax. Authorization ⊆ tool host.
5. **Thinking ⊂ output tokens** for billing and for TPOT.
6. **Idempotency lives in the activity, not the LLM.** ReAct replay will re-emit a tool call; the proxy must no-op on the same key.

---

## 3. Token Economics & NFR Analysis

Prices below are **per 1M tokens, 2026-08-21**, from the research file. Formula:

\[
C = n \cdot \frac{T_{\mathrm{in,miss}} P_{\mathrm{miss}} + T_{\mathrm{in,hit}} P_{\mathrm{hit}} + T_{\mathrm{write}} P_{\mathrm{write}} + T_{\mathrm{out}} P_{\mathrm{out}}}{10^{6}}
\]

\(n\) = executions. Output \(T_{\mathrm{out}}\) **includes thinking tokens** on OpenAI reasoning models, Gemini, and Anthropic usage breakdowns.

**Assumptions (all worked examples):** USD list price, no regional +10%, no web-search tool fees, no Priority/Fast multiplier unless stated, tokenizer = vendor tokenizer (Claude 4.7+ is ≈ **+30% tokens** vs prior tokenizer for the same text — bill shock on migrated prompts). Cached tokens **still count toward TPM**.

### 3.1 Cost per 1k runs

**Workload A — deterministic extract.** 4k input / 400 output, no reasoning, 0% cache.

| Stack | \(P_{in}/P_{out}\) | \(C_{1k}\) |
| --- | --- | --- |
| GPT-5.6 Luna | $0.20 / $1.20 | **$1.28** |
| DeepSeek V4 Flash off-peak miss | $0.22 / $0.66 | **$1.14** |
| Gemini 3.5 Flash-Lite | $0.30 / $2.50 | **$2.20** |
| Claude Haiku 4.5 | $1 / $5 | **$6.00** |
| Claude Sonnet 5 | $2 / $10 | **$12.00** |
| GPT-5.6 Terra | $2 / $12 | **$12.80** |

Luna: \(1000 \times (4000 \times 0.20 + 400 \times 1.20) / 10^6 = 1.28\).

**Workload B — agent turn with prompt cache.** 20k system+tools (90% hit after first write) + 1k new user + 800 out.

GPT-5.6 Terra (write = 1.25× input = $2.50/M, hit = $0.20/M):

- First: \(20\mathrm{k} \times 2.50 + 1\mathrm{k} \times 2 + 800 \times 12\) per million = **$0.0616 / turn**
- Steady: \(20\mathrm{k} \times 0.20 + 1\mathrm{k} \times 2 + 800 \times 12\) = **$0.0156 / turn** → **$15.60 / 1k steady-state turns**

Sonnet 5, 5-minute cache (write 1.25×, read 0.1×): first **$0.060**, hit **$0.014** → **$14.00 / 1k** steady. Breakeven after **one** 5-minute hit: \(1.25 + 0.1 < 2.0\).

**Workload C — reasoning blowup.** Same 4k in, **8k thinking + 400 answer** billed as output:

| Stack | \(C_{1k}\) | vs 400-out-only |
| --- | --- | --- |
| GPT-5.6 Sol | **$272.00** | $17.00 (**16×**) |
| Claude Opus 5 | **$230.00** | |
| Gemini 3.6 Flash | **$69.00** | |

Sol: \(1000 \times (4000 \times 5 + 8400 \times 30) / 10^6 = 272\).

**Cache economics (control-plane design):**

| Provider | Match | Min prefix | Write | Read | TTL |
| --- | --- | --- | --- | --- | --- |
| OpenAI GPT-5.6+ | Exact prefix at breakpoints | 1024 | 1.25× | 0.1× | 30 min |
| Anthropic | Exact block prefix | 1024 Sonnet; 4096 Opus/Haiku 4.5 | 1.25× (5m) / 2× (1h) | 0.1× | 5m refresh-on-hit or 1h |
| Gemini implicit | Prefix, **no savings guarantee** | 1024 Flash / 4096 Pro (docs vary) | none | ~0.1× on hit | opportunistic |
| Gemini explicit | Named cache | same | **storage rent** | 0.1× | $1/MTok/h typical; **$4.50/MTok/h** on 3.1 Pro Preview |
| DeepSeek | Full cache-prefix **unit** (not substring) | n/a | none | hit ≈ **3.2%** of miss (Flash off-peak $0.007 / $0.22) | hours–days |

Timestamps/IDs in the static prefix **bust** Anthropic cache. Semantic cache (embed query, reuse answer) is **not** a first-party LLM-API feature; no vendor hit-rate SLA. **[inferred]** 20–40% on FAQ chat; ~0% on unique agent tool traces. Invalidation must track tool/DB mutations.

**Gemini SLA classes (same model, different NFR):** Priority = “seconds”, non-sheddable, **+75–100%** token price. Flex = 1–15 min, sheddable, **50%** off. Batch ≤24 h, **50%** off. Grounding with Google Search: 5k prompts/month free across Gemini 3, then **$14 / 1,000** queries.

**Long-context cliffs:** OpenAI GPT-5.6 **2×** input/output above 270k context. Gemini 3.1 Pro: in $2→$4 and out $12→$18 above **200k**. Regional/data-residency: OpenAI +10% (eligible models on/after 2026-03-05); Anthropic `inference_geo: "us"` **1.1×**; Bedrock/Vertex regional +10% for 4.5+.

### 3.2 Latency SLA targets and mitigations

Vendors publish **medians** (AA) or qualitative tiers, not contractual p99 for hosted chat. Use this working SLA for **interactive extract** (non-reasoning, 4k/400) and a separate SLA for **reasoning**:

| Percentile | Interactive extract target | Reasoning (medium effort) | Reasoning (max) |
| --- | --- | --- | --- |
| p50 TTFT | ≤ 0.4–0.9 s (Flash-Lite / low Flash / Luna-class) | 4–8 s (Gemini medium / Opus medium) | 40–60 s+ |
| p95 TTFT | **[inferred]** 1.5–3× median on mixed traffic (prefill queue + thinking preamble) | same multiplier | often > HTTP 60 s — **raise client timeout >180 s** or do not offer `max` on sync HTTP |
| p99 TTFT / ITL | DistServe design goal: **>90% SLO attainment**, not a published p99 | disagg to protect decode ITL | product should be async/job-style |

**[inferred]** Hosted p95 TTFT is typically 1.5–3× median because prefill queueing and reasoning-token preambles dominate tails.

| Tier | Mitigations |
| --- | --- |
| p50 | Prefix cache + sticky routing; small non-reasoning model; streaming so perceived TTFT = first chunk not full JSON |
| p95 | Continuous batching; chunked prefill if colocated; KV-aware router (~2× TTFT claim); avoid inserting long prefills into decode batches; schema catalog so XGrammar PDA hits |
| p99 | **Disaggregated P/D** (DistServe 12.6× tighter SLO); back-pressure when decode free blocks <10%; shed Flex/batch rather than interactive; circuit-break to a faster fallback model; never block the UX thread on `effort=max` |

Self-host anecdote (operator-reported, not a vendor SLA): ~12 ms extra TTFT for an 1,800-token cold prefill vs ~400 ms queue; ~5 ms wait when decode free blocks <10%. Queueing, not kernels, is the p95 story.

### 3.3 Throughput and back-pressure

Hosted: org+project RPM/TPM, not per-user. Example **gpt-5** table: T1 500 RPM / 500k TPM … T5 **15k RPM / 40M TPM**; Batch queue T5 15B tokens. OpenAI spend tiers cap monthly $ (T5 $1,000 paid → $200k/mo). Gemini paid caps T1 $250 … T3 $20k–$100k+. DeepSeek hard concurrency **2500 Flash / 500 Pro**. Cached tokens still burn TPM — caching is a cost lever, not a rate-limit lever.

Self-host: \(\mathrm{throughput} \approx \min(\mathrm{compute}, \mathrm{KV\_pages\_free}, \mathrm{max\_num\_seqs})\). Size decode HBM:

\[
\mathrm{KV\_bytes} \approx B \times n \times L \times h_{kv} \times d \times 2_{(K,V)} \times 2_{\mathrm{fp16}}
\]

MLA \(d_c=576\) cuts this vs MHA. DistServe chooses xPyD pool ratio from TTFT/TPOT SLOs.

**Worked capacity (research §6.4).** Target **50 interactive extracts/s**, 4k/400, Luna-class:

- Cost: $1.28 / 1k → **$0.064 / s** ≈ **$5,530 / month** at 50 rps continuous.
- TPM: \(50 \times 4400 = 220\mathrm{k}\) tok/s = **13.2M TPM** → OpenAI **Tier 5**-class (40M on the gpt-5 table) plus retry headroom.
- Same 50 rps on **Sol max** is not an interactive product: AA ~130–200 s first-chunk ⇒ in-flight ≈ \(50 \times 150\) = **7,500** HTTP calls; $272/1k ⇒ **$13.6 / s** ≈ **$35k/day**.

**Back-pressure design:**

1. Gateway admits only if breaker = closed/half-open **and** token bucket has room **and** (self-host) decode free-block % > threshold.
2. 429 + `Retry-After` / `x-ratelimit-*` → wait, do not hammer.
3. Over-admission destroys DistServe goodput; shed Flex/Batch first.
4. Agent fleets: each user turn is **N model calls**. Budget \(N \times (\mathrm{TTFT} + T_{\mathrm{out}}/\mathrm{TPOT})\). Cap N (e.g. 8) at the orchestrator. Parallel tools cut N for independent reads but multiply downstream QPS.

### 3.4 Non-functional requirements and explicit trade-offs

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | 99.9% gateway (control plane); model provider is a **dependency**, not your SLO unless multi-vendor fallback | Multi-vendor raises cost and output-distribution drift |
| RPO | App state: **0** for irreversible tools (checkpoint before execute). Prompt/KV cache: **minutes–hours**, best-effort | Treating KV as RPO=0 over-provisions GPU RAM |
| RTO | Interactive: fail over < 1 s to secondary model. Reasoning jobs: resume from checkpointer, do not re-run tools | Fast failover vs identical answers |
| Consistency | Tool side effects: **exactly-once via idempotency keys**. Model text: at-least-once retry may change tokens | Cannot have bit-identical retry on temperature>0 |
| Compliance | Regional endpoints (+10%); Bedrock Guardrails; PII redaction pre-tokenize; immutable audit of `call_id` / hashed args | Residency vs latency vs price |
| Cost vs latency | Luna $1.28/1k vs Sol $272/1k on workload C; Priority +75–100%; Flex 50% off but sheddable | Paying for `max` thinking to shave a rare error |
| Consistency vs availability | Sticky cache routing (higher hit, weaker spread) vs random worker (available, cold prefill) | Dynamo overlap vs multi-AZ |

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution

**Application state ≠ KV cache.** Messages, tool results, and interrupts must survive process death. KV is a performance cache.

**Documented pattern (LangGraph).** Compile with a checkpointer; snapshot every **superstep**. `thread_id` selects the timeline (`PostgresSaver` column limit **255**). `InMemorySaver` dies on exit. Stores hold cross-thread memory. Unbounded history grows latency — prune. HITL = `interrupt()` + resume on the same thread. Time-travel = `get_state_history`. OpenAI Responses: `store=true` + `previous_response_id` so reasoning items next to function calls survive; Chat Completions re-reasons every tool hop.

**Temporal (equivalent workflow runtime).** Wrap the agent loop as a workflow; each model call and each tool execution is an **activity**.

- **Replay:** Temporal reconstructs workflow state by replaying event history. Activities must be **idempotent**; the LLM activity returns a structured `ModelTurn` (already-sampled tokens + tool calls), never “call the model again” inside a replay-unsafe closure. Non-determinism (temperature, clock) belongs **inside** the activity, recorded in the history.
- **Distributed locking:** use a workflow-id = `tenant:thread_id` so two gateways cannot run the same conversation. Tool activities take a lock keyed by `idempotency_key`.
- **Checkpointing:** each completed activity is the checkpoint. Prefer this over ad-hoc Redis “save messages” — replay is the source of truth.
- **Dead-letter:** activity failure types (see §4.2) after `max_attempts` go to a DLQ workflow; do not infinite-retry irreversible tools.

> ⚠️ Gap: the research file has no Temporal-specific LLM runbooks, worker-versioning schemes, or measured replay costs for multi-MB message histories. Treat Temporal here as the enterprise mapping of LangGraph’s superstep snapshots.

**Kafka (equivalent log).** Topic per tenant-shard: `agent.turns`, `agent.tool_results`, `agent.dlq`.

- Produce the **intent** (`tool_call` + idempotency key) **before** executing the side effect (outbox).
- Tool workers consume, execute, produce `tool_result`. Orchestrator appends and continues decode.
- Compaction on `thread_id` keeps a snapshot; the full log is the chain-of-custody.
- Poison messages (unparseable payloads, repeated handler crashes) → DLQ after N; do not block the partition.

> ⚠️ Gap: no provider publishes Kafka lag SLOs for tool-result buses or exactly-once recipes specific to ReAct.

**KV durability (soft).** vLLM LookupBuffer; Dynamo NIXL + xPyD pools that resize at runtime; Mooncake device/host/remote hierarchy; Dynamo agentic inference: lead agent writes tool-def/system-prompt blocks, subagents RDMA-read instead of re-prefill. Retention/pin APIs are **per-worker**; cross-worker pin was in-progress in the cited digest. Provider prompt caches are **not** customer-exportable (OpenAI GPT-5.6 TTL 30 min exact; Anthropic 5 min refresh-on-hit or 1 h at 2× write).

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | HTTP 429, 503, TLS reset, KV preempt/swap, Flex shed | Exponential backoff + jitter; honor `Retry-After`; retry **idempotent** model reads; do not retry unknown tool side effects |
| Permanent | HTTP 400 illegal schema, `refusal`, unsupported JSON Schema, Anthropic 400 on `budget_tokens` / non-default sampling on Sonnet 5 | Fail the turn; fix schema or route to a model that accepts it |
| Poison pill | Same request crashes the parser/worker every time; recursive tool storm; truncated JSON executed as a tool | Detect via identical payload hash + N crashes or `N ≥ Nmax` tool rounds; DLQ; never auto-replay |
| Semantic | Schema-valid but unauthorized (`DROP TABLE`); indirect injection in `tool_result` | Authorization + injection classifier; not a retry |

**Idempotency keys.** `key = hash(tenant, thread_id, tool_name, canonical_json(args), turn_index)`. Tool proxy stores key → result. Replay after workflow recovery returns the stored result. Model retries with temperature>0 are **not** idempotent — cache the `ModelTurn` once sampled.

**Infinite tool loops (ASI02).** Cap rounds in the orchestrator. `parallel_tool_calls=false` / `disable_parallel_tool_use` when writes or injection amplification matter (incompatible with Anthropic programmatic calling).

### 4.3 Circuit breaker and fallback chain

Per downstream (OpenAI, Anthropic, vLLM decode pool):

- **Closed:** traffic flows; consecutive failures or error-rate window trips to open.
- **Open:** fail fast; start a timer (e.g. 30 s). Interactive traffic routes to fallback; Flex/Batch can wait.
- **Half-open:** allow a probe (one request or a small percentage). Success → closed; fail → open.

Gemini Flex is a **provider-side** load-shed circuit (sheddable under standard spikes). DeepSeek concurrency caps are a hard open. vLLM KV collapse is a scheduler-internal preempt; the **gateway** must still stop sending prefills.

**Fallback chain:** primary (Terra / Sonnet / Flash) → secondary (other vendor or Haiku / Luna / Flash-Lite) → **deterministic fallback** (schema-only extract, regex, or “degraded: cannot complete this turn”). Deterministic fallback must still emit **valid structured output** so downstream parsers do not crash. Do not fall back from `strict` JSON to free-form text on a parser path.

> ⚠️ Gap: no major provider publishes breaker trip curves, KV-OOM HBM watermarks, or p99 prefill-queue delay distributions.

### 4.4 Enterprise security

**Zero-Trust around the model (including MCP).** Treat the model as an untrusted planner. Tokens, IAM, and egress live on the **tool host**. OWASP GenAI LLM Top 10 **2026** (2026-08-04): **LLM01 Prompt Injection** remains #1; **Excessive Agency** is **LLM03** (largest rank jump); Hidden Context Exposure is LLM08. ASI companion: ASI01 Goal Hijack, ASI02 Tool Misuse, ASI03 Identity Abuse.

MCP (Model Context Protocol) servers are **tool proxies**. Zero-Trust MCP:

1. Each MCP server gets a **short-lived audience-bound token**, not a long-lived superuser key in an env var the model can be tricked into printing.
2. Network: private endpoints (Azure OpenAI / Bedrock VPC / Vertex PSC); MCP tools must not reach cloud metadata servers.
3. Per-request **signed tool tickets** (scope, tenant, expiry, tool name). The MCP server verifies the ticket **before** executing; the LLM never sees the raw secret.
4. Allowlists are not enough: Claudy Day research showed exfil via **allowlisted** `api.anthropic.com` Files API. Egress policy must be **method + resource**, not hostname.

**Tool-level RBAC (least privilege per turn).** Do not attach `send_email` unless the user asked to send mail. OpenAI `tool_choice` allowed-tools subset and Agents SDK `isEnabled` hide handoffs at runtime. Bedrock `toolChoice` can force a named tool. HITL for irreversible tools: LangGraph `interrupt()`; Anthropic computer-use classifiers ask for confirmation on screenshot injections.

**PII pipeline:** detect → redact **before tokenize** → audit the redaction map (token-for-token placeholders), never log raw PII. Cached prefixes must not contain secrets.

**Prompt injection via tool results.** Dominant agent threat (Greshake et al.): malicious text in pages, mail, RAG, **`tool_result`**. Anthropic mitigations to implement, not just cite:

1. Untrusted content **only** in `tool_result`, never system/user.
2. Label source in the result structure.
3. System policy: tool content is data, not commands.
4. **JSON-encode** third-party strings (delimiter breakout).
5. Do **not** put developer instructions inside tool results.
6. Least privilege + sandbox.
7. Screen with Haiku 4.5 + structured boolean `injection_suspected` before append.

Promptfoo (third-party): GPT-5.2 jailbreak success **4.3% → 78.5%** multi-turn vs single-turn. Multi-turn is the threat model.

**Auditability / chain of custody.** Persist `call_id` / `toolUseId` / Gemini `id`, hashed args, policy decision, latency, token breakdown (`thinking_tokens`, `cached_tokens`, `cache_write_tokens`). Streaming: usage only on `response.completed`. Kafka log or WORM object store; workflow event history is a second copy. An agent decision is reconstructable as: policy snapshot + model id + sampled turn + tool results + human interrupt.

---

## 5. Production Enterprise Code

Cohesive stdlib-only module: retries with full jitter, circuit breaker (closed → open → half-open), primary → secondary → deterministic fallback, correlation-id JSON logs, PII redaction, JSON-Schema subset validation for function calls, structured-output parse, reasoning/grammar phase gate, tool-round cap, idempotent tool proxy. Run: `python llm_gateway.py`.

```python
#!/usr/bin/env python3
"""Production LLM gateway primitives (stdlib only). Run: python llm_gateway.py"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id: str, tenant: str) -> CorrelationAdapter:
    base = logging.getLogger("llm.gateway")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(base, {"correlation_id": correlation_id, "tenant": tenant})


_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit


class SchemaError(ValueError):
    pass


def validate_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(instance, dict):
            raise SchemaError(f"{path} expected object")
        props: dict[str, Any] = schema.get("properties", {})
        required = schema.get("required", list(props))
        additional = schema.get("additionalProperties", False)
        for key in required:
            if key not in instance:
                raise SchemaError(f"{path}.{key} required")
        for key, value in instance.items():
            if key not in props and additional is False:
                raise SchemaError(f"{path}.{key} additionalProperties=false")
            if key in props:
                validate_schema(value, props[key], f"{path}.{key}")
        return
    if expected == "array":
        if not isinstance(instance, list):
            raise SchemaError(f"{path} expected array")
        item_schema = schema.get("items", {})
        for i, item in enumerate(instance):
            validate_schema(item, item_schema, f"{path}[{i}]")
        return
    checkers: dict[str, Callable[[Any], bool]] = {
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: type(v) is int,
        "boolean": lambda v: isinstance(v, bool),
    }
    if expected in checkers and not checkers[expected](instance):
        raise SchemaError(f"{path} expected {expected}")
    enum = schema.get("enum")
    if enum is not None and instance not in enum:
        raise SchemaError(f"{path} not in enum")


def parse_structured_output(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise SchemaError("root must be object")
    validate_schema(obj, schema)
    return obj


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and (time.monotonic() - self._opened_at) >= self.recovery_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    def allow(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError("circuit open")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError("half-open probe in flight")
                self._half_open_inflight += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    base_seconds: float = 0.25,
    max_seconds: float = 8.0,
    retry_after: float | None = None,
) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            cap = min(max_seconds, base_seconds * (2**i))
            sleep_s = max(cap, retry_after or 0.0)
            time.sleep(random.random() * sleep_s)
    assert last is not None
    raise last


class GrammarPhase(Enum):
    THINKING = "thinking"
    CONSTRAINED = "constrained"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    parameters: dict[str, Any]
    irreversible: bool = False


@dataclass
class FunctionCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelTurn:
    text: str | None
    tool_calls: list[FunctionCall]
    thinking_tokens: int
    output_tokens: int
    refusal: bool = False
    truncated: bool = False
    phase_ended: GrammarPhase = GrammarPhase.CONSTRAINED


class ModelClient(Protocol):
    name: str

    def complete(
        self,
        prompt: str,
        tools: list[ToolSpec],
        schema: dict[str, Any] | None,
        phase: GrammarPhase,
    ) -> ModelTurn:
        ...


@dataclass
class ToolResult:
    call_id: str
    name: str
    payload: str
    idempotency_key: str


class ToolProxy:
    def __init__(self, executors: dict[str, Callable[[dict[str, Any]], Any]]) -> None:
        self._executors = executors
        self._done: dict[str, ToolResult] = {}
        self._lock = threading.Lock()

    def execute(
        self,
        call: FunctionCall,
        spec: ToolSpec,
        *,
        tenant: str,
        thread_id: str,
        turn_index: int,
        allowed: set[str],
        ticket_ok: bool,
    ) -> ToolResult:
        if call.name not in allowed or not ticket_ok:
            raise PermanentError(f"rbac/ticket deny {call.name}")
        validate_schema(call.arguments, spec.parameters)
        canonical = json.dumps(call.arguments, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(
            f"{tenant}|{thread_id}|{call.name}|{canonical}|{turn_index}".encode()
        ).hexdigest()
        with self._lock:
            hit = self._done.get(key)
        if hit is not None:
            return hit
        raw = self._executors[call.name](call.arguments)
        result = ToolResult(call.id, call.name, json.dumps(raw, default=str), key)
        with self._lock:
            self._done[key] = result
        return result


EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["extract", "degraded", "refuse"]},
        "fields": {"type": "object", "additionalProperties": True},
        "injection_suspected": {"type": "boolean"},
    },
    "required": ["intent", "fields", "injection_suspected"],
    "additionalProperties": False,
}

LOOKUP_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


def deterministic_extract(prompt: str) -> dict[str, Any]:
    emails = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", prompt, flags=re.I)
    return {
        "intent": "degraded",
        "fields": {"emails": emails, "note": "deterministic_fallback"},
        "injection_suspected": False,
    }


def screen_tool_result(payload: str) -> bool:
    """Cheap structured classifier stand-in: delimiter/instruction patterns."""
    lowered = payload.lower()
    needles = ("ignore previous", "system prompt", "exfiltrate", "drop table")
    return any(n in lowered for n in needles)


class StaticClient:
    def __init__(self, name: str, turns: list[ModelTurn], fail: type[Exception] | None = None) -> None:
        self.name = name
        self._turns = list(turns)
        self._fail = fail

    def complete(
        self,
        prompt: str,
        tools: list[ToolSpec],
        schema: dict[str, Any] | None,
        phase: GrammarPhase,
    ) -> ModelTurn:
        if self._fail is not None:
            raise self._fail(f"{self.name} down")
        if not self._turns:
            raise PermanentError(f"{self.name} exhausted")
        turn = self._turns.pop(0)
        if phase is GrammarPhase.THINKING and turn.phase_ended is GrammarPhase.CONSTRAINED:
            return turn
        if turn.truncated:
            raise PermanentError("incomplete structured output")
        if turn.refusal:
            raise PermanentError("model refusal")
        if schema is not None and turn.text:
            parse_structured_output(turn.text, schema)
        return turn


class FallbackChain:
    def __init__(
        self,
        primary: ModelClient,
        secondary: ModelClient,
        breaker: CircuitBreaker,
        *,
        retry_attempts: int = 4,
        retry_base: float = 0.25,
        retry_max: float = 8.0,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker
        self.retry_attempts = retry_attempts
        self.retry_base = retry_base
        self.retry_max = retry_max

    def complete(
        self,
        prompt: str,
        tools: list[ToolSpec],
        schema: dict[str, Any] | None,
        phase: GrammarPhase,
        log: CorrelationAdapter,
    ) -> ModelTurn:
        def _try(client: ModelClient) -> ModelTurn:
            return client.complete(prompt, tools, schema, phase)

        kwargs = {
            "attempts": self.retry_attempts,
            "base_seconds": self.retry_base,
            "max_seconds": self.retry_max,
        }
        try:
            self.breaker.allow()
            turn = retry_call(lambda: _try(self.primary), **kwargs)
            self.breaker.record_success()
            log.info("primary_ok model=%s", self.primary.name)
            return turn
        except (CircuitOpenError, TransientError, PermanentError) as exc:
            if not isinstance(exc, CircuitOpenError):
                self.breaker.record_failure()
            log.warning("primary_fail err=%s", exc)
            try:
                turn = retry_call(lambda: _try(self.secondary), **kwargs)
                log.info("secondary_ok model=%s", self.secondary.name)
                return turn
            except (TransientError, PermanentError) as sec:
                log.error("degraded err=%s", sec)
                payload = deterministic_extract(prompt)
                return ModelTurn(
                    text=json.dumps(payload),
                    tool_calls=[],
                    thinking_tokens=0,
                    output_tokens=1,
                    phase_ended=GrammarPhase.CONSTRAINED,
                )


class AgentRuntime:
    def __init__(
        self,
        chain: FallbackChain,
        proxy: ToolProxy,
        catalog: dict[str, ToolSpec],
        max_rounds: int = 8,
    ) -> None:
        self.chain = chain
        self.proxy = proxy
        self.catalog = catalog
        self.max_rounds = max_rounds

    def run(
        self,
        user_text: str,
        *,
        tenant: str,
        allowed_tools: set[str],
        ticket_ok: bool = True,
        enable_thinking: bool = False,
    ) -> dict[str, Any]:
        correlation_id = str(uuid.uuid4())
        log = build_logger(correlation_id, tenant)
        prompt, pii_audit = redact_pii(user_text)
        log.info("pii_redactions count=%s", len(pii_audit))
        thread_id = f"{tenant}:extract"
        tools = [self.catalog[n] for n in allowed_tools if n in self.catalog]
        messages = prompt
        phase = GrammarPhase.THINKING if enable_thinking else GrammarPhase.CONSTRAINED

        for round_i in range(self.max_rounds + 1):
            if round_i == self.max_rounds:
                raise PermanentError("tool round cap (ASI02)")
            use_schema = None if phase is GrammarPhase.THINKING else EXTRACT_SCHEMA
            turn = self.chain.complete(messages, tools, use_schema, phase, log)
            if phase is GrammarPhase.THINKING:
                # Constraints stay off until think_end_id; then this turn is already constrained.
                phase = turn.phase_ended
                if phase is GrammarPhase.THINKING:
                    continue
            if turn.tool_calls:
                for call in turn.tool_calls:
                    spec = self.catalog[call.name]
                    result = self.proxy.execute(
                        call,
                        spec,
                        tenant=tenant,
                        thread_id=thread_id,
                        turn_index=round_i,
                        allowed=allowed_tools,
                        ticket_ok=ticket_ok,
                    )
                    if screen_tool_result(result.payload):
                        raise PermanentError("poison tool_result: injection_suspected")
                    messages += f"\n<tool_result>{result.payload}</tool_result>"
                continue
            if not turn.text:
                raise PermanentError("empty model turn")
            parsed = parse_structured_output(turn.text, EXTRACT_SCHEMA)
            log.info(
                "done thinking_tokens=%s output_tokens=%s breaker=%s",
                turn.thinking_tokens,
                turn.output_tokens,
                self.chain.breaker.state.value,
            )
            return {
                "correlation_id": correlation_id,
                "result": parsed,
                "pii_audit": pii_audit,
                "thinking_tokens": turn.thinking_tokens,
            }
        raise PermanentError("unreachable")


def _lookup(args: dict[str, Any]) -> dict[str, str]:
    return {"source": "crm", "query": args["query"], "hits": "0"}


def _demo() -> None:
    lookup = ToolSpec("lookup_customer", LOOKUP_PARAMS)
    primary = StaticClient(
        "terra",
        [
            ModelTurn(
                text=None,
                tool_calls=[FunctionCall("c1", "lookup_customer", {"query": "acme"})],
                thinking_tokens=12,
                output_tokens=40,
            ),
            ModelTurn(
                text=json.dumps(
                    {
                        "intent": "extract",
                        "fields": {"account": "acme"},
                        "injection_suspected": False,
                    }
                ),
                tool_calls=[],
                thinking_tokens=0,
                output_tokens=28,
            ),
        ],
        fail=TransientError,
    )
    secondary = StaticClient(
        "luna",
        [
            ModelTurn(
                text=None,
                tool_calls=[FunctionCall("c1", "lookup_customer", {"query": "acme"})],
                thinking_tokens=0,
                output_tokens=20,
            ),
            ModelTurn(
                text=json.dumps(
                    {
                        "intent": "extract",
                        "fields": {"account": "acme"},
                        "injection_suspected": False,
                    }
                ),
                tool_calls=[],
                thinking_tokens=0,
                output_tokens=20,
            ),
        ],
    )
    retry = dict(retry_attempts=2, retry_base=0.01, retry_max=0.04)
    runtime = AgentRuntime(
        FallbackChain(primary, secondary, CircuitBreaker(failure_threshold=1), **retry),
        ToolProxy({"lookup_customer": _lookup}),
        {"lookup_customer": lookup},
        max_rounds=8,
    )
    out = runtime.run(
        "Extract account for user@example.com ssn 123-45-6789",
        tenant="t1",
        allowed_tools={"lookup_customer"},
    )
    assert out["result"]["intent"] == "extract"
    assert out["thinking_tokens"] == 0
    assert any(x["type"] == "email" for x in out["pii_audit"])
    degrade = FallbackChain(
        StaticClient("dead", [], fail=TransientError),
        StaticClient("also_dead", [], fail=TransientError),
        CircuitBreaker(failure_threshold=1),
        **retry,
    )
    log = build_logger("demo", "t1")
    degraded = degrade.complete("mail a@b.com", [], EXTRACT_SCHEMA, GrammarPhase.CONSTRAINED, log)
    parsed = parse_structured_output(degraded.text or "", EXTRACT_SCHEMA)
    assert parsed["intent"] == "degraded"
    print(json.dumps({"ok": True, "extract": out["result"], "degraded": parsed}, indent=2))


if __name__ == "__main__":
    _demo()
```

**Behavior encoded (maps to §§2–4):**

- Grammar phase: thinking turns skip `EXTRACT_SCHEMA`; constrained turns must parse.
- Function-call args validated against `additionalProperties: false` before the proxy runs.
- Tool results JSON-encoded; injection screen is a structured-policy stand-in for the Haiku classifier.
- Primary 429-class `TransientError` trips the breaker; secondary serves; dual failure emits schema-valid `intent=degraded`.
- Idempotency key is `sha256(tenant|thread|name|canonical_args|turn)`.
- PII redacted before it would be tokenized; audit stores placeholders only.

**Interview talking point:** retries with jitter handle 429; they do not make `send_wire` safe. Idempotency + round cap + schema-valid deterministic fallback are three different failure classes.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers and component choices are from the research file.

### Scenario 1 — Multi-tenant SaaS copilot (hosted data plane)

**Problem statement.** Design a multi-tenant coding/ops copilot at **~1,000 interactive requests/min** (~17 rps) with **p95 TTFT ≤ 2 s** on extract/classify turns, **p95 time-to-final ≤ 8 s** on medium-reasoning turns, per-tenant cost caps, and irreversible tools (open PR, page on-call). Peak thinking (`effort=max`) must not take down the interactive pool. Compliance: regional inference acceptable at +10%; prompt injection via `tool_result` is in-scope (OWASP LLM01 / ASI02).

**Proposed architecture.**

```
┌──────────────┐     ┌─────────────────────────────────────────────────────────┐
│ Browser IDE  │ SSE │ CONTROL PLANE (your VPC)                                │
│ / Slack bot  │────▶│  Gateway: auth, tenant TPM, breaker, correlation-id     │
└──────────────┘     │    │                                                    │
                     │    ▼                                                    │
                     │  Policy: PII redact, tool RBAC/turn, schema allowlist   │
                     │    │                                                    │
                     │    ▼                                                    │
                     │  Router: Luna/Haiku extract │ Terra/Sonnet medium       │
                     │          Sol/Opus only on async job + timeout 180s+     │
                     │          prompt_cache_key = tenant|prompt_version       │
                     │    │                                                    │
                     │    ▼                                                    │
                     │  Orchestrator: Temporal workflow or LangGraph           │
                     │  PostgresSaver (thread_id), HITL interrupt on PRs       │
                     │  max_rounds=8, parallel_tool_calls=false on writes      │
                     └────┬──────────────────────────────┬─────────────────────┘
                          │ hosted complete()            │ tool tickets
                          ▼                              ▼
                     ┌─────────────────────┐    ┌────────────────────────────┐
                     │ DATA PLANE (vendor) │    │ TOOL PROXIES               │
                     │ tokenizer→prefill→  │    │ Git MCP (short-lived PAT)  │
                     │ decode→strict JSON  │    │ PagerDuty (scope: page)    │
                     │ thinking hidden     │    │ JSON-encode + Haiku screen │
                     └─────────────────────┘    └────────────────────────────┘
                          │
                          ▼
                     ┌─────────────────────────────────────────────────────────┐
                     │ PERSISTENCE / TELEMETRY                                 │
                     │ Postgres checkpoints │ Kafka agent.turns (WORM S3)      │
                     │ usage on response.completed │ breaker + cache hit %     │
                     └─────────────────────────────────────────────────────────┘
```

**Technology choices.** OpenAI Responses `store=true` + `previous_response_id` (or Anthropic client-tool loop) so reasoning items adjacent to function calls are not re-prefilled. `strict: true` on all custom tools. Static system prompt + tool JSON **above** the cache breakpoint; volatile user text below. Client HTTP timeout **>180 s** only on the async reasoning path; interactive path forbids Sol/Opus `max` (AA first-chunk 60–209 s). Cost cap: `max_tokens` + round cap + per-tenant TPM. Fallback: Terra → Luna → deterministic JSON (`intent=degraded`).

**Trade-off evaluation matrix.**

| Dimension | A. Single vendor, Chat Completions, prompted JSON | B. Recommended: Responses/Messages + strict tools + Temporal/LangGraph + tiered models | C. All turns on Sol/Opus max |
| --- | --- | --- | --- |
| Cost / 1k extract (4k/400) | Terra ~$12.80; no cache discipline | Luna **$1.28** extract; Terra cache-steady agent **$15.60/1k**; Sol blowup isolated | Sol **$272/1k** if 8k thinking |
| Latency | Re-reason after every tool; p95 TTFT unpredictable | Extract p50 ~sub-second (Lite/Luna class); medium reasoning ~7 s Opus / ~4.5 s Gemini medium | p50 first-chunk **40–200 s**; 60 s HTTP = outage |
| Ops complexity | Low | Medium (checkpointer, breakers, two product paths) | Low until pages; then incident-only |
| Security posture | Prompted JSON + broad tools = LLM03 | Per-turn RBAC, JSON-encoded tool_result, HITL on writes | Same model power, worse agency surface |
| Scalability ceiling | TPM burns on re-reasoning | Cache + Luna path fits ~17 rps well under T5 40M TPM | In-flight concurrency explodes (50 rps × 150 s = 7,500 — even 17 rps × 150 s ≈ 2,550) |

**Decision rationale.** **B** is the only option that simultaneously hits the 2 s extract p95 (non-reasoning SKU + cache), keeps medium-reasoning interactive (effort medium, not max), and bounds blast radius of irreversible tools (HITL + idempotency + round cap). A fails structured-output reliability and tool quality after the first hop. C fails the latency SLO and the cost cap by 16× on workload C. Regional +10% is accepted for residency; Flex/Batch are for offline evals, not this UX.

### Scenario 2 — Self-host RAG + structured extract (disaggregated data plane)

**Problem statement.** Internal knowledge extract: **50 rps**, 4k prompt (3.6k static corpus instruction + schema + 0.4k retrieved chunks) / 400 token JSON extract, **p99 ITL tight enough that colocated prefill-in-decode is unacceptable**, schema catalog of **~20** JSON Schemas (not unique per tenant). Air-gapped weights; no customer text to a public API. Target **>90% SLO hit** (DistServe goodput definition). GPU budget: enough for two pools (not a 2-GPU hobby box).

**Proposed architecture.**

```
┌────────────┐   ┌─────────────────────────────────────────────────────────────┐
│ ETL / apps │──▶│ CONTROL PLANE                                               │
│ 50 rps JSON│   │ Gateway admit if decode_free_blocks ≥ 10%                   │
└────────────┘   │ Router: sticky by corpus_id (Radix / prefix hash)           │
                 │ Schema compiler: pin 20 XGrammar PDAs in StructuredOutputMgr│
                 │ Orchestrator: single-shot extract (N=0 tools) or N≤2 reads  │
                 └───────────┬──────────────────────────────┬──────────────────┘
                             │                              │
                             ▼                              ▼
                 ┌───────────────────────┐     ┌──────────────────────────────┐
                 │ PREFILL POOL          │     │ DECODE POOL                  │
                 │ high-FLOP, large batch│ NIXL│ high-bandwidth, cont. batch  │
                 │ prefix-cache sticky   │ /   │ PagedAttention + XGrammar    │
                 │ GQA or MLA weights    │Moon-│ grammar bitmask after logits │
                 │                       │cake │ sampler → JSON parser        │
                 └───────────────────────┘     └──────────────┬───────────────┘
                             KV pages                         │
                                                              ▼
                 ┌────────────────────────────────────────────────────────────┐
                 │ PERSISTENCE: block-hash prefix cache (not RPO=0)           │
                 │ TELEMETRY: TTFT, ITL, goodput, KV free %, PDA compile miss │
                 └────────────────────────────────────────────────────────────┘
```

**Technology choices.** vLLM or SGLang; Dynamo on top if KV-aware routing across many prefill workers is required (~2× TTFT claim). XGrammar not llguidance: schemas **repeat**. Disable thinking; this is extract. GQA (Llama/Qwen ecosystem) unless the chosen checkpoint is DeepSeek-class MLA. MoE only if the serving stack’s expert A2A is production-ready on both pools (vLLM MORI-IO TP=4+EP examples). Chunked prefill is the rollback if the second pool cannot be staffed. Hosted Luna at 50 rps is **$5.5k/mo** and **13.2M TPM** — use that as the **opportunity-cost ceiling** for GPU spend.

**Trade-off evaluation matrix.**

| Dimension | A. Colocated vLLM + chunked prefill (SARATHI) | B. Recommended: P/D disagg + sticky prefix + XGrammar catalog | C. Hosted Luna/Flash-Lite |
| --- | --- | --- | --- |
| Cost | Fewer GPUs; lower capex | More GPUs (xPyD); higher capex, better goodput | **$1.28/1k** → ~$5.5k/mo at 50 rps; no GPU ops |
| Latency | p50 OK; **tail ITL inflates** when prefills insert into decode | DistServe: 7.4× requests **or** 12.6× tighter SLO; >90% SLO | p50 first-chunk 0.31 s (Flash-Lite class); p99 is vendor-opaque |
| Ops complexity | Lowest self-host | KV connector (LookupBuffer / MORI-IO read vs write), two autoscalers | Lowest overall; data-residency may forbid |
| Security posture | Weights + data stay in VPC | Same, plus larger network surface (RDMA) | Prompts leave VPC; regional +10% still third-party |
| Scalability ceiling | Bound by interference; PagedAttention 2–4× vs naive still colocated | Independent TTFT vs TPOT scaling; MLA cuts KV bytes | Org TPM (T5 40M; need 13.2M + retries) |

**Decision rationale.** **B** wins when the constraint set is **air-gap + p99 ITL + 50 rps + small schema catalog**. DistServe’s result is specifically about removing prefill/decode interference; that is this workload (4k prefill at 50 rps will constantly collide with 400-token decodes on one pool). A is correct below ~8 GPUs or short prompts (research trade-off table). C wins if data can leave the VPC — then do not self-host just for ideology; Luna already meets extract economics. Sticky `corpus_id` routing is mandatory: 3.6k/4k static tokens are the cache; random load-balancing throws away prefill FLOPs. Pin 20 PDAs so TTFT does not include grammar compile (unique-schema regime would pick llguidance instead).
