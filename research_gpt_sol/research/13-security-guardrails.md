# Research: Security & Guardrails — Prompt Injection, Permissions, Sandboxing, and Policies

**Date researched**: 2026-08-21  
**Sources consulted**: 44

Security for an agentic system is not a model setting. It is a distributed control system around a probabilistic planner that reads attacker-controlled content and may request real side effects. OWASP's 2026 LLM Top 10 keeps prompt injection at LLM01 and states that current GenAI systems do not have robust prompt-injection prevention; systems should assume the instruction boundary can eventually be bypassed and limit the resulting impact architecturally [[1]](https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/) [[2]](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/blob/main/2026/final/LLM01_PromptInjection.md). That produces the central design rule:

> Treat model output as an untrusted proposal. A deterministic enforcement layer, using authenticated identity and current state, decides whether any proposal becomes an action.

The four named concerns have different jobs:

- **Prompt-injection controls** reduce the probability that hostile data changes agent intent.
- **Permissions** reduce the authority available to the user, agent, tool, workload, and credential.
- **Sandboxing** limits filesystem, process, network, and resource impact when code or a model is compromised.
- **Policies** express and enforce which principal may perform which action on which resource under which conditions.

No one control supplies all three security functions. This chapter labels recommendations as **prevention** (make compromise or an unauthorized action harder), **detection** (observe and classify it), or **containment** (bound impact after it occurs).

## 1. System Topology & Mechanics

### 1.1 Threat model and trust boundaries

NIST's adversarial-ML taxonomy distinguishes direct prompt injection, indirect injection through external content, jailbreaking, prompt extraction, poisoning, privacy attacks, and misuse; the taxonomy applies across chat, RAG, and agents rather than only to a chat input box [[5]](https://csrc.nist.gov/pubs/ai/100/2/e2025/final). An agent also inherits conventional application threats: broken access control, injection, security misconfiguration, software-supply-chain failures, and missing logging remain part of the OWASP Web Top 10 [[10]](https://owasp.org/Top10/).

The earlier OWASP 2025 LLM Top 10 remains useful for comparing category evolution, while the 2026 preface explains that prompt injection remains first and that incident counts can understate the underlying attack surface because deployed defenses suppress some incidents [[9]](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf) [[11]](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/blob/main/2026/final/LLM00_Preface.md). Use the 2026 document for current risk classification.

Define these actors and assets before selecting guardrails:

| Element | Examples | Default trust decision |
|---|---|---|
| Human principal | employee, customer, administrator, attacker | authenticated identity is not proof that every requested action is permitted |
| Agent runtime | orchestrator, planner, subagent, memory writer | untrusted decision maker; never a policy decision point |
| Instructions | system/developer policy, user goal, delegated task | authority depends on authenticated source and precedence, not natural-language confidence |
| Data | web page, email, ticket, document, tool result, image, code comment | untrusted content; may contain instructions or encoded payloads |
| Tools | browser, shell, database, payment, messaging, MCP server | capabilities with independent identity, scope, side effects, and failure modes |
| Credentials | OAuth token, workload identity, API key, cloud role | bearer authority; must be short-lived, scoped, audience-bound, and kept outside model-visible context |
| Resources | tenant rows, repository, branch, account, file, cluster | authorization target with owner, classification, and current state |
| Side effects | send, publish, purchase, delete, deploy, change access | require deterministic authorization; high-impact actions may also require bound approval |
| Logs and traces | prompts, tool arguments/results, decisions, artifacts | security evidence and a separate sensitive-data asset |

Direct injection arrives in a user-controlled prompt. Indirect injection is embedded in content the system retrieves: a web page, email, issue, document, image, database field, package output, or another agent's message. The original indirect-injection work showed that connecting an LLM to retrieved data and APIs blurs instructions and data, enabling remote manipulation, data theft, and API abuse [[12]](https://arxiv.org/abs/2302.12173). OWASP 2026 further includes cross-modal attacks and attacks planted on apparently trusted surfaces [[2]](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/blob/main/2026/final/LLM01_PromptInjection.md).

Prompt injection differs from SQL injection. SQL can separate code and data through a formal grammar and parameterization. General-purpose models deliberately interpret natural language in both instructions and content; delimiters, XML tags, or a statement that content is "untrusted" can help but do not create an enforceable parser boundary [[3]](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html). OpenAI's instruction-hierarchy research trains models to prioritize privileged instructions, but it is a robustness layer, not an authorization mechanism or universal guarantee [[17]](https://openai.com/index/the-instruction-hierarchy/).

### 1.2 Reference architecture

```text
 Human / calling service
        |
        v
 Identity + session + tenant binding
        |
        v
 Input gateway ---- provenance / content classification / DLP ----> evidence store
        |
        v
 Agent runtime (model, planner, memory) <---- read-only evidence adapters
        |
        |  untrusted proposed action: tool + normalized arguments
        v
 Tool gateway / Policy Enforcement Point (PEP)
        |---- schema and semantic validation
        |---- current identity, tenant, purpose, resource state
        |---- Policy Decision Point (PDP): permit / deny / obligations
        |---- risk engine and transaction-bound approval
        |---- budget, rate, idempotency, circuit breaker
        v
 Credential broker ---- short-lived, audience/resource-scoped token
        |
        v
 Sandboxed tool executor ---- filesystem / process / resource / network egress controls
        |
        v
 External resource or service

 Every boundary ----> append-only decisions, traces, security alerts, outcome reconciliation
```

The **control plane** owns identities, policy authoring and signed bundles, approval workflow, credential issuance, sandbox images, audit configuration, and emergency revocation. The **data plane** handles each request: untrusted-content labeling, planning, PEP/PDP decisions, sandboxed execution, egress, and result filtering. Separate them so a prompt-injected model cannot edit the policy or guardrail configuration it must obey `[inferred]`.

### 1.3 Prompt-injection controls

Use defense in depth, while being precise about what each layer proves:

