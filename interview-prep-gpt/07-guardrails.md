# Guardrails

## Why It Matters
Guardrails matter because the model is not a security boundary. A strong system prompt, a moderation classifier, or a fine-tuned refusal behavior can reduce risk, but none of those should be trusted as the final enforcement layer when an agent can read private data or trigger side effects.

The best interview framing is defense in depth:

- probabilistic detection layers
- deterministic policy enforcement
- sandboxing and egress control
- human approval for consequential actions

That framing immediately separates guardrails from "just prompt the model better."

## Mental Model
Use three planes:

- Control plane: identity, policy, approval rules, credential issuance, audit
- Data plane: user inputs, retrieved docs, tool outputs, model context
- Sandbox plane: isolated runtime for code, browser actions, or other risky execution

Then add the core principle:

`the model proposes, deterministic code disposes`

In other words, the model can suggest an action, but a policy enforcement layer decides whether that action is allowed.

The other high-value mental model is Simon Willison's lethal trifecta:

- private data
- untrusted input
- outbound capability

If all three are present, exfiltration is structurally possible unless you add stronger controls.

## Architecture / Flow
```text
user input -> input filters -> orchestrator
          -> PDP/PEP authorization -> tool or MCP gateway
          -> sandbox and egress policy
          -> output filters and DLP
          -> user + audit log
```

Break the architecture into responsibilities:

1. Detection layers
   - jailbreak and injection classifiers
   - topic or safety screening
   - suspicious output detection

2. Enforcement layers
   - RBAC
   - policy engine
   - schema validation
   - destination allowlists

3. Containment layers
   - sandbox isolation
   - network egress restrictions
   - short-lived credentials
   - immutable audit

Good interview answers emphasize that these layers do different jobs and should fail differently.

## Key Concepts
- Prompt injection paths:
  - direct user injection
  - indirect injection through retrieved docs, pages, or emails
  - tool-result injection
  - tool-description poisoning
  - MCP resource injection
  - memory poisoning

- Why RAG and fine-tuning do not solve injection:
  - RAG may import hostile text into context
  - fine-tuning may improve robustness but does not create a formal instruction/data boundary

- PEP and PDP:
  - PEP is the policy enforcement point on each effectful action
  - PDP is the policy decision engine that returns allow, deny, or require-approval
  - the model should be neither

- Tool RBAC:
  - separate read from write
  - separate low-impact from irreversible actions
  - apply least privilege per tool, not just per agent

- MCP security specifics:
  - OAuth 2.1-style authorization-code + PKCE defaults
  - RFC 8707 `resource` indicators so tokens are bound to the target API or resource server
  - no token passthrough to downstream APIs
  - hash-pin tool definitions or at least detect drift

- Sandboxing:
  - shared-kernel containers are convenient, but hostile multi-tenant execution usually calls for a stronger boundary
  - gVisor interposes a userspace application kernel between the workload and host syscalls
  - Firecracker provides a microVM boundary with its own guest kernel
  - WASM is good for narrow interpreters and policy engines, not general Linux workloads

- HITL:
  - best for irreversible, high-impact, or ambiguous actions
  - approvals must be bound to exact normalized arguments and expiry
  - otherwise approval fatigue and TOCTOU bugs create bypasses

- Fail-open versus fail-closed:
  - authorization and spend control should fail closed
  - some low-risk content-classification layers may fail open with alerting
  - you need a published matrix, not incident-time improvisation

- Egress and DLP:
  - destination control matters as much as domain control
  - an allowed domain can still host an attacker-controlled account or path

## Metrics and Formulas to Memorize
- OWASP keeps prompt injection at `LLM01` in `2026`

- Anthropic Constitutional Classifiers:
  - jailbreak ASR `86% -> 4.4%`
  - over-refusal `+0.38 pp`
  - compute overhead `+23.7%`

- CaMeL:
  - `77%` task completion with provable security
  - versus `84%` undefended utility

- PlanGuard:
  - ASR `72.8% -> 0%`
  - FPR `1.49%`

- Llama Guard 3 response classification:
  - `F1 0.939`
  - `FPR 0.040`

