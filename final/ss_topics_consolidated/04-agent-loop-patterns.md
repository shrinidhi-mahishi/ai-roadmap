# Topic 4: Agent Loop Patterns
> Consolidated from multi-model research (Grok + Opus) | Study & Interview Prep

---

## Introduction

### What This Topic Covers

Agent loop patterns are the **core execution architectures** that make AI agents work. This topic covers the five major loop patterns — ReAct (Reasoning + Acting), Plan-and-Execute, Reflexion, Self-Correction, and LATS (Language Agent Tree Search) — along with their production failure modes, token economics (the quadratic cost problem), durable execution with Temporal/Kafka, enterprise governance with human-in-the-loop, and framework-level implementations across LangGraph, OpenAI Agents SDK, CrewAI, and Google ADK.

### Why Study This

- **The heart of agentic AI**: Every agent — from a simple chatbot to an autonomous code reviewer — runs one of these loop patterns. Choosing the right pattern for the right task is a core architectural decision.
- **Cost trap**: Agent loops have a **quadratic cost problem** — each iteration re-sends all prior context. An 8-step GPT-4o task costs ~$0.116, not the $0.015 you'd expect from linear scaling. Interviewers probe whether you understand this.
- **Production danger zone**: 68 confirmed infinite loop failures across 47 projects (IAL-Scan study). The $47K runaway incident. 65% of enterprise AI failures trace to context drift. Knowing failure modes and their mitigations is essential.
- **Interview depth**: Expect questions on when ReAct beats Plan-and-Execute (and vice versa), the self-correction stability threshold (ECR/EIR > Acc/(1-Acc)), and why debate doesn't consistently outperform chain-of-thought (ICLR 2025 MAD findings).

### What Details Are Included

- Five loop patterns with state machine diagrams, benchmarks, and when-to-use guidance
- Framework comparison table (LangGraph, OpenAI Agents SDK, CrewAI, Google ADK, Anthropic)
- Quadratic cost derivation with worked examples
- Durable execution with Temporal (replay semantics, Continue-As-New)
- Human-in-the-loop with risk-tiered approval gates
- Real-world cost incidents ($47K, $4.2K weekend, 27M-token Claude Code loop)
- Production Python code for ReAct, Plan-and-Execute, and Reflexion loops
- Two enterprise system design scenarios with trade-off matrices

### How to Approach This Document

> **First pass (2-3 hours)**: Sections 1-4. Draw the state machine for each loop pattern from memory. Focus on the comparison table — know when to use each pattern and why.
>
> **Second pass (2-3 hours)**: Sections 5-7. Work through the quadratic cost derivation. Run the ReAct and Plan-and-Execute code. Study the failure taxonomy — especially infinite loops and context exhaustion.
>
> **Interview prep (1 hour)**: Section 10 (Interview Quick Reference). Practice articulating the tradeoff between ReAct (flexible, expensive) and Plan-and-Execute (cheaper, less adaptive). Memorize the cost incidents.
>
> **Before an interview (30 min)**: Re-read section 10 only.

### How This Document Is Structured

This guide follows a **10-section progressive learning flow** — each section builds on the previous:

| # | Section | What It Covers | Study Approach |
|---|---------|---------------|----------------|
| 1 | Concept Overview | What and why | Read first for orientation |
| 2 | Core Concepts | Fundamental building blocks | Study deeply, take notes |
| 3 | Architecture & System Design | ASCII diagrams, topology, data flow | Draw diagrams from memory |
| 4 | Key Algorithms & Mechanics | Technical depth, complexity analysis | Understand the "why" |
| 5 | Token Economics & Cost Analysis | Pricing, cost formulas, optimization | Memorize key numbers |
| 6 | Production Patterns & Code | Runnable Python implementations | Run, modify, and break the code |
| 7 | Failure Modes & Mitigations | What goes wrong, how to handle it | Practice explaining failure scenarios |
| 8 | Security & Governance | Enterprise security considerations | Know compliance frameworks by name |
| 9 | System Design Scenarios | Real-world problems with trade-offs | Practice whiteboarding these |
| 10 | Interview Quick Reference | Key numbers, frameworks, talking points | Review 30 min before interviews |

---

**Scope**: Single-agent loop architectures -- ReAct, Plan-and-Execute, Reflexion, Self-Correction, LATS -- with production failure modes, token economics, durable execution, enterprise governance, and framework-level implementation details.

**Prerequisite modules**: Vendor list prices, tokenizer tables, and SDK HTTP retry constants live in `01-python-llm-foundations.md`. Prefix-stability, cache breakpoints, and the tool-schema tax live in `02-context-engineering.md`. JSON Schema / `strict` / dispatcher IDs / `is_error` mapping live in `03-tool-calling.md`.

**Core principle**: The model **does not execute tools or terminate the process**. It emits structured actions (or text); **your** runtime decides whether to dispatch, inject an observation, replan, interrupt a human, or halt. Collapsing `max_turns` (model invocations), `recursion_limit` (Pregel super-steps), and `max_budget_usd` (client-side dollars) into one "iteration cap" is how teams ship infinite spend.

---

## 1. Concept Overview

### What Is an Agent Loop?

An **agent loop** is a control-plane state machine wrapped around a growing transcript. The model samples thoughts and actions; the runtime dispatches tools, enforces budgets, and decides when to stop. Production is a **deterministic outer graph** wrapping a model-chosen inner loop, with hop / dollar / wall-clock fuses plus a `(tool, canonical_args)` hash circuit.

**Five core patterns** exist, each with different cost, latency, and reliability profiles:

| Pattern | Core Idea | When to Use |
|---|---|---|
| **ReAct** | Interleave Thought-Action-Observation until done | Dynamic, exploratory tasks where next action depends on last observation |
| **Plan-and-Execute** | Emit a plan/DAG first, then workers execute | Many independent calls; predictable multi-step workflows |
| **Reflexion** | Generate-Evaluate-Reflect across trials | Tasks with reliable external evaluators (tests, validators) |
| **Self-Correction** | Revise output using feedback | Only with grounded external signals, never oracle-free LLM judgment |
| **LATS** | Monte Carlo Tree Search over thought/action space | High-value, accuracy-critical tasks where cost is secondary |

### Why It Matters

The difference between a controlled agent and a cost disaster is the loop design:

- **Runaway cost**: OpenAI Agents SDK defaults to `max_turns=10`. A cached GPT-6-sol short-context toy at 10 hops costs **~$85/1k runs**; a 25-hop runaway costs **~$187/1k** -- **2.2x** more. A 1k-ticket/day support mix costs **~$31/day**; the same fleet with runaway 25-hop agents costs **~$187/day (6x)**.
- **Token waste**: ReWOO showed interleaved ReAct uses **5x** the tokens of batched plans (HotpotQA gpt-3.5: **9,795** vs **1,986** tokens; **$19.59** vs **$3.97 per 1k queries**).
- **Speed**: LLMCompiler Movie Rec is **6.73x** cheaper and **3.74x** faster than ReAct at **higher** accuracy.
- **Self-harm**: Huang et al. showed GPT-4 GSM8K accuracy drops from **95.5 to 89.0** after 5 oracle-free self-correction calls -- you paid **5x** to **lose 6.5 percentage points**.
- **Infinite loops**: 47% of HotpotQA ReAct failures include **repetitive Thought-Action-Observation** -- the model will not stop itself. IAL-Scan examined 6,549 LLM agent repositories and found **68 confirmed infinite agentic loop failures** across 47 projects.
- **Enterprise exposure**: 96% of enterprises reported AI costs exceeding initial projections. Multi-agent LLM systems fail **41-86%** of the time in production. Gartner predicts over 40% of agentic AI projects will be canceled by 2027 due to missing resilience infrastructure.

### Interview Traps (fail these, fail the round)

- Treating **"turn"** as portable: OpenAI = one model invocation **including** parallel tools; Claude Agent SDK `max_turns` = **tool-use round trips only** (no default; `max_turns=2` can stop before an edit); LangGraph `recursion_limit` = **super-steps** (ReAct uses ~2 per tool round).
- `max_turns=None` / `LoopAgent` without `max_iterations` **and** without `escalate` = unbounded spend.
- Deep Agents **parent** 1000/2000/9999 **does not propagate** to subagents (still reported hitting **25**).
- Claude `max_budget_usd` checked **after** a call -- can overshoot by **one** API call; no `result` text on `error_max_turns`.
- `pause_turn` treated as `end_turn` (Anthropic **server-tool** inner cap -- must resend assistant content).
- Mixing REFLECT rewrite of PLAN in the same forward pass that calls Stripe (control vs data plane).
- Self-Refine / Huang loop on math without an oracle; Reflexion **without tests** **hurts** (52% vs 60%).
- Replan **every** hop = you paid for a planner and got ReAct. Cap `max_replans` at **2-3**.
- Observation that injects a **new goal** (email, new vendor) = PlanFlip / LLM06; **freeze** the user objective.
- Hash-circuit missing: greedy ReAct repeats `(tool, args)`; AutoGPT spent **51+ min** / **21+** empty `input: {}`.

---

## 2. Core Concepts

### 2.1 ReAct: Reasoning + Acting

**Paper**: Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models" (ICLR 2023).

**How it works**: The model alternates between language **thoughts** (reasoning that does not touch the environment) and domain **actions** (tool calls that do). Each cycle is: `Thought -> Action -> Observation`.

```
          +-------------------------------------+
          |                                     |
          v                                     |
    +----------+     +----------+     +---------+--+
    |  THOUGHT  |---->|  ACTION  |---->| OBSERVATION |
    |  (reason) |     |  (tool)  |     |  (result)   |
    +----------+     +----------+     +------------+
          |
          | stop_reason == "end_turn"
          v
    +----------+
    |  ANSWER  |
    +----------+
```

**Why it works**: Without alternation, agents either hallucinate answers (reasoning without grounding) or execute tools blindly (acting without interpretation). ReAct grounds reasoning in real-world observations. HotpotQA: ReAct failure-hallucination rate is **0%** vs CoT's **56%**.

**Key results (PaLM-540B)**:
- HotpotQA: ReAct **loses** EM to CoT (27.4 vs 29.4) but **wins** FEVER (60.9 vs 56.3). Best is the **hybrid** (ReAct + CoT-SC fallback).
- ALFWorld: ReAct **71** vs BUTLER **37** (+34 pp). Sparse thoughts beat dense inner-monologue (**71** vs **53**).
- Paper loop caps: HotpotQA **7** steps, FEVER **5**, ALFWorld **49**. Of already-correct trajectories, full 7/5 steps were only needed **0.84% / 1.33%** of the time.
- **47%** of ReAct failures include **repetitive TAO** -- the model will not stop itself. Production implication: **external loop breaker required**.

**Error compounding**: If each step has 95% reliability, a 10-step task succeeds only ~60% of the time (0.95^10 = 0.5987). This is the fundamental reliability constraint of sequential loops.

**Variants (2024-2026)**:
- **RP-ReAct**: Decouples planning from execution. A Reasoner-Planner handles strategy; Proxy-Execution agents each run internal ReAct loops with context-saving strategies.
- **Focused ReAct**: Reiterates the original question at each step + early-stops on repetitive actions. Reported **530% relative accuracy gains** and **34% runtime reduction** in low-resource models.

**When to use**: Dynamic, exploratory, open-ended tasks where the next action depends heavily on the previous observation. **Not** for long, predictable workflows (use Plan-and-Execute) or when many independent searches can be parallelized.

### 2.2 Plan-and-Execute / ReWOO / LLMCompiler

**Papers**: Wang et al., "Plan-and-Solve Prompting" (ACL 2023); Xu et al., "ReWOO" (2023); Kim et al., "LLMCompiler" (2023).

**Two-phase architecture**:

