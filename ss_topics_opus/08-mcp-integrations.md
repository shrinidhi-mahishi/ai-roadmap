# Module 08: MCP & Agent Integrations

> **Audience**: Principal Data Scientist (12+ YOE) preparing for Director/VP-level AI roles.
> **Scope**: End-to-end architecture of the Model Context Protocol (MCP) and Agent-to-Agent (A2A) protocol -- protocol mechanics, transport layers, token economics, enterprise security (OAuth 2.1, OWASP MCP Top 10, CVEs, NSA guidance), gateway patterns, production resilience, and multi-agent system design.
> **Pricing assumptions**: Claude Sonnet 4 input $3/1M tokens, output $15/1M; Claude Opus 4 input $15/1M, output $75/1M; GPT-4o input $2.50/1M, output $10/1M. Prompt caching reduces input cost by 90% for cached prefixes. All as of mid-2026.
> **Key references**: MCP Specification 2026-07-28 RC (stateless core), OWASP MCP Top 10 (2025), NSA CSI on MCP Security (May 2026), Five Eyes Agentic AI Guidance (May 2026), A2A Protocol v1.0.0 (March 2026), Google A2A Announcement (April 2025).

---

## 1. System Topology & Data Flow

### 1.1 MCP Three-Participant Architecture

MCP uses a three-participant client-server model inspired by the Language Server Protocol (LSP). The host application creates one MCP client per server -- an IDE connected to filesystem, GitHub, and database servers runs three independent client instances simultaneously.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              HOST APPLICATION                                    │
│                         (Claude Desktop / IDE / Custom)                          │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │                         CONTROL PLANE                                    │    │
│  │                                                                          │    │
│  │  ┌──────────────────┐  ┌────────────────────┐  ┌─────────────────────┐  │    │
│  │  │  MCP Gateway /    │  │  Capability        │  │  Auth Provider       │  │    │
│  │  │  Router           │  │  Negotiator        │  │                     │  │    │
│  │  │                  │  │                    │  │  - OAuth 2.1 + PKCE │  │    │
│  │  │  - Tool routing  │  │  - Protocol ver    │  │  - IdP integration  │  │    │
│  │  │  - Server disc.  │  │    matching         │  │    (Okta/Azure AD)  │  │    │
│  │  │  - Load balance  │  │  - Feature caps    │  │  - Per-tool scoping │  │    │
│  │  │  - Schema cache  │  │    exchange          │  │  - Token lifecycle  │  │    │
│  │  └────────┬─────────┘  └─────────┬──────────┘  └──────────┬──────────┘  │    │
│  │           │                      │                         │              │    │
│  │  ┌────────v──────────────────────v─────────────────────────v──────────┐  │    │
│  │  │                    SERVER REGISTRY                                  │  │    │
│  │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │  │    │
│  │  │  │ Server A  │  │ Server B  │  │ Server C  │  │ Private Registry │   │  │    │
│  │  │  │ (stdio)   │  │ (HTTP)    │  │ (HTTP)    │  │ (enterprise-     │   │  │    │
│  │  │  │ local     │  │ remote    │  │ remote    │  │  approved list)  │   │  │    │
│  │  │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘   │  │    │
│  │  └───────────────────────────────────────────────────────────────────┘  │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │                          DATA PLANE                                      │    │
│  │                                                                          │    │
│  │  ┌──────────────────┐  ┌────────────────────┐  ┌─────────────────────┐  │    │
│  │  │  Tool Dispatch    │  │  Resource Access    │  │  Prompt Templates   │  │    │
│  │  │                  │  │                    │  │                     │  │    │
│  │  │  - tools/list    │  │  - resources/list  │  │  - prompts/list    │  │    │
│  │  │  - tools/call    │  │  - resources/read  │  │  - prompts/get     │  │    │
│  │  │  - Structured    │  │  - URI templates   │  │  - Dynamic context │  │    │
│  │  │    output (v3+)  │  │  - Change notifs   │  │    assembly         │  │    │
│  │  └────────┬─────────┘  └─────────┬──────────┘  └──────────┬──────────┘  │    │
│  │           │                      │                         │              │    │
│  │  ┌────────v──────────────────────v─────────────────────────v──────────┐  │    │
│  │  │              ELICITATION (user-facing structured input)             │  │    │
│  │  │              Server pauses mid-task, requests user input            │  │    │
│  │  │              via JSON Schema-defined form fields                    │  │    │
│  │  └───────────────────────────────────────────────────────────────────┘  │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                          MCP CLIENT LAYER                                        │
│                     (one client instance per server)                             │
│                                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  Client 1     │  │  Client 2     │  │  Client 3     │  │  Client N         │    │
│  │  (stdio)      │  │  (HTTP)       │  │  (HTTP)       │  │  (HTTP)           │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────────┘    │
│         │                 │                 │                   │                │
├─────────┼─────────────────┼─────────────────┼───────────────────┼────────────────┤
│         │     TRANSPORT   │                 │                   │                │
│         │     LAYER       │                 │                   │                │
│    stdin/stdout     Streamable HTTP   Streamable HTTP     Streamable HTTP       │
│    (local proc)     (POST + SSE)      (POST + SSE)        (POST + SSE)          │
│         │                 │                 │                   │                │
├─────────┼─────────────────┼─────────────────┼───────────────────┼────────────────┤
│         │                 │                 │                   │                │
│  ┌──────v───────┐  ┌──────v───────┐  ┌──────v───────┐  ┌──────v───────────┐    │
│  │  TOOL PROXY   │  │  TOOL PROXY   │  │  TOOL PROXY   │  │  TOOL PROXY       │    │
│  │  SERVERS      │  │  SERVERS      │  │  SERVERS      │  │  SERVERS          │    │
│  │               │  │               │  │               │  │                   │    │
│  │  Filesystem   │  │  GitHub       │  │  PostgreSQL   │  │  Slack / Jira /   │    │
│  │  (read/write/ │  │  (93 tools,   │  │  (query,      │  │  Salesforce /     │    │
│  │   search)     │  │   55K tokens) │  │   schema)     │  │  Browser          │    │
│  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────────┘    │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                     PERSISTENCE LAYER                                            │
│                                                                                  │
│  ┌──────────────────┐  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │  Server Registry   │  │  Session State*     │  │  Audit Logs                │  │
│  │                  │  │                    │  │                            │  │
│  │  Approved server │  │  *Eliminated by    │  │  Every tools/list,        │  │
│  │  catalog, caps,  │  │   2026-07-28 RC    │  │  tools/call, discovery    │  │
│  │  versions, health│  │  (stateless core)  │  │  logged with crypto       │  │
│  │                  │  │                    │  │  timestamps, user/agent   │  │
│  │  Gateway config  │  │  Pre-2026: Redis   │  │  identity, I/O hashes     │  │
│  └──────────────────┘  └────────────────────┘  └────────────────────────────┘  │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                     TELEMETRY / OBSERVABILITY                                    │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │  - Per-tool-call latency traces (target: <300ms/call)                    │    │
│  │  - Token overhead per request (tool schema inflation tracking)           │    │
│  │  - Circuit breaker state changes (closed/open/half-open transitions)     │    │
│  │  - Rate limit counters and rejection rates (fleet-wide)                  │    │
│  │  - Tool selection accuracy (correct tool vs. overlapping tool chosen)    │    │
│  │  - Error rate by server, by tool, by agent                               │    │
│  │  - Sinks: Splunk / Datadog / Sentinel / CloudWatch + S3 audit bucket    │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narratives

**Tool invocation flow (stateless, 2026-07-28 RC)**:

```
User Prompt                                                       External API
    │                                                                   ^
    v                                                                   │
┌────────┐  1. User message   ┌────────┐  4. tools/call   ┌─────────┐  │
│  Host  │ ────────────────>  │  LLM   │ ──────────────>  │ MCP     │  │
│  App   │                    │        │                   │ Client  │──┘
│        │  3. LLM selects    │        │  5. JSON-RPC      │         │
│        │ <──── tool call    │        │     POST to       │         │
│        │                    │        │     server        │         │
│        │  7. Final response │        │                   │         │
│        │ <────────────────  │        │  6. Result        │         │
└────────┘                    └────────┘ <────────────────  └─────────┘
                                  ^
                                  │ 2. Tool schemas in
                                  │    system prompt
                                  │    (50-75K tokens
                                  │     for 5 servers)
```

Steps: (1) User sends message. (2) Host injects all connected servers' tool schemas into the LLM system prompt. (3) LLM reasons over schemas, selects tool and parameters. (4) Host routes `tools/call` via the appropriate MCP client. (5) Client sends JSON-RPC POST to the server endpoint (or writes to stdin for stdio). (6) Server executes, returns result. Any server instance can handle the request (stateless). (7) Host feeds result back to LLM for final answer generation.

**Resource access flow**:

```
┌────────┐  resources/list   ┌──────────┐   URI template    ┌──────────┐
│  Host  │ ───────────────>  │  MCP     │  resolution       │  MCP     │
│  App   │                   │  Client  │ ───────────────>  │  Server  │
│        │  resource catalog │          │                    │          │
│        │ <───────────────  │          │  resources/read    │          │
│        │                   │          │ ───────────────>  │          │
│        │  resource content │          │                    │          │
│        │ <───────────────  │          │  content blob      │          │
│        │                   │          │ <───────────────  │          │
└────────┘                   └──────────┘                    └──────────┘
```

Resources are application-controlled (the host decides when to surface them), unlike tools which are model-controlled (the LLM decides when to invoke them). The host may inject resource content into context based on UI actions, not LLM reasoning.

**A2A agent communication flow**:

```
┌────────────┐  1. Discover agent card  ┌────────────┐
│  Calling   │ ──────────────────────>  │  Remote    │
│  Agent     │    GET /.well-known/     │  Agent     │
│            │    agent.json            │            │
│            │                          │            │
│            │  2. SendMessage          │            │
│            │ ──────────────────────>  │            │
│            │    (A2A Task created)    │            │
│            │                          │            │
│            │  3. Status: "working"    │            │
│            │ <──────────────────────  │            │
│            │                          │  ┌───────┐ │
│            │  4. SubscribeToTask      │  │ MCP   │ │
│            │ ──────────────────────>  │  │ Tools │ │
│            │    (SSE stream opens)    │  └───┬───┘ │
│            │                          │      │     │
│            │  5. Artifact (result)    │  Uses MCP  │
│            │ <──────────────────────  │  internally│
│            │    Status: "completed"   │            │
└────────────┘                          └────────────┘
```

