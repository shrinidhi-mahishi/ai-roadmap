# Module 02: How AI Agents Work

**Scope**: Agent execution engines (ReAct, Plan-and-Execute, Hybrid, ReWOO, LLMCompiler), state orchestration (DAG graphs, supervisor-worker), communication protocols (MCP, A2A), tool-use mechanics, token economics, durable execution, zero-trust security, production failure taxonomy, and enterprise system design.

---

### What Is This?

An AI agent is a system that produces **side effects** -- it changes the world rather than just generating text. Where a chatbot maps input to output in a single turn, an agent determines intermediate steps autonomously, calls tools, observes results, and loops until a goal is met or a budget is exhausted. Think of it like a travel assistant who does not just tell you about flights but actually searches airlines, compares prices, books the ticket, and emails you the confirmation -- each step decided based on what it learned from the previous one.

The LLM inside the agent is a **processor, not a knowledge base** -- it proposes next actions and interprets observations, but current facts come from tools and memory systems. Three defining properties separate agents from chatbots:

1. **Autonomy** -- the agent selects intermediate steps without a pre-written script.
2. **Proactiveness** -- it recognizes missing information and takes initiative (with designed uncertainty thresholds and "ask-for-help" triggers).
3. **Action** -- it executes operations through defined tool interfaces (APIs, browsers, file systems).

The **PEAS framework** (classical AI) specifies agent design: **Performance** (measurable success criteria), **Environment** (operational boundaries -- which APIs, systems, data are in scope), **Actuators** (available tools), and **Sensors** (observation mechanisms -- parsing API responses, reading page content, detecting confirmation signals).

### Why It Matters

Every production AI system that goes beyond single-turn Q&A is an agent. 57% of organizations now have agents in production (LangChain 2025), yet only 3% successfully scale them across departments (IDC/AWS, N=900+). The bottleneck is architecture, not model capability. ~95% of generative AI pilots stall due to flawed enterprise integration, not issues with AI models themselves (MIT/NANDA, August 2025).

---

## 1. System Topology & Data Flow

### Architecture Diagram

```
 CONTROL PLANE (Orchestrator)
 +-----------------------------------------------------------+
 |  Runner / StateGraph compiler / ADK workflow outer loop    |
 |  turn caps, routing/handoffs, approval gates, checkpoints  |
 |                                                             |
 |  +------------------+  +------------------+  +------------+|
 |  | OpenAI Runner    |  | LangGraph        |  | Google ADK ||
 |  | (DEFAULT_MAX_    |  | StateGraph +     |  | Loop/Seq/  ||
 |  |  TURNS = 10)     |  | Checkpointer    |  | Parallel   ||
 |  +--------+---------+  +--------+---------+  +------+-----+|
 +-----------|---------------------|------------------|---------+
             |                     |                  |
             v                     v                  v
 DATA PLANE (LLM Inference + Tool Execution)
 +-----------------------------------------------------------+
 |  model completions, tool executions, observations          |
 |  Thought/Action/Obs trajectory appended into context       |
 |  (perceive -> plan/reason -> act -> observe)               |
 +--------+-------------------+-------------------+-----------+
          |                   |                   |
 +--------v---------+ +------v----------+ +------v-----------+
 | TOOL PROXY LAYER | | STATE LAYER     | | TELEMETRY        |
 +------------------+ +-----------------+ +------------------+
 | Schema validate  | | App State:      | | Step count       |
 | RBAC per tool    | |  Checkpointer   | | Token $          |
 | Sandbox execute  | |  (PG/Redis/     | | Breaker state    |
 | Credential proxy | |   Temporal)     | | Loop detection   |
 |  (vault-backed)  | | Cache Tier:     | | Cost attribution |
 | Concurrency caps | |  Prompt cache   | | Audit trail      |
 | Allow/deny lists | |  Semantic cache | | Rate-limit hdrs  |
 | Injection screen | |  Plan cache     | | Correlation IDs  |
 +------------------+ +-----------------+ +------------------+
```

### Plane Responsibilities

| Plane | What lives here | Examples |
|-------|----------------|---------|
| **Control plane** | Orchestrator/runner/graph compiler: turn limits, routing (handoffs), approval gates, checkpoint policy, tool registrations | OpenAI `Runner` loop; LangGraph compiled `StateGraph` + checkpointer; ADK `SequentialAgent` / `ParallelAgent` / `LoopAgent` outer loop |
| **Data plane** | Model completions + tool executions + observation messages appended into context | Anthropic `tool_use` / `tool_result` round trip; OpenAI classify: final / handoff / tools |
| **Tool proxy** | Schema registration -> host validation -> execution; MCP remote connector; credential proxying | JSON Schema tools; `tool_execution.max_function_tool_concurrency`; MCP allowlist/denylist + OAuth Bearer |
| **Persistence** | Two fundamentally different stores: (1) application state (conversation history, tool results, plan progress, checkpoints) -- transactional, must survive process death; (2) cache tier (prompt cache, semantic cache, plan cache) -- soft, best-effort | LangGraph `thread_id` + PostgresSaver; ADK `Session` + `user:`/`app:`/`temp:` state; Temporal Workflow history |
| **Telemetry** | Token/cost meters, rate-limit headers, HITL decision trails, correlation IDs, loop detection | OpenAI `Retry-After` / `x-ratelimit-*`; Temporal event history; LangGraph time-travel checkpoints |

### End-to-End Request Flow

**Step 1 -- Ingress.** Client submits a task (e.g., "book a flight under $500 SFO to JFK next Tuesday"). Gateway authenticates, stamps correlation-id, checks per-user/session budget ceiling. If session budget is exhausted, reject immediately. Control plane loads prior checkpoint or empty trajectory.

**Step 2 -- Plan / Route.** Orchestrator decides execution strategy: pure ReAct (simple, exploratory), Plan-and-Execute (known phases), or Hybrid (structured plan with ReAct within phases). For complex tasks, the planner decomposes into phases with verification checkpoints between them. For multi-agent systems, the supervisor routes to a specialist worker.

**Step 3 -- Reason (LLM call).** Context window = working RAM: system instructions + trajectory + prior tool outputs. The LLM either emits final text, a handoff (switch agent), or one or more structured tool calls -- not free-form "browsing."

**Step 4 -- Classify (control plane).** OpenAI `Runner`: (a) final output of desired type with no tool calls -> return; (b) handoff -> switch agent, re-run; (c) tool calls -> execute, append results, re-run; (d) `max_turns` exceeded -> `MaxTurnsExceeded` (default `DEFAULT_MAX_TURNS = 10`; `max_turns=None` disables).

**Step 5 -- Validate and execute tool.** Tool proxy validates JSON against registered schema (reject invented params). RBAC checks whether this agent/user may invoke this tool. Sandbox executes with timeout. Credentials are fetched from vault -- the agent never sees real API tokens. Result is JSON-encoded and injection-screened.

**Step 6 -- MCP branch (optional).** Messages API `mcp_servers` + `mcp_toolset` connect remote HTTPS MCP servers; allowlist/denylist tools; OAuth `authorization_token`; only tool calls supported (not full MCP feature set); no local STDIO via connector.

**Step 7 -- Observe.** Tool results (or errors) append as observations. If the result is empty (HTTP 200 with no payload), the system flags it rather than treating silence as success. Compress large tool dumps externally (e.g., 47 flights -> top 3 in context).

**Step 8 -- Loop or terminate.** The agentic loop checks: step count < recursion limit? Token budget remaining? Wall-clock < timeout? No repeated identical tool calls detected? If all pass, return to step 3. If any fail, force termination with partial result or escalation to human.

**Step 9 -- Checkpoint and persist.** LangGraph snapshots channel state each super-step under `thread_id`. Pending writes from successful sibling nodes persist if one node fails mid-step. Final answer delivered to client. Cost, latency, step count, and tool call audit trail written to telemetry.

**Step 10 -- Terminate.** Final answer, escalate flag (ADK `EventActions.escalate=True`), or turn/iteration cap. Telemetry records tokens, rate-limit headers, and audit decisions.

**Message style**: observe-reason-act cycle under a code-owned outer loop. Parallelism is at tool concurrency within a turn or DAG ready-set (LLMCompiler), not an unconstrained peer mesh.

---

## 2. Core Mechanics & Algorithms

### 2.1 ReAct (Reason + Act) -- The Dominant Execution Engine

ReAct (Yao et al., ICLR 2023) interleaves verbal **Thought**, environment **Action**, and **Observation**. Thoughts do not affect the environment; they update context for subsequent actions. On ALFWorld / WebShop, few-shot ReAct beat imitation/RL baselines by **+34%** and **+10%** absolute success with 1-2 in-context examples. HotpotQA/FEVER setups used dense thought-action-observation steps with Wikipedia `search` / `lookup` / `finish`.

```
State machine:

    +----------+    tool_use     +--------+   result     +----------+
    |  REASON  |--------------->|  ACT   |------------>| OBSERVE  |
    +----------+                +--------+             +-----+----+
         ^                                                   |
         |                  not done                        |
         +---------------------------------------------------+
         |
         |  stop / budget exhausted / max steps
         v
    +----------+
    |   DONE   |
    +----------+

    Optional branch: approval pause -> serialize RunState -> resume
```

