# Context Engineering

Context engineering is the discipline of giving the model the minimum sufficient context, in the highest-signal order, with deterministic packaging, bounded cost, and recoverable execution semantics. In production systems, the core problem is not "fit more text into the window"; it is "decide what belongs in hot prompt state, what belongs in durable memory, what should be cached, and what must never be trusted as instruction material."

## 1. System Topology & Data Flow

The production topology below separates control-plane decisions from data-plane execution so that prompt structure, authorization, caching, and observability are explicit system responsibilities rather than hidden application glue.

```text
┌──────────────────────────── Control Plane ────────────────────────────┐
│ Policy Pack │ Prompt Templates │ Tool RBAC │ Cache Policy │ SLO Rules │
└───────────────┬─────────────────────┬──────────────┬──────────────────┘
                │                     │              │
                v                     v              v
┌──────────────────────────────── Data Plane ───────────────────────────┐
│ Client/API ─> Admission Ctrl ─> Context Assembler ─> Prompt Packer    │
│                    │                 │                │                │
│                    │                 │                ├─> Exact Cache  │
│                    │                 │                ├─> Compression  │
│                    │                 │                └─> Model Router │
│                    │                 │                               │ │
│                    │                 ├─> Thread Checkpoint Store     │ │
│                    │                 ├─> Long-term Memory Store      │ │
│                    │                 └─> Retrieval / Semantic Cache  │ │
│                    │                                                 v │
│                    │                                    Provider APIs / │
│                    │                                    Managed Caches  │
│                    │                                                 │ │
│                    └───────────────────────────────> MCP Tool Proxy ─┘ │
└─────────────────────────────────────────────────────────────────────────┘
                │
                v
┌──────────────────── Persistence & Observability ──────────────────────┐
│ Event Log │ Audit Ledger │ Metrics │ Traces │ DLQ │ Cost Telemetry    │
└────────────────────────────────────────────────────────────────────────┘
```

Request flow:

1. `Admission Ctrl` enforces quotas, concurrency caps, and deadlines before a single token is sent to a model.
2. `Context Assembler` loads static runtime context, short-term thread state, and long-term memory separately. This follows the LangChain/LangGraph split between hot thread-scoped state and colder cross-conversation memory.
3. `Prompt Packer` canonicalizes tool schemas, examples, and policies so exact-prefix caches remain reusable. It then orders long source material first and the specific question last, matching current Anthropic and Google guidance.
4. `Exact Cache` attempts deterministic reuse for stable prefixes such as policies, tool manifests, and large unchanged documents. `Semantic Cache` handles paraphrase-heavy repetition where exact-prefix reuse is low.
5. `Compression` is applied only when budget pressure exists or reuse is poor. Compression is query-aware, because naive entropy-only trimming can delete the evidence that matters.
6. `Model Router` chooses the cheapest model tier that meets quality and latency constraints, then invokes the provider or a managed cache resource.
7. `Thread Checkpoint Store` persists execution state at workflow boundaries so retries, resume, and replay do not require re-sending the whole transcript.
8. `MCP Tool Proxy` executes external actions behind policy checks. Tool outputs flow back as low-trust data, not as developer instructions.
9. `Event Log`, tracing, and audit sinks record latency percentiles, `cached_tokens`/`cache_write_tokens`, semantic-cache hit rate, fallback activation, and user-visible degradation.

The key design move is that the prompt is not the system of record. Durable state lives outside the prompt; the prompt is a just-in-time projection of the state required for the current decision.

## 2. Core Mechanics & Algorithms

### Context assembly model

Treat context as four classes with different lifecycles:

- `Static context`: policies, tool schemas, examples, persona, compliance rules.
- `Hot state`: current thread turns, tool outputs for the active workflow, unresolved subgoals.
- `Warm memory`: recent summaries, user preferences, open cases, session-level facts.
- `Cold memory`: archived history, source corpus, durable documents, analytics artifacts.

Only the first two belong in every inference path. Warm and cold memory must compete for budget.

### Packing algorithm

The practical packing order is:

1. Canonical static prefix.
2. Relevant source blocks and citations.
3. Retrieved or remembered state.
4. Tool outputs wrapped as data blocks.
5. Final question, instructions, and output schema.

