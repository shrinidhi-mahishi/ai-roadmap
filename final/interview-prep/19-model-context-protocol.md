# Module 19: Model Context Protocol (MCP)

## What Is This?

MCP is the USB-C for AI. Just like USB-C replaced the chaos of proprietary chargers, display cables, and data connectors with one universal port, MCP replaces the chaos of custom API integrations between AI models and external tools with one standardized protocol. Before MCP, every LLM application built bespoke connectors to every database, API, and file system it needed. MCP gives you a single protocol that lets any AI model talk to any tool.

The model does not speak MCP directly. The host runtime does. This is the critical architectural insight: policy, approval, identity, and routing live in the host, not in the model. MCP is infrastructure plumbing, not a behavior the model owns. Think of it like a waiter (the host) translating between a customer (the model) and the kitchen (the server) -- the customer never walks into the kitchen.

Introduced by Anthropic in November 2024 and donated to the Linux Foundation's Agentic AI Foundation in December 2025, MCP is now adopted by Anthropic, OpenAI, Google, Microsoft, and AWS. Monthly SDK downloads grew from 100K at launch to 97M+ by mid-2026 -- a 970x increase in 18 months. The current spec (2026-07-28) is explicitly stateless and self-describing, meaning session assumptions moved out of the transport and into explicit handles or extensions like Tasks.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
+-----------------------------------------------------------------------+
|                           HOST                                         |
|  (Claude Desktop, Cursor, VS Code, Custom App)                        |
|                                                                        |
|  +--------------+  +--------------+  +--------------+                 |
|  |   Client 1   |  |   Client 2   |  |   Client 3   |                |
|  |  (1:1 conn)  |  |  (1:1 conn)  |  |  (1:1 conn)  |                |
|  +------+-------+  +------+-------+  +------+-------+                |
|         |                  |                  |                         |
+---------+------------------+------------------+------------------------+
          | stdio            | stdio            | Streamable HTTP
          | (JSON-RPC)       | (JSON-RPC)       | (JSON-RPC)
          v                  v                  v
   +--------------+  +--------------+  +--------------------------+
   |  MCP Server  |  |  MCP Server  |  |    MCP Server (Remote)   |
   |  (Local)     |  |  (Local)     |  |                          |
   |              |  |              |  |  +--------+ +---------+  |
   |  Tools:      |  |  Tools:      |  |  | OAuth  | | Health  |  |
   |  - db_query  |  |  - git_log   |  |  | 2.1    | | Checks  |  |
   |  - db_write  |  |  - git_diff  |  |  +--------+ +---------+  |
   |              |  |              |  |                          |
   |  Resources:  |  |  Resources:  |  |  Tools:                 |
   |  - schema    |  |  - repo tree |  |  - jira_create          |
   |              |  |              |  |  - jira_search          |
   |  Prompts:    |  |  Prompts:    |  |                          |
   |  - sql_help  |  |  - review    |  |  Resources:             |
   +--------------+  +--------------+  |  - sprint_board         |
                                        +--------------------------+
```

### Three Roles

| Role | Responsibility | Key Constraint |
|---|---|---|
| **Host** | The LLM application the user interacts with. Owns conversation, model invocation, user consent. Controls what tools the model can see, what requires approval, and what credentials each server gets. | Policy lives here, not in the model or server |
| **Client** | A per-server connection living inside the host. One client per server (strict 1:1). Handles wire-level protocol: connection management, capability negotiation, message framing, request routing. | One client per server; never many-to-one |
| **Server** | Exposes tools (callable functions), resources (read-only data), and prompts (reusable templates). Thin translation layers wrapping existing services in the MCP protocol. | Servers are stateless (2026-07-28); state belongs in tool arguments or server-minted handles |

### Three Primitives (Who Controls What)

| Primitive | Control | Purpose | Discovery | Execution |
|---|---|---|---|---|
| **Tools** | Model-controlled | Executable functions (the workhorse) | `tools/list` | `tools/call` |
| **Resources** | Application-controlled | Read-only data addressed by URI | `resources/list` | `resources/read` |
| **Prompts** | User-controlled | Parameterized instruction templates | `prompts/list` | `prompts/get` |

**Tools** are the most-used primitive. Defined by name, description, and `inputSchema` (JSON Schema). The LLM decides when to invoke them.

**Resources** provide contextual data without triggering operations. Each has a unique URI (e.g., `file:///data/schema.json`), name, description, and MIME type. Resource Templates enable dynamic access via URI parameters (e.g., `travel://activities/{city}/{category}`).

**Prompts** move prompt engineering into the server that owns the domain. The least-used and most underrated primitive. Explicitly invoked by the user or client, not auto-executed. Can dynamically reference available resources and tools.

### JSON-RPC 2.0 Wire Protocol

All MCP communication uses JSON-RPC 2.0. Request-response correlation via `id` fields. Notifications (no `id`) for fire-and-forget messages. Since 2026-07-28, every request carries protocol version and capabilities in a `_meta` field, making each request self-describing.

### Request-Flow Narrative (End-to-End Tool Call)

1. **Discovery**: Client calls `server/discover` (or reads cached response). Server returns its tool catalog with names, descriptions, and input schemas. Response includes `ttlMs` and `cacheScope` for caching.
2. **Schema injection**: Host injects tool definitions into the LLM's context window as function-calling schemas. Each tool costs **200-500 tokens** of context.
3. **Model decision**: User sends a message. The LLM reasons over the conversation and decides to call `db_query` with `{"sql": "SELECT ..."}`.
4. **Host routing**: Host identifies which client owns `db_query`, constructs a JSON-RPC `tools/call` request with `_meta` containing protocol version and capabilities.
5. **Wire transport**: Client sends the request over stdio (local) or Streamable HTTP (remote). HTTP requests include `Mcp-Method` and `Mcp-Name` headers for gateway routing without body parsing.
6. **Server execution**: Server validates the input against its schema, executes the underlying database query, and returns the result.
7. **Response delivery**: Result flows back through client to host. Host feeds the tool response into the LLM's context for the next generation step.
8. **Multi-round (MRTR)**: If the server needs more information, it returns `resultType: "input_required"` with an opaque `requestState` blob. Client collects answers, retries with `inputResponses`.

