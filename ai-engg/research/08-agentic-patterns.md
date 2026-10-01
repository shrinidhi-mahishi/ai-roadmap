# Research: Agentic Design Patterns
**Date researched**: 2026-09-29
**Sources consulted**: 18+

---

## 1. System Topology & Mechanics

### 1.1 The Control Spectrum: Workflows vs. Agents

The foundational design axis is **who controls execution flow** -- code or the model. Both Anthropic (Schluntz & Zhang, Dec 2024) and the SystemDesign.one newsletter frame this as an escalation ladder:

| Level | Controller | Defining Signal | Example |
|---|---|---|---|
| **L1: Direct API call** | Code | Single prompt, single response | Summarization, classification, extraction |
| **L2: Workflow patterns** | Code orchestrates, LLM executes steps | All steps known before runtime | Contract review pipeline, customer triage |
| **L3: Agent patterns** | LLM decides next steps | Steps/count unknown until runtime | SWE-bench coding agent, open-ended research |
| **L4: Multi-agent systems** | Multiple LLMs coordinate | No single LLM can hold all context/tools | Magentic-One web+code+file tasks |

**Key heuristic**: "If you can still write down all the steps before the system runs, stick with a workflow." (SystemDesign.one). Anthropic's equivalent: "Add complexity only when it demonstrably improves outcomes."

### 1.2 The Building Block: Augmented LLM

Every agentic system rests on an LLM enhanced with three capabilities (Anthropic):
- **Retrieval** -- accessing external knowledge (RAG, search)
- **Tools** -- interacting with external services/APIs
- **Memory** -- determining what information to retain across turns

MCP (Model Context Protocol) has emerged as the open standard for model-tool integration. Donated to the Linux Foundation's Agentic AI Foundation in Dec 2025; adopted by OpenAI, Google, Microsoft.

### 1.3 Andrew Ng's Four Foundational Patterns (March 2024)

The first widely adopted taxonomy. Ng described these as "four design patterns for AI agentic workflows":

**1. Reflection** -- LLM critiques its own output and iterates. Can be self-critique or a separate critic agent. Improves quality at 2-3x token cost. Most mature of the four patterns.

**2. Tool Use** -- LLM decides which functions to call (web search, calendar, email, code execution). Now subsumed into native function calling in GPT-4o, Claude 3.x+, Gemini 2.x. The ReAct mental model (reason-act-observe-repeat) remains the dominant paradigm even when implemented via native function calling.

**3. Planning** -- Breaking complex tasks into executable steps. Ng explicitly flagged this as "less mature, less predictable" than Reflection and Tool Use. Variants: Plan-and-Execute (separate planning from execution), Tree-of-Thoughts (branching exploration).

**4. Multi-Agent Collaboration** -- Multiple specialized agents with distinct prompts, LLMs, tools, and code working together. Role specialization + coordination. Most powerful but most complex.

### 1.4 Anthropic's Five Workflow Patterns

Anthropic's guide (Dec 2024, expanded 2026) provides five patterns at the workflow level (code controls flow):

#### Pattern 1: Prompt Chaining
- **Topology**: Linear pipeline -- LLM1 -> Gate1 -> LLM2 -> Gate2 -> LLM3
- **Trade-off**: Latency grows linearly (N steps = N round-trips). Errors carry forward.
- **Validation gates** between steps are the critical reliability mechanism.
- **When to use**: Task cleanly decomposes into fixed subtasks.
- **Example**: Generate marketing copy -> translate; write outline -> validate -> write full document.

#### Pattern 2: Routing
- **Topology**: Classifier -> {Handler_cheap, Handler_specialized, Handler_expensive}
- **Key property**: Router accuracy is a system-wide ceiling on quality.
- **Cost optimization**: Simple queries hit cheap/fast models; complex queries hit expensive/capable ones.
- **Example**: Sierra AI routes across 15+ models by task type. Customer triage to specialized handlers.
- **When to use**: Distinct input categories that benefit from specialized handling.

