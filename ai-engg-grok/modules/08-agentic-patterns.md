# Module 08 — Agentic Patterns

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 08 (control-flow patterns after agent/MCP/prompt foundations)  
**Grounded in**: `research/08-agentic-patterns.md` (24 sources, 2026-09-30)

Agentic patterns answer one control question: **who decides the next step—code or the model?** ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents); [System Design Newsletter — Agentic Design Patterns](https://newsletter.systemdesign.one/p/agentic-design-patterns)). Escalation ladder: single LLM call → **workflow** (predefined paths + LLM/tool steps) → **agent** (LLM dynamically directs process and tools). Production rule: find the simplest solution; agentic systems **trade latency and cost for better task performance** ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). This module catalogs workflow patterns (chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer) and agent patterns (ReAct, Reflexion, plan-and-execute / LLMCompiler)—not multi-agent *platform* topology (next topic).

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    CONTROL PLANE                         │
                         │  Pattern router: chain | ReAct | plan-and-execute        │
                         │  gates · max_iterations · spend caps · recursion_limit   │
                         │  circuit breaker · fallback policy · HITL checkpoints    │
                         │                                                          │
                         │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
                         │  │ Classifier │  │ Graph /    │  │ Stop / budget      │  │
                         │  │ + route    │  │ planner    │  │ enforcer           │  │
                         │  └─────┬──────┘  └─────┬──────┘  └─────────┬──────────┘  │
                         └────────┼───────────────┼───────────────────┼─────────────┘
                                  │               │                   │
                                  ▼               ▼                   ▼
                         ┌──────────────────────────────────────────────────────────┐
                         │                     DATA PLANE                           │
                         │  LLM completions · Thought/Action/Obs · plan steps       │
                         │  intermediate artifacts · merge / vote / replan          │
                         └───┬──────────────────────┼───────────────────────┬───────┘
                             │                      │                       │
              ┌──────────────┴───────┐  ┌───────────┴─────────┐  ┌──────────┴───────┐
              │     TOOL PROXIES     │  │    PERSISTENCE      │  │    TELEMETRY     │
              ├──────────────────────┤  ├─────────────────────┤  ├──────────────────┤
              │  schema-validate ACI │  │  LangGraph ckpt     │  │  pattern label   │
              │  MCP allow/deny      │  │  thread_id / plan   │  │  tokens · $ · ms │
              │  sandboxed runners   │  │  episodic memory    │  │  breaker state   │
              │  tool RBAC grant     │  │  decision audit log │  │  correlation IDs │
              └──────────────────────┘  └─────────────────────┘  └──────────────────┘
```

**Plane responsibilities** [inferred from pattern defs + framework docs]

| Plane | Workflow patterns | Agent patterns |
| --- | --- | --- |
| **CONTROL PLANE** | App/graph edges, routers, fan-out/fan-in, gate predicates, recursion limits | Tool allowlist, spend/iteration caps, stop conditions, pattern selection |
| **DATA PLANE** | Per-step prompts, intermediate artifacts, merge/vote results | Thought/plan text, tool args, observations, episodic memory |
| **PERSISTENCE** | Step artifacts + gate outcomes under `thread_id` | Trajectory + plan + reflections; Memory before context compaction |
| **TOOL PROXIES** | Fixed tool set per step; schema validation | Dynamic tool choice within allowlist; ACI poka-yoke |
| **TELEMETRY** | Per-step latency/tokens; route label | Call count vs CoT; pattern-selected path; breaker/degrade flags |

Sources: [Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); research §1.

### End-to-end request-flow — pattern router (chain vs ReAct vs plan-and-execute)

1. **Ingress** — Request arrives with goal, tenant id, optional `thread_id`. CONTROL PLANE loads checkpoint (if any) and budget remaining ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).
2. **Classify / route** — Lightweight classifier (or rules) emits a pattern key: `chain` | `react` | `plan_execute`. Misroute wastes cost or quality—router is an SPOF ([System Design Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)). TELEMETRY records `pattern`, confidence, `correlation_id`.
3. **Branch A — Prompt chaining (workflow)** — CONTROL PLANE walks a **fixed** step list. DATA PLANE runs LLM call \(i\); TOOL PROXIES optional for that step; a programmatic **gate** validates schema/quality. Fail-closed aborts; pass persists intermediate artifact to PERSISTENCE and advances. Latency ≈ linear in steps (5 steps ≈ 5 RTTs) ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).
4. **Branch B — ReAct (agent)** — Loop: model emits Thought (context only) → Action → TOOL PROXIES execute → Observation appended. CONTROL PLANE enforces `max_iterations` / `recursion_limit ≈ 2×max_iterations+1`. History growth drives tokens ([ReAct arXiv:2210.03629](https://arxiv.org/abs/2210.03629); [LangGraph GRAPH_RECURSION_LIMIT](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)).
5. **Branch C — Plan-and-execute** — Planner LLM writes an explicit plan (or DAG). Executor runs steps (often smaller model / tools); Joiner or planner **replans** on failure. Fewer mid-step planner calls than ReAct when deps are known ([LangChain planning agents](https://www.langchain.com/blog/planning-agents); [LLMCompiler](https://arxiv.org/abs/2312.04511)).
6. **Circuit / fallback** — On breaker **open** or budget breach: degrade agent → workflow (chain) → deterministic handler. TELEMETRY flags `degraded=true`.
7. **Persist & audit** — Checkpoint pattern-native state (see Part 4). Immutable decision log: route → steps → tool grants → final outcome.
8. **Egress** — Return answer or structured error; emit tokens, wall-clock, pattern, breaker state to TELEMETRY sinks.

Message style: **synchronous pattern-selected control flow** with optional fan-out inside parallelization/LLMCompiler DAGs—not peer A2A transfer ([Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).

---

## Part 2 — Core Mechanics & Algorithms

### Escalation ladder

| Level | Who owns control flow | When to use |
| --- | --- | --- |
| **Single LLM call** | Caller | Summarize, classify, extract, rewrite, translate, code with clear specs |
| **Workflow** | Predefined code + LLM/tool steps | Steps known a priori; validation gates between steps |
| **Agent** | LLM dynamically directs process | Step count/type unknown until observations arrive |

Do **not** escalate at “70–80% prototype”—fix prompts/gates first ([Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns); [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)).

### Workflow patterns (code controls flow)

| Pattern | Mechanism | Complexity / invariant |
| --- | --- | --- |
| **Prompt chaining** | Fixed sequence; gates abort/repair | \(O(S)\) LLM RTTs for \(S\) steps; errors cascade unless gated |
| **Routing** | Classify → specialized prompt/model | +1 classifier call; optimize one class without degrading others (e.g. easy→Haiku / hard→Sonnet; Sierra **15+** models cited) |
| **Parallelization** | Sectioning (independent subtasks) or voting (N samples → aggregate) | Wall-clock ≈ max(branch)+merge [inferred]; cost × branches |
| **Orchestrator-workers** | Central LLM decomposes → N workers → synthesize | Cookbook **N+1** LLM calls minimum; subtasks **not** pre-defined |
| **Evaluator-optimizer** | Generate ↔ evaluate until criteria/stop | **2×** LLM calls per round [inferred]; hard iteration/cost caps |

Sources: [Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [Anthropic cookbook](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/basic_workflows.ipynb); [orchestrator_workers](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb); [Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns).

### Agent patterns (model controls flow)

**ReAct** — Interleave Thought (does not affect Env) with Action → Observation ([ReAct](https://arxiv.org/abs/2210.03629)). Published lifts (PaLM-540B prompting): ALFWorld success **71%** vs Act-only 45 / IL ~37; WebShop **40%** vs Act 30.1; HotpotQA best ReAct+CoT EM **35.1**; FEVER best **64.6%** ([Google Research blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)).

**Reflexion** — Sparse feedback → linguistic self-reflection → episodic memory → retry (no weight updates). Memory bound (HotPotQA size **3**). AlfWorld **+22** pp in 12 trials; HotPotQA **+20** pp; HumanEval **91%** vs GPT-4 **80%**; WebShop **no useful improvement** after 4 trials ([Reflexion](https://arxiv.org/abs/2303.11366)).

**Plan-and-execute family**

| Variant | Topology |
| --- | --- |
| Plan-and-Solve (PS/PS+) | Single-pass: devise plan → execute (no tools required) |
| Plan-and-Execute agent | Planner → step executor → replan/finish |
| ReWOO | Plan with `#E` vars; workers fill; Solver answers |
| LLMCompiler | Streamed **DAG** of tool tasks; parallel ready set; Joiner replan |

PS+ vs Zero-shot-CoT (text-davinci-003): MultiArith **91.8%**, GSM8K **59.3%**, CSQA **71.9%** vs CoT **65.2%** ([Wang et al.](https://arxiv.org/abs/2305.04091)). LLMCompiler vs ReAct (GPT): HotpotQA **1.80×** latency / **3.37×** cost; Movie Rec **3.74×** / **6.73×**; abstract claims up to **3.7×** latency, **6.7×** cost ([LLMCompiler](https://arxiv.org/abs/2312.04511)).

### State machines

**ReAct turn** (strictly sequential):

```
  ┌────────┐   Thought   ┌────────┐  Action   ┌────────────┐
  │  LLM   │────────────►│ decide │──────────►│ TOOL PROXY │
  └───▲────┘             └────────┘           └─────┬──────┘
      │  Observation                                │
      └─────────────────────────────────────────────┘
           until Finish | max_iterations | breaker open
```

**Circuit breaker** (enterprise wrapper around pattern execution):

```
          success                    recovery_timeout
     ┌──────────────┐  fail≥N   ┌──────┐  elapsed   ┌───────────┐
     │    CLOSED    │──────────►│ OPEN │───────────►│ HALF_OPEN │
     └──────▲───────┘           └──────┘            └─────┬─────┘
            │ success                                      │
            └──────────────────────────────────────────────┘
                         fail → OPEN
```

**Pattern router** (deterministic outer FSM):

```
  ┌─────────┐   classify   ┌──────────┐
  │ INGRESS │─────────────►│  ROUTE   │
  └─────────┘              └────┬─────┘
           ┌────────┬───────────┼───────────┐
           ▼        ▼           ▼           ▼
        CHAIN    REACT    PLAN_EXECUTE   FALLBACK
           │        │           │           │
           └────────┴─────┬─────┴───────────┘
                          ▼
                     ┌─────────┐
                     │  EGRESS │
                     └─────────┘
```

### Complexity & invariants

| Property | Statement |
| --- | --- |
| Call multiplicity | Agent suite **~9.2×** LLM calls vs CoT (ReAct/Reflexion/LATS/LLMCompiler avg); LATS extreme **71.0** calls/request ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) |
| Context growth | Input ~**1k** → **3–4×** as history accumulates ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) |
| Latency mix | LLM **69.4%** / tools **30.2%** E2E; LLMCompiler overlap only **18.2%** of latency ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) |
| Convergence | Evaluator-optimizer / Reflexion / ReAct converge only with **hard stop** (iterations, recursion_limit, cost); WebShop shows reflections may not help ([Reflexion NeurIPS](https://papers.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf)) |
| Invariant | Prefer workflows when steps enumerable; agents when not ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)) |

