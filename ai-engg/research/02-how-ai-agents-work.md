# Research: How AI Agents Work

**Date researched**: 2026-09-29
**Sources consulted**: 34

---

## 1. System Topology & Mechanics

### 1.1 What Makes an Agent an Agent

An agent is distinguished from a chatbot by its ability to produce **side effects** -- changing the world rather than just generating text. Three defining characteristics (systemdesign.one):

1. **Autonomy** -- determines intermediate steps independently without a pre-written script.
2. **Proactiveness** -- recognizes missing information and takes initiative; requires explicit design of uncertainty thresholds and "ask-for-help" triggers.
3. **Action** -- executes operations through defined tool interfaces (APIs, browsers, file systems).

The **PEAS framework** (from classical AI) specifies agent design:
- **Performance**: measurable success criteria (e.g., "flight booked under $500, confirmation received").
- **Environment**: operational boundaries -- which APIs, systems, data sources are in/out of scope.
- **Actuators**: available tools/capabilities (search, click, fill forms, purchase, send emails).
- **Sensors**: observation mechanisms (parsing API responses, reading page content, detecting confirmation numbers).

The LLM functions as a **processor, not a knowledge base**. It proposes next steps and interprets results but does not store facts. Current prices, seat availability, and policies come from tools and memory systems.

### 1.2 Core Execution Patterns

#### ReAct Loop (Reason + Act)
The dominant execution engine for single-agent systems:

```
Reason --> Act --> Observe --> Reason --> Act --> Observe --> ... --> Done
```

Each step depends on prior observations -- no pre-written script. The agent reasons about what to do next, acts by calling a tool, observes the result, and loops. Key weakness: early mistakes compound through later steps. Mitigation requires checkpoints, structured plans for known phases, and verification before irreversible actions.

#### Plan-and-Execute
Agent generates a complete plan upfront, then executes sequentially. Advantage: fewer LLM round-trips, reviewable plan. Disadvantage: rigid -- cannot adapt to unexpected findings mid-execution.

#### Hybrid Approach (Recommended in Practice)
Structured plan with checkpoints; ReAct reasoning within each phase:
- Phase 1: "Search and filter" -- ReAct handles messy search results.
- Phase 2: "Book and confirm" -- ReAct handles payment flow and error recovery.
- Human/system verification checkpoint between phases.

### 1.3 State Orchestration Models

#### DAG-Based Graphs (LangGraph)
LangGraph is built around a directed graph structure with explicit state flow control. Nodes are Python functions that receive current state and return state updates. A multi-agent graph typically has:
- A **router node** (decides which specialist to invoke)
- Several **specialist agent nodes**
- A **supervisor/reducer node** (merges outputs into coherent state)

State management: every node receives the full state dictionary. If messages are appended indefinitely, a supervisor running 20 steps sends thousands of accumulated tokens per node call. Mitigation: periodic summarization or slicing messages to last N entries.

Production stack (LangGraph v0.2.5): Redis Streams queue + containerized sub-agent workers + PostgreSQL (JSONB) state store + OpenTelemetry. Trade-off: **620 ms -- 1.8 s routing latency per hop** in exchange for fault isolation and independently retryable sub-agents.

#### Supervisor-Worker Pattern
The production standard for multi-agent systems. 57% of organizations with multi-agent systems in production use some variant (2026 industry survey). One orchestrator receives the task, decomposes it, routes to specialists, and synthesizes the result. Each worker knows exactly what it is responsible for and nothing else.

Topology guidance:
- **Hub-and-spoke**: works for up to ~6 workers.
- **Hierarchical**: needed for 6+ workers or when workers need their own sub-teams.
- Skip the supervisor design if you have fewer than 3 distinct agent capabilities, no external tool calls, or an end-to-end latency budget under 2 seconds.

The #1 production bug in naive supervisor implementations: a worker returns empty, supervisor treats it as success, final output is silently broken. Fix: Pydantic output validation at every worker boundary.

### 1.4 Agent-to-Agent Communication Protocols

#### MCP (Model Context Protocol) -- Agent-to-Tool
Open standard by Anthropic (November 2024), donated to Linux Foundation's Agentic AI Foundation (December 2025). Defines how AI agents connect to external tools, data sources, and services. Enables standardized, secure, two-way connections between data sources and AI tools. Key 2025-2026 milestones:
- One-click local installation on Claude Desktop (June 2025).
- Remote MCP connectors (January 2026).
- November 2025 spec update added: asynchronous operations, formal server identity verification, structured audit trails.
- **Tool Search Tool**: discovers tools on-demand instead of loading all definitions upfront. 85% reduction in token usage. Opus 4 improved from 49% to 74% accuracy; Opus 4.5 from 79.5% to 88.1% with Tool Search enabled.

