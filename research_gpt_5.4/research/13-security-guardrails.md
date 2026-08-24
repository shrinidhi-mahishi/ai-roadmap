# Research: Security & Guardrails - Prompt injection, permissions, sandboxing, policies

**Date researched**: 2026-08-21
**Sources consulted**: 9

---

## 1. System Topology & Mechanics

`Security & guardrails` appears in the local research corpus as a layered control plane around model reasoning and tool execution rather than as one isolated feature. The recurring layers are: `instruction trust boundaries`, `schema validation`, `approval checkpoints`, `authorization and identity propagation`, `sandboxed execution`, and `audit / trace capture` (`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`, `10-mcp-interoperability.md`) [inferred].

The local notes repeatedly separate `planning` from `privileged execution`. Planning-and-reasoning guidance argues that correctness verification is not the same as authorization for a side effect, while framework notes show approvals, guardrails, or human feedback placed around the actual tool call rather than around free-form model thought (`05-agent-frameworks.md`, `08-planning-reasoning.md`). The clean topology is therefore: let the model propose; let typed tools, policy checks, and approvals decide whether execution is allowed [inferred] (`04-agent-architecture.md`, `08-planning-reasoning.md`).

`Prompt injection` is treated structurally, not just behaviorally. The memory and planning notes both warn that untrusted content from browser pages, retrieved passages, or tool outputs must not be promoted into high-trust instruction channels (`07-memory.md`, `08-planning-reasoning.md`). The strongest local pattern is a channel split:

- `system / developer policy` for high-trust instructions
- `tool_result / retrieval content / page text` for low-trust evidence
- `structured outputs` for bounded model decisions

(`03-tool-use.md`, `07-memory.md`, `08-planning-reasoning.md`) [inferred]

For permissions and interoperability, the corpus positions `MCP` as the clearest protocol boundary. The MCP note describes host-to-tool/resource interoperability with OAuth-style authorization, PKCE, resource-bound tokens, and protected-resource metadata, while Azure retrieval notes show that knowledge bases exposed through `retrieve` or `MCP` still need permission-aware backends (`06-rag.md`, `07-memory.md`, `10-mcp-interoperability.md`). That means the protocol boundary is only safe when identity and access policy continue down to the actual data or action surface [inferred].

For sandboxing, the local tool-use and specialized-agent notes distinguish `API/function tools`, `browser/computer tools`, and `code-execution sandboxes` as different risk envelopes. API tools are the narrowest and most schema-governable; browser/computer loops expose the largest prompt-injection surface; server-side code execution gives tighter execution isolation but usually with constrained network or package access (`03-tool-use.md`, `11-specialized-agents.md`). In practice, the safest architecture prefers the narrowest executable surface that can still complete the task [inferred].

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for stable `p50/p95/p99` latency of guardrail-heavy agent workflows in the local research set. The strongest local evidence is on token overhead, approval pauses, tool-surface cost, cache behavior, and structural latency trade-offs rather than published end-to-end safety SLAs.

The local notes make clear that guardrails are not free. Tool schemas, approval prompts, policy instructions, tracing metadata, browser/computer tool declarations, and validator loops all consume context or extend the critical path (`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`). A useful first-order formula is:

```text
guardrailed_run_cost
  ~= model_tokens
   + tool_schema_tokens
   + policy_prefix_tokens
   + validation_or_guardrail_turns
   + approval_pause_overhead
   + sandbox_or_hosted_tool_fees
```

(`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`) [inferred]

The biggest fixed overhead in the local corpus comes from visual tool surfaces. The tool-use note reports that Anthropic browser-tool declarations add roughly `6,610-6,670` input tokens and computer-tool declarations add roughly `4,520-4,590` input tokens before screenshots, task text, and outputs (`03-tool-use.md`). That means the most security-sensitive automation pattern is also one of the most token-expensive ones [inferred].

Approval layers add latency even when they save risk. OpenAI-style approvals and resumable run state, CrewAI human-feedback pauses, and verifier/replanner loops all improve control, but each inserted gate lengthens the critical path by at least one decision step or one pause/resume cycle (`05-agent-frameworks.md`, `08-planning-reasoning.md`). For sensitive workflows, this usually represents a deliberate latency trade rather than an optimization failure [inferred].

