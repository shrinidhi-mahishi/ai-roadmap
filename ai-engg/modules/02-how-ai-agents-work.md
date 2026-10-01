# Module 02: How AI Agents Work

**Scope**: Agent execution engines (ReAct, Plan-and-Execute, hybrid), state orchestration (DAG graphs, supervisor-worker), communication protocols (MCP, A2A), tool-use mechanics, token economics, durable execution, zero-trust security, production failure taxonomy, and enterprise system design.

**Audience**: Principal/Director/VP-level AI architecture interviews.

---

### What Is This?

An AI agent is a system that produces **side effects** -- it changes the world rather than just generating text. Where a chatbot maps input to output in a single turn, an agent determines intermediate steps autonomously, calls tools, observes results, and loops until a goal is met or a budget is exhausted. The LLM inside the agent is a **processor, not a knowledge base** -- it proposes next actions and interprets observations, but current facts come from tools and memory systems.

Three defining properties separate agents from chatbots:

1. **Autonomy** -- the agent selects intermediate steps without a pre-written script.
2. **Proactiveness** -- it recognizes missing information and takes initiative (with designed uncertainty thresholds and "ask-for-help" triggers).
3. **Action** -- it executes operations through defined tool interfaces (APIs, browsers, file systems).

The **PEAS framework** (classical AI) specifies agent design: **Performance** (measurable success criteria), **Environment** (operational boundaries -- which APIs, systems, data are in scope), **Actuators** (available tools), and **Sensors** (observation mechanisms -- parsing API responses, reading page content, detecting confirmation signals).

### Why It Matters

Every production AI system that goes beyond single-turn Q&A is an agent. Understanding agent internals -- execution loops, state management, failure modes, cost dynamics -- is the difference between a prototype that demos well and a system that survives production traffic. 57% of organizations now have agents in production (LangChain 2025), yet only 3% successfully scale them across departments (IDC/AWS, N=900+). The bottleneck is architecture, not model capability.

---

### 1. System Topology & Data Flow

The production unit is not "an LLM with tools." It is a **control plane** (orchestrator) that routes, checkpoints, enforces policy, and manages the agentic loop around a **data plane** (LLM inference + tool execution) that tokenizes, reasons, generates structured actions, and dispatches tool calls. The model never executes tools -- it emits structured action requests that a tool proxy layer validates, sandboxes, and dispatches.

**Control plane** owns: the agentic loop (step counter, recursion limit, wall-clock timeout), state management (checkpointer or durable execution runtime), routing (which specialist agent handles the task), policy enforcement (tool RBAC, PII redaction, budget caps), and human-in-the-loop gates.

**Data plane** owns: LLM inference (reasoning, action proposal, observation interpretation), structured output (JSON tool-call parameters), and streaming response delivery.

**Tool proxy layer** owns: schema validation (parameter types, required fields, RBAC per tool), sandbox execution (gVisor/container/WASM with timeout), credential proxying (agent never sees real tokens), injection screening, and result formatting.

**Persistence** splits into two fundamentally different stores: (1) **application state** -- conversation history, tool results, plan progress, checkpoint snapshots (PostgreSQL, Redis, Temporal history) -- transactional, must survive process death; and (2) **context/cache** -- prompt cache (prefix-addressed KV tensors), semantic cache (vector similarity), plan cache (reusable task templates) -- soft, best-effort, not a transaction log.

**Telemetry** tracks: step count per task, tokens consumed per step, tool call success/failure rates, loop detection metrics (output similarity across turns), cost attribution per task/user/feature, and circuit breaker state per provider/tool.

```
 CONTROL PLANE (Orchestrator)
 ┌──────────────────┐  ┌─────────────────┐  ┌──────────────────┐
 │ Task Ingress     │─>│ Plan / Route    │─>│ Agentic Loop     │
 │ auth, quota,     │  │ decompose task, │  │ ReAct engine,    │
 │ session init,    │  │ select workers, │  │ step counter,    │
 │ budget ceiling   │  │ hybrid planner  │  │ recursion limit, │
 │                  │  │                 │  │ wall-clock cap   │
 └──────────────────┘  └─────────────────┘  └───────┬──────────┘
                                                    │
                              ┌──────────────────────┤
                              │                      │
                              v                      v
 DATA PLANE (LLM Inference)              TOOL PROXY LAYER
 ┌──────────────────┐                    ┌───────────────────┐
 │ LLM Provider     │                    │ Schema Validator   │
 │ reason, propose  │                    │ param types,       │
 │ action, interpret│                    │ required fields,   │
 │ observation      │                    │ RBAC per-tool      │
 │                  │                    ├───────────────────┤
 │ Structured output│                    │ Sandbox Executor   │
 │ tool_use JSON    │                    │ gVisor/container,  │
 │                  │                    │ timeout, credential│
 └────────┬─────────┘                    │ proxy (vault)      │
          │                              └─────────┬─────────┘
          │ observation                            │ tool result
          v                                        v
 ┌────────────────────────────────────────────────────────┐
 │                    STATE LAYER                         │
 │                                                        │
 │  App State              Cache Tier         Telemetry   │
 │  ┌──────────────┐  ┌──────────────────┐  ┌──────────┐ │
 │  │ Checkpointer │  │ Prompt cache     │  │ Step cnt │ │
 │  │ (PG/Redis/   │  │ Semantic cache   │  │ Token $  │ │
 │  │  Temporal)    │  │ Plan cache       │  │ Breaker  │ │
 │  │ Conversation │  │ (prefix-matched, │  │ Loop det │ │
 │  │ Tool results │  │  vector-matched, │  │ Cost atr │ │
 │  │ Plan progress│  │  template-based) │  │ Audit log│ │
 │  └──────────────┘  └──────────────────┘  └──────────┘ │
 └────────────────────────────────────────────────────────┘
```

**End-to-end request flow (8 steps)**:

1. **Ingress.** Client submits a task (e.g., "book a flight under $500 SFO to JFK next Tuesday"). Gateway authenticates, stamps correlation-id, checks per-user/session budget ceiling. If session budget is exhausted, reject immediately.

2. **Plan.** Orchestrator decides execution strategy: pure ReAct (simple, exploratory), Plan-and-Execute (known phases), or Hybrid (structured plan with ReAct within phases). For complex tasks, the planner decomposes into phases with verification checkpoints between them.

3. **Route.** For multi-agent systems, the supervisor routes to a specialist worker (e.g., flight-search agent, booking agent). Hub-and-spoke for up to 6 workers; hierarchical for 6+. Single-agent systems skip this step.

4. **Reason (LLM call).** The agent sends accumulated context (system prompt, conversation history, available tools, current observation) to the LLM. The LLM returns either a final answer (stop) or a structured tool call (tool_use with function name and JSON parameters).

5. **Validate and execute tool.** Tool proxy validates parameters against registered JSON schema. RBAC checks whether this agent/user may invoke this tool. Sandbox executes with timeout. Credentials are fetched from vault -- the agent never sees real API tokens. Result is JSON-encoded and injection-screened.

6. **Observe.** Tool result is appended to conversation history as an observation. If the result is empty (HTTP 200 with no payload), the system flags it rather than treating silence as success.

7. **Loop or terminate.** The agentic loop checks: step count < recursion limit? Token budget remaining? Wall-clock < timeout? No repeated identical tool calls detected? If all pass, return to step 4. If any fail, force termination with partial result or escalation.

8. **Persist and emit.** Final answer delivered to client. Application state checkpointed. Cost, latency, step count, and tool call audit trail written to telemetry sink.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Execution Engines

**ReAct (Reason + Act)** -- the dominant single-agent execution engine. Each iteration: the LLM reasons about the current state, selects and parameterizes a tool call, observes the result, and reasons again.

```
State machine:

    ┌─────────┐    tool_use     ┌─────────┐   result     ┌─────────┐
    │ REASON  │───────────────>│  ACT    │────────────>│ OBSERVE │
    └─────────┘                └─────────┘             └────┬────┘
         ^                                                  │
         │                  not done                        │
         └──────────────────────────────────────────────────┘
         │
         │  stop / budget exhausted / max steps
         v
    ┌─────────┐
    │  DONE   │
    └─────────┘
```

