# Research: Spec Driven Development for AI Agents

**Date researched**: 2026-09-29
**Sources consulted**: 28

## 1. System Topology & Mechanics

Spec-Driven Development (SDD) inverts the traditional code-as-truth model: specifications become the primary, executable artifact and code is treated as a generated expression of those specs ([GitHub Spec Kit — `spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md); [System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)). Thoughtworks defines SDD as workflows that begin with a structured functional specification, then decompose it into smaller pieces, solutions, and tasks; the form can be a single document or a set of structured artifacts ([Thoughtworks Technology Radar — Spec-driven development](https://www.thoughtworks.com/radar/techniques/spec-driven-development)).

Three complementary roles of a specification appear across tooling and commentary: **spec-first** (write before implementation), **spec-anchored** (keep the spec connected after implementation so it guides future change), and **spec-as-source** (treat the human-edited spec as the source from which plans, tasks, and tests are regenerated) ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

### Control plane vs. data plane

Across the major SDD stacks, the **control plane** is the durable Markdown (or equivalent) artifact graph—constitution/steering, requirements, design, tasks—plus human approval gates. The **data plane** is the coding agent’s tool loop (file edits, tests, MCP tools, sandboxes) that consumes those artifacts as structured context rather than chat history ([GitHub Spec Kit docs](https://github.github.com/spec-kit/); [Kiro introducing post](https://kiro.dev/blog/introducing-kiro/); [OpenSpec overview](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md); [Blitzy AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)).

### Canonical process topologies

| Stack | Process shape | Orchestration model |
| --- | --- | --- |
| **GitHub Spec Kit** | Constitution → Specify → Clarify → Plan → Checklist → Tasks → Analyze → Implement → Converge | Agent-driven slash-command pipeline; Spec Kit is a specification/process layer, not an execution engine; works with 38 agent integrations ([Agentic SDD reference](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Spec Kit home](https://github.github.com/spec-kit/)) |
| **AWS Kiro** | Requirements (EARS) → Design → Tasks → implement with parallel agents; plus event-driven **hooks** | Spec mode delays coding until requirements/design/tasks exist; tasks can run in dependency-ordered parallel waves; hooks fire on save/create/delete ([Introducing Kiro](https://kiro.dev/blog/introducing-kiro/); [Kiro product](https://kiro.dev/)) |
| **OpenSpec** | Explore (optional) → Propose → Apply → Archive; artifact chain `proposal → specs → design → tasks → implement` | Lightweight agreement layer; **delta specs** (`ADDED`/`MODIFIED`/`REMOVED`) for brownfield; “enablers, not gates” ([OpenSpec overview](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md); [Thoughtworks — OpenSpec](https://www.thoughtworks.com/radar/tools/openspec)) |
| **Blitzy** | Codebase onboarding → Technical Spec → Generation prompt → Agent Action Plan (AAP) review → multi-agent execution → validation → Project Guide / PR | Orchestrator sequences specialized agents (architecture, implementation, testing, debugging, validation, integration); independent AAP tasks run in parallel ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [AAP review docs](https://docs.blitzy.com/project-lifecycle/aap-review)) |

Thoughtworks contrasts interpretations: Kiro’s three stages (requirements, design, tasks); Spec Kit’s richer orchestration plus a **constitution** of immutable principles; Tessl (private beta as of Sep 2025) where the specification itself is the maintained artifact rather than the code ([Thoughtworks — Spec-driven development](https://www.thoughtworks.com/radar/techniques/spec-driven-development)).

### Spec Kit mechanics (reference pipeline)

Only `/speckit.specify` is strictly required before `/speckit.plan`; clarify, checklist, and analyze are optional quality gates for ambiguous work ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).

- **`/speckit.constitution`**: project principles every later phase is evaluated against ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).
- **`/speckit.specify`**: what/why requirements and user stories; tech stack deferred to plan ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).
- **`/speckit.clarify`**: up to five targeted questions per run; answers encoded back into `spec.md` ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).
- **`/speckit.plan`**: design artifacts, stack, architecture ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).
- **`/speckit.tasks`**: dependency-ordered `tasks.md` with phases Setup → Foundational → per–user-story → Polish; marks parallelizable tasks ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md)).
- **`/speckit.analyze`**: read-only cross-artifact consistency across `spec.md` / `plan.md` / `tasks.md`; never edits files ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).
- **`/speckit.implement`**: executes tasks in dependency order; checklist checkbox state is a gate (unchecked items prompt before proceed; implement must not flip checklist markers) ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).
- **`/speckit.converge`**: append-only gap assessment; either reports Converged or appends Convergence tasks for another implement pass ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)).

Templates force `[NEEDS CLARIFICATION]` markers instead of silent LLM guesses, and keep specify-phase output free of premature HOW/tech-stack details ([`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md)).