#### Pattern 3: Parallelization
Two sub-patterns:
- **Sectioning**: Split task into independent parts, run simultaneously, merge. (Construction crew analogy.)
- **Voting**: Run same task N times with different prompts, aggregate. (Jury analogy.) Flag only if 2+ agree.
- **Cost**: Multiplies with every parallel branch.
- **Critical design decision**: Partial failure strategy must be decided at design time -- retry, proceed without, or fail entire operation.
- **Example (Sectioning)**: Guardrail + main agent run in parallel -- "tends to perform better than having the same LLM call handle both" (Anthropic).
- **Example (Voting)**: Code vulnerability review with multiple prompts; content moderation with vote thresholds.

#### Pattern 4: Orchestrator-Workers
- **Topology**: Hub-and-spoke. Central orchestrator LLM dynamically decomposes task, delegates to workers, synthesizes results.
- **Not multi-agent**: Single LLM retains central control.
- **Distinction from parallelization**: Subtasks are NOT pre-defined; discovered at runtime.
- **Failure modes**: Goal drift, over-decomposition, orchestrator becomes bottleneck.
- **Example**: Cursor's agent mode (multi-file edits); complex search across multiple sources.

#### Pattern 5: Evaluator-Optimizer
- **Topology**: Generator LLM <-> Evaluator LLM (iterative loop).
- **When to use**: (1) LLM responses improve demonstrably with articulated feedback, AND (2) LLM can provide such feedback. Requires clear evaluation criteria.
- **Example**: Literary translation refinement; iterative search deepening.

**Anthropic's progression guidance**: "Most production systems shouldn't need to go further than parallelization." (SystemDesign.one confirms.)

### 1.5 Agentic Reasoning Patterns (Single-Agent)

