# 15 - Inference and Optimization

**Scope:** Caching, routing, batching, and quantization for hosted and self-managed model inference.
**Study goal:** Remove redundant computation and memory traffic while preserving policy, task quality, isolation, and complete latency SLOs.

Optimization is a constrained control problem, not a collection of speed switches. A semantic response hit can be fast and wrong. A cache-affinity router can improve TTFT while overloading decode. A larger batch can increase tokens/second while reducing user-visible goodput. A four-bit artifact can use less memory yet run slower without a native kernel. Measure accepted outcomes on the exact workload, artifact, engine and hardware.

## 1. System Topology & Data Flow

### Reference serving topology

```text
                                      CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ model/route policy │ cache schema/TTL/invalidation │ scheduler/admission     │
│ signed model+tokenizer+quant+kernel registry │ eval gates │ canary/rollback   │
│ quotas/SLOs/autoscaling │ residency/security │ pricing │ telemetry/runbooks   │
└────────────┬─────────────────────┬──────────────────────┬─────────────────────┘
             │ signed routes       │ immutable release    │ budgets/limits
             ▼                     ▼                      ▼
┌──────────────┐ auth/deadline ┌──────────────────────────────────────────────┐
│ client/agent ├──────────────►│ inference gateway                           │
└──────────────┘               │ schema │ quota │ idempotency │ cancellation │
                               └──────────────┬───────────────────────────────┘
                                              │ authorized read-only request
                                              ▼
                                   ┌──────────────────────┐
                                   │ exact/semantic       │────► response store
                                   │ response cache       │      + provenance
                                   └──────────┬───────────┘
                                              │ miss
                                              ▼
                                   ┌──────────────────────┐
                                   │ model-policy router  │ capability/quality/
                                   │ logical route        │ cost/region/risk
                                   └──────────┬───────────┘
                                              ▼
                                   ┌──────────────────────┐
                                   │ inference pool/EPP   │ health/queue/KV/
                                   │ replica route        │ adapter/locality
                                   └────┬────────┬────────┘
                                        │        │
                          ┌─────────────┘        └─────────────┐
                          ▼                                    ▼
                  ┌──────────────┐                      ┌──────────────┐
                  │ worker A     │                      │ worker B     │
                  │ continuous   │                      │ continuous   │
                  │ batch/paged KV│                     │ batch/paged KV│
                  │ quant kernel │                      │ quant kernel │
                  └──────┬───────┘                      └──────┬───────┘
                         └─────────────┬────────────────────────┘
                                       ▼
                           ┌─────────────────────────┐
                           │ tiered KV: GPU/CPU/SSD │
                           │ optional remote cache  │
                           └─────────────────────────┘

 TOOL PROXY: policy/credential/idempotency boundary; never response-cache effects
 PERSISTENCE: route/eval history │ job/item ledger │ cache invalidation/outbox
 TELEMETRY: queue/prefill/TTFT/ITL/E2E │ token/block hits │ route/precision/quality
```

The two routers have different authority. The model-policy router chooses an eligible logical model/cascade only after capability, residency, risk and quality constraints. The replica router then chooses a compatible physical worker using health, deadline, queue, active decode/KV load, adapter locality and reusable prefix. KV affinity cannot make an ineligible model or saturated worker eligible.

### End-to-end request flow

1. The gateway authenticates user/workload/tenant, canonicalizes input, validates token/output bounds, sets request/trace/idempotency IDs and propagates one deadline through queue and stream.
2. Policy classifies the request as read-only or effectful and constructs an authorization scope. Exact or semantic response-cache lookup happens only after this step. Tool effects and transactions are never replayed as cached language responses.
3. An exact key includes canonical messages plus prompt, model, tokenizer, tools/schema, decoding, retrieval/data/ACL, locale and policy versions. Semantic lookup additionally requires calibrated equivalence and current provenance. Ambiguity becomes a miss.
4. On a miss, hard eligibility removes models that fail capability, context, tool, residency, retention, safety or budget policy. A rules/learned router chooses within the remaining set and records reason, score, calibration and route-policy version.
5. The replica picker excludes unhealthy, incompatible and queue/KV-saturated workers, then scores predicted prefill after reusable prefix, active decode, queue age, adapter and network locality. Prefix metadata remains a hint; the worker verifies blocks or recomputes.
6. The scheduler admits against sequence, token, KV, workspace and deadline budgets. Continuous batching removes finished sequences and joins new ones per iteration. Long prefills are chunked or sent to a separate prefill pool when target measurements justify it.
7. The worker validates the immutable `(weights, tokenizer/template, adapter, quant recipe, KV precision, engine/kernel)` release tuple. It uses only kernels proven available at startup; silent CPU or high-precision fallback is a failure signal.
8. Streaming reports actual served model/precision internally. Cancellation stops decode at a scheduler boundary and releases KV references; partially delivered streams are not transparently restarted.
9. A verified, policy-compliant read response may be cached with provenance, versions and expiry. Side effects remain in the tool ledger with idempotency and receipts.
10. Telemetry separates queue, cache lookup/transfer, tokenization, prefill, TTFT, ITL, decode, E2E, route regret, quality, OOM/preemption, wasted post-cancel tokens and cost per accepted outcome.

## 2. Core Mechanics & Algorithms

### 2.1 Prefill, decode, memory, and measurement

Prefill processes prompt tokens in parallel, constructs layer KV state and produces the first output token. It is commonly compute-sensitive. Decode repeatedly produces one token while reading growing KV state and weights; it is commonly memory-bandwidth/synchronization sensitive. These are workload tendencies, not laws for every model/kernel.

Without a KV cache, autoregressive decoding repeatedly reconstructs prior attention state. KV reuse makes work per new attention step linear in retained sequence length rather than recomputing the whole prefix, while memory grows linearly:

```text
weight_bytes ~= parameters × weight_bits/8 + scales + zero_points + padding

KV_bytes/token/sequence ~=
  2 × layers × KV_heads × head_dim × bytes/element

ideal_KV_token_capacity ~= usable_KV_pool_bytes / KV_bytes_per_token
```

The factor `2` is K and V. GQA/MQA reduces KV heads. Parallel sharding changes per-rank storage. Activations, logits, graphs, workspaces, allocator metadata and fragmentation must be measured separately.

Metric definitions:

```text
TTFT = first content token received - request submitted
ITL  = (final time - first-token time)/(output_tokens - 1)
E2E  = final response received - request submitted

goodput = requests meeting TTFT + ITL + E2E + quality / wall time
```

Compare tools only after aligning whether queue/network/first token are included. Report prompt/output distributions, arrivals/concurrency, cache state, engine, commit, model/tokenizer/quant release, kernel, hardware/driver, parallelism and quality suite. Tokens/second alone is not a product SLO.

### 2.2 Caching: four contracts and algorithms