**Complexity per step**: O(C_infer + C_tool) where C_infer is the LLM inference cost (dominated by context length -- O(n^2) attention over n accumulated tokens) and C_tool is the external API latency. Over k steps, total context grows linearly, so inference cost per step increases: total inference cost is O(k * n_avg^2) without summarization, or O(k * n_window^2) with sliding-window summarization.

**Key invariant**: each step depends on the observation from the prior step. There is no pre-written script. This makes ReAct adaptive but also means early mistakes compound through later steps.

**Plan-and-Execute** -- generates a complete plan upfront, then executes sequentially. The planner LLM call is expensive (full task analysis) but runs once. Execution steps may use a cheaper model. Advantage: reviewable plan, fewer total LLM round-trips for predictable tasks. Disadvantage: cannot adapt to unexpected findings mid-execution without replanning.

**Hybrid (recommended in practice)** -- structured plan with checkpoints; ReAct reasoning within each phase:

```
    ┌────────────┐     ┌────────────┐     ┌────────────┐
    │  Phase 1   │────>│ Checkpoint │────>│  Phase 2   │────> ...
    │ ReAct loop │     │ Verify /   │     │ ReAct loop │
    │ (search)   │     │ HITL gate  │     │ (execute)  │
    └────────────┘     └────────────┘     └────────────┘
```

Each phase runs a bounded ReAct loop. Checkpoints between phases allow verification before irreversible actions (e.g., verify the found flight before booking).

#### 2.2 State Orchestration Models

**DAG-based graphs (LangGraph)**: nodes are Python functions that receive current state and return state updates. Edges define control flow including conditionals and cycles.

```
    ┌─────────┐
    │ Router  │ ─── decides which specialist
    └────┬────┘
    ┌────┼──────────────┐
    v    v              v
 ┌──────┐ ┌──────┐ ┌──────┐
 │Work-1│ │Work-2│ │Work-N│    specialist agent nodes
 └──┬───┘ └──┬───┘ └──┬───┘
    └────┬────┘───────┘
         v
    ┌───────────┐
    │ Supervisor│ ─── merges outputs into coherent state
    │ / Reducer │
    └───────────┘
```

**State accumulation problem**: every node receives the full state dictionary. If messages append indefinitely, a supervisor running 20 steps sends thousands of accumulated tokens per node call. Mitigation: periodic summarization or slicing messages to last N entries. Without mitigation, cost grows O(k^2) in step count.

**Routing latency**: 620 ms -- 1.8 s per hop in production LangGraph (Redis Streams queue + containerized workers + PostgreSQL JSONB). This is the price of fault isolation and independently retryable sub-agents.

**Supervisor-Worker pattern**: the production standard. 57% of organizations with multi-agent systems in production use some variant. One orchestrator receives the task, decomposes, routes to specialists, synthesizes. Each worker has a scoped responsibility and toolset.

Topology guidance:
- **Hub-and-spoke**: works for up to ~6 workers.
- **Hierarchical**: needed for 6+ workers or when workers need their own sub-teams.
- **Skip supervisor design** if you have fewer than 3 distinct capabilities, no external tool calls, or latency budget under 2 seconds.

**Critical invariant**: validate outputs at every worker boundary. The #1 production bug in naive supervisor implementations: a worker returns empty, supervisor treats it as success, final output is silently broken. Fix: Pydantic output validation at every worker boundary.

#### 2.3 Communication Protocols

**MCP (Model Context Protocol)** -- agent-to-tool standard. Open standard by Anthropic (Nov 2024), donated to Linux Foundation (Dec 2025). Defines how agents connect to external tools, data sources, and services. Key capability: **Tool Search Tool** discovers tools on-demand instead of loading all definitions upfront -- 85% reduction in token usage, significant accuracy improvement (Opus 4: 49% to 74%; Opus 4.5: 79.5% to 88.1%).

**A2A (Agent2Agent Protocol)** -- agent-to-agent standard. Open standard by Google (Apr 2025), donated to Linux Foundation (Jun 2025). Enables agents to discover, authenticate, and delegate tasks across platforms. Core primitives: Agent Card (capability advertisement as JSON), Task Object (work unit over JSON-RPC 2.0), Artifacts (typed outputs), push notifications/SSE for streaming. A2A v1.2 (2026) added signed agent cards for domain verification. 150+ organizations in production.

**Complementary, not competitive**: MCP gives an agent its "hands" (tool access). A2A gives a team of agents the ability to coordinate. The converging enterprise stack: A2A for coordination, MCP for tool access, shared context layer for governed business knowledge.

#### 2.4 Tool Use Mechanics

LLMs produce text only. Tools enable real-world action via native tool calling:

1. Define allowed functions as JSON schemas in the API request (name, description, parameters with types).
2. LLM outputs a structured JSON tool call with parameters.
3. External code validates (field presence, type correctness), executes the real API call, returns results.

**Tool-calling fails 3--15% of the time** in production, varying by model size and task complexity. Four primary failure modes:

| Mode | Description | Detection | Severity |
|------|-------------|-----------|----------|
| **Hallucinated parameters** | Agent invents options not in schema | Schema validation | Medium -- caught by validation |
| **Hallucinated tool invocations** | Agent calls nonexistent tools | Registry check | Medium -- caught by registry |
| **Silent failures** | HTTP 200 with empty payload | Empty-response detector | **Critical** -- no error surfaces |
| **Context-truncated calls** | Long history pushes tool defs past attention | Duplicate/contradictory call detector | High -- redundant or wrong calls |

#### 2.5 Reliability Math for Multi-Agent Systems

Multi-agent LLM systems fail **41--86% of the time** depending on task complexity. Even with well-trained individual agents, end-to-end reliability degrades multiplicatively:

```
P(system_success) = P(agent_1) * P(agent_2) * ... * P(agent_n)

5 agents at 95% individual accuracy: 0.95^5 = 0.774 (77.4% system success)
5 agents at 90% individual accuracy: 0.90^5 = 0.590 (59.0% system success)
```

Three error propagation patterns:
1. **Factual drift**: one agent confidently states something incorrect; next agent treats it as ground truth.
2. **Context window poisoning**: bad tool result appended to shared scratchpad contaminates every future call.
3. **Cascading retries**: downstream agent detects problem, triggers self-correction, spawning more failing tool calls.

Errors typically pass through **3--4 reasoning layers** before surfacing. A single hallucination forwarded to 3 subagents produces 3 wrong answers, each with apparent coherence.

---

### 3. Token Economics & NFR Analysis

#### 3.1 The Cost Problem

Token prices fell ~80% between 2025 and 2026, yet enterprise AI bills went up. Agents make **3--10x more LLM calls** than simple chatbots. A single user request can trigger planning, tool selection, execution, verification, and response generation -- easily 5x the token budget of a direct completion. Gartner (March 2026): agentic models require **5--30x more tokens per task** than a standard chatbot.

Average inference spend: 85% of enterprise AI budgets. 60% of AI projects exceed original cost estimates by 30--50%.

**Incident**: Two LangChain-based agents entered an infinite conversation cycle that ran for 11 days, generating a **$47,000 bill** before detection (November 2025).

#### 3.2 Cost Formulas

**Per-execution cost model**:

```
Cost_per_task = (input_tokens * input_price + output_tokens * output_price) * avg_steps

Example: 5-step agent, GPT-4o ($2.50 / $10.00 per 1M tokens)
  ~2,000 input + ~500 output per step:
  = (2000 * $0.0000025 + 500 * $0.00001) * 5
  = ($0.005 + $0.005) * 5
  = $0.05 per task

At 1,000 tasks/day = $50/day = $1,500/month (unoptimized)
```

**Unconstrained complex agent** (e.g., software engineering tasks): **$5--8 per task** at premium model rates.

**Monthly cost projection formula**:

```
Monthly_cost = tasks_per_day * 30 * cost_per_task * (1 + retry_overhead)

Where retry_overhead = avg_retry_rate * cost_per_retry / cost_per_task
Typical retry_overhead: 0.15 -- 0.30 (15-30% of base cost)
```

