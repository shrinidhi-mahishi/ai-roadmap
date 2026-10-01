# 08. Agentic Design Patterns

**Sub-areas covered**: The control spectrum from direct API calls through workflows to autonomous agents, the augmented LLM building block (retrieval + tools + memory), Anthropic's five workflow patterns (prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer), Andrew Ng's four foundational patterns (reflection, tool use, planning, multi-agent collaboration), single-agent reasoning architectures (ReAct, ReWOO, Reflexion, Plan-and-Execute, Tree-of-Thoughts) with algorithmic complexity analysis and state machine formalization, multi-agent orchestration topologies (supervisor, decentralized handoff, Magentic outer/inner loop, sequential pipeline, debate/voting), token economics with cost amplification formulas ($0.02-$40+ per agentic run), latency SLA profiles (p50/p95/p99 across architectures), context accumulation O(n^2) problem and mitigation strategies, framework comparison (LangGraph, OpenAI Agents SDK, CrewAI, Microsoft Agent Framework) with production readiness assessment, durable execution patterns (checkpointing, idempotency, circuit breakers, graceful degradation), failure taxonomy from 1,642 execution traces (41-86.7% failure rates), seven production failure modes with detection and mitigation, permission boundaries (60% of agents over-permissioned), tiered human-in-the-loop governance, audit trail requirements with reasoning trace capture, regulatory landscape (EU AI Act, ISO 42001, NIST Zero Trust), production Python code with exponential backoff/jitter/circuit breakers/fallback chains/structured logging, and two enterprise system-design scenarios (customer support platform, autonomous code review pipeline) with architecture diagrams and trade-off matrices

---

## 1. System Topology & Data Flow

A production agentic system spans five cooperating layers: a **control plane** handling pattern selection, agent lifecycle management, and permission enforcement before any tool executes; a **reasoning plane** where LLM-driven decision loops (ReAct, Plan-and-Execute, Reflexion) produce the next action; a **tool execution plane** routing tool calls through circuit breakers, rate limiters, and idempotency guards; a **persistence layer** checkpointing agent state at every decision boundary so crashes never require full restarts; and a **telemetry layer** capturing per-step token counts, latency, reasoning traces, and cost for post-hoc audit and anomaly detection.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                   │
│                                                                              │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │ Pattern Selector   │   │ Agent Lifecycle   │   │ Permission Boundary    │  │
│  │                    │   │ Manager            │   │ Engine                 │  │
│  │ Decision tree:     │   │                    │   │                        │  │
│  │  L1 Direct call    │──▶│ Spawn / suspend /  │──▶│ Tiered approval:       │  │
│  │  L2 Workflow       │   │ resume / kill      │   │  read-only: auto       │  │
│  │  L3 Single agent   │   │ agents. Enforce    │   │  write: async gate     │  │
│  │  L4 Multi-agent    │   │ step ceiling,      │   │  irreversible: sync    │  │
│  │                    │   │ token budget,      │   │  policy change: human  │  │
│  │ Escalate only when │   │ wall-clock timeout │   │  workflow only         │  │
│  │ simpler level      │   │                    │   │                        │  │
│  │ demonstrably fails │   └────────┬─────────┘   └───────────┬────────────┘  │
│  └────────┬──────────┘            │                          │              │
└───────────┼────────────────────────┼──────────────────────────┼──────────────┘
            │ selected pattern       │ lifecycle events         │ scoped perms
┌───────────▼────────────────────────▼──────────────────────────▼──────────────┐
│                         REASONING PLANE                                      │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ ReAct Loop        │  │ Plan-and-Execute │  │ Orchestrator-Workers     │   │
│  │                   │  │                  │  │                          │   │
│  │ thought -> action │  │ plan(task)       │  │ Orchestrator LLM:        │   │
│  │ -> observation    │  │ -> [step_1,      │  │  decompose(task)         │   │
│  │ -> thought -> ... │  │    step_2, ...]  │  │  -> delegate(worker_i)   │   │
│  │                   │  │ execute(step_i)  │  │  -> synthesize(results)  │   │
│  │ Context grows     │  │ replan if        │  │                          │   │
│  │ O(n^2) per step   │  │ observation      │  │ Worker LLMs:             │   │
│  │                   │  │ contradicts plan │  │  scoped tools + context  │   │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────┬───────────────┘   │
│           │                     │                        │                   │
│  ┌────────▼─────────────────────▼────────────────────────▼───────────────┐   │
│  │                    Reflection / Self-Critique Layer                    │   │
│  │  After each iteration: evaluate output quality, detect loops,         │   │
│  │  check diminishing returns (<5% improvement over last 3 steps).       │   │
│  │  2-3x token cost per reflection cycle.                                │   │
│  └────────┬──────────────────────────────────────────────────────────────┘   │
└───────────┼─────────────────────────────────────────────────────────────────┘
            │ tool_call(name, args, idempotency_key)
┌───────────▼─────────────────────────────────────────────────────────────────┐
│                     TOOL EXECUTION PLANE                                     │
│                                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  ┌────────────────┐    │
│  │ Tool Router   │  │ Per-Tool     │  │ Idempotency │  │ Fallback Chain │    │
│  │               │  │ Circuit      │  │ Guard       │  │                │    │
│  │ MCP / native  │  │ Breaker      │  │             │  │ Primary tool   │    │
│  │ function call │──▶│              │──▶│ Check key   │──▶│ -> cached     │    │
│  │ dispatch      │  │ CLOSED ->    │  │ before      │  │    result      │    │
│  │               │  │ OPEN ->      │  │ executing   │  │ -> parametric  │    │
│  │               │  │ HALF_OPEN    │  │ side effect │  │    fallback    │    │
│  └──────┬───────┘  └──────┬──────┘  └──────┬──────┘  └────────┬───────┘    │
└─────────┼──────────────────┼────────────────┼──────────────────┼────────────┘
          │                  │                │                  │
┌─────────▼──────────────────▼────────────────▼──────────────────▼────────────┐
│                          PERSISTENCE LAYER                                   │
│                                                                              │
│  ┌──────────────────┐  ┌───────────────────┐  ┌────────────┐  ┌──────────┐ │
│  │ Checkpoint Store  │  │ Idempotency       │  │ Plan Cache │  │ Memory   │ │
│  │                   │  │ Registry          │  │            │  │ Store    │ │
│  │ LangGraph:        │  │                   │  │ Successful │  │          │ │
│  │  PostgresSaver    │  │ (tool_name,       │  │ tool-call  │  │ Short-   │ │
│  │  (every node      │  │  idempotency_key) │  │ plans for  │  │ term:    │ │
│  │  transition)      │  │ -> completed_at,  │  │ recurring  │  │  working │ │
│  │                   │  │    result_hash    │  │ tasks.     │  │ Long-    │ │
│  │ Temporal:         │  │                   │  │ 20-35%     │  │  term:   │ │
│  │  event log +      │  │ Required for      │  │ cost       │  │  episodic│ │
│  │  deterministic    │  │ durable exec:     │  │ reduction  │  │          │ │
│  │  replay           │  │ resume must not   │  │            │  │          │ │
│  │                   │  │ re-fire actions   │  │            │  │          │ │
│  └──────────────────┘  └───────────────────┘  └────────────┘  └──────────┘ │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────────────────┐
│                    TELEMETRY / OBSERVABILITY LAYER                            │
│                                                                              │
│  Per-step token count + latency  |  Tool call success/failure rates  |      │
│  Reasoning trace log (not just I/O)  |  Loop count anomaly detection  |     │
│  Cost-per-run meter  |  Step count vs ceiling alerts  |                     │
│  Wall-clock time vs SLA  |  Quality regression detector  |                  │
│  Cascading failure propagation tracker (multi-agent)                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A user request enters the control plane, where the pattern selector determines the appropriate complexity level -- direct call, workflow, single agent, or multi-agent -- applying the heuristic that if all steps can be enumerated before runtime, a workflow suffices. (2) The agent lifecycle manager spawns the selected pattern with enforced resource bounds: step ceiling (default 25 in LangGraph), token budget cap, and wall-clock timeout. The permission boundary engine assigns tiered access scoped to the task: read-only operations proceed autonomously, write operations require async approval, irreversible actions require synchronous human gates. (3) The request enters the reasoning plane, where the selected reasoning architecture (ReAct, Plan-and-Execute, or Orchestrator-Workers) drives iterative LLM calls. Each iteration produces a thought (reasoning trace), an action (tool call with arguments), and an observation (tool result). The reflection layer optionally evaluates output quality after each iteration and terminates execution on loop detection or diminishing returns. (4) Each tool call exits the reasoning plane into the tool execution plane, where the tool router dispatches via MCP or native function calling. The per-tool circuit breaker checks backend health (CLOSED/OPEN/HALF_OPEN state machine), the idempotency guard verifies via key lookup whether the side effect has already been executed (critical for checkpoint-resume), and the fallback chain degrades gracefully if the primary tool is unavailable. (5) At every node transition in the reasoning plane, the persistence layer checkpoints the full agent state -- conversation history, task state, completed steps, and external side-effect records. This checkpoint is the single most impactful resilience mechanism: without it, every crash means restarting from scratch. (6) Throughout execution, the telemetry layer records per-step metrics. Anomaly detection fires alerts when loop counts exceed thresholds, token consumption spikes, or output quality metrics degrade silently -- the failure mode unique to agentic systems compared to traditional distributed systems.

