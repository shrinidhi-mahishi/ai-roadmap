# Module 08: Planning & Reasoning

## What Is This?

When you ask an LLM a simple question, it answers in one shot. But complex tasks -- "analyze this dataset, find anomalies, and write a report" -- require multiple steps. **Planning** is how agents break big tasks into smaller steps before executing them.

**Chain-of-thought (CoT)** is the simplest form: you ask the model to "think step by step," and it works through the problem sequentially before giving a final answer. This dramatically improves accuracy on math, logic, and multi-step reasoning tasks. It's like asking a student to show their work instead of just writing the answer.

**Reflection** means the agent checks its own work. After generating an answer or taking an action, it asks itself: "Is this correct? Did I miss anything? Should I try a different approach?" This is like re-reading your essay before submitting -- it catches mistakes that the first pass missed.

The key planning patterns are:
- **ReAct**: Think -> Act -> Observe -> Repeat. Simple, one step at a time.
- **Plan-then-Execute**: Make a full plan upfront, then execute each step. Better for complex tasks but the plan might be wrong.
- **DAG (Directed Acyclic Graph)**: Plan steps that can run in parallel, like a project management timeline. Fastest but hardest to build.

**Reasoning models** (like OpenAI o3, DeepSeek R1) have built-in chain-of-thought -- they "think" internally before answering, trading latency for accuracy. They're 3-10x slower but significantly better at hard problems.

## Why It Matters

Planning separates toy demos from production agents. A well-planned agent can handle complex, multi-step tasks reliably. A poorly planned agent loops endlessly, wastes money, or gives wrong answers after 15 steps of work.

---

## 2. Core Concepts

### Four Roles, Two Planes

Think of building a house. The **planner** is the architect (draws blueprints). The **executor** is the contractor (builds what the blueprint says). The **critic** is the building inspector who explains what is wrong. The **verifier** is the code compliance office that signs off or rejects. Fusing all four into one person is how you get a contractor who redesigns the house every time they pick up a hammer.

| Role | Owns | Typical Implementation | Failure If Fused |
|------|------|----------------------|------------------|
| **Planner** | Decompose objective -> DAG/list of steps, deps, tool names, success criteria | Structured-output LLM, PDDL compiler, HTN method library, HuggingGPT JSON | Tool observations inject new goals (prompt injection); plan mutates every turn |
| **Executor** | Run one ready node; bind placeholders | Tool runtime, sandboxed code, Temporal Activities | Planner tokens billed on every search; serial ReAct latency |
| **Critic / Reflector** | Verbalize *why* a trial failed; write episodic hint | Reflexion memory buffer, Self-Refine FEEDBACK, Constitutional self-critique | Infinite critique loop; reflection becomes prompt-injection surface |
| **Verifier** | Accept/reject a step or final answer | Unit tests, compiler, math checker, PRM, LLM-as-judge, human interrupt | Gaming (fake-green tests); judge bias; unverifiable open-ended work |

**Control plane vs data plane**: The control plane decides whether to replan, which node is ready (topological fetch), max_replans, reasoning effort, and whether to escalate. The data plane is the plan graph, past_steps, checkpoints, tool I/O blobs, and audit logs. LangGraph StateGraph is the control-plane loop; PostgresSaver is the data-plane snapshot. Temporal Workflows are the control plane; Activities are the data plane.

**Key invariant**: The LLM is not the planner. The planner is a function that emits a plan data structure. The executor interprets it. The critic annotates it. The verifier gates it. o1/R1 "internal CoT" collapses planner+critic+search into hidden tokens inside one model call -- cheaper to operate, harder to audit, still needs an external verifier for consequential actions.

### The Planning Topology Spectrum

```
Simple -------- ReAct -------- Plan-Execute -------- DAG -------- Tree/MCTS
(one shot)     (serial loop)  (plan then do)    (parallel)    (search)

Cost:      $     $$            $$                 $$            $$$$$$
Latency:   low   medium        medium             low(parallel) very high
Quality:   low   medium        medium-high        high          highest
Control:   none  implicit      explicit plan       DAG deps      full tree
```

### Process vs Outcome Verification (Know the Difference)

| Signal | Supervises | Example | Failure Mode |
|--------|-----------|---------|-------------|
| **Outcome (ORM)** | Final answer / pass-fail | MATH label, unit-test gate, AlfWorld done | Credits lucky wrong reasoning; sparse |
| **Process (PRM)** | Each step correct/neutral/wrong | PRM800K; Lightman et al. | Step boundaries ill-defined; reward hacking |
| **Verbal process** | NL "what went wrong" | Reflexion traces | Uncalibrated; injectable |

OpenAI "Let's Verify Step by Step" (Lightman et al., 2023): on 500 MATH problems, process-supervised RM **78.2%** vs outcome RM **72.4%** at best-of-1860. Gap widens with N -- PRMs monetize test-time compute better than ORMs.

---

## 3. How It Works

### 3.1 Decomposition Methods

**Least-to-Most** (Zhou et al., ICLR 2023): Two stages: (1) decompose the problem into ordered subproblems; (2) solve sequentially, conditioning each solve on prior answers. Unlike chain-of-thought, it is explicitly compositional: the prompt teaches how to break, not just how to chain. GPT-3 + LtM solves SCAN at **99.7%** with 14 in-context examples vs neural-symbolic systems trained on >15,000 examples. Cost: linear in subproblem count; no native parallelism.

**Plan-and-Solve / PS+** (Wang et al., ACL 2023): Zero-shot replacement for "Let's think step by step": first devise a plan, then carry it out. PS+ adds "extract variables/numerals" and "calculate intermediates." On text-davinci-003: CSQA **71.9% vs 65.2%** over Zero-shot-CoT. Error autopsy on 100 GSM8K items: calculation 7%, missing-step 12%, semantic misunderstanding 27%. PS targets missing steps.