This ordering defends against "lost in the middle" failure modes and improves exact-prefix cache stability. Long documents first and the question last also align with Anthropic and Gemini guidance for large-context prompting.

### State machine

```text
┌─────────┐   load    ┌────────────┐   pack    ┌────────────┐
│  Idle   ├──────────>│ Assemble   ├──────────>│ Canonical  │
└────┬────┘           └─────┬──────┘           └─────┬──────┘
     │                      │                        │
     │ retry/resume         │ miss                   │ budget overflow
     │                      v                        v
     │                ┌────────────┐          ┌────────────┐
     │                │ Retrieve / │          │ Compress / │
     │                │ Re-rank    │          │ Trim       │
     │                └─────┬──────┘          └─────┬──────┘
     │                      └────────────┬──────────┘
     │                                   v
     │                            ┌────────────┐
     └────────────────────────────┤ Infer      │
                                  └─────┬──────┘
                                        │
                             success    │    failure
                                        v
                                  ┌────────────┐
                                  │ Persist    │
                                  └─────┬──────┘
                                        v
                                   ┌────────┐
                                   │ Done   │
                                   └────────┘
```

### Core algorithms

#### Exact-prefix caching

- Mechanism: reuse only if the serialized prefix matches exactly at provider cache breakpoints.
- Complexity: `O(n)` to serialize `n` prompt blocks; provider lookup cost is externalized.
- Invariant: all bytes before the cache breakpoint must be stable across requests.
- Failure mode: silent cache miss when a single early block changes, the shared prefix is too short, or the wrong cache key is reused.

#### Semantic caching

- Mechanism: embed the query plus normalized task metadata, then search a cache index by similarity.
- Complexity: `O(log n)` or `O(k)` approximate lookup depending on index type; revalidation adds a bounded constant step.
- Invariant: semantic hits require post-retrieval validation against tenant, policy version, and freshness constraints.
- Failure mode: false positives that return a plausible but wrong answer unless confidence thresholds and guard predicates are enforced.

#### Query-aware compression

- Mechanism: score tokens or chunks by relevance to the current question, then keep high-yield spans and supporting structure.
- Complexity: typically `O(n log n)` when ranking `n` chunks; summarization-based compression may add an inference pass.
- Invariant: compression must preserve identifiers, numbers, and cited evidence required for downstream correctness.
- Failure mode: quality drift rather than infrastructure failure; aggressive compression often looks "fluent but incomplete."

### Key invariants

- Prompt packaging must be canonical, versioned, and tenant-safe.
- External data must never be merged into the instruction layer.
- Cacheability is a serialization problem before it is an optimization problem.
- Short-term execution state and long-term memory must be recoverable independently.
- Token budget decisions must be explainable after the fact from logs and traces.

> ⚠️ Gap: Public provider documentation is strong on cache thresholds, breakpoints, and billing, but thin on formal proofs of convergence for adaptive trimming or on provider-managed cache consistency semantics across regions.

## 3. Token Economics & NFR Analysis

### Cost formulas

Use live vendor list prices for the selected model tier. Let:

- `P_in` = uncached input price in `$ / 1M tokens`
- `P_out` = output price in `$ / 1M tokens`
- `T_u` = uncached dynamic input tokens per run
- `T_cr` = cached-read tokens per run
- `T_cw` = cache-write tokens per run
- `T_out` = output tokens per run

#### OpenAI / exact-prefix cache economics

OpenAI prompt caching exposes distinct read and write buckets, with published multipliers of `0.1x` for cached reads and `1.25x` for writes on GPT-5.6-era caching.

`$ per 1k runs = 1000 * ((T_u * P_in) + (T_cr * 0.1 * P_in) + (T_cw * 1.25 * P_in) + (T_out * P_out)) / 1_000_000`

Assumption set for a support assistant:

- Static prefix: `2,400` tokens
- Dynamic user/context delta: `600` tokens
- Output: `450` tokens
- Warm path: first run writes the prefix, subsequent runs read it

Derived steady-state formula:

`$ per 1k runs = 1000 * (((600 * P_in) + (2400 * 0.1 * P_in) + (450 * P_out)) / 1_000_000)`

