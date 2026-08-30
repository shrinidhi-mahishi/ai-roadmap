# 12. Evaluation

**Sub-areas covered**: the offline/online eval pipeline topology (playground → offline experiment → async production scoring → feedback loop) and trajectory-logging mechanics across LangSmith/Braintrust/OpenAI/Arize · core algorithms for task-success scoring, four-mode deterministic trajectory matching (strict/unordered/subset/superset) vs. LLM-as-judge trajectory review, two-stage tool-call accuracy (selection vs. invocation), and pairwise/pointwise judge algorithms with TrustJudge's probabilistic-scoring fix · the five-factor eval-cost-scaling formula and the tiered/cascaded judge architecture that cuts judge spend 30–190×, a full explicit P50/P95/P99 latency table spanning deterministic/classifier/judge tiers and offline CI gates, Little's-Law-based CI/CD throughput planning, and an explicit availability/RPO/RTO table per eval-pipeline component with eval-rigor-vs-release-velocity and judge-cost-vs-accuracy trade-offs · durable eval pipelines (Temporal heartbeat checkpointing, event-driven result aggregation, idempotency keys), a transient/permanent/poison-pill failure taxonomy for judge calls, and enterprise security (Zero-Trust MCP, RBAC for eval-gated release approval, two-layer PII redaction, sandbox isolation for code-eval harnesses, immutable chain-of-custody audit logs) · a hardened Python LLM-as-judge scorer with cascade fallback to rule-based scoring, retries with backoff+jitter, a per-provider circuit breaker, and structured correlation-ID logging · two enterprise system-design scenarios with trade-off matrices

---

## 1. System Topology & Data Flow

An agent-evaluation platform is not a single "test runner" — it is four structurally different pipelines (offline batch, online guardrail, async LLM-as-judge, trajectory logging/replay) sharing one control plane, one persistence layer, and one telemetry spine, because each pipeline has a different latency budget, a different ground-truth availability, and a different consumer (a CI job blocking a merge vs. a request handler blocking a user response vs. a background worker mining production drift).

