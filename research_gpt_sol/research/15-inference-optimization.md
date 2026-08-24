# Research: Inference & Optimization — Caching, Routing, Batching, and Quantization

**Date researched**: 2026-08-21  
**Sources consulted**: 48

Inference optimization is not one technique. It is a coordinated attempt to remove four different forms of waste:

- **Caching** avoids work already performed: response generation, prompt prefill, or KV construction.
- **Routing** chooses the model, execution tier, region, or replica that best satisfies policy and service objectives.
- **Batching** combines compatible work so accelerator kernels operate at useful occupancy.
- **Quantization** represents weights, activations, or KV state with fewer bits so serving consumes less memory bandwidth and capacity.

These techniques interact. A quality router may select a cheaper model, then a replica router may select the worker holding the longest matching prefix. The worker continuously batches that request with unrelated decodes, reads quantized weights, and may store its KV state at another precision. Optimizing one layer in isolation can move the bottleneck: larger batches raise throughput but tail latency and KV pressure; aggressive cache affinity creates hot replicas; quantization makes weights smaller but may not accelerate an unsupported kernel; semantic response caching saves an entire call but can return an authorized, fluent, and wrong answer.

## 1. System Topology & Mechanics

### 1.1 End-to-end serving topology

```text
 client / agent / offline producer
              |
       auth, quota, deadline, idempotency
              |
       exact/semantic response cache -------- policy + data-version namespace
              | miss
       model-policy router ------------------ capability, quality, cost, region
              |
       inference gateway -------------------- admission, rate limit, fallback
              |
       replica / KV-aware router ------------ queue, adapter, prefix, health
          /         |          \
    worker A     worker B     worker C        continuous/in-flight batching
       |             |            |
       +---- paged GPU KV pools ---+          exact block reuse
                  |
          CPU / SSD / remote KV tier          optional offload and transfer
                  |
      model artifact + engine registry        model, tokenizer, quant recipe,
                                              kernels, calibration and eval IDs

 control plane: routes, model releases, cache policy, quantization policy,
 scheduler limits, autoscaling, SLOs, security policy, evaluation and rollback
```

Keep two routing planes separate `[inferred]`:

1. A **model-policy router** selects the logical model or cascade based on task, capability, risk, quality, price, latency, residency, and availability.
2. A **replica router** selects a physical endpoint for that model based on health, queue, active decode load, available KV blocks, prefix overlap, loaded adapter, hardware, and locality.

