# Module 05: Model Context Protocol (MCP)

## What Is This?

MCP is the USB-C for AI. Just like USB-C replaced the chaos of proprietary chargers, display cables, and data connectors with one universal port, MCP replaces the chaos of custom API integrations between AI models and external tools with one standardized protocol. Before MCP, every LLM application built bespoke connectors to every database, API, and file system it needed. MCP gives you a single protocol that lets any AI model talk to any tool.

Introduced by Anthropic in November 2024 and donated to the Linux Foundation's Agentic AI Foundation in December 2025, MCP is now adopted by Anthropic, OpenAI, Google, Microsoft, and AWS. Monthly SDK downloads grew from 100K at launch to 97M+ by mid-2026 -- a 970x increase in 18 months.

## Why It Matters

If you are designing or leading AI systems at scale, MCP is the protocol layer you will build on, defend in architecture reviews, and hire teams to operate. It determines how your agents access tools, how you govern that access across the enterprise, and how you avoid the N-times-M integration explosion that killed earlier attempts at tool-augmented AI. Every Director/VP AI candidate in 2026 needs to reason about MCP's security model, token economics, and production failure modes.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                           HOST                                       │
│  (Claude Desktop, Cursor, VS Code, Custom App)                      │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │   Client 1   │  │   Client 2   │  │   Client 3   │              │
│  │  (1:1 conn)  │  │  (1:1 conn)  │  │  (1:1 conn)  │              │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │
│         │                  │                  │                       │
└─────────┼──────────────────┼──────────────────┼──────────────────────┘
          │ stdio            │ stdio            │ Streamable HTTP
          │ (JSON-RPC)       │ (JSON-RPC)       │ (JSON-RPC)
          ▼                  ▼                  ▼
   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐
   │  MCP Server  │  │  MCP Server  │  │    MCP Server (Remote)   │
   │  (Local)     │  │  (Local)     │  │                          │
   │              │  │              │  │  ┌────────┐ ┌─────────┐  │
   │  Tools:      │  │  Tools:      │  │  │ OAuth  │ │ Health  │  │
   │  - db_query  │  │  - git_log   │  │  │ 2.1    │ │ Checks  │  │
   │  - db_write  │  │  - git_diff  │  │  └────────┘ └─────────┘  │
   │              │  │              │  │                          │
   │  Resources:  │  │  Resources:  │  │  Tools:                 │
   │  - schema    │  │  - repo tree │  │  - jira_create          │
   │              │  │              │  │  - jira_search          │
   │  Prompts:    │  │  Prompts:    │  │                          │
   │  - sql_help  │  │  - review    │  │  Resources:             │
   └──────────────┘  └──────────────┘  │  - sprint_board         │
                                        └──────────────────────────┘
```

### Three Roles

**Host**: The LLM application the user interacts with. Owns the conversation, model invocation, and user consent. Controls what tools the model can see, what requires user approval, and what credentials each server gets.

**Client**: A per-server connection living inside the host. One client per server, maintaining a strict 1:1 relationship. Handles wire-level protocol -- connection management, capability negotiation, message framing, request routing.

**Server**: Exposes tools (callable functions), resources (read-only data), and prompts (reusable templates). Servers are thin translation layers wrapping existing services (databases, APIs, file systems) in the MCP protocol.

### JSON-RPC 2.0 Wire Protocol

All MCP communication uses JSON-RPC 2.0. Request-response correlation via `id` fields. Notifications (no `id`) for fire-and-forget messages. Since the 2026-07-28 spec, every request carries protocol version and capabilities in a `_meta` field, making each request self-describing.

### Request-Flow Narrative (End-to-End Tool Call)

1. **Discovery**: Client calls `server/discover` (or reads cached response). Server returns its tool catalog with names, descriptions, and input schemas. Response includes `ttlMs` and `cacheScope` for caching.
2. **Schema injection**: Host injects tool definitions into the LLM's context window as function-calling schemas. Each tool costs 200-500 tokens of context.
3. **Model decision**: User sends a message. The LLM reasons over the conversation and decides to call `db_query` with `{"sql": "SELECT ..."}`.
4. **Host routing**: Host identifies which client owns `db_query`, constructs a JSON-RPC `tools/call` request with `_meta` containing protocol version and capabilities.
5. **Wire transport**: Client sends the request over stdio (local) or Streamable HTTP (remote). HTTP requests include `Mcp-Method` and `Mcp-Name` headers for gateway routing without body parsing.
6. **Server execution**: Server validates the input against its schema, executes the underlying database query, and returns the result.
7. **Response delivery**: Result flows back through client to host. Host feeds the tool response into the LLM's context for the next generation step.
8. **Multi-round**: If the server needs more information, it returns `resultType: "input_required"` with an opaque `requestState` blob (MRTR pattern). Client collects answers, retries with `inputResponses`.

---

## Part 2: Core Mechanics & Algorithms

### Three Server Primitives

| Primitive | Control | Purpose | Discovery | Execution |
|---|---|---|---|---|
| **Tools** | Model-controlled | Executable functions (the workhorse) | `tools/list` | `tools/call` |
| **Resources** | Application-controlled | Read-only data addressed by URI | `resources/list` | `resources/read` |
| **Prompts** | User-controlled | Parameterized instruction templates | `prompts/list` | `prompts/get` |

**Tools** are the most-used primitive. Defined by name, description, and `inputSchema` (JSON Schema). The LLM decides when to invoke them. Example: `weather_current` with `location` (required) and `units` (optional, default metric).

**Resources** provide contextual data without triggering operations. Each has a unique URI (e.g., `file:///data/schema.json`), name, description, and MIME type. Resource Templates enable dynamic access via URI parameters (e.g., `travel://activities/{city}/{category}`).