#### A2A (Agent2Agent Protocol) -- Agent-to-Agent
Open standard by Google (April 2025), donated to Linux Foundation (June 2025). Enables AI agents to discover, authenticate, and delegate tasks across platforms/frameworks.

Core architecture:
- **Agent Card**: standardized capability advertisement (JSON).
- **Task Object**: well-defined work unit over JSON-RPC 2.0.
- **Artifacts**: typed structured outputs (text, files, data).
- **Push notifications or SSE** for progress streaming.
- Supports long-running tasks with defined lifecycle states: pending, in-progress, completed, failed.

A2A v1.2 (2026): signed agent cards using cryptographic signatures for domain verification. 150+ organizations in production. GitHub repo: 22,000+ stars. SDKs in 5 languages (Python, JavaScript, Java, Go, .NET).

**MCP vs. A2A**: Complementary, not competitive. MCP gives an agent its "hands" (tool access). A2A gives a team of agents the ability to coordinate. The enterprise stack converges on: A2A for coordination, MCP for tool access, shared context layer for governed business knowledge.

### 1.5 Framework Comparison

| Dimension | LangGraph | OpenAI Agents SDK | CrewAI |
|-----------|-----------|-------------------|--------|
| **Abstraction level** | Low-level graph primitives | Mid-level, opinionated 4-primitive API | High-level role-based |
| **Orchestration** | DAG with cycles, conditional edges | Runner-driven agentic loop | Sequential or hierarchical processes |
| **Agent transfer** | Subgraphs with shared/isolated state | Handoffs (full conversation transfer) | Delegation via manager agent |
| **State persistence** | Checkpointer (Postgres, SQLite, Redis) | Session-level + long-horizon harness (Apr 2026) | SQLite-backed Flow persistence |
| **Guardrails** | Custom middleware (v1.1, Dec 2025) | Input/Output/Tool guardrails (built-in) | Tool-level; via Bedrock integration |
| **Observability** | OpenTelemetry integration | Built-in tracing to OpenAI dashboard | Logging; enterprise dashboard |
| **Ideal for** | Complex stateful workflows | Rapid multi-agent with handoffs | Role-based team simulation |

**OpenAI Agents SDK specifics**: Built on 4 primitives -- Agents, Tools, Handoffs, Guardrails. The Runner handles the call-tool-respond cycle including multi-step chains. Every `Runner.run` produces a trace (model calls, tool invocations, handoffs, guardrail checks) uploaded to the OpenAI dashboard automatically. Handoffs transfer the entire conversation (not just a result) to another agent. Input filters can modify conversation history during handoff. April 2026 update added sandboxing for untrusted code and a long-horizon harness for multi-day tasks.

**CrewAI specifics**: Five primitives -- Agents, Tools, Tasks, Processes, Crews. Agents defined by role, goal, backstory. Built-in delegation: agents can autonomously assign tasks to others based on capabilities. With knowledge base integration, delegation accuracy improved from 0.33 to 0.73 and process success from 45.3% to 72.9%.

### 1.6 Tool Use Mechanics

LLMs produce text only -- tools enable real-world action via native tool calling:

1. Define allowed functions as JSON schemas in the API request (name, description, parameters with types).
2. LLM outputs structured JSON tool call with parameters.
3. External code validates (field presence, type correctness), executes the real API call, returns results.

Failure modes:
- **Hallucinated parameters**: agent invents options not in the schema. Schema validation catches these.
- **Hallucinated tool invocations**: agent calls tools that do not exist in its registered set.
- **Silent failures**: tools return HTTP 200 with empty payloads -- most damaging because no error surfaces.
- **Context window truncation**: long histories push tool definitions past effective attention range, producing redundant/contradictory calls.

Tool-calling fails **3--15% of the time** in production, varying by model size and task complexity.

---

## 2. Token Economics & NFR Metrics

### 2.1 The Cost Problem

Token prices fell ~80% between 2025 and 2026, yet enterprise AI bills went up. Average inference spend represents 85% of enterprise AI budgets. 60% of AI projects exceed original cost estimates by 30--50%.

Agents make **3--10x more LLM calls** than simple chatbots. A single user request can trigger planning, tool selection, execution, verification, and response generation -- easily 5x the token budget of a direct chat completion. An unconstrained agent solving a software engineering task can cost **$5--8 per task** in API fees alone. Gartner (March 2026): agentic models require **5--30x more tokens per task** than a standard chatbot.

Incident: In November 2025, two LangChain-based agents entered an infinite conversation cycle that ran for 11 days, generating a **$47,000 bill** before detection.

