# Module 08: Agentic Patterns

### What Is This?

An agentic pattern is any system where an LLM decides the next action rather than following a hardcoded script. The fundamental control question is: who picks the next step -- your code or the model? If your code picks (e.g., a fixed pipeline of classify -> extract -> format), that is a workflow. If the model picks (e.g., deciding whether to search, compute, or respond based on observations), that is an agent. Agentic systems trade determinism for adaptability -- they handle messy, open-ended tasks that cannot be fully scripted in advance, but they cost 4-15x more tokens, fail in novel ways (infinite loops, goal drift, tool misuse), and require new infrastructure for state management, human approval, and observability. The cardinal rule: do not use agents where a workflow suffices, and do not use workflows where a direct LLM call suffices.

---

## 1. System Topology & Data Flow

### 1.1 The Escalation Ladder (L1-L4)

Every LLM system sits on an escalation ladder. Start at L1 and only climb when evaluation data proves the simpler level fails.

| Level | Pattern | Who Decides Next Step | When to Use | Token Multiple |
|---|---|---|---|---|
| **L1** | Direct LLM call | Code (one call) | Classification, extraction, reformatting | 1x |
| **L2** | Workflow (chain, route, parallel) | Code (fixed graph) | Multi-step with known structure | 1.5-3x |
| **L3** | Agent (ReAct, plan-execute) | Model (dynamic loop) | Open-ended, unknown steps | 4-8x |
| **L4** | Multi-agent | Multiple models (delegation) | Cross-domain, parallel expertise | 10-20x |

**The 70-80% trap:** When an L1/L2 system scores 70-80%, the instinct is to escalate to L3. The correct action is to fix prompts, add validation gates, and improve tool descriptions first. Architecture escalation should be the last resort, not the first. Anthropic's internal experience: they found that 50 subagents on simple queries produced worse results than a single well-prompted agent with effort scaling (think harder, not wider).

### 1.2 Five-Layer Architecture

```
+--------------------------------------------------------------------------+
|                     CONTROL LAYER (Orchestration)                          |
|  Pattern router: classify task -> select execution pattern                |
|  Step ceiling per run (hard limit, not suggestion)                        |
|  Token budget per run (hard ceiling with margin)                          |
|  Decision log: every step logged with reasoning trace                     |
|  Escalation/fallback: agent -> workflow -> deterministic                  |
+--------+-----------------------+----------------------------+------------+
         |                       |                            |
         v                       v                            v
+--------------------------------------------------------------------------+
|                     REASONING LAYER (LLM Turns)                            |
|  Prompt assembly (Module 07 compilation pipeline)                          |
|  Tool-use decision: model emits structured tool_call or final_answer       |
|  Observation integration: tool results enter as tool_result messages       |
|  Loop detection: hash recent (action, observation) pairs                  |
|  Memory management: sliding window + summarization when context grows      |
+--------+-----------------------+----------------------------+------------+
         |                       |                            |
         v                       v                            v
+--------------------------------------------------------------------------+
|                     TOOL EXECUTION LAYER                                   |
|  MCP servers (filesystem, database, APIs) + A2A protocol (agent-to-agent) |
|  Per-tool retry policies (idempotent: retry; non-idempotent: check-first) |
|  Per-tool timeouts and circuit breakers                                    |
|  RBAC enforcement: least-privilege per tool per agent                     |
|  Tool output truncation (prevent context explosion from verbose tools)     |
+--------+-----------------------+----------------------------+------------+
         |                       |                            |
         v                       v                            v
+--------------------------------------------------------------------------+
|                     PERSISTENCE LAYER                                      |
|  Checkpointing: serialize full agent state after each step                |
|  Thread state: conversation history, plan cache, working memory            |
|  Idempotency keys: prevent duplicate side effects on replay               |
|  Durable execution: Temporal / LangGraph checkpointer / MAF               |
+--------+-----------------------+----------------------------+------------+
         |                       |                            |
         v                       v                            v
+--------------------------------------------------------------------------+
|                     TELEMETRY LAYER                                        |
|  Per-step traces: (step_n, action, tool, observation, tokens, latency)    |
|  Aggregate metrics: steps/run, tokens/run, $/run, success rate            |
|  Loop/drift alerts: consecutive identical actions, rising step count       |
|  Audit trail: agent_id, delegated_permissions, tool_invoked, policy,      |
|               reasoning_trace (5 structured fields)                       |
+--------------------------------------------------------------------------+
```

### 1.3 The Augmented LLM

Before any pattern, the base building block is the **augmented LLM**: a model with retrieval (RAG), tools (MCP), and memory (short-term context + long-term store). This is not an agent -- it is the "enhanced function call" that every agent and workflow is built from.

```
                    +-------------------+
  Retrieval (RAG) <-|                   |-> Tool Use (MCP)
                    |  Augmented LLM    |
     Memory Store <-|                   |-> Structured Output
                    +-------------------+
```

The **MCP + A2A protocol stack** standardizes how augmented LLMs connect to tools (MCP: model-to-tool) and to each other (A2A: agent-to-agent). MCP provides tool discovery, schema negotiation, and invocation; A2A provides agent capability cards, task delegation, and result return.

---

## 2. Core Mechanics & Algorithms

### 2.1 Workflow Patterns (Code Controls Next Step)

These are deterministic pipelines where your code decides the execution order. Prefer these whenever the task structure is known in advance.

#### 2.1.1 Prompt Chaining (Sequential with Gates)

```
Step A -> [GATE: validate output] -> Step B -> [GATE] -> Step C

Gate types:
- Schema validation (Pydantic/Zod)
- Quality threshold (confidence score, regex match)
- Human approval (for high-stakes steps)
- Policy check (budget, permissions, safety)

If gate fails: retry step (max N), escalate to human, or abort chain.
```

**Use when:** Task decomposes into fixed, known steps (e.g., translate -> summarize -> format). Each step is a focused prompt that is easier to optimize than one monolithic prompt. Accuracy compounding: p^n (see Module 07).

#### 2.1.2 Routing (Classifier Dispatch)

```
                    +-> Handler A (simple query -> Haiku)
Input -> Classifier +-> Handler B (complex query -> Sonnet)
                    +-> Handler C (code task -> specialized model)
                    +-> Fallback (unknown -> human queue)
```

**Ceiling:** Route accuracy <= classifier accuracy. If the classifier is 90% accurate, 10% of requests go to the wrong handler. Use deterministic rules (keyword, regex, metadata) before LLM classifiers. **Cost lever:** Routing simple queries to small models saves 60-80% while preserving quality on complex queries.

