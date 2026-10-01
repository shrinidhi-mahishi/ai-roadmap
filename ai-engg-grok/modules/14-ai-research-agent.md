# Module 14 — How an AI Research Agent Works

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 14 (research-agent *workload*: search → fetch → synthesize → cite; MCP protocol internals live in module 04; general multi-agent platform design in module 09)  
**Grounded in**: `research/14-ai-research-agent.md` (16 sources, 2026-09-30)

Static RAG fetches a fixed chunk set and generates once. Research is **path-dependent**: intermediate findings change the next query ([Anthropic — Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)). An agent is an LLM that uses tools in a loop until the task is done—remove tools → chat; remove the loop → one-shot function call; remove the model → workflow ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)). This module covers the research product path: MCP `search`/`fetch`, orchestrator-worker + CitationAgent, token multipliers, citation failure modes, and enterprise harness design.

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    CONTROL PLANE                         │
                         │  LeadResearcher decompose / fan-out policy               │
                         │  turn caps (10–25) · $ / token budget · stop guards      │
                         │  reviewer + judge gates · MCP tool allowlist             │
                         │                                                          │
                         │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
                         │  │ Decompose  │  │ Fan-out    │  │ Effort / budget    │  │
                         │  │ + plan     │  │ 1 / 2–4 /  │  │ enforcer           │  │
                         │  │            │  │ >10        │  │                    │  │
                         │  └─────┬──────┘  └─────┬──────┘  └─────────┬──────────┘  │
                         └────────┼───────────────┼───────────────────┼─────────────┘
                                  │               │                   │
                                  ▼               ▼                   ▼
                         ┌──────────────────────────────────────────────────────────┐
                         │                     DATA PLANE                           │
                         │  Per-worker think–act–observe contexts                   │
                         │  fetched page text · condensed findings · report draft   │
                         │  claim↔citation graph (CitationAgent pass)               │
                         └───┬──────────────────────┼───────────────────────┬───────┘
                             │                      │                       │
              ┌──────────────┴───────┐  ┌───────────┴─────────┐  ┌──────────┴───────┐
              │     TOOL PROXIES     │  │    PERSISTENCE      │  │    TELEMETRY     │
              ├──────────────────────┤  ├─────────────────────┤  ├──────────────────┤
              │  MCP search (RO)     │  │  Memory plan        │  │  trajectory log  │
              │  MCP fetch (RO)      │  │  artifact store     │  │  tokens · $ · ms │
              │  URL dedupe / schema │  │  research checkpoints│  │  citation audit  │
              │  SSRF sandbox        │  │  immutable cite log │  │  correlation IDs │
              │  body/timeout caps   │  │  cached briefs      │  │  breaker state   │
              └──────────────────────┘  └─────────────────────┘  └──────────────────┘
```

**Plane responsibilities** [inferred from Anthropic Research + Newsletter #168 + OpenAI search/fetch standard]

| Plane | Research-agent contents |
| --- | --- |
| **CONTROL PLANE** | Iteration/budget caps, decompose/fan-out policy (1 / 2–4 / >10), reviewer/judge gates, MCP tool allowlist (search + fetch only for workers) |
| **DATA PLANE** | Per-worker contexts, fetched page text, Memory plan, citation graph / report artifacts, think–act–observe trajectory |
| **PERSISTENCE** | Memory plan (survive **200k** truncation), artifact store (large worker outputs as refs), research-trace checkpoints, immutable citation log, cached briefs |
| **TOOL PROXIES** | Read-only MCP `search` / `fetch`, schema validation, URL idempotency/dedupe, SSRF-hardened fetch, size/timeout/rate caps |
| **TELEMETRY** | Trajectory log (every model/tool/obs/stop), token/$ meters, wall-clock, citation audit events, correlation IDs, breaker state |

### Research pipeline stages

| Stage | Role |
| --- | --- |
| Decompose | Lead breaks user question into focused sub-questions |
| Parallel worker loops | Each worker runs think–act–observe over MCP `search` / `fetch` |
| Synthesize | Lead (or writer) merges findings into a structured report |
| Review | In-run reviewer checks claim↔citation coupling |
| Judge | Separate post-report evaluation (binary / rubric) |

Output design targets (newsletter build—not a measured production SLA): **600–1,200** words, **2–3** sections, **8–15** inline citations, confidence per section; **10–20** tool calls; **2–4** minutes wall-clock, almost all spent reading fetched pages ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

### End-to-end request-flow narrative (search → fetch → synthesize → cite)

1. **Ingress** — User submits a research question + optional session/thread id. CONTROL PLANE loads prior checkpoint (if any), remaining turn/token/$ budget, and MCP allowlist ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
2. **Decompose (CONTROL → DATA)** — LeadResearcher plans approach; persists plan to Memory before context can exceed **200,000** tokens and truncate ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
3. **Fan-out** — CONTROL PLANE spawns Subagents under effort rules: simple fact-finding → **1** agent / **3–10** tools; comparisons → **2–4** agents / **10–15** tools each; complex → **>10** with clear division. Prefer **3–5** parallel subagents and **3+** parallel tools per worker (wall-clock cut up to **90%**). Lead waits synchronously for each wave today ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
4. **Search (TOOL PROXIES)** — Worker calls MCP `search(query)` → `{ results: [{ id, title, url }] }`. Idempotency key = normalized URL (or query hash); duplicate URLs short-circuit. ChatGPT citation metadata requires non-empty `url` ([OpenAI MCP](https://developers.openai.com/api/docs/mcp); [search/fetch standard](https://github.com/openai/skills/blob/82d2c5b4/skills/.curated/chatgpt-apps/references/search-fetch-standard.md)).
5. **Fetch (TOOL PROXIES)** — Worker calls MCP `fetch(id)` → `{ id, title, text, url, metadata? }`. Proxy enforces HTTPS-only, private-IP blocklist, body/timeout caps, truncate (e.g. **8,000** chars in BrowseComp-style harnesses). Large text → artifact store; worker returns a lightweight ref ([agent-fetch](https://github.com/Parassharmaa/agent-fetch/); [BrowseComp](https://github.com/EnvCommons/BrowseComp); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
6. **Think–act–observe (DATA PLANE)** — Each turn: model emits tool call or final findings; harness acts; observation appends. Typical single-agent research: **10–20** turns. First stop wins: completion signal / iteration cap (**10** newsletter build; **15–25** general) / budget cap → partial + failure flag ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).
7. **Synthesize** — Lead merges condensed findings (not raw page dumps). May spawn more subagents or refine strategy ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
8. **Cite** — Separate **CitationAgent** walks documents + report and attaches claim-level citations; TELEMETRY/PERSISTENCE append immutable citation log entries ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
9. **Review / Judge** — In-run reviewer checks claim↔citation coupling; offline judge scores factual accuracy, citation accuracy, completeness, source quality, tool efficiency ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).
10. **Egress** — Cited report returned; checkpoint + trajectory + citation log sealed under correlation id.

Message style: **search → fetch → synthesize → cite** under a code-owned outer loop with hard stop guards. Without a hard stop, one stuck task can burn on the order of **~$50** of tokens ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

---

## Part 2 — Core Mechanics & Algorithms

### Why research needs an agent (not one-shot RAG)

| Pattern | Behavior | Failure vs research |
| --- | --- | --- |
| One-shot RAG | Fixed chunks → one generation | Cannot decide mid-answer that more evidence is needed |
| Workflow | Predetermined tool sequence | Brittle when intermediate findings invalidate the plan |
| Research agent | LLM + tools in a loop until done | Path-dependent exploration with stop guards |

([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system))

### Think–act–observe state machine

```
                    ┌──────────────┐
                    │   START      │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
           ┌───────►│    THINK     │◄──────────────────────────┐
           │        │  (model)     │                           │
           │        └──────┬───────┘                           │
           │               │                                   │
           │        ┌──────▼───────┐                           │
           │        │  CLASSIFY    │                           │
           │        └──────┬───────┘                           │
           │     ┌─────────┼─────────┬────────────┐            │
           │     ▼         ▼         ▼            ▼            │
           │  FINAL     SEARCH    FETCH      MAX_TURNS / $     │
           │  ANSWER      │         │            │            │
           │     │        ▼         ▼            ▼            │
           │     ▼   ┌──────────────────┐   partial + flag    │
           │  RETURN │ ACT (MCP proxy)  │                     │
           │         │ + URL dedupe     │                     │
           │         └────────┬─────────┘                     │
           │                  ▼                               │
           │           ┌──────────┐                           │
           │           │ OBSERVE  │──append──►context─────────┘
           │           └──────────┘
           └── (optional) reviewer / CitationAgent before RETURN
