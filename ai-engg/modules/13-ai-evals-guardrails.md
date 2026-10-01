# 13. AI Evals, Guardrails & Security

**Sub-areas covered**: The four-component trust layer (evaluation, guardrails, observability, security), five system properties making trust layers non-optional, evaluation pipeline design with cheapest-first grader hierarchy (deterministic / LLM-judge / human), golden test set construction from production failures (20-50 cases minimum, three required groups), LLM-as-judge configuration (pass/fail over numeric scales, critique-first ordering, three biases with mitigations), RAG-specific metrics (hit rate, MRR, context precision/recall, faithfulness, response relevance), agent evaluation (outcome-first grading, pass@k vs pass^k reliability), eval-as-CI with regression and capability suites, online evaluation (A/B testing with user-level randomization, shadow mode, SPRT), four-layer guardrail reference architecture (input gate, semantic guard, output filter, execution gate), NeMo Guardrails (Colang 2.0 dialog rails), Guardrails AI (structured output validation with re-ask), gateway-level enforcement as 2026 default, guardrail classifier latency benchmarks (10ms to 2940ms per tool), risk-proportional guardrail tiers, async batch optimization for classifier inference, prompt injection as OWASP LLM #1 for three consecutive years, eight attack vectors (direct/indirect/encoding/typoglycemia/RAG poisoning/multimodal/agent-specific/cross-modal), 7-layer defense-in-depth model with latency budgets, Anthropic Constitutional Classifiers (86% to 4.4% jailbreak rate, +0.38% overrefusal, +23.7% compute), PINT benchmark scores across five providers, Goodhart's Law in eval suites (benchmaxxing, verification horizon problem, EvalSafetyGap), guardrail failover strategies (cascade, fail-closed vs fail-open, circuit breakers, multi-provider redundancy), eval data pipeline reliability (OTEL-based collection, dataset versioning, production feedback loops), compliance frameworks (EU AI Act staged enforcement through Dec 2027, SOC 2, ISO 42001, HIPAA BAA requirements, NIST AI RMF), OWASP 2026 Top 10 and separate Agentic Framework, MITRE ATLAS v5.4.0 (84 techniques, MCP compromise case studies), red teaming with Promptfoo (OpenAI-acquired, 50+ vulnerability types), observability platform comparison (Arize Phoenix, Langfuse, LangSmith, Braintrust), production Python code with async guardrail pipeline using retry/backoff/circuit-breaker/structured-logging/batch-accumulation, and two enterprise system-design scenarios (regulated-industry guardrail gateway, continuous evaluation platform) with architecture diagrams and trade-off matrices

---

## 1. System Topology & Data Flow

A production LLM trust layer spans four cooperating subsystems operating at different lifecycle stages: an **evaluation plane** grading system quality pre-deployment through CI-integrated test suites with deterministic, LLM-judge, and human graders; a **guardrail plane** filtering every request and response at runtime through a four-layer cascade (input gate, semantic guard, output filter, execution gate); an **observability plane** capturing OpenTelemetry-based traces across agents, retrievers, and tools, feeding production failures back into golden test sets; and a **security plane** cutting across all three, defending against prompt injection, data poisoning, and supply-chain attacks through defense-in-depth.

Five system properties make this trust layer non-optional: **non-determinism** (same input yields different outputs), **plausible mistakes** (wrong answers arrive as fluently as correct ones), **silent regressions** (a prompt fix for one case can break five others), **untrustworthy inputs** (instructions can arrive inside retrieved documents, tool results, or MCP server responses), and **deployment-dependent legal exposure** (obligations depend on system usage, not the model).

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              SECURITY PLANE                                      │
│                       (cuts across all three planes)                              │
│                                                                                  │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │ Prompt Injection    │  │ Content Safety       │  │ Compliance Engine        │  │
│  │ Defense             │  │ Classification       │  │                          │  │
│  │                     │  │                      │  │ EU AI Act risk tier      │  │
│  │ 7-layer model:      │  │ OWASP LLM Top 10    │  │ SOC 2 audit trails       │  │
│  │  input gate (20ms)  │  │ MITRE ATLAS v5.4.0   │  │ HIPAA BAA + PII redact  │  │
│  │  prompt hardening   │  │ Constitutional       │  │ ISO 42001 ISMS           │  │
│  │  model controls     │  │  Classifiers         │  │ NIST AI RMF lifecycle    │  │
│  │  output constraints │  │ Red teaming:         │  │                          │  │
│  │  privilege sep      │  │  Promptfoo (50+      │  │ "Show me why the AI     │  │
│  │  runtime monitoring │  │  vuln types)         │  │  made this decision      │  │
│  │  human verification │  │  DeepTeam (ATLAS)    │  │  six months ago"         │  │
│  └────────────────────┘  └─────────────────────┘  └──────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────┘
                    │                       │                        │
  ┌─────────────────▼───────────────────────▼────────────────────────▼────────────┐
  │                          API GATEWAY / AI FIREWALL                             │
  │         Gateway-level enforcement (2026 default deployment pattern)            │
  │   Solves: inconsistent policy, provider lock-in, scattered audit logs          │
  └────────────────────────────────────────┬──────────────────────────────────────┘
                                           │ every request
┌──────────────────────────────────────────▼──────────────────────────────────────┐
│                          GUARDRAIL PLANE (runtime)                               │
│                                                                                  │
│  ┌──────────────────┐  ┌───────────────────┐  ┌──────────────────────────────┐  │
│  │ Layer 1: Input    │  │ Layer 2: Semantic  │  │ Layer 3: Output Filter       │  │
│  │ Gate              │  │ Guard              │  │                              │  │
│  │                   │  │                    │  │ Llama Guard 3/4 (8B):        │  │
│  │ Prompt Guard 2    │  │ Topic control      │  │  content safety (~460ms)     │  │
│  │  (86M, 20-50ms)  │  │ System prompt      │  │ Structured output            │  │
│  │ PII regex + NER   │  │  hardening         │  │  validation (JSON schema)    │  │
│  │  (10-30ms)       │  │ Retrieval-rail     │  │ Hallucination check          │  │
│  │ Input normalize   │  │  RAG chunk filter  │  │  (high-risk only)            │  │
│  │ Length constraint  │  │                    │  │                              │  │
│  │ Encoding decode   │  │                    │  │                              │  │
│  └────────┬─────────┘  └────────┬───────────┘  └──────────────┬───────────────┘  │
│           │ clean input         │ scoped context               │ safe output      │
│           ▼                     ▼                              │                  │
│  ┌──────────────────────────────────────────┐                  │                  │
│  │         LLM INFERENCE ENGINE              │                  │                  │
│  │  Constrained params: temp, token limits,  │──────────────────┘                  │
│  │  stop sequences, structured output mode   │                                    │
│  └──────────────────────────────────────────┘                                    │
│                         │ raw output                                              │
│  ┌──────────────────────▼───────────────────────────────────────────────────────┐ │
│  │ Layer 4: Execution Gate                                                       │ │
│  │  Tool-call permission checks (allowlisted tools, scoped credentials)          │ │
│  │  Human approval for high-risk operations (async, seconds-minutes)             │ │
│  │  Audit logging (every decision, full trace)                                   │ │
│  └──────────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────┬─────────────────────────────────────────┘
                                        │ guarded response
