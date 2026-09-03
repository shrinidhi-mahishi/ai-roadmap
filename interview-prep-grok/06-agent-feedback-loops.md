# Module 06: Agent Feedback Loops

**Study + interview prep.** Grounded in research dated 2026-09-02 (95 sources). Prices, hop caps, and bench numbers are vendor docs / papers as of that date. `$ per 1k tasks` figures that multiply published rates by a stated reference loop are **[inferred]**, not a vendor SKU. Public pages do **not** publish production p50/p95/p99 of extra critique hops on *your* SLO path; missing percentiles are marked and policy targets are architecture-derived **[inferred]**. Dual-oracle measurement lives in evals; trajectory sampling lives in observability. This module uses oracles as **loop stoppers** and traces only where a hop cap or checkpoint is a control-plane primitive.

---

## What Is This?

A **feedback loop** is a control-plane state machine: actor → environment → critic/verifier → replanner. It is not a smarter prompt. The model proposes the next thought, tool, or plan node. A **deterministic harness** owns hop caps, role routing, memory writes, and whether a critic may fire.

Four roles must stay separate — fusing them into one ReAct generation is the dominant cost, correctness, and injection failure:

| Role | Owns | Typical implementation | Failure if fused |
| --- | --- | --- | --- |
| **Planner** | Decompose objective → list/DAG of steps, deps, tool names, success criteria | Structured-output LLM, LLMCompiler Function Calling Planner, HuggingGPT `{task,id,dep,args}`, ADaPT recursive splitter | Tool observations inject new goals (IPI); plan mutates every turn |
| **Executor** | Run one ready node; bind placeholders (`$k`) | Tool runtime, sandboxed code, Temporal Activities, LangGraph `ToolNode` | Planner tokens billed on every search; serial ReAct latency |
| **Critic / reflector** | Verbalize *why* a trial failed; write episodic hint | Reflexion memory buffer, Self-Refine FEEDBACK, CRITIC tool-interactive critique, ADK `CriticAgent` | Infinite critique; reflection becomes prompt-injection surface |
| **Verifier** | Accept/reject a step or final answer | Unit tests, compiler, math checker, PRM, LLM-as-judge, human interrupt | Gaming (fake-green tests); judge bias; unverifiable open-ended work |

Think of a house: the planner is the architect, the executor the contractor, the critic the inspector who explains what is wrong, the verifier the code office that signs off or rejects. A contractor who redesigns the house every time they pick up a hammer is classic ReAct.

**Loops help only with new evidence.** Attach a critic when a test log, interpreter, citation matcher, toxicity API, or DB predicate exists. Huang et al. (ICLR 2024): intrinsic self-correction (same model, no oracle) **drops** GSM8K. CRITIC without tools can **degrade** math and toxicity vs baseline. Reflexion without tests on the hardest 50 HumanEval-Rust: **52% vs 60%** baseline — harmful edits, no early return.

**Internalized o1/R1 does not replace hop caps.** RL teaches the model to break steps, detect mistakes, and switch strategy *inside hidden tokens*. That is cheaper to operate and harder to audit. It is still one model call. External replan is required when tools fail, policy forbids the next call, you need a durable DAG across crashes, or you must show a visible plan. A “wait” spike in R1 is search inside the forward pass, not a Temporal workflow.

Invariant: the LLM is **not** the planner. The planner is a function that *emits* a plan data structure. The executor *interprets* it. The critic *annotates* it. The verifier *gates* it.

## Why It Matters

Every production agent is a loop whether you named the roles. Interviews test whether you split **control plane vs data plane**, attach a critic **only with an oracle**, cap **turns / replans / same_action_k / $**, and treat reflections as **untrusted memory**. A Principal answer names `max_turns=10`, `max_replans=2`, PlanGuard/CaMeL (untrusted data must not change the tool set), and “o1 still needs pytest.”

---

### 1. System Topology & Data Flow

A production loop is **four independently scaled roles** sharing a typed plan / trial object. Anthropic’s 2024 split still holds: **workflows** are LLMs and tools on predefined code paths; **agents** are systems where the LLM dynamically directs process and tool use. Production stacks mix both: a deterministic outer graph wrapping an inner ReAct or plan-execute cycle.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  hop / turn / replan counters    same_action hashes    $ / trial │
         │  oracle verdicts (WORM)   plan JSON + tool names + arg hashes    │
         │  NOT hidden CoT summaries as the audit (o-series never sent you) │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ meters            │ audit events
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (whether to replan, which node is ready, whether critic    │
│                 fires, who may stop — not token math)                     │
│                                                                           │
│  ┌──────────┐ ┌────────────┐ ┌──────────────┐ ┌────────────┐ ┌─────────┐ │
│  │ PEP /    │ │ Hop caps   │ │ Role router  │ │ Circuit    │ │ HITL    │ │
│  │ IdP JWT  │ │ max_turns  │ │ planner │    │ │ breaker    │ │ inter-  │ │
│  │ → tool   │ │ max_replans│ │ executor│    │ │ same_action│ │ rupt()  │ │
│  │ allowlist│ │ maxBudget$ │ │ critic  │    │ │ k / critic │ │ before  │ │
│  │ PlanGuard│ │ recursion  │ │ verifier│    │ │ open→half  │ │ refund  │ │
│  │ S_ref    │ │ Remaining- │ │ escalate     │ │            │ │         │ │
│  │          │ │ Steps      │ │              │ │            │ │         │ │
│  └────┬─────┘ └─────┬──────┘ └──────┬───────┘ └─────┬──────┘ └────┬────┘ │
└───────┼─────────────┼───────────────┼───────────────┼─────────────┼──────┘
        │             │               │               │             │
        ▼             ▼               ▼               ▼             ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (plan graph, past_steps, tool I/O blobs, episodic hints)      │
│                                                                           │
│  plan → act → observe → (oracle | critic) → fail? → reflect/replan        │
│                                      pass? → END                          │
│                                                                           │
│  ┌────────────── TOOL PROXIES (MCP tools/call — least privilege) ───────┐ │
│  │ execute_node {one tool} │ sandboxed_code │ pytest/sympy │ search     │ │
│  │ Identity from verified token / RunContext — NEVER from critic text   │ │
│  │ Frozen allowlist ∩ S_ref ∩ capability tags (PlanGuard + CaMeL)       │ │
│  │ Denied tool → rejection AS the tool result (that retry CONSUMES a    │ │
│  │   Claude tool-use turn)                                              │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER                                                         │
│                                                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐  │
│  │ LangGraph    │ │ Temporal     │ │ Store        │ │ Observation      │  │
│  │ PostgresSaver│ │ Workflow     │ │ (skills,     │ │ blobs (S3)       │  │
│  │ thread_id    │ │ state: trial │ │ trusted AFTER│ │ hash + summary   │  │
│  │ checkpoint / │ │ oracle,      │ │ verifier)    │ │ in history; HTML │  │
│  │ super-step   │ │ replan_count │ │ append-only  │ │ must NOT sit in  │  │
│  │              │ │ last 3 hint  │ │ Voyager JS   │ │ Workflow events  │  │
│  │ InMemory=test│ │ IDs          │ │              │ │ (50 MB cap)      │  │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────────┘  │
│  Untrusted reflections: origin=critic, oracle_hash, untrusted=true, TTL   │
│  Checkpointer = this thread's plan. Store = cross-thread lessons.         │
│  Copying critic output Store←checkpoint is the poisoning path.            │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not swap writes):**

| Write | Plane | Durable store | Retry rule |
| --- | --- | --- | --- |
| `plan`, `replan_count`, `escalate` | Control | LangGraph checkpoint / Temporal Workflow state | Deterministic replay OK |
| Tool HTTP / email / refund | Data | Idempotency key + provider | At-least-once; never from replay of a non-Activity |
| Episodic hint | Data, **untrusted** | Store namespace with TTL | Do not replay into system prompt as instructions |
| Skill (Voyager JS, typed tool) | Data, **trusted after verifier** | Append-only Store | Promote only on oracle pass |
| Hidden reasoning item `rs_…` | Control (vendor) | Responses API / encrypted blob | Must pair with following message or HTTP **400** |

LangGraph `StateGraph` is the control-plane loop; `PostgresSaver` is the data-plane snapshot. Temporal Workflows are the control plane; Activities (model + I/O tools) are the data plane. Google ADK `LoopAgent` is a **deterministic** template: the outer loop is not an LLM; sub-agents may be. MCP servers sit on the **tool boundary**: data-plane with control-plane auth. Do not let the critic write the plan in the same forward pass that executes tools.

**Framework mapping (same topology, different units — do not mix in an SLO):**

| Harness | Loop unit | Default cap | What one unit includes | Critic primitive |
| --- | --- | --- | --- | --- |
| LangGraph / `create_agent` | Super-step | `recursion_limit` **1000** (≥1.0.6); SDK schema still documents **25** | One node execution; ReAct tool cycle ≈ **2** super-steps | Custom nodes or LoopAgent-equivalent cycle |
| OpenAI Agents SDK | Turn | **10** | One model invocation **including** its tool calls | Separate agent + handoff, or output guardrail |
| Claude Agent SDK | Tool-use turn | **None** | Model output that includes tool calls; text-only final is a non-counted wrap-up | Hooks + `permissionMode` (PEP), not a critic role |
| Google ADK `LoopAgent` | Iteration | You must set; examples **5** / **10** | One pass over `sub_agents` in order | First-class `CriticAgent` + `escalate=True` |
| Temporal + Agents SDK | Workflow event | **51,200** events / **50 MB** | Each Activity (model or tool) appends history | Workflow `if` on Activity result |

`max_turns=None` disables the Agents SDK cap. Claude `maxTurns` / `maxBudgetUsd` default **no limit**. Anthropic cookbook `loop()` `while True`s until `evaluation == "PASS"` — production must add `max_iterations`. LangGraph v1 deprecates `create_react_agent` in favor of `langchain.agents.create_agent` (still LangGraph; adds `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware`). `max_replans` is **not** a LangGraph built-in — put `replan_count` on state.

**Request-flow narrative (plan → act → observe → critique/verify → replan, with budget caps):**

1. **PEP / control.** TLS terminates. Verified JWT (not a tool argument) expands groups. PEP emits the tool allowlist. PlanGuard isolated planner \(\mathcal{P}(I,\mathcal{T})=S_{ref}\) sees **only** the user instruction and tool definitions — never retrieved content. Freeze \(S_{ref}\) for the trial.
2. **Planner (control → data).** Structured-output LLM emits a typed plan (list or DAG with `$k` placeholders). This is one LLM call. HuggingGPT schema: `[{task,id,dep,args}]`. LLMCompiler: Function Calling Planner streams the DAG so the first ready node can run before the planner finishes.
3. **Executor (data, tool proxy).** Topological fetch: run ready nodes. Temporal: model and I/O are **Activities**. LangGraph: `ToolNode`. Bind `$k` from parent results. Idempotency key = planner-stable node id + `trial_id`. POST without a key is refused. Parallel read-only tools may run concurrently (Claude: `Read`/`Glob`/`Grep` and MCP `readOnlyHint`); writes (`Edit`/`Write`/`Bash`) sequential.
4. **Observe.** Tool I/O blobs go to object storage; the plan object keeps hashes + summaries. Pagination-by-LLM (`page=1` forever) is a data-plane loop: cap `limit`, return a terminal observation.
5. **Verifier (hard gate first).** Rank stoppers: (1) deterministic env flag (AlfWorld done, HTTP 2xx on idempotent GET, DB predicate); (2) held-out tests; (3) replayable computation (interpreter, compiler, calculator); (4) PRM — rerank, not stop, when 1–3 exist; (5) LLM-as-judge / self-eval — subjective quality only. If 1–3 exist, **4–5 must not override**. Pass → END. Checkpoint is a completed run.
6. **Budget fuse (control, every hop).** Check `max_turns` / `max_iterations` / `max_replans` / `maxBudgetUsd` / `RemainingSteps` / Temporal event count / `same_action_k`. Hit → do **not** ask the model. Route to END / HITL / refuse. Agents SDK `MaxTurnsExceeded` unless `error_handlers={"max_turns": ...}`; set `include_in_history=False` on the fallback or it becomes next-session memory.
7. **Critic (only on oracle fail).** Critic reads **oracle logs** (SQL diff, pytest, interpreter), not the webpage that failed. Verbalize why. Write episodic hint with `origin=critic`, `oracle_hash`, `untrusted=true`. Cap last **3** (Reflexion). Never let reflection emit tool calls. If no oracle: **skip critic** (Huang / CRITIC w/o Tool).
8. **Replan (control).** Replanner sees schema-only observations or HITL for new tools. Post-reflection actions ⊆ original allowlist ∪ approved delta. `replan_count += 1`; at 2–3 → HITL. Replan that feeds raw observations back into PlanGuard \(\mathcal{P}\) **destroys** isolation.
9. **Telemetry.** Visible plan JSON + tool names + arg hashes + verifier verdict + critic id. Hidden CoT summaries are **not** the SOX tape. Judge/critic models are subprocessors — reflections with PII re-export that PII on the next trial.

ADK canonical critic loop: `SequentialAgent([story_generator, LoopAgent(sub_agents=[critic_agent, refiner_agent], max_iterations=5)])`. Stop when `max_iterations` reached **or** any sub-agent returns `escalate=True`. `max_iterations` does **not** propagate into sub-agents. Put critic **before** refiner so the critic sees the latest draft. Nested loops: one `escalate=True` may exit **all ancestor** LoopAgents.

OpenAI Agents SDK Runner: (1) invoke current agent; (2) if output matches `output_type`, stop; (3) if handoff, switch agent; (4) else run tools and loop. Only the **first** agent’s input guardrails run — handoff is a hole. `run_in_parallel=False` blocks the model until the check completes (injection + cost).

Claude Agent SDK: receive prompt → evaluate → execute tools → repeat until a response with **no tool calls**. `max_turns=2` on the auth.ts example stops **before the Edit** (turn 3). Streaming input: a queued user message that arrives when max-turns fires is **not** folded into the last model call; it starts a **new** turn and the count **resets**. `effort` ∈ {low, medium, high, xhigh} is per-turn, orthogonal to `maxTurns`.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants

