# Module 04: Agent Loop Patterns

> **Audience**: Principal Data Scientist (12+ YOE) preparing for Director/VP-level AI roles.
> **Scope**: Single-agent loop architectures -- ReAct, Plan-and-Execute, Reflexion, Self-Correction, LATS -- with production failure modes, token economics, durable execution, enterprise governance, and framework-level implementation details.
> **Pricing assumptions**: GPT-4o input $2.50/1M, output $10/1M; GPT-4o-mini input $0.15/1M, output $0.60/1M; Claude Sonnet 4 input $3/1M, output $15/1M; Claude Opus 4 input $15/1M, output $75/1M. All as of mid-2026.

---

## 1. System Topology & Data Flow

### 1.1 Reference Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           CONTROL PLANE                                      │
│                                                                              │
│  ┌──────────────────┐   ┌───────────────────┐   ┌────────────────────────┐  │
│  │  Loop            │   │  Iteration Budget  │   │  Convergence           │  │
│  │  Orchestrator    │──>│  Manager           │──>│  Detector              │  │
│  │                  │   │                    │   │                        │  │
│  │  - Pattern select│   │  - max_iterations  │   │  - Output hash diff    │  │
│  │    (ReAct / P&E /│   │  - token ceiling   │   │  - Goal-completion     │  │
│  │    Reflexion)    │   │  - cost ceiling $  │   │    signal (stop_reason)│  │
│  │  - State machine │   │  - wall-clock cap  │   │  - Similarity thresh   │  │
│  │    transitions   │   │  - per-step budget  │   │  - Stall detection     │  │
│  └────────┬─────────┘   └───────────────────┘   └────────────────────────┘  │
│           │                                                                  │
├───────────┼──────────────────────────────────────────────────────────────────┤
│           │              DATA PLANE                                           │
│           v                                                                  │
│  ┌──────────────────┐   ┌───────────────────┐   ┌────────────────────────┐  │
│  │  LLM Inference   │   │  Tool Executor    │   │  Observation           │  │
│  │  Engine          │<──│                   │──>│  Collector             │  │
│  │                  │   │  - Schema validate │   │                        │  │
│  │  - Prompt build  │   │  - Sandbox / OCI  │   │  - Parse tool output   │  │
│  │  - Model routing │   │  - Timeout enforce│   │  - Truncate / compress │  │
│  │    (tier select) │   │  - Idempotency key│   │  - Error classification│  │
│  │  - Response parse│   │  - Retry w/ backoff│   │  - Structured extract  │  │
│  └────────┬─────────┘   └───────────────────┘   └────────────────────────┘  │
│           │                                                                  │
│           v                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐│
│  │  State Accumulator                                                       ││
│  │  - Append thought/action/observation to trajectory                       ││
│  │  - Context window budget check (prune if exceeding threshold)            ││
│  │  - Rolling summarization of old turns                                    ││
│  │  - Goal reiteration injection (Focused ReAct)                            ││
│  └────────┬─────────────────────────────────────────────────────────────────┘│
├───────────┼──────────────────────────────────────────────────────────────────┤
│           │              PERSISTENCE LAYER                                    │
│           v                                                                  │
│  ┌──────────────────┐   ┌───────────────────┐   ┌────────────────────────┐  │
│  │  Checkpoint       │   │  Trajectory        │   │  Plan Store            │  │
│  │  Store            │   │  History           │   │  (Plan-and-Execute)    │  │
│  │                  │   │                    │   │                        │  │
│  │  - Redis / SQL / │   │  - Full T-A-O log  │   │  - Current plan DAG   │  │
│  │    Temporal event│   │  - Per-step token  │   │  - Completed steps    │  │
│  │    journal       │   │    counts + costs  │   │  - Re-plan history    │  │
│  │  - Resumable     │   │  - Eval scores     │   │  - Dependency graph   │  │
│  │    state blob    │   │    (Reflexion)     │   │                        │  │
│  └──────────────────┘   └───────────────────┘   └────────────────────────┘  │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                      CROSS-CUTTING                                           │
│                                                                              │
│  ┌──────────────────┐   ┌───────────────────┐   ┌────────────────────────┐  │
│  │  Tool Proxies     │   │  HITL Gate         │   │  Telemetry /           │  │
│  │                  │   │                    │   │  Observability         │  │
│  │  - API adapters  │   │  - Risk-tier eval  │   │                        │  │
│  │  - Auth injection│   │  - Pause/resume    │   │  - OpenTelemetry spans │  │
│  │  - Rate limiting │   │  - Approval queue  │   │  - Cost per iteration  │  │
│  │  - Result cache  │   │  - Timeout + escal.│   │  - Trace ID per task   │  │
│  └──────────────────┘   └───────────────────┘   └────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Single Iteration Request Flow (ReAct)

A complete iteration through the data plane proceeds as follows:

```
User Goal + History
       │
       v
┌──────────────┐   tokens_before = count(history)
│ Budget Check  │──> if tokens_before + est_response > ceiling: HALT
└──────┬───────┘
       v
┌──────────────┐   prompt = system + goal + trajectory[-K:]
│ Prompt Build  │──> (optional: inject goal reiteration, summarize old turns)
└──────┬───────┘
       v
┌──────────────┐   response = llm.chat(prompt, tools=tool_schemas)
│ LLM Call      │──> emit OTel span: {model, input_tokens, output_tokens, latency_ms}
└──────┬───────┘
       v
┌──────────────┐   if response.stop_reason == "end_turn": return final_answer
│ Parse Response│──> if response.stop_reason == "tool_use": extract tool_name, args
└──────┬───────┘   if neither: raise UnexpectedStopReason
       v
┌──────────────┐   validate(tool_call, schema)
│ Tool Dispatch │──> execute in sandbox with timeout
└──────┬───────┘   generate idempotency_key = hash(tool_name + args + step_id)
       v
┌──────────────┐   observation = truncate(tool_result, max_obs_tokens)
│ Observation   │──> classify: success | error | timeout | rate_limited
│ Collect       │
└──────┬───────┘
       v
┌──────────────┐   trajectory.append(Thought, Action, Observation)
│ State Update  │──> checkpoint.save(trajectory, step_id, cost_so_far)
└──────┬───────┘   iteration_count += 1
       │
       v
  Back to Budget Check (next iteration)
```

**Key invariant**: The model's own `stop_reason` (or equivalent) is the primary termination signal. Iteration caps are safety nets, not control flow. This is the anti-pattern most teams get wrong -- they use `max_iterations` as the termination mechanism and wonder why agents stop mid-task.

---

## 2. Core Mechanics & Algorithms

### 2.1 ReAct: Reasoning + Acting

**Paper**: Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models" (ICLR 2023).

**State machine**:

```
          ┌─────────────────────────────────────┐
          │                                     │
          v                                     │
    ┌──────────┐     ┌──────────┐     ┌─────────┴──┐
    │  THOUGHT  │────>│  ACTION  │────>│ OBSERVATION │
    │  (reason) │     │  (tool)  │     │  (result)   │
    └──────────┘     └──────────┘     └────────────┘
          │
          │ stop_reason == "end_turn"
          v
    ┌──────────┐
    │  ANSWER  │
    └──────────┘
```

**Why it works**: LLMs perform better when they alternate between reasoning and acting. Without alternation, agents either hallucinate answers (reasoning without grounding) or execute tools blindly (acting without interpretation). ReAct grounds reasoning in real-world observations.

**Error compounding**: If each step has 95% reliability, a 10-step task succeeds ~60% of the time (0.95^10 = 0.5987). This is the fundamental reliability constraint of sequential loops.

**Variants (2024-2026)**:
- **RP-ReAct**: Decouples planning from execution. A Reasoner-Planner handles strategy; Proxy-Execution agents each run internal ReAct loops with context-saving strategies to avoid token overflow.
- **Focused ReAct**: Reiterates the original question at each step + early-stops on repetitive actions. Reported 530% relative accuracy gains and 34% runtime reduction in low-resource models.

**When to use**: Dynamic, exploratory, open-ended tasks where the next action depends heavily on the previous observation. Not ideal for long, predictable workflows (use Plan-and-Execute).

### 2.2 Plan-and-Execute

**Paper**: Wang et al., "Plan-and-Solve Prompting" (ACL 2023). Popularized by BabyAGI.

**Two-phase architecture**:

```
┌─────────────┐          ┌──────────────────┐          ┌─────────────┐
│   PLANNER   │─────────>│   EXECUTOR       │─────────>│  RE-PLANNER │
│ (frontier   │  plan[]  │ (smaller model / │  results  │ (frontier   │
│  model)     │          │  deterministic)  │          │  model)     │
└─────────────┘          └──────────────────┘          └─────────────┘
      ^                                                       │
      └───────────────────────────────────────────────────────┘
                         (on failure / deviation)
```