**Key invariant**: each step depends on the observation from the prior step. No pre-written script. This makes ReAct adaptive but also means early mistakes compound through later steps.

**Invariants table:**

| Invariant | Binding |
|-----------|---------|
| Turn budget | OpenAI default `max_turns=10`; ReAct paper used step caps (e.g., 7 HotpotQA / 5 FEVER when backing off to CoT-SC); ADK `max_iterations` and/or escalate |
| Termination | Final typed output with no tool calls; escalate; max turns; human reject |
| Message pairing | Handoffs must keep AIMessage <-> ToolMessage pairs valid |
| Schema gate | Invented tool parameters rejected before side effects |

**Complexity**: for N tool steps with growing context size C_i ~ C_0 + i*delta, model calls = N+1. Token volume ~ sum(C_i) = O(N^2 * delta) in the naive replay case. This matches ReWOO/LangChain motivation that ReAct cost scales with steps x cumulative context. Inference cost per step increases as context grows: total inference cost is O(k * n_avg^2) without summarization, or O(k * n_window^2) with sliding-window summarization.

**Production caveat**: CoT alone suffers fact hallucination and error propagation; tool grounding reduces but does not eliminate early-observation poisoning.

### 2.2 Plan-and-Execute

**Plan-and-Solve** (Wang et al., ACL 2023): devise a plan that divides the task into subtasks, then carry out the plan -- zero-shot CoT variant that reduces missing-step errors.

**LangChain/LangGraph Plan-and-Execute**: (1) planner LLM emits multi-step plan, (2) executor(s) run steps with tools, (3) re-plan or finish. Benefits vs ReAct: fewer frontier planner calls per tool step; can use smaller models for sub-tasks; forces explicit whole-task reasoning. Rigidity: without replan, unexpected observations cannot course-correct mid-plan.

```
     +---------+     +----------+     +----------+     +---------+
     |  PLAN   |---->| EXECUTE  |---->| OBSERVE  |---->| REPLAN? |--yes--> PLAN
     | (large) |     | (tools / |     |  step i  |     | or DONE |
     +---------+     |  small)  |     +----------+     +----+----+
                     +----------+                           | no
                                                            v
                                                         FINISH
```

### 2.3 Hybrid Pattern (Recommended in Practice)

Outer plan with phase checkpoints; ReAct (or tool loop) inside each phase:

```
    +------------+     +------------+     +------------+
    |  Phase 1   |---->| Checkpoint |---->|  Phase 2   |----> ...
    | ReAct loop |     | Verify /   |     | ReAct loop |
    | (search)   |     | HITL gate  |     | (execute)  |
    +------------+     +------------+     +------------+
```

Each phase runs a bounded ReAct loop. Checkpoints between phases allow verification before irreversible actions (e.g., verify the found flight before booking).

### 2.4 ReWOO / LLMCompiler (Plan -> Execute DAG)

**ReWOO** (Xu et al.): detaches planning from interleaved observations. Planner emits plan with variable placeholders (`#E1`...); Worker executes tools; Solver answers. Reported **~5x token efficiency** and **+4% HotpotQA accuracy** vs ReAct. Failure mode: no mid-flight adapt if evidence contradicts the fixed plan.

**LLMCompiler** (Kim et al.): Planner streams a **DAG** of tasks with dependencies; Task Fetching Unit schedules ready tasks in parallel; Joiner decides replan vs finish. Reported vs ReAct:

| Benchmark | Latency Improvement | Cost Improvement |
|-----------|-------------------|-----------------|
| HotpotQA | **1.80x** faster | **3.37x** cheaper |
| Movie Recommendation | **3.74x** faster | **6.73x** cheaper |
| ParallelQA | -- | **4.65x** cheaper |
| Token sample (HotpotQA) | ReAct ~2900 in / 120 out vs LLMCompiler ~1300 / 80 | -- |

**Scheduling complexity**: topological ready-set scheduling over a DAG of V tool tasks is O(V+E) per joiner cycle; parallelism bounded by independent ready nodes + tool concurrency caps.

**When to use LLMCompiler multipliers**: use Movie Rec 6.73x only when the workload matches that benchmark's parallelism -- do not universalize. Cite HotpotQA 3.37x for general cost discussions.

### 2.5 Supervisor / Orchestrator-Workers

**Anthropic orchestrator-workers**: central LLM dynamically decomposes, delegates to workers, synthesizes -- suited when subtasks are unpredictable (e.g., multi-file code edits). Related composable patterns: prompt chaining, routing, parallelization (sectioning/voting), evaluator-optimizer.

**LangChain subagents**: main agent (supervisor) invokes workers as **tools**; subagents typically **stateless** per call (context isolation); multiple subagents in one turn; distinct from a one-shot **router**. Deprecated `langgraph-supervisor` -> migrate to tool-wrapped `create_agent` subagents.

**Handoffs**: tool-driven transfer of control (OpenAI term); LangGraph `Command(goto=..., update=...)` / `Command.PARENT`. Google ADK **AgentTool** wraps a child as a function declaration for the parent `LlmAgent`.

```
                    +----------------+
                    |  SUPERVISOR    |<---- synthesize / next delegate
                    +-------+--------+
                            | invoke workers-as-tools
              +-------------+-------------+
              v             v             v
         +--------+   +--------+   +--------+
         |Worker A|   |Worker B|   |Worker C|  (stateless per call)
         +--------+   +--------+   +--------+
```

**Production standard**: 57% of organizations with multi-agent systems in production use supervisor-worker. Topology guidance:

| Topology | When to use |
|----------|-------------|
| **Hub-and-spoke** | Up to ~6 workers |
| **Hierarchical** | 6+ workers or when workers need their own sub-teams |
| **Skip supervisor** | <3 distinct capabilities, no external tool calls, or latency budget <2 seconds |

**Critical invariant**: the #1 production bug in naive supervisor implementations is a worker returning empty and the supervisor treating it as success. Fix: Pydantic output validation at every worker boundary.

### 2.6 Convergence Properties

| Pattern | Converges when | Diverges when |
|---------|---------------|---------------|
| ReAct | `finish` / final text / step cap | Looping failed tools; LLMCompiler notes ReAct looping and early stopping on HotpotQA / Movie Rec |
| Plan-and-Execute | Plan empty / replan says done | Stale plan without replan |
| Supervisor | Synthesized answer / handoff complete | Endless re-delegation without turn budget |
| ADK LoopAgent | `max_iterations` or escalate | Missing escalate + unbounded iterations |

### 2.7 Communication Protocols (MCP and A2A)

**MCP (Model Context Protocol)** -- agent-to-tool standard. Open standard by Anthropic (Nov 2024), donated to Linux Foundation (Dec 2025). Defines how agents connect to external tools, data sources, and services. Key capability: **Tool Search Tool** discovers tools on-demand instead of loading all definitions upfront -- **85% reduction** in token usage. Opus 4 improved from 49% to 74% accuracy; Opus 4.5 from 79.5% to 88.1% with Tool Search enabled.

**A2A (Agent2Agent Protocol)** -- agent-to-agent standard. Open standard by Google (Apr 2025), donated to Linux Foundation (Jun 2025). Enables agents to discover, authenticate, and delegate tasks across platforms. Core primitives: Agent Card (JSON capability advertisement), Task Object (JSON-RPC 2.0 work unit), Artifacts (typed outputs), push notifications/SSE for streaming. A2A v1.2 (2026) added signed agent cards for domain verification. 150+ organizations in production.

**Complementary, not competitive**: MCP gives an agent its "hands" (tool access). A2A gives a team of agents the ability to coordinate. The converging enterprise stack: A2A for coordination, MCP for tool access, shared context layer for governed business knowledge.

### 2.8 Tool Use Mechanics

LLMs produce text only. Tools enable real-world action via native tool calling:

1. **Schema registration**: tools declared with name, description, JSON Schema parameters in the API request.
2. **Model emission**: structured function/tool call (not free-form "browsing").
3. **Host validation + execution**: verify required fields/types; call real API; return observation; reject invented parameters.
4. **Concurrency**: OpenAI SDK can start all local function tools emitted in a turn, or cap with `tool_execution.max_function_tool_concurrency` (>= 1) -- independent of provider `parallel_tool_calls`.
5. **MCP path**: Messages API `mcp_servers` + `mcp_toolset` connects remote HTTPS MCP; allowlist/denylist; OAuth `authorization_token`; only tool calls supported.

**Tool-calling fails 3-15% of the time** in production, varying by model size and task complexity. 31% of production failures come from tool misuse. Four primary failure modes:

| Mode | Description | Detection | Severity |
|------|-------------|-----------|----------|
| **Hallucinated parameters** | Agent invents options not in schema | Schema validation | Medium -- caught by validation |
| **Hallucinated tool invocations** | Agent calls nonexistent tools | Registry check | Medium -- caught by registry |
| **Silent failures** | HTTP 200 with empty payload | Empty-response detector | **Critical** -- no error surfaces |
| **Context-truncated calls** | Long history pushes tool defs past attention | Duplicate/contradictory call detector | High -- redundant or wrong calls |