```
+-----------+          +----------------+          +-----------+
|  PLANNER  |--------->|   EXECUTOR     |--------->| RE-PLANNER|
| (frontier |  plan[]  | (smaller model |  results | (frontier |
|  model)   |          |  / determin.)  |          |  model)   |
+-----------+          +----------------+          +-----------+
      ^                                                   |
      +---------------------------------------------------+
                       (on failure / deviation)
```

**Why it is cheaper**: The expensive frontier model is called only for planning and re-planning (typically 2-3 calls). The key insight is **do not re-send the planner prefix every search**.

**Plan-and-Solve / PS+** is a **single generation** ("devise a plan, then carry it out") -- not a tool loop unless you split nodes. LangGraph P&E: `planner -> agent(plan[0]) -> replan -> END or agent`. State: `input`, `plan`, `past_steps`, `response`. Template replans **after every executed step** -- cap `max_replans` yourself.

**ReWOO** (Reasoning WithOut Observation): Planner emits `(Plan, #E_s)` placeholders; workers fill evidence **without** the planner seeing observations.
- HotpotQA gpt-3.5: **42.4** acc / **1,986** tokens / **$3.97/1k queries** vs ReAct **40.8** / **9,795** / **$19.59**.
- Tool-failure ablation: ReAct loses **-40.8** acc, ReWOO only **-29.2**. Extraneous tools hurt.

**LLMCompiler**: Planner DAG with `$k` dependencies -> Task Fetching Unit -> concurrent Executor -> optional Joiner.
- vs ReAct: up to **3.7x** latency, **6.7x** cost, **~9 pp** accuracy improvement.
- HotpotQA: **3.37x** cheaper, **1.80x** faster. Movie Rec: **6.73x** / **3.74x**.
- ~**10%** of HotpotQA ReAct needed **>4** calls (looped/divergent).
- WebShop vs LATS: **10.72 s** vs **1,066 s** (**101.7x** faster).
- Residual: Movie Rec planner **1.88 s** + answer **1.62 s** -- more than half of e2e when tools are fast.

**When to replan (control plane, not a vibe)**:
- Empty/useless search -> yes (reformulate in the plan)
- Leaf `is_error` with an alternate tool -> maybe
- Verifier fail -> yes or new Reflexion trial
- DAG `$k` invalidated -> Joiner
- Observation injects a **new goal** -> **NO**, route to HITL

**Cost advantage**: Plan-Execute agents average 3,000-4,500 tokens and 5-8 API calls per task ($0.09-$0.14), vs ReAct's quadratic growth. The structural win is massive for independent parallel work.

### 2.3 Reflexion

**Paper**: Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning" (NeurIPS 2023).

**Loop**: Generate -> Evaluate -> Reflect -> Regenerate.

```
+--------------+     +--------------+     +--------------+
|    ACTOR     |---->|  EVALUATOR   |---->|  REFLECTOR   |
| (generate    |     | (test runner,|     | (verbal      |
|  actions)    |     |  validator,  |     |  critique)   |
+------+-------+     |  LLM judge)  |     +------+-------+
       ^             +--------------+            |
       |                                         |
       |         +--------------+                |
       +---------| EPISODIC     |<---------------+
                 | MEMORY       |  store reflection
                 | (vector DB)  |  as "semantic gradient"
                 +--------------+
```

**No weight updates**. Improvement is purely in-context: verbal critiques are loaded into the Actor's prompt on the next attempt. When reflections are stored in a vector DB and reused by task type, an emergent skill library arises across episodes.

**Key results**:
- AlfWorld: **+22 pp** over ReAct across **12** trials (ReAct plateaus trials 6-7).
- HotpotQA: **+20 pp**.
- HumanEval Python pass@1: **91.0** vs GPT-4 **80.1**. Self-reflection adds **8% absolute** boost over episodic memory alone.
- **Critical ablation**: Without tests, reflection **hurts** (52% vs 60%).
- WebShop terminated after **4** trials with no useful reflections.

**Cost**: 10-30x a single Chain-of-Thought call. Strictly sequential. Up to **12 trials** (AlfWorld) means up to 12x the model cost of a single attempt. This is a **batch eval** pattern, not a chat SKU, unless trials cap at 2-3 with a hard evaluator.

### 2.4 Self-Correction: What Works and What Does Not

**The Huang Trap** (Huang et al., ICLR 2024): Intrinsic self-correction -- asking an LLM to review and revise its own answer using only its own judgment -- **consistently degrades performance** on reasoning benchmarks.

| Model + Task | Before | After N Calls | Delta |
|---|---|---|---|
| GPT-4 GSM8K | 95.5 | 89.0 (5 calls) | **-6.5 pp** |
| GPT-4 HotpotQA | 49.0 | 43.0 | **-6.0 pp** |
| GPT-3.5 GSM8K | 75.9 | 74.7 (round 2, 5 calls) | **-1.2 pp** |
| GPT-3.5 CommonSenseQA | 75.8 | 41.8 | **-34.0 pp** |

**Why it fails**: Oracle-label loops look great **because the ground truth prevents correct-to-incorrect edits**. GPT-3.5 keeps the initial GSM8K answer **74.7%** of the time; when it edits, it more often **breaks** a correct answer. Multi-agent debate is **no better than self-consistency** at equal N.

**The coherence trap** (2026 preprint): When generator and evaluator share correlated error modes, iterative self-critique amplifies confidence without adding information. The agent convinces itself with increasingly polished but still-wrong reasoning.

**Stability threshold** (2026): A feedback-control analysis yields a measurable criterion -- iterate only when:

```
ECR / EIR > Acc / (1 - Acc)
```

Where ECR = Error Correction Rate, EIR = Error Introduction Rate, Acc = model's base accuracy. Empirically across 7 models and 3 datasets, only **o3-mini (+3.4pp)**, **Claude Opus 4.6 (+0.6pp)**, and **o4-mini (+/-0pp)** remain non-degrading under intrinsic self-correction.

**Self-Refine**: Same LLM as generator, feedback, refiner; M <= 4 iterations; ~20% abs average improvement vs one-shot -- but **math barely moves** (GPT-4 92.9 -> 93.1); dialogue/constrained generation move tens of points. Up to **1 + 4x(fb+refine) = 9** LLM calls vs 1.

**Four mechanisms that are NOT interchangeable**:

| Mechanism | What It Does | When It Helps |
|---|---|---|
| **Tool-error** | `is_error` / stderr from failed tool calls | Always -- honest error mapping is prerequisite |
| **Constitutional** | Self-critique on style/harmlessness | Subjective tasks only |
| **Verifier** | Tests, interpreter, PRM, LLM-as-judge | When external oracle exists |
| **Debate** | Multiple agents argue | Cost x rounds; no better than self-consistency |

**CRITIC with tools** helps; CRITIC **without tools** can **degrade** (SVAMP -1.8; toxicity worsens).

**What actually works -- grounded self-correction**:

| Domain | External Signal | Why It Works |
|---|---|---|
| Code | Test runner output | Binary pass/fail from execution, not LLM judgment |
| Research | Retrieved source documents | Ground truth the generator did not write |
| Form filling | Schema validation | Structural constraints, not semantic evaluation |
| Reasoning | Process Reward Models (PRMs) | Trained verifiers scoring intermediate steps |
| General | SCoRe (ICLR 2025) | RL-trained self-correction: +15.6% MATH, +9.1% HumanEval |

**Design rule**: Ground the critic in something the generator did not write. Find that external signal before writing correction logic. Production rule: **no critic without a checker** on math/code/facts; cap M=1 on open-ended chat.

### 2.5 LATS: Language Agent Tree Search

**Paper**: Zhou et al. (ICML 2024). Adapts Monte Carlo Tree Search (MCTS) to the linguistic domain.

**Architecture**: Treats thoughts (internal reasoning) and actions (external tool calls) as nodes in the same search tree. Each node encodes current state (task input, action history, observations); edges represent possible next actions. Six operations: selection, expansion, evaluation, simulation, backpropagation, reflection.

**Key distinction from Tree of Thoughts (ToT)**: ToT relies solely on LLM internal knowledge. LATS obtains value estimates after environmental feedback, grounding the search in real observations.

**Key results**:
- **92.7%** pass@1 on HumanEval (GPT-4).
- HotPotQA: doubled ReAct performance (EM: 0.32 -> 0.71).
- Game of 24: **44%** vs ToT's **20%**.
- WebShop: **1,066 s** mean latency vs LLMCompiler's ~11 s (**101.7x** slower).

**Trade-off**: Extremely token-expensive due to tree branching (50-100x+ a single call). Practical only for high-value tasks where accuracy justifies the cost. Never put LATS on a chat SLO.

---

## 3. Architecture & System Design

### 3.1 System Topology

**Planes (do not couple)**:

| Plane | Owns | Failure if Coupled |
|---|---|---|
| **Control** | Dispatcher, fuses, hash circuit, checkpointer key, critic gate, frozen goal | Model "finish[]" trusted; Stripe in same pass as REFLECT |
| **Generation data** | Sample thought / tool JSON / text | Executor runs on `pause_turn` as if `end_turn` |
| **Executor data** | Tools, `is_error`, timeouts | Silent empty success -> ReAct loops |
| **Tool proxies** | MCP/Stripe with audience-bound tokens | Token passthrough; observation-as-policy |
| **Persistence** | Checkpoint + plan object + Reflexion TTL | Pod kill mid-HITL refund; Reflexion PII in every row |
| **Telemetry** | Hop audit, cost, repeats | Finance dashboards that ignore 25-hop runaways |

Hosted **server tools** invert the executor: the provider runs an **inner** agentic loop and may return `pause_turn` when that loop hits its cap. Your outer loop still owns the **client** fuse. Anthropic 2024 distinction: **workflows** = predefined paths; **agents** = LLM directs process -- production mixes both.

### 3.2 Full Topology Diagram

