# Research: Agent Architecture

**Date researched**: 2026-08-21
**Sources consulted**: 48

## 1. System Topology & Mechanics

### Core Agent Execution Patterns

**ReAct (Reason + Act)** [1][2]. Introduced by Yao et al. (2022, Princeton/Google), ReAct is the canonical agent loop pattern. The LLM follows a Thought-Action-Observation cycle: at each step it writes explicit reasoning (Thought), selects a tool (Action), reads the result (Observation), then reasons again. This continues until the agent produces a final answer or hits an iteration cap. ReAct remains the default starting pattern for general-purpose agents in 2026 [3].

Key components: (1) LLM as reasoning engine, (2) tools with name/description/schema, (3) memory (accumulated conversation history), (4) control loop managing the cycle. Each iteration requires a full LLM inference pass, consuming tokens proportional to accumulated history.

Recent advances include RP-ReAct (Molinari et al., Dec 2025), which decouples strategic planning from low-level execution using a Reasoner-Planner Agent that decomposes goals into sub-questions while Proxy Execution Agents handle standard ReAct loops per sub-task [2]. Focused ReAct adds reiteration of the original question at each step and early-stops on repetitive actions, yielding up to 530% relative accuracy gains [2].

**Plan-and-Execute** [4][5]. Separates plan generation (Planner) from step-by-step execution (Executor). The planner creates a full plan upfront; each step is executed by a potentially cheaper/smaller model. Key advantages over ReAct: (1) faster multi-step execution since the large model only plans, not acts per step, (2) cost savings by routing 85% of tokens through smaller executor models, (3) better task completion by forcing explicit reasoning about all required steps upfront [4]. Best for tasks with 5+ interdependent steps in stable environments (financial analysis, data pipelines, report generation). Weakness: less adaptive to unexpected outcomes without a replanning mechanism.

**Reflexion** [4][6]. A single-agent pattern using self-reflection through linguistic feedback. Uses an LLM evaluator to provide specific feedback to the agent, improving success rates and reducing hallucination compared to Chain-of-Thought and ReAct. However, a 2025 replication study found single-agent Reflexion consistently repeats earlier misconceptions because the same model generates both output and critique, reinforcing its own blind spots [4]. Adds ~30% latency; typically improves quality 10-30% on failure-mode subsets. Best layered on top of ReAct or Plan-and-Execute when output quality matters more than speed.

**LATS (Language Agent Tree Search)** [4][7]. Combines reflection/evaluation with Monte-Carlo tree search to explore multiple reasoning paths simultaneously. Evaluates different action sequences, discards unpromising ones, pursues the most likely path. In practice, full LATS is rarely deployed in production due to cost. Most teams use a lighter variant: generate 2-3 candidate plans, evaluate them, pick the best one without deep tree search [4].

### Agent Loop Architectures

**Single-agent loops**: The standard ReAct-style while-loop. The OpenAI Agents SDK Runner exemplifies this: `Runner.run()` enters a while-loop that repeatedly calls the active agent's model, executes tool calls, processes handoffs, and checks guardrails until final output or `max_turns` [8][9].

**Router-based branching**: A central LLM routes incoming requests to specialized agents based on intent classification. Common in customer service architectures. Microsoft's Agent Framework RC (2026) delivers WorkflowBuilder with explicit compile-time type-safe routing [10].

**DAG workflows**: Tasks and dependencies modeled as directed acyclic graphs enabling parallel execution of independent steps. The LLMCompiler architecture streams a DAG of tasks with dependencies; a Task Fetching Unit schedules execution once dependencies are met, claiming 3.6x speedup through parallelism [5]. LangGraph supports both DAGs and cyclic graphs [11].

**Cyclic graphs**: LangGraph's key differentiator over LangChain's LCEL. While LCEL targets acyclic pipelines, LangGraph handles workflows needing cycles, explicit state management, conditional branching, and multi-agent coordination [11][12].

### State Management

**Conversation state**: Accumulated message history (user/assistant/tool turns). Grows linearly with interaction length; primary driver of context window exhaustion.

**Tool state**: Results from tool executions, intermediate data products. Must be persisted for replay and recovery.

**Planning state**: Current plan, completed steps, pending steps, replanning history. In Plan-and-Execute architectures, this is the primary state artifact.

**Memory state**: Short-term (checkpoints within a run) and long-term (cross-run knowledge stores). LangGraph separates these: checkpointers for short-term, stores for long-term [13].

**LangGraph StateGraph model** [11][12]: The core primitive. StateGraph is the controller/blueprint defining nodes, edges, start/end points, and loop/branch conditions. State is the shared data structure flowing through the graph -- every node receives current state and returns updates. State is incrementally updated (not overwritten), enabling parallel execution where multiple nodes modify different fields simultaneously. State is typically defined as a TypedDict or Pydantic model. Before execution, the graph undergoes compilation that validates connections, identifies cycles, and optimizes execution paths; the compiled graph is immutable [11].

