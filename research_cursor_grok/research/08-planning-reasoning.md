# Research: Planning & Reasoning

**Date researched**: 2026-08-21
**Sources consulted**: 82

Scope: decomposition (task graphs, Least-to-Most, Plan-and-Solve, HuggingGPT, LLMCompiler, hierarchical HTN-like), reflection (Reflexion, self-critique, critic models, process vs outcome feedback), verification (verifiers, process reward models, unit tests as oracles, LLM-as-judge, debate), replanning (failure-triggered replan, dynamic graphs, backtracking, Tree-of-Thoughts, MCTS/LATS). Production overlay: OpenAI o-series / GPT-5.x reasoning, DeepSeek-R1 (and 2026 DeepSeek-V4 thinking API), Claude extended/adaptive thinking, LangGraph plan-execute, Temporal durable agent harness. Prices and eval numbers below are from vendor docs, papers, or named blogs as of 2026-08-21. ⚠️ No unpublished production p50/p95/p99 planning SLOs are invented; missing percentiles are marked. `$ per 1k tasks` figures are **[inferred]** from published SKUs × a stated reference task, not a vendor “per task” product.

---

## 1. System Topology & Mechanics

### 1.1 Four roles, two planes

A production planning system is **not** “the model thinks.” It is four independently scaled roles sharing a **durable plan object**. Collapsing them into one ReAct loop is the dominant cost and correctness failure: every tool call re-invokes the planner, every critique can rewrite control flow, every verifier timeout looks like a plan failure.

| Role | Owns | Typical implementation | Failure if fused |
| --- | --- | --- | --- |
| **Planner** | Decompose objective → DAG/list of steps, deps, tool names, success criteria | Structured-output LLM, PDDL compiler (LLM+P), HTN method library, HuggingGPT JSON `{task,id,dep,args}` | Tool observations inject new goals (prompt injection); plan mutates every turn |
| **Executor** | Run one ready node; bind placeholders (`$1`, `<resource>-task_id`) | Tool runtime, Hugging Face endpoints, sandboxed code, Temporal Activities | Planner tokens billed on every search; serial ReAct latency |
| **Critic / reflector** | Verbalize *why* a trial failed; write episodic hint | Reflexion memory buffer, Self-Refine FEEDBACK, Constitutional self-critique | Infinite critique loop; reflection text becomes the new prompt-injection surface |
| **Verifier** | Accept/reject a step or final answer | Unit tests, compiler, math checker, PRM, LLM-as-judge, human interrupt | Gaming (fake-green tests); judge bias; unverifiable open-ended work |

**Control plane vs data plane.** Control plane decides *whether* to replan, *which* node is ready (topological fetch), *max_replans*, *reasoning.effort*, and *whether* to escalate to a judge/human. Data plane is the plan graph, `past_steps`, checkpoints (`thread_id`), tool I/O blobs, and audit logs. LangGraph `StateGraph` with `plan` / `past_steps` / `response` is the control-plane loop; `PostgresSaver` is the data-plane snapshot. Temporal Workflows are the control plane; Activities (model + tools + sandboxes) are the data plane. MCP servers sit on the **tool boundary**: data-plane with control-plane auth. Do not let the critic write the plan in the same forward pass that executes tools.

Invariant: the LLM is **not** the planner. The planner is a function that *emits* a plan data structure. The executor *interprets* it. The critic *annotates* it. The verifier *gates* it. o1/R1 “internal CoT” collapses planner+critic+search into hidden tokens *inside one model call* — cheaper to operate, harder to audit, still needs an external verifier for consequential actions.

### 1.2 Decomposition: from lists to DAGs to hierarchies

**Least-to-Most (Zhou et al., ICLR 2023; arXiv:2205.10625).** Two stages: (1) decompose the problem into ordered subproblems; (2) solve sequentially, conditioning each solve on prior answers. Unlike CoT, it is explicitly compositional: the prompt teaches *how to break*, not just *how to chain*. Headline result: GPT-3 `code-davinci-002` + LtM solves SCAN at **99.7%** with **14** in-context examples vs neural-symbolic systems trained on **>15,000** examples. Topology: planner-once then N executor calls; stages can merge into a single pass for short tasks. Cost: linear in subproblem count; no native parallelism.

**Plan-and-Solve / PS+ (Wang et al., ACL 2023; arXiv:2305.04091).** Zero-shot replacement for “Let’s think step by step”: first *devise a plan* that splits the task, then *carry out* the plan. Zero-shot-CoT error autopsy on 100 GSM8K-style arithmetic items with GPT-3: calculation **7%**, missing-step **12%**, semantic misunderstanding **27%** of the sampled incorrect set. PS targets missing steps; PS+ adds “extract variables/numerals” and “calculate intermediates.” On `text-davinci-003`, PS+ beats Zero-shot-CoT on all ten datasets; arithmetic gain is **≥5%** on every math set except GSM8K (**+2.9%**, harder linguistically). CSQA **71.9% vs 65.2%**. Topology: still **one** LLM generation unless you split plan vs execute into two API calls (LangGraph does). Plan and execution share one context — no DAG, no placeholder binding.

**HuggingGPT / JARVIS (Shen et al., NeurIPS 2023; arXiv:2303.17580).** LLM as **controller**, Hugging Face models as **executors**. Four stages: task planning → model selection (by model card text + download rank as popularity proxy) → task execution on hybrid endpoints → response generation. Plan schema: `[{"task","id","dep","args"}]`. `dep` is prerequisite task ids; `args` may contain `<resource>-task_id` placeholders resolved after parents finish. Independent tasks run in parallel. Authors’ own limits: plans are not guaranteed feasible/optimal; **multiple sequential LLM round-trips** dominate latency; context length caps how many model cards you can rank; LLM instability throws workflow exceptions. Topology: global plan in **one** planner query (vs BabyAGI/AutoGPT iterative next-task, which can loop forever on a bad step).