#### 2.1.3 Parallelization (Sectioning & Voting)

**Sectioning:** Split task into independent subtasks, execute in parallel, merge results. N+0 extra LLM calls (no orchestrator overhead). Use for: document section analysis, multi-aspect evaluation, independent data extraction.

**Voting:** Same task to N models/prompts, majority vote on result. N-1 extra calls. Self-Consistency is the prompt-level version. Use for: high-stakes classification where accuracy justifies cost.

#### 2.1.4 Orchestrator-Workers (Dynamic Decomposition)

```
Task -> Orchestrator (LLM) -> [Subtask 1, Subtask 2, ..., Subtask N]
                                    |           |              |
                                 Worker 1    Worker 2      Worker N
                                    |           |              |
                               Orchestrator merges results -> Output
```

**Call cost:** N+1 LLM calls minimum (1 decomposition + N workers). The orchestrator decides subtask count dynamically based on the input -- unlike prompt chaining where N is fixed. Use when: subtask count or nature varies per input (e.g., code refactoring where file count varies).

#### 2.1.5 Evaluator-Optimizer (Iterative Refinement)

```
Draft -> Evaluator (LLM) -> [feedback] -> Optimizer (LLM) -> Revised Draft
  ^                                                              |
  +---------- repeat until eval passes or max rounds -----------+
```

**Call cost:** 2 LLM calls per round. Converges in 2-3 rounds for most tasks. Use for: literary translation, code review, content that benefits from critique-then-revise.

### 2.2 Agent Patterns (Model Controls Next Step)

The model decides what to do next based on observations. More flexible than workflows, but more expensive, harder to debug, and prone to novel failure modes.

#### 2.2.1 ReAct (Reason + Act)

The foundational agent loop. Model alternates between thinking (reasoning) and acting (tool use), incorporating observations at each step.

```
                   +---> Thought (reasoning about what to do)
                   |         |
                   |         v
                   |     Action (tool call or final_answer)
                   |         |
                   |         v
                   +---- Observation (tool result enters context)
                   |
                   +---> [Loop until final_answer or step ceiling]

State machine:
  IDLE -> THINKING -> ACTING -> OBSERVING -> THINKING -> ... -> DONE | FAILED

Complexity: O(n^2) context growth -- every step adds tokens, and each
subsequent step must process all prior steps. At step 8 with 500 tokens/step,
the model processes 4,000 tokens of history on each reasoning turn.
```

**Published benchmarks:**
- ALFWorld: **71%** (ReAct) vs **45%** (Act-only) -- reasoning before acting nearly doubles success
- WebShop: **40%** vs **30.1%**
- HotpotQA: significant improvement with interleaved reasoning

**The context accumulation problem:** ReAct context grows O(n^2) per request. At 8 steps, the model re-reads all prior thoughts and observations before generating the next thought. This is the dominant cost driver and the reason agents cost 4-8x a direct call.

#### 2.2.2 ReWOO (Reasoning Without Observation)

Plans all tool calls upfront, executes them (potentially in parallel), then synthesizes results in one pass. Eliminates repeated context processing.

```
Plan (all steps at once) -> Execute tools (parallel where possible) -> Synthesize

Complexity: O(n) -- plan once, execute once, synthesize once.
No iterative context accumulation.
```

**vs ReAct:** 64% fewer tokens, +4.4% accuracy on benchmarks. But cannot adapt mid-execution -- if step 3's result changes what step 5 should do, ReWOO cannot course-correct.

#### 2.2.3 Reflexion (Episodic Memory + Self-Improvement)

Agent runs, evaluates its own performance, stores failure analysis in episodic memory, and retries with that memory. Turns failed attempts into learning signal.

```
Attempt -> Evaluate (self or external) -> Reflect (analyze failure)
  ^                                           |
  |                                    Store reflection in
  |                                    episodic memory
  |                                           |
  +------ Retry with reflection context ------+
```

**Published lifts:**
- AlfWorld: **+22 percentage points** (from ~50% to 72%)
- HotPotQA: **+20pp**
- HumanEval: **91%** vs GPT-4 baseline **80%**
- WebShop: No improvement (task does not benefit from self-reflection)

**Cost:** Multiplicative -- each retry includes all prior reflections. Typically 2-3 retries. Diminishing returns after 3.

#### 2.2.4 Plan-and-Execute

Separates planning from execution. A planner model generates a high-level plan, then a separate executor handles each step.

```
Task -> Planner (generates ordered step list)
           |
           v
        Step 1 -> Executor -> Result 1
        Step 2 -> Executor -> Result 2 (can use Result 1)
        ...
        Step N -> Executor -> Result N
           |
           v
        Synthesizer (optional) -> Final Answer

Complexity: O(p) for planning + O(n) for execution = O(p + n)
vs ReAct O(n^2). Significant latency reduction for complex tasks.
```

**PS+ vs Zero-shot-CoT:** MultiArith **91.8%**, GSM8K **59.3%** (Plan-and-Solve+).

#### 2.2.5 LLMCompiler (DAG Parallelism)

Plans a DAG of tool calls, identifies independent branches, executes them in parallel. Combines planning efficiency of ReWOO with dependency-aware parallelism.

```
Task -> Planner -> DAG (nodes = tool calls, edges = dependencies)
                      |
                 Scheduler -> Parallel execution of independent branches
                      |
                 Joiner -> Synthesize results

Example DAG:
  [Search A] ----+
                  +---> [Compare A vs B] ---> [Format Report]
  [Search B] ----+
```

**LLMCompiler vs ReAct (published benchmarks):**

| Benchmark | Latency Improvement | Cost Improvement |
|---|---|---|
| HotpotQA | 1.80x faster | 3.37x cheaper |
| Movie Recommendation | 3.74x faster | 6.73x cheaper |

#### 2.2.6 Tree-of-Thoughts (ToT)

Branch-and-bound exploration of multiple reasoning paths. Each node is a partial solution; the model evaluates which branches to expand. Useful for combinatorial problems where greedy (CoT) fails.

```
Root Problem
    +-> Branch A (evaluated: 0.7)
    |     +-> A1 (0.8) <- expand
    |     +-> A2 (0.3) <- prune
    +-> Branch B (evaluated: 0.9) <- expand first
    |     +-> B1 (0.6)
    |     +-> B2 (0.95) <- expand
    +-> Branch C (evaluated: 0.2) <- prune

Complexity: O(b^d) where b = branching factor, d = depth.
Extremely expensive. Use only when other patterns fail.
```