ACI (Agent-Computer Interface) hygiene: poka-yoke args (e.g., require absolute file paths after SWE-bench relative-path failures); invest in tool docs as much as prompts.

### 2.9 Reliability Math for Multi-Agent Systems

Multi-agent LLM systems fail **41-86% of the time** depending on task complexity. Even with well-trained individual agents, end-to-end reliability degrades multiplicatively:

```
P(system_success) = P(agent_1) * P(agent_2) * ... * P(agent_n)

5 agents at 95% individual accuracy: 0.95^5 = 0.774 (77.4% system success)
5 agents at 90% individual accuracy: 0.90^5 = 0.590 (59.0% system success)
```

Three error propagation patterns:
1. **Factual drift**: one agent confidently states something incorrect; next agent treats it as ground truth.
2. **Context window poisoning**: bad tool result appended to shared scratchpad contaminates every future call.
3. **Cascading retries**: downstream agent detects problem, triggers self-correction, spawning more failing tool calls.

Errors typically pass through **3-4 reasoning layers** before surfacing. A single hallucination forwarded to 3 subagents produces 3 wrong answers, each with apparent coherence.

### 2.10 Framework Comparison

| Dimension | LangGraph | OpenAI Agents SDK | CrewAI |
|-----------|-----------|-------------------|--------|
| **Abstraction level** | Low-level graph primitives | Mid-level, opinionated 4-primitive API | High-level role-based |
| **Orchestration** | DAG with cycles, conditional edges | Runner-driven agentic loop | Sequential or hierarchical processes |
| **Agent transfer** | Subgraphs with shared/isolated state | Handoffs (full conversation transfer) | Delegation via manager agent |
| **State persistence** | Checkpointer (Postgres, SQLite, Redis) | Session-level + long-horizon harness (Apr 2026) | SQLite-backed Flow persistence |
| **Guardrails** | Custom middleware (v1.1, Dec 2025) | Input/Output/Tool guardrails (built-in) | Tool-level; via Bedrock integration |
| **Observability** | OpenTelemetry integration | Built-in tracing to OpenAI dashboard | Logging; enterprise dashboard |
| **Ideal for** | Complex stateful workflows | Rapid multi-agent with handoffs | Role-based team simulation |

---

## 3. Token Economics & NFR Analysis

### 3.1 The Cost Problem

Token prices fell ~80% between 2025 and 2026, yet enterprise AI bills went up. Enterprise LLM API spend passed $8.4B in 2025. Agents make **3-10x more LLM calls** than simple chatbots. Gartner (March 2026): agentic models require **5-30x more tokens per task** than a standard chatbot. 60% of AI projects exceed original cost estimates by 30-50%.

**Incident**: Two LangChain-based agents entered an infinite conversation cycle that ran for 11 days, generating a **$47,000 bill** before detection (November 2025).

### 3.2 Cost Formulas -- $ per 1k Runs

**Assumptions (state explicitly; substitute your contract rates):**

| Symbol | Assumed Value | Role |
|--------|--------------|------|
| P_in | $3.00 / 1M input | Frontier list-class placeholder |
| P_out | $15.00 / 1M output | Same |
| ReAct trajectory | N=8 model turns; avg 3,000 input + 150 output tokens/turn | Aligns with research |
| GPT-4o-class | $2.50 / $10.00 per 1M | Mid-tier reference |

**A. Naive ReAct (no cache, frontier):**

```
Cost_1run = N * (3000/1M * P_in + 150/1M * P_out)
         = 8 * (0.009 + 0.00225) = $0.090

Cost_1k_runs ~ $90
```

**B. ReWOO-class (~5x token efficiency on HotpotQA):**

```
Cost_1k_ReWOO ~ $90 / 5 = $18
```

**C. LLMCompiler-class (HotpotQA 3.37x):**

```
Cost_1k_LLMCompiler ~ $90 / 3.37 ~ $26.7
```

**D. Per-task GPT-4o-class (5-step agent):**

```
Cost_per_task = (2000 * $0.0000025 + 500 * $0.00001) * 5
             = ($0.005 + $0.005) * 5 = $0.05

At 1,000 tasks/day = $50/day = $1,500/month unoptimized
```

**E. Unconstrained complex agent** (software engineering tasks): **$5-8 per task** at premium model rates.

### 3.3 Prompt Cache Impact Math

GPT-5.6+ multipliers: write **1.25x**, read **0.1x** uncached input. Minimum cacheable prefix: 1,024 tokens. TTL: GPT-5.6+ only `30m` (default); older models 5-10 min inactivity. Traffic above ~15 RPM per cache key can overflow machines and hurt hits.

For a stable system+tools prefix of S=2,000 tokens reused across 8 turns with 1 write + 7 reads:

```
PrefixCost_8 = P_in * (S/1M) * (1.25 + 7 * 0.1)
            = 3 * (2000/1M) * 1.95 = $0.0117

vs 8x full uncached prefix = 3 * 8 * 2000/1M = $0.048
```

1 write + 9 full reads ~ **2.15x** vs **10x** uncached for that prefix.

**Cache breakers**: tool name/description/schema/order changes, `parallel_tool_calls`, structured output format, reasoning effort, verbosity, compaction. Compaction/summarization can break prompt-cache prefixes.

Anthropic: up to **90% cost reduction** on cached prefixes + 13-31% TTFT improvement. One developer: $720/month to $72/month via prompt caching alone.

### 3.4 Dynamic Model Routing

Route easy tasks to cheap models, complex tasks to premium ones. The **100-300x** cost differential between premium and small model tiers is the primary leverage point.

- **RouteLLM** (UC Berkeley/Anyscale/Canva, ICLR 2025): **85% cost reduction** maintaining **95% of GPT-4 performance**.
- Moving 70% of requests to budget tier: ~60% LLM cost reduction.

If 6 of 8 turns use a model at 0.2x of frontier and 2 use full frontier:

```
Cost_1k_routed ~ 1000 * (6 * 0.2 * c_turn + 2 * c_turn)
              = 1000 * 3.2 * c_turn
              with c_turn = $0.01125 from ReAct -> ~ $36 / 1k (illustrative mix)
```

### 3.5 Four-Layer Cost Optimization Cascade

```
 Request
    |
    v
 +-------------------+  hit
 | Semantic cache     |-------> Return cached response (100% savings)
 | (~31% query overlap|        No LLM call needed
 +--------+----------+
          | miss
          v
 +-------------------+
 | Plan cache         |-------> Execute cached plan template (50% savings)
 | (NeurIPS 2025:     |        96.61% quality, 27% latency reduction
 |  50.31% cost cut)  |
 +--------+----------+
          | miss
          v
 +-------------------+
 | Complexity         |--- simple ---> Budget model (Haiku/Flash-Lite)
 | classifier         |--- medium ---> Mid-tier (Sonnet/Flash)
 | (RouteLLM)         |--- complex --> Frontier (Opus/Pro)
 +-------------------+
          | failure
          v
 +-------------------+
 | Escalation         |-------> Retry with next tier up
 +-------------------+
```

**Combined impact**: caching + routing together delivers **70-85% cost reduction** on unoptimized baselines. One practitioner documented 90% total reduction through combined RAG optimization, prompt compression, and context pruning.

### 3.6 Budget Governance (Three-Layer)

```
 +------------------------------------------------+
 | Layer 1: Session ceiling                        |
 |   Max tokens/dollars per individual session     |
 |   Hard terminate when reached                   |
 +------------------------------------------------+
 | Layer 2: Per-user / per-feature quotas          |
 |   Daily/weekly caps attributed to features      |
 |   Alert at 80%, throttle at 100%                |
 +------------------------------------------------+
 | Layer 3: Organizational spending limits         |
 |   Alert at 60%, hard stop at 100%               |
 |   Finance dashboard + anomaly detection         |
 +------------------------------------------------+
```

**Tooling**: LiteLLM, Portkey, OpenRouter support multi-model routing, semantic caching, budget enforcement, and failover out of the box.

### 3.7 Latency SLA Targets

**TTFT (Time to First Token) -- 2026 Production Benchmarks:**

| Model Class | P50 | P95 | P99 |
|-------------|-----|-----|-----|
| Frontier (GPT-5.5 / Opus 4.7 / Gemini 3) | 0.85-1.4s | 1.6-2.4s | ~3.2s |
| Reasoning (Opus 4.7 max thinking) | ~28s | -- | -- |
| Reasoning (GPT-5.5 Pro high) | ~67s | -- | -- |
| Reasoning (Gemini 3 DT high) | ~52s | -- | -- |

P95/P50 ratio averages 2.1x, worst pairings hit 3.2x. **SLO design should anchor on P95, not P50.**

**UX thresholds**: Chat UX requires sub-2s P95 TTFT (defensible bar), sub-1s (premium bar). Streaming throughput: 50 TPS feels slow, 100 TPS normal, 200+ TPS instant.

**Component-level latency (inferred engineering SLAs):**

