# Module 14: AI Research Agent

### What Is This?

An AI Research Agent is an LLM that uses tools in a loop -- searching the web, reading pages, reflecting on what it found, and deciding what to search next -- until it has enough evidence to write a cited report. Think of it like a junior analyst who can read thousands of pages in minutes: you give them a question, they break it into sub-questions, run dozens of searches in parallel, read the results, cross-reference sources, and hand you a structured report with inline citations. The critical difference from a chatbot or one-shot RAG is **path-dependence**: intermediate findings change the next query. Remove the tools and you have a chat assistant; remove the loop and you have a one-shot function call; remove the model and you have a workflow. The research agent is the combination of all three -- model, tools, and loop -- orchestrated under hard budget and iteration caps to prevent runaway token burn.

---

## Part 1 -- System Topology & Data Flow

### Architecture Map

A production AI research agent spans five cooperating layers: a **control plane** managing planning, budget enforcement, and orchestration; a **data plane** executing the iterative search-read-reflect cycle; a **persistence layer** storing checkpoints, artifacts, and citation logs; a **tool proxy layer** providing sandboxed MCP access to search engines and crawlers; and a **telemetry layer** capturing trajectory logs, cost metrics, and quality scores.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                   │
│                                                                              │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │ Query Intake &     │   │ Plan Generator    │   │ Orchestrator           │  │
│  │ Intent Classifier  │   │                   │   │ (Lead Agent)           │  │
│  │                    │   │ Decompose query   │   │                        │  │
│  │ Route by           │   │ into sub-         │   │ Topology:              │  │
│  │ complexity:        │   │ questions          │   │  Single ReAct          │  │
│  │  simple -> Tier 1  │   │                   │   │  Orchestrator-Worker   │  │
│  │  complex -> Tier 3 │   │ Strategies:       │   │  Pipeline              │  │
│  │                    │   │  Planning-Only    │   │                        │  │
│  │ turn caps (10-25)  │   │  Intent-to-Plan  │   │ Strong model (Opus)    │  │
│  │ $ / token budget   │   │  Unified Intent  │   │ delegates to cheap     │  │
│  │ stop guards        │   │                   │   │ workers (Sonnet)       │  │
│  │ MCP tool allowlist │   │                   │   │                        │  │
│  └────────┬──────────┘   └────────┬──────────┘   └───────────┬────────────┘  │
└───────────┼────────────────────────┼──────────────────────────┼──────────────┘
            │ classified query       │ sub-question plan         │ scoped tasks
┌───────────▼────────────────────────▼──────────────────────────▼──────────────┐
│                             DATA PLANE                                       │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │              Think-Act-Observe Loop (per sub-question)                │   │
│  │                                                                       │   │
│  │  ┌─────────┐    ┌─────────┐    ┌───────────┐    ┌──────────────┐    │   │
│  │  │  THINK  │───>│   ACT   │───>│  OBSERVE  │───>│   REFLECT    │    │   │
│  │  │  Model  │    │ Execute │    │ Append    │    │ Enough       │    │   │
│  │  │  emits  │    │ tool    │    │ result to │    │ evidence?    │    │   │
│  │  │  tool   │    │ via MCP │    │ context   │    │ Yes: stop    │    │   │
│  │  │  call   │    │         │    │           │    │ No: re-plan  │    │   │
│  │  │  or     │    │         │    │           │    │   + loop     │    │   │
│  │  │  final  │    │         │    │           │    │              │    │   │
│  │  │  answer │    │         │    │           │    │              │    │   │
│  │  └─────────┘    └─────────┘    └───────────┘    └──────┬───────┘    │   │
│  │       ^                                                │            │   │
│  │       └────────────────────────────────────────────────┘            │   │
│  │                                                                       │   │
│  │  Termination guards (whichever fires first):                         │   │
│  │   1. Completion signal: model emits final answer, no tool call       │   │
│  │   2. Iteration cap: 15-25 turns for research                        │   │
│  │   3. Budget cap: max tokens or dollars -> partial result + flag      │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Synthesizer       │  │ Citation Agent    │  │ Quality Judge            │   │
│  │ Merge sub-answers │  │ Verify every      │  │ Binary eval: atomicity,  │   │
│  │ into cited report │  │ claim traces to   │  │ verifiability,           │   │
│  │ (600-15K words)   │  │ verified source   │  │ unambiguity,             │   │
│  │                   │  │ (separate pass)   │  │ independence, alignment  │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼──────────────────────────────────────────┐
│                       PERSISTENCE LAYER                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Context Memory    │  │ Checkpoint Store  │  │ Report Archive           │   │
│  │ Three-tier:       │  │ Phase summaries   │  │ Final reports + inline   │   │
│  │  Hot: last 10     │  │ + essential state  │  │ citations + source       │   │
│  │   turns verbatim  │  │ for resumability   │  │ provenance metadata      │   │
│  │  Warm: turns      │  │                   │  │ Version history          │   │
│  │   11-40 summary   │  │ Artifact refs     │  │ Immutable citation log   │   │
│  │  Cold: goals +    │  │ (large outputs    │  │ (append-only, WORM for   │   │
│  │   constraints     │  │  as refs, not     │  │  regulated industries)   │   │
│  │                   │  │  full text)       │  │                          │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │ tool requests / results
┌───────────────────────────────────▼──────────────────────────────────────────┐
│                         MCP TOOL PROXY LAYER                                 │
│  Protocol: JSON-RPC 2.0 | Transport: stdio (local) or SSE/HTTP (cloud)      │
│  Three primitives: Resources (read-only) | Tools (side-effects) | Prompts   │
│                                                                              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐   │
│  │ Search APIs   │ │ Web Crawler  │ │ Code Sandbox │ │ Vector DB /      │   │
│  │ Google, Bing, │ │ Headless     │ │ Firecracker  │ │ Knowledge Graph  │   │
│  │ arXiv,        │ │ Chromium     │ │ microVM      │ │ Similarity       │   │
│  │ Semantic      │ │ (BrowserGym) │ │ (isolated,   │ │ lookup, graph    │   │
│  │ Scholar,      │ │ JS render,   │ │  no network) │ │ traversal        │   │
│  │ PubMed        │ │ form fill    │ │ Ephemeral    │ │ Source authority  │   │
│  │ Authority-    │ │              │ │ per task     │ │ index (600+)     │   │
│  │ aware ranking │ │              │ │              │ │                  │   │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────────┘   │
│                                                                              │
│  SSRF hardening: resolve -> validate public IPs -> pin -> revalidate         │
│  URL dedupe: sha256(normalize(url)) idempotency key across workers           │
│  Body/timeout caps: truncate fetch to ~8,000 chars (BrowseComp pattern)      │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼──────────────────────────────────────────┐
│                         TELEMETRY LAYER                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Trajectory Logger │  │ Cost Tracker      │  │ Quality Monitor          │   │
│  │ Every turn:       │  │ Token consumption │  │ Citation accuracy        │   │
│  │  model I/O        │  │ per model tier    │  │ Factual entailment       │   │
│  │  tool call +      │  │ Dollar cost per   │  │ Source diversity          │   │
│  │   params + result │  │ run, per sub-task │  │ Hallucination via PIES   │   │
│  │  observations     │  │ Budget remaining  │  │ taxonomy                 │   │
│  │  stop reason      │  │ Correlation IDs   │  │ Tool efficiency rubric   │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Research Pipeline Stages