**Prompts** move prompt engineering into the server that owns the domain. The least-used and most underrated primitive. Explicitly invoked by the user or client, not auto-executed. Can dynamically reference available resources and tools for context-aware templates.

### Transport Layers

**Stdio (Local)**: Host spawns server as a child process. JSON-RPC over stdin/stdout. No network overhead. Cannot be shared across machines. Critical constraint: stdout is exclusively reserved for protocol messages; all logging must go to stderr.

**Streamable HTTP (Remote)**: Introduced 2025-03-26, replacing deprecated HTTP+SSE. Uses HTTP POST for client-to-server messages with optional Server-Sent Events for streaming responses. Standard HTTP authentication (bearer tokens, API keys, OAuth). POST requests include `Mcp-Method` and `Mcp-Name` headers for gateway routing.

**SSE (Deprecated)**: Original transport from 2024-11-05. Formally deprecated in 2026-07-28 with a 12-month removal window. Do not build on this.

### Capability Negotiation Lifecycle

**Pre-2026-07-28 (Stateful)**: Client sends `initialize` with supported version and capabilities. Server responds with its capabilities. Client sends `initialized` notification. Session begins. State is tied to the connection -- sticky sessions or shared session stores required for scaling.

**2026-07-28+ (Stateless)**: No handshake. Each request carries everything in `_meta`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {"name": "my-app", "version": "1.0"},
      "io.modelcontextprotocol/clientCapabilities": {"elicitation": {}}
    },
    "name": "db_query",
    "arguments": {"sql": "SELECT * FROM users LIMIT 10"}
  }
}
```

Any server instance can handle any request behind a plain round-robin load balancer. Serverless deployments (Lambda, Knative) become viable. Application state travels in tool arguments or server-minted handles (e.g., `basket_id`).

### MCP vs Function Calling vs A2A

These are complementary layers, not competitors:

| Layer | Protocol | Direction | Purpose |
|---|---|---|---|
| Foundation | Function Calling | LLM --> Host code | LLM emits structured arguments; host executes |
| Vertical | MCP | Agent --> Tools/Data | Standardized tool/data access for one agent |
| Horizontal | A2A | Agent <--> Agent | Peer-to-peer agent collaboration and delegation |

MCP does not replace function calling. Under the hood, an MCP client converts discovered tools into function-calling schemas for the model. MCP adds discovery, transport, and capability negotiation on top. A2A (Google, April 2025, 1.0 in March 2026) standardizes how agents discover and collaborate with each other. Production reality in 2026: most stacks run both -- A2A between agents, MCP from each agent to its tools. Both are governed by AAIF under the Linux Foundation (190 member organizations).

### Server Implementation Pattern (Python SDK)

A minimal but complete MCP server in Python uses the `mcp` SDK. The decorator pattern registers tools and resources:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("inventory-server")

@mcp.tool()
async def search_products(query: str, category: str = "all", limit: int = 10) -> str:
    """Search product inventory by keyword and optional category."""
    results = await db.execute(
        "SELECT name, sku, stock FROM products WHERE name ILIKE $1 AND ($2 = 'all' OR category = $2) LIMIT $3",
        f"%{query}%", category, limit
    )
    return json.dumps([dict(r) for r in results])

@mcp.resource("inventory://schema")
async def get_schema() -> str:
    """Return the product database schema for context."""
    return open("schema.sql").read()

@mcp.prompt()
async def inventory_report(category: str) -> str:
    """Generate a prompt for creating an inventory analysis report."""
    return f"Analyze the inventory for category '{category}'. Identify low-stock items (below 10 units), overstocked items (above 1000 units), and recommend reorder quantities."
```

Run locally: `mcp.run(transport="stdio")`. Run remotely: `mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)`.

---

## Part 3: Token Economics & NFR Analysis

### Tool Definition Overhead

Each MCP tool definition consumes 200-500 tokens in the LLM's context window. This is a fixed cost per request that never disappears.

| Setup | Token Overhead | Context Impact |
|---|---|---|
| GitHub MCP server (93 tools) | 55,000 tokens | 28% of 200K window gone before user speaks |
| GitHub + Slack + Sentry (3 servers) | 143,000 tokens | 72% of 200K window consumed on idle |
| Typical production (5 servers x 30 tools) | 30,000-60,000 tokens | 15-30% of context permanently unavailable |
| Single complex Gmail tool | 820 tokens | Nested parameters cost 5-10x simple ones |
| 508-tool benchmark | 1,150,000 tokens | $377 per benchmark round in pure metadata |

Microsoft Research documented that large tool spaces lower agent performance by up to 85%. Every 10K tokens of tool schemas removes approximately 5 pages of reasoning capacity from the model.

### Financial Cost at Scale

At Claude Sonnet pricing ($3/M input tokens):