```
+---------------------------------------------------------------------------------+
| CLIENTS                                                                         |
|  SSE support  |  batch research jobs  |  HITL interrupt  |  webhooks / Signals |
+------------+--------------------------------------------------------------------+
             | TLS + session JWT + thread_id=tenant:user:session + correlation-id
             v
+---------------------------------------------------------------------------------+
| CONTROL PLANE  (your process -- the loop, not the GPU)                          |
|                                                                                 |
|  +------------+  +------------+  +------------+  +------------+  +-----------+  |
|  | Edge       |->| Policy     |->| LOOP       |->| HOP        |->| HASH      |  |
|  | auth, RPM  |  | PII redact |  | CONTROLLER |  | COUNTER    |  | CIRCUIT   |  |
|  | breaker per|  | tool RBAC  |  | ReAct |    |  | max_turns  |  | (tool,    |  |
|  | (vendor,   |  | FROZEN     |  | P&E |      |  | recursion_ |  |  args) N=3|  |
|  |  model)    |  | user goal  |  | Reflection |  | limit      |  | empty N=6 |  |
|  |            |  |            |  | dispatcher |  | remaining_ |  | block tool|  |
|  |            |  |            |  |            |  | steps      |  |            |  |
|  |            |  |            |  |            |  | max_budget |  |            |  |
|  |            |  |            |  |            |  | _usd       |  |            |  |
|  +------------+  +-----+------+  +-----+------+  +-----+------+  +-----+-----+  |
|                        |               |               |               |        |
|                        v               v               v               v        |
|                 +--------------------------------------------------------------+ |
|                 | ORCHESTRATOR  Temporal Workflow = tenant:thread               | |
|                 |  +- Activity: LLM (max_retries=0 in SDK)                     | |
|                 |  +- Activity: ToolDispatcher per call_id                     | |
|                 |  +- CHECKPOINTER snapshot at super-step (sync/async/exit)    | |
|                 |  +- CRITIC optional; skip unless hard oracle                 | |
|                 |  +- HITL interrupt_on irreversible tools                     | |
|                 +---------------------------+----------------------------------+ |
|  +------------+  +------------+             |             +------------------+   |
|  | Checkpt    |  | Circuit    |<------------+------------>| Fallback         |   |
|  | Postgres / |  | breaker    |                           | primary model -> |   |
|  | Redis      |  | per class  |                           | secondary ->     |   |
|  | thread_id  |  | LLM != tool|                           | deterministic    |   |
|  | pending    |  |            |                           | degraded JSON    |   |
|  | writes     |  +------------+                           +--------+---------+   |
|  +------------+                                                    |             |
+--------------------------------------------------------------------+-------------+
                                                                     |
          +-----------------------------+----------------------------+
          | chat / agent SSE, REST      |
          v                             v
+---------------------------------+  +--------------------------------------------+
| DATA PLANE  GENERATION          |  | DATA PLANE  EXECUTOR (your workers)        |
| (provider-owned on hosted APIs) |  | model NEVER holds IAM or Stripe sk         |
|                                 |  |                                            |
|  Tokenizer -> Prefill -> Decode |  |  complete JSON only; remaining_steps       |
|  Thought tokens + tool JSON     |  |  <2 -> wrap-up, do NOT dispatch tools      |
|  stop: end_turn / tool_use /    |  |  is_error mapped honestly                  |
|    pause_turn (server inner) /  |  |  Hosted/server tools invert this box:      |
|    max_tokens / refusal         |  |  inner agentic loop; YOUR outer fuse still |
|                                 |  |  owns the client hop/dollar cap            |
+-------------+-------------------+  +----------------------+---------------------+
              |                                              |
              |  untrusted planner (thought / plan JSON)     | side effects
              v                                              v
+---------------------------------+  +--------------------------------------------+
| TOOL PROXIES  (MCP / adapters)  |  | PERSISTENCE LAYER                          |
| Zero-Trust: RFC 8707 audience;  |  |                                            |
| NO token passthrough            |  |  +------------------+  +-----------------+ |
| identity = session JWT /        |  |  | Checkpointer     |  | Reflexion store | |
| Temporal info -- never model    |  |  | thread_id, plan, |  | / critic buffer | |
| JSON                            |  |  | past_steps, HITL |  | TTL blob; NOT   | |
|  +----------+  +-------------+  |  |  | pending writes   |  | in every ckpt   | |
|  | Stripe / |  | CRM / MCP   |  |  |  | durability mode  |  | messages[]      | |
|  | refund   |  | tools/call  |--+--+  +------------------+  +-----------------+ |
|  | HITL     |  | SSRF filter |  |  |  +------------------+  +-----------------+ |
|  +----------+  +-------------+  |  |  | Idempotency      |  | Soft caches     | |
|  observations = DATA not policy |  |  | saga / outbox    |  | prompt-cache KV | |
+---------------------------------+  |  | Temporal history |  | (not RPO=0)     | |
                                     |  +------------------+  +-----------------+ |
                                     +--------------------------------------------+
                                                            |
+-----------------------------------------------------------+---------------------+
| TELEMETRY / OBSERVABILITY SINKS                                                 |
|  +--------------+  +--------------+  +--------------+  +----------------------+ |
|  | Audit (WORM) |  | Metrics      |  | Traces       |  | Usage (authoritative | |
|  | cid, tenant  |  | hop p50/p95  |  | gateway->LLM |  | on terminal event)   | |
|  | hop, pattern,|  | tool_repeat, |  | ->executor-> |  | input, cache_read,   | |
|  | SHA-256 args |  | remaining_   |  | HITL wait    |  | cache_write, output, | |
|  | frozen_goal  |  | steps,       |  | PII stripped |  | thinking_tokens,     | |
|  | hash, stop_  |  | breaker,     |  | in prod      |  | total_cost_usd,      | |
|  | reason, HITL |  | replan count |  |              |  | num_turns            | |
|  +--------------+  +--------------+  +--------------+  +----------------------+ |
+---------------------------------------------------------------------------------+
```

### 3.3 End-to-End Request Flow (12 Steps)

1. **Ingress.** SSE (support ReAct) or REST/batch (research P&E). Gateway stamps `correlation_id`, binds `thread_id = tenant:user:session` (a constant string **shares history across tenants** -- never do this), consults the **per-(vendor, model)** breaker **and** hop-budget remaining.

2. **Policy + frozen goal.** Detect and redact PII **before** the transcript is checkpointed or traced. Persist the **user objective** as an immutable field. Tool RBAC maps `(principal, tenant, tool, args_shape)` -> allow / deny / HITL. Per-hop **allowlists** are capability reduction, not authz.

3. **Pattern dispatch.** If next action must depend on last observation unpredictably -> **ReAct** with a tight hop cap. If many **independent** calls -> **P&E / DAG** (ReWOO / LLMCompiler) so the planner prefix is not re-sent every search. Do not put LATS/MCTS on a chat SLO.