```

**Three stop conditions (first trip wins)**

| Guard | Behavior |
| --- | --- |
| Completion signal | Final answer with no tool call |
| Iteration cap | Newsletter build: **10** turns hard stop; general: **15–25** turns |
| Budget cap | Max tokens / dollars → partial result + failure flag |

([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp))

**Loop overlays** on the same cycle: ReAct (interleaved reasoning + tools), Plan-and-Execute (plan then execute; replan on failure), Reflection/Reflexion (critic when quality plateaus) ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

### Orchestrator-worker + CitationAgent

```
                    ┌────────────────┐
                    │ LeadResearcher │──plan──► Memory (PERSISTENCE)
                    └───────┬────────┘
                            │ fan-out (sync wave)
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
         ┌────────┐   ┌────────┐   ┌────────┐
         │Worker A│   │Worker B│   │Worker C│  search/fetch only
         └───┬────┘   └───┬────┘   └───┬────┘
             │ artifact   │            │
             └────────────┼────────────┘
                          ▼
                    ┌────────────┐
                    │ SYNTHESIZE │
                    └─────┬──────┘
                          ▼
                    ┌──────────────┐
                    │ CitationAgent│──► immutable citation log
                    └──────────────┘
```

Fan-out heuristics ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)):

| Query class | Subagents | Tool calls |
| --- | --- | --- |
| Simple fact-finding | **1** | **3–10** |
| Direct comparisons | **2–4** | **10–15** each |
| Complex research | **>10** with clear division | Divided responsibilities |

**Invariants**

| Invariant | Binding |
| --- | --- |
| Tool surface | Workers: read-only `search` + `fetch`; quality drops once the model sees roughly **>20** tools ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)) |
| Artifact refs | Large outputs to filesystem/store; return refs—avoid “game of telephone” ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Citation pass | Separate from synthesis; claim-level attribution ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Termination | Completion / **10–25** turns / $ budget; without stop ≈ **~$50** runaway ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) |

**Complexity [inferred]**: for \(N\) serial tool turns with growing context \(C_i \approx C_0 + i\cdot\Delta_{\text{page}}\), naive token volume \(\approx \sum C_i = \Theta(N^2 \Delta_{\text{page}})\). Parallel \(W\) workers with sync waves of size \(w\) approximate wall-clock \(T \approx T_{\text{plan}} + \lceil N_w/w\rceil\cdot\max_i T_{\text{worker}_i} + T_{\text{synth+cite}}\)—matches Anthropic’s sync-wave + parallelization narrative ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### MCP search / fetch contract

| Tool | Input | Output | Role |
| --- | --- | --- | --- |
| `search` | `query: string` | `{ results: [{ id, title, url }] }` | Discover candidate sources |
| `fetch` | `id: string` | `{ id, title, text, url, metadata? }` | Read full document for synthesis + citation |

([OpenAI MCP](https://developers.openai.com/api/docs/mcp); [search/fetch standard](https://github.com/openai/skills/blob/82d2c5b4/skills/.curated/chatgpt-apps/references/search-fetch-standard.md))

### Citation / grounding mechanics (research-specific hard problem)

Empirical ranges on deep-research Markdown reports ([Onweller et al.](https://arxiv.org/html/2605.06635)):

| Dimension | Frontier range | Meaning |
| --- | --- | --- |
| Link Works | **>94%** (most models) | URL resolves |
| Relevant Content | **>80%** (frontier) | Source is on-topic |
| Fact Check | **39–77%** | Claim actually supported by source |
| Citation hallucination (commercial, prior) | **11–57%** | Fabricated / wrong refs |

Ablation: Fact Check accuracy drops **~42%** on average as tool calls scale from **2 → 150**, while Link Works / Relevant Content stay stable—more retrieval can **hurt** factual synthesis via information overload ([Onweller et al.](https://arxiv.org/html/2605.06635)). Searcher snippets can be far more reliable (~**3.8%** mistakes) than researcher notes consolidating many docs (~**70.8%** mistakes)—prefer single-document extracts to the orchestrator ([“Who is the Agent to Blame?”](https://arxiv.org/pdf/2608.24306)).

### Convergence properties

| Pattern | Converges when | Diverges when |
| --- | --- | --- |
| Single ReAct research | Final answer / turn cap / $ cap | Endless search refine (termination failure) ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) |
| Orchestrator-worker | Lead synthesizes + CitationAgent | **50**-subagent runaway; vague briefs → duplicate searches ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Deep fetch trajectory | Budget + citation gate | Fact Check collapse past ~**150** tool calls ([Onweller et al.](https://arxiv.org/html/2605.06635)) |

---

## Part 3 — Token Economics & NFR Analysis

### Published multipliers (Anthropic)

| Metric | Value | Source |
| --- | --- | --- |
| Multi-agent vs single Opus 4 (internal research eval) | **+90.2%** relative performance | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Agents vs chat token use | **~4×** | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Multi-agent vs chat token use | **~15×** | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| BrowseComp variance from token usage alone | **80%** | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Token usage + tool calls + model choice | **95%** of BrowseComp variance | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Parallelization wall-clock cut | **up to 90%** | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Tool-description rewrite via testing agent | **40%** decrease in task completion time | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Coordinator pattern: input tokens at worker rate | **84–98%** | [Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small) |

Implication Anthropic states explicitly: multi-agent systems work mainly because they help **spend enough tokens** to solve the problem—economic viability requires task value high enough to absorb the **15×** chat baseline ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Cost formulas — $ per 1k runs

**Labeled model price assumption** (substitute your contract rates; not a live Anthropic quote):

| Symbol | Assumed value | Role |
| --- | --- | --- |
| \(P_{\text{in}}\) | **$3.00 / 1M** input tokens | Frontier list-class placeholder—**labeled assumption** |
| \(P_{\text{out}}\) | **$15.00 / 1M** output tokens | Same labeled assumption |
| \(T_{\text{chat}}\) | 4,000 in + 500 out | Single-turn chat reference shape (*assumption*) |
| \(M_{\text{agent}}\) | **~4×** chat tokens | Anthropic: agents vs chat |
| \(M_{\text{multi}}\) | **~15×** chat tokens | Anthropic: multi-agent vs chat |

Chat baseline cost per run:

\[
C_{\text{chat}} = \frac{4000}{10^6}\cdot \$3 + \frac{500}{10^6}\cdot \$15
= \$0.012 + \$0.0075 = \mathbf{\$0.0195}
\]

**Single research agent** — apply **~4×**:

\[
C_{\text{agent}} = 4 \times C_{\text{chat}} = \$0.078
\quad\Rightarrow\quad
C_{\text{1k, agent}} = 1000 \times \$0.078 = \mathbf{\$78\ /\ 1k\ runs}
\]

**Multi-agent research** — apply **~15×**:

\[
C_{\text{multi}} = 15 \times C_{\text{chat}} = \$0.2925
\quad\Rightarrow\quad
C_{\text{1k, multi}} = 1000 \times \$0.2925 = \mathbf{\$292.50\ /\ 1k\ runs}
\]

**Delta (multi vs single)** under these labeled prices:

\[
C_{\text{1k, multi}} - C_{\text{1k, agent}} = \$292.50 - \$78 = \mathbf{\$214.50\ /\ 1k\ runs}
\]

Coordinator economics further approximate:

\[
\text{Cost} \approx C_{\text{lead-plan+synth}} + \sum_i C_{\text{worker}_i}(\text{fetched pages})
\]

with most spend in \(\sum_i C_{\text{worker}_i}\) when **84–98%** of team input tokens bill at the worker rate ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

**~$50 runaway risk (hard cap example)**: without an iteration/budget stop, one stuck research task can burn on the order of **~$50** of tokens. Treat **$50 / run** as an illustrative circuit-breaker / budget-cap ceiling in CONTROL PLANE—not a measured mean cost ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

> No stable published “research agent unit price.” Absolute dollars scale with page-fetch volume and model mix; meter live usage rather than a fixed SKU ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

### Latency SLA targets (2–4 minute design target)

> ⚠️ **Gap**: Research lacks vendor-published **p50/p95/p99** milliseconds for Claude Research / ChatGPT Deep Research / Newsletter builds as production SLAs. Published shapes are the newsletter **2–4 minute** wall-clock *design target*, Anthropic “minutes instead of hours” after parallelization, and relative BrowseComp variance from token spend—not percentile SLAs ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); research §2). Do **not** treat the table below as vendor SLAs.

**[inferred] latency budget** aimed at the **2–4 minute** design target — arithmetic from component assumptions (engineering targets, not measured):

| Component | Assumed p50 | Assumed p95 | Assumed p99 | Notes |
| --- | ---: | ---: | ---: | --- |
| Lead plan | 8 s | 15 s | 25 s | Frontier planner |
| Worker wave (parallel sync; fetch-dominated) | 70 s | 140 s | 200 s | \(\approx \max_i T_{\text{worker}}\) |
| Lead synthesize | 12 s | 25 s | 40 s | Merge condensed findings |
| CitationAgent | 10 s | 20 s | 35 s | Claim-level pass |
| Checkpoint / persist | 1 s | 2 s | 4 s | Trace + citation log |
| **E2E sum** | **101 s** | **202 s** | **304 s** | ≈ **1.7 / 3.4 / 5.1 min** |

| Tier | **[inferred] target** | Arithmetic vs design band | Mitigations |
| --- | --- | --- | --- |
| **p50** | **≤ 120 s (2.0 min)** | Matches lower edge of **2–4 min** (\(101\) s point estimate + headroom) | Cap fan-out at **1** for simple facts; **3+** parallel tools inside workers; truncate fetch bodies; URL dedupe ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [BrowseComp](https://github.com/EnvCommons/BrowseComp)) |
| **p95** | **≤ 240 s (4.0 min)** | Upper edge of newsletter design target (\(202\) s ≈ mid-band) | Cohort **3–5** (not **50**); per-fetch timeout; sync-wave deadline; session $ budget as fan-out guard ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)) |
| **p99** | **≤ 360 s (6.0 min)** | Exceeds **4 min** design target when waves stall (\(304\) s); soft-fail to partial report | Hard turn cap **10–25**; breaker → single agent → cached brief; resume from checkpoint not full restart ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |

### Throughput & back-pressure

| Lever | Behavior |
| --- | --- |
| **Turn cap** | Newsletter build **10**; general research **15–25** hard stop ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) |
| **Tool budget** | Simple **3–10**; comparison **10–15**/worker; watch Fact Check degradation toward **~150** calls ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Onweller et al.](https://arxiv.org/html/2605.06635)) |
| **Fan-out caps** | Query class → **1** / **2–4** / **>10**; prefer **3–5** parallel per wave ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **$ / token ceiling** | Session budget as fan-out guard; **~$50** illustrative stuck-task cap ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small); [Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) |
| **URL dedupe** | Idempotent search/fetch by normalized URL → cuts duplicate fetch RPM **[inferred]** |
| **Shed load** | Under TPM pressure: cut \(N\) toward **1**, fall back multi→single→cached brief, queue non-interactive research **[inferred]** |
| **Breakers** | Open multi-agent path → single agent → cached brief (Part 4) |

> ⚠️ Limited public data for Research/Deep Research **RPM/TPM** product limits and prompt-cache hit rates. Apply provider caching + org quotas outside the research harness ([research §2](../research/14-ai-research-agent.md)).

### NFR trade-offs

| NFR | Target / posture | Research-agent implication |
| --- | --- | --- |
| **Availability** | Design **99.9%** on ingress + lead; worker fetch path may degrade | Breaker open ≠ total outage if single-agent / cached-brief fallback remains healthy **[inferred]** |
| **RPO (research traces)** | Checkpoint after each tool observe + each sync wave → RPO ≈ last completed tool/wave | Lose in-flight fetch without checkpoint; plan lost past **200k** unless Memory ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) |
| **RTO (research traces)** | Resume from checkpointed plan + artifact refs + citation log cursor | Resume-from-failure preferred over full restart; rainbow deploys so mid-flight agents survive updates ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Compliance** | Immutable trajectory + citation log; PII redact before persist; read-only MCP connectors; least-privilege tool RBAC | Owned harness when audit/cost ownership required; hosted when public-web only ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); research §4) |

**Explicit trade-off — fact-check quality vs search depth / cost**: deeper trajectories burn tokens (**~4×** single / **~15×** multi vs chat → **$78 / $292.50 per 1k** under labeled prices) and can cut wall-clock via parallelism (up to **90%**), but Fact Check accuracy drops **~42%** on average as tool calls scale **2 → 150** while link/relevance stay stable. Cap depth before overload; prefer CitationAgent + single-document extracts over dumping multi-doc notes into the lead ([Onweller et al.](https://arxiv.org/html/2605.06635); [blame localization](https://arxiv.org/pdf/2608.24306); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

---

## Part 4 — Distributed Resilience & Security

### Durable execution — checkpoint the research trace

| Mechanism | Behavior | Source |
| --- | --- | --- |
| Memory plan persistence | Lead saves plan before context truncation at **200k** tokens | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Phase summarization | Summarize completed phases; store essentials externally; spawn fresh subagents with clean contexts | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Artifact store | Subagents persist large outputs; return refs to lead | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Trajectory log | Every model call, tool call, observation, stop reason | [Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp) |
| Resume-on-error | Resume from checkpoint rather than restart | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Rainbow deployments | Old + new versions run concurrently so in-flight agents are not broken mid-run | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |

> ⚠️ **Gap**: Limited public data on Anthropic/OpenAI **internal** Temporal/Kafka/event-sourcing choices, distributed locks, or circuit-breaker thresholds for Research. Public material covers Memory checkpoints, rainbow deploys, trajectory logs, and sync-wave bottlenecks—not a published breaker tuning guide. Rows marked **[inferred]** below are enterprise posture for interview design, not vendor SLAs.

**[inferred] checkpoint payload** per research run: `(run_id, correlation_id, plan_ref, completed_subquestions[], url_seen_set, artifact_refs[], citation_log_cursor, breaker_state, spend_usd, turn_count)`.

### Failure taxonomy

| Class | Examples | Detection / response |
| --- | --- | --- |
| **Transient** | Search/fetch 429/5xx; slow page RTT stalls sync wave | Retry + jitter; per-fetch timeout; surface tool error to model ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Permanent** | Schema-invalid `fetch` id; auth deny on connector; SSRF blocked URL | Fail-closed; return recoverable error only when model can correct query/id |
| **Poison-pill / runaway** | Endless search/refine; **50** subagents on simple queries; SEO farm distraction | Turn cap **10–25**; $ budget (~**$50** illustrative); effort-scaling prompts; source-quality heuristics ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Research-specific** | Citation hallucination (**11–57%** prior); Fact Check collapse with deep tools; telephone paraphrase; context overflow at **200k**; duplicate subagent searches; efficiency failure (right answer in **50** calls when **5** suffice) | CitationAgent + judge; depth caps; artifact refs; Memory; rich task briefs; tool-efficiency rubric ([Onweller et al.](https://arxiv.org/html/2605.06635); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) |

### IDEMPOTENCY KEYS for search / fetch (dedupe URLs)

| Tool | Idempotency key | Replay behavior |
| --- | --- | --- |
| `search` | `sha256("search:" + normalize(query))` **[inferred]** | Return cached result list; do not re-bill upstream search |
| `fetch` | `sha256("fetch:" + normalize_url(url_or_id))` | Return cached page text / artifact ref; **dedupe URLs** across workers |

Normalize: lowercase host, strip fragments/tracking params, trailing-slash policy **[inferred]**. Cross-worker shared `url_seen` set prevents two subagents from fetching the same SEO page twice ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) duplication failure mode + open MCP TTL-cache practice).

### Circuit breaker: closed → open → half-open

```
          success                    recovery_timeout
     ┌──────────────┐  fail≥N   ┌──────┐  elapsed   ┌───────────┐
     │    CLOSED    │──────────►│ OPEN │───────────►│ HALF_OPEN │
     └──────▲───────┘           └──────┘            └─────┬─────┘
            │ success                                      │
            └──────────────────────────────────────────────┘
                         fail → OPEN