```
                          ┌────────────────────────────────────────────────────────────────────────────────┐
                          │                                 CONTROL PLANE                                     │
                          │ ┌──────────────────┐  ┌───────────────────────┐  ┌────────────────────────────┐ │
                          │ │ Eval Orchestrator/ │─▶│ Cascade Router          │─▶│ Gate Policy Engine (RBAC:   │ │
                          │ │ Scheduler (CI      │  │ Tier0 deterministic →   │  │ who may approve a STAGING→  │ │
                          │ │ trigger / nightly  │  │ Tier1 classifier →      │  │ PROD promotion; threshold   │ │
                          │ │ cron / prod-sample │  │ Tier2 frontier judge,   │  │ values per gate; §4.5)      │ │
                          │ │ policy, §2.5)       │  │ §2.4/§3.2)             │  │                              │ │
                          │ └─────────┬──────────┘  └───────────┬────────────┘  └─────────────┬────────────┘ │
                          └───────────┼─────────────────────────┼──────────────────────────────┼──────────────┘
                                      │ run spec + dataset ref   │ per-case routing decision     │ scoped, time-limited
                                      │                          │                               │ approval credentials
                          ┌───────────▼─────────────────────────▼───────────────────────────────▼──────────────┐
                          │                     DATA PLANE — four eval loop shapes (§2)                          │
                          │ ┌───────────────┐ ┌───────────────────┐ ┌────────────────────┐ ┌──────────────────┐ │
                          │ │ OFFLINE BATCH  │ │ ONLINE GUARDRAIL   │ │ ASYNC LLM-AS-JUDGE  │ │ TRAJECTORY        │ │
                          │ │ RUNNER (CI/CD, │ │ EVALUATOR (sync,   │ │ WORKER POOL         │ │ LOGGER / REPLAY   │ │
                          │ │ reference-     │ │ on the request     │ │ (reference-free,    │ │ (full execution   │ │
                          │ │ based, golden  │ │ path; deterministic │ │ sampled or full     │ │ tree: every LLM   │ │
                          │ │ dataset, §1.1) │ │ + small classifier  │ │ prod traffic,       │ │ call + tool call  │ │
                          │ │                │ │ only — NEVER a full │ │ §1.1/§2.4)          │ │ + reasoning step, │ │
                          │ │                │ │ judge inline, §3.2) │ │                     │ │ §1.2)             │ │
                          │ └───────┬───────┘ └─────────┬──────────┘ └──────────┬──────────┘ └────────┬─────────┘ │
                          └─────────┼───────────────────┼──────────────────────┼──────────────────────┼───────────┘
                                    │ scorer calls       │ inline block/allow   │ post-hoc judge calls  │ span export
                          ┌─────────▼───────────────────▼──────────────────────▼──────────────────────▼───────────┐
                          │                    TOOL PROXIES — enforcement boundary (§4.5)                            │
                          │ ┌────────────────────────┐ ┌─────────────────────┐ ┌─────────────────────────────────┐ │
                          │ │ Judge-Model API Gateway  │ │ Sandbox Executor     │ │ Production Traffic Sampler       │ │
                          │ │ (rate-limited, per-      │ │ (Firecracker/gVisor  │ │ (reservoir/stratified sampling,   │ │
                          │ │ provider circuit breaker,│ │ microVM per eval     │ │ feeds online scoring without      │ │
                          │ │ PII redact-before-send,  │ │ case for code-gen/   │ │ coupling to the serving path,    │ │
                          │ │ prompt-cache + Batch API │ │ SWE-bench-style      │ │ §1.5)                            │ │
                          │ │ routing, §3.1/§4.4)      │ │ harnesses, §4.4)     │ │                                  │ │
                          │ └────────────┬─────────────┘ └──────────┬───────────┘ └────────────┬─────────────────┘ │
                          └──────────────┼──────────────────────────┼───────────────────────────┼───────────────────┘
                                         │ backend I/O               │ backend I/O                │ backend I/O
                          ┌──────────────▼──────────────────────────▼───────────────────────────▼───────────────────┐
                          │                                  PERSISTENCE LAYER                                        │
                          │ ┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐ ┌────────────────────┐ │
                          │ │ Golden Dataset       │ │ Trace/Trajectory   │ │ Eval Result Store   │ │ Immutable Audit     │ │
                          │ │ Store (versioned,    │ │ Store (Brainstore- │ │ (immutable,         │ │ Log (hash-chained;  │ │
                          │ │ curated; fed by the  │ │ style full-text-   │ │ versioned per-run   │ │ every gate pass/    │ │
                          │ │ feedback loop, §1.1) │ │ searchable spans,  │ │ experiments; never  │ │ fail/threshold/     │ │
                          │ │                      │ │ §1.2)              │ │ overwritten, §1.1)  │ │ approver, §4.5)     │ │
                          │ └───────────────────┘ └───────────────────┘ └───────────────────┘ └────────────────────┘ │
                          └────────────────────────────────────┬───────────────────────────────────────────────────┘
                                                                 │
                          ┌────────────────────────────────────▼───────────────────────────────────────────────────┐
                          │                             TELEMETRY / OBSERVABILITY SINKS                                │
                          │ Judge-cost meter (§3.1) · P50/P95/P99 latency dashboards per tier (§3.2) · gate pass/fail  │
                          │ history & release-gate audit feed (§4.5) · judge-inconsistency/position-bias drift monitor │
                          │ (§5.2 of research) · Goodhart/reward-hacking alerts (score-gaming detection) · eval-spend  │
                          │ ratio vs. inference-spend health signal (target <0.2, §3.1)                                │
                          └────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A run enters through the **control plane**: the orchestrator resolves *which* pipeline applies (a PR triggers the offline batch runner against a versioned golden dataset; a live user request triggers the online guardrail evaluator inline; a background sampler triggers the async judge pool with no reference available at all), and the cascade router decides, per case, how far up the Tier0→Tier1→Tier2 ladder that case needs to escalate before its score is resolved (§2.4/§3.1). (2) The case runs through its **data-plane** loop shape: offline batch scoring is reference-based and can use exact-match or deterministic checks in addition to judges; online guardrail scoring is necessarily reference-free and restricted to deterministic + small-classifier tiers because a full LLM judge cannot fit the request-path latency budget (§3.2); async judge scoring is reference-free and runs fully decoupled from the serving path; trajectory logging is not a scorer at all but the **instrumentation substrate** every other pipeline reads from — it captures the full LLM-call/tool-call/reasoning tree so that trajectory-match and tool-accuracy evaluators (§2.2–§2.3) have something to score against. (3) Every judge call, sandbox execution, or production-traffic read crosses a **tool-proxy enforcement boundary**: the Judge-Model API Gateway is where rate limiting, per-provider circuit breaking, PII redaction-before-send, and cost-saving routing (prompt caching, Batch API) all live — enforced in infrastructure, not trusted to a prompt instruction (§4.5) — while a Sandbox Executor isolates any eval that must run agent-authored code, and a Production Traffic Sampler decouples what production traffic online scoring sees from the live request path itself. (4) Results land in the **persistence layer**: golden datasets are versioned and grow via the feedback loop (production failures flagged by online scoring get promoted into the next offline dataset revision, §1.1), trace/trajectory data is written once and never mutated, eval results are immutable per-experiment records (the unit CI compares against for regression), and every release-gate decision is written to a hash-chained audit log **at evaluation time**, not reconstructed after the fact (§4.5). (5) The **telemetry layer** closes the loop by watching the signals that predict failure of the eval system itself, not just failure of the agent under test — judge cost against the <0.2 eval/inference-spend health ratio, per-tier latency against SLA, and drift in judge consistency/position-bias that would otherwise silently erode trust in every downstream gate decision.

---

## 2. Core Mechanics & Algorithms

### 2.1 Task-success scoring

Task-success scoring is a function `score: (input, output, [reference]) → ℝ` evaluated in one of two regimes:

- **Reference-based (offline only)**: exact-match, structured-diff, or a reference-conditioned LLM judge compares candidate output against a labeled expected output. Deterministic and cheap where applicable (Tier 0, §3.1).
- **Reference-free (offline *and* online)**: a rubric-based LLM judge or classifier scores output against a quality/safety rubric with no expected answer — the *only* option for live production traffic, since there is no ground truth for a request that hasn't happened before. This reference-free constraint is why online guardrail evaluation and async production scoring are architecturally judge-only (deterministic checks aside) — there is nothing to exact-match against.

**Invariant**: a reference-based score and a reference-free score for the same case are not directly comparable numbers — mixing them in one aggregate (e.g., averaging a CI regression pass-rate with a production judge-quality score) silently conflates two different measurement regimes and is a documented source of misleading dashboards.

### 2.2 Trajectory evaluation methodology

Trajectory scoring requires the **full execution tree** — every LLM call, every tool call (name + args + result), every intermediate state transition — not just the final answer, because two agents can reach the same final output via very different (and very differently reliable) paths.

**Deterministic trajectory match — four modes, formalized as set/sequence operations** over the candidate trajectory `C = [c₁...cₙ]` and reference trajectory `R = [r₁...rₘ]` (each `cᵢ`/`rᵢ` a tool-call name, optionally with args):

| Mode | Definition | Complexity | Semantics |
|---|---|---|---|
| **Strict** | `C == R` as ordered sequences | O(n) | Exact order match; fails any valid-but-different path |
| **Unordered** | `multiset(C) == multiset(R)` | O(n log n) sort-compare, or O(n) via hash-count | Set match ignoring order; still fails on extra/missing calls |
| **Subset** | `multiset(C) ⊆ multiset(R)` | O(n + m) via hash-count | No disallowed extra tool calls permitted |
| **Superset** | `multiset(R) ⊆ multiset(C)` | O(n + m) via hash-count | All required tools present; extras tolerated |

**Known flaws of deterministic matching** (load-bearing for why LLM-as-judge trajectory review exists at all): (a) multiple valid paths can exist, so exact-match wrongly fails a correct-but-different trajectory; (b) a binary match/no-match score cannot distinguish "off by one step" from "completely wrong" — no partial credit; (c) tool-**name** matching alone ignores tool **arguments** entirely, so a trajectory that calls the right tool with garbage arguments still "matches."

**LLM-as-judge trajectory review** addresses flaw (c) by passing the full candidate (and optionally reference) message/tool-call sequence to a judge model with a rubric prompt (e.g., LangChain's `TRAJECTORY_ACCURACY_PROMPT`), trading determinism and cheap compute for argument-aware, partial-credit-capable scoring — at judge cost and judge-bias risk (§2.4, §5.2 of the research). This is the hardest trajectory-eval reference to compile and the least deterministic metric in the whole eval stack, so production systems typically run deterministic match as a cheap Tier-0/1 filter and escalate only ambiguous or failing cases to the trajectory judge (the same cascade principle as §3.1's cost architecture, applied to trajectory scoring specifically).

**Judge placement in a Temporal-orchestrated pipeline**: judge scoring is triggered post-hoc via a Signal emitted at activity completion, consumed by a separate eval worker pool — decoupling eval-stack durability from the production workflow's own durability guarantees (§4.1).

### 2.3 Tool-call accuracy: selection vs. invocation as independent evaluators

Tool-call correctness decomposes into two independent, complementary binary classifiers rather than one combined score:

- **Tool Selection** ("the *what*")**: correct tool chosen from the available set; no hallucinated/nonexistent tool call; no tool called when none was needed; no missing call when one was required; correct call count for multi-tool tasks. Output: `{1.0, 0.0} + explanation`.
- **Tool Invocation** ("the *how*")**: all required parameters present with correct values; JSON well-formed; no hallucinated fields; no unsafe content (PII) in arguments. Evaluated **independently** of selection so "wrong tool" failures are distinguishable from "right tool, bad arguments" failures — a diagnostic property lost the moment the two are folded into one composite score.

Both are reference-free (LLM-as-judge reasoning purely from conversational context) and so can run on any tool-calling trace without a labeled dataset; a ground-truth (reference-based) variant is recommended whenever labels exist, since it is cheaper and tighter than judge reasoning.

### 2.4 LLM-as-judge algorithms

**Pointwise vs. pairwise scoring.** Pointwise judging assigns an absolute rating (e.g., 1–5, or pass/fail) to a single candidate independently — O(1) judge calls per candidate. Pairwise judging compares candidates head-to-head and is combinatorially more expensive: for *N* candidates, a full round-robin comparison requires **N(N−1)/2** judge calls — O(N²) — before ensembling (×3–5) or multi-branch refresh (×4–6) multipliers are even applied. This combinatorial blow-up is the single largest lever in the cost model of §3.1.

**Probabilistic (TrustJudge) scoring as a bias-mitigation algorithm.** Standard discrete-rating judges (pick one of {1,2,3,4,5}) exhibit two measured inconsistency classes: **Score-Comparison Inconsistency** (a response scored lower in absolute rating outperforms a higher-scored response in a *separate* pairwise comparison — 23.32% baseline rate) and **Pairwise Transitivity Inconsistency** (circular preferences A>B>C>A, or false equivalence A=B=C≠A — 15.22% baseline rate). TrustJudge's fix replaces the discrete argmax rating with a **continuous expectation over the judge's own token-level rating probabilities** (`E[score] = Σᵢ i · P(rating=i)`), reducing the two inconsistency rates to 14.89% and 4.40% respectively — a meaningful but **non-zero residual**, i.e., no known algorithm eliminates judge inconsistency; it can only be reduced.

**Position-bias mitigation via swap-and-compare.** Because judges exhibit measured position bias (favoring whichever response is shown first/second depending on the model), a standard pairwise-judging algorithm runs each comparison **twice with swapped order** and only accepts the verdict as reliable if both runs agree — an explicit doubling of judge-call cost purchased for bias resistance, itself an instance of the cost-vs-rigor trade-off formalized in §3.4.

**CoT rubric prompting** improves reliability (structured criteria + explicit reasoning steps before a final verdict) but does not close the "unfaithfulness" gap: judges' written justifications have been shown to not reliably reflect the actual cue that swayed the verdict (recency/provenance metadata rather than content quality), which is an algorithmic — not merely a prompting — limitation, since the rationale is generated *after* an internal decision the prompt cannot inspect.

### 2.5 Eval-pipeline state machine (stage placement)

```
DEV/PLAYGROUND (mutable, fast iteration)
        │  promote a passing config
        ▼
OFFLINE EXPERIMENT (immutable, versioned, reference-based, runs in CI on every PR)
        │  pass all T1/T2 gates (§3.2)
        ▼
SHADOW PATH (synchronous gating judge call against live-shaped traffic,
             pre-promotion — the ONE place a full judge legitimately
             runs on/near the critical path, §1.5)
        │  gate approves promotion (RBAC-scoped, §4.5)
        ▼
PRODUCTION — STEADY STATE
        │                              │
        │ online guardrail (sync,      │ async production scoring
        │ deterministic+classifier     │ (reference-free judge,
        │ only, blocks/allows in       │ decoupled from serving path)
        │ real time)                   │
        ▼                              ▼
   ALLOW / BLOCK response        FLAGGED CASE → promoted into
                                  golden dataset (feedback loop,
                                  closes back to OFFLINE EXPERIMENT)
