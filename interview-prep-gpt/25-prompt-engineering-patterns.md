# Prompt Engineering Patterns

## Why It Matters
Prompt engineering is now best explained as interface design plus measurement, not prompt "tricks." Clean instruction boundaries, good few-shot examples, and structured outputs still move reliability a lot, but they only matter if you can explain the cost, latency, and failure trade-offs.

In interviews, the strongest move is to frame prompts as versioned artifacts in a larger system. If a prompt change shifts quality, cost, or safety, that is a product and release-engineering event, not just wording polish.

## Mental Model
Treat a prompt as a layered contract:
- role or policy
- instructions
- context or retrieved data
- examples
- user input
- output contract

Three rules matter most:
- stable prefix first, dynamic payload last
- examples teach decision boundary and format, not just style
- validators, retrieval, and tools carry truth beyond prompt wording

## Architecture / Flow
```text
task -> choose model + output contract
     -> compose prompt layers
     -> run model
     -> parse and validate
     -> retry, escalate, retrieve, or use tools if needed

optimization loop:
  baseline prompt -> dataset + metric -> compile/tune -> version -> CI -> deploy
```

The prompt is not complete until you know how it will be parsed, evaluated, and rolled back.

## Key Concepts
- Instruction hierarchy:
  - keep role, instructions, context, examples, and user data visibly separated
  - delimit untrusted content with XML-style tags or equivalent boundaries
- Few-shot prompting:
  - start with a small set of relevant, diverse, format-faithful examples
  - examples should cover edge cases and refusal patterns, not just happy-path style
- Zero-shot CoT vs few-shot CoT:
  - zero-shot CoT is often enough for reasoning-heavy tasks on strong models
  - few-shot CoT still helps when the task needs a specific decomposition style or answer shape
- Self-consistency:
  - sample multiple reasoning paths, then aggregate the final answer
  - best when paired with a verifier or reranker, otherwise you may just average the same bias
- Meta-prompting:
  - use conductor/expert or prompt-about-prompts patterns for hard decomposition
  - useful for complex planning, but expensive and harder to observe
- DSPy-style optimization:
  - define signatures and modules, then compile prompts against a metric
  - the important shift is from hand-tuned strings to measured, optimizable artifacts
- Structured output ladder:
  - prompted JSON: weakest guarantee
  - JSON mode: valid JSON syntax only
  - strict schema-constrained output: strongest when supported
- Semantic validation gap:
  - schema-valid output can still be wrong, unsafe, or unauthorized
  - parsing is not business-rule validation
- Caching and prompt layout:
  - keep few-shots, tool schemas, and stable instructions in the reusable prefix
  - keep user-specific and retrieval-specific content late to preserve cache hits
- When prompting is the wrong tool:
  - use retrieval for knowledge gaps
  - use tools for world interaction
  - use fine-tuning for stable behavior shifts instead of endlessly stacking examples

## Metrics and Formulas to Memorize
- Few-shot heuristic from Anthropic docs: start with `3-5` relevant, diverse examples
- CoT paper anchor: `8` CoT exemplars on `PaLM 540B` reached SOTA on GSM8K
- CoT emergence rule of thumb from Google reporting: gains become much more reliable around `~100B`-scale models
- Self-consistency gains reported in the paper:
  - `+17.9%` GSM8K
  - `+11.0%` SVAMP
  - `+12.2%` AQuA
  - `+6.4%` StrategyQA
  - `+3.9%` ARC-Challenge
- Meta-prompting paper anchor: GPT-4 plus meta-prompting beat standard prompting by `17.1%`, expert-dynamic prompting by `17.3%`, and multipersona prompting by `15.2%`
- OpenAI JSON mode guarantees valid JSON syntax, not schema adherence
- OpenAI strict structured-output subset is intentionally constrained; practical design guidance is to keep schemas shallow, roughly `<=5` nesting levels and `<=100` total properties
- Self-consistency cost scales roughly with sampled paths:
  - `total_generation_cost ~= k * one_path_cost`
  - unless a smaller verifier or reranker prunes early