```

1. **CLOSED** — Multi-agent research path accepts traffic; failures in sliding window counted (fetch timeouts, lead synth errors, budget overruns) **[inferred]**.  
2. **OPEN** — After ≥ \(N\) consecutive failures (or half-open probe fail), reject multi-agent fan-out; start recovery timer; route to fallback.  
3. **HALF_OPEN** — Allow one probe through multi-agent research; success → CLOSED; fail → OPEN.

No peer-reviewed Research-product breaker curves in consulted sources—tune \(N\)/timeout from your SLOs **[inferred]**.

### Fallback chain (multi-agent → single agent → cached brief)

```
  Multi-agent (lead + workers + CitationAgent)
       │ fail / breaker / budget / turn cap
       ▼
  Single agent (one ReAct loop + MCP search/fetch, ~4× chat tokens)
       │ fail / breaker / budget
       ▼
  Cached brief (deterministic: last good report / FAQ template / human queue)
```

| Stage | Behavior |
| --- | --- |
| **Multi-agent** | Fan-out under caps; CitationAgent; full trajectory (~**15×** chat) |
| **Single agent** | One context, search/fetch allowlist (~**4×** chat); no peer workers |
| **Cached brief** | No live web loop; return prior sealed brief for same idempotent query key, or escalate to human; always terminates |

### Enterprise security

> ⚠️ Limited public data for **research-product-specific** PII NER schemas or SOC2/HIPAA mappings for Claude Research / ChatGPT Deep Research. Treat the following as enterprise expectations; thin areas labeled **[inferred]**.

**Read-only MCP search/fetch**: Deep-research connector contract is two read-only tools; approval can be skipped for those while write/consequential tools keep approval ([OpenAI MCP](https://developers.openai.com/api/docs/mcp)). Cookbook scopes workers to `web_search` + `web_fetch` with other tools disabled; coordinator has **no** tools and only reads distilled reports ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)). A tool that does not exist cannot be called ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

**Zero-Trust** **[inferred]**: authenticate every MCP call (mTLS/OAuth); deny-by-default connector registry; per-session scoped tokens for custom Deep Research connectors; no ambient credentials in prompts; re-validate egress on every redirect (SSRF: resolve → public IP only → pin → revalidate) ([agent-fetch](https://github.com/Parassharmaa/agent-fetch/); [deepagents tools](https://github.com/langchain-ai/deepagents/blob/d60560d6/libs/code/deepagents_code/tools.py); [OpenAI for Business connectors](https://www.linkedin.com/posts/openai-for-business_yesterday-we-launched-custom-deep-research-activity-7336428401160790016-t6GC)).

**Tool RBAC**: workers → `search`/`fetch` only; lead/coordinator → plan/synthesize (no raw page tools); CitationAgent → read artifacts + attach citations (no web write). Internal connectors (SQL, contract repos) enabled per session by admin ([OpenAI MCP](https://developers.openai.com/api/docs/mcp); [Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

**PII pipeline on fetched pages**: **detect → redact → audit** before page text enters trajectory logs, checkpoints, or shared lead context. Treat fetched pages as untrusted. **[inferred]** regex/NER for emails/phones/SSNs; redact in persisted artifacts; audit event records `(correlation_id, url, pii_types[], action=redact)` without storing raw PII.

**Immutable citation log**: append-only `(correlation_id, claim_id, url, source_id, model_pass, disposition)` for chain-of-custody; prefer high-level decision tracing without logging full conversation contents where vendor framing requires it ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)). **[inferred]** WORM/object-lock storage for regulated industries.

---

## Part 5 — Production Enterprise Code

Runnable Python: **search/fetch research loop** with URL-dedupe idempotency keys, retries + full jitter, circuit breaker (closed → open → half-open), fallback (multi-agent → single agent → cached brief), correlation IDs, structured logging, and a citation list. **Deterministic fake web** — no API keys, no network, no stub placeholders.

```python
#!/usr/bin/env python3
"""AI research agent loop with enterprise resilience primitives (fake web)."""

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
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


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
        }
        for key in ("correlation_id", "run_id", "tool", "state", "url", "event"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, sort_keys=True)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOG = get_logger("research_agent")