**Cost advantage**: The expensive frontier model is called only for planning and re-planning (typically 2-3 calls). Sub-task execution uses cheaper models or deterministic code. Benchmark data (2026): Plan-Execute agents average 3,000-4,500 tokens and 5-8 API calls per task, costing $0.09-$0.14.

**Re-planning triggers**: Step failure, unexpected tool output, budget threshold crossed, external state change detected.

**Limitation**: Less adaptive to surprises. If a step yields unexpected results, the agent struggles to deviate without a re-planning call. Not suitable when execution feedback must immediately influence reasoning -- use ReAct.

### 2.3 Reflexion

**Paper**: Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning" (NeurIPS 2023).

**Loop**: Generate -> Evaluate -> Reflect -> Regenerate.

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│    ACTOR     │────>│  EVALUATOR   │────>│  REFLECTOR   │
│ (generate    │     │ (test runner,│     │ (verbal      │
│  actions)    │     │  validator,  │     │  critique)   │
└──────┬───────┘     │  LLM judge)  │     └──────┬───────┘
       ^             └──────────────┘            │
       │                                         │
       │         ┌──────────────┐                │
       └─────────│  EPISODIC    │<───────────────┘
                 │  MEMORY      │  store reflection
                 │ (vector DB)  │  as semantic gradient
                 └──────────────┘
```

**No weight updates**. Improvement is purely in-context: verbal critiques are loaded into the Actor's prompt on the next attempt. When reflections are stored in a vector DB and reused by task type, an emergent skill library arises across episodes.

**Results**: 91% pass@1 on HumanEval vs. GPT-4's 80% baseline. Self-reflection adds an 8% absolute boost over episodic memory alone.

**Critical limitation**: 10-30x cost of a single Chain-of-Thought call. Strictly sequential. Useless without a reliable evaluator. If the model misdiagnoses why it failed, the next attempt inherits the wrong correction.

### 2.4 Self-Correction: What Works and What Does Not

**The fundamental constraint** (Huang et al., ICLR 2024): Intrinsic self-correction -- asking an LLM to review and revise its own answer using only its own judgment -- consistently degrades performance on reasoning benchmarks. The model that generated the wrong answer shares the same blind spots as the model asked to evaluate it.

**The coherence trap** (2026 preprint): When generator and evaluator share correlated error modes, iterative self-critique amplifies confidence without adding information. The agent convinces itself with increasingly polished but still-wrong reasoning.

**Stability threshold (2026)**: A feedback-control analysis yields a measurable criterion: iterate only when ECR/EIR > Acc/(1-Acc), where:
- ECR = Error Correction Rate (probability of fixing a wrong answer)
- EIR = Error Introduction Rate (probability of breaking a correct answer)
- Acc = model's base accuracy on the task

Empirically across 7 models and 3 datasets, only o3-mini (+3.4pp), Claude Opus 4.6 (+0.6pp), and o4-mini (+/-0pp) remain non-degrading under intrinsic self-correction.

**What actually works -- grounded self-correction**:

| Domain | External Signal | Why It Works |
|---|---|---|
| Code | Test runner output | Binary pass/fail from execution, not LLM judgment |
| Research | Retrieved source documents | Ground truth the generator did not write |
| Form filling | Schema validation | Structural constraints, not semantic evaluation |
| Reasoning | Process Reward Models (PRMs) | Trained verifiers scoring intermediate steps |
| General | SCoRe (ICLR 2025) | RL-trained self-correction (+15.6% MATH, +9.1% HumanEval) |

**Design rule**: Ground the critic in something the generator did not write. Find that external signal before writing correction logic.

### 2.5 LATS: Language Agent Tree Search

**Paper**: Zhou et al. (ICML 2024). Adapts Monte Carlo Tree Search (MCTS) to the linguistic domain.

**Architecture**: Treats thoughts (internal reasoning) and actions (external tool calls) as nodes in the same search tree. Each node encodes current state (task input, action history, observations); edges represent possible next actions. Six operations: selection, expansion, evaluation, simulation, backpropagation, reflection.

**Key distinction from Tree of Thoughts (ToT)**: ToT relies solely on LLM internal knowledge. LATS obtains value estimates after environmental feedback, grounding the search in real observations.

**Results**: 92.7% pass@1 on HumanEval (GPT-4). On HotPotQA, doubled ReAct performance (EM: 0.32 -> 0.71). On Game of 24, 44% vs. ToT's 20%.

**Trade-off**: Extremely token-expensive due to tree branching. Practical only for high-value tasks where accuracy justifies the cost.

### 2.6 Max Iteration Guards & Convergence Detection

All frameworks implement iteration limits, but semantics differ:

| Framework | Default | Parameter | What It Counts |
|---|---|---|---|
| OpenAI Agents SDK | None | `max_turns` | Full reason-act cycles |
| LangGraph | Configurable | `recursion_limit` | Graph traversals |
| CrewAI | 15 | `max_iter` per Agent | Tool-calling cycles only |
| Google ADK LoopAgent | Required | `max_iterations` | Loop iterations |
| Claude reference | 10 | `max_iterations` | Tool-call cycles |

**Best practice**: Set conservative limits (5-10 for most tasks). Use the model's own termination signal as primary control. Treat iteration caps as cost/safety guardrails, never as control flow.

**Convergence detection strategies**:
1. **Output hash comparison**: Flag when consecutive outputs have cosine similarity > 0.95 (agent is stuck).
2. **Action repetition**: Detect when the same tool is called with identical arguments N times.
3. **Progress metric**: Define a task-specific progress function; halt if delta < threshold for K consecutive steps.
4. **Token velocity**: If tokens consumed per step are increasing but observable progress is flat, the agent is likely in a degenerate loop.

### 2.7 Framework Implementations Comparison

| Dimension | LangGraph v2.0 | OpenAI Agents SDK | CrewAI v0.80+ | Google ADK | Claude Agent Loop |
|---|---|---|---|---|---|
| **Abstraction** | Cyclic state machine | Agent/Runner/Handoff/Guardrail | Role-based multi-agent | Sequential/Parallel/Loop agents | Messages API + stop_reason |
| **Loop control** | Conditional edges, recursion_limit | max_turns, stop on text output | max_iter per agent (15 default) | max_iterations (required) | stop_reason field, external max_iterations |
| **Checkpointing** | Built-in (Redis/SQL/file) | None native | None native | None native | None native (framework must add) |
| **HITL** | Built-in interrupt nodes | Via hooks (on_tool_start) | Manual | Manual | Manual |
| **Streaming** | Type-safe (v1.2, May 2026) | run_streamed() | Limited | Limited | Messages API streaming |
| **Multi-agent** | Fan-out/fan-in via graph edges | Handoff primitive | Process.sequential / hierarchical | ParallelAgent, SequentialAgent | Not built-in |
| **GitHub stars** | 30K+ | N/A (part of SDK) | 54K+ | ~20K | N/A |
| **Maturity** | Production-grade | Production-grade | Growing | Early production | Reference implementation |
| **Best for** | Complex stateful workflows | Simple tool-calling agents | Role-based team orchestration | Deterministic pipelines | Anthropic model integration |

**LangGraph** models agents as cyclic state machines from four primitives: State (typed schema with reducers), Nodes (functions returning state updates), Edges (static or conditional), and Checkpointers. Over 70% of production agents use some form of graph structure per LangChain's 2026 State of Agent Engineering report.

**OpenAI Agents SDK** is deliberately minimal. The Runner manages the tool-call loop: call model -> if tool_use, execute + continue -> if handoff, switch agent -> if text output, return -> if max_turns exceeded, raise MaxTurnsExceeded. Built-in tracing with spans for every LLM call, tool execution, and handoff.

**CrewAI** assigns roles to agents, each running a ReAct loop internally. Flows (`@start`, `@listen`, `@router` decorators) solve statelessness for production use. 450M+ agentic workflow executions/month.

**Google ADK** provides three deterministic workflow agent types -- SequentialAgent, ParallelAgent, LoopAgent -- composable into arbitrary nesting. No LLM reasoning for orchestration decisions. Failure mode: write-set collision when two ParallelAgent branches write the same session key.

**Claude Agent Loop** uses the Messages API `stop_reason` field: `"tool_use"` means continue, `"end_turn"` means terminate. The framework must enforce iteration limits externally.

---

## 3. Token Economics & NFR Analysis

### 3.1 The Quadratic Cost Problem

Agent loops do not scale linearly. Each iteration re-sends the entire conversation history to the LLM API. Cost follows the triangular number series:

```
Total tokens sent across N steps = sum(i=1 to N) of (base + i * avg_step_tokens)

For a 5-step agent loop:
  Step 1: base + 1 * step_tokens
  Step 2: base + 2 * step_tokens
  Step 3: base + 3 * step_tokens
  Step 4: base + 4 * step_tokens
  Step 5: base + 5 * step_tokens
  ─────────────────────────────
  Total = 5 * base + 15 * step_tokens

