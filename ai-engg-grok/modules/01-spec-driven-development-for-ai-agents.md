# Module 01 — Spec Driven Development for AI Agents

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 01 (foundational)  
**Grounded in**: `research/01-spec-driven-development-for-ai-agents.md` (28 sources, 2026-09-29)

Spec-Driven Development (SDD) inverts code-as-truth: structured specifications become the primary, executable artifact; code is a generated expression of those specs ([GitHub Spec Kit — `spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md); [System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)). Thoughtworks frames SDD as workflows that start from a structured functional specification and decompose into solutions and tasks ([Thoughtworks Technology Radar — Spec-driven development](https://www.thoughtworks.com/radar/techniques/spec-driven-development)). Three complementary roles recur across stacks: **spec-first** (write before implement), **spec-anchored** (keep the spec bound after ship), and **spec-as-source** (human-edited spec regenerates plans, tasks, and tests) ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    CONTROL PLANE                         │
                         │  constitution/steering · requirements · design · tasks   │
                         │  human approval gates · delta/archive · AAP review       │
                         │                                                          │
                         │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
                         │  │ Spec Kit   │  │ Kiro Spec  │  │ OpenSpec / Blitzy  │  │
                         │  │ slash cmds │  │ mode+hooks │  │ propose·AAP        │  │
                         │  └─────┬──────┘  └─────┬──────┘  └─────────┬──────────┘  │
                         └────────┼───────────────┼───────────────────┼─────────────┘
                                  │               │                   │
                                  ▼               ▼                   ▼
                         ┌──────────────────────────────────────────────────────────┐
                         │                     DATA PLANE                           │
                         │  coding-agent tool loop: edit · test · sandbox · compile │
                         │  parallel task waves [P] · specialized agent roles       │
                         │  (arch / impl / QA / debug / validate / integrate)       │
                         └───┬──────────────────────┼───────────────────────┬───────┘
                             │                      │                       │
              ┌──────────────┴───────┐  ┌───────────┴─────────┐  ┌──────────┴───────┐
              │     TOOL PROXIES     │  │    PERSISTENCE      │  │    TELEMETRY     │
              ├──────────────────────┤  ├─────────────────────┤  ├──────────────────┤
              │  MCP servers         │  │  specs/[branch]/   │  │  usage dashboards│
              │  GitHub Issues MCP   │  │  openspec/specs/    │  │  credit/line $   │
              │  sandbox domain ACL  │  │  changes/archive/   │  │  Project Guide   │
              │  file/test runners   │  │  AAP blueprints     │  │  checklist state │
              │  Skills / AGENTS.md  │  │  git semantic br.   │  │  correlate IDs   │
              └──────────────────────┘  └─────────────────────┘  └──────────────────┘
```

**Plane responsibilities**

| Plane | What lives here | Truth source |
| --- | --- | --- |
| **Control plane** | Durable Markdown (or equivalent) artifact graph + human gates | Spec Kit constitution → specify → plan → tasks; Kiro EARS requirements → design → tasks; OpenSpec proposal → specs → design → tasks; Blitzy Technical Spec + AAP ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Kiro introducing](https://kiro.dev/blog/introducing-kiro/); [OpenSpec overview](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md); [AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)) |
| **Data plane** | Agent tool loop consuming artifacts as structured context (not chat archaeology) | File edits, tests, MCP tools, sandboxes ([Spec Kit](https://github.github.com/spec-kit/); [Kiro](https://kiro.dev/); [Blitzy](https://docs.blitzy.com/project-lifecycle/aap-review)) |
| **Persistence** | Versioned feature dirs, delta/archive folders, approved AAPs | `specs/[branch]/`, `openspec/changes/<id>/` → `archive/`, AAP documents ([`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md); [OpenSpec](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md)) |
| **Tool proxies** | MCP, sandboxes, Skills, issue bridges | Kiro MCP + AGENTS.md + Skills; Spec Kit `/taskstoissues` via GitHub MCP; domain-allowlisted sandboxes ([Kiro](https://kiro.dev/); [Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Kiro Sandbox](https://kiro.dev/docs/web/sandbox/)) |
| **Telemetry** | Credit/line meters, PR Project Guides, checklist gates, org dashboards | Kiro usage/cost controls; Blitzy Project Guide per PR; Spec Kit checkbox/converge state ([Kiro](https://kiro.dev/); [Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)) |

### End-to-end request-flow narrative

1. **Intent ingress** — A developer (or product owner) opens a change. Spec Kit runs `/speckit.constitution` (if missing) then `/speckit.specify`; Kiro Spec mode blocks coding until requirements/design/tasks exist; OpenSpec starts at Propose (optional Explore); Blitzy begins from codebase onboarding + Technical Spec ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Introducing Kiro](https://kiro.dev/blog/introducing-kiro/); [OpenSpec](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md)).
2. **Clarify / quality gate (control plane)** — Ambiguities become `[NEEDS CLARIFICATION]` markers, not silent guesses (`/speckit.clarify` ≤5 questions/run; Kiro EARS + automated contradiction checks; Blitzy rejects incomplete AAPs with “TBD”) ([`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md); [AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)).
3. **Plan & task graph** — Control plane emits design + dependency-ordered tasks (Setup → Foundational → per–user-story → Polish; `[P]` / parallel waves). Optional `/speckit.analyze` is read-only consistency across `spec.md` / `plan.md` / `tasks.md` ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).
4. **Human checkpoint** — Checklist unchecked items block implement; Blitzy requires AAP approval before generation; Kiro reviews specs before coding ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)).
5. **Data-plane execution** — Implement agents load only the high-signal Markdown slice for the current task (Anthropic: finite attention budget; JIT identifiers over corpus stuffing) ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Kiro from-chat-to-specs](https://kiro.dev/blog/from-chat-to-specs-deep-dive/)). Tool proxies open sandboxes (clone → execute → tear down), call MCP tools, run tests. Independent AAP/task nodes fan out in parallel ([Kiro Sandbox](https://kiro.dev/docs/web/sandbox/); [Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).
6. **Brownfield context retrieval (optional branch)** — Blitzy-style Dynamic Knowledge Graph + GraphRAG forward/reverse traversal bounds blast radius before planning ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); Microsoft GraphRAG). Kiro regenerates `structure.md` / `tech.md` / `product.md` from the repo ([Kiro from-chat-to-specs](https://kiro.dev/blog/from-chat-to-specs-deep-dive/)).
7. **Adherence & converge** — Spec-adherence agents (Blitzy) or `/speckit.converge` (append-only gap assessment → Convergence tasks → re-implement) close the loop until Converged ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).
8. **Persist & audit** — OpenSpec archives deltas into main specs under a dated `changes/archive/`; Blitzy emits Project Guide on the PR; Spec Kit leaves `tasks.md` + optional GitHub issues as the trail ([OpenSpec](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md); [Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).
9. **Telemetry sink** — Credits/lines consumed, sandbox lifecycle events, gate outcomes, and PR mappings land in dashboards / meters for cost and governance ([Kiro billing](https://kiro.dev/docs/billing/); [Blitzy Security](https://blitzy.com/security)).

Message style across stacks: **synchronous, human-gated, artifact-passing** pipelines. Parallelism is at the **task-graph** layer, not a peer A2A mesh ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

---

## Part 2 — Core Mechanics & Algorithms

### Theoretical fundamentals

SDD treats the specification as a **desired-state document** and the codebase as **observed state**. Implementation agents are effectors; analyze/converge/adherence loops are reconciler controllers. This is why Spec Kit’s `/speckit.converge` is append-only (never deletes code) and why OpenSpec archives merge deltas into canonical specs rather than leaving chat as truth ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [OpenSpec](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md)).

Formal artifacts (types, OpenAPI, property tests) as instructions reduce hallucinated structure versus prose-only prompts ([agentpatterns.ai — Specification as Prompt](https://agentpatterns.ai/instructions/specification-as-prompt/)). Kiro’s EARS acceptance criteria + property-based tests assert invariants across randomized inputs beyond example unit tests ([Introducing Kiro](https://kiro.dev/blog/introducing-kiro/); [Kiro product](https://kiro.dev/)).

### Spec Kit pipeline state machine

Only `/speckit.specify` is strictly required before `/speckit.plan`; clarify, checklist, and analyze are optional quality gates ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).

```
                    ┌──────────────┐
                    │ constitution │──────────────┐
                    └──────┬───────┘              │ (immutable principles
                           │                      │  evaluated every phase)
                           ▼                      │
                    ┌──────────────┐              │
           ┌───────►│   specify    │◄─────────────┤
           │        └──────┬───────┘              │
           │               │                      │
           │        ┌──────▼───────┐              │
           │        │   clarify*   │──answers──►spec.md
           │        └──────┬───────┘              │
           │               ▼                      │
           │        ┌──────────────┐              │
           │        │    plan      │──────────────┤
           │        └──────┬───────┘              │
           │               ▼                      │
           │        ┌──────────────┐              │
           │        │  checklist*  │──gate────────┤
           │        └──────┬───────┘              │
           │               ▼                      │
           │        ┌──────────────┐              │
           │        │    tasks     │──[P] DAG─────┤
           │        └──────┬───────┘              │
           │               ▼                      │
           │        ┌──────────────┐              │
           │        │  analyze*    │──read-only───┤
           │        └──────┬───────┘              │
           │               ▼                      │
           │        ┌──────────────┐              │
           │        │  implement   │──dep order───┤
           │        └──────┬───────┘              │
           │               ▼                      │
           │        ┌──────────────┐              │
           └────────│   converge   │──append gaps─┘
                    └──────────────┘
         * optional    Converged | Convergence tasks → re-implement
```

**OpenSpec cycle**: Explore? → Propose → Apply → Archive (delta `ADDED`/`MODIFIED`/`REMOVED`; “enablers, not gates”—wrong design mid-flight → edit `design.md` and continue) ([OpenSpec](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md)).

**Kiro cycle**: Requirements (EARS) → Design → Tasks → parallel implement; hooks on save/create/delete ([Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)).

**Blitzy cycle**: Onboard → Technical Spec → Generation prompt → AAP review → multi-agent execute → validate → Project Guide / PR ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)).

### Task-graph scheduling algorithm

1. Parse `tasks.md` / AAP into nodes with `depends_on` edges and optional `[P]` / independent flags.
2. Topologically sort (Kahn or DFS). **Complexity**: \(O(V+E)\) for sort; detecting cycles \(O(V+E)\).
3. Emit **waves**: all ready nodes with satisfied deps and mutual file/interface independence → parallel wave; else serialize.
4. Gate: Spec Kit implement must not flip checklist markers; unchecked checklist items prompt before proceed ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).

**Invariant (parallel safety)**: two `[P]` tasks may run concurrently only if their write sets are disjoint (different files/interfaces). Overlap without locking → merge conflicts / duplicated assumptions ([inferred] from Kiro “waves” framing + Spec Kit `[P]` semantics; research notes no published file-level lock protocol).

### Context retrieval algorithm (brownfield)

GraphRAG-style traversal on a Dynamic Knowledge Graph (control flow, call graphs, inheritance, module deps) ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)):