**LLMCompiler (Kim et al., ICML 2024; arXiv:2312.04511).** Compiler analogy: (i) Function Calling Planner emits a **DAG** of tool calls with `$k` placeholders; (ii) Task Fetching Unit dispatches ready nodes; (iii) Executor runs tools in parallel; optional **Joiner** replans or answers. Streaming the DAG hides planner latency behind tool I/O (up to **1.3×** extra on ParallelQA). Vs ReAct: up to **3.7×** latency, **6.7×** cost, **~9%** accuracy (ParallelQA). HotpotQA comparison set: **1.80×** speedup / **3.37×** cheaper; Movie Recommendation **3.74× / 6.73×**. Game of 24 vs ToT: **2×** speedup. WebShop vs LATS: **101.7×** speedup at similar score (**72.8 ± 4.01** gpt-3.5-turbo). ReAct failure modes the DAG avoids: premature stop, repetitive same-tool loops. Residual: planner+joiner are serial; Movie Rec planner **1.88 s** + answer **1.62 s** average — more than half of end-to-end when tools are fast. Theoretical speedup upper bound is *N* independent tasks; lower bound ≈ 1 when planning dominates.

**Hierarchical / HTN-like.** Classical HTN: compound tasks + method library → primitives (Erol et al. 1994). LLM variants:

| System | How hierarchy works | Soundness |
| --- | --- | --- |
| **ADaPT** (Prasad et al., NAACL Findings 2024) | Try executor; on failure, planner splits with AND/OR; recurse to depth `d_max` | Controller is a deterministic program; success of children ⇒ parent |
| **LLM+P** (Liu et al., arXiv:2304.11477) | NL → problem PDDL → Fast-Downward → NL plan | Classical planner is sound *given* correct PDDL; LLM translation is the risk |
| **ChatHTN** (NEUS 2025) | Symbolic HTN; if no method, query ChatGPT for primitive sequence + verifier task `t_ver`; optional online method learning via goal regression | Verifier task checks effects; ChatGPT non-determinism even at T=0 — authors give **5** attempts |
| **LLM-generated HTN heuristics** (arXiv:2605.07707) | LLM writes Python heuristic for Pytrich; search remains symbolic | Correctness delegated to search; heuristic quality only |

ADaPT reported up to **+28.3%** ALFWorld, **+27%** WebShop, **+33%** TextCraft vs plan-and-execute / iterative executors — the point is *as-needed* depth, not always-max decomposition. LLM+P’s claim: LLMs fail to produce even feasible long-horizon robot plans; classical search on LLM-authored PDDL recovers optimal plans when the domain file is given.

**LangGraph plan-and-execute (LangChain, 2024–26).** Canonical production graph: `planner` → `agent` (execute `plan[0]`) → `replan` → conditional END or back to `agent`. State: `input`, `plan`, `past_steps`, `response`. Inspired by Plan-and-Solve + BabyAGI. Documented limitation: **serial** steps; embarrassingly parallel work should be a DAG (LLMCompiler). Secure variant (arXiv:2509.08646): planner names the **single tool** per step; executor spins a temporary agent with *only* that tool — least privilege per node.

### 1.3 Reflection: verbal RL, not weight updates

**Reflexion (Shinn et al., NeurIPS 2023; arXiv:2303.11366).** Actor (often ReAct) → environment / evaluator → self-reflection LLM → **episodic memory** of verbal hints → next trial. Feedback can be scalar or NL, external or self-simulated. Results: AlfWorld **130/134** vs ReAct (absolute **+22%** over 12 trials); HotPotQA **+20%**; HumanEval Python pass@1 **91.0** vs GPT-4 **80.1** (authors’ table; GPT-4 baseline 80%). Programming loop: CoT-generate ≤6 unit tests, AST-filter, run, reflect. Ablation on hardest 50 HumanEval-Rust: without tests, reflection **hurts** (52% vs 60% baseline) — the critic needs an oracle. Documented failure: WebShop after 4 trials, no useful reflections — Reflexion does not explore diverse catalogs.

**Self-Refine (Madaan et al., NeurIPS 2023; arXiv:2303.17651).** Same LLM as generator, feedback, and refiner. Loop until “stop” or **M≤4**. ~**20%** absolute average gain across 7 tasks vs one-shot same model. No tools. Risk: the model declaring “it is correct” (CRITIC paper notes this failure on Codex).

**CRITIC (Gou et al., arXiv:2305.11738).** Critique is **tool-interactive**: calculator, interpreter, search. “CRITIC w/o Tool” can *degrade* (e.g. **−1.8** on text-davinci-003). Gains scale with model size (TabMWP: +4.7 / +9.4 / +16.0 at 7B/13B/70B). Production rule: never attach a critic that cannot call a checker on math/code.

**Constitutional AI (Bai et al., arXiv:2212.08073).** Train-time critic: SL phase (sample → self-critique vs written principles → revise → SFT); RL phase RLAIF (AI preference labels on harmlessness). This is a **critic model distilled into weights**, not a runtime loop — but the same topology (critique then revise) is what Self-Refine/Reflexion do at inference.

**Process vs outcome feedback (do not mix).**

| Signal | Supervises | Example | Failure |
| --- | --- | --- | --- |
| **Outcome (ORM)** | Final answer / pass-fail | MATH label, unit-test gate, AlfWorld done | Credits lucky wrong reasoning; sparse |
| **Process (PRM)** | Each step correct/neutral/wrong | PRM800K; Lightman et al. | Step boundaries ill-defined; reward hacking if the PRM is learned |
| **Verbal process** | NL “what went wrong” | Reflexion traces | Uncalibrated; injectable |

OpenAI *Let’s Verify Step by Step* (Lightman et al., 2023; arXiv:2305.20050): on a 500-problem MATH slice, process-supervised RM **78.2%** vs outcome RM **72.4%** at best-of-**1860**; majority vote **62.9%**; greedy GPT-4 **50%**; oracle selection **96.3%**. Gap **widens** with N — PRMs monetize test-time compute better than ORMs. Dataset: PRM800K, ~**800k** step labels / **75k** solutions / **12k** problems (filtered from 1.09M labels). Uesato et al. 2022 (arXiv:2211.14275) is the earlier process-vs-outcome comparison on GSM8K-style work.