| Daily Volume | Schema Overhead (90K tokens) | Monthly Cost (Overhead Only) |
|---|---|---|
| 100 requests/day | $0.27/request | $810/month |
| 1,000 requests/day | $0.27/request | $8,100/month |
| 10,000 requests/day | $0.27/request | $81,000/month |

These are pure overhead costs -- the actual query and response tokens are additional.

### Latency SLA Targets

| Metric | Target | Reality in Production |
|---|---|---|
| Message serialization | < 10ms | Unoptimized JSON adds 50-100ms |
| Tool call p50 | < 50ms | Achievable with local stdio |
| Tool call p95 | < 300ms | Comfortable range |
| Tool call p99 | < 1,000ms | Exceeding this correlates with user churn |
| MCP baseline | 300-800ms | Cannot be cached away (per multiple reports) |
| Bifrost gateway overhead | 11 microseconds | Go-based, sub-3ms MCP latency, 5,000+ RPS |

Documented incident: a 10-connection pool saturated by 7 agents in 2 seconds; p95 went from 180ms to 4.2 seconds. GitHub Copilot measured a 400ms latency reduction after cutting tools from 40 to 13.

### Optimization Strategies

**1. Search-First Discovery**: Replace upfront schema dump with on-demand retrieval. Agent starts with 2-3 meta-tools (`search_tools` + `execute_tool`). Claude Code activates this automatically when tool descriptions exceed 10% of context.

**2. Code Execution Mode**: Single `execute_code` tool replaces the full catalog. Reduces 150K tokens to 2K (98.7% reduction). Addresses both schema bloat and response bloat.

**3. Tiered Schema Discovery**: Discovery tier (name + one-line description + parameter types) vs. invocation tier (full schema injected on-demand when the model selects a tool).

**4. Gateway Aggregation**: Cloudflare reference architecture: 52 backend tools collapsed into 2 portal tools at ~600 tokens (94% reduction).

**5. Cacheable List Results** (2026-07-28 spec): `tools/list` responses carry `ttlMs` and `cacheScope`. Deterministic ordering keeps upstream prompt caches stable across reconnects.

### Availability & RPO/RTO

- RPO (Recovery Point Objective): Zero for stateless MCP (2026-07-28) -- no protocol state to lose.
- RTO (Recovery Time Objective): Next-request for stateless servers. Client retries and the next server instance handles it. Application-level state (e.g., a multi-step workflow) requires the model to re-derive state from tool arguments or server-minted handles.
- 73% of production outages start at the transport layer, cascading upward.
- 20-30% recovery failure rate without explicit retry handling.

---

## Part 4: Distributed Resilience & Security

### Zero-Trust MCP

NSA advisory (May 2026) and industry guidance converge on treating every tool call as potentially unauthorized:

1. **Authenticate per request**, not per session. The 2026-07-28 stateless core makes this natural -- there is no session to inherit trust from.
2. **Least privilege scoping**: Per-tool, per-action tokens. Not one shared credential across the server.
3. **Continuous validation**: Re-verify on every call. Token expiry, scope checks, rate limits.
4. **Explicit deny by default**: Tools are invisible until explicitly granted.
5. **Treat every MCP server as an untrusted third party**: Validate tool outputs against a schema before passing to downstream components.
6. **Signed provenance checks** for dynamic server discovery.

### Authorization Evolution

| Spec | Auth Model |
|---|---|
| 2024-11-05 | No standardized auth. Roll your own. |
| 2025-03-26 | OAuth 2.1 + PKCE. Dynamic Client Registration (DCR). |
| 2025-06-18 | Servers classified as OAuth Resource Servers. RFC 8707 + RFC 9728 PRM mandatory. |
| 2026-07-28 | DCR deprecated for CIMD. RFC 9207 issuer validation. Credential binding. Stateless core. |

**Enterprise-Managed Authorization (EMA)**: Solves per-user, per-server auth at scale. Employees access all MCP servers through a single SSO login via enterprise IdPs (first: Okta). Security teams get centralized audit trail and consistent policy enforcement. Users cannot inadvertently connect personal accounts to work tools.

### OWASP MCP Top 10

| ID | Risk | Mitigation |
|---|---|---|
| MCP01 | Token Mismanagement & Secret Exposure | Vault-backed credential rotation; never embed secrets in tool arguments |
| MCP02 | Excessive Permissions | Per-tool scope tokens; principle of least privilege |
| MCP03 | Command Injection | Input validation + parameterized queries; never pass raw tool args to shells |
| MCP04 | Supply Chain Compromise | Pin server versions; signed provenance; AIBOM inventory |
| MCP05 | Tool Poisoning | Verify tool definitions against known-good manifests; code review |
| MCP06 | Prompt Injection & Intent Flow | Separate system/user/tool context; output validation |
| MCP07 | Insufficient Authentication | OAuth 2.1 + PKCE mandatory; EMA for enterprise |
| MCP08 | Audit & Logging Gaps | Log every tool call with correlation IDs; immutable audit trail |
| MCP09 | Shadow MCP Servers | Maintain inventory of all deployed servers; network-level controls |
| MCP10 | Context Over-Sharing | Gateway-level response filtering; output schema validation |

### CVE Landscape (Real Vulnerabilities)