No-cache baseline:

`$ per 1k runs = 1000 * (((3000 * P_in) + (450 * P_out)) / 1_000_000)`

The savings come from replacing `2,400` uncached tokens with `2,400` cached-read tokens on repeated calls.

#### Anthropic / breakpoint cache economics

Anthropic publishes `1.25x` base input for 5-minute writes, `2x` for 1-hour writes, and `0.1x` for reads.

`$ per 1k runs (5m TTL) = 1000 * ((T_u * P_in) + (T_cr * 0.1 * P_in) + (T_cw * 1.25 * P_in) + (T_out * P_out)) / 1_000_000`

Break-even intuition:

- 5-minute cache: second use is already cheaper than repeating uncached input.
- 1-hour cache: third use is typically where the write premium amortizes.

#### Gemini explicit cache economics

Gemini explicit caches differ because storage rent is separate from cache-use price.

`$ per 1k runs = 1000 * ((T_u * P_in) + (T_cache_create * P_in) / R + (T_cache_use * P_cache_use) + (T_out * P_out)) / 1_000_000 + storage_amortized`

Where:

- `R` = number of runs sharing the cache object
- `storage_amortized = (cache_tokens / 1_000_000) * storage_rate_per_hour * hours_held / (R / 1000)`

Concrete storage example using figures documented for some Gemini `2.5 Pro` tiers:

- Shared cache size: `100,000` tokens
- Storage rate: `$4.50 / 1M tokens / hour`
- Held for `0.25` hours
- Shared by `1,000` runs

Then:

`storage_amortized = (100000 / 1_000_000) * 4.50 * 0.25 = $0.1125 per 1k runs`

That storage overhead is small when reuse is high, but becomes material when teams create many long-lived low-hit caches.

### Latency targets

Because vendors rarely publish hard percentile latency distributions, the numbers below should be treated as platform SLO targets, not provider guarantees.

- `p50 <= 1.2s`: achieved via semantic-cache hits, streaming-first UX, and local prompt assembly.
- `p95 <= 4.0s`: requires cache reuse, bounded retrieval fan-out, and concurrency caps on slow tools.
- `p99 <= 8.0s`: requires deadline propagation, circuit breakers, and deterministic fallbacks for partial outages.

Route-level targets:

- Semantic cache hit: `p50 80ms`, `p95 180ms`, `p99 350ms`
- Exact-prefix cached model call: `p50 900ms`, `p95 2.5s`, `p99 5s`
- Cold path with retrieval/compression: `p50 2.5s`, `p95 6s`, `p99 12s`

Mitigations by tier:

- `p50`: precompute canonical prefixes, keep thread checkpoints hot, stream the first token quickly.
- `p95`: cap retrieval breadth, pin frequent workloads to warm caches, compress only when the expected savings exceed the extra preprocessing latency.
- `p99`: enforce deadlines, shed load before queue collapse, and return reduced-capability answers instead of timing out entire workflows.

> ⚠️ Gap: Public sources provide limited hard percentile data for OpenAI, Anthropic, and Gemini under sustained enterprise load. Internal SLOs must be validated with your own traces, not inferred from pricing pages or quota docs.

### Throughput and back-pressure

For capacity planning, think in effective token throughput, not just requests per second.

- Example target: `500 rps` ingress with `70%` semantic-cache hit rate, `20%` exact-prefix cached inference, `10%` cold path.
- If Anthropic-style cache reads do not count toward ITPM, cache hits can raise effective total input throughput by multiples rather than percentages.
- Use token-bucket admission control on request count and token volume.
- Bound the queue length per tenant and per model tier.
- Apply back-pressure in this order: delay low-priority traffic, drop speculative background jobs, downgrade model tier, then serve deterministic fallback.

Operational rules:

- Never let retries bypass the same admission controller as original requests.
- Use separate concurrency pools for tool calls, cached inference, and cold inference.
- Monitor `queue_wait_ms`, `deadline_budget_ms`, `cached_tokens`, and `fallback_rate` together; any one metric in isolation is misleading.

### Availability, RPO, RTO, compliance