def log(msg: str, **extra: Any) -> None:
    LOG.info(msg, extra=extra)


# ---------------------------------------------------------------------------
# URL normalize + idempotency keys
# ---------------------------------------------------------------------------

_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING
    ]
    query = urlencode(sorted(query_pairs))
    return urlunparse((scheme, netloc, path, "", query, ""))


def idempotency_key(tool: str, identity: str) -> str:
    material = f"{tool}:{identity}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


# ---------------------------------------------------------------------------
# Circuit breaker: CLOSED → OPEN → HALF_OPEN
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout_s: float = 0.05  # short for demo; raise in production
    failure_count: int = 0
    state: BreakerState = BreakerState.CLOSED
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: single probe

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.state is BreakerState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


# ---------------------------------------------------------------------------
# Retries with exponential backoff + full jitter
# ---------------------------------------------------------------------------

def retry_with_jitter(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 3,
    base_s: float = 0.01,
    correlation_id: str,
    tool: str,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — demo surfaces all tool errors
            last_exc = exc
            if attempt == max_attempts:
                break
            sleep_s = random.uniform(0, base_s * (2 ** (attempt - 1)))
            log(
                "retry",
                correlation_id=correlation_id,
                tool=tool,
                event=f"attempt={attempt} sleep={sleep_s:.4f} err={exc}",
            )
            time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Deterministic fake web (no network)
# ---------------------------------------------------------------------------

FAKE_CORPUS: dict[str, dict[str, str]] = {
    "https://docs.example.com/mcp-search-fetch": {
        "title": "MCP search and fetch standard",
        "text": (
            "Deep research connectors expose read-only search and fetch tools. "
            "Search returns id, title, and url. Fetch returns full document text "
            "for synthesis and citation. Contact: research@example.com."
        ),
    },
    "https://research.example.com/citation-quality": {
        "title": "Citation quality in deep research",
        "text": (
            "Link validity can exceed 94 percent while fact-check accuracy ranges "
            "39 to 77 percent. Excess tool calls can degrade claim support. "
            "Phone: +1-555-0100."
        ),
    },
    "https://engineering.example.com/multi-agent-research": {
        "title": "Multi-agent research topology",
        "text": (
            "LeadResearcher plans, spawns subagents with isolated contexts, "
            "synthesizes findings, then CitationAgent attaches claim-level citations. "
            "Multi-agent research uses about 15x chat tokens."
        ),
    },
}

SEARCH_INDEX: list[tuple[str, str]] = [
    ("mcp search fetch", "https://docs.example.com/mcp-search-fetch"),
    ("citation fact check", "https://research.example.com/citation-quality"),
    ("multi-agent research", "https://engineering.example.com/multi-agent-research"),
]


class TransientFetchError(RuntimeError):
    pass


@dataclass
class FakeWeb:
    """Deterministic search/fetch with optional injected transient failures."""

    fail_fetch_times: int = 0
    _fetch_attempts: dict[str, int] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)

    def search(self, query: str) -> list[dict[str, str]]:
        q = query.lower()
        results: list[dict[str, str]] = []
        for keywords, url in SEARCH_INDEX:
            if any(tok in q for tok in keywords.split()):
                doc = FAKE_CORPUS[url]
                results.append({"id": normalize_url(url), "title": doc["title"], "url": url})
        return results

    def fetch(self, doc_id: str) -> dict[str, str]:
        url = normalize_url(doc_id)
        self._fetch_attempts[url] = self._fetch_attempts.get(url, 0) + 1
        if self._fetch_attempts[url] <= self.fail_fetch_times:
            raise TransientFetchError(f"upstream 503 for {url}")
        if url not in FAKE_CORPUS:
            raise KeyError(f"unknown document id: {doc_id}")
        doc = FAKE_CORPUS[url]
        return {"id": url, "title": doc["title"], "text": doc["text"], "url": url}


