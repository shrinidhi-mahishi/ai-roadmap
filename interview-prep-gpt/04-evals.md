# Evals

## Why It Matters
Evaluation is the measurement system for the whole agent, not a leaderboard screenshot for the model. In interviews, the strongest move is to say explicitly that the harness, environment, tools, grader, retry policy, and even cache settings are part of the thing being measured.

That matters because agent systems are non-deterministic, multi-step, and often stateful. A naked "91% success rate" is almost meaningless unless you also know the benchmark split, scaffold, grading rule, and retry budget. Good teams use evals to make release decisions, catch regressions, and separate real quality improvements from harness tricks.

## Mental Model
Think of evals as a dual-oracle system:

- A hard oracle checks correctness or safety with deterministic evidence where possible.
- A soft oracle measures quality where only rubric-based judgment is available.

That is the enterprise default because hard-only evaluation can ship "correct but hostile," while soft-only evaluation can ship "pretty wrong."

Another strong framing is:

`evaluated system = model x scaffold x tools x environment x judge x sampling`

If any term changes, the score can change a lot.

## Architecture / Flow
```text
dataset -> runner -> target agent -> environment/tools
       -> traces -> deterministic graders + model graders + humans
       -> statistics -> release gate
```

Operationally, separate three layers:

1. Eval harness
   - versioned dataset
   - runner
   - environment reset logic

2. Production tracing
   - what live traffic actually did
   - used to discover failures and build future eval sets

3. Judge sidecar
   - deterministic code/state grader
   - LLM-as-judge
   - human review queue

The critical rule is that online judging must be asynchronous and off the user latency path.

## Key Concepts
- Six dimensions to evaluate:
  - task success
  - trajectory quality
  - tool accuracy
  - output quality
  - cost
  - latency

- `pass@k` versus `pass^k`:
  - `pass@k` answers "can at least one of k attempts succeed?"
  - `pass^k` answers "do all k attempts succeed?"
  - The gap between them is product risk.

- Hard gates and soft scores:
  - Use deterministic checks when you can: hidden tests, database state, schema validity, policy assertions.
  - Use model or human rubric scoring only where deterministic grading is impossible.

- Benchmark families measure different things:
  - SWE-bench measures issue resolution against tests.
  - tau-bench measures goal-state correctness in task environments.
  - GAIA tests broad tool-using reasoning.
  - BFCL focuses on function/tool correctness.
  - HealthBench is a rubric-heavy domain benchmark.
  - RAGAS focuses on RAG-specific groundedness and retrieval quality.

- Dataset design matters more than people admit:
  - include slices
  - protect private holdouts
  - watch for contamination
  - keep temporal freshness in mind

- Stateful versus stateless evals:
  - tool-call JSON validation is not enough for multi-step systems
  - sometimes only world-state change tells you if the task really succeeded

- Judge bias:
  - LLM-as-judge can over-reward verbosity, prefer its own style, and be sensitive to answer order
  - judge calibration is a real workstream, not a formality

- Statistical hygiene:
  - compare candidate and baseline on the same tasks
  - use task-level units, not step-level pseudo-replication
  - publish intervals, not only point estimates

## Metrics and Formulas to Memorize
- Unbiased `pass@k` estimator:
  - `1 - C(n-c, k) / C(n, k)`

- `pass^k`:
  - probability that all `k` independent trials succeed
  - use this as a reliability metric

- Randomness study anchor:
  - about `60,000` trajectories
  - up to `24.9` percentage-point gaps between `pass@k` and `pass^k`

- SWE-bench anchors:
  - original: `2,294` tasks
  - Verified: `500`
  - Pro: `731`
  - later reporting called Verified contaminated and Pro partly broken, so always name the split and date

- GAIA anchor:
  - `466` questions
  - humans `92%`
  - GPT-4 plus plugins `15%`

- HealthBench anchor:
  - `5,000` conversations
  - `48,562` rubric criteria
  - median `11` criteria per example

- RAGAS faithfulness:
  - local material cites about `~95%` human agreement

- Anthropic tau-airline example:
  - `pass^1 0.332 -> 0.584`
  - `pass^5 0.100 -> 0.340`

- Eval cost warning:
  - public platform pricing exists, but total `$/1k tasks` is workload-dependent because agent turns, graders, retries, and human review all compound

The interview-ready lesson is that a reliability number without trial policy and oracle design is not enough.

## Trade-offs and Failure Modes
- Benchmark contamination:
  high scores can reflect memorization of public tasks rather than real capability.

- Harness confounding:
  scaffold improvements get mistaken for model intelligence gains.

- Judge gaming:
  the system learns to please the grader instead of solving the task.

- Retry inflation:
  hidden retries make pass@1 look stronger than the user experience really is.

- Sync online grading:
  this adds latency tax and often makes the eval system itself unpopular.

- Cache or environment leakage:
  later trials benefit from hidden shared state.

- Composite score abuse:
  safety regressions get buried under cosmetic quality improvements.

- Using a model judge where a deterministic oracle exists:
  this usually reduces clarity and increases noise.

## Interview Q&A
**Q: What is the dual-oracle pattern?**  
A: Use a hard correctness or safety gate plus a softer rubric score. They answer different questions and should not be collapsed into one number.

**Q: Why does `pass^k` matter so much for agents?**  
A: Because users experience reliability, not best-case sampling. The gap between `pass@k` and `pass^k` is operational risk.

**Q: What is wrong with saying "the model scored 91%"?**  
A: It hides which split, scaffold, tools, grader, retries, and environment produced that number.

**Q: When should you trust LLM-as-judge?**  
A: For open-ended quality where deterministic grading is impossible, but only after calibrating bias and keeping it out of high-stakes hard gates.

**Q: How do you evaluate a customer-support or workflow agent?**  
A: Check end-state and policy compliance first, then layer tone or helpfulness rubrics on top.

**Q: What is the biggest statistical mistake teams make?**  
A: Treating steps as independent samples instead of evaluating at the task level.

**Q: How should online evals run in production?**  
A: Asynchronously, sampled, and off the request path.

**Q: What does a serious release gate look like?**  
A: Minimum policy-compliant success threshold, maximum safety-violation threshold, latency and cost bounds, and judge-calibration coverage.

## Sources
- Local anchors:
  - `ai-roadmap/final/12-evaluation.md`
  - `ai-roadmap/final/06-rag.md`
  - `ai-roadmap/final/14-observability.md`
  - `ai-roadmap/final/13-security-guardrails.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [HumanEval / pass@k Paper](https://arxiv.org/abs/2107.03374)
  - [SWE-bench Paper](https://arxiv.org/abs/2310.06770)
  - [tau-bench Paper](https://arxiv.org/abs/2406.12045)
  - [GAIA Paper](https://arxiv.org/abs/2311.12983)
  - [HealthBench Paper](https://arxiv.org/abs/2505.08775)
  - [RAGAS Docs](https://docs.ragas.io/)
  - [DeepEval Repo](https://github.com/confident-ai/deepeval/)