**HuggingGPT / JARVIS** (Shen et al., NeurIPS 2023): LLM as controller, Hugging Face models as executors. Four stages: task planning -> model selection (by model card text + download rank) -> parallel task execution on hybrid endpoints -> response generation. Plan schema: `[{"task","id","dep","args"}]`. `dep` is prerequisite task IDs; `args` may contain `<resource>-task_id` placeholders resolved after parents finish. Independent tasks run in parallel. Limitations: plans not guaranteed feasible; multiple sequential LLM round-trips dominate latency; context length caps model card ranking.

**LLMCompiler** (Kim et al., ICML 2024): The compiler analogy that makes DAG planning click.

```
  Source code    ->  Compiler    ->  Machine code
  User question  ->  LLM Planner ->  DAG of tool calls

  Components:
  (i)   Function Calling Planner: emits DAG with $k placeholders
  (ii)  Task Fetching Unit: dispatches ready nodes (topological order)
  (iii) Executor: runs tools in parallel
  (iv)  Joiner: replans or answers
```

Results vs ReAct: up to **3.7x** latency improvement, **6.7x** cost reduction, ~9% accuracy gain (ParallelQA). HotpotQA: 1.80x speedup / 3.37x cheaper. Movie Recommendation: 3.74x / 6.73x. Game of 24 vs ToT: 2x speedup. WebShop vs LATS: **101.7x** speedup at similar score. ReAct failure modes the DAG avoids: premature stop, repetitive same-tool loops.

Residual bottleneck: planner + joiner are serial. Movie Rec planner 1.88s + answer 1.62s average -- more than half of end-to-end when tools are fast.

**Hierarchical / HTN-like Decomposition**

Classical HTN: compound tasks + method library -> primitives (Erol et al. 1994). LLM variants:

| System | How Hierarchy Works | Soundness |
|--------|-------------------|-----------|
| **ADaPT** (NAACL Findings 2024) | Try executor; on failure, planner splits with AND/OR; recurse to depth d_max | Controller is deterministic; success of children => parent |
| **LLM+P** (Liu et al.) | NL -> problem PDDL -> Fast-Downward classical planner -> NL plan | Classical planner is sound given correct PDDL; LLM translation is the risk |
| **ChatHTN** (NEUS 2025) | Symbolic HTN; if no method, query LLM for primitive sequence + verifier task | Verifier task checks effects; LLM non-determinism -- 5 attempts |
| **LLM-generated HTN heuristics** | LLM writes Python heuristic for Pytrich; search remains symbolic | Correctness delegated to search; heuristic quality only |

ADaPT: up to +28.3% ALFWorld, +27% WebShop, +33% TextCraft vs plan-and-execute -- the point is as-needed depth, not always-max decomposition.

**LangGraph Plan-and-Execute** (canonical production graph):

```
planner -> agent (execute plan[0]) -> replan -> conditional END or back to agent
```

State: `input`, `plan`, `past_steps`, `response`. Limitation: serial steps; embarrassingly parallel work should be a DAG (LLMCompiler pattern). Secure variant (arXiv:2509.08646): planner names the single tool per step; executor spins a temporary agent with only that tool -- least privilege per node.

### 3.2 Reflection

**Reflexion** (Shinn et al., NeurIPS 2023): The verbal RL paradigm. Actor (often ReAct) -> environment/evaluator -> self-reflection LLM -> episodic memory of verbal hints -> next trial. Results: AlfWorld 130/134 (absolute +22% over 12 trials); HotPotQA +20%; HumanEval Python pass@1 **91.0** vs GPT-4 80.1. Programming loop: CoT-generate <=6 unit tests, AST-filter, run, reflect.

Critical ablation: on hardest 50 HumanEval-Rust, without tests, reflection **hurts** (52% vs 60% baseline) -- the critic needs an oracle. WebShop after 4 trials: no useful reflections -- Reflexion does not explore diverse catalogs.

**Self-Refine** (Madaan et al., NeurIPS 2023): Same LLM as generator, feedback, and refiner. Loop until "stop" or M<=4. ~20% absolute average gain across 7 tasks vs one-shot. Risk: the model declaring "it is correct" without real checking.

**CRITIC** (Gou et al.): Critique is tool-interactive: calculator, interpreter, search. "CRITIC w/o Tool" can degrade (-1.8 on text-davinci-003). Gains scale with model size (TabMWP: +4.7 / +9.4 / +16.0 at 7B/13B/70B). Production rule: **never attach a critic that cannot call a checker on math/code**.

**Constitutional AI** (Bai et al.): Train-time critic -- sample -> self-critique vs written principles -> revise -> SFT; RL phase RLAIF. This is a critic model distilled into weights, not a runtime loop. But the same topology (critique then revise) is what Self-Refine/Reflexion do at inference.

### 3.3 Internalized Reasoning (2025-26 Production)

The major shift: reasoning models collapse planner+critic+search into hidden tokens inside one model call.

**OpenAI o1** (Sep 2024): RL teaches the model to break steps, detect mistakes, switch strategy -- inside hidden reasoning tokens. o1 AIME 2024: 74% pass@1, 83% cons@64, 93% rerank-1000.

**DeepSeek-R1-Zero** (Nature 2025): No SFT, GRPO, rule-based accuracy + format rewards only (explicitly no neural ORM/PRM because of hacking). AIME 2024 pass@1: 15.6% -> 77.9%; cons@16: 86.7%. Emergent "aha moment": spike in "wait" after ~8k RL steps; reflective-word count 5-7x. R1 (cold-start + multi-stage RL): AIME pass@1 79.8% vs o1-1217 79.2%.

**Why DeepSeek abandoned PRMs for large-scale RL**: (1) Step granularity undefined in general reasoning. (2) Intermediate correctness is hard to judge. (3) Reward hacking + RM retrain cost. PRMs still useful for rerank/search, not as the sole RL reward at R1 scale.