| Component | p50 | p95 | p99 |
|-----------|-----|-----|-----|
| Model TTFT | 400ms | 1,000ms | 2,500ms |
| Output decode (150 tokens) | 3,750ms | 3,750ms | 4,950ms |
| Tool round-trip | 200ms | 800ms | 3,000ms |
| Checkpoint write | 50ms | 150ms | 400ms |
| **Short tool-using turn** | **4,400ms** | **5,700ms** | **10,850ms** |
| **N=8 ReAct trajectory** | **35,200ms** | **45,600ms** | **86,800ms** |
| LangGraph routing latency | 620ms-1.8s per hop | -- | -- |
| Gateway/guardrails overhead | +11ms | +14ms | +21ms |

**Regional latency**: US-East to APAC adds 180-220ms TTFT; EU to US-East adds 80-110ms.

**Baseten benchmark (Sep 2026)**: P99 end-to-end = 3.23s, median = 331ms, TTFT P95 = 1.16s, task success = 95.8%.

**Relative lever (citeable)**: LLMCompiler HotpotQA **1.80x** / Movie Rec **3.74x** vs ReAct -> budget compressions: ~19,600ms (p50 HotpotQA-like) or ~9,400ms (p50 Movie-Rec-like) when the DAG fits.

### 3.8 Throughput & Back-Pressure

| Anchor | Value |
|--------|-------|
| chatgpt-4o-latest Tier 1 | **500 RPM / 30k TPM** |
| chatgpt-4o-latest Tier 5 | **10k RPM / 30M TPM** |
| Usage tiers | Free -> T1 ($5) -> ... -> T5 ($1,000) cumulative spend |
| Ramp | After ~1M input TPM, increase <=50% every 15 minutes |

**Capacity insight**: a 10-turn agent with ~3k tokens/turn ~ 30k tokens/request -> Tier 1 TPM exhausted at ~1 concurrent full agent completion per minute if saturated. This drives caching, smaller step models, or plan-and-execute.

**Back-pressure design:**

1. Honor `Retry-After` and `x-ratelimit-*` at the **runner**, not inside the LLM prompt.
2. When RPM-bound but TPM-available, batch multiple tasks into one request.
3. Queue/shed non-critical agents; keep `max_turns` and cost caps as hard shed valves.
4. Cap local tool concurrency (`max_function_tool_concurrency`) to protect downstream APIs.

### 3.9 NFR Targets for Agent Systems

| NFR | Target | Trade-off |
|-----|--------|-----------|
| **Availability** | 99.9% (multi-provider circuit break) | Checkpointer alone persists data, not execution -- need outer orchestrator to resume |
| **TTFT P95** | <2s (interactive), <5s (agentic) | Streaming tokens after TTFT masks wall clock |
| **Task success rate** | >95% (simple), >80% (complex) | Higher steps/models raise $ and latency |
| **RPO (state loss)** | 0 (checkpointed state per super-step) | `store=False` / ZDR websocket flows lose state |
| **RTO (resume time)** | <30s from last checkpoint | Longer HITL windows raise RTO for "done" |
| **Cost per task** | Track, alert at 2x baseline | More autonomy = more steps = more $ |
| **Max steps/task** | Hard cap (e.g., 25 default) | -- |
| **Blast radius** | Single-tenant failure isolation | -- |

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution Patterns

Temporal raised $300M at $5B valuation (Feb 2026), with 9.1 trillion lifetime action executions -- 1.86 trillion from AI-native companies. OpenAI runs Temporal for Codex, handling millions of daily coding agent requests.

**Journal-based replay**: record each completed step; replay on crash. A new worker replays event history from the beginning -- every completed Activity call is skipped (reads recorded result from history). When replay reaches the last completed step, normal execution resumes.

**Critical constraint**: workflow code must avoid non-deterministic operations (random numbers, timestamps, direct network calls). Those must be wrapped as Activities whose results are recorded. Pattern: anything touching the outside world is an Activity; everything else is deterministic workflow logic.

### 4.2 Checkpointing vs. Durable Execution

| Concern | Checkpointer (LangGraph) | Durable Execution (Temporal) |
|---------|-------------------------|------------------------------|
| **Who owns retry** | Developer | Runtime |
| **Who owns resume** | Developer | Runtime |
| **Side-effect dedup** | Developer | Runtime (event sourced) |
| **Protects against** | App-level failures, bad reasoning, HITL | Infra-level failures, container crashes, network |
| **Resume granularity** | From last checkpoint | From any completed step |
| **Pending writes** | Successful siblings persist on failure | Activity replay skips completed |
| **Savers** | InMemory (dev); SQLite/Postgres (prod) | Temporal cluster or Cloud |

**Production deployments often need BOTH.** Checkpointing cuts wasted processing by 60%+ on multi-step workflows.

**Competing platforms (2026):**

| Platform | Mechanism | Differentiator |
|----------|-----------|---------------|
| **Temporal** | Journal/replay, multi-language SDKs | Mature; 3,000+ customers (NVIDIA, Netflix, OpenAI). GA integration with OpenAI Agents SDK (Mar 2026) |
| **Restate** | Journal/replay, lighter footprint | Same mechanism, simpler deployment model |
| **DBOS** | All state in Postgres | Zero explicit checkpoint code; any decorated function is durable |
| **Inngest** | Durable execution for serverless | Steps, waits, retries without infrastructure management |

**Event history growth**: for long-running workflows, event histories grow unbounded. Temporal's **Continue-As-New**: atomically complete current run and start new run with same workflow ID, carrying forward only essential state.

**HITL patterns**: OpenAI `needs_approval` -> interruptions -> serialize `RunState` -> approve/reject -> resume. LangGraph `interrupt()` + Temporal Workflow `wait_condition` / signal for durable HITL. ADK `LoopAgent` terminates via `max_iterations` and/or `EventActions.escalate=True`.

### 4.3 Failure Taxonomy

```
 FAILURE TAXONOMY FOR AI AGENTS

 CONTEXT FAILURES
   Attention decay: initial instructions lose weight over turns
   Working-memory rot: agent's own trace corrupts active memory (EPAM)
   Specification drift: by ~20th turn, optimizing adjacent goal
   Context overflow: history fills window, truncates tool defs

 LOOP FAILURES
   Infinite retry loop: same tool call with identical inputs
     (68 confirmed across 47 projects -- IAL-Scan, 6,549 repos)
   Agent paralysis: contradictory signals, no action taken
   Polling tax: hyperactive status-check instead of webhook wait

 PROPAGATION FAILURES
   Factual drift: confident wrong output treated as ground truth
   Context poisoning: bad tool result contaminates shared state
   Cascading retries: 10 agents x 10 retries = 100 dead requests

 TOOL FAILURES (31% of production failures)
   Schema violations: wrong types, missing required fields
   Hallucinated invocations: calls to nonexistent tools
   Silent failures: HTTP 200, empty payload, no error surfaced
   Context-truncated calls: tool defs pushed past attention range

 INFRASTRUCTURE FAILURES
   Cascading API timeouts: circular deps + no timeout config
   Worker cascade: worker returns empty, supervisor treats as OK
   Retry storms: exponential multiplication of upstream requests

 SECURITY FAILURES
   Direct prompt injection: overwrite system instructions
   Indirect injection: malicious payload in RAG/tool output
```

**Detection principle**: monitor **step counts and output similarity** across turns, not just latency/error rates. A looping agent may never throw an error while burning compute on identical retries.

**Root cause of loops**: agents are given objectives but not exit conditions. They oscillate between spinning when they should stop and stopping when they should escalate.

### 4.4 Zero-Trust MCP Architecture

Traditional Zero Trust breaks for AI agents because agents are non-deterministic, goal-interpreting entities that select their own tools, chain API calls, and spawn sub-tasks. Static RBAC is "like trying to govern a conversation with a list of approved words."

**Anthropic Managed Agents** (April 2026) -- three mutually untrusted components:

```
 +----------------------------------------------------------+
 |                    MANAGED AGENT                          |
 |                                                           |
 |  +----------+    +--------------+    +----------------+  |
 |  |  BRAIN   |    |   SESSION    |    |    HANDS       |  |
 |  | Claude + |    | Append-only  |    | Disposable     |  |
 |  | harness  |    | event log    |    | Linux sandbox  |  |
 |  | (routing |    | (outside     |    | (code runs     |  |
 |  | decisions|    |  both brain  |    |  here, no      |  |
 |  |  only)   |    |  and hands)  |    |  credentials)  |  |
 |  +----+-----+    +--------------+    +-------+--------+  |
 |       |                                      |           |
 |       | session-bound token                  | request   |
 |       v                                      v           |
 |  +---------------------------------------------------+  |
 |  |              CREDENTIAL PROXY                      |  |
 |  |  Fetches real OAuth tokens from vault              |  |
 |  |  Makes external call on behalf of agent            |  |
 |  |  Returns result -- agent never sees real token     |  |
 |  +---------------------------------------------------+  |
 +----------------------------------------------------------+
```

**NVIDIA NemoClaw**: five enforcement layers. Sandboxed execution with Landlock, seccomp, and network namespace isolation at kernel level. Default-deny outbound networking -- every external connection requires explicit operator approval via YAML policy.

**Zentera Enclave Model**: trust boundary containing sandboxed agents, authorized assets, and scoped tools. Agent in Project A's enclave cannot reach Project B's assets, tools, or agents. Prompt-layer controls tell an agent what it should not do; an enclave enforces what it cannot reach.

### 4.5 Tool-Level RBAC & Least Agency