Each agent is opaque to its peers. The remote agent uses MCP tools internally but exposes only its A2A Task interface. The calling agent never sees the remote agent's tool definitions, prompts, or reasoning chain.

---

## 2. Core Mechanics & Algorithms

### 2.1 MCP Protocol Foundation: JSON-RPC 2.0

MCP sits on JSON-RPC 2.0 as its wire format. Every message is one of three types:

- **Request**: `{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {...}}` -- expects a response.
- **Response**: `{"jsonrpc": "2.0", "id": 1, "result": {...}}` or `{"jsonrpc": "2.0", "id": 1, "error": {...}}`.
- **Notification**: `{"jsonrpc": "2.0", "method": "notifications/resources/updated", "params": {...}}` -- fire-and-forget, no `id`.

JSON-RPC batching was removed in the 2025-06-18 revision.

### 2.2 Six Core Primitives

**Server-provided (model/application/user controlled):**

| Primitive | Controller | Purpose | Key Methods |
|---|---|---|---|
| **Tools** | Model-controlled | Callable functions the LLM invokes | `tools/list`, `tools/call` |
| **Resources** | Application-controlled | Read-only data (files, DB records, API responses) | `resources/list`, `resources/read`, URI templates |
| **Prompts** | User-controlled | Reusable prompt templates with dynamic context | `prompts/list`, `prompts/get` |

**Client-provided (host-to-server):**

| Primitive | Status | Purpose |
|---|---|---|
| **Sampling** | Deprecated (2026-07-28 RC) | Server asks host LLM to generate text |
| **Roots** | Deprecated (2026-07-28 RC) | Sandbox boundary for server file access |
| **Elicitation** | Active (added 2025-06-18) | Server requests structured user input mid-task |

**Why Sampling and Roots were deprecated**: Sampling coupled servers to the host's LLM, creating implicit dependencies. The recommendation is to use direct provider APIs. Roots conflated protocol-level concerns with tool parameterization -- tool parameters should specify paths explicitly.

### 2.3 Tool Schema Structure

Each tool definition contains:

```json
{
  "name": "search_issues",
  "description": "Search GitHub issues by query string, labels, and state",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Search query"},
      "labels": {"type": "array", "items": {"type": "string"}},
      "state": {"type": "string", "enum": ["open", "closed", "all"]}
    },
    "required": ["query"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "issues": {"type": "array", "items": {"$ref": "#/definitions/Issue"}}
    }
  }
}
```

Since 2025-06-18, tools can declare structured output schemas and return resource links alongside results, enabling tools to point clients at follow-up data.

### 2.4 Transport Layers

**stdio**: Server runs as a child process. Client writes JSON-RPC to the process's stdin, reads from stdout. Newline-delimited messages. No network configuration. Used by Claude Desktop, VS Code, and most local setups. Latency is sub-millisecond for the transport itself.

**Streamable HTTP**: Single endpoint (e.g., `https://example.com/mcp`). Two response modes:
- **Synchronous**: POST with JSON-RPC, receive `application/json` response. For fast tool calls.
- **Streaming**: POST with JSON-RPC, server upgrades to `text/event-stream` SSE. For long-running operations. Client can also GET the endpoint to open an SSE stream for server-initiated notifications.

Under the 2026-07-28 RC, session IDs (`Mcp-Session-Id`) are removed entirely. Each request is self-contained. Any server instance behind a load balancer can handle any request. This is the architectural shift that enables serverless deployment and horizontal scaling.

**Legacy HTTP+SSE**: Separate SSE endpoint for server-to-client events. Deprecated in 2026-07-28 with a 12-month removal window. Backward compatibility: clients auto-detect old servers by falling back to GET when POST returns 400/404/405.

**Transport decision matrix for interviews**:

| Factor | stdio | Streamable HTTP |
|---|---|---|
| Deployment | Local process only | Local or remote |
| Latency | Sub-ms transport | Network RTT (~1-50ms) |
| Scaling | Single instance | Horizontal (stateless) |
| Auth | OS-level process isolation | OAuth 2.1 mandatory |
| Serverless | Not possible | Native fit (Cloud Run, Lambda) |
| Use case | Desktop tools, IDE plugins | Enterprise, multi-tenant, cloud |

### 2.5 MCP Server Lifecycle

Four phases with specific JSON-RPC exchanges:

```
Phase 1: INITIALIZATION
Client ──> Server:  initialize {protocolVersion: "2026-07-28", capabilities: {...}}
Server ──> Client:  {protocolVersion: "2026-07-28", capabilities: {...}, serverInfo: {...}}
Client ──> Server:  initialized (notification, no response expected)

Phase 2: CAPABILITY EXCHANGE (within initialization)
Both sides declare supported features:
  Client caps: {sampling: {}, roots: {listChanged: true}}
  Server caps: {tools: {listChanged: true}, resources: {subscribe: true}, prompts: {}}

Phase 3: OPERATION
Client ──> Server:  tools/list {}
Server ──> Client:  {tools: [{name: "search", ...}, ...]}
Client ──> Server:  tools/call {name: "search", arguments: {query: "bug"}}
Server ──> Client:  {content: [{type: "text", text: "..."}]}
Server ──> Client:  notifications/tools/list_changed (server can signal changes)

Phase 4: SHUTDOWN
stdio:  Client terminates server process (SIGTERM, then SIGKILL)
HTTP:   Client stops sending requests. No explicit shutdown message.
```

**Protocol version negotiation**: Client proposes its version; server responds with a compatible version. Mismatch causes handshake failure. Claude Desktop negotiating 2025-11-25 against a server speaking only 2024-11-05 results in Claude Desktop refusing all tool calls -- the server works, but the integration is non-functional.

### 2.6 MCP Client Integration Patterns

Three patterns for integrating MCP clients into applications:

1. **Direct embedding**: Application instantiates MCP client SDK directly. Simplest pattern. Works for single-purpose apps with a fixed set of servers.

2. **Gateway proxy**: Application routes all MCP traffic through a centralized gateway that handles auth, rate limiting, audit, and routing. The gateway speaks MCP on both sides. This is the enterprise pattern.

3. **Sidecar**: MCP client runs as a sidecar container in Kubernetes. Each pod gets its own client. Provides process isolation without application code changes.

### 2.7 A2A Protocol

Google's Agent-to-Agent protocol (v1.0.0, March 2026) solves a different problem: agent-to-agent coordination rather than agent-to-tool access.

**Core concepts:**

- **Agent Cards**: JSON documents at `/.well-known/agent.json` (RFC 8615). Declare capabilities, supported I/O formats, auth requirements. Equivalent to OpenAPI specs but for agents.

- **Tasks**: Work units with lifecycle states: `submitted -> working -> input-required -> completed / failed / canceled`. Output captured as "artifacts." Long-running tasks supported via `SubscribeToTask` and push notifications.

- **Messages & Parts**: Multi-modal -- text, binary, files, structured data in one message. Roles: `user` or `agent`.

- **Transport**: HTTP + SSE + JSON-RPC 2.0. Auth: API keys, HTTP auth, OAuth 2.0/OIDC, mTLS. 11 JSON-RPC methods including `SendMessage`, `SendStreamingMessage`, `GetTask`, `SubscribeToTask`. v0.3 added gRPC support.

**Governance**: Donated to Linux Foundation June 2025. TSC includes AWS, Cisco, Google, IBM, Microsoft, Salesforce, SAP, ServiceNow. IBM's Agent Communication Protocol (ACP) merged into A2A August 2025. 150+ supporting organizations.

### 2.8 MCP vs A2A: Complementary Roles

| Dimension | MCP | A2A |
|---|---|---|
| **Purpose** | Agent-to-tool access | Agent-to-agent coordination |
| **Direction** | Vertical (model to capabilities) | Horizontal (agent to agent) |
| **Architecture** | Client-server | Peer-to-peer |
| **Discovery** | Server registries | Agent Cards at well-known URIs |
| **Opacity** | Server internals transparent | Agents opaque to each other (by design) |
| **Maturity** | Production-grade, 1B+ SDK downloads | v1.0 stable, earlier on tooling curve |

**Combined architecture**: MCP gives each agent standardized tool access; A2A manages orchestration between agents. 80%+ of production AI deployments are single-agent + MCP. A2A becomes relevant for multi-vendor, multi-agent workflows where agents built on different frameworks (LangGraph, CrewAI, Semantic Kernel) need to collaborate.

### 2.9 MCP Registries and Discovery

The discovery landscape is fragmented across multiple registries:

| Registry | Type | Scale (mid-2026) |
|---|---|---|
| **Official MCP Registry** | Machine-readable API | 9,652 server records |
| **Glama.ai** | Directory + metadata + sandbox | 36,950 servers |
| **mcp.so** | Third-party marketplace | 20,222 servers |
| **PulseMCP** | Hand-reviewed directory | 16,820+ servers |
| **Smithery.ai** | Docker Hub equivalent, hosted runtime | 7,000+ servers |
| **MCPfinder** | Agent-native meta-index (MCP server itself) | 27,432 aggregated |

No single number is the authoritative ecosystem total due to different crawl methods and deduplication approaches. The Official Registry (registry.modelcontextprotocol.io) launched September 2025 and is the canonical source for programmatic discovery.

### 2.10 Specification Evolution Timeline

| Version | Key Changes |
|---|---|
| **2024-11-05** | Initial release. Client-server model, tools/resources/prompts, stdio + HTTP+SSE |
| **2025-03-26** | Streamable HTTP, OAuth 2.1, HTTP+SSE deprecation begins |
| **2025-06-18** | Structured output, elicitation, resource links, JSON-RPC batching removed, RFC 8707 resource indicators |
| **2025-11-25** | Current stable. OIDC Discovery, icons metadata, experimental tasks/extensions |
| **2026-07-28 RC** | **Stateless core** (no sessions), multi round-trip requests, header-based routing, cacheable lists, formal extensions framework (SEP-2133), MCP Apps (SEP-1865, sandboxed HTML UIs), roots/sampling/logging deprecated |