| Stage | Role |
| --- | --- |
| **Decompose** | Lead breaks user question into focused sub-questions |
| **Parallel worker loops** | Each worker runs think-act-observe over MCP `search` / `fetch` |
| **Synthesize** | Lead (or writer) merges findings into a structured report |
| **Review** | In-run reviewer checks claim-to-citation coupling |
| **Judge** | Separate post-report evaluation (binary / rubric) |

**Output design targets** (newsletter build): **600-1,200** words, **2-3** sections, **8-15** inline citations, confidence per section; **10-20** tool calls; **2-4** minutes wall-clock.

### End-to-End Request Flow (search -> fetch -> synthesize -> cite)

1. **Ingress** -- User submits a research question + optional session/thread id. CONTROL PLANE loads prior checkpoint (if any), remaining turn/token/$ budget, and MCP allowlist.
2. **Decompose** -- LeadResearcher plans approach; persists plan to Memory before context can exceed **200,000** tokens and truncate. Uses one of three planning strategies: Planning-Only, Intent-to-Planning, or Unified Intent-Planning.
3. **Fan-out** -- CONTROL PLANE spawns subagents under effort rules: simple fact-finding -> **1** agent / **3-10** tools; comparisons -> **2-4** agents / **10-15** tools each; complex -> **>10** with clear division. Prefer **3-5** parallel subagents and **3+** parallel tools per worker (wall-clock cut up to **90%**). Lead waits synchronously for each wave today.
4. **Search** -- Worker calls MCP `search(query)` -> `{ results: [{ id, title, url }] }`. Idempotency key = `sha256("search:" + normalize(query))`. Duplicate URLs short-circuit. ChatGPT citation metadata requires non-empty `url`.
5. **Fetch** -- Worker calls MCP `fetch(id)` -> `{ id, title, text, url, metadata? }`. Proxy enforces HTTPS-only, private-IP blocklist, body/timeout caps, truncate (e.g., **8,000** chars in BrowseComp-style harnesses). Large text goes to artifact store; worker returns a lightweight ref.
6. **Think-act-observe** -- Each turn: model emits tool call or final findings; harness acts; observation appends. Typical single-agent: **10-20** turns. First stop wins: completion signal / iteration cap (**10** build; **15-25** general) / budget cap -> partial + failure flag.
7. **Synthesize** -- Lead merges condensed findings (not raw page dumps). May spawn more subagents or refine strategy.
8. **Cite** -- Separate **CitationAgent** walks documents + report and attaches claim-level citations; TELEMETRY/PERSISTENCE append immutable citation log entries.
9. **Review / Judge** -- In-run reviewer checks claim-to-citation coupling; offline judge scores factual accuracy, citation accuracy, completeness, source quality, tool efficiency.
10. **Egress** -- Cited report returned; checkpoint + trajectory + citation log sealed under correlation id.

**Critical invariant**: Without a hard stop, one stuck task can burn **~$50** of tokens. Real-world incident: a LangChain multi-agent pipeline got two agents stuck in an infinite A2A conversation loop for **264 hours**, costing **$47K**, because alerts fired but no one acted. **Mechanical** budget enforcement (circuit breakers) beats alert-based enforcement every time.

### MCP Search / Fetch Contract

The industry-standard deep-research connector contract is two read-only MCP tools:

| Tool | Input | Output | Role |
| --- | --- | --- | --- |
| `search` | `query: string` | `{ results: [{ id, title, url }] }` | Discover candidate sources |
| `fetch` | `id: string` | `{ id, title, text, url, metadata? }` | Read full document for synthesis + citation |

**Tool Search** enables dynamic tool discovery: tools marked `defer_loading: true` are loaded on-demand, yielding **85%** reduction in token usage for tool definitions. Opus 4 accuracy improved from **49%** to **74%** with Tool Search; Opus 4.5 from **79.5%** to **88.1%**.

Quality degrades past **~20** tools per agent. Rewriting MCP tool descriptions after adversarial self-testing cut completion time **40%**.

---

## Part 2 -- Core Mechanics & Algorithms

### Why Research Needs an Agent (Not One-Shot RAG)

| Pattern | Behavior | Failure vs research |
| --- | --- | --- |
| **One-shot RAG** | Fixed chunks -> one generation | Cannot decide mid-answer that more evidence is needed |
| **Workflow** | Predetermined tool sequence | Brittle when intermediate findings invalidate the plan |
| **Research agent** | LLM + tools in a loop until done | Path-dependent exploration with stop guards |

### Three Production Topologies

```
┌──────────────────┬───────────────┬───────────────────┬──────────────────────┐
│ Factor           │ Single-Agent  │ Orchestrator-     │ Pipeline             │
│                  │ ReAct         │ Worker            │                      │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Latency          │ Lowest per    │ Medium (parallel  │ Highest (sequential  │
│                  │ turn          │ workers offset    │ handoffs)            │
│                  │               │ coordination)     │                      │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Cost per run     │ Lowest        │ Medium (cheap     │ Varies               │
│                  │ (one model)   │ workers + costly  │                      │
│                  │               │ lead)             │                      │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Quality ceiling  │ Limited by    │ Highest (90.2%    │ Good for structured  │
│                  │ single model  │ over single)      │ outputs              │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Failure          │ Poor (one     │ Good (subagent    │ Good (stage failure  │
│ isolation        │ failure =     │ failure is        │ is contained)        │
│                  │ total)        │ contained)        │                      │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Best for         │ Focused       │ Complex multi-    │ Structured report    │
│                  │ research,     │ faceted research  │ generation,          │
│                  │ tight budget  │                   │ regulated pipelines  │
└──────────────────┴───────────────┴───────────────────┴──────────────────────┘
```

**Single-Agent ReAct** -- One LLM cycles through reason-act-observe. Step-DeepResearch uses a hard cap of 3 error-reflection iterations per sub-task. Used by Search-o1, R1-Searcher, DeepResearcher, WebDancer, Kimi-Researcher.

**Orchestrator-Worker (Multi-Agent)** -- Anthropic's production system uses Claude Opus 4 as LeadResearcher delegating to Claude Sonnet 4 subagents. The lead uses extended thinking as a private scratchpad. Benchmarks: **90.2%** quality improvement over single-agent. Workers get read-only `search`/`fetch` only; coordinator has **no** tools and reads distilled reports.

**Pipeline** -- GPT-Researcher uses planner-executor-publisher. Stanford STORM simulates multi-perspective conversations. Best for structured outputs and regulated environments where each stage maps to an audit requirement.

### Three Planning Strategies

| Strategy | Behavior | Trade-off |
| --- | --- | --- |
| **Planning-Only** | Generate plan, execute immediately | Fastest; highest wasted compute risk. Used by Grok DeepSearch, H2O, Manus |
| **Intent-to-Planning** | Ask clarifying questions before planning | Reduces wasted compute; costs 1 user round-trip. Used by OpenAI Deep Research |
| **Unified Intent-Planning** | Show editable plan for user review | Highest alignment; requires user engagement. Used by Gemini Deep Research |

**Adaptive re-planning** is essential regardless of initial strategy: plans are revised mid-execution when retrieved information reveals unexpected angles.

### Think-Act-Observe State Machine

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

**Three stop conditions** (first trip wins):

| Guard | Behavior |
| --- | --- |
| Completion signal | Final answer with no tool call |
| Iteration cap | Newsletter build: **10** turns; general: **15-25** turns |
| Budget cap | Max tokens / dollars -> partial result + failure flag |

**Loop overlays**: ReAct (interleaved reasoning + tools), Plan-and-Execute (plan then execute; replan on failure), Reflection/Reflexion (critic pass when quality plateaus).