| Pattern | Mechanism | Token Scaling | Best For |
|---|---|---|---|
| **ReAct** | Interleave thought-action-observation loops | O(n^2) -- context reinclusion per step | General-purpose tool-using agents (default choice) |
| **ReWOO** | Separate planning from execution entirely | 30-50% cheaper than ReAct | Predictable multi-step workflows |
| **Reflexion** | ReAct + self-critique after failure, episodic memory | 2-3x ReAct | Tasks with repeating failure modes |
| **Plan-and-Execute** | Create full plan first, execute steps sequentially | Variable; avoids ReAct's context accumulation | Planning-bottlenecked tasks |
| **Tree-of-Thoughts** | Branch-and-bound exploration of reasoning paths | 10-100x CoT | Hard combinatorial problems (Game of 24: 74% vs CoT's 4%) |

**Production recommendation** (LangGraph docs, multiple sources): "Default to ReAct. Build a baseline; measure success rate, tool-call accuracy, latency, cost. Identify the specific failure mode. Then escalate. Multi-agent adds 2-5x coordination overhead."

### 1.6 Multi-Agent Orchestration Topologies

| Topology | Description | Framework Support |
|---|---|---|
| **Supervisor/Manager** | Central agent routes/delegates to specialists | CrewAI (hierarchical), LangGraph (supervisor), OpenAI Agents SDK (manager pattern) |
| **Decentralized Handoff** | Agents transfer control to one another; no central coordinator | OpenAI Agents SDK (handoff pattern), AutoGen |
| **Magentic (Outer+Inner Loop)** | Orchestrator maintains task ledger (facts, guesses, plan); outer loop manages plan, inner loop manages progress | Microsoft Agent Framework (MAF), AutoGen |
| **Sequential Pipeline** | Agents run in fixed order, output feeds next | CrewAI (sequential process) |
| **Debate/Voting** | Multiple agents independently solve same problem, results aggregated | AutoGen, custom implementations |

### 1.7 Framework Implementations Comparison (as of mid-2026)

| Framework | Core Abstraction | Strengths | Limitations |
|---|---|---|---|
| **LangGraph** (LangChain) | Stateful graph: nodes + edges + shared state | Most mature fault-tolerance (checkpointing GA at v1.0, Oct 2025). Full control over agent architecture. | Requires more explicit wiring. Steeper learning curve. |
| **OpenAI Agents SDK** (March 2025) | 4 primitives: Agent, Runner, Tools, Handoffs + Guardrails, Sessions | Minimal, explicit, easy to understand. Provider-agnostic (100+ LLMs). Guardrails as first-class. | Less suited for complex stateful graphs. |
| **CrewAI** | Crews (teams) + Flows (event-driven) + Roles | Fastest setup for role-based teams. 5.76x faster than LangGraph in QA benchmarks (JetThoughts 2025). | Lower success rate on complex reasoning (54% vs LangGraph 62%, Pooya.blog 2026). Memory >2GB for 10+ agent/50+ task crews. Telemetry on by default. |
| **Microsoft Agent Framework** (MAF, Apr 2026 v1.0) | Merger of AutoGen + Semantic Kernel. Typed graph-based Workflows. | Enterprise-grade: Azure AI Foundry integration, A2A protocol, MCP support. Magentic orchestration built-in. | AutoGen now maintenance-mode. MAF is new; ecosystem still maturing. |

**Meta-guidance**: "Framework choice matters less than pattern fit. Pick by team familiarity and ops integration, not by exclusive pattern support." (LangGraph docs)

---

## 2. Token Economics & NFR Metrics

### 2.1 The Fundamental Cost Shift

Agentic AI workloads are fundamentally different from chat:
- **Single agentic session**: 1-3.5 million tokens per task (50-500x a chat interaction). (AgentMarketCap, 2026)
- **Input tokens dominate**: Context re-reading drives cost, not output generation. Stanford Digital Economy Lab found **re-sent context accounts for 62% of total agent inference bills**.
- **Token prices dropped 67% YoY** (Q1 2025 to Q1 2026: $18.40 -> $6.07 per million tokens), yet **73% of enterprises exceeded AI budgets** (Gartner).

### 2.2 Token Amplification by Pattern

| Pattern | Token Multiplier vs. Single Call | Key Driver |
|---|---|---|
| **Single LLM call** | 1x (baseline) | -- |
| **Prompt Chaining (3-5 steps)** | 3-5x | Linear step accumulation |
| **ReAct (5-10 steps)** | 10-30x | O(n^2) context reinclusion |
| **Reflection** | 2-3x per iteration | Self-critique + regeneration |
| **ReWOO** | 30-50% less than ReAct | Planning separated from execution |
| **Tree-of-Thoughts** | 10-100x vs CoT | Branch exploration |
| **Multi-agent debate** | 20x vs ReAct [inferred from multiple sources] | Bilateral token charges + coordination |
| **Multi-agent pipeline** | 3-100x | Inter-agent messages cost tokens on both send and receive sides |

### 2.3 Context Accumulation Problem

ReAct's O(n^2) scaling explained: each step re-reads entire message history + all prior tool outputs. A 10-step workflow does NOT cost 10x a single step -- it costs substantially more. Context window fills quadratically.

**Mitigation strategies** (40-70% token reduction without quality loss):
- Truncate tool outputs to top-k results or summaries
- Implement message history sliding windows after 10+ steps
- Cache embeddings of completed tasks

### 2.4 Latency Profiles

| Architecture | Typical Latency | Notes |
|---|---|---|
| **Single call** | 1-5s | Baseline |
| **Prompt chaining (3 steps)** | 3-15s | Linear growth |
| **ReAct (5-10 steps)** | 15-60s | Tool call latency adds up |
| **Plan-and-Execute** | 10-30s planning + execution | Planning phase is upfront cost |
| **Multi-agent (R1+Sonnet pairing)** | 251.6s per case vs 12.4s for solo model [observed benchmark] | Order-of-magnitude slower |

### 2.5 Cost Optimization Strategies

1. **Model Routing/Tiering**: Reserve strong models for planning/review; cheaper models for mechanical execution. Inherent in Routing pattern.
2. **Prompt Caching**: Claude Sonnet 4.5 achieved **78.5% cost reduction** from caching across 500+ agentic sessions (2025 study).
3. **Plan Caching**: Store successful tool execution plans, replay for similar tasks. 20-35% cost reduction on recurring structured tasks.
4. **Speculative Execution**: Pre-fetch likely tool results in parallel. 1.4-2.1x throughput improvement on tool-heavy workloads.
5. **Context Compression**: LLMLingua achieves 20x prompt compression; ACON framework shows 26-54% reduction in peak token usage.
6. **Memory Layer**: Before expensive Orchestrator planning, query memory for cached plans. Can reduce latency from 30s to 300ms.

### 2.6 Enterprise Cost Reality

- Deloitte Q4 2025: Teams discovering "tens of millions" in monthly bills from agentic loops.
- Gartner: **40% of AI agent projects will be cancelled by 2027 due to cost overruns alone**.
- Google researchers: Multi-agent coordination **dropped performance 39-70% on complex tasks** while multiplying token spend -- more expensive AND less reliable.
- Enterprise two-agent setups (assistant + user proxy) cover most cases; group chat only when rotating roles are genuinely needed.

---

## 3. Distributed Resilience & State

### 3.1 State Management Across Agent Loops

Every agent loop maintains state that must persist across iterations:
- **Conversation history** (messages, tool calls, observations)
- **Task state** (current plan, completed steps, pending actions)
- **Memory** (short-term working memory, long-term episodic memory)
- **External state** (side effects already executed)

**LangGraph's model**: Shared state object that every node reads from and writes to. State persisted at every node transition via SqliteSaver (dev) or PostgresSaver (production). This is the most mature approach as of late 2025.

**OpenAI Agents SDK**: Sessions provide automatic conversation history across runs. Lighter-weight than LangGraph's full graph state.

**CrewAI**: Memory system for cross-session context retention. Can exceed 2GB for large crews (10+ agents, 50+ tasks).

### 3.2 Checkpointing for Long-Running Agents

**The single most impactful resilience pattern.** Without it, every agent failure means starting from scratch.

| Framework | Checkpoint Mechanism | Production Readiness |
|---|---|---|
| **LangGraph** | Automatic at every node transition. SqliteSaver (dev), PostgresSaver (prod). Resume from last checkpoint on crash. | GA at v1.0 (Oct 2025). Most mature. |
| **Temporal** | Workflow history replay from immutable event log. Activity-level retries. Deterministic replay. | Production-grade but heavyweight. Better for discrete steps than open-ended reasoning loops. |
| **Microsoft Agent Framework** | Built-in checkpointing + persistent state in Azure AI Foundry (Hosted Agents). | v1.0 (Apr 2026). Enterprise-grade. |

**Human-in-the-loop checkpoints**: Persist workflow state before external decision event; resume after decision arrives. No live process held while a human decides. Every production implementation must persist state to external storage BEFORE the human wait begins.

### 3.3 Error Recovery Patterns

**Checkpointing + Idempotency**: Both are required. Durable execution without idempotency = half-solved. Any side-effecting tool must carry an idempotency key so resumed execution can check whether the action already happened before re-issuing.

**Circuit Breakers**: When a downstream service goes down, naive per-execution retries turn an agent fleet into a self-inflicted DDoS. Fix: exponential backoff with jitter per agent + global circuit breaker that trips after a shared failure threshold.

**Retry Policies**: Must be per-tool, not global. Different tools have different failure modes and recovery characteristics.

**Graceful Degradation Hierarchies**: When a tool is unavailable, fall back to a less capable alternative rather than failing entirely. E.g., if web search fails, fall back to cached results or parametric knowledge.

**Self-Healing Architecture Patterns**: Supervisor trees, circuit breakers, idempotency guards, health checks -- all decades-old distributed systems patterns. What is new: the "failure mode" may be a silent quality regression rather than a process crash.

### 3.4 Durable Execution Frameworks

For production agent systems that need reliability guarantees beyond what agent frameworks provide natively:
- **Temporal**: Best for decomposed, discrete-step workflows. Activity-level retries + deterministic replay.
- **Inngest / Hatchet**: Event-driven durable execution with built-in retry and idempotency.
- **LangGraph Platform** (LangSmith): Managed deployment with built-in persistence, streaming, and monitoring.

---

## 4. Enterprise Security & Governance

### 4.1 Permission Boundaries

**The problem is real and measured**: Databricks Opsin Labs (2025) found **60% of enterprise AI agents are over-permissioned**. 2026 CISO AI Risk Report (235 large-enterprise security leaders): 92% lack full visibility into AI identities, 86% don't enforce access policies for AI identities.

**Key risks**:
- Agents inheriting too much access from user accounts
- Actions changing mid-session from read-only to write operations
- Delegation between agents obscuring accountability
- Static credentials outliving intended tasks

**Best practices**:
- No agent should receive admin-class permissions during a run where it also executes other work.
- Self-modification of policy/entitlements must be a separate, human-initiated change.
- Zero Trust for AI: cryptographically verifiable identity per agent, dynamic permission evaluation, policy-driven actions, authenticated inter-agent communication.
- OpenAI Agents SDK implements this via input/output guardrails as first-class primitives and scoped handoffs.

### 4.2 Human-in-the-Loop Patterns

Gartner warns against binary governance ("fully locked down or fully trusted"). The solution is **tiered approval based on action impact**:

| Impact Level | Governance Model | Example |
|---|---|---|
| **Read-only / low-risk** | Fully autonomous | Searching knowledge base, summarizing documents |
| **Medium-risk writes** | Async approval with timeout | Sending customer emails, updating records |
| **High-risk / irreversible** | Synchronous human gate | Financial transactions, data deletion, production deployments |
| **Policy changes** | Separate human-initiated workflow | Changing agent permissions, modifying guardrails |

**Approval quality**: Approvals must arrive before the action, can realistically be refused, capture what the approver saw, and come from a person (never a shared inbox or rubber-stamp automation). Gartner's Shiva Varma: "Approvals can degrade under time pressure or approval fatigue, creating a false sense of safety."

**Framework support**:
- LangGraph: Checkpointing enables natural pause-for-approval at any node.
- OpenAI Agents SDK: Guardrails run concurrently with agent; tripwire mechanism halts execution on violation.
- Microsoft Agent Framework: Agent Harness includes approvals, context control, telemetry.

### 4.3 Audit Trails

Traditional audit logs record WHAT was accessed. Agent governance requires capturing **WHY** and **what decisions resulted**.

**Required structured fields per agent action**:
- Unique agent identifier
- Delegated permissions at time of action
- Specific tool or API invoked
- Governance policy decision (allowed/denied/escalated)
- Reasoning step the agent generated before acting (the reasoning trace)

**The reasoning trace is critical**: Difference between knowing an agent deleted a file and understanding WHY it believed that was correct. Regulators will expect reconstruction of what agents did, why, and with whose authorization.

**Framework support**: LangGraph (LangSmith tracing), OpenAI Agents SDK (built-in tracing), Microsoft Agent Framework (telemetry + observability).

### 4.4 Regulatory Landscape (as of Sep 2026)

- **EU AI Act**: Enforcement powers active Aug 2, 2026. Fines up to 35M EUR or 7% of global annual revenue.
- **ISO/IEC 42001**: First AI Management System standard (Dec 2023). AWS, Microsoft, SAP certified. Enterprise procurement increasingly requires it.
- **NIST NCCoE** (Feb 2026): Concept paper applying OAuth 2.0, Zero Trust (SP 800-207), and Digital Identity Guidelines (SP 800-63-4) to agent scenarios.

### 4.5 Multi-Agent Security Risks

Galileo AI (Dec 2025): A **single compromised agent poisoned 87% of downstream decision-making within 4 hours** -- faster than typical incident response. Implication: individual agent governance is insufficient; system-level circuit breakers and quarantine mechanisms are required.

---

## 5. Production Failure Modes

### 5.1 Taxonomy of Agent Failures

Berkeley and Stanford MAST taxonomy (March 2025): Analyzed 1,642 agent execution traces across 7 multi-agent frameworks. **Failure rates: 41% to 86.7%.**

Seven major failure modes documented across enterprise deployments:

| Failure Mode | Frequency | Description |
|---|---|---|
| **Tool misuse** | High | Wrong tool selected, incorrect parameters, misinterpreted results |
| **Context drift / hallucination cascades** | High | Agent loses track of context; hallucinations in step N corrupt all subsequent steps |
| **Goal drift** | Medium | Orchestrator loses sight of original objective during multi-step execution |
| **Infinite loops / step repetition** | 15.7% of all failures | Agent repeats same tool call with tiny parameter variations. IAL-Scan found 68 confirmed infinite loop failures across 47 projects in 6,549 repos. |
| **Silent quality degradation** | Hard to detect | Output quality drops without observable errors; no crash, just worse results |
| **Prompt injection** | Security-critical | Adversarial inputs hijack agent behavior |
| **Cascading multi-agent failures** | Catastrophic | One agent's error propagates through the system; 87% downstream poisoning in 4 hours (Galileo AI) |

### 5.2 Infinite Loop Mitigations

- **Hard step ceiling**: LangGraph's `recursion_limit` (default 25). Always set explicitly.
- **No-progress detection**: Kill repeated tool+argument calls (same tool, same or near-identical args).
- **Diminishing returns**: If last 3 iterations each improved output by <5%, terminate.
- **Token budget caps**: Hard ceiling on total tokens per agent run.
- **Cost**: An agent can burn through **$40 in API credits** running the same search query with slightly different phrasing.

### 5.3 Planning Failures and Replanning

- **Over-decomposition**: Orchestrator breaks simple tasks into too many subtasks, adding latency and cost without benefit.
- **Under-specification**: Plan steps are too vague for worker agents to execute correctly.
- **Plan rigidity**: Agent follows original plan even when observations contradict it.
- **Replanning strategies**: Magentic-One's outer/inner loop design -- outer loop revises the plan based on progress; inner loop executes current steps. LangGraph's conditional edges enable dynamic replanning.

### 5.4 Multi-Agent Coordination Failures

- **Communication overhead**: Inter-agent messages cost tokens bilaterally (sender generates, receiver processes).
- **Role confusion**: Agents with overlapping responsibilities duplicate work or drop tasks.
- **Accountability diffusion**: When multiple agents contribute to a decision, tracing responsibility for errors is difficult.
- **Deadlock**: Agents waiting on each other's outputs with no timeout mechanism.

### 5.5 Observability Requirements

Production agents need:
- Per-step latency and token counts
- Tool call success/failure rates
- Reasoning trace logging (not just inputs/outputs)
- Anomaly detection on loop counts, token consumption, and output quality metrics
- A]ert thresholds on cost per run, step count, and wall-clock time