#### 3.3 Latency SLA Targets (2026 Data)

```
 TTFT (Time to First Token) -- 2026 Production Benchmarks
 ┌─────────────────────────────────────────┬──────────┬──────────┬──────────┐
 │ Model Class                             │   P50    │   P95    │   P99    │
 ├─────────────────────────────────────────┼──────────┼──────────┼──────────┤
 │ Frontier (GPT-5.5/Opus 4.7/Gemini 3)   │ 0.85-1.4s│ 1.6-2.4s │  ~3.2s   │
 ├─────────────────────────────────────────┼──────────┼──────────┼──────────┤
 │ Reasoning (Opus 4.7 max thinking)       │  ~28s    │   --     │   --     │
 ├─────────────────────────────────────────┼──────────┼──────────┼──────────┤
 │ Reasoning (GPT-5.5 Pro high)            │  ~67s    │   --     │   --     │
 ├─────────────────────────────────────────┼──────────┼──────────┼──────────┤
 │ Reasoning (Gemini 3 DT high)            │  ~52s    │   --     │   --     │
 └─────────────────────────────────────────┴──────────┴──────────┴──────────┘

 P95/P50 ratio averages 2.1x, worst pairings hit 3.2x.
 SLO design should anchor on P95, not P50.
```

**UX thresholds**: Chat UX requires sub-2s P95 TTFT (defensible bar), sub-1s (premium bar). Streaming throughput: 50 TPS feels slow, 100 TPS normal, 200+ TPS instant.

**Gateway/guardrails overhead**: +11ms P50, +14ms P95, +21ms P99. Negligible against 1,000+ ms baseline LLM calls.

**Regional latency impact**: US-East to APAC adds 180--220ms TTFT; EU to US-East adds 80--110ms.

**Baseten benchmark (Sep 2026)**: P99 end-to-end = 3.23s, median = 331ms, TTFT P95 = 1.16s, task success = 95.8%.

#### 3.4 Cost Optimization Stack

**Layer 1: Prompt caching** (exact-prefix, provider-level). Stores computed KV tensors behind a repeated prompt prefix. Static portion (system prompt, tool definitions, reference docs) bills at up to **90% discount**. No quality trade-off -- byte-identical output.

```
Prompt ordering for maximum cache hit:
  [Static: system instructions, persona, few-shot examples]
  [Heavy: large reference documents]
  [Dynamic: user query, conversation history]      <-- only this part is uncached
```

Measured impact: Anthropic up to 90% cost reduction on cached prefixes + 13--31% TTFT improvement. One developer: $720/month to $72/month via prompt caching alone.

**Layer 2: Semantic caching** (application-level). Vector similarity search returns cached responses for semantically equivalent queries. ~31% of LLM queries exhibit semantic similarity across typical workloads.

**Layer 3: Agentic plan caching** (NeurIPS 2025). Extracts reusable plan templates from previously solved tasks. Traditional semantic caching fails for agents because outputs depend on external data. Plan caching achieves **50.31% cost reduction** while maintaining **96.61% of baseline performance**, plus 27.28% latency reduction.

**Layer 4: Dynamic model routing**. Route easy tasks to cheap models, complex tasks to premium ones. The 100--300x cost differential between premium and small model tiers is the primary leverage point.

- RouteLLM (ICLR 2025): **85% cost reduction** maintaining **95% of GPT-4 performance**.
- Moving 70% of requests to budget tier: ~60% LLM cost reduction.

**Cascade architecture** (all layers combined):

```
 Request
    │
    v
 ┌─────────────────┐  hit
 │ Semantic cache   │──────> Return cached response (100% savings)
 │ check            │
 └────────┬────────┘
          │ miss
          v
 ┌─────────────────┐
 │ Plan cache       │──────> Execute cached plan template (50% savings)
 │ check            │
 └────────┬────────┘
          │ miss
          v
 ┌─────────────────┐
 │ Complexity       │──── simple ──> Budget model (Haiku/Flash-Lite)
 │ classifier       │──── medium ──> Mid-tier (Sonnet/Flash)
 │                  │──── complex ─> Frontier (Opus/Pro)
 └─────────────────┘
          │ failure
          v
 ┌─────────────────┐
 │ Escalation       │──────> Retry with next tier up
 └─────────────────┘
```

**Combined impact**: caching + routing together delivers **70--85% cost reduction** on unoptimized baselines. One practitioner documented 90% total reduction through combined RAG optimization, prompt compression, and context pruning.

#### 3.5 Budget Governance (Three-Layer)

```
 ┌────────────────────────────────────────────────┐
 │ Layer 1: Session ceiling                       │
 │   Max tokens/dollars per individual session    │
 │   Hard terminate when reached                  │
 ├────────────────────────────────────────────────┤
 │ Layer 2: Per-user / per-feature quotas         │
 │   Daily/weekly caps attributed to features     │
 │   Alert at 80%, throttle at 100%               │
 ├────────────────────────────────────────────────┤
 │ Layer 3: Organizational spending limits        │
 │   Alert at 60%, hard stop at 100%              │
 │   Finance dashboard + anomaly detection        │
 └────────────────────────────────────────────────┘
```

**Tooling**: LiteLLM, Portkey, OpenRouter support multi-model routing, semantic caching, budget enforcement, and failover out of the box.

#### 3.6 NFR Targets for Agent Systems

```
 ┌────────────────────┬──────────────────────────────────────┐
 │ NFR                │ Target                               │
 ├────────────────────┼──────────────────────────────────────┤
 │ Availability       │ 99.9% (multi-provider circuit break) │
 │ TTFT P95           │ < 2s (interactive), < 5s (agentic)   │
 │ Task success rate  │ > 95% (simple), > 80% (complex)      │
 │ RPO (state loss)   │ 0 (checkpointed state)               │
 │ RTO (resume time)  │ < 30s (from last checkpoint)          │
 │ Cost per task      │ Track, alert at 2x baseline           │
 │ Max steps/task     │ Hard cap (e.g., 25 default)           │
 │ Compliance         │ Full audit trail, PII never in logs   │
 │ Blast radius       │ Single-tenant failure isolation       │
 └────────────────────┴──────────────────────────────────────┘
```

---

### 4. Distributed Resilience & Security

#### 4.1 Durable Execution Patterns

Temporal raised $300M at $5B valuation (Feb 2026), with 9.1 trillion lifetime action executions. OpenAI runs Temporal for Codex, handling millions of daily coding agent requests.

Two core mechanisms:

**Journal-based replay**: record each completed step; replay on crash. A new worker replays event history from the beginning -- every completed Activity call is skipped (reads recorded result from history). When replay reaches the last completed step, normal execution resumes.

**Database checkpointing**: persist state after each node (e.g., LangGraph checkpointers with Postgres/SQLite).

**Critical constraint**: workflow code must avoid non-deterministic operations (random numbers, timestamps, direct network calls). Those must be wrapped as Activities whose results are recorded. Pattern: anything touching the outside world is an Activity; everything else is deterministic workflow logic.

#### 4.2 Checkpointing vs. Durable Execution

```
 ┌───────────────────┬────────────────────────┬────────────────────────────┐
 │                   │ Checkpointer           │ Durable Execution          │
 │                   │ (LangGraph)            │ (Temporal)                 │
 ├───────────────────┼────────────────────────┼────────────────────────────┤
 │ Who owns retry    │ Developer              │ Runtime                    │
 │ Who owns resume   │ Developer              │ Runtime                    │
 │ Side-effect dedup │ Developer              │ Runtime (event sourced)    │
 │ Protects against  │ App-level failures,    │ Infra-level failures,      │
 │                   │ bad reasoning, HITL    │ container crashes, network │
 │ Resume granularity│ From last checkpoint   │ From any completed step    │
 │ Operational cost  │ DB (Postgres/Redis)    │ Temporal cluster or Cloud  │
 └───────────────────┴────────────────────────┴────────────────────────────┘

 Production deployments often need BOTH.
 Checkpointing cuts wasted processing by 60%+ on multi-step workflows.
```