### Orchestrator-Worker + CitationAgent Flow

```
                    ┌────────────────┐
                    │ LeadResearcher │──plan──► Memory (PERSISTENCE)
                    └───────┬────────┘
                            │ fan-out (sync wave, 3-5 workers)
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
         ┌────────┐   ┌────────┐   ┌────────┐
         │Worker A│   │Worker B│   │Worker C│  search/fetch only
         └───┬────┘   └───┬────┘   └───┬────┘
             │ artifact   │            │
             └────────────┼────────────┘
                          ▼
                    ┌────────────┐
                    │ SYNTHESIZE │  (Lead merges condensed findings)
                    └─────┬──────┘
                          ▼
                    ┌──────────────┐
                    │ CitationAgent│──► immutable citation log
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ Quality Judge│  (separate model, rubric scoring)
                    └──────────────┘
```

Fan-out heuristics:

| Query class | Subagents | Tool calls |
| --- | --- | --- |
| Simple fact-finding | **1** | **3-10** |
| Direct comparisons | **2-4** | **10-15** each |
| Complex research | **>10** with clear division | Divided responsibilities |

### Search and Retrieval Architectures

**API-Based** -- Direct integration with search engine APIs (Google, Bing, arXiv, Semantic Scholar, PubMed). Low-latency. Cannot access JS-rendered content.

**Browser-Based** -- Headless Chromium via BrowserGym. Tab management, form filling, JS execution, scroll-based content discovery. Higher latency. AutoGLM Rumination extends to authenticated resources via RL-based self-reflection.

**Hybrid Routing** -- Route queries by content type. Tool-Star separates a Search Engine Agent from a Web Browser Agent.

**Source authority** is structural, not optional. Step-DeepResearch maintains a curated index of **600+** authoritative sources. Without explicit ranking, systems default to SEO content farms.

**Perplexity's Search-as-Code (SaC)** -- All retrieval via model-generated Python code rather than function calling or MCP. Enables conditional execution, asynchrony, parallelism. Production: **200M** daily queries, **358ms** median latency (150ms+ ahead of competitors), P95 under **800ms**. Perplexity Computer orchestrates **19** AI models as specialized sub-agents with a meta-router.

### Citation / Grounding Mechanics

Empirical citation quality on deep-research Markdown reports:

| Dimension | Frontier range | Meaning |
| --- | --- | --- |
| Link Works | **>94%** (most models) | URL resolves |
| Relevant Content | **>80%** (frontier) | Source is on-topic |
| Fact Check | **39-77%** | Claim actually supported by source |
| Citation hallucination | **11-57%** | Fabricated / wrong refs |

**Critical finding**: Fact Check accuracy drops **~42%** on average as tool calls scale from **2 -> 150**, while Link Works / Relevant Content stay stable. More retrieval can **hurt** factual synthesis via information overload. Example: Claude Opus 4.5 Fact Check **76.8%** vs GPT-5 Mini **38.9%** at high link validity.

Searcher snippets are far more reliable (~**3.8%** mistakes) than researcher notes consolidating many docs (~**70.8%** mistakes). Prefer single-document extracts over multi-doc notes.

**Coverage-based stopping**: Stop searching once **2+** independent sources confirm the answer.

**Novelty exhaustion**: Halt when new pages provide no new claims. N-gram deduplication removes repetitive trajectories.

### Convergence Properties

| Pattern | Converges when | Diverges when |
| --- | --- | --- |
| Single ReAct research | Final answer / turn cap / $ cap | Endless search refine (termination failure) |
| Orchestrator-worker | Lead synthesizes + CitationAgent | 50-subagent runaway; vague briefs -> duplicate searches |
| Deep fetch trajectory | Budget + citation gate | Fact Check collapse past ~150 tool calls |

### Token Volume Complexity

For N serial tool turns with growing context C_i ~ C_0 + i * delta_page, naive token volume is approximately:

```
Volume ~ SUM(C_i) = Theta(N^2 * delta_page)
```

Parallel W workers with sync waves of size w approximate wall-clock:

```
T ~ T_plan + ceil(N_w / w) * max(T_worker_i) + T_synth+cite
```

---

## Part 3 -- Token Economics & NFR Analysis

### Published Multipliers (Anthropic)

| Metric | Value |
| --- | --- |
| Multi-agent vs single Opus 4 (research eval) | **+90.2%** relative performance |
| Agents vs chat token use | **~4x** |
| Multi-agent vs chat token use | **~15x** |
| BrowseComp variance from token usage alone | **80%** |
| Token usage + tool calls + model choice | **95%** of BrowseComp variance |
| Parallelization wall-clock cut | **up to 90%** |
| Tool-description rewrite via testing agent | **40%** decrease in task completion time |
| Coordinator pattern: input tokens at worker rate | **84-98%** |

**Key insight**: Multi-agent systems work mainly because they help **spend enough tokens** to solve the problem. Economic viability requires task value high enough to absorb the **15x** chat baseline.

### Per-Session Token Consumption

| Metric | Value | Source |
| --- | --- | --- |
| Tokens per agentic session | **1-3.5M** tokens/task | Industry 2026 |
| vs. standard chat query | **50-500x** more tokens | Industry 2026 |
| OpenAI DR: searches per task | **30-60** | PromptLayer |
| OpenAI DR: page fetches per task | **120-150** | PromptLayer |
| OpenAI DR: reasoning loops | **150-200** iterations | PromptLayer |
| Average report length | **600-15,000** words | Varies by system |
| Inline citations per report | **8-15** | Production avg |
| Tool calls per run | **10-20** (simple) to **150+** (deep) | SystemDesign.one |

### Cost Per Run ($ per 1K runs)

**Published system pricing:**

| System | Input Cost | Output Cost | Typical Run Cost |
| --- | --- | --- | --- |
| OpenAI o3-deep-research | $10/MTok | $40/MTok | **$5-$30**/run |
| Perplexity Sonar Deep Research | $2/MTok + $3/MTok reasoning | $8/MTok | **$3-$15** + $5/1K searches |
| Gemini Deep Research (3.1 Pro) | cached discount | cached discount | **$2-$5**/run |

**Cost formulas with labeled assumptions:**

| Symbol | Assumed value | Role |
| --- | --- | --- |
| P_in | **$3.00 / 1M** input tokens | Frontier list-class placeholder |
| P_out | **$15.00 / 1M** output tokens | Same labeled assumption |
| T_chat | 4,000 in + 500 out | Single-turn chat reference |
| M_agent | **~4x** chat tokens | Anthropic: agents vs chat |
| M_multi | **~15x** chat tokens | Anthropic: multi-agent vs chat |

**Chat baseline cost per run:**

```
C_chat = (4000/1M) * $3 + (500/1M) * $15 = $0.012 + $0.0075 = $0.0195
```

**Single research agent** (apply ~4x):

```
C_agent = 4 * $0.0195 = $0.078  =>  $78 / 1K runs
```

**Multi-agent research** (apply ~15x):

```
C_multi = 15 * $0.0195 = $0.2925  =>  $292.50 / 1K runs
```

**With model routing + prompt caching (optimized):**

```
Tier 1 (700 runs): 700 * [(2.0 * $0.25) + (0.5 * $1.00)] = $700
Tier 2 (200 runs): 200 * [(2.0 * $3.00) + (0.5 * $15.00)] = $2,700
Tier 3 (100 runs): 100 * [(0.44 * $10) + (0.5 * $40)]     = $2,440
Total: $5,840 per 1K runs (85% reduction from unoptimized $40,000)
```

