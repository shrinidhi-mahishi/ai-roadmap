"""
Observability for AI Agents -- Traces, Spans, Cost Tracking, and Structured Logging.

Production agents are hard to debug because they make autonomous decisions. Observability
is the flight recorder: every LLM call, tool invocation, and decision point is captured
with tokens, latency, cost, and lineage so you can replay what happened and why.
"""

import json, time, uuid, hashlib
from dataclasses import dataclass, field
from typing import Optional
from contextlib import contextmanager

# ================================================================
# Section 1: Trace / Span Creation (OTel-style, no imports needed)
# ================================================================
# W3C Trace Context: traceparent = 00-{trace_id}-{span_id}-{flags}
# A trace is a tree of spans. Each span has a parent (except the root).


@dataclass
class Span:
    """Minimal OTel-compatible span. Kind follows GenAI semconv."""
    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: Optional[str] = None
    kind: str = "INTERNAL"  # CLIENT for LLM calls, INTERNAL for tools/agents
    attributes: dict = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    def end(self):
        self.end_time = time.time()

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000 if self.end_time else 0.0


class Tracer:
    """Simulated OTel tracer -- creates nested spans and holds the active context."""

    def __init__(self, service_name: str):
        self.service_name = service_name
        self._active_span: Optional[Span] = None
        self.completed_spans: list[Span] = []

    @contextmanager
    def start_span(self, name: str, kind: str = "INTERNAL", attributes: dict = None):
        trace_id = self._active_span.trace_id if self._active_span else uuid.uuid4().hex
        parent_id = self._active_span.span_id if self._active_span else None
        span = Span(name=name, trace_id=trace_id, parent_id=parent_id,
                    kind=kind, attributes=attributes or {})
        previous = self._active_span
        self._active_span = span
        try:
            yield span
        finally:
            span.end()
            self._active_span = previous
            self.completed_spans.append(span)


def demo_tracing():
    tracer = Tracer("my-agent")
    with tracer.start_span("invoke_agent assistant", kind="INTERNAL",
                           attributes={"gen_ai.agent.name": "assistant"}) as agent:
        with tracer.start_span("chat gpt-4o", kind="CLIENT",
                               attributes={"gen_ai.request.model": "gpt-4o"}) as llm:
            time.sleep(0.01)
            llm.attributes.update({"gen_ai.usage.input_tokens": 1500,
                                   "gen_ai.usage.output_tokens": 200})
        with tracer.start_span("execute_tool search_db", kind="INTERNAL"):
            time.sleep(0.005)
    print("--- Trace / Span Demo ---")
    for s in tracer.completed_spans:
        indent = "  " if s.parent_id else ""
        print(f"{indent}[{s.kind}] {s.name}  {s.duration_ms:.1f}ms")


# ================================================================
# Section 2: LLM Call Instrumentation
# ================================================================
# Wrap any LLM call to capture tokens, latency, cost, and finish reason.

# Pricing table: $/1M tokens (input, output) -- interview-ready numbers
MODEL_PRICING = {
    "gpt-4o":          {"input": 2.50, "output": 10.00},
    "gpt-4o-mini":     {"input": 0.15, "output": 0.60},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-haiku-3":  {"input": 0.25, "output": 1.25},
}


def compute_cost(model: str, input_tokens: int, output_tokens: int,
                 cached_tokens: int = 0) -> float:
    """Dollar cost from token counts. Cached reads billed at 0.1x input."""
    prices = MODEL_PRICING.get(model, {"input": 3.0, "output": 15.0})
    fresh_input = input_tokens - cached_tokens
    cost = (fresh_input * prices["input"]
            + cached_tokens * prices["input"] * 0.1
            + output_tokens * prices["output"]) / 1_000_000
    return round(cost, 6)


def mock_llm_call(prompt: str, model: str = "gpt-4o"):
    """Simulate an LLM API response with usage metadata."""
    time.sleep(0.02)
    return {
        "content": f"Mock response to: {prompt[:40]}...",
        "model": model,
        "usage": {"input_tokens": len(prompt) * 2, "output_tokens": 85,
                  "cached_tokens": len(prompt)},
        "finish_reason": "stop",
    }


def instrumented_llm_call(prompt: str, model: str = "gpt-4o") -> dict:
    """Wraps an LLM call to capture observability signals."""
    start = time.time()
    response = mock_llm_call(prompt, model)
    latency_ms = (time.time() - start) * 1000
    u = response["usage"]
    cost = compute_cost(model, u["input_tokens"], u["output_tokens"], u["cached_tokens"])
    response["_obs"] = {
        "latency_ms": round(latency_ms, 1),
        "cost_usd": cost,
        "model": model,
    }
    return response


