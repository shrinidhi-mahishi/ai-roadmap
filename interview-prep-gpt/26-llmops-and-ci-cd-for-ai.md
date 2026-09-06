# LLMOps and CI/CD for AI

## Why It Matters
LLMOps is release engineering for probabilistic systems. The deployable unit is not just a model checkpoint; it is model snapshot, prompt version, tool/schema set, dataset and evaluator versions, runtime config, and trace lineage.

The interview-ready insight is that offline evals before merge and online evals after deploy solve different problems. Offline gates catch regressions before release. Online scoring catches drift, distribution shift, and failure modes your curated dataset missed.

## Mental Model
Think in terms of an artifact graph, not one artifact:
- model
- prompt
- tool schemas
- dataset
- evaluator
- code commit
- runtime/image/config

Two rules follow:
- versions should be immutable
- promotions should happen through mutable aliases, not code edits

## Architecture / Flow
```text
change in code/prompt/model/tool/data
  -> register immutable artifacts
  -> run offline evals in CI on a fixed dataset
  -> compare candidate vs baseline on the same slices
  -> promote alias or prompt version
  -> canary low traffic
  -> run online traces + judges + human review
  -> feed failures back into the dataset
```

A good system makes every scored run traceable back to exact artifacts and config.

## Key Concepts
- Artifact graph:
  - the behavior-changing surface includes model, prompt, tools, evaluator, data, and runtime settings
  - if any of these changes, your results may no longer be comparable
- Experiment tracking and lineage:
  - every evaluation run should point back to the exact artifacts that produced it
  - traces are useful only if you can reconnect them to versions and rollout state
- Model registry patterns:
  - immutable versions for reproducibility
  - mutable aliases such as `@champion` for promotion and rollback
  - environment boundaries like dev, stage, and prod should be explicit
- Prompt registry and versioning:
  - prompts need the same treatment as models
  - tags, aliases, owners, review history, and rollback paths are not optional in serious systems
- Offline evals in CI:
  - use curated regression sets and clear thresholds
  - compare candidate and baseline on the same dataset and slices
  - repeat trials where stochasticity matters
- Online evals after deploy:
  - sample live traces
  - score asynchronously, not on the user latency path
  - watch drift, anomaly rate, and cost changes
- A/B and champion-challenger:
  - compare prompt/model/tool variants against the same task distribution
  - do not compare mismatched baselines or stale datasets
- Release gates:
  - success, safety, latency, cost-per-success, and judge coverage should all be first-class
  - an unscored run is not a silent pass
- Human review queues:
  - failing or ambiguous traces need annotation or manual review
  - the value appears only when reviewed traces are promoted back into the regression set
- Governance:
  - traces and datasets are another PII surface
  - registries need RBAC, audit trails, and explicit promotion authority

## Metrics and Formulas to Memorize
- `pass@k = 1 - C(n-c, k) / C(n, k)` for unbiased best-of-`k` capability measurement
- `pass^k` is the reliability metric: all `k` trials succeed
- `cost_per_success = total_cost / compliant_successes`
- `99.9%` SLO over `3,000,000` requests leaves an error budget of `3,000`
- MLflow model stages are deprecated starting in `2.9`; use aliases such as `@champion` instead
- LangSmith pricing anchors:
  - base traces: `$0.0005` each, or about `~$0.50 / 1k`
  - extended traces: `$0.005` each, or about `~$5 / 1k`
- OpenAI eval-driven design guidance:
  - `>$10/day/engineer` on evals is normal
  - `>$100/day/engineer` often becomes hard to sustain
- Challenger rollout rule of thumb: start around `1-5%` traffic before full promotion
- Judge coverage is a real NFR:
  - `coverage = scored_runs / eligible_runs`
  - if coverage drops, the dashboard can look green while the scoring system is down

## Trade-offs and Failure Modes
- Prompts, tools, or datasets change with no immutable versioning, so results are not reproducible
- Teams run only offline evals and discover drift from customers instead of monitors
- A/B tests compare mismatched datasets or inconsistent baselines and produce false conclusions
- Online judges sit on the user latency path and turn observability into a tax
- Composite scores hide safety regressions behind small helpfulness improvements
- Promotion happens manually in docs or chat instead of via auditable alias state
- Traces and datasets quietly become a second PII surface with weaker access control
- Human review queues exist, but their outputs never make it back into the regression suite

## Interview Q&A
**Q: What is the deployable unit in an AI system?**  
A: Model plus prompt plus tools plus dataset plus evaluator plus runtime config. Shipping only a new model version is an incomplete answer.

**Q: Why do prompts need a registry too?**  
A: Because prompt changes alter behavior just like model changes do. You need versioning, owners, aliases, rollout control, and rollback.

**Q: Offline evals or online evals?**  
A: Both. Offline evals are the pre-merge regression gate. Online evals catch real-world drift and new failure modes after deploy.

**Q: What is a clean rollback story?**  
A: Flip an alias or prompt version back to the last known-good artifact. Rollback should not require editing application code under pressure.

**Q: What does a serious release gate include?**  
A: Policy-compliant success, safety thresholds, latency bounds, cost-per-success, and judge coverage, not just one quality score.

**Q: Why are MLflow aliases more important now than stages?**  
A: Because stages are deprecated. Aliases like `@champion` are the clean way to separate immutable versions from mutable deployment targets.

**Q: How do you use production failures constructively?**  
A: Sample and score traces, queue the ambiguous ones for review, then add the confirmed failures back into the dataset so CI can catch them next time.

**Q: Biggest anti-pattern in LLMOps?**  
A: Treating models as the only artifact that matters. In practice, prompts, tool schemas, evaluators, and datasets break reproducibility just as easily.

## Sources
- Local anchors:
  - `ai-roadmap/interview-prep-gpt/04-evals.md`
  - `ai-roadmap/interview-prep-gpt/05-observability.md`
  - `ai-roadmap/final/ai-concepts/12-evaluation.md`
  - `ai-roadmap/final/ai-concepts/16-production.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [MLflow Model Registry overview](https://mlflow.org/docs/latest/ml/model-registry/)
  - [MLflow registry workflows](https://mlflow.org/docs/latest/ml/model-registry/workflow/)
  - [W&B Weave prompt versioning](https://docs.wandb.ai/weave/guides/core-types/prompts-version)
  - [W&B Weave evaluations overview](https://docs.wandb.ai/weave/guides/core-types/evaluations)
  - [LangSmith evaluation docs](https://docs.langchain.com/langsmith/evaluation)
  - [LangSmith CI/CD pipeline example](https://docs.langchain.com/langsmith/cicd-pipeline-example)
  - [Braintrust CI docs](https://www.braintrust.dev/docs/evaluate/run-in-ci)
  - [Braintrust A/B testing prompts](https://www.braintrust.dev/articles/ab-testing-llm-prompts)
  - [OpenAI eval-driven system design](https://developers.openai.com/cookbook/examples/partners/eval_driven_system_design/receipt_inspection)
