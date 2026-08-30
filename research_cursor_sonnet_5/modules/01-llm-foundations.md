# 01. LLM Foundations

**Sub-areas covered**: transformer internals (attention / KV cache / MoE / positional encoding) · reasoning models (CoT, test-time compute, o1/o3/R1) · function calling (schemas, parallel calls, native vs. prompted) · structured output (JSON mode, grammar-constrained decoding, schema validation)

---

## 1. System Topology & Data Flow

An enterprise LLM foundation is not "a model behind an API" — it is a serving system with a control plane that makes policy/routing decisions and a data plane that does the compute-heavy token generation, wired together with a persistence layer, tool proxies for function calling, and telemetry sinks for cost/latency/safety observability.

```
                                   ┌───────────────────────────────────────────────────────┐
                                   │                    CONTROL PLANE                        │
                                   │                                                          │
  ┌──────────┐   HTTPS/gRPC        │  ┌──────────────┐   ┌───────────────┐   ┌────────────┐  │
  │  Client  │────────────────────▶│  │  AuthN/AuthZ │──▶│ Model Router / │──▶│  Rate      │  │
  │ (app/    │                     │  │  (OIDC, PEP) │   │ Cascade Policy │   │  Limiter   │  │
  │  agent)  │◀────────────────────│  └──────────────┘   │ (fast vs       │   │ (RPM/TPM)  │  │
  └──────────┘   streamed tokens   │         │             │  reasoning)   │   └─────┬──────┘  │
                                   │         ▼             └───────┬───────┘         │         │
                                   │  ┌──────────────┐             │                 ▼         │
                                   │  │ Policy Decision│◀───────────┘        ┌────────────────┐│
                                   │  │ Point (PDP):   │                     │ Circuit Breaker ││
                                   │  │ RBAC/ABAC for  │                     │ Registry        ││
                                   │  │ tool calls     │                     │ (per model/     ││
                                   │  └───────┬────────┘                    │  provider)      ││
                                   └──────────┼─────────────────────────────┴────────┬────────┘│
                                              │                                       │         │
                                   ┌──────────▼───────────────────────────────────────▼─────────┐
                                   │                       DATA PLANE                            │
                                   │                                                              │
                                   │  ┌───────────────┐     ┌────────────────────────────────┐   │
                                   │  │ Prefill Pool  │     │        Decode Pool              │   │
                                   │  │ (compute-bound│────▶│  (memory-bandwidth-bound,       │   │
                                   │  │  parallel;    │ KV  │   sequential, 1 token/step)      │   │
                                   │  │  fills KV     │xfer │                                  │   │
                                   │  │  cache)       │ RDMA│  ┌────────────────────────────┐  │   │
                                   │  └───────┬───────┘/NIXL│  │ Grammar/CFG Constrained     │  │   │
                                   │          │           └─▶│  Decoding Engine (token mask │  │   │
                                   │          ▼               │  = -inf for invalid tokens) │  │   │
                                   │  ┌───────────────┐       └──────────┬─────────────────┘  │   │
                                   │  │ MoE Router /  │                  │                     │   │
                                   │  │ Expert Shards │                  ▼                     │   │
                                   │  │ (top-k gating)│       ┌────────────────────────────┐   │   │
                                   │  └───────────────┘       │ Reasoning State Machine:    │   │   │
                                   │                          │ HIDDEN_COT → SUMMARY →      │   │   │
                                   │                          │ VISIBLE_ANSWER              │   │   │
                                   │                          └──────────┬─────────────────┘   │   │
                                   └─────────────────────────────────────┼─────────────────────┘   │
                                                                          │                          │
                                   ┌──────────────────────────────────────▼────────────────────┐    │
                                   │                     TOOL PROXY LAYER                       │    │
                                   │                                                             │    │
                                   │  ┌───────────────┐   ┌────────────────┐   ┌─────────────┐  │    │
                                   │  │ Function-Call │──▶│ Tool Registry / │──▶│  Sandbox    │  │    │
                                   │  │ Dispatcher    │   │ Capability      │   │ (gVisor /   │  │    │
                                   │  │ (parallel or  │   │ Tokens (scoped, │   │ Firecracker │  │    │
                                   │  │  sequential)  │   │ time-limited)   │   │  microVM)   │  │    │
                                   │  └───────┬───────┘   └────────────────┘   └──────┬──────┘  │    │
                                   │          │  loop-detection hash                  │          │    │
                                   │          │  (tool,args,result)                   │          │    │
                                   └──────────┼────────────────────────────────────────┼─────────┘    │
                                              │                                        │              │
                                   ┌──────────▼────────────────────────────────────────▼─────────┐    │
                                   │                   PERSISTENCE LAYER                          │    │
                                   │                                                               │    │
                                   │  ┌───────────────┐  ┌────────────────┐  ┌─────────────────┐  │    │
                                   │  │ Prompt Cache  │  │ Session/History │  │ Immutable Audit  │  │    │
                                   │  │ Store (prefix-│  │ Store (workflow │  │ Log (WORM, SHA- │  │    │
                                   │  │ indexed KV    │  │ event history,  │  │ 256 hashed,     │  │    │
                                   │  │ blocks)       │  │ continue-as-new)│  │ metadata-only)  │  │    │
                                   │  └───────────────┘  └────────────────┘  └─────────────────┘  │    │
                                   └───────────────────────────────────────────────────────────────┘    │
                                                                                                          │
                                   ┌──────────────────────────────────────────────────────────────────┘
                                   │            TELEMETRY / OBSERVABILITY SINKS
                                   │
                                   │  ┌───────────────┐  ┌────────────────┐  ┌──────────────────┐
                                   │  │ Distributed    │  │ Cost/Token      │  │ Structured Logs   │
                                   │  │ Tracing        │  │ Meter (per      │  │ w/ Correlation-ID │
                                   │  │ (correlation-  │  │ model tier,     │  │ (JSON, no raw     │
                                   │  │  id propagated)│  │ cache hit rate) │  │ prompt content)   │
                                   │  └───────────────┘  └────────────────┘  └──────────────────┘
                                   └──────────────────────────────────────────────────────────────
```