### 2.2 Latency SLA Benchmarks

#### TTFT (Time to First Token) -- 2026 Data

| Model Class | P50 | P95 | P99 |
|-------------|-----|-----|-----|
| GPT-5.5 / Claude Opus 4.7 / Gemini 3 Pro | 0.85--1.4 s | 1.6--2.4 s | ~3.2 s |
| Reasoning (Claude Opus 4.7 max thinking) | ~28 s | -- | -- |
| Reasoning (GPT-5.5 Pro high) | ~67 s | -- | -- |
| Reasoning (Gemini 3 DT high) | ~52 s | -- | -- |

P95 inflates **1.6--3.2x over P50** in 2026. The P95/P50 ratio averages 2.1x, with worst pairings hitting 3.2x. SLO design should anchor on P95.

Provider example (Baseten, Sep 2026): P99 end-to-end = 3.23 s, median = 331 ms, TTFT P95 = 1.16 s, task success = 95.8%.

**UX thresholds**: Chat UX requires sub-2 s P95 TTFT (defensible bar), sub-1 s (premium bar). Streaming throughput: 50 TPS feels slow, 100 TPS normal, 200+ TPS instant.

**Gateway/guardrails overhead**: +11 ms P50, +14 ms P95, +21 ms P99. Negligible against 1,000+ ms baseline LLM calls.

**Regional impact**: US-East to APAC adds 180--220 ms TTFT; EU to US-East adds 80--110 ms.

### 2.3 Token Cost Formulas

**Per-execution cost model**:
```
Cost = (input_tokens * input_price + output_tokens * output_price) * avg_steps_per_task
```

For a 5-step agent task using GPT-4o ($2.50/$10.00 per 1M tokens), with ~2,000 input + ~500 output tokens per step:
```
= (2000 * $0.0000025 + 500 * $0.00001) * 5 = ($0.005 + $0.005) * 5 = $0.05 per task
```

At 1,000 executions/day = **$50/day** or **$1,500/month** unoptimized.

An unconstrained complex agent (software engineering): **$5--8 per task** at premium model rates.

### 2.4 Prompt Caching Mechanics

**Exact-prefix caching** (provider-level): Stores computed KV tensors behind a repeated prompt prefix. The static portion (tool definitions, system prompt, reference docs) bills at up to **90% discount**. No quality trade-off -- byte-identical output.

- Anthropic: up to 90% cost reduction on cached prefixes, 13--31% TTFT improvement.
- OpenAI: 50% discount on cached prompt content.
- ProjectDiscovery: 59% cost reduction, reaching 70% over 10 days, ~74% cache hit rate.
- One developer: $720/month to $72/month (90% reduction) via prompt caching alone.

**Best practice for prompt ordering**: Static content first (system instructions, persona, few-shot examples) --> heavy context (large documents) --> dynamic content last (user query, conversation history).

### 2.5 Semantic Caching

Application-level caching that handles semantically equivalent queries via vector similarity search. Returns cached response directly without hitting the LLM. ~31% of LLM queries across typical workloads exhibit semantic similarity.

### 2.6 Agentic Plan Caching

NeurIPS 2025 paper: **50.31% cost reduction** while maintaining **96.61% of baseline performance**, plus 27.28% latency reduction. Insight: traditional chatbot-style semantic caching fails for agents because outputs depend on external data. Plan caching extracts reusable plan templates from previously solved tasks.

### 2.7 Dynamic Model Routing

Route easy tasks to cheap models, complex tasks to premium ones. The 100--300x cost differential between premium and small model tiers is the primary leverage point.

- RouteLLM (UC Berkeley, Anyscale, Canva; ICLR 2025): **85% cost reduction** while maintaining **95% of GPT-4 performance** using a trained routing classifier.
- Moving 70% of requests from GPT-4-class to GPT-3.5-class: ~60% LLM cost reduction.
- OpenAI's GPT-4o explicitly routes between fast/efficient and deeper reasoning models.

**Cascade architecture**: semantic cache check (100% savings) --> complexity classification to route simple tasks to lightweight models --> escalation on failure (retry with next tier). Treats expensive inference as last resort.

### 2.8 Combined Optimization Impact

Caching + routing together routinely delivers **70--85% cost reduction** on unoptimized baselines. One practitioner documented 90% total reduction through combined RAG optimization, prompt compression, and context pruning.

**Budget governance**: Enforce at three layers:
1. **Session ceiling**: max tokens/dollars per individual session; terminates immediately when reached.
2. **Per-user/per-feature quotas**: daily/weekly caps attributed to specific features.
3. **Organizational spending limits**: alerting at 60%, hard stop at 100%.

