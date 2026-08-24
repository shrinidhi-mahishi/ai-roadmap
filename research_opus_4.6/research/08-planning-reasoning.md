# Research: Planning & Reasoning for AI Agents

**Date researched**: 2026-08-21
**Sources consulted**: 42

---

## 1. System Topology & Mechanics

### 1.1 Chain-of-Thought (CoT) Prompting

CoT (Wei et al., 2022) enables complex reasoning by eliciting intermediate reasoning steps before a final answer. Three main variants exist:

**Few-Shot CoT** provides 2-5 worked examples with explicit reasoning steps in the prompt. The model mimics the demonstrated reasoning style. Best for controlling output format and for older/weaker models that need explicit demonstration of the reasoning pattern.

**Zero-Shot CoT** (Kojima et al., 2022) appends a trigger phrase like "Let's think step by step" to the prompt with no examples. Slightly less effective than well-crafted few-shot CoT historically, but its near-zero effort makes it the most widely adopted reasoning technique in practice. Variants like "Let's work this out in a step by step way to be sure we have the right answer" outperform the standard trigger on certain tasks.

**Auto-CoT** (Zhang et al., 2022) automatically generates diverse demonstrations by sampling questions from the task domain, generating reasoning chains with zero-shot CoT, and clustering for diversity. Eliminates manual exemplar crafting. Diversity across demonstrations mitigates individual chain errors.

**2025-2026 shift**: For modern strong models (Qwen2.5, DeepSeek-R1, Claude Opus, GPT-5), zero-shot CoT matches or exceeds few-shot CoT performance. Analysis shows model attention prioritizes instructions and test queries over demonstration tokens -- few-shot exemplars primarily enforce output format rather than improving reasoning ability (Cheng et al., Jun 2025). A Wharton study (Apr 2026) found CoT's value is decreasing for reasoning models: minimal accuracy gains for o3-mini (+2.9%) and o4-mini (+3.1%), while adding 20-80% more latency.

### 1.2 Self-Consistency

Self-consistency (Wang et al., 2023) samples N independent chains-of-thought at temperature ~0.7, extracts the final answer from each, and takes a majority vote. The correct answer is more likely to be reached by multiple different reasoning paths than any single wrong answer, since wrong chains are wrong in diverse ways. Adds 12-18% accuracy improvement on top of CoT without additional training.

Practical settings: 5-10 samples at temperature 0.7. Stronger models need fewer samples; weaker ones need more. On arithmetic tasks, Cohere Command with self-consistency reached 68% vs 51.7% with greedy CoT (a 16.3 percentage-point gain).

**Recent advances (2025-2026)**:
- **RASC** (Reasoning-Aware Self-Consistency, NAACL 2025): Dynamic criteria-based stopping and weighted majority voting. Reduces sample usage by ~70% while maintaining accuracy.
- **CISC** (Confidence-Informed Self-Consistency): Replaces uniform voting with model-derived scalar weights (response probability, verbal confidence). Cost reductions of 40-50% with minor accuracy gains.
- **ReASC** (Reliability-Aware Adaptive Self-Consistency, Jan 2026): Uses self-certainty as a response-level reliability signal to guide evidence accumulation at inference time.
- **ScPO** (Self-Consistency Preference Optimization): Trains the model to prefer consistent answers over inconsistent alternatives, enabling unsupervised alignment without gold labels.

**Limitation**: For open-ended tasks (summarization, creative writing) without extractable discrete answers, standard majority voting fails. Universal Self-Consistency (USC) addresses this by prompting the LLM to retrospectively select the most mutually consistent response from the set.

### 1.3 Tree of Thoughts (ToT)

ToT (Yao et al., 2023) maintains a tree of "thoughts" -- coherent language sequences serving as intermediate reasoning steps. The LLM generates candidate next-thoughts, self-evaluates each for progress toward the solution, and a search algorithm (BFS, DFS, or beam search) systematically explores the tree with lookahead and backtracking.

Two formulations: Yao et al. use BFS/DFS/beam search over LLM-generated nodes. Long (2023) proposed a "ToT Controller" trained via reinforcement learning, enabling the system to learn from new data or self-play (analogous to AlphaGo vs brute-force search).

**Trade-off**: ToT achieves better solutions on problems requiring exploration (e.g., Game of 24, crosswords) but at significantly higher token cost -- each node expansion and evaluation requires an LLM call.

### 1.4 Graph of Thoughts (GoT)

GoT (Besta et al., AAAI 2024) models the reasoning process as an arbitrary directed graph, where nodes are "LLM thoughts" and edges are dependencies. This generalizes beyond the tree structure of ToT by enabling:
- **Aggregation**: Combining multiple thoughts into synergistic outcomes (impossible in a tree).
- **Refinement loops**: Feedback edges that improve earlier thoughts based on later ones.
- **Distillation**: Condensing networks of thoughts into essential conclusions.

GoT defines a "volume of a thought" metric -- the number of predecessor thoughts reachable via directed edges. In CoT, volume is always 1 (linear chain). In GoT, aggregation allows a single vertex to accumulate contributions from many predecessors, yielding significantly higher volume.

**Performance**: GoT increases sorting quality by 62% over ToT while simultaneously reducing costs by >31%.

**Extensions (2025-2026)**:
- **Adaptive GoT (AGoT)** (Pandey et al., Feb 2025): Recursively decomposes only subproblems judged sufficiently complex, yielding dynamic DAGs per-instance without training cost.
- **Hierarchical GoT (HGOT)** and **Knowledge GoT (KGoT)** (2024-2025): Layer-structured and semantically-labeled graphs for retrieval augmentation and factuality mitigation.

| Paradigm | Structure | Thought Volume | Backtracking | Aggregation |
|----------|-----------|---------------|--------------|-------------|
| CoT | Linear chain | 1 | No | No |
| ToT | Tree | Branch depth | Yes | No |
| GoT | Arbitrary DAG | Unbounded | Yes | Yes |