---

## 6. Enterprise System Design Scenarios

### 6.1 Pattern Selection Decision Framework

```
START: Can a single LLM call solve this?
  YES -> L1: Direct API call. Stop.
  NO  -> Can you write down all steps before runtime?
    YES -> Is it a linear pipeline?
      YES -> Prompt Chaining
      NO  -> Are inputs categorically different?
        YES -> Routing
        NO  -> Can subtasks run independently?
          YES -> Parallelization (Sectioning or Voting)
          NO  -> Prompt Chaining
    NO  -> Is a single LLM sufficient with tools?
      YES -> Is the task open-ended?
        YES -> ReAct agent (default) + Reflection if quality insufficient
        NO  -> Plan-and-Execute
      NO  -> Do you need a central coordinator?
        YES -> Orchestrator-Workers (if single controller) or Supervisor multi-agent
        NO  -> Decentralized Handoff multi-agent
```

**The 70-80% trap**: A common mistake is reaching 70-80% with a simple approach and assuming the architecture needs upgrading. Usually the real issue is prompt quality or missing validation gates. (SystemDesign.one)

### 6.2 Production Architecture: Customer Support

Validated production domain (Anthropic). Characteristics: conversation + action, clear success criteria, feedback loops, human oversight.

```
User Input
  -> Routing (intent classification: general / refund / technical / escalation)
    -> [General] ReAct agent with knowledge base tools
    -> [Refund] Prompt chain: verify order -> check policy -> process refund (with human gate for high-value)
    -> [Technical] Orchestrator-Worker: diagnose issue, search docs, generate solution
    -> [Escalation] Handoff to human agent with full context
  -> Guardrails (parallel): content safety, PII detection, scope enforcement
  -> Evaluator: response quality check before delivery
```

