"""LLMOps & CI/CD for AI -- interview prep code snippets.

Covers the promotion control plane: model registry with alias-based promotion,
CI eval gates (fail-closed), prompt versioning with content hashing, sticky
A/B canary deployment, output-distribution drift detection, tiered rollback
logic, and per-version cost tracking.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any


# --- Model Registry: Version, Promote, Rollback ----------------------------
# Key insight: the deployable unit is a 6-tuple "release bundle," NOT just a
# model checkpoint.  Environment tags (production, staging) are mutable
# pointers to immutable versions -- like git tags pointing at commits.

@dataclass(frozen=True)
class ReleaseBundle:
    """Immutable release pin.  Any field change = new release."""
    model_id: str          # provider model or self-hosted URI
    prompt_hash: str       # SHA of prompt template content
    schema_hash: str       # SHA of output JSON Schema / GBNF
    decoding_params: str   # temperature, top_p, max_tokens, seed
    index_gen: str         # embedding model + vector index generation
    eval_suite_id: str     # dataset version + scorer versions
    version: int = 0       # assigned by registry on register()

    def digest(self) -> str:
        """Deterministic content hash of the full tuple."""
        blob = "|".join([
            self.model_id, self.prompt_hash, self.schema_hash,
            self.decoding_params, self.index_gen, self.eval_suite_id,
            str(self.version),
        ])
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


class RegistryError(RuntimeError):
    pass


@dataclass
class ModelRegistry:
    """In-memory model registry mirroring MLflow alias semantics.

    Production pattern: MLflow aliases @champion / @challenger replaced
    deprecated stages (None/Staging/Production/Archived) since ~2.9.
    Aliases are mutable pointers; versions are immutable artifacts.
    """
    _versions: dict[int, ReleaseBundle] = field(default_factory=dict)
    _aliases: dict[str, int] = field(default_factory=dict)
    _etags: dict[str, str] = field(default_factory=dict)  # for CAS
    _next_ver: int = 1

    def register(self, bundle: ReleaseBundle) -> ReleaseBundle:
        """Register an immutable version.  Never retag production here."""
        ver = self._next_ver
        self._next_ver += 1
        pinned = replace(bundle, version=ver)
        self._versions[ver] = pinned
        return pinned

    def set_alias(self, alias: str, version: int, *,
                  expected_etag: str | None = None,
                  actor: str = "system") -> str:
        """Move a mutable alias pointer with compare-and-swap (CAS).

        CAS prevents two concurrent promotions from racing -- the second
        one sees a stale etag and fails, forcing explicit resolution.
        """
        if version not in self._versions:
            raise RegistryError(f"unknown_version:{version}")

        current_etag = self._etags.get(alias, "")
        if expected_etag is not None and current_etag and expected_etag != current_etag:
            raise RegistryError(f"cas_conflict:{alias}:expected={expected_etag}")

        self._aliases[alias] = version
        new_etag = hashlib.sha256(
            f"{alias}:{version}:{actor}:{time.time()}".encode()
        ).hexdigest()[:12]
        self._etags[alias] = new_etag
        return new_etag

    def resolve(self, alias: str) -> ReleaseBundle:
        """Resolve a mutable alias to its immutable bundle."""
        if alias not in self._aliases:
            raise RegistryError(f"no_alias:{alias}")
        return self._versions[self._aliases[alias]]

    def rollback_alias(self, alias: str, to_version: int, *, actor: str) -> str:
        """Rollback = retag the alias to a previous version, not rewrite."""
        return self.set_alias(alias, to_version, expected_etag=None, actor=actor)

    def list_versions(self) -> list[int]:
        return sorted(self._versions.keys())


# --- Eval Gate in CI Pipeline -----------------------------------------------
# CI is fail-closed: a metric drop blocks merge.  The gate runs offline
# (not on user p99).  Majority-of-3 LLM judges reduce flakiness on
# subjective metrics.

@dataclass
class EvalResult:
    task_id: str
    score: float        # 0.0 - 1.0
    judge_scores: list[float] = field(default_factory=list)

    @property
    def majority_score(self) -> float:
        """Majority-of-3 voting: median of 3 judge runs."""
        if len(self.judge_scores) >= 3:
            return sorted(self.judge_scores)[1]  # median of 3
        return self.score


def ci_eval_gate(
    results: list[EvalResult],
    threshold: float = 0.85,
    min_samples: int = 50,
) -> dict[str, Any]:
    """Fail-closed CI gate.  Blocks merge if quality drops below threshold.

    Why fail-closed: a red CI with green prod is CORRECT -- the gate
    protects future deploys, it is not a liveness probe.  Never skip
    the merge gate because the judge returned a 429.
    """
    if len(results) < min_samples:
        return {
            "pass": False,
            "reason": f"insufficient_samples:{len(results)}<{min_samples}",
            "mean_score": 0.0,
        }

    scores = [r.majority_score for r in results]
    mean_score = sum(scores) / len(scores)
    passed = mean_score >= threshold

    return {
        "pass": passed,
        "mean_score": round(mean_score, 4),
        "n": len(results),
        "threshold": threshold,
        "reason": "ok" if passed else f"metric_drop:{mean_score:.4f}<{threshold}",
    }


def sample_size_for_ab(
    baseline_rate: float = 0.85,
    min_detectable_effect: float = 0.03,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Miller's formula: n >= 16*p*(1-p) / delta^2 at 80% power, 5% sig.

    Key number: n ~ 969 for detecting a 3 percentage-point change.
    A 50-item golden set CANNOT support a 3pp ship claim.
    """
    p = baseline_rate
    n = 16 * p * (1 - p) / (min_detectable_effect ** 2)
    return int(math.ceil(n * 1.5))  # 1.5x safety buffer


