# LLM Foundations

**Topic**: Transformers, reasoning, function calling, structured output  
**Source note**: `research_cursor/research/01-llm-foundations.md`

This module turns the raw research into an enterprise-study reference for how modern LLM systems actually behave in production: parallel prefill plus serial decode at the model layer, application-owned tool loops above it, constrained decoding for machine-readable outputs, and explicit reliability controls around every external side effect.

## 1. System Topology & Data Flow

The critical architectural split is between:

- the **control plane**, which owns routing, policy, retries, budgeting, orchestration, and audit;
- the **data plane**, which owns tokenization, inference, tool invocation payloads, and response streaming.

```text
┌───────────────────── Client / Caller ─────────────────────┐
│ user prompt | request metadata | tenant | correlation id │
└────────────────────────────┬──────────────────────────────┘
                             │
                             v
┌──────────────────── Control Plane ────────────────────────┐
│ API Gateway -> AuthN/AuthZ -> Prompt Builder             │
│              -> Model Router -> Policy Engine            │
│              -> Token Budgeter -> Workflow Orchestrator  │
└───────────────┬──────────────────────┬────────────────────┘
                │                      │
                │ checkpoint/run state │ telemetry/events
                v                      v
     ┌──────────────────┐    ┌─────────────────────────────┐
     │ Persistence      │    │ Observability Sinks         │
     │ run journal      │    │ logs | traces | metrics     │
     │ tool outputs     │    │ cost events | alerts        │
     │ idempotency keys │    └─────────────────────────────┘
     └─────────┬────────┘
               │
               v
┌────────────────────── Data Plane ────────────────────────┐
│ tokenizer -> prefill attention -> decode loop ->         │
│ {answer | tool_call | refusal | incomplete}              │
└───────────────┬──────────────────────┬────────────────────┘
                │                      │
                │ tool schema          │ final structured output
                v                      v
      ┌──────────────────────┐    ┌────────────────────────┐
      │ Tool Proxy / MCP GW  │    │ Schema Validator       │
      │ OAuth2.1 + PKCE      │    │ CFG / JSON / strict    │
      │ RBAC + PII filters   │    │ business rule checks   │
      └──────────┬───────────┘    └────────────────────────┘
                 │
                 v
      ┌─────────────────────────────────────────────────────┐
      │ External systems: DBs | search | ERP | CRM | APIs  │
      └─────────────────────────────────────────────────────┘
```

### Request-flow narrative

1. The caller submits a prompt plus tenant context, correlation ID, and optional latency/cost tier.
2. The control plane authenticates the request, resolves allowed tools, injects stable system instructions, and computes a token budget before any model call is made.
3. The data plane tokenizes the request and runs **prefill**, where all current prompt tokens attend in parallel. This is why prompt construction, retrieval augmentation, and cache-eligible prefixes strongly affect total latency.
4. The decoder then emits tokens autoregressively. At any decode step the model can terminate with:
   - a final answer,
   - a schema-constrained object,
   - a tool call,
   - a refusal,
   - or an incomplete response if token or context limits are hit.
5. If the model emits a tool call, the application, not the model, executes it. The orchestrator writes a checkpoint before dispatch, the tool proxy enforces RBAC and PII rules, and the result is appended back into the transcript.
6. The next turn repeats with preserved provider-specific artifacts such as reasoning items or thinking blocks, because these are part of the semantic state of the run.
7. Every step emits structured logs, metrics, traces, token counters, and audit records so operators can answer: what happened, why it happened, what it cost, and whether it was authorized.

The topology matters because transformers solve token prediction, not enterprise workflow durability. The closer a step is to the model, the more it is probabilistic; the closer it is to the control plane, the more it must be deterministic and replay-safe.

## 2. Core Mechanics & Algorithms

### Transformer fundamentals

The core primitive is scaled dot-product attention:

\[
\mathrm{Attention}(Q,K,V) = \mathrm{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V
\]

For a sequence length `n` and model width `d`, a dense self-attention layer has:

- **prefill compute**: `O(n^2 * d)` due to all-pairs token attention;
- **prefill memory**: `O(n^2)` for the attention map, plus KV storage;
- **decode with KV cache**: each new token avoids recomputing prior keys/values, but still attends over the growing history, so per-token decode is effectively `O(n * d)` rather than `O(n^2 * d)` over the full prefix.

That asymmetry explains the dominant performance pattern in production:

- **long prompts hurt prefill latency**;
- **long outputs hurt decode latency and cost**;
- **reasoning models amplify output-side spend** because internal reasoning tokens behave like billable output tokens.

### Why transformers displaced recurrence

RNNs and LSTMs require `O(n)` sequential steps to move information across a sequence. Self-attention reduces the path length between any two positions to a constant number of layer transitions, making training-time parallelism dramatically better. The original Transformer paper established the winning economic trade: more FLOPs overall, but much better hardware utilization and much lower wall-clock training time than recurrence-heavy alternatives.

### Function calling as a state machine

Function calling is not "the model executed a function." It is:

1. the model predicts a schema-valid intent;
2. the orchestrator decides whether that intent is allowed;
3. the application executes it;
4. the tool result becomes new context.

A useful run-state machine is:

```text
RECEIVED
  -> PREFILL
  -> DECODE
  -> {FINAL_ANSWER | TOOL_CALL | REFUSAL | INCOMPLETE}
TOOL_CALL
  -> AUTHORIZE
  -> EXECUTE_TOOL
  -> CHECKPOINT_RESULT
  -> PREFILL
INCOMPLETE
  -> RETRY_WITH_BUDGET_ADJUSTMENT | FALLBACK | FAIL
REFUSAL
  -> SAFE_RESPONSE | HUMAN_REVIEW
FINAL_ANSWER
  -> VALIDATE_SCHEMA
  -> COMMIT_RESULT
```

Key invariants:

- every mutating tool call must carry an **idempotency key**;
- every tool boundary must be **checkpointed**;
- every response path must branch explicitly on **refusal**, **tool call**, and **incomplete** rather than assuming "assistant text";
- schema-valid output is necessary but not sufficient, because semantic correctness and authorization still have to be checked downstream.

### Structured output as constrained decoding

Structured output systems convert a JSON Schema or grammar into a constrained decoder that masks invalid next tokens to zero probability. Operationally, this changes the failure surface:

- without constrained decoding, failures are often parser-level: malformed JSON, invalid enums, missing braces;
- with constrained decoding, failures move upward into business semantics: wrong customer ID, wrong date range, unauthorized action, or logically inconsistent fields.

This is a major improvement because parser failures are low-signal noise, while semantic failures can be classified, retried, escalated, or blocked with policy.

### Reasoning loops and convergence

Reasoning models introduce an internal planner-like phase before visible output or tool use. This improves performance on hard tasks, but it does **not** guarantee convergence in the mathematical sense. In production, convergence is engineered through bounded controls:

- maximum iteration count;
- maximum cost budget;
- maximum wall-clock runtime;
- tool allowlists and denial policies;
- deterministic terminal states such as `COMPLETE`, `DEGRADED`, `ESCALATED`, or `FAILED_PERMANENTLY`.

Without those bounds, long-horizon tool loops can wander, repeat, or spend invisible reasoning tokens without producing business value.

## 3. Token Economics & NFR Analysis

### Cost formulas

Use this baseline formula for **$ per 1k runs**:

```text
$ per 1k runs =
1000 * (
  (uncached_input_tokens * input_rate_per_mtok) +
  (cached_input_tokens   * cached_input_rate_per_mtok) +
  (cache_write_tokens    * cache_write_rate_per_mtok) +
  (output_tokens         * output_rate_per_mtok)
) / 1_000_000
```

Where:

- `output_tokens` includes visible output plus any billed reasoning tokens;
- `cache_write_tokens` is usually non-zero only on warmup or prompt-version changes;
- the stable prompt prefix must exceed the provider's cacheability threshold to matter economically.

#### Example A: deterministic extraction on `gpt-5.6-luna`

Assumptions:

- uncached input = `900` tokens
- cached input = `1,100` tokens
- cache write = `0` after warmup
- output = `250` tokens
- pricing = `$0.20/MTok` input, `$0.02/MTok` cached input, `$0.25/MTok` cache write, `$1.20/MTok` output

```text
$ per 1k runs =
1000 * (
  (900  * 0.20) +
  (1100 * 0.02) +
  (0    * 0.25) +
  (250  * 1.20)
) / 1_000_000
= 1000 * 502 / 1_000_000
= $0.502 per 1k runs
```

This is why small strict-output models dominate ETL-style workloads: low output size, high cache reuse, and near-zero parser retries.

#### Example B: tool-augmented reasoning on `gpt-5.6-terra`

Assumptions:

- uncached input = `2,500` tokens
- cached input = `1,500` tokens
- cache write = `0` after warmup
- output = `1,800` tokens, including hidden reasoning and visible answer/tool arguments
- pricing = `$2.00/MTok` input, `$0.20/MTok` cached input, `$2.50/MTok` cache write, `$12.00/MTok` output

```text
$ per 1k runs =
1000 * (
  (2500 * 2.00) +
  (1500 * 0.20) +
  (0    * 2.50) +
  (1800 * 12.00)
) / 1_000_000
= 1000 * 26_900 / 1_000_000
= $26.90 per 1k runs
```

The lesson is structural: for reasoning-heavy workloads, **output-side governance** is often a bigger savings lever than prompt compression.

### Latency targets

Provider docs publish quotas and pricing more reliably than percentile latency, so production teams should set internal SLOs by workload class.

> ⚠️ Gap: public primary sources remain thin on provider-guaranteed p50/p95/p99 latency by model tier under real production load. The targets below are engineering SLOs, not vendor SLAs.

Recommended internal targets:

| Workload class | p50 target | p95 target | p99 target | Main mitigation levers |
| --- | ---: | ---: | ---: | --- |
| Strict extraction / classification | `< 0.9s` | `< 2.5s` | `< 5s` | prompt caching, short outputs, response size caps, small model tier |
| Tool-augmented reasoning | `< 3s` | `< 8s` | `< 15s` | bounded reasoning effort, streaming, early tool dispatch, fallback on slow path |
| Regulated human-in-loop actions | `< 4s` first token | `< 12s` action proposal | `< 20s` finalization | asynchronous completion, durable workflow, UI progress updates |

Operational guidance:

- optimize **prefill** with cacheable prefixes, prompt compaction, and retrieval filtering;
- optimize **decode** with response schemas, token caps, and concise tool contracts;
- route by intent so extraction traffic never pays reasoning-model latency.

### Throughput and back-pressure

A first-pass capacity formula is:

```text
effective_rps = min(
  RPM / 60,
  TPM / (avg_input_tokens + avg_output_tokens) / 60
)
```

For tool-augmented agents, use:

```text
effective_run_rps = provider_rps / average_model_turns_per_run
```

because one business request often expands into multiple model calls.

Back-pressure design should include:

- token-bucket admission control keyed by tenant and model tier;
- queue-length thresholds for `ACCEPT`, `DEGRADE`, and `REJECT`;
- retry scheduling that honors `Retry-After` rather than free-running;
- concurrency caps per tool to avoid the model layer overwhelming downstream systems;
- circuit-breaker driven load shedding when external tools are degraded.

### Availability, recovery, and compliance

Baseline enterprise targets:

- **availability**: `99.9%` for non-critical assistants; `99.95%` for production document pipelines; higher only if the surrounding workflow justifies multi-region cost
- **RPO**: `<= 5 minutes` for run journals and audit events
- **RTO**: `<= 15 minutes` for orchestration plane recovery, `<= 60 minutes` for analytics backfills

Compliance discussion:

- **SOC 2 / ISO 27001**: required for most enterprise procurement paths
- **GDPR / data residency**: use regional deployments and avoid unnecessary transcript retention
- **HIPAA / PCI / SOX-adjacent workloads**: force PII detection and redact-before-log pipelines; isolate tool credentials by tenant and resource
- **Zero Data Retention / BYOK / customer-managed keys**: relevant when prompts or tool outputs contain regulated data or proprietary source material

If the application cannot replay a run safely after process death, it is not highly available, even if the model endpoint itself is.

## 4. Distributed Resilience & Security

### Durable execution

Hosted LLM APIs do not give you a Temporal-like workflow engine by default. Durable execution has to be built around the model call boundary.

The practical unit of durability is the **turn checkpoint**:

- before a model call: persist request envelope, prompt version, tool allowlist, token budget, and correlation ID;
- after a model call: persist the raw provider payload, reasoning artifacts, tool-call intent, and billable usage;
- before a tool call: persist an idempotency key and authorization decision;
- after a tool call: persist the tool result, redaction status, and retry count.

Common implementations:

- **Temporal** for long-lived workflows, replay, timers, and compensation steps;
- **Kafka + compacted state store** for event-driven agent loops with replayable journals;
- **Postgres or DynamoDB** for simpler single-service run ledgers with transactional idempotency.

### Failure taxonomy

| Failure class | Typical symptoms | Retry? | Required control |
| --- | --- | --- | --- |
| Transient provider failure | `429`, `503`, timeout, connection reset | Yes | exponential backoff, jitter, breaker metrics |
| Transient tool failure | downstream API timeout, lock contention | Yes | retry budget plus per-tool concurrency cap |
| Permanent request failure | invalid schema, bad auth, denied tool, unsupported model param | No | fail fast, return deterministic error class |
| Semantic failure | schema-valid but wrong business values | Maybe | business validation, re-prompt, human review |
| Poison-pill input | same request fails repeatedly across providers | No after threshold | dead-letter queue, quarantine, operator alert |
| State corruption | missing reasoning items / altered thinking blocks | No until repaired | checkpoint replay or run restart from last good boundary |
| Capacity failure | queue growth, acceleration-limit `429`s | Maybe later | load shedding, traffic shaping, admission control |

### Retries, circuit breakers, and fallback chains

Retries are appropriate only for failures likely to succeed later. A clean policy is:

- retry transient network and quota failures with exponential backoff and full jitter;
- do not retry permission denials, schema contract errors, or provider refusals as raw copies of the same request;
- trip a circuit breaker when failure rate or latency crosses a threshold;
- downgrade to a secondary model or deterministic fallback when the breaker is open.

Circuit breaker states should be explicit:

- `CLOSED`: traffic flows normally;
- `OPEN`: calls fail fast and immediately use fallback logic;
- `HALF_OPEN`: a small probe budget tests recovery before reopening the floodgate.

Recommended fallback chain:

```text
primary reasoning model
  -> secondary strict-output model
  -> deterministic rules engine / cached answer
  -> human review queue
```

The final stage is important. "Graceful degradation" in enterprise systems often means preserving correctness and auditability while sacrificing automation depth.

### Zero-Trust MCP and enterprise security

For tool access, use a Zero-Trust pattern:

```text
LLM Orchestrator
  -> MCP Gateway
  -> OAuth2.1 Authorization Server
  -> Resource Server Metadata Discovery
  -> Scoped Access Token
  -> Tool Proxy
  -> External System
```

Minimum controls:

- OAuth `2.1` with PKCE `S256` where supported
- resource indicators so tokens are bound to the intended tool/resource server
- tool-level RBAC with least privilege, ideally scoped by tenant, user role, and data domain
- approval workflows for mutating tools such as ticket creation, payment execution, or account changes

### PII filtering and auditability

A production redaction pipeline is:

```text
detect -> classify -> redact/tokenize -> execute -> store immutable audit event
```

Design rules:

- prompts and tool outputs are scanned before they enter logs or long-lived stores;
- reversible tokenization is restricted to tightly controlled services;
- audit records are append-only and carry actor, tenant, tool, decision basis, timestamp, and correlation ID;
- agent decisions and tool side effects are linked by chain-of-custody IDs so investigators can reconstruct who asked, what was proposed, what was executed, and why.

> ⚠️ Gap: public vendor documentation is still thin on provider-internal replay journals, lock managers, and the exact runtime isolation substrate behind hosted tool execution. Treat internal vendor durability as opaque and keep your own durable ledger.

## 5. Production Enterprise Code

The code below is intentionally runnable with the Python standard library. It demonstrates:

- retries with exponential backoff and jitter;
- circuit breakers with `closed -> open -> half-open`;
- fallback model chaining;
- structured logging with correlation IDs;
- graceful degradation to a deterministic parser;
- durable checkpoints around tool boundaries.

### Resilient structured-output orchestration

```python
from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Optional


logging.basicConfig(level=logging.INFO, format="%(message)s")
LOGGER = logging.getLogger("llm-runtime")


def log_event(event: str, correlation_id: str, **fields: Any) -> None:
    record = {
        "event": event,
        "correlation_id": correlation_id,
        "timestamp_ms": int(time.time() * 1000),
        **fields,
    }
    LOGGER.info(json.dumps(record, sort_keys=True))


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class TransientProviderError(RuntimeError):
    pass


class PermanentProviderError(RuntimeError):
    pass


@dataclass
class Invoice:
    invoice_id: str
    amount_usd: float
    currency: str
    vendor: str
    degraded: bool = False
    source: str = "llm"


def validate_invoice(payload: Dict[str, Any]) -> Invoice:
    required = {"invoice_id": str, "amount_usd": (int, float), "currency": str, "vendor": str}
    for key, expected_type in required.items():
        if key not in payload:
            raise PermanentProviderError(f"missing required field: {key}")
        if not isinstance(payload[key], expected_type):
            raise PermanentProviderError(f"invalid type for field: {key}")
    return Invoice(
        invoice_id=str(payload["invoice_id"]),
        amount_usd=float(payload["amount_usd"]),
        currency=str(payload["currency"]),
        vendor=str(payload["vendor"]),
    )


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout_s: float = 15.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.opened_at = 0.0

    def allow_call(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN and (time.time() - self.opened_at) >= self.recovery_timeout_s:
            self.state = CircuitState.HALF_OPEN
            return True
        return self.state == CircuitState.HALF_OPEN

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.opened_at = 0.0

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.time()


def with_retries(
    operation: Callable[[], Dict[str, Any]],
    correlation_id: str,
    max_attempts: int = 4,
    base_delay_s: float = 0.25,
) -> Dict[str, Any]:
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except TransientProviderError as exc:
            if attempt == max_attempts:
                log_event("retry_exhausted", correlation_id, attempt=attempt, error=str(exc))
                raise
            sleep_s = base_delay_s * (2 ** (attempt - 1)) + random.uniform(0, base_delay_s)
            log_event("retry_scheduled", correlation_id, attempt=attempt, sleep_s=round(sleep_s, 3), error=str(exc))
            time.sleep(sleep_s)


def primary_model_extract(document_text: str) -> Dict[str, Any]:
    if "FORCE_PRIMARY_TIMEOUT" in document_text:
        raise TransientProviderError("primary provider timeout")
    if "FORCE_PRIMARY_BAD_SCHEMA" in document_text:
        return {"invoice": "oops"}
    return {
        "invoice_id": "INV-10042",
        "amount_usd": 921.14,
        "currency": "USD",
        "vendor": "Acme Industrial Parts",
    }


def secondary_model_extract(document_text: str) -> Dict[str, Any]:
    if "FORCE_SECONDARY_TIMEOUT" in document_text:
        raise TransientProviderError("secondary provider timeout")
    return {
        "invoice_id": "INV-10042",
        "amount_usd": 921.14,
        "currency": "USD",
        "vendor": "Acme Industrial Parts",
    }


def deterministic_fallback_extract(document_text: str) -> Invoice:
    invoice_id_match = re.search(r"(INV-\d+)", document_text)
    amount_match = re.search(r"\$([0-9]+(?:\.[0-9]{2})?)", document_text)
    vendor_match = re.search(r"Vendor:\s*(.+)", document_text)
    return Invoice(
        invoice_id=invoice_id_match.group(1) if invoice_id_match else "UNKNOWN",
        amount_usd=float(amount_match.group(1)) if amount_match else 0.0,
        currency="USD",
        vendor=vendor_match.group(1).strip() if vendor_match else "UNKNOWN",
        degraded=True,
        source="deterministic_fallback",
    )


def extract_invoice(document_text: str, breaker: CircuitBreaker, correlation_id: str) -> Invoice:
    backends: Iterable[tuple[str, Callable[[str], Dict[str, Any]]]] = (
        ("primary", primary_model_extract),
        ("secondary", secondary_model_extract),
    )

    for backend_name, backend in backends:
        if not breaker.allow_call() and backend_name == "primary":
            log_event("breaker_open_skip_primary", correlation_id, backend=backend_name, state=breaker.state.value)
            continue

        try:
            response = with_retries(lambda: backend(document_text), correlation_id)
            invoice = validate_invoice(response)
            breaker.record_success()
            log_event("llm_extract_success", correlation_id, backend=backend_name, degraded=False)
            return invoice
        except TransientProviderError as exc:
            breaker.record_failure()
            log_event(
                "llm_extract_transient_failure",
                correlation_id,
                backend=backend_name,
                breaker_state=breaker.state.value,
                error=str(exc),
            )
        except PermanentProviderError as exc:
            log_event("llm_extract_permanent_failure", correlation_id, backend=backend_name, error=str(exc))

    degraded_invoice = deterministic_fallback_extract(document_text)
    log_event("graceful_degradation", correlation_id, fallback=degraded_invoice.source)
    return degraded_invoice


def main() -> None:
    correlation_id = str(uuid.uuid4())
    breaker = CircuitBreaker()
    sample_doc = """Vendor: Acme Industrial Parts
Invoice: INV-10042
Amount Due: $921.14
FORCE_PRIMARY_TIMEOUT
"""
    invoice = extract_invoice(sample_doc, breaker, correlation_id)
    print(invoice)


if __name__ == "__main__":
    main()
```

### Durable tool-loop checkpointing

```python
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class RunCheckpoint:
    run_id: str
    step: str
    payload: Dict[str, Any]


class RunStore:
    def __init__(self, path: str = "agent_runs.db") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_checkpoints (
                run_id TEXT NOT NULL,
                step TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                idempotency_key TEXT PRIMARY KEY,
                created_at_ms INTEGER NOT NULL
            )
            """
        )
        self.conn.commit()

    def append(self, checkpoint: RunCheckpoint) -> None:
        self.conn.execute(
            "INSERT INTO run_checkpoints(run_id, step, created_at_ms, payload_json) VALUES (?, ?, ?, ?)",
            (checkpoint.run_id, checkpoint.step, int(time.time() * 1000), json.dumps(checkpoint.payload, sort_keys=True)),
        )
        self.conn.commit()

    def claim_idempotency_key(self, idempotency_key: str) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO idempotency_keys(idempotency_key, created_at_ms) VALUES (?, ?)",
                (idempotency_key, int(time.time() * 1000)),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def load_run(self, run_id: str) -> List[RunCheckpoint]:
        rows = self.conn.execute(
            "SELECT run_id, step, payload_json FROM run_checkpoints WHERE run_id = ? ORDER BY created_at_ms ASC",
            (run_id,),
        ).fetchall()
        return [RunCheckpoint(run_id=row[0], step=row[1], payload=json.loads(row[2])) for row in rows]


def execute_tool(tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name == "lookup_customer_balance":
        return {"customer_id": tool_args["customer_id"], "balance_usd": 128.55}
    raise ValueError(f"unknown tool: {tool_name}")


def run_tool_loop(customer_id: str) -> List[RunCheckpoint]:
    run_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    store = RunStore()

    store.append(RunCheckpoint(run_id, "request_received", {"customer_id": customer_id, "correlation_id": correlation_id}))

    tool_call = {"name": "lookup_customer_balance", "arguments": {"customer_id": customer_id}}
    store.append(RunCheckpoint(run_id, "tool_selected", tool_call))

    idempotency_key = f"{run_id}:{tool_call['name']}:{customer_id}"
    if not store.claim_idempotency_key(idempotency_key):
        raise RuntimeError("duplicate tool execution blocked")

    store.append(RunCheckpoint(run_id, "tool_dispatch_started", {"idempotency_key": idempotency_key}))
    tool_result = execute_tool(tool_call["name"], tool_call["arguments"])
    store.append(RunCheckpoint(run_id, "tool_dispatch_completed", tool_result))

    final_answer = {
        "status": "COMPLETE",
        "message": f"Customer {customer_id} balance is ${tool_result['balance_usd']:.2f}",
    }
    store.append(RunCheckpoint(run_id, "final_answer", final_answer))
    return store.load_run(run_id)


if __name__ == "__main__":
    for checkpoint in run_tool_loop("cust-123"):
        print(checkpoint)
```

The first snippet shows runtime protection around a single structured-output request. The second shows the more important enterprise rule: every tool boundary is persisted so the run can be replayed, audited, or resumed after process death.

## 6. Architectural System Design Scenarios

### Scenario 1: High-volume document extraction platform

**Problem statement**

Design a multi-tenant document extraction service for invoices, claims, and onboarding packets handling `40,000` documents per minute with `p99 < 2s` for the extraction response, `99.95%` availability, and GDPR/SOC 2 controls. Outputs must be strict JSON and downstream systems cannot tolerate malformed payloads.

**Proposed architecture**

```text
┌───────────── Ingress ─────────────┐
│ upload API -> queue -> worker pool│
└────────────────┬──────────────────┘
                 v
┌─────────────────────────────────────────────┐
│ Prompt Cache + Schema Registry             │
│ stable instructions | versioned JSONSchema │
└────────────────┬────────────────────────────┘
                 v
┌─────────────────────────────────────────────┐
│ Small Strict-Output Model Tier             │
│ constrained decoding | short max outputs   │
└───────────────┬─────────────────────────────┘
                v
┌─────────────────────────────────────────────┐
│ Business Validator -> Normalizer -> Sink   │
│ required fields | range checks | dedupe    │
└───────────────┬─────────────────────────────┘
                v
┌─────────────────────────────────────────────┐
│ Storage + Telemetry + Audit                │
│ extracted JSON | token cost | bad-doc DLQ  │
└─────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**

| Option | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Hosted small model with strict structured outputs | Lowest for stable prompts and short outputs | Best p50/p95 | Low | Strong if paired with redaction and regional controls | High until provider quotas dominate |
| Frontier reasoning model for every document | High due to output/reasoning tokens | Worse p95/p99 | Low-medium | Similar provider controls, but larger transcript surface | High, but economically inefficient |
| Self-hosted vLLM with constrained decoding | Medium at scale, high fixed cost | Excellent if GPU fleet is tuned | Highest | Strongest data locality control | Very high if infra team is mature |

**Decision rationale**

Choose the **hosted small model with strict structured outputs** as the primary path. This workload is mostly deterministic, so parser reliability and cache economics matter more than general reasoning power. Add business validation after decoding because schema correctness is not semantic correctness. Self-hosted vLLM becomes attractive only when data-locality or sustained-volume economics justify owning GPU operations.

### Scenario 2: Regulated multi-tenant operations copilot

**Problem statement**

Design an internal operations copilot that can read tickets, query systems of record, and propose or execute approved actions across finance, support, and identity domains. It must support `10,000` user requests per minute, preserve full auditability, keep `RPO <= 5 minutes`, `RTO <= 15 minutes`, and require human approval for high-risk side effects. Latency target is `p95 < 8s` for read-only requests and `p95 < 15s` for approval-backed action flows.

**Proposed architecture**

```text
┌───────────── Employee UI / API ─────────────┐
│ chat | approvals | progress UI | evidence  │
└──────────────────┬──────────────────────────┘
                   v
┌─────────────────────────────────────────────┐
│ API Gateway -> AuthN -> Tenant Policy      │
│ user role | session policy | correlation id│
└──────────────────┬──────────────────────────┘
                   v
┌─────────────────────────────────────────────┐
│ Temporal Orchestrator                      │
│ checkpoints | timers | compensations       │
└───────────────┬─────────────────────────────┘
                v
┌─────────────────────────────────────────────┐
│ Reasoning Model Router                     │
│ primary model | secondary model | breaker  │
└───────┬───────────────────────┬─────────────┘
        │                       │
        v                       v
┌───────────────┐      ┌──────────────────────┐
│ MCP Gateway   │      │ Audit / Telemetry    │
│ OAuth2.1 RBAC │      │ immutable ledger     │
│ PII filters   │      │ trace + cost events  │
└───────┬───────┘      └──────────────────────┘
        v
┌─────────────────────────────────────────────┐
│ Tools: ticketing | CRM | ERP | IAM | search│
└─────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**

| Option | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Frontier reasoning model + Temporal + MCP gateway | Medium-high | Good if bounded by iteration and tool caps | Medium | Strong with durable audit and least-privilege tokens | High |
| Small model plus deterministic workflow rules | Lowest | Best | Medium | Strong, but poor task coverage for ambiguous cases | Moderate |
| Self-hosted open-weight agent stack | Potentially lower at large scale | Variable, highly tuning-dependent | Highest | Strongest isolation control if operated well | High, but operator-risk heavy |

**Decision rationale**

Choose the **frontier reasoning model + Temporal + MCP gateway**. This class of problem benefits from reasoning and tool use, but only if state is durable and tool access is treated as a Zero-Trust integration surface. The orchestrator, not the model, must own retries, approvals, and compensating actions. Deterministic-only flows are cheaper but fail too often on ambiguous cross-system requests; fully self-hosted stacks increase operational risk before they reduce business risk.

## Sources

- [Attention Is All You Need](https://arxiv.org/abs/1706.03762)
- [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Introducing Structured Outputs](https://openai.com/index/introducing-structured-outputs-in-the-api/)
- [OpenAI Reasoning Guide](https://developers.openai.com/api/docs/guides/reasoning)
- [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [OpenAI Pricing](https://developers.openai.com/api/docs/pricing)
- [OpenAI Rate Limits](https://developers.openai.com/api/docs/guides/rate-limits)
- [OpenAI Your Data](https://developers.openai.com/api/docs/guides/your-data)
- [Anthropic Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
- [Anthropic Strict Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)
- [Anthropic Extended Thinking](https://platform.claude.com/cookbook/extended-thinking-extended-thinking-with-tool-use)
- [Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Anthropic Rate Limits](https://platform.claude.com/docs/en/api/rate-limits)
- [MCP Authorization Spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [vLLM Structured Outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/)
- [Azure OpenAI Data Privacy](https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/openai/data-privacy)
- [BFCL 2025 Paper](https://proceedings.mlr.press/v267/patil25a.html)
