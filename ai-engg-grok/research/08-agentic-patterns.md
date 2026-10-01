# Research: Agentic Patterns

**Date researched**: 2026-09-30
**Sources consulted**: 24
**Scope note**: Catalog of **control-flow patterns** (workflow vs. agent) — chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer, ReAct, Reflexion, plan-and-execute. Full multi-agent *topology / platform design* is the next topic; orchestrator-workers appears here as a pattern, not as a multi-agent platform architecture.

## 1. System Topology & Mechanics

### Escalation ladder: prompt → workflow → agent

Anthropic and the System Design Newsletter agree on the same control question: **who decides the next step—code or the model?** ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents); [System Design Newsletter — Agentic Design Patterns](https://newsletter.systemdesign.one/p/agentic-design-patterns)).

| Level | Who owns control flow | When to use |
| --- | --- | --- |
| **Single LLM call** | Caller | Summarization, classification, extraction, rewrite, translation, code with clear specs ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)) |
| **Workflow** | Predefined code paths + LLMs/tools as steps | Steps known before runtime; validation gates between steps ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)) |
| **Agent** | LLM dynamically directs process and tool use | Number/type of steps unknown until observations arrive ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)) |

Anthropic’s production rule: find the simplest solution; agentic systems **trade latency and cost for better task performance** ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Newsletter escalation trigger: you write more exception-handling code than real work, or keep adding unplanned special cases—then leave pure workflows for agents ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)). Do **not** escalate at “70–80% prototype”—usually fix prompts/gates first ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).

**Control plane vs. data plane** [inferred from pattern defs]:

| Plane | Workflow patterns | Agent patterns |
| --- | --- | --- |
| **Control** | App/graph edges, routers, fan-out/fan-in, recursion limits | Constraints: tool allowlist, spend/iteration caps, stop conditions |
| **Data** | Per-step prompts, intermediate artifacts, merge results | Thought/plan text, tool args, observations, episodic memory |

### Workflow patterns (code controls flow)

Canonical set from Anthropic (Dec 2024), mirrored by Neo Kim (Apr 2026) ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns); [Anthropic cookbook — basic_workflows](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/basic_workflows.ipynb)):

#### 1) Prompt chaining

Decompose into a **fixed sequence** of LLM calls; each consumes prior output. Optional programmatic **gates** abort or repair between steps ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Analogy: CI/CD—compile → lint → test → package; failed gate stops the pipeline ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)). Use when the task cleanly decomposes into fixed subtasks; goal is **accuracy via easier per-call tasks at the cost of latency** ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)).

#### 2) Routing

Classify input, then dispatch to a specialized prompt, model, or sub-workflow ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Separation of concerns: optimizing one class must not degrade another. Examples: support intents; easy→Haiku / hard→Sonnet ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)); Sierra cited as routing across **15+** models ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)). Cookbook implements LLM classifier → specialized prompt ([Anthropic cookbook](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/basic_workflows.ipynb)).

#### 3) Parallelization

Two variants ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)):

| Variant | Mechanism | Typical use |
| --- | --- | --- |
| **Sectioning** | Independent subtasks in parallel → merge | Guardrails vs. response; multi-aspect eval; multi-scanner PR security (CodeQL/Snyk/Semgrep cited) |
| **Voting** | Same task, multiple prompts/samples → aggregate | Vulnerability review; content-policy with vote thresholds |

Subtasks must be independently executable; aggregation is programmatic ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)).

#### 4) Orchestrator-workers

Central LLM **dynamically** decomposes, delegates to workers, synthesizes ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Differs from parallelization: subtasks are **not** pre-defined—determined per input (e.g., multi-file code edits) ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [cookbook orchestrator_workers](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb)). Cookbook: analysis/planning phase emits structured (XML) subtasks; workers get original task + specific instructions; sample notes **N+1 LLM calls** (1 orchestrator + N workers) ([Anthropic cookbook](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb)).

**Pattern vs. multi-agent platform**: Newsletter distinguishes orchestrator-workers (single central LLM retains control) from multi-agent systems where agents call each other and **transfer** control ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)). Anthropic’s Research product *uses* an orchestrator-worker *pattern* inside a multi-agent system (lead + parallel subagents)—that platform design is out of scope here; token multipliers from that post appear in §2 as published cost evidence ([Anthropic — Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)).

