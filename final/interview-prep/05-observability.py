"""
LLM & Agent Observability -- Interview Prep Code Snippets

Covers the three observability layers (metrics, traces, audit), OpenTelemetry
instrumentation for LLM calls, tail-sampling decision logic, cost tracking,
PII-safe export pipelines, and burn-rate SLO alerting. All examples use stdlib
or lightweight stubs so the file is self-contained and runnable.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# =============================================================================
# --- Section 1: OpenTelemetry Trace Setup for LLM Calls ---------------------
# =============================================================================
# In production you'd use the real opentelemetry-api / opentelemetry-sdk.
# These stubs mirror the real API surface so the patterns are portable.

@dataclass
class Span:
    """Minimal OTel span stub.  Real spans live in opentelemetry.trace."""
    name: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: Optional[str] = None
    attributes: dict = field(default_factory=dict)
    status: str = "UNSET"       # UNSET | OK | ERROR
    start_ns: int = field(default_factory=time.monotonic_ns)
    end_ns: Optional[int] = None
    events: list = field(default_factory=list)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: str) -> None:
        self.status = status

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        self.events.append({"name": name, "attributes": attributes or {}})

    def end(self) -> None:
        self.end_ns = time.monotonic_ns()

    @property
    def duration_ms(self) -> float:
        if self.end_ns is None:
            return 0.0
        return (self.end_ns - self.start_ns) / 1_000_000


class Tracer:
    """Stub tracer that collects spans in memory."""

    def __init__(self, name: str = "llm-service"):
        self.name = name
        self.spans: list[Span] = []

    def start_span(
        self,
        name: str,
        *,
        parent: Span | None = None,
        attributes: dict | None = None,
    ) -> Span:
        span = Span(
            name=name,
            trace_id=parent.trace_id if parent else uuid.uuid4().hex,
            parent_id=parent.span_id if parent else None,
        )
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        self.spans.append(span)
        return span


def create_llm_span(tracer: Tracer, *, model: str, parent: Span | None = None) -> Span:
    """Create an OTel-GenAI-convention span for an LLM call.

    Key convention: span name = '{operation} {model}'.
    Required attributes: gen_ai.provider.name, gen_ai.operation.name,
    gen_ai.request.model.  Content is OFF by default in production.
    """
    span = tracer.start_span(
        f"chat {model}",
        parent=parent,
        attributes={
            # --- required by OTel GenAI semantic conventions ---
            "gen_ai.provider.name": model.split(":")[0] if ":" in model else "unknown",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
            # --- span kind CLIENT for outbound model call ---
            "span.kind": "CLIENT",
        },
    )
    return span


# =============================================================================
# --- Section 2: Custom Span Attributes (Tokens, Model, Cost) ----------------
# =============================================================================

def record_llm_usage(
    span: Span,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cost_usd: float,
    finish_reason: str = "stop",
) -> None:
    """Attach token-usage and cost attributes to an LLM span.

    These are content-free metrics -- safe for 100% sampling and Prometheus
    export.  Never put prompt text on a metric label (cardinality bomb).
    """
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    span.set_attribute("gen_ai.usage.cache_read_tokens", cached_tokens)
    span.set_attribute("gen_ai.response.finish_reason", finish_reason)
    # Cost is inferred, not a standard OTel attribute -- prefix with custom ns
    span.set_attribute("app.cost_usd", cost_usd)
    span.set_attribute("app.cost_formula", "input*rate + output*rate + cache_read*rate")
    span.set_status("OK")
    span.end()


# =============================================================================
# --- Section 3: Trace Context Propagation Across Agent Steps ----------------
# =============================================================================

def new_traceparent() -> tuple[str, str, str]:
    """Generate a W3C traceparent header.

    Format: 00-{32 hex trace-id}-{16 hex span-id}-{2 hex flags}
    Flag 01 = sampled (head hint, not a tail decision).
    """
    trace_id = uuid.uuid4().hex          # 32 hex chars = 16 bytes
    span_id = uuid.uuid4().hex[:16]      # 16 hex chars = 8 bytes
    return f"00-{trace_id}-{span_id}-01", trace_id, span_id


def inject_traceparent_into_mcp(params_meta: dict, traceparent: str) -> dict:
    """Inject W3C trace context into MCP params._meta.

    Per SEP-414 (MCP spec 2026-07-28): keys MUST be unprefixed --
    'traceparent', 'tracestate', 'baggage'.  DNS-prefixing breaks traces.
    """
    params_meta["traceparent"] = traceparent
    # tracestate is optional; carries vendor-specific key=value pairs
    # params_meta["tracestate"] = "myvendor=abc123"
    return params_meta


def propagate_across_agent_steps(tracer: Tracer) -> list[Span]:
    """Demonstrate trace propagation: root agent -> tool -> nested LLM call.

    In production the root span is 'invoke_agent {name}', tool spans are
    'execute_tool {tool_name}', and nested LLM calls are 'chat {model}'.
    All share the same trace_id via W3C traceparent on the wire.
    """
    # Root: the agent invocation
    root = tracer.start_span("invoke_agent support-bot", attributes={
        "gen_ai.operation.name": "invoke_agent",
        "span.kind": "SERVER",
    })

    # Step 1: LLM decides to call a tool
    llm_plan = create_llm_span(tracer, model="anthropic:claude-sonnet-4", parent=root)
    record_llm_usage(llm_plan, input_tokens=800, output_tokens=120,
                     cost_usd=0.0034)

    # Step 2: Tool execution (e.g., database lookup)
    tool_span = tracer.start_span("execute_tool crm_lookup", parent=root, attributes={
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "crm_lookup",
        "span.kind": "INTERNAL",
    })
    # Propagate traceparent to MCP / downstream service
    tp, _, _ = new_traceparent()
    inject_traceparent_into_mcp({"_meta": {}}, tp)
    tool_span.set_status("OK")
    tool_span.end()

    # Step 3: Second LLM call with tool result
    llm_final = create_llm_span(tracer, model="anthropic:claude-sonnet-4", parent=root)
    record_llm_usage(llm_final, input_tokens=1200, output_tokens=250,
                     cached_tokens=800, cost_usd=0.0051)

    root.set_status("OK")
    root.end()

    return tracer.spans


# =============================================================================
# --- Section 4: Tail Sampling Decision Logic --------------------------------
# =============================================================================

@dataclass
class TraceTree:
    """A completed trace tree ready for a tail-sampling decision."""
    trace_id: str
    root_span: Span
    spans: list[Span]
    has_error: bool = False
    has_content_filter: bool = False
    has_hitl: bool = False
    total_latency_ms: float = 0.0
    total_tokens: int = 0


def tail_sampling_decision(tree: TraceTree, *, latency_threshold_ms: float = 60_000) -> bool:
    """Decide whether to keep a trace tree after it completes.

    Agent default = tail sampling.  Head sampling decides before tools or
    finish_reason -- wrong for agents because the interesting bit is only
    known at the tail.

    Policy stack (order matters -- first match wins):
      1. Keep all errors / content_filter / policy-deny / HITL
      2. Keep high-latency roots (above p95 threshold)
      3. Rate-limit / bytes cap under overload (not shown)
      4. Probabilistic remainder (e.g., 10% of happy paths)
      5. SDK head sample only as last-ditch if collectors saturated
    """
    # Rule 1: Always keep error / policy / safety / HITL traces
    if tree.has_error:
        return True
    if tree.has_content_filter:
        return True
    if tree.has_hitl:
        return True

    # Rule 2: Keep high-latency roots (slow agents often reveal issues)
    if tree.total_latency_ms > latency_threshold_ms:
        return True

    # Rule 4: Probabilistic remainder -- keep 10% of normal traces
    # In production this is the tailsamplingprocessor probabilistic policy
    if random.random() < 0.10:
        return True

    return False  # Drop -- metrics (L1) and audit (L3) are unaffected


# =============================================================================
# --- Section 5: Cost Tracking Decorator -------------------------------------
# =============================================================================

# Published rates per million tokens (2026 illustrative)
MODEL_RATES = {
    "anthropic:claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_read": 0.30},
    "anthropic:claude-haiku-4.5": {"input": 1.0, "output": 5.0, "cache_read": 0.10},
    "openai:gpt-5.5":           {"input": 2.0, "output": 12.0, "cache_read": 0.20},
}

# Thread-safe accumulator for cost tracking across calls
_cost_lock = threading.Lock()
_cost_ledger: dict[str, float] = {}  # tenant -> total cost


def compute_cost(model: str, input_tokens: int, output_tokens: int,
                 cached_tokens: int = 0) -> float:
    """Compute cost in USD for a single LLM call.

    Observable run cost ~= model_input + cached_read + output
                          + tool/retrieval surcharges
                          + trace/checkpoint persistence overhead
                          + eval LLM-as-judge cost (if online)
    """
    rates = MODEL_RATES.get(model, {"input": 5.0, "output": 25.0, "cache_read": 0.50})
    # Cached tokens are a subset of input_tokens in most APIs
    uncached = max(0, input_tokens - cached_tokens)
    cost = (
        (uncached / 1_000_000) * rates["input"]
        + (cached_tokens / 1_000_000) * rates["cache_read"]
        + (output_tokens / 1_000_000) * rates["output"]
    )
    return round(cost, 6)


def track_cost(model: str, tenant: str = "default"):
    """Decorator that automatically tracks cost of any function returning
    a dict with 'input_tokens', 'output_tokens', and optionally 'cached_tokens'.

    Usage:
        @track_cost("anthropic:claude-sonnet-4", tenant="acme")
        def call_llm(prompt): ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            cost = compute_cost(
                model,
                result.get("input_tokens", 0),
                result.get("output_tokens", 0),
                result.get("cached_tokens", 0),
            )
            result["cost_usd"] = cost
            with _cost_lock:
                _cost_ledger[tenant] = _cost_ledger.get(tenant, 0.0) + cost
            return result
        return wrapper
    return decorator