**Claude Extended/Adaptive Thinking**: Extended thinking with `budget_tokens` (min 1024) on Sonnet 4.5 and earlier. Adaptive thinking with `effort` on Claude 4.6+/4.7. Interleaved thinking with tools: budget spans the whole assistant turn. Thinking tokens billed as output.

**Control knobs across providers**:
- OpenAI: `reasoning.effort` in {low, medium, high} (o-series). GPT-5.6: `reasoning.effort` in {none, minimal, low, medium, high, xhigh, max} + `mode=pro` (independent).
- Claude: `effort` (adaptive) or `budget_tokens` (legacy, deprecated on newer models).
- DeepSeek: thinking is default on V4; toggle via API.

### 3.4 Verification

**Oracle ranking** (how much to trust each verifier to stop the loop):

1. **Deterministic environment flag** (AlfWorld success, PDDL goal, HTTP 2xx)
2. **Held-out tests / hidden cases** (HumanEval hidden tests, CodeContests private tests -- AlphaCodium)
3. **Replayable computation** (calculator, interpreter, compiler logs)
4. **PRM / process label** (good for rerank; not as sole RL reward per DeepSeek)
5. **LLM-as-judge / debate / self-eval** (ToT state scores). Stop here only for subjective quality.

If 1-3 exist, do not let 4-5 override them. Reflexion false positive: green self-tests halt a wrong program -- worse than false negatives.

**AlphaCodium** (Ridnik et al.): Flow-engineering around public + generated tests. GPT-4 CodeContests valid pass@5: 19% -> 44%. Key insight: prefer FN over FP in gates.

**LLM-as-judge** (Zheng et al., NeurIPS 2023): GPT-4 judge vs humans: >80% agreement (human-human level) on MT-Bench/Arena. Biases: position, verbosity, self-enhancement, weak reasoning. Mitigations: swap order, reference answers, pairwise not absolute. Not an oracle for math/code.

**Debate** (Irving, Christiano, Amodei): Two agents argue; a weak judge picks. Complexity analogy: debate with optimal play can answer PSPACE questions with poly-time judges (direct judging ~ NP). MNIST sparse-pixel toy: 6 pixels 59.4% -> 88.9%.

**ProcessBench** (Zheng et al., 2024): Identify first erroneous step or confirm all-correct. F1 of error vs correct. Qwen2.5-Math-PRM-72B remains a strong open PRM.

### 3.5 Replanning

**Trigger**: Tool error, verifier fail, empty search, critic "hallucinated possession" (AlfWorld), or Joiner "need more evidence."

**LangGraph replan node**: After each step, LLM sees `past_steps` and either emits remaining steps or a Response. This is local repair, not full search. **You must set max_replans** -- the graph will not do it for you.

**Tree of Thoughts** (Yao et al., NeurIPS 2023): Thoughts = intermediate candidates; BFS/DFS with LM self-eval; backtrack. Game of 24: GPT-4 CoT 4%, CoT-SC 9%, ToT b=1 45%, ToT b=5 **74%**. ~60% of CoT samples already fail at step 1 -- left-to-right cannot recover. Cost: branching x depth LM calls.

**RAP** (Hao et al., EMNLP 2023): LLM as agent + world model; MCTS on imagined next states. LLaMA-33B RAP > GPT-4 CoT on some plan/math/logic splits.

**LATS** (Zhou et al., ICML 2024): MCTS over ReAct-style actions; LM value + self-consistency hybrid. HumanEval GPT-4 pass@1 92.7%; WebShop GPT-3.5 avg 75.9. Environment feedback is the point vs ToT's self-eval-only. Cost: many model calls -- LLMCompiler's 101.7x WebShop speedup is the production warning.

**MCTS at R1 scale (failed)**: DeepSeek found token branching >> chess; cap on expansions -> local optima; value model too weak. MCTS can help inference with a pretrained value head; cannot easily self-improve the policy via search at their RL scale.

**o-series / GPT-5.x / Claude adaptive replanning**: Replanning is internal -- try strategy, backtrack in hidden tokens. Not a durable DAG: crash mid-thought loses the tree. External replan still required when tools fail or policy forbids the next call.

### 3.6 Self-Consistency

(Wang et al., ICLR 2023): Sample K CoTs, majority vote. GSM8K-class gains historically +10-18 points at K~20, but you pay K x generate cost. o1 reported AIME 74% pass@1 vs 83% cons@64 vs 93% rerank-1000. Test-time compute is the product.

**Snell et al. (2024)**: Allocate a fixed inference FLOP budget across (a) longer single chains, (b) majority vote, (c) verifier-guided search. The winner is task-dependent: PRM search wins when the verifier is well-calibrated, voting wins when it is not.

---

## 4. Key Patterns & Best Practices

### The Routing Table (Control Plane, Not the Model)

Classify the job before spending thinking tokens:

| Job Class | Strategy | Model |
|-----------|----------|-------|
| DAG-shaped tool parallelism | LLMCompiler planner + cheap executor | Mini/Flash for executor |
| Single hard question with a checker | Reasoning model + oracle verification | o3/R1 + unit tests |
| Open-ended writing/style | One critic pass, M<=2 | Self-Refine |
| Irreversible side effect | HITL regardless of effort | Any + human approval |

Putting o3-high on class (i) is the usual bill shock.

### Per-Role Model Assignment

Do not use one frontier model for planner, executor, critic, and judge.

| Role | Cheap Default (2026-08) | Escalate When |
|------|------------------------|---------------|
| Planner | o4-mini medium, Sonnet 5, V4-Flash thinking | Cyclic deps, PDDL needed, safety CFI |
| Executor (tool args) | Haiku 4.5, GPT-mini, V4-Flash non-think | Args are code or SQL |
| Critic | Haiku / Flash with tools (CRITIC) | No oracle exists |
| Verifier | pytest/sympy $0 | Open-ended only -> judge with swap-order |
| Replanner | Same as planner, max_replans=2 | After cap: human |