A 5-step loop costs ~15x a single call, not 5x.
```

In the worst case (no pruning), cost approaches O(n^2) with step count.

**Anthropic's empirical data (2025)**: Single agents use ~4x the tokens of a single chat turn. Multi-agent systems use ~15x.

### 3.2 Cost Formulas

**Per-iteration cost** (iteration i):

```
C_i = (input_tokens_i * price_input) + (output_tokens_i * price_output)

where input_tokens_i = system_prompt + sum(j=1..i-1)(thought_j + action_j + obs_j) + goal
```

**Total task cost** (N iterations, no pruning):

```
C_total = sum(i=1..N) C_i
        ~ N * C_system + (N*(N+1)/2) * C_avg_step    [quadratic term]
```

**With context pruning** (keep last K turns verbatim, summarize older):

```
C_total ~ N * (C_system + K * C_avg_step + C_summary)  [linear in N]
```

**Worked example**: ReAct loop, 8 iterations, GPT-4o, no pruning:
- System prompt: 1,000 tokens
- Average step (thought + action + observation): 800 tokens
- Average output per step: 300 tokens
- Input cost per iteration: (1,000 + i*800) * $2.50/1M
- Output cost per iteration: 300 * $10/1M

| Iteration | Input Tokens | Input Cost | Output Cost | Cumulative |
|---|---|---|---|---|
| 1 | 1,800 | $0.0045 | $0.003 | $0.0075 |
| 2 | 2,600 | $0.0065 | $0.003 | $0.017 |
| 3 | 3,400 | $0.0085 | $0.003 | $0.029 |
| 4 | 4,200 | $0.0105 | $0.003 | $0.042 |
| 5 | 5,000 | $0.0125 | $0.003 | $0.058 |
| 6 | 5,800 | $0.0145 | $0.003 | $0.075 |
| 7 | 6,600 | $0.0165 | $0.003 | $0.095 |
| 8 | 7,400 | $0.0185 | $0.003 | $0.116 |

**Total**: $0.116 per task. Naive estimate (8 * single call) would predict $0.06. Actual is ~2x higher due to quadratic growth. At 10,000 tasks/day, the delta is $560/day.

### 3.3 Context Rot

As tokens accumulate, the model's effective attention budget thins and recall of earlier instructions drops. 65% of enterprise AI failures in 2025 were attributed to context drift or memory loss during multi-step reasoning -- not raw context exhaustion, but gradual degradation as irrelevant information crowds out relevant context. You pay rising cost for declining quality.

### 3.4 Cost Comparison Across Patterns

| Pattern | Token Multiplier vs. Single Call | Latency Profile | Cost Lever | Best For |
|---|---|---|---|---|
| ReAct (5-step) | ~15x (quadratic) | 100-500ms per iter | Context pruning | Dynamic exploration |
| Plan-and-Execute | ~5-8x (linear, small models) | Faster multi-step | Model tier routing | Predictable workflows |
| Reflexion | 10-30x a CoT call | Sequential, high | Reduce retry count | Tasks with reliable evaluators |
| LATS | 50-100x+ (tree branching) | Minutes per task | Limit branching factor | High-value accuracy-critical |

### 3.5 Latency SLA Targets

| Metric | Target | Mitigation |
|---|---|---|
| Per-iteration latency (ReAct) | < 2s (P95) | Model tier routing, streaming, cached system prompts |
| End-to-end (simple task, 3-5 steps) | < 10s | Parallel tool execution, connection pooling |
| End-to-end (complex task, 8-15 steps) | < 60s | Phase-based checkpointing, async execution |
| HITL gate response time | Minutes to days | Durable execution (Temporal), webhook notifications |
| Timeout per tool call | 10-30s configurable | Circuit breaker, fallback tool |

### 3.6 Throughput: Concurrent Agent Capacity Planning

```
Max concurrent agents = API_rate_limit_TPM / avg_tokens_per_agent_per_minute

Example (GPT-4o, Tier 5):
  TPM limit: 30,000,000
  Agent consuming ~5,000 tokens/min (8-step task over 2 minutes):
  Max concurrent = 30M / 5K = 6,000 agents

  But: RPM limit is 10,000
  At ~4 API calls/min per agent: Max = 10,000 / 4 = 2,500 agents

  Binding constraint: RPM, not TPM.
```

### 3.7 Five Mitigation Strategies

1. **Subagent isolation**: Each subagent receives only its relevant context slice. Reduces tokens from ~15K to ~9K for multi-domain queries.
2. **State resets**: For loops exceeding 10 steps, serialize state at phase boundaries, start fresh. Cost of lossy handoffs < unbounded accumulation.
3. **Context pruning**: Rolling summarization, tool result compression, keep only last K turns verbatim. Reduces costs 40-75% without quality degradation.
4. **Model tier routing**: Run 80% of steps on a smaller model, escalate only the hard 20% to frontier. Costs ~12% of all-frontier workflow. Typical savings: 60-80%.
5. **Budget enforcement (not alerts)**: Check token budget *before* each API call, blocking the call rather than reporting after. An alert fires after cost has accumulated; enforcement prevents it.

### 3.8 Phase-Based Checkpointing

The most cost-efficient pattern for multi-step workflows: define explicit state checkpoints at phase boundaries. Serialize agent state, start the next phase with a fresh, minimal context. Each phase starts with 2,000-5,000 tokens rather than carrying 50,000+ accumulated tokens from prior phases.

### 3.9 Real-World Cost Incidents

| Incident | Cost | Root Cause | Missing Safeguard |
|---|---|---|---|
| LangChain market research pipeline (Nov 2025) | $47,000 | 4 agents in infinite loop for 11 days | Per-agent budget ceiling, enforcement mechanism |
| Solo developer weekend (2026) | $4,200 | Autonomous refactoring run over a long weekend | Wall-clock timeout, human checkpoint |
| Claude Code sub-agent (GitHub #15909, 2025) | 27M tokens | Sub-agent stuck in infinite loop | Output similarity detection, iteration cap |
| Fortune 500 collective (2025) | ~$400M | Unbudgeted AI cloud spend | Financial guardrails (only 44% of enterprises had any) |

96% of enterprises reported AI costs exceeding initial projections.

---

## 4. Distributed Resilience & Security

### 4.1 Failure Taxonomy (2026 Production)

A mid-2026 production taxonomy identifies six categories and fifteen failure modes:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AGENT FAILURE TAXONOMY                            │
├──────────────┬──────────────────────────────────┬───────────────────┤
│ Category     │ Failure Modes                    │ Detection         │
├──────────────┼──────────────────────────────────┼───────────────────┤
│ Drift        │ Semantic, reasoning,             │ Hard -- late      │
│              │ coordination, behavioral         │ surfacing         │
├──────────────┼──────────────────────────────────┼───────────────────┤
│ State        │ Context exhaustion, memory       │ Token counting,   │
│              │ pollution, hallucinated state    │ state validation  │
├──────────────┼──────────────────────────────────┼───────────────────┤
│ Coordination │ Sub-agent loss, race conditions, │ Heartbeat,        │
│              │ orchestration overhead           │ timeouts          │
├──────────────┼──────────────────────────────────┼───────────────────┤
│ Termination  │ Premature stop, infinite loop,   │ Most common,      │
│              │ budget exhaustion               │ easiest to fix    │
├──────────────┼──────────────────────────────────┼───────────────────┤
│ Adversarial  │ Prompt injection, reward         │ Catastrophic but  │
│              │ hacking, alignment faking        │ low maturity      │
├──────────────┼──────────────────────────────────┼───────────────────┤
│ Tool         │ Tool selection error,            │ Schema validation,│
│ Interface    │ schema mismatch                  │ type checking     │
└──────────────┴──────────────────────────────────┴───────────────────┘
```

**Reliability at scale**: Multi-agent LLM systems fail 41-86% of the time in production depending on task complexity. Five agents at 95% individual accuracy deliver ~77% overall success. 88% of failures trace to infrastructure gaps, not model quality. Gartner predicts over 40% of agentic AI projects will be canceled by 2027 -- not because the technology does not work, but because teams deployed without resilience infrastructure.

**Engineering prioritization** (by detection difficulty x frequency x cost per incident):
1. Tool interface (high frequency, easy fix) -- schema validation, idempotent tools
2. Termination (most common) -- iteration limits, budget caps
3. State (context exhaustion) -- pruning, summarization, resets
4. Drift (hard to detect) -- goal reiteration, trajectory evaluation
5. Coordination (multi-agent only) -- timeouts, dead-letter queues
6. Adversarial (catastrophic but rare) -- input sanitization, sandboxing

### 4.2 Infinite Loop Detection