**Tooling**: LiteLLM, Portkey, OpenRouter support multi-model routing, semantic caching, budget enforcement, and failover out of the box.

---

## 3. Distributed Resilience & State

### 3.1 Durable Execution Patterns

Temporal raised $300M at a $5B valuation (February 2026), with 9.1 trillion lifetime action executions -- 1.86 trillion from AI-native companies. OpenAI runs Temporal for Codex, handling millions of production coding agent requests daily.

Two core mechanisms dominate:
- **Journal-based replay**: record each completed step; replay on crash. A new worker replays event history from the beginning -- every completed Activity call is skipped (reads recorded result from history). When replay reaches the last completed step, normal execution resumes.
- **Database checkpointing**: persist state after each node (e.g., LangGraph checkpointers with Postgres/SQLite).

**Critical constraint**: Workflow code must avoid non-deterministic operations (random numbers, timestamps, direct network calls). Those must be wrapped as Activities whose results are recorded. Pattern: anything touching the outside world is an Activity; everything else is deterministic workflow logic.

### 3.2 Checkpointing vs. Durable Execution

A key architectural distinction:
- **Checkpointer**: saves state at developer-marked points; developer owns retry, resume, and side-effect deduplication.
- **Durable execution**: the runtime owns retry, resume, and dedup; the developer writes ordinary code.

LangGraph checkpointing protects against **application-level** failures (bad reasoning, incorrect branches, HITL pauses). Temporal protects against **infrastructure-level** failures (container crashes, network partitions, host preemptions). Production deployments often need both.

Analysis from production: checkpointing cuts wasted processing by **60%+** on multi-step workflows.

### 3.3 Competing Durable Execution Platforms (2026)

| Platform | Mechanism | Differentiator |
|----------|-----------|----------------|
| **Temporal** | Journal/replay, multi-language SDKs | Mature; 3,000+ customers incl. Nvidia, Netflix, OpenAI |
| **Restate** | Journal/replay, lighter footprint | Same mechanism as Temporal, simpler deployment |
| **DBOS** | All state in Postgres | Zero explicit checkpoint instrumentation; any decorated function is durable |
| **Inngest** | Durable execution for serverless | Steps, waits, retries without infra management |

Key platform announcements:
- AWS Lambda Durable Functions (December 2025).
- Microsoft Durable Task for AI agents (April 2026).
- OpenAI Agents SDK + Temporal Python SDK GA integration (March 2026).
- Temporal Replay 2026: Serverless Workers, Standalone Activities, Workflow Streams.

### 3.4 Event Sourcing for AI Agents

Stores an immutable, append-only log of events rather than current state. Current state derived by replaying all events. Featured at DDD Europe 2025, Data Mesh Live 2025, and EventCentric.eu 2025 as "Event Sourcing: The Backbone of Agentic AI."

For long-running workflows, event histories grow unbounded. Temporal's **Continue-As-New**: when history grows too large, atomically complete current run and start a new run with the same workflow ID, carrying forward only essential state.

### 3.5 Circuit Breakers, Rate-Limiting, Fallbacks

- LangGraph v1.1 (December 2025): model retry middleware with configurable exponential backoff.
- LangGraph does not catch exceptions by default -- a tool call that raises `TimeoutError` crashes the graph with no recovery path. Explicit error boundaries required.
- Hard step ceiling recommended (e.g., LangGraph `recursion_limit`, default 25) combined with no-progress detection (kill repeated tool+argument calls).
- Retry storms in multi-agent systems: 10 agents each retrying 10 times can hit a dead service with 100 requests. Solution: exponential backoff with jitter, shared circuit breaker state.
- Deadlocks from circular dependencies in agent graph combined with no timeout configuration. Most frameworks default to infinite blocking reads.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP Architecture

Traditional Zero Trust breaks for AI agents because agents are non-deterministic, goal-interpreting entities that select their own tools, chain API calls, spawn sub-tasks, and disappear when done. Scoping agent access with static RBAC is "like trying to govern a conversation with a list of approved words."

**Anthropic's Managed Agents** (April 2026) split every agent into three mutually untrusted components:
- **Brain**: Claude and the harness routing decisions.
- **Hands**: disposable Linux containers where code executes.
- **Session**: append-only event log outside both.

Credentials never enter the sandbox. OAuth tokens stored in external vault. When the agent needs to call an MCP tool, it sends a session-bound token to a dedicated proxy. The proxy fetches real credentials, makes the external call, returns results. The agent never sees the actual token.

**NVIDIA NemoClaw**: five enforcement layers between agent and host. Sandboxed execution with Landlock, seccomp, and network namespace isolation at kernel level. Default-deny outbound networking -- every external connection requires explicit operator approval via YAML policy.

