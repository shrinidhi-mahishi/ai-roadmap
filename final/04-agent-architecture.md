# Module 04: Agent Architecture

## What Is This?

An **agent** is a program that uses an LLM in a loop to accomplish a task. Instead of one question → one answer, the agent:

1. **Thinks** about what to do next (the LLM reasons about the task)
2. **Acts** by calling a tool (search the web, run code, query a database)
3. **Observes** the result
4. **Repeats** until the task is done or it gives up

This loop is called **ReAct** (Reason + Act). A simple example: "Find the cheapest flight from NYC to London next Friday." The agent might (1) search a flight API, (2) notice it needs to check multiple airlines, (3) search each one, (4) compare prices, (5) return the best option. No single LLM call could do this — it requires multiple steps with tool calls in between.

**Workflows vs. Agents**: A workflow is a predefined sequence of steps (like a flowchart) — the developer decides the path in advance. An agent is dynamic — the LLM decides what to do at each step based on what it observes. Workflows are more predictable and easier to debug; agents are more flexible and handle unexpected situations better.

**State** is what the agent remembers between steps — the conversation so far, tool results, intermediate calculations. This state needs to be persisted (saved to disk/database) so the agent can recover from crashes and resume where it left off.

## Why It Matters

Agents are the bridge between "LLM as a chatbot" and "LLM as a worker that accomplishes real tasks." Understanding the architecture — the loop, the state, the stop conditions — is essential for building reliable AI applications that go beyond simple Q&A.

---

## 2. Core Concepts

The architecture you choose -- ReAct, Plan-and-Execute, DAG, multi-agent supervisor -- determines your cost, latency, reliability, and failure modes. In 2026, the gap between a working demo and a production agent is almost entirely an architecture problem: 88% of agent failures trace to infrastructure gaps, not model quality (Arize, 2026). Getting the architecture right is the difference between a $0.10 successful run and a $1.00 runaway loop that quietly destroys your business case.

### The Two Planes

Every agent system, regardless of framework, has two planes:

- **Control plane**: Owns the loop itself -- which model to call, which tools are legal this turn, when to stop, where state is saved, and how events stream to the client. Think of it as the conductor of an orchestra.
- **Data plane**: Everything with side effects -- tool adapters, MCP servers, sandboxes, knowledge bases, other agents. Think of it as the musicians playing their instruments.

**Critical invariant across all frameworks**: The model never executes tools or handoffs directly. It emits a structured action; the runtime dispatches it; an observation is injected back; the loop continues. The LLM is the brain, not the hands.

| Plane | Owns | Failure if conflated |
| --- | --- | --- |
| Control | Loop budget, routing, checkpoint key, RBAC, stream mux | Infinite spend; lost resume; cross-tenant state |
| Data | Tool HTTP, MCP `tools/call`, A2A tasks, sandbox FS | Side effects on hallucinated args; duplicate charges |

### Workflows vs. Agents

Anthropic's 2024 distinction (still holding in 2026): **workflows** are LLMs and tools on predefined code paths; **agents** are systems where the LLM dynamically directs the process and tool use. Production stacks almost always mix both: a deterministic outer graph (workflow) wrapping ReAct inner loops (agent).

### The Agent Loop

At its simplest, an agent is a while-loop:

```
while not done:
    thought = llm.reason(context)       # Think about what to do
    action = llm.select_tool(thought)   # Pick a tool
    observation = execute(action)       # Run the tool
    context.append(observation)         # Add result to memory
    if llm.is_done(context):            # Check if task complete
        done = True
```

Each iteration requires a full LLM inference pass, consuming tokens proportional to accumulated history. This is why agent cost grows superlinearly with turns.

#### Architecture Diagram: The Agent Loop

```
┌──────────────────────────────────────────────────────────────┐
│                    Canonical Agent Loop                       │
│                                                              │
│         ┌──────────┐    ┌──────────┐    ┌──────────────┐     │
│    ┌───►│  THINK   │───►│   ACT    │───►│   OBSERVE    │──┐  │
│    │    │ (reason  │    │ (select  │    │ (read tool   │  │  │
│    │    │  about   │    │  & call  │    │  result)     │  │  │
│    │    │  task)   │    │  tool)   │    │              │  │  │
│    │    └──────────┘    └────┬─────┘    └──────────────┘  │  │
│    │                        │                             │  │
│    │                        ▼                             │  │
│    │               ┌────────────────┐                     │  │
│    │               │  Environment   │                     │  │
│    │               │ (tools, APIs,  │                     │  │
│    │               │  sandboxes)    │                     │  │
│    │               └────────────────┘                     │  │
│    │                                                      │  │
│    └──────── loop until done or max_turns ────────────────┘  │
│                          │  ▲                                │
│                     read │  │ update                         │
│                          ▼  │                                │
│            ┌────────────────────────────┐                    │
│            │      State / Memory        │                    │
│            │  - Conversation history    │                    │
│            │  - Tool results            │                    │
│            │  - Plan & checkpoints      │                    │
│            │  - Long-term memory store  │                    │
│            └────────────────────────────┘                    │
└──────────────────────────────────────────────────────────────┘
```

### State: The Four Types

1. **Conversation state**: Accumulated message history (user/assistant/tool turns). Grows linearly; primary driver of context window exhaustion.
2. **Tool state**: Results from tool executions, intermediate data. Must be persisted for replay and recovery.
3. **Planning state**: Current plan, completed steps, pending steps, replanning history. Primary artifact in Plan-and-Execute architectures.
4. **Memory state**: Short-term (checkpoints within a run) and long-term (cross-run knowledge stores). LangGraph separates these: checkpointers for short-term, stores for long-term.

---

## 3. How It Works

### 3.1 ReAct (Reason + Act) -- The Canonical Pattern

Introduced by Yao et al. (ICLR 2023, Princeton/Google). ReAct augments the action space to include language **thoughts** that do not touch the environment alongside domain **actions** that do. The trajectory interleaves Thought, Action, Observation:

```
Thought: I need to find the population of France.
Action: search["population of France"]
Observation: France has a population of approximately 68 million.
Thought: I now have the answer.
Action: finish["68 million"]
```

**Key insight**: Thoughts serve five purposes: decompose goals, extract from observations, inject commonsense, reformulate search queries, and synthesize answers. For QA tasks, thoughts are dense (every step); for web/game tasks, they can be sparse (model decides when to think).

**Paper benchmarks (PaLM-540B):**

| Method | HotpotQA EM | FEVER Acc |
| --- | --- | --- |
| Standard | 28.7 | 57.1 |
| CoT (Chain-of-Thought) | 29.4 | 56.3 |
| CoT-SC (21 samples, T=0.7) | 33.4 | 60.4 |
| Act-only (no thoughts) | 25.7 | 58.9 |
| ReAct | 27.4 | 60.9 |
| ReAct -> CoT-SC backoff | **35.1** | 62.0 |
| Supervised SoTA (at that time) | 67.5 | 89.5 |

On ALFWorld/WebShop: 1-2-shot ReAct beat IL/RL trained on 10^3-10^5 instances by +34 and +10 percentage points.

**When ReAct fails (human labels, 200 HotpotQA trajectories):**