The 2026-07-28 RC is the most significant architectural shift since inception. The stateless core transforms MCP from a bidirectional stateful protocol to a request/response stateless protocol. GitHub's MCP server removed Redis session storage after upgrading. Any request can be answered by any server instance behind ordinary HTTP infrastructure -- plain round-robin, no sticky sessions.

### 2.11 MCP Governance

In December 2025, Anthropic donated MCP to the **Agentic AI Foundation (AAIF)**, a directed fund under the Linux Foundation, co-founded by Anthropic, Block, and OpenAI. Platinum members include Google, Microsoft, AWS, and Cloudflare. OpenAI adopted MCP March 2025, Microsoft July 2025, AWS November 2025.

### 2.12 API Connector Patterns

MCP normalizes diverse APIs into a uniform tool interface:

- **REST APIs**: Each endpoint becomes an MCP tool. Path/query params and request body map to tool input schema.
- **GraphQL APIs**: Queries/mutations map to tools. Schema introspection auto-generates tool definitions.
- **gRPC services**: Proto definitions map to tool schemas. Streaming RPCs map to SSE tool responses.
- **Webhooks**: Event-driven servers expose webhook receivers as resources. `notifications/resources/updated` signals real-time changes.

The key value proposition is collapsing the N*M integration problem. Without MCP: 10 agents x 20 systems = 200 custom connectors. With MCP: 10 + 20 = 30 (each system exposes one server, each agent uses one client SDK).

---

## 3. Token Economics & NFR Analysis

### 3.1 Token Overhead per MCP Server

Every MCP server connection loads its full tool definition set into the LLM's context window on every request. These definitions are not free:

| Server | Tools | Token Cost per Request |
|---|---|---|
| GitHub MCP (official) | 93 | ~55,000 tokens |
| Typical enterprise server | 15-20 | 10,000-15,000 tokens |
| Simple single-tool server | 1 | 50-100 tokens |
| Enterprise tool (nested schemas, enums, examples) | 1 | 500-1,000 tokens |

**Aggregate formula**:

```
Per-request tool overhead = SUM(token_cost(server_i)) for all connected servers

Example: 5 servers with 12 tools each
  = 5 x ~12,000 tokens
  = ~60,000 tokens per request (before user prompt)

Monthly cost (Claude Sonnet 4, 10K requests/day):
  = 60,000 tokens x 10,000 requests x 30 days x $3/1M tokens (input)
  = $54.00/month just for tool schemas

With prompt caching (90% hit rate at steady state):
  = $54.00 x 0.10 = $5.40/month
```

**Token inflation research**: An arXiv study found prompt-to-completion token inflation of 2x-30x compared to baseline chat across nine LLMs. This directly translates to higher cost and increased latency.

### 3.2 Cost Optimization Strategies

**Strategy 1: Schema compression** -- Strip descriptions, enums, and nested type documentation while preserving parameter structure. Atlassian's `mcp-compressor` is an open-source proxy implementing this. Typical reduction: 40-60%.

**Strategy 2: Search-first tool discovery (progressive disclosure)** -- Expose only two meta-tools: `get_tool` (search by description) and `invoke_tool` (execute by name). Solo.io's agentgateway reduced prompt tokens from 10,877 to 970 tokens (**91.1% reduction**). The model searches for relevant tools by description, loads only those schemas, then invokes.

**Strategy 3: Code mode / dynamic loading** -- At 500+ tools, the model pays 1M+ tokens for "menu reading." Code mode loads schemas only for tools actually used. Latency drops ~40%, and adding tools has near-zero marginal cost per query.

**Strategy 4: Prefix caching** -- Anthropic's prompt cache and similar features cache the tool definitions portion of the prompt. At high request volume, the marginal cost of tool tokens approaches zero for repeated sessions. Claude's prompt caching charges 10% of input token price for cached prefixes.

**Optimization decision framework**:

| Scale | Best Strategy | Savings | Complexity |
|---|---|---|---|
| 1-5 servers, <20 tools | Prefix caching alone | 80-90% | None |
| 5-15 servers, 20-100 tools | Schema compression + caching | 85-95% | Low (proxy) |
| 15+ servers, 100-500 tools | Progressive disclosure | 90%+ | Medium (meta-tool logic) |
| 500+ tools | Code mode / dynamic loading | 95%+ | High (runtime schema fetch) |

### 3.3 Latency SLA Targets

| Metric | Value | Notes |
|---|---|---|
| Per-tool-call latency | ~300ms | Target for production MCP |
| Tool calls per task | 5-15 | Observed in Claude Desktop |
| Total tool-call wait per task | 1.5-4.5s | Calculated |
| Prefill overhead from schemas | 1-2s added to TTFT | At ~400 tok/s prefill on H100 |
| 10-turn agent conversation | 10x per-turn schema cost | Cumulative (mitigated by caching) |
| TypeScript vs Python cold start | TypeScript ~80ms faster | V8 startup vs interpreter |
| Python idle memory overhead | ~15MB more RSS | Interpreter cost |
| Gateway overhead (Bifrost, TrueFoundry) | <3ms | In-memory routing, no DB lookup |

**Latency budget for agentic task (5 tool calls)**:

```
TTFT (with schemas):        1.5s  (schema prefill + model reasoning)
Tool call 1 (round trip):   0.3s
Model reasoning:            0.5s
Tool call 2 (round trip):   0.3s
Model reasoning:            0.5s
Tool call 3 (round trip):   0.3s
Model reasoning:            0.5s
Tool call 4 (round trip):   0.3s
Model reasoning:            0.5s
Tool call 5 (round trip):   0.3s
Final response generation:  1.0s
─────────────────────────────────
Total wall-clock:           ~6.0s
```

### 3.4 Throughput

- The 2026-07-28 stateless core removes the throughput bottleneck of session affinity. Any server instance handles any request.
- Concurrent connections: limited only by server infrastructure (pods, cloud functions, thread pools), not protocol constraints.
- Serverless deployments (Cloud Run, Lambda): auto-scale to zero, burst to thousands of concurrent instances.
- Message rates: bounded by backend API rate limits, not MCP protocol overhead.

### 3.5 Tool Selection Accuracy Degradation

Presenting a model with 50+ tools degrades selection quality. Past a few dozen tools, the model starts choosing overlapping or incorrect tools. Error rate climbs with tool surface area. This is both a token argument and a correctness argument: **ten focused tools beat thirty** in both cost and accuracy.

### 3.6 Ecosystem Scale and Enterprise Adoption

| Metric | Value | Date |
|---|---|---|
| Monthly SDK downloads | 97 million | March 2026 |
| Tier 1 SDK total downloads (TS & Python) | 1 billion+ each | Mid-2026 |
| Active public MCP servers | 10,000+ | March 2026 |
| GitHub repos with mcp-server topic | 15,926 | May 2026 |
| MCP clients | 300+ | Mid-2026 |
| Remote server deployments growth | 4x since May 2025 | Mid-2026 |

**Enterprise adoption reality**:
- 41-45% of technical leaders report limited production use (Stacklok survey, N=300).
- ~62% of enterprise AI teams experimenting with MCP-compatible architectures.
- ~30% of Fortune 500 firms piloting MCP-based AI orchestration.
- The "78% enterprise production use" claim has been retracted -- does not hold under scrutiny.

**ROI data points**:
- Block: MCP-powered tools reduced daily task time by up to **75%** for refactoring and unit tests.
- Bloomberg: Reduced agent deployment time from **days to minutes**.
- Industry average: **40-60% faster agent deployment** vs. custom integrations.

---

## 4. Distributed Resilience & Security

### 4.1 Connection Management

**Stateless protocol benefits (2026-07-28 RC)**:

The original MCP (2024-11-05) was session-oriented, optimized for a single client talking to a single local server. At scale, organizations hit hard walls:

| Problem (Pre-2026) | Stateless Fix (2026-07-28) |
|---|---|
| Session affinity required (sticky load balancing) | Any instance handles any request |
| Pod failure = irrecoverable session loss | Transparent failover (invisible to client) |
| Horizontal scaling needed shared session stores (Redis) | No session storage needed |
| Serverless deployment impossible | Native fit for Cloud Run, Lambda |

GitHub's MCP server removed Redis entirely after upgrading to the stateless core.

**stdio transport management**: Client manages the server process lifecycle (spawn, monitor, terminate). If the server process crashes, the client detects a broken pipe on stdin/stdout and restarts. No automatic reconnection in the protocol -- recovery depends on host implementation.

**Streamable HTTP management**: Stream resumability via `Last-Event-ID` header on GET reconnection. Under stateless core, each request is self-contained. Standard HTTP failure modes (502/503/504) indicate server issues and trigger client-side retry logic.

### 4.2 Gateway Patterns

The N*M connectivity problem (10 agents x 20 systems = 200 connections) is solved by gateway architectures:

```
┌──────────────────────────────────────────────────────────────┐
│                      MCP GATEWAY                              │
│                                                                │
│  ┌─────────────────────────────┐  ┌─────────────────────────┐ │
│  │      CONTROL PLANE           │  │      DATA PLANE          │ │
│  │                             │  │                           │ │
│  │  - Policy engine            │  │  - OAuth 2.1 validation  │ │
│  │  - Server registry          │  │  - Tool-level RBAC       │ │
│  │  - Certificate management   │  │  - Rate limiting          │ │
│  │  - Analytics / dashboards   │  │    (fleet-wide counters)  │ │
│  │  - Configuration            │  │  - DLP filtering          │ │
│  │                             │  │  - Circuit breakers       │ │
│  │  Optimized for:             │  │  - Caching (tool lists)   │ │
│  │    reliability              │  │  - Audit logging          │ │
│  │                             │  │                           │ │
│  │                             │  │  Optimized for:           │ │
│  │                             │  │    latency, throughput    │ │
│  └─────────────────────────────┘  └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

**Leading gateway solutions (2026)**:

| Gateway | Distinguishing Feature | Overhead |
|---|---|---|
| Bifrost | Zero-config, in-memory routing | <3ms |
| Cloudflare MCP Portals | Edge network distribution | Edge latency |
| Kong AI Gateway | Plugin ecosystem | Configurable |
| TrueFoundry | Low-latency under load | <3ms |
| IBM ContextForge | Multi-gateway federation | Varies |

**Rate limiting challenge**: MCP rate limiting differs fundamentally from REST API limiting. Agents do not pause between tool calls. A misconfigured agent can call the same tool hundreds of times in a loop. Per-instance limits are insufficient -- an agent hitting 10 independently-limited servers gets 10x its intended quota. Production deployments need **fleet-wide shared counters** (Redis Cluster) where every server instance checks a common limiter before accepting a call.

### 4.3 Enterprise Security: OAuth 2.1 with PKCE

MCP mandates OAuth 2.1 for remote server authentication. The MCP server acts as an OAuth 2.1 Resource Server; the MCP client acts as an OAuth 2.1 Client.

**Key requirements**:

- **Mandatory PKCE** for all flows (not just public clients). Implicit grant dropped entirely.
- **Exact redirect URI matching** to reduce attack surface for desktop clients.
- **Protected Resource Metadata (PRM)**: Servers MUST implement RFC 9728. Auth servers MUST provide RFC 8414 metadata.
- **Dynamic Client Registration (DCR)**: SHOULD support RFC 7591 for automatic registration -- critical because clients may not know all possible servers in advance.
- **Resource Indicators (RFC 8707)**: Required since June 2025 to bind tokens to specific MCP servers.
- **Token passthrough prohibited**: Servers calling upstream APIs MUST obtain separate tokens. Passing through client tokens creates confused deputy vulnerabilities.
- **Enterprise-Managed Authorization (EMA)**: Production-grade in 2026-07-28 RC. Enables centralized organizational policy control.

**Auth evolution timeline**:

```
2025-03-26:  OAuth 2.1 introduced, server conflated resource + auth server roles
2025-06-18:  Clean separation: MCP server = resource server, distinct auth server
             RFC 8707 resource indicators added. Token passthrough prohibited.
2025-11-25:  OIDC Discovery added
2026-07-28:  Enterprise-Managed Authorization production-grade
```

**Reality check**: Only ~8.5% of MCP deployments implement OAuth 2.1 with PKCE, despite it being mandatory for remote servers since November 2025. 24-25% of MCP servers have no authentication at all.

### 4.4 OWASP MCP Top 10

The first dedicated security framework for MCP (beta, 2025):

| # | Risk | Interview-Ready Description |
|---|---|---|
| MCP01 | Token Mismanagement | Hard-coded credentials, long-lived tokens, secrets in logs |
| MCP02 | Excessive Permissions | Scope expansion over time, weak enforcement of least privilege |
| MCP03 | Tool Poisoning | Malicious instructions hidden in tool metadata, invisible to users, visible to LLMs |
| MCP04 | Supply Chain Attacks | Compromised packages, typosquatted server names |
| MCP05 | Command Injection | Unsanitized input reaching shell/API execution |
| MCP06 | Intent Flow Subversion | Indirect prompt injection via tool response content |
| MCP07 | Inadequate Auth & AuthZ | Missing identity verification, no access controls |
| MCP08 | Insufficient Audit Logging | Tool calls not logged, incidents undetectable |
| MCP09 | Shadow MCP Servers | Unregistered servers bypassing security governance |
| MCP10 | Context Injection | Cross-session data leakage, sensitive data exposure in context |

### 4.5 Known CVEs and Attack Vectors

**Critical CVEs (2025-2026)**:

| CVE | Target | CVSS | Impact |
|---|---|---|---|
| CVE-2025-6514 | mcp-remote v0.0.5-0.1.15 | 9.6 | First real-world RCE via untrusted remote server. 437K+ downloads. |
| CVE-2025-49596 | Anthropic MCP Inspector | 9.4 | RCE via DNS rebinding. Patched v0.14.1. |
| CVE-2025-54136 | Cursor IDE (MCPoison) | 7.2 | Persistent RCE via trusted-but-swapped MCP config |
| CVE-2026-33032 | nginx-ui MCP (MCPwn) | 9.8 | Auth bypass, actively exploited |
| CVE-2026-32211 | Azure MCP Server | High | Missing auth layer entirely |

**Scale**: 30+ CVEs in a 60-day window in early 2026 (~43% command injection). OX Security identified ~200,000 vulnerable instances. Palo Alto Unit 42: with 5 connected MCP servers, a single compromised server achieved **78.3% attack success rate**.

**Tool poisoning attack chain**:
1. Attacker publishes MCP server with malicious instructions in tool descriptions (invisible in UI, visible to LLM).
2. User installs server, approves tool list (descriptions look benign in truncated UI).
3. LLM reads full descriptions including hidden instructions, follows them.
4. Data exfiltration, credential theft, or arbitrary code execution.

**Rug pull variant**: Server initially has safe descriptions. After trust is established, server-side update changes descriptions to include malicious instructions. No re-approval required by most clients.

**IDE auto-execution risk**: Leading IDEs (Cursor, Claude Code, Gemini CLI, GitHub Copilot, Amazon Q) auto-execute project-defined MCP servers with developer-level OS privileges and no process isolation. A malicious `.mcp.json` in a cloned repository executes arbitrary code.

### 4.6 Zero-Trust MCP Architecture

**Maturity levels**:

- **Level 3 (mature)**: mTLS transport identity + token-based user/tenant authorization. Signed/verified tool artifacts. Centralized capability registry. Comprehensive audit logging.
- **Level 4 (advanced)**: Hardware-backed identity and workload attestation. Tools in isolated micro-VMs. Real-time behavioral anomaly detection. Full dependency supply chain verification.

**Core principle**: "Trust domain is down to a single request." Every request authenticated and authorized independently.

### 4.7 Government Guidance

**NSA CSI (May 20, 2026)**: "Model Context Protocol: Security Design Considerations for AI-Driven Automation." 17 pages. First U.S. SIGINT agency protocol-specific guidance for AI agent infrastructure. Key findings:
- "MCP's rapid proliferation has outpaced the development of its security model."
- Authentication is optional, RBAC not in the protocol, session-to-identity mapping undefined.
- Inverted interaction pattern (servers execute for clients) creates poorly-traced attack paths.
- Recommends: treat every MCP session as untrusted, enforce least-privilege tokens per action, require signed provenance for dynamically discovered servers.

**Five Eyes (May 1, 2026)**: CISA, NSA, ASD's ACSC, CCCS, NZ NCSC, UK NCSC jointly published 30-page guidance on agentic AI. First time all five Five Eyes nations coordinated on a single AI attack surface. Doctrine: zero trust, defense in depth, least privilege for agentic AI.

### 4.8 Triple-Gate Security Pattern

Defense-in-depth for enterprise MCP:

```
┌─────────────────────────────────────────────────────────────┐
│                                                               │
│   GATE 1: AI Client ──> LLM                                 │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  - Prompt injection filtering                         │   │
│   │  - PII detection and redaction in user input          │   │
│   │  - Input length and complexity validation             │   │
│   └──────────────────────────────────────────────────────┘   │
│                          │                                    │
│   GATE 2: LLM ──> MCP Server                                │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  - Tool authorization (per-role, per-tool RBAC)       │   │
│   │  - Parameter validation (schema + business rules)     │   │
│   │  - DLP filtering on tool inputs                       │   │
│   │  - Tool call rate limiting                            │   │
│   └──────────────────────────────────────────────────────┘   │
│                          │                                    │
│   GATE 3: MCP Server ──> External API                        │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  - Rate limiting (fleet-wide shared counters)         │   │
│   │  - Authentication (separate tokens, no passthrough)   │   │
│   │  - Egress policy enforcement                          │   │
│   │  - Response sanitization before returning to LLM      │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### 4.9 Audit Logging Requirements

Every MCP operation must be logged with:
- Cryptographic timestamps
- User identity (from OAuth token)
- Agent identity (which agent initiated)
- Tool name and method (`tools/call`, `resources/read`)
- Hashed input/output (for investigation without storing sensitive data)
- Latency and result status
- Conversation-level chain from prompt through tool calls to response

Immutable audit trail feeding SIEM (Splunk, Sentinel) and compliance S3 bucket. Required for HIPAA, SOC 2, GDPR, and DORA compliance.

---

## 5. Production Enterprise Code

### 5.1 MCP Server Implementation

A complete MCP server with tool and resource handlers using the Python SDK:

```python
"""
MCP server exposing a product catalog as tools and resources.
Run: python product_server.py (stdio transport)
Or:  uvicorn product_server:app (Streamable HTTP transport)
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    Resource,
    TextContent,
    ResourceContents,
    TextResourceContents,
)

logger = logging.getLogger(__name__)

# Simulated product database
PRODUCTS: dict[str, dict[str, Any]] = {
    "PROD-001": {"name": "Enterprise License", "price": 50000, "category": "software", "stock": 999},
    "PROD-002": {"name": "Support Plan", "price": 12000, "category": "service", "stock": 999},
    "PROD-003": {"name": "Training Package", "price": 8000, "category": "service", "stock": 50},
}

server = Server("product-catalog")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_products",
            description="Search products by category or name substring",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Name substring to match"},
                    "category": {
                        "type": "string",
                        "enum": ["software", "service", "hardware"],
                        "description": "Product category filter",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 10,
                        "description": "Maximum results to return",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="get_product_details",
            description="Get full details for a specific product by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Product ID (e.g., PROD-001)"},
                },
                "required": ["product_id"],
            },
        ),
        Tool(
            name="check_inventory",
            description="Check current stock level for a product",
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                },
                "required": ["product_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "search_products":
        query = arguments.get("query", "").lower()
        category = arguments.get("category")
        max_results = arguments.get("max_results", 10)

        results = []
        for pid, product in PRODUCTS.items():
            if category and product["category"] != category:
                continue
            if query and query not in product["name"].lower():
                continue
            results.append({"id": pid, **product})
            if len(results) >= max_results:
                break

        return [TextContent(type="text", text=json.dumps(results, indent=2))]

    elif name == "get_product_details":
        product_id = arguments["product_id"]
        product = PRODUCTS.get(product_id)
        if not product:
            return [TextContent(type="text", text=f"Product {product_id} not found")]
        return [TextContent(type="text", text=json.dumps({"id": product_id, **product}, indent=2))]

    elif name == "check_inventory":
        product_id = arguments["product_id"]
        product = PRODUCTS.get(product_id)
        if not product:
            return [TextContent(type="text", text=f"Product {product_id} not found")]
        return [TextContent(
            type="text",
            text=json.dumps({"product_id": product_id, "stock": product["stock"],
                             "checked_at": datetime.now(timezone.utc).isoformat()})
        )]

    raise ValueError(f"Unknown tool: {name}")


@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri="catalog://products/all",
            name="Full Product Catalog",
            description="Complete product listing with prices and categories",
            mimeType="application/json",
        )
    ]


@server.read_resource()
async def read_resource(uri: str) -> list[TextResourceContents]:
    if uri == "catalog://products/all":
        catalog = [{"id": pid, **p} for pid, p in PRODUCTS.items()]
        return [TextResourceContents(
            uri=uri,
            mimeType="application/json",
            text=json.dumps(catalog, indent=2),
        )]
    raise ValueError(f"Unknown resource: {uri}")


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### 5.2 MCP Client with Capability Negotiation

```python
"""
MCP client that connects to a server, negotiates capabilities,
discovers tools, and executes a tool call.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp.client import Client
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)


class MCPClientSession:
    """Wraps MCP client with capability tracking and structured tool invocation."""

    def __init__(self, server_command: str, server_args: list[str] | None = None):
        self.server_params = StdioServerParameters(
            command=server_command,
            args=server_args or [],
        )
        self.client: Client | None = None
        self._tools: dict[str, dict[str, Any]] = {}

    @asynccontextmanager
    async def connect(self):
        """Connect to server, negotiate capabilities, cache tool catalog."""
        async with stdio_client(self.server_params) as (read_stream, write_stream):
            self.client = Client("enterprise-client", "1.0.0")
            async with self.client.session(read_stream, write_stream) as session:
                # Initialization and capability exchange happen automatically
                # during session creation. The SDK handles:
                #   1. Sending initialize with client capabilities
                #   2. Receiving server capabilities
                #   3. Sending initialized notification

                # Cache the tool catalog
                tools_response = await session.list_tools()
                self._tools = {
                    tool.name: {
                        "description": tool.description,
                        "input_schema": tool.inputSchema,
                    }
                    for tool in tools_response.tools
                }

                logger.info(
                    "Connected. Server offers %d tools: %s",
                    len(self._tools),
                    list(self._tools.keys()),
                )

                yield session

    async def invoke_tool(self, session, tool_name: str, arguments: dict[str, Any]) -> str:
        """Invoke a tool with validation against cached schema."""
        if tool_name not in self._tools:
            available = list(self._tools.keys())
            raise ValueError(f"Tool '{tool_name}' not found. Available: {available}")

        result = await session.call_tool(tool_name, arguments)

        # Extract text content from result
        texts = [part.text for part in result.content if hasattr(part, "text")]
        return "\n".join(texts)

    @property
    def available_tools(self) -> list[str]:
        return list(self._tools.keys())

    def get_tool_schema(self, tool_name: str) -> dict[str, Any] | None:
        return self._tools.get(tool_name)


async def main():
    client = MCPClientSession("python", ["product_server.py"])

    async with client.connect() as session:
        print(f"Available tools: {client.available_tools}")

        # Search for products
        result = await client.invoke_tool(
            session, "search_products", {"category": "service"}
        )
        print(f"Search results:\n{result}")

        # Get specific product
        result = await client.invoke_tool(
            session, "get_product_details", {"product_id": "PROD-001"}
        )
        print(f"Product details:\n{result}")


if __name__ == "__main__":
    asyncio.run(main())
```

### 5.3 MCP Gateway Proxy with Auth, Rate Limiting, and Audit

```python
"""
MCP Gateway Proxy: sits between MCP clients and backend MCP servers.
Enforces OAuth token validation, per-tool RBAC, fleet-wide rate limiting,
circuit breaking, and immutable audit logging.

This is a simplified but structurally complete implementation showing
the gateway pattern. In production, each concern would be a separate
middleware layer.
"""
import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token validation (simplified OAuth 2.1 resource server)
# ---------------------------------------------------------------------------
@dataclass
class TokenClaims:
    sub: str                    # Subject (user or service identity)
    client_id: str              # OAuth client ID
    scopes: set[str]            # Granted scopes
    exp: float                  # Expiry timestamp
    resource: str               # RFC 8707 resource indicator (bound to specific MCP server)


def validate_token(bearer_token: str) -> TokenClaims:
    """
    Validate OAuth 2.1 bearer token. In production this calls the authorization
    server's introspection endpoint or verifies a JWT signature.
    """
    # Simulated validation -- replace with real JWT verification or introspection
    MOCK_TOKENS = {
        "eng-agent-token": TokenClaims(
            sub="agent:eng-assistant",
            client_id="eng-client",
            scopes={"tools:github", "tools:jira", "tools:postgres"},
            exp=time.time() + 3600,
            resource="https://mcp-gateway.internal.company.com",
        ),
        "sales-agent-token": TokenClaims(
            sub="agent:sales-assistant",
            client_id="sales-client",
            scopes={"tools:salesforce", "tools:slack"},
            exp=time.time() + 3600,
            resource="https://mcp-gateway.internal.company.com",
        ),
    }

    claims = MOCK_TOKENS.get(bearer_token)
    if claims is None:
        raise PermissionError("Invalid or expired token")
    if claims.exp < time.time():
        raise PermissionError("Token expired")
    return claims


# ---------------------------------------------------------------------------
# RBAC: tool-level authorization
# ---------------------------------------------------------------------------
# Maps tool names to required scopes
TOOL_SCOPE_MAP: dict[str, str] = {
    "github_search_issues": "tools:github",
    "github_create_pr": "tools:github",
    "jira_get_ticket": "tools:jira",
    "salesforce_get_account": "tools:salesforce",
    "postgres_query": "tools:postgres",
    "slack_post_message": "tools:slack",
}


def authorize_tool_call(claims: TokenClaims, tool_name: str) -> None:
    """Check that the token's scopes include permission for this tool."""
    required_scope = TOOL_SCOPE_MAP.get(tool_name)
    if required_scope is None:
        raise PermissionError(f"Tool '{tool_name}' not registered in gateway")
    if required_scope not in claims.scopes:
        raise PermissionError(
            f"Agent '{claims.sub}' lacks scope '{required_scope}' for tool '{tool_name}'"
        )


# ---------------------------------------------------------------------------
# Fleet-wide rate limiting (token bucket with shared state)
# ---------------------------------------------------------------------------
@dataclass
class RateLimiter:
    """
    Per-agent, per-tool rate limiter using token bucket algorithm.
    In production, counters live in Redis Cluster for fleet-wide enforcement.
    """
    max_calls_per_minute: int = 60
    _buckets: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def check(self, agent_id: str, tool_name: str) -> None:
        key = f"{agent_id}:{tool_name}"
        now = time.time()
        window_start = now - 60.0

        # Prune old entries
        self._buckets[key] = [t for t in self._buckets[key] if t > window_start]

        if len(self._buckets[key]) >= self.max_calls_per_minute:
            raise RuntimeError(
                f"Rate limit exceeded: {agent_id} made {len(self._buckets[key])} "
                f"calls to {tool_name} in the last 60s (limit: {self.max_calls_per_minute})"
            )

        self._buckets[key].append(now)


# ---------------------------------------------------------------------------
# Circuit breaker (per backend server)
# ---------------------------------------------------------------------------
class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, fast-reject
    HALF_OPEN = "half_open" # Testing recovery


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for an MCP backend server.
    Opens after failure_threshold consecutive failures.
    Half-opens after recovery_timeout seconds.
    """
    failure_threshold: int = 5
    recovery_timeout: float = 30.0

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0

    def before_call(self) -> None:
        """Check if the circuit allows the call."""
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit moved to HALF_OPEN, allowing probe request")
            else:
                raise RuntimeError(
                    f"Circuit OPEN: backend unavailable, retry after "
                    f"{self.recovery_timeout - (time.time() - self.last_failure_time):.1f}s"
                )

    def on_success(self) -> None:
        """Reset circuit on successful call."""
        if self.state == CircuitState.HALF_OPEN:
            logger.info("Circuit recovered: HALF_OPEN -> CLOSED")
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def on_failure(self) -> None:
        """Record failure, potentially open circuit."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit OPENED after %d consecutive failures", self.failure_count
            )


# ---------------------------------------------------------------------------
# Audit logger (immutable, append-only)
# ---------------------------------------------------------------------------
@dataclass
class AuditEntry:
    timestamp: str
    agent_id: str
    client_id: str
    tool_name: str
    input_hash: str       # SHA-256 of input (don't store raw sensitive data)
    output_hash: str
    latency_ms: float
    status: str           # "success", "auth_denied", "rate_limited", "circuit_open", "error"
    error_detail: str | None = None


class AuditLogger:
    """
    Append-only audit log. In production, writes to an immutable store
    (S3 with Object Lock, or append-only Kafka topic) and streams to SIEM.
    """
    def __init__(self):
        self._entries: list[AuditEntry] = []

    def log(self, entry: AuditEntry) -> None:
        self._entries.append(entry)
        logger.info(
            "AUDIT | %s | agent=%s | tool=%s | status=%s | latency=%.1fms",
            entry.timestamp, entry.agent_id, entry.tool_name,
            entry.status, entry.latency_ms,
        )

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)