IAL-Scan examined 6,549 LLM agent repositories and found 68 confirmed infinite agentic loop failures across 47 projects (91.9% precision). This is not a corner case -- it is a shipped design pattern.

**Detection strategies**:
- Hash-based: Compare output hashes across iterations; flag when consecutive outputs have similarity above threshold.
- Schema-strict: Validate tool call schemas before dispatch; reject malformed calls.
- Step count + output similarity: Combine iteration count with semantic similarity to detect oscillation.
- Replayed production traces as evals: Use recorded failure traces as regression tests.

### 4.3 Durable Execution

**Problem**: Most agent implementations run synchronously in memory. If anything interrupts the loop -- exception, timeout, process termination -- state disappears. Agent workflows are long-running (minutes to hours), need to survive infrastructure failures, and require exactly-once semantics for side effects.

**Critical distinction**: Session memory is not durable execution. Saving chat history helps an agent remember, but does not prove which shell command ran, which email was sent, or whether a retry would duplicate a side effect.

**Temporal's model**: Replays event history to reconstruct in-memory state after a crash. The runtime maintains a journal of every completed step:
- On crash recovery, the workflow function re-executes from the beginning.
- For each step already in the journal, the cached result is returned immediately (no re-execution).
- Execution continues normally from the first step not in the journal.

**Critical pitfall**: Workflows must be deterministic. LLM calls are inherently non-deterministic, so they must be wrapped as "Activity" steps whose results are journaled on first execution and never re-run on replay.

**Continue-As-New**: When event history grows too large, the workflow atomically completes the current run and starts a new one with the same workflow ID, carrying forward only essential state.

**LangGraph checkpointing vs. durable execution**: LangGraph saves state to Redis/SQL/file after each step. However, it lacks automatic failure detection -- if the process crashes, no supervisor notices. "Checkpointing says: 'I saved your state. You take it from here.' Durable execution says: 'Your agent workflows will run to completion.'"

**2025-2026 market**: AWS Durable Functions, Cloudflare Workflows (GA), Vercel Workflow DevKit, Temporal ($5B valuation, 9.1 trillion lifetime action executions -- 1.86 trillion from AI-native companies), Azure Durable Functions, Inngest, Restate, Hatchet, DBOS.

**Cost argument**: A 20-step workflow at $0.05/step: without durable execution, crash at step 18 wastes $1.00 (re-run all 20). With durable execution, resume at step 18 costs $0.10.

### 4.4 Idempotent Tool Design

When a tool call fails partway through and the agent retries, the tool must produce the same result whether it runs once or three times. A tool that writes a database record on every invocation turns retry logic into a data corruption vector. Patterns: idempotency keys for API calls, upserts instead of inserts, check-before-write for state mutations.

### 4.5 Enterprise Security & Governance

**The governance gap**: Deloitte 2026 -- only 21% of organizations have a mature governance model for agentic AI. Autonomous agents outnumber humans 82:1 in enterprise environments, yet only 22% treat agents as identity-bearing entities with formal access controls.

**Risk-tiered approval design**:

```
┌─────────────────────────────────────────────────────────────────┐
│                    RISK TIER MATRIX                              │
├─────────┬───────────────────┬───────────────────────────────────┤
│ Tier    │ Action Type       │ Authorization                    │
├─────────┼───────────────────┼───────────────────────────────────┤
│ LOW     │ Read-only queries,│ Auto-approve. No interrupt.      │
│         │ search, compute   │ Log for audit.                   │
├─────────┼───────────────────┼───────────────────────────────────┤
│ MEDIUM  │ Write to staging, │ Policy check at invocation time. │
│         │ draft emails,     │ Route to human if policy         │
│         │ create branches   │ escalates. 5-min timeout.        │
├─────────┼───────────────────┼───────────────────────────────────┤
│ HIGH    │ Financial txns,   │ Mandatory human approval.        │
│         │ production writes,│ Workflow pauses (durable).       │
│         │ admin operations, │ Full context in approval UI.     │
│         │ external comms    │ No timeout -- wait indefinitely. │
├─────────┼───────────────────┼───────────────────────────────────┤
│ CRITICAL│ Irreversible      │ Multi-party approval.            │
│         │ destructive ops,  │ Manager + domain expert.         │
│         │ regulatory actions│ Hash-chained audit trail.        │
└─────────┴───────────────────┴───────────────────────────────────┘
```

"Rubber-stamping is worse than no gate at all, because it creates the appearance of oversight without the substance." Low-risk reversible actions should run without interruption. Forcing approval on routine actions trains reviewers to rubber-stamp everything.

**Human-in-the-loop as a durability primitive**: A HITL gate suspends the agent at a named checkpoint, writes full state to the persistent log, releases the process thread entirely, and resumes only when the approval signal arrives (minutes or days later). If approval is stored only as a chat message, replay becomes unsafe.

**Enterprise identity model for agents**: Give each production agent a distinct identity. Move secrets into managed vaults. Replace permanent credentials with task-bound, short-lived tokens. Five questions the identity model must answer: Which agent is acting? Whose authority is it using? What may it access? Which actions require approval? How can its access be withdrawn?

**Audit and accountability**: Every approval, denial, override, and timeout logged as an event with context. When a regulator asks how a consequential action was authorized, the answer should be a record, not a recollection. Historically, the focus was on explaining why access was denied; with autonomous agents, organizations must explain why an action was permitted.

**Regulatory drivers**: NIST AI Agent Standards Initiative (Feb 2026). EU AI Act Article 14 (human oversight for high-risk AI), Article 50 transparency duties (effective Aug 2, 2026). DORA mapping to human oversight requirements.

**Governance tools**: HumanLayer (SDK for HITL approval gates), Proofpane (runtime governance gateway, NIST/ISO 42001/EU AI Act compliance), Kakunin (X.509 certificates, per-agent action scope enforcement), GATE by Sage IT (runtime authorization for ERP/CRM/cloud), Arthur (human oversight monitoring).

### 4.6 Delegated Authority vs. Approval Queues

Forbes (Aug 2026) argues for a paradigm shift: govern delegated authority, not individual actions. Define delegation boundaries, escalation policies, acceptable risk, and automatic authority withdrawal conditions. This works more like a command structure than an approval queue, and scales where per-action approval does not.

---

## 5. Production Enterprise Code

### 5.1 ReAct Loop with Max Iteration Guard and Convergence Detection