# ---------------------------------------------------------------------------
# PII detect → redact → audit
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE = re.compile(r"\+?\d[\d-]{7,}\d")


@dataclass
class PiiEvent:
    correlation_id: str
    url: str
    pii_types: list[str]
    action: str = "redact"


def redact_pii(text: str, *, correlation_id: str, url: str, audit: list[PiiEvent]) -> str:
    types: list[str] = []
    if _EMAIL.search(text):
        types.append("email")
        text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    if _PHONE.search(text):
        types.append("phone")
        text = _PHONE.sub("[REDACTED_PHONE]", text)
    if types:
        audit.append(PiiEvent(correlation_id=correlation_id, url=url, pii_types=types))
    return text


# ---------------------------------------------------------------------------
# Research harness
# ---------------------------------------------------------------------------

@dataclass
class Citation:
    claim_id: str
    url: str
    source_id: str
    excerpt: str


@dataclass
class ResearchTrace:
    run_id: str
    correlation_id: str
    plan: str
    turns: list[dict[str, Any]] = field(default_factory=list)
    url_seen: set[str] = field(default_factory=set)
    citations: list[Citation] = field(default_factory=list)
    pii_audit: list[PiiEvent] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)

    def checkpoint(self, label: str) -> None:
        self.checkpoints.append(
            {
                "label": label,
                "turns": len(self.turns),
                "urls": sorted(self.url_seen),
                "citations": len(self.citations),
                "ts": time.time(),
            }
        )


