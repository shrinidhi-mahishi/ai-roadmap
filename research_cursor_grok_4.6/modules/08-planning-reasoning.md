# Module 08 — Planning & Reasoning

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/08-planning-reasoning.md` (researched 2026-08-21, 82 sources).
**Mandatory topics**: Decomposition · Reflection · Verification · Replanning.

The unit of production is not “the model thinks.” It is four independently scaled **roles sharing a durable plan object**: **planner** (emit DAG), **executor** (run ready nodes), **critic** (verbalize why a trial failed), **verifier** (accept/reject). Collapsing them into one ReAct loop is the dominant cost and correctness failure: every tool call re-invokes the planner, every critique can rewrite control flow, every verifier timeout looks like a plan failure. Control plane decides *whether* to replan, *which* node is ready, `max_replans`, `reasoning.effort`, and whether to escalate. Data plane is the graph, `past_steps`, checkpoints, tool I/O blobs, and audit logs. Interview answers that skip this split fail when the follow-up is “who may add a tool after a web observation, and what stops the critic loop?”

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, policy, task-class routing, structured-output schema for the DAG, the replan fuse, critic/verifier circuit breakers, HITL interrupts, and `reasoning.effort`. Data plane owns topological fetch, placeholder binding (`$k`, `<resource>-task_id`), parallel tool I/O, sandbox execution, and oracle runners (pytest, compiler, sympy). Persistence is **two stores**: the **plan checkpoint** (typed graph + cursor + `replan_count` in Postgres/Temporal history) versus **observation blobs** (object storage; hashes only in the checkpoint). Tool proxies are MCP servers plus sandboxes; the model never holds IAM. Telemetry is the only place token usage, cache-hit ratio, breaker state, and verifier verdicts are authoritative.

Do not let the critic write the plan in the same forward pass that executes tools. o1/R1 internal CoT collapses planner+critic+search into hidden tokens *inside one model call* — cheaper to operate, harder to audit, still needs an **external** verifier for consequential actions.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (SSE chat / HITL resume / Batch / MCP host / CI webhook)               │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id + tenant token (never tool args)
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                                   │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ API Gateway│─▶│ Policy       │─▶│ Task router  │─▶│ Orchestrator           │ │
│  │ auth,quota │  │ PII detect→  │  │ DAG | reason │  │ LangGraph / Temporal   │ │
│  │ RPM/TPM    │  │ redact→audit │  │ | open-end | │  │ max_replans fuse       │ │
│  │ Retry-After│  │ tool RBAC    │  │ HITL-irrev.  │  │ effort / budget_tokens │ │
│  │ critic CB  │  │ CFI freeze   │  │              │  │ interrupt() before     │ │
│  └────────────┘  │ PlanGuard    │  └──────┬───────┘  │  destructive tools     │ │
│                  └──────┬───────┘         │          └──────────┬─────────────┘ │
│                         │                 │                     │               │
│  ┌──────────────────────┴─────────────────┴─────────────────────┴─────────────┐ │
│  │ FOUR ROLES (independently scaled; share plan_id)                           │ │
│  │  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────────────────┐ │ │
│  │  │ PLANNER  │──▶│ EXECUTOR │──▶│ CRITIC   │──▶│ VERIFIER GATE            │ │ │
│  │  │ JSON DAG │   │ 1 tool / │   │ Reflexion│   │ 1. env flag / tests      │ │ │
│  │  │ PDDL/HTN │   │  node;   │   │ buffer   │   │ 2. interpreter/compiler  │ │ │
│  │  │ allowlist│   │ bind $k  │   │ origin-  │   │ 3. PRM rerank (soft)     │ │ │
│  │  │ CFI      │   │ Temporal │   │  tagged  │   │ 4. LLM-judge (never      │ │ │
│  │  │          │   │ Activity │   │  data    │   │    overrides 1–3)        │ │ │
│  │  └──────────┘   └──────────┘   └──────────┘   └──────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────┬───────────────────────────┬───────────────────────────┘
                          │                           │
                          ▼                           ▼
┌─────────────────────────────────────────┐  ┌────────────────────────────────────┐
│ DATA PLANE                              │  │ TOOL PROXIES (Zero-Trust MCP)      │
│  ┌────────────┐  ┌────────────┐         │  │  Host=planner; servers=tools       │
│  │ Task Fetch │─▶│ Wave exec  │         │  │  OAuth 2.1 / RFC 9728 / 8707       │
│  │ ready-set  │  │ parallel   │         │  │  per-tool RBAC; pin server hash    │
│  │ Kahn/topo  │  │ bulkheads: │         │  │  payloads = DATA until schema-ok   │
│  └────────────┘  │ search ∥   │         │  │  ┌──────────┐  ┌────────────────┐  │
│                  │ code ∥     │         │  │  │ STS /    │─▶│ Sandbox        │  │
│  ┌────────────┐  │ critic     │         │  │  │ signed   │  │ code / HTTP /  │  │
│  │ Placeholder│  └────────────┘         │  │  │ scope    │  │ Slack MCP      │  │
│  │ $k /       │                         │  │  └──────────┘  │ JSON-encode    │  │
│  │ <res>-id   │                         │  │                │ observations   │  │
│  └────────────┘                         │  │                └────────────────┘  │
└─────────────────────┬───────────────────┘  └──────────────────┬─────────────────┘
                      │                                         │
                      ▼                                         │
┌─────────────────────────────────────────────────────────────────┴───────────────┐
│ PERSISTENCE                                                                     │
│  ┌────────────────────────────┐  ┌────────────────┐  ┌────────────────────────┐ │
│  │ Plan checkpoint (control)  │  │ Observation    │  │ Soft caches            │ │
│  │ plan_id, graph, cursor,    │  │ blobs (data)   │  │ tool catalog + schema  │ │
│  │ past_steps[], replan_count │  │ object store;  │  │ + constitution prefix  │ │
│  │ effort, tenant, actor      │  │ HASH in DB     │  │ NEVER past_steps       │ │
│  │ PostgresSaver thread_id    │  │                │  │ effort in cache key    │ │
│  │  <255; Temporal history    │  │                │  │                        │ │
│  └────────────────────────────┘  └────────────────┘  └────────────────────────┘ │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
┌─────────────────────────────────────┴───────────────────────────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────────────┐  │
│  │ Audit (WORM)│  │ Metrics      │  │ Traces      │  │ Usage (terminal SSE)  │  │
│  │ plan JSON,  │  │ plan-emit p50│  │ gateway →   │  │ thinking_tokens billed│  │
│  │ tool+arg    │  │ first-tool   │  │ planner →   │  │ as output; cache hit; │  │
│  │ hashes,     │  │ p95; job p99 │  │ fetch →     │  │ effort; replan_count  │  │
│  │ verdicts,   │  │ CB state;    │  │ tool →      │  │                       │  │
│  │ critic ids, │  │ same_action_k│  │ verify      │  │                       │  │
│  │ model+effort│  │              │  │             │  │                       │  │
│  └─────────────┘  └──────────────┘  └─────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Four roles, two planes

| Role | Owns | Typical implementation | Failure if fused |
| --- | --- | --- | --- |
| **Planner** | Objective → DAG/list: deps, tool names, success criteria | Structured-output LLM, PDDL compiler (LLM+P), HTN method library, HuggingGPT JSON `{task,id,dep,args}` | Tool observations inject new goals (prompt injection); plan mutates every turn |
| **Executor** | Run one ready node; bind placeholders | Tool runtime, HF endpoints, sandboxed code, Temporal Activities | Planner tokens billed on every search; serial ReAct latency |
| **Critic / reflector** | Verbalize *why* a trial failed; write episodic hint | Reflexion memory buffer, Self-Refine FEEDBACK, Constitutional self-critique | Infinite critique loop; reflection text becomes the new prompt-injection surface |
| **Verifier** | Accept/reject a step or final answer | Unit tests, compiler, math checker, PRM, LLM-as-judge, human interrupt | Gaming (fake-green tests); judge bias; unverifiable open-ended work |

**Control vs data.** LangGraph `StateGraph` with `plan` / `past_steps` / `response` is the control-plane loop; `PostgresSaver` is the data-plane snapshot. Temporal Workflows are the control plane; Activities (model + tools + sandboxes) are the data plane. MCP servers sit on the **tool boundary**: data-plane with control-plane auth.

**Invariant:** the LLM is **not** the planner. The planner is a function that *emits* a plan data structure. The executor *interprets* it. The critic *annotates* it. The verifier *gates* it.

### 1.3 End-to-end request flow

1. **Ingress.** Client opens SSE (interactive), sync HTTP, Batch, or a CI webhook. Gateway stamps correlation-id, authenticates, checks RPM/TPM. Critic-circuit and provider-circuit state are already routing inputs.
2. **Policy.** Detect→redact PII **before** any planner/critic call. Attach **only** the tools this task class may use. Freeze a **CFI allowlist** from the *user* prompt (plan-then-execute; Debenedetti / Design Patterns arXiv:2506.08837). Untrusted bytes (web, email, MCP resources) are not in the same instruction channel as “here is your next DAG.”
3. **Route (control plane, not the model).** (i) DAG-shaped tool parallelism → compiler planner + cheap executor; (ii) single hard question with a checker → reasoning model + oracle; (iii) open-ended → one critic pass, \(M \le 2\); (iv) irreversible side effect → HITL regardless of effort. Putting o3-high on class (i) is the usual bill shock **[inferred from SKU × hop count]**.
4. **Plan.** Planner emits a typed DAG (HuggingGPT `{task,id,dep,args}` or LLMCompiler `$k` placeholders) under structured output. Schema-validate; refuse unknown tools; dry-run cyclic-deps. One planner query for a global DAG beats BabyAGI/AutoGPT iterative next-task (can loop forever on a bad step).
5. **Fetch + execute (data plane).** Task Fetching Unit dispatches the ready-set (parents done). Independent nodes run in parallel behind bulkheads (search pool vs code-exec vs critic). Bind placeholders after parents finish. Each node is a Temporal Activity with an idempotency key. Stream the DAG so planner latency hides behind tool I/O (LLMCompiler: up to **1.3×** extra on ParallelQA from streaming).
6. **Verify (hard gate).** Rank: (1) deterministic env flag, (2) held-out tests, (3) replayable computation, (4) PRM, (5) LLM-as-judge. If 1–3 exist, **4–5 must not override** (Reflexion false-positive suites halt a wrong program). Prefer false negatives over false positives in gates.
7. **Critique (optional, bulkheaded).** On verifier fail or empty search: critic reads **quarantined** observations (schema-only, or oracle logs — not raw HTML). Write reflection as **data** with origin `critic_v1` + observation hash. Cap memory 1–3 items (Reflexion). Critic never emits tool calls.
8. **Replan or halt.** Local repair (LangGraph replan node: remaining `steps` or `Response`) under `max_replans` (ship default **2**). Dynamic replan **re-opens** CFI — planner sees schema-only observations, or HITL is required to expand the allowlist. After cap: return best-so-far + `PLAN_EXHAUSTED`.
9. **Persist and emit.** Checkpoint `plan` + `past_steps` hashes + verdicts (`thread_id` **<255**). Raw blobs in object storage. Audit: plan JSON, tool names + arg hashes, verifier verdicts, critic ids, model+effort+cache-hit. KV/prompt cache stays a soft prefix of catalog+schema+constitution — **not** `past_steps`.
10. **Degrade.** Critic circuit open (Temporal Nexus default: **5** consecutive retryable errors → open, **60 s** half-open): skip critique, execute allowlist tools only. Provider 5xx: fallback chain in §5. Never re-bill a succeeded Activity on Temporal replay.

**Interview talking point:** “The model is an untrusted compiler. The plan is a workflow. IAM, CFI, and oracles live outside the forward pass. Hidden CoT is not the audit log.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Decomposition — lists, DAGs, hierarchies

**Least-to-Most** (Zhou et al., ICLR 2023; arXiv:2205.10625). Two stages: (1) decompose into ordered subproblems; (2) solve sequentially, conditioning each solve on prior answers. Unlike CoT, the prompt teaches *how to break*, not just *how to chain*. Headline: GPT-3 `code-davinci-002` + LtM solves SCAN at **99.7%** with **14** in-context examples vs neural-symbolic systems trained on **>15,000** examples. Topology: planner-once then \(N\) executor calls. Cost: linear in subproblem count; **no native parallelism**.

**Plan-and-Solve / PS+** (Wang et al., ACL 2023; arXiv:2305.04091). Zero-shot replacement for “Let’s think step by step”: first *devise a plan*, then *carry it out*. Autopsy of 100 GSM8K-style errors on GPT-3: calculation **7%**, missing-step **12%**, semantic misunderstanding **27%** of the sampled incorrect set. PS targets missing steps; PS+ adds “extract variables/numerals” and “calculate intermediates.” On `text-davinci-003`, PS+ beats Zero-shot-CoT on all ten datasets; arithmetic **≥5%** on every math set except GSM8K (**+2.9%**). CSQA **71.9% vs 65.2%**. Topology: still **one** LLM generation unless you split plan vs execute (LangGraph does). No DAG, no placeholder binding — plan and execution share one context.

**HuggingGPT / JARVIS** (Shen et al., NeurIPS 2023; arXiv:2303.17580). LLM as **controller**, Hugging Face models as **executors**. Four stages: task planning → model selection (model-card text + download rank) → hybrid endpoint execution → response generation. Plan schema: `[{"task","id","dep","args"}]`. `dep` = prerequisite ids; `args` may contain `<resource>-task_id` placeholders. Independent tasks run in parallel. Authors’ limits: plans are not guaranteed feasible/optimal; **multiple sequential LLM round-trips** dominate latency; context length caps how many model cards you can rank; LLM instability throws workflow exceptions. Download-rank is **not** an authz model.

**LLMCompiler** (Kim et al., ICML 2024; arXiv:2312.04511). Compiler analogy: (i) Function Calling Planner emits a **DAG** of tool calls with `$k` placeholders; (ii) Task Fetching Unit dispatches ready nodes; (iii) Executor runs tools in parallel; optional **Joiner** replans or answers. Vs ReAct: up to **3.7×** latency, **6.7×** cost, **~9%** accuracy (ParallelQA). HotpotQA comparison set: **1.80×** speedup / **3.37×** cheaper; Movie Recommendation **3.74× / 6.73×**. Game of 24 vs ToT: **2×** speedup. WebShop vs LATS: **101.7×** speedup at similar score (**72.8 ± 4.01**, gpt-3.5-turbo). ReAct failure modes the DAG avoids: premature stop, repetitive same-tool loops. Residual: planner+joiner are serial; Movie Rec planner **1.88 s** + answer **1.62 s** average — more than half of end-to-end when tools are fast. Theoretical speedup upper bound is \(N\) independent tasks; lower bound \(\approx 1\) when planning dominates.

**Hierarchical / HTN-like.** Classical HTN: compound tasks + method library → primitives (Erol et al. 1994).

| System | How hierarchy works | Soundness |
| --- | --- | --- |
| **ADaPT** (Prasad et al., NAACL Findings 2024) | Try executor; on failure, planner splits with AND/OR; recurse to depth \(d_{\max}\) | Controller is a deterministic program; success of children ⇒ parent. Up to **+28.3%** ALFWorld, **+27%** WebShop, **+33%** TextCraft vs plan-and-execute / iterative executors — *as-needed* depth, not always-max |
| **LLM+P** (Liu et al., arXiv:2304.11477) | NL → problem PDDL → Fast-Downward → NL plan | Classical planner is sound *given* correct PDDL; LLM translation is the risk. LLMs fail to produce even feasible long-horizon robot plans without this |
| **ChatHTN** (NEUS 2025) | Symbolic HTN; if no method, query ChatGPT for primitive sequence + verifier task \(t_{ver}\); optional online method learning via goal regression | Verifier task checks effects; ChatGPT non-determinism even at T=0 — authors give **5** attempts |
| **LLM-generated HTN heuristics** (arXiv:2605.07707) | LLM writes Python heuristic for Pytrich; search remains symbolic | Correctness delegated to search; heuristic quality only |

**LangGraph plan-and-execute** (LangChain, 2024–26). Canonical graph: `planner` → `agent` (execute `plan[0]`) → `replan` → conditional END or back to `agent`. State: `input`, `plan`, `past_steps`, `response`. Inspired by Plan-and-Solve + BabyAGI. Documented limitation: **serial** steps; embarrassingly parallel work should be a DAG (LLMCompiler). Secure variant (arXiv:2509.08646): planner names the **single tool** per step; executor spins a temporary agent with *only* that tool — least privilege per node.

**Decomposition graph (production type).** Nodes \(V\), directed edges \(E\) = dependencies. Placeholders are data-flow edges. **Invariant:** \(G=(V,E)\) is a DAG (reject cycles at schema time). Ready-set \(R = \{v \mid \mathrm{pred}(v) \subseteq \mathrm{done}\}\). Makespan \(=\) longest path in wall-clock, not \(|V|\).

**Complexity.** Serial list: \(\Theta(N)\) sequential LLM+tool. DAG wave: \(\Theta(W)\) waves where \(W \le N\) is the longest chain; within a wave, fan-out \(\le |R|\). HuggingGPT/LLMCompiler planner is \(\Theta(1)\) LLM calls per (re)plan, plus joiner. ADaPT worst-case \(\Theta(b^{d_{\max}})\) splits; expected much less because split is on-fail.

### 2.2 Reflection — verbal RL, not weight updates

**Reflexion** (Shinn et al., NeurIPS 2023; arXiv:2303.11366). Actor (often ReAct) → environment / evaluator → self-reflection LLM → **episodic memory** of verbal hints → next trial. Feedback can be scalar or NL, external or self-simulated. Results: AlfWorld **130/134** vs ReAct (absolute **+22%** over 12 trials); HotPotQA **+20%**; HumanEval Python pass@1 **91.0** vs GPT-4 **80.1**. Programming loop: CoT-generate ≤6 unit tests, AST-filter, run, reflect. Ablation on hardest 50 HumanEval-Rust: without tests, reflection **hurts** (**52% vs 60%** baseline) — the critic needs an oracle. Documented failure: WebShop after 4 trials, no useful reflections — Reflexion does not explore diverse catalogs.

**Self-Refine** (Madaan et al., NeurIPS 2023; arXiv:2303.17651). Same LLM as generator, feedback, and refiner. Loop until “stop” or \(M \le 4\). ~**20%** absolute average gain across 7 tasks vs one-shot same model. No tools. Risk: the model declaring “it is correct” (CRITIC notes this on Codex).

**CRITIC** (Gou et al., arXiv:2305.11738). Critique is **tool-interactive**: calculator, interpreter, search. “CRITIC w/o Tool” can *degrade* (e.g. **−1.8** on text-davinci-003). Gains scale with model size (TabMWP: **+4.7 / +9.4 / +16.0** at 7B/13B/70B). Production rule: never attach a critic that cannot call a checker on math/code.

**Constitutional AI** (Bai et al., arXiv:2212.08073). Train-time critic: SL phase (sample → self-critique vs written principles → revise → SFT); RL phase RLAIF. This is a **critic distilled into weights**, not a runtime loop — but the same topology (critique then revise) is what Self-Refine/Reflexion do at inference.

**Process vs outcome — do not mix.**

| Signal | Supervises | Example | Failure |
| --- | --- | --- | --- |
| **Outcome (ORM)** | Final answer / pass-fail | MATH label, unit-test gate, AlfWorld done | Credits lucky wrong reasoning; sparse |
| **Process (PRM)** | Each step correct/neutral/wrong | PRM800K; Lightman et al. | Step boundaries ill-defined; reward hacking if the PRM is learned |
| **Verbal process** | NL “what went wrong” | Reflexion traces | Uncalibrated; injectable |

OpenAI *Let’s Verify Step by Step* (Lightman et al., 2023; arXiv:2305.20050): on a 500-problem MATH slice, process-supervised RM **78.2%** vs outcome RM **72.4%** at best-of-**1860**; majority vote **62.9%**; greedy GPT-4 **50%**; oracle selection **96.3%**. Gap **widens** with \(N\) — PRMs monetize test-time compute better than ORMs. Dataset: PRM800K, ~**800k** step labels / **75k** solutions / **12k** problems.

**Internalized reflection (2025–26 production).** OpenAI o1: RL teaches the model to break steps, detect mistakes, switch strategy — **inside hidden reasoning tokens**. DeepSeek-R1-Zero (Nature 2025 / arXiv:2501.12948): **no SFT**, GRPO, **rule-based** accuracy + format rewards only (explicitly **no** neural ORM/PRM because of hacking). AIME 2024 pass@1 **15.6% → 77.9%**; cons@16 **86.7%**. Emergent “aha”: spike in “wait” after ~8k RL steps; reflective-word count **5–7×**. R1 (cold-start + multi-stage RL) AIME pass@1 **79.8%** vs o1-1217 **79.2%**. Claude: extended thinking (`budget_tokens`, min **1024**) on 4.5-and-earlier; **adaptive thinking** (`effort`) on 4.6+ / 4.7 (legacy budget **400** on 4.7). Interleaved thinking with tools: budget can span the whole assistant turn.

**Reflection state machine.** `trial → observe → evaluate → reflect → write_memory → retry` with caps: `max_reflect_tokens`, memory **1–3**, `same_action_k` (AlfWorld heuristic: same act+obs \(k\) times → force replan or human). **Invariant:** critic output is untrusted data (`origin=critic_v1`, hash of observations); it cannot expand the tool allowlist.

**Complexity.** Reflexion: \(\Theta(T)\) full actor trials (\(T=12\) AlfWorld paper). Self-Refine: \(\le 4\) rewrite passes on the same context. Token cost is dominated by **replaying the trajectory into the critic**, not the hint length.

### 2.3 Verification — oracles beat judges; judges beat nothing

**Hard oracles (prefer always).** Unit tests (Reflexion, AlphaCodium, CI), interpreters (CRITIC, PAL/PoT), compilers, exact-match, PDDL validators, \(t_{ver}\) in ChatHTN. AlphaCodium (Ridnik et al., arXiv:2401.08500): flow-engineering around **public + generated tests**; GPT-4 CodeContests valid **pass@5 19% → 44%**. False-positive tests (Reflexion): green suite on wrong code → agent **stops** — worse than false negatives (agent keeps editing). Prefer FN over FP in gates. Generated tests are **advisory**; platform-owned hidden tests are the gate.

**Process reward models at inference.** Best-of-N rerank (Lightman); search guidance (Snell et al., arXiv:2408.03314 — test-time compute: voting vs verifiers vs longer chains; winner is **task-dependent**). ProcessBench (Zheng et al., 2024, arXiv:2412.06559): identify **first erroneous step** or all-correct. Qwen2.5-Math-PRM-72B remains a strong open PRM; outcome-only methods lag. DeepSeek-R1 **abandoned PRMs for large-scale RL**: (1) step granularity undefined in general reasoning; (2) intermediate correctness hard; (3) **reward hacking** + RM retrain cost. PRMs still useful for **rerank/search**, not as the sole RL reward at R1 scale.

**LLM-as-judge** (Zheng et al., NeurIPS 2023; arXiv:2306.05685). GPT-4 judge vs humans: **>80%** agreement (human–human level) on MT-Bench / Arena. Biases: **position**, **verbosity**, **self-enhancement**, weak reasoning. Mitigations: swap order, reference answers, pairwise not absolute. **Not** an oracle for math/code. Production: soft critic **behind** a hard gate, or open-ended style only.

**Debate** (Irving, Christiano, Amodei, arXiv:1805.00899). Two agents argue; a (possibly weak) judge picks. Complexity analogy: debate with optimal play can answer **PSPACE** questions with poly-time judges (direct judging ≈ **NP**). MNIST sparse-pixel toy: 6 pixels **59.4% → 88.9%**. 2025 temporal-consistency verifiers beat one-shot debate on ProcessBench-style F1 in at least one OpenReview study (**82.5 vs 56.7** on MathCheck in that paper’s table) — debate is not automatically the best test-time verifier.

**Oracle ranking (stop the loop):**

1. Deterministic environment flag (AlfWorld success, PDDL goal, HTTP 2xx on idempotent GET).
2. Held-out tests / hidden cases (HumanEval hidden tests, CodeContests private tests).
3. Replayable computation (calculator, interpreter, compiler logs).
4. PRM / process label (rerank; not sole RL reward).
5. LLM-as-judge / debate / self-eval (ToT state scores). Stop here only for subjective quality.

If only 5 exists, cap turns and **price the residual error**. `verifier_disagree` breaker: tests fail ∧ judge pass → **prefer tests**; log gaming suspicion.

**Verifier gate (state).** Per node: `UNCHECKED → PASS | FAIL | ERROR`. Job-level: all required nodes `PASS` ∧ success criteria met → `DONE`. FAIL on a hard oracle → critic (if circuit closed) then replan that node, not the whole graph, unless deps are stale.

### 2.4 Replanning — when the graph is wrong

**Trigger.** Tool error, verifier fail, empty search, critic “hallucinated possession” (AlfWorld), or Joiner “need more evidence.”

**LangGraph replan node.** After each step, LLM sees `past_steps` and either emits remaining `steps` or a `Response`. This is **local** repair, not full search. Cap `max_replans` in the **conditional edge** — the graph will not do it for you. Merge rule: **stable node ids**; skip completed nodes; do not re-execute succeeded Activities.

**Tree of Thoughts** (Yao et al., NeurIPS 2023; arXiv:2305.10601). Thoughts = intermediate candidates; BFS/DFS with LM self-eval; backtrack. Game of 24: GPT-4 CoT **4%**, CoT-SC **9%**, ToT \(b=1\) **45%**, ToT \(b=5\) **74%**. ~**60%** of CoT samples already fail at step 1 — left-to-right cannot recover. Cost: branching × depth LM calls. Not a production default except puzzle-like search with cheap eval.

**RAP** (Hao et al., EMNLP 2023). LLM as **agent + world model**; MCTS on imagined next states. LLaMA-33B RAP > GPT-4 CoT on some plan/math/logic splits (paper’s 33% relative claim vs CoT+LtM+SC — task-specific, not a universal 33%).

**LATS** (Zhou et al., ICML 2024; arXiv:2310.04406). MCTS over ReAct-style actions; LM value + self-consistency hybrid \(V(s)=\lambda\,\mathrm{LM}(s)+(1-\lambda)\,\mathrm{SC}(s)\); reflections on failed trajectories (Reflexion inside the tree). HumanEval GPT-4 pass@1 **92.7%**; WebShop GPT-3.5 avg **75.9**; HotPotQA ~**2×** ReAct. Environment feedback is the point vs ToT’s self-eval-only. Cost: many model calls per task — LLMCompiler’s **101.7×** WebShop note is the production warning.

**MCTS at R1 scale (failed).** DeepSeek: token branching ≫ chess; cap on expansions → local optima; value model too weak to iterate like AlphaGo. MCTS **can** help inference with a pretrained value head; **cannot** easily self-improve the policy via search at their RL scale.

**o-series / GPT-5.x / Claude adaptive.** Replanning is **internal**: try strategy, backtrack in hidden tokens. Control knob: `reasoning.effort` ∈ {none, minimal, low, medium, high, xhigh, max} (model-dependent; o-series typically low/medium/high, default **medium**). OpenAI docs (2026): start with `gpt-5.6` for most reasoning; `gpt-5.6-sol` + `reasoning.mode=pro` for max intelligence; effort and mode are **independent**. This is not a durable DAG: crash mid-thought loses the tree unless the platform caches reasoning items (Responses API). External replan still required when **tools** fail or **policy** forbids the next call.

**Generative Agents / CoALA.** Park et al. (UIST 2023) interleave *observe → retrieve → reflect → plan* on a memory stream; the “plan” is an NL agenda, not a DAG. CoALA (Sumers et al., TMLR 2024) places planning in the **decision cycle** over working/episodic/semantic/procedural stores. Production takeaway: reflection that *writes back* into memory is a planner input on the next cycle — same poisoning path as Reflexion buffers.

**Buy search (ToT/LATS/MCTS) when:** cheap exact evaluator, high value, branching factor \(<\sim 5\), depth \(<\sim 10\) (Game-of-24 regime). Do **not** buy when: WebShop-like open catalogs (use parallel explore like LLMCompiler) or when DeepSeek-scale token MCTS cannot fit a value model.

**Replan fuse (invariants).**

- `replan_count < max_replans` (default 2; research ship bar).
- New tools \(\subseteq\) original CFI allowlist unless HITL delta.
- Completed node ids are skipped (idempotent merge).
- `same_action_k` trips → human or `PLAN_EXHAUSTED`, not another identical search.
- Effort change **invalidates** Anthropic prompt-cache breakpoints (effort is rendered into the prompt) — stabilize constitution; cache tools not `past_steps`.

**Job-level state machine.**

```
IDLE ─▶ PLAN ─▶ WAVE_FETCH ─▶ EXECUTE ─▶ VERIFY ─┬─▶ WAVE_FETCH (ready-set nonempty)
                                                 ├─▶ CRITIC ─▶ REPLAN ─▶ PLAN   [if fuse allows]
                                                 ├─▶ HITL (irreversible / allowlist expand)
                                                 ├─▶ DONE
                                                 └─▶ EXHAUSTED (max_replans | same_action_k | CB)
