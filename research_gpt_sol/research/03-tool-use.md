# Research: Tool Use - APIs, Function Calling, Browser, Code Execution

**Date researched**: 2026-08-21
**Sources consulted**: 34

## Scope and evidence labels

This brief covers all four roadmap subtopics: APIs, function calling, browser use, and code execution. A plain statement is documented by a linked primary source or current official documentation. `[inferred]` marks architecture guidance derived from those mechanics rather than a vendor guarantee. Current prices and platform limits are point-in-time facts as of the research date; paper and benchmark results are not production SLAs.

## 1. System Topology & Mechanics

### Tool use is a proposal-execution-observation protocol

- A model does not execute a custom function merely by emitting its name. OpenAI's function-calling loop is: send schemas, receive one or more calls with JSON arguments and `call_id`, execute in the application, return `function_call_output` bound to that ID, and let the model continue. Anthropic describes the same contract for client tools: the model emits `tool_use`, the application runs it, then returns `tool_result`. [[1]](https://developers.openai.com/api/docs/guides/function-calling) [[2]](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)
- Gemini likewise separates provider-managed built-in tools from application-managed custom functions: built-ins run inside the provider call, while a custom call returns a name, arguments, and unique ID for the application to execute and return. [[3]](https://ai.google.dev/gemini-api/docs/tools)
- Toolformer formalized the learned decisions behind the interface: whether to call a tool, which tool, what arguments to supply, and how to incorporate its result. Those are separate failure surfaces; a syntactically valid call can still choose the wrong tool or cause the wrong business effect. [[4]](https://arxiv.org/abs/2302.04761)

```text
CONTROL PLANE
tool registry | schema/version registry | auth policy | approvals | budgets | eval gates
                                      |
DATA PLANE                           v
request -> model/tool router -> model -> proposed call -> policy/validation gateway
                                           |                 |
                                           |                 +-> API worker
                                           |                 +-> browser worker
                                           |                 +-> code sandbox
                                           v
                    append-only call/result trajectory <- normalized observation
                                           |
                                       next model turn
```

`[inferred]` The model is a probabilistic planner/caller. The **tool gateway** is the enforcement boundary: it resolves a versioned tool name, validates syntax and business invariants, authorizes the actual resource, obtains approval, attaches an idempotency key and deadline, dispatches, sanitizes the result, and records the attempt. Never let generated code call an unrestricted SDK client that bypasses this gateway.

### APIs and tool contracts

- OpenAPI 3.2 defines a language-neutral description of paths, operations, request/response schemas, and security schemes. It is useful as an upstream catalog, but an OpenAPI document is not an authorization policy and exposing every operation to the model creates excess capability. [[5]](https://spec.openapis.org/oas/latest.html)
- `[inferred]` Generate narrow model-facing tools from reviewed operations rather than passing a raw enterprise OpenAPI catalog. Rename ambiguous operations, remove server/auth fields, expose stable resource IDs, bound arrays/strings/pagination, document error shapes, and split reads from mutations (`get_invoice` versus `refund_invoice`). Preserve the source `operationId` and spec digest for auditability.
- JSON Schema constrains call shape, not meaning. OpenAI strict functions require all properties to be required and `additionalProperties: false`, with nullable types used for optional values; Anthropic strict tool use uses grammar-constrained sampling to guarantee adherence to its supported schema subset. The dispatcher must still enforce ranges, referential integrity, authorization, freshness, and business rules. [[1]](https://developers.openai.com/api/docs/guides/function-calling) [[6]](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)
- HTTP distinguishes safe/idempotent methods from non-idempotent operations. RFC 9110 says GET, PUT, DELETE, and other safe methods are idempotent by defined intent and warns against automatically retrying non-idempotent methods unless the client knows the operation is idempotent or the original was not applied. [[7]](https://datatracker.ietf.org/doc/html/rfc9110)
- Application-level idempotency is still needed for POST-like mutations. Stripe's documented pattern stores the first result for an idempotency key, returns it for retries, compares parameters on reuse, and allows keys to be pruned after at least 24 hours. That retention is Stripe-specific; the portable principle is a unique operation key bound to canonical arguments and caller scope. [[8]](https://docs.stripe.com/api/idempotent_requests)
- OAuth 2.0 Security BCP requires exact redirect-URI matching (except specified localhost port behavior) and prohibits open redirectors. `[inferred]` An agent tool should receive a short-lived, audience-restricted delegated token with minimum scopes at execution time; tokens and client secrets should never be model-visible schema arguments or browser-page text. [[9]](https://datatracker.ietf.org/doc/html/rfc9700)

### Function-calling modes and orchestration

- Tool choice should be controlled structurally where possible: `none` prohibits calls, `auto` lets the model decide, `required`/`any` requires a call, and allowed-tool lists narrow the active capability set. OpenAI supports disabling parallel calls so a turn produces zero or one function call; supported models can otherwise emit multiple independent calls. [[1]](https://developers.openai.com/api/docs/guides/function-calling)
- `[inferred]` Parallelize only independent, read-only or commutative operations. Sequence calls when one result supplies another argument, when order changes business state, when an action needs approval, or when concurrent writes could violate invariants. A parallel batch needs per-call IDs and partial-failure handling; do not fail the entire batch and blindly replay successful mutations.
- Direct function calls let the model inspect each result before deciding what follows. OpenAI Programmatic Tool Calling instead lets the model write bounded JavaScript that calls eligible tools and reduces intermediate results inside a hosted runtime. Current guidance recommends it for filtering, joining, ranking, deduplication, aggregation, and validation, but not when every result may change the next semantic decision, when an action needs approval, or when native citations/artifacts must survive. [[10]](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)
- Large tool catalogs increase prompt cost and selection ambiguity. `[inferred]` Retrieve/defer tool schemas using intent plus policy, then give the model a small eligible set. Tool discovery cannot expand permission: intersect retrieved candidates with the principal's capability set before rendering them.
- BFCL evaluates serial, parallel, abstention, and stateful multi-step function calling. Its ICML 2025 paper reports that current models can be strong on single-turn calls while memory, dynamic decisions, and long-horizon behavior remain open challenges. ToolSandbox similarly evaluates state dependencies, insufficient information, intermediate milestones, and arbitrary trajectories rather than only exact final syntax. [[11]](https://proceedings.mlr.press/v267/patil25a.html) [[12]](https://arxiv.org/abs/2408.04682)

### Web search versus browser control

These are different tools and should not be interchangeable by default:

| Mode | Observation/action space | Execution | Appropriate use |
|---|---|---|---|
| Search/fetch API | queries, URLs, page text, citation metadata | provider or application | current facts, research, source retrieval |
| Direct business API | typed request/response | application worker | reliable reads and mutations |
| DOM/browser automation | locators, DOM/accessibility tree, navigation | isolated browser worker | site workflow without usable API |
| Visual computer use | screenshots plus mouse/keyboard actions | isolated browser/VM worker | visual/canvas/desktop UI with no structured interface |

- OpenAI web search returns a `web_search_call`, final text, and URL annotations. Its UI requirement says inline citations shown to end users must be visible and clickable; `sources` can expose the broader consulted URL list. Google Search grounding similarly returns search steps and `url_citation` annotations. [[13]](https://developers.openai.com/api/docs/guides/tools-web-search) [[14]](https://ai.google.dev/gemini-api/docs/google-search)
- `[inferred]` Search output is evidence, not authorization or instruction. Store the query, canonical URL, retrieval time, title, publisher/domain, cited span, and content digest. Resolve redirects and enforce URL/egress policy before fetching. Claims should be traceable to a source actually returned by the tool; a well-formed citation does not prove the claim is supported.
- Computer use is an observation-action loop: provide the current screenshot, receive actions, execute them, capture the updated state, and return it under the call ID. OpenAI currently recommends `detail: "original"` for screenshots and explicitly advises an isolated browser/VM, domain/action allowlists, and a human in the loop for high-impact operations. [[15]](https://developers.openai.com/api/docs/guides/tools-computer-use)
- Playwright `BrowserContext` instances are incognito-like isolated profiles with independent cookies and storage; non-persistent contexts do not write browsing data to disk. Its auth guide warns that saved storage state can contain cookies/headers capable of impersonation and should not be committed to a repository. [[16]](https://playwright.dev/docs/browser-contexts) [[17]](https://playwright.dev/docs/auth)
- `[inferred]` Prefer stable semantic locators and API responses over pixel coordinates. For every action, verify the intended origin, active principal, target element, precondition, and postcondition. Browser automation needs navigation/time/action budgets and a stuck-state detector based on repeated screenshots/DOM digests.
- GUI success is not solved. WebArena's original reproducible benchmark reported 14.41% end-to-end success for its best GPT-4-based baseline versus 78.24% for humans. OSWorld reported 12.24% for its best evaluated model versus more than 72.36% human success across 369 real-computer tasks. These older baseline numbers are not rankings of current models, but demonstrate why production acceptance must use execution-state assertions rather than model self-report. [[18]](https://arxiv.org/abs/2307.13854) [[19]](https://arxiv.org/abs/2404.07972)

### Code execution

- OpenAI Code Interpreter runs Python in a fully sandboxed VM/container, supports uploaded/generated files, and offers 1, 4, 16, and 64 GiB memory tiers. A container expires after 20 minutes without use; associated data is discarded and cannot be recovered, so durable artifacts must be downloaded before expiry. [[20]](https://developers.openai.com/api/docs/guides/tools-code-interpreter)
- Anthropic server-side code execution runs Python/Bash and file operations in a sandboxed container with no Internet access; packages cannot be downloaded at runtime. Gemini code execution runs Python only, does not allow arbitrary library installation, and bills generated code plus execution results as tokens even though the tool itself has no separate enablement fee. [[21]](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool) [[22]](https://ai.google.dev/gemini-api/docs/code-execution)
- `[inferred]` Distinguish four code modes: (1) pure calculator/DSL, smallest surface; (2) provider-hosted interpreter, operationally simple but provider-governed; (3) self-hosted container/application-kernel sandbox, flexible but security-intensive; (4) local shell, maximum capability and risk. Route to the narrowest mode that can complete the task.
- Native containers share the host kernel; Kubernetes seccomp can restrict allowed system calls, while privileged containers override seccomp/AppArmor/SELinux constraints. For adversarial code, use non-root, read-only base image, dropped capabilities, seccomp/AppArmor, user/network namespaces, cgroup quotas, ephemeral filesystem, no host mounts/socket, and deny-by-default egress; consider a stronger application-kernel or microVM boundary for multi-tenant workloads. [[23]](https://kubernetes.io/docs/reference/node/seccomp/)
- `[inferred]` Code results are untrusted too. Validate artifact type/size, scan archives and executables, strip active content where appropriate, compute digests, and copy only approved files to durable object storage. Do not deserialize untrusted pickle-like objects or execute generated notebook output in a privileged process.

### Reference guarded dispatcher

```python
def execute_tool(principal, run, call):
    spec = registry.resolve(call.name, run.toolset_version)
    args = spec.schema.validate_json(call.arguments)

    policy = authorize(
        principal=principal,
        action=spec.action,
        resource=spec.resource_from(args),
        context={"tenant": run.tenant_id, "purpose": run.purpose},
    )
    spec.business_rules.validate(args, policy)

    if spec.risk.requires_approval:
        approval = require_signed_approval(call.call_id, spec, args)
    else:
        approval = None

    key = stable_idempotency_key(run.id, call.call_id, spec.version, args)
    attempt = ledger.reserve(key, args_digest(args), approval)
    if attempt.completed:
        return attempt.recorded_result

    result = worker_pool(spec.kind).execute(
        spec=spec,
        args=args,
        credential=mint_scoped_credential(principal, policy, ttl=attempt.deadline),
        idempotency_key=key,
        deadline=attempt.deadline,
    )
    checked = spec.output_schema.validate(sanitize(result))
    return ledger.commit(attempt, checked)
```

Strict function arguments eliminate malformed JSON/schema errors; they do not eliminate any authorization, approval, idempotency, output-validation, or audit step above.

## 2. Token Economics & NFR Metrics

### End-to-end latency model

`[inferred]` Tool workflows should decompose latency by turn and dependency:

```text
T_total = sum(T_model_queue + T_model_prefill + T_model_decode)
        + critical_path(T_policy + T_approval + T_tool_queue + T_tool_exec)
        + sum(T_browser_render + T_screenshot + T_network)
        + T_retries
```

A client function normally adds at least one model round trip because its result must be sent back before final synthesis. Independent parallel calls reduce the tool critical path to approximately the slowest call, not the sum, but can increase quota pressure and partial failures. Browser tasks often serialize observation/action turns; code execution adds sandbox allocation, runtime, and artifact transfer.

Track p50/p95/p99 for model turn, policy gateway, each tool, browser action/render, sandbox cold start, and total trajectory. Also track tool-selection accuracy, argument-schema pass rate, business-validation rejection, abstention accuracy, calls per successful task, retry/duplicate suppression, browser steps, approval wait, artifact validation failure, and success per dollar.

> ⚠️ Limited public data available for this dimension. Hosted providers do not publish stable end-to-end p50/p95/p99 figures segmented by model, region, function versus browser/code tool, tool-result size, sandbox cold start, and customer tier. Benchmark the full trajectory against production-shaped dependencies; single-call model latency is not the workflow SLA.

### Cost formula and worked example

```text
C_1000 = (I_uncached*P_in + I_cached*P_cache + O_total*P_out) / 1,000,000
       + N_search*P_search_call
       + N_other_tool*P_tool_call
       + container_minutes*P_container_minute
       + browser_compute + API/vendor charges + egress + storage
```

`I` must include tool schemas and tool results placed back in context. `O_total` includes reasoning and generated code where the provider bills them as tokens. Count failed attempts, retries, browser screenshots, paid API transactions, and code/container sessions.

OpenAI's current standard short-context prices are `$2/$0.20/$12` per 1M uncached input/cached input/output tokens for `gpt-5.6-terra`. Web search is currently $10 per 1,000 calls plus search-content tokens at model rates. Hosted Shell/Code Interpreter containers are listed at $0.03/$0.12/$0.48/$1.92 for 1/4/16/64 GiB per 20-minute session, with eligible sessions billed by minute and a five-minute minimum. [[24]](https://developers.openai.com/api/docs/pricing)

Worked example: 1,000 `terra` research executions consume an aggregate 6,500 input tokens and 800 output tokens each, including search content, and issue two web searches each. With no cache/container/other vendor costs:

| Component | Calculation | Cost |
|---|---:|---:|
| Input | `6.5M * $2 / 1M` | $13.00 |
| Output | `0.8M * $12 / 1M` | $9.60 |
| Search calls | `2,000 * $10 / 1,000` | $20.00 |
| **Total per 1K executions** | | **$42.60** |

This example shows that tool charges can dominate token input cost. It assumes every execution succeeds first time; observed calls per successful task is the correct budget multiplier.

### Tool-schema and result economics

- Tool definitions consume input context on every relevant turn unless prefix caching applies. Anthropic's current docs explicitly enumerate additional tool-use system-prompt tokens by model/tool-choice mode; tool definitions and accumulated results can exhaust context. [[25]](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)
- `[inferred]` Optimize in this order: remove unused tools, defer/retrieve schemas, canonicalize and prefix-cache stable definitions, limit result fields/rows/bytes, store large results externally by digest, and return a compact structured observation. Do not truncate the authorization/resource identifier or evidence needed for verification.
- Programmatic calls can reduce intermediate results before they enter the main model context. Direct calls are preferable when the next action depends on semantic inspection or when citations/native artifacts must remain available. Measure both final-message quality and intermediate program output, as OpenAI's guide recommends. [[10]](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)

### Routing and back-pressure

`[inferred]` A cost/risk router should prefer:

1. No tool for stable knowledge or pure transformation already in context.
2. Deterministic library/calculator for bounded computation.
3. Typed business API for reliable state and mutations.
4. Search/fetch for current external information.
5. DOM browser automation only when an API is unavailable or cannot express the workflow.
6. Visual computer use only for interfaces that require GUI perception.
7. General code/shell only when a narrower DSL/library cannot do the work.

Back-pressure should use separate quotas for model tokens, tool calls, paid vendor operations, browser slots, sandbox CPU/GiB-minutes, and outbound bytes. OWASP's API resource-consumption guidance calls out execution time, memory, processes, upload size, batch size, records per page, per-client rate limiting, and third-party spending limits. [[26]](https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/)

Capacity worksheet:

```text
model_turns/sec       = task_RPS * mean_model_turns
tool_ops/sec          = task_RPS * mean_tool_calls
browser_concurrency   = browser_task_RPS * p95_browser_duration_seconds
sandbox_concurrency   = code_task_RPS * p95_sandbox_duration_seconds
uncached_input_TPM    = task_RPM * mean_uncached_input_tokens
vendor_spend/hour     = calls/hour * vendor_price_per_call
```

`[inferred]` Admission should reserve the worst-case remaining budget before a mutation. Set `max_tool_calls`, max trajectory steps, output/token cap, browser time/actions, code CPU/wall/memory/process/file/output limits, and a tenant spend ceiling.

## 3. Distributed Resilience & State

### Durable call ledger and checkpoints

- `[inferred]` Persist an append-only trajectory with events: `call_proposed`, `validated`, `denied`, `approval_requested`, `approved`, `leased`, `started`, `succeeded|failed|unknown`, `result_committed`, `observation_sent`. Store `run_id`, `call_id`, tool/schema version, canonical argument digest, principal/tenant, policy decision ID, approval ID, idempotency key, deadline, worker lease/fencing token, external request ID, status, result/artifact digest, and token/cost usage.
- Temporal documents durable workflows that resume after crashes and infrastructure failures. `[inferred]` Model calls, API calls, browser sessions, and sandbox jobs should be workflow activities whose completed outputs are recorded; replay must reuse recorded results rather than repeat a nondeterministic model or side effect. [[27]](https://docs.temporal.io/)
- `[inferred]` Checkpoint browser workflows at semantic milestones, not only screenshots: current URL/origin, authenticated role, completed action IDs, external entity IDs, relevant DOM/state digest, and durable artifact links. Browser cookies and in-memory code objects are leases, not durable truth; recreate sessions and reconcile external state after worker loss.

### Exactly-once effect is an application property

- RFC 9110 permits automatic retry of idempotent intent but not arbitrary non-idempotent methods. `[inferred]` Exactly-once business effect requires a server-recognized idempotency key or transactional deduplication; a broker/workflow can redeliver work and still duplicate an external effect if the effect commits before the local checkpoint. [[7]](https://datatracker.ietf.org/doc/html/rfc9110)
- `[inferred]` For internal mutations, use an outbox/inbox and unique `(tenant_id, idempotency_key)` constraint. For external APIs, pass their idempotency key and record their request/operation ID. If a timeout leaves status unknown, query/reconcile the resource before retrying; never ask the model to infer whether a payment/email/delete succeeded.
- `[inferred]` Lease calls to workers with expiry and monotonically increasing fencing tokens. Use compare-and-swap on the run state so a late browser/code worker cannot commit over a newer attempt. Do not hold a database lock across a model call, browser session, approval wait, or external API request.

### Retries, circuit breakers, and partial failure

- Azure's circuit-breaker pattern uses Closed, Open, and Half-Open states, with limited probes during recovery. Use separate breakers per provider/tool/region/operation class; a failing search API should not block an internal read tool, and a failing mutation endpoint should not be failed over blindly to a second endpoint. [[28]](https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker)
- `[inferred]` Retry only classified transient failures with exponential backoff, jitter, `Retry-After`, remaining-deadline checks, and an aggregate retry budget. Schema errors, authorization denial, policy refusal, invalid business state, unsupported browser action, and deterministic code errors are not transient.
- `[inferred]` For parallel reads, return successes and typed failures so the model can decide whether missing data is material. For parallel writes, avoid speculative batches; if required, define a saga with explicit compensation and expose compensation failure to an operator.
- Browser fallback should preserve safety, not merely completion: `[inferred]` if the DOM locator fails, refresh state and retry within a small bound; do not switch automatically to visual clicks for a payment/delete unless the visual path has its own target verification and approval gate.

### Graceful degradation

1. Bypass an optional cache and call the same approved read API.
2. Use a compatible provider/region for read-only search or computation under the same policy and data residency.
3. Return a previously validated, still-fresh read result with provenance.
4. Queue resumable non-interactive work using the same idempotency key.
5. Return partial results with explicit missing tools and no claim of completion.
6. Stop a high-impact mutation when authorization, approval, reconciliation, or audit persistence is unavailable.

## 4. Enterprise Security & Governance

### Least capability and complete mediation

- OWASP describes excessive agency as excessive functionality, permissions, or autonomy and recommends minimum tool sets, minimum downstream scopes, user-context execution, downstream authorization, and human approval. A read-only email summarizer should not even expose `send_email`; hiding it in the prompt is not a control. [[29]](https://genai.owasp.org/llmrisk2023-24/llm08-excessive-agency/)
- `[inferred]` Tool-level RBAC/ABAC is the intersection of principal permissions, tenant policy, purpose, resource attributes, tool risk, current workflow state, approval, and spend quota. Filter the model-visible tool list, then re-authorize every concrete call at execution time. Short-lived delegated credentials should be minted after authorization, never stored in conversation history.
- Strict schemas do not defend against a correctly typed malicious call. Validate resource ownership, allowed state transition, monetary/quantity limits, destination allowlists, and version preconditions. Use two tools for propose/commit where review matters; the commit tool accepts a signed proposal ID rather than free-form regenerated arguments.

### Untrusted tool observations and prompt injection

- OpenAI's computer-use guidance says screenshots, page text, PDFs, emails, chats, and tool outputs are untrusted and only direct user instructions count as permission. It recommends confirmation immediately before risky actions, including deletion, access changes, external communications, software installation, financial transactions, and sensitive-data transmission. [[15]](https://developers.openai.com/api/docs/guides/tools-computer-use)
- AgentDojo contains 97 realistic tasks and 629 security test cases for indirect prompt injection through tool data. Its results show that both utility and security remain challenging. Use adversarial tool-result fixtures in release gates, not only benign happy paths. [[30]](https://arxiv.org/abs/2406.13352)
- `[inferred]` Tag every observation with source/trust and keep untrusted data in evidence fields. The model may reason about "click here to reveal secrets" but the gateway must not treat it as user intent. Search citations, browser alerts, code stdout, filenames, and API error messages can all carry injection strings.

### Browser controls

- `[inferred]` Give each run/tenant a fresh non-persistent browser context. Restrict DNS and egress to approved origins; block loopback, link-local, cloud metadata, internal control planes, `file:` and dangerous URL schemes. Validate every redirect and download. This controls SSRF-style pivoting as well as accidental exfiltration.
- Encrypt any reusable auth state with a tenant-scoped key, restrict access to the browser worker, and expire it quickly. Playwright warns that stored state may contain impersonation-capable cookies/headers. Never reuse a shared authenticated context across tenants or parallel tasks that mutate server state. [[17]](https://playwright.dev/docs/auth)
- `[inferred]` Approval binds exact action, origin, account, resource, payload digest, price/quantity where applicable, and expiry. Reconfirm if the page changes, price changes, redirect changes origin, or arguments are regenerated. Capture before/after evidence and the external confirmation ID.

### Code sandbox controls

- `[inferred]` Execute untrusted code as non-root in a fresh sandbox with no privileged mode, no host filesystem or container socket, read-only base image, ephemeral writable quota, syscall/capability restrictions, process/CPU/memory/wall/output limits, and deny-by-default egress. Mount inputs read-only and export outputs through a broker that validates type, size, path, and content.
- Network-off is preferable for analysis. When Internet is necessary, proxy through a policy gateway with domain/method/byte limits and no access to internal address ranges or ambient credentials. Package installation should resolve from a pinned, scanned mirror with lockfile/SBOM rather than the public Internet at runtime.
- `[inferred]` Separate sandbox identity from business-tool identity. Generated code can call a narrow broker using per-call capabilities; it must not inherit the orchestrator's cloud credentials. Rotate/revoke the capability when the call ends.

### API supply chain, privacy, and audit

- OWASP API10 warns that consumers often trust third-party API data and endpoints without equivalent transport, validation, redirect, resource, and timeout controls. Validate third-party responses to an output schema and treat them as hostile even if the request was authenticated. [[31]](https://owasp.org/API-Security/editions/2023/en/0xaa-unsafe-consumption-of-apis/)
- `[inferred]` Pin SDK/package versions, verify provider endpoints/certificates, inventory tool/spec owners, diff OpenAPI/schema changes, and require review for new capabilities. A backward-compatible API field can still alter model behavior by injecting large or instruction-like content.
- Audit records must capture proposal, decision, approval, execution and observation without defaulting to raw sensitive payloads. OpenTelemetry GenAI conventions include tool call IDs/names/arguments/results and warn that inputs and tool fields may contain sensitive data; use content digests, redaction, access controls, and separate encrypted evidence storage. [[32]](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
- Provider-hosted tools have provider-specific retention. Review endpoint-by-endpoint controls rather than assuming model-call policy covers browser/container artifacts; OpenAI's data guide documents different application-state retention and ZDR eligibility by endpoint/feature. [[33]](https://developers.openai.com/api/docs/guides/your-data)

## 5. Production Failure Modes

| Failure | Detection | Mitigation |
|---|---|---|
| Wrong tool / failure to abstain | tool-selection and relevance eval; unexpected capability class | small policy-filtered toolset, clearer descriptions, negative/insufficient-information cases, BFCL-style abstention tests [[11]](https://proceedings.mlr.press/v267/patil25a.html) |
| Schema-valid but wrong arguments | business-rule rejection, nonexistent ID, amount/destination anomaly | strict schema plus deterministic semantic validation, enums/ranges, resolve human labels to IDs before mutation |
| Duplicate side effect after timeout | same intent produces multiple external IDs | stable idempotency key, reconcile unknown status, outbox/inbox, never blind-retry POST |
| Parallel-call race | stale version, conflicting writes, partial batch success | parallel reads only by default, ETag/version preconditions, per-call ledger, saga/compensation |
| Tool/API drift | rising validation/404 rate after provider change | pinned schema/spec digest, contract tests, canary, versioned adapters, rollback |
| Rate-limit/retry storm | 429/503, queue and cost spike | bounded retry budget, `Retry-After`, jitter, breaker, token/tool buckets, bulkheads |
| Oversized/hostile result | token/context spike, parser error, prompt injection | response schema, row/byte cap, sanitizer, external artifact reference, trust labels |
| Search citation mismatch | cited URL does not support statement | claim-to-source entailment/evidence check, retain source spans, prefer primary sources |
| Browser prompt injection/phishing | page instructs agent to disclose/change behavior | treat page as untrusted, origin allowlist, stop/escalate, AgentDojo red-team suite [[30]](https://arxiv.org/abs/2406.13352) |
| Browser UI drift | locator failures, repeated screenshot/DOM digest, unexpected origin | semantic locators, refresh/re-observe, step cap, target/postcondition assertions, API fallback |
| Browser wrong-account action | displayed account differs from run principal | per-run context, verify identity before mutation, signed approval bound to account |
| CAPTCHA/MFA/HTTPS warning | safety boundary encountered | hand off to user; never bypass browser/site safety barrier [[15]](https://developers.openai.com/api/docs/guides/tools-computer-use) |
| Sandbox escape attempt | denied syscall/egress, runtime alert | defense-in-depth sandbox, patched hosts, no privileged mode, per-tenant boundary, destroy environment |
| Resource bomb/fork bomb | CPU/memory/process/disk/output thresholds | cgroup/rlimit/seccomp quotas, wall timeout, output truncation, spend cap |
| Data exfiltration from code | blocked internal/unknown egress, secret access attempt | network-off/default deny, scoped proxy, no ambient credentials, read-only inputs |
| Ephemeral artifact loss | container expires before export | durable object-store copy plus digest before checkpoint; recreate sandbox from immutable inputs [[20]](https://developers.openai.com/api/docs/guides/tools-code-interpreter) |
| False completion | model claims success without state change | verify API/browser/code postcondition and external receipt; trajectory success is evaluator-derived, not self-reported |
| Infinite tool loop | repeated call/arguments or no state progress | max calls/turns/time/tokens/cost, repeated-state detector, terminal typed failure |
| Cascading dependency timeout | nested retries consume deadline and workers | propagated deadline, per-tool breaker, bounded queues, partial result/queue fallback |

`[inferred]` Release evaluation must combine: schema/AST accuracy; executable result correctness; tool relevance/abstention; serial/parallel/multi-turn state; error recovery; idempotency; security under injected tool data; browser task-state assertions; code result/artifact correctness; latency/cost/calls per successful task. Gorilla showed that retrieving current API documentation can reduce hallucinated API usage under changed docs, but retrieval does not replace contract tests or authorization. [[34]](https://arxiv.org/abs/2305.15334)

> ⚠️ Limited public data available for this dimension. No authoritative cross-vendor post-mortem was found that quantifies a major production incident caused specifically by an LLM-issued duplicate mutation, browser prompt injection, or hosted code-sandbox escape. Public benchmarks establish capability and attack gaps, but enterprise incident rates, approval interception rates, and sandbox escape attempts are generally private.

## 6. Enterprise System Design Scenarios

### Scenario A: service-desk agent over enterprise APIs

**Workload**: read tickets/assets, propose account changes, reset a credential, and update a case across several internal systems.

`[inferred]` Architecture: identity gateway -> policy-filtered tool retrieval -> model -> typed tool gateway -> per-system adapter workers -> append-only call ledger. Reads may run concurrently; mutations are sequenced behind signed approval and idempotency. Each adapter translates a stable internal schema to a pinned vendor API version, validates output, and propagates the external request/entity ID. The model never receives generic admin credentials.

Design gates: BFCL/ToolSandbox-style evals for relevance, missing information, state dependencies, and recovery; contract tests against vendor sandboxes; tenant/role/resource authorization tests; duplicate-delivery game day; approval bypass red team. SLO is task-success and p95 trajectory latency, with separate dependency SLOs.

### Scenario B: browser agent for a legacy back office

**Workload**: authenticated legacy application has no supported API; agent reads a record, fills a form, and submits after user approval.

`[inferred]` Architecture: one isolated non-persistent Playwright context per run -> egress allowlist -> DOM/accessibility observations -> model actions -> deterministic action validator -> browser worker. Verify origin/account at every mutation phase. Build a structured proposal from the form, show it for approval, bind approval to a digest, then re-read visible fields immediately before submit and verify the resulting record/confirmation ID afterward.

Capacity is constrained by browser duration, not only model RPM:

```text
required_browser_slots = peak_task_RPS * p95_browser_duration_seconds
```

Use per-origin concurrency limits and unique backend test accounts. Store traces/screenshots under a short, governed retention period because they can contain session and customer data.

### Scenario C: multi-tenant data-analysis/code agent

**Workload**: users upload CSV/PDF/XLSX, request calculation/visualization, and download generated artifacts.

`[inferred]` Architecture: malware/type/size scan -> object store -> per-run isolated sandbox with read-only input -> model-generated Python -> resource-limited execution -> output broker -> artifact scan/type verification -> durable object store -> signed download URL. Default network-off; installed packages are pinned and scanned. The sandbox has no tenant database/cloud credentials. Logs contain command/code digest and resource usage, while raw data follows tenant retention.

Quality gates compare numeric results with deterministic reference programs, validate charts/files open correctly, fuzz hostile archives/formulas, run resource bombs, and test cross-tenant filesystem/network denial. Destroy the sandbox after export; a replay starts from immutable inputs and recorded package image, not leftover memory.

### Scenario D: cited web-research tool

**Workload**: answer current market/regulatory questions with multiple sources and calculations.

`[inferred]` Architecture: provider search or policy-controlled search API -> URL canonicalization/domain policy -> fetch/parse -> source records with timestamp/digest -> optional sandboxed computation -> claim/evidence validator -> cited response. Use APIs for structured primary datasets and browser/search for discovery or documents. Do not allow page text to invoke business mutations.

Budget search queries, pages, bytes, code time, and model turns. Evaluate freshness, primary-source preference, claim coverage, citation entailment, conflicting-source handling, and total cost per accepted report.

### Trade-off matrix

| Tool mode | Reliability | Latency/cost | Security surface | State/replay | Best fit |
|---|---|---|---|---|---|
| Typed API/function | highest when contract stable | low; usually one extra model round trip | downstream auth and argument misuse | external IDs/idempotency make replay tractable | enterprise reads/mutations |
| Provider web search | good discovery; evidence quality varies | per query/call plus tokens | untrusted pages, citation mismatch | store query/source metadata | current public facts |
| DOM browser | brittle to UI/auth changes | many serialized steps; browser compute | sessions, phishing, prompt injection | semantic checkpoint plus re-login | legacy web workflows |
| Visual computer use | broadest UI coverage, lowest grounding predictability | screenshot tokens and many turns | same as browser plus coordinate error | screenshots insufficient without state assertions | canvas/desktop/visual-only UI |
| Provider code interpreter | managed isolation, fixed environment | container/session plus tokens | provider boundary and artifact handling | ephemeral; export artifacts | analysis/calculation |
| Self-hosted sandbox | configurable and reproducible | infrastructure/patching overhead | sandbox escape, egress, supply chain | image/input digest supports replay | regulated/custom runtimes |
| Local shell | maximum compatibility | low setup, high operational risk | host/user data and credentials | host drift complicates replay | trusted developer workstation with approvals |

### Principal-architect decision rules

1. Prefer a typed API over browser actions and a narrow deterministic tool over general code.
2. Treat every model call as a proposal and every tool observation as untrusted data.
3. Separate tool discovery from authorization; permission is checked again on the concrete resource.
4. Make mutations idempotent, approved at the point of risk, externally reconcilable, and postcondition-verified.
5. Persist the call/result ledger before advancing model state; replay recorded results rather than side effects.
6. Evaluate executable task success, abstention, security, calls, latency, and cost together; schema accuracy alone is not readiness.

## Sources

- [1] https://developers.openai.com/api/docs/guides/function-calling - OpenAI function-calling lifecycle, strict mode, call IDs, and parallel calls.
- [2] https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works - Anthropic client/server tool execution contract and loop.
- [3] https://ai.google.dev/gemini-api/docs/tools - Gemini managed versus custom tool execution.
- [4] https://arxiv.org/abs/2302.04761 - Toolformer tool-selection and argument-learning paper.
- [5] https://spec.openapis.org/oas/latest.html - OpenAPI 3.2 specification.
- [6] https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use - Anthropic grammar-constrained strict tools.
- [7] https://datatracker.ietf.org/doc/html/rfc9110 - HTTP idempotency, retry, and Retry-After semantics.
- [8] https://docs.stripe.com/api/idempotent_requests - Production idempotency-key behavior.
- [9] https://datatracker.ietf.org/doc/html/rfc9700 - OAuth 2.0 Security Best Current Practice.
- [10] https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling - OpenAI Programmatic Tool Calling mechanics and selection guidance.
- [11] https://proceedings.mlr.press/v267/patil25a.html - BFCL ICML 2025 function-calling benchmark.
- [12] https://arxiv.org/abs/2408.04682 - ToolSandbox stateful tool-use benchmark.
- [13] https://developers.openai.com/api/docs/guides/tools-web-search - OpenAI search actions, sources, and citation contract.
- [14] https://ai.google.dev/gemini-api/docs/google-search - Gemini Google Search grounding and annotations.
- [15] https://developers.openai.com/api/docs/guides/tools-computer-use - OpenAI computer-use loop and safety controls.
- [16] https://playwright.dev/docs/browser-contexts - Playwright browser-context isolation.
- [17] https://playwright.dev/docs/auth - Playwright authenticated-state security guidance.
- [18] https://arxiv.org/abs/2307.13854 - WebArena realistic web-agent benchmark.
- [19] https://arxiv.org/abs/2404.07972 - OSWorld computer-use benchmark.
- [20] https://developers.openai.com/api/docs/guides/tools-code-interpreter - OpenAI code containers, memory tiers, files, and expiration.
- [21] https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool - Anthropic sandboxed code execution and network restriction.
- [22] https://ai.google.dev/gemini-api/docs/code-execution - Gemini Python execution, libraries, limits, and billing.
- [23] https://kubernetes.io/docs/reference/node/seccomp/ - Kubernetes seccomp profiles and privileged-container limitation.
- [24] https://developers.openai.com/api/docs/pricing - Current model, search, and container prices.
- [25] https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview - Claude tool taxonomy and token overhead.
- [26] https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/ - API resource and spend controls.
- [27] https://docs.temporal.io/ - Durable workflow execution and recovery.
- [28] https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker - Circuit-breaker states and recovery probes.
- [29] https://genai.owasp.org/llmrisk2023-24/llm08-excessive-agency/ - Excessive functionality, permission, and autonomy risks.
- [30] https://arxiv.org/abs/2406.13352 - AgentDojo tool-agent prompt-injection benchmark.
- [31] https://owasp.org/API-Security/editions/2023/en/0xaa-unsafe-consumption-of-apis/ - Unsafe consumption of third-party APIs.
- [32] https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/ - GenAI/tool telemetry attributes and sensitive-data warnings.
- [33] https://developers.openai.com/api/docs/guides/your-data - OpenAI endpoint data controls and retention.
- [34] https://arxiv.org/abs/2305.15334 - Gorilla/API retrieval and hallucinated-call research.