@dataclass
class ResearchAgent:
    web: FakeWeb
    breaker: CircuitBreaker
    max_turns: int = 10
    tool_budget: int = 20
    cached_briefs: dict[str, str] = field(default_factory=dict)

    def _cached_search(self, query: str, correlation_id: str) -> list[dict[str, str]]:
        key = idempotency_key("search", query.strip().lower())
        if key in self.web.cache:
            log("idempotent_hit", correlation_id=correlation_id, tool="search", event=key[:12])
            return self.web.cache[key]

        def call() -> list[dict[str, str]]:
            return self.web.search(query)

        results = retry_with_jitter(call, correlation_id=correlation_id, tool="search")
        self.web.cache[key] = results
        return results

    def _cached_fetch(self, url: str, correlation_id: str, trace: ResearchTrace) -> dict[str, str]:
        norm = normalize_url(url)
        key = idempotency_key("fetch", norm)
        if norm in trace.url_seen or key in self.web.cache:
            log("idempotent_hit", correlation_id=correlation_id, tool="fetch", url=norm)
            if key in self.web.cache:
                return self.web.cache[key]

        def call() -> dict[str, str]:
            return self.web.fetch(norm)

        doc = retry_with_jitter(call, correlation_id=correlation_id, tool="fetch")
        doc = dict(doc)
        doc["text"] = redact_pii(
            doc["text"], correlation_id=correlation_id, url=norm, audit=trace.pii_audit
        )
        self.web.cache[key] = doc
        trace.url_seen.add(norm)
        return doc

    def _synthesize(self, docs: list[dict[str, str]], trace: ResearchTrace) -> str:
        claims: list[str] = []
        for i, doc in enumerate(docs, start=1):
            sentence = doc["text"].split(".")[0].strip() + "."
            claim_id = f"C{i}"
            cite = Citation(
                claim_id=claim_id,
                url=doc["url"],
                source_id=doc["id"],
                excerpt=sentence[:160],
            )
            trace.citations.append(cite)
            claims.append(f"{sentence} [{claim_id}]({doc['url']})")
        return " ".join(claims)

    def _single_agent_loop(self, question: str, trace: ResearchTrace) -> str:
        tools_used = 0
        docs: list[dict[str, str]] = []
        queries = [
            "mcp search fetch",
            "citation fact check",
            "multi-agent research",
        ]
        for turn, query in enumerate(queries, start=1):
            if turn > self.max_turns or tools_used >= self.tool_budget:
                break
            results = self._cached_search(query, trace.correlation_id)
            tools_used += 1
            trace.turns.append({"turn": turn, "action": "search", "query": query, "n": len(results)})
            trace.checkpoint(f"after_search_{turn}")
            for hit in results:
                if tools_used >= self.tool_budget:
                    break
                doc = self._cached_fetch(hit["url"], trace.correlation_id, trace)
                tools_used += 1
                docs.append(doc)
                trace.turns.append({"turn": turn, "action": "fetch", "url": doc["url"]})
                trace.checkpoint(f"after_fetch_{tools_used}")
        if not docs:
            raise RuntimeError("no_documents_fetched")
        report = self._synthesize(docs, trace)
        trace.checkpoint("synthesize_cite")
        return report

    def _multi_agent_loop(self, question: str, trace: ResearchTrace) -> str:
        # Deterministic "workers": each owns one query; URL dedupe shared via trace.url_seen
        worker_queries = ["mcp search fetch", "citation fact check"]
        docs: list[dict[str, str]] = []
        tools_used = 0
        turn = 0
        for wq in worker_queries:
            if tools_used >= self.tool_budget:
                break
            turn += 1
            results = self._cached_search(wq, trace.correlation_id)
            tools_used += 1
            trace.turns.append({"turn": turn, "action": "search", "query": wq, "n": len(results)})
            for hit in results:
                if tools_used >= self.tool_budget:
                    break
                doc = self._cached_fetch(hit["url"], trace.correlation_id, trace)
                tools_used += 1
                docs.append(doc)
                trace.turns.append({"turn": turn, "action": "fetch", "url": doc["url"]})
                trace.checkpoint(f"worker_fetch_{tools_used}")
        # Third worker would re-search overlapping URLs — idempotency prevents re-fetch burn
        overlap = self._cached_search("mcp search fetch", trace.correlation_id)
        for hit in overlap:
            self._cached_fetch(hit["url"], trace.correlation_id, trace)
        if not docs:
            raise RuntimeError("multi_agent_empty")
        report = self._synthesize(docs, trace)
        # CitationAgent-style second pass: ensure every claim has a log entry (already in synthesize)
        trace.checkpoint("citation_agent")
        return report

    def run(self, question: str, *, mode: str = "multi") -> dict[str, Any]:
        correlation_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        cache_key = idempotency_key("brief", question.strip().lower())
        trace = ResearchTrace(
            run_id=run_id,
            correlation_id=correlation_id,
            plan=f"Research: {question}",
        )
        log("run_start", correlation_id=correlation_id, run_id=run_id, event=mode)

        try:
            if mode == "multi":
                if not self.breaker.allow():
                    raise RuntimeError(f"circuit_open:{self.breaker.state.value}")
                report = self._multi_agent_loop(question, trace)
                self.breaker.record_success()
                path = "multi_agent"
            else:
                report = self._single_agent_loop(question, trace)
                path = "single_agent"
        except Exception as multi_exc:  # noqa: BLE001
            log("fallback", correlation_id=correlation_id, event=f"multi_fail:{multi_exc}")
            self.breaker.record_failure()
            try:
                report = self._single_agent_loop(question, trace)
                path = "single_agent_fallback"
            except Exception as single_exc:  # noqa: BLE001
                log("fallback", correlation_id=correlation_id, event=f"single_fail:{single_exc}")
                if cache_key in self.cached_briefs:
                    report = self.cached_briefs[cache_key]
                    path = "cached_brief"
                else:
                    report = (
                        "Cached brief unavailable. Escalate to human queue. "
                        f"correlation_id={correlation_id}"
                    )
                    path = "human_queue_stub"
                    self.cached_briefs[cache_key] = report

        self.cached_briefs.setdefault(cache_key, report)
        return {
            "path": path,
            "report": report,
            "correlation_id": correlation_id,
            "run_id": run_id,
            "citations": [c.__dict__ for c in trace.citations],
            "pii_audit": [e.__dict__ for e in trace.pii_audit],
            "checkpoints": trace.checkpoints,
            "urls_fetched": sorted(trace.url_seen),
            "breaker_state": self.breaker.state.value,
            "turns": len(trace.turns),
        }