**Published:** Game of 24: **74%** (ToT) vs **4%** (CoT). But 47x token cost.

### 2.3 Multi-Agent Topologies

| Topology | Structure | Best For | Risk |
|---|---|---|---|
| **Supervisor** | Central coordinator delegates to specialists | Heterogeneous tasks, clear subtask boundaries | Single point of failure, bottleneck |
| **Sequential Pipeline** | Agent A -> Agent B -> Agent C (fixed order) | Linear processing (draft -> review -> publish) | No parallelism, single failure blocks chain |
| **Decentralized Handoff** | Agents hand off to each other based on expertise | Customer support with specialist routing | Circular handoffs, lost context |
| **Debate/Voting** | Multiple agents argue/vote on answer | High-stakes decisions, fact-checking | N*cost, diminishing returns after 3-5 agents |
| **Magentic (Outer/Inner Loop)** | Outer loop = high-level plan, inner loop = execution | Complex research, multi-document synthesis | Nested context explosion |

**Google's multi-agent finding (2025):** Multi-agent architectures **dropped performance 39-70%** compared to single-agent on their benchmarks. The added coordination overhead and context loss between agents outweighed any specialization benefit. Use multi-agent only when agents truly need different tool sets or different model capabilities.

### 2.4 Pattern Selection Decision Tree

```
Can a single LLM call solve this?
  YES -> L1: Direct call. Stop.
  NO  -> Is the task structure known in advance?
    YES -> L2: Workflow. Pick sub-pattern:
      - Fixed steps? -> Prompt Chaining (with gates)
      - Input determines handler? -> Routing
      - Independent subtasks? -> Parallelization (sectioning)
      - Variable subtask count? -> Orchestrator-Workers
      - Quality requires iteration? -> Evaluator-Optimizer
    NO  -> Does the task require adapting based on intermediate results?
      YES -> L3: Agent. Pick sub-pattern:
        - Simple tool use, <5 steps? -> ReAct
        - All tool calls plannable upfront? -> ReWOO (64% fewer tokens)
        - Complex multi-step, parallelizable? -> LLMCompiler
        - Benefits from self-critique? -> Reflexion
        - Combinatorial search space? -> Tree-of-Thoughts
      NO  -> L1 or L2 with better prompting (the 70-80% trap)

Does it require multiple distinct expertise domains?
  YES -> L4: Multi-agent (but verify single-agent first; Google: -39-70%)
  NO  -> Stay at L3 with better tools
```

### 2.5 Five Key Invariants

1. **Step ceiling:** Every agent run MUST have a hard maximum step count. Without it, a confused agent loops forever. LangGraph default `recursion_limit` is 25; recommend `2 * max_iterations + 1` for ReAct (accounts for both reasoning and tool-call nodes).
2. **Token budget:** Hard ceiling on total tokens consumed per run. Includes all reasoning, tool results, and retries. Monitor and abort when exceeded.
3. **Loop detection:** Hash recent (action, observation) pairs. If the same pair repeats 2-3 times, the agent is stuck. Exit with partial result or escalate.
4. **Idempotency:** Every tool call must be idempotent or check-before-act. On replay after checkpoint recovery, non-idempotent tools (payment, email) without idempotency keys cause duplicate side effects.
5. **Fallback chain:** Agent -> workflow -> deterministic. When the agent fails (step ceiling, budget exhaustion, loop), fall back to a simpler execution strategy -- not just an error message.

---

## 3. Token Economics & NFR Analysis

### 3.1 Token Amplification

Agents consume dramatically more tokens than direct calls. Every reasoning step re-reads the full context history.

| Pattern | Token Multiple vs Direct Call | LLM Calls vs CoT |
|---|---|---|
| Direct call (L1) | 1x | 1x |
| Workflow (L2) | 1.5-3x | 2-5x |
| Agent (L3) | 4-8x | ~9.2x |
| Multi-agent (L4) | 10-20x | ~15x+ |

**KV cache memory:** Agent patterns require 3.0-5.4x KV cache memory vs CoT due to growing context windows across steps.

**LATS (Language Agent Tree Search):** 71.0 LLM calls per request -- the extreme end of token amplification.

### 3.2 Context Growth: The O(n^2) Problem

In ReAct, each step adds tokens to the context, and each subsequent step must process all prior tokens. After n steps with s tokens per step:

```
Total tokens processed = s + 2s + 3s + ... + ns = s * n(n+1)/2 = O(n^2 * s)

Example: 8 steps, 500 tokens/step:
- Step 1: 500 tokens
- Step 2: 1,000 tokens
- Step 8: 4,000 tokens
- Total processed: 500 * 8 * 9 / 2 = 18,000 tokens
- vs 8 independent calls: 8 * 500 = 4,000 tokens
- Overhead: 4.5x due to context accumulation alone
```

### 3.3 Context Mitigation Strategies (40-70% Reduction)

| Strategy | Reduction | Mechanism |
|---|---|---|
| **Tool output truncation** | 40-50% | Truncate verbose tool outputs to essential fields before injecting into context |
| **Sliding window** | 30-40% | Keep only last K steps in context; summarize older steps |
| **Embedding cache** | 20-30% | Store tool results in vector DB; retrieve only relevant past observations |
| **Prefix caching** | 78.5% prefill reduction | System prompt + tool definitions cached across steps. ReAct: **5.62x throughput**, **15.7% E2E latency reduction** |
| **Plan caching** | 20-35% | Cache and reuse plans for similar tasks (Plan-and-Execute, LLMCompiler) |
| **Speculative execution** | 1.4-2.1x throughput | Predict likely next tool calls; pre-execute in background |

### 3.4 Cost Formulas

**Single agent (ReAct, 8 steps):**

```
Assumptions: Claude Sonnet 5, $2/MTok input, $10/MTok output
System prefix: 2K tokens (cached after step 1)
Per-step observation: 500 tokens
Per-step reasoning output: 200 tokens

Uncached:
  Total input tokens = 2K + sum(500*i for i=1..8) = 2K + 18K = 20K
  Total output tokens = 200 * 8 = 1.6K
  Cost = 20K/1M * $2 + 1.6K/1M * $10 = $0.040 + $0.016 = $0.056/run
  Per 1k runs: $56

With prefix caching (system prompt cached, f=0.9):
  Cached reads: 2K * 8 * 0.9 * $0.20/MTok = $0.0029
  Uncached: 18K/1M * $2 + 1.6K/1M * $10 = $0.036 + $0.016 = $0.052
  Total: ~$0.055/run -> $55/1k (modest saving because system prefix is small)

Aggressive caching (stable prefix = 10K with tools/examples):
  Cost drops to ~$0.038/run -> $38/1k (32% reduction)

Full cost formula:
  C_agent = sum_i=1..n [(P_sys * cache_rate + P_hist_i) * input_price + O_i * output_price]
  where P_hist_i = sum_j=1..i (thought_j + observation_j)
```