- Anthropic usage anchor from local material:
  - users accepted about `93%` of permission prompts
  - sandboxing reduced prompts by `84%`

- Firecracker anchors:
  - start `<=125 ms`
  - VMM overhead `<=5 MiB`

- GKE Agent Sandbox anchors:
  - `p90 <=200 ms`
  - about `300` sandboxes per second per cluster

- Bedrock Guardrails pricing anchors from local material:
  - content filters `0.15` per 1,000 text units
  - PII `0.10`
  - automated reasoning `0.17`

These numbers are useful only if you present them as benchmark or vendor anchors, not universal guarantees for your environment.

## Trade-offs and Failure Modes
- Treating the system prompt as the boundary:
  this is the classic mistake. Prompts are instructions, not authorization.

- PDP fail-open:
  an auth timeout that becomes an allow is effectively a platform-wide zero-day.

- Tool-description poisoning:
  the model can trust malicious metadata before it ever executes the tool.

- Domain-only allowlists:
  "allowed domain" is weaker than "allowed destination object and operation."

- Approval fatigue:
  too many prompts train users to approve without review.

- Memory poisoning:
  bad observations become durable authority if memory writes are not gated.

- Container-only isolation:
  shared-kernel sandboxes are often not enough for hostile multi-tenant execution.

- Overblocking:
  if FPR is high and workflows become painful, teams will disable the protections.

## Interview Q&A
**Q: Why can prompt injection not be "solved" the way SQL injection is solved?**  
A: Because the model does not have a strict instruction/data parser boundary. Guardrails reduce risk, but authorization and containment still have to live outside the model.

**Q: What is the lethal trifecta?**  
A: Private data, untrusted input, and outbound capability. If all three exist, you need serious containment and approval design.

**Q: What is the difference between a guardrail and authorization?**  
A: Guardrails are often probabilistic detectors or steering layers. Authorization is deterministic policy enforcement on actions and resources.

**Q: Why do RAG and fine-tuning not solve prompt injection?**  
A: RAG can import hostile content and fine-tuning does not create a trustworthy action boundary.

**Q: How would you secure an agent that reads email and can send email?**  
A: Split untrusted reading from privileged sending, put send behind policy and approval, and do not let the model that saw the raw hostile bytes own the outbound action.

**Q: How do you choose between gVisor and Firecracker?**  
A: Use gVisor when you want stronger isolation with container ergonomics. Use Firecracker when hostile multi-tenant code execution needs a stronger VM boundary.

**Q: What should fail closed?**  
A: Authorization, spend limits, sandbox creation for risky execution, and DLP on outbound tool calls.

**Q: What is the biggest anti-pattern in guardrail design?**  
A: Collapsing detection and enforcement into one model call and assuming that means the system is secure.

## Sources
- Local anchors:
  - `ai-roadmap/final/13-security-guardrails.md`
  - `ai-roadmap/final/02-context-engineering.md`
  - `ai-roadmap/final/05-agent-frameworks.md`
  - `ai-roadmap/final/14-observability.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [OWASP LLM Top 10 2026](https://genai.owasp.org/download/56857/?tmstv=1785822482)
  - [Anthropic Constitutional Classifiers](https://www.anthropic.com/research/constitutional-classifiers)
  - [CaMeL](https://arxiv.org/pdf/2503.18813)
  - [PlanGuard Paper](https://arxiv.org/html/2604.10134)
  - [Amazon Bedrock Guardrails Docs](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html)
  - [OAuth 2.1 Draft (IETF)](https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/)
  - [RFC 8707: Resource Indicators for OAuth 2.0](https://www.rfc-editor.org/rfc/rfc8707.html)
  - [gVisor Architecture Guide](https://gvisor.dev/docs/architecture_guide/intro/)
  - [Firecracker Official Site](https://firecracker-microvm.github.io/)
  - [GKE Agent Sandbox Launch Post](https://cloud.google.com/blog/products/containers-kubernetes/bringing-you-agent-sandbox-on-gke-and-agent-substrate)
  - [Llama Guard 3 8B Model Card](https://github.com/meta-llama/PurpleLlama/blob/main/Llama-Guard3/8B/MODEL_CARD.md)