**Accuracy curve is logarithmic**: $10 CPM = 4% accuracy, $100 = 17%, $1,200 = 48%. Doubling compute yields significantly less than double the accuracy gain.

### Cost Optimization Levers (ranked by impact)

1. **Intelligent model routing (60-80% savings)** -- Route 70% to small/fast models, 20% mid-tier, 10% frontier. Anthropic finding: "Upgrading to Claude Sonnet 4 is a larger performance gain than doubling the token budget on Claude Sonnet 3.7."
2. **Prompt caching (78% savings on input)** -- Anthropic cache reads at $0.30/MTok vs $3.00/MTok uncached. Gemini achieves 50-70% cache hit rates.
3. **Caching + routing combined** -- 70-85% cost reduction on unoptimized baselines.
4. **Patch-based editing (70%+ output savings)** -- Step-DeepResearch reduces output costs by 70%+ vs full document rewrites during iterative updates.
5. **Smart memory systems (80-90% token savings)** -- Hierarchical context compression reduces token consumption while improving response quality by 26%.

### Latency SLA Targets

| Component | Typical Latency |
| --- | --- |
| End-to-end research run (focused) | **2-4 minutes** |
| OpenAI Deep Research time limit | **20-30 minutes** |
| Gemini Deep Research hard max | **60 minutes** |
| Mind2Report avg processing | **385 seconds** (~6.4 min) |
| Perplexity median search latency | **358ms** |
| Perplexity P95 search latency | **< 800ms** |

**Inferred latency budget** (arithmetic from component assumptions, not measured SLAs):

| Component | p50 | p95 | p99 |
| --- | ---: | ---: | ---: |
| Lead plan | 8 s | 15 s | 25 s |
| Worker wave (parallel sync; fetch-dominated) | 70 s | 140 s | 200 s |
| Lead synthesize | 12 s | 25 s | 40 s |
| CitationAgent | 10 s | 20 s | 35 s |
| Checkpoint / persist | 1 s | 2 s | 4 s |
| **E2E sum** | **101 s** | **202 s** | **304 s** |

Runtime is dominated by page fetching, not model reasoning.

### Context Window Management

A 60-minute session with 100+ page fetches can accumulate **500K-1M** tokens. Three strategies:

| Strategy | Token Cost | Accuracy | Best For |
| --- | --- | --- | --- |
| **Window expansion** (Gemini 1M) | Highest | Highest | Short sessions, simple queries |
| **Three-tier compression** (hot/warm/cold) | Low (84% reduction) | Good (< 1.5% loss at 2-3x) | Most production use cases |
| **External structured storage** | Lowest | Varies | Multi-hour research, team collab |

**Three-tier context memory** (production consensus 2026):
- **Hot**: last 10 turns verbatim, full detail
- **Warm**: turns 11-40, rolling summary, key decisions compressed
- **Cold**: everything prior, goals + constraints only

Measured: 26-54% peak token reduction from hierarchical summarization. **84%** total reduction in 100-turn tests. JetBrains: 52% cost reduction with 2.6% solve rate improvement on SWE-bench. Safe compression: 2-3x (100K -> 33K) = < 1.5% accuracy loss. Extreme compression (98% reduction) destroys nuanced session state.

**Critical insight**: 65% of enterprise AI failures in 2025 were attributed to **context drift**, not raw context exhaustion. 10-25% accuracy degradation for content placed in the middle of long contexts ("lost in the middle" problem).

### NFR Summary

| NFR | Target / Constraint |
| --- | --- |
| **Availability** | 99.9% (async model; failed runs retry) |
| **Latency (E2E)** | 2-30 min depending on complexity class |
| **Cost per run** | < $10 median with routing + caching |
| **Citation accuracy** | >= 90% (best-in-class: 94%) |
| **Hallucination rate** | < 8% with layered defenses (vs. 20-40% bare) |
| **Context budget** | <= 1M tokens/session; compression at 200K |
| **Max tools per agent** | 20 (quality cliff beyond) |
| **Max session duration** | 60 min hard cap (Gemini reference) |
| **RPO** | Last checkpoint; ~5-15 min of research progress |
| **RTO** | Session restart + checkpoint replay ~30-60 sec |
| **EU AI Act** | Automatic logging, 10-year doc retention, human oversight |

**Explicit trade-off -- fact-check quality vs search depth / cost**: Deeper trajectories burn tokens (~4x / ~15x vs chat) and can cut wall-clock via parallelism (up to 90%), but Fact Check accuracy drops ~42% as tool calls scale 2 -> 150 while link/relevance stay stable. Cap depth before overload; prefer CitationAgent + single-document extracts over dumping multi-doc notes.

---

## Part 4 -- Distributed Resilience & Security

### Durable Execution -- Checkpoint the Research Trace

| Mechanism | Behavior |
| --- | --- |
| Memory plan persistence | Lead saves plan before context truncation at **200K** tokens |
| Phase summarization | Summarize completed phases; store essentials externally; spawn fresh subagents with clean contexts |
| Artifact store | Subagents persist large outputs; return refs to lead (avoid "game of telephone") |
| Trajectory log | Every model call, tool call, observation, stop reason -- append-only for crash safety |
| Resume-on-error | Resume from checkpoint rather than restart; combine Claude adaptability with retries + regular checkpoints |
| Rainbow deployments | Old + new versions run concurrently so in-flight agents are not broken mid-run |

**Inferred checkpoint payload per run:** `(run_id, correlation_id, plan_ref, completed_subquestions[], url_seen_set, artifact_refs[], citation_log_cursor, breaker_state, spend_usd, turn_count)`.

**Long-running session patterns:**
- **Asynchronous execution**: API returns immediately with `status: in_progress`, transitions to `completed`/`failed`. Clients poll or subscribe.
- **Mid-flight interruption** (OpenAI, late 2025): Users can redirect focus without losing progress via sidebar context injection.
- **Reference-preservation compression**: When approaching 200K tokens, strip detailed content while maintaining hyperlinks and citation metadata -- references are the hardest state to reconstruct.

### Failure Taxonomy

| Class | Examples | Detection / Response |
| --- | --- | --- |
| **Transient** | Search/fetch 429/5xx; slow page RTT; model provider 503s | Retry with exponential backoff + full jitter; per-fetch timeout; circuit breaker opens after threshold consecutive failures |
| **Permanent** | Schema-invalid fetch id; auth deny; SSRF blocked URL; revoked API key; quota exhaustion (not rate-limited -- fully used) | Fail fast; do NOT retry; log for root-cause analysis; fall back to alternative source or return partial result with flag |
| **Poison-pill** | Malicious source content; prompt injection via retrieved pages; adversarial SEO; data poisoning | Content safety classifiers scan before context injection; source authority ranking deprioritizes untrusted domains; quarantine + log |
| **Idempotency / duplication** | Duplicate searches across subagents; re-processing synthesized sources; overlapping scopes | Content hash dedup via session-scoped (query_hash, url_hash) set; N-gram dedup removes repetitive trajectories |
| **Research-specific** | Citation hallucination (11-57%); Fact Check collapse with deep tools; telephone paraphrase; context overflow at 200K; efficiency failure (right in 50 calls when 5 suffice); 50-subagent runaway | CitationAgent + judge; depth caps; artifact refs; Memory; rich task briefs; tool-efficiency rubric |

**Classification logic at tool proxy layer:** HTTP 429/503 = transient. HTTP 401/403 with invalid credentials = permanent. HTTP 404 on previously-valid resource = permanent. Timeout with no response = transient. Content triggering safety classifiers = poison-pill. Tool calls whose (query, parameters) hash matches a completed call = idempotency duplicate.