**I1.** A feedback loop is a **control-plane state machine**, not a prompt. The harness owns hop caps and whether the critic fires.

**I2.** Planner, executor, critic, verifier are **separate roles**. o1/R1 collapse planner+critic+search into hidden tokens inside one call — still need an external verifier for consequential actions.

**I3.** **No oracle, no critic.** Huang: prior “self-correction wins” were oracle labels (RCI/Reflexion), unfair vs self-consistency (debate), or weak one-shot baselines (Self-Refine). Anthropic: evaluator-optimizer only when (1) there is a clear evaluation criterion and (2) LLM feedback measurably improves the output.

**I4.** Memory is **data**, not instructions. Store reflections as `origin=critic`, `untrusted=true`. Cap last 1–3. Never auto-promote web observations to semantic memory.

**I5.** Stop ranking is total-order: env flag > hidden tests > interpreter > PRM (rerank) > LLM-as-judge. Never let 4–5 override 1–3.

#### 2.2 ReAct vs plan-and-execute vs DAG (LLMCompiler) vs ToT / LATS

**ReAct** (Yao et al., ICLR 2023). Interleaves Thought / Action / Observation. HotpotQA PaLM-540B: ReAct **27.4 EM**, Act 25.7, CoT 29.4, CoT-SC 33.4; best combo ReAct→CoT-SC **35.1**. Fever: ReAct **60.9** vs CoT 56.3. ALFWorld / WebShop: 1–2-shot ReAct beats IL/RL trained on \(10^3\)–\(10^5\) instances by **+34** and **+10** pp. Human labels on 200 HotpotQA failures:

| Failure mode | ReAct | CoT |
| --- | --- | --- |
| Hallucinated reasoning/facts | 6% | **56%** |
| Reasoning error (incl. failing to recover from repetitive steps) | **47%** | 16% |
| Search result error | 23% | — |
| Label ambiguity | 29% | 28% |

Grounding kills hallucination (success-mode false-positive hallucination **6% vs CoT 14%**) but creates the signature production failure: **repetitive thought-action loops**. The paper suspects greedy decoding; production must assume the model will not jump out. Stop conditions that actually work: (1) model emits Finish/answer with **no** tool call; (2) hop cap; (3) same thought+action hash `k` times; (4) empty/useless search budget (23% of ReAct Hotpot failures are a **data-plane miss**, not a critic miss). ReAct slightly **lags** CoT on HotpotQA EM (27.4 vs 29.4) and **beats** CoT on Fever: acting is worth more when labels differ by a token of fact.

**Focused ReAct:** re-inject the original query each step + early-stop on repeated thought/action. Control-plane patch, not a new topology.

**Plan-and-execute (LangGraph canonical):** `planner` → `agent` (execute `plan[0]`) → `replan` → END or back to `agent`. State: `input`, `plan`, `past_steps` (`Annotated[..., operator.add]`), `response`. Inspired by Plan-and-Solve + BabyAGI. **Serial** steps; embarrassingly parallel work should be a DAG. The graph will **not** cap `max_replans` for you.

**Plan-and-Solve / PS+** (Wang et al., ACL 2023) and **Least-to-Most** (Zhou et al., ICLR 2023): PS+ is zero-shot “devise a plan, then carry it out” — still **one** generation unless you split plan vs execute (LangGraph does). Error autopsy on 100 GSM8K-style items: calculation **7%**, missing-step **12%**, semantic misunderstanding **27%** of the sampled incorrect set — PS targets missing steps. Least-to-Most reduces then solves sequentially using prior answers. SCAN with code-davinci-002: **99.7%** with **14** exemplars vs CoT **16.2%** (neural-symbolic SCAN trained on **>15k**). Linear in subproblem count; no native parallelism.

**LLMCompiler DAG** (Kim et al., ICML 2024). Compiler analogy: (i) Function Calling Planner emits a DAG with `$k` placeholders; (ii) Task Fetching Unit dispatches ready nodes; (iii) Executor runs tools in parallel; optional **Joiner** replans or answers. Streaming the DAG hides planner latency (up to **1.3×** extra on ParallelQA). Vs ReAct: up to **3.7×** latency, **6.7×** cost, **~9%** accuracy (ParallelQA). HotpotQA: **1.80×** speedup / **3.37×** cheaper; Movie Recommendation **3.74× / 6.73×**. Game of 24 vs ToT: **2×** speedup. WebShop vs LATS: **101.7×** speedup at similar score (**72.8 ± 4.01** gpt-3.5-turbo). Residual: planner+joiner are serial; Movie Rec planner **1.88 s** + answer **1.62 s** average — more than half of end-to-end when tools are fast. LLMCompiler documents ReAct’s two modes a DAG removes: premature stop (~**85%** of Movie Rec examples exit before 8 searches) and infinite same-tool loops.

Analytical latency (embarrassingly parallel, N tasks):

- ReAct: \(T_R = \sum_i (T^R_P(P_i) + T_E(E_i))\) — plan and execute **serial** per task.
- Compiler: \(T_C = \sum_i T^C_P(P_i) + \max_k T_E(E_k)\) — plans still serial; executes join on the **slowest** tool.
- Streaming compiler: \(T_{SC} = \sum_i T^C_P(P_i) + T_E(E_N) \le T_C\) — first ready node runs before the planner finishes.
- \(\gamma = T_R / T_C\); \(\gamma_{\max} \approx N\) when tools dominate and are equal; \(\gamma_{\min} \approx 1\) when planning dominates.

**Complexity of extra hops.** One extra critique/replan hop is **O(1)** additional LLM calls plus **O(context)** growth (observations accumulate). Self-Refine worst case: INIT + 4×(FEEDBACK+REFINE) = **9** generations vs 1. CRITIC is **linear in n**. Huang intrinsic: **3** calls after round 1 and **5** after round 2 vs **1** for standard prompting, with **negative** quality. ToT/LATS: branching × depth; LATS WebShop is **101.7×** slower than a parallel DAG at similar score. Profile **planner share**: if planner+joiner > 50% of wall time (Movie Rec), a bigger model on the planner **increases** p50 with no parallel gain.

**HuggingGPT / JARVIS** (Shen et al., NeurIPS 2023): plan → model selection → execute → summarize. Independent tasks parallel. Authors’ limits: plans not guaranteed feasible; **multiple sequential LLM round-trips** dominate latency. Global plan in **one** planner query vs BabyAGI/AutoGPT iterative next-task (can loop forever on a bad step).

**ADaPT** (Prasad et al., NAACL Findings 2024): try executor; on failure, planner splits with AND/OR; recurse to `d_max`. Controller is a deterministic program; success of children ⇒ parent. GPT-3.5: up to **+28.3 pp** ALFWorld, **+27 pp** WebShop, **+33 pp** TextCraft vs ReAct / Plan-and-Solve. Vs Reflexion: **+14.1 / +9 / +20 pp**. Point is *as-needed* depth, not always-max decomposition.

**Tree of Thoughts** (Yao et al., NeurIPS 2023). Thoughts = intermediate candidates; BFS/DFS with LM self-eval. Game of 24 (GPT-4): IO 7.3%, CoT **4.0%**, CoT-SC (k=100) **9.0%**, ToT b=1 **45%**, ToT b=5 **74%**. ~**60%** of CoT samples already fail at step 1. Cost (GPT-4 2023 list in the paper): **$0.74**/case vs CoT best-of-100 **$0.47** at 49%. GPT-3.5+ToT **19%** vs GPT-4+ToT 74%; generation quality dominates evaluation (GPT-4 gen + GPT-3.5 eval 64%; reverse 31%). Not a production default except puzzle-like search with a cheap eval.

**LATS** (Zhou et al., ICML 2024). MCTS over ReAct-style actions; LM value + SC hybrid; Reflexion on failed paths. HumanEval GPT-4 pass@1 **92.7%**; WebShop GPT-3.5 **75.9** (+22.1 vs ReAct); HotPotQA ~**2×** ReAct. LLMCompiler’s **101.7×** WebShop note is the production warning. DeepSeek-R1 MCTS at RL scale **failed** (token branching ≫ chess; weak value model). MCTS can help *inference* with a pretrained value head; not a default RL loop.

#### 2.3 Reflexion, Self-Refine, CRITIC

**Reflexion** (Shinn et al., NeurIPS 2023):

```
for trial in 1..T:
  y = Actor(task, memory)                 # usually ReAct
  r = Env/Evaluator(y)                    # scalar or tests
  if oracle_pass(r): return y
  if same_action_same_obs_k: r = fail     # AlfWorld heuristic
  z = Reflector(task, y, r)               # verbal RL
  memory.append(z)                        # keep last 3
```

Programming variant: generate ≤**6** unit tests with CoT, AST-filter, execute, then reflect on interpreter logs. Memory is **episodic NL**, not a skill. AlfWorld: ReAct+Reflexion completes **130/134** tasks using a **hand-written heuristic** (same action + same observation for several steps = hallucinated possession / stuck) plus optional GPT classifier; +**22 pp** over 12 trials. HotPotQA **+20 pp**; memory size **3**. HumanEval Python pass@1 **91.0** vs GPT-4 **80.1**; HumanEval Rust 68.0 vs 60.0; Leetcode Hard PY 15.0 vs 7.5. WebShop: after **4** trials, terminate — reflections not useful; Reflexion does not explore diverse catalogs. **Without tests** on hardest 50 HumanEval-Rust: **52% vs 60%**.

**Self-Refine** (Madaan et al., NeurIPS 2023): same LLM as INIT / FEEDBACK / REFINE. Loop until a task-specific stop (e.g. “looks good”) or **k=4**. Seven tasks: review rewrite, acronyms, stories, code rewrite, dialogue, constrained generation, toxicity. ~**20%** absolute average over one-shot; per-task **5–40%**. Control: vs ChatGPT **k=4 samples without feedback**, humans still prefer Self-Refine (iteration ≠ sampling). **No tools.** Production: Self-Refine is a **style/fluency** loop, not a fact loop. Worst case **9** generations.

**CRITIC** (Gou et al., 2023):

```
y0 = generate(task)                       # often CoT or PoT
for i in 1..n:
  c = critique_with_tools(y)              # search | interpreter | Perspective
  if stop(c): return y
  y = correct(y, c)
```

QA: greedy decode; CoT init then **n=3**, stop if answer unchanged **2 consecutive** rounds. Toxicity: **n=4**, stop if toxicity **<10%**. ChatGPT: **+7.7 F1** averaged over AmbigNQ/TriviaQA/HotpotQA; **+7.0 pp** on three math sets; toxicity probability **−79.2%**. ChatGPT HotpotQA F1: Vanilla 36.6, CoT 42.8, ReAct 50.2, CRITIC **52.9**, CRITIC w/o Tool **46.1** (below ReAct). ChatGPT GSM8K: CRITIC **78.2 (+5.7)** vs Vanilla. text-davinci-003 GSM8K PoT **70.1 → CRITIC 72.2 vs w/o Tool 68.3 (−1.8)**. Toxicity w/o Tool can exceed baseline (davinci 0.344 → CRITIC 0.180 vs w/o Tool **0.353**). Self-Eval on HotpotQA is ~**54%** at verifying own answers (barely above chance). Manual 100 HotpotQA traces: CoT hallucination **36% → CRITIC 7%**; remaining CRITIC errors shift to reasoning, refusal, and **49%** label-ambiguity/FN under F1>0.6. `CRITIC*` (oracle-aware upper bound) is **not** a production setting. Latency: **linear in n**; two math corrections ≈ **2×** PoT wall time; gains exist at **n=1**.

**When to attach which:** Reflexion if a **trial oracle** exists (tests, AlfWorld done). CRITIC if a **tool checker** exists (search with citation, interpreter, toxicity API). Self-Refine if the metric is **preference**, not truth. None of the three if the only signal is the same model saying “looks good.”

**Paper-to-role mapping (do not mix in a design review):**

| System | Actor | Environment | Critic | Verifier | Memory | Stop |
| --- | --- | --- | --- | --- | --- | --- |
| ReAct | Same LM thought+act | Tools / wiki / ALFWorld | None | Implicit (model stops) | Trajectory in context | Model or hop cap |
| Reflexion | ReAct actor | Env + tests | Separate reflection LM | Tests / EM / heuristic | Episodic, last **3** | Oracle pass or trial budget |
| Self-Refine | Same LM generate | None | Same LM feedback | Same LM “looks good” or score | History of drafts in prompt | **k≤4** or score |
| CRITIC | Same LM | Search / interpreter / Perspective | Same LM **after** tool | Tool output | None (in-context critiques) | Critique says correct, or **n=3/4** |
| LLMCompiler | Planner LM | Parallel tools | Joiner (optional replan) | Joiner “enough evidence” | DAG + results `$k` | Joiner answers |
| ToT | Thought proposer | None (internal) | LM state evaluator | Self-eval / exact 24 | Tree | BFS breadth `b` / DFS 100 steps |
| LATS | ReAct actions in MCTS | Env | Reflexion on failed paths | LM value + SC hybrid | Tree + reflections | Search budget |
| o1/R1 | Hidden tokens | Optional tools | Internal “wait” | External tests still required | None durable | `effort` / token budget |
| ADK LoopAgent | Writer/refiner LlmAgent | Shared session state | Critic LlmAgent | `escalate` or `max_iterations` | Session keys | Deterministic |
| PlanGuard | Victim agent | Tools + retrieved content | Isolated planner + intent verifier | Hard tool allowlist then LLM intent | \(S_{ref}\) from user only | Block Type I/II |

**Constitutional AI** (Bai et al.) is a **train-time** critic: sample → self-critique vs written principles → revise → SFT; RL phase RLAIF. Topology matches Self-Refine, but the critic is distilled into weights. Do not confuse RLAIF with a runtime Reflexion buffer. A “constitutional” model can still ReAct-loop — runtime still needs hop caps.

**Huang coupling table (why roles must split):**

