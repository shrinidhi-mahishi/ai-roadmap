# Memory

## Why It Matters
Deep Agents treats memory as a filesystem-backed persistence layer, not as vague "the model remembers things now." That is the interview-friendly framing. Memory matters because project conventions, user preferences, and durable agent instructions should survive across threads without being manually re-pasted into every run.

The hard part is not persistence alone. It is scoping, write policy, and protection against poisoned shared state.

## Mental Model
Start with the main API:

- `memory=["/memories/AGENTS.md"]`

That tells Deep Agents which files to load persistently into the prompt. The backend then decides where those files live and who shares them.

There are two memory modes to keep distinct:

- semantic memory: durable files such as `AGENTS.md`, preferences, and policies
- episodic memory: past conversation history preserved as checkpointed threads

Deep Agents gives you both, but through different mechanisms.

## Architecture / Flow
```text
agent startup
  -> load memory files from memory=
  -> inject them into prompt

during run
  -> optional edit_file updates memory on the hot path
  -> backend persists changes by namespace

between runs
  -> future threads reload memory files
  -> optional background consolidation agent merges recent thread history

episodic path
  -> thread checkpoints preserve conversation history
  -> app can expose search tools over thread history when needed
```

## Key Concepts
- `AGENTS.md` is the canonical memory format called out in the docs, but `memory=` can point to other routed files too.

- Memory files are always loaded when configured. This is the key difference from skills, which use progressive disclosure.

- Scoping is a backend design question:
  - agent-scoped memory: `namespace=lambda rt: (rt.server_info.assistant_id,)`
  - user-scoped memory: `namespace=lambda rt: (rt.server_info.user.identity,)`
  - per-agent-per-user memory: `namespace=lambda rt: (rt.server_info.assistant_id, rt.server_info.user.identity)`

- By default, the agent can update memory during the conversation using `edit_file`. The docs call this the hot path.

- Background consolidation is the alternative. A separate agent periodically reviews recent conversations, extracts facts, and merges them into memory. The docs present this as the pattern for sleep-time compute.

- Episodic memory already exists because Deep Agents use checkpointers. What is missing by default is search over past threads, which you add by wrapping thread-history APIs in a tool.

- Shared memory should usually be read-only. Organization policies and common knowledge bases are exactly where prompt injection through shared state becomes dangerous.

- Concurrent writes are possible. The docs warn that parallel writes to the same memory file can degrade into last-write-wins behavior, especially for agent-scoped or organization-scoped memory.

## Metrics and Formulas to Memorize
- Canonical file anchor: `AGENTS.md`
- Common namespace patterns:
  - agent scope -> `(assistant_id,)`
  - user scope -> `(user.identity,)`
  - agent + user scope -> `(assistant_id, user.identity)`
- Update strategies:
  - hot path -> write during conversation
  - background -> consolidate between conversations
- Scheduling rule from the docs: `cron interval ~= lookback window`
- The docs are architectural here and do not provide universal recall or latency benchmarks for memory quality

## Trade-offs and Failure Modes
- Shared writable memory is the biggest risk. If one user can write memory that another user later reads, you have a prompt-injection channel.

- Overusing always-loaded memory makes the prompt heavier than it needs to be. Large task-specific procedures often belong in skills instead.

- Background consolidation reduces user-facing latency but introduces staleness. New facts may not be available until the next consolidation run.

- Hot-path writes are immediate, but they add latency and increase the chance that the active agent spends too much effort maintaining memory mid-task.

- Multiple writers to the same file can create last-write-wins conflicts unless you partition memory or serialize updates.

## Interview Q&A
**Q: How does memory work in Deep Agents?**  
A: You pass memory file paths with `memory=`, Deep Agents loads them into the prompt at startup, and the backend decides where they persist and how they are scoped.

**Q: What is the difference between memory and skills?**  
A: Memory is always loaded because it is assumed to be always relevant. Skills are loaded progressively only when needed.

**Q: What is episodic memory in this system?**  
A: Checkpointed thread history. The runtime already preserves past conversations; if you want search over them, you expose that through a tool.

**Q: When should memory be user-scoped versus agent-scoped?**  
A: Use user scope for preferences and private context. Use agent scope when the agent itself should accumulate shared behavior or knowledge across users.

**Q: Why make shared memory read-only?**  
A: To prevent prompt injection through shared durable state and keep policies under application control.

**Q: When should I use background consolidation?**  
A: When hot-path writes add too much latency or when you want a separate agent to synthesize patterns across multiple recent conversations.

## Sources
- [Memory](https://docs.langchain.com/oss/python/deepagents/memory.md)
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends.md)
- [Permissions](https://docs.langchain.com/oss/python/deepagents/permissions.md)
- [Context engineering in Deep Agents](https://docs.langchain.com/oss/python/deepagents/context-engineering.md)