**OpenAI Agents SDK Runner loop** [8][9]: Three entry points -- `Runner.run()` (async), `Runner.run_sync()` (sync), and streaming. Loop termination: if model produces output matching `agent.output_type` with no tool calls, the loop ends. Input guardrails run before model calls; output guardrails run after model response but before finalization. "Tripwire" guardrails halt execution immediately. State serialized via `RunResult.to_state()` for resume-from-checkpoint. The 2026 update (April 15, 2026) introduced the harness/sandbox separation: harness is the control plane, sandbox is the execution plane [8].

**Google ADK event loop** [14][15]: Hybrid architecture combining deterministic workflow agents (sequential, parallel, loop) with LLM-routed dynamic delegation in hierarchical multi-agent teams. The A2A protocol enables cross-vendor agent interoperability via Server-Sent Events. ADK 2.x (v2.5 as of July 2026) introduced graph-based workflow runtime with task API for agent-to-agent delegation supporting routing, loops, retries, and human-in-the-loop [14]. Model-agnostic: Gemini-optimized but supports Anthropic, Meta, Mistral via LiteLLM. Python repo: 20,000+ GitHub stars, 65+ releases on near-weekly cadence [15].

### Control Flow Primitives

**Conditional branching**: Edges in LangGraph can be conditional (routing based on state) or standard (direct transition). OpenAI Agents SDK uses handoffs for agent-to-agent transitions [8][11].

**Parallel fan-out/fan-in**: LangGraph's Send API dynamically creates worker nodes with specific inputs; each worker has its own state, all outputs written to shared state key accessible to orchestrator [11]. Fan-out cuts wall-clock time by ~75% compared to sequential execution [10]. Key failure modes: aggregation hallucination (LLM synthesizes false consensus), API rate limit collisions, race conditions scaling as N(N-1)/2 [10].

**Human-in-the-loop interrupts**: LangGraph's runtime pauses execution, saves state, waits for human input without blocking threads; execution resumes from exact pause point [13]. OpenAI Agents SDK supports HITL via `to_state()`/resume pattern [8]. Google ADK 2.x has native resumable human-in-the-loop execution [14].

**Sub-agent delegation**: Anthropic's canonical pattern: specialized subagents handle focused tasks with clean context windows, return condensed summaries (1,000-2,000 tokens) to parent agent [16]. LangGraph's orchestrator-worker pattern with subgraphs for modularity [11]. OpenAI Agents SDK uses handoffs for agent delegation [8].

## 2. Token Economics & NFR Metrics

### Cost Per Task by Architecture

**ReAct agents**: 2,000-3,000 tokens per task for simple tasks (3-5 API calls, $0.06-0.09/task). For complex scenarios, each loop iteration adds to context window; typical completion in 3-7 loops consuming 10,000-25,000 total tokens [17]. Agents consume 4x the tokens of a chat interaction; multi-agent systems ~15x, with token usage explaining 80% of performance variance on BrowseComp (Anthropic measurement) [18].

**Plan-and-Execute agents**: Front-loads a larger output (plan itself: 1,000-2,000 tokens) but fewer API calls overall. CLEAR Framework data: Plan-Execute costs $1.24/task vs. $5.12 for Reflexion -- same accuracy class, 4.4x lower cost. The pattern: GPT-4 for planning (15% of tokens), cheaper model for execution (85% of tokens) [17].

**DAG/Graph agents**: LLMCompiler claims 3.6x speedup through parallelism. Efficient via parallel execution but complexity overhead in DAG configuration [5].

**Enterprise scale multiplier**: Enterprise AI inference represents 85% of total AI budgets. Agentic workflows consume 5-30x more tokens per task than standard chatbot queries. A single user request can trigger planning, tool selection, execution, verification, and response generation -- easily 5x the token budget of direct chat completion [17].

### Cost Optimization Strategies

1. **Plan caching**: NeurIPS 2025 paper showed 50.31% cost reduction while maintaining 96.61% of baseline performance, plus 27.28% latency reduction [17].
2. **Model routing**: Cheap small model for easy 70% of queries, frontier model for hard 30%. Real workloads see 40-70% cost reduction with no measurable quality loss [17].
3. **Prompt caching**: When agent system prompts and tool definitions repeat across runs, prompt caching cuts prompt tokens 50-90%. Anthropic, OpenAI, and Google all support natively in 2026. For tasks where prompt tokens are 90% of total, caching cuts per-task cost by 40-80% [17].
4. **Hybrid model routing**: DeepSeek R1 (reasoning/planning) + Claude Sonnet (code editing) hit SOTA on Aider's polyglot benchmark at 14x less cost than OpenAI o1 alone (Gauthier, Jan 2025) [17].

### Benchmark Results (2026)