```python
"""
ReAct loop with budget enforcement, convergence detection,
and structured trajectory logging. Production-ready skeleton.
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.react")


@dataclass
class AgentBudget:
    max_iterations: int = 10
    max_input_tokens: int = 100_000
    max_cost_usd: float = 1.0
    cost_per_input_token: float = 2.5e-6   # GPT-4o input
    cost_per_output_token: float = 10.0e-6  # GPT-4o output
    tokens_used: int = 0
    cost_used: float = 0.0

    def record(self, input_tokens: int, output_tokens: int) -> None:
        self.tokens_used += input_tokens + output_tokens
        self.cost_used += (
            input_tokens * self.cost_per_input_token
            + output_tokens * self.cost_per_output_token
        )

    def check(self, iteration: int) -> str | None:
        """Return a reason string if budget is exceeded, else None."""
        if iteration >= self.max_iterations:
            return f"max_iterations ({self.max_iterations}) reached"
        if self.tokens_used >= self.max_input_tokens:
            return f"token ceiling ({self.max_input_tokens}) exceeded"
        if self.cost_used >= self.max_cost_usd:
            return f"cost ceiling (${self.max_cost_usd:.2f}) exceeded"
        return None


@dataclass
class TrajectoryStep:
    iteration: int
    thought: str
    action: str | None
    action_args: dict[str, Any] | None
    observation: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: float
    output_hash: str


@dataclass
class ConvergenceDetector:
    """Detects agent stalls via output hash similarity."""
    window: int = 3
    recent_hashes: list[str] = field(default_factory=list)

    def is_stuck(self, output_hash: str) -> bool:
        self.recent_hashes.append(output_hash)
        if len(self.recent_hashes) < self.window:
            return False
        tail = self.recent_hashes[-self.window:]
        return len(set(tail)) == 1  # all identical

    def reset(self) -> None:
        self.recent_hashes.clear()


def _hash_output(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def react_loop(
    llm_client,          # Any client with .chat(messages, tools) -> response
    tool_registry: dict, # {tool_name: callable}
    goal: str,
    system_prompt: str,
    budget: AgentBudget | None = None,
    convergence_window: int = 3,
) -> dict[str, Any]:
    """
    Run a ReAct loop until the model produces a final answer,
    a budget limit is hit, or convergence stall is detected.

    Returns:
        {"answer": str, "trajectory": list[TrajectoryStep],
         "total_cost": float, "total_tokens": int, "exit_reason": str}
    """
    budget = budget or AgentBudget()
    detector = ConvergenceDetector(window=convergence_window)
    trajectory: list[TrajectoryStep] = []
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": goal},
    ]
    tool_schemas = [
        {"type": "function", "function": meta}
        for meta in tool_registry.values()
        if isinstance(meta, dict)  # schema dicts
    ]

    for iteration in range(budget.max_iterations):
        # --- Budget gate (checked BEFORE the call) ---
        exceeded = budget.check(iteration)
        if exceeded:
            logger.warning("Budget exceeded: %s", exceeded)
            return _finalize(trajectory, budget, exit_reason=exceeded)

        # --- LLM call ---
        t0 = time.monotonic()
        response = llm_client.chat(messages=messages, tools=tool_schemas)
        latency_ms = (time.monotonic() - t0) * 1000

        budget.record(response.input_tokens, response.output_tokens)
        output_hash = _hash_output(response.content_text)

        # --- Convergence check ---
        if detector.is_stuck(output_hash):
            logger.warning("Convergence stall detected at iteration %d", iteration)
            return _finalize(
                trajectory, budget, exit_reason="convergence_stall"
            )

        # --- Terminal check: model says it is done ---
        if response.stop_reason == "end_turn" and not response.tool_calls:
            step = TrajectoryStep(
                iteration=iteration,
                thought=response.content_text,
                action=None, action_args=None, observation=None,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=latency_ms,
                output_hash=output_hash,
            )
            trajectory.append(step)
            logger.info(
                "Final answer at iteration %d | cost=$%.4f | tokens=%d",
                iteration, budget.cost_used, budget.tokens_used,
            )
            return _finalize(
                trajectory, budget,
                answer=response.content_text, exit_reason="completed",
            )

        # --- Tool call branch ---
        tool_call = response.tool_calls[0]
        tool_name = tool_call.function.name
        tool_args = json.loads(tool_call.function.arguments)
        tool_fn = tool_registry.get(tool_name)

        if tool_fn is None:
            observation = f"Error: unknown tool '{tool_name}'"
        else:
            try:
                observation = str(tool_fn(**tool_args))
            except Exception as exc:
                observation = f"Tool error: {type(exc).__name__}: {exc}"

        step = TrajectoryStep(
            iteration=iteration,
            thought=response.content_text,
            action=tool_name,
            action_args=tool_args,
            observation=observation,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=latency_ms,
            output_hash=output_hash,
        )
        trajectory.append(step)

        # Append assistant message + tool result to conversation history
        messages.append({"role": "assistant", "content": response.raw_message})
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": observation,
        })

        logger.info(
            "iter=%d | action=%s | tokens=%d | cost=$%.4f | latency=%dms",
            iteration, tool_name, budget.tokens_used,
            budget.cost_used, int(latency_ms),
        )

    return _finalize(trajectory, budget, exit_reason="max_iterations_exhausted")


def _finalize(
    trajectory: list[TrajectoryStep],
    budget: AgentBudget,
    answer: str | None = None,
    exit_reason: str = "unknown",
) -> dict[str, Any]:
    return {
        "answer": answer,
        "trajectory": trajectory,
        "total_cost": round(budget.cost_used, 6),
        "total_tokens": budget.tokens_used,
        "iterations": len(trajectory),
        "exit_reason": exit_reason,
    }
```

### 5.2 Plan-and-Execute with Re-Planning

```python
"""
Plan-and-Execute agent with re-planning on step failure.
Uses a frontier model for planning, a cheaper model for execution.
"""
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.plan_execute")


@dataclass
class PlanStep:
    step_id: int
    instruction: str
    status: str = "pending"  # pending | running | completed | failed
    result: str | None = None
    model_used: str | None = None
    tokens: int = 0


@dataclass
class ExecutionPlan:
    steps: list[PlanStep] = field(default_factory=list)
    replan_count: int = 0
    max_replans: int = 2


def plan_and_execute(
    planner_client,      # Frontier model (GPT-4o, Claude Sonnet 4, etc.)
    executor_client,     # Cheap model (GPT-4o-mini, Haiku, etc.)
    tool_registry: dict,
    goal: str,
    system_prompt: str,
    max_replans: int = 2,
    max_steps_per_plan: int = 10,
) -> dict[str, Any]:
    """
    1. Planner generates a multi-step plan.
    2. Executor runs each step with tools.
    3. On failure, re-planner adjusts remaining steps.
    """
    plan = _generate_plan(planner_client, goal, system_prompt, max_steps_per_plan)
    total_tokens = 0
    all_results: list[dict[str, Any]] = []

    step_idx = 0
    while step_idx < len(plan.steps):
        step = plan.steps[step_idx]
        step.status = "running"
        logger.info("Executing step %d: %s", step.step_id, step.instruction)

        result = _execute_step(executor_client, tool_registry, step, goal)
        total_tokens += result["tokens"]
        step.tokens = result["tokens"]
        step.model_used = result["model"]

        if result["success"]:
            step.status = "completed"
            step.result = result["output"]
            all_results.append(result)
            step_idx += 1
        else:
            step.status = "failed"
            step.result = result["error"]
            logger.warning("Step %d failed: %s", step.step_id, result["error"])

            if plan.replan_count >= max_replans:
                logger.error("Max replans (%d) exhausted. Escalating.", max_replans)
                return {
                    "answer": None,
                    "plan": plan,
                    "results": all_results,
                    "total_tokens": total_tokens,
                    "exit_reason": "replan_limit_exhausted",
                }

            # Re-plan from current step onward
            completed = [s for s in plan.steps if s.status == "completed"]
            remaining_goal = _build_replan_context(goal, completed, step)
            new_steps = _generate_plan(
                planner_client, remaining_goal, system_prompt, max_steps_per_plan
            )
            plan.steps = [s for s in plan.steps if s.status == "completed"] + new_steps.steps
            plan.replan_count += 1
            step_idx = len([s for s in plan.steps if s.status == "completed"])
            logger.info("Re-planned (attempt %d). New plan has %d remaining steps.",
                        plan.replan_count, len(plan.steps) - step_idx)

    # Final synthesis
    final_answer = _synthesize(planner_client, goal, all_results)
    total_tokens += final_answer["tokens"]

    return {
        "answer": final_answer["output"],
        "plan": plan,
        "results": all_results,
        "total_tokens": total_tokens,
        "replans": plan.replan_count,
        "exit_reason": "completed",
    }


def _generate_plan(
    client, goal: str, system_prompt: str, max_steps: int,
) -> ExecutionPlan:
    """Ask the planner model to produce a structured multi-step plan."""
    prompt = (
        f"{system_prompt}\n\n"
        f"Break the following goal into {max_steps} or fewer concrete, "
        f"actionable steps. Return as a JSON array of strings.\n\n"
        f"Goal: {goal}"
    )
    response = client.chat(
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    steps_raw = response.parsed_json.get("steps", [])
    return ExecutionPlan(
        steps=[PlanStep(step_id=i, instruction=s) for i, s in enumerate(steps_raw)]
    )


def _execute_step(
    client, tool_registry: dict, step: PlanStep, original_goal: str,
) -> dict[str, Any]:
    """Execute a single plan step using the executor model with tools."""
    prompt = (
        f"You are executing one step of a larger plan.\n"
        f"Original goal: {original_goal}\n"
        f"Current step: {step.instruction}\n"
        f"Execute this step using available tools and return the result."
    )
    try:
        response = client.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=list(tool_registry.values()),
        )
        return {
            "success": True,
            "output": response.content_text,
            "tokens": response.input_tokens + response.output_tokens,
            "model": client.model_name,
            "error": None,
        }
    except Exception as exc:
        return {
            "success": False,
            "output": None,
            "tokens": 0,
            "model": client.model_name,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _build_replan_context(
    goal: str, completed: list[PlanStep], failed: PlanStep,
) -> str:
    completed_summary = "\n".join(
        f"  Step {s.step_id}: {s.instruction} -> {s.result}" for s in completed
    )
    return (
        f"Original goal: {goal}\n\n"
        f"Completed steps:\n{completed_summary}\n\n"
        f"Failed step: {failed.instruction}\n"
        f"Failure reason: {failed.result}\n\n"
        f"Generate a revised plan for the remaining work."
    )


def _synthesize(client, goal: str, results: list[dict]) -> dict[str, Any]:
    """Use the planner model to synthesize a final answer from all step results."""
    results_text = "\n".join(
        f"Step {i}: {r['output']}" for i, r in enumerate(results)
    )
    response = client.chat(messages=[{"role": "user", "content": (
        f"Goal: {goal}\n\nStep results:\n{results_text}\n\n"
        f"Synthesize a final, complete answer."
    )}])
    return {
        "output": response.content_text,
        "tokens": response.input_tokens + response.output_tokens,
    }
```

### 5.3 Reflection/Self-Correction Loop with External Grounding