### Protocol Triangle (MCP / A2A / Function Calling)

These are complementary layers, not competitors:

| Layer | Protocol | Direction | Purpose |
|---|---|---|---|
| **Foundation** | Function Calling | LLM --> Host code | LLM emits structured arguments; host executes |
| **Vertical** | MCP | Agent --> Tools/Data | Standardized tool/data access for one agent |
| **Horizontal** | A2A | Agent <--> Agent | Peer-to-peer agent collaboration and delegation |

MCP does not replace function calling. Under the hood, an MCP client converts discovered tools into function-calling schemas for the model. MCP adds discovery, transport, and capability negotiation on top. A2A (Google, April 2025, 1.0 in March 2026) standardizes how agents discover and collaborate with each other. Production reality in 2026: most stacks run both -- A2A between agents, MCP from each agent to its tools. Both are governed by AAIF under the Linux Foundation (190 member organizations).

**Real-world example**: A coding agent in VS Code uses MCP to call a Jira server (create ticket), a Git server (read repo), and a database server (query schema). That same agent uses A2A to delegate a code review to a specialized review agent running in a different deployment. Function calling is what happens inside the LLM when it decides which tool to use.

---

## Part 2: Core Mechanics & Algorithms

### 2.1 Transport Layers

**Stdio (Local)**: Host spawns server as a child process. JSON-RPC over stdin/stdout. No network overhead. Cannot be shared across machines. Critical constraint: **stdout is exclusively reserved for protocol messages; all logging must go to stderr.**

**Streamable HTTP (Remote)**: Introduced 2025-03-26, replacing deprecated HTTP+SSE. Uses HTTP POST for client-to-server messages with optional Server-Sent Events for streaming responses. Standard HTTP authentication (bearer tokens, API keys, OAuth). POST requests include `Mcp-Method` and `Mcp-Name` headers for gateway routing without body parsing.

**SSE (Deprecated)**: Original transport from 2024-11-05. Formally deprecated in 2026-07-28 with a **12-month removal window**. Do not build on this.

| Transport | Use When | Auth | Scaling |
|---|---|---|---|
| **stdio** | Local subprocess tools inside an IDE or desktop app | Host-env secrets | Cannot share across machines |
| **Streamable HTTP** | Remote or multi-tenant servers needing standard auth and horizontal scaling | OAuth 2.1, bearer tokens | Round-robin LB, serverless viable |
| **HTTP+SSE** | Never (deprecated, 12-month removal window from 2026-07-28) | -- | -- |

### 2.2 Capability Negotiation Lifecycle

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

### 2.3 MRTR and Tasks Extension

**MRTR (Multi-Round Tool Resolution)**: `resultType: "input_required"` is the modern way for a server to request more input. This replaces older server-initiated elicitation and sampling patterns. The server returns an opaque `requestState` blob, and the client collects answers and retries with `inputResponses`.

**Tasks Extension**: Long-running work should return a task handle and use polling like `tasks/get` and `tasks/update`. Do not hold open one request forever for CI or batch-style work. This makes MCP suitable for durable workflows (CI pipelines, batch processing, multi-step deployments).

**Real-world example**: Deploying a service. Step 1: `tools/call` "deploy_service" returns `resultType: "input_required"` asking for confirmation of the deployment target. Step 2: Client provides confirmation. Step 3: Server returns a task handle. Step 4: Client polls `tasks/get` until the deployment completes (or fails).

### 2.4 Spec Evolution Timeline

| Date | Change |
|---|---|
| **2024-11-05** | Initial MCP spec. No standardized auth. HTTP+SSE transport. |
| **2025-03-26** | Streamable HTTP transport. OAuth 2.1 + PKCE. Dynamic Client Registration (DCR). |
| **2025-06-18** | Servers classified as OAuth Resource Servers. RFC 8707 + RFC 9728 PRM mandatory. |
| **2026-07-28** | **Stateless core.** `server/discover` added. DCR deprecated for CIMD. RFC 9207 issuer validation. Credential binding. Roots, Sampling, Logging, and HTTP+SSE deprecated with 12-month removal. |

Key 2026-07-28 changes:
- Protocol-level sessions removed
- `server/discover` added for capability discovery
- Each request is self-describing via `_meta`
- Cacheable `tools/list` with `ttlMs` and `cacheScope`
- Deterministic ordering for prompt-cache stability

### 2.5 Server Implementation Pattern (Python SDK)

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("inventory-server")