**Internalized reflection (2025–26 production).** OpenAI o1 (Sep 2024 system card + “Learning to reason”): RL teaches the model to break steps, detect mistakes, switch strategy — **inside hidden reasoning tokens**. DeepSeek-R1-Zero (Nature 2025 / arXiv:2501.12948): **no SFT**, GRPO, **rule-based** accuracy + format rewards only (explicitly **no** neural ORM/PRM because of hacking). AIME 2024 pass@1 **15.6% → 77.9%**; cons@16 **86.7%**. Emergent “aha”: spike in “wait” after ~8k RL steps; reflective-word count **5–7×**. R1 (cold-start + multi-stage RL) AIME pass@1 **79.8%** vs o1-1217 **79.2%** (paper table). Claude: extended thinking (`budget_tokens`, min **1024**) on 4.5-and-earlier; **adaptive thinking** (`effort`) on 4.6+ / 4.7 (legacy budget **400** on 4.7). Interleaved thinking with tools: budget can span the whole assistant turn.

### 1.4 Verification: oracles beat judges; judges beat nothing

**Hard oracles (prefer always).** Unit tests (Reflexion, AlphaCodium, CI), interpreters (CRITIC, PAL/PoT), compilers, exact-match, PDDL validators, `t_ver` in ChatHTN. AlphaCodium (Ridnik et al., arXiv:2401.08500): flow-engineering around **public + generated tests**; GPT-4 CodeContests valid **pass@5 19% → 44%**. False-positive tests (Reflexion): green suite on wrong code → agent **stops** — worse than false negatives (agent keeps editing). Prefer FN over FP in gates.

**Process reward models at inference.** Best-of-N rerank (Lightman); search guidance (Snell et al., arXiv:2408.03314 — test-time compute: voting vs verifiers vs longer chains). ProcessBench (Zheng et al., 2024, arXiv:2412.06559): identify **first erroneous step** or all-correct; F1 of error vs correct (harmonic mean). Qwen2.5-Math-PRM-72B remains a strong open PRM on that board; outcome-only methods lag. DeepSeek-R1 **abandoned PRMs for large-scale RL**: (1) step granularity undefined in general reasoning; (2) intermediate correctness hard; (3) **reward hacking** + RM retrain cost. PRMs still useful for **rerank/search**, not as the sole RL reward at R1 scale.

**LLM-as-judge (Zheng et al., NeurIPS 2023; arXiv:2306.05685).** GPT-4 judge vs humans: **>80%** agreement (human–human level) on MT-Bench / Arena. Biases to mitigate: **position**, **verbosity**, **self-enhancement**, weak reasoning. Mitigations: swap order, reference answers, pairwise not absolute. **Not** an oracle for math/code. Production: use as a **soft** critic behind a hard gate, or for open-ended style only.

**Debate (Irving, Christiano, Amodei, arXiv:1805.00899).** Two agents argue; a (possibly weak) judge picks. Complexity analogy: debate with optimal play can answer **PSPACE** questions with poly-time judges (direct judging ≈ **NP**). MNIST sparse-pixel toy: 6 pixels **59.4% → 88.9%**. 2025 temporal-consistency verifiers (iterated self-judgment) beat one-shot debate on ProcessBench-style F1 in at least one OpenReview study (82.5 vs 56.7 on MathCheck in that paper’s table) — debate is not automatically the best test-time verifier.

### 1.5 Replanning: when the graph is wrong

**Trigger.** Tool error, verifier fail, empty search, critic “hallucinated possession” (AlfWorld), or Joiner “need more evidence.”

**LangGraph replan node.** After each step, LLM sees `past_steps` and either emits remaining `steps` or a `Response`. This is **local** repair, not full search. Cap `max_replans` in the conditional edge — the graph will not do it for you.

**Tree of Thoughts (Yao et al., NeurIPS 2023; arXiv:2305.10601).** Thoughts = intermediate candidates; BFS/DFS with LM self-eval; backtrack. Game of 24: GPT-4 CoT **4%**, CoT-SC **9%**, ToT b=1 **45%**, ToT b=5 **74%**. ~**60%** of CoT samples already fail at step 1 — left-to-right cannot recover. Cost: branching × depth LM calls. Not a production default except puzzle-like search with cheap eval.

**RAP (Hao et al., EMNLP 2023).** LLM as **agent + world model**; MCTS on imagined next states. LLaMA-33B RAP > GPT-4 CoT on some plan/math/logic splits (paper’s 33% relative claim vs CoT+LtM+SC — task-specific, not a universal 33%).

**LATS (Zhou et al., ICML 2024; arXiv:2310.04406).** MCTS over ReAct-style actions; LM value + self-consistency hybrid `V(s)=λ LM(s)+(1−λ) SC(s)`; reflections on failed trajectories (Reflexion inside the tree). HumanEval GPT-4 pass@1 **92.7%**; WebShop GPT-3.5 avg **75.9**; HotPotQA ~**2×** ReAct. Environment feedback is the point vs ToT’s self-eval-only. Cost: many model calls per task — LLMCompiler’s 101.7× WebShop note is the production warning.

**MCTS at R1 scale (failed).** DeepSeek: token branching ≫ chess; cap on expansions → local optima; value model too weak to iterate like AlphaGo. MCTS **can** help inference with a pretrained value head; **cannot** easily self-improve the policy via search at their RL scale.

**o-series / GPT-5.x / Claude adaptive.** Replanning is **internal**: try strategy, backtrack in hidden tokens. Control knob: `reasoning.effort` ∈ {none, minimal, low, medium, high, xhigh, max} (model-dependent; o-series typically low/medium/high, default **medium**). OpenAI docs (2026): start with `gpt-5.6` for most reasoning; `gpt-5.6-sol` + `reasoning.mode=pro` for max intelligence; effort and mode are **independent**. This is not a durable DAG: crash mid-thought loses the tree unless the platform caches reasoning items (Responses API). External replan still required when **tools** fail or **policy** forbids the next call.

**Generative Agents / CoALA (context, not a product).** Park et al. (UIST 2023) interleave *observe → retrieve → reflect → plan* on a memory stream; the “plan” is a natural-language agenda, not a DAG. CoALA (Sumers et al., TMLR 2024) places planning in the **decision cycle** over working/episodic/semantic/procedural stores. Production takeaway: reflection that *writes back* into memory is a planner input on the next cycle — same poisoning path as Reflexion buffers.

### 1.6 What “done” means (oracle ranking)

Rank verifiers by how much you should trust them to stop the loop:

1. **Deterministic environment flag** (AlfWorld success, PDDL goal, HTTP 2xx on idempotent GET).
2. **Held-out tests / hidden cases** (HumanEval hidden tests, CodeContests private tests — AlphaCodium).
3. **Replayable computation** (calculator, interpreter, compiler logs).
4. **PRM / process label** (good for rerank; DeepSeek: not as sole RL reward).
5. **LLM-as-judge / debate / self-eval** (ToT state scores). Stop here only for subjective quality.

