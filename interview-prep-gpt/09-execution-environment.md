# Execution Environment

## Why It Matters
The execution environment is where a Deep Agent actually does work. That sounds obvious, but it is the difference between a toy agent that only talks and a real one that can read files, route storage, enforce path rules, and run code. In interviews, this is the clean way to explain where side effects live and how Deep Agents separates storage, permissions, and execution.

The Deep Agents docs present the environment as a layered system, not a single sandbox toggle.

## Mental Model
Use a four-layer model:

- tools: custom functions, APIs, databases, and built-ins
- virtual filesystem: the model-facing file surface
- filesystem permissions: declarative rules over built-in file tools
- code execution: shell execution or in-process interpretation

Then map those layers onto the backend you choose:

- `StateBackend` for thread-scoped scratch space
- `FilesystemBackend` for local disk
- `StoreBackend` for cross-thread durable storage
- `ContextHubBackend` for LangSmith Context Hub repos
- `CompositeBackend` for path-based routing
- `LocalShellBackend` for host shell execution
- sandbox backends for isolated shell execution

## Architecture / Flow
```text
agent tool call
  -> FilesystemMiddleware
  -> backend resolution
     -> StateBackend / FilesystemBackend / StoreBackend
     -> ContextHubBackend / CompositeBackend / LocalShellBackend / sandbox
  -> optional permissions check for built-in file tools
  -> optional code execution layer
     -> execute (sandbox or local shell)
     -> eval (interpreter middleware)
  -> result returns to model
```

The main-agent stack from the docs is also worth memorizing:

1. `SkillsMiddleware` when `skills=` is configured
2. `FilesystemMiddleware`
3. `SubAgentMiddleware` when synchronous subagents exist
4. `SummarizationMiddleware`
5. `PatchToolCallsMiddleware`
6. `AsyncSubAgentMiddleware` when async subagents are configured
7. user-supplied `middleware=`
8. harness profile extras
9. excluded-tool filtering
10. `AnthropicPromptCachingMiddleware` and `BedrockPromptCachingMiddleware`
11. `MemoryMiddleware`

When `interrupt_on` is configured, Deep Agents also adds `HumanInTheLoopMiddleware`.

## Key Concepts
- `StateBackend()` is the default. It stores files in graph state for the current thread and persists them across turns through checkpoints, but not across threads.

- `FilesystemBackend(root_dir=..., virtual_mode=True)` exposes real disk under a root directory. It is convenient for local coding tasks, but it writes actual files and should be used carefully.

- `StoreBackend(namespace=lambda rt: (...))` stores files in a LangGraph `BaseStore`. It is the main cross-thread persistence story for memories and durable agent data.

- `ContextHubBackend("my-agent")` mounts a LangSmith Context Hub repo as the agent's filesystem. Linked skill repos appear under `/skills/`, which makes it a good fit for repo-backed memory and skills.

- `CompositeBackend(default=..., routes={...})` is the routing primitive. Longer prefixes win, so `/memories/projects/` can override `/memories/`.

- `LocalShellBackend(root_dir=".", virtual_mode=True, env={...})` extends filesystem access with `execute`, but commands run directly on the host via `subprocess.run(shell=True)`. It is explicitly not isolated.

- Sandbox backends also expose `execute`, but unlike `LocalShellBackend`, they provide an isolated environment. The docs recommend sandboxes for production code execution.

- The backend factory pattern is deprecated. Modern Deep Agents code passes prebuilt backend instances directly instead of runtime factory functions.

## Metrics and Formulas to Memorize
- `4` execution layers: tools, virtual filesystem, permissions, code execution
- `StateBackend()` is the default backend
- `LocalShellBackend` defaults called out in docs:
  - `timeout=120s`
  - `max_output_bytes=100000`
- `virtual_mode=False` provides no real path safety even if `root_dir` is set
- `CompositeBackend` route rule: longest prefix wins
- The docs do not publish universal p50/p95/p99 numbers for these backends; treat environment performance as workload- and provider-specific

## Trade-offs and Failure Modes
- Using `FilesystemBackend` alone mixes agent artifacts like `/large_tool_results/` and `/conversation_history/` into your real project tree. The docs explicitly recommend wrapping it in `CompositeBackend` for most real use cases.

- `LocalShellBackend` is fast for local development but is the least safe option. It combines real filesystem writes with unrestricted host shell access.

- `virtual_mode=True` is helpful for path normalization on filesystem backends, but it is not a security boundary once shell execution exists.

- Forgetting a `namespace` on `StoreBackend` can collapse multiple users into shared storage.

- If you override `FilesystemMiddleware` yourself, you must pass the `backend` and any `permissions` directly to that middleware instance. It does not "inherit" them automatically from `create_deep_agent`.

## Interview Q&A
**Q: What are the four execution-environment layers in Deep Agents?**  
A: Tools, virtual filesystem, filesystem permissions, and code execution.

**Q: What backend do I get by default?**  
A: `StateBackend()`, which is thread-scoped and checkpoint-backed.

**Q: When should I use `CompositeBackend`?**  
A: When different path prefixes need different persistence or safety properties, such as ephemeral `/workspace/` plus durable `/memories/`.

**Q: What is the difference between `LocalShellBackend` and a sandbox backend?**  
A: Both expose `execute`, but `LocalShellBackend` runs on the host without isolation while sandbox backends execute in isolated environments.

**Q: Why does middleware order matter here?**  
A: Because file access, subagents, summarization, caching, memory, and HITL are all layered middleware behaviors. Wrong assumptions about order lead to wrong assumptions about prompt shape, permissions, and execution flow.

**Q: What is the cleanest production pattern for local project files?**  
A: Use `CompositeBackend` with `StateBackend()` as the default and route the real project path to `FilesystemBackend(...)` so internal artifacts stay out of your repository tree.

## Sources
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends.md)
- [Customize Deep Agents](https://docs.langchain.com/oss/python/deepagents/customization.md)
- [Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes.md)
- [Interpreters](https://docs.langchain.com/oss/python/deepagents/interpreters.md)