### Context retrieval topologies

Enterprise brownfield SDD (Blitzy case study) pairs a change specification with a **Dynamic Knowledge Graph** built from multi-repo analysis (control flow, call graphs, inheritance, module dependencies), then uses **GraphRAG**-style traversal—forward for downstream effects, reverse for dependents—to bound blast radius before planning ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); Microsoft GraphRAG referenced therein). Kiro generates baseline steering docs `structure.md`, `tech.md`, and `product.md` from the existing codebase before feature specs ([Kiro from-chat-to-specs](https://kiro.dev/blog/from-chat-to-specs-deep-dive/)). Anthropic’s agent context guidance argues for the smallest high-signal token set and **just-in-time** loading of identifiers/tools rather than stuffing exploratory chat into the window—aligning with Kiro’s claim that externalized Markdown specs free context for coding ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Kiro from-chat-to-specs](https://kiro.dev/blog/from-chat-to-specs-deep-dive/)).

### Message / execution style

SDD frameworks are predominantly **synchronous, human-gated, artifact-passing** pipelines (slash commands → Markdown → agent tool calls). Parallelism appears at the **task-graph** layer (Spec Kit `[P]` markers; Kiro parallel agents; Blitzy independent AAP workstreams), not as a shared A2A protocol among peer agents ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Kiro product](https://kiro.dev/); [System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)). Tool dispatch in IDE/CLI stacks commonly uses MCP (Kiro documents MCP + AGENTS.md + Skills; Spec Kit can convert tasks to GitHub issues via GitHub MCP) ([Kiro product](https://kiro.dev/); [Agentic SDD — taskstoissues](https://github.github.io/spec-kit/reference/agentic-sdd.html)).

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for this dimension. Published p50/p95/p99 latency SLAs, RPM/TPM ceilings, and semantic-cache hit rates specific to SDD orchestration loops are not disclosed by Spec Kit, OpenSpec, or Blitzy. Available figures are credit/line pricing, model multipliers, and qualitative context-cost guidance.

### Cost models (published)

**Kiro (credit-based, no daily/weekly rate limits):** Free = 50 credits; Pro = 1,000 credits at $20/user/month; Pro+ = 2,000 at $40; Pro Max = 5,000 at $100; Power = 10,000 at $200. Add-on credits on paid tiers cost **$0.04/credit** (packs $5–$100; unused add-ons roll 12 months). Credits are consumed fractionally; complex/lengthy tasks burn more than simple edits ([Kiro pricing](https://kiro.dev/pricing/); [Kiro billing docs](https://kiro.dev/docs/billing/); [Add-on credits](https://kiro.dev/docs/billing/add-on-credits/)).

**Kiro model-tier multipliers vs. Auto baseline (1.0×)** (selected, from product page): `deepseek-3.2` / `minimax-m2.5` at 0.25×; `claude-haiku-4.5` at 0.4×; `claude-sonnet-*` at 1.3×; `claude-opus-*` near 2.0–2.2×; `gpt-5.6-sol` at 4.4×; `claude-fable-5.1` at 6.0×. Auto routes by complexity factoring quality, latency, and cost ([Kiro product](https://kiro.dev/)). [inferred] A full feature “spec run” that costs ~15–25 credits on community reports would be ~$0.60–$1.00 at add-on rates after plan exhaustion—not an official Kiro figure.

**Blitzy (line-based enterprise):** Evaluation: Reverse Engineer $0 (≤100K LOC), POC $50K / 2 months, Structured Pilot $250K / 6 months (5M LOC onboard + 1.25M generate). Deployment: Commercial ~$500K/yr (first 20M LOC onboard included; then **$0.10/line onboard**, **$0.20/line generate** from 2.5M); Enterprise ~$5M/yr; Transformation ~$50M/yr. All tiers claim SOC 2 Type II + ISO 27001 and no training on customer code ([Blitzy Security / pricing FAQ](https://blitzy.com/security)).

**Open-source Spec Kit / OpenSpec:** process tooling is free; token cost is whatever the attached coding agent (Copilot, Claude Code, Codex, etc.) bills—Spec Kit explicitly supports switching among 38 integrations without lock-in ([Spec Kit home](https://github.github.com/spec-kit/)).

### Context / caching economics (architectural, not hit-rate benchmarks)

Anthropic: finite attention budget → minimize high-signal tokens; prefer just-in-time retrieval over upfront corpus loading; agents should hold lightweight identifiers and fetch at runtime ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). Kiro’s design claim: exploratory chat fills the window before coding; Markdown specs move planning out of the active window so coding gets maximum remaining context ([Kiro from-chat-to-specs](https://kiro.dev/blog/from-chat-to-specs-deep-dive/)). Spec Kit docs advise staging large features (`/speckit.implement` Setup+Foundational only, then user stories) to avoid overwhelming agent context ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)). Thoughtworks reports **instruction bloat → context rot** when constitutions/prompt files grow unbounded; remediation was extracting reusable guidance into Skills and loading detail only when needed ([Thoughtworks — GitHub Spec Kit](https://www.thoughtworks.com/radar/languages-and-frameworks/github-spec-kit)).

### Dynamic model routing

Kiro Auto selects models by task complexity with explicit credit multipliers (above) ([Kiro product](https://kiro.dev/)). Blitzy’s multi-agent orchestration is described as coordinating specialized agents across models for architecture/implementation/QA rather than a single frontier model for every step ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)). No public SDD-specific semantic-cache TTLs or prompt-cache hit rates were found.

### Throughput / back-pressure

Kiro markets **no daily or weekly rate limits**, with prepaid add-ons / optional enterprise overages at $0.04/credit as the back-pressure mechanism when monthly credits exhaust ([Kiro pricing](https://kiro.dev/pricing/); [Add-on credits](https://kiro.dev/docs/billing/add-on-credits/)). Blitzy states jobs are **not cancelable** once submitted and consume assigned quota ([Blitzy Security FAQ](https://blitzy.com/security)). Spec Kit has no platform TPM/RPM—limits are those of the underlying agent.

## 3. Distributed Resilience & State

> ⚠️ Limited public data available for this dimension. Spec Kit and OpenSpec are local artifact workflows without published Temporal/Kafka/event-sourcing runtimes, distributed locks, or circuit-breaker configs. Resilience patterns below are those documented for SDD control-plane state and enterprise agent sandboxes—not classic microservice SLO machinery.

### Durable “workflow” state = versioned artifacts

- Spec Kit feature state lives under `specs/[branch]/` (`spec.md`, `plan.md`, `tasks.md`, contracts, research); `/speckit.specify` auto-creates a semantic branch and numbered feature directory ([`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md)).
- OpenSpec persists truth in `openspec/specs/` and in-flight work in `openspec/changes/<change>/`; **archive** merges delta requirements into main specs and moves the change folder to `changes/archive/` with a date stamp—providing an audit trail of completed work ([OpenSpec overview](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md)).
- Blitzy’s AAP is the durable, human-approved execution blueprint (file changes, dependencies, design decisions, scope boundaries) that must be approved before generation; incomplete AAPs (e.g., “TBD”) are explicitly rejected as review failures ([AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)).

### Checkpointing & replay semantics

- **Human checkpoints**: Spec Kit plan/analyze/checklist gates; Blitzy AAP review + merge decision; Kiro review of requirements/design/tasks before implementation ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [AAP review](https://docs.blitzy.com/project-lifecycle/aap-review); [Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)).
- **Converge loop (Spec Kit)**: after implement, `/speckit.converge` is append-only (never deletes code); if gaps remain it appends Convergence tasks → re-implement → re-converge until Converged ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)). [inferred] This is a closed-loop reconciliation pattern analogous to desired-state controllers, implemented via Markdown task append rather than an orchestrator DB.
- **OpenSpec fluidity**: artifacts are revisitable mid-flight (“enablers, not gates”); wrong design mid-implementation → edit `design.md` and continue ([OpenSpec overview](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md)).
- **Kiro Web sandboxes**: each assigned task spins up an isolated sandbox, clones authorized repos, executes with permitted resources, then tears down—ephemeral execution environments with configurable internet domain allowlists ([Kiro Sandbox docs](https://kiro.dev/docs/web/sandbox/)).

### Concurrency & parallel execution risks

Spec Kit and Kiro mark/run independent tasks in parallel ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Kiro product](https://kiro.dev/)). [inferred] Without file-level locking, overlapping parallel agents can produce merge conflicts or duplicated assumptions—operators must keep parallel groups truly independent (different files/interfaces), matching Kiro’s own dependency-graph “waves” framing.

### Rate limits / graceful degradation

Kiro: credit exhaustion pauses usage until add-on purchase or monthly reset (Free has no add-ons) ([Add-on credits](https://kiro.dev/docs/billing/add-on-credits/)). Blitzy: no mid-job cancel; quota is consumed on submit ([Blitzy Security FAQ](https://blitzy.com/security)). No published circuit-breaker threshold tables for SDD orchestrators.

### Spec-adherence during long runs

Blitzy describes orchestrating **spec-adherence agents** during execution that continuously check generated work against approved requirements across file → E2E levels to catch incomplete work, missing requirements, and out-of-scope changes ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

## 4. Enterprise Security & Governance

### Isolation & deployment

- **Blitzy**: customer data encrypted in transit and at rest; **no training on user code** and no direct storage of code (embeddings only, per security page); **air-gapped** code-generation platform with no public endpoints; **inbound-only VPC** architecture that never initiates outbound requests; deployment options Cloud / VPC / black-box VPC / on-prem / black-box on-prem; SOC 2 Type II and ISO 27001 across tiers ([Blitzy Security](https://blitzy.com/security); [System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).
- **Kiro Web**: per-task sandboxes; secrets encrypted at rest and injected as env vars, with explicit warning that agents may exfiltrate secrets via code, logs, or external requests—provide least-privilege secrets only ([Kiro Sandbox](https://kiro.dev/docs/web/sandbox/); [Kiro env vars/secrets](https://kiro.dev/docs/web/sandbox/environment-variables/)). Enterprise features advertised: IAM/SSO, usage dashboards, cost controls, IP indemnity, governance ([Kiro product](https://kiro.dev/)).
- **Spec Kit**: runs offline/behind firewalls; orgs can host private extension/preset catalogs; community extensions such as **CI Guard** and **Architecture Guard** add compliance gates ([Spec Kit home](https://github.github.com/spec-kit/)).

### Policy layers (constitution / rules / steering)

- Spec Kit **constitution** encodes stack versions, naming, architectural intent, allowed/banned libraries, auth/data/audit requirements—read before implementation so agents follow *project* standards, not generic training priors ([EPAM on Spec Kit](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering); [Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)). Thoughtworks field reports: useful constitutions capture scope, domain context, tech versions, coding standards, and repository structure (e.g., hexagonal/layered) ([Thoughtworks — GitHub Spec Kit](https://www.thoughtworks.com/radar/languages-and-frameworks/github-spec-kit)).
- Blitzy separates **Rules** (project-wide: style, testing, security) from **Generation prompts** (change-specific intent); AAP review must verify every custom rule’s *intent* appears (rules may be dropped if conflicting—reviewers must catch omissions) ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [AAP review](https://docs.blitzy.com/project-lifecycle/aap-review)).
- Kiro **steering** files (`structure.md`, `tech.md`, `product.md`) plus hooks for credential scans on commit ([Kiro from-chat-to-specs](https://kiro.dev/blog/from-chat-to-specs-deep-dive/); [Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)).

### Least privilege & prompt-injection posture

Newsletter framing for autonomous SDD: limit repository, tool, and network access; apply least privilege; treat retrieved code/comments/docs as **data**, not trusted instructions, to reduce prompt-injection risk (references OWASP LLM/Agentic Top 10) ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)). Blitzy documents `.blitzyignore` for excluding paths from tech-spec and generation ([Blitzy Security FAQ](https://blitzy.com/security)). Kiro sandboxes support domain-restricted internet access and MCP/Powers configuration ([Kiro Sandbox](https://kiro.dev/docs/web/sandbox/)).

### Human oversight models

- **Human-in-the-loop**: mandatory AAP approval before Blitzy generation; Spec Kit checklist/analyze gates; Kiro spec review before coding ([AAP review](https://docs.blitzy.com/project-lifecycle/aap-review); [Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)).
- **Human-on-the-loop**: Blitzy multi-agent execution with QA agents, then human merge; Kiro hooks run background checks while developers work ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)).
- Escalation guidance: raise human oversight for auth/session, data migrations, external integrations, and regulatory logic ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

### Auditability

OpenSpec archive folders timestamp completed changes ([OpenSpec overview](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md)). Blitzy Project Guide on each PR maps built work to requirements and remaining tasks ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)). Spec Kit `tasks.md` + optional GitHub issues provide execution trail ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)). Formal immutable audit-log schemas for SDD control planes are not published as open standards.

## 5. Production Failure Modes

### Intent vs. test-pass (canonical SDD motivator)

The newsletter’s notification migration vignette: agent implements a change, tests pass, but a user who disabled email still receives assignment mail—tests verified local correctness, not preserved preferences/intent. Longer prompts fail to supply system context, change discipline, or autonomy controls ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

### Context window degradation

Symptoms: earlier instructions “forgotten,” quality drop as exploratory dialogue fills the window ([Anthropic Claude Code best practices via product docs](https://code.claude.com/docs/en/best-practices.md); [Kiro from-chat-to-specs](https://kiro.dev/blog/from-chat-to-specs-deep-dive/)). Mitigations used by SDD tooling: externalize plans to Markdown; stage implement phases; just-in-time retrieval; Skills instead of monolithic constitutions ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Thoughtworks — GitHub Spec Kit](https://www.thoughtworks.com/radar/languages-and-frameworks/github-spec-kit); [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). EPAM: quality collapses when a single request spans **10+ files** or multi-hour autonomous windows; Spec Kit’s cure is Feature → User Stories → atomic tasks typically touching **1–2 files** ([EPAM Spec Kit blog](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering)).

### Infinite / unbounded execution loops

Risk: agents loop across files introducing regressions under unbounded autonomy ([EPAM Spec Kit blog](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering)). Guards: dependency-ordered task lists with human gates; Spec Kit checklist gate before implement; Blitzy AAP scope boundaries and out-of-scope lists; credit/quota caps (Kiro/Blitzy) ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [AAP review](https://docs.blitzy.com/project-lifecycle/aap-review); [Kiro billing](https://kiro.dev/docs/billing/)).

### Spec–code drift & state divergence

Causes: implementation diverges from approved requirements; partial task completion; silent scope creep ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)). Detection/mitigation: Spec Kit `/speckit.analyze` (pre-implement) and `/speckit.converge` (post-implement); Blitzy continuous spec-adherence agents + Project Guide; OpenSpec archive merges deltas so truth catches up; Kiro bidirectional sync (update specs from code or refresh tasks from specs) ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [OpenSpec overview](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md); [Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)). Unsolved open question called out by Neo Kim: when code and spec disagree, who decides which is intended behavior ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

### Hallucinated assumptions / tool parameters

Spec Kit templates mandate `[NEEDS CLARIFICATION]` instead of guessing auth methods, etc. ([`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md)). `/speckit.clarify` and `/speckit.checklist` (“unit tests for requirements”) catch underspecification before plan/implement ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html)). Kiro uses EARS acceptance criteria and automated reasoning to check requirements for contradictions/gaps before coding; property-based tests assert invariants across randomized inputs beyond unit-test examples ([Introducing Kiro](https://kiro.dev/blog/introducing-kiro/); [Kiro product](https://kiro.dev/)). Formal specs (types, OpenAPI, tests) as agent instructions reduce hallucinated structural details vs. prose ([agentpatterns.ai — Specification as Prompt](https://agentpatterns.ai/instructions/specification-as-prompt/)).

### Cascading timeouts / multi-agent failure

> ⚠️ Limited public data available for this sub-area. No published timeout-budget or bulkhead configurations for Blitzy/Kiro orchestrators. Documented mitigations are organizational: smaller tasks, sequential phases for large features, and sandbox teardown per task ([Agentic SDD](https://github.github.io/spec-kit/reference/agentic-sdd.html); [Kiro Sandbox](https://kiro.dev/docs/web/sandbox/)).

### Process-level failure modes (field reports)

Thoughtworks: lengthy hard-to-review spec files; unclear audience for generated PRDs/user stories; risk that “handcrafting detailed rules for AI ultimately doesn’t scale”; Spec Kit teams saw unnecessary defensive checks and verbose Markdown increasing cognitive load—improved by trimming templates ([Thoughtworks — Spec-driven development](https://www.thoughtworks.com/radar/techniques/spec-driven-development); [Thoughtworks — GitHub Spec Kit](https://www.thoughtworks.com/radar/languages-and-frameworks/github-spec-kit)). EPAM: constitution authoring is hard/senior-heavy; Spec Kit (cited at v0.0.72 in their Oct 2025 write-up) was early-stage; expect only **60–80%** of generated code usable after review; weaker fit for scattered refactors vs. end-to-end features ([EPAM Spec Kit blog](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering)).

## 6. Enterprise System Design Scenarios

### Published scale / case signals

- **Blitzy BCC compiler**: newsletter cites **229,983** lines of Rust across **129** source files + 14 SIMD headers; **3,600+** agents coordinated across **127** files; **2,271** passing tests—illustrating multi-agent SDD at project scale ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).
- **Blitzy SWE-Bench Pro**: vendor claims **84.95%** on SWE-Bench Pro Public with multi-model orchestration (independently referenced as Quesma-verified in secondary coverage); treat as vendor-reported harness result, not a pure model score ([System Design Newsletter #181 references](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); see also Blitzy blog cites in that article’s reference list).
- **EPAM adoption**: safe delegation window expanding from **10–20 minute** tasks to multi-hour feature delivery under Spec Kit decomposition + gates ([EPAM Spec Kit blog](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering)).
- **Spec Kit ecosystem scale (docs, Sep 2026)**: **130K+** GitHub stars, **270+** contributors, **38** integrations, **157** extensions, **33** presets ([Spec Kit home](https://github.github.com/spec-kit/)). OpenSpec GitHub listing shows **~70K** stars at fetch time ([Fission-AI/OpenSpec](https://github.com/Fission-AI/OpenSpec)).

### Tool trade-off matrix

| Dimension | Spec Kit | Kiro | OpenSpec | Blitzy |
| --- | --- | --- | --- | --- |
| **Lock-in** | Low (38 agents; process layer) | Medium (AWS IDE/CLI/Web; ACP-compatible) | Low (30+ assistants; slash commands) | High (hosted multi-agent platform) |
| **Brownfield fit** | Strong with constitution + existing-project guide; Thoughtworks mostly brownfield trials | Steering docs from codebase analysis | **Delta specs** explicitly optimized for edits | Knowledge graph + living tech spec across repos |
| **Ops complexity** | Local Markdown + agent; org catalogs optional | IDE + credits + optional cloud sandboxes | Minimal CLI (`openspec init`) | Enterprise onboarding, VPC/on-prem options |
| **Governance depth** | Constitution + analyze/converge + extensions | Steering, hooks, IAM/SSO, sandboxes | Archive audit trail; lightweight | SOC2/ISO, air-gap, AAP + QA agents |
| **Cost shape** | Agent tokens only | Credits ($20–$200/mo tiers + $0.04) | Agent tokens only | $0.10/$0.20 per line + six-figure engagements |
| **Thoughtworks Radar** | Assess (Apr 2026) | Discussed under SDD technique (Nov 2025) | Assess (Apr 2026); praised for deltas vs. greenfield-heavy kits | Case-study vendor in newsletter (not a Radar blip) |

Sources for matrix cells: [Spec Kit](https://github.github.com/spec-kit/), [Kiro](https://kiro.dev/), [OpenSpec](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md), [Blitzy Security](https://blitzy.com/security), [Thoughtworks OpenSpec](https://www.thoughtworks.com/radar/tools/openspec), [Thoughtworks Spec Kit](https://www.thoughtworks.com/radar/languages-and-frameworks/github-spec-kit), [Thoughtworks SDD](https://www.thoughtworks.com/radar/techniques/spec-driven-development).

### Architecture choice rationale (when to pick what)

- **Greenfield / multi-agent IDE with AWS ecosystem**: Kiro specs + parallel agents + property-based tests + hooks ([Kiro product](https://kiro.dev/)).
- **Tool-agnostic team already on Copilot/Claude/Codex needing org process**: Spec Kit constitution + specify/plan/tasks/implement/converge ([Spec Kit home](https://github.github.com/spec-kit/); [Thoughtworks Spec Kit](https://www.thoughtworks.com/radar/languages-and-frameworks/github-spec-kit)).
- **Brownfield incremental change without rewriting the whole system spec**: OpenSpec delta + propose/apply/archive ([OpenSpec overview](https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md); [Thoughtworks OpenSpec](https://www.thoughtworks.com/radar/tools/openspec)).
- **Enterprise multi-repo autonomy with compliance packaging**: Blitzy knowledge graph + AAP + isolated generation ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [Blitzy Security](https://blitzy.com/security)).
- **Spec-as-maintained-artifact (radical)**: Tessl direction noted by Thoughtworks (private beta as of Sep 2025)—monitor separately ([Thoughtworks SDD](https://www.thoughtworks.com/radar/techniques/spec-driven-development)).

### Capacity planning notes

- Plan SDD capacity around **artifact review effort** and **agent credit/line budgets**, not only model RPM: EPAM shifts cost upstream to constitution/spec quality; Blitzy bills onboard/generate lines; Kiro bills credits with model multipliers ([EPAM Spec Kit blog](https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering); [Blitzy Security](https://blitzy.com/security); [Kiro pricing](https://kiro.dev/pricing/)).
- Concurrent agents: Blitzy’s BCC example implies thousands of agent instances for a large build; Spec Kit/Kiro concurrency is bounded by task-graph independence and human review bandwidth ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).
- Thoughtworks caution (Apr 2026 OpenSpec blip): as native coding-agent capability grows, re-evaluate whether heavyweight SDD tooling remains necessary ([Thoughtworks — OpenSpec](https://www.thoughtworks.com/radar/tools/openspec)).

### Principal-architect interview angles

1. Separate **project rules** (constitution/steering) from **change specs** (generation prompt / delta) ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).
2. Design **verification vs. validation**: tests ≠ intent; add acceptance criteria, property tests, and converge/adherence loops ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents); [Kiro product](https://kiro.dev/)).
3. Treat context as a scarce NFR: high-signal specs + JIT retrieval beat chat archaeology ([Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
4. Size autonomy to blast radius: HITL for auth/data/regulatory; HOTL for reversible low-risk tasks ([System Design Newsletter #181](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents)).

## Sources

- [1] https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents — Neo Kim #181 primary article (Blitzy SDD case study)
- [2] https://github.github.com/spec-kit/ — GitHub Spec Kit official documentation home
- [3] https://github.github.io/spec-kit/reference/agentic-sdd.html — Spec Kit Agentic SDD command reference
- [4] https://github.com/github/spec-kit/blob/main/spec-driven.md — Spec Kit SDD philosophy and template constraints
- [5] https://github.com/github/spec-kit/ — Spec Kit GitHub repository / README
- [6] https://www.thoughtworks.com/radar/techniques/spec-driven-development — Thoughtworks Radar: Spec-driven development (Assess, Nov 2025)
- [7] https://www.thoughtworks.com/radar/languages-and-frameworks/github-spec-kit — Thoughtworks Radar: GitHub Spec Kit (Assess, Apr 2026)
- [8] https://www.thoughtworks.com/radar/tools/openspec — Thoughtworks Radar: OpenSpec (Assess, Apr 2026)
- [9] https://raw.githubusercontent.com/Fission-AI/OpenSpec/main/docs/overview.md — OpenSpec core concepts
- [10] https://github.com/Fission-AI/OpenSpec — OpenSpec repository
- [11] https://kiro.dev/ — Kiro product page (specs, parallel agents, model multipliers, pricing stance)
- [12] https://kiro.dev/blog/introducing-kiro/ — Introducing Kiro (specs, EARS, hooks)
- [13] https://kiro.dev/blog/from-chat-to-specs-deep-dive/ — Kiro deep dive on SDD and steering docs
- [14] https://kiro.dev/docs/web/sandbox/ — Kiro Web sandbox isolation lifecycle
- [15] https://kiro.dev/docs/web/sandbox/environment-variables/ — Kiro secrets handling warnings
- [16] https://kiro.dev/pricing/ — Kiro subscription tiers and credit economics
- [17] https://kiro.dev/docs/billing/ — Kiro billing / credit consumption
- [18] https://kiro.dev/docs/billing/add-on-credits/ — Add-on credits at $0.04
- [19] https://docs.blitzy.com/project-lifecycle/aap-review — Blitzy Agent Action Plan review checklist
- [20] https://blitzy.com/security — Blitzy security, compliance, deployment, line pricing FAQ
- [21] https://www.epam.com/insights/ai/blogs/inside-spec-driven-development-what-githubspec-kit-makes-possible-for-ai-engineering — EPAM field experience with Spec Kit
- [22] https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — Anthropic context engineering for agents
- [23] https://code.claude.com/docs/en/best-practices.md — Claude Code context-window degradation guidance
- [24] https://agentpatterns.ai/instructions/specification-as-prompt/ — Formal specs (types/OpenAPI/tests) as agent instructions
- [25] https://github.github.io/spec-kit/ — Spec Kit docs alternate host (process overview)
- [26] https://openspec.dev/docs/cli — OpenSpec CLI reference (workflow commands)
- [27] https://www.microsoft.com/en-us/research/blog/graphrag-unlocking-llm-discovery-on-narrative-private-data/ — GraphRAG (referenced via newsletter for relationship-aware retrieval)
- [28] https://owasp.org/www-project-top-10-for-large-language-model-applications/ — OWASP LLM Top 10 (prompt injection framing cited by newsletter)