**Zentera Enclave Model**: trust boundary containing sandboxed agents, authorized assets, and scoped tools. Agent in Project A's enclave cannot reach Project B's assets, tools, or agents. Prompt-layer controls tell an agent what it should not do; an enclave enforces what it cannot reach.

### 4.2 Tool-Level RBAC & Least Agency

The concept of **"least agency"** extends beyond least privilege: least privilege asks what an agent may read; least agency asks what it may do. An agent that summarizes documents should not hold the ability to send mail, even if it may lawfully read the mailbox.

Xage governs AI agents through operational lifecycle: secure digital identity, agent-specific access policies, role-based permissions, time-bound privileges. Policies adjustable as agents connect to new tools without losing historical visibility.

Cisco Zero Trust Access for AI agents: holds each agent accountable to a human employee. Duo IAM capabilities integrate with MCP policy enforcement and intent-aware monitoring.

**Cloud Security Alliance Agentic Trust Framework (ATF)**: treats agent autonomy as something earned through demonstrated trustworthiness. Four maturity levels with progressively greater autonomy and governance requirements. Adds behavioral anomaly detection, PII protection, RBAC, and error tracking.

### 4.3 PII Redaction

- Xage: automatic identification and protection of PII, intellectual property, confidential business data at access boundaries.
- Content guardrails: PII detection, secrets detection, redaction with native checks and external providers (AWS Bedrock Guardrails, Azure Content Safety).
- Lakera Guard (acquired by Check Point, September 2025): inspects prompts and responses for injection, jailbreak, PII, and exfiltration patterns at runtime.

### 4.4 Sandbox Isolation

Anthropic Managed Agents: disposable Linux containers; credentials proxied through vault.
NVIDIA NemoClaw: Landlock + seccomp + network namespace isolation.
Zentera: enclave-based project isolation.
OpenAI Agents SDK (April 2026): sandboxing primitive for safely executing untrusted code.

### 4.5 Structured Audit Logs

November 2025 MCP spec update added structured audit trails as a formal capability. Enterprise requirement: every tool access, data retrieval, and user trigger must be logged with full provenance.

Anthropic's principal hierarchy: Anthropic --> operators --> users. System designed for humans to observe actions in real time, approve/reject operations, interrupt in-progress work, and audit after the fact.

### 4.6 Industry Standards

- **OWASP GenAI Security Project**: Top 10 for Agentic Applications (2026 edition), updated to v2.01 in June 2026 -- first OWASP flagship list for software that acts rather than models that power it.
- **NIST SP 800-207** extended by Dr. Chase Cunningham's "Agentic Zero Trust" research: Token Isolation Pattern, Agent Persona framework, Behavioral Identity.
- Only **14.4%** of organizations reported full security approval for their entire agent fleet (Gravitee, February 2026, N=919).
- **97%** of organizations with AI-related breaches lacked proper AI access controls (IBM 2025 Cost of a Data Breach Report).

---

## 5. Production Failure Modes

### 5.1 Context Window Degradation

Over long sessions, the agent's internal representation of the original task compresses, earlier constraints get deprioritized, and the agent reasons against a progressively incomplete picture.

- **Attention decay**: as conversation grows, "weight" of initial system prompt diminishes relative to recent tokens. The model prioritizes immediate context over static rules defined 50 turns ago.
- **Working-memory rot** (EPAM): gradual degradation/corruption of an agent's active runtime memory during long tasks. Self-inflicted corruption driven by the agent's own execution trace -- distinct from prompt-decay.
- **Specification drift**: by ~20th turn, agent may optimize for something adjacent to the original task with no structural signal.
- **Context overflow**: loop fills its own context window with accumulated history until calls truncate or fail. Fix: summarize history at fixed intervals. Risk: an OpenClaw agent mass-deleted a user's inbox during context compaction because the safety instruction was dropped from active context.

### 5.2 Infinite Loops

IAL-Scan examined 6,549 LLM agent repositories: **68 confirmed infinite agentic loop failures across 47 projects** (91.9% precision). Not a corner case -- it is shipped to production regularly.

Two distinct patterns:

| Pattern | Trigger | Behavior | Resource Impact |
|---------|---------|----------|-----------------|
| **Infinite retry loop** | Failed tool response with no recognition | Same tool call fires repeatedly with identical inputs | Token budget and compute exhausted |
| **Agent paralysis** | Contradictory signals or unsatisfiable criteria | No tool call fires; no output produced | Downstream systems stall silently |

Root cause: agents are given objectives but not exit conditions. They oscillate between spinning when they should stop and stopping when they should escalate.