| CVE | CVSS | Impact |
|---|---|---|
| CVE-2025-6514 (mcp-remote) | 9.6 | First real-world RCE on client OS via untrusted remote MCP server (437K+ downloads) |
| CVE-2026-33032 (MCPwn) | 9.8 | Missing auth for command execution; 2,600+ exposed instances |
| CVE-2026-26118 (Azure MCP) | 8.8 | SSRF stealing managed identity tokens |

Broader landscape: 40+ CVEs against MCP SDKs and servers (Jan-Apr 2026). VIPER-MCP framework scanned ~40,000 repos, found 106 zero-day vulnerabilities, produced 67 CVEs. Censys identified 12,520 publicly exposed MCP services with ~40% unauthenticated. Only 8.5% of remote servers implement the mandatory OAuth 2.1 + PKCE standard. GitGuardian found 24,008 secrets in MCP config files on public GitHub; 2,117 still valid.

### PII & Audit Governance

Tool responses can contain PII, financial data, or sensitive business information with no protocol-level redaction mechanism. Enterprise mitigation:

- **Gateway-level response filtering**: Strip sensitive fields before they reach the LLM context.
- **Output schema validation**: Define allowed response shapes; reject or redact anything outside the schema.
- **Immutable audit trail**: Log every tool call, arguments, and response with correlation IDs and tenant context.
- **Auth0 Token Vault pattern**: Agent trades internal token for API token just-in-time. Secrets stay in vault; agent gets just-enough access. No stored refresh tokens in agent context.

### Connection Lifecycle & Failover

**Three-tier health checks**:
- `/health` (Liveness): Returns 200 if process is up. Lightweight.
- `/ready` (Readiness): Checks critical dependencies (database, rate limits). Failing means load balancer stops sending traffic.
- `client.ping()`: Protocol-level transport health verification.

**Reconnection**: No automatic reconnection when MCP servers disconnect mid-session in most implementations. The 2026-07-28 stateless core mitigates this -- since each request is self-contained, a server restart is transparent. Streamable HTTP supports resumability via `Last-Event-ID`. Best practice: exponential backoff with jitter on reconnection attempts.

### MCP Failure Taxonomy

Not all failures are equal. Effective resilience requires classifying failures by type
and responding differently to each:

| Type | Examples | Detection | Response |
|------|----------|-----------|----------|
| **Transient** | Transport timeout, token refresh failure, server overloaded | HTTP 503, connection reset, OAuth 401 with valid refresh | Retry with exponential backoff (max 3), re-authenticate |
| **Permanent** | Server decommissioned, OAuth scope revoked, tool removed from server | HTTP 404/410, capability negotiation fails, repeated 403 | Remove from tool registry, alert admin, degrade gracefully |
| **Protocol** | Schema drift (server updated tool schema), version mismatch | JSON-RPC parse error, unexpected response format | Re-discover tools (`tools/list`), update client schema cache |
| **Adversarial** | Malicious tool response, prompt injection via tool output | Output validation failure, canary token triggered | Quarantine server, block tool, alert security team |

The taxonomy drives automation: transient failures retry, permanent failures degrade,
protocol failures re-discover, adversarial failures isolate. Without this classification,
all failures get the same retry logic -- which means retrying permanent failures (wasting
time) and retrying adversarial failures (amplifying the attack).

### Circuit Breaker for MCP Servers

Track failures per server independently. One failing server should not block access to
other healthy servers.

**State machine**:
- **Closed** (normal): All requests flow through. Track consecutive failures.
- **Open** (tripped): After 3 consecutive failures in a 60-second window, open the
  circuit for that server. All calls to that server immediately return a degraded
  response without attempting the call. Prevents cascading timeouts.
- **Half-open** (probing): Every 30 seconds, send a lightweight probe (`ping` or
  `tools/list`) to the tripped server. If the probe succeeds, increment a success
  counter.
- **Close** (recovery): After 2 consecutive successful probes, close the circuit and
  resume normal traffic.

Implementation must be **per-server, not global**. A global circuit breaker means one
flaky Jira server takes down Salesforce, Stripe, and Google Calendar access. Per-server
breakers isolate blast radius.

### Durable Execution for MRTR Flows

Multi-Round Tool Resolution (MRTR) flows span multiple request-response cycles -- the
server returns `resultType: "input_required"` and the client must collect answers and
retry. These multi-step flows are vulnerable to crashes mid-execution.

**Checkpoint strategy**:
- Checkpoint after each successful tool call result. Store the checkpoint in durable
  storage (Redis, DynamoDB) keyed by a flow ID.
- On crash or restart, resume from the last checkpoint rather than replaying all tool
  calls from the beginning. Replaying is dangerous because tool calls may not be
  idempotent -- a `create_ticket` call replayed creates a duplicate ticket.
- Use **idempotency keys** for tool calls that have side effects (write operations,
  API mutations). Pass a client-generated UUID with each mutating call. The server
  uses the key to deduplicate: if it has already processed that key, it returns the
  cached result instead of re-executing.

Without durable execution, a crash at step 4 of a 6-step MRTR flow forces a full
restart. With side-effecting tools, that restart may create duplicate records, send
duplicate notifications, or charge a customer twice.

### Fallback Strategies

When an MCP server is unavailable (circuit breaker open, permanent failure, or
maintenance window), the system must degrade gracefully rather than fail completely:

1. **Graceful degradation**: Inform the LLM that the tool is temporarily unavailable.
   Let the model proceed without it -- a well-prompted model can acknowledge the
   limitation and still provide partial help. Example: "Salesforce is temporarily
   unavailable. I can still help with your Jira and Stripe questions."
2. **Static fallback**: For read-only tools, serve cached tool responses. If the
   `get_product_catalog` tool is down, return the last-known catalog (with a staleness
   warning). Not suitable for write operations or real-time data.
3. **Alternative server**: Route to a backup MCP server if one is available. Requires
   maintaining standby server instances or using a different provider for the same
   capability (e.g., a backup calendar MCP server).
4. **Human escalation**: When no automated fallback exists, pause the flow and ask the
   user to perform the action manually. Example: "I cannot access Jira right now. Could
   you create the ticket manually and share the ticket ID with me?"

The fallback hierarchy should be configured per tool, not globally. A read-only tool
(catalog lookup) can safely use static fallback. A write tool (create ticket) should
escalate to the user rather than silently failing.

---

## Part 5: Production Enterprise Code

### MCP Server with Tools, Resources, and Validation

```python
"""Production MCP server for a customer data platform.
Demonstrates tool + resource implementation with input validation,
structured error handling, and audit logging.
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("cdp-mcp-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

mcp = FastMCP("customer-data-platform", version="1.0.0")

# --- Simulated data layer (replace with real DB in production) ---
CUSTOMERS = {
    "C-1001": {"name": "Acme Corp", "tier": "enterprise", "arr": 240000, "region": "NA"},
    "C-1002": {"name": "Globex Inc", "tier": "mid-market", "arr": 48000, "region": "EMEA"},
    "C-1003": {"name": "Initech", "tier": "startup", "arr": 12000, "region": "NA"},
}

TICKETS = {
    "C-1001": [
        {"id": "T-5001", "status": "open", "priority": "high", "subject": "SSO integration failing"},
        {"id": "T-5002", "status": "resolved", "priority": "medium", "subject": "Billing discrepancy"},
    ],
    "C-1002": [
        {"id": "T-5003", "status": "open", "priority": "low", "subject": "Feature request: bulk export"},
    ],
}


def _audit_log(action: str, params: dict[str, Any], result_summary: str) -> None:
    """Write structured audit entry. In production, ship to immutable log store."""
    log.info(
        json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "params": params,
            "result_summary": result_summary,
        })
    )


@mcp.tool()
async def lookup_customer(customer_id: str) -> str:
    """Look up a customer by ID. Returns account details including tier, ARR, and region."""
    customer_id = customer_id.strip().upper()
    if not customer_id.startswith("C-") or not customer_id[2:].isdigit():
        return json.dumps({"error": "Invalid customer_id format. Expected C-NNNN."})

    customer = CUSTOMERS.get(customer_id)
    if not customer:
        _audit_log("lookup_customer", {"customer_id": customer_id}, "not_found")
        return json.dumps({"error": f"Customer {customer_id} not found."})

    _audit_log("lookup_customer", {"customer_id": customer_id}, "found")
    return json.dumps({"customer_id": customer_id, **customer})


@mcp.tool()
async def search_customers(region: str = "all", tier: str = "all", min_arr: int = 0) -> str:
    """Search customers by region, tier, and minimum ARR. Returns matching accounts."""
    valid_regions = {"all", "NA", "EMEA", "APAC"}
    valid_tiers = {"all", "enterprise", "mid-market", "startup"}

    if region.upper() not in {r.upper() for r in valid_regions}:
        return json.dumps({"error": f"Invalid region. Must be one of: {sorted(valid_regions)}"})
    if tier.lower() not in {t.lower() for t in valid_tiers}:
        return json.dumps({"error": f"Invalid tier. Must be one of: {sorted(valid_tiers)}"})

    results = []
    for cid, c in CUSTOMERS.items():
        if region != "all" and c["region"] != region.upper():
            continue
        if tier != "all" and c["tier"] != tier.lower():
            continue
        if c["arr"] < min_arr:
            continue
        results.append({"customer_id": cid, **c})

    _audit_log("search_customers", {"region": region, "tier": tier, "min_arr": min_arr}, f"{len(results)} results")
    return json.dumps(results)


@mcp.tool()
async def get_open_tickets(customer_id: str) -> str:
    """Get all open support tickets for a customer. Returns ticket ID, priority, and subject."""
    customer_id = customer_id.strip().upper()
    tickets = TICKETS.get(customer_id, [])
    open_tickets = [t for t in tickets if t["status"] == "open"]
    _audit_log("get_open_tickets", {"customer_id": customer_id}, f"{len(open_tickets)} open")
    return json.dumps(open_tickets)


@mcp.resource("cdp://schema/customers")
async def customer_schema() -> str:
    """Return the customer data schema for LLM context grounding."""
    return json.dumps({
        "table": "customers",
        "columns": {
            "customer_id": "string (C-NNNN format)",
            "name": "string",
            "tier": "enum: enterprise | mid-market | startup",
            "arr": "integer (USD, annual recurring revenue)",
            "region": "enum: NA | EMEA | APAC",
        },
    })


if __name__ == "__main__":
    import sys
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
    else:
        mcp.run(transport="stdio")
```

### MCP Client: Discovery and Tool Invocation