@mcp.tool()
async def search_products(query: str, category: str = "all", limit: int = 10) -> str:
    """Search product inventory by keyword and optional category."""
    results = await db.execute(
        "SELECT name, sku, stock FROM products "
        "WHERE name ILIKE $1 AND ($2 = 'all' OR category = $2) LIMIT $3",
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
    return (f"Analyze the inventory for category '{category}'. "
            "Identify low-stock items (below 10 units), "
            "overstocked items (above 1000 units), "
            "and recommend reorder quantities.")
```

Run locally: `mcp.run(transport="stdio")`. Run remotely: `mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)`.

---

## Part 3: Token Economics & NFR Analysis

### 3.1 Tool Definition Overhead

Each MCP tool definition consumes **200-500 tokens** in the LLM's context window. This is a fixed cost per request that never disappears.

| Setup | Token Overhead | Context Impact |
|---|---|---|
| GitHub MCP server (93 tools) | ~55,000 tokens | **28%** of 200K window gone before user speaks |
| GitHub + Slack + Sentry (3 servers) | ~143,000 tokens | **72%** of 200K window consumed on idle |
| Typical production (5 servers x 30 tools) | 30,000-60,000 tokens | 15-30% of context permanently unavailable |
| Single complex Gmail tool | ~820 tokens | Nested parameters cost 5-10x simple ones |
| 508-tool benchmark | ~1,150,000 tokens | $377 per benchmark round in pure metadata |

**Microsoft Research documented that large tool spaces lower agent performance by up to 85%.** Every 10K tokens of tool schemas removes approximately 5 pages of reasoning capacity from the model.

### 3.2 Financial Cost at Scale

At Claude Sonnet pricing ($3/M input tokens):

| Daily Volume | Schema Overhead (90K tokens) | Monthly Cost (Overhead Only) |
|---|---|---|
| 100 requests/day | $0.27/request | $810/month |
| 1,000 requests/day | $0.27/request | $8,100/month |
| 10,000 requests/day | $0.27/request | $81,000/month |

These are **pure overhead costs** -- the actual query and response tokens are additional.

### 3.3 Optimization Strategies

| Strategy | Mechanism | Savings |
|---|---|---|
| **Search-First Discovery** | Replace upfront schema dump with 2-3 meta-tools (`search_tools` + `execute_tool`). Agent searches first, gets full schema on demand. Claude Code activates this automatically when tool descriptions exceed 10% of context. | ~85% context preserved |
| **Code Execution Mode** | Single `execute_code` tool replaces the full catalog. | 98.7% reduction (150K -> 2K) |
| **Tiered Schema Discovery** | Discovery tier (name + one-line + param types) vs invocation tier (full schema injected on-demand). | 60-80% reduction |
| **Gateway Aggregation** | Cloudflare reference: 52 backend tools collapsed into 2 portal tools at ~600 tokens. | 94% reduction |
| **Cacheable List Results** (2026-07-28) | `tools/list` responses carry `ttlMs` and `cacheScope`. Deterministic ordering keeps upstream prompt caches stable across reconnects. | Eliminates repeated discovery cost |

### 3.4 Latency SLA Targets

| Metric | Target | Reality in Production |
|---|---|---|
| Message serialization | < 10ms | Unoptimized JSON can add 50-100ms |
| Tool call p50 | < 50ms | Achievable with local stdio |
| Tool call p95 | < 300ms | Comfortable range |
| Tool call p99 | < 1,000ms | Exceeding this correlates with user churn |
| MCP baseline overhead | 300-800ms | Cannot be cached away (per multiple reports) |
| Bifrost gateway overhead | 11 microseconds | Go-based, sub-3ms MCP latency, 5,000+ RPS |

**Documented incident**: A 10-connection pool saturated by 7 agents in 2 seconds; p95 went from 180ms to 4.2 seconds. GitHub Copilot measured a **400ms latency reduction** after cutting tools from 40 to 13.

### 3.5 Availability & RPO/RTO

| Metric | Value | Notes |
|---|---|---|
| **RPO** | Zero for stateless MCP (2026-07-28) | No protocol state to lose |
| **RTO** | Next-request for stateless servers | Client retries; next server instance handles it |
| **Transport-layer failures** | 73% of production outages start here | Cascading upward |
| **Recovery failure rate** | 20-30% without explicit retry handling | Exponential backoff + jitter required |
| **Application state** | Requires model to re-derive from tool args or server-minted handles | Not automatic |

---

## Part 4: Distributed Resilience & Security

### 4.1 Zero-Trust MCP

NSA advisory (May 2026) and industry guidance converge on treating every tool call as potentially unauthorized. The 2026-07-28 stateless core makes this natural -- there is no session to inherit trust from.

| Control | Implementation | What It Prevents |
|---|---|---|
| **Authenticate per request** | OAuth 2.1 + PKCE. RFC 9728 Protected Resource Metadata. | Session hijacking, inherited trust |
| **Least privilege scoping** | Per-tool, per-action tokens. Not one shared credential across the server. | Over-privileged access |
| **Continuous validation** | Re-verify on every call. Token expiry, scope checks, rate limits. | Stale credentials |
| **Explicit deny by default** | Tools invisible until explicitly granted. | Accidental exposure |
| **Treat servers as untrusted** | Validate tool outputs against schema before passing to downstream components. | Malicious responses |
| **Signed provenance** | For dynamic server discovery. | Supply chain attacks |

**Enterprise-Managed Authorization (EMA)**: Solves per-user, per-server auth at scale. Employees access all MCP servers through a single SSO login via enterprise IdPs (first: Okta). Security teams get centralized audit trail and consistent policy enforcement. Users cannot inadvertently connect personal accounts to work tools.

### 4.2 Authorization Evolution

| Spec | Auth Model |
|---|---|
| 2024-11-05 | No standardized auth. Roll your own. |
| 2025-03-26 | OAuth 2.1 + PKCE. Dynamic Client Registration (DCR). |
| 2025-06-18 | Servers classified as OAuth Resource Servers. RFC 8707 + RFC 9728 PRM mandatory. |
| 2026-07-28 | DCR deprecated for CIMD. RFC 9207 issuer validation. Credential binding. Stateless core. |

**Key auth requirements (2026-07-28)**:
- Clients **MUST** send RFC 8707 `resource` = canonical MCP server URI on authorize AND token requests.
- Servers **MUST** accept only tokens whose audience is themselves.
- Servers **MUST NOT** passthrough the client token to upstream APIs (typically RFC 8693 exchange instead).
- Hash-pin tool JSON + server digest. Re-verify on every `tools/call`. Mismatch -> pause / re-consent.

### 4.3 OWASP MCP Top 10

| ID | Risk | Mitigation |
|---|---|---|
| **MCP01** | Token Mismanagement & Secret Exposure | Vault-backed credential rotation; never embed secrets in tool arguments |
| **MCP02** | Excessive Permissions | Per-tool scope tokens; principle of least privilege |
| **MCP03** | Command Injection | Input validation + parameterized queries; never pass raw tool args to shells |
| **MCP04** | Supply Chain Compromise | Pin server versions; signed provenance; AIBOM inventory |
| **MCP05** | Tool Poisoning | Verify tool definitions against known-good manifests; code review |
| **MCP06** | Prompt Injection & Intent Flow | Separate system/user/tool context; output validation |
| **MCP07** | Insufficient Authentication | OAuth 2.1 + PKCE mandatory; EMA for enterprise |
| **MCP08** | Audit & Logging Gaps | Log every tool call with correlation IDs; immutable audit trail |
| **MCP09** | Shadow MCP Servers | Maintain inventory of all deployed servers; network-level controls |
| **MCP10** | Context Over-Sharing | Gateway-level response filtering; output schema validation |

### 4.4 CVE Landscape (Real Vulnerabilities)

| CVE | CVSS | Impact |
|---|---|---|
| CVE-2025-6514 (mcp-remote) | **9.6** | First real-world RCE on client OS via untrusted remote MCP server (437K+ downloads) |
| CVE-2025-54136 (MCPoison) | **8.8** | Tool poisoning via description manipulation; hash-pin every `tools/call` |
| CVE-2026-33032 (MCPwn) | **9.8** | Missing auth for command execution; 2,600+ exposed instances |
| CVE-2026-26118 (Azure MCP) | **8.8** | SSRF stealing managed identity tokens |

Broader landscape: **40+ CVEs** against MCP SDKs and servers (Jan-Apr 2026). VIPER-MCP framework scanned ~40,000 repos, found 106 zero-day vulnerabilities, produced 67 CVEs. Censys identified **12,520 publicly exposed MCP services** with ~40% unauthenticated. **Only 8.5%** of remote servers implement the mandatory OAuth 2.1 + PKCE standard. GitGuardian found **24,008 secrets** in MCP config files on public GitHub; 2,117 still valid.

### 4.5 MCP Failure Taxonomy

Not all failures are equal. Effective resilience requires classifying failures by type and responding differently to each:

| Type | Examples | Detection | Response |
|---|---|---|---|
| **Transient** | Transport timeout, token refresh failure, server overloaded | HTTP 503, connection reset, OAuth 401 with valid refresh | Retry with exponential backoff (max 3), re-authenticate |
| **Permanent** | Server decommissioned, OAuth scope revoked, tool removed | HTTP 404/410, capability negotiation fails, repeated 403 | Remove from tool registry, alert admin, degrade gracefully |
| **Protocol** | Schema drift (server updated tool schema), version mismatch | JSON-RPC parse error, unexpected response format | Re-discover tools (`tools/list`), update client schema cache |
| **Adversarial** | Malicious tool response, prompt injection via tool output | Output validation failure, canary token triggered | Quarantine server, block tool, alert security team |

The taxonomy drives automation: transient failures retry, permanent failures degrade, protocol failures re-discover, adversarial failures isolate. Without this classification, all failures get the same retry logic -- which means retrying permanent failures (wasting time) and retrying adversarial failures (amplifying the attack).

### 4.6 Circuit Breaker for MCP Servers

Track failures **per server independently**. One failing server should not block access to other healthy servers.

**State machine:**
- **Closed** (normal): All requests flow through. Track consecutive failures.
- **Open** (tripped): After 3 consecutive failures in a 60-second window, open the circuit for that server. All calls immediately return a degraded response.
- **Half-open** (probing): Every 30 seconds, send a lightweight probe (`ping` or `tools/list`). If the probe succeeds, increment success counter.
- **Close** (recovery): After 2 consecutive successful probes, close the circuit and resume normal traffic.

**A global circuit breaker means one flaky Jira server takes down Salesforce, Stripe, and Google Calendar access.** Per-server breakers isolate blast radius.

### 4.7 Durable Execution for MRTR Flows

MRTR flows span multiple request-response cycles. These multi-step flows are vulnerable to crashes mid-execution.

**Checkpoint strategy:**
- Checkpoint after each successful tool call result. Store in durable storage (Redis, DynamoDB) keyed by a flow ID.
- On crash or restart, resume from the last checkpoint rather than replaying all tool calls from the beginning. Replaying is dangerous because tool calls may not be idempotent -- a `create_ticket` call replayed creates a duplicate ticket.
- Use **idempotency keys** for tool calls that have side effects. Pass a client-generated UUID with each mutating call. The server uses the key to deduplicate.

### 4.8 Fallback Strategies

When an MCP server is unavailable, the system must degrade gracefully:

1. **Graceful degradation**: Inform the LLM that the tool is temporarily unavailable. Let the model proceed without it. Example: "Salesforce is temporarily unavailable. I can still help with your Jira and Stripe questions."
2. **Static fallback**: For read-only tools, serve cached tool responses with a staleness warning. Not suitable for write operations or real-time data.
3. **Alternative server**: Route to a backup MCP server if available. Requires maintaining standby instances.
4. **Human escalation**: Pause the flow and ask the user to perform the action manually.

The fallback hierarchy should be configured **per tool, not globally**. A read-only tool (catalog lookup) can safely use static fallback. A write tool (create ticket) should escalate to the user.

### 4.9 PII & Audit Governance

Tool responses can contain PII, financial data, or sensitive business information with **no protocol-level redaction mechanism**. Enterprise mitigation:

- **Gateway-level response filtering**: Strip sensitive fields before they reach the LLM context.
- **Output schema validation**: Define allowed response shapes; reject or redact anything outside the schema.
- **Immutable audit trail**: Log every tool call, arguments, and response with correlation IDs and tenant context.
- **Auth0 Token Vault pattern**: Agent trades internal token for API token just-in-time. Secrets stay in vault; agent gets just-enough access. No stored refresh tokens in agent context.

**PII pipeline (detect -> redact -> audit):**
1. **Detect** with regex (email, PAN, SSN) + ML NER before bytes leave the trust boundary.
2. **Redact/mask/hash** to stable tokens (`[EMAIL_<hash12>]`) so the task continues; block when the field must not exist (secrets paths, MCP args, sandbox env).
3. **Audit WORM** of decisions: pre/post hashes, entity types, counts, detector, correlation_id, tenant -- not raw PAN.

### 4.10 Connection Lifecycle & Health

**Three-tier health checks:**
- `/health` (Liveness): Returns 200 if process is up. Lightweight.
- `/ready` (Readiness): Checks critical dependencies (database, rate limits). Failing means load balancer stops sending traffic.
- `client.ping()`: Protocol-level transport health verification.

**Reconnection**: The 2026-07-28 stateless core mitigates mid-session disconnects -- each request is self-contained, so a server restart is transparent. Streamable HTTP supports resumability via `Last-Event-ID`. Best practice: exponential backoff with jitter.

---

## Part 5: Production Enterprise Code

### MCP Server with Tools, Resources, and Validation

```python
"""Production MCP server for a customer data platform.
Demonstrates tool + resource implementation with input validation,
structured error handling, and audit logging.
Run locally: python server.py
Run remote:  python server.py http
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
    log.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "params": params,
        "result_summary": result_summary,
    }))


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
    """Search customers by region, tier, and minimum ARR."""
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
    _audit_log("search_customers", {"region": region, "tier": tier, "min_arr": min_arr},
               f"{len(results)} results")
    return json.dumps(results)


@mcp.tool()
async def get_open_tickets(customer_id: str) -> str:
    """Get all open support tickets for a customer."""
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
Run: python client.py stdio python server.py
     python client.py http http://localhost:8080/mcp
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
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if mode == "http":
        asyncio.run(run_http_client(sys.argv[2]))
    else:
        asyncio.run(run_stdio_client(sys.argv[2], sys.argv[3:]))
```

### Multi-Tenant MCP Gateway with Circuit Breaker

```python
"""MCP gateway: tenant isolation, rate limiting, circuit breaker per server,
PII filtering, audit logging. Zero-Trust: explicit allow only.
Run: python mcp_gateway.py
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("mcp-gateway")
logging.basicConfig(level=logging.INFO)