# ---------------------------------------------------------------------------
# Gateway: orchestrates all middleware
# ---------------------------------------------------------------------------
class MCPGateway:
    """
    MCP Gateway Proxy. Processes incoming tools/call requests through:
    1. Token validation (OAuth 2.1)
    2. Tool-level RBAC
    3. Rate limiting (fleet-wide)
    4. Circuit breaker check
    5. Forward to backend MCP server
    6. Audit logging
    """

    def __init__(self):
        self.rate_limiter = RateLimiter(max_calls_per_minute=60)
        self.circuit_breakers: dict[str, CircuitBreaker] = defaultdict(CircuitBreaker)
        self.audit = AuditLogger()

    async def handle_tool_call(
        self,
        bearer_token: str,
        tool_name: str,
        arguments: dict[str, Any],
        backend_server: str,
    ) -> dict[str, Any]:
        """
        Process a tool call through the full middleware chain.
        Returns the tool result or raises with a specific error.
        """
        start_time = time.time()
        agent_id = "unknown"
        client_id = "unknown"
        status = "error"
        error_detail = None
        result: dict[str, Any] = {}

        try:
            # Gate 1: Token validation
            claims = validate_token(bearer_token)
            agent_id = claims.sub
            client_id = claims.client_id

            # Gate 2: Tool-level RBAC
            authorize_tool_call(claims, tool_name)

            # Rate limiting
            self.rate_limiter.check(agent_id, tool_name)

            # Circuit breaker check
            cb = self.circuit_breakers[backend_server]
            cb.before_call()

            # Forward to backend MCP server
            # In production, this sends JSON-RPC POST to the backend server URL
            result = await self._forward_to_backend(backend_server, tool_name, arguments)

            cb.on_success()
            status = "success"

        except PermissionError as e:
            status = "auth_denied"
            error_detail = str(e)
            raise

        except RuntimeError as e:
            if "Rate limit" in str(e):
                status = "rate_limited"
            elif "Circuit OPEN" in str(e):
                status = "circuit_open"
            else:
                status = "error"
            error_detail = str(e)
            raise

        except Exception as e:
            cb = self.circuit_breakers[backend_server]
            cb.on_failure()
            status = "error"
            error_detail = str(e)
            raise

        finally:
            elapsed_ms = (time.time() - start_time) * 1000
            self.audit.log(AuditEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                agent_id=agent_id,
                client_id=client_id,
                tool_name=tool_name,
                input_hash=hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:16],
                output_hash=hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:16],
                latency_ms=elapsed_ms,
                status=status,
                error_detail=error_detail,
            ))

        return result

    async def _forward_to_backend(
        self, backend_server: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Forward tool call to backend MCP server via JSON-RPC POST.
        In production, this uses httpx/aiohttp to POST to the server URL.
        """
        # Simulated backend call
        await asyncio.sleep(0.05)  # Simulate 50ms network latency
        return {
            "content": [{"type": "text", "text": f"Result from {backend_server}/{tool_name}"}],
        }


# ---------------------------------------------------------------------------
# Usage example
# ---------------------------------------------------------------------------
async def main():
    gateway = MCPGateway()

    # Engineering agent calls GitHub tool -- should succeed
    result = await gateway.handle_tool_call(
        bearer_token="eng-agent-token",
        tool_name="github_search_issues",
        arguments={"query": "memory leak", "state": "open"},
        backend_server="github-mcp.internal",
    )
    print(f"Success: {result}")

    # Sales agent tries GitHub tool -- should fail RBAC
    try:
        await gateway.handle_tool_call(
            bearer_token="sales-agent-token",
            tool_name="github_search_issues",
            arguments={"query": "bug"},
            backend_server="github-mcp.internal",
        )
    except PermissionError as e:
        print(f"RBAC denied (expected): {e}")

    # Print audit trail
    print(f"\nAudit entries: {len(gateway.audit.entries)}")
    for entry in gateway.audit.entries:
        print(f"  {entry.status}: {entry.agent_id} -> {entry.tool_name} ({entry.latency_ms:.1f}ms)")


if __name__ == "__main__":
    asyncio.run(main())
```

### 5.4 Tool Schema Compression for Token Optimization

```python
"""
Tool schema compressor that strips verbose descriptions, examples, and
nested documentation while preserving the structural schema the LLM
needs for parameter construction.

Implements Strategy 1 from Section 3.2. Runs as a transparent proxy
between the MCP client and the LLM prompt assembler.
"""
import copy
import json
from typing import Any


def compress_tool_schema(tool: dict[str, Any], keep_top_description: bool = True) -> dict[str, Any]:
    """
    Compress a single tool schema to reduce token overhead.

    Removes:
    - Nested property descriptions (the LLM infers from property names)
    - Enum documentation (keeps enum values, removes long descriptions)
    - Examples and default value descriptions
    - Redundant type annotations in nested objects

    Preserves:
    - Tool name (always)
    - Top-level description (optional, controlled by keep_top_description)
    - Property names, types, required fields
    - Enum values (just the list, not descriptions)
    - Nested object structure
    """
    compressed = {"name": tool["name"]}

    if keep_top_description and "description" in tool:
        # Truncate top-level description to first sentence
        desc = tool["description"]
        first_sentence_end = desc.find(". ")
        if first_sentence_end > 0 and first_sentence_end < 120:
            compressed["description"] = desc[:first_sentence_end + 1]
        elif len(desc) > 120:
            compressed["description"] = desc[:120].rsplit(" ", 1)[0] + "..."
        else:
            compressed["description"] = desc

    if "inputSchema" in tool:
        compressed["inputSchema"] = _compress_schema_node(tool["inputSchema"])

    if "outputSchema" in tool:
        compressed["outputSchema"] = _compress_schema_node(tool["outputSchema"])

    return compressed


def _compress_schema_node(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively compress a JSON Schema node."""
    result: dict[str, Any] = {}

    if "type" in schema:
        result["type"] = schema["type"]

    if "required" in schema:
        result["required"] = schema["required"]

    if "enum" in schema:
        result["enum"] = schema["enum"]
        # Don't include description for enum properties

    if "properties" in schema:
        result["properties"] = {}
        for prop_name, prop_schema in schema["properties"].items():
            # Recurse but strip descriptions at nested levels
            compressed_prop = _compress_schema_node(prop_schema)
            result["properties"][prop_name] = compressed_prop

    if "items" in schema:
        result["items"] = _compress_schema_node(schema["items"])

    if schema.get("type") == "array" and "items" not in result:
        result["items"] = {"type": "string"}

    return result


def compress_tool_catalog(
    tools: list[dict[str, Any]],
    keep_top_description: bool = True,
) -> list[dict[str, Any]]:
    """Compress a full tool catalog. Returns compressed copies (originals unchanged)."""
    return [compress_tool_schema(copy.deepcopy(t), keep_top_description) for t in tools]


def measure_compression(original: list[dict[str, Any]], compressed: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure token savings (approximate: 1 token ~ 4 chars for JSON)."""
    orig_chars = len(json.dumps(original))
    comp_chars = len(json.dumps(compressed))
    approx_tokens_saved = (orig_chars - comp_chars) / 4

    return {
        "original_chars": orig_chars,
        "compressed_chars": comp_chars,
        "reduction_pct": round((1 - comp_chars / orig_chars) * 100, 1),
        "approx_tokens_saved": int(approx_tokens_saved),
    }


# Example usage
if __name__ == "__main__":
    # Simulate a verbose GitHub-style tool definition
    verbose_tools = [
        {
            "name": "search_repositories",
            "description": "Search for GitHub repositories matching a query string. "
                           "Returns repository metadata including name, description, "
                           "stars, forks, primary language, and last updated date. "
                           "Supports pagination and sorting by various criteria.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string. Supports GitHub search qualifiers "
                                       "like 'language:python', 'stars:>100', 'topic:machine-learning'.",
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["stars", "forks", "updated", "help-wanted-issues", "best-match"],
                        "description": "The field to sort results by. Default is 'best-match' which "
                                       "uses GitHub's relevance algorithm.",
                    },
                    "order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Sort order. 'desc' for descending (highest first), "
                                       "'asc' for ascending (lowest first). Default: desc.",
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Number of results per page. Maximum 100. Default 30.",
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page number for pagination. Starts at 1.",
                    },
                },
                "required": ["query"],
            },
        },
    ]

    compressed = compress_tool_catalog(verbose_tools)
    stats = measure_compression(verbose_tools, compressed)

    print("Original:", json.dumps(verbose_tools, indent=2))
    print("\nCompressed:", json.dumps(compressed, indent=2))
    print(f"\nStats: {stats['reduction_pct']}% reduction, ~{stats['approx_tokens_saved']} tokens saved")
```

### 5.5 Circuit Breaker for MCP Server Connections

The `CircuitBreaker` class is already implemented in Section 5.3 (gateway proxy) as it is an integral part of the gateway middleware chain. Here is the standalone version with additional monitoring hooks suitable for wrapping individual MCP client connections outside a gateway:

```python
"""
Standalone circuit breaker for wrapping MCP server connections.
Use this when you don't have a gateway and need per-server resilience
directly in the MCP client layer.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5        # Consecutive failures to open
    recovery_timeout: float = 30.0    # Seconds before half-open probe
    half_open_max_calls: int = 1      # Max concurrent calls in half-open
    success_threshold: int = 2        # Successes in half-open to close


@dataclass
class CircuitBreaker:
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    half_open_calls: int = 0

    # Monitoring counters
    total_calls: int = 0
    total_failures: int = 0
    total_rejections: int = 0
    state_changes: list[tuple[float, str, str]] = field(default_factory=list)

    def _transition(self, new_state: CircuitState) -> None:
        old = self.state.value
        self.state = new_state
        self.state_changes.append((time.time(), old, new_state.value))
        logger.info("Circuit breaker: %s -> %s", old, new_state.value)

    async def call(self, func: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        """Execute a function through the circuit breaker."""
        self.total_calls += 1

        # Check circuit state
        if self.state == CircuitState.OPEN:
            elapsed = time.time() - self.last_failure_time
            if elapsed < self.config.recovery_timeout:
                self.total_rejections += 1
                raise RuntimeError(
                    f"Circuit OPEN: retry in {self.config.recovery_timeout - elapsed:.1f}s "
                    f"({self.failure_count} consecutive failures)"
                )
            self._transition(CircuitState.HALF_OPEN)
            self.half_open_calls = 0
            self.success_count = 0

        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.config.half_open_max_calls:
                self.total_rejections += 1
                raise RuntimeError("Circuit HALF_OPEN: max probe calls reached, waiting for results")
            self.half_open_calls += 1

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self._transition(CircuitState.CLOSED)
                self.failure_count = 0
        else:
            self.failure_count = 0

    def _on_failure(self) -> None:
        self.failure_count += 1
        self.total_failures += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.OPEN)
        elif self.failure_count >= self.config.failure_threshold:
            self._transition(CircuitState.OPEN)

    @property
    def health_report(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "total_rejections": self.total_rejections,
            "error_rate": (
                round(self.total_failures / self.total_calls * 100, 1)
                if self.total_calls > 0 else 0.0
            ),
            "state_transitions": len(self.state_changes),
        }


# ---------------------------------------------------------------------------
# Usage: wrap MCP tool calls with circuit breaker
# ---------------------------------------------------------------------------
async def mcp_tool_call_with_circuit_breaker():
    """Example of wrapping MCP tool calls."""
    cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10.0))

    async def call_github_tool(query: str) -> dict:
        # Simulated MCP tools/call to GitHub server
        await asyncio.sleep(0.05)
        # Simulate intermittent failures
        if time.time() % 3 < 1:
            raise ConnectionError("GitHub MCP server timeout")
        return {"issues": [{"title": f"Issue matching '{query}'"}]}

    for i in range(10):
        try:
            result = await cb.call(call_github_tool, f"query-{i}")
            print(f"Call {i}: success - {result}")
        except (RuntimeError, ConnectionError) as e:
            print(f"Call {i}: {e}")
        await asyncio.sleep(0.5)

    print(f"\nHealth: {cb.health_report}")


