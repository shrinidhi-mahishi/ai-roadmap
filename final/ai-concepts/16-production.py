"""
Production Patterns for LLM Serving -- Health Checks, Idempotency, Queues, Circuit Breakers, SLOs.

LLM apps are GPU-bound, stateful (KV cache), and slow (seconds not ms). These patterns
address reliability challenges unique to inference: readiness vs liveness, idempotent
side effects, retry with backoff, circuit breaking, and SLO monitoring with burn rates.
"""

import time, random, uuid
from collections import deque

# ================================================================
# Section 1: Health Check Endpoint (Readiness + Liveness)
# ================================================================
# Readiness = "model loaded." Liveness = "process alive."
# Inverting these sends traffic to a loading GPU and then OOM-kills it.


class InferenceServer:
    def __init__(self):
        self.process_alive = True
        self.model_loaded = False
        self.kv_utilization = 0.0

    def load_model(self):
        time.sleep(0.01)  # real: minutes for 70B
        self.model_loaded = True

    def health_live(self) -> dict:
        """Liveness: process running? Never check dependencies here."""
        return {"status": "ok" if self.process_alive else "fail"}

    def health_ready(self) -> dict:
        """Readiness: model loaded and KV not saturated?"""
        ok = self.model_loaded and self.kv_utilization < 0.95
        return {"status": "ok" if ok else "not_ready", "kv_util": self.kv_utilization}


def demo_health_checks():
    print("--- Health Check Endpoint ---")
    s = InferenceServer()
    print(f"  Before load: ready={s.health_ready()['status']}")
    s.load_model()
    print(f"  After load:  ready={s.health_ready()['status']}")
    s.kv_utilization = 0.96
    print(f"  KV pressure: ready={s.health_ready()['status']}")


# ================================================================
# Section 2: Idempotent Request Handler
# ================================================================
# Side-effecting tools need idempotency keys. Chat completions are NOT
# Stripe-idempotent -- split idempotency by effect type.


class IdempotencyStore:
    def __init__(self):
        self.store: dict[str, dict] = {}

    def check(self, key: str, req_hash: str) -> dict | None:
        if key in self.store:
            if self.store[key]["hash"] != req_hash:
                raise ValueError(f"Key reused with different input")
            return self.store[key]["result"]
        return None

    def commit(self, key: str, req_hash: str, result: dict):
        self.store[key] = {"result": result, "hash": req_hash}


def demo_idempotency():
    print("\n--- Idempotent Request Handler ---")
    store = IdempotencyStore()
    key, body = str(uuid.uuid4()), {"action": "charge", "amount": 99.99}
    req_hash = str(hash(frozenset(body.items())))
    cached = store.check(key, req_hash)
    result = {"status": "completed", "id": uuid.uuid4().hex[:8]}
    store.commit(key, req_hash, result)
    print(f"    First call: PROCESSED -> {result['id']}")
    cached = store.check(key, req_hash)
    print(f"    Second call: {'DUPLICATE' if cached else 'PROCESSED'} -> {cached['id']}")


# ================================================================
# Section 3: Queue Worker with Retry + Exponential Backoff
# ================================================================
# Pull -> process -> ack/nack. Backoff + jitter prevents thundering herd.

MAX_ATTEMPTS = 3


class SimpleQueue:
    def __init__(self):
        self.messages: deque = deque()
        self.dlq: list = []

    def enqueue(self, msg: dict):
        msg.setdefault("attempts", 0)
        self.messages.append(msg)

    def receive(self) -> dict | None:
        if self.messages:
            msg = self.messages.popleft()
            msg["attempts"] += 1
            return msg
        return None


def process_with_retry(q: SimpleQueue):
    while (msg := q.receive()) is not None:
        try:
            if random.random() < 0.5 and msg["attempts"] < MAX_ATTEMPTS:
                raise RuntimeError("Transient failure")
            print(f"    OK: {msg['body']} (attempt {msg['attempts']})")
        except RuntimeError:
            if msg["attempts"] >= MAX_ATTEMPTS:
                q.dlq.append(msg)
                print(f"    DLQ: {msg['body']} after {msg['attempts']} attempts")
            else:
                delay = min(2 ** msg["attempts"] + random.uniform(0, 1), 30)
                q.enqueue(msg)
                print(f"    RETRY: {msg['body']} #{msg['attempts']}, backoff={delay:.1f}s")