# --- Prompt Versioning with Content Hash ------------------------------------
# The prompt is an artifact, not a string.  Hash the content so any change
# produces a new version.  This is the "prompt_hash" in the 6-tuple.

def prompt_hash(template: str, model_config: dict | None = None) -> str:
    """Content-addressable prompt version.

    Include model_config (temperature, max_tokens) in the hash because
    changing decoding params changes behavior even if the text is identical.
    """
    payload = template
    if model_config:
        # Canonical JSON for deterministic hashing
        payload += "\n---config---\n" + json.dumps(model_config, sort_keys=True)
    return "ph_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class PromptVersion:
    template: str
    model_config: dict
    hash: str
    created_at: float = field(default_factory=time.time)


class PromptRegistry:
    """Git-SoT prompt registry.  Every save is an immutable version.

    Pattern: PR -> CI eval -> merge -> CD sets alias.  Hub playground
    saves that bypass git cause "silent drift" -- the #1 anti-pattern.
    """
    def __init__(self):
        self._versions: dict[str, PromptVersion] = {}
        self._aliases: dict[str, str] = {}  # alias -> hash

    def register(self, template: str, model_config: dict | None = None) -> PromptVersion:
        cfg = model_config or {}
        h = prompt_hash(template, cfg)
        if h not in self._versions:
            self._versions[h] = PromptVersion(template=template, model_config=cfg, hash=h)
        return self._versions[h]

    def set_alias(self, alias: str, hash_: str) -> None:
        if hash_ not in self._versions:
            raise KeyError(f"unknown_prompt_hash:{hash_}")
        self._aliases[alias] = hash_

    def resolve(self, alias: str) -> PromptVersion:
        if alias not in self._aliases:
            raise KeyError(f"no_prompt_alias:{alias}")
        return self._versions[self._aliases[alias]]


# --- A/B Deployment with Sticky Traffic Splitting ---------------------------
# Sticky assignment: hash(salt + thread_id) so the same user stays in the
# same bucket across turns.  Per-request coin flips contaminate session
# metrics.  Change the salt between experiments.

def sticky_assignment(
    thread_id: str,
    salt: str,
    pct_candidate: int,
) -> str:
    """Deterministic sticky assignment for canary / A/B experiments.

    Returns "candidate" or "control".  Same inputs always produce the
    same output -- critical for multi-turn conversations where switching
    mid-thread pollutes metrics.
    """
    if pct_candidate <= 0:
        return "control"
    if pct_candidate >= 100:
        return "candidate"
    h = int(hashlib.sha256(f"{salt}:{thread_id}".encode()).hexdigest()[:8], 16)
    return "candidate" if (h % 100) < pct_candidate else "control"