def main() -> None:
    random.seed(42)
    # Inject two transient fetch failures to exercise retries + breaker accounting
    agent = ResearchAgent(web=FakeWeb(fail_fetch_times=1), breaker=CircuitBreaker())
    result = agent.run(
        "How do MCP search/fetch and citation quality interact in multi-agent research?",
        mode="multi",
    )
    print(json.dumps({k: result[k] for k in (
        "path", "correlation_id", "urls_fetched", "turns", "breaker_state"
    )}, indent=2))
    print("--- report ---")
    print(result["report"])
    print("--- citations ---")
    print(json.dumps(result["citations"], indent=2))
    print("--- pii_audit ---")
    print(json.dumps(result["pii_audit"], indent=2))

    # Second run: force OPEN breaker (long recovery) so multi path sheds to single-agent
    agent.web.fail_fetch_times = 0
    agent.breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_s=3600.0)
    agent.breaker.state = BreakerState.OPEN
    agent.breaker.opened_at = time.monotonic()
    degraded = agent.run("force fallback path", mode="multi")
    assert degraded["path"] == "single_agent_fallback", degraded["path"]
    print("--- degraded path ---")
    print(degraded["path"], degraded["breaker_state"])

    # Third run: poison fetch layer so single-agent also fails → cached brief / human stub
    agent.web = FakeWeb(fail_fetch_times=100)
    agent.breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_s=3600.0)
    agent.breaker.state = BreakerState.OPEN
    agent.breaker.opened_at = time.monotonic()
    cached = agent.run("force fallback path", mode="multi")
    assert cached["path"] in {"cached_brief", "human_queue_stub"}, cached["path"]
    print("--- cached/human path ---")
    print(cached["path"])


if __name__ == "__main__":
    main()
```

Run: `python3 modules/14-ai-research-agent-snippet.py` after extracting the block, or paste into a file and execute. The harness exercises search → fetch → synthesize → cite, URL dedupe, retries, breaker transitions, and multi→single→cached-brief fallback with correlation IDs—no external APIs.

---

## Part 6 — Architectural System Design Scenarios

### Scenario A — Enterprise knowledge Deep Research with owned audit trail

**Problem statement**: A regulated enterprise needs analysts to run **~5k research reports/month** over **internal** contract/SQL stores **plus** public web, with claim-level citations, owned trajectory logs for auditors, and a hard **~$50/run** cost ceiling. Hosted Deep Research is acceptable for public-web pilots but fails the audit-ownership and internal-ranking requirements. Design a system that meets the **2–4 minute** p50/p95 design band for scoped reports while preventing termination/runaway failures.

**Proposed architecture** (component diagram):

```
┌────────────┐     ┌─────────────────────────────────────────┐
│  Analyst   │────►│           CONTROL PLANE                 │
│  UI / API  │     │  budget ($50) · turns(10–25) · fan-out  │
└────────────┘     │  reviewer + judge gates                  │
                   └───────────────┬─────────────────────────┘
                                   │
                   ┌───────────────▼─────────────────────────┐
                   │              DATA PLANE                 │
                   │  Lead → Workers → Synthesize → Cite     │
                   └─────┬─────────────┬─────────────┬───────┘
                         │             │             │
              ┌──────────▼──┐  ┌───────▼──────┐  ┌──▼──────────┐
              │ TOOL PROXIES│  │ PERSISTENCE  │  │ TELEMETRY   │
              │ MCP search  │  │ Memory plan  │  │ trajectory  │
              │ MCP fetch   │  │ artifacts    │  │ cite log    │
              │ (web+SQL RO)│  │ cite WORM    │  │ $ meters    │
              └─────────────┘  └──────────────┘  └─────────────┘