| Failure Mode | ReAct | CoT |
| --- | --- | --- |
| Success: true positive | 94% | 86% |
| Success: false positive (hallucinated facts) | **6%** | **14%** |
| Failure: reasoning error (incl. repetitive TAO loops) | **47%** | 16% |
| Failure: empty/useless search | **23%** | n/a |
| Failure: hallucination | **0%** | **56%** |

**The key trade-off**: Grounding via tools kills hallucination (0% vs CoT's 56%), but the same interleaving reduces reasoning flexibility and creates a signature failure -- the model gets stuck in repetitive thought-action loops (47% of failures). Production implication: ReAct needs an external loop breaker; the model will not reliably stop itself.

Step caps from the paper: 7 steps for HotpotQA, 5 for FEVER; extra steps recovered only 0.84%/1.33% of already-correct trajectories.

**Recent advances**:
- **RP-ReAct** (Molinari et al., Dec 2025): Decouples strategic planning from low-level execution. A Reasoner-Planner Agent decomposes goals into sub-questions while Proxy Execution Agents handle standard ReAct loops per sub-task.
- **Focused ReAct**: Adds reiteration of the original question at each step and early-stops on repetitive actions, yielding up to 530% relative accuracy gains.

### 3.2 Variants in the Same Family

| Variant | Control Topology | What Changes vs ReAct | Key Numbers |
| --- | --- | --- | --- |
| **Act-only** | Action->Obs | No thoughts | Worse: 25.7 vs 27.4 EM on HotpotQA |
| **ReAct -> CoT-SC** | Sequential backoff | Use external KB or internal majority vote | Best HotpotQA 35.1 EM |
| **Plan-and-Solve (PS+)** | Plan then execute in one generation | Zero-shot "devise a plan then carry out"; PS+ adds variable extraction | PS+: MultiArith 91.8, GSM8K 59.3 (+2.9 pp) |
| **Plan-and-Execute** | Planner LLM + executor ReAct | Plan is data; executor walks steps; replanner optional | 85% tokens through cheaper executor |
| **ReWOO** | Planner -> Worker(s) -> Solver | Thoughts decoupled from observations; blueprint then tool burst | **5x** token efficiency, +4 pp HotpotQA |
| **LLMCompiler** | Streamed DAG + task-fetch + joiner | Parallel function DAG; args can reference prior tasks | **3.7x** latency, **6.7x** cost vs ReAct |
| **Tree of Thoughts** | BFS/DFS over thought nodes | Lookahead + backtrack; not tool-centric | Game of 24: GPT-4 CoT 4% vs ToT 74% |
| **LATS** | MCTS over ReAct steps | LM as actor, value, reflection | HumanEval GPT-4 pass@1 92.7% |
| **Reflexion** | Actor + Evaluator + Self-Reflection + episodic buffer | Verbal RL across trials, not within one trajectory | HumanEval pass@1 91% vs GPT-4 80% |
| **HuggingGPT** | Plan -> select HF models -> execute -> summarize | LLM as controller over modality-specific models | NeurIPS 2023 four-stage pipeline |

**Production mapping**: ReAct = LangGraph `bind_tools` + `ToolNode` + `tools_condition`, or OpenAI Agents SDK `Runner` loop. Plan-and-Execute = Anthropic orchestrator-workers. Evaluator-optimizer = ADK `LoopAgent` + escalate.

### 3.3 Plan-and-Execute (Deep Dive)

Separates plan generation (Planner) from step-by-step execution (Executor). The planner creates a full plan upfront; each step is executed by a potentially cheaper/smaller model.

**Advantages over ReAct**:
1. Faster multi-step execution -- the large model only plans, not acts per step
2. Cost savings -- route 85% of tokens through smaller executor models
3. Better task completion -- forces explicit reasoning about all required steps upfront

**Best for**: Tasks with 5+ interdependent steps in stable environments (financial analysis, data pipelines, report generation).

**Weakness**: Less adaptive to unexpected outcomes without a replanning mechanism. A 2025 preprint argues separating strategic planning from tactical execution improves integrity when combined with least privilege, task-scoped tools, sandboxing, and replanning.

**CLEAR Framework data**: Plan-Execute costs $1.24/task vs. $5.12 for Reflexion -- same accuracy, 4.4x lower cost.

### 3.4 Reflexion (Deep Dive)

A single-agent pattern using self-reflection through linguistic feedback. An LLM evaluator provides specific feedback to the agent, improving success rates and reducing hallucination compared to CoT and ReAct.

**Limitation (2025 replication study)**: Single-agent Reflexion consistently repeats earlier misconceptions because the same model generates both output and critique, reinforcing its own blind spots. ICLR 2024 (Huang et al.): LLMs cannot reliably self-correct reasoning using only intrinsic capabilities -- the evaluator shares the generator's blind spots.

**Trade-off**: Adds ~30% latency; typically improves quality 10-30% on failure-mode subsets. Best layered on top of ReAct or Plan-and-Execute when output quality matters more than speed.

**Emerging solutions**: PreFlect (prospective reflection) outperforms classic Reflexion by 10-15% with 15-20% additional token overhead. GSAR framework (2026) extends hallucination detection to multi-agent settings. Post-hoc critics in multi-agent settings yield +8-25 percentage points on complex reasoning.

### 3.5 LATS (Language Agent Tree Search)

Combines reflection/evaluation with Monte-Carlo tree search to explore multiple reasoning paths simultaneously. Evaluates different action sequences, discards unpromising ones, pursues the most likely path.

**In practice**: Full LATS is rarely deployed in production due to cost (many LLM calls per puzzle -- thought samples x tree depth x value). Most teams use a lighter variant: generate 2-3 candidate plans, evaluate them, pick the best one without deep tree search. Treat as research spend, not a chat SKU.

### 3.6 Five Distinct Clocks (Loop Types)

Do not collapse these. They have different meters, stop conditions, and cost functions.

**A. Max-iteration / recursion (control-plane fuse):**
- OpenAI Agents SDK: a "turn" = one model invocation including tool calls. Default `max_turns` = 10; `MaxTurnsExceeded` error. Pass `None` to disable (dangerous in prod).
- LangGraph: `recursion_limit` default 25 supersteps; `GraphRecursionError`. One ReAct tool cycle is typically 2 supersteps (model node + tool node) = ~12 tool rounds before the fuse.
- ADK `LoopAgent`: sequential sub-agents until `max_iterations` or any sub-agent emits `escalate=True`. Docs example: `max_iterations=5`.
- CrewAI hierarchical: workers should set `allow_delegation=False` or manager/worker ping-pong is unbounded.

**B. Tool loops (data-plane inner ReAct):** Same tool with same args, or pagination-by-LLM (`page=1` forever). Adapter must: cap `limit`, return `is_error` on 4xx (except 429), refuse POST without idempotency key, and treat identical `(tool, canonical_args)` N times as a circuit breaker.

**C. Human-in-the-loop (HITL):** Pause without burning a GPU/worker.
- LangGraph: `interrupt(value)` requires a checkpointer; resume with `Command(resume=...)`.
- OpenAI Agents SDK: first-class HITL plus `AbortSignal`.
- Temporal TS integration: waits on Signal/Update then Continue-As-New.
- ADK Go 2.0: HITL is a built-in primitive; interrupt format shared with Python.
- Inngest AgentKit: `step.waitForEvent` -- zero compute while paused.

**D. Event loops (runtime, not ReAct):** ADK Runner is an ask-yield event loop: user message + session id -> internal events -> streamed events. Inngest functions are event-triggered; Temporal workflows are event-sourced. These loops outlive a single LLM call.

**E. Streaming loops:** Control plane must mux tokens and tool/interrupt events without executing on partial JSON. LangGraph has 7+ stream modes; OpenAI has `run_streamed`; ADK supports native multimodal streaming.

### 3.7 Agent Loop Architectures

**Single-agent loops**: The standard ReAct-style while-loop. The OpenAI Agents SDK Runner exemplifies this: `Runner.run()` enters a while-loop that repeatedly calls the active agent's model, executes tool calls, processes handoffs, and checks guardrails until final output or `max_turns`.

**Router-based branching**: A central LLM routes incoming requests to specialized agents based on intent classification. Common in customer service architectures.

**DAG workflows**: Tasks and dependencies modeled as directed acyclic graphs enabling parallel execution of independent steps. The LLMCompiler streams a DAG of tasks with dependencies; a Task Fetching Unit schedules execution once dependencies are met, claiming 3.6x speedup through parallelism.

**Cyclic graphs**: LangGraph's key differentiator. A ReAct loop is not a DAG -- it needs cycles. While LCEL targets acyclic pipelines, LangGraph handles workflows needing cycles, explicit state management, conditional branching, and multi-agent coordination.

### 3.8 Topology Choice

Anthropic names five workflow patterns plus agents:
1. Prompt chaining (fixed sequence + optional gates)
2. Routing (classifier -> specialist)
3. Parallelization: sectioning (independent subtasks) or voting (same task N ways)
4. Orchestrator-workers (LLM decides subtasks at runtime)
5. Evaluator-optimizer (generate/critique loop)

| Topology | Cycles? | Who Picks Next Hop | Typical Runtime |
| --- | --- | --- | --- |
| DAG (Prefect, Airflow, static LangGraph) | No | Engineer | ETL, fixed RAG pipelines |
| Cyclic ReAct graph | Yes | Model + `tools_condition` | Tool-using assistants |
| Supervisor / hierarchical | Yes at manager; workers often DAGs or ReAct | Manager LLM | CrewAI hierarchical, Bedrock |
| Orchestrator-workers | Fan-out DAG per plan, then join | Orchestrator | Anthropic coding/search |
| Plan-and-execute | Outer DAG of steps; inner ReAct optional | Planner then executor | LangChain planning agents |

### 3.9 State Management In Depth

**LangGraph StateGraph model**: The core primitive. StateGraph is the controller/blueprint defining nodes, edges, start/end points, and loop/branch conditions. State is a typed dictionary (TypedDict or Pydantic model) flowing through the graph. State is incrementally updated via reducers (not overwritten), enabling parallel execution where multiple nodes modify different fields simultaneously. Before execution, the graph undergoes compilation that validates connections, identifies cycles, and optimizes paths.

Key details:
- Channels default to **LastValue** (overwrite). Use `Annotated[list, operator.add]` for merge semantics.
- Concurrent writes to a key without a reducer -> `InvalidUpdateError`.
- `Send(node, state)` enables dynamic fan-out with per-child state (map-reduce).
- `Command` combines a state update with a hop (`goto`) so a node can both write and redirect.

**LangGraph checkpoint grain**: Full `StateSnapshot` at each super-step; task-level writes as nodes finish so a sibling failure does not re-run successful parallel nodes. Time travel resumes at super-step boundaries, not mid-node. `update_state` creates a new checkpoint; reducers still apply.

**Durability modes** (`sync` | `async` | `exit`):
- `sync`: persist before next step (slowest, safest)
- `async` (default): persist while next step runs
- `exit`: persist only when graph exits (less duplication; lose mid-run on pod kill)

**OpenAI Agents SDK Runner loop**: Three entry points -- `Runner.run()` (async), `Runner.run_sync()` (sync), and streaming. Loop termination: if model produces output matching `agent.output_type` with no tool calls, the loop ends. Input guardrails run before model calls; output guardrails run after but before finalization. "Tripwire" guardrails halt execution immediately. State serialized via `RunResult.to_state()` for resume-from-checkpoint.

**Google ADK**: Hybrid architecture combining deterministic workflow agents (sequential, parallel, loop) with LLM-routed dynamic delegation. ADK 2.x introduced graph-based workflow runtime with task API for agent-to-agent delegation. Model-agnostic: Gemini-optimized but supports Anthropic, Meta, Mistral via LiteLLM. Session state uses key prefixes:

| Prefix | Scope | Example |
| --- | --- | --- |
| (none) | this session | `current_intent` |
| `user:` | all sessions for this user | `user:preferred_language` |
| `app:` | all users of the app | `app:api_endpoint` |
| `temp:` | this invocation only | `temp:raw_api_response` |

### 3.10 Agent-to-Agent vs Tool Dispatch

Two complementary protocols:
- **MCP** (Model Context Protocol) = agent -> tools/resources (JSON-RPC `tools/list`, `tools/call`). "USB-C for AI applications."
- **A2A** (Agent-to-Agent Protocol) = agent -> agent: Agent Card discovery, task lifecycle, messages, artifacts, streaming, push notifications. Spec 1.0.0; Linux Foundation; Apache-2.0.

A2A is not an agent framework and not a replacement for MCP. Use A2A when the peer is a different trust domain/vendor/language. Use in-process dispatch (LangGraph `ToolNode`, Agents SDK `FunctionTool`, ADK `FunctionTool`) when agents are co-located.

---

## 4. Key Patterns & Best Practices

### Multi-Agent Orchestration Patterns

Six production-proven patterns:

1. **Supervisor/Worker**: A supervisor agent decomposes tasks, dispatches to workers, synthesizes results. 2026 production default across frameworks.
2. **Sequential Pipeline**: Agents execute in order, each processing and passing state to the next.
3. **Parallel Fan-Out/Fan-In**: Multiple agents run concurrently on independent sub-tasks, results merged. Cuts wall-clock time ~75%. Needs a reducer function and partial-failure handling.
4. **Router**: Central agent classifies intent and routes to specialized agents.
5. **Hierarchical Delegation**: Multi-level supervisor hierarchy for complex organizations.
6. **Evaluator-Optimizer Loop**: Producer generates, critic evaluates, loop until quality threshold or escalation.

**Composition**: Patterns are composable. Common architecture: fan-out research agents feeding supervisor for quality-gating, HITL checkpoint before external actions, consensus round for highest-stakes decisions.

**Anthropic's own system**: Orchestrator-worker pattern. Lead agent coordinates; specialized subagents operate in parallel with their own context windows. Subagents provide compression (parallel exploration) and separation of concerns (distinct tools, prompts, trajectories). Subagents return condensed summaries (1,000-2,000 tokens) to the parent agent.

**When multi-agent wins**: (1) Parallelizable read-heavy work with independent sub-problems (fan-out research, log triage, multi-source enrichment) -- AORCHESTRA reports +16.28% over strongest baseline. (2) Narrow-domain reliability tasks (100% actionable rate vs 1.7% single-agent in incident response). For sequential tasks or shared-state scenarios, single agent recommended.

### Architecture Selection Heuristic

Start with the simplest effective pattern and escalate:
1. Start with ReAct (simplest)
2. Move to Plan-and-Execute when tasks have 5+ interdependent steps
3. Add Reflexion when output quality matters more than speed
4. Use LATS only for problems with large solution spaces (research budget)
5. Go multi-agent when no single agent has all required expertise
6. Pick based on the failure mode you can tolerate: wasted tokens (ReAct), rigidity (Plan-and-Execute), latency (Reflexion)

### OpenAI's Two Multi-Agent Contracts

| Pattern | Ownership of User-Facing Reply | Mechanism |
| --- | --- | --- |
| **Handoffs** | Specialist takes over | Control moves to specialist; `AgentUpdatedStreamEvent` |
| **Agents as tools** | Manager keeps the reply | Nested run, bounded |

Official guidance: split agents only when instructions, tools, or policy actually change -- extra agents multiply prompts, traces, and approval surfaces.

### Cost Optimization Strategies

1. **Plan caching**: NeurIPS 2025 paper showed 50.31% cost reduction while maintaining 96.61% of baseline performance, plus 27.28% latency reduction.
2. **Model routing**: Cheap small model for easy 70% of queries, frontier model for hard 30%. 40-70% cost reduction with no measurable quality loss.
3. **Prompt caching**: When agent system prompts and tool definitions repeat across runs, prompt caching cuts prompt tokens 50-90%. All major providers support it natively in 2026.
4. **Hybrid model pairing**: DeepSeek R1 (reasoning/planning) + Claude Sonnet (code editing) hit SOTA on Aider's polyglot benchmark at 14x less cost than OpenAI o1 alone.
5. **Cache hygiene for agents**: Stable byte-prefix ordering: tools -> system -> few-shot -> then mutating messages. Mutating the prefix (tool list shuffle, timestamp in system prompt) kills cache hit rate.

### Progressive Deployment

1. Define use cases
2. Establish clean data foundations
3. Design modular core components
4. Select models and tool integrations
5. Establish security guardrails
6. Validate in sandbox
7. Expand from human-supervised pilots to full-scale automation

Orchestration sophistication should follow workload complexity. Teams deploying swarm-style systems on tasks a three-subagent supervisor could handle are spending engineering budget that infrastructure does not require. Measure task shape first, match pattern second, choose framework third.

---

## 5. System Design Considerations

### Token Economics

**ReAct agents**: 2,000-3,000 tokens per simple task (3-5 API calls, $0.06-0.09/task). Complex scenarios: each loop iteration adds to context window; typical completion in 3-7 loops consuming 10,000-25,000 total tokens. Agents consume 4x the tokens of a chat interaction; multi-agent systems ~15x.

**Cost is quadratic-ish in turns** unless the stable prefix caches. Example with gpt-5.6-terra, 8k-token frozen prefix, 400 output tokens/turn, 600 new input tokens/turn:

| Turns | Approx $/run | $/1k runs |
| --- | --- | --- |
| 1 (no tools) | $0.021 | ~$21 |
| 3 (2 tools) | $0.036 | ~$36 |
| 10 (SDK default cap) | $0.087 | ~$87 |
| 25 (LangGraph default fuse) | $0.203 | ~$203 |

If the prefix mutates (cache miss), the 10-turn row jumps to ~$0.22/run ($220/1k) because all input is billed at full price.

**Plan-and-Execute agents**: Front-loads a larger output (plan: 1,000-2,000 tokens) but fewer API calls overall. GPT-4 for planning (15% of tokens), cheaper model for execution (85% of tokens).

**Enterprise scale**: Agentic workflows consume 5-30x more tokens per task than standard chatbot queries. Enterprise AI inference represents 85% of total AI budgets.

### Benchmark Results (2026)

| Benchmark | Top Result | Note |
| --- | --- | --- |
| SWE-bench Verified | Claude Opus 4.7 (87.6%) | Baseline in 2023: 1.96%. Gap between top 3 compressed to <5 pp (saturation signal) |
| WebArena | Claude Mythos Preview (68.7%) | Human baseline: ~78%. Best hybrid approaches outperform pure-pixel agents |
| GAIA | Claude Sonnet 4.5 (74.6%) | Anthropic sweeps top 6 HAL spots. Agentic-search specialist leads at 92.36% |
| TAU-bench | Claude 3.5 Sonnet: 69.2% retail / 46.0% airline | pass^k reveals reliability decay; ICML 2026: recent gains yield only small reliability improvements |

**Benchmark caveats**: (1) 0 of 15 major benchmarks integrate cost-efficiency into scoring. (2) Same model posts different numbers under different harnesses (scaffold dependency). (3) UC Berkeley RDI (April 2026): automated scanning agent broke all 8 major benchmarks by reward hacking, achieving near-perfect scores without solving a single task.

### Latency Profiles

- **ReAct**: Sequential processing accumulates latency over many steps. `T_loop = sum(TTFT_i + T_decode_i + T_tool_i)`. p99 dominated by slowest tool + longest decode.
- **Plan-and-Execute**: Higher upfront latency (plan generation) but fewer total round-trips.
- **DAG/Graph**: Lowest wall-clock time for parallelizable workloads. LLMCompiler claims 3.7x wall-clock improvement.
- **Fan-out**: p99 = max(worker p99) + join LLM. Voting x N multiplies cost linearly, latency stays ~one call if parallel.
- **HITL**: p99 is the human SLA (hours/days), not the model.

### Distributed Resilience & State

**LangGraph checkpointing**: State saved after every node transition (super-step), keyed by thread ID. A simple two-node graph creates four checkpoints. Checkpointer options: MemorySaver (dev), SqliteSaver (single-server), PostgresSaver (production). AWS integration: DynamoDBSaver stores lightweight metadata, S3 for large payloads (>350KB). Postgres write ~5-15 ms, ~3-8 ms with asyncpg pool.

**The checkpointing gap**: Checkpointing alone is not full durable execution. LangGraph saves state but provides no automatic failure detection -- no supervisor, no watchdog, no heartbeat. If the process crashes, the workflow is dead until something external notices. LangGraph protects against application-level failures (bad reasoning, incorrect branches, HITL pauses). Temporal protects against infrastructure-level failures (container crashes, network partitions). Production often needs both.

**Sharp edges on resume**: On resume, later graph work can re-execute. Nondeterministic operations need idempotency. The node boundary must be engineered as a replay boundary. LangChain's 2026 State of Agent Engineering report: 60% of production incidents trace to state management.

### Durable Execution for Long-Running Agents

**The operational wall**: Agent frameworks solved the planning loop by 2025. The remaining challenge is operational: agent dies mid-run, approval lands a day late, upstream API rate-limits, partial side-effects leave audit log inconsistent.

**Temporal**: Workflow (deterministic orchestration blueprint) + Activities (non-deterministic work: LLM calls, tool invocations). Key capabilities: automatic retry on activity failure, state held over long periods (even years), human-in-the-loop via signal/query, self-healing with automatic retries. OpenAI uses Temporal for Codex in production. Official OpenAI Agents SDK integration reached GA on March 23, 2026.

**Temporal limits**: Warn at 10,240 events / 10 MB; terminate at 51,200 events / 50 MB; also 2,000 Updates / 10,000 Signals. **Continue-As-New** passes latest state into a new RunId with empty history. Do not put full tool payloads in Activity return values. LLM tokens belong in Activity results so replay does not re-call the API.

**Other durable execution platforms**: Restate (event-driven, Rust-based), Inngest (serverless step functions), DBOS (database-oriented), Prefect 3.

**Composition pattern**: LangGraph (cognition) inside Temporal/Inngest/Prefect (durability).

### Event Sourcing for Agent Trajectories

Every state transition, tool call, and observation recorded as an immutable event in an append-only log (Kafka, Redis Streams, NATS). On crash, a standby worker reads the event stream, replays every event to reconstruct exact graph state. Provides natural idempotency.

**Key distinction**: Event streaming (Kafka) moves data between systems (designed for throughput). Event stores (Axon Server) capture decisions with full causal context (designed for auditability and replay).

### Enterprise Security & Governance

**Prompt injection**: Remains #1 on OWASP LLM Top 10 in 2026 -- unsolved structural problem. Core issue: LLMs treat system prompt, user request, and retrieved text as a single token stream with no reliable command/data boundary. Documented injection attempts rose ~340% YoY in late 2025. Indirect attacks (instructions hidden in email/document/web page) now >55% of incidents. Average agent-related breach costs ~$4.7M.

**Multi-hop attacks in multi-agent systems**: Injection in one data source can propagate through agent chains. CVE-2026-22708 (Cursor): attacker poisons execution environment so allowlisted commands deliver arbitrary payloads.

**Least privilege as primary control**: Because no fully reliable injection defense exists, assume injection succeeds; the durable mitigation is ensuring a compromised agent cannot perform high-impact actions. Zero Trust for agents: all actions explicitly allowed.

**Sandboxing (2026)**: Three dominant: Firecracker microVMs (strongest, regulated data), gVisor (syscall-level, compute-heavy multi-tenant), V8 Isolates (JS-only, latency-critical). Standard containers are NOT acceptable isolation for agentic workloads.

**Audit trails**: EU AI Act Article 12 requires high-risk AI systems to enable automatic recording of events: structured complete records, tamper-evident, retained at least 6 months (24 months for biometric/law enforcement), exportable for regulators. Full high-risk mandates enforceable August 2, 2026. Penalties: up to 35M EUR or 7% of worldwide annual turnover.

**Agent identity**: Each agent in a multi-agent pipeline needs its own identity, scope constraints, and audit trail segment. Non-human identities already outnumber human identities in most enterprises.

**Defensive architecture consensus (2026)**: Containment, not cure. Six control layers: identity, least-privilege access, runtime enforcement, behavioral monitoring, audit logging, supply chain security. Defense-in-depth assumes any single layer can fail.

### Scaling Agent Workloads

**Infrastructure requirements**: Only 1.6% of Claude Code's codebase is AI decision logic; 98.4% is operational infrastructure.

**Capacity sketch** (1k concurrent agents):
- Checkpoint IOPS: 1k agents x 2 supersteps/turn x 4 turns/min = ~8k writes/min. PostgresSaver handles this; add TTL.
- TPM: 1k agents x 8k prefix/turn x 4 turns/min = 32M TPM if uncached -- over T5 limits. Cache is a capacity feature: 90% hit drops uncached to ~3.2M.
- Fan-out cap: Hard-code `max_workers=8`; do not let the LLM pick 200.

---

## 6. Code Examples

### ReAct Loop (Conceptual Python)

```python
# Minimal ReAct loop -- the pattern every framework implements
def react_loop(llm, tools, query, max_turns=10):
    messages = [{"role": "user", "content": query}]
    
    for turn in range(max_turns):
        # Reason: LLM generates thought + action
        response = llm.chat(messages, tools=tools)
        messages.append(response)
        
        if response.tool_calls:
            # Act: Execute the selected tool
            for call in response.tool_calls:
                result = execute_tool(call.name, call.args)
                # Observe: Feed result back
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result
                })
        else:
            # No tool calls = final answer
            return response.content
    
    raise MaxTurnsExceeded(f"Hit {max_turns} turns without answer")
```

### LangGraph StateGraph with Typed State

```python
from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated
import operator

# Define typed state with reducer for messages
class AgentState(TypedDict):
    messages: Annotated[list, operator.add]  # Merge, don't overwrite
    plan: str
    completed_steps: list[str]

# Nodes are plain Python functions
def planner(state: AgentState) -> dict:
    plan = llm.plan(state["messages"])
    return {"plan": plan}

def executor(state: AgentState) -> dict:
    result = execute_step(state["plan"])
    return {"messages": [result], "completed_steps": [result.step_id]}

# Build the graph
graph = StateGraph(AgentState)
graph.add_node("planner", planner)
graph.add_node("executor", executor)
graph.add_edge(START, "planner")
graph.add_conditional_edges("executor", should_continue,  # Route based on state
    {"continue": "executor", "done": END})
graph.add_edge("planner", "executor")

# Compile with checkpointer for persistence
app = graph.compile(checkpointer=PostgresSaver(conn))

# Run with thread_id for state tracking
result = app.invoke(
    {"messages": [user_query]},
    config={"configurable": {"thread_id": "user_123"}}
)
```

### OpenAI Agents SDK with Handoffs

```python
from agents import Agent, Runner, handoff

# Specialist agents
refund_agent = Agent(
    name="refund_specialist",
    instructions="Handle refund requests. Max refund $500.",
    tools=[process_refund, check_order_status],
    output_type=RefundResult  # Typed output via Pydantic
)

billing_agent = Agent(
    name="billing_specialist",
    instructions="Handle billing inquiries.",
    tools=[get_invoice, update_payment]
)

# Triage agent routes to specialists
triage_agent = Agent(
    name="triage",
    instructions="Route customer requests to the right specialist.",
    handoffs=[refund_agent, billing_agent]  # Appear as tools to the LLM
)

# Run with turn limit (financial safety control)
result = await Runner.run(
    triage_agent,
    input="I need a refund for order #12345",
    max_turns=8  # Hard stop, not None
)
```

### Fan-Out/Fan-In with LangGraph Send

```python
from langgraph.graph import StateGraph, Send

def orchestrator(state):
    """Dynamically fan out to workers based on plan."""
    subtasks = generate_subtasks(state["query"])
    # Send creates parallel worker instances with independent state
    return [Send("worker", {"task": t}) for t in subtasks]

def worker(state):
    """Each worker gets its own context window."""
    result = llm.execute(state["task"])
    return {"results": [result]}  # Reducer merges into parent

def synthesizer(state):
    """Join all worker results."""
    return {"answer": llm.synthesize(state["results"])}

graph = StateGraph(State)
graph.add_node("orchestrator", orchestrator)
graph.add_node("worker", worker)
graph.add_node("synthesizer", synthesizer)
graph.add_conditional_edges("orchestrator", orchestrator)  # Dynamic fan-out
graph.add_edge("worker", "synthesizer")
```

---

## 7. Common Pitfalls & Failure Modes

### Failure Rate Statistics

Industry estimates: agent failure rates in live environments at 70-95%, depending on task complexity. Large enterprises abandoned an average of 2.3 AI initiatives in 2025 (avg loss: $16.5M per enterprise). Gartner predicts >40% of agentic AI projects canceled by end of 2027.

**Top failure classes** (Arize 2026, categorized from production incidents):
- Context blindness: 31.6%
- Rogue actions: 30.3%
- Silent degradation: 24.9%
- Memory corruption: 8.1%
- Runaway execution: 5.1%

### Infinite Loops

LLMs lack an internal "stop" signal when encountering repetitive errors. A retry loop consumes context window space, pushing earlier reasoning out of scope. By timeout, the agent has no coherent record of its original goal.

**Symptoms**: `GraphRecursionError` at limit; `MaxTurnsExceeded`; identical `next` in consecutive checkpoints; 429 storm; linear cost growth.

**Causes**: Router never returns `END`; mapping key mismatch (`"done"` vs `"end"`); ReAct repetitive TAO (paper: 47% of failures); worker `allow_delegation=True` both ways; pagination-by-LLM; tool error returned as empty string so model retries.

**Mitigations**: Hard fuse AND soft `max_iterations` in state; hash last K (thought, action, args) and break on repeat; adapter-level circuit on duplicate calls; `RemainingSteps`; never `max_turns=None` in prod.

### Context Window Exhaustion

Long-running loops accumulate every tool output, intermediate thought, and message, stuffing all of it back into context each turn. Even with 200K+ token windows, recall degrades as context fills.

**Symptom**: Agent performs perfectly for first 5 steps, then degrades dramatically -- repeating work, forgetting constraints, contradicting prior decisions.

**Mitigations**: Context summarization at fixed intervals; Anthropic's subagent model (clean context windows, condensed summaries 1,000-2,000 tokens); move critical constraints out of conversation into durable database.

### Tool Call Cascading Failures

Each step conditions the next. A small error early compounds into completely wrong outcomes several steps later with no exception raised. In multi-agent systems, one agent's hallucinated output becomes another agent's authoritative input.

### Agent Hallucinating Task Completion

Agent hallucinations are fabricated "human-like behaviors" at any pipeline stage. Because agents operate in long-running loops (Observe-Plan-Act-Reflect), every decision becomes the foundation for the next; one bad API call or hallucinated fact and the entire plan collapses. EMNLP 2025: LLMs generate plausible but incorrect content with high internal self-consistency, defeating consistency-based detection methods.

Task completion rates reach ~70-75% in 2026 (8,128-user survey), but trust paradox: high completion alongside lower trust than manual search (37 percentage point trust gap among technically sophisticated users).

### Self-Correction Limitations

Reflection improvements vary wildly: +7-18% for reasoning tasks, but can decrease performance when initial accuracy is already high. Prompts soliciting mistakes induce up to 40.4% false positive correction rates. Research consensus: self-correction requires external verification (tool outputs, test results, separate critic models) to be reliable.

### Cost Runaway

OWASP 2025 LLM10: Unbounded Consumption. An agent costing $0.10/successful run but $1.00/failed loop quietly destroys its business case.

**Mitigation**: Per-task and per-hour budget limits enforced at platform level. Alert on cost per successful outcome (not total spend) -- total spend rising with volume is fine; cost per outcome rising is the regression signal.

### State Drift

**Symptoms**: User B sees user A's history; parallel workers clobber `session.state['result']`; subgraph amnesia; reducer not associative.

**Causes**: Shared `thread_id`; missing reducer; mutating `Session.state` off the event path; schema evolution without migration; Continue-As-New dropping fields.

**Mitigations**: Typed state + tests that two parallel `Send`s merge; distinct keys under ParallelAgent; version the state schema; Store for cross-thread facts.

### Lost Checkpoints

**Symptoms**: HITL never resumes; pod restart re-bills tools from turn 0; `exit` durability + OOM.

**Causes**: MemorySaver in prod; SQLite under multi-worker; Postgres connection timeout (unpooled); Temporal history overflow terminates the workflow.

### The Demo-to-Production Gap

2026 is the year enterprises moved agents from demos to production, exposing failure modes no demo showed. Organizations successfully scaling agents design for failure modes before deployment. Core production guardrails: irreversible actions require HITL approval; cost bounded per-task and per-hour; failures graceful with clear errors (not hallucinated success); monitoring real-time.

### Failure-Mode x Layer Matrix

| Failure | ReAct Loop | LangGraph | Temporal/Inngest | MCP/Tools |
| --- | --- | --- | --- | --- |
| Infinite loop | Repeat TAO | `recursion_limit` | Workflow loop without Continue-As-New | Duplicate `tools/call` |
| State drift | Context overflow | Missing reducer | History vs blob split | Session vs token identity |
| Lost checkpoint | Process death | MemorySaver / `exit` | History 50 MB kill | MCP session hijack |
| Plan hallucination | Bad thought | Supervisor node | Activity input is the bad plan | Wrong tool selected |
| Timeout cascade | N sequential tools | Super-step wait | Activity retry x children | Downstream 504 |

---

## 8. Interview Questions & Answers

**Q1: What is the ReAct pattern and why is it the default starting point for agents?**

ReAct stands for Reason + Act. It is a loop where the LLM alternates between writing explicit reasoning (Thought), selecting a tool (Action), and reading the result (Observation). It is the default because it is the simplest effective pattern -- you get grounding via tools (which nearly eliminates hallucination -- 0% vs CoT's 56% in the original paper) while maintaining the LLM's ability to reason across steps. The trade-off is that ReAct's dominant failure mode is repetitive loops (47% of failures in labeled data), so production use requires an external fuse like `max_turns`. Every major framework -- LangGraph, OpenAI Agents SDK, Google ADK -- implements ReAct as their base agent loop.

**Q2: When should you move from ReAct to Plan-and-Execute?**

When the task has 5+ interdependent steps in a stable environment. Plan-and-Execute front-loads reasoning in a single planning call, then routes 85% of execution tokens through a cheaper model. The CLEAR Framework measured $1.24/task for Plan-Execute vs $5.12 for Reflexion at the same accuracy. The weakness is rigidity -- if the environment changes mid-execution (a tool fails, data is unexpected), you need a replanning mechanism or the plan walks off a cliff. I would start with ReAct for dynamic exploratory tasks and move to Plan-and-Execute for structured repeatable workflows like financial analysis or data pipelines.

**Q3: What is the difference between a DAG and a cyclic graph in agent architectures?**

A DAG (Directed Acyclic Graph) has no cycles -- data flows one direction, which makes it perfect for deterministic ETL pipelines and fixed RAG workflows (Prefect, Airflow). But a ReAct loop is inherently cyclic: the agent keeps going around think-act-observe until it decides to stop. LangGraph exists precisely because a ReAct loop is not a DAG. It supports cycles, conditional branching, dynamic fan-out via `Send`, and shared typed state with reducers. If your workflow needs to loop back on itself (retry after tool error, iterate until quality threshold), you need a cyclic graph, not a DAG.

**Q4: Explain the difference between `max_turns=10` in OpenAI Agents SDK and `recursion_limit=25` in LangGraph.**

They measure different units. In the Agents SDK, a "turn" is one model invocation including any tool calls that happen with it. In LangGraph, a "superstep" is one round of the Pregel execution model where all scheduled nodes run, then reducers merge, then a checkpoint is written. A typical ReAct tool cycle takes 2 supersteps (model node + tool node), so `recursion_limit=25` is roughly 12 tool rounds before the fuse blows. Converting between them requires knowing how many nodes are in each tool cycle. This matters because a default-25 LangGraph graph and a default-10 Agents SDK runner have very different cost ceilings.

**Q5: How do you handle state in a multi-agent system? What are the failure modes?**

There are four types of state: conversation (message history), tool (intermediate results), planning (current plan and completed steps), and memory (cross-run knowledge). The critical architectural decision is choosing between shared state (all agents read/write the same object) and isolated state (each agent has its own context). LangGraph uses typed state with reducers -- if two parallel nodes update the same key without a reducer, you get `InvalidUpdateError`. Anthropic's pattern gives each subagent a clean context window and has it return a condensed 1,000-2,000 token summary. Common failure modes: user B seeing user A's history (shared thread_id), parallel workers clobbering the same state key (missing reducer), and stale state after schema evolution. The fix is typed state, tested reducer logic, and separate stores for cross-thread facts.

**Q6: What is durable execution and why do agents need it?**

Durable execution means your agent survives infrastructure failures -- container crashes, network partitions, host preemptions -- by automatically replaying to the point of failure and continuing. Agent frameworks like LangGraph solved the planning loop by 2025, but the remaining challenge is operational: the agent dies mid-run, an approval lands a day late, an upstream API rate-limits. Temporal is the dominant solution: you define a Workflow (deterministic orchestration) and Activities (non-deterministic work like LLM calls). On crash, Temporal replays the workflow history without re-executing completed activities. OpenAI uses Temporal for Codex in production. The key insight is that checkpointing alone is not durable execution -- LangGraph checkpoints but provides no automatic failure detection. Production deployments often compose LangGraph (cognition) inside Temporal (durability).

**Q7: How do you prevent prompt injection in a multi-agent system?**

The 2026 consensus is containment, not cure. Prompt injection remains #1 on OWASP LLM Top 10 because LLMs treat system prompt, user request, and retrieved text as one token stream with no reliable command/data boundary. In multi-agent systems, injection in one data source can propagate through agent chains. The defense-in-depth approach has six layers: (1) identity -- each agent gets its own identity, (2) least privilege -- assume injection succeeds and limit what a compromised agent can do, (3) runtime enforcement -- checks before tool calls, (4) behavioral monitoring, (5) audit logging, (6) supply chain security. Sandboxing controls where an agent runs; least-privilege controls what it does. Both are required. Specific sandboxing choices: Firecracker microVMs for regulated data, gVisor for multi-tenant compute, V8 Isolates for latency-critical JS. Standard containers are not considered acceptable isolation for agentic workloads.

**Q8: Design a customer support agent system that handles refunds, billing, and technical issues.**

I would use the router pattern with Anthropic's workflow-first principle. A lightweight triage agent (Haiku-class model) classifies intent and routes to specialist agents. Each specialist has scoped tools: the refund agent gets `process_refund` and `check_order_status` but never `delete_account`; the billing agent gets `get_invoice` and `update_payment`. For the flow: router -> policy DAG (refund rules as deterministic code, not LLM) -> ReAct specialist (CRM via MCP, max 6 turns) -> HITL interrupt if refund > threshold -> Inngest/Temporal wait for approval (could be 24 hours, zero compute while waiting). Key NFRs: irreversible actions (refunds) require approval, per-task cost bounded at $0.50, real-time monitoring for runaway agents. I would use model routing -- Haiku/luna for the 70% easy queries, Sonnet/terra for the 30% hard ones -- for 40-70% cost reduction. Success metric: resolution rate and cost per successful outcome, not tokens consumed.

**Q9: What are the trade-offs between the major agent loop architectures?**

| Criterion | ReAct | Plan-and-Execute | DAG/Graph |
| --- | --- | --- | --- |
| Best for | Dynamic, exploratory tasks | Structured, repeatable workflows | Complex parallel pipelines |
| Adaptability | High (re-plans each step) | Low unless replanning added | Dynamic replanning possible |
| Token efficiency | Low (LLM call per tool) | High (cheap executor, expensive planner) | High via parallelism |
| Speed | Sequential | Sequential | Parallel (3.7x with LLMCompiler) |
| Predictability | Variable | High | Depends on DAG config |
| Complexity | Low | Medium | High |
| Cost (CLEAR data) | Baseline | $1.24/task | Depends on parallelism |
| Dominant failure | Repetitive loops (47%) | Stale plan walks off cliff | Aggregation hallucination |

**Q10: How would you handle an agent that needs to wait days for human approval?**

Never hold a request worker or GPU. Use durable execution: Temporal Signal/Update or Inngest `step.waitForEvent`. The agent persists its state, releases all compute, and resumes from the exact pause point when approval arrives -- zero cost while waiting. LangGraph's `interrupt(value)` requires a checkpointer; the graph waits indefinitely until `Command(resume=...)`. On Agent Server or Temporal, this is a durable wait. Without durable execution, you serialize `RunState` (Agents SDK) or use `@persist` (CrewAI) and park it in your database. The architectural mistake is using in-process blocking, which ties up a worker and crashes on restart.

**Q11: Explain event sourcing for agent trajectories and why it matters for compliance.**

Event sourcing records every state transition, tool call, and observation as an immutable event in an append-only log. On crash, you replay events to reconstruct exact state without re-executing side effects (natural idempotency). For compliance (EU AI Act Article 12), this is critical: high-risk AI systems must maintain structured, tamper-evident, complete records retained at least 6 months. Event sourcing gives you a permanent, queryable record of why an autonomous system made each decision. The key distinction: event streaming (Kafka) moves data between systems (throughput); event stores (Axon Server) capture decisions with full causal context (auditability). In 2026, 61% of organizations have fragmented logs across systems; 33% lack evidence-quality audit trails.

**Q12: What is the cost difference between a 10-turn agent loop and a single LLM call, and how do you control it?**

A single LLM call costs roughly $0.02 with a mid-tier model. A 10-turn agent loop costs $0.087 with prompt caching, or $0.22 without it -- roughly 4-10x more. Multi-agent systems can be 15x a single call. The controls: (1) `max_turns` is a financial control, not just correctness -- a default-25 LangGraph graph at 1k runs/day is $203/day vs $21/day for single-turn; (2) model routing saves 40-70% by using cheap models for easy queries; (3) prompt caching saves 40-80% when tool definitions and system prompts repeat; (4) per-task and per-hour token budgets that halt execution, not just warn; (5) alert on cost per successful outcome -- total spend rising with volume is fine, cost per outcome rising is the regression signal.

**Q13: How do you design for observability in a multi-agent system?**

Multi-agent observability tracks "trajectories" -- the series of steps through tools and sub-agents. Three pillars: distributed tracing for cross-agent calls (OpenTelemetry with W3C trace-context is the 2026 standard), evaluation frameworks for reasoning quality, and real-time logs for debugging. Key tools: Arize Phoenix (open-source, OpenTelemetry-native), LangSmith, Temporal Conductor. The shift from log-diving to time-travel debugging is permanent -- with deterministic replay, you pull workflow history, replay locally with new code, and watch the decision change. Critical: map traces to identity (LangSmith auth user, GCP IAM, AMP RBAC actor) or they are useless for SOX/compliance.

**Q14: What is A2A vs MCP and when do you use each?**

MCP (Model Context Protocol) is agent-to-tools: JSON-RPC for `tools/list` and `tools/call`. Think of it as "USB-C for AI applications." A2A (Agent-to-Agent Protocol) is agent-to-agent: Agent Card discovery, task lifecycle, messages, artifacts, streaming. They are complementary, not competing. Use MCP when an agent needs to call tools (database queries, API calls, file operations). Use A2A when agents from different trust domains, vendors, or languages need to communicate as opaque peers. In-process dispatch (LangGraph `ToolNode`, SDK `FunctionTool`) is cheaper than either protocol when agents are co-located. The Google enterprise pattern: ADK orchestrator on Cloud Run, MCP servers as anti-corruption layers to backends, A2A for remote agents from different vendors.

---

## 9. Key Numbers to Memorize

| Metric | Value | Context |
| --- | --- | --- |
| ReAct hallucination rate | 0% (vs CoT 56%) | Paper labeled data; grounding kills hallucination |
| ReAct loop failure rate | 47% | Dominant failure mode: repetitive TAO |
| Agent token multiplier | 4x chat; 15x multi-agent | vs single LLM call |
| Plan-Execute cost ratio | $1.24 vs $5.12 Reflexion | CLEAR Framework, same accuracy |
| LLMCompiler speedup | 3.7x latency, 6.7x cost | vs sequential ReAct |
| ReWOO token efficiency | 5x | vs interleaved approaches |
| Fan-out wall-clock savings | ~75% | vs sequential execution |
| Agents SDK default max_turns | 10 | `MaxTurnsExceeded` error |
| LangGraph default recursion_limit | 25 supersteps (~12 tool rounds) | `GraphRecursionError` |
| Production failure rate | 70-95% | Varies by task complexity |
| Infra vs model failures | 88% infrastructure | Arize 2026 |
| Prompt injection cost | ~$4.7M average breach | 2025 estimate |
| SWE-bench SOTA | 87.6% (Claude Opus 4.7) | Baseline was 1.96% in 2023 |
| Model routing savings | 40-70% cost reduction | No quality loss |
| Prompt caching savings | 40-80% | When prompt tokens dominate |
| EU AI Act penalties | 35M EUR or 7% global turnover | Full enforcement Aug 2, 2026 |
| Temporal history limits | 10,240 events warn / 51,200 terminate | Continue-As-New to reset |
| State management incidents | 60% of production incidents | LangChain 2026 report |
| Agentic AI market | $10.9B (2026) -> $199B (2034) | 43.8% CAGR |

---

## 10. Quick Reference

### Architecture Selection Cheat Sheet

```
Is the task dynamic and exploratory?
  YES -> Start with ReAct (simplest effective)
  NO  -> Is it 5+ interdependent steps in stable environment?
    YES -> Plan-and-Execute (85% tokens on cheap model)
    NO  -> Is it parallelizable independent subtasks?
      YES -> DAG/Fan-out (3.7x latency improvement)
      NO  -> Is output quality critical?
        YES -> Add Reflexion/Evaluator-Optimizer
        NO  -> Stick with ReAct
```

### Pattern Trade-Offs at a Glance

| Pattern | Latency | $/task | Durability | Best For |
| --- | --- | --- | --- | --- |
| Single LLM call | Best | Best | None | Classification, simple QA |
| ReAct (10 turns) | Linear in tools | Linear-quadratic tokens | App-managed | Tool-using assistants |
| Plan-and-Execute | Plan + exec | Fewer planner tokens | Same | Structured workflows |
| Orchestrator-workers | ~max(workers) | 2x supervisor + N workers | Same | Research, coding |
| Fan-out/Fan-in | Parallel | N x worker cost | Same | Independent subtasks |
| Evaluator-Optimizer | +30% latency | +30% tokens | Same | Quality-critical output |
| ToT/LATS | Worst | Worst | Research | Large solution spaces |

### Production Guardrails Checklist

- [ ] Hard `max_turns` / `recursion_limit` set (never unbounded)
- [ ] Per-task and per-hour token budgets enforced
- [ ] Irreversible actions require HITL approval
- [ ] Checkpointer is durable (Postgres, not MemorySaver)
- [ ] Loop detection: hash recent (tool, args), break on repeat
- [ ] Tool timeouts on every external call
- [ ] Idempotency keys on mutating tool calls
- [ ] Cost alert on per-outcome metric, not total spend
- [ ] Real-time monitoring (see runaway agents while running)
- [ ] Graceful failure with clear errors, not hallucinated success

### Key Formulas

```
Agent loop cost = sum_i(input_tokens_i * price_in + output_tokens_i * price_out)
                  where input grows each turn (context accumulates)

Cache savings   = (1 - cache_miss_rate) * stable_prefix_tokens * (price_in - price_cached)

Fan-out latency = max(worker_latencies) + join_call_latency
Fan-out cost    = N * worker_cost + supervisor_cost

Temporal limit  = 51,200 events or 50 MB (hard terminate)
                  Use Continue-As-New before hitting this
```

### Decision Matrix

| Requirement | Prefer | Avoid |
| --- | --- | --- |
| Fixed 4-step pipeline, SLO < 3s | Prompt chain / Sequential | Open ReAct with 10 turns |
| Unknown subtasks (multi-file coding) | Orchestrator-workers + cap N + HITL | Unbounded ReAct |
| Chat + tools, <10 hops | ReAct, `max_turns=8`, cache prefix | ToT/LATS in the hot path |
| Approval that may take days | Temporal Signal / Inngest wait | Holding a request worker |
| Multi-vendor agents | A2A tasks + MCP tools | Shared DB as "protocol" |
| Strict audit / HIPAA | Customer-managed checkpoints + redacted traces | Default full-payload logging |
| 10k concurrent sessions | Postgres checkpoints + token buckets | SQLite, in-memory sessions |

### Interview Sound-Bites

1. ReAct trades hallucination (0%) for reasoning loops (47%) -- production must add a fuse the paper did not.
2. A ReAct loop is a cyclic graph; Prefect/Airflow DAGs cannot express retry-until.
3. `max_turns=10` and `recursion_limit=25` are different units; converting requires nodes per tool cycle.
4. Checkpointer (thread state) is not memory Store (cross-thread) is not Temporal history is not MCP session.
5. Honor 429; break on 5xx; retry once; never nested retries (3x3x3 = 27 upstream calls).
6. Extra ReAct turns are the dominant cost knob; cache-break the prefix and cost doubles.
7. Dynamic replanning is the difference between working Plan-and-Execute and a stale plan walking off a cliff.
8. 88% of agent failures are infrastructure, not model quality.