**Multi-agent (3 agents, 5 steps each):**

```
Per agent: ~$0.056/run
Orchestrator: 3 delegation calls + 1 synthesis = 4 calls * ~$0.015 = $0.060
Total: 3 * $0.056 + $0.060 = $0.228/run -> $228/1k

With prefix caching + tool truncation: ~$0.145/run -> $145/1k (36% reduction)
```

**Published cost benchmarks:**

| Pattern | $/1k Runs (Uncached) | $/1k Runs (Cached) |
|---|---|---|
| Direct call (L1) | $15-22 | $10-18 |
| Workflow (L2) | $30-65 | $20-45 |
| Agent (L3, 8-step ReAct) | $78-138 | $38-58 |
| Multi-agent (L4) | $228-500 | $145-292 |

### 3.5 Latency Analysis

**Latency composition (published infrastructure study):**
- LLM inference: **69.4%** of E2E latency
- Tool execution: **30.2%** of E2E latency
- Orchestration overhead: **0.4%** of E2E latency
- Overlap between LLM and tool: only **18.2%** (most execution is sequential)

**Published E2E latencies:**

| Pattern | p50 | p95 | p99 |
|---|---|---|---|
| Direct call (L1) | 0.8s | 2.0s | 4.0s |
| Workflow 3-step (L2) | 2.5s | 6.0s | 12.0s |
| ReAct 5-step (L3) | 4.0s | 12.0s | 25.0s |
| Multi-agent (L4) | 15.0s | 45.0s | 120.0s |

| Benchmark | ReAct Latency | LLMCompiler Latency | Improvement |
|---|---|---|---|
| HotpotQA | 7.12s | 3.95s | 1.80x |
| Movie Rec | - | - | 3.74x |

**Anthropic Research system:** Multi-agent research system consumed 4x tokens of single agent; parallel execution gave 90% speedup. Multi-agent: ~251.6s per complex case vs 12.4s for solo agent (tasks warranting multi-agent are inherently harder/larger).

### 3.6 NFR Targets

| NFR | L2 Workflow | L3 Agent | L4 Multi-Agent |
|---|---|---|---|
| **Availability** | 99.9% | 99.5% (tool failures) | 99.0% (coordination failures) |
| **Max latency (p99)** | 15s | 30s | 120s |
| **Max steps** | Fixed (known) | 10-15 (hard ceiling) | 25-50 across all agents |
| **Token budget** | Predictable | 2x average (burst) | 3-5x average (burst) |
| **Success rate** | >95% | >80% | >70% (Google: 30-61%) |

---

## 4. Distributed Resilience & Security

### 4.1 Checkpointing and Durable Execution

Agent state must survive process crashes, timeouts, and human-in-the-loop pauses. Without checkpointing, a 10-minute agent run lost at step 9 must restart from scratch.

**Checkpointing modes:**

| Mode | Durability | Latency Overhead | Use When |
|---|---|---|---|
| **Sync** | Full (every step persisted) | +5-15ms/step | Side-effecting tools, human approval |
| **Async** | Near-full (buffered writes) | +1-3ms/step | Read-only tools, internal reasoning |
| **Exit** | Partial (save only on completion/failure) | ~0ms/step | Short chains, idempotent operations |

**Framework comparison:**

| Framework | Checkpointing | State Management | Best For |
|---|---|---|---|
| **LangGraph** | Most mature (GA v1.0, Oct 2025). PostgreSQL/SQLite/Redis backends. Built-in `interrupt()` for HITL. Thread-level state. | Graph-based state with typed channels | Complex agents, production workloads |
| **OpenAI Agents SDK** | Minimal, explicit. Developer manages persistence. | Handoff-based, explicit state passing | Simple agents, OpenAI-native stacks |
| **CrewAI** | Basic. Process-level. | Role-based agent definitions | Rapid prototyping (5.76x faster setup, but 54% vs 62% success rate) |
| **MAF (Microsoft)** | Azure Durable Functions integration. Event-driven. | Enterprise Azure integration | Azure-native, enterprise governance |
| **Temporal** | Production-grade workflow engine. Activity-level retry policies. | Workflow-as-code, replay-safe | High-reliability, long-running workflows |

**Invariant:** Checkpointing + idempotency -- both are required. Checkpointing without idempotency keys means replayed steps re-execute non-idempotent tools (double-charge a payment). Idempotency without checkpointing means you lose all state on crash and cannot resume.

### 4.2 Permission Boundaries

**The over-permissioning problem:** **60%** of agents are over-permissioned (Databricks Opsin Labs). **92%** of organizations lack visibility into AI agent identities (Oracle Cloud Security, 2026).

**Tiered approval model:**

| Tool Category | Approval | Examples |
|---|---|---|
| **Read-only** | Automatic (no human) | Search, database queries, file reads |
| **Write (reversible)** | Async approval (queue, review within SLA) | Draft email, create ticket, update record |
| **Irreversible** | Sync approval (human must confirm before execution) | Send email, process payment, delete data |
| **Policy change** | Human-only (agent cannot execute) | Change permissions, modify security rules, alter configs |

**Implementation:** Never hold a live process waiting for human approval. Use checkpoint-wait-resume: save agent state, emit approval request, terminate process, resume from checkpoint when approval arrives. This prevents resource leaks during potentially hours-long human review.

### 4.3 Human-in-the-Loop (HITL) Checkpoint Pattern

```
Agent reaches approval-required step
  |
  v
Serialize full state to durable store (checkpoint)
  |
  v
Emit approval request (Slack, email, dashboard)
  |
  v
Terminate agent process (no resources held)
  |
  v
... human reviews (minutes to hours) ...
  |
  v
Approval webhook triggers
  |
  v
Load checkpoint, resume agent from exact step
  |
  v
Execute approved action, continue loop
```

LangGraph `interrupt()` implements this natively: `interrupt({"question": "Approve payment of $5,000?"})` serializes state, returns control to the application, and resumes when `Command(resume=True)` is called.

### 4.4 Audit Trail Requirements

Every agent step must emit **5 structured fields** for compliance and debugging:

| Field | Content | Why |
|---|---|---|
| **agent_id** | Unique identifier for this agent instance | Trace actions to specific agent |
| **delegated_permissions** | What this agent is allowed to do (scope list) | Verify least-privilege |
| **tool_invoked** | Tool name, arguments, return value (redacted PII) | Reproduce decisions |
| **policy_decision** | approve/deny/escalate + policy rule that fired | Explainability for regulators |
| **reasoning_trace** | Model's reasoning for this step (condensed) | Debug, compliance review |

### 4.5 Multi-Agent Security

**Threat:** **87%** of downstream agents can be poisoned within 4 hours of a single compromised upstream agent (Galileo AI research). A malicious instruction in Agent A's output propagates through Agent B's context and Agent C's actions.

**Defenses:**
- Treat inter-agent messages as untrusted user input (data plane, not control plane)
- Each agent validates inputs against its own schema before processing
- Output sanitization: strip any instruction-like patterns from tool/agent results
- Network segmentation: agents cannot directly access each other's tools
- Supervisor agents validate worker outputs before passing downstream

### 4.6 Regulatory Landscape

| Regulation | Deadline | Agent Requirements | Penalty |
|---|---|---|---|
| **EU AI Act** | August 2026 | Full audit trails for high-risk AI, human oversight, transparency | 35M EUR or 7% global revenue |
| **ISO 42001** | Ongoing | AI management system standard, risk assessment, monitoring | Certification loss |
| **NIST Zero Trust** | Guidance | Least privilege, continuous verification, assume breach | N/A (framework) |

### 4.7 Failure Taxonomy

**MAST taxonomy (1,642 agent traces analyzed):** Overall failure rates: **41-86.7%** depending on task complexity and framework.

| # | Failure Mode | Frequency | Detection | Mitigation |
|---|---|---|---|---|
| 1 | **Tool misuse** | 25-35% | Wrong tool selected, wrong parameters | Better tool descriptions, strict schemas, poka-yoke args |
| 2 | **Context drift** | 15-20% | Agent forgets original goal mid-execution | Explicit goal re-injection every N steps |
| 3 | **Goal drift** | 10-15% | Agent optimizes for subtask, loses sight of main objective | Evaluator checks against original goal, step summaries |
| 4 | **Infinite loops** | **15.7%** | Repeated identical (action, observation) pairs | Loop detection (hash pairs), hard step ceiling |
| 5 | **Silent quality degradation** | 20-30% | Output is valid but subtly wrong | LLM-as-Judge on final output, eval benchmarks, sampling |
| 6 | **Prompt injection via tools** | 5-10% | Malicious content in tool results hijacks agent | Treat tool output as data, not instructions; sanitize |
| 7 | **Cascading multi-agent failures** | 10-15% | One agent's error propagates through others | Inter-agent validation, circuit breakers per agent, supervisor review |

**Enterprise cost reality:** **73%** of AI agent projects exceeded their budgets (various 2025-2026 surveys). Gartner projects **40%** of agent projects will be cancelled by 2027 due to cost overruns and reliability issues.

---

## 5. Code Examples

### 5.1 ReAct Agent Loop with Safety Controls (Production Pattern)

```python
import hashlib, time, json
from dataclasses import dataclass, field

@dataclass
class AgentState:
    """Serializable agent state for checkpointing."""
    goal: str
    steps: list = field(default_factory=list)
    total_tokens: int = 0
    step_count: int = 0

def run_agent(goal: str, llm, tools: dict, *,
              max_steps: int = 10,
              token_budget: int = 50_000) -> dict:
    """ReAct loop with step ceiling, token budget, and loop detection.

    Key safety controls:
    1. Hard step ceiling (not a suggestion -- process terminates)
    2. Token budget (abort before runaway cost)
    3. Loop detection (hash recent action-observation pairs)
    4. Fallback to partial result on any safety trigger
    """
    state = AgentState(goal=goal)
    seen_hashes: list[str] = []  # recent (action, obs) hashes

    for step in range(max_steps):
        # Budget check BEFORE each LLM call
        if state.total_tokens >= token_budget:
            return {"status": "budget_exceeded", "partial": state.steps}

        # Build context: system + goal + history (O(n^2) growth here)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Goal: {goal}"},
        ] + [s["messages"] for s in state.steps]  # flatten history

        response = llm.call(messages)
        state.total_tokens += response.usage.total_tokens
        state.step_count += 1

        # Check if model wants to use a tool or give final answer
        if response.tool_call:
            tool_name = response.tool_call.name
            tool_args = response.tool_call.arguments

            # RBAC check: is this tool allowed?
            if tool_name not in tools:
                state.steps.append({"error": f"Tool {tool_name} not permitted"})
                continue

            # Execute with per-tool timeout
            result = tools[tool_name].execute(tool_args, timeout=30)

            # Truncate verbose tool output to prevent context explosion
            result_text = json.dumps(result)[:2000]

            # Loop detection: hash (action, observation)
            pair_hash = hashlib.md5(
                f"{tool_name}:{tool_args}:{result_text}".encode()
            ).hexdigest()[:12]
            if pair_hash in seen_hashes[-3:]:  # same pair in last 3 steps
                return {"status": "loop_detected", "partial": state.steps}
            seen_hashes.append(pair_hash)

            state.steps.append({
                "thought": response.reasoning,
                "action": tool_name,
                "observation": result_text,
            })
        else:
            # Final answer
            return {"status": "success", "answer": response.content,
                    "steps": state.step_count, "tokens": state.total_tokens}

    return {"status": "max_steps_reached", "partial": state.steps}
```

### 5.2 Circuit Breaker for Tool Execution

```python
import threading, time
from enum import Enum

class BreakerState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing recovery

class CircuitBreaker:
    """Thread-safe circuit breaker for tool/API calls.

    Prevents cascading failures: after `threshold` consecutive failures,
    stops calling the failing service for `recovery_timeout` seconds.
    """
    def __init__(self, name: str, threshold: int = 5, recovery_timeout: float = 60.0):
        self.name = name
        self.threshold = threshold
        self.recovery_timeout = recovery_timeout
        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    def call(self, func, *args, **kwargs):
        with self._lock:
            if self._state == BreakerState.OPEN:
                if time.time() - self._last_failure_time > self.recovery_timeout:
                    self._state = BreakerState.HALF_OPEN  # Try one call
                else:
                    raise CircuitOpenError(f"{self.name} circuit open")

        try:
            result = func(*args, **kwargs)
            with self._lock:
                self._failure_count = 0
                self._state = BreakerState.CLOSED
            return result
        except Exception as e:
            with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.time()
                if self._failure_count >= self.threshold:
                    self._state = BreakerState.OPEN
            raise
```

