# Research: Context Engineering - Prompting, Context Management, Compression, Caching

**Date researched**: 2026-08-21
**Sources consulted**: 34

## Scope and evidence labels

This brief covers all four roadmap subtopics: prompting, context management, compression, and caching. A plain statement is documented by a linked primary source or current official documentation. `[inferred]` marks an architecture recommendation derived from those mechanics rather than a provider guarantee. Vendor thresholds, prices, and rate limits are point-in-time facts as of the research date; benchmark results are not production SLAs.

## 1. System Topology & Mechanics

### Context engineering as a compiled data product

- Anthropic defines context engineering as curating and maintaining the optimal set of tokens available to a model during inference. The context is broader than a prompt string: it can contain system instructions, message history, tool definitions and results, MCP data, retrieved documents, images, and other external state. Its stated principle is to find the smallest set of high-signal tokens that maximizes the desired outcome. [[1]](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- `[inferred]` Treat the model input as a **compiled artifact**, not the source of truth. The canonical sources are a versioned prompt, append-only conversation events, tool registry, policy decision, retrieval results with provenance, and workflow state. A context compiler filters, ranks, budgets, orders, renders, token-counts, and hashes those sources for one model call. This makes the exact input reproducible without asking the model to reconstruct history.

```text
CONTROL PLANE
prompt/schema registry | model policy | tenant policy | eval gates | rollout/rollback
                              |
DATA PLANE                   v
user/event log -> auth + trust labels -> retrieval/tool selection -> dedupe/rank
     -> token budget -> role/order renderer -> cache breakpoints -> token count
     -> model -> validated output/tool result -> append-only trajectory + metrics
```

`[inferred]` The control plane owns prompt versions, templates, model snapshots, compression policies, cache TTL policy, tenant quotas, and eval-based promotion. The data plane builds a request and processes its response. Identity, authorization, retention, and budget decisions must not be delegated to text in the active context.

### Prompting mechanics

- Role precedence is part of the API contract, not merely prose style. OpenAI documents that developer messages are prioritized ahead of user messages, and recommends placing overall role/tone guidance in the system/developer layer while keeping request-specific details in the user message. [[2]](https://developers.openai.com/api/docs/guides/prompting) [[3]](https://developers.openai.com/api/docs/guides/prompt-engineering)
- Delimit instruction, examples, reference context, and variable input using unambiguous Markdown headings or XML-like tags. OpenAI recommends clear section boundaries; Anthropic recommends relevant, diverse, structured examples and currently suggests 3-5 examples as a starting point for Claude. The number is provider guidance, not a universal optimum: select examples using task evals and their token opportunity cost. [[3]](https://developers.openai.com/api/docs/guides/prompt-engineering) [[4]](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)
- Few-shot examples define behavior in-context without changing weights, but poor examples can teach the wrong shortcut. `[inferred]` Each example should cover a real decision boundary, include the exact output contract, and be regression-tested independently. Do not fill the window with redundant demonstrations merely because capacity exists.
- Ordering is model- and workload-dependent. Anthropic recommends putting large documents near the top and the query near the end for long-context Claude prompts and reports up to 30% improvement in its tests; the older cross-model *Lost in the Middle* study found that relevant evidence was often used best at the beginning or end and worse in the middle. Treat both as signals to evaluate position, not as a universal layout law. [[4]](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) [[5]](https://arxiv.org/abs/2307.03172)
- OpenAI recommends pinning model snapshots and running evals when prompts change. Its current guidance also favors storing production prompt source with application code for typed inputs, code review, and tests; the API reusable-prompts feature is documented as deprecated on June 3, 2026 with shutdown scheduled for November 30, 2026. [[3]](https://developers.openai.com/api/docs/guides/prompt-engineering)

### Context lifecycle and active-window policy

- Context-window accounting includes more than visible user text. Anthropic documents that system instructions, tool definitions/results, images/documents, output, and thinking can consume the window. Prompt-cached tokens still occupy that window: caching changes prefill cost and latency, not the amount of context seen by the model. [[6]](https://platform.claude.com/docs/en/build-with-claude/context-windows)
- OpenAI conversation chaining with `previous_response_id` simplifies state transport but does not make earlier tokens free: all prior input tokens in the chain are still billed. Response objects are retained for 30 days by default unless `store=false`; Conversation objects and their items have different retention semantics and are not subject to that 30-day response TTL. [[7]](https://developers.openai.com/api/docs/guides/conversation-state)
- `[inferred]` Establish an explicit budget before retrieval or rendering:

```text
B_active = W_model - O_reserved - R_reserved - safety_margin

system + tools + selected_history + retrieved_evidence + current_request <= B_active
```

`W_model` is the provider window, `O_reserved` the maximum visible output, and `R_reserved` any reasoning allowance that shares the limit. The safety margin covers tokenizer variance and provider-added tokens. Count with the target model's tokenizer; Anthropic's token-count endpoint accepts system, tools, images, and PDFs and notes that its estimate can differ slightly from final usage. [[8]](https://platform.claude.com/docs/en/build-with-claude/token-counting)
- `[inferred]` Use an immutable `context_manifest` for every call: `{run_id, tenant_id, model_snapshot, prompt_version, renderer_version, source_event_ids, retrieval_doc_ids+versions, tool_schema_versions, policy_version, compression_checkpoint_id, token_counts, cache_key, rendered_digest}`. Store sensitive text under the data-retention policy; the manifest can retain hashes and IDs when raw content must expire.

### Compression strategies and semantics

Compression is not one operation. Each strategy loses different information and requires a different recovery path:

| Strategy | Mechanics | Best fit | Principal risk |
|---|---|---|---|
| Deterministic pruning | Remove duplicate, expired, or known-irrelevant blocks; preserve IDs/provenance | stale retrieval, superseded state, old tool output | policy bug removes required evidence |
| Extractive compression | Select original sentences/chunks | factual evidence and citations | misses distributed/multi-hop evidence |
| Abstractive summary | Generate a shorter state summary | long conversations and completed phases | omission, fabrication, provenance loss |
| Token-level prompt compression | Rank/drop tokens using a smaller model or information score | large documents under cost pressure | grammar distortion and downstream distribution shift |
| Provider compaction | API creates a compaction/summary item and resumes from it | long-running agent loops | opaque or lossy checkpoint; provider coupling |
| Context editing | Clear old tool results or reasoning blocks | tool-heavy histories | later step unexpectedly needs removed detail |

- Selective Context prunes low-information context; its paper reports 50% context-cost reduction with 36% lower inference memory and 32% lower inference time, with small reported quality drops on its four evaluated applications. LLMLingua reports up to 20x compression with little loss on its evaluated datasets, while LongLLMLingua reports 1.4x-2.6x end-to-end latency acceleration for roughly 10K-token prompts compressed 2x-6x. These are method-specific research results, not safe defaults for arbitrary enterprise tasks. [[9]](https://arxiv.org/abs/2310.06201) [[10]](https://arxiv.org/abs/2310.05736) [[11]](https://arxiv.org/abs/2310.06839)
- OpenAI server-side compaction can trigger at `compact_threshold`, emits an opaque encrypted compaction item, and supports stateful and stateless chaining. The standalone `/responses/compact` endpoint is stateless and Zero Data Retention (ZDR) friendly; OpenAI instructs clients to pass the returned compacted window forward as the canonical next input rather than edit it. [[12]](https://developers.openai.com/api/docs/guides/compaction)
- Anthropic server-side compaction triggers at a configured threshold, generates a summary in a `compaction` block, and on later requests drops blocks before that checkpoint. Its context-editing API can instead remove old tool results or thinking blocks; the client retains the unmodified history, and clearing a prefix can invalidate prompt caching. [[13]](https://platform.claude.com/docs/en/build-with-claude/compaction) [[14]](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- `[inferred]` A summary is a materialized view, never the only record. Preserve the canonical event log and source documents so a later step can rehydrate exact evidence. Summaries should contain task goal, decisions, unresolved questions, constraints, committed side effects, identifiers, and source pointers; never summarize authorization grants into prose and then treat that prose as permission.

### Cache taxonomy: do not conflate the layers

| Cache | Key | Reused value | Quality/security concern |
|---|---|---|---|
| Provider prompt/KV cache | exact token prefix plus provider routing metadata | precomputed attention KV state | prefix mutation misses; shared-cache side channel |
| Self-hosted prefix cache | exact token blocks/model config | GPU/host KV blocks | eviction, placement, tenant isolation |
| Retrieval cache | normalized query + corpus/filter/version | document IDs/chunks | stale corpus or changed ACL |
| Deterministic response cache | exact normalized request + all versions | final validated answer | invalidation and personalization |
| Semantic response cache | embedding similarity + policy | approximate prior answer | false match, stale facts, cross-tenant leakage |
| Compiled-artifact cache | prompt/schema/tool version | rendered static prefix or grammar | rollout invalidation |

- OpenAI prompt caching requires exact prefix matches. Stable instructions, examples, tool definitions, and schemas should come first; volatile content should come later. GPT-5.6+ uses explicit breakpoints with a strict 1,024-token minimum, current 30-minute exact TTL, and `prompt_cache_key` for routing; changing content before a breakpoint causes a miss. [[15]](https://developers.openai.com/api/docs/guides/prompt-caching)
- Anthropic supports automatic and explicit prefix caching. Its standard cache write is 1.25x base input for five minutes, a one-hour write is 2x, and reads are 0.1x; context editing that clears cached content forces a new write. [[16]](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) [[17]](https://platform.claude.com/docs/en/about-claude/pricing)
- Gemini implicit caching is enabled for Gemini 2.5 and newer models, reports hits in usage metadata, and recommends common content at the beginning. Its explicit cache is a named, immutable content resource with a user-managed TTL and storage charge; cached tokens still count toward model token limits. [[18]](https://ai.google.dev/gemini-api/docs/caching) [[19]](https://ai.google.dev/gemini-api/docs/generate-content/caching)
- vLLM automatic prefix caching hashes token blocks and can include a per-request `cache_salt` in the first block hash to restrict reuse to an agreed trust group and reduce timing-side-channel risk. [[20]](https://docs.vllm.ai/en/stable/design/prefix_caching/)

### Reference code pattern

```python
def compile_context(run, request, model):
    policy = authorize_and_label(run.tenant_id, request)
    prompt = prompt_registry.get(run.prompt_version)
    tools = tool_registry.authorized_schemas(policy)

    candidates = load_relevant_events(run.id) + retrieve(request, policy.filters)
    selected = budget_and_rank(
        dedupe(candidates),
        max_tokens=active_budget(model, reserved_output=run.output_cap),
        preserve=["goal", "constraints", "side_effects", "source_ids"],
    )
    rendered = render_stable_prefix_first(prompt, tools, selected, request)
    counts = count_tokens(model, rendered)
    manifest = persist_manifest(run, rendered, counts, policy)
    return rendered, manifest
```

`[inferred]` `authorize_and_label` must execute before retrieval and caching; `budget_and_rank` must be deterministic for the same versioned inputs where replay matters; `persist_manifest` should occur before inference so even timed-out attempts are auditable.

## 2. Token Economics & NFR Metrics

### Latency model and measurable SLOs

`[inferred]` Decompose latency to show where context work pays off:

```text
T_total = T_queue + T_retrieval + T_compile + T_token_count + T_cache_lookup
        + T_prefill(uncached_input) + T_decode(output + reasoning)
        + T_compaction_if_triggered + T_tools + T_retries
```

Track p50/p95/p99 for total latency and time-to-first-token (TTFT), plus input tokens before/after compression, cache-read/write tokens, cache hit rate by prompt version, retrieval latency, compaction frequency, summary-retention score, and context-overflow rate. Google documents that longer input generally raises TTFT, while caching is intended to reduce repeated processing cost; cache benefit still depends on workload and provider. [[21]](https://ai.google.dev/gemini-api/docs/long-context)

> ⚠️ Limited public data available for this dimension. Hosted providers do not publish stable cross-region p50/p95/p99 latency SLAs segmented by model, context length, cache state, compaction, and service tier. Measure the exact snapshot and production-shaped token distribution; paper speedups are not hosted-API guarantees.

### Token-cost formulas and cache break-even

For 1,000 executions:

```text
C_1000 = (U*P_input + H*P_cache_read + W*P_cache_write
          + (O_visible + O_reasoning)*P_output) / 1,000,000
          + C_retrieval + C_compression + C_storage + C_tools
```

`U`, `H`, `W`, and `O` are aggregate tokens across the 1,000 calls. Count provider compaction output, application summarizer calls, embeddings/reranking, cache storage, and retry misses; otherwise the optimization can appear cheaper by moving cost outside the model line item.

OpenAI's current short-context standard prices per 1M tokens are: `gpt-5.6-sol` $5 input/$0.50 cached/$6.25 cache-write/$30 output; `gpt-5.6-terra` $2/$0.20/$2.50/$12; and `gpt-5.6-luna` $0.20/$0.02/$0.25/$1.20. [[22]](https://developers.openai.com/api/docs/pricing)

Worked example: 1,000 `terra` requests share an 8,000-token exact prefix, each adds 1,500 uncached tokens and produces 600 output tokens. Assuming one cache write, 999 hits, no misses, no reasoning/tool/storage charges, and all requests within cache eligibility:

| Component | Calculation | Cost |
|---|---:|---:|
| Prefix write | `8,000 * $2.50 / 1M` | $0.02 |
| Prefix reads | `7,992,000 * $0.20 / 1M` | $1.5984 |
| Dynamic input | `1,500,000 * $2 / 1M` | $3.00 |
| Output | `600,000 * $12 / 1M` | $7.20 |
| **Total** | | **$11.8184** |
| Same calls, prefix uncached | `9.5M*$2/1M + 0.6M*$12/1M` | **$26.20** |

This idealized example saves about 54.9%; production savings fall with misses, version churn, sparse reuse, or added compression/retrieval costs. Prices must be re-read before budgeting.

For an eligible prefix in base-input price units:

```text
uncached after N uses = N
OpenAI/Anthropic 5m cache = 1.25 + 0.1*(N-1)
Anthropic 1h cache = 2.0 + 0.1*(N-1)
```

Thus a five-minute cache becomes cheaper on the second total use; Anthropic's one-hour cache becomes cheaper after two cache reads (three total uses), matching its pricing guidance. [[17]](https://platform.claude.com/docs/en/about-claude/pricing)

### Compression economics and quality gates

- `[inferred]` Compress only when `C_compress + C_shorter_calls + expected_quality_loss_cost < C_full_calls`. A model-based summary can lose money on a one-shot request even if the resulting prompt is shorter. It is most attractive when the compressed checkpoint will be reused many times or prevents a hard overflow.
- Compression ratio alone is a vanity metric. Gate deployment on task success, evidence recall, citation correctness, constraint retention, decision/state retention, and adversarial-instruction preservation/rejection at several context lengths and evidence positions. LongBench covers six long-context task categories; RULER adds multi-needle, multi-hop, and aggregation tasks and found that many tested models degraded with length despite strong simple needle retrieval. [[23]](https://arxiv.org/abs/2308.14508) [[24]](https://arxiv.org/abs/2404.06654)
- `[inferred]` Use a risk-tiered policy: deterministic prune first, retrieve exact source second, extractive compress third, abstractive summarize only when needed. High-risk legal, financial, medical, or side-effect state should retain exact source spans and identifiers even if surrounding narrative is summarized.

### Throughput, routing, and back-pressure

- Prefix caches are locality-sensitive. OpenAI documents cache routing using `prompt_cache_key` plus prefix hash and recommends keeping a key around 15 requests/minute because higher volume can reduce hits; partition high-volume traffic without destroying prefix reuse. [[15]](https://developers.openai.com/api/docs/guides/prompt-caching)
- Anthropic enforces requests/minute, input-tokens/minute, and output-tokens/minute limits. For most current Claude models, cache reads do not count toward input-token limits; its documentation gives a provider-specific example in which a 2M ITPM allocation with 80% cache hits can process 10M total input tokens/minute. This is not a portable capacity number. [[25]](https://platform.claude.com/docs/en/api/rate-limits)
- Distributed self-hosted prefix caching couples scheduling to GPU state. Preble's research scheduler co-optimizes prefix reuse and load balance and reported 1.5x-14.5x lower average latency and 2x-10x lower p99 in its tested 2-8 GPU workloads. Treat this as research evidence that naive round-robin can destroy locality, not as a vLLM production guarantee. [[26]](https://arxiv.org/abs/2407.00023)
- `[inferred]` Back-pressure on estimated **uncached prefill tokens** and output tokens, not only request count. Maintain bounded interactive and batch queues, a concurrency cap for compaction jobs, deadline-aware admission, and a degradation chain: cached exact response -> smaller context/retrieval top-k -> compatible cheaper model -> asynchronous queue -> explicit unavailable response. Never degrade by dropping policy or authorization context.

## 3. Distributed Resilience & State

### Canonical state, checkpoints, and replay

- `[inferred]` Persist the full conversation/trajectory as append-only events and store compacted context as a versioned checkpoint derived from a closed event range: `{checkpoint_id, first_event, last_event, strategy_version, source_digest, summary, retained_source_refs, token_count}`. Advance the run's checkpoint pointer with compare-and-swap. A losing concurrent compactor may leave an unreferenced artifact for garbage collection but cannot overwrite newer state.
- Temporal documents durable workflows that resume after crashes, network failures, or outages. `[inferred]` In a context pipeline, retrieval, summarization, and provider inference should be activities whose completed results are recorded; deterministic workflow replay must reuse those recorded results rather than invoke a nondeterministic model again. [[27]](https://docs.temporal.io/)
- `[inferred]` Checkpoint before and after every lossy boundary: raw events committed, context manifest frozen, compaction requested, compacted artifact received, artifact quality-checked, checkpoint activated. If failure occurs between creation and activation, the old checkpoint remains authoritative. Do not delete covered raw events merely because a summary succeeded; retention should be a separate governed lifecycle.
- OpenAI stateless compaction returns a new canonical compacted window, while Anthropic compaction expects the response containing the compaction block to be appended to later messages. Provider-specific adapters must preserve those semantics rather than normalizing opaque blocks into editable text. [[12]](https://developers.openai.com/api/docs/guides/compaction) [[13]](https://platform.claude.com/docs/en/build-with-claude/compaction)

### Concurrency, ordering, and cache consistency

- `[inferred]` Partition event consumption by `run_id` so one logical conversation has ordered writes. Use optimistic concurrency for UI/API updates and a short lease with fencing token for a compaction worker. Never hold a database lock across retrieval, model inference, or human approval.
- `[inferred]` Version every cache key with `tenant_id`, authorization-scope digest, model snapshot, tokenizer, prompt/renderer/schema/tool versions, corpus snapshot, and compression strategy. A prompt rollout creates a new namespace; do not bulk-delete active keys on the request path.
- `[inferred]` Prevent a cache stampede with request coalescing/singleflight per exact key, bounded fill concurrency, negative caching only for deterministic misses, and jittered TTLs for application caches. Provider prefix caches generally cannot be explicitly invalidated, so versioned prefixes are the rollback mechanism.
- `[inferred]` A semantic answer cache needs stricter invalidation than a KV cache because it reuses meaning, not computation. Return the cached answer only if tenant, principal/access scope, locale, policy, prompt/model contract, source-data version, and freshness window all match; re-run authorization even on a hit.

### Timeouts, breakers, and regional fallbacks

- Azure's circuit-breaker pattern separates transient retry from persistent failure: Closed routes calls, Open fails fast, and Half-Open permits a limited number of probes so a recovering service is not flooded. Its transient-fault guidance recommends finite retries, aggregate retry budgets, exponential backoff, and no more than one immediate retry. [[28]](https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker) [[29]](https://learn.microsoft.com/en-us/azure/architecture/best-practices/transient-faults)
- `[inferred]` Use separate breakers for retrieval, token-counting, compaction, model/region, and cache storage. A failed optional cache lookup should bypass to uncached inference; a failed mandatory policy/retrieval ACL service should fail closed. A failed compactor may continue from the prior valid checkpoint only while sufficient window remains.
- `[inferred]` Carry one deadline across compile, model, and tools. Retrying a timed-out model call with the same idempotency key is not equivalent to replaying a deterministic result; record every attempt and accept only one committed response with a version check. Avoid nested SDK, service-mesh, and workflow retries that multiply requests.

### Graceful degradation chain

1. Use the current valid context checkpoint and bypass a failed optional application cache.
2. Route to an approved compatible model/region with the same prompt and output-contract eval gate.
3. Reduce retrievable breadth using a pretested top-k policy, never by deleting system/security instructions.
4. Return a source-backed cached answer only if its scope and freshness policy pass.
5. Queue the run for later or return an explicit partial/unavailable result with resumable `run_id`.

`[inferred]` The chain must be policy-specific. A low-risk FAQ can serve stale-while-revalidate; a regulated approval workflow should stop rather than use stale eligibility rules.

## 4. Enterprise Security & Governance

### Context is untrusted data

- Retrieved pages, uploaded documents, tool results, prior assistant text, memory, and summaries can all carry instructions. OWASP states that RAG and fine-tuning do not fully mitigate prompt injection. Keep instructions in higher-priority roles, label context by source/trust, quote untrusted evidence as data, and ensure deterministic policy code mediates every external effect. [[30]](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- `[inferred]` The context compiler is a security enforcement point: authenticate first; apply row/document ACLs before retrieval; strip executable markup where appropriate; attach provenance; separate `trusted_instruction` from `untrusted_evidence`; and never promote a generated summary into a developer/system role merely because it is shorter.
- `[inferred]` Compression must preserve trust labels and source IDs. A malicious document can ask the summarizer to omit warnings or rewrite itself as policy; evaluate compression with indirect-injection fixtures and produce structured summaries whose evidence fields contain quoted source spans, not free-form authority claims.

### Tenant isolation and cache security

- A cache key must include tenant and authorization scope. vLLM's `cache_salt` isolates prefix reuse to a trust group and explicitly addresses timing-based inference of cached content. For hosted systems, verify the provider's isolation boundary rather than assuming organization, workspace, or project semantics. [[20]](https://docs.vllm.ai/en/stable/design/prefix_caching/)
- Anthropic documents that prompt-cache KV representations and cryptographic hashes are memory-only, ZDR eligible, and currently isolated by workspace on its first-party API/Claude Platform on AWS/Microsoft Foundry, while Bedrock and Vertex retain organization-level isolation. [[16]](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Gemini documents project-level isolation for implicit in-memory caching with a 24-hour TTL; explicit cached context lives for the user-defined TTL and therefore is incompatible with an absolute zero-data footprint. [[31]](https://ai.google.dev/gemini-api/docs/zdr)
- OpenAI documents endpoint-specific application-state retention and ZDR eligibility; `store=false` and stateless compaction can support ZDR-compatible flows, but eligibility depends on approved account controls and feature choice. Do not equate a short cache TTL with deletion of response/application state. [[32]](https://developers.openai.com/api/docs/guides/your-data)
- `[inferred]` Encrypt canonical history and summaries at rest with tenant-scoped keys; use TLS in transit; keep raw prompt text out of general logs; and rotate cache namespaces when access membership changes. A cache hit must never bypass the underlying document ACL.

### PII and secrets

- Redact or tokenize sensitive fields before they enter prompts, embeddings, application caches, and telemetry when the task does not require them. Amazon Bedrock Guardrails can block or mask built-in and regex-defined PII in text, but its documentation warns that the filter is probabilistic and does not detect PII inside `tool_use` output parameters. Deterministic schema-aware validation is still required for tool arguments and structured payloads. [[33]](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)
- `[inferred]` Store redaction maps in a separate, access-controlled vault when reversible substitution is necessary. Include detector/version, entity type, source field, action, and policy decision in the audit event. Never place long-lived credentials in a system prompt or cached tool schema; inject short-lived credentials only at the executor boundary.

### RBAC, sandboxing, and auditability

- `[inferred]` Authorization context should contain only the minimum model-visible affordance list. The actual executor re-authenticates, authorizes `(principal, action, resource, conditions)`, validates arguments, enforces rate and spend limits, and requires approval for sensitive mutations. A summary saying "approval granted" is not evidence; use a signed approval event ID.
- `[inferred]` Run untrusted document parsers and custom compression code in an isolated container/process with CPU, memory, time, filesystem, and egress limits. WASM is attractive for deterministic lightweight transforms; containers better fit native parsers/models but have a larger image and patching surface. Provider compaction reduces local execution exposure but increases provider coupling and opacity.
- Audit logs should be append-only and include context manifest/checkpoint IDs, prompt/model/policy versions, source IDs, trust labels, token counts, cache hit/write, redaction actions, compression strategy, evaluator result, and final output digest. Avoid logging raw prompts by default: OpenTelemetry's GenAI conventions explicitly warn that input messages, retrieval queries, and tool arguments can contain sensitive data. [[34]](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)

## 5. Production Failure Modes

| Failure | Symptoms and detection | Mitigation |
|---|---|---|
| Context rot / lost middle | accuracy falls as irrelevant tokens grow; position-sweep eval fails | retrieve/rank, remove dead context, repeat critical constraints at tested positions, evaluate at production lengths [[5]](https://arxiv.org/abs/2307.03172) |
| Hard context overflow | provider 400, truncation, incomplete output, no output budget | preflight target-model token count, reserve output/reasoning, compact before threshold [[6]](https://platform.claude.com/docs/en/build-with-claude/context-windows) |
| Summary drift | goals, negations, IDs, approvals, or unresolved work disappear across generations | immutable raw log, structured summary schema, exact source pointers, diff/retention eval, periodic rebase from raw events |
| Contradictory/stale context | model alternates between old and new policy or tool state | version/freshness metadata, deterministic supersession, dedupe, latest-authoritative-state section |
| Indirect prompt injection | retrieved/tool text changes behavior or requests secrets/actions | trust labels, role separation, injection evals, tool allowlists, deterministic authorization [[30]](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) |
| Prefix-cache fragmentation | low hit rate after prompt/tool ordering changes; repeated cache writes | canonical serialization, stable-prefix-first layout, versioned breakpoints, hit metrics by prompt version [[15]](https://developers.openai.com/api/docs/guides/prompt-caching) |
| Cache poisoning / false semantic hit | plausible answer belongs to another tenant, policy, or stale corpus | tenant/scope/version keys, source freshness check, exact-cache default, conservative semantic threshold and offline false-hit eval |
| Cache stampede / eviction churn | latency and input TPM spike on expiry or deployment | singleflight fills, warm new version gradually, TTL jitter for app cache, locality-aware routing, bounded fill concurrency |
| Compression-cache conflict | context editing repeatedly invalidates expensive prefixes | clear in larger batches, place breakpoints around stable segments, compare tokens cleared with write cost [[14]](https://platform.claude.com/docs/en/build-with-claude/context-editing) |
| Cascading timeouts | compile/retrieval/model retries consume deadline and queue | one propagated deadline, retry budget, per-dependency breaker, bulkheads, bounded queues [[28]](https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker) |
| State drift after retry | summary covers an uncommitted event or duplicate side effect | append-only events, checkpoint CAS, idempotency key, fenced leases, reconcile external action receipts |
| Infinite context-growth loop | repeated retrieval/summarization with no task progress | max steps/tokens/cost/time, progress predicate, compaction count cap, terminal failure state |
| Hallucinated retained state | summary invents facts or says a tool succeeded | validate IDs against event log; retain exact receipts; never infer side-effect completion from prose |

Operational detection should segment all quality and NFR metrics by model snapshot, prompt version, compression strategy, context-length bucket, evidence position, cache state, tenant tier, and retrieval corpus version. A global average can conceal a new prompt version that has excellent cache hits but worse constraint retention.

> ⚠️ Limited public data available for this dimension. No authoritative public post-mortem was found that quantifies a major production outage caused specifically by context compaction or cross-tenant LLM prefix-cache leakage. The risks above follow documented mechanics, published long-context degradation, and established distributed-cache failure patterns; validate them with internal game days and red-team tests.

## 6. Enterprise System Design Scenarios

### Scenario A: high-volume product-policy assistant

**Workload**: many short user questions share a long, approved policy and tool schema; answers must cite current source sections.

`[inferred]` Architecture: versioned policy corpus -> ACL-aware hybrid retrieval/reranking -> context compiler -> stable system/tool prefix breakpoint -> top-k exact policy chunks + user query -> fast model -> citation/structured-output validator -> response. Use provider KV caching for the common system/tools, retrieval cache keyed by corpus snapshot, and an exact answer cache only for non-personalized FAQs. Do not put the whole rapidly changing corpus in the cached prefix if that causes frequent invalidation.

Capacity worksheet:

```text
arrival RPS                     = Q
uncached prefill tokens/sec     = Q * (dynamic_tokens + shared_prefix*(1-hit_rate))
output tokens/sec               = Q * mean_output_tokens
required concurrency (Little)   = Q * p95_service_time_seconds
retrieval QPS                   = Q * (1-retrieval_cache_hit_rate)
```

Load-test prefix hit/miss separately, because equal request RPS can impose very different prefill load.

### Scenario B: long-running coding or research agent

**Workload**: hours of tool calls, large files/pages, resumability after worker failure, exact record of edits and findings.

`[inferred]` Architecture: durable workflow -> append-only trajectory/object store -> source index -> context compiler -> model/tool loop. Keep task goal, plan, invariants, changed-file list, tests, and unresolved failures in structured working state. Retain tool outputs in object storage by digest; inject only recent/relevant excerpts. Compact at a tested threshold into a versioned checkpoint, quality-check it, then activate with CAS. Replay uses recorded model/tool results; a new model attempt is explicitly a new event.

Operational guards: maximum calls/tokens/cost/time, per-tool breaker, idempotent edit/test actions, checkpoint before side effects, rehydrate-on-demand source pointers, and an eval comparing completion from full history versus compacted history on representative long-horizon tasks.

### Scenario C: regulated multi-tenant support copilot

**Workload**: customer and case PII, tenant-specific knowledge, human-approved mutations, retention/deletion obligations.

`[inferred]` Architecture: identity gateway -> tenant/purpose policy -> field-aware PII tokenization -> ACL-filtered retrieval -> tenant-isolated context and cache namespace -> approved-region model -> structured recommendation -> human approval -> separately authenticated executor. Store context manifests and signed approval IDs; store raw text only under the case retention policy. A deletion request invalidates application caches/index entries and expires or cryptographically erases governed source state; provider cache/retention behavior must be covered by the vendor control assessment.

Fail closed on identity, ACL, redaction, or policy-service failure. A cache hit re-runs policy and freshness checks. Use exact cited evidence for decisions; summaries can support continuity but cannot be the sole record of eligibility or consent.

### Trade-off matrix

| Approach | Cost/latency | Quality | Operational complexity | Security/governance | Best use |
|---|---|---|---|---|---|
| Full raw context | highest repeated prefill; simplest | preserves all evidence but can suffer context rot | low initially | largest exposure/retention surface | short bounded tasks |
| Sliding recent window | low and predictable | loses early constraints/state | low | smaller active exposure | casual chat with weak long-term dependency |
| RAG/retrieval | processes only selected evidence | depends on retrieval recall/ranking | medium-high | strong ACL/provenance possible | large changing corpora |
| Extractive compression | moderate reduction | exact retained wording; omission risk | medium | provenance easier | evidence-heavy QA |
| Abstractive/provider compaction | large continuity gain | lossy; must be evaluated | medium | summary may preserve sensitive/injected content | long-running workflows |
| Prompt/KV caching | large repeat-prefill savings | no semantic change on a hit | low-medium; locality/versioning | isolation and timing concerns | stable shared prefixes |
| Semantic answer caching | greatest latency/cost saving on hit | false-match/staleness risk | high invalidation/eval burden | highest cross-scope leakage risk | low-risk repetitive queries |

### Principal-architect decision rules

1. Start with prompt/version discipline and token observability; a larger window is capacity, not a quality strategy.
2. Remove deterministic waste before adding a lossy summarizer.
3. Use retrieval for large, changing, permissioned knowledge; use compaction for workflow continuity; use KV caching for repeated exact prefixes. They solve different problems.
4. Keep canonical state outside the prompt and preserve exact evidence/receipts for high-risk decisions.
5. Gate every prompt, model, renderer, and compression change on task evals across length and evidence position, then canary by version.
6. Calculate cache economics from observed reuse intervals and misses, and calculate capacity from uncached prefill plus output, not request count alone.

## Sources

- [1] https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents - Anthropic engineering definition and principles for context engineering.
- [2] https://developers.openai.com/api/docs/guides/prompting - Official OpenAI prompting guidance and prompt lifecycle.
- [3] https://developers.openai.com/api/docs/guides/prompt-engineering - OpenAI role hierarchy, prompt structure, examples, evals, and current prompt-storage guidance.
- [4] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices - Claude prompting, examples, structure, and long-context ordering guidance.
- [5] https://arxiv.org/abs/2307.03172 - Lost in the Middle position-sensitivity study.
- [6] https://platform.claude.com/docs/en/build-with-claude/context-windows - Context accounting, overflow behavior, and context-management guidance.
- [7] https://developers.openai.com/api/docs/guides/conversation-state - OpenAI state chaining, billing, and retention mechanics.
- [8] https://platform.claude.com/docs/en/build-with-claude/token-counting - Claude token-count endpoint and limitations.
- [9] https://arxiv.org/abs/2310.06201 - Selective Context prompt-pruning paper.
- [10] https://arxiv.org/abs/2310.05736 - LLMLingua prompt-compression paper.
- [11] https://arxiv.org/abs/2310.06839 - LongLLMLingua long-context compression paper.
- [12] https://developers.openai.com/api/docs/guides/compaction - OpenAI stateful/stateless compaction mechanics.
- [13] https://platform.claude.com/docs/en/build-with-claude/compaction - Claude server-side compaction mechanics.
- [14] https://platform.claude.com/docs/en/build-with-claude/context-editing - Claude tool/thinking clearing and cache interaction.
- [15] https://developers.openai.com/api/docs/guides/prompt-caching - OpenAI exact-prefix caching, breakpoints, TTL, and routing.
- [16] https://platform.claude.com/docs/en/build-with-claude/prompt-caching - Claude caching mechanics, retention, and isolation.
- [17] https://platform.claude.com/docs/en/about-claude/pricing - Claude cache pricing multipliers and break-even guidance.
- [18] https://ai.google.dev/gemini-api/docs/caching - Gemini implicit context-caching behavior.
- [19] https://ai.google.dev/gemini-api/docs/generate-content/caching - Gemini explicit cache objects, TTL/storage, and token-limit behavior.
- [20] https://docs.vllm.ai/en/stable/design/prefix_caching/ - vLLM automatic prefix caching and cache-salt isolation.
- [21] https://ai.google.dev/gemini-api/docs/long-context - Gemini long-context latency and caching guidance.
- [22] https://developers.openai.com/api/docs/pricing - Current OpenAI token and cache prices.
- [23] https://arxiv.org/abs/2308.14508 - LongBench long-context benchmark.
- [24] https://arxiv.org/abs/2404.06654 - RULER long-context benchmark.
- [25] https://platform.claude.com/docs/en/api/rate-limits - Claude RPM/ITPM/OTPM and cache-read rate-limit treatment.
- [26] https://arxiv.org/abs/2407.00023 - Preble distributed prefix-aware scheduling paper.
- [27] https://docs.temporal.io/ - Temporal durable execution and recovery documentation.
- [28] https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker - Circuit-breaker states and recovery behavior.
- [29] https://learn.microsoft.com/en-us/azure/architecture/best-practices/transient-faults - Retry budgets and backoff guidance.
- [30] https://genai.owasp.org/llmrisk/llm01-prompt-injection/ - OWASP prompt-injection risk and RAG limitations.
- [31] https://ai.google.dev/gemini-api/docs/zdr - Gemini cache isolation, retention, and ZDR implications.
- [32] https://developers.openai.com/api/docs/guides/your-data - OpenAI data controls, endpoint retention, and ZDR eligibility.
- [33] https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html - Bedrock PII filters and tool-use limitation.
- [34] https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/ - GenAI tracing attributes and sensitive-content warnings.