```

---

## 3. Token Economics & NFR Analysis

Prices, rate limits, and latency fragments below are from vendor docs, papers, or named blogs as of **2026-08-21**. ⚠️ No unpublished production p50/p95/p99 **planning-agent** SLOs are invented; missing percentiles are marked. `$ per 1k tasks` figures are **[inferred]** from published SKUs × a stated reference task, not a vendor “per task” product.

Thinking/reasoning tokens are **output-priced** on OpenAI, Anthropic, and DeepSeek thinking modes. Cache is the only large lever on the **input** of multi-step graphs. Anthropic: cache hit = **10%** of base input; 5-minute write **1.25×**, 1-hour write **2×**. Changing `budget_tokens` or `effort` **invalidates** prompt-cache breakpoints. Claude Batch API: **50%** off input and output. US-only `inference_geo` on Claude 4.6+: **1.1×**. ⚠️ Aggregator blogs disagree on o4-mini ($0.55 vs $1.10) — **use OpenAI’s model page ($1.10 / $4.40)**. Historical DeepSeek-R1 list prices (~$0.55/$2.19) are **not** on the current official V4 table.

### 3.1 Cost per 1k runs

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
| D. Claude Sonnet 5, 8k thinking budget planner only, rest Haiku | **~$45–80** | cache hits on tool schemas; unused budget still reserved in `max_tokens` |
| E. DeepSeek V4-Flash thinking, off-peak, 70% cache hit | **~$8–20** | cheap; concurrency **2500**; still output-heavy. Official off-peak out **$0.66/MTok** |
| F. ReAct 12 hops vs LLMCompiler 4-wave DAG | F is **3–7×** A | Kim et al. **3.37–6.73×** cost on parallel QA; use as multiplier not $ |
| G. ToT \(b=5\) Game-of-24-like | **10–40×** a single CoT | 74% vs 4% is the quality buy; rarely justified for CRUD agents |
| H. LATS / full MCTS | **tens–hundreds ×** | WebShop: LATS ~**100×** slower than parallel compiler exploration |

**SKU anchors (2026-08-21):** o3 **$2 / $8** per 1M in/out (cached in **$0.50**); o4-mini **$1.10 / $4.40** (cached **$0.275**); Claude Sonnet 5 **$2 / $10** (hit **$0.20**); Sonnet 4.5/4.6 **$3 / $15** (hit **$0.30**); Opus 4.5–4.8/5 **$5 / $25** (hit **$0.50**); Haiku 4.5 **$1 / $5** (hit **$0.10**); DeepSeek V4-Flash miss peak **$0.44** / off **$0.22**, out peak **$1.32** / off **$0.66**; V4-Pro ~3× Flash, concurrency **500** vs Flash **2500**.

Formula: `cost ≈ Σ_calls (in_uncached·P_in + in_cached·P_cache + (visible_out + reasoning_out)·P_out) + tool_egress`.

**Per-role SKUs (do not use one frontier model for all four roles).**

| Role | Cheap default (2026-08) | Escalate when |
| --- | --- | --- |
| Planner | o4-mini medium, Sonnet 5, V4-Flash thinking | Cyclic deps, PDDL needed, safety CFI |
| Executor (tool args) | Haiku 4.5, GPT-mini, V4-Flash non-think | Args are code or SQL |
| Critic | Haiku / Flash **with tools** (CRITIC) | No oracle exists |
| Verifier | pytest/sympy **$0** | Open-ended only → judge with swap-order |
| Replanner | Same as planner, `max_replans=2` | After cap: human |

Anthropic cookbook: thinking tokens count toward **rate limits** as well as the bill — a critic storm is a 429 as well as a $ event. Analyst-copilot shape in research §6.2: **[inferred] ~$15–40 / 1k** on Flash+cache; **5–10×** more on o3-everywhere.

### 3.2 Latency SLA targets and mitigations

Published fragments, **not** SLOs:

- LLMCompiler Movie Rec: planner **1.88 s** + join **1.62 s** average; search straggler **1.13 s** vs mean **0.61 s** (**2×**).
- HuggingGPT: qualitative “increasing time costs” from multi-LLM stages; no percentile table.
- o1/o3/R1: latency **tracks reasoning tokens**; raise `reasoning_effort` → more tokens → slower. DeployBase (Mar 2026 blog, not DeepSeek official): V3 **1–3 s**, V3 thinking **5–10 s**, R1 **15–30+ s** — anecdotal ⚠️.
- Claude: higher `budget_tokens` / `effort` increases TTFT of the final answer; interleaved thinking adds a think block **per tool round**.
- DeepSeek V4: **off-peak is price, not latency**, unless their fleet is quieter ⚠️.

**Engineering SLOs to set yourself (not published).** Circuit-break the critic before p99 of the user-facing SLA. Split *machine p99* vs *human p99* (Temporal HITL can idle hours at zero compute).

| Percentile | Plan emitted | First tool dispatched | Job done or replan-exhausted | Mitigation |
| --- | --- | --- | --- | --- |
| **p50** | Structured-output planner; Movie Rec **1.88 s** class when tools are fast | DAG stream; hide planner behind first wave | Serial list ≈ sum of steps; DAG ≈ longest path + join **1.62 s** class | Compiler planner + cheap executor; prefix-cache catalog+schema |
| **p95** | ⚠️ unpublished; **[inferred]** 2–3× p50 if decode is stable, worse if effort=high | Straggler **~2×** mean (1.13 vs 0.61 s) | Replan + critic on the failing 20% **[assumed]** | Per-tool timers; cancel+replan **that node only**; skip critic on easy PASS |
| **p99** | ⚠️ unpublished; effort-high / 100k max out / cache miss / 429 | Hung interpreter stalling the join | Critic storm, LATS, HITL wait | Critic CB before user SLA; `max_output_tokens`; degrade effort; never LATS on the interactive fuse |

| Tier | Mitigations |
| --- | --- |
| p50 | Stream DAG; parallel ready-set; Haiku/Flash executor; pin constitution+tools in prompt cache |
| p95 | Bulkhead search vs code vs critic; per-tool deadline; joiner timeout; `max_replans=2` |
| p99 | Temporal Nexus-style critic CB (**5** failures / **60 s**); skip critique; allowlist-only execute; HITL off the machine SLA |

**Self-consistency tax.** Wang et al. (arXiv:2203.11171): sample \(K\) CoTs, majority vote. GSM8K-class gains historically **+10–18** points at \(K \sim 20\) — you pay **\(K\times\)** generate. o1 reported AIME **74%** pass@1 vs **83%** cons@64 vs **93%** rerank-1000. Snell et al.: allocate a **fixed** inference FLOP budget across (a) longer single chains, (b) majority vote, (c) verifier-guided search.

### 3.3 Throughput and back-pressure

Fetch unit is a **queue of ready nodes**, not a recursive LLM. Parallel tool fan-out needs bulkheads so a hung interpreter does not stall replans. Run critics on a **separate** Temporal task queue / worker pool: a 32k-token reflection must not steal slots from `search`. Nexus CB then isolates a sick judge without freezing executors.

**Back-pressure design:**

1. Gateway admits interactive traffic if **executor** breaker is closed/half-open **or** a degraded path exists (cached plan + allowlist tools, or `PLAN_EXHAUSTED` + best-so-far). Critic breaker does **not** shed tool execution.
2. Bulkhead: search pool vs code-exec pool vs critic pool vs planner pool. HuggingGPT warned that iterative plan+reflect agents enter **endless loops** on one bad step; global DAG + caps is the antidote.
3. Honor 429 with full jitter; thinking tokens count toward TPM — a high-effort critic storm is a quota event.
4. Shed order: drop ToT/LATS first, then critic, then PRM BoN, then parallel tools to serial, then best-so-far. Never shed CFI/RBAC. Never auto-expand allowlist when shed.
5. LangGraph checkpointers serialize concurrent updates per `thread_id` — duplicate FastAPI workers need advisory locks or you fork histories.
6. Batch / off-peak Flash for cost-sensitive graphs; interactive pool stays on a latency SKU.

**Worked capacity [inferred].** 10 T★/s continuous on stack A (~$50/1k) → **$0.50/s** ≈ **~$1.3M/mo** model spend before tools. Same QPS on stack C (~$250/1k) → **~$6.5M/mo**. Stack E (~$15/1k) → **~$390k/mo** but V4-Flash concurrency **2500** is a fuse (Pro **500**). ReAct 12-hop at 3–7× A does not fit a chat SLA; compiler 4-wave does.

### 3.4 Availability, RPO/RTO, compliance — explicit NFR trade-offs

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | 99.9% **gateway** with degrade: skip critic → allowlist execute → best-so-far + `PLAN_EXHAUSTED`. No vendor planning-agent SLO | Degraded ≠ “thought hard”; log `critic_skipped` / `plan_exhausted` as product metrics |
| RPO | Last checkpointed wave (`past_steps` + observation hashes). Temporal: completed Activities must not re-bill | Treating in-flight hidden CoT as durable violates RPO — o-series thoughts vanish unless Responses cache |
| RTO | Interactive: fail over **<1 s** to allowlist tools or cached remaining DAG. Graph rebuild = replan, not a 10-minute index | Fast failover vs a freshly reasoned DAG |
| Consistency | Plan is a typed DAG; merge by stable node ids. Checkpointer: one writer per `thread_id` | Serial LangGraph vs LLMCompiler parallelism; subgraph checkpoint **parent only** to avoid dupes |
| Compliance | Visible DAG + tool log for regulated actions; hidden CoT **cannot** be SOX-audited (OpenAI does not give raw reasoning). Claude summaries ≠ full chain. R1-Zero mixed-language CoT was a readability/compliance problem | Effort-high quality vs retainable trace |
| Cost vs latency | T★ **[inferred] $8–20** Flash off-peak vs **$180–350** o3-everywhere vs **3–7×** ReAct vs **~100×** LATS | Paying LATS for a DAG-shaped ticket |
| Cache vs freshness | Stable constitution+tools prefix vs replan that rewrites system / effort | Hit ratio vs adaptive thinking |
| Soundness vs tokens | LLM+P / HTN verifier tasks vs pure LLM plan | Classical search latency vs unsound long-horizon DAGs |

---

## 4. Distributed Resilience & Security

### 4.1 Durable plan (Temporal / Kafka)

The plan is a **workflow**, not a string. Durable fields: `plan_id`, `graph` (nodes, edges, placeholders), `cursor` / ready-set, `past_steps[]` (action, observation hash, verifier verdict, critic text id), `replan_count`, `effort`, `tenant`, `actor`. Store observations in object storage; keep hashes in the checkpoint so replay does not duplicate 10 MB tool dumps in Postgres.

**LangGraph.** `PostgresSaver` / `AsyncPostgresSaver`: thread-scoped checkpoints; `thread_id` column length cap (keep **<255**). `InMemorySaver` dies on restart — not production. Connection pool: `autocommit=True`, `dict_row`; Support article recommends `max_size≈10`, `max_idle=300s`. Subgraphs: checkpoint **parent only**. TTL: OSS Postgres has no native checkpoint TTL — cron `delete_thread` or Agent Server TTL. Interrupts: pause before destructive tools (HITL) — the plan waits in DB, not in a Python stack frame.

**Temporal (2025–26 Agent Harness + OpenAI Agents SDK).** Workflow = agent loop; Activities = model, tools, sandboxes. Event history replays completed Activities after crash — **do not re-bill** succeeded LLM calls if you persisted the Activity result. Outer loops, approvals, and timers are first-class. Fork: snapshot workspace + conversation, new workflow id. **Nexus Operations circuit breaker:** per caller-namespace/endpoint; default **5 consecutive retryable errors** → open; **60 s** → half-open probe. Timeouts with no workers count as retryable — scale handlers or you black-hole all critics.

**Kafka (log = chain of custody).** Topics per tenant-shard: `plan.created`, `node.executed`, `verify.verdict`, `replan.requested`, `plan.dlq`. Produce the **redacted** plan + arg hashes **before** tool side effects (outbox). Compact on `plan_id`. Poison (unparseable DAG, repeated handler crash on same `node_id`) → DLQ after \(N\); do not block the partition. HITL approval is a durable timer + signal, not a Flask session.

**Idempotency.** At-least-once Activities + non-idempotent “send email / charge card” = duplicate side effects on replay. Dedup keys on the data plane. Planner must emit **stable node ids** so a replan merge can skip completed nodes. Key: `hash(tenant, plan_id, node_id, canonical_args)`.

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | 429, 503, TLS reset, tool timeout, Nexus retryable, Temporal worker gap | Full-jitter retry on **idempotent** Activities; honor Retry-After; do not retry `payments.charge` without dedup |
| Permanent | Unknown tool, cyclic DAG, schema fail, 400 from OPA deny | Fail the node; do not replan the same illegal tool; HITL or `PLAN_EXHAUSTED` |
| Poison pill | Same `node_id` crashes the executor; HuggingGPT endless loop on one bad step; reflection that re-emits “exfiltrate” | `same_action_k`; `max_replans`; sha256 + \(N\) crashes → DLQ; critic origin tags |
| Semantic | Feasible-looking JSON, impossible deps, invented APIs; missing-step plans (PS **12%** class); FP unit tests; judge pass ∧ tests fail | Schema + allowlist + dry-run; PS+/ADaPT; **prefer tests**; hidden oracles |

**Mode → blast radius → mitigation (research §5):**

| Mode | Blast radius | Detection | Mitigation |
| --- | --- | --- | --- |
| Plan hallucination | Workflow exceptions; unsound PDDL | Schema + dry-run + allowlisted tools | Structured output + catalog RAG; symbolic planner; refuse unknown tools |
| Missing-step plans | Silent wrong answer | Step-count vs SOP; PRM | PS+ instructions; ADaPT recurse on fail |
| Infinite replan / ReAct loop | Token blowup, hanging thread | `same_action_k`, token budget | DAG + `max_replans`; LLMCompiler vs ReAct repetitive HotpotQA (<10% acc when >4 calls) |
| Verifier gaming | Ship wrong code with green CI | Immutable hidden tests; coverage; tamper-evident runner | Oracle owned by platform, not the actor; prefer FN |
| PRM / judge gaming | Systematic wrong proofs | RM–oracle disagreement | Rule-based rewards where possible; ensemble judges; periodic human |
| LLM-as-judge bias | Wrong promotion of long wrong plans | Swap-order A/B | Pairwise + references; never sole gate |
| Reasoning token blowup | Bill shock; latency; context eviction | `output_tokens` vs visible chars | Effort routing; `max_tokens`; Flash/mini for easy DAG nodes |
| Cache stampede / invalidation | 10× input $ | Cache hit ratio | Stabilize constitution; cache tools not `past_steps` |
| Hidden CoT opacity | Missed exfil intent in thoughts | — | External plan CFI; tool allowlists |
| Straggler join | p99 ≈ 2× mean | Per-tool timers | Cancel+replan that node only |
| Reflection poisoning | Next trial jailbreak | Origin tags | §4.4 |
| Durable replay dual-spend | Double charge | Idempotency keys | Temporal + tool dedup |
| ToT/LATS cost cliff | Budget wipe | Node-call counter | Reserve for irreversible decisions |

### 4.3 Circuit breaker and fallback chain

Per downstream (planner LLM, executor tools, critic, judge, MCP server):

- **Closed:** traffic flows; consecutive failures or error-rate window trips **open**.
- **Open:** fail fast; timer (Temporal Nexus **60 s**). Interactive traffic takes the next fallback; critic can wait on a queue.
- **Half-open:** one probe (or a small percentage). Success → closed; fail → open.

Research critic-loop breakers you implement (the graph will not):

| Breaker | Trip | Action |
| --- | --- | --- |
| `max_replans` | e.g. 3 (ship **2**) | return best-so-far + `PLAN_EXHAUSTED` |
| `max_reflect_tokens` | critic output > N | drop to outcome-only gate |
| `same_action_k` | same act+obs \(k\) times | force replan or human |
| `verifier_disagree` | tests fail ∧ judge pass | prefer tests; log gaming suspicion |
| `reasoning_token_cap` | o-series effort high + output→100k | hard `max_output_tokens`; degrade effort |
| `critic_open_circuit` | 5 critic 5xx / timeouts (Nexus default) | skip critique, execute with allowlist tools only |

```
CLOSED ──(failures ≥ 5 or error-rate)──▶ OPEN ──(60 s)──▶ HALF_OPEN
  ▲                                     │ fail fast              │
  │                                     │ fallback chain         ├── probe OK ──▶ CLOSED
  └─────────────────────────────────────┴────────────────────────┘ probe fail ──▶ OPEN