#### 5) Evaluator-optimizer

Generator LLM produces; evaluator LLM scores/critiques; loop until criteria met or stop ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Fit signals: (1) human feedback demonstrably improves answers; (2) an LLM can supply that feedback (e.g., literary translation nuances; iterative search) ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Newsletter frames it as a reliability layer with **iteration limits and cost control** ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).

### Agent patterns (model controls flow)

#### ReAct (Reason + Act)

Yao et al. interleave **Thought** (verbal reasoning; does not affect Env) with **Action** → **Observation** from tools/environment ([ReAct arXiv:2210.03629](https://arxiv.org/abs/2210.03629); [Google Research blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)). Synergy: reason-to-act (plans, exception handling) and act-to-reason (grounding via Wikipedia/API). HotpotQA/FEVER use dense Thought–Action–Observation; ALFWorld/WebShop allow sparser thoughts ([Google blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)).

Published task lifts (PaLM-540B prompting) ([Google blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)):

| Benchmark | Metric | ReAct | Strong baseline |
| --- | --- | --- | --- |
| ALFWorld (2-shot) | Success % | **71** | Act-only 45; IL ~37 (**+34** pp vs IL) |
| WebShop (1-shot) | Success % | **40** | Act 30.1; IL 29.1 (**+10** pp vs IL) |
| HotpotQA (6-shot EM) | Best ReAct+CoT | **35.1** | Standard 28.7; ReAct alone 27.4 |
| FEVER (3-shot acc.) | Best ReAct+CoT | **64.6** | Standard 57.1; ReAct alone 60.9 |

#### Reflexion (verbal reinforcement / reflection across trials)

Reflexion agents convert sparse feedback into **linguistic self-reflection**, store it in an **episodic memory buffer**, and retry—no weight updates ([Reflexion arXiv:2303.11366](https://arxiv.org/abs/2303.11366)). Components: Actor (often ReAct/CoT), Evaluator, Self-Reflection model, short-term trajectory + long-term reflection memory (bounded; HotPotQA uses memory size **3**) ([Reflexion HTML](https://ar5iv.labs.arxiv.org/html/2303.11366)).

Published lifts ([Reflexion](https://arxiv.org/abs/2303.11366)):

| Setting | Result |
| --- | --- |
| AlfWorld | **+22** pp absolute over strong baselines in **12** iterative learning steps; ReAct+Reflexion **130/134** tasks with hallucination heuristic |
| HotPotQA | **+20** pp over baselines |
| HumanEval Python | **91%** pass@1 vs GPT-4 **80%** (**+11** pp) |
| WebShop | **No useful improvement** after 4 trials—reflections unhelpful when exploration is constrained ([Reflexion NeurIPS PDF](https://papers.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf)) |

**In-trial reflection** (generate → critique → revise in one session) is the newsletter’s “Reflection” pattern and overlaps Anthropic evaluator-optimizer; Reflexion’s distinctive topology is **cross-trial episodic memory** ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns); [Reflexion](https://arxiv.org/abs/2303.11366)).

#### Plan-and-execute (and relatives)

| Variant | Topology | Source |
| --- | --- | --- |
| **Plan-and-Solve (PS / PS+)** | Single-pass prompt: devise plan → carry out steps (zero-shot; no tools required) | [Wang et al., ACL 2023](https://arxiv.org/abs/2305.04091) |
| **Plan-and-Execute agent** | Planner LLM → step executor (often tool agent) → **replan** or finish | [LangChain blog](https://www.langchain.com/blog/planning-agents); BabyAGI + Plan-and-Solve inspiration |
| **ReWOO** | Plan with `#E` variable assignment; workers fill vars; Solver answers—fewer mid-step planner calls | [LangChain blog](https://www.langchain.com/blog/planning-agents) |
| **LLMCompiler** | Planner streams a **DAG** of tool tasks + deps; Task Fetching Unit parallelizes ready nodes; Joiner replan/finish | [Kim et al., 2023/2024](https://arxiv.org/abs/2312.04511); [LangChain blog](https://www.langchain.com/blog/planning-agents) |

Plan-and-Solve PS+ (text-davinci-003) vs Zero-shot-CoT: MultiArith **91.8%**, GSM8K **59.3%** (vs CoT lifts “at least **5%**” on most arithmetic sets, **+2.9%** on GSM8K); CSQA **71.9%** vs CoT **65.2%**; Last Letters **75.2%** vs CoT **64.8%** ([Wang et al.](https://arxiv.org/abs/2305.04091); [ACL PDF](https://aclanthology.org/2023.acl-long.147.pdf)). LangChain cites advantages over ReAct: explicit long-horizon plan; cheaper/smaller models for execution; planner LLM not consulted after every tool call ([LangChain blog](https://www.langchain.com/blog/planning-agents)).

### Autonomous agent loop (Anthropic)

Start from user command → plan/operate with tools → ground each step in Env feedback → optional human checkpoints → terminate on completion or **max iterations** ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Implementation is often “just LLMs using tools based on environmental feedback in a loop”; invest in **ACI** (tool docs, formats, poka-yoke args) as much as prompts ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)).

### Message / execution topology summary

| Pattern | Sync shape | Typical protocol |
| --- | --- | --- |
| Chaining | Sequential round-trips | Gate predicates between calls |
| Routing | 1 classify + 1 handler | Classifier output → keyed prompt/model |
| Parallelization | Fan-out / barrier / merge | Sectioning or voting aggregator |
| Orchestrator-workers | Plan then N workers (+ optional parallel) | Structured subtask list (e.g. XML) |
| Evaluator-optimizer | Alternating generator ↔ critic | Score/feedback until stop |
| ReAct | Strictly sequential LLM↔tool | Append Thought/Action/Observation to context |
| Plan-and-execute | Plan once (or replan) + serial steps | Shared plan state |
| LLMCompiler | Streamed DAG + parallel ready set | Placeholder `$id` substitution |

## 2. Token Economics & NFR Metrics

### Published multipliers (do not treat as universal SLAs)

#### Infrastructure study (Kim et al., 2025 — Llama-3.1-8B, vLLM, agent suite)

([Cost of Dynamic Reasoning arXiv:2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301))

| Metric | Published figure | Notes |
| --- | --- | --- |
| LLM calls vs CoT | Agent suite **~9.2×** more LLM calls than CoT | ReAct, Reflexion, LATS, LLMCompiler averaged |
| LATS extreme | **71.0** LLM calls / request avg | Tree search expands many child nodes |
| Latency mix | LLM **69.4%** / tools **30.2%** of E2E | Hard to overlap; LLMCompiler overlap only **18.2%** of latency |
| Tool latency examples | WebShop tools **~20 ms**; HotpotQA Wikipedia API **~1.2 s**/call | Tool-bound vs LLM-bound workloads |
| Context growth | Input ~**1k** tokens → **3–4×** as history accumulates | Instruction/few-shot fixed; LLM+tool history grow |
| Prefix caching | Prefill **−58.6%** avg; E2E LLM latency **−15.7%** avg | Agents benefit more than CoT |
| KV memory | Tool agents **3.0×** (up to **5.4×**) vs CoT even with prefix cache | Per-request GPU KV |
| Serving throughput | ReAct **5.62×** throughput with prefix cache vs without; ShareGPT only **1.03×** | Multi-call agents amplify cache wins |
| Latency distribution | ShareGPT ~**3–7 s**; ReAct much heavier tail | Step/tool count variance |

#### LLMCompiler vs ReAct (Kim et al.)

([LLMCompiler arXiv:2312.04511](https://arxiv.org/abs/2312.04511); abstract claims up to **3.7×** latency, **6.7×** cost, **~9%** accuracy)

| Benchmark (GPT) | Latency speedup vs ReAct† | Cost reduction (paper §5.1) |
| --- | --- | --- |
| HotpotQA | **1.80×** (3.95 s vs 7.12 s) | **3.37×** |
| Movie Recommendation | **3.74×** (5.47 s vs 20.47 s) | **6.73×** |
| ParallelQA | **2.15×** | Up to **4.65×** (abstract/sec 5.2) |
| Game of 24 vs ToT | **2.89×** (83.6 s vs 241.2 s) | — |

LangChain summarizes LLMCompiler paper claim as **~3.6×** speed boost from DAG parallelism ([LangChain planning agents](https://www.langchain.com/blog/planning-agents)).

#### Anthropic Research system (orchestrator-worker *pattern* at product scale)

([Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system))

| Claim | Value |
| --- | --- |
| Agents vs chat tokens | Agents **~4×** chat |
| Multi-agent vs chat | Multi-agent **~15×** chat |
| Parallelization wall-clock | Up to **90%** research-time cut (3–5 subagents + 3+ parallel tools) |
| Eval lift | Opus lead + Sonnet subagents **+90.2%** vs single Opus on internal research eval |
| Variance drivers (BrowseComp) | Token usage alone **80%** of performance variance; tokens+tools+model **95%** |

#### Pattern-level cost/latency intuition (qualitative + linear models)

| Pattern | Token / latency model | Source |
| --- | --- | --- |
| Prompt chaining | Latency **linear** in steps (5 steps ≈ 5 RTTs); errors cascade unless gated | [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns); [Anthropic](https://www.anthropic.com/engineering/building-effective-agents) |
| Routing | **+1** classifier call; downstream cost = selected tier | [Anthropic](https://www.anthropic.com/engineering/building-effective-agents) |
| Parallelization | Cost **× branches**; wall-clock ≈ max(branch) + merge [inferred] | [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns) |
| Orchestrator-workers | **N+1** LLM calls minimum in cookbook sample | [Anthropic cookbook](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb) |
| Evaluator-optimizer | **2×** per iteration (gen+eval); total = 2 × rounds until stop [inferred] | [Anthropic](https://www.anthropic.com/engineering/building-effective-agents) |
| ReAct | 1 LLM call per Thought/Action step + tool time; history append → quadratic-ish $ growth [inferred from 2506.04301 growth] | [2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301) |
| Reflexion | ReAct cost × trials (up to 12 on AlfWorld) + reflection LLM calls | [Reflexion](https://arxiv.org/abs/2303.11366) |
| Plan-and-execute | Fewer large-model calls than ReAct if executor uses smaller models | [LangChain blog](https://www.langchain.com/blog/planning-agents) |

> ⚠️ Limited public data available for this dimension on **enterprise $/1k executions, p50/p95/p99 SLAs by pattern, and production RPM/TPM under each pattern**. Published numbers above are **benchmark/lab or single-vendor product** figures (PaLM-540B, GPT-4-era APIs, Llama-3.1-8B vLLM, Anthropic internal Research). Do not fabricate cross-vendor SLAs.

**Dynamic model routing**: Anthropic documents easy→smaller / hard→capable routing as a first-class workflow ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Newsletter cites Sierra multi-model routing for cost ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)). Hit-rate statistics for production routers are not published in these sources.

**Prompt / prefix caching**: Agent loops share growing prefixes → high cache affinity; **58.6%** prefill cut and **5.62×** ReAct serving throughput with prefix cache ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)). Vendor TTL/pricing schedules are product-specific (out of scope unless needed for capacity math).

## 3. Distributed Resilience & State

### Pattern-native failure boundaries

| Pattern | Resilience hook | Documented failure |
| --- | --- | --- |
| Chaining | Programmatic **gates** between steps | Errors carry forward if gate misses the failure mode ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)) |
| Routing | Router accuracy caps system | Misroute = quality fail or cost waste; router is SPOF ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)) |
| Parallelization | Explicit partial-failure policy | Retry branch / proceed degraded / fail-all—must decide up front ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)) |
| Orchestrator-workers | Goal tracking + worker merge | Goal drift; over-decomposition; orchestrator bottleneck ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns); [Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system) early bugs: **50** subagents for simple queries) |
| Evaluator-optimizer | Iteration + cost caps | Infinite polish loops without stop ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)) |
| ReAct / agents | Max iterations / sandboxed tools | Compounding errors; open-ended tool loops ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)) |
| Reflexion | Max trials; memory bound | WebShop: reflections don’t help → wasted trials ([Reflexion](https://papers.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf)) |
| LLMCompiler | Joiner replan; DAG deps | Bad plans → wrong parallel fan-out; need replan feedback ([Kim et al.](https://arxiv.org/abs/2312.04511)) |

### Checkpointing & durable execution (framework mapping)

LangGraph checkpointers snapshot state each **super-step**, keyed by `thread_id` (+ optional `checkpoint_id` for time-travel resume). Per-task `checkpoint_writes` enable **pending-writes recovery**: completed parallel nodes not re-run after sibling failure ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). Durability modes: `exit` (persist on exit only), `async` (persist while next step runs; small crash risk), `sync` (persist before next step; highest durability) ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

**Recursion / iteration guards**: default `recursion_limit` **25**; exceed → `GraphRecursionError`. For ReAct-style agents, docs recommend `recursion_limit = 2 * max_iterations + 1` ([LangGraph GRAPH_RECURSION_LIMIT](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT); [LangGraph run agents](https://docs.langchain.com/oss/python/langgraph/agents/overview) pattern cited in community docs).

Anthropic Research: persist plan to **Memory** before context truncation at **200k** tokens so orchestrator can resume strategy after compaction ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)).

> ⚠️ Limited public data available for this dimension on **Temporal/Kafka event-sourcing of agent patterns, distributed locking/leader election specific to pattern choice, and circuit-breaker threshold tuning**. Map patterns onto durable workflows [inferred]: treat each LLM/tool step as an activity; gate/router as deterministic activities; ReAct loop as a workflow with continue-as-new every N turns to bound history.

### Rate limiting & degradation [inferred]

- Routing → cheaper model / human queue on upstream 429s.
- Parallelization → shed voting quorum (e.g., 3→1) under load.
- Agents → tighten `max_iterations` / tool RPM when budgets burn.

No peer-reviewed pattern-specific breaker curves found in consulted sources.

## 4. Enterprise Security & Governance

> ⚠️ Limited public data available for this dimension **tied specifically to named agentic patterns**. Security properties are mostly **tool/ACI and deployment** concerns that apply across patterns. Below: what primary pattern sources *do* say, plus [inferred] enterprise mappings.

### Documented pattern-adjacent controls

| Concern | What sources state |
| --- | --- |
| **Autonomy bounds** | Agents: define tools, spend, stop conditions—not open-ended scripts ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns); [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)) |
| **Sandboxing** | Anthropic: extensive testing in sandboxes + guardrails before autonomy ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)) |
| **Tool / ACI hardening** | Absolute paths over relative; formats easy for models; poka-yoke parameters; tool docs ≈ prompt engineering ([Anthropic App. 2](https://www.anthropic.com/engineering/building-effective-agents)) |
| **Parallel guardrails** | Sectioning: separate model instance screens content while another answers—better than one call doing both ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)) |
| **Human oversight** | Agents pause at checkpoints/blockers; support/coding appendices stress human review ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)); ReAct human-in-the-loop thought edits ([Google blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)) |
| **Delegation scope** | Orchestrator must give workers objective, output format, tools, boundaries—vague tasks → duplicate/misaligned work ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)) |

