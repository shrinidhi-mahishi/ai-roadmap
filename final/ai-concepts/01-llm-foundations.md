# LLM Foundations

## What Is This?

A Large Language Model (LLM) is a neural network that predicts the next word (technically, "token") in a sequence. It works like an extremely sophisticated autocomplete — given "The capital of France is," it predicts "Paris" because it learned patterns from billions of text documents during training.

The core architecture is called a **Transformer**. Its key innovation is **attention** — a mechanism that lets the model look at every other word in the input when deciding what to generate next. Think of it like reading a sentence where you can glance back at any earlier word to understand context. For example, in "The animal didn't cross the road because it was too tired," attention helps the model figure out that "it" refers to "the animal," not "the road."

A **token** is the basic unit the model reads and writes — roughly 3/4 of an English word. "ChatGPT is amazing" becomes 4 tokens: ["Chat", "G", "PT", " is", " amazing"]. Everything the model does — reading input, generating output, billing you — is measured in tokens.

**Temperature** controls randomness: low temperature (0.0-0.3) makes the model pick the most likely next token (deterministic, good for code), high temperature (0.7-1.0) makes it explore less likely tokens (creative, good for brainstorming). **Top-p** is similar — it limits the pool of tokens the model considers.

**Decoder-only** means the model generates one token at a time, left to right, each token conditioned on everything before it. This is how GPT-4, Claude, and Gemini all work. The alternative (encoder-decoder, used by older models like T5) encodes the full input first, then generates output — mostly obsolete for general-purpose LLMs.

## Why It Matters

Every AI application is built on top of LLMs, so understanding how they work — their strengths, limitations, and cost structure — is foundational. Knowing the difference between prefill and decode, how the KV cache works, and what structured output guarantees you get from each provider directly impacts your architecture decisions.

---

## 2. Core Concepts

Every frontier LLM in 2026 is a decoder-only Transformer, but the internal components have converged far from the 2017 original. The de facto stack is: pre-norm (RMSNorm), RoPE positional encoding, SwiGLU MLPs, KV-sharing (GQA or MLA), and bias-free layers. This convergence is partly a network effect — FlashAttention, fused RMSNorm, and fused SwiGLU kernels are heavily optimized for this combination, creating path dependence. You can't design production systems without understanding these primitives.

**The business context:** 37% of enterprises use 5+ models. Two teams building similar applications can end up with 10x different AI costs. Prices are falling 30-50% per year since 2023, but understanding token economics, context management, and failure modes is what separates a $5K/month AI system from a $50K/month one. LLM providers run at 99-99.5% uptime, which is 6-14x worse than cloud infrastructure. You must design for failure.



### 2.1 The Transformer Architecture

A Transformer is a neural network architecture that processes sequences using self-attention mechanisms. It replaced RNNs and LSTMs as the dominant architecture for language tasks because it can process entire sequences in parallel rather than one token at a time.

**Decoder-only vs Encoder-Decoder:**

Decoder-only is simpler (one stack of blocks instead of two), scales more cleanly, and treats any task as "continue this prompt." This is why GPT, Claude, Llama, and every frontier model use decoder-only.

Encoder-decoder isn't dead. Elfeki et al. (2025) report 47% lower first-token latency and 4.7x higher throughput on edge hardware. Encoders with 400M parameters outperform decoders with 1B parameters on classification and retrieval tasks.

### 2.2 Attention Mechanisms: MHA -> MQA -> GQA -> MLA

Attention is how the model decides which parts of the input to focus on when generating each token. Multi-Head Attention (MHA) was the original mechanism, but it's expensive in memory.

The evolution is one parameterized family. Changing the number of KV heads trades representational capacity against KV-cache size and decoding efficiency:


| Variant | Q heads | KV heads          | KV cache size (relative) | Use                                                                       |
| ------- | ------- | ----------------- | ------------------------ | ------------------------------------------------------------------------- |
| MHA     | h       | h                 | 1x (baseline)            | Original Transformer; highest quality, highest memory                     |
| MQA     | h       | 1                 | 1/h of MHA               | Maximum KV-cache reduction; some quality loss                             |
| GQA     | h       | g (1 < g < h)     | g/h of MHA               | Best quality-efficiency tradeoff; used by Llama 3, Mixtral                |
| MLA     | h       | compressed latent | More aggressive than GQA | DeepSeek-V2/V3/V4, GLM-5; compresses KV cache into low-rank latent vector |


**Key insight:** MLA achieves a better balance between memory efficiency and modeling capacity. Its modeling capacity even surpasses original MHA in benchmarks.

**2026 frontier status:** GLM-5.2 and Kimi K2.7 use MLA. Qwen3.5 uses a gated linear-attention hybrid. Every major open model released in 2026 is sparse.

### 2.3 Positional Encodings

Transformers have no inherent notion of token order. Positional encodings inject sequence position information.

**RoPE (Rotary Position Embeddings):** Has won mainstream adoption. It encodes positional information directly into attention by rotating Q and K vectors using sinusoidal functions. This preserves relative distance naturally and extends to longer sequences more gracefully.

**ALiBi (Attention with Linear Biases):** Adds no learned parameters. Attention scores decay linearly with relative distance. Simpler but less common in frontier models.

**YaRN (Yet another RoPE extensioN):** Piecewise NTK-by-parts plus attention temperature scaling. SOTA context extension after fine-tuning on less than 0.1% of original pretrain data. Dynamic-YaRN claimed greater than 2x extension without fine-tuning.