**The Polling Tax**: agent enters hyperactive status-check loop instead of waiting for a webhook. Worst case: hundreds of API calls for a single task while user sees only "Thinking..."

Detection: monitor **step counts and output similarity** across turns, not just latency/error rates. A looping agent may never throw an error while burning compute on identical retries.

### 5.3 State Drift & Error Propagation

Multi-agent LLM systems fail **41--86% of the time** in production depending on task complexity. Even with well-trained individual agents, end-to-end reliability degrades fast: five agents at 95% individual accuracy deliver roughly **77% overall success** (0.95^5).

Three propagation patterns:
1. **Factual drift**: one agent confidently states something incorrect; next agent treats it as ground truth.
2. **Context window poisoning**: bad tool result appended to shared scratchpad contaminates every future call.
3. **Cascading retries**: downstream agent detects problem, triggers self-correction, which spawns more failing tool calls.

Errors typically pass through **3--4 reasoning layers** before surfacing. A single hallucination forwarded to 3 subagents produces 3 wrong answers, each with apparent coherence.

### 5.4 Hallucinated Tool Parameters

Tool misuse and incorrect tool arguments account for approximately **31%** of production failures (2024--2025 data). Four primary types:
1. **Schema violations**: wrong types, wrong key names, omitted required fields.
2. **Hallucinated tool invocations**: calls to tools not in the registered set.
3. **Context-truncated calls**: long histories push tool definitions past effective attention range.
4. **Silent failures**: HTTP 200 with empty payloads -- most damaging because no error surfaces.

### 5.5 Cascading API Timeouts

- Root cause: circular dependencies in agent graph + no timeout configuration. Most frameworks default to infinite blocking reads.
- Retry storms: 10 agents x 10 retries = 100 requests hitting a dead service.
- Worker cascade failure: worker returns empty, supervisor treats as success, final output silently broken.

### 5.6 Prompt Injection in Agentic Contexts

Two patterns:
- **Direct injection**: targets system prompt or user input, overwriting instructions.
- **Indirect injection**: embeds malicious instructions in externally retrieved content (RAG poisoning, tool output manipulation).

Defenses (each with limitations):
1. Input sanitization -- catches known patterns, fails against novel phrasing.
2. Privilege separation -- limits tool access by context, reduces blast radius.
3. Instruction hierarchy enforcement -- probabilistic, not a hard boundary.
4. Runtime output review -- separate model inspects actions before execution; closest to enforcement.

### 5.7 Properties of Production-Ready Agents

1. **Bounded execution**: defined step limits, token budgets, wall-clock timeouts on every execution path. Calibrate against observed p99 session lengths.
2. **Idempotent tool design**: tools produce same result whether run once or three times. Use deduplication keys or transactional locks.
3. **Propagation awareness**: check outputs at each step before passing forward; defined fallback when checks fail.

---

## 6. Enterprise System Design Scenarios

### 6.1 Real-World Scale Benchmarks

- **AgentArch benchmark** (ServiceNow, 2025): even top models achieve only **35.3% success on complex enterprise tasks**. Examines orchestration strategy, prompt implementation (ReAct vs function calling), memory architecture, and thinking tool integration.
- **LangChain State of AI Agents 2025**: 57% of organizations now have AI agents in production. Quality (not cost) cited as primary barrier to deployment.
- **IDC/AWS survey** (November 2025, N=900+): only **3% of companies** successfully scaling agentic AI across multiple departments, even as 62% actively experiment.
- Multi-agent architectures achieve **45% faster problem resolution** and **60% more accurate outcomes** compared to single-agent systems.
- Project Mariner (Google DeepMind): 83.5% on WebVoyager benchmark, handles 10 concurrent tasks on cloud VMs.

### 6.2 Published Architecture Case Studies

| Organization | Architecture | Outcome |
|-------------|-------------|---------|
| **Wells Fargo** | Agent-assisted banking | 35,000 bankers access 1,700 procedures in 30 s (was 10 min) |
| **Morgan Stanley** (DevGen.AI) | Code review agent | 9M+ lines reviewed, ~280,000 developer hours saved |
| **PwC** | CrewAI workflows | Code-generation accuracy: 10% --> 70% |
| **Uber, LinkedIn, AppFolio** | LangGraph-based agents | 10+ hours saved weekly, sub-3-minute research results |
| **Tyson Foods / Gordon Food Service** | A2A cross-org agents | Real-time supply chain data sharing for sales and logistics |
| **OpenAI (Codex)** | Temporal + Agents SDK | Millions of daily coding agent requests with durable execution |

### 6.3 ROI Benchmarks by Industry (2025--2026)