def demo_queue_worker():
    print("\n--- Queue Worker with Retry ---")
    random.seed(42)
    q = SimpleQueue()
    for i in range(4):
        q.enqueue({"body": f"task_{i}"})
    process_with_retry(q)
    print(f"  Dead-letter queue: {len(q.dlq)} messages")


# ================================================================
# Section 4: Circuit Breaker
# ================================================================
# Track failures -> open -> half-open probe -> close.
# Prevents cascading failure when a model API degrades.


class CircuitBreaker:
    """Three-state: CLOSED (normal) -> OPEN (fail fast) -> HALF_OPEN (probe)."""
    CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"

    def __init__(self, threshold: int = 3, recovery_s: float = 10.0):
        self.state = self.CLOSED
        self.failures = 0
        self.threshold = threshold
        self.recovery_s = recovery_s
        self.last_fail = 0.0

    def call(self, fn):
        if self.state == self.OPEN:
            if time.time() - self.last_fail >= self.recovery_s:
                self.state = self.HALF_OPEN
            else:
                raise ConnectionError("Circuit OPEN -- failing fast")
        try:
            r = fn()
            self.state, self.failures = self.CLOSED, 0
            return r
        except Exception:
            self.failures += 1
            self.last_fail = time.time()
            if self.state == self.HALF_OPEN or self.failures >= self.threshold:
                self.state = self.OPEN
            raise


def demo_circuit_breaker():
    print("\n--- Circuit Breaker ---")
    cb = CircuitBreaker(threshold=3, recovery_s=0.1)
    ok = lambda: "LLM OK"
    bad = lambda: (_ for _ in ()).throw(ConnectionError("503"))
    for i in range(3):
        print(f"  Call {i+1}: {cb.call(ok)}  [{cb.state}]")
    for i in range(4):
        try:
            cb.call(bad)
        except ConnectionError as e:
            print(f"  Call {i+4}: {e}  [{cb.state}]")
    time.sleep(0.15)
    print(f"  Probe:  {cb.call(ok)}  [{cb.state}]")


# ================================================================
# Section 5: SLO Monitor (Latency Percentiles + Error Budget Burn Rate)
# ================================================================
# SLI = good/total. SLO = target. Error budget = 100% - SLO.
# Google SRE: page at 14.4x burn on 1h, ticket at 1x on 3d.


class SLOMonitor:
    def __init__(self, slo: float = 0.999, latency_threshold_ms: float = 2000):
        self.slo = slo
        self.threshold = latency_threshold_ms
        self.latencies: list[float] = []
        self.good = 0
        self.total = 0

    def record(self, latency_ms: float, is_error: bool = False):
        self.total += 1
        self.latencies.append(latency_ms)
        if not is_error and latency_ms <= self.threshold:
            self.good += 1

    def availability(self) -> float:
        return self.good / self.total if self.total else 1.0

    def burn_rate(self) -> float:
        allowed = 1 - self.slo
        return round((1 - self.availability()) / allowed, 2) if allowed else 0

    def percentile(self, p: float) -> float:
        s = sorted(self.latencies)
        return s[min(int(len(s) * p / 100), len(s) - 1)] if s else 0

    def report(self) -> dict:
        return {"requests": self.total, "availability": round(self.availability(), 4),
                "burn_rate": self.burn_rate(),
                "p50_ms": round(self.percentile(50), 1),
                "p95_ms": round(self.percentile(95), 1),
                "p99_ms": round(self.percentile(99), 1)}


def demo_slo_monitor():
    print("\n--- SLO Monitor ---")
    m = SLOMonitor(slo=0.999, latency_threshold_ms=2000)
    random.seed(123)
    for _ in range(970): m.record(random.uniform(100, 1500))
    for _ in range(25): m.record(random.uniform(2000, 5000))
    for _ in range(5): m.record(500, is_error=True)
    r = m.report()
    for k, v in r.items():
        print(f"  {k}: {v}")
    if r["burn_rate"] >= 14.4:
        print(f"  ** PAGE: burn rate {r['burn_rate']}x exceeds 14.4x threshold **")


# ================================================================
# Main -- run all demos
# ================================================================

if __name__ == "__main__":
    demo_health_checks()
    demo_idempotency()
    demo_queue_worker()
    demo_circuit_breaker()
    demo_slo_monitor()