**Least agency** extends beyond least privilege: least privilege asks what an agent may read; least agency asks what it may do. An agent that summarizes documents should not hold the ability to send mail, even if it may lawfully read the mailbox.

**Cloud Security Alliance Agentic Trust Framework (ATF)**: treats agent autonomy as something earned through demonstrated trustworthiness. Four maturity levels with progressively greater autonomy and governance requirements. Adds behavioral anomaly detection, PII protection, RBAC, and error tracking.

| Capability | Mechanism |
|-----------|-----------|
| Tool inventory | MCP allowlist/denylist; register only needed schemas |
| High-stakes actions | `needs_approval` / MCP `require_approval`; confirm before charge |
| Guardrails | Input/output guardrails; `tool_not_found_behavior` (raise or model-visible error) |
| Auth helpers | ADK `ToolContext.request_credential` / `get_auth_response` |
| ACI hygiene | Poka-yoke args (absolute file paths); invest in tool docs as much as prompts |

Enforce rate limits, allow lists, and verification gates **in code** -- agents scale mistakes instantly (e.g., email blast on vague "follow up with leads").

### 4.6 PII Filtering & Audit Logs

PII redaction must happen at access boundaries, before content enters the LLM context. Tools: Lakera Guard (acquired by Check Point, Sep 2025) inspects prompts and responses for injection, jailbreak, PII, and exfiltration at runtime. AWS Bedrock Guardrails and Azure Content Safety provide native checks.

1. **Detect** -- scan prompts, tool args, and observations for PII/secrets before model/MCP egress.
2. **Redact** -- replace with stable tokens in model context; keep mapping in a sealed vault.
3. **Audit** -- append-only event with correlation id, tool name, decision, redaction counts; Temporal/LangGraph history holds HITL decisions for chain-of-custody.

**Industry posture**: only **14.4%** of organizations reported full security approval for their entire agent fleet (Gravitee, Feb 2026, N=919). **97%** of organizations with AI-related breaches lacked proper AI access controls (IBM 2025).

**Sandbox isolation**: Anthropic Managed Agents: disposable Linux containers; credentials proxied through vault. NVIDIA NemoClaw: Landlock + seccomp + network namespace. OpenAI Agents SDK (Apr 2026): sandboxing primitive for safely executing untrusted code.

Zero-Trust MCP transport: remote Streamable HTTP or SSE only; `authorization_token` via OAuth; **tokens in URL query strings prohibited** (leak via logs/proxies; MCP auth spec forbids query-string access tokens).

---

## 5. Production Enterprise Code

### Complete Agent Runner with Resilience Primitives

Self-contained Python: agent loop with deterministic fake model (no API keys), retries with exponential backoff + full jitter, circuit breaker (closed -> open -> half-open), fallback model chain, tool registry with schema validation + RBAC, structured logging with correlation IDs, loop detection, PII redaction, and graceful degradation.

