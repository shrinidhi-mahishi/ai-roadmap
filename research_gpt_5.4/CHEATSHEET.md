# Agentic AI Interview Cheatsheet

Use this file for fast interview revision. For each topic:

- `What it is`: shortest correct framing
- `What to say`: 2-3 lines you can say out loud
- `Trade-off`: the decision lens interviewers want
- `Watch-out`: the production risk to mention

## 1. LLM Foundations

**What it is**  
Transformers, reasoning behavior, function calling, and structured output are the base execution model for modern agents.

**What to say**  
LLMs are best treated as probabilistic planners and generators, not autonomous executors. In production, the app owns tool execution, validation, retries, and side effects. Structured output and schema validation reduce ambiguity, but they do not remove the need for guardrails.

**Trade-off**  
Higher reasoning quality usually means higher token cost and latency.

**Watch-out**  
Hallucinated tool parameters and malformed output at system boundaries.

## 2. Context Engineering

**What it is**  
The discipline of deciding what the model sees, in what order, and at what cost.

**What to say**  
Good context engineering is about relevance density, not stuffing more tokens into the prompt. The real tools are prompt design, retrieval, compression, summarization, caching, and window management. Exact-prefix caching is especially powerful but fragile to formatting drift.

**Trade-off**  
More context can improve recall but often hurts cost, latency, and reasoning quality.

**Watch-out**  
Context dilution and "lost in the middle" failures.

## 3. Tool Use

**What it is**  
How an agent calls APIs, browser tools, code runtimes, and other external capabilities.

**What to say**  
Tool use is an application-controlled loop: the model proposes an action, and the runtime validates and executes it. Production systems need schemas, permission checks, timeouts, retries, and deterministic fallbacks around every tool boundary.

**Trade-off**  
More tools increase capability but widen the failure and security surface.

**Watch-out**  
Invalid tool calls, cascading timeouts, and prompt-injected tool arguments.

## 4. Agent Architecture

**What it is**  
The control flow of the agent: ReAct, planner-executor, DAGs, loops, and workflow state.

**What to say**  
Simple ReAct loops are easy to start with but expensive at scale because each step often triggers another model call. Planner-executor or DAG-based architectures work better when you need cost control, concurrency, and durable execution. The key separation is control plane versus data plane.

**Trade-off**  
Simpler loops are easier to ship; structured workflows are easier to operate.

**Watch-out**  
Infinite loops, state drift, and hard-to-replay failures.

## 5. Agent Frameworks

**What it is**  
Frameworks like `LangGraph`, `OpenAI Agents SDK`, `Google ADK`, and `CrewAI` that provide orchestration primitives.

**What to say**  
I choose frameworks based on persistence, approvals, tracing, session state, and interoperability, not just developer ergonomics. `LangGraph` is strong for graph/state orchestration, `OpenAI Agents SDK` for approvals and tracing, `Google ADK` for session/state handling, and `CrewAI` for team-style orchestration.

**Trade-off**  
Framework convenience versus control and portability.

**Watch-out**  
Framework lock-in and unclear runtime guarantees.

## 6. RAG

**What it is**  
Retrieval-augmented generation: fetching external knowledge before generation.

**What to say**  
In practice, hybrid retrieval plus reranking is the most reliable default. Agentic RAG improves control flow around retrieval, while Graph RAG is useful when relationship structure matters more than isolated passages. Retrieval is not just about recall; it is also about latency, freshness, and authorization.

**Trade-off**  
Better retrieval quality usually adds more indexing, reranking, and operational complexity.

**Watch-out**  
Poor chunking, stale indexes, irrelevant retrieval, and access-control leaks.

## 7. Memory

**What it is**  
Short-term and long-term state the agent can use across steps and sessions.

**What to say**  
Short-term memory supports the current task, while long-term memory captures durable preferences, facts, or histories. Good memory systems separate semantic, episodic, and procedural memory and retrieve selectively instead of replaying everything back into context.

**Trade-off**  
More persistent memory improves continuity but raises privacy, freshness, and governance risk.

**Watch-out**  
Writing bad memories, retrieving stale memories, and storing sensitive data without policy controls.

## 8. Planning & Reasoning

**What it is**  
Task decomposition, self-checking, verification, reflection, and replanning.

**What to say**  
Planning improves reliability when tasks are multi-step or ambiguous, but it should be scoped and observable. Reflection and verification help only when they are bounded, measurable, and cheaper than the errors they prevent. The right pattern is usually plan, execute, verify, and replan only on failure signals.

**Trade-off**  
More reasoning steps can improve quality but often explode latency and cost.

**Watch-out**  
Overthinking loops that add tokens without improving outcomes.

## 9. Multi-Agent Systems

**What it is**  
Using multiple agents with roles like supervisor, worker, reviewer, or specialist.

**What to say**  
Multi-agent systems are useful when work can be decomposed into specialized, parallelizable units. A supervisor-worker topology is common, but it only helps if coordination cost is lower than the gain from specialization. You need strict delegation rules, message schemas, and failure containment.

**Trade-off**  
More agents can improve specialization and throughput, but coordination overhead rises quickly.

**Watch-out**  
Unbounded delegation, duplicate work, and cross-agent state inconsistency.