| Coupling | What breaks | Evidence |
| --- | --- | --- |
| Planner = executor (classic ReAct) | Every observation re-plans; premature stop; same-tool loops | LLMCompiler Movie Rec ~85% exit before 8 searches; HotpotQA LLaMA-2-70B repetitive calls |
| Critic = generator (intrinsic) | Same blind spots; accuracy **drops** | GPT-3.5 GSM8K **75.9% → 75.1%** (round 1, 3 calls) → **74.7%** (round 2, 5 calls). GPT-4-Turbo **91.5% → 88.0%** with “assume could be wrong.” Llama-2-70B **62.0% → 36.5%**. Retains initial answer **74.7%** of the time on GSM8K; when it changes, net is negative |
| Critic without tools | Can **degrade** math/toxicity | CRITIC w/o Tool GSM8K PoT **−1.8**; toxicity **0.353** vs baseline 0.344 |
| Verifier = critic (LLM declares “it is correct”) | False-positive stop | Self-Refine stops when the model generates “it is correct”; Reflexion without tests **52% vs 60%** |
| Replanner can add tools | Post-reflection escalation | PlanGuard / CaMeL / Secure P-t-E: planner names **one tool** per step; executor is ephemeral with *only* that tool |

Multi-agent debate is **not** Reflexion: two models argue; a judge reads the transcript. Huang: published “wins” often beat a single sample but lose to **self-consistency** at equal sample count. Cost is 2N generations + judge; not a default production critic.

#### 2.4 PRM vs ORM; `same_action_k`

| Signal | Supervises | Example | Failure |
| --- | --- | --- | --- |
| **Outcome (ORM)** | Final answer / pass-fail | MATH label, unit-test gate, AlfWorld done | Credits lucky wrong reasoning; sparse |
| **Process (PRM)** | Each step correct/neutral/wrong | PRM800K; Lightman et al. | Step boundaries ill-defined; reward hacking if the PRM is learned |
| **Verbal process** | NL “what went wrong” | Reflexion traces | Uncalibrated; injectable |

**Let’s Verify Step by Step** (Lightman et al., ICLR 2024). 500-problem MATH slice (4,500 of 7,500 test problems folded into training). Best-of-**1860**: PRM **78.2%**, ORM **72.4%**, majority voting **69.6%**. Solution score = **product** of step-level scores with neutrals treated as positive (78.2% vs min 77.6%; neutrals-as-negative 77.4–77.8%). Gap **widens** with N — PRMs monetize test-time compute better than ORMs. PRM800K: ~**800k** step labels / **75k** solutions. Active learning **2.6×** data efficiency.

**Uesato et al. 2022:** outcome supervision matches **final-answer** error with **less** labeling (1–4 tokens/problem vs hundreds of process labels). Process (or an RM that *emulates* process) is required to cut **trace** error among final-answer-correct solutions: **14.0% → 3.4%**; final-answer error **16.8% → 12.7%**. Outcome-only models are right for the wrong reasons more often. Lightman is the **rerank** follow-on; Uesato is the **training-signal** result.

**ProcessBench** (Zheng et al., ACL 2025). **3,400** expert-annotated cases: identify **first erroneous step** or all-correct. Existing PRMs fail to generalize beyond GSM8K/MATH — Math-Shepherd-PRM-7B **47.9 → 23.8** GSM8K vs Omni-MATH. Prompted critics: GPT-4o-0806 mean **61.9**; QwQ-32B-Preview **71.5**; o1-mini **93.2 / 88.9 / 87.2 / 82.4** (mean **87.9**). Qwen2.5-Math-7B-PRM800K mean **56.5**, still **44.3** on Omni-MATH. A PRM that looks good on GSM8K is not a loop stopper on contest math.

**Snell et al.** (ICLR 2025): compute-optimal allocation (per-prompt difficulty) beats naive best-of-N by **>4×** less test-time compute on math. FLOPs-matched: test-time compute on PaLM 2-S* can beat a **~14×** larger greedy model when the small model already has non-trivial success. Beam search wins on harder questions / low budget; best-of-N wins on easy / high budget; beam search **over-optimizes** easy questions (reward hacking).

**DeepSeek-R1-Zero:** **no SFT**, GRPO, **rule-based** accuracy + format rewards only (explicitly **no** neural ORM/PRM). AIME 2024 pass@1 **15.6% → 77.9%**; cons@16 **86.7%**. R1 (cold-start + multi-stage RL) AIME pass@1 **79.8%** vs o1-1217 **79.2%**. Reflective-word count **5–7×**; “wait” spikes after ~**8k** RL steps. Three PRM limits they cite: (1) step granularity undefined in general reasoning; (2) intermediate correctness hard; (3) reward hacking + RM retrain cost. PRMs still useful for **rerank/search**, not as the sole RL reward at R1 scale.

**Production:** tests as ORM **stop**; optional PRM for *choosing among* failing-but-close patches. Never PRM-as-stop when pytest exists. Never intrinsic “check your work” as the only loop.

**`same_action_k`:** Reflexion AlfWorld heuristic — same act + same obs for several steps → fail trial / reflect. That is an **environment-level** breaker, not an LLM opinion. DeerFlow `LoopDetectionMiddleware` (LangGraph middleware, **not** OSS LangGraph core): hash canonical `(tool, args)`; warn at **3** identical sets; hard-stop at **5** by **stripping `tool_calls`**. Window **20**. Layer 2: same *tool type* warn **30** / hard **50** (catches `read_file` across different paths — unique hashes bypass layer 1; `read_file` on many paths consumed 150k–225k tokens and 60–280s until `recursion_limit=100`).

#### 2.5 Internalized reasoning vs explicit loops; memory

**o1** (Sep 2024): AIME 2024 pass@1 **74%** (11.1/15), cons@64 **83%**, rerank-1000 **93%**. GPT-4o: 12% (1.8/15). Snapshot `o1-2024-12-17`: AIME pass@1 **79.2%**, SWE-bench Verified **48.9%**. OpenAI does **not** return raw o-series CoT; ChatGPT surfaces summaries. CoT-must-remain-untrained-on-policy so it can be *monitored* is in tension with SOX-style audit of tokens you never received.

**Responses API pairing (2026):** reasoning items (`rs_…`) and the following assistant message/tool call must be replayed as a **consecutive pair**. Filtering history to messages-only → HTTP **400**. `previous_response_id` avoids manual pairing. Stateless/ZDR: `store=false` + `reasoning.encrypted_content`. Crash mid-thought still loses the tree unless the platform stored the item.

**Claude thinking / GPT-5.6.** Extended thinking: `thinking.type=enabled` + `budget_tokens` (min **1024**). Adaptive thinking on Opus 4.6+/Sonnet 4.6+; `budget_tokens` deprecated/removed on newer models (Opus 4.7+ rejects old syntax). Interleaved thinking billed as **output**; `usage.output_tokens_details.thinking_tokens` is the meter. Changing `effort` / `budget_tokens` **invalidates** prompt-cache breakpoints. GPT-5.6 `reasoning.effort` ∈ {none, low, medium (default), high, xhigh, max}. `gpt-5.6` alias → Sol. `mode=pro` is a separate knob on Sol. o3 snapshot `o3-2025-04-16` **deprecated**, shutdown **2026-12-11**, replacement `gpt-5.6-sol`.

**Voyager skill library:** executable JavaScript skills indexed by description embeddings. Inner loop: up to **4** refinement rounds; then mark fail and ask curriculum for a new task. Retrieval: **top-5** prior skills as ICL. **63** unique items in 160 prompting iterations, **3.3×** vs ReAct/Reflexion/AutoGPT. Ablations: random curriculum **−93%** items; remove self-verification **−73%** (largest of the three feedback types); GPT-3.5 instead of GPT-4 **5.7×** fewer items; no skill library → late-stage plateau. Skills are **append-only files**, not overwritten weights. Production: **promote successful traces into typed skills, not chat summaries**.

**Generative Agents:** memory stream; reflections when recent importance sum > **150** (~2–3×/day). Retrieval = recency (decay 0.995/hour) + relevance + importance. Failure mode: retrieving *wrong* memories. CoALA: planning is a decision cycle over working/episodic/semantic/procedural stores. Reflection that *writes back* is a planner input on the next cycle — same poisoning path as Reflexion buffers.

**State machines:**

```
retrieve-grade-rewrite (RAG; hop cap is a fuse — see 01-rag):
  retrieve → grade chunks → rewrite query

critic-replan (evaluator-optimizer / Reflexion / ADK LoopAgent):
  draft → (oracle | critic) → fail? → reflect/replan → draft
                           → pass? → END
  state: draft, feedback, trial_id, memory[], replan_count

plan-execute (LangGraph):
  planner → agent(plan[0]) → replan → END | agent
```

---

### 3. Token Economics & NFR Analysis

Thinking/reasoning tokens are **output-priced** on OpenAI, Anthropic, and DeepSeek thinking modes. Cache is the large lever on the **input** of multi-step graphs (see 03-caching; here only as a loop cost driver).

#### 3.1 Published SKUs that price a loop (2026-09-02)

| Model (API) | Input / 1M | Cached in | Output / 1M | Notes |
| --- | --- | --- | --- | --- |
| OpenAI **gpt-5.6-sol** | $4.00 | $0.40 | $20.00 | Flagship; `reasoning.effort` none…max; promo through **2026-11-21**. >272K input: **2×** in / **1.5×** out for the **full** request. Cache writes **1.25×** uncached input |
| OpenAI **gpt-5.6-terra** | $2.00 | $0.20 | $12.00 | Mini-class; same effort enum |
| OpenAI **gpt-5.6-luna** | $0.20 | $0.02 | $1.20 | Nano-class / cheap executor |
| OpenAI **o3** (`o3-2025-04-16`) | $2.00 | $0.50 | $8.00 | Still listed; **shutdown 2026-12-11** → `gpt-5.6-sol`. 200k ctx / 100k max out |
| Claude **Haiku 4.5** | $1 | hit $0.10; 5m write $1.25 | $5 | Cheap critic/verifier role; 200k / 64k out |
| Claude **Sonnet 5** | $2 | hit $0.20 | $10 | Adaptive thinking default effort `high` on current card |
| Claude **Opus 5** | $5 | hit $0.50 | $25 | Thinking billed as output |

Anthropic: cache hit = **10%** of base input; 5-minute write **1.25×**, 1-hour write **2×** (Haiku card). Batch **50%** off input and output. US-only inference **1.1×**. OpenAI regional processing: **10%** uplift for eligible models released on/after 2026-03-05.

> ⚠️ Gap: o3 “2–5× GPT-4.1 bill” appears in third-party blogs (PerUnit) as an observational claim, **not** an OpenAI published average. Do not treat it as a SKU. DeepSeek-R1 published a concrete thinking-token average on a 2024 contest set: **8,793** thinking tokens/problem, <7k on easy / >18k on hard, 61.8% pass@1 at that budget — R1-specific, not o3.

**Role-based routing (do not use one frontier model for all four roles):**

| Role | Cheap default (2026-09) | Escalate when |
| --- | --- | --- |
| Planner | Terra / Sonnet 5 / V4-Flash-class | Cyclic deps, PDDL, safety CFI |
| Executor (tool args) | Luna / Haiku 4.5 | Args are code or SQL |
| Critic | Haiku 4.5 **with tools** (CRITIC) | No oracle exists — then **do not attach** |
| Verifier | pytest/sympy **$0** | Open-ended only → judge with swap-order |
| Replanner | Same as planner, `max_replans=2` | After cap: human |

Putting Sol-high on LLMCompiler-shaped parallelism is the usual bill shock: planner+joiner already dominate when tools are fast.

#### 3.2 `$ cost per 1k tasks` **[inferred]**

**T★ definition (explicit, not a vendor metric):** one enterprise “research → act → verify” job with a **hard oracle** (tests or DB predicate).

- Planner: 4k in + 600 out (JSON plan, ~6 nodes)
- Execute 4 tool rounds: 8k in + 400 visible out each (observations grow)
- Critic (optional): 10k in + 500 out, **Haiku 4.5** unless noted
- Verifier: pytest **$0 model**
- No ToT/LATS. Replan on 20% of jobs **[assumed]** unless stated.
- Cache: 70% of repeated system+tools on rounds 2–4 **[assumed]** at published cache-hit rates.

Call counts: 0-critique = 1 plan + 4 execute = **5** LLM calls; 1-critique = **6**; N=3 Self-Refine-style = 1 init + 3×(feedback+refine) = **7** extra critic/refine calls on top of generate (paper k=4 max).

| Stack | Model $ / 1k T★ **[inferred]** | Method |
| --- | --- | --- |
| **A. 0 critique**, Terra planner+executor, $0 tests | **~$55–70** | 5× ~8k in × $2 + 5× ~0.5k out × $12; 70% of 4 follow-up inputs at $0.20 |
| **B. 1 critique round**, Haiku critic | **~$60–80** | A + 10k×$1 + 0.5k×$5 |
| **C. 3 critique rounds**, Haiku | **~$75–100** | B × ~3 critic+refine; context growth dominates |
| **D. Same as B but Sol executor** with medium thinking **+2.5k thinking out/call [assumed]** | **~$200–350** | 5× 2.5k × $20 thinking + visible out; do not put Sol on DAG-shaped tool parallelism |
| **E. LLMCompiler vs ReAct** (paper, GPT-3.5-era list prices) | **0.15–0.30× ReAct $** | Up to **6.7×** cheaper when 8-way parallel; not a 2026 SKU |
| **F. ToT Game of 24** (GPT-4 2023 prices in paper) | **$740 / 1k puzzles** | $0.74/case; CoT-Bo100 $0.47 at 49% vs ToT 74% |
| **G. LATS WebShop** | **~$100× LLMCompiler wall-clock** | 101.7× slower than LLMCompiler at similar score |
| **H. SWE-agent GPT-4 Turbo** | **mean $1.59 resolved-mix; cap $4** | Not T★; full SWE-bench 12.47% resolved |

Worked T★ arithmetic for stack A **[inferred, not a SKU]** using Terra $2 / $0.20 cached / $12 out:

- Plan: 4k uncached in × $2/1M = $0.008; 600 out × $12/1M = $0.0072
- Execute rounds 1–4: round 1 is 8k uncached; rounds 2–4 are 30% uncached + 70% cached:
  in = \(8000\times2/10^6 + 3\times(0.3\times8000\times2 + 0.7\times8000\times0.20)/10^6\) = $0.016 + 3×$0.00592 = **$0.0338**
  out = 4×400 × $12/1M = $0.024
- **Subtotal ≈ $0.073/task → ~$73 / 1k** before tool HTTP. Range **$55–70** in the table assumes slightly shorter observations and a higher cache hit than 70% on round 1 tools. Order-of-magnitude, not finance.

**Huang cost of a useless critic:** intrinsic self-correction uses **3** model calls after round 1 and **5** after round 2 vs **1** for standard prompting, while GSM8K **drops** 75.9 → 74.7. **[inferred]** At Terra rates, that is ~3–5× input tokens of the original prompt with **negative** quality. Do not buy this loop.

Self-Refine token multiplier: INIT + up to 4×(FEEDBACK+REFINE) = **1 + 8** generations worst case vs 1. Paper k=4 is the published cap, not an accuracy optimum; stop early on “looks good.”

Reflexion trial multiplier: AlfWorld learns over **12** trials; coding uses multiple generate-test-reflect iterations. HumanEval +11 pp (80.1 → 91.0) is **not** free: each failed trial is a full generation + tests + reflection.

SWE-agent: per-instance **$4** auto-submit. Successes: median **$1.21 / 12 steps**; unsuccessful mean **$2.52 / 21 steps**. **93%** of resolved runs submit before budget vs **69%** overall — raising the cap is a weak lever. ACI vs shell-only: **64% relative**. Lint-on-edit: 51.7% of trajectories have a failed edit; agents often recover.

Extra critic hop on oracle fail ≈ Haiku 10k in + 0.5k out ≈ **$0.0125/fail [inferred]** vs a wrong refund.

> ⚠️ Gap: CLEAR-style **$1.24 vs $5.12** Plan-Execute vs Reflexion appears in secondary roundups, not a named vendor study bound to a URL with methodology — **do not quote as a benchmark**. Use LLMCompiler’s 6.7× and Huang’s 5-call intrinsic drop instead.

#### 3.3 Latency SLA — extra hops, numeric ms

> ⚠️ Gap: **No vendor publishes p50/p95/p99 of “+1 critique hop” on a production agent SLO.** Paper/framework numbers below are **means / classes**, not percentiles. Policy targets are architecture-derived **[inferred]** from `extra_hop ≈ TTFT_critic + T_decode_feedback + (optional) T_refine`. If the critic is a frontier model on the **user** path, you have built a latency tax, not a quality sidecar. Put the critic **off** the p99 path unless the oracle is local (tests). Braintrust/LangSmith online judges are the eval analog: async, not request-path.

**Published hop facts (ms, not percentiles):**

| Loop | Published | Source class |
| --- | --- | --- |
| LLMCompiler Movie Rec planner | **1,880 ms** avg | Serial residual when tools are fast |
| LLMCompiler Movie Rec answer | **1,620 ms** avg | Joiner/answer serial |
| LLMCompiler parallel search | slowest **1,130 ms** vs mean **610 ms** | Straggler |
| LLMCompiler vs ReAct | up to **3.7×** wall-clock | When deps permit |
| LLMCompiler vs LATS WebShop | **101.7×** | Similar score |
| CRITIC | linear in `n`; 2 math corrections ≈ **2×** PoT; gains at `n=1` | Wall-clock multiplier |
| OpenAI o1 / o3-pro | “several minutes” class; docs recommend background mode | Internalized, one call |
| Claude thinking | Extra decode; budgets >32k recommended via Batch to avoid timeouts (third-party API guides) | Not a percentile |

**[inferred] policy targets — extra hops (numeric ms).** Clock-split: (a) user-facing agent; (b) critic/oracle sidecar. Happy path skips the critic.

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **Happy path extra-hop tax** (critic skipped: oracle pass, or no critic attached) **[inferred policy]** | **0 ms** | **0 ms** | **0 ms** | Critic only after oracle fail. User p99 stays one plan-execute |
| **Local pytest / DB / compiler verifier** **[inferred]** | **20 ms** | **80 ms** | **250 ms** | Local process; timeout **500 ms [policy]** then fail-closed. Never an LLM |
| **One extra Haiku-class critic hop ON user path** (anti-pattern) **[inferred]** | **2,000 ms** | **6,000 ms** | **15,000 ms** | p50 anchored on published planner-class hop **1,880 ms**; p99 **skip critic** (fallback chain). 10k-in/500-out is larger than a 2k/200 judge — treat 2,000 ms as a **floor**, not a measurement |
| **Same critic hop OFF user path** (async after oracle fail) **[inferred policy]** | **0 ms** user tax | **0 ms** | **0 ms** | Sidecar; do not block the refund |
| **Two CRITIC math corrections ON path** (≈2× PoT, published multiplier) **[inferred]** | **4,000 ms** | **12,000 ms** | **30,000 ms** | Linear in `n`; `n=1` already helps — do not stack `n=3` on the SLO path |
| **Serial plan+answer residual** (Movie Rec, tools fast) **[inferred p50-class from published means]** | **3,500 ms** | **6,500 ms** | **12,000 ms** | 1,880+1,620 published avg as p50-class; DAG does not help when planner share >50%. Do not put Sol-high here |
| **Internalized o-series / high effort** (one call, hidden tokens) **[inferred from “several minutes” class]** | **120,000 ms** | **180,000 ms** | **300,000 ms** | Background mode; still need external verifier; crash loses the tree unless Responses API stored the item |
| **ReAct extra tool cycle** (LangGraph: 2 super-steps) **[inferred]** | **2,000 ms** | **8,000 ms** | **20,000 ms** | One model + one tool RTT; p99 is the slow tool + decode, not the fuse. Cap with `max_turns` / `same_action_k` |

**Mitigations mapped to percentiles:**

- **p50 (user):** skip critic on pass; DAG-parallel tools (`max()` not `sum()`); Terra/Haiku on executor; stream TTFT; cache system+tools prefix (70% hit on rounds 2–4 in T★).
- **p95:** `max_replans=2`; CRITIC `n=1` if you need a tool-check; timeout the critic independently; remaining-steps route to END **inside** the graph so the checkpoint is a completed run.
- **p99:** never put a frontier critic on the handler; `same_action_k` hard-stop; Claude always set `maxTurns` **and** `maxBudgetUsd`; internalized high-effort is a **background job**, not a chat SLO.

#### 3.4 Throughput / back-pressure

| Ceiling | Number | Effect |
| --- | --- | --- |
| OpenAI Agents SDK `max_turns` | **10** (default); `None` disables | One turn = one model invocation **including** its tool calls. Parallel tools still one turn |
| Claude `maxTurns` / `maxBudgetUsd` | **No default** | Unbounded “improve this repo.” Hitting either → `error_max_turns` / `error_max_budget_usd`. Budget covers subagents (Claude Code ≥ v2.1.217) |
| LangGraph `recursion_limit` | **1000** default ≥1.0.6; schema still **25** | ReAct cycle ≈ 2 super-steps. Default 1000 is **not** a product policy |
| LangChain `ModelCallLimitMiddleware` | example `thread_limit=10`, `run_limit=5` | `exit_behavior` `end` or `error`. Thread limit needs a checkpointer |
| LangChain `ToolCallLimitMiddleware` | example global `thread_limit=20`, `run_limit=10`; per-tool e.g. search 5/3 | `exit_behavior`: `continue` (default), `error`, `end` |
| ADK `LoopAgent.max_iterations` | examples **5** / **10**; must set | Outer loop only; does not propagate |
| Self-Refine `k` | max **4** | Stop on “looks good” or score |
| CRITIC `n` | QA **3** (stop if 2 consecutive answers match); toxicity **4** | Linear wall-clock |
| Voyager inner refine | **4** then curriculum skip | Do not infinite-decompose |
| Reflexion memory / WebShop trials | last **3**; WebShop cut at **4** | Further reflections do not explore a different catalog |
| `max_replans` | **2–3** on state (not built-in) | Conditional edge to END |
| Temporal event history | warn **10,240**; terminate **51,200** events or **50 MB**; also >2000 Updates / >10000 Signals | Continue-As-New every **100–1000** iterations |
| SWE-agent spend | **$4** / instance | Raising the cap is a weak lever |
| DeerFlow identical `(tool,args)` | warn **3** / hard **5**; window **20** | Strip `tool_calls` on hard-stop |
| DeerFlow tool *type* | warn **30** / hard **50** | Catches unique-hash bypass |

**Unit conversion (do not mix in an SLO):**

| You configured | Equivalent ReAct tool rounds (typical) |
| --- | --- |
| LangGraph `recursion_limit=25` (legacy) | ~12 model+tool cycles |
| LangGraph `recursion_limit=1000` (default ≥1.0.6) | ~500 cycles — **not a product policy** |
| Agents SDK `max_turns=10` | ≤10 model calls; tools **inside** the turn do not add turns |
| Claude `maxTurns=8` | ≤8 **tool-use** turns; a final text reply is extra and uncounted; streaming reset starts a new counter |
| ADK `max_iterations=5` | 5 critic+refine **pairs** if both are sub-agents |
| Self-Refine k=4 | ≤4 feedback+refine after INIT; 9 generations worst case |
| CRITIC n=3 QA | ≤3 verify-correct after CoT init |

**Back-pressure design:** (1) admit work with `max_turns` / `max_replans` / `maxBudgetUsd` in **config**, not a prompt; (2) bulkhead **user serve** vs **critic API** vs **tool fleet** — a Haiku 429 must not stall the happy path; (3) `same_action_k` + tool-type frequency as data-plane fuses; (4) degrade: skip critic → execute once → deterministic refuse / HITL; (5) Temporal: Activities return hashes; blobs in object storage — HTML in Workflow history hits 50 MB before `max_replans`; (6) never ship `max_turns=None`; (7) token budget: SWE $4 and Claude `maxBudgetUsd` are the published $ fuses.

#### 3.5 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of the loop vs of the critic** | Product SLO is one plan-execute. Critic is **best-effort on fail**. Circuit-open critic → skip critic, do not 500 the user. Oracle (pytest/DB) is fail-closed for consequential actions | Quality on the tail vs user p99 |
| **RPO of checkpoints** | Last super-step snapshot (`PostgresSaver`) / last Temporal Continue-As-New. `InMemorySaver` RPO = **empty on restart**. Encrypted reasoning `store=false`: RPO of hidden CoT = **empty** — you cannot checkpoint mid-thought | Debug resume vs ZDR |
| **RTO of checkpoints** | Resume from `thread_id` + `checkpoint_id`. Replay **re-executes** nodes after that checkpoint (LLM/tools may differ) — debugger, not audit. Replay of the final checkpoint (no `next`) is a no-op. RemainingSteps → graceful END so the last checkpoint is a **completed** run; `GraphRecursionError` leaves the last checkpoint as the **failed** super-step | Time-to-resume vs forensic truth |
| **RPO of memory / skills** | Episodic hints: last **3** in checkpoint (dies with the thread unless copied). Store copy is the **poisoning path**. Skills: append-only after oracle pass (Voyager). Untrusted namespace TTL | Lifelong learning vs injection |
| **RTO of memory** | Rebuild critic from **oracle logs**, not webpage text. Do not restore untrusted reflections into the system prompt | Velocity of “lessons” vs safety |
| **Compliance** | Visible plan JSON + tool receipts + verifier verdicts. Hidden CoT summaries ≠ SOX. Critic = subprocessor (reflections re-export PII). PCI: do not persist PAN in plan/critique. GDPR erasure of a “lesson” is Store-namespace surgery, not trace TTL | Auditability vs internalized reasoning |
| **Correctness vs $** | Oracle-gated Reflexion is +11 pp HumanEval *with tests*. Intrinsic critic is **negative** EV (Huang). SWE: ACI + tests ≫ raising $4 | Token multiplier (3–9×) vs quality |
| **Security vs utility** | PlanGuard Stage I+II: ASR **0%** structural; Stage-I-only FPR **27.00% DH / 38.01% DS**; full FPR **0.97% / 3.28%**. CaMeL **77%** vs **84%** undefended (**−7 pp**). Legitimate tools implied only by retrieved content are Type I blocked | “Do whatever the page needs” |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_checkpoint = last successful super-step / Continue-As-New (seconds if Postgres fsync; **empty** if InMemory). RTO_checkpoint = resume `thread_id` (seconds) vs “we dropped reasoning items” (HTTP 400; cannot restore the hidden tree). RPO_skills = last oracle-passed append. RPO_untrusted_hints = TTL expiry (minutes–hours by policy). A spend-cap / `maxBudgetUsd` stop is a **completed** refuse, not an RPO hole — log it as `error_max_budget_usd`.

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: LangGraph checkpoints, Temporal, hop caps

**LangGraph.** Checkpointers save a snapshot at every **super-step** boundary. A super-step is one Pregel tick: all scheduled nodes run (possibly in parallel), reducers merge, then checkpoint. Sequential `START → A → B → END` produces checkpoints after input, A, and B. Resume only from a checkpoint, not mid-node. `thread_id` required; optional `checkpoint_id` forks/time-travels. Pending writes: successful node writes inside a super-step are preserved when a sibling fails — resume does not re-run completed work. Replay from `checkpoint_id` **re-executes** nodes after that checkpoint. `InMemorySaver` dies on restart — not production. `PostgresSaver` / `AsyncPostgresSaver` for production. `thread_id` column: keep under **255** chars. OSS Postgres has historically lacked `prune`/`keep_latest` — application-level TTL or Agent Server retention is required. HITL: `interrupt()` before a destructive tool — the checkpoint holds plan + pending args; a human PATCH is another state update. The plan waits in DB, not in a Python stack frame.

