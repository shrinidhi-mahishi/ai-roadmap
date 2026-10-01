# Module 07: Prompt Engineering

### What Is This?

Prompt engineering is the control surface for next-token prediction: you compose system instructions, examples, tool schemas, retrieved data, and user content into a shared context window, then constrain the model's generation through role hierarchy, output schemas, and placement strategy. Think of it as programming with natural language -- where the "function definition" is the system/developer message, the "arguments" are the user data, and the "return type" is enforced by structured output schemas. A prompt is not a search query or a magic incantation; it is a compiled artifact assembled at runtime from versioned templates, injected variables, and cache-aligned role ordering. Anthropic draws a further distinction between prompt engineering (how you write instructions) and context engineering (curating all inference-time tokens under a finite attention budget and O(n^2) pairwise attention cost).

---

## 1. System Topology & Data Flow

A production prompt engineering system spans five cooperating layers: a **prompt registry** (control plane) managing versioned, immutable prompt artifacts with environment-based deployment; a **compilation pipeline** (data plane) assembling runtime prompts from templates, variables, retrieved documents, and conversation history; a **cache layer** eliminating redundant computation through semantic and prefix caching; an **LLM gateway** handling model routing, fallbacks, rate limiting, and circuit breaking; and a **telemetry layer** tracking drift, cost, latency, and compliance.

```
+--------------------------------------------------------------------------+
|                           CONTROL PLANE                                    |
|  Versioned prompt templates (prompt_id@semver, immutable)                 |
|  Schema registry (Pydantic/Zod per prompt version)                        |
|  Eval suite (fixed eval set, runs on every version change)                |
|  Tool-use rules, output schemas, safety + injection boundaries            |
|  developer/instructions policy, cache_control / prompt_cache_key          |
|  Environment labels: dev / staging / prod (rollback = re-label)           |
+--------+-----------------------+----------------------------+------------+
         |                       |                            |
         v                       v                            v
+--------------------------------------------------------------------------+
|              DATA PLANE (PROMPT COMPILATION PIPELINE)                       |
|  TEMPLATE_RESOLVED -> INJECTED -> BUDGET_CHECKED -> ROLE_ASSEMBLED         |
|  -> CACHE_ALIGNED                                                          |
|                                                                            |
|  Load versioned template by env label                                      |
|  -> Inject variables (user data, RAG docs, tool defs, history)             |
|  -> Token budget check (sys + inst + few-shot + docs + hist + user + out   |
|     < context window; if exceeded: trim examples, compress history,        |
|     rerank/prune docs, or reject with structured error)                    |
|  -> Role assembly: Anthropic 10-part framework (identity, style,           |
|     reference, instructions, examples, context, docs, history, format,     |
|     verification); developer/instructions outrank user content             |
|  -> Cache alignment: static prefix first, volatile suffix last             |
|  -> PII detect -> redact -> audit (before any log or model entry)         |
+--------+-----------------------+----------------------------+------------+
         |                       |                            |
         v                       v                            v
+--------------------------------------------------------------------------+
|                      CACHE LAYER (MULTI-TIER)                              |
|  L1: Semantic cache (embedding similarity, ~31% hit rate, 100% savings)   |
|  L2: Prefix cache (exact prefix match; Anthropic 90% off / OpenAI 50%)   |
|  L3: Full inference (cache miss path, full-cost API call)                 |
|  Scope: per-tenant prompt_cache_key; invalidate on prefix change          |
+--------+-----------------------+----------------------------+------------+
         |                       |                            |
         v                       v                            v
+--------------------------------------------------------------------------+
|                      LLM GATEWAY                                           |
|  Model router (model-specific prompt variant selection)                    |
|  Circuit breaker (per-provider: CLOSED -> OPEN -> HALF_OPEN)              |
|  Output validator (constrained decoding + Pydantic + LLM-as-Critic)       |
|  Fallback chain (primary -> secondary -> deterministic parser)            |
+--------+-----------------------+----------------------------+------------+
         |                       |                            |
         v                       v                            v
+--------------------------------------------------------------------------+
|                 TELEMETRY / OBSERVABILITY                                   |
|  Prompt drift monitor (embedding + PSI + LLM-as-Judge)                    |
|  Per-technique cost tracker ($/call by technique)                          |
|  Cache hit rate dashboard | p50/p95/p99 TTFT + total latency              |
|  Accuracy vs baseline | Injection attempt log | Model migration reports  |
|  Compliance mapping (OWASP, EU AI Act, ISO 42001)                         |
+--------------------------------------------------------------------------+
```

### Request-flow narrative

1. **Assemble prompt (control + data).** Load versioned template (`prompt_id@semver`) by environment label. Compose privileged instructions: Identity -> Instructions -> Examples -> Context (volatile context last). Attach tool schemas and JSON Schema for structured output. **Never interpolate untrusted user/web/tool text into `developer` / `instructions`** -- pass untrusted content as user data so it cannot claim developer privilege.
2. **PII gate (before any log or model entry).** Detect -> redact -> audit. Replace with stable tokens (`[PII:email:3f2a]`); keep mapping in sealed vault. Never place raw customer PII in the stable cacheable prefix shared across tenants.
3. **Token budget check.** Fully-injected prompt + expected output must fit within context window. If budget exceeded, enter recovery: (a) trim few-shot examples by relevance, (b) compress/summarize conversation history, (c) rerank and prune retrieved documents, (d) if still over, reject with structured error -- never silently truncate.
4. **Cache lookup.** Vendor checks longest common prefix against machine-local prompt cache. Minimum cacheable prefix: **1,024 tokens** (Claude Sonnet 5, GPT-5.6+); **4,096 tokens** (Claude Opus 4.5/Haiku 4.5). Hit -> charge at 0.1x; miss -> full prefill + optional 1.25x write surcharge. A **single changed character** anywhere in the cached prefix causes a complete miss.
5. **Model decode.** Prefill (or reuse KV) then generate. Tool-using turns may emit `tool_use`; tool proxies execute under RBAC and return `tool_result` into the data plane.
6. **Structured parse + validation.** Constrained decoding / `strict` schema adherence: **100%** schema adherence on OpenAI's published complex JSON-schema eval for `gpt-4o-2024-08-06` (vs **93%** unconstrained, vs **<40%** for `gpt-4-0613`). Schema adherence does NOT equal semantic correctness -- app-level validators still check values. On failure -> retry-with-schema correction -> deterministic parser fallback.
7. **Telemetry sink.** Emit correlation ID, prompt version hash, cache hit/miss tokens, TTFT, parse/refusal outcome, tool RBAC decisions.

