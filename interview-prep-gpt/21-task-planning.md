# Task Planning

## Why It Matters
Deep Agents is explicit about planning: it is not hidden "reasoning magic" inside the model. If you want a visible plan, you opt into a concrete tool and state channel. That is a strong interview point because it distinguishes product-grade planning from invisible chain-of-thought speculation.

Task planning matters most when work is multi-step, long-running, or user-visible enough that progress tracking is part of the product.

## Mental Model
Task planning in Deep Agents is the `TodoListMiddleware` capability:

- it adds the `write_todos` tool
- the agent stores a `todos` array in graph state
- each todo has a status
- the UI reads that state directly through streaming

So planning here is not abstract. It is a synchronized todo list.

## Architecture / Flow
```text
create_deep_agent(..., middleware=[TodoListMiddleware()])
  -> write_todos tool becomes available
  -> agent creates or updates todos in state
  -> statuses move:
     pending -> in_progress -> completed
  -> stream.values.todos reflects updates live
  -> UI renders progress list or progress bar
```

The docs frame this as a progress dashboard built from agent state, not from post-hoc text parsing.

## Key Concepts
- `TodoListMiddleware()` is opt-in. Starting in `deepagents v0.7`, task planning is no longer included by default.

- The tool is `write_todos`, not a hidden planner API. The model explicitly creates and updates task items as it works.

- The state surface is `stream.values.todos`. This is how the frontend stays synchronized with the plan.

- Supported statuses in the docs are:
  - `pending`
  - `in_progress`
  - `completed`

- The best fit for planning is:
  - long multi-step tasks
  - weaker models that benefit from explicit accountability
  - UIs where progress visibility matters

- The todo list is ordinary graph state, so it can be rendered separately from chat bubbles and updated reactively.

- Good UI patterns from the docs are worth remembering:
  - show the todo list prominently
  - hide it when empty
  - highlight only one `in_progress` item
  - derive a visible progress percentage

## Metrics and Formulas to Memorize
- Planning is opt-in starting in `v0.7`
- Status lifecycle:
  - `pending -> in_progress -> completed`
- Progress formula from the frontend docs:
  - `percentage = round(completed / total * 100)`
- Streaming surface to memorize: `stream.values.todos`
- The official docs do not provide benchmark accuracy improvements from todo planning; they position it as a reliability and UX aid

## Trade-offs and Failure Modes
- Adding planning to trivial requests creates noise and may waste tool calls.

- A todo list is not proof of correctness. An agent can mark the wrong work as completed.

- Multiple simultaneous `in_progress` items make the UI hard to scan and dilute the value of the progress display.

- If you do not opt in with `TodoListMiddleware`, there is no `write_todos` tool and no `stream.values.todos` channel to render.

- Planning helps weaker models and long tasks, but it is still another moving part. Use it when the workflow or UI genuinely benefits from explicit structure.

## Interview Q&A
**Q: Is task planning built into Deep Agents by default?**  
A: Not anymore. Starting in `deepagents v0.7`, it is opt-in through `TodoListMiddleware()`.

**Q: What does task planning actually add?**  
A: A concrete `write_todos` tool plus a `todos` state channel that the agent updates during execution.

**Q: How do I show planning progress in the UI?**  
A: Read `stream.values.todos` and render the items and derived completion metrics directly.

**Q: What statuses should I remember?**  
A: `pending`, `in_progress`, and `completed`.

**Q: Why is this better than parsing the agent's text for progress?**  
A: Because it is structured state, not best-effort string interpretation.

**Q: When is task planning most useful?**  
A: Long multi-step requests, weaker models, and any UI where visible progress improves the user experience.

## Sources
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
- [Todo list](https://docs.langchain.com/oss/python/deepagents/frontend/todo-list.md)