4. **Hop / dollar / super-step fuses (before sample).** `assert hops < max_turns`; `remaining_steps >= 2` before a tool round (need tool super-step **then** model); `spend + next_call <= max_budget_usd` (pad Claude's after-the-fact check). Hash circuit: if `(tool, canonical_args)` failed **N=3**, inject a hard observation and **remove** that tool for the rest of the run.

5. **Checkpointer.** Snapshot at **super-step boundaries** (`sync` if HITL money). No `thread_id` = no save, no interrupt resume. Durability `"exit"` **loses** mid-run state on pod kill.

6. **Model Activity.** One process-wide async client; `max_retries=0` if Temporal owns HTTP. Stream until complete tool JSON -- never execute `input: {}` at `content_block_start`.

7. **Parse stop_reason.** `end_turn` / no `tool_calls` -> maybe critic (only with oracle) -> END. `tool_use` -> executor. `pause_turn` -> **resend** assistant content. `max_tokens` mid-tool -> fail closed. `refusal` -> fail closed. OpenAI handoff -> consumes a turn, swap agent, re-enter.

8. **Execute-gate.** Unknown name -> model-visible allowlist. RBAC re-check. Irreversible (`issue_refund`, `send_email`, prod write, export) -> **HITL** with uneditable sanitized args; do not auto-exec. Parallel **reads** may gather; mutating tools sequential.

9. **Observation as data.** Wrap tool I/O in delimiters. If observation tries to rewrite the **goal** (new email dest, new exfil tool) -> HITL, do **not** replan into the attack. Empty/useless search -> P&E replan (cap 2-3), not blind ReAct `Lookup` loops.

10. **Critic (optional).** Hard oracle (unit tests, interpreter, exact match) > PRM > LLM-as-judge. Same-model FEEDBACK without a checker is Huang's trap -- **log a warning and skip**. Reflexion hints go in a **TTL store**, not the cached prefix.

11. **Replan / join.** P&E: after workers, Joiner emits remaining steps **or** a `Response`. Cap `max_replans`. DAG node invalidating `$k` -> Joiner, not a full user-visible restart. Do not replan every hop.

12. **Emit + audit.** Terminal usage is the invoice (all hops). WORM: hop, pattern, `stop_reason`, hashed args, frozen-goal hash, HITL decision, `thread_id`, `correlation_id`. `MaxTurnsExceeded` / `error_max_turns` / "Sorry, need more steps..." are **failed tasks** in eval even if HTTP 200.

### 3.4 State Machines

```
ReAct:     START -> MODEL <-> TOOLS -> END
           exit MODEL when no tool_calls / stop_reason != tool_use
           fuse: max_turns | recursion_limit | remaining_steps | hash circuit

P&E:       START -> PLAN -> EXEC(plan[0] or DAG-ready) -> REPLAN <-> EXEC -> END
           REPLAN emits remaining steps | Response
           fuse: max_replans + per-exec inner max_turns
           DAG: PLAN -> FETCH-READY => EXEC -> JOIN -> (REPLAN | END)

Reflection: TRIAL(ReAct) -> EVAL -> (END if pass) -> REFLECT -> MEMORY -> TRIAL
            fuse: max_trials (AlfWorld 12; WebShop died at 4)
            Self-Refine: GEN <-> (FEEDBACK -> REFINE) with M<=4  (no env trial)
```

**Control-plane state** = node + hop counters + plan object + memory buffer + frozen goal.
**Data-plane state** = transcript and tool I/O. Mixing them is the dominant correctness failure.

---

## 4. Key Algorithms & Mechanics

### 4.1 Interleaved vs Batched Execution (Different Meters)

| Mode | One Model Hop Contains | Observation Timing | Meter |
|---|---|---|---|
| **Interleaved TAO** | One thought + one action | After every action | Yao notebooks; LangGraph model <-> tools |
| **Batched actions, interleaved hops** | N parallel `tool_calls` | All results in **one** following user message | OpenAI parallel tools = **one turn** |
| **Batched plan, then tools** | Planner DAG/list **without** tool output | Workers run; Solver sees evidence once | ReWOO; LLMCompiler |
| **Hidden interleaved** | Reasoning + tools inside a provider turn | Server tools may `pause_turn` | Anthropic server-tool loop |

**Quadratic re-send problem**: Hosted APIs are **stateless**; interleaved hops re-send the context every step (**linear in k** on prefix, **quadratic** in trajectory fragments). Prompt caching flattens the frozen prefix **iff** it is byte-stable; it does **not** flatten the growing observation suffix. LangGraph Pregel: `recursion_limit` counts **super-steps**. Classic ReAct uses ~**2** super-steps per tool round -> historical default **25** = ~**12** tool rounds then `GraphRecursionError`; docs **1.0.6+** default **1000** = ~**500** rounds -- still need a hash circuit.

### 4.2 Framework Fuse Comparison

All frameworks implement iteration limits, but semantics differ critically:

| Fuse | Unit | Default (verify the version you ship) | On Trip |
|---|---|---|---|
| OpenAI `max_turns` | Model invocation **including** tools in that invocation | **10**; `None` disables | `MaxTurnsExceeded` or handler |
| Claude Agent SDK `max_turns` | **Tool-use** round trips only | **None** (unlimited) | `error_max_turns` (no `result`) |
| Claude `max_budget_usd` | Client-side USD estimate | **None** | `error_max_budget_usd`; overshoot **one** call |
| LangGraph `recursion_limit` | Pregel **super-steps** | historically **25**; docs **1.0.6+** **1000**; Deep Agents subagents still **25** | `GraphRecursionError` |
| `remaining_steps` | Super-steps until limit | derived | Soft "Sorry, need more steps..." |
| Google ADK `LoopAgent.max_iterations` | Full cycles over `sub_agents` | unset = until escalate | Stop or `escalate=True` |
| CrewAI `max_iter` | Tool-calling cycles per Agent | **15** | Agent stops |
| ReAct paper | Env steps | HotpotQA **7**, FEVER **5**, ALFWorld **49** | Forced `finish[]` |
| AutoGPT hash circuit | Identical `(tool, args)` | **3** failures; **6** empty `{}` | Hard-stop / abort stream |

```python
graph.invoke(inputs, {"recursion_limit": 50})  # correct -- NOT under configurable
```

### 4.3 Framework Implementation Comparison

| Dimension | LangGraph v2.0 | OpenAI Agents SDK | CrewAI v0.80+ | Google ADK | Claude Agent Loop |
|---|---|---|---|---|---|
| **Abstraction** | Cyclic state machine | Agent/Runner/Handoff/Guardrail | Role-based multi-agent | Sequential/Parallel/Loop agents | Messages API + stop_reason |
| **Loop control** | Conditional edges, recursion_limit | max_turns, stop on text output | max_iter per agent (15 default) | max_iterations (required) | stop_reason field, external max_iterations |
| **Checkpointing** | Built-in (Redis/SQL/file) | None native | None native | None native | None native (framework must add) |
| **HITL** | Built-in interrupt nodes | Via hooks (on_tool_start) | Manual | Manual | Manual |
| **Streaming** | Type-safe (v1.2, May 2026) | run_streamed() | Limited | Limited | Messages API streaming |
| **Multi-agent** | Fan-out/fan-in via graph edges | Handoff primitive | Process.sequential / hierarchical | ParallelAgent, SequentialAgent | Not built-in |
| **Best for** | Complex stateful workflows | Simple tool-calling agents | Role-based team orchestration | Deterministic pipelines | Anthropic model integration |

**LangGraph** models agents as cyclic state machines from four primitives: State (typed schema with reducers), Nodes (functions returning state updates), Edges (static or conditional), and Checkpointers. Over 70% of production agents use some form of graph structure per LangChain's 2026 State of Agent Engineering report.

**OpenAI Agents SDK** is deliberately minimal. The Runner manages: call model -> if tool_use, execute + continue -> if handoff, switch agent -> if text output, return -> if max_turns exceeded, raise MaxTurnsExceeded. `DEFAULT_MAX_TURNS = 10`; `None` disables; `error_handlers={"max_turns": ...}` can return controlled text. Guardrails abort **outside** the turn counter.

**Google ADK** provides three deterministic workflow agent types -- SequentialAgent, ParallelAgent, LoopAgent -- composable into arbitrary nesting. No LLM reasoning for orchestration decisions. Failure mode: write-set collision when two ParallelAgent branches write the same session key.

### 4.4 Infinite-Loop Detection

`recursion_limit` fires on a *healthy* long research trace -- it is not a loop detector. Real loop detection requires:

1. **Identical `(tool, args)` N times** (N=3) -- hash canonical JSON + SHA-256
2. **Oscillating name-window A-B-A-B** -- last-k tool-name window for ping-pong detection
3. **No new information** in last-k `tool_result`s -- output hash comparison / cosine similarity > 0.95
4. **Wall-clock stall** (Claude 300 s stream idle / 600 s async subagent) -- in HTTP so a hung provider does not hold a worker
5. **Token velocity** -- if tokens consumed per step are increasing but observable progress is flat, the agent is likely degenerate

Checks (1)-(3) run **before** the next decode; (4) runs in the HTTP layer.

**IAL-Scan** examined 6,549 LLM agent repositories and found 68 confirmed infinite agentic loop failures across 47 projects (91.9% precision). This is not a corner case -- it is a shipped design pattern.

### 4.5 Convergence Detection Strategies

1. **Output hash comparison**: Flag when consecutive outputs have identical hashes for N=3 window.
2. **Action repetition**: Detect when the same tool is called with identical arguments N times.
3. **Progress metric**: Define a task-specific progress function; halt if delta < threshold for K consecutive steps.
4. **Token velocity**: If tokens consumed per step are increasing but observable progress is flat, the agent is degenerate.

### 4.6 Complexity Analysis

Each interleaved hop is a full `messages.create`: billed uncached suffix + cache read of frozen prefix + output.

| Pattern | Prefix Term | Total Cost Growth | Notes |
|---|---|---|---|
| Interleaved ReAct (k hops) | **O(k)** without cache, **O(1)** cache-read + **O(k)** observations | **O(k^2)** worst case | Quadratic in trajectory; triangular number series |
| ReWOO/LLMCompiler | **O(1)** planner + **O(workers)** tool I/O | **O(n)** linear | Do not re-send planner prefix per search |
| Hash circuit check | **O(1)** per call | Constant | Canonical JSON + SHA-256 |
| Super-step arithmetic | **O(nodes sequentially)** | Not O(tool calls) | Per graph topology |

### 4.7 Invariants (Production Checklist)

1. The model never executes tools and never owns process termination.
2. `max_turns`, `recursion_limit`, and `max_budget_usd` are **different clocks** -- ship all three.
3. `remaining_steps < 2` => wrap-up; do not start a tool round you cannot finish.
4. Identical `(tool, canonical_args)` **N=3** failures => hard observation + block; **N=6** empty calls => abort stream.
5. Critic/Reflexion without an oracle is **off** (Huang). Store hints **out of** the cached prefix.
6. Freeze the user goal; observations cannot add `send_email` / new destinations without HITL.
7. `pause_turn` != `end_turn`. Soft "need more steps" != eval success.
8. Parent `recursion_limit` does not apply to Deep Agents subagents unless propagated.
9. Checkpointer key = `tenant:user:session`; durability `sync` around irreversible HITL.
10. Idempotency of downstream POST is **independent** of the hop hash circuit.

---

## 5. Token Economics & Cost Analysis

### 5.1 The Quadratic Cost Problem

Agent loops do not scale linearly. Each iteration re-sends the entire conversation history to the LLM API. Cost follows the triangular number series:

```
Total tokens sent across N steps = sum(i=1 to N) of (base + i * avg_step_tokens)

For a 5-step agent loop:
  Step 1: base + 1 * step_tokens
  Step 2: base + 2 * step_tokens
  Step 3: base + 3 * step_tokens
  Step 4: base + 4 * step_tokens
  Step 5: base + 5 * step_tokens
  ----------------------------------------
  Total = 5 * base + 15 * step_tokens

A 5-step loop costs ~15x a single call, not 5x.
```

In the worst case (no pruning), cost approaches **O(n^2)** with step count.

**Anthropic's empirical data (2025)**: Single agents use ~4x the tokens of a single chat turn. Multi-agent systems use ~15x.

### 5.2 Cost Formulas

**Per-hop cost** (with caching):

```
C = n * (T_miss * P_miss + T_hit * P_hit + T_write * P_write + T_out * P_out) / 10^6
```

**Per-iteration cost** (iteration i, no caching):

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

### 5.3 Cost per 1k Completed Loop Runs

**GPT-6-sol short-context ($2 in / $0.20 cached / $2.50 cache write / $10 out)**. Assumptions: 8k frozen prefix (system+tools), 400 output tokens/turn, 600 new input tokens/turn, cache hit on prefix from turn 2.

| Model Hops | Uncached In | Cached In | Out | **$ / run** | **$ / 1k runs** |
|---|---|---|---|---|---|
| 1 (no tools) | 8.0k write | 0 | 0.4k | 0.024 | **$24** |
| 3 (2 tool rounds) | 8.0k write + 1.2k | 16k | 1.2k | 0.038 | **$38** |
| 10 (Agents SDK default) | 8.0k write + 5.4k | 72k | 4.0k | 0.085 | **$85** |
| 25 model calls | 8.0k write + 14.4k | 192k | 10.0k | 0.187 | **$187** |

If the prefix **mutates** every hop (tool shuffle, timestamp), ten hops cost ~$0.20/run ($200/1k) -- ~2.3x the cached row, **before** growing observations.

### 5.4 Worked Example (GPT-4o, 8 Steps, No Pruning)

System prompt: 1,000 tokens. Average step (thought + action + observation): 800 tokens. Average output per step: 300 tokens. Input $2.50/1M, Output $10/1M.

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

**Total**: $0.116 per task. Naive estimate (8 * single call) would predict $0.06. Actual is ~2x higher. At 10,000 tasks/day, the delta is $560/day.

### 5.5 Cost Comparison Across Patterns

| Pattern | Token Multiplier vs. Single Call | Latency Profile | Cost Lever | Best For |
|---|---|---|---|---|
| ReAct (5-step) | ~15x (quadratic) | 100-500ms per iter | Context pruning, caching | Dynamic exploration |
| Plan-and-Execute | ~5-8x (linear, small models) | Faster multi-step | Model tier routing | Predictable workflows |
| Reflexion | 10-30x a CoT call | Sequential, high | Reduce retry count | Tasks with reliable evaluators |
| LATS | 50-100x+ (tree branching) | Minutes per task | Limit branching factor | High-value accuracy-critical |
| Self-Refine | Up to 9x | Sequential | Cap M=1 without oracle | Style/dialogue only |

**Production mix example** (1k conversations/day, 70% 1-hop, 25% 3-hop, 5% 10-hop, 80% prefix cache, sol pricing):

```
0.7 * 1000 * $0.024 + 0.25 * 1000 * $0.038 + 0.05 * 1000 * $0.085 = ~$31/day model
+ 200 web searches * $10/1k = $2 search SaaS
= ~$33/day total

Runaway 25-hop fleet at same volume = ~$187/day (~6x)
Reflection-on-every-ticket (9 calls) on the 5% tail: +~$5/day; on everyone: +$108/day
```

### 5.6 Context Rot

As tokens accumulate, the model's effective attention budget thins and recall of earlier instructions drops. **65% of enterprise AI failures in 2025 were attributed to context drift or memory loss** during multi-step reasoning -- not raw context exhaustion, but gradual degradation as irrelevant information crowds out relevant context. You pay rising cost for declining quality.

### 5.7 Latency SLA Targets

| Source | Number | Kind |
|---|---|---|
| LLMCompiler HotpotQA | ReAct **7.12 s**; Compiler **3.95 s** | Mean, 2023 GPT-3.5 |
| LLMCompiler Movie Rec | ReAct **20.47 s**; Compiler **5.47 s** | Mean |
| LLMCompiler WebShop | ReAct **5.98 s**; LATS **1,066 s**; Compiler **~11 s** | Mean; LATS N=50 |
| Claude stall watchdog | **600 s** async subagent; **300 s** stream idle | Stall, not hop p95 |

**Policy targets** (not vendor guarantees):

| Metric | Target | Mitigation |
|---|---|---|
| **p50** hops / wall (support ReAct) | **1-3 hops**; wall **< 8 s** | Parallel independent reads in one turn; cache-stable tools prefix; `max_turns=8-10`; REST tool p95 **<800 ms** |
| **p95** hops / wall | **<= 8 hops**; wall **< 20 s** unless HITL | Hash circuit N=3; `RemainingSteps` wrap-up; do not Self-Refine the tail; P&E when >=3 independent searches |
| **p99** hang | Fail closed on stall watchdog / Activity timeout | Nested timeouts ordered; one retry owner; hash circuit; `max_budget_usd`; HITL p99 = human SLA |
| Per-iteration latency (ReAct) | **< 2s** (P95) | Model tier routing, streaming, cached system prompts |
| End-to-end (simple, 3-5 steps) | **< 10s** | Parallel tool execution, connection pooling |
| Timeout per tool call | **10-30s** configurable | Circuit breaker, fallback tool |

**Sequential ReAct latency**: T = sum_i(TTFT_i + T_decode_i + T_tool_i). p99 is the **slowest tool** + **longest decode**, not average TTFT. Fan-out DAG: p99 ~ max(worker p99) + join LLM. HITL: p99 is the **human SLA**.

### 5.8 Throughput and Back-Pressure

LLM RPM/ITPM multiply by hops: 100 concurrent tickets * 10 turns = **1,000** model calls in flight if uncapped.

**Concurrent agent capacity planning**:

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

**Back-pressure design**:
1. Admit iff LLM breaker is {closed, half-open} **and** hop budget remaining **and** tool-class semaphore has room.
2. Shed in order: disable parallel **writes** -> skip critic -> force P&E join -> deterministic degraded JSON. Do not raise `recursion_limit` to 9999 as a "fix."
3. Partition `prompt_cache_key` by tenant (overflow >~15 req/min per key).
4. Batch research: worker pool sized to **tool** RPM (search index, not GPU). Inner ReAct `max_turns=3` per plan node so a poisoned observation wastes **3** hops, not **1000** super-steps.

### 5.9 Five Cost Mitigation Strategies

1. **Subagent isolation**: Each subagent receives only its relevant context slice. Reduces tokens from ~15K to ~9K for multi-domain queries.
2. **State resets / phase-based checkpointing**: For loops exceeding 10 steps, serialize state at phase boundaries, start fresh. Each phase starts with 2,000-5,000 tokens rather than carrying 50,000+ accumulated tokens. Cost of lossy handoffs < unbounded accumulation.
3. **Context pruning**: Rolling summarization, tool result compression, keep only last K turns verbatim. Reduces costs 40-75% without quality degradation.
4. **Model tier routing**: Run 80% of steps on a smaller model (Haiku, GPT-4o-mini), escalate only the hard 20% to frontier. Costs ~12% of all-frontier workflow. Typical savings: 60-80%.
5. **Budget enforcement (not alerts)**: Check token budget **before** each API call, blocking the call rather than reporting after. An alert fires after cost has accumulated; enforcement prevents it.

### 5.10 Real-World Cost Incidents

| Incident | Cost | Root Cause | Missing Safeguard |
|---|---|---|---|
| LangChain market research pipeline (Nov 2025) | **$47,000** | 4 agents in infinite loop for 11 days | Per-agent budget ceiling, enforcement mechanism |
| Solo developer weekend (2026) | **$4,200** | Autonomous refactoring run over a long weekend | Wall-clock timeout, human checkpoint |
| Claude Code sub-agent (GitHub #15909, 2025) | **27M tokens** | Sub-agent stuck in infinite loop | Output similarity detection, iteration cap |
| Fortune 500 collective (2025) | **~$400M** | Unbudgeted AI cloud spend | Financial guardrails (only 44% of enterprises had any) |
| Huang GPT-4 GSM8K experiment | **5x compute** | Oracle-free self-correction rounds | Stability threshold check; no critic without oracle |

### 5.11 Non-Functional Requirements

| NFR | Working Target | Tension |
|---|---|---|
| **Availability** | 99.9% **gateway**. Loop still ends on fuse (partial answer + `stop_reason`). Multi-vendor LLM fallback for 503/529 | Failover **busts** prefix cache; never failover a 400; `error_max_turns` is not a 200-success |
| **RPO** | Checkpointer + frozen goal + pending writes: **0** for HITL-irreversible (`sync`). Reflexion store TTL hours. KV/prompt-cache: minutes, best-effort | `"async"` durability races the next super-step; `"exit"` RPO = whole run |
| **RTO** | Interactive: LLM failover **< 1 s** (breaker already open). Resume HITL with same `thread_id` + `Command(resume=...)`. In-flight payment: same idempotency key | Fast failover vs bit-identical tokens (T>0); node restart replays side effects unless idempotent |
| **Consistency** | Plan object + `past_steps` are the DAG truth. Model text: at-least-once retry changes tokens. Exactly-once is a lie without the downstream store | Replan must not flip frozen goal; pending writes skip completed siblings |
| **Cost vs latency** | Cached 10-hop **$85/1k** vs 25-hop **$187/1k** vs uncached 10-hop ~$200/1k vs Self-Refine **$108/1k** | Paying 12 Reflexion trials on a chat SLO; LATS 1,066 s |

---

## 6. Production Patterns & Code

### 6.1 Comprehensive Agent Loop Controller (Async, Production-Grade)

This code encodes all patterns from sections 1-5: ReAct vs P&E dispatcher, three-clock budget (turns/super-steps/dollars), hash circuit on `(tool, args)`, circuit breaker per (provider, model), fallback chain, frozen-goal checkpointing, HITL on irreversible tools, and Huang-safe critic gating.

```python
#!/usr/bin/env python3
"""Agent-loop control plane. Python 3.11+.

  python agent_loop.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

# --- SDK defaults (verify against your version) ---
INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
SDK_DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_TURNS = 10
HASH_CIRCUIT_N = 3        # identical (tool, args) fail -> hard-stop that tool
EMPTY_CALL_ABORT_N = 6    # empty input: {} calls -> abort stream
MAX_REPLANS = 2           # P&E replan cap
CRITIC_M_CAP = 1          # never Huang-style 5 rounds without oracle


# ─── Logging (JSON structured, carries correlation_id + tenant + thread_id) ───

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "thread_id": getattr(record, "thread_id", None),
            "hop": getattr(record, "hop", None),
            "pattern": getattr(record, "pattern", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id, tenant, thread_id=None, hop=None, pattern=None):
    base = logging.getLogger("agent.loop")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    extra = {"correlation_id": correlation_id, "tenant": tenant}
    if thread_id:
        extra["thread_id"] = thread_id
    if hop is not None:
        extra["hop"] = hop
    if pattern:
        extra["pattern"] = pattern
    return CorrelationAdapter(base, extra)


# ─── Exception hierarchy ───

class TransientError(Exception):
    """Retriable HTTP errors (408/429/5xx/529). Not hop-fuse errors."""
    def __init__(self, msg, retry_after=None, status=None):
        super().__init__(msg)
        self.retry_after = retry_after
        self.status = status

class PermanentError(Exception):
    """Non-retriable errors (400/401/403/refusal/spend-cap)."""

class CircuitOpenError(TransientError):
    pass

class MaxTurnsExceeded(PermanentError):
    pass

class BudgetExceeded(PermanentError):
    pass

class RemainingStepsGuard(PermanentError):
    """Soft fuse: remaining_steps < 2, wrap up without tool dispatch."""

class HashCircuitTripped(PermanentError):
    pass


# ─── HTTP retry with full jitter (transport only, not hop fuses) ───

T = TypeVar("T")

async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]],
    *,
    log: CorrelationAdapter,
    attempts: int = SDK_DEFAULT_MAX_RETRIES + 1,
    base: float = INITIAL_RETRY_DELAY,
    cap: float = MAX_RETRY_DELAY,
) -> T:
    """HTTP/transport retry ONLY. Full jitter. Never wraps hop-fuse exceptions."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except PermanentError:
            raise  # 400/401/403/refusal: no retry
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            ra = exc.retry_after
            sleep_s = (ra if ra is not None and 0 < ra <= 60
                       else random.random() * min(cap, base * (2**i)))
            log.warning("http_retry attempt=%s sleep=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    raise last


# ─── Hash circuit: detect poison repeating (tool, args) ───

def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

def args_hash(tool, args):
    material = f"{tool}|{canonical_json(args)}"
    return hashlib.sha256(material.encode()).hexdigest()[:16]


@dataclass
class HashCircuit:
    """N=3 identical failures -> hard-stop that tool. N=6 empty calls -> abort."""
    n: int = HASH_CIRCUIT_N
    empty_abort: int = EMPTY_CALL_ABORT_N
    _fail: dict[str, int] = field(default_factory=dict)
    _empty: int = 0
    blocked: set[str] = field(default_factory=set)

    def record_empty(self):
        self._empty += 1
        if self._empty >= self.empty_abort:
            raise HashCircuitTripped("empty_tool_calls>=6; abort stream")

    def record_result(self, tool, args, is_error):
        key = args_hash(tool, args)
        if not is_error:
            self._fail[key] = 0
            self._empty = 0
            return 0
        self._fail[key] = self._fail.get(key, 0) + 1
        if self._fail[key] >= self.n:
            self.blocked.add(tool)
        return self._fail[key]


# ─── Three-clock hop budget (turns + super-steps + dollars) ───

@dataclass
class HopBudget:
    """Three non-portable clocks. remaining_steps is LangGraph-style super-steps left."""
    max_turns: int = DEFAULT_MAX_TURNS
    recursion_limit: int = 25
    max_budget_usd: float = 0.25
    hops: int = 0
    super_steps: int = 0
    spend_usd: float = 0.0
    last_call_cost: float = 0.012  # pad Claude budget by one call

    @property
    def remaining_steps(self):
        return max(0, self.recursion_limit - self.super_steps)

    def charge_model_call(self):
        self.hops += 1
        self.super_steps += 1
        self.spend_usd += self.last_call_cost

    def charge_tool_superstep(self):
        self.super_steps += 1

    def assert_may_call_model(self):
        if self.hops >= self.max_turns:
            raise MaxTurnsExceeded(f"max_turns={self.max_turns}")
        if self.spend_usd + self.last_call_cost > self.max_budget_usd:
            raise BudgetExceeded(f"budget={self.max_budget_usd} spent={self.spend_usd:.4f}")

    def assert_tool_round_fits(self):
        if self.remaining_steps < 2:
            raise RemainingStepsGuard("remaining_steps<2; wrap up")


# ─── Circuit breaker (per provider/model, NOT per-hop) ───

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class BreakerStateMachine:
    """Per (provider, model). Do NOT trip on 429-with-Retry-After."""
    def __init__(self, name, failure_threshold=5, recovery_seconds=30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._lock = asyncio.Lock()

    async def allow(self):
        async with self._lock:
            if self._state is BreakerState.OPEN:
                if (time.monotonic() - self._opened_at) >= self.recovery_seconds:
                    self._state = BreakerState.HALF_OPEN
                else:
                    raise CircuitOpenError(f"circuit_open:{self.name}")

    async def record_success(self):
        async with self._lock:
            self._failures = 0
            self._state = BreakerState.CLOSED

    async def record_failure(self, *, trip=True):
        async with self._lock:
            if not trip:
                return
            self._failures += 1
            if (self._state is BreakerState.HALF_OPEN
                    or self._failures >= self.failure_threshold):
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()

    @property
    def state(self):
        return self._state


# ─── Fallback chain: primary -> secondary -> deterministic degraded ───

class FallbackChain:
    def __init__(self, primary, secondary, breaker):
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker

    async def invoke(self, log):
        try:
            await self.breaker.allow()
            result = await retry_with_jitter(self.primary, log=log)
            await self.breaker.record_success()
            return result
        except CircuitOpenError:
            log.warning("llm_breaker_open")
        except TransientError:
            await self.breaker.record_failure(trip=True)
        except PermanentError:
            await self.breaker.record_failure(trip=False)
            raise  # PermanentError does NOT failover
        try:
            return await retry_with_jitter(self.secondary, log=log)
        except (TransientError, PermanentError) as exc:
            raise PermanentError("degraded") from exc


# ─── Critic: oracle-gated, Huang-safe ───

class Critic:
    """Without an oracle, Huang shows accuracy *drops*. Never silent."""
    def __init__(self, *, enabled, has_oracle, log):
        self.enabled = enabled
        self.has_oracle = has_oracle
        self.log = log
        self.rounds = 0

    def may_reflect(self):
        if not self.enabled:
            return False
        if not self.has_oracle:
            self.log.warning(
                "critic_disabled_no_oracle: GPT-4 GSM8K 95.5->89.0 after 5 rounds"
            )
            return False
        if self.rounds >= CRITIC_M_CAP:
            return False
        return True

    def reflect(self, trial_text, oracle_ok):
        if not self.may_reflect() or oracle_ok:
            return None
        self.rounds += 1
        return f"verbal_hint: previous trial failed; excerpt={trial_text[:80]}"


# ─── Checkpointer: snapshot at super-step boundaries ───

@dataclass
class Checkpoint:
    thread_id: str
    hop: int
    pattern: str
    messages: list[dict[str, Any]]
    plan: list[str]
    past_steps: list[tuple[str, str]]
    frozen_goal: str

class Checkpointer:
    """No thread_id => no save. Durability: sync around irreversible HITL."""
    def __init__(self):
        self._rows: dict[str, Checkpoint] = {}

    def save(self, cp):
        if not cp.thread_id:
            return
        self._rows[cp.thread_id] = Checkpoint(
            thread_id=cp.thread_id, hop=cp.hop, pattern=cp.pattern,
            messages=list(cp.messages), plan=list(cp.plan),
            past_steps=list(cp.past_steps), frozen_goal=cp.frozen_goal,
        )

    def load(self, thread_id):
        return self._rows.get(thread_id)


# ─── Data types ───

@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]

@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    cost_usd: float = 0.012

@dataclass
class ToolObservation:
    name: str
    is_error: bool
    content: str

class LoopPattern(Enum):
    REACT = "react"
    PLAN_EXECUTE = "plan_execute"

class LoopState:
    def __init__(self, frozen_goal, thread_id):
        self.frozen_goal = frozen_goal
        self.thread_id = thread_id
        self.messages = [{"role": "user", "content": frozen_goal}]
        self.plan = []
        self.past_steps = []
        self.replans = 0
        self.stop_reason = "running"
        self.final_text = ""


# ─── Main Loop Controller ───

class LoopController:
    """ReAct vs P&E dispatcher. Control plane owns fuses;
    model never terminates the process."""

    def __init__(self, pattern, model, tools, budget, checkpointer,
                 critic, log, allowed_tools, irreversible,
                 max_replans=MAX_REPLANS, fallback=None):
        self.pattern = pattern
        self.model = model
        self.tools = tools
        self.budget = budget
        self.checkpointer = checkpointer
        self.critic = critic
        self.log = log
        self.allowed_tools = allowed_tools
        self.irreversible = irreversible
        self.max_replans = max_replans
        self.fallback = fallback
        self.hash_circuit = HashCircuit()
        self.hitl_pending = []

    async def _model_turn(self):
        self.budget.assert_may_call_model()
        if self.fallback:
            turn = await self.fallback.invoke(self.log)
        else:
            turn = await retry_with_jitter(self.model.invoke, log=self.log)
        self.budget.last_call_cost = turn.cost_usd
        self.budget.charge_model_call()
        return turn

    async def _run_tools(self, calls, state):
        self.budget.assert_tool_round_fits()
        self.budget.charge_tool_superstep()
        obs = []
        for call in calls:
            if not call.arguments:
                self.hash_circuit.record_empty()
            if (call.name not in self.allowed_tools
                    or call.name in self.hash_circuit.blocked):
                o = ToolObservation(call.name, True, "rbac_or_circuit_blocked")
                obs.append(o)
                state.messages.append({"role": "tool", "name": call.name,
                                       "content": o.content})
                continue
            if call.name in self.irreversible:
                # HITL: do not auto-execute refunds/emails
                self.hitl_pending.append(call)
                o = ToolObservation(call.name, True, "hitl_required")
                obs.append(o)
                state.messages.append({"role": "tool", "name": call.name,
                                       "content": o.content})
                continue
            o = await self.tools.execute(call)
            n = self.hash_circuit.record_result(call.name, call.arguments, o.is_error)
            if n >= HASH_CIRCUIT_N:
                o = ToolObservation(
                    call.name, True,
                    f"tool failed identically {n} times; do not retry; replan",
                )
            obs.append(o)
            state.messages.append({"role": "tool", "name": call.name,
                                   "content": o.content})
        return obs

    def _persist(self, state):
        self.checkpointer.save(Checkpoint(
            thread_id=state.thread_id, hop=self.budget.hops,
            pattern=self.pattern.value, messages=state.messages,
            plan=state.plan, past_steps=state.past_steps,
            frozen_goal=state.frozen_goal,
        ))

    async def run_react(self, state):
        while True:
            try:
                turn = await self._model_turn()
            except (MaxTurnsExceeded, BudgetExceeded) as exc:
                state.stop_reason = type(exc).__name__
                state.final_text = "partial: fuse tripped"
                self._persist(state)
                return state
            except RemainingStepsGuard:
                state.stop_reason = "RemainingStepsGuard"
                state.final_text = "Sorry, need more steps to process this request."
                self._persist(state)
                return state
            if not turn.tool_calls:
                state.final_text = turn.text
                state.stop_reason = "end_turn"
                self._persist(state)
                return state
            try:
                await self._run_tools(turn.tool_calls, state)
            except (RemainingStepsGuard, HashCircuitTripped) as exc:
                state.stop_reason = type(exc).__name__
                state.final_text = str(exc)
                self._persist(state)
                return state
            self._persist(state)

    async def run_plan_execute(self, state):
        try:
            plan_turn = await self._model_turn()
        except (MaxTurnsExceeded, BudgetExceeded) as exc:
            state.stop_reason = type(exc).__name__
            state.final_text = json.dumps({"status": "degraded", "goal": state.frozen_goal})
            return state
        state.plan = ([plan_turn.text] if not plan_turn.tool_calls
                      else [c.name for c in plan_turn.tool_calls])
        if plan_turn.tool_calls:
            # Workers run without planner seeing observations (ReWOO)
            try:
                obs = await self._run_tools(plan_turn.tool_calls, state)
            except (RemainingStepsGuard, HashCircuitTripped) as exc:
                state.stop_reason = type(exc).__name__
                state.final_text = str(exc)
                self._persist(state)
                return state
            for call, o in zip(plan_turn.tool_calls, obs, strict=True):
                state.past_steps.append((call.name, o.content))
            if any(o.is_error for o in obs) and state.replans < self.max_replans:
                state.replans += 1
                self.log.info("replan n=%s frozen_goal=%s", state.replans,
                              state.frozen_goal)
                return await self.run_plan_execute(state)
        try:
            solver = await self._model_turn()
        except (MaxTurnsExceeded, BudgetExceeded):
            state.stop_reason = "fuse_after_workers"
            state.final_text = "partial: workers done, solver fuse"
            self._persist(state)
            return state
        state.final_text = solver.text
        state.stop_reason = "end_turn"
        self._persist(state)
        return state

    async def run(self, frozen_goal, thread_id):
        existing = self.checkpointer.load(thread_id)
        state = LoopState(frozen_goal, thread_id)
        if existing is not None:
            state.messages = list(existing.messages)
            state.plan = list(existing.plan)
            state.past_steps = list(existing.past_steps)
            state.frozen_goal = existing.frozen_goal  # never let obs rewrite
        self.log.info("loop_start pattern=%s goal_hash=%s",
                      self.pattern.value,
                      hashlib.sha256(frozen_goal.encode()).hexdigest()[:8])
        if self.pattern is LoopPattern.REACT:
            return await self.run_react(state)
        return await self.run_plan_execute(state)
```

**Behavior encoded**: Full-jitter HTTP retries (transport only); breaker closed->open->half-open; three-clock budget (turns/super-steps/dollars); remaining_steps<2 wrap-up; identical-call hash circuit N=3; critic off without oracle (Huang); P&E workers without planner seeing observations; max_replans=2; frozen goal never rewritten; HITL on irreversible tools; checkpointer keyed by thread_id.

### 6.2 Reflexion Loop with External Evaluator Grounding

```python
"""
Reflexion-style loop with external evaluator grounding.
Stores verbal critiques in episodic memory for cross-task reuse.
Key design rule: the evaluator MUST be external (tests, validator, retrieval)
-- never the LLM judging itself (Huang trap).
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

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
    """Stores verbal reflections indexed by task type for cross-episode reuse.
    When stored in a vector DB, this becomes an emergent skill library."""
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, task_type: str, reflection: str, score: float) -> None:
        self.entries.append({
            "task_type": task_type, "reflection": reflection, "score": score,
        })

    def retrieve(self, task_type: str, top_k: int = 3) -> list[str]:
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
    max_attempts: int = 3,    # AlfWorld used 12; production cap at 2-3
    pass_threshold: float = 0.8,
) -> dict[str, Any]:
    """
    Generate -> Evaluate (EXTERNAL) -> Reflect -> Regenerate.
    Stops when evaluator score exceeds pass_threshold or max_attempts reached.
    Critical: evaluator must NOT be the same LLM (Huang trap).
    """
    prior_reflections = episodic_memory.retrieve(task_type)
    attempts: list[dict[str, Any]] = []

    for attempt in range(max_attempts):
        # Build actor prompt with episodic memory + prior attempt feedback
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
                f"Evaluator: {last['eval_feedback']}\n"
                f"Reflection: {last['reflection']}"
            )

        actor_prompt = (
            f"{system_prompt}\n\nGoal: {goal}"
            f"{reflection_context}{attempt_feedback}"
        )

        # Generate
        response = actor_client.chat(
            messages=[{"role": "user", "content": actor_prompt}]
        )
        output = response.content_text

        # Evaluate (EXTERNAL -- not the LLM judging itself)
        eval_result = evaluator.evaluate(output, {"goal": goal, "attempt": attempt})
        logger.info("Attempt %d | score=%.2f | passed=%s",
                    attempt, eval_result.score, eval_result.passed)

        if eval_result.passed and eval_result.score >= pass_threshold:
            attempts.append({"output": output, "score": eval_result.score,
                           "eval_feedback": eval_result.feedback, "reflection": None})
            return {"answer": output, "attempts": attempts,
                    "exit_reason": "passed", "final_score": eval_result.score}

        # Reflect (verbal critique grounded in evaluator feedback)
        reflect_prompt = (
            f"Task: {goal}\nYour output:\n{output}\n\n"
            f"External evaluator feedback:\n{eval_result.feedback}\n"
            f"{'Error: ' + eval_result.error_details if eval_result.error_details else ''}\n\n"
            f"Analyze what went wrong. Be concrete -- reference specific parts."
        )
        reflection = reflector_client.chat(
            messages=[{"role": "user", "content": reflect_prompt}]
        ).content_text

        attempts.append({"output": output, "score": eval_result.score,
                        "eval_feedback": eval_result.feedback, "reflection": reflection})
        episodic_memory.add(task_type, reflection, eval_result.score)
        prior_reflections.append(reflection)

    return {"answer": attempts[-1]["output"] if attempts else None,
            "attempts": attempts, "exit_reason": "max_attempts_exhausted",
            "final_score": attempts[-1]["score"] if attempts else 0.0}
```

### 6.3 Budget Enforcement with Cost Tracking

```python
"""
Pre-call budget enforcement with structured cost tracking.
Blocks the API call BEFORE it happens, rather than alerting after.
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.budget")


@dataclass
class CostTracker:
    """Tracks cumulative cost and enforces ceilings BEFORE each API call."""
    ceiling_usd: float = 5.0
    ceiling_tokens: int = 500_000
    wall_clock_limit_s: float = 300.0  # 5 minutes

    pricing: dict[str, tuple[float, float]] = field(default_factory=lambda: {
        "gpt-4o": (2.5e-6, 10.0e-6),
        "gpt-4o-mini": (0.15e-6, 0.60e-6),
        "claude-sonnet-4": (3.0e-6, 15.0e-6),
        "claude-opus-4": (15.0e-6, 75.0e-6),
        "claude-haiku-3.5": (0.80e-6, 4.0e-6),
    })

    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    call_count: int = 0
    start_time: float = field(default_factory=time.monotonic)
    ledger: list[dict[str, Any]] = field(default_factory=list)

    def pre_call_check(self, model, estimated_input, estimated_output=500):
        """Raise BudgetExceededError BEFORE the call. Enforcement, not alerting."""
        ip, op = self.pricing.get(model, (10.0e-6, 30.0e-6))
        estimated = estimated_input * ip + estimated_output * op

        if self.total_cost + estimated > self.ceiling_usd:
            raise BudgetExceededError(
                f"Est ${estimated:.4f} would exceed ceiling ${self.ceiling_usd:.2f} "
                f"(current: ${self.total_cost:.4f})")

        projected = (self.total_input_tokens + self.total_output_tokens
                     + estimated_input + estimated_output)
        if projected > self.ceiling_tokens:
            raise BudgetExceededError(f"Projected {projected:,} > ceiling {self.ceiling_tokens:,}")

        elapsed = time.monotonic() - self.start_time
        if elapsed > self.wall_clock_limit_s:
            raise BudgetExceededError(f"Wall clock {elapsed:.0f}s > {self.wall_clock_limit_s:.0f}s")

    def record_call(self, model, input_tokens, output_tokens, latency_ms, label=""):
        ip, op = self.pricing.get(model, (10.0e-6, 30.0e-6))
        cost = input_tokens * ip + output_tokens * op
        self.total_cost += cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.call_count += 1
        self.ledger.append({
            "call": self.call_count, "model": model,
            "in": input_tokens, "out": output_tokens,
            "cost": round(cost, 6), "cumulative": round(self.total_cost, 6),
            "latency_ms": round(latency_ms, 1), "label": label,
        })

    def summary(self):
        elapsed = time.monotonic() - self.start_time
        return {
            "total_cost_usd": round(self.total_cost, 4),
            "total_calls": self.call_count,
            "wall_clock_s": round(elapsed, 1),
            "utilization_pct": round(self.total_cost / self.ceiling_usd * 100, 1),
        }


class BudgetExceededError(Exception):
    pass
```

---

## 7. Failure Modes & Mitigations

### 7.1 Failure Taxonomy

A mid-2026 production taxonomy identifies six categories:

| Category | Failure Modes | Detection | Priority |
|---|---|---|---|
| **Tool Interface** | Tool selection error, schema mismatch, empty `input: {}` | Schema validation, type checking | Highest (frequent, easy fix) |
| **Termination** | Premature stop, infinite loop, budget exhaustion | Iteration caps, budget checks | High (most common) |
| **State** | Context exhaustion, memory pollution, hallucinated state | Token counting, state validation | Medium (growing context) |
| **Drift** | Semantic, reasoning, coordination, behavioral drift | Hard -- late surfacing; goal reiteration | Medium (hard to detect) |
| **Coordination** | Sub-agent loss, race conditions, orchestration overhead | Heartbeat, timeouts | Multi-agent only |
| **Adversarial** | Prompt injection, reward hacking, alignment faking, PlanFlip | Input sanitization, sandboxing | Catastrophic but rare |

**Reliability at scale**: Five agents at 95% individual accuracy deliver ~77% overall success. **88%** of failures trace to infrastructure gaps, not model quality.

### 7.2 Detailed Handler Mapping

| Class | Examples | Handler |
|---|---|---|
| **Transient** | 408/429/5xx/529, TLS reset, MCP disconnect | Full jitter; same idempotency key; last-good catalog |
| **Permanent** | 400 schema, 401/403, `refusal`, spend-cap 429 | Fail the hop; **do not** failover schema 400s |
| **Poison pill (loop)** | Identical `(tool, args)` failures; empty `input: {}`; A-B oscillation; greedy TAO repeat (47% ReAct fail bucket) | N=3 hard-stop that tool; N=6 abort stream; name-window for ping-pong |
| **Semantic** | PlanFlip / goal hijack via tool output; schema-valid unauthorized refund | Frozen goal + RBAC + HITL; not a retry |
| **Soft fuse as success** | "Sorry, need more steps..."; Claude `error_max_turns` parsed as text | Metric + eval fail -- this is NOT a success |
| **Fuse mismatch** | Subagent 25 vs parent 1000; `pause_turn` as `end_turn`; `max_tokens` mid-`tool_use` | Propagate config; resend; raise `max_tokens` |
| **Critic regression** | Huang -6.5 pp; Reflexion without tests -8 pp | Disable critic unless oracle exists |

### 7.3 Poison Repeating Circuit (Algorithm)

1. Canonicalize args (JSON key sort; strip ephemeral timestamps).
2. Key = `(tool_name, args_hash)` or include `output_hash` for "same in and out."
3. After **N=3** identical **failures**: hard observation ("do not retry; answer or replan") and optionally remove the tool from `allowed_tools`.
4. After **N=6** empty calls: abort the **stream**.
5. Pagination: cap `page` / `offset`. Oscillation: last-k tool-name window.

**Important**: A looping `create_charge` with a **new** model-invented UUID is a **duplicate-charge** bug -- hop hash != Stripe idempotency key. Derive money keys from `call_id` / workflow ids.

### 7.4 Circuit Breaker Pattern

One per **(provider, model)** for LLM **and** one per **tool-class**. Open on high **5xx/529/timeout** rate. **Do NOT** open solely on 429-with-Retry-After. Half-open: probe with a **cheap read**, not `issue_refund`.

```
           5xx/529/timeout rate >= threshold           probe success
  +--------+  ------------------------------------->  +------+  ------> CLOSED
  | CLOSED |                                          | OPEN |
  +---+----+  429 with Retry-After = throttle         +--+---+
      |       (stay CLOSED; sleep)                       | timer (30s)
      | success resets window                            v
      |                                          +----------+
      +------------------------------------------| HALF_OPEN|-- probe fail --> OPEN
                                                 | 1 cheap  |
                                                 | read     |
                                                 +----------+
```

**Fallback chain**: primary (Sonnet 5 / GPT-6-sol) -> secondary vendor (same IR) -> **deterministic** `{"status":"degraded"}` (no charge, no email). Tool-class open -> queue / HITL, not a second processor. **PermanentError on schema / RBAC does NOT failover.**

### 7.5 Durable Execution

**Problem**: Most agent implementations run synchronously in memory. If anything interrupts the loop, state disappears. Agent workflows are long-running (minutes to hours), need to survive infrastructure failures, and require exactly-once semantics for side effects.

**Critical distinction**: Session memory is not durable execution. Saving chat history helps an agent remember, but does not prove which shell command ran or whether a retry would duplicate a side effect.

**Temporal's model**: Replays event history to reconstruct in-memory state after a crash. On crash recovery, the workflow re-executes from the beginning; for each step already in the journal, the cached result is returned immediately (no re-execution). **Critical pitfall**: Workflows must be deterministic. LLM calls are inherently non-deterministic, so they must be wrapped as "Activity" steps whose results are journaled on first execution and never re-run on replay.

**Continue-As-New**: When event history grows too large (multi-MB 25-hop transcripts), the workflow atomically completes and starts a new run with the same workflow ID, carrying forward only essential state.

**LangGraph checkpointing vs durable execution**: LangGraph saves state after each step. However, it lacks automatic failure detection -- if the process crashes, no supervisor notices. "Checkpointing says: 'I saved your state. You take it from here.' Durable execution says: 'Your agent workflows will run to completion.'"

**Checkpointer durability modes**:
- `"sync"`: Snapshot before next super-step (safest; use around refund HITL)
- `"async"`: While next step runs (small risk of losing latest checkpoint)
- `"exit"`: Only on graph exit -- **loses** all mid-run state on pod kill

**Crash mid-hop**: The interrupted node restarts from the top -- side effects before `interrupt()` run **twice** unless idempotent (Stripe idempotency keys; Temporal Activity id).

**Cost argument**: A 20-step workflow at $0.05/step: without durable execution, crash at step 18 wastes $1.00 (re-run all 20). With durable execution, resume at step 18 costs $0.10.

**2025-2026 market**: Temporal ($5B valuation, 9.1 trillion lifetime action executions), AWS Durable Functions, Cloudflare Workflows (GA), Inngest, Restate, Hatchet, DBOS.

### 7.6 Idempotent Tool Design

When a tool call fails partway and the agent retries, the tool must produce the same result whether it runs once or three times. Patterns:
- Idempotency keys for API calls (derive from `call_id` / workflow id, NOT model-invented UUIDs)
- Upserts instead of inserts
- Check-before-write for state mutations
- Temporal Activity IDs for exactly-once execution

### 7.7 Max-Iteration Is a Circuit, Not a Success Signal

`GraphRecursionError` means **you did not design a stop**. Catching it for a partial answer is acceptable **only** if pending writes persisted and no payment Activity is in doubt.

- OpenAI `error_handlers["max_turns"]` is the explicit policy
- Claude `error_max_turns` has **no** `result` -- do not treat it as an assistant message
- `create_react_agent`'s "Sorry, need more steps..." is a **silent success-shaped failure** (SQL agents historically hit this under default 25)
- Raising Deep Agents dcode to **2000** only **delays** an infinite loop -- pair with hash circuit

---

## 8. Security & Governance

### 8.1 Zero-Trust Principles

The planner (ReAct thought, P&E JSON, HuggingGPT task list, LLMCompiler DAG) is **untrusted**.

- OWASP LLM01: user **and retrieved** content can alter behavior
- LLM06 Excessive Agency: least privilege, log, rate-limit
- **PlanFlip**: planning-phase injection in tool outputs; one poisoned entry redirects all n sub-tasks; keyword filters can have **detection rate = 0.00**
- MCP **tool descriptions** loaded at session init are an injection channel (Invariant Labs; cross-server shadowing; CVE-2025-54136 CVSS 8.8)

### 8.2 Zero-Trust MCP

Remote MCP servers are OAuth 2.1 resource servers:
- RFC 9728 metadata; **RFC 8707** `resource` indicator on auth and token requests
- PKCE; MUST validate audience; MUST NOT token-passthrough (confused deputy)
- Gateway: terminate OAuth, RFC 8693 exchange to upstream
- Pin manifests `hash(description+schema)` against rug-pulls
- Re-validate authorization **at execution**, not only at plan-approval
- DNS rebinding: pin TS SDK >= 1.24.0 / Python >= 1.23.0 (CVE-2025-66414 / 66416)

### 8.3 Tool RBAC

Never take identity from model JSON. Bind principal from session JWT / Temporal memo / MCP access token.

Map `(principal, tenant, tool, args_shape)` -> allow / deny / HITL. Least privilege per plan node: one named tool per P&E step. `allowed_tools` per hop so an observation cannot enable `shell`. Parse P&E plans as schema-constrained JSON; reject unknown tool names before the executor runs.

### 8.4 Risk-Tiered Approval Design

| Tier | Action Type | Authorization |
|---|---|---|
| **LOW** | Read-only queries, search, compute | Auto-approve. No interrupt. Log for audit. |
| **MEDIUM** | Write to staging, draft emails, create branches | Policy check at invocation time. Route to human if escalated. 5-min timeout. |
| **HIGH** | Financial txns, production writes, admin operations, external comms | Mandatory human approval. Workflow pauses (durable). Full context in approval UI. No timeout -- wait indefinitely. |
| **CRITICAL** | Irreversible destructive ops, regulatory actions | Multi-party approval. Manager + domain expert. Hash-chained audit trail. |

**Key insight**: "Rubber-stamping is worse than no gate at all, because it creates the appearance of oversight without the substance." Low-risk reversible actions should run without interruption. Forcing approval on routine actions trains reviewers to rubber-stamp everything.

**HITL as a durability primitive**: Suspends the agent at a named checkpoint, writes full state to persistent log, releases the process thread entirely, and resumes only when approval arrives (minutes or days later). Show **uneditable, sanitized** preview of name+args -- do not let the model narrate "I will refund $X" without the actual args in the interrupt payload.

### 8.5 Enterprise Identity & Governance

**The governance gap**: Deloitte 2026 -- only **21%** of organizations have a mature governance model for agentic AI. Autonomous agents outnumber humans **82:1** in enterprise environments, yet only **22%** treat agents as identity-bearing entities with formal access controls.

**Five questions the identity model must answer**: Which agent is acting? Whose authority is it using? What may it access? Which actions require approval? How can its access be withdrawn?

**Delegated authority model** (Forbes, Aug 2026): Govern delegated authority, not individual actions. Define delegation boundaries, escalation policies, acceptable risk, and automatic authority withdrawal conditions. This scales where per-action approval does not.

### 8.6 PII Pipeline

DLP at ingress **and** executor **before** checkpoint/trace.
- OpenAI Agents: `trace_include_sensitive_data` / env default **true** unless set false -- if false, spans emit but LLM I/O and tool args/outputs are stripped
- LangSmith / PostgresSaver store **full state** -- these are PII stores
- Reflexion buffers and Self-Refine histories are durable injection + PII
- WORM: placeholder -> hash, not plaintext

### 8.7 Immutable Hop Audit

Every hop must produce an immutable record: `correlation_id`, tenant, `thread_id`, hop, pattern (ReAct/P&E), `stop_reason`, frozen-goal hash, tool name, `call_id`, **hashed** args, policy/HITL decision, hash-circuit count, breaker state, `total_cost_usd` / `num_turns`, model id. Provider traces are not a SIEM.

**Regulatory drivers**: NIST AI Agent Standards Initiative (Feb 2026). EU AI Act Article 14 (human oversight for high-risk AI), Article 50 transparency duties (effective Aug 2, 2026). DORA mapping to human oversight requirements.

---

## 9. System Design Scenarios

### Scenario 1: Customer-Support ReAct with a 10-Turn Cap

**Problem statement.** Multi-tenant support copilot: **1k conversations/day**, peak **100 concurrent** tickets. Job is 1-4 hops: retrieve ticket + policy + optional mutation (refund, email). Latency SLO is **seconds**. Mix: 70% 1-hop ($24/1k), 25% 3-hop ($38/1k), 5% 10-hop ($85/1k) -> ~$31/day model + $2 search SaaS. A 25-hop runaway fleet costs ~$187/day (6x) plus incident review if one refunded twice.

**Architecture:**

```
                +------------------------------------------------------+
                | EDGE  auth, tenant TPM, correlation-id, PII redact   |
                | thread_id = tenant:ticket_id  (never shared const)   |
                +---------------------------+--------------------------+
                                            |
                +---------------------------v--------------------------+
                | CONTROL  Temporal workflow = tenant:ticket            |
                |  LOOP CONTROLLER = ReAct (create_agent / Runner)     |
                |  HOP COUNTER: max_turns=8-10; remaining_steps wrap   |
                |               max_budget_usd=$0.25/ticket; hash N=3  |
                |  CHECKPOINTER sync around refund HITL                |
                |  CRITIC off (no oracle on chat; Huang trap)          |
                |  Activity LLM: SDK max_retries=0; parallel reads OK  |
                |  Activity tools: get_ticket, search_policy,          |
                |    issue_refund (HITL), send_email (HITL)            |
                |  CircuitBreaker(llm) != CircuitBreaker(payments)     |
                +------+------------------------------+----------------+
                       |                              |
                       v                              v
                +----------------+            +-----------------------+
                | DATA  Gen      |            | DATA  Executor        |
                | cached prefix  |            | Stripe same key after |
                | tools[] frozen |            | 500; KB search cap pg |
                +-------+--------+            +----------+------------+
                        |                                |
                +-------v--------+            +----------v------------+
                | TOOL PROXIES   |            | PERSIST  ckpt + WORM  |
                | ticket body =  |            | hop, stop_reason,     |
                | untrusted obs  |            | hashed args, HITL     |
                | no shell / MCP |            | Kafka: Stripe->Signal |
                +----------------+            +-----------------------+
```

**Trade-off matrix:**

| Dimension | A. Unbounded (no hash, Reflexion on all) | B. Recommended: ReAct max_turns=8-10 | C. Outer P&E for every ticket |
|---|---|---|---|
| **Cost/1k** | Mix + Reflexion 9-call **+$108/day** if on everyone | ~$31/day + $2 search; fuse stops $187 incident | Planner overhead on every 1-hop ticket |
| **Latency** | 12 AlfWorld-style trials; minutes | p50 1-3 hops / <8s; p99 = HITL SLA | Planner 1.88s leftover even when tools fast |
| **Security** | Ticket injection loops tools forever; critic without oracle edits correct answers | Frozen goal; allowlist 4 tools; HITL irreversible; traces stripped | Plan JSON is another injection surface |

**Decision**: B is the only design that treats `max_turns=10` as a **financial control** matching the finding that extra HotpotQA steps recover only 0.84% of correct trajectories.

### Scenario 2: Batch Plan-and-Execute Research with a Replan Cap

**Problem statement.** Overnight research jobs: multi-hop web research, **8+ independent** searches, one synthesis. Failure mode is cost and loop, not 800ms p95. LLMCompiler showed interleaved ReAct uses **20k** input tokens vs Compiler **2.8k** on Movie Rec (~7.1x fewer) and **6.73x** cheaper. Constraint: `max_replans=2`; inner executor `max_turns=3` + single-tool allowlist per plan node; `max_budget_usd` $2-5/job; web-search count cap ($10/1k).

**Architecture:**

```
  +-----------+    +-----------------------------------------------------+
  | Batch job |---->| CONTROL  Temporal: one Workflow per research job    |
  | analyst   |    |  LOOP CONTROLLER = P&E (LLMCompiler DAG / ReWOO)    |
  |           |    |  PLAN (schema-constrained JSON, tool enum)          |
  |           |    |  FETCH-READY => worker Activities (Haiku / luna)    |
  |           |    |    each worker: inner ReAct max_turns=3, 1-tool ACL |
  |           |    |  JOIN / REPLAN cap 2 (empty evidence only)          |
  |           |    |  SOLVER (Sonnet / GPT-6-sol) + optional CRITIC      |
  |           |    |  HOP COUNTER: max_tool_calls + max_budget $2-5     |
  |           |    |  HASH CIRCUIT per worker; parent != subagent limit  |
  +-----------+    +----------+---------------------------+--------------+
                              |                           |
                              v                           v
                   +-------------------+     +---------------------------+
                   | DATA  Generation  |     | TOOL PROXIES  web_search  |
                   | planner cached    |     | snippets = untrusted      |
                   | workers cheap     |     | HITL before send/write    |
                   +-------------------+     +---------------------------+
```

**Trade-off matrix:**

| Dimension | A. Interleaved ReAct (20k-token shape) | B. Recommended: P&E/DAG + inner ReAct max_turns=3 | C. LATS / 12-trial Reflexion |
|---|---|---|---|
| **Cost/1k** | ReWOO 5x tokens; $20.46/1k vs $3.04/1k (2023) | 3.37-6.73x cheaper; Haiku workers; inner cap bounds poison | LATS 30 trajectories; Reflexion $1.02/task x fleet |
| **Latency** | ReAct Movie Rec 20.47s; loops add unbounded p99 | Compiler 5.47s; streaming planner 1.3x; hours-OK SLO | LATS WebShop **1,066s** vs Compiler ~11s (101.7x) |
| **Security** | One injected snippet steers all subsequent TAO hops | Frozen goal; plan schema enum; inner allowlist = 1 tool; PlanFlip blast radius 3 hops | More samples = more injection surface |

**Decision**: B is the only design that keeps the **planner prefix off the per-search hot path** while bounding goal hijack to an inner 3-turn worker.

### Scenario 3: Autonomous Code Review Pipeline

**Problem statement.** A financial services firm processes 200+ pull requests/day across 15 microservice repositories. Human reviewers spend 40% of review time on mechanical issues. The security team requires every PR touching payment or PII services to pass an automated security check before merge. Current SAST tools produce 30%+ false-positive rates.

**Architecture:**

```
+-----------------------------------------------------------------------+
|                         PR WEBHOOK (GitHub)                             |
+---------------------------------+-------------------------------------+
                                  |
+---------------------------------v-------------------------------------+
|                    ORCHESTRATOR (LangGraph DAG)                         |
|  - Classify PR: {payment, pii, general}                                |
|  - Fan-out to parallel sub-agents                                      |
|  - Fan-in: merge, deduplicate, rank findings                           |
|  - Global timeout: 120s                                                |
+----+------------------+-----------------+-----------------------------+
     |                  |                 |
     v                  v                 v
+---------+       +-----------+     +------------+
| Security|       |  Style    |     | Performance|
| Agent   |       |  Agent    |     | Agent      |
| ReAct   |       | ReAct     |     | ReAct      |
| max=5   |       | max=3     |     | max=3      |
| Tools:  |       | Tools:    |     | Tools:     |
| Semgrep |       | ruff      |     | complexity |
| CodeQL  |       | mypy      |     | profiler   |
+---------+       +-----------+     +------------+
     |                  |                 |
     v                  v                 v
+---------------------------------v-------------------------------------+
|    SELF-REVIEW (Reflexion pass, fresh context)                         |
|    - Evaluator: re-run SAST tools on flagged lines                     |
|    - Remove low-confidence findings                                    |
+-------------------+----------------------+----------------------------+
                    |                      |
                    v                      v
            +--------------+      +------------------+
            | severity <=  |      | severity > WARN  |
            | WARN: auto-  |      | (payment/PII):   |
            | post as PR   |      | HITL approval    |
            | comments     |      | gate before fix  |
            +--------------+      +------------------+
```

**Trade-off matrix:**

| Dimension | A. Monolithic ReAct Agent | B. Parallel Sub-Agents (Proposed) | C. LATS Tree Search |
|---|---|---|---|
| **Cost per PR** | $0.08-0.15 (single long context) | $0.04-0.06 (parallel, isolated) | $0.50-2.00 (tree branching) |
| **Latency** | 30-60s (sequential) | 10-20s (parallel) | 2-5 min |
| **Precision** | Medium (context pollution) | High (domain isolation + Reflexion) | Highest (exhaustive) |
| **Scalability** | ~100 PRs/day | ~500+ PRs/day | ~20 PRs/day (cost prohibitive) |

**Decision**: B wins because it isolates concerns, grounds findings in real SAST tool output, and the Reflexion pass eliminates low-confidence findings. Cost at $0.05/PR = ~$10/day for 200 PRs.

---

## 10. Interview Quick Reference

### Pattern Selection Decision Tree

```
Is the next action predictable from the current state?
+-- YES: Are steps independent with a known dependency graph?
|   +-- YES: Plan-and-Execute (or static DAG if fully deterministic)
|   +-- NO: Plan-and-Execute with re-planning (cap max_replans=2-3)
+-- NO: Does the task require exploration / dynamic tool selection?
    +-- YES: Is single-pass accuracy critical (>95% required)?
    |   +-- YES: LATS (accept the token cost)
    |   +-- NO: ReAct (with Focused ReAct if drift is a concern)
    +-- NO: Is there a reliable external evaluator?
        +-- YES: Reflexion (generate-evaluate-reflect loop)
        +-- NO: ReAct + grounded self-correction (external signal required)
```

### Key Numbers to Memorize

| Number | What |
|---|---|
| **$24 / $38 / $85 / $187 per 1k** | Cached sol 1 / 3 / 10 / 25 hops |
| **~$31/day vs ~$187/day** | 1k-ticket mix vs 25-hop runaway fleet |
| **$108/1k vs $12/1k** | Self-Refine 9 calls vs one-shot (math ~0 pp gain) |
| **$19.59 vs $3.97 per 1k** | ReWOO: interleaved ReAct vs batched (gpt-3.5, 2023) |
| **3.37x / 6.73x cheaper** | LLMCompiler vs ReAct HotpotQA / Movie Rec |
| **1.80x / 3.74x faster** | Same benches (mean latency) |
| **20k vs 2.8k** | Movie Rec ReAct vs Compiler input tokens |
| **47% / 0%** | ReAct fail: repetitive TAO / hallucination rate |
| **0.95^10 = 0.60** | Error compounding: 95% step reliability, 10 steps |
| **10 / None / 25->1000** | OpenAI max_turns / Claude default / LangGraph recursion_limit |
| **2 super-steps** | ReAct model+tools per tool round in LangGraph |
| **N=3 / N=6** | Identical (tool,args) fail circuit / empty-call abort |
| **2-3** | max_replans; Self-Refine M<=4 but production M=1 without oracle |
| **95.5 -> 89.0** | Huang GPT-4 GSM8K after 5 oracle-free calls |
| **91.0 vs 80.1** | Reflexion HumanEval vs GPT-4 baseline |
| **52 vs 60** | Reflexion without tests **hurts** (ablation) |
| **92.7%** | LATS HumanEval pass@1 (GPT-4) |
| **0.32 -> 0.71** | LATS doubled ReAct on HotPotQA EM |
| **1,066 s vs ~11 s** | LATS vs LLMCompiler WebShop latency |
| **600 s / 300 s** | Claude async stall / stream idle watchdog |
| **$0.25 / $2-5** | Support ticket vs research job max_budget_usd |
| **65%** | Enterprise AI failures attributed to context drift/memory loss |
| **41-86%** | Multi-agent system failure rate in production |
| **68 / 6,549** | Confirmed infinite loops / repos examined (IAL-Scan) |
| **$47,000** | LangChain pipeline infinite loop incident (11 days) |
| **ECR/EIR > Acc/(1-Acc)** | Self-correction stability threshold |

### Interview Closer

"The model is an untrusted planner. I dispatch ReAct when the next tool must see the last observation, and plan-and-execute when work is independent -- with `max_turns`, `recursion_limit`, and `max_budget_usd` as three different clocks, a hash circuit on identical `(tool, args)`, remaining-steps wrap-up before the fuse, and no critic unless I have an oracle. Huang paid 5x to lose 6.5 points; I will not."