`recursion_limit` is a **top-level** `invoke`/`stream` config key, **not** inside `configurable` (silent no-op if misplaced). Default **1000** since 1.0.6 means “hope the model stops” is **not** a policy. Still need `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware` / `max_replans` on state.

| Approach | When it fires | Where you handle it | Graph result |
| --- | --- | --- | --- |
| Proactive `RemainingSteps` | Before the last allowed super-step | Conditional edge inside the graph | Route to END / “budget exceeded”; checkpoint is a **completed** run |
| Reactive `GraphRecursionError` | After `langgraph_step` exceeds `recursion_limit` | Caller `try/except` | Execution **terminated**; last checkpoint is the **failed** super-step |

Checkpointer vs Store: checkpointer is **thread** state (current plan, `past_steps`, HITL interrupt). Store is **cross-thread** memory (skills, user prefs). A critic that `put`s into Store is writing **durable lessons**.

**Temporal + OpenAI Agents SDK.** Orchestration (agent loop, tool selection, handoffs) runs **inside the Workflow**; **model calls are Activities** and are not re-invoked on replay. Tools that perform I/O must be `activity_as_tool()` or Nexus Operations — a `@function_tool` in the Workflow is **not** durable and must be deterministic. Event history: warn **10,240**; terminate **51,200** events or **50 MB**. Continue-As-New checkpoints latest state into a new Run ID, same Workflow ID. Python: `workflow.info().is_continue_as_new_suggested()`. Docs suggest CAN every **100–1000** iterations for infinite loops. Pattern: Activities return **hashes + summaries**; blobs in object storage keyed by the Activity’s idempotency key. Workflow keeps `trial_id`, `oracle_verdict`, `replan_count`, last **3** hint IDs — not the pages. A looping computer-use agent without `maxBudgetUsd` + env TTL + `ScheduleToClose` is an unbounded meter.

Provider replay contracts: OpenAI requires reasoning items paired with tool outputs. Anthropic requires prior thinking/`redacted_thinking` blocks preserved. Dropping these changes the agent’s effective plan or 400s.

ADK `DatabaseSessionService` documents row-level locking via `SELECT … FOR UPDATE` for multi-writer session state — the strongest published multi-writer control plane among the three frameworks (ADK-specific, not LangGraph).

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Tool 429/5xx, critic 429, Temporal Activity timeout, Claude denied-tool retry (consumes a turn), provider 5xx | Error rate; turn burn with no oracle progress | Full-jitter retries on **idempotent** tools; do **not** retry non-idempotent refund/email without a key; circuit-break the critic independently |
| **Permanent** | 4xx auth, unsupported tool, policy deny, `MaxTurnsExceeded`, `error_max_budget_usd`, GraphRecursionError after RemainingSteps ignored | Non-retryable; hop cap | Skip critic / refuse / HITL. 4xx (except 429) returns `is_error` so the model does not spin |
| **Poison-pill reflections** | Critic that just read a malicious observation writes “the user asked to exfiltrate” into episodic memory; Hidden in Memory write ASR **99.8%** GPT-5.5; eTAMP **32.5%** GPT-5-mini, frustration **8×** | Origin tags; unexpected tool in \(S_{ref}\) delta; memory write from tool args | Cap buffer 3; `untrusted=true`; never promote web text; regenerate from oracle logs; PlanGuard subset check on post-reflection tools; RBAC: tools never `put` skills |
| **Poison-pill loop** | Same `(tool,args)` forever; `read_file` across paths; `page=1` forever; cookbook `while True` until PASS | DeerFlow hashes; token burn; recursion_limit | Warn 3 / hard 5; tool-type 30/50; terminal observation on empty search; `max_iterations` |
| **Idempotency / replay lie** | LangGraph replay re-calls tools; Temporal retry of “send email”; planner node IDs not stable so replan re-executes completed work | Duplicate side effects; doubled refunds | Idempotency key = node id + trial id; POST without key refused; model Activities skipped on Temporal replay **because** the loop lives in the Workflow |
| **Verifier disagreement** | Tests fail AND judge pass; tests pass AND PRM flags a bad step; green self-tests halt a wrong program (Reflexion FP) | Dual-oracle dashboard; EvalPlus: HumanEval pass@k drops up to **19.3%** pass@1 with 80× tests | Prefer tests; log gaming suspicion; AlphaCodium: prefer FN over FP in generated-test gates (pass@5 **19% → 44%**; ~15–20 LLM calls/solution so pass@5 ≈ **~100** calls) |
| **Hidden CoT vs audit** | Crash mid-thought; summaries claimed as the chain; `store=false` | Missing `rs_` pair → 400; no plan JSON | Visible plan + tool log for regulated actions; do not claim “the model reasoned correctly” from a summary |

#### 4.3 Circuit breaker: closed → open → half-open

Independent breakers: **critic API**, **tool fleet**, **same_action_k / max_replans**, **verifier disagreement**. A critic TPM storm must not stall chat (**bulkhead**). `same_action_k` is a data-plane breaker; `max_replans` is a control-plane breaker; verifier disagreement (tests fail, judge pass) trips a **policy** breaker — prefer tests, do not average.

```
        same_action_k hard | max_replans | critic 5xx/429 window | verifier split
  ┌──────────┐  ─────────────────────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                                       │   OPEN   │
  │ pass all │  success resets consecutive count                     │ fail fast│
  └────┬─────┘                                                       └────┬─────┘
       ▲                                                                  │ cooldown elapsed
       │ trial success                                                    ▼
       │                                                            ┌──────────┐
       └──────────── trial OK ──────────────────────────────────────│ HALF-OPEN│
                    trial fail → OPEN                               │ 1 probe  │
                                                                    └──────────┘
```

**Thresholds [policy, not vendor SLO], grounded in published fuses:**

| Trip condition | Closed → open | Half-open probe |
| --- | --- | --- |
| Identical `(tool, args)` | DeerFlow: warn **3**, hard **5** (strip `tool_calls`) | Allow one **different** tool; same hash → stay open |
| Tool *type* frequency | warn **30** / hard **50** | One probe of a different type |
| `max_replans` | **2–3** on state → END / HITL | Do not probe a fourth replan automatically |
| Critic API 5xx/429 | consecutive failures ≥ **5** or error-rate window | One critic call; fail → skip critic |
| Verifier disagreement | tests fail ∧ judge pass → **prefer tests**, open the judge breaker | Judge stays skipped until a human recalibrates |
| Claude denied tool | rejection as tool result; retry **consumes a turn** — count toward `maxTurns` | Not a critic trip; it is turn burn |

**Fallback chain (cited policy):** **oracle critic (Haiku + tool/logs) → skip critic → execute once → deterministic refuse / HITL.** Never: skip oracle and keep the critic. Never: verifier fail → “looks good, ship.” Never: fail-open a refund because the critic 429’d — **refuse or HITL**. Hedging a second critic of the **same** checkpoint is Huang’s correlated-error loop.

#### 4.4 Zero-Trust MCP, tool-level RBAC, PII pipeline, immutable lesson logs

**Zero-Trust: model proposes, PEP disposes.** CaMeL (Debenedetti et al., DeepMind): Privileged LLM sees **only** the trusted user query and emits a restricted-Python plan; Quarantined LLM processes untrusted data **with no tools**; custom interpreter attaches **capabilities** to every value and enforces policy at tool-call time. Untrusted data cannot change control flow. AgentDojo: **77%** tasks with provable security vs **84%** undefended (**−7 pp** utility tax). Capability tags travel with values: a string that came from a webpage cannot flow into `send_email(to=…)` unless policy allows.

PlanGuard (Gong et al., 2026): training-free. Isolated Planner \(\mathcal{P}(I,\mathcal{T})=S_{ref}\) never sees retrieved content. When the victim emits \(a_{act}=(t_{act},v_{act})\):

| Stage I case | Condition | Action |
| --- | --- | --- |
| Exact match | \(a_{act}\in S_{ref}\) | Pass |
| Unauthorized tool (Type I) | \(t_{act}\) not in any \(S_{ref}\) tool name | **Block** |
| Parameter mismatch | tool name OK, args ≠ any reference | Suspend → Stage II |

Stage II Tool Intent Verifier: \(V_{res}=\mathcal{M}_{verify}(I,S_{ref},a_{act},r_{act})\in\{T,F\}\). Passes stochastic formatting (`last_week` vs `lastweek`); blocks Type II intent shift. ASR **0%** is **structural**. Stage-I-only FPR **27.00% DH / 38.01% DS** — you cannot skip Stage II. Full system FPR **0.97% DH / 3.28% DS**. Vanilla InjecAgent body: DH **56.90%**, DS **88.67%** under a compliance-inducing prompt; abstract quotes **72.8% → 0%** with FPR **1.49%**. Wrap execute; do not retrain.

**Limitation [inferred from method]:** any **legitimate** tool implied by retrieved content but not by \(I\) (user said “summarize this page,” page asks to email a colleague) is Type I blocked. Replan that feeds observations back into \(\mathcal{P}\) **destroys** isolation — freeze \(S_{ref}\) or HITL-extend it.

Combining: PlanGuard decides *which tools* may fire; CaMeL decides *which values* may fill their args. Neither is a critic. Secure P-t-E: planner names the **single** tool per step; executor is a temporary agent with *only* that tool.

OpenAI Agents SDK: tool guardrails (`ToolInputGuardrailTripwireTriggered` / `ToolOutputGuardrailTripwireTriggered`). Input guardrails `run_in_parallel=False` block the model until the check completes. Only the first agent’s input guardrails run. Claude Agent SDK: hooks intercept tools **before** execution; `permissionMode` gates which tools run. `maxTurns` is **not** a dollar cap.

**InjecAgent** (Zhan et al.): 1,054 cases, 17 user tools, 62 attacker tools. ReAct GPT-4 ASR **23.6%** base / **47.0%** enhanced; Llama2-70B **>80%**; fine-tuned GPT-4 **3.8–7.1%**. **Hidden in Memory** (2026): poisoned memories written up to **99.8%** on GPT-5.5, **95%** on Kimi-K2.6; among successful retrievals, attacker-intended agentic actions **60–89%**; dormant across later conversations; adversary has **no** direct memory API. **eTAMP:** one contaminated observation poisons raw trajectory memory; activates on **different sites/sessions**; ASR up to **32.5%** GPT-5-mini, **23.4%** GPT-5.2, **19.5%** GPT-OSS-120B; frustration (dropped clicks, garbled text) increases ASR up to **8×**. **MPBench:** prompt-injection defenses **fail** to cover memory poisoning; one successful write persists.

**Tool-level RBAC (least privilege):**

| Tool / write | Who | Must not |
| --- | --- | --- |
| `execute_node {allowlisted}` | Executor, identity from token | Omnibus `search(collection)`; model-filled `tenant_id` |
| MCP `tools/call` | Same; PEP ∩ \(S_{ref}\) ∩ capabilities | Run tools the isolated planner never named |
| `critic.reflect` | Orchestrator after **oracle fail** | Read raw webpage into the hint; emit tool calls |
| `memory.put` untrusted namespace | Orchestrator service account | Be a tool the model can call |
| `store.put` skills / beliefs | Orchestrator **after verifier pass** | Critic or tool `put` |
| `dataset.write` / “promote lesson” | Human + control-plane role | Auto-promote a reflection |
| `refund` / `send_email` | Executor + HITL interrupt | Run from critic text |

LangGraph Store namespaces e.g. `("t", tenant, "u", user)` at write-time: only the control plane may `put` into skill/belief. Critic writes go to `("t", tenant, "u", user, "untrusted_reflections")` with TTL. Read-time: re-score in the current query context; do not treat retrieved reflections as system instructions (spotlighting / typed envelopes).

**PII pipeline — detect → redact → audit — on reflections and memory, before persist and before the next critic call.** Plans and critiques contain customer identifiers, retrieved documents, and failed test logs. Hidden CoT: you cannot SOX-audit tokens OpenAI never sent. Judge/critic models are **subprocessors** — reflections that include PII and then get retrieved into the next prompt export that PII to the critic vendor again.

1. **Detection (regex + NER/classifier, on the control plane before Store/checkpoint write).** Scan plan JSON, critic text, episodic hints, tool observations about to be summarized into Workflow history, and skill-promotion candidates. Regex: email, US SSN, US phones, PANs. NER: names in tickets regex misses (Presidio / Comprehend-class). Dual-gate: regex is cheap/high-precision on PAN/email/phone; NER catches “call Jane at the Dallas desk.” If the classifier is down: **fail closed on memory writes and critic egress** (still serve the user with skip-critic) — do not copy raw reflections into Store or to a cloud critic. Persist: plan JSON, tool names + **arg hashes**, verifier verdicts, critic IDs, origin tags. Raw observations: shorter TTL, encryption, tenant partition.

2. **Redaction.** Replace with stable tokens (`[EMAIL_<hash12>]`) so task structure (refund amount, tool names, test assertion names) survives for the next trial. Critic receives **already-redacted oracle logs**, not the ticket body from the web. Do **not** send cardholder data to the critic vendor at all. Self-host the critic (Haiku-class in-VPC, or skip critic) when support-ticket PII is in the loop.

3. **Audit trail (WORM).** Immutable log of detect/redact **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action (`tokenize`/`strip`/`block-from-critic`/`block-from-store`), detector (`regex`|`presidio`|`ner`), `trial_id`, `thread_id`, `origin`. Separate **who wrote a lesson**: `actor=orchestrator|human`, `origin=critic|oracle|skill_promote`, `oracle_hash`, timestamp, tenant. Sampled APM traces are **not** this tape. A critic `put` without an audit row is a control-plane bug.

**Zero-Trust loop contract (config, not a prompt):**

1. **Who may write the plan?** Isolated planner (PlanGuard) or P-LLM (CaMeL), never the observation string.
2. **Who may execute a tool?** PEP: allowlist ∩ \(S_{ref}\) ∩ capability tags; hooks / `ToolInputGuardrail`.
3. **Who may write memory?** Orchestrator service account; critic → untrusted namespace only; tools never `put` skills.
4. **Who may stop the loop?** Oracle (tests/DB) > hop caps > model “Finish”.
5. **Who may add a tool after reflection?** HITL or frozen allowlist (Secure P-t-E one-tool-per-step).
6. **What is logged?** Visible plan JSON + tool names + arg hashes + verifier verdict — not hidden CoT summaries as the audit.