**SWE-bench Verified** [19]: Claude Opus 4.7 (87.6%), GPT-5.3 Codex (85.0%), Claude Opus 4.5 (80.9%). Baseline in 2023: Claude 2 resolved 1.96%. Top models crossed 80% range by early 2026. Gap between top 3 models compressed to <5 percentage points (saturation signal).

**WebArena** [19]: Claude Mythos Preview (68.7%), GPT-5.4 Pro (65.8%), Claude Opus 4.6 (64.5%). Human baseline: ~78%. Original GPT-4-based agent: 14.41%. Best hybrid approaches (computer-use + API calls) outperform pure-pixel agents on both accuracy and latency.

**GAIA** [19]: Claude Sonnet 4.5 (74.6% on Princeton HAL). Anthropic sweeps top 6 HAL spots. Agentic-search specialist leads at 92.36%. GAIA2 succeeds original benchmark.

**TAU-bench** [20]: Original leaderboard (frozen): Claude 3.5 Sonnet topped at 69.2% retail / 46.0% airline. pass^k metric reveals reliability decay: a model scoring well at pass^1 can drop below 25% at pass^8. Successor benchmarks: tau2-bench (June 2025, dual-control), tau3-bench (Feb 2026, audited tasks), plus knowledge and voice domains. ICML 2026 paper (Princeton): recent capability gains yield only small reliability improvements across 15 models.

**Benchmark caveats** [19]: (1) 0 of 15 major benchmarks integrate cost-efficiency into primary scoring. (2) Scaffold dependency: same model posts different numbers under different harnesses. (3) UC Berkeley RDI (April 2026): automated scanning agent broke all 8 major benchmarks by reward hacking, achieving near-perfect scores without solving a single task.

### Latency Profiles

ReAct: ~250ms average response time for real-time analytics with GPT-4 (outperforms Plan-and-Execute and Graph by 15-20% in similar environments) but sequential processing accumulates latency over many steps [5][17]. Plan-and-Execute: higher upfront latency (plan generation) but fewer total round-trips. DAG/Graph: lowest wall-clock time for parallelizable workloads via concurrent execution [5].

### Iteration Caps and Cost/Quality Trade-offs

Without `max_turns`, misbehaving agents loop indefinitely. OpenAI recommends 5-10 for most use cases [8]. LangGraph default `recursion_limit` is 25 [18]. Per-task and per-tenant token budgets should halt execution, not just warn. Multi-turn agent cost compounds 3-5x faster than naive "turns x avg_cost" models predict [18].

## 3. Distributed Resilience & State

### State Persistence Across Agent Steps

**LangGraph checkpointing** [13][21]: State saved after every node transition, keyed by thread ID. Serialization/deserialization handled automatically. A simple two-node graph creates four checkpoints: empty at START, user input before node_a, node_a output before node_b, final output at END. "Sync" checkpointing persists state changes synchronously before next step begins -- strong durability at some performance overhead. LangGraph 1.0 (October 2025) brought production-grade checkpointing [13].

Checkpointer options: MemorySaver (dev), SqliteSaver (single-server production), PostgresSaver (multi-instance scale). AWS integration: DynamoDBSaver stores lightweight metadata in DynamoDB, uses S3 for large payloads (>350KB) [13].

**OpenAI Agents SDK state**: `RunResult.to_state()` serializes execution context into `RunState` for resume. Supports HITL interruption and resume [8].

### Durable Execution for Long-Running Agents

**The operational wall** [22][23]: Agent frameworks solved the planning loop by 2025. The remaining challenge is operational: agent dies mid-run, approval lands a day late, upstream API rate-limits, partial side-effects leave audit log inconsistent. Durable execution puts a primitive under all these failures.

**Temporal** [22][23]: Workflow (deterministic orchestration blueprint) + Activities (non-deterministic actual work: LLM calls, tool invocations, API requests). Key capabilities: automatic retry on activity failure, state held over long periods (even years) without state machines, human-in-the-loop via signal/query, self-healing with automatic retries for probabilistic LLM outputs.

Real-world adoption: OpenAI uses Temporal for Codex in production handling millions of requests [23]. Official OpenAI Agents SDK integration (`temporalio.contrib.openai_agents`) reached GA on March 23, 2026. At Replay 2026, Temporal announced Serverless Workers and Google ADK integration [22].

**Other durable execution platforms** [22]: Restate (event-driven, Rust-based), Inngest (serverless step functions), DBOS (database-oriented), Hatchet, Cloudflare Workflows, AWS Lambda Durable Functions, Azure Durable Task. Each exposes persistence primitives directly while agent frameworks add checkpointing at the agent layer.

### Handling Agent Crashes Mid-Execution

**Checkpoint-based resume** [13][21]: On crash, restore from last checkpoint. LangGraph checkpoints are recovery points, not just logs -- agent picks up exactly where it left off after server restart. Works for agents running hours or days.

