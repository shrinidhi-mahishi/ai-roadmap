# Research: Agent Loop Patterns

**Date researched**: 2026-09-23
**Sources consulted**: 94

Vendor list prices, tokenizer tables, SDK HTTP retry constants, and RPM/TPM live in [`01-python-llm-foundations.md`](01-python-llm-foundations.md). Prefix-stability, cache breakpoints, and the **tool-schema tax** live in [`02-context-engineering.md`](02-context-engineering.md). JSON Schema / `strict` / dispatcher IDs / `is_error` mapping live in [`03-tool-calling.md`](03-tool-calling.md). This file does **not** recopy those tables. It covers the **control-plane loop**: who decides the next hop, when the model is allowed to think vs act vs replan vs reflect, and which fuse (`max_turns`, `recursion_limit`, `max_budget_usd`, watchdog) actually stops spend.

Invariant across OpenAI Agents SDK, Anthropic Messages, LangGraph `create_agent`, Claude Agent SDK, and Google ADK: **the model does not execute tools or terminate the process**. It emits structured actions (or text); **your** runtime decides whether to dispatch, inject an observation, replan, interrupt a human, or halt ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/); [Anthropic how tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works); [LangChain create_agent](https://reference.langchain.com/python/langchain/agents/create_agent); [Claude Agent SDK loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)).

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane

An agent loop is two planes sharing a growing transcript.

| Plane | Owns | Does not own |
| --- | --- | --- |
| **Control plane** | Stop reasons, hop budget (`max_turns` / `recursion_limit` / `max_iterations` / `max_budget_usd`), which tools are legal this hop, whether to replan vs continue, HITL interrupt, checkpointer `thread_id`, watchdog / circuit on repeated `(tool, args)` | Transformer weights, KV cache |
| **Data plane (model)** | Sample thought / tool JSON / final text; optional hidden reasoning tokens | HTTP to Stripe, Wikipedia, sandbox |
| **Data plane (executor)** | Run tools, return observations; map errors to `tool_result` / `function_call_output` (see 03) | Sampling, loop termination |

Hosted **server tools** (OpenAI built-ins, Anthropic `web_search` / `code_execution`) invert the executor: the provider runs an **inner** agentic loop and may return `pause_turn` when *that* inner loop hits its iteration cap ([Anthropic stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons); [Server tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools)). Your outer loop still owns the **client** fuse.

Anthropic’s 2024 split still holds: **workflows** = LLMs and tools on predefined code paths; **agents** = the LLM dynamically directs process and tool use ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Production stacks mix both: a deterministic outer graph wrapping a ReAct inner loop. Their field report: the most successful implementations used **simple composable patterns**, not complex frameworks — start with a workflow, add a loop only where the next step is genuinely model-chosen.

**State machines (the three loops you must be able to draw):**

```
ReAct:     START → MODEL ⇄ TOOLS → END
           exit MODEL when no tool_calls / stop_reason ≠ tool_use
           fuse: max_turns | recursion_limit | remaining_steps | hash circuit

P&E:       START → PLAN → EXEC(plan[0]) → REPLAN ⇄ EXEC → END
           REPLAN emits remaining steps | Response
           fuse: max_replans + per-exec inner max_turns
           DAG variant: PLAN → FETCH-READY ⇉ EXEC → JOIN → (REPLAN | END)

Reflection: TRIAL(ReAct) → EVAL → (END if pass) → REFLECT → MEMORY → TRIAL
            fuse: max_trials (Reflexion AlfWorld used 12; WebShop died at 4)
            Self-Refine is TRIAL-less: GEN ⇄ (FEEDBACK → REFINE) with M≤4
```

The **control-plane state** is the node + hop counters + plan object + memory buffer. The **data-plane state** is the transcript and tool I/O. Mixing them (letting REFLECT rewrite PLAN in the same forward pass that calls Stripe) is the dominant correctness failure.

### 1.2 ReAct: thought–action–observation

Yao et al. (ICLR 2023; arXiv:2210.03629) augment the action space to \(\hat{\mathcal{A}} = \mathcal{A} \cup \mathcal{L}\): language **thoughts** do not touch the environment; domain **actions** do. The trajectory is interleaved `Thought → Action → Observation` ([ReAct arXiv](https://arxiv.org/abs/2210.03629); [HTML v3](https://arxiv.org/html/2210.03629v3); [Google Research blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)).

Thoughts in the paper: decompose goals, extract from observations, inject commonsense, reformulate search, synthesize answers. For HotpotQA/FEVER they used **dense** TAO steps (a thought on every hop). For ALFWorld/WebShop, thoughts are **sparse**: the LM decides when to emit `think:` vs an environment action; a `think:` action returns observation `"OK."` ([ALFWorld notebook](https://github.com/ysymyth/ReAct/blob/master/alfworld.ipynb)).

**HotpotQA action space** (paper + official notebook): `Search[entity]`, `Lookup[keyword]`, `Finish[answer]` against a weak Wikipedia API (first paragraph / next-sentence Ctrl+F), deliberately not a neural retriever ([hotpotqa.ipynb](https://github.com/ysymyth/ReAct/blob/master/hotpotqa.ipynb)). The reference loop is `for i in range(1, 8)` — **7 steps** then a forced `finish[]`. FEVER was capped at **5**. Of already-correct trajectories, those using the full 7 / 5 steps were only **0.84% / 1.33%** ([ReAct HTML](https://arxiv.org/html/2210.03629v3)). ALFWorld’s notebook caps at **`range(1, 50)`** — a 49-step fuse, not a learned stop ([alfworld.ipynb](https://github.com/ysymyth/ReAct/blob/master/alfworld.ipynb)).

**PaLM-540B prompting (paper Table 1):**

| Method | HotpotQA EM | FEVER Acc |
| --- | --- | --- |
| Standard | 28.7 | 57.1 |
| CoT | 29.4 | 56.3 |
| CoT-SC (Wang et al. 2022a) | 33.4 | 60.4 |
| Act-only | 25.7 | 58.9 |
| ReAct | 27.4 | 60.9 |
| CoT-SC → ReAct | 34.2 | 64.6 |
| ReAct → CoT-SC | **35.1** | 62.0 |
| Supervised SoTA (then) | 67.5 | 89.5 |

ReAct **loses** to CoT on HotpotQA EM (27.4 vs 29.4) and **wins** on FEVER (60.9 vs 56.3). The authors’ hybrid is the actual SoTA of the paper: back off from ReAct to CoT-SC when the step cap fires, or from CoT-SC to ReAct when majority vote is weaker than \(n/2\) ([ReAct HTML](https://arxiv.org/html/2210.03629v3)).

**ALFWorld / WebShop (Google blog + paper Table 4):** 1–2-shot ReAct beat IL/RL trained on \(10^3\)–\(10^5\) instances by **+34 pp** (ALFWorld 71 vs BUTLER 37) and **+10 pp** success (WebShop 40.0 vs IL 29.1; Act-only 30.1). Act-only ALFWorld 45 vs ReAct 71. ReAct-IM (Inner-Monologue-style dense thoughts) 53 vs ReAct 71 — sparse versatile thoughts beat dense “what just happened” monologue ([Google blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/); [ReAct HTML](https://arxiv.org/html/2210.03629v3)).

**When ReAct fails (human labels, 200 HotpotQA trajectories, Table 2):**

| Mode | ReAct | CoT |
| --- | --- | --- |
| Success: true positive | 94% | 86% |
| Success: false positive (hallucinated facts) | 6% | 14% |
| Failure: reasoning error (incl. **repetitive TAO loops**) | **47%** | 16% |
| Failure: empty/useless search | **23%** | n/a |
| Failure: hallucination | **0%** | **56%** |
| Failure: label ambiguity | 29% | 28% |

Grounding kills hallucination; the same interleaving **reduces reasoning flexibility** and creates the signature failure: greedy decode repeats the previous thought+action. The authors suspected greedy decoding; production implication: ReAct needs an **external** loop breaker. The model will not reliably stop itself ([ReAct HTML](https://arxiv.org/html/2210.03629v3)).

### 1.3 Interleaved vs batched (same family, different clocks)

Do not collapse these. They have different meters and injection surfaces.

| Mode | What one model hop contains | Observation timing | Typical product |
| --- | --- | --- | --- |
| **Interleaved TAO** | One thought + one action | After every action | Yao notebooks; LangGraph model node → tools node → model |
| **Batched actions, interleaved hops** | N parallel `tool_calls` / `tool_use` in one assistant message | All results in **one** following user message | OpenAI `parallel_tool_calls`; Anthropic parallel `tool_use` (03) |
| **Batched plan, then tools** | Planner emits a DAG/list **without** seeing tool output | Workers run; Solver/Joiner sees all evidence once | ReWOO; LLMCompiler; HuggingGPT |
| **Hidden interleaved** | Reasoning tokens + tools inside one provider turn | Server tools may `pause_turn` | Anthropic server-tool loop; o-series / adaptive thinking (01) |

**Interleaved cost topology.** ReWOO writes the quadratic: for \(k\) TAO steps, interleaved ALMs re-send context \(C\), exemplars \(S\), and all prior \((T_j, A_j, O_j)\) on every hop because hosted APIs are **stateless**. Token count grows **linear in \(k\) on \(C+S\)** and **quadratic in trajectory fragments** ([ReWOO arXiv](https://arxiv.org/abs/2305.18323); [HTML](https://arxiv.org/html/2305.18323v1)). Prompt caching (02) flattens the \(C\) term **if** the prefix is byte-stable; it does **not** flatten the growing observation suffix.

**Batched-actions hop.** One OpenAI Agents SDK **turn** = “one AI invocation (including any tool calls that might occur)” ([Python running agents](https://openai.github.io/openai-agents-python/running_agents/); [JS running agents](https://openai.github.io/openai-agents-js/guides/running-agents/)). Parallel tools in that invocation are **one** turn, not \(N\). Claude Agent SDK is the opposite meter: `max_turns` counts **tool-use round trips only**; a final text-only message is a separate closing turn and `max_turns=2` can stop **before** an edit ([Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)). Interview trap: “turn” is not a portable unit.

**LangGraph super-step.** Pregel: nodes that run in parallel share a super-step; sequential nodes are separate super-steps. `recursion_limit` counts **super-steps**, not tool calls. A classic ReAct graph is typically **2 super-steps per tool round** (model node + tools node) ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api); [Use graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)).

### 1.4 Production ReAct: LangGraph `create_agent` / OpenAI Agents SDK / Anthropic

**LangChain `create_agent` (replaces deprecated `create_react_agent`).** Compiled LangGraph: call model → if `tool_calls` then tools node → repeat until no tool calls ([create_agent](https://reference.langchain.com/python/langchain/agents/create_agent); [factory.py](https://github.com/langchain-ai/langchain/blob/8b21400627672754a56350e552d39b23f77206a3/libs/langchain_v1/langchain/agents/factory.py)). Middleware hooks wrap the loop, not a second runtime: `before_agent` / `before_model` / `wrap_model_call` / `wrap_tool_call` / `after_model` / `after_agent`. `before_*` runs first-to-last; `after_*` last-to-first; `wrap_*` nests ([Middleware overview](https://docs.langchain.com/oss/python/langchain/middleware/overview); [Custom middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom); [LangChain blog](https://www.langchain.com/blog/how-middleware-lets-you-customize-your-agent-harness)). Deep Agents is that factory plus an opinionated stack (todos, filesystem, subagents, summarization, prompt-caching, HITL) ([same blog](https://www.langchain.com/blog/how-middleware-lets-you-customize-your-agent-harness)).

Deprecated `create_react_agent`: if managed `remaining_steps < 2` and the model still wants tools, it returns a final AI message **"Sorry, need more steps to process this request."** and does **not** raise `GraphRecursionError` ([create_react_agent](https://reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent); [chat_agent_executor.py](https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py); [SO #79446089](https://stackoverflow.com/questions/79446089/langgraph-create-react-agent-with-sqltoolkit-issue-sorry-need-more-steps-to-pr)). `remaining_steps ≈ recursion_limit − total_steps_taken`. Production graphs should route on `RemainingSteps` **before** the fuse ([Graph API RemainingSteps](https://docs.langchain.com/oss/python/langgraph/graph-api)).

**OpenAI Agents SDK `Runner`.** Loop: invoke LLM → final output (text of `output_type` and **no** tool calls) **or** handoff (swap agent, re-enter) **or** run tools and re-enter. Default `DEFAULT_MAX_TURNS = 10`. Exceed → `MaxTurnsExceeded` / JS `MaxTurnsExceededError`. Pass `None` / `null` to disable. `error_handlers={"max_turns": ...}` can return a controlled final output instead of throwing ([run_config.py](https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py); [running agents](https://openai.github.io/openai-agents-python/running_agents/); [JS](https://openai.github.io/openai-agents-js/guides/running-agents/); [exceptions](https://openai.github.io/openai-agents-python/ref/exceptions/)). A **handoff consumes a turn** of the same `max_turns` budget: specialist ping-pong can `MaxTurnsExceeded` with **zero** user-visible tools. Guardrails (`InputGuardrailTripwireTriggered` / `OutputGuardrailTripwireTriggered`) abort the loop **outside** the turn counter — do not confuse a tripwire with a fuse.

**Anthropic Messages client loop.** Canonical: `while stop_reason == "tool_use"`: execute every client tool, send **one** user message with **all** `tool_result` blocks first (03). Exit on `end_turn`, `max_tokens`, `stop_sequence`, `refusal`, or `model_context_window_exceeded`. `pause_turn` is **not** client-tool: a **server-tool** inner loop hit its iteration limit — resend the assistant content unchanged with the same `tools` array ([How tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works); [Stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons); [Server tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools)). Mixed parallel client+server in one group: `stop_reason: "tool_use"` with a `server_tool_use` that has **no** result yet; the API runs the server tool **after** you return client results (03).

**Claude Agent SDK.** Same Claude Code loop embedded: evaluate → tools → results → repeat until text with no tool calls. Caps: `max_turns` / `maxTurns` (tool-use round trips; **no default limit**), `max_budget_usd` / `maxBudgetUsd` (client-side estimate vs `total_cost_usd`). Hit → `ResultMessage.subtype` `error_max_turns` or `error_max_budget_usd` — **no** `result` text. Budget is checked **after** an API call, so cost may overshoot by **one call** ([Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop); [Python options](https://code.claude.com/docs/en/agent-sdk/python); [max_budget_usd.py](https://github.com/anthropics/claude-agent-sdk-python/blob/main/examples/max_budget_usd.py)).

### 1.5 Plan-and-execute: plan, then a tool loop

**Plan-and-Solve / PS+ (Wang et al., ACL 2023; arXiv:2305.04091).** Zero-shot replacement for “Let’s think step by step”: first *devise a plan*, then *carry it out* **in one generation**. Not a tool loop. Autopsy on 100 GSM8K-style Zero-shot-CoT misses with GPT-3: calculation **7%**, missing-step **12%**, semantic misunderstanding **27%** of the sampled incorrect set. PS targets missing steps; PS+ adds “extract variables/numerals” and “calculate intermediates.” On `text-davinci-003` T=0: PS+ MultiArith **91.8**, GSM8K **59.3** (+2.9 vs Zero-shot-CoT), six-dataset arithmetic average **76.7**; CSQA **71.9 vs 65.2**; StrategyQA **65.4 vs 63.8**. PS+ with self-consistency \(N=10\), T=0.7: GSM8K **73.7**, SVAMP **84.4** vs Zero-shot-CoT+SC **70.7 / 81.7** ([ACL 2023](https://aclanthology.org/2023.acl-long.147/); [arXiv](https://arxiv.org/abs/2305.04091)). Topology: still **one** LLM call unless you split plan vs execute (LangGraph does).

**LangChain / LangGraph plan-and-execute.** Inspired by Plan-and-Solve + BabyAGI. Graph: `planner` → `agent` (execute `plan[0]`) → `replan` → END or back to `agent`. State: `input`, `plan`, `past_steps` (`Annotated[..., operator.add]`), `response`. Replanner sees objective + remaining plan + past step/result pairs; emits remaining steps **or** a `Response`. Documented limitation: **serial** steps; embarrassingly parallel work should be a DAG (LLMCompiler) ([Planning agents blog](https://www.langchain.com/blog/planning-agents); [LangGraphJS notebook](https://github.com/langchain-ai/langgraphjs/blob/main/examples/plan-and-execute/plan-and-execute.ipynb)). When to replan: **after every executed step** in this template — local repair, not full search. Cap `max_replans` in the conditional edge; the graph will not do it for you.

**ReWOO (Xu et al., 2023; arXiv:2305.18323).** Planner → Worker(s) → Solver. Planner emits consecutive `(Plan, #E_s)` tuples; later plans may reference `#E_s` placeholders. Workers fill evidence **without** the planner seeing observations. Solver reads plans + evidence once. HotpotQA (gpt-3.5-turbo): ReWOO **42.4** acc / **1,986.2** tokens / **$3.97 per 1k queries** vs ReAct **40.8** / **9,795.1** / **$19.59** — authors’ **5×** token claim and **+4 pp** (42.4−40.8). Across six public benchmarks: **−64%** tokens, **+4.4 pp** accuracy. Tool-failure ablation (“No evidence found” from every tool): ReAct **−40.8** acc, ReWOO **−29.2** — batched plans degrade less when workers fail. Extraneous tools **hurt**: 17/20 inspected 7-tool failures were tool misuse (e.g. Yelp for a celebrity) ([ReWOO HTML](https://arxiv.org/html/2305.18323v1)).

**LLMCompiler (Kim et al., ICML 2024; arXiv:2312.04511).** Compiler analogy: (i) Function Calling Planner emits a **DAG** with `$k` placeholders; (ii) Task Fetching Unit substitutes completed outputs and dispatches ready nodes; (iii) Executor runs independent tasks concurrently; optional **Joiner** replans or answers. Streaming the DAG hides planner latency behind tool I/O (up to **1.3×** extra on ParallelQA). Headline vs ReAct: up to **3.7×** latency, **6.7×** cost, **~9 pp** accuracy. Concrete Table 1/2 (GPT closed-source):

| Bench | ReAct† / ReAct | LLMCompiler | vs OAI parallel FC |
| --- | --- | --- | --- |
| HotpotQA | 62.47%, 7.12 s, 2900 in / 120 out, $5.00/1k | 62.00%, 3.95 s (**1.80×**), 1300/80, $1.47/1k (**3.37×** cheaper) | OAI 62.05%, 4.42 s (**1.61×**) |
| Movie Rec | 72.47%, 20.47 s, 20k/230, $20.46/1k | **77.13%**, 5.47 s (**3.74×**), 2800/115, $3.04/1k (**6.73×**) | OAI 77.00%, 7.42 s (**2.76×**) |
| ParallelQA | 89.09%, 35.90 s, 46k/470, $480/1k | 89.38%, 16.69 s (**2.15×**), 9200/340, $103/1k (**4.65×**) | OAI 87.32%, 19.29 s |

LLaMA-2 70B HotpotQA **54.40 → 57.83** (~**+3.4 pp**); Movie Rec **70.60 → 77.80** (~**+7.2 pp**). Residual: Movie Rec planner **1.88 s** + answer **1.62 s** average — more than half of e2e when tools are fast. ~**10%** of HotpotQA ReAct runs needed **>4** function calls (expected 2-way parallel) — looping/divergent. WebShop vs LATS: gpt-3.5-turbo LATS **38.0** SR / **75.9** score / **1,066 s** (N=50) vs LLMCompiler **44.0 / 72.8 / 10.72 s** — **101.7×** wall-clock at similar score (**72.8 ± 4.01**) ([LLMCompiler HTML](https://arxiv.org/html/2312.04511v3); [GitHub](https://github.com/SqueezeAILab/LLMCompiler)).

**When the Joiner / replanner should fire (operational).** Replan is a **control-plane decision**, not a vibe:

| Trigger | Replan? | Else |
| --- | --- | --- |
| Tool `is_error` on a **leaf** that has an alternate tool | Maybe (swap worker) | Retry with backoff (03); hash circuit if identical args |
| Empty / useless search (ReAct’s 23% HotpotQA failure) | Yes, reformulate query in the **plan** | Blind ReAct `Lookup` loops |
| Verifier / unit test fail | Yes, or Reflexion-style new trial | Do not Self-Refine the same code without the log |
| DAG node output invalidates a downstream `$k` | Yes (LLMCompiler Game of 24) | Joiner, not a full user-visible restart |
| Observation injects a new **goal** (send email, new vendor) | **No** — freeze original objective; HITL | PlanFlip / LLM06 |
| Every executed P&E step | LangGraph template does this | Cap `max_replans` at **2–3** |

Do **not** replan every hop — that collapses to ReAct and you pay planner tokens on every search. HuggingGPT’s documented contrast: global plan in **one** planner query vs BabyAGI/AutoGPT iterative next-task, which can loop forever on a bad step ([HuggingGPT PDF](https://papers.neurips.cc/paper_files/paper/2023/file/77c33e6a367922d003ff102ffb92b658-Paper-Conference.pdf)).

**HuggingGPT / JARVIS (Shen et al., NeurIPS 2023; arXiv:2303.17580).** Four stages: task planning → model selection (HF model cards + download rank) → task execution on hybrid endpoints → response generation. Plan schema `[{task, id, dep, args}]`; `dep` is prerequisite ids; args may contain `<resource>-task_id` placeholders. Independent tasks run in parallel. Authors’ limits: plans not guaranteed feasible/optimal; **multiple sequential LLM round-trips** dominate latency; context length caps how many model cards you can rank ([arXiv](https://arxiv.org/abs/2303.17580); [JARVIS README](https://github.com/microsoft/jarvis/blob/main/hugginggpt/README.md)).

**Anthropic orchestrator-workers vs evaluator-optimizer.** Orchestrator dynamically creates subtasks at runtime (not a fixed fan-out). Evaluator-optimizer: generate ↔ critique loop when criteria are clear and a second LLM can give actionable feedback ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents); [Cookbook orchestrator](https://platform.claude.com/cookbook/patterns-agents-orchestrator-workers)).

### 1.6 Reflection: verbal RL, not weight updates

**Reflexion (Shinn et al., NeurIPS 2023; arXiv:2303.11366).** Actor (often ReAct) → environment / evaluator → self-reflection LLM → **episodic memory** of verbal hints → **next trial**. Feedback can be scalar or NL, external or self-simulated. This is **across trials**, not within one trajectory. AlfWorld: **130/134** with a simple heuristic detector; **+22 pp** over ReAct across **12** trials (ReAct-only plateaus between trials 6–7). HotpotQA **+20 pp**. HumanEval Python pass@1 **91.0** vs GPT-4 **80.1**. Programming: CoT-generate ≤6 unit tests, AST-filter, run, reflect. Ablation on hardest 50 HumanEval-Rust: **without tests, reflection hurts (52% vs 60% baseline)** — the critic needs an oracle. WebShop: two-shot ReAct+Reflexion, **100** envs, **terminated after 4 trials** with no useful reflections — cannot explore diverse catalogs ([NeurIPS PDF](https://proceedings.neurips.cc/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf); [ar5iv](https://ar5iv.labs.arxiv.org/html/2303.11366)).

**Self-Refine (Madaan et al., NeurIPS 2023; arXiv:2303.17651).** Same LLM as generator, feedback, and refiner. \(y_0 = M(p_{gen}\|x)\); then \(fb_t = M(p_{fb}\|x\|y_t)\); \(y_{t+1} = M(p_{refine}\|x\|y_0\|fb_0\|\ldots\|y_t\|fb_t)\). Stop at task criterion or **M ≤ 4**. ~**20%** absolute average across 7 tasks vs one-shot same model. **Math barely moves:** GPT-3.5 64.1→64.1 (0); ChatGPT 74.8→75.0; GPT-4 92.9→93.1. Dialogue / constrained generation move tens of points (GPT-4 dialogue 25.4→74.6; constrained 4.4→61.3). Acronym generation is **non-monotonic** across aspects; they pick the max score across iterations. Code optimization: 22.0→28.8 by \(y_3\) in the iteration plot ([NeurIPS PDF](https://proceedings.neurips.cc/paper_files/paper/2023/file/91edff07232fb1b55a505a9e9f6c0ff3-Paper-Conference.pdf); [selfrefine.info](https://selfrefine.info/)).

**Trajectory reflection vs output reflection.** Reflexion writes a hint from a **failed episode** into the next episode’s prompt. Self-Refine rewrites the **same** artifact in-context. LATS (Zhou et al., ICML 2024; arXiv:2310.04406) puts Reflexion **inside MCTS** over ReAct steps: LM as actor, value, reflection. HumanEval GPT-4 pass@1 **92.7%**; WebShop GPT-3.5 avg **75.9**; HotpotQA ~**2×** ReAct. Cost: LATS WebShop **1,066 s** vs ReAct **5.98 s** on gpt-3.5-turbo (LLMCompiler Table 3) ([LATS HTML](https://arxiv.org/html/2310.04406)).

**Constitutional AI (Bai et al., arXiv:2212.08073)** is a **train-time** critic: SL (sample → self-critique vs written principles → revise → SFT) then RLAIF. Same topology (critique then revise) as Self-Refine, distilled into weights. Runtime “constitutional self-critique” is the SL phase without the SFT — a policy artifact in the prompt, not a new model ([CAI HTML](https://arxiv.org/html/2212.08073)).

**Critic models.** Prefer a **separate** evaluator with a different prompt (Anthropic evaluator-optimizer) or a **hard oracle** (unit tests, interpreter). Same-model FEEDBACK is Self-Refine; it is cheaper and more correlated with the generator’s mistakes.

### 1.7 Self-correction: tools, constitution, verifiers, debate

Four mechanisms that are **not** interchangeable:

| Mechanism | Feedback source | When it works | When it burns tokens |
| --- | --- | --- | --- |
| **Tool-error feedback** | Executor `is_error` / stderr / HTTP 4xx (03) | Schema/timeout/retryable 5xx mapped honestly | Swallowed exceptions look like empty success |
| **Constitutional self-critique** | Written principles, same or critic model | Harmlessness / style with a constitution | Open-ended math without an oracle |
| **Verifier loop** | Unit tests, interpreter, search, PRM, LLM-as-judge | Tasks with a checker | Fake-green tests; judge bias |
| **Debate** | Two agents, (possibly weak) judge | Oversight / PSPACE analogy | Cost × rounds; not better than self-consistency at equal \(N\) |

**Tool-error feedback.** The control plane must inject a **structured** failure, not a silent empty string. Anthropic `is_error: true` on `tool_result`; skipped parallel siblings still need an error block (03). AutoGPT Copilot (2026): after **3** consecutive identical `(tool, canonical_args)` failures, hard-stop the model toward text; after **6** consecutive empty `input: {}` tool calls, abort the stream (`circuit_breaker_empty_tool_calls`). Trigger: a session spent **51+ minutes** / **21+** identical empty calls after context saturation ([AutoGPT PR #12499](https://github.com/Significant-Gravitas/AutoGPT/pull/12499)). LangGraph `ToolNode.handle_tool_error` is the in-process analog (03) — it is **not** a hop fuse.

**CRITIC (Gou et al., ICLR 2024; arXiv:2305.11738).** Critique is **tool-interactive**: search, interpreter. ChatGPT: **+7.7 F1** across three QA sets; **+7.0** absolute on three math sets; **79.2%** reduction in toxicity probability. **CRITIC w/o Tool** can *degrade*: text-davinci-003 SVAMP **−1.8**; toxicity **worsens** (0.344→0.353). TabMWP gains scale with size: **+4.7 / +9.4 / +16.0** at 7B/13B/70B. Stop: correct up to **n=3**, early-stop if the answer is unchanged for two consecutive corrections; QA uses up to **7** interactions ([CRITIC](https://arxiv.org/abs/2305.11738)).

**Huang et al. ICLR 2024 (arXiv:2310.01798) — intrinsic self-correction.** Without oracle labels, self-correction **drops** accuracy. GPT-3.5 GSM8K **75.9 → 74.7** (round 2, **5** calls); CommonSenseQA **75.8 → 41.8**; GPT-4 GSM8K **95.5 → 89.0**, HotpotQA **49.0 → 43.0**. Oracle-label loops look great (GPT-3.5 GSM8K **75.9 → 84.3**) because the ground truth **prevents** correct→incorrect edits. On GSM8K, GPT-3.5 **keeps** the initial answer **74.7%** of the time; when it edits, it more often breaks a correct answer than fixes a wrong one. Multi-agent debate (Du/Liang 2023) was **no better than self-consistency** at equal response count ([Huang arXiv](https://arxiv.org/abs/2310.01798); [ICLR PDF](https://proceedings.iclr.cc/paper_files/paper/2024/file/8b4add8b0aa8749d80a34ca5d941c355-Paper-Conference.pdf)). Kamoi et al. survey (arXiv:2406.01297): bottleneck is **feedback generation**; no work shows successful intrinsic self-correction on general tasks; it works when a reliable checker exists ([survey](https://arxiv.org/html/2406.01297)).

**Debate (Irving, Christiano, Amodei, arXiv:1805.00899).** Two agents take turns; a human (or sparse classifier) judges. Complexity analogy: debate with optimal play can answer **PSPACE** questions with poly-time judges (direct judging ≈ **NP**). MNIST sparse-pixel toy: 6 pixels **59.4% → 88.9%**; 4 pixels **48.2% → 85.2%** ([arXiv](https://arxiv.org/abs/1805.00899)). Production: use as a **soft** critic behind a hard gate, not as the sole stop condition.

**Verifier ranking (production).** Hard oracles (tests, interpreters, exact match) > process reward models for rerank > LLM-as-judge for open-ended style. False-positive tests (green suite on wrong code) stop the agent — worse than false negatives (agent keeps editing). Reflexion’s Rust ablation is the exhibit.

### 1.8 Max iteration limits (the actual fuses)

These are **different clocks**. Collapsing them is how teams ship infinite spend.

| Fuse | Unit | Default (verify the version you ship) | On trip |
| --- | --- | --- | --- |
| OpenAI Agents SDK `max_turns` | One **model invocation** including tools in that invocation | **10** (`DEFAULT_MAX_TURNS`); `None` disables | `MaxTurnsExceeded` or `error_handlers["max_turns"]` |
| Claude Agent SDK `max_turns` | **Tool-use** round trips only | **None** (unlimited) | `error_max_turns` |
| Claude Agent SDK `max_budget_usd` | Client-side USD estimate | **None** | `error_max_budget_usd`; may overshoot **one** API call |
| LangGraph `recursion_limit` | Pregel **super-steps** | **Version-dependent:** RunnableConfig historically **25**; docs as of LangGraph **1.0.6+** say **1000**; Deep Agents **subagents** still reported hitting **25** | `GraphRecursionError` / `GRAPH_RECURSION_LIMIT` |
| `create_react_agent` `remaining_steps` | Super-steps until limit | Derived | Soft stop: “Sorry, need more steps…” |
| Deep Agents parent graph | Super-steps via `.with_config` | **9,999** in older SDK comments ([issue #2826](https://github.com/langchain-ai/deepagents/issues/2826)); **1000** in current `graph.py` ([graph.py](https://github.com/langchain-ai/deepagents/blob/bdc7da64/libs/deepagents/deepagents/graph.py)); dcode **2000** (`RECURSION_LIMIT_DEFAULT`), floor **25**, ceiling **100,000** ([config_manifest.py](https://github.com/langchain-ai/deepagents/blob/d60560d6/libs/code/deepagents_code/config_manifest.py); [PR #4994](https://github.com/langchain-ai/deepagents/pull/4994)) | Same `GraphRecursionError`; **parent limit does not propagate to subagents** ([issue #1698](https://github.com/langchain-ai/deepagents/issues/1698)) |
| Google ADK `LoopAgent.max_iterations` | Full cycles over `sub_agents` | Unset = **run until escalate** | Stop, or `EventActions.escalate=True` from any sub-agent ([loop_agent.py](https://github.com/google/adk-python/blob/main/src/google/adk/agents/loop_agent.py); [docs](https://github.com/google/adk-docs/blob/5331a07f/docs/agents/multi-agents.md); [discussion #4921](https://github.com/google/adk-python/discussions/4921)) |
| ReAct paper | Env steps | HotpotQA **7**, FEVER **5**, ALFWorld **49** | Forced `finish[]` / return 0 |
| AutoGPT | Iterations + watchdog | Product-specific; WatchdogComponent switches “smart mode” on loop detect ([Forge components](https://github.com/Significant-Gravitas/AutoGPT/blob/master/docs/content/forge/components/built-in-components.md)) | Circuit breaker / `finished` yield |

**`recursion_limit` is a standalone config key**, not inside `configurable` ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)):

```python
graph.invoke(inputs, {"recursion_limit": 50})  # correct
# not: {"configurable": {"recursion_limit": 50}}
```

**Super-step arithmetic (interview).** Count nodes that run **sequentially**, not tool calls.

| Graph | Super-steps per tool round | Historical default 25 | Docs default 1000 (1.0.6+) |
| --- | --- | --- | --- |
| ReAct: `model → tools → model` | **2** (model + tools); the next model is the start of the next round | ≈ **12** tool rounds then `GraphRecursionError` | ≈ **500** tool rounds |
| Parallel `Send` fan-out of N workers then join | 1 (workers) + 1 (join) after the plan node | Burns fast if you also loop | Still need a **tool-hash** circuit |
| P&E: `planner → agent → replan` | **3** per executed step | ≈ **8** steps | Plenty; **`max_replans`** is the real fuse |
| Deep Agents subagent (unpropagated) | same as ReAct | Hits **25** while parent has 150–9999 ([#1698](https://github.com/langchain-ai/deepagents/issues/1698)) | Parent 1000 is irrelevant |

`remaining_steps < 2` needs **two** super-steps left to finish a tool round (call tool, then model). That is why the soft message fires while tools are still pending ([chat_agent_executor.py](https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py)).

Proactive vs reactive: `RemainingSteps` routes to END **inside** the graph; catching `GraphRecursionError` is after the run already failed ([Use graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api); [GRAPH_RECURSION_LIMIT](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)). The step counter lives in `config["metadata"]["langgraph_step"]` ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

**Watchdogs (stall vs loop).** Claude Agent SDK: `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` default **600,000** (10 min) for `run_in_background` subagents — resets on each stream event; abort, mark task failed, surface partial result to parent. `CLAUDE_ENABLE_STREAM_WATCHDOG=1` + `CLAUDE_STREAM_IDLE_TIMEOUT_MS` default **300,000** (clamped to that minimum) aborts when headers arrived but the body went idle; then the normal retry path ([TypeScript options](https://code.claude.com/docs/en/agent-sdk/typescript); [Python](https://code.claude.com/docs/en/agent-sdk/python)). These catch **hung sockets**, not **repeating tools**. Repeating tools need a **hash of `(tool_name, canonical_args)`** — AutoGPT issue #1994 / #3444 proposed exactly that in 2023; PR #12499 shipped it in 2026 ([issue #1994](https://github.com/Significant-Gravitas/AutoGPT/issues/1994)).

**Cost cap vs hop cap.** A 10-turn ReAct support bot can be cheap if the prefix caches (02) and hops are short. A 10-turn research agent with web search **$10/1k calls** (01) plus growing observations is a different bill. `max_budget_usd` is the fuse that tracks **dollars**; `max_turns` tracks **model calls**; `recursion_limit` tracks **graph super-steps**. Ship all three for production agents. LangGraph has **no** built-in dollar cap — add middleware that reads `usage` (01) and jumps to END.

**Infinite-loop detection is not `recursion_limit`.** The Pregel counter fires even on a *healthy* long research trace. Detection is: (a) identical `(tool, args)` N times (AutoGPT); (b) oscillating name-window (A-B-A-B); (c) no new information in observations (exact string of last k `tool_result`s); (d) wall-clock stall watchdogs (Claude 300 s / 600 s). (a)–(c) belong **before** the model samples the next action so you do not pay another decode. (d) belongs in the HTTP layer so a hung provider does not hold a worker forever. AutoGPT SmartDecisionMaker still uses `agent_mode_max_iterations` as a last-resort `finished` yield ([smart_decision_maker.py](https://github.com/Significant-Gravitas/AutoGPT/blob/f482eb66/autogpt_platform/backend/backend/blocks/smart_decision_maker.py)) — iteration cap without (a) is how you get 51 minutes of empty `input: {}`.

---

## 2. Token Economics & NFR Metrics

List prices and cache multipliers: **see 01**. Tool-schema tax every hop: **see 02**. This section prices **hops**, not SKUs. `$ per 1k completed tasks` is **[inferred]** from published rates × stated hop counts, not a vendor product. ⚠️ OpenAI/Anthropic/Google do **not** publish p50/p95/p99 for *agent loops*. Missing percentiles are marked.

### 2.1 Cost per hop (why ReAct is quadratic-ish)

Each interleaved hop is a **full** `messages.create` / `responses.create` / `invoke`. Billed: uncached input (new observations + any mutated prefix) + cache read (stable prefix, 02) + output (including thinking/reasoning tokens, 01) + tool SaaS (web search $10/1k, 01).

ReWOO Eq. (1) vs (2): interleaved input tokens duplicate \(C\) and \(S\) every step; batched plan pays \(C+S\) **once** for the planner plus a Solver pass ([ReWOO](https://arxiv.org/html/2305.18323v1)). LLMCompiler Table 2 is the empirical version: Movie Rec ReAct **20,000** input tokens vs LLMCompiler **2,800** (**~7.1×** fewer in) at **higher** accuracy ([LLMCompiler](https://arxiv.org/html/2312.04511v3)).

**[inferred] support-agent hop, GPT-6-sol short-context (01: $2 in / $0.20 cached / $2.50 cache write / $10 out).** Assumptions: 8k frozen prefix (system+tools), 400 output tokens/turn, 600 new input tokens/turn (user/tool obs), cache hit on prefix from turn 2. Formula: turn 1 writes 8k at $2.50/M + 0.4k out; later turns read 8k at $0.20/M + 0.6k uncached in + 0.4k out.

| Model hops | Uncached in | Cached in | Out | **[inferred] $ / run** | **$ / 1k runs** |
| --- | --- | --- | --- | --- | --- |
| 1 (no tools) | 8.0k write | 0 | 0.4k | 0.024 | **24** |
| 3 (2 tool rounds) | 8.0k write + 1.2k | 16k | 1.2k | 0.0376 | **38** |
| 10 (Agents SDK default) | 8.0k write + 5.4k | 72k | 4.0k | 0.0852 | **85** |
| 25 model calls | 8.0k write + 14.4k | 192k | 10.0k | 0.187 | **187** |

If the prefix **mutates** every hop (tool list shuffle, timestamp in system — 02), cache hit rate → 0. Ten hops of ~8k uncached in + 4k out ≈ **[inferred] 80k×$2/M + 4k×$10/M = $0.20/run ($200/1k)** — ~**2.3×** the cached 10-hop row, **before** growing observations.

**Sonnet 5 same shape (01: $2 / $0.20 read / $2.50 write / $10 out):** numerically close to GPT-6-sol on this toy. Real delta is **hidden tool-system tokens** (Sonnet 5 auto **354** / any **474** even with one empty tool — 02) added **every** hop.

**AutoGPT footnote:** “Single request to solve a multi-step task with Auto-GPT easily exceeds $1 (excluding API costs)” at 2023 prices ([ReWOO](https://arxiv.org/html/2305.18323v1)). Treat as historical color, not a 2026 SKU.

### 2.2 Reflection / critic extra calls

Self-Refine: 1 generate + up to **4** × (feedback + refine) = **up to 9** LLM calls vs 1. If each call is ~2k in / 800 out on Sonnet 5 uncached: **[inferred] 9 × $0.012 = $0.108/run ($108/1k)** vs one-shot **$12/1k** (01’s 2k/800 worked example) — **9×** for gains that are **~0 pp on math** and large on dialogue ([Self-Refine Table 1](https://proceedings.neurips.cc/paper_files/paper/2023/file/91edff07232fb1b55a505a9e9f6c0ff3-Paper-Conference.pdf); 01 economics).

Reflexion: **N trials × ReAct trajectory** plus one reflection call per failure, and the reflection text is **prefix** on the next trial (cache-busting if you append it into the cached region — 02). AlfWorld used **12** trials. **[inferred]** 12 × 10-hop cached sol ≈ **$1.02/task** model-only if every trial runs to the SDK cap — why Reflexion is a **batch eval** pattern, not a chat SKU, unless you cap trials at 2–3 and have a hard evaluator.

CRITIC: initial CoT + up to **3** correction rounds (math) or **7** interactions (QA). Each correction can include tool tokens. CRITIC\* (oracle: only correct wrong answers) is **not** a production policy unless you already know the label ([CRITIC](https://arxiv.org/abs/2305.11738)).

Huang’s intrinsic loop: GPT-4 GSM8K **5 calls** to go **95.5 → 89.0**. You paid **5×** to **lose 6.5 pp**. GPT-3.5 CommonSenseQA **75.8 → 41.8** after the same pattern — the self-correction prompt **biases** the model off a correct multiple-choice option ([Huang Table 3](https://arxiv.org/abs/2310.01798)). Equal-cost alternative they measured: self-consistency / extra samples beat debate. If you have budget for 5 calls, prefer **parallel samples + vote** or **one call + a tool verifier**, not 2 rounds of “are you sure?”

LATS / ToT: research spend. WebShop LATS **1,066 s** and **30 trajectories** vs LLMCompiler **~11 s** ([LLMCompiler Table 3](https://arxiv.org/html/2312.04511v3)). Do not put MCTS on the customer-support hot path.

### 2.3 p50 / p95 hops and latency

⚠️ **No vendor publishes agent-loop percentiles.** What exists:

| Source | Number | What it is |
| --- | --- | --- |
| ReAct HotpotQA | Cap **7**; extra steps recover **0.84%** of correct traj. | Paper fuse, not p95 |
| ReAct ALFWorld | Cap **49** env steps | Notebook |
| LLMCompiler HotpotQA | ReAct† **7.12 s**; Compiler **3.95 s**; OAI parallel **4.42 s** | Mean latency, 2023 GPT-3.5-turbo-1106 |
| LLMCompiler Movie Rec | ReAct† **20.47 s**; Compiler **5.47 s** | Mean |
| LLMCompiler WebShop | ReAct gpt-3.5 **5.98 s**; LATS **1,066 s**; Compiler **10.72–10.48 s** | Mean; LATS N=50 |
| LLMCompiler planner overhead | **1.88 s** plan + **1.62 s** answer on Movie Rec | Serial remainder |
| Streaming planner | up to **1.3×** on ParallelQA | Hides plan behind tool I/O |
| OpenAI default | **10** turns | Cap, not p50 |
| LangGraph 1.0.6+ | default **1000** super-steps | Cap |
| Claude stall watchdog | **600 s** async subagent; **300 s** stream idle | Stall, not hop p95 |

**[inferred] sequential ReAct wall-clock:** \(T \approx \sum_i (\mathrm{TTFT}_i + T_{\mathrm{decode},i} + T_{\mathrm{tool},i})\). p99 is dominated by the **slowest tool** + **longest decode**, not average TTFT (01/03 nested timeouts). Fan-out DAG: p99 ≈ max(worker p99) + join LLM. HITL: p99 is the **human SLA**, not the model.

Measure: turn count, cached vs uncached tokens, tool latency histogram, `langgraph_step`, `total_cost_usd`, `num_turns`. Do not invent a p95 in an architecture review.

### 2.4 `$ per 1k completed tasks` mix **[inferred]**

Support bot, 1k conversations/day, 70% 1-hop sol, 25% 3-hop, 5% 10-hop, 80% prefix cache hit, ignore tool SaaS:

`0.7×1000×$0.024 + 0.25×1000×$0.038 + 0.05×1000×$0.085 ≈ $16.8 + $9.5 + $4.3 ≈ **$31/day model**`.

Add 200 web searches × $10/1k = **$2**. A runaway 25-hop fleet at 1k/day is **~$187/day** — **~6×** — which is why max-turns is a **financial** control. Reflection-on-every-ticket (9 calls) on the 5% tail: **+~$5/day** if you already filtered to that tail; **+$108/day** if you Self-Refine everyone.

Research agent (plan once + 8 parallel workers + 1 solver): closer to LLMCompiler Movie Rec **$3.04/1k** at 2023 prices, or Anthropic orchestrator-workers: 1 Sonnet plan + N Haiku workers (01 Haiku $1/$5). Parallelism buys **latency and planner-token avoidance**, not free output tokens.

---

## 3. Distributed Resilience & State

### 3.1 Durable loop state (checkpointer)

LangGraph checkpointers snapshot **at super-step boundaries**, not mid-node. Threads: `configurable.thread_id` is the primary key. No `thread_id` ⇒ no save, no interrupt resume ([Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [checkpoint README](https://github.com/langchain-ai/langgraph/blob/5931a5f0/libs/checkpoint/README.md)). Production: `thread_id = f"{tenant}:{user}:{session}"` — a constant string shares history across tenants.

**Durability modes:**

| Mode | Persist when | Crash mid-hop |
| --- | --- | --- |
| `"sync"` | Before the next super-step | Safest; slowest |
| `"async"` (common default) | While the next step runs | Small risk of losing the latest checkpoint |
| `"exit"` | Only when the graph exits (success, error, or interrupt) | **Lose** all mid-run state on pod kill |

([Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). **Pending writes:** if one node fails mid-super-step, successful siblings’ writes are kept so resume does not re-run them ([checkpoint README](https://github.com/langchain-ai/langgraph/blob/5931a5f0/libs/checkpoint/README.md)).

**Crash mid-hop.** The interrupted **node restarts from the top** on resume. Side effects before `interrupt()` run **twice** unless idempotent (03 Stripe keys; Temporal Activity id). `interrupt(value)` requires a checkpointer; resume with `Command(resume=...)` and the **same** `thread_id`. Multiple interrupts match resume values **by order**. Event streaming v3: `stream.interrupted` / `stream.interrupts` ([Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts); [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

OpenAI Agents SDK: default `Runner.run` is **stateless** across calls unless you attach a Session or pass `RunState` / `previous_response_id` / `conversation_id`. Resume after `cancel(mode="after_turn")` uses `RunState` ([running agents](https://openai.github.io/openai-agents-python/running_agents/)).

Claude Agent SDK: `continue_conversation` / `resume` session id; `ResultMessage` carries `session_id` ([Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)).

ADK `LoopAgent`: `LoopAgentState` (`times_looped`, `current_sub_agent`) for resumable invocations; `ctx.reset_sub_agent_states` at the end of a full cycle ([loop_agent.py](https://github.com/google/adk-python/blob/main/src/google/adk/agents/loop_agent.py)).

**Compaction vs the loop (cite 02, do not re-implement here).** A 25-hop ReAct transcript will blow the context window even if `recursion_limit` is 1000. Summarization middleware (LangChain / Deep Agents) is a **second sampling pass** billed as output then re-injected as input (02). Compaction **must not** rewrite the cached prefix or you pay a full cache miss on every hop after the first summary. Put summaries in the **mutating suffix**. Deep Agents `SummarizationMiddleware` trigger example: **100k tokens** (`customization` docs) — that is a **context** fuse, orthogonal to `recursion_limit`.

Compose: **LangGraph cognition inside Temporal/Inngest** so replay does not re-bill tokens — LLM call and tool I/O as Activities (03). Disable nested SDK retries so **one** owner retries (03). Checkpointer = thread memory for HITL/crash; Store = cross-thread facts. A Reflexion buffer belongs in the Store (or a blob) with TTL, not in every checkpoint’s `messages` channel, or Postgres bloats linearly with hop count.

**OpenAI `cancel(mode="after_turn")`.** Cooperative: finish the current model+tool turn, persist `RunState`, return. Cancelling **mid-stream** without persisting tool results is the same class of bug as killing a LangGraph node mid-`ToolNode`: the model will retry the tool on resume unless the adapter is idempotent (03).

### 3.2 Max-iter as a circuit, not a success signal

`GraphRecursionError` means **you did not design a stop**. Catching it and returning “here is a partial answer” is acceptable **only** if you persisted pending writes and did not leave a payment Activity in doubt. OpenAI `error_handlers["max_turns"]` is the explicit version of that policy ([running agents](https://openai.github.io/openai-agents-python/running_agents/)). Claude `error_max_turns` has **no** `result` field — you must not treat it as a successful assistant message ([Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)).

`create_react_agent`’s “Sorry, need more steps…” is a **silent success-shaped** failure: HTTP 200, useless text, **no** exception. SQL agents historically hit this under the default 25-step fuse ([SO #79446089](https://stackoverflow.com/questions/79446089/langgraph-create-react-agent-with-sqltoolkit-issue-sorry-need-more-steps-to-pr)). Raise `recursion_limit` **and** add a completion node; do not only raise the fuse.

Deep Agents: raising parent `recursion_limit` to 150/300 **does not** save subagents — they still die at **25** unless you pass config through `SubAgentMiddleware` ([issue #1698](https://github.com/langchain-ai/deepagents/issues/1698)). Raising dcode to **2000** (or historically **9,999**) only **delays** an infinite loop; pair with `--max-turns` / tool-hash circuit.

### 3.3 Poison repeating tool

ReAct Table 2: **47%** of failures include repetitive TAO. LLMCompiler: ~**10%** of HotpotQA ReAct examples needed **>4** calls and usually looped or diverged; Movie Rec ReAct often searched **<8** movies then stopped early ([LLMCompiler appendix](https://arxiv.org/html/2312.04511v3)). AutoGPT #1994: same action hashed → increment counter → bail ([issue #1994](https://github.com/Significant-Gravitas/AutoGPT/issues/1994)).

**Circuit (control plane):**

1. Canonicalize args (JSON key sort; strip ephemeral timestamps).
2. Key = `(tool_name, args_hash)` or `(tool_name, args_hash, output_hash)` for “same in and out.”
3. After **N=3** identical failures: inject a **hard** observation (“this tool failed identically 3 times; do not retry; answer or replan”) and optionally **remove** that tool from `tool_choice` / `allowed_tools` for the rest of the run (03 cache-preserving allowlist).
4. After **N=6** empty calls: abort the **stream**, not just the tool (AutoGPT).
5. Pagination: cap `page` / `offset`; never let the model invent the next page forever.

WatchdogComponent (AutoGPT Forge) is a **mode switch**, not a proof of progress ([Forge](https://github.com/Significant-Gravitas/AutoGPT/blob/master/docs/content/forge/components/built-in-components.md)). Prefer a deterministic counter over “ask the model if it is looping.”

Idempotency of the **downstream** POST is independent: a looping `create_charge` with a **new** model-invented UUID is a duplicate-charge bug (03). Derive keys from `call_id` / workflow ids, not from the planner.

---

## 4. Security

### 4.1 The loop as an untrusted planner

The planner (ReAct thought, P&E plan JSON, HuggingGPT task list, LLMCompiler DAG) is **untrusted natural language / JSON**. OWASP LLM01:2025: user and **retrieved** content can alter behavior; RAG does not fix injection ([LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)). LLM06 Excessive Agency: the LLM dynamically chooses extensions; hallucination **or** injection can cause damaging actions; log, rate-limit, least privilege ([LLM06](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM06_ExcessiveAgency.md)).

**Goal hijack** (OWASP agentic ASI): deceptive **tool outputs**, artefacts, forged A2A messages, or poisoned docs redirect **multi-step** goals, not just one reply ([hve-core ASI01](https://github.com/microsoft/hve-core/blob/main/.github/skills/security/owasp-agentic/references/01-agent-goal-hijack.md)). **PlanFlip** (arXiv:2607.16199): planning-phase injection in tool outputs / retrieved docs; **cascade** — one poisoned context entry redirects all \(n\) sub-tasks; keyword filters can have **DR = 0.00** on disguised templates ([PlanFlip](https://arxiv.org/html/2607.16199v1)). MCP: tool **descriptions** loaded at session init are an injection channel (Invariant Labs tool poisoning, Apr 2025; cross-server shadowing; CVE-2025-54136 CVSS 8.8) ([OWASP PR #841](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/pull/841)).

**Control-plane rules:**

1. Treat observations as **data**, not instructions (02 XML/Markdown delimiters).
2. Authorize at the **tool boundary** (03 RBAC × arg shape), not in the prompt.
3. Least privilege **per plan node**: one named tool per P&E step (LangChain planning blog’s secure variant direction).
4. Re-validate authorization **at execution**, not only at plan-approval time (LLM06 / MCP rug-pull).
5. Lock the user goal; detect plan drift (new exfiltration tool, new destination email) → HITL.

### 4.2 HITL on irreversible tools

LangGraph `HumanInTheLoopMiddleware(interrupt_on={"send_email": True})` matches `tool.name` ([middleware overview](https://docs.langchain.com/oss/python/langchain/middleware/overview)). Deep Agents `interrupt_on` is the same hook. OpenAI Agents SDK: HITL + `AbortSignal` (JS `signal`). Claude Agent SDK: `permission_mode` and permission denials on `ResultMessage`. ADK: `requested_tool_confirmations` on `EventActions` ([event_actions.py](https://github.com/google/adk-python/blob/7de5bc54/src/google/adk/events/event_actions.py)).

Show an **uneditable, sanitized** preview of tool name + args. Do not let the model narrate “I will refund $X” without the actual args in the interrupt payload. Resume values must not be concatenable into a new tool call without re-RBAC.

Irreversible class: payments, email send, production writes, account close, data export. Read-only search can stay autonomous. LLM06 mailbox example: summarize-email agent with **send** capability + injected email → exfiltrate inbox ([LLM06](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM06_ExcessiveAgency.md)).

### 4.3 Prompt injection via observations

Indirect injection lives in **Wikipedia snippets, web-search pages, ticket bodies, MCP tool results**. ReAct’s whole point is to **attend** to those tokens. Defense is **not** “the model is aligned”:

- Separate untrusted content (LLM01).
- Citation / quotes-then-answer (02) so the model cannot treat “ignore previous instructions” inside a tool blob as policy.
- `allowed_tools` per hop so an observation cannot enable `shell` that was not in the original allowlist (03).
- Output guardrails on the **final** user-visible message (OpenAI Agents SDK tripwires).
- For P&E: parse the plan as **schema-constrained JSON** (01/03 structured outputs); reject unknown tool names before the executor runs.

### 4.4 PII in traces

OpenAI Agents SDK: `trace_include_sensitive_data` / `OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA` (default **true** unless env is false). If false, spans still emit but **LLM I/O and tool args/outputs are stripped** ([run_config.py](https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py); [JS tracing](https://openai.github.io/openai-agents-js/guides/tracing/); [JS running agents](https://openai.github.io/openai-agents-js/guides/running-agents/)). LangSmith / checkpoint blobs store **full state** including messages — treat PostgresSaver as a **PII store** (retention, encryption, tenant isolation). Reflexion episodic buffers and Self-Refine histories are **durable prompt injection + PII** surfaces. Do not log raw `tool_result` from CRM/PII tools to the default trace exporter.

---

## 5. Failure Modes

### 5.1 Infinite loops

Causes: no stop condition; `max_turns=None` + open-ended prompt (“improve this codebase”); Deep Agents parent 1000/2000/9999 with a cycle; ADK `LoopAgent` without `max_iterations` **and** without `escalate`; AutoGPT empty `input: {}` after context saturation; ReAct greedy repeat (47% reasoning-error bucket).

Mitigations: three fuses (hops, dollars, wall-clock watchdog); hash circuit; `RemainingSteps` graceful END; never disable **all** caps in prod.

### 5.2 Oscillating tools

A↔B ping-pong (search then lookup then search; manager↔worker delegation). Distinct from identical-args repeat: the hash must include a **window** (last \(k\) tool names) or an **edit distance** on args. CrewAI-style hierarchical ping-pong is the multi-agent version — disable worker delegation unless required. Oscillation also happens when error text says “try the other tool” and both fail.

### 5.3 Plan drift

P&E / HuggingGPT / LLMCompiler emit a plan, then observations **rewrite goals**. PlanFlip is the adversarial case; benign drift is “step 3 failed so invent step 9: email the customer” when the original task was “summarize.” LangGraph replanner prompt says **only add steps that still NEED to be done** — models still append. Cap `max_replans` (e.g. 2–3). Re-check the frozen user goal after every replan. HuggingGPT: infeasible plans throw workflow exceptions; that is better than silently drifting ([HuggingGPT limits](https://papers.neurips.cc/paper_files/paper/2023/file/77c33e6a367922d003ff102ffb92b658-Paper-Conference.pdf)).

### 5.4 Reflection that increases tokens without quality

Huang: GPT-4 GSM8K **−6.5 pp** after 5 calls. Self-Refine math **~0**. Reflexion without tests **−8 pp** (52 vs 60). CRITIC w/o Tool toxicity **worse**. Self-Refine acronym **non-monotonic**. Production rule: **no critic without a checker** on math/code/facts; cap M=1 on open-ended chat; store reflection **out of** the cached prefix (02) or you pay cache-bust **and** extra calls.

### 5.5 `max_turns` cutting success

ReAct paper: 7-step cap left almost no extra juice (0.84%). Production SQL ReAct: “Sorry, need more steps” **before** the answer. OpenAI default **10** is enough for “lookup + refund decision” and **not** for “refactor auth + tests” (Claude’s own example uses **4** tool-use turns for a small fix). Claude `max_turns=2` stops **before the edit**. Deep Agents research tasks need hundreds of super-steps — 25 on a subagent looks like a model failure. **Measure success vs hop histogram** before you pick the number. `error_handlers` / remaining-steps wrap-up beats a raw exception in UX, but it **is** a failed task in eval.

### 5.6 Other loop-specific failures

| Failure | Exhibit | Control |
| --- | --- | --- |
| Early stop (under-acting) | LLMCompiler Movie Rec ReAct searched **<8** movies | DAG / required cardinality; not more CoT |
| Premature `finish[]` | ReAct HotpotQA | Don’t trust model stop; require verifier |
| `pause_turn` treated as `end_turn` | Anthropic server tools | Resend assistant content |
| `max_tokens` mid-`tool_use` | Anthropic stop reasons | Raise `max_tokens`; incomplete JSON (03) |
| Budget overshoot | Claude `max_budget_usd` | Check after call; pad the cap |
| Subagent fuse ≠ parent fuse | Deep Agents #1698 | Propagate `recursion_limit` |
| Soft fuse looks like success | “Sorry, need more steps…” | Metric on that exact string |
| HITL node replay | LangGraph interrupt | Idempotent tools (03) |
| Trace PII leak | default `trace_include_sensitive_data=true` | Env false in prod |

---

## 6. Scenarios

### 6.1 Customer-support ReAct

**Job:** 1–4 hops: retrieve ticket + policy + optional mutation (refund, email). Latency SLO is **seconds**. Policy must not drift.

**Topology:** Interleaved ReAct via `create_agent` or OpenAI `Runner`. **Not** a 12-trial Reflexion. **Not** LATS. Parallel `lookup_customer` + `lookup_policy` in **one** batched-action hop if independent (03 parallel rules). Irreversible `issue_refund` / `send_email` behind HITL middleware.

**Fuses:** `max_turns=8` (OpenAI) or `recursion_limit` such that ~4 tool rounds remain (`RemainingSteps` wrap-up at 2). `max_budget_usd` e.g. **$0.25** per ticket. Hash circuit N=3 on `search_kb`. Prefix-stable tools array (02). `trace_include_sensitive_data=false`.

**Economics [inferred]:** mix in §2.4 ≈ **$31/day** model at 1k tickets + search SaaS. A 25-hop runaway is the incident.

**Resilience:** checkpointer `thread_id=tenant:ticket_id`; durability `sync` if you HITL a refund (do not lose the interrupt). Stripe idempotency from `tool_use_id` (03).

**Security:** ticket body is **untrusted observation**. Allowlist: `get_ticket`, `search_policy`, `issue_refund` (HITL), `send_email` (HITL). No shell, no arbitrary MCP.

**Eval:** success = correct policy citation + correct refund amount, **not** “the loop ended.” Track hops p50/p95 yourself; vendors will not. Instrument: `n_turns`, `cached_input_tokens`, `tool_repeat_count`, HITL wait, `MaxTurnsExceeded` rate. A 1% infinite-loop rate at 1k tickets/day is **10** runaway traces; at 25 hops that is **[inferred] ~$1.9 extra/day** on sol — small money, large incident review if one of them refunded twice.

### 6.2 Batch plan-and-execute research agent

**Job:** multi-hop web research, hours OK, parallel searches, one synthesis. Failure mode is **cost and loop**, not 800 ms p95.

**Topology:** LLMCompiler-style DAG or ReWOO Planner/Worker/Solver, **not** interleaved ReAct on the planner. HuggingGPT-like `dep` placeholders if tools are heterogeneous. Joiner replans **at most 2** times on empty evidence. Optional CRITIC **with search** on the final answer (not Self-Refine-only). Reflexion **only** if you have a grader and cap trials at 2.

**Fuses:** parent `recursion_limit` 200–1000; **explicit** `max_tool_calls` and `max_replans`; `max_budget_usd` (e.g. **$2–5** per job); wall-clock job timeout; worker subagents must get their **own** limit (Deep Agents #1698). Web search count cap (each call $10/1k — 01).

**Economics:** LLMCompiler showed **3.37–6.73×** cheaper than ReAct on parallel-ish tasks **in 2023 tokens**; the structural win remains: **do not re-send the planner prefix every search**. Cache the planner system+tools (02). Run workers on a small model (01 luna/Haiku).

**Resilience:** persist the **plan object** (`plan`, `past_steps`, worker results in blob store). Temporal: one Activity per worker; Continue-as-needed so history does not hold 500 KB snippets (03). Crash mid-worker: pending writes / Activity retry, not a full replan.

**Security:** every search snippet is PlanFlip-class untrusted. Constrain the plan schema (tool names enum). HITL before any **write** or **send**. Strip PII from traces; research pages often contain personal data.

**When not to use P&E:** live conversation where the next tool **must** depend on the last observation in a way the planner cannot foresee (troubleshooting a unique production incident). Then ReAct with a tight hop cap. When not to use ReAct: 8 independent searches — you will pay ReAct’s **20k** input tokens to do Movie-Rec-shaped work ([LLMCompiler Table 2](https://arxiv.org/html/2312.04511v3)).

**Hybrid that ships:** outer P&E (plan 5–15 steps, DAG where possible) + **inner** ReAct executor **per step** with `max_turns=3` and a **single-tool allowlist**. That is Anthropic orchestrator-workers with a fuse on each worker. The inner ReAct handles observation-dependent micro-decisions; the outer plan prevents goal hijack from cascading across the whole job (PlanFlip). Cap inner loops so a poisoned observation can waste **3** hops, not **1000** super-steps.

### 6.3 Decision table (interview)

| Signal | Prefer ReAct (interleaved) | Prefer plan-and-execute / DAG |
| --- | --- | --- |
| Next action depends on last observation | Yes | Only if you replan (pay Joiner) |
| Many independent tool calls | Wasteful (ReWOO 5×; Compiler 6.7×) | Yes |
| Need user-visible thoughts / citations | ReAct traces | Plan JSON + Solver summary |
| Hard verifier exists | Optional short Reflexion/CRITIC | Verify at join |
| Irreversible tools | HITL per call | HITL per node; fewer planner hops to attack |
| Latency SLO < few seconds | Small hop cap | Only if workers are actually parallel and fast |

---

## Sources

1. https://arxiv.org/abs/2210.03629
2. https://arxiv.org/html/2210.03629v3
3. https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/
4. https://github.com/ysymyth/ReAct/blob/master/hotpotqa.ipynb
5. https://github.com/ysymyth/ReAct/blob/master/alfworld.ipynb
6. https://openai.github.io/openai-agents-python/running_agents/
7. https://openai.github.io/openai-agents-python/ref/run/
8. https://openai.github.io/openai-agents-python/ref/exceptions/
9. https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py
10. https://github.com/openai/openai-agents-python/blob/48ff99bb736249e99251eb2c7ecf00237488c17a/src/agents/run.py
11. https://openai.github.io/openai-agents-js/guides/running-agents/
12. https://openai.github.io/openai-agents-js/guides/tracing/
13. https://openai.github.io/openai-agents-js/openai/agents/type-aliases/runconfig/
14. https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works
15. https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
16. https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools
17. https://www.anthropic.com/engineering/building-effective-agents
18. https://platform.claude.com/cookbook/patterns-agents-orchestrator-workers
19. https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb
20. https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/evaluator_optimizer.ipynb
21. https://code.claude.com/docs/en/agent-sdk/agent-loop
22. https://code.claude.com/docs/en/agent-sdk/python
23. https://code.claude.com/docs/en/agent-sdk/typescript
24. https://github.com/anthropics/claude-agent-sdk-python/blob/main/examples/max_budget_usd.py
25. https://docs.langchain.com/oss/python/langgraph/graph-api
26. https://docs.langchain.com/oss/python/langgraph/use-graph-api
27. https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT
28. https://docs.langchain.com/oss/python/langgraph/checkpointers
29. https://docs.langchain.com/oss/python/langgraph/interrupts
30. https://github.com/langchain-ai/langgraph/blob/5931a5f0/libs/checkpoint/README.md
31. https://reference.langchain.com/python/langgraph/checkpoints
32. https://reference.langchain.com/python/langchain/agents/create_agent
33. https://github.com/langchain-ai/langchain/blob/8b21400627672754a56350e552d39b23f77206a3/libs/langchain_v1/langchain/agents/factory.py
34. https://docs.langchain.com/oss/python/langchain/middleware/overview
35. https://docs.langchain.com/oss/python/langchain/middleware/custom
36. https://www.langchain.com/blog/how-middleware-lets-you-customize-your-agent-harness
37. https://reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent
38. https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py
39. https://stackoverflow.com/questions/79446089/langgraph-create-react-agent-with-sqltoolkit-issue-sorry-need-more-steps-to-pr
40. https://stackoverflow.com/questions/78337975/setting-recursion-limit-in-langgraphs-stategraph-with-pregel-engine
41. https://www.langchain.com/blog/planning-agents
42. https://www.langchain.com/blog/plan-and-execute-agents
43. https://github.com/langchain-ai/langgraphjs/blob/main/examples/plan-and-execute/plan-and-execute.ipynb
44. https://github.com/langchain-ai/deepagents/blob/bdc7da64/libs/deepagents/deepagents/graph.py
45. https://github.com/langchain-ai/deepagents/issues/1698
46. https://github.com/langchain-ai/deepagents/issues/2826
47. https://github.com/langchain-ai/deepagents/pull/4994
48. https://github.com/langchain-ai/deepagents/pull/5882
49. https://github.com/langchain-ai/deepagents/blob/d60560d6/libs/code/deepagents_code/config_manifest.py
50. https://reference.langchain.com/python/deepagents-code/config/config
51. https://docs.langchain.com/oss/python/deepagents/customization
52. https://forum.langchain.com/t/how-to-cap-tool-and-sub-agent-calls-in-deepagents/1653
53. https://arxiv.org/abs/2305.04091
54. https://aclanthology.org/2023.acl-long.147/
55. https://arxiv.org/abs/2305.18323
56. https://arxiv.org/html/2305.18323v1
57. https://arxiv.org/abs/2312.04511
58. https://arxiv.org/html/2312.04511v3
59. https://github.com/SqueezeAILab/LLMCompiler
60. https://arxiv.org/abs/2303.17580
61. https://papers.neurips.cc/paper_files/paper/2023/file/77c33e6a367922d003ff102ffb92b658-Paper-Conference.pdf
62. https://github.com/microsoft/jarvis/blob/main/hugginggpt/README.md
63. https://arxiv.org/abs/2303.11366
64. https://ar5iv.labs.arxiv.org/html/2303.11366
65. https://proceedings.neurips.cc/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf
66. https://arxiv.org/abs/2303.17651
67. https://proceedings.neurips.cc/paper_files/paper/2023/file/91edff07232fb1b55a505a9e9f6c0ff3-Paper-Conference.pdf
68. https://selfrefine.info/
69. https://arxiv.org/abs/2305.11738
70. https://arxiv.org/html/2310.04406
71. https://arxiv.org/abs/2310.04406
72. https://arxiv.org/abs/2212.08073
73. https://arxiv.org/html/2212.08073
74. https://arxiv.org/abs/1805.00899
75. https://arxiv.org/abs/2310.01798
76. https://proceedings.iclr.cc/paper_files/paper/2024/file/8b4add8b0aa8749d80a34ca5d941c355-Paper-Conference.pdf
77. https://arxiv.org/html/2406.01297
78. https://github.com/google/adk-python/blob/main/src/google/adk/agents/loop_agent.py
79. https://github.com/google/adk-python/discussions/4921
80. https://github.com/google/adk-docs/blob/5331a07f/docs/agents/multi-agents.md
81. https://github.com/google/adk-python/blob/7de5bc54/src/google/adk/events/event_actions.py
82. https://github.com/Significant-Gravitas/AutoGPT/issues/1994
83. https://github.com/Significant-Gravitas/AutoGPT/pull/12499
84. https://github.com/Significant-Gravitas/AutoGPT/blob/master/docs/content/forge/components/built-in-components.md
85. https://github.com/Significant-Gravitas/AutoGPT/blob/f482eb66/autogpt_platform/backend/backend/blocks/smart_decision_maker.py
86. https://genai.owasp.org/llmrisk/llm01-prompt-injection/
87. https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM06_ExcessiveAgency.md
88. https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/pull/841
89. https://github.com/microsoft/hve-core/blob/main/.github/skills/security/owasp-agentic/references/01-agent-goal-hijack.md
90. https://arxiv.org/html/2607.16199v1
91. https://reference.langchain.com/python/deepagents/graph/create_deep_agent
92. https://github.com/Significant-Gravitas/AutoGPT/pull/12673
93. https://ccaf-exam.guide/docs/02-agentic-loops/
94. https://mlanthology.org/icml/2024/kim2024icml-llm/