| Control | Function | What it does | What it does not prove |
|---|---|---|---|
| Privileged-instruction hierarchy | prevention | teaches the model to prefer authenticated higher-priority instructions | cannot make the model a security boundary |
| Keep untrusted variables out of privileged prompts | prevention | avoids elevating retrieved/user text into developer authority | does not neutralize untrusted text later read by the model |
| Provenance labels and spotlighting | prevention/detection | preserves source/trust metadata; transforms untrusted content to make provenance salient | adaptive or cross-modal attacks can still succeed |
| Typed structured outputs | prevention/containment | limits the next component to an allowlisted schema and rejects extra free text | a schema-valid action can still be malicious or unauthorized |
| Input/output injection detector | detection | scores suspicious instructions, obfuscation, exfiltration, and policy evasion | has false negatives and false positives; adversaries adapt |
| Content sanitization/rendering controls | prevention/containment | removes active markup, unsafe URLs, hidden text, script, or executable content | semantic instructions can survive sanitization |
| Separate evidence and action planes | containment | research content can inform a report without acquiring write capability | does not ensure the report is true |
| Action-level PEP/PDP | prevention/containment | rejects unauthorized side effects regardless of the model's rationale | requires complete mediation and correct policies |
| DLP, egress monitor, canaries | detection/containment | detects or blocks secret/PII movement and unexpected destinations | cannot recover secrets already exposed to an allowed recipient |
| Scoped capabilities and sandbox | containment | reduces reachable files, APIs, destinations, and resources | does not correct a permitted but unintended action |

OpenAI's current agent-safety guidance specifically recommends keeping untrusted variables out of developer messages, using structured outputs between nodes, enabling approvals for MCP tools, adding input guardrails, and evaluating traces; it also cautions that filters and PII detection are not foolproof [[15]](https://developers.openai.com/api/docs/guides/agent-builder-safety). Its broader safety guidance recommends moderation, red-teaming, human review for high-stakes use, constrained inputs/outputs, and user reporting mechanisms [[16]](https://developers.openai.com/api/docs/guides/safety-best-practices).