# Example usage of the decorator
@track_cost("anthropic:claude-sonnet-4", tenant="acme")
def fake_llm_call(prompt: str) -> dict:
    """Simulate an LLM call returning token counts."""
    return {
        "response": f"Answer to: {prompt[:30]}...",
        "input_tokens": 800,
        "output_tokens": 150,
        "cached_tokens": 600,
    }


# =============================================================================
# --- Section 6: Alert / SLO Checker (Burn-Rate) -----------------------------
# =============================================================================

@dataclass
class SLOConfig:
    """Configuration for a burn-rate SLO.

    For a 30-day, 99.9% SLO:
      Page:   1h long / 5m short  at burn rate 14.4  (2% budget consumed)
      Page:   6h long / 30m short at burn rate 6.0   (5% budget consumed)
      Ticket: 3d long / 6h short  at burn rate 1.0   (10% budget consumed)

    Short window = 1/12 of long.  Multiwindow AND: fire only if BOTH
    windows exceed so the alert resets when burn stops.
    """
    slo_target: float = 0.999           # 99.9%
    budget_window_days: int = 30
    long_window_minutes: int = 60       # 1 hour
    short_window_minutes: int = 5       # 5 minutes
    burn_rate_threshold: float = 14.4   # page-level severity


def check_burn_rate(
    config: SLOConfig,
    *,
    long_window_error_rate: float,
    short_window_error_rate: float,
) -> dict:
    """Evaluate whether the current error burn rate should trigger an alert.

    Burn rate = actual_error_rate / allowed_error_rate.
    Multiwindow AND: both long AND short must exceed threshold.
    This prevents alerting on a brief spike that already stopped.
    """
    allowed_error_rate = 1.0 - config.slo_target  # 0.001 for 99.9%

    long_burn = long_window_error_rate / allowed_error_rate if allowed_error_rate > 0 else 0
    short_burn = short_window_error_rate / allowed_error_rate if allowed_error_rate > 0 else 0

    # Multiwindow AND: fire only if both windows exceed
    should_alert = (
        long_burn >= config.burn_rate_threshold
        and short_burn >= config.burn_rate_threshold
    )

    return {
        "should_alert": should_alert,
        "severity": "page" if config.burn_rate_threshold >= 6.0 else "ticket",
        "long_burn_rate": round(long_burn, 2),
        "short_burn_rate": round(short_burn, 2),
        "threshold": config.burn_rate_threshold,
        "budget_consumed_pct": round(
            long_burn * (config.long_window_minutes / (config.budget_window_days * 24 * 60)) * 100, 2
        ),
    }