---

## 2. Core Mechanics & Algorithms

### 2.1 Prompt as a Component Graph

A prompt is a composable component graph, not one blob: instruction, context, constraints, examples, output format, and (for agents) tool-use decision rules. Diagnose failures by missing/unclear component rather than rewriting everything. Anthropic's "Goldilocks altitude": specific enough to steer, not brittle if-else trees or vague slogans.

`developer` / `instructions` outrank `user` content -- analogous to a function definition vs. its arguments. On o1-era and newer Chat Completions APIs, `developer` replaces prior `system` for this privileged role.

### 2.2 Prompt Compilation as a Finite-State Pipeline

```
TEMPLATE_RESOLVED -> INJECTED -> BUDGET_CHECKED -> ROLE_ASSEMBLED -> CACHE_ALIGNED

State transitions and invariants:
- TEMPLATE_RESOLVED -> INJECTED: All variables must resolve. Missing = fail loudly.
  Schema validation (Pydantic/Zod) enforces type/format on injected values.
- INJECTED -> BUDGET_CHECKED: Total tokens < context window minus expected output.
  Overflow triggers budget recovery subroutine (trim, compress, prune, or reject).
- BUDGET_CHECKED -> ROLE_ASSEMBLED: Content assigned to roles per Anthropic 10-part
  framework. developer/instructions outrank user content.
- ROLE_ASSEMBLED -> CACHE_ALIGNED: Static content (system, few-shot, tools) moved
  to prefix. Variable content (user, query) appended last. Economic optimization
  maximizing prefix cache hit rate.

Complexity: O(T + D log D) per request where T = total tokens, D = retrieved chunks.
```

### 2.3 Message Role Semantics and Model-Specific Behavior

| Role | Content Type | Cache Behavior | Attention Weight |
|---|---|---|---|
| **System** | Persona, tool defs, guardrails, constraints | Cacheable (static prefix) | High (beginning-of-context bias) |
| **User** | Task instructions, input data, queries | Variable (low cache hit) | High (end-of-context bias) |
| **Assistant** | Pre-filled responses, format steering, few-shot | N/A (output) | Format-steering anchor |

**Model-specific behavioral differences (interview-critical):**
- **Claude 4.x+**: Takes instructions literally. Does exactly what is asked, nothing more. Vague prompts produce minimal output. "Lazy model" reports are usually effort-too-low, not a prompt problem.
- **GPT reasoning models (o1, o3)**: Chain-of-thought built in. Explicit "think step by step" is redundant and can degrade performance. Focus on clearly stating the problem and desired output.
- **Non-reasoning models**: Explicit CoT yields measurable gains. But diminishing returns: reasoning models (o3-mini, o4-mini) gain only 2.9-3.1% from explicit CoT while requiring 20-80% more time.

### 2.4 In-Context Learning (ICL)

| Mode | Definition | Production Note |
|---|---|---|
| **Zero-shot** | Instruction only | Baseline cost/latency; 93.1% accuracy on modern models |
| **One-shot** | One exemplar | Format anchoring |
| **Few-shot** | 3-5 well-crafted examples (Anthropic rec) | Diverse > near-duplicates; exemplar ORDER matters hugely |

**Exemplar order sensitivity:** GPT-3 SST-2 accuracy swings from **54.3% to 93.4%** under different few-shot permutations (Zhao et al., cited in Wei). Treat prompts as versioned code with eval suites, not as strings you tweak ad hoc.

### 2.5 Attention Economics and the Degradation Threshold

**The 3,000-token degradation threshold.** Research found LLM reasoning performance starts degrading around 3,000 tokens of prompt instruction -- well below technical context window maximums (128K-1M tokens). Practical sweet spot: 150-300 words (~200-400 tokens). More content does NOT mean better results; it means diluted attention.

**Lost-in-the-middle effect.** Instructions in the middle of long contexts receive less attention than those at the beginning or end. Production mitigation: place critical instructions at start (system prompt) and end (final user message). Bury reference material in the middle where lower attention is acceptable.

**Negative instruction failure.** "Don't use markdown" is less reliable than "Use flowing prose paragraphs." Models attend to the semantic content of instructions, so negative framing ("don't X") activates the representation of X. Positive reframing eliminates the ambiguity.

**Cache-layout vs long-doc-top conflict:** For cache-critical high-RPM classifiers, prefer stable-prefix-first layout. For one-shot long-doc Q&A, prefer Anthropic long-doc placement (longform data near top, query/instructions below) and accept miss cost.

### 2.6 Reasoning & Tool-Loop Patterns