```python
#!/usr/bin/env python3
"""Production-shaped agent loop with resilience primitives (no API keys)."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol


# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """JSON lines with agent context for SIEM/observability."""
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.time(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
            "run_id": getattr(record, "run_id", None),
            "turn": getattr(record, "turn", None),
            "attempt": getattr(record, "attempt", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "provider": getattr(record, "provider", None),
            "tool": getattr(record, "tool", None),
            "cost_usd": getattr(record, "cost_usd", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps({k: v for k, v in payload.items() if v is not None}, default=str)


def build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOG = build_logger("agent.runtime")


def log_extra(**kwargs: Any) -> dict[str, Any]:
    return kwargs


# ---------------------------------------------------------------------------
# PII redaction pipeline
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    POISON = "poison"


class AgentError(Exception):
    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Circuit breaker: closed -> open -> half-open
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout_sec: float = 30.0
    window_sec: float = 60.0
    state: BreakerState = BreakerState.CLOSED
    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def _prune(self, now: float) -> None:
        self.failures = [t for t in self.failures if now - t <= self.window_sec]

    def allow(self) -> bool:
        now = time.monotonic()
        if self.state is BreakerState.OPEN:
            assert self.opened_at is not None
            if now - self.opened_at >= self.recovery_timeout_sec:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures.clear()
        self.state = BreakerState.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        now = time.monotonic()
        self._prune(now)
        self.failures.append(now)
        if self.state is BreakerState.HALF_OPEN:
            self.state = BreakerState.OPEN
            self.opened_at = now
            return
        if len(self.failures) >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = now


# ---------------------------------------------------------------------------
# Retries: exponential backoff + full jitter
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay_sec: float = 0.5
    max_delay_sec: float = 20.0

    def delay(self, attempt: int) -> float:
        # AWS-style full jitter: sleep ~ U(0, min(cap, base * 2^attempt))
        ceiling = min(self.max_delay_sec, self.base_delay_sec * (2**attempt))
        return random.uniform(0.0, ceiling)


# ---------------------------------------------------------------------------
# Tool registry with schema validation + RBAC
# ---------------------------------------------------------------------------

class ToolValidationError(ValueError):
    """Tool call failed validation."""


class ToolAccessDenied(PermissionError):
    """Agent lacks permission to invoke this tool."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(frozen=True)
class Observation:
    call_id: str
    name: str
    ok: bool
    payload: dict[str, Any]


class ToolRegistry:
    """Validates required fields, enforces RBAC, then executes deterministic tools."""

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._required: dict[str, set[str]] = {}
        self._allowed_roles: dict[str, set[str]] = {}
        self._agent_roles: dict[str, set[str]] = {}
        # Register demo tools
        self._register("search_flights", {"origin", "destination", "date"},
                        self._search_flights, {"*"})
        self._register("book_flight", {"flight_id", "confirm"},
                        self._book_flight, {"booking_agent", "*"})

    def _register(self, name: str, required: set[str],
                  fn: Callable, roles: set[str]) -> None:
        self._tools[name] = {"fn": fn}
        self._required[name] = required
        self._allowed_roles[name] = roles

    def grant_role(self, agent_id: str, role: str) -> None:
        self._agent_roles.setdefault(agent_id, set()).add(role)

    def execute(self, call: ToolCall, agent_id: str = "default") -> Observation:
        # Guard: hallucinated tool invocation
        if call.name not in self._tools:
            return Observation(call.call_id, call.name, False,
                             {"error": "tool_not_found",
                              "available": sorted(self._tools.keys())})
        # Guard: RBAC check
        allowed = self._allowed_roles[call.name]
        if "*" not in allowed:
            roles = self._agent_roles.get(agent_id, set())
            if not roles & allowed:
                return Observation(call.call_id, call.name, False,
                                 {"error": "access_denied",
                                  "required_roles": sorted(allowed)})
        # Guard: schema validation
        missing = self._required[call.name] - set(call.arguments)
        if missing:
            return Observation(call.call_id, call.name, False,
                             {"error": "schema_invalid", "missing": sorted(missing)})
        try:
            payload = self._tools[call.name]["fn"](call.arguments)
            # Guard: silent failure (empty response detection)
            if payload in (None, "", {}, []):
                return Observation(call.call_id, call.name, False,
                                 {"error": "empty_response",
                                  "warning": "Tool returned empty -- verify before proceeding"})
            return Observation(call.call_id, call.name, True, payload)
        except AgentError as exc:
            return Observation(call.call_id, call.name, False,
                             {"error": str(exc), "kind": exc.kind.value})

    @staticmethod
    def _search_flights(args: dict[str, Any]) -> dict[str, Any]:
        catalog = [
            {"flight_id": "AA100", "price": 420, "stops": 0},
            {"flight_id": "UA220", "price": 390, "stops": 1},
            {"flight_id": "DL310", "price": 455, "stops": 0},
        ]
        return {"origin": args["origin"], "destination": args["destination"],
                "date": args["date"], "top": catalog[:3]}

    @staticmethod
    def _book_flight(args: dict[str, Any]) -> dict[str, Any]:
        if args.get("confirm") is not True:
            raise AgentError("booking requires confirm=true", FailureKind.PERMANENT)
        return {"status": "booked", "flight_id": args["flight_id"], "pnr": "PNR-DEMO-42"}


# ---------------------------------------------------------------------------
# Model providers + fallback chain
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelOutput:
    kind: str  # "final" | "tool_calls"
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    provider: str = ""


class ModelProvider(Protocol):
    name: str
    def complete(self, messages: list[dict[str, Any]], turn: int) -> ModelOutput: ...


class PrimaryFakeModel:
    """Simulates a frontier model. Fails transiently a configurable number of times."""
    name = "primary"

    def __init__(self, fail_times: int = 0) -> None:
        self._remaining_failures = fail_times

    def complete(self, messages: list[dict[str, Any]], turn: int) -> ModelOutput:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise AgentError("model 503", FailureKind.TRANSIENT)
        has_search = any(m.get("role") == "tool" and m.get("name") == "search_flights"
                        and m.get("ok") for m in messages)
        has_book = any(m.get("role") == "tool" and m.get("name") == "book_flight"
                      and m.get("ok") for m in messages)
        if not has_search:
            return ModelOutput(kind="tool_calls", provider=self.name,
                tool_calls=(ToolCall("search_flights",
                    {"origin": "SFO", "destination": "JFK", "date": "2026-10-10"},
                    f"call-search-{turn}"),))
        if not has_book:
            last = next(m for m in reversed(messages)
                       if m.get("role") == "tool" and m.get("name") == "search_flights")
            fid = last["payload"]["top"][1]["flight_id"]
            return ModelOutput(kind="tool_calls", provider=self.name,
                tool_calls=(ToolCall("book_flight",
                    {"flight_id": fid, "confirm": True}, f"call-book-{turn}"),))
        return ModelOutput(kind="final", provider=self.name,
            text="Booked UA220 SFO->JFK on 2026-10-10. PNR-DEMO-42. Total $390.")


class SecondaryFakeModel:
    """Cheaper/faster fallback: answers from search alone without booking."""
    name = "secondary"

    def complete(self, messages: list[dict[str, Any]], turn: int) -> ModelOutput:
        has_search = any(m.get("role") == "tool" and m.get("name") == "search_flights"
                        and m.get("ok") for m in messages)
        if not has_search:
            return ModelOutput(kind="tool_calls", provider=self.name,
                tool_calls=(ToolCall("search_flights",
                    {"origin": "SFO", "destination": "JFK", "date": "2026-10-10"},
                    f"call-search-sec-{turn}"),))
        last = next(m for m in reversed(messages)
                   if m.get("role") == "tool" and m.get("name") == "search_flights")
        top = last["payload"]["top"][0]
        return ModelOutput(kind="final", provider=self.name,
            text=f"[secondary] Top option {top['flight_id']} at ${top['price']}. Booking deferred.")


class DeterministicFallback:
    """Graceful degradation: no further model/tool side effects."""
    name = "deterministic"

    def complete(self, messages: list[dict[str, Any]], turn: int) -> ModelOutput:
        return ModelOutput(kind="final", provider=self.name,
            text="[deterministic] Agent capacity degraded. Escalated to human queue.")


# ---------------------------------------------------------------------------
# Loop detection
# ---------------------------------------------------------------------------

@dataclass
class LoopDetector:
    max_identical_calls: int = 3
    _call_hashes: list[str] = field(default_factory=list, init=False)

    def _hash_call(self, tool_name: str, args: dict) -> str:
        payload = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def check(self, tool_name: str, args: dict) -> bool:
        """Return True if loop detected (same tool+args called too many times)."""
        h = self._hash_call(tool_name, args)
        self._call_hashes.append(h)
        return self._call_hashes.count(h) >= self.max_identical_calls


# ---------------------------------------------------------------------------
# Agent Runner: control-plane loop with max_turns, retries, breaker, fallbacks
# ---------------------------------------------------------------------------

@dataclass
class AgentRunner:
    providers: list[ModelProvider]
    tools: ToolRegistry
    breaker: CircuitBreaker
    retry: RetryPolicy
    loop_detector: LoopDetector = field(default_factory=LoopDetector)
    max_turns: int = 10
    session_budget_usd: float = 5.0
    sleep_fn: Callable[[float], None] = time.sleep

    def run(self, goal: str, correlation_id: str | None = None) -> dict[str, Any]:
        cid = correlation_id or str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        # PII redaction on goal before it enters any model context
        redacted_goal, pii_audit = redact_pii(goal)
        if pii_audit:
            LOG.info("pii_redacted", extra=log_extra(
                correlation_id=cid, run_id=run_id, pii_count=len(pii_audit)))

        messages: list[dict[str, Any]] = [{"role": "user", "content": redacted_goal}]
        degraded = False
        provider_used = ""
        total_cost = 0.0

        for turn in range(self.max_turns):
            # Budget check
            if total_cost >= self.session_budget_usd:
                return self._result(False, "budget_exhausted", turn, degraded,
                                   provider_used, cid, run_id, total_cost)

            output = self._call_with_fallback(messages, turn, cid, run_id)
            if output is None:
                return self._result(False, "all_providers_failed", turn, True,
                                   "none", cid, run_id, total_cost)
            provider_used = output.provider
            if output.provider != self.providers[0].name:
                degraded = True

            if output.kind == "final":
                LOG.info("run_complete", extra=log_extra(
                    correlation_id=cid, run_id=run_id, turn=turn,
                    provider=provider_used))
                return self._result(True, output.text, turn + 1, degraded,
                                   provider_used, cid, run_id, total_cost)

            # Execute tools, check for loops
            for call in output.tool_calls:
                if self.loop_detector.check(call.name, call.arguments):
                    LOG.warning("loop_detected", extra=log_extra(
                        correlation_id=cid, run_id=run_id, turn=turn,
                        tool=call.name))
                    return self._result(False,
                        f"Loop detected: {call.name} called {self.loop_detector.max_identical_calls}+ times with same args",
                        turn, True, provider_used, cid, run_id, total_cost)

                obs = self.tools.execute(call)
                messages.append({
                    "role": "tool", "name": obs.name, "call_id": obs.call_id,
                    "ok": obs.ok, "payload": obs.payload,
                })

        return self._result(False,
            "Stopped at max_turns; escalate to human.", self.max_turns,
            True, provider_used, cid, run_id, total_cost)

    def _call_with_fallback(self, messages, turn, cid, run_id) -> ModelOutput | None:
        for provider in self.providers:
            if not self.breaker.allow():
                continue
            for attempt in range(self.retry.max_attempts):
                try:
                    result = provider.complete(messages, turn)
                    self.breaker.record_success()
                    return result
                except AgentError as exc:
                    if exc.kind is FailureKind.PERMANENT:
                        return None
                    self.breaker.record_failure()
                    if attempt + 1 >= self.retry.max_attempts:
                        break
                    self.sleep_fn(self.retry.delay(attempt))
                except Exception:
                    self.breaker.record_failure()
                    if attempt + 1 >= self.retry.max_attempts:
                        break
                    self.sleep_fn(self.retry.delay(attempt))
        # All providers exhausted -- deterministic last resort
        det = next((p for p in self.providers if p.name == "deterministic"), None)
        return det.complete(messages, 0) if det else None

    @staticmethod
    def _result(ok, detail, turns, degraded, provider, cid, run_id, cost):
        return {"ok": ok, "detail": detail, "turns": turns, "degraded": degraded,
                "provider": provider, "correlation_id": cid, "run_id": run_id,
                "total_cost_usd": cost}


def demo() -> None:
    runner = AgentRunner(
        providers=[PrimaryFakeModel(fail_times=2), SecondaryFakeModel(),
                   DeterministicFallback()],
        tools=ToolRegistry(),
        breaker=CircuitBreaker(failure_threshold=5, recovery_timeout_sec=0.01),
        retry=RetryPolicy(max_attempts=4, base_delay_sec=0.01, max_delay_sec=0.05),
        max_turns=10,
        sleep_fn=lambda _: None,
    )
    result = runner.run(
        "Book the cheapest flight SFO->JFK on 2026-10-10 for john@example.com",
        correlation_id="corr-agent-demo-001",
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    demo()
```

Run: `python3 02-agent-runtime.py`. Expected path: PII (email) in the goal is redacted before model context; primary fails twice (transient 503), then succeeds through `search_flights` -> `book_flight` -> final PNR text. Raise `fail_times` or lower breaker threshold to exercise secondary (search-only degraded) or deterministic human-queue fallback. Repeat a tool call 3+ times to trigger loop detection.

---

## 6. Architectural System Design Scenarios

### Scenario 1: Consumer Flight-Booking Agent with Purchase HITL

**Problem statement**: A travel platform wants an agent that searches flights, filters by price/stops, emails itineraries, and books. Constraints: irreversible charges need human confirmation of flight/price/total; schema-validated tools only; context must not drop budget/airline prefs as tool dumps grow; target cost near ReWOO/plan-and-execute economics (~$18-$27 / 1k under Part 3 assumptions vs ~$90 / 1k naive ReAct); OpenAI-tier TPM must not collapse at ~30k tokens/full run on Tier 1.

**Architecture:**

```
 +-------------+  goal+prefs   +------------------+  phase ckpt   +-------------+
 | API Gateway |-------------->| Hybrid control   |-------------->| ReAct inner |
 | + corr IDs  |               | plan outer       |               | per phase   |
 +------+------+               +--------+---------+               +------+------+
        |                               |                                |
        |                      +--------v---------+                      |
        |                      | Postgres ckpt /  |                      |
        |                      | Temporal HITL    |<-- approve purchase -+
        |                      +--------+---------+                      |
        |                               |                                |
        v                               v                                v
 +--------------+              +------------------+               +--------------+
 | Telemetry    |              | Tool proxies     |               | External mem |
 | $ / RPM/TPM  |              | search/book/mail |               | full results |
 +--------------+              | schema + RBAC    |               | top-3 only   |
                               +------------------+               +--------------+
```

**Trade-off matrix:**

