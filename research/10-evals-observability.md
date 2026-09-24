# Research: Evals & Observability

**Date researched**: 2026-09-23
**Sources consulted**: 48

---

## 1. System Topology & Mechanics

### 1.1 Eval Taxonomy: Four Evaluation Layers

Evaluation in 2026 runs at four distinct layers, each with different datasets, methods, and failure thresholds ([Confident AI](https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide), [DeepEval](https://deepeval.com/docs/evaluation-component-level-llm-evals)):

| Layer | What it tests | Speed | Method | Example |
|---|---|---|---|---|
| **Unit** | Single output properties | ms | Deterministic code checks | JSON parses, no PII in output, label in allowed set |
| **Component / Integration** | Individual spans -- retrievers, tool calls, LLM generations | seconds | Metrics on per-span traces | Retrieval precision, tool-call argument validity |
| **End-to-End** | Full task completion from input to final output | seconds-minutes | LLM-as-judge, golden dataset comparison | "Did the agent actually answer the user's question correctly?" |
| **Trajectory** | Entire execution path -- every tool call, reasoning step, intermediate decision | minutes | Trajectory-specific metrics | Step efficiency, tool selection accuracy, reasoning quality, action safety |

A 95% per-step accuracy over 8 steps compounds to ~66% end-to-end. This is why trajectory evaluation matters: agents that pass per-turn evals can fail in production due to compounding errors.

Three metric categories have crystallized: **deterministic** (exact match, BLEU, ROUGE, BERTScore, JSON-schema validity), **rubric-based** (LLM-as-judge or human scoring), and **composite** (weighted combinations). Deterministic metrics have zero LLM-judge cost and are perfectly reproducible but miss semantic and contextual quality.

### 1.2 Golden Datasets

A golden dataset is a curated, versioned collection of inputs, contexts, and expected outputs that serves as the source of truth for measuring quality ([Langfuse](https://langfuse.com/resources/engineering/golden-dataset-evaluation), [Maxim AI](https://www.getmaxim.ai/articles/building-a-golden-dataset-for-ai-evaluation-a-step-by-step-guide/), [Statsig](https://www.statsig.com/perspectives/golden-datasets-evaluation-standards)).

**Construction best practices:**

- **Source from real failures**: Thumbs-down feedback, abandoned sessions, human escalations, flagged low-confidence outputs. Documentation-derived or purely synthetic datasets are too clean and miss real edge cases.
- **Combine three sources**: Human-crafted edge cases + real production samples (PII-scrubbed) + synthetic expansions for underrepresented scenarios.
- **Right-size**: Start with 20-50 reviewed items covering most important behaviors. Grow to 100-1,000 for a full regression set. Beyond 1,000, maintenance breaks down -- switch to stratified sampling or segment-specific datasets. 100 diverse items beat 1,000 near-duplicates.
- **Expert-label everything**: Pair every input with a reference answer and a short rationale.
- **Version control**: Treat datasets as append-mostly logs with date metadata. ISO/IEC 42001 requires traceability, transparency, and continuous improvement -- golden datasets should support this via metadata, audit trails, and versioning.

**Maintenance**: Treat as a living entity. Refresh quarterly at minimum, adding cases from production failures, new user patterns, and newly approved tools/retrieval sources. Never modify the dataset to fit a new model -- if accuracy drops >5pp, revert the model change and investigate.

### 1.3 LLM-as-a-Judge

LLM-as-a-judge uses one LLM to score another's output against a rubric ([Survey on LLM-as-a-Judge](https://arxiv.org/html/2411.15594v1), [Comet](https://www.comet.com/site/blog/llm-as-a-judge/)).

**Three paradigms:**

| Paradigm | How it works | Best for | Scaling |
|---|---|---|---|
| **Pointwise** | Score a single response against a rubric (1-5 or pass/fail) | Continuous monitoring, debugging, longitudinal tracking | Linear |
| **Pairwise** | Compare two responses, decide which is better | Model selection, A/B testing prompt variants | Quadratic (O(n^2)) |
| **Reference-based** | Compare response against a gold-standard answer | Factual accuracy, structured output validation | Linear |

Pairwise comparisons approximate human preferences with greater fidelity than pointwise scoring, but scaling is quadratic -- best suited for periodic A/B tests, not continuous monitoring.

**Known biases ([Survey](https://arxiv.org/html/2411.15594v1), [Bansal](https://jatinbansal.com/ai-engineering/llm-as-judge/)):**

| Bias | Description | Mitigation |
|---|---|---|
| **Position** | In pairwise prompts, one slot is systematically favored | Swap-and-average: run each comparison twice with positions swapped. 2x cost, non-optional. |
| **Verbosity** | Prefer longer responses regardless of quality | Length-controlled scoring (AlpacaEval 2 approach), explicit rubric criteria penalizing unnecessary length |
| **Self-preference** | Judge prefers outputs from its own model family | Benchmark multiple judge families against human labels |
| **Capability-dependent leniency** | Systematically favors more capable models' outputs | Multi-judge ensemble across model families |

**Calibration approaches:**

- Narrower scales (1-5 or binary pass/fail) reduce noise vs. 1-10 scales.
- Provide calibration examples demonstrating what each score level looks like.
- Three cross-family small judges beat one large judge at 7x lower cost, with lower self-preference bias.
- Post-hoc quantitative calibration using item response theory frames reliability as a property of the measurement instrument.
- The PAJAMA framework (program-as-judge) uses LLM-synthesized, auditable judging programs, achieving 3 orders of magnitude cost reduction.

**G-Eval** (Liu et al., EMNLP 2023, [arxiv](https://arxiv.org/abs/2303.16634), 1,771 citations): Framework using LLMs with chain-of-thought and form-filling paradigm. GPT-4 backbone achieves Spearman correlation of 0.514 with human judgments on summarization, outperforming all prior methods. Uses probability-weighted summation of output scores for fine-grained continuous scoring.

### 1.4 Trajectory Evaluation

Trajectory evaluation scores the entire execution path rather than just the final output ([LangChain](https://www.langchain.com/resources/llm-evaluation-framework), [FutureAGI](https://futureagi.com/blog/agent-evaluation-frameworks-2026/)).

**Seven trajectory metrics** (FutureAGI's AgentTrajectoryInput):
1. TaskCompletion -- did the agent accomplish the goal?
2. StepEfficiency -- did it take a reasonable number of steps?
3. ToolSelectionAccuracy -- did it pick the right tools?
4. TrajectoryScore -- overall path quality
5. GoalProgress -- incremental progress tracking
6. ActionSafety -- no policy-violating intermediate actions
7. ReasoningQuality -- sound logic in planning and execution

**Framework support**: LangSmith, Arize Phoenix, DeepEval, and Galileo support trajectory evaluation natively. OpenAI Evals and RAGAS are weighted toward output and RAG-component scoring.

### 1.5 Eval Frameworks

**Anthropic's approach** ([Anthropic docs](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests), [Anthropic engineering](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)):

- Three grading methods: code-based (fastest, most reliable -- "by far the best if you can design an eval for it"), human grading (most capable but slow/expensive), model-based grading (LLM grades itself).
- Agent eval specifics: you're evaluating the harness AND the model together. Agent behavior varies between runs, so use **pass@k** metric (likelihood of at least one correct solution in k attempts).
- Statistical rigor: Anthropic published research applying power analysis and statistical theory to evals, recommending confidence intervals and proper sample sizing.

**OpenAI Evals** ([GitHub](https://github.com/openai/evals), 17.6k stars):

- Registry-based workflow: evals defined as YAML files, run via `oaieval` CLI.
- Three eval types: Match (exact string), Includes (substring), ModelGraded (another model scores).
- Dashboard + API for programmatic access.
- **Major 2026 change**: Hosted Evals dashboard/API becoming read-only October 31, 2026; closing November 30, 2026. Migration path to Promptfoo.

**DeepEval** ([GitHub](https://github.com/confident-ai/deepeval)):

- "Pytest for LLMs" -- `assert_test` raises on sub-threshold score, `deepeval test run` fails CI jobs exactly like broken unit tests.
- Largest publicly documented metric library: conversational quality, hallucination, bias, toxicity, summarization, tool-use/agent trajectories.
- Component-level eval via `@observe` makes each trajectory step a unit test.
- Built-in caching to control cost. Native CI/CD integration.

**RAGAS** ([EACL 2024 paper](https://arxiv.org/abs/2309.15217)):

- Research-validated metrics: faithfulness, answer relevancy, context precision/recall.
- Reference-free metrics designed to need minimal ground-truth data.
- A library, not a test runner -- computes scores but does not assert, gate builds, or track runs. You bring your own CI plumbing.
- RAG-shaped catalog; for tool-calling agent trajectory eval, reach for custom logic or another tool.

**Key difference**: DeepEval is a testing framework with assertions and CI gates. RAGAS is a metrics engine. DeepEval needs less glue for CI/CD; RAGAS needs a wrapper.

### 1.6 Observability Stack

#### LangSmith ([LangChain](https://www.langchain.com/langsmith-platform))

- **Tracing**: Structured trace trees (root run + child runs). Every LLM call, tool invocation, reasoning step is observable. LangSmith Engine clusters production failures into prioritized issues, finds root cause, proposes fix.
- **Evaluation**: Offline (curated datasets, pre-deployment) + Online (production traffic, real-time quality drift). Human annotation queues, heuristic checks, LLM-as-judge, pairwise comparisons, custom evaluators.
- **Dataset management**: Versioned test cases, experiment runner, comparison across dataset/code versions.
- **Monitoring**: Token usage, cost breakdowns, latency percentiles (P50, P99), unified cost view across full agent workflow (LLM calls + retrieval + tool execution + external API).
- **2025-2026 updates**: LangChain 1.0 ships evals as first-class concern. Full OpenTelemetry support. AWS Marketplace availability. LangSmith Fleet for agent deployment.
- **Pricing**: Free (5k traces/month), Plus $39/seat/month (50k traces, 90-day retention), Enterprise custom. Overage: $2.50/1k base traces.

#### Langfuse ([langfuse.com](https://langfuse.com/))

- **Open source**: MIT-licensed, 21k+ GitHub stars (Feb 2026). Acquired by ClickHouse. Used by 50,000+ companies.
- **Tracing**: `@observe()` decorator for Python. 100+ library/framework integrations. OpenTelemetry support. Drop-in integrations for popular frameworks.
- **Scoring**: LLM-as-judge (configurable in UI, no code) + programmatic scores via SDK. Numeric, boolean, categorical. Code evaluators in Python/TypeScript run directly in platform (July 2026).
- **Monitoring**: Score analytics, custom dashboards, Slack/webhook/GitHub Actions alerts. Boolean score averages chart pass rates.
- **Deployment**: Self-host via Docker Compose in 5 min, single VM, or Kubernetes via Helm.
- **Pricing**: Free self-host (unlimited). Cloud: Hobby 50k observations/mo free, $59/seat teams. Unit-based pricing (traces + observations + scores).

#### Arize Phoenix ([arize.com/phoenix](https://arize.com/phoenix/))

- **Open source**: 10.2k+ GitHub stars, Elastic License 2.0. Fully self-hostable, zero feature gates.
- **Built on OpenTelemetry**: Vendor-agnostic observability, export traces to any OTel-compatible backend.
- **Features**: Tracing, evaluation, versioned datasets, experiments, playground, prompt management, PXI (AI engineering agent for debugging).
- **Evaluation**: Deterministic + LLM-as-judge. Dynamic concurrency adjusts parallelism based on provider rate limits. All evaluator runs automatically traced via OTel.
- **Framework support**: OpenAI Agents SDK, Claude Agent SDK, LangGraph, Vercel AI SDK, Mastra, CrewAI, LlamaIndex, DSPy.
- **Pricing**: Phoenix is free (self-host). Arize AX (commercial) is custom pricing.

#### Braintrust ([braintrust.dev](https://www.braintrust.dev/))

- **Eval-first**: Evaluation scores live directly inside the observability workflow. Loop Agent autonomously analyzes production logs, identifies failure patterns, suggests optimizations.
- **Brainstore**: Purpose-built database for AI-specific data patterns.
- **Security**: SOC 2 Type II, GDPR, SSO, RBAC, HIPAA, hybrid deployment.
- **Funding**: $80M Series B (Feb 2026) at $800M valuation. Customers: Notion, Stripe, Vercel, Dropbox, Replit.
- **Pricing**: Starter free (1GB data, 10k scores, unlimited users), Pro $249/mo (5GB, 50k scores), Enterprise custom.

### 1.7 OpenTelemetry for LLM Observability

The GenAI Special Interest Group (formed April 2024) standardizes how GenAI operations are recorded ([OpenTelemetry blog](https://opentelemetry.io/blog/2026/genai-observability/), [Greptime](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)).

**Current state** (Semantic Conventions v1.41.0, Development status):

- Four primary areas: LLM client spans, agent spans, events (prompt/completion content capture), metrics.
- Agentic conventions (in proposal): attributes for tasks, actions, agents, teams, artifacts, memory with relationships.
- MCP conventions: trace broken MCP tool calls across agent boundaries.
- **Industry adoption**: Datadog, Honeycomb, New Relic support natively. LangChain, CrewAI, AutoGen emit OTel-compliant spans. OpenAI Python SDK instrumentation is most mature.
- **Limitation**: Captures what happened, does not assess whether what happened was good. The boundary between telemetry and evaluation.

### 1.8 Regression Gates: CI/CD Integration

Every PR that touches a prompt, model version, or retrieval configuration should trigger an eval run against the golden dataset ([Galtea](https://galtea.ai/blog/automated-llm-evaluation-building-a-ci-cd-quality-gate-that-actually-runs), [Traceloop](https://www.traceloop.com/blog/automated-prompt-regression-testing-with-llm-as-a-judge-and-ci-cd), [Braintrust](https://www.braintrust.dev/articles/best-ai-evals-tools-cicd-2025)).

**Two-tier pattern:**

| Tier | When | Dataset size | Judge model | Runtime |
|---|---|---|---|---|
| **Pre-merge gate** | Every PR | 50-100 representative cases | Cheaper model (GPT-4o-mini, Haiku) | Minutes |
| **Nightly sweep** | Scheduled against main | Full golden dataset (500+) | Frontier model (GPT-4o, Opus) | 30-60 min |

**Handling non-determinism:**

- Set temperature to 0 (doesn't guarantee identical outputs across API calls).
- Run each test case 3-5 times, evaluate statistical consistency.
- Use semantic similarity and LLM-as-judge, not exact string matching.
- Require 4/5 runs to pass rather than perfect consistency.
- Track variance as a metric itself.

**What triggers eval runs**: Model changes (version, provider, inference config), prompt changes (system prompt, few-shot examples, output format), retrieval changes (chunking strategy, embedding model, top-k).

**Closing the loop**: Feed real production failures back into the golden dataset. When a user reports a bad answer, that case becomes a permanent regression test.

### 1.9 Practitioner Wisdom

**Hamel Husain & Shreya Shankar** ([hamel.dev](https://hamel.dev/blog/posts/evals-faq/), Maven course -- #1 highest-grossing):

- Start with error analysis, not infrastructure: manually review 20-50 LLM outputs whenever you make significant changes.
- Build a failure taxonomy from that review. Use it to decide which evaluators to build.
- Code-based evals for objective rules, LLM judges for failures requiring human judgment.
- Criteria drift is real: evaluation criteria shift after reviewing model outputs. Evaluation is iterative, human-driven sensemaking, not a static target.
- Forthcoming O'Reilly book: "Evals for AI Engineers."

**Eugene Yan**: The "God Evaluator" anti-pattern -- benchmark is human performance, not perfection. Process over tooling. Treat evals as the scientific method.

**Collaborative work**: "What We've Learned From a Year of Building with LLMs" (O'Reilly Radar) by Eugene Yan, Bryan Bischof, Charles Frye, Hamel Husain, Jason Liu, Shreya Shankar.

---

## 2. Token Economics & NFR Metrics

### 2.1 LLM-as-Judge Token Costs

Per-evaluation costs scale with context size and judge model ([Arize](https://arize.com/resources/llm-evaluation-costs/), [FutureAGI](https://futureagi.com/eval-tco-calculator/), [Neural Base](https://theneuralbase.com/evaluation-framework/learn/advanced/llm-as-judge-costs/)):

| Parameter | Typical range |
|---|---|
| Tokens per judgment | 500-2,000 (input + output) |
| 1,000 samples x 3 judgments | 1.5-6M tokens per eval run |
| Cost at GPT-4o pricing (Sep 2026) | $5-$25 per 1,000-sample eval pass |
| Cost at Gemini 3.1 Flash | ~$0.15-$0.60 per 1,000-sample pass |
| Classifier alternative | ~$0.01/call or less -- 100-300x cheaper per call |
| Luna-2-class fine-tuned scorer | ~$0.00003/call for 5 dimensions |

**The "double tax"**: Every trace scored by LLM-as-judge gets taxed twice -- judge token cost, plus missed-incident risk if you sample less to control cost.

**The output token asymmetry**: Output tokens priced at 3-8x input tokens across all major models (median ratio ~4:1). Judge explanations are output-heavy, making rationale generation the dominant cost component.

**Pricing collapse context**: API prices dropped ~80% between early 2025 and early 2026. Floor for mainstream APIs is ~$0.20/Mtok input (GPT-5.6 Luna). Flagships run $5-$10/Mtok input (Claude Opus 5 $5/$25, Claude Fable 5 $10/$50).

### 2.2 Observability Platform Pricing Comparison

| Platform | Free tier | Paid tier | Billing unit | Self-host |
|---|---|---|---|---|
| **LangSmith** | 5k traces/mo, 14-day retention | $39/seat/mo (Plus), $2.50/1k overage | Traces | Enterprise only |
| **Langfuse** | 50k observations/mo (cloud), unlimited (self-host) | $59/seat (cloud teams) | Units (traces + observations + scores) | Yes (MIT) |
| **Braintrust** | 1GB data, 10k scores, unlimited users | $249/mo (Pro) | Processed data + scores | No |
| **Arize Phoenix** | Unlimited (self-host) | AX: custom | N/A (self-host) | Yes (ELv2) |
| **Helicone** | 10k requests/mo | Usage-based | Requests | No |
| **Laminar** | 1GB, 7-day retention | $30/mo Hobby, $150/mo Pro | Data volume | No |
| **Confident AI** | Free tier exists | $249/mo Pro | Usage-based | No |

**Unit comparison caveat**: Billing units are not comparable across platforms. Langfuse counts traces + observations + scores as separate units. An agent run with 40-75 spans burns 8-15 Langfuse units but counts as 1 LangSmith trace.

### 2.3 Storage and Infrastructure Costs

- A single agent task may produce 20+ spans. On a 50,000-spans/month plan, a coding agent making 20 tool calls/task exhausts quota after ~2,500 runs.
- Trace storage includes inputs, outputs, latency, cost, metadata. At high volume, cold storage tiering (hot vs. warm vs. cold) is essential.
- Agent workflows multiply costs: one user request can trigger several LLM calls, tool invocations, and sub-agent dispatches, each generating separate trace events.

### 2.4 ROI of Eval Infrastructure

- In 2026, the single biggest reason GenAI apps fail in production is missing or shallow evaluation.
- CloudZero's 2026 AI ROI survey of 260 finance leaders: 64% said tying AI spend to outcomes would change investment decisions.
- FinOps Foundation 2026 State of FinOps: 98% of practitioners now manage AI spending, up from 31% in 2024.
- Deloitte January 2026: AI is the fastest-growing corporate IT expense, with some firms at ~50% of IT spend. Cloud bills rising 19% from AI workloads. Nearly half of leaders expect 3 years to see AI ROI.

### 2.5 Latency: Inline vs. Async Evaluation

| Mode | Latency impact | Use case |
|---|---|---|
| **Inline (synchronous)** | Adds 100ms-2s per request depending on judge model | Real-time guardrails, safety checks |
| **Async (post-hoc)** | Zero user-facing latency | Quality monitoring, regression detection, CI/CD gates |
| **Hybrid** | Fast deterministic checks inline, LLM-judge async | Production default for most teams |

OTel instrumentation overhead: <1ms per call. LLM API latency: 100ms-30s.

---

## 3. Distributed Resilience & State

### 3.1 Eval Dataset Versioning and Storage

- Version control datasets alongside code ([Langfuse](https://langfuse.com/resources/engineering/golden-dataset-evaluation)). Track every schema and label change.
- Append-mostly log model: date items in metadata to gauge staleness at a glance.
- ISO/IEC 42001 mandates traceability, transparency, and continuous improvement -- datasets should support this via metadata, audit trails, and versioning.
- LangSmith, Langfuse, Braintrust, and Phoenix all support versioned datasets natively. For git-native workflows, JSONL files in version control work for datasets under 10k items.

### 3.2 Distributed Trace Collection at Scale

A four-layer observability topology for LLM systems ([Code Intel Log](https://codeintel.xyz/blog/llm-observability-architecture/)):
1. **Structured events** -- individual LLM calls, tool calls, retrieval steps
2. **Distributed traces** -- tree of spans for a single user request across agents
3. **Aggregated metrics** -- P50/P95/P99 latency, token usage, cost, quality scores
4. **Eval-driven quality scores** -- LLM-as-judge results joined back to spans

Infrastructure pattern converges on a sidecar-based OTel pipeline with span-level cost attribution, weighted sampling, and circuit-breaker backpressure.

Multi-agent workloads produce 40-200 spans per user request. Raw log reading is no longer viable at this scale; structured trace trees are essential.

### 3.3 Trace Sampling Strategies

For high-throughput systems ([MLflow](https://mlflow.org/articles/observability-sampling-strategies)):

| Strategy | How it works | Tradeoff |
|---|---|---|
| **Head-based** | Decision at trace start via deterministic hash. Stateless, cheap. | Blind to outcome -- misses errors and slow requests |
| **Tail-based** | Decision after trace completes. Can filter by error, latency, cost. | Requires stateful infrastructure, higher resource cost |
| **Hybrid (recommended)** | Head-based baseline + tail-based rules for errors/slow/rare | Best balance of cost and coverage |
| **Weighted retention** | 100% error traces, 10% successful for cost analysis, 1% for latency distribution | Preserves debugging visibility while reducing volume 90% |

**LLM-specific guidance:**

- Agent traces carry so much per-run variance that a "typical" run barely exists. Treat agent observability as a distinct category with its own retention logic.
- Skip sampling entirely if volume is low, retention rules demand full capture, or you're tracing LLM/agent calls that are non-reproducible.
- For >5,000 requests/min: sampling at 10% + Prometheus-style histogram aggregation reduces storage 90% while preserving latency distributions.
- 1% sampling of 1M calls still captures 10k traces; upsample errors separately.
- Production default in llm-d: parent-based ratio sampling at 10%, OTel Collector in the middle, GenAI semantic conventions.

### 3.4 Eval Pipeline Reliability

- Eval failures should not block deployment permanently -- implement timeout and retry logic for LLM-as-judge calls.
- Rate limit handling: Phoenix adjusts parallelism dynamically based on provider performance to maximize throughput without triggering rate limits.
- Cache eval results: DeepEval ships built-in caching to avoid re-evaluating unchanged outputs.
- Pre-merge gates should use a representative subset; full dataset runs reserved for nightly sweeps.

### 3.5 Cross-Service Trace Correlation

- OpenTelemetry trace context propagation ensures the same trace ID flows across all components (gateway, model server, tool servers, sub-agents).
- The sampling decision is made at trace entry (gateway) and propagated to all components via trace context. This ensures complete traces: either all spans for a request are collected or none are.
- Non-sampled requests have spans built as empty placeholders that record and export nothing, so skipping costs almost nothing.

---

## 4. Enterprise Security & Governance

### 4.1 PII in Traces and Eval Datasets

Trace payloads mix operational metadata with potentially sensitive user data ([IJC](https://ijcjournal.org/InternationalJournalOfComputer/article/view/2458), [Confident AI](https://www.confident-ai.com/knowledge-base/guides/enterprise-ai-governance-audit-trails)):

- OWASP elevated Sensitive Information Disclosure from position 6 (2023) to **LLM02** (2025 Top Ten), noting LLMs now require more organizational data access.
- Enterprise log and trace data contains PII at rates that surprise collecting teams -- often without their knowledge.
- **Redaction approaches**: Hybrid PII detection combining regex speed with NER contextual accuracy, implemented as decoupled microservice. Tokenization replaces sensitive references with secure placeholders. Redact PII before storage, not after.
- OpenTelemetry prompt/completion capture creates data governance risk. Before enabling content capture, implement sampling, redaction, and retention policies.

### 4.2 Access Control for Eval Results and Traces

Minimum role structure for observability ([Confident AI](https://www.confident-ai.com/knowledge-base/guides/enterprise-ai-governance-audit-trails)):

| Role | Access level |
|---|---|
| Engineers | Full trace access, propose config changes |
| PMs / Domain experts | Product-scoped read and annotation |
| QA reviewers | Queue-scoped review |
| Compliance / Audit | Read everything (access logged), approval rights |
| Platform admins | Full administrative access |

**Key design decision**: Separate access to trace metadata from access to trace payloads, since payloads contain user data many roles do not need.

### 4.3 Compliance Requirements

- **EU AI Act** (fully applicable August 2, 2026): Activity logs, risk assessments, human oversight documentation required for high-risk AI systems.
- **NIST AI RMF**: Map tests, runtime controls, and monitoring outputs to framework requirements so governance evidence is produced continuously.
- **ISO 42001**: Requirements for AI management systems -- traceability, transparency, risk management, continuous improvement.
- **GDPR**: Trace payloads are user data; observability store inherits residency obligations. Check regional storage options, where evaluation inference runs, and whether erasure requests can be honored. Crypto-shredding or scoped deletion with audit records enables GDPR erasure while maintaining log immutability.
- **HIPAA**: Braintrust offers HIPAA compliance. Self-hosted solutions (Langfuse, Phoenix) give full data control.

### 4.4 Audit Trails

Four categories of events must be recorded in tamper-proof form:
1. **System behavior**: Every LLM call, tool invocation, decision
2. **Configuration changes**: Prompt updates, model version changes, retrieval config
3. **Human actions**: Annotations, approvals, overrides
4. **Quality measurements**: Eval scores, thresholds, pass/fail decisions

The observability stack is effectively the compliance evidence store. Design it that way from the start; retrofitting means every audit becomes an archaeology project.

### 4.5 Data Retention Policies

- Free tiers: 7-14 day retention (LangSmith Developer: 14 days, Laminar free: 7 days).
- Paid tiers: 30-90 days hot (LangSmith Plus: 90 days, Braintrust Pro: 30 days).
- Cold storage: Archive to S3/GCS for regulatory retention (EU AI Act may require years of logs).
- Deletion workflows: Must satisfy GDPR erasure while maintaining audit integrity.

### 4.6 Breach Cost Context

- US average data breach cost in 2025: $10.22M -- all-time high, driven by higher regulatory fines and slower detection.
- Majority of AI-related breaches occurred in organizations without proper AI access controls.

---

## 5. Production Failure Modes

### 5.1 Eval-Production Gap

The benchmark-vs-production gap is a core issue ([Braintrust](https://www.braintrust.dev/articles/llm-evaluation-guide), [FutureAGI](https://futureagi.com/blog/evaluating-llm-systems-metrics-benchmarks-2026/)):

- Benchmarks score the model alone on multiple-choice or short prompts, with no tools, no retrieval, no parsing layer, no refusal policy. Production runs a full stack, bounded by the weakest link (rarely the base model).
- A 91-MMLU model can lose to an 88-MMLU model on a support agent because retrieval is the binding constraint.
- Offline metrics (BLEU, ROUGE, BERTScore) do not capture safety, relevance, or operational reliability for real-world queries.
- In 2026, the single biggest reason GenAI apps fail in production is missing or shallow evaluation.

### 5.2 LLM-as-a-Judge Inconsistency and Bias

- Position bias: slot-dependent scoring in pairwise comparisons.
- Verbosity bias: systematically rating longer responses higher.
- Self-preference: favoring outputs matching the judge's own style.
- Capability-dependent leniency: intensifies with model capability.
- Judge drift: judge model behavior changes due to upstream provider updates without warning -- "a class of prompts your eval dataset never exercised, scored by metrics that did not catch the failure mode, against a judge model that quietly drifted last Tuesday."

### 5.3 Golden Dataset Staleness

- Production systems evolve quickly; stale test data hides new failure modes.
- Stable test cases provide consistent regression detection, but cases that no longer represent real usage create false confidence.
- **Prescription**: Version golden sets alongside code, refresh quarterly with production-sourced cases. Date items in metadata to see staleness at a glance.

### 5.4 Metric Gaming

- **Benchmark chasing**: Tune for public leaderboards while neglecting production requirements. "Arguing over leaderboard points is the single most common failure mode in 2026 LLM evaluation."
- **Contamination**: Benchmark examples enter pretraining, fine-tuning, prompt development, or evaluator prompts, making results unreliable.
- **Overfitting to eval**: Optimizing for eval metrics instead of actual user satisfaction. Metrics and benchmarks are two different jobs -- most teams run one when they need the other.

### 5.5 Observability Blind Spots

- End-to-end final-answer scoring misses tool-selection regressions, retrieval misses, plan deviations, and loop behavior in 10-50 span agent runs.
- Model providers update models without incrementing version names -- silent quality shifts that only golden-set eval catches.
- Prompt drift: small wording changes accumulate until the prompt behaves differently than intended.
- Model drift: upstream provider updates change behavior without any application code changes.
- A passing gate means "no worse than baseline on the cases we thought to test" -- not "correct." The blind spots are the inputs missing from your dataset.
- A super-majority of YC agent builders report offline suites under-deliver as keeping them current becomes impossible.

### 5.6 Alert Fatigue

- Quality-aware alerts require careful threshold tuning. Track 7-day rolling faithfulness and answer relevance; alert when they drop >0.05 from monthly baseline.
- Cost anomaly detection as behavioral proxy: a context window growing 40% over baseline or tool invocations tripling within an hour may indicate a replanning loop -- catches behavioral failures before users notice quality changes.
- Four quality dimensions (correctness, faithfulness, relevance, safety) each need separate metrics. Teams tracking "quality" with a single number miss specific production incidents.

### 5.7 The Offline Eval Gap

- LLM systems do not have stable, deterministic behavior. They drift through corpus changes, model updates, prompt evolution, and query distribution shift.
- Evaluation is not a checkpoint -- it is continuous infrastructure.
- Production sampling catches drift: sample 5% of live traffic for async evaluation, track rolling metrics, alert on degradation.

---

## 6. Enterprise System Design Scenarios

### 6.1 End-to-End Eval Pipeline for an Agentic Application with CI/CD Regression Gates

**Architecture:**

```
Developer commits prompt/model/retrieval change
        |
        v
[CI Trigger: GitHub Actions / GitLab CI]
        |
        v
[Pre-merge Gate - Fast]                    [Nightly Sweep - Comprehensive]
|                                           |
| 50-100 representative cases               | Full golden dataset (500+)
| Cheaper judge (GPT-4o-mini, Haiku)         | Frontier judge (GPT-4o, Opus)
| 3-5 min runtime                            | 30-60 min runtime
| Blocks merge if quality < threshold        | Reports to dashboard, alerts on regression
|                                           |
v                                           v
[Deterministic checks]                  [LLM-as-judge scoring]
- JSON validity                         - Answer relevance
- Tool-call argument format             - Faithfulness / hallucination
- Output length bounds                  - Reasoning quality
- PII absence                           - Safety compliance
        |
        v
[Threshold Comparison]
- Per-metric: fail if any drops >5% from baseline
- Per-category: fail if any category drops >10%
- Cost/latency: fail if either exceeds 2x baseline
        |
        v
[PR Comment with Results]
- Pass/fail status per metric
- Regression details for failures
- Cost and latency comparison
- Link to trace viewer
        |
        v
[Production Deployment]
        |
        v
[Online Evaluation - Async]
- Sample 5% of live traffic
- Run same evaluators as CI
- Track 7-day rolling scores
- Alert on >0.05 drop from monthly baseline
        |
        v
[Feedback Loop]
- User thumbs-down -> new golden dataset case
- Flagged low-confidence -> review queue
- Production failure -> permanent regression test
```

**Key implementation decisions:**

1. **Golden dataset construction**: Start with 50-100 cases from production failures and expert-crafted edge cases. Version in git alongside code. Refresh quarterly with production-sourced cases.
2. **Evaluator hierarchy**: Deterministic checks first (cheapest, fastest). LLM-as-judge for open-ended quality. Human review for calibration (10% spot-check).
3. **Non-determinism handling**: Temperature 0, run each case 3-5 times, require 4/5 pass, track variance.
4. **Cost control**: Cache eval results (DeepEval built-in), use cheaper judge for pre-merge, frontier judge for nightly. Estimated cost: ~$5-15/day for a 500-case nightly suite with GPT-4o.
5. **Framework choice**: DeepEval for pytest-native CI integration with `deepeval test run`. RAGAS metrics for RAG-specific dimensions. LangSmith/Langfuse for trace-backed evaluation.
6. **Closing the loop**: Every production failure becomes a test case. The dataset grows toward exactly the cases that hurt you.

### 6.2 Production Observability Platform for Multi-Agent System with Alerting

**Architecture:**

```
[Multi-Agent System]
  Agent A (planner) --> Agent B (researcher) --> Agent C (writer)
       |                     |                      |
       v                     v                      v
  [OTel SDK]            [OTel SDK]             [OTel SDK]
  GenAI Semantic        GenAI Semantic         GenAI Semantic
  Conventions           Conventions            Conventions
       |                     |                      |
       +----------+----------+----------+-----------+
                  |
                  v
         [OTel Collector]
         - Tail-based sampling (100% errors, 10% success)
         - PII redaction processor
         - Cost attribution per span
         - Trace context propagation
                  |
          +-------+-------+
          |               |
          v               v
    [Trace Store]    [Metrics Store]
    Langfuse /       Prometheus /
    Phoenix          InfluxDB
          |               |
          v               v
    [Eval Layer]     [Dashboard]
    - Async LLM-     Grafana /
      as-judge on     Platform
      sampled          native
      traces
    - Code evals
      on 100%
          |               |
          +-------+-------+
                  |
                  v
           [Alert Manager]
           |
           +-- Quality: faithfulness < 0.85 for 1h -> PagerDuty
           +-- Cost: per-task token spend > 2x baseline -> Slack
           +-- Latency: P95 > 30s for 15min -> PagerDuty
           +-- Error rate: >5% tool call failures -> PagerDuty
           +-- Loop detection: >10 replanning iterations -> kill + alert
           +-- Drift: 7-day rolling score drops >0.05 -> Slack
```

**Four observability pillars for agents:**

| Pillar | What it captures | Tooling |
|---|---|---|
| **Tracing** | Every span: LLM call, tool call, handoff, state transition, memory read/write | OTel + Langfuse/Phoenix/LangSmith |
| **Metrics** | P50/P95/P99 latency, token usage, cost, throughput, error rates | Prometheus + Grafana |
| **Evaluation** | Quality scores on sampled production traces | LLM-as-judge (async), code evals (sync) |
| **Governance** | PII redaction, access control, audit trail, retention | OTel Collector processors, RBAC |

**Agent-specific observability concerns:**

- **Cognitive readiness**: Beyond Kubernetes liveness/readiness probes, agents need a third dimension -- is the agent capable of making sound decisions right now? (tool availability, context freshness, model responsiveness)
- **Cost as behavioral signal**: Token spend spikes indicate agent stuck in replanning loops, often before quality degrades.
- **Trace cardinality**: A single agent task produces 20+ spans. Plan quotas around agent runs, not individual API calls.
- **Cross-agent correlation**: Trace context propagation ensures complete trace trees across handoffs between agents.

**Technology selection guidance:**

| Requirement | Recommended platform |
|---|---|
| LangChain/LangGraph native, agent IDE | LangSmith |
| Open-source, self-hosted, data residency | Langfuse (MIT) or Phoenix (ELv2) |
| Eval-first workflow, enterprise compliance | Braintrust |
| OTel-native, eval rigor, embeddings analysis | Arize Phoenix |
| Existing Datadog investment | Datadog LLM Observability |
| Regression testing only, not debugging | Braintrust or DeepEval |
| Drop-in proxy, minimal code changes | Helicone |

**Market context**: LLM observability market reached $1.97B in 2025, projected $6.8B by 2029 (36.5% CAGR). 89% of organizations running agents have implemented some form of observability; quality issues are the #1 production barrier at 32%.

---

## Sources

- [1] [Confident AI - LLM Agent Evaluation Complete Guide](https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide) -- Agent eval metrics taxonomy
- [2] [DeepEval - Component-Level LLM Evals](https://deepeval.com/docs/evaluation-component-level-llm-evals) -- Component/integration eval methodology
- [3] [DeepEval - Top 5 LLM Evaluation Frameworks 2026](https://deepeval.com/blog/top-5-llm-evaluation-frameworks) -- Framework comparison
- [4] [LangChain - LLM Evaluation Framework: Trajectories vs Outputs](https://www.langchain.com/resources/llm-evaluation-framework) -- Trajectory evaluation rationale
- [5] [FutureAGI - Agent Evaluation Frameworks 2026](https://futureagi.com/blog/agent-evaluation-frameworks-2026/) -- 6-framework comparison
- [6] [A Survey on LLM-as-a-Judge (arXiv)](https://arxiv.org/html/2411.15594v1) -- Comprehensive LLM judge survey
- [7] [Jatin Bansal - LLM-as-Judge: Pointwise and Pairwise](https://jatinbansal.com/ai-engineering/llm-as-judge/) -- Practical guide to judge paradigms
- [8] [Comet - LLM-as-a-Judge Guide](https://www.comet.com/site/blog/llm-as-a-judge/) -- Production LLM judge patterns
- [9] [G-Eval (EMNLP 2023, arXiv 2303.16634)](https://arxiv.org/abs/2303.16634) -- CoT-based NLG evaluation, 1,771 citations
- [10] [Zheng et al. - Judging LLM-as-a-Judge](https://arxiv.org/html/2306.05685) -- MT-Bench and Chatbot Arena
- [11] [LangSmith Platform](https://www.langchain.com/langsmith-platform) -- Official platform page
- [12] [Analytics Vidhya - LangSmith Evaluation](https://www.analyticsvidhya.com/blog/2025/11/evaluating-llms-with-langsmith/) -- LangSmith evaluation guide
- [13] [Langfuse Documentation](https://langfuse.com/docs) -- Official docs
- [14] [Langfuse - Golden Dataset Evaluation](https://langfuse.com/resources/engineering/golden-dataset-evaluation) -- Golden dataset best practices
- [15] [Langfuse GitHub](https://github.com/langfuse/langfuse) -- 21k+ stars, open source
- [16] [Arize Phoenix GitHub](https://github.com/arize-ai/phoenix) -- 10.2k+ stars, OTel-native
- [17] [Arize Phoenix Documentation](https://arize.com/docs/phoenix/evaluation/llm-evals) -- Eval capabilities
- [18] [Braintrust Platform](https://www.braintrust.dev/) -- Eval-first observability
- [19] [Braintrust Pricing](https://www.braintrust.dev/pricing) -- Free/Pro/Enterprise tiers
- [20] [OpenTelemetry GenAI Observability Blog](https://opentelemetry.io/blog/2026/genai-observability/) -- OTel GenAI conventions
- [21] [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) -- Specification reference
- [22] [Greptime - OTel GenAI Semantic Conventions](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions) -- Agent spans and MCP conventions
- [23] [Datadog OTel GenAI Support](https://www.datadoghq.com/blog/llm-otel-semantic-convention/) -- Enterprise adoption
- [24] [Galtea - Automated LLM Evaluation CI/CD](https://galtea.ai/blog/automated-llm-evaluation-building-a-ci-cd-quality-gate-that-actually-runs) -- Regression gate architecture
- [25] [Traceloop - Automated Prompt Regression Testing](https://www.traceloop.com/blog/automated-prompt-regression-testing-with-llm-as-a-judge-and-ci-cd) -- CI/CD integration
- [26] [Braintrust - Best AI Eval Tools for CI/CD](https://www.braintrust.dev/articles/best-ai-evals-tools-cicd-2025) -- Tool comparison for CI
- [27] [Anthropic - Develop Tests](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests) -- Anthropic eval methodology
- [28] [Anthropic - Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) -- Agent eval guidance
- [29] [Anthropic - Statistical Approach to Model Evals](https://www.anthropic.com/research/statistical-approach-to-model-evals) -- Power analysis for evals
- [30] [OpenAI Evals GitHub](https://github.com/openai/evals) -- 17.6k stars, registry-based
- [31] [OpenAI Evals Deprecation Notice](https://datanorth.ai/blog/evals-openais-framework-for-evaluating-llms) -- Hosted evals closing Nov 2026
- [32] [DeepEval GitHub](https://github.com/confident-ai/deepeval) -- Pytest-native LLM eval
- [33] [RAGAS Framework](https://docs.ragas.io/) -- RAG evaluation metrics
- [34] [Hamel Husain - AI Evals FAQ](https://hamel.dev/blog/posts/evals-faq/) -- Practitioner best practices
- [35] [Shreya Shankar Papers](https://www.sh-reya.com/papers/) -- Research on eval criteria drift
- [36] [O'Reilly Radar - What We've Learned from a Year of Building with LLMs](https://www.oreilly.com/radar/what-we-learned-from-a-year-of-building-with-llms-part-i/) -- Collaborative practitioner guide
- [37] [Arize - LLM Evaluation Costs](https://arize.com/resources/llm-evaluation-costs/) -- Cost breakdown
- [38] [FutureAGI - Eval TCO Calculator](https://futureagi.com/eval-tco-calculator/) -- Cost modeling tool
- [39] [IJC - Safe Observability: Automated PII Redaction](https://ijcjournal.org/InternationalJournalOfComputer/article/view/2458) -- PII in OTel pipelines
- [40] [Confident AI - Enterprise AI Governance Audit Trails](https://www.confident-ai.com/knowledge-base/guides/enterprise-ai-governance-audit-trails) -- Governance framework
- [41] [FutureAGI - LLM Observability Transparency 2025](https://futureagi.com/blog/llm-observability-transparency-2025/) -- CTO playbook
- [42] [MLflow - Sampling Strategies for Distributed Systems](https://mlflow.org/articles/observability-sampling-strategies) -- Trace sampling
- [43] [Code Intel Log - Distributed Observability for LLM Systems](https://codeintel.xyz/blog/llm-observability-architecture/) -- Architecture patterns
- [44] [FutureAGI - Trace Debug Multi-Agent Systems](https://futureagi.com/blog/trace-debug-multi-agent-systems-observability-guide/) -- Multi-agent observability
- [45] [Braintrust - Agent Observability Complete Guide](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026) -- Agent observability patterns
- [46] [MarkTechPost - Top LLM Observability Platforms 2026 Compared](https://www.marktechpost.com/2026/08/09/top-llm-observability-and-evaluation-platforms-in-2026-langfuse-langsmith-braintrust-arize-and-more-compared/) -- Platform comparison
- [47] [Morphllm - LLM Observability Tools 2026](https://www.morphllm.com/llm-observability-tools) -- Free tier comparison
- [48] [Zylos Research - AI Agent Observability Health Monitoring](https://zylos.ai/research/2026-03-07-ai-agent-observability-health-monitoring-diagnostic-patterns/) -- Cognitive readiness patterns
