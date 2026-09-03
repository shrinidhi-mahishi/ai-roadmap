# Context Management

## Why It Matters
Most agent failures are context failures before they are reasoning failures. The model either sees too much, too little, or the wrong slice at the wrong time. Deep Agents treats this as a systems problem: prompt assembly, hidden runtime state, compression, delegation, and long-term persistence are all part of the context design.

In interviews, a strong answer separates what the model sees from what the runtime knows.

## Mental Model
Use the five-layer model from the docs:

- input context
- runtime context
- context compression
- context isolation
- long-term memory

That split is crucial:

- input context goes into the prompt
- runtime context stays outside the prompt unless code explicitly injects it
- compression manages window pressure
- isolation keeps heavy subtasks out of the parent window
- long-term memory persists across threads

## Architecture / Flow
```text
prompt assembly
  -> custom system_prompt
  -> built-in Deep Agents instructions
  -> memory files
  -> skill metadata and skill bodies when activated
  -> tool prompts and tool descriptions
  -> subagent/task guidance
  -> user middleware prompts
  -> HITL prompt when configured

runtime state
  -> context_schema-defined invoke-time data
  -> ToolRuntime access inside tools and middleware

window management
  -> offload large tool I/O
  -> summarize old history
  -> delegate heavy work to subagents
  -> reload only what is needed
```

## Key Concepts
- `system_prompt=` is static. If prompt content depends on runtime values or the store, use `@dynamic_prompt` instead of trying to hard-code every case.

- `@dynamic_prompt` is the right primitive when instructions depend on context such as user role, access level, feature flags, or stored preferences.

- `context_schema=` defines per-run runtime context. Use a `dataclass` or `TypedDict` shape, then pass actual values at invoke time with `context=...`.

- Runtime context is not automatically included in the model prompt. The model only sees it if a tool, middleware, or prompt builder reads it and injects it.

- Tool descriptions are context too. Built-in and custom tool schemas are repeatedly sent to the model, so unused tools consume tokens every turn. The docs explicitly recommend `excluded_tools` when you want to shrink that baseline.

- Use `state_schema=` for mutable graph state that should be checkpointed and updated during execution. Use `context_schema=` for immutable per-run configuration such as user IDs or API keys.

- Subagents are part of context management, not only parallelism. Their main value is often context isolation: heavy work stays in the child window and the parent receives only a compact result.

- Runtime context propagates to subagents. If one subagent needs special settings, use namespaced keys such as `researcher:max_depth` or model the settings as separate context fields.

## Metrics and Formulas to Memorize
- `5` context layers: input context, runtime context, compression, isolation, long-term memory
- Runtime rule to memorize: `context_schema` values are hidden runtime inputs, not prompt text by default
- Prompt-size rule: unused built-in tools still send schemas unless removed with `excluded_tools`
- Data-placement rule:
  - `context_schema` -> immutable per-run configuration
  - `state_schema` -> mutable checkpointed state
- The official docs are mostly architectural here and do not provide universal context-window benchmarks on this page

## Trade-offs and Failure Modes
- Stuffing user-specific secrets or credentials into the prompt instead of runtime context wastes tokens and weakens safety.

- Overusing memory for task-specific procedures makes the prompt heavy. Those procedures often belong in skills instead.

- Keeping every tool available "just in case" silently bloats the prompt because tool descriptions ride along on every turn.

- Skipping subagents for output-heavy work makes the parent context absorb all intermediate tool results and reasoning.

- Mixing up `state_schema` and `context_schema` is a classic design bug: configuration becomes mutable state, or durable state becomes invisible runtime config.

## Interview Q&A
**Q: What is the difference between input context and runtime context?**  
A: Input context is prompt-visible material such as system prompt, memory, skills, and tool prompts. Runtime context is invoke-time data hidden from the model unless code explicitly injects it.

**Q: When should I use `@dynamic_prompt`?**  
A: When instructions depend on runtime context or stored data, such as role-based behavior or user-specific preferences.

**Q: What does `context_schema` do?**  
A: It types the per-run runtime context so tools and middleware can safely access values like user IDs, API keys, and feature flags.

**Q: Does runtime context automatically show up in the prompt?**  
A: No. That is a key distinction in the docs.

**Q: Why are subagents part of context management?**  
A: Because they isolate heavy work into a separate context window and return only a compact result to the parent.

**Q: How do tool descriptions affect context size?**  
A: Unused tool schemas still consume prompt budget, which is why the docs recommend removing tools the agent should never call.

## Sources
- [Context engineering in Deep Agents](https://docs.langchain.com/oss/python/deepagents/context-engineering.md)
- [Customize Deep Agents](https://docs.langchain.com/oss/python/deepagents/customization.md)
- [Subagents](https://docs.langchain.com/oss/python/deepagents/subagents.md)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