A failing loop is a **security** event, not only a quality event: frustration increases injection ASR up to **8×**.

---

### 5. Production Enterprise Code

Self-contained stdlib. Optional LangGraph/Temporal wiring is commented. Run: `python agent_feedback_loop.py`.

Wired: retries + full jitter, circuit breaker (closed → open → half-open) on the **critic**, fallback **oracle critic → skip critic → deterministic refuse**, `max_turns=10`, `max_replans=2`, `same_action_k` warn 3 / hard 5, PII detect→redact→audit **before** memory write, origin-tagged untrusted hints (cap 3), idempotent tool keys, structured logs with correlation IDs. Happy path **never** waits on a down critic.

```python
#!/usr/bin/env python3
"""Agent feedback-loop harness: hop caps, same_action_k, critic fallback.

Stdlib only. Swap FakeOracle / FakeLlm for pytest and a provider SDK.
# Optional: from langgraph.checkpoint.postgres import PostgresSaver
# Optional: from temporalio import activity, workflow
Run: python agent_feedback_loop.py
"""
from __future__ import annotations

import hashlib, json, logging, random, re, threading, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

MAX_TURNS = 10
MAX_REPLANS = 2
SAME_ACTION_WARN = 3
SAME_ACTION_HARD = 5
MEMORY_CAP = 3


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (("correlation_id", "-"), ("tenant_id", "-"),
                     ("trial_id", "-"), ("turn", "-")):
            setattr(record, k, getattr(record, k, d))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("loop")
    if logger.handlers:
        return logger
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","cid":"%(correlation_id)s",'
        '"tenant":"%(tenant_id)s","trial":"%(trial_id)s","turn":"%(turn)s",'
        '"msg":"%(message)s"}'
    ))
    h.addFilter(CorrelationFilter())
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(level: int, msg: str, *, cid: str, tenant: str, trial: str = "-",
         turn: int | str = "-", **fields: object) -> None:
    extra = {"correlation_id": cid, "tenant_id": tenant,
             "trial_id": trial, "turn": str(turn)}
    LOG.log(level, "%s %s", msg, json.dumps(fields, default=str), extra=extra)


class TransientError(Exception):
    """429, 5xx, timeout, circuit open — retry idempotent tools / critic."""


class PermanentError(Exception):
    """4xx auth, policy deny, hop cap — do not retry."""


class CircuitOpenError(TransientError):
    pass


def retry_with_jitter(
    fn: Callable[[], object], *, cid: str, tenant: str, trial: str, op: str,
    attempts: int = 4, base_s: float = 0.05, cap_s: float = 1.0,
) -> object:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep = random.uniform(0, min(cap_s, base_s * (2 ** i)))
            slog(logging.WARNING, "retry", cid=cid, tenant=tenant, trial=trial,
                 op=op, attempt=i + 1, sleep_s=round(sleep, 3), err=str(exc))
            time.sleep(sleep)
    assert last is not None
    raise last


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 15.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def state(self) -> CircuitState:
        return self._state

    def allow(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._state is CircuitState.OPEN:
                if now - self._opened_at >= self.cooldown_s:
                    self._state = CircuitState.HALF_OPEN
                    self._probes_used = 0
                else:
                    raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is CircuitState.HALF_OPEN:
                if self._probes_used >= self.half_open_probes:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._probes_used += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._probes_used = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


@dataclass
class RedactionResult:
    text: str
    types: dict[str, int]
    pre_sha: str
    post_sha: str

    @property
    def hit(self) -> bool:
        return bool(self.types)


class AuditSink:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._lock = threading.Lock()

    def write(self, row: dict) -> None:
        with self._lock:
            self.rows.append(dict(row))


class PiiPipeline:
    """Detect → redact → audit. Never logs raw values."""

    def __init__(self, audit: AuditSink) -> None:
        self.audit = audit

    def redact(self, text: str) -> RedactionResult:
        pre = hashlib.sha256(text.encode()).hexdigest()
        types = {n: len(rx.findall(text)) for n, rx in
                 (("EMAIL", EMAIL_RE), ("SSN", SSN_RE),
                  ("PHONE", PHONE_RE), ("PAN", PAN_RE))}
        types = {k: v for k, v in types.items() if v}

        def tok(prefix: str, m: re.Match[str]) -> str:
            return f"[{prefix}_{hashlib.sha256(m.group(0).encode()).hexdigest()[:12]}]"

        out = EMAIL_RE.sub(lambda m: tok("EMAIL", m), text)
        out = SSN_RE.sub(lambda m: tok("SSN", m), out)
        out = PHONE_RE.sub(lambda m: tok("PHONE", m), out)
        out = PAN_RE.sub(lambda m: tok("PAN", m), out)
        return RedactionResult(out, types, pre, hashlib.sha256(out.encode()).hexdigest())

    def apply(self, text: str, **meta: str) -> RedactionResult:
        result = self.redact(text)
        self.audit.write({
            "type": "pii_decision", "ts": time.time(), **meta,
            "pre_sha": result.pre_sha, "post_sha": result.post_sha,
            "types": result.types,
            "action": "tokenize" if result.hit else "none",
            "detector": "regex",
        })
        return result


@dataclass
class Hint:
    text: str
    origin: str
    oracle_hash: str
    untrusted: bool
    actor: str


@dataclass
class LoopState:
    goal: str
    allowlist: frozenset[str]
    s_ref: frozenset[str]
    plan: list[str] = field(default_factory=list)
    past_steps: list[str] = field(default_factory=list)
    memory: list[Hint] = field(default_factory=list)
    turns: int = 0
    replans: int = 0
    action_counts: dict[str, int] = field(default_factory=dict)
    last_obs_hash: str = ""
    status: str = "running"


class FakeOracle:
    """Deterministic verifier. $0 model. Prefer this over any critic."""

    def __init__(self, pass_on_turn: int = 2) -> None:
        self.pass_on_turn = pass_on_turn

    def verdict(self, state: LoopState) -> tuple[bool, str]:
        logs = f"tests={'PASS' if state.turns >= self.pass_on_turn else 'FAIL'} turn={state.turns}"
        return state.turns >= self.pass_on_turn, logs


class FakeCritic:
    """Oracle-log critic. Raises TransientError to exercise the breaker."""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def reflect(self, logs: str) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransientError("critic_429")
        return f"hint: retry with a different tool; logs={logs[:80]}"


def action_hash(tool: str, args: str) -> str:
    return hashlib.sha256(f"{tool}|{args}".encode()).hexdigest()[:16]


def pep_allows(state: LoopState, tool: str) -> bool:
    return tool in state.allowlist and tool in state.s_ref


class AgentLoop:
    def __init__(self, oracle: FakeOracle, critic: FakeCritic, pii: PiiPipeline,
                 critic_breaker: CircuitBreaker, audit: AuditSink) -> None:
        self.oracle = oracle
        self.critic = critic
        self.pii = pii
        self.critic_breaker = critic_breaker
        self.audit = audit

    def run(self, *, goal: str, tenant: str, cid: str,
            allowlist: frozenset[str], s_ref: frozenset[str]) -> LoopState:
        trial = uuid.uuid4().hex[:12]
        state = LoopState(goal=goal, allowlist=allowlist, s_ref=s_ref,
                          plan=["lookup", "act"])
        slog(logging.INFO, "trial_start", cid=cid, tenant=tenant, trial=trial,
             max_turns=MAX_TURNS, max_replans=MAX_REPLANS)
        while state.status == "running":
            if state.turns >= MAX_TURNS:
                state.status = "refuse_max_turns"
                slog(logging.ERROR, "max_turns", cid=cid, tenant=tenant,
                     trial=trial, turn=state.turns)
                break
            state.turns += 1
            tool = state.plan[0] if state.plan else "lookup"
            if not pep_allows(state, tool):
                state.status = "refuse_pep"
                slog(logging.ERROR, "pep_block", cid=cid, tenant=tenant,
                     trial=trial, turn=state.turns, tool=tool)
                break
            key = action_hash(tool, f"trial={trial}")
            state.action_counts[key] = state.action_counts.get(key, 0) + 1
            n = state.action_counts[key]
            if n >= SAME_ACTION_HARD:
                state.status = "refuse_same_action"
                slog(logging.ERROR, "same_action_hard", cid=cid, tenant=tenant,
                     trial=trial, turn=state.turns, n=n)
                break
            if n >= SAME_ACTION_WARN:
                slog(logging.WARNING, "same_action_warn", cid=cid, tenant=tenant,
                     trial=trial, turn=state.turns, n=n)

            def _exec() -> str:
                # Idempotent: key = planner-stable node id + trial.
                return f"obs:{tool}:ok:{key}"

            obs = retry_with_jitter(_exec, cid=cid, tenant=tenant, trial=trial,
                                    op=f"tool:{tool}")
            state.past_steps.append(str(obs))
            ok, logs = self.oracle.verdict(state)
            self.audit.write({
                "type": "oracle_verdict", "ts": time.time(), "cid": cid,
                "tenant": tenant, "trial": trial, "ok": ok,
                "logs_sha": hashlib.sha256(logs.encode()).hexdigest(),
            })
            if ok:
                state.status = "pass"
                slog(logging.INFO, "oracle_pass", cid=cid, tenant=tenant,
                     trial=trial, turn=state.turns)
                break
            hint = self._critic_fallback(logs, cid=cid, tenant=tenant, trial=trial,
                                         turn=state.turns)
            if hint is None:
                state.status = "refuse_skip_critic"
                slog(logging.ERROR, "refuse_after_skip_critic", cid=cid,
                     tenant=tenant, trial=trial, turn=state.turns)
                break
            redacted = self.pii.apply(
                hint, cid=cid, tenant=tenant, trial=trial,
                origin="critic", actor="orchestrator",
            )
            if redacted.types.get("PAN"):
                state.status = "refuse_pii"
                slog(logging.ERROR, "pii_block_from_store", cid=cid,
                     tenant=tenant, trial=trial, turn=state.turns)
                break
            state.memory.append(Hint(
                text=redacted.text, origin="critic",
                oracle_hash=hashlib.sha256(logs.encode()).hexdigest()[:16],
                untrusted=True, actor="orchestrator",
            ))
            state.memory = state.memory[-MEMORY_CAP:]
            self.audit.write({
                "type": "lesson_write", "ts": time.time(), "cid": cid,
                "tenant": tenant, "trial": trial, "origin": "critic",
                "actor": "orchestrator", "untrusted": True,
                "oracle_hash": state.memory[-1].oracle_hash,
                "post_sha": redacted.post_sha,
            })
            state.replans += 1
            if state.replans > MAX_REPLANS:
                state.status = "refuse_max_replans"
                slog(logging.ERROR, "max_replans", cid=cid, tenant=tenant,
                     trial=trial, turn=state.turns, replans=state.replans)
                break
            # Frozen allowlist: replan must not add tools. Rotate to next node only.
            if len(state.plan) > 1:
                state.plan = state.plan[1:] + state.plan[:1]
        slog(logging.INFO, "trial_end", cid=cid, tenant=tenant, trial=trial,
             status=state.status, turns=state.turns, replans=state.replans,
             hints=len(state.memory), breaker=self.critic_breaker.state.value)
        return state

    def _critic_fallback(self, logs: str, *, cid: str, tenant: str, trial: str,
                         turn: int) -> str | None:
        """oracle critic → skip critic → caller refuses. Never ship on skip."""
        try:
            self.critic_breaker.allow()

            def _call() -> str:
                return self.critic.reflect(logs)

            text = str(retry_with_jitter(_call, cid=cid, tenant=tenant,
                                         trial=trial, op="critic"))
            self.critic_breaker.record_success()
            slog(logging.INFO, "critic_ok", cid=cid, tenant=tenant, trial=trial,
                 turn=turn)
            return text
        except (TransientError, PermanentError) as exc:
            self.critic_breaker.record_failure()
            slog(logging.WARNING, "critic_skip", cid=cid, tenant=tenant,
                 trial=trial, turn=turn, err=str(exc),
                 breaker=self.critic_breaker.state.value)
            return None


def main() -> None:
    audit = AuditSink()
    pii = PiiPipeline(audit)
    loop = AgentLoop(
        oracle=FakeOracle(pass_on_turn=2),
        critic=FakeCritic(fail_times=1),
        pii=pii,
        critic_breaker=CircuitBreaker("critic"),
        audit=audit,
    )
    cid = uuid.uuid4().hex
    state = loop.run(
        goal="resolve ticket", tenant="acme", cid=cid,
        allowlist=frozenset({"lookup", "act"}),
        s_ref=frozenset({"lookup", "act"}),
    )
    assert state.status == "pass", state.status
    assert state.turns == 2, state.turns
    assert any(r["type"] == "lesson_write" for r in audit.rows)
    assert any(r["type"] == "pii_decision" for r in audit.rows)
    refuse = AgentLoop(
        oracle=FakeOracle(pass_on_turn=99),
        critic=FakeCritic(fail_times=99),
        pii=pii,
        critic_breaker=CircuitBreaker("critic", failure_threshold=1, cooldown_s=60),
        audit=audit,
    ).run(
        goal="resolve ticket", tenant="acme", cid=cid,
        allowlist=frozenset({"lookup"}),
        s_ref=frozenset({"lookup"}),
    )
    assert refuse.status in {"refuse_skip_critic", "refuse_max_turns",
                             "refuse_same_action", "refuse_max_replans"}
    print(json.dumps({"pass_status": state.status, "refuse_status": refuse.status,
                      "audit_rows": len(audit.rows)}, indent=2))


if __name__ == "__main__":
    main()
```

Graceful degradation in that harness: critic 429 trips the breaker → `critic_skip` → `refuse_skip_critic` (deterministic refuse, not a guessed refund). Oracle pass never calls the critic (0 ms extra-hop tax). PEP ∩ \(S_{ref}\) blocks Type I tools. PAN in a hint is `block-from-store`. Optional PostgresSaver would snapshot `LoopState` per turn; optional Temporal would wrap `FakeOracle.verdict` / tool HTTP as Activities and keep hashes in Workflow history.