if __name__ == "__main__":
    asyncio.run(mcp_tool_call_with_circuit_breaker())
```

### 5.6 A2A Agent Card and Task Handling

```python
"""
A2A Agent Card definition and Task lifecycle handler.
Demonstrates how an agent advertises its capabilities via Agent Card
and processes incoming A2A tasks.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent Card (served at /.well-known/agent.json)
# ---------------------------------------------------------------------------
def build_agent_card(
    name: str,
    description: str,
    capabilities: list[str],
    input_formats: list[str] | None = None,
    output_formats: list[str] | None = None,
    auth_schemes: list[str] | None = None,
    base_url: str = "https://agent.example.com",
) -> dict[str, Any]:
    """
    Build an A2A Agent Card per the A2A v1.0.0 specification.
    Agent Cards are JSON documents hosted at /.well-known/agent.json (RFC 8615).
    """
    card = {
        "name": name,
        "description": description,
        "url": base_url,
        "version": "1.0.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": True,
            "stateTransitionHistory": True,
        },
        "skills": [
            {"id": f"skill-{i}", "name": cap, "description": cap}
            for i, cap in enumerate(capabilities)
        ],
        "defaultInputModes": input_formats or ["text/plain", "application/json"],
        "defaultOutputModes": output_formats or ["text/plain", "application/json"],
        "authentication": {
            "schemes": auth_schemes or ["OAuth2"],
        },
    }
    return card


# ---------------------------------------------------------------------------
# A2A Task lifecycle
# ---------------------------------------------------------------------------
class TaskState(Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class Artifact:
    """Output produced by an agent during task execution."""
    name: str
    mime_type: str
    data: Any
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class A2ATask:
    """
    A2A Task: the core work unit with lifecycle management.
    States: submitted -> working -> [input-required] -> completed/failed/canceled
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: TaskState = TaskState.SUBMITTED
    input_message: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = ""

    def transition(self, new_state: TaskState, detail: str = "") -> None:
        """Transition task state with history tracking."""
        old_state = self.state
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.history.append({
            "from": old_state.value,
            "to": new_state.value,
            "timestamp": self.updated_at,
            "detail": detail,
        })
        logger.info("Task %s: %s -> %s (%s)", self.id[:8], old_state.value, new_state.value, detail)

    def add_artifact(self, name: str, mime_type: str, data: Any) -> None:
        self.artifacts.append(Artifact(name=name, mime_type=mime_type, data=data))

    def to_response(self) -> dict[str, Any]:
        """Serialize task state for A2A JSON-RPC response."""
        return {
            "id": self.id,
            "status": {"state": self.state.value, "timestamp": self.updated_at},
            "artifacts": [
                {"name": a.name, "parts": [{"type": a.mime_type, "data": a.data}]}
                for a in self.artifacts
            ],
            "history": self.history,
        }


# ---------------------------------------------------------------------------
# Agent task handler (processes incoming A2A tasks)
# ---------------------------------------------------------------------------
class ComplianceAgent:
    """
    Example A2A agent: reviews financial transactions for regulatory compliance.
    Uses MCP tools internally (SEC filings, regulatory DB) but exposes only
    the A2A Task interface to calling agents.
    """

    def __init__(self):
        self.agent_card = build_agent_card(
            name="Compliance Review Agent",
            description="Reviews financial transactions and recommendations for "
                        "regulatory compliance (SEC, FINRA, MiFID II)",
            capabilities=[
                "Review transactions for regulatory compliance",
                "Generate compliance reports",
                "Flag regulatory risks in investment recommendations",
            ],
            auth_schemes=["OAuth2", "mTLS"],
        )
        self._tasks: dict[str, A2ATask] = {}

    def get_agent_card(self) -> dict[str, Any]:
        """Serve at GET /.well-known/agent.json"""
        return self.agent_card

    async def handle_send_message(self, message: dict[str, Any]) -> A2ATask:
        """
        Handle A2A SendMessage JSON-RPC method.
        Creates a new task and begins processing.
        """
        task = A2ATask(input_message=message)
        self._tasks[task.id] = task
        task.transition(TaskState.WORKING, "Compliance review initiated")

        # In production, this would:
        # 1. Extract the document/transaction from the message
        # 2. Use MCP tools to query SEC filings, regulatory databases
        # 3. Apply compliance rules
        # 4. Generate findings

        # Simulated compliance review
        input_text = message.get("parts", [{}])[0].get("data", "")
        findings = self._run_compliance_check(input_text)

        task.add_artifact(
            name="compliance_report",
            mime_type="application/json",
            data=findings,
        )
        task.transition(TaskState.COMPLETED, f"Review complete: {findings['risk_level']} risk")
        return task

    def _run_compliance_check(self, content: str) -> dict[str, Any]:
        """
        Simulated compliance check. In production, this calls MCP tools:
        - SEC filing search (MCP tool)
        - Regulatory database lookup (MCP tool)
        - Internal policy document search (MCP resource)
        """
        return {
            "risk_level": "LOW",
            "findings": [
                {"rule": "FINRA 2111", "status": "PASS", "detail": "Suitability requirements met"},
                {"rule": "SEC Rule 10b-5", "status": "PASS", "detail": "No material misstatements detected"},
            ],
            "recommendation": "Transaction approved for processing",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }

    async def handle_get_task(self, task_id: str) -> A2ATask | None:
        """Handle A2A GetTask JSON-RPC method."""
        return self._tasks.get(task_id)


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
async def main():
    import asyncio

    agent = ComplianceAgent()

    # Serve agent card
    card = agent.get_agent_card()
    print("Agent Card:")
    print(json.dumps(card, indent=2))

    # Simulate incoming A2A task from another agent
    task = await agent.handle_send_message({
        "role": "user",
        "parts": [{"type": "text/plain", "data": "Review investment recommendation for Client X: "
                   "Buy 1000 shares of AAPL at market price."}],
    })

    print(f"\nTask result:")
    print(json.dumps(task.to_response(), indent=2))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Enterprise MCP Gateway with Centralized Auth, Audit, and Rate Limiting

**Problem statement**: A mid-size financial technology company operates 50 AI agents across engineering, sales, support, and compliance departments. These agents need access to 30 internal systems (CRM, issue trackers, databases, code repositories, Slack, cloud APIs). Without centralization, the company faces 1,500 potential point-to-point connections, fragmented security policies, no unified audit trail, uncontrolled API costs from runaway agents, and compliance gaps for SOC 2 and GDPR.

**Proposed architecture**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AI AGENT FLEET (50 agents)                         │
│                                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │
│  │ Eng Agent 1  │  │ Sales Bot   │  │ Support AI  │  │ Compliance Agent│   │
│  │ (Claude)     │  │ (GPT-4o)    │  │ (Gemini)    │  │ (Claude)        │   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘   │
│         │                │                │                    │             │
│         └────────────────┼────────────────┼────────────────────┘             │
│                          │                │                                  │
│                    Single gateway URL:                                       │
│                    mcp-gateway.internal.company.com                          │
└──────────────────────────┼────────────────┼──────────────────────────────────┘
                           │                │
┌──────────────────────────v────────────────v──────────────────────────────────┐
│                     MCP GATEWAY CLUSTER (K8s, HPA)                           │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  CONTROL PLANE                                                         │  │
│  │  ┌──────────────┐  ┌───────────────┐  ┌────────────────┐              │  │
│  │  │ Policy Engine │  │ Server        │  │ Analytics /    │              │  │
│  │  │ (OPA/Cerbos)  │  │ Registry      │  │ Dashboards     │              │  │
│  │  │               │  │ (approved     │  │ (Grafana)      │              │  │
│  │  │ Per-role RBAC │  │  servers only)│  │                │              │  │
│  │  └──────────────┘  └───────────────┘  └────────────────┘              │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  DATA PLANE (stateless, round-robin LB)                                │  │
│  │                                                                        │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │  │
│  │  │ OAuth 2.1     │  │ Tool-level   │  │ Fleet-wide   │  │ DLP       │ │  │
│  │  │ validation    │  │ RBAC         │  │ rate limiter │  │ filter    │ │  │
│  │  │ (PKCE, EMA)   │──│ (scope check)│──│ (Redis       │──│ (PII/     │ │  │
│  │  │              │  │              │  │  Cluster)    │  │  secrets) │ │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  └───────────┘ │  │
│  │         │                                                     │        │  │
│  │  ┌──────v───────────────────────────────────────────────────────v────┐ │  │
│  │  │ Circuit breakers (per backend)  │  Audit logger (S3 + SIEM)      │ │  │
│  │  └─────────────────────────────────┴────────────────────────────────┘ │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────┬─────────────────────────────────────────────────┘
                             │