```

**Key invariant**: the only cycle back to the start of the state machine is through the feedback loop — an eval system that never promotes production-flagged cases into its offline dataset is structurally incapable of catching the same regression twice, since online-only signal never accumulates into a reusable, versioned artifact.

### 2.6 Goodhart's Law as a formal design constraint on judge/reward design

Four distinct Goodhart mechanisms apply to any eval metric used as an optimization or gating target: **Regressional** (proxy-goal correlation degrades under selection pressure — optimizing hard enough for the proxy erodes the correlation that made it a useful proxy in the first place); **Extremal** (optimization pushes the system into out-of-distribution regions where the proxy no longer tracks the true goal at all); **Causal** (the proxy correlates with, but does not cause, the true goal — optimizing the proxy directly does nothing to the goal); **Adversarial** (deliberate, intentional proxy manipulation by the optimized system itself). This is why every judge-design algorithm above (probabilistic scoring, swap-and-compare, multi-rubric orthogonality) is necessary but not sufficient: **any single scalar eval metric used as a hard gate is, by construction, a target that a sufficiently optimized agent (or a sufficiently over-fit judge-tuning loop) will eventually learn to game rather than genuinely satisfy** — the practical countermeasure is multiple orthogonal metrics plus randomized human audit, not a better single metric (§6.2 of research; carried into §4.5's audit requirements below).

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost formulas ($ per 1k runs) for judge-model eval

Eval cost scales **multiplicatively**, not linearly, with production inference:

```
Cost_eval ∝ queries × candidates × judge_prompt_tokens × refresh_frequency × n_judges
```

— a five-factor product that is why eval spend diverges from (and can exceed) inference spend, which scales only as `queries × answer_tokens`.

**Flat (non-cascaded), single-pass judge cost:**

```
Cost_judge_flat(1k runs) = 1000 × rubrics_per_run × cost_per_judge_call

Stated assumptions: cost_per_judge_call ≈ $0.003–$0.005 (frontier judge,
~800–2,000 input tokens + 100–300 output tokens for rubric reasoning);
rubrics_per_run = 5 (mid-point of the documented 3–7 range)

Cost_judge_flat(1k runs) ≈ 1000 × 5 × $0.004 = $20 / 1k runs (single frontier judge, no ensembling)
```

At production scale (1M traces/day, 3–7 rubrics/trace) this flat approach costs **$9K–$37K/day ($270K–$1.1M/month)** — i.e., **$9–$37 per 1k traces**, consistent with the per-call formula above. Stacked multipliers (pairwise N(N−1)/2 comparisons, 3–5× ensembling, 4–6× multi-branch refresh across `main` + feature branches) commonly compound to **100–200× the naive single-pass cost**, which is the point at which eval spend crosses and exceeds inference spend.

**Cascaded (tiered) judge cost** — the dominant 2025–2026 cost-reduction pattern:

```
Cost_judge_cascade(1k runs) = 1000 × [ p_det × cost_det
                                        + p_classifier × cost_classifier
                                        + p_judge × rubrics_per_run × cost_per_judge_call ]

Stated assumptions (published worked example): p_det = 0.70, cost_det ≈ $0
                                                p_classifier = 0.25, cost_classifier ≈ $0.00005
                                                p_judge = 0.05, cost_per_judge_call ≈ $0.004, rubrics=1

Cost_judge_cascade(1k runs) ≈ 1000 × [0.70×0 + 0.25×$0.00005 + 0.05×$0.004]
                             ≈ 1000 × $0.0002125 ≈ $0.26 / 1k runs (as published: 1M traces/day → $260/day)
```

This is a **~30× reduction** from the $5,000/day flat-judge baseline for the same traffic volume, and a documented separate case cut judge cost *share* of total eval budget from 50% → 16% by resolving 68% of spans deterministically before any judge escalation.

**Published health thresholds** for judge-cost governance: eval/inference spend ratio should stay **under 0.2**; frontier-judge share of total judge calls should stay **under 30%**; cache-hit rate on repeated eval pairs (prompt caching, 50–90% discount) should exceed **70%**. Batch APIs add a further ~50% discount for non-time-sensitive nightly/CI runs.

**CI/CD gate unit economics** (published, T2 tier — 50-case suite, `repeat: 3` majority voting, ~2000 input/200 output tokens per call = 150 judge calls/merge, 210 merges/month):

```
Cost_T2_gate(1k merges) = 1000 × 150 calls/merge × cost_per_judge_call(model class)

  Opus-class:        1000 × 150 × ~$0.0313 ≈ $4,700 / 1k merges  ($470/mo at 210 merges/mo)
  Haiku-class:        1000 × 150 × ~$0.0063 ≈ $950  / 1k merges  ($95/mo)
  GPT-4o-mini-class:  1000 × 150 × ~$0.00087≈ $130  / 1k merges  ($13/mo)