### 2.4 Mixture of Experts (MoE) Architecture

MoE has become the architectural paradigm for frontier LLMs. Nearly every frontier model in 2025-2026 uses MoE.

**How it works:** MoE replaces the monolithic FFN (feed-forward network) in each transformer block with N smaller "expert" FFNs in parallel, plus a lightweight router that picks the top-k experts per token. Only the FFN becomes sparse; self-attention layers remain dense.


| Model            | Total Params | Active Params | Experts                 | Router                          |
| ---------------- | ------------ | ------------- | ----------------------- | ------------------------------- |
| Mixtral 8x7B     | 47B          | 13B           | 8, top-2                | Softmax gating                  |
| DeepSeek-V3      | 671B         | 37B           | 256 fine-grained, top-8 | Auxiliary-loss-free             |
| DeepSeek-V4 Pro  | 1.6T         | 49B           | --                      | Dynamic bias routing            |
| Qwen3-235B       | 235B         | 22B           | 128, top-8              | --                              |
| Llama 4 Maverick | ~400B        | ~17B          | 128 routed + 1 shared   | Interleaved dense/MoE           |
| Mistral Large 3  | 675B         | 41B           | --                      | Deployable on single 8-GPU node |


**Critical tradeoff:** MoE saves compute, not memory. All experts must be loaded into GPU memory. This is why DeepSeek-V3 (671B total, 37B active) can run inference more cheaply than a dense 70B model, but still requires massive GPU clusters.

### 2.5 Tokenization

Tokenization is the process of converting text into integer IDs that the model can process. BPE (Byte Pair Encoding) is the dominant algorithm.

**Edge cases that matter in production:**

- **Multilingual token tax:** Non-English text costs 2-3x more tokens. Japanese and Arabic cost 3-6x.
- **BPE positional sensitivity:** "unhappy" vs "un happy" vs " unhappy" tokenize differently.
- **Unicode edge cases:** Emoji sequences can explode into dozens of tokens.
- **Model version mismatch:** Llama 2 vs Llama 3 tokenizers are incompatible. Never trust client-side token counts for billing.

**Future direction:** Meta's BLT (Byte Latent Transformer) and other token-free approaches eliminate this complexity entirely.

### 2.6 FlashAttention

FlashAttention is not an architectural variant but an IO-aware kernel optimization. It reduces HBM (high-bandwidth memory) traffic by tiling softmax computation in SRAM, achieving 2-4x speedup vs optimized baselines and linear memory instead of quadratic.

FlashAttention-2 reaches 50-73% of A100 peak FLOPs/s and up to 225 TFLOPs/s (72% MFU) end-to-end GPT training. This is why the ecosystem converged on the architecture FlashAttention optimizes.

## 3. How It Works



### 3.1 Inference Pipeline Topology

```
Input text
  -> Tokenizer (BPE: text -> token IDs)
  -> Embedding layer (token IDs -> dense vectors, shape B x S x E)
  -> + Positional encoding (RoPE rotation of Q/K)
  -> N x Transformer blocks:
       -> RMSNorm
       -> Masked self-attention (GQA/MLA) + KV cache
       -> RMSNorm
       -> SwiGLU FFN (or MoE routing to expert FFNs)
  -> Logit head (project to vocabulary size V)
  -> Softmax -> probability distribution
  -> Sampling (temperature, top-p, top-k, min-p)
  -> Output token ID
  -> Detokenizer -> text
```

**Two phases define the cost profile:**

- **Prefill (prompt processing):** Compute-bound. Processes all input tokens in parallel. Matrix multiplications dominate.
- **Decode (generation):** Memory-bandwidth-bound. Generates one token at a time autoregressively. Reads KV cache from memory repeatedly.

This asymmetry is why disaggregated serving architectures exist.

### 3.2 Serving Pipeline with Constrained Decoding

The production pipeline is more complex than the basic topology above:

1. **Chat template + tokenize:** Apply provider-specific formatting, then BPE.
2. **Prefill forward pass:** Write KV cache pages, sample first token.
3. **Decode loop:** PagedAttention over KV cache; logit processors and grammar bitmask applied after logits, before sampling.
4. **Sampler:** Apply temperature, top-p/k, penalties. Reasoning models consume extra decode steps as "thinking" tokens.
5. **Detokenize + parsers:** SSE events for OpenAI, content blocks for Anthropic, functionCall for Gemini.
6. **Constrained decoding:** Compile JSON Schema / regex / EBNF into PDA/FSM, then vocab bitmask. Mask illegal logits to negative infinity.

**XGrammar:** Compiles CFG (context-free grammar) to PDA with near-zero JSON overhead. Up to 3.5x vs Outlines on JSON schema mask generation and greater than 10x on CFG workloads.

### 3.3 Control Plane vs Data Plane

The serving infrastructure separates orchestration (control plane) from inference (data plane).

**Control plane components:**

- API gateway (auth, routing, A/B testing, rate limiting, shadowing)
- Model registry
- Autoscaler
- Scheduler
- LangGraph/ADK/Agents SDK checkpointer (Postgres)
- Audit log
- HITL (human-in-the-loop) queue

**Data plane components:**

