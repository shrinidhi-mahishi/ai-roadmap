# 02 — Context Engineering

**Scope:** Prompting, context management, compression, and caching.  
**Study goal:** Build the smallest high-signal, policy-safe model input that preserves task state and can be reproduced, measured, cached, compacted, and recovered.

Context engineering is not “write a better prompt.” It is the runtime compilation of trusted instructions and selected data into a bounded model input. The prompt is a compiled artifact; canonical events, documents, policy decisions, tool definitions, and approvals remain the source of truth.

## 1. System Topology & Data Flow

### Reference topology

```text
                                      CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Prompt/schema registry │ model/tokenizer policy │ compression + cache policy│
│ tenant quotas/retention│ eval gates + canaries   │ rollout/rollback + RBAC   │
└───────────────┬────────────────────────┬──────────────────────────┬──────────┘
                │ pinned versions        │ budgets/quality gates    │ policy
                ▼                        ▼                          ▼
                                       DATA PLANE
┌───────────┐   ┌──────────────┐   ┌──────────────────┐   ┌───────────────────┐
│ API/WAF   ├──►│ Auth + trust ├──►│ Source collector ├──►│ Context compiler  │
│ identity  │   │ labels/ACLs  │   │ events/docs/tools│   │ dedupe/rank/budget│
└───────────┘   └──────┬───────┘   └────────┬─────────┘   │ order/render/hash │
                       │                    │             └──────┬────────────┘
                       │                    │                    │ manifest + input
                       │                    │             ┌──────▼────────────┐
                       │                    │             │ Cache coordinator │
                       │                    │             │ artifact/retrieval│
                       │                    │             │ exact/prefix      │
                       │                    │             └──────┬────────────┘
                       │                    │                    │ stable prefix first
                       │                    │             ┌──────▼────────────┐
                       │                    │             │ Model gateway     │
                       │                    │             │ prefill + decode  │
                       │                    │             └──────┬────────────┘
                       │                    │                    │ validated output
                       │                    │             ┌──────▼────────────┐
                       │                    └─────────────┤ Context lifecycle │
                       │                                  │ append/compact/CAS│
                       │                                  └──────┬────────────┘
                       │                                         │
                 TOOL PROXIES                                    │
┌──────────────────────▼─────────────────┐                        │
│ MCP/API proxy │ per-call RBAC │ PII gate│◄──── tool affordance/result ─────┘
│ approvals     │ sandbox       │ egress  │
└──────────────────────┬─────────────────┘
                       │
                 PERSISTENCE LAYER
┌──────────────────────▼───────────────────────────────────────────────────────┐
│ Append-only event log │ source/object store │ manifests │ compact checkpoints│
│ ACL/corpus versions   │ cache metadata      │ idempotency │ signed approvals  │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ events, spans, counters, digests
                                ▼
                       TELEMETRY / OBSERVABILITY
┌──────────────────────────────────────────────────────────────────────────────┐
│ OTel traces │ structured logs │ token/cache/compression metrics │ WORM/SIEM  │
└──────────────────────────────────────────────────────────────────────────────┘
```

The control plane versions prompts, renderers, compression strategies, tokenizer/model pairs, quotas, retention, and eval-based promotion. The data plane compiles and executes one call under pinned versions. Identity, authorization, retention, approval, and spend policy are control decisions, never instructions delegated to model-visible text.

### Request flow

1. The edge authenticates the principal and creates `run_id`, `trace_id`, an absolute deadline, and an idempotency key. Admission reserves estimated uncached-prefill and output tokens.
2. Policy resolves the tenant, purpose, authorization-scope digest, residency, allowed tools, and data classifications. Document and row ACLs apply **before** retrieval or cache access.
3. The collector loads canonical conversation events, structured workflow state, authorized tool schemas, and retrieved evidence with source IDs and versions. Each block carries a trust label; retrieved pages, tool output, prior model text, and summaries remain untrusted data.
4. The compiler removes exact duplicates and superseded state, ranks optional blocks, assigns a budget, and renders role-separated sections. Stable developer instructions, examples, and tool schemas precede volatile evidence and the current request.
5. A preflight counter for the target model verifies:

   ```text
   compiled_input ≤ model_window - visible_output_reserve
                                  - reasoning_reserve - safety_margin
   ```

6. Before inference, the service persists an immutable context manifest containing source/version IDs, trust labels, token counts, cache identity, compression checkpoint, and rendered digest. Raw text follows its own retention policy.
7. The cache coordinator checks only scope-compatible caches. An artifact hit reuses compiled bytes; a retrieval hit still re-checks ACL/freshness; an exact response hit still re-checks policy; a provider prefix hit reuses prefill computation but still consumes context-window capacity.
8. The model gateway sends the compiled input and processes a typed result. Tool requests pass through the zero-trust proxy; no prompt or summary is authorization evidence.
9. New messages and tool receipts are appended to the canonical trajectory. When a tested threshold is reached, a compactor creates and quality-checks a checkpoint from a closed event range, then activates it with compare-and-swap. Raw events are retained or deleted only by governed lifecycle policy.
10. Telemetry separates retrieval, compile, token-count, cache lookup, uncached prefill, decode, compaction, tool, queue, and retry time. Quality is segmented by model, prompt, renderer, compression, length, evidence position, and cache state.

## 2. Core Mechanics & Algorithms

### 2.1 Prompting is a precedence and data-layout contract