**Competing platforms (2026)**:

```
 ┌──────────────┬──────────────────────┬───────────────────────────────────┐
 │ Platform     │ Mechanism            │ Differentiator                    │
 ├──────────────┼──────────────────────┼───────────────────────────────────┤
 │ Temporal     │ Journal/replay       │ Mature; 3,000+ customers (NVIDIA, │
 │              │ multi-lang SDKs      │ Netflix, OpenAI). GA integration  │
 │              │                      │ with OpenAI Agents SDK (Mar 2026) │
 ├──────────────┼──────────────────────┼───────────────────────────────────┤
 │ Restate      │ Journal/replay       │ Same mechanism, lighter footprint │
 │              │ lighter footprint    │ Simpler deployment model          │
 ├──────────────┼──────────────────────┼───────────────────────────────────┤
 │ DBOS         │ All state in PG      │ Zero explicit checkpoint code;    │
 │              │                      │ any decorated function is durable │
 ├──────────────┼──────────────────────┼───────────────────────────────────┤
 │ Inngest      │ Durable execution    │ Steps, waits, retries without     │
 │              │ for serverless       │ infrastructure management         │
 └──────────────┴──────────────────────┴───────────────────────────────────┘
```

**Event history growth**: for long-running workflows, event histories grow unbounded. Temporal's **Continue-As-New**: atomically complete current run and start new run with same workflow ID, carrying forward only essential state.

#### 4.3 Failure Taxonomy

```
 FAILURE TAXONOMY FOR AI AGENTS
 ┌────────────────────────────────────────────────────────────────────────┐
 │                                                                        │
 │  CONTEXT FAILURES                                                      │
 │  ├── Attention decay: initial instructions lose weight over turns       │
 │  ├── Working-memory rot: agent's own trace corrupts active memory      │
 │  ├── Specification drift: by ~20th turn, optimizing adjacent goal      │
 │  └── Context overflow: history fills window, truncates tool defs       │
 │                                                                        │
 │  LOOP FAILURES                                                         │
 │  ├── Infinite retry loop: same tool call with identical inputs         │
 │  │   (68 confirmed across 47 projects -- IAL-Scan, 6,549 repos)       │
 │  ├── Agent paralysis: contradictory signals, no action taken           │
 │  └── Polling tax: hyperactive status-check instead of webhook wait    │
 │                                                                        │
 │  PROPAGATION FAILURES                                                  │
 │  ├── Factual drift: confident wrong output treated as ground truth     │
 │  ├── Context poisoning: bad tool result contaminates shared state      │
 │  └── Cascading retries: 10 agents x 10 retries = 100 dead requests    │
 │                                                                        │
 │  TOOL FAILURES (31% of production failures)                            │
 │  ├── Schema violations: wrong types, missing required fields           │
 │  ├── Hallucinated invocations: calls to nonexistent tools              │
 │  ├── Silent failures: HTTP 200, empty payload, no error surfaced       │
 │  └── Context-truncated calls: tool defs pushed past attention range    │
 │                                                                        │
 │  INFRASTRUCTURE FAILURES                                               │
 │  ├── Cascading API timeouts: circular deps + no timeout config         │
 │  ├── Worker cascade: worker returns empty, supervisor treats as OK     │
 │  └── Retry storms: exponential multiplication of upstream requests     │
 │                                                                        │
 │  SECURITY FAILURES                                                     │
 │  ├── Direct prompt injection: overwrite system instructions            │
 │  └── Indirect injection: malicious payload in RAG/tool output          │
 │                                                                        │
 └────────────────────────────────────────────────────────────────────────┘
```

**Detection principle**: monitor **step counts and output similarity** across turns, not just latency/error rates. A looping agent may never throw an error while burning compute on identical retries.

**Root cause of loops**: agents are given objectives but not exit conditions. They oscillate between spinning when they should stop and stopping when they should escalate.

#### 4.4 Zero-Trust MCP Architecture

Traditional Zero Trust breaks for AI agents because agents are non-deterministic, goal-interpreting entities that select their own tools, chain API calls, and spawn sub-tasks. Static RBAC is "like trying to govern a conversation with a list of approved words."

**Anthropic Managed Agents** (April 2026) -- three mutually untrusted components:

```
 ┌──────────────────────────────────────────────────────────┐
 │                    MANAGED AGENT                         │
 │                                                          │
 │  ┌──────────┐    ┌──────────────┐    ┌───────────────┐  │
 │  │  BRAIN   │    │   SESSION    │    │    HANDS      │  │
 │  │ Claude + │    │ Append-only  │    │ Disposable    │  │
 │  │ harness  │    │ event log    │    │ Linux sandbox │  │
 │  │ (routing │    │ (outside     │    │ (code runs    │  │
 │  │ decisions│    │  both brain  │    │  here, no     │  │
 │  │  only)   │    │  and hands)  │    │  credentials) │  │
 │  └────┬─────┘    └──────────────┘    └───────┬───────┘  │
 │       │                                      │          │
 │       │ session-bound token                  │ request  │
 │       v                                      v          │
 │  ┌───────────────────────────────────────────────────┐  │
 │  │              CREDENTIAL PROXY                     │  │
 │  │  Fetches real OAuth tokens from vault             │  │
 │  │  Makes external call on behalf of agent           │  │
 │  │  Returns result -- agent never sees real token    │  │
 │  └───────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────┘
```

**NVIDIA NemoClaw**: five enforcement layers. Sandboxed execution with Landlock, seccomp, and network namespace isolation at kernel level. Default-deny outbound networking -- every external connection requires explicit operator approval via YAML policy.

**Zentera Enclave Model**: trust boundary containing sandboxed agents, authorized assets, and scoped tools. Agent in Project A's enclave cannot reach Project B's assets, tools, or agents. Prompt-layer controls tell an agent what it should not do; an enclave enforces what it cannot reach.

#### 4.5 Tool-Level RBAC & Least Agency

**Least agency** extends beyond least privilege: least privilege asks what an agent may read; least agency asks what it may do. An agent that summarizes documents should not hold the ability to send mail, even if it may lawfully read the mailbox.

**Cloud Security Alliance Agentic Trust Framework (ATF)**: treats agent autonomy as something earned through demonstrated trustworthiness. Four maturity levels with progressively greater autonomy and governance requirements. Adds behavioral anomaly detection, PII protection, RBAC, and error tracking.

#### 4.6 PII Filtering & Audit Logs

PII redaction must happen at access boundaries, before content enters the LLM context. Tools: Lakera Guard (acquired by Check Point, Sep 2025) inspects prompts and responses for injection, jailbreak, PII, and exfiltration at runtime. AWS Bedrock Guardrails and Azure Content Safety provide native checks.

**Structured audit trails**: November 2025 MCP spec update added structured audit trails as a formal capability. Enterprise requirement: every tool access, data retrieval, and user trigger must be logged with full provenance.

Anthropic's principal hierarchy: Anthropic --> operators --> users. System designed for humans to observe actions in real time, approve/reject operations, interrupt in-progress work, and audit after the fact.

**Industry posture**: only **14.4%** of organizations reported full security approval for their entire agent fleet (Gravitee, Feb 2026, N=919). **97%** of organizations with AI-related breaches lacked proper AI access controls (IBM 2025).

---

### 5. Production Enterprise Code

All snippets are stdlib-only Python, runnable as-is.

**Snippet 1: Retry with Exponential Backoff + Full Jitter and Circuit Breaker**