---

## Part 3 — Token Economics & NFR Analysis

### Cost formula: `$ per 1k runs`

**Stated assumptions (labeled)**

| Symbol | Value | Meaning |
| --- | --- | --- |
| Baseline model | Claude Sonnet-class | List rates used for arithmetic only |
| \(P_{\text{in}}\) | **$3 / MTok** | Assumed input price (label: *assumption*, not a live quote) |
| \(P_{\text{out}}\) | **$15 / MTok** | Assumed output price |
| \(T_{\text{chat}}\) | 4,000 in + 500 out | Single-turn chat reference shape |
| \(M_{\text{agent}}\) | **~4×** chat tokens | Anthropic Research: agents vs chat ([Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| \(M_{\text{multi}}\) | **~15×** chat tokens | Anthropic Research: multi-agent vs chat |
| \(K_{\text{calls}}\) | **~9.2×** | Agent suite LLM calls vs CoT ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) |
| Cache | Prefill **−58.6%** avg; E2E LLM latency **−15.7%** avg with prefix cache | Same study; ReAct serving throughput **5.62×** with prefix cache |

Chat baseline cost per run:

\[
C_{\text{chat}} = \frac{4000}{10^6}\cdot \$3 + \frac{500}{10^6}\cdot \$15
= \$0.012 + \$0.0075 = \mathbf{\$0.0195}
\]

**Agent pattern (token-multiplier path)** — apply Anthropic **~4×**:

\[
C_{\text{agent}} = 4 \times C_{\text{chat}} = \$0.078
\quad\Rightarrow\quad
C_{\text{1k, agent}} = 1000 \times \$0.078 = \mathbf{\$78\ /\ 1k\ runs}
\]

**Multi-agent / heavy orchestrator-workers** — apply **~15×**:

\[
C_{\text{1k, multi}} = 1000 \times 15 \times \$0.0195 = \mathbf{\$292.50\ /\ 1k\ runs}
\]

**Call-multiplier path (same token shape per call, CoT baseline)** — if CoT uses 1 call at \(C_{\text{chat}}\) and agent suite averages **9.2×** calls with context growth ~**3×** mid-run input [inferred blend from 2506.04301 growth]:

\[
\begin{align*}
C_{\text{call-heavy}} &\approx 9.2 \times \Bigl(\tfrac{4000\times 2}{10^6}\cdot\$3 + \tfrac{500}{10^6}\cdot\$15\Bigr)
\quad\text{[inferred: ~2× avg input vs first call]} \\
&= 9.2 \times (\$0.024 + \$0.0075) = 9.2 \times \$0.0315 \approx \$0.290 \\
C_{\text{1k, call-heavy}} &\approx \mathbf{\$290\ /\ 1k\ runs}
\end{align*}
\]

Pattern sketches (linear models from research §2):

| Pattern | Cost model | Illustrative \(C_{\text{1k}}\) under assumptions |
| --- | --- | --- |
| Chaining (\(S=5\)) | ≈ \(5\times C_{\text{chat}}\) | **~$97.5 / 1k** |
| Routing | \(C_{\text{cls}} + C_{\text{tier}}\) | Classifier + selected tier |
| Parallel sectioning (\(N=3\)) | ≈ \(3\times\) branch cost | Cost ×N; wall-clock ~max |
| Orchestrator-workers | ≥ \(N{+}1\) calls | Cookbook minimum |
| Evaluator-optimizer | \(2\times\) rounds | Cap rounds |
| ReAct | ~9.2× calls class (suite avg) | Heavy-tail $ |
| Plan-and-execute / LLMCompiler | Often **<** ReAct $ if DAG-parallel | HotpotQA **3.37×** cost cut vs ReAct published |

Prefix caching: agents share growing prefixes → high affinity; use for ReAct/plan loops ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)). Economic viability: reserve **~15×** multi-agent spend for high-value parallelizable work ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Latency SLA targets