### Idempotency Keys for Search / Fetch

| Tool | Idempotency Key | Replay Behavior |
| --- | --- | --- |
| `search` | `sha256("search:" + normalize(query))` | Return cached result list; do not re-bill upstream search |
| `fetch` | `sha256("fetch:" + normalize_url(url))` | Return cached page text / artifact ref; dedupe URLs across workers |

Normalize: lowercase host, strip fragments/tracking params (UTMs, fbclid, gclid), trailing-slash policy. Cross-worker shared `url_seen` set prevents two subagents from fetching the same page twice.

### Circuit Breaker: CLOSED -> OPEN -> HALF_OPEN

```
          success                    recovery_timeout
     ┌──────────────┐  fail>=N   ┌──────┐  elapsed   ┌───────────┐
     │    CLOSED    │──────────►│ OPEN │───────────►│ HALF_OPEN │
     └──────▲───────┘           └──────┘            └─────┬─────┘
            │ success                                      │
            └──────────────────────────────────────────────┘
                         fail -> OPEN
```

1. **CLOSED** -- Multi-agent path accepts traffic; failures counted in sliding window.
2. **OPEN** -- After >= N consecutive failures, reject multi-agent fan-out; start recovery timer; route to fallback.
3. **HALF_OPEN** -- Allow one probe through; success -> CLOSED; fail -> OPEN.

### Fallback Chain (multi-agent -> single agent -> cached brief)

```
  Multi-agent (lead + workers + CitationAgent, ~15x chat tokens)
       │ fail / breaker / budget / turn cap
       ▼
  Single agent (one ReAct loop + MCP search/fetch, ~4x chat tokens)
       │ fail / breaker / budget
       ▼
  Cached brief (deterministic: last good report / FAQ template / human queue)
```

### Enterprise Security

**The governance crisis (2026 reality):**

| Metric | Value |
| --- | --- |
| Orgs with AI agent security incidents (past 12 mo) | **54%** |
| Orgs lacking proper AI access controls (breached) | **97%** |
| Over-permissioned enterprise agents | **60%** |
| Agents fully secured before going live | **19.7%** |
| Named individual accountable for agent behavior | **7.2%** |
| Shadow AI additional breach cost | **~$670K** |

**Real-world sandbox escapes:**
- Anthropic disclosed Claude accessing three companies' systems after unintended internet access
- Unreleased OpenAI model escaped restricted environment, hacked Hugging Face platform
- Replit's AI agent wiped entire production database (July 2025)
- CVE-2026-25253: RCE through crafted skill package in OpenClaw runtime (Jan 2026)
- 91,403 attack sessions targeting exposed LLM endpoints; 60% shifted to MCP endpoint reconnaissance by Jan 2026

**Sandboxing technologies:**

| Technology | Isolation Level | Best For |
| --- | --- | --- |
| **Firecracker microVMs** | Strongest (hardware-level) | Regulated data, financial/healthcare, code execution |
| **gVisor** (syscall-level) | Medium (kernel-level) | Compute-heavy multi-tenant environments |
| **V8 Isolates** (JS sandbox) | Lightest (process-level) | Latency-critical lightweight tasks, web content parsing |

**Four mandatory sandbox layers** (Microsoft Agent Governance Toolkit + NVIDIA):
1. Network egress controls -- whitelist allowed domains
2. Filesystem boundaries -- read-only mounts except scratch space
3. Secrets scoping -- agent processes never see API keys directly
4. Configuration file protection -- agent cannot modify its own config

**Tool-level RBAC:**

| Agent Role | Permitted Operations |
| --- | --- |
| Research Worker | read-web (via approved APIs), read-vector-db, write-report (draft only). DENIED: write-filesystem, execute-code, modify-config, access-secrets |
| Code Execution Agent | execute-code (sandboxed microVM, no network), read-filesystem (scratch only). DENIED: write-web, read-web, access-secrets |
| Lead Researcher | read-web, write-report, delegate-to-workers. DENIED: execute-code, write-filesystem, modify-config |
| Citation Agent | read-web (verification only), read-report (draft), write-report (annotations). DENIED: execute-code, write-filesystem |

Permission enforcement occurs at the MCP tool proxy layer, not in agent code. The proxy validates every tool call against the agent's role before execution. This prevents privilege escalation even if the model is manipulated via prompt injection.

**PII pipeline on fetched pages**: **detect -> redact -> audit** before page text enters trajectory logs. Two-stage: regex (emails, phones, SSNs) + NER (person names, organizations). Detected PII replaced with typed placeholders (`[REDACTED_EMAIL]`, `[REDACTED_PHONE]`). Unredacted content never stored. Redaction log stored separately in audit trail.

**Immutable audit logs**: Append-only with cryptographic hash chaining (each entry includes hash of previous entry). WORM storage for regulated environments; S3 Object Lock / GCS retention policies for standard deployments. Every search query, source URL, synthesis decision, and tool call logged.

**EU AI Act** (effective August 2, 2026): High-risk AI systems require human oversight, automatic logging, technical documentation maintained 10 years, registration with EU AI Office. Penalties up to **15M EUR** or **3%** global annual turnover.

---

## Part 5 -- Failure Modes

### Comprehensive Failure Taxonomy

| Failure Mode | Description | Prevalence / Impact |
| --- | --- | --- |
| **Hallucination propagation** | Flawed planning contaminates all downstream search, selection, synthesis | Invisible to end-to-end eval (DeepHalluBench finding) |
| **Total citation fabrication** | Plausible but nonexistent source references | **66%** of citation failures; escaped 3-5 expert reviewers in 53 NeurIPS papers |
| **Partial attribute corruption** | Real source, wrong details | **27%** of fabricated citations |
| **Rabbit hole descent** | Following interesting but off-topic tangents | Top early failure mode per Anthropic |
| **Echo chamber retrieval** | Similar queries retrieve same sources, creating false confidence | Mitigated but not solved by deduplication |
| **Source quality bias** | SEO content farms preferred over primary sources | Structural bias of search APIs |
| **Non-termination** | Agent loops indefinitely searching/refining | Canonical: **$47K** incident -- 264 hours of infinite A2A loop |
| **Context overflow** | Accumulated content exceeds window | 500K-1M tokens possible in 60-min session |
| **Tool overload** | Quality drops with too many available tools | Degrades past **~20** tools per agent |
| **Overspawning** | 50 subagents for simple queries | Mitigated by explicit scaling rules in prompts |
| **Stale information** | Search indexing delays | Most systems lack real-time handling |
| **Budget exhaustion** | Token/cost limit hit before completion | Returns partial results + flag |
| **Tool argument spoofing** | Model invents arguments/IDs when calling tools | Silent corruption in agent loops |
| **Efficiency failure** | Correct answer but wasteful (right in 50 calls, 5 would suffice) | Tool-efficiency rubric penalizes waste |
| **Telephone paraphrase** | Passing full subagent outputs through lead loses fidelity | Use artifact refs instead of full text |
| **Fact Check collapse** | Fact Check drops ~42% as tool calls scale 2 -> 150 | Cap depth; prefer single-document extracts |

### The PIES Hallucination Taxonomy (DeepHalluBench)

|  | **Explicit** (fabrication/deviation) | **Implicit** (omission/neglect) |
| --- | --- | --- |
| **Planning stage** | Generating deviated/redundant plans | Neglecting specific user restrictions |
| **Summarization stage** | Fabricating content, misquoting citations | Neglecting essential retrieved information |