| Pattern | Mechanism | Published Signal |
|---|---|---|
| **Chain-of-thought (CoT)** | Exemplars with intermediate steps | PaLM 540B GSM8K ~57-58% CoT; **more than doubled** vs standard prompting. Emergent >~100B params. |
| **Self-consistency** | Majority vote over sampled CoT paths | PaLM 540B GSM8K **74%** (+12-18% over CoT on high-stakes) |
| **Zero-shot CoT** | "Let's think step by step" | InstructGPT MultiArith **17.7% -> 78.7%**; GSM8K **10.4% -> 40.7%** |
| **ReAct** | Thought -> Action -> Observation | ALFWorld **71%** vs Act-only **45%**; WebShop **40%** vs Act **30.1%** |
| **Structured Outputs** | Constrained decoding to schema | **100%** schema adherence on OpenAI eval; `gpt-4-0613` was **<40%** |
| **Tree-of-Thought** | Branch-and-bound exploration | Game of 24: **74%** vs CoT **4%** (domain-specific) |

CoT is emergent with scale: below ~100B parameters, fluent but illogical chains can hurt vs standard prompting. Gains largest on hard multi-step sets, near-zero/negative on easy single-op subsets. Add reasoning tokens only when the task needs them.

### 2.7 Prompting Technique Decision Tree

```
Is this a reasoning-native model (o1, o3, DeepSeek-R1)?
  YES -> Simple direct prompt. No CoT, no few-shot needed.
  NO  -> Does the task need multi-step reasoning?
    YES -> CoT prompting (+1.5x tokens, <1% accuracy gain on modern models)
    NO  -> Is format/style control critical?
      YES -> Few-shot (3-5 examples, 1.3x tokens)
      NO  -> Zero-shot (93.1% accuracy, 1x tokens)

Is this high-stakes with clear rubric?
  YES -> Self-Consistency (10x tokens, +12-18% over CoT)
  NO  -> Does it involve tool use?
    YES -> ReAct or ReWOO (ReWOO: 64% fewer tokens, +4.4% accuracy vs ReAct)
    NO  -> Standard techniques above
```

### 2.8 Prompt Chaining: Accuracy Compounding

Prompt chaining decomposes complex tasks into focused steps. Research (Chainer, 2025): optimal decomposition depth is **2.8-3.2 steps**. +15.6% accuracy vs monolithic prompts.

**Accuracy compounding formula:** For n steps each with per-step accuracy p, end-to-end accuracy is p^n.

| Per-step accuracy | 3 steps | 5 steps | 10 steps |
|---|---|---|---|
| 99% | 97.0% | 95.1% | 90.4% |
| 95% | 85.7% | 77.4% | 59.9% |
| 90% | 72.9% | 59.0% | 34.9% |
| 85% | 61.4% | 44.4% | 19.7% |

**Mandatory inter-step gates:** Programmatic validation (schema, required fields, policy thresholds, human approval) between steps is not optional. Without gates, 95%-accurate 5-step chain delivers 77.4% E2E. With gates that catch and retry failures, effective per-step accuracy rises to 99%+, yielding 95.1% E2E.

### 2.9 Structured Output Reliability Tiers

| Tier | Mechanism | Failure Rate | Status |
|---|---|---|---|
| Prompt-only ("respond in JSON") | Hope-based | 5-20% | Never use |
| JSON Mode | Valid syntax only | 1-5% | Legacy |
| Schema-enforced (constrained decoding) | FSM masks invalid tokens at generation | ~0% syntactic | **Prod default** |

74% of LLM production applications use structured output (2026), up from ~40% two years ago. Instructor library: 3M+ monthly downloads, provider-neutral Pydantic validation with automatic retry.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Per Technique (Empirical Data)

| Technique | $/call | Token Multiple | Accuracy (general) | Value/token |
|---|---|---|---|---|
| Zero-shot | $0.015 | 1.0x | 93.1% | Baseline (best) |
| Few-shot (3-5 ex.) | $0.019 | 1.3x | ~94% | ~0.97x baseline |
| Chain-of-Thought | $0.022 | 1.5x | ~94% | ~0.67x baseline |
| ReAct | $0.040 | 2.7x | High | ~0.37x baseline |
| Self-Consistency (5) | $0.154 | 10x | ~95-99%* | ~0.10x baseline |
| Tree-of-Thought | $0.700 | 47x | Domain** | ~0.02x baseline |

*Self-Consistency: +12-18% over CoT on high-stakes decisions.*
**ToT: 74% on Game of 24 vs 4% CoT (domain-specific win).*

**Key finding:** Zero-shot on modern models scores within 1% of CoT while using 40% fewer tokens. Few-shot and zero-shot deliver 30x more value per token than ToT or Self-Consistency for typical tasks.

### 3.2 Prompt Caching Economics

| Provider | Mechanism | Read Discount | Write Surcharge | Min Prefix |
|---|---|---|---|---|
| **Anthropic** | Explicit cache_control | 90% (0.1x) | 25% (1.25x) for 5m; 100% (2x) for 1h | 1,024 tokens (Sonnet 5); 4,096 (Opus 4.5/Haiku 4.5) |
| **OpenAI** | Automatic (GPT-5.6+) | 50-95% (0.1x-0.05x) | 25% (1.25x) | 1,024 tokens |
| **Google** | Explicit | Up to 75% | Yes | Varies |
| **vLLM/SGLang** | Automatic prefix | Free (self-hosted) | N/A | N/A |

**Break-even:** 2+ requests sharing the same prefix make Anthropic caching cost-positive. Ten requests (1 write + 9 reads) at 0.1x = 2.15x total vs 10x uncached.

**Real-world case study:** Sonnet with caching ($240/month) beats Haiku without caching ($1,600/month) -- **6.7x cost improvement** while retaining full capability. This is counterintuitive: caching on a larger model is cheaper than running a smaller model without caching.

**ProjectDiscovery:** Raised cache hit rate from **7% to 84%**, cutting total LLM spend by **59-70%**.

### 3.3 Cost Formula -- $ per 1k runs

Model: Claude Sonnet 5 (base input $2/MTok, 5m write $2.50, hit $0.20, output $10/MTok). Shape: 20K stable prefix, 2K volatile user tokens, 1K output. Cache hit fraction f=0.90.