- Inference engines (vLLM, SGLang, TensorRT-LLM)
- GPU clusters
- KV cache pools
- Continuous batching scheduler
- Tokenizer
- Prefill GPU pool
- Decode GPU pool
- Grammar bitmask and sampler
- Tool/JSON parser
- Tool host sandbox

**Interview-ready sketch:**

```
Client --SSE/WSS--> API gateway (auth, quota, circuit breaker)
                 --> Policy (PII, tool RBAC, schema allowlist)
                 --> Router (model tier, cache key, KV-aware worker)

Data plane: Tokenizer --> Prefill GPU pool --> KV transfer (NIXL/Mooncake)
         --> Decode GPU pool (continuous batch + grammar bitmask + sampler)
         --> Tool/JSON parser --> (if tool_use) Tool host sandbox 
         --> append result --> decode again

Control plane: LangGraph/ADK/Agents SDK checkpointer (Postgres), 
               audit log, HITL queue
```

Kubernetes is dominant. Approximately 66% of organizations hosting generative AI models use Kubernetes for inference.

### 3.4 Hosted APIs vs Self-Hosted

**Hosted APIs:** The provider owns tokenizer, transformer, sampler, and function-call parser. The application owns the agentic loop (ReAct, planning, tool orchestration).

**Self-hosted (vLLM/SGLang/TensorRT-LLM):**

- vLLM: HTTP/OpenAI-compat front-end + Engine Core (scheduler, PagedAttention, continuous batching) + Structured Output Manager
- NVIDIA Dynamo: Orchestration layer above vLLM/SGLang/TensorRT-LLM. PrefillRouter selects workers, NIXL moves KV GPU-to-GPU.
- Mooncake (Moonshot Kimi serving): KVCache-centric disaggregated architecture.



## 4. Key Patterns and Best Practices



### Reasoning Models (Test-Time Compute)4.1 Prompt Caching

Claude's prompt caching is a prefix match. Any byte change anywhere in the prefix invalidates everything after it. Render order: tools -> system -> messages.


| TTL                 | Write Cost       | Read Cost       | Savings      |
| ------------------- | ---------------- | --------------- | ------------ |
| 5 minutes (default) | 1.25x base input | 0.1x base input | 90% on reads |
| 1 hour              | 2.0x base input  | 0.1x base input | 90% on reads |


**Silent invalidators:**

- Timestamps in cached content
- User-specific content in system prompt prefix
- Unsorted JSON keys
- Varying tool sets

GPT-5.6 pricing for caching:


| Model         | Input | Cached input | Cache writes | Output |
| ------------- | ----- | ------------ | ------------ | ------ |
| gpt-5.6-sol   | $5.00 | $0.50        | $6.25        | $30.00 |
| gpt-5.6-terra | $2.00 | $0.20        | $2.50        | $12.00 |
| gpt-5.6-luna  | $0.20 | $0.02        | $0.25        | $1.20  |




### 4.2 Structured Outputs

Three levels of guarantees, each solving a different problem:


| Mechanism                   | Guarantee                                        | Failure shape                           |
| --------------------------- | ------------------------------------------------ | --------------------------------------- |
| Prompted JSON               | None                                             | Truncation, extra keys, markdown fences |
| JSON mode                   | Valid JSON syntax                                | Schema drift                            |
| Constrained decoding        | Every token schema-legal if generation completes | Refusals, max_tokens truncation         |
| Provider strict/json_schema | Same as constrained decode on supported subset   | 400 on illegal schema; refusal field    |


**JSON Mode** guarantees syntactically valid JSON but does not enforce your schema.

**Structured Outputs (Strict Mode)** guarantees full schema compliance through constrained decoding. JSON Schema compiled into FSM (finite state machine).

**Claude caveat:** The strict parameter is currently ignored for tool definitions. Claude makes best effort but does not guarantee schema compliance.

**Validation tooling:** Pydantic AI, Instructor (13K+ GitHub stars), Outlines (14K+ GitHub stars).

### 4.3 The Semantic Validation Gap

Structured outputs solve syntax, not semantics. You need three layers:

1. **Guardrails (policy filter):** PII detection, content moderation
2. **Schema validation (typed parse):** Pydantic/Zod/JSON Schema
3. **Business-rule validation:** Cross-field consistency, authorization scope

Example: A model might return valid JSON with `{"start_date": "2026-09-01", "end_date": "2026-08-01"}`. Schema passes, business rule (start before end) fails.

### 4.4 Multi-Model Routing

37% of enterprises use 5+ models. RouteLLM cut cost 85% on MT Bench while keeping 95% of GPT-4 Turbo quality.

**Router overhead:**

- Rule-based: less than 1ms
- Embedding-based: approximately 5ms
- Semantic: 50-100ms

**Routing pattern:**

```
User Request
  -> Classifier (rule-based or lightweight ML, <5ms)
  -> Simple queries (70-85% of traffic): GPT-4.1 Nano ($0.10/M) or Haiku ($1/M)
  -> Complex queries (15-30% of traffic): Opus ($5/M) or GPT-5.5 ($5/M)
```

Routing classification and extraction to Haiku ($1/M) instead of Sonnet ($3/M) yields 12x cost reduction.

**Platforms:** OpenRouter (500+ models), LiteLLM, Portkey, Vercel AI Gateway.

### 4.5 Quantization

Quantization reduces precision to save memory with minimal quality loss.