Prompt roles are not visual formatting. The API gives higher-priority developer/system instructions precedence over user content. Put stable behavior, output constraints, and security boundaries in the highest supported instruction role; put request-specific details in the user role. Delimit instructions, examples, evidence, and input with explicit sections so data is less likely to be interpreted as authority ([OpenAI prompting](https://developers.openai.com/api/docs/guides/prompt-engineering)).

A useful compiled layout is:

```text
┌──────────────────────────────────┐
│ Developer: role, invariants      │  stable, trusted, versioned
├──────────────────────────────────┤
│ Tool schemas / output contract   │  stable, authorized subset
├──────────────────────────────────┤
│ Few-shot decision boundaries     │  stable until eval-approved change
├──────────────────────────────────┤
│ Structured current state         │  latest authoritative projection
├──────────────────────────────────┤
│ Untrusted evidence + provenance  │  dynamic, quoted as data
├──────────────────────────────────┤
│ User request                     │  dynamic, normally last
└──────────────────────────────────┘
```

Few-shot examples perform in-context conditioning without changing weights. Examples should be diverse, relevant, regression-tested instances of real decision boundaries and should show the exact output contract. Additional examples impose an opportunity cost: they may displace evidence or output headroom. Start with a small evaluated set rather than filling available capacity.

Ordering is workload- and model-dependent. Long-context guidance for some models favors large documents early and the query late, while *Lost in the Middle* observed weaker use of evidence in middle positions across tested models ([position study](https://arxiv.org/abs/2307.03172)). Resolve this empirically with position-sweep evals at production lengths. Pin model snapshots: a prompt is code whose behavior depends on the model/runtime version.

**Prompt invariants**

- Untrusted text is never promoted into a higher-priority role.
- The same pinned source versions and renderer version produce byte-identical output.
- The security policy and output contract are never removed by a latency/cost degradation path.
- A prompt change is deployed as a new version through offline eval, canary, and rollback; it is not silently edited in place.

### 2.2 Context selection and active-window management

The active budget is:

```text
B_active = W_model - O_reserved - R_reserved - S_margin
B_optional = B_active - tokens(system + required_tools + current_request + state)
```

Caching does not change this inequality. Prior messages referenced through a provider conversation ID may still be billed and occupy context; state transport is not free storage.

Treat optional selection as a budgeted utility problem. Candidate block `i` has token cost `c_i`, relevance/utility `u_i`, source/trust metadata, and dependency set `D_i`:

```text
maximize Σ u_i x_i
subject to Σ c_i x_i ≤ B_optional, x_i ∈ {0,1}
           x_i = 1 ⇒ all required provenance/dependencies D_i are retained
```

Exact 0/1 knapsack is `O(nB)` pseudo-polynomial and impractical when `B` is large. A production compiler normally:

1. removes exact duplicates and deterministically superseded state in `O(n)` expected time with hashes/IDs;
2. preserves mandatory goals, constraints, receipts, and current authoritative state;
3. scores/reranks optional blocks, commonly `O(n log n)` for sorting;
4. greedily selects by risk-aware utility density while enforcing document diversity and dependencies;
5. token-counts the rendered result with the target tokenizer and trims the lowest-value optional block until within budget.

Greedy selection is not globally optimal, but it is deterministic, fast, and auditable. Maximum marginal relevance can discourage redundant evidence:

```text
MMR(d) = λ·similarity(d, query) - (1-λ)·max similarity(d, selected)
```

Naively recomputing MMR is `O(nk)` similarity comparisons for `k` selected blocks. Approximate indexes reduce candidate generation cost, but ACL filtering must occur before selection.

**Context manifest**

For every call, freeze:

```text
{run_id, tenant_id, auth_scope_digest, model_snapshot, tokenizer,
 prompt_version, renderer_version, tool/schema versions, source event IDs,
 document IDs+versions, corpus snapshot, checkpoint ID, trust labels,
 token counts by section, cache key, rendered digest}
```

This manifest lets an auditor reproduce why a block was present without retaining raw sensitive content indefinitely.

### 2.3 Compression is a controlled lossy state transition

Compression strategies have different semantics:

| Strategy | Operation | Appropriate use | Recovery requirement |
|---|---|---|---|
| Deterministic pruning | Remove duplicates, expired retrieval, and superseded state | First step for every workload | Versioned rule and removed IDs |
| Extractive | Select original spans/chunks | Evidence/citation-heavy tasks | Exact source pointer and span |
| Abstractive summary | Generate shorter structured state | Closed phases and long conversations | Raw event range plus quality result |
| Token-level compression | Rank/drop tokens using a smaller model or information score | Large low-risk documents after eval | Original document and compressor version |
| Provider compaction | Continue from provider-produced checkpoint/block | Long model/tool loops | Preserve opaque provider semantics |
| Context editing | Clear old tool/thinking blocks | Tool-heavy histories | Object-store digest and rehydrate path |

Research systems report substantial reductions on evaluated tasks, but compression ratios are not portable guarantees ([Selective Context](https://arxiv.org/abs/2310.06201), [LLMLingua](https://arxiv.org/abs/2310.05736), [LongLLMLingua](https://arxiv.org/abs/2310.06839)). Gate a strategy on task success, evidence recall, citations, negation/constraint retention, identifiers, decisions, state, and indirect-injection fixtures across length and evidence position.

```text
┌──────────────┐ close range ┌──────────────┐ summarize ┌──────────────┐
│ RAW_ACTIVE   ├────────────►│ COMPACTING   ├──────────►│ CANDIDATE    │
└──────────────┘             └──────┬───────┘           └──────┬───────┘
                                    │ fail                       │ validate
                                    ▼                            ▼
                             ┌──────────────┐ reject       ┌──────────────┐
                             │ RAW_ACTIVE   │◄─────────────┤ QUALITY_GATE │
                             └──────────────┘               └──────┬───────┘
                                                                  │ pass + CAS
                                                                  ▼
                                                           ┌──────────────┐
                                                           │ CHECKPOINTED │
                                                           └──────┬───────┘
                                                                  │ rehydrate
                                                                  ▼
                                                           ┌──────────────┐
                                                           │ EXACT_SOURCE │
                                                           └──────────────┘
```

A checkpoint is a materialized view over a closed event range, never the only record. Preserve goals, unresolved work, constraints/negations, decisions, identifiers, committed side effects, exact receipt IDs, and source pointers. An approval described in summary prose is not an approval; only a signed approval event is authoritative.

**Compression invariants**

- A checkpoint names `first_event`, `last_event`, `strategy_version`, `source_digest`, retained pointers, token count, and evaluator result.
- Only a quality-passed artifact can become active, and activation uses checkpoint-version CAS.
- The summary cannot cover an event that was not durably committed.
- Workflow replay reuses the recorded compacted artifact; it does not call a nondeterministic summarizer again.
- Periodic rebasing from raw events prevents multi-generation summary drift.

### 2.4 Cache taxonomy and algorithms

Do not conflate computation reuse with answer reuse:

| Cache | Exact key includes | Reused object | Main risk |
|---|---|---|---|
| Provider/self-hosted prefix KV | exact prefix, model/runtime, routing/salt | prefill/KV computation | fragmentation, locality, timing/isolation |
| Compiled artifact | prompt/renderer/tool/schema + stable source versions | rendered bytes/token count | stale rollout version |
| Retrieval | normalized query, corpus snapshot, filters, auth scope | document IDs/chunks | ACL or corpus staleness |
| Deterministic response | exact normalized request and all versions | validated final answer | personalization/invalidation |
| Semantic response | embedding neighborhood plus policy/version scope | approximate prior answer | false hit, poisoning, cross-tenant leak |

Provider prompt caching requires exact eligible prefixes. Stable instructions, examples, tools, and schemas go first; volatile evidence and the query go after the cache boundary. Prefix caches reduce repeated prefill cost/latency but do not reduce context-window tokens. Self-hosted serving should salt the first block hash by trust group and schedule for both prefix locality and load; round-robin can destroy reuse ([vLLM cache salt](https://docs.vllm.ai/en/stable/design/prefix_caching/)).

Use versioned keys rather than deletion on the request path:

```text
K = SHA-256(tenant_id || auth_scope_digest || model || tokenizer ||
            prompt || renderer || schema || tools || corpus ||
            compression_strategy || normalized_request)
```

On membership or ACL change, rotate the authorization-scope digest. On prompt rollout, create a new namespace and warm it gradually. Prevent stampedes with singleflight per exact key, bounded fill concurrency, and jittered TTL for application caches. Negative-cache only deterministic misses.

Semantic answer caches demand the strongest policy: require identical tenant, principal/access scope, locale, model/prompt/output contract, corpus/source versions, and freshness class; re-authorize on every hit; verify cited sources still exist; and measure false-hit harm offline. Prefer an exact cache for consequential or personalized work.

## 3. Token Economics & NFR Analysis

### 3.1 Explicit cost per 1,000 runs

For 1,000 executions:

```text
C_1000 = [U·P_input + H·P_cache_read + W·P_cache_write
          + (O_visible + O_reasoning)·P_output] / 1,000,000
          + C_retrieval + C_compression + C_cache_storage + C_tools
```

`U`, `H`, `W`, and `O` are aggregate tokens over all calls. Include summarizer/compaction output, embeddings/reranking, retries and cache misses; otherwise an optimization merely moves cost off the model line.

**Assumptions as of 2026-08-21:** 1,000 calls share an exact 8,000-token prefix, each adds 1,500 uncached tokens and returns 600 billed output tokens; no additional reasoning, retries, retrieval, or storage cost. One prefix write is followed by 999 eligible hits. Prices per one million tokens are point-in-time [OpenAI standard prices](https://developers.openai.com/api/docs/pricing).

```text
W = 8,000
H = 8,000 × 999 = 7,992,000
U = 1,500 × 1,000 = 1,500,000
O = 600 × 1,000 = 600,000
uncached input = (8,000 + 1,500) × 1,000 = 9,500,000
```

| Tier | Input / cache read / write / output per 1M | No cache / 1K | One write + 999 hits / 1K | Saving |
|---|---|---:|---:|---:|
| `gpt-5.6-sol` | $5 / $0.50 / $6.25 / $30 | `(9.5M×$5)+(0.6M×$30)` = **$65.50** | `$0.05+$3.996+$7.50+$18` = **$29.55** | 54.9% |
| `gpt-5.6-terra` | $2 / $0.20 / $2.50 / $12 | `(9.5M×$2)+(0.6M×$12)` = **$26.20** | `$0.02+$1.5984+$3+$7.20` = **$11.82** | 54.9% |
| `gpt-5.6-luna` | $0.20 / $0.02 / $0.25 / $1.20 | `(9.5M×$0.20)+(0.6M×$1.20)` = **$2.62** | `$0.002+$0.15984+$0.30+$0.72` = **$1.18** | 54.9% |

At write `1.25×` and read `0.1×` base input, an eligible five-minute-style cache costs `1.25 + 0.1(N-1)` base units across `N` uses instead of `N`; it becomes cheaper on the second total use. An Anthropic one-hour write at `2×` becomes cheaper after two reads, on the third total use. Realized savings fall with misses, sparse reuse, prompt churn, cache routing, and TTL expiry.

**Compression example.** Suppose each call uses a `luna` summarizer to compress 4,000 tokens to 1,000 before a `terra` call. Across 1,000 calls, summarization costs `(4M×$0.20 + 0.5M×$1.20) = $1.40` and saves `3M×$2 = $6.00` of `terra` input, a gross token-line saving of **$4.60/1K runs** before latency, cache interaction, retrieval, and expected quality-loss cost. The economic gate is:

```text
C_compress + C_shorter_calls + expected_quality_loss_cost < C_full_calls
```

A one-shot request or a harmful omission can invalidate the apparent saving. Compression is most attractive when a checkpoint will be reused or prevents a hard overflow.

### 3.2 Latency objectives

No hosted provider publishes a portable percentile SLA segmented by model, region, context length, cache state, and compaction. Treat these as application targets to validate on production-shaped load:

```text
T_total = T_queue + T_auth/retrieval + T_compile + T_token_count
          + T_cache_lookup + T_prefill(uncached tokens)
          + T_decode(output + reasoning) + T_compaction + T_tools + T_retries
```

| Path | p50 target | p95 target | p99 target | Tail mitigation |
|---|---:|---:|---:|---|
| Cached-prefix knowledge answer | ≤ 0.9 s | ≤ 2.5 s | ≤ 5 s | Locality-aware routing, precompiled artifacts, bounded top-k, region failover once. |
| Context compile only | ≤ 40 ms | ≤ 120 ms | ≤ 300 ms | Cache token counts/ranking features; cap candidates; bypass optional artifact cache on failure. |
| Compaction activity | ≤ 2 s | ≤ 8 s | ≤ 20 s | Run off the interactive path; bound concurrency; keep old checkpoint active on timeout. |
| Long-running turn with one tool | ≤ 2.5 s | ≤ 8 s | ≤ 15 s | Deadline propagation, per-dependency breaker, checkpoint/queue continuation. |

Track total and TTFT p50/p95/p99 plus retrieval latency, input tokens before/after compression, uncached/cached/write tokens, hit rate by prompt version, compaction frequency, summary-retention score, overflow rate, and queue time. Longer input generally raises TTFT; a cache hit changes prefill work, not decode or tool latency.

### 3.3 Throughput and back-pressure

Capacity against the limiting dimensions:

```text
uncached_prefill_TPM = RPS × 60 × [dynamic_tokens + prefix_tokens×(1-hit_rate)]
output_TPM           = RPS × 60 × p95(output + reasoning tokens)
max_inflight         = RPS × p99_service_seconds
retrieval_QPS        = RPS × (1-retrieval_cache_hit_rate)
queue_drain_seconds  = queued_uncached_tokens / sustainable_prefill_tokens_per_second
```

At `200 RPS`, an 8,000-token prefix with `90%` hits, 1,500 dynamic tokens, 600 output tokens, `4 s` p99, and `60%` retrieval-cache hits requires `27.6M uncached-prefill TPM`, `7.2M output TPM`, `800` in-flight capacity, and `80 retrieval QPS`, before failover reserve. Equal RPS with zero prefix hits needs `114M` input TPM, over 4× as much prefill capacity.

Back-pressure must charge estimated **uncached prefill** and maximum output, not request count alone:

- Use per-tenant token buckets plus global model/region budgets; reserve stable capacity for policy, status, and approval paths.
- Bound interactive, batch, cache-fill, and compaction queues independently. Reject or queue before saturation rather than accepting work with impossible deadlines.
- Coalesce identical cache fills, cap fill/compaction concurrency, and add TTL jitter to application caches.
- Degrade through exact scoped response, current valid checkpoint, smaller pretested retrieval top-k, compatible approved model/region, async queue, then typed unavailability.
- Never degrade by dropping system/security instructions, ACL filtering, signed approval checks, or output headroom.
- Prefix-aware routing must balance locality against hot-worker load; a cache hit on an overloaded worker can be slower than an uncached request elsewhere.

### 3.4 Non-functional requirements and trade-offs

| Requirement | Target | Architectural consequence / trade-off |
|---|---|---|
| Availability | 99.9% answer path; 99.99% run/status API | Optional caches fail open to recomputation; identity, policy, and ACL services fail closed. |
| Durability | 100% acknowledged turns have canonical event and manifest | Commit-before-inference/ack adds I/O but enables replay and audit. |
| RPO | 0 for canonical events, approvals, side effects, active checkpoint pointer; ≤ 5 min for metrics | Synchronous replicated state for authority; async metrics are cheaper. |
| RTO | ≤ 15 min context/workflow service; ≤ 60 min analytics | Warm standby and restored cache namespaces cost more; caches are rebuildable. |
| Quality | Overflow < 0.01%; required-constraint retention 100% in release eval; task thresholds by risk tier | Conservative reserves reduce usable window; lossy compression needs a gate. |
| Privacy | No cross-tenant hit; raw prompt text absent from general logs; governed residency/TTL | Scope-rich keys reduce hit rate; tokenization can reduce model utility. |
| Audit | Every call maps to a manifest, versions, source IDs, cache status, and digest | Hashes reduce exposure but make human forensics depend on governed source access. |
| Compliance | Deletion/retention propagated to sources, indexes, app caches, and approved provider controls | Provider cache TTL is not proof that all response/application state was deleted. |

## 4. Distributed Resilience & Security

### 4.1 Durable context and replay

Persist conversation/tool/model activity as append-only events. A compacted checkpoint is derived from a closed range:

```text
{checkpoint_id, first_event, last_event, strategy_version, source_digest,
 structured_summary, retained_source_refs, token_count, evaluator_result}
```

Temporal can durably orchestrate collection, compaction, quality evaluation, approval waits, and inference activities. Its replay consumes recorded activity results; it must not call the model or retriever again. Kafka can fan out ordered run events when partitioned by `run_id`; consumer offsets permit replay, but at-least-once delivery still requires idempotent sinks and a unique event/checkpoint key.

Checkpoint lossy boundaries: raw range committed, manifest frozen, compaction requested, candidate received, quality-passed, pointer activated. CAS activation means a losing compactor leaves an unreferenced artifact instead of replacing newer state. Use a short lease and monotonically increasing fencing token for compaction ownership. Never hold a distributed/database lock across retrieval, inference, tools, or human approval.

Provider adapters must preserve provider-specific opaque compaction objects. An OpenAI stateless compacted window becomes canonical next input; an Anthropic compaction block is appended according to its protocol. Converting either into casually editable prose breaks replay semantics ([OpenAI compaction](https://developers.openai.com/api/docs/guides/compaction), [Anthropic compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)).

### 4.2 Failure taxonomy

| Failure class | Examples | Detection and handling |
|---|---|---|
| Transient | 429/503, retrieval timeout, cache-node loss | One retry owner; bounded exponential backoff with jitter; honor deadline/`Retry-After`; breaker by dependency/region. |
| Permanent | invalid prompt/schema version, unsupported model/window, denied ACL | Do not retry unchanged data; fail closed or deploy a corrected version. |
| Poison context | parser crash, adversarial document, repeatedly failing compaction range | Count durable attempts; quarantine source/range to DLQ; preserve redacted digest; keep prior checkpoint active. |
| Summary drift | lost negation, ID, constraint, receipt, unresolved task | Structured retention validator, source diff, periodic raw rebase; reject candidate. |
| Cache poisoning/false hit | wrong tenant, stale corpus, semantically similar but incorrect answer | Scope/version key, re-authorization, source freshness and exact citations; invalidate namespace. |
| Cache stampede | prefill/DB spike at expiry or rollout | Singleflight, bounded fills, gradual warmup, jittered application TTL, locality-aware scheduling. |
| Ambiguous model attempt | timeout after provider accepted request | Record attempt; commit only one result with run-version CAS; do not claim deterministic replay. |
| Infinite growth/compaction | repeated retrieval and summaries with no progress | Maximum turns/tokens/cost/compactions, progress predicate, terminal escalation state. |

Use separate closed/open/half-open breakers for policy, retrieval, token counting, cache, compactor, and each model/region. Optional artifact/cache failure bypasses to recomputation. Policy or ACL failure fails closed. Compactor failure continues only from the prior valid checkpoint and only while the active-window reserve remains safe.

### 4.3 Zero-Trust MCP, tools, and prompt injection

All retrieved documents, web pages, uploads, tool results, memory, prior assistant text, and summaries are untrusted. RAG and fine-tuning do not eliminate indirect prompt injection ([OWASP prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)).

```text
┌──────────────┐ trust labels ┌──────────────┐ minimal tools ┌──────────────┐
│ Context      ├─────────────►│ Model host   ├──────────────►│ MCP/tool     │
│ compiler     │              │ no secrets   │ proposal      │ proxy        │
└──────┬───────┘              └──────────────┘               └──────┬───────┘
       │ ACL-filtered evidence                                  mTLS/OAuth
       ▼                                                           ▼
┌──────────────┐             policy decision                ┌──────────────┐
│ Source/index │◄────────────────────────────────────────────┤ MCP server / │
│ tenant scope │                                             │ executor     │
└──────────────┘                                             └──────┬───────┘
                                                                    ▼
                                                             ┌──────────────┐
                                                             │ Sandboxed API│
                                                             │ short token  │
                                                             └──────────────┘
```

- Authenticate the user/workload before retrieval; apply document/row ACLs and purpose limitation before a block reaches a cache or prompt.
- Separate `trusted_instruction` and `untrusted_evidence` structurally. Strip executable markup where appropriate, retain source/trust metadata, and never let a summarizer rewrite evidence as policy.
- Expose only the tenant-authorized tool schema subset. The proxy re-authenticates and authorizes `(principal, action, resource, conditions)` per call, applies least privilege, obtains signed approval, and mints a short-lived audience-bound credential.
- Authenticate MCP server identity, encrypt transport, allowlist capabilities and egress, sandbox custom parsers/compressors/tools, and give the model no ambient credentials.
- A context statement such as “approval granted” never authorizes execution; the executor requires a signed approval event ID and canonical parameters.

### 4.4 PII, cache isolation, and audit custody

PII control applies before prompts, embeddings, application caches, telemetry, summaries, and tool arguments:

```text
┌────────┐   ┌────────────────┐   ┌────────────────┐   ┌──────────────┐
│ Source ├──►│ regex + NER +  ├──►│ block/tokenize ├──►│ scoped use   │
│ field  │   │ schema detector│   │ /mask          │   │ prompt/cache │
└────────┘   └───────┬────────┘   └───────┬────────┘   └──────┬───────┘
                     │ detector/version    │ vault map          │
                     └─────────────────────┴─────────────►┌─────▼────────┐
                                                         │ immutable log│
                                                         └──────────────┘
```

Use tenant-scoped encryption keys at rest and TLS in transit. Include tenant and authorization-scope digest in all application cache keys; use a self-hosted prefix `cache_salt` per trust group. Rotate namespaces when membership changes. A cache hit never bypasses ACL, freshness, purpose, or policy checks.

PII filters are probabilistic and may not inspect tool-use parameters, so validate structured arguments separately. Keep reversible redaction maps in a segregated vault. Never put long-lived secrets in prompts, cached schemas, summaries, or logs.

Append-only audit events link `trace_id`, `run_id`, actor, tenant, manifest/checkpoint IDs, prompt/model/tokenizer/renderer/policy/compression versions, source IDs and trust labels, token counts, cache read/write/hit, redaction actions, quality result, output digest, signed approval, and parent event. Hash-chain or sign batches, write to WORM storage, and log reads. OpenTelemetry GenAI fields provide trace vocabulary, but sensitive prompt/query/tool payload capture remains opt-in ([OTel GenAI conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)).

## 5. Production Enterprise Code

This Python 3.11 program uses only the standard library. It provides a deterministic context compiler, target-counter injection, scope/version-complete cache keys, TTL artifact cache, PII masking, immutable manifests, full-jitter retry, closed/open/half-open circuit breakers, an approved primary-to-secondary model chain, JSON logs with correlation IDs, and an extractive read-only fallback. Run it with `python context_gateway.py`.

```python
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Protocol, Sequence


class TransientError(RuntimeError):
    """A dependency failure that may succeed within a bounded retry budget."""


class ContractError(RuntimeError):
    """A permanent input, schema, policy, or model-contract failure."""


class CircuitOpen(TransientError):
    """The dependency is failing fast while its recovery window elapses."""


class TokenCounter(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class ConservativeCounter:
    """Safe upper bound for the demo; inject the target tokenizer in production."""

    name = "utf8-byte-upper-bound-v1"

    def count(self, text: str) -> int:
        return len(text.encode("utf-8"))


@dataclass(frozen=True)
class ContextBlock:
    source_id: str
    version: str
    kind: str
    text: str
    utility: float
    mandatory: bool = False
    trusted: bool = False


@dataclass(frozen=True)
class CompilePolicy:
    tenant_id: str
    auth_scope_digest: str
    model_snapshot: str
    prompt_version: str
    renderer_version: str
    schema_version: str
    tool_version: str
    corpus_version: str
    compression_version: str
    model_window: int
    output_reserve: int
    reasoning_reserve: int
    safety_margin: int


@dataclass(frozen=True)
class Manifest:
    manifest_id: str
    cache_key: str
    rendered_digest: str
    tokenizer: str
    selected_source_ids: tuple[str, ...]
    selected_source_refs: tuple[str, ...]
    dropped_source_ids: tuple[str, ...]
    input_tokens: int
    active_budget: int


@dataclass(frozen=True)
class CompiledContext:
    rendered: str
    manifest: Manifest


@dataclass(frozen=True)
class Answer:
    answer: str
    source_ids: tuple[str, ...]
    model: str
    degraded: bool

    @classmethod
    def parse(cls, raw: str, model: str) -> "Answer":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContractError("model returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != {"answer", "source_ids"}:
            raise ContractError("model response violates exact schema")
        answer, sources = value["answer"], value["source_ids"]
        if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 2_000:
            raise ContractError("answer violates length policy")
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(item, str) for item in sources)
        ):
            raise ContractError("source_ids must be a non-empty string list")
        return cls(answer.strip(), tuple(sources), model, False)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {
            "timestamp": time.time(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for name in ("correlation_id", "model", "attempt", "cache", "state"):
            if hasattr(record, name):
                value[name] = getattr(record, name)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("context_gateway")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class ArtifactCache:
    def __init__(self, ttl_s: float = 300.0):
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        self._ttl_s = ttl_s
        self._items: dict[str, tuple[float, CompiledContext]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> CompiledContext | None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires, value = item
            if expires <= now:
                del self._items[key]
                return None
            return value

    def put(self, key: str, value: CompiledContext) -> None:
        with self._lock:
            self._items[key] = (time.monotonic() + self._ttl_s, value)


PII_PATTERNS = (
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[PAYMENT_NUMBER]"),
)


def redact(text: str) -> str:
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def digest(*parts: str) -> str:
    value = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


class ContextCompiler:
    def __init__(self, counter: TokenCounter, cache: ArtifactCache):
        self._counter = counter
        self._cache = cache

    def _key(self, policy: CompilePolicy, blocks: Sequence[ContextBlock]) -> str:
        source_versions = json.dumps(
            sorted(
                (
                    b.source_id,
                    b.version,
                    b.kind,
                    b.mandatory,
                    b.trusted,
                    digest(b.text),
                )
                for b in blocks
            ),
            separators=(",", ":"),
        )
        return digest(
            policy.tenant_id,
            policy.auth_scope_digest,
            policy.model_snapshot,
            self._counter.name,
            policy.prompt_version,
            policy.renderer_version,
            policy.schema_version,
            policy.tool_version,
            policy.corpus_version,
            policy.compression_version,
            source_versions,
        )

    def compile(
        self, policy: CompilePolicy, blocks: Sequence[ContextBlock]
    ) -> tuple[CompiledContext, bool]:
        allowed_kinds = {"instruction", "tool", "example", "state", "evidence", "request"}
        if any(block.kind not in allowed_kinds for block in blocks):
            raise ContractError("context contains an unsupported block kind")
        if any(block.utility < 0 for block in blocks):
            raise ContractError("context utility scores cannot be negative")
        if any(block.kind in {"instruction", "tool"} and not block.trusted for block in blocks):
            raise ContractError("instruction and tool blocks must be trusted registry data")
        if not any(
            block.kind == "instruction" and block.mandatory and block.trusted
            for block in blocks
        ):
            raise ContractError("a mandatory trusted instruction block is required")
        if not any(block.kind == "request" and block.mandatory for block in blocks):
            raise ContractError("a mandatory request block is required")
        versions_by_source: dict[str, str] = {}
        for block in blocks:
            existing = versions_by_source.setdefault(block.source_id, block.version)
            if existing != block.version:
                raise ContractError("one source ID cannot have conflicting active versions")
        key = self._key(policy, blocks)
        cached = self._cache.get(key)
        if cached is not None:
            call_manifest = replace(cached.manifest, manifest_id=str(uuid.uuid4()))
            return CompiledContext(cached.rendered, call_manifest), True

        active = (
            policy.model_window
            - policy.output_reserve
            - policy.reasoning_reserve
            - policy.safety_margin
        )
        if active <= 0:
            raise ContractError("reserves leave no active context budget")

        # Exact duplicates collapse deterministically after version conflicts fail closed.
        unique: dict[tuple[str, str], ContextBlock] = {}
        for block in blocks:
            unique.setdefault((block.source_id, block.version), block)
        values = list(unique.values())
        mandatory = sorted(
            (b for b in values if b.mandatory), key=lambda b: (b.kind, b.source_id)
        )
        optional = sorted(
            (b for b in values if not b.mandatory),
            key=lambda b: (
                -(b.utility / max(1, self._counter.count(redact(b.text)))),
                b.source_id,
            ),
        )

        selected: list[ContextBlock] = []
        used = 0
        for block in mandatory:
            cost = self._counter.count(redact(block.text))
            if used + cost > active:
                raise ContractError("mandatory context exceeds active budget")
            selected.append(block)
            used += cost
        for block in optional:
            cost = self._counter.count(redact(block.text))
            if used + cost <= active:
                selected.append(block)
                used += cost

        # Renderer keeps trusted policy first and volatile request last.
        order = {"instruction": 0, "tool": 1, "example": 2, "state": 3,
                 "evidence": 4, "request": 5}
        selected.sort(key=lambda b: (order.get(b.kind, 4), b.source_id))
        sections = []
        for block in selected:
            trust = "TRUSTED_INSTRUCTION" if block.trusted else "UNTRUSTED_DATA"
            sections.append(
                f"## {block.kind.upper()} [{block.source_id}@{block.version}]\n"
                f"<{trust}>\n{redact(block.text)}\n</{trust}>"
            )
        rendered = "\n\n".join(sections)
        final_count = self._counter.count(rendered)
        if final_count > active:
            # Delimiters add cost; remove lowest-utility optional blocks and retry.
            removable = sorted(
                (b for b in selected if not b.mandatory),
                key=lambda b: (b.utility, b.source_id),
            )
            while final_count > active and removable:
                selected.remove(removable.pop(0))
                selected.sort(key=lambda b: (order.get(b.kind, 4), b.source_id))
                sections = [
                    f"## {b.kind.upper()} [{b.source_id}@{b.version}]\n"
                    f"<{'TRUSTED_INSTRUCTION' if b.trusted else 'UNTRUSTED_DATA'}>\n"
                    f"{redact(b.text)}\n"
                    f"</{'TRUSTED_INSTRUCTION' if b.trusted else 'UNTRUSTED_DATA'}>"
                    for b in selected
                ]
                rendered = "\n\n".join(sections)
                final_count = self._counter.count(rendered)
        if final_count > active:
            raise ContractError("rendered mandatory context exceeds active budget")

        selected_ids = tuple(b.source_id for b in selected)
        dropped_ids = tuple(
            sorted(b.source_id for b in values if b.source_id not in selected_ids)
        )
        manifest = Manifest(
            manifest_id=str(uuid.uuid4()),
            cache_key=key,
            rendered_digest=digest(rendered),
            tokenizer=self._counter.name,
            selected_source_ids=selected_ids,
            selected_source_refs=tuple(f"{b.source_id}@{b.version}" for b in selected),
            dropped_source_ids=dropped_ids,
            input_tokens=final_count,
            active_budget=active,
        )
        compiled = CompiledContext(rendered, manifest)
        self._cache.put(key, compiled)
        return compiled, False


class Model(Protocol):
    name: str

    def generate(self, context: str, timeout_s: float) -> str: ...


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, threshold: int = 3, recovery_s: float = 10.0):
        if threshold < 1 or recovery_s <= 0:
            raise ValueError("invalid circuit-breaker configuration")
        self._threshold = threshold
        self._recovery_s = recovery_s
        self._failures = 0
        self._opened_at = 0.0
        self._probe = False
        self._state = BreakerState.CLOSED
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if self._state is BreakerState.OPEN:
                if time.monotonic() - self._opened_at < self._recovery_s:
                    raise CircuitOpen("model circuit is open")
                self._state = BreakerState.HALF_OPEN
            if self._state is BreakerState.HALF_OPEN:
                if self._probe:
                    raise CircuitOpen("half-open probe already running")
                self._probe = True

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._probe = False
            self._state = BreakerState.CLOSED

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe = False
            if self._state is BreakerState.HALF_OPEN or self._failures >= self._threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state.value


def invoke(
    model: Model,
    breaker: CircuitBreaker,
    context: CompiledContext,
    deadline: float,
    correlation_id: str,
    max_attempts: int = 3,
) -> Answer:
    for attempt in range(1, max_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransientError("deadline exhausted")
        breaker.before()
        try:
            raw = model.generate(context.rendered, min(remaining, 5.0))
            # Transport recovered even if the response later fails our contract.
            breaker.success()
            answer = Answer.parse(raw, model.name)
            if not set(answer.source_ids).issubset(context.manifest.selected_source_ids):
                raise ContractError("answer cites a source absent from context")
        except ContractError:
            raise
        except (TimeoutError, ConnectionError, TransientError) as exc:
            breaker.failure()
            logger.warning(
                "transient model failure",
                extra={"correlation_id": correlation_id, "model": model.name,
                       "attempt": attempt, "state": breaker.state},
            )
            if attempt == max_attempts:
                raise TransientError("retry budget exhausted") from exc
            delay = random.uniform(0.0, 0.1 * (2 ** (attempt - 1)))
            if delay >= deadline - time.monotonic():
                raise TransientError("insufficient retry deadline") from exc
            time.sleep(delay)
        else:
            return answer
    raise AssertionError("bounded retry loop did not terminate")


class Gateway:
    def __init__(self, compiler: ContextCompiler, models: Sequence[Model]):
        if not models:
            raise ValueError("at least one approved model is required")
        self._compiler = compiler
        self._models = tuple(models)
        self._breakers = {model.name: CircuitBreaker() for model in models}

    def answer(
        self, policy: CompilePolicy, blocks: Sequence[ContextBlock], timeout_s: float
    ) -> tuple[Answer, Manifest]:
        correlation_id = str(uuid.uuid4())
        compiled, hit = self._compiler.compile(policy, blocks)
        logger.info(
            "context compiled",
            extra={"correlation_id": correlation_id,
                   "cache": "hit" if hit else "miss"},
        )
        deadline = time.monotonic() + timeout_s
        for model in self._models:
            try:
                answer = invoke(
                    model, self._breakers[model.name], compiled, deadline, correlation_id
                )
                return answer, compiled.manifest
            except (TransientError, CircuitOpen, ContractError) as exc:
                logger.error(
                    f"model rejected: {type(exc).__name__}",
                    extra={"correlation_id": correlation_id, "model": model.name,
                           "state": self._breakers[model.name].state},
                )

        # Read-only extractive degradation: no generated claim or side effect.
        sources = [
            b for b in blocks
            if b.kind == "evidence" and b.source_id in compiled.manifest.selected_source_ids
        ]
        excerpt = " ".join(redact(b.text) for b in sources)[:500]
        if not excerpt:
            raise TransientError("no safe degraded answer is available")
        return Answer(excerpt, tuple(b.source_id for b in sources),
                      "extractive-fallback-v1", True), compiled.manifest


class DemoModel:
    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def generate(self, context: str, timeout_s: float) -> str:
        if timeout_s <= 0:
            raise TimeoutError("deadline expired")
        if not self._available:
            raise TimeoutError("simulated regional outage")
        return json.dumps({
            "answer": "Refunds are available within 30 days.",
            "source_ids": ["policy-refunds"],
        })


def main() -> None:
    policy = CompilePolicy(
        tenant_id="tenant-a", auth_scope_digest="scope-7d2", model_snapshot="terra-v1",
        prompt_version="prompt-3", renderer_version="renderer-2",
        schema_version="answer-1", tool_version="tools-4", corpus_version="kb-42",
        compression_version="extractive-2", model_window=8_000,
        output_reserve=1_000, reasoning_reserve=500, safety_margin=500,
    )
    blocks = [
        ContextBlock("system", "3", "instruction",
                     "Answer only from cited evidence.", 100, True, True),
        ContextBlock("policy-refunds", "42", "evidence",
                     "Refunds are available within 30 days. Contact billing@example.com.",
                     10),
        ContextBlock("request", "1", "request", "What is the refund window?", 100, True),
    ]
    gateway = Gateway(
        ContextCompiler(ConservativeCounter(), ArtifactCache()),
        [DemoModel("primary-region", False), DemoModel("secondary-region", True)],
    )
    answer, manifest = gateway.answer(policy, blocks, timeout_s=3.0)
    print(json.dumps({"answer": asdict(answer), "manifest": asdict(manifest)},
                     separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The compiler is conservative because its standard-library counter treats each UTF-8 byte as one token. Replace it with the exact target-model tokenizer or token-count API, keeping the same fail-closed reserve check. Persist the manifest before provider inference in a real service; place canonical events and checkpoints in durable storage rather than this process-local cache. Keep retry ownership in one layer.

## 6. Architectural System Design Scenarios

### Scenario 1 — High-volume product-policy assistant

**Problem statement.** Design a multi-tenant assistant serving 1,200 product-policy questions/second with a 12,000-token approved instruction/tool prefix, 1,500 dynamic tokens, 500 output tokens, p99 ≤ 4 seconds, source citations, 99.9% answer availability, and no cross-tenant/cache-scope leakage. Policy content changes daily; 20% of questions are personalized and may not use a shared final-answer cache.

**Proposed architecture and technology choices.** Store policies in versioned object storage and OpenSearch with tenant/document ACLs. The context compiler uses a Git-reviewed prompt/renderer, Redis retrieval/artifact caches, an exact provider prefix breakpoint for stable instructions/tools, and top-k cited policy chunks after that boundary. A fast model serves ordinary queries; validation failures escalate to an approved stronger model. Only non-personalized, exact normalized FAQs use a final-answer cache. Kafka carries corpus invalidation/version events; Kubernetes workers isolate interactive, cache-fill, and indexing pools. OTel exports latency, cache, token, source, and quality dimensions.

```text
┌──────────────┐ OIDC/tenant ┌──────────────┐ token budget ┌──────────────┐
│ Web/mobile   ├────────────►│ API + policy ├─────────────►│ Context      │
│ clients      │             │ ACL service  │              │ compiler     │
└──────────────┘             └──────┬───────┘              └──────┬───────┘
                                    │ authorized query             │
                            ┌───────▼───────┐              ┌──────▼───────┐
                            │ OpenSearch +  │ chunks       │ Redis scoped│
                            │ object versions├────────────►│ caches       │
                            └───────┬───────┘              └──────┬───────┘
                                    │ corpus events                 │ stable prefix
                            ┌───────▼───────┐              ┌──────▼───────┐
                            │ Kafka/indexer │              │ Model router │
                            └───────────────┘              │ fast→strong  │
                                                           └──────┬───────┘
                                                                  ▼
                                                           ┌──────────────┐
                                                           │ Citation +   │
                                                           │ schema gate  │
                                                           └──────┬───────┘
                                                                  ▼
                                                           ┌──────────────┐
                                                           │ Result + OTel│
                                                           └──────────────┘
```

At a measured `90%` prefix hit rate, uncached prefill demand is `1,200×60×(1,500+12,000×0.10) = 194.4M TPM`; zero hits would require `972M TPM`. Output demand is `36M TPM`, and `4 s` p99 implies up to `4,800` in-flight requests before failover reserve. Split provider cache routing keys enough to avoid hot-key degradation while preserving exact-prefix locality. A daily policy version creates and warms a new namespace; old versions drain without request-path deletion.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| **ACL RAG + stable prefix cache + exact FAQ cache** | Low repeated prefill; bounded retrieval cost | Best hit latency; predictable top-k | Medium-high: indexes, versions, caches, evals | Strong provenance and scope keys; re-auth on hits | High with partitioned retrieval/model pools |
| Entire policy in every uncached prompt | Highest input/prefill cost | TTFT grows with policy | Low initially | Large exposure; still needs ACL/version handling | Quota-limited at burst |
| Semantic answer cache first | Lowest latency on a hit | Very low but false-hit tail | High invalidation and similarity eval | Weakest for personalized/stale answers | High compute, limited by risk tolerance |

**Decision rationale.** ACL-aware retrieval keeps changing evidence out of the daily-invalidated stable prefix, while prefix caching reuses truly stable instructions and tools. Exact caching captures safe repetitive FAQs without semantic false matches. The extra retrieval/versioning work is justified by source freshness, personalization boundaries, and the 5× difference between 90% hits and no prefix hits.

### Scenario 2 — Long-running regulated investigation copilot

**Problem statement.** Design a financial-crime investigation copilot whose cases run for 30 days, accumulate 250,000 events and documents, include PII, resume after worker/region loss, and support analyst-approved external requests. Each active turn must fit a 128K context window, return at p95 ≤ 10 seconds excluding human wait, preserve RPO 0 for case events/approvals, recover in 15 minutes, and retain an immutable seven-year decision chain.

**Proposed architecture and technology choices.** Temporal owns the case workflow; PostgreSQL stores append-only event metadata and checkpoint pointers, and encrypted object storage keeps exact documents/tool outputs by digest. An ACL-aware OpenSearch index retrieves case-scoped evidence. The compiler preserves goal, hypotheses, constraints, entity IDs, unresolved items, signed approval IDs, and recent events; it injects exact cited spans on demand. A structured summarizer creates a candidate checkpoint at 80% of the tested active budget, a deterministic retention validator checks it, and CAS activates it. A zero-trust MCP proxy performs read-only searches and approved requests using short-lived credentials. WORM audit and SIEM retain manifests and policy decisions.

```text
┌──────────────┐ signed action ┌──────────────┐ activities  ┌──────────────┐
│ Analyst UI   ├──────────────►│ Temporal case├────────────►│ Context      │
│ + approvers  │◄────status────┤ workflow     │             │ compiler     │
└──────────────┘               └───┬──────┬───┘             └──────┬───────┘
                                   │      │ checkpoint/CAS          │ evidence
                                   │      ▼                         ▼
                            ┌──────▼───────┐                ┌──────────────┐
                            │ Postgres +   │                │ OpenSearch + │
                            │ object store │                │ exact objects│
                            └──────┬───────┘                └──────┬───────┘
                                   │                                │
                                   │                        ┌───────▼──────┐
                                   │                        │ Model +      │
                                   │                        │ compactor    │
                                   │                        └───────┬──────┘
                                   │                                │ tool proposal
                                   │                        ┌───────▼──────┐
                                   └───────────────────────►│ MCP proxy    │
                                                            │ RBAC/PII     │
                                                            └───────┬──────┘
                                                                    ▼
                                                            ┌──────────────┐
                                                            │ WORM + SIEM  │
                                                            └──────────────┘
```

Compaction runs outside the interactive turn and never replaces raw evidence. Poison documents or repeatedly invalid summaries go to a DLQ while the previous checkpoint remains active. Replay consumes recorded model, retrieval, and compaction results. A new inference after failure is a new attempt/event, not hidden replay. Identity, ACL, PII, or policy outage fails closed; optional cache outage recomputes; model outage checkpoints and queues the turn.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| **Durable events + retrieval + validated structured checkpoints** | Medium-high platform cost; bounded model input | Stable turns; compaction off path | High: Temporal, CAS, index, evaluator | Strong RPO/audit/provenance; exact rehydration | High across partitioned cases |
| Full raw history per turn | Prohibitive and eventually impossible | TTFT/overflow degrade with case age | Low initially | Maximum active PII exposure; context injection surface | Hard stop at model window |
| Sliding recent window only | Low and predictable | Fast | Low | Smaller exposure but loses early obligations/evidence | High throughput, unacceptable continuity |

**Decision rationale.** The durable retrieval-plus-checkpoint design is the only option that simultaneously handles a 30-day horizon, exact evidence recovery, RPO 0, approval authority, and a finite model window. Its operational cost buys reproducibility and controlled loss: summaries accelerate continuity, while canonical events and exact objects remain authoritative.

## Interview Review

1. **What is context engineering?** Compiling the smallest high-signal, policy-safe token set from versioned authoritative sources for one model call.
2. **Does prompt caching expand the context window?** No. It reuses prefill computation; cached tokens still occupy the active window.
3. **When should retrieval replace compaction?** Retrieval selects evidence from a large changing corpus; compaction preserves continuity across a growing trajectory. They solve different axes.
4. **Why is a summary not canonical state?** It is lossy and model-generated. Exact events, source spans, receipts, and signed approvals must remain recoverable.
5. **What belongs in a cache key?** Tenant and authorization scope plus model, tokenizer, prompt, renderer, schema/tool, corpus, compression, locale/freshness, and normalized request where applicable.
6. **What metric drives prefix-cache capacity planning?** Uncached prefill tokens, alongside output tokens, concurrency, request quotas, and worker locality.

## Primary References

- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [OpenAI prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)
- [Anthropic context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)
- [OpenAI conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- [OpenAI compaction](https://developers.openai.com/api/docs/guides/compaction)
- [Anthropic context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching)
- [LongBench](https://arxiv.org/abs/2308.14508)
- [RULER](https://arxiv.org/abs/2404.06654)
- [Temporal documentation](https://docs.temporal.io/)
- [OWASP prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