```python
#!/usr/bin/env python3
"""
Retry with exponential backoff + full jitter, three-state circuit breaker,
and fallback model chain. Stdlib only. Run: python agent_resilience.py
"""
from __future__ import annotations
import random, threading, time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class TransientError(Exception):
    """Retryable failure (429, 503, timeout)."""
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class PermanentError(Exception):
    """Non-retryable failure (400, 401, schema violation)."""


class CircuitOpenError(Exception):
    """Circuit breaker is open -- calls rejected without attempting."""


# ── Circuit Breaker (closed -> open -> half-open -> closed) ──────────

class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """
    Three-state circuit breaker.
    - CLOSED: requests flow normally; failures increment counter.
    - OPEN: all requests rejected immediately for `reset_timeout` seconds.
    - HALF_OPEN: one probe request allowed; success closes, failure re-opens.
    """
    failure_threshold: int = 5
    reset_timeout: float = 30.0
    _state: _State = field(default=_State.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def state(self) -> str:
        return self._state.value

    def _trip(self) -> None:
        self._state = _State.OPEN
        self._last_failure_time = time.monotonic()

    def _attempt_reset(self) -> None:
        self._state = _State.HALF_OPEN

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = _State.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._trip()

    def allow_request(self) -> bool:
        with self._lock:
            if self._state == _State.CLOSED:
                return True
            if self._state == _State.OPEN:
                if time.monotonic() - self._last_failure_time >= self.reset_timeout:
                    self._attempt_reset()
                    return True  # allow one probe
                return False
            # HALF_OPEN: only one probe in-flight
            return False


# ── Retry with Full Jitter ───────────────────────────────────────────

def retry_with_backoff(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    breaker: CircuitBreaker | None = None,
    **kwargs: Any,
) -> Any:
    """
    Retry `fn` on TransientError with exponential backoff + full jitter.
    Respects Retry-After headers and circuit breaker state.
    Raises PermanentError immediately. Raises last TransientError after exhaustion.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        if breaker and not breaker.allow_request():
            raise CircuitOpenError(
                f"Circuit open (>{breaker.failure_threshold} failures in window)"
            )
        try:
            result = fn(*args, **kwargs)
            if breaker:
                breaker.record_success()
            return result
        except PermanentError:
            raise  # never retry 400/401/schema violations
        except TransientError as e:
            last_error = e
            if breaker:
                breaker.record_failure()
            if attempt == max_retries:
                break
            # Respect Retry-After if provided; otherwise exponential + full jitter
            if e.retry_after is not None:
                delay = e.retry_after
            else:
                exp_delay = min(base_delay * (2 ** attempt), max_delay)
                delay = random.uniform(0, exp_delay)  # full jitter
            time.sleep(delay)
    raise last_error  # type: ignore[misc]


# ── Fallback Model Chain ─────────────────────────────────────────────

def call_with_fallback(
    prompt: str,
    model_chain: list[dict[str, Any]],
    breakers: dict[str, CircuitBreaker],
) -> dict[str, Any]:
    """
    Try each model in chain until one succeeds.
    Each model entry: {"name": "gpt-4o", "fn": callable, "max_retries": 3}
    Falls back to deterministic response if all models fail.
    """
    errors: list[str] = []
    for model in model_chain:
        name = model["name"]
        breaker = breakers.get(name)
        try:
            result = retry_with_backoff(
                model["fn"], prompt,
                max_retries=model.get("max_retries", 3),
                breaker=breaker,
            )
            return {"model": name, "result": result, "fallback": False}
        except (TransientError, CircuitOpenError, PermanentError) as e:
            errors.append(f"{name}: {e}")
            continue

    # All models exhausted -- deterministic degraded response
    return {
        "model": "deterministic_fallback",
        "result": {"status": "degraded", "message": "All models unavailable"},
        "fallback": True,
        "errors": errors,
    }
```

**Snippet 2: Structured Logging with Correlation ID and Agent Step Tracking**

```python
import hashlib, json, logging, re, uuid
from dataclasses import dataclass, field


class AgentJsonFormatter(logging.Formatter):
    """Structured JSON logs with correlation-id, agent step, and cost tracking."""
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
            "agent_id": getattr(record, "agent_id", None),
            "step": getattr(record, "step", None),
            "tool": getattr(record, "tool", None),
            "tokens_used": getattr(record, "tokens_used", None),
            "cost_usd": getattr(record, "cost_usd", None),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class AgentLogAdapter(logging.LoggerAdapter):
    """Logger adapter that injects agent context into every log line."""
    def process(self, msg, kwargs):
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def make_agent_logger(agent_id: str, correlation_id: str | None = None) -> AgentLogAdapter:
    """Create a structured logger for an agent execution."""
    logger = logging.getLogger(f"agent.{agent_id}")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(AgentJsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    return AgentLogAdapter(logger, {
        "agent_id": agent_id,
        "correlation_id": correlation_id or uuid.uuid4().hex[:12],
    })


# ── PII Redaction ────────────────────────────────────────────────────

_PII_PATTERNS = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")),
    ("credit_card", re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    """Replace PII with hashed placeholders; return (redacted_text, audit_trail)."""
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m, _label=label):
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit
```

**Snippet 3: Agentic Loop with Bounded Execution, Loop Detection, and Graceful Degradation**

```python
import hashlib, time
from dataclasses import dataclass, field


@dataclass
class AgentLoopConfig:
    max_steps: int = 25
    max_tokens: int = 100_000
    wall_clock_timeout: float = 300.0  # seconds
    max_identical_calls: int = 3       # detect infinite loops
    session_budget_usd: float = 5.0


@dataclass
class StepRecord:
    step: int
    action: str           # tool name or "final_answer"
    args_hash: str        # SHA256 of serialized args
    tokens_used: int
    cost_usd: float
    latency_ms: float
    success: bool


@dataclass
class AgentLoop:
    """
    Bounded agentic loop with:
    - Hard step limit
    - Token and cost budget
    - Wall-clock timeout
    - Infinite loop detection (repeated identical tool calls)
    - Graceful degradation on budget exhaustion
    """
    config: AgentLoopConfig = field(default_factory=AgentLoopConfig)
    _steps: list[StepRecord] = field(default_factory=list, init=False)
    _total_tokens: int = field(default=0, init=False)
    _total_cost: float = field(default=0.0, init=False)
    _start_time: float = field(default=0.0, init=False)
    _call_hashes: list[str] = field(default_factory=list, init=False)

    def _hash_call(self, tool_name: str, args: dict) -> str:
        payload = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _detect_loop(self, call_hash: str) -> bool:
        """Return True if the same tool+args has been called max_identical_calls times."""
        self._call_hashes.append(call_hash)
        count = self._call_hashes.count(call_hash)
        return count >= self.config.max_identical_calls

    def _budget_remaining(self) -> bool:
        if self._total_tokens >= self.config.max_tokens:
            return False
        if self._total_cost >= self.config.session_budget_usd:
            return False
        if time.monotonic() - self._start_time >= self.config.wall_clock_timeout:
            return False
        return True

    def run(self, reason_fn, tool_fn, context: dict) -> dict:
        """
        Execute the agentic loop.

        reason_fn(context) -> {"action": "tool_name", "args": {...}} or {"action": "final_answer", "result": ...}
        tool_fn(tool_name, args) -> {"result": ..., "tokens": int, "cost": float}
        """
        self._start_time = time.monotonic()

        for step_num in range(1, self.config.max_steps + 1):
            if not self._budget_remaining():
                return {
                    "status": "budget_exhausted",
                    "steps_completed": len(self._steps),
                    "total_tokens": self._total_tokens,
                    "total_cost_usd": self._total_cost,
                    "partial_context": context,
                }

            # Reason: ask LLM what to do next
            t0 = time.monotonic()
            decision = reason_fn(context)
            reason_latency = (time.monotonic() - t0) * 1000

            if decision["action"] == "final_answer":
                return {
                    "status": "completed",
                    "result": decision["result"],
                    "steps_completed": len(self._steps),
                    "total_tokens": self._total_tokens,
                    "total_cost_usd": self._total_cost,
                }

            # Loop detection
            tool_name = decision["action"]
            tool_args = decision.get("args", {})
            call_hash = self._hash_call(tool_name, tool_args)

            if self._detect_loop(call_hash):
                return {
                    "status": "loop_detected",
                    "repeated_call": {"tool": tool_name, "args": tool_args},
                    "steps_completed": len(self._steps),
                    "total_tokens": self._total_tokens,
                    "total_cost_usd": self._total_cost,
                }

            # Act: execute tool
            t0 = time.monotonic()
            tool_result = tool_fn(tool_name, tool_args)
            tool_latency = (time.monotonic() - t0) * 1000

            # Track costs
            step_tokens = tool_result.get("tokens", 0)
            step_cost = tool_result.get("cost", 0.0)
            self._total_tokens += step_tokens
            self._total_cost += step_cost

            record = StepRecord(
                step=step_num,
                action=tool_name,
                args_hash=call_hash,
                tokens_used=step_tokens,
                cost_usd=step_cost,
                latency_ms=reason_latency + tool_latency,
                success=tool_result.get("success", True),
            )
            self._steps.append(record)

            # Observe: update context with tool result
            context["last_observation"] = tool_result.get("result")
            context["step"] = step_num

            # Empty result detection
            if tool_result.get("result") in (None, "", {}, []):
                context["_empty_result_warning"] = (
                    f"Tool '{tool_name}' returned empty. Verify before proceeding."
                )

        return {
            "status": "max_steps_reached",
            "steps_completed": len(self._steps),
            "total_tokens": self._total_tokens,
            "total_cost_usd": self._total_cost,
            "partial_context": context,
        }
```

