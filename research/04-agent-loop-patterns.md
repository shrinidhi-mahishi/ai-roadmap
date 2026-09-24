# Research: Agent Loop Patterns

**Date researched**: 2026-09-23
**Sources consulted**: 42

---

## 1. System Topology & Mechanics

### 1.1 ReAct (Reasoning + Acting)

**Origin**: Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models" (ICLR 2023). The most widely adopted single-agent loop pattern.

**Architecture**: A while-loop around an LLM call following the **Thought -> Action -> Observation** cycle:
1. **Thought**: The LLM analyzes the user goal and history of prior actions/observations, then articulates a plan for the next step.
2. **Action**: The LLM selects and parameterizes a tool call (search, API, calculator, database query, etc.).
3. **Observation**: The tool result is appended to the conversation history and fed back to the LLM.
4. The cycle repeats until the LLM produces a final answer with no tool calls.

The entire skeleton is a while loop, an LLM call inside it, a check for final answer, and a tool-call branch that feeds observations back. Every major agent framework -- LangChain, CrewAI, AutoGen, OpenAI Agents SDK -- implements this under the hood ([apxml.com](https://apxml.com/courses/agentic-llm-memory-architectures/chapter-2-advanced-agent-architectures-reasoning/react-framework-reasoning-acting), [outcomeschool.com](https://outcomeschool.com/blog/react-agent)).

**Why ReAct over pure Chain-of-Thought**: LLMs perform better when they alternate between reasoning and acting. Without this alternation, agents either hallucinate answers (reasoning without acting) or blindly execute tools without understanding results (acting without reasoning). ReAct grounds reasoning in real-world data.

**Known limitations**:
- **Error compounding**: If each step is 95% reliable, a 10-step task succeeds only ~60% of the time. Keep tasks short and validate tool results after each step.
- **Token cost**: Each reasoning step adds computational overhead -- generating explicit thoughts costs extra tokens and latency per iteration.
- **Context window exhaustion**: Accumulated history of Observations, Thoughts, and Actions can exceed the LLM's context window, requiring summarization or context management.
- **Reasoning loops**: Agents can get stuck in verbose loops if the thought process is not well-guided ([emergentmind.com](https://www.emergentmind.com/topics/react-loop-architecture)).

**Variants (2024-2025)**:
- **RP-ReAct**: Decouples planning from execution -- a Reasoner-Planner Agent handles strategic planning while Proxy-Execution Agents each run internal ReAct loops, with context-saving strategies to avoid token overflow.
- **Focused ReAct**: Adds reiteration of the original question at each step and early-stop on repetitive actions, yielding up to 530% relative accuracy gains and 34% runtime reduction in low-resource models ([grindengineer.substack.com](https://grindengineer.substack.com/p/react-pattern-the-most-important-agentic-pattern)).

### 1.2 Plan-and-Execute

**Origin**: Based on Wang et al., "Plan-and-Solve Prompting" (ACL 2023) and Yohei Nakajima's BabyAGI project.

**Architecture**: A two-phase agentic pattern:
1. **Planner**: An LLM call that breaks the user request into a multi-step plan. The planner reasons about what to do but does not execute.
2. **Executor**: Carries out each step using tools. The executor does not re-plan; it follows instructions and reports results.
3. **Re-planner** (optional): After each step or on failure, a re-planning call adjusts the remaining plan based on execution results.

**Advantages over ReAct**:
- **Speed**: Sub-tasks can be performed without consulting the large planning model after each action, or with calls to lighter-weight models.
- **Cost**: The large model is called only for planning/re-planning steps and final response; sub-tasks use smaller, domain-specific models.
- **Task completion quality**: Forcing the planner to explicitly think through all steps improves completion rates ([langchain.com/blog/planning-agents](https://www.langchain.com/blog/planning-agents)).

**Benchmark data (2026)**: Plan-Execute agents using GPT-4 for complex tasks average 3,000-4,500 tokens and 5-8 API calls per task, costing $0.09-$0.14 per task ([dasroot.net](https://dasroot.net/posts/2026/04/agent-architectures-react-plan-execute-graph-agents/)).

**Limitations**: Less adaptive to unexpected outcomes during execution. If a step fails or yields surprising results, the agent struggles to deviate from the plan without a sophisticated re-planning mechanism. Not suitable when execution feedback must immediately influence planning -- use ReAct instead ([amitavroy.com](https://amitavroy.com/articles/2025-06-29-LangGraph-vs-ReAct-When-Should-You-Use-Which-for-Your-Next-AI-Agent)).

| Criteria | ReAct | Plan-and-Execute |
|---|---|---|
| Task type | Dynamic, exploratory, open-ended | Multi-step, predictable workflows |
| Adaptability | High -- adapts after each step | Lower -- follows the plan |
| Cost | Higher (LLM call per step) | Lower (smaller models for sub-tasks) |
| Latency | Higher for complex tasks | Faster multi-step execution |

### 1.3 Reflection / Reflexion

**Origin**: Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning" (NeurIPS 2023, [arXiv:2303.11366](https://arxiv.org/abs/2303.11366)).

**Architecture**: Three components in a **Generate -> Evaluate -> Reflect -> Regenerate** loop:
1. **Actor**: An LLM policy that generates text/actions conditioned on observations plus a memory context.
2. **Evaluator**: Assesses whether the trajectory is correct (test runner, validator, LLM judge, etc.).
3. **Self-Reflection**: An LLM writes a verbal critique analyzing what went wrong, producing a "semantic gradient signal" stored in episodic memory.

No model weights are altered. Improvement is purely in-context: the verbal critique is loaded into the Actor's context on the next attempt. When reflections are stored in a vector database and reused by task type, an emergent skill library arises across episodes ([github.com/noahshinn/reflexion](https://github.com/noahshinn/reflexion)).

**Results**: Achieved 91% pass@1 on HumanEval vs. GPT-4's 80% baseline. Self-reflection adds an 8% absolute boost over episodic memory alone ([niteagent.com](https://niteagent.com/blog/build-log-agent-self-reflection-loop/)).

**Critical limitation**: High cost (10-30x a Chain-of-Thought call), strictly sequential, and useless without a reliable evaluator. If the model misdiagnoses why it failed, the next attempt inherits the wrong correction ([zylos.ai](https://zylos.ai/research/2026-05-12-agent-self-correction-reflexion-to-prm)).

### 1.4 Self-Correction Patterns

**The fundamental constraint**: Huang et al. (ICLR 2024, [proceedings.iclr.cc](https://proceedings.iclr.cc/paper_files/paper/2024/file/8b4add8b0aa8749d80a34ca5d941c355-Paper-Conference.pdf)) demonstrate that intrinsic self-correction -- asking an LLM to review and revise its own answer using only its own judgment -- consistently degrades performance on reasoning benchmarks. The model that generated the wrong answer shares the same blind spots as the model asked to evaluate it.

**The coherence trap**: A 2026 preprint formalizes this information-theoretically: when generator and evaluator share correlated error modes, iterative self-critique can amplify confidence without adding information. The agent convinces itself with increasingly polished but still-wrong reasoning ([arxiv.org/html/2606.05976v1](https://arxiv.org/html/2606.05976v1)).

**Stability threshold (2026)**: A formal feedback-control analysis yields a measurable stability criterion: iterate only when ECR/EIR > Acc/(1-Acc), where ECR = Error Correction Rate and EIR = Error Introduction Rate. Empirically across 7 models and 3 datasets, only o3-mini (+3.4pp), Claude Opus 4.6 (+0.6pp), and o4-mini (+/-0pp) stay non-degrading under intrinsic self-correction ([awesomepapers.io](https://awesomepapers.io/ai-agents/papers/2604.22273)).

**What actually works -- grounded self-correction**:
- **Code**: Run the tests. If tests fail, feed failure output to the correction loop.
- **Research**: Retrieve an external source and compare against it.
- **Form-filling**: Schema validation as the external signal.
- **Process Reward Models (PRMs)**: Trained verifiers that score intermediate reasoning steps, not just final answers.
- **SCoRe (ICLR 2025)**: RL-trained self-correction on the model's own outputs achieves +15.6% on MATH and +9.1% on HumanEval -- genuine intrinsic improvement, but through training, not prompting.

**Design rule**: Ground the critic in something the generator did not write. Find that external signal before writing correction logic ([machinelearningmastery.com](https://machinelearningmastery.com/designing-ai-agents-that-can-self-correct/)).

### 1.5 Language Agent Tree Search (LATS)

**Origin**: Zhou et al. (ICML 2024, [arxiv.org/abs/2310.04406](https://arxiv.org/abs/2310.04406)). Unifies reasoning, acting, and planning by adapting Monte Carlo Tree Search (MCTS) to the linguistic domain.

**Architecture**: Treats thoughts (internal reasoning) and actions (external API calls) as part of the same search tree. Each node encodes current state (task input, action history, observations); edges represent possible next actions or reasoning steps. Six operations: selection, expansion, evaluation, simulation, backpropagation, reflection.

**Key distinction from Tree of Thoughts (ToT)**: ToT relies solely on the LLM's internal knowledge; LATS obtains value estimates after environmental feedback, grounding the search in real observations and significantly reducing hallucinations.

**Results**: 92.7% pass@1 on HumanEval with GPT-4. On HotPotQA, doubled ReAct performance (EM: 0.32 -> 0.71). On Game of 24, 44% success vs. ToT's 20% ([proceedings.mlr.press](https://proceedings.mlr.press/v235/zhou24r.html)).

**Trade-off**: Extremely token-expensive due to tree branching. Practical primarily for high-value tasks where accuracy justifies the cost.

### 1.6 Framework Implementations

#### LangGraph (v2.0, Feb 2026)
Models agents as **cyclic state machines** built from four primitives: State (typed schema with reducers), Nodes (functions returning state updates), Edges (static or conditional), and Checkpointers (persist state to Redis/SQL/file after each step). Over 70% of production agents use some form of graph structure per LangChain's 2026 State of Agent Engineering report. 30,000+ GitHub stars ([docs.langchain.com](https://docs.langchain.com/oss/python/langgraph/overview), [langchain.com/langgraph](https://www.langchain.com/langgraph)).

Key features: durable execution via checkpointing, human-in-the-loop pauses, type-safe streaming (v1.2, May 2026), `create_react_agent()` helper, conditional edges for hybrid patterns.

#### OpenAI Agents SDK (March 2025)
Deliberately minimal: four primitives -- **Agent, Runner, Handoff, Guardrail**. The Runner is the central runtime managing the tool-call loop:
1. Call current agent's model
2. If tool calls: execute them, append results, continue
3. If handoff: switch agent, continue
4. If final answer (text output, no tool calls): return result
5. If `max_turns` exceeded: raise `MaxTurnsExceeded`

Three execution methods: `Runner.run()` (async), `Runner.run_sync()`, `Runner.run_streamed()`. Built-in tracing with spans for every LLM call, tool execution, and handoff. Hooks system: `on_agent_start`, `on_agent_end`, `on_llm_start/end`, `on_tool_start/end`, `on_handoff` ([openai.github.io/openai-agents-python](https://openai.github.io/openai-agents-python/running_agents/), [turion.ai](https://turion.ai/blog/framework-deep-dive-openai-agents-sdk/)).

#### CrewAI (v0.80+, 2026)
Role-based multi-agent framework. 54,000+ GitHub stars, 450M+ agentic workflow executions/month. Each agent runs a ReAct loop internally. `max_iter` (default 15) counts only tool-calling cycles, not LLM calls. Two process modes: `Process.sequential` and `Process.hierarchical`. **Flows** (event-driven orchestration with `@start`, `@listen`, `@router` decorators) solve the statelessness problem for production use. Enforce hard timeouts on `crew.kickoff()` (30s recommended) and run behind task queues like Celery ([docs.crewai.com](https://docs.crewai.com/en/concepts/agents), [atlan.com](https://atlan.com/know/ai-agent/what-is-crewai/)).

#### Google ADK (2025-2026)
Three core workflow agent types, all deterministic (no LLM reasoning for orchestration):
- **SequentialAgent**: Runs agents in order; each receives previous output. Classic pipeline.
- **ParallelAgent**: Runs agents concurrently, collects all outputs. Failure mode: write-set collision when two branches write to the same session key.
- **LoopAgent**: Repeats agents until a stop condition or `max_iterations`. Correctness depends on the termination signal.

Composable: nest Sequential/Parallel/Loop agents arbitrarily. Python, TypeScript, Go, Java under Apache 2.0. ~20,000 GitHub stars by mid-2026 ([google.github.io/adk-docs](https://google.github.io/adk-docs/agents/workflow-agents/loop-agents/), [developers.googleblog.com](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/)).

#### Anthropic Claude Agent Loop
The loop uses the Messages API `stop_reason` field as the authoritative signal: `"tool_use"` means continue (execute tools, append results, re-send), `"end_turn"` means terminate. The reference computer-use implementation ships `max_iterations` (default 10) as a safety net. Claude Code Agent SDK supports `maxTurns: 30` and `effort: "high"` parameters. No built-in cap on the API side -- the framework must enforce limits ([platform.claude.com](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool), [claudecertificationguide.com](https://claudecertificationguide.com/learn/1-agentic-architecture/1-1-agentic-loops)).

**Anti-pattern**: Using `max_iterations` as the primary termination mechanism. The model signals completion via `stop_reason` -- use that signal. Iteration caps are safety nets, not control flow.

### 1.7 Max Iteration Guards

All frameworks implement iteration limits, but the philosophy differs:

| Framework | Default | Parameter | What It Counts |
|---|---|---|---|
| OpenAI Agents SDK | None (no default) | `max_turns` | Full reason-act cycles |
| LangGraph | Configurable | `recursion_limit` | Graph traversals |
| CrewAI | 15 | `max_iter` per Agent | Tool-calling cycles only |
| Google ADK LoopAgent | Required | `max_iterations` | Loop iterations |
| Claude reference | 10 | `max_iterations` | Tool-call cycles |

Best practice: Set conservative limits (5-10 for most tasks), use the model's own termination signal as primary control, and treat iteration caps as cost/safety guardrails only.

---

## 2. Token Economics & NFR Metrics

### 2.1 The Quadratic Cost Problem

Agent loops do not scale linearly. Each iteration re-sends the entire conversation history to the LLM API. A 5-step agent loop costs approximately 15x a single call (triangular number series: 1+2+3+4+5), not 5x. In the worst case, cost approaches O(n^2) with step count ([augmentcode.com](https://www.augmentcode.com/guides/ai-agent-loop-token-cost-context-constraints), [machinelearningmastery.com](https://machinelearningmastery.com/identifying-token-costs-hiding-in-your-agentic-loop/)).

According to Anthropic's multi-agent research (2025): single agents use ~4x the tokens of a single chat; multi-agent systems use ~15x ([getunblocked.com](https://getunblocked.com/blog/agent-auto-loop-token-cost/)).

### 2.2 Context Rot

As tokens accumulate, the model's effective attention budget thins, and recall of earlier instructions drops. Nearly 65% of enterprise AI failures in 2025 were attributed to context drift or memory loss during multi-step reasoning -- not raw context exhaustion, but gradual degradation as irrelevant information crowds out relevant context. You pay rising cost for results that are getting worse ([zylos.ai](https://zylos.ai/research/2026-05-27-context-window-economics-persistent-agents/)).

### 2.3 Cost Comparison Across Patterns

| Pattern | Typical Token Multiplier vs. Single Call | Latency Profile | Best For |
|---|---|---|---|
| ReAct (5-step) | ~15x (quadratic accumulation) | 100-500ms per iteration | Dynamic, exploratory tasks |
| Plan-and-Execute | Lower (smaller models for sub-tasks) | Faster multi-step | Predictable workflows |
| Reflexion | 10-30x a CoT call | Strictly sequential, high latency | Tasks with reliable evaluators |
| LATS | Very high (tree branching) | Minutes per task | High-value accuracy-critical tasks |

### 2.4 Real-World Cost Incidents

- **$47,000 incident (Nov 2025)**: A LangChain market research pipeline with 4 agents entered an infinite loop. An Analyzer and Verifier agent exchanged requests for 11 days with no budget cap. Root cause: no per-agent budget ceiling and no enforcement mechanism ([dev.to/waxell](https://dev.to/waxell/the-47000-agent-loop-why-token-budget-alerts-arent-budget-enforcement-389i)).
- **$4,200 weekend (2026)**: Single developer hit $4,200 in API fees over a long weekend during an autonomous refactoring run ([agentmarketcap.ai](https://agentmarketcap.ai/blog/2026/04/12/ai-agent-token-consumption-gap-enterprise-agentic-workloads)).
- **Claude Code infinite loop (2025)**: A sub-agent consumed 27M tokens stuck in a loop (GitHub Issue #15909).
- **AnalyticsWeek estimate**: $400M collective leak in unbudgeted AI cloud spend across the Fortune 500 in 2025 ([leanopstech.com](https://leanopstech.com/blog/agentic-ai-cost-runaway-token-budget-2026/)).

### 2.5 Five Mitigation Strategies

1. **Subagent isolation**: Distribute work across stateless subagents receiving only relevant context. Reduces tokens from ~15K to ~9K for multi-domain queries.
2. **State resets**: For loops exceeding 10 steps, reset state at phase boundaries. Cost of lossy handoffs is lower than unbounded accumulation.
3. **Context pruning**: Rolling summarization, tool result compression, keeping only last K turns verbatim. Reduces costs 40-75% without quality degradation.
4. **Model tier routing**: Run 80% of steps on a smaller model, escalate only the hard 20% to frontier. Costs roughly 12% of all-frontier workflow with similar results. Typical savings: 60-80%.
5. **Budget enforcement (not just alerts)**: Check token budget *before* each API call completes, blocking the call rather than reporting after the fact. A spending alert fires after cost has accumulated; a token budget prevents it ([waxell.ai](https://waxell.ai/blog/ai-agent-token-budget-enforcement)).

### 2.6 Phase-Based Checkpointing

The most cost-efficient pattern for multi-step workflows: define explicit state checkpoints at phase boundaries. Serialize agent state, start the next phase with a fresh, minimal context. Each phase starts with 2,000-5,000 tokens rather than carrying 50,000+ accumulated tokens from prior phases.

### 2.7 The Bottom Line

You cannot reliably cost-estimate a production agent from staging performance. Staging agents run short sessions against constrained test cases. Without intentional cost engineering, agent costs scale with developer enthusiasm, not delivered value. A survey found 96% of enterprises reported AI costs exceeding initial projections, and only 44% had financial guardrails for AI ([agentmarketcap.ai](https://agentmarketcap.ai/blog/2026/04/12/ai-agent-token-consumption-gap-enterprise-agentic-workloads)).

---

## 3. Distributed Resilience & State

### 3.1 The Core Problem

Most agent implementations run synchronously in memory. If anything interrupts the loop -- an exception, timeout, or process termination -- the state disappears. Agent workflows are long-running by nature (minutes to hours), need to survive infrastructure failures, and require exactly-once semantics for operations with side effects ([inngest.com](https://www.inngest.com/blog/durable-execution-key-to-harnessing-ai-agents)).

### 3.2 Durable Execution

**Definition**: A pattern where execution state of a long-running process is persisted externally so the process can resume from its last checkpoint after any interruption.

**Four key components**:
1. **State checkpointing**: Saves complete agent state after each meaningful step (after every LLM call, tool return, or decision).
2. **Resumability**: Restart from most recent checkpoint, not from the beginning.
3. **Retry logic**: Handle transient failures with appropriate backoff.
4. **Idempotency**: Retrying steps does not cause duplicate side effects.

**Critical distinction**: Session memory is not durable execution. Saving chat history helps an agent remember, but does not prove which shell command ran, which email was sent, which approval was granted, or whether a retry would duplicate a side effect ([diagrid.io](https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows)).

### 3.3 How Temporal's Model Works

Temporal replays event history to reconstruct in-memory state after a crash. The runtime maintains a journal (event history) of every completed step:
- On crash recovery, the workflow function re-executes from the beginning.
- For each step already in the journal, the cached result is returned immediately (no re-execution).
- Execution continues normally from the first step not in the journal.

**Critical pitfall**: Workflows must be deterministic. LLM calls are inherently non-deterministic, so they must be wrapped as "activity" steps whose results are journaled on first execution and never re-run on replay.

**Continue-As-New**: When event history grows too large, the workflow atomically completes the current run and starts a new one with the same workflow ID, carrying forward only essential state. The workflow appears continuous externally but uses a fresh history log internally.

Temporal raised $300M at a $5B valuation (Feb 2026), with 9.1 trillion lifetime action executions -- 1.86 trillion from AI-native companies alone ([zylos.ai](https://zylos.ai/research/2026-04-24-durable-execution-agent-runtimes/)).

### 3.4 Temporal + OpenAI Agents SDK Integration

The most significant development: Temporal's public preview integration with the OpenAI Agents SDK wraps OpenAI agents inside Temporal workflows, where reasoning loops and tool calls are orchestrated as discrete steps. This gives OpenAI agents automatic checkpointing, failure recovery, and exactly-once execution semantics ([inference.sh](https://inference.sh/blog/agent-runtime/durable-execution)).

### 3.5 LangGraph Checkpointing vs. True Durable Execution

LangGraph's checkpointer saves state to an external store (Redis, SQL, file) after each step, enabling fault-tolerant and asynchronous conversations. However, it lacks automatic failure detection -- if your process crashes, no one knows. There is no supervisor, watchdog, or heartbeat mechanism. The workflow is dead until something external notices.

"Checkpointing says: 'I saved your state. You take it from here.' Durable execution says: 'Your agent workflows will run to completion.'" ([diagrid.io](https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows)).

### 3.6 2025 Market Adoption

Durable execution crossed into the early majority in 2025:
- AWS released Durable Functions
- Cloudflare shipped Workflows in GA
- Vercel launched Workflow DevKit
- Temporal, Azure Durable Functions, Inngest, Restate, Hatchet, DBOS all competing

Primary driver: AI agent infrastructure needs ([inngest.com](https://www.inngest.com/blog/durable-execution-key-to-harnessing-ai-agents)).

### 3.7 Cost Argument for Durable Execution

A complex agent workflow making 20 LLM calls at $0.05 each: without durable execution, a crash at step 18 wastes $1.00 (re-running all 20). With durable execution, resuming at step 18 costs only the 2 remaining calls ($0.10).

### 3.8 Human-in-the-Loop as a Durability Primitive

A HITL gate suspends the agent at a named checkpoint, writes full state to the persistent log, releases the process thread entirely, and resumes only when the approval signal arrives (minutes or days later). If an approval is stored only as a chat message, replay can become unsafe ([quellixlabs.com](https://quellixlabs.com/insights/durable-execution-long-running-ai-agent-workflows)).

### 3.9 Idempotent Tool Design

When a tool call fails partway through and the agent retries, the tool must produce the same result whether it runs once or three times. A tool that writes a database record on every invocation turns retry logic into a data corruption vector. Idempotency keeps retries safe. Key patterns: idempotency keys for API calls, upserts instead of inserts, check-before-write for state mutations ([openlayer.com](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation)).

---

## 4. Enterprise Security & Governance

### 4.1 The Governance Gap

Deloitte's 2026 State of AI in the Enterprise: only 21% of organizations have a mature governance model for agentic AI. Autonomous agents outnumber humans in enterprise environments at an 82:1 ratio, yet only 22% of organizations treat AI agents as identity-bearing entities with formal access controls ([sennovate.com](https://sennovate.com/blog/ai-agent-authorization-governance-enterprise-security-in-2026/)).

Gartner projects that by 2029, 70% of enterprises will deploy agentic AI as part of IT infrastructure, up from <5% in 2025.

### 4.2 Approval Gates as Authorization Primitives

Approval gates answer: "Should this invocation execute, with these arguments, in this context?" This is distinct from grant-time authorization (OAuth scopes). Three patterns:
1. **Pause-Resume Interrupt**: Human approval required before execution.
2. **Dynamic Authorization Check**: Policy evaluation at tool invocation time; routes to human if policy escalates.
3. **Scope-Escalation Request**: Mid-workflow re-consent for additional scopes.

([scalekit.com](https://www.scalekit.com/blog/human-in-the-loop-tool-calling), [arthur.ai](https://www.arthur.ai/column/human-in-the-loop-governance-for-ai-agents)).

### 4.3 Risk-Tiered Approval Design

Low-risk, reversible actions should run without interruption -- forcing approval on routine actions trains reviewers to rubber-stamp. Human approval is appropriate for: irreversible actions, financial transactions, administrative operations, regulated activities, and externally visible communications.

"Rubber-stamping is worse than no gate at all, because it creates the appearance of oversight without the substance." ([stackai.com](https://www.stackai.com/insights/human-in-the-loop-ai-agents-how-to-design-approval-workflows-for-safe-and-scalable-automation)).

### 4.4 From Approval Queues to Delegated Authority

Forbes (Aug 2026) argues for a paradigm shift: govern delegated authority, not individual actions. Define delegation boundaries, escalation policies, acceptable risk, and circumstances for automatic authority withdrawal. This works more like a command structure than an approval queue ([forbes.com](https://www.forbes.com/councils/forbestechcouncil/2026/08/11/ai-agent-governance-moving-from-human-approval-to-runtime-authorization/)).

### 4.5 Audit and Accountability

Every approval, denial, override, and timeout should be logged as an event with context. When a regulator asks how a consequential action was authorized, the answer should be a record, not a recollection. With autonomous systems, organizations need to explain why an action was *permitted* (historically, the focus was on explaining why access was *denied*) ([runsymphony.com](https://www.runsymphony.com/blog/human-in-the-loop-agentic-automation-governing-ai-agents-with-approvals-and-audit-trails/)).

### 4.6 Enterprise Identity Model for Agents

Give each production agent a distinct identity. Move secrets into managed vaults and replace permanent credentials with task-bound, short-lived tokens. Key questions an identity model must answer:
- Which agent is acting?
- Whose authority is it using?
- What may it access?
- Which actions require approval?
- How can its access be withdrawn?

### 4.7 Regulatory Drivers

- **NIST**: Launched an AI Agent Standards Initiative (Feb 2026).
- **EU AI Act**: Article 14 sets explicit human-oversight requirements for high-risk AI systems. Article 50 transparency duties began applying Aug 2, 2026.
- **DORA**: Maps to human oversight requirements.

### 4.8 Governance Tools

| Tool | Function |
|---|---|
| **HumanLayer** | SDK for HITL workflows: wraps tool calls with approval gates, audit trails, escalation paths |
| **Proofpane** | Runtime governance gateway for AI coding agents; policy allow/deny/HITL, DLP redaction, hash-chained audit mapped to NIST AI RMF, ISO 42001, EU AI Act, GDPR, SOC 2 |
| **Kakunin** | Compliance and identity infrastructure; X.509 certificates via AWS KMS, per-agent action scope enforcement, auto-revocation on risk threshold |
| **GATE (Sage IT)** | Runtime authorization for agents accessing ERP, CRM, cloud systems; identity, RBAC, least privilege |
| **Arthur** | Human oversight as a control you design, enforce, and monitor |

([github.com/systempromptio/awesome-ai-agent-governance](https://github.com/systempromptio/awesome-ai-agent-governance)).

---

## 5. Production Failure Modes

### 5.1 Taxonomy (2026)

A mid-2026 production taxonomy identifies six categories and fifteen failure modes:
1. **Drift**: Semantic drift, reasoning drift, coordination drift, behavioral drift -- hard to detect, late to surface.
2. **State**: Context exhaustion, memory pollution, hallucinated state.
3. **Coordination**: Sub-agent loss, race conditions, orchestration overhead.
4. **Termination**: Premature stop, infinite loop, budget exhaustion -- most common, easiest to fix.
5. **Adversarial**: Prompt injection, reward hacking, alignment faking -- catastrophic but low maturity.
6. **Tool Interface**: Tool selection error, schema mismatch.

([1023jack.com](https://1023jack.com/news/agentic-loop-failure-modes-a-production-taxonomy-at-the-end-of-year-one/)).

### 5.2 Infinite Loops

IAL-Scan examined 6,549 LLM agent repositories and found 68 confirmed infinite agentic loop failures across 47 projects, with 91.9% precision -- this is not a corner case but a shipped design pattern ([openlayer.com](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation)).

**Detection strategies**:
- Hash-based loop detection: Compare output hashes across iterations; flag when consecutive outputs are too similar.
- Schema-strict tool calls: Validate tool call schemas before dispatch.
- Step count with output similarity thresholds.
- Replayed production traces as evals.

**Notable incidents**:
- Amazon's Kiro AI agent autonomously deleted a production AWS environment, causing a 13-hour outage (2026).
- Claude Code sub-agent stuck in infinite loop, consuming 27M tokens (GitHub #15909, 2025).

### 5.3 Context Window Exhaustion

Multi-step agents accumulate context monotonically. When the context window fills, models silently truncate earlier content -- losing system prompts, initial instructions, or critical early observations. The agent continues running but with degraded recall, producing increasingly off-target outputs.

### 5.4 Hallucinated Actions and Self-Reinforcing Errors

Agents can hallucinate tool calls (calling non-existent tools or passing invalid parameters), then interpret the error messages as environmental signals and adapt their strategy based on false premises. In multi-agent systems, one agent's hallucinated output becomes another agent's trusted input, propagating errors.

### 5.5 Plan Invalidation During Execution

In Plan-and-Execute patterns, the plan can become invalid during execution due to: tool failures returning unexpected results, external state changes (database modified by another process), rate limiting or API unavailability, and time-dependent operations crossing boundaries (market close, maintenance windows).

### 5.6 Degenerate Self-Correction

As established in Section 1.4: intrinsic self-correction (model judging itself) consistently degrades performance. The degenerate loop pattern: agent makes an error, self-critiques, generates a "correction" that introduces a new error, self-critiques again with increasing confidence, and diverges from the correct solution while appearing to make progress.

CorrectBench (2025): Self-correction adds ~5% on hard reasoning benchmarks (MATH), but on easy tasks plain CoT does just as well using 40% less compute. Reflection is not free -- it costs tokens, latency, and money every iteration ([beancount.io](https://beancount.io/bean-labs/research-logs/2026/04/28/llms-cannot-self-correct-reasoning-yet)).

### 5.7 Action-Observation Drift

The agent loses track of its original goal as observations accumulate. Common in research/browsing tasks: the agent follows an interesting tangent in tool results and never returns to the original objective. Mitigations: reiterate the original question at each step (Focused ReAct), include goal in system prompt, add a goal-relevance check before each action.

### 5.8 Reliability at Scale

Multi-agent LLM systems fail 41-86% of the time in production depending on task complexity. Five agents at 95% individual accuracy deliver roughly 77% overall success. 88% of agent failures trace to infrastructure gaps, not model quality (Arize, 2026). Context blindness (31.6%) and rogue actions (30.3%) are the top two failure classes.

Gartner predicts over 40% of agentic AI projects will be canceled by 2027 -- not because the technology does not work, but because teams deployed without resilience infrastructure ([galileo.ai](https://galileo.ai/blog/agent-failure-modes-guide), [trantorinc.com](https://www.trantorinc.com/blog/ai-agent-failure-modes-what-goes-wrong-design-resilience)).

### 5.9 Engineering Prioritization

Prioritize by: detection difficulty x frequency x cost per incident.

Production order of return-on-engineering:
1. **Tool interface** (high frequency, easy fix) -- schema validation, idempotent tools
2. **Termination** (most common) -- iteration limits, budget caps
3. **State** (context exhaustion) -- pruning, summarization, resets
4. **Drift** (hard to detect) -- goal reiteration, trajectory evaluation
5. **Coordination** (multi-agent only) -- timeouts, dead-letter queues
6. **Adversarial** (catastrophic but rare) -- input sanitization, sandboxing

The most-cited 2026 failure pattern: silent contamination + late surfacing + hard recovery. Diagnostics on the trace, not the score -- final-score evaluation hides almost everything interesting ([niteagent.com](https://niteagent.com/blog/multi-agent-failure-modes-7-patterns-that-break-production-systems/)).

---

## 6. Enterprise System Design Scenarios

### 6.1 Scenario: Autonomous Code Review Agent with ReAct Loop

**Problem**: Code review is a bottleneck. Human reviewers spend time on mechanical issues (style violations, unused imports, common vulnerability patterns, secret exposure) instead of design and business logic.

**Architecture (ReAct + Reflection)**:

```
                     +-------------------+
                     |   PR Webhook      |
                     +--------+----------+
                              |
                     +--------v----------+
                     |  Orchestrator     |
                     |  (LangGraph DAG)  |
                     +--------+----------+
                              |
              +---------------+----------------+
              |               |                |
     +--------v------+ +-----v-------+ +------v--------+
     | Security Agent| | Style Agent | | Perf Agent    |
     | (ReAct loop)  | | (ReAct loop)| | (ReAct loop)  |
     | Tools: Semgrep| | Tools: lint | | Tools: profiler|
     | CodeQL, Bandit| | ruff, mypy  | | complexity    |
     +--------+------+ +-----+-------+ +------+--------+
              |               |                |
              +---------------+----------------+
                              |
                     +--------v----------+
                     |  Synthesizer      |
                     |  (merges findings)|
                     +--------+----------+
                              |
                     +--------v----------+
                     |  Self-Review      |
                     |  (Reflexion pass) |
                     +--------+----------+
                              |
                     +--------v----------+
                     |  Post PR Comments |
                     +-------------------+
```

**Design decisions**:
1. **Parallel sub-agents (Google ADK ParallelAgent or LangGraph fan-out)**: Security, Style, and Performance agents run concurrently against the diff, each with their own ReAct loop. Each receives only the diff as context (subagent isolation), not the full conversation history.
2. **Structured output per finding**: Each agent produces findings with description, file/line range, severity, and proposed fix.
3. **Reflexion self-review pass**: Before posting, a fresh-context review agent evaluates the synthesized findings for false positives and removes low-confidence items. Fresh context reduces confirmation bias.
4. **External grounding**: Security agent runs actual static analysis tools (Semgrep, CodeQL, Bandit) rather than relying on LLM knowledge. The LLM interprets results, not generates them.
5. **Iteration limits**: Each sub-agent capped at `max_iter=5`. The orchestrator has a global timeout of 120 seconds.
6. **Human gate**: Findings above "warning" severity trigger a HITL approval gate before auto-fix is applied.

**Cost model**: 3 parallel agents x ~3 iterations each x ~2K tokens/iteration = ~18K tokens total. At GPT-4o pricing (~$2.50/1M input tokens), approximately $0.05 per PR review. Compare to Copilot code review: 60M+ reviews since April 2025 launch ([agentpatterns.ai](https://agentpatterns.ai/code-review/), [agentpatterns.ai/agent-self-review-loop](https://agentpatterns.ai/code-review/agent-self-review-loop/)).

**Scope ceiling**: Self-review catches mechanical issues. It does not catch architectural misjudgments, incorrect business logic, or design problems requiring domain knowledge beyond the agent's context. Two review passes covers most cases; a third only when an external validator (test runner, linter) fails.

### 6.2 Scenario: Multi-Step Data Pipeline Orchestration with Plan-and-Execute

**Problem**: An enterprise ETL pipeline must extract data from 5 heterogeneous sources, transform and validate it, load into a data warehouse, run quality checks, and notify stakeholders. Steps have dependencies, some can run in parallel, and failures at any step require intelligent recovery.

**Architecture (Plan-and-Execute + Durable Execution)**:

```
+------------------+     +-------------------+     +------------------+
|   Planner Agent  |     |  Temporal Workflow |     |  Re-planner      |
|   (GPT-4 class)  +---->|  (Durable Runtime) +---->|  (on step fail)  |
|   Generates plan |     |  Executes steps    |     |  Adjusts plan    |
+------------------+     +---------+---------+     +------------------+
                                    |
                    +---------------+----------------+
                    |               |                |
           +--------v------+ +-----v-------+ +------v--------+
           | Extract: CRM  | | Extract: ERP| | Extract: API  |
           | (Activity)    | | (Activity)  | | (Activity)    |
           +--------+------+ +-----+-------+ +------+--------+
                    |               |                |
                    +---------------+----------------+
                                    |
                           +--------v----------+
                           |  Transform Agent  |
                           |  (Smaller model)  |
                           |  Schema validation|
                           +--------+----------+
                                    |
                           +--------v----------+
                           |  Load to DWH      |
                           |  (Idempotent)     |
                           +--------+----------+
                                    |
                           +--------v----------+
                           |  Quality Check    |
                           |  (SQL assertions) |
                           +--------+----------+
                                    |
                      +-------------+-------------+
                      |                           |
               +------v-------+           +------v--------+
               | Pass: Notify |           | Fail: Re-plan |
               | stakeholders |           | or escalate   |
               +--------------+           +---------------+
```

**Design decisions**:
1. **Planner as GPT-4 class model**: Generates the full execution plan with dependency graph. Called only twice: initial planning and re-planning on failure. This keeps the expensive model calls to a minimum.
2. **Executor uses smaller models**: Each extraction/transformation step uses a lightweight model (GPT-4o-mini class) or deterministic code, saving 60-80% on token costs.
3. **Temporal for durable execution**: Each step is a Temporal Activity with its own retry policy and timeout. If the workflow crashes at step 4 of 6, it resumes at step 4 -- not from the beginning. LLM calls wrapped as Activities so results are journaled and never re-run on replay.
4. **Parallel extraction**: The three extract steps run concurrently as parallel Activities within Temporal, with individual timeouts and retry policies.
5. **Idempotent loads**: The Load step uses upserts with idempotency keys so retries do not create duplicate records.
6. **Quality check as external grounding**: SQL assertion queries provide the external signal for self-correction. If quality checks fail, the Re-planner generates a corrective plan (e.g., re-extract from the failing source with different parameters).
7. **Budget enforcement**: Token budget set at the workflow level. Each Activity has a cost ceiling. If exceeded, the workflow escalates to human rather than continuing.
8. **HITL gate**: Re-planning beyond 2 attempts triggers human review. The Temporal workflow pauses, serializes state, and resumes on human approval (minutes or days later).

**Cost model**: Planning: ~4K tokens x 2 calls = $0.04. Extraction: 3 sources x ~1K tokens = $0.001 (small model). Transform + Load + QC: ~3K tokens = $0.001. Total per pipeline run: ~$0.05. Without durable execution and a crash at step 5: re-running costs $0.04 extra in wasted planning tokens plus operational delay.

**Failure handling matrix**:

| Failure | Detection | Recovery |
|---|---|---|
| Source unavailable | Timeout on Activity | Retry with backoff (3x), then skip + flag |
| Schema mismatch | Transform validation | Re-planner adjusts transform logic |
| Quality check fails | SQL assertions | Re-extract from failing source |
| Budget exceeded | Pre-call token check | Escalate to human |
| Process crash | Temporal heartbeat | Auto-resume from last checkpoint |

---

## Sources

- [1] [apxml.com - ReAct Framework](https://apxml.com/courses/agentic-llm-memory-architectures/chapter-2-advanced-agent-architectures-reasoning/react-framework-reasoning-acting) -- ReAct architecture and Thought-Action-Observation cycle
- [2] [outcomeschool.com - What is a ReAct Agent?](https://outcomeschool.com/blog/react-agent) -- ReAct agent fundamentals
- [3] [emergentmind.com - ReAct Loop Architecture](https://www.emergentmind.com/topics/react-loop-architecture) -- ReAct loop variants and evolution
- [4] [grindengineer.substack.com - ReAct Pattern](https://grindengineer.substack.com/p/react-pattern-the-most-important-agentic-pattern) -- Focused ReAct and RP-ReAct variants
- [5] [langchain.com/blog/planning-agents](https://www.langchain.com/blog/planning-agents) -- Plan-and-Execute agents in LangChain
- [6] [amitavroy.com - LangGraph vs ReAct](https://amitavroy.com/articles/2025-06-29-LangGraph-vs-ReAct-When-Should-You-Use-Which-for-Your-Next-AI-Agent) -- When to use ReAct vs Plan-and-Execute
- [7] [dasroot.net - Agent Architectures](https://dasroot.net/posts/2026/04/agent-architectures-react-plan-execute-graph-agents/) -- Benchmark data for Plan-Execute agents
- [8] [theaiengineer.substack.com - 4 Single Agent Patterns](https://theaiengineer.substack.com/p/the-4-single-agent-patterns) -- ReAct vs Plan-and-Execute vs ReWOO vs Reflexion comparison
- [9] [arxiv.org/abs/2303.11366](https://arxiv.org/abs/2303.11366) -- Reflexion: Language Agents with Verbal Reinforcement Learning (Shinn et al., NeurIPS 2023)
- [10] [github.com/noahshinn/reflexion](https://github.com/noahshinn/reflexion) -- Official Reflexion implementation
- [11] [niteagent.com - Self-Reflection Loop](https://niteagent.com/blog/build-log-agent-self-reflection-loop/) -- Production Reflexion implementation guide
- [12] [zylos.ai - Agent Self-Correction](https://zylos.ai/research/2026-05-12-agent-self-correction-reflexion-to-prm) -- From Reflexion to Process Reward Models
- [13] [proceedings.iclr.cc - LLMs Cannot Self-Correct](https://proceedings.iclr.cc/paper_files/paper/2024/file/8b4add8b0aa8749d80a34ca5d941c355-Paper-Conference.pdf) -- Huang et al. ICLR 2024
- [14] [arxiv.org/html/2606.05976v1](https://arxiv.org/html/2606.05976v1) -- The Self-Correction Illusion (2026)
- [15] [awesomepapers.io](https://awesomepapers.io/ai-agents/papers/2604.22273) -- Self-Correction as Feedback Control (stability threshold analysis)
- [16] [beancount.io](https://beancount.io/bean-labs/research-logs/2026/04/28/llms-cannot-self-correct-reasoning-yet) -- ICLR 2024 analysis and implications
- [17] [arxiv.org/abs/2310.04406](https://arxiv.org/abs/2310.04406) -- LATS: Language Agent Tree Search (Zhou et al., ICML 2024)
- [18] [proceedings.mlr.press](https://proceedings.mlr.press/v235/zhou24r.html) -- LATS ICML proceedings
- [19] [docs.langchain.com/langgraph](https://docs.langchain.com/oss/python/langgraph/overview) -- LangGraph overview and documentation
- [20] [langchain.com/langgraph](https://www.langchain.com/langgraph) -- LangGraph agent orchestration framework
- [21] [eastondev.com - LangGraph State](https://eastondev.com/blog/en/posts/ai/20260424-langgraph-agent-architecture/) -- LangGraph checkpoints, threads, recovery
- [22] [reactify-solutions.com - LangGraph in 2026](https://www.reactify-solutions.com/articles/langgraph-production-agents-2026) -- Production agents as state machines
- [23] [openai.github.io/openai-agents-python](https://openai.github.io/openai-agents-python/running_agents/) -- OpenAI Agents SDK Runner documentation
- [24] [turion.ai - Agents SDK Deep Dive](https://turion.ai/blog/framework-deep-dive-openai-agents-sdk/) -- Production agent builder guide
- [25] [cohorte.co - Mastering Agents SDK](https://cohorte.co/blog/mastering-the-openai-agents-sdk-a-field-guide-for-busy-developers-ai-vps) -- Field guide for developers and AI VPs
- [26] [docs.crewai.com/agents](https://docs.crewai.com/en/concepts/agents) -- CrewAI agent concepts and max_iter
- [27] [atlan.com - What is CrewAI](https://atlan.com/know/ai-agent/what-is-crewai/) -- CrewAI architecture, limits, context gap
- [28] [google.github.io/adk-docs](https://google.github.io/adk-docs/agents/workflow-agents/loop-agents/) -- Google ADK LoopAgent documentation
- [29] [developers.googleblog.com](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/) -- Multi-agent patterns in ADK
- [30] [platform.claude.com](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) -- Claude computer use tool and agent loop
- [31] [claudecertificationguide.com](https://claudecertificationguide.com/learn/1-agentic-architecture/1-1-agentic-loops) -- Agentic loops certification guide
- [32] [augmentcode.com](https://www.augmentcode.com/guides/ai-agent-loop-token-cost-context-constraints) -- AI agent loop token costs and context constraints
- [33] [machinelearningmastery.com](https://machinelearningmastery.com/identifying-token-costs-hiding-in-your-agentic-loop/) -- Identifying hidden token costs in agentic loops
- [34] [dev.to/waxell](https://dev.to/waxell/the-47000-agent-loop-why-token-budget-alerts-arent-budget-enforcement-389i) -- The $47,000 agent loop incident
- [35] [waxell.ai](https://waxell.ai/blog/ai-agent-token-budget-enforcement) -- Token budget enforcement strategies
- [36] [agentmarketcap.ai](https://agentmarketcap.ai/blog/2026/04/12/ai-agent-token-consumption-gap-enterprise-agentic-workloads) -- Agentic AI costs 10-100x more than chat
- [37] [inngest.com](https://www.inngest.com/blog/durable-execution-key-to-harnessing-ai-agents) -- Durable execution for AI agents
- [38] [diagrid.io](https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows) -- Checkpoints vs. durable execution
- [39] [1023jack.com](https://1023jack.com/news/agentic-loop-failure-modes-a-production-taxonomy-at-the-end-of-year-one/) -- Agentic loop failure modes taxonomy
- [40] [openlayer.com](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation) -- AI agent failure modes: loops and propagation
- [41] [galileo.ai](https://galileo.ai/blog/agent-failure-modes-guide) -- 7 AI agent failure modes and prevention
- [42] [agentpatterns.ai](https://agentpatterns.ai/code-review/) -- Agentic code review patterns and architectures
- [43] [forbes.com](https://www.forbes.com/councils/forbestechcouncil/2026/08/11/ai-agent-governance-moving-from-human-approval-to-runtime-authorization/) -- AI agent governance: from approval to runtime authorization
- [44] [sennovate.com](https://sennovate.com/blog/ai-agent-authorization-governance-enterprise-security-in-2026/) -- AI agent authorization governance 2026
- [45] [github.com/systempromptio/awesome-ai-agent-governance](https://github.com/systempromptio/awesome-ai-agent-governance) -- Curated governance tools and standards
- [46] [scalekit.com](https://www.scalekit.com/blog/human-in-the-loop-tool-calling) -- Human-in-the-loop tool calling approval gates
- [47] [arthur.ai](https://www.arthur.ai/column/human-in-the-loop-governance-for-ai-agents) -- HITL governance for AI agents
