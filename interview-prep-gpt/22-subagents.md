# Subagents

## Why It Matters
Subagents are the main isolation primitive in Deep Agents. They let the coordinator hand work to a child with a fresh context window, let that child do its own tool use, and then take back a compact result instead of every intermediate step. In interviews, this is the core reason Deep Agents handles long tasks better than a single overloaded agent loop.

The important detail is not only that subagents exist. It is what they inherit and what they do not.

## Mental Model
There are four subagent flavors to keep straight:

- the built-in synchronous `general-purpose` subagent
- custom dictionary-defined synchronous subagents
- `CompiledSubAgent` for prebuilt LangGraph graphs
- dynamic and async variants layered on top of the same delegation idea

The default is surprisingly important: unless you disable or replace it, every Deep Agent already has a synchronous `general-purpose` subagent and therefore a `task` tool.

## Architecture / Flow
```text
parent agent
  -> task(name=..., task=...)
  -> select child
     -> general-purpose
     -> custom SubAgent dict
     -> CompiledSubAgent
  -> child runs in fresh context
  -> child uses inherited or overridden config
  -> child returns one final report or structured result

variants
  -> dynamic subagents: interpreter uses task() from code
  -> async subagents: background stateful workers with task IDs
```

Subagents are therefore both an execution primitive and a context-management primitive.

## Key Concepts
- The built-in `general-purpose` subagent exists by default. It:
  - has its own default prompt
  - uses the same model unless overridden
  - has access to the same tools
  - inherits the main agent's skills when skills are configured

- To replace the default, add your own subagent with `name="general-purpose"`.

- To remove it entirely, disable `general_purpose_subagent` in the active harness profile and pass no synchronous subagents.

- Dictionary-defined `SubAgent` specs are the common path. Important fields include:
  - `name`
  - `description`
  - `system_prompt`
  - optional `tools`
  - optional `model`
  - optional `middleware`
  - optional `interrupt_on`
  - optional `skills`
  - optional `response_format`
  - optional `permissions`

- `CompiledSubAgent` is the escape hatch for more complex child workflows. It packages a precompiled LangGraph `runnable` behind the same named delegation interface.

- The inheritance rules are the high-value interview detail:
  - `system_prompt`: does not inherit; custom subagents must define their own
  - `tools`: inherit by default, but specifying `tools` replaces the inherited set
  - `model`: inherits by default, override per subagent if needed
  - `middleware`: does not inherit; subagent middleware is matched against the subagent stack independently
  - `interrupt_on`: inherits by default, subagent value overrides
  - `skills`: only the `general-purpose` subagent inherits; custom subagents need explicit `skills`
  - `permissions`: inherit by default, but a subagent `permissions=` list replaces the parent's rules entirely
  - runtime context: propagates automatically from parent to child

- Runtime context propagation is not enough for per-child specialization. If only one child needs extra configuration, use namespaced keys like `researcher:max_depth`.

- Dynamic subagents require the interpreter bridge and expose `task()` inside code. Async subagents are a separate background-worker primitive with stateful threads.

## Metrics and Formulas to Memorize
- Default synchronous child: `general-purpose`
- Disablement rule:
  - `general_purpose_subagent.enabled = False`
  - plus no synchronous `subagents=`
- Inheritance shorthand:
  - inherits: `tools`, `model`, `interrupt_on`, `permissions`, runtime context
  - does not inherit: `system_prompt`, `middleware`, custom `skills`
  - special case: only `general-purpose` inherits parent skills
- Dynamic subagents require `langchain-quickjs>=0.2.0` and Python `>=3.11`
- The docs are conceptual here and do not publish benchmark win rates for when subagents outperform single-agent execution

## Trade-offs and Failure Modes
- Forgetting that the default `general-purpose` subagent exists is the easiest way to misunderstand where the `task` tool came from.

- Poorly scoped custom subagents are expensive context isolation with no specialization benefit.

- Assuming skill inheritance for custom subagents is a common error. The child silently loses capabilities unless you pass `skills=` explicitly.

- Overriding `tools` can accidentally remove safe inherited capabilities or expose too much if you treat child tools as an afterthought.

- Turning off subagents by trying to remove `SubAgentMiddleware` is the wrong path. The docs explicitly say that approach is rejected.

## Interview Q&A
**Q: What subagent types exist in Deep Agents?**  
A: The built-in synchronous `general-purpose` child, custom dictionary-defined synchronous subagents, `CompiledSubAgent`, dynamic subagents through the interpreter, and async subagents for background work.

**Q: What is the default subagent behavior?**  
A: A synchronous `general-purpose` subagent is auto-added unless you replace or disable it.

**Q: What are the most important inheritance rules?**  
A: Tools and model inherit by default, `system_prompt` and middleware do not, permissions inherit unless replaced, and only the `general-purpose` child inherits parent skills.

**Q: When should I use `CompiledSubAgent`?**  
A: When the child needs a prebuilt LangGraph workflow instead of a simple declarative spec.

**Q: How do dynamic subagents differ from normal `task` calls?**  
A: The interpreter dispatches them from code using `task()`, which is useful for loops, filtering, and batched orchestration.

**Q: How do I remove the `task` tool entirely?**  
A: Disable the general-purpose subagent in the harness profile and provide no synchronous subagents.

## Sources
- [Subagents](https://docs.langchain.com/oss/python/deepagents/subagents.md)
- [Dynamic subagents](https://docs.langchain.com/oss/python/deepagents/dynamic-subagents.md)
- [Async subagents](https://docs.langchain.com/oss/python/deepagents/async-subagents.md)
- [Skills](https://docs.langchain.com/oss/python/deepagents/skills.md)
- [Customize Deep Agents](https://docs.langchain.com/oss/python/deepagents/customization.md)