| Technique         | Memory Reduction   | Quality Impact              |
| ----------------- | ------------------ | --------------------------- |
| FP16 -> FP8       | 2x                 | Negligible                  |
| INT4 (AWQ/GPTQ)   | 4x                 | Minor for most tasks        |
| Google TurboQuant | KV cache to 3 bits | Zero measured accuracy loss |




### 4.6 Batch API

Flat 50% discount on both input and output tokens for all models. Results returned within 24 hours. Use for offline workloads like dataset generation, evaluation runs, and bulk classification.

### 4.7 Cost Optimization Stacking

These techniques are cumulative:

- **Batch API:** 50% off
- **Prompt caching:** up to 90% off cached input
- **Model routing:** reduces costs by up to 86%

Total savings can exceed 95% for the right workload.

## 5. System Design Considerations



### 5.1 Model Parallelism

When a model doesn't fit on one GPU, you must split it:


| Strategy                  | What is Distributed                 | Communication                 | Use When                     |
| ------------------------- | ----------------------------------- | ----------------------------- | ---------------------------- |
| Tensor Parallelism (TP)   | Individual layers split across GPUs | All-reduce per layer          | Model doesn't fit on one GPU |
| Pipeline Parallelism (PP) | Layers assigned to different GPUs   | Point-to-point between stages | Multi-node                   |
| Expert Parallelism (EP)   | MoE experts on different GPUs       | All-to-all exchange           | MoE models                   |
| Data Parallelism (DP)     | Same model replicated, data split   | Gradient sync                 | High throughput              |


**Best practice for large MoE:** DP attention + EP MoE. Attention layers are dense and benefit from data parallelism. MoE layers are sparse and need expert parallelism.

### 5.2 KV Cache Management

The KV cache stores attention keys and values for all previously generated tokens, avoiding recomputation. It's the primary memory bottleneck in decode.

**PagedAttention (vLLM, SOSP 2023):** Applies virtual memory paging to KV caches. Each sequence addresses its KV cache through a logical block table mapping to non-contiguous physical blocks in GPU memory (default block size: 16 tokens). Eliminates 60-80% memory fragmentation. Delivers 2-4x throughput vs FasterTransformer/Orca.

**Tiered storage:** vLLM takes a hierarchical approach. First checks GPU memory, then CPU memory, then configured remote backends.

**LMCache:** Turns KV cache from temporary state into reusable, persistent knowledge. Decoupled from inference engine process. Enables checkpoint/restart semantics for long generations.

**KV Cache-Aware Routing:** Google's GKE Inference Gateway routes requests to replicas already holding relevant cached state. Round-robin load balancing is actively harmful for LLM inference because it ignores cache affinity.

### 5.3 Continuous Batching

Static batching locks the GPU until the slowest sequence finishes. Continuous batching operates per forward pass. When a sequence finishes, its blocks return immediately and new requests can fill the batch.

At 128+ concurrent requests on H100, delivers 2,200-2,400 tok/s for Llama 3.3 70B FP8. Roughly 25% above default vLLM and 3-4x above naive PyTorch.

### 5.4 Disaggregated Serving

Prefill is compute-bound. Decode is memory-bandwidth-bound. Running them on the same GPU creates interference and inefficiency.