| Cache | Match/key | Reuse | Correctness boundary |
|---|---|---|---|
| Exact response | complete canonical request and versions | final response | deterministic-equivalent read contract |
| Semantic response | embedding candidates + equivalence verifier | prior response | domain equivalence, ACL, freshness, policy |
| Provider prompt | exact rendered prefix under provider contract | provider prefix state | model/project/TTL/minimum/breakpoint |
| Engine KV prefix | token block hash chain and release identity | layer KV tensors | exact compatible model/tokenizer/adapter/position/precision/trust |

**Exact response cache.** Canonical serialization and cryptographic hashing are `O(B)` in request bytes. Lookup is expected `O(1)` in a hash store. Include every response-changing field: system/developer prompt, model/revision, decoding parameters, tool definitions, output schema, retrieval index/data/ACL version, locale, safety policy and authorization sharing group. Store source versions, accepted quality and expiry. Sampling changes the product from “sample again” to “return this accepted sample”; state that contract explicitly.

**Semantic response cache.** Normalize and embed, use ANN retrieval (index-dependent approximate sublinear search), rerank `k` candidates in `O(k)`, and apply intent/domain/policy/freshness equivalence. A cosine threshold alone is not correctness. Namespace by tenant/sharing group, role, language, model/prompt/data/policy versions and response class. Disable personalized, mutable, security-sensitive, high-stakes and transactional reuse unless an authoritative verifier rechecks current sources. Track false hits with adjudicated denominators.

**Provider prompt cache.** Place stable instructions, examples, tools, schemas and common documents before variable content. As researched on 2026-08-21, OpenAI's GPT-5.6-era explicit cache contract uses a 1,024-token minimum through a breakpoint, 30-minute refreshable lifetime, read price `0.1x` and write price `1.25x`; Anthropic's default five-minute explicit write/read multipliers are `1.25x/0.1x`, with a one-hour write at `2x`; Gemini minimums and implicit behavior differ by model. These are dated provider contracts, not universal semantics or hit guarantees.

For prefix `T`, write multiplier `w`, read multiplier `r`, and `n` later hits:

```text
uncached = (n+1)TP
cached   = wTP + nrTP
break-even: n > (w-1)/(1-r)
```

At `w=1.25,r=0.1`, one later hit clears the approximate arithmetic threshold `0.278`; at `w=2`, the approximate threshold is `1.112`, so two whole hits. TTL, minimum length, eviction, changed prefix and affinity misses can erase the gain. Use provider-reported cached/write tokens, not request-hit counts.

**Engine KV prefix cache.** Tokenize, split into full blocks and compute `H_i = SHA256(H_(i-1) || tokens_i || release_identity || tenant_salt)`. Lookup is `O(number_of_blocks)`. Only exact full compatible blocks reuse. Paged allocation maps logical blocks to noncontiguous physical pages, reducing fragmentation and allowing reference-counted sharing. A radix tree instead finds longest prefixes in `O(L)` token/tree traversal. Tiered GPU/CPU/SSD/remote KV is worthwhile only when lookup plus transfer is less than recomputation at the current queue/interconnect state.

Cache invariants:

- authentication/authorization precede lookup; tenant salt is server-derived HMAC;
- no cache key omits model, tokenizer/template, adapter, positional/KV precision, data/ACL or policy version;
- response caches never replay tool side effects;
- semantic ambiguity and stale provenance return miss;
- cache metadata may be stale, so workers verify or recompute;
- stampede control uses single-flight, jittered expiry and bounded prewarm; stale-while-revalidate is used only where stale is policy-safe.

### 2.3 Logical and physical routing

Hard eligibility precedes optimization:

```text
eligible = models satisfying capability, context, tool, residency,
           retention, safety, audit, availability and maximum budget

choose m minimizing E[cost_m] + λl E[latency_penalty_m] + λf E[failure_loss_m]
subject to P(quality_m >= target | x) >= target_confidence
```

Rules are auditable and stable but coarse. A classifier/embedding router predicts task complexity. A cascade runs a cheaper model then escalates on confidence/verifier/policy failure. Learned preference routers predict relative model quality. Contextual bandits can explore only inside hard eligibility and spend/safety limits. Router inference is `O(MF)` for scoring `M` routes with `F` features unless a smaller candidate index is used.

Public results do not guarantee production savings. RouteLLM's “over 2x” occurred in some evaluated strong/weak pairs and benchmarks. RouterBench's simple routers did not consistently beat its zero-router baseline, and judge error harmed cascades. Train, calibrate and gate on current application traffic/model revisions. Track OOD, route share, escalation, fallback, quality/cost Pareto frontier and regret against an offline allowed-route oracle.

A replica score can be:

```text
score(worker) = α × predicted_uncached_prefill_tokens
              + β × active_decode_blocks
              + γ × queue_delay
              + δ × adapter/network_penalty
              - ε × request_age
```

First filter health, exact release/adapter compatibility, residency, KV/workspace capacity and deadline feasibility. Then minimize score. For `R` replicas, a full scan is `O(R)`; maintain heaps/indexes cautiously because load/KV state changes every iteration. Cache affinity is bounded: when imbalance crosses a threshold, spill to a less-local worker and recompute rather than destroy ITL/p99.

Fallback is a directed acyclic graph with attempt, deadline and cost limits:

```text
local quantized -> local high precision -> approved provider
                -> asynchronous queue or explicit failure
```

Never switch model/precision silently after partial generation.

### 2.4 Batching and scheduling

Static batching pads and waits for the slowest sequence. Dynamic batching waits briefly for compatible arrivals. Continuous/in-flight batching schedules at token-iteration granularity:

```text
PENDING -> PREFILLING -> DECODING -> FINISHED
                  └──── capacity/preemption ───► PAUSED -> DECODING
any nonterminal -> CANCELLED/DEADLINE_EXCEEDED -> release KV
```

At every iteration, retire completed/cancelled sequences, account KV pages, reserve active-decode completion capacity, admit prefills within token/request/page budgets, and execute one step. Selection is `O(Q log Q)` with a priority queue for `Q` candidates; block allocation is near `O(1)` per page with free lists. Weighted token-cost fairness is superior to request-count fairness for heterogeneous prompts and outputs.

Long prefills can stall interactive decodes. Chunked prefill admits bounded prompt chunks alongside active decode; too-small chunks add kernel/repeated-KV overhead and too-large chunks hurt ITL. Prefill/decode disaggregation permits independent phase scaling but adds KV transfer and services. Choose either only from an SLO load sweep on the actual interconnect.

Engine batching is not provider offline batching. An offline Batch API trades TTFT for deadline/discount/separate quota; item results may be out of order and partially complete. Maintain a `custom_id` ledger and retry only unfinished retryable records.

Batching invariants:

- admission occurs before OOM using sequence/token/KV/workspace/deadline budgets;
- interactive and bulk classes have reservations or separate pools;
- client priority is authenticated, not trusted input;
- cancellation releases queue, KV and stream resources;
- choose maximum **goodput** with burst/failure headroom, not maximum raw throughput.

### 2.5 Quantization mechanics and quality invariants

Affine quantization maps high-precision `x` to code `q = clamp(round(x/s)+z)` and reconstructs `x_hat = s(q-z)`. Per-tensor scale is cheap and outlier-sensitive; per-channel/group/block scales isolate ranges with metadata/kernel cost. PTQ calibrates after training, QAT simulates quantization during training, and quantized fine-tuning adapts an already compact representation.

| Choice | Examples | Optimizes | Principal risk |
|---|---|---|---|
| Weight only | W4A16, W8A16, GPTQ, AWQ | weight memory/bandwidth/model fit | dequant/kernel overhead; accuracy |
| Weight + activation | W8A8, FP8 | native compute and memory | calibration/outliers/hardware support |
| KV precision | FP8/INT8/vendor FP4 | long-context/concurrency capacity | accumulated long-sequence error |
| Mixed sensitive layers | higher precision embeddings/head/outliers | recover quality | less compression, complex artifact |

LLM.int8(), SmoothQuant, GPTQ, AWQ and FP8 solve different outlier/reconstruction/format problems. Their paper speedups are scoped to particular models, engines, kernels, hardware, shapes and baselines; they are not forecasts. Algorithm support is not native kernel support. A valid compact checkpoint can be slower because dequantization, packing, unsupported operators, CPU fallback, small batches or communication dominate.

Quantization cost is typically `O(N)` over `N` weights plus method-specific calibration/reconstruction; GPTQ-like layer reconstruction adds matrix/second-order work. Ideal weight storage is `Nb/8`, but scales, zero points, padding, high-precision layers and parallel replication reduce savings.

Release invariant: a quant artifact is immutable and distinct. Its manifest binds base-model/tokenizer/template digests, quantizer/version, calibration lineage, recipe/scales/excluded layers, engine/compiler/kernel, container, driver requirements, evaluation, signer and approval. At admission, verify signature/digest/capability and run a golden-vector startup test.

Quality gates compare the exact high-precision baseline on policy-compliant task success, safety, tool/schema validity, domain/language, long context, rare tokens, math/code/adversarial slices, paired answer difference, TTFT/ITL/goodput, peak memory, power and cost at target concurrency. Retain a tested high-precision rollback.

## 3. Token Economics & NFR Analysis

### 3.1 Explicit cost per 1,000 executions

These are scoped planning assumptions, not published benchmark results. One thousand read-only executions each use 12,000 input and 2,000 output tokens. Of 12M total input tokens, 8M belong to 50 stable-prefix groups repeated 20 times: 0.4M are cache writes and 7.6M are reads; 4M are variable uncached input. Planning rates per million input/output are `sol $5/$30`, `terra $2/$12`, `luna $0.20/$1.20`; cache reads are `0.1x` input and writes `1.25x`. Replace with contract rates.

| Tier | Without prompt cache | With measured write/read split |
|---|---:|---:|
| `sol` | `12×$5 + 2×$30` = **$120.00** | `4×$5 + .4×$6.25 + 7.6×$.50 + 2×$30` = **$86.30** |
| `terra` | `12×$2 + 2×$12` = **$48.00** | `4×$2 + .4×$2.50 + 7.6×$.20 + 2×$12` = **$34.52** |
| `luna` | `12×$.20 + 2×$1.20` = **$4.80** | `4×$.20 + .4×$.25 + 7.6×$.02 + 2×$1.20` = **$3.45** |

A constrained route sends 70% to `luna`, 25% to `terra`, and 5% to `sol`: `.70×$3.452 + .25×$34.52 + .05×$86.30 = $15.36`. Router classification uses 1M `luna` input and 0.1M output = `$0.32`; cache embedding/rerank/store is `$1.50`; gateway/telemetry allocation is `$0.80`. Total is **$17.98/1K executions**, versus **$34.52** for all-`terra` cached under the same token assumption. With 930 policy-compliant successes, cost per 1,000 accepted successes is `$17.98×1000/930 = $19.33`.

This comparison assumes route quality gates pass, every tier produces the same output length, and response semantic-cache hits are zero. Add expected false-hit loss, escalation double calls, accelerator/KV/network allocation, retries and tools when present. Do not count a cheap failed answer as savings.

```text
C_request = uncached_input×P_in + cache_write×P_write
          + cache_read×P_read + output×P_out
          + router + cache lookup + tool/network + allocated accelerator

effective_cost/success = total provider + serving + failed/retried work
                       / policy-compliant successes
```

### 3.2 Latency SLOs and mitigation

Internal starting targets for an interactive read path, not public hardware benchmarks:

| Stage/metric | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| Exact cache lookup | 2 ms | 8 ms | 20 ms | local partition, bounded key, breaker |
| Semantic lookup + verifier | 20 ms | 80 ms | 180 ms | candidate cap, domain disable, miss fallback |
| Model-policy route | 3 ms | 15 ms | 40 ms | rules first, cached features, safe default |
| Replica route/admission | 2 ms | 10 ms | 30 ms | bounded eligible set, stale-state fallback |
| Queue delay | 20 ms | 150 ms | 500 ms | fair queues, reservations, early shed |
| TTFT, cache miss | 300 ms | 1.5 s | 3 s | prefix reuse, chunked prefill, warm pool |
| ITL | 20 ms | 55 ms | 90 ms | decode reservation, batch/KV cap |
| E2E for 2K output | 4 s | 12 s | 25 s | output cap, cancellation, route/batch headroom |

Report warm and cold cache, each prompt/output cohort, served model/precision, successes/failures/cancellations and cache hits/misses separately. A hit-rate improvement that violates ITL or correctness is a regression.

### 3.3 Capacity, KV sizing, and backpressure

Assume target-workload replay measures **18 requests/s/replica at the complete SLO and quality gate**. For 120 requests/s peak, seven replicas cover arithmetic load; add two replicas for failure/burst headroom, yielding **nine**. This number is valid only for that measured release/hardware/workload.

At 12K input and 2K output, offered tokens are 1.44M input/s and 0.24M output/s. A measured 65% eligible prefix-token hit leaves roughly 0.504M input tokens/s to prefill, excluding cache lookup/transfer. Do not use this estimate as kernel capacity.

Illustrative memory arithmetic for 32 layers, 8 KV heads, 128 head dimension and BF16 KV:

```text
KV/token = 2×32×8×128×2 bytes = 131,072 bytes = 128 KiB
48 GiB ideal usable KV / 128 KiB = 393,216 tokens
```

That is an architectural upper bound before fragmentation, graph/workspace/runtime reserve and parallel layout. FP8/INT8 KV may halve raw bytes but requires target long-context validation and native support.