```


**Trade-off matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| **A1. Hosted Deep Research + vendor connector** | Vendor-priced; opaque unit economics | Vendor SLA undisclosed; often minutes | Low | Connector trust + vendor retention; weaker owned cite log | Scales with vendor quotas; limited custom stop rules |
| **A2. Own harness + read-only MCP search/fetch (recommended)** | Metered; budget **~$50** hard stop; ~**4×/15×** chat via single/multi | Design **2–4 min** with parallel workers (**≤90%** wall-clock cut) | Medium–high (harness, breakers, judges) | Zero-Trust MCP, tool RBAC, PII redact, immutable citation log | Horizontal workers + URL cache; fan-out caps bound TPM |
| **A3. One-shot RAG over warehouse** | Lowest token $ | Lowest latency | Low | Strong if corpus ACL’d | High QPS; **fails** path-dependent research |

**Decision rationale**: Choose **A2**. Audit ownership, custom stop rules, CRM/warehouse/PubMed-first ranking, and immutable citation logs are the newsletter’s explicit “build” triggers ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)). A1 cannot own the trajectory; A3 cannot mid-flight fetch. Use frontier lead + cheap workers (**84–98%** tokens at worker rate), CitationAgent, turn/**$50** caps, and breaker fallback multi→single→cached brief ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

---

### Scenario B — Breadth-first competitive research with citation rigor under cost tax

**Problem statement**: A strategy team runs complex, **breadth-first** competitive analyses (often **>10** parallel workstreams) where Anthropic-class multi-agent research shows **+90.2%** relative eval gains and up to **90%** wall-clock reduction—but burns **~15×** chat tokens. Leadership wants citation Fact Check closer to frontier **~77%** (not **~39%**) while avoiding the Onweller overload regime (~**150** tool calls / **~42%** Fact Check drop). Pick a topology that is economically viable only when report value ≫ token tax.

**Proposed architecture** (component diagram):

```
┌──────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                            │
│ effort rules: simple=1 · compare=2–4 · complex=>10       │
│ tool budget soft=20 · hard=40 · Fact-Check depth guard   │
└────────────────────────────┬─────────────────────────────┘
                             │ sync waves (3–5 workers)
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
   ┌─────────┐         ┌─────────┐         ┌─────────┐
   │ Worker  │         │ Worker  │         │ Worker  │
   │ search  │         │ search  │         │ search  │
   │ fetch   │         │ fetch   │         │ fetch   │
   └───┬─────┘         └───┬─────┘         └───┬─────┘
       │ artifact refs     │                   │
       └───────────────────┼───────────────────┘
                           ▼
                    ┌────────────┐     ┌──────────────┐
                    │ Lead synth │────►│ CitationAgent│
                    └────────────┘     └──────┬───────┘
                                              ▼
                                       ┌──────────────┐
                                       │ Judge rubric │
                                       │ + efficiency │
                                       └──────────────┘
```


**Trade-off matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| **B1. Single ReAct + MCP search/fetch** | **~4×** chat → **~$78/1k** at labeled prices | Medium–high (serial); hard to hit **2–4 min** on broad tasks | Medium | Small tool surface; easier RBAC | Limited by one context; weak for breadth-first |
| **B2. Orchestrator + parallel workers + CitationAgent (recommended)** | **~15×** chat → **~$292.50/1k**; needs high task value | Lower wall-clock via **3–5** parallel workers (**≤90%** cut) | High (sync waves, Memory, rainbow deploys) | Workers RO search/fetch; coordinator tool-less; cite log | Cap waves; avoid **50**-spawn; depth guard ≪ **150** tools |
| **B3. Max-depth scrape (unbounded tools)** | Highest; runaway toward **~$50**/stuck run without caps | Wall-clock dominated by slowest fetch | High noise / SEO bias | Larger SSRF + PII exposure surface | Illusory—Fact Check **drops ~42%** at **2→150** tools |

**Decision rationale**: Choose **B2** when queries are breadth-first, high-value, and parallelizable—the Anthropic Research fit ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Reject B3: more retrieval is not more truth ([Onweller et al.](https://arxiv.org/html/2605.06635)). Prefer B1 when steps are known/highly dependent or value cannot absorb the **15×** tax. Enforce effort rules, **3–5** cohort size, tool budgets, CitationAgent, and judge rubric including **tool efficiency** so “correct in 50 calls” loses to “correct in 5” ([Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

---

### Interview prompts (after both scenarios)

1. Walk search → fetch → synthesize → cite on the Part 1 diagram; where do URL idempotency keys and the immutable citation log live?
2. Derive **$/1k** for single (**~4×**) vs multi-agent (**~15×**) research under a labeled **$3/$15 per 1M** in/out price; where does the **~$50** runaway cap bind?
3. Research lacks vendor p50/p95/p99—how would you defend an **[inferred]** **2 / 4 / 6 minute** budget tied to the newsletter **2–4 minute** design target?
4. Why can increasing tool calls from **2 → 150** hurt Fact Check by **~42%** while Link Works stays high? What control-plane guard do you add?
5. Specify IDEMPOTENCY KEYS for `search` and `fetch`, the breaker state machine, and the multi-agent → single agent → cached brief fallback.
6. Design Zero-Trust read-only MCP for workers that read untrusted pages: tool RBAC, SSRF pins, PII detect→redact→audit, citation WORM log.
7. Scenario A vs B: when do you buy hosted Deep Research, when do you build the harness, and when do you refuse multi-agent despite the **+90.2%** eval lift?

---

## Sources

- [1] https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp — System Design Newsletter #168
- [2] https://www.anthropic.com/engineering/multi-agent-research-system — Anthropic multi-agent Research system
- [3] https://developers.openai.com/api/docs/mcp — OpenAI MCP search/fetch
- [4] https://github.com/openai/skills/blob/82d2c5b4/skills/.curated/chatgpt-apps/references/search-fetch-standard.md — search/fetch standard
- [5] https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small — Coordinator vs cheap workers
- [6] https://arxiv.org/html/2605.06635 — Onweller et al. citation evaluation
- [7] https://arxiv.org/pdf/2608.24306 — Agent blame / faithfulness localization
- [8] https://github.com/Parassharmaa/agent-fetch/ — SSRF-hardened agent fetch
- [9] https://github.com/EnvCommons/BrowseComp — BrowseComp fetch truncation patterns
- [10] `research/14-ai-research-agent.md` — consolidated research note (16 sources)