> ⚠️ **Gap**: Research lacks vendor-published **p50/p95/p99** milliseconds by agentic pattern for production SaaS. Published figures are lab/benchmark (ShareGPT ~**3–7 s**; ReAct heavier tail; HotpotQA ReAct **7.12 s** vs LLMCompiler **3.95 s**; Movie Rec ReAct **20.47 s** vs **5.47 s**) ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301); [LLMCompiler](https://arxiv.org/abs/2312.04511)). Do not treat the table below as vendor SLAs.

**[inferred] latency budget** — arithmetic for a support/tool agent (engineering targets, not measured):

| Component | Chain (\(S=3\)) | ReAct (avg 6 LLM + 5 tools) | Plan-exec (1 plan + 4 steps) |
| --- | --- | --- | --- |
| Classifier / route | 80 ms | 80 ms | 80 ms |
| LLM RTTs | \(3\times 400\) = 1,200 ms | \(6\times 450\) = 2,700 ms | \(1\times 600 + 4\times 350\) = 2,000 ms |
| Tools | 0–200 ms | \(5\times 200\) = 1,000 ms (mix of ~20 ms WebShop-class and ~1.2 s wiki-class tools from lab) | 800 ms |
| Gates / merge | 20 ms | 20 ms | 40 ms |
| **E2E point estimate** | ≈ **1,300 ms** | ≈ **3,800 ms** | ≈ **2,920 ms** |

Blend notes: LLM **69.4%** / tools **30.2%** of E2E in agent suite; hard to overlap ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)).

| Tier | **[inferred] target** | Covers | Mitigations |
| --- | --- | --- | --- |
| **p50** | **≤ 1,500 ms** | Warm chain or short plan-exec; few tools | Prefer chain/routing for known steps; prefix cache (**−15.7%** E2E LLM latency avg); smaller executor models ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301); [LangChain](https://www.langchain.com/blog/planning-agents)) |
| **p95** | **≤ 8,000 ms** | Typical ReAct with moderate tools | Cap `max_iterations`; LLMCompiler/DAG when independent tools (up to **3.7×** vs ReAct); shed voting quorum 3→1 under load [inferred] |
| **p99** | **≤ 25,000 ms** | Heavy-tail ReAct / tool-bound wiki-class / replan storms | Circuit breaker; fallback agent→workflow→deterministic; tighten tool RPM; continue-as-new every N turns ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301); research §3) |