┌────────────────────────────v─────────────────────────────────────────────────┐
│                     BACKEND MCP SERVERS (30)                                  │
│                                                                              │
│  ┌───────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ GitHub MCP │  │ Jira MCP  │  │ SFDC MCP │  │ PG MCP   │  │ Slack MCP    │ │
│  │ (93 tools) │  │           │  │          │  │          │  │              │ │
│  └───────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
                             │
                        ┌────v────┐
                        │ IdP     │  (Okta / Azure AD)
                        │ (SSO)   │
                        └─────────┘
```

**Trade-off evaluation matrix**:

| Dimension | A: Gateway (recommended) | B: Per-Agent Direct Connection | C: Service Mesh Sidecar |
|---|---|---|---|
| **Cost** | Gateway infra (~$500/mo K8s) + Redis (~$100/mo) | Zero infra cost | Service mesh license + sidecar overhead |
| **Latency** | +3-5ms per call (gateway hop) | Zero overhead | +1-2ms (sidecar proxy) |
| **Ops complexity** | Medium (single control plane) | Low initially, exponential with scale | High (Istio/Linkerd expertise) |
| **Security posture** | Centralized RBAC, DLP, audit | Per-agent, inconsistent | mTLS native, but no MCP-aware auth |
| **Scalability** | Linear (add gateway pods) | N*M explosion at 50 agents | Linear, but sidecar per pod |
| **Audit completeness** | 100% coverage (single funnel) | Fragmented, gaps guaranteed | Partial (network-level only) |
| **Time to add new tool** | Config change in registry | Coordinate across 50 agent teams | Config change + sidecar update |

**Decision rationale**: The gateway approach wins because it provides the only architecture that delivers 100% audit coverage (SOC 2 requirement), centralized RBAC (prevents scope creep), and fleet-wide rate limiting (prevents runaway agents from generating unbounded API costs). The +3-5ms latency overhead is negligible against the ~300ms per-tool-call baseline. The N*M reduction from 1,500 to 80 connections (50 agents + 30 servers) is the fundamental scalability argument. Per-agent direct connection is untenable past 10 agents. Service mesh adds mTLS but lacks MCP-specific authorization (tool-level RBAC), DLP filtering, and schema-aware routing.

**Phased rollout**:
- Phase 1 (weeks 1-4): 10-50 users, 3-5 low-risk read-only MCP servers (internal knowledge base, dev tools). Validate architecture, establish latency and error baselines.
- Phase 2 (weeks 5-12): Department-level with RBAC. Add write-capable tools (Jira, GitHub PRs). Enable DLP filtering.
- Phase 3 (weeks 13-20): Organization-wide with full audit, DLP, compliance integration. All 30 servers, all 50 agents.

---

### Scenario 2: Multi-Agent Platform Using MCP + A2A for Cross-Domain Collaboration

**Problem statement**: A financial services company needs specialized AI agents for compliance, research, trading, and client services. Complex client requests (e.g., "prepare a compliance-reviewed investment recommendation") span multiple domains and require agents to collaborate. No single agent has all the knowledge or tool access needed. Agents are built by different teams using different frameworks (LangGraph, CrewAI, Semantic Kernel). The company must maintain full provenance from user request through every agent delegation and tool call for regulatory audit.

**Proposed architecture**:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          A2A PROTOCOL LAYER                                  │
│                     (Agent-to-Agent Coordination)                           │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                      A2A REGISTRY                                    │    │
│  │     /.well-known/agent.json per agent (RFC 8615)                    │    │
│  │     Discovery, capability advertisement, auth requirements          │    │
│  │     Auth: OAuth 2.0/OIDC for inter-agent calls                      │    │
│  └──────┬──────────────┬──────────────┬──────────────┬─────────────────┘    │
│         │              │              │              │                       │
│  ┌──────v──────┐ ┌─────v──────┐ ┌────v───────┐ ┌───v──────────┐           │
│  │ Client      │ │ Research   │ │ Trading    │ │ Compliance   │           │
│  │ Agent       │ │ Agent      │ │ Agent      │ │ Agent        │           │
│  │ (Semantic   │ │ (CrewAI)   │ │ (Custom    │ │ (LangGraph)  │           │
│  │  Kernel)    │ │            │ │  Python)   │ │              │           │
│  │             │ │ Skills:    │ │ Skills:    │ │ Skills:      │           │
│  │ Orchestrate │ │ - Market   │ │ - Execute  │ │ - Regulatory │           │
│  │ multi-agent │ │   analysis │ │   orders   │ │   review     │           │
│  │ workflows   │ │ - Sector   │ │ - Risk     │ │ - Compliance │           │
│  │             │ │   reports  │ │   assess   │ │   reports    │           │
│  └──────┬──────┘ └─────┬──────┘ └────┬───────┘ └───┬──────────┘           │
│         │              │              │              │                       │
├─────────┼──────────────┼──────────────┼──────────────┼───────────────────────┤
│         │              │              │              │                       │
│         │         MCP PROTOCOL LAYER                 │                       │
│         │      (Agent-to-Tool Access)                │                       │
│         │              │              │              │                       │
│  ┌──────v──────────────v──────────────v──────────────v───────────────────┐  │
│  │                    ENTERPRISE MCP GATEWAY                              │  │
│  │          (Scenario 1 architecture applied here)                       │  │
│  │   RBAC, rate limiting, DLP, audit -- governs ALL agent tool access    │  │
│  └──────┬──────────────┬──────────────┬──────────────┬───────────────────┘  │
│         │              │              │              │                       │
│  ┌──────v──────┐ ┌─────v──────┐ ┌────v───────┐ ┌───v──────────┐           │
│  │ MCP:        │ │ MCP:       │ │ MCP:       │ │ MCP:         │           │
│  │ - CRM       │ │ - Market   │ │ - OMS API  │ │ - SEC filings│           │
│  │ - Email     │ │   data     │ │ - Risk     │ │ - RegDB      │           │
│  │ - Calendar  │ │ - News     │ │   engine   │ │ - Policy     │           │
│  │             │ │ - Papers   │ │ - Market   │ │   documents  │           │
│  └─────────────┘ └────────────┘ │   data     │ └──────────────┘           │
│                                  └────────────┘                             │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                     OBSERVABILITY                                            │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  End-to-end trace: User -> Client Agent -> A2A Task -> Research     │   │
│  │  Agent -> MCP tool call -> External API (full provenance chain)     │   │
│  │                                                                      │   │
│  │  Sinks: Splunk (SIEM) + S3 (compliance) + Grafana (ops)             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**How the workflow executes (concrete example)**:

1. User to Client Agent: "Prepare a compliance-reviewed investment recommendation for Client X in the energy sector."

2. Client Agent discovers Research Agent via Agent Card at `/.well-known/agent.json`. Sends A2A `SendMessage`: "Generate investment analysis for energy sector."

3. Research Agent (CrewAI) receives A2A Task, transitions to `working`. Internally uses MCP tools: market data API, news feed, research paper search. Produces artifact: sector analysis report. Task transitions to `completed`.

4. Client Agent receives Research artifact via A2A. Sends A2A `SendMessage` to Trading Agent: "Assess execution feasibility for this energy recommendation" (passes research artifact).

5. Trading Agent uses MCP tools: OMS API, risk engine. Returns feasibility assessment artifact.

6. Client Agent sends A2A `SendMessage` to Compliance Agent: "Review this recommendation for SEC and FINRA compliance" (passes research + trading artifacts).

7. Compliance Agent (LangGraph) uses MCP tools: SEC filing search, regulatory database. Returns compliance status artifact. Task may enter `input-required` if human review is needed.

8. Client Agent assembles final recommendation from all artifacts. Full audit trail: user request -> 3 A2A task delegations -> 8-12 MCP tool calls -> external API interactions.

**Trade-off evaluation matrix**:

| Dimension | A: MCP + A2A (recommended) | B: Single Monolithic Agent | C: Custom REST Microservices |
|---|---|---|---|
| **Cost** | Gateway + A2A registry infra | Single LLM with massive context | Custom API dev per service |
| **Latency** | 3-8s total (sequential A2A tasks) | 10-30s (huge context, one pass) | 2-5s (direct API, no LLM per hop) |
| **Ops complexity** | Medium (two protocols to manage) | Low (one agent, one deployment) | High (custom API per integration) |
| **Security posture** | Per-agent RBAC + inter-agent auth | Single blast radius, all-or-nothing | Per-service auth, proven patterns |
| **Scalability ceiling** | Each agent scales independently | Context window limit (~200K tokens) | Unlimited, but N*M connector problem |
| **Cross-vendor flexibility** | Full (any framework per agent) | Locked to one LLM provider | Framework-agnostic |
| **Regulatory audit** | Full provenance chain | Opaque single-pass reasoning | Manual correlation across services |

**Decision rationale**: MCP + A2A wins in this financial services context for three reasons. First, **regulatory compliance**: the dual-protocol architecture provides end-to-end provenance that traces from user request through each agent delegation to individual tool calls -- essential for SEC and FINRA audit requirements. The monolithic agent's opaque reasoning chain is a non-starter for regulators. Second, **blast radius containment**: each agent has access only to its domain-specific MCP tools (enforced by the gateway). A compromised Research Agent cannot access trading systems. The monolithic agent with access to all 30 tools is a single point of compromise. Third, **team autonomy**: each department owns its agent and chooses its framework (the Compliance team uses LangGraph for its deterministic state machines; the Research team uses CrewAI for its multi-agent crews). A2A's opacity principle means framework changes are invisible to other agents. The latency tradeoff (3-8s vs. the monolith's 10-30s) actually favors this approach because parallel A2A task delegation is possible when dependencies allow.