```
C_uncached = (22K/1M) * $2 + (1K/1M) * $10 = $0.044 + $0.010 = $0.054/run
C_warm     = (20K/1M) * $0.20 + (2K/1M) * $2 + (1K/1M) * $10 = $0.018/run
C_write    = (20K/1M) * $2.50 = $0.050 (prefix write alone)

Steady-state (f=0.9, amortize 1 write every 10 runs):
C_1k = 1000 * [0.1 * ($0.050 + $0.004 + $0.010) + 0.9 * $0.018]
     = 1000 * [$0.0064 + $0.0162] = $22.60 / 1k runs

Fully cold (f=0): $54 / 1k runs
Fully warm (f=1): $18 / 1k runs
```

~67% turn-cost reduction when warm vs fully uncached.

### 3.4 Latency SLA Targets

| Metric | p50 Target | p95 Target | p99 Target |
|---|---|---|---|
| TTFT (cached prefix) | 150ms | 400ms | 800ms |
| TTFT (uncached) | 500ms | 1,500ms | 3,000ms |
| Total latency (zero-shot) | 800ms | 2,000ms | 4,000ms |
| Total latency (CoT) | 1,500ms | 4,000ms | 8,000ms |
| Total latency (reasoning model) | 5,000ms | 15,000ms | 30,000ms |
| Prompt compilation | <10ms | <25ms | <50ms |
| Cache lookup | <5ms | <15ms | <30ms |
| Output validation | <20ms | <50ms | <100ms |

Prefill dominates TTFT on long prompts. Cache hits skip recomputing KV for the cached prefix: TTFT improvements track prefix length and hit rate more than decode TPS. Anthropic cookbook reports **~2-10x** wall-clock speedup on large-prefix cache hits.

### 3.5 Throughput & Back-Pressure

| Lever | Behavior |
|---|---|
| **Vendor RPM/TPM** | Hard ceilings; OpenAI: traffic above ~15 RPM can overflow machine-local cache routing; use stable `prompt_cache_key` |
| **Token-bucket per tenant** | Admit r req/s; queue or 429 when full |
| **Spend ceiling** | Hard abort when projected burn exceeds budget |
| **Tool RPM** | Separate bulkhead from model RPM; encode max-N tool calls in prompt AND enforce in executor |
| **Shed load** | Under pressure: disable CoT/thinking, shrink few-shots, route to Haiku-class, or deterministic fallback |

**Capacity sketch:** At p50=200ms service time and 50% concurrency utilization, one worker = ~2.5 RPS; 100 workers = ~250 RPS before model quota.

### 3.6 NFR Requirements

| NFR | Target / Posture |
|---|---|
| **Availability** | 99.9% with multi-model fallback; cache miss must not be an outage |
| **RPO** | Versioned templates in git/registry -> seconds-minutes; ephemeral vendor cache RPO = N/A |
| **RTO** | Redeploy last known-good `prompt_id@semver` -> minutes; cold-cache ramp -> TTL-dependent |
| **Compliance** | No secrets/PII in cacheable system prefixes; per-tenant cache keys; immutable prompt-version audit |

### 3.7 Caching vs. Model Selection Decision

| Strategy | Monthly Cost (example) | Quality |
|---|---|---|
| Large model + caching (Sonnet + cache) | $240 | High |
| Large model, no caching | $1,700+ | High |
| Small model, no caching (Haiku) | $1,600 | Lower |

**Decision: Always evaluate caching on the larger model before downgrading to a smaller model.** The cost-quality frontier usually favors caching.

---

## 4. Distributed Resilience & Security

### 4.1 Prompt Versioning and Durable Execution

**Version lifecycle state machine:**

```
DRAFT -> commit -> STAGED -> eval pass -> CANDIDATE -> approve -> PROD
                     |                       |                     |
                 eval fail               eval fail             rollback
                     v                       v                     v
                  REJECTED               REJECTED         re-label to prior
```

**Production workflow:**
1. Define input/output schema (Pydantic/Zod) first -- schema-first development.
2. Build prompt around schema, version as immutable artifact.
3. Run evaluation suite on every version change (CI/CD integration).
4. Deploy via environment labels (dev -> staging -> prod) with feature flags.
5. Monitor with observability dashboards for drift detection.
6. Rollback = re-point environment label to previous version (instant, no redeployment).

**Model pinning:** Pin to specific model snapshots (e.g., `gpt-4.1-2025-04-14`). Unpinned model versions silently migrate, causing prompt drift.

### 4.2 Prompt Drift Detection

**What drifts:** The same prompt produces different outputs over time even when unchanged. A classification task at 95% accuracy can hover around 80% months later. Without monitoring, models left unchanged for 6+ months saw **error rates jump 35%** on new data (2025 LLMOps report).

| Drift Type | Cause | Detection Method |
|---|---|---|
| **Model drift** | Silent API model updates | CI/CD eval on fixed test set, nightly builds |
| **Data drift** | New user segments, seasonal patterns | PSI, KL-Divergence on input distribution |
| **Semantic drift** | Subtle meaning shifts in I/O | Embedding-based distance metrics |
| **Cascade drift** | Upstream prompt in chain updated | LLM-as-Judge comparing drifted samples to baseline |
| **Deprecation drift** | Unpinned model version silently migrated | Version monitoring, provider changelog alerts |

**Migration best practice:** Build a fixed migration eval set from real production inputs. Run through both old and new model. Compare every difference. Re-tune prompt for new model. Roll out to small traffic slice first. Keep old model reachable for rollback.

### 4.3 Prompt Injection: Threat Landscape

**Threat status:** OWASP LLM01. Attack success rates: **50-84%** depending on configuration. No complete fix exists as of 2026. OpenAI publicly acknowledged (December 2025) that prompt injection, like social engineering, is unlikely to ever be fully solved. The realistic goal is containment, not prevention.