**Snippet 4: Tool Schema Validator with RBAC**

```python
from typing import Any


class ToolValidationError(ValueError):
    """Tool call failed validation."""


class ToolAccessDenied(PermissionError):
    """Agent lacks permission to invoke this tool."""


_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: type(v) is int,
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate_tool_params(params: dict, schema: dict, path: str = "$") -> None:
    """Recursively validate tool call parameters against JSON schema."""
    expected_type = schema.get("type")

    if expected_type == "object":
        if not isinstance(params, dict):
            raise ToolValidationError(f"{path}: expected object, got {type(params).__name__}")
        required = schema.get("required", [])
        for key in required:
            if key not in params:
                raise ToolValidationError(f"{path}.{key}: required field missing")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in params:
                if key not in props:
                    raise ToolValidationError(f"{path}.{key}: additionalProperties=false")
        for key, value in params.items():
            if key in props:
                validate_tool_params(value, props[key], f"{path}.{key}")

    elif expected_type == "array":
        if not isinstance(params, list):
            raise ToolValidationError(f"{path}: expected array, got {type(params).__name__}")
        items_schema = schema.get("items", {})
        for i, item in enumerate(params):
            validate_tool_params(item, items_schema, f"{path}[{i}]")

    elif expected_type in _TYPE_CHECKS:
        if not _TYPE_CHECKS[expected_type](params):
            raise ToolValidationError(f"{path}: expected {expected_type}")

    enum = schema.get("enum")
    if enum is not None and params not in enum:
        raise ToolValidationError(f"{path}: value not in enum {enum}")


class ToolRegistry:
    """
    Registry of available tools with schema validation and RBAC.
    Prevents hallucinated tool invocations and unauthorized access.
    """

    def __init__(self) -> None:
        self._tools: dict[str, dict] = {}  # name -> {schema, fn, allowed_roles}
        self._role_grants: dict[str, set[str]] = {}  # agent_id -> {roles}

    def register(
        self, name: str, schema: dict, fn: Any, allowed_roles: set[str] | None = None
    ) -> None:
        self._tools[name] = {
            "schema": schema,
            "fn": fn,
            "allowed_roles": allowed_roles or {"*"},
        }

    def grant_role(self, agent_id: str, role: str) -> None:
        self._role_grants.setdefault(agent_id, set()).add(role)

    def invoke(self, agent_id: str, tool_name: str, params: dict) -> Any:
        # Guard: hallucinated tool invocation
        if tool_name not in self._tools:
            raise ToolValidationError(
                f"Tool '{tool_name}' does not exist in registry. "
                f"Available: {list(self._tools.keys())}"
            )

        tool = self._tools[tool_name]

        # Guard: RBAC check
        if "*" not in tool["allowed_roles"]:
            agent_roles = self._role_grants.get(agent_id, set())
            if not agent_roles & tool["allowed_roles"]:
                raise ToolAccessDenied(
                    f"Agent '{agent_id}' lacks role for tool '{tool_name}'. "
                    f"Required: {tool['allowed_roles']}, has: {agent_roles}"
                )

        # Guard: schema validation
        validate_tool_params(params, tool["schema"])

        # Execute
        return tool["fn"](**params)
```

---

### 6. Architectural System Design Scenarios

#### Scenario 1: Multi-Agent Customer Support Platform for a Fintech Company

**Problem**: Design an AI agent system handling 5,000 customer support tickets/day for a fintech company. Requirements: sub-3s first response, handle account inquiries + transaction disputes + loan applications, PII-safe (SOC 2 + PCI-DSS), maximum $2,500/month model spend, 99.9% availability.

**Architecture**:

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                         API GATEWAY                                │
 │  Auth (OAuth2), rate limit, correlation-id, budget enforcement     │
 └──────────────────────────────┬──────────────────────────────────────┘
                                │
                                v
 ┌─────────────────────────────────────────────────────────────────────┐
 │                     PII REDACTION LAYER                            │
 │  Detect + redact SSN, account numbers, card numbers BEFORE LLM    │
 │  Audit trail: hash of original + placeholder mapping               │
 └──────────────────────────────┬──────────────────────────────────────┘
                                │
                                v
 ┌──────────────────────────────────────────────────────────────────┐
 │                    COMPLEXITY CLASSIFIER                         │
 │               (Haiku/Flash-Lite, <50ms, ~$0.001/ticket)         │
 │                                                                  │
 │  simple (60%)──> Account FAQ Agent (Haiku, cached system prompt) │
 │  medium (30%)──> Transaction Agent (Sonnet, tool access)         │
 │  complex (10%)─> Loan/Dispute Agent (Opus, multi-step)           │
 └──────────────────────────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        v                      v                        v
 ┌──────────────┐    ┌──────────────────┐    ┌────────────────────┐
 │ FAQ Agent    │    │ Transaction Agent│    │ Loan/Dispute Agent │
 │ Haiku        │    │ Sonnet           │    │ Opus               │
 │ 1 step       │    │ 2-3 steps        │    │ 5-8 steps          │
 │ RAG only     │    │ DB lookup tool   │    │ DB + external APIs │
 │              │    │ balance check    │    │ credit check       │
 │ $0.003/ticket│    │ $0.02/ticket     │    │ $0.15/ticket       │
 └──────────────┘    └──────────────────┘    └────────┬───────────┘
                                                      │
                                                      v
                                              ┌───────────────┐
                                              │ HITL Escalation│
                                              │ (disputes >    │
                                              │  $1000, fraud) │
                                              └───────────────┘
 ┌─────────────────────────────────────────────────────────────────┐
 │                     PERSISTENCE LAYER                           │
 │  LangGraph checkpointer (Postgres) for multi-turn state        │
 │  Semantic cache (Redis + vector) for FAQ deduplication          │
 │  Audit log (append-only, WORM) for compliance                  │
 └─────────────────────────────────────────────────────────────────┘
```

**Cost projection**:

```
Daily: 0.60 * 5000 * $0.003 + 0.30 * 5000 * $0.02 + 0.10 * 5000 * $0.15
     = $9 + $30 + $75 = $114/day
     + classifier: 5000 * $0.001 = $5
     = $119/day

Monthly (unoptimized): $119 * 30 = $3,570  -- OVER BUDGET

With semantic cache (31% hit rate on FAQ tier):
     FAQ savings: 0.31 * $9 = -$2.79/day
With prompt caching (90% on system prompts):
     System prompt savings across all tiers: ~-$15/day
With plan cache on loan flows (50% savings on repeat patterns):
     Loan savings: 0.50 * $75 * 0.30 = -$11.25/day  (30% of loan queries are repeat patterns)

Optimized daily: $119 - $2.79 - $15 - $11.25 = ~$90/day
Monthly: $90 * 30 = $2,700 -- slightly over, trim with aggressive FAQ caching