| Dimension | Alt 1: Pure ReAct | Alt 2: Hybrid plan + ReAct (recommended) | Alt 3: ReWOO / LLMCompiler DAG |
|-----------|------------------|----------------------------------------|-------------------------------|
| **Cost** | ~$90 / 1k; highest frontier calls/step | Balanced; fewer planner calls | ReWOO ~5x tokens; LLMCompiler up to 3.37-6.73x cost |
| **Latency** | Serial model+tool each step; worst p95 | Phase parallelism; better than ReAct | Best when many independent tools |
| **Ops complexity** | Lowest code | Medium (checkpoint/phase design) | Higher (DAG planner quality, joiner) |
| **Security** | Approvals bolt-on; easy to miss | HITL at purchase phase boundary | Static plan may skip re-verify |
| **Scalability** | Burns Tier 1 TPM fast (~30k tok/run) | Caching + smaller executors help | Parallel tools need concurrency caps |

**Decision rationale**: Recommend **Alt 2**: production booking needs mid-flight adapt (inventory changes) that pure ReWOO lacks, while pure ReAct is too expensive and context-bloated. Keep purchase behind code-enforced approval; use Temporal if approval can outlive the API worker. Alt 3 only when tool calls are highly parallel and plans are stable.

---

### Scenario 2: Multi-Agent Customer Support Platform for a Fintech Company

**Problem statement**: Design an AI agent system handling 5,000 customer support tickets/day. Requirements: sub-3s first response, handle account inquiries + transaction disputes + loan applications, PII-safe (SOC 2 + PCI-DSS), maximum $2,500/month model spend, 99.9% availability.

**Architecture:**

```
 +-------------------------------------------------------------------+
 |                         API GATEWAY                                |
 |  Auth (OAuth2), rate limit, correlation-id, budget enforcement     |
 +----------------------------+--------------------------------------+
                               |
                               v
 +-------------------------------------------------------------------+
 |                     PII REDACTION LAYER                            |
 |  Detect + redact SSN, account numbers, card numbers BEFORE LLM    |
 +----------------------------+--------------------------------------+
                               |
                               v
 +-------------------------------------------------------------------+
 |                    COMPLEXITY CLASSIFIER                           |
 |               (Haiku/Flash-Lite, <50ms, ~$0.001/ticket)           |
 |                                                                    |
 |  simple (60%)--> Account FAQ Agent (Haiku, cached system prompt)  |
 |  medium (30%)--> Transaction Agent (Sonnet, tool access)          |
 |  complex (10%)-> Loan/Dispute Agent (Opus, multi-step)            |
 +-------------------------------------------------------------------+
         |                      |                        |
         v                      v                        v
  FAQ Agent            Transaction Agent        Loan/Dispute Agent
  Haiku, 1 step        Sonnet, 2-3 steps        Opus, 5-8 steps
  RAG only             DB lookup tool            DB + external APIs
  $0.003/ticket        $0.02/ticket              $0.15/ticket
                                                        |
                                                        v
                                               HITL Escalation
                                               (disputes >$1000, fraud)
```

**Cost projection:**

```
Daily: 0.60 * 5000 * $0.003 + 0.30 * 5000 * $0.02 + 0.10 * 5000 * $0.15
     = $9 + $30 + $75 = $114/day
     + classifier: 5000 * $0.001 = $5
     = $119/day

With semantic cache (31% hit rate on FAQ): -$2.79/day
With prompt caching (90% on system prompts): ~-$15/day
With plan cache on loan flows (50% savings on 30% repeat patterns): -$11.25/day

Optimized: ~$90/day -> ~$2,400/month  -- WITHIN BUDGET
```

**Trade-off matrix:**

| Dimension | A. Single Opus for all | B. 3-Tier Routing (recommended) | C. Fine-tuned small model |
|-----------|----------------------|--------------------------------|--------------------------|
| **Cost/month** | $22,500 | ~$2,400 (89% reduction) | ~$600 |
| **Latency P95** | 3-5s (Opus TTFT) | <1s FAQ, <3s complex | <500ms all |
| **Ops burden** | Simplest | Medium: 3 models, cache, routing | Highest: training pipeline |
| **Security** | PII redaction before LLM | PII redaction + per-tier RBAC + audit | PII in training data risk |

**Decision**: **B** is the only option meeting $2,500/month budget while handling complex flows requiring frontier reasoning. A is 9x over budget. C requires months of training investment and cannot handle novel patterns without retraining.

---

### Scenario 3: Durable Multi-Agent Code Review Pipeline

**Problem statement**: Design an automated code review system processing 500 pull requests/day across 12 microservice repositories. Requirements: review completeness (security + correctness + style), resume after Kubernetes pod preemptions, structured output for CI integration, $300/day model budget, results within 10 minutes per PR.

**Architecture:**

```
 GitHub Webhook -> Redis Streams queue -> dedup by PR ID
                                |
                                v
              TEMPORAL WORKFLOW ORCHESTRATOR
              (durable execution: survives pod preemption)
                                |
              +-----------------+------------------+
              v                 v                  v
     Diff Analyzer      Security Scanner     Style Checker
     (Sonnet)           (Opus)               (Haiku)
     2 steps/file       3 steps/file         1 step/file
     $0.012/file        $0.025/file          $0.002/file
              +-----------------+------------------+
                                |
                                v
                     Supervisor / Reducer
                     (merge findings, dedup, Pydantic validate)
                                |
                                v
                     CI Output Formatter
                     (GitHub PR comments, SARIF for Security tab)
```

**Cost projection:**

```
Average PR: 15 changed files
Per PR:  15 * $0.012 + 15 * $0.025 + 15 * $0.002 + $0.01 = $0.595
Daily:   500 * $0.595 = $297.50  -- WITHIN BUDGET

With prompt caching (90% on analysis prompts): ~$208/day
```

**Why Temporal**: Pod preemptions at 5-15% are normal in shared Kubernetes clusters. A code review spanning 15 files with 3 parallel workers takes 3-8 minutes. Without durable execution, preemption at minute 6 discards all completed work. Temporal replays completed Activities from journal, resuming exactly where execution stopped. Temporal Cloud ~$25/month for this volume.

**Decision**: Multi-agent + Temporal meets budget at $208/day with headroom. The specialized worker pattern allocates model spend where reasoning depth matters most.

---

### Common Failure Modes

| Failure Mode | Detection | Mitigation |
|-------------|-----------|------------|
| Infinite retry loop (68 confirmed in 47 repos) | Step count + output similarity monitoring | Hard step ceiling + repeated call detection + exit conditions |
| Context window degradation (attention decay, spec drift) | Quality benchmarks at turn 10, 20, 30 | Summarize at fixed intervals; pin critical instructions |
| Silent 200 OK from tools (most damaging -- no error) | Empty-response detector on every tool result | Treat empty as failure, not success; require validation |
| Cascading retries (10x10 = 100 dead requests) | Upstream request count monitoring | Retry at ONE layer only; backoff + jitter + breaker |
| Factual drift across agents (hallucination propagation) | Cross-agent consistency checks | Validate outputs at each boundary before forwarding |
| Hallucinated tool params (31% of production failures) | Schema validation | Strict JSON schema + RBAC + registry (no unknown tools) |
| Prompt injection (indirect) | Injection classifier on tool outputs | Privilege separation + input sanitization + output review |
| Budget runaway ($47K incident, 11 days) | Per-session cost tracking + anomaly alerting | Three-layer budget caps; kill switch at 2x baseline |

---

### Key Takeaways for Interviews

- **An agent is defined by side effects, not intelligence.** Three properties (autonomy, proactiveness, action) separate agents from chatbots. The LLM is a processor, not a knowledge base.

- **ReAct is the dominant execution engine; hybrid is the recommended production pattern.** Pure ReAct is adaptive but compounds early mistakes. Plan-and-Execute is rigid but reviewable. Hybrid (structured plan with ReAct per phase + verification checkpoints) gives both.

- **Supervisor-worker is the production standard for multi-agent systems (57% adoption).** Hub-and-spoke for up to 6 workers, hierarchical above that. Skip the supervisor if you have <3 capabilities or <2s latency budget.

- **MCP and A2A are complementary, not competitive.** MCP = agent-to-tool (hands). A2A = agent-to-agent (coordination). The converging stack uses both.

- **Multi-agent reliability degrades multiplicatively.** Five 95%-reliable agents deliver 77% system success. Validate at every worker boundary -- the #1 bug is treating empty worker output as success.

- **Agents cost 5-30x more tokens than chatbots.** Budget governance requires three layers: session ceiling, per-user/feature quotas, organizational limits. Model routing is the highest-leverage optimization (100-300x cost spread across model tiers).

- **Caching + routing together delivers 70-85% cost reduction.** Four-layer cascade: semantic cache (31% overlap), plan cache (50.31% cost reduction, 96.6% quality), dynamic routing (85% cost at 95% quality), prompt caching (up to 90% on prefix).

- **Checkpointing and durable execution solve different problems.** LangGraph checkpointing handles app-level failures (bad reasoning, HITL pauses). Temporal handles infra-level failures (container crashes, network partitions). Production needs both.

- **Zero-trust for agents requires least agency, not just least privilege.** Static RBAC fails because agents are non-deterministic. Credential proxying (agent never sees real tokens), sandbox isolation, and enclave boundaries enforce what prompt-layer controls cannot.

- **The #1 reason agents fail in production is not model quality -- it is flawed enterprise integration.** ~95% of generative AI pilots stall due to integration issues (MIT/NANDA 2025).

---

### Interview Q&A

