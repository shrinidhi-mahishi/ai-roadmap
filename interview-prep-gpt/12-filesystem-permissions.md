# Filesystem Permissions

## Why It Matters
Deep Agents gives the model built-in file tools, so you need a deterministic boundary around what those tools can touch. `FilesystemPermission` is that boundary. In interview language: this is not "prompt the model to be careful"; it is declarative policy enforced on the built-in filesystem tool layer.

The subtle but important caveat is that permissions are path-based controls over built-in file tools only. They are not a general sandbox for arbitrary code execution or custom tools.

## Mental Model
Treat permissions as an ordered rule list:

- classify the operation as `read` or `write`
- test the requested path against rules from top to bottom
- stop at the first match
- apply `allow`, `deny`, or `interrupt`
- if nothing matches, allow by default

This is simple enough to reason about quickly and strict enough to matter in production.

## Architecture / Flow
```text
built-in filesystem tool call
  -> FilesystemMiddleware
  -> map tool to operation
     -> read: ls, read_file, glob, grep
     -> write: write_file, edit_file, delete
  -> evaluate FilesystemPermission rules in order
  -> first match wins
     -> allow: run tool
     -> deny: block tool
     -> interrupt: pause for human review
  -> return result or rejection
```

Subagents inherit the parent's permission rules by default, but a subagent-level `permissions=` list replaces the parent's rules entirely.

## Key Concepts
- The API is `FilesystemPermission(operations=[...], paths=[...], mode=...)`.

- `operations` accepts `"read"` and/or `"write"`.
  - `"read"` covers `ls`, `read_file`, `glob`, and `grep`
  - `"write"` covers `write_file`, `edit_file`, and `delete`

- `paths` is a list of glob patterns such as `"/workspace/**"` or `"/workspace/.env"`.

- `mode` can be:
  - `"allow"`
  - `"deny"`
  - `"interrupt"`

- Evaluation is first-match-wins. This is why narrow rules belong before broad catch-alls.

- `mode="interrupt"` pauses instead of allowing or denying outright. It plugs into the same human-in-the-loop flow as `interrupt_on`.

- Permissions apply only to built-in filesystem tools. They do not cover:
  - custom tools that read or write files
  - MCP tools with file access
  - sandbox `execute` calls

- Directory deletion is conservative. `delete` checks the target directory and every descendant path and rejects the whole operation if any descendant is denied.

- Plain-file deletion is narrower. The docs note that `delete` now uses exact-match behavior for files, so an earlier specific `allow` can beat a later catch-all `deny`.

- With a `CompositeBackend` whose default backend is a sandbox, permission paths must live under routed prefixes. Otherwise Deep Agents raises `NotImplementedError`, because path rules cannot safely constrain arbitrary shell access in the default sandbox backend.

## Metrics and Formulas to Memorize
- Permissions require `deepagents>=0.5.2`
- `mode="interrupt"` requires `deepagents>=0.6.8`
- Exact-match plain-file delete behavior requires `deepagents>=0.7.3`
- Evaluation rule: `first matching rule wins`, otherwise `allow`
- Scope reminder:
  - built-in file tools only
  - not `execute`
  - not custom tools
  - not MCP tools
- The docs do not provide performance benchmarks for permission evaluation because this feature is primarily about correctness and safety

## Trade-offs and Failure Modes
- Wrong rule ordering is the classic bug. If `"/workspace/**"` is allowed before `"/workspace/.env"` is denied, the deny never fires.

- Treating permissions as a sandbox is incorrect. A shell-capable backend can still reach paths outside the built-in file tools.

- Unanchored interrupt patterns can overfire on bulk operations like `ls`, `glob`, `grep`, or directory `delete`, because the system has to be conservative about possible overlap.

- Shared parent permissions can be too broad for specialized subagents. If a child should be narrower, give it its own `permissions=` list instead of assuming context alone makes it safer.

- Catch-all rules like `"/**"` can be invalid on routed composites with sandbox defaults, because they cross from routed storage into shell-capable territory.

## Interview Q&A
**Q: How do Deep Agents filesystem permissions work?**  
A: They are declarative `FilesystemPermission` rules evaluated top to bottom with first-match-wins semantics on the built-in filesystem tools.

**Q: What operations do `read` and `write` actually cover?**  
A: `read` covers `ls`, `read_file`, `glob`, and `grep`; `write` covers `write_file`, `edit_file`, and `delete`.

**Q: Do these permissions cover `execute`?**  
A: No. The docs explicitly say they do not apply to sandbox backends or arbitrary shell execution.

**Q: What does `mode="interrupt"` do?**  
A: It turns a matching file operation into a human-review pause instead of an automatic allow or deny.

**Q: How do permissions behave with subagents?**  
A: Subagents inherit the parent rules by default, but a subagent's own `permissions=` list replaces the parent's rules entirely.

**Q: Why is directory `delete` more conservative than file `delete`?**  
A: Because Deep Agents checks the whole subtree and refuses partial deletion if any descendant path would violate policy.

## Sources
- [Permissions](https://docs.langchain.com/oss/python/deepagents/permissions.md)
- [Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop.md)
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends.md)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