Caching interacts strongly with policy design. The memory, tool-use, and architecture notes all imply that stable instruction prefixes, stable schemas, and stable server metadata are the easiest parts of a guarded workflow to cache (`03-tool-use.md`, `04-agent-architecture.md`, `07-memory.md`). Safety instructions that churn every turn reduce cache hits and raise both cost and latency, so enterprise policy blocks should be as stable and reusable as possible [inferred].

The local corpus does not provide strong apples-to-apples numbers for `PII redaction latency`, `policy-engine evaluation time`, or `sandbox startup time` across frameworks. It is much stronger on structural guidance than on benchmarked operational envelopes (`05-agent-frameworks.md`, `10-mcp-interoperability.md`).

## 3. Distributed Resilience & State

The security-relevant state split in the local notes is consistent: keep `workflow state` in sessions, checkpoints, or resumable run state; keep `capability access` behind structured tool or protocol boundaries; keep `durable knowledge` or memory behind governed retrieval layers (`05-agent-frameworks.md`, `07-memory.md`, `10-mcp-interoperability.md`). This matters because approval state, authorization state, and execution state fail differently [inferred].

For guarded execution, resumability is part of the safety model. The framework notes describe OpenAI run-state serialization around approvals, LangGraph checkpoint boundaries, ADK session/state separation, and CrewAI persistence plus human feedback (`04-agent-architecture.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`). Without durable state, a pause for review can degrade into replay ambiguity or duplicate execution risk [inferred].

The memory note adds an important resilience rule for security: `semantic memory writes should be treated as higher-risk than episodic logs`, because semantic memory is intended for future reuse across runs (`07-memory.md`). A poisoned episodic trace harms one workflow; a poisoned semantic memory entry can steer many later workflows [inferred].

Permission-aware retrieval is another state boundary. The RAG and memory notes show that governed knowledge systems should enforce authorization on reads, not only on index administration or endpoint exposure (`06-rag.md`, `07-memory.md`). This is a resilience property as much as a security one: if authorization is decoupled from retrieval execution, cached or replayed results can drift out of policy [inferred].

The multi-agent and interoperability notes also imply that approvals and auth decisions should not live only inside transient protocol sessions. Once systems use MCP servers or remote agents, independent failure domains appear around remote endpoints, transport, discovery metadata, and coordinator state (`09-multi-agent-systems.md`, `10-mcp-interoperability.md`). Durable policy outcomes, audit IDs, and timeout decisions therefore need to live above those transient connections [inferred].

> ⚠️ Limited public data available in the local research set for exactly-once guarantees on side-effecting guarded actions, immutable approval-event stores, or provider-internal policy replay journals.

## 4. Enterprise Security & Governance

### Prompt injection and trust boundaries

The local corpus is strongest on one rule: `treat external content as untrusted`. Browser content, screenshots, retrieved passages, tool outputs, and third-party text should remain in low-trust channels rather than being concatenated into high-trust instructions (`03-tool-use.md`, `07-memory.md`, `08-planning-reasoning.md`, `11-specialized-agents.md`). For research, browser, and data specialists, this is the primary defense against prompt injection [inferred].

### Permissions, scopes, and approval planes

For external tools and resources, `MCP` is the clearest Zero-Trust baseline in the local research set. The interoperability note summarizes OAuth-style authorization, PKCE, protected-resource metadata discovery, and resource-bound tokens for MCP over HTTP (`10-mcp-interoperability.md`). Framework notes add that OpenAI Agents SDK can require approval for MCP-backed tools and nested tool calls, while permission-aware retrieval systems still enforce their own role- or key-based access controls below the protocol layer (`05-agent-frameworks.md`, `06-rag.md`, `07-memory.md`, `10-mcp-interoperability.md`).

The planning and framework notes also distinguish `schema validity` from `authorization`. Strict schemas reduce malformed inputs, but a schema-valid action can still be unauthorized, over-scoped, or misaligned with user intent (`04-agent-architecture.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`). The defensible enterprise pattern is `strict schema -> policy check -> optional human approval -> execution` [inferred].

### Sandboxing and execution isolation

The tool-use note shows the clearest local sandbox hierarchy:

- `API/function tools`: narrowest authority and easiest to validate (`03-tool-use.md`)
- `server-side code execution`: sandboxed execution with tighter environmental control (`03-tool-use.md`)
- `browser/computer use`: largest attack surface because the model consumes and acts on untrusted visual/UI content (`03-tool-use.md`, `11-specialized-agents.md`)