Benchmark claims must be scoped. AgentDojo introduced 97 realistic tasks and 629 security test cases in an executable tool environment [[13]](https://arxiv.org/abs/2406.13352). CaMeL separates trusted control flow from untrusted data flow and applies capability policies; its paper reports completing 67% of AgentDojo tasks with provable security under that formalized benchmark, not 67% of arbitrary production work [[14]](https://arxiv.org/abs/2503.18813). Spotlighting reported reducing attack success from above 50% to below 2% in its tested GPT-family indirect-injection setup while preserving most utility, but source transformation is not a universal proof [[19]](https://arxiv.org/abs/2403.14720). The May 2026 AgentDyn benchmark contains 60 open-ended tasks and 560 injection cases across three domains and found that almost all ten evaluated defenses were insecure or incurred substantial over-defense in its dynamic setting [[18]](https://arxiv.org/abs/2602.03117).

CaMeL's public reference implementation is useful for studying capability-tag propagation and enforcement mechanics, but the repository describes research code rather than a complete production security product [[44]](https://github.com/google-research/camel-prompt-injection).

These results are compatible: a defense can perform well on a fixed distribution and still fail on an adaptive, dynamic, cross-tool distribution. Track both attack success and benign utility.

### 1.4 Permissions and capability design

Permissions answer **who can do what to which resource now**, not "does this command sound safe?" Implement complete mediation at a tool gateway. The model proposes an action; the PEP normalizes it and asks the PDP using authenticated attributes:

```json
{
  "principal": {"user": "u123", "workload": "agent-prod", "tenant": "t7"},
  "action": "payment.transfer",
  "resource": {"account": "a9", "owner": "u123", "classification": "financial"},
  "context": {
    "purpose": "invoice-8421",
    "amount": 1250,
    "currency": "INR",
    "destination": "vendor-44",
    "risk": 0.71,
    "policy_version": "2026-08-12.4",
    "approval_id": null
  }
}
```

Cedar models authorization around principal, action, resource, and context. Its semantics are default deny when no permit matches, and a matching `forbid` overrides permits [[28]](https://docs.cedarpolicy.com/auth/authorization.html). Schema validation catches policy/type errors before deployment, although schema changes can invalidate existing policy assumptions [[29]](https://docs.cedarpolicy.com/policies/validation.html). OPA similarly separates a policy decision point from application enforcement and documents local/sidecar versus centralized deployment trade-offs [[31]](https://www.openpolicyagent.org/docs/deploy).

Permission rules `[inferred]`:

- **Prevention:** default deny; allow exact actions/resources, not broad tool access.
- **Prevention:** intersect user authority, agent/service authority, task delegation, tenant boundary, and tool capability. Delegation may narrow authority, never expand it.
- **Containment:** issue just-in-time, short-lived credentials only after authorization; do not place long-lived secrets in prompts, environment dumps, or the sandbox.
- **Containment:** restrict token audience/resource. OAuth 2.0 Security BCP recommends minimum privileges and audience-restricted access tokens, sender-constrained tokens where possible, asymmetric client authentication, and refresh-token protections [[22]](https://www.rfc-editor.org/rfc/rfc9700.html). OAuth Resource Indicators let a client request a token for a specific protected resource [[23]](https://www.rfc-editor.org/rfc/rfc8707.html); DPoP binds tokens to a key to reduce replay value [[24]](https://www.rfc-editor.org/rfc/rfc9449.html).
- **Prevention:** enforce row, field, repository, branch, account, origin, method, monetary, geographic, and time limits at the resource/service layer as well as the tool gateway.
- **Detection:** log each allow/deny with the policy version and reason; alert on scope probing, repeated denials, cross-tenant requests, and unusual credential use.
- **Containment:** revoke leases and terminate runs on policy changes, user revocation, anomaly thresholds, or budget exhaustion.

SPIFFE's Workload API can deliver X.509 or JWT workload identities without application-bundled secrets [[25]](https://spiffe.io/docs/latest/spiffe-specs/spiffe_workload_api/). SPIFFE notes that JWT-SVIDs are bearer tokens with replay considerations and generally prefers X.509-SVIDs where the environment supports them [[26]](https://spiffe.io/docs/latest/spiffe/concepts/). Workload identity complements, but does not replace, user/tenant authorization.

### 1.5 Human approvals

Approval is a policy obligation for ambiguity or high consequence, not a replacement for least privilege. Present a transaction preview with actor, tool, normalized arguments, target, data leaving the boundary, irreversible effects, and why approval is needed. Cryptographically or server-side bind approval to `(principal, action, resource, normalized_args_hash, account, policy_version, expiry)`; any material change invalidates it `[inferred]`.

Use risk tiers `[inferred]`:

| Tier | Examples | Execution rule |
|---|---|---|
| 0 | search, read public documentation | automatic within budgets |
| 1 | read tenant data, edit ephemeral worktree | automatic if narrow policy and audit apply |
| 2 | send draft externally, push working branch, moderate spend | bound approval or pre-approved workflow |
| 3 | delete, transfer funds, publish, change access, production deploy | fresh explicit approval, separation of duties where required |
| 4 | disabled capability or prohibited data/action | deny; approval cannot override policy |

Approval fatigue is measurable. Anthropic reported that Claude Code users accepted about 93% of permission prompts and that OS-level sandboxing reduced prompts by 84% in its internal usage; these are vendor observations, not cross-product constants [[36]](https://www.anthropic.com/engineering/claude-code-auto-mode) [[35]](https://www.anthropic.com/engineering/claude-code-sandboxing). Its 2026 action classifier evaluation reported 0.4% false positives, 17% false negatives on 52 real overeager actions, and 5.7% false negatives on 1,000 synthetic exfiltration attempts after a two-stage pipeline; the small, internal/synthetic datasets and product-specific threat model limit generalization [[36]](https://www.anthropic.com/engineering/claude-code-auto-mode). The lesson is not to remove approvals: reduce their frequency by creating a safe capability envelope, and retain explicit review for high-impact actions.

### 1.6 Sandboxing

Sandboxing is **containment**. It must cover both filesystem and network: a process with host-file access can steal secrets through an allowed network; a process with unrestricted local execution may find an alternate network path. Anthropic's implementation uses OS primitives such as Linux bubblewrap and macOS Seatbelt, plus a controlled egress proxy, and keeps high-value Git credentials outside the sandbox behind a scoped proxy [[35]](https://www.anthropic.com/engineering/claude-code-sandboxing).

| Isolation level | Typical mechanism | Strength and use | Residual risk/trade-off |
|---|---|---|---|
| Process only | UID, directory, language runtime | low-risk trusted jobs | weak tenant boundary; host/kernel and inherited handles exposed |
| Hardened container | namespaces, cgroups, capabilities, seccomp, read-only root | controlled single-tenant tools | shared kernel; mounts, socket, daemon, or privileged mode can collapse isolation |
| User-space kernel | gVisor `runsc` | stronger shared-host isolation for untrusted Linux workloads | syscall compatibility and performance costs; permitted mounts/network remain reachable |
| MicroVM | Firecracker/KVM | separate guest kernel for untrusted or multi-tenant execution | higher image/startup/memory and orchestration cost |
| Full VM | conventional hypervisor | strongest general-purpose isolation and endpoint separation | largest operational footprint; monitoring/EDR integration can be harder |

gVisor interposes a user-space application kernel between the workload and host kernel and explicitly says it is one defense layer, not a replacement for secure architecture [[37]](https://gvisor.dev/docs/architecture_guide/intro/) [[38]](https://gvisor.dev/docs/architecture_guide/security/). Firecracker was designed around lightweight microVMs and a reduced device model for multi-tenant serverless workloads [[39]](https://www.usenix.org/conference/nsdi20/presentation/agache). Kubernetes Pod Security Standards define Privileged, Baseline, and Restricted profiles; an agent executor should normally meet Restricted and additionally apply workload-specific network, identity, mount, and egress policy [[40]](https://kubernetes.io/docs/concepts/security/pod-security-standards/).

Minimum sandbox profile `[inferred]`:

- ephemeral instance per run or tenant; immutable image; non-root; no privilege escalation;
- no host PID/IPC/network namespace, Docker socket, cloud metadata, hostPath, SSH agent, browser profile, or ambient credentials;
- read-only root; explicit read/write mounts; fresh worktree; encrypted scratch; secure cleanup;
- CPU, memory, process, file-count, disk, I/O, wall-time, token, and outbound-byte limits;
- deny network by default; egress through an authenticated L7 proxy with destination, method, account, request-size, and data-classification rules;
- package/dependency access through a controlled, scanned, optionally pinned proxy;
- credential broker outside the sandbox; inject single-operation capability or proxy authenticated calls;
- syscall/capability restrictions, signed images/SBOM, vulnerability patching, and escape detection;
- preserve an audit trail outside the sandbox so the workload cannot edit it.

A domain allowlist alone is weak: an attacker may own an account, repository, path, bucket, issue, or webhook on an allowed domain. Anthropic describes an egress design failure where allowing a domain still allowed exfiltration to an arbitrary account; its remediation incorporated session-token provenance [[34]](https://www.anthropic.com/engineering/how-we-contain-claude). Authorize the **destination object and operation**, not only DNS name `[inferred]`.

### 1.7 Policy lifecycle

A policy system has four different policy families:

1. **Instruction policy:** natural-language behavioral expectations for the model. Probabilistic.
2. **Authorization policy:** deterministic decision over principal/action/resource/context. Enforceable at PEPs.
3. **Data policy:** classification, residency, retention, redaction, encryption, and permitted flows.
4. **Runtime policy:** sandbox filesystem, process, network, resource, image, and dependency limits.

Treat policies as code: owner, source, version, tests, schema validation, peer review, signed bundle, staged rollout, canary, decision log, expiry, exception workflow, rollback, and periodic access review `[inferred]`. The NIST AI RMF organizes governance through Govern, Map, Measure, and Manage, while its GenAI Profile adapts those functions to generative-AI risks [[6]](https://www.nist.gov/itl/ai-risk-management-framework) [[7]](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence). MITRE ATLAS provides a common knowledge base for mapping adversary techniques and defensive coverage [[8]](https://atlas.mitre.org/).

Machine policy must remain authoritative. Natural-language policy helps model behavior but cannot authorize an action. Avoid policies such as "allow safe commands" or "do not share sensitive data" without formal definitions. Define safe resources, actions, data classes, recipients, amounts, and state predicates `[inferred]`.

## 2. Token Economics & NFR Metrics

### 2.1 Security and utility metrics

Measure the complete system under benign, malicious, and ambiguous inputs. A guardrail that blocks all tools has perfect attack prevention and zero utility.

| Dimension | Metric definition `[inferred]` | Required slices |
|---|---|---|
| Injection security | attack success rate; policy-violating success; secret-exfiltration rate/bytes; unauthorized side effects | direct/indirect, source, modality, encoding, tool, model, language |
| Benign utility | policy-compliant task success; completion/progress; permitted-tool success | task/domain/risk tier; with and without guardrail |
| Over-defense | benign refusal/block rate; false-positive rate; unnecessary approval/escalation | user group, tool, policy, classifier version |
| Detector quality | precision, recall, FPR, FNR, PR-AUC, calibration, abstention | attack family, novelty, severity, base rate |
| Authorization | correct permit/deny; missing PEP rate; cross-tenant denial; stale-policy decision | action/resource/policy version/PDP topology |
| Approval | prompt rate, accept/deny/timeout, change after preview, rubber-stamp sequences | risk tier, reviewer, workload, time-on-task |
| Containment | sandbox escape, blocked FS/network attempts, unexpected egress, residue/leak rate | image, isolation runtime, dependency, tenant |
| Reliability | PDP/credential/egress availability; fail-open events; time to revoke/kill/reconcile | region, dependency, failure injection |
| Performance | guardrail p50/p95/p99; sandbox cold start; approval wait; total task latency | permitted, denied, escalated, cached |
| Economics | security cost/run, cost/1,000 actions, cost/policy-compliant success, incident loss avoided | model, tenant, risk tier, environment |

Benchmark at multiple levels: adversarial strings, retrieved documents, complete trajectories, actual tool state, and red-team campaigns. Static attack corpora can leak into model/defense training. AgentDojo and AgentDyn are more realistic because they combine benign tasks, tools, and hostile content, but neither represents every enterprise tool or attacker [[13]](https://arxiv.org/abs/2406.13352) [[18]](https://arxiv.org/abs/2602.03117). Run private, rotating tasks with canary secrets and deterministic environment assertions `[inferred]`.

### 2.2 Cost model

```text
guardrail_cost_per_run =
    classifier_model_tokens_and_inference
  + policy_decision_compute_and_cache
  + sandbox_startup_and_runtime
  + credential_broker_and_egress_proxy
  + approval_labor_and_wait_cost
  + trace_storage_redaction_and_review
  + false_positive_rework_and_abandonment

risk_adjusted_unit_cost =
  (run_cost + expected_security_loss + review_cost) / policy_compliant_successes
```

Use invoices and measured compute, not token prices alone. A two-stage detector can route most traffic through a cheap filter and invoke an expensive reasoner only for suspected actions; Anthropic's described implementation also exploits prompt-cache overlap between stages [[36]](https://www.anthropic.com/engineering/claude-code-auto-mode). Do not let caching cross tenants, policy versions, identities, resource state, or sensitive prompts. A cached security decision is valid only when all authorization-relevant inputs and revocation state are unchanged `[inferred]`.

Latency budget `[inferred]`:

```text
T_action = T_normalize + T_policy + T_risk + T_approval
         + T_credential + T_sandbox_queue/start + T_tool + T_reconcile + T_audit
```

Measure denied paths as well as successful paths. A slow denial enables resource-exhaustion attacks. Budget model detectors separately from deterministic PDP latency, and degrade safely when either fails.

> ⚠️ Limited public data available for this dimension. There is no comparable public dataset of production p50/p95/p99 policy-decision latency, sandbox cold-start overhead, human-approval labor, and total security cost per 1,000 agent actions across providers. Capacity and unit economics must be measured in the target stack.

### 2.3 Security SLOs and release gates

Set explicit, risk-based gates rather than adopting the following as universal numbers `[inferred]`:

```text
release_eligible =
    no_known_critical_policy_bypass
AND no_cross_tenant_success_in_release_suite
AND attack_success_upper_bound <= risk_tier_ceiling
AND benign_task_success_delta >= allowed_regression
AND detector_FPR <= friction_budget
AND action_PEP_coverage == 100_percent_for_in_scope_tools
AND sandbox_profile_and_egress_tests_pass
AND p95_guardrail_latency <= action_SLO
AND revocation_and_kill_switch_drills_pass
```

For rare critical harms, report numerator, denominator, and confidence bounds; zero observed attacks is not proof of zero risk. Keep attack success, utility, cost, and latency separate or show a Pareto frontier. Do not average away one successful cross-tenant write with many correctly blocked low-risk reads `[inferred]`.

### 2.4 Audit and privacy telemetry

Each event should include run/session/step/span IDs; tenant; authenticated user and workload; model, prompt, tool, schema, sandbox-image, detector, and policy versions; content provenance/classification; normalized action and argument hash; resource; PDP decision/reason/obligations; credential audience/expiry without the secret; approval identity and binding; model/tool tokens, cost, and latency; sandbox/egress decision; external outcome/receipt; retries; and artifact hashes `[inferred]`.

OpenTelemetry's GenAI span conventions warn that tool arguments/results can contain sensitive information and make raw content capture opt-in rather than default [[43]](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md). OPA decision logs support masking sensitive fields before upload [[33]](https://www.openpolicyagent.org/docs/management-decision-logs). Presidio combines regex, deny lists, checksums, rule logic, NER, and contextual analysis and can redact, replace, hash, or encrypt identified entities; detection remains imperfect and domain-specific [[42]](https://microsoft.github.io/presidio/text_anonymization/).

Keep a minimally redacted security audit store separate from tightly access-controlled raw artifacts. Protect log integrity, encrypt in transit/at rest, apply regional/tenant retention, and record access to the evidence itself. Excessive redaction destroys forensic value; excessive capture creates a second data breach `[inferred]`.

## 3. Distributed Resilience & State

### 3.1 Authorization is a per-action state machine

A login-time check is insufficient for a long-running agent. Reauthorize each side effect using current user status, tenancy, delegation, resource ownership, policy version, cumulative spend, approval, and revocation state `[inferred]`.

```text
PROPOSED
  -> NORMALIZED
  -> POLICY_ALLOWED or DENIED
  -> APPROVAL_REQUIRED -> APPROVED or DENIED/EXPIRED
  -> CAPABILITY_ISSUED
  -> EXECUTING
  -> COMMITTED or FAILED or UNKNOWN
  -> RECONCILED
```

Persist the transition and idempotency key before external execution. For an ambiguous timeout, query the external receipt/state before retrying. Automatically retry safe reads; retry writes only when the provider supports idempotency or reconciliation. Never let an agent infer that "timeout" means "not executed" `[inferred]`.

### 3.2 PDP and policy distribution

OPA describes co-located/sidecar PDPs as low-latency and resilient to network loss, while centralized deployment simplifies some management at the cost of a remote dependency [[31]](https://www.openpolicyagent.org/docs/deploy). A production hybrid uses signed, versioned policy bundles near PEPs plus a central policy administration point and decision-log sink `[inferred]`.

- Validate and sign bundles; distribute monotonically; retain last-known-good.
- Include policy version in every decision and approval.
- Fail closed for writes and sensitive reads. A declared, narrow read-only degraded mode may use a fresh cached policy for non-sensitive resources.
- Block high-risk execution when policy freshness exceeds its SLO.
- Push revocation epochs/deny lists quickly; short credential TTL bounds stale authority.
- Abort or reauthorize pending high-risk actions after a material policy/resource change.
- Test inconsistent regional versions, partitions, rollback, corrupted bundles, clock skew, and PDP overload.

Cedar's formalized policy language and schema validation reduce ambiguity, while OPA/Rego supports broad infrastructure/application policy. Neither makes the surrounding PEP complete or the business policy correct [[27]](https://docs.cedarpolicy.com/) [[30]](https://arxiv.org/abs/2403.04651) [[32]](https://www.openpolicyagent.org/docs/deploy/k8s).

### 3.3 Credentials, revocation, and delegation

Issue an operation-specific capability after policy and approval, redeemable only by the intended workload at the intended proxy/resource, with a short expiry and nonce. Keep provider credentials at the broker/proxy. This follows the same pattern as a sandboxed Git client using a narrow credential to a proxy that validates repository and branch before attaching the actual GitHub credential [[35]](https://www.anthropic.com/engineering/claude-code-sandboxing).

For multi-agent delegation, transmit task, evidence references, capability set, budget, expiry, and parent trace ID. The child receives the intersection of parent authority and task policy. Recheck on return because the child may have read hostile content. Anthropic's auto-mode design checks subagent delegation and return for this reason [[36]](https://www.anthropic.com/engineering/claude-code-auto-mode). Do not accept an agent-generated statement of its own permissions `[inferred]`.

### 3.4 Failure isolation and recovery

Partition queues and budgets by tenant/risk class; apply admission control, concurrency caps, tool-specific circuit breakers, and egress quotas. A runaway low-value research task must not exhaust the PDP or sandbox pool needed to stop production actions `[inferred]`.

Emergency controls:

- revoke workload/user capabilities and OAuth grants;
- disable a tool/action/resource/policy version globally or by tenant;
- terminate active sandboxes and invalidate approval leases;
- freeze writes while allowing forensic read access;
- rotate credentials and quarantine artifacts;
- replay signed audit events to enumerate possibly committed effects;
- reconcile or compensate external state using receipts and domain workflows.

Backups and logs are recovery controls, not permission to allow dangerous actions. Test kill switches and restore/compensation procedures in game days.

## 4. Enterprise Security & Governance

### 4.1 Governance operating model

Map responsibilities `[inferred]`:

| Owner | Accountabilities |
|---|---|
| Product/domain owner | intended use, prohibited actions, task utility, user disclosure |
| Security | threat model, red team, sandbox/egress baseline, incident response |
| IAM/platform | principal/workload identity, credential broker, revocation, tenant boundaries |
| Policy owner | authorization/data/runtime policy semantics, tests, review and exceptions |
| Model/agent team | prompts, tool schemas, provenance handling, detector and trace evals |
| Data/privacy/legal | classification, purpose, residency, retention, regulatory obligations |
| SRE | SLOs, capacity, failure drills, circuit breakers, rollback, audit delivery |
| Independent approver/audit | high-risk release and exception review, evidence sampling |

Maintain an inventory of models, agents, tools, MCP servers, data sources, credentials, sandboxes, policies, and downstream recipients. Classify actions by reversibility, externality, data sensitivity, and blast radius. Connect NIST Govern/Map/Measure/Manage evidence to conventional secure SDLC and incident response [[6]](https://www.nist.gov/itl/ai-risk-management-framework) [[7]](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence).

### 4.2 MCP and third-party tools

Treat every MCP server as a third-party application and every tool description/result as untrusted data. The MCP authorization specification builds on OAuth, including protected-resource metadata and resource/audience binding; implement the exact version supported by client and server and do not pass a client's bearer token through to arbitrary downstream services [[21]](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization). OpenAI recommends keeping MCP tool approvals enabled for consequential operations [[15]](https://developers.openai.com/api/docs/guides/agent-builder-safety).

Onboard a tool only after reviewing publisher, code/artifact provenance, update channel, schemas, auth scopes, data handling, tenancy, egress, destructive actions, idempotency, availability, and audit support. Pin versions or digests; continuously detect tool-name/schema/description drift. A description that says "read-only" is not enforcement `[inferred]`.

### 4.3 Data governance

Before model access, classify and minimize data. Retrieve only permitted tenant rows/fields and redact secrets that the task does not require. Keep customer-managed keys, signing keys, production credentials, and raw regulated data outside model context where possible. Propagate classification and purpose tags through retrieval, memory, subagents, tools, logs, and exports `[inferred]`.

Memory creates persistence risk: a successful injection may be written as a future instruction. Separate factual memory from executable policy, attach origin/trust/expiry, require validation before promotion, scan on read/write, and give users/admins deletion and audit controls. Never let retrieved memory alter the authorization policy `[inferred]`.

### 4.4 Assurance program

Use four evidence streams:

1. **Design assurance:** data-flow diagram, attack tree, permission matrix, policy/sandbox specification.
2. **Build assurance:** schema/policy tests, SAST/dependency/image scanning, secret scanning, signed artifacts/SBOM.
3. **Behavioral assurance:** benign and adversarial agent evals, adaptive red team, model/tool/version regression.
4. **Operational assurance:** access reviews, decision-log sampling, anomaly detection, incident and recovery drills.

OWASP's Excessive Agency category emphasizes limiting functionality, permissions, and autonomy rather than expecting the model to decline inappropriate actions [[4]](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/). The OpenAI ChatGPT agent system card likewise describes layered safeguards for browser, terminal, connectors, and high-impact actions rather than one prompt defense [[20]](https://openai.com/index/chatgpt-agent-system-card/). Vendor system cards are useful architecture evidence, not independent certification.

## 5. Production Failure Modes

| # | Failure | Prevention / detection / containment `[inferred]` |
|---:|---|---|
| 1 | Direct user prompt overrides task/policy | instruction hierarchy; detector; deterministic action policy |
| 2 | Web/email/document indirect injection | provenance labels; isolate evidence; scan; no action authority from content |
| 3 | Cross-modal hidden instruction | OCR/metadata/image adversarial tests; modality-aware detector; action PEP |
| 4 | Encoded, fragmented, multilingual, or typoglycemic attack | normalization plus ensemble tests; never rely on pattern filters alone |
| 5 | Multi-turn attack gradually changes intent | immutable goal/policy anchor; trajectory anomaly detection; reauthorization |
| 6 | Injected content is saved to long-term memory | quarantine source-tagged memory; validation/expiry; delete poisoned entry |
| 7 | Ticket, issue, or internal wiki is incorrectly trusted | trust based on authenticated authority, not domain/location |
| 8 | Tool name/description/schema is poisoned or changes | registry allowlist, digest/version pinning, schema diff and reapproval |
| 9 | Model reveals system prompt or secret context | do not put secrets in prompts; output DLP/canaries; rotate exposed secret |
| 10 | Exfiltration through URL, rendered Markdown, image, or callback | safe renderer; strip active URLs; destination-aware egress; DLP |
| 11 | Allowed domain used with attacker-controlled account/path | authorize origin + account/resource/operation; authenticated proxy |
| 12 | Agent becomes confused deputy for a low-privilege user | intersect user/workload/task authority at every action |
| 13 | OAuth token has broad scopes or wrong audience | resource indicator, least scope, sender constraint, short TTL |
| 14 | Agent discovers credentials in files/environment | credentials outside sandbox; mount denial; secret-access alert and rotation |
| 15 | Revoked user retains a cached capability | revocation epochs, short leases, per-action recheck, terminate run |
| 16 | PDP unavailable and application fails open | fail closed for writes/sensitive reads; narrow documented degraded mode |
| 17 | Conflicting/shadowed policy permits action | formal semantics, forbid precedence, decision tests and explain output |
| 18 | Policy/schema/resource model drifts | CI validation, compatibility tests, signed version coupling, rollback |
| 19 | User rubber-stamps repeated approvals | reduce prompts via sandbox; risk grouping; explicit transaction preview |
| 20 | Approved arguments change before execution | bind hash/resource/account/expiry; reapprove on any material change |
| 21 | Resource changes between authorization and use (TOCTOU) | conditional write/version check; transaction; reauthorize at commit |
| 22 | Sandbox escape through kernel/runtime flaw | patched isolation, gVisor/microVM for risk, no host secrets, escape telemetry |
| 23 | Host mount, socket, metadata, or browser profile exposes authority | deny mounts/sockets/metadata; explicit minimal filesystem |
| 24 | DNS rebinding, redirect, proxy bypass, or alternate protocol evades egress | proxy all egress; resolve/pin/validate redirects; block raw sockets/DNS tunnels |
| 25 | Fork bomb, disk fill, huge output, or infinite loop | cgroup/quota/time/token/output limits; terminate and clean instance |
| 26 | Ephemeral cleanup fails and next tenant reads residue | per-run encrypted storage; destroy/verify; tenant-isolated pool |
| 27 | Package install or generated code compromises executor | deny arbitrary install; pinned/scanned proxy; ephemeral sandbox; no prod creds |
| 28 | Trace/log captures secrets or regulated data | opt-in content, field allowlist, redaction, encryption and access audit |
| 29 | Detector misses an adaptive attack | rotating private red team; action policy and containment independent of detector |
| 30 | Detector over-blocks benign work | measure FPR/utility slices; staged decision; retry/escalate; version rollback |
| 31 | Model upgrade regresses injection robustness | fixed + rotating adversarial suite; shadow/canary; rollback model/prompt |
| 32 | Retry duplicates payment/message/delete | idempotency key; receipt and reconciliation before retry |
| 33 | Context compression drops safety constraints | re-inject immutable policy outside summary; verify before action |
| 34 | Subagent receives broader scope than parent | capability intersection, budget/expiry, handoff authorization and return scan |
| 35 | Isolation removes EDR/forensic visibility | external telemetry, immutable audit, controlled forensic snapshot |
| 36 | Kill switch exists but does not revoke in-flight work | game-day testing; lease invalidation; executor checks before commit |

The same-origin policy is not automatically preserved by an agentic browser: a 2026 study of seven agentic browsers found one complete proof of concept in ChatGPT Atlas and the necessary preconditions in others, showing that an injected agent can relay information across origins unless the action interface enforces origin-aware information flow [[41]](https://agent-security.cs.washington.edu/agentic_browsers_sop.html). Browser automation therefore needs per-origin credentials, storage partitions, source-to-destination flow policy, and approval for cross-origin disclosure `[inferred]`.

## 6. Enterprise System Design Scenarios

### 6.1 Web research agent

**Goal:** search the public web and produce an evidence-backed report from internal and external sources.

**Design:** The browser runs in an ephemeral sandbox with no internal credentials and deny-by-default egress through a URL/protocol/size-controlled proxy. Retrieval stores raw content in an evidence store with origin, timestamp, trust, and content hash. The model can cite evidence but has no email, publish, shell, or write-capable internal tools. Internal search applies tenant ACLs before retrieval. Output rendering neutralizes active markup and unsafe links. DLP scans export; a human approves any external publication `[inferred]`.

**Why:** prompt injection may corrupt analysis, but the separate evidence-only plane prevents it from converting that corruption into a privileged side effect. Evaluate factual task utility, citation support, direct/indirect ASR, unsafe URL emission, internal-to-external data flow, latency, and cost.

### 6.2 Enterprise coding agent

**Goal:** fix an issue, run tests, and open a pull request without reaching production.

**Design:** Create a fresh ephemeral worktree inside gVisor or a microVM. Mount only the repository/branch; keep home directory, SSH agent, cloud metadata, Docker socket, signing keys, and production configuration absent. Default-deny network; permit dependency access only through a pinned/scanned proxy. A Git proxy holds the real token and permits reads plus push only to the assigned branch. A PEP blocks direct main push, force push, release, deploy, and secrets access. Generated code and tests execute under CPU/memory/process/time/output quotas. A human reviews the diff before merge `[inferred]`.

**Why:** source files, issue text, compiler output, and dependencies are all injection/supply-chain surfaces. Sandboxing limits compromise; branch-scoped proxy credentials limit confused-deputy behavior. Measure test success, forbidden-file/network attempts, dependency provenance, unauthorized Git operations, sandbox escape probes, resource use, and review burden.

### 6.3 Browser procurement or payment agent

**Goal:** purchase an approved item or pay a known invoice.

**Design:** Bind the user and tenant to an isolated browser profile. Retrieval/search cannot access payment credentials. The checkout tool takes a typed request with merchant account, SKU/invoice, quantity, amount, currency, address, and idempotency key. The PDP checks procurement policy, vendor allowlist, budget, owner, cumulative spend, and separation of duties. The user sees a fresh transaction preview; approval is bound to exact values and expiry. A payment proxy supplies a single-use token only after approval. The agent cannot read the payment credential. Reconcile against the processor receipt before any retry `[inferred]`.

**Why:** a page can inject instructions, change price, or induce exfiltration. Origin-aware data-flow policy, deterministic amount/vendor checks, transaction-bound approval, and single-use capability contain those failures. Measure unauthorized purchase rate, price/recipient mismatch, ASR, approval accuracy/fatigue, duplicate rate, and time to revoke.

### 6.4 Data and MCP analytics agent

**Goal:** answer business questions using warehouse and approved SaaS tools without exposing cross-tenant or row-level data.

**Design:** Register and pin approved MCP servers; treat tool descriptions/results as untrusted. OAuth tokens are audience/resource-bound and read-only by default. The warehouse enforces tenant row-level and column-level security independently of prompts. A query gateway parses SQL, rejects writes/unsafe functions, applies scan/cost/time/row limits, and stores a query receipt. PII is minimized or aggregated before the model; raw exports require policy and bound approval. Tool calls and decision logs omit raw result content by default. A separate governed workflow handles writes `[inferred]`.

**Why:** prompt injection can request a valid-looking query, and model-written SQL can be wrong even without an attacker. Resource enforcement and data policy make the model incapable of bypassing tenant and field boundaries. Measure cross-tenant attempts/success, unauthorized columns, data scanned, query cost/latency, PII leakage, result correctness, and policy availability.

### 6.5 Production readiness checklist

- Threat model includes direct, indirect, cross-modal, memory, tool, credential, exfiltration, and supply-chain paths.
- Every side-effecting tool is behind a PEP; there is no alternate SDK, shell, browser, or network path.
- User, workload, tenant, task, resource, and action identity are independently available to the PDP.
- Policies default deny, are schema-validated/versioned/signed/tested, and fail closed for high-risk actions.
- Credentials are short-lived, audience/resource-scoped, kept outside model context/sandbox, and revocable.
- Approval is risk-tiered and bound to exact transaction state; prohibited actions cannot be approved.
- Sandboxes isolate filesystem and network, deny ambient authority, cap resources, and clean tenant state.
- Egress policy validates destination object/operation, not merely domain.
- Untrusted provenance survives retrieval, compression, memory, handoff, and rendering.
- Logs support forensics without becoming an uncontrolled store of secrets and PII.
- Evals report attack success and benign utility, including adaptive/private tests and state assertions.
- PDP outage, stale policy, timeout-after-write, revocation, sandbox escape, and kill-switch drills pass.
- Model, prompt, tool, policy, detector, and sandbox upgrades use shadow/canary release and rollback.

## Interview Preparation

1. **Why can prompt injection not be solved with delimiters?** Natural language remains both instructions and data; delimiters are interpreted probabilistically. Use them as a robustness hint, then enforce actions outside the model.
2. **What is the difference between a guardrail and authorization?** A guardrail may classify or steer behavior; authorization is a deterministic permit/deny decision over authenticated principal, action, resource, and context at a complete enforcement point.
3. **How do you secure an agent that must read arbitrary web content and send email?** Separate research and action capabilities, preserve provenance, prevent the browsing context from directly authorizing email, apply recipient/data policy, DLP/egress controls, and bind approval to the exact message and recipients.
4. **Why is human approval insufficient?** Users approve habitually, previews can omit real effects, and arguments can change after approval. Reduce prompts with safe capability envelopes and cryptographically/server-side bind consequential approvals.
5. **What does a sandbox protect?** It contains process, filesystem, network, and resource impact after compromise. It does not decide whether an allowed business action is intended.
6. **How do you choose container, gVisor, or microVM?** Match isolation to tenant separation, code trust, kernel risk, compatibility, startup, and cost; then remove mounts, ambient credentials, and unrestricted egress regardless of runtime.
7. **How do you evaluate injection defense?** Measure attack success and policy-violating side effects alongside benign task utility, FPR/FNR, latency, and cost across dynamic tool environments, adaptive attacks, sources, modalities, and models.
8. **How should agent permissions delegate to subagents?** Pass only the intersection of parent authority and child task, with explicit tools/resources, budget, expiry, and trace; reauthorize each action and scan the return path.
9. **How do you handle policy-service failure?** Fail closed for writes and sensitive reads; optionally permit a narrow, documented, fresh-cache read-only mode. Preserve last-known-good signed policy and test recovery.
10. **What is the strongest architectural principle?** Assume the model may be compromised and ensure a compromised model still lacks the capability to cause unacceptable impact.

## Sources

1. [OWASP GenAI LLM Top 10 2026 release](https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/)
2. [OWASP LLM01:2026 Prompt Injection](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/blob/main/2026/final/LLM01_PromptInjection.md)
3. [OWASP Prompt Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
4. [OWASP LLM06:2025 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
5. [NIST AI 100-2e2025: Adversarial Machine Learning Taxonomy](https://csrc.nist.gov/pubs/ai/100/2/e2025/final)
6. [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework)
7. [NIST AI 600-1: Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence)
8. [MITRE ATLAS](https://atlas.mitre.org/)
9. [OWASP Top 10 for LLM Applications 2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)
10. [OWASP Web Application Security Top 10:2025](https://owasp.org/Top10/)
11. [OWASP GenAI LLM Top 10 2026 preface](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/blob/main/2026/final/LLM00_Preface.md)
12. [Greshake et al.: More than you've asked for — Indirect Prompt Injection](https://arxiv.org/abs/2302.12173)
13. [Debenedetti et al.: AgentDojo](https://arxiv.org/abs/2406.13352)
14. [Debenedetti et al.: CaMeL](https://arxiv.org/abs/2503.18813)
15. [OpenAI: Safety in building agents](https://developers.openai.com/api/docs/guides/agent-builder-safety)
16. [OpenAI API: Safety best practices](https://developers.openai.com/api/docs/guides/safety-best-practices)
17. [OpenAI: The Instruction Hierarchy](https://openai.com/index/the-instruction-hierarchy/)
18. [AgentDyn: Toward Dynamic and Stateful LLM Agent Security Evaluation](https://arxiv.org/abs/2602.03117)
19. [Hines et al.: Defending Against Indirect Prompt Injection with Spotlighting](https://arxiv.org/abs/2403.14720)
20. [OpenAI: ChatGPT Agent System Card](https://openai.com/index/chatgpt-agent-system-card/)
21. [Model Context Protocol Authorization Specification, 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
22. [RFC 9700: Best Current Practice for OAuth 2.0 Security](https://www.rfc-editor.org/rfc/rfc9700.html)
23. [RFC 8707: Resource Indicators for OAuth 2.0](https://www.rfc-editor.org/rfc/rfc8707.html)
24. [RFC 9449: OAuth 2.0 Demonstrating Proof of Possession](https://www.rfc-editor.org/rfc/rfc9449.html)
25. [SPIFFE Workload API](https://spiffe.io/docs/latest/spiffe-specs/spiffe_workload_api/)
26. [SPIFFE Concepts](https://spiffe.io/docs/latest/spiffe/concepts/)
27. [Cedar Policy Language documentation](https://docs.cedarpolicy.com/)
28. [Cedar Authorization](https://docs.cedarpolicy.com/auth/authorization.html)
29. [Cedar Schema and Policy Validation](https://docs.cedarpolicy.com/policies/validation.html)
30. [Cedar: A New Language for Expressive, Fast, Safe, and Analyzable Authorization](https://arxiv.org/abs/2403.04651)
31. [Open Policy Agent: Deployment](https://www.openpolicyagent.org/docs/deploy)
32. [Open Policy Agent on Kubernetes](https://www.openpolicyagent.org/docs/deploy/k8s)
33. [Open Policy Agent Decision Logs](https://www.openpolicyagent.org/docs/management-decision-logs)
34. [Anthropic: How we contain Claude](https://www.anthropic.com/engineering/how-we-contain-claude)
35. [Anthropic: Making Claude Code more secure with sandboxing](https://www.anthropic.com/engineering/claude-code-sandboxing)
36. [Anthropic: How we built Claude Code auto mode](https://www.anthropic.com/engineering/claude-code-auto-mode)
37. [gVisor Architecture Guide: Introduction](https://gvisor.dev/docs/architecture_guide/intro/)
38. [gVisor Security Model](https://gvisor.dev/docs/architecture_guide/security/)
39. [Agache et al.: Firecracker — Lightweight Virtualization for Serverless Applications](https://www.usenix.org/conference/nsdi20/presentation/agache)
40. [Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
41. [Agentic Browsers Circumvent the Same-Origin Policy](https://agent-security.cs.washington.edu/agentic_browsers_sop.html)
42. [Microsoft Presidio: Text anonymization](https://microsoft.github.io/presidio/text_anonymization/)
43. [OpenTelemetry GenAI Agent and Tool Spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
44. [CaMeL reference implementation](https://github.com/google-research/camel-prompt-injection)

## Research Gaps

> ⚠️ Limited public data available for this dimension. Public prompt-injection benchmarks cover only a small subset of enterprise tools, languages, modalities, identities, and adaptive attackers; benchmark success does not establish production security.

> ⚠️ Limited public data available for this dimension. Comparable incident rates, loss magnitudes, cross-tenant near misses, and bypass disclosures are rarely published, so residual-risk estimates require internal red-team and production telemetry.

> ⚠️ Limited public data available for this dimension. No neutral cross-runtime study reports current sandbox escape rate, compatibility, cold-start, steady-state overhead, EDR visibility, and total operating cost for containers, gVisor, microVMs, and full VMs under identical agent workloads.

> ⚠️ Limited public data available for this dimension. Human-approval effectiveness, fatigue, false consent, staffing cost, and accessibility across consequential agent domains lack representative longitudinal studies.

> ⚠️ Limited public data available for this dimension. Public policy-engine benchmarks do not capture complete agent PEP coverage, policy-authoring error, distributed staleness, revocation delay, or organization-specific authorization semantics.

> ⚠️ Limited public data available for this dimension. Vendor-reported guardrail metrics use different models, classifiers, attack distributions, policies, and utility definitions; they should not be compared as a common leaderboard.