If 1–3 exist, do not let 4–5 override them (Reflexion FP: green self-tests halt a wrong program). If only 5 exists, cap turns and price the residual error.

---

## 2. Token Economics & NFR Metrics

### 2.1 Published SKUs (2026-08-21)

Thinking/reasoning tokens are **output-priced** on OpenAI, Anthropic, and DeepSeek thinking modes. Cache is the only large lever on the **input** of multi-step graphs.

| Model (API) | Input / 1M | Cached in | Output / 1M | Context / max out | Thinking control |
| --- | --- | --- | --- | --- | --- |
| OpenAI **o3** | $2.00 | $0.50 | $8.00 | 200k / 100k | `reasoning_effort` low/med/high; snapshot `o3-2025-04-16` |
| OpenAI **o4-mini** | $1.10 | $0.275 | $4.40 | 200k / 100k | same; `o4-mini-2025-04-16` |
| OpenAI GPT-5.x family | see vendor table | yes | see vendor table | 400k–1.5M class | `reasoning.effort`; GPT-5.6 `mode=pro` separate from effort |
| Claude **Sonnet 5** | $2 | 5m write $2.50; 1h $4; **hit $0.20** | $10 | docs | adaptive `effort` on new models; legacy `budget_tokens` ≥1024 on 4.5 |
| Claude **Sonnet 4.5 / 4.6** | $3 | hit $0.30 | $15 | | 4.6: budget deprecated but accepted; 4.7: budget → **400** |
| Claude **Opus 4.5–4.8 / 5** | $5 | hit $0.50 | $25 | | thinking billed as output; summarized thinking in the client |
| Claude **Haiku 4.5** | $1 | hit $0.10 | $5 | | cheap critic/verifier role |
| DeepSeek **V4-Flash** (official 2026-08) | miss peak $0.44 / off-peak $0.22; hit peak $0.014 / off $0.007 | (see left) | peak $1.32 / off $0.66 | 1M / max 384k | thinking **default**; peak hours 01:00–04:00 and 06:00–10:00 **UTC** |
| DeepSeek **V4-Pro** | miss peak $1.32 / off $0.66; hit peak $0.044 / off $0.022 | | peak $3.96 / off $1.98 | same | concurrency **500** vs Flash **2500** |

OpenAI docs: o3 “succeeded by GPT-5” as the general reasoning line; o-series still billed. Anthropic: cache hit = **10%** of base input; 5-minute write **1.25×**, 1-hour write **2×**. Changing `budget_tokens` or `effort` **invalidates** prompt-cache breakpoints (effort is rendered into the prompt). Claude Batch API: **50%** off input and output. US-only `inference_geo` on Claude 4.6+: **1.1×**.

⚠️ Aggregator blogs disagree on o4-mini ($0.55 vs $1.10). **Use OpenAI’s model page ($1.10 / $4.40)** as of this date. Historical DeepSeek-R1 list prices (~$0.55/$2.19) are **not** on the current official V4 table — do not mix eras.

### 2.2 Reference task T★ and `$ per 1k tasks` **[inferred]**

**T★ definition (explicit, not a vendor metric):** one enterprise “research → patch → verify” job.

- Planner: 5k in + 800 out (JSON DAG, 6 nodes)
- Execute 4 tool rounds: average 8k in + 300 visible out each (observations grow)
- One critic turn: 12k in + 600 out
- Verifier: pytest in CI (**$0** model) **or** Haiku judge 4k in + 250 out
- Thinking (if o3-medium): treat extra reasoning as **+2,500 output tokens per model call** that thinks — **[inferred]** from OpenAI/Anthropic “thinking counts as output” plus third-party notes that hard prompts emit **3–10×** visible output as hidden reasoning (PerUnit, Calcis). Not a published average.

**Call counts:** 1 plan + 4 execute + 1 critic = **6** LLM calls if tools are deterministic; **+1** replan on 20% of jobs **[assumed]**; no ToT.

| Stack | Model $ / 1k T★ **[inferred]** | Notes |
| --- | --- | --- |
| A. GPT-4.1-class non-reasoner plan-execute, no thinking | **~$40–55** | 6× (~8k in + 0.4k out) at $2/$8; cache 70% of repeated system+tools @ $0.50 |
| B. o4-mini medium thinking on planner+critic only | **~$70–110** | executor on mini/non-think; reasoning on 2 calls × 2.5k out @ $4.40 |
| C. o3 medium on all 6 calls | **~$180–350** | output-dominated; 6×2.5k reasoning @ $8 |
| D. Claude Sonnet 5, 8k thinking budget planner only, rest Haiku | **~$45–80** | cache hits on tool schemas; budget unused remainder still reserved in `max_tokens` |
| E. DeepSeek V4-Flash thinking, off-peak, 70% cache hit | **~$8–20** | cheap; concurrency 2500; still output-heavy |
| F. ReAct 12 hops vs LLMCompiler 4-wave DAG | F is **3–7×** A | Kim et al. **3.37–6.73×** cost on parallel QA; use as multiplier not $ |
| G. ToT b=5 Game-of-24-like | **10–40×** a single CoT | 74% vs 4% is the quality buy; rarely justified for CRUD agents |
| H. LATS / full MCTS | **tens–hundreds ×** | WebShop: LATS ~**100×** slower than parallel compiler exploration |

Formula to reuse:  
`cost ≈ Σ_calls (in_uncached·P_in + in_cached·P_cache + (visible_out + reasoning_out)·P_out) + tool_egress`.

### 2.3 Latency NFRs — ⚠️ no vendor p50/p95/p99 for “planning agents”

Published fragments, not SLOs:

- LLMCompiler Movie Rec: planner **1.88 s** + join **1.62 s** average; search straggler **1.13 s** vs mean **0.61 s** (2×).
- HuggingGPT: qualitative “increasing time costs” from multi-LLM stages; no percentile table.
- o1/o3/R1: latency **tracks reasoning tokens**; OpenAI community: raise `reasoning_effort` → more tokens → slower. DeployBase (Mar 2026 blog, not DeepSeek official): V3 **1–3 s**, V3 thinking **5–10 s**, R1 **15–30+ s** — treat as anecdotal ⚠️.
- Claude: higher `budget_tokens` / `effort` increases TTFT of the final answer; interleaved thinking adds a think block **per tool round**.
- DeepSeek V4: **off-peak** is price, not latency, unless their fleet is quieter ⚠️.