Apply hierarchical admission: tenant request/input/output/concurrency/cost quotas; model context/adapter/SLO compatibility; replica sequence/batched-token/KV/workspace/deadline limits; cache byte/bandwidth/IOPS/promotion quotas. Backpressure returns retry hints, reduces allowed output, selects an eligible smaller route, defers bulk work, or sheds low priority before OOM. Reserve interactive capacity and use token-cost weighted fairness. Cancellation and expired deadlines reclaim work promptly.

### 3.4 NFR targets and trade-offs

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Availability | 99.95% gateway/router; 99.9% model pool | More pools/routes and validation |
| Quality | <=1 percentage-point accepted-task regression per approved slice | Limits aggressive route/quant/cache optimization |
| Cache correctness | zero known cross-tenant/stale-policy hits; semantic false-hit bound per domain | Lower hit rate and verifier cost |
| Latency | p95/p99 TTFT, ITL and E2E targets by cohort | Reserved capacity lowers utilization |
| Goodput | capacity measured at all latency + quality constraints | Lower than headline tokens/s |
| RPO | 0 route/policy/job/effect ledger; cache/KV may be lost | Durable authority outside cache |
| RTO | <=5 min route rollback; <=15 min pool failover; <=60 min cache rebuild | Warm high-precision capacity costs |
| Security | tenant-salted cache, signed release, no unsafe artifact loading | Less sharing; release overhead |
| Compliance | provider cache/retention/residency verified per route | Fewer cheap providers/tiers |
| Fairness | bounded queue age/service by tenant and language cohort | Scheduler complexity |

## 4. Distributed Resilience & Security

### 4.1 Durable ownership and execution

```text
┌──────────────┐ request/item ┌──────────────┐ lease/idempotency ┌────────────┐
│ Temporal/job ├─────────────►│ Kafka queue  ├──────────────────►│ gateway/   │
│ workflow     │◄─checkpoint──┤ + DLQ        │◄────receipt───────┤ model pool │
└──────┬───────┘              └──────────────┘                   └─────┬──────┘
       │ outbox: route/outcome/eval                                disposable KV
       ▼                                                               ▼
┌──────────────┐ signed config ┌──────────────┐ snapshots/events ┌────────────┐
│ route/config │──────────────►│ cache control│◄────────────────►│ KV workers │
│ + registry   │               │ plane       │ verify/recompute  │ GPU/CPU    │
└──────────────┘               └──────────────┘                  └────────────┘
```

Durable authority: signed route/policy config, immutable model/quant registry, response-cache versions, item/job ledger and labelled route/eval history. GPU KV is disposable acceleration. Tiered KV metadata can rebuild from worker reports/events or be discarded. Stale metadata can cause recompute, never incompatible tensor reuse.

Temporal or equivalent owns long/offline workflow state, deadlines and compensation. Kafka claims use leases and stable item IDs; consumers are idempotent. Persist external item receipt/output hash before acknowledging. Poison schema/artifact records enter a dead-letter queue; capacity throttling remains retryable. Provider batch output is reconciled by `custom_id`, never file order, and only unfinished retryable items are resubmitted.

Retries use exponential full jitter inside the remaining deadline. A partially delivered stream is not blindly retried. Breakers isolate provider/model/region/replica/cache tier; half-open probes are small **uncached** requests so a cache hit cannot certify failed compute. One layer owns each retry. Fallback routes are acyclic, finite, policy-approved and recorded.

### 4.2 Failure handling and mixed-version safety

| Failure | Detection | Containment/recovery |
|---|---|---|
| Cache stampede | write/prefill/queue surge | single-flight, jittered TTL, bounded prewarm |
| Semantic false hit/poison | outcome/provenance adjudication | disable domain, purge namespace, stricter verifier |
| Stale KV index | worker block miss/event lag | verify, recompute, snapshot/replay/TTL |
| Hot-prefix overload | TTFT improves while ITL/queue worsens | bound affinity, replicate/offload or recompute |
| KV exhaustion/OOM | reservation/preemption/allocator telemetry | stop admission, reserve decode, cap/offload |
| Long prefill interference | ITL by prompt cohort | chunk or isolate prefill after workload test |
| Router distribution shift | OOD/regret/escalation | fixed safe route, recent replay, rollback |
| Fallback loop | attempts/cost exceed budget | DAG validation, deadline/attempt/cost cap |
| Unsupported quant kernel | startup capability/CPU fallback | reject artifact; high-precision route |
| Quant quality drift | paired slice/effect regression | stop canary, drain namespace, rollback |
| Mixed release fleet | inconsistent response/cache misses | immutable tuple namespace; drain old pool |
| Disconnect leaks compute | post-cancel token/KV growth | scheduler cancellation and reference leak tests |

Roll out one axis at a time: engine, model, tokenizer/template, quant recipe/kernel, scheduler or cache key. Shadow, replay a stratified workload, canary by risk/tenant, then expand on quality, safety, p99 goodput, OOM/preemption, isolation and accepted-cost gates. Changing any state-computing artifact changes the KV namespace. Maintain a warm tested high-precision rollback.

### 4.3 Zero Trust MCP, cache isolation, and data governance

MCP tool descriptions/results are untrusted data. Model optimization never bypasses the tool PEP. Tool-level RBAC sets role baselines; ABAC narrows tenant, task, action, resource and current state. Response caches contain only verified read results; tool side effects use their own durable idempotency/effect ledger.

Cache lookup follows authenticated policy construction. Namespace response/embedding/KV caches with a server-derived tenant or approved-sharing-group HMAC, not a client salt. Include document ACL and version for RAG. Use cryptographic block hashes across tenants because collisions and timing hits can leak private-prefix membership. Encrypt transport/storage, apply region and retention policy, audit clear/export, and propagate deletion to response, embedding and tiered KV stores.

PII flow is `classify -> minimize -> redact/tokenize -> authorize route/cache -> execute -> audit/delete`. Provider prompt-cache isolation, lifetime, residency and zero-retention eligibility are verified for the exact provider/cloud route. Do not assume a direct-provider contract survives through every reseller.

### 4.4 Quantized artifact supply chain and audit

Prefer tensor-only formats such as Safetensors and pin immutable revisions. Pickle/custom remote code can execute during load; scanning helps but does not establish trust. Verify publisher, signature, digests, license, base model, tokenizer/template, quantizer, calibration lineage, recipe/scales, excluded layers, engine/compiler/kernel/container/driver, evaluation and approval as an AI/ML bill of materials.

The inference runtime has no artifact-write permission. Registry admission and startup verify manifest, digest, required native operator coverage and golden vectors before health becomes ready. Every internal response/audit record carries route-policy ID, actual model/artifact/precision, cache decision/namespace version, scheduler class, fallbacks, cost/tokens and outcome. Immutable audit records route, policy, release and side-effect boundaries without storing raw PII prompts.