@dataclass
class CanaryFlag:
    """Feature flag for canary deployment.

    Kill switch: set pct=0 for immediate rollback (RTO = flag SDK TTL,
    typically seconds).  This is faster than alias retag (60s) or
    image rebuild (~20 min).
    """
    salt: str
    pct: int  # 0-100
    candidate_bundle: ReleaseBundle | None = None

    def resolve(self, thread_id: str, control: ReleaseBundle) -> ReleaseBundle:
        bucket = sticky_assignment(thread_id, self.salt, self.pct)
        if bucket == "candidate" and self.candidate_bundle:
            return self.candidate_bundle
        return control

    def kill(self) -> None:
        """Immediate kill switch.  RTO = seconds (flag SDK cache TTL)."""
        self.pct = 0


# --- Drift Detection: Output Distribution Shift ----------------------------
# Silent quality regression produces zero errors and zero alerts.  HTTP
# dashboards glow green while user satisfaction drops.  Monitor the
# *distribution* of outputs, not just error rates.

@dataclass
class DriftDetector:
    """Detect output distribution shifts between baseline and current window.

    Tracks output-length distribution and category distribution as proxies
    for behavioral drift.  A 20% increase in average response length or a
    shift in topic distribution can signal prompt drift or silent model
    changes by the provider.
    """
    baseline_lengths: list[int] = field(default_factory=list)
    baseline_categories: Counter = field(default_factory=Counter)
    current_lengths: list[int] = field(default_factory=list)
    current_categories: Counter = field(default_factory=Counter)

    def record_baseline(self, output: str, category: str = "default") -> None:
        self.baseline_lengths.append(len(output))
        self.baseline_categories[category] += 1

    def record_current(self, output: str, category: str = "default") -> None:
        self.current_lengths.append(len(output))
        self.current_categories[category] += 1

    def check_length_drift(self, threshold_pct: float = 20.0) -> dict[str, Any]:
        """Flag if mean output length shifted by > threshold_pct."""
        if not self.baseline_lengths or not self.current_lengths:
            return {"drift": False, "reason": "insufficient_data"}

        base_mean = sum(self.baseline_lengths) / len(self.baseline_lengths)
        curr_mean = sum(self.current_lengths) / len(self.current_lengths)
        pct_change = abs(curr_mean - base_mean) / max(base_mean, 1) * 100

        return {
            "drift": pct_change > threshold_pct,
            "baseline_mean": round(base_mean, 1),
            "current_mean": round(curr_mean, 1),
            "pct_change": round(pct_change, 1),
            "threshold_pct": threshold_pct,
        }

    def check_category_drift(self, threshold_pct: float = 15.0) -> dict[str, Any]:
        """Flag if any category's share shifted by > threshold_pct points."""
        if not self.baseline_categories or not self.current_categories:
            return {"drift": False, "reason": "insufficient_data"}

        all_cats = set(self.baseline_categories) | set(self.current_categories)
        base_total = sum(self.baseline_categories.values())
        curr_total = sum(self.current_categories.values())

        max_shift = 0.0
        shifted_cat = ""
        for cat in all_cats:
            base_pct = self.baseline_categories.get(cat, 0) / max(base_total, 1) * 100
            curr_pct = self.current_categories.get(cat, 0) / max(curr_total, 1) * 100
            shift = abs(curr_pct - base_pct)
            if shift > max_shift:
                max_shift = shift
                shifted_cat = cat

        return {
            "drift": max_shift > threshold_pct,
            "max_shift_pct": round(max_shift, 1),
            "shifted_category": shifted_cat,
            "threshold_pct": threshold_pct,
        }


# --- Rollback Decision Logic ------------------------------------------------
# Rollback order is mandatory: flag first (seconds), alias second (60s),
# image last (~20 min).  ~60% of production incidents end in rollback,
# ~40% in forward-fix.