```python
"""
Reflexion-style loop with external evaluator grounding.
Stores verbal critiques in episodic memory for cross-task reuse.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

logger = logging.getLogger("agent.reflexion")


class Evaluator(Protocol):
    """External evaluator: test runner, schema validator, retrieval checker."""
    def evaluate(self, output: str, context: dict[str, Any]) -> "EvalResult": ...


@dataclass
class EvalResult:
    passed: bool
    score: float          # 0.0 - 1.0
    feedback: str         # structured feedback from external signal
    error_details: str | None = None


@dataclass
class EpisodicMemory:
    """Stores verbal reflections indexed by task type for cross-episode reuse."""
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, task_type: str, reflection: str, score: float) -> None:
        self.entries.append({
            "task_type": task_type,
            "reflection": reflection,
            "score": score,
        })

    def retrieve(self, task_type: str, top_k: int = 3) -> list[str]:
        """Retrieve most relevant reflections for a task type."""
        relevant = [e for e in self.entries if e["task_type"] == task_type]
        relevant.sort(key=lambda x: x["score"])  # worst scores first (most instructive)
        return [e["reflection"] for e in relevant[:top_k]]


def reflexion_loop(
    actor_client,
    reflector_client,
    evaluator: Evaluator,
    goal: str,
    task_type: str,
    system_prompt: str,
    episodic_memory: EpisodicMemory,
    max_attempts: int = 3,
    pass_threshold: float = 0.8,
) -> dict[str, Any]:
    """
    Generate -> Evaluate (external) -> Reflect -> Regenerate.
    Stops when evaluator score exceeds pass_threshold or max_attempts reached.
    """
    prior_reflections = episodic_memory.retrieve(task_type)
    attempts: list[dict[str, Any]] = []

    for attempt in range(max_attempts):
        # --- Build actor prompt with episodic memory ---
        reflection_context = ""
        if prior_reflections:
            reflection_context = (
                "\n\nPrevious reflections on similar tasks:\n"
                + "\n".join(f"- {r}" for r in prior_reflections)
            )

        attempt_feedback = ""
        if attempts:
            last = attempts[-1]
            attempt_feedback = (
                f"\n\nPrevious attempt feedback:\n"
                f"Score: {last['score']}\n"
                f"Evaluator feedback: {last['eval_feedback']}\n"
                f"Self-reflection: {last['reflection']}"
            )

        actor_prompt = (
            f"{system_prompt}\n\n"
            f"Goal: {goal}"
            f"{reflection_context}"
            f"{attempt_feedback}"
        )

        # --- Generate ---
        response = actor_client.chat(
            messages=[{"role": "user", "content": actor_prompt}]
        )
        output = response.content_text

        # --- Evaluate (EXTERNAL -- not the LLM judging itself) ---
        eval_result = evaluator.evaluate(output, {"goal": goal, "attempt": attempt})
        logger.info(
            "Attempt %d | score=%.2f | passed=%s",
            attempt, eval_result.score, eval_result.passed,
        )

        if eval_result.passed and eval_result.score >= pass_threshold:
            attempts.append({
                "output": output, "score": eval_result.score,
                "eval_feedback": eval_result.feedback,
                "reflection": None,
            })
            return {
                "answer": output,
                "attempts": attempts,
                "exit_reason": "passed",
                "final_score": eval_result.score,
            }

        # --- Reflect (verbal critique grounded in evaluator feedback) ---
        reflect_prompt = (
            f"You attempted the following task and received external feedback.\n\n"
            f"Task: {goal}\n"
            f"Your output:\n{output}\n\n"
            f"External evaluator feedback:\n{eval_result.feedback}\n"
            f"{'Error details: ' + eval_result.error_details if eval_result.error_details else ''}\n\n"
            f"Analyze specifically what went wrong and what to change next time. "
            f"Be concrete -- reference specific parts of your output and the feedback."
        )
        reflection_response = reflector_client.chat(
            messages=[{"role": "user", "content": reflect_prompt}]
        )
        reflection = reflection_response.content_text

        attempts.append({
            "output": output,
            "score": eval_result.score,
            "eval_feedback": eval_result.feedback,
            "reflection": reflection,
        })

        # Store reflection for future episodes
        episodic_memory.add(task_type, reflection, eval_result.score)
        prior_reflections.append(reflection)

        logger.info("Reflection stored. Retrying (attempt %d/%d).", attempt + 1, max_attempts)

    return {
        "answer": attempts[-1]["output"] if attempts else None,
        "attempts": attempts,
        "exit_reason": "max_attempts_exhausted",
        "final_score": attempts[-1]["score"] if attempts else 0.0,
    }
```

### 5.4 Circuit Breaker for Agent Iterations

```python
"""
Circuit breaker pattern for agent loops.
Prevents cascading failures when a tool or LLM endpoint is degraded.
States: CLOSED (normal) -> OPEN (failing, block calls) -> HALF_OPEN (probe).
"""
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("agent.circuit_breaker")


class CircuitState(Enum):
    CLOSED = "closed"         # Normal operation
    OPEN = "open"             # Failing -- block all calls
    HALF_OPEN = "half_open"   # Probing -- allow one test call


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3        # consecutive failures to trip
    recovery_timeout_s: float = 30.0  # seconds before probing
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    success_count_in_half_open: int = 0
    half_open_success_threshold: int = 2  # successes to close

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Execute fn through the circuit breaker.
        Raises CircuitOpenError if the circuit is open.
        """
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.last_failure_time >= self.recovery_timeout_s:
                logger.info("Circuit entering HALF_OPEN state for probing.")
                self.state = CircuitState.HALF_OPEN
                self.success_count_in_half_open = 0
            else:
                remaining = self.recovery_timeout_s - (time.monotonic() - self.last_failure_time)
                raise CircuitOpenError(
                    f"Circuit is OPEN. Retry in {remaining:.1f}s."
                )

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.success_count_in_half_open += 1
            if self.success_count_in_half_open >= self.half_open_success_threshold:
                logger.info("Circuit CLOSED after %d successful probes.",
                            self.success_count_in_half_open)
                self.state = CircuitState.CLOSED
                self.failure_count = 0
        else:
            self.failure_count = 0

    def _on_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.state == CircuitState.HALF_OPEN:
            logger.warning("Probe failed. Circuit returning to OPEN.")
            self.state = CircuitState.OPEN
        elif self.failure_count >= self.failure_threshold:
            logger.warning(
                "Circuit OPEN after %d consecutive failures.", self.failure_count
            )
            self.state = CircuitState.OPEN


class CircuitOpenError(Exception):
    """Raised when calling through an open circuit breaker."""
    pass


# --- Integration with ReAct loop ---

def react_loop_with_circuit_breaker(
    llm_client,
    tool_registry: dict,
    goal: str,
    system_prompt: str,
    llm_breaker: CircuitBreaker | None = None,
    tool_breakers: dict[str, CircuitBreaker] | None = None,
) -> dict[str, Any]:
    """
    Wraps LLM calls and tool calls through circuit breakers.
    If the LLM circuit opens, the loop halts with a partial result.
    If a tool circuit opens, the tool is reported as unavailable.
    """
    llm_breaker = llm_breaker or CircuitBreaker(failure_threshold=3)
    tool_breakers = tool_breakers or {}

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": goal},
    ]

    for iteration in range(10):
        # LLM call through circuit breaker
        try:
            response = llm_breaker.call(
                llm_client.chat, messages=messages
            )
        except CircuitOpenError as exc:
            return {
                "answer": None,
                "exit_reason": f"llm_circuit_open: {exc}",
                "iterations": iteration,
            }

        if response.stop_reason == "end_turn" and not response.tool_calls:
            return {
                "answer": response.content_text,
                "exit_reason": "completed",
                "iterations": iteration + 1,
            }

        # Tool call through per-tool circuit breaker
        tool_call = response.tool_calls[0]
        tool_name = tool_call.function.name
        breaker = tool_breakers.get(tool_name, CircuitBreaker())

        try:
            tool_fn = tool_registry[tool_name]
            observation = breaker.call(tool_fn, **tool_call.parsed_args)
        except CircuitOpenError:
            observation = f"Tool '{tool_name}' is temporarily unavailable (circuit open)."
        except KeyError:
            observation = f"Unknown tool: {tool_name}"
        except Exception as exc:
            observation = f"Tool error: {type(exc).__name__}: {exc}"

        messages.append({"role": "assistant", "content": response.raw_message})
        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(observation)})

    return {"answer": None, "exit_reason": "max_iterations", "iterations": 10}
```

### 5.5 Budget Enforcement and Cost Tracking