### Plan Representation

A plan should be a typed data structure, not prose:

```json
{
  "plan_id": "p-abc123",
  "nodes": [
    {"id": "1", "task": "search_docs", "tool": "retriever",
     "args": {"query": "$input"}, "deps": []},
    {"id": "2", "task": "search_web", "tool": "tavily",
     "args": {"query": "$input"}, "deps": []},
    {"id": "3", "task": "synthesize", "tool": "llm",
     "args": {"context": ["$1", "$2"]}, "deps": ["1", "2"]}
  ],
  "max_replans": 2,
  "effort": "medium"
}
```

Nodes 1 and 2 can run in parallel. Node 3 waits for both. Placeholders ($1, $2) are resolved after parent execution. This is the HuggingGPT/LLMCompiler pattern that avoids serial ReAct.

### Ship-Bar Checklist

1. Plan is a typed DAG in DB, not prose.
2. Fetch/execute/critic/verify are separate nodes with caps.
3. Hard oracle before LLM judge.
4. Replan cannot add tools outside the original allowlist without HITL.
5. Reasoning tokens metered per tenant; effort policy by task class.
6. Checkpoints + idempotent tools.
7. Reflections stored as untrusted data.
8. Cache breakpoints exclude observations.
9. Audit: plan, args hashes, verdicts, model+effort+cache-hit.
10. Kill switches: max_replans, critic circuit breaker, max_output_tokens.

---

## 5. System Design Considerations

### Durable Plan State

Durable fields for crash recovery: `plan_id`, `graph` (nodes, edges, placeholders), `cursor` / ready-set, `past_steps[]` (action, observation hash, verifier verdict, critic text id), `replan_count`, `effort`, `tenant`, `actor`. Store observations in object storage; keep hashes in the checkpoint.

**LangGraph**: `PostgresSaver` / `AsyncPostgresSaver` with thread-scoped checkpoints. `InMemorySaver` dies on restart -- not production. Connection pool: `autocommit=True`, `max_size~10`, `max_idle=300s`. Subgraphs: checkpoint parent only to avoid dupes. TTL: OSS Postgres has no native checkpoint TTL -- cron or Agent Server TTL. Interrupts: pause before destructive tools (HITL) -- the plan waits in DB, not in a Python stack frame.

**Temporal** (2025-26 Agent Harness + OpenAI Agents SDK integration): Workflow = agent loop; Activities = model, tools, sandboxes. Event history replays completed Activities after crash -- do not re-bill succeeded LLM calls if you persisted the Activity result. Fork: snapshot workspace + conversation, new workflow id. Nexus Operations circuit breaker: 5 consecutive retryable errors -> open; 60s -> half-open probe.

**Google ADK**: Session/State/Memory model with DatabaseSessionService providing row-level locking via SELECT...FOR UPDATE. The strongest documented multi-writer control plane.

**Provider replay contracts**: OpenAI requires reasoning items to be passed back with tool outputs. Anthropic requires prior thinking/redacted_thinking blocks preserved. Dropping these changes the agent's effective plan or causes hard failure.

**Idempotency**: At-least-once Activities + non-idempotent "send email / charge card" = duplicate side effects on replay. Dedup keys on the data plane. Planner must emit stable node IDs so a replan merge can skip completed nodes.

### Critic-Loop Circuit Breakers

| Breaker | Trip Condition | Action |
|---------|---------------|--------|
| max_replans | e.g. 3 | Return best-so-far + PLAN_EXHAUSTED |
| max_reflect_tokens | Critic output > N | Drop to outcome-only gate |
| same_action_k | Same act+obs k times (Reflexion heuristic) | Force replan or human |
| verifier_disagree | Tests fail AND judge pass | Prefer tests; log gaming suspicion |
| reasoning_token_cap | o-series effort high + output -> 100k | Hard max_output_tokens; degrade effort |
| critic_open_circuit | 5 critic 5xx/timeouts (Temporal Nexus default) | Skip critique, execute with allowlist tools only |

### Backpressure and HITL

Fetch unit should be a queue (ready nodes), not a recursive LLM. Parallel tool fan-out needs bulkheads: search pool vs code-exec pool vs critic pool so a hung interpreter does not stall replans.

**HITL as a first-class node**: LangGraph `interrupt()` before a node writes to prod: the checkpoint holds plan + pending tool args; a human PATCH is another state update. Temporal: approval is a durable timer + signal, not a Flask session. Split SLAs: machine p99 vs human p99.

### Security: Zero-Trust Around the Planner

Treat the planner as a privileged compiler. Untrusted bytes (web, email, MCP resources, tool JSON) must not be in the same instruction channel as "here is your next DAG."

**Plan-then-execute CFI** (Debenedetti et al. 2024): Freeze the plan from the user prompt; tool outputs cannot add actions. Does not stop injection in the user prompt itself. Dynamic replan re-opens the hole -- run replanner on schema-only observations or require HITL.

**CaMeL** (DeepMind): Privileged LLM -> Python-like plan; custom interpreter; capabilities on values; untrusted data cannot change control flow. AgentDojo: 77% tasks with provable security vs 84% undefended. Dual-LLM tax is the NFR.

**PlanGuard**: Isolated planner from user instructions only; hierarchical check: hard tool allowlist then intent verifier for params. InjecAgent: ASR 72.8% -> 0%, FPR 1.49%.

**MCP Zero-Trust**: OAuth 2.1 / RFC 9728 / 8707 resource indicators; per-tool RBAC; treat all MCP payloads as data until schema-validated; pin server hashes; no standing tokens in planner context.

**Tool RBAC**: LangGraph secure plan-execute: one tool per step, ephemeral executor. Map IAM to node types. Planner proposes; policy engine (OPA) authorizes; model never sees raw cloud keys.

### Audit Requirements