### 6.3 Production Architecture: Coding Agent

Validated production domain (Anthropic, SWE-bench). Code solutions verifiable via automated tests.

```
Task Description
  -> Planning: decompose into file-level changes
  -> For each file: ReAct agent with code tools (read, edit, search, execute)
  -> Test execution: run tests, capture results
  -> Reflection: if tests fail, analyze errors, iterate
  -> Human review: final approval before merge
```

Key insight from Anthropic: "We spent more time optimizing our tools than the overall prompt." Tool design (Agent-Computer Interface / ACI) deserves as much attention as prompt engineering.

### 6.4 Trade-off Matrix

| Dimension | Workflows (L2) | Single Agent (L3) | Multi-Agent (L4) |
|---|---|---|---|
| **Predictability** | High (deterministic paths) | Medium (LLM-controlled) | Low (emergent behavior) |
| **Cost** | Low-moderate | Moderate-high (O(n^2) scaling) | High (3-100x multiplier) |
| **Latency** | Linear in steps | Variable, often 15-60s | Often 100s+ |
| **Debuggability** | High (trace each step) | Medium (reasoning traces help) | Low (multi-agent traces complex) |
| **Flexibility** | Low (fixed paths) | High (dynamic tool selection) | Highest (dynamic delegation) |
| **Failure modes** | Predictable (gate failures) | Moderate (loops, drift) | Unpredictable (cascading, deadlock) |
| **When to use** | Well-defined, decomposable tasks | Open-ended, tool-heavy tasks | Tasks exceeding single-agent capacity |