1. Seed nodes from change-spec path/symbols.
2. **Forward** edges → downstream blast radius; **reverse** → dependents.
3. Bound hop depth / node budget to protect the attention window (Anthropic: smallest high-signal token set) ([Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

**Complexity**: BFS/DFS to depth \(d\) is \(O(b^d)\) worst-case fan-out; production systems must hard-cap nodes/tokens per planning call.

### Key invariants & convergence

| Invariant | Enforcement |
| --- | --- |
| Spec precedes HOW/stack in specify phase | Templates forbid premature tech in `spec.md` ([`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md)) |
| No silent assumptions | `[NEEDS CLARIFICATION]` mandatory ([`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md)) |
| Analyze is non-mutating | `/speckit.analyze` never edits files ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)) |
| Converge is append-only | Gaps → Convergence tasks; never delete code ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)) |
| AAP completeness | “TBD” / incomplete plans rejected at review ([AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)) |
| Constitution/rules bind all phases | Spec Kit constitution; Blitzy Rules vs Generation prompts; Kiro steering ([EPAM](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering); [Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)) |
| Atomic task size | Prefer 1–2 files / task; quality collapses at 10+ files or multi-hour unbounded autonomy ([EPAM Spec Kit](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering)) |

**Convergence property**: Desired = Observed when converge reports Converged **and** adherence/Project Guide map every requirement to artifacts. Open question (Neo Kim): when code and spec disagree, who is authority ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents))—enterprise answer must be policy: **spec-as-source** until a deliberate constitution amendment.