**The checkpointing gap** [21]: Checkpointing alone is not full durable execution. LangGraph saves state but provides no automatic failure detection -- no supervisor, no watchdog, no heartbeat mechanism. If the process crashes, the workflow is dead until something external notices. LangGraph protects against application-level failures (bad reasoning, incorrect branches, HITL pauses). Temporal protects against infrastructure-level failures (container crashes, network partitions, host preemptions). Production deployments often need both [21].

**Sharp edges on resume** [13]: On resume, later graph work can re-execute. Nondeterministic operations and side effects need idempotency. Code before an interrupt may run again. The node boundary must be engineered as a replay boundary. LangChain's 2026 State of Agent Engineering report: 60% of production incidents trace to state management [13].

### Event Sourcing for Agent Trajectories

**Pattern** [24]: Every state transition, tool call, and observation recorded as an immutable event in an append-only log (Apache Kafka, Redis Streams, NATS). On crash, a standby worker reads the event stream, replays every event chronologically to reconstruct exact graph state (event sourcing and state rehydration). Provides natural idempotency: events recorded before execution; replay reconstructs state without re-executing side effects [24].

**Explainability** [24]: In event-sourced systems, agent decisions become permanent, queryable records. AI agent explainability = ability to reconstruct why an autonomous system made a specific decision. New tooling: EventSourcingDB 1.0 (May 2025), OpenCQRS 1.0 (October 2025).

**Key distinction**: Event streaming (Kafka, Confluent) moves data between systems (designed for throughput). Event stores (Axon Server) capture decisions with full causal context as immutable sequenced records (designed for auditability and replay) [24].

### Distributed Agent State in Multi-Node Deployments

Framework convergence on event-driven architecture [24]: LangGraph 1.0 ships Pregel/BSP execution where state updates are events. AutoGen v0.4 rebuilt around actor model with typed message passing. Google A2A protocol uses Server-Sent Events for cross-agent coordination.

**Observability** [24]: Multi-agent observability tracks "trajectories" -- series of steps through tools and sub-agents. Three pillars: distributed tracing for cross-agent calls, evaluation frameworks for reasoning quality, real-time logs for debugging. Key tools: Arize Phoenix (open-source, OpenTelemetry-native), Future AGI traceAI library. OpenTelemetry with W3C trace-context is the 2026 standard [24].

**Debugging with deterministic replay** [22]: Pull workflow history, replay locally with new code, watch the decision change. Braintrust, LangSmith, Temporal Conductor, and DBOS Conductor all expose replay primitive. The shift from log diving to time-travel debugging is permanent.

## 4. Enterprise Security & Governance

### Prompt Injection in Agentic Contexts

**Scale of the threat** [25][26]: Prompt injection remains #1 on OWASP LLM Top 10 in 2026 -- treated as an unsolved structural problem, not a bug awaiting a patch. The core issue: LLMs treat system prompt, user request, and retrieved text as a single token stream with no reliable command/data boundary. OWASP maps prompt injection to 6 of 10 categories in its Top 10 for Agentic Applications.

**Statistics** [25][26]: Documented injection attempts against enterprise AI rose ~340% YoY in late 2025. Indirect attacks (instructions hidden in email/document/web page) now >55% of incidents. Prompt injection appears in >73% of production AI deployments, caused estimated $2.3B in losses globally in 2025. Current detection tools catch only 23% of sophisticated injection attempts. Average AI agent-related breach costs ~$4.7M [26].

**Multi-hop attacks** [25]: In multi-agent systems, injection in one data source can propagate through agent chains. CVE-2026-22708 (Cursor): attacker poisons agent execution environment so allowlisted commands deliver arbitrary payloads -- the allowlist made the attack easier by auto-approving needed commands [25].

**Supply chain attacks** [25]: LiteLLM backdoor on PyPI (March 2026, ~47,000 downloads in 3 hours) -- compromised package serves as language-model gateway for CrewAI, DSPy, Microsoft GraphRAG. First malicious MCP server in the wild: postmark-mcp shipped 15 clean versions before adding exfiltration code [25].

### Agent Permission Boundaries & Execution Sandboxing

**Least privilege as primary control** [27]: Directly counters OWASP Excessive Agency (LLM06:2025). Because no fully reliable injection defense exists, assume injection succeeds; the durable mitigation is ensuring a compromised agent cannot perform high-impact actions. Zero Trust for agents: all actions explicitly allowed rather than implicitly permitted [27].

**Sandboxing technologies (2026)** [27]: Three dominant: Firecracker microVMs (strongest, regulated data), gVisor (syscall-level, compute-heavy multi-tenant), V8 Isolates (JS-only, latency-critical). Standard containers are NOT considered an acceptable isolation boundary for agentic workloads. WebAssembly emerging for polyglot + fine-grained capability control [27].