### 6.5 Microsoft Build 2026: Emerging Patterns

- **CodeAct**: Instead of one model call per tool, the model writes a short Python program calling several tools in one pass. Reduces round-trips for tool-heavy tasks.
- **Agent Harness**: Wraps tools, memory, plans, approvals, context control, telemetry, and web search around a model. Production scaffolding.
- **Hosted Agents**: Managed deployment with sandboxed sessions, persistent state, observability, and version control.

### 6.6 The A2A + MCP Stack

Emerging standard architecture for multi-agent enterprise systems:
- **MCP** (Model Context Protocol): Standardized model-tool integration. Agent-to-tool communication.
- **A2A** (Agent-to-Agent Protocol, Google): Standardized inter-agent communication. Microsoft Agent Framework supports both.
- Together they form the "TCP/IP of agents" -- a protocol stack separating tool access from agent coordination.

---

## Sources

1. [SystemDesign.one -- Agentic Design Patterns](https://newsletter.systemdesign.one/p/agentic-design-patterns)
2. [Anthropic -- Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)
3. [Anthropic -- Building Effective AI Agents (expanded PDF)](https://resources.anthropic.com/building-effective-ai-agents)
4. [Andrew Ng on X -- Four Design Patterns](https://x.com/AndrewYNg/status/1773393357022298617)
5. [DeepLearning.AI -- Agentic AI Course](https://learn.deeplearning.ai/courses/agentic-ai/lesson/rm9bg7/agentic-design-patterns)
6. [LangGraph -- Agent Orchestration Framework](https://www.langchain.com/langgraph)
7. [OpenAI Agents SDK -- Agents Documentation](https://openai.github.io/openai-agents-python/agents/)
8. [OpenAI Agents SDK -- Handoffs](https://openai.github.io/openai-agents-python/handoffs/)
9. [OpenAI -- Practical Guide to Building Agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)
10. [CrewAI Documentation](https://docs.crewai.com/en/concepts/agents)
11. [CrewAI GitHub](https://github.com/crewaiinc/crewai)
12. [Microsoft -- Magentic-One](https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/)
13. [Microsoft Agent Framework -- Magentic Orchestration](https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/orchestrations/magentic)
14. [AutoGen Explained (sanj.dev)](https://sanj.dev/post/autogen-microsoft-multi-agent-framework)
15. [Stevens Online -- Hidden Economics of AI Agents](https://online.stevens.edu/blog/hidden-economics-ai-agents-token-costs-latency/)
16. [arXiv -- Token Economics for LLM Agents](https://arxiv.org/html/2605.09104v1)
17. [COMPEL Framework -- Agentic AI Cost Modeling](https://www.compelframework.org/articles/agentic-ai-cost-modeling-token-economics-compute-budgets-and-roi)
18. [Augment Code -- AI Coding Cost Analysis](https://www.augmentcode.com/guides/ai-coding-cost-analysis-agent-token-spend)
19. [arXiv -- How Do AI Agents Spend Your Money](https://arxiv.org/abs/2604.22750)
20. [Trantor -- AI Agent Failure Modes](https://www.trantorinc.com/blog/ai-agent-failure-modes-what-goes-wrong-design-resilience)
21. [Zylos Research -- Agent Self-Healing and Failure Recovery](https://zylos.ai/research/2026-05-06-agent-self-healing-failure-recovery/)
22. [CodeCrux -- AI Agent Failure Recovery: Checkpoints, Timeouts](https://codecrux.com/blog/ai-agent-failure-recovery-checkpoints-timeouts-and-durable-execution-patterns/)
23. [NiteAgent -- Multi-Agent Failure Modes](https://niteagent.com/blog/multi-agent-failure-modes-7-patterns-that-break-production-systems/)
24. [Galileo -- 7 AI Agent Failure Modes](https://galileo.ai/blog/agent-failure-modes-guide)
25. [Zylos Research -- AI Agent Governance & Compliance 2026](https://zylos.ai/research/2026-05-01-ai-agent-governance-compliance-2026/)
26. [Gartner -- Uniform Governance Across AI Agents](https://www.gartner.com/en/newsroom/press-releases/2026-05-26-gartner-says-applying-uniform-governance-across-ai-agents-will-lead-to-enterprise-ai-agent-failure)
27. [Sennovate -- AI Agent Authorization Governance](https://sennovate.com/blog/ai-agent-authorization-governance-enterprise-security-in-2026/)
28. [arXiv -- Agentic Design Patterns: A System-Theoretic Framework](https://arxiv.org/html/2601.19752v1)
29. [Augment Code -- Agentic Design Patterns 2026 Catalog](https://www.augmentcode.com/guides/agentic-design-patterns)
30. [The AI Engineer -- ReAct vs Plan-and-Execute vs ReWOO vs Reflexion](https://theaiengineer.substack.com/p/the-4-single-agent-patterns)
31. [ServicesGround -- Agentic Reasoning Patterns 2026](https://servicesground.com/blog/agentic-reasoning-patterns/)