---

## 2. Core Mechanics & Algorithms

### 2.1 The Control Spectrum: Escalation Levels

The foundational design axis is who controls execution flow. This forms an escalation ladder where each level adds capability and complexity:

```
┌──────────┬──────────────┬───────────────────────────┬──────────────────────┐
│ Level    │ Controller   │ Defining Signal           │ Example              │
├──────────┼──────────────┼───────────────────────────┼──────────────────────┤
│ L1       │ Code         │ Single prompt, single     │ Summarization,       │
│ Direct   │              │ response                  │ classification       │
│ API call │              │                           │                      │
├──────────┼──────────────┼───────────────────────────┼──────────────────────┤
│ L2       │ Code         │ All steps known before    │ Contract review      │
│ Workflow │ orchestrates │ runtime                   │ pipeline, customer   │
│ patterns │ LLM executes │                           │ triage               │
├──────────┼──────────────┼───────────────────────────┼──────────────────────┤
│ L3       │ LLM decides  │ Steps/count unknown       │ SWE-bench coding     │
│ Agent    │ next steps   │ until runtime             │ agent, open-ended    │
│ patterns │              │                           │ research             │
├──────────┼──────────────┼───────────────────────────┼──────────────────────┤
│ L4       │ Multiple     │ No single LLM can hold    │ Magentic-One         │
│ Multi-   │ LLMs         │ all context/tools         │ web+code+file tasks  │
│ agent    │ coordinate   │                           │                      │
└──────────┴──────────────┴───────────────────────────┴──────────────────────┘
```

**Key heuristic**: "If you can still write down all the steps before the system runs, stick with a workflow." Anthropic's equivalent: "Add complexity only when it demonstrably improves outcomes." Most production systems should not need to go further than parallelization (L2 workflow patterns).

### 2.2 The Augmented LLM Building Block

Every agentic system rests on an LLM enhanced with three capabilities that extend its reach beyond parametric knowledge:

- **Retrieval**: Accessing external knowledge at inference time (RAG, search indices, knowledge graphs). Grounds responses in current facts rather than training-time snapshots.
- **Tools**: Interacting with external services and APIs via function calling. The ReAct paradigm (reason-act-observe-repeat) remains the dominant mental model even when implemented via native function calling in Claude 3.x+, GPT-4o, or Gemini 2.x.
- **Memory**: Determining what information to retain across turns. Short-term working memory (within a session) and long-term episodic memory (across sessions) serve different purposes.

MCP (Model Context Protocol) has emerged as the open standard for model-tool integration, donated to the Linux Foundation's Agentic AI Foundation in Dec 2025 and adopted by OpenAI, Google, and Microsoft. A2A (Agent-to-Agent Protocol, Google) standardizes inter-agent communication. Together they form the protocol stack separating tool access from agent coordination.

### 2.3 Workflow Patterns (L2): Code Controls Flow

Five patterns at the workflow level where code orchestrates and the LLM executes predefined steps:

**Pattern 1: Prompt Chaining** -- Linear pipeline where each LLM call feeds the next. Validation gates between steps are the critical reliability mechanism. Latency grows linearly: N steps = N round-trips. Errors carry forward through the chain if gates are absent.

```
┌─────────┐   gate   ┌─────────┐   gate   ┌─────────┐
│  LLM_1  │──pass?──▶│  LLM_2  │──pass?──▶│  LLM_3  │──▶ output
│ (draft) │  fail?   │(refine) │  fail?   │(format) │
└─────────┘   ↓      └─────────┘   ↓      └─────────┘
            abort                abort
```

**Pattern 2: Routing** -- Classifier dispatches to specialized handlers. Router accuracy is a system-wide ceiling on quality: if the classifier misroutes, the best handler in the world cannot compensate. Cost optimization is inherent: simple queries hit cheap/fast models, complex queries hit expensive/capable ones.

```
                 ┌──▶ Handler_cheap (FAQ, simple)
┌────────────┐   │
│ Classifier │───┼──▶ Handler_specialized (domain-specific)
│  (router)  │   │
└────────────┘   └──▶ Handler_expensive (reasoning-heavy)
```

**Pattern 3: Parallelization** -- Two sub-patterns:
- **Sectioning**: Split task into independent parts, run simultaneously, merge results. Critical design decision: partial failure strategy (retry / proceed without / fail entire operation) must be decided at design time.
- **Voting**: Run same task N times with different prompts, aggregate results. Flag only if 2+ agree. Useful for content moderation and code vulnerability review.

Cost multiplies with every parallel branch. Anthropic notes that running a guardrail agent in parallel with the main agent "tends to perform better than having the same LLM call handle both."

**Pattern 4: Orchestrator-Workers** -- Hub-and-spoke topology. Central orchestrator LLM dynamically decomposes the task at runtime (subtasks are NOT pre-defined), delegates to workers, and synthesizes results. Failure modes include goal drift, over-decomposition, and orchestrator becoming a bottleneck.

**Pattern 5: Evaluator-Optimizer** -- Generator and evaluator LLMs iterate in a feedback loop. Applicable only when: (1) LLM responses demonstrably improve with articulated feedback, AND (2) the LLM can provide such feedback. Requires clear, measurable evaluation criteria.

### 2.4 Single-Agent Reasoning Architectures (L3): State Machines

Each reasoning pattern can be formalized as a state machine with distinct transition rules and complexity characteristics:

**ReAct (Reason-Act-Observe)**

The default choice for general-purpose tool-using agents. Each step re-reads the entire message history plus all prior tool outputs, creating quadratic context growth.

```
State Machine:
                    ┌──────────────────────────────────────┐
                    │                                      │
                    ▼                                      │
┌────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┤
│  INIT  │───▶│  THINK   │───▶│   ACT    │───▶│  OBSERVE  │
└────────┘    │          │    │(tool call)│    │(tool resp) │
              │ Generate │    └──────────┘    └───────────┘
              │ reasoning│          │                │
              │ + select │          │ no tool needed  │
              │ next tool│          ▼                │
              └──────────┘    ┌──────────┐          │
                              │ COMPLETE │◀─────────┘
                              │ (final   │   done condition
                              │  answer) │
                              └──────────┘

Complexity: O(n^2) tokens where n = number of steps
            Step k re-reads context of size O(k), total = sum(1..n) = O(n^2)
Typical cost: 10-30x a single call for 5-10 steps
Latency: 15-60s for 5-10 steps (tool call latency dominates)
```

**ReWOO (Reasoning Without Observation)**

Separates planning from execution entirely. The planner generates all tool calls upfront without seeing intermediate results. 30-50% cheaper than ReAct because context is not re-accumulated at each step.

```
State Machine:
┌────────┐    ┌──────────┐    ┌──────────────┐    ┌───────────┐
│  INIT  │───▶│  PLAN    │───▶│ EXECUTE_ALL  │───▶│ SYNTHESIZE│
└────────┘    │          │    │              │    │           │
              │ Generate │    │ Run all tool │    │ Combine   │
              │ complete │    │ calls (may   │    │ results + │
              │ tool-call│    │ run parallel)│    │ produce   │
              │ plan     │    │              │    │ answer    │
              └──────────┘    └──────────────┘    └───────────┘

Complexity: O(n) tokens -- no context re-reading between steps
Trade-off: Cannot adapt mid-execution if early results change the plan
Best for: Predictable multi-step workflows where steps are independent
```

**Reflexion**

ReAct augmented with self-critique after failure and episodic memory. The agent reflects on why it failed and stores the lesson for future attempts.

```
State Machine:
                         ┌────────────────────────────────┐
                         │                                │
                         ▼                                │
┌────────┐    ┌──────────────────┐    ┌───────────┐    ┌──┴────────┐
│  INIT  │───▶│ ReAct EPISODE    │───▶│ EVALUATE  │───▶│ REFLECT   │
└────────┘    │ (standard loop)  │    │           │    │           │
              └──────────────────┘    │ Success?  │    │ Analyze   │
                                      │  YES ──▶ DONE │ failure   │
                                      │  NO  ──▶──────│ mode,     │
                                      └───────────┘    │ store in  │
                                                       │ episodic  │
                                                       │ memory    │
                                                       └───────────┘

Complexity: 2-3x ReAct per episode
Best for: Tasks with repeating failure modes (the memory amortizes cost)
```

**Plan-and-Execute**

Create a full plan first, then execute steps sequentially. Avoids ReAct's context accumulation during execution but pays an upfront planning cost.

```
Complexity: O(p) for planning + O(n) for execution
            Avoids O(n^2) context re-reading
Latency: 10-30s planning upfront + sequential execution
Best for: Planning-bottlenecked tasks where step order matters
```