| Attack Type | Vector | Trend |
|---|---|---|
| **Direct injection** | User crafts input overriding system prompt | Declining (better instruction hierarchy) |
| **Indirect injection** | Malicious instructions in documents/emails/web the model reads | **Dominant vector in 2026** (multi-hop via agents +70% YoY) |
| **Multi-hop indirect** | Chained across agents and tools | **Fastest-growing attack surface** |

**Real-world CVEs:**
- **EchoLeak (CVE-2025-32711):** CVSS 9.3. Zero-click: crafted email caused Microsoft 365 Copilot to retrieve internal files and forward to attacker.
- **MCP Server (CVE-2025-68143/44/45):** Critical. Anthropic's own Git MCP server -- attacker influences what AI reads to trigger code execution.
- **GitHub Copilot:** CVSS 9.6. Active production exploitation.
- **Cursor IDE:** CVSS 9.8. Active production exploitation.

**Measured success rates (system cards):**
- Claude Opus 4.6: 17.8% single attempt, **78.6% across 200 attempts** without safeguards, 57.1% with published defenses.
- Google Gemini: 53.6% after adversarial fine-tuning.

### 4.4 Layered Defense Framework

Defense frameworks reduce attack success from **73.2% to 8.7%** when layered properly.

| Layer | Controls |
|---|---|
| **L1: Architectural Prevention** | Handle functions in code, NOT delegated to model. Scope each tool to least privilege. Require human approval for privileged ops. Separate data plane from control plane. Hard limits on tool call counts. |
| **L2: Runtime Detection** | Input filtering (pattern-based + semantic classifiers). LLM-as-Critic output validation: **+21% detection precision** over input-only. Confidence scoring -> low-certainty routes to human review. Output schema enforcement via constrained decoding. |
| **L3: Governance** | Audit trails for every prompt/response pair. Role-based access to prompt modification. Compliance: OWASP, MITRE ATLAS, NIST, EU AI Act (Aug 2026 deadline), ISO 42001, GDPR, NIS2. |

**Emerging defenses:** SecAlign (misses ~10% of optimization-based attacks). ReasAlign (Jan 2026): cuts attack success to 3.6% on one benchmark, but static benchmarks, not adaptive attackers.

**Enterprise readiness gap:** 83% of organizations plan agentic AI, only 29% feel ready to secure it (Cisco State of AI Security 2026). Only 34.7% have deployed dedicated prompt injection defenses.

### 4.5 Zero-Trust MCP (When Tools Are in the Prompt)

Tool schemas describe capability; they are NOT a trust boundary.

| Control | Requirement |
|---|---|
| **Authenticate** | Every MCP server: mTLS or signed tokens |
| **Authorize** | Executor checks RBAC before side effects; `strict: true` constrains argument shape only |
| **Data-as-data** | MCP/tool_result payloads enter as user/tool data, never merged into developer instructions |
| **Network** | Egress allowlists; no ambient credentials in model-visible prompt |
| **Confirm** | HITL for high-impact tools (payments, deletes, outbound email) |

### 4.6 Hallucination Mitigation Ladder

