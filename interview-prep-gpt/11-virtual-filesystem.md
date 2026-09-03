# Virtual Filesystem

## Why It Matters
Deep Agents treats files as a first-class agent primitive. That is a big design choice. Instead of forcing everything through prompt text, the harness gives the model a file namespace it can search, read, write, and reuse across turns. In interviews, this is the right abstraction to emphasize because memory, skills, context offloading, and code execution all build on the same file surface.

The important point is that the model-facing filesystem is abstracted from storage. The agent sees paths; the backend decides where those paths live.

## Mental Model
Think of the virtual filesystem as a stable API boundary:

- the model sees file tools
- `FilesystemMiddleware` exposes those tools
- a backend resolves paths to state, disk, store, Context Hub, or sandbox storage

That means the same agent behavior can target:

- thread-scoped scratch files with `StateBackend()`
- local project files with `FilesystemBackend(...)`
- durable cross-thread files with `StoreBackend(...)`
- routed hybrid storage with `CompositeBackend(...)`

## Architecture / Flow
```text
model
  -> built-in file tools
     -> ls
     -> read_file
     -> write_file
     -> edit_file
     -> delete
     -> glob
     -> grep
  -> FilesystemMiddleware
  -> backend path resolution
  -> file content or artifact
  -> other Deep Agents features reuse the same namespace
     -> /skills/
     -> /memories/
     -> /large_tool_results/
     -> /conversation_history/
```

The VFS is not only for "user files." Deep Agents uses it internally for offloaded tool output and preserved conversation history.

## Key Concepts
- The built-in model-visible filesystem tools are:
  - `ls`
  - `read_file`
  - `write_file`
  - `edit_file`
  - `delete`
  - `glob`
  - `grep`

- `read_file` is multimodal-aware. For non-text content it can return standard content blocks for images, video, audio, and documents rather than pretending everything is plain text.

- The filesystem surface is reusable infrastructure. Skills, memory, context offloading, sandbox seeding, and delegated work can all rely on the same path namespace.

- If you want no model-visible file tools, use a harness profile with `excluded_tools`. This hides the tools from the model, but it does not remove `FilesystemMiddleware` itself.

- If you want only a subset of file tools, pass your own `FilesystemMiddleware(tools=[...])`. The docs call out an important invariant: `read_file` must always be included or agent creation raises `ValueError`.

- `virtual_mode=True` matters on `FilesystemBackend` and `LocalShellBackend`. It normalizes paths under `root_dir` and blocks path escapes like `..`, `~`, or absolute paths outside the root. The docs also warn that this is not a full security boundary once shell execution exists.

- The general-purpose subagent inherits main-agent filesystem middleware overrides. Declarative custom subagents do not; if you want different file-tool limits there, pass their own `FilesystemMiddleware(...)`.

- `FilesystemBackend` alone writes Deep Agents' internal files into the real root directory. For most real projects, the docs recommend wrapping it in `CompositeBackend` so the project tree and internal artifacts stay separate.

## Metrics and Formulas to Memorize
- `7` core built-in filesystem tools: `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`
- `execute` is not part of the base VFS surface; it appears only with shell-capable backends
- `delete` requires `deepagents>=0.7`
- `FilesystemMiddleware(tools=[...])` allowlisting requires `deepagents>=0.7`
- `read_file` is mandatory in any file-tool allowlist
- The docs describe supported multimodal file categories, but they do not publish universal read/write performance numbers

## Trade-offs and Failure Modes
- Hiding file tools with `excluded_tools` only removes model visibility. It does not remove filesystem scaffolding from the harness.

- Treating `virtual_mode=True` as full security is a mistake. It is path normalization, not a sandbox for unrestricted shell access.

- If you use `FilesystemBackend` directly on a project root, Deep Agents can mix internal artifacts with source files. That is cleanly avoidable with `CompositeBackend`.

- File-tool allowlisting only affects built-in filesystem tools. Custom tools you add through `tools=` are unaffected.

- Backends that do not support `delete` or `execute` quietly drop those tools from the model surface. If your prompt assumes they exist, behavior will drift.

## Interview Q&A
**Q: What is the virtual filesystem in Deep Agents?**  
A: It is the model-facing file namespace exposed through built-in tools and resolved by a pluggable backend.

**Q: Why is the virtual filesystem important beyond file editing?**  
A: Because memory, skills, context offloading, and code execution all reuse the same file surface.

**Q: What is the difference between `excluded_tools` and overriding `FilesystemMiddleware`?**  
A: `excluded_tools` hides tools from the model. Overriding `FilesystemMiddleware` changes the actual built-in file-tool configuration.

**Q: Why must `read_file` stay in a file-tool allowlist?**  
A: The docs treat it as foundational. Omitting it raises `ValueError` when the agent is created.

**Q: What does `virtual_mode=True` actually do?**  
A: It normalizes and constrains paths under `root_dir`, but it is not a complete security boundary when shell access exists.

**Q: Why is `CompositeBackend` a common companion to `FilesystemBackend`?**  
A: It lets real project files live on disk while Deep Agents' internal artifacts stay in ephemeral or separate storage.

## Sources
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends.md)
- [Customize Deep Agents](https://docs.langchain.com/oss/python/deepagents/customization.md)