### [inferred] Pattern → governance mapping

| Pattern | Governance implication |
| --- | --- |
| Chaining | Audit each gate decision; retain intermediate artifacts for SOC2 trail |
| Routing | Log route label + confidence; policy on human escalate classes |
| Voting parallelization | Quorum rules are security-policy (false-positive vs false-negative tradeoff) |
| Orchestrator-workers | Worker tool RBAC ⊆ orchestrator grant; prevent privilege amplification via spawn |
| ReAct / plan-execute | Schema-validate every tool call; deny-by-default tool registry |
| Reflexion | Episodic memory may store PII from failed trajectories—TTL/redact |

Zero-Trust MCP, WASM sandboxes, and formal HIPAA mappings are **not** specified in the pattern papers/blogs consulted—defer to MCP/security topics.

## 5. Production Failure Modes

| Failure mode | Which patterns | Detection / mitigation | Source |
| --- | --- | --- | --- |
| **Cascading bad intermediates** | Chaining | Gates; fail-closed on schema/validation | [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns) |
| **Misclassification** | Routing | Router eval set; shadow dual-route; human fallback | [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns) |
| **Partial branch failure** | Parallelization | Pre-declared retry/degrade/fail-all | [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns) |
| **Goal drift / over-decomposition** | Orchestrator-workers | Effort scaling rules (1 agent / 3–10 tools for simple facts; 2–4 subagents for comparisons; >10 only for complex) | [Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system); [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns) |
| **Duplicate worker work** | Orchestrator-workers | Detailed task boundaries; avoid vague “research X” | [Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system) |
| **Hallucinated CoT without grounding** | CoT-only vs ReAct | ReAct + Env observations; human edit thoughts | [Google ReAct blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/) |
| **Repetitive tool loops** | ReAct / agents | Max iterations; detect repeated action+observation (Reflexion AlfWorld heuristic) | [Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [Reflexion](https://ar5iv.labs.arxiv.org/html/2303.11366); LangGraph `recursion_limit` |
| **Context inflation** | All multi-turn agents | History → **3–4×** input; summarize/memory offload; prefix cache | [2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301); [Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system) |
| **Compounding autonomy errors** | Full agents | Sandbox; stop conditions; measure before complexity | [Anthropic](https://www.anthropic.com/engineering/building-effective-agents) |
| **Evaluator gaming / endless polish** | Evaluator-optimizer | Hard iteration/cost caps; external success criteria | [Anthropic](https://www.anthropic.com/engineering/building-effective-agents) |
| **Useless reflection** | Reflexion | Domain check (WebShop failure); cap trials | [Reflexion](https://papers.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf) |
| **Hallucinated tool parameters** | Any tool-using pattern | ACI poka-yoke; structured/schema tools; absolute paths lesson | [Anthropic App. 2](https://www.anthropic.com/engineering/building-effective-agents) |
| **Synchronous orchestrator stall** | Orchestrator-workers | Lead waits on subagent set; slowest worker blocks | [Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system) |
| **Heavy-tail latency under load** | ReAct serving | p95 blows up vs chatbot ShareGPT as QPS rises | [2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301) Fig. 14–15 |

**Real-world / product post-mortems (pattern-level):** Anthropic Research early failures—spawning **50** subagents on simple queries, endless search for nonexistent sources, noisy inter-agent updates—mitigated via prompt heuristics and effort budgets, not a new topology ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)). Newsletter case study (AI code review combining patterns) is paywalled beyond outline—details not available in free fetch ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).

## 6. Enterprise System Design Scenarios

### When to pick which pattern (trade-off matrix)

| Pattern | Cost | Latency | Ops complexity | Predictability | Best enterprise fit |
| --- | --- | --- | --- | --- | --- |
| Single call | Lowest | Lowest | Lowest | Highest | Classification, rewrite, extract |
| Chaining | Linear in steps | Linear RTTs | Low | High | Contract clause pipelines; outline→doc |
| Routing | Classifier + tiered | +1 hop | Med (router eval) | High if router solid | Support triage; model cost tiers |
| Parallel sectioning | ×N | ~max(N) | Med (merge/partial fail) | High | Guardrails; multi-scanner PR |
| Voting | ×N samples | ~max(N) | Med | Med | Security/policy high-precision flags |
| Orchestrator-workers | N+1+ | Variable | High | Med–low | Multi-file coding; dynamic research fan-out |
| Evaluator-optimizer | ×2×rounds | ×rounds | Med | Med | Translation polish; iterative retrieval |
| ReAct | ~9× LLM calls vs CoT (lab) | Heavy-tail | Med–high | Low | Open tool use; unknown step count |
| Plan-and-execute / LLMCompiler | Often < ReAct $ if parallel DAG | LLMCompiler up to **3.7×** faster than ReAct | High | Med | Multi-tool APIs with known deps |
| Reflexion | × trials | × trials | High | Low | Coding/debug with unit tests; AlfWorld-like |

Sources for cells: [Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns); [2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301); [LLMCompiler](https://arxiv.org/abs/2312.04511); [LangChain](https://www.langchain.com/blog/planning-agents); [Reflexion](https://arxiv.org/abs/2303.11366).

### Capacity planning anchors (published)

| Anchor | Value | Use |
| --- | --- | --- |
| Agent token burn vs chat | **~4×** | Budget per session ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Multi-agent vs chat | **~15×** | Only for high-value parallelizable work ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Parallel research speedup | Up to **90%** wall-clock | 3–5 subagents + parallel tools ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Context growth per agent request | **3–4×** input over steps | Size KV / cache ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) |
| GPU KV vs CoT | **3–5.4×** | Agent cluster sizing ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) |
| Prefix cache on ReAct serving | **5.62×** throughput | Enable KV prefix reuse in serving ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) |

### Case studies (pattern composition)

1. **Customer support**: natural agent fit—conversation + tools + clear resolution metrics; usage-based pricing on successful resolutions cited ([Anthropic App. 1](https://www.anthropic.com/engineering/building-effective-agents)). Often **routing** (intent/sentiment) + **ReAct tools** + human handoff ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns) Intercom Fin).
2. **Coding agents**: verifiable via tests; SWE-bench-style file edits; tool ACI dominates prompt work ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Cursor agent mode cited as orchestrator-workers over codebase slices ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).
3. **Legal / contract review**: chaining extract → risk-classify → summarize high-risk (CoCounsel / Robin AI cited) ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).
4. **Research assistants**: orchestrator-worker pattern with parallel subagents; token spend is the performance driver (**80%** BrowseComp variance) ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Design principles (repeatable)

1. Start simple; add patterns only when evals prove lift ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)).
2. Prefer workflows when steps are enumerable; agents when not ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).
3. Prefer **plan/DAG parallelism** over naïve ReAct when tool graph has independent edges ([LLMCompiler](https://arxiv.org/abs/2312.04511); [LangChain](https://www.langchain.com/blog/planning-agents)).
4. Transparency: show plans/thoughts; craft ACI carefully ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)).
5. Economic viability: multi-agent **~15×** chat tokens—reserve for high-value, parallelizable tasks ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)).