```python
"""MCP client that discovers tools from a server and invokes them.
Works with both stdio (local) and Streamable HTTP (remote) servers.
"""
import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


async def run_stdio_client(server_command: str, server_args: list[str]) -> None:
    """Connect to a local MCP server via stdio, discover tools, and call one."""
    server_params = StdioServerParameters(command=server_command, args=server_args)

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # Step 1: Discover available tools
            tools_response = await session.list_tools()
            print(f"Discovered {len(tools_response.tools)} tools:")
            for tool in tools_response.tools:
                print(f"  - {tool.name}: {tool.description}")

            # Step 2: Call a tool
            result = await session.call_tool(
                "lookup_customer",
                arguments={"customer_id": "C-1001"},
            )
            print(f"\nResult: {result.content[0].text}")

            # Step 3: Search with filters
            search_result = await session.call_tool(
                "search_customers",
                arguments={"region": "NA", "min_arr": 20000},
            )
            print(f"Search: {search_result.content[0].text}")

            # Step 4: Discover and read resources
            resources = await session.list_resources()
            for resource in resources.resources:
                content = await session.read_resource(resource.uri)
                print(f"\nResource [{resource.name}]: {content.contents[0].text}")


async def run_http_client(server_url: str) -> None:
    """Connect to a remote MCP server via Streamable HTTP."""
    async with streamablehttp_client(server_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"Remote server: {len(tools.tools)} tools available")
            for tool in tools.tools:
                print(f"  - {tool.name}")


if __name__ == "__main__":
    # Local: python mcp_client.py stdio python server.py
    # Remote: python mcp_client.py http http://localhost:8080/mcp
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if mode == "http":
        asyncio.run(run_http_client(sys.argv[2]))
    else:
        asyncio.run(run_stdio_client(sys.argv[2], sys.argv[3:]))
```

### Multi-Tenant MCP Gateway