## 5. Production Enterprise Code

This Python 3.11 standard-library program implements the serving control path, not a low-level accelerator kernel. It has a complete versioned exact-cache key, tenant HMAC namespace, hard-eligibility model router, load-bounded KV-aware replica picker, bounded token-budgeted fair batch scheduler, quant artifact validation, structured correlation logs, exponential full-jitter retries, closed/open/half-open breakers, primary-to-secondary model fallback, and a deterministic read-only degradation response. The simulated backends make failure paths reproducible; production adapters implement the same interfaces with an inference gateway/engine, and registry admission verifies the release signature described in Section 4.

```python
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Protocol, Sequence


class TransientFailure(RuntimeError):
    """A retryable capacity or transport failure."""


class PermanentFailure(RuntimeError):
    """A non-retryable policy, schema, or artifact failure."""


class CircuitOpen(TransientFailure):
    """A dependency is isolated pending a probe."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "severity": record.levelname,
                 "message": record.getMessage()}
        for key in ("request_id", "tenant_ref", "route", "backend",
                    "attempt", "status", "cache"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("inference-control")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class Breaker:
    def __init__(self, threshold: int = 2, recovery_s: float = 2.0):
        self._threshold = threshold
        self._recovery_s = recovery_s
        self._failures = 0
        self._state = "closed"
        self._opened_at = 0.0
        self._probe = False
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if self._state == "open":
                if time.monotonic() - self._opened_at < self._recovery_s:
                    raise CircuitOpen("circuit open")
                self._state = "half_open"
            if self._state == "half_open":
                if self._probe:
                    raise CircuitOpen("half-open probe active")
                self._probe = True

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"
            self._probe = False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe = False
            if self._state == "half_open" or self._failures >= self._threshold:
                self._state = "open"
                self._opened_at = time.monotonic()


@dataclass(frozen=True)
class Request:
    request_id: str
    tenant: str
    prompt: str
    risk: str
    region: str
    read_only: bool
    input_tokens: int
    max_output_tokens: int
    prompt_version: str
    tools_version: str
    output_schema_version: str
    data_version: str
    acl_version: str
    policy_version: str
    tokenizer_version: str
    decoding: tuple


@dataclass(frozen=True)
class Route:
    name: str
    quality_floor: float
    price_rank: int
    regions: frozenset[str]
    supports_high_risk: bool
    max_context: int
    artifact_id: str


@dataclass(frozen=True)
class Replica:
    name: str
    route: str
    artifact_id: str
    healthy: bool
    queue_ms: float
    active_decode_blocks: int
    matching_prefix_tokens: int
    free_kv_tokens: int


@dataclass(frozen=True)
class QuantManifest:
    artifact_id: str
    artifact_sha256: str
    base_model_sha256: str
    tokenizer_version: str
    recipe: str
    engine: str
    kernel: str
    native_kernel: bool
    quality_gate_passed: bool
    golden_vector_passed: bool

    def verify(self, artifact: bytes, expected_base: str) -> None:
        if not hmac.compare_digest(hashlib.sha256(artifact).hexdigest(),
                                   self.artifact_sha256):
            raise PermanentFailure("quant artifact digest mismatch")
        if not hmac.compare_digest(self.base_model_sha256, expected_base):
            raise PermanentFailure("base model digest mismatch")
        if self.recipe not in {"W4A16-AWQ", "W8A8-SmoothQuant", "FP8"}:
            raise PermanentFailure("quant recipe is not approved")
        if not (self.native_kernel and self.quality_gate_passed
                and self.golden_vector_passed):
            raise PermanentFailure("artifact readiness gate failed")


class ModelRouter:
    def __init__(self, routes: Sequence[Route], policy_version: str):
        self._routes = tuple(routes)
        self.policy_version = policy_version

    def choose(self, request: Request) -> Route:
        total = request.input_tokens + request.max_output_tokens
        eligible = [route for route in self._routes
                    if request.region in route.regions
                    and total <= route.max_context
                    and (request.risk != "high" or route.supports_high_risk)]
        if request.policy_version != self.policy_version or not eligible:
            raise PermanentFailure("no policy-eligible model route")
        quality_target = .95 if request.risk == "high" else .82
        eligible = [route for route in eligible
                    if route.quality_floor >= quality_target]
        if not eligible:
            raise PermanentFailure("no route meets quality floor")
        return min(eligible, key=lambda route: route.price_rank)


class ReplicaPicker:
    @staticmethod
    def choose(route: Route, request: Request,
               replicas: Sequence[Replica]) -> Replica:
        required_kv = request.input_tokens + request.max_output_tokens
        eligible = [replica for replica in replicas
                    if replica.healthy and replica.route == route.name
                    and replica.artifact_id == route.artifact_id
                    and replica.free_kv_tokens >= required_kv
                    and replica.queue_ms <= 400]
        if not eligible:
            raise TransientFailure("no compatible replica has capacity")

        def score(replica: Replica) -> float:
            uncached = max(0, request.input_tokens
                           - replica.matching_prefix_tokens)
            return (uncached + 8 * replica.active_decode_blocks
                    + 20 * replica.queue_ms)

        return min(eligible, key=score)


class CompleteExactCache:
    def __init__(self, secret: bytes):
        self._secret = secret
        self._entries: dict[str, tuple[float, dict[str, object]]] = {}
        self._lock = threading.Lock()

    def _tenant_ref(self, tenant: str) -> str:
        return hmac.new(self._secret, tenant.encode(),
                        hashlib.sha256).hexdigest()

    def key(self, request: Request, route: Route) -> str:
        canonical = {
            "tenantRef": self._tenant_ref(request.tenant),
            "prompt": request.prompt,
            "promptVersion": request.prompt_version,
            "toolsVersion": request.tools_version,
            "outputSchemaVersion": request.output_schema_version,
            "dataVersion": request.data_version,
            "aclVersion": request.acl_version,
            "policyVersion": request.policy_version,
            "tokenizerVersion": request.tokenizer_version,
            "decoding": request.decoding,
            "maxOutputTokens": request.max_output_tokens,
            "risk": request.risk,
            "region": request.region,
            "readOnly": request.read_only,
            "route": route.name,
            "artifact": route.artifact_id,
        }
        return hashlib.sha256(json.dumps(
            canonical, separators=(",", ":"), sort_keys=True
        ).encode()).hexdigest()

    def get(self, key: str) -> dict[str, object] | None:
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if not entry or entry[0] <= now:
                self._entries.pop(key, None)
                return None
            return dict(entry[1])

    def put(self, key: str, value: dict[str, object], ttl_s: float) -> None:
        jittered = ttl_s * random.uniform(.9, 1.1)
        with self._lock:
            self._entries[key] = (time.time() + jittered, dict(value))


@dataclass(frozen=True)
class ScheduledItem:
    request_id: str
    tenant: str
    tokens: int


class FairTokenBatcher:
    """Tenant round-robin admission under request and token budgets."""

    def __init__(self, max_tokens: int, max_requests: int,
                 max_queued: int = 100):
        self._max_tokens = max_tokens
        self._max_requests = max_requests
        self._max_queued = max_queued
        self._queued = 0
        self._queues: dict[str, deque[ScheduledItem]] = {}
        self._rotation: deque[str] = deque()
        self._lock = threading.Lock()

    def submit(self, item: ScheduledItem) -> None:
        if not 0 < item.tokens <= self._max_tokens:
            raise PermanentFailure("request exceeds batch token budget")
        with self._lock:
            if self._queued >= self._max_queued:
                raise TransientFailure("batch queue is applying backpressure")
            queue = self._queues.setdefault(item.tenant, deque())
            if not queue:
                self._rotation.append(item.tenant)
            queue.append(item)
            self._queued += 1

    def next_batch(self) -> list[ScheduledItem]:
        batch: list[ScheduledItem] = []
        used = 0
        with self._lock:
            attempts = len(self._rotation)
            while self._rotation and len(batch) < self._max_requests:
                tenant = self._rotation.popleft()
                queue = self._queues[tenant]
                item = queue[0]
                if used + item.tokens <= self._max_tokens:
                    batch.append(queue.popleft())
                    used += item.tokens
                    self._queued -= 1
                if queue:
                    self._rotation.append(tenant)
                else:
                    self._queues.pop(tenant, None)
                attempts -= 1
                if attempts <= 0:
                    attempts = len(self._rotation)
                    if attempts and all(
                        used + self._queues[name][0].tokens > self._max_tokens
                        for name in self._rotation
                    ):
                        break
        return batch


class Backend(Protocol):
    name: str

    def generate(self, request: Request, route: Route,
                 replica: Replica, timeout_s: float) -> dict[str, object]:
        """Return a complete response or raise a classified failure."""


class DemoBackend:
    def __init__(self, name: str, failures_before_success: int,
                 served_model: str | None = None,
                 artifact: str | None = None):
        self.name = name
        self._failures = failures_before_success
        self._served_model = served_model
        self._artifact = artifact

    def generate(self, request: Request, route: Route,
                 replica: Replica, timeout_s: float) -> dict[str, object]:
        if timeout_s <= 0:
            raise TransientFailure("deadline exceeded")
        if self._failures > 0:
            self._failures -= 1
            raise TransientFailure(f"{self.name} temporarily unavailable")
        return {"status": "ok", "answer": "verified read response",
                "servedModel": self._served_model or route.name,
                "artifact": self._artifact or route.artifact_id,
                "replica": replica.name}


class InferenceChain:
    def __init__(self, primary: Backend, secondary: Backend):
        self._backends = (primary, secondary)
        self._breakers = {backend.name: Breaker() for backend in self._backends}

    def invoke(self, request: Request, route: Route, replica: Replica,
               deadline: float, tenant_ref: str) -> dict[str, object]:
        for backend in self._backends:
            breaker = self._breakers[backend.name]
            for attempt in range(1, 3):
                if time.monotonic() >= deadline:
                    return self._deterministic_fallback(request, route)
                try:
                    breaker.before()
                    result = backend.generate(
                        request, route, replica,
                        max(0.0, deadline - time.monotonic())
                    )
                    breaker.success()
                    logger.info("inference completed", extra={
                        "request_id": request.request_id,
                        "tenant_ref": tenant_ref, "route": route.name,
                        "backend": backend.name, "attempt": attempt,
                        "status": "ok", "cache": "miss",
                    })
                    return result
                except CircuitOpen:
                    break
                except PermanentFailure:
                    break
                except (TransientFailure, TimeoutError) as exc:
                    breaker.failure()
                    logger.warning("inference retryable failure", extra={
                        "request_id": request.request_id,
                        "tenant_ref": tenant_ref, "route": route.name,
                        "backend": backend.name, "attempt": attempt,
                        "status": type(exc).__name__, "cache": "miss",
                    })
                    if attempt < 2:
                        cap = min(.02 * (2 ** (attempt - 1)),
                                  max(0.0, deadline - time.monotonic()))
                        time.sleep(random.uniform(0.0, cap))
        return self._deterministic_fallback(request, route)

    @staticmethod
    def _deterministic_fallback(request: Request,
                                route: Route) -> dict[str, object]:
        return {"status": "deferred" if request.read_only else "denied",
                "answer": "Inference unavailable; retry later.",
                "servedModel": "deterministic-fallback",
                "intendedRoute": route.name}


class OptimizationGateway:
    def __init__(self, router: ModelRouter, replicas: Sequence[Replica],
                 cache: CompleteExactCache, chain: InferenceChain,
                 cache_secret: bytes):
        self._router = router
        self._replicas = tuple(replicas)
        self._cache = cache
        self._chain = chain
        self._cache_secret = cache_secret

    def handle(self, request: Request, timeout_s: float = 1.0) -> dict[str, object]:
        if not request.tenant or not request.request_id or request.input_tokens <= 0:
            raise PermanentFailure("invalid authenticated request")
        route = self._router.choose(request)
        key = self._cache.key(request, route)
        tenant_ref = hmac.new(self._cache_secret, request.tenant.encode(),
                              hashlib.sha256).hexdigest()[:16]
        if request.read_only:
            hit = self._cache.get(key)
            if hit:
                logger.info("exact response cache hit", extra={
                    "request_id": request.request_id,
                    "tenant_ref": tenant_ref, "route": route.name,
                    "backend": "cache", "attempt": 0, "status": "ok",
                    "cache": "exact-hit",
                })
                return hit
        replica = ReplicaPicker.choose(route, request, self._replicas)
        result = self._chain.invoke(request, route, replica,
                                    time.monotonic() + timeout_s, tenant_ref)
        if (request.read_only and result.get("status") == "ok"
                and result.get("servedModel") == route.name
                and result.get("artifact") == route.artifact_id):
            self._cache.put(key, result, ttl_s=60)
        return result


def make_request(tenant: str = "tenant-a") -> Request:
    return Request(
        request_id=uuid.uuid4().hex, tenant=tenant,
        prompt="Summarize the current authorized policy document.",
        risk="normal", region="in-blr", read_only=True,
        input_tokens=4_000, max_output_tokens=500,
        prompt_version="prompt-v7", tools_version="tools-v4",
        output_schema_version="schema-v2", data_version="index-2026-08-21",
        acl_version="acl-91", policy_version="route-policy-12",
        tokenizer_version="tok-v3", decoding=(("temperature", "0"),),
    )


def main() -> None:
    artifact = b"immutable-quantized-tensors"
    manifest = QuantManifest(
        artifact_id="luna-awq-r12",
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        base_model_sha256="a" * 64, tokenizer_version="tok-v3",
        recipe="W4A16-AWQ", engine="engine-4.2", kernel="native-gemm-9",
        native_kernel=True, quality_gate_passed=True,
        golden_vector_passed=True,
    )
    manifest.verify(artifact, "a" * 64)
    routes = (
        Route("luna", .86, 1, frozenset({"in-blr"}), False, 16_384,
              manifest.artifact_id),
        Route("sol", .98, 3, frozenset({"in-blr"}), True, 131_072,
              "sol-fp16-r8"),
    )
    replicas = (
        Replica("luna-1", "luna", manifest.artifact_id, True,
                30, 80, 3_500, 30_000),
        Replica("luna-2", "luna", manifest.artifact_id, True,
                10, 10, 1_000, 30_000),
        Replica("sol-1", "sol", "sol-fp16-r8", True,
                20, 20, 0, 140_000),
    )
    secret = b"rotate-through-managed-key-service"
    cache = CompleteExactCache(secret)
    gateway = OptimizationGateway(
        ModelRouter(routes, "route-policy-12"), replicas, cache,
        InferenceChain(DemoBackend("primary-engine", 0),
                       DemoBackend("secondary-engine", 0)), secret,
    )
    request = make_request()
    first = gateway.handle(request)
    second = gateway.handle(request)

    failover = OptimizationGateway(
        ModelRouter(routes, "route-policy-12"), replicas,
        CompleteExactCache(secret),
        InferenceChain(
            DemoBackend("primary-quantized-down", 3),
            DemoBackend("secondary-high-precision", 0,
                        "luna-high-precision", "luna-fp16-r12")
        ), secret,
    ).handle(make_request())

    outage = OptimizationGateway(
        ModelRouter(routes, "route-policy-12"), replicas,
        CompleteExactCache(secret),
        InferenceChain(DemoBackend("primary-down", 3),
                       DemoBackend("secondary-down", 3)), secret,
    )
    degraded = outage.handle(make_request("tenant-b"))

    batcher = FairTokenBatcher(max_tokens=1_000, max_requests=3)
    batcher.submit(ScheduledItem("a1", "tenant-a", 600))
    batcher.submit(ScheduledItem("a2", "tenant-a", 300))
    batcher.submit(ScheduledItem("b1", "tenant-b", 400))
    batch = batcher.next_batch()
    print(json.dumps({
        "artifactVerified": True,
        "firstBackend": first.get("replica"),
        "secondWasExactCache": first == second,
        "failoverModel": failover.get("servedModel"),
        "degradedModel": degraded.get("servedModel"),
        "batchRequestIds": [item.request_id for item in batch],
        "batchWithinBudget": sum(item.tokens for item in batch) <= 1_000,
        "tenantsRepresented": sorted({item.tenant for item in batch}),
    }, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The healthy primary response is reused through the complete exact-response key. A separate primary outage falls back to an explicit high-precision secondary and is not cached under the quantized artifact key; loss of both models returns a deterministic deferred response rather than replaying an effect. Replica selection balances prefix reuse against queue/decode load, the batch stays within token budget with both tenants represented, and the quant artifact must pass digest, recipe, kernel, quality and golden-vector gates before serving.

## 6. Architectural System Design Scenarios

### Scenario 1 - Multi-tenant interactive RAG and agent service

**Problem statement.** Design inference for 120 streaming requests/s across 2,000 tenants. Inputs have p50/p95/p99 lengths of 4K/24K/80K tokens; outputs are 500/2K/5K. Repeated system/tool prefixes are common, retrieved data changes hourly, and 5% of requests can propose tool writes. Requirements are p95 TTFT under 1.5 seconds, p99 ITL under 90 ms, task success within one percentage point of the approved baseline, zero cross-tenant cache reuse, 99.95% gateway availability, and graceful loss of one replica.

**Proposed architecture.** Gateway authentication constructs tenant/ACL/policy context and separates read-only from effectful requests. Only versioned FAQ-like reads use exact response caching; semantic caching is limited to low-risk intents with provenance revalidation. Stable system/tool schemas precede volatile RAG/user content for provider/KV prefix reuse. Hard policy chooses eligible models; a calibrated model router sends simple reads to a validated quantized tier and high-risk/write tasks to the approved strong tier. Gateway API Inference Extension-style model/pool separation lets an EPP choose compatible replicas using queue, active decode, free KV and salted prefix overlap. Workers use paged KV, continuous batching, chunked prefill and decode reservation. The tool PEP and effect ledger stay outside response caching.

```text
┌──────────────┐ auth/ACL  ┌──────────────┐ read miss ┌──────────────┐
│ clients      ├──────────►│ gateway/cache├──────────►│ policy/model │
│ 120 req/s    │           │ tenant HMAC  │           │ router       │
└──────────────┘           └──────────────┘           └──────┬───────┘
                                                             ▼
                                                    ┌────────────────┐
                                                    │ pool EPP       │
                                                    │ load + KV hint │
                                                    └───┬─────────┬──┘
                                                        ▼         ▼
                                              ┌────────────┐ ┌────────────┐
                                              │ quant pool │ │ strong pool│
                                              │ paged KV/CB│ │ paged KV/CB│
                                              └─────┬──────┘ └─────┬──────┘
                                                    │ proposal      │
                                                    └───────┬───────┘
                                                            ▼
                                                    ┌──────────────┐
                                                    │ tool PEP +   │
                                                    │ effect ledger│
                                                    └──────────────┘
