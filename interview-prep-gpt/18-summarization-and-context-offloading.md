# Summarization and Context Offloading

## Why It Matters
Long-running agents do not usually fail because one answer is too hard. They fail because tool outputs, file contents, and conversation history keep piling up until the model can no longer see the right context. Deep Agents addresses that with a two-step strategy: offload large tool I/O first, then summarize history when the context window is pressured.

In interviews, this is the clean explanation for how Deep Agents keeps multi-step work alive without asking the user to manually trim context.

## Mental Model
Use this ordering:

1. offload oversized tool inputs and results to the filesystem
2. keep recent context live while replacing bulky older material with pointers
3. summarize older history only when needed
4. let the agent re-open offloaded artifacts later through the same file tools

The key point is that summarization is not the first move. Deep Agents tries to preserve retrievability through files before compressing the conversation itself.

## Architecture / Flow
```text
tool call or tool result grows large
  -> if size > offload threshold
     -> write content to backend
     -> replace in active context with file pointer + preview

session nears context limit
  -> truncate older redundant write/edit tool payloads
  -> if pressure remains
     -> SummarizationMiddleware runs
     -> create structured summary for working memory
     -> write canonical conversation text to filesystem

later
  -> agent can read_file / grep offloaded artifacts or preserved history
```

If you want the model to compact proactively instead of waiting for the automatic trigger, add the `compact_conversation` tool with `create_summarization_tool_middleware(...)`.

## Key Concepts
- Automatic context compression is built into every `create_deep_agent(...)` call. You do not need to wire custom middleware just to get the default offloading and summarization behavior.

- Offloading uses the built-in filesystem tools. That is why the backend matters even for "context management" topics.

- Tool call inputs are special. Large `write_file` and `edit_file` payloads are already persisted to disk, so keeping their full contents forever in conversation history is often redundant.

- Tool call results above the threshold are offloaded to the backend and replaced with a file path reference plus a short preview, so the agent can reopen them later if necessary.

- Summarization is handled by `SummarizationMiddleware`. The active context keeps a model-generated summary plus recent messages, while the full historical text is preserved in the filesystem.

- The docs call out a fallback path: if a model call raises `ContextOverflowError`, Deep Agents immediately summarizes and retries with the summary plus recent preserved messages.

- Streaming UIs may see summarization tokens. The docs show filtering them by checking `metadata.get("lc_source") == "summarization"`.

- Compression is text-oriented. The docs explicitly say built-in compression does not resize images, reduce image resolution, or generate visual embeddings.

## Metrics and Formulas to Memorize
- Offloading threshold: `20,000` tokens
- Automatic summarization trigger: about `85%` of the model's `max_input_tokens`
- Recent-context keep budget after summarization: about `10%` of tokens
- Fallback when model profile is unavailable:
  - trigger at `170,000` tokens
  - keep `6` recent messages
- Escape hatch API: `compact_conversation`
- The docs give concrete thresholds here, but they do not provide universal quality scores for how much answer fidelity changes after compression

## Trade-offs and Failure Modes
- Offloading only works well if the backend stays readable and the agent can reopen the artifacts later. Poor backend choices turn "compression" into accidental data loss.

- Summaries are smaller than history because they discard detail. The canonical preserved conversation file is the recovery path when details matter later.

- If you store internal artifacts on a real project filesystem, offloaded results and preserved conversation history can clutter the working tree.

- Multimodal data is a weak spot for built-in compression. The docs are clear that images are not automatically made cheaper or more searchable.

- Waiting too long to compact can cause emergency retries. Proactive compaction with `compact_conversation` is useful between major task phases.

## Interview Q&A
**Q: What happens first in Deep Agents when context grows too large?**  
A: Offloading. Large tool inputs and results are moved to the filesystem before the system falls back to summarizing message history.

**Q: What is the offloading threshold?**  
A: `20,000` tokens for oversized tool inputs or results.

**Q: When does summarization trigger?**  
A: Around `85%` of the model's `max_input_tokens`, or at a fallback `170,000`-token threshold with `6` recent messages kept if no model profile is available.

**Q: What does summarization preserve?**  
A: A structured in-context summary for working memory and a canonical text rendering of the original conversation in the filesystem.

**Q: What is `compact_conversation` for?**  
A: It lets the agent trigger compaction on demand instead of waiting for the automatic threshold.

**Q: Does this system handle multimodal compression automatically?**  
A: No. The docs explicitly say it does not resize images or create visual embeddings.

## Sources
- [Context engineering in Deep Agents](https://docs.langchain.com/oss/python/deepagents/context-engineering.md)
- [Customize Deep Agents](https://docs.langchain.com/oss/python/deepagents/customization.md)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