Final monthly: ~$2,400 with tuned semantic cache threshold  -- WITHIN BUDGET
```

**Trade-off matrix**:

```
 ┌──────────────┬─────────────────────┬──────────────────────────┬────────────────────────┐
 │ Dimension    │ A. Single Opus      │ B. 3-Tier Routing        │ C. Fine-tuned small    │
 │              │    for all tickets  │    (Recommended)         │    model               │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Cost/month   │ $22,500             │ ~$2,400                  │ ~$600                  │
 │              │ (all Opus, 5 steps) │ (89% cost reduction)     │ (training cost amort.) │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Latency P95  │ 3-5s (Opus TTFT)   │ <1s FAQ, <3s complex     │ <500ms all             │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Ops burden   │ Simplest            │ Medium: 3 models, cache, │ Highest: training      │
 │              │                     │ routing, checkpointing   │ pipeline, eval, drift  │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Security     │ PII redaction       │ PII redaction + per-tier │ PII in training data   │
 │              │ before LLM          │ tool RBAC + audit trail  │ risk + all of B        │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Scalability  │ Linear cost growth  │ Sub-linear (cache gains  │ Flat compute cost,     │
 │              │                     │ improve with volume)     │ retraining on drift    │
 └──────────────┴─────────────────────┴──────────────────────────┴────────────────────────┘
```

**Decision**: **B** (3-tier routing) is the only option that meets the $2,500/month budget while handling complex loan/dispute flows that require frontier-model reasoning. Option A is 9x over budget. Option C would meet budget but requires months of training pipeline investment and cannot handle novel dispute patterns without retraining. The 89% cost reduction from routing + caching makes Option B viable without sacrificing quality on the 10% of tickets that genuinely need frontier reasoning.

---

#### Scenario 2: Durable Multi-Agent Code Review Pipeline

**Problem**: Design an automated code review system processing 500 pull requests/day across 12 microservice repositories. Requirements: review completeness (security + correctness + style), resume after infrastructure failures (Kubernetes pod preemptions), structured output for CI integration, $300/day model budget, results within 10 minutes per PR.

**Architecture**:

```
 ┌───────────────────────────────────────────────────────────────────────┐
 │                      GITHUB WEBHOOK INGRESS                          │
 │  PR opened/updated -> queue (Redis Streams) -> dedup by PR ID        │
 └──────────────────────────────┬────────────────────────────────────────┘
                                │
                                v
 ┌───────────────────────────────────────────────────────────────────────┐
 │                    TEMPORAL WORKFLOW ORCHESTRATOR                     │
 │  Durable execution: survives pod preemption, replays from journal    │
 │  Continue-As-New for PRs with 100+ files (event history cap)         │
 └──────────────────────────────┬────────────────────────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              v                 v                  v
 ┌────────────────┐  ┌──────────────────┐  ┌──────────────────┐
 │ DIFF ANALYZER  │  │ SECURITY SCANNER │  │ STYLE CHECKER    │
 │ (Activity)     │  │ (Activity)       │  │ (Activity)       │
 │                │  │                  │  │                  │
 │ Sonnet         │  │ Opus             │  │ Haiku            │
 │ per-file diff  │  │ dependency +     │  │ linting rules +  │
 │ analysis       │  │ injection +      │  │ naming + docs    │
 │ 2 steps/file   │  │ auth patterns    │  │ 1 step/file      │
 │                │  │ 3 steps/file     │  │                  │
 │ $0.012/file    │  │ $0.025/file      │  │ $0.002/file      │
 └───────┬────────┘  └────────┬─────────┘  └────────┬─────────┘
         │                    │                      │
         └────────────────────┼──────────────────────┘
                              v
 ┌───────────────────────────────────────────────────────────────────────┐
 │                    SUPERVISOR / REDUCER                               │
 │  Merges findings from all 3 workers                                  │
 │  Deduplicates overlapping findings                                   │
 │  Pydantic validation on every worker output (empty = failure)        │
 │  Severity ranking: critical > high > medium > low                    │
 └──────────────────────────────┬────────────────────────────────────────┘
                                │
                                v
 ┌───────────────────────────────────────────────────────────────────────┐
 │                    CI OUTPUT FORMATTER                                │
 │  Structured JSON -> GitHub PR comments (inline per-line)             │
 │  SARIF output for GitHub Security tab                                │
 │  Summary comment with severity counts                                │
 └──────────────────────────────────────────────────────────────────────┘
```

**Cost projection**:

```
Average PR: 15 changed files

Per PR:
  Diff analysis:  15 files * $0.012 = $0.18
  Security scan:  15 files * $0.025 = $0.375
  Style check:    15 files * $0.002 = $0.03
  Supervisor:     1 call * $0.01   = $0.01
  Total per PR:   $0.595

Daily (500 PRs): 500 * $0.595 = $297.50  -- WITHIN BUDGET

With prompt caching on shared analysis prompt (90% savings on cached prefix):
  Savings: ~30% of total = -$89
  Optimized daily: ~$208
```

**Why Temporal here**: Kubernetes pod preemptions are routine in CI infrastructure. A code review spanning 15 files with 3 parallel workers takes 3-8 minutes. Without durable execution, a preemption at minute 6 discards all completed work. Temporal replays completed Activities from journal, resuming from exactly where execution stopped. The overhead (Temporal Cloud at ~$25/month for this volume) pays for itself in the first week of avoided re-reviews.

**Trade-off matrix**:

```
 ┌──────────────┬─────────────────────┬──────────────────────────┬────────────────────────┐
 │ Dimension    │ A. Single-agent     │ B. Multi-agent +         │ C. Fine-tuned static   │
 │              │    sequential       │    Temporal (Recommended)│    analysis rules      │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Cost/day     │ $400 (Opus for all) │ ~$208 (tiered + cache)   │ ~$0 (compute only)     │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Latency/PR   │ 15-25 min serial    │ 3-8 min (parallel)       │ <1 min                 │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Ops burden   │ Low                 │ Medium: Temporal cluster, │ Low ongoing, high      │
 │              │                     │ 3 worker configs, Redis  │ initial rule authoring  │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Preemption   │ Full restart        │ Resume from last step    │ Stateless, restart OK  │
 │ recovery     │ (wasted work)       │ (zero wasted work)       │                        │
 ├──────────────┼─────────────────────┼──────────────────────────┼────────────────────────┤
 │ Review depth │ Good (single model  │ Best (specialized models │ Shallow (pattern match │
 │              │ loses focus on long │ per concern, focused     │ only, misses semantic  │
 │              │ PRs)                │ context per worker)      │ issues)                │
 └──────────────┴─────────────────────┴──────────────────────────┴────────────────────────┘