```python
"""MCP gateway that routes tool calls to tenant-specific server instances.
Handles authentication, tenant isolation, rate limiting, and audit logging.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("mcp-gateway")


@dataclass
class TenantConfig:
    tenant_id: str
    allowed_tools: set[str]
    rate_limit_per_minute: int
    server_url: str
    api_key: str  # In production: fetched from vault, never stored in memory


@dataclass
class RateLimiter:
    """Token bucket rate limiter per tenant."""
    window_calls: dict[str, list[float]] = field(default_factory=dict)

    def check(self, tenant_id: str, limit: int) -> bool:
        now = time.time()
        calls = self.window_calls.setdefault(tenant_id, [])
        # Evict calls older than 60 seconds
        self.window_calls[tenant_id] = [t for t in calls if now - t < 60]
        if len(self.window_calls[tenant_id]) >= limit:
            return False
        self.window_calls[tenant_id].append(now)
        return True


class MCPGateway:
    """Routes MCP tool calls through tenant-aware security and governance layers.

    Architecture:
        Agent --> Gateway --> [Auth] --> [Rate Limit] --> [Tool Scope] --> Tenant MCP Server
    """

    def __init__(self) -> None:
        self.tenants: dict[str, TenantConfig] = {}
        self.rate_limiter = RateLimiter()
        self.audit_log: list[dict[str, Any]] = []

    def register_tenant(self, config: TenantConfig) -> None:
        self.tenants[config.tenant_id] = config
        log.info(f"Registered tenant {config.tenant_id} with {len(config.allowed_tools)} tools")

    def authenticate(self, tenant_id: str, bearer_token: str) -> bool:
        """Validate bearer token against tenant config.
        In production: verify JWT signature, check expiry, validate scopes.
        """
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            return False
        return bearer_token == tenant.api_key  # Simplified; use JWT validation in production

    async def route_tool_call(
        self, tenant_id: str, bearer_token: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Route a tool call through the gateway's security layers."""
        request_start = time.time()

        # Layer 1: Authentication
        if not self.authenticate(tenant_id, bearer_token):
            self._audit("auth_failed", tenant_id, tool_name, {})
            return {"error": "Authentication failed", "code": 401}

        tenant = self.tenants[tenant_id]

        # Layer 2: Tool scope enforcement (Zero-Trust: explicit allow only)
        if tool_name not in tenant.allowed_tools:
            self._audit("tool_denied", tenant_id, tool_name, {})
            return {"error": f"Tool '{tool_name}' not permitted for tenant {tenant_id}", "code": 403}

        # Layer 3: Rate limiting
        if not self.rate_limiter.check(tenant_id, tenant.rate_limit_per_minute):
            self._audit("rate_limited", tenant_id, tool_name, {})
            return {"error": "Rate limit exceeded", "code": 429}

        # Layer 4: Forward to tenant's MCP server
        result = await self._forward_to_server(tenant, tool_name, arguments)

        # Layer 5: Response filtering (strip sensitive fields before returning)
        filtered = self._filter_response(result, tenant_id)

        latency_ms = (time.time() - request_start) * 1000
        self._audit("tool_call", tenant_id, tool_name, {"latency_ms": round(latency_ms, 1)})
        return filtered

    async def _forward_to_server(
        self, tenant: TenantConfig, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Forward the tool call to the tenant's MCP server.
        In production: use MCP client SDK with connection pooling.
        """
        # Simulated server response for demonstration
        return {"status": "success", "data": {"tool": tool_name, "result": "executed"}}

    def _filter_response(self, response: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        """Strip PII or sensitive fields from tool responses based on tenant policy.
        In production: apply tenant-specific redaction rules.
        """
        return response

    def _audit(self, event: str, tenant_id: str, tool: str, extra: dict[str, Any]) -> None:
        entry = {
            "timestamp": time.time(),
            "event": event,
            "tenant_id": tenant_id,
            "tool": tool,
            **extra,
        }
        self.audit_log.append(entry)
        log.info(json.dumps(entry))

    def list_tools_for_tenant(self, tenant_id: str) -> list[str]:
        """Return only the tools this tenant is allowed to see.
        Implements per-tenant tool visibility (MCP02 mitigation).
        """
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            return []
        return sorted(tenant.allowed_tools)


async def main() -> None:
    gateway = MCPGateway()

    # Register two tenants with different tool scopes
    gateway.register_tenant(TenantConfig(
        tenant_id="tenant-acme",
        allowed_tools={"lookup_customer", "search_customers", "get_open_tickets"},
        rate_limit_per_minute=100,
        server_url="http://mcp-acme:8080/mcp",
        api_key="acme-secret-key",
    ))
    gateway.register_tenant(TenantConfig(
        tenant_id="tenant-globex",
        allowed_tools={"lookup_customer", "search_customers"},  # No ticket access
        rate_limit_per_minute=50,
        server_url="http://mcp-globex:8080/mcp",
        api_key="globex-secret-key",
    ))

    # Acme can access tickets
    result = await gateway.route_tool_call(
        "tenant-acme", "acme-secret-key", "get_open_tickets", {"customer_id": "C-1001"}
    )
    print(f"Acme tickets: {result}")

    # Globex cannot access tickets (tool scope enforcement)
    result = await gateway.route_tool_call(
        "tenant-globex", "globex-secret-key", "get_open_tickets", {"customer_id": "C-1001"}
    )
    print(f"Globex tickets: {result}")  # 403 error

    # Wrong credentials (auth failure)
    result = await gateway.route_tool_call(
        "tenant-acme", "wrong-key", "lookup_customer", {"customer_id": "C-1001"}
    )
    print(f"Bad auth: {result}")  # 401 error


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Enterprise AI Coding Assistant with MCP Tool Governance

**Problem Statement**: A fintech company (2,000 engineers, 15 product teams) wants to deploy an AI coding assistant (like Cursor or a custom IDE plugin) across the organization. Engineers need access to internal tools: code search, CI/CD pipelines, incident management, database schemas, and documentation. Security requirements: SOC 2 Type II, no cross-team data leakage (Team A cannot query Team B's production database), audit trail for every tool invocation, and ability to revoke tool access per-team in under 5 minutes. Target: <500ms p95 for tool calls, 99.9% availability, <$20K/month infrastructure.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────────────┐
│                        DEVELOPER IDE (Host)                          │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  MCP Client (embedded in IDE plugin)                          │  │
│  │  Connects to gateway via Streamable HTTP + OAuth 2.1          │  │
│  └────────────────────────┬─────────────────────────────────────┘  │
└───────────────────────────┼──────────────────────────────────────────┘
                            │ HTTPS + Bearer Token
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    MCP GATEWAY (Aggregation + Multi-Tenant)           │
│                                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────┐  │
│  │ OAuth 2.1   │  │ Team-Scoped  │  │ Rate       │  │ Audit     │  │
│  │ + EMA       │  │ Tool ACLs    │  │ Limiter    │  │ Logger    │  │
│  │ (Okta SSO)  │  │ (per-team    │  │ (per-user  │  │ (immutable│  │
│  │             │  │  visibility) │  │  per-tool)  │  │  append)  │  │
│  └─────────────┘  └──────────────┘  └────────────┘  └───────────┘  │
│                            │                                         │
│  ┌─────────────────────────┼─────────────────────────────────────┐  │
│  │         TIERED SCHEMA DISCOVERY                               │  │
│  │  Initial: 8-12 tool summaries (~2,400 tokens)                │  │
│  │  On-demand: full schema injected when model selects a tool    │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                            │                                         │
└────────────┬───────────────┼──────────────┬──────────────────────────┘
             │               │              │
             ▼               ▼              ▼
      ┌────────────┐  ┌────────────┐  ┌────────────┐
      │ Code Search│  │ CI/CD      │  │ Incident   │
      │ MCP Server │  │ MCP Server │  │ MCP Server │
      │ (stdio,    │  │ (HTTP,     │  │ (HTTP,     │
      │  local)    │  │  shared)   │  │  shared)   │
      └────────────┘  └────────────┘  └────────────┘
```

**Trade-Off Matrix**:

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **A: Direct MCP connections (no gateway)** | Simplest. Each IDE connects to each server directly. | No centralized auth or audit. Cross-team leakage risk. Tool revocation requires config push to 2,000 machines. SOC 2 auditors reject this. | Rejected |
| **B: Aggregation gateway with EMA** | Centralized auth via Okta SSO. Per-team tool ACLs. Single audit stream. Tool revocation in seconds (ACL change). Tiered discovery cuts token overhead 94%. | Gateway is a single point of failure (mitigated by 3-replica deployment). Adds 10-50ms latency per call. | **Selected** |
| **C: Per-team dedicated MCP servers** | Perfect isolation. No gateway bottleneck. | 15 teams x 5 servers = 75 server deployments. Operational nightmare. Cross-team tools (shared CI/CD) require duplication. $60K+/month infrastructure. | Rejected |