- Availability target: `99.9%` for interactive assistant responses, `99.95%` for audit log ingestion, `99.99%` for policy distribution.
- RPO target: `<= 1 minute` for thread checkpoints and audit records; `<= 15 minutes` for derived semantic-cache entries.
- RTO target: `<= 15 minutes` for regional inference failover; `<= 60 minutes` for full rebuild of noncritical caches.

Compliance discussion:

- `SOC 2` / `ISO 27001`: require repeatable controls, least privilege, traceability, and change-managed prompt/policy versions.
- `GDPR` / `CCPA`: require minimization, retention control, deletion semantics for memory stores, and data-subject traceability.
- `HIPAA` or regulated workloads: require explicit PHI/PII filtering before model submission, tamper-evident audit logs, and tenant-isolated storage.

The practical conclusion is that semantic caches are usually disposable, but checkpoints, policy versions, and audit records are not.

## 4. Distributed Resilience & Security

### Durable execution pattern

Use a workflow engine such as Temporal, LangGraph with durable checkpoints, or Kafka-backed orchestrators for anything that spans multiple model/tool steps.

Recommended pattern:

1. Assign a `workflow_id` and `correlation_id` at ingress.
2. Persist input envelope, tenant, policy version, and deadline before the first model call.
3. Checkpoint after every super-step: retrieval complete, compression complete, model response complete, tool response complete.
4. Store large tool outputs by reference, not by prompt duplication.
5. On resume, rebuild prompt state from checkpoint plus durable memory rather than replaying the full user transcript.
6. Route exhausted or poison tasks to a dead-letter queue with preserved evidence.

### Failure taxonomy

- `Transient`: 429s, 5xxs, network flaps, temporary tool timeouts. Retry with jitter and budget awareness.
- `Persistent`: invalid credentials, malformed schemas, revoked entitlements, tenant-policy violations. Do not retry blindly.
- `Semantic`: low-confidence retrieval, prompt-packing error, compression drift, model hallucination. Trigger validation or downgrade behavior.
- `Poison-pill`: specific payloads that deterministically fail serialization, token budgeting, or provider validation. Quarantine with signatures and sample payload hashes.

Required controls:

- Idempotency key per user-visible action and per tool invocation.
- Checkpoint versioning so replays use the same policy pack that created the original run.
- Dead-letter retention long enough for compliance review and bug forensics.

### Circuit breaker model

The inference path should expose explicit breaker states:

- `Closed`: normal operation.
- `Open`: recent failure rate or latency breach exceeds threshold; no new traffic to the dependency.
- `Half-open`: limited probes permitted; success closes the breaker, failure reopens it.

Breakers should exist independently for:

- Primary model provider
- Secondary model provider
- Semantic cache
- Retrieval backend
- Write-capable tools

This avoids a single failing component forcing a full system brownout.

### Fallback chain

A production fallback chain for context-heavy systems should be:

1. Semantic cache result if confidence and freshness gates pass.
2. Primary model with exact-prefix or explicit cache.
3. Secondary model with a smaller context package.
4. Deterministic rules-based answer or partial summary.
5. User-visible degraded response with next-step guidance.

The fallback chain must preserve the same correlation IDs and audit lineage across hops.

### Zero-Trust MCP and governance

Tool connectivity should assume the model is untrusted with respect to authorization:

- Use OAuth 2.1 with PKCE `S256`, resource indicators, and resource-specific tokens for MCP servers.
- Never pass upstream bearer tokens through the agent to downstream APIs.
- Enforce tool-level RBAC outside the model. Hints like `allowed_callers` are advisory, not a security boundary.
- Require human confirmation for high-impact writes even if the model "looks confident."

PII and audit pipeline:

1. Detect sensitive fields with schema-aware classifiers and regex backstops.
2. Redact or tokenize before logging or long-term memory writes.
3. Preserve reversible mappings only in isolated vault systems when business policy requires re-identification.
4. Emit immutable audit events for prompt version, memory IDs, tool requests, tool responses, and user-visible outputs.

> ⚠️ Gap: Public materials remain thin on provider-side cache replication guarantees, redact-before-log internals, and sandbox substrate details for hosted tool execution. Enterprises should assume those internals are opaque and build their own control evidence around ingress, egress, and audit boundaries.