### 5.3 Fallback Chain (Agent -> Workflow -> Deterministic)

```python
def execute_with_fallback(task: str, llm, tools: dict) -> dict:
    """Three-tier fallback: agent -> workflow -> deterministic.

    Documents capability loss at each tier so callers know
    what quality/completeness to expect from the result.
    """
    # Tier 1: Full agent (most capable, most expensive)
    try:
        result = run_agent(task, llm, tools, max_steps=10)
        if result["status"] == "success":
            return {**result, "tier": "agent", "capability_loss": "none"}
    except Exception:
        pass  # Fall through to workflow

    # Tier 2: Fixed workflow (less flexible, more reliable)
    try:
        result = run_workflow_chain(task, llm)  # deterministic 3-step chain
        return {**result, "tier": "workflow",
                "capability_loss": "no dynamic tool use, fixed steps"}
    except Exception:
        pass  # Fall through to deterministic

    # Tier 3: Deterministic (no LLM, always works)
    return {
        "status": "degraded",
        "answer": "Unable to process with AI. Routing to human queue.",
        "tier": "deterministic",
        "capability_loss": "no AI processing, human intervention required",
    }
```

---

## 6. System Design Scenarios

### Scenario 1: Customer Support Platform (B2B SaaS)

**Problem:** B2B SaaS with 500K customers, 50K support tickets/month. Current state: L1 (direct LLM call) for auto-responses achieves 60% resolution rate. Goal: 85%+ resolution rate, <$0.15/ticket average, 99.5% availability, SOC 2 compliance.

**Architecture:**

```
INTAKE:
  Ticket -> Classifier (L1, Haiku-class, <200ms)
    |
    +-> Simple FAQ (40% of tickets) -> L1 direct RAG response
    |     Cost: ~$0.003/ticket. No agent needed.
    |
    +-> Account-specific (35%) -> L2 Workflow (chain: lookup -> draft -> validate)
    |     3 fixed steps, tools: CRM read, KB search, template fill.
    |     Cost: ~$0.025/ticket. Gates between steps.
    |
    +-> Complex/multi-system (20%) -> L3 ReAct Agent (max 8 steps)
    |     Tools: CRM read/write, billing API, KB search, Jira create.
    |     HITL: write operations require async approval.
    |     Cost: ~$0.12/ticket. Circuit breaker per tool.
    |
    +-> Escalation (5%) -> Human queue (agent provides summary + suggested actions)
          Cost: ~$0.005 for summary generation.

SAFETY:
  Per-ticket token budget: 10K (simple), 30K (workflow), 80K (agent)
  Step ceiling: N/A (L1), 3 (L2), 8 (L3)
  Loop detection: hash-based, 2 repeats -> escalate to human
  Checkpoint: sync mode for L3 (write tools)
  Audit: 5-field structured log per step
  RBAC: read-only auto, write async-approve, delete human-only
```

**Trade-off matrix:**

| Decision | Chosen | Why | Trade-off |
|---|---|---|---|
| Pattern mix | L1/L2/L3 hybrid | 75% of tickets don't need agents; saves 80% vs all-agent | More complex routing logic |
| Agent framework | LangGraph | Most mature checkpointing, HITL support | Vendor lock-in, learning curve |
| Approval model | Tiered (read auto, write async, delete human) | Prevents over-permissioning (60% industry default) | Slower resolution for write-heavy tickets |
| Fallback | Agent -> workflow -> human queue | 99.5% availability requires graceful degradation | Human cost on fallback |

**Cost projection:**

```
Simple (40%):   20K * $0.003  = $60/month
Workflow (35%): 17.5K * $0.025 = $437.50/month
Agent (20%):    10K * $0.12   = $1,200/month
Escalation (5%): 2.5K * $0.005 = $12.50/month

Total: $1,710/month = $0.034/ticket average (well under $0.15 target)
Resolution rate: 40% * 95% + 35% * 85% + 20% * 70% = 38% + 29.75% + 14% = 81.75%
+ human escalation handling -> 85%+ achievable
```

### Scenario 2: Autonomous Code Review Pipeline

**Problem:** Engineering org with 200 developers, 150 PRs/day. Need automated first-pass code review covering: security vulnerabilities, style compliance, test coverage gaps, performance anti-patterns. Current human reviewers take 2-4 hours average. Target: <15 min automated first-pass, <5% false positive rate on blocking issues, $0.50/PR budget.

**Architecture:**

```
PR WEBHOOK -> File Diff Extraction -> Classification
  |
  v
PARALLEL SECTIONING (L2 - all run simultaneously):
  +-> Security Scanner Agent (tools: SAST results, CVE DB, dependency audit)
  +-> Style Checker (L1 - direct call against style guide, no tools needed)
  +-> Test Coverage Analyzer (tools: coverage report parser, test file scanner)
  +-> Performance Reviewer (tools: profiler output, benchmark DB)
  |
  v
AGGREGATOR (L1 - merge all findings, deduplicate):
  Collect findings from all 4 reviewers
  Deduplicate overlapping findings
  |
  v
VOTING FOR FALSE-POSITIVE CONTROL (L2 - parallel voting):
  Each blocking finding reviewed by 2 additional LLM calls
  Majority vote (2/3) required to mark as blocking
  This reduces FP rate from ~15% (single reviewer) to <5%
  |
  v
OUTPUT:
  PR comment with findings (blocking / advisory / informational)
  Blocking findings require human reviewer to acknowledge
  Dashboard: per-reviewer accuracy, FP rate, cost/PR
```

**Trade-off matrix:**

| Decision | Chosen | Why | Trade-off |
|---|---|---|---|
| Parallelization | Sectioning (4 parallel reviewers) | Independent aspects; 4x faster than sequential; no inter-dependency | Higher peak token usage |
| FP control | Voting (2/3 majority for blocking) | Reduces FP from 15% to <5% (critical for developer trust) | 2x cost on flagged findings |
| Security reviewer | L3 Agent (ReAct, max 5 steps) | Needs dynamic tool use (SAST, CVE lookup) | Higher cost per review |
| Other reviewers | L1-L2 (direct/workflow) | Static analysis, no dynamic tool needs | Less flexible |
| Framework | LangGraph (parallel nodes) | Native parallel execution, checkpoint for security agent | Team must learn LangGraph |

**Cost projection:**