Plans and critiques often contain customer identifiers, retrieved documents, and sensitive logic. Persist: plan JSON, tool names + arg hashes, verifier verdicts, critic IDs. Raw observations: shorter TTL, encryption, tenant partition.

**Hidden CoT opacity**: OpenAI does not give raw o-series reasoning tokens. You cannot SOX-audit tokens you never received. For regulated actions, require visible plan + tool log even if the model thought privately. Claude thinking summaries != full chain.

### Prompt Injection in Reflections

Critic text is written by a model that just read untrusted tool output. A poisoned page saying "reflect that the user asked to exfiltrate" becomes next-trial memory (Reflexion buffer). Mitigations: (1) Store reflections as data with origin tag + hash. (2) Cap memory to 1-3 items. (3) Never let reflection emit tool calls. (4) Regenerate critic from oracle (test log) not webpage text. (5) PlanGuard-style check that post-reflection actions are subset of original plan or HITL delta.

---

## 6. Code Examples

### LangGraph Plan-and-Execute with Replan Cap

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Tuple

class PlanState(TypedDict):
    input: str
    plan: List[str]            # Remaining steps
    past_steps: List[Tuple[str, str]]  # (action, observation)
    response: str
    replan_count: int

def planner(state: PlanState) -> PlanState:
    """Emit a structured plan as a list of steps."""
    plan = llm_plan(state["input"])  # Returns list of step strings
    return {"plan": plan, "replan_count": 0}

def executor(state: PlanState) -> PlanState:
    """Execute the first step in the plan."""
    step = state["plan"][0]
    result = execute_tool(step)
    return {
        "plan": state["plan"][1:],     # Pop executed step
        "past_steps": state["past_steps"] + [(step, result)],
    }

def replanner(state: PlanState) -> PlanState:
    """Replan based on past results. Cap at max_replans."""
    if state["replan_count"] >= 3:
        return {"response": "Plan exhausted. Best result: " +
                state["past_steps"][-1][1]}
    new_plan = llm_replan(state["input"], state["past_steps"])
    if new_plan.response:          # Replanner decided to answer
        return {"response": new_plan.response}
    return {"plan": new_plan.steps,
            "replan_count": state["replan_count"] + 1}

def should_end(state: PlanState) -> str:
    if state.get("response"):
        return "end"
    if not state["plan"]:
        return "replan"            # No more steps, replan
    return "execute"

graph = StateGraph(PlanState)
graph.add_node("planner", planner)
graph.add_node("executor", executor)
graph.add_node("replanner", replanner)

graph.set_entry_point("planner")
graph.add_conditional_edges("planner", should_end,
    {"execute": "executor", "replan": "replanner", "end": END})
graph.add_conditional_edges("executor", should_end,
    {"execute": "executor", "replan": "replanner", "end": END})
graph.add_conditional_edges("replanner", should_end,
    {"execute": "executor", "replan": "replanner", "end": END})

app = graph.compile(checkpointer=postgres_saver)  # Durable state
```

### LLMCompiler-Style DAG with Parallel Execution

```python
import asyncio
from dataclasses import dataclass

@dataclass
class TaskNode:
    id: str
    task: str
    tool: str
    args: dict        # May contain "$k" placeholders
    deps: list[str]   # IDs this node depends on

async def execute_dag(nodes: list[TaskNode]) -> dict:
    """
    Execute a DAG of tasks, running independent nodes in parallel.
    Resolves $k placeholders from parent results.
    """
    results = {}
    pending = {n.id: n for n in nodes}

    while pending:
        # Find ready nodes (all deps satisfied)
        ready = [n for n in pending.values()
                 if all(d in results for d in n.deps)]

        if not ready:
            raise ValueError("Circular dependency or missing node")

        # Resolve placeholders and execute in parallel
        tasks = []
        for node in ready:
            resolved_args = resolve_placeholders(node.args, results)
            tasks.append(run_tool(node.tool, resolved_args))

        outputs = await asyncio.gather(*tasks)

        for node, output in zip(ready, outputs):
            results[node.id] = output
            del pending[node.id]

    return results

def resolve_placeholders(args: dict, results: dict) -> dict:
    """Replace $k with results[k]."""
    resolved = {}
    for key, val in args.items():
        if isinstance(val, str) and val.startswith("$"):
            resolved[key] = results[val[1:]]
        elif isinstance(val, list):
            resolved[key] = [results[v[1:]] if isinstance(v, str)
                            and v.startswith("$") else v for v in val]
        else:
            resolved[key] = val
    return resolved
```

### Reflexion with Oracle-Backed Critic

```python
def reflexion_loop(problem: str, max_trials: int = 6) -> str:
    """
    Reflexion: generate -> test -> reflect -> retry.
    Key: critic uses ORACLE (tests), not self-eval.
    """
    memory = []  # Episodic hints from past failures

    for trial in range(max_trials):
        # Generate solution, conditioned on past reflections
        code = llm_generate(problem, reflections=memory)

        # VERIFY with hard oracle (unit tests), not LLM judge
        test_results = run_tests(code, problem.test_cases)

        if test_results.all_passed:
            return code  # Done -- oracle says correct

        # Reflect: explain WHY it failed using test output
        # CRITICAL: critic reads test logs, not web pages
        reflection = llm_reflect(
            problem=problem,
            code=code,
            test_output=test_results.output,  # Oracle feedback
        )
        memory.append(reflection)

        # Cap memory to prevent prompt overflow
        if len(memory) > 3:
            memory = memory[-3:]

    return code  # Return best attempt after exhausting trials