**Tree-of-Thoughts**

Branch-and-bound exploration of multiple reasoning paths. Most expensive but most powerful for hard combinatorial problems.

```
Complexity: O(b^d) where b = branching factor, d = depth
            10-100x CoT (chain-of-thought) token cost
Performance: Game of 24 benchmark: 74% vs CoT's 4%
Best for: Hard combinatorial problems where greedy search fails
Use rarely: The cost is justified only when other patterns plateau
```

**Production recommendation** (LangGraph docs, multiple practitioners): "Default to ReAct. Build a baseline; measure success rate, tool-call accuracy, latency, cost. Identify the specific failure mode. Then escalate."

### 2.5 Multi-Agent Orchestration Topologies (L4)

Multi-agent systems add 2-5x coordination overhead from inter-agent communication (every message costs tokens bilaterally: sender generates, receiver processes).

```
┌───────────────────┬───────────────────────────────┬──────────────────────┐
│ Topology          │ Description                   │ Framework Support    │
├───────────────────┼───────────────────────────────┼──────────────────────┤
│ Supervisor /      │ Central agent routes/delegates│ CrewAI (hierarchical)│
│ Manager           │ to specialists                │ LangGraph, OpenAI    │
├───────────────────┼───────────────────────────────┼──────────────────────┤
│ Decentralized     │ Agents transfer control to    │ OpenAI Agents SDK    │
│ Handoff           │ each other; no coordinator    │ (handoff), AutoGen   │
├───────────────────┼───────────────────────────────┼──────────────────────┤
│ Magentic (Outer + │ Orchestrator maintains task   │ Microsoft Agent      │
│ Inner Loop)       │ ledger; outer loop manages    │ Framework (MAF)      │
│                   │ plan, inner loop manages      │                      │
│                   │ progress                      │                      │
├───────────────────┼───────────────────────────────┼──────────────────────┤
│ Sequential        │ Agents in fixed order; output │ CrewAI (sequential   │
│ Pipeline          │ feeds next                    │ process)             │
├───────────────────┼───────────────────────────────┼──────────────────────┤
│ Debate / Voting   │ Multiple agents independently │ AutoGen, custom      │
│                   │ solve same problem; results   │                      │
│                   │ aggregated                    │                      │
└───────────────────┴───────────────────────────────┴──────────────────────┘
```

Google researchers found multi-agent coordination dropped performance 39-70% on complex tasks while multiplying token spend. Enterprise two-agent setups (assistant + user proxy) cover most real cases; group chat topologies should be used only when rotating roles are genuinely required.

### 2.6 Pattern Selection Decision Tree

```
START: Can a single LLM call solve this?
  │
  ├─ YES ──▶ L1: Direct API call. Stop.
  │
  └─ NO  ──▶ Can you write down all steps before runtime?
              │
              ├─ YES ──▶ Is it a linear pipeline?
              │          │
              │          ├─ YES ──▶ Prompt Chaining
              │          │
              │          └─ NO  ──▶ Are inputs categorically different?
              │                     │
              │                     ├─ YES ──▶ Routing
              │                     │
              │                     └─ NO  ──▶ Can subtasks run independently?
              │                                │
              │                                ├─ YES ──▶ Parallelization
              │                                │
              │                                └─ NO  ──▶ Prompt Chaining
              │
              └─ NO  ──▶ Is a single LLM sufficient with tools?
                         │
                         ├─ YES ──▶ Open-ended? ──▶ ReAct (default)
                         │                          + Reflection if quality insufficient
                         │         Structured? ──▶ Plan-and-Execute
                         │
                         └─ NO  ──▶ Need central coordinator?
                                    │
                                    ├─ YES ──▶ Orchestrator-Workers (single)
                                    │          or Supervisor (multi-agent)
                                    │
                                    └─ NO  ──▶ Decentralized Handoff
```

**The 70-80% trap**: A common mistake is reaching 70-80% with a simple approach and assuming the architecture needs upgrading. Usually the real issue is prompt quality, missing validation gates, or poor tool design. Anthropic's insight: "We spent more time optimizing our tools than the overall prompt."

### 2.7 Key Invariants

1. **Escalation monotonicity**: Never jump to L4 without demonstrating L2/L3 failure on the same task. Each level adds cost, latency, and failure modes.
2. **Checkpoint-before-wait**: Any human-in-the-loop pause must persist state to external storage BEFORE the wait begins. No live process held during human decision time.
3. **Idempotency-with-checkpointing**: Durable execution without idempotency is half-solved. Every side-effecting tool must carry an idempotency key.
4. **Router-accuracy ceiling**: In routing patterns, the classifier's accuracy is an upper bound on system quality. Invest in the router before investing in handlers.
5. **Context growth bound**: ReAct agents must enforce a step ceiling. Without it, O(n^2) context growth can burn $40+ in a single runaway loop.

---

## 3. Token Economics & NFR Analysis

### 3.1 The Fundamental Cost Shift

Agentic AI workloads differ from chat by orders of magnitude:

- A single agentic session consumes 1-3.5 million tokens per task (50-500x a chat interaction).
- Input tokens dominate: Stanford Digital Economy Lab found re-sent context accounts for 62% of total agent inference bills.
- Token prices dropped 67% YoY (Q1 2025 to Q1 2026: $18.40 to $6.07 per million tokens), yet 73% of enterprises exceeded AI budgets (Gartner).
- Gartner projects 40% of AI agent projects will be cancelled by 2027 due to cost overruns alone.

### 3.2 Token Amplification by Pattern

```
┌──────────────────────────────┬────────────┬──────────────────────────────────┐
│ Pattern                      │ Multiplier │ Key Driver                       │
│                              │ vs. single │                                  │
│                              │ call       │                                  │
├──────────────────────────────┼────────────┼──────────────────────────────────┤
│ Single LLM call              │ 1x         │ Baseline                         │
├──────────────────────────────┼────────────┼──────────────────────────────────┤
│ Prompt Chaining (3-5 steps)  │ 3-5x       │ Linear step accumulation         │
├──────────────────────────────┼────────────┼──────────────────────────────────┤
│ ReAct (5-10 steps)           │ 10-30x     │ O(n^2) context reinclusion       │
├──────────────────────────────┼────────────┼──────────────────────────────────┤
│ Reflection                   │ 2-3x/iter  │ Self-critique + regeneration     │
├──────────────────────────────┼────────────┼──────────────────────────────────┤
│ ReWOO                        │ 0.5-0.7x   │ Planning separated from          │
│                              │ of ReAct   │ execution (30-50% savings)       │
├──────────────────────────────┼────────────┼──────────────────────────────────┤
│ Tree-of-Thoughts             │ 10-100x    │ Branch exploration               │
│                              │ vs. CoT    │                                  │
├──────────────────────────────┼────────────┼──────────────────────────────────┤
│ Multi-agent pipeline         │ 3-100x     │ Bilateral token charges +        │
│                              │            │ coordination messages             │
├──────────────────────────────┼────────────┼──────────────────────────────────┤
│ Multi-agent debate           │ 20x        │ Bilateral charges + full context │
│                              │ vs. ReAct  │ exchange per debate round        │
└──────────────────────────────┴────────────┴──────────────────────────────────┘
```

### 3.3 Cost Formula: $ per 1,000 Runs

For a ReAct agent with `n` average steps, input context size `C_0` tokens, average tool output `T` tokens, and model pricing `P_in` / `P_out` per million tokens:

```
Total input tokens per run:
  I(n) = sum(k=1..n) [ C_0 + k * T ]
       = n * C_0 + T * n(n+1)/2
       ~ O(n^2 * T)  when T dominates

Total output tokens per run:
  O(n) ~ n * O_avg    (linear in steps)

Cost per run:
  $ = I(n) * P_in/1M + O(n) * P_out/1M

Cost per 1,000 runs:
  $1k = 1000 * $
```

**Worked example** (Claude Sonnet 4, $3/$15 per M in/out tokens):

```
Parameters: n=8 steps, C_0=2000 tokens, T=500 tokens/tool, O_avg=300 tokens

Input:  I(8) = 8*2000 + 500*8*9/2 = 16,000 + 18,000 = 34,000 tokens
Output: O(8) = 8*300 = 2,400 tokens
Cost:   $ = 34,000 * 3/1M + 2,400 * 15/1M = $0.102 + $0.036 = $0.138/run
Per 1k: $138

With prompt caching (78.5% reduction on input):
  $cached = 34,000 * 0.215 * 3/1M + $0.036 = $0.022 + $0.036 = $0.058/run
  Per 1k: $58 (58% savings)
```

### 3.4 Latency Profiles and SLA Targets

