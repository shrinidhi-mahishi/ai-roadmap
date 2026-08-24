# Research: LLM Foundations

**Date researched**: 2026-08-21
**Sources consulted**: 93

## 1. System Topology & Mechanics

### 1.1 Transformer architecture (control plane vs data plane)

The 2017 encoder–decoder Transformer defines the data-plane primitive still used by nearly all production LLMs: scaled dot-product attention `softmax(QKᵀ / √d_k) V` plus a position-wise FFN, stacked with residual connections and layer norm ([Attention Is All You Need](https://arxiv.org/abs/1706.03762)). Decoder-only causal masking (GPT lineage) is the serving default; encoder–decoder remains in T5-class and some multimodal stacks.

**Control plane** (request routing, batching, KV placement, tool dispatch, schema compilation) is separate from the **data plane** (tokenizer → embedding → stacked transformer forward → sampler → detokenizer / parser). Production stacks make this split explicit:

- vLLM: HTTP/OpenAI-compat front-end + Engine Core (scheduler, PagedAttention, continuous batching) + Structured Output Manager ([vLLM anatomy, 2025-09-05](https://vllm.ai/blog/2025-09-05-anatomy-of-vllm)).
- NVIDIA Dynamo: orchestration layer *above* vLLM / SGLang / TensorRT-LLM; `PrefillRouter` selects workers, NIXL moves KV GPU-to-GPU, engines stay data-plane ([Dynamo disaggregated serving](https://docs.nvidia.com/dynamo/dev/design-docs/disaggregated-serving); [Dynamo README](https://github.com/ai-dynamo/dynamo)).
- Mooncake (Moonshot Kimi serving): KVCache-centric disaggregated architecture with Transfer Engine + Mooncake Store as a shared KV pool across CPU/DRAM/SSD ([Mooncake docs](https://kvcache-ai.github.io/Mooncake/index.html)).
- Hosted APIs: the provider owns tokenizer, transformer, sampler, and function-call parser; the application owns the agentic loop (execute tools, append `tool_result`, re-call). Anthropic states the model never executes tools; it emits a structured request ([How tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)).

### 1.2 Attention variants (KV-cache economics)

| Variant | KV layout | Typical use | Source |
| --- | --- | --- | --- |
| Multi-Head Attention (MHA) | 1 KV head per Q head | Original Transformer | [Vaswani et al. 2017](https://arxiv.org/abs/1706.03762) |
| Multi-Query Attention (MQA) | 1 shared KV head for all Q heads | Falcon, PaLM decode | [Shazeer 2019](https://arxiv.org/abs/1911.02150) |
| Grouped-Query Attention (GQA) | KV heads = groups of Q heads | Llama 3, Qwen, Gemma | [Ainslie et al., EMNLP 2023](https://aclanthology.org/2023.emnlp-main.298.pdf) |
| Multi-Head Latent Attention (MLA) | Compress K/V into latent `c_KV` (DeepSeek-V3 `d_c=576`) | DeepSeek-V2/V3/R1, Kimi K2, GLM-5 | [DeepSeek-V3 report](https://arxiv.org/abs/2412.19437); [Raschka visual guide](https://magazine.sebastianraschka.com/p/visual-attention-variants) |

GQA is the interpolation: Ainslie et al. show uptrained GQA quality close to MHA while remaining almost as fast as MQA ([GQA paper](https://aclanthology.org/2023.emnlp-main.298.pdf)). MLA caches a low-rank latent plus a small RoPE-decoupled key rather than full per-head K/V; DeepSeek-V3 reports this as the inference-efficiency counterpart to DeepSeekMoE ([DeepSeek-V3](https://arxiv.org/pdf/2412.19437v2)).

**FlashAttention / FlashAttention-2** are IO-aware kernels, not architectural variants: FA reduces HBM traffic by tiling softmax in SRAM (2–4× vs optimized baselines, linear memory instead of quadratic materialization); FA-2 reaches 50–73% of A100 peak FLOPs/s and up to 225 TFLOPs/s (72% MFU) end-to-end GPT training ([FlashAttention-2](https://arxiv.org/abs/2307.08691)). Serving engines (vLLM, SGLang) dispatch to FlashAttention / FlashInfer backends per batch shape ([vLLM anatomy](https://vllm.ai/blog/2025-09-05-anatomy-of-vllm)).

### 1.3 Mixture-of-Experts (MoE)

Sparse MoE replaces the dense FFN with a router + N expert FFNs. Only k experts fire per token.

- **Switch Transformers** (Fedus et al.): simplified top-1 routing to scale toward trillion-parameter models ([Switch Transformers](https://arxiv.org/abs/2101.03961)).
- **Mixtral 8×7B**: 8 experts/layer, top-2 routing; 47B total params, **13B active** per token; 32k dense context; vLLM + MegaBlocks kernels for serving ([Mixtral of Experts](https://arxiv.org/abs/2401.04088)).
- **DeepSeek-V3**: **671B total / 37B activated** per token; DeepSeekMoE + **auxiliary-loss-free** load balancing (expert-specific bias on routing scores, update speed 0.001 in most training); Multi-Token Prediction (MTP) training objective also usable as speculative decoding; pretrained on **14.8T** tokens in **2.788M H800 GPU-hours** ([DeepSeek-V3](https://arxiv.org/abs/2412.19437)).

Serving implication: MoE decode is **all-to-all expert traffic** plus KV. Dynamo exposes `--speculative-moe-runner-backend` / A2A backends for draft MoE; vLLM MORI-IO disagg examples run TP=4 **with expert parallelism** on both prefill and decode pools ([vLLM MORI-IO blog, 2026-04-07](https://vllm.ai/blog/2026-04-07-moriio-kv-connector)).

### 1.4 Prefill vs decode (inference phases)

| Phase | Compute character | Latency KPI | Batching preference |
| --- | --- | --- | --- |
| **Prefill** | Compute-bound; all prompt tokens in parallel; writes full KV | Time-to-first-token (TTFT) | Large batches, high-FLOP GPUs |
| **Decode** | Memory-bound; 1 token/step; reads growing KV + weights | Time-per-output-token (TPOT) / inter-token latency (ITL) | Small batches, high-bandwidth GPUs, continuous batching |

DistServe (OSDI’24) assigns the two phases to different GPUs, eliminating prefill–decode interference and allowing **phase-specific parallelism**. Evaluations: **7.4× more requests** or **12.6× tighter SLO** vs colocated SOTA while meeting latency for **>90%** of requests ([DistServe](https://arxiv.org/abs/2401.09670)). Concurrent academic systems: Splitwise (heterogeneous H100/A100 for energy), TetriInfer, DéjàVu ([Hao AI Lab DistServe blog](https://haoailab.com/blogs/distserve/); [18-month retro](https://haoailab.com/blogs/distserve-retro/)).

vLLM disaggregated prefilling runs two instances (`kv_producer` / `kv_consumer`) plus a KV connector. Benefits cited: independent TP/PP for TTFT vs ITL, and **tail ITL control** (colocated vLLM can insert prefills into a decode batch). Decode can reuse prefill token IDs and skip re-tokenization; tool/reasoning parse and streaming still happen on the decode path ([vLLM disagg prefill](https://docs.vllm.ai/en/stable/features/disagg_prefill/)). MORI-IO **read mode**: proxy waits for prefill `remote_block_ids`, decode pulls KV via RDMA. **Write mode**: proxy dispatches both; prefill **pushes KV layer-by-layer** into decode memory so decode can start as soon as prefill finishes ([MORI-IO](https://vllm.ai/blog/2026-04-07-moriio-kv-connector)).

**Chunked prefill** (SARATHI) is the colocated alternative: split long prefills and piggyback decodes to keep utilization without full disaggregation ([DistServe related work citing SARATHI](https://arxiv.org/html/2401.09670v3)).

**Continuous batching** (Orca, iteration-level scheduling): after each forward step, finished sequences leave and waiting sequences enter. Combined with PagedAttention (OS virtual-memory analog for KV blocks), vLLM reports **2–4× throughput** vs FasterTransformer/Orca at matched latency; near-zero KV waste ([PagedAttention SOSP paper](https://arxiv.org/abs/2309.06180)). Default vLLM block size in common deployments is 16 tokens ([Floating Bytes 2026-07](https://saraswatmks.github.io/2026/07/disaggregated-prefill-decode-vllm.html) — production anecdote, not the paper).

SGLang **RadixAttention** stores KV in a radix tree so shared prefixes (system prompts, tool schemas) are reused across requests ([SGLang RadixAttention](https://mintlify.wiki/sgl-project/sglang/concepts/radix-attention)).

### 1.5 Positional encodings

- **Absolute learned embeddings**: original Transformer / GPT-2; length-locked to training window ([Vaswani et al.](https://arxiv.org/abs/1706.03762)).
- **RoPE**: rotate Q/K in 2D subspaces by position-dependent angles; encodes relative distance in the inner product ([Su et al. 2021](https://arxiv.org/abs/2104.09864)). Dominant in Llama, Mistral, Gemma, Qwen, DeepSeek, gpt-oss. Hugging Face `rope_type`: `default | linear | dynamic | yarn | longrope | llama3` ([Transformers RoPE utils](https://huggingface.co/docs/transformers/en/internal/rope_utils)).
- **ALiBi**: no position embeddings; add a head-specific linear distance bias to `QKᵀ` before softmax; better native length extrapolation but lost ecosystem share after Llama standardized on RoPE ([Press et al. 2021](https://arxiv.org/abs/2108.12409)).
- **YaRN**: piecewise NTK-by-parts + attention temperature; SOTA context extension after fine-tuning on **<~0.1%** of original pretrain data; Dynamic-YaRN claimed **>2×** extension without fine-tuning ([YaRN](https://arxiv.org/abs/2309.00071)).
- **Hybrids (2025)**: Gemma 3 uses RoPE with θ=10k on local sliding-window layers and θ=1M on global layers; Command R7B mixes RoPE local + NoPE global ([RoPE/ALiBi ecosystem writeup](https://medium.com/@wasowski.jarek/one-formula-that-powers-90-of-models-rope-and-alibi-bb025588caee)).

**Lost in the Middle**: U-shaped retrieval accuracy — beginning/end of long context outperform the middle, including on explicitly long-context models (GPT-3.5-16k, Claude-100k, MPT-30B-Instruct ALiBi, LongChat RoPE) ([Liu et al., TACL 2024](https://aclanthology.org/2024.tacl-1.9.pdf)). Ms-PoE reports up to **+3.8** average on Zero-SCROLLS via plug-and-play multi-scale RoPE rescaling ([Found in the Middle, NeurIPS 2024](https://papers.nips.cc/paper_files/paper/2024/file/6ffdbbe354893979367f93e2121e37dd-Paper-Conference.pdf)).

### 1.6 Serving pipeline: tokenizer → transformer → sampling → function-call parse → structured decode

Canonical path inside vLLM / SGLang / hosted APIs:

1. **Chat template + tokenize** (disagg: prefill and decode both render; decode may skip if token IDs transferred) ([vLLM disagg](https://docs.vllm.ai/en/stable/features/disagg_prefill/)).
2. **Prefill forward** → write KV pages; sample first token.
3. **Decode loop**: PagedAttention over KV; **logit processors / grammar bitmask** applied *after* logits, *before* sampling ([vLLM structured decoding intro](https://vllm.ai/blog/2025-01-14-struct-decode-intro)).
4. **Sampler**: temperature, top-p/k, penalties. Reasoning models consume extra decode steps as “thinking” tokens before visible tokens ([OpenAI o1](https://openai.com/index/learning-to-reason-with-llms/)).
5. **Detokenize + parsers**:
   - OpenAI Responses: semantic SSE events (`response.output_text.delta`, `response.function_call_arguments.delta`, `response.reasoning_summary_text.delta`, `response.completed`) ([Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses); [streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events/)).
   - Anthropic: `content` blocks (`text` | `tool_use` | `thinking` | `server_tool_use`); `stop_reason` drives the client loop ([How tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)).
   - Gemini: `functionCall` always carries a unique `id` that must be echoed on `functionResponse` ([Gemini function calling](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)).
6. **Constrained decoding** (if schema/grammar requested): compile JSON Schema / regex / EBNF → PDA/FSM → vocab bitmask; mask illegal logits to −∞ ([XGrammar](https://github.com/mlc-ai/xgrammar/); [vLLM structured outputs](https://docs.vllm.ai/en/v0.20.2/features/structured_outputs/)).

OpenAI Structured Outputs converts the schema to a **context-free grammar** and only allows tokens that keep the partial string schema-legal ([OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)). vLLM backends: `xgrammar` (default, PDA, cache-friendly) and `guidance` / llguidance (fast TTFT on unique schemas) ([Red Hat on vLLM structured outputs](https://developers.redhat.com/articles/2025/06/03/structured-outputs-vllm-guiding-ai-responses)). SGLang `ReasonerGrammarBackend` **bypasses** JSON constraints during thinking (`think_end_id` not yet emitted), then enables the grammar; optional `max_think_tokens` forces `think_end_id` by masking all other tokens ([SGLang constrained output](https://deepwiki.com/sgl-project/sglang/19.2-constrained-and-structured-output)). Overlapped constrained decoding + Spec V2 runs CPU grammar updates concurrent with GPU forward ([SGLang PR #15623](https://github.com/sgl-project/sglang/pull/15623)).

### 1.7 Orchestration topologies (LLM serving + tools)

| Topology | Mechanics | Typical stack |
| --- | --- | --- |
| **ReAct loop** | Model emits thought + tool call; host executes; result appended; repeat until final answer | LangGraph single-agent tool node; OpenAI Responses tool loop; Claude `stop_reason=="tool_use"` while-loop ([LangGraph](https://github.com/langchain-ai/langgraph); [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling); [Claude tool loop](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)) |
| **Supervisor–Worker** | Supervisor LLM routes via tool-shaped handoffs; workers are specialists | `langgraph-supervisor`; OpenAI Agents SDK `handoffs` vs `agent.asTool()`; Google ADK `sub_agents` + LLM-driven delegation; CrewAI `Process.hierarchical` + `manager_llm` ([langgraph-supervisor](https://github.com/langchain-ai/langgraph-supervisor-py); [OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration); [ADK multi-agent](https://github.com/google/adk-docs/blob/5331a07f/docs/agents/multi-agents.md); [CrewAI hierarchical](https://docs.crewai.com/en/learn/hierarchical-process)) |
| **Plan-and-Execute** | Planner emits a step list/DAG; executors run tools without re-planning every token | ADK `SequentialAgent` / `LoopAgent` / `ParallelAgent` as workflow nodes ([ADK 2.0](https://github.com/google/adk-docs/blob/main/docs/2.0/index.md); [ADK Codelab](https://codelabs.developers.google.com/codelabs/production-ready-ai-with-gc/3-developing-agents/build-a-multi-agent-system-with-adk)) |
| **DAG / graph runtime** | Explicit edges, reducers, interrupts | LangGraph `StateGraph`; ADK 2.0 Workflow Runtime (`BaseAgent` subclasses `BaseNode`) ([LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/durable-execution); [ADK 2.0](https://adk.dev/)) |

OpenAI Agents SDK: **handoff** transfers conversation ownership (`transfer_to_<agent>`); **agents as tools** keep the manager as the user-facing responder ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/)). `toolUseBehavior`: `run_llm_again` (default), `stop_on_first_tool`, name allowlist, or custom function ([AgentConfiguration](https://openai.github.io/openai-agents-js/openai/agents-core/interfaces/agentconfiguration/)).

CrewAI: `Process.sequential` requires a pre-assigned agent per task; `Process.hierarchical` requires `manager_llm` or `manager_agent`; manager allocates tasks dynamically ([CrewAI processes](https://docs.crewai.com/edge/en/concepts/processes)).

### 1.8 Message protocols

- **Sync HTTP**: Chat Completions / Messages / `generateContent` / Bedrock `Converse` — full JSON when generation ends.
- **SSE streaming**: OpenAI `stream=true` on Responses (typed `event:` + `data:` frames, `sequence_number` ordering); Anthropic Messages streaming; Gemini streamGenerateContent. Token usage is authoritative only on the terminal event (`response.completed` / final `message_delta`) ([OpenAI streaming](https://developers.openai.com/api/docs/guides/streaming-responses)).
- **WebSocket**: OpenAI Responses WebSocket mode for persistent transport + incremental input via `previous_response_id` ([OpenAI streaming guide](https://developers.openai.com/api/docs/guides/streaming-responses)).
- **Async batch**: OpenAI Batch API; Gemini Batch at **50%** of standard token rates, target ≤24h ([Gemini optimization](https://ai.google.dev/gemini-api/docs/optimization)).
- **Bedrock**: `Converse` / `ConverseStream` with `toolConfig.tools[].toolSpec` + `toolChoice`; `stopReason=tool_use`; multiple `toolUse` blocks in one turn ([Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html); [Bedrock recipes](https://aws-samples.github.io/amazon-bedrock-samples/agents-and-function-calling/function-calling/function_calling_with_converse/function_calling_with_converse/)).

### 1.9 Function calling (native vs prompted)

**Native (API-constrained)**: tools are first-class request fields; sampler + parser emit typed calls.

OpenAI ([function calling](https://developers.openai.com/api/docs/guides/function-calling)):

- `tools[]` with JSON Schema `parameters`; `strict: true` uses Structured Outputs (all properties required, `additionalProperties: false`).
- `tool_choice`: `"auto"` | `"required"` | `"none"` | `{"type":"function","name":...}` | allowed-tools subset.
- `parallel_tool_calls: false` forces 0 or 1 call. GPT-5+ can mix custom functions with built-in tools with restrictions (built-ins not in the same parallel batch).
- Responses API attempts to **normalize** schemas into strict mode; Chat Completions defaults non-strict. `tool_search` (load deferred tools) requires `gpt-5.4`+.
- Custom tools: free-form text I/O vs JSON functions.

Anthropic ([how tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works); [tool concepts](https://github.com/anthropics/skills/blob/HEAD/skills/claude-api/shared/tool-use-concepts.md)):

- Client tools: `tool_use` / `tool_result` round-trip. Anthropic-schema tools (`bash`, `text_editor`, `computer`, `browser`, `memory`) are trained-in signatures executed by the client.
- Server tools (`web_search`, `web_fetch`, `code_execution`, `tool_search`): Anthropic runs the loop; `pause_turn` when the internal iteration cap hits.
- `strict: true` on tools = grammar-constrained sampling. **Incompatible** with programmatic tool calling, citations, message prefilling. Schema compilation cached **24h**; first request pays compile latency.
- Programmatic tool calling: tools exposed as async Python in a sandbox; `asyncio.gather` for parallel; cannot combine with `strict: true` or `disable_parallel_tool_use: true` ([programmatic tool calling](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)).

Gemini ([function calling](https://ai.google.dev/gemini-api/docs/generate-content/function-calling); [structured output](https://ai.google.dev/gemini-api/docs/structured-output)):

- `functionDeclarations` + `functionCall`/`functionResponse` with mandatory `id`.
- Tool-combination mode (Gemini 3): constrain to function-call **or** NL; `allowed_function_names`; reduces `Malformed_Function_Call`.
- Gemini 3 series can combine Structured Outputs with built-in tools (Search, URL Context, Code Execution, File Search) and function calling.
- Python SDK can auto-execute functions (prototype); production still needs an explicit loop for observability.

**Prompted (legacy)**: “return JSON with keys …” in the system prompt. No logit mask; validity is best-effort. JSON mode (OpenAI `json_object`) guarantees parseable JSON **not** schema adherence, and requires the word “json” in the input ([OpenAI Help Center](https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api)).

### 1.10 Structured output reliability mechanisms

| Mechanism | Guarantee | Failure shape |
| --- | --- | --- |
| Prompted JSON | None | Truncation, extra keys, markdown fences |
| JSON mode | Valid JSON syntax | Schema drift |
| Constrained decoding (CFG/PDA/FSM) | Every sampled token is schema-legal if generation completes | Refusals, `max_tokens` truncation, unsupported schema features, distribution shift onto “safe” enums |
| Provider `strict` / `json_schema` | Same as constrained decode on supported subset | 400 on illegal schema; `refusal` field (OpenAI) / `stop_reason: refusal` (Anthropic) |

OpenAI: Structured Outputs via function calling **or** `text.format` / `json_schema`; use functions when bridging to app actions, `json_schema` when structuring the user-facing reply ([Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)). Anthropic: `output_config.format` JSON outputs + `strict` tools; `client.messages.parse()` validates client-side ([tool concepts](https://github.com/anthropics/skills/blob/HEAD/skills/claude-api/shared/tool-use-concepts.md)). Gemini: `response_format` + `mime_type=application/json` + schema subset; keys emitted in schema order ([Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)). XGrammar: CFG → PDA; near-zero JSON overhead; up to **3.5×** vs Outlines on JSON schema mask generation and **>10×** on CFG workloads in MLC benches ([XGrammar MLC blog](https://blog.mlc.ai/2024/11/22/achieving-efficient-flexible-portable-structured-generation-with-xgrammar); [MLSys 2025](https://catalyst.cs.cmu.edu/projects/xgrammar.html)). Outlines: FSM over logits (Willard & Louf 2023), historically higher compile cost on the batch critical path ([vLLM struct-decode intro](https://vllm.ai/blog/2025-01-14-struct-decode-intro)).

**Reasoning ∩ structured output**: constraints must not apply to thinking tokens. SGLang’s reasoner grammar is the open-source encoding of that state machine ([SGLang](https://deepwiki.com/sgl-project/sglang/19.2-constrained-and-structured-output)).

## 2. Token Economics & NFR Metrics

### 2.1 Published latency (Artificial Analysis, accessed 2026-08-21)

Artificial Analysis reports **median** output tok/s and **median first-chunk** (TTFT-like), not vendor p95/p99. Values below are medians from their public leaderboards ([AA models](https://artificialanalysis.ai/leaderboards/models); [AA providers](https://artificialanalysis.ai/leaderboards/providers)).

| Model / setting | Median first chunk | Median tok/s | Total response (median) |
| --- | --- | --- | --- |
| Gemini 2.5 Flash-Lite (non-reasoning) | **0.31 s** | — | — |
| Command A+ | 0.38 s | — | — |
| Gemini 3.7 Flash (low) AI Studio | 0.93 s | 333 | 2.44 s |
| Gemini 3.7 Flash (medium) | 4.55 s | 338 | 6.03 s |
| Gemini 3.7 Flash (high) | 12.10–15.42 s | 385–390 | 13.40–16.70 s |
| Claude Opus 5 (medium) | 7.20 s | 60 | 15.57 s |
| Claude Opus 5 (high) | 20.73 s | 59 | 29.26 s |
| Claude Opus 5 (xhigh) | 29.10 s | 60 | 37.41 s |
| Claude Opus 5 (max) | 60.12 s | 60 | 68.41 s |
| GPT-5.6 Sol (xhigh) | 39.54–43.11 s | 71–72 | 46.47–50.04 s |
| GPT-5.6 Sol (max) | 120.71–209.11 s (provider-dependent) | 71–130 | 124.57–216.16 s |
| Celeris-1 | 0.58 s | **1,932** | 0.84 s |
| Mercury 2 | 4.67 s | **1,044** | 5.15 s |

**[inferred]** Hosted p95 TTFT is typically 1.5–3× median on mixed traffic because prefill queueing and reasoning-token preambles dominate tails; DistServe’s design goal is >90% SLO attainment rather than a published p99 number ([DistServe](https://arxiv.org/abs/2401.09670)). vLLM colocated serving **inflates tail ITL** when prefills insert into decode batches; disagg is the documented mitigation ([vLLM disagg](https://docs.vllm.ai/en/stable/features/disagg_prefill/)).

Gemini **Priority** tier: “seconds” latency, non-sheddable, **+75–100%** vs standard token price. **Flex**: 1–15 min target, sheddable, **50%** discount. **Batch**: ≤24h, **50%** discount ([Priority inference](https://ai.google.dev/gemini-api/docs/priority-inference); [optimization](https://ai.google.dev/gemini-api/docs/optimization)).

Self-host anecdote (not a lab SLA): one vLLM disagg write-up cites ~12 ms extra TTFT for an 1,800-token cold prefill vs ~400 ms queue, and ~5 ms back-pressure wait when decode free blocks <10% ([Floating Bytes](https://saraswatmks.github.io/2026/07/disaggregated-prefill-decode-vllm.html)). Treat as **[inferred]/operator-reported**, not a vendor SLA.

### 2.2 Current API prices (per 1M tokens, 2026-08-21)

**OpenAI GPT-5.6 family** — standard processing, context **<270K** ([openai.com/api/pricing](https://openai.com/api/pricing/); [developers.openai.com pricing](https://developers.openai.com/api/docs/pricing)):

| Model | Input | Cached input | Cache writes | Output | Long-context input / cached / writes / output |
| --- | --- | --- | --- | --- | --- |
| gpt-5.6-sol | $5.00 | $0.50 | $6.25 | $30.00 | $10 / $1.00 / $12.50 / $45 |
| gpt-5.6-terra | $2.00 | $0.20 | $2.50 | $12.00 | $4 / $0.40 / $5.00 / $18 |
| gpt-5.6-luna | $0.20 | $0.02 | $0.25 | $1.20 | $0.40 / $0.04 / $0.50 / $1.80 |

Regional/data-residency endpoints: **+10%** for eligible models released on/after 2026-03-05. Fast/priority processing is a separate `service_tier`. Web search: **$10 / 1k calls** + search-content tokens at model rates (preview non-reasoning: $25 / 1k, content tokens free) ([OpenAI pricing](https://developers.openai.com/api/docs/pricing)).

**Anthropic** ([Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)):

| Model | Input | 5m cache write | 1h cache write | Cache read | Output |
| --- | --- | --- | --- | --- | --- |
| Claude Fable 5 / Mythos 5 | $10 | $12.50 | $20 | $1.00 | $50 |
| Claude Opus 5 (and 4.5–4.8) | $5 | $6.25 | $10 | $0.50 | $25 |
| Claude Sonnet 5 | $2 | $2.50 | $4 | $0.20 | $10 |
| Claude Sonnet 4.6 / 4.5 | $3 | $3.75 | $6 | $0.30 | $15 |
| Claude Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 |

Sonnet 5 **$2/$10** introductory price was made **standard** (the scheduled 2026-09-01 rise to $3/$15 **will not occur**). Claude 4.7+ tokenizer ≈ **+30%** tokens vs prior tokenizer for the same text. Bedrock/Vertex **regional** endpoints: **+10%** vs global for 4.5+. First-party `inference_geo: "us"`: **1.1×**. Marketplace bills **CCU** at $0.01/CCU (100 CCU = $1 of API fees).

**Google Gemini Developer API** ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)) — output **includes thinking tokens**:

| Model | Standard in / out | Cache read | Cache storage | Notes |
| --- | --- | --- | --- | --- |
| gemini-3.6-flash | $1.50 / $7.50 | $0.15 | $1.00 / 1M tok / hour | Priority $2.70 / $13.50 |
| gemini-3.5-flash | $1.50 / $9.00 | $0.15 | $1.00 / 1M / h | Priority $2.70 / $16.20 |
| gemini-3.5-flash-lite | $0.30 / $2.50 | $0.03 | $1.00 / 1M / h | |
| gemini-3.1-pro-preview | $2.00 / $12.00 (≤200k); $4.00 / $18.00 (>200k) | $0.20 / $0.40 | **$4.50 / 1M / h** | Priority $3.60/$21.60 (≤200k) |

Grounding with Google Search: 5,000 prompts/month free across Gemini 3, then **$14 / 1,000 search queries**. Batch/Flex = 50% of standard.

**DeepSeek** official table ([api-docs.deepseek.com pricing](https://api-docs.deepseek.com/quick_start/pricing)), per 1M tokens. Peak = 01:00–04:00 **and** 06:00–10:00 UTC; off-peak = half of peak. Context 1M, max output 384K. Tool calls + JSON output supported.

| Model | Cache hit off/peak | Cache miss off/peak | Output off/peak | Concurrency |
| --- | --- | --- | --- | --- |
| deepseek-v4-flash | $0.007 / $0.014 | $0.22 / $0.44 | $0.66 / $1.32 | 2500 |
| deepseek-v4-pro | $0.022 / $0.044 | $0.66 / $1.32 | $1.98 / $3.96 | 500 |

Cache is **automatic disk prefix cache**; `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` in `usage`; best-effort, eviction “hours to days” ([DeepSeek KV cache](https://api-docs.deepseek.com/guides/kv_cache)). Hit ≈ **3.2%** of miss on Flash off-peak ($0.007 / $0.22).

### 2.3 Cost formula and $ per 1k executions

Let \(C = n \cdot (T_{in,miss} P_{miss} + T_{in,hit} P_{hit} + T_{write} P_{write} + T_{out} P_{out}) / 10^6\), with prices per token-million.

Worked **$ / 1,000 executions** (no cache write unless noted):

**A. Deterministic extract** — 4k in / 400 out, no reasoning:

| Stack | Calculation | $ / 1k exec |
| --- | --- | --- |
| GPT-5.6 Luna | 1000×(4000×0.20 + 400×1.20)/1e6 | **$1.28** |
| GPT-5.6 Terra | 1000×(4000×2 + 400×12)/1e6 | **$12.80** |
| Sonnet 5 | 1000×(4000×2 + 400×10)/1e6 | **$12.00** |
| Haiku 4.5 | 1000×(4000×1 + 400×5)/1e6 | **$6.00** |
| Gemini 3.5 Flash-Lite | 1000×(4000×0.30 + 400×2.50)/1e6 | **$2.20** |
| DeepSeek V4 Flash off-peak miss | 1000×(4000×0.22 + 400×0.66)/1e6 | **$1.14** |

**B. Agent turn** — 20k system+tools (90% cache hit after first write) + 1k new user + 800 out:

OpenAI GPT-5.6 Terra, GPT-5.6+ write fee 1.25×:

- First call write 20k at $2.50/M + 1k miss at $2 + 800 out at $12 → $0.050 + $0.002 + $0.0096 = **$0.0616**
- Steady state: 20k hit at $0.20/M + 1k miss $2 + 800 out $12 → $0.004 + $0.002 + $0.0096 = **$0.0156**
- **$15.60 / 1k steady-state turns**

Anthropic Sonnet 5, 5-minute cache (write 1.25×, read 0.1×):

- First: 20k write $2.50/M + 1k $2 + 800 $10 → $0.050 + $0.002 + $0.008 = **$0.060**
- Hit: 20k × $0.20/M + 1k $2 + 800 $10 → $0.004 + $0.002 + $0.008 = **$0.014**
- **$14.00 / 1k steady-state turns** (breakeven after **one** 5-minute hit: 1.25 + 0.1 < 2.0) ([Anthropic cache math](https://platform.claude.com/docs/en/about-claude/pricing)).

**C. Reasoning blowup** — same 4k in, **8k thinking + 400 answer** billed as output:

- GPT-5.6 Sol: 1000×(4000×5 + 8400×30)/1e6 = **$272.00 / 1k** vs $17.00 if 400 out only (**16×**).
- Opus 5: 1000×(4000×5 + 8400×25)/1e6 = **$230.00 / 1k**.
- Gemini 3.6 Flash: 1000×(4000×1.50 + 8400×7.50)/1e6 = **$69.00 / 1k**.

OpenAI documents that reasoning tokens are **output-billed** even when hidden ([o1 announcement](https://openai.com/index/learning-to-reason-with-llms/); [reasoning best practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices)). Gemini pricing page states output includes thinking tokens ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)). Anthropic reports `usage.output_tokens_details.thinking_tokens` ([release notes](https://platform.claude.com/docs/en/release-notes/overview)).

### 2.4 Prompt caching vs semantic caching

| Provider | Enable | Match | Min prefix | Write | Read | TTL |
| --- | --- | --- | --- | --- | --- | --- |
| OpenAI GPT-5.6+ | Automatic + optional breakpoints / `prompt_cache_key` | Exact prefix at breakpoints | 1024 strict | 1.25× | 0.1× | 30m (`prompt_cache_options.ttl`) |
| OpenAI earlier | Automatic | Best-effort longest prefix | 1024–2048 | free | cached rate (historically 50% on some SKUs; GPT-5.6 table is 90% off) | 5–10 min idle, up to 1h off-peak (`in_memory`) or 24h retention option |
| Anthropic | `cache_control` automatic **or** ≤4 explicit breakpoints | Exact block prefix | 1024 Sonnet; 4096 Opus/Haiku 4.5 | 1.25× (5m) / 2× (1h) | 0.1× | 5m default (refreshed on hit) or 1h |
| Gemini implicit | Automatic on 2.5+ | Prefix; **no savings guarantee** | 1024 Flash / 4096 Pro (docs vary) | none | ~0.1× when it hits | opportunistic |
| Gemini explicit | Named cache | Guaranteed | same | storage rent | 0.1× | billed per hour ($1/M/h typical; **$4.50/M/h** on 3.1 Pro Preview) |
| DeepSeek | Always-on disk | Full cache-prefix **unit** match (not arbitrary substring) | n/a | none | ~3% of miss | hours–days; construction takes seconds |

OpenAI: cached tokens **still count toward TPM**; caching does not change rate-limit math or guarantee identical outputs ([OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Anthropic: timestamps/IDs in the static prefix **bust** the cache; move volatility below the breakpoint ([Anthropic cost cookbook](https://platform.claude.com/cookbook/cost-optimization-cost-optimization)). DeepSeek V4: Sliding Window Attention changes prefix units; persistence at user-input end, model-output end, and detected common prefixes ([DeepSeek cache](https://api-docs.deepseek.com/guides/kv_cache)).

**Semantic cache** (embed query → reuse answer if cosine > threshold): not a first-party LLM-API feature. Hit rate is workload-specific; invalidation must track tool/DB mutations. No vendor publishes a universal hit-rate SLA. **[inferred]** 20–40% hit on FAQ chat; near 0% on unique agent tool traces.

### 2.5 Dynamic model routing

Documented levers, not a single RFC:

- **Capability routing**: cheap non-reasoning (Luna / Flash-Lite / Haiku) for extract/classify; reasoning (Sol / Opus / 3.1 Pro) for math/code; `reasoning.effort` / Anthropic `output_config.effort` / Gemini thinking as a continuous knob. AA shows Opus 5 **max** vs **medium**: first-chunk 60 s vs 7.2 s at similar ~60 tok/s ([AA](https://artificialanalysis.ai/leaderboards/models)).
- **SLA routing**: Gemini Priority vs Standard vs Flex vs Batch ([optimization](https://ai.google.dev/gemini-api/docs/optimization)). OpenAI Fast/`priority` service tier ([pricing](https://developers.openai.com/api/docs/pricing)).
- **KV-aware routing**: Dynamo router uses cache-overlap scores; claimed **~2× faster TTFT** by avoiding redundant prefill ([Dynamo README](https://github.com/ai-dynamo/dynamo)). Sticky routing to prefix-cache-hot prefill nodes is required for high prefix hit rate ([Floating Bytes](https://saraswatmks.github.io/2026/07/disaggregated-prefill-decode-vllm.html)).
- **Cascade / speculative**: small model drafts, large model verifies (EAGLE-3 in SGLang) ([SGLang speculative decoding](https://docs.sglang.io/docs/advanced_features/speculative_decoding)). DeepSeek MTP heads can serve as speculative heads ([DeepSeek-V3](https://arxiv.org/abs/2412.19437)).

### 2.6 Throughput, RPM/TPM, batching, KV

OpenAI **org usage tiers** (monthly spend caps): Free $100; T1 $5 paid → $100/mo; T2 $50 → $500; T3 $100 → $1,000; T4 $250 → $5,000; T5 $1,000 → **$200,000/mo**. Limits are org+project, **not** user. Long-context models have separate TPM buckets ([rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). Example **gpt-5** published RPM/TPM: T1 500 / 500k; T2 5k / 1M; T3 5k / 2M; T4 10k / 4M; T5 **15k RPM / 40M TPM**; Batch queue T5 15B tokens ([GPT-5 model page](https://developers.openai.com/api/docs/models/gpt-5)). Gemini paid billing caps: T1 $250, T2 $2,000, T3 $20k–$100k+ ([Gemini billing](https://ai.google.dev/gemini-api/docs/billing)). DeepSeek concurrency **2500** Flash / **500** Pro ([DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing)).

Self-host: throughput ≈ `min(compute_ceiling, KV_pages_free, max_num_seqs)`. PagedAttention 2–4× vs Orca/FasterTransformer at iso-latency ([PagedAttention](https://arxiv.org/abs/2309.06180)). Prefix/Radix cache hit rates of **~98%** are reported on highly sticky creator workloads; spill at 85% util caused **~8%** extra misses in one write-up ([Floating Bytes](https://saraswatmks.github.io/2026/07/disaggregated-prefill-decode-vllm.html)).

## 3. Distributed Resilience & State

### 3.1 Durable execution (application vs KV)

**Application state** (messages, tool results, interrupts) is not the KV cache.

LangGraph: compile with a checkpointer; snapshot at every **superstep**. `thread_id` selects the timeline. `InMemorySaver` dies on process exit; production = `PostgresSaver` / `SqliteSaver`. Stores hold cross-thread memory. `PostgresSaver` `thread_id` column length limit **255**. Unbounded checkpoint history increases latency/storage — prune or cron-delete ([LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/durable-execution)). Human-in-the-loop = `interrupt()` + resume on same thread. Time-travel = `get_state_history`. LangGraph 1.0 GA October 2025 ([LangGraph GitHub](https://github.com/langchain-ai/langgraph)).

OpenAI Responses: `store=true` + `previous_response_id` (or replay output items) so **reasoning items adjacent to function calls** stay in context for o3/o4-mini; Chat Completions is stateless and **re-reasons** after each tool call (more tokens, worse tool quality) ([reasoning best practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices)).

Anthropic server-tool loop: `pause_turn` → resend conversation including the paused assistant message ([How tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)).

### 3.2 KV-cache durability and disaggregation

KV is a **soft** cache: prefix reuse, not a transaction log.

- vLLM LookupBuffer `insert` / `drop_select` between prefill and decode instances ([vLLM disagg](https://docs.vllm.ai/en/stable/features/disagg_prefill/)).
- Dynamo NIXL: non-blocking GPU→GPU KV; workers publish `RuntimeConfig` including KV capacity; **xPyD** pools resize at runtime ([Dynamo](https://docs.nvidia.com/dynamo/dev/design-docs/disaggregated-serving)).
- Mooncake Store: hierarchical KV (device / host / remote); SGLang uses it as RadixAttention backend (2025-09-10) ([Mooncake](https://kvcache-ai.github.io/Mooncake/index.html)).
- Dynamo agentic inference: subagent cold-start — lead agent writes tool-def/system-prompt blocks to shared storage; subagents RDMA-read instead of re-prefill; decode-produced tokens written back so the next prefill worker can fetch them ([Dynamo agentic inference](https://github.com/ai-dynamo/dynamo/blob/ce0cb901/docs/digest/agentic-inference/agentic-inference.md)). Retention/pin APIs today are **per-worker**; cross-worker pin is described as in-progress.

Provider prompt caches are **not** customer-exportable checkpoints. OpenAI GPT-5.6 TTL is 30 minutes exact; Anthropic 5 minutes (refresh-on-hit) or 1 hour at 2× write.

### 3.3 Circuit breakers, rate limits, back-pressure

- OpenAI: HTTP 429 + `Retry-After` / `x-ratelimit-*` headers (`ratelimit-limit-requests`, `ratelimit-limit-tokens`, remaining, reset). Batch when RPM-bound but TPM-free ([rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).
- Gemini Flex is **sheddable** under standard spikes — a built-in load-shed circuit ([optimization](https://ai.google.dev/gemini-api/docs/optimization)).
- DeepSeek hard concurrency caps (2500/500) ([pricing](https://api-docs.deepseek.com/quick_start/pricing)).
- vLLM: when KV free blocks collapse, scheduler **preempts/swaps** sequences to CPU; operator back-pressure: stop dispatching prefill if decode free blocks < threshold (example 10%) ([Floating Bytes](https://saraswatmks.github.io/2026/07/disaggregated-prefill-decode-vllm.html); [Runpod vLLM guide](https://www.runpod.io/articles/guides/vllm-pagedattention-continuous-batching)).
- DistServe: goodput = throughput **conditional on TTFT+TPOT SLOs**; over-admission destroys SLO even if raw tok/s looks high ([DistServe](https://arxiv.org/abs/2401.09670)).

> ⚠️ Limited public data available for this dimension. No major provider publishes circuit-breaker trip curves, KV-OOM postmortems with exact HBM watermarks, or p99 prefill-queue delay distributions for production fleets.

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust around LLM APIs

Treat the model as an **untrusted planner**. Tokens, IAM roles, and network egress live on the **tool host**, not in the prompt. OWASP GenAI LLM Top 10 **2026** (published 2026-08-04): **LLM01 Prompt Injection** remains #1; **Excessive Agency** moved to **LLM03** (largest rank jump); Hidden Context Exposure replaces system-prompt leakage as LLM08 ([OWASP project](https://owasp.org/www-project-top-10-for-large-language-model-applications/); [Check Point summary](https://blog.checkpoint.com/ai-security/reading-the-signals-in-the-owasp-llm-top-10-2026/)). Companion **OWASP Top 10 for Agentic Applications (ASI)** 2026: ASI01 Goal Hijack, ASI02 Tool Misuse (recursive tool use, unsafe composition), ASI03 Identity Abuse ([DeepTeam ASI mapping](https://trydeepteam.com/docs/frameworks-owasp-top-10-for-agentic-applications)).

Patterns:

- Network: private endpoints (Azure OpenAI / Bedrock VPC / Vertex PSC); no tool that can reach metadata servers.
- Auth: per-request signed tool tickets; short-lived STS, not a long-lived superuser key in env vars visible to the model.
- Data residency: OpenAI +10% regional; Anthropic 1.1× `inference_geo=us`; Bedrock global vs regional (+10% for 4.5+).

### 4.2 Tool-level RBAC

Capability **scoping per turn**: do not attach `send_email` unless the user asked to send mail ([EmberLM on Anthropic’s metric shift](https://emberlm.dev/blog/indirect-prompt-injection-why-anthropic-dropped-direct-metric)). OpenAI `tool_choice` allowed-tools subset and Agents SDK `isEnabled` predicates hide handoffs at runtime ([function calling](https://developers.openai.com/api/docs/guides/function-calling); [JS handoffs](https://openai.github.io/openai-agents-js/guides/handoffs/)). Bedrock `toolChoice` can force a named tool or `{auto:{}}` ([Bedrock recipes](https://aws-samples.github.io/amazon-bedrock-samples/agents-and-function-calling/function-calling/function_calling_with_converse/function_calling_with_converse/)). Human-in-the-loop for irreversible tools: LangGraph `interrupt()`; Anthropic computer-use classifiers ask for confirmation on screenshot injections ([mitigate jailbreaks](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)).

### 4.3 PII, sandbox, audit

- **PII**: redact **before** tokenize (control plane). Cached prefixes must not contain secrets — cache keys are content-addressed; a SSN in the system prompt is stored for the TTL.
- **Sandbox**: Anthropic `code_execution` is server-side; client `bash`/`computer` must be OS-sandboxed. Programmatic tool calling runs in Anthropic’s code sandbox ([programmatic tool calling](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)).
- **Audit**: persist `call_id` / `toolUseId` / Gemini `id`, hashed args, policy decision, latency, token breakdown (`thinking_tokens`, `cached_tokens`, `cache_write_tokens`). OpenAI streaming: usage only on `response.completed` ([streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events/)).

### 4.4 Prompt injection via tool results and structured-output bypass

Indirect injection (Greshake et al., arXiv:2302.12173) is the dominant agent threat: malicious text in web pages, emails, RAG chunks, **tool_result** bodies ([FutureAGI 2026](https://futureagi.com/blog/prompt-injection-2025/); [Verax, 2026-08-03](https://www.verax.ai/blog/everything-is-instructions-how-prompt-injection-hides-inside-tool-results)).

Anthropic official mitigations ([mitigate jailbreaks](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)):

1. Put untrusted content **only** in `tool_result`, never system/user text.
2. Label source in tool description / result structure.
3. System policy: tool content is data, not commands.
4. **JSON-encode** third-party strings so attackers cannot break out of delimiters.
5. Do **not** put developer instructions inside tool results (model treats them as untrusted).
6. Least privilege + sandbox.
7. Screen tool output with Haiku 4.5 + **structured output** boolean `injection_suspected` before appending.

**Structured-output bypass**: constrained decoding guarantees **shape**, not **benign semantics**. A schema-valid `{"sql":"DROP TABLE users"}` or `{"url":"https://exfil.attacker"}` still executes if the tool host does not authorize. Classifier schemas (`injection_suspected: boolean`) are the intended pairing of structured output with security ([Anthropic](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks); [FutureAGI structured-output eval 2026](https://futureagi.com/blog/evaluating-llm-structured-output-modes-2026/)). Constrained decode can also **collapse** to a default enum when the preferred token is masked — schema-valid, semantically wrong.

Claudy Day research: exfil via **allowlisted** `api.anthropic.com` Files API, bypassing naive egress filters ([TrueFoundry](https://www.truefoundry.com/blog/claude-code-prompt-injection)). Promptfoo: GPT-5.2 jailbreak success **4.3% → 78.5%** in multi-turn vs single-turn (third-party eval, not OpenAI) ([EmberLM citing Promptfoo](https://emberlm.dev/blog/indirect-prompt-injection-why-anthropic-dropped-direct-metric)).

## 5. Production Failure Modes

### 5.1 Context-window degradation

- **Lost-in-the-middle** accuracy drop when the needle is mid-context ([Liu et al.](https://aclanthology.org/2024.tacl-1.9.pdf)).
- RoPE models **without** YaRN/NTK/LongRoPE degrade when serving length ≫ train length ([YaRN](https://arxiv.org/abs/2309.00071)).
- Anthropic 4.7+ tokenizer **+~30%** tokens → earlier `max_tokens` / bill shock on migrated prompts ([Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)).
- Long-context OpenAI SKUs: **2×** input/output prices above the short-context cutoff (270K for GPT-5.6) ([OpenAI pricing](https://developers.openai.com/api/docs/pricing)). Gemini 3.1 Pro doubles input above **200k** ($2→$4) and raises output $12→$18 ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)).

### 5.2 Infinite / recursive tool loops (ASI02)

No native “max tool rounds” in the base Chat Completions spec — the **application** while-loop must cap iterations. Anthropic server tools have an internal cap then `pause_turn` ([How tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)). LangGraph: recursion limit on the graph; CrewAI hierarchical manager can re-delegate indefinitely if validation never accepts. Parallel tool storms: Bedrock Converse can emit **all** independent `toolUse` blocks in turn 1 (4 calls observed in one A/B harness) ([shinyaz 2026-03](https://shinyaz.com/en/til/2026/03/28/bedrock-tool-use-parallel-calls)). Set `parallel_tool_calls=false` / Anthropic `disable_parallel_tool_use` when fan-out is dangerous (not compatible with Anthropic programmatic calling).

### 5.3 Hallucinated tool parameters

Non-strict function calling is **best-effort**. OpenAI recommends `strict: true`; illegal schemas are **rejected** rather than silently degraded ([function calling](https://developers.openai.com/api/docs/guides/function-calling)). Gemini `AUTO` vs constrained tool-combination mode: latter reduces `Malformed_Function_Call` ([Gemini function calling](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)). Fine-tuned OpenAI models: **strict disabled** if multiple functions are called in one turn. Prompted JSON remains the highest hallucination rate; JSON mode still allows wrong keys ([OpenAI Help](https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api)).

### 5.4 Schema-invalid JSON

Failure modes even with constrained decode ([OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs); [FutureAGI 2026](https://futureagi.com/blog/evaluating-llm-structured-output-modes-2026/); [Anthropic tool concepts](https://github.com/anthropics/skills/blob/HEAD/skills/claude-api/shared/tool-use-concepts.md)):

- `refusal` / `stop_reason=refusal` — output **does not** match schema.
- `max_tokens` / `incomplete` — truncated JSON.
- Unsupported JSON Schema (recursion, dynamic keys) → 400 or silent constraint stripping + client-side validate (Anthropic SDKs).
- Streaming: partial JSON is not schema-valid until the done event; do not execute tools on deltas.
- XGrammar vs llguidance: unique-per-request schemas neutralize XGrammar’s cache; TTFT rises ([SqueezeBits](https://blog.squeezebits.com/guided-decoding-performance-vllm-sglang)).

### 5.5 Reasoning-token cost and latency blowups

o1-class models **scale test-time compute**; raw CoT is hidden; summaries are shown ([o1](https://openai.com/index/learning-to-reason-with-llms/)). Prompting “think step by step” on reasoning models is **unnecessary** and can inflate visible tokens ([reasoning best practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices)). Anthropic: `budget_tokens` **rejected (400)** on Claude 4.7+/Sonnet 5; use `thinking: {type:"adaptive"}` + `effort` ([extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking); [Sonnet 5 what’s new](https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5)). Adaptive thinking is **on by default** on Sonnet 5; sampling params at non-default also 400.

Process vs outcome: PRM800K process supervision solved **78%** of a MATH subset vs weaker ORMs; best-of-N gap **widens** with N ([Let's Verify Step by Step](https://arxiv.org/abs/2305.20050); [OpenAI process supervision](https://openai.com/index/improving-mathematical-reasoning-with-process-supervision/)). Counter-result: on Omni-MATH, o3-mini (m) beats o1-mini **without longer** chains; accuracy can **fall** as chains grow; o3-mini (h) spends extra tokens even on already-solved items ([Van Hoyweghen et al. 2025](https://www.alphaxiv.org/abs/2502.15631)).

AA: GPT-5.6 Sol **max** median first-chunk **>120 s** (Bedrock) to **>200 s** (OpenAI direct) — a product-facing outage if the client HTTP timeout is 60 s ([AA providers](https://artificialanalysis.ai/leaderboards/providers)).

### 5.6 KV-cache OOM and preemption

PagedAttention avoids **internal** fragmentation but not **capacity** exhaustion: when pages run out, vLLM swaps/preempts, recomputing KV later (latency cliff) ([PagedAttention](https://arxiv.org/abs/2309.06180); [Runpod](https://www.runpod.io/articles/guides/vllm-pagedattention-continuous-batching)). MLA reduces bytes/token (DeepSeek-V3 `d_c=576`) but MoE expert-parallel + KV still OOMs on long n×batch. Disagg decode pool must have persistent KV; back-pressure must originate from **decode** free blocks, not only prefill queue ([vLLM MORI-IO](https://vllm.ai/blog/2026-04-07-moriio-kv-connector)).

### 5.7 Incidents / post-mortems

> ⚠️ Limited public data available for this dimension. Providers do not publish named production post-mortems with KV-OOM traces or tool-loop SEVs. Public substitutes: DistServe/Splitwise academic evaluations; Hao AI Lab 18-month retro (disagg ignored in 2024, default playbook in 2025: Dynamo, llm-d, SGLang, vLLM, LMCache, Mooncake) ([retro](https://haoailab.com/blogs/distserve-retro/)); Claudy Day exfil via allowlisted API ([TrueFoundry](https://www.truefoundry.com/blog/claude-code-prompt-injection)); OWASP 2026 analysis of 7,714 incidents with a “defense effect” undercounting prompt injection ([Invicti](https://www.invicti.com/blog/web-security/owasp-llm-top-10-2026-whats-new)).

## 6. Enterprise System Design Scenarios

### 6.1 Scale benchmarks (published)

| System | Claim | Source |
| --- | --- | --- |
| DistServe | 7.4× request rate **or** 12.6× tighter SLO vs colocated SOTA; >90% SLO hit | [arXiv:2401.09670](https://arxiv.org/abs/2401.09670) |
| DistServe prototype vs vLLM (blog numbers) | up to 4.48× goodput / 10.2× tighter SLO on summarization | [Hao AI Lab](https://haoailab.com/blogs/distserve/) |
| vLLM PagedAttention | 2–4× throughput vs FasterTransformer/Orca iso-latency; up to 22× vs FasterTransformer on some ShareGPT points | [arXiv:2309.06180](https://arxiv.org/abs/2309.06180) |
| FlashAttention-2 | ~2× vs FA-1; 50–73% A100 peak; 225 TFLOPs/s GPT train | [arXiv:2307.08691](https://arxiv.org/abs/2307.08691) |
| XGrammar | ≤3.5× mask-gen vs Outlines (JSON); >10× CFG; “near-zero” JSON overhead | [MLC blog](https://blog.mlc.ai/2024/11/22/achieving-efficient-flexible-portable-structured-generation-with-xgrammar) |
| vLLM XGrammar under load | up to 5× better TPOT vs prior outlines path | [vLLM struct-decode](https://vllm.ai/blog/2025-01-14-struct-decode-intro) |
| DeepSeek-V3 train | 671B/37B act; 14.8T tok; 2.788M H800-hours | [arXiv:2412.19437](https://arxiv.org/abs/2412.19437) |
| Mixtral | 47B/13B act; 32k context | [arXiv:2401.04088](https://arxiv.org/abs/2401.04088) |
| Dynamo KV-aware routing | ~2× TTFT (vendor claim) | [Dynamo README](https://github.com/ai-dynamo/dynamo) |
| Let's Verify | PRM 78% MATH subset; 800k step labels | [arXiv:2305.20050](https://arxiv.org/abs/2305.20050) |
| o1 | quality scales with train-time RL **and** test-time thinking | [OpenAI](https://openai.com/index/learning-to-reason-with-llms/) |

### 6.2 Architecture case studies

**Hosted agent (SaaS copilot)**  
Control plane: API gateway → policy (RBAC, PII redaction) → model router (Haiku/Lite vs Sonnet/Terra vs Opus/Sol by effort) → SSE to client. Data plane: provider tokenizer/transformer. Tools: least-privilege, `strict` schemas, JSON-encoded `tool_result`, Haiku injection screen. State: Responses `store=true` or LangGraph Postgres. Cache: static tool JSON + system prompt above breakpoint; `prompt_cache_key` = tenant+prompt-version. Timeouts: **>180 s** if `effort=max` (AA first-chunk). Cost cap: `max_tokens` + tool-round limit + per-tenant TPM.

**Self-host RAG + structured extract**  
Prefill pool (high-FLOP, large batch, prefix cache sticky by corpus-id) → NIXL/Mooncake → decode pool (high bandwidth, continuous batching, XGrammar on a **small schema catalog** so PDA cache hits). Chunked prefill if disagg ROI is negative (short prompts, small GPU count). Grammar compile once per schema (Anthropic-style 24h cache analog: pin compiled XGrammar objects in the StructuredOutputManager) ([vLLM anatomy](https://vllm.ai/blog/2025-09-05-anatomy-of-vllm)).

**Multi-agent coding org**  
OpenAI Agents SDK handoffs for ownership transfer; ADK 2.0 graph for deterministic CI steps (`SequentialAgent` test→lint→pr); CrewAI hierarchical only if a manager LLM is budgeted (extra tokens every delegate). Dynamo shared KV so subagents do not re-prefill identical tool catalogs ([Dynamo agentic](https://github.com/ai-dynamo/dynamo/blob/ce0cb901/docs/digest/agentic-inference/agentic-inference.md)).

**Regulated (finance)**  
Bedrock Converse + Guardrails; regional endpoints (+10%); CCU marketplace billing if using Claude Platform on AWS ([Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Structured classifier tools for compliance **before** any side-effecting tool ([Anthropic chain-safeguard example](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)). LangGraph HITL on wire/trade tools.

### 6.3 Trade-off matrix

| Decision | Choose A when | Choose B when |
| --- | --- | --- |
| Dense vs MoE | Simple TP, uniform latency | 13B–37B active quality at lower $/tok; accept expert A2A |
| GQA vs MLA | Llama/Qwen ecosystem, simpler kernels | DeepSeek-class long context; implement latent KV + RoPE-decoupled keys |
| Colocated + chunked prefill vs P/D disagg | <~8 GPUs, short prompts | Tight tail ITL, long prefill, independent TTFT/TPOT SLOs ([DistServe](https://arxiv.org/abs/2401.09670); [vLLM disagg](https://docs.vllm.ai/en/stable/features/disagg_prefill/)) |
| Prompted JSON vs constrained | Prototyping | Production parsers, tool args (`strict`) |
| XGrammar vs llguidance | Repeated schemas, long JSON | Multi-tenant unique schemas, TTFT-sensitive ([SqueezeBits](https://blog.squeezebits.com/guided-decoding-performance-vllm-sglang)) |
| Native tools vs prompted ReAct text | Any side effect | Legacy models without tool APIs |
| Parallel tools on | Independent reads | Writes, rate limits, injection amplification |
| Reasoning effort max vs medium | Hard math/code; cost/latency acceptable | Interactive UX; AA shows ~8× TTFT Opus 5 max vs medium |
| Explicit Anthropic cache vs OpenAI auto | Need 1h TTL or mixed TTLs | Prefix-stable apps on GPT-5.6 with 30m TTL |
| Gemini implicit vs explicit cache | Opportunistic savings | Must hit cache; pay **$4.50/MTok/h** on 3.1 Pro Preview |

### 6.4 Capacity planning (worked)

Target: **50 interactive extracts/s**, 4k in / 400 out, Luna-class or self-host 8B.

**Hosted Luna**: $1.28 / 1k exec → **$0.064 / s** = **~$5,530 / month** at 50 rps continuous (2.592e9 exec-ms → 4.32e6 exec/day × 30 × $0.00128). TPM: 50 × (4000+400) = 220k tok/s = **13.2M TPM** → needs OpenAI **Tier 5**-class TPM (40M on gpt-5 table) plus headroom for retries ([rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [GPT-5 TPM](https://developers.openai.com/api/docs/models/gpt-5)).

**Hosted Sol max** on the same 50 rps is not feasible: AA ~130–200 s first-chunk implies in-flight concurrency **[inferred]** ≈ 50 × 150 s = **7,500** outstanding HTTP calls and **$272/1k** if 8k thinking (§2.3C) → **$13.6/s** = **~$35k/day**.

**Self-host**: PagedAttention 2–4× vs naive; size decode GPUs by `batch × seq × layers × kv_heads × d × 2(K,V) × 2 bytes`. MLA/`d_c=576` cuts that vs MHA. Disagg ratio xPyD from DistServe’s two-phase optimizer given TTFT/TPOT SLOs ([DistServe](https://arxiv.org/abs/2401.09670)). Prefix cache: if 4k of 4.4k is static and 90% hits, prefill FLOPs drop ~90% on those tokens (provider cache **and** vLLM block hash).

**Agent fleet**: each user turn may be **N model calls** (ReAct). Budget `N × (TTFT + tokens/TPOT)`. Parallel tools cut N for independent reads but multiply downstream QPS. Cap N (e.g. 8) at the orchestrator; persist LangGraph checkpoints so retries do not re-execute irreversible tools.

### 6.5 Interview-ready control/data-plane sketch

```
Client --SSE/WSS--> API gateway (auth, quota, circuit breaker)
                 --> Policy (PII, tool RBAC, schema allowlist)
                 --> Router (model tier, cache key, KV-aware worker)
Data plane: Tokenizer --> Prefill GPU pool --> KV transfer (NIXL/Mooncake)
         --> Decode GPU pool (continuous batch + grammar bitmask + sampler)
         --> Tool/JSON parser --> (if tool_use) Tool host sandbox --> append result --> decode again
Control plane: LangGraph/ADK/Agents SDK checkpointer (Postgres), audit log, HITL queue
```

This split is what DistServe, Dynamo, vLLM disagg, and hosted Responses+Agents all instantiate at different layers ([DistServe](https://arxiv.org/abs/2401.09670); [Dynamo](https://docs.nvidia.com/dynamo/dev/design-docs/disaggregated-serving); [OpenAI tools](https://developers.openai.com/api/docs/guides/tools); [LangGraph](https://docs.langchain.com/oss/python/langgraph/durable-execution)).

## Sources

- [1] https://arxiv.org/abs/1706.03762 — Attention Is All You Need (Transformer, scaled dot-product attention)
- [2] https://arxiv.org/abs/1911.02150 — Shazeer, Fast Transformer Decoding (MQA)
- [3] https://aclanthology.org/2023.emnlp-main.298.pdf — GQA (Ainslie et al., EMNLP 2023)
- [4] https://arxiv.org/abs/2307.08691 — FlashAttention-2
- [5] https://arxiv.org/abs/2104.09864 — RoPE (Su et al.)
- [6] https://arxiv.org/abs/2108.12409 — ALiBi (Press et al.)
- [7] https://arxiv.org/abs/2309.00071 — YaRN context extension
- [8] https://huggingface.co/docs/transformers/en/internal/rope_utils — Hugging Face RoPE types
- [9] https://arxiv.org/abs/2101.03961 — Switch Transformers (MoE)
- [10] https://arxiv.org/abs/2401.04088 — Mixtral of Experts (47B/13B, top-2)
- [11] https://arxiv.org/abs/2412.19437 — DeepSeek-V3 (671B/37B, MLA, MTP, 2.788M H800-h)
- [12] https://arxiv.org/pdf/2412.19437v2 — DeepSeek-V3 PDF (MLA d_c, aux-loss-free)
- [13] https://magazine.sebastianraschka.com/p/visual-attention-variants — MHA/GQA/MLA/DSA visual guide
- [14] https://arxiv.org/abs/2309.06180 — PagedAttention / vLLM (2–4× throughput)
- [15] https://vllm.ai/blog/2025-09-05-anatomy-of-vllm — vLLM engine, continuous batching, guided decode
- [16] https://vllm.ai/blog/2025-01-14-struct-decode-intro — FSM/PDA structured decoding; 5× TPOT (XGrammar)
- [17] https://docs.vllm.ai/en/v0.20.2/features/structured_outputs/ — guided json/regex/grammar/structural_tag
- [18] https://docs.vllm.ai/en/stable/features/disagg_prefill/ — vLLM disaggregated prefill/decode
- [19] https://vllm.ai/blog/2026-04-07-moriio-kv-connector — MORI-IO RDMA read/write KV transfer
- [20] https://arxiv.org/abs/2401.09670 — DistServe (7.4× requests / 12.6× SLO)
- [21] https://haoailab.com/blogs/distserve/ — DistServe goodput blog (4.48× / 10.2×)
- [22] https://haoailab.com/blogs/distserve-retro/ — Disaggregated inference 18 months later
- [23] https://docs.nvidia.com/dynamo/dev/design-docs/disaggregated-serving — NVIDIA Dynamo P/D + NIXL
- [24] https://github.com/ai-dynamo/dynamo — Dynamo KV-aware routing (~2× TTFT), xPyD
- [25] https://github.com/ai-dynamo/dynamo/blob/ce0cb901/docs/digest/agentic-inference/agentic-inference.md — Subagent KV reuse
- [26] https://kvcache-ai.github.io/Mooncake/index.html — Mooncake KVCache-centric serving
- [27] https://github.com/mlc-ai/xgrammar/ — XGrammar CFG constrained decoding
- [28] https://blog.mlc.ai/2024/11/22/achieving-efficient-flexible-portable-structured-generation-with-xgrammar — XGrammar vs Outlines benches
- [29] https://catalyst.cs.cmu.edu/projects/xgrammar.html — XGrammar MLSys 2025 / overlap design
- [30] https://deepwiki.com/sgl-project/sglang/19.2-constrained-and-structured-output — SGLang GrammarManager, reasoner grammar
- [31] https://github.com/sgl-project/sglang/pull/15623 — Overlapped constrained decoding + Spec V2
- [32] https://docs.sglang.io/docs/advanced_features/speculative_decoding — SGLang speculative decoding flags
- [33] https://mintlify.wiki/sgl-project/sglang/concepts/radix-attention — RadixAttention prefix KV
- [34] https://developers.openai.com/api/docs/guides/structured-outputs — OpenAI Structured Outputs / JSON mode table
- [35] https://developers.openai.com/api/docs/guides/function-calling — tool_choice, parallel_tool_calls, strict
- [36] https://developers.openai.com/api/docs/guides/tools — built-in tools, tool_search (gpt-5.4+)
- [37] https://developers.openai.com/api/docs/guides/streaming-responses — SSE vs WebSocket Responses
- [38] https://developers.openai.com/api/reference/resources/responses/streaming-events/ — response.* event types
- [39] https://developers.openai.com/api/docs/guides/prompt-caching — GPT-5.6 1024 min, 1.25× write, 30m TTL
- [40] https://developers.openai.com/api/docs/pricing — GPT-5.6 price table, long-context, tools
- [41] https://openai.com/api/pricing/ — GPT-5.6 Sol/Terra/Luna list prices
- [42] https://developers.openai.com/api/docs/guides/rate-limits — tiers, 429, TPM vs RPM
- [43] https://developers.openai.com/api/docs/models/gpt-5 — example RPM/TPM by tier
- [44] https://developers.openai.com/api/docs/guides/reasoning-best-practices — no CoT prompts; store reasoning + tools
- [45] https://openai.com/index/learning-to-reason-with-llms/ — o1 train/test-time compute; hidden CoT
- [46] https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api — JSON mode vs structured; json keyword
- [47] https://developers.openai.com/api/docs/guides/agents/orchestration — handoffs vs agents-as-tools
- [48] https://openai.github.io/openai-agents-python/handoffs/ — Agents SDK handoff API
- [49] https://openai.github.io/openai-agents-js/openai/agents-core/interfaces/agentconfiguration/ — toolUseBehavior
- [50] https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works — client/server tools, agentic loop
- [51] https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling — sandbox tools; strict incompatible
- [52] https://github.com/anthropics/skills/blob/HEAD/skills/claude-api/shared/tool-use-concepts.md — JSON outputs, 24h schema cache
- [53] https://platform.claude.com/docs/en/about-claude/pricing — Claude 5/4.x prices, cache multipliers, CCU
- [54] https://platform.claude.com/docs/en/build-with-claude/prompt-caching — breakpoints, min tokens, TTL
- [55] https://platform.claude.com/docs/en/build-with-claude/extended-thinking — budget_tokens vs adaptive
- [56] https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5 — Sonnet 5 thinking/sampling 400s
- [57] https://platform.claude.com/docs/en/release-notes/overview — thinking_tokens usage field
- [58] https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks — tool_result injection defenses
- [59] https://ai.google.dev/gemini-api/docs/structured-output — Gemini JSON Schema structured outputs
- [60] https://ai.google.dev/gemini-api/docs/generate-content/function-calling — functionCall ids, Gemini 3 combo
- [61] https://ai.google.dev/gemini-api/docs/pricing — Gemini 3.x token + cache + search prices
- [62] https://ai.google.dev/gemini-api/docs/optimization — Standard/Flex/Priority/Batch tradeoffs
- [63] https://ai.google.dev/gemini-api/docs/priority-inference — Priority +75–100% price
- [64] https://ai.google.dev/gemini-api/docs/billing — Gemini spend-tier caps
- [65] https://api-docs.deepseek.com/quick_start/pricing — V4 Flash/Pro peak/off-peak + concurrency
- [66] https://api-docs.deepseek.com/guides/kv_cache — automatic disk prefix cache
- [67] https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html — Bedrock Converse toolConfig
- [68] https://aws-samples.github.io/amazon-bedrock-samples/agents-and-function-calling/function-calling/function_calling_with_converse/function_calling_with_converse/ — Bedrock parallel toolUse
- [69] https://github.com/langchain-ai/langgraph — LangGraph durable agents
- [70] https://docs.langchain.com/oss/python/langgraph/durable-execution — checkpointers vs stores
- [71] https://github.com/langchain-ai/langgraph-supervisor-py — supervisor-worker
- [72] https://adk.dev/ — Google ADK 2.0 graph runtime
- [73] https://github.com/google/adk-docs/blob/main/docs/2.0/index.md — ADK 2.0 nodes/workflows
- [74] https://github.com/google/adk-docs/blob/5331a07f/docs/agents/multi-agents.md — ADK hierarchy / AgentTool
- [75] https://docs.crewai.com/en/learn/hierarchical-process — CrewAI hierarchical process
- [76] https://docs.crewai.com/edge/en/concepts/processes — sequential vs hierarchical
- [77] https://arxiv.org/abs/2305.20050 — Let's Verify Step by Step (process vs outcome)
- [78] https://openai.com/index/improving-mathematical-reasoning-with-process-supervision/ — PRM vs ORM narrative
- [79] https://github.com/openai/prm800k — PRM800K dataset
- [80] https://aclanthology.org/2024.tacl-1.9.pdf — Lost in the Middle
- [81] https://owasp.org/www-project-top-10-for-large-language-model-applications/ — OWASP LLM Top 10 2026
- [82] https://blog.checkpoint.com/ai-security/reading-the-signals-in-the-owasp-llm-top-10-2026/ — LLM03 Excessive Agency
- [83] https://arxiv.org/abs/2302.12173 — Greshake et al. indirect prompt injection
- [84] https://artificialanalysis.ai/leaderboards/models — median TTFT / tok/s / cost-per-task
- [85] https://artificialanalysis.ai/leaderboards/providers — provider-level first-chunk (Sol max 120–209 s)
- [86] https://developers.redhat.com/articles/2025/06/03/structured-outputs-vllm-guiding-ai-responses — XGrammar vs Guidance
- [87] https://blog.squeezebits.com/guided-decoding-performance-vllm-sglang — schema-repeat vs unique-schema benches
- [88] https://www.alphaxiv.org/abs/2502.15631 — o3-mini thinks harder, not longer
- [89] https://futureagi.com/blog/evaluating-llm-structured-output-modes-2026/ — strict vs prompt JSON failure shapes
- [90] https://www.truefoundry.com/blog/claude-code-prompt-injection — Claudy Day allowlist exfil
- [91] https://platform.claude.com/cookbook/cost-optimization-cost-optimization — cache-busting timestamps
- [92] https://codelabs.developers.google.com/codelabs/production-ready-ai-with-gc/3-developing-agents/build-a-multi-agent-system-with-adk — ADK Sequential/Loop/Parallel
- [93] https://arxiv.org/abs/2201.11903 — Wei et al. Chain-of-Thought prompting (2022)

**Coverage check**: Transformers (architecture, attention variants, MoE, prefill/decode, positional encodings) — §§1.1–1.5, 3.2, 6. Reasoning (CoT, process vs outcome, thinking tokens, inference-time compute) — §§1.6, 1.10, 2.1, 2.3C, 3.1, 5.5, 6.1. Function calling (schemas, parallel, tool-choice, native vs prompted) — §§1.7–1.9, 4.2, 5.2–5.3. Structured output (JSON schema, constrained/grammar decoding, reliability) — §§1.6, 1.10, 4.4, 5.4, 6.3. Dimensions 1–6 all present; limited-data callouts in §§3.3 and 5.7.