```
Per PR (average):
  Security agent (L3): 1 * $0.08 = $0.08
  Style checker (L1): 1 * $0.015 = $0.015
  Test analyzer (L1): 1 * $0.015 = $0.015
  Perf reviewer (L1): 1 * $0.015 = $0.015
  Aggregator (L1): 1 * $0.01 = $0.01
  Voting (avg 3 findings * 2 votes): 6 * $0.015 = $0.09
  Subtotal: $0.225/PR (well under $0.50 budget)

Monthly: 150 * 22 * $0.225 = $742.50/month
vs 150 * 22 * 3h * $75/hr (engineer cost) = $742,500/month
ROI: 1000x cost reduction on first-pass review time
```

**Latency:** All 4 reviewers run in parallel. Slowest (security agent, 5 steps): ~15-20s. Voting: ~3s parallel. Total: <25s per PR (well under 15-minute target).

---

## Key Takeaways for Interviews

1. **The control question decides the pattern:** If your code picks the next step, it is a workflow (L2). If the model picks, it is an agent (L3). Start at L1, escalate only when evaluation data proves the simpler level fails.
2. **Agents cost 4-15x more tokens** due to O(n^2) context accumulation. An 8-step ReAct agent re-reads all prior history on every step, processing 4.5x more tokens than 8 independent calls.
3. **Google found multi-agent dropped performance 39-70%.** Do not assume more agents = better results. Coordination overhead and context loss between agents usually outweigh specialization benefits. Verify single-agent first.
4. **The 70-80% trap:** When an L1/L2 system scores 70-80%, fix prompts, gates, and tool descriptions before escalating architecture. Anthropic tried 50 subagents on simple queries and got worse results.
5. **Five invariants are non-negotiable:** Step ceiling, token budget, loop detection, idempotency keys, and fallback chain. Missing any one causes production failures (infinite loops at 15.7% frequency, budget overruns at 73% of projects).
6. **Checkpointing + idempotency -- both required.** Checkpointing without idempotency keys means replayed steps double-charge payments. Idempotency without checkpointing means total state loss on crash.
7. **Prefix caching gives 5.62x throughput on ReAct** by caching the system prompt and tool definitions across steps. This is the single highest-ROI optimization for agent workloads.
8. **Fallback chain: agent -> workflow -> deterministic.** Never let agent failure become user-visible failure. Document capability loss at each tier so callers know what they are getting.

## Interview Q&A

**Q1: When do you use an agent vs a workflow?**

A1: "I ask one question: is the task structure known in advance? If yes, I use a workflow -- the code controls the execution path, steps are fixed, and behavior is deterministic and testable. If the task requires adapting based on intermediate results -- deciding which tool to call next based on what was just observed -- then I use an agent. But I always start at L1 (direct call) and only escalate when evaluation proves the simpler pattern fails. The 70-80% trap is real: when an L1/L2 system scores 70-80%, the instinct is to add agents, but fixing prompts and adding validation gates is almost always the right move."

**Q2: How do you handle the O(n^2) context growth problem in agents?**

A2: "In ReAct, each step adds tokens to context, and every subsequent step must re-read all prior history. After 8 steps with 500 tokens per step, the model processes 18,000 total tokens vs 4,000 for 8 independent calls -- a 4.5x overhead from context accumulation alone. I apply six mitigations in priority order: first, prefix caching for system prompt and tool definitions -- this gives 5.62x throughput improvement and is the single highest-ROI fix. Second, tool output truncation to essential fields, cutting 40-50%. Third, a sliding window keeping only the last K steps in full context with older steps summarized. Fourth, hard step ceilings and token budgets to prevent runaway growth. For plan-based patterns like LLMCompiler, I cache and reuse plans for similar tasks, saving 20-35%."

**Q3: How do you prevent infinite loops in agents?**

A3: "Three overlapping controls. First, a hard step ceiling -- every agent run has a maximum step count that is enforced by the framework, not suggested to the model. For LangGraph I set recursion_limit to 2 times max_iterations plus 1. Second, loop detection: I hash recent action-observation pairs and check if the same hash appears in the last 2-3 steps. If an agent calls the same tool with the same arguments and gets the same result twice, it is stuck. Third, a token budget that aborts the run when cumulative tokens exceed a ceiling. The MAST taxonomy found 15.7% of agent runs hit infinite loops, so these controls are not theoretical -- they fire regularly in production."

**Q4: How do you handle human-in-the-loop for agent systems?**

A4: "The cardinal rule: never hold a live process waiting for human approval. I use the checkpoint-wait-resume pattern. When the agent reaches a step requiring human approval -- say, processing a payment -- it serializes its full state to a durable store, emits an approval request via Slack or dashboard, and terminates the process. No resources are held. When the human approves, a webhook triggers, loads the checkpoint, and resumes the agent from the exact step. In LangGraph this is the interrupt() function. The alternative -- holding a process in memory while a human reviews for potentially hours -- is a resource leak and reliability risk."

**Q5: What is your agent fallback strategy?**

A5: "Three-tier fallback: agent to workflow to deterministic. If the ReAct agent fails -- step ceiling hit, budget exhausted, loop detected, or tool failure cascading -- I fall back to a fixed workflow that handles the same task with less flexibility but more reliability. If the workflow also fails, I fall back to a deterministic path: route to a human queue with a summary of what the agent attempted. Each tier documents its capability loss. The caller knows: tier 1 means full capability, tier 2 means no dynamic tool use, tier 3 means human intervention required. This is how I hit 99.5% availability -- the system always returns something useful."

**Q6: How do you secure multi-agent systems?**

A6: "The key threat: Galileo AI showed that 87% of downstream agents can be poisoned within 4 hours from a single compromised upstream agent. My defense: treat all inter-agent messages as untrusted user input, not trusted system instructions. Each agent validates inputs against its own schema before processing. I apply output sanitization to strip instruction-like patterns from agent and tool results. Network segmentation prevents agents from directly accessing each other's tools. And the supervisor pattern adds a validation layer -- the supervisor reviews worker outputs before passing them downstream. The tiered approval model applies per-agent: read-only tools are automatic, write tools require async approval, irreversible actions require sync human confirmation."

**Q7: How do you choose between ReAct, ReWOO, and LLMCompiler?**

A7: "If the agent needs to adapt based on intermediate tool results -- for example, the answer from search A determines what to search for next -- ReAct is the right pattern despite its O(n^2) context cost. If all tool calls can be planned upfront without needing intermediate results, ReWOO saves 64% of tokens with 4.4% better accuracy -- but it cannot course-correct mid-execution. If the tool calls form a DAG with independent branches that can run in parallel, LLMCompiler gives 1.8-3.7x latency improvement and 3.4-6.7x cost improvement over ReAct. In practice, I start with ReWOO for most cases, move to ReAct when adaptability is genuinely needed, and use LLMCompiler when parallelism matters and I can identify independent branches."