Concrete published anchors (not percentiles): HotpotQA ReAct **7,120 ms** / LLMCompiler **3,950 ms**; Game-of-24 ToT **241.2 s** vs LLMCompiler **83.6 s** ([LLMCompiler](https://arxiv.org/abs/2312.04511)).

### Throughput & back-pressure

| Lever | Behavior |
| --- | --- |
| **Prefix cache** | ReAct serving **5.62×** throughput with prefix cache vs without (ShareGPT only **1.03×**) ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) |
| **KV memory** | Tool agents **3.0×** (up to **5.4×**) KV vs CoT even with prefix cache—size GPU pool accordingly |
| **Token-bucket** | Admit by pattern class: agents consume more TPM; separate bulkheads for classifier vs ReAct loop |
| **Shed load** | Routing → cheaper model / human queue on 429; parallelization → drop vote quorum; agents → cut `max_iterations` / tool RPM [inferred] |
| **Breakers** | Open pattern path → fall back to chain/deterministic; protect shared model quota |

**Capacity sketch [inferred]**: at ReAct p50≈3.8 s and 40% concurrency utilization, one worker ≈ \(1000/3800\times 0.4 \approx 0.105\) RPS; 100 workers ≈ 10.5 RPS before model TPM—orders of magnitude below chatbot ShareGPT shapes at same hardware ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301) latency distribution).

### NFR trade-offs

| NFR | Target / posture | Pattern implication |
| --- | --- | --- |
| **Availability** | Design **99.9%** on ingress + router; pattern paths may degrade | Breaker open ≠ total outage if fallback chain remains healthy |
| **RPO** | Checkpoint every super-step (`sync` durability) → RPO ≈ last completed step; `async` small crash risk; `exit` only on graph exit ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)) | ReAct: lose in-flight Thought if no ckpt; plan-exec: persist plan before compaction (Anthropic Memory before **200k** truncation) ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **RTO** | Resume from `thread_id` + `checkpoint_id` (time-travel); pending-writes avoid re-running completed parallel siblings | Workflow resume faster than rebuilding agent trajectory |
| **Compliance** | Immutable decision logs; PII redact before persist; tool RBAC audit | Chaining/gates leave clearest SOC2 trail; Reflexion episodic memory needs TTL/redact [inferred] |

**Explicit trade-off — autonomy vs cost/latency**: Agents improve task performance when steps are unknown, but Anthropic states they **trade latency and cost** for that performance; multi-agent burns **~15×** chat tokens; lab agents issue **~9.2×** LLM calls vs CoT ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system); [2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)). Prefer plan/DAG parallelism over naïve ReAct when the tool graph has independent edges ([LLMCompiler](https://arxiv.org/abs/2312.04511)).

---

## Part 4 — Distributed Resilience & Security

### What each pattern checkpoints

| Pattern | Checkpointed state | Durability notes |
| --- | --- | --- |
| **Chaining** | Step index, intermediate artifacts, gate pass/fail | Gate decisions are deterministic activities [inferred Temporal mapping] |
| **Routing** | Route label, confidence, selected handler id | Router SPOF—log dual-route in shadow mode |
| **Parallelization** | Per-branch outputs + aggregator policy | Pending-writes: completed siblings not re-run ([LangGraph](https://docs.langchain.com/oss/python/langgraph/checkpointers)) |
| **Orchestrator-workers** | Plan/subtask list, worker results, merge state | Persist plan to Memory before context truncation ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Evaluator-optimizer** | Best candidate, critique, round count | Cap rounds in checkpointed counter |
| **ReAct** | Full Thought/Action/Observation trajectory | `recursion_limit`; continue-as-new every N turns [inferred] |
| **Reflexion** | Trajectory + episodic reflection buffer (bound size) | Memory may hold PII—TTL/redact [inferred] |
| **Plan-and-execute / LLMCompiler** | Plan/DAG, completed node ids, `$id` substitutions, Joiner status | Replan feedback on bad DAGs ([LLMCompiler](https://arxiv.org/abs/2312.04511)) |

LangGraph modes: `exit` / `async` / `sync`; default `recursion_limit` **25** ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [GRAPH_RECURSION_LIMIT](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)).

> ⚠️ **Gap**: Limited public data on Temporal/Kafka event-sourcing *specific* to pattern choice and published breaker threshold curves. Map [inferred]: each LLM/tool step → activity; gate/router → deterministic activity; ReAct loop → workflow with continue-as-new.

### Failure taxonomy

| Class | Examples | Detection / response |
| --- | --- | --- |
| **Transient** | 429/5xx, tool timeout, cache stampede | Retry + full jitter; breaker counts failures |
| **Permanent** | Schema-invalid tool args, auth deny, unsupported intent | Fail-closed; no retry; escalate or deterministic |
| **Poison-pill** | Repeated identical Action+Observation; endless polish | Loop detector; max iterations; DLQ |
| **Pattern-specific** | Cascading bad intermediates (chain); misroute; partial branch fail; goal drift / 50-subagent spawn; evaluator gaming; useless Reflexion trials; heavy-tail under load | Gates; router eval; pre-declared degrade policy; effort budgets; hard caps ([research §5](../research/08-agentic-patterns.md)) |

Idempotency: key tool side effects by `(thread_id, step_id, tool_name, args_hash)` so replay after checkpoint restore does not double-book [inferred].

### Circuit breaker: closed → open → half-open

1. **CLOSED** — Requests flow to primary pattern path; failures in sliding window counted.  
2. **OPEN** — After ≥ \(N\) failures (or half-open probe fail), reject primary; route to fallback; start recovery timer.  
3. **HALF_OPEN** — Allow one probe through primary; success → CLOSED; fail → OPEN.

No peer-reviewed pattern-specific breaker curves in consulted sources—tune \(N\)/timeout from your SLOs [inferred].

### Fallback chains (agent → workflow → deterministic)

```
  ReAct / plan-exec  ──fail/breaker/budget──►  Prompt chain / routed workflow
           │                                              │
           │                                              ▼
           └───────────────►  Deterministic handler (rules, FAQ, human queue)
```

| Stage | Behavior |
| --- | --- |
| **Agent** | Full tool autonomy within allowlist + iteration caps |
| **Workflow** | Fixed chain or routed specialized prompts; gates on |
| **Deterministic** | No LLM; template/rules/cached answer; always terminates |

Parallelization partial-failure policy must be declared up front: retry branch / proceed degraded / fail-all ([Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).

### Enterprise security

> ⚠️ Limited public data **tied specifically to named agentic patterns**. Zero-Trust MCP / formal HIPAA mappings are not in pattern papers—defer details to MCP/security topics. Pattern→governance rows below are **[inferred]** where research marks them so.

**Zero-Trust MCP** [inferred enterprise posture]: authenticate every tool call (mTLS/OAuth); deny-by-default tool registry; per-request scoped tokens; no ambient credentials in agent prompts; sandbox network egress ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents) sandboxing + ACI guidance).

**Tool RBAC**: least privilege per pattern role—worker grants ⊆ orchestrator grant to prevent privilege amplification via spawn ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system); [inferred]). Schema-validate every tool call; absolute paths; poka-yoke args ([Anthropic App. 2](https://www.anthropic.com/engineering/building-effective-agents)).

**PII pipeline**: **detect → redact → audit** before persistence of trajectories, reflections, and decision logs. Reflexion episodic memory especially likely to store PII from failed trials—TTL + redact **[inferred]**.

**Immutable decision logs**: append-only records of route label, pattern, tool grants, gate outcomes, breaker transitions, final disposition—chain-of-custody for agent decisions (SOC2) **[inferred]**; chaining intermediates are the clearest trail.

| Pattern | Governance mapping **[inferred]** |
| --- | --- |
| Chaining | Audit each gate; retain intermediates |
| Routing | Log route + confidence; human escalate classes |
| Voting parallelization | Quorum rules = security policy (FP vs FN) |
| Orchestrator-workers | Worker RBAC ⊆ orchestrator; effort budgets |
| ReAct / plan-execute | Schema-validate tools; deny-by-default registry |
| Reflexion | Episodic memory TTL/redact |
| Parallel guardrails | Separate model instance for screening vs answer ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)—documented) |

Documented (not inferred): autonomy bounds (tools/spend/stop); sandbox before autonomy; human checkpoints; ACI hardening; parallel sectioning for guardrails ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)).

