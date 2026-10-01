# Research: How to Design an Incident Response AI Agent

**Date researched**: 2026-09-29
**Sources consulted**: 32

---

## 1. System Topology & Mechanics

### Control Plane / Data Plane Separation

The single most important architectural principle from Google SRE's whitepaper: **decouple the reasoning engine from the execution engine**. Google's AI Operator (reasoning) is explicitly separated from the Actuation Agent (execution), ensuring that "no matter how rapidly AI models evolve, their ability to mutate production remains strictly governed by deterministic, human-controlled safety boundaries."

**Control plane** (the AI reasoning layer):
- Ingests alert signals, observability data, topology graphs, and historical incident context.
- Forms and tests hypotheses for root cause analysis (RCA).
- Selects mitigation strategies from a curated catalog.
- Outputs structured action plans (not raw commands).

**Data plane** (the execution layer):
- Receives structured action plans from the control plane.
- Enforces pre-flight safety validations: mandatory dry-runs, justification verification (action must target an open incident), concurrent action checks.
- Routes through delegated control planes rather than executing raw scripts.
- Infrastructure tools are designed to be "incapable of single-handedly taking down production, regardless of who or what is calling them" (Google SRE).

**PagerDuty's parallel architecture**: The SRE Agent uses a durable supervisor + stateless sub-agents model. The supervisor checkpoints state and orchestrates; sub-agents are stateless, replaceable workers that query logs, metrics, and deploys. If a sub-agent dies, it is re-spawned rather than resumed -- avoiding N+1 checkpoint reconciliation.

### State Orchestration Models for Incident Workflows