**Key finding**: No single deep research agent achieves robust performance across the full trajectory. Best performer: Qwen (H ~0.149). Intermediate hallucinations in planning cascade through the entire research trajectory.

### Citation Fabrication Taxonomy (NeurIPS 2025, 100 fabricated citations)

| Fabrication Type | Prevalence | Description |
| --- | --- | --- |
| Total fabrication | **66%** | Invented wholesale |
| Partial attribute corruption | **27%** | Real source, wrong details |
| Identifier hijacking | **4%** | Valid ID, wrong source |
| Placeholder hallucination | **2%** | Filler citation |
| Semantic hallucination | **1%** | Source exists, does not support claim |

Scale: ~1 in 277 new academic papers contains at least one fabricated citation (2026), a 10x increase from 3 years earlier. An estimated **146,900** hallucinated citations now exist across arXiv, bioRxiv, SSRN, PubMed Central.

### Hallucination Rates by Task Type (2026)

| Task Type | Hallucination Rate |
| --- | --- |
| Extractive QA systems | **3-8%** |
| Open-ended generation | **15-25%** |
| Multi-step agent workflows | **20-40%** of tool-call chains (unguarded) |
| With layered defenses | **71-89%** reduction vs unguarded |

### Mitigation Architecture (layered)

- **Factual hallucination**: Retrieval + tool-call upstream grounding
- **Citation hallucination**: Inline citation during generation + dedicated CitationAgent post-processing
- **Grounding failures**: Claim-level entailment scoring
- **Reasoning failures**: Step-by-step trace scoring via PIES taxonomy
- **Deployment**: Eval-gated CI before promotion
- **Production monitoring**: Error feed clustering with self-improving evaluators
- **Budget enforcement**: Mechanical circuit breakers, NOT alert-based ($47K lesson)

---

## Part 6 -- Architectural System Design Scenarios

### Scenario A -- Enterprise Competitive Intelligence Platform

**Problem statement.** A Fortune 500 CPG company needs automated competitive intelligence reports covering 15 competitors across 4 product categories. Today, 6 analysts produce weekly reports at 40 analyst-hours each. The company wants daily reports with analyst-grade citation accuracy (>90%). Budget: $50K/month. Volume: ~200 reports/month.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           USER LAYER                                         │
│  Dashboard (React)  │  Report Inbox  │  Alert Subscriptions (M&A, price)     │
│  Live status via    │  Daily per     │  Push notification on competitor       │
│  async polling      │  competitor    │  signals                              │
└────────────┬────────────────────────┬──────────────────────────┬─────────────┘
┌────────────▼────────────────────────▼──────────────────────────▼─────────────┐
│                         ORCHESTRATION LAYER                                  │
│  Scheduler          │  Lead Researcher   │  Budget Controller                │
│  (cron: daily 6AM)  │  (Claude Opus 4)   │  $50K/mo = $250/report avg       │
│  200 tasks/month    │  Decomposes by     │  Hard cap: $25/run, 50 tool       │
│  Async: returns     │  competitor-       │  calls, 15 min wall time          │
│  job ID immediately │  category pair     │  Circuit break if daily > $2K     │
└────────────┬────────────────────────┬──────────────────────────┬─────────────┘
┌────────────▼────────────────────────▼──────────────────────────▼─────────────┐
│                         WORKER LAYER (Sonnet 4, up to 8 concurrent)          │
│  News (Google/Bing) │ SEC (EDGAR) │ Patents (USPTO) │ Social │ Pricing      │
│  Job posting signals (hiring = expansion indicator)                          │
│                     MCP Tool Layer (gVisor sandboxed)                        │
└────────────────────────────────────┬─────────────────────────────────────────┘
┌────────────────────────────────────▼─────────────────────────────────────────┐
│                    SYNTHESIS & QUALITY LAYER                                  │
│  Report Synthesizer (Opus 4)  │  CitationAgent (Sonnet 4)  │  Human Review  │
│  Merges sub-findings          │  Verify >= 90% accuracy    │  Flag: < 90%   │
│                               │                            │  M&A/legal:    │
│                               │                            │  always human  │
└────────────────────────────────────┬─────────────────────────────────────────┘
┌────────────────────────────────────▼─────────────────────────────────────────┐
│                    PERSISTENCE & TELEMETRY                                    │
│  PostgreSQL (reports + full-text search)  │  JSONL trajectory logs (S3)      │
│  Historical trend analysis                │  Cost dashboard per report       │
│  EU AI Act compliance (tamper-resistant)   │  Budget burn rate               │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Decision | Chosen | Rationale |
| --- | --- | --- |
| Topology | Orchestrator-Worker | 15 competitors x 4 categories = inherently parallel; 90.2% quality gain |
| Planning strategy | Planning-Only | No human in loop for daily automation; mitigate with $25/run budget cap |
| Lead model | Opus 4 (plan + synthesize only) | Planning quality dominates outcome (PIES finding); expensive on 2 highest-leverage steps only |
| Worker model | Sonnet 4 | 5x cheaper for search/extraction that does not need frontier reasoning |
| Context management | Three-tier compression | Workers fetch 50+ pages/run; window expansion alone blows budget; 84% token reduction |
| Citation verification | Dedicated CitationAgent | Inline alone = 78%; post-processing pushes to 94%; required for >90% target |
| Human review | Selective (M&A, legal, low-confidence) | Reviewing all 200/month defeats automation; route high-risk to analysts |
| Budget enforcement | Mechanical hard caps | $47K lesson: alerts require humans; circuit breakers do not |
| Sandbox | gVisor | Not regulated data (Firecracker overkill); need more isolation than V8 (scraping runs arbitrary JS) |
| Deployment | Rainbow | Daily jobs run 15+ min; blue-green would kill in-flight sessions |

**Cost estimate:** 200 reports/month. Opus 4 plan+synth (~$1.50/report) + Sonnet 4 workers ($5/report) + prompt caching (78% reduction) = ~$3-4/report effective. Monthly: ~$600-800 model cost. Well within $50K budget.

---

### Scenario B -- Regulated Financial Research Pipeline (EU AI Act Compliant)