---

### 6. Architectural System Design Scenarios

#### Scenario A — Support agent WITH a test/oracle Reflexion loop

**Problem.** τ-style support (DB is the world): refunds, bookings, plan changes. Users get **one** try. Need reliability (`pass^k` in evals; here: **stop on a DB predicate**). A wrong refund is worse than a slow one. The team is proposing Self-Refine on the refund utterance (“customer seemed angry”) and storing that as a skill.

**Proposed architecture:**

```
  ┌─────────────┐   ┌─────────────────────────────────────────────────────┐
  │ IdP / PEP   │──▶│ CONTROL: goal contract (allowed tools, refund cap,  │
  │ JWT→tenant  │   │   spend). PlanGuard S_ref frozen from I + T only    │
  │ refund cap  │   │   max_turns=10 (or Claude maxTurns=8+maxBudgetUsd)  │
  │             │   │   max_replans=2  same_action_k on refund/ticket     │
  └─────────────┘   └──────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: plan-and-execute (Terra planner, Luna/Haiku    │
                    │   executor). Tool PEP + idempotency keys.            │
                    │ HARD ORACLE: DB goal-state match + policy assertions │
                    │ On FAIL only: Haiku critic reads SQL diff / pytest   │
                    │   (NOT the ticket body from the web)                 │
                    │ Episodic hint untrusted, cap 3 → replan              │
                    │ Else HITL interrupt() — plan waits in PostgresSaver  │
                    └──────────────────────────────────────────────────────┘
                    Dual-oracle from evals: hard DB match in CI;
                    soft rubric async OFF the SLO path
```

**Technology choices:** Reflexion-style verbal RL **because an oracle exists** (Huang: gains vanish without labels; Reflexion coding ablation: tests are the signal). Do **not** Self-Refine the refund decision with the same model. Do **not** store “customer seemed angry so refund” as a skill. Caps: Agents SDK `max_turns=10` or Claude `maxTurns=8` + `maxBudgetUsd`; Temporal Activity idempotency keys. Extra critic hop ≈ Haiku 10k in + 0.5k out ≈ **$0.0125/fail [inferred]** vs a wrong refund. Latency: critic **after** oracle fail, not on the happy path (p99 extra-hop tax **0 / 0 / 0 ms [inferred policy]**).

**Trade-off matrix:**

| Axis | **A1 Oracle Reflexion after fail + HITL at replan cap (recommended)** | **A2 Intrinsic Self-Refine on every refund (same model, no tests)** | **A3 No critic, one-shot plan-execute, HITL on any DB miss** |
| --- | --- | --- | --- |
| **Cost** | T★ **[inferred] ~$60–80 / 1k** with 1 Haiku hop on the fail fraction; happy path ≈ stack A **~$55–70** | Huang 3–5× calls with **negative** quality; you pay for correlated errors | Cheapest tokens; human minutes on every miss |
| **Latency** | User extra-hop tax **0 / 0 / 0 ms [inferred]** on pass; local oracle **20 / 80 / 250 ms**; critic sidecar off path | **+2,000 / +6,000 / +15,000 ms [inferred]** per refine hop ON path | One plan-execute; HITL is a **gap** the latency policy ignores |
| **Ops complexity** | Oracle contract + frozen \(S_{ref}\) + untrusted memory TTL | Lowest until the first injected “lesson” | HITL queue is the product |
| **Security posture** | Critic sees redacted oracle logs; PlanGuard freeze; tools never `put` skills | Reflection poisoning (Hidden in Memory **99.8%** write); eTAMP frustration **8×** on a failing loop | No memory write from the model; still need PEP on tools |
| **Scalability ceiling** | `max_replans=2` + `$` cap; critic breaker skip→refuse | Token bomb disguised as quality | Human hours |

**Decision.** **A1 wins.** An oracle exists, so Reflexion is licensed (HumanEval **80.1 → 91.0** *with tests*; **−8 pp** without). A2 is Huang’s loop on a money path. A3 is correct when you cannot afford a critic vendor as a subprocessor — then cap turns and HITL, still no ungrounded critic. Dual-oracle: hard DB match fail-closed in CI; soft tone rubric async.

#### Scenario B — Research agent WITHOUT a critic vs code-agent tests vs PRM

**Problem.** Three sibling products share one “agent platform” team: (1) open-ended market / literature research (no unique gold); (2) a repo coding agent with pytest; (3) a contest-math sidecar that already samples N completions. Leadership wants “one Reflexion critic for all three” and a PRM as the **stop** condition on code.

**Proposed architecture (recommended split — do not unify the critic):**

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ CONTROL (shared): max_turns, PlanGuard/CaMeL if tools can send/post,    │
  │   PII detect→redact→audit, PostgresSaver, $ budget                      │
  └──────────────┬─────────────────────────────┬────────────────────────────┘
                 │                             │
     ┌───────────▼──────────┐      ┌───────────▼──────────┐
     │ Research             │      │ Code                 │
     │ LLMCompiler DAG      │      │ Reflexion /          │
     │ parallel search      │      │ AlphaCodium          │
     │ NO Reflexion critic  │      │ generate→pytest→     │
     │ Optional Self-Refine │      │ reflect on LOGS      │
     │ M=1 for prose only   │      │ hidden tests gate    │
     │ Citation matcher $0  │      │ PRM optional RERANK  │
     │ stop; unmatched      │      │ of close patches,    │
     │ claims never → memory│      │ NEVER as stop        │
     └──────────────────────┘      └──────────────────────┘
                 │
     ┌───────────▼──────────┐
     │ Math (if sampling)   │
     │ Interpreter/sympy    │
     │ stop; PRM best-of-N  │
     │ for choice; Snell    │
     │ difficulty-route N   │
     │ R1/o1 effort + tests │
     └──────────────────────┘