```

### Reasoning Effort Routing

```python
def route_and_execute(task: dict) -> str:
    """Route tasks to appropriate model + effort level."""
    complexity = classify_complexity(task)  # cheap classifier

    if complexity == "trivial":
        # No reasoning needed -- cheap model, no thinking
        return call_model("gpt-mini", task, reasoning_effort="none")

    elif complexity == "parallel_tools":
        # DAG planning -- use compiler pattern, cheap executors
        dag = call_model("o4-mini", task, reasoning_effort="medium")
        return execute_dag(dag)  # Executors use Haiku/Flash

    elif complexity == "hard_with_oracle":
        # Reasoning model + verifier
        result = call_model("o3", task, reasoning_effort="high")
        if verify_with_oracle(result, task.test_cases):
            return result
        return escalate_to_human(task)

    elif complexity == "irreversible":
        # Always HITL regardless of model confidence
        plan = call_model("sonnet-5", task, reasoning_effort="high")
        return await_human_approval(plan)
```

---

## 7. Common Pitfalls & Failure Modes

| Failure | Mechanism | Detection | Mitigation |
|---------|-----------|-----------|------------|
| **Plan hallucination** | Feasible-looking JSON, impossible deps, wrong tools, invented APIs | Schema + dry-run + allowlisted tools | Structured output + catalog RAG; refuse unknown tools |
| **Missing-step plans** | PS paper's 12% class | Step-count vs gold SOP; PRM | PS+ instructions; ADaPT recurse on fail |
| **Infinite replan / ReAct loop** | Same search, growing context | same_action_k, token budget | DAG + max_replans; LLMCompiler vs ReAct |
| **Verifier gaming** | Agent edits tests, sys.exit(0), patches pytest | Immutable hidden tests; coverage; tamper-evident runner | Oracle owned by platform, not the actor; prefer FN |
| **PRM / judge gaming** | Policy maximizes RM not truth | RM-oracle disagreement | Rule-based rewards where possible; ensemble judges |
| **LLM-as-judge bias** | Position/verbosity/self-preference | Swap-order A/B | Pairwise + references; never sole gate |
| **Reasoning token blowup** | Hard prompt + high effort + 100k max out | output_tokens vs visible chars | Effort routing; max_tokens; Flash/mini for easy nodes |
| **Cache stampede** | Effort change every call; replan rewrites system prompt | Cache hit ratio | Stabilize constitution; cache tools not past_steps |
| **Hidden CoT opacity** | Cannot see o1/o3 thoughts | -- | External plan CFI; tool allowlists |
| **Straggler join** | Slowest parallel tool ~2x mean | Per-tool timers | Cancel + replan that node only |
| **Reflection poisoning** | Poisoned webpage -> critic memory -> next trial jailbreak | Origin tags | Store reflections as untrusted data |
| **HTN method miss** | LLM decomposition flaky | Retry count | Cache learned methods; fail-closed |
| **Debate collapse** | Collusion / persuasive wrong answer | Diversity of debaters | Hard oracles; limited rounds |
| **ToT/LATS cost cliff** | Branching factor explosion | Node-call counter | Reserve for irreversible decisions |
| **Durable replay dual-spend** | Non-idempotent Activity retry | Idempotency keys | Temporal + tool dedup |
| **Silent token burn** | Reasoning incomplete, output truncated | status="incomplete" | Reserve 25,000 tokens; monitor usage |
| **Schema-valid but wrong** | Strict JSON passes, values wrong | Business-rule check | Verifier layer beyond parser |
| **Over-decomposition** | Too many subqueries/subtasks | Latency + cost increase | Adaptive decomposition (ADaPT) |
| **State drift** | Dropped reasoning items between tool calls | Hard API failure | Preserve all thinking/reasoning blocks |

---

## 8. Interview Questions & Answers

**Q1: What is the difference between ReAct, plan-and-execute, and DAG planning?**

ReAct interleaves reasoning and action every turn -- think, act, observe, think again. It is flexible but serial: every tool call pays for another planner invocation, and the model can get stuck in repetitive loops. Plan-and-execute separates planning from execution: the planner emits a multi-step plan upfront, the executor runs steps sequentially, and a replanner adjusts after new evidence. This amortizes the expensive planning call across multiple steps. DAG planning (LLMCompiler) goes further by expressing the plan as a dependency graph with placeholders -- independent steps run in parallel, cutting latency. LLMCompiler showed 3.7x latency improvement and 6.7x cost reduction vs ReAct on their benchmarks. The trade-off: DAGs require more orchestration complexity and the planner+joiner are still serial bottlenecks.

**Q2: When should you use a reasoning model (o3, R1) vs standard planning?**

Use reasoning models when there is a single hard question with a verifiable answer -- math proofs, code generation with test suites, complex logical problems. The internal chain-of-thought explores strategies and backtracks automatically. But do not use them for DAG-shaped tool parallelism (you are paying output-rate pricing for thinking tokens that could be replaced by a cheap executor). The routing table: (1) many independent tools -> DAG planner + cheap executors, (2) single hard problem + checker -> reasoning model + oracle, (3) open-ended -> one critic pass, (4) irreversible action -> HITL regardless. Putting o3-high on every API call is the classic bill shock pattern.

**Q3: Explain how Reflexion works and its limitations.**

Reflexion is verbal reinforcement learning without weight updates. The actor generates a solution, the environment/evaluator provides feedback (pass/fail, test results), a self-reflection LLM writes a verbal hint about what went wrong, and that hint is added to episodic memory for the next trial. On HumanEval Python, it achieved 91.0% pass@1 vs GPT-4's 80.1%. But there is a critical caveat from the ablation studies: on the hardest 50 HumanEval-Rust problems, without tests, reflection actually hurt performance (52% vs 60% baseline). The critic needs an oracle. Also, on WebShop after 4 trials, reflections stopped being useful -- Reflexion does not explore diverse catalogs, it just refines the same approach. Production rule: Reflexion works when you have a hard oracle (unit tests, compiler, math checker). Without one, do not add a critic.

**Q4: What is the difference between process reward models and outcome reward models?**

An outcome reward model (ORM) only supervises the final answer -- right or wrong. A process reward model (PRM) supervises each step -- was this reasoning step correct? Lightman et al. showed on 500 MATH problems: PRM scored 78.2% vs ORM 72.4% at best-of-1860, and the gap widens with more candidates. PRMs are better because they give credit assignment -- they can identify exactly where reasoning went wrong. But DeepSeek abandoned PRMs for their large-scale R1 training for three reasons: step granularity is undefined in general reasoning, intermediate correctness is hard to judge, and reward hacking is a real problem. Their solution: rule-based rewards (exact match, format checks) without any neural verifier. For production inference, PRMs are still excellent for reranking candidate solutions.

**Q5: How do you prevent infinite loops in planning agents?**

Multiple circuit breakers working together. First, `max_replans` (e.g., 3) -- after exhausting replans, return best-so-far with a PLAN_EXHAUSTED status. Second, `same_action_k` -- if the agent repeats the same action with the same observation k times, force a replan or escalate to human. Third, `max_reflect_tokens` -- if the critic generates more than N tokens, drop to outcome-only verification. Fourth, `reasoning_token_cap` with a hard `max_output_tokens` to prevent reasoning models from generating 100k tokens of hidden thought. Fifth, use Temporal Nexus-style circuit breakers: 5 consecutive errors opens the circuit for 60 seconds. The fundamental insight is that LangGraph and most frameworks will not impose these caps for you -- they are product decisions you must implement.

**Q6: How does LLMCompiler achieve 3-7x cost reduction over ReAct?**

LLMCompiler uses a compiler analogy. Instead of the ReAct pattern where every tool call triggers another full planner invocation (reason-act-observe-reason), it has the planner emit a complete DAG upfront with dependency edges and placeholder variables. A Task Fetching Unit dispatches ready nodes in parallel. The executor runs tools concurrently. An optional Joiner decides if replanning is needed. The savings come from two sources: fewer planner calls (one upfront vs one per tool) and parallel execution (independent tool calls overlap instead of running serially). On Movie Recommendation: 3.74x faster, 6.73x cheaper. On WebShop vs LATS: 101.7x faster at similar score. The residual bottleneck is that the planner and joiner are still serial -- on Movie Rec, planner took 1.88s and joiner 1.62s, which is more than half of end-to-end when tools are fast.

**Q7: How do you secure a planning agent against prompt injection?**

The key principle is plan-then-execute control flow integrity (CFI): freeze the plan from the user prompt so tool outputs cannot add new actions. Three concrete approaches. CaMeL (DeepMind): privileged LLM generates a Python-like plan; a custom interpreter runs it with capabilities on values, so untrusted data cannot change control flow. AgentDojo: 77% task completion with provable security. PlanGuard: isolated planner reads only user instructions; hierarchical check with hard tool allowlist then intent verifier for parameters. InjecAgent ASR: 72.8% -> 0%, FPR 1.49%. LangGraph secure variant: planner names the single tool per step; executor spins a temporary agent with only that tool (least privilege). The vulnerability: dynamic replanning re-opens the injection surface. If you replan, run the planner on schema-only observations (not raw tool output) or require human-in-the-loop.

**Q8: Explain the cost structure of reasoning models. Why does bill shock happen?**

Thinking/reasoning tokens are billed as output tokens, which are typically 4-5x more expensive than input tokens. o3: $2/M input but $8/M output. When the model thinks hard, it can generate thousands of hidden reasoning tokens. A simple prompt might generate 2,500 reasoning tokens per call at $8/M = $0.02/call. If your agent has 6 LLM calls per task and reasoning on all of them, that is ~$0.12/task or ~$120/1k tasks with o3. The fix is effort routing: use reasoning only on the planner and critic (2 calls), use cheap models (Haiku, GPT-mini) for executors (4 calls). This brings it down to ~$45-80/1k tasks. The worst case: LATS/MCTS with full branching, which can be tens to hundreds of times more expensive. Also: changing effort/budget between calls invalidates prompt cache breakpoints, multiplying input cost.

**Q9: How do you design a production coding agent with proper verification?**

The key insight from AlphaCodium: prefer false negatives over false positives in test gates. A false positive (tests pass on wrong code) stops the agent -- worse than a false negative (tests fail on correct code) which just triggers another iteration. Design: planner writes a file-level DAG; executor runs in a sandbox; platform-owned unit tests (hidden from the agent) are the oracle; Reflexion on compiler/test logs (not web pages); HITL interrupt before `apply` to main. Agent-generated tests are advisory only, never the sole gate. Temporal Activities for LLM calls so retries do not double-commit. Effort escalation: high reasoning only on the failing node, not every node. The gaming attack: agent rewrites tests, patches pytest, or calls `sys.exit(0)`. Mitigation: tests live outside the workspace ACL, in a tamper-evident runner.

**Q10: What is Tree of Thoughts and when is it worth the cost?**

Tree of Thoughts (Yao et al., NeurIPS 2023) treats intermediate reasoning steps as candidates in a search tree. At each step, the model generates multiple candidates (branching), self-evaluates them, and uses BFS or DFS with backtracking. On Game of 24: GPT-4 CoT gets 4%, self-consistency gets 9%, ToT with branching factor 1 gets 45%, and ToT b=5 gets 74%. The quality improvement is dramatic -- 60% of CoT samples already fail at step 1, and left-to-right generation cannot recover. But the cost is branching x depth LM calls. When is it worth it? When you have: (1) a cheap exact evaluator (not LLM self-eval), (2) high value per task, (3) branching factor < ~5, (4) depth < ~10. Do not use it for open-catalog search (WebShop -- LLMCompiler is 101.7x faster) or when reasoning models can internalize the search (o3 on math).

**Q11: How does Temporal improve agent reliability over LangGraph alone?**

LangGraph gives you graph-structured control flow with checkpoints. Temporal adds durable execution: workflows survive process crashes, Activities (model calls, tool executions) are replayed from event history without re-billing already-completed steps. Temporal also gives you: (1) fork -- snapshot workspace + conversation for branching explorations. (2) Nexus circuit breaker -- 5 consecutive retryable errors opens the circuit for 60 seconds, preventing cascading failures. (3) Durable timers and signals for HITL approvals that can take hours while token p99 stays bounded. (4) Activity-level timeouts so a hung code interpreter does not block the entire workflow. The main caveat: at-least-once Activities with non-idempotent tools (send email, charge card) cause duplicate side effects on replay -- you need dedup keys.

**Q12: Compare the reasoning capabilities and costs of o3, o4-mini, DeepSeek V4, and Claude Sonnet 5.**

o3 ($2/$8 in/out) is the premium reasoning model -- AIME 79.2% with o1, succeeded by GPT-5 as the general line. Best for the hardest problems. o4-mini ($1.10/$4.40) is the cost-efficient reasoning model -- same effort controls, good enough for most planning tasks. DeepSeek V4-Flash ($0.22-0.44/$0.66-1.32 in/out, off-peak) is dramatically cheaper -- thinking is default, 2500 concurrency, 1M context. The off-peak pricing makes it 5-20x cheaper than o3 on output-heavy traces. But: peak hours are UTC 01:00-04:00 and 06:00-10:00, and you need to validate quality on your specific tasks. Claude Sonnet 5 ($2/$10) with adaptive thinking is competitive on quality, and Anthropic's cache hit at 10% of base input makes multi-step agents cheaper if you stabilize the prefix. Haiku 4.5 ($1/$5) is the cheap critic/verifier. The pattern: frontier reasoning model for the planner, cheap model for executors, cheapest for critics with tool access.

---

## 9. Key Numbers to Memorize

| Metric | Value | Source |
|--------|-------|--------|
| LLMCompiler vs ReAct cost | Up to 6.7x cheaper | Kim et al., ICML 2024 |
| LLMCompiler vs ReAct latency | Up to 3.7x faster | Kim et al., ICML 2024 |
| LLMCompiler vs LATS (WebShop) | 101.7x faster | Kim et al., ICML 2024 |
| ToT Game of 24 (CoT vs ToT b=5) | 4% vs 74% | Yao et al., NeurIPS 2023 |
| Reflexion HumanEval pass@1 | 91.0% vs GPT-4 80.1% | Shinn et al., NeurIPS 2023 |
| PRM vs ORM (MATH best-of-1860) | 78.2% vs 72.4% | Lightman et al. |
| R1 AIME pass@1 | 79.8% (vs o1-1217: 79.2%) | DeepSeek Nature 2025 |
| R1-Zero AIME improvement | 15.6% -> 77.9% | DeepSeek |
| ADaPT improvement | Up to +28.3% ALFWorld | NAACL 2024 |
| AlphaCodium CodeContests | pass@5: 19% -> 44% | Ridnik et al. |
| PS+ vs Zero-shot-CoT (CSQA) | 71.9% vs 65.2% | Wang et al., ACL 2023 |
| LtM on SCAN | 99.7% with 14 examples | Zhou et al., ICLR 2023 |
| Self-consistency GSM8K gain | +10-18 points at K~20 | Wang et al., ICLR 2023 |
| o3 pricing | $2 / $8 (in/out per 1M) | OpenAI |
| o4-mini pricing | $1.10 / $4.40 | OpenAI |
| DeepSeek V4-Flash off-peak | $0.22 / $0.66 | DeepSeek |
| Claude Sonnet 5 pricing | $2 / $10 | Anthropic |
| Claude cache hit | 10% of base input | Anthropic |
| Temporal Nexus CB default | 5 errors / 60s half-open | Temporal docs |
| LLM-as-judge agreement | >80% with humans | Zheng et al. |
| PlanGuard InjecAgent ASR | 72.8% -> 0%, FPR 1.49% | PlanGuard paper |
| CaMeL AgentDojo | 77% tasks with provable security | DeepMind |

---

## 10. Quick Reference

### Planning & Reasoning Cheat Sheet

**Topology selection**:
- Simple task, few tools: ReAct (serial loop)
- Multi-step with dependencies: Plan-and-Execute (LangGraph)
- Many independent tools: DAG planning (LLMCompiler -- 3-7x cheaper than ReAct)
- Hard search with cheap evaluator: ToT/MCTS (reserve for high-value tasks)
- Irreversible action: HITL regardless of topology

**Model assignment** (do not use one model for everything):
- Planner: o4-mini medium / Sonnet 5 / V4-Flash thinking
- Executor: Haiku 4.5 / GPT-mini / V4-Flash non-think
- Critic: Haiku + tools (CRITIC pattern)
- Verifier: pytest / compiler / sympy ($0)

**Circuit breaker stack**:
```
max_replans = 3
same_action_k = 2
max_reflect_tokens = N
reasoning_token_cap = max_output_tokens
critic_circuit_breaker = 5 errors / 60s
```

**Verification hierarchy** (trust in order):
1. Deterministic environment flag (tests, compiler, exact match)
2. Held-out / hidden test cases
3. Replayable computation (interpreter, calculator)
4. Process reward model (rerank, not RL)
5. LLM-as-judge (subjective only; swap order, reference answers)

**Cost formula**:
```
cost = SUM over calls:
  (in_uncached * P_in + in_cached * P_cache
   + (visible_out + reasoning_out) * P_out)
  + tool_egress
```

**Security checklist**:
- Plan is frozen from user prompt; tool outputs cannot add actions
- Replan runs on schema-only observations or requires HITL
- One tool per step (least privilege executor)
- Reflections stored as untrusted data with origin tags
- MCP: OAuth 2.1, per-tool RBAC, no standing tokens in planner context
- Hidden CoT is not auditable -- require visible plan + tool log for regulated actions

**Durable state minimum**:
- plan_id, graph (nodes, edges, placeholders)
- past_steps (action, observation hash, verdict, critic ID)
- replan_count, effort, tenant, actor
- Observations in object storage; hashes in checkpoint
- Idempotency keys for non-idempotent tools
