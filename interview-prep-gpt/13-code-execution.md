# Code Execution

## Why It Matters
Most real agent tasks eventually hit a boundary where plain tool calling is too weak. You need loops, batching, shell commands, package installs, test execution, or isolated artifact generation. Deep Agents supports two very different code-execution models, and interviews go better when you explain why both exist instead of treating "code execution" as one feature.

The short answer is: use sandboxes for operating-system work, and use the interpreter for in-memory orchestration.

## Mental Model
Deep Agents supports code execution in two ways:

- shell execution through `execute` on sandbox-like backends
- in-process JavaScript execution through `CodeInterpreterMiddleware`

Those solve different problems:

- sandbox backends are for OS-level work: install deps, run tests, call CLIs, manipulate files
- interpreters are for control-flow work: loops, filtering, batching, deterministic transforms, and programmatic tool calling

The interview trap is assuming the interpreter is a sandbox. It is not.

## Architecture / Flow
```text
agent needs code execution
  -> choose shell path or interpreter path

shell path:
  model -> execute
        -> sandbox backend or LocalShellBackend
        -> command runs in OS environment

interpreter path:
  model -> eval
        -> CodeInterpreterMiddleware
        -> QuickJS runtime
        -> optional bridges
           -> tools.* via PTC
           -> task() via dynamic subagents
```

File movement across a sandbox boundary is separate from in-agent tool use:

- the agent uses `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute`
- your application code uses `upload_files()` and `download_files()`

## Key Concepts
- Sandbox backends expose `execute` in isolated environments. The docs describe them as the right choice when the agent needs shell access, package installs, or OS-level files.

- Internally, sandbox providers implement `execute()`. The base sandbox layer builds other filesystem operations on top of that primitive.

- `LocalShellBackend` also exposes `execute`, but it runs directly on the host. It is powerful for local development and explicitly unsafe for hostile or multi-tenant scenarios.

- `CodeInterpreterMiddleware()` adds an `eval` tool. The model writes JavaScript and calls `eval`; you do not invoke QuickJS directly.

- By default, interpreter code has no filesystem, network, shell, package manager, or clock access. It can compute, hold state, and log through `console`.

- Programmatic tool calling (PTC) is the bridge from the interpreter to ordinary tools. Enable it with an explicit allowlist such as `CodeInterpreterMiddleware(ptc=["web_search"])`.

- Inside the interpreter, PTC tools appear under the global `tools` namespace and use camelCase names. For example, `web_search` becomes `tools.webSearch(...)`.

- Dynamic subagents are a second bridge. When subagents are configured, the interpreter can dispatch them with the built-in `task()` global. If you want to disable that bridge, use `CodeInterpreterMiddleware(subagents=False)`.

- Persistence is controlled by `mode=`:
  - `"thread"`: state persists across turns
  - `"turn"`: state persists within one turn only
  - `"call"`: fresh REPL every `eval`

- PTC does not go through the normal tool-call path. The docs explicitly warn that `interrupt_on` approval workflows are not enforced per PTC-invoked tool call.

## Metrics and Formulas to Memorize
- Interpreters require `langchain-quickjs>=0.2.0` and Python `>=3.11`
- `CodeInterpreterMiddleware(mode="thread")` is the default
- `max_ptc_calls=256` is the default ceiling per `eval`
- `LocalShellBackend` defaults called out in docs:
  - `timeout=120s`
  - `max_output_bytes=100000`
- Capability split:
  - sandbox -> OS, shell, files, packages
  - interpreter -> in-memory compute, optional `tools.*`, optional `task()`
- The docs do not give universal benchmark numbers for sandbox startup, command latency, or interpreter throughput across providers

## Trade-offs and Failure Modes
- Using the interpreter for shell work is a bad fit. QuickJS is for orchestration and deterministic computation, not package installs or OS integration.

- Using `LocalShellBackend` in anything resembling production is risky because it combines unrestricted shell access with direct host file access.

- QuickJS is embedded in-process, not a VM. It is a capability-scoped runtime, not a strong isolation boundary for hostile code.

- PTC expands power quickly. Every tool you expose through the allowlist becomes callable from code, so the allowlist is a real permission boundary.

- Interpreter snapshots restore serializable in-memory state only. They do not roll back external side effects caused by tools or shell commands.

- Secrets inside a sandbox are still dangerous. The sandbox protects the host, but a context-injected agent can still read and exfiltrate sandbox-accessible credentials.

## Interview Q&A
**Q: What are the two code-execution paths in Deep Agents?**  
A: Shell execution through `execute` on a shell-capable backend, and JavaScript execution through `CodeInterpreterMiddleware`.

**Q: When should I use a sandbox instead of the interpreter?**  
A: Use a sandbox for OS-level work such as tests, package installs, and CLI calls. Use the interpreter for lightweight orchestration, loops, and data transforms.

**Q: What is PTC?**  
A: Programmatic tool calling. It exposes an allowlisted subset of tools inside the interpreter as async `tools.*` functions.

**Q: What do `mode="thread"`, `"turn"`, and `"call"` mean?**  
A: They control interpreter persistence across turns, within a turn, or per individual `eval` call.

**Q: Does `interrupt_on` protect PTC tool calls?**  
A: No. The docs explicitly say PTC-invoked tool calls do not use the normal approval path.

**Q: How do files move in and out of a sandbox?**  
A: The agent uses filesystem tools inside the sandbox; your application uses `upload_files()` and `download_files()` across the boundary.

## Sources
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
- [Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes.md)
- [Interpreters](https://docs.langchain.com/oss/python/deepagents/interpreters.md)
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends.md)
- [Dynamic subagents](https://docs.langchain.com/oss/python/deepagents/dynamic-subagents.md)