# =============================================================================
# --- Section 7: PII Pipeline (Detect -> Redact -> Audit) --------------------
# =============================================================================
# PII must be handled BEFORE export.  Three layers:
#   L1 SDK (pre-serialization) -- primary control
#   L2 OTel Collector (redaction processor) -- central policy
#   L3 Backend (last resort) -- post-hoc, has a raw PII window

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+")
SSN_RE   = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")


class PiiPipeline:
    """Detect -> redact -> audit.  Never logs raw PII values.

    If the classifier is down: fail closed on content export
    (still serve the user; still emit metrics + redacted metadata).
    """

    def __init__(self) -> None:
        self.audit_log: list[dict] = []

    def apply(self, text: str, *, trace_id: str = "", tenant: str = "") -> str:
        pre_sha = hashlib.sha256(text.encode()).hexdigest()[:16]
        detected: dict[str, int] = {}

        for name, pattern in [("EMAIL", EMAIL_RE), ("SSN", SSN_RE), ("PHONE", PHONE_RE)]:
            hits = len(pattern.findall(text))
            if hits:
                detected[name] = hits

        # Redact to stable tokens so downstream can still correlate
        redacted = text
        for name, pattern in [("EMAIL", EMAIL_RE), ("SSN", SSN_RE), ("PHONE", PHONE_RE)]:
            redacted = pattern.sub(
                lambda m: f"[{name}_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]",
                redacted,
            )

        post_sha = hashlib.sha256(redacted.encode()).hexdigest()[:16]

        # Audit: log decisions (types + counts), never raw values
        self.audit_log.append({
            "type": "pii_decision",
            "trace_id": trace_id,
            "tenant": tenant,
            "pre_sha": pre_sha,
            "post_sha": post_sha,
            "entity_types": detected,
            "action": "tokenize" if detected else "none",
        })

        return redacted