---

## Part 5 — Production Enterprise Code

Runnable Python: **pattern router** (chain vs ReAct vs plan-and-execute) with retries + full jitter, circuit breaker (closed → open → half-open), fallback chain (agent → workflow → deterministic), correlation IDs, graceful degradation. **Deterministic fake model**—no API keys, no network, no TODOs.

```python
#!/usr/bin/env python3
"""Agentic pattern router with enterprise resilience primitives."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.time(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "pattern": getattr(record, "pattern", None),
            "attempt": getattr(record, "attempt", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "degraded": getattr(record, "degraded", None),
            "step": getattr(record, "step", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOG = build_logger("agentic.patterns")


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    POISON = "poison"


class PatternError(Exception):
    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Circuit breaker: closed → open → half-open
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout_s: float = 0.05  # short for demo; 30–60s in prod
    window_s: float = 60.0
    state: BreakerState = BreakerState.CLOSED
    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def allow(self) -> bool:
        now = time.time()
        if self.state == BreakerState.OPEN:
            if self.opened_at is not None and now - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures.clear()
        self.state = BreakerState.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        now = time.time()
        self.failures = [t for t in self.failures if now - t <= self.window_s]
        self.failures.append(now)
        if self.state == BreakerState.HALF_OPEN or len(self.failures) >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = now


# ---------------------------------------------------------------------------
# Retries with exponential backoff + full jitter
# ---------------------------------------------------------------------------

def retry_with_jitter(
    fn: Callable[[], Any],
    *,
    max_attempts: int,
    base_s: float,
    max_s: float,
    correlation_id: str,
    pattern: str,
    should_retry: Callable[[Exception], bool],
) -> Any:
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classified below
            last = exc
            LOG.warning(
                "attempt_failed",
                extra={
                    "correlation_id": correlation_id,
                    "pattern": pattern,
                    "attempt": attempt,
                },
            )
            if not should_retry(exc) or attempt == max_attempts:
                break
            sleep_s = min(max_s, base_s * (2 ** (attempt - 1)))
            time.sleep(random.uniform(0, sleep_s))
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# PII: detect → redact → audit
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def redact_pii(text: str, correlation_id: str) -> tuple[str, dict[str, Any]]:
    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:6]
        return f"[PII:email:{digest}]"

    redacted = EMAIL_RE.sub(_sub, text)
    audit = {
        "correlation_id": correlation_id,
        "content_sha256": hashlib.sha256(redacted.encode()).hexdigest(),
        "pii_tokens_redacted": count,
        "ts": time.time(),
    }
    return redacted, audit


# ---------------------------------------------------------------------------
# Deterministic fake model (no API keys)
# ---------------------------------------------------------------------------

@dataclass
class FakeModel:
    """Deterministic completions keyed by prompt prefix; injectable faults."""

    fail_times: int = 0
    _fails_left: int = field(init=False)

    def __post_init__(self) -> None:
        self._fails_left = self.fail_times

    def complete(self, prompt: str, *, role: str = "llm") -> str:
        if self._fails_left > 0:
            self._fails_left -= 1
            raise PatternError("transient model 503", FailureKind.TRANSIENT)
        digest = hashlib.sha256(f"{role}:{prompt}".encode()).hexdigest()[:8]
        if prompt.startswith("CLASSIFY:"):
            body = prompt.split("CLASSIFY:", 1)[1].strip().lower()
            if "multi-hop" in body or "research" in body:
                return "plan_execute"
            if "tool" in body or "lookup" in body or "unknown steps" in body:
                return "react"
            return "chain"
        if prompt.startswith("CHAIN_STEP:"):
            return f"artifact:{digest}:{prompt.split(':', 1)[1][:40]}"
        if prompt.startswith("THOUGHT:"):
            return f"Thought: need data ({digest})\nAction: search[{prompt[-30:]}]"
        if prompt.startswith("PLAN:"):
            return "1) gather\n2) analyze\n3) answer"
        if prompt.startswith("EXEC:"):
            return f"step_result:{digest}"
        return f"final:{digest}"


@dataclass
class FakeTools:
    """Schema-validated tool proxy with deny-by-default RBAC."""

    allowed: set[str] = field(default_factory=lambda: {"search", "lookup"})

    def run(self, action_line: str) -> str:
        m = re.search(r"Action:\s*(\w+)\[(.*)\]", action_line)
        if not m:
            raise PatternError("malformed action", FailureKind.PERMANENT)
        name, args = m.group(1), m.group(2)
        if name not in self.allowed:
            raise PatternError(f"rbac_deny:{name}", FailureKind.PERMANENT)
        return f"Observation: {name}({args}) -> ok"


# ---------------------------------------------------------------------------
# Immutable decision log
# ---------------------------------------------------------------------------

@dataclass
class DecisionLog:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def append(self, **kwargs: Any) -> None:
        row = {"ts": time.time(), **kwargs}
        row["entry_sha256"] = hashlib.sha256(
            json.dumps(row, sort_keys=True, default=str).encode()
        ).hexdigest()
        self.entries.append(row)


# ---------------------------------------------------------------------------
# Pattern executors
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    pattern: str
    answer: str
    degraded: bool
    steps: int
    correlation_id: str
    audit: list[dict[str, Any]]


def run_chain(model: FakeModel, goal: str, cid: str, log: DecisionLog) -> str:
    artifacts: list[str] = []
    for i, step in enumerate(("extract", "transform", "summarize"), start=1):
        prompt = f"CHAIN_STEP:{step}:{goal}:{'|'.join(artifacts)}"
        out = model.complete(prompt)
        if not out.startswith("artifact:"):
            raise PatternError("gate_fail:bad_artifact", FailureKind.PERMANENT)
        artifacts.append(out)
        log.append(correlation_id=cid, pattern="chain", step=i, gate="pass")
        LOG.info("chain_step", extra={"correlation_id": cid, "pattern": "chain", "step": i})
    return artifacts[-1]


def run_react(
    model: FakeModel,
    tools: FakeTools,
    goal: str,
    cid: str,
    log: DecisionLog,
    max_iterations: int = 4,
) -> str:
    traj: list[str] = []
    for i in range(1, max_iterations + 1):
        thought_prompt = f"THOUGHT:{goal}|{'||'.join(traj[-3:])}"
        line = model.complete(thought_prompt)
        traj.append(line)
        if "Action:" not in line:
            log.append(correlation_id=cid, pattern="react", step=i, event="finish")
            return line
        obs = tools.run(line)
        traj.append(obs)
        log.append(correlation_id=cid, pattern="react", step=i, event="tool", detail=obs[:80])
        LOG.info("react_step", extra={"correlation_id": cid, "pattern": "react", "step": i})
        if i >= 2:
            return model.complete(f"FINAL:{goal}|{obs}")
    raise PatternError("poison:max_iterations", FailureKind.POISON)


def run_plan_execute(model: FakeModel, goal: str, cid: str, log: DecisionLog) -> str:
    plan = model.complete(f"PLAN:{goal}")
    log.append(correlation_id=cid, pattern="plan_execute", step=0, event="plan", detail=plan)
    results: list[str] = []
    for i, raw in enumerate(plan.splitlines(), start=1):
        step = raw.strip()
        if not step:
            continue
        results.append(model.complete(f"EXEC:{step}:{goal}"))
        log.append(correlation_id=cid, pattern="plan_execute", step=i, event="exec")
        LOG.info("plan_step", extra={"correlation_id": cid, "pattern": "plan_execute", "step": i})
    return model.complete(f"FINAL:{goal}|{'|'.join(results)}")


def deterministic_fallback(goal: str, cid: str, log: DecisionLog) -> str:
    safe, audit = redact_pii(goal, cid)
    log.append(correlation_id=cid, pattern="deterministic", event="fallback", pii_audit=audit)
    return f"DETERMINISTIC: received request hash={hashlib.sha256(safe.encode()).hexdigest()[:12]}"


# ---------------------------------------------------------------------------
# Pattern router with graceful degradation
# ---------------------------------------------------------------------------

class Pattern(str, Enum):
    CHAIN = "chain"
    REACT = "react"
    PLAN_EXECUTE = "plan_execute"


@dataclass
class PatternRouter:
    model: FakeModel
    tools: FakeTools
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    decision_log: DecisionLog = field(default_factory=DecisionLog)

    def classify(self, goal: str, cid: str) -> Pattern:
        label = self.model.complete(f"CLASSIFY:{goal}").strip()
        try:
            pattern = Pattern(label)
        except ValueError as exc:
            raise PatternError(f"bad_route:{label}", FailureKind.PERMANENT) from exc
        self.decision_log.append(correlation_id=cid, event="route", pattern=pattern.value)
        return pattern

    def _run_pattern(self, pattern: Pattern, goal: str, cid: str) -> str:
        if pattern == Pattern.CHAIN:
            return run_chain(self.model, goal, cid, self.decision_log)
        if pattern == Pattern.REACT:
            return run_react(self.model, self.tools, goal, cid, self.decision_log)
        return run_plan_execute(self.model, goal, cid, self.decision_log)

    def run(self, goal: str, *, force_pattern: Pattern | None = None) -> RunResult:
        cid = str(uuid.uuid4())
        safe_goal, pii_audit = redact_pii(goal, cid)
        self.decision_log.append(correlation_id=cid, event="pii", pii_audit=pii_audit)
        degraded = False
        pattern = force_pattern or self.classify(safe_goal, cid)

        def _should_retry(exc: Exception) -> bool:
            return isinstance(exc, PatternError) and exc.kind == FailureKind.TRANSIENT

        # Primary: agent-capable patterns when selected; breaker guards the path
        if pattern in (Pattern.REACT, Pattern.PLAN_EXECUTE) and self.breaker.allow():
            try:
                answer = retry_with_jitter(
                    lambda: self._run_pattern(pattern, safe_goal, cid),
                    max_attempts=3,
                    base_s=0.01,
                    max_s=0.05,
                    correlation_id=cid,
                    pattern=pattern.value,
                    should_retry=_should_retry,
                )
                self.breaker.record_success()
                return RunResult(pattern.value, answer, False, 0, cid, list(self.decision_log.entries))
            except PatternError as exc:
                self.breaker.record_failure()
                LOG.warning(
                    "agent_path_failed",
                    extra={
                        "correlation_id": cid,
                        "pattern": pattern.value,
                        "breaker_state": self.breaker.state.value,
                        "degraded": True,
                    },
                )
                degraded = True
                if exc.kind == FailureKind.PERMANENT and "rbac_deny" in str(exc):
                    # skip straight toward deterministic after permanent tool deny
                    answer = deterministic_fallback(safe_goal, cid, self.decision_log)
                    return RunResult("deterministic", answer, True, 0, cid, list(self.decision_log.entries))
        else:
            if pattern in (Pattern.REACT, Pattern.PLAN_EXECUTE):
                degraded = True
                LOG.info(
                    "breaker_short_circuit",
                    extra={
                        "correlation_id": cid,
                        "pattern": pattern.value,
                        "breaker_state": self.breaker.state.value,
                        "degraded": True,
                    },
                )

        # Fallback 1: workflow (chain)
        try:
            answer = retry_with_jitter(
                lambda: run_chain(self.model, safe_goal, cid, self.decision_log),
                max_attempts=2,
                base_s=0.01,
                max_s=0.04,
                correlation_id=cid,
                pattern="chain",
                should_retry=_should_retry,
            )
            self.decision_log.append(correlation_id=cid, event="fallback", to="chain")
            return RunResult("chain", answer, degraded or pattern != Pattern.CHAIN, 0, cid, list(self.decision_log.entries))
        except PatternError:
            LOG.error(
                "workflow_failed",
                extra={"correlation_id": cid, "pattern": "chain", "degraded": True},
            )

        # Fallback 2: deterministic
        answer = deterministic_fallback(safe_goal, cid, self.decision_log)
        return RunResult("deterministic", answer, True, 0, cid, list(self.decision_log.entries))


def _demo() -> None:
    random.seed(0)
    router = PatternRouter(model=FakeModel(fail_times=0), tools=FakeTools())
    cases = [
        "summarize the contract clause",
        "tool lookup unknown steps for order status",
        "multi-hop research on vendor pricing",
    ]
    for goal in cases:
        result = router.run(goal)
        print(json.dumps({
            "goal": goal,
            "pattern": result.pattern,
            "answer": result.answer,
            "degraded": result.degraded,
            "correlation_id": result.correlation_id,
            "log_entries": len(result.audit),
        }, indent=2))

    # Demonstrate retries → breaker open → workflow → deterministic fallback
    flaky = PatternRouter(model=FakeModel(fail_times=8), tools=FakeTools())
    flaky.breaker.failure_threshold = 1
    r = flaky.run("tool lookup unknown steps", force_pattern=Pattern.REACT)
    print(json.dumps({
        "scenario": "breaker_degrade",
        "pattern": r.pattern,
        "degraded": r.degraded,
        "breaker": flaky.breaker.state.value,
        "answer_prefix": r.answer[:48],
    }, indent=2))


if __name__ == "__main__":
    _demo()
```

