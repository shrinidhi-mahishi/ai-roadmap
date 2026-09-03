# Agent Feedback Loops

## Why It Matters
Feedback loops are what turn a one-shot assistant into an agent that can recover from mistakes. But the strongest interview framing is that loops help only when they add new evidence. More self-talk without a verifier usually just adds latency and token burn.

That is why production-grade loop design is less about "make the model reflect" and more about role separation, loop caps, and oracle quality. Good systems separate planner, executor, critic, verifier, and replanner instead of asking one model to improvise all five roles inside an unbounded loop.

## Mental Model
Use this topology:

- Planner decides what should happen next.
- Executor does the work.
- Critic explains what may be wrong.
- Verifier checks whether it is actually wrong.
- Replanner decides whether to try again, route differently, or stop.

The key distinction is between evidence-bearing feedback and speculation-bearing feedback.

- Evidence-bearing feedback comes from tests, compilers, tool results, database state, or structured evaluators.
- Speculation-bearing feedback comes from the model criticizing itself without new information.

Reasoning models such as o1, o3, or DeepSeek-R1 internalize some of this loop, but they do not remove the need for explicit verifiers, visible plans, or control-flow integrity around tools.

## Architecture / Flow
```text
goal -> planner -> executor/tools -> verifier
                         |             |
                         v             v
                       critic <- evidence
                         |
                         v
                    replan or stop
```

A production loop is usually bounded:

- cap replans
- cap repeated identical actions
- cap total tokens and wall-clock time
- escalate when verifier confidence is weak or side effects are high impact

That boundedness is a first-class design decision, not a nice-to-have.

## Key Concepts
- Role separation matters:
  - a planner should not mutate the world directly
  - a critic should not be able to call high-impact tools
  - a verifier should outrank the critic when they disagree

- ReAct versus plan-and-execute versus DAG planning:
  - ReAct is flexible but serial and loop-prone
  - plan-and-execute amortizes planning across multiple steps
  - DAG planning, as in LLMCompiler-style systems, parallelizes independent work and often beats ReAct on cost and latency

- Reflexion:
  - verbal feedback carried from one attempt to the next
  - strongest when the feedback is anchored in a real oracle such as tests

- Self-Refine:
  - same model generates, critiques, and revises
  - useful for writing or low-risk refinement
  - weaker when correctness requires external checks

- CRITIC:
  - criticism backed by tools such as calculators, code runners, or search
  - the important lesson is that critique without tools can be worse than no critique

- PRM versus ORM:
  - process reward models supervise steps
  - outcome reward models supervise only the final answer
  - PRMs can exploit test-time compute better when step labels are trustworthy

- Internalized reasoning:
  - frontier reasoning models already do hidden planning and backtracking
  - that reduces the need for visible loop scaffolding on simple tasks
  - it does not remove the need for an external verifier on consequential tasks

- Feedback memory:
  - episodic lessons can help future attempts
  - but reflections are untrusted data if they came from poisoned tools or web content

## Metrics and Formulas to Memorize
- Reflexion HumanEval Python:
  - `91.0%` pass@1
  - GPT-4 baseline in the paper: `80.1%`

- Reflexion ablation warning:
  - on hard HumanEval-Rust without tests, feedback hurt: `52%` versus `60%` baseline

- CRITIC without tools:
  - the paper's ablations show only marginal gains and occasional regressions once tool feedback is removed

- PRM versus ORM on MATH:
  - `78.2%` versus `72.4%` at best-of-1860

- LLMCompiler versus ReAct:
  - up to `3.7x` faster
  - up to `6.7x` cheaper

- Tree of Thoughts Game of 24 anchor:
  - GPT-4 CoT `4%`
  - ToT `74%`

- DeepSeek-R1-Zero AIME 2024:
  - `15.6% -> 77.9%`

- DeepSeek-R1 versus o1-1217:
  - `79.8%` versus `79.2%`

- PlanGuard loop-control number worth remembering:
  - ASR `72.8% -> 0%`
  - FPR `1.49%`

- Practical loop caps from field practice:
  - `max_replans = 2-3`
  - `same_action_k = 2`

These numbers are useful because they support the deeper claim: loops are powerful, but only when guided by oracles, budgets, and explicit control.

## Trade-offs and Failure Modes
- Reflection without an oracle:
  the model rationalizes errors instead of correcting them.

- Infinite critique or replan loops:
  token burn rises while progress stalls.

- Same-model blind spots:
  generator and critic often share the same failure pattern.

- Reflection poisoning:
  hostile tool or web output becomes a durable "lesson" for future attempts.

- Over-decomposition:
  splitting work too aggressively creates orchestration overhead larger than the quality gain.

- Hidden reasoning opacity:
  internal reasoning may help performance but hurts auditability unless plans and actions are exposed elsewhere.

- Verifier disagreement:
  if tests fail and the critic says "looks good," trust the stronger oracle.

- Tool-unsafe replanning:
  a replan step that expands allowed actions can become a security problem, not just a quality problem.

## Interview Q&A
**Q: When should you add a critic to an agent?**  
A: When you have a high-signal evaluator or tool-backed checker. Without that, the critic often just adds noise.

**Q: ReAct or plan-and-execute?**  
A: ReAct for simple adaptive loops. Plan-and-execute when the task has enough structure that amortizing planning is worth it.

**Q: Why did LLMCompiler matter?**  
A: It showed that explicit dependency planning and parallel execution can materially reduce latency and cost versus serial ReAct.

**Q: What is the biggest lesson from Reflexion?**  
A: Reflection works when it is grounded in real evidence such as tests. It can hurt when no oracle exists.

**Q: PRM or ORM?**  
A: PRMs can use test-time compute better because they supervise steps, but only when step labels are meaningful and hard to game.

**Q: Do reasoning models eliminate explicit loops?**  
A: No. They internalize some planning, but explicit verification, tool control, and stop conditions still matter.

**Q: How do you keep loops safe?**  
A: Separate roles, cap retries, verify with stronger oracles, and prevent replanners from expanding tool authority without approval.

**Q: What is the fastest way to sound senior on this topic?**  
A: Say that loops should add new evidence, not just more tokens.

## Sources
- Local anchors:
  - `ai-roadmap/final/08-planning-reasoning.md`
  - `ai-roadmap/final/17-advanced-autonomous-agents.md`
  - `ai-roadmap/final/12-evaluation.md`
  - `ai-roadmap/final/07-memory.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [ReAct Paper](https://arxiv.org/abs/2210.03629)
  - [Reflexion Paper](https://arxiv.org/abs/2303.11366)
  - [LLMCompiler Paper](https://arxiv.org/abs/2312.04511)
  - [Self-Refine Paper](https://arxiv.org/abs/2303.17651)
  - [CRITIC Paper](https://arxiv.org/abs/2305.11738)
  - [Tree of Thoughts Paper](https://arxiv.org/abs/2305.10601)
  - [Let's Verify Step by Step](https://arxiv.org/abs/2305.20050)
  - [DeepSeek-R1 Paper](https://arxiv.org/abs/2501.12948)
  - [PlanGuard Paper](https://arxiv.org/abs/2604.10134)