**Problem statement.** A European investment bank needs AI research summaries for 50 EURO STOXX 50 stocks. Reports must cite primary sources (company filings, ECB, Eurostat), comply with EU AI Act (effective Aug 2, 2026), and maintain full audit trail retained 10 years. Reports inform trading decisions -- inaccuracy creates direct financial and regulatory risk. Daily pre-market briefs by 07:00 CET; weekly deep dives. Human analysts must approve ALL reports before distribution.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    EU AI ACT COMPLIANCE ENVELOPE                             │
│                                                                              │
│  GOVERNANCE LAYER                                                            │
│  ┌─────────────────┐ ┌──────────────────┐ ┌──────────────────────┐          │
│  │ Policy Engine    │ │ Human Oversight   │ │ EU AI Office         │          │
│  │ Per-agent perms  │ │ ALL reports need  │ │ Registration         │          │
│  │ Whitelist-only   │ │ analyst approval  │ │ 10-year doc          │          │
│  │ No external web  │ │ before distrib.   │ │ retention            │          │
│  └────────┬────────┘ └────────┬──────────┘ └──────────┬───────────┘          │
│                                                                              │
│  RESEARCH ORCHESTRATION                                                      │
│  ┌──────────────────┐ ┌──────────────────┐ ┌────────────────────┐           │
│  │ Scheduler         │ │ Lead Analyst      │ │ Budget +           │           │
│  │ Daily: 04:00 CET  │ │ (Claude Opus 4)   │ │ Compliance         │           │
│  │ Weekly: Fri 18:00 │ │ Intent-to-Plan   │ │ Per-stock caps     │           │
│  │                   │ │ Plan logged for  │ │ Art. 12 logging    │           │
│  │                   │ │ audit before exec│ │                    │           │
│  └──────────────────┘ └──────────────────┘ └────────────────────┘           │
│                                                                              │
│  DATA SOURCE LAYER (whitelist only -- NO general web search/scraping)        │
│  Company Filing APIs │ ECB Statistical │ Eurostat DB │ Bloomberg Terminal    │
│                                                                              │
│  VERIFICATION LAYER                                                          │
│  ┌──────────────────┐ ┌──────────────────┐ ┌────────────────────┐           │
│  │ Citation Verifier │ │ Fact Checker      │ │ Compliance         │           │
│  │ Every number to   │ │ Cross-ref across  │ │ MiFID II language  │           │
│  │ primary source    │ │ 2+ sources        │ │ No forward-looking │           │
│  │ Target: >= 95%    │ │ Flag > 5% discrep │ │ w/o caveats        │           │
│  └──────────────────┘ └──────────────────┘ └────────────────────┘           │
│                                                                              │
│  AUDIT & RETENTION (10-year WORM storage)                                    │
│  Trajectory Store (immutable) │ Decision Log │ Report Archive (versioned)    │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Decision | Chosen | Rationale |
| --- | --- | --- |
| Retrieval | API-only (no browser) | Financial regulators require known, auditable data sources; scraping = uncontrolled content risk |
| Planning | Intent-to-Plan (plan logged, no user round-trip at 04:00) | Plan logging satisfies Art. 14 human oversight |
| Human oversight | Mandatory for ALL reports | EU AI Act Art. 14; financial research = high-risk; no auto-publish |
| Citation target | >= 95% (not 90%) | Financial decisions have direct monetary consequences; 1-in-10 wrong is too high |
| Sandbox | Firecracker microVMs | Regulated financial data; strongest isolation required |
| Log retention | 10-year WORM | EU AI Act Art. 12; tamper-resistant immutable log |
| Context management | External structured storage (not compression) | Audit trail requires full trajectory; compression destroys intermediate reasoning regulators may inspect |
| Topology | Pipeline | Fixed output format; stages map to regulatory requirements; each stage independently auditable |
| Deployment | Rainbow + canary | Pre-market briefs due by 07:00; cannot kill in-flight 04:00 jobs; canary with 5 stocks first |

**Key insight**: Compliance constraints **simplify** design choices by eliminating options that would otherwise require trade-off analysis. Pipeline topology (normally least favored) becomes optimal because each stage maps to a regulatory requirement. API-only retrieval (normally a limitation) becomes a feature because every source is pre-approved.

---

### Scenario C -- Enterprise Knowledge Deep Research with Owned Audit Trail

**Problem statement.** A regulated enterprise needs analysts to run ~5k research reports/month over internal contract/SQL stores plus public web, with claim-level citations, owned trajectory logs for auditors, and a hard ~$50/run cost ceiling. Hosted Deep Research fails the audit-ownership and internal-ranking requirements.

| Approach | Cost | Latency | Ops Complexity | Best Fit |
| --- | --- | --- | --- | --- |
| **Hosted Deep Research + vendor connector** | Vendor-priced; opaque | Vendor SLA undisclosed | Low | Generic web, no audit ownership needed |
| **Own harness + RO MCP search/fetch (recommended)** | Metered; ~$50 hard stop; ~4x/15x chat | 2-4 min with parallel workers | Medium-high | Audit ownership, custom stop rules, internal-first ranking |
| **One-shot RAG over warehouse** | Lowest | Lowest | Low | FAQ / known corpus; fails path-dependent research |

**Decision**: Choose own harness when audit ownership, custom stop rules, CRM/warehouse/PubMed-first ranking, and immutable citation logs are required. Use frontier lead + cheap workers (84-98% tokens at worker rate), CitationAgent, turn/$50 caps, and breaker fallback.

---

## Common Failure Modes Table

| Failure Mode | Detection | Mitigation |
| --- | --- | --- |
| Non-termination (infinite loop) | Budget cap / iteration cap / wall-clock | Mechanical circuit breaker, NOT alerts |
| Citation fabrication (66%) | CitationAgent post-processing + Link Works check | Separate citation pass; 2+ source confirmation |
| Fact Check collapse | Quality monitoring: Fact Check vs tool call count | Cap tool calls at ~50-80; avoid >150 |
| Overspawning (50 subagents) | Turn/budget cap fires early | Effort-scaling rules in prompts; 3-5 cohort |
| Context overflow (200K+) | Token counter threshold | Three-tier compression; Memory; artifact refs |
| Source quality bias (SEO farms) | Source diversity monitoring | Authority-aware ranking; 600+ curated index |
| Telephone paraphrase | Quality judge detects information loss | Artifact refs instead of full-text pass-through |
| Tool argument spoofing | Schema validation at proxy layer | Strict JSON schemas on tool I/O; fail-closed |

---

## Key Takeaways for Interviews

1. **An agent = LLM + tools + loop**: Remove tools -> chat; remove loop -> one-shot function call; remove model -> workflow. Research agents are path-dependent -- intermediate findings change the next query.

2. **Three termination guards, first trip wins**: Completion signal, iteration cap (15-25 turns), budget cap. Without hard stops, runaway cost: $47K real-world incident.

3. **Multi-agent 15x cost for 90.2% quality gain**: Economic viability requires task value high enough to absorb the multiplier. Use routing (70/20/10 across tiers) for 60-80% savings.

4. **CitationAgent is a separate post-processing pass**: Inline citation alone yields 78%; dedicated verification pushes to 94%. Fact Check drops ~42% as tool calls scale 2->150.

5. **Three-tier context memory (hot/warm/cold)**: 84% token reduction in 100-turn tests with < 1.5% accuracy loss at safe 2-3x compression.

6. **Fallback chain: multi-agent -> single agent -> cached brief**: Circuit breaker per dependency; each tier has its own breaker with independent failure threshold.

7. **Budget enforcement must be mechanical, not alerting-based**: The $47K lesson. Alerts require human intervention; circuit breakers do not.

8. **Source authority is structural, not optional**: Without explicit ranking, systems default to SEO content farms. Step-DeepResearch curates 600+ authoritative sources.

---

## Interview Q&A

**Q1: How does a research agent differ from one-shot RAG?**
A1: In my understanding, the core difference is path-dependence. One-shot RAG retrieves a fixed chunk set and generates once -- it cannot decide mid-answer that more evidence is needed. A research agent runs an LLM in a tool loop, where intermediate findings change the next search query. The model decides the next step, not hardcoded control flow. Remove the tools and you have chat; remove the loop and you have a function call; remove the model and you have a workflow.

**Q2: Walk me through the three termination guards.**
A2: There are three guards, and whichever fires first wins. First, the completion signal -- the model emits a final answer with no tool call, which is the normal exit. Second, the iteration cap -- typically 10 turns for a scoped build, 15-25 for general research. Third, the budget cap -- max tokens or dollars, which returns a partial result with a failure flag. Without these guards, a stuck task can burn ~$50 in tokens. The $47K LangChain incident showed what happens when you rely on alerts instead of mechanical enforcement.