# =============================================================================
# --- Section 8: Circuit Breaker for Telemetry Export -------------------------
# =============================================================================
# Telemetry failure must NEVER become a user 500.  Circuit-break the exporter.
# Fallback chain: full content -> redacted/pointer -> metrics-only -> disk buffer.

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class ExportCircuitBreaker:
    """Fail-fast circuit breaker for telemetry export backends.

    Independent breakers needed for: trace backend (Datadog/LangSmith/Tempo),
    content blob store, metrics backend.  A Datadog timeout must not block
    the user (bulkhead).
    """
    name: str
    threshold: int = 5          # consecutive failures to trip
    cooldown_s: float = 15.0    # seconds before half-open probe
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self) -> bool:
        with self._lock:
            if self._state is CircuitState.CLOSED:
                return True
            if self._state is CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self.cooldown_s:
                    self._state = CircuitState.HALF_OPEN
                    return True  # one probe allowed
                return False
            # HALF_OPEN: allow one probe
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


# =============================================================================
# --- Section 9: Telemetry Runtime (Three-Layer Export) -----------------------
# =============================================================================

class TelemetryRuntime:
    """Production-shaped telemetry runtime with three never-shared layers.

    L1: Metrics (100%, content-free) -- SLOs, token burn, cost
    L2: Traces (tail-sampled, redacted) -- debug, trajectory UI
    L3: Audit (never sampled, WORM) -- legal proof a tool ran
    """

    def __init__(self, hmac_key: bytes) -> None:
        self.hmac_key = hmac_key
        self.metrics: list[dict] = []        # L1
        self.traces: list[dict] = []         # L2
        self.audit: list[dict] = []          # L3
        self.pii = PiiPipeline()
        self.trace_breaker = ExportCircuitBreaker("traces")

    def hash_user_id(self, user_id: str) -> str:
        """HMAC user ID -- never put raw user IDs on metric labels."""
        return hmac.new(self.hmac_key, user_id.encode(), hashlib.sha256).hexdigest()[:16]

    def export_turn(
        self,
        *,
        tenant: str,
        trace_id: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        cost_usd: float,
        tool_calls: list[dict],
        prompt_text: str,
    ) -> dict:
        """Export a single agent turn across all three layers.

        L1 metrics always succeed (content-free).
        L3 audit always succeeds (never sampled, never blocked by breaker).
        L2 traces use circuit breaker with fallback.
        """
        # L1: Metrics -- always, 100%, content-free
        self.metrics.append({
            "tenant": tenant,
            "trace_id": trace_id,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "latency_ms": latency_ms,
            "cost_usd": cost_usd,
        })

        # L3: Audit -- always, never sampled, WORM
        for tc in tool_calls:
            args_hash = hashlib.sha256(
                json.dumps(tc.get("args", {}), sort_keys=True).encode()
            ).hexdigest()[:16]
            self.audit.append({
                "type": "tool_action",
                "tenant": tenant,
                "trace_id": trace_id,
                "tool": tc["name"],
                "args_sha": args_hash,
                "policy": tc.get("policy", "allow"),
            })

        # L2: Traces -- redacted, circuit-breaker-protected
        redacted = self.pii.apply(prompt_text, trace_id=trace_id, tenant=tenant)

        if self.trace_breaker.allow():
            try:
                # In production: OTLP export to Tempo / Honeycomb / LangSmith
                self.traces.append({"trace_id": trace_id, "content": redacted})
                self.trace_breaker.record_success()
                return {"status": "ok", "tier": "redacted_trace"}
            except Exception:
                self.trace_breaker.record_failure()

        # Fallback: metrics-only -- user path never blocked
        return {"status": "degraded", "tier": "metrics_only"}