**Canonical failure motivator**: tests can pass while intent fails (notification migration: disabled-email user still mailed)—verification ≠ validation ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

---

## Part 3 — Token Economics & NFR Analysis

> ⚠️ **Gap**: Published p50/p95/p99 latency SLAs, RPM/TPM ceilings, and semantic-cache hit rates specific to SDD orchestration loops are **not disclosed** by Spec Kit, OpenSpec, or Blitzy. Available figures are credit/line pricing, model multipliers, and qualitative context-cost guidance (research note §2). Latency targets below are **[inferred] engineering targets** for an enterprise SDD control plane you operate yourself—not vendor SLOs. Cost formulas use **documented pricing only**.

### Cost formulas — `$ / 1k runs` with stated assumptions

Define one **feature run** = specify → plan → tasks → implement (one user story wave) → optional converge pass.

#### A. Kiro credit path (documented)

| Tier | Credits included | List price |
| --- | --- | --- |
| Free | 50 | $0 |
| Pro | 1,000 | $20/user/mo |
| Pro+ | 2,000 | $40 |
| Pro Max | 5,000 | $100 |
| Power | 10,000 | $200 |

Add-on credits (paid tiers): **$0.04/credit** ([Kiro pricing](https://kiro.dev/pricing/); [Add-on credits](https://kiro.dev/docs/billing/add-on-credits/)). Model multipliers vs Auto 1.0× (selected): `deepseek-3.2` / `minimax-m2.5` 0.25×; `claude-haiku-4.5` 0.4×; `claude-sonnet-*` 1.3×; `claude-opus-*` ~2.0–2.2×; `gpt-5.6-sol` 4.4×; `claude-fable-5.1` 6.0× ([Kiro product](https://kiro.dev/)).

**[inferred] community credit burn**: ~15–25 credits per full feature “spec run” (not an official Kiro figure; research note).

\[
\text{Cost}_{1\text{k runs}}^{\text{Kiro add-on}} = 1000 \times C_{\text{run}} \times M_{\text{model}} \times \$0.04
\]

| Assumption | Value |
| --- | --- |
| \(C_{\text{run}}\) | 20 credits (mid of 15–25) |
| \(M_{\text{model}}\) | 1.3 (Sonnet) vs 0.4 (Haiku) vs 2.1 (Opus) |
| Caching | No public SDD prompt-cache hit rate; treat \(M\) as post-routing effective multiplier |

| Scenario | Formula | **$ / 1k runs** |
| --- | --- | --- |
| Haiku-class (0.4×) | \(1000 \times 20 \times 0.4 \times 0.04\) | **$320** |
| Auto/Sonnet (1.3×) | \(1000 \times 20 \times 1.3 \times 0.04\) | **$1,040** |
| Opus-class (2.1×) | \(1000 \times 20 \times 2.1 \times 0.04\) | **$1,680** |

Included-plan amortization (Pro 1,000 credits @ $20): if each run burns 20 credits → 50 runs/mo covered ≈ **$0.40/run** plan-only ≈ **$400 / 1k runs** until exhaustion, then add-on rates apply ([Kiro pricing](https://kiro.dev/pricing/)).

#### B. Spec Kit / OpenSpec + agent tokens (documented process free; agent billed)

Assumptions for a **coding-agent attach** (illustrative token split grounded in Anthropic-style context advice—minimize window; stage Setup+Foundational then stories ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html))):

| Phase | Input tokens | Output tokens | Notes |
| --- | --- | --- | --- |
| Specify+clarify | 8,000 | 3,000 | Spec Markdown only |
| Plan+tasks | 12,000 | 4,000 | Includes constitution slice |
| Implement wave | 40,000 | 12,000 | JIT file fetches; not full chat archaeology |
| Converge | 15,000 | 4,000 | Append-only assessment |
| **Total / run** | **75,000** | **23,000** | |

Using a published frontier reference rate **as an assumption you must substitute with your contract** (placeholder label, not a vendor SDD price):  
**[assumed]** $3.00 / 1M input, $15.00 / 1M output (Sonnet-class list; replace with actual).

\[
\text{Cost}_{1\text{run}} = \frac{75\text{k}}{10^6}(3) + \frac{23\text{k}}{10^6}(15) = \$0.225 + \$0.345 = \$0.57
\]

\[
\text{Cost}_{1\text{k runs}}^{\text{agent}} \approx \$570
\]

**Prompt-cache impact [inferred formula]**: if constitution + steering (~10k tokens) are cacheable at 10% input price on 60% of calls:

\[
\text{Cost}_{1\text{k}}^{\text{cached}} \approx 1000 \times \bigl(0.4\cdot\text{in}_{\text{full}} + 0.6\cdot(0.1\cdot\text{in}_{\text{cached}} + \text{in}_{\text{uncached}})\bigr)\cdot P_{\text{in}} + \text{out}\cdot P_{\text{out}}
\]

With the table above and 10k cached / 65k uncached on implement-heavy mix, expect **~15–25% input savings**—still no published SDD hit-rate benchmark (⚠️ Gap).

#### C. Blitzy line path (documented)

- Onboard: **$0.10/line** (after included allotment); Generate: **$0.20/line** ([Blitzy Security FAQ](https://blitzy.com/security)).
- Commercial ~$500K/yr (first 20M LOC onboard); Enterprise ~$5M/yr; POC $50K / 2 mo; Pilot $250K / 6 mo ([Blitzy Security](https://blitzy.com/security)).

**[inferred] per 1k “runs”** only makes sense if you define run = one generation batch of \(L\) lines:

\[
\text{Cost}_{1\text{k batches}} = 1000 \times (0.10\cdot L_{\text{onboard}} + 0.20\cdot L_{\text{gen}})
\]

Example: \(L_{\text{gen}}=5{,}000\), onboard amortized 0 for that batch → \(1000 \times 0.20 \times 5000 = \$1{,}000{,}000\) / 1k batches—illustrates why Blitzy is engagement-priced, not IDE-credit-priced.

### Latency SLA targets (engineering; not vendor-published)

| Tier | Target | What it covers | Mitigations |
| --- | --- | --- | --- |
| **p50** | ≤ 45s control-plane turn (specify/plan slash) | Single-agent Markdown rewrite | Slim templates; Skills over constitution bloat ([Thoughtworks Spec Kit](https://www.thoughtworks.com/radar/languages-and-frameworks/github-spec-kit)); Haiku/0.4× routing ([Kiro](https://kiro.dev/)) |
| **p95** | ≤ 8 min implement task (1–2 files) | Tool loop + tests | Stage phases; atomic tasks ([EPAM](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering)); JIT retrieval ([Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)) |
| **p99** | ≤ 25 min user-story wave / sandbox job | Parallel wave + CI | Dependency waves; sandbox tear-down per task ([Kiro Sandbox](https://kiro.dev/docs/web/sandbox/)); fail closed on credit exhaustion rather than unbounded retry |

Streaming partial Markdown / PR diffs improves *perceived* p50; does not change wall-clock tool execution.

### Throughput & back-pressure

| Mechanism | Behavior |
| --- | --- |
| **Kiro** | No daily/weekly rate limits; back-pressure = credit exhaustion → pause until add-on ($0.04) or monthly reset; Free has no add-ons ([Kiro pricing](https://kiro.dev/pricing/); [Add-on credits](https://kiro.dev/docs/billing/add-on-credits/)) |
| **Blitzy** | Jobs **not cancelable** once submitted; consume assigned quota on submit ([Blitzy Security](https://blitzy.com/security)) |
| **Spec Kit / OpenSpec** | No platform TPM/RPM—limits = underlying agent |

**Capacity planning**: size on **artifact review bandwidth** + **credit/line budgets**, not only model RPM ([EPAM](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering); research capacity notes). Concurrent agents: Blitzy BCC case (~3,600+ agents / 127 files) is an upper signal for platform concurrency; Spec Kit/Kiro bounded by task independence + human review ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

**Back-pressure design for a self-hosted SDD orchestrator**: token-bucket per tenant on implement waves; queue excess tasks; shed optional analyze/clarify under load; never auto-purchase credits without policy.

### Availability, RPO/RTO, compliance, NFR trade-offs

| NFR | Target / posture | Trade-off |
| --- | --- | --- |
| **Availability** | Control plane = git + Markdown (high); data plane = agent/sandbox (dependent on vendor) | Prefer Spec Kit offline/firewall for air-gapped orgs ([Spec Kit home](https://github.github.com/spec-kit/)); Blitzy inbound-only VPC / on-prem for no public endpoints ([Blitzy Security](https://blitzy.com/security)) |
| **RPO** | Spec artifacts in git → RPO ≈ last push (seconds–minutes with frequent commit) | Chat-only workflows have worse RPO (lost context) |
| **RTO** | Restore branch + `specs/` / `openspec/` → minutes; rehydrate sandboxes → minutes–tens of minutes | Blitzy in-flight jobs not cancelable—design RTO around job boundaries |
| **Compliance** | Blitzy: SOC 2 Type II + ISO 27001, no training on customer code, embeddings-only stance ([Blitzy Security](https://blitzy.com/security)); Kiro: IAM/SSO, IP indemnity, least-privilege secrets warning ([Kiro](https://kiro.dev/); [Kiro secrets](https://kiro.dev/docs/web/sandbox/environment-variables/)) | Heavier governance ↑ ops complexity and $ |
| **Quality vs cost** | EPAM: expect **60–80%** generated code usable after review ([EPAM](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering)) | Cheaper/faster models raise review load |
| **Context vs autonomy** | Externalize specs to free coding context ([Kiro from-chat-to-specs](https://kiro.dev/blog/from-chat-to-specs-deep-dive/)); Thoughtworks: instruction bloat → context rot ([Thoughtworks Spec Kit](https://www.thoughtworks.com/radar/languages-and-frameworks/github-spec-kit)) | Large constitutions ↑ token $ and ↓ quality |

---

## Part 4 — Distributed Resilience & Security

> ⚠️ **Gap**: Spec Kit and OpenSpec are local artifact workflows without published Temporal/Kafka runtimes, distributed locks, or circuit-breaker configs. Patterns below bind **documented SDD control-plane durability** to **enterprise agent orchestration** you must add when productizing SDD ([research §3](../research/01-spec-driven-development-for-ai-agents.md)).

### Durable execution

| Pattern | SDD mapping |
| --- | --- |
| **Workflow as artifacts** | Feature state under `specs/[branch]/`; OpenSpec `changes/<id>/` → dated `archive/`; Blitzy AAP = approved execution blueprint ([`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md); [OpenSpec](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md); [AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)) |
| **Checkpoints** | Human gates: plan/analyze/checklist; AAP approval; Kiro spec review before code ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)) |
| **Replay** | Re-run `/speckit.implement` from remaining unchecked tasks; converge appends new tasks without wiping history ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)) |
| **Ephemeral workers** | Kiro Web: sandbox per task → clone → execute → tear down; domain allowlists ([Kiro Sandbox](https://kiro.dev/docs/web/sandbox/)) |
| **Orchestrator upgrade path** | Wrap task DAG in Temporal/Kafka: each task = activity with idempotency key = `(feature_id, task_id, attempt)`; DLQ for poison pills; workflow history mirrors Markdown gates |

**[inferred] converge ≈ desired-state controller** implemented via Markdown append rather than an orchestrator DB ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).

### Failure taxonomy

| Class | Examples in SDD | Handling |
| --- | --- | --- |
| **Transient** | Model 429/5xx, sandbox boot flake, MCP timeout | Exponential backoff + jitter; circuit breaker; retry same idempotency key |
| **Permanent** | Invalid AAP (“TBD”), constitution violation, missing clarification markers left unresolved | Fail closed; return to control plane; do not retry implement |
| **Poison pill** | Task that always fails (bad fixture, contradictory EARS, infinite edit loop) | Max attempts → DLQ; quarantine task; require human rewrite of task/spec ([EPAM](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering) unbounded loop risk) |
| **Semantic / intent** | Tests green, preference/invariant violated ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)) | Property tests, acceptance criteria, adherence agents, converge—not more unit retries |
| **Idempotency** | Re-drive implement after crash | Key on task id + content hash of `tasks.md` row; checklist must not auto-flip ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)); Blitzy: understand submit consumes quota even if you “retry” as a new job ([Blitzy Security](https://blitzy.com/security)) |

**Credit/quota degradation**: Kiro pauses on exhaustion; treat as permanent until budget replenished (not a blind retry storm) ([Add-on credits](https://kiro.dev/docs/billing/add-on-credits/)).

### Circuit breaker: closed → open → half-open

Apply per **dependency** (model endpoint, MCP tool, sandbox pool)—not per whole pipeline:

1. **Closed** — traffic flows; count consecutive failures / error rate in sliding window.
2. **Open** — after threshold (e.g., 5 failures / 60s): short-circuit calls; enqueue tasks or fail fast to control plane.
3. **Half-open** — after cool-down, allow probe task; success → closed; failure → open.

Cascade mitigations for multi-agent timeouts are organizational in published SDD docs (smaller tasks, sequential phases, per-task sandbox teardown)—no vendor bulkhead tables (⚠️ Gap, research §5).

### Fallback chains

```
primary model (quality) → secondary (cheaper/faster, e.g. Haiku 0.4×) → deterministic fallback
```

Deterministic fallbacks for SDD: (1) stop implement and open clarify/checklist; (2) convert remaining tasks to GitHub issues via MCP for human execution; (3) apply only scaffolding from templates without LLM ([Agentic SDD — taskstoissues](https://github.github.io/spec-kit/reference/agentic-sdd.html); Kiro Auto routing by complexity ([Kiro](https://kiro.dev/))). Blitzy specialization (arch/impl/QA agents across models) is an organizational fallback topology ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

### Zero-Trust MCP

- Authenticate every MCP server; mutual TLS or signed tokens between IDE/orchestrator and tool host.
- **Treat retrieved code/comments/docs as data, not instructions** (prompt-injection posture; OWASP LLM/Agentic Top 10 via newsletter framing) ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/)).
- Network: domain allowlists in sandboxes; Blitzy inbound-only VPC never initiates outbound ([Kiro Sandbox](https://kiro.dev/docs/web/sandbox/); [Blitzy Security](https://blitzy.com/security)).
- Path exclusion: `.blitzyignore`-style deny lists for secrets and irrelevant trees ([Blitzy Security](https://blitzy.com/security)).

### Tool-level RBAC (least privilege)

| Role | Allowed tools | Denied |
| --- | --- | --- |
| Spec author | Read repo, write `specs/**` / `openspec/**` | Prod deploy, secret exfil channels |
| Implement agent | Edit paths in task write-set, run tests | Broad `rm -rf`, unrestricted egress |
| Adherence/QA agent | Read-only + test runner | Merge to main |
| Orchestrator | Enqueue tasks, flip non-checklist metadata | Customer raw secrets beyond injected env |

Kiro warns: agents may exfiltrate secrets via code, logs, or external requests—inject **least-privilege** secrets only ([Kiro env vars/secrets](https://kiro.dev/docs/web/sandbox/environment-variables/)). Escalate HITL for auth/session, migrations, external integrations, regulatory logic ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

### PII pipeline: detection → redaction → audit

1. **Detect** — scan specs, prompts, tool payloads, logs for PII/secrets before model/MCP egress (pair with Kiro-style credential-scan hooks on commit ([Introducing Kiro](https://kiro.dev/blog/introducing-kiro/))).
2. **Redact** — replace with stable tokens (`[PII:email:3f2a]`) in model context; keep mapping in a sealed vault.
3. **Audit** — append-only event: `{correlation_id, actor, artifact_hash, tool, decision, pii_tokens_redacted}` to immutable store (WORM object lock / hash-chained log). Formal open-standard SDD audit schemas are **not published**—you must define them ([research §4](../research/01-spec-driven-development-for-ai-agents.md)).

### Immutable audit & chain-of-custody

- OpenSpec dated `changes/archive/` = completed-change audit trail ([OpenSpec](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md)).
- Blitzy Project Guide on each PR maps work → requirements ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).
- Spec Kit `tasks.md` + optional issues = execution trail ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).
- Enterprise extension: sign artifact hashes at each gate (specify → plan → AAP approve → implement wave → converge) so agent decisions have chain-of-custody for regulated reviews.

Human oversight models: **HITL** (mandatory AAP / checklist) vs **HOTL** (QA agents + hooks while humans merge) ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)).

---

## Part 5 — Production Enterprise Code

Runnable Python demonstrating retries with exponential backoff + jitter, circuit breaker (closed → open → half-open), fallback chain, structured logging with correlation IDs, and graceful degradation. No TODOs; no placeholder stubs.

```python
#!/usr/bin/env python3
"""SDD implement-wave executor with enterprise resilience primitives."""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol


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
            "feature_id": getattr(record, "feature_id", None),
            "task_id": getattr(record, "task_id", None),
            "attempt": getattr(record, "attempt", None),
            "breaker_state": getattr(record, "breaker_state", None),
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


LOG = build_logger("sdd.executor")


def log_extra(**kwargs: Any) -> dict[str, Any]:
    return kwargs


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    POISON = "poison"


class AgentError(Exception):
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
    failure_threshold: int = 5
    recovery_timeout_sec: float = 30.0
    window_sec: float = 60.0
    state: BreakerState = BreakerState.CLOSED
    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def _prune(self, now: float) -> None:
        self.failures = [t for t in self.failures if now - t <= self.window_sec]

    def allow(self) -> bool:
        now = time.monotonic()
        if self.state is BreakerState.OPEN:
            assert self.opened_at is not None
            if now - self.opened_at >= self.recovery_timeout_sec:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures.clear()
        self.state = BreakerState.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        now = time.monotonic()
        self._prune(now)
        self.failures.append(now)
        if self.state is BreakerState.HALF_OPEN:
            self.state = BreakerState.OPEN
            self.opened_at = now
            return
        if len(self.failures) >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = now


# ---------------------------------------------------------------------------
# Retries: exponential backoff + full jitter
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay_sec: float = 0.5
    max_delay_sec: float = 20.0
    poison_after: int = 5

    def delay(self, attempt: int) -> float:
        # AWS-style full jitter: sleep ~ U(0, min(cap, base * 2^attempt))
        ceiling = min(self.max_delay_sec, self.base_delay_sec * (2**attempt))
        return random.uniform(0.0, ceiling)


# ---------------------------------------------------------------------------
# Model / tool providers + fallback chain
# ---------------------------------------------------------------------------

class ImplementProvider(Protocol):
    name: str

    def run_task(self, task: "Task", correlation_id: str) -> "TaskResult":
        ...


@dataclass(frozen=True)
class Task:
    feature_id: str
    task_id: str
    write_paths: tuple[str, ...]
    prompt: str
    idempotency_key: str


@dataclass(frozen=True)
class TaskResult:
    ok: bool
    provider: str
    files_touched: tuple[str, ...]
    detail: str
    degraded: bool = False


class PrimaryCodingAgent:
    name = "primary"

    def __init__(self, fail_times: int = 0) -> None:
        self._remaining_failures = fail_times

    def run_task(self, task: Task, correlation_id: str) -> TaskResult:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise AgentError("model 503", FailureKind.TRANSIENT)
        return TaskResult(True, self.name, task.write_paths, "implemented")


class SecondaryCodingAgent:
    name = "secondary"

    def run_task(self, task: Task, correlation_id: str) -> TaskResult:
        return TaskResult(True, self.name, task.write_paths, "implemented-secondary")


class DeterministicFallback:
    """Graceful degradation: emit human-issue stub without LLM."""

    name = "deterministic"

    def run_task(self, task: Task, correlation_id: str) -> TaskResult:
        issue = {
            "title": f"[SDD fallback] {task.feature_id}/{task.task_id}",
            "body": task.prompt,
            "idempotency_key": task.idempotency_key,
            "correlation_id": correlation_id,
        }
        return TaskResult(
            ok=True,
            provider=self.name,
            files_touched=(),
            detail=json.dumps(issue),
            degraded=True,
        )


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

@dataclass
class ImplementWaveExecutor:
    providers: list[ImplementProvider]
    breaker: CircuitBreaker
    retry: RetryPolicy
    sleep_fn: Callable[[float], None] = time.sleep

    def execute(self, task: Task, correlation_id: str | None = None) -> TaskResult:
        cid = correlation_id or str(uuid.uuid4())
        last_error: Exception | None = None

        for provider in self.providers:
            if not self.breaker.allow():
                LOG.warning(
                    "circuit_open_skip_provider",
                    extra=log_extra(
                        correlation_id=cid,
                        feature_id=task.feature_id,
                        task_id=task.task_id,
                        breaker_state=self.breaker.state.value,
                    ),
                )
                continue

            for attempt in range(self.retry.max_attempts):
                LOG.info(
                    "attempt_start",
                    extra=log_extra(
                        correlation_id=cid,
                        feature_id=task.feature_id,
                        task_id=task.task_id,
                        attempt=attempt,
                        breaker_state=self.breaker.state.value,
                    ),
                )
                try:
                    result = provider.run_task(task, cid)
                    self.breaker.record_success()
                    LOG.info(
                        "attempt_success",
                        extra=log_extra(
                            correlation_id=cid,
                            feature_id=task.feature_id,
                            task_id=task.task_id,
                            attempt=attempt,
                            breaker_state=self.breaker.state.value,
                        ),
                    )
                    return result
                except AgentError as exc:
                    last_error = exc
                    if exc.kind is FailureKind.PERMANENT:
                        LOG.error(
                            "permanent_failure",
                            extra=log_extra(
                                correlation_id=cid,
                                feature_id=task.feature_id,
                                task_id=task.task_id,
                                attempt=attempt,
                            ),
                        )
                        break  # next provider / degrade
                    if exc.kind is FailureKind.POISON or attempt + 1 >= self.retry.poison_after:
                        LOG.error(
                            "poison_or_exhausted",
                            extra=log_extra(
                                correlation_id=cid,
                                feature_id=task.feature_id,
                                task_id=task.task_id,
                                attempt=attempt,
                            ),
                        )
                        self.breaker.record_failure()
                        break
                    self.breaker.record_failure()
                    delay = self.retry.delay(attempt)
                    LOG.warning(
                        "transient_retry",
                        extra=log_extra(
                            correlation_id=cid,
                            feature_id=task.feature_id,
                            task_id=task.task_id,
                            attempt=attempt,
                            breaker_state=self.breaker.state.value,
                        ),
                    )
                    self.sleep_fn(delay)
                except Exception as exc:  # noqa: BLE001 — isolate unknown faults as transient
                    last_error = exc
                    self.breaker.record_failure()
                    self.sleep_fn(self.retry.delay(attempt))

        # Graceful degradation: always end on deterministic provider if listed
        deterministic = next((p for p in self.providers if p.name == "deterministic"), None)
        if deterministic is not None:
            LOG.warning(
                "graceful_degradation",
                extra=log_extra(
                    correlation_id=cid,
                    feature_id=task.feature_id,
                    task_id=task.task_id,
                    breaker_state=self.breaker.state.value,
                ),
            )
            return deterministic.run_task(task, cid)

        raise AgentError(
            f"all providers failed: {last_error!r}",
            FailureKind.POISON,
        )


def demo() -> None:
    task = Task(
        feature_id="feat-0142",
        task_id="T3.2",
        write_paths=("src/notify/prefs.py",),
        prompt="Preserve email opt-out when migrating assignment notifications.",
        idempotency_key="feat-0142:T3.2:sha256:abc",
    )
    executor = ImplementWaveExecutor(
        providers=[
            PrimaryCodingAgent(fail_times=2),
            SecondaryCodingAgent(),
            DeterministicFallback(),
        ],
        breaker=CircuitBreaker(failure_threshold=3, recovery_timeout_sec=0.01),
        retry=RetryPolicy(max_attempts=4, base_delay_sec=0.01, max_delay_sec=0.05),
        sleep_fn=lambda _: None,
    )
    result = executor.execute(task, correlation_id="corr-demo-001")
    print(json.dumps({
        "ok": result.ok,
        "provider": result.provider,
        "degraded": result.degraded,
        "detail": result.detail,
        "files": list(result.files_touched),
    }))


if __name__ == "__main__":
    demo()
```

Run: `python3 01-sdd-executor.py` (or paste into a file). Expected path: primary fails twice (transient), then succeeds—or, with higher `fail_times` / open breaker, secondary or deterministic degraded issue JSON. Logs emit JSON lines carrying `correlation_id`, `feature_id`, `task_id`, `attempt`, `breaker_state`.

---

## Part 6 — Architectural System Design Scenarios

### Scenario A — Multi-repo brownfield change with blast-radius control

**Problem statement**  
A regulated payments org maintains 12 services (~8M LOC). Product needs a cross-cutting “notification preference” fix: agents previously shipped green tests that emailed users who opted out ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents) vignette). Constraints: SOC2 evidence, no vendor training on source, p95 implement task ≤ 8 min for 1–2 file tasks, HITL on auth and messaging paths, budget either IDE-credit scale or a six-figure platform—not both in year one.

**Proposed architecture**

```
┌─────────────┐   delta specs    ┌──────────────────┐   GraphRAG slice   ┌─────────────┐
│ OpenSpec /  │─────────────────►│ Knowledge graph  │───────────────────►│ Plan+tasks  │
│ Spec Kit    │   constitution   │ (call/deps)      │   hop-capped       │ human gate  │
└─────────────┘                  └──────────────────┘                    └──────┬──────┘
                                                                                │
                     ┌───────────────────────────────┬──────────────────────────┘
                     ▼                               ▼
              ┌──────────────┐               ┌──────────────┐
              │ Sandbox wave │               │ Adherence +  │
              │ MCP allowlist│               │ property tests│
              └──────┬───────┘               └──────┬───────┘
                     └──────────────┬───────────────┘
                                    ▼
                           ┌────────────────┐
                           │ PR + Project   │
                           │ Guide / archive│
                           └────────────────┘
```

Technology: OpenSpec deltas for brownfield ([OpenSpec](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md); [Thoughtworks OpenSpec](https://www.thoughtworks.com/radar/tools/openspec)) **or** Spec Kit constitution + analyze/converge ([Spec Kit](https://github.github.com/spec-kit/)); optional Blitzy-class graph for multi-repo blast radius ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)); Kiro-style sandboxes + hooks for credential scans ([Kiro Sandbox](https://kiro.dev/docs/web/sandbox/); [Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)); property-based tests on preference invariants ([Kiro](https://kiro.dev/)).

**Trade-off matrix**

| Dimension | Alt 1: Spec Kit + existing agents | Alt 2: OpenSpec deltas + agents | Alt 3: Blitzy platform |
| --- | --- | --- | --- |
| **Cost** | Agent tokens only (~**$570/1k** runs under Part 3 assumptions) | Same token shape; less rewrite churn | **$0.10/$0.20** per line + Commercial ~$500K/yr ([Blitzy Security](https://blitzy.com/security)) |
| **Latency** | Strong for atomic tasks; weak if specs bloat | Fast incremental edits | High coordination overhead; large jobs not cancelable |
| **Ops complexity** | Low–medium (Markdown + CI guards) | Lowest CLI footprint | High (VPC/on-prem, onboarding) |
| **Security** | Offline/firewall capable; DIY audit | Lightweight archive trail | SOC2/ISO, air-gap, inbound-only VPC |
| **Scalability** | Bounded by review + agent RPM | Excellent for many small deltas | Demonstrated ~3.6k agents / 229k-LOC class builds ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)) |

**Decision rationale**  
Year-one recommendation: **Alt 2 (OpenSpec) + Spec Kit constitution for org rules**, GraphRAG only on the services touched by notification prefs, HITL checklist on messaging, property tests for opt-out invariant. Choose Blitzy (Alt 3) only if multi-repo autonomy + compliance packaging outweighs lock-in and line economics. Pure Spec Kit (Alt 1) fits if the team already standardized on one slash-command pipeline and brownfield deltas are rare ([Thoughtworks](https://www.thoughtworks.com/radar/tools/openspec) praises deltas vs greenfield-heavy kits).

---

### Scenario B — Greenfield platform team: parallel agents, credit budgets, AWS-aligned IDE

**Problem statement**  
A 40-engineer platform group builds a new internal developer portal (greenfield). They want Spec mode (no code before requirements/design/tasks), parallel implement waves, property-based tests, and spend controls. Target: ≤ **$1,040 / 1k** Sonnet-equivalent feature runs on add-on math (Part 3), no surprise TPM locks mid-sprint, IAM/SSO, and graceful degradation to tickets when credits exhaust.

**Proposed architecture**

```
┌──────────────┐  EARS+design+tasks  ┌─────────────┐  waves [P]  ┌──────────────┐
│ Kiro Spec    │────────────────────►│ Task graph  │────────────►│ Parallel     │
│ mode + Auto  │  multipliers 0.25×… │ scheduler   │             │ sandboxes    │
└──────┬───────┘                     └─────────────┘             └──────┬───────┘
       │ hooks (secret scan)                                            │
       ▼                                                                ▼
┌──────────────┐                                              ┌────────────────┐
│ Steering     │                                              │ Circuit+credit │
│ structure/   │                                              │ breaker/meter  │
│ tech/product │                                              │ → GH issues    │
└──────────────┘                                              └────────────────┘
```

**Trade-off matrix**

| Dimension | Alt 1: Kiro Spec mode + credits | Alt 2: Spec Kit + Copilot/Claude/Codex | Alt 3: Chat-only coding agents |
| --- | --- | --- | --- |
| **Cost** | Pro $20–Power $200 + **$0.04**/credit; Sonnet-ish **~$1,040/1k** runs [inferred] | Agent tokens ~**$570/1k** under Part 3 assumptions; 38 integrations, no credit lock-in ([Spec Kit](https://github.github.com/spec-kit/)) | Hidden context-rot cost; rework dominates |
| **Latency** | Auto routes cheap models for simple tasks (0.25×–0.4×) | Depends on attached agent | p50 chat feels fast; p99 feature delivery worse |
| **Ops complexity** | Medium (IDE/CLI/Web + billing) | Low process layer | Lowest tooling / highest process chaos |
| **Security** | Sandboxes, IAM/SSO, secret warnings ([Kiro](https://kiro.dev/)) | Org catalogs, CI/Architecture Guard extensions ([Spec Kit](https://github.github.com/spec-kit/)) | Weak gates; prompt injection via pasted code |
| **Scalability** | No daily/weekly rate limits; credit back-pressure ([Kiro pricing](https://kiro.dev/pricing/)) | Scales with agent contracts | Collapses beyond 10–20 min tasks without decomposition ([EPAM](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering)) |

**Decision rationale**  
Recommend **Alt 1 (Kiro)** when the team wants integrated Spec mode, parallel agents, hooks, and explicit credit multipliers with Auto routing ([Kiro](https://kiro.dev/); [Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)). Prefer **Alt 2 (Spec Kit)** if agent diversity / anti-lock-in dominates (Thoughtworks Assess on Spec Kit, Apr 2026). Reject **Alt 3**: the notification-intent failure mode and context-window degradation are exactly what SDD externalization prevents ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). Wire Part 5’s breaker + deterministic GitHub-issue fallback to credit-meter events so exhaustion fails soft.

### Interview prompts

1. Separate **project rules** (constitution/steering) from **change specs** (delta / generation prompt) ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).
2. Design **verification vs validation**: tests ≠ intent; add acceptance criteria, property tests, converge/adherence ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [Kiro](https://kiro.dev/)).
3. Treat context as a scarce NFR: high-signal specs + JIT retrieval ([Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
4. Size autonomy to blast radius: HITL for auth/data/regulatory; HOTL for reversible tasks ([Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).
5. When code and spec disagree, what is the authority policy—and how do archive/converge encode it?