**Isolation vs. behavioral gap** [27]: Sandboxing controls where an agent runs. Least-privilege controls what it does. A sandboxed agent with an over-privileged API token can still misuse it. Both layers are required.

**Microsoft Agent Governance Toolkit (April 2026)** [27]: Four execution rings (Ring 0 supervisor through Ring 3 untrusted sandbox), each with resource limits plus instant kill-switch. Maps controls to every OWASP agentic risk.

**OWASP Agentic AI Top 10 (Dec 2025)** [27]: ASI05 (Unexpected Code Execution) classified top-tier risk. "Never execute agent-generated code without strict sandboxing, input validation, and allowlisting."

**CISA (May 2026)** [27]: Five risk categories: Privilege risks, Design & configuration risks, Behavioral risks, Structural risks, Accountability risks.

### Audit Trails for Agent Decision Chains

**EU AI Act Article 12** [28]: High-risk AI systems must enable automatic recording of events (logs) over the system lifetime. Requirements: structured complete records (timestamp, agent identity, action type, input, output, context), tamper-evident (cryptographic measures), retained at least 6 months (24 months for biometric/law enforcement), exportable for regulator review, independently verifiable, accessible for human oversight [28].

**Agent identity** [28]: Each agent in a multi-agent pipeline needs its own identity, scope constraints, and audit trail segment. When orchestrator calls specialist agent which calls tool which changes a record, shared API keys collapse accountability. Non-human identities already outnumber human identities in most enterprises [28].

**Runtime governance** [28]: For high-risk systems, governance must be runtime enforcement, not post-hoc observation. Runtime checks required before tool calls, credential issuance, exports, sends, deletes, permission changes. Interventions logged with reason codes [28].

### Compliance Landscape

**EU AI Act** [28]: Full high-risk mandates enforceable August 2, 2026 (possible extension to December 2027 via EU Digital Omnibus). Penalties: up to 35M EUR or 7% of worldwide annual turnover. 2026 frameworks: EU AI Act, GDPR, HIPAA, SOC 2 Type II, NIST AI RMF [28][5].

**Industry readiness gap** [28]: Cisco (RSA Conference 2026): 85% of enterprise customers experimenting with agents, only 5% in production. Gravitee (2026): 80.9% of technical teams testing/running agents, only 14.4% have full security approval. 61% of organizations have fragmented logs across systems; 33% lack evidence-quality audit trails [28].

### Defensive Architecture

Consensus 2026 strategy: containment, not cure [25][26][27]. Six control layers: identity, least-privilege access, runtime enforcement, behavioral monitoring, audit logging, supply chain security. Defense-in-depth pairs model-level resistance with architectural controls, assuming any single layer can fail.

Progressive enforcement [27]: 4-stage methodology: discovery (inventory AI workloads), observation (build behavioral baselines), selective enforcement (constrain high-risk agents first), full least privilege (enforce boundaries on all agents based on evidence).

## 5. Production Failure Modes

### Failure Rate Statistics

Industry estimates put agent failure rates in live environments at 70-95%, depending on task complexity and success criteria [18][29]. Large enterprises abandoned an average of 2.3 AI initiatives in 2025 (avg loss: $16.5M to AI project abandonment per enterprise). Gartner predicts >40% of agentic AI projects canceled by end of 2027 [29].

88% of agent failures trace to infrastructure gaps, not model quality (Arize, 2026) [29]. Top failure classes: context blindness (31.6%), rogue actions (30.3%), silent degradation (24.9%), memory corruption (8.1%), runaway execution (5.1%) [29].

### Infinite Loops and Loop Detection

LLMs lack an internal "stop" signal when encountering repetitive errors [18][29]. A retry loop consumes context window space, pushing earlier reasoning out of scope. By timeout, the agent has no coherent record of its original goal.

**Mitigations**: Hard iteration caps per task with forced stop and escalation. Per-task/per-tenant token budgets that halt execution. Loop detection comparing current state to recent states (hash tool+arguments, terminate on repeat within recent window). Timeouts on every external tool call. LangGraph: set `recursion_limit` as hard ceiling (default 25) plus no-progress detection [18].

### Context Window Exhaustion

Long-running loops accumulate every tool output, intermediate thought, and message, stuffing all of it back into context each turn [18][29]. Even with 200K+ token windows, recall of specific instructions degrades as context fills. The symptom: agent performs perfectly for first 5 steps, then degrades dramatically -- repeating work, forgetting constraints, contradicting prior decisions.

**Mitigations**: Context summarization at fixed intervals (every N steps, compress history into running summary). Anthropic's subagent model: specialized subagents with clean context windows return condensed summaries (1,000-2,000 tokens) to parent [16][18]. Move critical constraints out of conversation into durable database for on-demand retrieval.

### Tool Call Cascading Failures