@dataclass
class RollbackDecision:
    """Three-tier rollback with escalating RTO.

    Tier 1: Flag kill (seconds) -- set canary pct=0
    Tier 2: Alias retag (60s) -- move @champion to previous version
    Tier 3: Image rebuild (~20 min) -- last resort
    """
    flag: CanaryFlag
    registry: ModelRegistry
    previous_champion_ver: int | None = None
    rollback_log: list[dict] = field(default_factory=list)

    def evaluate_and_rollback(
        self,
        current_score: float,
        threshold: float = 0.85,
        error_rate: float = 0.0,
        error_threshold: float = 0.05,
    ) -> dict[str, Any]:
        """Decide whether and how to roll back.

        Decision tree:
        1. Error rate spike -> flag kill immediately
        2. Quality below threshold -> flag kill, then alias retag
        3. Both fine -> no action
        """
        action = "none"
        tier = 0

        if error_rate > error_threshold:
            # Tier 1: immediate flag kill for error spikes
            self.flag.kill()
            action = "flag_kill"
            tier = 1
            self.rollback_log.append({
                "ts": time.time(), "action": action, "tier": tier,
                "reason": f"error_rate:{error_rate:.3f}>{error_threshold}",
            })

        if current_score < threshold:
            # Tier 1 first, then Tier 2 if we have a previous version
            if self.flag.pct > 0:
                self.flag.kill()

            if self.previous_champion_ver is not None:
                self.registry.rollback_alias(
                    "champion", self.previous_champion_ver, actor="rollback_bot",
                )
                action = "alias_retag"
                tier = 2

            self.rollback_log.append({
                "ts": time.time(), "action": action, "tier": tier,
                "reason": f"quality_drop:{current_score:.3f}<{threshold}",
            })

        return {"action": action, "tier": tier, "flag_pct": self.flag.pct}


# --- Cost Tracking Per Deployment Version -----------------------------------
# Token economics: know what each version costs so you can catch cost
# explosions early.  Track per-request costs by version digest.

@dataclass
class RequestCost:
    version_digest: str
    input_tokens: int
    output_tokens: int
    model_id: str
    timestamp: float = field(default_factory=time.time)