Save the block as `pattern_router.py` and run `python3 pattern_router.py`. Expected: three routed results (`chain` / `react` / `plan_execute`) plus `breaker_degrade` → `deterministic`, `degraded: true`, `breaker: open`.

---

## Part 6 — Architectural System Design Scenarios

### Scenario A — Multi-tenant support: route + ReAct with hard cost caps

**Problem**: Design a support assistant for **50k tickets/day** (~0.6 RPM average, **~8 RPM** peak). ~70% intents are enumerable (reset password, order status, FAQ); ~30% need tools with unknown step counts. Cost target near the **~$78 / 1k** agent sketch for the tool cohort only; FAQ cohort must stay near chat baseline (**~$19.5 / 1k** under Part 3 assumptions). **[inferred] p95 ≤ 8 s** for tool path; never open-ended spend; PII in tickets; SOC2 decision trail.

**Proposed architecture**

```
┌────────────┐    ┌─────────────────────────────────────────────────────┐
│ API GW /   │───►│              CONTROL PLANE                          │
│ WAF        │    │  intent router · spend caps · max_iterations=6      │
└────────────┘    │  breaker per model tier · HITL on refunds           │
                  └───────────┬─────────────────┬───────────────────────┘
                              │                 │
              ┌───────────────▼──────┐   ┌──────▼──────────────────────┐
              │ DATA PLANE: CHAIN/   │   │ DATA PLANE: REACT tools     │
              │ FAQ / status prompts │   │ (allowlisted CRM/order API) │
              └───────────┬──────────┘   └──────┬──────────────────────┘
                          │                     │
         ┌────────────────┼─────────────────────┼────────────────┐
         ▼                ▼                     ▼                ▼
  ┌────────────┐  ┌────────────┐        ┌────────────┐  ┌────────────┐
  │TOOL PROXIES│  │PERSISTENCE │        │ TELEMETRY  │  │ PII redact │
  │ MCP+RBAC   │  │ ckpt+audit │        │ $/pattern  │  │ → audit    │
  └────────────┘  └────────────┘        └────────────┘  └────────────┘
```

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A1. Router → chain for known intents + ReAct for unknown (recommended)** | ~70% @ chat $/1k; ~30% @ ~4× tokens | p50 chain-like; p95 tool path bounded by caps | Med: router eval + two paths | Tool RBAC on ReAct only; gate trail on chain | Scales with router hit-rate; shed to chain |
| **A2. ReAct for all tickets** | ~4×–9.2× call pressure on all traffic | Heavy-tail p99 ([2506.04301](https://ar5iv.labs.arxiv.org/html/2506.04301)) | Lower pattern count, higher on-call for loops | Larger tool blast radius | KV **3–5.4×** vs CoT hurts density |
| **A3. Pure workflow, no agent** | Lowest | Most predictable | Low | Strongest gates | Won’t cover unknown-step 30% without endless special cases ([Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)) |

**Decision rationale**: Choose **A1**. Anthropic/Newsletter escalation: workflows when steps known; agents when not. Routing (easy→small / hard→capable) is a first-class workflow; Sierra-style multi-model routing cited for cost ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [Newsletter](https://newsletter.systemdesign.one/p/agentic-design-patterns)). Caps + breaker implement the autonomy vs cost/latency trade-off without paying **~15×** multi-agent tokens for FAQ.

---

### Scenario B — Internal research assistant: plan-and-execute vs orchestrator-workers vs naïve ReAct

**Problem**: Design an internal research copilot for GTM analysts. Queries often fan into **3–5** sub-questions with parallelizable web/doc tools. Leadership accepts higher spend only if wall-clock drops materially (Anthropic cites up to **90%** research-time cut with 3–5 subagents + parallel tools). Must avoid early Anthropic failure mode: spawning **~50** subagents on simple queries. Token burn is the performance driver (**80%** BrowseComp variance from tokens alone) ([Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)).

**Proposed architecture**

```
┌────────────┐   ┌──────────────────────────────────────────────────────┐
│ Analyst UI │──►│ CONTROL PLANE                                         │
└────────────┘   │ effort scaler: 1 agent / 3–10 tools simple facts;     │
                 │ 2–4 workers comparisons; >10 only if complex          │
                 │ plan persist → Memory before 200k compaction          │
                 └────────────┬───────────────┬──────────────────────────┘
                              │               │
                 ┌────────────▼─────┐   ┌─────▼──────────────────────────┐
                 │ DATA PLANE       │   │ DATA PLANE                     │
                 │ Planner + Joiner │   │ Workers (parallel tool DAGs)   │
                 │ (LLMCompiler /   │   │ scoped objective + format      │
                 │  plan-execute)   │   │                                │
                 └────────┬─────────┘   └─────┬──────────────────────────┘
                          │                   │
         ┌────────────────┼───────────────────┼──────────────┐
         ▼                ▼                   ▼              ▼
  ┌────────────┐  ┌────────────┐       ┌────────────┐ ┌────────────┐
  │TOOL PROXIES│  │PERSISTENCE │       │ TELEMETRY  │ │ Decision   │
  │ parallel   │  │ plan+ckpt  │       │ tokens 80% │ │ log / RBAC │
  │ ready-set  │  │            │       │ variance   │ │            │
  └────────────┘  └────────────┘       └────────────┘ └────────────┘
```

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **B1. Plan / LLMCompiler DAG + bounded workers (recommended)** | Often **<** naïve ReAct; HotpotQA **3.37×** cost cut vs ReAct published | Up to **3.7×** faster vs ReAct on parallel tool graphs | High: planner+joiner+effort rules | Worker RBAC ⊆ lead; schema tools | Parallel ready-set; still LLM-bound (only **18.2%** overlap in lab) |
| **B2. Naïve ReAct only** | Suite **~9.2×** calls vs CoT class | Heavy-tail; sequential tool waits | Med | Deny-by-default tools | Poor GPU KV density under load |
| **B3. Unbounded orchestrator-workers (no effort scaler)** | Approaches **~15×** chat; over-decomposition | Lead waits on slowest worker | High; goal drift | Privilege amplification risk via spawn | Spawns **50** workers on simple queries (documented early bug) |

**Decision rationale**: Choose **B1**. LangChain/LLMCompiler: explicit plan + DAG parallelism beats step-wise ReAct when tools have independent edges; Anthropic effort scaling (1 agent simple facts; 2–4 for comparisons) prevents 50-worker pathology; persist plan before compaction for RPO; reserve full multi-agent **~15×** only when evals show lift worth the tokens ([LLMCompiler](https://arxiv.org/abs/2312.04511); [LangChain](https://www.langchain.com/blog/planning-agents); [Anthropic Research](https://www.anthropic.com/engineering/multi-agent-research-system)).

---

### Interview prompts

1. Walk a ticket from API gateway through the pattern router. Where do CONTROL PLANE, DATA PLANE, PERSISTENCE, TOOL PROXIES, and TELEMETRY sit for chain vs ReAct vs plan-and-execute?
2. Derive `$ / 1k runs` for chat vs agent (**4×**) vs multi-agent (**15×**) under stated \(P_{\text{in}}/P_{\text{out}}\) assumptions. When would you reject multi-agent on cost alone?
3. Research lacks vendor p50/p95/p99 by pattern—how would you set **[inferred]** SLOs and prove them? What mitigations move p99?
4. For each pattern, what state do you checkpoint? How do `sync` vs `async` durability change RPO?
5. Draw the circuit breaker state machine and the fallback chain agent → workflow → deterministic. What failure kinds skip retry?
6. Map Zero-Trust MCP, tool RBAC, PII detect→redact→audit, and immutable decision logs onto orchestrator-workers. Which rows are documented vs **[inferred]**?
7. Scenario A: why is “ReAct for everything” the wrong default at 50k tickets/day?
8. Scenario B: how do effort scalers and LLMCompiler DAGs prevent the 50-subagent failure while still capturing parallel speedup?

---

## Sources (selected)

- [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [System Design Newsletter — Agentic Design Patterns](https://newsletter.systemdesign.one/p/agentic-design-patterns)
- [Anthropic — Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [ReAct](https://arxiv.org/abs/2210.03629) · [Reflexion](https://arxiv.org/abs/2303.11366) · [Plan-and-Solve](https://arxiv.org/abs/2305.04091) · [LLMCompiler](https://arxiv.org/abs/2312.04511)
- [Cost of Dynamic Reasoning (2506.04301)](https://ar5iv.labs.arxiv.org/html/2506.04301)
- [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) · [LangChain planning agents](https://www.langchain.com/blog/planning-agents)
