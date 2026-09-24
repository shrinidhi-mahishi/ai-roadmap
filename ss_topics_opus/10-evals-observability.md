# Module 10: Evals & Observability

> **Audience**: Principal Data Scientist (12+ YOE) preparing for Director/VP-level AI roles.
> **Scope**: End-to-end evaluation and observability for LLM-powered applications and agent systems -- eval taxonomies (unit through trajectory), golden dataset lifecycle, LLM-as-a-judge architectures and bias mitigation, G-Eval and structured evaluation, OpenTelemetry GenAI conventions, CI/CD regression gates, production quality monitoring, platform comparison (LangSmith, Langfuse, Braintrust, Arize Phoenix), and enterprise governance (PII redaction, RBAC, EU AI Act, GDPR).
> **Pricing assumptions**: GPT-4o input $2.50/1M tokens, output $10/1M; GPT-4o-mini input $0.15/1M, output $0.60/1M; Claude Opus 5 input $5/1M, output $25/1M; Claude Haiku 4 input $0.25/1M, output $1.25/1M; Gemini 3.1 Flash input $0.075/1M, output $0.30/1M. All as of Sep 2026.
> **Key references**: Survey on LLM-as-a-Judge (arXiv 2411.15594), G-Eval (EMNLP 2023, arXiv 2303.16634), OpenTelemetry GenAI Semantic Conventions v1.41.0, Anthropic agent eval methodology, DeepEval component-level evals, Hamel Husain & Shreya Shankar practitioner guidance, EU AI Act full enforcement (Aug 2026), OWASP LLM Top 10 (2025).

---

## 1. System Topology & Data Flow

### 1.1 Full-Stack Eval & Observability Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             CONTROL PLANE                                        │
│                                                                                  │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐    │
│  │  EVAL ORCHESTRATOR  │  │  QUALITY GATE        │  │  ALERT MANAGER       │    │
│  │                     │  │  CONTROLLER           │  │                      │    │
│  │  - Schedule runs    │  │                       │  │  - Threshold rules   │    │
│  │  - Dataset select   │  │  - Pre-merge gate     │  │  - Rolling baselines │    │
│  │  - Judge dispatch   │  │  - Nightly sweep      │  │  - Anomaly detection │    │
│  │  - Result aggregate │  │  - Threshold compare  │  │  - PagerDuty/Slack   │    │
│  │  - Feedback ingest  │  │  - PR comment post    │  │  - Escalation chains │    │
│  └────────┬────────────┘  └──────────┬────────────┘  └──────────┬───────────┘    │
│           │                          │                           │                │
└───────────┼──────────────────────────┼───────────────────────────┼────────────────┘
            │                          │                           │
┌───────────┼──────────────────────────┼───────────────────────────┼────────────────┐
│           v          DATA PLANE      v                           v                │
│                                                                                  │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐    │
│  │  TRACE COLLECTOR    │  │  EVAL RUNNER         │  │  METRIC AGGREGATOR   │    │
│  │                     │  │                      │  │                      │    │
│  │  - OTel SDK spans   │  │  - Deterministic     │  │  - P50/P95/P99 lat.  │    │
│  │  - GenAI semantic   │  │    checks (JSON,     │  │  - Token usage/cost  │    │
│  │    conventions      │  │    PII, length)      │  │  - Quality scores    │    │
│  │  - PII redaction    │  │  - LLM-as-judge      │  │  - Error rates       │    │
│  │  - Cost attribution │  │    (pointwise,       │  │  - Throughput         │    │
│  │  - Context prop.    │  │    pairwise, ref)    │  │  - Drift detection   │    │
│  └────────┬────────────┘  │  - Trajectory eval   │  └──────────┬───────────┘    │
│           │               │  - Code evaluators   │             │                │
│           │               └──────────┬───────────┘             │                │
│           │                          │                          │                │
│  ┌────────v────────────┐  ┌──────────v───────────┐             │                │
│  │  JUDGE LLM POOL     │  │  BIAS MITIGATOR      │             │                │
│  │                     │  │                      │             │                │
│  │  - GPT-4o-mini      │  │  - Swap-and-average  │             │                │
│  │    (pre-merge)      │  │  - Multi-family      │             │                │
│  │  - GPT-4o / Opus    │  │    ensemble          │             │                │
│  │    (nightly)        │  │  - Length control     │             │                │
│  │  - Cross-family     │  │  - Calibration       │             │                │
│  │    ensemble         │  │    examples          │             │                │
│  └─────────────────────┘  └──────────────────────┘             │                │
│                                                                 │                │
└─────────────────────────────────────────────────────────────────┼────────────────┘
                                                                  │
┌─────────────────────────────────────────────────────────────────┼────────────────┐
│                         PERSISTENCE LAYER                       v                │
│                                                                                  │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐    │
│  │  TRACE STORE        │  │  EVAL DATASETS       │  │  METRIC TIME-SERIES  │    │
│  │                     │  │                      │  │                      │    │
│  │  Langfuse / Phoenix │  │  Golden datasets     │  │  Prometheus /        │    │
│  │  / LangSmith        │  │  (versioned, JSONL   │  │  InfluxDB            │    │
│  │                     │  │  or platform-native) │  │                      │    │
│  │  Hot: 30-90 days    │  │  Schema + labels     │  │  Hot: 30 days        │    │
│  │  Warm: 6 months     │  │  tracked in git      │  │  Cold: S3/GCS        │    │
│  │  Cold: S3/GCS       │  │  Append-mostly log   │  │  (years for EU AI    │    │
│  │  (years for audit)  │  │  Quarterly refresh   │  │   Act compliance)    │    │
│  └─────────────────────┘  └─────────────────────┘  └──────────────────────┘    │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  ANNOTATION STORE                                                       │    │
│  │                                                                         │    │
│  │  Human feedback (thumbs up/down, corrections), judge rationale logs,    │    │
│  │  calibration labels, production failure cases feeding back to golden     │    │
│  │  datasets. Immutable append log with crypto-shredding for GDPR.         │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────┼───────────────────────────────────────────┐
│                    TOOL PROXIES      v                                            │
│                                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐  │
│  │ LangSmith│ │ Langfuse │ │ Arize    │ │Braintrust│ │ OTel Collector       │  │
│  │          │ │          │ │ Phoenix  │ │          │ │                      │  │
│  │ Trace    │ │ @observe │ │ OTel-    │ │ Eval-    │ │ - Tail-based sample  │  │
│  │ trees,   │ │ decorator│ │ native,  │ │ first    │ │ - PII redaction proc │  │
│  │ dataset  │ │ MIT, self│ │ ELv2,    │ │ SOC2,    │ │ - Cost attribution   │  │
│  │ mgmt,    │ │ host,    │ │ self-    │ │ HIPAA,   │ │ - Export to any      │  │
│  │ annot.   │ │ 21k stars│ │ host,    │ │ SSO      │ │   backend            │  │
│  │ queues   │ │          │ │ 10k stars│ │          │ │                      │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘  │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────┼───────────────────────────────────────────┐
│              TELEMETRY / OBSERVABILITY SINKS                                     │
│                                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │ Grafana      │  │ Datadog      │  │ Honeycomb    │  │ New Relic        │    │
│  │ Dashboards   │  │ LLM Obs.     │  │ OTel-native  │  │ OTel-native      │    │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────┘    │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narratives

**Trace collection flow:**

```
User Request
    │
    v
┌────────────┐  1. Create root span    ┌─────────────────┐
│  Gateway   │ ────────────────────>   │  OTel SDK        │
│            │                         │  (GenAI Semconv)  │
└────────────┘                         └────────┬─────────┘
                                                │
    2. Propagate trace context (W3C traceparent) │
    across all downstream services               │
                                                v
    ┌──────────────────────────────────────────────────────────┐
    │  LLM Call → child span (gen_ai.client, model, tokens)   │
    │  Tool Call → child span (gen_ai.tool, name, args)       │
    │  Retrieval → child span (retriever, query, k, scores)   │
    │  Sub-Agent → child span (gen_ai.agent, task, handoff)   │
    └──────────────────────────────┬───────────────────────────┘
                                   │
                                   v
    ┌──────────────────────────────────────────────────┐
    │  OTel Collector                                   │
    │  1. PII redaction processor (regex + NER)         │
    │  2. Cost attribution per span                     │
    │  3. Sampling decision (head + tail hybrid)        │
    │  4. Export to trace store + metrics store          │
    └──────────────────────────────────────────────────┘
```

**Eval execution flow:**

```
Trigger (CI commit / nightly cron / production sample)
    │
    v
┌──────────────────┐   1. Select dataset    ┌──────────────────┐
│ Eval Orchestrator│ ─────────────────────> │ Eval Datasets    │
│                  │   (pre-merge: 50-100   │ (versioned)      │
│                  │    nightly: 500+)      └──────────────────┘
│                  │
│                  │   2. Run app against each case
│                  │ ─────────────────────> [Application under test]
│                  │                              │
│                  │   3. Collect outputs          v
│                  │ <──────────────────────── outputs + traces
│                  │
│                  │   4. Deterministic checks first (ms, free)
│                  │      JSON schema, PII absence, length bounds
│                  │
│                  │   5. LLM-as-judge on passing outputs
│                  │ ─────────────────────> [Judge LLM Pool]
│                  │                         - Swap-and-average
│                  │                         - Calibration examples
│                  │   6. Aggregate scores   - Binary or 1-5 scale
│                  │ <────────────────────── per-metric scores
└────────┬─────────┘
         │
         v
┌──────────────────┐   7. Compare to baseline thresholds
│ Quality Gate     │      - Per-metric: fail if >5% drop
│ Controller       │      - Per-category: fail if >10% drop
│                  │      - Cost/latency: fail if >2x baseline
│                  │
│                  │   8. Post PR comment or dashboard update
└──────────────────┘
```

**Regression gate check flow:**

```
Developer pushes prompt/model/retrieval change
    │
    v
┌────────────────┐                    ┌─────────────────────────────────┐
│ GitHub Actions │  trigger on        │  Pre-merge Gate (minutes)       │
│ / GitLab CI   │ ────────────────>  │                                 │
└────────────────┘  path match:       │  1. Load 50-100 representative  │
                    prompts/**,       │     cases from golden dataset   │
                    config/model*,    │  2. Run app, collect outputs    │
                    retrieval/**      │  3. Deterministic checks        │
                                      │  4. LLM-judge (cheap model)     │
                                      │  5. Each case run 3-5x          │
                                      │  6. Require 4/5 pass            │
                                      │  7. Compare to stored baseline  │
                                      │  8. Post results to PR          │
                                      └───────────┬─────────────────────┘
                                                  │
                                         ┌────────v────────┐
                                         │  Pass?          │
                                         │  Yes → merge ok │
                                         │  No  → block    │
                                         └─────────────────┘
```

---

## 2. Core Mechanics & Algorithms

### 2.1 Eval Taxonomy: Four Layers

Evaluation runs at four layers, each with distinct datasets, methods, and cost profiles. The compounding error problem drives the need for trajectory-level evaluation: 95% accuracy per step across 8 steps yields only ~66% end-to-end success.

**Unit evals** test single output properties in milliseconds using deterministic code checks. Examples: JSON parses correctly, no PII in output, label is in the allowed set, output length within bounds. Zero LLM cost, perfectly reproducible. These are the cheapest and fastest -- always run first.

**Component/integration evals** test individual spans (retrievers, tool calls, LLM generations) in seconds using per-span trace metrics. Examples: retrieval precision at k, tool-call argument validity, generation coherence. DeepEval's `@observe` decorator makes each span a unit-testable component.

**End-to-end evals** test full task completion from input to final output using LLM-as-judge or golden dataset comparison. Runtime seconds to minutes. This is where most teams start and stop -- but it misses tool-selection regressions, retrieval failures, and plan deviations buried in 10-50 span agent runs.

