# Model Context Protocol

## Why It Matters
MCP is a protocol contract for interoperable tools, resources, and prompts, not just a plugin catalog. The model does not speak MCP directly; the host runtime does. That is why good interview answers explain host/client/server roles, control boundaries, and security assumptions instead of saying "it is tool calling with standards."

The current spec is also explicitly stateless and self-describing. That matters for scaling, multi-tenant HTTP servers, and long-running work, because session assumptions moved out of the transport and into explicit handles or extensions like Tasks.

## Mental Model
Think of MCP as a three-role adapter layer:
- host: the app the user sees
- client: the protocol connector inside the host
- server: the process exposing tools, resources, and prompts

And think of its three primitives by who controls them:
- tools are model-controlled
- resources are app-controlled
- prompts are user-controlled

The model never sends JSON-RPC itself. It emits a native tool selection, and the host's MCP client translates that into protocol calls.

## Architecture / Flow
```text
host/app -> MCP client -> server/discover
         -> tools/list | resources/list | prompts/list

model chooses tool -> tools/call
app chooses resource -> resources/read
user chooses prompt -> prompts/get

result -> complete | input_required | task
```

Transport choice follows deployment shape:
- `stdio` for local subprocess tools
- Streamable HTTP for remote, multi-tenant servers

## Key Concepts
- Three roles:
  - host owns UX, consent, conversation, and policy
  - client speaks MCP to one server
  - server exposes primitives and may be local or remote
- Three primitives:
  - tools are actions the model may invoke
  - resources are URI-addressed context the app decides to attach
  - prompts are reusable templates the user chooses
- JSON-RPC 2.0:
  - request, response, and notification model
  - protocol errors are not the same thing as business-level tool failures
- Stateless core:
  - the 2026-07-28 spec removed protocol-level sessions and the old handshake
  - each request carries self-description in `_meta`
  - cross-call state now belongs in explicit handles, not hidden transport session state
- Capability negotiation:
  - `server/discover` advertises supported versions and capabilities
  - `_meta` carries protocol version, client capabilities, and related metadata
- Transports:
  - `stdio` is best for local tools with low overhead
  - Streamable HTTP is the intended path for remote, horizontally scaled servers
- HTTP auth model:
  - OAuth 2.1
  - RFC 9728 Protected Resource Metadata
  - RFC 8707 resource indicators
  - PKCE for public clients
- MRTR:
  - `resultType: "input_required"` is the modern way for a server to request more input
  - this replaces older server-initiated elicitation and sampling patterns
- Tasks extension:
  - long-running work should return a task handle and use polling like `tasks/get` and `tasks/update`
  - do not hold open one request forever for CI or batch-style work
- Security model:
  - tool poisoning, rug-pull catalogs, confused deputy, token passthrough, and shadowing are real protocol-era risks
  - hash pinning, drift detection, deterministic ordering, and approval UX matter
- Comparison lens:
  - native function calling is smallest and fastest for fixed toolsets
  - OpenAPI is the mature contract for HTTP services
  - A2A is agent-to-agent, not agent-to-tool

## Metrics and Formulas to Memorize
- `3` roles: host, client, server
- `3` core primitives: tools, resources, prompts
- `2` standard transports: `stdio` and Streamable HTTP
- Tool-schema overhead from field measurements in local material: roughly `550-1,400` tokens per tool
- Local token-tax anchor: GitHub MCP alone (`35` tools) can cost about `~26,000` prompt tokens
- Local token-tax anchor: a `3`-server configuration can reach `~143,000` tool-schema tokens
- Practical ceiling from local material: keep always-loaded tools around `~30-40` before progressive discovery becomes important
- `2026-07-28` is the key stateless-spec revision:
  - protocol-level sessions removed
  - `server/discover` added
  - Roots, Sampling, Logging, and legacy HTTP+SSE are deprecated with at least a `12`-month removal window
- Local optimization anchors:
  - progressive discovery or tool search preserved about `85%` of context
  - Code Mode reduced tool-schema footprint by `99.9%`
- Official specs are precise on semantics but light on universal latency benchmarks; measure real transports locally

## Trade-offs and Failure Modes
- Treating MCP as "tool calling only" and ignoring resources, prompts, and extension negotiation
- Designing a server that implicitly depends on sticky sessions in a stateless protocol era
- Dumping huge tool catalogs into the prompt and destroying context budget
- Passing inbound tokens straight through to downstream systems instead of doing audience-bound auth
- Trusting tool descriptions or catalogs as benign text and missing poisoning or rug-pull attacks
- Building new systems on deprecated HTTP+SSE or other legacy patterns when Streamable HTTP is the intended path
- Forgetting deterministic tool ordering, cache hints, or catalog-drift detection, leading to prompt-cache churn and inconsistent behavior

## Interview Q&A
**Q: What is MCP in one sentence?**  
A: A standard protocol that lets hosts connect models to tools, resources, and prompts through a self-describing client/server contract.

**Q: What are the three roles?**  
A: Host, client, and server. The host owns the user experience, the client speaks the protocol, and the server exposes capabilities.

**Q: What are the three primitives and who controls them?**  
A: Tools are model-controlled, resources are app-controlled, and prompts are user-controlled.

**Q: Why does it matter that the model does not speak MCP directly?**  
A: Because policy, approval, identity, and routing live in the host runtime. MCP is infrastructure plumbing, not a behavior the model owns.

**Q: What changed in the 2026-07-28 spec?**  
A: MCP became stateless at the protocol level, added `server/discover`, moved long-lived work toward Tasks, and deprecated older patterns like protocol sessions and HTTP+SSE.

**Q: When do you use `stdio` vs Streamable HTTP?**  
A: `stdio` for local subprocess tools inside an IDE or desktop app. Streamable HTTP for remote or multi-tenant servers that need standard auth and horizontal scaling.

**Q: What are MRTR and Tasks?**  
A: MRTR is the `input_required` pattern for multi-round requests. Tasks is the extension for durable, pollable long-running work.

**Q: MCP vs native function calling vs OpenAPI vs A2A?**  
A: Native function calling is best for a small fixed toolset, OpenAPI is the mature HTTP contract, MCP is the reusable interoperability layer for tools/resources/prompts, and A2A is for agent-to-agent delegation.

**Q: Biggest MCP security mistake?**  
A: Treating tool catalogs and descriptions as trusted text, then combining that with over-privileged tokens or giant always-loaded tool surfaces.

## Sources
- Local anchors:
  - `ai-roadmap/interview-prep-gpt/10-tools-and-mcp.md`
  - `ai-roadmap/interview-prep-gpt/07-guardrails.md`
  - `ai-roadmap/interview-prep-gpt/05-observability.md`
  - `ai-roadmap/final/ai-concepts/10-mcp-interoperability.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [MCP specification index](https://modelcontextprotocol.io/specification/draft/index)
  - [MCP 2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
  - [MCP transport overview](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
  - [MCP Streamable HTTP transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
  - [MCP authorization spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index)
  - [MCP auth tutorial](https://modelcontextprotocol.io/docs/tutorials/security/authorization)
  - [MCP Tasks extension overview](https://modelcontextprotocol.io/extensions/tasks/overview.md)
  - [MCP 2026-07-28 spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
