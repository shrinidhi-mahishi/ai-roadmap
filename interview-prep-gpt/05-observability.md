# Observability

## Why It Matters
Agent observability is not just "logging prompts." Production systems need enough evidence to answer three different questions:

- What did the agent do?
- What did it cost and how long did it take?
- What evidence justified the action?

That is why the best mental model is not one dashboard. It is a layered system with metrics, traces, and audit logs serving different purposes. In interviews, this stands out because many people collapse all of observability into prompt capture, which is both too expensive and too weak for governance.

## Mental Model
Use three observability surfaces:

- Trajectory: steps, branches, retries, tool calls, handoffs, checkpoints
- Resource: tokens, latency, cost, cache behavior
- Evidence: retrieved docs, tool outputs, citations, policy decisions

Then map them to three storage layers:

- Metrics for always-on health and cost
- Sampled traces for debugging and performance analysis
- Unsampled immutable audit logs for consequential actions

The key interview insight is that an agent trace is not the same as a classic APM trace. Agent traces are much wider, much larger, and often much more sensitive because they can contain prompts, tool arguments, screenshots, and user data.

## Architecture / Flow
```text
app/sdk -> OTel instrumentation -> edge collector -> tail sampler
        -> trace backend
        -> metrics backend
        -> content blobs and audit sinks
```

A stable production design usually looks like this:

1. Instrument once with W3C Trace Context and OTel-style metadata.
2. Fan out from OTLP to one or more backends.
3. Keep metrics at 100% traffic.
4. Tail-sample traces so you keep the weird failures.
5. Store raw content separately from metadata when privacy or volume matters.

That "instrument once, export many" rule is important. Dual-instrumenting the same code path with multiple vendor SDKs usually creates duplicate spans, inconsistent correlation, and avoidable complexity.

## Key Concepts
- Trace, thread, trajectory, checkpoint:
  - a trace is one execution tree
  - a thread groups related executions across turns
  - a trajectory is the ordered path through messages and actions
  - a checkpoint is resumable state, not just telemetry

- W3C Trace Context:
  - `traceparent` is the correlation backbone across services
  - without it, agent spans and tool spans split into unrelated traces

- OTel GenAI versus OpenInference:
  - OTel GenAI provides emerging shared attribute conventions
  - OpenInference adds AI-oriented span kinds and works well with Phoenix
  - they are complementary, not mutually exclusive

- Tail sampling beats head sampling for agents:
  - head sampling decides before the interesting failure happens
  - tail sampling can keep errors, long latency, policy denies, and HITL traces

- Metrics versus traces versus audit:
  - metrics are cheap and always on
  - traces are richer and sampled
  - audit logs are small but must be durable and unsampled for important actions

- Content capture modes:
  - none
  - small redacted content on spans
  - external blob plus pointer
  - production systems often need the third option

- PII and RBAC:
  - trace metadata and raw prompt content should not have the same access policy
  - the person who debugs latency is not automatically the person who should view customer content

- Multi-agent and MCP propagation:
  - correlation has to continue across queues, workers, and MCP calls
  - if tool or MCP spans are missing, the trace tree lies about where time went

- SLO design:
  - agents need TTFT, end-to-end latency, availability, correctness, and cost-per-success metrics
  - public composed p99 numbers are limited, so tail budgets should be measured locally

## Metrics and Formulas to Memorize
- LLM traces are often about `10-100x` larger than classic APM spans once prompt/response content is attached

- LangSmith pricing anchor from local material:
  - about `~$0.50 / 1k` base traces
  - about `~$5.00 / 1k` extended traces

- Datadog Agent Observability anchor:
  - `40k` LLM spans per month on the free tier
  - overage about `$3.50 / 10k` LLM spans on annual pricing

- Honeycomb anchor from local material:
  - about `$3.00 / million events`

- OTel tail-sampling defaults commonly cited:
  - `decision_wait = 30s`
  - `num_traces = 50,000`

- Phoenix operational anchors:
  - queue default `20,000` spans
  - common gRPC payload ceiling `4 MB`

- Grafana Cloud local anchor:
  - metrics slack reference `30s`

- Burn-rate rule worth memorizing for a 30-day SLO:
  - page at `14.4x` burn on `1h` and `5m`

- Reality check:
  - public end-to-end p99 numbers across model + MCP + tools + storage are limited
  - treat most composed tail estimates as local engineering work, not vendor guarantees

## Trade-offs and Failure Modes
- Head sampling away the failures:
  the exact traces you need are the ones the SDK already dropped.

- Broken trace trees:
  missing propagation across MCP, queues, or tool workers hides real bottlenecks.

- Cardinality explosion:
  metrics labels like raw `user_id`, session IDs, or prompt hashes can destroy monitoring systems.

- PII leakage:
  prompts, tool args, screenshots, or retrieved docs can turn your tracing stack into a second data breach surface.

- Replay confusion:
  replay can help debugging, but it is not immutable audit truth because the model or tool may behave differently on rerun.

- Final-answer-only dashboards:
  they miss retry storms, dead-end tool loops, and expensive thrashing that still ends in a good-looking answer.

- Retention surprises:
  auto-upgraded traces and content-on-by-default policies can create large bills and large privacy footprints.

## Interview Q&A
**Q: What are the three observability surfaces for agents?**  
A: Trajectory, resource usage, and evidence or provenance. You need all three to debug and govern real systems.

**Q: Why is head sampling usually wrong for agents?**  
A: Because the interesting information, like tool failure or policy denial, is usually only known at the tail of the trace.

**Q: What is the difference between a trace and a trajectory?**  
A: A trace is the execution tree. A trajectory is the ordered path through the interaction or workflow.

**Q: How would you design a production observability stack?**  
A: Metrics at 100%, sampled redacted traces, and a separate immutable action audit keyed by trace ID.

**Q: OTel GenAI or OpenInference?**  
A: Instrument once with OTel-compatible metadata and export where needed. OpenInference is a useful semantic layer, not a competing transport.

**Q: How do you handle PII in traces?**  
A: Default content off, redact before write, separate metadata from blobs, and gate access with different RBAC levels.

**Q: What should be in an audit log that is not necessarily in a trace?**  
A: Policy decisions, approval bindings, exact tool actions, and immutable evidence of side effects.

**Q: What is the most common observability anti-pattern?**  
A: Treating prompt capture as observability. It is only one expensive and risky slice of the overall system.

## Sources
- Local anchors:
  - `ai-roadmap/final/14-observability.md`
  - `ai-roadmap/final/12-evaluation.md`
  - `ai-roadmap/consolidated_study_guide.md`
  - `ai-roadmap/research_opus_4.6/research/14-observability.md`
- External:
  - [W3C Trace Context](https://www.w3.org/TR/trace-context/)
  - [OTel GenAI Spans Spec](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
  - [OpenInference Spec](https://github.com/Arize-ai/openinference/blob/main/spec/README.md)
  - [LangSmith OTEL Tracing](https://docs.langchain.com/langsmith/trace-with-opentelemetry)
  - [Phoenix: OTel + OpenInference Overview](https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/overview)
  - [Phoenix: OpenInference Semantic Conventions](https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/semantic-conventions)