## 10. MCP & Interoperability

**What it is**  
A standard way for models and runtimes to access tools and resources through MCP servers and clients.

**What to say**  
MCP matters because it gives agents a cleaner interoperability layer for tools and resources instead of bespoke integrations everywhere. In enterprise settings, the real value is standardization around discovery, invocation, auth, and capability boundaries.

**Trade-off**  
Standardization improves portability, but every protocol boundary adds governance and latency overhead.

**Watch-out**  
Weak auth, overbroad tool exposure, and mismatched capability contracts.

## 11. Specialized Agents

**What it is**  
Agents optimized for domains like coding, browser automation, research, or data analysis.

**What to say**  
Specialized agents outperform general agents when the environment, toolchain, and evaluation criteria are well defined. The runtime, prompts, tools, and safeguards should all reflect the task class. A coding agent and a browser agent should not share identical permissions or success metrics.

**Trade-off**  
Specialization improves performance but reduces generality and reuse.

**Watch-out**  
Giving a high-privilege specialist access patterns meant for a lower-risk workload.

## 12. Evaluation

**What it is**  
Measuring whether the agent is correct, useful, efficient, and safe.

**What to say**  
I evaluate agents across outcomes and trajectories: task success, correctness, tool accuracy, policy adherence, cost, and latency. Final-answer scoring is not enough; I also want to see the path the agent took, which tools it called, and where it failed.

**Trade-off**  
Richer evaluation gives better signal but costs more to build and maintain.

**Watch-out**  
Optimizing for superficial benchmark scores while real production quality stays flat.

## 13. Security & Guardrails

**What it is**  
Controls for prompt injection, permissions, sandboxing, approval flows, and policy enforcement.

**What to say**  
Guardrails should sit outside the model at decision boundaries: input filters, tool policies, RBAC, approval gates, sandboxing, and audit logs. Prompt injection is not just a prompt problem; it is a trust-boundary problem between untrusted content and privileged instructions or tools.

**Trade-off**  
Stronger controls improve safety but can slow workflows and reduce autonomy.

**Watch-out**  
Treating the model as the security boundary instead of the runtime.

## 14. Observability

**What it is**  
Tracing, logging, metrics, and trajectory inspection for agent behavior.

**What to say**  
Agent observability must capture more than request latency. I want traces for every model/tool step, structured logs with correlation IDs, cost telemetry, failure states, and replayable trajectories. Without that, debugging agent failures becomes guesswork.

**Trade-off**  
More telemetry improves debuggability but adds cost, storage, and privacy burden.

**Watch-out**  
Logging sensitive prompt or tool data without redaction and retention policy.

## 15. Inference & Optimization

**What it is**  
Caches, routing, batching, and quantization used to reduce cost and improve throughput.

**What to say**  
The best optimization stack starts with prompt caching and smart model routing, then adds batching and selective degradation. Not every request needs the strongest model. The goal is to preserve quality on high-value paths while moving routine work to cheaper or faster lanes.

**Trade-off**  
Aggressive optimization cuts cost, but too much routing or compression can hurt answer quality.

**Watch-out**  
Saving money by silently degrading the tasks that matter most.

## 16. Production

**What it is**  
Running agents as reliable systems with APIs, queues, containers, orchestration, scaling, and SRE controls.

**What to say**  
Production agents need queues, deadlines, retries, idempotency, back-pressure, and durable state just like any distributed system. The novelty is the model; the operational discipline is classic platform engineering. Docker and Kubernetes matter, but reliability patterns matter more.

**Trade-off**  
Higher reliability and isolation usually mean more infrastructure and slower iteration.

**Watch-out**  
Building a clever demo that collapses under concurrency or retry storms.

## 17. Advanced Autonomous Agents

**What it is**  
Long-horizon agents that act over time, across tools, and inside richer environments.

**What to say**  
Autonomous agents are not just bigger chatbots; they are long-running systems with planning, state, environment interaction, and recovery requirements. The hard problems are bounded autonomy, verification, environment feedback, interruption, and safe resumption over long time horizons.

**Trade-off**  
More autonomy can unlock more value, but it compounds cost, control, and safety risk.

**Watch-out**  
Runaway autonomy, compounding small errors, and poor recovery after partial progress.

## Cross-Cutting Answers

### How would you design a production agent?

Start with a constrained task, a clear tool boundary, and a durable workflow. Add retrieval or memory only when the task requires it. Wrap every model and tool boundary with validation, timeouts, retries, fallback paths, logging, and policy enforcement.

### How do you choose between single-agent and multi-agent?

Use a single agent by default. Move to multi-agent only when specialization, isolation, or parallelism clearly outweigh coordination cost and failure complexity.

### How do you reduce cost?

Use caching, routing, bounded context, strong evaluation, and fallback models. Cut unnecessary tokens before you cut quality-critical reasoning steps.

### How do you make agents safe?

Treat the runtime as the security boundary. Use least privilege, approvals, sandboxing, schema validation, content isolation, and immutable audit trails.

### How do you evaluate success?

Measure both final outcomes and intermediate trajectories: correctness, tool accuracy, latency, cost, policy adherence, recovery behavior, and operator debuggability.