```

**Fallback chain (research order):**

1. **DAG compiler path** — LLMCompiler/HuggingGPT fetch + parallel allowlist tools + hard oracle.
2. **Serial plan-execute** — LangGraph list; still CFI-frozen; no critic if CB open.
3. **Best-so-far + `PLAN_EXHAUSTED`** — return completed nodes’ observations; do not invent remaining steps. HITL if the next action is irreversible.

Hedging: duplicate a straggler tool to a replica on p99; cancel loser. On planner failure: do **not** fall back to ReAct with raw tool JSON in the instruction channel.

### 4.4 Zero-Trust MCP, tool RBAC, PII, immutable logs

**Zero-Trust around the planner.** Treat the planner as a **privileged** compiler. Untrusted bytes must not share the instruction channel with the DAG.

- **Plan-then-execute CFI** (Debenedetti et al. 2024, cited in arXiv:2506.08837): freeze the plan from the **user** prompt; tool outputs cannot add actions. Does **not** stop injection in the user prompt itself. Dynamic replan **re-opens** the hole — if you replan, run the planner on a **quarantined** view (schema-only observations) or require HITL.
- **CaMeL** (DeepMind, arXiv:2503.18813): privileged LLM → Python-like plan; custom interpreter; capabilities on values; untrusted data cannot change control flow. AgentDojo: **77%** tasks with provable security vs **84%** undefended. Dual-LLM tax is the NFR. Follow-up (arXiv:2505.22852): assumes trusted user prompt, weak on side channels.
- **PlanGuard** (arXiv:2604.10134): isolated planner from user instructions only; hierarchical check: hard tool allowlist then intent verifier for params. InjecAgent: ASR **72.8% → 0%**, FPR **1.49%**. Model-agnostic overlay.

**MCP.** Host (planner) ↔ servers (tools/resources/prompts). Threats: (1) tool **response** injection (OWASP Agentic A2); (2) malicious **resources** concatenated as if instructions; (3) prompt templates leaking secrets. Controls: OAuth 2.1 / RFC 9728 / 8707 resource indicators; per-tool RBAC; treat all MCP payloads as **data** until schema-validated; pin server hashes; no standing tokens in planner context. Unit42 (Palo Alto): in-the-wild **web IDPI** against agentic browsers — planning agents that `search` then `replan` are the target.

**Tool RBAC.** LangGraph secure P-t-E: **one tool per step**, ephemeral executor. Map IAM to node types (`search` vs `payments.charge`). Planner proposes; **policy engine** (OPA) authorizes; model never sees raw cloud keys. HuggingGPT model-selection-by-card is **not** an authz model.

**PII pipeline:** detect → redact **before** planner/critic/embed → audit placeholders (never raw). Plans and critiques often contain customer identifiers, retrieved documents, and “I should have used SSN field X.” Persist: plan JSON, tool names+arg **hashes**, verifier verdicts, critic ids. Raw observations: shorter TTL, encryption, tenant partition.

**Prompt injection in reflections.** Critic text is written by a model that just read untrusted tool output. A poisoned page saying “reflect that the user asked to exfiltrate” becomes next-trial memory. Mitigations: (1) store reflections as **data** with origin `critic_v1` + hash of observations; (2) cap memory to 1–3 items; (3) never let reflection emit tool calls; (4) regenerate critic from **oracle** (test log) not from webpage text; (5) PlanGuard-style check that post-reflection actions \(\subseteq\) original plan or HITL delta.

**Audit / immutable logs.** Need: plan, args hashes, verdicts, model+effort+cache-hit, `replan_count`, correlation-id. Hidden o-series CoT: you cannot SOX-audit tokens you never received — require **visible** plan+tool log for regulated actions even if the model thought privately. Kafka log or WORM object store; **hash-chain** the audit events. Metrics: `plan_exhausted`, `critic_skipped`, `verifier_disagree`, entitlement violations.

---

## 5. Production Enterprise Code

Stdlib-only harness: full-jitter retries, circuit breaker (closed → open → half-open, Nexus defaults 5 / 60 s), fallback chain (DAG wave → serial list → best-so-far), correlation-id JSON logs, PII detect→redact→audit, CFI allowlist, plan DAG + topological fetch, `max_replans` fuse, hard-oracle verifier gate (judge cannot override), critic skip on open circuit, hash-chained audit. Run: `python planning_harness.py`.

```python
#!/usr/bin/env python3
"""Plan-execute-verify-replan harness (stdlib only). Run: python planning_harness.py"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

MAX_REPLANS = 2
SAME_ACTION_K = 3
CRITIC_FAILURE_THRESHOLD = 5
CRITIC_RECOVERY_S = 60.0
MAX_REFLECT_TOKENS = 400

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "plan_id": getattr(record, "plan_id", None),
            "breaker": getattr(record, "breaker", None),
            "degraded": getattr(record, "degraded", None),
            "replan_count": getattr(record, "replan_count", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)

class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs

def build_logger(correlation_id: str, tenant: str, plan_id: str) -> CorrelationAdapter:
    base = logging.getLogger("planning.harness")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base, {"correlation_id": correlation_id, "tenant": tenant, "plan_id": plan_id}
    )

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)

def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit

class TransientError(Exception):
    pass

class PermanentError(Exception):
    pass

class CircuitOpenError(Exception):
    pass

class PlanExhausted(Exception):
    pass

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = CRITIC_FAILURE_THRESHOLD,
        recovery_seconds: float = CRITIC_RECOVERY_S,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and (time.monotonic() - self._opened_at) >= self.recovery_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    def allow(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError("circuit open")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError("half-open probe in flight")
                self._half_open_inflight += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0

def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    base_seconds: float = 0.25,
    max_seconds: float = 8.0,
    retry_after: float | None = None,
) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            cap = min(max_seconds, base_seconds * (2**i))
            sleep_s = max(cap, retry_after or 0.0)
            time.sleep(random.random() * sleep_s)
    assert last is not None
    raise last

class JobState(Enum):
    IDLE = "idle"
    PLAN = "plan"
    WAVE_FETCH = "wave_fetch"
    EXECUTE = "execute"
    VERIFY = "verify"
    CRITIC = "critic"
    REPLAN = "replan"
    DONE = "done"
    EXHAUSTED = "exhausted"
    HITL = "hitl"

class NodeStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"

class Verdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    UNCHECKED = "unchecked"

@dataclass
class PlanNode:
    id: str
    tool: str
    args: dict[str, Any]
    deps: list[str]
    status: NodeStatus = NodeStatus.PENDING
    observation: str = ""
    observation_hash: str = ""
    verdict: Verdict = Verdict.UNCHECKED
    irreversible: bool = False

@dataclass
class PlanGraph:
    nodes: dict[str, PlanNode]
    allowlist: frozenset[str]
    replan_count: int = 0

    def ready_set(self) -> list[PlanNode]:
        ready: list[PlanNode] = []
        for node in self.nodes.values():
            if node.status is not NodeStatus.PENDING:
                continue
            preds = [self.nodes[d] for d in node.deps]
            if all(p.status is NodeStatus.DONE and p.verdict is Verdict.PASS for p in preds):
                node.status = NodeStatus.READY
                ready.append(node)
        return ready

    def bind_placeholders(self, node: PlanNode) -> dict[str, Any]:
        bound = json.loads(json.dumps(node.args))
        for key, val in list(bound.items()):
            if isinstance(val, str) and val.startswith("$"):
                src = self.nodes.get(val[1:])
                if src is None or src.status is not NodeStatus.DONE:
                    raise PermanentError(f"unbound placeholder {val}")
                bound[key] = src.observation
        return bound

    def merge_replan(self, new_nodes: list[PlanNode]) -> None:
        incoming = {n.id for n in new_nodes}
        for node in new_nodes:
            if node.tool not in self.allowlist:
                raise PermanentError(f"CFI deny {node.tool}")
            existing = self.nodes.get(node.id)
            if existing is not None and existing.status is NodeStatus.DONE:
                continue
            self.nodes[node.id] = node
        for node in self.nodes.values():
            if node.id not in incoming and node.status is NodeStatus.FAILED:
                node.status = NodeStatus.SKIPPED

    def best_so_far(self) -> dict[str, str]:
        return {n.id: n.observation for n in self.nodes.values() if n.status is NodeStatus.DONE}

    def all_pass(self) -> bool:
        active = [n for n in self.nodes.values() if n.status is not NodeStatus.SKIPPED]
        return bool(active) and all(
            n.status is NodeStatus.DONE and n.verdict is Verdict.PASS for n in active
        )

@dataclass
class AuditEvent:
    prev: str
    body: dict[str, Any]

    def digest(self) -> str:
        blob = json.dumps({"prev": self.prev, "body": self.body}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

class HashChainAudit:
    def __init__(self) -> None:
        self._head = "0" * 64
        self.events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def append(self, body: dict[str, Any]) -> str:
        with self._lock:
            ev = AuditEvent(self._head, body)
            self._head = ev.digest()
            self.events.append(ev)
            return self._head

class ToolProxy:
    def __init__(self, executors: dict[str, Callable[[dict[str, Any]], str]]) -> None:
        self._executors = executors
        self._done: dict[str, str] = {}
        self._lock = threading.Lock()

    def execute(
        self,
        *,
        tenant: str,
        plan_id: str,
        node: PlanNode,
        args: dict[str, Any],
        allowed: frozenset[str],
    ) -> str:
        if node.tool not in allowed:
            raise PermanentError(f"rbac deny {node.tool}")
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(f"{tenant}|{plan_id}|{node.id}|{canonical}".encode()).hexdigest()
        with self._lock:
            hit = self._done.get(key)
        if hit is not None:
            return hit
        raw = self._executors[node.tool](args)
        redacted, _pii = redact_pii(raw)
        with self._lock:
            self._done[key] = redacted
        return redacted

class HardOracle:
    """Platform-owned tests. Prefer FN over FP. Judge must not override FAIL."""

    def __init__(self, checks: dict[str, Callable[[str], bool]]) -> None:
        self.checks = checks

    def verify(self, node: PlanNode, observation: str) -> Verdict:
        check = self.checks.get(node.id) or self.checks.get(node.tool)
        if check is None:
            return Verdict.PASS if observation else Verdict.FAIL
        try:
            return Verdict.PASS if check(observation) else Verdict.FAIL
        except Exception:
            return Verdict.ERROR

def judge_soft(observation: str) -> Verdict:
    """LLM-as-judge stand-in. Never overrides a hard FAIL."""
    if "looks good" in observation.lower():
        return Verdict.PASS
    return Verdict.PASS if observation else Verdict.FAIL

class Critic:
    def __init__(self) -> None:
        self.memory: list[str] = []

    def reflect(self, node: PlanNode, oracle_log: str) -> str:
        redacted, _ = redact_pii(oracle_log)
        hint = f"origin=critic_v1 node={node.id} hash={node.observation_hash} log={redacted[:180]}"
        if len(hint) > MAX_REFLECT_TOKENS:
            hint = hint[:MAX_REFLECT_TOKENS]
        self.memory = (self.memory + [hint])[-3:]
        return hint

@dataclass
class ActionFingerprint:
    counts: dict[str, int] = field(default_factory=dict)

    def bump(self, tool: str, obs_hash: str) -> int:
        key = f"{tool}|{obs_hash}"
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

class PlanningHarness:
    def __init__(
        self,
        proxy: ToolProxy,
        oracle: HardOracle,
        critic: Critic,
        critic_cb: CircuitBreaker,
        audit: HashChainAudit,
        log: CorrelationAdapter,
        *,
        max_replans: int = MAX_REPLANS,
    ) -> None:
        self.proxy = proxy
        self.oracle = oracle
        self.critic = critic
        self.critic_cb = critic_cb
        self.audit = audit
        self.log = log
        self.max_replans = max_replans
        self.fingerprints = ActionFingerprint()
        self.state = JobState.IDLE
        self.degraded = False

    def _exec_node(self, graph: PlanGraph, node: PlanNode, tenant: str, plan_id: str) -> None:
        if node.irreversible:
            self.state = JobState.HITL
            raise PermanentError("HITL required before irreversible tool")
        args = graph.bind_placeholders(node)
        obs = retry_call(lambda: self.proxy.execute(
            tenant=tenant, plan_id=plan_id, node=node, args=args, allowed=graph.allowlist
        ))
        node.observation = obs
        node.observation_hash = hashlib.sha256(obs.encode()).hexdigest()[:16]
        if self.fingerprints.bump(node.tool, node.observation_hash) >= SAME_ACTION_K:
            raise PlanExhausted("same_action_k")
        self.state = JobState.VERIFY
        hard = self.oracle.verify(node, obs)
        soft = judge_soft(obs)
        if hard is Verdict.FAIL and soft is Verdict.PASS:
            self.log.warning("verifier_disagree prefer tests", extra={"degraded": self.degraded})
        node.verdict = hard
        node.status = NodeStatus.DONE if hard is Verdict.PASS else NodeStatus.FAILED
        self.audit.append(
            {
                "node": node.id,
                "tool": node.tool,
                "args_hash": hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16],
                "obs_hash": node.observation_hash,
                "verdict": hard.value,
            }
        )

    def _maybe_critic(self, node: PlanNode) -> str | None:
        self.state = JobState.CRITIC
        try:
            self.critic_cb.allow()
        except CircuitOpenError:
            self.degraded = True
            self.log.warning("critic skipped circuit open", extra={"breaker": self.critic_cb.state.value, "degraded": True})
            return None
        try:
            hint = self.critic.reflect(node, node.observation or "oracle_fail")
            self.critic_cb.record_success()
            return hint
        except TransientError:
            self.critic_cb.record_failure()
            self.degraded = True
            return None

    def run(self, graph: PlanGraph, tenant: str, plan_id: str, replan_fn: Callable[[PlanGraph], list[PlanNode]]) -> dict[str, Any]:
        self.state = JobState.PLAN
        while True:
            if graph.all_pass():
                self.state = JobState.DONE
                return {"status": "done", "nodes": graph.best_so_far(), "replans": graph.replan_count, "degraded": self.degraded}
            self.state = JobState.WAVE_FETCH
            ready = graph.ready_set()
            if not ready:
                return self._replan_or_stop(graph, tenant, plan_id, replan_fn)
            self.state = JobState.EXECUTE
            for node in ready:
                try:
                    self._exec_node(graph, node, tenant, plan_id)
                except PlanExhausted as exc:
                    self.state = JobState.EXHAUSTED
                    return {"status": "exhausted", "reason": str(exc), "nodes": graph.best_so_far(), "replans": graph.replan_count}
                except TransientError:
                    node.status = NodeStatus.FAILED
                    node.verdict = Verdict.ERROR
                if node.verdict is not Verdict.PASS:
                    self._maybe_critic(node)
                    return self._replan_or_stop(graph, tenant, plan_id, replan_fn)

    def _replan_or_stop(
        self, graph: PlanGraph, tenant: str, plan_id: str, replan_fn: Callable[[PlanGraph], list[PlanNode]]
    ) -> dict[str, Any]:
        if graph.replan_count >= self.max_replans:
            self.state = JobState.EXHAUSTED
            self.log.error("PLAN_EXHAUSTED", extra={"replan_count": graph.replan_count, "degraded": True})
            return {"status": "exhausted", "reason": "max_replans", "nodes": graph.best_so_far(), "replans": graph.replan_count, "degraded": True}
        self.state = JobState.REPLAN
        graph.replan_count += 1
        graph.merge_replan(replan_fn(graph))
        self.log.info("replan merge", extra={"replan_count": graph.replan_count})
        return self.run(graph, tenant, plan_id, replan_fn)

class FallbackChain:
    def __init__(self, harness: PlanningHarness) -> None:
        self.harness = harness

    def run(
        self,
        dag: PlanGraph,
        serial: PlanGraph,
        tenant: str,
        plan_id: str,
        replan_fn: Callable[[PlanGraph], list[PlanNode]],
    ) -> dict[str, Any]:
        try:
            return self.harness.run(dag, tenant, plan_id, replan_fn)
        except PermanentError as exc:
            self.harness.degraded = True
            self.harness.log.warning("fallback serial", extra={"degraded": True})
            try:
                return self.harness.run(serial, tenant, plan_id + ":serial", replan_fn)
            except Exception:
                self.harness.state = JobState.EXHAUSTED
                return {
                    "status": "exhausted",
                    "reason": f"fallback_best_so_far:{exc}",
                    "nodes": dag.best_so_far() or serial.best_so_far(),
                    "replans": dag.replan_count,
                    "degraded": True,
                }

def _search(args: dict[str, Any]) -> str:
    q = str(args.get("q", ""))
    if "fail-once" in q:
        return "empty"
    return f"hits for {q}"

def _sheet(args: dict[str, Any]) -> str:
    src = str(args.get("src", ""))
    if not src or src == "empty":
        raise TransientError("sheet needs search")
    return f"cells_filled from {src[:80]}"

def demo() -> dict[str, Any]:
    correlation_id = str(uuid.uuid4())
    tenant, plan_id = "acme", "plan-001"
    log = build_logger(correlation_id, tenant, plan_id)
    proxy = ToolProxy({"search": _search, "sheet": _sheet})
    oracle = HardOracle({
        "n1": lambda obs: obs.startswith("hits") and "empty" not in obs,
        "n2": lambda obs: obs.startswith("cells_filled"),
        "n1b": lambda obs: obs.startswith("hits") and "empty" not in obs,
    })
    harness = PlanningHarness(proxy, oracle, Critic(), CircuitBreaker(failure_threshold=5, recovery_seconds=0.01), HashChainAudit(), log)
    dag = PlanGraph(
        nodes={
            "n1": PlanNode("n1", "search", {"q": "fail-once AAPL 10-K"}, []),
            "n2": PlanNode("n2", "sheet", {"src": "$n1"}, ["n1"]),
        },
        allowlist=frozenset({"search", "sheet"}),
    )
    serial = PlanGraph(
        nodes={"n1": PlanNode("n1", "search", {"q": "AAPL 10-K"}, [])},
        allowlist=frozenset({"search", "sheet"}),
    )

    def replan_fn(graph: PlanGraph) -> list[PlanNode]:
        n1b = graph.nodes.get("n1b")
        if n1b is None or n1b.verdict is not Verdict.PASS:
            return [
                PlanNode("n1b", "search", {"q": "AAPL 10-K"}, []),
                PlanNode("n2", "sheet", {"src": "$n1b"}, ["n1b"]),
            ]
        return [PlanNode("n2", "sheet", {"src": "$n1b"}, ["n1b"])]

    result = FallbackChain(harness).run(dag, serial, tenant, plan_id, replan_fn)
    log.info("job terminal", extra={"degraded": result.get("degraded"), "replan_count": result.get("replans")})
    return result

if __name__ == "__main__":
    out = demo()
    print(json.dumps(out, indent=2, default=str))
    assert out["status"] in {"done", "exhausted"}
    assert out["status"] == "done"
    assert out["nodes"]["n2"].startswith("cells_filled")
```

Extract the block to `planning_harness.py` and run it. The demo plans a two-node DAG (`search` → `sheet` with `$n1`), fails the first oracle (empty hits; judge would pass — `verifier_disagree` prefers tests), marks `n1` skipped, replans onto `n1b` under CFI, rewires `sheet` to `$n1b`, and gates on `cells_filled`. Critic CB, PII redaction, arg-hash audit, and `max_replans` are on the same path. `TransientError` on `_sheet` is jitter-retried; irreversible tools raise into HITL rather than executing.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers and component choices are from the research file.

### Scenario 1 — Internal analyst copilot (search + spreadsheet + Slack)

**Problem statement.** Design a planning stack for an internal “analyst copilot”: parallel ticker fetches, a spreadsheet fill, optional Slack notify. Users paste 10-Ks (IDPI surface). Budget must stay near research’s Flash+cache band **[inferred] ~$15–40 / 1k**, not o3-everywhere (**5–10×**). p95 first-tool should track DAG stream, not a 12-hop ReAct loop (LLMCompiler **1.8–3.7×** latency / **3–7×** $ vs ReAct on parallel QA). Slack send is irreversible → HITL. A PM wants LATS “because WebShop scored **75.9**.”

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Analyst UI │ SSE │ CONTROL PLANE                                             │
│ / Slack    │────▶│ Gateway: SSO, correlation-id, TPM, critic CB              │
└────────────┘     │ Policy: PII redact; CFI freeze from user prompt           │
                   │ Router: DAG-shaped → compiler planner (Sonnet 5 / o4-mini)│
                   │ Orchestrator: LangGraph + LLMCompiler fetch; max_replans=2│
                   │ interrupt() before Slack send; no replan from 10-K text   │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ PLAN / REPLAN                │ EXECUTE / VERIFY
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES                 │
                   │ planner JSON DAG │        │ search (bulkhead)            │
                   │ $k placeholders  │        │ sheet fill (deterministic    │
                   │ parallel tickers │        │  “cells filled” oracle)      │
                   │ Haiku/Flash exec │        │ Slack MCP: OAuth, no send in │
                   │ critic=Haiku     │        │  planner context; HITL send  │
                   │  AFTER schema-ok │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ PostgresSaver plan; object-store 10-K blobs (hash in DB)  │
                   │ WORM audit: plan, arg hashes, verdicts; cache tools not   │
                   │  past_steps; reflections origin-tagged                    │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Planner = Sonnet 5 / o4-mini medium; executor = Haiku/Flash; critic = Haiku **only after** schema-validated tool JSON; verifier = deterministic “all required cells filled.” DAG for parallel ticker fetches (LLMCompiler). `max_replans=2`. MCP to Slack: OAuth 2.1, RFC 8707, no send in planner context. Freeze plan on pasted filings — quarantined schema-only observations if a replan is required.

**Trade-off evaluation matrix.**

| Dimension | A. ReAct 12-hop, o3-medium on every hop | B. Recommended: LLMCompiler DAG + role SKUs (planner mini/Sonnet, Haiku exec/critic) + cells-filled oracle + `max_replans=2` + Slack HITL | C. LATS / full MCTS on every research job |
| --- | --- | --- | --- |
| Cost | Stack C **[inferred] ~$180–350 / 1k T★**; ReAct multiplier **3–7×** A on parallel QA | Copilot band **[inferred] ~$15–40 / 1k** Flash+cache; T★ D **~$45–80** if Sonnet planner; **5–10×** less than o3-everywhere | **Tens–hundreds ×**; WebShop LATS ~**100×** slower than compiler at similar score (**72.8 ± 4.01** vs **75.9**) |
| Latency | Serial hops; no native parallelism; thinking per hop | Stream DAG; Movie Rec planner **1.88 s** + join **1.62 s** class; straggler **2×** mean mitigated per-node | Interactive SLA miss; many LM calls/task |
| Ops | Endless loop on one bad search (HuggingGPT warning) | Fetch queue + bulkheads; Temporal Activities; critic on its own worker pool | Node-call counters; dedicated search budget line |
| Security | Tool JSON in the instruction channel; 10-K IDPI → replan adds tools | CFI freeze; no replan from filing text; PlanGuard-style allowlist; Slack send HITL | Broader action tree = broader injection surface |
| Scalability | TPM/thinking-token storms; critic 429s | Horizontal ready-set; Flash concurrency **2500**; shed critic first | Does not fit 10 jobs/s without a search SKU |

**Decision rationale.** **B** is the only option that hits the $/1k band, uses the DAG where tools are independent, and treats Slack as HITL. A is the bill-shock anti-pattern the routing table exists to prevent. C’s WebShop quality is **not** a 100× latency bargain when LLMCompiler already matches score. Interview close: “Freeze the plan; parallelize the fetch; oracle the sheet; HITL the send.”

### Scenario 2 — Production coding agent (CI oracle, Temporal, no test gaming)

**Problem statement.** File-level coding agent on a monorepo. Must not ship green-on-wrong (Reflexion FP suites; Anthropic reward-hacking writeups: agent edits tests, `sys.exit(0)`, patches pytest). AlphaCodium shows public+generated tests lift CodeContests valid **pass@5 19% → 44%**, but generated tests are **advisory**. HITL before `apply` to main. Temporal retries must not double-commit. Effort-high only on the failing node. A staff engineer wants “just GPT-4 pass@1 **92.7%** LATS” as the default loop.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ PR bot /   │────▶│ CONTROL PLANE                                             │
│ IDE agent  │     │ Gateway: CI identity, correlation-id, repo RBAC           │
└────────────┘     │ Policy: CFI file+tool allowlist; no web in critic         │
                   │ Router: code+checker → reasoning on failing node only     │
                   │ Orchestrator: Temporal workflow; Activities=LLM/sandbox   │
                   │ interrupt() before apply-to-main; max_replans=2           │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │                              │
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES / ORACLES       │
                   │ planner: file DAG│        │ sandbox exec (bulkhead)      │
                   │ executor sandbox │        │ compiler logs → Reflexion    │
                   │ ADaPT split on   │        │ PLATFORM hidden tests (gate) │
                   │  fail, d_max cap │        │ generated tests = advisory   │
                   │ effort high only │        │ tests live outside workspace │
                   │  on failed node  │        │  ACL (anti-gaming)           │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE                                               │
                   │ Temporal history = control; workspace snapshot on fork    │
                   │ idempotency keys on apply; WORM: plan, patch hashes,      │
                   │  hidden-test verdicts; critic from compiler log not web   │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Planner writes a file-level DAG; executor in sandbox; **platform unit tests** as oracle; Reflexion on **compiler logs**; no web in critic (ablation: without tests reflection **hurts** 52% vs 60%). HITL interrupt before `apply` to main. Temporal Activities for LLM so retries do not double-commit. AlphaCodium-style generated tests are advisory. Hidden tests live **outside** the workspace ACL. After `max_replans`, human.

**Trade-off evaluation matrix.**

| Dimension | A. Self-generated tests as the only gate + ReAct in the workspace | B. Recommended: file DAG + platform hidden tests as oracle + Reflexion on compiler logs + Temporal idempotent apply + HITL before main | C. LATS default (HumanEval GPT-4 **92.7%**) on every file |
| --- | --- | --- | --- |
| Cost | Cheap per hop until infinite edit loop; FP green **stops** a wrong program | T★ A/B/D band if executor is mini and verifier is **$0** pytest; effort-high **only** on the failing node | LATS **tens–hundreds ×**; HumanEval ≠ monorepo side effects |
| Latency | Unbounded retries; no wave parallelism across independent files | DAG waves per package; ADaPT depth on fail (**+28.3%** ALFWorld-class lesson: as-needed split) | Search tree on the interactive fuse |
| Ops | Agent patches pytest; dual-spend on replay | Temporal replay skips succeeded Activities; tests outside ACL; `same_action_k` | Need a dedicated search budget and node-call cap |
| Security | Workspace ACL includes tests → gaming; web critic = IDPI | CFI one-tool-per-step; critic from oracle log; no allowlist expand without HITL | Broader action space; still needs hidden tests |
| Scalability | Linear in retries × files | Horizontal sandboxes; hidden-test runners scale independently of the planner | Value-model/MCTS does not self-improve at R1 scale; don’t pretend it will here |

**Decision rationale.** **B** is the only option that ranks oracles correctly (hidden tests > generated tests > judge), keeps apply idempotent, and uses reflection where it has an oracle (compiler log). A is the Reflexion FP failure mode. C’s **92.7%** is a HumanEval number, not a license to run MCTS on every PR — LLMCompiler vs LATS on WebShop is the cost cliff warning. Interview close: “The platform owns the tests. The workflow owns apply. The model proposes patches.”

---

*End of module. Six sections. Four topics (decomposition, reflection, verification, replanning). Token `$ / 1k` tables are **[inferred]** from the stated T★ reference job and list prices dated 2026-08-21. No unpublished planning-agent e2e p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or cited to LLMCompiler averages / anecdotal thinking latencies.*