**Request-flow narrative.** (1) A client request enters through AuthN/AuthZ — the agent is treated as an untrusted principal with its own verifiable identity, not a shared API key. (2) The **Model Router** inspects task metadata (declared complexity, latency budget, SLA tier) and picks a lane: fast non-reasoning model, or reasoning model at a given effort level — this is the cascade/routing decision point that dominates unit economics (§3). (3) The **Rate Limiter** checks RPM/TPM budgets before admission; the **Circuit Breaker Registry** short-circuits the call entirely if the target model/provider is currently `OPEN` (§4). (4) The request enters the data plane: **prefill** pushes the full prompt through the model once (compute-bound, highly parallel), populating the **KV cache**; if the architecture is MoE, each token is routed through a **top-k expert subset** via the gating network. (5) **Decode** begins — one token per step, memory-bandwidth-bound, sequential. If the caller requested structured output or a function call, every decode step is intercepted by the **grammar-constrained decoding engine**, which computes a token mask from the compiled JSON-Schema/CFG and zeroes out (`-inf`) any logit that would produce an invalid token — schema violations become *structurally impossible*, not just unlikely. (6) If reasoning is enabled, generation moves through a **state machine**: `HIDDEN_COT → SUMMARY → VISIBLE_ANSWER`, where the raw chain-of-thought is generated but never returned to the client. (7) If the model emits a function-call token, the **dispatcher** routes to the **Tool Proxy layer**: a Policy Decision Point evaluates RBAC/ABAC and returns ALLOW/DENY/REQUIRE_APPROVAL/MASK; on ALLOW, the tool executes inside an isolated sandbox (gVisor for low-risk/dev, Firecracker microVM when the agent touches production data or untrusted input). Every call is hashed as `(tool_name, canonicalized_args, result)` and checked against a rolling window to catch infinite tool-loops before they burn budget. (8) Tool results and generated tokens are persisted to a **session/history store** (event-sourced, so a crashed worker can replay rather than re-execute non-deterministic LLM calls), while the **prompt cache store** retains prefix blocks for reuse on the next turn. (9) Every hop emits a structured, correlation-ID-tagged log line to the metadata-only **audit log** (no raw prompt/response by default) and to the cost/token meter, which is what makes cache-hit-rate and cascade-escalation-rate — the two variables that actually govern spend — observable in production.

---

## 2. Core Mechanics & Algorithms

### 2.1 Attention and the KV cache

Standard scaled dot-product attention for a sequence of length `n` computes `softmax(QKᵀ/√d)V`, an `O(n²·d)` operation per layer if recomputed from scratch at every generation step. Autoregressive decoding avoids this: at step `t`, only the new token's Query vector needs to be computed; Key/Value projections for all `t−1` prior tokens are unchanged and are cached. This turns **per-step** attention cost from `O(n²)` to `O(n)` (dot the new query against `n` cached K vectors), at the cost of `O(n·d)` memory to hold the cache per layer per head.

- **Prefill phase**: the entire prompt is pushed through the model in one parallel forward pass, populating the KV cache for all prompt tokens. Compute-bound (GPU FLOPs are the bottleneck); throughput scales with batch size and matrix-multiply efficiency.
- **Decode phase**: one token generated per step, each step reads the full KV cache. Memory-bandwidth-bound (the GPU spends most of its time streaming cache values from HBM rather than computing), which is why decode throughput does *not* scale the same way as prefill throughput and why disaggregating the two into separate worker pools ("P/D disaggregation") avoids long prompts stalling other users' token generation.
- **Grouped-Query Attention (GQA)**: reduces KV cache memory by sharing a single K/V head projection across multiple Q heads, cutting cache size roughly by the query-to-KV head ratio without a full quality hit from Multi-Query Attention's more aggressive sharing.
- **Invariant**: cached K/V values for position `i` are immutable once written (assuming no architecture change mid-sequence) — this is what makes caching correct at all; violating it (e.g., caching across incompatible RoPE bases) silently corrupts attention.

### 2.2 Positional encoding — RoPE

RoPE injects relative position by rotating Q/K vectors instead of adding a positional bias. Split each `d`-dimensional head into `d/2` 2D pairs; pair `i` is rotated by angle `m·θᵢ` where `θᵢ = 10000^(-2i/d)` and `m` is the token's absolute position. Because 2D rotations compose additively (`R(a)·R(b) = R(a+b)`), the inner product `⟨R(m)q, R(n)k⟩` depends only on `m − n`, giving relative-position sensitivity with **zero added parameters**. Practical consequence: raising the base frequency (e.g., Llama 3's 10,000 → 500,000) stretches the rotation period, which is the standard lever for context-length extrapolation without retraining position embeddings from scratch.

### 2.3 Mixture-of-Experts (MoE) routing

A router (linear layer + softmax) scores each token against `N` experts and activates only the top-`k`, so parameter count scales independently of per-token compute:

| Model | Total params | Active/token | Routing |
|---|---|---|---|
| Mixtral 8×7B | 47B | 13B | top-2 of 8 |
| DBRX | 132B | — | top-4 of 16 |
| DeepSeek-V3/R1 | 671B | 37B | top-8 of 256 (+ shared experts always-on) |

**Load-balancing state machine.** Naive top-k routing collapses onto a few popular experts under gradient descent (a positive-feedback loop: popular experts get more training signal → get better → get routed to more). Two fixes:
1. *Auxiliary load-balancing loss* (Switch Transformer, Mixtral): an extra loss term penalizes uneven routing, but it directly competes with the primary task loss — a hyperparameter tradeoff.
2. *Auxiliary-loss-free* (DeepSeek-V3): each expert carries a bias term added only to the *routing decision* (not gradient-visible to the task loss); a control loop increments the bias for under-utilized experts and decrements it for over-utilized ones after every batch. This decouples load balancing from task-loss gradients entirely — an engineering invariant worth remembering: **routing-fairness and task-quality objectives should not share a gradient path** if you want either to converge cleanly.

Practical serving consequence: because all expert weights must reside in GPU memory regardless of activation frequency, MoE shifts the bottleneck from **compute** to **memory capacity** — a 671B-total/37B-active model still needs enough VRAM to hold 671B parameters.

### 2.4 Reasoning: chain-of-thought and test-time compute

State machine for a reasoning-model turn:

```
  [PROMPT] → (RL-trained policy) → [HIDDEN_COT tokens, billed, not shown]
                                         │
                                         ▼
                              [MODEL-GENERATED SUMMARY] (optional, shown)
                                         │
                                         ▼
                                 [VISIBLE_ANSWER tokens]
```