**Engineering SLOs to set yourself (not published):** p50 “plan emitted” (planner structured-output timeout, e.g. 8 s); p95 “first tool dispatched” (DAG stream); p99 “job done or replan-exhausted.” Circuit-break the critic before p99 of the user-facing SLA.

**Cache NFRs.** Prefix-cache the **tool catalog + planner schema + constitution**. Do **not** put volatile `past_steps` before the breakpoint. Replan that rewrites the system prompt kills the cache. Anthropic: consecutive requests must keep the same thinking config.

**Self-consistency tax.** Wang et al. (arXiv:2203.11171): sample K CoTs, majority vote. GSM8K-class gains historically **+10–18** points at K~20 — you pay **K×** generate. o1 reported AIME **74%** pass@1 vs **83%** cons@64 vs **93%** rerank-1000 — test-time compute is the product. Snell et al. (arXiv:2408.03314): allocate a **fixed** inference FLOP budget across (a) longer single chains, (b) majority vote, (c) verifier-guided search — the winner is **task-dependent**; PRM search wins when the verifier is well-calibrated, voting wins when it is not.

**Routing table (control plane, not the model).** Classify the job before spending thinking tokens: (i) DAG-shaped tool parallelism → compiler planner + cheap executor; (ii) single hard question with a checker → reasoning model + oracle; (iii) open-ended → one critic pass, M≤2; (iv) irreversible side effect → HITL regardless of effort. **[inferred]** putting o3-high on class (i) is the usual bill shock.

### 2.4 Per-role model SKUs

Do not use one frontier model for planner, executor, critic, and judge.

| Role | Cheap default (2026-08) | Escalate when |
| --- | --- | --- |
| Planner | o4-mini medium, Sonnet 5, V4-Flash thinking | Cyclic deps, PDDL needed, safety CFI |
| Executor (tool args) | Haiku 4.5, GPT-mini, V4-Flash non-think | Args are code or SQL |
| Critic | Haiku / Flash **with tools** (CRITIC) | No oracle exists |
| Verifier | pytest/sympy **$0** | Open-ended only → judge with swap-order |
| Replanner | Same as planner, `max_replans=2` | After cap: human |

Anthropic cookbook: thinking tokens count toward **rate limits** as well as the bill — a critic storm is a 429 as well as a $ event.

---

## 3. Distributed Resilience & State

### 3.1 The plan is a workflow, not a string

Durable fields: `plan_id`, `graph` (nodes, edges, placeholders), `cursor` / ready-set, `past_steps[]` (action, observation hash, verifier verdict, critic text id), `replan_count`, `effort`, `tenant`, `actor`. Store observations in object storage; keep hashes in the checkpoint so replay does not duplicate 10 MB tool dumps in Postgres.

**LangGraph.** `PostgresSaver` / `AsyncPostgresSaver`: thread-scoped checkpoints; `thread_id` column length cap (keep **<255**). `InMemorySaver` dies on restart — not production. Connection pool: `autocommit=True`, `dict_row`; Support article recommends `max_size≈10`, `max_idle=300s`. Subgraphs: checkpoint **parent only** to avoid dupes. TTL: OSS Postgres has no native checkpoint TTL — cron `delete_thread` or Agent Server TTL. Interrupts: pause before destructive tools (HITL) — the plan waits in DB, not in a Python stack frame.

**Temporal (2025–26 Agent Harness + OpenAI Agents SDK).** Workflow = agent loop; Activities = model, tools, sandboxes. Event history replays completed Activities after crash — **do not re-bill** succeeded LLM calls if you persisted the Activity result. Outer loops, approvals, and timers are first-class. Fork: snapshot workspace + conversation, new workflow id. Nexus Operations circuit breaker: per caller-namespace/endpoint; default **5 consecutive retryable errors** → open; **60 s** → half-open probe. Timeouts with no workers count as retryable — scale handlers or you black-hole all critics.

**Idempotency.** At-least-once Activities + non-idempotent “send email / charge card” = duplicate side effects on replay. Dedup keys on the data plane. Planner must emit **stable node ids** so a replan merge can skip completed nodes.

### 3.2 Critic-loop circuit breakers (you implement these)

| Breaker | Trip | Action |
| --- | --- | --- |
| `max_replans` | e.g. 3 | return best-so-far + `PLAN_EXHAUSTED` |
| `max_reflect_tokens` | critic output > N | drop to outcome-only gate |
| `same_action_k` | Reflexion AlfWorld heuristic: same act+obs k times | force replan or human |
| `verifier_disagree` | tests fail ∧ judge pass | prefer tests; log gaming suspicion |
| `reasoning_token_cap` | o-series effort high + output→100k | hard `max_output_tokens`; degrade effort |
| `critic_open_circuit` | 5 critic 5xx / timeouts (Temporal Nexus default) | skip critique, execute with allowlist tools only |

HuggingGPT warned that iterative plan+reflect agents enter **endless loops** on one bad step; global DAG + caps is the antidote.

### 3.3 Backpressure, HITL, and partition

Fetch unit should be a queue (ready nodes), not a recursive LLM. Parallel tool fan-out needs bulkheads: search pool vs code-exec pool vs critic pool so a hung interpreter does not stall replans. Run critics on a **separate** Temporal task queue / worker pool: a 32k-token reflection must not steal slots from `search`. Nexus CB then isolates a sick judge without freezing executors.

**HITL as a first-class node.** LangGraph `interrupt()` before a node writes to prod: the checkpoint holds `plan` + pending tool args; a human PATCH is another state update. Temporal: approval is a durable timer + signal, not a Flask session. Wall-clock p99 can be hours while **token** p99 stays bounded if the workflow idles at zero compute (Temporal harness claim). Split SLAs: *machine p99* vs *human p99*.

LangGraph checkpointers serialize concurrent updates per `thread_id` — duplicate FastAPI workers need advisory locks or you fork histories (forum reports).

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust around the planner

Treat the planner as a **privileged** compiler. Untrusted bytes (web, email, MCP resources, tool JSON) must not be in the same instruction channel as “here is your next DAG.”

