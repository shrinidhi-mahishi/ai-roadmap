# 11. Specialized Agents

> Research compiled for the Principal AI Architect study guide.
> Sources span 2024-2026; data current as of August 2026.

---

## Table of Contents

1. [Coding Agents](#1-coding-agents)
2. [Research and Knowledge Agents](#2-research-and-knowledge-agents)
3. [Browser and Computer Use Agents](#3-browser-and-computer-use-agents)
4. [Data and Analysis Agents](#4-data-and-analysis-agents)
5. [Domain-Specific Agents](#5-domain-specific-agents)
6. [Agent Specialization Patterns](#6-agent-specialization-patterns)
7. [Benchmarks and Evaluation](#7-benchmarks-and-evaluation)
8. [Key Takeaways for Architects](#8-key-takeaways-for-architects)
9. [Sources](#9-sources)

---

## 1. Coding Agents

### 1.1 Market Overview

The AI coding-tool market crossed **$7 billion in annual revenue** in April 2026. Terminal-native coding agents emerged as a major category between February 2025 and early 2026, with every major AI lab shipping one: Anthropic (Claude Code), OpenAI (Codex CLI), Google (Gemini CLI), Block (Goose), and Sourcegraph (Amp). The most-starred open-source coding agent is opencode with 193,678 GitHub stars, ahead of Claude Code (140,331), Gemini CLI (106,374), and OpenAI Codex (104,106).

### 1.2 Claude Code

**Architecture.** Claude Code is a terminal-native agentic coding tool that runs as the `claude` command. Its core is a simple while-loop that calls the model, runs tools, and repeats. The real complexity lives in the systems around this loop: a permission system with seven modes and an ML-based classifier, a five-layer compaction pipeline for context management, and isolated subagent boundaries for multi-agent orchestration. A research paper (arXiv:2604.14228) identified five human values motivating the architecture: human decision authority, safety/security, reliable execution, capability amplification, and contextual adaptability.

**Agent SDK.** First shipped in September 2025 as Claude Code SDK, renamed to Claude Agent SDK later that month. Available in Python and TypeScript, it exposes the same engine as a library with subagents, lifecycle hooks, sessions, MCP support, and a hosted execution model. Built-in tools include Read/Write/Edit, Bash, Glob, Grep, WebSearch, WebFetch, and AskUserQuestion. From June 15, 2026, subscription plans include separate monthly Agent SDK credits: $20 (Pro), $100 (Max 5x), $200 (Max 20x).

**Key features (2026).** Plan Mode, native plugin architecture, Slack integration, subagent orchestration, Routines for async automations, MCP server connectivity, Outcomes (rubric-based grading), multi-agent orchestration via Managed Agents, and "Dreaming" (background synthesis).

**Benchmark.** Claude Opus 4.7 achieves ~87.6% on SWE-bench Verified under the Claude Code harness. Opus 4.7 scores 69.4% on Terminal-Bench 2.0, up from Opus 4.6's 65.4%.

**Usage data.** Anthropic published a privacy-preserving analysis of ~400,000 interactive sessions from ~235,000 users (Oct 2025 - Apr 2026). Writing and data analysis roughly doubled from ~10% to ~20% of sessions.

### 1.3 OpenAI Codex CLI

**Architecture.** Released April 2025, written from scratch in Rust under Apache 2.0 license. The agent loop assembles a prompt, sends it to the model, and processes the response. Its critical architectural distinction is a **kernel-level sandboxed execution model**: network access is disabled during command execution by default and file operations are scoped to the current directory tree.

**Approval modes.** Suggest (default, proposes every edit), Auto Edit (applies file edits but confirms commands), and Full Auto (executes everything within sandbox constraints).

**Growth.** 3 million weekly active users, 14.5 million monthly npm downloads, ~74,468 GitHub stars (April 2026). Usage rose from ~5% of Claude Code's to ~40% between September 2025 and January 2026.

**Model evolution.** Launched with codex-1 (o3 variant). GPT-5.3-Codex arrived February 2026, followed by GPT-5.3-Codex-Spark on Cerebras hardware (~15x faster). Now runs on GPT-5.5, OpenAI's agentic-first base model.

**Multi-surface strategy.** CLI, web app (chatgpt.com/codex), desktop app (Windows/macOS), IDE extensions for VS Code/Cursor/Windsurf. Supports MCP servers with parallel tool calls.

**Security.** Codex Security launched March 2026 — an application-security agent for vulnerability identification.

### 1.4 Cursor

**Company.** Built by Anysphere. Became the fastest-growing SaaS product in history: **$2B ARR** by February 2026, over 2 million users, 1 million+ paying customers, adoption by half the Fortune 500. Valued at $29.3B.

**Key features.** VS Code fork with Supermaven autocomplete engine (72% acceptance rate). Agent Mode uses 20x scaled RL for multi-file editing. Background Agents clone repos in the cloud and work autonomously, opening PRs when done. 200K token context window. `.cursorrules` system for encoding team conventions.

**Pricing.** $20/month (individual), $40/user/month (Teams).

### 1.5 Windsurf / Devin Desktop

**Evolution.** Codeium pivoted to an agentic editor called Windsurf in 2024. Renamed from Codeium to Windsurf in April 2025. After OpenAI's reported acquisition collapsed in July 2025, Google hired Windsurf's founders in a $2.4B licensing deal and Cognition acquired the product. Renamed to **Devin Desktop** on June 2, 2026.

**Key features.** Cascade agent acts autonomously with less configuration than Cursor. Remote indexing scales beyond 1M+ lines of code. Plugins for 40+ IDEs (JetBrains, Vim, NeoVim, XCode). Windsurf 2.0 (April 2026) added Agent Command Center (Kanban for agent statuses), Spaces, and Devin Cloud integration. Cascade rewritten in Rust as Devin Local with 30% better token efficiency, subagent support, and sandboxing.

**Enterprise compliance.** ZDR, SOC 2, HIPAA, FedRAMP/DOD, ITAR, RBAC, SCIM — significantly broader than Cursor (SOC 2 only).

### 1.6 Devin

**What it is.** Cognition's autonomous AI software engineer. Gets its own virtual machine with shell, code editor, and browser. Can clone repos, install dependencies, run tests, debug failures, and open PRs without human intervention.

**Key capabilities (2026).** Self-healing code (reads error logs, iterates autonomously). Legacy code migration (COBOL, Fortran, Objective-C to Rust, Go, Python). Automated PR creation with detailed descriptions. Additional surfaces: Devin CLI, Devin Review, Devin Windows VM, DeepWiki.

**Pricing evolution.** Launched at $500/month (2024). Cut to $20/month Core plan (April 2025). Current tiers (mid-2026): Free ($0), Pro ($20/seat/mo), Max ($200/seat/mo), Teams ($80 base + $40/dev seat/mo), Enterprise (custom).

**Performance.** Devin 2.0 scores 45.8% on SWE-bench Verified (conservative pass@1, no human assistance). SWE-1.7 model scores 77.8% on SWE-bench Multilingual, 81.5% on Terminal-Bench 2.1, 42.3% on FrontierCode 1.1 Main. Cognition reports ~75% task completion rate.

**Enterprise adoption.** Named customers include Goldman Sachs, Microsoft, Anduril, Ramp, MongoDB, Santander, Zillow, U.S. Army, Intact Insurance, Nubank. Goldman Sachs piloted alongside 12,000 human developers with 20% efficiency gains. Nubank migrated a multi-million line ETL monolith with 8-12x engineering efficiency gains.

**Funding.** Over $1 billion raised at $26 billion valuation from Founders Fund, Lux Capital, General Catalyst, 8VC, Elad Gil.

### 1.7 SWE-bench Verified Leaderboard (Mid-2026)

| Agent/Model | SWE-bench Verified | Notes |
|---|---|---|
| Claude Opus 5 | 97.0% | Closed leader (Vals AI) |
| DeepSeek V4 Pro 0813 | 96.4% | Top open-weight |
| GPT-5.6 Sol | 96.2% | Codex default model |
| Claude Fable 5 | 95.0% | Restored July 1, 2026 |
| Kimi K3 | 93.4% | Open-weight |
| Claude Opus 4.8 | 88.6% | Everyday Claude default |
| Claude Opus 4.7 (Claude Code) | 87.6% | Claude Code harness |
| GPT-5.3 Codex | 85.0% | Codex harness |
| OpenHands | 72.0% | Open-source |
| Devin 2.0 | 45.8% | Conservative pass@1 eval |

**Key insight:** SWE-bench Verified is saturated above 95% by every frontier model; it is kept for historical comparability only. The scaffold around the model accounts for more variance than swapping frontier models: the same base model in different scaffolds varies by 15+ percentage points.

---

## 2. Research and Knowledge Agents

### 2.1 Deep Research Systems Overview

Deep research agents conduct multi-step, autonomous research by iteratively planning queries, searching, reading results, reflecting, and synthesizing findings into comprehensive reports. The core architectural pattern across all mature implementations is a **Plan -> Search -> Read -> Reflect -> Iterate -> Synthesize** loop, with the most capable systems layering multi-agent parallelism on top.

### 2.2 Gemini Deep Research

**Architecture.** Google's Deep Research uses Gemini 3 Pro as the reasoning core, specifically trained to reduce hallucinations. The agent autonomously navigates complex information landscapes by scaling multi-step reinforcement learning for search. Users can review and modify the structured research plan before execution.

**Key features (2026).** Retrieves data from both public web and company internal systems via MCP. Available through the Interactions API (public preview July 7, 2026) so developers can embed research into applications. Deep Research Max uses enhanced compute for harder problems.

**Benchmarks.** Leads DeepResearch Bench at 48.9 points. Gemini Deep Research Max leads Humanity's Last Exam at 54.6% (April 2026). On BrowseComp, Gemini 3.1 Pro scored 85.9 (25+ points above Gemini 3 Pro).

### 2.3 OpenAI Deep Research

**Architecture.** Integrated into ChatGPT's o3 model (launched February 2025). Adjusts research path in real time (more adaptive, less structured than Gemini). Supports multimodal analysis including text, images, and PDFs (Gemini is text-only).

**Benchmarks.** Scored 26.6% on Humanity's Last Exam at launch. Produces deeper reports for hard or ambiguous analytical work.

**API changes.** The standalone o3-deep-research and o4-mini-deep-research API models shut down July 23, 2026. API deep research now runs on GPT-5.x models with the web search tool. Original pricing: $10/M input tokens, $40/M output tokens for o3.

### 2.4 Perplexity Deep Research

**Architecture — "Search as Code."** The model generates code that calls Perplexity's Agentic Search SDK, runs thousands of retrieval steps in parallel inside a secure sandbox, deduplicates and reranks results in code, then feeds the cleaned corpus to the reasoning model. Uses "chain-of-search" reasoning: broad initial searches to establish knowledge boundaries, identifies gaps through semantic analysis, generates follow-up queries using template-based refinement.

**Scale.** A documented example consumed 193,000 reasoning tokens for a single 21-search query. 45+ million monthly active users (early 2026), more than double the 22M at start of 2025.

**Speed advantage.** Completes research in 2-4 minutes vs. 20+ for ChatGPT. Cites 100-300 sources (vs. 20-50 for ChatGPT). Live process view for transparency.

**Products (2026).** Perplexity Computer (agentic browser, February 2026). Comet AI browser (free since October 2025). Personal Computer for Mac (April-May 2026). SPACE sandbox for long-running agentic workflows. Numbat open-source agent security suite.

**Benchmark.** Independent evaluations place Sonar's Deep Research mode alongside Gemini 2.5 Pro Grounding at the top of web-augmented benchmark scores. Moving Deep Research inside Computer improved factual accuracy, depth, and citation quality on Humanity's Last Exam, BrowseComp, and DeepSearchQA.

### 2.5 Comparative Assessment

| Dimension | Gemini Deep Research | OpenAI Deep Research | Perplexity Deep Research |
|---|---|---|---|
| **Strength** | Breadth, Google integration, API access | Analytical depth, multimodal, hard reasoning | Speed, source volume, transparency |
| **DeepResearch Bench** | 48.9 (leader) | 46.5 | Competitive (within 4 pts) |
| **Speed** | Moderate | 20+ minutes | 2-4 minutes |
| **Source count** | Broad web + internal | 20-50 cited | 100-300 cited |
| **Multimodal** | Text-only research | Text + images + PDFs | Code-driven retrieval |
| **API availability** | Interactions API (July 2026) | GPT-5.x with web search | Sonar API |

**Overall assessment (2026):** Perplexity Pro is the best all-round deep research tool on price and speed. OpenAI and Claude produce the deepest reports for hard or ambiguous work. Gemini wins when source material lives in Google or you need a callable API. Differences in search breadth, long-context stability, structured writing, and citation clarity are shrinking; differentiation now rests on workflow control and enterprise readiness.

---

## 3. Browser and Computer Use Agents

### 3.1 Browser Agent Categories

The browser-agent landscape in 2026 splits along two modality axes:

- **DOM-driven agents** receive parsed HTML or structured DOM representations and act through DOM operations (click, type, navigate). These fit Playwright, Selenium, and BrowserGym-style stacks. Benchmarked by WebArena.
- **Vision-driven agents** consume screenshots and interact through mouse/keyboard events. Benchmarked by VisualWebArena and OSWorld.

### 3.2 Five Dominant Browser Stacks (2026)

| Stack | Type | Reliability | Notes |
|---|---|---|---|
| Playwright + Claude | DOM + agentic | 92% | DX leader, deterministic + agentic hybrid |
| Browserbase | Managed CDP-as-a-service | 90% | Cloud-hosted runtime |
| Stagehand | AI primitives on Playwright | 89% | Cleanest abstraction, likely the template others will follow |
| Anthropic Computer Use | Vision-driven screen control | 78% | Fallback when DOM access fails |
| OpenAI CUA (Operator) | Vision-driven, cloud-only | 75% | OpenAI-locked ecosystem |

DOM-driven stacks lead vision-driven by 12-17 percentage points. The winning 2026 approach is **hybrid**: AI automation layered on deterministic Playwright, with vision-driven fallback. Pure AI automation is too slow/expensive; pure deterministic is too brittle.

### 3.3 Anthropic Computer Use

**How it works.** Claude perceives screens visually, takes frequent screenshots translated into a coordinate grid ("counts pixels"), identifies buttons/fields/icons, and acts through mouse/keyboard inputs. Uses a feedback loop to handle unexpected pop-ups or loading screens.

**Core tools.** Computer tool (mouse/keyboard), Text Editor (file operations), Bash tool (system commands). Returns screen coordinates that the application maps to the active display.

**Architecture layers.** First attempts direct API integrations (Gmail, Slack), then falls back to browser control, finally interacts with the screen itself.

**Performance trajectory.**
- Late 2024: ~15% on OSWorld benchmark (beta launch)
- Mid-2025 (Claude 4.5): Over 60% on OSWorld
- Late 2025 (Claude 4 / Sonnet 4.5): High 80s on standard office tasks
- August 2026: Claude Mythos 5 / Fable 5 at 85% on OSWorld

**API evolution.** Beta header `computer-use-2025-11-24` for Claude 4.x models. New `computer_toolset_20260801` client toolset (August 2026) removes beta header requirement.

**Consumer launch.** March 24, 2026 — research preview for Claude Pro and Max subscribers on macOS. Users can message Claude a task from a phone and the agent completes it on the computer.

**Safety.** Recommended to run in VMs/containers with minimal privileges. ML classifiers run on prompts to flag prompt injections in screenshots; model asks for user confirmation before proceeding.

### 3.4 OpenAI Operator / Computer-Using Agent (CUA)

**Architecture.** GPT-4o variant fine-tuned on GUI interaction through reinforcement learning. Sees the web through screenshots, interacts through mouse/keyboard events. Arrived early 2025, reached full ChatGPT integration by July 2025.

**Performance.** 87% success rate on WebVoyager, 58.1% on WebArena (internal benchmarks). 75.0% on OSWorld (March 2026 with GPT-5.4).

### 3.5 OSWorld Performance Timeline

OSWorld is the definitive desktop computer use benchmark: 369 tasks across real Ubuntu Linux desktop applications, requiring pixel-precise keyboard and mouse interactions. Human baseline: 72.36%.

| Date | Agent | OSWorld Score |
|---|---|---|
| Apr 2024 | Best at launch | 7-12% |
| Jan 2025 | OpenAI CUA (Operator) | 38.1% |
| Sept 2025 | Claude Sonnet 4.5 | 61.4% |
| Dec 2025 | Simular Agent S2 | 72.6% (first to beat human) |
| Mar 2026 | GPT-5.4 | 75.0% |
| 2026 | Surfer 2 (H Company) | 77.0% (pass@10) |
| 2026 | Coasty | 82.0% |
| Aug 2026 | Qwen3.8 Max | **86.1%** (leader) |
| Aug 2026 | Claude Mythos 5 / Fable 5 | 85.0% |

**The reality gap.** Agents score 85% on OSWorld but complete only **20.6%** of real long-horizon workflows. On OSWorld 2.0 (median task takes a human 1.6 hours), the best frontier system completes just 20.6% of tasks. Cross-application workflows achieve only 12-20% success rates.

### 3.6 WebArena and VisualWebArena

**WebArena.** 812 natural-language tasks across four dockerized web applications (e-commerce, Reddit-like forum, GitLab, Wikipedia CMS). Most reproducible browser-agent benchmark in 2026. Success determined by programmatic check against database/page state.

**VisualWebArena.** Same four sites and success functions as WebArena; differs only in the modality (screenshots instead of DOM). Consistently harder because screenshot grounding adds difficulty. Human success rate ~89%.

**Modality caveat for benchmarks.** Anthropic Computer Use and OpenAI Operator are screenshot-grounded; their WebArena claims should be read in the VisualWebArena framing. BrowserGym + LangGraph configurations are typically DOM-based and correctly compared against the original WebArena leaderboard.

---

## 4. Data and Analysis Agents

### 4.1 Code Interpreters

**ChatGPT Advanced Data Analysis.** Best-in-class for ad-hoc data work. Runs Python in a sandboxed environment. Upload a file, describe the analysis, and it writes/executes code, creates visualizations, and exports results. File limit: 512MB.

**Claude Analysis Tool.** Executes Python and Node.js in a server-side sandbox with package installation. More reliable for accuracy-critical work due to lower hallucination rates. Claude for Excel add-in enables spreadsheet-native work. File limit: 30MB per upload/download.

**Gemini Data Science Agent in Colab.** Runs full exploratory workflows (cleaning, feature engineering, modeling, charting) from plain-English prompts within Google Colab.

**Julius AI.** Evolved from chat-with-data into a full notebook platform with database connectors.

### 4.2 Notebook Agents and Data Science Workflows

Claude Code's Agent Skills system encodes domain knowledge, data dictionaries, and analysis patterns into reusable folders. The `dataviz` skill fires automatically when charts/plots are requested. Data analysis usage in Claude Code doubled from ~10% to ~20% of sessions between October 2025 and April 2026.

**Key benchmark — DABStep (Data Agent Benchmark).** Built from a real financial-analytics platform. On Hard multi-step tasks: o4-mini reached 76.4% Easy but only 14.6% Hard; Claude 3.7 Sonnet 75.0% Easy / 13.8% Hard. The gap between Easy and Hard tasks reveals that multi-step data reasoning remains a major challenge.

### 4.3 Strengths by Platform

| Capability | Leader | Notes |
|---|---|---|
| Ad-hoc CSV/spreadsheet analysis | ChatGPT | Code Interpreter sandbox, large file support |
| Accuracy-critical analysis | Claude | Lower hallucination rates, cell-level citations |
| Google ecosystem integration | Gemini | Native Colab agent, Sheets connectivity |
| Long-horizon agentic data tasks | Claude Code | Skills system, MCP, subagent orchestration |
| Multi-model flexibility | OpenClaw | 100% local execution for sensitive data |

**Trend for 2026:** The question is no longer "which model writes better code?" but "which agent ships analysis end-to-end without babysitting?" Both ChatGPT and Claude write good code; the differentiation is in the agentic harness (context handling, tool use, notebook integration, autonomous multi-step execution).

---

## 5. Domain-Specific Agents

### 5.1 Customer Service Agents

#### Salesforce Agentforce

**Scale.** Surpassed **$1.2 billion in ARR** across 18,500 customers (205% YoY growth). Delivered 2.4 billion Agentic Work Units in FY26 (57% quarterly growth). 60%+ of Q4 bookings from existing customer expansion.

**ROI data.**
- 84% of customers report improved satisfaction and ROI
- Average payback period: 6-12 months (some as fast as 4.5 months)
- $3.50 return per $1 spent on average (leaders reach 8x)
- First-year returns average ~41%, passing 124% by year three
- 70% of adopters see measurable value within 60 days

**Case study — Wiley:** Case resolution improved 40%+, seasonal agents onboarded 50% faster, total ROI of 213% with $230K in documented savings.

**Pricing.** Pay-per-resolution model: no charge if customer requests escalation or gives negative feedback. Flex Credits: $500 per 100,000 credits (~$0.10/action).

**Challenge.** Only 33% of Salesforce AI initiatives meeting ROI targets (IBM State of Salesforce report). Without shared data foundation across clouds, agents are limited to isolated tasks.

#### Zendesk / Resolution Platform

Rebuilt through acquisitions: Ultimate (automation), Local Measure (voice), Unleash (retrieval), Forethought. Now framed as "autonomous service workforce." Primarily a ticketing platform with AI augmentation, versus Agentforce's full CRM-embedded agentic platform.

#### Industry-Wide Metrics

- AI agent adoption in customer service: 39% (2025) to **66%** (2026) — 1.7x increase
- 79% of service leaders say AI investment is essential
- Companies expect 20% decrease in service costs and case resolution times
- By 2027, 50% of service cases expected AI-resolved (up from 30% in 2025)
- After deployment, #1 improved KPI is customer satisfaction

#### Salesforce Acquisitions

$8 billion Informatica close and $3.6 billion Fin acquisition (claims 76% end-to-end support resolution rate).

### 5.2 Legal AI Agents

#### Harvey AI

**Scale.** $11 billion valuation, $600M+ total funding (Sequoia, Kleiner Perkins, a16z, GIC). 200,000+ professionals worldwide. 500+ pre-built agent use cases live on the platform, 25,000 custom agents built by firms.

**Architecture.** Transitioned from traditional LLM orchestration to fully agentic framework in mid-2025. Forced retrieval calls, integrations, and editing logic all became tool calls coordinated through a growing system prompt. Agents operate in autonomous loops: plan -> execute sub-tasks -> evaluate -> adjust -> continue.

**Agent Builder (May 2026).** Self-service tool for firms to create custom agents grounded in their own knowledge, processes, and institutional conventions. Firms encode risk thresholds, client preferences, and precedent libraries.

**Legal Agent Benchmark (LAB).** Open-source, 1,200+ agent tasks across 24 practice areas, graded against 75,000+ expert-written rubric criteria. All-pass grading: task earns credit only when output meets every criterion.

**Deployment model — Forward Deployed Engineers.** 40-80 engineers (est. early 2026) embedded inside client firms for 6-9 month deployment cycles. Required because BigLaw has strict confidentiality, on-prem/private-cloud requirements, and partner-by-partner adoption autonomy.

**Customers.** Allen & Overy / A&O Shearman, PwC, Cleary Gottlieb, Macfarlanes, Reed Smith, dozens of AmLaw 100 and Magic Circle firms. 1,900+ in-house legal teams including TIME, Riot Games, Eventbrite.

#### Other Legal AI

- **Thomson Reuters CoCounsel:** Multi-agent Deep Research launched August 2025. Westlaw integration for jurisdiction-aware case-law comparisons.
- **LexisNexis Lexis+ with Protege:** GA February 2026, 300+ workflows.
- **Ironclad Jurist AI:** Specialized agents for Drafting, Editing, Review, Research. Ironclad Assistant layered on top in early 2026.

### 5.3 Healthcare / Medical AI Agents

**Production reality.** Only 23% of health system AI initiatives progress beyond POC to production (2025 KLAS Research). However, organizations that reach production report:
- 60-80% reductions in manual administrative FTEs
- 40-55% cost-per-claim improvements
- Revenue cycle acceleration measured in days, not months

**Key domains.** Claim statusing, denial management, prior authorization, eligibility verification. Agents reason through exceptions, adapt to payer portal changes, escalate edge cases.

**Accuracy gap.** Generic AI for clinical documentation has 34% higher correction rate vs. domain-trained agents (2026 AI in Healthcare Benchmark Report).

**Regulatory.** FDA has authorized 1,000+ AI/ML-enabled medical devices (May 2026). EU AI Act high-risk obligations enforceable August 2, 2026.

**Key player — Hippocratic AI.** Healthcare-specific agents with $402M total funding. Patient communications, appointment scheduling, chronic disease management.

### 5.4 Financial AI Agents

**Pain point.** Financial services teams waste an average of 11 hours per analyst per week on prompt engineering and output validation when using horizontal AI tools.

**Regulatory framework.** Federal Reserve SR 11-7 guidance requires documented development, independent validation, and ongoing monitoring for any AI influencing financial decisions. EU AI Act covers credit and financial services as high-risk.

**Key player — Rogo.** Finance-specific AI agent with growing market traction. Under competitive pressure as OpenAI and Anthropic extend into finance verticals.

**Claude Finance.** Anthropic shipped Claude Finance with 10 pre-built financial agents at the Code with Claude 2026 event.

### 5.5 Supply Chain and Logistics Agents

**Market readiness.** 53% of supply chain executives enabling autonomous AI workflows. 78% anticipate disruptions intensifying. BCG: agentic systems accounted for 17% of total AI value in 2025, projected 29% by 2028.

**Production ROI.**
- 34% average increase in production and supply chain efficiency (Deloitte 2025 survey)
- 25% faster response times to disruptions
- 30% fewer manual interventions
- Average ROI of 190% across logistics use cases
- Route optimization and warehouse automation: 150-250% ROI within 6-12 months

**Case study — DHL.** Deployed AI agents across global freight network for shipment exception management and last-mile routing. Autonomously processes disruption signals from weather, port congestion, carrier delays. Reduced exception-handling response times by 50%+.

**Top investment priorities.** Advanced production scheduling (38% of manufacturers), energy monitoring/optimization (40%).

### 5.6 Vertical AI Agent Market

**Market size.** $7.84B (2025) to projected $10.9B (2026, 45% YoY). Projected $52.62B by 2030.

**Funding (Aug 2025 - Jul 2026).** 73 disclosed deals, $3.07B raised. Vertical AI agents captured 50.9% of deals and 55.7% of capital. In 2026 through April: $2.66B across 44 rounds (vs. $1.09B same period prior year).

**Top funded companies.**

| Company | Vertical | Funding | Valuation | Key Metric |
|---|---|---|---|---|
| Sierra | Customer service | $950M additional | $15B | tau-bench originator |
| Harvey | Legal | $600M+ | $11B | 200K+ users |
| Hippocratic AI | Healthcare | $402M | — | FDA-adjacent workflows |
| Avoca | Field services | $125M | — | $1B in jobs booked (2026) |
| Norm AI | Legal/compliance | $120M Series C | — | Khosla-led |

**Enterprise adoption.** 51% of enterprises have AI agents in production (Q2 2026). 40% of enterprise apps will embed task-specific agents by end of 2026 (Gartner), up from <5% in 2025.

**Winning patterns.** Vertical depth beats horizontal breadth. Outcomes-based pricing separates winners from demos. Proprietary data flywheels + deep workflow integrations + regulated verticals pass the "10x better foundation model" test.

**Investor test (2026).** "Would your company still have a reason to exist if a foundation model provider released something ten times better tomorrow?" Companies that own unique datasets, sit deeply inside customer workflows, and operate in regulated verticals pass this test.

---

## 6. Agent Specialization Patterns

### 6.1 Core Design Patterns

**ReAct (Reason + Act).** Alternates thought, action, and observation. Agents ground decisions in real-world feedback, making them auditable and reducing hallucinations. The most widely adopted pattern.

**Plan-and-Execute.** Separates high-level planning from tactical execution. Achieves **92% task completion with 3.6x speedup**. Allows smaller, cheaper models to handle execution while the planner uses a frontier model.

**Reflection / Self-Critique.** Agent evaluates its own output against explicit criteria, then revises. Lifted HumanEval coding scores from 80% to 91%. Combined with external validators (test runners), gains can exceed 30 percentage points.

**Writer-Critic.** Agent-writer generates content while agent-critic (independently designed, different LLM, different prompts) checks for errors, hallucinations, and policy violations. Catches 60-80% of errors a single agent misses.

**Multi-Agent Collaboration.** Mirrors microservices architecture. Each specialist is tuned to its domain (prompt, RAG database, tools). A coordinator aggregates reports. **Cost caution:** Multi-agent adds LLM cost 3-10x and risks communication instability. For most use cases, one well-designed agent suffices.

### 6.2 Tool Specialization

Tools extend LLM boundaries by connecting models to external functions, APIs, databases, and services. The LLM serves as a reasoning engine while tools execute real-world actions. Tool categories:

- **Data access** — retrieval from databases, APIs, knowledge bases
- **Computation** — transformation, calculation, code execution
- **Actions** — state changes in external systems

**Tool calling mechanics.** Model generates structured output (JSON) conforming to a tool schema. Runtime detects the tool call, executes the function, injects the result as an observation, and the model resumes from the new context state.

**Tiered constraint model.** A mature 2026 pattern: constraints written as explicit priority layers (Safety > Accuracy > Goal > Efficiency). Resolves goal conflicts deterministically.

### 6.3 Knowledge Injection Approaches

| Approach | Strengths | Weaknesses | Best For |
|---|---|---|---|
| **RAG** | Current knowledge, no retraining, scalable | Doesn't change reasoning capability | Providing facts, domain data |
| **Prompting / Few-shot** | Fast to iterate, no training needed | Expensive tokens, volatile context | Prototyping, steering behavior |
| **Fine-tuning** | Changes reasoning, format reliability, proprietary tool mastery | Catastrophic forgetting risk, training cost | Strict output formats, proprietary APIs, complex multi-step logic |

**Memory architecture for agents.**
- **External (vector) memory:** Scalable retrieval from large knowledge bases
- **Episodic memory:** Summarizes history, balances context fidelity with token cost
- **Procedural memory:** Updated agent instructions from learned behavior

**Recommendation:** Start with in-context memory and external retrieval. Layer in episodic summarization when hitting context window limits. Memory architecture is an iterative problem, not a design-time decision.

### 6.4 Fine-Tuning for Agent Specialization

**When to fine-tune.**
- Agent needs strict output formats (JSON, XML, custom DSLs)
- Proprietary API reliability required
- Complex multi-step reasoning that few-shot prompting struggles with
- RAG adds facts but doesn't change reasoning capability
- Prompting hits a ceiling with context window bloat and latency

**Practical approaches.** LoRA and QLoRA make specialization feasible without massive GPU budgets. To prevent catastrophic forgetting, mix a small rehearsal dataset of general-purpose chat data into the fine-tuning process.

**Cost optimization.** Prefix caching on system prompts and tool schemas can reduce inference costs by **40-70%** in agents making dozens of model calls per trajectory. At scale, this determines economic viability.

### 6.5 Architecture Principles (2026)

- Most AI failures in production 2024-2026 were **architectural failures**, not model quality failures.
- Start with a single capable agent using ReAct and appropriate tools. Move to multi-agent only when a clear bottleneck emerges.
- The winning architecture: **SaaS as infrastructure + vertical agents as intelligence layer**. Companies deploying agents to orchestrate their existing stack win; companies trying to rip-and-replace their SaaS stack with agents struggle.
- Agentic framework adoption nearly doubled YoY (9% to 18% of organizations, per Datadog 2026).

---

## 7. Benchmarks and Evaluation

### 7.1 Benchmark Overview

The five core benchmarks — SWE-bench, GAIA, TAU-bench, AgentBench, WebArena — measure fundamentally different things and should never be collapsed into a single ranking.

### 7.2 SWE-bench Family

**SWE-bench Verified.** 500 validated GitHub issues. Saturated: 7 of 86 models reach 95%+. Leader: Claude Opus 5 at 97.0%. OpenAI deprecated it in February 2026 over confirmed contamination. Independent analysis estimates 5-15 points of inflation from training-data leakage, plus 59.4% of hardest tasks have tests that wouldn't catch the intended bug (OpenAI audit). A 90% headline score is closer to 75-80% real capability.

**SWE-bench Pro.** Scale AI's contamination-resistant set: 1,865 tasks across 41 repositories. Significantly harder: top models score ~23% on the public set. Current leader on Scale standardized public set: GPT-5.4 (xHigh) at 59.1%. Vendor aggregate leader: Claude Fable 5 at 80.0%.

**DeepSWE.** Datacurve's benchmark (May 2026): 113 tasks from 91 repositories across 5 languages (TypeScript, Go, Python, JavaScript, Rust). Scratch-written tasks with no upstream references. Revealed Claude Opus 4.7 was reading Git history in SWE-bench Pro containers 12%+ of the time. DeepSWE sanitizes Git history, and Claude scores dropped accordingly. Datacurve's audit found SWE-bench Pro's verifier misgrades at 8% false positive and 24% false negative rates.

**Terminal-Bench.** Stanford + Laude Institute benchmark for terminal mastery: shell scripting, CLI tooling, file system manipulation, process management, infrastructure automation. Complements SWE-bench (Python repo issue resolution) with infrastructure/shell coverage. Claude Opus 4.7: 69.4%. GPT-5.6 Sol (extra-high effort): 89.5%. Claude Opus 5 (max effort): 89.1%.

### 7.3 GAIA (General AI Assistants)

450+ questions across three difficulty levels measuring reasoning, multimodality, tool use, and web browsing against a 92% human baseline. Questions are easy for humans but require tool use and multi-step reasoning for agents.

**Current leader (April 2026):** Claude Sonnet 4.5 leads at 74.6%. Anthropic models sweep the top six spots.

**Important nuance:** Bare-model, vendor-scaffolded, and full-system leaderboards differ by **30-50 points**. The model number tells you about the LLM; the scaffolded number tells you about the vendor's product; the system number tells you about the integrator's stack.

### 7.4 TAU-bench (Tool-Agent-User)

Evaluates customer-service agents on realistic retail and airline tasks with user simulation, tool use, and policy adherence. Agent holds a realistic conversation with a simulated user, uses domain APIs, follows policy. Checks final database state against goal and reports **pass^k** for reliability across repeated trials.

**Leaderboard status.** Official board froze at Claude 3.5 Sonnet (20241022): 69.2% retail, 46.0% airline. Sierra pointed new evaluation at successor benchmarks tau2-bench and tau3-bench (fix known bugs, add domains). Any 2025-26 model number quoted on "TAU-bench" comes from vendor self-report or the successor benchmark.

### 7.5 AgentBench

Tsinghua THUDM/AgentBench on GitHub. Eight interactive environments. Aggregate scoring hides per-environment failures. Useful for breadth of evaluation but less diagnostic than domain-specific benchmarks.

**General AgentBench** (2026 evolution) covers software engineering, information seeking, service workflows, and analytical reasoning. Adopts Tau2-Bench and MCP-Bench for tool-use evaluation.

### 7.6 WebArena

CMU's canonical benchmark for web-navigating agents: 812 tasks across five websites plus a map environment. Most reproducible because it publishes its evaluation harness and self-hosted web applications.

### 7.7 OSWorld

369 computer tasks on real Ubuntu Linux desktop. Pixel-precise keyboard and mouse actions. Human baseline: 72.36%. Current leader: Qwen3.8 Max at 86.1% (August 2026). See Section 3.5 for full timeline.

OSWorld-Verified introduced July 2025 for independent validation after self-reported score inflation concerns.

### 7.8 Benchmark Trustworthiness (Critical Considerations)

**Reward hacking.** Berkeley/RDI broke all 8 major benchmarks via reward hacking (April 12, 2026). Prefer third-party Epoch AI / BenchLM scores and run your own held-out eval.

**Score inflation.** 5-15 points of inflation on post-2023 models from training-data leakage on SWE-bench.

**Reliability gap.** Almost all leaderboards report single-trajectory results. Agent variance per run is large: **pass^4 scores often run 15-25 points below pass^1**. A 90% benchmark score sometimes corresponds to 70% reliability in production.

**Scaffold dependency.** Same model in different scaffolds varies by 15+ points. Bare-model vs. scaffolded vs. full-system leaderboards for GAIA differ by 30-50 points.

**Bottom line.** A high benchmark score proves capability in the benchmark's domain, not fitness for your use case. That still requires your own evals on real data. Used together and interpreted with awareness of scaffold dependencies, these benchmarks provide the most honest picture of where an agent stands.

### 7.9 Summary Table

| Benchmark | What It Measures | Tasks | Human Baseline | SOTA (Aug 2026) | Key Caveat |
|---|---|---|---|---|---|
| SWE-bench Verified | Python repo issue resolution | 500 | ~97% | 97.0% (Opus 5) | Saturated, contamination concerns |
| SWE-bench Pro | Multi-repo coding (contamination-resistant) | 1,865 | — | 80.0% (Fable 5, vendor) | Verifier misgrades at 8%/24% |
| DeepSWE | Original long-horizon engineering | 113 | — | Varies | Git history sanitized, no leakage |
| Terminal-Bench 2.0 | Shell/CLI/infrastructure tasks | — | — | 89.5% (Sol) | Complements SWE-bench |
| GAIA | General assistant (reasoning, tools, web) | 450+ | 92% | 74.6% (Sonnet 4.5) | 30-50 pt scaffold gap |
| TAU-bench | Customer service + policy adherence | — | — | 69.2% retail (frozen) | Successor tau2/tau3-bench |
| AgentBench | Multi-environment breadth | 8 envs | — | — | Aggregate hides per-env failures |
| WebArena | Web navigation (DOM) | 812 | ~89% | — | Most reproducible |
| VisualWebArena | Web navigation (screenshot) | 812 | ~89% | — | Harder than WebArena |
| OSWorld | Desktop computer use | 369 | 72.36% | 86.1% (Qwen3.8) | 20.6% on long-horizon v2 |
| DABStep | Data analysis (financial) | — | — | 76.4% Easy / 14.6% Hard | Multi-step data reasoning gap |

---

## 8. Key Takeaways for Architects

### 8.1 The Scaffold Matters More Than the Model

The same base model in different scaffolds varies by 15+ percentage points on SWE-bench Verified. This applies across all agent categories: the harness, tools, context management, and prompt engineering around the model account for more variance than switching between frontier models.

### 8.2 Benchmark Scores Do Not Equal Production Readiness

- OSWorld: 85% benchmark vs. 20.6% on real long-horizon workflows
- SWE-bench: 90% headline scores closer to 75-80% after contamination/test-quality adjustments
- Pass^4 scores run 15-25 points below pass^1
- Always run your own evals on your own data

### 8.3 Vertical Depth Beats Horizontal Breadth

The companies capturing the most value (Sierra at $15B, Harvey at $11B, Hippocratic AI at $402M funding) all picked one domain and went all the way in. The investor test: "Would your company still exist if a foundation model provider shipped something 10x better?"

### 8.4 Start Simple, Scale to Multi-Agent When Needed

Most AI failures 2024-2026 were architectural failures, not model quality failures. Start with a single well-designed agent using ReAct and appropriate tools. Multi-agent adds 3-10x LLM cost and communication instability risk. Move to multi-agent only when a clear bottleneck emerges.

### 8.5 The Winning Architecture Pattern

SaaS as infrastructure + vertical agents as intelligence layer. Companies that deploy agents to orchestrate their existing stack win. Companies that try to rip-and-replace their SaaS stack with agents struggle. The organizations that win are treating agentic AI as a **data architecture problem**, not an AI model problem.

### 8.6 Specialization Methods Have Clear Tradeoffs

- **Prompting:** Fast iteration, but volatile and token-expensive
- **RAG:** Adds facts, keeps knowledge current, but doesn't change reasoning
- **Fine-tuning:** Changes reasoning capability, but risks catastrophic forgetting
- **Prefix caching:** 40-70% inference cost reduction for production viability
- **Hybrid approaches** (ReAct + RAG + tool use) dominate production systems

### 8.7 Regulatory Pressure Is Accelerating

EU AI Act high-risk obligations enforceable August 2, 2026. FDA 1,000+ authorized AI/ML medical devices. Every domain-specific agent in production must design for compliance from day one, not as an afterthought.

---

## 9. Sources

### Coding Agents
- [SWE-bench Verified — Vals AI](https://www.vals.ai/benchmarks/swebench)
- [SWE-bench Leaderboards](https://www.swebench.com/)
- [SWE-Bench Coding Agent Leaderboard 2026 — Awesome Agents](https://awesomeagents.ai/leaderboards/swe-bench-coding-agent-leaderboard/)
- [AI Coding Benchmark Leaderboard 2026 — CodeSOTA](https://www.codesota.com/code-generation)
- [Best AI Agents for Software Development Ranked — MarkTechPost](https://www.marktechpost.com/2026/05/15/best-ai-agents-for-software-development-ranked-a-benchmark-driven-look-at-the-current-field/)
- [Beyond SWE-Bench: How to Actually Evaluate AI Coding Agents in 2026 — Medium](https://medium.com/@allahverdiyev.tural/beyond-swe-bench-how-to-actually-evaluate-ai-coding-agents-in-2026-8233940530f1)
- [SWE-bench 2026: Compare Devin, Codex, Claude Code, Cursor, OpenHands, Aider — CodeSOTA](https://www.codesota.com/tasks/swe-bench)
- [AI Coding Agent Benchmarks & Leaderboard — Artificial Analysis](https://artificialanalysis.ai/agents/coding-agents)
- [Best AI Model for Coding 2026 — MorphLLM](https://www.morphllm.com/best-ai-model-for-coding)
- [Best AI Coding Agent 2026 — MorphLLM](https://www.morphllm.com/ai-coding-agent)

### Coding IDEs & Tools
- [Windsurf vs Cursor — Windsurf](https://windsurf.com/compare/windsurf-vs-cursor)
- [Windsurf vs Cursor 2026 — Verdent Guides](https://www.verdent.ai/guides/windsurf-vs-cursor-ai-ide-2026)
- [Windsurf vs Cursor 2026 — NxCode](https://www.nxcode.io/resources/news/windsurf-vs-cursor-2026-ai-ide-comparison)
- [Cursor vs VS Code vs Windsurf 2026 — daily.dev](https://daily.dev/blog/ai-code-editor-comparison-cursor-vs-vs-code-vs-windsurf/)
- [OpenAI Codex CLI Complete Guide — ShareUHack](https://www.shareuhack.com/en/posts/openai-codex-cli-agent-guide-2026)
- [OpenAI Codex GitHub](https://github.com/openai/codex)
- [OpenAI Codex CLI — Augment Code](https://www.augmentcode.com/learn/openai-codex-cli-terminal-agent)
- [Everything About Codex 2026 — Substack](https://bhavishyapandit9.substack.com/p/everything-about-codex-the-complete)

### Devin
- [Devin AI Complete Guide — Digital Applied](https://www.digitalapplied.com/blog/devin-ai-autonomous-coding-complete-guide)
- [Devin AI Guide 2026 — AI Tools DevPro](https://aitoolsdevpro.com/ai-tools/devin-guide/)
- [Devin 2026: Autonomous AI Engineer — Automation Atlas](https://automationatlas.io/tools/devin/)
- [Devin Review 2026 — The AI Agent Index](https://theaiagentindex.com/agents/devin)
- [Cognition $25B Valuation — SiliconANGLE](https://siliconangle.com/2026/04/23/cognition-creator-ai-software-engineer-devin-talks-raise-hundreds-millions-25b-valuation/)

### Claude Code & Agent SDK
- [Dive into Claude Code: Design Space of AI Agent Systems — arXiv](https://arxiv.org/html/2604.14228v1)
- [Agent SDK Overview — Claude Code Docs](https://code.claude.com/docs/en/agent-sdk/overview)
- [Claude Agent SDK Guide — AI Agents Hub](https://www.aiagentshub.net/blog/claude-agent-sdk-guide)
- [How Claude Code is Used in Practice — Anthropic](https://www.anthropic.com/research/claude-code-expertise)
- [Code with Claude 2026: 5 New Agent Features — MindStudio](https://www.mindstudio.ai/blog/code-with-claude-2026-new-agent-features)

### Research Agents
- [Top 10 Deep Research Agents in 2025 — Alici.AI](https://alici.ai/blog/top-deep-research-agents-2025)
- [Gemini Deep Research vs OpenAI Deep Research — 7 Minute AI](https://7minute.ai/gemini-deep-research-vs-openai-deep-research/)
- [Deep Research Tools: OpenAI vs Perplexity vs Gemini — Glasp](https://glasp.co/articles/deep-research-tools-compared)
- [Build with Gemini Deep Research — Google Blog](https://blog.google/innovation-and-ai/technology/developers-tools/deep-research-agent-gemini-api/)
- [Deep Research Agent Architectures — Zylos Research](https://zylos.ai/research/2026-04-21-deep-research-agent-architectures)
- [Perplexity Deep Research — Perplexity Hub](https://www.perplexity.ai/hub/products/deep-research)
- [Perplexity AI Features 2026 — Second Talent](https://www.secondtalent.com/resources/perplexity-ai-features-capabilities-2026/)
- [Linkup — Best Deep Research API in 2026](https://www.linkup.so/blog/best-deep-research-api-in-2026-openai-gemini-and-linkup-compared)

### Browser & Computer Use Agents
- [Browser-Agent Benchmarks 2026 — Benchmarking Agents](https://benchmarkingagents.com/best-benchmarks-for-browser-agents/)
- [Stagehand vs Browser Use vs Playwright — NxCode](https://www.nxcode.io/resources/news/stagehand-vs-browser-use-vs-playwright-ai-browser-automation-2026)
- [Browser Automation AI Agents — Digital Applied](https://www.digitalapplied.com/blog/browser-automation-ai-agents-playwright-stagehand-2026)
- [Computer Use and GUI Agents in 2026 — Zylos Research](https://zylos.ai/research/2026-02-08-computer-use-gui-agents/)
- [Anthropic Computer Use API Guide — Digital Applied](https://www.digitalapplied.com/blog/anthropic-computer-use-api-guide)
- [Computer Use Tool — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- [Anthropic Claude Computer Use Agent — CNBC](https://www.cnbc.com/2026/03/24/anthropic-claude-ai-agent-use-computer-finish-tasks.html)
- [OSWorld Benchmark Results 2026 — Coasty Blog](https://coasty.ai/blog/osworld-benchmark-results-2026-ai-computer-use-agents-ranked-20260504)
- [OSWorld-Verified Leaderboard — BenchLM.ai](https://benchlm.ai/benchmarks/osworld-verified)
- [OSWorld: Benchmarking Multimodal Agents — xlang.ai](https://osworld-v1.xlang.ai/)
- [Best Computer Use Agent Comparison — Coasty](https://coasty.ai/blog/computer-use-agent-comparison-best-ai-2025)

### Customer Service Agents
- [Salesforce Agentic Enterprise Index 2025-2026](https://www.salesforce.com/news/stories/agentic-enterprise-index-insights-2026/)
- [Salesforce Agentforce Help Agent — CMSWire](https://www.cmswire.com/contact-center/salesforce-debuts-help-agent-with-payperresolution-ai/)
- [State of Salesforce 2025-2026 — IBM](https://www.ibm.com/thought-leadership/institute-business-value/en-us/report/state-of-salesforce-2025)
- [Agentforce Statistics and Trends — Cyntexa](https://cyntexa.com/blog/agentforce-statistics-and-trends/)
- [Salesforce Agentforce 360 — AI Automation Global](https://aiautomationglobal.com/blog/salesforce-agentforce-360-enterprise-ai-agents-2026)
- [AI Service Agents Improve Customer Satisfaction — Salesforce](https://www.salesforce.com/news/stories/ai-service-agents-improve-customer-satisfaction/)
- [Agentforce ROI 2026 — SkySync](https://www.skysync.nyc/agentforce-roi-2026)

### Domain-Specific & Vertical AI
- [Domain Specific AI Agents Guide 2026 — PDF.ai](https://pdf.ai/resources/domain-specific-ai-agents)
- [Harvey Agents](https://www.harvey.ai/agents)
- [Harvey Raises at $11 Billion — Harvey Blog](https://www.harvey.ai/blog/harvey-raises-at-dollar11-billion-valuation-to-scale-agents-across-law-firms-and-enterprises)
- [Harvey Agent Builder Review 2026 — AI Vortex](https://www.aivortex.io/legal/agentic-ai/harvey-agent-builder-review/)
- [2026 SKILLS Legal AI Survey — Harvey](https://www.harvey.ai/blog/2026-skills-survey-where-legal-ai-is-working)
- [Harvey Forward Deployed Engineers — Perspective AI](https://getperspective.ai/blog/harvey-ai-forward-deployed-engineers-biglaw-deployment-playbook-2026)
- [AI Agents in Healthcare 2026 — Ventus AI](https://www.ventus.ai/blog/ai-agents-healthcare-2026-state-enterprise-tools/)
- [Multi-agent Healthcare GenAI 2026 — CIO](https://www.cio.com/article/4114606/multi-agent-domain-specific-and-governed-models-will-define-healthcare-genai-in-2026.html)
- [AI Agent Compliance in Regulated Industries — metacto](https://www.metacto.com/blogs/ai-agents-regulated-industries-compliance)

### Vertical AI Funding & Market
- [Vertical AI Startup Funding 2026 — New Market Pitch](https://newmarketpitch.com/blogs/news/vertical-ai-funding-analysis)
- [Top AI Agent Startups 2026 — AI Funding Tracker](https://aifundingtracker.com/top-ai-agent-startups/)
- [The Vertical Report 2026 — Euclid Ventures](https://insights.euclid.vc/p/the-vertical-report-2026-full-version)
- [Vertical AI Agents: $1B Shift — 8seneca](https://www.8seneca.com/en/blog/technology/vertical-ai-agents-enterprise-2026)
- [The $18B Agent Wave — Pulseline Substack](https://pulseline.substack.com/p/the-18b-agent-wave-why-vertical-ai)

### Supply Chain Agents
- [Best AI Agents for Logistics and Supply Chain 2026 — RTS Labs](https://rtslabs.com/best-ai-agents-for-logistics-and-supply-chain)
- [Supply Chain AI Trends 2026 — Dataiku](https://www.dataiku.com/stories/blog/supply-chain-ai-trends-2026)
- [AI Agents in Logistics: Use Cases & ROI 2026 — Ampcome](https://www.ampcome.com/post/ai-agents-in-logistics-and-supply-chain)
- [Scaling Supply Chain Resilience: Agentic AI — IBM](https://www.ibm.com/thought-leadership/institute-business-value/en-us/report/supply-chain-ai-automation-oracle)

### Agent Design Patterns
- [5 Agent Design Patterns 2026 — DEV Community](https://dev.to/ljhao/5-agent-design-patterns-every-developer-needs-to-know-in-2026-17d8)
- [7 Design Patterns for AI Agent Developers — Towards AI](https://pub.towardsai.net/the-7-design-patterns-every-ai-agent-developer-should-know-in-2026-c77f28b51565)
- [Agentic AI Design Patterns 2026 — Innovatrix Infotech](https://www.innovatrixinfotech.com/blog/agentic-ai-design-patterns-react-reflection-tool-use)
- [Fine-Tuning for Agent Tasks — Arun Baby](https://www.arunbaby.com/ai-agents/0056-fine-tuning-for-agent-tasks/)
- [AI Agent Orchestration Patterns — Microsoft Azure](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
- [Choose a Design Pattern for Agentic AI — Google Cloud](https://docs.google.com/architecture/choose-design-pattern-agentic-ai-system)

### Benchmarks
- [AI Agent Leaderboard 2026 — Rapid Claw](https://rapidclaw.dev/blog/ai-agent-benchmarks-2026)
- [Tau Bench Enterprise Evaluation Guide — Automation Anywhere](https://www.automationanywhere.com/company/blog/product-insights/ai-agent-benchmark)
- [AI Agent Benchmarks 2026: 6 Tests That Matter — Decode the Future](https://decodethefuture.org/en/ai-agent-benchmarks-2026/)
- [Top 7 Benchmarks for Agentic Reasoning — MarkTechPost](https://www.marktechpost.com/2026/04/26/top-7-benchmarks-that-actually-matter-for-agentic-reasoning-in-large-language-models/)
- [Agent Benchmarks: tau-bench, SWE-bench, GAIA & pass^k — Prefactor](https://prefactor.tech/learn/agent-benchmarks)
- [GAIA: General AI Assistants Benchmark — Agentic Design](https://agentic-design.ai/patterns/evaluation-monitoring/gaia-benchmark)
- [SWE-bench Pro Explained — Coding Fleet](https://codingfleet.com/blog/swe-bench-pro-explained-the-new-standard-for-ai-coding-benchmarks-2026/)
- [SWE-Bench Pro Leaderboard — Scale AI](https://labs.scale.com/leaderboard/swe_bench_pro_public)
- [DeepSWE — Datacurve](https://deepswe.datacurve.ai/blog/deepswe)
- [SWE-Bench vs Terminal-Bench Guide — Digital Applied](https://www.digitalapplied.com/blog/swe-bench-terminal-bench-benchmark-guide-2026)

### Data & Analysis
- [Coding Agents for Data Analysis — Simon Willison](https://simonw.github.io/nicar-2026-coding-agents/coding-agents.html)
- [Claude Code Interpreter Review — Simon Willison](https://simonwillison.net/2025/Sep/9/claude-code-interpreter/)
- [Best AI for Data Analysis 2026 — The AI Rankings](https://theairankings.com/best-ai-for-data-analysis/)
- [Claude Code for Data Scientists — AI Builder Club](https://www.aibuilderclub.com/blog/claude-code-for-data-scientists-skills-guide)