The specialized-agent note synthesizes this into a practical rule: use the narrowest tool surface that can accomplish the task, and reserve browser/computer control for workflows that truly lack safe APIs (`03-tool-use.md`, `11-specialized-agents.md`) [inferred].

### Policies, auditability, and governance gaps

The strongest documented policy surfaces in the local corpus are approvals, human-feedback checkpoints, strict schemas, tracing, and permission-aware retrieval (`03-tool-use.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`, `10-mcp-interoperability.md`). The corpus is materially weaker on public details for `PII redaction pipelines`, immutable audit-log schemas, and fine-grained built-in RBAC hierarchies across frameworks:

> ⚠️ Limited public data available in the local research set for first-party `PII redaction` internals, compliance-grade `audit log` schemas, and formal `policy engines` that span prompts, tools, memory, and remote agents end to end (`05-agent-frameworks.md`, `09-multi-agent-systems.md`, `10-mcp-interoperability.md`).

## 5. Production Failure Modes

### Prompt injection through tools, pages, or retrieved content

This is the most explicit security failure mode in the local research set. Browser pages, screenshots, retrieved documents, and tool outputs can all carry hostile instructions, and the notes repeatedly warn against promoting that content into trusted instruction channels (`03-tool-use.md`, `07-memory.md`, `08-planning-reasoning.md`, `11-specialized-agents.md`).

### Schema-valid but unauthorized or unsafe actions

Strict schemas reduce malformed arguments, but they do not guarantee that the chosen target, scope, or side effect is permitted (`04-agent-architecture.md`, `08-planning-reasoning.md`). This creates a dangerous false sense of safety where the call parses correctly yet still violates business policy [inferred].

### Memory poisoning and permission drift

The memory note identifies durable-memory poisoning as a serious governance failure, especially when low-trust content is promoted into reusable semantic memory (`07-memory.md`). The RAG and interoperability notes add that permission-aware retrieval must remain attached to the actual read path; otherwise cached or replayed results can outlive the policy context that should have constrained them (`06-rag.md`, `07-memory.md`, `10-mcp-interoperability.md`) [inferred].

### Replay ambiguity after approvals or retries

Checkpointed and resumable systems improve safety, but they can still replay non-idempotent steps if approval state, tool results, or checkpoint boundaries are mishandled (`04-agent-architecture.md`, `05-agent-frameworks.md`, `11-specialized-agents.md`). For guarded workflows, `resume` semantics without idempotent side-effect handling can become a security bug, not just a reliability bug [inferred].

### Browser-agent observation drift

Browser and computer-use loops are unusually brittle because the visible environment can change between observation and action (`03-tool-use.md`, `11-specialized-agents.md`). In security-sensitive flows, stale observations can cause the right policy to be applied to the wrong screen or the wrong entity [inferred].

### Governance mismatch in multi-agent systems

The multi-agent note highlights that group behavior can diverge from single-agent safety assumptions, and that extra delegation boundaries introduce new auth, timeout, and observability surfaces (`09-multi-agent-systems.md`). A set of individually aligned specialists can still produce collectively unsafe execution if delegation permissions and audit surfaces are poorly scoped [inferred].

### Incident coverage

> ⚠️ Limited public data available for detailed RCA-style incident reports focused specifically on prompt-injection incidents, sandbox escapes, or policy-plane failures in the local research set. Most evidence is architectural guidance rather than production post-mortems.

## 6. Enterprise System Design Scenarios

### 6.1 Guardrail pattern matrix

| Pattern | Best fit | Strongest benefits | Main trade-offs |
| --- | --- | --- | --- |
| `Strict schema + API tool` | SaaS APIs, internal services, CRUD workflows | Lowest ambiguity; easiest validation and approval insertion (`03-tool-use.md`, `04-agent-architecture.md`) | Still needs authz and business-rule checks [inferred] |
| `Approval-gated tool execution` | Sensitive writes, code edits, infra actions | Clean separation between planning and execution (`05-agent-frameworks.md`, `08-planning-reasoning.md`) | Adds human or policy latency |
| `Permission-aware retrieval` | Internal docs, multi-tenant knowledge systems | Keeps access control at the read path (`06-rag.md`, `07-memory.md`) | Retrieval quality and auth context must both be preserved |
| `Sandboxed code execution` | Analysis, ETL, transformation, bounded code tasks | Stronger isolation than local arbitrary execution (`03-tool-use.md`) | Limited network/package freedom; weaker fit for live integrations |
| `Browser/computer automation with isolation` | API-less web or desktop workflows | Can automate otherwise inaccessible systems (`03-tool-use.md`, `11-specialized-agents.md`) | Highest injection risk, highest overhead, strongest need for approvals |