**Q8: What is the realistic success rate for agent systems?**

A8: "The MAST taxonomy analyzed 1,642 real agent traces and found failure rates of 41-86.7% depending on task complexity and framework. The dominant failure modes are tool misuse at 25-35%, silent quality degradation at 20-30%, and infinite loops at 15.7%. Google's multi-agent experiments showed performance dropping 39-70% compared to single-agent baselines. Enterprise reality: 73% of agent projects exceeded their budgets, and Gartner projects 40% will be cancelled by 2027. This does not mean agents are useless -- it means they require serious engineering. The winners treat agents like distributed systems: circuit breakers, checkpointing, idempotency, monitoring, and graceful degradation."

**Q9: How do you manage token costs for agent workloads?**

A9: "Four strategies in priority order. First, pattern routing: classify tasks upfront and only use agents for the 20-25% that genuinely need dynamic tool use. Simple tasks go through L1/L2 at 1-3x tokens instead of agent 4-8x. Second, prefix caching: cache the system prompt and tool definitions across agent steps -- this gave 5.62x throughput in published benchmarks and is the single highest-ROI optimization. Third, tool output truncation: verbose API responses get truncated to essential fields before entering agent context, cutting 40-50% of context growth. Fourth, step ceilings and token budgets: hard limits prevent runaway costs. For the customer support scenario, this hybrid approach brings average cost to $0.034/ticket, with only the 20% complex tickets paying full agent cost."

**Q10: How do you debug agent failures in production?**

A10: "The audit trail with 5 structured fields -- agent_id, delegated_permissions, tool_invoked, policy_decision, reasoning_trace -- gives me the full replay of every agent run. I can reconstruct exactly what the agent was thinking at each step, what tool it called, what result it got, and what it decided to do next. For failure analysis, I look at: step count distribution to find ceiling hits, token usage distribution to find budget exhaustions, loop detection firing rates, and tool error rates per tool per agent. The key dashboard metrics are success rate by task type, average steps per successful resolution, cost per resolution, and fallback tier distribution. When a failure pattern emerges -- say, 30% of billing-related tickets are looping -- I fix the tool description or add a gate, not escalate the architecture."

**Q11: What is Reflexion and when would you use it?**

A11: "Reflexion adds episodic memory to an agent: after a failed attempt, the agent reflects on why it failed, stores that analysis in memory, and retries with that reflection context. On HumanEval it reaches 91% vs GPT-4's 80% baseline, and it adds 20+ percentage points on AlfWorld and HotPotQA. But it only works when the task benefits from self-critique -- on WebShop, it showed no improvement. I would use Reflexion for code generation, complex reasoning, and multi-hop QA where the agent can learn from its mistakes. I would not use it for straightforward extraction or classification where the answer is either right or wrong with no useful failure signal. The cost is multiplicative -- each retry includes all prior reflections -- so I cap retries at 3."

**Q12: How do you architect for EU AI Act compliance in agent systems?**

A12: "The EU AI Act deadline is August 2026 with penalties of 35 million EUR or 7% of global revenue. For agent systems classified as high-risk, three requirements dominate. First, full audit trails: every agent step must log the 5 structured fields -- this is not optional. Second, human oversight: the tiered approval model ensures humans control irreversible actions, and the checkpoint-wait-resume pattern makes this operationally viable. Third, transparency: the reasoning trace must be available for regulatory review, which means agent decisions need to be explainable, not just correct. I implement this with LangGraph's built-in checkpointing for the audit trail, the interrupt() pattern for human oversight, and structured logging with the AgentRunContext pattern for transparency. The key architectural decision is making compliance a first-class concern from day one, not bolting it on later."

## Key Numbers to Memorize

| Metric | Value |
|---|---|
| Agent token multiple vs direct call | 4-8x (single agent), 10-20x (multi-agent) |
| LLM calls: agent vs CoT | ~9.2x |
| ReAct vs Act-only (ALFWorld) | 71% vs 45% |
| ReWOO vs ReAct token savings | 64% fewer tokens, +4.4% accuracy |
| LLMCompiler vs ReAct (HotpotQA) | 1.80x latency, 3.37x cost improvement |
| LLMCompiler vs ReAct (Movie Rec) | 3.74x latency, 6.73x cost improvement |
| Prefix caching throughput gain (ReAct) | 5.62x throughput, 15.7% E2E latency reduction |
| Reflexion lift (HumanEval) | 91% vs GPT-4 baseline 80% |
| Google multi-agent performance drop | 39-70% vs single agent |
| MAST failure rates | 41-86.7% |
| Infinite loop frequency | 15.7% of agent runs |
| Over-permissioned agents | 60% (Databricks) |
| Lack AI identity visibility | 92% of organizations (Oracle) |
| Multi-agent poisoning speed | 87% downstream in 4 hours (Galileo) |
| Enterprise budget overruns | 73% exceeded budgets |
| Projected cancellation rate | 40% by 2027 (Gartner) |
| EU AI Act penalty | 35M EUR or 7% revenue |
| LATS calls per request | 71.0 LLM calls |
| Context growth per step | O(n^2) total tokens processed |

## Quick Reference

- **Escalation ladder:** L1 direct call -> L2 workflow -> L3 agent -> L4 multi-agent. Start low, escalate only with eval evidence.
- **Control question:** Who picks the next step? Code = workflow. Model = agent.
- **Five invariants:** Step ceiling, token budget, loop detection, idempotency, fallback chain. All five are mandatory.
- **ReAct:** O(n^2) context growth. Use when intermediate results affect next action.
- **ReWOO:** O(n) context. 64% fewer tokens. Use when all tool calls are plannable upfront.
- **LLMCompiler:** DAG parallelism. 1.8-3.7x latency improvement. Use when branches are independent.
- **Prefix caching:** 5.62x throughput on agent workloads. Cache system prompt + tool defs.
- **HITL pattern:** Checkpoint-wait-resume. Never hold live process during human review.
- **Multi-agent caution:** Google found 39-70% performance drop. Verify single-agent first.
- **Fallback chain:** Agent -> workflow -> deterministic. Document capability loss at each tier.
- **Checkpointing + idempotency:** Both required. Missing either causes production failures.
- **Audit trail:** 5 structured fields per step (agent_id, permissions, tool, policy, reasoning).