```

**Technology choices:** Research: ReAct or LLMCompiler DAG; **no** Reflexion critic. Huang: intrinsic self-correction **hurts** reasoning. CRITIC without search **hurts** QA (ChatGPT HotpotQA F1 **46.1** vs CRITIC **52.9**). If you have search, that is CRITIC’s tool — fact-check against retrieved docs, then **stop**. Do not invent a second critic that re-reads the same poisonable web text into memory (eTAMP). Optional one Self-Refine pass **M=1** for prose (Anthropic evaluator-optimizer: style, not facts). Citation matcher (deterministic overlap / NLI, $0) is a high-signal evaluator even without a unique gold — CRITIC-style revise **only on unmatched claims**; hop cap = unmatched-claim budget. Code: unit-test loop (Reflexion / AlphaCodium / SWE-agent). HumanEval **80.1→91.0**; AlphaCodium pass@5 **19→44** (~100 LLM calls for pass@5); SWE-agent **12.47%** SWE-bench @ **$4**, successes median **$1.21 / 12 steps**. Do **not** use a PRM as the stop when pytest exists (DeepSeek abandoned PRMs as RL rewards for hacking; ProcessBench: PRMs miss Olympiad steps). **Do** use tests as ORM stop + optional PRM for choosing among failing-but-close patches. LATS/ToT: tiny puzzles / cheap eval only (LATS HumanEval **92.7**; ToT Game24 **74%**; ToT **$0.74/puzzle** 2023; LATS **101.7×** WebShop vs DAG).

**Trade-off matrix:**

| Axis | **B1 Split: research = no critic + citation oracle; code = test Reflexion; PRM = rerank only (recommended)** | **B2 One Reflexion critic for all three (web text in memory)** | **B3 PRM / LATS / ToT as the stop on code and research** |
| --- | --- | --- | --- |
| **Cost** | Research ≈ stack A **[inferred] $55–70/1k**; code = SWE median **$1.21** success / **$4** cap; PRM = N gens + forwards only where you already sample | Huang 3–5× on research **and** −quality; eTAMP persist | ToT **$740/1k** puzzles (2023 GPT-4); LATS **~100×** DAG wall-clock; Lightman N=**1860** |
| **Latency** | Research extra-hop **0 / 0 / 0 ms [inferred]**; code oracle **20 / 80 / 250 ms**; CRITIC n=1 only on unmatched claims | Every task pays **+2,000 / +6,000 / +15,000 ms [inferred]** critic ON path | ToT/LATS exponential LM calls; internalized math **120,000 / 180,000 / 300,000 ms [inferred]** class |
| **Ops complexity** | Three stop signals, one control plane | One critic service — looks simple | Value-model / tree-search ops; ProcessBench OOD |
| **Security posture** | No cross-session writes from web observations; critic on code sees **test logs only** | Memory poisoning **99.8%** write ASR; post-reflection tool escalation | Self-eval ToT is not a PEP; still need PlanGuard if tools exist |
| **Scalability ceiling** | DAG \(\gamma_{\max} \approx N\); `max_turns` tight on research | Token bomb + Store poison | Search budget; DeepSeek MCTS failed at RL scale |

**Decision.** **B1 wins.** The critic is licensed by the **oracle**, not by the platform team’s desire for one diagram. Research without an oracle: cap turns, price residual error, optional M=1 style refine, citation matcher if you can check claims. Code: tests stop, PRM optional rerank. Math: interpreter stop, PRM for choice, internalized effort as a product knob — still pytest/sympy for audit. B2 ships Huang + Hidden-in-Memory. B3 confuses rerank with stop (Lightman **78.2 vs 72.4** is a **best-of-1860** result, not a loop fuse). Design-review script: Q1 / Key Takeaways (four roles, oracle-gated critic, hop caps, untrusted memory).

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| **Intrinsic critic drops accuracy** | Same-model self-correction; no oracle | GSM8K 75.9→74.7; CRITIC w/o Tool −1.8; Reflexion no-tests 52 vs 60 | Attach critic only with tests/interpreter/search-with-citation; else skip |
| **Infinite replan / same-tool loop** | ReAct 47% repetitive; cookbook `while True`; `recursion_limit=1000` as “policy” | Token burn; GraphRecursionError; DeerFlow hashes | `max_turns=10`; `max_replans=2`; same_action warn 3 / hard 5; RemainingSteps → END |
| **Premature stop** | ReAct Movie Rec ~85% exit before 8 searches | Joiner “enough evidence” too early | DAG + joiner with evidence bar; hop cap is the floor not the policy |
| **Green tests, wrong program** | Reflexion FP; thin suite (EvalPlus 19.3% drop) | Hidden tests fail; PRM flags step | Prefer FN over FP (AlphaCodium); dual-oracle; tests > judge |
| **Poisoned lesson** | Critic read untrusted obs; Hidden in Memory 99.8%; eTAMP 32.5%; frustration 8× | Unexpected tool after retrieve; Store write from tool | origin tags; cap 3; regenerate from oracle logs; tools never `put`; PlanGuard freeze \(S_{ref}\) |
| **Post-reflection tool escalation** | Replan from raw observations adds tools | Type I tool not in \(S_{ref}\) | Frozen allowlist; Secure P-t-E one-tool-per-step; HITL for deltas |
| **Hidden CoT claimed as audit** | o-series summaries; `store=false` crash | HTTP 400 on unpaired `rs_`; no plan JSON | Visible plan + tool hashes + verifier verdict |
| **Critic on user p99** | Frontier evaluator-optimizer on the handler | User p99 ≈ critic p99 (**+15,000 ms [inferred]**) | Critic after oracle fail; 0 ms happy-path tax |
| **InMemorySaver in prod** | Lost HITL / lost replan state | Empty resume after bounce | PostgresSaver; Temporal CAN |
| **`include_in_history=True` on max-turns** | Fallback text becomes next-session memory | Error utterance retrieved as a lesson | `include_in_history=False` for error results |
| **Handoff skips input guardrails** | Only first Agents SDK agent is gated | Second agent sees ungated user text | Re-run guardrails per agent or new `Runner.run` |
| **Planner+joiner >50% wall time** | Sol-high on Movie Rec-shaped DAG | p50 **up** with no parallel gain | Profile planner share; cheap planner; don’t buy LATS (101.7×) |
| **Over-decomposition** | Always-max HTN; ToT/LATS default | Token multiplier; ADaPT point missed | As-needed depth; Voyager 4 then skip; SWE $4 raise is a weak lever |
| **Claude no budget** | Default `maxTurns`/`maxBudgetUsd` none | Open-ended “improve repo” unbounded | Always set both |
| **Reasoning item dropped** | Messages-only history | OpenAI 400 | `previous_response_id` or pair `rs_`+message |

---

## Key Takeaways

- A feedback loop is **four roles and two planes**. The harness owns hop caps; the model proposes. Fusing planner/executor/critic/verifier is the dominant failure.
- **No oracle, no critic.** Huang intrinsic self-correction *drops* GSM8K; CRITIC w/o Tool can go negative; Reflexion without tests **52% vs 60%**. Reflexion +11 pp HumanEval *with tests*.
- ReAct needs an external fuse (**47%** repetitive reasoning). Promote to plan-and-execute when steps are stable; to a DAG when parallel (LLMCompiler **3.7× / 6.7×**). ToT/LATS are puzzle/search, not a default (LATS **101.7×** vs DAG on WebShop).
- o1/R1 internalized “wait” does **not** replace `max_turns`, a durable DAG, or pytest. Hidden CoT is not a SOX tape. Pair `rs_…` or HTTP 400.
- Caps to copy unless measured otherwise: Agents SDK **10** turns; ADK LoopAgent **5**; Self-Refine **4**; CRITIC **3**; Voyager inner **4**; Reflexion memory **3**; `max_replans=2`; DeerFlow same_action **3/5**; Temporal **51,200** events. Do not raise LangGraph `recursion_limit` 25→1000 to “let it think.”
- Memory is untrusted: Hidden in Memory **99.8%** write ASR; eTAMP **32.5%**. PII is **detect → redact → audit** before persist. Who wrote a lesson is a WORM row (`origin`, `actor`, `oracle_hash`).
- PlanGuard/CaMeL: untrusted data must not change the tool set or the values that fill args. Skip critic → execute once → refuse/HITL. Verifier fail → prefer tests, never “looks good.”

---

## Interview Q&A

**Q1. What is a production feedback loop, in one minute?**  
I treat it as a control-plane state machine, not a smarter prompt. Four roles — planner, executor, critic, verifier — share a typed plan object. The harness owns `max_turns`, `max_replans`, `same_action_k`, and whether the critic fires. I attach a critic only when an oracle or high-signal evaluator exists. o1/R1 internalized reasoning is cheaper ops and worse audit; it still needs an external verifier and a hop cap.

**Q2. When do you refuse to attach a critic?**  
When the only signal is the same model saying “looks good.” Huang: GPT-3.5 GSM8K 75.9→74.7 after two intrinsic rounds (5 calls); GPT-4-Turbo 91.5→88.0 if you tell it it might be wrong; Llama-2-70B 62.0→36.5. CRITIC without tools went −1.8 on math and *worse* than baseline on toxicity. Reflexion without tests on the hardest 50 HumanEval-Rust was 52% vs 60%. Open-ended research: cap turns, optional M=1 style Self-Refine, no Reflexion buffer.

**Q3. ReAct vs plan-and-execute vs DAG — how do you choose?**  
ReAct is the default inner cycle and it will loop: 47% of Hotpot failures are repetitive reasoning, and LLMCompiler saw ~85% of Movie Rec examples exit before 8 searches. I promote to plan-and-execute when the step list is stable (`max_replans=2` on state — LangGraph will not cap it for you). I promote to a DAG when work is embarrassingly parallel: LLMCompiler up to 3.7× latency and 6.7× cost vs ReAct. I profile planner share; if planner+joiner are more than half of wall time, a bigger planner model makes p50 worse.

**Q4. Give me `$ per 1k` for 0 vs 1 vs N critique rounds.**  
I state T★: Terra planner+executor, 4 tool rounds, pytest at $0, 70% cache hit on follow-up inputs. Zero critique is about $55–70/1k inferred. One Haiku critic round is about $60–80. Three Haiku critique/refine rounds is about $75–100 — context growth dominates. Sol with +2.5k thinking tokens per call is $200–350 and I will not put it on DAG-shaped tool parallelism. A useless Huang critic is 3–5× input tokens with negative quality. Extra hop on an oracle fail is about $0.0125 at Haiku 10k/500 inferred — cheaper than a wrong refund.

**Q5. What p50/p95/p99 do you put on extra hops?**  
Nobody publishes production percentiles of +1 critique hop. I contract 0/0/0 ms extra-hop tax on the happy path by skipping the critic unless the oracle failed. Local pytest is 20/80/250 ms inferred. If someone inlines a Haiku-class critic I treat 2,000/6,000/15,000 ms as the inferred policy (p50 anchored on the published 1,880 ms planner-class hop) and at p99 I skip the critic. Two CRITIC math corrections are ~2× PoT, so 4,000/12,000/30,000 ms inferred on-path — I do not stack n=3 on the SLO. o-series high effort is a several-minutes class: 120,000/180,000/300,000 ms inferred, background mode.

**Q6. Walk closed → open → half-open for this loop.**  
Independent breakers: critic API, tool fleet, same_action_k, max_replans, verifier disagreement. Identical (tool, args) warn at 3 and hard-stop at 5 by stripping tool_calls — that is DeerFlow’s encoding of Reflexion’s AlfWorld heuristic, not a LangGraph default. max_replans=2 goes to HITL, not a fourth model call. Critic 429s trip the critic breaker; fallback is skip critic then deterministic refuse, not a guessed refund. Tests fail and judge pass: I prefer tests and I open the judge breaker. Frustration is a security event — eTAMP ASR went 8× under garbled tools.

**Q7. PII on reflections — detect → redact → audit.**  
Before Store or checkpoint write, and before the next critic call: regex + NER on plan JSON, critic text, and observations about to be summarized. Redact to stable tokens so refund amounts and assertion names survive. The critic sees already-redacted oracle logs, not the ticket body. Audit WORM of decisions — pre/post hashes, entity types, counts — plus who wrote the lesson: actor=orchestrator, origin=critic, oracle_hash, untrusted=true. If NER is down I fail closed on memory writes and critic egress; I still serve the user with skip-critic. Tools never put skills.

**Q8. Why doesn’t o1 replace my harness?**  
o1 AIME 74% pass@1 still gains from cons@64 at 83% and rerank-1000 at 93%. R1-Zero grew reflective words 5–7× and “wait” after ~8k RL steps — that is search inside one forward pass. External replan is required when tools fail, policy forbids the next call, I need a durable DAG across crashes, or I must show a visible plan. Encrypted reasoning with store=false means I cannot checkpoint mid-thought. Pairing rs_ with the next message is load-bearing or I get HTTP 400. I still cap turns.

**Q9. PRM vs unit tests as the loop stopper.**  
Tests are the ORM stop when they exist. Lightman PRM 78.2 vs ORM 72.4 vs majority 69.6 at best-of-1860 is a rerank result; the gap widens with N so PRMs monetize test-time compute, they do not replace pytest. ProcessBench: GSM8K-looking PRMs fail on Omni-MATH (Math-Shepherd 47.9→23.8). DeepSeek dropped neural PRMs as RL rewards for hacking. Uesato: process supervision cuts trace error 14.0→3.4 among answers that were already finally-correct. AlphaCodium: prefer false negatives over false-positive generated tests.

**Q10. Design the support agent vs the research agent.**  
Support: plan-and-execute, DB oracle, Reflexion on SQL/pytest logs only, untrusted hints cap 3, max_replans=2, HITL, PlanGuard freeze. Research: DAG parallel search, no Reflexion critic, optional M=1 Self-Refine for prose, citation matcher on unmatched claims, no cross-session writes from web text. Unifying those critics is how you buy Huang plus Hidden-in-Memory 99.8% write ASR.

**Q11. LangGraph recursion_limit 1000 — ship it?**  
No. Since 1.0.6 the default is 1000 super-steps, about 500 ReAct cycles. The SDK schema still documents 25. That default is “hope the model stops,” not a policy. I set RemainingSteps to route to END inside the graph so the checkpoint is a completed run, plus ModelCallLimitMiddleware / ToolCallLimitMiddleware, plus max_replans on state. GraphRecursionError is the reactive fuse and leaves a failed checkpoint. I never put recursion_limit inside configurable — that is a silent no-op.

**Q12. Zero-Trust around the loop — failure mode?**  
Post-reflection tool escalation and memory poisoning. InjecAgent ReAct GPT-4 was 23.6%/47% ASR. PlanGuard Stage I alone has 27–38% FPR so I run Stage II; ASR 0% is structural because the isolated planner never saw poison. CaMeL is −7 pp utility for capability tags on values. Secure plan-then-execute: one tool per step, ephemeral executor. Replan that feeds observations back into the isolated planner destroys CFI — I freeze S_ref or HITL-extend it. A failing loop is an 8× injection amplifier, not just a quality miss.

---

## Key Numbers to Memorize

### Roles / caps / units
| Number | What |
| --- | --- |
| **4 roles** | Planner, executor, critic, verifier — do not fuse |
| **10 / None** | Agents SDK default `max_turns` / disabled |
| **1000 / 25** | LangGraph `recursion_limit` ≥1.0.6 default / SDK schema still 25 |
| **2 super-steps** | Typical ReAct model+tool cycle |
| **None / None** | Claude `maxTurns` / `maxBudgetUsd` defaults |
| **5 / 10** | ADK LoopAgent example `max_iterations` |
| **4 / 3 / 4** | Self-Refine k; CRITIC n QA; CRITIC n toxicity / Voyager inner |
| **3 / 4 / 12** | Reflexion memory size; WebShop trial cut; AlfWorld trials |
| **2–3** | Production `max_replans` on state (not built-in) |
| **3 / 5 / 20** | DeerFlow identical (tool,args) warn / hard / window |
| **30 / 50** | DeerFlow tool-*type* warn / hard |
| **10,240 / 51,200 / 50 MB** | Temporal warn / terminate events / bytes |
| **100–1000** | Continue-As-New every N iterations |
| **255** | `thread_id` column length |

### Quality / papers
| Number | What |
| --- | --- |
| **75.9→74.7 / 91.5→88.0 / 62.0→36.5** | Huang GSM8K GPT-3.5 / GPT-4-Turbo / Llama-2-70B intrinsic |
| **3 / 5 calls vs 1** | Huang round-1 / round-2 vs standard prompting |
| **−1.8 / 0.353 vs 0.344** | CRITIC w/o Tool GSM8K PoT; toxicity worse than baseline |
| **52% vs 60%** | Reflexion hardest 50 HumanEval-Rust **without** tests |
| **80.1→91.0 / 130/134** | Reflexion HumanEval-PY vs GPT-4; AlfWorld |
| **+11 pp not free** | Each failed trial = generate + tests + reflection |
| **52.9 vs 46.1 / 50.2** | CRITIC vs w/o Tool vs ReAct ChatGPT HotpotQA F1 |
| **~20% abs; 5–40% / k≤4 / 9 gens** | Self-Refine avg / per-task / cap / worst-case generations |
| **27.4 / 47% / 56% / 23%** | ReAct Hotpot EM; repetitive reasoning; CoT hallucination; search-result error |
| **3.7× / 6.7× / 101.7× / ~85%** | LLMCompiler vs ReAct latency/cost; vs LATS WebShop; Movie Rec premature stop |
| **1,880 / 1,620 / 1,130 vs 610 ms** | Movie Rec planner / answer / slowest vs mean search |
| **78.2 / 72.4 / 69.6 / 1860** | Lightman PRM / ORM / majority / best-of-N |
| **14.0%→3.4%** | Uesato trace error among final-answer-correct |
| **47.9→23.8 / 87.9** | ProcessBench Math-Shepherd GSM8K→Omni-MATH; o1-mini mean F1 |
| **>4× / ~14×** | Snell vs naive best-of-N; small+test-time vs larger greedy |
| **74% / 83% / 93%** | o1 AIME pass@1 / cons@64 / rerank-1000 |
| **15.6→77.9 / 79.8 vs 79.2 / 8,793** | R1-Zero AIME; R1 vs o1-1217; R1 thinking tokens/problem |
| **5–7× / ~8k RL steps** | R1 reflective words / “wait” spike |
| **92.7 / 75.9 / 74% / $0.74** | LATS HumanEval; LATS WebShop; ToT Game24; ToT $/case 2023 |
| **19%→44% / ~100 calls** | AlphaCodium CodeContests pass@5 |
| **12.47% / $4 / $1.21 vs $2.52 / 64%** | SWE-agent resolved; cap; success vs fail $; ACI relative |
| **+28.3 / +27 / +33 pp** | ADaPT vs ReAct/PS (ALFWorld / WebShop / TextCraft) |
| **99.7% vs 16.2%** | Least-to-Most SCAN vs CoT, 14 exemplars |

### $ / SKUs / dates
| Number | What |
| --- | --- |
| **$4/$20 / $2/$12 / $0.20/$1.20** | Sol / Terra / Luna in/out per 1M; cache 0.10× |
| **$1/$5 / $2/$10 / $5/$25** | Haiku 4.5 / Sonnet 5 / Opus 5 |
| **10% / 1.25× / 2×** | Anthropic cache hit / 5m write / 1h write |
| **2026-12-11 / 2026-11-21** | o3 shutdown; Sol promo end |
| **[inferred] ~$55–70 / ~$60–80 / ~$75–100** | T★ 0 / 1 / 3 Haiku critique rounds per 1k |
| **[inferred] ~$200–350** | T★ Sol +2.5k thinking out/call |
| **[inferred] $0.0125/fail** | Haiku 10k in + 0.5k out critic hop |
| **[inferred] ~$73 / 1k** | Worked stack A before tool HTTP |
| **$740 / 1k** | ToT Game of 24 at paper 2023 GPT-4 prices |
| **Do not quote $1.24 vs $5.12** | Unbound secondary roundup, not a named study |

### Latency / security (numeric ms)
| Number | What |
| --- | --- |
| **0 / 0 / 0 ms** | **[inferred policy]** happy-path extra-hop tax if critic skipped |
| **20 / 80 / 250 ms** | **[inferred]** local pytest/DB/compiler p50/p95/p99 |
| **2,000 / 6,000 / 15,000 ms** | **[inferred]** one Haiku-class critic hop ON user path (p50 ~ published 1,880 ms planner hop) |
| **4,000 / 12,000 / 30,000 ms** | **[inferred]** two CRITIC math corrections ON path (published ≈2× PoT) |
| **3,500 / 6,500 / 12,000 ms** | **[inferred]** serial plan+answer residual (1,880+1,620 p50-class) |
| **120,000 / 180,000 / 300,000 ms** | **[inferred]** o-series “several minutes” class |
| **2,000 / 8,000 / 20,000 ms** | **[inferred]** ReAct extra tool cycle (2 super-steps) |
| **500 ms** | **[policy]** local-oracle timeout then fail-closed |
| **23.6% / 47.0% / >80%** | InjecAgent ReAct GPT-4 base / enhanced / Llama2-70B |
| **99.8% / 95% / 60–89%** | Hidden in Memory write ASR GPT-5.5 / Kimi-K2.6; agentic actions among retrievals |
| **32.5% / 8×** | eTAMP GPT-5-mini ASR / frustration amplifier |
| **0% ASR / 27–38% / 0.97–3.28% FPR** | PlanGuard structural ASR; Stage-I-only FPR DH/DS; full-system FPR |
| **77% vs 84% / −7 pp** | CaMeL AgentDojo vs undefended |
| **72.8% → 0% / 1.49% FPR** | PlanGuard abstract InjecAgent quote |
| **detect → redact → audit** | PII on reflections/memory **before** persist and before next critic call |

---

*End of module. Practice the Q&A out loud; recode the breaker states and fallback chain from memory; recompute T★ `$ per 1k` on a whiteboard with 0 vs 1 vs 3 critique rounds and the happy-path 0 ms extra-hop tax.*
