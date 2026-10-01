# 07. Prompt Engineering

**Sub-areas covered**: Prompt compilation pipeline (template versioning, variable injection, token budget allocation, cache prefix alignment), message role semantics (system/user/assistant) with model-specific behavioral differences, attention economics and the 3,000-token degradation threshold, prompt management infrastructure (Braintrust, PromptLayer, Confident AI, Langfuse, Portkey -- plus OpenAI's deprecation of reusable prompt objects), token economics per technique (zero-shot through Tree-of-Thought with empirical cost multipliers and accuracy data), prompt caching economics across providers (Anthropic 90% read discount, OpenAI 50% automatic, multi-tier semantic+prefix architecture), prompt drift detection (embedding-based, statistical PSI/KL-divergence, LLM-as-Judge, CI/CD eval), prompt injection attack taxonomy (direct, indirect, multi-hop -- OWASP LLM01, CVE-2025-32711 EchoLeak, 78.6% success across 200 attempts), layered defense framework (architectural prevention, runtime detection, governance -- reducing attack success from 73.2% to 8.7%), structured output reliability tiers (prompt-only 5-20% failure vs. constrained decoding ~0%), prompt chaining orchestration patterns (sequential, evaluator-optimizer, orchestrator-worker, parallel synthesis -- +15.6% accuracy vs. monolithic), accuracy compounding across chain steps (95%^10 = 60%), production failure modes (brittleness across model versions, token overflow, instruction-following degradation, tool loops), and two enterprise system-design scenarios with trade-off matrices

---

## 1. System Topology & Data Flow

A production prompt engineering system spans five cooperating layers: a **prompt registry** (control plane) managing versioned, immutable prompt artifacts with environment-based deployment; a **compilation pipeline** (data plane) assembling runtime prompts from templates, variables, retrieved documents, and conversation history; a **cache layer** eliminating redundant inference through semantic and prefix caching; an **LLM gateway** handling model routing, fallbacks, rate limiting, and circuit breaking; and a **telemetry layer** tracking drift, cost, latency, and compliance.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           CONTROL PLANE                                      │
│                                                                              │
│  ┌─────────────────────┐   ┌──────────────────┐   ┌──────────────────────┐  │
│  │ Prompt Registry      │   │ Schema Registry   │   │ Eval Suite           │  │
│  │ (immutable versions, │──▶│ (Pydantic/Zod     │──▶│ (fixed eval set,     │  │
│  │  environment labels: │   │  schemas defining  │   │  runs on every       │  │
│  │  dev/staging/prod,   │   │  input/output      │   │  version change,     │  │
│  │  rollback = re-label │   │  contracts per     │   │  multi-metric:       │  │
│  │  to prior version)   │   │  prompt version)   │   │  accuracy, format,   │  │
│  │                      │   │                    │   │  latency, cost,      │  │
│  │ Git-based (Confident │   │ Schema-first dev:  │   │  safety)             │  │
│  │ AI) or platform-     │   │ define schema ->   │   │                      │  │
│  │ managed (Braintrust, │   │ build prompt ->    │   │ Gate: fail pipeline  │  │
│  │ PromptLayer)         │   │ version artifact   │   │ when scores drop     │  │
│  └─────────┬───────────┘   └────────┬─────────┘   └──────────┬───────────┘  │
│            │ fetch by env            │ validate                │ pass/fail    │
└────────────┼─────────────────────────┼────────────────────────┼──────────────┘
             │                         │                        │
┌────────────▼─────────────────────────▼────────────────────────▼──────────────┐
│                     DATA PLANE  (PROMPT COMPILATION PIPELINE)                 │
│                                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │ Template      │  │ Variable     │  │ Token Budget │  │ Role Assembly   │  │
│  │ Resolution    │  │ Injection    │  │ Check        │  │ & Cache Prefix  │  │
│  │               │  │              │  │              │  │ Alignment       │  │
│  │ Load versioned│─▶│ User data,   │─▶│ sys + inst + │─▶│ Static content  │  │
│  │ template from │  │ RAG docs,    │  │ few-shot +   │  │ first (system,  │  │
│  │ registry by   │  │ tool defs,   │  │ docs + hist  │  │ few-shot, tool  │  │
│  │ environment   │  │ conversation │  │ + user + out │  │ defs) -> var    │  │
│  │ label         │  │ history      │  │ < ctx window │  │ content last    │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────┬────────┘  │
└───────────────────────────────────────────────────────────────────┼──────────┘
                                                                    │
┌───────────────────────────────────────────────────────────────────▼──────────┐
│                     CACHE LAYER  (MULTI-TIER)                                │
│                                                                              │
│  ┌──────────────────────┐  ┌───────────────────────┐  ┌──────────────────┐  │
│  │ Semantic Cache        │  │ Prefix Cache           │  │ Full Inference   │  │
│  │ (embedding similarity │─▶│ (exact prefix match,   │─▶│ (no cache hit,   │  │
│  │  ~31% of queries,     │  │  Anthropic: 90% off,   │  │  full-cost API   │  │
│  │  100% cost savings    │  │  OpenAI: 50% off,      │  │  call)           │  │
│  │  on hit)              │  │  min 1,024 token       │  │                  │  │
│  │                       │  │  prefix)               │  │                  │  │
│  └──────────────────────┘  └───────────────────────┘  └────────┬─────────┘  │
└───────────────────────────────────────────────────────────────────┼──────────┘
                                                                    │
┌───────────────────────────────────────────────────────────────────▼──────────┐
│                     LLM GATEWAY                                              │
│                                                                              │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Model Router  │  │ Circuit       │  │ Output       │  │ Fallback       │  │
│  │ (select model │  │ Breaker       │  │ Validator    │  │ Chain          │  │
│  │  variant,     │  │ (per-provider │  │ (constrained │  │ (primary ->    │  │
│  │  model-       │  │  CLOSED ->    │  │  decoding,   │  │  secondary ->  │  │
│  │  specific     │  │  OPEN ->      │  │  Pydantic    │  │  degraded      │  │
│  │  prompt       │  │  HALF-OPEN)   │  │  validation, │  │  response)     │  │
│  │  variant)     │  │               │  │  LLM-as-     │  │                │  │
│  │               │  │               │  │  Critic)     │  │                │  │
│  └──────────────┘  └───────────────┘  └──────────────┘  └────────────────┘  │
└───────────────────────────────────────────────────────────────────┬──────────┘
                                                                    │
┌───────────────────────────────────────────────────────────────────▼──────────┐
│                     TELEMETRY / OBSERVABILITY                                │
│                                                                              │
│  Prompt drift monitor (embedding + PSI + LLM-as-Judge)  |  per-technique   │
│  cost tracker ($/call by technique)  |  cache hit rate dashboard  |         │
│  p50/p95/p99 TTFT + total latency  |  accuracy vs. baseline  |  prompt     │
│  version audit trail  |  injection attempt log  |  model migration          │
│  comparison reports  |  compliance mapping (OWASP, EU AI Act, ISO 42001)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A developer or CI/CD pipeline commits a prompt change to the registry, triggering the eval suite against a fixed set of real production inputs. The pipeline gates deployment: scores must meet or exceed baseline thresholds across accuracy, format compliance, latency, cost, and safety metrics. (2) At runtime, the compilation pipeline loads the prompt template by environment label (dev/staging/prod), injects variable content (user data, RAG-retrieved documents, tool definitions, conversation history), and performs token budget accounting: system prompt + instructions + few-shot examples + retrieved documents + history + user message + expected output must fit within the context window. If budget is exceeded, dynamic example selection trims few-shot examples, conversation history is compressed, or retrieved chunks are relevance-reranked and pruned. (3) Role assembly orders content for cache efficiency: static content (system instructions, few-shot examples, tool definitions) occupies the prefix, variable content (user messages, query-specific data) goes last. This ordering enables prompt caching -- up to 90% cost reduction on Anthropic, 50% on OpenAI. A single changed character anywhere in the cached prefix causes a complete miss, so stateless architectures with identical system prompts across users maximize benefit. (4) The assembled prompt passes through the multi-tier cache: semantic cache checks embedding similarity (~31% of queries match), then prefix cache checks exact prefix match, then falls through to full inference. (5) The LLM gateway selects the model-specific prompt variant (different models respond differently to identical prompts), applies circuit breaker logic, and dispatches the call. (6) The response passes through output validation: constrained decoding ensures schema compliance at generation time (~0% syntactic failure rate), Pydantic validation catches semantic errors with automatic retry, and optionally an LLM-as-Critic validates content quality (+21% detection precision over input-layer filtering alone). (7) The telemetry layer records prompt version, technique, model, token counts, latency, cost, cache hit/miss, and output quality scores for drift monitoring and compliance audit.

---

## 2. Core Mechanics & Algorithms

### 2.1 Prompt Compilation as a Finite-State Pipeline

A production prompt is a compiled artifact, not a string. The compilation pipeline is a deterministic state machine:

```
┌──────────┐    ┌───────────┐    ┌────────────┐    ┌───────────┐    ┌──────────┐
│ TEMPLATE │───▶│ INJECTED  │───▶│ BUDGET     │───▶│ ROLE      │───▶│ CACHE    │
│ RESOLVED │    │           │    │ CHECKED    │    │ ASSEMBLED │    │ ALIGNED  │
└──────────┘    └───────────┘    └────────────┘    └───────────┘    └──────────┘
     │               │               │                  │               │
 Load from      Substitute       Verify total       Assign sys/      Order static
 registry by    user data,       tokens < ctx       user/asst        prefix first,
 env label      RAG docs,        window minus       roles per        variable
                tool defs,       expected out       Anthropic        content last
                conv history                        10-part
                                                    framework
```

**State transitions and invariants:**

- **TEMPLATE_RESOLVED -> INJECTED**: All template variables must resolve. Missing variables fail loudly (no silent empty-string substitution). Schema validation (Pydantic/Zod) enforces type and format constraints on injected values.
- **INJECTED -> BUDGET_CHECKED**: Token count of the fully-injected prompt + expected output length must be strictly less than the model's context window. If exceeded, the pipeline enters a **budget recovery subroutine**: (a) trim few-shot examples by relevance to current query, (b) compress/summarize conversation history, (c) rerank and prune retrieved documents, (d) if still over budget, reject the request with a structured error rather than silently truncating.
- **BUDGET_CHECKED -> ROLE_ASSEMBLED**: Content is assigned to system/user/assistant roles following Anthropic's 10-part production framework: (1) role definition, (2) communication style/guardrails, (3) reference material, (4) step-by-step instructions + hard rules, (5) sample outputs, (6) context/background, (7) retrieved documents, (8) conversation history, (9) output format specification, (10) verification/self-check instructions.
- **ROLE_ASSEMBLED -> CACHE_ALIGNED**: Static content (system instructions, few-shot examples, tool definitions) is moved to the prefix. Variable content (user messages, query-specific data) is appended last. This ordering is an economic optimization, not a semantic one -- it maximizes prefix cache hit rate.

**Complexity**: Template resolution is O(V) where V is the number of variables. Token counting is O(T) where T is total tokens. Budget recovery (reranking + pruning) is O(D log D) where D is the number of retrieved document chunks. The entire pipeline is O(T + D log D) per request.

### 2.2 Message Role Semantics and Model-Specific Behavior

The system/user/assistant tri-role structure is not cosmetic. Each role carries distinct caching, attention, and behavioral implications:

```
┌───────────────────────────────────────────────────────────────────────────┐
│ Role      │ Content Type           │ Cache Behavior  │ Attention Weight  │
├───────────┼────────────────────────┼─────────────────┼───────────────────┤
│ System    │ Persona, tool defs,    │ Cacheable       │ High (beginning-  │
│           │ guardrails, behavioral │ (static prefix) │ of-context bias)  │
│           │ constraints            │                 │                   │
├───────────┼────────────────────────┼─────────────────┼───────────────────┤
│ User      │ Task instructions,     │ Variable        │ High (end-of-     │
│           │ input data, queries    │ (low cache hit) │ context bias)     │
├───────────┼────────────────────────┼─────────────────┼───────────────────┤
│ Assistant │ Pre-filled responses,  │ N/A (output)    │ Format-steering   │
│           │ format steering,       │                 │ anchor            │
│           │ few-shot examples      │                 │                   │
└───────────────────────────────────────────────────────────────────────────┘
```

**Model-specific behavioral differences (interview-critical):**
- **Claude 4.x+**: Takes instructions literally. Does exactly what is asked, nothing more. Vague prompts produce minimal output rather than expanded interpretations. Extended thinking effort levels are the primary lever -- "lazy model" reports are usually effort-too-low, not a prompt problem.
- **GPT reasoning models (o1, o3)**: Chain-of-thought is built into the model. Explicit "think step by step" instructions are redundant and can degrade performance. Focus on clearly stating the problem and desired output.
- **Non-reasoning models**: Explicit CoT prompting ("think step by step") yields measurable accuracy gains. But diminishing returns are real: reasoning models (o3-mini, o4-mini) gain only 2.9-3.1% from explicit CoT while requiring 20-80% more time.

### 2.3 Attention Economics and the Degradation Threshold

The transformer attention mechanism allocates differential focus across tokens. This has three production-critical implications:

**The 3,000-token degradation threshold.** Research found that LLM reasoning performance starts degrading around 3,000 tokens of prompt instruction -- well below technical context window maximums (128K-1M tokens). The practical sweet spot for most tasks is 150-300 words of instruction (~200-400 tokens). More content does not mean better results; it means diluted attention.

**The lost-in-the-middle effect.** Instructions in the middle of long contexts receive less attention than those at the beginning or end. Production mitigation: place critical instructions at the start (system prompt) and end (final user message). Bury reference material in the middle where lower attention is acceptable.

**Negative instruction failure.** "Don't use markdown" is less reliable than "Use flowing prose paragraphs." Models attend to the semantic content of instructions, so negative framing ("don't X") activates the representation of X. Positive reframing eliminates the ambiguity.

### 2.4 Prompting Technique Decision Tree

```
                         ┌──────────────────────┐
                         │ Is this a reasoning-  │
                         │ native model (o1, o3, │
                         │ DeepSeek-R1)?         │
                         └─────┬──────┬──────────┘
                           YES │      │ NO
                               ▼      ▼
                  ┌──────────────┐  ┌──────────────────────┐
                  │ Simple direct │  │ Does the task need    │
                  │ prompt. No    │  │ multi-step reasoning? │
                  │ CoT, no few- │  └─────┬──────┬──────────┘
                  │ shot needed.  │    YES │      │ NO
                  └──────────────┘        ▼      ▼
                              ┌──────────────┐  ┌───────────────────┐
                              │ CoT prompting │  │ Is format/style   │
                              │ (+1.5x tokens,│  │ control critical? │
                              │ +<1% accuracy │  └────┬───────┬──────┘
                              │ on modern     │   YES │       │ NO
                              │ models)       │       ▼       ▼
                              └──────────────┘  ┌──────────┐ ┌──────────────┐
                                                │ Few-shot │ │ Zero-shot    │
                                                │ (3-5     │ │ (93.1%       │
                                                │ examples,│ │ accuracy on  │
                                                │ 1.3x     │ │ modern       │
                                                │ tokens)  │ │ models, 1x   │
                                                └──────────┘ │ tokens)      │
                                                             └──────────────┘
                         ┌──────────────────────┐
                         │ Is this high-stakes   │
                         │ with clear rubric?    │
                         └─────┬──────┬──────────┘
                           YES │      │ NO
                               ▼      ▼
                  ┌──────────────┐  ┌──────────────────┐
                  │ Self-         │  │ Does it involve   │
                  │ Consistency   │  │ tool use?         │
                  │ (10x tokens,  │  └────┬───────┬─────┘
                  │ +12-18% over  │  YES  │       │ NO
                  │ CoT)          │       ▼       ▼
                  └──────────────┘  ┌──────────┐  Standard
                                    │ ReAct or │  techniques
                                    │ ReWOO    │  above
                                    │ (ReWOO:  │
                                    │ 64% fewer│
                                    │ tokens,  │
                                    │ +4.4%    │
                                    │ accuracy)│
                                    └──────────┘
```

### 2.5 Prompt Chaining: Accuracy Compounding Analysis

Prompt chaining decomposes complex tasks into focused steps. The accuracy trade-off is non-linear:

**Accuracy compounding formula:** For n steps each with per-step accuracy p, the end-to-end accuracy is p^n.

```
┌─────────────────────────────────────────────────────────────┐
│ Per-step accuracy │  3 steps  │  5 steps  │  10 steps       │
├───────────────────┼───────────┼───────────┼─────────────────┤
│       99%         │   97.0%   │   95.1%   │    90.4%        │
│       95%         │   85.7%   │   77.4%   │    59.9%        │
│       90%         │   72.9%   │   59.0%   │    34.9%        │
│       85%         │   61.4%   │   44.4%   │    19.7%        │
└─────────────────────────────────────────────────────────────┘
```

**Effective chain length: 2.8-3.2 steps.** Research on automated chaining (Chainer, 2025) found this to be the optimal decomposition depth. Beyond 3-4 steps, accuracy compounding erodes gains from decomposition.

**Mandatory inter-step gates:** Programmatic validation between steps is not optional. Schema validation, required field checks, policy thresholds, and human approval gates prevent error propagation. Without gates, a 95%-accurate 5-step chain delivers 77.4% end-to-end accuracy. With gates that catch and retry failures, effective per-step accuracy rises to 99%+, yielding 95.1% end-to-end.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Per Technique (Empirical Data)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Technique            │ $/call  │ Token    │ Accuracy  │ Value/token         │
│                      │         │ Multiple │ (general) │ (accuracy/tokens)   │
├──────────────────────┼─────────┼──────────┼───────────┼─────────────────────┤
│ Zero-shot            │ $0.015  │ 1.0x     │ 93.1%     │ Baseline (best)     │
│ Few-shot (3-5 ex.)   │ $0.019  │ 1.3x     │ ~94%      │ ~0.97x baseline     │
│ Chain-of-Thought     │ $0.022  │ 1.5x     │ ~94%      │ ~0.67x baseline     │
│ ReAct                │ $0.040  │ 2.7x     │ High      │ ~0.37x baseline     │
│ Self-Consistency (5) │ $0.154  │ 10x      │ ~95-99%*  │ ~0.10x baseline     │
│ Tree-of-Thought      │ $0.700  │ 47x      │ Domain**  │ ~0.02x baseline     │
└──────────────────────────────────────────────────────────────────────────────┘
  * Self-Consistency: +12-18% over CoT on high-stakes decisions
  ** ToT: 74% on Game of 24 vs. 4% CoT (domain-specific win)

  Key finding: Zero-shot on modern models scores within 1% of CoT while
  using 40% fewer tokens. Few-shot and zero-shot deliver 30x more value
  per token than ToT or Self-Consistency for typical tasks.
```

**Cost formula for 1,000 runs:**
```
  cost_1k = technique_cost_per_call * 1000 * (1 - cache_hit_rate * cache_discount)

  Examples (Anthropic pricing, 75% cache hit rate, 90% cache discount):
    Zero-shot:          $0.015 * 1000 * (1 - 0.75 * 0.90) = $4.88 / 1k runs
    Few-shot:           $0.019 * 1000 * (1 - 0.75 * 0.90) = $6.18 / 1k runs
    CoT:                $0.022 * 1000 * (1 - 0.75 * 0.90) = $7.15 / 1k runs
    Self-Consistency:   $0.154 * 1000 * (1 - 0.75 * 0.90) = $50.05 / 1k runs
    Tree-of-Thought:    $0.700 * 1000 * (1 - 0.75 * 0.90) = $227.50 / 1k runs
```

### 3.2 Prompt Caching Economics

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Provider   │ Mechanism         │ Read Discount │ Write     │ Min Prefix     │
│            │                   │               │ Surcharge │                │
├────────────┼───────────────────┼───────────────┼───────────┼────────────────┤
│ Anthropic  │ Explicit          │ 90% (0.1x)    │ 25%       │ 1,024 tokens   │
│            │ cache_control     │               │           │                │
├────────────┼───────────────────┼───────────────┼───────────┼────────────────┤
│ OpenAI     │ Automatic         │ 50%           │ None      │ 1,024 tokens   │
├────────────┼───────────────────┼───────────────┼───────────┼────────────────┤
│ Google     │ Explicit          │ Up to 75%     │ Yes       │ Varies         │
├────────────┼───────────────────┼───────────────┼───────────┼────────────────┤
│ vLLM/SGLang│ Automatic prefix  │ Free (self-   │ N/A       │ N/A            │
│            │                   │ hosted)       │           │                │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Break-even**: 2+ requests sharing the same prefix make Anthropic caching cost-positive.

**Real-world case study**: Sonnet with caching ($240/month) beats Haiku without caching ($1,600/month) -- 6.7x cost improvement while retaining full capability. This is a counterintuitive result: caching on a larger model is cheaper than running a smaller model without caching.

**Production result**: ProjectDiscovery raised cache hit rate from 7% to 84%, cutting total LLM spend by 59-70%.

**Cache invalidation pitfall**: Exact prefix matching means a single changed character anywhere in the cached prefix (a changed timestamp, tool serialization reorder, rewritten earlier turn) causes a complete miss. Stateless architectures with identical system prompts across users maximize benefit.

### 3.3 Latency SLA Targets

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Metric                     │ p50 Target  │ p95 Target  │ p99 Target        │
├────────────────────────────┼─────────────┼─────────────┼───────────────────┤
│ TTFT (cached prefix)       │ 150ms       │ 400ms       │ 800ms             │
│ TTFT (uncached)            │ 500ms       │ 1,500ms     │ 3,000ms           │
│ Total latency (zero-shot)  │ 800ms       │ 2,000ms     │ 4,000ms           │
│ Total latency (CoT)        │ 1,500ms     │ 4,000ms     │ 8,000ms           │
│ Total latency (reasoning)  │ 5,000ms     │ 15,000ms    │ 30,000ms          │
│ Prompt compilation         │ <10ms       │ <25ms       │ <50ms             │
│ Cache lookup               │ <5ms        │ <15ms       │ <30ms             │
│ Output validation          │ <20ms       │ <50ms       │ <100ms            │
└──────────────────────────────────────────────────────────────────────────────┘

  TTFT dominated by prefill computation, which scales linearly with prompt
  length. Caching skips prefix computation, reducing TTFT by 50-85%.
```

### 3.4 Throughput Capacity Planning

**Token budget growth**: Average LLM API prompt length grew ~4x between early 2024 and late 2025 (from ~1,500 to ~6,000 tokens per request). Capacity planning must account for this growth trajectory.

**Prompt engineering time cost**: Prompt engineering accounts for 30-40% of time spent in AI application development (2025 industry surveys). This is the dominant engineering cost -- far exceeding inference costs for most teams.

**Caching vs. model selection decision matrix:**

```
┌──────────────────────────────────────────────────────────────────────┐
│ Strategy                            │ Monthly Cost  │ Quality       │
├─────────────────────────────────────┼───────────────┼───────────────┤
│ Large model + caching (Sonnet+cache)│ $240          │ High          │
│ Large model, no caching             │ $1,700+       │ High          │
│ Small model, no caching (Haiku)     │ $1,600        │ Lower         │
└──────────────────────────────────────────────────────────────────────┘
  Decision: always evaluate caching on large model before downgrading
  to smaller model. The cost-quality frontier usually favors caching.
```

---

## 4. Distributed Resilience & Security

### 4.1 Prompt Versioning and Durable Execution

**Version lifecycle state machine:**

```
┌─────────┐  commit  ┌──────────┐  eval pass  ┌──────────┐  approve  ┌────────┐
│  DRAFT  │────────▶│  STAGED  │───────────▶│ CANDIDATE│─────────▶│  PROD  │
└─────────┘         └──────────┘            └──────────┘          └────────┘
                         │                       │                     │
                     eval fail                eval fail             rollback
                         │                       │                     │
                         ▼                       ▼                     ▼
                    ┌──────────┐           ┌──────────┐         re-label to
                    │ REJECTED │           │ REJECTED │         prior version
                    └──────────┘           └──────────┘
```

**Production workflow:**
1. Define input/output schema (Pydantic/Zod) first -- schema-first development.
2. Build prompt around schema, version as immutable artifact.
3. Run evaluation suite on every version change (CI/CD integration).
4. Deploy via environment labels (dev -> staging -> prod) with feature flags for staged rollout.
5. Monitor with observability dashboards for drift detection.
6. Rollback = re-point environment label to previous version (instant, no redeployment).

**Model pinning**: Pin to specific model snapshots (e.g., `gpt-4.1-2025-04-14`). Unpinned model versions silently migrate, causing prompt drift. Build eval suites that measure prompt behavior before iterating.

### 4.2 Prompt Drift Detection

**What drifts**: The same prompt produces different outputs over time even when unchanged. A classification task at 95% accuracy can hover around 80% months later. Without monitoring, models left unchanged for 6+ months saw error rates jump 35% on new data (2025 LLMOps report).

**Failure taxonomy:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Drift Type         │ Cause                    │ Detection Method            │
├────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Model drift        │ Silent API model updates  │ CI/CD eval on fixed test    │
│                    │ (model tested in March    │ set, nightly builds, fail   │
│                    │ differs from April)       │ pipeline when scores drop   │
├────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Data drift         │ New user segments,        │ PSI, KL-Divergence on       │
│                    │ seasonal patterns,        │ input distribution          │
│                    │ edge case frequency shift │                             │
├────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Semantic drift     │ Subtle meaning shifts     │ Embedding-based distance    │
│                    │ in input/output           │ metrics on I/O pairs        │
├────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Cascade drift      │ Upstream prompt in chain  │ LLM-as-Judge comparing      │
│                    │ updated, changing          │ drifted samples to          │
│                    │ downstream context        │ reference baseline          │
├────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Deprecation drift  │ Unpinned model version    │ Version monitoring,         │
│                    │ silently migrated          │ provider changelog alerts   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Migration best practice:** Build a fixed migration eval set from real production inputs. Run through both old and new model. Compare every difference for correctness, format, length. Re-tune prompt for new model. Roll out to small traffic slice first. Keep old model reachable for rollback.

### 4.3 Prompt Injection: Threat Landscape

**Threat status**: OWASP LLM01. Attack success rates: 50-84% depending on configuration. No complete fix exists as of 2026. OpenAI publicly acknowledged (December 2025) that prompt injection, like social engineering, is unlikely to ever be fully solved. The realistic goal is containment, not prevention.

**Attack taxonomy:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Attack Type        │ Vector                   │ Trend                       │
├────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Direct injection   │ User crafts input that   │ Declining (better           │
│                    │ overrides system prompt   │ instruction hierarchy)      │
├────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Indirect injection │ Malicious instructions   │ Dominant vector in 2026     │
│                    │ in documents, emails,     │ (multi-hop via agents       │
│                    │ web content model reads   │ increased 70%+ YoY)        │
├────────────────────┼──────────────────────────┼─────────────────────────────┤
│ Multi-hop indirect │ Chained across agents    │ Fastest-growing attack      │
│                    │ and tools -- attacker     │ surface due to agentic AI   │
│                    │ influences what AI reads  │ adoption                    │
│                    │ to trigger code execution │                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Real-world CVEs:**
- **EchoLeak (CVE-2025-32711)**: CVSS 9.3. Zero-click: crafted email caused Microsoft 365 Copilot to retrieve internal files and forward to attacker.
- **MCP Server (CVE-2025-68143/44/45)**: Critical. Anthropic's own Git MCP server -- attacker influences what AI reads to trigger code execution.
- **GitHub Copilot**: CVSS 9.6. Active production exploitation.
- **Cursor IDE**: CVSS 9.8. Active production exploitation.

**Measured success rates from system cards:**
- Claude Opus 4.6: 17.8% on single attempt, 78.6% across 200 attempts without safeguards, 57.1% with published defenses.
- Google Gemini: 53.6% after adversarial fine-tuning.

### 4.4 Layered Defense Framework

Defense frameworks reduce attack success from 73.2% to 8.7% when layered properly.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                     LAYER 1: ARCHITECTURAL PREVENTION                        │
│                                                                              │
│  Handle functions in code, NOT delegated to model                            │
│  Scope each tool token to least privilege                                    │
│  Require human approval for privileged operations                            │
│  Separate data plane from control plane                                      │
│  Hard limits on tool call counts                                             │
├──────────────────────────────────────────────────────────────────────────────┤
│                     LAYER 2: RUNTIME DETECTION                               │
│                                                                              │
│  Input filtering (pattern-based + semantic classifiers)                       │
│  LLM-as-Critic output validation: +21% detection precision over input-only   │
│  Confidence scoring -> low-certainty routes to human review                  │
│  Output schema enforcement via constrained decoding                          │
├──────────────────────────────────────────────────────────────────────────────┤
│                     LAYER 3: GOVERNANCE                                       │
│                                                                              │
│  Audit trails for every prompt/response pair                                 │
│  Role-based access to prompt modification                                    │
│  Compliance: OWASP, MITRE ATLAS, NIST, EU AI Act (Aug 2026 deadline),       │
│  ISO 42001, GDPR, NIS2                                                       │
│  83% of organizations plan agentic AI, only 29% feel ready to secure it     │
└──────────────────────────────────────────────────────────────────────────────┘

  Emerging defenses:
    SecAlign: misses ~10% of optimization-based attacks
    ReasAlign (Jan 2026): cuts attack success to 3.6% on one benchmark,
      but static benchmarks, not adaptive attackers
```

### 4.5 Structured Output as a Security Boundary

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Tier                        │ Mechanism             │ Failure Rate │ Status  │
├─────────────────────────────┼───────────────────────┼──────────────┼─────────┤
│ Prompt-only ("respond in    │ Hope-based            │ 5-20%        │ Never   │
│ JSON")                      │                       │              │ use     │
├─────────────────────────────┼───────────────────────┼──────────────┼─────────┤
│ JSON Mode                   │ Valid syntax only     │ 1-5%         │ Legacy  │
├─────────────────────────────┼───────────────────────┼──────────────┼─────────┤
│ Schema-enforced (constrained│ FSM masks invalid     │ ~0%          │ Prod    │
│ decoding)                   │ tokens at generation  │ syntactic    │ default │
└──────────────────────────────────────────────────────────────────────────────┘

  74% of LLM production applications use structured output (2026), up from
  ~40% two years ago. Instructor library: 3M+ monthly downloads, 11K GitHub
  stars, provider-neutral Pydantic validation with automatic retry.
```

### 4.6 Hallucination Mitigation Ladder

Ordered by implementation complexity and effectiveness:

1. **Temperature 0** for factual tasks (40-60% hallucination reduction)
2. **Explicit permission** to say "I don't know"
3. **Quote extraction before reasoning** (Anthropic's single most effective mitigation)
4. **Self-verification step** appended to prompt
5. **RAG with source grounding**
6. **Output validation via LLM-as-Critic**

---

## 5. Production Enterprise Code

### 5.1 Prompt Compilation Pipeline with Token Budget Management

```python
"""
Production prompt compilation pipeline with token budget management,
cache-aligned role assembly, and dynamic content trimming.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import tiktoken

logger = logging.getLogger("prompt_compiler")


@dataclass(frozen=True)
class PromptVersion:
    """Immutable prompt version artifact."""
    version_id: str
    template: str
    schema_hash: str
    created_at: float
    environment: str  # dev | staging | prod


@dataclass
class CompiledPrompt:
    """Output of the compilation pipeline."""
    messages: list[dict[str, str]]
    total_tokens: int
    cache_prefix_hash: str
    trimmed_examples: int
    trimmed_doc_chunks: int


class PromptCompiler:
    """
    Compiles versioned prompt templates into cache-aligned,
    token-budget-checked API payloads.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        context_window: int = 200_000,
        reserved_output_tokens: int = 4_096,
        encoding_name: str = "cl100k_base",
    ):
        self._model = model
        self._context_window = context_window
        self._reserved_output = reserved_output_tokens
        self._encoder = tiktoken.get_encoding(encoding_name)
        self._budget = context_window - reserved_output_tokens

    def _count_tokens(self, text: str) -> int:
        return len(self._encoder.encode(text))

    def _compute_prefix_hash(self, static_content: str) -> str:
        return hashlib.sha256(static_content.encode()).hexdigest()[:16]

    def compile(
        self,
        version: PromptVersion,
        variables: dict[str, str],
        few_shot_examples: list[dict[str, str]],
        retrieved_docs: list[tuple[str, float]],  # (text, relevance_score)
        conversation_history: list[dict[str, str]],
        user_message: str,
    ) -> CompiledPrompt:
        """
        Full compilation pipeline:
        TEMPLATE_RESOLVED -> INJECTED -> BUDGET_CHECKED
        -> ROLE_ASSEMBLED -> CACHE_ALIGNED
        """
        # --- Stage 1: Template resolution ---
        try:
            system_content = version.template.format(**variables)
        except KeyError as e:
            raise ValueError(
                f"Missing template variable {e} in version {version.version_id}"
            ) from e

        # --- Stage 2: Token budget accounting ---
        system_tokens = self._count_tokens(system_content)
        user_tokens = self._count_tokens(user_message)
        history_tokens = sum(
            self._count_tokens(m.get("content", ""))
            for m in conversation_history
        )

        remaining = self._budget - system_tokens - user_tokens - history_tokens
        if remaining <= 0:
            raise ValueError(
                f"Core content ({system_tokens + user_tokens + history_tokens} tokens) "
                f"exceeds budget ({self._budget} tokens) before examples and docs"
            )

        # --- Stage 3: Budget recovery - trim few-shot examples ---
        trimmed_examples = 0
        included_examples: list[dict[str, str]] = []
        for ex in few_shot_examples:
            ex_tokens = self._count_tokens(
                ex.get("user", "") + ex.get("assistant", "")
            )
            if remaining - ex_tokens > 0:
                included_examples.append(ex)
                remaining -= ex_tokens
            else:
                trimmed_examples += 1
                logger.warning(
                    "Trimmed few-shot example due to budget. "
                    "Remaining: %d tokens", remaining
                )

        # --- Stage 4: Budget recovery - rank and trim retrieved docs ---
        sorted_docs = sorted(retrieved_docs, key=lambda d: d[1], reverse=True)
        trimmed_docs = 0
        included_docs: list[str] = []
        for doc_text, score in sorted_docs:
            doc_tokens = self._count_tokens(doc_text)
            if remaining - doc_tokens > 0:
                included_docs.append(doc_text)
                remaining -= doc_tokens
            else:
                trimmed_docs += 1

        # --- Stage 5: Role assembly (Anthropic 10-part framework) ---
        # Static content first for cache alignment
        static_parts = [system_content]
        for ex in included_examples:
            static_parts.append(f"Example user: {ex['user']}")
            static_parts.append(f"Example assistant: {ex['assistant']}")

        static_block = "\n\n".join(static_parts)
        cache_prefix_hash = self._compute_prefix_hash(static_block)

        messages: list[dict[str, str]] = [
            {"role": "system", "content": static_block}
        ]

        # Retrieved docs in user context (middle position -- lower
        # attention is acceptable for reference material)
        if included_docs:
            doc_block = "\n\n---\n\n".join(included_docs)
            messages.append({
                "role": "user",
                "content": f"Reference documents:\n\n{doc_block}",
            })

        # Conversation history
        messages.extend(conversation_history)

        # Current user message last (high attention position)
        messages.append({"role": "user", "content": user_message})

        total_tokens = self._budget - remaining + self._reserved_output

        return CompiledPrompt(
            messages=messages,
            total_tokens=total_tokens,
            cache_prefix_hash=cache_prefix_hash,
            trimmed_examples=trimmed_examples,
            trimmed_doc_chunks=trimmed_docs,
        )
```

### 5.2 Multi-Tier Cache with Semantic Similarity

```python
"""
Multi-tier prompt cache: semantic similarity -> prefix hash -> full inference.
Implements the production caching architecture that cut LLM spend 59-70%
at ProjectDiscovery.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger("prompt_cache")


@dataclass
class CacheEntry:
    """Cached LLM response with metadata."""
    response: str
    embedding: np.ndarray
    prefix_hash: str
    created_at: float
    hit_count: int = 0
    ttl_seconds: float = 3600.0


class MultiTierCache:
    """
    Three-tier cache:
      1. Semantic cache (embedding cosine similarity, ~31% hit rate)
      2. Prefix cache (exact hash match on static prefix)
      3. Miss -> full inference

    Metrics tracked: hit rate per tier, latency saved, cost saved.
    """

    def __init__(
        self,
        semantic_threshold: float = 0.95,
        max_entries: int = 10_000,
        embed_fn: Any = None,
    ):
        self._semantic_threshold = semantic_threshold
        self._max_entries = max_entries
        self._embed_fn = embed_fn  # Callable[[str], np.ndarray]
        self._semantic_store: list[CacheEntry] = []
        self._prefix_store: dict[str, CacheEntry] = {}

        # Metrics
        self._hits = {"semantic": 0, "prefix": 0, "miss": 0}
        self._total_lookups = 0

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    def _evict_expired(self) -> None:
        now = time.time()
        self._semantic_store = [
            e for e in self._semantic_store
            if now - e.created_at < e.ttl_seconds
        ]
        expired_keys = [
            k for k, v in self._prefix_store.items()
            if now - v.created_at >= v.ttl_seconds
        ]
        for k in expired_keys:
            del self._prefix_store[k]

    def lookup(
        self,
        query: str,
        prefix_hash: str,
    ) -> tuple[str | None, str]:
        """
        Returns (cached_response, tier_hit).
        tier_hit is one of: "semantic", "prefix", "miss".
        """
        self._total_lookups += 1
        self._evict_expired()

        # Tier 1: Semantic cache
        if self._embed_fn is not None:
            query_embedding = self._embed_fn(query)
            best_score = 0.0
            best_entry: CacheEntry | None = None

            for entry in self._semantic_store:
                score = self._cosine_similarity(query_embedding, entry.embedding)
                if score > best_score:
                    best_score = score
                    best_entry = entry

            if best_entry is not None and best_score >= self._semantic_threshold:
                best_entry.hit_count += 1
                self._hits["semantic"] += 1
                logger.info(
                    "Semantic cache hit (similarity=%.4f, threshold=%.4f)",
                    best_score, self._semantic_threshold,
                )
                return best_entry.response, "semantic"

        # Tier 2: Prefix cache
        if prefix_hash in self._prefix_store:
            entry = self._prefix_store[prefix_hash]
            entry.hit_count += 1
            self._hits["prefix"] += 1
            logger.info("Prefix cache hit (hash=%s)", prefix_hash[:8])
            return entry.response, "prefix"

        # Tier 3: Miss
        self._hits["miss"] += 1
        return None, "miss"

    def store(
        self,
        query: str,
        prefix_hash: str,
        response: str,
        ttl_seconds: float = 3600.0,
    ) -> None:
        """Store response in both tiers."""
        embedding = (
            self._embed_fn(query) if self._embed_fn is not None
            else np.array([])
        )
        entry = CacheEntry(
            response=response,
            embedding=embedding,
            prefix_hash=prefix_hash,
            created_at=time.time(),
            ttl_seconds=ttl_seconds,
        )

        if len(self._semantic_store) >= self._max_entries:
            # Evict least-hit entry
            self._semantic_store.sort(key=lambda e: e.hit_count)
            self._semantic_store.pop(0)

        self._semantic_store.append(entry)
        self._prefix_store[prefix_hash] = entry

    @property
    def hit_rates(self) -> dict[str, float]:
        if self._total_lookups == 0:
            return {"semantic": 0.0, "prefix": 0.0, "miss": 0.0}
        return {
            tier: count / self._total_lookups
            for tier, count in self._hits.items()
        }
```

### 5.3 Prompt Drift Detector with Multi-Method Detection

```python
"""
Prompt drift detection using embedding-based, statistical (PSI),
and LLM-as-Judge methods. Production systems without drift monitoring
saw 35% error rate jumps over 6 months.
"""

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

import numpy as np

logger = logging.getLogger("drift_detector")


class DriftSeverity(Enum):
    NONE = auto()
    LOW = auto()       # <5% score drop
    MEDIUM = auto()    # 5-15% score drop
    HIGH = auto()      # >15% score drop
    CRITICAL = auto()  # >25% score drop or output format breakdown


@dataclass
class DriftReport:
    severity: DriftSeverity
    psi_score: float
    embedding_drift: float
    eval_score_delta: float
    recommended_action: str
    details: dict[str, Any] = field(default_factory=dict)


class PopulationStabilityIndex:
    """
    PSI measures distribution shift between a reference (baseline)
    and current population. PSI < 0.1 = stable, 0.1-0.25 = moderate
    drift, > 0.25 = significant drift requiring prompt re-tuning.
    """

    @staticmethod
    def compute(
        reference: np.ndarray,
        current: np.ndarray,
        bins: int = 10,
        epsilon: float = 1e-6,
    ) -> float:
        """Compute PSI between reference and current distributions."""
        ref_min = min(reference.min(), current.min())
        ref_max = max(reference.max(), current.max())
        bin_edges = np.linspace(ref_min, ref_max, bins + 1)

        ref_counts, _ = np.histogram(reference, bins=bin_edges)
        cur_counts, _ = np.histogram(current, bins=bin_edges)

        ref_pct = (ref_counts + epsilon) / (len(reference) + epsilon * bins)
        cur_pct = (cur_counts + epsilon) / (len(current) + epsilon * bins)

        psi = float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))
        return psi


class PromptDriftDetector:
    """
    Multi-method drift detection:
      1. Embedding-based: cosine distance between baseline and current I/O
      2. Statistical (PSI): distribution shift in confidence scores
      3. Eval-based: score delta against fixed eval set

    Usage: run on a schedule (nightly) or on every prompt version change.
    """

    def __init__(
        self,
        baseline_embeddings: np.ndarray,
        baseline_scores: np.ndarray,
        baseline_eval_accuracy: float,
        embedding_drift_threshold: float = 0.15,
        psi_threshold: float = 0.25,
        eval_drop_threshold: float = 0.05,
    ):
        self._baseline_embeddings = baseline_embeddings
        self._baseline_scores = baseline_scores
        self._baseline_eval_accuracy = baseline_eval_accuracy
        self._embedding_threshold = embedding_drift_threshold
        self._psi_threshold = psi_threshold
        self._eval_threshold = eval_drop_threshold
        self._psi_calc = PopulationStabilityIndex()

    def _compute_embedding_drift(
        self, current_embeddings: np.ndarray
    ) -> float:
        """Mean cosine distance between baseline and current centroid."""
        baseline_centroid = self._baseline_embeddings.mean(axis=0)
        current_centroid = current_embeddings.mean(axis=0)

        dot = np.dot(baseline_centroid, current_centroid)
        norm_b = np.linalg.norm(baseline_centroid)
        norm_c = np.linalg.norm(current_centroid)

        if norm_b == 0 or norm_c == 0:
            return 1.0
        similarity = dot / (norm_b * norm_c)
        return float(1.0 - similarity)

    def _classify_severity(
        self,
        psi: float,
        embedding_drift: float,
        eval_delta: float,
    ) -> DriftSeverity:
        if eval_delta > 0.25 or psi > 0.5:
            return DriftSeverity.CRITICAL
        if eval_delta > 0.15 or psi > self._psi_threshold:
            return DriftSeverity.HIGH
        if eval_delta > self._eval_threshold or psi > 0.1:
            return DriftSeverity.MEDIUM
        if embedding_drift > self._embedding_threshold:
            return DriftSeverity.LOW
        return DriftSeverity.NONE

    def _recommend_action(self, severity: DriftSeverity) -> str:
        actions = {
            DriftSeverity.NONE: "No action required. Continue monitoring.",
            DriftSeverity.LOW: (
                "Investigate input distribution changes. "
                "Review recent model provider changelogs."
            ),
            DriftSeverity.MEDIUM: (
                "Run full eval suite. Compare output quality on "
                "migration eval set. Consider prompt re-tuning."
            ),
            DriftSeverity.HIGH: (
                "Immediate eval required. Roll out to small traffic "
                "slice only. Prepare rollback to last known-good version."
            ),
            DriftSeverity.CRITICAL: (
                "ROLLBACK RECOMMENDED. Revert to previous prompt version. "
                "Conduct root cause analysis before redeploying."
            ),
        }
        return actions[severity]

    def detect(
        self,
        current_embeddings: np.ndarray,
        current_scores: np.ndarray,
        current_eval_accuracy: float,
    ) -> DriftReport:
        """Run all three detection methods and produce a unified report."""
        psi = self._psi_calc.compute(self._baseline_scores, current_scores)
        embedding_drift = self._compute_embedding_drift(current_embeddings)
        eval_delta = self._baseline_eval_accuracy - current_eval_accuracy

        severity = self._classify_severity(psi, embedding_drift, eval_delta)

        return DriftReport(
            severity=severity,
            psi_score=psi,
            embedding_drift=embedding_drift,
            eval_score_delta=eval_delta,
            recommended_action=self._recommend_action(severity),
            details={
                "baseline_eval_accuracy": self._baseline_eval_accuracy,
                "current_eval_accuracy": current_eval_accuracy,
                "psi_thresholds": {
                    "stable": "<0.10",
                    "moderate": "0.10-0.25",
                    "significant": ">0.25",
                },
            },
        )
```

### 5.4 LLM Gateway with Circuit Breaker, Fallback Chain, and Structured Logging

```python
"""
LLM Gateway with per-provider circuit breaker, model fallback chain,
exponential backoff with jitter, output validation via constrained
decoding, and structured JSON logging.
"""

import json
import logging
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Protocol

logger = logging.getLogger("llm_gateway")


# --- Structured JSON Logging ---

class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data"):
            log_entry.update(record.extra_data)
        return json.dumps(log_entry)


handler = logging.StreamHandler()
handler.setFormatter(StructuredFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)


# --- Circuit Breaker ---

class CircuitState(Enum):
    CLOSED = auto()      # Normal operation
    OPEN = auto()        # Failing, reject immediately
    HALF_OPEN = auto()   # Testing recovery


@dataclass
class CircuitBreaker:
    """
    Per-provider circuit breaker. Opens after `failure_threshold`
    consecutive failures. Transitions to HALF_OPEN after `recovery_timeout`
    seconds. Closes after `success_threshold` consecutive successes in
    HALF_OPEN state.
    """
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    success_threshold: int = 2

    state: CircuitState = field(default=CircuitState.CLOSED)
    failure_count: int = field(default=0)
    success_count: int = field(default=0)
    last_failure_time: float = field(default=0.0)

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                logger.info("Circuit breaker CLOSED (recovered)")
        else:
            self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.success_count = 0
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN after %d failures", self.failure_count
            )

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                logger.info("Circuit breaker HALF_OPEN (testing recovery)")
                return True
            return False
        # HALF_OPEN: allow limited traffic
        return True


# --- LLM Provider Protocol ---

class LLMProvider(Protocol):
    def complete(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> dict[str, Any]: ...

    @property
    def name(self) -> str: ...


# --- Retry with Exponential Backoff + Jitter ---

def retry_with_backoff(
    fn: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter_range: float = 0.5,
    retryable_exceptions: tuple[type[Exception], ...] = (
        ConnectionError,
        TimeoutError,
    ),
) -> Any:
    """
    Exponential backoff with full jitter.
    Delay = min(max_delay, base_delay * 2^attempt) * uniform(1-jitter, 1+jitter)
    """
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retryable_exceptions as e:
            last_exception = e
            if attempt == max_retries:
                break

            delay = min(max_delay, base_delay * (2 ** attempt))
            jitter = random.uniform(1 - jitter_range, 1 + jitter_range)
            sleep_time = delay * jitter

            logger.warning(
                "Retry %d/%d after %.2fs (error: %s)",
                attempt + 1, max_retries, sleep_time, str(e),
            )
            time.sleep(sleep_time)

    raise last_exception  # type: ignore[misc]


# --- LLM Gateway ---

@dataclass
class GatewayMetrics:
    total_requests: int = 0
    successful_requests: int = 0
    fallback_requests: int = 0
    circuit_breaker_rejections: int = 0
    total_latency_ms: float = 0.0
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0


class LLMGateway:
    """
    Production LLM gateway with:
    - Per-provider circuit breakers
    - Ordered fallback chain (primary -> secondary -> degraded)
    - Exponential backoff with jitter on retries
    - Structured JSON logging for every request
    - Graceful degradation (returns cached/default response on total failure)
    """

    def __init__(
        self,
        providers: list[LLMProvider],
        degraded_response: str = "Service temporarily unavailable. Please retry.",
        max_retries: int = 3,
    ):
        self._providers = providers
        self._degraded_response = degraded_response
        self._max_retries = max_retries
        self._breakers: dict[str, CircuitBreaker] = {
            p.name: CircuitBreaker() for p in providers
        }
        self._metrics = GatewayMetrics()

    def complete(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Try each provider in order. Skip providers with open circuit
        breakers. Retry transient failures with backoff. Fall through
        to degraded response if all providers fail.
        """
        self._metrics.total_requests += 1
        start_time = time.time()

        for i, provider in enumerate(self._providers):
            breaker = self._breakers[provider.name]

            if not breaker.allow_request():
                self._metrics.circuit_breaker_rejections += 1
                logger.info(
                    "Skipping %s (circuit breaker OPEN)", provider.name,
                    extra={"extra_data": {
                        "provider": provider.name,
                        "action": "circuit_breaker_skip",
                    }},
                )
                continue

            try:
                result = retry_with_backoff(
                    fn=lambda p=provider: p.complete(messages, **kwargs),
                    max_retries=self._max_retries,
                )
                breaker.record_success()

                latency_ms = (time.time() - start_time) * 1000
                self._metrics.successful_requests += 1
                self._metrics.total_latency_ms += latency_ms
                if i > 0:
                    self._metrics.fallback_requests += 1

                logger.info(
                    "Request completed via %s", provider.name,
                    extra={"extra_data": {
                        "provider": provider.name,
                        "latency_ms": round(latency_ms, 2),
                        "is_fallback": i > 0,
                        "tokens": result.get("usage", {}).get("total_tokens", 0),
                    }},
                )
                return result

            except Exception as e:
                breaker.record_failure()
                logger.error(
                    "Provider %s failed: %s", provider.name, str(e),
                    extra={"extra_data": {
                        "provider": provider.name,
                        "error": str(e),
                        "action": "provider_failure",
                    }},
                )

        # All providers exhausted: graceful degradation
        logger.critical(
            "All providers exhausted. Returning degraded response.",
            extra={"extra_data": {"action": "graceful_degradation"}},
        )
        return {
            "content": self._degraded_response,
            "model": "degraded",
            "usage": {"total_tokens": 0},
            "degraded": True,
        }

    @property
    def metrics(self) -> dict[str, Any]:
        avg_latency = (
            self._metrics.total_latency_ms / self._metrics.successful_requests
            if self._metrics.successful_requests > 0
            else 0.0
        )
        return {
            "total_requests": self._metrics.total_requests,
            "successful_requests": self._metrics.successful_requests,
            "fallback_rate": (
                self._metrics.fallback_requests / self._metrics.total_requests
                if self._metrics.total_requests > 0
                else 0.0
            ),
            "circuit_breaker_rejections": self._metrics.circuit_breaker_rejections,
            "avg_latency_ms": round(avg_latency, 2),
            "provider_states": {
                name: breaker.state.name
                for name, breaker in self._breakers.items()
            },
        }
```

### 5.5 Prompt Injection Input Filter

```python
"""
Layer 2 runtime detection: pattern-based + heuristic prompt injection
filter. Not a complete defense (no complete fix exists), but reduces
attack surface as part of the layered framework.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger("injection_filter")


class ThreatLevel(Enum):
    CLEAN = auto()
    SUSPICIOUS = auto()
    BLOCKED = auto()


@dataclass(frozen=True)
class FilterResult:
    threat_level: ThreatLevel
    matched_patterns: tuple[str, ...]
    sanitized_input: str
    explanation: str


# Patterns ordered from most specific (high confidence) to broad
_INJECTION_PATTERNS: list[tuple[str, str, ThreatLevel]] = [
    # Direct system prompt override attempts
    (
        r"(?i)ignore\s+(all\s+)?(previous|prior|above|earlier)\s+"
        r"(instructions|prompts|directives|rules)",
        "system_override",
        ThreatLevel.BLOCKED,
    ),
    (
        r"(?i)you\s+are\s+now\s+(a|an|in)\s+\w+\s+mode",
        "role_hijack",
        ThreatLevel.BLOCKED,
    ),
    (
        r"(?i)(system\s*prompt|system\s*message)\s*[:=]",
        "system_prompt_injection",
        ThreatLevel.BLOCKED,
    ),
    # Delimiter-based attacks
    (
        r"(?i)(<\|?(system|endoftext|im_start|im_end)\|?>)",
        "delimiter_injection",
        ThreatLevel.BLOCKED,
    ),
    # Exfiltration attempts
    (
        r"(?i)(send|forward|email|post|upload|transmit)\s+"
        r".*(to|at)\s+\S+@\S+",
        "exfiltration_attempt",
        ThreatLevel.BLOCKED,
    ),
    (
        r"(?i)(fetch|load|visit|open|navigate)\s+(https?://|www\.)",
        "url_fetch_attempt",
        ThreatLevel.SUSPICIOUS,
    ),
    # Encoded/obfuscated payloads
    (
        r"(?i)base64[:\s]|atob\(|btoa\(",
        "encoded_payload",
        ThreatLevel.SUSPICIOUS,
    ),
    # Instruction boundary manipulation
    (
        r"(?i)(new\s+instructions?|updated\s+instructions?|"
        r"revised\s+instructions?)\s*:",
        "instruction_boundary",
        ThreatLevel.SUSPICIOUS,
    ),
]


def filter_input(user_input: str) -> FilterResult:
    """
    Screen user input for known prompt injection patterns.
    Returns the threat level, matched patterns, and sanitized input.

    This is Layer 2 defense -- complements (does not replace) Layer 1
    architectural prevention (handle functions in code, least-privilege
    tool tokens, human approval for privileged ops).
    """
    matched: list[str] = []
    max_threat = ThreatLevel.CLEAN

    for pattern, name, threat_level in _INJECTION_PATTERNS:
        if re.search(pattern, user_input):
            matched.append(name)
            if threat_level.value > max_threat.value:
                max_threat = threat_level

    sanitized = user_input
    if max_threat == ThreatLevel.BLOCKED:
        # Strip the offending patterns but preserve surrounding context
        for pattern, name, _ in _INJECTION_PATTERNS:
            sanitized = re.sub(pattern, "[REDACTED]", sanitized)

    explanation = {
        ThreatLevel.CLEAN: "No injection patterns detected.",
        ThreatLevel.SUSPICIOUS: (
            f"Suspicious patterns detected: {', '.join(matched)}. "
            "Routing to human review."
        ),
        ThreatLevel.BLOCKED: (
            f"Injection attempt blocked: {', '.join(matched)}. "
            "Input sanitized."
        ),
    }[max_threat]

    if max_threat != ThreatLevel.CLEAN:
        logger.warning(
            "Injection filter triggered: level=%s patterns=%s",
            max_threat.name,
            matched,
        )

    return FilterResult(
        threat_level=max_threat,
        matched_patterns=tuple(matched),
        sanitized_input=sanitized,
        explanation=explanation,
    )
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Enterprise Prompt Management Platform for a Regulated Financial Services Firm

**Problem statement.** A financial services firm (500+ employees, SOC 2 Type II, PCI-DSS) operates 40+ LLM-powered applications across customer support, fraud detection, loan underwriting, and compliance monitoring. Prompts are scattered across application codebases with no version control, no evaluation baseline, and no audit trail. A prompt change in the fraud detection system went undetected for 3 weeks, causing a 23% increase in false negatives. The EU AI Act August 2026 deadline requires full prompt audit trails for their high-risk AI systems.

**Architecture:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                      GOVERNANCE LAYER                                        │
│                                                                              │
│  ┌───────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ RBAC + Approval    │  │ Compliance       │  │ Immutable Audit Log      │  │
│  │ Workflows          │  │ Dashboard        │  │ (hash-chained, links     │  │
│  │                    │  │                  │  │  every prompt version    │  │
│  │ Prompt authors:    │  │ OWASP LLM Top 10│  │  to eval scores, model   │  │
│  │  create, edit      │  │ EU AI Act mapping│  │  version, approver,      │  │
│  │ Reviewers:         │  │ ISO 42001 checks │  │  deployment timestamp)   │  │
│  │  approve versions  │  │ SOC 2 evidence   │  │                          │  │
│  │ Deployers:         │  │ collection       │  │ Retention: 7 years       │  │
│  │  promote env labels│  │                  │  │ (regulatory requirement) │  │
│  └────────┬──────────┘  └────────┬─────────┘  └──────────┬───────────────┘  │
└───────────┼──────────────────────┼────────────────────────┼──────────────────┘
            │                      │                        │
┌───────────▼──────────────────────▼────────────────────────▼──────────────────┐
│                      PROMPT REGISTRY (Git-based, Confident AI model)         │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ Branching model: main (prod) <- staging <- feature branches             │ │
│  │ Every commit triggers eval suite (CI/CD)                                │ │
│  │ Merge requires: (a) eval score >= baseline, (b) human reviewer approval │ │
│  │ Environment labels: dev / staging / prod (rollback = re-label)          │ │
│  │ Schema registry: Pydantic schemas co-versioned with each prompt         │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────────────┐    │
│  │ Fraud       │  │ Customer   │  │ Underwriting│  │ Compliance         │    │
│  │ Detection   │  │ Support    │  │ Analysis    │  │ Monitoring         │    │
│  │ Prompts     │  │ Prompts    │  │ Prompts     │  │ Prompts            │    │
│  │ (v47, prod) │  │ (v23, prod)│  │ (v12, prod) │  │ (v8, prod)         │    │
│  └──────┬─────┘  └──────┬─────┘  └──────┬─────┘  └──────┬─────────────┘    │
└─────────┼───────────────┼───────────────┼───────────────┼───────────────────┘
          │               │               │               │
┌─────────▼───────────────▼───────────────▼───────────────▼───────────────────┐
│                      EVALUATION ENGINE                                       │
│                                                                              │
│  ┌─────────────────────────┐  ┌──────────────────────────────────────────┐  │
│  │ Per-Domain Eval Sets     │  │ Multi-Metric Scoring                     │  │
│  │                          │  │                                          │  │
│  │ Fraud: 500 labeled cases │  │ Accuracy (vs. labeled ground truth)      │  │
│  │ Support: 300 dialogues   │  │ Format compliance (schema validation)    │  │
│  │ Underwriting: 200 apps   │  │ Latency (p95 < SLA target)              │  │
│  │ Compliance: 150 filings  │  │ Cost ($ per 1k runs within budget)      │  │
│  │                          │  │ Safety (injection resistance score)      │  │
│  │ Each drawn from real     │  │                                          │  │
│  │ production inputs        │  │ Gate: all metrics must meet thresholds   │  │
│  └─────────────────────────┘  └──────────────────────────────────────────┘  │
└──────────────────────────────────────────┬──────────────────────────────────┘
                                           │
┌──────────────────────────────────────────▼──────────────────────────────────┐
│                      DRIFT MONITORING                                       │
│                                                                              │
│  Nightly: run full eval suite against prod prompts on current model          │
│  Weekly: PSI on input distributions, embedding drift on I/O pairs            │
│  On model provider changelog: trigger migration eval set comparison          │
│  Alert thresholds: PSI > 0.1 -> WARN, PSI > 0.25 -> PAGE, eval drop > 5%   │
│    -> BLOCK DEPLOYMENT                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Decision               │ Option A              │ Option B                    │
├────────────────────────┼───────────────────────┼─────────────────────────────┤
│ Prompt storage         │ Git-based (Confident  │ Code-managed (OpenAI        │
│                        │ AI): branching, commit│ recommendation): prompts in │
│                        │ history, approval     │ application source code     │
│                        │ workflows, ISO 42001  │                             │
│                        │                       │                             │
│ CHOSEN: Git-based      │ WHY: regulatory       │ TRADE-OFF: extra tooling,   │
│                        │ audit trail, approval │ learning curve, platform    │
│                        │ workflows required    │ dependency                  │
├────────────────────────┼───────────────────────┼─────────────────────────────┤
│ Eval trigger           │ On every commit       │ Nightly batch               │
│                        │ (CI/CD integration)   │                             │
│                        │                       │                             │
│ CHOSEN: Every commit   │ WHY: prevents         │ TRADE-OFF: higher compute   │
│                        │ regression deploy     │ cost, longer CI pipeline    │
├────────────────────────┼───────────────────────┼─────────────────────────────┤
│ Model pinning          │ Pinned snapshots      │ Floating versions           │
│                        │ (gpt-4.1-2025-04-14)  │ (gpt-5.1, auto-updates)    │
│                        │                       │                             │
│ CHOSEN: Pinned         │ WHY: prevents silent  │ TRADE-OFF: manual migration │
│                        │ drift, reproducible   │ effort every quarter        │
│                        │ eval results          │                             │
├────────────────────────┼───────────────────────┼─────────────────────────────┤
│ Injection defense      │ Full 3-layer stack    │ Input filtering only        │
│                        │ (arch + runtime +     │                             │
│                        │ governance)           │                             │
│                        │                       │                             │
│ CHOSEN: Full 3-layer   │ WHY: reduces attack   │ TRADE-OFF: 15-20% added    │
│                        │ success 73.2% -> 8.7% │ latency, operational       │
│                        │                       │ complexity                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Decision rationale.** The git-based approach wins over code-managed prompts because regulatory requirements (EU AI Act, SOC 2) demand immutable audit trails with approval workflows that standard code review alone does not provide. Commit-triggered evaluation is non-negotiable: the 3-week undetected drift incident in fraud detection would have been caught within hours. Model pinning prevents the class of silent drift that caused the original incident. The full 3-layer injection defense is justified by the financial services threat model: the EchoLeak CVE demonstrated that a single indirect injection in a document can exfiltrate internal files.

---

### Scenario 2: Multi-Model Prompt Optimization Platform for a High-Volume Consumer AI Product

**Problem statement.** A consumer AI product handles 2M requests/day across four use cases: intent classification, response generation, content moderation, and document summarization. Currently running all traffic through a single large model (Claude Sonnet) at $1,700/month without caching. The product team needs to (a) cut inference costs by 60%+ while maintaining quality, (b) support model-specific prompt variants because testing revealed the same prompt scores 35% on Mistral-7B vs. 80% on GPT-4o-mini for few-shot tasks, and (c) implement graceful degradation for a product with 99.9% availability SLA.

**Architecture:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                      REQUEST ROUTER                                          │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ Task Classifier (rule-based, not LLM -- zero additional latency)     │    │
│  │                                                                      │    │
│  │ Intent classification -> Route to small model (Haiku-class)          │    │
│  │ Content moderation   -> Route to small model (Haiku-class)           │    │
│  │ Response generation  -> Route to large model (Sonnet-class)          │    │
│  │ Document summary     -> Route to large model (Sonnet-class)          │    │
│  └──────────┬───────────────────────────────┬───────────────────────────┘    │
│             │ simple tasks                   │ complex tasks                  │
└─────────────┼───────────────────────────────┼───────────────────────────────┘
              │                               │
┌─────────────▼──────────┐   ┌────────────────▼──────────────────────────────┐
│ SMALL MODEL PATH       │   │ LARGE MODEL PATH                              │
│                        │   │                                                │
│ ┌────────────────────┐ │   │ ┌──────────────────────────────────────────┐  │
│ │ Prompt Variant     │ │   │ │ Prompt Variant Registry                  │  │
│ │ Registry           │ │   │ │                                          │  │
│ │                    │ │   │ │ Sonnet variant (primary)                 │  │
│ │ Haiku variant      │ │   │ │ GPT-4.1 variant (fallback)              │  │
│ │ (zero-shot,        │ │   │ │ Each tuned to target model strengths    │  │
│ │  simpler prompts)  │ │   │ └──────────────────────────────────────────┘  │
│ └────────┬───────────┘ │   │                     │                         │
│          │             │   │ ┌────────────────────▼───────────────────────┐ │
│ ┌────────▼───────────┐ │   │ │ Multi-Tier Cache                          │ │
│ │ Direct inference   │ │   │ │                                           │ │
│ │ (no caching --     │ │   │ │ Semantic cache (31% hit rate, 100% save) │ │
│ │  already cheap)    │ │   │ │     │                                     │ │
│ └────────┬───────────┘ │   │ │ Prefix cache (Anthropic 90% discount,    │ │
│          │             │   │ │  target 75%+ hit rate via identical       │ │
└──────────┼─────────────┘   │ │  system prompts across all users)        │ │
           │                 │ │     │                                     │ │
           │                 │ │ Full inference (cache miss path)          │ │
           │                 │ └────────────────────┬──────────────────────┘ │
           │                 └──────────────────────┼────────────────────────┘
           │                                        │
┌──────────▼────────────────────────────────────────▼────────────────────────┐
│                      LLM GATEWAY (shared)                                  │
│                                                                            │
│  ┌────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Per-Provider    │  │ Fallback Chain   │  │ Output Validation        │   │
│  │ Circuit Breaker │  │                  │  │                          │   │
│  │                 │  │ Primary: Sonnet  │  │ Constrained decoding     │   │
│  │ Sonnet: CLOSED  │  │ Fallback: GPT-4.1│  │ (~0% syntactic failure)  │   │
│  │ GPT-4.1: CLOSED │  │ Degraded: cached │  │ Pydantic retry on        │   │
│  │ Haiku: CLOSED   │  │   response or    │  │ semantic errors          │   │
│  │                 │  │   user-facing     │  │                          │   │
│  │ 5 failures ->   │  │   error message  │  │ LLM-as-Critic for       │   │
│  │ OPEN (60s)      │  │                  │  │ response generation      │   │
│  └────────────────┘  └──────────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────┬────────────────────────────────┘
                                           │
┌──────────────────────────────────────────▼────────────────────────────────┐
│                      COST & QUALITY DASHBOARD                             │
│                                                                           │
│  Per-task cost tracking  |  Cache hit rate (target: 75%+)  |  Quality    │
│  scores per model variant  |  Fallback rate (target: <1%)  |  p95        │
│  latency per path  |  A/B test results (variant quality comparison)      │
└──────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Decision               │ Option A              │ Option B                    │
├────────────────────────┼───────────────────────┼─────────────────────────────┤
│ Cost reduction method  │ Caching on large model│ Downgrade all traffic       │
│                        │ + task-based routing  │ to small model              │
│                        │                       │                             │
│ CHOSEN: Hybrid         │ WHY: Sonnet+cache     │ TRADE-OFF: more operational │
│ (cache + routing)      │ ($240/mo) beats       │ complexity than single      │
│                        │ Haiku no cache         │ model deployment            │
│                        │ ($1,600/mo) at 6.7x   │                             │
│                        │ better cost-quality    │                             │
├────────────────────────┼───────────────────────┼─────────────────────────────┤
│ Prompt variant mgmt    │ Model-specific        │ Universal prompt            │
│                        │ variants in registry  │ (one prompt, all models)    │
│                        │                       │                             │
│ CHOSEN: Model-specific │ WHY: same prompt      │ TRADE-OFF: N variants to    │
│                        │ scores 35% on Mistral │ maintain per task (eval     │
│                        │ vs. 80% on GPT-4o-mini│ cost scales linearly)       │
│                        │ -- universal prompts  │                             │
│                        │ leave 45% accuracy    │                             │
│                        │ on the table          │                             │
├────────────────────────┼───────────────────────┼─────────────────────────────┤
│ Prompting technique    │ Zero-shot for simple  │ CoT/few-shot everywhere     │
│                        │ tasks, few-shot for   │                             │
│                        │ complex only          │                             │
│                        │                       │                             │
│ CHOSEN: Task-matched   │ WHY: zero-shot scores │ TRADE-OFF: requires task    │
│                        │ 93.1% on modern       │ classification accuracy     │
│                        │ models (within 1% of  │ to avoid misrouting         │
│                        │ CoT at 40% fewer      │                             │
│                        │ tokens)               │                             │
├────────────────────────┼───────────────────────┼─────────────────────────────┤
│ Availability strategy  │ Multi-provider        │ Single provider with        │
│                        │ fallback chain        │ retries                     │
│                        │                       │                             │
│ CHOSEN: Multi-provider │ WHY: 99.9% SLA        │ TRADE-OFF: prompt variants  │
│                        │ requires independence │ needed per provider,        │
│                        │ from single-provider  │ more complex billing        │
│                        │ outages               │                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Decision rationale.** The hybrid approach (caching + task-based routing) targets a 70%+ cost reduction: simple tasks (classification, moderation -- ~60% of volume) route to Haiku-class models at 1/10th the per-token cost, while complex tasks (generation, summarization) stay on Sonnet with aggressive prefix caching (targeting 75%+ hit rate, matching ProjectDiscovery's 84% result). Model-specific prompt variants are required because empirical data shows 45-point accuracy swings between models on identical prompts. The multi-provider fallback chain (Sonnet primary, GPT-4.1 fallback, cached/degraded tertiary) satisfies the 99.9% availability SLA by ensuring no single-provider outage causes user-facing failures. The cost target: from $1,700/month to under $500/month while maintaining or improving output quality, validated by per-task eval sets run on every prompt version change.

**Projected cost breakdown:**

```
  Simple tasks (60% of 2M/day):  Haiku @ $0.003/call = $3,600/mo
  Complex tasks (40% of 2M/day): Sonnet @ $0.022/call
    With 75% cache hit, 90% discount: effective $0.0072/call = $5,760/mo
  Total: ~$9,360/mo (vs. $1,700/mo current -- wait, current is uncached Sonnet)

  Correction: Current $1,700/mo implies ~77k calls/mo at $0.022/call.
  At 77k calls/mo with hybrid approach:
    Simple (60%): 46.2k * $0.003 = $138.60
    Complex (40%): 30.8k * $0.022 * (1 - 0.75*0.90) = $30.8k * $0.0072 = $221.76
  Total: ~$360/mo -- 79% cost reduction from $1,700.
```

---

## Key Interview Anchors

**"Why not always use the most advanced prompting technique?"** Zero-shot on modern models (2025+) scores 93.1% accuracy, within 1% of Chain-of-Thought, at 40% fewer tokens. Tree-of-Thought and Self-Consistency score 10-13 points LOWER on general QA benchmarks while burning 15-47x more tokens. Advanced techniques are surgical tools for specific domains (ToT: 74% vs. 4% CoT on Game of 24), not universal improvements. The value-per-token ratio drops precipitously beyond few-shot.

**"How do you handle prompt injection in production?"** Accept that prevention is impossible (OpenAI's public position, December 2025). Design for containment: architectural prevention (functions in code, least-privilege tool tokens, human approval for privileged ops) reduces blast radius. Runtime detection (input filtering + LLM-as-Critic output validation) adds +21% detection precision. Governance (audit trails, RBAC on prompt modification, compliance mapping) enables post-incident response. Layered together, these reduce attack success from 73.2% to 8.7%.

**"How do you manage prompt drift?"** Three concurrent detection methods: embedding-based for semantic drift, PSI/KL-divergence for data drift, CI/CD eval on fixed test sets for functional drift. Without monitoring, 35% error rate increase over 6 months. Migration protocol: fixed eval set from real production inputs, compare old vs. new model, re-tune prompt, small traffic slice rollout, keep old model reachable for rollback.

**"When do you use prompt caching vs. switching to a smaller model?"** Always evaluate caching on the larger model first. Sonnet with caching ($240/month) beats Haiku without caching ($1,600/month) -- 6.7x cost improvement while retaining full capability. The break-even for Anthropic caching is just 2 requests sharing the same prefix. Key optimization: static content first (system prompt, few-shot, tool defs), variable content last. Single-character changes in the prefix cause complete cache misses.