**DistServe (OSDI'24):** Assigns prefill and decode to different GPUs. 7.4x more requests or 12.6x tighter SLO vs colocated. Greater than 90% SLO attainment.

**NVIDIA Dynamo and llm-d:** Independently scale prefill and decode phases. PrefillRouter selects workers. NIXL moves KV GPU-to-GPU.

### 5.5 Speculative Decoding

A small draft model proposes k tokens cheaply. The large target model verifies all k tokens in a single forward pass. Delivers 2-3x decode speedup with no quality loss when draft acceptance rate is high.

Best for tasks where a smaller model often predicts correctly (code completion, translation).

### 5.6 Inference Engine Selection (2026)


| Engine         | Strength                        | Best For                  |
| -------------- | ------------------------------- | ------------------------- |
| vLLM (v0.17.1) | Broadest hardware support       | General production        |
| SGLang         | Shared prefix optimization      | Chatbots, RAG, multi-turn |
| TensorRT-LLM   | Maximum single-model throughput | Long-term single-model    |




### 5.7 Resilience Architecture

LLM providers run at 99-99.5% uptime, which is 6-14x worse than cloud infrastructure. You must design for failure.

```
Request Queue (dual TPM/RPM limits)
  -> Circuit Breaker (error-rate and cost-threshold triggers)
  -> Gateway (exponential backoff with full jitter)
  -> Primary Provider
  -> [On 429/5xx/timeout] Secondary Provider failover
```

**Exponential backoff with full jitter:** Start 1s, double each retry, cap at 30-60s, max 3-5 attempts. Full jitter adds random offset to prevent thundering herd.

Multi-provider adoption grew from 23% to 40% of organizations between 2024 and 2026.

### 5.8 Orchestration Topologies

How you chain LLM calls and tool executions:

- **ReAct loop:** Model emits thought + tool call; host executes; result appended; repeat
- **Supervisor-Worker:** Supervisor LLM routes via tool-shaped handoffs
- **Plan-and-Execute:** Planner emits step list/DAG; executors run tools
- **DAG / graph runtime:** Explicit edges, reducers, interrupts (LangGraph StateGraph)



### 5.9 Durable Execution

Agentic workflows can take minutes or hours. Durable execution survives crashes and enables human-in-the-loop.

- **LangGraph:** Checkpointer saves at every superstep. PostgresSaver/SqliteSaver.
- **OpenAI Responses:** store=true + previous_response_id keeps reasoning adjacent to function calls.
- **Anthropic server-tool loop:** pause_turn -> resend conversation.



## 6. Code Examples



### 6.1 Structured Output with Pydantic

```python
from openai import OpenAI
from pydantic import BaseModel

class ExtractedData(BaseModel):
    company_name: str
    revenue_usd: float
    year: int

client = OpenAI()
completion = client.beta.chat.completions.parse(
    model="gpt-4.1",
    messages=[
        {"role": "system", "content": "Extract financial data."},
        {"role": "user", "content": "Apple reported $394.3B revenue in 2023."}
    ],
    response_format=ExtractedData,
)

data = completion.choices[0].message.parsed
print(data.company_name)  # "Apple"
print(data.revenue_usd)   # 394300000000.0
```



### 6.2 Multi-Model Routing

```python
def route_request(prompt: str) -> str:
    # Simple heuristic: check length and complexity
    if len(prompt) < 100 and "explain" not in prompt.lower():
        return "gpt-4.1-nano"  # $0.10/M input
    elif "code" in prompt.lower() or "debug" in prompt.lower():
        return "claude-opus-5"  # $5/M input
    else:
        return "claude-sonnet-5"  # $3/M input

model = route_request("What is 2+2?")
# Returns "gpt-4.1-nano" - saves 30x vs Opus
```



### 6.3 Exponential Backoff with Full Jitter

```python
import time
import random

def call_llm_with_retry(prompt: str, max_retries: int = 5):
    base_delay = 1.0
    max_delay = 60.0
    
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "user", "content": prompt}]
            )
        except RateLimitError as e:
            if attempt == max_retries - 1:
                raise
            
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay)
            time.sleep(jitter)
```



### 6.4 Prompt Caching with Claude

```python
import anthropic

client = anthropic.Anthropic()

# System prompt and tools will be cached
response = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": "You are a financial analyst...",  # Large system prompt
            "cache_control": {"type": "ephemeral"}  # Cache this
        }
    ],
    messages=[
        {"role": "user", "content": "What was Apple's revenue in Q3?"}
    ]
)

# First call: writes to cache (1.25x cost)
# Next calls within 5 min: read from cache (0.1x cost, 90% savings)
```



### 6.5 Circuit Breaker Pattern

```python
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open
    
    def call(self, func, *args, **kwargs):
        if self.state == "open":
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "half-open"
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = func(*args, **kwargs)
            if self.state == "half-open":
                self.state = "closed"
                self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = "open"
            raise
```



## 7. Common Pitfalls and Failure Modes



### 7.1 Context Window Overflow and Context Rot

A 200K context window that loses instruction fidelity between 60-80% fill is effectively a 140K-160K context window. Multi-turn degradation: Performance drops 39% on average in multi-turn conversations.

**Lost in the Middle:** U-shaped retrieval accuracy. Content at the beginning and end of long context outperforms the middle. Ms-PoE reports up to +3.8 average on Zero-SCROLLS via plug-and-play multi-scale RoPE rescaling.

**Mitigation:** Claude's compaction feature auto-summarizes earlier context when approaching a trigger threshold (default: 150K tokens). Use sliding windows or explicit summarization.

### 7.2 Hallucination Patterns

82% of enterprise teams report hallucination as significant. Production hallucination is:

- Invisible to reviewers (confident tone)
- Inconsistent (correct 95%, hallucinated 5%)
- Propagates downstream in multi-agent systems

**Mitigation:** Ground in retrieved context, use structured outputs, validate against source documents, enable citations.

### 7.3 Function Call Schema Violations

AI agents attempting simple CRM tasks failed up to 75% of the time. Multi-agent systems show failure rates between 41% and 86.7%.

Common violations:

- Missing required fields
- Wrong types (string instead of integer)
- Extra fields not in schema
- Nested object mismatches

**Mitigation:** Use constrained decoding (Structured Outputs, Instructor), validate with Pydantic, retry with error feedback.

### 7.4 Cascading Failures in Tool Chaining

Seven dominant failure modes:

1. **Silent data corruption:** Tool returns partial result, next tool treats as complete
2. **Context loss:** Information discarded between steps
3. **Cascading hallucination:** Early hallucination feeds into later steps
4. **Tool misuse:** Wrong tool selected or wrong parameters
5. **Timeout cascade:** First timeout triggers retries, amplifying load
6. **Error swallowing:** Exception caught but not propagated
7. **Tool poisoning:** Attacker-controlled tool output

**Mitigation:** Validate tool outputs, explicit error handling, checkpointing, end-to-end validation.

### 7.5 Rate Limit Cascades

Three retries at each layer of a five-service call chain: 3^5 = 243 backend calls. A team's API spend climbed from $127/week to $47,000/week from an unchecked agent loop.

**Mitigation:** Exponential backoff with full jitter. Circuit breakers. TPM/RPM tracking. Cost-threshold alarms.

### 7.6 Temperature/Sampling Pitfalls

- **Temperature and top-p are coupled, not independent.** Setting both can produce unexpected distributions.
- **Temperature 0 is not deterministic** due to GPU floating-point math.
- **Claude 4.x rejects simultaneous temperature and top_p** with 400 error.
- **For agents:** Temperature 0 (greedy) is the usual right answer. You want consistency.



### 7.7 The "Silent 200 OK" Problem

The worst failures arrive with a 200 status code and a confident tone. The model confidently returns invalid data, and your pipeline processes it as valid.

**Mitigation:** Semantic validation (section 4.3), business rule checks, end-to-end tests.

### 7.8 Tokenizer Edge Cases

Examples that break production systems:

- Japanese text: "こんにちは世界" (Hello World) tokenizes to 10+ tokens vs 2 for "Hello World"
- Emoji: "👨‍👩‍👧‍👦" (family) can become 7+ tokens
- Whitespace: " hello" vs "hello" are different tokens
- Model mismatch: Llama 2 and Llama 3 tokenizers produce different counts for the same text

**Mitigation:** Never trust client-side token counts for billing. Always use provider's tokenizer or API-returned usage.

### 7.9 Reasoning Model Latency Blowup

GPT-5.6 Sol max median first-chunk greater than 120s (Bedrock) to greater than 200s (OpenAI direct). For interactive UX, this is unusable.

Process supervision (PRM800K) solved 78% of MATH subset vs weaker outcome reward models. But o3-mini beats o1-mini without longer chains. Accuracy can fall as chains grow due to error accumulation.

**Mitigation:** Use reasoning models for hard problems only. Route simple queries to fast models. Set max reasoning effort to medium for interactive use (approximately 8x TTFT difference vs max).

## 8. Interview Questions and Answers



### Q1: Why did the industry converge on decoder-only Transformers?

Decoder-only is simpler than encoder-decoder (one stack instead of two), scales more cleanly, and treats every task uniformly as "continue this prompt." This unified interface makes it easier to do few-shot learning and instruction following. The causal mask also makes sense for generation tasks.

That said, encoder-decoder isn't obsolete. On edge hardware, encoder-decoder can deliver 47% lower first-token latency and 4.7x higher throughput. For classification and retrieval, smaller encoders outperform larger decoders.

### Q2: Explain the difference between MHA, MQA, GQA, and MLA.

They're all variations of multi-head attention that trade model quality for memory efficiency. MHA is the baseline with h query heads and h key-value heads. MQA collapses to 1 KV head, saving maximum memory but losing some quality. GQA is the sweet spot with g KV heads (1 < g < h), used by Llama 3 and Mixtral. MLA goes further by compressing KV cache into a low-rank latent vector, used by DeepSeek-V3 and V4. MLA actually exceeds MHA quality in benchmarks while being more memory-efficient than GQA.

### Q3: What's the difference between prefill and decode?

Prefill processes the entire prompt in parallel and is compute-bound. It generates the KV cache and produces the first token. Decode generates one token at a time autoregressively and is memory-bandwidth-bound because it repeatedly reads the KV cache.

This asymmetry is why disaggregated serving exists. DistServe assigns prefill and decode to different GPUs, eliminating interference and delivering 7.4x more requests or 12.6x tighter SLO.

### Q4: How does MoE save compute but not memory?

MoE replaces the dense FFN in each transformer block with N smaller expert FFNs and a router that activates only top-k experts per token. So if you have 256 experts but only activate 8, you do 8/256 of the FFN compute.

However, all 256 experts must be loaded into GPU memory because you don't know in advance which ones the router will pick. This is why DeepSeek-V3 (671B total params, 37B active) is cheaper to run than a dense 70B but still requires a large GPU cluster.

### Q5: Walk me through how prompt caching works in Claude.

Claude uses prefix matching. The cache key is the exact byte sequence. Tools render first, then system prompt, then messages. If any byte changes anywhere in the prefix, everything after it is invalidated.

Write cost is 1.25x base input (5-min TTL) or 2x (1-hour TTL). Read cost is 0.1x base input, so 90% savings on cache hits. Silent killers: timestamps in cached content, user-specific data in system prompt prefix, unsorted JSON keys, varying tool definitions.

### Q6: How do you design a cost-optimized LLM system?

Three layers: routing, caching, and batching. Route 70-85% of simple queries to cheap models (Nano at $0.10/M, Haiku at $1/M). Route complex queries to expensive models (Opus at $5/M). Use prompt caching for repeated prefixes (90% savings). Use Batch API for offline workloads (50% discount). Stack all three and you can achieve 95%+ cost reduction vs naive "use Opus for everything."

Two teams building similar apps can have 10x different costs. The difference is routing.

### Q7: What's the semantic validation gap and why does it matter?

Structured outputs solve syntax, not semantics. A model might return perfectly valid JSON with `{"start_date": "2026-09-01", "end_date": "2026-08-01"}`. Your schema validator passes it, but the business rule (start before end) fails.

You need three layers: guardrails (PII, content moderation), schema validation (Pydantic/Zod), and business-rule validation (cross-field checks, authorization). Most teams stop at layer 2 and wonder why invalid data gets through.

### Q8: How does PagedAttention work?

PagedAttention applies virtual memory paging to KV caches. Instead of allocating contiguous memory for each sequence, it divides the cache into fixed-size blocks (default 16 tokens) and uses a logical block table to map to non-contiguous physical blocks in GPU memory.

This eliminates 60-80% of memory fragmentation and delivers 2-4x throughput vs naive implementations. It's the key innovation behind vLLM.

### Q9: Why is round-robin load balancing harmful for LLM inference?

Because it ignores KV cache affinity. If user A's first request goes to GPU 1 and builds a KV cache, their second request should also go to GPU 1 to reuse that cache. Round-robin sends it to GPU 2, which has no cache, so you recompute everything.

Google's GKE Inference Gateway does KV cache-aware routing. This is especially important for chatbots and multi-turn workflows.

### Q10: What's the "Silent 200 OK" problem?

The worst failures arrive with a 200 status code and a confident tone. The model confidently returns invalid data (hallucinated facts, schema violations, wrong tool calls), and your pipeline processes it as valid because the HTTP request succeeded.

You can't rely on status codes. You need semantic validation: business rule checks, source document verification, end-to-end tests.

### Q11: Explain exponential backoff with full jitter.

Start with 1s delay. On each retry, double the delay (1s, 2s, 4s, 8s, ...) but cap at 30-60s. Max 3-5 attempts. Full jitter adds a random offset between 0 and the delay to prevent thundering herd.

Why jitter? Without it, 1000 clients that get rate-limited simultaneously will all retry at exactly the same time, creating a spike that triggers another rate limit.

### Q12: How do you choose between GQA and MLA?

GQA if you're in the Llama/Qwen ecosystem and want a proven, widely-supported architecture. MLA if you need DeepSeek-class long context and are willing to adopt a newer approach. MLA has better memory efficiency and can exceed MHA quality, but GQA has broader tooling support.

For most production systems in 2026, GQA is the safe choice. MLA is the frontier.

### Q13: What's the difference between JSON Mode and Structured Outputs?

JSON Mode guarantees syntactically valid JSON but doesn't enforce your schema. The model might return `{"foo": 123}` when you wanted `{"bar": "text"}`.

Structured Outputs (Strict Mode) compiles your JSON Schema into a finite state machine and uses constrained decoding to guarantee every token is schema-legal. If generation completes, it will match your schema exactly.

### Q14: How does speculative decoding work?

A small, fast draft model proposes k tokens (typically 4-8). The large target model verifies all k tokens in a single forward pass. If the draft is correct, you get k tokens for the cost of approximately 1. If wrong, you reject and fall back to standard decoding.

Delivers 2-3x decode speedup with zero quality loss when the draft acceptance rate is high. Best for code completion, translation, and other tasks where a smaller model often predicts correctly.

### Q15: What are the seven failure modes in tool chaining?

1. Silent data corruption: Tool returns partial result, next tool treats as complete
2. Context loss: Information discarded between steps
3. Cascading hallucination: Early hallucination feeds into later steps
4. Tool misuse: Wrong tool or wrong parameters
5. Timeout cascade: First timeout triggers retries, amplifying load
6. Error swallowing: Exception caught but not propagated
7. Tool poisoning: Attacker-controlled tool output

Mitigation: Validate tool outputs, explicit error handling, checkpointing, end-to-end validation.

## 9. Key Numbers to Memorize



### 9.1 Model Pricing (August 2026, $/1M tokens)

**Frontier:**

- Claude Opus 5: $5 in / $25 out
- Claude Sonnet 5: $3 in / $15 out (intro: $2 / $10)
- Claude Haiku 4.5: $1 in / $5 out
- GPT-5.5: $5 in / $30 out
- GPT-4.1: $5 in / $15 out
- o3 (reasoning): $15 in / $60 out

**Mid-Tier:**

- GPT-4.1 Mini: $0.40 in / $1.60 out
- Gemini 2.5 Flash: $0.15 in / $0.60 out

**Budget:**

- GPT-4.1 Nano: $0.10 in / $0.40 out
- DeepSeek V3: $0.14 in / $0.28 out

**Prompt Caching:**

- 5-min TTL: 1.25x write, 0.1x read (90% savings)
- 1-hour TTL: 2.0x write, 0.1x read (90% savings)

**Batch API:** 50% off input and output

### 9.2 Latency Benchmarks

**TTFT (Time to First Token):**

- Frontier P50: 0.85-1.4s
- Frontier P95: 1.6-2.4s
- Mid-Tier P50: 250-350ms
- Speed leaders: sub-300ms (Gemini 2.5 Flash: 0.18s)

**TPS (Tokens Per Second):**

- Frontier: 50-100
- Mid-Tier: 100-200
- Speed leaders: 480 (Groq), 841 (Mercury 2)

**UX thresholds:**

- 50 TPS: feels slow
- 100 TPS: feels normal
- 200+ TPS: feels instant
- 300+ TPS: bottleneck shifts to renderer



### 9.3 Context Windows

All Claude models: 1M tokens, no surcharge

- GPT-5.6 Sol: 2x pricing above 272K
- Gemini 3.1 Pro: 2x pricing above 200K



### 9.4 Scale Benchmarks

- PagedAttention: 2-4x throughput vs FasterTransformer/Orca
- FlashAttention-2: 50-73% A100 peak FLOPs/s
- Continuous batching: 2,200-2,400 tok/s for Llama 3.3 70B FP8 on H100 (25% above default vLLM, 3-4x above naive PyTorch)
- DistServe: 7.4x more requests or 12.6x tighter SLO vs colocated
- XGrammar: <=3.5x mask-gen vs Outlines, >10x on CFG



### 9.5 Failure Rates

- Hallucination: 82% of enterprise teams report as significant
- Agent task failure: 41-86.7% in multi-agent systems
- Simple CRM tasks: up to 75% failure
- LLM provider uptime: 99-99.5% (6-14x worse than cloud)
- Prompt injection attack success: 84% in agentic systems
- Multi-turn performance drop: 39% average



### 9.6 Cost Optimization

- Model routing: up to 86% cost reduction
- Prompt caching: 90% on cache hits
- Batch API: 50% discount
- Routing Haiku vs Sonnet: 12x cost reduction
- Total stacked savings: 95%+ achievable



### 9.7 Adoption Stats

- Multi-provider orgs: 23% (2024) -> 40% (2026)
- 5+ models: 37% of enterprises
- Kubernetes for inference: ~66% of orgs
- Prompt injection defenses deployed: only 34.7%



## 10. Quick Reference



### Trade-off Matrix


| Decision                            | Choose A when              | Choose B when                         |
| ----------------------------------- | -------------------------- | ------------------------------------- |
| Dense vs MoE                        | Simple TP, uniform latency | 13B-37B active quality at lower $/tok |
| GQA vs MLA                          | Llama/Qwen ecosystem       | DeepSeek-class long context           |
| Colocated vs P/D disagg             | <~8 GPUs, short prompts    | Tight tail ITL, long prefill          |
| Prompted JSON vs constrained        | Prototyping                | Production parsers                    |
| Native tools vs prompted ReAct text | Any side effect            | Legacy models                         |
| Reasoning effort max vs medium      | Hard math/code             | Interactive UX (~8x TTFT difference)  |




### Inference Engine Selection


| Engine         | Strength                        | Best For                  |
| -------------- | ------------------------------- | ------------------------- |
| vLLM (v0.17.1) | Broadest hardware support       | General production        |
| SGLang         | Shared prefix optimization      | Chatbots, RAG, multi-turn |
| TensorRT-LLM   | Maximum single-model throughput | Long-term single-model    |




### Model Parallelism Quick Reference


| Strategy                  | Use When                     | Communication Overhead              |
| ------------------------- | ---------------------------- | ----------------------------------- |
| Tensor Parallelism (TP)   | Model doesn't fit on one GPU | All-reduce per layer (high)         |
| Pipeline Parallelism (PP) | Multi-node                   | Point-to-point between stages (low) |
| Expert Parallelism (EP)   | MoE models                   | All-to-all exchange (medium)        |
| Data Parallelism (DP)     | High throughput              | Gradient sync (training only)       |




### Attention Variant Selection


| Variant | KV Cache Size            | Quality     | Use                                    |
| ------- | ------------------------ | ----------- | -------------------------------------- |
| MHA     | 1x (baseline)            | Highest     | Original Transformer                   |
| MQA     | 1/h of MHA               | Lower       | Maximum KV reduction                   |
| GQA     | g/h of MHA               | High        | Production standard (Llama 3, Mixtral) |
| MLA     | More aggressive than GQA | Exceeds MHA | Frontier (DeepSeek, Kimi)              |




### Security Checklist

- [ ] Treat all system prompts as extractable
- [ ] Use constrained decoding for production
- [ ] Implement three-layer validation (guardrails, schema, business rules)
- [ ] Add circuit breakers and exponential backoff
- [ ] Enable audit logging for SOC2 compliance
- [ ] Deploy multi-provider failover
- [ ] Set cost-threshold alarms
- [ ] Validate tool outputs before chaining
- [ ] Never trust client-side token counts for billing
- [ ] Use KV cache-aware routing, not round-robin



### Capacity Planning Example

**Target:** 50 interactive extracts/s, 4k in / 400 out, Luna-class

**Hosted Luna:**

- Cost: $1.28/1k execs = ~$5,530/month continuous
- TPM needed: 50 x 4400 = 220k tok/s = 13.2M TPM
- Requires Tier 5 quota

**Hosted Sol at same 50 rps:**

- Not feasible (150s first-chunk = 7,500 outstanding HTTP calls)



### Cost Worked Examples

**A. Deterministic extract (4k in / 400 out):**

- GPT-5.6 Luna: $1.28 / 1k exec
- Sonnet 5: $12.00 / 1k exec
- DeepSeek V4 Flash off-peak: $1.14 / 1k exec

**B. Agent turn (20k system with 90% cache hit + 1k new + 800 out):**

- Anthropic Sonnet 5 steady-state: $14.00 / 1k turns

**C. Reasoning blowup (4k in, 8k thinking + 400 answer):**

- GPT-5.6 Sol: $272.00 / 1k (16x non-reasoning)
- Gemini 3.6 Flash: $69.00 / 1k



### OWASP Top 10 for LLMs (2026)

1. **LLM01:** Prompt Injection (remains #1)
2. **LLM02:** Insecure Output Handling
3. **LLM03:** Excessive Agency (largest rank jump)
4. **LLM04:** Training Data Poisoning
5. **LLM05:** Supply Chain Vulnerabilities
6. **LLM06:** Sensitive Information Disclosure
7. **LLM07:** System Prompt Leakage
8. **LLM08:** Hidden Context Exposure
9. **LLM09:** Misinformation
10. **LLM10:** Model Theft

**Agentic-specific (ASI):**

- **ASI01:** Goal Hijack
- **ASI02:** Tool Misuse
- **ASI03:** Identity Abuse