```

Capacity is based on a versioned replay measuring 18 good requests/s/replica at all SLO and quality gates: `ceil(120/18)=7`, plus two failure/burst replicas gives nine. This is not generalized to another GPU, engine, artifact or traffic mix. Load tests sweep token/sequence budgets, chunk size and cache weight under cold-cache, hot-prefix, one-replica-loss and burst conditions.

| Approach | Cost | Latency | Operations | Security/quality | Scalability ceiling |
|---|---|---|---|---|---|
| One strong hosted model; no cache-aware pool | Highest variable cost | Predictable until provider throttle | Low | Simple quality baseline; provider governance | Provider quota/price |
| **Policy router + tenant caches + continuous-batch quant/strong pools** | Lowest accepted-cost target | Best measured goodput; more routing hops | High cache/engine/eval work | Strong hard gates and rollback; cache isolation | High with pool shards |
| One self-hosted strong high-precision pool | High fixed accelerator cost | Good at steady load, poor elasticity | Medium-high | Maximum data control; no route-quality risk | Memory and burst capacity |

**Decision rationale.** The mixed design is selected only because application replay demonstrates the quality and SLO frontier. Hard eligibility prevents the optimizer from trading away tool safety or residency; tenant salts and ACL versions protect reuse; load-bounded affinity avoids TTFT gains that ruin ITL. Separate strong capacity is both the high-risk route and quantization rollback. Goodput, accepted-task cost and cache false hits are release gates, not raw tokens/s.

### Scenario 2 - Restartable offline document enrichment

**Problem statement.** Enrich 8 million independent documents overnight in 10 hours across four regions. Records average 2K input and 200 output tokens, share one of 400 stable instruction/schema prefixes, and may contain PII. The job must be restartable without duplicate billing/output, preserve residency, finish 99.5% of eligible records, cap total spend, and show no more than a one-point extraction-F1 loss versus the approved high-precision baseline. Interactive TTFT is irrelevant.

**Proposed architecture.** A Temporal workflow partitions a versioned manifest into region/model/prefix cohorts; Kafka items carry `custom_id`, data/prompt/schema/model/quant versions, deadline and attempt. The eligibility router fixes high-risk/unsupported languages to a strong route and sends validated cohorts to the quantized route. Where provider retention/residency and the 24-hour contract fit, workers submit asynchronous Batch API files and reconcile results by `custom_id`, never order. Otherwise a dedicated local pool uses throughput-optimized continuous batches and bounded prefix grouping. Raw text remains in regional object storage; queue/telemetry uses opaque references. Each output is schema-validated, PII-filtered, quality-sampled and committed with an idempotent digest before acknowledgement.

```text
┌──────────────┐ partitions ┌──────────────┐ items/custom_id ┌──────────────┐
│ Temporal     ├───────────►│ manifest DB  ├────────────────►│ regional     │
│ deadline/cost│◄─status────┤ RPO 0        │                 │ Kafka + DLQ  │
└──────────────┘            └──────────────┘                 └──────┬───────┘
                                                                    ▼
                                                         ┌──────────────────┐
                                                         │ eligibility route│
                                                         └────┬────────┬────┘
                                                              ▼        ▼
                                                     ┌────────────┐ ┌───────────┐
                                                     │ provider   │ │ local quant│
                                                     │ batch files│ │ batch pool  │
                                                     └─────┬──────┘ └─────┬─────┘
                                                           └──────┬───────┘
                                                                  ▼
                                                         ┌────────────────┐
                                                         │ validate/PII/  │
                                                         │ idempotent sink│
                                                         └────────────────┘