```
┌──────────────────────────┬────────┬────────┬────────┬─────────────────────┐
│ Architecture             │ p50    │ p95    │ p99    │ Notes               │
├──────────────────────────┼────────┼────────┼────────┼─────────────────────┤
│ Single LLM call          │ 1.5s   │ 3s     │ 5s     │ Baseline            │
├──────────────────────────┼────────┼────────┼────────┼─────────────────────┤
│ Prompt chaining (3 step) │ 5s     │ 10s    │ 15s    │ Linear growth       │
├──────────────────────────┼────────┼────────┼────────┼─────────────────────┤
│ Routing + handler        │ 3s     │ 7s     │ 12s    │ Router adds ~1s     │
├──────────────────────────┼────────┼────────┼────────┼─────────────────────┤
│ Parallelization (3-way)  │ 3s     │ 6s     │ 10s    │ Wall-clock = max    │
│                          │        │        │        │ branch              │
├──────────────────────────┼────────┼────────┼────────┼─────────────────────┤
│ ReAct (5-10 steps)       │ 20s    │ 45s    │ 60s    │ Tool latency addtv  │
├──────────────────────────┼────────┼────────┼────────┼─────────────────────┤
│ Plan-and-Execute         │ 25s    │ 50s    │ 75s    │ 10-30s planning     │
│                          │        │        │        │ upfront             │
├──────────────────────────┼────────┼────────┼────────┼─────────────────────┤
│ Multi-agent (2-agent)    │ 60s    │ 180s   │ 250s   │ Observed: 251.6s    │
│                          │        │        │        │ per case vs 12.4s   │
│                          │        │        │        │ for solo model      │
└──────────────────────────┴────────┴────────┴────────┴─────────────────────┘
```

### 3.5 Context Accumulation: The O(n^2) Problem

ReAct's quadratic scaling explained: each step re-reads the entire message history plus all prior tool outputs. A 10-step workflow does NOT cost 10x a single step -- it costs the triangular number sum(1..10) = 55 units of context.

**Mitigation strategies (40-70% token reduction without quality loss)**:

1. **Tool output truncation**: Return only top-k results or summaries rather than full outputs. Compress verbose tool responses before appending to context.
2. **Sliding window**: After 10+ steps, drop the oldest messages from context while retaining a compressed summary of early steps.
3. **Embedding cache**: Cache embeddings of completed task segments; retrieve only when relevant to current step rather than carrying everything.
4. **Prompt caching**: Claude achieved 78.5% cost reduction from prompt caching across 500+ agentic sessions by caching the stable prefix (system prompt + tool definitions).
5. **Plan caching**: Store successful tool execution plans and replay for similar recurring tasks. 20-35% cost reduction.
6. **Speculative execution**: Pre-fetch likely tool results in parallel based on planning output. 1.4-2.1x throughput improvement on tool-heavy workloads.

### 3.6 Capacity Planning Formula

```
Required throughput (runs/hour):
  R = peak_concurrent_users * avg_runs_per_user_per_hour

Token throughput (tokens/second):
  T = R * avg_tokens_per_run / 3600

API rate limit headroom:
  Rate_limit_needed = T * 1.3   (30% headroom for bursts)

Monthly cost projection:
  Monthly_$ = R * 720 * cost_per_run   (720 hours/month)
  + 20% buffer for retries and reflection loops
```

### 3.7 NFR Summary

```
┌─────────────────────┬───────────────────────────────────────────────────────┐
│ NFR                 │ Target / Constraint                                   │
├─────────────────────┼───────────────────────────────────────────────────────┤
│ Cost per run        │ Budget ceiling per run; alert at 80% of ceiling      │
├─────────────────────┼───────────────────────────────────────────────────────┤
│ Latency (user-      │ L2 workflows: p95 < 15s                             │
│ facing)             │ L3 agents: p95 < 60s                                │
│                     │ L4 multi-agent: p95 < 300s (set user expectations)   │
├─────────────────────┼───────────────────────────────────────────────────────┤
│ Step ceiling        │ Hard limit per run (LangGraph default: 25)           │
├─────────────────────┼───────────────────────────────────────────────────────┤
│ Token budget        │ Hard cap on total tokens per run; kill on breach     │
├─────────────────────┼───────────────────────────────────────────────────────┤
│ Availability        │ Checkpoint-resume must survive process restarts      │
│                     │ without re-executing completed side effects          │
├─────────────────────┼───────────────────────────────────────────────────────┤
│ Observability       │ Per-step token count, latency, reasoning trace,     │
│                     │ tool success rate -- all queryable within 30s        │
├─────────────────────┼───────────────────────────────────────────────────────┤
│ Security            │ Zero agent with admin-class permissions during       │
│                     │ general task execution                               │
├─────────────────────┼───────────────────────────────────────────────────────┤
│ Audit               │ Every tool invocation logged with agent ID, scoped  │
│                     │ permissions, reasoning trace, policy decision        │
└─────────────────────┴───────────────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Failure Taxonomy

Berkeley and Stanford's MAST taxonomy (March 2025) analyzed 1,642 agent execution traces across 7 multi-agent frameworks and found failure rates of 41% to 86.7%. Seven major failure modes:

```
┌──────────────────────────┬───────────┬──────────────────────────────────────┐
│ Failure Mode             │ Frequency │ Detection / Mitigation              │
├──────────────────────────┼───────────┼──────────────────────────────────────┤
│ Tool misuse              │ High      │ Schema validation on args before    │
│ (wrong tool, wrong args, │           │ execution. Log tool selection       │
│ misinterpreted results)  │           │ reasoning for post-hoc analysis.    │
├──────────────────────────┼───────────┼──────────────────────────────────────┤
│ Context drift /          │ High      │ Periodic re-grounding: inject       │
│ hallucination cascades   │           │ original task statement every N     │
│ (step N error corrupts   │           │ steps. Fact-check tool outputs      │
│ all subsequent steps)    │           │ against source before proceeding.   │
├──────────────────────────┼───────────┼──────────────────────────────────────┤
│ Goal drift               │ Medium    │ Re-inject original objective at     │
│ (orchestrator loses      │           │ each planning cycle. Compare        │
│ sight of original        │           │ current trajectory against goal.    │
│ objective)               │           │                                     │
├──────────────────────────┼───────────┼──────────────────────────────────────┤
│ Infinite loops /         │ 15.7% of │ Hard step ceiling. No-progress      │
│ step repetition          │ failures │ detection (same tool+args repeated).│
│ (same call with tiny     │           │ Diminishing returns check (<5%     │
│ param variations)        │           │ improvement over 3 iterations).    │
├──────────────────────────┼───────────┼──────────────────────────────────────┤
│ Silent quality           │ Hard to   │ Output quality metrics (not just   │
│ degradation              │ detect    │ success/failure). A/B comparison   │
│ (no crash, just worse    │           │ against baseline. Human spot-check │
│ results)                 │           │ sampling.                          │
├──────────────────────────┼───────────┼──────────────────────────────────────┤
│ Prompt injection         │ Security- │ Input sanitization. Guardrail      │
│ (adversarial inputs      │ critical  │ agents running in parallel.        │
│ hijack agent behavior)   │           │ Output validation before action.   │
├──────────────────────────┼───────────┼──────────────────────────────────────┤
│ Cascading multi-agent    │ Cata-     │ System-level circuit breakers.     │
│ failures                 │ strophic  │ Agent quarantine on anomalous      │
│ (one error propagates;   │           │ behavior. Independent validation   │
│ 87% downstream poisoning │           │ of cross-agent inputs.            │
│ in 4 hours -- Galileo)   │           │                                   │
└──────────────────────────┴───────────┴──────────────────────────────────────┘
```

### 4.2 Durable Execution Patterns

**Checkpointing** is the single most impactful resilience pattern. Without it, every agent failure means starting from scratch.

```
Framework Comparison:
┌─────────────────┬─────────────────────────────┬──────────────────────────┐
│ Framework       │ Checkpoint Mechanism        │ Production Readiness     │
├─────────────────┼─────────────────────────────┼──────────────────────────┤
│ LangGraph       │ Automatic at every node     │ GA at v1.0 (Oct 2025).  │
│                 │ transition. SqliteSaver     │ Most mature.             │
│                 │ (dev), PostgresSaver (prod).│                          │
│                 │ Resume from last checkpoint │                          │
│                 │ on crash.                   │                          │
├─────────────────┼─────────────────────────────┼──────────────────────────┤
│ Temporal        │ Workflow history replay     │ Production-grade but     │
│                 │ from immutable event log.   │ heavyweight. Better for  │
│                 │ Activity-level retries.     │ discrete steps than      │
│                 │ Deterministic replay.       │ open-ended reasoning.    │
├─────────────────┼─────────────────────────────┼──────────────────────────┤
│ Microsoft Agent │ Built-in checkpointing +   │ v1.0 (Apr 2026).        │
│ Framework (MAF) │ persistent state in Azure  │ Enterprise-grade.        │
│                 │ AI Foundry.                │                          │
└─────────────────┴─────────────────────────────┴──────────────────────────┘
```

**The checkpointing + idempotency invariant**: Both are required. Any side-effecting tool must carry an idempotency key so resumed execution can verify whether the action already happened before re-issuing. Without idempotency, a checkpointed agent that resumes after a crash may duplicate a payment, send a duplicate email, or create a duplicate record.

### 4.3 Permission Boundaries

Databricks Opsin Labs (2025) found 60% of enterprise AI agents are over-permissioned. The 2026 CISO AI Risk Report (235 large-enterprise security leaders): 92% lack full visibility into AI identities, 86% do not enforce access policies for AI identities.

**Tiered approval model** (Gartner warns against binary "fully locked down or fully trusted"):

```
┌────────────────────┬──────────────────────┬──────────────────────────────┐
│ Impact Level       │ Governance Model     │ Example                      │
├────────────────────┼──────────────────────┼──────────────────────────────┤
│ Read-only /        │ Fully autonomous     │ Search knowledge base,       │
│ low-risk           │                      │ summarize documents           │
├────────────────────┼──────────────────────┼──────────────────────────────┤
│ Medium-risk writes │ Async approval with  │ Send customer email, update  │
│                    │ timeout              │ records                       │
├────────────────────┼──────────────────────┼──────────────────────────────┤
│ High-risk /        │ Synchronous human    │ Financial transactions, data │
│ irreversible       │ gate                 │ deletion, production deploy  │
├────────────────────┼──────────────────────┼──────────────────────────────┤
│ Policy changes     │ Separate human-      │ Change agent permissions,    │
│                    │ initiated workflow   │ modify guardrails            │
└────────────────────┴──────────────────────┴──────────────────────────────┘
```

**Approval quality requirements**: Approvals must arrive before the action, can realistically be refused, capture what the approver saw, and come from a person (never a shared inbox or rubber-stamp automation). Approvals degrade under time pressure and approval fatigue, creating a false sense of safety.

### 4.4 Human-in-the-Loop Implementation

The critical constraint: no live process should be held while a human decides. The pattern is checkpoint-wait-resume.

```
Agent execution:
  ... step N ...
       │
       ▼
  ┌──────────────┐     ┌──────────────────────────┐
  │ CHECKPOINT   │────▶│ Persist full state to     │
  │ (before      │     │ external store (Postgres, │
  │  human wait) │     │ Redis, Temporal event log)│
  └──────┬───────┘     └──────────────────────────┘
         │
         ▼
  ┌──────────────┐     Process terminates. No resources held.
  │ AWAIT HUMAN  │     Notification sent to approver.
  │ DECISION     │     Could be minutes, hours, or days.
  └──────┬───────┘
         │ decision arrives (webhook, queue message, API call)
         ▼
  ┌──────────────┐     ┌──────────────────────────┐
  │ RESUME FROM  │◀────│ Load state from external  │
  │ CHECKPOINT   │     │ store. Verify idempotency │
  └──────┬───────┘     │ keys before re-executing. │
         │             └──────────────────────────┘
         ▼
  ... step N+1 ...