AI agents operate through decision sequences: reasoning over context, selecting tools, interpreting outputs, feeding results into the next step [29]. Any step can degrade silently, and because each step conditions the next, a small error early compounds into completely wrong outcomes several steps later with no exception raised. In multi-agent systems, one agent's hallucinated output becomes another agent's authoritative input [29].

### Agent Hallucinating Task Completion

Agent hallucinations are not linguistic errors but fabricated "human-like behaviors" at any pipeline stage [30]. Because agents operate in long-running loops (Observe-Plan-Act-Reflect), every decision becomes the foundation for the next; one bad API call or hallucinated fact and the entire plan collapses [30]. EMNLP 2025: LLMs generate plausible but incorrect content with high internal self-consistency, defeating consistency-based detection methods [30].

Task completion rates reach ~70-75% in 2026 (First Page Sage survey of 8,128 users), but trust paradox: high completion alongside lower trust than manual search, especially among technically sophisticated users (37 percentage point trust gap) [30].

### Self-Correction / Reflection Limitations

ICLR 2024 (Huang et al.): LLMs cannot reliably self-correct reasoning using only intrinsic capabilities -- the evaluator shares the generator's blind spots [30]. 2026 preprint formalizes with information-theoretic argument: when generator and evaluator share correlated error modes, self-evaluation provides weak evidence of correctness [30].

Reflection improvements vary wildly: +7-18% for reasoning tasks, but can decrease performance when initial accuracy is already high. Prompts soliciting mistakes induce up to 40.4% false positive correction rates [30]. Research consensus: self-correction requires external verification (tool outputs, test results, separate critic models) to be reliable.

Emerging solutions: PreFlect (prospective reflection) outperforms classic Reflexion by 10-15% with 15-20% additional token overhead. GSAR framework (2026) extends hallucination detection to multi-agent settings with typed grounding. Post-hoc critics in multi-agent settings yield +8-25 percentage points on complex reasoning [30].

### Cost Runaway

OWASP 2025 LLM10: Unbounded Consumption [18][29]. An agent costing $0.10/successful run but $1.00/failed loop quietly destroys its business case. Without per-agent cost tracking and token attribution, cost explosion from runaway agents is undetectable until the invoice arrives.

**Mitigation**: Per-task and per-hour budget limits enforced at platform level. Alert on cost per successful outcome (not total spend) -- total spend rising with volume is fine; cost per outcome rising is the regression signal [18].

### The Demo-to-Production Gap

2026 is the year enterprises moved agents from demos to production, exposing failure modes no demo showed [29]. Agents working perfectly in testing fail spectacularly in production. Organizations successfully scaling agents design for failure modes before deployment. Organizations canceling programs aren't canceling because the technology doesn't work -- they deployed without resilience infrastructure [29].