> ⚠️ Limited public data available for **multi-tenant agent cluster RPM, concurrent agents/node, and memory footprint by pattern in production SaaS**—only lab serving curves ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) and Anthropic Research token multiples.

## Sources

- [1] https://newsletter.systemdesign.one/p/agentic-design-patterns — Neo Kim, Agentic Design Patterns (primary article; partial free content)
- [2] https://www.anthropic.com/engineering/building-effective-agents — Anthropic, Building effective agents (workflows + agents)
- [3] https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/basic_workflows.ipynb — Chaining, routing, parallelization samples
- [4] https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb — Orchestrator-workers sample (N+1 calls)
- [5] https://www.anthropic.com/engineering/multi-agent-research-system — Orchestrator-worker pattern at product scale; 4×/15× tokens; 90% parallel speedup
- [6] https://arxiv.org/abs/2210.03629 — Yao et al., ReAct
- [7] https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/ — ReAct results tables
- [8] https://arxiv.org/abs/2303.11366 — Shinn et al., Reflexion
- [9] https://ar5iv.labs.arxiv.org/html/2303.11366 — Reflexion HTML (memory, trials)
- [10] https://papers.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf — Reflexion NeurIPS (WebShop negative result)
- [11] https://arxiv.org/abs/2305.04091 — Wang et al., Plan-and-Solve Prompting
- [12] https://aclanthology.org/2023.acl-long.147.pdf — Plan-and-Solve ACL PDF (tables)
- [13] https://www.langchain.com/blog/planning-agents — Plan-and-execute, ReWOO, LLMCompiler overview
- [14] https://arxiv.org/abs/2312.04511 — Kim et al., LLMCompiler
- [15] https://ar5iv.labs.arxiv.org/html/2312.04511 — LLMCompiler HTML (speedup/cost tables)
- [16] https://ar5iv.labs.arxiv.org/html/2506.04301 — Cost of Dynamic Reasoning (9.2× calls, cache, latency tails)
- [17] https://docs.langchain.com/oss/python/langgraph/checkpointers — Checkpoint durability modes
- [18] https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT — recursion_limit / infinite loops
- [19] https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/plan-and-execute/ — Plan-and-execute tutorial
- [20] https://ysymyth.github.io/papers/react_llm.pdf — ReAct PDF mirror
- [21] https://arxiv.org/pdf/2312.04511v3 — LLMCompiler PDF
- [22] https://proceedings.mlr.press/v235/kim24y.html — LLMCompiler ICML proceedings abstract
- [23] https://github.com/agi-edgerunners/Plan-and-Solve-Prompting — PS/PS+ trigger prompts
- [24] https://github.com/noahshinn024/reflexion — Reflexion code/release referenced by paper
