# 01 — LLM Foundations

**Scope:** Transformers, reasoning, function calling, and structured output.  
**Study goal:** Explain what an LLM guarantees, what the surrounding system must guarantee, and how those boundaries change architecture, cost, latency, and risk.

The central production distinction is simple:

- A Transformer predicts tokens.
- Reasoning spends additional inference-time compute to improve some answers.
- Function calling emits a typed proposal; it does not execute or authorize an action.
- Structured output guarantees a supported shape; it does not guarantee truth, permission, or business correctness.

## 1. System Topology & Data Flow

### Reference topology

```text
                                      CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Model/snapshot registry │ prompt + schema registry │ router + rollout rules │
│ tenant quotas           │ policy/RBAC/approvals    │ tool allowlists/secrets│
└───────────────┬──────────────────────────┬───────────────────────┬───────────┘
                │ signed versions          │ policy decision       │ short-lived
                │                          │                       │ credentials
                ▼                          ▼                       ▼
                                      DATA PLANE
┌───────────┐   ┌───────────────┐   ┌───────────────────┐   ┌──────────────┐
│ API/WAF   ├──►│ Run admission ├──►│ Context assembler ├──►│ Model router │
│ identity  │   │ quota/deadline│   │ stable prefix first│  │ + client    │
└───────────┘   └───────┬───────┘   └───────────────────┘   └──────┬───────┘
                        │                                          │
                        │                                  ┌───────▼────────┐
                        │                                  │ LLM inference  │
                        │                                  │ prefill/decode │
                        │                                  │ KV/prompt cache│
                        │                                  └───────┬────────┘
                        │                                          │ tokens or
                        │                                          │ tool proposal
                        │                                  ┌───────▼────────┐
                        │                                  │ Output gateway │
                        │                                  │ strict schema  │
                        │                                  │ + domain rules │
                        │                                  └───┬────────┬───┘
                        │                        final result   │        │ tool call
                        │                              ┌────────┘        ▼
                        │                              │      ┌──────────────────┐
                        │                              │      │ Tool proxy       │
                        │                              │      │ authz/PII/approve│
                        │                              │      │ idempotency      │
                        │                              │      └────────┬─────────┘
                        │                              │               │
                        │                              │      ┌────────▼─────────┐
                        │                              │      │ API/code/browser │
                        │                              │      │ sandbox/connectors│
                        │                              │      └────────┬─────────┘
                        │                              │               │ tool result
                        │                              │      ┌────────▼─────────┐
                        │                              └──────┤ Continue or end  │
                        │                                     └────────┬─────────┘
                        │                                              │
              PERSISTENCE LAYER                                       │
┌───────────────────────▼──────────────────────────────────────────────▼────────┐
│ Run/event store │ prompt/schema hashes │ idempotency ledger │ encrypted blobs │
│ checkpoints     │ approval evidence    │ result digests     │ cache metadata  │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                │ append-only events and metrics
                                ▼
                       TELEMETRY / OBSERVABILITY
┌──────────────────────────────────────────────────────────────────────────────┐
│ OpenTelemetry traces │ structured logs │ cost/quality metrics │ WORM audit/SIEM│
└──────────────────────────────────────────────────────────────────────────────┘
```

The control plane owns identities, versions, policy, budgets, and rollout decisions. These are deterministic administrative facts and must never be delegated to model judgment. The data plane executes a particular admitted run using pinned control-plane versions.

### End-to-end request flow

1. The API authenticates the caller, assigns `tenant_id`, `run_id`, `trace_id`, an idempotency key, and an absolute deadline. Admission enforces tenant quotas before consuming model capacity.
2. The run service persists `REQUEST_ACCEPTED`, including redacted input digest and pinned model, prompt, schema, tool, and policy versions.
3. The context assembler puts stable instructions, examples, and tool schemas first so a prefix cache can match; volatile user data goes last. It reserves context space for visible output and hidden reasoning tokens.
4. The router selects an eval-approved model tier. The provider performs **prefill** over input tokens, builds KV state, then **decodes** tokens autoregressively. Streaming reduces perceived latency but does not reduce completion time.
5. The output gateway handles one of four typed outcomes: refusal, incomplete response, final structured result, or function call. It never force-parses a refusal or truncated object.
6. A final object passes JSON Schema/Pydantic validation and then semantic checks such as ownership, date validity, limits, evidence presence, and deterministic recomputation.
7. A function call is treated as untrusted intent. The proxy validates arguments, re-authorizes the principal and resource, filters PII, obtains human approval when required, and executes once under a short-lived credential. A `function_call_output` tied to the original `call_id` is appended before another model turn.
8. Each model/tool boundary is checkpointed. The final response and its audit digest are committed before acknowledgment. Telemetry records queue, prefill, decode, tool, validation, retry, token, and cost dimensions separately.

Choose the interaction shape deliberately: one synchronous request for extraction, streaming for human-visible generation, and a queue/durable workflow for bursty, long-running, or consequential tool use.

## 2. Core Mechanics & Algorithms

### 2.1 Transformer inference

For token representations `X`, learned projections form `Q = XW_Q`, `K = XW_K`, and `V = XW_V`. A single attention head computes:

```text
Attention(Q, K, V) = softmax(QKᵀ / √d_k)V
```

Scaling by `√d_k` keeps dot products from driving softmax into low-gradient saturation as the head dimension grows. Multi-head attention runs independent learned projections, concatenates their outputs, and projects the result. A feed-forward sublayer transforms each position; residual connections and normalization stabilize deep networks. Because attention itself is permutation invariant, position must be introduced explicitly. Modern decoder-only stacks commonly use causal masks, RoPE, RMSNorm, grouped-query attention, and SwiGLU, but those are design choices rather than the definition of a Transformer ([Transformer paper](https://arxiv.org/abs/1706.03762), [OLMo report](https://arxiv.org/abs/2402.00838)).

During training, teacher forcing minimizes next-token cross entropy:

```text
L(θ) = -Σ_t log p_θ(x_t | x_<t)
```

During generation, a causal decoder samples or chooses one token, appends it, and repeats. Tokens in a response are therefore dependent; the model does not independently fill fields in a JSON object.

**Prefill versus decode**

- Prefill processes all `n` prompt tokens in parallel and materializes per-layer keys and values.
- Decode processes one new token per sequence step and reuses cached keys/values.
- Dense full-sequence attention requires `O(n²d)` arithmetic and `O(n²)` attention-score memory in the direct formulation. FlashAttention remains exact while tiling to reduce high-bandwidth-memory traffic ([FlashAttention](https://arxiv.org/abs/2205.14135)).
- With a KV cache, each decode step attends over the existing prefix: approximately `O(nd)` work per layer per generated token. KV memory grows linearly with active sequence length:

```text
KV bytes ≈ 2 × layers × KV_heads × head_dim × sequence_tokens × bytes_per_element
```

The factor two stores keys and values. PagedAttention manages KV in blocks to reduce fragmentation and enable sharing; it improves utilization, not the fundamental linear growth ([vLLM/PagedAttention](https://arxiv.org/abs/2309.06180)).

**Invariant:** at decode step `t`, the token may attend only to positions `≤ t`; violating the causal mask leaks future data.  
**Serving invariant:** a KV page belongs to the correct tenant, model snapshot, layer, and sequence generation; stale or cross-tenant reuse is a data leak.  
**Convergence caveat:** greedy decoding terminates only if EOS is selected or a token cap is reached. Sampling has no application-level convergence guarantee, so enforce explicit output, time, and spend limits.

### 2.2 Reasoning as test-time computation

Chain-of-thought prompting elicits intermediate steps and can improve multi-step tasks. Self-consistency draws `k` reasoning paths and chooses the modal or verifier-preferred answer ([CoT](https://arxiv.org/abs/2201.11903), [self-consistency](https://arxiv.org/abs/2203.11171)). Its cost is roughly `k` times generation, while wall time is roughly one path only if the paths run concurrently and capacity exists.

For answers `a_i` sampled from paths `r_i`, simple self-consistency is:

```text
a* = argmax_a Σ_i 1[a_i = a]
```

Counting is `O(k)` time and `O(u)` memory for `u` unique normalized answers. It converges statistically only when samples are sufficiently independent and the correct normalized answer has the largest probability mass. Correlated mistakes, a weak normalizer, or an ambiguous task defeat that assumption.

Hosted reasoning models may consume hidden reasoning tokens. They occupy context and are billed as output, so budget them explicitly:

```text
input_tokens + reasoning_tokens + visible_output_tokens ≤ model_context_limit
```

Reasoning effort is a quality/cost/latency control, not a trust control. A plausible rationale is neither a proof nor a faithful audit trace; tested models often omit the influence of injected hints ([reasoning faithfulness study](https://arxiv.org/abs/2505.05410)). Verify final claims with code, retrieval evidence, tests, or domain rules.

### 2.3 Function calling

A tool definition gives the model a name, description, and argument schema. The model chooses whether and how to emit a call. The application remains responsible for execution ([function-calling lifecycle](https://developers.openai.com/api/docs/guides/function-calling)).

```text
┌──────────┐ model response ┌──────────────┐ valid final ┌──────────┐
│ REQUESTED├───────────────►│ INTERPRETING ├────────────►│ COMPLETED│
└──────────┘                └──────┬───────┘             └──────────┘
                                  │ function call
                                  ▼
                           ┌──────────────┐ denied/invalid ┌────────┐
                           │  PROPOSED    ├───────────────►│ FAILED │
                           └──────┬───────┘                └────────┘
                                  │ schema + authz + approval
                                  ▼
                           ┌──────────────┐ transient       ┌────────┐
                           │  EXECUTING   ├────────────────►│ RETRY  │
                           └──────┬───────┘                 └───┬────┘
                                  │ result                       │ bounded
                                  ▼                              └──────┐
                           ┌──────────────┐ tool output                   │
                           │  RECORDED    ├───────────────────────────────┘
                           └──────┬───────┘
                                  └────────────► next model turn
```

**Tool-loop invariants**

- Only a tool in the caller's effective allowlist can enter `EXECUTING`.
- Every argument passes structural and semantic validation before authorization; authorization is repeated immediately before execution.
- `(tenant_id, call_id)` or a business idempotency key uniquely identifies a side effect.
- A recorded result must reference the exact `call_id`; otherwise the model can associate a result with the wrong action.
- The loop has maximum steps, wall time, repeated-call count, reasoning/output tokens, and spend. It therefore terminates in `COMPLETED`, `FAILED`, or an explicit escalation state.
- Parallel calls are permitted only when independent and read-only. Dependent calls and mutations are serialized because completion order does not express business causality.

### 2.4 Structured output and constrained decoding

JSON mode constrains syntax to valid JSON but can still produce missing fields, invalid enums, or extra properties. Strict structured output compiles a supported JSON Schema into a grammar and masks illegal next tokens during sampling. If `V` is the vocabulary and `A(s) ⊆ V` contains tokens valid in parser state `s`, constrained decoding samples only from `A(s)`:

```text
p'(token | s) = p(token | s) / Σ_{v∈A(s)}p(v | s),  token ∈ A(s)
p'(token | s) = 0,                                otherwise
```

The naive mask is `O(|V|)` per token; practical engines cache grammar states and valid-token masks. A first use can pay schema-compilation latency, while later uses reuse the artifact ([Structured Outputs implementation](https://openai.com/index/introducing-structured-outputs-in-the-api/), [JSONSchemaBench](https://arxiv.org/abs/2501.10868)).

For strict OpenAI function schemas, make each property required and set `additionalProperties: false`. Represent optional values with a union including `null` when supported. Keep intermediate action schemas separate from final response schemas.

**Grammar invariant:** every emitted prefix can still be extended to a document accepted by the compiled grammar.  
**Non-guarantees:** constrained decoding cannot establish that an account belongs to a caller, an identifier exists, a number obeys a dynamic limit, evidence supports a claim, or a requested action is authorized. Those require deterministic validation after decoding.

## 3. Token Economics & NFR Analysis

### 3.1 Cost per 1,000 runs

Use measured distributions, not a single average. For 1,000 runs:

```text
C_1000 = [I_u×P_in + I_c×P_cached + I_w×P_write
          + (O_visible + O_reasoning)×P_out] / 1,000,000
         + C_tools + C_compute + C_storage
```

`I_u`, `I_c`, and `I_w` are aggregate uncached-read, cached-read, and cache-write tokens across all 1,000 runs. `P_*` is price per one million tokens. Reasoning tokens belong in output.

**Point-in-time assumptions, 2026-08-21:** each run has 2,000 input tokens and 500 billed output tokens (350 visible + 150 hidden reasoning); no retries or tool/container charges. Prices per one million tokens are from the [current pricing reference](https://developers.openai.com/api/docs/pricing):

| Tier | Input | Cache read | Cache write | Output | No-cache cost / 1K runs |
|---|---:|---:|---:|---:|---:|
| `gpt-5.6-sol` | $5.00 | $0.50 | $6.25 | $30.00 | `(2.0M×$5)+(0.5M×$30)` = **$25.00** |
| `gpt-5.6-terra` | $2.00 | $0.20 | $2.50 | $12.00 | `(2.0M×$2)+(0.5M×$12)` = **$10.00** |
| `gpt-5.6-luna` | $0.20 | $0.02 | $0.25 | $1.20 | `(2.0M×$0.20)+(0.5M×$1.20)` = **$1.00** |

Now assume 1,200 stable prefix tokens are written once, hit cache for 999 runs, and 800 volatile tokens remain uncached every run. This idealized single-prefix calculation excludes cache misses:

```text
I_w = 1,200             I_c = 1,200 × 999 = 1,198,800
I_u = 800 × 1,000       O = 500 × 1,000
```

| Tier | Prefix write | Prefix reads | Volatile input | Output | Cached cost / 1K | Saving |
|---|---:|---:|---:|---:|---:|---:|
| `sol` | $0.0075 | $0.5994 | $4.0000 | $15.0000 | **$19.61** | 21.6% |
| `terra` | $0.0030 | $0.2398 | $1.6000 | $6.0000 | **$7.84** | 21.6% |
| `luna` | $0.0003 | $0.0240 | $0.1600 | $0.6000 | **$0.78** | 21.6% |

At the documented 1.25× write and 0.1× read multipliers, a reused eligible prefix breaks even on its second use. Real savings are lower when prefixes vary, keys are overloaded, or TTLs expire. Keep stable instructions, examples, and schemas byte-identical and first; append volatile data. Key application-level semantic caches by tenant, authorization scope, model/prompt/schema version, intent, and source-data version. Never cache an authorization decision across principals.

Model routing should be eval-gated: use `luna` for deterministic extraction/classification, `terra` for ordinary synthesis and bounded tools, and `sol` for complex or high-risk work. Escalate on validation failure, uncertainty, or risk rather than using the most expensive tier universally.

### 3.2 Latency SLOs

Hosted vendors do not publish universal percentile SLAs across snapshots, regions, quotas, context lengths, and reasoning effort. The following are application targets to validate under load, not provider promises.

```text
T_total = T_queue + T_prefill + T_decode(reasoning + visible tokens)
          + Σ(T_tool_network + T_tool_execution) + T_retries
```

| Path and percentile | Target | Alert/mitigation |
|---|---:|---|
| One-shot structured extraction p50 | ≤ 0.8 s | Prefix-cache stable context; use low reasoning; stream only if users benefit. |
| One-shot extraction p95 | ≤ 2.5 s | Route around degraded region/model; cap input/output; precompile common schemas. |
| One-shot extraction p99 | ≤ 5 s | Shed low-priority load; fail over once; return a typed retryable response before deadline. |
| One read-only tool loop p50 | ≤ 1.8 s | Run independent reads in parallel; colocate proxy/connectors; cache safe reference data. |
| One read-only tool loop p95 | ≤ 6 s | Per-hop timeouts; circuit breakers; reduce reasoning effort within an approved quality gate. |
| One read-only tool loop p99 | ≤ 12 s | Stop retries, checkpoint, queue continuation, or return partial read-only results with provenance. |

Track queue time, time to first token, prefill time, inter-token latency/tokens per second, reasoning tokens, visible tokens, tool latency, validation, and retry time separately. Means conceal saturation; p99 reveals queueing, cold schema compilation, long prompts, and tail tool calls.

### 3.3 Throughput and back-pressure

Capacity must satisfy request, token, concurrency, KV-memory, and downstream-tool limits simultaneously:

```text
input_TPM_required  = peak_RPS × 60 × p95_input_tokens
output_TPM_required = peak_RPS × 60 × p95(reasoning + visible output tokens)
max_inflight        = peak_RPS × p99_end_to_end_seconds
queue_drain_seconds = queued_tokens / sustainable_tokens_per_second
```

For `100 RPS`, `3,000` p95 input tokens, `800` p95 billed output tokens, and `8 s` p99 latency, provision at least `18M input TPM`, `4.8M output TPM`, and `800` in-flight slots before a recommended 2× peak/failover reserve. A provider request-per-minute quota may bind earlier.

Back-pressure design:

- Admit with a token bucket charged on estimated input plus maximum output, not request count alone.
- Use bounded queues. At a two-second interactive queue budget, reject or degrade when queued predicted work exceeds two seconds of sustainable capacity.
- Isolate interactive, batch, and high-risk traffic in separate bulkheads so offline work cannot starve users.
- Cap context, reasoning, output, tool steps, and spend before dispatch. Honor `Retry-After`; otherwise apply bounded jittered backoff.
- Assign retries to one layer. Nested SDK, service, and workflow retries multiply load during an outage.
- Batch offline extraction. The provider batch path is cheaper but has a long completion window, so it is not an interactive overflow queue.

### 3.4 NFR targets and trade-offs

| Requirement | Target | Design consequence / trade-off |
|---|---|---|
| Availability | 99.9% for generation; 99.99% for admission/status APIs | Multi-region/provider fallback improves availability but increases eval, data-residency, and schema-compatibility work. |
| Durability | No acknowledged side effect without durable event and idempotency record | Commit-before-ack adds database latency but prevents ambiguous execution. |
| RPO | 0 for approvals and tool side effects; ≤ 5 min for derived telemetry | Synchronous replicated ledger for actions; async telemetry replication is cheaper. |
| RTO | ≤ 15 min workflow control plane; ≤ 60 min analytics | Warm workers and tested failover cost more than cold recovery. |
| Quality | Schema pass ≥ 99.9%; domain-rule pass per task eval; zero unauthorized execution | Strict schemas reduce parse failures but not semantic error; high-risk failures close to review. |
| Privacy | PII minimized before inference; regional residency where required | Redaction can remove useful context; retain reversible tokens only in an isolated vault. |
| Audit | 100% model/tool boundaries linked by trace/run/call IDs | Full payload logging helps forensics but increases privacy exposure; prefer redacted payloads plus digests. |
| Compliance | Data classification drives endpoint, retention, vendor, and approval policy | SOC 2/HIPAA/GDPR readiness is system-wide; provider certification alone is insufficient. |

## 4. Distributed Resilience & Security

### 4.1 Durable execution

Persist before inference and checkpoint after `MODEL_RECEIVED`, `TOOL_PROPOSED`, `APPROVAL_GRANTED`, `SIDE_EFFECT_COMMITTED`, `TOOL_RESULT_RECORDED`, and `FINAL_VALIDATED`. A checkpoint contains model snapshot, prompt/schema/tool/policy hashes, token use, `call_id`, external idempotency key, attempt, result digest, and parent event.

Temporal is a strong fit for long-running, approval-bearing flows: the service persists Event History and replays deterministic workflow code, while provider/tool calls run as retryable activities. Replay must consume recorded outputs; it must not re-query a nondeterministic model. Kafka fits high-volume ordered event fan-out and replay. Its at-least-once delivery does not make an external action exactly once, so combine it with an outbox and unique `(tenant_id, call_id)` constraint ([Temporal execution](https://docs.temporal.io/workflow-execution), [Kafka design](https://kafka.apache.org/43/design/design/)).

Use optimistic compare-and-swap on `run.version`, partition messages by `run_id`, and lease a tool call with an expiry plus monotonically increasing fencing token. Prefer that single-owner partitioning over a distributed lock. If a legacy resource requires a distributed mutex, give the lock a bounded lease and fencing token so an expired owner cannot commit stale work. Never hold a database or distributed lock while awaiting inference, a remote tool, or a human.

### 4.2 Failure taxonomy and recovery

| Class | Examples | Response |
|---|---|---|
| Transient | 429/503, timeout, connection reset, worker loss | Retry once at the owning layer with exponential backoff and jitter; honor deadline and `Retry-After`. |
| Permanent input/policy | 400, unsupported schema, refusal, denied permission, invalid resource | Do not retry unchanged input; return typed failure or request correction/approval. |
| Semantic | Schema-valid but impossible date, wrong owner, unsupported evidence | Deterministic rule failure; one constrained correction attempt only when safe, then review. |
| Poison pill | Same event repeatedly crashes workers or fails validation | Increment attempt in durable metadata; quarantine to DLQ after threshold; alert with redacted digest. |
| Ambiguous side effect | Tool timed out after sending mutation | Reconcile by idempotency key/read API before retry; compensate if the business operation supports it. |
| Capacity cascade | Rising queue, p95/p99, 429/503, synchronized retries | Open breaker, shed load, isolate bulkheads, lower bounded output, and preserve status/control APIs. |

A circuit breaker is keyed by dependency, model, and region. It moves `CLOSED → OPEN` after a rolling failure threshold, fails fast through a cooldown, then permits limited `HALF_OPEN` probes. A successful probe closes it; a transient failure reopens it. Permanent client or policy errors should not count as dependency-health failures.

Fallback order is policy-bound: same approved model in another region, compatible lower tier that passed the same schema/quality gate, safe cached or read-only result, durable queue, then explicit unavailability. Never silently downgrade a consequential workflow.

### 4.3 Zero-Trust MCP and tool execution

MCP or any equivalent interoperability layer expands the function-call boundary; it does not change the trust rule.

```text
┌────────────┐ delegated identity ┌──────────────┐ mTLS/OAuth ┌──────────────┐
│ Agent host ├───────────────────►│ Policy/tool  ├───────────►│ MCP server   │
│ no secrets │                    │ proxy        │            │ verified ID  │
└─────┬──────┘                    └──────┬───────┘            └──────┬───────┘
      │ proposed call                    │ allowlisted capability    │ scoped API
      ▼                                  ▼                           ▼
┌────────────┐                    ┌──────────────┐            ┌──────────────┐
│ Schema +   │                    │ Approval +   │            │ Sandbox /    │
│ domain gate│                    │ token minting│            │ enterprise API│
└────────────┘                    └──────────────┘            └──────────────┘
```

- Authenticate the user/workload and the MCP server; encrypt transport and pin an approved server/capability registry.
- Intersect model-visible tools with tenant policy. Apply tool- and resource-level RBAC/ABAC on every call using least privilege.
- Mint a short-lived, audience-bound downstream credential after authorization; do not expose ambient cloud or user credentials to the model or server.
- Restrict egress, filesystem, CPU, memory, wall time, and syscalls. Prefer containers for ordinary isolation, microVMs for hostile code, and capability-limited WASM where compatibility permits.
- Require approval immediately before consequential mutations and show canonical action details, not model-authored prose.

### 4.4 PII and chain of custody

Apply the same pipeline independently to user input, retrieved context, model output, tool arguments/results, and logs:

```text
┌────────┐   ┌──────────────────┐   ┌──────────────┐   ┌──────────┐
│ Payload├──►│ regex + NER detect├──►│ block/tokenize├──►│ policy use│
└────────┘   └─────────┬────────┘   └──────┬───────┘   └────┬─────┘
                       │ entity/type/conf. │ vault mapping         │
                       └───────────────────┴──────────►┌───────────▼──────┐
                                                     │ immutable audit   │
                                                     └──────────────────┘
```

Tool arguments need a dedicated scan: some provider guardrails explicitly do not detect PII inside tool-use parameters. Logs also need independent redaction because invocation logging may retain original input ([Bedrock sensitive filters](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)).

Every append-only audit event records `event_id`, parent event, `trace_id`, `run_id`, `tenant_id`, actor/workload, model snapshot, prompt/schema/tool/policy hashes, redacted input/output or digest, policy decision, approval identity, `call_id`, timestamps, tokens/cost, and result/error. Sign batches or chain event hashes, write them to immutable/WORM storage, separate tenants, and log every read. This preserves chain of custody without treating hidden reasoning as an audit explanation. OpenTelemetry GenAI attributes provide common trace vocabulary; sensitive prompt and argument capture remains opt-in ([OTel GenAI conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)).

## 5. Production Enterprise Code

The following Python 3.11 program is executable with the standard library. It demonstrates schema and semantic validation, bounded full-jitter retries, a thread-safe circuit breaker, a primary/secondary/deterministic fallback chain, JSON logs with correlation IDs, deadline propagation, and a degraded result that cannot trigger a side effect.

Save it as `resilient_llm.py` and run `python resilient_llm.py`. Replace injected provider adapters in an application; keep the resilience and validation boundary provider-neutral.

```python
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Callable, Protocol


class TransientProviderError(RuntimeError):
    pass


class PermanentProviderError(RuntimeError):
    pass


class CircuitOpenError(TransientProviderError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class Ticket:
    category: str
    priority: int
    summary: str
    source: str
    degraded: bool = False

    @classmethod
    def parse(cls, raw: str, source: str) -> "Ticket":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PermanentProviderError("provider returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != {
            "category", "priority", "summary"
        }:
            raise PermanentProviderError("response violates the exact schema")
        category = value["category"]
        priority = value["priority"]
        summary = value["summary"]
        if category not in {"billing", "security", "technical", "other"}:
            raise PermanentProviderError("category violates the domain enum")
        if type(priority) is not int or not 1 <= priority <= 5:
            raise PermanentProviderError("priority must be an integer from 1 to 5")
        if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 240:
            raise PermanentProviderError("summary length violates policy")
        return cls(category, priority, summary.strip(), source)


class Provider(Protocol):
    name: str

    def complete(self, text: str, timeout_s: float) -> str:
        """Return JSON matching Ticket's three model-produced fields."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.time(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for field in ("correlation_id", "provider", "attempt", "state"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("llm_gateway")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_s: float = 10.0):
        if failure_threshold < 1 or recovery_s <= 0:
            raise ValueError("invalid circuit-breaker configuration")
        self._threshold = failure_threshold
        self._recovery_s = recovery_s
        self._failures = 0
        self._opened_at = 0.0
        self._state = BreakerState.CLOSED
        self._probe_inflight = False
        self._lock = threading.Lock()

    def before_call(self) -> None:
        with self._lock:
            if self._state is BreakerState.OPEN:
                if time.monotonic() - self._opened_at < self._recovery_s:
                    raise CircuitOpenError("dependency circuit is open")
                self._state = BreakerState.HALF_OPEN
            if self._state is BreakerState.HALF_OPEN:
                if self._probe_inflight:
                    raise CircuitOpenError("half-open probe already in flight")
                self._probe_inflight = True

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._probe_inflight = False
            self._state = BreakerState.CLOSED

    def transient_failure(self) -> None:
        with self._lock:
            self._probe_inflight = False
            self._failures += 1
            if (
                self._state is BreakerState.HALF_OPEN
                or self._failures >= self._threshold
            ):
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state.value


def call_with_retry(
    provider: Provider,
    breaker: CircuitBreaker,
    text: str,
    deadline: float,
    correlation_id: str,
    max_attempts: int = 3,
    base_delay_s: float = 0.1,
) -> Ticket:
    for attempt in range(1, max_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransientProviderError("request deadline exhausted")
        breaker.before_call()
        try:
            raw = provider.complete(text, timeout_s=min(remaining, 5.0))
            ticket = Ticket.parse(raw, source=provider.name)
        except PermanentProviderError:
            # Invalid input/schema/policy failures do not signal dependency health.
            raise
        except (TimeoutError, ConnectionError, TransientProviderError) as exc:
            breaker.transient_failure()
            logger.warning(
                "transient provider failure",
                extra={
                    "correlation_id": correlation_id,
                    "provider": provider.name,
                    "attempt": attempt,
                    "state": breaker.state,
                },
            )
            if attempt == max_attempts:
                raise TransientProviderError("retry budget exhausted") from exc
            # Full jitter avoids synchronized retry waves.
            delay = random.uniform(0.0, base_delay_s * (2 ** (attempt - 1)))
            if delay >= deadline - time.monotonic():
                raise TransientProviderError("insufficient deadline for retry") from exc
            time.sleep(delay)
        else:
            breaker.success()
            logger.info(
                "provider call succeeded",
                extra={
                    "correlation_id": correlation_id,
                    "provider": provider.name,
                    "attempt": attempt,
                    "state": breaker.state,
                },
            )
            return ticket
    raise AssertionError("bounded retry loop did not terminate")


def deterministic_fallback(text: str) -> Ticket:
    lowered = text.lower()
    if any(term in lowered for term in ("breach", "stolen", "unauthorized")):
        category, priority = "security", 5
    elif any(term in lowered for term in ("invoice", "charged", "refund")):
        category, priority = "billing", 3
    elif any(term in lowered for term in ("error", "crash", "failed")):
        category, priority = "technical", 3
    else:
        category, priority = "other", 2
    normalized = " ".join(text.split())
    summary = (normalized[:237] + "...") if len(normalized) > 240 else normalized
    return Ticket(category, priority, summary or "Empty ticket", "rules-v1", True)


class ResilientGateway:
    def __init__(self, providers: list[Provider]):
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = providers
        self._breakers = {p.name: CircuitBreaker() for p in providers}

    def classify(self, text: str, timeout_s: float = 8.0) -> Ticket:
        if not text.strip():
            raise ValueError("ticket text cannot be empty")
        correlation_id = str(uuid.uuid4())
        deadline = time.monotonic() + timeout_s
        for provider in self._providers:
            try:
                return call_with_retry(
                    provider,
                    self._breakers[provider.name],
                    text,
                    deadline,
                    correlation_id,
                )
            except (TransientProviderError, CircuitOpenError) as exc:
                logger.error(
                    f"provider unavailable: {type(exc).__name__}",
                    extra={
                        "correlation_id": correlation_id,
                        "provider": provider.name,
                        "state": self._breakers[provider.name].state,
                    },
                )
            except PermanentProviderError as exc:
                # A schema-invalid model response can fall through to another
                # eval-approved provider, but the malformed output is never used.
                logger.error(
                    f"provider contract failure: {exc}",
                    extra={
                        "correlation_id": correlation_id,
                        "provider": provider.name,
                        "state": self._breakers[provider.name].state,
                    },
                )
        logger.warning(
            "returning deterministic degraded classification",
            extra={"correlation_id": correlation_id, "provider": "rules-v1"},
        )
        return deterministic_fallback(text)


class FunctionProvider:
    """Adapter useful for SDK clients while keeping gateway logic testable."""

    def __init__(self, name: str, fn: Callable[[str, float], str]):
        self.name = name
        self._fn = fn

    def complete(self, text: str, timeout_s: float) -> str:
        return self._fn(text, timeout_s)


def main() -> None:
    def unavailable(_: str, __: float) -> str:
        raise TimeoutError("simulated regional outage")

    def healthy(_: str, __: float) -> str:
        return json.dumps(
            {"category": "billing", "priority": 3, "summary": "Duplicate charge"}
        )

    gateway = ResilientGateway(
        [
            FunctionProvider("primary-region", unavailable),
            FunctionProvider("secondary-region", healthy),
        ]
    )
    result = gateway.classify("I was charged twice", timeout_s=3.0)
    print(json.dumps(asdict(result), separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

In a real adapter, pass the remaining timeout to the provider SDK and request a strict schema. Do not retry SDK calls again if this gateway owns retries. Persist `correlation_id`, model snapshot, usage, schema version, and response digest around this code. A `degraded=true` ticket is safe for triage display but must be barred from automated consequential action.

## 6. Architectural System Design Scenarios

### Scenario 1 — Multi-tenant support-ticket extraction

**Problem statement.** Design a service for 600 SaaS tenants that sustains 100,000 tickets/minute (1,667 RPS), bursts to 2,500 RPS, returns a typed category/priority/summary at p99 ≤ 3 seconds for interactive traffic, costs less than $1.25 per 1,000 tickets at the baseline workload, and never leaks ticket data or cached content across tenants. Backlogged imports may complete within 24 hours.

**Proposed architecture.** Use a strict response schema and no tools: this task needs a machine contract, not live action. Route ordinary English tickets to `gpt-5.6-luna` with low reasoning; route validation failures, unsupported languages, and eval-defined ambiguity to `terra`. Put versioned extraction policy and schema in the stable cached prefix and ticket text last. Use synchronous regional workers for interactive traffic and provider Batch for imports. Validate enum/range/length deterministically, store `schema_version`, and sample outputs for labeled quality evaluation.

```text
┌──────────────┐  mTLS/OIDC  ┌──────────────┐  token bucket ┌──────────────┐
│ Tenant apps  ├────────────►│ Regional API ├──────────────►│ Priority     │
│ + bulk import│             │ WAF + quotas │               │ queues       │
└──────────────┘             └──────┬───────┘               └──────┬───────┘
                                    │ interactive                    │ batch
                                    ▼                                ▼
                             ┌──────────────┐                ┌──────────────┐
                             │ Luna workers │                │ Batch files  │
                             │ cached prefix│                │ 24h window   │
                             └──────┬───────┘                └──────┬───────┘
                                    │ strict object                  │
                                    └──────────────┬─────────────────┘
                                                   ▼
                                           ┌──────────────┐
                                           │ Schema +     │ validation fail
                                           │ domain gate  ├──────────────┐
                                           └──────┬───────┘              ▼
                                                  │             ┌──────────────┐
                                                  │             │ Terra retry  │
                                                  │             │ /review      │
                                                  │             └──────┬───────┘
                                                  ▼                    │
                                           ┌──────────────┐            │
                                           │ Tenant DB +  │◄───────────┘
                                           │ object store │
                                           └──────┬───────┘
                                                  ▼
                                           ┌──────────────┐
                                           │ OTel + audit │
                                           └──────────────┘
```

At 2,000 input and 500 output tokens, all-`luna` is $1.00/1K without cache and about $0.78/1K under the ideal cache assumptions in Section 3. Budget the remaining $0.47 for escalation, storage, and service compute; enforce a maximum escalation rate derived from measured `terra` usage. Baseline capacity is roughly `1,667×60×2,000 = 200M input TPM` and `50M output TPM`; burst capacity is `300M/75M TPM`, plus failover reserve. Split traffic across provider projects/regions only where residency policy permits, and isolate bulk imports.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| **Strict `luna` + eval-gated `terra`; batch imports** | Lowest interactive baseline; targeted escalation | Low p50/p99; batch ≤ 24h | Medium: router, evals, two paths | Strong schema; tenant-keyed cache and storage still required | High; separate sync/batch pools |
| All `terra`, synchronous | About $10/1K baseline before cache | Predictable but more scarce capacity | Low-medium | Same contract; larger cost-abuse exposure | Medium at fixed budget/quota |
| Self-hosted open model | Hardware-dependent; attractive only at steady utilization | Controllable after warmup | High: GPU/KV scheduler, upgrades, evals | Maximum residency control and responsibility | High with capital and expert operations |

**Decision rationale.** The hybrid wins because strict output supplies the required machine contract while the low-cost tier meets the $1.25 target. Validation-based escalation buys quality only where needed. Separate batch capacity absorbs imports without damaging interactive tails. A tool loop would add latency and attack surface without providing business value.

### Scenario 2 — Regulated high-value account action

**Problem statement.** Design an assistant for corporate treasury users that proposes account transfers up to $250,000. It handles 60 requests/second, must produce a proposal at p95 ≤ 6 seconds and p99 ≤ 12 seconds, requires two-person approval above $50,000, has RPO 0 for approvals and transfers, and must provide an immutable decision chain for seven years. No model output may directly move money.

**Proposed architecture.** A reasoning-capable model creates only a strict proposal `{action, source_account_id, beneficiary_id, amount, currency, evidence_ids, risk_flags}`. A Temporal workflow checkpoints the proposal, calls deterministic ownership/balance/limit/sanctions services through a zero-trust tool proxy, and waits for approvals. The execution activity receives a one-use, audience-bound credential and idempotency key. The bank connector records or reconciles the transfer before the workflow continues. The model may summarize the committed result, but the ledger is authoritative.

```text
┌──────────────┐ authenticated ┌──────────────┐         ┌──────────────┐
│ Treasury UI  ├──────────────►│ API/policy   ├────────►│ Temporal     │
│ + approvers  │◄────status────┤ admission    │         │ workflow     │
└──────┬───────┘               └──────────────┘         └───┬──────┬───┘
       │ signed approvals                                   │      │
       └────────────────────────────────────────────────────┘      │
                                                     proposal call ▼
                                                     ┌──────────────┐
                                                     │ Reasoning LLM│
                                                     │ strict schema│
                                                     └──────┬───────┘
                                                            │ untrusted proposal
                                                            ▼
┌──────────────┐  mTLS/OAuth  ┌──────────────┐ checks ┌──────────────┐
│ Bank/payment │◄─────────────┤ Tool proxy   ├───────►│ Rules, KYC,  │
│ connector    │ idempotent   │ RBAC/PII     │        │ limits       │
└──────┬───────┘ execution    └──────┬───────┘        └──────────────┘
       │ result/reconcile             │
       └──────────────────────┬───────┘
                              ▼
                     ┌─────────────────┐        ┌──────────────┐
                     │ Ledger + outbox ├───────►│ WORM audit   │
                     │ RPO 0           │        │ + SIEM       │
                     └─────────────────┘        └──────────────┘
```

At `60 RPS`, `4,000` p95 input tokens, `1,500` p95 visible-plus-reasoning tokens, and `12 s` p99, provision at least `14.4M input TPM`, `5.4M output TPM`, and `720` in-flight workflow/model operations, then add failover reserve. Separate model, rules, approval, and connector deadlines. Queue safely after proposal checkpoint when the bank is unavailable; never retry an ambiguous transfer until reconciliation by idempotency key.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| **Strict proposal + deterministic rules + Temporal + approval** | Medium-high | Adds validation/approval; within machine p95 before humans | High | Strongest: model has no payment credential; RPO 0 ledger | High with partitioned workflows/workers |
| Direct function call from chat service | Medium | Lowest apparent latency | Medium | Unacceptable: weak durability, approval, and ambiguous-retry handling | Medium; chat service becomes stateful bottleneck |
| Human-only forms and rules | Low model cost | Slow user completion; deterministic processing | Medium | Strong but poorer intent capture; still needs secure execution | High for fixed products, low flexibility |

**Decision rationale.** The durable propose-validate-approve-execute design wins because the governing constraint is unauthorized or duplicated transfer risk, not minimum model latency. Temporal supplies checkpointed orchestration and approval waits; the ledger, outbox, idempotency key, and reconciliation close the side-effect ambiguity. Strict output reduces parsing failures, while deterministic services and two-person approval establish truth and permission.

## Interview Review

1. **Why can structured output still be dangerous?** It constrains syntax, not ownership, authorization, factuality, or dynamic business rules.
2. **Why is function calling not tool execution?** The model emits a name and arguments; the application authenticates, validates, authorizes, approves, executes, records, and returns the result.
3. **What dominates long-context serving capacity?** Prefill compute and per-sequence KV memory; decode remains sequential and attends over the growing prefix.
4. **When does prompt caching help?** When a sufficiently long, byte-stable prefix is reused within provider eligibility/TTL. It does not cache a personalized authorization decision.
5. **What is the safe interpretation of reasoning tokens?** They are billed test-time compute that may improve results; they consume context and latency but are not a faithful proof trace.
6. **What belongs outside the model?** Identity, policy, budgets, durable state, idempotency, approvals, semantic validation, side-effect execution, and audit retention.

## Primary References

- [Attention Is All You Need](https://arxiv.org/abs/1706.03762)
- [Chain-of-Thought Prompting](https://arxiv.org/abs/2201.11903)
- [Self-Consistency Improves Chain of Thought Reasoning](https://arxiv.org/abs/2203.11171)
- [Toolformer](https://arxiv.org/abs/2302.04761)
- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI reasoning models](https://developers.openai.com/api/docs/guides/reasoning)
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [OpenAI pricing](https://developers.openai.com/api/docs/pricing)
- [Temporal workflow execution](https://docs.temporal.io/workflow-execution)
- [OWASP excessive agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