- DSPy optimizer docs note you can start with about `5-10` examples, and a simple optimization run is often on the order of `$2` and `~10 minutes`, though cost scales with model and dataset size

## Trade-offs and Failure Modes
- Few-shot examples are too narrow, so the model learns style mimicry instead of the intended rule
- Chain-of-thought is forced onto simple extraction or classification tasks and only adds latency and tokens
- Self-consistency is used without a verifier, so the system samples the same mistake multiple times
- Meta-prompting quietly turns into an expensive multi-agent scaffold with poor observability
- DSPy or other auto-optimizers overfit to a weak metric or tiny dev set
- Teams confuse valid JSON with semantically correct business output
- Prompt changes are made in place with no versioning or eval history, so regressions become unexplainable
- Prompt stacking continues long after the right answer is retrieval, tool use, or fine-tuning

## Interview Q&A
**Q: What is prompt engineering in 2026 terms?**  
A: Interface design plus measurement. You are specifying instructions, boundaries, examples, and output contracts, then validating them with evals instead of chasing one-off wording tricks.

**Q: How do you choose few-shot examples?**  
A: Start with `3-5` examples that are relevant, diverse, and format-faithful. Cover edge cases and refusal behavior, not just duplicate happy-path answers.

**Q: Zero-shot CoT or few-shot CoT?**  
A: Zero-shot CoT is often enough on strong models for generic reasoning. Use few-shot CoT when the task needs a very specific decomposition pattern or answer format.

**Q: What is self-consistency really buying you?**  
A: Better accuracy on some reasoning tasks by sampling multiple paths, but at roughly `k` times the generation cost and latency unless you add a cheaper verifier.

**Q: When is meta-prompting worth it?**  
A: When the task is hard to decompose and the model benefits from a conductor/expert pattern. It is not a default because observability and cost get worse quickly.

**Q: Why does DSPy matter for interviews?**  
A: It reframes prompts as compiled artifacts. You declare signatures and modules, optimize them against a metric, and stop treating prompt tuning as manual string editing.

**Q: JSON mode vs strict structured outputs?**  
A: JSON mode guarantees valid JSON syntax. Strict structured outputs aim for schema-constrained generation. Even then, you still need semantic and authorization checks after parsing.

**Q: When should you stop prompt tuning and change the system?**  
A: When the failure is really about missing knowledge, missing tools, or a stable behavior shift. That means retrieval, tool use, workflow changes, or fine-tuning, not more examples.

## Sources
- Local anchors:
  - `ai-roadmap/final/ai-concepts/01-llm-foundations.md`
  - `ai-roadmap/final/ai-concepts/08-planning-reasoning.md`
  - `ai-roadmap/interview-prep-gpt/07-guardrails.md`
  - `ai-roadmap/interview-prep-gpt/19-prompt-caching.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [Anthropic prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices?_rsc=1ur0b)
  - [Anthropic Prompt Improver](https://claude.com/blog/prompt-improver)
  - [Chain-of-thought prompting paper](https://proceedings.neurips.cc/paper_files/paper/2022/hash/9d5609613524ecf4f15af0f7b31abca4-Abstract-Conference.html)
  - [Self-consistency paper page](https://research.google/pubs/self-consistency-improves-chain-of-thought-reasoning-in-language-models/)
  - [OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
  - [OpenAI Structured Outputs launch](https://openai.com/index/introducing-structured-outputs-in-the-api/)
  - [DSPy signatures docs](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/learn/programming/signatures.md)
  - [DSPy optimizers docs](https://github.com/stanfordnlp/dspy/blob/43bf2c59/docs/docs/learn/optimization/optimizers.md)
  - [Meta-Prompting paper](https://doi.org/10.48550/arxiv.2401.12954)