## 5. Production Enterprise Code

The snippets below are runnable Python and demonstrate canonical context packing plus resilient inference with retries, jitter, circuit breaking, fallback chains, structured logging with correlation IDs, and graceful degradation.

### Canonical context packer

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class ContextBlock:
    kind: str
    text: str
    priority: int
    tokens: int
    mutable: bool = False


class ContextPacker:
    def __init__(self, token_budget: int, reserve_for_output: int = 512) -> None:
        if reserve_for_output >= token_budget:
            raise ValueError("reserve_for_output must be smaller than token_budget")
        self.token_budget = token_budget
        self.reserve_for_output = reserve_for_output

    def pack(self, blocks: Iterable[ContextBlock]) -> dict:
        usable_budget = self.token_budget - self.reserve_for_output
        ordered = sorted(
            blocks,
            key=lambda block: (
                block.mutable,      # immutable first for cache stability
                -block.priority,    # then most important first
                block.kind,
            ),
        )

        selected: List[ContextBlock] = []
        used_tokens = 0
        for block in ordered:
            if used_tokens + block.tokens > usable_budget:
                continue
            selected.append(block)
            used_tokens += block.tokens

        prefix_blocks = [block for block in selected if not block.mutable]
        canonical_prefix = json.dumps(
            [{"kind": b.kind, "text": b.text} for b in prefix_blocks],
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=True,
        )
        cache_key = hashlib.sha256(canonical_prefix.encode("utf-8")).hexdigest()

        return {
            "cache_key": cache_key,
            "used_tokens": used_tokens,
            "blocks": selected,
            "prefix_tokens": sum(block.tokens for block in prefix_blocks),
            "dynamic_tokens": sum(block.tokens for block in selected if block.mutable),
        }


if __name__ == "__main__":
    packer = ContextPacker(token_budget=4096, reserve_for_output=512)
    prompt = packer.pack(
        [
            ContextBlock("policy", "answer with citations", priority=100, tokens=120, mutable=False),
            ContextBlock("tools", '{"search": "enabled"}', priority=95, tokens=180, mutable=False),
            ContextBlock("document", "customer contract clauses...", priority=90, tokens=1400, mutable=False),
            ContextBlock("memory", "tenant prefers concise summaries", priority=60, tokens=90, mutable=True),
            ContextBlock("question", "what termination obligations apply?", priority=99, tokens=40, mutable=True),
        ]
    )
    print(prompt["cache_key"])
    print(prompt["used_tokens"], prompt["prefix_tokens"], prompt["dynamic_tokens"])