```

Required average completion is `8,000,000/36,000 = 222.2 records/s`; provision for 400 records/s to cover retries, skew and validation. At 2K input, raw offered prefill is 800K tokens/s at that design rate before prefix hits. Size local replicas from measured batch goodput at the F1 gate. Provider jobs maintain queued-token and file-size budgets; expired partial jobs retry only missing retryable IDs. A 50% provider Batch API price reduction is treated as the researched current contract for eligible models, not a permanent universal discount.

| Approach | Cost | Latency/deadline | Operations | Security/quality | Scalability ceiling |
|---|---|---|---|---|---|
| Synchronous hosted calls per record | Highest price/rate pressure | Difficult ten-hour deadline | Simple per call; huge retry surface | Provider policy dependent | Online quota |
| **Regional provider batch where eligible + local quant batch pool + durable item ledger** | Lowest measured mixed cost | Meets deadline with headroom | Highest reconciliation/routing work | Residency gates; paired quality audit | High across regions/pools |
| Local high-precision pool only | High accelerator allocation | Predictable after provisioning | Medium | Strongest placement and simplest quality baseline | Fixed overnight fleet |

**Decision rationale.** Offline work can trade latency for price and utilization, but not correctness, lineage or bounds. The item ledger handles out-of-order/partial provider completion and local redelivery without duplicate output. Prefix cohorts improve provider prompt/KV reuse without crossing regional or tenant boundaries. Quantized routing remains cohort-gated by paired extraction F1 and native-kernel measurements; the high-precision route audits samples and handles unsupported cohorts.

## Interview Review

1. **Prompt cache versus response cache?** Prompt/KV caching reuses exact prefix computation but still generates an answer; response caching reuses the final answer and has a stronger freshness/equivalence boundary.
2. **Why separate model and replica routing?** Policy/quality chooses an eligible logical model; queue/KV locality chooses a compatible worker. Load cannot override safety or residency.
3. **What is the batching objective?** Maximum goodput meeting TTFT, ITL, E2E and quality constraints with failure/burst headroom, not maximum tokens/s.
4. **Why can a cache hit hurt?** Affinity can concentrate active decodes, KV transfer can exceed recompute, or a semantic hit can be stale/wrong.
5. **Does INT4 mean 4x faster than FP16?** No. It suggests ideal raw weight storage near one quarter before metadata; speed depends on native kernels, shapes, hardware, dequantization and bottleneck.
6. **How is quantization released?** As a separately signed artifact with base/tokenizer/recipe/calibration/kernel manifest, paired quality/SLO gates, canary and high-precision rollback.
7. **How do retries work for streaming?** Retry only before partial delivery unless resumable protocol and consumer deduplication exist; propagate deadline and cap a fallback DAG.
8. **How do you size a fleet?** `ceil(peak rate / measured goodput per replica at complete SLO) + failure headroom` using a versioned workload replay.

## Primary References

- [Kubernetes Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/reference/spec/)
- [NVIDIA Dynamo KV-aware routing](https://docs.nvidia.com/dynamo/user-guides/kv-cache-aware-routing)
- [Hugging Face KV cache explanation](https://huggingface.co/docs/transformers/main/en/cache_explanation)
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching)
- [vLLM prefix caching](https://docs.vllm.ai/en/latest/design/prefix_caching/)
- [PagedAttention](https://arxiv.org/abs/2309.06180)
- [SGLang and RadixAttention](https://papers.nips.cc/paper_files/paper/2024/file/724be4472168f31ba1c9ac630f15dec8-Paper-Conference.pdf)
- [RouteLLM](https://arxiv.org/abs/2406.18665)
- [RouterBench](https://arxiv.org/abs/2403.12031)
- [Orca iteration-level scheduling](https://www.usenix.org/conference/osdi22/presentation/yu)
- [Hugging Face continuous batching](https://huggingface.co/docs/transformers/continuous_batching_architecture)
- [Sarathi-Serve chunked prefill](https://www.usenix.org/conference/osdi24/presentation/agrawal)
- [DistServe](https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin)
- [OpenAI Batch API](https://developers.openai.com/api/docs/guides/batch)
- [Hugging Face quantization concepts](https://huggingface.co/docs/transformers/quantization/concept_guide)
- [SmoothQuant](https://arxiv.org/abs/2211.10438)
- [GPTQ](https://arxiv.org/abs/2210.17323)
- [AWQ](https://arxiv.org/abs/2306.00978)
- [TensorRT-LLM quantization](https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html)
- [torchao inference quantization](https://docs.pytorch.org/ao/stable/workflows/inference.html)
- [MLPerf Inference](https://mlcommons.org/benchmarks/inference-datacenter/)
- [Safetensors security guidance](https://huggingface.co/docs/hub/security-pickle)