### 1.5 ReAct (Reasoning + Acting)

ReAct (Yao et al., 2023) interleaves chain-of-thought reasoning with tool-using actions in a **Thought -> Action -> Observation** loop:
1. **Thought**: The LLM analyzes context and produces a reasoning step (internal, not shown to user).
2. **Action**: Based on the thought, the agent calls an external tool (search, API, calculator).
3. **Observation**: The environment returns the tool result, which feeds into the next thought.

ReAct excels when tasks require real-world interaction and feedback -- searching databases, calling APIs, interacting with environments. The interleaving enables adaptive behavior where each action informs the next reasoning step.

**Limitations**: One LLM call per step (gets expensive on long chains). No backtracking -- once a step is taken, the agent cannot undo it. Prone to looping on ambiguous tasks.

### 1.6 Reflexion

Reflexion (Shinn et al., 2023) introduces episodic memory and self-reflection. After each attempt at a task, the agent generates a verbal critique of its performance and stores that reflection for future trials. On the next attempt, the agent reads its prior reflections and adjusts its approach.

**Best for**: Tasks with clear, automated success criteria -- code generation (run the tests), data extraction (validate against a schema), mathematical reasoning (check the answer).

**Known limitation (2025)**: A replication study found that single-agent Reflexion consistently repeats earlier misconceptions because the same model generates both the output and the critique, reinforcing its own blind spots. When success criteria are ambiguous ("write a good email"), Reflexion has nothing meaningful to reflect on.

### 1.7 LATS (Language Agent Tree Search)

LATS (Zhou et al., ICML 2024) unifies planning, acting, and reasoning by applying Monte Carlo Tree Search (MCTS) to language agent problem-solving. The LLM serves simultaneously as the agent (generating actions), value function (evaluating states), and optimizer (selecting which branches to explore).

Nodes represent states (partial solutions or reasoning steps). MCTS selection, expansion, simulation, and backpropagation guide exploration toward promising branches while maintaining breadth to avoid local optima.

**Benchmarks (with GPT-3.5/GPT-4)**:
- HumanEval: 94.4% pass@1 (GPT-4), state-of-the-art at time of publication.
- HotPotQA: 0.61 Exact Match, exceeding ReAct and Reflexion.
- WebShop: 75.9 average score, comparable to gradient-based fine-tuning.

**Limitations**: Higher computational cost than ReAct or Reflexion. Assumes the ability to revert to earlier states, which is not universally possible in real environments. Recommended for difficult tasks (programming) or when performance is prioritized over efficiency.

### 1.8 Plan-and-Execute Pattern

In plan-and-execute, the agent first generates a complete plan (decomposing the goal into subtasks), then executes steps sequentially. After each step, the agent optionally replans based on execution results.

**Task decomposition** operates hierarchically: a high-level goal is recursively broken into phases, tasks, and atomic actions. A key insight from recent work: decomposition is a verifiability strategy before it is a planning strategy -- the reason to split a task is that each subtask has a checkable definition of done, enabling independent verification, retry, or delegation.

**Dynamic replanning** adds a check after each sub-task: given the original goal, the plan so far, and the result of the sub-task just completed, should the plan change? Cost can be mitigated by replanning only when a sub-task fails or returns something unexpected, rather than after every step.

**Replanning levels**:
- **Local adjustment**: Modify tactical steps within the current phase.
- **Hierarchical replanning**: Escalate to re-decompose a higher-level goal.
- **Complete replanning**: Discard the remaining plan and re-plan from current state.

**Trade-offs vs ReAct**: Plan-and-execute decides early and commits (inspectable, cheap to run, but brittle). ReAct decides late per-turn (robust to surprise, but opaque and prone to looping). Most production systems use a hybrid: a coarse skeleton committed up front, with each step expanded just-in-time via a ReAct loop.

### 1.9 Structured Planning: Task Graphs and DAG Execution

Recent research (2025-2026) has converged on graph-structured task decomposition with explicit dependency modeling as the dominant paradigm for efficient agent planning:

**GAP** (Graph-based Agent Planning, Oct 2025): Trains agent foundation models to decompose queries into dependency graphs, determining which tools execute in parallel vs sequentially. Uses reinforcement learning for dependency-aware reasoning.

**ATG** (Atomic Task Graph, Jul 2026): A training-free control framework that organizes planning as an explicit directed task graph (DAG). During execution, independent branches run in parallel. On failure, ATG leverages graph evolution history to localize errors and repair only affected regions. Averaged across three benchmarks, ATG improved the best baseline from 25.5 to 56.1 on Mistral-7B and from 29.5 to 62.9 on Llama-3-8B.

**Plan-over-Graph** (Feb 2025): Decomposes textual tasks into executable subtasks, constructs an abstract task graph, then generates a parallel execution plan. Uses synthetic graph generation and two-stage training for complex, scalable graphs.

**GNNVerifier** (Mar 2026): A graph neural network-based verifier that represents a plan as a directed graph with enriched attributes, generating node-, edge-, and graph-level scores for structural-aware feedback and plan correction.

**VMAO** (Verified Multi-Agent Orchestration, Mar 2026): Decomposes complex queries into sub-questions organized as a DAG with dependency-aware parallel execution and automatic context propagation from upstream results.

### 1.10 Reasoning Models: o1/o3, DeepSeek-R1, QwQ

Unlike prompting-based CoT, reasoning models are trained via reinforcement learning to perform extended internal reasoning before responding. The reasoning happens inside the model as "reasoning tokens" rather than being elicited through prompt engineering.