Test-time compute scales along two independent axes:
- **Sequential scaling**: generate a longer hidden CoT per query. Counter-intuitively, this does *not* monotonically improve accuracy — empirical results show accuracy can *decline* as CoT length grows past an optimum (an "overthinking" effect, more pronounced in smaller/distilled reasoning models), and o3-mini surpasses o1-mini in accuracy at *shorter* average CoT length, i.e., improvements increasingly come from a better RL-trained policy ("thinks harder"), not merely from more inference-time tokens ("thinks longer").
- **Parallel scaling**: sample `m` independent candidate solutions and aggregate (majority vote / best-of-n via a verifier). Complexity is `O(m)` in inference cost for (typically sublinear, diminishing-returns) accuracy gains — the standard self-consistency algorithm:

```python
def self_consistency(sample_fn, verify_fn, n_samples: int):
    """O(n_samples) LLM calls; O(n_samples) verifier calls.
    Convergence property: accuracy approaches the model's "oracle"
    ceiling as n_samples -> inf, but with strongly diminishing
    returns past n_samples ~ 5-10 for most reasoning benchmarks."""
    candidates = [sample_fn() for _ in range(n_samples)]
    scored = [(c, verify_fn(c)) for c in candidates]
    return max(scored, key=lambda cs: cs[1])[0]
```

### 2.5 Function calling and structured output as constrained decoding

Both are implemented at the decoding layer as **grammar-constrained generation**: a JSON Schema (or a "call function X with these typed args" schema) is compiled into a context-free grammar (CFG). OpenAI chose CFGs over finite-state machines specifically because FSMs cannot express recursive/self-referential structures (nested objects, recursive schemas) that a CFG handles natively.

**Token-masking algorithm** (per decode step):
1. Maintain a parser state (Earley-style or automaton state) reflecting how much of the grammar has been matched so far.
2. For the current state, compute the set of tokens whose surface text would keep the partial output grammar-valid.
3. Set logits for all other tokens to `-inf` before softmax/sampling.
4. If the valid-token set has cardinality 1, skip sampling entirely (deterministic emission).