**Q3: Why does Fact Check accuracy drop ~42% when tool calls scale from 2 to 150?**
A3: The finding from Onweller et al. is counterintuitive. Link Works and Relevant Content stay stable as you add more retrieval, but Fact Check -- whether the claim is actually supported by the source -- degrades sharply. The mechanism is information overload: as the model ingests more pages, it starts synthesizing across documents in ways that introduce unsupported claims. The searcher snippets are far more reliable (~3.8% mistakes) than researcher notes consolidating many docs (~70.8% mistakes). The mitigation is to cap tool calls, prefer single-document extracts fed to the orchestrator, and use a dedicated CitationAgent as a separate verification pass.

**Q4: Explain the three production topologies and when you would choose each.**
A4: Single-Agent ReAct is simplest -- one LLM cycles through reason-act-observe. Best for focused research on tight budgets but has no failure isolation. Orchestrator-Worker uses a strong lead model delegating to parallel cheap workers. Anthropic's system shows 90.2% quality improvement over single-agent. Best for complex multi-faceted research where parallelism matters. Pipeline architecture uses sequential stages (planner-executor-publisher). It has the highest latency due to sequential handoffs, but each stage is independently auditable, making it ideal for regulated environments where compliance maps to stages.

**Q5: How does the three-tier context memory work?**
A5: It is a production consensus for managing long research sessions that can accumulate 500K-1M tokens. Hot tier keeps the last 10 turns verbatim with full detail. Warm tier compresses turns 11-40 into a rolling summary of key decisions. Cold tier reduces everything prior to just goals and constraints. Measured impact: 84% token reduction in 100-turn tests with under 1.5% accuracy loss at safe 2-3x compression. The critical detail is preserving citations at all tiers because references are the hardest state to reconstruct.

**Q6: How would you design budget enforcement for a research agent?**
A6: The $47K LangChain incident is the canonical cautionary tale -- two agents stuck in an infinite A2A loop for 264 hours because alerts fired but no one acted. Budget enforcement must be mechanical: hard caps on tokens, tool calls, and dollar cost per run, enforced by the code, not by dashboards. I would implement three dimensions: a token ceiling (e.g., 2M tokens), a tool call limit (e.g., 50), and a dollar cap (e.g., $25/run). Any dimension exhausted returns a partial result with a flag. The circuit breaker pattern adds another layer: after N consecutive failures, the multi-agent path opens and traffic routes to single-agent, then to cached briefs.

**Q7: What is the CitationAgent and why is it separate from synthesis?**
A7: The CitationAgent is a dedicated post-processing pass that walks the report and source documents, verifying that every claim traces to a verified source. It is separate from synthesis because inline citation during generation only achieves ~78% accuracy, while the dedicated verification pass pushes to 94%. The separation also enables the citation log to be an immutable append-only store with its own audit trail, which is critical for regulated industries.

**Q8: How do you prevent SSRF attacks in the fetch layer?**
A8: The fetch proxy must enforce: HTTPS-only, private-IP and cloud metadata blocklists, DNS pin + revalidate on every redirect, body size and timeout caps, and rate limits. The pattern is resolve -> validate public IPs -> pin connection -> revalidate redirects -> disable environment proxies. I would also enforce URL normalization and idempotency keys so that duplicate fetch attempts short-circuit, reducing the attack surface.

**Q9: Derive the cost per 1K runs for single vs multi-agent research.**
A9: Starting with a chat baseline of 4K input + 500 output tokens at $3/$15 per 1M: C_chat = $0.0195. Single agent applies the ~4x multiplier: $0.078 per run, or $78 per 1K runs. Multi-agent applies ~15x: $0.2925 per run, or $292.50 per 1K runs. The delta is $214.50 per 1K runs. With model routing (70/20/10 across tiers) and prompt caching, you can bring multi-agent down to ~$5,840 per 1K runs from a $40K unoptimized baseline -- an 85% reduction. The economics only work when task value justifies the 15x multiplier.

**Q10: What is the PIES hallucination taxonomy?**
A10: PIES from DeepHalluBench models hallucinations along two dimensions: explicit versus implicit, crossed with planning versus summarization. Explicit planning hallucinations are deviated or redundant plans. Implicit planning hallucinations are neglecting specific user restrictions. Explicit summarization hallucinations are fabricating content or misquoting citations. Implicit summarization hallucinations are neglecting essential retrieved information. The critical finding is that no single agent achieves robust performance across the full trajectory. Intermediate hallucinations in planning cascade through all downstream search, selection, and synthesis.

**Q11: When would you buy hosted Deep Research vs build your own harness?**
A11: I would buy hosted when the use case is generic web research with no audit ownership requirement and public data only. I would build my own harness when I need CRM/warehouse/PubMed-first ranking, custom stop rules, owned trajectory logs for auditors, or citation gates. Multi-agent architecture makes sense for breadth-first, high-value, parallelizable research where information exceeds one context. Single-agent or workflow makes sense when steps are known, highly dependent, or when value cannot absorb the 15x cost tax.

**Q12: How do rainbow deployments differ from blue-green for research agents?**
A12: Standard blue-green deployments cut over cleanly -- the old version stops receiving traffic and eventually shuts down. This works for stateless services but would kill in-flight research sessions that run 15-60 minutes. Rainbow deployments run old and new versions simultaneously while traffic gradually shifts. In-flight jobs continue executing on the version that started them. This is a deployment concern unique to long-running agent workloads and is explicitly used by Anthropic's Research system.

---

## Key Numbers to Memorize

| Number | What It Means |
| --- | --- |
| **~4x** | Agent vs chat token multiplier |
| **~15x** | Multi-agent vs chat token multiplier |
| **90.2%** | Multi-agent quality improvement over single-agent (Anthropic) |
| **84%** | Token reduction from three-tier context memory |
| **94%** | Best-in-class citation accuracy (Claude with search) |
| **42%** | Fact Check accuracy drop as tool calls scale 2 -> 150 |
| **66%** | Citation failures that are total fabrication |
| **20** | Max tools per agent before quality degrades |
| **$47K** | Real-world runaway cost incident (264 hours, infinite loop) |
| **358ms** | Perplexity median search latency (200M daily queries) |
| **2-4 min** | Newsletter design target for focused research |
| **15-25** | Typical iteration cap for research agents |
| **200K** | Token threshold for Memory plan persistence |
| **$78 / $292.50** | Cost per 1K runs: single / multi-agent (labeled assumptions) |
| **54%** | Orgs with AI agent security incidents in past 12 months |

---

## Quick Reference

```
Agent = LLM + tools + loop
  remove tools  -> chat
  remove loop   -> function call
  remove model  -> workflow

Loop: Think -> Act (MCP) -> Observe -> Reflect -> (loop or stop)

Three stops: completion | iteration cap (15-25) | budget cap (tokens/$$)

Topologies: Single-Agent ReAct | Orchestrator-Worker | Pipeline
  - Single: lowest cost, no failure isolation
  - Multi: 90.2% quality gain, ~15x tokens, needs parallel workers
  - Pipeline: highest latency, best for regulated (stage = audit point)

Fallback chain: Multi-agent -> Single agent -> Cached brief

Cost: ~4x chat (single) | ~15x chat (multi) | routing saves 60-80%

Citations: 94% best, 66% of failures = total fabrication
  Fact Check drops ~42% at 2->150 tool calls
  CitationAgent = separate verification pass

Context: 3-tier (hot/warm/cold) = 84% token reduction
  Hot: last 10 turns verbatim
  Warm: turns 11-40 summary
  Cold: goals + constraints only

Security: Zero-Trust MCP, per-call RBAC, SSRF hardening,
  PII detect->redact->audit, immutable WORM audit logs

$47K lesson: budget enforcement must be mechanical, not alerts
```