```python
"""
Pre-call budget enforcement with structured cost tracking.
Blocks the API call rather than alerting after cost has accumulated.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.budget")


@dataclass
class CostTracker:
    """
    Tracks cumulative cost and enforces ceilings BEFORE each API call.

    Pricing is configurable per model tier. The tracker estimates
    the cost of the next call before it happens, using the measured
    input token count and an estimated output token count.
    """
    ceiling_usd: float = 5.0
    ceiling_tokens: int = 500_000
    wall_clock_limit_s: float = 300.0  # 5 minutes

    # Pricing: model_name -> (input_price_per_token, output_price_per_token)
    pricing: dict[str, tuple[float, float]] = field(default_factory=lambda: {
        "gpt-4o": (2.5e-6, 10.0e-6),
        "gpt-4o-mini": (0.15e-6, 0.60e-6),
        "claude-sonnet-4-20250514": (3.0e-6, 15.0e-6),
        "claude-opus-4-20250514": (15.0e-6, 75.0e-6),
        "claude-haiku-3.5": (0.80e-6, 4.0e-6),
    })

    # Accumulators
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    call_count: int = 0
    start_time: float = field(default_factory=time.monotonic)
    ledger: list[dict[str, Any]] = field(default_factory=list)

    def pre_call_check(
        self,
        model: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int = 500,
    ) -> None:
        """
        Raise BudgetExceededError BEFORE the call if the estimated cost
        would push us over the ceiling. This is enforcement, not alerting.
        """
        input_price, output_price = self.pricing.get(model, (10.0e-6, 30.0e-6))
        estimated_cost = (
            estimated_input_tokens * input_price
            + estimated_output_tokens * output_price
        )

        if self.total_cost + estimated_cost > self.ceiling_usd:
            raise BudgetExceededError(
                f"Estimated call cost ${estimated_cost:.4f} would exceed "
                f"ceiling ${self.ceiling_usd:.2f} "
                f"(current: ${self.total_cost:.4f})"
            )

        projected_tokens = (
            self.total_input_tokens + self.total_output_tokens
            + estimated_input_tokens + estimated_output_tokens
        )
        if projected_tokens > self.ceiling_tokens:
            raise BudgetExceededError(
                f"Projected token usage {projected_tokens:,} would exceed "
                f"ceiling {self.ceiling_tokens:,}"
            )

        elapsed = time.monotonic() - self.start_time
        if elapsed > self.wall_clock_limit_s:
            raise BudgetExceededError(
                f"Wall clock {elapsed:.0f}s exceeds limit {self.wall_clock_limit_s:.0f}s"
            )

    def record_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        step_label: str = "",
    ) -> None:
        """Record actual usage after a successful call."""
        input_price, output_price = self.pricing.get(model, (10.0e-6, 30.0e-6))
        cost = input_tokens * input_price + output_tokens * output_price

        self.total_cost += cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.call_count += 1

        entry = {
            "call_number": self.call_count,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
            "cumulative_cost_usd": round(self.total_cost, 6),
            "latency_ms": round(latency_ms, 1),
            "step_label": step_label,
        }
        self.ledger.append(entry)
        logger.info(
            "Call %d | model=%s | tokens=%d+%d | cost=$%.4f | cumulative=$%.4f | %s",
            self.call_count, model, input_tokens, output_tokens,
            cost, self.total_cost, step_label,
        )

    def summary(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self.start_time
        return {
            "total_cost_usd": round(self.total_cost, 4),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_calls": self.call_count,
            "wall_clock_s": round(elapsed, 1),
            "avg_cost_per_call": round(self.total_cost / max(self.call_count, 1), 6),
            "utilization_pct": round(self.total_cost / self.ceiling_usd * 100, 1),
        }


class BudgetExceededError(Exception):
    """Raised when a pre-call budget check fails."""
    pass
```

### 5.6 Structured Logging with Trajectory Tracing

```python
"""
Structured logging for agent loops with OpenTelemetry-style trace correlation.
Every log entry carries a trace_id and step_id for end-to-end debugging.
"""
import json
import logging
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

# Context variables for trace propagation
_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_step_id: ContextVar[int] = ContextVar("step_id", default=0)


class AgentTraceFormatter(logging.Formatter):
    """JSON structured formatter that injects trace context into every log line."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": _trace_id.get(""),
            "step_id": _step_id.get(0),
            "message": record.getMessage(),
        }
        # Merge any extra fields attached to the record
        if hasattr(record, "agent_data"):
            log_entry.update(record.agent_data)
        return json.dumps(log_entry, default=str)


def setup_agent_logging(level: int = logging.INFO) -> None:
    """Configure structured JSON logging for agent modules."""
    handler = logging.StreamHandler()
    handler.setFormatter(AgentTraceFormatter())
    root = logging.getLogger("agent")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


@dataclass
class TraceContext:
    """Manages trace correlation for a single agent task."""
    trace_id: str
    logger: logging.Logger

    @classmethod
    def new(cls, logger_name: str = "agent") -> "TraceContext":
        trace_id = uuid.uuid4().hex[:16]
        _trace_id.set(trace_id)
        _step_id.set(0)
        return cls(trace_id=trace_id, logger=logging.getLogger(logger_name))

    def step(self, step_num: int) -> None:
        _step_id.set(step_num)

    def log_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        stop_reason: str,
    ) -> None:
        self.logger.info(
            "LLM call completed",
            extra={"agent_data": {
                "event": "llm_call",
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": round(latency_ms, 1),
                "stop_reason": stop_reason,
            }},
        )

    def log_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        result_length: int,
        latency_ms: float,
        success: bool,
    ) -> None:
        self.logger.info(
            "Tool call %s", "succeeded" if success else "failed",
            extra={"agent_data": {
                "event": "tool_call",
                "tool": tool_name,
                "args_keys": list(args.keys()),
                "result_length": result_length,
                "latency_ms": round(latency_ms, 1),
                "success": success,
            }},
        )

    def log_budget(self, cost_usd: float, tokens: int, ceiling_usd: float) -> None:
        self.logger.info(
            "Budget checkpoint",
            extra={"agent_data": {
                "event": "budget_check",
                "cost_usd": round(cost_usd, 4),
                "tokens": tokens,
                "ceiling_usd": ceiling_usd,
                "utilization_pct": round(cost_usd / ceiling_usd * 100, 1),
            }},
        )

    def log_termination(self, reason: str, iterations: int, total_cost: float) -> None:
        self.logger.info(
            "Agent loop terminated: %s", reason,
            extra={"agent_data": {
                "event": "termination",
                "reason": reason,
                "iterations": iterations,
                "total_cost_usd": round(total_cost, 4),
            }},
        )
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Autonomous Code Review Pipeline

**Problem statement**: A financial services firm processes 200+ pull requests/day across 15 microservice repositories. Human reviewers spend 40% of review time on mechanical issues (style violations, unused imports, common vulnerability patterns, secret exposure). The security team requires every PR touching payment or PII services to pass an automated security check before merge. Current SAST tools produce high false-positive rates (30%+), causing alert fatigue.

**Proposed architecture**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PR WEBHOOK (GitHub)                             │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 v
┌─────────────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR (LangGraph DAG)                         │
│                                                                         │
│  - Classify PR: {payment, pii, general}                                 │
│  - Select agent set based on classification                             │
│  - Fan-out to parallel sub-agents                                       │
│  - Fan-in: merge, deduplicate, rank findings                            │
│  - Global timeout: 120s                                                 │
└────┬──────────────┬──────────────┬──────────────────────────────────────┘
     │              │              │
     v              v              v
┌─────────┐  ┌───────────┐  ┌────────────┐
│ Security │  │  Style    │  │ Performance│
│ Agent    │  │  Agent    │  │ Agent      │
│          │  │           │  │            │
│ ReAct    │  │ ReAct     │  │ ReAct      │
│ max=5    │  │ max=3     │  │ max=3      │
│          │  │           │  │            │
│ Tools:   │  │ Tools:    │  │ Tools:     │
│ Semgrep  │  │ ruff      │  │ complexity │
│ CodeQL   │  │ mypy      │  │ profiler   │
│ Bandit   │  │ formatter │  │ benchmarks │
│ TruffleH.│  │           │  │            │
└────┬─────┘  └─────┬─────┘  └─────┬──────┘
     │              │              │
     v              v              v
┌─────────────────────────────────────────────────────────────────────────┐
│                    SYNTHESIZER (merge + deduplicate)                     │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 v
┌─────────────────────────────────────────────────────────────────────────┐
│              SELF-REVIEW (Reflexion pass, fresh context)                 │
│              - Evaluator: re-run SAST tools on flagged lines            │
│              - Remove low-confidence findings                           │
│              - Result: high-precision finding set                        │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                        ┌────────┴────────┐
                        │                 │
                        v                 v
                ┌──────────────┐  ┌──────────────────┐
                │ severity <=  │  │ severity > WARN  │
                │ WARN: auto-  │  │ (payment/PII):   │
                │ post as PR   │  │ HITL approval    │
                │ comments     │  │ gate before      │
                └──────────────┘  │ auto-fix applied │
                                  └──────────────────┘
```