# ---- Circuit Breaker (per server, not global) ----------------------------
class CircuitState(Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

class CircuitOpenError(RuntimeError): pass

@dataclass
class CircuitBreaker:
    name: str; failure_threshold: int = 3; cooldown_s: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0; _opened_at: float = 0.0
    def allow(self) -> None:
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
            else: raise CircuitOpenError(f"circuit_open:{self.name}")
    def record_success(self) -> None:
        self._failures, self._state = 0, CircuitState.CLOSED
    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


# ---- PII detect-redact-audit before passing to LLM context ---------------
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

def pii_filter_response(response: dict, *, audit: list, tenant_id: str,
                         tool_name: str, cid: str) -> dict:
    """Strip PII from tool responses before they reach the LLM context."""
    raw = json.dumps(response)
    kinds = [k for k, rx in (("email", EMAIL_RE), ("pan", PAN_RE)) if rx.search(raw)]
    if not kinds:
        audit.append({"cid": cid, "tenant": tenant_id, "tool": tool_name, "action": "allow"})
        return response
    clean = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", raw)
    clean = PAN_RE.sub("[PAN_REDACTED]", clean)
    audit.append({"cid": cid, "tenant": tenant_id, "tool": tool_name,
                  "action": "redact", "kinds": kinds})
    return json.loads(clean)


# ---- Tenant Config and Rate Limiter --------------------------------------
@dataclass
class TenantConfig:
    tenant_id: str
    allowed_tools: set[str]          # Zero-Trust: explicit allow only
    rate_limit_per_minute: int
    server_url: str
    api_key: str                     # Production: fetched from vault

@dataclass
class RateLimiter:
    window_calls: dict[str, list[float]] = field(default_factory=dict)
    def check(self, tenant_id: str, limit: int) -> bool:
        now = time.time()
        calls = self.window_calls.setdefault(tenant_id, [])
        self.window_calls[tenant_id] = [t for t in calls if now - t < 60]
        if len(self.window_calls[tenant_id]) >= limit:
            return False
        self.window_calls[tenant_id].append(now)
        return True


# ---- Gateway: routing through security layers ----------------------------
class MCPGateway:
    def __init__(self) -> None:
        self.tenants: dict[str, TenantConfig] = {}
        self.breakers: dict[str, CircuitBreaker] = {}  # per server URL
        self.rate_limiter = RateLimiter()
        self.audit_log: list[dict[str, Any]] = []

    def register_tenant(self, config: TenantConfig) -> None:
        self.tenants[config.tenant_id] = config
        if config.server_url not in self.breakers:
            self.breakers[config.server_url] = CircuitBreaker(config.server_url)

    def authenticate(self, tenant_id: str, bearer_token: str) -> bool:
        """Production: verify JWT signature, check expiry, validate scopes."""
        tenant = self.tenants.get(tenant_id)
        return tenant is not None and bearer_token == tenant.api_key

    async def route_tool_call(
        self, tenant_id: str, bearer_token: str, tool_name: str,
        arguments: dict[str, Any], cid: str = ""
    ) -> dict[str, Any]:
        t0 = time.time()
        cid = cid or f"auto-{int(t0)}"

        # Layer 1: Authentication
        if not self.authenticate(tenant_id, bearer_token):
            self._log("auth_failed", tenant_id, tool_name, cid)
            return {"error": "Authentication failed", "code": 401}

        tenant = self.tenants[tenant_id]

        # Layer 2: Tool scope enforcement (Zero-Trust: explicit allow only)
        if tool_name not in tenant.allowed_tools:
            self._log("tool_denied", tenant_id, tool_name, cid)
            return {"error": f"Tool '{tool_name}' not permitted", "code": 403}

        # Layer 3: Rate limiting
        if not self.rate_limiter.check(tenant_id, tenant.rate_limit_per_minute):
            self._log("rate_limited", tenant_id, tool_name, cid)
            return {"error": "Rate limit exceeded", "code": 429}

        # Layer 4: Circuit breaker (per server)
        breaker = self.breakers[tenant.server_url]
        try:
            breaker.allow()
        except CircuitOpenError:
            self._log("circuit_open", tenant_id, tool_name, cid)
            return {"error": "Service temporarily unavailable", "code": 503}

        # Layer 5: Forward to tenant's MCP server
        try:
            result = await self._forward(tenant, tool_name, arguments)
            breaker.record_success()
        except Exception as exc:
            breaker.record_failure()
            self._log("server_error", tenant_id, tool_name, cid)
            return {"error": str(exc), "code": 502}

        # Layer 6: PII filtering on response
        filtered = pii_filter_response(
            result, audit=self.audit_log, tenant_id=tenant_id,
            tool_name=tool_name, cid=cid)

        ms = (time.time() - t0) * 1000
        self._log("tool_call_ok", tenant_id, tool_name, cid, latency_ms=round(ms, 1))
        return filtered

    async def _forward(self, tenant: TenantConfig, tool_name: str,
                       arguments: dict[str, Any]) -> dict[str, Any]:
        """Production: use MCP client SDK with connection pooling."""
        return {"status": "success", "data": {"tool": tool_name, "result": "executed"}}

    def _log(self, event: str, tenant_id: str, tool: str, cid: str, **kw: Any) -> None:
        entry = {"ts": time.time(), "event": event, "tenant": tenant_id,
                 "tool": tool, "cid": cid, **kw}
        self.audit_log.append(entry)
        log.info(json.dumps(entry))

    def list_tools_for_tenant(self, tenant_id: str) -> list[str]:
        """Return only the tools this tenant is allowed to see (MCP02 mitigation)."""
        tenant = self.tenants.get(tenant_id)
        return sorted(tenant.allowed_tools) if tenant else []


async def main() -> None:
    gw = MCPGateway()
    gw.register_tenant(TenantConfig(
        tenant_id="acme", allowed_tools={"lookup_customer", "search_customers", "get_open_tickets"},
        rate_limit_per_minute=100, server_url="http://mcp-acme:8080", api_key="acme-key"))
    gw.register_tenant(TenantConfig(
        tenant_id="globex", allowed_tools={"lookup_customer", "search_customers"},
        rate_limit_per_minute=50, server_url="http://mcp-globex:8080", api_key="globex-key"))

    # Acme can access tickets
    r1 = await gw.route_tool_call("acme", "acme-key", "get_open_tickets",
                                   {"customer_id": "C-1001"}, "cid-1")
    assert "error" not in r1
    # Globex cannot access tickets (tool scope enforcement)
    r2 = await gw.route_tool_call("globex", "globex-key", "get_open_tickets",
                                   {"customer_id": "C-1001"}, "cid-2")
    assert r2["code"] == 403
    # Bad auth
    r3 = await gw.route_tool_call("acme", "wrong", "lookup_customer",
                                   {"customer_id": "C-1001"}, "cid-3")
    assert r3["code"] == 401
    print(f"ok: {len(gw.audit_log)} audit entries, breakers: "
          f"{[(k, v._state.value) for k, v in gw.breakers.items()]}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Enterprise AI Coding Assistant with MCP Tool Governance

**Problem**: A fintech company (2,000 engineers, 15 product teams) wants to deploy an AI coding assistant across the organization. Engineers need access to internal tools: code search, CI/CD pipelines, incident management, database schemas, and documentation. Security requirements: SOC 2 Type II, no cross-team data leakage, audit trail for every tool invocation, and ability to revoke tool access per-team in under 5 minutes. Target: <500ms p95 for tool calls, 99.9% availability, <$20K/month infrastructure.

**Architecture**:

```
+----------------------------------------------------------------------+
|                        DEVELOPER IDE (Host)                           |
|  +----------------------------------------------------------------+  |
|  |  MCP Client (embedded in IDE plugin)                           |  |
|  |  Connects to gateway via Streamable HTTP + OAuth 2.1           |  |
|  +----------------------------+-----------------------------------+  |
+-------------------------------+--------------------------------------+
                                | HTTPS + Bearer Token
                                v
+----------------------------------------------------------------------+
|                    MCP GATEWAY (Aggregation + Multi-Tenant)            |
|                                                                        |
|  +-------------+ +--------------+ +------------+ +----------+        |
|  | OAuth 2.1   | | Team-Scoped  | | Rate       | | Audit    |        |
|  | + EMA       | | Tool ACLs    | | Limiter    | | Logger   |        |
|  | (Okta SSO)  | | (per-team    | | (per-user  | | (WORM    |        |
|  |             | |  visibility) | |  per-tool)  | | append)  |        |
|  +-------------+ +--------------+ +------------+ +----------+        |
|                            |                                          |
|  +------------------------------------------------------------+      |
|  |         TIERED SCHEMA DISCOVERY                             |      |
|  |  Initial: 8-12 tool summaries (~2,400 tokens)              |      |
|  |  On-demand: full schema injected when model selects a tool  |      |
|  +------------------------------------------------------------+      |
|                            |                                          |
+------------+---------------+--------------+---------------------------+
             |               |              |
             v               v              v
      +------------+  +------------+  +------------+
      | Code Search|  | CI/CD      |  | Incident   |
      | MCP Server |  | MCP Server |  | MCP Server |
      | (stdio,    |  | (HTTP,     |  | (HTTP,     |
      |  local)    |  |  shared)   |  |  shared)   |
      +------------+  +------------+  +------------+
```

**Trade-Off Matrix**:

| Decision | Option A: Direct MCP (no gateway) | Option B: Aggregation gateway + EMA | Option C: Per-team dedicated servers |
|---|---|---|---|
| **Security** | No centralized auth or audit. Cross-team leakage risk. SOC 2 auditors reject this. | Centralized auth via Okta SSO. Per-team tool ACLs. Single audit stream. Tool revocation in seconds. | Perfect isolation but 75 server deployments. |
| **Token overhead** | Each IDE loads all schemas (~30K+ tokens). | Tiered discovery: ~2,400 tokens initial (94% reduction). | Same as A per team. |
| **Operations** | Tool revocation requires config push to 2,000 machines. | ACL change in gateway, propagated in <5 minutes. | Cross-team tools require duplication. $60K+/month. |
| **Latency** | Direct to server (lowest hop count). | Adds 10-50ms per call (well under 500ms p95). | Same as A per team. |
| **Verdict** | Rejected | **Selected** | Rejected |

**Decision Rationale**: The aggregation gateway with EMA provides centralized governance SOC 2 requires. Tiered schema discovery keeps token overhead under 3,000 tokens. Three gateway replicas behind a load balancer achieve 99.9% availability. Total cost: ~$12K/month (3 gateway pods + 8 MCP server pods + Okta + logging).

---

### Scenario 2: Customer-Facing AI Agent with MCP Tool Access Across SaaS Integrations

**Problem**: A customer success platform wants to ship an AI agent that helps end-users manage their accounts. The agent needs to access 6 external SaaS tools (Salesforce, Zendesk, Slack, Stripe, Jira, Google Calendar) on behalf of each customer. Each customer has their own credentials for each service. The agent must never access Customer A's Salesforce data when serving Customer B. Must handle service outages and graceful degradation. Target: 10,000 end-users, <3s end-to-end response time, <$50K/month total.

**Architecture**:

```
+----------------------------------------------------------------------+
|                     CUSTOMER-FACING CHAT UI                           |
|                            |                                          |
|                            v                                          |
|  +----------------------------------------------------------------+  |
|  |  ORCHESTRATOR (Agent Loop)                                     |  |
|  |  - Identifies user intent                                      |  |
|  |  - Selects tools via search-first discovery (3 meta-tools)     |  |
|  |  - Enforces max 3 tool calls per turn                         |  |
|  +----------------------------+-----------------------------------+  |
|                                |                                      |
|  +-----------------------------+----------------------------------+  |
|  |           MCP GATEWAY (Multi-Tenant, Per-User Isolation)       |  |
|  |                                                                 |  |
|  |  +--------------+ +--------------+ +---------------------+     |  |
|  |  | User Identity| | Token Vault  | | Circuit Breaker     |     |  |
|  |  | (JWT claim   | | (per-user    | | (per-service, trips |     |  |
|  |  |  -> tenant)  | |  per-service | |  at 50% in 60s)     |     |  |
|  |  |              | |  OAuth mgmt) | |                     |     |  |
|  |  +--------------+ +--------------+ +---------------------+     |  |
|  +-----------------------------------------------------------------+  |
|                                 |                                     |
+----------------------------------------------------------------------+
          +----------+-----------+----------+-----------+
          v          v           v          v           v
   +----------++----------++----------++----------++----------+
   |Salesforce||  Zendesk ||  Stripe  ||   Jira   || Google   |
   |MCP Server||MCP Server||MCP Server||MCP Server|| Calendar |
   | 8 tools  || 6 tools  || 5 tools  || 7 tools  || 4 tools  |
   +----------++----------++----------++----------++----------+
```

**Trade-Off Matrix**:

| Decision | Option A: All 30 tools upfront | Option B: Search-first discovery | Option C: Code execution mode |
|---|---|---|---|
| **Token cost** | 30 tools x 400 = 12,000 tokens. $0.036/request. At 50K req/day = $54K/month overhead. | 3 tools = ~600 tokens. +200ms search round-trip. 94% reduction. | Maximum savings (98.7%). |
| **Accuracy** | Model degrades with large tool spaces (up to 85% drop). | Preserves reasoning capacity. | Requires model to generate arbitrary API code. |
| **Security** | All tools visible regardless of intent. | Only selected tool schema exposed. | Impossible to audit individual tool permissions. Security nightmare for customer-facing agent. |
| **Verdict** | Rejected | **Selected** | Rejected |

**Decision Rationale**: Search-first discovery is the correct trade-off. The 200ms search overhead is invisible within the 3s budget. Per-user token vault (Auth0 pattern) handles OAuth lifecycle: tokens are fetched just-in-time, never stored in agent context. Circuit breakers per service prevent cascading failures. JWT-to-tenant mapping ensures complete isolation. Total cost: ~$18K/month (gateway + 5 MCP server pods + Token Vault + LLM inference at ~$0.03/request).

---

## Common Failure Modes

| Failure | What Breaks | Prevention |
|---|---|---|
| **Treating MCP as "tool calling only"** | Ignoring resources, prompts, and extension negotiation | Use all three primitives; resources for context, prompts for domain templates |
| **Giant tool catalogs in context** | 93 GitHub tools = 55K tokens, 72% of window with 3 servers, 85% accuracy drop | Search-first discovery, tiered schemas, gateway aggregation |
| **Sticky sessions on stateless protocol** | Scaling fails; serverless impossible; connection loss = session loss | 2026-07-28 stateless core; state in tool args or server handles |
| **Token passthrough to downstream** | Confused deputy attack; one leaked token compromises all downstream services | RFC 8693 token exchange; RFC 8707 audience binding |
| **Trusting tool catalogs as benign** | Tool poisoning, rug-pull attacks, MCPoison (CVE-2025-54136) | Hash-pin tool JSON; re-verify every `tools/call`; code review |
| **Building on deprecated HTTP+SSE** | 12-month removal clock started 2026-07-28 | Use Streamable HTTP for all new remote servers |
| **No deterministic tool ordering** | Prompt-cache churn; inconsistent behavior across reconnects | Deterministic ordering + `ttlMs` + `cacheScope` |
| **Global circuit breaker** | One flaky Jira takes down Salesforce, Stripe, Calendar | Per-server circuit breakers |
| **Replaying non-idempotent MRTR flows** | Duplicate tickets, double charges, duplicate notifications | Idempotency keys; checkpoint after each tool call |
| **No PII filtering on tool responses** | Sensitive data in LLM context; Vec2Text inversion risk | Gateway-level response filtering; output schema validation |
| **Shadow MCP servers** | Unmonitored tool access; audit gaps | Server inventory; network-level controls; AIBOM |
| **Shared credentials across servers** | One compromised server = full access | Per-tool, per-action tokens; vault-backed rotation |

---

## Interview Q&A

**Q1: What is MCP in one sentence?**
A standard protocol that lets hosts connect models to tools, resources, and prompts through a self-describing client/server contract. Introduced by Anthropic November 2024, now under the Linux Foundation. The model does not speak MCP directly -- the host runtime does. Policy, approval, identity, and routing live in the host, not in the model.

**Q2: What are the three roles and three primitives?**
Roles: host owns the user experience and policy, client speaks the protocol (one per server, 1:1), server exposes capabilities. Primitives: tools are model-controlled (the workhorse), resources are app-controlled (read-only data by URI), prompts are user-controlled (reusable templates). The model never sends JSON-RPC itself -- it emits a native tool selection, and the host's MCP client translates.

**Q3: What changed in the 2026-07-28 spec?**
MCP became stateless at the protocol level. `server/discover` was added. Each request carries everything in `_meta` -- no handshake, no session. `tools/list` responses carry `ttlMs` and `cacheScope` for caching. Deterministic ordering keeps prompt caches stable. DCR was deprecated for CIMD. Roots, Sampling, Logging, and HTTP+SSE were deprecated with a 12-month removal window. This makes round-robin LB and serverless deployments viable.

**Q4: When do you use stdio vs Streamable HTTP?**
Stdio for local subprocess tools inside an IDE or desktop app -- no network overhead, stdout reserved for protocol messages, all logging to stderr. Streamable HTTP for remote or multi-tenant servers that need standard auth and horizontal scaling. Never HTTP+SSE -- deprecated with a 12-month clock.

**Q5: MCP vs native function calling vs A2A?**
Three complementary layers, not competitors. Function calling is the foundation: LLM emits structured arguments, host executes. MCP is vertical: standardized tool/data access for one agent (discovery, transport, capability negotiation on top of function calling). A2A is horizontal: agent-to-agent delegation and collaboration. Production stacks run both -- A2A between agents, MCP from each agent to its tools. Both governed by AAIF under the Linux Foundation.

**Q6: How does tool overhead scale and how do you mitigate it?**
Each tool costs 200-500 tokens. GitHub MCP (93 tools) consumes 55K tokens -- 28% of a 200K window. Three servers can hit 143K tokens (72%). Microsoft found large tool spaces lower accuracy by up to 85%. Mitigation: search-first discovery (2-3 meta-tools, ~85% context preserved), code execution mode (98.7% reduction), tiered schema discovery (name + one-liner first, full schema on demand), gateway aggregation (Cloudflare: 52 tools to 2 portal tools, 94% reduction), cacheable `tools/list` with `ttlMs`.

**Q7: What is the Zero-Trust MCP model?**
Authenticate per request (no inherited session trust). Least privilege (per-tool, per-action tokens). Continuous validation (re-verify every call). Explicit deny by default. Treat every server as untrusted third party (validate outputs against schema). OAuth 2.1 + PKCE, RFC 8707 audience binding (server accepts only tokens for itself), RFC 8693 exchange (never passthrough client token to upstream). Hash-pin tool definitions and re-verify on every `tools/call`. EMA for enterprise (single SSO via Okta, centralized audit). The editor is the TCB, not a PEP.

**Q8: What real CVEs should I know?**
CVE-2025-6514 (mcp-remote, CVSS 9.6): first real RCE via untrusted server, 437K+ downloads. CVE-2025-54136 (MCPoison, 8.8): tool poisoning. CVE-2026-33032 (MCPwn, 9.8): missing auth, 2,600+ exposed. CVE-2026-26118 (Azure MCP, 8.8): SSRF stealing managed identity. 40+ CVEs in Jan-Apr 2026. 12,520 exposed services with 40% unauthenticated. Only 8.5% implement mandatory OAuth. 24,008 secrets in MCP config files on public GitHub.

**Q9: How do you handle MCP server failures?**
Classify failures by type: transient (retry with backoff), permanent (degrade, alert), protocol (re-discover), adversarial (quarantine). Per-server circuit breakers -- never global. Fallback hierarchy per tool: graceful degradation (inform model), static fallback (cached responses for read-only), alternative server, human escalation. For MRTR flows: checkpoint after each tool call, use idempotency keys for mutating operations. 73% of production outages start at the transport layer.

**Q10: OWASP MCP Top 10 -- name three and your mitigations.**
MCP01 Token Mismanagement: vault-backed rotation, never embed secrets in tool arguments. MCP04 Supply Chain: pin server versions, signed provenance, AIBOM inventory -- VIPER-MCP found 106 zero-days across 40K repos. MCP10 Context Over-Sharing: gateway-level response filtering, output schema validation, PII detect-redact-audit pipeline before tool responses reach LLM context. The common thread is that MCP servers are untrusted third parties and must be treated as such.

---

## Key Numbers to Memorize

| Number | What |
|---|---|
| **3 / 3 / 2** | Roles (host/client/server), primitives (tools/resources/prompts), transports (stdio/Streamable HTTP) |
| **200-500 tokens** | Per-tool schema overhead in context |
| **55,000 tokens** | GitHub MCP (93 tools) schema cost -- 28% of 200K window |
| **143,000 tokens** | 3 servers combined -- 72% of 200K window consumed on idle |
| **85%** | Accuracy drop with large tool spaces (Microsoft Research) |
| **94%** | Reduction via gateway aggregation (Cloudflare: 52 to 2 tools) |
| **98.7%** | Reduction via code execution mode (150K to 2K) |
| **85%** | Context preserved via search-first discovery |
| **400ms** | Latency reduction when GitHub Copilot cut tools from 40 to 13 |
| **970x** | SDK download growth in 18 months (100K to 97M+) |
| **2026-07-28** | Stateless spec: `server/discover`, no sessions, 12-month SSE removal |
| **9.6 / 9.8 / 8.8** | CVE CVSS: mcp-remote RCE / MCPwn / MCPoison and Azure SSRF |
| **40+** | CVEs against MCP (Jan-Apr 2026) |
| **12,520** | Publicly exposed MCP services (Censys); ~40% unauthenticated |
| **8.5%** | Remote servers implementing mandatory OAuth 2.1 + PKCE |
| **24,008** | Secrets in MCP config files on public GitHub (GitGuardian) |
| **73%** | Production outages starting at transport layer |
| **300-800ms** | MCP baseline overhead (cannot be cached away) |
| **11 microseconds** | Bifrost gateway overhead (Go-based, 5,000+ RPS) |
| **190** | Member organizations in AAIF (Linux Foundation) |

---

## Quick Reference

- **Three roles**: host (policy + UX), client (wire protocol, 1:1 per server), server (tools + resources + prompts).
- **Three primitives**: tools (model-controlled), resources (app-controlled), prompts (user-controlled).
- **Model never speaks MCP**: it emits native tool selection; host's MCP client translates.
- **2026-07-28 = stateless**: no handshake, `_meta` in every request, round-robin LB, serverless viable.
- **Token overhead is the #1 operational concern**: 200-500 tokens/tool; mitigate with search-first, tiered discovery, gateway aggregation.
- **Zero-Trust**: OAuth 2.1 + PKCE, RFC 8707 audience, no token passthrough, hash-pin tool definitions, per-server circuit breakers.
- **Protocol triangle**: function calling (foundation) -> MCP (agent to tools) -> A2A (agent to agent). Complementary, not competing.
- **Failure taxonomy**: transient (retry), permanent (degrade), protocol (re-discover), adversarial (quarantine).
- **MRTR**: `input_required` for multi-round; Tasks extension for long-running; idempotency keys for durability.
- **PII**: no protocol-level redaction; gateway filters, output schema validation, audit trail are your responsibility.
- **CVE landscape is real**: 40+ CVEs, 12,520 exposed services, 8.5% with proper auth. This is not theoretical.