```

### Resilient inference service

```python
from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "timestamp": int(time.time() * 1000),
        }
        for key in ("correlation_id", "provider", "breaker_state", "attempt"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


logger = logging.getLogger("context-engineering")
handler = logging.StreamHandler()
handler.setFormatter(StructuredJsonFormatter())
logger.handlers = [handler]
logger.setLevel(logging.INFO)


class FailureCategory(Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class ProviderError(Exception):
    def __init__(self, message: str, category: FailureCategory) -> None:
        super().__init__(message)
        self.category = category


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout_s: float = 5.0
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at: float = 0.0

    def before_call(self) -> None:
        if self.state == BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
            else:
                raise ProviderError("circuit open", FailureCategory.TRANSIENT)

    def on_success(self) -> None:
        self.state = BreakerState.CLOSED
        self.failures = 0
        self.opened_at = 0.0

    def on_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


def retry_with_backoff(
    fn: Callable[[], str],
    correlation_id: str,
    provider: str,
    max_attempts: int = 3,
    base_delay_s: float = 0.2,
) -> str:
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except ProviderError as exc:
            logger.warning(
                f"provider call failed: {exc}",
                extra={
                    "correlation_id": correlation_id,
                    "provider": provider,
                    "attempt": attempt,
                },
            )
            if exc.category == FailureCategory.PERMANENT or attempt == max_attempts:
                raise
            sleep_s = base_delay_s * (2 ** (attempt - 1)) + random.uniform(0, 0.1)
            time.sleep(sleep_s)
    raise RuntimeError("unreachable")


class SimulatedProvider:
    def __init__(self, name: str, failure_pattern: list[str]) -> None:
        self.name = name
        self.failure_pattern = failure_pattern
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= len(self.failure_pattern):
            kind = self.failure_pattern[self.calls - 1]
            if kind == "transient":
                raise ProviderError(f"{self.name} temporary outage", FailureCategory.TRANSIENT)
            if kind == "permanent":
                raise ProviderError(f"{self.name} schema rejected", FailureCategory.PERMANENT)
        return f"{self.name} answer for: {prompt[:40]}"


class ResilientInferenceService:
    def __init__(self, primary: SimulatedProvider, secondary: SimulatedProvider) -> None:
        self.primary = primary
        self.secondary = secondary
        self.primary_breaker = CircuitBreaker()
        self.secondary_breaker = CircuitBreaker()

    def answer(self, prompt: str, semantic_cache_hit: Optional[str] = None) -> str:
        correlation_id = str(uuid.uuid4())

        if semantic_cache_hit:
            logger.info(
                "served from semantic cache",
                extra={"correlation_id": correlation_id, "provider": "semantic-cache"},
            )
            return semantic_cache_hit

        for provider, breaker in (
            (self.primary, self.primary_breaker),
            (self.secondary, self.secondary_breaker),
        ):
            try:
                breaker.before_call()
                result = retry_with_backoff(
                    fn=lambda provider=provider: provider.generate(prompt),
                    correlation_id=correlation_id,
                    provider=provider.name,
                )
                breaker.on_success()
                logger.info(
                    "provider succeeded",
                    extra={
                        "correlation_id": correlation_id,
                        "provider": provider.name,
                        "breaker_state": breaker.state.value,
                    },
                )
                return result
            except ProviderError:
                breaker.on_failure()
                logger.warning(
                    "provider unavailable, escalating fallback",
                    extra={
                        "correlation_id": correlation_id,
                        "provider": provider.name,
                        "breaker_state": breaker.state.value,
                    },
                )

        logger.error(
            "all providers failed, returning graceful degradation response",
            extra={"correlation_id": correlation_id, "provider": "deterministic-fallback"},
        )
        return (
            "I cannot complete the full context-heavy answer right now. "
            "Here is the minimum safe response: request accepted, evidence retained, "
            "and the workflow can be resumed from the last durable checkpoint."
        )


if __name__ == "__main__":
    primary = SimulatedProvider("gpt-primary", ["transient", "transient", "transient"])
    secondary = SimulatedProvider("claude-secondary", [])
    service = ResilientInferenceService(primary=primary, secondary=secondary)
    print(service.answer("Summarize the contract obligations for tenant ACME."))
```

Why this code matters:

- Retries use exponential backoff with jitter and stop on permanent failures.
- Circuit breakers explicitly transition `closed -> open -> half-open`.
- Fallback chain is semantic cache -> primary provider -> secondary provider -> deterministic degraded response.
- All log lines carry a `correlation_id`, and provider identity is preserved across fallback hops.
- Graceful degradation returns a bounded, safe response instead of failing the entire workflow.

## 6. Architectural System Design Scenarios

### Scenario 1: Global support copilot at 100k requests/minute

**Problem statement.** Design a multi-tenant support copilot that answers policy, FAQ, and troubleshooting questions at `100,000` requests/minute with a user-facing target of sub-`5s` `p99`, while keeping model cost predictable across tenants with highly repetitive prompts.

**Proposed architecture.**

```text
┌─────────────────────── Scenario 1 ───────────────────────┐
│ Web / Chat Clients                                        │
└──────────────┬────────────────────────────────────────────┘
               v
      ┌─────────────────┐
      │ API + AuthN/Z   │
      └──────┬──────────┘
             v
      ┌─────────────────┐      ┌─────────────────────────┐
      │ Semantic Cache  ├─────>│ Exact-Prefix Prompt     │
      │ (tenant scoped) │      │ Cache / Provider Cache  │
      └──────┬──────────┘      └────────────┬────────────┘
             │                               v
             │                      ┌─────────────────────┐
             └─────────────────────>│ Model Router        │
                                    └──────────┬──────────┘
                                               v
                                    ┌─────────────────────┐
                                    │ MCP Tool Proxy      │
                                    └──────────┬──────────┘
                                               v
                                    ┌─────────────────────┐
                                    │ CRM / KB / Status   │
                                    └─────────────────────┘
```

Technology choices:

- Redis or equivalent for tenant-scoped semantic cache with freshness metadata.
- Provider exact-prefix caching for policy packs, style rules, and tool manifests.
- Separate admission pools for cache hits versus cold-path model calls.
- Prompt canonicalization service so schema serialization does not drift.

**Trade-off evaluation matrix.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Semantic cache + exact-prefix cache + model router | Lowest steady-state spend on repetitive traffic | Best p50 and strong p95/p99 | Medium | Strong if tenant scoping and policy versioning are enforced | Very high |
| Brute-force long-context prompting only | Highest | Acceptable p50, weakest p99 under load | Low | Medium; fewer moving parts but more prompt exposure | Limited by provider cost and token quotas |
| RAG-first for every request | Medium | Moderate; retrieval adds fixed overhead | High | Strong when retrieval ACLs are correct | High, but operationally heavier |

**Decision rationale.** The hybrid cache-first approach wins because this workload is repetition-heavy. Exact-prefix caching exploits deterministic shared prefixes, while semantic caching absorbs paraphrase variants and removes model calls entirely for the hottest intents. A pure long-context design is simplest to ship but is too expensive at `100k` requests/minute, and a mandatory retrieval hop on every request adds unnecessary latency to questions that are already covered by stable policy context.

### Scenario 2: Regulated document-analysis agent with durable review workflows

**Problem statement.** Design a regulated enterprise agent that reviews contracts and internal policy documents, supports multi-step analyst workflows over hours or days, and must provide full audit lineage, `RPO <= 1 minute`, and resumability after partial outages.

**Proposed architecture.**

```text
┌─────────────────────── Scenario 2 ───────────────────────┐
│ Analyst UI                                                │
└──────────────┬────────────────────────────────────────────┘
               v
      ┌─────────────────┐
      │ Workflow Engine │
      │ (Temporal/LG)   │
      └──────┬──────────┘
             ├───────────────┐
             v               v
      ┌──────────────┐  ┌──────────────┐
      │ Checkpoints  │  │ Audit Ledger │
      └──────┬───────┘  └──────┬───────┘
             v                 v
      ┌──────────────────────────────────┐
      │ Context Assembler + PII Filter   │
      └──────────────┬───────────────────┘
                     v
      ┌──────────────────────────────────┐
      │ Explicit Cache / Prompt Cache    │
      └──────────────┬───────────────────┘
                     v
      ┌──────────────────────────────────┐
      │ Model Router + MCP Policy Proxy  │
      └──────────────┬───────────────────┘
                     v
      ┌──────────────────────────────────┐
      │ DMS / Policy DB / Approval APIs  │
      └──────────────────────────────────┘
```

Technology choices:

- Temporal or LangGraph durable checkpoints for resumable long-running workflows.
- Explicit or provider-managed caches for repeated interaction with the same document set.
- PII/PHI filtering before model submission and before long-term logging.
- Immutable audit events keyed by `workflow_id`, `correlation_id`, `tenant_id`, and prompt/policy version.

**Trade-off evaluation matrix.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Durable workflow + explicit cache + policy proxy | Medium | Strong for repeated document sessions | High | Strongest; best auditability and resumability | High |
| Self-hosted RAG + short prompts only | Medium to high | Moderate; retrieval required on every step | High | Strong if ACLs are robust, weaker on session reuse | High |
| Single-turn long-context prompting without durable state | High | Good for small pilots, poor for multi-step workflows | Low | Weakest; poor audit lineage and resume story | Low |

**Decision rationale.** The durable-workflow design wins because the problem is not just "answer a question about a document"; it is "maintain a regulated review process over time." Checkpoints, audit lineage, and explicit policy boundaries matter more than raw prompt convenience. A pure RAG approach still needs workflow durability and audit evidence, while single-turn prompting collapses state, execution history, and compliance evidence into one fragile artifact.