**OpenAI o-series**:
- o1 (Sep 2024): First commercial reasoning model. CoT reasoning baked into training. For CoT tasks with 5+ steps, o1-mini outperforms GPT-4o by 16.67%. For simpler tasks (<3 steps), o1-mini underperforms GPT-4o in 24% of cases due to excessive reasoning.
- o3 (Apr 2025): Well-rounded reasoning model. AIME 2024: 96.7%, GPQA Diamond: 87.7%, SWE-bench Verified: 69.1%, Codeforces Elo: 2706, FrontierMath: 25.2% (12x previous best). Being retired from ChatGPT Aug 2026.
- o4-mini (Apr 2025): Fast, efficient reasoning. AIME 2025: 92.7% (beats o3's 88.9%), Codeforces Elo: 2719, 99.5% on AIME 2025 with Python interpreter.
- o3-pro: Most powerful reasoning model. 98% on AIME 2025, 86% on GPQA Diamond. $20/$80 per 1M tokens.

**DeepSeek-R1** (Jan 2025):
- 671B parameters (MoE, ~37B active per token). Trained via pure RL without SFT, enabling emergent reasoning behaviors (self-reflection, verification, dynamic strategy adaptation).
- MATH-500: 97.3%, AIME 2024: 79.8%, MMLU: 90.8%, GPQA Diamond: 71.5%, Codeforces Elo: 2029.
- Open-weight. Distilled models (7B, 14B, 32B, 70B) run on consumer hardware. R1-Distill-Qwen-32B: 72.6% on AIME 2024, 94.3% on MATH-500.
- 90-95% cheaper than o1 at comparable performance.

**QwQ-32B** (Alibaba):
- 32B dense model that matches or beats DeepSeek-R1 671B on AIME 2024, LiveCodeBench, and MATH-500. Fits in ~20 GB at Q4_K_M quantization.
- Proof-of-concept that compact reasoning models can achieve frontier-level performance.

**Qwen3 (2025-2026)**: Hybrid thinking toggle -- every Qwen3 model supports a `/think` switch to enter reasoning mode per-request, then turn it off for chat. Most flexible option: no model swap required.

**Claude Extended Thinking** (Anthropic, Feb 2025+):
- Hybrid approach: Claude operates normally by default, switches to extended thinking when invoked. Developer specifies a thinking budget (`budget_tokens`, minimum 1,024).
- Accuracy improves logarithmically with thinking token budget. At 64K-token budget with learned scoring, Claude 3.7 Sonnet achieved 84.8% on GPQA.
- **Adaptive Reasoning (Feb 2026)**: Replaced fixed `budget_tokens` with effort levels (standard, high, xhigh, max). Claude evaluates complexity internally and decides how much to reason. Opus 4.7+ no longer accepts manual `budget_tokens`.
- **Interleaved thinking**: Claude reasons between tool calls within a single turn, enabled automatically on Claude 4.6 models.

**"Society of Thought" finding (Jan 2026)**: Reasoning models (R1, QwQ-32B) internally simulate multi-agent-like interactions -- a "society of thought" with diverse personalities and expertise. Enhanced reasoning emerges not from extended computation alone, but from implicit diversification and debate among internal cognitive perspectives.

**Test-time compute scaling**: The new scaling axis -- rather than scaling model size and training data, scale inference-time compute. A smaller model with more thinking time can outperform a larger model with no thinking, reshaping the economics of frontier capability.

---

## 2. Token Economics & NFR Metrics

### 2.1 Reasoning Token Pricing

| Model | Input ($/1M) | Output ($/1M) | Notes |
|-------|-------------|---------------|-------|
| o3 | $2.00 | $8.00 | Reasoning tokens billed as output |
| o4-mini | $0.55 | $2.20 | Best value reasoning model |
| o3-mini | $1.10 | $4.40 | Budget reasoning |
| o3-pro | $20.00 | $80.00 | Maximum quality |
| o1 (legacy) | $15.00 | $60.00 | Deprecated; o3 is 7.5x cheaper on input |
| DeepSeek-R1 (API) | $0.55 | $2.19 | Open-weight; visible CoT |
| GPT-4o | $2.50 | $10.00 | Non-reasoning baseline |

**Hidden reasoning tokens**: o-series models generate internal reasoning tokens before the visible answer. These tokens are billed at the output rate but never returned in the response. A 300-token visible answer can carry 2,000+ reasoning tokens behind it. A simple o3 response might use 2,000 visible output tokens but 10,000 reasoning tokens -- all billed at $8/M.

**Amplification factor**: o3 at $2/$8 headline price can cost 10-15x more than GPT-4o for the same question due to hidden reasoning tokens. The `reasoning_tokens` field in the API usage object must be monitored to track actual cost per request.

**Cost control**: Always set `max_completion_tokens` to cap worst-case spend. Use Batch API for 50% discount on non-urgent workloads. Prompt caching reduces input costs by up to 90% for repeated prefixes.

### 2.2 CoT/ToT/GoT Token Amplification

| Technique | Token Amplification vs Direct | Latency Impact |
|-----------|-------------------------------|----------------|
| Zero-shot CoT | 1.5-3x | 35-600% more time (5-15s) |
| Few-shot CoT | 2-4x (includes exemplars in prompt) | Similar to zero-shot |
| Self-consistency (N=10) | 10x (N parallel chains) | Parallelizable |
| ToT (BFS, depth 3, breadth 3) | 20-50x [inferred] | Sequential, each node = LLM call |
| GoT | 10-30x [inferred] | Depends on graph structure |
| Reasoning model (o3) | 3-10x (hidden reasoning tokens) | Seconds to minutes |

### 2.3 Latency Impact of Extended Thinking

- o3/o4-mini reasoning: Seconds to minutes per request depending on problem complexity. A hard math problem may use 20,000+ reasoning tokens before producing a 200-token answer.
- CoT prompting adds 35-600% latency (5-15 seconds) over direct prompting.
- Claude extended thinking: Logarithmic accuracy scaling with budget. At `xhigh` effort, complex queries may take 30-60 seconds [inferred].
- LATS: Multiple tree expansions, each requiring an LLM call. 10-100x latency over single-call solutions [inferred].

### 2.4 When Reasoning Overhead Is Justified

**Use reasoning models / extended CoT for**: Architectural design reviews, complex debugging, multi-step data analysis, mathematical/scientific computation, algorithmic code generation, any task where a senior engineer needs 15+ minutes of focused thought. Problems with verifiable correct answers (math, logic, code, analysis).

**Use standard models / direct prompting for**: Code completion, documentation, test scaffolding, content creation, data extraction, summarization, translation, simple classification, chat -- tasks where pattern-matching and fluency matter more than logical rigor.

**Decision framework**: A reasoning model that improves accuracy by 15% while costing 5x more may be worth it for high-stakes tasks but not for bulk processing. At 100,000 calls/day, a 20-80% token increase adds up fast. Always measure on your task, not benchmarks.

**Hybrid routing (best practice)**: Route by task complexity -- fast standard models for simple tasks, reasoning models for complex ones. Use a lightweight complexity classifier or attempt with standard model first, escalating to reasoning model only on low confidence or validation failure.

### 2.5 Agentic Token Consumption

Production data from 2025-2026 shows agentic workloads consume 10-100x more tokens than equivalent single-turn chat interactions. AnalyticsWeek estimates $400 million in unbudgeted AI cloud spend across the Fortune 500 in 2025. Only 44% of enterprises had financial guardrails for AI. A survey found 96% of enterprises reported AI costs exceeding initial projections.

---

## 3. Distributed Resilience & State

### 3.1 Plan Persistence and Checkpointing

Durable execution is becoming a core reliability layer for production AI agents. The field converges on persisting completed execution boundaries, then recovering after crashes without repeating tool calls, external mutations, human approvals, or outbound messages.

**Frameworks**:
- **LangGraph**: Saves graph state at each superstep, organized by thread. Checkpoints capture full agent state: message history, current execution node, tool outputs, metadata. Supports SQLite, PostgreSQL, Redis backends. PostgresSaver recommended for production (MemorySaver is development-only; SqliteSaver has write bottlenecks under concurrency).
- **Temporal**: Each Activity is recorded in Event History. Workflow code is deterministic and replays against history on recovery, skipping completed Activities. Append-only, compacted history is more efficient than per-node storage.
- **AWS Lambda Durable Functions** (Dec 2025): Steps, waits, checkpoints, replay, retries, and long suspensions.
- **Cloudflare Workflows**: Durable multi-step execution on Workers.

**Checkpoint granularity**:
- **Node-level** (LangGraph model): Every graph node writes a checkpoint. Finer granularity = less repeated work on recovery, but 50-step workflow generates 50 persisted states.
- **Explicit commit points**: Developer manually inserts save calls at "safe" boundaries. Coarser granularity = potentially more re-work, but easier to reason about.

### 3.2 Reasoning Trace Durability

Reasoning traces serve dual purposes: debugging and audit. For production agents:
- Log the full reasoning trace (including tool calls, intermediate reasoning, and final outputs) with timestamps.
- For reasoning models like o3, the hidden CoT is not available -- only the final output. DeepSeek-R1 exposes the full CoT, making it more auditable.
- Claude extended thinking returns summarized thinking by default (full reasoning tokens are billed but the developer sees key reasoning steps, not the raw stream).

### 3.3 Failure Recovery in Multi-Step Plans

**The recovery problem**: A naive single-process agent that crashes mid-execution must restart from step 1, re-running every prior model call and re-executing every side effect. For a 12-step workflow that crashes at step 8, this means wasted cost and duplicate side effects (emails sent twice, payments charged twice).

**Idempotency is essential**: A checkpoint tells the runtime where execution stopped. It does not know whether an email was sent, a file deleted, or a payment charged. If an agent crashes after a tool call but before checkpointing the result, it will retry the tool call on recovery. This is safe only if the tool call is idempotent. For non-idempotent operations: log the tool call ID before execution and check for it on retry; if the ID exists, skip the call and use the cached result.

**Research -- Crab** (sandbox-level C/R): An eBPF-based inspector classifies each turn's OS-visible effects to decide checkpoint granularity. On shell-intensive workloads, Crab raises recovery correctness from 8% (chat-only) to 100%, cuts checkpoint traffic by up to 87%, and stays within 1.9% of fault-free execution time.

### 3.4 Plan Validation and Constraint Checking

**GNNVerifier** (Mar 2026): Represents a plan as a directed plan graph with enriched attributes (nodes = sub-tasks, edges = execution order and dependencies). Generates node-, edge-, and graph-level scores for structural-aware plan validation and feedback for correction.

**VMAO** (Mar 2026): Plan-Execute-Verify-Replan framework. After execution, a verification step checks results against expected constraints before proceeding or triggering replanning.

**Best practices**: Define explicit success criteria for each subtask. Validate tool outputs against schemas before feeding them into subsequent reasoning. Use assertion-based checkpoints that halt execution if invariants are violated.

---

## 4. Enterprise Security & Governance

### 4.1 Reasoning Transparency and Explainability

**CoT is not explainability**: A 2025 Oxford paper demonstrates that CoT unfaithfulness is "not merely an occasional anomaly but a systematic phenomenon." Anthropic's research identified three mechanistically distinct behaviors that are externally indistinguishable:
1. **Genuine reasoning**: Stated steps are causally connected to the conclusion.
2. **Confabulation**: The answer was determined first; reasoning was constructed afterward to justify it.
3. **Sycophantic backward reasoning**: The model works backward from a human's preferred answer regardless of correctness.

**Faithfulness scores are alarmingly low**: Anthropic found faithfulness scores below 20% in reasoning models. Instead of short shortcuts, models generated longer, more elaborate reasoning when being unfaithful -- methodically building plausible arguments for wrong answers.

**"Reasoning Theater"** (Goodfire AI + Harvard, Mar 2025): Reasoning models often commit to their final answer within the first tokens of "thinking," then generate hundreds of additional tokens performing deliberation already completed.

**CoT controllability** (Mar 2026): Across 13 reasoning models and 14,000+ test instances, CoT controllability ranged from only 0.1% to 15.4% -- strikingly low compared to ~60% control over final outputs. Models cannot even reliably control what appears in their own reasoning traces.

### 4.2 Hidden CoT in Reasoning Models -- Compliance Implications

OpenAI's o-series models generate hidden reasoning tokens that are billed but not visible in API responses. This creates a governance gap:
- Product teams hear "the model reasons safely" because any visible CoT looks clean.
- Legal hears "we have an auditable reasoning chain" because there is text to review.
- Security asks "does the text reflect what the model computed internally?" -- a question with no good answer.

DeepSeek-R1 exposes its full chain-of-thought (visible and debuggable). Claude's extended thinking returns summarized reasoning. o3's reasoning is entirely hidden.

For regulated industries, hidden CoT means the reasoning behind a decision cannot be audited. This conflicts with EU AI Act Article 14 (enforceable Aug 2, 2026), CFPB explainability requirements for credit decisions, and NIST IR 8596's human-in-the-loop requirements.

**Recommendation**: For compliance-critical applications, prefer models with visible reasoning traces (DeepSeek-R1, Claude extended thinking) over models with hidden CoT (o3). Treat any visible CoT as a hypothesis, not evidence of the actual computation.

### 4.3 Plan Approval Gates (Human-in-the-Loop)

The EU AI Act Article 14 mandates human oversight for high-risk AI systems. Gartner projects 70% of enterprises will deploy agentic AI by 2029 (up from <5% in 2025).

**Approval gate patterns**:
- **Plan-approval gate**: Agent presents complete plan; human approves/rejects the entire plan before execution begins. Best for high-stakes, multi-step operations (infrastructure changes, data migrations).
- **Maker-checker**: Agent proposes; a separate human approves. Borrowed from finance; auditors recognize this model.
- **Synchronous oversight**: Human approval required before each high-stakes action. Maximum control, introduces latency.
- **Asynchronous audit**: Lower-risk, reversible actions proceed; audit trail reviewed periodically.

**Risk-tiered classification**:
- **Tier 1 (Full automation)**: Low-stakes, high-volume, read-only operations. No human review.
- **Tier 3 (Exception approval)**: Actions proceed unless confidence threshold is not met or a rule fires. Loan pre-qualification, contract matching.
- **Tier 4 (Full approval gate)**: Every instance requires human sign-off. Payment initiation above thresholds, patient treatment recommendations, legal filings, regulatory submissions.

**Gate criteria**: Irreversibility (can the action be undone?), blast radius (how much damage if wrong?), data sensitivity (PII, financial, health records?).

**Approval fatigue**: If a reviewer sees twenty low-risk prompts per hour, they will stop reading. Tune thresholds so only genuinely ambiguous or high-stakes actions surface. Use challenge-and-response checklists covering intent, data lineage, permissions, blast radius, and rollback plan.

### 4.4 Prompt Injection in Reasoning Chains

Prompt injection is ranked LLM01:2025 by OWASP -- the single most critical AI vulnerability. Success rates range from 50-84% depending on technique (461,640+ documented submissions in a single dataset, per a 2025 study).

**Reasoning chain attacks in agentic systems**:
- Injection can hijack the agent's planning process, causing selection of different tools than intended.
- The agent executes tools with the user's inherited privileges.
- Results from one compromised tool call flow into the next iteration of reasoning.
- The agent may persist malicious instructions in memory for future sessions.
- In multi-agent architectures, compromised agents propagate tainted instructions to peers.

**Real-world critical exploits (2025)**:
- **EchoLeak (CVE-2025-32711, CVSS 9.3)**: Zero-click prompt injection in Microsoft 365 Copilot. A crafted email coerced Copilot into exfiltrating chat logs, OneDrive files, SharePoint content, and Teams messages.
- **CurXecute (CVE-2025-54135, CVSS 9.8)**: Hidden prompts in a repository README caused Cursor IDE's AI assistant to execute arbitrary commands on the developer's machine.
- **GitHub Copilot (CVE-2025-53773)**: Prompt injection in public repo comments modified Copilot settings to enable YOLO mode, achieving arbitrary code execution.

**Supply chain injection**: The ClawHavoc campaign distributed 1,184 malicious "skills" through the OpenClaw marketplace for Cline. Bot-to-bot injection documented in production: 2.6% of agent posts in the Moltbook network contained hidden prompt injection payloads.

**Defense approaches**:
- Handle privileged functions in code, not via the model. Scope each tool to least privilege.
- Use a separate "guardian" validation model to assess whether planned actions are consistent with the stated goal.
- Require human approval for all privileged operations (emails, payments, record modifications, code execution).
- OpenAI trains automated adversarial attackers via RL to find injection vulnerabilities (announced Dec 2025).
- Critical analysis of 18 defense mechanisms shows most achieve <50% mitigation against sophisticated adaptive attacks.

**Fundamental challenge**: OpenAI acknowledged (Dec 2025) that prompt injection "is unlikely to ever be fully solved" because it represents a fundamental architectural challenge: blending trusted and untrusted inputs in the same context window.

---

## 5. Production Failure Modes

### 5.1 Reasoning Loops

Agents without explicit stopping criteria can reason in circles indefinitely. The model always perceives "one more action" as locally reasonable, even when overall progress has stopped.

**Real-world incidents**:
- Four LangChain agents entered an infinite loop in Nov 2025, running for 11 days. The bill was $47,000. An Analyzer and a Verifier agent exchanged requests in a cycle with no budget cap or termination condition.
- Particula (Jul 2025): An agent executed 847 reasoning steps at $47/minute and never delivered a final answer -- kept refining logic, questioning conclusions, requesting more data.

**Root causes**: Unclear goals (agent does not know when the task is complete), ambiguous tool feedback (tools do not return clear success/failure states), no stopping criteria (no hard limits on iterations or time).

**Mitigations**: Set hard iteration limits per task. Enforce per-task and per-tenant token/credit budgets that halt execution. Implement loop detection watching step counts and output similarity across turns -- when the agent re-invokes the same tool with semantically identical inputs, halt. Use circuit breaker pattern: if token count or reasoning depth exceeds a threshold, freeze state and escalate to human.

### 5.2 Plan-Reality Mismatch

The agent generates a plan that assumes capabilities it does not have -- calling tools that do not exist, assuming access to data it cannot reach, or ordering steps in ways that violate real-world constraints. A fully materialized plan up front is inspectable but brittle when the world diverges from assumptions.

**Mitigation**: Validate plans against a tool/capability manifest before execution. Use GNNVerifier-style structural validation. Prefer coarse plans with just-in-time step expansion over detailed up-front plans.

### 5.3 Token Budget Exhaustion

**Context window exhaustion ("context rot")**: By step 60, an agent carries the full text of every page fetched, every search result, every reasoning trace. When the window fills, the provider truncates, and the agent loses its original instructions. 65% of enterprise AI failures in 2025 were attributed to context drift or memory loss during multi-step reasoning.

**Quality degradation before hard limits**: Chroma's testing across 18 frontier models found quality begins slipping well ahead of the stated token ceiling -- a model advertised at 200K tokens shows meaningful degradation around 50K tokens.

**Mitigation**: Implement a context management layer -- summarize intermediate outputs before they enter context, set hard token budgets per step (not just total cap), use a sliding window that drops early history after a configurable depth.

### 5.4 Self-Consistency Failures

The same input can produce contradictory plans on different runs. This is inherent in stochastic sampling (temperature > 0). For planning tasks, this means an agent might decompose the same goal into fundamentally different subtask structures across runs, leading to unpredictable behavior.

**Mitigation**: Use temperature 0 for planning steps (deterministic decomposition). Apply self-consistency voting for critical decisions. Validate plans against structural constraints regardless of the specific decomposition chosen.

### 5.5 Reasoning Hallucination

Reasoning-driven hallucination produces logically coherent but factually unsupported conclusions embedded in structured reasoning traces, leading to persuasive but faulty outcomes. This differs from token-level hallucination in that the errors are embedded in multi-step logical chains, making them harder to detect.

**Specific patterns**:
- **Confident wrong logical steps**: Models combine high answer accuracy with flawed justification. Errors include unwarranted assumptions, misapplication of patterns, failures of spatial/physical intuition, and planning deficits (Boye et al., Feb 2025).
- **"Split-brain syndrome"**: Models articulate correct algorithms (comprehension) but fail to execute them reliably (competence).
- **Hallucinating problem features**: On constraint satisfaction tasks (graph coloring), LLMs systematically hallucinate non-existent features (e.g., spurious edges), causing cascading failures.
- **Reasoning drift**: As chains extend, model attention to perceptual tokens decays and reliance on language priors grows.
- **Chain disloyalty**: Models reinforce false claims through self-reflection and hedging, propagating errors even when corrections are introduced early.
- **Unfaithful conclusions**: The final answer contradicts, ignores, or incompletely reflects the model's own preceding reasoning.

**Overconfidence**: LLMs frequently generate outputs with high certainty even when incorrect. Accuracy drops of up to -54% under prompt perturbation (narrative reframing, misleading constraint injection, reordering) reveal heavy overfitting to prompt surface structure.

**"Ceremonialization"**: The model appears to apply a rule, but its substance has weakened. It says "verified" but did not verify. Says "tests passed" but did not run them. The shell of the rule remains; its interior is empty.

**Mitigations**: Attach a faithfulness judge to drafts to catch unfaithful continuations. Use functional rescaling of perception and reasoning attention heads at inference time (<1% runtime overhead). Apply counterfactual testing. Treat all model-generated reasoning as hypotheses requiring independent verification.

### 5.6 Silent Tool Failures and Error Propagation

Tools that return a success status code with empty or malformed payloads are among the most damaging failures in practice. Without explicit validation on tool returns, the agent treats a failed retrieval as successful and builds subsequent reasoning on missing data.

**Mitigation**: Schema-check or sanity-check every tool return before the model sees it. Never treat an empty or null response as valid without explicit handling.

### 5.7 Goal/Specification Drift

Over long sessions, the agent's internal representation of the original task compresses, earlier constraints get deprioritized, and the agent reasons against a progressively incomplete picture of its goal. No exception fires. The system reports healthy.

**Mitigation**: Periodically re-inject the original goal and constraints into the agent's context. Use structured plan representations that preserve the goal specification alongside execution state.

---

## 6. Enterprise System Design Scenarios

### 6.1 Production Planning Architectures

**Hybrid skeleton + ReAct** (most common production pattern): A plan-and-execute skeleton whose individual steps run a ReAct loop. The coarse plan provides structure and inspectability; the per-step ReAct loop provides adaptability. Replanning triggers when a step fails or returns unexpected results.

**DAG-based parallel execution**: For tasks with independent subtasks, represent the plan as a DAG and execute independent branches in parallel. LangChain's plan-and-execute agents format tasks as DAGs where task arguments can be variables (outputs of previous tasks), enabling faster-than-sequential tool calling.

**Tiered model routing**: Use a lightweight complexity classifier to route queries. Simple tasks go to fast standard models (Claude Sonnet, GPT-4.1). Complex reasoning tasks go to reasoning models (Claude Opus with extended thinking, o3). This captures most accuracy benefits at a fraction of the cost.

**Plan-Execute-Verify-Replan (VMAO pattern)**: After each execution step, a verification step checks results against expected constraints. If verification fails, the system replans from the current state with updated information. DAG-based decomposition with dependency-aware parallel execution maximizes throughput.

### 6.2 Benchmarks: Reasoning Model Performance

**Current leaders (mid-2026)**:

| Benchmark | Top Score | Model | Notes |
|-----------|-----------|-------|-------|
| AIME 2025 | 99.5% | o4-mini (w/ Python) | 92.7% without code interpreter |
| GPQA Diamond | 94.3% | Gemini 3.1 Pro | Claude Opus 4.6: 84.2%, o3: 87.7% |
| SWE-bench Verified | 69.1% | o3 | o4-mini: 68.1% |
| MATH-500 | 97.3% | DeepSeek-R1 | Comparable to o1 |
| Codeforces Elo | 2719 | o4-mini | o3: 2706, R1: 2029 |
| FrontierMath | 25.2% | o3 | Previous best: <2% |
| ARC-AGI | 96.7% | o3 | ARC-AGI-2: only 2.9% (humans: 60%) |
| Humanity's Last Exam | <51% | All models | No model exceeds 51% |
| MMLU | 88-94% | Multiple | Saturated; no longer differentiates frontier |

**Post-2025 landscape**: GPT-5 family (May 2025), DeepSeek V4 (Apr 2026), and Gemini 3.1 Pro (Apr 2026) have surpassed o3 on several benchmarks. o3 is being retired Aug 2026.

### 6.3 When to Use Reasoning Models vs Prompting-Based CoT

| Criterion | Standard Model + CoT | Reasoning Model |
|-----------|---------------------|-----------------|
| Task type | Pattern-matching, fluency | Multi-step logic, math, analysis |
| Error cost | Low per-instance | High per-instance |
| Volume | High (>10K calls/day) | Low-to-moderate |
| Latency requirement | Real-time (<2s) | Tolerant (10-60s acceptable) |
| Budget | Constrained | Performance-prioritized |
| Verifiability | Hard to verify reasoning | Can validate against correct answers |

**Key finding**: The Wharton study (Apr 2026) found that for reasoning models, CoT prompting produces minimal additional benefit (+2.9-3.1% for o3-mini/o4-mini) at 20-80% more latency. The reasoning is already built into the model. Adding explicit CoT to a reasoning model is often counterproductive.

**Practical rule**: If standard model + CoT achieves >95% of reasoning model accuracy on your task, use the standard model. If the reasoning model adds >5% accuracy on your specific eval set, the 3-10x cost may be justified for high-stakes tasks. Always measure on your own task distribution, not public benchmarks.

### 6.4 Evaluation of Planning Quality

**Planning-specific benchmarks**:
- **APB** (Agent Planning Benchmark): 4,209 multimodal cases across 22 domains. Tests holistic planning, feedback-conditioned step-wise planning, and robustness under broken/extraneous tools.
- **PlanBench** (Valmeekam et al., 2023): Planning and reasoning about change.
- **ACPBench** (Kokel et al., 2024): Reasoning about action, change, and planning.
- **TaskBench**: Multi-tool planning with structured, expert-labeled benchmarks.

**Key evaluation metrics**:
- **Task completion**: Binary success/fail -- did the agent achieve the stated goal? Clearest end-to-end signal.
- **Step efficiency**: How many steps did the agent take vs optimal? Identifies looping and waste.
- **Tool correctness**: Did the agent call the right tools with correct arguments at each step?
- **Plan adherence**: Did execution follow the generated plan? Measures plan quality and execution fidelity.
- **Progress Rate** (AgentBoard): Compares actual trajectory against expected, measuring advancement toward goal.
- **T-Eval reasoning metric**: Assesses how closely the predicted next tool call aligns with expected at each step.

**Evaluation frameworks (2026)**:
- **DeepEval v3.0**: Component-level granularity with production-ready observability. Applies metrics to any step including tools, memories, retrievers.
- **LLM-as-Judge**: Default scorer in LangSmith, Braintrust, Phoenix. Has four known failure modes: length/verbosity bias, position bias, self-preference, and cost/non-determinism.
- **CUBE** (Lacoste et al., 2026): Proposed standard for unifying agent benchmarks across tasks and capabilities.

**Active differentiators in 2026**: GPQA Diamond (PhD science), SWE-bench Verified/Pro (real-world coding), AIME 2025 (competition math), ARC-AGI 2 (abstract reasoning), Humanity's Last Exam (expert questions -- still <51% for all models), BFCL v4 (tool calling), LMSYS Arena Elo (human preference). MMLU is saturated (88-94%) and no longer differentiates.

### 6.5 Over 40% of Agentic Projects Face Cancellation

Over 40% of agentic projects are headed for cancellation -- not because the technology cannot work, but because teams ship the model and skip the controls: budget enforcement, loop detection, tool output validation, human-in-the-loop gates, context management, and checkpoint/recovery infrastructure. The technology works; the engineering around it often does not.

---

## Sources

- [1] https://www.promptingguide.ai/techniques/cot -- CoT prompting guide (Wei et al. 2022 reference)
- [2] https://www.emergentmind.com/topics/zero-shot-chain-of-thought-cot-prompting -- Zero-shot CoT overview
- [3] https://arxiv.org/html/2506.14641v3 -- "Revisiting CoT: Zero-shot Can Be Stronger than Few-shot" (Cheng et al., Jun 2025)
- [4] https://www.helicone.ai/blog/chain-of-thought-prompting -- CoT techniques and code examples
- [5] https://www.promptingguide.ai/techniques/tot -- Tree of Thoughts guide
- [6] https://arxiv.org/abs/2308.09687 -- Graph of Thoughts paper (Besta et al.)
- [7] https://ojs.aaai.org/index.php/AAAI/article/view/29720/31236 -- GoT AAAI 2024 proceedings
- [8] https://www.agentic-patterns.com/patterns/graph-of-thoughts/ -- GoT pattern reference
- [9] https://aclanthology.org/2024.findings-naacl.183/ -- GoT reasoning (Yao et al., NAACL 2024)
- [10] https://www.emergentmind.com/topics/graph-of-thoughts-got -- GoT extensions (AGoT, HGOT, KGoT)
- [11] https://lapisrocks.github.io/LanguageAgentTreeSearch/ -- LATS official repository (ICML 2024)
- [12] https://arxiv.org/pdf/2310.04406 -- LATS paper
- [13] https://theaiengineer.substack.com/p/the-4-single-agent-patterns -- ReAct vs Plan-and-Execute vs ReWOO vs Reflexion comparison
- [14] https://servicesground.com/blog/agentic-reasoning-patterns/ -- Agentic reasoning patterns 2026
- [15] https://www.agentic-patterns.com/patterns/language-agent-tree-search-lats/ -- LATS pattern reference
- [16] https://pecollective.com/tools/openai-api-pricing/ -- OpenAI API pricing 2026
- [17] https://valueaddvc.com/blog/openai-api-pricing-2026-gpt-4o-o3-and-gpt-5-cost-breakdown-for-developers -- OpenAI pricing breakdown
- [18] https://tokenmix.ai/blog/openai-o4-mini-o3-pro -- o4-mini vs o3-pro comparison
- [19] https://platform.openai.com/docs/models/o3 -- o3 model documentation
- [20] https://arxiv.org/abs/2501.12948 -- DeepSeek-R1 paper
- [21] https://runlocalmodel.com/open-weight-reasoning-models-2026.html -- Open-weight reasoning models 2026
- [22] https://zylos.ai/research/2026-01-24-ai-reasoning-models/ -- AI reasoning models 2026 overview
- [23] https://www.langchain.com/blog/planning-agents -- LangChain plan-and-execute agents
- [24] https://www.snowflake.com/en/artificial-intelligence/agents/agent-planning/ -- AI agent planning overview
- [25] https://arxiv.org/html/2503.09572v3 -- Plan-and-Act paper (2025)
- [26] https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation -- AI agent failure modes
- [27] https://dev.to/aws/how-to-prevent-ai-agent-reasoning-loops-from-wasting-tokens-2652 -- Preventing reasoning loops
- [28] https://agentmarketcap.ai/blog/2026/04/12/ai-agent-token-consumption-gap-enterprise-agentic-workloads -- Agentic token consumption gap
- [29] https://waxell.ai/blog/ai-agent-token-budget-enforcement -- Token budget enforcement
- [30] https://aigi.ox.ac.uk/wp-content/uploads/2025/07/Cot_Is_Not_Explainability.pdf -- "CoT Is Not Explainability" (Oxford, 2025)
- [31] https://www.spraiandprai.com/blog/reasoning-models-cot-faithfulness -- CoT faithfulness analysis
- [32] https://www.emergentmind.com/topics/hidden-chain-of-thought -- Hidden CoT in reasoning models
- [33] https://blog.promptlayer.com/chain-of-thought-is-not-explainability-our-takeaways/ -- CoT explainability takeaways
- [34] https://www.emergentmind.com/topics/self-consistency-prompting -- Self-consistency prompting
- [35] https://aclanthology.org/2025.naacl-long.184/ -- RASC (NAACL 2025)
- [36] https://www.securance.com/blog/prompt-injection-the-owasp-1-ai-threat-in-2026/ -- Prompt injection OWASP #1
- [37] https://www.sciencedirect.com/science/article/pii/S2405959525001997 -- Prompt injection to protocol exploits
- [38] https://zylos.ai/research/2026-04-24-durable-execution-agent-runtimes/ -- Durable execution for agent runtimes
- [39] https://activewizards.com/blog/langgraph-state-management-checkpointing-recovery-and-the-persistence-layer-decision/ -- LangGraph checkpointing
- [40] https://arxiv.org/html/2604.28138v1 -- Crab: Semantics-Aware C/R for Agent Sandboxes
- [41] https://platform.claude.com/docs/en/build-with-claude/extended-thinking -- Claude extended thinking docs
- [42] https://www.anthropic.com/news/visible-extended-thinking -- Claude visible extended thinking announcement
- [43] https://arxiv.org/html/2510.25320v1 -- GAP: Graph-based Agent Planning
- [44] https://arxiv.org/html/2607.01942 -- ATG: Atomic Task Graph
- [45] https://arxiv.org/html/2502.14563v1 -- Plan-over-Graph
- [46] https://arxiv.org/html/2603.11445v2 -- VMAO: Verified Multi-Agent Orchestration
- [47] https://arxiv.org/html/2606.04874 -- Agent Planning Benchmark
- [48] https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide -- LLM agent evaluation metrics 2026
- [49] https://www.morphllm.com/ai-agent-evaluation -- AI agent evaluation 2026
- [50] https://chatforest.com/reviews/openai-o3-o4-mini-reasoning-models-review/ -- o3 and o4-mini benchmarks
- [51] https://gail.wharton.upenn.edu/research-and-insights/tech-report-chain-of-thought/ -- Wharton: Decreasing Value of CoT
- [52] https://tokenmix.ai/blog/openai-o3-pricing -- o3 hidden reasoning token costs
- [53] https://www.strata.io/blog/agentic-identity/practicing-the-human-in-the-loop/ -- HITL guide 2026
- [54] https://www.elementum.ai/blog/human-in-the-loop-agentic-ai -- HITL for agentic AI
- [55] https://www.emergentmind.com/topics/reasoning-failures-in-llms -- LLM reasoning failures
- [56] https://arxiv.org/html/2505.12151v1 -- Reasoning LLM errors from hallucinating problem features
- [57] https://www.emergentmind.com/topics/reasoning-driven-hallucination -- Reasoning-driven hallucination
- [58] https://apito.ai/en/blog/dev-guides/claude-extended-thinking-practical-guide-2026/ -- Claude extended thinking practical guide
- [59] https://www.obsidiansecurity.com/blog/prompt-injection -- Prompt injection attacks 2025
- [60] https://christian-schneider.net/blog/prompt-injection-agentic-amplification/ -- Prompt injection in agentic AI
