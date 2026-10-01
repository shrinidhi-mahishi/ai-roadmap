# Research: How to Design an Incident Response AI Agent

**Date researched**: 2026-09-29
**Sources consulted**: 34
**Sibling topics (do not duplicate)**: Spec-driven development (`01-…`); general agent runtime loops (`02-…`). This note focuses on **incident-response (IR) agents**: alert ingestion → triage → runbooks → tool dispatch → human approval → blast-radius limits → on-call SLAs.

## 1. System Topology & Mechanics

### Control plane vs. data plane (IR-specific)

| Plane | IR agent role |
| --- | --- |
| **Control plane** | Alert/event ingress (PagerDuty Event Orchestration, AWS EventBridge case creation), escalation/virtual-responder placement, approval gates, policy/risk metadata on tools, durable run state for paused HITL, audit trail of proposed vs. approved actions ([PagerDuty Event Orchestration](https://support.pagerduty.com/main/docs/event-orchestration); [OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/); [Google Gemini CLI / ProdAgent safety layers](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)) |
| **Data plane** | LLM reasoning turns + MCP/tool calls against logs/metrics/runbooks/tickets; observation messages (log lines, CloudTrail events, Slack digests) appended into working context ([System Design Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work); [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)) |

Newsletter #131’s IR walkthrough is the progressive topology: **manual runbook → LLM assistant → MCP tools → Agent Skills → memory (`Agents.md`) → Agent SOPs (RFC 2119 MUST/SHOULD) → declared agent package → event/cron triggers (e.g. PagerDuty webhook → Lambda) → multi-agent orchestration** sharing a filesystem workspace ([System Design Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)).

### Orchestration models used in published IR agents

| Pattern | Where it appears in IR |
| --- | --- |
| **Workflow (deterministic outer loop)** | PagerDuty Event Orchestration rules: enrich/suppress/route → optionally run Automation Action **before human notify**; Incident Workflows that auto-engage Paige as virtual responder ([PagerDuty Event Orchestration](https://support.pagerduty.com/main/docs/event-orchestration); [Paige SRE Agent docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)) |
| **Agent (LLM-directed tool loop)** | Google ProdAgent / Gemini CLI: classify symptoms → `fetch_playbook` → typed mitigation tools; AWS Security IR investigative agent: clarify → query CloudTrail/IAM/EC2/Cost Explorer → timeline summary ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages); [AWS Security IR AI agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html)) |
| **Orchestrator–workers** | Google SRE AI: distinct agents for playbook navigation, alerting, anomaly detection, incident insights; Newsletter #131 Step 10: log-gatherer / infra / deploy-review specialists under a manager ([Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations); [Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work); [Anthropic orchestrator-workers](https://www.anthropic.com/engineering/building-effective-agents)) |
| **ICS / IMAG role topology (human control plane)** | Incident Commander / Communications Lead / Ops Lead hierarchy; agentic layer **on top of** IMAG for chat summarization, handoff docs, postmortem drafts ([Google SRE Workbook — Incident Response](https://sre.google/workbook/incident-response/); [Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations)) |

Anthropic’s distinction still applies: use a **workflow** when the IR path is fixed (noise reduction, priority assignment, known Automation Action); use an **agent** when subtasks are unpredictable (novel outage diagnosis) ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

### Alert ingestion & triage pipeline

1. **Ingest**: Monitoring → PagerDuty Global Integration / Event Orchestration; or GuardDuty/Security Hub → EventBridge → AWS Security Incident Response case ([PagerDuty AIOps Quickstart](https://support.pagerduty.com/main/docs/pagerduty-aiops-quickstart-guide); [AWS Security Blog — investigative agent](https://aws.amazon.com/blogs/security/accelerate-investigations-with-aws-security-incident-response-ai-powered-capabilities/)).
2. **Noise / priority**: AIOps grouping + Event Orchestration priority/severity rules; Forrester TEI (PagerDuty-commissioned) reports **91% signal-noise reduction** and **50% fewer incidents** for studied Operations Cloud customers ([PagerDuty Forrester TEI newsroom](https://www.pagerduty.com/newsroom/forrester-tei/); [PagerDuty AIOps product](https://www.pagerduty.com/platform/aiops/)).
3. **Precompute incident context (critical IR pattern)**: PagerDuty SRE Agent does **not** lazily discover PD facts via sequential API calls. On open it assembles a structured working set: incident object, raw alert payloads, Past/Related/Outlier incidents, Related Change events, runbook sections. Lazy discovery prototypes ran **>60 s** first response and order-dependent answers; precompute cut first-response latency to **~10 s** ([PagerDuty Eng — Context Over Cleverness](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
4. **Virtual responder**: Paige can be engaged via Incident Workflow on trigger/priority, or added to an escalation level to investigate **in parallel** with humans ([Paige SRE Agent docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent); [PagerDuty — triage without waking a human](https://www.pagerduty.com/blog/ai/new-enhancements-to-pagerdutys-sre-agent-triage-faster-without-waking-a-human/)).

### Runbooks → tools → skills → SOPs

- **Deterministic runbook bind**: if alert `custom_details.runbook_url` points at GitHub/Confluence, eagerly attach; else opportunistic follow of links already in context ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
- **Agent Skills**: markdown + template scripts loaded only when needed (e.g. metrics skill ignored when only alarms queried) to save context ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)).
- **Agent SOPs**: ordered RFC 2119 procedures (Amazon Kiro/Strands practice cited); invoke as `/Incident-response-agent-sop Investigate JIRA-1234` ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)).
- **Google mitigation playbooks**: closed set of generic mitigations (drain, rollback, restart, add capacity); LLM classifies symptoms and selects a **typed** playbook (e.g. `borg_task_restart`), not free-form bash ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).
- **Playbook quality agents**: Google SRE AI agents continuously improve playbooks from incident usage and can generate new playbooks from incidents ([Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations)).

### Tool dispatch surfaces (PagerDuty / Slack / logs / metrics)

| Surface | Documented capabilities |
| --- | --- |
| **PagerDuty ↔ Slack** | Dedicated incident channels; declare/ack/escalate/resolve; run Incident Workflows; `@PagerDuty` AI Assistant for catch-up / status drafts; bi-directional sync ([PagerDuty Slack Integration Guide](https://support.pagerduty.com/main/docs/slack-integration-guide)) |
| **Automation Actions** | Push-button or Event-Orchestration-triggered jobs via Runbook Automation (SaaS/self-hosted); diagnostics or remediation; can run **before** human notification ([PagerDuty Automation Actions](https://support.pagerduty.com/main/docs/automation-actions); [Event Orchestration](https://support.pagerduty.com/main/docs/event-orchestration)) |
| **Paige tools (examples)** | `get_incident_details`, `list_incidents`, `add_incident_note`, `get_service_details`, `get_related_services` (upstream/downstream for blast radius); connectors to Datadog, CloudWatch, Grafana, New Relic, Confluence, GitHub ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)) |
| **Google ProdAgent tools (examples)** | `get_incident_details`, `causal_analysis`, `timeseries_correlation`, `log_analysis`, `fetch_playbook`, typed mutations like `borg_task_restart` via MCP ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)) |
| **AWS investigative agent** | Clarifying questions → CloudTrail, IAM, EC2, Cost Explorer; statuses include Pending/InProgress/Waiting/Completed/Failed/Cancelled; action types Evidence/Investigation/Summarization ([AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html); [Boto3 `list_investigations`](https://docs.aws.amazon.com/boto3/latest/reference/services/security-ir/client/list_investigations.html)) |

**Bias-to-relevance tool policy (PagerDuty)**: restrict tools to **external** observability/knowledge; PagerDuty facts come from precomputed context—avoid inward PD API fishing. Auto-search logs only when provider, target, and time window are clear and the result would change the next step; otherwise **propose** the query ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).

### Human approval gates (placement)

Newsletter #131 stance: keep IR agent examples **read-only** (alarms/metrics/logs); push humans rightward—agent proposes, human owns mutations; cites postmortems of unauthorized AI production ops without review ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)).

**OpenAI Agents SDK**: `needs_approval=True` or callable policy; run pauses with `interruptions` / `ToolApprovalItem`; serialize `RunState` to durable store; `approve`/`reject` then resume; MCP `require_approval` / hosted `require_approval: "always"` ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).

**Anthropic Managed Agents**: permission policies `always_allow` vs `always_ask` (MCP toolsets default **`always_ask`** so newly added MCP tools do not auto-execute); confirm via `user.tool_confirmation` ([Anthropic permission policies](https://platform.claude.com/docs/en/managed-agents/permission-policies)).

**Google multi-layer safety**: (1) deterministic typed tools, (2) risk metadata per tool (safe/reversible/destructive), (3) policy layer (e.g. no global restart at peak; 2-person approval), (4) CLI confirmation, (5) audit of AI proposal + human approval ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).

**PagerDuty current state**: SRE Agent **proposes** commands; does not mutate/restart/cleanup until explicit approval flows, preconditions, and audit trails are proven ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)). Marketing materials describe remediation **with human approval** as the target path ([PagerDuty SRE Agent with memory blog](https://www.pagerduty.com/blog/aiops/we-built-an-sre-agent-with-memory-and-its-transforming-incident-response/)).

### Blast-radius limits

- **Related incidents + service graph**: Paige `get_related_services` and “Analyze Related Incidents” nudge for upstream/downstream impact ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent); [PagerDuty Eng — Related Incidents inform blast radius “why”](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
- **Scoped log queries**: short windows derived from alert; quote only discriminative lines ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
- **Memory tenant isolation**: observations/recollections/playbooks stay **per tenant and service**; no cross-customer learning ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
- **Input truncation**: Paige analyzes only the **first 2,000 characters** of each `custom_details` / note blob ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).
- **Google policy examples**: “No global restarts during peak traffic”; risk-flagged tools get stricter review ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).
- **AWS agent**: read-only via `AWSServiceRoleForSupport` SLR; CloudTrail-audited; does not train on customer data ([AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html)).

### On-call SLAs / MTT* framing

| Metric | Published guidance |
| --- | --- |
| **Acknowledge SLO** | Google Core SRE: typically **5-minute SLO just to acknowledge a page**; optimize **MTTM** (Mean Time to Mitigation / stop Bad Customer Minutes) ahead of full MTTR ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)) |
| **MTTA target (industry runbook automation)** | Incident.io automated-runbook guide: target **MTTA under 5 minutes** with good automation ([incident.io automated runbook guide](https://incident.io/blog/automated-runbook-guide)) |
| **Customer case (PagerDuty)** | Anaplan: MTTA from hours → **5 minutes**; MTTR from **3 hours → under 30 minutes** (vendor case study) ([PagerDuty AIOps outage blog](https://www.pagerduty.com/blog/aiops/built-to-withstand-the-next-outage-how-pagerduty-aiops-keeps-you-ahead/)) |
| **AWS resolution claim** | AI investigative agent on AWS-supported cases: resolution time from **days to hours**; summaries **within minutes**; clarifying questions timeout **10 minutes** then auto-start ([AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html)) |
| **ICS process** | Declare early; IC owns 3Cs (coordinate/communicate/control); Ops Lead applies tools; Communications Lead owns stakeholder updates ([Google SRE Workbook](https://sre.google/workbook/incident-response/)) |

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for this dimension. No vendor publishes p50/p95/p99 end-to-end latency SLAs, RPM/TPM ceilings, or per-incident token budgets specifically for production IR agents. Available figures: first-response latency experiments, AI Action metering, model list prices, and qualitative “within minutes” investigation claims.

### Latency (published)

| Path | Figure | Source |
| --- | --- | --- |
| PagerDuty SRE Agent first response after **precompute** | **~10 s** | [PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/) |
| Same agent, lazy API discovery prototype | **>60 s** first response + unstable answers | [PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/) |
| AWS investigative summary | “Within **minutes**” (not percentile-bound) | [AWS What’s New 2025-11-21](https://aws.amazon.com/about-aws/whats-new/2025/11/aws-security-incident-response-agentic-ai-powered-investigation/); [AWS docs](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html) |
| Clarifying-question wait | **10 minutes** then auto-start investigation | [AWS docs](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html) |
| Google root-cause code scan (narrative) | “Under **two minutes**” to find bad config push in walkthrough | [Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages) — **demo narrative, not an SLA** |

### Metering & platform cost (not raw tokens)

- **Paige / PagerDuty AI Actions**: each user request or nudge click consumes **4 AI Actions** from the account allotment ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent); [PagerDuty AI overview](https://docs.pagerduty.com/ai-automation/advance.md)).
- **AWS Security Incident Response investigative agent**: included at **no additional cost** with the service; service uses metered findings with free tier of **first 10,000 findings/month** then volume-tiered rates ([AWS Security Blog](https://aws.amazon.com/blogs/security/accelerate-investigations-with-aws-security-incident-response-ai-powered-capabilities/)).
- **Anthropic Managed Agents runtime**: **$0.08 per session-hour** active runtime + standard token rates ([Anthropic pricing](https://www.anthropic.com/pricing#api)).

### Model list prices (building-block costs for self-hosted IR agents)

Anthropic API (selected, from Anthropic pricing page as of research date):

| Model | Input / MTok | Output / MTok | Cache read (5-min TTL) |
| --- | --- | --- | --- |
| Sonnet 5.5 | $2 | $10 | $0.20 |
| Haiku 4.5 | $1 | $5 | $0.10 |
| Sonnet 4.6 (legacy listing) | $3 | $15 | $0.30 |

([Anthropic pricing](https://www.anthropic.com/pricing#api))

[inferred] Worked example for a mid-complexity IR investigation on Sonnet 5.5: ~40k input (precomputed context + runbook excerpts + 2–3 tool rounds) + ~4k output ≈ \(40 \times \$0.002 + 4 \times \$0.01 = \$0.08 + \$0.04 = \$0.12\) per investigation before caching. With prompt-cache hits on a stable system prompt + tool schemas, input can drop toward cache-read rates (~$0.20/MTok) for the static prefix. **Not a vendor IR benchmark.**

### Dynamic model routing for IR

[inferred] Anthropic’s routing workflow pattern (Haiku for easy/common, Sonnet for hard/unusual) maps cleanly to IR: route alert-noise classification and status drafting to a small model; route multi-hop RCA and mitigation selection to a frontier model ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). No public IR-specific routing hit rates found.

### Throughput / back-pressure

PagerDuty Event Orchestration is the machine-speed back-pressure layer (suppress, group, enrich before page). Agent loops inherit provider TPM/RPM; OpenAI SDK documents `max_turns` (default exists; `None` disables) as a cost/iteration guard—general agent NFR, not IR-specific ([PagerDuty Event Orchestration](https://support.pagerduty.com/main/docs/event-orchestration); see sibling `02-how-ai-agents-work.md` for SDK turn defaults).

## 3. Distributed Resilience & State

### Durable incident / agent state

| Mechanism | Behavior |
| --- | --- |
| **Precomputed Incident Context rebuild** | On trigger, note add, resolve; debounce bursty notes; proceed if a source is slow and **note what was skipped** ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)) |
| **HITL RunState serialization** | Persist paused approvals to DB/queue; `to_json`/`from_json`; sticky `always_approve`/`always_reject` survives resume; version agent defs with stored state ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)) |
| **Memory layers** | Observations (service facts), Recollections (decision-changing incident details), promoted Playbooks with normalized alert signatures (strip timestamps/UUIDs, keep discriminators) ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/); [Paige memory types](https://docs.pagerduty.com/ai-automation/advance/sre-agent)) |
| **Event-driven activation** | PagerDuty webhook → Lambda agent; cron for periodic reports; EventBridge case pipelines ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work); [AWS Security Blog](https://aws.amazon.com/blogs/security/accelerate-investigations-with-aws-security-incident-response-ai-powered-capabilities/)) |
| **Filesystem collaboration** | Multi-agent IR: orchestrator + specialists write shared files rather than heavy A2A protocols initially ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)) |

### Checkpoint / replay semantics

Google SRE AI design principle: agents must **explain** why/how an action was taken and which options were rejected (transparency over black-box automation); business continuity plans must include **AI failure contingencies** and backup automated/manual options ([Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations)).

[inferred] For mitigation tools, playbooks should include verify + rollback steps (Google narrative: playbook includes command, verify effectiveness, rollback) so failed mutations can be reversed without full RCA completion ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).

### Circuit breakers & rate limits (IR-shaped)

> ⚠️ Limited public data available for this dimension. No published breaker thresholds (error %, half-open probe intervals) for IR agent runtimes.

Documented substitutes:

- **Automation before page**: Event Orchestration Automation Actions can complete diagnostics/remediation before human notify—reduces cascade pages ([PagerDuty Event Orchestration](https://support.pagerduty.com/main/docs/event-orchestration)).
- **Fail-closed approval callables**: OpenAI callable `needs_approval` fails closed on malformed/missing args → requires manual approval ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
- **Skip-and-annotate**: if observability source missing, continue with explicit gap callout rather than blocking forever ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
- **Google**: prefer classic automation when deterministic automation already meets needs; do not replace working non-AI mitigations ([Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations)).

### Distributed locking / concurrency

OpenAI HITL production guidance: atomic owner-checked transition when consuming approval decisions so concurrent/replayed submissions cannot resume the same snapshot twice ([OpenAI HITL — long-running approvals](https://openai.github.io/openai-agents-python/human_in_the_loop/)). PagerDuty context rebuild uses debounce on note bursts to avoid racing on stale context ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).

## 4. Enterprise Security & Governance

### Identity, RBAC, least privilege

- Google SRE AI: agents must have a **strong identity** with assigned roles/permissions; same security/safety/privacy bar as humans/systems ([Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations)).
- AWS investigative agent: `AWSServiceRoleForSupport` **read-only** SLR; actions attributed in CloudTrail to that role ([AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html)).
- PagerDuty AI: Account Owner/Global Admin enable agents; team-level AI access toggle; Automation Actions have role matrices (who can create vs run) ([PagerDuty AI](https://docs.pagerduty.com/ai-automation/advance.md); [Automation Actions](https://support.pagerduty.com/main/docs/automation-actions)).
- Paige Memory API: view/update/redact memory artifacts at **human speed** for compliance ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).

### Tool-level approval & MCP governance

| Control | Spec |
| --- | --- |
| Anthropic MCP default | `always_ask` for MCP toolsets | ([permission policies](https://platform.claude.com/docs/en/managed-agents/permission-policies)) |
| OpenAI MCP | Local `require_approval`; hosted `require_approval: "always"`; sticky approvals keyed by `server_label` + tool name | ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)) |
| Anthropic tool annotations | Disclose open-world / **destructive** tools in MCP tool annotations | ([Anthropic — Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents)) |
| Google risk metadata | Per-tool impact class → stricter review | ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)) |

### PII / sensitive data

- AWS: customer data **not used for model training**; not shared with third parties ([AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html)).
- PagerDuty: tenant-isolated memory; Memory API for redaction ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/); [Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).
- OpenAI HITL: treat tool names/args as untrusted display content; keep full `RunState` snapshots **server-side**; authenticate reviewer via app session, not request body ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).

> ⚠️ Limited public data available for this dimension. No IR-vendor papers publishing NER/regex PII redaction F1 scores for log/chat pipelines feeding agents.

### Audit & compliance

- Google: every CLI-proxied action logs AI proposal + human approval for compliance ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).
- AWS: full CloudTrail of agent data access; responsible-AI disclosure that humans must verify recommendations ([AWS docs](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html)).
- PagerDuty: Advance AI Disclosure on Assurance Profile; generative AI fact-check guidelines ([PagerDuty AI](https://docs.pagerduty.com/ai-automation/advance.md)).
- ICS: keep working record of debugging/mitigation; record conference calls for timeline reconstruction ([Google SRE Workbook](https://sre.google/workbook/incident-response/)).

### Sandbox isolation

Anthropic: extensive testing in sandboxed environments + guardrails before autonomy; agents trade latency/cost for performance ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Newsletter #131: watch agents live (don’t switch tabs); log all output before 24/7 promotion ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)).

## 5. Production Failure Modes

### Context assembly failure (IR’s #1 failure)

PagerDuty’s motivating incident: known race-condition runbook existed but was not recalled; response stretched **~3 hours** with extra late-night responders. Prototype with alert + one log line + runbook identified failure immediately. Lesson: **context recall > cleverness** ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).

Lazy tool-driven discovery of PD facts: **>60 s** latency, first-fetched bias, order-dependent answers ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).

### Hallucinated queries / invented fields

Newsletter #131 Skills pattern: prefer template query builders over free-form LLM queries to reduce hallucinations of nonexistent log fields ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)). PagerDuty: formulate queries from runbooks/playbooks/service memory; ask for missing params rather than guessing ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).

### Unsafe / unauthorized production mutation

Newsletter #131 cites postmortems where AI ran unauthorized AWS production ops without human review—argues against ungoverned write tools ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)). Google: command safe in one state may be unsafe during config push; policy + HITL required ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).

### Mitigation failure mid-loop

Gemini CLI walkthrough: `borg_task_restart` fails → agent stays in flow, analyzes that only this job fails in cell → pivots to code RCA instead of expanding blast radius ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).

### Infinite / costly agent loops

Anthropic: include max-iteration stopping conditions; agents’ autonomy raises compounding error risk ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). PagerDuty: bias against speculative fetches; if grounding thin, flag absence and ask for runbook rather than asserting ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).

### State drift / stale context

Debounce note bursts; rebuild on trigger/notes/resolve ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)). OpenAI: version markers with serialized HITL state when approvals sit a long time ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).

### Cascading alert storms

Without AIOps grouping, agent triage is drowned; Forrester TEI cites **91%** noise reduction and **59%** downtime reduction for studied PagerDuty Operations Cloud cohorts—platform precondition for agent usefulness ([PagerDuty Forrester TEI](https://www.pagerduty.com/newsroom/forrester-tei/)). [inferred] Noise reduction is a prerequisite NFR for IR agents; treating the LLM as the first noise filter is the wrong layer.

### Process failure (human IR)

Google Home quota bug case study: failing to **declare an incident early** delayed organized response despite an on-call noticing anomalous QPS ([Google SRE Workbook](https://sre.google/workbook/incident-response/)). Agents that only summarize chat cannot fix undeclared incidents—virtual responder / early declare automation addresses this ([Paige virtual responder](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).

### Eval / non-determinism

PagerDuty: small prompt changes yield different results; techniques that helped: instruct agent to **admit unknowns**, avoid over-structured answer templates for unanticipated questions; LLM judges + human sampling + dogfooding on real incidents ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).

## 6. Enterprise System Design Scenarios

### Reference architectures (published)

**A. Progressive IR agent (Newsletter #131 / Fran Soto)**  
Manual runbook → LLM → MCP → Skills → Memory → SOP → packaged agent → webhook/cron → multi-agent filesystem swarm. Emphasis: earn autonomy step-by-step; keep writes behind human approval ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)).

**B. PagerDuty Paige / SRE Agent**  
Event → AIOps noise reduction → precomputed Incident Context (~10 s) → unprompted first proposal in Slack/Console → selective external tools (logs/metrics/docs) → memory/playbook promotion on resolve → remediation still propose-only until guardrails mature ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/); [Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).

**C. Google ProdAgent + Gemini CLI**  
Page → MTTM-first → classify into closed mitigation set → typed MCP tools + risk/policy/HITL → on failure pivot to RCA → CL for patch → postmortem custom command + issue filing ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages); [Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations)).

**D. AWS Security Incident Response investigative agent**  
Case create (manual or GuardDuty/Security Hub) → parallel SIRE humans + AI agent → optional clarify (10 min timeout) → read-only multi-service evidence → timeline in Investigation tab / case comment; auto-triage filters known-good entities before agent runs ([AWS docs](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html); [AWS Security Blog](https://aws.amazon.com/blogs/security/accelerate-investigations-with-aws-security-incident-response-ai-powered-capabilities/)).

### Trade-off matrix

| Approach | Cost | Latency | Ops complexity | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **Deterministic Automation Actions / Event Orchestration** | Low per-event (no LLM) | Machine-speed | Rules maintenance | High (pre-approved jobs) | High for known classes |
| **Read-only triage agent (Paige-style propose)** | AI Actions / tokens | ~10 s first response if precomputed | Connector + runbook hygiene | Medium–high (no mutate) | Scales with service memory |
| **HITL mutation agent (Google CLI-style)** | Tokens + on-call time | Seconds–minutes per approval | Policy + tool metadata + audit | Highest when policy enforced | Bounded by human approvers |
| **Fully autonomous remediation** | Lowest human cost if correct | Fastest MTTM **if safe** | Highest (blast radius, rollback, eval) | Highest risk | Google: only with mature safety systems; PagerDuty: not yet for mutations |

### Capacity planning signals (what exists)

- Precompute context to stay near **~10 s** first useful message rather than **>60 s** discovery ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
- Budget **4 AI Actions** per Paige interaction/nudge ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).
- Align virtual-responder engagement to priority so P1/P2 get parallel agent triage against **5-minute ack SLO** ([Google ack SLO](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages); [Paige virtual responder](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).
- Truncation: design alert `custom_details` so discriminative fields fit in **2,000 characters** for Paige ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).
- Forrester TEI (commissioned): **249% ROI / 3 years**, **91%** noise reduction, **59%** less downtime, **50%** fewer incidents—use as vendor-reported capacity case, not independent audit ([PagerDuty Forrester TEI](https://www.pagerduty.com/newsroom/forrester-tei/)).

### Design checklist (interview-ready)

1. Separate **noise reduction** (deterministic) from **diagnosis** (agentic).
2. **Precompute** incident context; don’t make the model discover the IR platform via tools.
3. Bind **runbooks** deterministically when possible; Skills/SOPs for query construction.
4. Tools: typed, risk-annotated, external-signal focused; MCP defaults to ask.
5. Writes: HITL with durable `RunState` / policy / audit; verify + rollback in playbook.
6. Bound blast radius: service graph, scoped queries, tenant memory, peak-traffic policies.
7. Measure **MTTA / MTTM** against on-call SLOs (e.g. 5-minute ack), not only chat quality.
8. Promote successful recollections to versioned playbooks; redact via Memory API.

## Sources

- [1] https://newsletter.systemdesign.one/p/how-do-ai-agents-work — System Design Newsletter #131: 10-step IR agent build framework (primary article)
- [2] https://www.anthropic.com/engineering/building-effective-agents — Workflows vs agents; orchestrator-workers; guardrails
- [3] https://openai.github.io/openai-agents-python/human_in_the_loop/ — HITL approvals, RunState durability, MCP require_approval
- [4] https://platform.claude.com/docs/en/managed-agents/permission-policies — always_allow / always_ask; MCP default ask
- [5] https://www.anthropic.com/engineering/writing-tools-for-agents — Tool design; MCP destructive/open-world annotations
- [6] https://www.anthropic.com/pricing#api — Token and Managed Agents session-hour pricing
- [7] https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages — ProdAgent tools, 5-min ack SLO, MTTM, safety layers
- [8] https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations — SRE AI agents across SDLC/IMAG; design principles
- [9] https://sre.google/workbook/incident-response/ — ICS/IMAG roles; 3Cs; case studies
- [10] https://sre.google/resources/practices-and-processes/twenty-years-of-sre-lessons-learned/ — Automate mitigations; MTTR via automated first response
- [11] https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/ — Precompute context (~10 s vs >60 s); memory; propose-only mutations
- [12] https://docs.pagerduty.com/ai-automation/advance/sre-agent — Paige capabilities, AI Actions (×4), tools, memory, 2k char limit
- [13] https://docs.pagerduty.com/ai-automation/advance.md — PagerDuty AI Assistant/Agents overview and settings
- [14] https://support.pagerduty.com/main/docs/event-orchestration — Routing, enrichment, Automation Actions before notify
- [15] https://support.pagerduty.com/main/docs/automation-actions — Runbook Automation Actions RBAC and runners
- [16] https://support.pagerduty.com/main/docs/slack-integration-guide — Slack incident channels and AI collaboration
- [17] https://support.pagerduty.com/main/docs/pagerduty-aiops-quickstart-guide — AIOps ingestion and triage setup
- [18] https://www.pagerduty.com/newsroom/forrester-tei/ — Forrester TEI: 249% ROI, 91% noise↓, 59% downtime↓, 50% incidents↓
- [19] https://www.pagerduty.com/platform/aiops/ — AIOps product claims (91% noise)
- [20] https://www.pagerduty.com/blog/aiops/built-to-withstand-the-next-outage-how-pagerduty-aiops-keeps-you-ahead/ — Anaplan MTTA/MTTR case figures
- [21] https://www.pagerduty.com/blog/ai/new-enhancements-to-pagerdutys-sre-agent-triage-faster-without-waking-a-human/ — Workflow-triggered virtual responder triage
- [22] https://www.pagerduty.com/blog/aiops/we-built-an-sre-agent-with-memory-and-its-transforming-incident-response/ — Detect/diagnose/remediate(with approval)/learn loop
- [23] https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html — AWS AI investigative agent workflow, SLR, 10-min clarify timeout
- [24] https://aws.amazon.com/blogs/security/accelerate-investigations-with-aws-security-incident-response-ai-powered-capabilities/ — Evidence sources; free-tier findings; EventBridge pipelines
- [25] https://aws.amazon.com/about-aws/whats-new/2025/11/aws-security-incident-response-agentic-ai-powered-investigation/ — Feature GA announcement
- [26] https://docs.aws.amazon.com/boto3/latest/reference/services/security-ir/client/list_investigations.html — Investigation action types and statuses
- [27] https://incident.io/blog/automated-runbook-guide — Automated runbooks; MTTA <5 min target; MTTR baseline ranges
- [28] https://modelcontextprotocol.io/docs/getting-started/intro — MCP as tool transport (cited via #131 footnotes)
- [29] https://agents.md/ — Agents.md memory/context standard (cited via #131)
- [30] https://datatracker.ietf.org/doc/html/rfc2119 — MUST/SHOULD requirement language for Agent SOPs
- [31] https://kiro.dev/ — Amazon Kiro (SOP/agent packaging context in #131)
- [32] https://strandsagents.com/latest/ — AWS Strands Agents framework (cited in #131)
- [33] https://developers.openai.com/api/docs/guides/agents/guardrails-approvals — Guardrails vs human review placement
- [34] https://www.pagerduty.com/blog/product/accelerating-velocity-with-aiops-in-the-age-of-ai-everything/ — AIOps ingestion/grouping narrative + TEI metrics restatement