def demo_instrumentation():
    print("\n--- LLM Call Instrumentation ---")
    resp = instrumented_llm_call("Explain the difference between prefill and decode.")
    obs = resp["_obs"]
    u = resp["usage"]
    print(f"Model: {obs['model']}  Latency: {obs['latency_ms']}ms  Cost: ${obs['cost_usd']}")
    print(f"Tokens -- in: {u['input_tokens']}  out: {u['output_tokens']}  cached: {u['cached_tokens']}")


# ================================================================
# Section 3: Agent Trajectory Logging
# ================================================================
# Log each step: thought, action, observation, timestamp.
# This is the "trajectory" view -- ordered steps, not nested spans.


def log_trajectory_step(run_id: str, step: int, phase: str, content: str,
                        tokens: int = 0, cost: float = 0.0):
    """Emit one trajectory entry. Phase is thought | action | observation."""
    entry = {"run_id": run_id, "step": step, "phase": phase,
             "content": content[:200], "tokens": tokens, "cost_usd": cost,
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    print(json.dumps(entry))


def demo_trajectory():
    print("\n--- Agent Trajectory Logging ---")
    rid = uuid.uuid4().hex[:12]
    log_trajectory_step(rid, 1, "thought", "Need the sales DB for quarterly revenue.")
    log_trajectory_step(rid, 2, "action", "tool:search_db(query='Q3 revenue')", tokens=150)
    log_trajectory_step(rid, 3, "observation", "Revenue: $4.2M, up 12% QoQ")
    log_trajectory_step(rid, 4, "action", "respond('Q3 revenue was $4.2M')", tokens=85, cost=0.001)


# ================================================================
# Section 4: Cost Tracking Across a Run
# ================================================================
# Track cumulative token usage and dollar cost per model across an agent run.


class CostTracker:
    """Accumulates token/cost metrics across multiple LLM calls in one run."""

    def __init__(self):
        self.calls: list[dict] = []

    def record(self, model: str, input_tokens: int, output_tokens: int,
               cached_tokens: int = 0):
        cost = compute_cost(model, input_tokens, output_tokens, cached_tokens)
        self.calls.append({"model": model, "input": input_tokens,
                           "output": output_tokens, "cached": cached_tokens,
                           "cost": cost})

    def summary(self) -> dict:
        total_in = sum(c["input"] for c in self.calls)
        total_out = sum(c["output"] for c in self.calls)
        total_cost = sum(c["cost"] for c in self.calls)
        return {"calls": len(self.calls), "total_input_tokens": total_in,
                "total_output_tokens": total_out, "total_cost_usd": round(total_cost, 6)}


def demo_cost_tracking():
    print("\n--- Cost Tracking ---")
    tracker = CostTracker()
    # Simulate a 3-turn agent: plan, execute, verify
    tracker.record("gpt-4o", input_tokens=2000, output_tokens=300)
    tracker.record("gpt-4o-mini", input_tokens=800, output_tokens=150)  # cheap executor
    tracker.record("gpt-4o", input_tokens=2500, output_tokens=200, cached_tokens=2000)
    print(json.dumps(tracker.summary(), indent=2))


# ================================================================
# Section 5: Structured Logging for Agents
# ================================================================
# JSON log events with trace_id, span_id, metadata -- no raw user text in prod.


def emit_structured_log(trace_id: str, span_id: str, model: str,
                        input_tokens: int, output_tokens: int, cached_tokens: int,
                        finish_reason: str, latency_ms: float, cost_usd: float,
                        tenant_id: str, user_id: str):
    """Control-safe structured log line. User text is never included."""
    log = {
        "event": "llm.call.complete", "trace_id": trace_id, "span_id": span_id,
        "gen_ai.request.model": model, "input_tokens": input_tokens,
        "output_tokens": output_tokens, "cache_read_tokens": cached_tokens,
        "finish_reason": finish_reason, "latency_ms": latency_ms,
        "cost_usd": cost_usd, "tenant_id": tenant_id,
        # Hash user_id -- never log PII in plain text
        "user_id_hash": hashlib.sha256(user_id.encode()).hexdigest()[:16],
        "ts": time.time(),
    }
    print(json.dumps(log, indent=2))


def demo_structured_logging():
    print("\n--- Structured Logging ---")
    emit_structured_log(
        trace_id=uuid.uuid4().hex, span_id=uuid.uuid4().hex[:16],
        model="gpt-4o", input_tokens=1245, output_tokens=387, cached_tokens=823,
        finish_reason="stop", latency_ms=2340.0, cost_usd=0.012,
        tenant_id="acme-corp", user_id="user@example.com",
    )


# ================================================================
# Main -- run all demos
# ================================================================

if __name__ == "__main__":
    demo_tracing()
    demo_instrumentation()
    demo_trajectory()
    demo_cost_tracking()
    demo_structured_logging()