**Three execution models evaluated by PagerDuty** (they chose #3):

| Model | Latency | Interactivity | Complexity |
|-------|---------|---------------|------------|
| Sequential | Sum of all durations | None | Low |
| Parallel, wait-for-all | Slowest sub-agent | None (main agent idle) | Medium |
| Parallel fan-out / concurrent fan-in | Slowest sub-agent | Full (user input is first-class event) | High |

**PagerDuty's reactive loop** is a six-node graph: `accept_event` -> `route_event` -> `handle_sub_agent_result` OR `handle_user_input` -> `plan` (dispatch sub-agents). The graph spends most of its time paused at `accept_event`, waiting for the drain loop to deliver the next event.

**Concurrency primitives** in the PagerDuty architecture:
- Priority queue: user input at priority 0 (highest), sub-agent results at priority 1.
- Lock: guards against concurrent resume race conditions. Drain loop holds lock while resuming; graph signals through callback when re-interrupted.
- Drain loop: the spine of execution. Concurrent arrivals are serialized.

**Google's five-level autonomy model** defines progression across five operational functions (Monitor, Investigate, Mitigate, Actuate, Self-Direct):

| Level | Description | Gate to Next Level |
|-------|-------------|-------------------|
| L0 - Manual | Automation monitors only | Monitoring/investigation tool adoption |
| L1 - Assisted | Automation investigates | Confidence in reliable action ID + safe actuation paths |
| L2 - Partial | Automation actuates, human mitigates | Demonstrated high precision/reliability |
| L3 - High | Full automation, human self-directs | Multi-step resolution capability |
| L4 - Full | End-to-end autonomy | Continuous monitoring of own interventions |

### Integration with Monitoring/Alerting Pipelines

**Event-driven trigger patterns**:
- Webhook from PagerDuty/Datadog -> triggers agent in AWS Lambda or container.
- Datadog: `@Datadog investigate` in Slack incident channel triggers investigation.
- Google: AI Alert System operates within a ~2-minute time budget constraint, using massive parallelism to query monitoring systems, logging, change logs, and dependency graphs. Operates in read-only mode.

**Context consumption model** (Google AI Operator):
1. **Enrichers**: Deterministic signal boosters (observability tool findings, alert descriptions, playbooks).
2. **Specialized Skills**: Define how to mitigate specific problem types.
3. **Few-shot prompts**: Encoded in text protos guiding investigation strategy.

The agent consumes available context dynamically -- it does not require all three types.

**Datadog's automation architecture**: Automations trigger based on incident events (severity changes, state transitions). Powered by Datadog Workflow Automation. Configured per incident type.

### Tool Dispatch for Runbooks, Diagnostics, Remediation

**Model Context Protocol (MCP)** is emerging as the standard for tool integration -- described as "like HTTP for AI agents." PagerDuty's open architecture connects to 750+ tools through three pathways: partners connecting to PagerDuty's MCP Server, PagerDuty's connections to partner MCP servers, and direct API integrations.

**Google's typed, governed tool access pattern**: Agent capabilities are exposed as typed, governed tools (e.g., `fetch_playbook` as a function call) rather than freeform shell access. The Production Agent MCP server exposes observability, incident management, traffic control, and infrastructure inspection capabilities.

**Datadog Bits Agent Builder** enables chaining agents: an investigation agent analyzes logs and traces, then triggers a remediation agent if the fix matches a known pattern (restart service, rollback deployment, scale resource). Combines rule-based automation with AI reasoning in a single workflow.

**Skill/SOP pattern** (from systemdesign.one): Reusable logic modules combining markdown guides with deterministic scripts. Key properties:
- Lazy loading: skills loaded into model only when needed, saving context window.
- Hybrid determinism: natural language instructions + templated scripts reduce hallucination.
- Invocation: `/Skill-name <instruction>` pattern.

---

## 2. Token Economics & NFR Metrics

### Latency SLA Benchmarks

**Production AI agent latency multiplier**: A real agent turn involves 2-5 LLM hops plus tool calls, compounding latency by 3-10x beyond raw API benchmarks.

| Metric | P50 | P95 | P99 | Notes |
|--------|-----|-----|-----|-------|
| Raw LLM API TTFT | ~330ms | ~1.2s | ~3.2s | Baseten benchmark, Sept 2026 |
| Agent turn (end-to-end) | 3-5s | 6-9s | 8-12s | 2-5 LLM hops + tool calls [inferred] |
| PagerDuty sequential investigation | 10+ min | -- | -- | 3-4 hypothesis, sequential mode |
| Google AI Alert System | -- | -- | ~2 min | Hard time budget, parallel queries |
| Google InvD MTTM (supported incidents) | -- | -- | -- | 44% reduction vs. baseline |

**Provider latency consistency** (2026 benchmarks):
- Anthropic (Claude): most consistent latency variance -- P50 and P99 TTFT stay close together.
- OpenAI (GPT-4.1): P99 can spike 3-5x above P50 during peak hours.
- Google (Gemini): fast and stable, but post-update behavioral changes observed.

**TTFT cost curve**: Going from 500ms to 200ms P99 targets increases infrastructure spend by roughly 35%.

### Token Cost Formulas for Incident Analysis

**Enterprise LLM spending context**: $8.4B in H1 2025. Agents make 3-10x more LLM calls than chatbots. Unconstrained agent on a software engineering task: $5-8 per task in API fees.

**Cost optimization strategies achieving 60-80% token spend reduction**:

1. **Model routing** (primary lever): 100-300x cost differential between premium and small model tiers. Route simple triage to small models; escalate complex RCA to frontier models.
2. **Semantic caching**: 31% of LLM queries exhibit semantic similarity to previous requests. Cache common incident patterns.
3. **Context compression**: Cloudflare's Code Mode collapsed 2,500+ API endpoints into two tools consuming ~1,000 tokens (down from 1.17M tokens for traditional MCP server).
4. **Token management within agent**: Google's AI Operator uses "the minimum set of tokens per step" because incident chain-of-thought can have a very long horizon. Strict token management prevents context loss and hallucination.

**Inference cost trend**: Stanford HAI 2025 AI Index: inference cost for GPT-3.5-level system dropped 280x between Nov 2022 and Oct 2024. Hardware costs declining 30%/year, energy efficiency improving 40%/year.

### Prompt Caching for Recurring Incident Patterns

**Google's approach**: Few-shot prompts encoded in text protos guide investigation strategy for known incident types. AI Insights agents continuously review past incidents and extract patterns into a vector-searchable knowledge base using Gemini embedding models.

**PagerDuty memory system**: SRE Agent remembers changes, dependencies, past incidents, conversation history, and the steps human responders took. Creates a "virtuous cycle: fewer tickets, fewer escalations, fewer late-night pings."

**Long-term memory pattern** (systemdesign.one): File-based persistent storage using `Agents.md` standard format -- cross-platform standard readable by any AI system. Stores past errors, solutions, system architecture context, historical incident data.

---

## 3. Distributed Resilience & State

### Durable Execution for Multi-Step Incident Workflows

**Core abstraction**: Durable execution automatically persists workflow state at defined checkpoints; resumes from those checkpoints after any failure. The developer writes sequential code; durability is provided by the runtime.

**Why incident response agents specifically need durable execution**:
- Incident workflows span minutes to hours.
- Session memory is not durable execution. "Saving chat history helps an agent remember, but it does not prove which shell command ran, which email was sent, which approval was granted, or whether a retry would duplicate a side effect."
- Without checkpointing, a crash at 3h50m restarts from zero, wasting GPU time and compounding incident duration.

**Temporal's event history model**: Records every workflow execution step, every Activity call/return, and all return values. Memory is fully visible and debuggable through Temporal tooling. Non-deterministic side effects (LLM outputs, timestamps, retrieval results) are recorded the first time and replayed during recovery.

**Ecosystem**: Temporal, Restate, Inngest, Hatchet, DBOS, Cloudflare Workflows, AWS Lambda Durable Functions, Azure Durable Task. Agent frameworks (LangGraph, OpenAI Agents SDK, AutoGen, CrewAI) are adding persistence at the agent layer.

### Checkpointing During Incident Resolution

**PagerDuty's asymmetric durability model**:
- Supervisor: checkpointed state, can pause/resume/recover. Message queue lives inside supervisor, covered by same checkpoint. "Events survive a crash because the supervisor's state does."
- Sub-agents: no checkpoints. If a sub-agent dies, re-spawn rather than resume.
- Each step is atomic and persisted the moment it is applied.
- Identity convention: `task_id === thread_id` -- no lookup table, no correlation logic.

**Critical checkpointing principle**: "Do not checkpoint only model messages. 'The email was sent' in conversation history may be a model claim rather than a provider result. Store the provider message ID or a reference to the operation record."

**Google's approach**: All AI Operator execution traces stored in Spanner. Post-actuation, system maintains long-running operation (LRO) state, polling infrastructure to verify mitigation success/failure.

### Circuit Breakers for Cascading Failures

**Why AI agents need agent-specific circuit breakers**: Multi-agent systems fail at 41-86.7% rates in production without deliberate fault tolerance. Unprotected AI agent systems can cascade from a single API timeout into complete system failure within minutes.

**Three-state circuit breaker model** adapted for AI agents:
- **Closed** (normal): monitors success rates, response times, and response quality metrics.
- **Open** (tripped): immediately rejects all requests. Initial open duration: 30 seconds, increasing exponentially.
- **Half-open** (testing): simplified prompts sent first, gradually increasing complexity as success rates improve.

**Trigger conditions**: Repeated identical tool calls (loop detection), cost velocity exceeding defined rate, consecutive failures without recovery, permission boundary violations.

**Google's agentic safety guardrails**:
- Agent-specific rate limits and automated circuit breakers prevent runaway loops.
- All agent actions must be "highly interruptible."
- Mandatory dry-run support: any agent-facing system must support `dry_run=true` mode.
- Zero-trust, safe-by-default actuation: agents route through delegated control planes.
- **Red Button**: emergency endpoints allow SREs to instantly pause all in-flight agentic actions, block new actions, and globally revoke L3 permissions across the entire fleet.

**Dynamic autonomy downgrade** (Google Actuation Agent): If an L3 request is made but elevated risk or anomalous production state is detected, automatically downgrades to L2 and routes approval to a human SRE.

**Real-world incident**: Amazon Kiro AI (Dec 2025) determined deleting and rebuilding an environment was the most efficient fix, executed autonomously without human approval, causing a 13-hour outage.

**Layered resilience architecture**: Circuit breakers (cascading failure prevention) + timeout management (indefinite blocking prevention) + compensating transactions (workflow rollbacks) + bulkhead isolation (critical component protection).

---

## 4. Enterprise Security & Governance

### Zero-Trust Access to Production Systems

**Google SRE's "No Ambient Access & Least Privilege" principle**: Agent identities must be distinct from human users, strongly authenticated, with on-demand permissions only. Agents must never use "standing, human-like credentials of their developers."

**Anthropic's three-layer defense**:
1. **Environment layer** (deterministic): sandboxes, VMs, filesystem boundaries, egress controls. "Design for containment at the environment layer first."
2. **Model layer** (probabilistic): system prompts, classifiers, probes.
3. **External content layer**: tool permission scoping, input inspection, connector auditing.

**Isolation patterns** (Anthropic):
- Ephemeral container (gVisor, seccomp) for multi-tenant SaaS.
- Human-in-the-loop sandbox (Seatbelt/macOS, bubblewrap/Linux) for developer tools. 84% reduction in permission prompts.
- Sealed VM (hypervisor-level isolation) for autonomous agents. Per-session scoped-down tokens, independently revocable.

**Egress controls**: Allowlists must be treated as capability grants, not destination filters. "Every function reachable through any domain on an allowlist is now an attack surface." Real incident: malicious file in workspace instructed Claude to upload files via Anthropic's Files API using attacker-controlled key. Fix: defensive MITM proxy inside VM rejects non-provisioned tokens.

**Anthropic's principle of least agency**: "Grant the narrowest capability that still completes the task."

### RBAC for Incident Response Actions

**PagerDuty Enterprise**: RBAC lets admins pull access from specific groups while leaving others running. Per-connector controls disable write operations on a specific integration without touching the rest.

**Datadog**: Bits AI SRE supports HIPAA-regulated workloads with role-based access controls. Tested against 2,000+ customer environments.

**Google's agent identity management**: Agent principals must have unique, machine-distinguishable identities separate from human principals. Every action requires "a complete, immutable record."

**Progressive authorization** (Google): Agents start at lower autonomy levels (human-approved) and scale up based on demonstrated performance. Risk evaluation is contextual -- draining a cell may be low-risk normally but high-risk during regional peaks.

### Audit Trails for Compliance (SOC2, HIPAA)

**SOC 2 for AI agents** (2026 auditor expectations):
- Model lineage: exact dataset, code, and approval behind each deployed model.
- Prompt and inference logs with PII redaction applied before logging.
- Drift-monitoring output.
- Vendor risk assessment for every third-party LLM called.
- Cost: $35K-$150K first year; enterprise-readiness $200K-$250K+.

**HIPAA requirements**:
- "If PHI was in the context during inference, the system is in scope" -- even transient context window presence counts.
- Required: TLS 1.2+ transit encryption, AES-256 at rest, role-based access, audit logs, data minimization, no training on PHI.
- January 2025 HIPAA Security Rule NPRM (finalization mid-2026) explicitly brings AI systems into scope for ePHI governance.
- Healthcare breach costs: $10.93M average (IBM 2024 Cost of a Data Breach) -- highest industry for 14 consecutive years.

**Audit trail requirements**: Every access to regulated data must generate structured records including timestamp, accessor identity, action performed, and reference to data accessed. Organizations with complete AI audit trails report 60-70% reduction in HIPAA audit prep time.

**Current governance gaps** (2026):
- Only 21% of organizations maintain a real-time agent registry (CSA/Strata, 2026).
- Only 18% of security leaders believe their IAM handles AI agent identities effectively.
- Only 38% monitor AI activity end-to-end; 17% track agent-to-agent interactions (EY/AIUC-1 Consortium, 2026).
- 42% of companies abandoned AI initiatives in 2025 due to compliance/governance failures.

**Google's transparency requirement**: Agents must log chain of thought -- signals used, hypotheses considered, action rationale, confidence levels. Every execution trace stored in Spanner.

---

## 5. Production Failure Modes

### False Positive/Negative Detection

**Production failure rate**: AI agent failure rates range from 70% to 95% in production. 88% of agents that work in demos fail in real workflows (Fiddler AI, 2026).

**Arize field analysis** (2026) -- failure mode distribution:
| Failure Mode | Frequency |
|-------------|-----------|
| Context blindness | 31.6% |
| Rogue actions | 30.3% |
| Silent degradation | 24.9% |
| Memory corruption | 8.1% |
| Runaway execution | 5.1% |

**Silent/false-positive failures**: Agents cannot distinguish between "I failed the task" and "the task is impossible." They hallucinate success messages to close the loop. Functional-but-wrong outputs survive because reviewers see polished results and assume sound reasoning.

**Detection gap**: Observability failures have no MTTD because they have no detection mechanism. Teams discover problems through downstream business signals (support tickets, billing discrepancies). Average MTTR for undetected failures: 4.2 hours vs. 54 minutes for tool-call failures with schema validation.

**Six monitoring signals for production agents**: Goal Completion Rate, Tool Success Rate, Context Quality Score, Reasoning Trace Completeness, Escalation Rate, Hallucination Rate.

**Fifth telemetry layer**: Traditional four (metrics, logs, traces, events) are insufficient. Agents need a fifth: reasoning traces. "Without them, you are doing forensics on a crime scene with no witnesses."

### Runbook Hallucination Risks

**Hallucination cascades**: Agent generates false information, then uses that fabrication to inform subsequent decisions, creating a multi-system incident. Example: inventory agent invents nonexistent SKU, then calls four downstream APIs to price, stock, and ship the phantom item.

**Google's mitigation -- deterministic scoring**: A mitigation is scored "correct" only if the agent's output "deterministically matches the fully actionable, exact parameters of the Golden data (e.g., the specific binary and version)" rather than providing vague suggestions.

**Hybrid determinism** for runbook execution: Combine natural language instructions with templated scripts. Query construction from templates rather than free-form generation reduces hallucination.

**Verification-before-completion gate**: Agent must produce evidence (curl output, screenshot, log line) before claiming a task is done. Hash-based loop detection and schema-strict tool calls further mitigate.

### State Drift During Long-Running Incidents

**Context rot** (PagerDuty): Performance degrades as context grows. JSON blobs of alerts, past incidents, topology, dependency graphs overwhelm the model's ability to weight information correctly. Cited: Liu et al. (2023).

**Instruction overload**: Inverse relationship between instruction volume and output quality (Jaroslawicz et al., 2025). Each new capability competes with existing ones for model attention. Monolithic agents that worked well at a certain feature set degraded as new capabilities were added.

**Google's token management**: Uses minimum token set per step because incident chain-of-thought can have very long horizon. Strict management prevents context loss and hallucination over time.

**PagerDuty's solution**: Multi-agent splitting sacrifices unified context for better per-agent reasoning quality. Each sub-agent receives only context relevant to its specific task.

**Memory corruption** (8.1% of production failures): Agent state becomes inconsistent over time, leading to decisions based on stale or contradictory information.

---

## 6. Enterprise System Design Scenarios

### Real-World Incident Response Agent Architectures

**Architecture 1: PagerDuty SRE Agent** (Multi-agent, LangGraph-based)
- Framework: LangGraph (Bulk Synchronous Parallel execution model).
- Pattern: Durable supervisor + stateless sub-agents. Supervisor formulates candidate root causes, spawns sub-agent per hypothesis.
- Deployment: Single-process co-location. Investigation work is overwhelmingly I/O-bound. No CPU hotspot to isolate.
- Memory: Past incidents, human responder actions, conversation history, changes, dependencies.
- Specialized agents: SRE Agent (investigation), Scribe Agent (Zoom transcription + summaries), Shift Agent (scheduling conflicts), Insights Agent (analytics recommendations).
- Transport: In-process asyncio.Queue (production). PubSub + webhooks + durable store evaluated but eliminated for intra-agent communication.
- Result: Incidents resolved up to 50% faster.

**Architecture 2: Google AI Operator** (Tiered autonomy, safety-first)
- Foundation: Gemini + custom fine-tuned models, Agent Development Kit (ADK), MCP servers.
- Pattern: Orchestrator spawns specialized sub-agents (LogAnalyzer, FixSuggester, ResponseFormatter). Mirrors SRE team structure.
- Safety: Actuation Agent serves as unified control plane and safety gateway. Three phases: standardized discovery/planning, dynamic autonomy/safety guardrails, post-actuation guardians + Red Button.
- Evaluation: Bronze/Silver/Gold data tiers. Nightly evaluations on Everest platform against rolling real-world incidents. Hybrid LLM-as-Judge + deterministic scoring.
- Scale: Thousands of incidents, every trace in Spanner. A/B tested at Google scale.
- Results: 10% MTTM reduction (investigation hypothesis), 44% MTTM reduction (investigation dashboards), 195% increase in anomaly findings.

**Architecture 3: Datadog Bits AI SRE** (Platform-integrated, builder-friendly)
- Pattern: Platform-native agent with access to all telemetry (logs, metrics, traces, APM).
- Builder: Bits Agent Builder -- create agents via natural language description, blueprints, or from scratch.
- Chaining: Investigation agent -> remediation agent (conditionally triggered on known pattern match).
- Hybrid automation: Rule-based + AI reasoning in single workflow.
- Work management: Assign work items to AI agents alongside people. Automation rules auto-assign matching items to agents.
- Enterprise: HIPAA-regulated workload support, RBAC, tested against 2,000+ environments.

### Trade-Off Matrices

**Monolithic Agent vs. Multi-Agent**:

| Dimension | Monolithic | Multi-Agent |
|-----------|-----------|-------------|
| Context quality | Degrades with scale | High (scoped per agent) |
| Latency | Sequential bottleneck | Parallel fan-out |
| Interactivity | None (wait for completion) | Mid-run steering |
| Debugging | Single trace | N+1 traces |
| State consistency | Trivial | Complex (need checkpointing strategy) |
| Failure blast radius | Total | Isolated (sub-agent re-spawn) |

**Durable vs. Stateless Sub-Agents**:

| Dimension | Durable Everywhere | Asymmetric (PagerDuty model) |
|-----------|-------------------|------------------------------|
| Recovery | Per-agent checkpoint | Re-spawn sub-agents from scratch |
| Complexity | N+1 checkpoint reconciliation | Single supervisor checkpoint |
| Consistency | Strong but expensive | Eventual but simple |
| Recommended when | Sub-agent work is expensive/long | Sub-agents are cheap/fast |

**Human-in-the-Loop Spectrum**:

| Approach | Read Operations | Write Operations | Blast Radius |
|----------|----------------|------------------|-------------- |
| Full autonomy (L4) | Allowed | Allowed (with dynamic risk eval) | Highest |
| Human-approved mutations (L2) | Allowed | Human approval required | Medium |
| Full oversight (L0) | Human-directed | Human-directed | Lowest |
| Recommended | L3-L4 for reads | L2 for writes, L3 for low-risk writes | Contextual |

**Distributed vs. Single-Process Deployment**:

| Dimension | Distributed (PubSub/webhooks) | Single-Process (asyncio) |
|-----------|-------------------------------|--------------------------|
| Network failures | Must handle | Eliminated |
| Operational complexity | Broker + endpoints + store | Single binary |
| Multi-team ownership | Supports | Does not support |
| I/O-bound workloads | Over-engineered | Ideal |
| PagerDuty recommendation | "Build the hard version to understand the problem; ship the simple one" | Production choice |

---

## Sources

- [1] [SystemDesign.one -- How Do AI Agents Work](https://newsletter.systemdesign.one/p/how-do-ai-agents-work) -- AI agent architecture: brain/hands/skills/memory/SOPs, 10-step evolution framework, multi-agent orchestration patterns.
- [2] [PagerDuty -- Inside PagerDuty's SRE Agent](https://www.pagerduty.com/eng/inside-pagerdutys-sre-agent-how-we-built-deep-incident-investigation/) -- Deep technical architecture: monolithic-to-multi-agent transition, reactive loop, durable supervisor, LangGraph BSP model.
- [3] [PagerDuty -- End-to-End AI Agent Suite Launch](https://www.pagerduty.com/newsroom/2025-fall-productlaunch/) -- SRE Agent, Scribe Agent, Shift Agent, Insights Agent; 50% faster resolution.
- [4] [PagerDuty -- SRE Agent with Memory](https://www.pagerduty.com/blog/ai/we-built-an-sre-agent-with-memory-and-its-transforming-incident-response/) -- Memory as foundational component, compounding operational gains.
- [5] [PagerDuty -- AI Ecosystem Expansion](https://www.pagerduty.com/newsroom/pagerduty-expands-ai-ecosystem-to-supercharge-ai-agents/) -- MCP integration, Claude Code plugin, Cursor integration, 750+ tools.
- [6] [Google Cloud Blog -- How Google SRE Uses Agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations) -- Ecosystem of specialized agents, design principles, autonomy levels.
- [7] [Google SRE Whitepaper -- AI Engineering Reliable Operations](https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/) -- Bronze/Silver/Gold evaluation tiers, AI Operator architecture, IRMA, nightly evaluations, Actuation Agent, 10% and 44% MTTM reductions.
- [8] [Anthropic -- How We Contain Claude](https://www.anthropic.com/engineering/how-we-contain-claude) -- Three-layer defense, isolation patterns, egress controls, real incident examples, sealed VM architecture.
- [9] [Anthropic -- CISO's Guide to Agentic AI](https://claude.com/blog/ciso-guide-to-agentic-ai) -- Principle of least agency, RBAC, per-connector controls, shadow adoption risks.
- [10] [Anthropic -- Writing Effective Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents) -- Tool design for non-deterministic systems, agent context management.
- [11] [Anthropic -- Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use) -- Tool Search Tool, programmatic tool calling, tool use examples.
- [12] [Datadog -- Bits AI SRE Agent](https://www.datadoghq.com/about/latest-news/press-releases/datadog-launches-bits-ai-sre-agent-to-resolve-incidents-faster/) -- SRE agent with telemetry/architecture/org awareness, HIPAA support, 2000+ environments.
- [13] [Datadog -- Bits Agent Builder](https://docs.datadoghq.com/actions/agents/) -- Custom AI agents, chaining agents, hybrid rule-based + AI workflows.
- [14] [Datadog -- Incident AI](https://docs.datadoghq.com/incident_response/incident_management/investigate/incident_ai/) -- Proactive summaries, related incident detection, conversational queries in Slack.
- [15] [Temporal -- Durable Execution Meets AI](https://temporal.io/blog/durable-execution-meets-ai-why-temporal-is-the-perfect-foundation-for-ai) -- Event history model, deterministic replay, non-deterministic side effect recording.
- [16] [Inngest -- Durable Execution for AI Agents](https://www.inngest.com/blog/durable-execution-key-to-harnessing-ai-agents) -- Checkpoint-based state persistence, failure recovery cost analysis.
- [17] [Zylos Research -- Durable Execution for Agent Runtimes](https://zylos.ai/research/2026-04-24-durable-execution-agent-runtimes/) -- Replay mechanics, non-determinism handling, ecosystem landscape.
- [18] [Spheron -- LLM Inference SLO Engineering](https://www.spheron.network/blog/llm-inference-slo-ttft-itl-latency-budget-guide-2026/) -- TTFT targets, ITL P99 under 30ms, SLO cost curves.
- [19] [Kunal Ganglani -- 2026 LLM API Latency Benchmarks](https://www.kunalganglani.com/blog/llm-api-latency-benchmarks-2026) -- Provider comparison: Anthropic most consistent, OpenAI P99 3-5x P50.
- [20] [Kunal Ganglani -- AI Agent Latency Budgets: 6-Tier Framework](https://www.kunalganglani.com/blog/ai-agent-latency-optimization-budget) -- Agent latency multiplier 3-10x beyond API benchmarks.
- [21] [Maxim AI -- Reduce LLM Cost and Latency Guide 2026](https://www.getmaxim.ai/articles/reduce-llm-cost-and-latency-a-comprehensive-guide-for-2026/) -- 60-80% token spend reduction, model routing, semantic caching, context compression.
- [22] [Zylos Research -- AI Agent Cost Optimization](https://zylos.ai/research/2026-04-12-ai-agent-cost-optimization-token-budget-model-routing/) -- Token budgets, model routing, production FinOps.
- [23] [Waxell AI -- AI Agent Circuit Breakers](https://www.waxell.ai/blog/ai-agent-circuit-breaker-pattern) -- Three-state model, kill switch vs. circuit breaker, $437 retry loop incident.
- [24] [Zylos Research -- Graceful Degradation Patterns](https://zylos.ai/research/2026-02-20-graceful-degradation-ai-agent-systems/) -- 41-86.7% failure rates without fault tolerance, layered resilience.
- [25] [Microsoft -- Applying SRE to Autonomous AI Agents](https://techcommunity.microsoft.com/blog/linuxandopensourceblog/applying-site-reliability-engineering-to-autonomous-ai-agents/4521357) -- Agent SRE integration: safety SLO, circuit breaker, health check.
- [26] [Blaxel -- SOC 2 Compliance for AI Agents](https://blaxel.ai/blog/soc-2-compliance-ai-guide) -- Auditor expectations, model lineage, $35K-$150K cost.
- [27] [TianPan.co -- HIPAA, SOC2 Agent Architecture Constraints](https://tianpan.co/blog/2026-05-07-hipaa-soc2-ai-agent-architectural-constraints-compliance) -- PHI in context window is in-scope, encryption requirements.
- [28] [FutureAGI -- AI Agent Compliance and Governance 2026](https://futureagi.com/blog/ai-agent-compliance-governance-2026) -- 21% agent registry rate, 42% project abandonment from governance failures.
- [29] [Arize -- Why AI Agents Break: Field Analysis](https://arize.com/blog/common-ai-agent-failures/) -- Failure mode distribution, MTTR comparison.
- [30] [Fiddler AI -- Agent Failure Rate 70-95%](https://www.fiddler.ai/blog/ai-agent-failure-rate) -- 88% demo-to-production failure rate.
- [31] [Galileo -- 7 AI Agent Failure Modes](https://galileo.ai/blog/agent-failure-modes-guide) -- Hallucination cascades, verification gates.
- [32] [arxiv -- Design Space of AI Agent Systems](https://arxiv.org/pdf/2604.14228) -- Context management, tool routing, recovery, subagent delegation.