# =============================================================================
# --- Section 10: HMAC User Identity (Never as Metric Label) -----------------
# =============================================================================

def demonstrate_hmac_identity() -> None:
    """HMAC user identity so raw user IDs never appear in traces.

    The HMAC key lives on the server, NEVER in the trace or the model context.
    """
    key = b"server-secret-not-in-trace"
    user_id = "user-12345"
    hashed = hmac.new(key, user_id.encode(), hashlib.sha256).hexdigest()[:16]
    # hashed is safe for trace attributes; raw user_id is not
    assert len(hashed) == 16
    assert user_id not in hashed


# =============================================================================
# --- Demo / Self-Test -------------------------------------------------------
# =============================================================================

if __name__ == "__main__":
    # 1. Trace propagation across agent steps
    tracer = Tracer("demo")
    spans = propagate_across_agent_steps(tracer)
    assert len(spans) == 4  # root + 2 LLM + 1 tool
    assert all(s.trace_id == spans[0].trace_id for s in spans)
    print(f"[Trace] {len(spans)} spans, same trace_id: {spans[0].trace_id[:12]}...")

    # 2. W3C traceparent generation
    tp, tid, sid = new_traceparent()
    assert tp.startswith("00-")
    assert len(tid) == 32
    print(f"[Traceparent] {tp}")

    # 3. Tail sampling
    tree = TraceTree(trace_id=tid, root_span=spans[0], spans=spans,
                     has_error=True, total_latency_ms=5000)
    assert tail_sampling_decision(tree) is True  # error -> always keep
    tree_ok = TraceTree(trace_id=tid, root_span=spans[0], spans=spans,
                        total_latency_ms=1000)
    # Happy path: probabilistic (may or may not keep)
    print(f"[Sampling] Error trace kept: True, Happy path decision: "
          f"{tail_sampling_decision(tree_ok)}")

    # 4. Cost tracking
    result = fake_llm_call("What is the refund policy?")
    assert "cost_usd" in result
    assert _cost_ledger["acme"] > 0
    print(f"[Cost] Call cost: ${result['cost_usd']:.6f}, "
          f"Tenant total: ${_cost_ledger['acme']:.6f}")

    # 5. Burn-rate SLO check
    slo = SLOConfig()  # 99.9%, 1h/5m, burn_rate 14.4
    alert = check_burn_rate(slo, long_window_error_rate=0.02,
                            short_window_error_rate=0.025)
    print(f"[SLO] Should alert: {alert['should_alert']}, "
          f"Long burn: {alert['long_burn_rate']}x, "
          f"Short burn: {alert['short_burn_rate']}x")

    # 6. PII pipeline
    pii = PiiPipeline()
    redacted = pii.apply("Contact ada@example.com or 555-123-4567",
                         trace_id="abc", tenant="acme")
    assert "ada@example.com" not in redacted
    assert "[EMAIL_" in redacted
    print(f"[PII] Redacted: {redacted}")

    # 7. Full telemetry runtime
    rt = TelemetryRuntime(b"server-key")
    export = rt.export_turn(
        tenant="acme", trace_id=tid,
        tokens_in=800, tokens_out=150, latency_ms=2400, cost_usd=0.004,
        tool_calls=[{"name": "crm_lookup", "args": {"ticket": "T-9"}}],
        prompt_text="Email ada@example.com about refund for SSN 123-45-6789",
    )
    assert export["status"] == "ok"
    assert len(rt.metrics) == 1
    assert len(rt.audit) >= 1
    assert "ada@example.com" not in rt.traces[0]["content"]
    print(f"[Runtime] Export: {export['status']}, "
          f"Metrics: {len(rt.metrics)}, Audit: {len(rt.audit)}")

    # 8. HMAC identity
    demonstrate_hmac_identity()
    print("[HMAC] User identity hashing verified.")

    print("\nAll checks passed.")