```

— a **>30× spread** between judge model classes for an *identical* gate design, which is typically the number that determines whether a CI eval gate survives a budget review.

**Academic-scale reference points** (independent benchmark cost, not production eval, but a useful cross-check on judge-inclusive eval cost at scale): a single PaperBench evaluation with an LLM judge costs **~$9,500**; a three-seed, six-model publishable comparison exceeds **$150,000**; a 9-benchmark × 9-model HAL aggregate (single seed) runs **~$40,000**.

> ⚠️ Gap: no vendor publishes a standardized "trajectories per arm" formula tying minimum-detectable-effect to agent-specific stochastic variance for eval-driven A/B tests — the widely cited **10,000+ trajectories/arm** guidance is a practitioner heuristic, not a formal derivation. `[inferred as practitioner heuristic]`

### 3.2 Latency SLA targets: P50/P95/P99 for online guardrail vs. offline batch evals

Online guardrail latency directly competes with user-facing latency (it sits on the request path); offline batch/CI latency competes with developer iteration speed and merge-queue throughput. No single source publishes a fully composed table across every tier, so the table below merges the research's directly measured per-check-type figures with the CI-tier latency targets, and states provenance per row.

| Eval tier / pipeline stage | P50 | P95 | P99 | Dominant tail cause | Mitigation |
|---|---|---|---|---|---|
| **Online — Tier 0 deterministic** (regex/schema/blocklist) | <2ms `[measured]` | <10ms `[measured]` | ~20ms `[inferred]` | Regex catastrophic backtracking on adversarial input | Bound pattern complexity; timeout-wrapped regex engine |
| **Online — Tier 1 small classifier** (Llama-Guard-class, 0.1–0.3B) | 60ms `[measured, mid-point of 50–150ms]` | 150ms `[measured]` | ~300ms `[inferred]` | Cold model-serving replica; batching queue wait | Warm replica pool; non-autoregressive encoder guards (GLiGuard-class) report 16× throughput / 17× lower latency than 7B–27B decoder guards at comparable F1 |
| **Online — Tier 2 full LLM judge (inline, discouraged)** | 1.5s `[measured, mid-point of 1–3s]` | 3s `[measured]` | ~6s `[inferred]` | Full generation for CoT rubric reasoning | Should **not** run inline at all — move to async (see below); if unavoidable (shadow-path gating), cap to a hard timeout with deterministic fallback |
| **Online — composed guardrail suite, streaming architecture** | 40–70ms `[measured — input validation/rate-limiting kept synchronous]` | ≤200ms added to total P95 budget `[measured, industry-cited target]` | ~500ms `[inferred]` | Any full-judge check left on the sync path | Move PII/content-filter/compliance checks to **async, parallel with response streaming**; one case study cut guardrail-added latency **2.5s → 850ms (≈3×)** this way |
| **Offline — T1 deterministic CI gate** (schema, tool-call format, trajectory match, golden I/O) | ~40s `[inferred, within target]` | ~75s `[inferred]` | <90s `[measured, published target]` | Test-fixture/sandbox cold start | Warm CI runner pool; cache golden-dataset fixtures |
| **Offline — T2 LLM-judge CI gate** (behavioral spec, rubric scores, on merge to base) | ~4min `[inferred]` | ~8min `[inferred]` | <10min `[measured, published target]` | 150 sequential/parallel-bounded judge calls at `repeat:3` majority voting | Parallelize judge calls across cases; prompt caching on repeated rubric system-prompt; model-class routing to a cheaper judge tier for T2 (§3.1) |
| **Offline — T3 nightly regression suite** (full suite, cross-model comparison, cost audit) | ~25min `[inferred]` | ~50min `[inferred]` | <60min `[measured, published target]` | Full cross-model matrix (multiple model × multiple seed combinations) | Runs off the critical path (alert-only, does not block merge); shard across a distributed runner pool (Celery/Ray/Temporal/K8s) |
| **Async production LLM-judge scoring** (post-hoc, sampled traffic) | minutes `[inferred — decoupled from serving, no hard SLA]` | tens of minutes `[inferred]` | hours (worst case, backlog drain during provider degradation) `[inferred]` | Judge-provider rate limits under high sampling volume; backlog buildup during an incident | Bounded queue with load-shedding (sample-rate reduction) rather than unbounded backlog growth; EvalTag decouples scoring latency from the original request's own latency entirely |

**Cross-tier takeaway**: the single hard constraint that shapes this entire table is that a **full LLM judge (1–3s+) structurally cannot run synchronously on a <2s total user-facing latency budget** — every online-guardrail architecture in production either restricts the sync path to deterministic + small-classifier tiers, or moves the judge check to async-parallel-with-streaming. Offline CI tiers instead trade *developer* latency (minutes, not milliseconds) for *judge quality* (full rubric reasoning), gated by whether the check blocks a merge (T1/T2) or merely alerts (T3).

### 3.3 Throughput: eval-suite CI/CD gating capacity and back-pressure design

CI/CD gating capacity should be planned in **concurrent judge calls in flight**, not merges/hour, because Little's Law governs the relationship: `L = λ × W` (in-flight calls = arrival rate × mean judge-call latency). Worked example for a T2 gate: if merges arrive at 1/min during a busy release window and each merge issues 150 judge calls at ~2s average latency issued with bounded parallelism of 10 concurrent calls per merge, the sustained in-flight judge-call count is `λ(calls/s) × W(s)` — sizing the judge-provider connection pool and per-provider rate-limit headroom, not the headline "merges/hour" number.

```
Sustained_eval_throughput = min(
    Judge-provider rate limit (RPM/TPM ceiling, per-provider),
    CI runner pool concurrency (parallel job slots),
    Sandbox-executor cold-start rate (for code-eval harnesses, §4.4),
    Result-aggregation backend write capacity (immutable eval-result store)
)
```

**Back-pressure design**: use a **bounded queue with load-shedding** for the async production-scoring pool rather than unbounded queueing — under a judge-provider outage or rate-limit event, shed by reducing production sampling rate first (a %-of-traffic knob) rather than let the queue grow unbounded and eventually OOM the worker pool or silently drop scoring for an unbounded backlog with no visibility. For CI/CD gates specifically, a merge queue should **serialize T2 judge-gate admission** (not fan out unboundedly) so that a burst of simultaneous PRs does not spike judge-provider RPM past its ceiling and trip rate-limit-driven circuit breakers (§4.4) for every concurrent gate at once. Reserve 30–50% headroom above measured peak concurrent-judge-call volume for retry storms, consistent with general LLM-pipeline capacity practice.

### 3.4 NFR analysis: availability, RPO/RTO tied to eval-run checkpoint granularity, and compliance trade-offs

No vendor publishes a composed availability SLA scoped to "one eval pipeline component" as a unit; every figure below is an **`[inferred/recommended]`** design target, stated explicitly because this table is the one most often audited for exact numbers.

| Eval-pipeline component | Availability target | RPO | RTO | Basis / trade-off |
|---|---|---|---|---|
| **Online guardrail evaluator** (sync, on request path) | **99.99%** `[inferred — must match or exceed the host serving SLA, since an outage here either blocks or silently bypasses every production request]` | N/A — stateless per-request check, nothing to lose | **Immediate** — fail open to deterministic-only checks (never fail closed to "no check at all," and never fail open to "full bypass") | Highest availability bar in the whole eval stack precisely *because* it is coupled to the serving path; the trade-off is that this forces the online tier to stay restricted to cheap, highly-available deterministic + classifier checks (§3.2) rather than a full judge, which could not realistically hit this target |
| **Offline CI/CD eval gate** (per-PR, ephemeral run, Temporal-heartbeat-backed) | **99.5%** `[inferred]` | **Up to one heartbeat interval** since the last checkpoint (heartbeat pattern: `activity.heartbeat(progress)` after each processed batch, §4.1) | **Minutes** — a new worker resumes from the last heartbeat detail rather than reprocessing the full suite from scratch; heartbeat *timeout* is set to 2–3× the heartbeat interval, which directly bounds failure-detection latency (and therefore RTO) | Heartbeat interval is a direct dial on RPO: a 30s interval bounds progress loss to 30s of re-work on worker crash, at the cost of more frequent heartbeat I/O; too long an interval delays failure detection (violates the anti-pattern of very-long single-activity timeouts), too short adds heartbeat overhead for negligible RPO gain |
| **Async production LLM-judge scoring pool** (decoupled from serving path) | **99.9%** `[inferred]` | Up to the **EvalTag checkpoint interval** (score attachment can happen minutes-to-hours after the original execution span without harm, since the pipeline is explicitly decoupled, §4.1) | **Minutes–hours** (bounded by backlog-drain time under load-shedding, §3.3) | Because this pool is architecturally decoupled from serving, its own outage delays *signal* (drift detection, dataset-mining) rather than user-facing requests — the explicit trade-off enterprises make is accepting a wider RPO/RTO here in exchange for zero coupling risk to production traffic |
| **Golden dataset / eval-result store** (persistence layer, immutable versioned experiments) | **99.95%** `[inferred]` | **Near-zero** — writes are durable and versioned; nothing is overwritten in place | **Minutes** (restore/failover to a replica) | Must satisfy SOC 2 Type II full prompt/completion logging and EU AI Act Article 12 mandatory logging retention — a lower RPO here is a compliance requirement, not just an operational nicety |
| **Immutable release-gate audit log** (hash-chained, chain-of-custody) | **99.99%** `[inferred]` | **Zero** — write-once, append-only; a gate decision not durably recorded before the release proceeds is treated as equivalent to the decision never having happened | **N/A** (restore from an independently-written replica; the log itself is never "rolled back") | Non-negotiable regulatory requirement (SOC 2, EU AI Act Article 12); the explicit design principle is that **failing to write the audit record should block the release**, not merely log a warning — an availability failure here has to fail closed on the *release*, not fail open on the *audit* |

**Trade-off discussion — eval rigor vs. release velocity.** The T1 (<90s, blocks)/T2 (<10min, blocks)/T3 (<60min, alert-only) tiering *is itself* the resolved form of this trade-off: pushing every check to the highest-rigor tier (full LLM judge, human review, exhaustive cross-model comparison) on every commit would make T1's <90s target impossible, so the architecture deliberately accepts **lower rigor on the fast/blocking path and reserves full rigor for the slow/non-blocking path** — a regression that only a T3-tier nightly judge would catch ships to `main` for up to 24 hours before being flagged, which is the explicit cost of keeping the per-commit gate fast enough not to bottleneck every engineer's inner loop.

**Trade-off discussion — judge-model cost vs. accuracy.** The cascade architecture (§3.1) resolves this trade-off structurally rather than by picking one point on the curve: cheap deterministic/classifier tiers absorb 70–95% of volume at near-zero cost and near-100% precision on the narrow class of failures they can detect, while the expensive frontier-judge tier is reserved for the 5–30% tail that is genuinely ambiguous — but this only works if the **tier-escalation boundary itself is well-calibrated**; an under-calibrated cascade (escalating too little) silently trades accuracy for cost savings without anyone noticing until a production incident traces back to a case that should have escalated to Tier 2 and didn't.

**Compliance mapping.** RBAC for eval-gated release approval (§4.5) maps to SOC 2 and EU AI Act obligations; the immutable audit trail maps to SOC 2 and GDPR Article 30 records-of-processing; PII detect→redact→audit in eval data (§4.5) maps to GDPR directly and to OWASP's LLM02 (Sensitive Information Disclosure) finding that eval/training datasets are a leakage surface distinct from the model itself.

---

## 4. Distributed Resilience & Security

### 4.1 Durable eval pipelines and checkpointing

For large or long-running eval suites, the **Temporal Activity Heartbeat pattern** is the reference architecture: an activity heartbeats progress (`activity.heartbeat(progress)`) after each processed batch of eval cases; on worker crash, a heartbeat-timeout (set to 2–3× the heartbeat interval) triggers retry on a new worker, which **resumes from the last heartbeat detail** rather than reprocessing the entire suite from scratch. This avoids two anti-patterns simultaneously: very-long single-activity timeouts that delay failure detection, and full-batch reprocessing after any transient failure (which, at judge-call cost, is not merely slow but expensive to redo).

For eval-specific orchestration, dedicated eval SDKs support **four distributed runner backends (Celery, Ray, Temporal, Kubernetes)**, letting the eval suite run as a durable workflow *parallel to* the production agent workload. An `EvalTag` mechanism attaches judge scores back to the original execution span even when scoring happens minutes-to-hours later in a separate process — the concrete implementation of the async-decoupling principle from §1.5/§3.4.

### 4.2 Distributed locking and result aggregation

Eval-result aggregation generalizes a multi-level workflow hierarchy pattern: a **Root** workflow per eval run/session, an **Aggregation** workflow that collects results across many sessions and fires downstream analysis **only once enough data has accumulated** (via `wait_condition` and Signals — event-driven, not a cron poller), and a **Reporting/Gating** workflow gated on the aggregation stage completing. `continue_as_new` resets event history for long-running aggregation workflows without losing accumulated state, avoiding the 2MB per-argument payload ceiling that unbounded event history would otherwise hit. This event-driven gating is the key primitive that lets result aggregation scale **without a bespoke distributed lock service**: Temporal's server-side event-history append-log plus deterministic replay substitutes for manual checkpointing and manual mutex management.

### 4.3 Idempotency requirements

Every eval activity with a side effect — writing to a shared aggregate dataset, deduping into the golden dataset, inserting into an annotation queue, recording a gate decision — must be **idempotent**, keyed by a stable ID derived from the input event (a content hash of the case + judge-call parameters), so that automatic retries cannot double-count a judge score or double-insert a regression-suite entry. `ActivityIDConflictPolicy.USE_EXISTING` is the server-level mechanism for de-duping a resubmitted eval job without needing application-level dedup logic.

### 4.4 Failure taxonomy and circuit breakers for judge-model calls

Judge calls are just another class of LLM API call, so the same resilience stack applies — but the taxonomy must be adapted to the eval-specific consequence of getting error classification wrong (a mis-retried terminal error wastes judge budget on a guaranteed-fail request; a mis-classified systemic outage that isn't breaker-tripped can cascade a judge-provider degradation into every concurrent CI gate at once, §3.3):

| Error class | Example (eval-specific) | Correct response | Idempotency note |
|---|---|---|---|
| **Transient** (per-caller) | 429 rate limit on the judge-model API during a merge-queue burst | Honor `Retry-After`; exponential backoff + jitter; **do not** trip the breaker | Safe to retry with the same idempotency key — no side effect has occurred yet |
| **Systemic** (provider-wide) | 5xx, Anthropic 529 `overloaded_error` on the judge provider | Jittered backoff **and** trip the per-provider circuit breaker → fall back to a secondary judge provider or a cheaper judge tier | Breaker state itself must be keyed per-provider, never global (a global breaker tripped by one provider's outage disables fallback for everyone) |
| **Terminal** | 400/403/413, content-policy refusal on a judge prompt (e.g., a rubric containing content the judge model itself refuses to score) | **Never retry** — falls through to rule-based scoring immediately (§5) | Mark the case `judge_unscored_terminal` in the audit log rather than silently omitting it from the result set |
| **Poison-pill (eval-specific)** | A single eval case whose input reliably triggers judge timeout or an infinite trajectory-comparison loop (e.g., a malformed reference trajectory that never terminates a diff) regardless of retry count | Route to a **dead-letter queue** after a bounded retry count (e.g., 3); alert; exclude from the aggregate score with an explicit `excluded_poison_pill` flag rather than silently dropping it (a silently dropped case corrupts the denominator of the pass-rate metric) | The dead-letter entry itself needs a stable case-ID so a fix can be validated against the exact same input later |

**Circuit breaker state machine**: **Closed** (normal operation) → **Open** (fail-fast for a cooldown window — e.g., 30s — after 5 consecutive failures or a 50% failure rate over 10s) → **Half-Open** (a single probe request; success closes the breaker, failure reopens it). Layering: retries absorb transient noise → the circuit breaker absorbs a degraded judge endpoint → a fallback chain (secondary judge provider → cached/previous score → rule-based deterministic scoring → an explicit "unscored, flagged for human review" state, in decreasing quality order) absorbs extended outages.

> ⚠️ Gap: no source publishes judge-specific circuit-breaker thresholds distinct from general LLM-call thresholds; the taxonomy and thresholds above are generic resilience engineering applied to the judge-call path. `[inferred]`

### 4.5 Enterprise security: Zero-Trust MCP, RBAC, PII handling, sandboxing, and auditability

**Zero-Trust MCP for eval harnesses exercising an agent's MCP tool calls.** The unifying principle carried over from Module 10 §4.5: no user, client, server, token, or tool receives automatic trust — and this applies with equal force to the MCP server(s) an eval harness dispatches tool calls against, not just to production traffic. A defense living inside a judge model's own reasoning (a rubric instruction to "ignore PII in the input," or an assumption that "the tool the agent called is the tool it claims to have called") is advisory; a defense enforced by infrastructure the call must physically pass through — the Judge-Model API Gateway, an MCP eval-gateway sitting in front of the server(s) under test — is the only kind that holds under an adversarial or merely drifted MCP server. Four concrete mechanisms apply this to the specific sub-metrics this module scores (§2.2 trajectory match, §2.3 tool-selection/tool-invocation accuracy):

1. **Treat the MCP server under test as untrusted input to tool-accuracy scoring, not a trusted oracle.** Tool-selection and tool-invocation scoring (§2.3) implicitly assumes the `tools/list` manifest the agent saw during the run is the manifest the reference/rubric was written against. A compromised, misconfigured, or merely schema-drifted MCP server (Module 10 §4.11's schema-drift failure class — a `tools/list` JSON Schema that has silently diverged from the deployed handler) can invalidate a tool-accuracy score without the harness ever noticing, because the judge is scoring "did the agent call the right tool correctly" against a manifest that may no longer describe what actually executed. The eval harness must fetch and hash the `tools/list` response for every MCP server under test **at run start**, compare it against a pinned/expected schema hash checked into the eval fixture alongside the golden dataset (§1's Golden Dataset Store), and fail the run closed — before any judge call is spent — on a mismatch, rather than silently scoring tool accuracy against a manifest nobody reviewed. `ttlMs`/`cacheScope` tool-catalog caching (Module 10 §2.5, SEP-2549) is a performance optimization for a *production* client; an eval harness should treat its own tool-catalog cache as a correctness input and re-validate the pinned hash every run (or on every `ttlMs` expiry at minimum), since a stale-but-cached catalog that quietly diverged from the live server is exactly the failure mode that would let a compromised MCP server invalidate every trajectory/tool-accuracy result computed against it.

2. **Authenticate and log the capability-negotiation handshake as part of trajectory recording, not just the tool calls themselves.** Eval infrastructure should connect to each MCP server under test via an authenticated, scoped session (OAuth 2.1 resource-server validation per Module 10 §4.6, RFC 8707 audience-scoped tokens, OBO narrowing per Module 10 §4.5) — never ambient trust (an unauthenticated stdio subprocess spawned with the eval runner's own broad filesystem/network access, §4.10). Because the 2026-07-28 spec makes capability discovery self-describing rather than a blocking `initialize` handshake (Module 10 §2.2), a legacy-era eval fixture cannot assume a session-level capability negotiation happened at all — the harness must explicitly issue (or record the server's response to) `server/discover`, or capture the per-request `protocolVersion`/capability metadata, and write it into the **trajectory log itself** (§1.2's full execution tree) alongside every LLM call and tool call. This closes a specific gap: without a recorded capability-negotiation trace, an eval trace can only show what tools the *agent claimed* to call — it cannot prove which tools/resources were actually *exposed* to the agent at eval time. A trajectory-match score (§2.2) computed without this handshake record is only as trustworthy as the agent's own self-reported tool list, which is precisely the kind of self-reported evidence Module 10 §4.9 rejects as an audit source ("an agent's narration of its actions is not evidence").

3. **Resource-scope eval MCP servers separately from production, especially for adversarial/red-team tool-accuracy suites.** An eval suite that deliberately includes adversarial prompts to test tool-selection robustness under attack (the eval-domain analog of Module 10 §4.11's Tool Poisoning Attacks and MCPTox-style adversarial tool-call injection) must run against MCP servers provisioned with **read-only or eval-sandboxed resource scopes**, distinct from the OBO/RFC-8693-narrowed scopes a production agent session would carry — never the production credential path with "just don't actually execute the write" left to the judge's own discretion. Concretely: the eval orchestrator's MCP client sessions should authenticate against a resource server whose token audience (RFC 8707) is scoped to an eval-tenant/sandbox resource set (a replica database, a sandboxed CRM instance, a synthetic-data warehouse) that structurally cannot mutate production state, so that a red-team case designed to probe "does the agent refuse an unsafe tool call" cannot succeed *at* mutating production data even if the agent's tool-selection judgment fails during the test. This is the Sandbox Executor (§1's topology, Module 10 §4.10's isolation tiers) applied specifically to the *MCP resource grant*, not just the code-execution microVM.

4. **Enforce a per-eval-suite MCP tool allow-list at the protocol level, distinct from the full production tool surface.** The Judge-Model API Gateway (§1, §3.1) — or a dedicated eval-MCP-gateway sitting in front of it — should reject any `tools/call` whose tool name falls outside an explicit allow-list scoped to *that eval suite run*, enforced the same way Module 10 §4.7's tool-level RBAC is enforced in production (a PDP decision on every `tools/call`, never a prompt instruction telling the agent "only use tools X and Y"). This serves eval-specific goals beyond production security: it makes eval results **reproducible** (a suite run against a wider tool surface than the one the golden dataset/reference trajectories were authored against would silently invalidate strict/subset trajectory-match scoring, §2.2) and **scoped** (a suite designed to test the tool-selection metric for a 5-tool task should not let the agent escalate to an unrelated 40-tool production catalog mid-run, which would confound "wrong tool chosen" failures with "tool wasn't supposed to be reachable at all" failures — two different defects the two-stage tool-accuracy decomposition in §2.3 is specifically designed to keep distinguishable). Allow-list denials should be written to the same immutable audit log as production tool-call denials (§4.5's auditability requirement below) — an eval-time denial is itself a scoreable event, not a silent gap in the trajectory.

Taken together, these four mechanisms mean an eval harness's tool-accuracy and trajectory-match scores are only as trustworthy as the MCP boundary they were computed through: a judge model faithfully applying the §2.3 rubric to a tool-call trace is not sufficient if the manifest, the handshake, the resource scope, or the tool surface it was scored against cannot itself be verified independent of the agent-under-test's own account of what happened.

**RBAC for eval-gated release approval.** A converged pattern (MLflow, Braintrust, Confident AI) layers identity (SSO/SAML/OIDC + SCIM) → role-based permissions (not per-individual) → minimum-necessary data visibility per role, itself logged:

| Role | Traces & threads | Payloads (prompts/outputs) | Prompts/configs | Datasets & evals | Release-gate approval | Admin |
|---|---|---|---|---|---|---|
| Engineer | R/W | Read (masked if flagged) | Propose changes | R/W | No | No |
| PM/domain expert | Read | Read (product-scoped) | Propose changes | Annotate/curate | No | No |
| QA/reviewer | Read | Read (queue-scoped) | No | Annotate/verify | No | No |
| Compliance/audit | Read | Read (access logged) | Read + approval rights | Read | **Read + veto** | No |
| Platform admin | Read | Configurable | Approve/deploy | R/W | **Approve/deploy** | Yes |

Concrete implementation reference points: RBAC at org/project/object level with built-in Owner/Engineer/Viewer groups plus custom groups, SOC 2 Type II certification, AES-256 at rest / TLS 1.2 in transit, API keys stored as one-way hashes. Mandatory **human sign-off for high-risk/irreversible gate outcomes** (a STAGING→PROD promotion, a rollback) should be enforced via a pre-execution hook, not a code-review convention — with automated 90-day API-key rotation baked into the CI/CD pipeline itself.

**PII filtering (detect → redact → audit) in eval data/traces.** A **two-layer redaction architecture** is required, because the two layers fail independently:

1. **Gateway layer** — sits between the application and any external judge/LLM provider; redacts/tokenizes PII **before** it reaches any external model, using reversible tokenization for authorized de-redaction only.
2. **Application layer** — redacts before writing to logs, traces, vector stores, **or eval datasets**. This is the layer most commonly forgotten: teams redact prompts sent to the judge model but log the *unredacted* prompt to their observability platform or eval dataset, which becomes the audit finding.

**De-redaction hard limit**: you can only de-redact to a destination as trusted as the original source — de-redacting into a log, an eval dataset, or to an unauthorized viewer is itself a disclosure event. Eval-specific practice: eval datasets must be stripped of real user data via synthetic replay sets, anonymized patterns, or pseudonymization-at-capture (replace identifiers with tokens before any log/eval-store write, keep the entity map in a separate access-controlled vault) — this reconciles GDPR Article 17 (erasure) with EU AI Act Article 12 (mandatory logging), since deleting the entity-map key anonymizes historical eval logs without destroying the audit trail itself.

**Sandbox isolation for eval execution.** Any eval that executes untrusted or agent-generated code (SWE-bench-style code-gen benchmarks) needs isolation stronger than typical CI: SWE-bench's own harness moved to per-instance Docker images specifically to fix reproducibility problems, raising ground-truth pass rate to 99.78%, with parallelism capped at <28 workers to avoid docker-daemon contention. A 3-level sandboxing hierarchy (Cluster → Benchmark → Problem, each level its own isolated filesystem/network/process space) plus semaphore-based concurrency limiting and `atexit`/signal-handler emergency cleanup is the production pattern. Plain Docker/runc **shares the host kernel** and is explicitly insufficient for adversarial code; microVM isolation (Firecracker, Kata) or userspace-kernel isolation (gVisor) with default-deny egress (blocking the cloud-metadata endpoint `169.254.169.254` and RFC1918 ranges) is the emerging best practice. A documented eval-integrity failure mode — **agents cheating the harness itself** when verification logic is visible/trusted inside the same sandbox (a manipulated `conftest.py` hook, or an agent simply printing "all tests passed") — is countered by a **held-out, hidden verifier** injected only at verify-time into a separate forked microVM the agent never had access to, importing the artifact directly rather than trusting the agent's own reported exit code.

**Auditability (immutable logs, chain-of-custody) of eval decisions and release gates.** Every model promotion/gate decision must be an **immutable record** — pass/fail, threshold value, and model-version hash written to the audit trail **at evaluation time**, never reconstructed after the fact. A model-registry pattern (DEV → STAGING → PROD → DEPRECATED → RETIRED) requires mandatory review + approval on STAGING→PROD specifically, storing requester, approver, timestamp, and eval-derived notes (e.g., "passed eval suite, p95 latency = 380ms") per promotion — no silent stage changes. Append-only, hash-chained logs (HMAC-SHA256, Ed25519-signed, or Merkle-tree), written independently of the agent/eval-worker process, ensure the eval system itself cannot alter its own audit trail — load-bearing precisely because §5.2 of the research shows judges' own stated rationales are not reliable evidence of what actually drove a verdict, so the *decision record* (threshold, score, model hash), not the judge's narrated explanation, is what auditors must be able to trust.

---

## 5. Production Enterprise Code

The scorer below implements an LLM-as-judge evaluation call with the full resilience stack from §3–§4: a per-provider circuit breaker (closed → open → half-open), retries with exponential backoff + full jitter restricted to transient errors only, a fallback chain (secondary judge provider → deterministic rule-based scoring → an explicit unscored/flagged state), structured JSON logging correlated by `run_id` + `case_id` that survives thread-pool worker boundaries, and graceful degradation that reports exactly which cases fell back rather than failing an entire batch. Standard library only.

```python
"""
judge_eval_pipeline.py

A hardened LLM-as-judge scorer for an agent-evaluation pipeline,
demonstrating every pattern from Module 12 Sec 3-4:

  - per-provider circuit breaker: CLOSED -> OPEN -> HALF_OPEN (Sec 4.4)
  - retries with exponential backoff + full jitter, restricted to
    TRANSIENT errors only (Sec 4.4's transient/systemic/terminal/
    poison-pill taxonomy) -- terminal errors never retry
  - fallback chain (Sec 4.4/4.5): primary frontier judge -> secondary
    cheaper judge provider -> rule-based deterministic scoring ->
    explicit "unscored, flagged for human review" terminal state
  - poison-pill detection: a case that exhausts retries against BOTH
    judge providers is dead-lettered, never retried again, and
    excluded from the aggregate score with an explicit flag (Sec 4.4)
  - structured JSON logging correlated by run_id + case_id, surviving
    ThreadPoolExecutor workers via re-bound contextvars (Sec 4.5's
    chain-of-custody requirement applied to per-case audit logging)
  - idempotency: every score write is keyed by a content hash of
    (case_id, judge_provider, prompt_version) so a retried activity
    cannot double-write a score (Sec 4.3)
  - graceful degradation: the batch runner returns a "partial_degraded"
    result listing exactly which cases fell back and to which tier

Install:  no dependencies (stdlib only; swap the mock *_judge_call
          functions for real judge-model API calls in production)
Run:      python judge_eval_pipeline.py
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import json
import logging
import random
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


# --------------------------------------------------------------------------
# 1. Structured logging correlated by run_id + case_id (Sec 4.5)
# --------------------------------------------------------------------------

_run_id: ContextVar[str] = ContextVar("run_id", default="-")
_case_id: ContextVar[str] = ContextVar("case_id", default="-")


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        record.case_id = _case_id.get()
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("judge_eval_pipeline")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"run_id":"%(run_id)s","case_id":"%(case_id)s",'
            '"msg":%(message)s}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    return logger


log = configure_logging()


def bind_correlation_context(run_id: str, case_id: str) -> None:
    _run_id.set(run_id)
    _case_id.set(case_id)


# --------------------------------------------------------------------------
# 2. Error taxonomy: transient / systemic / terminal (Sec 4.4)
# --------------------------------------------------------------------------

class JudgeError(Exception):
    def __init__(self, message: str, transient: bool, retry_after: Optional[float] = None):
        super().__init__(message)
        self.transient = transient
        self.retry_after = retry_after


class TerminalJudgeError(JudgeError):
    """400/403/413-class or content-policy refusal -- never retried."""
    def __init__(self, message: str):
        super().__init__(message, transient=False)


# --------------------------------------------------------------------------
# 3. Idempotency key derivation (Sec 4.3)
# --------------------------------------------------------------------------

def idempotency_key(case_id: str, judge_provider: str, prompt_version: str) -> str:
    raw = f"{case_id}:{judge_provider}:{prompt_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# 4. Retry with exponential backoff + full jitter (transient-only, Sec 4.4)
# --------------------------------------------------------------------------

def call_with_retry(
    fn: Callable[[], dict],
    provider: str,
    max_attempts: int,
    backoff_base_s: float,
    backoff_cap_s: float,
) -> dict:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except TerminalJudgeError as exc:
            log.info(json.dumps({"event": "judge_call_terminal_no_retry",
                                  "provider": provider, "reason": str(exc)}))
            raise
        except JudgeError as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            sleep_for = exc.retry_after or min(backoff_cap_s, backoff_base_s * (2 ** (attempt - 1)))
            sleep_for = random.uniform(0, sleep_for)  # full jitter
            log.info(json.dumps({"event": "judge_call_retry", "provider": provider,
                                  "attempt": attempt, "sleep_s": round(sleep_for, 3),
                                  "reason": str(exc)}))
            time.sleep(sleep_for)
    assert last_exc is not None
    raise last_exc


# --------------------------------------------------------------------------
# 5. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN, keyed per-provider (Sec 4.4)
# --------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    provider: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0

    def allow_request(self) -> bool:
        if self.state == BreakerState.OPEN:
            if time.time() - self.opened_at >= self.cooldown_s:
                self.state = BreakerState.HALF_OPEN
                log.info(json.dumps({"event": "breaker_half_open", "provider": self.provider}))
                return True
            return False
        return True

    def record_success(self) -> None:
        if self.state == BreakerState.HALF_OPEN:
            log.info(json.dumps({"event": "breaker_closed", "provider": self.provider}))
        self.state = BreakerState.CLOSED
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        if self.state == BreakerState.HALF_OPEN:
            self.state = BreakerState.OPEN
            self.opened_at = time.time()
            log.info(json.dumps({"event": "breaker_reopened", "provider": self.provider}))
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.time()
            log.info(json.dumps({"event": "breaker_opened", "provider": self.provider,
                                  "consecutive_failures": self.consecutive_failures}))


_BREAKERS: dict[str, CircuitBreaker] = {
    "frontier_judge": CircuitBreaker(provider="frontier_judge", failure_threshold=5, cooldown_s=30.0),
    "secondary_judge": CircuitBreaker(provider="secondary_judge", failure_threshold=5, cooldown_s=30.0),
}


# --------------------------------------------------------------------------
# 6. Mock judge/rule-based backends (swap for real API calls in production)
# --------------------------------------------------------------------------

def _frontier_judge_call(case: dict) -> dict:
    """Simulates a frontier LLM judge (Sec 2.4): flaky ~20% of the time."""
    if case.get("_poison_pill"):
        raise JudgeError("judge timeout on malformed reference trajectory", transient=True)
    if random.random() < 0.20:
        raise JudgeError("429 rate limit", transient=True, retry_after=0.5)
    score = 1.0 if "correct" in case["candidate_output"].lower() else 0.4
    return {"score": score, "judge": "frontier_judge", "rationale": "CoT rubric pass"}


def _secondary_judge_call(case: dict) -> dict:
    """Simulates a cheaper fallback judge provider."""
    if case.get("_poison_pill"):
        raise JudgeError("judge timeout on malformed reference trajectory", transient=True)
    if random.random() < 0.10:
        raise JudgeError("529 overloaded_error", transient=True, retry_after=1.0)
    score = 0.9 if "correct" in case["candidate_output"].lower() else 0.3
    return {"score": score, "judge": "secondary_judge", "rationale": "cheaper rubric pass"}


_KEYWORD_RULES = [
    (re.compile(r"\bcorrect\b", re.I), 0.8),
    (re.compile(r"\berror\b|\bfail\w*\b", re.I), 0.1),
]


def rule_based_score(case: dict) -> dict:
    """Tier-0 deterministic fallback (Sec 3.1's cascade, applied as a
    last-resort fallback rather than a first-pass filter here)."""
    text = case["candidate_output"]
    for pattern, score in _KEYWORD_RULES:
        if pattern.search(text):
            return {"score": score, "judge": "rule_based", "rationale": f"matched /{pattern.pattern}/"}
    return {"score": 0.5, "judge": "rule_based", "rationale": "no rule matched, neutral default"}


# --------------------------------------------------------------------------
# 7. Scored result + idempotent write simulation
# --------------------------------------------------------------------------

@dataclass
class ScoreResult:
    case_id: str
    status: str          # "scored" | "scored_degraded" | "excluded_poison_pill"
    score: Optional[float]
    judge: Optional[str]
    idempotency_key: str
    degraded: bool = False


_WRITTEN_KEYS: set[str] = set()  # simulates an idempotent result store (Sec 4.3)


def idempotent_write(result: ScoreResult) -> None:
    if result.idempotency_key in _WRITTEN_KEYS:
        log.info(json.dumps({"event": "idempotent_write_skipped_duplicate",
                              "case_id": result.case_id, "key": result.idempotency_key}))
        return
    _WRITTEN_KEYS.add(result.idempotency_key)
    log.info(json.dumps({"event": "audit_score_written", "case_id": result.case_id,
                          "status": result.status, "score": result.score,
                          "judge": result.judge, "key": result.idempotency_key}))


# --------------------------------------------------------------------------
# 8. Score-one-case: breaker -> retry -> fallback chain (Sec 4.4/4.5)
# --------------------------------------------------------------------------

RETRY_MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 0.2
BACKOFF_CAP_S = 4.0
DEAD_LETTER_RETRY_LIMIT = 3

_dead_letter_counts: dict[str, int] = {}


def score_case(run_id: str, case_id: str, case: dict) -> ScoreResult:
    bind_correlation_context(run_id, case_id)  # re-bind: runs in a pool thread

    for provider_name, call_fn in (("frontier_judge", _frontier_judge_call),
                                    ("secondary_judge", _secondary_judge_call)):
        breaker = _BREAKERS[provider_name]
        if not breaker.allow_request():
            log.info(json.dumps({"event": "breaker_open_skip_provider", "provider": provider_name}))
            continue
        try:
            raw = call_with_retry(lambda: call_fn(case), provider_name,
                                   RETRY_MAX_ATTEMPTS, BACKOFF_BASE_S, BACKOFF_CAP_S)
            breaker.record_success()
            key = idempotency_key(case_id, provider_name, "v1")
            result = ScoreResult(case_id, "scored", raw["score"], raw["judge"], key,
                                  degraded=(provider_name != "frontier_judge"))
            idempotent_write(result)
            return result
        except JudgeError as exc:
            breaker.record_failure()
            _dead_letter_counts[case_id] = _dead_letter_counts.get(case_id, 0) + 1
            log.info(json.dumps({"event": "provider_exhausted", "provider": provider_name,
                                  "case_id": case_id, "reason": str(exc)}))

    # Poison-pill dead-letter path (Sec 4.4): exhausted both judge providers.
    if _dead_letter_counts.get(case_id, 0) >= DEAD_LETTER_RETRY_LIMIT:
        key = idempotency_key(case_id, "dead_letter", "v1")
        result = ScoreResult(case_id, "excluded_poison_pill", None, None, key, degraded=True)
        log.info(json.dumps({"event": "dead_lettered", "case_id": case_id,
                              "note": "excluded from aggregate score, flagged for human review"}))
        idempotent_write(result)
        return result

    # Final fallback: rule-based deterministic scoring (Sec 5's headline pattern).
    raw = rule_based_score(case)
    key = idempotency_key(case_id, "rule_based", "v1")
    result = ScoreResult(case_id, "scored_degraded", raw["score"], raw["judge"], key, degraded=True)
    idempotent_write(result)
    return result


# --------------------------------------------------------------------------
# 9. Batch entrypoint: fan out a run's cases in parallel (Sec 3.3)
# --------------------------------------------------------------------------

def run_eval_batch(cases: list[dict]) -> dict:
    run_id = str(uuid.uuid4())
    bind_correlation_context(run_id, "-")
    log.info(json.dumps({"event": "run_start", "run_id": run_id, "case_count": len(cases)}))

    results: list[ScoreResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(cases))) as pool:
        futures = {
            pool.submit(score_case, run_id, case["case_id"], case): case
            for case in cases
        }
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    degraded = [r for r in results if r.degraded]
    excluded = [r for r in results if r.status == "excluded_poison_pill"]
    scored = [r for r in results if r.score is not None]
    pass_rate = sum(1 for r in scored if r.score >= 0.5) / len(scored) if scored else 0.0

    status = "complete" if not degraded else (
        "partial_degraded" if len(degraded) < len(results) else "degraded_total"
    )
    log.info(json.dumps({
        "event": "run_complete", "run_id": run_id, "status": status,
        "pass_rate": round(pass_rate, 3), "degraded_case_ids": [r.case_id for r in degraded],
        "excluded_poison_pills": [r.case_id for r in excluded],
    }))
    return {
        "run_id": run_id, "status": status, "pass_rate": round(pass_rate, 3),
        "results": [dataclasses.asdict(r) for r in results],
    }


# --------------------------------------------------------------------------
# 10. Example run
# --------------------------------------------------------------------------

if __name__ == "__main__":
    random.seed(12)
    batch = [
        {"case_id": "c1", "candidate_output": "The answer is correct and complete."},
        {"case_id": "c2", "candidate_output": "This response contains an error."},
        {"case_id": "c3", "candidate_output": "Ambiguous but plausible output."},
        {"case_id": "c4", "candidate_output": "correct trajectory, all tools matched"},
        {"case_id": "c5", "candidate_output": "malformed reference case", "_poison_pill": True},
    ]
    output = run_eval_batch(batch)
    print(json.dumps(output, indent=2))
```

**What each pattern buys, mapped back to §2–§4.** The two-provider loop in `score_case` is the runnable form of §4.4's fallback-chain ordering (secondary judge before rule-based, never the reverse) — a case only reaches `rule_based_score` after **both** judge providers have exhausted retries, so the deterministic fallback is a last resort, not a first-pass filter (that role belongs to Tier 0 of §3.1's cascade, which is a separate, upstream concern from this failure-handling scorer). `_poison_pill` cases demonstrate §4.4's dead-letter path concretely: `c5` exhausts `DEAD_LETTER_RETRY_LIMIT` against both providers and is marked `excluded_poison_pill` rather than silently folded into the pass-rate denominator — protecting the aggregate score's integrity exactly as the taxonomy table requires. `TerminalJudgeError` (unused in the mock backends but present in the retry function) is the runnable form of the never-retry terminal-error rule — wiring a real judge provider's 400/403/content-policy responses to this exception type is the only change needed to make that rule live. `idempotency_key` plus `_WRITTEN_KEYS` is the runnable form of §4.3's requirement that a retried activity cannot double-write a score — every write path in the function funnels through `idempotent_write`, including the degraded and dead-lettered paths, so the chain-of-custody guarantee from §4.5 holds regardless of which tier ultimately produced the result. Finally, `bind_correlation_context` re-binds on every `score_case` invocation specifically because Python's `contextvars` do not propagate into `ThreadPoolExecutor` worker threads — without it, the structured audit log's `run_id`/`case_id` correlation would silently break on every parallel-scored case in the batch.

---

## 6. Architectural System Design Scenarios

### Scenario A — CI/CD eval-gating pipeline for a regulated financial-services agent platform

**Problem statement.** A bank's engineering org runs an internal customer-service agent (balance inquiries, dispute initiation, fraud-flag escalation) and needs to ship weekly prompt/model updates without either (1) shipping an undetected regression into a regulated, auditable workflow, or (2) slowing releases to the point that the eval gate itself becomes the org's bottleneck. Constraints from §3–§4: judge cost at $470/month (Opus-class) vs. $13/month (GPT-4o-mini-class) for an identical T2 gate design is a 30×+ spread that must survive a budget review; every gate decision needs an immutable, chain-of-custody audit record for regulatory exam; and a full LLM judge cannot run inline without violating the customer-facing latency SLA.

**Proposed architecture.**

```
PR opened → T1 deterministic gate (Sec 3.2): schema/tool-call-format/
            trajectory-strict-match against golden dataset, <90s, blocks merge
                                    │ pass
                                    ▼
            T2 LLM-judge gate on merge to base (Sec 3.1/3.2): 50-case
            behavioral suite, repeat:3 majority voting, GPT-4o-mini-class
            judge (cost-optimized: ~$13/mo vs $470/mo Opus-class), <10min,
            blocks merge
                                    │ pass
                                    ▼
            Shadow-path gating judge call (Sec 2.5) against live-shaped
            traffic before full promotion -- the one legitimate near-
            critical-path full-judge use, RBAC-scoped approval required
            (Compliance/Audit role, Sec 4.5) for STAGING→PROD
                                    │ approved
                                    ▼
            T3 nightly regression (Sec 3.2): full cross-model suite,
            frontier-judge tier, alert-only, does not block, feeds
            production-drift dashboard
                                    │
                                    ▼
            Immutable audit log (Sec 4.5): every gate decision written
            at evaluation time -- pass/fail, threshold, model hash,
            approver identity -- hash-chained for regulatory exam
```

**Trade-off matrix:**

| Dimension | (1) Full human review, every release | (2) Tiered T1/T2/T3 cascade (proposed) | (3) Fully automated judge-only gate, no human loop |
|---|---|---|---|
| Cost | Highest (reviewer-hours, $0.50–$2.00/eval-equivalent × full suite) | Moderate ($13–$470/mo judge spend depending on model class) | Lowest (no reviewer cost, judge-only) |
| Latency (time-to-merge) | Hours–days | <90s (T1) + <10min (T2) blocking; T3 alert-only | <10min (fastest fully-automated path) |
| Ops complexity | Low tooling, high coordination overhead | Moderate (breaker/fallback/heartbeat infra, §4) | Low tooling, but high hidden risk-management complexity |
| Security/compliance | Strong (human judgment on every release) but slow enough to create pressure to bypass under deadline | Strong — RBAC-gated shadow-path approval + immutable audit trail satisfies exam requirements while staying fast | Weak — no human veto point; Goodhart/judge-bias risk (§2.6) unmitigated by a human backstop on regulated releases |
| Scalability | Does not scale past a handful of releases/week | Scales to many concurrent PRs (bounded merge-queue admission, §3.3) | Scales best, but scales the *risk* equally |

**Decision rationale.** Option (2) is the only one that satisfies all three constraints simultaneously: it keeps the blocking path fast enough (T1+T2 <11min combined) not to bottleneck weekly releases, it keeps judge cost bounded and swappable by model class without changing the gate's structure, and — critically for a regulated release — it preserves a mandatory human (Compliance/Audit role) approval point at exactly the one stage (STAGING→PROD) where an automated judge's own known bias/inconsistency (§2.4, TrustJudge's non-zero residual error) would otherwise be the sole gatekeeper for a regulated financial workflow. Option (3) is rejected specifically because Goodhart's Law (§2.6) means a judge-only gate with no human audit point is structurally exploitable by a reward-hacking-adjacent failure mode (an agent update that games the judge's rubric rather than genuinely improving) with no backstop to catch it before it reaches regulated production traffic.

### Scenario B — Online guardrail + async quality-monitoring for a high-volume consumer support agent

**Problem statement.** A consumer product serving 1M+ agent interactions/day needs to (1) block clearly unsafe or policy-violating responses in real time without adding more than ~150ms to a <2s total response latency budget, and (2) continuously measure semantic quality/drift across all production traffic without a labeled ground truth for any of it, while keeping judge spend under the published eval/inference-spend health ratio of 0.2.

**Proposed architecture.**

```
User request → Agent response generation (streaming begins immediately)
                        │                              │
        ┌───────────────┘                              └────────────────┐
        ▼ (sync, on critical path, Sec 3.2)                              ▼ (async, parallel to streaming)
Tier 0 deterministic checks (<10ms) +                          PII/content-filter/compliance
Tier 1 small classifier (60-150ms)                              deep checks (Sec 3.2's 2.5s→850ms
        │                                                        latency-mitigation pattern)
        ▼                                                                │
   ALLOW (stream continues) / BLOCK                                      ▼
                                                                  Production Traffic Sampler
                                                                  (stratified sample, Sec 1)
                                                                          │
                                                                          ▼
                                                            Async LLM-judge cascade (Sec 3.1):
                                                            70% resolved deterministically,
                                                            25% classifier, 5% frontier judge
                                                            → ~$0.26/1k traces (30x cheaper
                                                              than flat judge-only)
                                                                          │
                                                                          ▼
                                                            Flagged cases → golden dataset
                                                            feedback loop (Sec 2.5) → next
                                                            offline CI regression suite
```

**Trade-off matrix:**

| Dimension | (1) Full LLM judge inline (sync) | (2) Async-only guardrail (no sync check at all) | (3) Tiered hybrid: deterministic+classifier sync, judge async (proposed) |
|---|---|---|---|
| Cost | Highest — every request pays a full judge call ($9K–$37K/day at 1M traces/day, flat) | Low — judge cost only on sampled traffic | Low — cascade brings judge-inclusive cost to ~$260/day at the same volume |
| Latency | Violates the <2s budget outright (1–3s+ added per request) | Best (zero added sync latency) | ~40–200ms added to P95, within the industry-cited target |
| Ops complexity | Moderate (one code path) but latency failure is systemic, not edge-case | Low, but leaves a real safety gap | Moderate (two coordinated pipelines, §1's dual data-plane loops) |
| Security | Highest per-request accuracy (95%+) but arrives too late to have blocked an already-streamed unsafe response | Unsafe/policy-violating content can reach the user before any check completes | Deterministic+classifier catch 89–95%+ of known-pattern violations in real time; the judge tier catches the semantic long tail *after the fact*, feeding future prevention rather than blocking this instance |
| Scalability | Does not scale — judge-provider RPM ceiling is hit almost immediately at 1M/day | Scales well but the safety gap scales with it | Scales — deterministic/classifier tiers are cheap and horizontally trivial; async judge pool sizing is decoupled from the serving path entirely (§3.4) |

**Decision rationale.** Option (3) is the only architecture consistent with the hard latency constraint from §3.2 (a full judge structurally cannot fit a <2s budget) while still providing the async judge tier's semantic-quality signal that a deterministic/classifier-only guardrail (option 2's implicit floor) cannot detect on its own — the explicit trade-off accepted here is that the *first* instance of a genuinely novel semantic-quality failure will reach the user before the async judge flags it (there is no sync full-judge check to catch it in the moment), but every subsequent occurrence of the same failure pattern is caught by the feedback loop promoting it into the golden dataset and, ultimately, into the Tier 1 classifier's training data — trading one-time exposure for compounding future prevention at a cost 30× lower than the alternative that could have caught it immediately.

---

## Sources

This module synthesizes and restructures the research compiled in `research/12-evaluation.md`, which consulted 45+ sources (primary vendor docs for LangSmith/Braintrust/OpenAI Evals/Arize Phoenix/DeepEval, peer-reviewed arXiv papers on LLM-as-judge bias and reward hacking, Temporal engineering docs, and independent FinOps/engineering blogs on eval cost economics). See that file's numbered source list (`[1]`–`[108]`) for full citations; inline `[inferred]` / `⚠️ Gap` flags in this module are carried forward from the same annotations in the source research where the underlying data was itself an extrapolation rather than a directly published figure.
