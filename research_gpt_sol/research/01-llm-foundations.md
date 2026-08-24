# Research: LLM Foundations - Transformers, Reasoning, Function Calling, Structured Output

**Date researched**: 2026-08-21
**Sources consulted**: 33

## Scope and evidence labels

This brief covers the four foundations named in the roadmap: Transformer mechanics, reasoning, function calling, and structured output. A plain statement is documented by a linked primary source or current official documentation. `[inferred]` marks an architecture recommendation derived from those mechanics rather than a vendor guarantee. Published benchmark results are identified as such and should not be treated as production SLAs.

## 1. System Topology & Mechanics

### Transformer mechanics

- The original Transformer replaced recurrent and convolutional sequence processing with stacked attention and feed-forward blocks. Scaled dot-product attention is `softmax(QK^T / sqrt(d_k))V`; multi-head attention runs several learned projections in parallel, and positional information is required because attention alone has no sequence order. The original architecture was encoder-decoder, while GPT-style LLMs use autoregressive next-token prediction, so generated token `t` is conditioned on the prior token prefix rather than produced independently. [[1]](https://arxiv.org/abs/1706.03762) [[2]](https://arxiv.org/abs/2005.14165)
- A representative modern open LLM stack uses decoder-only blocks with causal attention, RMSNorm, rotary position embeddings (RoPE), grouped-query attention (GQA), SwiGLU activations, and cross-entropy next-token training. These are common design choices, not requirements of the Transformer definition. [[3]](https://arxiv.org/abs/2402.00838)
- Inference has two materially different phases. **Prefill** processes the whole input in parallel and constructs per-layer key/value (KV) state; **decode** generates autoregressively and reuses that KV cache. Vanilla attention has quadratic sequence-length time and memory during full-sequence attention. FlashAttention preserves exact attention while reducing HBM traffic through tiling; its paper reported 3x speedup on GPT-2 at sequence length 1K, a research result on its test hardware rather than a current hosted-API SLA. [[4]](https://arxiv.org/abs/2205.14135)
- KV-cache capacity is often the serving bottleneck. PagedAttention maps KV blocks similarly to virtual memory, reducing fragmentation and enabling sharing; the vLLM paper reported 2-4x throughput at similar latency versus the evaluated systems, with larger gains for longer sequences. [[5]](https://arxiv.org/abs/2309.06180)

### Reasoning

- Chain-of-thought (CoT) prompting demonstrated that intermediate natural-language steps can improve arithmetic, commonsense, and symbolic reasoning in sufficiently large models. Self-consistency samples multiple paths and selects the most consistent answer; its paper reported gains including +17.9 percentage points on GSM8K in its experimental setup, at the cost of multiple generations. [[6]](https://arxiv.org/abs/2201.11903) [[7]](https://arxiv.org/abs/2203.11171)
- Current hosted reasoning models may generate hidden **reasoning tokens** in addition to input and visible output. OpenAI documents that these tokens occupy context and are billed as output; a problem may consume from hundreds to tens of thousands, and a response can end as `incomplete` before any visible text if `max_output_tokens` is exhausted. The supported `reasoning.effort` levels are model-dependent and trade latency/token use against completeness. [[8]](https://developers.openai.com/api/docs/guides/reasoning)
- Reasoning text is not a proof or a reliable audit record. A 2025 study found that tested reasoning models often failed to disclose use of injected hints, with reveal rates often below 20% in the evaluated settings. Therefore verify final claims with deterministic code, tests, retrieval, or domain rules rather than trusting a plausible rationale. [[9]](https://arxiv.org/abs/2505.05410)

### Function calling and tool dispatch

- Function calling is a typed request from the model, not execution. The application sends tool definitions, receives a tool name plus JSON arguments and a `call_id`, validates and executes the operation, returns a `function_call_output` tied to that ID, then asks the model to continue. The loop may yield a final answer or more calls. [[10]](https://developers.openai.com/api/docs/guides/function-calling)
- Custom/client tools and provider-hosted tools have different trust boundaries. Gemini documents that built-in tools execute within the provider call, while custom-function execution is explicitly the application's responsibility; its current API can return parallel or sequential calls with unique IDs. [[11]](https://ai.google.dev/gemini-api/docs/tools)
- Toolformer established the learning problem behind tool use: decide whether, when, and which API to call, construct arguments, then incorporate the result into future prediction. Its self-supervised research setup is distinct from API-time function schemas, but explains why tool selection and argument quality must both be evaluated. [[12]](https://arxiv.org/abs/2302.04761)
- On supported OpenAI models, multiple functions may be emitted in one turn; `parallel_tool_calls=false` restricts a response to zero or one call. Parallelism is appropriate only for independent, read-only calls. `[inferred]` Calls with dependencies or mutations should be sequenced because parallel completion order cannot encode business causality. [[10]](https://developers.openai.com/api/docs/guides/function-calling)

### Structured output

- JSON mode guarantees syntactically valid JSON but not adherence to a requested object schema. Structured Outputs/strict tool use constrain generation to a supported JSON Schema subset. OpenAI strict functions require every property to be listed as required and `additionalProperties: false`; Anthropic documents grammar-constrained sampling for `strict: true`. [[13]](https://developers.openai.com/api/docs/guides/structured-outputs) [[14]](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)
- Constrained decoding dynamically masks tokens that would violate the schema. OpenAI describes compiling JSON Schema into a context-free grammar (CFG), caching the grammar artifact, and allowing only valid next tokens. This guarantees syntactic/schema conformance for supported schemas, not factual correctness, authorization, referential integrity, or satisfaction of business invariants. [[15]](https://openai.com/index/introducing-structured-outputs-in-the-api/) [[16]](https://arxiv.org/abs/2501.10868)
- Function calling and structured final output solve different contracts: use a function schema for an intermediate action request; use a response schema for a final machine-consumed result. Gemini's official docs make the same distinction. [[11]](https://ai.google.dev/gemini-api/docs/tools)

### Control plane, data plane, and state model

| Plane | Owns | Must not be delegated to the model |
|---|---|---|
| Control plane | model/snapshot registry, prompt and schema versions, router rules, tenant quotas, safety policy, tool allowlists, secrets, RBAC, rollout/rollback | identity, authorization, budget policy, approval requirements |
| Data plane | tokenization, prefill/decode, streaming events, tool-call/result messages, schema validation, tool execution workers | deciding whether a caller actually has permission or whether a mutation is idempotent |

`[inferred]` The base API is a stateless or provider-state-assisted request/response service, not a durable DAG, Supervisor-Worker system, or workflow engine. A tool loop resembles ReAct at the message level, but checkpointing, retries, compensation, and terminal conditions belong to the application/orchestrator. Sync HTTPS/JSON suits one-shot extraction; streaming lowers perceived latency; queues/workflows suit long-running or bursty work. Agent-to-agent communication is outside this foundation and should use explicit, versioned messages rather than free-form shared memory.

### Current code patterns

Typed final output with the current OpenAI Python SDK pattern:

```python
from pydantic import BaseModel, Field
from openai import OpenAI

class Ticket(BaseModel):
    category: str
    priority: int = Field(ge=1, le=5)
    summary: str

client = OpenAI()
response = client.responses.parse(
    model="gpt-5.6-luna",
    input=[
        {"role": "system", "content": "Extract the support ticket."},
        {"role": "user", "content": user_text},
    ],
    text_format=Ticket,
)

if response.status == "incomplete":
    raise RuntimeError(response.incomplete_details)
ticket = response.output_parsed
```

The official SDK exposes `responses.parse(..., text_format=Model)` and `output_parsed`. Production code must also branch on refusal and incomplete output rather than assuming a parsed object always exists. [[13]](https://developers.openai.com/api/docs/guides/structured-outputs)

Vendor-neutral guarded tool loop:

```python
for step in range(MAX_STEPS):
    response = call_model(history, tools=authorized_tools(user), strict=True)
    calls = extract_tool_calls(response)
    if not calls:
        return validate_final(response)

    for call in calls:
        args = schema_validate(call.name, call.arguments)
        authorize(user, call.name, args)       # deterministic RBAC/ABAC
        enforce_business_rules(call.name, args)
        require_approval_if_mutating(call)
        result = execute_once(call.call_id, call.name, args)
        history.append(tool_result(call.call_id, result))

raise BudgetExceeded("tool-step limit reached")
```

`strict=True` removes a class of malformed-argument failures; every other guard in this pattern remains necessary.

## 2. Token Economics & NFR Metrics

### Latency and throughput model

`[inferred]` Model end-to-end latency should be decomposed rather than reported as one average:

```text
T_total = T_queue + T_prefill(input_tokens) +
          sum(T_decode(visible_tokens + reasoning_tokens)) +
          sum(T_tool_network + T_tool_execution) + T_retries
```

Track time-to-first-token (TTFT), inter-token latency/tokens per second, end-to-end p50/p95/p99, tool latency, queue time, and retry count separately. Function calling normally adds at least one model round trip because the application must return tool output before final generation. [[10]](https://developers.openai.com/api/docs/guides/function-calling)

> ⚠️ Limited public data available for this dimension. Major hosted-model vendors do not publish stable per-model p50/p95/p99 latency SLAs that can be used across regions, context lengths, reasoning efforts, service tiers, and customer quotas. Benchmark the exact snapshot and workload; do not turn a lab tokens/second figure into an API SLA.

### Cost formula and worked example

For 1,000 executions:

```text
C_1000 = (1000 / 1,000,000) *
         (I_uncached*P_in + I_cached*P_cached + I_written*P_write +
          (O_visible + O_reasoning)*P_out) + tool/container charges
```

OpenAI's current short-context standard prices per 1M tokens are: `gpt-5.6-sol` $5 input/$0.50 cached/$6.25 cache-write/$30 output; `terra` $2/$0.20/$2.50/$12; `luna` $0.20/$0.02/$0.25/$1.20. [[17]](https://developers.openai.com/api/docs/pricing)

At 2,000 uncached input tokens and 500 output tokens per execution, with no tool charges, 1,000 executions cost:

| Model | Input | Output | Total per 1K executions |
|---|---:|---:|---:|
| `gpt-5.6-sol` | $10.00 | $15.00 | **$25.00** |
| `gpt-5.6-terra` | $4.00 | $6.00 | **$10.00** |
| `gpt-5.6-luna` | $0.40 | $0.60 | **$1.00** |

Reasoning tokens belong in `O_reasoning`; omitting them systematically underestimates cost and context use. Prices are point-in-time values as of the research date and must be re-read before budgeting. [[8]](https://developers.openai.com/api/docs/guides/reasoning) [[17]](https://developers.openai.com/api/docs/pricing)

### Caching

- OpenAI prompt caching is prefix based. GPT-5.6+ requires at least 1,024 tokens through a cache breakpoint; the current write price is 1.25x uncached input and read price 0.1x, cache routing uses `prompt_cache_key`, and the docs recommend keeping each key near 15 requests/minute because higher traffic can reduce hits. Cached prefixes remain eligible for 30 minutes and may persist longer; manual clearing is unavailable. [[18]](https://developers.openai.com/api/docs/guides/prompt-caching)
- For one eligible OpenAI prefix reused `R` times, token-price units are `1.25 + 0.1*(R-1)` versus `R` uncached. `[inferred]` This breaks even on the second use, ignoring misses and operational complexity. Put stable instructions, tool schemas, and reference context first; append volatile user data after the breakpoint.
- Anthropic currently supports automatic or explicit prefix breakpoints, a default five-minute TTL, and an optional one-hour TTL whose write costs 2x base input. A hit refreshes the five-minute lifetime. [[19]](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Semantic caching is an application-level approximate-answer cache, not prompt/KV caching. `[inferred]` Key it by tenant, authorization scope, model/prompt/schema version, normalized intent, and source-data version; never reuse a cached tool authorization decision or personalized answer across principals.

### Routing, batching, and back-pressure

- `[inferred]` A defensible router sends deterministic extraction/classification to `luna`, ordinary synthesis and bounded tools to `terra`, and ambiguous/high-risk/complex work to `sol`; it escalates on validation failure, explicit uncertainty, high-risk intent, or eval-derived thresholds. RouteLLM reported more than 2x cost reduction in some benchmark settings without quality loss, but that is research evidence, not a universal production guarantee. [[20]](https://arxiv.org/abs/2406.18665)
- OpenAI Batch is asynchronous, has a 24-hour completion window, costs 50% less than synchronous APIs, uses a separate rate-limit pool, and accepts up to 50,000 requests/200 MB per batch. It is for evals, enrichment, and offline extraction, not an interactive request path. [[21]](https://developers.openai.com/api/docs/guides/batch)
- Limits are provider-, account-, region-, and model-specific. AWS documents one current Bedrock example with 15M input TPM for Claude 4.7+ on `bedrock-runtime`, while other models vary; its guidance is to plan 2-3x peak throughput and use jittered exponential backoff for 503s. Treat these as platform-specific examples. [[22]](https://docs.aws.amazon.com/bedrock/latest/userguide/scaling-throughput-best-practices.html)
- Back-pressure should be explicit: `[inferred]` bound the queue, reserve separate concurrency pools for interactive/batch/high-risk traffic, reject or degrade before saturation, cap output/reasoning tokens, and use a token bucket on estimated input plus maximum output. Honor `Retry-After`; otherwise use bounded exponential backoff with jitter, because failed requests can still count against rate limits. [[23]](https://developers.openai.com/api/docs/guides/rate-limits)

## 3. Distributed Resilience & State

### Durable execution and checkpointing

- LLM provider calls do not make the business workflow durable. `[inferred]` Persist a run record before inference and checkpoint after each boundary: request accepted, model response received, tool call proposed, approval granted, side effect committed, tool result recorded, final output validated. Store model snapshot, prompt/schema/tool versions, token usage, `call_id`, external idempotency key, and result digest.
- Temporal records workflow Event History and replays from the last event after failure; workers execute application code while the service persists state. Its model fits long-running tool workflows because model/tool calls can be activities and approvals/signals can be awaited, but workflow code must remain deterministic and side-effecting activities must still be idempotent. [[24]](https://docs.temporal.io/workflow-execution)
- Kafka is useful for a high-throughput event log and worker fan-out. It persists ordered partitions, checkpoints consumer offsets, and allows rewind/re-consumption; at-least-once delivery can duplicate a tool action if the consumer commits after the side effect. `[inferred]` Use an outbox plus a unique `(tenant_id, call_id)` constraint, or a transactional sink, rather than assuming the broker makes an arbitrary external API exactly-once. [[25]](https://kafka.apache.org/43/design/design/)

### Concurrency and consistency

- `[inferred]` Prefer optimistic concurrency (`run.version` compare-and-swap) for conversation/run state. Partition queue traffic by `run_id` or use an orchestrator with exclusive workflow state. Lease a tool call to one worker with an expiry and fence stale workers with an incrementing lease/version. Never hold a database lock while waiting for model inference or a human approval.
- `[inferred]` Replay must reuse recorded model output and tool results, not re-query a nondeterministic model during state reconstruction. A deliberate re-run is a new attempt linked to the old one and may produce a different answer; record that lineage.
- `[inferred]` Prevent state drift by treating the append-only event/trajectory as authoritative and materialized run state as a projection. Reconcile orphaned `proposed` calls, timed-out `executing` calls, and completed external actions missing a committed result.

### Timeouts, circuit breakers, and fallbacks

- Use a deadline budget across model and tool hops: `[inferred]` pass the remaining deadline downstream, set each timeout below it, and leave time to return a controlled response. Retrying every layer creates retry amplification.
- A circuit breaker transitions Closed -> Open after a time-window failure threshold, fails fast while Open, then permits limited Half-Open probes. Azure's reference pattern emphasizes that the half-open state prevents a recovering dependency from being flooded. Tune separate breakers by provider/model/region and classify 400/schema/policy errors as non-transient. [[26]](https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker)
- `[inferred]` Graceful degradation chain: same model in another approved region -> lower-cost compatible model with the same schema contract -> cached/read-only answer -> queue for later -> explicit unavailable response. Never silently downgrade a high-risk workflow to a model that has not passed its eval gate.

## 4. Enterprise Security & Governance

### Identity, permissions, and zero trust

- Function schemas describe possible calls; they are not an authorization mechanism. `[inferred]` The dispatcher must authenticate the user/workload, intersect requested tools with tenant policy, authorize each call and resource, mint short-lived downstream credentials, and re-check policy immediately before execution. Provider RBAC can scope platform resources: OpenAI documents organization/project roles and granular permissions, but application tool RBAC remains the customer's responsibility. [[27]](https://developers.openai.com/api/docs/guides/rbac)
- `[inferred]` For future MCP/interoperability layers, apply the same zero-trust boundary: authenticated server identity, encrypted transport, allowlisted capabilities, per-call user delegation, egress control, and no ambient credentials. Function calling by itself supplies a name, arguments, and call ID; it does not establish tool-server identity or confer permission.
- Strict output narrows syntax but does not make a call safe. OpenAI recommends structured fields between nodes, keeping tool approvals enabled, and preventing arbitrary untrusted text from directly driving actions. [[28]](https://developers.openai.com/api/docs/guides/agent-builder-safety)

### PII, sandboxing, and data controls

- Scan both input and output with deterministic patterns for known identifiers plus contextual ML/NER for names, addresses, and free-form sensitive data; tokenize or mask before model calls when possible. Bedrock Guardrails supports built-in PII entities and custom regex with `BLOCK`, `ANONYMIZE`, or detect-only behavior. Its documented limitation is critical: the sensitive-information filter does **not** detect PII inside `tool_use` parameters, and invocation logs can retain original unmasked input, so tool arguments and logs need separate controls. [[29]](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)
- OpenAI documents default abuse-monitoring retention up to 30 days, optional approved Zero Data Retention controls, and endpoint-specific application-state behavior; `/v1/responses` storage and prompt-cache KV retention have distinct rules. Select endpoints and `store` behavior from the data classification, not convenience. [[30]](https://developers.openai.com/api/docs/guides/your-data)
- `[inferred]` Sandbox choice follows tool risk: a process sandbox is light but shares the kernel; a container adds namespaces/cgroups and is the common default for untrusted code; microVMs provide a stronger kernel boundary at higher startup/ops cost; WASM offers capability-oriented isolation but limited OS/library compatibility. Disable ambient network/filesystem access and mount only per-task data.

### Audit record

`[inferred]` Emit an append-only audit event for every model and tool boundary with `event_id`, `trace_id`, `run_id`, `tenant_id`, actor/workload identity, model snapshot, prompt/schema/tool version hashes, input/output digests or redacted payloads, policy decision and version, approval identity, `call_id`, timestamps, token/cost counters, result/error, and parent event. OpenTelemetry's GenAI conventions define common model/operation/tool attributes, but capture of prompts and tool arguments should be opt-in because they may be sensitive. [[31]](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)

`[inferred]` Send security/audit events to immutable or WORM retention with access logging and tenant separation. SOC 2, HIPAA, and GDPR readiness depends on the entire system's access, retention, deletion, incident, and vendor controls; schema-valid output or a provider certification cannot make the application compliant by itself.

## 5. Production Failure Modes

| Failure | Detection | Mitigation |
|---|---|---|
| Context-window exhaustion or degradation | token budget, `incomplete` status, rising reasoning tokens, long-context position evals | reserve output/reasoning headroom, trim/summarize, retrieve relevant evidence, put critical instructions at boundaries; test middle-position recall |
| Infinite tool/reasoning loop | repeated `(tool, normalized_args)` hash, no-progress counter, steps/wall-clock/token spend | max steps, max repeated call, budget cap, explicit terminal states, human escalation |
| Hallucinated tool or parameters | unknown tool, schema/semantic validation error, missing resource, impossible enum combination | tool allowlist, strict schema, business validation, one correction retry, then fail closed |
| Duplicate side effect | repeated `call_id`/idempotency key, external reconciliation mismatch | idempotency ledger, outbox, unique constraint, read-before-retry, compensating action |
| State drift | event/projection mismatch, tool completed without result, version conflict | append-only events, CAS, reconciliation worker, fenced leases |
| Cascading timeout/retry storm | p95/p99 growth, queue depth, 429/503 and retry amplification | deadline propagation, one retry owner, jitter, bulkheads, circuit breaker, load shedding |
| Schema-valid but wrong output | domain-rule/evidence failure despite successful parse | semantic validation, referential checks, deterministic recomputation, human review |
| Refusal or truncated structured output | refusal item, incomplete status/finish reason, missing parsed object | handle as a first-class union state; do not force-parse or execute partial output |

- Long context is not equivalent to reliable context use. *Lost in the Middle* found performance often highest when relevant information was at the beginning or end and significantly worse in the middle, including for explicitly long-context models tested. Use task-specific positional evals rather than a context-window marketing number. [[32]](https://arxiv.org/abs/2307.03172)
- Strict decoding prevents unsupported tokens, but it cannot know whether `account_id` belongs to the user, a date exists, an amount violates a limit, or a tool's description was misunderstood. The JSONSchemaBench paper evaluates compliance, coverage, efficiency, and output quality as separate dimensions for this reason. [[16]](https://arxiv.org/abs/2501.10868)
- Reasoning exhaustion can charge for input and hidden reasoning without producing visible output. Begin with provider-recommended headroom, measure actual reasoning-token distributions, and alert on `incomplete_details.reason=max_output_tokens`. [[8]](https://developers.openai.com/api/docs/guides/reasoning)
- Excessive agency converts a model mistake into a security incident. OWASP attributes this to excessive functionality, permissions, or autonomy and recommends reducing tools/scopes and requiring review for consequential actions. [[33]](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

> ⚠️ Limited public data available for real-world incident post-mortems. Providers publish error behavior and mitigations, but detailed model-specific outages involving hallucinated function parameters, reasoning loops, or schema compilation are rarely disclosed with enough traffic and timeline data for quantitative comparison.

## 6. Enterprise System Design Scenarios

### Scenario A: High-volume support-ticket extraction

`[inferred]` Use a low-cost model, no/low reasoning, a versioned strict response schema, and deterministic validators. Keep the stable extraction policy/schema in the cached prefix; place ticket text last. Run synchronous traffic only when users wait for the result; route backlog to Batch. Store the raw ticket under normal data controls and the parsed result with `schema_version`. The model does not need any mutating tool.

### Scenario B: Regulated account action

`[inferred]` Separate **propose** from **execute**. A reasoning-capable model may produce a strict proposal `{action, account_id, amount, evidence_ids, risk_flags}`. Deterministic services then authenticate, authorize, fetch the canonical account, recompute limits, run PII/policy checks, and request approval. Only an idempotent execution worker receives a short-lived scoped credential. A second model call may summarize the committed result, but its text is not the transaction record.

### Scenario C: Long-running research/enrichment job

`[inferred]` API -> durable workflow/queue -> model workers -> read-only tools -> validator -> result store. Checkpoint every call/result boundary; partition by tenant/run; bound concurrency by predicted tokens; cache common corpora; use batch/flex for noninteractive stages. Temporal supplies event-history replay, while Kafka supplies log-based fan-out; neither removes the need for idempotent external side effects. [[24]](https://docs.temporal.io/workflow-execution) [[25]](https://kafka.apache.org/43/design/design/)

### Trade-off matrix

| Approach | Cost | Latency | Operational complexity | Security/reliability | Best fit |
|---|---|---|---|---|---|
| One-shot text generation | low-medium | lowest | low | weak machine contract | prose/summarization |
| Strict structured output | low-medium | low; first unseen schema may compile | low-medium | syntactic contract; semantic checks still required | extraction/classification/UI data |
| Function loop | medium-high; multiple calls | tool + extra model round trips | medium | strong when dispatcher validates/authorizes | live data and bounded actions |
| Self-consistency/multi-sample reasoning | high, roughly proportional to samples | high unless parallelized | medium | improves some benchmark accuracy, not guaranteed faithfulness | hard offline decisions with verifier |
| Durable workflow + model/tools | medium-high platform cost | queue/checkpoint overhead | high | replay, approvals, compensation, isolation | long-running/consequential work |
| Self-hosted optimized inference | hardware/ops dependent | controllable | highest | maximum data/control responsibility | steady high volume or residency constraints |

### Capacity-planning equations

```text
required_input_TPM  = peak_RPS * 60 * p95_input_tokens
required_output_TPM = peak_RPS * 60 * p95(output_tokens + reasoning_tokens)
max_inflight        = peak_RPS * p99_end_to_end_seconds
queue_drain_seconds = queued_tokens / sustainable_tokens_per_second

KV_bytes_per_sequence ≈
  2 * num_layers * num_kv_heads * head_dim * sequence_tokens * bytes_per_element
```

`[inferred]` The factor `2` is for keys and values. Add allocator/block metadata, temporary activations, model weights, fragmentation, and safety margin before converting this estimate into GPU concurrency. Capacity tests must mix realistic prompt/output lengths because long sequences consume more KV memory and reasoning raises output-token demand. Paged KV management can reduce waste but does not make memory unlimited. [[5]](https://arxiv.org/abs/2309.06180)

### Principal-level interview conclusions

1. A Transformer predicts tokens; the production system supplies state, tools, policy, identity, and recovery.
2. Reasoning is a test-time compute/quality lever whose hidden tokens affect cost, latency, and context; it is not a trustworthy proof trace.
3. Function calling is an untrusted typed proposal. Execute only after schema, semantic, authorization, and idempotency checks.
4. Structured output guarantees shape for a supported schema, not truth or permission.
5. Optimize against an eval-gated quality/cost/latency frontier, and measure p50/p95/p99 on the exact workload instead of borrowing vendor or paper benchmarks.

## Sources

- [1] https://arxiv.org/abs/1706.03762 - *Attention Is All You Need*, original Transformer paper.
- [2] https://arxiv.org/abs/2005.14165 - GPT-3 autoregressive language-model and in-context learning paper.
- [3] https://arxiv.org/abs/2402.00838 - OLMo architecture/training report with modern decoder component comparison.
- [4] https://arxiv.org/abs/2205.14135 - FlashAttention algorithm and research benchmarks.
- [5] https://arxiv.org/abs/2309.06180 - PagedAttention/vLLM memory-management and throughput paper.
- [6] https://arxiv.org/abs/2201.11903 - Chain-of-thought prompting paper.
- [7] https://arxiv.org/abs/2203.11171 - Self-consistency reasoning paper.
- [8] https://developers.openai.com/api/docs/guides/reasoning - Current reasoning-token, effort, context, and incomplete-response behavior.
- [9] https://arxiv.org/abs/2505.05410 - Empirical study of reasoning-chain faithfulness.
- [10] https://developers.openai.com/api/docs/guides/function-calling - Current function-call lifecycle, IDs, strict and parallel calls.
- [11] https://ai.google.dev/gemini-api/docs/tools - Gemini built-in/custom tool execution and structured-output distinction.
- [12] https://arxiv.org/abs/2302.04761 - Toolformer paper.
- [13] https://developers.openai.com/api/docs/guides/structured-outputs - Structured Outputs versus JSON mode, SDK parsing, refusals, schema rules.
- [14] https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use - Claude strict tool schemas and grammar-constrained sampling.
- [15] https://openai.com/index/introducing-structured-outputs-in-the-api/ - Constrained-decoding implementation description.
- [16] https://arxiv.org/abs/2501.10868 - JSONSchemaBench constrained-decoding evaluation paper.
- [17] https://developers.openai.com/api/docs/pricing - Current model token prices.
- [18] https://developers.openai.com/api/docs/guides/prompt-caching - OpenAI cache thresholds, pricing, routing, retention, and troubleshooting.
- [19] https://platform.claude.com/docs/en/build-with-claude/prompt-caching - Claude cache modes and TTLs.
- [20] https://arxiv.org/abs/2406.18665 - RouteLLM cost/quality routing paper.
- [21] https://developers.openai.com/api/docs/guides/batch - Batch cost, limits, and turnaround.
- [22] https://docs.aws.amazon.com/bedrock/latest/userguide/scaling-throughput-best-practices.html - Bedrock quotas, retry, regional, and capacity guidance.
- [23] https://developers.openai.com/api/docs/guides/rate-limits - Rate-limit headers and bounded exponential-backoff guidance.
- [24] https://docs.temporal.io/workflow-execution - Durable execution, Event History, replay, and workflow state.
- [25] https://kafka.apache.org/43/design/design/ - Kafka persistence, offsets, batching, and delivery semantics.
- [26] https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker - Circuit-breaker states and recovery behavior.
- [27] https://developers.openai.com/api/docs/guides/rbac - Organization/project roles and granular platform permissions.
- [28] https://developers.openai.com/api/docs/guides/agent-builder-safety - Structured data flow, approvals, guardrails, and untrusted-input guidance.
- [29] https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html - PII block/mask modes and documented tool/log limitations.
- [30] https://developers.openai.com/api/docs/guides/your-data - Endpoint retention, ZDR, storage, and cache data controls.
- [31] https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/ - GenAI telemetry attributes for models, operations, and tools.
- [32] https://arxiv.org/abs/2307.03172 - *Lost in the Middle* long-context evaluation.
- [33] https://genai.owasp.org/llmrisk/llm062025-excessive-agency/ - Excessive functionality, permissions, and autonomy risk model.