```

**Decision**: **B** (multi-agent + Temporal) meets the $300/day budget at $208/day with headroom for spikes. Parallelization keeps latency under 10 minutes. Temporal's durable execution eliminates wasted compute from pod preemptions -- critical in shared Kubernetes clusters where preemption rates of 5-15% are normal. The specialized worker pattern (security scanner uses Opus; style checker uses Haiku) allocates model spend where reasoning depth matters most. Option A exceeds budget and serialization makes it too slow. Option C is cheap but fundamentally cannot catch the semantic bugs (logic errors, auth bypass, injection patterns) that justify AI-powered code review.

---

### Key Takeaways for Interviews

- **An agent is defined by side effects, not intelligence.** The three properties (autonomy, proactiveness, action) separate agents from chatbots. The LLM is a processor, not a knowledge base.

- **ReAct is the dominant execution engine; hybrid is the recommended production pattern.** Pure ReAct is adaptive but compounds early mistakes. Plan-and-Execute is rigid but reviewable. Hybrid (structured plan with ReAct per phase + verification checkpoints) gives both.

- **Supervisor-worker is the production standard for multi-agent systems (57% adoption).** Hub-and-spoke for up to 6 workers, hierarchical above that. Skip the supervisor if you have <3 capabilities or <2s latency budget.

- **MCP and A2A are complementary, not competitive.** MCP = agent-to-tool (hands). A2A = agent-to-agent (coordination). The converging stack uses both.

- **Multi-agent reliability degrades multiplicatively.** Five 95%-reliable agents deliver 77% system success. Validate at every worker boundary -- the #1 bug is treating empty worker output as success.

- **Agents cost 5--30x more tokens than chatbots.** Budget governance requires three layers: session ceiling, per-user/feature quotas, organizational limits. Model routing is the highest-leverage optimization (100--300x cost spread across model tiers).

- **Caching + routing together delivers 70--85% cost reduction.** Prompt caching (prefix-matched, up to 90% discount), semantic caching (31% query overlap), plan caching (50% cost reduction, 96.6% quality), and dynamic model routing (85% cost reduction at 95% quality).

- **Checkpointing and durable execution solve different problems.** LangGraph checkpointing handles app-level failures (bad reasoning, HITL pauses). Temporal handles infra-level failures (container crashes, network partitions). Production needs both.

- **Zero-trust for agents requires least agency, not just least privilege.** Static RBAC fails because agents are non-deterministic and select their own tools. Credential proxying (agent never sees real tokens), sandbox isolation, and enclave boundaries enforce what prompt-layer controls cannot.

- **The #1 reason agents fail in production is not model quality -- it is flawed enterprise integration.** ~95% of generative AI pilots stall due to integration issues (MIT/NANDA 2025). The organizations reaching production invested in architecture before agent code.

---

### Common Failure Modes

```
 ┌──────────────────────────────┬────────────────────────────┬──────────────────────────────┐
 │ Failure Mode                 │ Detection                  │ Mitigation                   │
 ├──────────────────────────────┼────────────────────────────┼──────────────────────────────┤
 │ Infinite retry loop          │ Step count + output        │ Hard step ceiling + repeated │
 │ (68 confirmed in 47 repos)  │ similarity monitoring      │ call detection + exit conds  │
 ├──────────────────────────────┼────────────────────────────┼──────────────────────────────┤
 │ Context window degradation   │ Quality benchmarks at      │ Summarize at fixed intervals │
 │ (attention decay, spec drift)│ turn 10, 20, 30            │ Pin critical instructions    │
 ├──────────────────────────────┼────────────────────────────┼──────────────────────────────┤
 │ Silent 200 OK from tools     │ Empty-response detector    │ Treat empty as failure, not  │
 │ (most damaging -- no error)  │ on every tool result       │ success; require validation  │
 ├──────────────────────────────┼────────────────────────────┼──────────────────────────────┤
 │ Cascading retries            │ Upstream request count     │ Retry at ONE layer only;     │
 │ (10x10 = 100 dead requests) │ monitoring                 │ backoff + jitter + breaker   │
 ├──────────────────────────────┼────────────────────────────┼──────────────────────────────┤
 │ Factual drift across agents  │ Cross-agent consistency    │ Validate outputs at each     │
 │ (hallucination propagation)  │ checks                     │ boundary before forwarding   │
 ├──────────────────────────────┼────────────────────────────┼──────────────────────────────┤
 │ Hallucinated tool params     │ Schema validation (31% of  │ Strict JSON schema + RBAC +  │
 │                              │ production failures)       │ registry (no unknown tools)  │
 ├──────────────────────────────┼────────────────────────────┼──────────────────────────────┤
 │ Prompt injection (indirect)  │ Injection classifier on    │ Privilege separation + input │
 │                              │ tool outputs               │ sanitization + output review │
 ├──────────────────────────────┼────────────────────────────┼──────────────────────────────┤
 │ Budget runaway               │ Per-session cost tracking  │ Three-layer budget caps;     │
 │ ($47K incident, 11 days)     │ + anomaly alerting         │ kill switch at 2x baseline   │
 └──────────────────────────────┴────────────────────────────┴──────────────────────────────┘
```

---

### Interview Q&A

**Q1: What distinguishes an AI agent from a chatbot?**

An agent produces side effects -- it changes the world rather than just generating text. Three defining properties: autonomy (selects intermediate steps without a script), proactiveness (recognizes missing information and takes initiative), and action (executes operations through tool interfaces). The LLM inside the agent is a processor, not a knowledge base. It proposes next steps and interprets results, but current facts come from tools and memory systems.

**Q2: When would you use supervisor-worker vs. a single agent?**

Skip the supervisor when you have fewer than 3 distinct capabilities, no external tool calls, or a latency budget under 2 seconds. Use supervisor-worker when tasks decompose into parallel sub-problems with different tool requirements. Hub-and-spoke scales to ~6 workers; beyond that, hierarchical orchestration is needed. The routing overhead is 620ms--1.8s per hop, so single-agent is always faster for simple tasks. 57% of organizations with multi-agent systems in production use the supervisor-worker pattern.

**Q3: How do you prevent infinite loops in agentic systems?**

Three mechanisms: (1) hard step ceiling (e.g., recursion_limit=25), (2) no-progress detection -- hash every tool call (name + serialized args) and kill execution when the same hash appears N times, (3) wall-clock timeout. Detection should monitor step counts and output similarity, not just errors -- a looping agent may never throw an error while burning compute. Root cause: agents are given objectives but not exit conditions.

**Q4: Explain the difference between MCP and A2A.**

MCP (Model Context Protocol) is agent-to-tool -- it gives an agent its "hands" by standardizing how agents connect to external tools, data sources, and services. A2A (Agent2Agent Protocol) is agent-to-agent -- it enables agents across different platforms and frameworks to discover, authenticate, and delegate tasks to each other. They are complementary: the converging enterprise stack uses MCP for tool access and A2A for inter-agent coordination.

**Q5: How do you achieve 70-85% cost reduction on agent workloads?**

Four-layer cascade: (1) Semantic cache check -- if a semantically equivalent query was recently answered, return the cached response (100% savings, ~31% hit rate). (2) Plan cache check -- reuse execution plan templates from similar solved tasks (50% cost reduction, 96.6% quality retention). (3) Dynamic model routing -- classify complexity and route simple tasks to budget models (100--300x cost differential is the primary lever; RouteLLM achieves 85% cost reduction at 95% GPT-4 quality). (4) Prompt caching -- order prompts with static content first so repeated prefixes bill at up to 90% discount. Combined impact: 70--85% cost reduction on unoptimized baselines.

**Q6: Why do production deployments need both checkpointing and durable execution?**

They solve different failure classes. LangGraph checkpointing protects against application-level failures: bad reasoning, incorrect branches, human-in-the-loop pauses. The developer owns retry and resume logic. Temporal durable execution protects against infrastructure-level failures: container crashes, network partitions, host preemptions. The runtime owns retry, resume, and side-effect deduplication. A Kubernetes pod preemption mid-workflow needs Temporal to resume. A bad agent decision needs a LangGraph checkpoint to rewind to.

**Q7: What does least agency mean and why does it matter?**

Least agency extends beyond least privilege. Least privilege asks what an agent may read; least agency asks what it may do. An agent that summarizes documents should not hold the ability to send mail, even if it may lawfully read the mailbox. This matters because agents are non-deterministic entities that select their own tools -- static RBAC alone cannot govern the gap between "can access" and "should act." Enforcement requires credential proxying (agent never sees real tokens), tool-level RBAC (not just data-level), and sandbox isolation (enclave boundaries enforce what prompt-layer controls cannot).

---

### Key Numbers to Memorize

```
 ┌─────────────────────────────────────────────────────────┐
 │ 57%  -- orgs with agents in production (2025)          │
 │ 3%   -- orgs successfully scaling agents (IDC/AWS)     │
 │ 95%  -- pilots stalled by integration, not models      │
 │ 5-30x -- token multiplier: agents vs chatbots          │
 │ 3-15% -- tool-calling failure rate in production       │
 │ 31%  -- production failures from tool misuse           │
 │ 41-86% -- multi-agent system failure rate              │
 │ 0.95^5 = 77.4% -- 5 agents at 95% individual          │
 │ 70-85% -- cost reduction from caching + routing        │
 │ 90%  -- max prompt cache discount (Anthropic)          │
 │ $47K -- infinite loop incident cost (11 days)          │
 │ 620ms-1.8s -- LangGraph routing latency per hop        │
 │ 14.4% -- orgs with full agent fleet security approval  │
 │ 97%  -- AI-breached orgs lacking access controls       │
 │ 150+ -- orgs using A2A in production (2026)            │
 │ 85%  -- token reduction from MCP Tool Search           │
 └─────────────────────────────────────────────────────────┘
```