```

### 4.5 Audit Trail Requirements

Traditional audit logs record WHAT was accessed. Agent governance requires capturing WHY and what decisions resulted. Required structured fields per agent action:

1. **Unique agent identifier** -- which agent instance acted
2. **Delegated permissions at time of action** -- the scoped permission set, not the maximum possible
3. **Specific tool or API invoked** -- tool name, arguments, response summary
4. **Governance policy decision** -- allowed / denied / escalated, with the policy rule that matched
5. **Reasoning trace** -- the reasoning step the agent generated before acting. This is the difference between knowing an agent deleted a file and understanding WHY it believed that was correct. Regulators will expect reconstruction of what agents did, why, and with whose authorization.

### 4.6 Regulatory Context

- **EU AI Act**: Enforcement powers active Aug 2, 2026. Fines up to 35M EUR or 7% of global annual revenue.
- **ISO/IEC 42001**: First AI Management System standard. Enterprise procurement increasingly requires certification.
- **NIST NCCoE** (Feb 2026): Concept paper applying OAuth 2.0, Zero Trust (SP 800-207), and Digital Identity Guidelines (SP 800-63-4) to agent scenarios.

### 4.7 Multi-Agent Security

Galileo AI (Dec 2025): A single compromised agent poisoned 87% of downstream decision-making within 4 hours -- faster than typical incident response cycles. This demonstrates that individual agent governance is insufficient. System-level defenses required:

- Cross-agent input validation (do not trust outputs from other agents without independent verification)
- Agent quarantine on anomalous behavior patterns
- System-level circuit breakers that halt all inter-agent communication when anomaly thresholds trip
- Cryptographically verifiable identity per agent (Zero Trust for AI)

---

## 5. Production Enterprise Code

### 5.1 Exponential Backoff with Jitter and Per-Tool Retry Policy

```python
"""
Production retry logic for agentic tool execution.
Per-tool retry policies with exponential backoff, jitter, and budget tracking.
"""

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

logger = logging.getLogger("agent.tools")


@dataclass
class RetryPolicy:
    """Per-tool retry configuration. Different tools have different
    failure modes and recovery characteristics."""

    max_retries: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 60.0
    jitter_factor: float = 0.5  # 0 = no jitter, 1 = full jitter
    retryable_exceptions: tuple = (TimeoutError, ConnectionError)


# Production configuration: each tool gets its own policy
DEFAULT_RETRY_POLICIES: dict[str, RetryPolicy] = {
    "web_search": RetryPolicy(
        max_retries=3, base_delay_s=2.0, max_delay_s=30.0,
        retryable_exceptions=(TimeoutError, ConnectionError),
    ),
    "database_query": RetryPolicy(
        max_retries=2, base_delay_s=0.5, max_delay_s=10.0,
        retryable_exceptions=(TimeoutError, ConnectionError),
    ),
    "send_email": RetryPolicy(
        max_retries=1, base_delay_s=5.0, max_delay_s=5.0,
        retryable_exceptions=(ConnectionError,),  # narrow: no retry on app errors
    ),
    "code_execute": RetryPolicy(
        max_retries=0,  # never retry code execution -- side effects unknown
    ),
}


async def execute_with_retry(
    tool_name: str,
    tool_fn: Callable,
    args: dict[str, Any],
    idempotency_key: str,
    policies: dict[str, RetryPolicy] | None = None,
) -> Any:
    """Execute a tool call with per-tool retry policy, exponential backoff,
    and decorrelated jitter (AWS-style).

    The idempotency_key is passed through to the tool function for
    checkpoint-resume safety. The caller is responsible for generating
    a deterministic key from (tool_name, args, step_index).
    """
    policy = (policies or DEFAULT_RETRY_POLICIES).get(
        tool_name, RetryPolicy()  # safe default
    )

    last_exception = None
    for attempt in range(1 + policy.max_retries):
        try:
            start = time.monotonic()
            result = await tool_fn(
                **args, _idempotency_key=idempotency_key
            )
            elapsed_ms = (time.monotonic() - start) * 1000

            logger.info(
                "tool_call_success",
                extra={
                    "tool": tool_name,
                    "attempt": attempt + 1,
                    "latency_ms": round(elapsed_ms, 1),
                    "idempotency_key": idempotency_key,
                },
            )
            return result

        except policy.retryable_exceptions as exc:
            last_exception = exc
            if attempt == policy.max_retries:
                logger.error(
                    "tool_call_exhausted_retries",
                    extra={
                        "tool": tool_name,
                        "attempts": attempt + 1,
                        "error": str(exc),
                        "idempotency_key": idempotency_key,
                    },
                )
                raise

            # Decorrelated jitter: delay = random between base and last_delay * 3
            # Bounded by max_delay. Avoids thundering herd better than equal jitter.
            delay = min(
                policy.max_delay_s,
                policy.base_delay_s * (2 ** attempt)
            )
            jitter = delay * policy.jitter_factor * random.random()
            actual_delay = delay - (policy.jitter_factor * delay / 2) + jitter

            logger.warning(
                "tool_call_retry",
                extra={
                    "tool": tool_name,
                    "attempt": attempt + 1,
                    "next_attempt_in_s": round(actual_delay, 2),
                    "error": str(exc),
                    "idempotency_key": idempotency_key,
                },
            )
            await asyncio.sleep(actual_delay)

    raise last_exception  # unreachable but satisfies type checker