The Kubernetes Gateway API Inference Extension embodies this separation: an `InferenceModel` expresses the user-facing model mapping while an `InferencePool` represents the serving pool; an Endpoint Picker chooses a pod using inference-specific state [[1]](https://kubernetes.io/blog/2025/06/05/introducing-gateway-api-inference-extension/) [[2]](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/) [[48]](https://gateway-api-inference-extension.sigs.k8s.io/reference/spec/). NVIDIA Dynamo can put KV-aware selection either in its frontend or in a Gateway API Endpoint Picker Plugin (EPP); in both cases the selector tokenizes the request and evaluates worker/cache state before returning an endpoint [[3]](https://docs.nvidia.com/dynamo/user-guides/kv-cache-aware-routing).

### 1.2 Inference phases and bottlenecks

Autoregressive inference has two materially different phases:

- **Prefill/context processing:** process many prompt tokens in parallel, populate keys and values for every layer, then produce the first output token. It is commonly compute-intensive; prompt length, queue time, batching, and prefix reuse dominate time to first token (TTFT).
- **Decode:** repeatedly process one new token per active sequence while reading the growing KV state. It is commonly memory-bandwidth and synchronization sensitive; batch occupancy, sequence lengths, weight/KV precision, and scheduling dominate inter-token latency (ITL/TPOT).

The KV cache stores each layer's past keys and values so generation computes only the new token's K/V instead of recomputing the full history; attention per decode step becomes linear rather than quadratic in sequence length, while cache memory grows linearly [[4]](https://huggingface.co/docs/transformers/main/en/cache_explanation). The useful first-order capacity formulas are `[inferred from tensor shapes]`:

```text
weight_bytes ~= parameter_count * weight_bits / 8 + scales + zero_points

KV_bytes_per_token_per_sequence ~=
  2 * num_layers * num_kv_heads * head_dim * KV_bytes_per_element

KV_capacity_tokens ~= usable_KV_pool_bytes / KV_bytes_per_token_per_sequence
```

`2` represents K and V. Grouped-query or multi-query attention reduces `num_kv_heads`; tensor/pipeline parallelism changes the per-rank allocation. Activations, allocator overhead, temporary workspaces, CUDA graphs, logits, and fragmentation must be profiled rather than hidden inside this estimate. TensorRT-LLM identifies weights, activations, and I/O/KV tensors as the main inference allocations and sizes its paged KV pool from configured token or free-memory limits [[5]](https://nvidia.github.io/TensorRT-LLM/reference/memory.html).

### 1.3 Caching: four different contracts

| Cache | Key / match | Reused value | Correctness boundary | Typical benefit |
|---|---|---|---|---|
| Exact response | Canonical full request + versions | Final response | Exact deterministic-equivalent request | Avoids entire model/tool path |
| Semantic response | Embedding/reranker similarity + policy | Prior final response | Domain-specific equivalence, not string equality | More hits, higher false-hit risk |
| Provider prompt cache | Exact rendered prompt prefix | Provider-internal prefix state | Provider/model/project/retention contract | Lower prefill price and TTFT |
| Engine KV prefix cache | Token block hash chain | Layer KV tensors | Same model/tokenizer/adapter/position/precision/trust namespace | Skips matching prefill blocks |

**Exact response caching.** The key must include the canonical messages, system/developer prompt version, model and revision, decoding parameters, tools and schemas, structured-output schema, retrieval index/data version, locale, safety policy, authorization scope, and any other input that can alter the answer `[inferred]`. Store provenance and expiry with the value. Non-deterministic sampling does not make reuse automatically wrong, but reuse changes the product contract from “sample again” to “return this accepted sample.” Side-effecting agent/tool executions must not be replayed merely because the natural-language request matches.

**Semantic response caching.** Embed a normalized query, retrieve candidates, apply an intent/domain/policy-aware evaluator, and reuse only above a calibrated threshold. GPTCache illustrates the pipeline—embedding, vector search, similarity evaluation, scalar answer store, and eviction—and explicitly notes false-positive hits and false-negative misses [[6]](https://github.com/zilliztech/GPTCache). Namespace by tenant, permissions, language, product/data version, policy, and response class. Disable it for personalized, rapidly changing, security-sensitive, transactional, or high-stakes answers unless a verifier revalidates the cached response `[inferred]`.

**Provider prompt caching.** It normally reuses an exact stable prefix, not a final answer. Put stable instructions, examples, tool definitions, schemas, and common documents first and request-specific content last. Current provider contracts differ:

- OpenAI's current explicit cache for GPT-5.6-era models requires at least 1,024 rendered tokens through a breakpoint, reports `cached_tokens` and `cache_write_tokens`, uses a 30-minute refreshable lifetime, bills reads at 0.1x and writes at 1.25x uncached input, and warns that changing earlier tools, schemas, images, settings, or content breaks reuse [[7]](https://developers.openai.com/api/docs/guides/prompt-caching). Earlier supported models use exact automatic prefix matching with model-dependent in-memory or extended retention.
- Anthropic supports automatic or explicit prefix breakpoints, a default five-minute refreshable lifetime, and a one-hour option; current multipliers are 1.25x base input for five-minute writes, 2x for one-hour writes, and 0.1x for reads. Caches are isolated by organization and, on specified first-party platforms, workspace [[8]](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
- Gemini 2.5-and-newer models currently enable implicit caching by default. The documented minimum is 2,048 tokens for Gemini 2.5 Pro/Flash and 4,096 for listed Gemini 3.x models, with hits reported as `usage.total_cached_tokens` [[9]](https://ai.google.dev/gemini-api/docs/caching).

Treat these values as dated provider contracts, not universal properties. Cache hits are availability optimizations, never correctness or latency guarantees.

**Engine KV prefix caching.** vLLM hashes full token blocks using the parent-block hash, exact block tokens, and extra identity such as LoRA, multimodal input, and cache salt. It caches only full blocks; current vLLM defaults to SHA-256 and offers canonical-CBOR variants for reproducibility [[10]](https://docs.vllm.ai/en/latest/design/prefix_caching/). PagedAttention maps logical sequence blocks to non-contiguous physical KV blocks, reducing fragmentation and enabling reference-counted sharing [[11]](https://arxiv.org/abs/2309.06180). SGLang's RadixAttention represents reusable prefixes in a radix tree and uses cache-aware scheduling across structured multi-call programs [[12]](https://papers.nips.cc/paper_files/paper/2024/file/724be4472168f31ba1c9ac630f15dec8-Paper-Conference.pdf).

GPU KV is fastest and most scarce. LMCache demonstrates a separate KV management layer with GPU, CPU, local disk, and remote tiers; cross-instance sharing and persistence avoid fate-sharing with one engine but add lookup, serialization, transfer, consistency, and security work [[13]](https://docs.lmcache.ai/). TensorRT-LLM's KV connector similarly supports offload to CPU/NVMe/network storage and prefill/decode transfer [[14]](https://nvidia.github.io/TensorRT-LLM/features/kv-cache-connector.html). Promote only when transfer plus lookup is cheaper than recomputation, and record bytes moved, transfer latency, and useful-token hit rate `[inferred]`.

### 1.4 Routing algorithms

**Logical model routing options:**

- deterministic rules: capability, language, context length, tool/vision support, residency, tenant tier, safety class;
- complexity/classification router: query features or embeddings predict which model meets a quality threshold;
- cascade: call a cheap model first, then escalate when confidence, verifier, or policy fails;
- learned preference router: predict whether a strong model will outperform a weak model;
- contextual bandit: explore routes and update from delayed quality/cost feedback, with hard safety constraints;
- ensemble/oracle analysis: evaluate possible complementarity offline, not a deployable oracle.

RouteLLM trains matrix-factorization, BERT, causal-LLM, and similarity routers from preference data to choose between a stronger and weaker model; its reported “over 2x” cost reduction occurs only in some evaluated strong/weak-model and benchmark settings [[15]](https://arxiv.org/abs/2406.18665). RouterBench contains 405,467 recorded outcomes across 11 models, eight datasets, and 64 tasks, but found its simple predictive routers did not consistently beat a zero-router baseline and that cascade quality deteriorated rapidly as judge error rose [[16]](https://arxiv.org/abs/2403.12031). A 2026 unified re-evaluation likewise found many methods similar under a common setup and a persistent gap to the oracle [[17]](https://arxiv.org/abs/2601.07206). Therefore, train and gate routing on the application's own distribution, current model revisions, and business loss, not a public aggregate.

**Physical replica routing.** Least-request and round-robin ignore that one decode can occupy a worker much longer than another. Prefer a score combining expected prefill work after prefix reuse, active decode/KV load, queue age, adapter locality, health, and network transfer. Dynamo's documented router credits cached prefix blocks against prefill load and combines that with decode blocks; higher cache weight favors TTFT, while lower weight spreads decode load and tends to favor ITL [[18]](https://docs.nvidia.com/dynamo/v-0-9-0/user-guides/kv-cache-aware-routing). SGLang's cache-aware data-parallel policy estimates a radix tree per worker but falls back toward shortest queue when load imbalance crosses its threshold [[19]](https://sgl-project.github.io/advanced_features/dp_dpa_smg_guide.html).

Affinity must be bounded: exclude unhealthy or saturated workers first, then optimize locality among eligible endpoints. Use consistent hashing only as a soft hint; model version, LoRA adapter, tenant cache namespace, multimodal hash, and tokenizer revision belong in the affinity identity `[inferred]`.

### 1.5 Batching and scheduling

**Static/request batching** forms a fixed group and often pads to maximum shapes; short sequences wait for the longest. **Dynamic batching** waits briefly to collect compatible requests. **Continuous/in-flight batching** schedules at token-iteration granularity: completed requests leave and queued requests join while others decode. Orca introduced iteration-level scheduling and selective batching; in its historical GPT-3 175B evaluation, it reported 36.9x throughput over FasterTransformer at matched latency, a result tied to that 2022 system and comparison [[20]](https://www.usenix.org/conference/osdi22/presentation/yu).

Current Hugging Face continuous batching describes four request states—pending, prefilling, decoding, finished—and budgets each forward pass by tokens, cache pages, and request count. It uses a paged KV pool, admits requests only when cache capacity exists, and can reserve a safety margin so active decodes finish before new prefills are admitted [[21]](https://huggingface.co/docs/transformers/continuous_batching_architecture). TensorRT-LLM distinguishes a conservative `GUARANTEED_NO_EVICT` scheduler from `MAX_UTILIZATION`, which may pause requests when KV pressure peaks; `STATIC_BATCH` is documented as legacy [[22]](https://nvidia.github.io/TensorRT-LLM/latest/legacy/performance/performance-tuning-guide/useful-runtime-flags.html). Triton's TensorRT-LLM backend exposes in-flight batching, paged attention, scheduler policy and KV configuration through a serving layer rather than requiring an application to implement iteration scheduling itself [[44]](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tensorrtllm_backend/README.html). Hugging Face now describes Text Generation Inference as maintenance mode and points new deployments toward actively developed engines such as vLLM and SGLang; engine lifecycle is therefore an operational selection criterion, not only a benchmark result [[45]](https://huggingface.co/docs/text-generation-inference/index).

Long prefills can block interactive decodes. **Chunked prefill** splits a prompt into token-budgeted chunks and forms hybrid prefill/decode batches. Sarathi-Serve's stall-free algorithm schedules ongoing decodes plus at most controlled prefill work; the paper warns that chunks that are too small add kernel and repeated-KV-read overhead, while chunks that are too large hurt token latency [[23]](https://www.usenix.org/conference/osdi24/presentation/agrawal). **Prefill/decode disaggregation** allocates separate workers to the two phases and transfers KV between them. DistServe reported up to 7.4x more served requests or 12.6x tighter SLO than its evaluated baselines while keeping more than 90% of requests within specified latency constraints; that 2024 result spans particular models, applications, interconnects, and SLOs [[24]](https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin).

Do not confuse engine batching with a provider's **offline Batch API**. OpenAI's current Batch API accepts asynchronous JSONL jobs, returns results out of order keyed by `custom_id`, provides a 50% discount and a separate rate-limit pool, and targets completion within 24 hours [[25]](https://developers.openai.com/api/docs/guides/batch). It is appropriate for evals, classification, embeddings, and offline enrichment, not interactive TTFT.

### 1.6 Quantization mechanics

Affine quantization maps a high-precision value to an integer/code plus scale and optionally zero point. Per-tensor scales are cheap but expose every value to one range; per-channel, per-group, and per-block scales better isolate outliers at the cost of metadata and more complex kernels [[26]](https://huggingface.co/docs/transformers/quantization/concept_guide). Key choices are:

- **what:** weights only (`W4A16`, `W8A16`), weights and dynamic/static activations (`W8A8`, FP8), KV cache, or combinations;
- **when:** post-training quantization (PTQ), quantization-aware training (QAT), or quantized fine-tuning;
- **format:** symmetric/asymmetric INT, FP8 E4M3/E5M2, microscaling formats, vendor FP4, or codebooks;
- **granularity:** tensor, row/channel, group, or block;
- **calibration:** representative tokens determine ranges, transformations, or reconstruction objectives;
- **execution:** dequantize into higher-precision math or use a native low-precision kernel.

Representative methods solve different problems:

- **LLM.int8()** isolates activation outlier dimensions into 16-bit computation and executes more than 99.9% of values in 8-bit in its evaluated models [[27]](https://arxiv.org/abs/2208.07339).
- **SmoothQuant** moves activation outlier difficulty into weights through a mathematically equivalent scaling transform, enabling W8A8 PTQ; its paper reported up to 1.56x speed and 2x memory reduction on its tested engines/models [[28]](https://arxiv.org/abs/2211.10438).
- **GPTQ** uses approximate second-order information to reconstruct layers under one-shot low-bit weight quantization; its paper quantized a 175B model to 3/4-bit in about four GPU hours and reported hardware-specific end-to-end speedups [[29]](https://arxiv.org/abs/2210.17323).
- **AWQ** uses activation statistics to identify salient weight channels and equivalent scaling rather than mixed-precision storage; TinyChat's paper results exceeded 3x over its Hugging Face FP16 baseline on tested desktop/mobile GPUs [[30]](https://arxiv.org/abs/2306.00978).
- **FP8** defines E4M3 and E5M2 encodings with different precision/range trade-offs and was evaluated on training and post-training inference up to 175B parameters [[31]](https://arxiv.org/abs/2209.05433).

Algorithm names do not imply kernel support. Current TensorRT-LLM lists distinct recipes for FP4, several FP8 scaling modes, FP8/NVFP4 KV, and GPTQ/AWQ W4A8/W4A16, with materially different model and GPU-generation support matrices [[32]](https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html). Current torchao similarly marks some formats stable and others prototype and gives explicit device requirements [[33]](https://docs.pytorch.org/ao/stable/workflows/inference.html); its quantization architecture separates algorithm/flow, quantized tensor representation, primitive/kernel, and base dtype layers [[47]](https://docs.pytorch.org/ao/stable/contributing/quantization_overview.html). A valid checkpoint can be slower when dequantization, packing, small batches, unsupported operators, CPU fallbacks, or communication dominate.

## 2. Token Economics & NFR Metrics

### 2.1 Metrics and workload specification

Benchmark a distribution, not a single prompt. Record model and revision, tokenizer, engine/commit, quantization recipe, kernel/backend, hardware/driver, tensor/pipeline/data parallelism, replicas, context/output distributions, concurrency/arrival process, streaming, cache-warm state, prefix-sharing distribution, adapters, and quality suite `[inferred]`.

Core metrics:

```text
TTFT = first content token received - request submitted
ITL  = (final response time - first token time) / (output_tokens - 1)
E2E  = final response received - request submitted

request_throughput = completed requests / wall time
output_token_throughput = total output tokens / wall time
goodput(SLO) = requests meeting all TTFT, ITL, E2E and quality constraints / wall time

cache_token_hit_rate = reused eligible tokens / eligible input tokens
cache_request_hit_rate = requests with any useful hit / eligible requests
false_semantic_hit_rate = reused responses later judged non-equivalent / semantic hits
route_regret = chosen-route loss - best allowed-route loss
```

NVIDIA's AIPerf definition confirms that TTFT includes network, queue, prefill, and first-token delivery and defines ITL without the first token; it warns tools differ, so metric definitions must align before comparison [[34]](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html). GenAI-Perf reports TTFT, ITL, request latency, sequence lengths, request throughput, and output-token throughput at multiple percentiles [[35]](https://docs.nvidia.com/deeplearning/triton-inference-server/archives/triton-inference-server-2520/user-guide/docs/perf_analyzer/genai-perf/README.html). vLLM's metrics design collects scheduler queue state plus TTFT and per-iteration TPOT/ITL observations [[46]](https://docs.vllm.ai/en/v0.14.0/design/metrics/). Add p50/p95/p99 queue, prefill, decode, cache lookup/transfer, router decision, tokens/sec/accelerator, useful KV bytes/GB, accelerator utilization/power, OOM/preemption, quality delta, and cost per accepted outcome `[inferred]`.

MLPerf Inference provides controlled system benchmarks and official rules, but it does not replace a workload replay with the application's own arrival, prefix, output, and quality distributions [[36]](https://mlcommons.org/benchmarks/inference-datacenter/).

> ⚠️ Limited public data available for this dimension. Providers and enterprises rarely publish reproducible production p95/p99 latency, cache-hit distributions, accelerator utilization, quality loss, and fully allocated cost for the same current model, hardware, engine, and workload. Cross-vendor “tokens/sec” numbers without this context are not decision-grade.

### 2.2 Cost model and cache break-even

Per-request variable cost `[inferred]`:

```text
C_request =
  uncached_input_tokens * P_input
  + cache_write_tokens * P_write
  + cache_read_tokens * P_read
  + output_tokens * P_output
  + router_tokens_or_compute
  + cache_lookup_embedding_rerank
  + retrieval_tool_network_cost
  + allocated_self_hosted_accelerator_time

C_per_1k_executions = 1000 * mean(C_request)
                      + amortized fixed cache/router/serving cost for that volume
```

For a cached prefix of `T` tokens with base input price `P`, write multiplier `w`, read multiplier `r`, and `n` later reads:

```text
uncached cost = (n + 1) * T * P
cached cost   = w * T * P + n * r * T * P
cache pays when n > (w - 1) / (1 - r)
```

With the current OpenAI/Anthropic five-minute-style multipliers `w=1.25`, `r=0.1`, one successful later read already crosses the arithmetic break-even; Anthropic's one-hour `w=2` crosses after more than 1.11 reads, i.e. two whole reads. This ignores TTL expiry, minimum length, capacity eviction, routing misses, and changed prefixes. Use measured `cached_tokens`/`cache_write_tokens`, not request-level hit count. OpenAI warns cached input still counts toward TPM on earlier automatic-caching models and does not guarantee identical output [[7]](https://developers.openai.com/api/docs/guides/prompt-caching).

An application response cache avoids input and output generation but incurs lookup, storage, invalidation, evaluation, and wrong-hit loss. Its economic objective is `[inferred]`:

```text
net_value = avoided_generation_cost_and_latency
            - cache_infrastructure_cost
            - stale_or_false_hit_expected_loss
```

### 2.3 Routing objective

A constrained router should minimize expected cost/latency subject to policy and quality, rather than collapse everything into an uncalibrated score `[inferred]`:

```text
eligible = models satisfying capability, residency, policy, context and availability

choose m in eligible minimizing:
  E[cost_m] + lambda_l * E[latency_penalty_m] + lambda_f * E[failure_loss_m]
subject to:
  P(quality_m >= target | x) >= target_confidence
  tenant_budget_remaining >= worst_case_cost_m
```

Measure quality/cost Pareto fronts, route share by cohort, escalation and fallback rate, classifier calibration, out-of-distribution rate, policy violations, route churn, and regret against an offline oracle. Include router overhead in TTFT. A cascade can spend more than direct strong-model routing if it routinely calls both models; cap total attempts and pass forward useful state only when provider and model semantics permit `[inferred]`.

### 2.4 Batching economics

Batch size is an outcome of token/KV budgets and arrivals, not a universal constant. Increasing the per-step token budget typically improves accelerator occupancy and prefill throughput until memory, queueing, or kernel shape dominates. Current Hugging Face documentation explicitly notes that `max_batch_tokens` competes with KV blocks for the same GPU memory, while `max_requests_per_batch` bounds vocabulary-sized logits temporaries [[37]](https://huggingface.co/docs/transformers/continuous_batching).

Tune against an SLO-constrained load curve:

1. sweep offered request rate/concurrency and input/output distributions;
2. measure p50/p95/p99 TTFT, ITL, E2E, goodput, queue, cache pressure, power, and quality;
3. vary max batched tokens, max sequences, chunk size, scheduler policy, memory reservation, and batch wait;
4. select the highest goodput region that preserves headroom under burst and failure `[inferred]`.

Offline provider batching changes price and quota but exchanges interactive latency for a 24-hour completion contract. The current OpenAI limit is 50,000 requests and 200 MB per batch file, plus model-specific queued-token limits; expired unfinished items are canceled while completed items remain billable [[25]](https://developers.openai.com/api/docs/guides/batch).

### 2.5 Quantization economics and validation

Ideal raw weight storage is `N*b/8`, so 4-bit storage is one quarter of a 16-bit baseline before scales, zero points, padding, packing, embeddings kept at higher precision, and duplicated ranks. Memory saved may allow a model on fewer accelerators, larger KV pools, more replicas, or higher concurrency. Native low-bit tensor cores can also raise arithmetic throughput. However, decode may be memory-bound while prefill is compute-bound, so weight-only `W4A16` and weight-activation `W8A8/FP8` optimize different regimes `[inferred]`.

For every quantized artifact, compare with the exact higher-precision baseline on:

- task success, safety and domain metrics, not only perplexity;
- languages, long context, structured/tool output, rare tokens, math/code, and adversarial inputs;
- logits or KL drift where available, plus answer-level paired significance;
- TTFT, ITL, goodput, peak/resident memory, power, and cost at the intended concurrency;
- engine/hardware compatibility and cold model-load/build time.

Do not quote GPTQ, AWQ, SmoothQuant, or vLLM paper speedups as a forecast. Each is a result for its paper's models, precisions, kernels, hardware, sequence distributions, and baselines. Re-run the same artifact and kernel in the target environment.

## 3. Distributed Resilience & State

### 3.1 State ownership

| State | Durable authority | Rebuild / recovery rule `[inferred]` |
|---|---|---|
| Model route and policy | versioned config store/Git + signed release | fail closed for policy; last-known-good for nonsecurity tuning |
| Model/quant artifact | immutable registry/object store | digest-pin model, tokenizer, config, recipe and kernels |
| GPU KV blocks | individual worker | disposable; never sole business state |
| Tiered KV metadata | cache controller/index | reconcile from block reports or discard stale entries |
| Response cache | tenant-aware cache + source versions | invalidate by namespace/version; tolerate miss, not wrong hit |
| Online batch queue | durable log/queue | idempotent claim, lease, retry, deadline and dead-letter |
| Provider batch manifest | application job ledger | reconcile each `custom_id`; never infer completion from order |
| Router feedback/evals | append-only event store | train from time-bounded, policy-filtered, quality-labelled data |

LMCache's current architecture makes KV management engine-independent so cache need not die with one worker and exposes controller operations such as lookup, clear and move [[13]](https://docs.lmcache.ai/) [[38]](https://docs.lmcache.ai/kv_cache_management/index.html). Dynamo's event-driven router can persist cache-state snapshots and replay KV events; its approximate mode predicts state with TTL/pruning but does not preserve that state across restart [[18]](https://docs.nvidia.com/dynamo/v-0-9-0/user-guides/kv-cache-aware-routing). Cache metadata is still a hint: after lost, delayed, duplicated, or reordered events, verify a block at the worker or fall back to recompute rather than return incorrect tensors `[inferred]`.

### 3.2 Request lifecycle, retries, and cancellation

Assign a globally unique request ID, tenant, deadline, route decision ID, model/artifact version, and idempotency key before admission. Propagate the deadline through router, queue, engine, and stream. Cancellation must remove the queued item, stop further decode at a scheduler boundary, release KV references and reservations, and close the stream. Track “client disconnected but compute continued” as wasted tokens `[inferred]`.

Retry only retryable transport/capacity failures within the remaining deadline and attempt budget. A retry may select another replica and lose local prefix reuse. Do not retry a partially delivered stream transparently unless the protocol supports resume and the consumer can deduplicate. Do not retry side effects around a model call without a tool-level idempotency key. For offline batches, ledger each item independently because output order is not input order and a batch can be partially complete when it expires [[25]](https://developers.openai.com/api/docs/guides/batch).

### 3.3 Admission, backpressure, fairness, and failover

Use hierarchical admission `[inferred]`:

- gateway: per-tenant requests, input tokens, concurrent streams, and cost budget;
- model pool: context-length, adapter, modality, and SLO class;
- replica: max sequences, max batched tokens, KV blocks, workspace, and queue-delay forecast;
- cache tiers: bytes, bandwidth, IOPS, tenant quota, and promotion budget.

Reject or degrade before GPU OOM: return a retry hint, route to a larger-context pool, lower max output, switch to an allowed smaller model, defer to offline processing, or shed low-priority work. Preserve premium/interactive capacity with separate queues or weighted fair scheduling. The Virtual Token Counter research defines service in input/output token work and proves a 2x bound on service difference for two backlogged clients under its model, showing why request-count fairness is inadequate for heterogeneous prompts [[39]](https://www.usenix.org/conference/osdi24/presentation/sheng).

Use outlier ejection and circuit breakers per provider/model/region/replica. A half-open probe should be small and uncached so a cache hit does not falsely certify failed inference compute `[inferred]`. Keep route fallback acyclic and finite:

```text
primary local quantized -> local high precision -> approved external provider
                       -> asynchronous queue / explicit failure
```

Avoid changing model or quantization silently after partial generation. Record the actual served model and precision in internal response metadata.

### 3.4 Deployment and mixed-version safety

Roll out one axis at a time: engine, model weights, tokenizer/template, quant recipe, kernel/driver, scheduler, or cache key. Shadow new routes, replay a stratified workload, canary by tenant/risk, then expand using quality and SLO gates `[inferred]`. A cache namespace must change when any artifact changes the computed state. Never share KV blocks across incompatible model weights, adapters, tokenizer renderings, positional encodings, RoPE scaling, KV precision, or engine serialization.

Warm model weights and representative stable prefixes before taking traffic only when the provider/engine contract supports it. Cap warmups so a fleet restart does not create a thundering herd. Maintain a high-precision fallback artifact and tested rollback; a quantized release is a distinct model release, not a flag on the same release `[inferred]`.

## 4. Enterprise Security & Governance

### 4.1 Cache isolation, authorization, and leakage

Cache lookup occurs after authentication and policy context construction. Include a pseudonymous tenant/principal or explicit sharing group in cache namespaces; include document ACL/version in RAG response keys. Encrypt durable cache data and transport, restrict operator access, audit clear/export actions, and apply retention/deletion to cached prompts, responses, embeddings, and KV tiers `[inferred]`.

Shared KV caches expose timing information because a hit changes TTFT. Current vLLM supports per-request `cache_salt`, injected into the first block hash so only matching trust groups can reuse blocks; its docs explicitly present this as protection against timing inference [[10]](https://docs.vllm.ai/en/latest/design/prefix_caching/). Use a server-derived HMAC of tenant/sharing-group identity, not an attacker-chosen global salt `[inferred]`. Keep cryptographic hashing in multi-tenant deployments: vLLM warns faster non-cryptographic hashes increase collision and private-data risk.

Provider cache isolation and retention are part of data governance. OpenAI states prompt caches are not shared between organizations and links retention to model and data-control policy [[7]](https://developers.openai.com/api/docs/guides/prompt-caching). Anthropic currently documents organization and platform-dependent workspace isolation and in-memory KV/hash handling [[8]](https://platform.claude.com/docs/en/build-with-claude/prompt-caching). Verify the exact provider, region, zero-retention eligibility, residency, and contract; do not assume one vendor's cache semantics apply through every cloud reseller.

### 4.2 Semantic cache poisoning and unsafe reuse

An attacker can attempt to populate a semantic cache with a response that is close in embedding space to a victim's later query. A high similarity threshold alone cannot prove answer equivalence, authorization, freshness, or policy compatibility. Defenses `[inferred]`:

- cache only verified, policy-compliant responses from authenticated producers;
- namespace by tenant, role, data/prompt/model/policy versions and locale;
- exclude user identity, mutable facts, tool actions, secrets, and security decisions from semantic reuse;
- retrieve several candidates, apply a cross-encoder or task-specific equivalence check, and require answer provenance;
- use short TTL/event invalidation for mutable data and revalidate source versions on hit;
- monitor write identities, unusual embedding neighborhoods, hit-to-writer relationships, false-hit adjudications, and hot-key amplification;
- return a miss when evidence is ambiguous.

### 4.3 Routing governance

The eligibility filter must precede learned optimization. A router cannot trade away residency, data-processing terms, minimum safety, approved model list, context retention, required tools, accessibility, or auditability for price `[inferred]`. Version every route policy and record input feature hashes, eligible set, selected model/replica, reason codes, score/calibration, fallback, actual model, cost, latency, and outcome label.

Adversaries can manipulate complexity keywords to force expensive routes or target weak models. Apply per-tenant budgets and maximum route tier, normalize untrusted metadata, detect out-of-distribution inputs, and force high-risk domains to an approved fixed route or verified cascade. Do not use sensitive protected attributes directly; test route quality and latency by language, region, accessibility, and customer cohort to detect disparate service `[inferred]`.

### 4.4 Batch and scheduler isolation

Enforce authenticated quotas before queue insertion, bound prompt/output size, and use weighted fairness so one tenant cannot exhaust KV memory with long contexts or starve others with high-priority flags. Never accept client-supplied scheduler priority without authorization. Separate interactive and offline queues/pools when practical; bulkhead different risk/residency classes. Logs and batch manifests contain prompts and outputs, so apply least privilege, encryption, retention, DLP, and item-level lineage `[inferred]`.

### 4.5 Quantized artifact supply chain

A quantized checkpoint bundles a transformation and often custom runtime code. Record an AI/ML bill of materials containing base model digest/license, tokenizer/template, quantizer and version, calibration dataset lineage/approval, recipe, scales, excluded layers, engine/compiler, kernel, container, driver/CUDA requirements, evaluation report, signer, and release approval `[inferred]`. Pin immutable revisions and verify signatures/digests in admission.

Prefer tensor-only formats. Hugging Face warns Python pickle can execute arbitrary code and recommends trusted sources, signed commits, scanning, and safer formats [[40]](https://huggingface.co/docs/hub/security-pickle). Current Transformers preferentially loads Safetensors when available and recommends pinning a revision for custom models [[41]](https://huggingface.co/docs/transformers/models). Safetensors reduces deserialization code-execution risk but does not prove that weights are benign, correctly quantized, licensed, or high quality. OWASP's LLM supply-chain guidance includes model provenance, integrity, vendor vetting, component inventory, and patching [[42]](https://genai.owasp.org/llmrisk/llm032025-supply-chain/).

## 5. Production Failure Modes

### 5.1 Failure matrix

| Failure | Signal | Mitigation `[inferred]` |
|---|---|---|
| Stable prefix placed after volatile content | writes high, reads low | canonical prompt builder; stable-first layout; breakpoint tests |
| Cache key omits model/data/policy/ACL | fluent stale or cross-scope answer | versioned complete key; authorization before lookup; purge |
| Semantic false hit | fast answer, task/grounding failure | stricter evaluator; domain disablement; provenance revalidation |
| Cache stampede after expiry/restart | prefill spike, queue and TTFT surge | single-flight, jittered TTL, bounded prewarm, stale-while-revalidate only when safe |
| Hot prefix pins traffic to one worker | high hit rate but bad ITL/queue | load-bounded affinity; replicate/offload hot KV; spill to recompute |
| Stale/lost KV events | router predicts blocks absent/present | event offsets/snapshots; worker verification; TTL; load-only fallback |
| Hash or identity collision | incorrect reuse/data leak | cryptographic hash; include model/adapter/multimodal/salt identity |
| GPU KV fragmentation/exhaustion | preemption, rejection, OOM | paged allocation; reserve; admission; cap output/context; offload |
| Offload slower than recompute | cache hit raises TTFT | compare transfer vs prefill estimate; bandwidth admission; tier bypass |
| Long prefill blocks decode | ITL spikes with long prompts | chunked prefill; separate SLO classes; P/D disaggregation |
| Batch too small | low utilization, high unit cost | continuous batching; short bounded batch wait; consolidate replicas |
| Batch too large | p99 TTFT/ITL, OOM | token/KV budgets; safety margin; SLO-aware admission |
| FCFS tenant starvation | old/large jobs dominate | token-cost fairness, aging, quotas, reserved queues |
| Cancellation leaks work/KV | tokens after disconnect, memory creep | scheduler-boundary cancel; reference cleanup; leak tests |
| Quality router distribution shift | route regret, escalation increase | OOD detector; fixed safe default; recent labelled replay; rollback |
| Router fallback loop | repeated calls and cost explosion | directed acyclic fallback; attempt/deadline/cost cap |
| Cache-aware route overload | low TTFT, high ITL/p99 | combine overlap with active decode/queue cost; temperature/spill |
| Quant calibration mismatch | domain/language/long-context regression | representative calibration and stratified eval; retain sensitive layers |
| Unsupported low-bit kernel | slower service or CPU fallback | startup capability assertion; kernel-level and E2E profile |
| Quant scale/format mismatch | corrupt outputs/NaNs/crash | manifest validation; checksum; golden-vector startup test |
| Quantized KV accumulates error | long-context degradation | long-sequence eval; higher KV precision; per-model support gate |
| Mixed artifact fleet | nondeterministic quality/cache misses | immutable release tuple; cache namespace per tuple; drain old workers |
| Provider batch partial expiry | missing records or duplicate retry | item ledger by `custom_id`; retry only unfinished; reconcile charges |
| Metrics average hides tail | benchmark passes, users fail | percentiles and goodput by prompt/output/tenant cohort |

### 5.2 Diagnosing by symptom

**TTFT regression:** decompose gateway, router, queue, cache lookup/transfer, tokenization, prefill, and first-token delivery. Check prefix-token hit rate, not only request hit rate; cache-affinity imbalance; long-prompt arrivals; batch token budget; cold artifacts; and network tier `[inferred]`.

**ITL regression:** check active decode batch, sequence-length mix, KV utilization/offload, prefill interference, quantized-kernel occupancy, collective/network stalls, and pause/preemption. A high cache hit can improve TTFT while worsening ITL by concentrating decode work.

**Throughput gain with quality loss:** compare served model route and quant artifact to baseline, separate routing regret from quantization drift, and inspect semantic cache false hits. Optimization metadata must make these three causes distinguishable.

**OOM despite smaller weights:** quantization freed weight memory but the scheduler consumed it with larger KV capacity or temporary logits/activation workspaces. Measure peak allocation by component and lower max batched tokens/sequences or KV fraction before blaming allocator fragmentation `[inferred]`.

### 5.3 Published incidents and evidence limits

Public inference-system literature primarily reports controlled benchmarks, not enterprise post-mortems. The strongest actionable failure evidence therefore comes from engine documentation: TensorRT-LLM documents that maximizing utilization can pause requests under KV pressure [[22]](https://nvidia.github.io/TensorRT-LLM/latest/legacy/performance/performance-tuning-guide/useful-runtime-flags.html); its KV-reuse guide documents full-block requirements, LRU eviction, reduced reuse under large batches/outputs, and host-transfer trade-offs [[43]](https://nvidia.github.io/TensorRT-LLM/0.18.2/advanced/kv-cache-reuse.html); GPTCache acknowledges false semantic hits and line-count-based eviction that can misestimate memory [[6]](https://github.com/zilliztech/GPTCache).

> ⚠️ Limited public data available for this dimension. Major providers rarely publish incident reports that isolate cache corruption, routing error, batching starvation, or quantization drift with customer impact and recovery timelines. Treat undocumented incident-rate claims as speculation.

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A: multi-tenant interactive RAG/agent service

**Requirements:** streaming chat, p95 TTFT/ITL objectives, tenant isolation, repeated system/tool prefixes, long retrieved context, bursty traffic, and occasional side effects.

**Design `[inferred]`:**

1. Gateway authenticates, applies tenant token/concurrency/cost quotas, assigns deadline and trace/request IDs.
2. Exact response cache is enabled only for read-only, versioned FAQ-like flows. Semantic cache is limited to low-risk intents and requires tenant/data/policy namespaces plus equivalence verification.
3. Rules first constrain model eligibility; a calibrated router selects small or strong model. High-risk/tool-write intents use the approved strong route.
4. Replica selector uses model/adapter compatibility, health, queue, active decode and salted prefix overlap. Cache affinity cannot override saturation.
5. Engine uses paged KV, continuous batching, chunked prefill, conservative no-evict admission, and per-tenant fairness. Stable system/tool schemas precede retrieved/user-variable content.
6. GPU KV is tenant-salted; a CPU tier stores only approved cache classes. Side-effecting tool results and secrets are excluded from shared response caches.
7. Quantized candidate runs only after task/tool/schema/safety/long-context parity; high precision remains the rollback route.

Track task success, tool correctness, p50/p95/p99 TTFT/ITL/E2E, goodput, route regret, semantic false hits, cached tokens, useful KV bytes, queue age by tenant, preemption/OOM, cost per successful task, and wasted post-cancel tokens.

### 6.2 Scenario B: offline evaluation and document enrichment

**Requirements:** millions of independent records, completion in hours, restartability, controlled cost, no interactive TTFT.

**Design `[inferred]`:** durable manifest partitions work by model/prompt/data version and assigns an idempotent record ID. Use provider asynchronous Batch API when its residency/retention and 24-hour contract fit; otherwise use a queue feeding large throughput-optimized continuous batches. Group stable prefixes without violating tenant boundaries. Persist per-item status and output digest; reconcile success/error/expired by ID and retry only absent/retryable items. Quantize only after paired quality validation; route low-complexity records to a cheaper model with a sampled strong-model audit.

Offline does not mean unbounded. Cap queued tokens, file size, output length, per-record attempts, total spend, and completion deadline. Sample and verify output before releasing the entire downstream dataset.

### 6.3 Scenario C: regulated on-prem inference

**Requirements:** no external prompt processing, strict data deletion, auditable artifacts, predictable degradation, heterogeneous accelerators.

**Design `[inferred]`:** deploy an inference gateway with explicit `InferenceModel`/pool mappings; use signed immutable model and quant artifacts; disable remote code; load Safetensors; enforce tenant-derived cache salts and encrypted local tiers. Separate control-plane route policy from data-plane selection. Use guaranteed-no-evict scheduling for critical traffic, a distinct offline pool, and a high-precision fallback artifact. Do not use semantic caching for clinical/legal/financial decisions unless an approved verifier rechecks current authorized sources. Record artifact/route/cache decision metadata without raw sensitive prompts in ordinary metrics.

### 6.4 Trade-off matrix

| Technique | Best fit | Primary gain | Main cost/risk | Required gate |
|---|---|---|---|---|
| Exact response cache | repeatable read-only deterministic-equivalent work | avoids full call | stale/incomplete key | canonical key + version/ACL tests |
| Semantic response cache | low-risk paraphrased FAQs/search | more whole-call hits | false hit/poisoning | domain equivalence + freshness eval |
| Provider prompt cache | stable long prefixes on hosted APIs | input price and TTFT | TTL/minimum/provider lock-in | token read/write telemetry |
| Local KV prefix cache | repeated system/history/RAG prefixes | prefill and TTFT | GPU capacity/leakage/affinity | cryptographic tenant namespace |
| Tiered/distributed KV | long prefixes across workers/restarts | reuse beyond one GPU | transfer/control-plane complexity | transfer-vs-recompute load test |
| Rules router | clear capability/policy boundaries | predictable cost/safety | coarse optimization | policy tests and audit reasons |
| Learned router/cascade | model complementarity and labelled traffic | cost-quality frontier | drift, judge error, double calls | OOD/default route + regret eval |
| Continuous batching | online mixed-length traffic | utilization/throughput | queue and KV contention | p99 goodput load curve |
| Chunked prefill | long prompts plus interactive decode | controls ITL stalls | repeated KV reads/kernel overhead | chunk sweep under target SLO |
| P/D disaggregation | large steady fleet with phase interference | independent phase scaling | KV transfer and more services | interconnect-aware benchmark |
| Offline provider batch | delay-tolerant bulk work | discount/separate quota | 24h/partial expiry/data retention | item ledger and deadline budget |
| Weight-only INT4 | memory-bound decode/model fit | lower HBM and bandwidth | calibration/kernel/quality | exact target-engine benchmark |
| W8A8/FP8/FP4 | supported compute-bound and mixed workloads | memory plus native compute | hardware/format specificity | operator coverage + quality suite |
| Quantized KV | KV-constrained long/concurrent serving | more cache tokens | long-context accuracy | long-sequence task eval |

### 6.5 Capacity and rollout method

1. **Specify workload:** hourly/peak arrivals, burst, prompt/output percentiles and joint distribution, prefix groups, cache interarrival, model/adapter mix, streaming, tenant/SLO/risk classes.
2. **Establish baseline:** high-precision artifact, caching off/cold, fixed router, conservative scheduler; measure quality, TTFT/ITL/E2E, memory, power, and goodput.
3. **Size memory:** weights + scales + activations/workspaces + KV pool + graph/runtime reserve. Validate with engine measurements, not formula alone.
4. **Add exact reuse:** provider/local prefix caching first; observe write/read tokens, block/token hits, eviction, and tenant isolation. Add semantic caching only with a labelled false-hit evaluation.
5. **Tune batching:** sweep token/sequence budgets, batch wait, chunk size, cache fraction, and scheduler. Keep failure and burst headroom.
6. **Add routing:** hard eligibility first, then replica load/cache awareness, then learned model routing. Replay current labelled traffic and report regret by cohort.
7. **Quantize last:** generate an immutable artifact, assert native kernel coverage, evaluate quality/safety/long context, and benchmark at target concurrency.
8. **Canary and rollback:** shadow, 1% canary, progressive expansion; gate on task quality, safety, p99 goodput, OOM/preemption, cache isolation, and cost per accepted outcome `[inferred]`.

Capacity estimate `[inferred]`:

```text
required_replicas = ceil(peak_offered_rate / measured_goodput_per_replica_at_SLO)
                    + failure_headroom

effective_cost_per_success =
  (accelerator + provider + cache + network + storage + router + failed/retried work)
  / policy_compliant_successes
```

Do not size from peak tokens/sec alone. Use **goodput at the complete SLO and quality target**, then test loss of a replica/AZ, cold cache, router-state loss, quantized-artifact rollback, and provider throttling.

> ⚠️ Limited public data available for this dimension. There is no portable public capacity table mapping current model/quantization/engine combinations to production replicas or cost because output length, prefix locality, SLO, hardware, kernels, power, utilization, and failure headroom dominate. The defensible architecture produces this table from a versioned workload replay.

## Sources

- [1] https://kubernetes.io/blog/2025/06/05/introducing-gateway-api-inference-extension/ — Kubernetes inference-aware routing and control resources.
- [2] https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/ — Current InferencePool and Endpoint Picker contract.
- [3] https://docs.nvidia.com/dynamo/user-guides/kv-cache-aware-routing — Dynamo Kubernetes KV-routing topologies.
- [4] https://huggingface.co/docs/transformers/main/en/cache_explanation — Transformer KV-cache computation mechanics.
- [5] https://nvidia.github.io/TensorRT-LLM/reference/memory.html — TensorRT-LLM weight, activation, I/O and KV memory model.
- [6] https://github.com/zilliztech/GPTCache — Semantic cache architecture, metrics and limitations.
- [7] https://developers.openai.com/api/docs/guides/prompt-caching — Current OpenAI exact-prefix caching, pricing, telemetry and retention.
- [8] https://platform.claude.com/docs/en/build-with-claude/prompt-caching — Current Anthropic caching mechanics, TTL, isolation and pricing.
- [9] https://ai.google.dev/gemini-api/docs/caching — Current Gemini implicit context caching.
- [10] https://docs.vllm.ai/en/latest/design/prefix_caching/ — vLLM block hashing, full-block reuse and cache salting.
- [11] https://arxiv.org/abs/2309.06180 — PagedAttention and vLLM serving paper.
- [12] https://papers.nips.cc/paper_files/paper/2024/file/724be4472168f31ba1c9ac630f15dec8-Paper-Conference.pdf — SGLang/RadixAttention paper.
- [13] https://docs.lmcache.ai/ — LMCache engine-independent, tiered KV-cache architecture.
- [14] https://nvidia.github.io/TensorRT-LLM/features/kv-cache-connector.html — TensorRT-LLM external KV connector and transfer uses.
- [15] https://arxiv.org/abs/2406.18665 — RouteLLM learned strong/weak model routing.
- [16] https://arxiv.org/abs/2403.12031 — RouterBench data, metrics and routing limitations.
- [17] https://arxiv.org/abs/2601.07206 — 2026 unified LLM routing benchmark.
- [18] https://docs.nvidia.com/dynamo/v-0-9-0/user-guides/kv-cache-aware-routing — Dynamo KV/load score, state events and persistence.
- [19] https://sgl-project.github.io/advanced_features/dp_dpa_smg_guide.html — SGLang cache-aware data-parallel routing.
- [20] https://www.usenix.org/conference/osdi22/presentation/yu — Orca iteration-level scheduling paper.
- [21] https://huggingface.co/docs/transformers/continuous_batching_architecture — Continuous batching request, scheduler and paged-memory mechanics.
- [22] https://nvidia.github.io/TensorRT-LLM/latest/legacy/performance/performance-tuning-guide/useful-runtime-flags.html — TensorRT-LLM capacity scheduler trade-offs.
- [23] https://www.usenix.org/conference/osdi24/presentation/agrawal — Sarathi-Serve chunked prefill and stall-free batching.
- [24] https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin — DistServe prefill/decode disaggregation and scoped results.
- [25] https://developers.openai.com/api/docs/guides/batch — Current OpenAI Batch API behavior, limits and economics.
- [26] https://huggingface.co/docs/transformers/quantization/concept_guide — Quantization granularity, PTQ and QAT concepts.
- [27] https://arxiv.org/abs/2208.07339 — LLM.int8() outlier-aware mixed precision.
- [28] https://arxiv.org/abs/2211.10438 — SmoothQuant W8A8 post-training quantization.
- [29] https://arxiv.org/abs/2210.17323 — GPTQ second-order weight quantization.
- [30] https://arxiv.org/abs/2306.00978 — AWQ activation-aware weight quantization.
- [31] https://arxiv.org/abs/2209.05433 — FP8 E4M3/E5M2 formats.
- [32] https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html — Current TensorRT-LLM quantization recipes and support matrices.
- [33] https://docs.pytorch.org/ao/stable/workflows/inference.html — Current torchao inference quantization workflows and device requirements.
- [34] https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html — TTFT, ITL and throughput definitions.
- [35] https://docs.nvidia.com/deeplearning/triton-inference-server/archives/triton-inference-server-2520/user-guide/docs/perf_analyzer/genai-perf/README.html — GenAI-Perf metric definitions.
- [36] https://mlcommons.org/benchmarks/inference-datacenter/ — MLPerf Inference datacenter benchmark scope and rules.
- [37] https://huggingface.co/docs/transformers/continuous_batching — Current continuous-batching configuration trade-offs.
- [38] https://docs.lmcache.ai/kv_cache_management/index.html — LMCache controller, worker and cache operations.
- [39] https://www.usenix.org/conference/osdi24/presentation/sheng — Virtual Token Counter fairness research.
- [40] https://huggingface.co/docs/hub/security-pickle — Pickle model-artifact execution risk and scanning.
- [41] https://huggingface.co/docs/transformers/models — Secure model-loading and revision-pinning guidance.
- [42] https://genai.owasp.org/llmrisk/llm032025-supply-chain/ — OWASP LLM supply-chain risk and mitigations.
- [43] https://nvidia.github.io/TensorRT-LLM/0.18.2/advanced/kv-cache-reuse.html — KV block reuse, eviction, block size and host offload trade-offs.
- [44] https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tensorrtllm_backend/README.html — Triton TensorRT-LLM backend, in-flight batching and serving controls.
- [45] https://huggingface.co/docs/text-generation-inference/index — TGI optimization capabilities and current maintenance status.
- [46] https://docs.vllm.ai/en/v0.14.0/design/metrics/ — vLLM scheduler, TTFT and TPOT metric collection design.
- [47] https://docs.pytorch.org/ao/stable/contributing/quantization_overview.html — Quantized tensors, kernels and algorithm-stack concepts.
- [48] https://gateway-api-inference-extension.sigs.k8s.io/reference/spec/ — Current Gateway API Inference Extension v1 API reference.