┌───────────────────────────────────────▼─────────────────────────────────────────┐
│                         OBSERVABILITY PLANE                                      │
│                                                                                  │
│  ┌──────────────────────┐  ┌───────────────────┐  ┌──────────────────────────┐  │
│  │ Trace Collection      │  │ Eval Scoring       │  │ Drift Detection &        │  │
│  │                       │  │                    │  │ Feedback Loop             │  │
│  │ OTEL nested spans:    │  │ 1-5% sample rate   │  │                          │  │
│  │  agent, retriever,    │  │ Same eval suite     │  │ Metric drift alerting    │  │
│  │  tool, guardrail      │  │  as CI pipeline     │  │ Production failures -->  │  │
│  │ Arize Phoenix /       │  │ LLM-judge on        │  │  golden test set         │  │
│  │  Langfuse / LangSmith │  │  sampled traces     │  │ Quarterly rater audit    │  │
│  │                       │  │ Scores attached     │  │  (150 examples, 2 raters │  │
│  │ 1T spans/yr at scale  │  │  to production      │  │   >= 0.7 Cohen's Kappa)  │  │
│  │                       │  │  traffic            │  │                          │  │
│  └──────────────────────┘  └───────────────────┘  └──────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────┘
                                        │ failures + drift signals
┌───────────────────────────────────────▼─────────────────────────────────────────┐
│                          EVALUATION PLANE (pre-deployment)                        │
│                                                                                  │
│  ┌──────────────────────┐  ┌───────────────────┐  ┌──────────────────────────┐  │
│  │ Grader Hierarchy      │  │ Test Suites        │  │ Online Experiments       │  │
│  │ (cheapest-first)      │  │                    │  │                          │  │
│  │                       │  │ Regression suite:  │  │ Shadow mode (week 1):    │  │
│  │ Tier 1: Deterministic │  │  known-good cases, │  │  both variants run,      │  │
│  │  JSON schema, regex,  │  │  pass rate ~100%   │  │  users see only control  │  │
│  │  code execution       │  │                    │  │                          │  │
│  │  Cost: negligible     │  │ Capability suite:  │  │ A/B test (user-level     │  │
│  │                       │  │  hard edge cases,  │  │  randomization, SPRT):   │  │
│  │ Tier 2: LLM-judge     │  │  pass rate < 100%  │  │  5000+ samples/arm for   │  │
│  │  Rubric, pairwise     │  │  (discriminative)  │  │  quality metrics         │  │
│  │  $0.01-0.10/eval     │  │                    │  │                          │  │
│  │                       │  │ Golden test set:   │  │ Metrics: latency, token  │  │
│  │ Tier 3: Human review  │  │  20-50 from prod   │  │  cost, LLM-judge score,  │  │
│  │  Domain expert        │  │  failures + holdout│  │  explicit feedback       │  │
│  │  $1-10+/eval         │  │                    │  │                          │  │
│  └──────────────────────┘  └───────────────────┘  └──────────────────────────┘  │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────────┐ │
│  │                          CI/CD Gate                                           │ │
│  │  1. Change prompt/model  2. Run suite 3-5x  3. Generate scorecard            │ │
│  │  4. Block merge if critical scores fall below release thresholds             │ │
│  └──────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A user request enters the **API gateway / AI firewall**, which enforces organization-wide policy before any model call -- solving the three problems that per-team guardrail enforcement created: inconsistent interpretation, provider lock-in, and scattered audit trails. (2) **Layer 1 (Input Gate)** runs the fastest classifiers: Llama Prompt Guard 2 (86M params, 20-50ms) screens for prompt injection, regex + NER hybrid detects and redacts PII before any cloud transmission, input normalization strips hidden characters and decodes Base64/Hex encoding attacks, and length constraints prevent resource exhaustion. (3) **Layer 2 (Semantic Guard)** applies topic control classification and system prompt hardening; for RAG workloads, retrieval-rails filter retrieved chunks before they enter the context window -- critical because 5 carefully crafted documents can manipulate responses 90% of the time. (4) The **LLM inference engine** runs with constrained parameters (temperature, token limits, stop sequences). (5) **Layer 3 (Output Filter)** applies content safety classification via Llama Guard 3/4 (8B, ~460ms), validates structured output against JSON schema, and runs hallucination checks against knowledge bases for high-risk outputs only. (6) **Layer 4 (Execution Gate)** checks tool-call permissions against an allowlist, routes high-risk operations to human approval, and writes an immutable audit log with the full trace. (7) The **observability plane** attaches OTEL spans to the complete request lifecycle, scores 1-5% of production traces with the same eval suite used in CI, and runs drift detection with automatic alerting. Production failures flow back into the golden test set, closing the feedback loop. (8) Pre-deployment, the **evaluation plane** runs regression and capability suites 3-5x per change for statistical confidence, enforces CI/CD merge gates, and manages online experiments through shadow mode (week 1 of model swaps) followed by A/B testing with user-level randomization and Sequential Probability Ratio Test (SPRT) for continuous monitoring.

---

## 2. Core Mechanics & Algorithms

### 2.1 Evaluation Methods: The Cheapest-First Grader Hierarchy

The foundational principle is cost-proportional grading: use the cheapest grader that catches the failure you care about. Three tiers apply in sequence.

**Tier 1 -- Deterministic graders (negligible cost).** Applicable when a correct answer exists or output structure is constrained. JSON schema validation catches format violations. Code execution verifies functional correctness. Regex patterns check for required/forbidden content. Latency caps enforce performance SLAs. Tool-call argument validation catches parameter errors. These run on every evaluation and never require LLM inference.

**Tier 2 -- LLM-as-judge graders ($0.01-0.10/eval).** Required when quality is subjective and no deterministic check captures the failure mode. Three formats, each optimized for a different evaluation shape:

```
┌────────────────────┬──────────────────────────────┬────────────────────────────────┐
│ Format             │ When to Use                  │ Configuration                  │
├────────────────────┼──────────────────────────────┼────────────────────────────────┤
│ Pass/fail          │ Default for most evals       │ Binary verdict, no numeric     │
│                    │ (1-5 boundaries are           │ scale. Critique-first: judge   │
│                    │  ambiguous across raters)     │ explains BEFORE returning      │
│                    │                               │ verdict to prevent post-hoc    │
│                    │                               │ justification                  │
├────────────────────┼──────────────────────────────┼────────────────────────────────┤
│ Pairwise           │ Subjective comparisons        │ Show judge two outputs, ask    │
│                    │ where pass/fail is too         │ which is better. Swap order    │
│                    │ restrictive (A/B prompt tests) │ across runs to mitigate        │
│                    │                               │ position bias, average results │
├────────────────────┼──────────────────────────────┼────────────────────────────────┤
│ Outcome grading    │ Agent evaluation              │ Check each subgoal separately  │
│ (agents)           │                               │ (refund issued? file written?  │
│                    │                               │ test passed?). Inspect          │
│                    │                               │ trajectory ONLY on failure      │
└────────────────────┴──────────────────────────────┴────────────────────────────────┘
```

**Three biases requiring active mitigation in LLM judges:**

- **Position bias**: Judge favors the first-read answer. Mitigation: swap answer order across runs, average results.
- **Self-preference**: Judge favors outputs matching its own style. Mitigation: use a different model family for the judge than for the system under test.
- **Verbosity bias**: Longer answers score higher regardless of quality. Mitigation: include length-matched pairs in the calibration set.

**Calibration protocol**: Run the judge on 30-50 human-labeled examples before trusting it. Compare automated verdicts with human labels, compute agreement. Pick the cheapest model that passes calibration -- consistency matters more than raw capability.

**Tier 3 -- Human review ($1-10+/eval).** Reserved for high-stakes decisions where automated graders lack domain expertise. Quarterly rater agreement audits (150 examples, two independent engineers, Cohen's Kappa >= 0.7) validate that human reviewers and automated scorers remain aligned. Below 0.6 correlation signals evaluator divergence from human judgment.

### 2.2 Golden Test Set Construction

Golden test sets are the backbone of offline evaluation. Construction follows five invariants:

1. **Start from production failures, not hypotheticals.** 20 cases from real production traces represent actual risks better than 100 hypothetical questions.
2. **Three required groups**: in-scope (system should answer correctly), out-of-scope (system should refuse), adversarial (designed to break the system).
3. **Starting size**: 20-50 cases. Target: 50-100 cases. Each case requires: input, expected behavior/key points, supporting evidence, prompt and data versions.
4. **Holdout set**: Locked down, run only for consequential decisions (release gates, model swaps). The working eval set can be Goodharted; the holdout tells you whether gaming translated to real improvement.
5. **Refresh cycle**: Production failures feed back into the working eval set as the product evolves.

### 2.3 RAG-Specific Metrics

```
┌─────────────────────┬──────────────────────────────────────────────────────────┐
│ Metric              │ What It Measures                                         │
├─────────────────────┼──────────────────────────────────────────────────────────┤
│ Hit rate            │ >= 1 relevant chunk in top-K results                     │
│ MRR                 │ Position of first relevant result (1/rank)               │
│ Context precision   │ Proportion of retrieved context that was relevant        │
│ Context recall      │ Proportion of relevant information retrieved             │
│ Faithfulness        │ Answer supported by retrieved context (not hallucinated) │
│ Response relevance  │ Answer actually addresses the user's question            │
└─────────────────────┴──────────────────────────────────────────────────────────┘
```

Hit rate and MRR evaluate retrieval quality independently of generation. Context precision and recall evaluate whether the retriever sends the right material. Faithfulness and response relevance evaluate whether the generator uses the retrieved material correctly and stays on-topic. A system can have perfect faithfulness (every claim grounded in context) yet poor response relevance (it answers a different question than asked).

### 2.4 Agent Reliability: pass@k vs pass^k

Two metrics capture fundamentally different operational requirements:

- **pass@k**: At least 1 of k attempts succeeds. Appropriate when a human reviews results and retries are acceptable.
- **pass^k**: All k attempts must succeed. Introduced by Sierra's tau-bench for unsupervised agents where every execution must complete correctly.

**Critical statistic**: An agent failing 10% of the time has a ~57% probability of at least one failure across 8 tasks. For unsupervised multi-step workflows, pass^k is the only honest metric.

Path-based grading (scoring the exact sequence of steps) penalizes valid alternative solutions. Grade the state the agent left behind (refund issued, file written, test passed), not the path taken. Trajectory metrics (step efficiency, loop detection, tokens per task) are diagnostic tools, not release gates.

### 2.5 Eval Saturation and Capability Suite Management

Two suites serve complementary purposes:

- **Regression suite**: Cases the system handles correctly. Pass rate should stay ~100%. Detects breakage from changes.
- **Capability suite**: Hard cases near system limits. Pass rate should stay below 100% to maintain discriminative power.

**Eval saturation** (Anthropic's term): When nearly all tests pass, the suite loses its ability to distinguish meaningful improvement from noise. The fix is adding harder tasks, not celebrating the high score.

### 2.6 Goodhart's Law in Eval Suites

When a measure becomes a target, it ceases to be a good measure. Most teams gaming evals do so through ordinary engineering instincts -- fix what the metric says to fix -- slowly eroding signal until the suite measures optimization-for-the-suite rather than production performance.

**Benchmaxxing patterns (2026):**
- **Contamination**: Benchmark questions leak into training data via web scraping or synthetic pipelines.
- **Cherry-picking**: Privately test many model versions, publish only the best.
- **Ceiling effects**: MMLU, HumanEval hit ceiling -- dozen models within 2 percentage points, rankings reflect noise.
- **Agent exploitation**: SWE-bench agents learned to inspect `.git` history to find human-written patches instead of solving problems.

**The Verification Horizon Problem** (June 2026): Rice's theorem and Goodhart's law jointly guarantee proxy-based verification is subject to inevitable failure. Verifier failure rate *increases* as agents become more capable. Reward hacking is emergent, not a bug -- cannot be eliminated by static hardening, only suppressed by dynamic audit.

**Practical mitigations**: (1) Holdout eval sets run only for consequential decisions. (2) Pair north-star metric with anti-gaming guardrail (reopen rate, human audit sample, held-out eval). (3) Rater agreement audits: 150 examples, two independent engineers, below 0.7 Kappa means ambiguous eval. (4) Dynamic verification: reward signals, evaluators, and monitoring must evolve in lockstep with model capability.

### 2.7 Guardrail Classification Algorithms

**Llama Prompt Guard 2 (86M params)**: BERT-class binary classifier for prompt injection detection. Runs in 20-50ms on H100 with FP8 quantization on short inputs. Designed as a fast first-pass gate -- high precision, acceptable recall. PINT benchmark score: 78.76%.

**Llama Guard 3/4 (8B params)**: Multi-label hazard classifier covering OWASP content categories. F1 of 0.961 on clean data, drops to 0.796 under adversarial inputs (17% degradation). ~459ms p95 on typical GPU. Used as a detailed second-pass classifier on flagged or sampled traffic.

**Anthropic Constitutional Classifiers**: Input and output classifiers trained on synthetic data generated from a "constitution" of allowed/disallowed content rules. Training pipeline: (1) Author constitution of rules. (2) Claude generates synthetic prompts/completions matching each rule. (3) Augment via translation + jailbreak-style transforms. (4) Train classifiers on augmented dataset. Results: jailbreak success rate dropped from 86% to 4.4% with +0.38% overrefusal (not statistically significant) and +23.7% compute overhead. The constitution can be rapidly adapted to cover novel attacks.

**Risk-proportional guardrail tiers** (the operational pattern that keeps guardrails from being disabled):
- **Lightweight (<50ms)**: All outputs -- regex, format checks, known-pattern matching.
- **Medium-weight (50-200ms)**: Most outputs -- toxicity classification, structured output enforcement.
- **Heavy (200-2000ms)**: Low-confidence or high-risk outputs only -- factuality checks, LLM-judge evaluation.

A 99% recall guardrail at 400ms is worse in practice than a 95% one at 10ms because the slow one gets turned off during incidents.

---

## 3. Token Economics & NFR Analysis

### 3.1 Guardrail Inference Cost (Classifier Overhead per Request)

```
┌──────────────────────────────┬────────────┬───────────────────┬──────────────────┐
│ Component                    │ Model Size │ Latency (p95)     │ Cost/Request     │
├──────────────────────────────┼────────────┼───────────────────┼──────────────────┤
│ Llama Prompt Guard 2         │ 86M params │ 20-50ms (H100 FP8)│ ~$0.0001 (GPU   │
│  (input injection gate)      │            │                   │  amortized)      │
├──────────────────────────────┼────────────┼───────────────────┼──────────────────┤
│ PII regex + NER hybrid       │ N/A + ~20M │ 10-30ms           │ ~$0.0000         │
│  (redaction before cloud)    │            │                   │  (CPU-bound)     │
├──────────────────────────────┼────────────┼───────────────────┼──────────────────┤
│ NeMo Guardrails orchestrator │ N/A        │ ~20ms overhead    │ LLM-provider     │
│  (routing, dialog state)     │            │                   │  dependent       │
├──────────────────────────────┼────────────┼───────────────────┼──────────────────┤
│ Llama Guard 3/4              │ 8B params  │ ~459ms            │ ~$0.001          │
│  (content safety class.)     │            │                   │  (GPU amortized) │
├──────────────────────────────┼────────────┼───────────────────┼──────────────────┤
│ LLM-judge factuality check   │ Full model │ 1-5s              │ $0.01-0.10       │
│  (high-risk outputs only)    │            │                   │                  │
├──────────────────────────────┼────────────┼───────────────────┼──────────────────┤
│ Total synchronous chain      │ --         │ 90-300ms          │ $0.001-0.005     │
│  (typical request)           │            │                   │                  │
├──────────────────────────────┼────────────┼───────────────────┼──────────────────┤
│ Total with heavy checks      │ --         │ 500ms-5s+         │ $0.05-0.15       │
│  (flagged/high-risk request) │            │                   │                  │
└──────────────────────────────┴────────────┴───────────────────┴──────────────────┘
```

**Anthropic Constitutional Classifiers overhead**: +23.7% compute (measured on Claude 3.5 Sonnet). This is the cost of reducing jailbreak success from 86% to 4.4%.

### 3.2 Cost Formula: Guardrail Pipeline per 1K Requests

```
Cost_per_1K = (1000 * C_fast_gate)                           # always-on input gate
            + (1000 * C_pii_redact)                          # always-on PII
            + (sample_rate * 1000 * C_content_safety)        # sampled heavy classifier
            + (flag_rate * 1000 * C_llm_judge)               # on-demand judge

Where (2026 self-hosted, amortized GPU):
  C_fast_gate    = $0.0001/req
  C_pii_redact   = $0.0000/req  (CPU)
  C_content_safety = $0.001/req  (Llama Guard 8B)
  C_llm_judge    = $0.05/req    (frontier model call)

Example at 10% sampling, 2% flagged:
  Cost_per_1K = (1000 * 0.0001) + 0 + (100 * 0.001) + (20 * 0.05)
             = $0.10 + $0.10 + $1.00
             = $1.20 per 1K requests
```

### 3.3 Evaluation Suite Costs

At 10,000 evaluations/day, monthly LLM-judge cost: **$150-$1,200** depending on metric depth (number of rubric dimensions, model tier used for judging). Standard cost control: sample 1-5% of live production traces rather than evaluating every request.

**Cost optimization**: Pick the cheapest judge model that passes calibration on 30-50 human-labeled examples. A $0.01/eval model matching human judgment at 0.85 agreement beats a $0.10/eval model at 0.87 agreement.

### 3.4 Latency SLA Targets

```
┌─────────────────────────────────┬─────────┬─────────┬─────────┬───────────────┐
│ Component                       │ p50     │ p95     │ p99     │ Action if     │
│                                 │         │         │         │ exceeded      │
├─────────────────────────────────┼─────────┼─────────┼─────────┼───────────────┤
│ Input gate (Prompt Guard 2)     │ 15ms    │ 35ms    │ 50ms    │ Fail-open     │
│                                 │         │         │         │ with logging  │
├─────────────────────────────────┼─────────┼─────────┼─────────┼───────────────┤
│ PII redaction (regex + NER)     │ 8ms     │ 20ms    │ 30ms    │ Regex-only    │
│                                 │         │         │         │ fallback      │
├─────────────────────────────────┼─────────┼─────────┼─────────┼───────────────┤
│ Content safety (Llama Guard 8B) │ 250ms   │ 459ms   │ 700ms   │ Circuit break │
│                                 │         │         │         │ to lighter    │
│                                 │         │         │         │ classifier    │
├─────────────────────────────────┼─────────┼─────────┼─────────┼───────────────┤
│ Full guardrail pipeline         │ 60ms    │ 200ms   │ 500ms   │ Skip heavy    │
│  (synchronous path)             │         │         │         │ classifiers   │
├─────────────────────────────────┼─────────┼─────────┼─────────┼───────────────┤
│ User-perceived total            │ 800ms   │ 2.5s    │ 5s      │ Degrade to    │
│  (guardrails + LLM inference)   │         │         │         │ cached resp   │
└─────────────────────────────────┴─────────┴─────────┴─────────┴───────────────┘
```

**Critical threshold**: >50ms inline and users feel it. >200ms and someone disables it during an incident. Guardrail classifier cold starts are as damaging as LLM cold starts -- pre-warm instances and maintain minimum replica counts.

### 3.5 Throughput Capacity Planning

**Async batch optimization** converts N sequential classifier calls into a single batched call:

- Collect requests for 5ms accumulation window
- Batch into single /v1/chat/completions call to classifier GPU (max batch size 16)
- Fan out responses to waiting coroutines
- Effect: N sequential 30ms calls become a single 35ms batched call

**Guardrail tool benchmarks (per-prompt latency vs. detection quality):**

```
┌─────────────────────────┬────────────┬───────────┬────────┬──────────────────┐
│ Tool                    │ Latency    │ Precision │ Recall │ Deployment Mode  │
├─────────────────────────┼────────────┼───────────┼────────┼──────────────────┤
│ Future AGI fi.evals     │ <10ms      │ --        │ --     │ Sync (ultra-fast)│
│ Lakera Guard            │ 66ms       │ 0.964     │ 0.501  │ Sync OK          │
│ Azure Prompt Shield     │ 349ms      │ --        │ --     │ Borderline sync  │
│ Protect AI LLM Guard   │ 1,590ms    │ --        │ 0.604  │ Async only       │
│ Vigil scanner           │ 2,940ms    │ --        │ --     │ Async only       │
└─────────────────────────┴────────────┴───────────┴────────┴──────────────────┘
```

### 3.6 Non-Functional Requirements

```
┌──────────────┬──────────────────────────────────────────────────────────────────┐
│ NFR          │ Requirement                                                      │
├──────────────┼──────────────────────────────────────────────────────────────────┤
│ Availability │ Guardrail pipeline: 99.9% (matches LLM serving SLA)              │
│              │ Eval CI pipeline: 99.5% (best-effort, retry on failure)          │
├──────────────┼──────────────────────────────────────────────────────────────────┤
│ Scalability  │ Guardrails: auto-scale with LLM inference fleet                  │
│              │ Eval: burst capacity for 3-5x parallel suite runs               │
├──────────────┼──────────────────────────────────────────────────────────────────┤
│ Durability   │ Eval datasets: versioned, immutable, SHA-256 hashed             │
│              │ Audit logs: append-only, 7-year retention (HIPAA/SOC2)          │
├──────────────┼──────────────────────────────────────────────────────────────────┤
│ Latency      │ Sync guardrails: p99 < 500ms total pipeline                     │
│              │ Fail-safe: degrade to lighter classifier, never block silently   │
├──────────────┼──────────────────────────────────────────────────────────────────┤
│ Consistency  │ Eval scores reproducible within 3-5x run variance               │
│              │ Guardrail decisions deterministic for same input (classifiers)   │
├──────────────┼──────────────────────────────────────────────────────────────────┤
│ Observability│ Every guardrail decision traced with OTEL span                   │
│              │ Every eval score attached to prompt version + model version      │
└──────────────┴──────────────────────────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Guardrail Failover: Cascade Architecture

Defense-in-depth is the only viable strategy -- no single guardrail catches every attack. The cascade operates in three tiers:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        GUARDRAIL CASCADE                                    │
│                                                                             │
│  ┌─────────────────────┐                                                   │
│  │ Tier 1: Fast Screen  │  86M params, <50ms                                │
│  │ (ALL traffic)        │  Prompt Guard 2 + regex PII + input normalize     │
│  └──────────┬──────────┘                                                   │
│             │ pass (clean) or flag (suspicious)                             │
│             ▼                                                               │
│  ┌─────────────────────┐                                                   │
│  │ Tier 2: Detailed     │  8B params, ~460ms                                │
│  │ (flagged + sampled)  │  Llama Guard 3/4 content safety classification    │
│  └──────────┬──────────┘                                                   │
│             │ pass or flag                                                  │
│             ▼                                                               │
│  ┌─────────────────────┐                                                   │
│  │ Tier 3: LLM Judge    │  Full model, 1-5s                                 │
│  │ (high-risk/ambiguous)│  Factuality check, nuanced policy evaluation      │
│  └─────────────────────┘                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Fail-closed vs. fail-open**: For regulated workloads (HIPAA, financial), guardrails must fail-closed -- if the classifier is unavailable, block the request. For low-risk consumer features, fail-open with logging and async review may be acceptable. This is a business-level decision, not a technical default.

**Circuit breaker behavior**: When Llama Guard p99 latency exceeds 500ms, the circuit breaker routes to the lighter Prompt Guard 2 fallback or cached policy decisions. The system never silently drops guardrail enforcement.

**Multi-provider redundancy**: The gateway pattern enables routing guardrail checks across multiple providers (Azure Prompt Shield + self-hosted Llama Guard) to avoid single-provider outages. Provider failover adds ~10ms for routing logic.

### 4.2 Eval Pipeline Reliability

**Three-stage data pipeline:**

1. **Collection**: OpenTelemetry-based tracing captures nested spans across agents, retrievers, and tools. OTEL is the standard instrumentation layer across all major platforms (Arize Phoenix, Langfuse, LangSmith, Braintrust). Arize Phoenix processes 1 trillion spans/year.
2. **Storage & versioning**: Eval datasets versioned alongside prompts and model configs. Immutable snapshots with SHA-256 hashes. Braintrust and LangSmith provide experiment-level versioning. DeepEval integrates with Confident AI for test management.
3. **Feedback loop**: Production failures feed back into golden test sets. Continuous sampling (1-5%) of production traffic against the same evaluator suite used in CI. Alert when a metric drifts beyond threshold.

**Statistical rigor for A/B tests**: Pre-commit to fixed sample size and check once, or use SPRT for continuous monitoring without inflating false positive rate. LLM quality tests need 5,000+ samples per arm (vs. 500 for a button-color test) because output variance is higher. User-level randomization (not per-request) for conversational features -- session contamination invalidates per-request randomization.

### 4.3 OWASP Threats and Attack Surface

**OWASP LLM Top 10 (2026 edition)** -- first data-driven version (7,714 real incidents):

```
┌──────┬──────────────────────────────────┬──────────────────────────────────────┐
│ Rank │ Risk                             │ Defense Layer                        │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 1    │ Prompt Injection (3rd yr at #1)  │ Input gate + prompt hardening +      │
│      │                                  │ privilege separation                 │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 2    │ Sensitive Info Disclosure        │ PII redaction at inference + output  │
│      │                                  │ filter                               │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 3    │ Excessive Agency (up from 6th)   │ Execution gate + tool allowlists +   │
│      │                                  │ human approval for high-risk ops     │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 4    │ Data and Model Poisoning         │ Training data validation + retrieval │
│      │                                  │ rail filtering                       │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 5    │ Supply Chain                      │ AIBOM + dependency scanning +        │
│      │                                  │ model provenance                     │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 6    │ Unbounded Consumption (up)       │ Token limits + rate limiting +       │
│      │                                  │ cost circuit breakers                │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 7    │ Vector & Embedding Weaknesses    │ Embedding validation + retrieval     │
│      │                                  │ rail filtering                       │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 8    │ Misinformation                   │ Faithfulness eval + fact-checking    │
│      │                                  │ against knowledge bases              │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 9    │ Hidden Context Exposure          │ System prompt protection + output    │
│      │  (renamed from System Prompt     │ scanning                             │
│      │   Leakage)                       │                                      │
├──────┼──────────────────────────────────┼──────────────────────────────────────┤
│ 10   │ Improper Output Handling (down)  │ Output sanitization + structured     │
│      │                                  │ output validation                    │
└──────┴──────────────────────────────────┴──────────────────────────────────────┘
```

**Separate Agentic Framework**: OWASP published Top 10 for Agentic Applications (December 2025) -- the LLM list is scoped to model-as-component; the agentic list covers autonomous tool-using workflows. Read together.

**MITRE ATLAS v5.4.0** (February 2026): 16 tactics, 84 techniques, 32 mitigations, 42 case studies. Expanded for agentic AI with techniques like "Publish Poisoned AI Agent Tool" and "Escape to Host." January 2026 update added MCP server compromise case studies.

### 4.4 Prompt Injection: The 7-Layer Defense Model

Prompt injection is structurally unsolvable: models cannot differentiate between data and instructions -- every token in the context window is treated the same way. There is no privileged channel. OpenAI publicly acknowledged (February 2026) that prompt injection in AI browsers "may never be fully patched."

**Eight attack vectors (2025-2026):** direct injection, indirect injection (payloads in retrieved content/tool results/MCP server descriptions), encoding-based (Base64/Hex), typoglycemia (scrambled words), RAG poisoning (5 crafted documents manipulate responses 90% of the time), multimodal (instructions in images), agent-specific (thought/observation injection), cross-modal (added to OWASP 2026).

**7-layer defense with latency budgets:**

```
┌───────┬────────────────────────────┬──────────────┬─────────────────────────────┐
│ Layer │ Function                   │ Latency      │ Implementation              │
├───────┼────────────────────────────┼──────────────┼─────────────────────────────┤
│ 1     │ Input Gate                 │ 20-50ms      │ PromptGuard 2 + regex       │
│ 2     │ Prompt Design              │ 0ms (build)  │ Delimiters, system prompt   │
│       │                            │              │ hardening, spotlighting     │
│ 3     │ Model Controls             │ 0ms (config) │ Temperature, token limits   │
│ 4     │ Output Constraints         │ 50-200ms     │ Schema validation,          │
│       │                            │              │ output classifier           │
│ 5     │ Privilege Separation       │ 0ms (arch)   │ Scoped creds, allowlisted   │
│       │                            │              │ tools, sandboxing           │
│ 6     │ Runtime Monitoring         │ Async        │ Anomaly detection           │
│ 7     │ Human Verification         │ Async (s-m)  │ Approval for high-risk ops  │
└───────┴────────────────────────────┴──────────────┴─────────────────────────────┘
```

Microsoft Research's spotlighting technique alone reduces injection success from >50% to <2%.

**PINT benchmark scores (prompt injection detection accuracy):**
- Lakera Guard: 95.22%
- AWS Bedrock Guardrails: 89.24%
- Azure Prompt Shield: 89.12%
- Llama Prompt Guard 2: 78.76%
- Google Model Armor: 70.07%

### 4.5 Compliance Frameworks

**EU AI Act staged enforcement:**

```
┌─────────────┬────────────────────────────────────────────────────────────────────┐
│ Date        │ Milestone                                                          │
├─────────────┼────────────────────────────────────────────────────────────────────┤
│ Aug 2024    │ Entered into force                                                 │
│ Feb 2025    │ Prohibited-AI provisions effective                                 │
│ Jul 2025    │ Anthropic signed EU GPAI Code of Practice                          │
│ Aug 2025    │ General-purpose AI model obligations (transparency, copyright)     │
│ Aug 2026    │ High-risk system obligations (risk mgmt, data governance,          │
│             │  documentation, human oversight, conformity assessment)            │
│ Dec 2027    │ Extended high-risk areas (biometrics, critical infra, education)   │
└─────────────┴────────────────────────────────────────────────────────────────────┘
```

**Penalties**: Up to EUR 35M or 7% global revenue for prohibited practices; EUR 15M or 3% for high-risk obligation violations.

**Five compliance frameworks at a glance:**

- **EU AI Act**: Risk classification + conformity assessment for AI in EU market.
- **SOC 2 Type II**: De facto B2B SaaS gate. Trust Service Criteria + AI-specific controls.
- **ISO 42001:2023**: AI-specific ISMS certification (Information Security Management System).
- **HIPAA**: PHI protection. Standard consumer API endpoints (OpenAI, Anthropic, Google) generally do not provide BAAs. Sending PHI without BAA is a violation. Real-time PII redaction at inference is the defensible standard; post-processing cleanup is not sufficient.
- **NIST AI RMF**: AI risk management lifecycle. US federal + voluntary adoption.

**BCG 2026 finding**: 73% of enterprise AI initiatives name compliance posture as top-three vendor selection criterion (up from 41% in 2024).

**Regulatory-driven architecture requirement**: "Show me why the AI made this specific decision six months ago." Retrofitting governance after an audit notice costs 2-3x the original build. Build audit trails from day one.

### 4.6 Red Teaming

**Promptfoo** (acquired by OpenAI March 2026, $86M): 22,351 GitHub stars, 50+ vulnerability types, YAML-driven, CI/CD integration. MIT license, still open-source. Scans for prompt injection, jailbreaks, PII leaks, tool misuse, toxic content.

**MITRE ATLAS** provides the taxonomy for structuring red team tests and reporting findings. Process: map attack surface (training pipeline, model serving, inference API, vector database, agent tools) to ATLAS techniques.

**Adversa AI 2025 report**: 35% of real-world AI security incidents caused by simple prompts, some leading to losses exceeding $100,000/incident. Only 34.7% of organizations have deployed dedicated prompt injection defenses.

---

## 5. Production Enterprise Code

### 5.1 Async Guardrail Pipeline with Circuit Breaker, Retry, and Batch Accumulation

```python
"""
Production guardrail pipeline: async classifier chain with circuit breaker,
retry with exponential backoff + jitter, batch accumulation for classifier
GPU efficiency, and structured logging.
"""

import asyncio
import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import structlog

# ── Structured logging ──────────────────────────────────────────────────────

logger = structlog.get_logger()
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)


# ── Data classes ────────────────────────────────────────────────────────────

class GuardrailVerdict(Enum):
    PASS = "pass"
    BLOCK = "block"
    FLAG = "flag"       # route to heavier classifier
    UNKNOWN = "unknown"  # circuit open, fallback applied


class FailMode(Enum):
    CLOSED = "closed"  # block on classifier failure (regulated)
    OPEN = "open"      # allow on classifier failure (low-risk)


@dataclass
class GuardrailResult:
    verdict: GuardrailVerdict
    layer: str
    latency_ms: float
    details: dict = field(default_factory=dict)
    trace_id: str = ""


@dataclass
class PipelineResult:
    final_verdict: GuardrailVerdict
    layer_results: list[GuardrailResult] = field(default_factory=list)
    total_latency_ms: float = 0.0
    trace_id: str = ""


# ── Circuit Breaker ─────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Three-state circuit breaker: CLOSED (normal) -> OPEN (failing) -> HALF_OPEN (probe).
    Prevents cascading failures when a guardrail classifier degrades.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
        half_open_max_calls: int = 3,
        name: str = "default",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.half_open_max_calls = half_open_max_calls
        self.name = name

        self._failure_count = 0
        self._last_failure_time = 0.0
        self._state = "closed"
        self._half_open_calls = 0

    @property
    def state(self) -> str:
        if self._state == "open":
            if time.monotonic() - self._last_failure_time > self.recovery_timeout_s:
                self._state = "half_open"
                self._half_open_calls = 0
        return self._state

    def record_success(self) -> None:
        if self._state == "half_open":
            self._half_open_calls += 1
            if self._half_open_calls >= self.half_open_max_calls:
                self._state = "closed"
                self._failure_count = 0
                logger.info("circuit_breaker.closed", breaker=self.name)
        else:
            self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            logger.warning(
                "circuit_breaker.opened",
                breaker=self.name,
                failures=self._failure_count,
            )

    def allow_request(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open":
            return self._half_open_calls < self.half_open_max_calls
        return False  # open


# ── Retry with Exponential Backoff + Jitter ─────────────────────────────────

async def retry_with_backoff(
    coro_factory,
    max_retries: int = 3,
    base_delay_s: float = 0.1,
    max_delay_s: float = 2.0,
    circuit_breaker: Optional[CircuitBreaker] = None,
    operation_name: str = "unknown",
):
    """
    Retry an async callable with exponential backoff + full jitter.
    Respects circuit breaker state. Returns result or raises last exception.
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        if circuit_breaker and not circuit_breaker.allow_request():
            logger.warning(
                "retry.circuit_open",
                operation=operation_name,
                attempt=attempt,
            )
            raise CircuitOpenError(f"Circuit open for {operation_name}")
        try:
            result = await coro_factory()
            if circuit_breaker:
                circuit_breaker.record_success()
            return result
        except CircuitOpenError:
            raise
        except Exception as e:
            last_exception = e
            if circuit_breaker:
                circuit_breaker.record_failure()
            if attempt < max_retries:
                delay = min(base_delay_s * (2 ** attempt), max_delay_s)
                jitter = random.uniform(0, delay)  # full jitter
                logger.warning(
                    "retry.backoff",
                    operation=operation_name,
                    attempt=attempt + 1,
                    delay_s=round(jitter, 3),
                    error=str(e),
                )
                await asyncio.sleep(jitter)
    raise last_exception


class CircuitOpenError(Exception):
    pass


# ── PII Redaction (regex + placeholder, always-on) ──────────────────────────

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone_us": re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "pan_india": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
}


def redact_pii(text: str) -> tuple[str, dict]:
    """
    Fast regex-based PII redaction. Returns redacted text and detection counts.
    Runs on every request before any cloud LLM call.
    """
    detections = {}
    redacted = text
    for pii_type, pattern in PII_PATTERNS.items():
        matches = pattern.findall(redacted)
        if matches:
            detections[pii_type] = len(matches)
            redacted = pattern.sub(f"[REDACTED_{pii_type.upper()}]", redacted)
    return redacted, detections


# ── Batch Accumulation for Classifier GPU ───────────────────────────────────

class BatchAccumulator:
    """
    Collects classifier requests for an accumulation window (default 5ms),
    batches into a single GPU call, fans out responses.
    Converts N sequential 30ms calls into a single 35ms batched call.
    """

    def __init__(
        self,
        classify_fn,
        accumulation_ms: float = 5.0,
        max_batch_size: int = 16,
    ):
        self._classify_fn = classify_fn
        self._accumulation_ms = accumulation_ms
        self._max_batch_size = max_batch_size
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._batch_loop())

    async def classify(self, text: str) -> dict:
        future = asyncio.get_event_loop().create_future()
        await self._queue.put((text, future))
        return await future

    async def _batch_loop(self) -> None:
        while self._running:
            batch = []
            # Wait for at least one item
            try:
                item = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=1.0,
                )
                batch.append(item)
            except asyncio.TimeoutError:
                continue
            # Accumulate for window or until max batch
            deadline = asyncio.get_event_loop().time() + self._accumulation_ms / 1000
            while len(batch) < self._max_batch_size:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=remaining,
                    )
                    batch.append(item)
                except asyncio.TimeoutError:
                    break
            # Execute batch
            texts = [text for text, _ in batch]
            try:
                results = await self._classify_fn(texts)
                for (_, future), result in zip(batch, results):
                    future.set_result(result)
            except Exception as e:
                for _, future in batch:
                    future.set_exception(e)


# ── Guardrail Pipeline ──────────────────────────────────────────────────────

class GuardrailPipeline:
    """
    Four-layer guardrail pipeline with per-layer circuit breakers,
    risk-proportional routing, and configurable fail mode.
    """

    def __init__(
        self,
        input_classifier,       # async fn(str) -> dict with "verdict"
        content_classifier,     # async fn(str) -> dict with "verdict", "categories"
        fail_mode: FailMode = FailMode.CLOSED,
        content_sample_rate: float = 0.10,   # sample 10% for heavy classifier
    ):
        self.input_classifier = input_classifier
        self.content_classifier = content_classifier
        self.fail_mode = fail_mode
        self.content_sample_rate = content_sample_rate

        self._input_cb = CircuitBreaker(name="input_gate", failure_threshold=5)
        self._content_cb = CircuitBreaker(name="content_safety", failure_threshold=3)

    async def run(self, text: str, trace_id: str = "") -> PipelineResult:
        if not trace_id:
            trace_id = hashlib.sha256(
                f"{text[:100]}{time.time()}".encode()
            ).hexdigest()[:16]

        result = PipelineResult(
            final_verdict=GuardrailVerdict.PASS,
            trace_id=trace_id,
        )
        pipeline_start = time.monotonic()

        # ── Layer 1: PII Redaction (always-on, CPU, <10ms) ──────────
        t0 = time.monotonic()
        redacted_text, pii_detections = redact_pii(text)
        pii_ms = (time.monotonic() - t0) * 1000

        result.layer_results.append(GuardrailResult(
            verdict=GuardrailVerdict.PASS,
            layer="pii_redaction",
            latency_ms=round(pii_ms, 2),
            details={"detections": pii_detections},
            trace_id=trace_id,
        ))

        if pii_detections:
            logger.info(
                "guardrail.pii_redacted",
                trace_id=trace_id,
                detections=pii_detections,
            )

        # ── Layer 1b: Input Gate (Prompt Guard 2, 20-50ms) ──────────
        input_result = await self._run_input_gate(redacted_text, trace_id)
        result.layer_results.append(input_result)

        if input_result.verdict == GuardrailVerdict.BLOCK:
            result.final_verdict = GuardrailVerdict.BLOCK
            result.total_latency_ms = (time.monotonic() - pipeline_start) * 1000
            logger.warning(
                "guardrail.blocked",
                trace_id=trace_id,
                layer="input_gate",
                details=input_result.details,
            )
            return result

        # ── Layer 3: Content Safety (sampled or flagged, ~460ms) ────
        should_run_content = (
            input_result.verdict == GuardrailVerdict.FLAG
            or random.random() < self.content_sample_rate
        )

        if should_run_content:
            content_result = await self._run_content_safety(
                redacted_text, trace_id
            )
            result.layer_results.append(content_result)
            if content_result.verdict == GuardrailVerdict.BLOCK:
                result.final_verdict = GuardrailVerdict.BLOCK
                result.total_latency_ms = (
                    time.monotonic() - pipeline_start
                ) * 1000
                logger.warning(
                    "guardrail.blocked",
                    trace_id=trace_id,
                    layer="content_safety",
                    details=content_result.details,
                )
                return result

        result.total_latency_ms = (time.monotonic() - pipeline_start) * 1000
        logger.info(
            "guardrail.passed",
            trace_id=trace_id,
            total_latency_ms=round(result.total_latency_ms, 2),
            layers_run=len(result.layer_results),
        )
        return result

    async def _run_input_gate(
        self, text: str, trace_id: str
    ) -> GuardrailResult:
        t0 = time.monotonic()
        try:
            response = await retry_with_backoff(
                coro_factory=lambda: self.input_classifier(text),
                max_retries=2,
                base_delay_s=0.05,
                circuit_breaker=self._input_cb,
                operation_name="input_gate",
            )
            latency_ms = (time.monotonic() - t0) * 1000
            verdict_str = response.get("verdict", "pass")
            verdict = {
                "pass": GuardrailVerdict.PASS,
                "block": GuardrailVerdict.BLOCK,
                "flag": GuardrailVerdict.FLAG,
            }.get(verdict_str, GuardrailVerdict.PASS)

            return GuardrailResult(
                verdict=verdict,
                layer="input_gate",
                latency_ms=round(latency_ms, 2),
                details=response,
                trace_id=trace_id,
            )
        except CircuitOpenError:
            return self._fallback_result(
                "input_gate", t0, trace_id, "circuit_open"
            )
        except Exception as e:
            logger.error(
                "guardrail.input_gate_error",
                trace_id=trace_id,
                error=str(e),
            )
            return self._fallback_result(
                "input_gate", t0, trace_id, str(e)
            )

    async def _run_content_safety(
        self, text: str, trace_id: str
    ) -> GuardrailResult:
        t0 = time.monotonic()
        try:
            response = await retry_with_backoff(
                coro_factory=lambda: self.content_classifier(text),
                max_retries=1,       # fewer retries for slow classifier
                base_delay_s=0.2,
                circuit_breaker=self._content_cb,
                operation_name="content_safety",
            )
            latency_ms = (time.monotonic() - t0) * 1000
            verdict_str = response.get("verdict", "pass")
            verdict = {
                "pass": GuardrailVerdict.PASS,
                "block": GuardrailVerdict.BLOCK,
                "flag": GuardrailVerdict.FLAG,
            }.get(verdict_str, GuardrailVerdict.PASS)

            return GuardrailResult(
                verdict=verdict,
                layer="content_safety",
                latency_ms=round(latency_ms, 2),
                details=response,
                trace_id=trace_id,
            )
        except CircuitOpenError:
            return self._fallback_result(
                "content_safety", t0, trace_id, "circuit_open"
            )
        except Exception as e:
            logger.error(
                "guardrail.content_safety_error",
                trace_id=trace_id,
                error=str(e),
            )
            return self._fallback_result(
                "content_safety", t0, trace_id, str(e)
            )

    def _fallback_result(
        self, layer: str, t0: float, trace_id: str, reason: str
    ) -> GuardrailResult:
        latency_ms = (time.monotonic() - t0) * 1000
        if self.fail_mode == FailMode.CLOSED:
            verdict = GuardrailVerdict.BLOCK
            logger.warning(
                "guardrail.fail_closed",
                layer=layer,
                trace_id=trace_id,
                reason=reason,
            )
        else:
            verdict = GuardrailVerdict.UNKNOWN
            logger.warning(
                "guardrail.fail_open",
                layer=layer,
                trace_id=trace_id,
                reason=reason,
            )
        return GuardrailResult(
            verdict=verdict,
            layer=layer,
            latency_ms=round(latency_ms, 2),
            details={"fallback": True, "reason": reason},
            trace_id=trace_id,
        )
```

### 5.2 LLM-as-Judge Evaluator with Critique-First Scoring

```python
"""
LLM-as-judge evaluator: critique-first format (judge explains assessment
before verdict), position-bias mitigation via answer-order swapping,
structured output, and calibration tracking.
"""

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Optional

import structlog

logger = structlog.get_logger()


@dataclass
class EvalCase:
    input_text: str
    expected_behavior: str
    system_output: str
    supporting_evidence: str = ""
    prompt_version: str = ""
    model_version: str = ""


@dataclass
class JudgeVerdict:
    verdict: str          # "pass" or "fail"
    critique: str         # explanation BEFORE verdict
    confidence: float     # 0-1
    latency_ms: float
    judge_model: str
    eval_case_hash: str


JUDGE_SYSTEM_PROMPT = """You are an evaluation judge. You will assess whether
an AI system's output meets the expected behavior for a given input.

IMPORTANT: You MUST provide your critique and reasoning FIRST, then your
verdict. This prevents post-hoc justification.

Respond in this exact JSON format:
{
    "critique": "<your detailed assessment of the output quality>",
    "verdict": "pass" or "fail",
    "confidence": <float 0-1>
}

Evaluation criteria:
- Does the output address the user's actual question?
- Is the output factually consistent with the supporting evidence?
- Does the output meet the expected behavior description?
"""


class LLMJudgeEvaluator:

    def __init__(
        self,
        llm_client,
        judge_model: str = "claude-sonnet-4-20250514",
        max_concurrent: int = 10,
    ):
        self.llm_client = llm_client
        self.judge_model = judge_model
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def evaluate(self, case: EvalCase) -> JudgeVerdict:
        async with self._semaphore:
            return await self._evaluate_single(case)

    async def evaluate_batch(
        self,
        cases: list[EvalCase],
        runs_per_case: int = 3,
    ) -> list[dict]:
        """
        Run each case multiple times (default 3) to distinguish signal
        from sampling noise. Returns aggregated results.
        """
        all_results = []
        for case in cases:
            case_verdicts = await asyncio.gather(
                *[self._evaluate_single(case) for _ in range(runs_per_case)]
            )
            pass_count = sum(
                1 for v in case_verdicts if v.verdict == "pass"
            )
            all_results.append({
                "case_hash": case_verdicts[0].eval_case_hash,
                "pass_rate": pass_count / runs_per_case,
                "verdicts": case_verdicts,
                "consensus": "pass" if pass_count > runs_per_case / 2 else "fail",
                "avg_confidence": sum(v.confidence for v in case_verdicts) / runs_per_case,
            })
        return all_results

    async def pairwise_compare(
        self,
        input_text: str,
        output_a: str,
        output_b: str,
    ) -> dict:
        """
        Pairwise comparison with position-bias mitigation:
        run twice with swapped order, average results.
        """
        result_ab = await self._pairwise_single(
            input_text, output_a, output_b, "A", "B"
        )
        result_ba = await self._pairwise_single(
            input_text, output_b, output_a, "B", "A"
        )
        a_wins = sum([
            1 if result_ab.get("winner") == "A" else 0,
            1 if result_ba.get("winner") == "B" else 0,
        ])
        return {
            "winner": "A" if a_wins > 1 else "B" if a_wins < 1 else "tie",
            "position_bias_detected": result_ab.get("winner") != (
                "B" if result_ba.get("winner") == "A"
                else "A" if result_ba.get("winner") == "B"
                else result_ab.get("winner")
            ),
            "run_ab": result_ab,
            "run_ba": result_ba,
        }

    async def _evaluate_single(self, case: EvalCase) -> JudgeVerdict:
        import hashlib
        case_hash = hashlib.sha256(
            f"{case.input_text}{case.expected_behavior}".encode()
        ).hexdigest()[:12]

        user_prompt = (
            f"## Input\n{case.input_text}\n\n"
            f"## Expected Behavior\n{case.expected_behavior}\n\n"
            f"## System Output\n{case.system_output}\n\n"
        )
        if case.supporting_evidence:
            user_prompt += (
                f"## Supporting Evidence\n{case.supporting_evidence}\n\n"
            )

        t0 = time.monotonic()
        response = await self.llm_client.create_message(
            model=self.judge_model,
            system=JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=1024,
        )
        latency_ms = (time.monotonic() - t0) * 1000

        parsed = json.loads(response.content)
        verdict = JudgeVerdict(
            verdict=parsed["verdict"],
            critique=parsed["critique"],
            confidence=parsed.get("confidence", 0.5),
            latency_ms=round(latency_ms, 2),
            judge_model=self.judge_model,
            eval_case_hash=case_hash,
        )
        logger.info(
            "eval.judge_verdict",
            case_hash=case_hash,
            verdict=verdict.verdict,
            confidence=verdict.confidence,
            latency_ms=verdict.latency_ms,
            prompt_version=case.prompt_version,
            model_version=case.model_version,
        )
        return verdict

    async def _pairwise_single(
        self,
        input_text: str,
        first_output: str,
        second_output: str,
        first_label: str,
        second_label: str,
    ) -> dict:
        prompt = (
            f"## Input\n{input_text}\n\n"
            f"## Output {first_label}\n{first_output}\n\n"
            f"## Output {second_label}\n{second_output}\n\n"
            f"Which output better addresses the input? Explain first, "
            f"then state winner as {first_label} or {second_label}.\n"
            f'Respond as JSON: {{"critique": "...", "winner": "{first_label}" or "{second_label}"}}'
        )
        response = await self.llm_client.create_message(
            model=self.judge_model,
            system="You are a pairwise comparison judge. Critique first, then pick a winner.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        return json.loads(response.content)
```

### 5.3 Eval-as-CI Gate with Scorecard and Drift Detection

```python
"""
CI/CD eval gate: runs evaluation suites multiple times for statistical
confidence, generates scorecards, blocks merge on regression, and detects
drift when attached to production sampling.
"""

import statistics
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger()


class SuiteType(Enum):
    REGRESSION = "regression"    # known-good cases, pass rate ~100%
    CAPABILITY = "capability"    # hard edge cases, pass rate < 100%


@dataclass
class EvalMetric:
    name: str
    value: float
    threshold: float       # minimum acceptable value
    is_blocking: bool      # block merge if below threshold
    suite_type: SuiteType


@dataclass
class Scorecard:
    run_id: str
    prompt_version: str
    model_version: str
    metrics: list[EvalMetric]
    runs: int                  # how many times suite was executed
    pass_rate_mean: float
    pass_rate_std: float
    blocking_failures: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def should_block_merge(self) -> bool:
        return len(self.blocking_failures) > 0


class EvalCIGate:

    def __init__(
        self,
        regression_threshold: float = 0.98,    # regression suite: near 100%
        capability_floor: float = 0.30,         # capability suite: >0 but <100%
        capability_ceiling: float = 0.95,       # if exceeded, suite lost discriminative power
        runs_per_change: int = 5,               # 3-5x for statistical confidence
        drift_alert_std: float = 2.0,           # alert if metric drifts >2 std from baseline
    ):
        self.regression_threshold = regression_threshold
        self.capability_floor = capability_floor
        self.capability_ceiling = capability_ceiling
        self.runs_per_change = runs_per_change
        self.drift_alert_std = drift_alert_std
        self._baseline_metrics: dict[str, list[float]] = {}

    def generate_scorecard(
        self,
        run_results: list[list[EvalMetric]],
        run_id: str,
        prompt_version: str,
        model_version: str,
    ) -> Scorecard:
        """
        Aggregate results from multiple evaluation runs into a scorecard.
        Each run_results entry is a list of EvalMetric from one suite execution.
        """
        # Aggregate pass rates across runs
        pass_rates = []
        for run_metrics in run_results:
            passed = sum(1 for m in run_metrics if m.value >= m.threshold)
            pass_rates.append(passed / len(run_metrics) if run_metrics else 0)

        mean_rate = statistics.mean(pass_rates)
        std_rate = statistics.stdev(pass_rates) if len(pass_rates) > 1 else 0.0

        # Check blocking conditions
        blocking_failures = []
        # Aggregate metrics by name across runs
        metric_values: dict[str, list[float]] = {}
        for run_metrics in run_results:
            for m in run_metrics:
                metric_values.setdefault(m.name, []).append(m.value)

        aggregated_metrics = []
        for run_metrics in run_results[0]:  # use first run's structure
            name = run_metrics.name
            values = metric_values.get(name, [])
            avg_value = statistics.mean(values) if values else 0

            aggregated_metrics.append(EvalMetric(
                name=name,
                value=round(avg_value, 4),
                threshold=run_metrics.threshold,
                is_blocking=run_metrics.is_blocking,
                suite_type=run_metrics.suite_type,
            ))

            if run_metrics.is_blocking and avg_value < run_metrics.threshold:
                blocking_failures.append(
                    f"{name}: {avg_value:.4f} < {run_metrics.threshold:.4f}"
                )

            # Eval saturation warning
            if (
                run_metrics.suite_type == SuiteType.CAPABILITY
                and avg_value > self.capability_ceiling
            ):
                logger.warning(
                    "eval.saturation",
                    metric=name,
                    value=avg_value,
                    ceiling=self.capability_ceiling,
                    action="Add harder tasks to maintain discriminative power",
                )

        scorecard = Scorecard(
            run_id=run_id,
            prompt_version=prompt_version,
            model_version=model_version,
            metrics=aggregated_metrics,
            runs=len(run_results),
            pass_rate_mean=round(mean_rate, 4),
            pass_rate_std=round(std_rate, 4),
            blocking_failures=blocking_failures,
        )

        if scorecard.should_block_merge:
            logger.error(
                "eval.merge_blocked",
                run_id=run_id,
                failures=blocking_failures,
                prompt_version=prompt_version,
                model_version=model_version,
            )
        else:
            logger.info(
                "eval.merge_allowed",
                run_id=run_id,
                pass_rate=scorecard.pass_rate_mean,
                runs=scorecard.runs,
            )

        return scorecard

    def check_drift(
        self,
        metric_name: str,
        current_value: float,
    ) -> Optional[dict]:
        """
        Compare current production metric against baseline.
        Returns alert dict if drift exceeds threshold, None otherwise.
        """
        baseline = self._baseline_metrics.get(metric_name, [])
        if len(baseline) < 10:
            # Not enough data for drift detection
            self._baseline_metrics.setdefault(metric_name, []).append(
                current_value
            )
            return None

        mean = statistics.mean(baseline)
        std = statistics.stdev(baseline)
        if std == 0:
            return None

        z_score = (current_value - mean) / std
        if abs(z_score) > self.drift_alert_std:
            alert = {
                "metric": metric_name,
                "current": current_value,
                "baseline_mean": round(mean, 4),
                "baseline_std": round(std, 4),
                "z_score": round(z_score, 2),
                "action": "Investigate and add failing cases to golden test set",
            }
            logger.warning("eval.drift_detected", **alert)
            return alert

        # Update rolling baseline
        self._baseline_metrics[metric_name].append(current_value)
        if len(self._baseline_metrics[metric_name]) > 100:
            self._baseline_metrics[metric_name] = (
                self._baseline_metrics[metric_name][-100:]
            )
        return None
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Regulated-Industry Guardrail Gateway for a Healthcare AI Platform

**Problem statement.** A healthcare SaaS company deploys an LLM-powered clinical decision support system used by 5,000 physicians across 200 hospitals. The system retrieves patient records (containing PHI) and medical literature to generate diagnostic suggestions. Requirements: HIPAA compliance (BAA required for any cloud LLM), SOC 2 Type II certification, EU AI Act high-risk classification (healthcare AI), sub-2s total response time, and zero tolerance for PHI leaking to cloud providers. The company uses multiple LLM providers (Anthropic via AWS Bedrock, self-hosted Llama) and must prove to regulators why any specific suggestion was made six months after the fact.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                        PHYSICIAN INTERFACE                                       │
│                   (EMR-integrated, browser-based)                                │
└──────────────────────────────────┬───────────────────────────────────────────────┘
                                   │ HTTPS + mTLS
┌──────────────────────────────────▼───────────────────────────────────────────────┐
│                         AI GATEWAY (single enforcement point)                    │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 1: INPUT GATE  [always-on, <50ms total]                               │ │
│  │                                                                              │ │
│  │  ┌──────────────┐ ┌──────────────────┐ ┌─────────────────────────────────┐  │ │
│  │  │ Prompt Guard 2│ │ PII Redactor     │ │ Input Normalizer               │  │ │
│  │  │ (86M, 20ms)  │ │ regex + NER      │ │ Strip hidden chars, decode     │  │ │
│  │  │              │ │ SSN,MRN,DOB -->   │ │ Base64/Hex, length cap         │  │ │
│  │  │ Injection    │ │ [REDACTED_*]      │ │                                │  │ │
│  │  │ detection    │ │ BEFORE cloud call │ │ Fail: reject malformed input   │  │ │
│  │  └──────────────┘ └──────────────────┘ └─────────────────────────────────┘  │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 2: SEMANTIC GUARD  [build-time + runtime, <20ms]                      │ │
│  │                                                                              │ │
│  │  Topic control: only clinical queries  │  Retrieval rail: filter RAG chunks │ │
│  │  System prompt: delimiter-hardened     │  before context window injection   │ │
│  │  Reject: non-medical queries           │  Remove injected instructions       │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │ LLM ROUTER  [provider failover, ~10ms routing]                              │ │
│  │                                                                              │ │
│  │  Primary: Anthropic via AWS Bedrock (BAA-covered)                           │ │
│  │  Fallback: Self-hosted Llama 3 on VPC (no external data transmission)       │ │
│  │  Circuit breaker: if primary p99 > 3s, route to fallback                    │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 3: OUTPUT FILTER  [risk-proportional, 50-500ms]                       │ │
│  │                                                                              │ │
│  │  Always: Structured output validation (JSON schema for diagnosis format)    │ │
│  │  Always: PII leak scan on output (catch model-hallucinated PHI)             │ │
│  │  Sampled (20%): Llama Guard 8B content safety + hallucination flag          │ │
│  │  High-risk only: Factuality check against medical KB (async, ~2s)           │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │ LAYER 4: EXECUTION GATE  [FAIL-CLOSED]                                      │ │
│  │                                                                              │ │
│  │  Prescription-class suggestions: require physician confirmation             │ │
│  │  Tool calls: allowlisted EMR APIs only, scoped credentials                 │ │
│  │  All actions: immutable audit log with full OTEL trace                      │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────┬───────────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────────────────────┐
│                    AUDIT & COMPLIANCE STORE                                       │
│                                                                                  │
│  ┌─────────────────────┐  ┌────────────────────┐  ┌─────────────────────────┐   │
│  │ Immutable Trace Store│  │ Decision Archive   │  │ Compliance Reporter     │   │
│  │                      │  │                    │  │                         │   │
│  │ OTEL spans: every    │  │ Input + redacted   │  │ SOC 2 evidence export  │   │
│  │  layer decision,     │  │ prompt + output +  │  │ HIPAA audit response   │   │
│  │  latency, verdict    │  │ guardrail verdicts │  │ EU AI Act conformity   │   │
│  │                      │  │ + model version +  │  │  assessment docs       │   │
│  │ 7-year retention     │  │ prompt version     │  │                         │   │
│  │ Append-only          │  │ Queryable by       │  │ "Show me why AI made   │   │
│  │                      │  │  decision ID       │  │  this decision 6 mo    │   │
│  │                      │  │                    │  │  ago" -- answered in    │   │
│  │                      │  │                    │  │  seconds, not weeks     │   │
│  └─────────────────────┘  └────────────────────┘  └─────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌────────────────────────┬───────────────────────────────┬──────────────────────────┐
│ Decision               │ Option A                      │ Option B (chosen)        │
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ Fail mode              │ Fail-open (higher avail.)     │ Fail-closed (HIPAA       │
│                        │ Physician sees degraded resp  │ requires it; blocked     │
│                        │                               │ request > leaked PHI)    │
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ PII redaction          │ Post-process cleanup          │ Pre-inference redaction  │
│                        │ Simpler; not HIPAA-defensible │ PHI never reaches cloud  │
│                        │                               │ provider; legally sound  │
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ Content safety         │ Run on every output (460ms)   │ Sample 20% + all flagged │
│                        │ Guaranteed coverage; +460ms   │ Keeps p95 < 2s; accepts  │
│                        │ pushes p95 beyond SLA         │ sampling risk on 80%     │
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ LLM provider           │ Single cloud provider         │ Multi-provider with      │
│                        │ Simpler ops; single point     │ gateway failover. Higher │
│                        │ of failure                    │ ops cost; no single      │
│                        │                               │ provider outage blocks   │
│                        │                               │ patient care             │
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ Audit storage          │ Application logs (standard)   │ Immutable trace store    │
│                        │ Cheaper; fragile under audit  │ 7-year retention;        │
│                        │                               │ regulatory query in      │
│                        │                               │ seconds; 2-3x cost but   │
│                        │                               │ avoids retrofit penalty  │
└────────────────────────┴───────────────────────────────┴──────────────────────────┘
```

**Decision rationale.** Fail-closed is mandatory for HIPAA; a blocked request is preferable to PHI exposure. Pre-inference PII redaction (not post-processing) is the legally defensible standard because PHI never reaches the cloud provider. Content safety sampling at 20% plus full coverage on flagged traffic keeps the synchronous path under the 2s SLA while accepting a calculated risk on unsampled clean traffic. Multi-provider routing (Bedrock as primary, self-hosted Llama as fallback) prevents a single cloud outage from blocking clinical workflows. The immutable trace store with 7-year retention costs 2-3x more than application logs but answers regulatory queries in seconds rather than weeks and avoids the 2-3x retrofit penalty documented when governance is added after audit notice.

---

### Scenario 2: Continuous Evaluation Platform for a Multi-Product AI Company

**Problem statement.** An enterprise runs 12 LLM-powered products (customer support chatbot, code assistant, document summarizer, internal search, and 8 domain-specific agents). Each product has different evaluation requirements: the chatbot needs faithfulness and response relevance, the code assistant needs functional correctness, and the agents need outcome-based grading. The company changes prompts ~20 times/week across products, wants to prevent silent regressions, and needs to demonstrate evaluation maturity for SOC 2 audit. Monthly eval budget: $5,000 for LLM-judge costs. Engineering team: 4 ML engineers managing the platform.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         DEVELOPER WORKFLOW                                       │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │ Prompt/Model Change (any of 12 products)                                 │    │
│  │                                                                          │    │
│  │ 1. Engineer modifies prompt or swaps model in product config             │    │
│  │ 2. Opens PR -- CI hook triggers eval pipeline                            │    │
│  │ 3. Cannot merge until scorecard passes all blocking metrics              │    │
│  └──────────────────────────────┬───────────────────────────────────────────┘    │
└─────────────────────────────────┼────────────────────────────────────────────────┘
                                  │ PR webhook
┌─────────────────────────────────▼────────────────────────────────────────────────┐
│                        CI EVALUATION PIPELINE                                    │
│                                                                                  │
│  ┌────────────────────────┐  ┌───────────────────────────────────────────────┐   │
│  │ Product Config Loader   │  │ Eval Suite Runner                             │   │
│  │                         │  │                                               │   │
│  │ Reads product manifest: │  │ Runs suite 3-5x per change                    │   │
│  │  - eval suite ID        │  │ Parallel execution across products            │   │
│  │  - golden test set ref  │──▶│ DeepEval (pytest-native) for general evals   │   │
│  │  - grader config        │  │ RAGAS for RAG-specific products               │   │
│  │  - blocking thresholds  │  │ Promptfoo for red-team scans                  │   │
│  │  - judge model          │  │                                               │   │
│  └────────────────────────┘  └─────────────────────┬─────────────────────────┘   │
│                                                     │ raw scores (3-5 runs)       │
│  ┌──────────────────────────────────────────────────▼─────────────────────────┐   │
│  │ Scorecard Generator                                                        │   │
│  │                                                                            │   │
│  │ Aggregates across runs: mean, std, blocking failures                       │   │
│  │                                                                            │   │
│  │ ┌─────────────────────────┐  ┌─────────────────────────────────────────┐   │   │
│  │ │ Regression Suite Gate    │  │ Capability Suite Monitor                │   │   │
│  │ │                          │  │                                         │   │   │
│  │ │ Threshold: >= 98% pass   │  │ Floor: >= 30% (suite has signal)       │   │   │
│  │ │ BLOCKING: merge fails    │  │ Ceiling: <= 95% (still discriminative) │   │   │
│  │ │  if below                │  │ WARNING: if ceiling exceeded, add      │   │   │
│  │ │                          │  │  harder tasks (eval saturation)        │   │   │
│  │ └─────────────────────────┘  └─────────────────────────────────────────┘   │   │
│  └────────────────────────────────────────────┬──────────────────────────────┘   │
│                                               │ scorecard (pass/block)           │
│  ┌────────────────────────────────────────────▼──────────────────────────────┐   │
│  │ PR Status Reporter                                                        │   │
│  │  Pass: green check + scorecard summary in PR comment                      │   │
│  │  Block: red X + specific failing metrics + suggested actions              │   │
│  └───────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────┘
                                  │ merged change deployed
┌─────────────────────────────────▼────────────────────────────────────────────────┐
│                       STAGING: SHADOW MODE                                       │
│                                                                                  │
│  Every request processed by BOTH incumbent and candidate model                   │
│  Users see only incumbent output; candidate output logged + scored async         │
│  Minimum 1 week for model swaps before proceeding to A/B                        │
│  Same eval suite scores both outputs for direct comparison                       │
└─────────────────────────────────┬────────────────────────────────────────────────┘
                                  │ shadow metrics confirm parity
┌─────────────────────────────────▼────────────────────────────────────────────────┐
│                       PRODUCTION: CONTINUOUS EVALUATION                          │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │ A/B Test Engine                                                          │    │
│  │                                                                          │    │
│  │ User-level randomization (NOT per-request for conversational products)   │    │
│  │ SPRT for continuous monitoring without false positive inflation           │    │
│  │ Metrics: latency, token cost, LLM-judge quality, explicit feedback       │    │
│  │ 5000+ samples/arm for quality metrics (vs 500 for CTR)                   │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │ Production Sampler (1-5% of traces)                                      │    │
│  │                                                                          │    │
│  │ Same eval suite as CI pipeline                                           │    │
│  │ LLM-judge scores attached to production OTEL traces                      │    │
│  │ Cost: ~$150-400/month at 5% sample rate across 12 products               │    │
│  └──────────────────────────────────────────┬───────────────────────────────┘    │
│                                              │ scores + drift signals            │
│  ┌───────────────────────────────────────────▼───────────────────────────────┐   │
│  │ Drift Detector & Feedback Loop                                            │   │
│  │                                                                           │   │
│  │ Z-score drift detection (alert if |z| > 2.0 from baseline)               │   │
│  │ Production failures auto-added to working golden test set                 │   │
│  │ Quarterly: rater agreement audit (150 examples, 2 engineers, K >= 0.7)   │   │
│  │ Quarterly: holdout eval set run (locked, never touched between runs)      │   │
│  │ On saturation: add harder tasks to capability suite                       │   │
│  └───────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────┘
                                  │ audit evidence
┌─────────────────────────────────▼────────────────────────────────────────────────┐
│                       SOC 2 COMPLIANCE EVIDENCE                                  │
│                                                                                  │
│  Eval run history: every scorecard, every PR, every production sample score      │
│  Rater agreement audit reports: quarterly, with Cohen's Kappa                    │
│  Holdout eval results: quarterly, with trend line                                │
│  Drift alert response records: incident, investigation, resolution               │
│  Golden test set version history: additions traced to production failures         │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌────────────────────────┬───────────────────────────────┬──────────────────────────┐
│ Decision               │ Option A                      │ Option B (chosen)        │
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ Eval framework         │ Single framework for all      │ Hybrid: DeepEval (CI) +  │
│                        │ products. Simpler; forces     │ RAGAS (RAG products) +   │
│                        │ one-size-fits-all metrics     │ Promptfoo (red team).    │
│                        │                               │ Higher complexity; each   │
│                        │                               │ product gets right metrics│
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ Judge model            │ Frontier model for all evals  │ Cheapest model passing   │
│                        │ Best accuracy; $1200/month    │ calibration per product. │
│                        │ blows $5K budget at scale     │ Stays within budget;     │
│                        │                               │ consistency > capability  │
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ Production sampling    │ Evaluate every response       │ 1-5% sampling + same     │
│                        │ Complete coverage; $12K+/mo   │ eval suite as CI.        │
│                        │ in judge costs                │ $150-400/month; accepts  │
│                        │                               │ sampling risk             │
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ Suite runs per change  │ 1 run (fast CI)               │ 3-5 runs (statistical    │
│                        │ 3-5x faster feedback; cannot  │ confidence). Slower CI;  │
│                        │ distinguish signal from noise │ prevents false alarms    │
│                        │                               │ from sampling variance    │
├────────────────────────┼───────────────────────────────┼──────────────────────────┤
│ Holdout set policy     │ Run holdout on every PR       │ Locked; quarterly only.  │
│                        │ More feedback; Goodharted     │ Prevents gaming; true    │
│                        │ within weeks                  │ signal on real progress   │
└────────────────────────┴───────────────────────────────┴──────────────────────────┘
```

**Decision rationale.** The hybrid eval framework (DeepEval + RAGAS + Promptfoo) adds operational complexity but gives each product the metrics that match its failure modes -- faithfulness for RAG products, functional correctness for code, outcome grading for agents. A single framework forces artificial metric mapping that produces misleading scores. Choosing the cheapest judge model that passes calibration (30-50 human-labeled examples, per-product) keeps monthly costs within the $5K budget while maintaining adequate agreement with human judgment; consistency across runs matters more than peak capability. Production sampling at 1-5% with the same eval suite as CI creates a closed feedback loop where drift in production feeds back into the golden test set, catching regressions that offline evals miss. Running suites 3-5x per change costs more CI time but prevents the false alarms that erode engineer trust in the eval gate. The quarterly-locked holdout set is the only defense against Goodhart's Law: the working eval set will inevitably be optimized for; the holdout reveals whether that optimization translated to real improvement.