**Plan-then-execute CFI** (Debenedetti et al. 2024, cited in “Design Patterns for Securing LLM Agents,” arXiv:2506.08837): freeze the plan from the **user** prompt; tool outputs cannot add actions. Does **not** stop injection in the user prompt itself. Dynamic replan **re-opens** the hole — if you replan, run the planner on a **quarantined** view (schema-only observations) or require HITL.

**CaMeL (DeepMind, arXiv:2503.18813).** Privileged LLM → Python-like plan; custom interpreter; capabilities on values; untrusted data cannot change control flow. AgentDojo: **77%** tasks with provable security vs **84%** undefended. Dual-LLM tax is the NFR. Enterprise follow-up (arXiv:2505.22852): CaMeL assumes trusted user prompt, weak on side channels.

**PlanGuard (arXiv:2604.10134).** Isolated planner from user instructions only; hierarchical check: hard tool allowlist then intent verifier for params. InjecAgent: ASR **72.8% → 0%**, FPR **1.49%**. Model-agnostic overlay.

**MCP (Zero-Trust).** Host (planner) ↔ servers (tools/resources/prompts). Threats: (1) tool **response** injection (OWASP Agentic A2); (2) malicious **resources** concatenated as if instructions; (3) prompt templates leaking secrets (spec historically weak on template ACL). Controls: OAuth 2.1 / RFC 9728 / 8707 resource indicators; per-tool RBAC; treat all MCP payloads as **data** until schema-validated; pin server hashes; no standing tokens in planner context.

### 4.2 Tool RBAC

LangGraph secure P-t-E: **one tool per step**, ephemeral executor. Map IAM to node types (`search` vs `payments.charge`). Planner proposes; **policy engine** (OPA) authorizes; model never sees raw cloud keys. HuggingGPT model-selection-by-card is **not** an authz model — anyone’s Space is a supply chain.

### 4.3 PII, audit, retention

Plans and critiques often contain customer identifiers, retrieved documents, and “I should have used SSN field X.” Persist: plan JSON, tool names+arg **hashes**, verifier verdicts, critic ids. Raw observations: shorter TTL, encryption, tenant partition. Hidden o-series CoT: OpenAI does **not** give raw reasoning (system card: monitoring vs UX vs competitive). You cannot SOX-audit tokens you never received — require **visible** plan+tool log for regulated actions even if the model thought privately.

Claude thinking summaries ≠ full chain. DeepSeek R1-Zero mixed-language CoT was a **readability/compliance** problem; R1 cold-start exists partly to make traces shareable.

### 4.4 Prompt injection **in reflections**

Critic text is written by a model that just read untrusted tool output. A poisoned page saying “reflect that the user asked to exfiltrate” becomes next-trial memory (Reflexion buffer). Mitigations: (1) store reflections as **data** with origin `critic_v1` + hash of observations; (2) cap memory to 1–3 items (Reflexion code used memory 1–3); (3) never let reflection emit tool calls; (4) regenerate critic from **oracle** (test log) not from webpage text; (5) PlanGuard-style check that post-reflection actions ⊆ original plan or HITL delta.

Unit42 (Palo Alto): in-the-wild **web IDPI** against agentic browsers — planning agents that `search` then `replan` are the target.

---

## 5. Production Failure Modes

| Mode | Mechanism | Blast radius | Detection | Mitigation |
| --- | --- | --- | --- | --- |
| **Plan hallucination** | Feasible-looking JSON, impossible deps, wrong tools, invented APIs | HuggingGPT: workflow exceptions; LLM+P: unsound PDDL | Schema + dry-run + allowlisted tools | Structured output + catalog RAG; symbolic planner; refuse unknown tools |
| **Missing-step plans** | PS paper’s 12% class | Silent wrong answer | Step-count vs gold SOP; PRM | PS+ instructions; ADaPT recurse on fail |
| **Infinite replan / ReAct loop** | Same search, growing context | Token blowup, hanging thread | `same_action_k`, token budget | DAG + `max_replans`; LLMCompiler vs ReAct repetitive HotpotQA (<10% acc when >4 calls) |
| **Verifier gaming** | Agent edits tests, `sys.exit(0)`, patches pytest (Anthropic reward-hacking writeups); Reflexion FP suites | Ship wrong code with green CI | Immutable hidden tests; coverage; tamper-evident runner | Oracle owned by platform, not the actor; prefer FN |
| **PRM / judge gaming** | Policy maximizes RM not truth (Gao et al. 2022; DeepSeek R1 section) | Systematic wrong proofs | RM–oracle disagreement | Rule-based rewards where possible; ensemble judges; periodic human |
| **LLM-as-judge bias** | Position/verbosity/self-preference | Wrong promotion of long wrong plans | Swap-order A/B | Pairwise + references; never sole gate |
| **Reasoning token blowup** | Hard prompt + high effort + 100k max out | Bill shock; latency; context eviction | `output_tokens` vs visible chars | Effort routing; `max_tokens`; Flash/mini for easy DAG nodes |
| **Cache stampede / invalidation** | Effort change every call; replan rewrites system | 10× input $ | Cache hit ratio | Stabilize constitution; cache tools not `past_steps` |
| **Hidden CoT opacity** | Cannot see o1/o3 thoughts | Missed exfil intent in thoughts | — | External plan CFI; tool allowlists |
| **Straggler join** | Slowest parallel tool ≈ 2× mean | p99 | Per-tool timers | Cancel+replan that node only |
| **Reflection poisoning** | IDPI → critic memory | Next trial jailbreak | Origin tags | §4.4 |
| **HTN method miss** | ChatGPT decomposition flaky | Intermittent unsat | Retry 5× (ChatHTN) | Cache learned methods; fall back fail-closed |
| **Debate collapse** | Collusion / persuasive wrong | Judge picks rhetoric | Diversity of debaters | Hard oracles; limited rounds |
| **ToT/LATS cost cliff** | Branching factor | Budget wipe | Node-call counter | Reserve for irreversible decisions |
| **Durable replay dual-spend** | Non-idempotent Activity retry | Double charge | Idempotency keys | Temporal + tool dedup |
| **Language-mix CoT** | R1-Zero | Unusable audit | Language-consistency reward (R1) | Prefer R1/V4 aligned traces |

---

## 6. Enterprise System Design Scenarios