| Industry | Cost Reduction | Time to Positive ROI |
|----------|---------------|---------------------|
| Financial Services | 25--45% | 4--7 months |
| Healthcare | 20--35% | 5--9 months |
| Manufacturing | 15--30% | 6--10 months |
| Retail / E-commerce | 30--50% | 3--6 months |
| B2B Technology | 25--40% | 3--5 months |

### 6.4 Trade-Off Matrices

#### Orchestration Pattern Selection

| Factor | Single Agent | Supervisor-Worker | Hierarchical Multi-Agent |
|--------|-------------|-------------------|--------------------------|
| Latency | Lowest (<2 s) | Medium (620 ms--1.8 s per hop) | Highest (multiple hops) |
| Complexity ceiling | Low (~3 tools) | Medium (~6 workers) | High (nested teams) |
| Failure attribution | Simple | Clear (per-worker) | Complex (nested failures) |
| Cost per task | 1x | 2--3x (routing overhead) | 3--5x |
| When to use | Simple tasks, tight latency | Most production systems | Cross-domain, high-parallelism |

#### State Persistence Selection

| Factor | In-Memory Only | LangGraph Checkpointer | Temporal Durable Execution |
|--------|---------------|----------------------|---------------------------|
| Protects against | Nothing (stateless) | App-level failures, HITL pauses | Infra failures, container crashes |
| Resume capability | None | From last checkpoint | From any completed step |
| Operational overhead | None | Database (Postgres/Redis) | Temporal cluster or Temporal Cloud |
| When to use | Stateless single-turn | Multi-turn with human review | Mission-critical, long-running |

#### Security Model Selection

| Factor | Prompt-Only Controls | External Policy Gateway | Full Enclave Isolation |
|--------|---------------------|------------------------|----------------------|
| Enforcement | Probabilistic | Deterministic at boundary | Deterministic + network-level |
| Blast radius | Unbounded | Limited by gateway rules | Contained to enclave |
| PII protection | Depends on model | Redaction at boundary | Data never enters sandbox |
| Complexity | Trivial | Moderate | High |
| When to use | Internal tools, low risk | Production with external APIs | Regulated industries, untrusted code |

### 6.5 Key Design Lessons (2026)

- Start simple with a ReAct loop. Add plan decomposition where tasks are long. Add layered memory before context sprawl becomes failure. Add runtime safety wrappers before the first production tool call. Scale into supervisor-worker only when the task graph is actually parallel.
- The central engineering lesson of 2026: agents are strong enough to automate well-scoped loops, but still weak at self-managing ambiguous, long-horizon work.
- ~95% of generative AI pilots stall due to flawed enterprise integration, not issues with AI models themselves (MIT/NANDA, August 2025).
- 55% of organizations cite lack of skilled personnel as the greatest implementation challenge (IDC 2025).
- The organizations reaching production in 2026 are those that invested in principled architectural decisions before writing agent code.

---

## Sources

