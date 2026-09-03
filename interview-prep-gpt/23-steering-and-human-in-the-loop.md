# Steering and Human-in-the-Loop

## Why It Matters
An agent that can call tools without control is a liability. Deep Agents addresses that with runtime steering: the model can propose an action, but execution can pause for human review before the side effect happens. In interviews, this is the clean answer to "how do you make risky tool use safe enough to ship?"

The important design point is that Deep Agents uses explicit interrupts and resume flow, not vague "ask the model if this seems dangerous."

## Mental Model
Steering in Deep Agents has two main sources:

- `interrupt_on` for tool-name-based review policy
- `FilesystemPermission(mode="interrupt")` for path-based review policy on built-in file tools

Both feed the same human-in-the-loop mechanism:

1. proposed action is intercepted
2. execution pauses
3. reviewer chooses a decision
4. application resumes the same thread with `Command(resume=...)`

This makes approval part of runtime state, not just UI chrome.

## Architecture / Flow
```text
model proposes tool call
  -> HumanInTheLoopMiddleware and/or permission interrupt rule
  -> execution pauses
  -> result.interrupts contains action_requests + review configs
  -> reviewer chooses:
     approve | edit | reject | respond
  -> app resumes same thread with Command(resume={"decisions": [...]})
  -> tool executes or returns synthetic rejection/response

repair path
  -> PatchToolCallsMiddleware fixes dangling tool-call history on resume
```

The docs also show a lower-level pattern where a tool itself calls `interrupt()` directly and later resumes through `Command(resume=...)`.

## Key Concepts
- `interrupt_on` is a mapping from tool names to either:
  - `True`
  - `False`
  - `InterruptOnConfig`

- `InterruptOnConfig` lets you control `allowed_decisions`. In Python, it can also take a `when` predicate so only some calls interrupt.

- The supported review decisions are:
  - `approve`
  - `edit`
  - `reject`
  - `respond`

- `respond` is for "ask user" style tools where the human is effectively supplying the tool result. The docs explicitly warn not to use it as a disguised denial for side-effecting tools.

- Conditional interrupts use a `ToolCallRequest` predicate. This is the precise way to say "interrupt writes outside the workspace, but not ordinary writes."

- Multiple tool calls can be batched into one interrupt, so your resume payload must provide decisions in the same order as `action_requests`.

- Resume uses `Command(resume={"decisions": [...]})` and the same thread config. If you change thread identity, you are no longer resuming the same paused run.

- Filesystem permission interrupts merge with `interrupt_on`. This is useful when human review should depend on a path pattern instead of only a tool name.

- Checkpointers are required. The docs call this out repeatedly because HITL needs persisted state between pause and resume.

## Metrics and Formulas to Memorize
- Decision set: `approve | edit | reject | respond`
- Conditional interrupts require `langchain>=1.3.3`
- Filesystem permission interrupts require `deepagents>=0.6.8`
- Resume form to memorize:
  - `Command(resume={"decisions": [...]})`
- Requirement to memorize:
  - `checkpointer` is mandatory for pause/resume flows
- The official Deep Agents docs do not publish approval-rate or human-latency benchmarks; they focus on mechanism and control flow

## Trade-offs and Failure Modes
- Broad interrupt policies create approval fatigue. If every minor action pauses, reviewers stop reviewing.

- No checkpointer means no reliable human-in-the-loop. The agent cannot safely pause and continue without persisted state.

- Using `respond` to deny a write or external side effect is dangerous because the model may interpret it like a successful tool result.

- Weak `reject` messages can cause loops. If the rejection does not tell the agent what happened and what not to retry, it may simply attempt the same call again.

- PTC tool calls are a blind spot. The interpreter docs say `interrupt_on` is not enforced per PTC-invoked tool call, so steering strategy has to account for that.

## Interview Q&A
**Q: How do you enable human approval in Deep Agents?**  
A: Pass an `interrupt_on` map to `create_deep_agent`, or use filesystem permissions with `mode="interrupt"` for path-based review on built-in file tools.

**Q: What is `InterruptOnConfig` for?**  
A: It lets you customize allowed review actions and, in Python, define a `when` predicate for conditional interrupts.

**Q: How do you resume after a pause?**  
A: Use `Command(resume={"decisions": [...]})` on the same thread config.

**Q: What is the difference between `reject` and `respond`?**  
A: `reject` denies execution and feeds rejection feedback to the agent. `respond` supplies a synthetic tool result when the human is effectively acting as the tool.

**Q: Can path permissions trigger the same interrupt flow?**  
A: Yes. `FilesystemPermission(mode="interrupt")` uses the same human-in-the-loop mechanism for built-in filesystem tools.

**Q: Why is a checkpointer required?**  
A: Because the paused agent state has to survive between the interrupt and the later resume call.

## Sources
- [Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop.md)
- [Permissions](https://docs.langchain.com/oss/python/deepagents/permissions.md)
- [Customize Deep Agents](https://docs.langchain.com/oss/python/deepagents/customization.md)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