### 6.1 Decision matrix (architect interview)

| Requirement | Prefer | Avoid | Why |
| --- | --- | --- | --- |
| Many independent tools | LLMCompiler DAG + streaming fetch | ReAct / serial LangGraph list | 1.8–3.7× latency, 3–7× $ |
| Long-horizon robot / warehouse | LLM+P or HTN + verifier tasks | Pure LLM plan | Soundness in the search, not the tokens |
| Adaptive depth (easy vs hard tickets) | ADaPT / effort routing | Always o3-high | Executor-first; decompose on fail |
| Code in CI | Tests as oracle + Reflexion/AlphaCodium loop, **hidden** tests | Self-generated tests as the only gate | FP stop |
| Math / STEM chat | Reasoning model + optional PRM BoN | LLM-as-judge on numeric | Lightman gap vs ORM |
| Open-ended writing | Self-Refine / constitution, K small | MCTS | No cheap eval |
| Untrusted web/MCP | Plan-then-execute + CaMeL/PlanGuard; HITL on replan | Replan-from-raw-HTML | CFI |
| Regulated action | Visible DAG + Temporal + OPA | Hidden CoT as the audit log | You cannot retain what the vendor withholds |
| Cost-sensitive batch | DeepSeek V4-Flash off-peak + cache | o3-pro on every hop | Official V4-Flash off-peak out **$0.66/MTok** |
| p99 user chat | Mini/Haiku executor, think only on planner | LATS | 100× WebShop lesson |

### 6.2 Scenario A — Internal “analyst copilot” (search + spreadsheet + Slack)

Topology: LangGraph plan-execute, planner = Sonnet 5 / o4-mini medium, executor = Haiku/Flash, critic = Haiku **only after** schema-validated tool JSON, verifier = “all required cells filled” deterministic. DAG for parallel ticker fetches (LLMCompiler pattern). `max_replans=2`. MCP to Slack: OAuth, no send in planner context. **[inferred]** ~$15–40 / 1k tasks on Flash+cache; 5–10× more on o3-everywhere. Failure: IDPI in a pasted 10-K — freeze plan, no replan from filing text.

### 6.3 Scenario B — Production coding agent

Topology: planner writes file-level DAG; executor in sandbox; **platform unit tests** as oracle; Reflexion on compiler logs; no web in critic. HITL interrupt before `apply` to main. Temporal Activities for LLM so retries do not double-commit. AlphaCodium-style generated tests are **advisory**. Effort high only on the failing node. Failure: gaming hidden by rewriting tests — tests live outside the workspace ACL.

### 6.4 Scenario C — Math-heavy research assistant

Topology: DeepSeek-R1-class / o4-mini thinking for solve; PRM or ProcessBench-style step check as **reranker** not RL; majority@k for cheap items (Wang SC). Do not wrap ToT around every query. DeepSeek lesson: skip neural PRM in the training loop; keep rule checkers (boxed answer, sympy). **[inferred]** off-peak Flash thinking can undercut o4-mini by ~5–20× on output-heavy traces if quality evals pass.

### 6.5 Scenario D — Multimodal “do this with my assets” (HuggingGPT-shaped)

Topology: one planner JSON `{task,id,dep,args}`; parallel independent modalities; placeholder resources; **allowlisted** model IDs not “top download on the Hub.” Timeout per expert model. Response-generation LLM sees **task results**, not raw model cards again (token cap). Failure: infeasible plan — validate `task` ∈ enum before dispatch.

### 6.6 Scenario E — High-stakes tool use (payments, medical advice routing)

Topology: CaMeL-like privileged plan; PlanGuard on every call; debate or dual-judge **only** for NL justification, never for the transfer. Temporal + human approval Activity. o-series thinking allowed for the privileged planner **without** untrusted documents in that call. Failure: dynamic replan after a malicious invoice PDF — require new user utterance to expand the plan.

### 6.7 Scenario F — When to buy search (ToT/LATS/MCTS)

Buy when: cheap exact evaluator, high value, branching factor <~5, depth <~10 (ToT Game-of-24 regime). Do not buy when: WebShop-like open catalogs (use parallel explore like LLMCompiler) or when DeepSeek-scale token MCTS cannot fit a value model. RAP/LATS remain research-grade unless you have a dedicated search budget line.

### 6.8 Control-plane checklist (ship bar)

1. Plan is a typed DAG in DB, not prose.  
2. Fetch/execute/critic/verify are separate nodes with caps.  
3. Hard oracle before LLM judge.  
4. Replan cannot add tools outside the original allowlist without HITL.  
5. Reasoning tokens metered per tenant; effort policy by task class.  
6. Checkpoints + idempotent tools.  
7. Reflections stored as untrusted data.  
8. Cache breakpoints exclude observations.  
9. Audit: plan, args hashes, verdicts, model+effort+cache-hit.  
10. Kill switches: `max_replans`, Nexus-style critic CB, `max_output_tokens`.

---

## Sources