- [1] [System Design Newsletter -- AI Agents Explained](https://newsletter.systemdesign.one/p/ai-agents-explained) -- Core agent mechanics, ReAct loop, tool use, memory systems
- [2] [LangGraph Multi-Agent Orchestration Guide (Latenode)](https://latenode.com/blog/ai-frameworks-technical-infrastructure/langgraph-multi-agent-orchestration/langgraph-multi-agent-orchestration-complete-framework-guide-architecture-analysis-2025) -- LangGraph architecture, supervisor-worker, state management
- [3] [LangGraph Agents in Production (AlphaBold)](https://www.alphabold.com/langgraph-agents-in-production/) -- Production costs, real-world outcomes
- [4] [LangGraph Multi-Agent Orchestration Enterprise 2026 (Gheware)](https://devops.gheware.com/blog/posts/langgraph-multi-agent-orchestration-enterprise-2026.html) -- Enterprise patterns, 7 orchestration patterns
- [5] [Supervisor Agent Architecture (Markaicode)](https://markaicode.com/architecture/supervisor-agent-architecture/) -- Supervisor pattern production design
- [6] [OpenAI Agents SDK Documentation](https://openai.github.io/openai-agents-python/) -- Official SDK reference
- [7] [OpenAI Agents SDK Deep Dive (CallSphere)](https://callsphere.ai/blog/openai-agents-sdk-deep-dive-agents-tools-handoffs-guardrails-2026) -- Tools, handoffs, guardrails, tracing
- [8] [OpenAI Agents SDK Review (Mem0)](https://mem0.ai/blog/openai-agents-sdk-review) -- Features, tools, memory
- [9] [Mastering OpenAI Agents SDK (Cohorte)](https://cohorte.co/blog/mastering-the-openai-agents-sdk-a-field-guide-for-busy-developers-ai-vps) -- Field guide for guardrails cost/latency
- [10] [AI Agent Failure Modes (Openlayer)](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation) -- Tool-calling errors, infinite loops, error propagation
- [11] [AI Agent Failure Modes in Production (Trantor)](https://www.trantorinc.com/blog/ai-agent-failure-modes-what-goes-wrong-design-resilience) -- Context degradation, design resilience
- [12] [AI Agent Failure Detection Guide (Latitude)](https://latitude.so/blog/ai-agent-failure-detection-guide) -- Observability-driven diagnosis framework
- [13] [7 AI Agent Failure Modes (Galileo)](https://galileo.ai/blog/agent-failure-modes-guide) -- Failure taxonomy and prevention
- [14] [Common AI Agent Failures (Arize)](https://arize.com/blog/common-ai-agent-failures/) -- Field analysis of production failures
- [15] [Multi-Agent Failure Modes (NiteAgent)](https://niteagent.com/blog/multi-agent-failure-modes-7-patterns-that-break-production-systems/) -- 7 patterns that break production systems
- [16] [AI Agent Failure Modes Enterprise (EPAM)](https://www.epam.com/insights/ai/blogs/ai-agent-failure-modes-enterprise) -- Working-memory rot, 21+ failure modes
- [17] [Anthropic Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use) -- Tool Search Tool, on-demand tool discovery
- [18] [MCP Enterprise Deployment Guide (MintMCP)](https://www.mintmcp.com/blog/enterprise-development-guide-ai-agents) -- Enterprise MCP architecture
- [19] [Anthropic MCP Introduction](https://www.anthropic.com/news/model-context-protocol) -- Protocol specification, architecture
- [20] [Anthropic Managed Agents (MindStudio)](https://www.mindstudio.ai/blog/what-is-anthropic-managed-agents) -- Sandbox isolation, credential proxying
- [21] [AI Agent Cost Optimization (Zylos)](https://zylos.ai/research/2026-02-19-ai-agent-cost-optimization-token-economics/) -- Token economics, FinOps
- [22] [AI Agent Cost Optimization: Token Budgets (Zylos)](https://zylos.ai/research/2026-04-12-ai-agent-cost-optimization-token-budget-model-routing/) -- Model routing, production FinOps
- [23] [Prompt Caching 2026 (Digital Applied)](https://www.digitalapplied.com/blog/prompt-caching-2026-cut-llm-costs-engineering-guide) -- Prompt caching mechanics
- [24] [Durable Execution for AI Agents (Zylos)](https://zylos.ai/research/2026-04-24-durable-execution-agent-runtimes/) -- Checkpointing, replay, recovery
- [25] [Durable AI Agents with Temporal (NiteAgent)](https://niteagent.com/blog/2026-06-29-durable-ai-agents-temporal-guide/) -- Crash-proof long-running workflows
- [26] [Durable Execution Meets AI (Temporal)](https://temporal.io/blog/durable-execution-meets-ai-why-temporal-is-the-perfect-foundation-for-ai) -- Temporal for AI agents
- [27] [Zero Trust for AI Agents (VentureBeat)](https://venturebeat.com/security/ai-agent-zero-trust-architecture-audit-credential-isolation-anthropic-nvidia-nemoclaw) -- Credential isolation, Anthropic/NVIDIA architectures
- [28] [Agentic Trust Framework (Cloud Security Alliance)](https://cloudsecurityalliance.org/blog/2026/02/02/the-agentic-trust-framework-zero-trust-governance-for-ai-agents) -- ATF zero trust governance
- [29] [A2A Protocol Announcement (Google)](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/) -- Agent2Agent protocol specification
- [30] [A2A Protocol 2026 Adoption (Glukhov)](https://www.glukhov.org/ai-systems/comparisons/a2a-protocol-2026-adoption/) -- Adoption data, 150+ orgs
- [31] [AgentArch Benchmark (arXiv)](https://arxiv.org/html/2509.10769v1) -- Enterprise agent architecture benchmark
- [32] [Enterprise Agent Systems (Dataiku)](https://www.dataiku.com/blog/enterprise-agent-systems) -- Design, deploy, govern at scale
- [33] [AI Agent Architecture 2026 (DEV Community)](https://dev.to/monuminu/ai-agent-architecture-2026-building-production-grade-systems-patterns-benchmarks-and-lessons-5d34) -- Production-grade patterns and benchmarks
- [34] [LLM Inference SLO Engineering (Spheron)](https://www.spheron.network/blog/llm-inference-slo-ttft-itl-latency-budget-guide-2026/) -- TTFT, P99 latency budgets