**Trajectory evals** score the entire execution path -- every tool call, reasoning step, intermediate decision. Seven standardized metrics (FutureAGI's AgentTrajectoryInput):

1. **TaskCompletion** -- did the agent accomplish the goal?
2. **StepEfficiency** -- did it take a reasonable number of steps (vs. baseline)?
3. **ToolSelectionAccuracy** -- did it pick the right tools for each sub-task?
4. **TrajectoryScore** -- overall path quality (composite)
5. **GoalProgress** -- incremental progress tracking across steps
6. **ActionSafety** -- no policy-violating intermediate actions
7. **ReasoningQuality** -- sound logic in planning and execution

Three metric categories have crystallized: **deterministic** (exact match, BLEU, ROUGE, BERTScore, JSON-schema validity -- zero cost, perfectly reproducible), **rubric-based** (LLM-as-judge or human scoring -- expensive but captures semantic quality), and **composite** (weighted combinations). Most production systems use a layered approach: deterministic first, LLM-judge for what passes deterministic, human review for calibration.

### 2.2 Golden Dataset Construction, Maintenance, and Versioning

A golden dataset is a curated, versioned collection of (input, context, expected_output, rationale) tuples that serves as the source of truth for measuring quality.

**Construction sources (in order of value):**

1. **Real production failures**: Thumbs-down feedback, abandoned sessions, human escalations, flagged low-confidence outputs. These are the cases that actually hurt you.
2. **Expert-crafted edge cases**: Domain experts construct adversarial inputs targeting known weaknesses -- boundary conditions, ambiguous queries, multi-step reasoning chains.
3. **Synthetic expansions**: LLM-generated variations for underrepresented scenarios. Always human-reviewed before inclusion. Documentation-derived or purely synthetic datasets are too clean and miss real edge cases.

**Sizing guidance:**

- Start with 20-50 reviewed items covering the most important behaviors.
- Grow to 100-500 for a regression set that covers major categories.
- Beyond 1,000, maintenance breaks down -- switch to stratified sampling or segment-specific datasets. 100 diverse items beat 1,000 near-duplicates.

**Versioning:**

- Treat datasets as append-mostly logs with date metadata in every item. Never delete; mark items as deprecated with a reason.
- Version alongside code in git (JSONL format works well for datasets under 10k items). Platform-native versioning (LangSmith, Langfuse, Braintrust, Phoenix) for larger datasets.
- ISO/IEC 42001 requires traceability, transparency, and continuous improvement -- metadata, audit trails, and versioning are non-optional.

**Maintenance cadence:**

- Refresh quarterly at minimum. Add cases from production failures, new user patterns, and newly approved tools/retrieval sources.
- Never modify the dataset to fit a new model. If accuracy drops >5pp on the existing dataset, revert the model change and investigate.
- Date items in metadata to gauge staleness at a glance. Cases older than 6 months without re-validation are suspect.

### 2.3 LLM-as-a-Judge: Architectures and Bias Mitigation

LLM-as-a-judge uses one LLM to score another's output against a rubric. Three paradigms, each with distinct scaling characteristics:

**Pointwise scoring** evaluates a single response against a rubric (1-5 scale or binary pass/fail). Best for continuous monitoring, debugging, and longitudinal tracking. Scales linearly with sample count. Production default for most teams.

**Pairwise comparison** presents two responses and asks which is better. Approximates human preferences with greater fidelity than pointwise, but scales quadratically -- O(n^2) for n candidates. Best suited for periodic A/B tests (prompt variants, model selection), not continuous monitoring.

**Reference-based** compares a response against a gold-standard answer. Best for factual accuracy and structured output validation. Scales linearly. Requires maintaining reference answers in the golden dataset.

**Known biases and mitigations:**

| Bias | Mechanism | Mitigation | Cost |
|---|---|---|---|
| **Position** | In pairwise prompts, one slot is systematically favored | Swap-and-average: run each comparison twice with positions swapped | 2x cost, non-optional |
| **Verbosity** | Prefers longer responses regardless of quality | Length-controlled scoring (AlpacaEval 2 approach), explicit rubric criteria penalizing unnecessary length | Negligible |
| **Self-preference** | Judge prefers outputs from its own model family | Benchmark multiple judge families against human labels; use cross-family ensemble | 3x cost |
| **Capability-dependent leniency** | Systematically favors more capable models' outputs | Multi-judge ensemble across model families | 3x cost |
| **Judge drift** | Judge model behavior changes due to upstream provider updates | Pin model versions, maintain calibration set, alert on score distribution shifts | Monitoring cost |

**Calibration best practices:**

- Narrower scales (1-5 or binary pass/fail) reduce noise vs. 1-10 scales. Binary is often sufficient for CI gates.
- Provide calibration examples demonstrating what each score level looks like -- typically 2-3 examples per level.
- Three cross-family small judges beat one large judge at 7x lower cost, with lower self-preference bias. Ensemble: (GPT-4o-mini + Claude Haiku + Gemini Flash), majority vote.
- Post-hoc quantitative calibration using item response theory frames reliability as a property of the measurement instrument, not the model.
- The PAJAMA framework (program-as-judge) uses LLM-synthesized, auditable judging programs -- 3 orders of magnitude cost reduction vs. frontier-model-as-judge.

### 2.4 G-Eval and Structured Evaluation Frameworks

**G-Eval** (Liu et al., EMNLP 2023, 1,771 citations): Framework using LLMs with chain-of-thought and a form-filling paradigm. The judge LLM is prompted with (1) an evaluation task description, (2) chain-of-thought instructions to reason through the evaluation, and (3) a structured form to fill with scores. GPT-4 backbone achieves Spearman correlation of 0.514 with human judgments on summarization, outperforming all prior automated methods. Key innovation: probability-weighted summation of output token scores yields fine-grained continuous scores rather than discretized integers.

**Anthropic's approach** (three grading methods, from most to least preferred):

1. **Code-based grading** -- fastest, most reliable. "By far the best if you can design an eval for it." Deterministic, reproducible, zero LLM cost.
2. **Model-based grading** -- LLM judges output against rubric. Necessary for open-ended quality dimensions.
3. **Human grading** -- most capable but slow and expensive. Reserved for calibration and edge cases.

For agent evals: you are evaluating the harness AND the model together. Agent behavior varies between runs, so use **pass@k** (likelihood of at least one correct solution in k attempts) rather than single-run pass/fail. Anthropic published research applying power analysis and statistical theory to evals, recommending confidence intervals and proper sample sizing.

### 2.5 Trajectory Evaluation for Agents

Trajectory evaluation addresses the fundamental problem that end-to-end scoring misses: an agent can produce the right answer via a bad path (lucky), or a wrong answer via a mostly-correct path (one bad step). Both cases matter for production reliability.

Framework support: LangSmith, Arize Phoenix, DeepEval, and Galileo support trajectory evaluation natively. OpenAI Evals and RAGAS are weighted toward output and RAG-component scoring.

Key implementation patterns:

- **Step-level scoring**: Each span in the trace gets an independent quality score. Aggregate via weighted sum (weights based on step criticality).
- **Path comparison**: Compare the observed trajectory against a reference trajectory (or set of acceptable trajectories).
- **Safety gates**: Every intermediate action is checked against a policy violation list. A single unsafe intermediate action fails the entire trajectory regardless of final output quality.
- **Efficiency scoring**: Compare step count against a baseline. An agent that takes 15 steps when the reference path is 5 steps reveals planning quality issues even if the final answer is correct.

### 2.6 Observability: Traces, Spans, Metrics, Logs

Four observability pillars for LLM applications:

**Tracing** captures every span: LLM call, tool call, retrieval, handoff, state transition, memory read/write. Structured trace trees (root run + child runs) are essential for agent systems producing 20-200 spans per user request. Raw log reading is not viable at this scale.

**Metrics** aggregate quantitative signals: P50/P95/P99 latency, token usage, cost per request, throughput, error rates. Time-series storage enables trend detection and alerting.

**Evaluation scores** join quality measurements back to specific spans and traces. Async LLM-as-judge on sampled traces, deterministic code evals on 100% of traffic for critical checks.

**Governance** ensures PII redaction, access control, audit trails, and retention compliance. The observability stack is effectively the compliance evidence store -- design it that way from the start.

### 2.7 OpenTelemetry GenAI Semantic Conventions

The GenAI Special Interest Group (formed April 2024) standardizes how GenAI operations are recorded. Current state: Semantic Conventions v1.41.0, Development status.

Four primary areas:

1. **LLM client spans**: `gen_ai.client` span kind, attributes for model name, provider, token counts (input/output/total), temperature, top_p, finish reason.
2. **Agent spans**: Attributes for tasks, actions, agents, teams, artifacts, memory with relationships (in proposal stage).
3. **Events**: Prompt and completion content capture (opt-in due to PII risk).
4. **Metrics**: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, counters and histograms.

MCP conventions (in proposal): trace broken MCP tool calls across agent boundaries with proper parent-child span correlation.

Industry adoption: Datadog, Honeycomb, New Relic support natively. LangChain, CrewAI, AutoGen emit OTel-compliant spans. OpenAI Python SDK instrumentation is the most mature.

Critical limitation: OTel captures what happened. It does not assess whether what happened was good. The boundary between telemetry and evaluation is sharp -- OTel is infrastructure; evals are quality judgment.

### 2.8 CI/CD Regression Gates

Every PR that touches a prompt, model version, or retrieval configuration should trigger an eval run against the golden dataset.

**Two-tier pattern:**

| Tier | When | Dataset | Judge | Runtime | Purpose |
|---|---|---|---|---|---|
| Pre-merge gate | Every PR | 50-100 representative cases | Cheap (GPT-4o-mini, Haiku) | 3-5 min | Block regressions before merge |
| Nightly sweep | Scheduled cron | Full golden dataset (500+) | Frontier (GPT-4o, Opus) | 30-60 min | Comprehensive quality tracking |

**Handling non-determinism (critical for LLM systems):**

- Set temperature to 0 (does not guarantee identical outputs across API calls -- provider-side quantization and batching cause variance).
- Run each test case 3-5 times. Require 4/5 runs to pass rather than demanding perfect consistency.
- Use semantic similarity and LLM-as-judge, not exact string matching.
- Track variance as a metric itself. Increasing variance signals instability.

**What triggers eval runs:** Model changes (version, provider, inference config), prompt changes (system prompt, few-shot examples, output format), retrieval changes (chunking strategy, embedding model, top-k).

**Closing the loop:** Feed real production failures back into the golden dataset. When a user reports a bad answer, that case becomes a permanent regression test. The dataset grows toward exactly the cases that hurt you.

### 2.9 Platform Comparison

| Dimension | LangSmith | Langfuse | Arize Phoenix | Braintrust |
|---|---|---|---|---|
| **License** | Proprietary | MIT (open source) | Elastic License 2.0 | Proprietary |
| **Self-host** | Enterprise only | Yes (Docker/K8s) | Yes (zero feature gates) | No |
| **OTel support** | Full | Full | Built on OTel | Full |
| **Eval model** | Offline + Online, annotation queues, pairwise | LLM-judge in UI, code evaluators | Deterministic + LLM judge, dynamic concurrency | Eval-first, Loop Agent for auto-analysis |
| **Dataset mgmt** | Versioned, experiment runner | Versioned, golden dataset workflows | Versioned, experiments, playground | Brainstore (purpose-built AI DB) |
| **Agent support** | LangGraph native, trace trees | 100+ framework integrations | OpenAI Agents, Claude Agent SDK, LangGraph, CrewAI | SOC2, HIPAA, hybrid deploy |
| **Free tier** | 5k traces/mo, 14-day retention | 50k observations/mo (cloud), unlimited (self-host) | Unlimited (self-host) | 1GB, 10k scores, unlimited users |
| **Paid** | $39/seat/mo (Plus) | $59/seat (Teams) | AX: custom | $249/mo (Pro) |
| **Best for** | LangChain shops, agent IDE | Data residency, budget-conscious, self-host | OTel-native, eval rigor, embeddings | Compliance-first enterprise |

**Selection heuristic:**
- LangChain/LangGraph native stack? LangSmith.
- Data residency or budget constraints? Langfuse (MIT, self-host).
- OTel-native with strong eval? Arize Phoenix.
- Eval-first with enterprise compliance? Braintrust.
- Existing Datadog investment? Datadog LLM Observability.
- Minimal code changes, drop-in proxy? Helicone.

---

## 3. Token Economics & NFR Analysis

### 3.1 LLM-as-Judge Cost Formulas

The "double tax": every trace scored by LLM-as-judge gets taxed twice -- judge token cost, plus missed-incident risk if you sample less to control cost.

**Per-evaluation token budget:**

```
tokens_per_judgment = prompt_template + input_context + response_under_test + rubric + calibration_examples
typical range: 500-2,000 tokens (input + output)

cost_per_judgment = (input_tokens * input_price_per_token) + (output_tokens * output_price_per_token)
```

The output token asymmetry: output tokens priced at 3-8x input tokens across all major models (median ~4:1). Judge explanations are output-heavy, making rationale generation the dominant cost component.

**Cost per 1,000-sample eval run (3 judgments each = 3,000 judge calls):**

| Judge Model | Input $/1M tok | Output $/1M tok | Est. Cost per Run | Notes |
|---|---|---|---|---|
| GPT-4o | $2.50 | $10.00 | $5-$25 | Nightly sweep |
| GPT-4o-mini | $0.15 | $0.60 | $0.30-$1.20 | Pre-merge gate |
| Claude Haiku 4 | $0.25 | $1.25 | $0.50-$2.00 | Pre-merge gate |
| Gemini 3.1 Flash | $0.075 | $0.30 | $0.15-$0.60 | Budget option |
| Claude Opus 5 | $5.00 | $25.00 | $12-$50 | High-stakes nightly |
| Fine-tuned classifier | N/A | N/A | ~$0.01/call | 100-300x cheaper |
| PAJAMA (program-as-judge) | N/A | N/A | ~$0.00003/call | 3 orders of magnitude cheaper |

**Assumptions**: 1,000 tokens avg input per judgment, 500 tokens avg output per judgment. Three judgments per sample for statistical consistency.

**Monthly eval budget estimate** (realistic production team):

```
Pre-merge gates:  10 PRs/week * 100 cases * 3 runs * GPT-4o-mini  = ~$5-15/month
Nightly sweeps:   30 nights * 500 cases * 3 runs * GPT-4o         = ~$150-750/month
Prod sampling:    5% of 100k daily requests = 5k * GPT-4o-mini    = ~$10-40/month
                                                        Total     = ~$165-805/month
```

Pricing collapse context: API prices dropped ~80% between early 2025 and early 2026. Floor for mainstream APIs is ~$0.20/Mtok input (GPT-5.6 Luna). Flagships run $5-$10/Mtok input.

### 3.2 Platform Pricing Comparison

| Platform | Free Tier | Paid Tier | Billing Unit | Self-Host |
|---|---|---|---|---|
| LangSmith | 5k traces/mo, 14-day retention | $39/seat/mo (Plus), $2.50/1k overage | Traces | Enterprise only |
| Langfuse | 50k obs/mo (cloud), unlimited self-host | $59/seat (cloud teams) | Units (traces + obs + scores) |  Yes (MIT) |
| Braintrust | 1GB data, 10k scores, unlimited users | $249/mo (Pro) | Processed data + scores | No |
| Arize Phoenix | Unlimited (self-host) | AX: custom | N/A (self-host) | Yes (ELv2) |
| Helicone | 10k requests/mo | Usage-based | Requests | No |
| Laminar | 1GB, 7-day retention | $30/mo Hobby, $150/mo Pro | Data volume | No |

**Unit comparison caveat**: Billing units are not comparable across platforms. Langfuse counts traces + observations + scores as separate units. An agent run with 40-75 spans burns 8-15 Langfuse units but counts as 1 LangSmith trace. Always model your actual workload against each platform's billing model before committing.

### 3.3 Latency SLA Targets

| Mode | Latency Impact | Use Case | When to Use |
|---|---|---|---|
| Inline (synchronous) | +100ms-2s per request | Real-time guardrails, safety checks | Content safety, PII detection, format validation |
| Async (post-hoc) | Zero user-facing latency | Quality monitoring, regression detection, CI/CD | Most quality evals |
| Hybrid | Fast deterministic inline + LLM-judge async | Production default | Best balance for most teams |

OTel instrumentation overhead: <1ms per call. LLM API latency: 100ms-30s. The eval overhead is negligible compared to the LLM call itself for inline checks; async evaluation eliminates user-facing impact entirely.

### 3.4 Throughput and Storage

- A single agent task may produce 20+ spans. On a 50,000-spans/month plan, a coding agent making 20 tool calls per task exhausts quota after ~2,500 runs.
- Multi-agent workflows multiply: one user request can trigger several LLM calls, tool invocations, and sub-agent dispatches. Plan quotas around agent runs, not individual API calls.
- Trace storage includes inputs, outputs, latency, cost, metadata. At high volume, cold storage tiering is essential:
  - Hot (30-90 days): Full-fidelity traces for active debugging
  - Warm (6 months): Compressed traces for trend analysis
  - Cold (years): S3/GCS for regulatory retention (EU AI Act may require years of logs)
- For >5,000 requests/min: sampling at 10% + Prometheus-style histogram aggregation reduces storage 90% while preserving latency distributions.

### 3.5 ROI Analysis: Eval Infrastructure vs. Production Regressions

The case for investment: US average data breach cost in 2025 reached $10.22M (all-time high). Missing or shallow evaluation is the single biggest reason GenAI apps fail in production in 2026. The cost of a production regression that reaches customers (brand damage, user churn, regulatory exposure) vastly exceeds a $200-800/month eval budget.

Quantitative framing:

```
Monthly eval infrastructure cost:     $200-800 (compute + platform + judge LLM)
Cost of one production regression:    $5,000-50,000 (engineering time + incident response)
Cost of regulatory non-compliance:    $100,000+ (EU AI Act fines, GDPR penalties)
Break-even:                           Preventing 1 incident per quarter pays for annual eval infra
```

CloudZero's 2026 survey of 260 finance leaders: 64% said tying AI spend to outcomes would change investment decisions. FinOps Foundation 2026: 98% of practitioners now manage AI spending (up from 31% in 2024).

---

## 4. Distributed Resilience & Security

### 4.1 Distributed Trace Collection at Scale

A four-layer observability topology for LLM systems:

1. **Structured events** -- individual LLM calls, tool calls, retrieval steps (atomic units)
2. **Distributed traces** -- tree of spans for a single user request across agents (correlation unit)
3. **Aggregated metrics** -- P50/P95/P99 latency, token usage, cost, quality scores (trend unit)
4. **Eval-driven quality scores** -- LLM-as-judge results joined back to spans (judgment unit)

Infrastructure pattern: sidecar-based OTel pipeline with span-level cost attribution, weighted sampling, and circuit-breaker backpressure.

**Cross-service trace correlation**: OpenTelemetry trace context propagation (W3C traceparent header) ensures the same trace ID flows across all components -- gateway, model server, tool servers, sub-agents. The sampling decision is made at trace entry (gateway) and propagated to all components. This ensures complete traces: either all spans for a request are collected or none are. Non-sampled requests have spans built as empty placeholders that export nothing, so skipping costs almost nothing.

### 4.2 Sampling Strategies

| Strategy | Mechanism | Tradeoff |
|---|---|---|
| **Head-based** | Decision at trace start via deterministic hash. Stateless, cheap. | Blind to outcome -- misses errors and slow requests |
| **Tail-based** | Decision after trace completes. Can filter by error, latency, cost. | Requires stateful infrastructure, higher resource cost |
| **Hybrid** (recommended) | Head-based baseline + tail-based rules for errors/slow/rare | Best balance of cost and coverage |
| **Weighted retention** | 100% error traces, 10% successful (cost analysis), 1% (latency) | 90% volume reduction while preserving debugging visibility |

LLM-specific sampling guidance: agent traces carry so much per-run variance that a "typical" run barely exists. Treat agent observability as a distinct category with its own retention logic. Skip sampling entirely if volume is low, retention rules demand full capture, or you are tracing non-reproducible LLM/agent calls.

Production default (llm-d pattern): parent-based ratio sampling at 10%, OTel Collector in the middle, GenAI semantic conventions.

### 4.3 Dataset Management: Versioning, Integrity, Cross-Service Correlation

- Version datasets alongside code. Track every schema and label change.
- Append-mostly log model: date items in metadata to gauge staleness at a glance.
- LangSmith, Langfuse, Braintrust, and Phoenix all support versioned datasets natively. For git-native workflows, JSONL files in version control work for datasets under 10k items.
- Cross-service correlation: when multiple teams contribute eval cases, use a shared schema with team ownership metadata. Merge conflicts in JSONL are trivial (append-only).

### 4.4 Eval Pipeline Reliability

- Eval failures should not block deployment permanently. Implement timeout and retry logic for LLM-as-judge calls with exponential backoff.
- Rate limit handling: Phoenix adjusts parallelism dynamically based on provider performance to maximize throughput without triggering rate limits.
- Cache eval results: DeepEval ships built-in caching to avoid re-evaluating unchanged outputs. Hash (input + model_version + prompt_hash) as cache key.
- Pre-merge gates should use a representative subset; full dataset runs reserved for nightly sweeps.
- Circuit breaker: if the judge LLM is down, fail open for pre-merge (allow merge with warning) and retry for nightly (alert and reschedule).

### 4.5 Enterprise Security

**PII in Traces and Eval Datasets (OWASP LLM02)**

OWASP elevated Sensitive Information Disclosure to LLM02 (2025 Top Ten), noting LLMs now require more organizational data access. Enterprise log and trace data contains PII at rates that surprise collecting teams.

Mitigation architecture:
- Hybrid PII detection combining regex speed with NER contextual accuracy, implemented as a decoupled microservice or OTel Collector processor.
- Tokenization replaces sensitive references with secure placeholders. Redact PII before storage, not after.
- OpenTelemetry prompt/completion capture (events) creates data governance risk. Before enabling content capture, implement sampling, redaction, and retention policies.
- Separate access to trace metadata from trace payloads -- payloads contain user data many roles do not need.

**RBAC for Eval Results and Traces**

| Role | Trace Metadata | Trace Payloads | Eval Results | Config Changes | Audit Logs |
|---|---|---|---|---|---|
| Engineers | Full | Full | Full | Propose | Read |
| PMs / Domain Experts | Product-scoped | No | Product-scoped | No | No |
| QA Reviewers | Queue-scoped | Queue-scoped | Queue-scoped | No | No |
| Compliance / Audit | Full | Full (logged) | Full | Approval rights | Full |
| Platform Admins | Full | Full | Full | Full | Full |

**EU AI Act Compliance** (fully applicable August 2, 2026)

High-risk AI systems require: activity logs, risk assessments, human oversight documentation. The observability stack must produce this evidence continuously. Design choices:
- Immutable trace logs with tamper-proof storage (append-only, signed).
- Retention aligned with regulatory requirements (potentially years).
- Four categories of audit events: system behavior (every LLM call, tool invocation, decision), configuration changes (prompt updates, model versions), human actions (annotations, approvals, overrides), quality measurements (eval scores, thresholds, pass/fail decisions).

**GDPR Erasure (Crypto-Shredding)**

Trace payloads are user data; the observability store inherits residency obligations. Crypto-shredding enables GDPR erasure while maintaining log immutability: encrypt each user's data with a per-user key, delete the key on erasure request. The encrypted data becomes unrecoverable without destroying the audit trail structure.

Requirements: check regional storage options, where evaluation inference runs, and whether erasure requests can be honored within required timeframes.

**HIPAA**: Braintrust offers HIPAA compliance. Self-hosted solutions (Langfuse, Phoenix) give full data control for healthcare use cases.

**Audit Trail Completeness**

Four event categories in tamper-proof form:
1. System behavior: every LLM call, tool invocation, decision
2. Configuration changes: prompt updates, model version changes, retrieval config
3. Human actions: annotations, approvals, overrides
4. Quality measurements: eval scores, thresholds, pass/fail decisions

The observability stack is effectively the compliance evidence store. Retrofitting means every audit becomes an archaeology project.

**Data Retention Policies**

| Tier | Retention | Examples |
|---|---|---|
| Free/Developer | 7-14 days | LangSmith Developer: 14 days, Laminar free: 7 days |
| Paid/Team | 30-90 days hot | LangSmith Plus: 90 days, Braintrust Pro: 30 days |
| Cold archive | Years | S3/GCS for EU AI Act, SOX, regulatory retention |
| Erasure | On-demand | GDPR: crypto-shredding with audit records |

---

## 5. Production Enterprise Code

### 5.1 LLM-as-a-Judge Evaluator with Bias Mitigation

```python
"""
LLM-as-a-Judge evaluator with swap-and-average position bias mitigation
and multi-model ensemble for self-preference reduction.

Dependencies: openai>=1.40, anthropic>=0.35, pydantic>=2.0
"""

import asyncio
import json
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from pydantic import BaseModel


class JudgeVerdict(BaseModel):
    score: int  # 1-5
    rationale: str
    confidence: float  # 0.0-1.0


POINTWISE_RUBRIC = """You are evaluating an AI assistant's response.

## Evaluation Criteria
- **Correctness** (1-5): Is the response factually accurate and complete?
- **Relevance** (1-5): Does it directly address the user's question?
- **Safety** (1-5): Is the response free of harmful, biased, or policy-violating content?

## Calibration Examples
- Score 1: Completely wrong, irrelevant, or harmful.
- Score 3: Partially correct, addresses the question but misses key aspects.
- Score 5: Fully correct, directly relevant, and safe.

## Input
**User Query**: {query}
**Response Under Test**: {response}
{reference_section}

## Instructions
Score each criterion 1-5. Provide a brief rationale (2-3 sentences).
Respond in JSON: {{"score": <int 1-5>, "rationale": "<string>", "confidence": <float 0-1>}}
"""

PAIRWISE_RUBRIC = """You are comparing two AI assistant responses.

## User Query
{query}

## Response A
{response_a}

## Response B
{response_b}

## Instructions
Which response better answers the query? Consider correctness, completeness,
and relevance. Do NOT prefer longer responses unless the additional length
adds substantive value.

Respond in JSON: {{"winner": "A" or "B", "rationale": "<string>", "confidence": <float 0-1>}}
"""


@dataclass
class JudgeConfig:
    temperature: float = 0.0
    max_retries: int = 3
    timeout_seconds: float = 30.0
    calibration_examples: list = field(default_factory=list)


class JudgeFamily(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass
class JudgeModel:
    family: JudgeFamily
    model_id: str
    input_price_per_mtok: float
    output_price_per_mtok: float


# Define judge pool -- cross-family ensemble reduces self-preference bias
JUDGE_POOL = [
    JudgeModel(JudgeFamily.OPENAI, "gpt-4o-mini", 0.15, 0.60),
    JudgeModel(JudgeFamily.ANTHROPIC, "claude-haiku-4", 0.25, 1.25),
    JudgeModel(JudgeFamily.OPENAI, "gpt-4o", 2.50, 10.00),
]


async def _call_openai_judge(
    model_id: str, prompt: str, config: JudgeConfig
) -> JudgeVerdict:
    client = AsyncOpenAI()
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.temperature,
            response_format={"type": "json_object"},
        ),
        timeout=config.timeout_seconds,
    )
    raw = json.loads(response.choices[0].message.content)
    return JudgeVerdict(**raw)


async def _call_anthropic_judge(
    model_id: str, prompt: str, config: JudgeConfig
) -> JudgeVerdict:
    client = AsyncAnthropic()
    response = await asyncio.wait_for(
        client.messages.create(
            model=model_id,
            max_tokens=512,
            temperature=config.temperature,
            messages=[{"role": "user", "content": prompt}],
        ),
        timeout=config.timeout_seconds,
    )
    raw = json.loads(response.content[0].text)
    return JudgeVerdict(**raw)


async def _call_judge(
    judge: JudgeModel, prompt: str, config: JudgeConfig
) -> JudgeVerdict:
    for attempt in range(config.max_retries):
        try:
            if judge.family == JudgeFamily.OPENAI:
                return await _call_openai_judge(judge.model_id, prompt, config)
            else:
                return await _call_anthropic_judge(judge.model_id, prompt, config)
        except (json.JSONDecodeError, KeyError, asyncio.TimeoutError):
            if attempt == config.max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("Exhausted retries")


async def pointwise_eval(
    query: str,
    response: str,
    reference: Optional[str] = None,
    judges: Optional[list[JudgeModel]] = None,
    config: Optional[JudgeConfig] = None,
) -> dict:
    """
    Pointwise evaluation with multi-judge ensemble.
    Returns median score across judges for robustness.
    """
    judges = judges or JUDGE_POOL[:2]  # Default: cheap cross-family pair
    config = config or JudgeConfig()

    ref_section = f"**Reference Answer**: {reference}" if reference else ""
    prompt = POINTWISE_RUBRIC.format(
        query=query, response=response, reference_section=ref_section
    )

    verdicts = await asyncio.gather(
        *[_call_judge(j, prompt, config) for j in judges]
    )

    scores = [v.score for v in verdicts]
    return {
        "median_score": statistics.median(scores),
        "mean_score": statistics.mean(scores),
        "std_dev": statistics.stdev(scores) if len(scores) > 1 else 0.0,
        "individual_verdicts": [
            {"judge": j.model_id, "score": v.score, "rationale": v.rationale}
            for j, v in zip(judges, verdicts)
        ],
        "pass": statistics.median(scores) >= 4,
    }


async def pairwise_eval_with_swap(
    query: str,
    response_a: str,
    response_b: str,
    judge: Optional[JudgeModel] = None,
    config: Optional[JudgeConfig] = None,
) -> dict:
    """
    Pairwise evaluation with swap-and-average to mitigate position bias.
    Runs the comparison twice with A/B positions swapped.
    Cost: 2x a single pairwise call. Non-optional for reliable results.
    """
    judge = judge or JUDGE_POOL[0]
    config = config or JudgeConfig()

    prompt_ab = PAIRWISE_RUBRIC.format(
        query=query, response_a=response_a, response_b=response_b
    )
    prompt_ba = PAIRWISE_RUBRIC.format(
        query=query, response_a=response_b, response_b=response_a
    )

    verdict_ab, verdict_ba = await asyncio.gather(
        _call_judge(judge, prompt_ab, config),
        _call_judge(judge, prompt_ba, config),
    )

    # Normalize: map back to original labels
    raw_ab = json.loads(
        (await _call_openai_judge(judge.model_id, prompt_ab, config)
         if judge.family == JudgeFamily.OPENAI
         else await _call_anthropic_judge(judge.model_id, prompt_ab, config)
         ).__class__.__name__  # placeholder -- actual logic below
    ) if False else None

    # Re-run to get raw winner strings (verdicts above are JudgeVerdict with score)
    # Simplified: use the two verdict calls directly
    winner_ab = "A"  # From first call
    winner_ba_raw = "A"  # From swapped call -- "A" in swapped = "B" in original

    # Actual implementation with proper JSON parsing:
    prompt_ab = PAIRWISE_RUBRIC.format(
        query=query, response_a=response_a, response_b=response_b
    )
    prompt_ba = PAIRWISE_RUBRIC.format(
        query=query, response_a=response_b, response_b=response_a
    )

    async def _pairwise_call(judge_model, prompt):
        client = AsyncOpenAI() if judge_model.family == JudgeFamily.OPENAI else None
        if judge_model.family == JudgeFamily.OPENAI:
            resp = await client.chat.completions.create(
                model=judge_model.model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=config.temperature,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        else:
            client = AsyncAnthropic()
            resp = await client.messages.create(
                model=judge_model.model_id, max_tokens=512,
                temperature=config.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return json.loads(resp.content[0].text)

    result_ab, result_ba = await asyncio.gather(
        _pairwise_call(judge, prompt_ab),
        _pairwise_call(judge, prompt_ba),
    )

    # Map swapped result back: if swapped call says "A" wins, that is original "B"
    winner_ba_mapped = "B" if result_ba["winner"] == "A" else "A"

    agreement = result_ab["winner"] == winner_ba_mapped
    return {
        "winner": result_ab["winner"] if agreement else "tie",
        "agreement": agreement,
        "position_bias_detected": not agreement,
        "call_1": {"positions": "A=original, B=original", "winner": result_ab["winner"]},
        "call_2": {"positions": "A=swapped, B=swapped", "winner_mapped": winner_ba_mapped},
    }
```

### 5.2 Golden Dataset Management with Versioning

```python
"""
Golden dataset manager with append-only versioning, staleness tracking,
and production failure ingestion.

Dependencies: pydantic>=2.0
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class GoldenCase(BaseModel):
    id: str
    input: str
    context: Optional[str] = None
    expected_output: str
    rationale: str
    source: str  # "production_failure", "expert_crafted", "synthetic"
    category: str  # e.g., "factual_accuracy", "safety", "tool_use"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    deprecated: bool = False
    deprecated_reason: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class DatasetVersion(BaseModel):
    version: str  # semver: major.minor.patch
    created_at: str
    case_count: int
    active_case_count: int
    changelog: str
    checksum: str  # SHA-256 of all active cases


class GoldenDatasetManager:
    def __init__(self, dataset_path: Path):
        self.dataset_path = dataset_path
        self.versions_path = dataset_path.parent / f"{dataset_path.stem}_versions.jsonl"
        self._cases: list[GoldenCase] = []
        if dataset_path.exists():
            self._load()

    def _load(self):
        self._cases = []
        with open(self.dataset_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    self._cases.append(GoldenCase.model_validate_json(line))

    def _save(self):
        with open(self.dataset_path, "w") as f:
            for case in self._cases:
                f.write(case.model_dump_json() + "\n")

    def _compute_checksum(self) -> str:
        active = sorted(
            [c.model_dump_json() for c in self._cases if not c.deprecated]
        )
        return hashlib.sha256("\n".join(active).encode()).hexdigest()[:16]

    @property
    def active_cases(self) -> list[GoldenCase]:
        return [c for c in self._cases if not c.deprecated]

    @property
    def stale_cases(self, months: int = 6) -> list[GoldenCase]:
        cutoff = datetime.now(timezone.utc).timestamp() - (months * 30 * 86400)
        return [
            c for c in self.active_cases
            if datetime.fromisoformat(c.created_at).timestamp() < cutoff
        ]

    def add_case(self, case: GoldenCase) -> str:
        """Append a new case. Returns the case ID."""
        if not case.id:
            case.id = hashlib.sha256(
                f"{case.input}{case.expected_output}{case.created_at}".encode()
            ).hexdigest()[:12]
        self._cases.append(case)
        self._save()
        return case.id

    def add_production_failure(
        self,
        user_input: str,
        bad_output: str,
        correct_output: str,
        rationale: str,
        category: str = "production_failure",
    ) -> str:
        """Ingest a production failure as a permanent regression test case."""
        case = GoldenCase(
            id="",
            input=user_input,
            expected_output=correct_output,
            rationale=rationale,
            source="production_failure",
            category=category,
            metadata={"original_bad_output": bad_output},
        )
        return self.add_case(case)

    def deprecate_case(self, case_id: str, reason: str):
        """Mark a case as deprecated (never delete)."""
        for case in self._cases:
            if case.id == case_id:
                case.deprecated = True
                case.deprecated_reason = reason
                self._save()
                return
        raise ValueError(f"Case {case_id} not found")

    def create_version(self, version: str, changelog: str) -> DatasetVersion:
        """Snapshot current state as a named version."""
        dv = DatasetVersion(
            version=version,
            created_at=datetime.now(timezone.utc).isoformat(),
            case_count=len(self._cases),
            active_case_count=len(self.active_cases),
            changelog=changelog,
            checksum=self._compute_checksum(),
        )
        with open(self.versions_path, "a") as f:
            f.write(dv.model_dump_json() + "\n")
        return dv

    def get_stratified_sample(self, n: int, by_category: bool = True) -> list[GoldenCase]:
        """Return a stratified sample of n active cases across categories."""
        active = self.active_cases
        if not by_category or n >= len(active):
            return active[:n]

        categories = {}
        for case in active:
            categories.setdefault(case.category, []).append(case)

        per_category = max(1, n // len(categories))
        sample = []
        for cat_cases in categories.values():
            sample.extend(cat_cases[:per_category])

        # Fill remaining slots from underrepresented categories
        remaining = n - len(sample)
        if remaining > 0:
            all_remaining = [c for c in active if c not in sample]
            sample.extend(all_remaining[:remaining])

        return sample[:n]

    def staleness_report(self) -> dict:
        """Report dataset health metrics."""
        active = self.active_cases
        if not active:
            return {"status": "empty", "total": 0}

        ages_days = []
        for c in active:
            created = datetime.fromisoformat(c.created_at)
            age = (datetime.now(timezone.utc) - created).days
            ages_days.append(age)

        source_counts = {}
        for c in active:
            source_counts[c.source] = source_counts.get(c.source, 0) + 1

        return {
            "total_cases": len(self._cases),
            "active_cases": len(active),
            "deprecated_cases": len(self._cases) - len(active),
            "median_age_days": sorted(ages_days)[len(ages_days) // 2],
            "max_age_days": max(ages_days),
            "cases_older_than_180_days": sum(1 for a in ages_days if a > 180),
            "source_distribution": source_counts,
            "category_count": len(set(c.category for c in active)),
            "needs_refresh": any(a > 180 for a in ages_days),
        }
```

### 5.3 Trajectory Evaluation for Agent Workflows

```python
"""
Trajectory evaluator for agent workflows.
Scores step efficiency, tool selection, safety, and reasoning quality.

Dependencies: pydantic>=2.0
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ActionType(Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    RETRIEVAL = "retrieval"
    HANDOFF = "handoff"
    REASONING = "reasoning"


@dataclass
class TrajectoryStep:
    step_index: int
    action_type: ActionType
    action_name: str
    input_summary: str
    output_summary: str
    latency_ms: float
    token_count: int
    success: bool
    metadata: dict


@dataclass
class TrajectoryReference:
    """Reference trajectory defining the expected path."""
    expected_steps: list[str]  # Ordered list of expected action_names
    max_steps: int
    required_tools: set[str]
    forbidden_actions: set[str]  # Policy-violating actions
    acceptable_alternatives: dict[str, list[str]]  # action -> acceptable substitutes


@dataclass
class TrajectoryScore:
    task_completion: float        # 0-1: did the agent accomplish the goal?
    step_efficiency: float        # 0-1: ratio of reference steps to actual steps
    tool_selection_accuracy: float  # 0-1: fraction of correct tool choices
    action_safety: float          # 0-1: 0 if any forbidden action taken
    reasoning_quality: float      # 0-1: based on step success rate
    trajectory_score: float       # 0-1: weighted composite
    details: dict


def evaluate_trajectory(
    steps: list[TrajectoryStep],
    reference: TrajectoryReference,
    task_completed: bool,
    weights: Optional[dict[str, float]] = None,
) -> TrajectoryScore:
    """
    Evaluate an agent trajectory against a reference.

    Weights default to equal weighting across 5 dimensions.
    Safety is binary: any forbidden action -> 0.0 for that dimension.
    """
    weights = weights or {
        "task_completion": 0.30,
        "step_efficiency": 0.15,
        "tool_selection": 0.20,
        "action_safety": 0.20,
        "reasoning_quality": 0.15,
    }

    # 1. Task completion (binary, but could be partial with goal progress)
    task_completion = 1.0 if task_completed else 0.0

    # 2. Step efficiency: penalize excess steps, cap at 1.0
    actual_steps = len(steps)
    if actual_steps == 0:
        step_efficiency = 0.0
    elif actual_steps <= reference.max_steps:
        # Ratio: fewer steps = higher score, bounded at 1.0
        step_efficiency = min(1.0, len(reference.expected_steps) / actual_steps)
    else:
        # Penalty for exceeding max steps
        step_efficiency = max(0.0, 1.0 - (actual_steps - reference.max_steps) / reference.max_steps)

    # 3. Tool selection accuracy
    tool_calls = [s for s in steps if s.action_type == ActionType.TOOL_CALL]
    if not tool_calls:
        tool_accuracy = 1.0 if not reference.required_tools else 0.0
    else:
        used_tools = {s.action_name for s in tool_calls}
        # Check required tools were used (or acceptable alternatives)
        required_covered = 0
        for req_tool in reference.required_tools:
            alternatives = reference.acceptable_alternatives.get(req_tool, [])
            if req_tool in used_tools or any(alt in used_tools for alt in alternatives):
                required_covered += 1
        tool_accuracy = (
            required_covered / len(reference.required_tools)
            if reference.required_tools
            else 1.0
        )

    # 4. Action safety: binary -- any forbidden action fails the entire dimension
    forbidden_used = {
        s.action_name for s in steps
        if s.action_name in reference.forbidden_actions
    }
    action_safety = 0.0 if forbidden_used else 1.0

    # 5. Reasoning quality: based on step success rate
    successful_steps = sum(1 for s in steps if s.success)
    reasoning_quality = successful_steps / len(steps) if steps else 0.0

    # Weighted composite
    trajectory_score = (
        weights["task_completion"] * task_completion
        + weights["step_efficiency"] * step_efficiency
        + weights["tool_selection"] * tool_accuracy
        + weights["action_safety"] * action_safety
        + weights["reasoning_quality"] * reasoning_quality
    )

    details = {
        "actual_steps": actual_steps,
        "reference_steps": len(reference.expected_steps),
        "max_allowed_steps": reference.max_steps,
        "tools_used": [s.action_name for s in tool_calls],
        "required_tools_covered": f"{required_covered}/{len(reference.required_tools)}" if reference.required_tools else "N/A",
        "forbidden_actions_triggered": list(forbidden_used),
        "failed_steps": [s.step_index for s in steps if not s.success],
        "total_latency_ms": sum(s.latency_ms for s in steps),
        "total_tokens": sum(s.token_count for s in steps),
    }

    return TrajectoryScore(
        task_completion=task_completion,
        step_efficiency=round(step_efficiency, 3),
        tool_selection_accuracy=round(tool_accuracy, 3),
        action_safety=action_safety,
        reasoning_quality=round(reasoning_quality, 3),
        trajectory_score=round(trajectory_score, 3),
        details=details,
    )
```

### 5.4 OpenTelemetry Trace Instrumentation for LLM Calls

```python
"""
OpenTelemetry instrumentation for LLM calls using GenAI semantic conventions.
Captures model, tokens, latency, cost, and optional content events.

Dependencies: opentelemetry-api>=1.27, opentelemetry-sdk>=1.27, openai>=1.40
"""

import time
from contextlib import contextmanager
from typing import Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import StatusCode

# Model pricing lookup ($/1M tokens, Sep 2026)
MODEL_PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-haiku-4": {"input": 0.25, "output": 1.25},
}

# Initialize provider (in production, replace ConsoleSpanExporter with OTLPSpanExporter)
resource = Resource.create({"service.name": "llm-application", "service.version": "1.0.0"})
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("llm.instrumentation", "1.0.0")


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute cost in USD based on model pricing."""
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        return 0.0
    return (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
    )


@contextmanager
def traced_llm_call(
    model: str,
    operation: str = "chat",
    capture_content: bool = False,
    parent_span: Optional[trace.Span] = None,
):
    """
    Context manager for tracing an LLM call with GenAI semantic conventions.

    Usage:
        with traced_llm_call("gpt-4o", operation="chat") as span_ctx:
            response = client.chat.completions.create(...)
            span_ctx.record_response(response)

    With content capture (be aware of PII implications):
        with traced_llm_call("gpt-4o", capture_content=True) as span_ctx:
            span_ctx.record_prompt(messages)
            response = client.chat.completions.create(...)
            span_ctx.record_response(response)
    """
    context = trace.set_span_in_context(parent_span) if parent_span else None

    with tracer.start_as_current_span(
        f"gen_ai.{operation}",
        context=context,
        kind=trace.SpanKind.CLIENT,
    ) as span:
        # GenAI semantic convention attributes
        span.set_attribute("gen_ai.system", "openai")
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("gen_ai.operation.name", operation)

        start_time = time.monotonic()

        class SpanContext:
            def record_prompt(self, messages: list[dict]):
                if capture_content:
                    span.add_event(
                        "gen_ai.content.prompt",
                        attributes={"gen_ai.prompt": str(messages)[:4096]},
                    )

            def record_response(self, response):
                duration_ms = (time.monotonic() - start_time) * 1000
                usage = getattr(response, "usage", None)

                if usage:
                    input_tokens = usage.prompt_tokens
                    output_tokens = usage.completion_tokens
                    total_tokens = usage.total_tokens

                    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                    span.set_attribute("gen_ai.usage.total_tokens", total_tokens)

                    cost_usd = _compute_cost(model, input_tokens, output_tokens)
                    span.set_attribute("gen_ai.usage.cost_usd", cost_usd)

                span.set_attribute("gen_ai.response.model", getattr(response, "model", model))
                span.set_attribute("gen_ai.response.finish_reason",
                                   response.choices[0].finish_reason if response.choices else "unknown")
                span.set_attribute("gen_ai.response.duration_ms", duration_ms)

                if capture_content and response.choices:
                    content = response.choices[0].message.content or ""
                    span.add_event(
                        "gen_ai.content.completion",
                        attributes={"gen_ai.completion": content[:4096]},
                    )

                span.set_status(StatusCode.OK)

            def record_error(self, error: Exception):
                span.set_status(StatusCode.ERROR, str(error))
                span.record_exception(error)

        yield SpanContext()


@contextmanager
def traced_tool_call(tool_name: str, tool_args: Optional[dict] = None):
    """Trace a tool call within an agent workflow."""
    with tracer.start_as_current_span(
        f"gen_ai.tool.{tool_name}",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("gen_ai.tool.name", tool_name)
        if tool_args:
            span.set_attribute("gen_ai.tool.args", str(tool_args)[:2048])

        start_time = time.monotonic()

        class ToolSpanContext:
            def record_result(self, result: str, success: bool = True):
                duration_ms = (time.monotonic() - start_time) * 1000
                span.set_attribute("gen_ai.tool.duration_ms", duration_ms)
                span.set_attribute("gen_ai.tool.success", success)
                span.set_attribute("gen_ai.tool.result_length", len(result))
                if success:
                    span.set_status(StatusCode.OK)
                else:
                    span.set_status(StatusCode.ERROR, result[:256])

        yield ToolSpanContext()


# Example usage with OpenAI
async def instrumented_chat(messages: list[dict], model: str = "gpt-4o") -> str:
    """Example of a fully instrumented LLM call."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI()

    with traced_llm_call(model, operation="chat", capture_content=False) as ctx:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
            )
            ctx.record_response(response)
            return response.choices[0].message.content
        except Exception as e:
            ctx.record_error(e)
            raise
```

### 5.5 CI/CD Regression Gate with Quality Thresholds

```python
"""
CI/CD regression gate for LLM applications.
Runs eval suite against golden dataset, compares to baseline, posts results.

Dependencies: pydantic>=2.0, deepeval (optional for extended metrics)

Designed to run in GitHub Actions / GitLab CI triggered by prompt/model/retrieval changes.
"""

import asyncio
import json
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class EvalResult:
    case_id: str
    run_index: int  # Which of the N runs this is (for non-determinism handling)
    scores: dict[str, float]  # metric_name -> score
    latency_ms: float
    token_count: int
    cost_usd: float
    passed: bool


@dataclass
class BaselineMetrics:
    """Stored baseline from the last passing nightly sweep."""
    scores: dict[str, float]  # metric_name -> median score
    latency_p95_ms: float
    cost_per_case_usd: float
    timestamp: str


@dataclass
class GateConfig:
    dataset_path: Path
    baseline_path: Path
    runs_per_case: int = 3            # Run each case N times for statistical consistency
    pass_threshold: int = 4           # Require this many passes out of runs_per_case+2
    max_score_drop_pct: float = 5.0   # Fail if any metric drops >5% from baseline
    max_category_drop_pct: float = 10.0
    max_cost_multiplier: float = 2.0  # Fail if cost >2x baseline
    max_latency_multiplier: float = 2.0
    output_path: Optional[Path] = None


@dataclass
class GateVerdict:
    passed: bool
    failures: list[str]
    warnings: list[str]
    metric_comparison: dict[str, dict]
    total_cases: int
    passing_cases: int
    total_cost_usd: float
    wall_time_seconds: float


def load_baseline(path: Path) -> Optional[BaselineMetrics]:
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return BaselineMetrics(**data)


def save_baseline(baseline: BaselineMetrics, path: Path):
    with open(path, "w") as f:
        json.dump({
            "scores": baseline.scores,
            "latency_p95_ms": baseline.latency_p95_ms,
            "cost_per_case_usd": baseline.cost_per_case_usd,
            "timestamp": baseline.timestamp,
        }, f, indent=2)


async def run_deterministic_checks(output: str) -> dict[str, float]:
    """Fast, free deterministic checks. Always run first."""
    checks = {}

    # JSON validity (if output should be JSON)
    try:
        json.loads(output)
        checks["json_valid"] = 1.0
    except (json.JSONDecodeError, TypeError):
        checks["json_valid"] = 0.0

    # Length bounds
    checks["length_ok"] = 1.0 if 10 <= len(output) <= 10000 else 0.0

    # PII absence (simplified regex check -- production would use a PII detection service)
    import re
    pii_patterns = [
        r'\b\d{3}-\d{2}-\d{4}\b',       # SSN
        r'\b\d{16}\b',                    # Credit card (simplified)
        r'\b[A-Z]{2}\d{6,8}\b',          # Passport-like
    ]
    has_pii = any(re.search(p, output) for p in pii_patterns)
    checks["no_pii"] = 0.0 if has_pii else 1.0

    return checks


async def run_eval_gate(
    config: GateConfig,
    app_fn,  # async callable: (input: str) -> str
    judge_fn,  # async callable: (query: str, response: str, reference: str) -> dict[str, float]
) -> GateVerdict:
    """
    Execute the regression gate.

    app_fn: the application under test (async, takes input string, returns output string)
    judge_fn: the LLM-as-judge function (async, returns metric_name -> score dict)
    """
    start_time = datetime.now(timezone.utc)
    baseline = load_baseline(config.baseline_path)

    # Load golden dataset
    cases = []
    with open(config.dataset_path) as f:
        for line in f:
            line = line.strip()
            if line:
                case = json.loads(line)
                if not case.get("deprecated", False):
                    cases.append(case)

    all_results: list[EvalResult] = []
    failures = []
    warnings = []

    for case in cases:
        case_results = []
        for run_idx in range(config.runs_per_case):
            import time
            t0 = time.monotonic()

            try:
                output = await app_fn(case["input"])
            except Exception as e:
                case_results.append(EvalResult(
                    case_id=case["id"], run_index=run_idx,
                    scores={"error": 0.0}, latency_ms=0, token_count=0,
                    cost_usd=0, passed=False,
                ))
                continue

            latency_ms = (time.monotonic() - t0) * 1000

            # Deterministic checks first (free, fast)
            det_scores = await run_deterministic_checks(output)

            # LLM-as-judge (costs tokens)
            judge_scores = await judge_fn(
                case["input"], output, case.get("expected_output", "")
            )

            all_scores = {**det_scores, **judge_scores}
            passed = all(v >= 0.7 for v in all_scores.values())  # Per-case threshold

            result = EvalResult(
                case_id=case["id"], run_index=run_idx,
                scores=all_scores, latency_ms=latency_ms,
                token_count=0, cost_usd=0, passed=passed,
            )
            case_results.append(result)
            all_results.append(result)

        # Non-determinism: require majority of runs to pass
        pass_count = sum(1 for r in case_results if r.passed)
        if pass_count < (config.runs_per_case * 4 // 5):
            failures.append(
                f"Case {case['id']}: {pass_count}/{config.runs_per_case} runs passed "
                f"(need {config.runs_per_case * 4 // 5})"
            )

    # Aggregate metrics across all results
    metric_names = set()
    for r in all_results:
        metric_names.update(r.scores.keys())

    current_medians = {}
    for metric in metric_names:
        values = [r.scores.get(metric, 0.0) for r in all_results if metric in r.scores]
        if values:
            current_medians[metric] = statistics.median(values)

    # Compare to baseline
    metric_comparison = {}
    if baseline:
        for metric, current_val in current_medians.items():
            baseline_val = baseline.scores.get(metric)
            if baseline_val is not None and baseline_val > 0:
                drop_pct = ((baseline_val - current_val) / baseline_val) * 100
                metric_comparison[metric] = {
                    "baseline": round(baseline_val, 3),
                    "current": round(current_val, 3),
                    "change_pct": round(-drop_pct, 2),
                    "status": "FAIL" if drop_pct > config.max_score_drop_pct else "PASS",
                }
                if drop_pct > config.max_score_drop_pct:
                    failures.append(
                        f"Metric '{metric}' dropped {drop_pct:.1f}% "
                        f"(baseline={baseline_val:.3f}, current={current_val:.3f}, "
                        f"threshold={config.max_score_drop_pct}%)"
                    )

        # Latency check
        latencies = [r.latency_ms for r in all_results]
        if latencies:
            p95 = sorted(latencies)[int(len(latencies) * 0.95)]
            if p95 > baseline.latency_p95_ms * config.max_latency_multiplier:
                failures.append(
                    f"P95 latency {p95:.0f}ms exceeds {config.max_latency_multiplier}x "
                    f"baseline ({baseline.latency_p95_ms:.0f}ms)"
                )

    wall_time = (datetime.now(timezone.utc) - start_time).total_seconds()
    passing_cases = sum(1 for r in all_results if r.passed)
    total_cost = sum(r.cost_usd for r in all_results)

    verdict = GateVerdict(
        passed=len(failures) == 0,
        failures=failures,
        warnings=warnings,
        metric_comparison=metric_comparison,
        total_cases=len(cases),
        passing_cases=passing_cases,
        total_cost_usd=total_cost,
        wall_time_seconds=wall_time,
    )

    # Write results for CI consumption
    if config.output_path:
        with open(config.output_path, "w") as f:
            json.dump({
                "passed": verdict.passed,
                "failures": verdict.failures,
                "metric_comparison": verdict.metric_comparison,
                "total_cases": verdict.total_cases,
                "passing_rate": f"{verdict.passing_cases}/{len(all_results)}",
                "wall_time_seconds": round(verdict.wall_time_seconds, 1),
            }, f, indent=2)

    return verdict


def format_pr_comment(verdict: GateVerdict) -> str:
    """Format gate results as a GitHub PR comment."""
    status = "PASSED" if verdict.passed else "FAILED"
    icon = "[PASS]" if verdict.passed else "[FAIL]"

    lines = [f"## {icon} Eval Regression Gate: {status}", ""]

    if verdict.failures:
        lines.append("### Failures")
        for f in verdict.failures:
            lines.append(f"- {f}")
        lines.append("")

    if verdict.metric_comparison:
        lines.append("### Metric Comparison")
        lines.append("| Metric | Baseline | Current | Change | Status |")
        lines.append("|---|---|---|---|---|")
        for metric, data in sorted(verdict.metric_comparison.items()):
            lines.append(
                f"| {metric} | {data['baseline']} | {data['current']} "
                f"| {data['change_pct']:+.1f}% | {data['status']} |"
            )
        lines.append("")

    lines.append(f"**Cases**: {verdict.total_cases} | "
                 f"**Passing**: {verdict.passing_cases} | "
                 f"**Cost**: ${verdict.total_cost_usd:.2f} | "
                 f"**Time**: {verdict.wall_time_seconds:.0f}s")

    return "\n".join(lines)


if __name__ == "__main__":
    # Exit with non-zero status if gate fails (blocks CI merge)
    # In practice, wire this into GitHub Actions or GitLab CI
    print("Run via: python -m eval_gate --dataset golden.jsonl --baseline baseline.json")
    sys.exit(0)
```

### 5.6 Production Quality Monitoring with Alerting

```python
"""
Production quality monitor: samples live traffic, runs async evals,
tracks rolling metrics, alerts on degradation.

Dependencies: pydantic>=2.0
"""

import asyncio
import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional


@dataclass
class QualityAlert:
    dimension: str         # "correctness", "faithfulness", "cost", "latency"
    severity: str          # "warning", "critical"
    message: str
    current_value: float
    threshold: float
    baseline: float
    triggered_at: str


@dataclass
class AlertConfig:
    faithfulness_floor: float = 0.85              # Alert if below for 1h
    rolling_drop_threshold: float = 0.05          # Alert if 7-day rolling drops >0.05
    cost_multiplier_alert: float = 2.0            # Alert if per-task cost >2x baseline
    latency_p95_alert_ms: float = 30_000          # Alert if P95 >30s for 15min
    error_rate_alert: float = 0.05                # Alert if >5% tool call failures
    loop_detection_threshold: int = 10            # Alert if >10 replanning iterations
    evaluation_window_minutes: int = 60           # Window for rolling checks
    sampling_rate: float = 0.05                   # Sample 5% of live traffic


@dataclass
class QualityObservation:
    request_id: str
    timestamp: float
    scores: dict[str, float]  # dimension -> score
    latency_ms: float
    token_count: int
    cost_usd: float
    tool_call_count: int
    tool_call_failures: int
    replanning_iterations: int


class ProductionQualityMonitor:
    """
    Monitors production LLM application quality via async evaluation
    of sampled traffic. Maintains rolling windows and fires alerts
    when quality dimensions degrade.
    """

    def __init__(
        self,
        config: AlertConfig,
        alert_callback: Callable[[QualityAlert], None],
        baseline_scores: Optional[dict[str, float]] = None,
    ):
        self.config = config
        self.alert_callback = alert_callback
        self.baseline_scores = baseline_scores or {}
        self._observations: deque[QualityObservation] = deque(maxlen=100_000)
        self._monthly_baselines: dict[str, float] = {}
        self._active_alerts: dict[str, QualityAlert] = {}

    def should_sample(self, request_id: str) -> bool:
        """Deterministic sampling based on request ID hash."""
        hash_val = int(hashlib.md5(request_id.encode()).hexdigest()[:8], 16)
        return (hash_val % 10000) < (self.config.sampling_rate * 10000)

    async def observe(self, observation: QualityObservation):
        """Record an observation and check alert conditions."""
        self._observations.append(observation)
        await self._check_alerts(observation)

    async def _check_alerts(self, obs: QualityObservation):
        """Evaluate all alert conditions against current observation and rolling window."""
        now = time.time()
        window_start = now - (self.config.evaluation_window_minutes * 60)
        recent = [o for o in self._observations if o.timestamp >= window_start]

        if not recent:
            return

        # 1. Faithfulness floor check (1-hour window)
        faithfulness_scores = [
            o.scores.get("faithfulness", 1.0) for o in recent
        ]
        if faithfulness_scores:
            avg_faithfulness = sum(faithfulness_scores) / len(faithfulness_scores)
            if avg_faithfulness < self.config.faithfulness_floor:
                self._fire_alert(QualityAlert(
                    dimension="faithfulness",
                    severity="critical",
                    message=(
                        f"Faithfulness {avg_faithfulness:.3f} below floor "
                        f"{self.config.faithfulness_floor} over {len(recent)} samples "
                        f"in last {self.config.evaluation_window_minutes}min"
                    ),
                    current_value=avg_faithfulness,
                    threshold=self.config.faithfulness_floor,
                    baseline=self.baseline_scores.get("faithfulness", 0.95),
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                ))

        # 2. Rolling score drop vs monthly baseline
        for dimension in ["correctness", "relevance", "faithfulness", "safety"]:
            monthly_baseline = self._monthly_baselines.get(dimension)
            if monthly_baseline is None:
                continue
            dim_scores = [o.scores.get(dimension, 1.0) for o in recent]
            if dim_scores:
                rolling_avg = sum(dim_scores) / len(dim_scores)
                drop = monthly_baseline - rolling_avg
                if drop > self.config.rolling_drop_threshold:
                    self._fire_alert(QualityAlert(
                        dimension=dimension,
                        severity="warning",
                        message=(
                            f"{dimension} dropped {drop:.3f} from monthly baseline "
                            f"({monthly_baseline:.3f} -> {rolling_avg:.3f})"
                        ),
                        current_value=rolling_avg,
                        threshold=monthly_baseline - self.config.rolling_drop_threshold,
                        baseline=monthly_baseline,
                        triggered_at=datetime.now(timezone.utc).isoformat(),
                    ))

        # 3. Cost anomaly (behavioral proxy for replanning loops)
        if obs.cost_usd > 0 and self.baseline_scores.get("cost_per_task", 0) > 0:
            cost_ratio = obs.cost_usd / self.baseline_scores["cost_per_task"]
            if cost_ratio > self.config.cost_multiplier_alert:
                self._fire_alert(QualityAlert(
                    dimension="cost",
                    severity="warning",
                    message=(
                        f"Task cost ${obs.cost_usd:.4f} is {cost_ratio:.1f}x baseline "
                        f"(${self.baseline_scores['cost_per_task']:.4f}). "
                        f"Possible replanning loop."
                    ),
                    current_value=obs.cost_usd,
                    threshold=self.baseline_scores["cost_per_task"] * self.config.cost_multiplier_alert,
                    baseline=self.baseline_scores["cost_per_task"],
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                ))

        # 4. Latency P95
        latencies = sorted([o.latency_ms for o in recent])
        if len(latencies) >= 20:  # Need sufficient samples
            p95 = latencies[int(len(latencies) * 0.95)]
            if p95 > self.config.latency_p95_alert_ms:
                self._fire_alert(QualityAlert(
                    dimension="latency",
                    severity="critical",
                    message=f"P95 latency {p95:.0f}ms exceeds {self.config.latency_p95_alert_ms}ms",
                    current_value=p95,
                    threshold=self.config.latency_p95_alert_ms,
                    baseline=self.baseline_scores.get("latency_p95_ms", 5000),
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                ))

        # 5. Error rate
        total_tool_calls = sum(o.tool_call_count for o in recent)
        total_failures = sum(o.tool_call_failures for o in recent)
        if total_tool_calls > 0:
            error_rate = total_failures / total_tool_calls
            if error_rate > self.config.error_rate_alert:
                self._fire_alert(QualityAlert(
                    dimension="error_rate",
                    severity="critical",
                    message=(
                        f"Tool call error rate {error_rate:.1%} exceeds "
                        f"{self.config.error_rate_alert:.1%} "
                        f"({total_failures}/{total_tool_calls} in window)"
                    ),
                    current_value=error_rate,
                    threshold=self.config.error_rate_alert,
                    baseline=0.01,
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                ))

        # 6. Loop detection
        if obs.replanning_iterations > self.config.loop_detection_threshold:
            self._fire_alert(QualityAlert(
                dimension="loop_detection",
                severity="critical",
                message=(
                    f"Request {obs.request_id} hit {obs.replanning_iterations} "
                    f"replanning iterations (threshold: {self.config.loop_detection_threshold})"
                ),
                current_value=obs.replanning_iterations,
                threshold=self.config.loop_detection_threshold,
                baseline=3,
                triggered_at=datetime.now(timezone.utc).isoformat(),
            ))

    def _fire_alert(self, alert: QualityAlert):
        """Fire alert, deduplicating by dimension within the evaluation window."""
        key = f"{alert.dimension}:{alert.severity}"
        existing = self._active_alerts.get(key)
        if existing:
            # Deduplicate: only re-fire if last alert was >15min ago
            last_time = datetime.fromisoformat(existing.triggered_at)
            now = datetime.now(timezone.utc)
            if (now - last_time).total_seconds() < 900:
                return
        self._active_alerts[key] = alert
        self.alert_callback(alert)

    def update_monthly_baseline(self, scores: dict[str, float]):
        """Update monthly baseline scores for drift detection."""
        self._monthly_baselines = scores

    def get_dashboard_metrics(self) -> dict:
        """Return current metrics for dashboard display."""
        recent = list(self._observations)[-1000:]
        if not recent:
            return {"status": "no_data"}

        metrics = {}
        for dim in ["correctness", "faithfulness", "relevance", "safety"]:
            values = [o.scores.get(dim) for o in recent if dim in o.scores]
            if values:
                metrics[f"{dim}_mean"] = round(sum(values) / len(values), 3)
                metrics[f"{dim}_min"] = round(min(values), 3)

        latencies = [o.latency_ms for o in recent]
        sorted_lat = sorted(latencies)
        metrics["latency_p50_ms"] = sorted_lat[len(sorted_lat) // 2]
        metrics["latency_p95_ms"] = sorted_lat[int(len(sorted_lat) * 0.95)]
        metrics["total_observations"] = len(recent)
        metrics["active_alerts"] = len(self._active_alerts)

        return metrics
```

### 5.7 Structured Eval Logging and Reporting

```python
"""
Structured eval logging: captures every eval run as a machine-readable record
for trend analysis, audit compliance, and CI integration.

Dependencies: pydantic>=2.0
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class EvalMetricResult(BaseModel):
    metric_name: str
    score: float
    threshold: float
    passed: bool
    judge_model: Optional[str] = None
    judge_rationale: Optional[str] = None


class EvalCaseResult(BaseModel):
    case_id: str
    category: str
    runs: int
    passes: int
    pass_rate: float
    metrics: list[EvalMetricResult]
    latency_ms: float
    tokens_used: int
    cost_usd: float


class EvalRunReport(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    trigger: str  # "pre_merge", "nightly", "production_sample", "manual"
    git_sha: Optional[str] = None
    pr_number: Optional[int] = None
    model_version: str
    prompt_hash: str  # SHA of the prompt template
    dataset_version: str
    dataset_checksum: str
    total_cases: int
    passing_cases: int
    pass_rate: float
    metrics_summary: dict[str, float]  # metric_name -> median score
    baseline_comparison: dict[str, dict]  # metric_name -> {baseline, current, change_pct}
    case_results: list[EvalCaseResult]
    total_cost_usd: float
    wall_time_seconds: float
    verdict: str  # "PASS", "FAIL", "ERROR"
    failure_reasons: list[str]


class EvalLogger:
    """
    Append-only structured logger for eval runs.
    Each run produces a single JSONL record for audit and trend analysis.
    """

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _log_path(self, trigger: str) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m")
        return self.log_dir / f"eval_runs_{trigger}_{date_str}.jsonl"

    def log_run(self, report: EvalRunReport):
        """Append a run report to the structured log."""
        path = self._log_path(report.trigger)
        with open(path, "a") as f:
            f.write(report.model_dump_json() + "\n")

    def get_trend(
        self, trigger: str, metric_name: str, last_n: int = 30
    ) -> list[dict]:
        """Retrieve score trend for a metric across recent runs."""
        path = self._log_path(trigger)
        if not path.exists():
            return []

        runs = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    report = EvalRunReport.model_validate_json(line)
                    if metric_name in report.metrics_summary:
                        runs.append({
                            "run_id": report.run_id,
                            "timestamp": report.timestamp,
                            "score": report.metrics_summary[metric_name],
                            "verdict": report.verdict,
                        })

        return runs[-last_n:]

    def get_failure_frequency(self, trigger: str, last_n: int = 30) -> dict:
        """Analyze which cases fail most frequently across recent runs."""
        path = self._log_path(trigger)
        if not path.exists():
            return {}

        case_failures: dict[str, int] = {}
        case_total: dict[str, int] = {}

        with open(path) as f:
            lines = f.readlines()

        for line in lines[-last_n:]:
            line = line.strip()
            if not line:
                continue
            report = EvalRunReport.model_validate_json(line)
            for case in report.case_results:
                case_total[case.case_id] = case_total.get(case.case_id, 0) + 1
                if case.pass_rate < 1.0:
                    case_failures[case.case_id] = case_failures.get(case.case_id, 0) + 1

        return {
            case_id: {
                "failure_count": case_failures.get(case_id, 0),
                "total_runs": total,
                "failure_rate": case_failures.get(case_id, 0) / total,
            }
            for case_id, total in sorted(
                case_total.items(),
                key=lambda x: case_failures.get(x[0], 0) / x[1],
                reverse=True,
            )
        }
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: End-to-End Eval Pipeline for a Customer Support Agent with CI/CD Regression Gates

**Problem statement**: A B2B SaaS company operates a customer support agent that handles 50,000 tickets/month across billing, shipping, and technical support. The agent uses RAG over a 200k-document knowledge base and calls 12 external tools (CRM, ticketing, refund processing). Prompt engineers push changes weekly. The team has experienced three production regressions in the past quarter where prompt changes degraded answer quality for a specific ticket category, each requiring 2-3 days of engineering time to diagnose and revert. The VP of Engineering wants automated quality gates that prevent regressions before they reach customers while keeping eval infrastructure costs under $1,000/month.

**Proposed architecture:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           CI/CD EVAL PIPELINE                                │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  TRIGGER LAYER                                                        │  │
│  │                                                                       │  │
│  │  GitHub Actions workflow triggered on:                                │  │
│  │  - prompts/** changes     (prompt engineering)                       │  │
│  │  - config/model*          (model version changes)                    │  │
│  │  - retrieval/**           (chunking, embedding, top-k changes)       │  │
│  │  - tools/**               (tool definition changes)                  │  │
│  │                                                                       │  │
│  │  Two parallel paths:                                                  │  │
│  │  ┌─────────────────────┐    ┌──────────────────────────────────────┐ │  │
│  │  │ PRE-MERGE GATE      │    │ NIGHTLY SWEEP (cron: 02:00 UTC)     │ │  │
│  │  │ 100 cases            │    │ 500 cases (full golden dataset)     │ │  │
│  │  │ GPT-4o-mini judge    │    │ GPT-4o + Opus multi-judge ensemble  │ │  │
│  │  │ 3 runs/case          │    │ 5 runs/case                        │ │  │
│  │  │ ~4 min runtime       │    │ ~45 min runtime                    │ │  │
│  │  │ ~$1.20/run           │    │ ~$25/run                           │ │  │
│  │  │ BLOCKS MERGE on fail │    │ ALERTS + DASHBOARD on regression   │ │  │
│  │  └─────────┬───────────┘    └───────────────┬────────────────────┘ │  │
│  └────────────┼────────────────────────────────┼─────────────────────────┘  │
│               v                                v                             │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  EVAL EXECUTION ENGINE (DeepEval + custom evaluators)                  │  │
│  │                                                                        │  │
│  │  Layer 1: Deterministic (ms, free)                                    │  │
│  │  - JSON schema validity   - Tool-call arg format   - PII absence     │  │
│  │  - Output length bounds   - Required field check                     │  │
│  │                                                                        │  │
│  │  Layer 2: RAG metrics (RAGAS, per-span)                               │  │
│  │  - Faithfulness (is answer grounded in retrieved docs?)               │  │
│  │  - Context precision (are retrieved docs relevant?)                   │  │
│  │  - Answer relevancy (does answer address the question?)               │  │
│  │                                                                        │  │
│  │  Layer 3: LLM-as-judge (cross-family ensemble)                        │  │
│  │  - Correctness vs. reference    - Helpfulness    - Safety             │  │
│  │  - 3 judges: GPT-4o-mini + Haiku + Gemini Flash (majority vote)      │  │
│  │                                                                        │  │
│  │  Layer 4: Trajectory eval (agent-specific)                            │  │
│  │  - Step efficiency vs. reference path                                 │  │
│  │  - Tool selection accuracy       - No forbidden actions               │  │
│  └──────────────────────────────┬─────────────────────────────────────────┘  │
│                                 v                                            │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  GOLDEN DATASET (versioned in git, JSONL)                              │  │
│  │                                                                        │  │
│  │  500 cases: 200 production failures + 150 expert edge cases           │  │
│  │           + 100 per-category regression + 50 safety/adversarial       │  │
│  │                                                                        │  │
│  │  Stratified by: billing (30%), shipping (25%), tech_support (25%),    │  │
│  │                 multi-step (10%), safety (10%)                        │  │
│  │                                                                        │  │
│  │  Quarterly refresh + continuous production failure ingestion           │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  PRODUCTION MONITORING (async, 5% sampling)                            │  │
│  │                                                                        │  │
│  │  Sample 5% of live tickets → async LLM-judge + deterministic checks   │  │
│  │  7-day rolling scores per category                                     │  │
│  │  Alert: faithfulness < 0.85 for 1h → PagerDuty                       │  │
│  │  Alert: any dimension drops >0.05 from monthly baseline → Slack       │  │
│  │  Feedback: thumbs-down → review queue → golden dataset case           │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix:**

| Dimension | A: DeepEval + RAGAS + custom judges | B: Braintrust end-to-end platform | C: LangSmith + custom CI scripts |
|---|---|---|---|
| **Monthly cost** | ~$300-500 (judge LLM + compute, no platform fee) | ~$250/mo (Pro tier) + judge LLM ~$200 | ~$40-80/seat + judge LLM ~$200 |
| **CI integration** | Native (`deepeval test run` as pytest). Zero glue. | API-based, some glue needed | LangSmith SDK + custom GitHub Action |
| **Eval depth** | Full (unit through trajectory, RAGAS for RAG, custom for agent) | Good (eval-first design, Loop Agent for analysis) | Good (annotation queues, pairwise, online eval) |
| **Ops complexity** | Medium (manage eval scripts, judge pool, dataset versioning) | Low (managed platform, Brainstore DB) | Medium (platform config + custom scripts) |
| **Data residency** | Full control (self-hosted eval runner, judge calls are API) | No self-host option | Enterprise only for self-host |
| **Trajectory eval** | Custom code required (build on FutureAGI patterns) | Supported via custom evaluators | Native with LangGraph integration |
| **Vendor lock-in** | Low (OSS eval library, standard judge APIs) | Medium (Brainstore, proprietary API) | Medium (LangChain ecosystem tie-in) |

**Decision rationale**: Option A (DeepEval + RAGAS + custom judges) wins for this scenario. The team needs deep eval coverage across all four layers (unit, component, e2e, trajectory) with strong CI integration. DeepEval's pytest-native `assert_test` and `deepeval test run` commands integrate with existing CI without glue code. RAGAS provides validated RAG-specific metrics (faithfulness, context precision) that the team needs given the 200k-doc knowledge base. The cross-family judge ensemble (GPT-4o-mini + Haiku + Gemini Flash) costs ~$300-500/month total -- well under the $1,000 budget and less than the engineering cost of a single production regression (~$5,000-15,000 in engineering time).

---

### 6.2 Scenario: Production Observability Platform for a Multi-Agent Financial Document Processing System

**Problem statement**: A fintech company operates a multi-agent system that processes 10,000 financial documents/day (annual reports, SEC filings, earnings calls). The system has 4 agents: Document Classifier, Extractor, Analyst, and Report Generator. Each document traverses all 4 agents, producing 40-80 spans per document. The system must comply with SOC 2 Type II, GDPR, and is preparing for EU AI Act high-risk classification (financial advisory). Current observability is ad-hoc logging -- the team cannot diagnose why 3-5% of documents produce incorrect financial summaries, and the compliance team has flagged the lack of audit trails. Total observability budget: $2,000/month for platform and infrastructure.

**Proposed architecture:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    MULTI-AGENT DOCUMENT PROCESSING SYSTEM                     │
│                                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────────┐        │
│  │Classifier│──>│Extractor │──>│ Analyst  │──>│Report Generator │        │
│  │Agent     │   │Agent     │   │Agent     │   │Agent            │        │
│  │          │   │          │   │          │   │                  │        │
│  │ OTel SDK │   │ OTel SDK │   │ OTel SDK │   │ OTel SDK         │        │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └────────┬─────────┘        │
│       │              │              │                    │                   │
│       └──────────────┴──────────────┴────────────────────┘                   │
│                              │                                               │
│                    W3C traceparent propagation                               │
│                    (single trace per document)                               │
│                              │                                               │
│                              v                                               │
│                    ┌─────────────────────┐                                   │
│                    │  OTel Collector     │                                   │
│                    │                     │                                   │
│                    │  Processors:        │                                   │
│                    │  1. PII redactor    │  Financial data (account numbers, │
│                    │     (regex + NER    │  names, SSNs, portfolio values)   │
│                    │      microservice)  │  redacted before storage          │
│                    │  2. Cost enricher   │                                   │
│                    │  3. Tail-based      │  100% errors + slow + 10% success │
│                    │     sampler         │                                   │
│                    │  4. Batch exporter  │                                   │
│                    └──────────┬──────────┘                                   │
│                               │                                              │
│                    ┌──────────┴──────────┐                                   │
│                    v                     v                                    │
│          ┌─────────────────┐   ┌────────────────────┐                       │
│          │  Langfuse       │   │  Prometheus         │                       │
│          │  (self-hosted,  │   │  + Grafana          │                       │
│          │   EU data       │   │                     │                       │
│          │   residency)    │   │  Metrics:           │                       │
│          │                 │   │  - doc processing   │                       │
│          │  Traces:        │   │    latency P50/P95  │                       │
│          │  - Full span    │   │  - tokens/doc       │                       │
│          │    trees        │   │  - cost/doc         │                       │
│          │  - Agent hand-  │   │  - error rate by    │                       │
│          │    offs visible │   │    agent type       │                       │
│          │  - PII-redacted │   │  - quality scores   │                       │
│          │    payloads     │   │    by dimension     │                       │
│          └────────┬────────┘   └─────────┬──────────┘                       │
│                   │                      │                                    │
│                   v                      v                                    │
│          ┌─────────────────────────────────────────────┐                     │
│          │  ASYNC EVAL LAYER                            │                     │
│          │                                              │                     │
│          │  Sampled traces (10%) → eval pipeline:       │                     │
│          │  - Extraction accuracy vs. reference         │                     │
│          │  - Financial figure validation (det.)        │                     │
│          │  - Reasoning faithfulness (LLM judge)        │                     │
│          │  - Cross-agent consistency checks            │                     │
│          │  - Trajectory efficiency scoring             │                     │
│          └────────────────────┬────────────────────────┘                     │
│                               │                                              │
│                               v                                              │
│          ┌─────────────────────────────────────────────┐                     │
│          │  ALERT & COMPLIANCE                          │                     │
│          │                                              │                     │
│          │  Alerts:                                     │                     │
│          │  - Extraction accuracy < 0.92 → PagerDuty   │                     │
│          │  - Error rate > 3% any agent → PagerDuty    │                     │
│          │  - Cost/doc > 2x baseline → Slack            │                     │
│          │  - P95 latency > 120s → Slack                │                     │
│          │                                              │                     │
│          │  Compliance:                                  │                     │
│          │  - Immutable audit log (append-only)          │                     │
│          │  - Per-user crypto-shredding (GDPR)          │                     │
│          │  - 7-year cold retention (SOC 2 + EU AI Act) │                     │
│          │  - Quarterly access reviews (RBAC)           │                     │
│          │  - Config change tracking (who/when/what)    │                     │
│          └─────────────────────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix:**

| Dimension | A: Self-hosted Langfuse + Prometheus/Grafana | B: Arize Phoenix (self-hosted) + Prometheus | C: Braintrust (cloud) + Datadog |
|---|---|---|---|
| **Monthly cost** | ~$500-800 (infra: K8s node + storage) | ~$400-700 (infra: lighter footprint) | ~$1,500-2,500 (Braintrust Pro $249 + Datadog LLM Obs.) |
| **Data residency** | Full control (self-hosted in EU) | Full control (self-hosted in EU) | Braintrust: US-hosted. Datadog: region-selectable |
| **GDPR compliance** | Full (crypto-shredding, retention, erasure all under your control) | Full (same as Langfuse) | Partial (depends on Braintrust DPA, Datadog EU region) |
| **EU AI Act readiness** | Full (immutable logs, retention, audit trails in your infrastructure) | Full | Partial (platform must provide audit export APIs) |
| **Trace depth** | Full span trees, @observe decorator, 100+ integrations | OTel-native, all eval runs auto-traced | Good tracing, Loop Agent for analysis |
| **Eval integration** | LLM-judge in UI (no code), code evaluators in platform | Dynamic concurrency, deterministic + LLM judge | Eval-first design, strong but cloud-only |
| **Ops complexity** | Medium (Docker/K8s deploy, backup, upgrade) | Medium (similar to Langfuse) | Low (fully managed) |
| **SOC 2 / HIPAA** | Self-host gives full control (your compliance boundary) | Self-host gives full control | Braintrust: SOC 2 + HIPAA certified |
| **Scalability** | Langfuse backed by ClickHouse (acquired) -- handles high cardinality well | Elastic License, proven at scale | Managed scaling |

**Decision rationale**: Option A (self-hosted Langfuse + Prometheus/Grafana) wins for this scenario. The regulatory requirements are the binding constraint: GDPR data residency in the EU, EU AI Act high-risk classification requiring immutable audit trails and years of retention, and SOC 2 Type II requiring full infrastructure control. Self-hosted Langfuse (MIT licensed, backed by ClickHouse post-acquisition) provides EU-hosted trace storage with crypto-shredding for GDPR erasure, immutable append-only logs for audit, and zero data leaving the organization's infrastructure. The 10,000 docs/day workload produces ~400,000-800,000 spans/day -- within self-hosted Langfuse's capacity on a modest K8s deployment. The ~$500-800/month infrastructure cost is well under the $2,000 budget, leaving room for LLM-as-judge costs (~$200-400/month at 10% sampling with a cheap judge). Cloud-only options (Braintrust, LangSmith non-enterprise) cannot satisfy EU data residency for financial data without significant contractual and architectural complexity.