### 6.2 Recommended deployment patterns

**Pattern A: Enterprise copilot over internal systems**

Prefer `strict schema + API/MCP tools + approval gates` rather than browser automation. The local notes repeatedly show that interoperability and approvals are safer when the action surface is typed and permission-scoped (`03-tool-use.md`, `05-agent-frameworks.md`, `10-mcp-interoperability.md`).

**Pattern B: Research assistant over governed knowledge**

Use permission-aware retrieval and keep retrieved evidence in low-trust channels. Do not let citations, snippets, or tool outputs rewrite the instruction layer (`06-rag.md`, `07-memory.md`, `08-planning-reasoning.md`).

**Pattern C: Coding or ops agent with mutation powers**

Separate reasoning from execution with schema validation, approval or review checkpoints, sandboxed bounded tooling where possible, and durable run state for resume/audit (`03-tool-use.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`, `11-specialized-agents.md`).

**Pattern D: Browser-first workflow with no viable API**

Use dedicated isolated environments and assume page content is adversarial by default. This is the strongest use case for high-friction guardrails because the workflow combines prompt injection, observation drift, and high-impact actions (`03-tool-use.md`, `11-specialized-agents.md`) [inferred].

### 6.3 Capacity-planning heuristics

Useful first-order formulas synthesized from the local notes:

```text
guardrail_latency
  ~= planning
   + validation
   + approval
   + execution
   + audit / trace persistence
```

(`05-agent-frameworks.md`, `08-planning-reasoning.md`, `10-mcp-interoperability.md`) [inferred]

```text
policy_surface_risk
  rises as tool authority,
  context trust mixing,
  and delegation depth increase
```

(`07-memory.md`, `09-multi-agent-systems.md`, `11-specialized-agents.md`) [inferred]

```text
safe_automation_preference
  = choose the narrowest tool
    that satisfies the task
```

(`03-tool-use.md`, `11-specialized-agents.md`) [inferred]

### 6.4 Strongest practical conclusions

1. The strongest local security pattern is not one product feature but a layered stack: `strict trust boundaries + typed tools + authz + approvals + sandboxing + auditability`.
2. `Prompt injection` is treated most effectively when external content stays in low-trust channels and never silently upgrades into policy text or durable memory (`07-memory.md`, `08-planning-reasoning.md`) [inferred].
3. `Browser/computer agents` are the highest-risk and highest-overhead specialist type in the local corpus, so they should be a last resort behind API or retrieval-based options.
4. The largest public evidence gaps remain `PII redaction internals`, immutable `audit schemas`, and benchmarked enterprise guardrail latency under real production workloads.

## Sources

- [1] `03-tool-use.md` - Local research note covering strict tool schemas, browser/computer use risks, sandboxed code execution, tracing-adjacent guardrails, and tool-loop failure modes.
- [2] `04-agent-architecture.md` - Local research note covering control-plane versus data-plane separation, strict schema validation, checkpoint/replay risks, and security/governance framing.
- [3] `05-agent-frameworks.md` - Local research note covering approvals, human feedback, tracing, persistence, and framework-level governance surfaces.
- [4] `06-rag.md` - Local research note covering permission-aware retrieval, agentic retrieval, references, and knowledge-base access patterns.
- [5] `07-memory.md` - Local research note covering prompt-injection isolation, semantic-memory poisoning, governed memory writes, and permission-aware retrieval memory.
- [6] `08-planning-reasoning.md` - Local research note covering verifier loops, approval-gated execution, schema-valid but unsafe actions, and prompt-injection handling in planning systems.
- [7] `09-multi-agent-systems.md` - Local research note covering delegation permissions, remote failure/auth surfaces, and group-level governance risks.
- [8] `10-mcp-interoperability.md` - Local research note covering MCP authorization, resource-bound access, approval boundaries, and separation of workflow state from capability access.
- [9] `11-specialized-agents.md` - Local research note covering browser and coding specialist risk profiles, narrow-authority design, and sandboxing trade-offs.