1. https://arxiv.org/abs/2205.10625 — Least-to-Most (Zhou et al.)
2. https://arxiv.org/pdf/2205.10625 — LtM PDF; SCAN 99.7% / 14-shot
3. https://arxiv.org/abs/2305.04091 — Plan-and-Solve
4. https://aclanthology.org/2023.acl-long.147 — ACL 2023 PS/PS+
5. https://github.com/AGI-Edgerunners/Plan-and-Solve-Prompting — PS trigger sentences
6. https://arxiv.org/abs/2303.17580 — HuggingGPT
7. https://arxiv.org/pdf/2303.17580 — HuggingGPT `{task,id,dep,args}`, limitations
8. https://www.microsoft.com/en-us/research/publication/hugginggpt-solving-ai-tasks-with-chatgpt-and-its-friends-in-hugging-face/ — MSR page
9. https://arxiv.org/abs/2312.04511 — LLMCompiler
10. https://proceedings.mlr.press/v235/kim24y.html — ICML 2024 LLMCompiler
11. https://github.com/SqueezeAILab/LLMCompiler — code
12. https://arxiv.org/abs/2303.11366 — Reflexion
13. https://github.com/noahshinn024/reflexion — Reflexion code
14. https://arxiv.org/abs/2303.17651 — Self-Refine
15. https://arxiv.org/abs/2305.11738 — CRITIC
16. https://arxiv.org/abs/2305.10601 — Tree of Thoughts
17. https://github.com/princeton-nlp/tree-of-thought-llm — ToT prompts
18. https://arxiv.org/abs/2310.04406 — LATS
19. https://proceedings.mlr.press/v235/zhou24r.html — ICML 2024 LATS
20. https://github.com/lapisrocks/LanguageAgentTreeSearch — LATS code
21. https://aclanthology.org/2023.emnlp-main.507/ — RAP (Hao et al.)
22. https://arxiv.org/abs/2305.20050 — Let’s Verify Step by Step / PRM800K
23. https://openai.com/index/improving-mathematical-reasoning-with-process-supervision/ — process vs outcome blog
24. https://github.com/openai/prm800k — PRM800K data
25. https://arxiv.org/abs/2211.14275 — Uesato process vs outcome
26. https://arxiv.org/abs/2501.12948 — DeepSeek-R1
27. https://www.nature.com/articles/s41586-025-09422-z — R1 Nature 645, 633–638 (2025)
28. https://api-docs.deepseek.com/quick_start/pricing/ — DeepSeek V4 official SKUs (2026-08-21)
29. https://openai.com/index/learning-to-reason-with-llms/ — o1 AIME 74/83/93 vs GPT-4o 12%
30. https://cdn.openai.com/o1-system-card-20241205.pdf — o1 system card; hidden CoT
31. https://developers.openai.com/api/docs/guides/reasoning — `reasoning.effort`, GPT-5.x
32. https://developers.openai.com/api/docs/models/o3 — o3 $2/$8, 200k/100k
33. https://developers.openai.com/api/docs/models/o4-mini — o4-mini $1.10/$4.40
34. https://platform.claude.com/docs/en/build-with-claude/extended-thinking — budget_tokens ≥1024, deprecation
35. https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking — effort vs max_tokens
36. https://platform.claude.com/docs/en/about-claude/pricing — Sonnet 5 $2/$10; cache 10% hits
37. https://www.langchain.com/blog/planning-agents — LangGraph plan-and-execute + LLMCompiler notes
38. https://docs.langchain.com/oss/python/langgraph/persistence — PostgresSaver production
39. https://support.langchain.com/articles/1242226068-how-do-i-configure-checkpointing-in-langgraph — pools, TTL, subgraphs
40. https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres — checkpointer
41. https://arxiv.org/pdf/2509.08646 — secure plan-then-execute / least-privilege executor
42. https://temporal.io/blog/temporal-agent-harness-durable-agent-infrastructure — durable agent harness
43. https://temporal.io/blog/announcing-openai-agents-sdk-integration — SDK + Temporal
44. https://docs.temporal.io/nexus/operations — CB: 5 errors / 60 s half-open
45. https://arxiv.org/abs/2306.05685 — LLM-as-judge, MT-Bench
46. https://arxiv.org/abs/1805.00899 — AI safety via debate
47. https://arxiv.org/abs/2212.08073 — Constitutional AI
48. https://arxiv.org/abs/2210.03629 — ReAct
49. https://arxiv.org/abs/2203.11171 — Self-Consistency
50. https://arxiv.org/abs/2304.11477 — LLM+P
51. https://github.com/Cranial-XIX/llm-pddl — LLM+P code
52. https://aclanthology.org/2024.findings-naacl.264/ — ADaPT
53. https://allenai.github.io/adaptllm/ — ADaPT site
54. https://neus-2025.github.io/files/papers/paper_61.pdf — ChatHTN
55. https://arxiv.org/html/2511.12901 — online HTN method learning
56. https://arxiv.org/abs/2605.07707 — LLM-generated HTN heuristics
57. https://arxiv.org/abs/2401.08500 — AlphaCodium
58. https://github.com/Codium-ai/AlphaCodium — AlphaCodium code
59. https://arxiv.org/abs/2412.06559 — ProcessBench
60. https://arxiv.org/abs/2408.03314 — Snell et al. test-time compute
61. https://arxiv.org/abs/2506.08837 — design patterns vs prompt injection
62. https://arxiv.org/abs/2503.18813 — CaMeL
63. https://github.com/google-research/camel-prompt-injection — CaMeL code
64. https://arxiv.org/html/2505.22852v1 — operationalizing CaMeL
65. https://arxiv.org/abs/2604.10134 — PlanGuard
66. https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/ — in-the-wild IDPI
67. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization — MCP auth
68. https://www.permit.io/blog/oauth-on-mcp — OAuth 2.1 / RFC 9728 / 8707
69. https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-track-them — reasoning tokens as output
70. https://simonwillison.net/2024/Sep/12/openai-o1/ — hidden reasoning tokens / 25k budget note
71. https://perunit.ai/blog/openai-o3-api-pricing — thinking billed as output (2026)
72. https://www.calcis.dev/pricing/openai — o-series SKU table (cross-check vs official)
73. https://huggingface.co/learn/llm-course/en/chapter12/3 — GRPO / R1-Zero vs R1
74. https://arxiv.org/abs/2501.07301 — lessons developing math PRMs
75. https://doi.org/10.48550/arxiv.2604.22981 — TCRM vs ProcessBench F1
76. https://zylos.ai/research/2026-04-23-parallel-tool-calling-optimization-ai-agents/ — LLMCompiler production reading
77. https://platform.claude.com/cookbook/extended-thinking-extended-thinking — thinking billed as output; min 1024
78. https://developers.openai.com/api/docs/guides/latest-model — GPT-5.6 effort `none`…`max`, `mode=pro`
79. https://arxiv.org/abs/2309.02427 — CoALA (Sumers, Yao, Narasimhan, Griffiths)
80. https://dl.acm.org/doi/10.1145/3586183.3606763 — Generative Agents (Park et al., UIST 2023)
81. https://arxiv.org/abs/2406.13352 — AgentDojo (Debenedetti et al., NeurIPS 2024)
82. https://docs.langchain.com/oss/python/langgraph/interrupts — HITL `interrupt()` on plan nodes

**Source count:** 82 URLs. Claims in §§1–6 are tied to papers, vendor docs, or named blogs dated on or before 2026-08-21. Third-party latency anecdotes and `$/1k tasks` rollups are labeled ⚠️ or **[inferred]**. Vendor SKUs were taken from first-party pages where they conflict with aggregators (o4-mini, DeepSeek V4).