Core production guardrails: irreversible actions require HITL approval; cost bounded per-task and per-hour at platform level; failures graceful with clear errors (not hallucinated success); monitoring real-time (see runaway agents while running, not in next week's cost report) [18][29].

## 6. Enterprise System Design Scenarios

### Architecture Selection Criteria

| Criterion | ReAct | Plan-and-Execute | DAG/Graph |
|---|---|---|---|
| **Best for** | Dynamic, exploratory tasks | Structured, repeatable workflows | Complex parallel pipelines |
| **Adaptability** | High (re-plans each step) | Low unless replanning added | Dynamic replanning possible |
| **Token efficiency** | Low (LLM call per tool) | High (cheap executor, expensive planner) | High via parallelism |
| **Speed** | Sequential | Sequential | Parallel |
| **Predictability** | Variable | High | Depends on DAG config |
| **Complexity** | Low | Medium | High |

**Selection heuristic** [4][5]: Start with ReAct (simplest effective pattern). Move to Plan-and-Execute when tasks have 5+ interdependent steps. Add Reflexion when output quality matters more than speed. Use LATS for problems with large solution spaces. Go multi-agent when no single agent has all required expertise. Pick based on the failure mode you can tolerate: wasted tokens (ReAct), rigidity (Plan-and-Execute), latency (Reflexion).

**Security consideration** [5]: A 2025 preprint argues separating strategic planning from tactical execution improves integrity when combined with least privilege, task-scoped tools, sandboxing, and re-planning (Del Rosario et al., 2025).

### Multi-Agent Orchestration Patterns

Six production-proven patterns [10]:

1. **Supervisor/Worker**: A supervisor agent decomposes tasks, dispatches to workers, synthesizes results. 2026 production default across frameworks [10].
2. **Sequential Pipeline**: Agents execute in order, each processing and passing state to the next.
3. **Parallel Fan-Out/Fan-In**: Multiple agents run concurrently on independent sub-tasks, results merged. Cuts wall-clock time ~75%. Needs reducer function and partial-failure handling [10].
4. **Router**: Central agent classifies intent and routes to specialized agents.
5. **Hierarchical Delegation**: Multi-level supervisor hierarchy for complex organizations.
6. **Evaluator-Optimizer Loop**: Producer generates, critic evaluates, loop until quality threshold or escalation.

**Composition**: Patterns are composable. Common architecture: fan-out research agents feeding supervisor for quality-gating, HITL checkpoint before external actions, consensus round for highest-stakes decisions. Supervisor + Fan-Out is a key composite [10].

**Anthropic's own system** [16]: Orchestrator-worker pattern. Lead agent coordinates, specialized subagents operate in parallel with own context windows exploring different aspects simultaneously. Subagents provide compression (parallel exploration) and separation of concerns (distinct tools, prompts, trajectories).

**When multi-agent wins** [10]: (1) Parallelizable read-heavy work with independent sub-problems (fan-out research, log triage, multi-source enrichment) -- AORCHESTRA reports +16.28% over strongest baseline. (2) Narrow-domain reliability tasks (100% actionable rate vs 1.7% single-agent in incident response). For sequential tasks or shared-state scenarios, single agent recommended.

### Scaling Agent Workloads

**Infrastructure requirements**: Durable execution (Temporal or equivalent) is becoming the baseline requirement for any agent touching external systems [22]. Only 1.6% of Claude Code's codebase is AI decision logic; 98.4% is operational infrastructure [13].

**Market trajectory**: Agentic AI market valued at ~$10.9B in 2026, projected $199B by 2034 (43.8% CAGR) [22]. Gartner: 40% of enterprise applications will feature task-specific agents by end of 2026, up from <5% in 2025 [5]. Deloitte: 50% of enterprises using generative AI will deploy autonomous agents by 2027, up from 25% in 2025 [5].

**Progressive deployment** [5][27]: Define use cases, establish clean data foundations, design modular core components, select models and tool integrations, establish security guardrails, validate in sandbox, expand from human-supervised pilots to full-scale automation.

**Orchestration sophistication should follow workload complexity** [10]: Teams deploying swarm-style systems on tasks a three-subagent supervisor could handle are spending engineering budget infrastructure doesn't require. Measure task shape first, match pattern second, choose framework third.

**Framework landscape (2026)** [10][11]: LangGraph (43% of enterprise agent deployments, state-machine based), OpenAI Agents SDK (Runner loop with handoffs), Google ADK (agent-as-class with workflow composition), CrewAI (role-based), AutoGen (actor model), Microsoft Agent Framework RC (compile-time type-safe DAGs). Direct cloud competitors: Amazon Bedrock AgentCore, Azure AI Foundry Agents, Databricks Agent Bricks [14].

**Cost management at scale** [17]: Model routing (cheap model for easy 70%, frontier for hard 30%) delivers 40-70% cost reduction. Prompt caching cuts 40-80% when prompt tokens dominate. Plan caching achieves 50% cost reduction at 96.6% performance retention. Hybrid model pairing (e.g., DeepSeek R1 + Claude Sonnet) can yield 14x cost reduction at SOTA quality [17].

## Sources

- [1] https://outcomeschool.com/blog/react-agent -- ReAct Agent overview
- [2] https://www.emergentmind.com/topics/reason-act-reflect-react-architectures -- ReAct architectures for LLM agents, RP-ReAct, Focused ReAct
- [3] https://blog.n8n.io/react-agent/ -- How to Build a ReAct Agent (n8n)
- [4] https://theaiengineer.substack.com/p/the-4-single-agent-patterns -- ReAct vs Plan-and-Execute vs ReWOO vs Reflexion
- [5] https://dasroot.net/posts/2026/04/agent-architectures-react-plan-execute-graph-agents/ -- Agent Architectures: ReAct vs Plan-Execute vs Graph Agents
- [6] https://www.langchain.com/blog/reflection-agents -- Reflection Agents (LangChain)
- [7] https://github.com/FareedKhan-dev/all-agentic-architectures -- 35 production-grade agentic architectures library
- [8] https://openai.github.io/openai-agents-python/running_agents/ -- Running agents (OpenAI Agents SDK)
- [9] https://deepwiki.com/openai/openai-agents-python/3.2-runner-and-execution-flow -- Runner and Execution Flow (OpenAI Agents SDK)
- [10] https://www.digitalapplied.com/blog/multi-agent-orchestration-5-patterns-that-work -- Multi-Agent Orchestration: 5 Patterns (2026)
- [11] https://latenode.com/blog/ai-frameworks-technical-infrastructure/langgraph-multi-agent-orchestration/langgraph-ai-framework-2025-complete-architecture-guide-multi-agent-orchestration-analysis -- LangGraph AI Framework 2025 architecture guide
- [12] https://eastondev.com/blog/en/posts/ai/20260424-langgraph-agent-architecture/ -- LangGraph State: Checkpoints, Threads, and Recovery
- [13] https://docs.langchain.com/oss/python/langgraph/persistence -- LangGraph Persistence docs
- [14] https://futureagi.com/blog/what-is-google-adk-2026 -- What is Google ADK (2026)
- [15] https://thenewstack.io/what-is-googles-agent-development-kit-an-architectural-tour/ -- Google ADK architectural tour
- [16] https://www.anthropic.com/engineering/multi-agent-research-system -- How we built our multi-agent research system (Anthropic)
- [17] https://www.kunalganglani.com/blog/ai-agent-cost-per-task-2026 -- AI Agent Cost Per Task 2026
- [18] https://cloudzy.com/blog/why-ai-agent-loops-fail-in-production/ -- Why AI Agent Loops Fail in Production
- [19] https://rapidclaw.dev/blog/ai-agent-benchmarks-2026 -- AI Agent Leaderboard 2026
- [20] https://taubench.com/ -- TAU-bench benchmarking AI agents
- [21] https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows -- Why Checkpoints Aren't Durable Execution
- [22] https://www.reactify-solutions.com/articles/durable-ai-agents-2026 -- Durable AI agents 2026: Temporal, Inngest, DBOS, Restate
- [23] https://temporal.io/solutions/ai -- AI Applications & Agents With Temporal
- [24] https://zylos.ai/research/2026-03-02-event-driven-architecture-ai-agent-systems/ -- Event-Driven Architecture for AI Agent Systems
- [25] https://www.helpnetsecurity.com/2026/06/11/owasp-prompt-injection-ai-security-failures/ -- Prompt injection drives most agentic AI security failures
- [26] https://shattered.io/agentic-ai-security-2026/ -- Agentic AI Security: $4.7M Breaches
- [27] https://northflank.com/blog/how-to-sandbox-ai-agents -- How to sandbox AI agents in 2026
- [28] https://ai2sql.io/ai-blog/eu-ai-act-agent-audit-trails-database -- EU AI Act and AI Agent Audit Trails
- [29] https://gravity.fast/blog/ai-agent-failures-lessons-from-2026/ -- AI Agent Failures: Lessons From 2026
- [30] https://zylos.ai/research/2026-05-12-agent-self-correction-reflexion-to-prm -- Agent Self-Correction: From Reflexion to Process Reward Models
- [31] https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation -- AI Agent Failure Modes: Tool-Calling Errors, Infinite Loops
- [32] https://neuraltrust.ai/blog/ai-agent-security-enterprises-complete-guide -- Complete Guide to AI Agent Security for Enterprises 2026
- [33] https://www.microsoft.com/en-us/security/blog/2026/07/16/least-privilege-for-ai-agents-identity-access-and-tool-binding/ -- Least privilege for AI agents (Microsoft)
- [34] https://www.armosec.io/blog/ai-agent-sandboxing-progressive-enforcement-guide/ -- AI Agent Sandboxing & Progressive Enforcement (ARMO)
- [35] https://www.cockroachlabs.com/blog/agentic-ai-costs-at-scale/ -- How to Manage Agentic AI Costs at Scale
- [36] https://atlan.com/know/ai-agent/react-vs-plan-and-execute-agent-architecture/ -- ReAct vs Plan-and-Execute Architecture Guide 2026
- [37] https://agentmarketcap.ai/blog/2026/04/10/durable-agent-execution-production-temporal-modal-event-sourced -- Durable Agent Execution in Production 2026
- [38] https://www.axoniq.io/blog/ai-agent-explainability-event-sourcing-infrastructure -- AI Agent Explainability: Event Sourcing Infrastructure
- [39] https://www.digitalapplied.com/blog/ai-agent-governance-policy-compliance-2026 -- AI Agent Governance: Policy and Compliance 2026
- [40] https://trussed.ai/resources/eu-ai-act-enforcement-august-2026-guide -- EU AI Act Enforcement August 2026 Guide
- [41] https://www.digitalapplied.com/blog/ai-agent-task-completion-rates-2026-user-study-analysis -- AI Agent Task Completion 2026: What 8,128 Users Reveal
- [42] https://futureagi.com/blog/trace-debug-multi-agent-systems-observability-guide/ -- Trace and Debug Multi-Agent Systems 2026
- [43] https://www.langchain.com/langgraph -- LangGraph: Agent Orchestration Framework
- [44] https://rahulkolekar.com/openai-agents-sdk-sandboxes-long-running-agents/ -- OpenAI Agents SDK 2026: Sandboxes & Long-Running Agents
- [45] https://www.spheron.network/blog/ai-agent-benchmarking-gpu-cloud-swebench-gaia/ -- AI Agent Benchmarking Infrastructure
- [46] https://www.flowhunt.io/blog/multi-agent-ai-system/ -- Multi-Agent AI Systems in 2026: What the Research Says
- [47] https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns -- AI Agent Orchestration Patterns (Microsoft Azure)
- [48] https://www.augmentcode.com/guides/multi-agent-ai-production-requirements -- Multi-Agent AI Production Requirements Beyond the Demo