def make_idempotency_key(tool_name: str, args: dict, step_index: int) -> str:
    """Deterministic idempotency key from tool call parameters.
    Same inputs at the same step always produce the same key,
    enabling checkpoint-resume without re-executing side effects."""
    payload = json.dumps(
        {"tool": tool_name, "args": args, "step": step_index},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
```

### 5.2 Circuit Breaker with State Machine

```python
"""
Per-backend circuit breaker preventing agent fleets from
self-inflicted DDoS when downstream services fail.

State machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED (or back to OPEN)
"""

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from threading import Lock
from typing import Any, Callable


class CircuitState(Enum):
    CLOSED = auto()     # normal operation, requests pass through
    OPEN = auto()       # backend down, all requests fail-fast
    HALF_OPEN = auto()  # testing recovery, allow one probe request


@dataclass
class CircuitBreaker:
    """Per-backend (not per-tool) circuit breaker. Multiple tools sharing
    one backend API share one breaker instance."""

    backend_name: str
    failure_threshold: int = 5          # consecutive failures before OPEN
    recovery_timeout_s: float = 30.0    # seconds before OPEN -> HALF_OPEN
    half_open_max_calls: int = 1        # probe calls allowed in HALF_OPEN

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if (
                self._state == CircuitState.OPEN
                and time.monotonic() - self._last_failure_time
                    >= self.recovery_timeout_s
            ):
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
            return self._state

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # Recovery confirmed
                self._state = CircuitState.CLOSED
            self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed, back to OPEN
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        state = self.state  # triggers OPEN -> HALF_OPEN transition check
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            with self._lock:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
            return False
        return False  # OPEN


class CircuitOpenError(Exception):
    """Raised when circuit breaker is OPEN -- fail fast, do not attempt."""

    def __init__(self, backend: str, retry_after_s: float):
        self.backend = backend
        self.retry_after_s = retry_after_s
        super().__init__(
            f"Circuit OPEN for {backend}; retry after {retry_after_s:.0f}s"
        )


# Global registry: one breaker per backend, shared across all agent instances
_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = Lock()


def get_breaker(backend_name: str, **kwargs) -> CircuitBreaker:
    """Get or create a circuit breaker for a backend."""
    with _registry_lock:
        if backend_name not in _breakers:
            _breakers[backend_name] = CircuitBreaker(
                backend_name=backend_name, **kwargs
            )
        return _breakers[backend_name]


async def call_with_breaker(
    backend_name: str,
    tool_fn: Callable,
    args: dict[str, Any],
) -> Any:
    """Execute a tool call through its backend's circuit breaker."""
    breaker = get_breaker(backend_name)

    if not breaker.allow_request():
        remaining = breaker.recovery_timeout_s - (
            time.monotonic() - breaker._last_failure_time
        )
        raise CircuitOpenError(backend_name, max(0, remaining))

    try:
        result = await tool_fn(**args)
        breaker.record_success()
        return result
    except Exception as exc:
        breaker.record_failure()
        raise
```

### 5.3 Fallback Chain with Graceful Degradation

```python
"""
Graceful degradation: when a tool is unavailable, fall back to
a less capable alternative rather than failing entirely.

The chain is ordered from most capable to least capable.
Each fallback level documents what capability is lost.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger("agent.fallback")


@dataclass
class FallbackLevel:
    name: str
    tool_fn: Callable
    capability_loss: str  # document what the user loses at this level


class FallbackChain:
    """Ordered chain of tool implementations from best to worst.
    Each level specifies what capability is lost relative to the primary.

    Example chain for search:
      1. Live web search (full capability)
      2. Cached search results (stale by hours/days)
      3. Parametric knowledge (stale by months, no citations)
    """

    def __init__(self, tool_name: str, levels: list[FallbackLevel]):
        if not levels:
            raise ValueError("Fallback chain must have at least one level")
        self.tool_name = tool_name
        self.levels = levels

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """Try each level in order. Return the first successful result
        annotated with the degradation level used."""
        errors: list[tuple[str, str]] = []

        for i, level in enumerate(self.levels):
            try:
                result = await level.tool_fn(**args)

                if i > 0:
                    logger.warning(
                        "fallback_used",
                        extra={
                            "tool": self.tool_name,
                            "level": level.name,
                            "levels_skipped": i,
                            "capability_loss": level.capability_loss,
                        },
                    )

                return {
                    "result": result,
                    "degraded": i > 0,
                    "level_used": level.name,
                    "capability_loss": level.capability_loss if i > 0 else None,
                }

            except Exception as exc:
                errors.append((level.name, str(exc)))
                logger.warning(
                    "fallback_level_failed",
                    extra={
                        "tool": self.tool_name,
                        "level": level.name,
                        "error": str(exc),
                    },
                )

        # All levels exhausted
        logger.error(
            "fallback_chain_exhausted",
            extra={
                "tool": self.tool_name,
                "errors": errors,
            },
        )
        raise RuntimeError(
            f"All {len(self.levels)} fallback levels failed for "
            f"{self.tool_name}: {errors}"
        )


# -- Example instantiation --

async def _live_web_search(query: str, max_results: int = 5) -> list[dict]:
    """Production web search via API."""
    # Real implementation calls search API
    raise NotImplementedError("Replace with actual search API call")


async def _cached_search(query: str, max_results: int = 5) -> list[dict]:
    """Search against locally cached results (updated hourly)."""
    raise NotImplementedError("Replace with cache lookup")


async def _parametric_fallback(query: str, max_results: int = 5) -> list[dict]:
    """Return a marker indicating the LLM should use parametric knowledge."""
    return [{"source": "parametric_knowledge", "note": "No live/cached results available"}]


search_chain = FallbackChain(
    tool_name="web_search",
    levels=[
        FallbackLevel(
            name="live_search",
            tool_fn=_live_web_search,
            capability_loss="none",
        ),
        FallbackLevel(
            name="cached_search",
            tool_fn=_cached_search,
            capability_loss="Results may be stale (up to 24h old). No new content.",
        ),
        FallbackLevel(
            name="parametric_knowledge",
            tool_fn=_parametric_fallback,
            capability_loss="No citations. Knowledge cutoff applies. May hallucinate.",
        ),
    ],
)
```

### 5.4 Structured Logging for Agent Observability

```python
"""
Structured JSON logging for agentic systems.
Captures the five required audit fields per action plus
telemetry needed for cost/latency anomaly detection.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any, Optional

# Context variables for per-agent-run tracing
_agent_id: ContextVar[str] = ContextVar("agent_id", default="unknown")
_run_id: ContextVar[str] = ContextVar("run_id", default="unknown")
_step_index: ContextVar[int] = ContextVar("step_index", default=0)


class AgentJsonFormatter(logging.Formatter):
    """Structured JSON formatter that automatically injects agent context."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "event": record.getMessage(),
            "agent_id": _agent_id.get(),
            "run_id": _run_id.get(),
            "step_index": _step_index.get(),
            "logger": record.name,
        }

        # Merge structured extra fields (tool name, latency, etc.)
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if key not in logging.LogRecord(
                    "", 0, "", 0, "", (), None
                ).__dict__ and key not in log_entry:
                    log_entry[key] = value

        return json.dumps(log_entry, default=str)


def configure_agent_logging(level: int = logging.INFO) -> None:
    """Configure structured JSON logging for agent processes."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(AgentJsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


class AgentRunContext:
    """Context manager for an agent run. Sets context variables for
    structured logging and tracks cumulative token/cost metrics."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.run_id = uuid.uuid4().hex[:12]
        self.start_time = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.step_count = 0
        self.tool_calls = 0
        self.tool_failures = 0
        self._tokens: list[tuple[str, int, int]] = []

    def __enter__(self):
        _agent_id.set(self.agent_id)
        _run_id.set(self.run_id)
        _step_index.set(0)
        self.start_time = time.monotonic()
        logging.getLogger("agent").info(
            "agent_run_started",
            extra={"agent_id": self.agent_id, "run_id": self.run_id},
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_s = time.monotonic() - self.start_time
        logger = logging.getLogger("agent")
        logger.info(
            "agent_run_completed",
            extra={
                "elapsed_s": round(elapsed_s, 2),
                "steps": self.step_count,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "tool_calls": self.tool_calls,
                "tool_failures": self.tool_failures,
                "success": exc_type is None,
                "error": str(exc_val) if exc_val else None,
            },
        )
        return False  # do not suppress exceptions

    def record_step(self, input_tokens: int, output_tokens: int) -> None:
        self.step_count += 1
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        _step_index.set(self.step_count)

    def record_tool_call(self, success: bool) -> None:
        self.tool_calls += 1
        if not success:
            self.tool_failures += 1
```

### 5.5 Agent Loop with Step Ceiling, Token Budget, and Loop Detection

```python
"""
Production agent loop integrating: step ceiling, token budget,
loop detection, circuit breaker, retry, fallback, and structured logging.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.loop")


@dataclass
class AgentConfig:
    max_steps: int = 25          # hard ceiling (LangGraph default)
    max_total_tokens: int = 500_000  # kill switch
    loop_detection_window: int = 3    # consecutive same-tool-args = loop
    diminishing_returns_threshold: float = 0.05  # <5% improvement = stop


@dataclass
class StepRecord:
    tool_name: str
    args_hash: str
    output_summary: str
    input_tokens: int
    output_tokens: int


def _hash_args(args: dict) -> str:
    import json
    return hashlib.md5(
        json.dumps(args, sort_keys=True).encode()
    ).hexdigest()[:8]


def detect_loop(history: list[StepRecord], window: int) -> bool:
    """Detect if the agent is repeating the same tool call."""
    if len(history) < window:
        return False
    recent = history[-window:]
    signatures = [(s.tool_name, s.args_hash) for s in recent]
    return len(set(signatures)) == 1  # all identical


async def run_agent_loop(
    task: str,
    llm_fn,          # async (messages) -> (response, tool_calls, tokens)
    tool_registry,   # maps tool_name -> async callable
    config: AgentConfig = AgentConfig(),
) -> dict[str, Any]:
    """
    Production agent loop with safety controls.

    Returns:
        {
            "answer": str,
            "steps": int,
            "total_input_tokens": int,
            "total_output_tokens": int,
            "terminated_by": "completion" | "step_ceiling" | "token_budget"
                             | "loop_detected" | "diminishing_returns",
        }
    """
    messages = [{"role": "user", "content": task}]
    history: list[StepRecord] = []
    total_in = 0
    total_out = 0

    for step in range(1, config.max_steps + 1):
        # --- Token budget check ---
        if total_in + total_out >= config.max_total_tokens:
            logger.warning(
                "token_budget_exceeded",
                extra={"step": step, "total_tokens": total_in + total_out},
            )
            return {
                "answer": _extract_best_answer(messages),
                "steps": step - 1,
                "total_input_tokens": total_in,
                "total_output_tokens": total_out,
                "terminated_by": "token_budget",
            }

        # --- LLM call ---
        response, tool_calls, in_tok, out_tok = await llm_fn(messages)
        total_in += in_tok
        total_out += out_tok

        # --- No tool calls = agent thinks it is done ---
        if not tool_calls:
            return {
                "answer": response,
                "steps": step,
                "total_input_tokens": total_in,
                "total_output_tokens": total_out,
                "terminated_by": "completion",
            }

        # --- Execute tool calls ---
        for tc in tool_calls:
            args_hash = _hash_args(tc["args"])

            # Execute with retry + circuit breaker (see sections 5.1, 5.2)
            tool_fn = tool_registry[tc["name"]]
            result = await tool_fn(**tc["args"])
            result_summary = str(result)[:200]

            history.append(StepRecord(
                tool_name=tc["name"],
                args_hash=args_hash,
                output_summary=result_summary,
                input_tokens=in_tok,
                output_tokens=out_tok,
            ))

            messages.append({
                "role": "tool",
                "name": tc["name"],
                "content": result_summary,
            })

        # --- Loop detection ---
        if detect_loop(history, config.loop_detection_window):
            logger.warning(
                "loop_detected",
                extra={
                    "step": step,
                    "repeated_tool": history[-1].tool_name,
                    "repeated_args_hash": history[-1].args_hash,
                },
            )
            return {
                "answer": _extract_best_answer(messages),
                "steps": step,
                "total_input_tokens": total_in,
                "total_output_tokens": total_out,
                "terminated_by": "loop_detected",
            }

    # Step ceiling reached
    logger.warning("step_ceiling_reached", extra={"max_steps": config.max_steps})
    return {
        "answer": _extract_best_answer(messages),
        "steps": config.max_steps,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "terminated_by": "step_ceiling",
    }


def _extract_best_answer(messages: list[dict]) -> str:
    """Extract the most recent assistant message as the best available answer."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return "Agent terminated without producing an answer."
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario A: Enterprise Customer Support Platform

**Problem statement**: A SaaS company (500K customers, 50K support tickets/month) needs an AI-powered customer support system that can handle general queries autonomously, process refunds with appropriate approvals, diagnose technical issues, and escalate complex cases to human agents -- while maintaining audit trails, permission boundaries, and cost controls.

**Requirements**: 80% autonomous resolution rate, p95 response latency under 30s for general queries, human gate on refunds over $500, full audit trail per interaction, cost under $0.50 per ticket average.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CUSTOMER INPUT                                    │
│                    (chat widget, email, API)                                 │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                     PARALLEL EXECUTION LAYER                                │
│                                                                             │
│  ┌────────────────────────┐         ┌────────────────────────────────────┐  │
│  │ GUARDRAIL AGENT        │         │ ROUTER (Intent Classifier)         │  │
│  │ (runs concurrently)    │         │                                    │  │
│  │                        │  async  │  Model: small/fast (Claude Haiku)  │  │
│  │ - PII detection +      │◀──────▶│  Categories:                       │  │
│  │   redaction            │         │    general / refund / technical /  │  │
│  │ - Content safety       │         │    escalation                      │  │
│  │ - Scope enforcement    │         │                                    │  │
│  │ - Prompt injection     │         │  Router accuracy = system quality  │  │
│  │   detection            │         │  ceiling. Invest here first.       │  │
│  │                        │         │                                    │  │
│  │ Tripwire: halt on      │         └──────────┬─────────────────────────┘  │
│  │ violation              │                    │                             │
│  └────────────────────────┘                    │                             │
└────────────────────────────────────────────────┼─────────────────────────────┘
                                                 │
              ┌──────────────┬───────────────────┼────────────────┐
              │              │                   │                │
              ▼              ▼                   ▼                ▼
┌─────────────────┐ ┌────────────────┐ ┌─────────────────┐ ┌───────────┐
│ GENERAL          │ │ REFUND          │ │ TECHNICAL        │ │ ESCALATION│
│                  │ │                 │ │                  │ │           │
│ Pattern: ReAct   │ │ Pattern: Prompt │ │ Pattern:         │ │ Handoff   │
│ agent with KB    │ │ Chaining with   │ │ Orchestrator-    │ │ to human  │
│ tools            │ │ human gate      │ │ Workers          │ │ with full │
│                  │ │                 │ │                  │ │ context   │
│ Tools:           │ │ Steps:          │ │ Orchestrator:    │ │ package   │
│ - search_kb      │ │ 1. Verify order │ │  diagnose issue  │ │           │
│ - search_docs    │ │    (gate: valid?)│ │  -> delegate     │ │           │
│ - get_account    │ │ 2. Check policy │ │                  │ │           │
│                  │ │    (gate: elig?) │ │ Workers:         │ │           │
│ Model: Sonnet    │ │ 3. Process      │ │ - search_docs    │ │           │
│ (balanced)       │ │    (gate: amt   │ │ - check_logs     │ │           │
│                  │ │    < $500? auto │ │ - run_diagnostic │ │           │
│ Step ceiling: 10 │ │    else human)  │ │ - gen_solution   │ │           │
│                  │ │                 │ │                  │ │           │
│ Fallback:        │ │ Model: Sonnet   │ │ Model: Sonnet    │ │           │
│ cached_kb ->     │ │ Step ceiling: 5 │ │ Step ceiling: 15 │ │           │
│ parametric       │ │                 │ │                  │ │           │
└────────┬────────┘ └────────┬────────┘ └────────┬────────┘ └─────┬─────┘
         │                   │                   │                │
         └───────────────────┴───────────────────┴────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────────┐
│                       EVALUATOR (Response Quality)                          │
│                                                                             │
│  Check before delivery: factual accuracy, tone, completeness, PII-free.    │
│  If quality score < threshold, route to human review.                      │
│  Model: Haiku (fast, cheap evaluation).                                    │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────────┐
│  PERSISTENCE: PostgresSaver checkpoints | Idempotency registry             │
│  TELEMETRY: Per-ticket cost, resolution time, route accuracy, CSAT         │
│  AUDIT: Agent ID + permissions + tool calls + reasoning trace + policy     │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
┌──────────────────┬──────────────────────────┬──────────────────────────────┐
│ Dimension        │ Chosen Approach          │ Alternative Considered       │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ Routing model    │ Dedicated small/fast     │ Single large model handles   │
│                  │ classifier (Haiku).      │ routing + response.          │
│                  │ PRO: Cost-optimal, fast. │ PRO: Simpler. CON: Cannot   │
│                  │ CON: Router accuracy     │ route to cheap model for     │
│                  │ limits system quality.   │ simple queries; 3-5x more   │
│                  │                          │ expensive per ticket.        │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ Guardrail        │ Parallel agent (separate │ Inline in each handler's    │
│ placement        │ LLM call).               │ system prompt.              │
│                  │ PRO: "Tends to perform   │ PRO: Fewer LLM calls.      │
│                  │ better" (Anthropic).     │ CON: One prompt doing two   │
│                  │ CON: Adds ~$0.01/ticket  │ jobs = both done worse.     │
│                  │ from extra LLM call.     │                             │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ Refund pattern   │ Prompt chaining with     │ ReAct agent with tools.     │
│                  │ validation gates.        │ PRO: More flexible.         │
│                  │ PRO: Predictable, every  │ CON: Cannot guarantee       │
│                  │ step validated. Human    │ human gate placement. More  │
│                  │ gate at precise point.   │ expensive. Harder to audit. │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ Technical diag.  │ Orchestrator-Workers     │ ReAct agent with all tools. │
│ pattern          │ (dynamic decomposition). │ PRO: Simpler architecture.  │
│                  │ PRO: Can adapt to novel  │ CON: Context accumulation   │
│                  │ issue types at runtime.  │ hits ceiling on complex     │
│                  │ CON: Goal drift risk.    │ multi-system diagnoses.     │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ Cost control     │ Router tiering: Haiku    │ Single model for all.       │
│                  │ for classify/evaluate,   │ PRO: Simpler. CON: 5x      │
│                  │ Sonnet for generate.     │ more expensive; general     │
│                  │ PRO: ~60% cost reduction │ queries do not need Sonnet. │
│                  │ on simple queries.       │                             │
└──────────────────┴──────────────────────────┴──────────────────────────────┘
```

**Decision rationale**: The routing pattern was chosen over a single-agent approach because support tickets have categorically different processing requirements. Refunds need deterministic steps with a human gate at a precise point -- impossible to guarantee with a ReAct agent. General queries need flexible tool use. Technical diagnosis needs dynamic decomposition. The parallel guardrail architecture adds ~$0.01/ticket but catches prompt injection and PII leakage that the main agent would miss when focused on task completion.

Projected cost per ticket: ~$0.12 average (60% general at $0.05, 20% refund at $0.08, 15% technical at $0.25, 5% escalation at $0.02 for context packaging).

---

### 6.2 Scenario B: Autonomous Code Review Pipeline

**Problem statement**: An engineering organization (200 developers, 150 PRs/day) needs an AI code review system that runs automatically on every pull request, catches bugs and security issues, suggests improvements, and integrates with the existing CI/CD pipeline -- while avoiding false positives that would erode developer trust and managing costs at scale.

**Requirements**: p95 review latency under 120s, false positive rate below 15% (developers will ignore the system above this threshold), cost under $0.80 per PR average, support for monorepo with 500K+ LOC, human reviewer remains the final approver.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PR WEBHOOK (GitHub / GitLab)                      │
│                    Event: pull_request.opened / synchronize                 │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                     CHANGE ANALYSIS (Prompt Chaining)                       │
│                                                                             │
│  Step 1: Diff Parser                                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ Extract changed files, hunks, surrounding context (3 lines above/   │   │
│  │ below). Classify: new_file / modified / deleted / renamed.          │   │
│  │ Deterministic code (no LLM). Cost: $0.                              │   │
│  └──────────────────────────────┬───────────────────────────────────────┘   │
│                                 │                                           │
│  Step 2: Change Scoping         │                                           │
│  ┌──────────────────────────────▼───────────────────────────────────────┐   │
│  │ LLM (Haiku) classifies each file change:                            │   │
│  │   trivial (rename, format, comment) -> skip review                  │   │
│  │   standard (logic change) -> standard review                        │   │
│  │   critical (auth, payments, data deletion) -> deep review           │   │
│  │ Gate: If all changes trivial, auto-approve with "LGTM" comment.    │   │
│  └──────────────────────────────┬───────────────────────────────────────┘   │
└─────────────────────────────────┼───────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                   PARALLEL REVIEW (Sectioning + Voting)                     │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ CORRECTNESS       │  │ SECURITY          │  │ STYLE / BEST PRACTICE    │  │
│  │ REVIEWER          │  │ REVIEWER          │  │ REVIEWER                 │  │
│  │                   │  │                   │  │                          │  │
│  │ Per-file ReAct    │  │ Per-file ReAct    │  │ Per-file single call     │  │
│  │ agent with tools: │  │ agent with tools: │  │ (no tools needed)        │  │
│  │ - read_file       │  │ - read_file       │  │                          │  │
│  │ - search_codebase │  │ - search_codebase │  │ Model: Haiku             │  │
│  │ - read_tests      │  │ - check_deps      │  │ (fast, cheap)            │  │
│  │ - check_types     │  │ - owasp_patterns  │  │                          │  │
│  │                   │  │                   │  │ Only for standard files; │  │
│  │ Model: Sonnet     │  │ Model: Sonnet     │  │ skipped for critical     │  │
│  │ Step ceiling: 8   │  │ Step ceiling: 8   │  │ (time better spent on   │  │
│  │                   │  │                   │  │ correctness/security)    │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────┬───────────────┘  │
│           │                     │                        │                   │
│  For critical files: each finding is independently voted on by a second    │
│  review pass (Voting pattern). Finding posted only if 2/2 reviewers agree. │
│  This cuts false positives at the cost of 2x tokens on critical files.     │
└───────────┼─────────────────────┼────────────────────────┼───────────────────┘
            │                     │                        │
┌───────────▼─────────────────────▼────────────────────────▼───────────────────┐
│                    SYNTHESIS (Evaluator-Optimizer)                            │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ Consolidate findings across all reviewers. Remove duplicates.       │    │
│  │ Rank by severity: blocking / warning / suggestion.                  │    │
│  │ Generate per-finding inline comments with:                          │    │
│  │   - What is wrong (specific, not vague)                             │    │
│  │   - Why it matters (security impact, correctness risk)              │    │
│  │   - Suggested fix (concrete code, not hand-wavy)                    │    │
│  │                                                                      │    │
│  │ Self-evaluation pass: "Would a senior engineer find this helpful    │    │
│  │ or annoying?" Remove low-confidence findings below threshold.       │    │
│  │                                                                      │    │
│  │ Model: Sonnet. Step ceiling: 3 (generate + evaluate + refine).     │    │
│  └──────────────────────────────┬───────────────────────────────────────┘    │
└─────────────────────────────────┼────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────────────┐
│                    OUTPUT (GitHub PR Comments)                                │
│                                                                              │
│  - Inline comments on specific lines (not a wall-of-text summary)           │
│  - Summary comment with finding count by severity                            │
│  - "Blocking" findings request changes; "suggestion" is informational       │
│  - Human reviewer remains final approver (NEVER auto-merge)                 │
│                                                                              │
│  PERSISTENCE: Review results cached by (repo, file_path, content_hash)     │
│  TELEMETRY: Cost/PR, latency/PR, false positive rate (developer dismissals)│
│  FEEDBACK: Developer dismiss/accept actions train the confidence threshold  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
┌──────────────────┬──────────────────────────┬──────────────────────────────┐
│ Dimension        │ Chosen Approach          │ Alternative Considered       │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ Review scope     │ Change scoping filter    │ Review all files equally.    │
│                  │ (trivial/standard/       │ PRO: No missed context.     │
│                  │ critical).               │ CON: 3-5x more expensive;   │
│                  │ PRO: 40-60% cost saving  │ comment-only and rename     │
│                  │ by skipping trivial.     │ changes waste reviewer      │
│                  │ CON: Misclassified       │ tokens and add noise.       │
│                  │ trivial file = missed    │                             │
│                  │ bug.                     │                             │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ False positive   │ Voting on critical file  │ Single-pass review.         │
│ control          │ findings (2/2 must       │ PRO: Half the token cost on │
│                  │ agree). Self-eval pass   │ critical files. CON: Higher │
│                  │ removes low-confidence.  │ false positive rate; devs   │
│                  │ PRO: FP rate < 15%.      │ start ignoring findings.    │
│                  │ CON: 2x tokens on        │ Trust erosion is            │
│                  │ critical files.          │ irreversible.               │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ Parallelism      │ Sectioning by review     │ Sequential review by        │
│ strategy         │ type (correctness /      │ single agent.               │
│                  │ security / style).       │ PRO: Simpler, cheaper.      │
│                  │ PRO: Wall-clock time =   │ CON: p95 latency 3x higher  │
│                  │ max(branch), not sum.    │ (sequential). One agent     │
│                  │ Independent failure.     │ context-switching between   │
│                  │ CON: 3x LLM calls.      │ security and style = worse  │
│                  │                          │ at both.                    │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ Model tiering    │ Haiku for scoping +      │ Sonnet for everything.      │
│                  │ style. Sonnet for        │ PRO: Highest quality.       │
│                  │ correctness + security.  │ CON: 5x cost increase.     │
│                  │ PRO: ~55% cost reduction │ Style review does not need  │
│                  │ vs. Sonnet everywhere.   │ Sonnet-level reasoning.     │
├──────────────────┼──────────────────────────┼──────────────────────────────┤
│ Output strategy  │ Inline comments on       │ Single summary comment.     │
│                  │ specific lines.          │ PRO: Cheaper (one output).  │
│                  │ PRO: Actionable; devs    │ CON: Devs must manually     │
│                  │ see feedback in context. │ locate the relevant code.   │
│                  │ CON: More output tokens. │ Lower adoption.             │
└──────────────────┴──────────────────────────┴──────────────────────────────┘
```

**Decision rationale**: The parallel sectioning approach was chosen because correctness, security, and style reviews are genuinely independent concerns with different tool needs and failure modes. A security reviewer that misses a vulnerability should not prevent style suggestions from being posted. The voting pattern for critical files doubles cost on ~15% of files but is essential for keeping false positive rates below the 15% trust threshold -- developer trust, once lost, is extremely difficult to regain. The change-scoping filter (trivial/standard/critical) is the highest-leverage cost optimization: it eliminates 40-60% of token spend on changes that do not benefit from AI review.

Projected cost per PR: ~$0.45 average (assuming 60% of files are trivial-skipped, 30% standard, 10% critical with double review). At 150 PRs/day, monthly cost is approximately $2,025.

> **Gap**: The voting pattern for false-positive reduction on critical files is well-established in content moderation but has limited published benchmarks for code review specifically. Production teams should measure their own FP rates and adjust the confidence threshold empirically using developer accept/dismiss signals as the training signal.
