# Research: How MCP (Model Context Protocol) Works

**Date researched**: 2026-09-29
**Sources consulted**: 22

## 1. System Topology & Mechanics

### Architecture: Host-Client-Server

MCP follows a **client-host-server** architecture inspired by the Language Server Protocol (LSP). Each layer has a distinct role:

- **Host**: The user-facing application (Claude Desktop, an IDE like VS Code/Cursor, or a custom AI agent). The host interprets user prompts, decides when external data/tools are needed, and creates/manages multiple MCP client instances -- one per connected MCP server. The host is the trust boundary: it must obtain user consent before exposing data or invoking tools.

- **Client**: A protocol-speaking connection manager that lives inside the host. Each client maintains a **dedicated 1:1 connection** with a single MCP server. It translates abstract AI requests into concrete MCP messages (`tools/call`, `resources/read`, etc.), handles capability negotiation at connection start, and manages the full session lifecycle including reconnections.

- **Server**: Bridges between the MCP protocol and real-world systems (databases, SaaS APIs, file systems). Servers translate MCP requests into native operations (e.g., `resources/read` becomes a SQL `SELECT`). They advertise their capabilities upon connection. Servers can run locally (subprocess on user's machine) or remotely (cloud-hosted service).

**Key invariant**: Clients and servers have a 1:1 relationship. A single host can run N clients, each connected to one server. This enables clear security boundaries and isolation between integrations.

### Transport Layer

MCP defines two standard transports. The wire format is always **JSON-RPC 2.0**, UTF-8 encoded. The protocol is transport-agnostic -- custom transports are permitted if they preserve JSON-RPC message format and lifecycle requirements.

#### stdio Transport
- Client launches the server as a **subprocess**.
- Server reads JSON-RPC from `stdin`, writes responses to `stdout`, may log to `stderr`.
- Messages are **newline-delimited**; embedded newlines are forbidden.
- Network overhead is effectively 0ms -- pure IPC, fastest option for same-machine communication.
- One client per process; no built-in auth (relies on OS-level process isolation).
- Use case: local developer tools, CLI integrations, desktop AI clients.
- Cold start: TypeScript (Node.js) starts ~80ms faster than Python for stdio servers.

#### Streamable HTTP Transport (current standard for remote)
Introduced in spec 2025-03-26, replacing the deprecated HTTP+SSE transport.

- **Single HTTP endpoint** (e.g., `https://example.com/mcp`) accepting both POST and GET.
- **POST** (client to server): Body is a single JSON-RPC request/notification/response. Client must include `Accept: application/json, text/event-stream`. Server responds with either `application/json` (short calls) or `text/event-stream` (streaming via SSE).
- **GET** (server-initiated stream): Client opens an SSE stream for server-to-client messages without first sending data.
- **Session management**: Server may assign a session ID via `Mcp-Session-Id` header on the `InitializeResult` response. Client must include this header on all subsequent requests. Sessions are terminated via HTTP DELETE.
- **Resumability**: Servers may attach `id` fields to SSE events (globally unique within a session). On disconnection, clients reconnect with `Last-Event-ID` header for message replay. Server may also send a `retry` field (in ms) before closing connections.
- **Performance**: ~10ms latency per call under load. Sustained ~290-300 RPS at 100% success rate in shared-session deployments.
- **Security requirements**: Servers MUST validate `Origin` header (prevent DNS rebinding); should bind to localhost when local; should implement proper authentication.
- **Protocol version header**: Client MUST include `MCP-Protocol-Version: <version>` on all HTTP requests after initialization.

#### HTTP+SSE Transport (deprecated)
- Required two separate endpoints (GET for SSE stream, POST for messages).
- No resumable streams -- dropped connections meant lost messages.
- Long-lived per-client SSE connections were expensive and incompatible with stateless infrastructure.
- Throughput collapsed to 7-30 RPS under sustained load with response times climbing to hundreds of milliseconds.
- SSE was officially deprecated in spec 2025-03-26. Backwards compatibility path exists: clients try POST first; on 400/404/405, fall back to GET expecting SSE endpoint event.

### Message Protocol: JSON-RPC 2.0

All MCP communication uses JSON-RPC 2.0 as the wire format. Three message types:

1. **Requests**: Expect a response. Include `jsonrpc`, `id`, `method`, and optional `params`.
2. **Responses**: Pair with a request via matching `id`. Contain either `result` or `error`.
3. **Notifications**: Fire-and-forget, no `id` field, no response expected.

Error codes follow JSON-RPC conventions:
- `-32602`: Invalid params (unknown tool, missing arguments)
- `-32603`: Internal error
- `-32002`: Resource not found (MCP-specific)
- `-1`: User rejected request (MCP-specific, for sampling)

### Capability Negotiation and Lifecycle

Connection lifecycle follows a strict initialization handshake (spec versions through 2025-11-25):

1. Client sends `initialize` request with its capabilities and supported protocol version.
2. Server responds with its capabilities, supported protocol version, and server info.
3. Client sends `notifications/initialized` notification.
4. Session is active -- both sides can now exchange requests and notifications.

**Version negotiation**: Client and server agree on the highest mutually supported protocol version. Server responds with the version it will use.

**Server capabilities declared during init**:
- `tools` (with `listChanged` flag)
- `resources` (with `subscribe` and `listChanged` flags)
- `prompts` (with `listChanged` flag)

**Client capabilities declared during init**:
- `sampling` (with optional `tools` and `context` sub-capabilities)
- `roots` (server can query filesystem boundaries)
- `elicitation` (server can request information from users)

**2026-07-28 spec change**: The `initialize`/`initialized` handshake and `Mcp-Session-Id` were **removed**. Each request is now self-describing, carrying protocol version, client identity, and capabilities in `_meta`. An optional `server/discover` RPC replaces the init-time capability exchange. This makes MCP stateless at the protocol level -- any server instance behind a round-robin load balancer can handle any request.

### Primitives

MCP defines four core primitives (plus additional utilities):

#### 1. Resources (Application-Controlled)
- Provide **read-only data** as context to the AI model.
- Identified by URIs (RFC 3986). Common schemes: `file://`, `https://`, `git://`, custom.
- Support URI templates (RFC 6570) for parameterized resources.
- Content can be **text** (UTF-8 string) or **binary** (base64-encoded blob), with MIME type.
- Operations: `resources/list` (paginated), `resources/read`, `resources/templates/list`.
- Subscriptions: Clients can subscribe to individual resources (`resources/subscribe`); servers emit `notifications/resources/updated` on change.
- Annotations: `audience` (["user", "assistant"]), `priority` (0.0-1.0), `lastModified` (ISO 8601).
- Resources are **application-driven**: the host decides how to incorporate them (UI selection, search, automatic inclusion).

#### 2. Tools (Model-Controlled)
- Enable LLMs to **execute actions** via external systems (query DB, call API, run computation).
- Each tool has: `name` (unique, 1-128 chars, case-sensitive), `description`, `inputSchema` (JSON Schema, defaults to 2020-12), optional `outputSchema`, optional `annotations`, optional `execution` config.
- Operations: `tools/list` (paginated), `tools/call`.
- **Two error types**: Protocol errors (JSON-RPC errors for malformed requests) and Tool execution errors (returned in result with `isError: true`, actionable for LLM self-correction).
- Tool results can contain: text, images (base64), audio (base64), resource links, embedded resources, or structured JSON (`structuredContent` field validated against `outputSchema`).
- **Tool annotations**: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` -- all untrusted unless from a trusted server.
- Dynamic discovery: `notifications/tools/list_changed` notifies clients when available tools change.
- Security: Servers MUST validate inputs, implement access controls, rate limit, sanitize outputs. Clients SHOULD prompt for user confirmation, show tool inputs before calling, implement timeouts.

#### 3. Prompts (User-Controlled)
- Reusable, parameterized instruction templates exposed by servers.
- Designed for **explicit user selection** (e.g., slash commands in UI).
- Each prompt has: `name`, `description`, `arguments` (with required flag).
- Operations: `prompts/list` (paginated), `prompts/get` (returns array of `PromptMessage`).
- Messages contain `role` (user/assistant) and content (text, image, audio, or embedded resource).
- Support auto-completion of arguments via the completion API.

#### 4. Sampling (Server-Initiated LLM Calls) [deprecated in 2026-07-28]
- Allows servers to request LLM completions ("generations") from the **client**, without needing their own API keys.
- Server sends `sampling/createMessage` with messages, optional model preferences, system prompt, and max tokens.
- **Model preferences**: Abstract capability priorities (`costPriority`, `speedPriority`, `intelligencePriority`, each 0-1) plus optional `hints` (substring-matched model names). Client makes final model selection.
- **Human-in-the-loop**: Applications SHOULD present sampling requests for user approval before sending, and present responses for review before delivery.
- **Tool use in sampling**: Servers can include a `tools` array and `toolChoice` config. Supports multi-turn tool loops where the LLM calls tools, server executes them, and results are fed back.
- Cross-API compatible with Claude, OpenAI, and Gemini providers.
- **Deprecated** in spec 2026-07-28 (SEP-2577) with 12-month minimum support window. Replaced by Multi Round-Trip Requests (MRTR) pattern.

#### Additional Utilities
- **Progress tracking**: Servers can report progress on long-running operations.
- **Cancellation**: Clients send `notifications/cancelled` to cancel in-flight requests.
- **Logging**: Servers can emit log messages (deprecated in 2026-07-28).
- **Elicitation**: Servers request additional information from users (added in 2025-11-25).
- **Tasks**: Track work being performed by servers; moved to `io.modelcontextprotocol/tasks` extension in 2026-07-28 with poll-based `tasks/get` and `tasks/update`.

## 2. Token Economics & NFR Metrics

### Token Cost of MCP

The "token tax" is the most significant production concern with MCP. Tool schemas are injected into the LLM's context window, consuming tokens before any user interaction occurs.

**Measured overhead**:
- A simple directory listing through MCP used **12x more tokens** than a hard-coded Python function.
- Across 9 state-of-the-art LLMs, prompt-to-completion token inflation ranged from **2x to 30x** vs. baseline chat.
- 7 MCP servers consumed 67,300 tokens (33.7% of a 200k context window) before any conversation.
- A full MCP setup consumed 143k of 200k tokens (72% usage), with tool metadata consuming 82k tokens.
- MCP tool definitions consume **5-15x more tokens** than the simplest possible schema.
- With 20-30 registered MCP tools, tool schemas alone occupy **15-30 KB** of context before a single user message.
- Each tool consumes approximately **1,000 tokens** of context per session (GitHub issue #2808 in the MCP spec repo).

**Dollar costs (production data, 22 days, 2,600 conversations)**:
- Per-conversation schema cost (first turn, no cache): ~10,000 tokens x $15/1M (Opus input) = **$0.15/conversation**.
- With 75% prompt cache hit rate: effective cost dropped to ~**$0.04/conversation**.
- Total first-turn schema cost across 2,600 conversations: ~$390 without cache, ~$100 with cache.

**Reasoning capacity cost**: With 20 MCP tools registered, the model has ~10,000 fewer tokens available for actual reasoning.

### Latency Overhead

- Each MCP tool call adds **50-200ms** of latency in production.
- In a typical session, the model invokes 5-15 tools per task. At 300ms per call, that is **1.5-4.5 seconds** of waiting.
- Protocol overhead (session negotiation, capability exchange, JSON-RPC framing) adds latency that a direct HTTP call avoids.
- Gateway layer overhead: Bifrost benchmarks at **11 microseconds** of overhead at 5,000 RPS.
- Streamable HTTP achieves ~10ms per call under load. stdio is effectively 0ms (pure IPC).

### MCP vs. Direct API/CLI

- CLI completed browser automation tasks with **33% better token efficiency** and a 77 vs. 60 point task completion score compared to MCP.
- The gap was largest in multi-step debugging workflows where context budget ran out mid-task with MCP but not with CLI.
- Perplexity CTO publicly moved away from MCP toward traditional APIs and CLI tools (March 2026).

### Optimization Strategies

- **Context trimming + KV-cache reuse + parallel execution + progressive disclosure**: up to **91% token savings** in production.
- **Code Mode** (Bifrost): up to **92.8% lower input tokens** at 500+ tools while maintaining 100% pass rate. Execution ~40% faster with large tool catalogs due to 3-4x fewer LLM round trips.
- **Tiered schema detail** (proposed protocol-level): Discovery tier (name + one-line description + param names/types only) and Invocation tier (full schema injected on-demand when model selects the tool).
- **Just-in-time MCP discovery**: Only fetch schemas from servers relevant to the current task, reducing context overhead by ~60%.
- **2026-07-28 spec**: Cacheable list results (`ttlMs`, `cacheScope`) on `tools/list`, `prompts/list`, `resources/list`, `resources/read` -- reducing unnecessary re-fetching. Deterministic ordering enables stable prompt caches across reconnects.

## 3. Distributed Resilience & State

### Connection Lifecycle Management

**Pre-2026-07-28 (stateful)**:
- Sessions begin with `initialize`/`initialized` handshake.
- Server assigns `Mcp-Session-Id` header, client includes it on all subsequent requests.
- Session terminates via HTTP DELETE or server-initiated 404 response.
- If client receives 404, it must start a new session with a fresh `InitializeRequest`.

**2026-07-28 (stateless)**:
- No sessions, no initialization handshake. Each request is self-describing.
- Protocol version, client identity, and capabilities embedded in `_meta` within the JSON-RPC body.
- Any server instance can handle any request behind a round-robin load balancer.
- If cross-call state is needed, servers "mint an explicit handle from a tool and have the model pass it back as an argument" -- the model can see the handle and thread it between tools.

### Reconnection Strategies

**Streamable HTTP resumability**:
- Servers attach globally unique `id` fields to SSE events within a session.
- On disconnection, clients issue HTTP GET with `Last-Event-ID` header.
- Server replays messages that would have been sent after the last event on that specific stream.
- Server may send `retry` field (ms) before closing connections; clients MUST respect it.
- Disconnection SHOULD NOT be interpreted as cancellation. To cancel, clients explicitly send `CancelledNotification`.

**stdio recovery**: No built-in reconnection. If the subprocess dies, the client must relaunch it and re-initialize.

### Server Discovery and Failover

- Pre-2026: No standard discovery mechanism -- integrations required explicit configuration.
- 2026 roadmap priority: Progressive discovery so servers can offer a small entry point and reveal more of their catalog as the conversation narrows.
- The 2026-07-28 spec introduces `server/discover` as an optional RPC for clients wanting server capabilities upfront.
- **MCP gateway pattern**: Centralized discovery and routing across multiple MCP servers. Gateways maintain a registry of available servers and their capabilities.
- Failover is not standardized in the protocol. In practice, it is handled at the infrastructure layer (load balancers, Kubernetes health checks, gateway circuit breakers).

## 4. Enterprise Security & Governance

### Authentication Standards

- **OAuth 2.1** was formalized as the authentication standard for remote MCP servers in the November 2025 specification.
- Mandatory **PKCE S256** for authorization code flow.
- **RFC 9728** for metadata discovery and **RFC 8707** for resource indicators.
- Token passthrough is an explicit **anti-pattern**: MCP servers MUST NOT accept tokens not issued specifically for that MCP server.
- **2026-07-28 changes**: RFC 9207 issuer validation (clients must validate `iss` before redeeming codes), `application_type` in DCR (prevents rejection of localhost redirects for desktop/CLI apps), credential binding to issuer (reuse across authorization servers forbidden).
- **Dynamic Client Registration (DCR)** formally deprecated in favor of **Client ID Metadata Documents (CIMD)** in 2026-07-28.

### Transport Security

- Servers MUST validate `Origin` header on all incoming Streamable HTTP connections (prevent DNS rebinding). Invalid Origin returns 403 Forbidden.
- Local servers SHOULD bind only to localhost (127.0.0.1), not 0.0.0.0.
- `Mcp-Session-Id` MUST be globally unique and cryptographically secure (e.g., UUID, JWT, cryptographic hash).
- Clients must handle session IDs securely to prevent session hijacking.

### Tool-Level Permissions and Capability Scoping

- Tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) provide metadata but MUST be considered untrusted unless from a trusted server.
- Hosts MUST obtain explicit user consent before invoking any tool.
- Gateway pattern enables per-consumer tool allow-lists, deny-lists, and role-based filtering.
- Enterprise gateways provide per-user authentication to upstream servers (identity propagation through the gateway).

### Human-in-the-Loop

The protocol mandates human oversight at multiple points:
- Users must explicitly consent to data access and operations.
- Hosts must prompt for confirmation before invoking tools (especially destructive ones).
- Sampling requests should be presented for user approval; users should control whether sampling occurs, the actual prompt sent, and what results the server sees.
- Applications should provide clear UI showing which tools are exposed, with visual indicators when tools are invoked.

### Audit and Governance

- Enterprise gateways provide audit trails for every tool suggestion, approval, and execution.
- **W3C Trace Context** propagation: `traceparent`, `tracestate`, and `baggage` keys documented in `_meta` (SEP-414 in 2026-07-28) for distributed trace correlation across SDKs and gateways.
- NSA/DoD published joint MCP Security Design guidance (May 2026).
- OWASP MCP Top 10 project established the first industry-standard framework for classifying MCP risks.
- Cloud Security Alliance published the Agentic MCP Security Best Practices Guide.
- Phased enterprise implementation: inventory + basic audit logging + secret management within 30 days; gateway architecture + approval processes within 90 days; complete identification pipelines + governance frameworks within 180 days.

### Known Security Threats

1. **Tool Poisoning**: Malicious instructions embedded in tool metadata/descriptions. Appear benign to users but execute when AI agents read metadata. Persist across sessions.
2. **Confused Deputy**: Proxy server attack where a malicious client obtains a victim's authorization through what appears to be a legitimate consent screen.
3. **Supply Chain Attacks**: Compromised npm packages impersonating legitimate MCP servers (documented: fake Postmark email MCP server, September 2025, 15 clean releases before adding exfiltration code).
4. **Prompt Injection via Tool Results**: MCP tool results injected back into LLM context can manipulate agent behavior if not sanitized.

### Governance

- Anthropic donated MCP to the **Agentic AI Foundation** (December 2025), a directed fund under the Linux Foundation co-founded by Anthropic, Block, and OpenAI, with backing from Google, Microsoft, AWS, Cloudflare, and Bloomberg.
- Specification Enhancement Proposals (SEPs) are the formal mechanism for protocol changes.
- Formal deprecation policy: 12-month minimum window for deprecated features.
- 2026-07-28 introduced a formal extensions framework: extensions identified by reverse-DNS IDs, negotiated through capabilities, versioned independently of the core spec.

## 5. Production Failure Modes

### Taxonomy

Academic research analyzed **833 confirmed runtime fault threads** from 473 actively maintained MCP server GitHub repositories, producing a taxonomy of **11 top-level categories and 27 subcategories (73 leaf fault types)**. In a survey of 55 MCP server developers, respondents experienced an average of **20 of 27** fault subcategories.

### Five Repeatable Production Failure Modes

1. **Transport flakiness under load**: Streamable HTTP connections are long-lived with per-session state, but load balancers terminate them at 60 seconds. Health checks pass while agent sessions silently break.

2. **Tool description drift**: Tool schemas change on the server side without notification to connected clients, causing argument mismatches and silent failures.

3. **Schema mismatches across SDK versions**: Different SDK versions produce subtly different JSON Schema interpretations, causing validation failures.

4. **OAuth refresh storms**: Hundreds of agent sessions hit token expiry within the same minute, overwhelming the IdP with refresh requests. Fix: jittered refresh at 80% of token lifetime with up to 10% random offset, plus single-flight lock.

5. **Silent JSON-RPC hangs**: Server crashes mid-handler, JSON-RPC `id` never gets a response, client waits forever. The protocol has no built-in timeout. Fix: per-call timeout on every JSON-RPC request id, circuit breaker per tool, child-process supervisor.

### Context Overflow

- Every MCP tool call adds to prompt context. With 10 servers each returning 500-2,000 tokens, 20,000 tokens are consumed by tool context alone before the actual prompt.
- LLMs exhibit attention decay on longer contexts, degrading response quality.
- Context length limit faults cause incomplete outputs, repeated restarts, or stalled execution.

### Cascading Failures

- LLM agents retry with slightly different parameters (unlike traditional APIs that fail fast), creating cascading failures that appear as success until downstream data is checked.
- Each retry hits the MCP server, which propagates to the already-struggling tool provider.
- Fix: per-session retry budgets with proper backoff. After N failures of the same capability within a time window, circuit-break and surface failure to the orchestration layer. Never implement retry logic in the model prompt.

### Latency Compounding

- MCP round-trips add 50-200ms per tool call. In agentic workflows with 10+ tool calls, this compounds to seconds.
- Long-running MCP tools can exceed the agent's per-step latency budget, causing planner timeouts.

### Missing Protocol-Level Primitives

Three primitives remain absent from the protocol: **identity propagation**, **adaptive tool budgeting**, and **structured error semantics**. These gaps force every production deployment to reinvent these capabilities.

## 6. Enterprise System Design Scenarios

### Multi-Server MCP Architectures

Real enterprise deployments connect dozens of AI agents to hundreds of MCP servers. As of 2026, enterprise MCP adoption exceeds **78% in production AI teams**, with the public registry surpassing **9,400 servers**. Tier 1 SDKs (TypeScript and Python) each crossed **1 billion total downloads**, with combined downloads at ~500 million/month.

Four enterprise topologies cover the majority of deployments:
1. **Single-tenant**: Isolated internal teams, one gateway per team.
2. **Multi-tenant row-isolated**: SaaS-style multi-customer MCPs with data isolation.
3. **Federated gateway**: Large estates with central audit requirements, multiple regional gateways reporting to a central governance plane.
4. **Edge-cached read-only**: High-RPS tool discovery where `tools/list` responses are stable and safe to cache.

### MCP Gateway/Proxy Patterns

An MCP gateway sits between AI agents and MCP servers, functioning as a **control plane** above the **execution plane** (MCP servers). The execution plane connects to GitHub, Postgres, Slack, internal APIs. The control plane decides which agent sees which tools, under which identity, with what constraints.

**Gateway vs. Proxy vs. Server**:
- Server: exposes tools from one system.
- Proxy: forwards MCP traffic to one server with minimal policy.
- Gateway: aggregates multiple servers through one endpoint; adds auth, tool filtering, logging, guardrails, budgets, audit-ready logs.

**42 gateways on the market** (as of 2026) in three categories:
1. **Cloud-Native / Kubernetes**: Run as pods, scale horizontally, multi-tenant governance (Microsoft MCP Gateway, Envoy AI Gateway).
2. **Developer-First / Desktop**: Binary or CLI plugin for individual developers (MCPProxy).
3. **Commercial SaaS**: Managed offerings (AWS AgentCore Gateway, Cloudflare).

**Key gateway players**:
- **Microsoft MCP Gateway**: Session-aware stateful routing in Kubernetes.
- **Kong AI Gateway**: Protocol bridge converting API schemas into MCP tool definitions.
- **Envoy AI Gateway**: Token-encoding architecture eliminates need for Redis/DB for session management.
- **Bifrost**: Open-source, 6 upstream auth types, per-virtual-key tool filtering, Code Mode for token reduction, 11 microseconds overhead.
- **AWS AgentCore Gateway**: Fully managed single entry point for agent traffic.
- **Cloudflare**: Edge-native reference architecture combining AI Gateway, MCP Server Portals, and Cloudflare Gateway.

**Essential gateway capabilities**: per-consumer tool scoping, per-user auth to upstream servers, governance of LLM calls that trigger tool calls, deployment control, cost behavior as tool counts grow, threat protection (tool poisoning, rug-pulls, shadow MCP usage).

### Trade-off: MCP vs. Custom Tool Integration

| Dimension | MCP | Custom/Direct API |
|---|---|---|
| **Discovery** | Dynamic: server advertises capabilities at runtime; new tools discovered automatically | Static: hard-coded at build time, requires code changes for new tools |
| **Token cost** | 2-30x overhead from schema injection; 5-15x more tokens per tool than minimal schema | Minimal: developer hand-picks necessary parameters |
| **Latency** | 50-200ms per tool call + protocol overhead | Direct HTTP call, no intermediary framing |
| **Interoperability** | Write once, connect to any MCP-compatible host | Each integration is bespoke |
| **Security** | Standardized OAuth 2.1 + PKCE, but broad attack surface (tool poisoning, supply chain) | Application-specific security; smaller but inconsistent attack surface |
| **Maintenance** | Protocol handles versioning, capability negotiation | Every integration reinvents these |
| **Scalability** | Gateway pattern enables centralized governance at scale | Each integration scaled independently |
| **Best for** | Dynamic tool sets, multi-agent systems, platform teams, unknown future integrations | Known, fixed tool sets, latency-critical paths, simple architectures |

**Decision heuristic**: Use MCP when tool sets are dynamic, multiple AI hosts need the same integrations, or governance/audit requirements exist. Use direct APIs for known, fixed tool sets where latency and token efficiency matter, or where the overhead of MCP's dynamic discovery provides no value.

### Spec Evolution Timeline

| Date | Version | Key Change |
|---|---|---|
| Nov 2024 | 2024-11-05 | Initial release. HTTP+SSE transport. |
| Mar 2025 | 2025-03-26 | Streamable HTTP replaces SSE. stdio formalized. |
| Nov 2025 | 2025-11-25 | OAuth 2.1 auth. Tasks. Elicitation. Structured output. Audio content. Tool annotations. One-year anniversary release. |
| Jul 2026 | 2026-07-28 | **Stateless protocol core**. Sessions removed. MRTR replaces sampling/elicitation/roots. Header-based routing (`Mcp-Method`, `Mcp-Name`). Cacheable list results. Extensions framework. Roots/Sampling/Logging deprecated. CIMD replaces DCR. |

## Sources

- [1] https://newsletter.systemdesign.one/p/how-mcp-works -- SystemDesign.one MCP architecture overview
- [2] https://modelcontextprotocol.io/specification/2025-11-25 -- Official MCP spec (Nov 2025)
- [3] https://modelcontextprotocol.io/specification/2025-11-25/basic/transports -- MCP transport specification
- [4] https://modelcontextprotocol.io/specification/2025-11-25/server/tools -- MCP tools primitive spec
- [5] https://modelcontextprotocol.io/specification/2025-11-25/server/resources -- MCP resources primitive spec
- [6] https://modelcontextprotocol.io/specification/2025-11-25/server/prompts -- MCP prompts primitive spec
- [7] https://modelcontextprotocol.io/specification/2025-11-25/client/sampling -- MCP sampling primitive spec
- [8] https://spec.modelcontextprotocol.io/specification/2025-03-26/architecture/ -- MCP architecture spec (Mar 2025)
- [9] https://blog.modelcontextprotocol.io/posts/2026-07-28/ -- 2026-07-28 spec release blog
- [10] https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/ -- 2026 MCP roadmap
- [11] https://gingerlabs.ai/blog/mcp-transport-comparison -- Transport comparison with benchmarks
- [12] https://explore.n1n.ai/blog/hidden-costs-model-context-protocol-mcp-2026-06-24 -- Hidden costs of MCP at scale
- [13] https://github.com/modelcontextprotocol/modelcontextprotocol/issues/2808 -- Tool schema token overhead issue
- [14] https://arxiv.org/html/2602.14878v1 -- MCP tool description token efficiency paper
- [15] https://arxiv.org/html/2511.07426v1 -- Network performance characterization of MCP agents
- [16] https://media.defense.gov/2026/Jun/02/2003943289/-1/-1/0/CSI_MCP_SECURITY.PDF -- NSA/DoD MCP Security Design guidance
- [17] https://labs.cloudsecurityalliance.org/agentic/agentic-mcp-security-best-practices-v1/ -- CSA MCP Security Best Practices
- [18] https://mcpproxy.app/blog/2026-03-15-mcp-gateway-landscape/ -- MCP gateway landscape 2026
- [19] https://arxiv.org/html/2603.05637v1 -- Taxonomy of runtime faults in MCP servers (833 fault threads)
- [20] https://archestra.ai/blog/mcp-in-production-what-breaks -- MCP production failure modes
- [21] https://dev.to/mrclaw207/97m-mcp-downloads-and-still-no-production-playbook-what-i-learned-the-hard-way-100j -- Production playbook learnings
- [22] https://chatforest.com/guides/mcp-gateway-proxy-patterns/ -- Gateway and proxy patterns guide