**Decision Rationale**: The aggregation gateway with EMA provides the centralized governance SOC 2 requires without the operational explosion of per-team deployments. Tiered schema discovery keeps token overhead under 3,000 tokens (vs. 30,000+ with full schemas), preserving reasoning capacity. EMA via Okta gives security teams a single pane for access control. Tool revocation is an ACL change in the gateway -- propagated in under 5 minutes. Three gateway replicas behind a load balancer achieve 99.9% availability. The 10-50ms gateway overhead keeps p95 well under the 500ms target. Total cost: ~$12K/month (3 gateway pods + 8 MCP server pods + Okta + logging infrastructure).

---

### Scenario 2: Customer-Facing AI Agent with MCP Tool Access Across SaaS Integrations

**Problem Statement**: A customer success platform wants to ship an AI agent that helps end-users manage their accounts. The agent needs to access 6 external SaaS tools (Salesforce, Zendesk, Slack, Stripe, Jira, Google Calendar) on behalf of each customer. Each customer has their own credentials for each service. The agent must never access Customer A's Salesforce data when serving Customer B. Must handle OAuth token refresh, service outages (Salesforce SLA: 99.9%, Zendesk: 99.95%), and graceful degradation. Target: 10,000 end-users, <3s end-to-end response time, <$50K/month total.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────────────┐
│                     CUSTOMER-FACING CHAT UI                          │
│                            │                                         │
│                            ▼                                         │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  ORCHESTRATOR (Agent Loop)                                    │  │
│  │  - Identifies user intent                                     │  │
│  │  - Selects tools via search-first discovery (3 meta-tools)    │  │
│  │  - Enforces max 3 tool calls per turn                        │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
│                              │                                       │
│  ┌──────────────────────────┼────────────────────────────────────┐  │
│  │           MCP GATEWAY (Multi-Tenant, Per-User Isolation)      │  │
│  │                                                                │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐  │  │
│  │  │ User Identity │  │ Token Vault  │  │ Circuit Breaker     │  │  │
│  │  │ (JWT claim   │  │ (per-user    │  │ (per-service, trips │  │  │
│  │  │  -> tenant)  │  │  per-service │  │  at 50% error rate  │  │  │
│  │  │              │  │  OAuth mgmt) │  │  in 60s window)     │  │  │
│  │  └──────────────┘  └──────────────┘  └─────────────────────┘  │  │
│  │                              │                                 │  │
│  └──────────────────────────────┼─────────────────────────────────┘  │
│                                 │                                    │
└─────────────────────────────────┼────────────────────────────────────┘
                                  │
          ┌───────────┬───────────┼───────────┬───────────┐
          ▼           ▼           ▼           ▼           ▼
   ┌───────────┐┌───────────┐┌───────────┐┌───────────┐┌──────────┐
   │Salesforce ││ Zendesk   ││  Stripe   ││   Jira    ││ Google   │
   │MCP Server ││MCP Server ││MCP Server ││MCP Server ││ Calendar │
   │           ││           ││           ││           ││MCP Server│
   │ 8 tools   ││ 6 tools   ││ 5 tools   ││ 7 tools   ││ 4 tools  │
   └───────────┘└───────────┘└───────────┘└───────────┘└──────────┘
```

**Trade-Off Matrix**:

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **A: All 30 tools in context upfront** | Simplest client. Model sees everything. | 30 tools x 400 tokens = 12,000 tokens overhead. Model accuracy degrades with large tool spaces (Microsoft: up to 85% drop). $0.036/request in pure schema cost. At 50K requests/day = $54K/month overhead alone. | Rejected |
| **B: Search-first discovery (3 meta-tools)** | 3 tools = ~600 tokens. Model searches, then gets full schema for selected tool only. 94% token reduction. Preserves reasoning capacity. | Extra round-trip for tool search (~200ms). Requires good tool descriptions for search quality. | **Selected** |
| **C: Code execution mode (single tool)** | Maximum token savings (98.7%). | Requires the model to generate arbitrary API code. Security nightmare for customer-facing agent. Impossible to audit individual tool permissions. | Rejected |

**Decision Rationale**: Search-first discovery is the correct trade-off for a customer-facing agent. The 200ms search overhead is invisible within the 3s end-to-end budget. Per-user token vault (Auth0 pattern) handles OAuth lifecycle: tokens are fetched just-in-time from the vault when the gateway routes a call, never stored in agent context. Circuit breakers per service prevent cascading failures -- when Salesforce is degraded, the agent gracefully reports "Salesforce is temporarily unavailable" instead of timing out or hallucinating. The JWT-to-tenant mapping ensures complete isolation: User A's gateway request is bound to User A's vault credentials; the gateway physically cannot fetch User B's Salesforce token. At 10,000 users averaging 5 requests/day, with search-first discovery: ~$900/month in schema overhead (vs. $54K with full context). Total cost: ~$18K/month (gateway + 5 MCP server pods + Token Vault + LLM inference at ~$0.03/request).