**Q1: What distinguishes an AI agent from a chatbot?**

An agent produces side effects -- it changes the world rather than just generating text. Three defining properties: autonomy (selects intermediate steps without a script), proactiveness (recognizes missing information and takes initiative), and action (executes operations through tool interfaces). The LLM inside the agent is a processor, not a knowledge base. Facts come from tools and memory systems.

**Q2: When would you use supervisor-worker vs. a single agent?**

Skip the supervisor when you have fewer than 3 distinct capabilities, no external tool calls, or a latency budget under 2 seconds. Use supervisor-worker when tasks decompose into parallel sub-problems with different tool requirements. Hub-and-spoke scales to ~6 workers; beyond that, hierarchical orchestration. The routing overhead is 620ms-1.8s per hop, so single-agent is always faster for simple tasks. 57% of organizations with multi-agent systems in production use this pattern.

**Q3: How do you prevent infinite loops in agentic systems?**

Three mechanisms: (1) hard step ceiling (e.g., recursion_limit=25), (2) no-progress detection -- hash every tool call (name + serialized args) and kill execution when the same hash appears N times, (3) wall-clock timeout. Detection should monitor step counts and output similarity, not just errors -- a looping agent may never throw an error while burning compute. Root cause: agents are given objectives but not exit conditions. IAL-Scan found 68 confirmed infinite loops across 47 production projects.

**Q4: Explain the difference between MCP and A2A.**

MCP (Model Context Protocol) is agent-to-tool -- it gives an agent its "hands" by standardizing how agents connect to external tools, data sources, and services. A2A (Agent2Agent Protocol) is agent-to-agent -- it enables agents across different platforms to discover, authenticate, and delegate tasks. They are complementary: MCP for tool access, A2A for inter-agent coordination. Key capability: MCP Tool Search Tool achieves 85% token reduction by discovering tools on-demand instead of loading all definitions upfront.

**Q5: How do you achieve 70-85% cost reduction on agent workloads?**

Four-layer cascade: (1) Semantic cache check -- return cached response for semantically equivalent queries (~31% hit rate, 100% savings per hit). (2) Plan cache -- reuse execution plan templates from similar solved tasks (50.31% cost reduction, 96.6% quality retention, NeurIPS 2025). (3) Dynamic model routing -- classify complexity, route to appropriate tier (100-300x cost differential; RouteLLM: 85% cost at 95% quality). (4) Prompt caching -- order prompts with static content first for up to 90% discount on repeated prefixes.

**Q6: Why do production deployments need both checkpointing and durable execution?**

They solve different failure classes. LangGraph checkpointing protects against application-level failures: bad reasoning, incorrect branches, HITL pauses. The developer owns retry/resume logic. Temporal protects against infrastructure-level failures: container crashes, network partitions, host preemptions. The runtime owns retry, resume, and side-effect deduplication. A Kubernetes pod preemption mid-workflow needs Temporal to resume. A bad agent decision needs a LangGraph checkpoint to rewind to.

**Q7: What does least agency mean and why does it matter?**

Least agency extends beyond least privilege. Least privilege asks what an agent may read; least agency asks what it may do. An agent that summarizes documents should not hold the ability to send mail, even if it may lawfully read the mailbox. This matters because agents are non-deterministic entities that select their own tools -- static RBAC alone cannot govern the gap between "can access" and "should act." Enforcement requires credential proxying (agent never sees real tokens), tool-level RBAC, and sandbox isolation.

**Q8: When is ReAct wrong on cost, and which multiplier would you cite?**

ReAct cost scales with steps x cumulative context -- O(N^2 * delta) tokens. For a standard benchmark comparison, cite HotpotQA: LLMCompiler is 3.37x cheaper. For parallel-heavy workloads, cite Movie Recommendation: 6.73x cheaper. For token efficiency, cite ReWOO: ~5x fewer tokens. Never universalize Movie Rec's 6.73x to non-parallel workloads. In practice, hybrid (plan outer + ReAct inner) is the recommended production pattern because it preserves mid-flight adaptation.

**Q9: How do you handle the state accumulation problem in multi-agent graphs?**

Every node receives the full state dictionary. With 20+ steps, thousands of accumulated tokens per node call. Cost grows O(k^2) in step count. Mitigations: (1) periodic summarization of older messages, (2) sliding window to last N entries, (3) externalize full results and keep only summaries in context, (4) restate critical constraints before irreversible steps. But summarization can break prompt-cache prefixes -- there is a cost-of-forgetting vs cost-of-remembering trade-off.

**Q10: What are the three error propagation patterns in multi-agent systems and how do you defend against them?**

(1) Factual drift -- one agent confidently states something incorrect, next agent treats it as ground truth. Defense: validate outputs at every worker boundary before forwarding. (2) Context window poisoning -- bad tool result appended to shared scratchpad contaminates every future call. Defense: schema validation on tool results + empty-response detection. (3) Cascading retries -- downstream agent detects problem, triggers self-correction, spawning more failing calls (10 agents x 10 retries = 100 dead requests). Defense: retry at ONE layer only, exponential backoff with jitter, shared circuit breaker state.

**Q11: Design Zero-Trust MCP for production.**

Four controls: (1) HTTPS only, no stdio for remote connections; (2) MCP allowlist/denylist per agent -- register only needed tool schemas; (3) OAuth Bearer tokens via vault credential proxy -- agent never sees real tokens, tokens never in URL query strings; (4) Treat MCP-retrieved content as data not instructions -- injection-screen tool outputs before they enter model context. Advanced: Anthropic Managed Agents splits into Brain (routing decisions only), Hands (disposable sandbox, no credentials), and Session (append-only event log outside both).

**Q12: Given Tier 1 30k TPM and a ~30k-token agent run, how do you size concurrency?**

Tier 1 TPM exhausted at ~1 concurrent full agent completion per minute if saturated. Solutions: (1) prompt caching to reduce per-request token volume, (2) plan-and-execute to use smaller models for execution steps, (3) semantic caching to bypass LLM entirely for ~31% of queries, (4) upgrade to higher tier ($1,000 cumulative for Tier 5: 10k RPM / 30M TPM). Back-pressure: honor Retry-After headers at runner level, batch when RPM-bound but TPM-available, cap tool concurrency to protect downstream APIs.

---

### Key Numbers to Memorize

```
 +---------------------------------------------------------------+
 | 57%  -- orgs with agents in production (2025)                  |
 | 3%   -- orgs successfully scaling agents (IDC/AWS)             |
 | 95%  -- pilots stalled by integration, not models              |
 | 5-30x -- token multiplier: agents vs chatbots                  |
 | 3-15% -- tool-calling failure rate in production               |
 | 31%  -- production failures from tool misuse                   |
 | 41-86% -- multi-agent system failure rate                      |
 | 0.95^5 = 77.4% -- 5 agents at 95% individual                  |
 | 68   -- confirmed infinite loops across 47 projects            |
 | 70-85% -- cost reduction from caching + routing                |
 | 90%  -- max prompt cache discount (Anthropic)                  |
 | 50.31% -- plan cache cost reduction (NeurIPS 2025)             |
 | 85%  -- RouteLLM cost reduction at 95% quality                 |
 | $47K -- infinite loop incident cost (11 days)                  |
 | 620ms-1.8s -- LangGraph routing latency per hop                |
 | 85%  -- token reduction from MCP Tool Search                   |
 | 150+ -- orgs using A2A in production (2026)                    |
 | 14.4% -- orgs with full agent fleet security approval          |
 | 97%  -- AI-breached orgs lacking access controls               |
 | $5B  -- Temporal valuation; 9.1T lifetime actions              |
 | 100-300x -- cost differential between model tiers              |
 +---------------------------------------------------------------+
```

### Quick Reference

```
Agent = side effects, not just text. Three properties: autonomy, proactiveness, action.
  LLM = processor, not knowledge base. Facts come from tools + memory.
  PEAS: Performance, Environment, Actuators, Sensors.

Execution engines:
  ReAct     -- adaptive, O(N^2) cost, early mistakes compound
  Plan+Exec -- rigid, fewer LLM calls, reviewable plan
  Hybrid    -- plan outer + ReAct inner (recommended in production)
  ReWOO     -- ~5x token efficiency, no mid-flight adapt
  LLMCompiler -- DAG parallel, up to 3.37-6.73x cost reduction

Multi-agent:
  Supervisor-worker (57% adoption); hub-spoke <=6, hierarchical 6+
  Reliability: 0.95^5 = 77.4%. Validate at every boundary.
  MCP = agent-to-tool; A2A = agent-to-agent. Complementary.

Cost optimization:
  Semantic cache (31%) -> Plan cache (50%) -> Model routing (85%) -> Prompt cache (90%)
  Combined: 70-85% reduction. Budget: session + user + org layers.

Resilience:
  Checkpointing (app failures) + Temporal (infra failures) = both needed
  Circuit breaker per dependency; fallback chain: primary -> secondary -> deterministic
  Loop detection: hash tool+args, kill at N repeats

Security:
  Least agency > least privilege. Credential proxy (agent never sees tokens).
  Managed Agents: brain / hands / session (mutually untrusted).
  MCP: HTTPS only, allowlist, no query-string tokens.
```