Ordered by implementation complexity and effectiveness:
1. **Temperature 0** for factual tasks (40-60% hallucination reduction)
2. **Explicit permission** to say "I don't know"
3. **Quote extraction before reasoning** (Anthropic's single most effective mitigation)
4. **Self-verification step** appended to prompt
5. **RAG with source grounding**
6. **Output validation via LLM-as-Critic**

### 4.7 Failure Taxonomy

| Class | Examples | Handling |
|---|---|---|
| **Transient** | 429/5xx, timeout, cache miss storm, truncation | Exponential backoff + jitter; circuit breaker; retry same idempotency key |
| **Permanent** | Auth failure, unsupported schema, policy refusal | Fail closed; do not blind-retry; surface refusal channel |
| **Poison pill** | Input always yields invalid semantic JSON / infinite tool loop | Max attempts -> DLQ; quarantine; require prompt/schema fix |
| **Prompt brittleness** | Exemplar-order swings; model upgrade over/under-triggers tools | Pin snapshots; regression evals; retune system language |
| **Injection / abuse** | User text overrides instructions; tool exfil | Role isolation; least-privilege tools; confirmations |

### 4.8 Prompt Management Platforms (2026)

Market consolidation: Humanloop wound down, Helicone acquired into maintenance, Vellum pivoted to consumer, PromptHub winding down.

| Platform | Key Differentiator |
|---|---|
| **Braintrust** | Versioning tied to evaluation infrastructure; environment-based deployment |
| **PromptLayer** | Non-technical team access; A/B testing by user segment |
| **Confident AI** | Git-based prompt management; branching, commit history, approvals; ISO 42001 |
| **Langfuse** | Open-source (MIT); linear versioning with labels; deep observability |
| **Portkey** | Gateway-layer management; runtime template serving, routing, fallbacks, caching |

**OpenAI's stance shift:** Deprecating reusable prompt objects. `v1/prompts` shutting down November 30, 2026. Their recommendation: store production prompts in application code.

---

## 5. Failure Modes

### Common Failure Modes Table

| # | Failure Mode | Severity | Detection | Mitigation |
|---|---|---|---|---|
| 1 | Ambiguous prompt / missing components | Medium | Model invents audience, format | Component checklist: instruction + context + constraints + format |
| 2 | Attention / context rot | High | Ignores mid-prompt facts | Fewer high-signal tokens; long docs top + quote-then-answer; compaction |
| 3 | Bad / non-diverse few-shots | Medium | Systematic class misses | Curate diverse canonical examples; avoid near-duplicates |
| 4 | Exemplar-order / prompt brittleness | High | Large accuracy swings (54-93%) | Eval suites; pin model snapshots; version prompts as code |
| 5 | CoT on wrong tasks | Medium | Longer wrong answers; higher $ | Use CoT only on multi-step; skip on trivial extract/classify |
| 6 | Tool loops | High | Repeated identical searches; runaway cost | Max calls + success criteria + stop condition in prompt + hard app caps |
| 7 | Invalid / partial JSON | Medium | Downstream parse crashes | Structured Outputs / strict tools; handle refusal and truncation |
| 8 | Hallucinated tool parameters | High | 4xx from APIs; invented IDs | Descriptive JSON schemas; strict:true; poka-yoke args |
| 9 | Prompt injection | Critical | Policy bypass; data exfil via tools | Role isolation; structured handoffs; least privilege; confirmations |
| 10 | Cache miss storms | High | Cost/latency spikes | Stable keys, >=15 RPM key partitioning, prefix >= minimum tokens |
| 11 | Prompt drift (6-month silent rot) | High | 35% error rate jump | Scheduled eval; PSI/embedding monitoring; pin model snapshots |
| 12 | Over/under-trigger tools after upgrade | Medium | Wrong aggressiveness | Retune system language; newer Claude may overtrigger old "CRITICAL: MUST" phrasing |

---

## 6. System Design Scenarios

### Scenario 1: Enterprise Prompt Management Platform for Regulated Financial Services

**Problem:** A financial services firm (500+ employees, SOC 2 Type II, PCI-DSS) operates 40+ LLM-powered applications with prompts scattered across codebases. No version control, no evaluation baseline, no audit trail. A prompt change in fraud detection went undetected for 3 weeks, causing 23% increase in false negatives. EU AI Act August 2026 deadline requires full prompt audit trails for high-risk AI systems.

**Architecture:**

```
GOVERNANCE LAYER:
  RBAC + Approval workflows | Compliance dashboard (OWASP, EU AI Act, ISO 42001)
  Immutable audit log (hash-chained, 7-year retention)
     |
PROMPT REGISTRY (Git-based, Confident AI model):
  Branching: main (prod) <- staging <- feature branches
  Every commit triggers eval suite (CI/CD)
  Merge requires: (a) eval score >= baseline (b) human reviewer approval
  Environment labels: dev / staging / prod (rollback = re-label)
  Schema registry: Pydantic schemas co-versioned with each prompt
     |
EVALUATION ENGINE:
  Per-domain eval sets (fraud 500 cases, support 300 dialogues, underwriting 200)
  Multi-metric: accuracy, format compliance, latency, cost, safety
  Gate: all metrics must meet thresholds
     |
DRIFT MONITORING:
  Nightly: full eval suite against prod prompts
  Weekly: PSI on input distributions, embedding drift on I/O pairs
  Alert: PSI > 0.1 -> WARN, PSI > 0.25 -> PAGE, eval drop > 5% -> BLOCK
```

**Trade-off matrix:**

| Decision | Chosen | Why | Trade-off |
|---|---|---|---|
| Prompt storage | Git-based (Confident AI) | Regulatory audit trail, approval workflows required | Extra tooling, platform dependency |
| Eval trigger | Every commit (CI/CD) | Prevents regression deploy (3-week fraud incident) | Higher compute cost, longer CI pipeline |
| Model pinning | Pinned snapshots | Prevents silent drift, reproducible eval results | Manual migration effort every quarter |
| Injection defense | Full 3-layer stack | Reduces attack success 73.2% -> 8.7% | 15-20% added latency, operational complexity |

### Scenario 2: Multi-Model Prompt Optimization for High-Volume Consumer AI

**Problem:** Consumer AI product handling 2M requests/day across four use cases: intent classification, response generation, content moderation, document summarization. Currently running all traffic through single large model (Claude Sonnet) at $1,700/month without caching. Need: (a) cut costs by 60%+, (b) support model-specific prompt variants (same prompt scores 35% on Mistral-7B vs 80% on GPT-4o-mini for few-shot), (c) 99.9% availability SLA.

**Architecture:**

```
REQUEST ROUTER (rule-based, zero LLM latency):
  Intent classification + content moderation -> Haiku-class (cheap/fast)
  Response generation + document summary -> Sonnet-class (capable)
     |
Each path: Model-specific prompt variants in registry
     |
MULTI-TIER CACHE (large model path only):
  Semantic cache (31% hit rate, 100% savings on hit)
  -> Prefix cache (Anthropic 90% discount, target 75%+ hit rate)
  -> Full inference (miss path)
     |
LLM GATEWAY (shared):
  Per-provider circuit breakers (5 failures -> OPEN, 60s recovery)
  Fallback chain: Sonnet (primary) -> GPT-4.1 (fallback) -> cached/degraded
  Output validation: constrained decoding + Pydantic retry + LLM-as-Critic
```

**Trade-off matrix:**

| Decision | Chosen | Why | Trade-off |
|---|---|---|---|
| Cost method | Hybrid (cache + routing) | Sonnet+cache ($240/mo) beats Haiku no cache ($1,600/mo) at 6.7x | More operational complexity |
| Prompt variants | Model-specific in registry | Same prompt scores 35% vs 80% across models -- universal prompts leave 45% accuracy on table | N variants to maintain (eval cost scales linearly) |
| Technique | Task-matched (zero-shot simple, few-shot complex) | Zero-shot scores 93.1% at 40% fewer tokens than CoT | Requires task classification accuracy |
| Availability | Multi-provider fallback chain | 99.9% SLA requires independence from single-provider outages | Prompt variants needed per provider |

**Projected cost:** From $1,700/month to ~$360/month (79% reduction). Simple tasks (60%): 46.2K * $0.003 = $139. Complex tasks (40%): 30.8K * $0.022 * (1 - 0.75 * 0.90) = $222.

---

## Key Takeaways for Interviews

1. **A prompt is a compiled artifact, not a string** -- it passes through a deterministic pipeline (template resolution -> variable injection -> budget check -> role assembly -> cache alignment) before reaching the model.
2. **Zero-shot on modern models scores 93.1%, within 1% of CoT at 40% fewer tokens.** Advanced techniques (ToT, Self-Consistency) score 10-13 points LOWER on general benchmarks while burning 15-47x more tokens. They are surgical tools for specific domains, not universal improvements.
3. **Always evaluate caching on the larger model before downgrading** -- Sonnet with caching ($240/mo) beats Haiku without caching ($1,600/mo) at 6.7x better cost-quality.
4. **Prompt injection is OWASP LLM01 with 50-84% success rates.** No complete fix exists. Design for containment: layered defenses reduce attack success from 73.2% to 8.7%.
5. **Prompt drift is the invisible killer** -- without monitoring, 35% error rate increase over 6 months. Three concurrent detection methods: embedding-based, PSI, CI/CD eval.
6. **Schema adherence does not equal semantic correctness** -- Structured Outputs give 100% schema compliance but wrong values inside valid JSON still need app-level validation.
7. **developer/instructions outrank user content** -- never interpolate untrusted text into the privileged message. This is the single most important architectural injection defense.
8. **Prompt chaining accuracy compounds as p^n** -- a 95%-accurate 5-step chain delivers only 77.4% E2E. Inter-step validation gates are mandatory, not optional.

## Interview Q&A

**Q1: How do you approach prompt engineering for a production system?**

A1: "I treat prompts as compiled artifacts, not strings. I start with schema-first development -- define the Pydantic input/output schema, then build the prompt around it. The prompt passes through a deterministic pipeline: template resolution, variable injection, token budget check, role assembly following Anthropic's 10-part framework, and cache alignment with static content first. I version prompts as immutable artifacts with environment labels, run evaluation suites on every change via CI/CD, and pin model snapshots to prevent drift. The key insight is that prompt engineering accounts for 30-40% of AI development time, so treating it as engineering rather than ad hoc tweaking has the highest ROI."

**Q2: When would you use Chain-of-Thought vs zero-shot?**

A2: "On modern models, zero-shot scores 93.1% -- within 1% of CoT -- while using 40% fewer tokens. I would use CoT only when the task genuinely requires multi-step reasoning, like math or complex logic chains. For simple classification, extraction, or reformatting, CoT is pure waste: it costs 1.5x more tokens for no accuracy gain. For reasoning-native models like o1 or o3, explicit CoT is actively counterproductive because reasoning is built in. The Wharton study found reasoning models gain only 2.9-3.1% from explicit CoT while requiring 20-80% more time."

**Q3: How do you handle prompt injection in production?**

A3: "I accept that prevention is impossible -- that is OpenAI's own public position. I design for containment across three layers. Layer 1, architectural prevention: handle functions in code with least-privilege tool tokens and human approval for privileged operations. Layer 2, runtime detection: input filtering plus LLM-as-Critic output validation, which adds 21% detection precision over input-only filtering. Layer 3, governance: audit trails, RBAC on prompt modification, compliance mapping. Layered together, these reduce attack success from 73.2% to 8.7%. The most critical single control: never interpolate untrusted text into the developer/system message."

**Q4: How do you manage prompt drift?**

A4: "Three concurrent detection methods. Embedding-based: track cosine distance shifts between baseline and current I/O pairs. Statistical: PSI and KL-divergence on input distributions -- PSI above 0.1 triggers investigation, above 0.25 triggers prompt re-tuning. CI/CD eval: fixed eval set from real production inputs, run nightly, fail pipeline when scores drop. Without this monitoring, production data shows 35% error rate increases over 6 months. For model migrations, I build a fixed migration eval set, compare old vs new model on every difference, re-tune the prompt, roll out to a small traffic slice first, and keep the old model reachable for rollback."

**Q5: When do you use prompt caching vs switching to a smaller model?**

A5: "Always evaluate caching on the larger model first. The counterintuitive result: Sonnet with caching at $240/month beats Haiku without caching at $1,600/month -- 6.7x cost improvement while retaining full capability. The break-even for Anthropic caching is just 2 requests sharing the same prefix. The key optimization: static content first in the prompt layout -- system instructions, few-shot examples, tool definitions -- then variable content last. ProjectDiscovery went from 7% to 84% cache hit rate and cut spend by 59-70%. But caching is fragile: a single changed character anywhere in the prefix causes a complete miss."

**Q6: How does structured output work as a security boundary?**

A6: "Constrained decoding via FSM achieves near-100% schema compliance -- the model physically cannot output a token that violates the schema. This eliminates the entire class of 'invalid JSON -> retry' failures. But schema adherence is not semantic correctness. The model can output a perfectly valid JSON object with wrong field values -- bad math, invented IDs, wrong classifications. So I always pair constrained decoding with app-level semantic validation: Pydantic validators on values, range checks, enum enforcement, and retry-with-correction for semantic errors. The refusal channel is a first-class audit outcome -- distinguish between 'model refused to answer' and 'model produced invalid output.'"

**Q7: How do you design a prompt for cache efficiency?**

A7: "The layout rule is: stable prefix first, volatile suffix last. System instructions, few-shot examples, and tool definitions go in the prefix because they are identical across requests. User messages, query-specific data, and conversation history go at the end. The minimum cacheable prefix is 1,024 tokens for Sonnet 5 and GPT-5.6+, and 4,096 for Opus 4.5 and Haiku 4.5 -- below minimum means always uncached with no error. I also scope cache keys per tenant to prevent cross-user cache probing, and I monitor cache_creation_input_tokens and cache_read_input_tokens to track hit rates. The operating rule: grow the stable prefix only while eval lift exceeds marginal miss-cost."

**Q8: What is the accuracy compounding problem in prompt chains?**

A8: "When you chain N prompts together, end-to-end accuracy is p^n where p is per-step accuracy. At 95% per step across 10 steps, you get only 60% E2E accuracy. The optimal decomposition depth from research is 2.8-3.2 steps. Beyond that, accuracy erosion from compounding outweighs gains from decomposition. The fix is mandatory programmatic gates between steps: schema validation, required field checks, policy thresholds, and human approval where needed. With gates that catch and retry failures, effective per-step accuracy rises to 99%+, keeping the chain reliable. Without gates, a prompt chain is just a more expensive way to get wrong answers."

**Q9: How do you version and deploy prompts in a regulated environment?**

A9: "Schema-first development: define the Pydantic schema, build the prompt around it, version as an immutable artifact. Every commit triggers the evaluation suite against a fixed set of real production inputs. The pipeline gates deployment: scores must meet or exceed baseline across accuracy, format compliance, latency, cost, and safety. I deploy via environment labels -- dev, staging, prod -- with rollback being just re-pointing the label to the previous version, which is instant. Every publish is recorded in an immutable audit log: prompt_id, semver, content_sha256, model_pin, publisher, approved_by, eval_pass_rate. For EU AI Act compliance, this gives full chain-of-custody on every decision."

**Q10: What is the trade-off triangle in prompt engineering?**

A10: "The three competing forces are prompt length, cache hit rate, and quality. A longer stable prefix with more few-shots and richer tool definitions improves quality -- but raises write cost on cache misses and may push past the minimum cache threshold differently. A shorter prefix is cheaper on misses but may underspecify the task. A high cache hit rate f dominates the cost formula toward the warm price. The operating rule: grow the stable prefix only while the eval quality lift exceeds the marginal miss-cost increase. Never buy quality with tokens that break prefix stability every request -- if user data leaks into the stable prefix, you kill the cache and pay full prefill on every call."

**Q11: How do you handle the lost-in-the-middle effect?**

A11: "The lost-in-the-middle research shows that instructions in the middle of long contexts receive less attention than those at the beginning or end -- it is a U-shaped curve. My production mitigation: place critical instructions at the start in the system prompt and at the end in the final user message. Bury reference material and retrieved documents in the middle where lower attention is acceptable. For Claude specifically, Anthropic recommends putting long documents near the top with the query below, and asking the model to quote relevant passages before answering. This conflicts with the cache-layout rule of stable-prefix-first, so I choose based on the use case: high-RPM classifiers get cache-optimized layout, one-shot long-doc Q&A gets the Anthropic placement."

**Q12: What prompting technique delivers the highest value per token?**

A12: "Zero-shot on modern models. It delivers 93.1% accuracy at 1x tokens, making it the highest value-per-token technique available. Few-shot at 1.3x tokens adds maybe 1% accuracy -- worth it for format/style control but not for accuracy alone. CoT at 1.5x adds another marginal percent. Self-Consistency at 10x adds 12-18% but only on high-stakes decisions with clear rubrics. Tree-of-Thought at 47x is a domain-specific weapon -- 74% vs 4% on Game of 24, but actually scores worse than zero-shot on general QA. The decision tree I use: start with zero-shot, add few-shot only for format control, add CoT only for multi-step reasoning, add Self-Consistency only for high-stakes decisions, and use ToT only when all else fails on combinatorial problems."

## Key Numbers to Memorize

| Metric | Value |
|---|---|
| Zero-shot accuracy (modern models) | 93.1% |
| Exemplar-order sensitivity (SST-2) | 54.3% -> 93.4% |
| Structured Outputs schema adherence | 100% (vs 93% unconstrained, <40% GPT-4-0613) |
| Anthropic cache hit discount | 0.1x (90% off) |
| Anthropic cache write surcharge | 1.25x (5m TTL) |
| Min cacheable prefix (Sonnet 5, GPT-5.6+) | 1,024 tokens |
| Warm vs cold cost reduction | ~67% |
| Prompt caching: Sonnet+cache vs Haiku | $240/mo vs $1,600/mo (6.7x) |
| ProjectDiscovery cache hit improvement | 7% -> 84%, 59-70% spend cut |
| Injection attack success rates | 50-84% |
| Layered defense reduction | 73.2% -> 8.7% |
| Prompt drift error jump (6 months) | 35% |
| Prompt chaining accuracy (95%^10) | 59.9% |
| Optimal chain depth | 2.8-3.2 steps |
| Zero-shot CoT lift (MultiArith) | 17.7% -> 78.7% |
| ReAct vs Act-only (ALFWorld) | 71% vs 45% |
| 3,000-token degradation threshold | Reasoning degrades around 3K instruction tokens |
| Prompt engineering time share | 30-40% of AI development time |

## Quick Reference

- **Role hierarchy:** developer/instructions outrank user content. Never interpolate untrusted text into privileged message.
- **Cache layout:** Static prefix first (system, few-shot, tools), volatile suffix last (user, query). Single char change = complete miss.
- **Budget check:** sys + inst + examples + docs + history + user + expected_out < context window. Overflow = trim/compress/reject, never truncate silently.
- **Structured output:** Constrained decoding = ~0% syntactic failure. But schema compliance does NOT equal semantic correctness -- validate values separately.
- **Drift monitoring:** Three methods -- embedding distance, PSI/KL, CI/CD eval. Run nightly minimum.
- **Injection defense:** Three layers -- architectural prevention, runtime detection, governance. No single layer suffices.
- **Technique selection:** Zero-shot default. Add complexity only when eval proves lift worth the token cost.
- **Caching first:** Evaluate caching on larger model before downgrading. Break-even = 2 requests.
- **Chain length:** 2.8-3.2 steps optimal. Inter-step gates mandatory.
- **Model pinning:** Always pin. Unpinned versions cause silent drift.