Two implementation strategies with different complexity trade-offs:
- **Outlines**: precomputes the full token-mask table for every automaton state *ahead of time*. Sampling is then `O(1)` lookup per token, but grammar **compilation** can take 3–12 seconds for complex/recursive schemas (reported ~7,000× slower to compile than the alternative below for equivalent expressiveness) — a real latency cost paid once per distinct schema.
- **llguidance**: builds the lexer/Earley parser **lazily**, computing only the mask needed for the current state on-the-fly, at ~50μs of single-core CPU time per token for a 128k-token vocabulary, with negligible startup cost. This is the better fit when schemas vary per-request (can't amortize a precompiled table).

**Invariant**: constrained decoding guarantees *syntactic/schema conformance* (the output always parses and type-checks against the schema) but carries **no guarantee of semantic correctness** — a schema-valid tool argument can still be a hallucinated value. Schema validation and hallucination detection are orthogonal concerns and must be handled by separate layers (§5).

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost formulas

```
cost_per_request = (input_tokens         × price_in  / 1e6)
                  + (reasoning_tokens     × price_out / 1e6)   # hidden CoT, billed as output, invisible to user
                  + (visible_output_tokens× price_out / 1e6)
                  - (cached_input_tokens  × price_in  / 1e6 × cache_discount)
```

**Confirmed list prices, August 2026 snapshot** (per 1M tokens, input/output):

| Model | Input | Output | Cached input |
|---|---|---|---|
| GPT-4o | $2.50 | $10.00 | — |
| GPT-4o mini | $0.15 | $0.60 | — |
| o1 | $15.00 | $60.00 | $7.50 |
| o3 | $2.00 | $8.00 | $0.50 |
| o3-mini / o4-mini | $1.10 | $4.40 | — |
| o1-pro | $150 | $600 | — |
| Claude Haiku 4.5 | $1 | $5 | — |
| Claude Opus 4.5–4.8/5 | $5 | $25 | — |

**Worked example** (2,000 input / 8,000 hidden-reasoning / 600 visible-output tokens/run, **no prompt caching applied** — see §3.2 for the cached-input discount): o3 ≈ **$0.364/request**; o1 ≈ **$0.450/request**; GPT-4o (no reasoning tokens) ≈ **$0.011/request** — a **>30× cost gap driven almost entirely by reasoning tokens billed at the output rate but never shown to the user**. This is the single most important cost lever in an agentic system: routing a query to a reasoning tier when a fast model would have sufficed is not a small inefficiency, it is an order-of-magnitude one.

**Same figures normalized to $ per 1,000 runs** (the unit that maps directly onto a monthly volume forecast — multiply by `daily_volume / 1000 × 30` for a monthly budget line item):

| Model | Assumptions | $/request | **$ per 1k runs** |
|---|---|---|---|
| o3 | 2,000 in / 8,000 reasoning / 600 out tok/run, no cache | $0.364 | **$364 per 1k runs** |
| o1 | 2,000 in / 8,000 reasoning / 600 out tok/run, no cache | $0.450 | **$450 per 1k runs** |
| GPT-4o | 2,000 in / 600 out tok/run (no reasoning tokens), no cache | $0.011 | **$11 per 1k runs** |
| o3, 80% cache hit rate on the 2,000 input tok | Same token mix; §3.2's cached-input discount (75% off the matched portion) applied to the input-token component only | ~$0.352 | **~$352 per 1k runs** |

At this token mix, the input-token cache discount barely moves the total (~3% reduction even at 80% hit rate) — with an 8,000-token hidden-reasoning tail dominating the cost, **reasoning-tier routing decisions, not cache tuning, are the dominant lever** on $/1k-runs; cache tuning matters far more once input tokens (e.g., long RAG context, repeated system prompts) dominate the mix instead of reasoning tokens.

### 3.2 Prompt caching mechanics

- **OpenAI**: automatic for prompts ≥1,024 tokens (GPT-4o+); matches the longest previously-seen prefix in 128-token increments; **50% discount** on the matched portion; retention ~5–10 min (up to ~1hr) best-effort, or an explicit 30-min TTL on GPT-5.6+ via `prompt_cache_options.ttl`. Not available on the Batch API. Real-world hit rates: ~50% (GPT-4o-mini) to ~80%+ (o1-mini), workload-dependent.
- **Anthropic**: explicit opt-in via `cache_control`. Write multipliers on base input price: **1.25×** (5-min TTL), **2×** (1-hr TTL); read (hit) = **0.1×** base input price.

```
breakeven_reads(ttl_5min)  = 1   # one cache hit repays the 1.25x write cost
breakeven_reads(ttl_1hr)   = 2   # two cache hits repay the 2x write cost
```

Below ~70–90% hit rate, documented production cases show **3–8× higher bills than optimal** — cache write costs accumulate without enough reads to amortize them. Compute and alert on `cache_read_tokens / (cache_read_tokens + cache_creation_tokens)` explicitly; neither provider surfaces a single "hit rate" metric.

### 3.3 Latency SLA targets

> ⚠️ Gap: Neither OpenAI nor Anthropic publishes a formal, contractual P50/P95/P99 latency SLA for reasoning models. The **P50 column** below is a third-party benchmark-aggregator estimate (mid-2026), not a vendor commitment — treat as **directional**, not contractual. Since no vendor publishes tail-latency figures at all, the **P95/P99 columns are architect-constructed design targets**, explicitly labeled `[inferred/recommended]` — reasonable SLA tiers to design a system against (retry budgets, timeout configs, async-vs-sync routing thresholds), not measured or promised figures. Treat them as a starting point to validate against your own production telemetry, not as a substitute for it.

| Tier | P50 (vendor-benchmark, reported) | P95 `[inferred/recommended]` | P99 `[inferred/recommended]` | Mitigation for the P95/P99 tail |
|---|---|---|---|---|
| Fast/non-reasoning (GPT-5.5 standard, Claude Opus 4.7 standard) | 0.85–1.1s | **≤2.5s** | **≤4s** | Streaming to first token (masks the P50→P95 gap from the user); keep in synchronous/interactive paths but set a client-side timeout at the P99 target with a fast-model fallback response, not an open-ended wait |
| Reasoning, low/medium effort (o3-mini, o1-mini, o3 default, GPT-5.5 medium) | 9–18.7s | **≤35s** | **≤50s** | Route to an async job + webhook/poll pattern rather than holding a synchronous connection open; show progressive status (not a spinner) so the P95/P99 tail is UX-absorbed; cache prior-turn KV/prefix aggressively (§3.2) since a cache miss on a long system prompt is a common cause of medium-tier P99 outliers |
| Reasoning, high effort (o1-pro, GPT-5.5 Pro high, Gemini 3 Deep Think) | 28–67s | **≤100s** | **≤150s** | Default to the async/webhook pattern, never a held HTTP connection; use the Batch API where correctness matters more than turnaround; parallel-sample `n` candidates with early-exit on the first verifier-passed answer to bound worst-case wall-clock (§2.4); pre-compute during idle periods when the workload is predictable (e.g., overnight batch scoring) |

These P95/P99 targets are constructed as roughly **2–3× the reported P50 midpoint** per tier — a conservative multiple consistent with the tail-inflation typically seen in queueing-bound, variable-compute systems (reasoning token count varies per query, unlike a fixed-cost fast-model call) — and should be tightened or loosened against real percentile telemetry once a tier is in production. For a *measured* (not designed) P99 outcome after applying cascading/routing, see the RouteNLP case study in §3.4 (1,847ms → 387ms), which is a specific pilot's observed result, not a general SLA target.

**Rule of thumb repeated across sources**: do not place a reasoning model in any path with a <2s latency budget. Route via a classifier or cascade (§3.4) instead of hard-coding a tier per product surface.

### 3.4 Dynamic routing / cascading

Two patterns: **upfront routing** (classify complexity, route once) vs. **cascading** (always start cheap, escalate on low verifier confidence). A production pilot (RouteNLP, ~5K queries/day, 8 weeks) reported **58% inference cost reduction**, 91% response-acceptance rate, and **P99 latency reduced from 1,847ms → 387ms** against a $200K+/month baseline.

**Key operational risk**: cascade economics are governed entirely by the **escalation rate**, a property of the verifier/judge, not of the models. A drifting or miscalibrated verifier can push escalation toward 100% — at which point every request pays for *both* the cheap and the expensive call, which is strictly worse than direct routing to the expensive model alone. Escalation rate must be tracked as a first-class SLO, with alerting on drift, exactly like an error-rate SLO.

### 3.5 Throughput and capacity planning

OpenAI enforces RPM/TPM/RPD/TPD limits simultaneously; whichever axis is hit first throttles the request, and TPM is pre-reserved from `input_tokens + max_tokens` **before** the call runs — an oversized `max_tokens` silently reserves budget even for short replies. Tier 1 → Tier 5 grows TPM **1,000×** (30K → 30M) while RPM grows only 20× (500 → 10,000), so large-context workloads hit the TPM ceiling long before the RPM ceiling.

Self-hosted capacity reference (vLLM, NVIDIA):

| Deployment scale | Throughput | Bottleneck → fix |
|---|---|---|
| Single GPU | 10–50 req/s | Memory fragmentation → continuous batching + PagedAttention |
| Multi-GPU node | 100–500 req/s | Model > 1 GPU → tensor/pipeline parallelism |
| Multi-node | 1K–10K req/s | Single-node GPU ceiling → Ray + InfiniBand/RDMA |
| Load-balanced fleet | 10K–100K req/s | Nginx + horizontal replicas |
| Full K8s + autoscaling | 100K–1M+ req/s | Orchestration, HPA on queue depth |

Sizing formula: capacity plan from **(requests/sec × tokens/request × latency target)**, never from raw user count — 1M users at 1 req/week is a categorically different system than 1M users at 10 req/day.

### 3.6 Non-functional requirements

- **Availability**: standard enterprise target 99.9% (≈8.7h/yr downtime) for non-critical paths, 99.95%+ for revenue-critical agentic flows; requires multi-provider fallback chains (§5) since no single LLM vendor publishes an availability SLA competitive with these targets alone.
- **RPO/RTO**: with Temporal-style durable execution, RPO ≈ 0 (event-sourced replay recovers exact state) and RTO ≈ worker-restart time (seconds), *not* the duration of the original outage — this is a materially better RTO than stateless retry-based architectures.
- **Compliance**: SOC2 (CC6.1/PI1.1/C1.1), HIPAA (45 CFR §164.312(b) audit controls, 6-year retention), GDPR (Art. 5(1)(c) minimization, Art. 17 erasure) — all significantly easier to satisfy against metadata-only structured logs than against raw-content logs (§4).

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution for long reasoning/agentic loops

Production pattern (Temporal and equivalents): split agent code into **Workflows** (deterministic orchestration: loop control, message history) and **Activities** (non-deterministic side effects: LLM calls, tool/API invocations). LLM calls must **never** execute directly inside a Workflow — Temporal recovers from crashes by *replaying* event history, and a direct (non-deterministic) LLM call would produce a different result on replay and silently corrupt state. Wrapping it in an Activity means the result is recorded once and replayed from history, not re-executed.

This gives checkpointing "for free" via event sourcing (no explicit checkpoint code), and `workflow.wait_condition` blocks durably at **zero compute cost** — an agent can pause for a human-in-the-loop approval for seconds or weeks without consuming worker resources. For very long conversations, event history grows unbounded unless periodically compacted via the **`continue-as-new`** pattern (summarize state, restart the workflow with condensed history).

### 4.2 Failure taxonomy and idempotency

- **Transient** (retry-eligible): 429, 500, 502, 503, 504 — and provider-specific overload codes (Anthropic 529).
- **Permanent** (never retry): 400, 401, and any schema/auth error — retrying these burns budget for a guaranteed-repeat failure.
- **Idempotency keys**: required on any tool call with side effects (payments, ticket creation, emails) so that a retried Activity after a crash does not double-execute.
- **Poison-pill / infinite-loop detection**: hash `(tool_name, canonicalized_args, result)` in a rolling window; a repeat within 2–4 calls triggers a typed refusal (`{error: 'tool_loop_detected', mode: 'generic_repeat'}`) fed back to the model, rather than relying on the model's own judgment. Named sub-patterns: *generic-repeat*, *poll-no-progress* (same tool, no state change), *ping-pong* (alternating between exactly two tools without completion). Standard latency/error-rate monitoring does **not** catch this class — a looping agent reports as healthy the entire time it burns budget; step-count and output-similarity must be explicitly instrumented.

### 4.3 Circuit breakers

Three states — **Closed** (pass through, track failure rate) → **Open** (fail fast, no calls attempted) → **Half-Open** (admit a small number of probes) — with retries living *inside* the breaker, not replacing it: retries absorb a single transient blip; the breaker protects the system once blips become a sustained pattern.

Converging configuration guidance: trip at ~50% failure rate over a ~100-call sliding window (or 3–5 consecutive 5xx/529s); cooldown 30–60s before half-open probing; **scope breakers per-provider/per-model** so one degraded provider doesn't block traffic to healthy ones. Documented incident: without a breaker, a 20-minute upstream outage pinned all 4 workers of a service exhausting full retry budgets on every request, producing 504s for *all* traffic — not just the affected calls. A less common but important refinement: breakers should also trip on **cost velocity** (e.g., spend > 10× planned rate), since a healthy-looking, error-free agent can still runaway-spend inside an unproductive-but-"successful" loop.

### 4.4 Zero-Trust MCP and tool-level RBAC

Every agent is an **untrusted principal**: unique verifiable identity (not a shared API key), scoped operating boundary, full audit trail. OWASP's LLM Top 10 names **Excessive Agency** as a core risk — mitigation is least-privilege enforced at the **tool's execution logic layer**, never left to the LLM to self-limit (prompt-level restrictions are advisory and bypassable).

**PEP/PDP pattern**: every tool call routes through a Policy Enforcement Point that calls a Policy Decision Point evaluating RBAC + ABAC + approval policy, returning `ALLOW / DENY / REQUIRE_APPROVAL / MASK` before execution. Access-control taxonomy: Role-Based (fixed roles like "support agent"), Attribute-Based (dynamic: identity, resource type, time, risk score), ACL (per-resource allow-lists), Mandatory (centrally assigned, non-overridable labels), **Capability-Based** (scoped, time-limited tokens for one specific action — avoids standing broad permissions entirely, the preferred default for agentic tool access). MCP-specific ecosystems converge on an **Agent Gateway** pattern: authenticate the agent, terminate the connection, check the request against policy, forward only allowed calls — reapplying OAuth/RBAC/ABAC rather than inventing new agent-specific primitives. Delegated authorization uses On-Behalf-Of flows or RFC 8693 token exchange so effective agent permission = **intersection** of the agent's own scope and the delegating human's scope, never exceeding either.

### 4.5 Sandbox isolation

Standard Docker/runc is **insufficient** for LLM-generated code execution — shared host kernel means a guest kernel CVE is a host compromise.

- **gVisor**: user-space application kernel intercepts ~70–80% of the Linux syscall surface; ~100ms boot; acceptable for dev/CI or compute-heavy workloads with limited I/O.
- **Firecracker microVMs**: dedicated Linux kernel per workload via KVM — zero shared kernel code paths, eliminating lateral kernel exploits; ~125ms boot, <5MiB overhead, up to 150 VMs/sec/host. **Required default** whenever the agent executes code derived from untrusted input (emails, web pages, documents) *and* has access to production data or credentials.

Defense-in-depth baseline regardless of sandbox choice: default-deny network egress (explicitly block the cloud metadata endpoint `169.254.169.254`), immutable/read-only root filesystem, hard cgroup limits, ephemeral per-task environments destroyed after execution.

### 4.6 PII pipeline and auditability

Best practice is explicitly **against** logging raw prompts/responses by default. Log a **metadata-only** structured record — actor identity, timestamp, model/prompt version, token counts, latency, policy decision, redaction summary — plus a SHA-256 hash of the payload for integrity verification, with raw-content access gated behind a separate, short-retention, explicitly-audited "break-glass" lane. Where raw-content PII detection/redaction is unavoidable (e.g., before sending to a third-party model), prefer **self-hosted** detection over an external redaction API — routing regulated content through an external service to redact it defeats the purpose. This pipeline directly satisfies HIPAA §164.312(b) audit controls, SOC2 CC6.1/PI1.1/C1.1, and GDPR data-minimization/erasure far more cheaply than an immutable full-content log; HIPAA's 6-year retention floor is commonly met via S3 Object Lock (WORM mode) for tamper-evident storage.

---

## 5. Production Enterprise Code

The module below is a self-contained, runnable resilience layer for LLM calls that demonstrates: correlation-ID structured logging, jittered exponential backoff, a per-provider circuit breaker, a primary→secondary→deterministic fallback chain, schema-validated structured output with bounded re-ask, and tool-call dispatch with RBAC + loop detection. It uses only the standard library plus `pydantic` so it runs anywhere.

```python
"""
resilient_llm_client.py

Production-grade resilience layer for LLM calls: retries, circuit breaking,
model fallback chains, structured-output validation, and RBAC-gated tool
dispatch with loop detection. No network calls are made here -- `call_model`
is injected so this module is testable and provider-agnostic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Optional, Protocol

from pydantic import BaseModel, ValidationError

# --------------------------------------------------------------------------
# 1. Structured logging with correlation IDs
# --------------------------------------------------------------------------

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get() or "-"
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("resilient_llm")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"correlation_id":"%(correlation_id)s","msg":%(message)s}'
        )
    )
    handler.addFilter(CorrelationIdFilter())
    logger.handlers = [handler]
    logger.propagate = False
    return logger


log = configure_logging()


class correlation_scope:
    """Context manager binding one correlation ID to every log line and
    audit record emitted within the block -- required for tracing a single
    agent turn across router -> model -> tool proxy -> audit log."""

    def __init__(self, correlation_id: Optional[str] = None):
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self._token = None

    def __enter__(self) -> str:
        self._token = _correlation_id.set(self.correlation_id)
        return self.correlation_id

    def __exit__(self, *exc_info) -> None:
        _correlation_id.reset(self._token)


# --------------------------------------------------------------------------
# 2. Failure taxonomy
# --------------------------------------------------------------------------

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504, 529}
PERMANENT_STATUS_CODES = {400, 401, 403, 404}


class LLMCallError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_transient(self) -> bool:
        return self.status_code in TRANSIENT_STATUS_CODES


# --------------------------------------------------------------------------
# 3. Exponential backoff with full jitter
# --------------------------------------------------------------------------

def backoff_with_full_jitter(attempt: int, base_s: float = 0.5, cap_s: float = 32.0) -> float:
    """AWS-style 'full jitter': sleep(random(0, min(cap, base * 2^attempt))).
    Avoids thundering-herd resynchronization across concurrent clients that
    fixed-interval retries produce under sustained provider degradation."""
    upper_bound = min(cap_s, base_s * (2 ** attempt))
    return random.uniform(0, upper_bound)


def call_with_retry(
    fn: Callable[[], Any],
    max_attempts: int = 3,
    base_s: float = 0.5,
    cap_s: float = 32.0,
) -> Any:
    last_error: Optional[LLMCallError] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except LLMCallError as exc:
            last_error = exc
            if not exc.is_transient:
                log.info(json.dumps({"event": "retry_aborted_permanent_error",
                                      "status_code": exc.status_code}))
                raise
            if attempt < max_attempts - 1:
                delay = backoff_with_full_jitter(attempt, base_s, cap_s)
                log.info(json.dumps({"event": "retry_backoff",
                                      "attempt": attempt + 1,
                                      "delay_s": round(delay, 3),
                                      "status_code": exc.status_code}))
                time.sleep(delay)
    assert last_error is not None
    raise last_error


# --------------------------------------------------------------------------
# 4. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN
# --------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-provider/per-model scoped breaker. Trips on error-rate over a
    sliding window OR on cost-velocity (spend exceeding a planned multiple),
    since a zero-error agentic loop can still runaway-spend."""

    failure_threshold_ratio: float = 0.5
    window_size: int = 100
    cooldown_s: float = 30.0
    half_open_max_probes: int = 3
    cost_velocity_multiplier: float = 10.0
    planned_cost_per_window_usd: float = 5.0

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _outcomes: Deque[bool] = field(default_factory=lambda: deque(maxlen=100), init=False)
    _cost_window_usd: float = field(default=0.0, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_probes_used: int = field(default=0, init=False)

    def _failure_ratio(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for ok in self._outcomes if not ok) / len(self._outcomes)

    def allow_request(self) -> bool:
        if self._state == BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = BreakerState.HALF_OPEN
                self._half_open_probes_used = 0
                log.info(json.dumps({"event": "breaker_half_open"}))
            else:
                return False
        if self._state == BreakerState.HALF_OPEN:
            if self._half_open_probes_used >= self.half_open_max_probes:
                return False
            self._half_open_probes_used += 1
        return True

    def record_success(self, cost_usd: float = 0.0) -> None:
        self._outcomes.append(True)
        self._cost_window_usd += cost_usd
        if self._state == BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self._outcomes.clear()
            self._cost_window_usd = 0.0
            log.info(json.dumps({"event": "breaker_closed_after_probe_success"}))
        self._check_cost_velocity()

    def record_failure(self, cost_usd: float = 0.0) -> None:
        self._outcomes.append(False)
        self._cost_window_usd += cost_usd
        if self._state == BreakerState.HALF_OPEN:
            self._trip("half_open_probe_failed")
            return
        if len(self._outcomes) >= self.window_size and self._failure_ratio() >= self.failure_threshold_ratio:
            self._trip("failure_ratio_exceeded")
        self._check_cost_velocity()

    def _check_cost_velocity(self) -> None:
        if self._cost_window_usd >= self.planned_cost_per_window_usd * self.cost_velocity_multiplier:
            self._trip("cost_velocity_exceeded")

    def _trip(self, reason: str) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        log.info(json.dumps({"event": "breaker_open", "reason": reason,
                              "failure_ratio": round(self._failure_ratio(), 3)}))

    @property
    def state(self) -> BreakerState:
        return self._state


# --------------------------------------------------------------------------
# 5. Fallback chain: primary -> secondary -> deterministic fallback
# --------------------------------------------------------------------------

class ModelTarget(Protocol):
    name: str

    def call(self, prompt: str) -> str: ...
    def cost_usd(self, prompt: str, response: str) -> float: ...


@dataclass
class FallbackChain:
    """Tries each tier in order, gated by its own circuit breaker. The final
    tier MUST be a deterministic, non-LLM fallback (cached template / rules
    engine) so the chain always terminates without an unbounded outage."""

    tiers: list[tuple[ModelTarget, CircuitBreaker]]
    deterministic_fallback: Callable[[str], str]

    def run(self, prompt: str) -> tuple[str, str]:
        for target, breaker in self.tiers:
            if not breaker.allow_request():
                log.info(json.dumps({"event": "tier_skipped_breaker_open", "tier": target.name}))
                continue
            try:
                response = call_with_retry(lambda: target.call(prompt))
                breaker.record_success(cost_usd=target.cost_usd(prompt, response))
                log.info(json.dumps({"event": "tier_success", "tier": target.name}))
                return target.name, response
            except LLMCallError as exc:
                breaker.record_failure()
                log.info(json.dumps({"event": "tier_failed", "tier": target.name,
                                      "status_code": exc.status_code}))
                continue
        log.info(json.dumps({"event": "fallback_to_deterministic"}))
        return "deterministic_fallback", self.deterministic_fallback(prompt)


# --------------------------------------------------------------------------
# 6. Structured output: schema-validated re-ask (Instructor-style pattern)
#    -- the safety net for providers/paths without native grammar constraints
# --------------------------------------------------------------------------

class ExtractedInvoice(BaseModel):
    vendor: str
    invoice_id: str
    amount_usd: float
    line_item_count: int


def call_with_schema_validation(
    call_fn: Callable[[str], str],
    schema: type[BaseModel],
    prompt: str,
    max_retries: int = 2,
) -> BaseModel:
    """Constrained/grammar decoding (see Sec 2.5) makes schema violations
    structurally impossible where available. This is the fallback layer for
    providers/paths without it: validate, and on failure, re-ask with the
    specific validation error appended so the model can self-correct."""

    current_prompt = prompt
    last_error: Optional[ValidationError] = None
    for attempt in range(max_retries + 1):
        raw = call_fn(current_prompt)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            last_error = exc
            log.info(json.dumps({"event": "schema_validation_failed",
                                  "attempt": attempt + 1,
                                  "errors": exc.errors()}))
            current_prompt = (
                f"{prompt}\n\nYour previous response failed schema validation "
                f"with these errors:\n{exc}\nReturn ONLY corrected JSON matching the schema."
            )
    raise ValueError(f"Schema validation failed after {max_retries + 1} attempts: {last_error}")


# --------------------------------------------------------------------------
# 7. Tool dispatch: RBAC gate + no-progress / infinite-loop guard
# --------------------------------------------------------------------------

class ToolPermissionError(Exception):
    pass


class ToolLoopDetectedError(Exception):
    pass


@dataclass
class ToolDispatcher:
    """PEP-style gate in front of every tool call: checks RBAC before
    execution and hashes (tool, args, result) to catch generic-repeat /
    poll-no-progress loops within a rolling window, independent of whether
    the model itself 'notices' it is stuck."""

    allowed_tools_by_role: dict[str, set[str]]
    repeat_threshold: int = 3
    window_size: int = 20

    _history: Deque[str] = field(default_factory=lambda: deque(maxlen=20), init=False)

    def _check_rbac(self, role: str, tool_name: str) -> None:
        allowed = self.allowed_tools_by_role.get(role, set())
        if tool_name not in allowed:
            log.info(json.dumps({"event": "tool_denied_rbac", "role": role, "tool": tool_name}))
            raise ToolPermissionError(f"role '{role}' is not permitted to call tool '{tool_name}'")

    def _canonical_hash(self, tool_name: str, args: dict, result: Any) -> str:
        payload = json.dumps({"tool": tool_name, "args": args, "result": result}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def dispatch(self, role: str, tool_name: str, args: dict, executor: Callable[[dict], Any]) -> Any:
        self._check_rbac(role, tool_name)
        result = executor(args)
        call_hash = self._canonical_hash(tool_name, args, result)
        repeats = sum(1 for h in self._history if h == call_hash)
        self._history.append(call_hash)
        if repeats + 1 >= self.repeat_threshold:
            log.info(json.dumps({"event": "tool_loop_detected", "tool": tool_name,
                                  "mode": "generic_repeat", "repeats": repeats + 1}))
            raise ToolLoopDetectedError(
                f"tool '{tool_name}' repeated identical (args, result) {repeats + 1} times"
            )
        return result


# --------------------------------------------------------------------------
# Example wiring (graceful degradation end-to-end)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    @dataclass
    class MockModel:
        name: str
        fail_rate: float
        price_in_per_m: float
        price_out_per_m: float

        def call(self, prompt: str) -> str:
            if random.random() < self.fail_rate:
                raise LLMCallError(f"{self.name} overloaded", status_code=529)
            return f'{{"vendor": "Acme Corp", "invoice_id": "INV-{random.randint(1000, 9999)}", ' \
                   f'"amount_usd": 1234.56, "line_item_count": 3}}'

        def cost_usd(self, prompt: str, response: str) -> float:
            return (len(prompt) / 4 / 1e6) * self.price_in_per_m + \
                   (len(response) / 4 / 1e6) * self.price_out_per_m

    primary = MockModel("gpt-5-reasoning", fail_rate=0.6, price_in_per_m=15.0, price_out_per_m=60.0)
    secondary = MockModel("gpt-4o", fail_rate=0.1, price_in_per_m=2.5, price_out_per_m=10.0)

    chain = FallbackChain(
        tiers=[(primary, CircuitBreaker(window_size=5, failure_threshold_ratio=0.5, cooldown_s=2)),
               (secondary, CircuitBreaker(window_size=5, failure_threshold_ratio=0.5, cooldown_s=2))],
        deterministic_fallback=lambda prompt: '{"vendor": "UNKNOWN", "invoice_id": "PENDING-MANUAL-REVIEW", '
                                               '"amount_usd": 0.0, "line_item_count": 0}',
    )

    with correlation_scope() as cid:
        log.info(json.dumps({"event": "request_start", "correlation_id": cid}))
        tier_used, raw_response = chain.run("Extract invoice fields from: <document>")
        invoice = call_with_schema_validation(
            call_fn=lambda p: raw_response, schema=ExtractedInvoice, prompt=raw_response
        )
        log.info(json.dumps({"event": "request_complete", "tier": tier_used,
                              "invoice": invoice.model_dump()}))
```

This demonstrates every required pattern in one coherent flow: a failing/overloaded `primary` (60% failure rate) trips its circuit breaker within a 5-call window and the chain falls through to `secondary`, or to the fully deterministic template if both LLM tiers are unavailable — the system degrades gracefully rather than raising an unhandled exception at the client boundary, and every state transition is captured in correlation-ID-tagged structured logs suitable for an audit pipeline (§4.6).

---

## 6. Architectural System Design Scenarios

### Scenario A — Cost-tiered agentic support platform at enterprise scale

**Problem statement.** A B2B SaaS company runs an agentic customer-support platform handling ~700K daily LLM inferences (peaking ~1.4M on high-traffic days), comparable in shape to Salesforce's reported Agentforce/ApexGuru production numbers (8,000+ enterprise users, 21 global inference regions, ~136B tokens/month). Support tickets range from trivial FAQ lookups to multi-step account investigations requiring function calls into billing/CRM systems. A prior static architecture — every ticket routed to a single frontier reasoning model — costs >$200K/month even though 70%+ of queries are routine, and P99 latency is unacceptable for chat-widget UX.

**Proposed architecture.**

```
Client (chat widget) → Auth/PEP → Complexity Classifier (small, fast model)
                                          │
                        ┌─────────────────┼──────────────────┐
                        ▼                 ▼                  ▼
                  Fast tier          Cascade tier       Reasoning tier
                (GPT-4o-mini /     (fast model first,   (o3/Opus, high
                 Haiku, <2s SLA)    verifier escalates    effort, async
                                    on low confidence)    job queue)
                        │                 │                  │
                        └────────┬────────┴──────────────────┘
                                 ▼
                    Function-Call Dispatcher (RBAC + loop guard)
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
               Billing API   CRM API     Knowledge-base
              (capability   (capability   retrieval (RAG)
               token)        token)
                                 │
                                 ▼
                  Structured response (JSON-schema constrained:
                  {resolution, confidence, escalate_to_human})
```

**Trade-off evaluation matrix.**

| Dimension | Static: reasoning-only for all tickets | Static: fast-model-only for all tickets | Dynamic router + cascade (proposed) |
|---|---|---|---|
| Cost / 1M tickets | Very high (~$300K+, per §3.1 worked example) | Very low (~$11K) but degrades on hard tickets | ~$85–125K (58% reduction demonstrated in RouteNLP-style pilot) |
| Latency P99 | 12–60s — unacceptable for chat UX | <2s but wrong-answer rate rises on complex tickets | 387ms–2s typical; async hand-off for the reasoning-tier minority |
| Ops complexity | Low (one model to operate) | Low | Higher — requires a maintained, monitored verifier/classifier and per-tier circuit breakers |
| Security posture | Same RBAC/sandbox requirements regardless of tier | Same | Same, plus capability tokens scoped per tier to bound blast radius of a misrouted high-privilege tool call |
| Scalability ceiling | Bounded by reasoning-tier TPM limits (hit fastest, per §3.5) | High (cheap model, high TPM headroom) | High — most traffic absorbed by fast tier; reasoning tier sized only for the escalation-rate tail |

**Decision rationale.** The dynamic router/cascade wins decisively on cost and P99 latency, which are the two metrics the business explicitly flagged as broken, and the added operational complexity (maintaining a verifier) is bounded and well-understood — the dominant residual risk is verifier drift silently pushing escalation toward 100%, which is mitigated by tracking escalation rate as a first-class SLO (§3.4) with the same alerting rigor as an error-rate SLO. A reasoning-only architecture is rejected outright on cost and latency; a fast-only architecture is rejected because it silently degrades resolution quality on the hardest (and often highest-value/highest-churn-risk) tickets with no mechanism to detect that degradation.

### Scenario B — Regulated document-extraction pipeline (structured output + PII compliance)

**Problem statement.** A healthcare-adjacent fintech must extract structured claims data (patient ID, procedure codes, billed amount, provider NPI) from scanned/OCR'd documents at ~50K documents/day, feed the structured output into downstream billing systems, and pass a SOC2 + HIPAA audit. Prior attempts using prompted-JSON-mode ("please return JSON") produced a ~4% malformed-output rate that silently corrupted downstream billing records, and the team logged raw document text (containing PHI) directly into their observability platform — a compliance violation discovered during a security review.

**Proposed architecture.**

```
Scanned doc → OCR → Self-hosted PII detector/redactor (data sovereignty:
                     PHI must never leave the security boundary)
                              │
                              ▼
              Grammar-constrained decoding engine (CFG compiled from
              claims JSON Schema — schema violations structurally
              impossible, not just retried)
                              │
                              ▼
              Pydantic-model re-ask loop (bounded max_retries=2, safety
              net for the rare non-schema semantic issue)
                              │
                              ▼
        Structured claim JSON → Billing system (idempotency-keyed write)
                              │
                              ▼
        Immutable audit log: metadata + SHA-256(payload) only,
        WORM storage (S3 Object Lock), 6-year HIPAA retention;
        raw PHI accessible only via a separate, short-TTL, fully
        audited break-glass lane
```

**Trade-off evaluation matrix.**

| Dimension | Prompted JSON mode ("return JSON") | Validate-then-retry only (Instructor-style, no grammar constraint) | Grammar-constrained decoding + bounded re-ask (proposed) |
|---|---|---|---|
| Schema-violation rate | ~4% observed in production, corrupting downstream records | Near-zero after retries, but each malformed attempt costs a full extra LLM call | Structurally near-zero at generation time; re-ask loop only needed for rare semantic (not structural) issues |
| Latency impact | Lowest, but rework cost hidden downstream | Variable — 1–2 extra round-trips on ~4% of documents | Low — CFG compilation (llguidance-style, ~50μs/token) adds negligible per-token overhead; no schema retries needed |
| Ops complexity | Low but hides a silent data-quality problem | Medium — must maintain re-ask prompts and retry budgets | Medium — requires a constrained-decoding-capable provider/runtime, but removes the retry-budget maintenance burden |
| Security/compliance posture | Fails audit — PHI logged raw, no structural guarantee on output | Same PHI-logging risk if not paired with redaction pipeline | Meets HIPAA/SOC2 when paired with self-hosted redaction + metadata-only audit logging (this is a logging-pipeline decision, orthogonal to but paired with the decoding choice) |
| Scalability ceiling | High throughput but downstream cost of corrupted records scales with volume | Throughput capped by retry overhead at scale (50K docs/day × 4% retry ≈ 2,000 extra calls/day) | High — no structural retries; scales linearly with document volume |

**Decision rationale.** Grammar-constrained decoding is selected because it converts the dominant failure mode (schema violation) from a *probabilistic, retry-dependent* problem into a *structurally eliminated* one — critical when the downstream consumer is a billing system with no tolerance for malformed records. It is paired with a bounded Pydantic re-ask loop (§5) as a safety net for the residual class of *semantically* wrong-but-schema-valid outputs (constrained decoding cannot fix hallucinated values), and with a self-hosted PII redaction + metadata-only audit pipeline (§4.6) to close the compliance gap identified in the security review. The validate-then-retry-only approach is rejected as a primary strategy because it treats a preventable structural failure as a runtime exception, adding avoidable latency and cost at 50K-documents/day scale; prompted JSON mode is rejected outright given the demonstrated 4% corruption rate against a compliance-sensitive downstream system.