**Technology choices**: LangGraph for orchestration (checkpointing, fan-out/fan-in, conditional edges). GPT-4o for sub-agent reasoning. Each sub-agent gets only the diff as context (subagent isolation). SAST tools provide external grounding -- the LLM interprets results, not generates vulnerability findings.

**Trade-off evaluation matrix**:

| Dimension | A: Monolithic ReAct Agent | B: Parallel Sub-Agents (Proposed) | C: LATS Tree Search |
|---|---|---|---|
| **Cost per PR** | $0.08-0.15 (single long context) | $0.04-0.06 (parallel, isolated context) | $0.50-2.00 (tree branching) |
| **Latency** | 30-60s (sequential) | 10-20s (parallel) | 2-5 min |
| **Precision** | Medium (context pollution across domains) | High (domain isolation + Reflexion filter) | Highest (exhaustive search) |
| **Ops complexity** | Low (single agent) | Medium (orchestrator + 3 agents) | High (tree state management) |
| **Scalability ceiling** | ~100 PRs/day (context bottleneck) | ~500+ PRs/day (horizontal parallelism) | ~20 PRs/day (cost prohibitive) |
| **Security posture** | Weak (LLM-generated findings only) | Strong (SAST-grounded, HITL for critical) | Strong (exhaustive, but overkill) |

**Decision rationale**: Option B wins because it isolates concerns (each agent focuses on one domain with clean context), grounds findings in real tool output (not LLM hallucination), and the Reflexion self-review pass eliminates low-confidence findings that cause alert fatigue. The parallel architecture keeps latency under 20s per PR, which is fast enough for CI/CD gating. Cost at $0.05/PR translates to ~$10/day for 200 PRs -- negligible versus the engineering time saved. The HITL gate for payment/PII PRs satisfies the compliance requirement without rubber-stamping low-risk changes. Option C (LATS) produces marginally higher accuracy but at 10-40x the cost, making it impractical for high-volume PR review.

---

### 6.2 Scenario: Multi-Source Data Pipeline with Intelligent Recovery

**Problem statement**: A healthcare analytics company runs a daily ETL pipeline ingesting data from 5 heterogeneous sources (EHR system via FHIR API, claims database, lab results SFTP, patient surveys via REST API, and a third-party pharmacy feed). Steps have dependencies (transforms require all extractions; quality checks require completed loads). The pipeline runs overnight; a failure at step 4 of 6 currently requires a full restart, wasting 45 minutes and $50+ in compute. Regulatory requirements (HIPAA) mandate audit trails for every data access and transformation decision.

**Proposed architecture**:

```
┌──────────────────────────────────────────────────────────────────────┐
│                 PLANNER AGENT (Claude Sonnet 4)                      │
│  - Generates execution plan as DAG                                   │
│  - Called twice: initial plan + re-plan on failure                    │
│  - Receives: source catalog, schema registry, prior run history      │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  plan = [{step_id, deps, source, ...}]
                             v
┌──────────────────────────────────────────────────────────────────────┐
│            TEMPORAL WORKFLOW (Durable Execution Runtime)              │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  EXTRACT PHASE (Parallel Activities, individually retried)   │    │
│  │                                                              │    │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐    │    │
│  │  │ EHR    │ │ Claims │ │ Labs   │ │ Survey │ │ Pharma │    │    │
│  │  │ (FHIR) │ │ (SQL)  │ │ (SFTP) │ │ (REST) │ │ (Feed) │    │    │
│  │  │ 3x     │ │ 3x     │ │ 3x     │ │ 3x     │ │ 3x     │    │    │
│  │  │ retry  │ │ retry  │ │ retry  │ │ retry  │ │ retry  │    │    │
│  │  └────┬───┘ └────┬───┘ └────┬───┘ └────┬───┘ └────┬───┘    │    │
│  │       └──────┴──────┴────┬───┴──────┴───┘                    │    │
│  └──────────────────────────┼───────────────────────────────────┘    │
│                             v                                        │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  TRANSFORM PHASE (Haiku / deterministic code)                │    │
│  │  - Schema validation against FHIR R4 + internal schemas      │    │
│  │  - PHI de-identification check                               │    │
│  │  - Data type coercion + null handling                        │    │
│  │  - Idempotent: deterministic transform ID per record batch   │    │
│  └──────────────────────────┬───────────────────────────────────┘    │
│                             v                                        │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  LOAD PHASE (Idempotent upserts with idempotency keys)       │    │
│  │  - Snowflake / BigQuery warehouse                            │    │
│  │  - Merge statements with dedup on record_id + batch_id       │    │
│  └──────────────────────────┬───────────────────────────────────┘    │
│                             v                                        │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  QUALITY CHECK (SQL assertions -- external grounding)        │    │
│  │  - Row count delta < 5% from prior run                       │    │
│  │  - No NULL in required fields                                │    │
│  │  - Referential integrity across dimension tables              │    │
│  │  - Statistical distribution checks (z-score outlier detect)  │    │
│  └──────┬──────────────────────────────────┬────────────────────┘    │
│         │ PASS                             │ FAIL                    │
│         v                                  v                         │
│  ┌──────────────┐                 ┌──────────────────────────┐      │
│  │ Notify       │                 │ RE-PLANNER               │      │
│  │ stakeholders │                 │ (Sonnet 4, attempt <= 2) │      │
│  │ (Slack, PD)  │                 │ - Diagnose from QC output│      │
│  └──────────────┘                 │ - Generate corrective plan│     │
│                                   │ - If attempt > 2: HITL   │      │
│                                   └──────────────────────────┘      │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  AUDIT LOG (hash-chained, HIPAA-compliant)                   │    │
│  │  - Every data access: source, timestamp, record count, agent │    │
│  │  - Every transform decision: input hash, output hash, rule   │    │
│  │  - Every approval/denial/escalation with full context        │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

**Technology choices**: Temporal for durable execution (crash at step 4 resumes at step 4, not step 1). Claude Sonnet 4 for planning/re-planning (strong at structured output, cost-efficient for 2-3 calls). Claude Haiku 3.5 or deterministic code for transforms (80% cost savings vs. frontier model). SQL assertions for quality checks (external grounding -- not LLM judgment). Hash-chained audit log satisfying HIPAA access logging requirements.

**Trade-off evaluation matrix**:

| Dimension | A: Airflow + Static DAG | B: Plan-and-Execute + Temporal (Proposed) | C: Full ReAct per Source |
|---|---|---|---|
| **Recovery cost** | High ($50+ on restart) | Low ($0.10, resume from checkpoint) | Medium ($15, per-source restart) |
| **Adaptability** | None (static DAG, manual fix) | High (AI re-planner adjusts to failures) | High (per-step reasoning) |
| **Latency (normal run)** | 45 min (optimized, parallel) | 50 min (planning overhead) | 90 min (LLM call per step) |
| **Ops complexity** | Low (mature tooling) | Medium (Temporal cluster + AI planner) | High (5 independent agent loops) |
| **Cost per run** | ~$0 AI + $50 compute | ~$0.10 AI + $50 compute | ~$5.00 AI + $50 compute |
| **Audit/compliance** | Manual logging, gaps | Hash-chained, automatic, HIPAA-ready | Difficult (scattered across agents) |
| **Scalability** | High (Airflow proven at scale) | High (Temporal handles 10K+ workflows) | Low (token cost scales badly) |

**Decision rationale**: Option B wins because durable execution via Temporal eliminates the $50 restart waste on failure (the pipeline resumes from the last checkpoint), while the AI planner provides intelligent recovery that a static Airflow DAG cannot match. The cost delta is negligible ($0.10/run for AI planning). The hash-chained audit log satisfies HIPAA requirements structurally rather than through manual logging discipline. Option A is simpler but cannot adapt to novel failures -- every new failure mode requires an engineer to update the DAG. Option C (full ReAct per source) adds $5/run in AI costs and doubles latency without proportional quality improvement for a pipeline where most steps are deterministic transforms. The re-planning cap (2 attempts before HITL escalation) prevents the $47,000 infinite loop scenario while keeping humans in the loop for genuinely ambiguous failures.

---

## Quick Reference: Pattern Selection Decision Tree

```
Is the next action predictable from the current state?
├── YES: Are steps independent with a known dependency graph?
│   ├── YES: Plan-and-Execute (or static DAG if fully deterministic)
│   └── NO: Plan-and-Execute with re-planning
└── NO: Does the task require exploration / dynamic tool selection?
    ├── YES: Is single-pass accuracy critical (>95% required)?
    │   ├── YES: LATS (accept the token cost)
    │   └── NO: ReAct (with Focused ReAct if drift is a concern)
    └── NO: Is there a reliable external evaluator?
        ├── YES: Reflexion (generate-evaluate-reflect loop)
        └── NO: ReAct + grounded self-correction (external signal required)
```