# Pricing per 1M tokens (representative 2026 rates)
MODEL_PRICING = {
    "gpt-5.6-luna":       {"input": 0.20,  "output": 1.20},
    "gpt-4o":             {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":        {"input": 0.15,  "output": 0.60},
    "claude-sonnet-4":    {"input": 3.00,  "output": 15.00},
}


class CostTracker:
    """Track inference cost per deployment version.

    Key insight: 80/20 routing (80% mini, 20% frontier) saves ~75%.
    Adding 25% semantic cache hits drops effective cost further to ~$1.73/1k.
    """
    def __init__(self):
        self._requests: list[RequestCost] = []

    def record(self, req: RequestCost) -> None:
        self._requests.append(req)

    def cost_per_request(self, req: RequestCost) -> float:
        pricing = MODEL_PRICING.get(req.model_id)
        if not pricing:
            return 0.0
        return (
            req.input_tokens * pricing["input"] / 1_000_000
            + req.output_tokens * pricing["output"] / 1_000_000
        )

    def cost_by_version(self) -> dict[str, dict[str, Any]]:
        """Aggregate cost per deployment version."""
        groups: dict[str, list[RequestCost]] = {}
        for req in self._requests:
            groups.setdefault(req.version_digest, []).append(req)

        result = {}
        for digest, reqs in groups.items():
            costs = [self.cost_per_request(r) for r in reqs]
            result[digest] = {
                "n_requests": len(reqs),
                "total_cost": round(sum(costs), 4),
                "avg_cost": round(sum(costs) / len(costs), 6),
                "total_input_tokens": sum(r.input_tokens for r in reqs),
                "total_output_tokens": sum(r.output_tokens for r in reqs),
            }
        return result

    def daily_spend_alert(self, daily_budget: float) -> dict[str, Any]:
        """Alert if today's spend exceeds budget.  Prevents denial-of-wallet."""
        today_start = time.time() - 86400
        today_costs = [
            self.cost_per_request(r)
            for r in self._requests
            if r.timestamp >= today_start
        ]
        total = sum(today_costs)
        return {
            "total_today": round(total, 4),
            "budget": daily_budget,
            "alert": total > daily_budget,
            "pct_used": round(total / max(daily_budget, 0.01) * 100, 1),
        }


# --- Cache Key With Prompt Hash ---------------------------------------------
# A new prompt_hash changes the system prefix -> compulsory cache miss.
# Omitting prompt_hash from cache keys serves STALE completions after
# a promote.

def cache_key(bundle: ReleaseBundle, user_query_hash: str) -> str:
    """Include prompt_hash in the cache key or stale completions survive promote.

    Cache key = (model_id, prompt_hash, schema_hash, decoding_params, query_hash).
    """
    return "|".join([
        bundle.model_id,
        bundle.prompt_hash,
        bundle.schema_hash,
        bundle.decoding_params,
        user_query_hash,
    ])


# --- Demo: End-to-End Promotion Flow ----------------------------------------

if __name__ == "__main__":
    # 1. Create registry and register initial bundle
    registry = ModelRegistry()
    v1 = registry.register(ReleaseBundle(
        model_id="gpt-5.6-luna",
        prompt_hash=prompt_hash("You are a helpful assistant.", {"temperature": 0}),
        schema_hash="schema_v1",
        decoding_params="t0_max400",
        index_gen="idx-3",
        eval_suite_id="gate-v4",
    ))
    etag = registry.set_alias("champion", v1.version, actor="bootstrap")
    print(f"[registry] v{v1.version} registered as @champion (etag={etag})")

    # 2. New prompt iteration -> new version
    new_prompt = "You are a helpful, concise assistant. Always cite sources."
    v2 = registry.register(replace(v1,
        prompt_hash=prompt_hash(new_prompt, {"temperature": 0}),
        version=0,
    ))

    # 3. CI eval gate (fail-closed)
    eval_results = [
        EvalResult(task_id=f"t-{i}", score=0.9, judge_scores=[0.88, 0.91, 0.90])
        for i in range(200)
    ]
    gate_result = ci_eval_gate(eval_results, threshold=0.85, min_samples=50)
    print(f"[ci_gate] pass={gate_result['pass']} mean={gate_result['mean_score']} "
          f"n={gate_result['n']}")
    assert gate_result["pass"], "Gate should pass with score 0.9"

    # 4. Stage as @challenger, then canary at 5%
    registry.set_alias("challenger", v2.version, actor="engineer")
    flag = CanaryFlag(salt="exp-42", pct=5, candidate_bundle=v2)

    # 5. Sticky assignment demo
    assignments = [sticky_assignment(f"user-{i}", "exp-42", 5) for i in range(1000)]
    candidate_count = sum(1 for a in assignments if a == "candidate")
    print(f"[canary] 5% flag: {candidate_count}/1000 in candidate bucket")

    # Stickiness check: same user always gets same bucket
    for _ in range(10):
        assert sticky_assignment("user-42", "exp-42", 5) == assignments[42]

    # 6. Drift detection
    detector = DriftDetector()
    for _ in range(100):
        detector.record_baseline("Short answer.", "factual")
    for _ in range(80):
        detector.record_current("A much longer, more verbose answer than before.", "factual")
    for _ in range(20):
        detector.record_current("Creative tangent.", "creative")  # category shift

    length_drift = detector.check_length_drift(threshold_pct=20.0)
    cat_drift = detector.check_category_drift(threshold_pct=15.0)
    print(f"[drift] length_drift={length_drift['drift']} "
          f"pct_change={length_drift['pct_change']}%")
    print(f"[drift] category_drift={cat_drift['drift']} "
          f"max_shift={cat_drift['max_shift_pct']}%")

    # 7. Cost tracking
    tracker = CostTracker()
    for i in range(100):
        tracker.record(RequestCost(
            version_digest=v1.digest(),
            input_tokens=2000, output_tokens=400,
            model_id="gpt-5.6-luna",
        ))
    for i in range(50):
        tracker.record(RequestCost(
            version_digest=v2.digest(),
            input_tokens=2000, output_tokens=400,
            model_id="gpt-5.6-luna",
        ))
    by_version = tracker.cost_by_version()
    for digest, stats in by_version.items():
        print(f"[cost] {digest[:12]}: {stats['n_requests']} reqs, "
              f"${stats['total_cost']:.4f} total, ${stats['avg_cost']:.6f}/req")

    # 8. Rollback decision
    rollback = RollbackDecision(
        flag=flag, registry=registry, previous_champion_ver=v1.version,
    )
    decision = rollback.evaluate_and_rollback(
        current_score=0.78, threshold=0.85, error_rate=0.02,
    )
    print(f"[rollback] action={decision['action']} tier={decision['tier']} "
          f"flag_pct={decision['flag_pct']}")

    # 9. Cache key includes prompt_hash
    key = cache_key(v1, hashlib.sha256(b"user query").hexdigest()[:16])
    print(f"[cache] key={key}")

    # 10. Sample size for A/B
    n = sample_size_for_ab(baseline_rate=0.85, min_detectable_effect=0.03)
    print(f"[ab_test] Miller n ~ {n} for 3pp at alpha=0.05, power=0.80")

    print("\n[ok] All LLMOps patterns demonstrated successfully.")
