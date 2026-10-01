# Research: How OpenClaw Works
**Date researched**: 2026-09-29
**Sources consulted**: 14

## 1. System Topology & Mechanics

### What OpenClaw Is

OpenClaw is an open-source, self-hosted AI agent framework that converts LLMs into persistent, autonomous digital workers. Originally "Clawdbot" (Nov 2025), renamed OpenClaw in Jan 2026 by creator Peter Steinberger. MIT-licensed, stewarded by the OpenClaw Foundation (501(c)(3)). As of Sep 2026: ~391K GitHub stars, ~82K forks, ~102K commits. No paid tier, hosted service, or token.

The core philosophy: **"brain and body" separation**. The LLM is the brain (reasoning); OpenClaw is the body (execution). You supply intelligence via API key; it supplies infrastructure.

### Two-Layer Architecture

The architecture follows **"trusted gateway, untrusted execution, deterministic policy"**.

**Layer 1: Gateway (Control Plane)**
- Node.js daemon, persistent background process
- Binds to `ws://127.0.0.1:18789` by default
- Manages: sessions, presence, configuration, cron jobs, webhooks, channel routing
- Authenticates incoming messages, creates/retrieves sessions, assembles LLM context
- Handles tool call routing and result feeding
- Serves the Control UI (browser dashboard at `http://localhost:18789`)
- Intercepts dangerous commands for human approval before execution
- Supports **hot configuration reloads** -- model changes, permissions, channels applied without restart
- Installed as system service: launchd on macOS, systemd on Linux (via `openclaw onboard --install-daemon`)

**Layer 2: Pi (Agent Runtime)**
- Minimal coding agent runtime by Mario Zechner
- Communicates with Gateway over RPC (tool streaming + block streaming)
- Deliberately minimal: ships with only **four core tools**: Read, Write, Edit, Bash
- Has "the shortest system prompt of any coding agent"
- Self-extending: agent writes its own extension code, hot-reloads, tests, iterates

### Channel Layer (Messaging Abstraction)

Each messaging platform is a separate plugin/adapter that normalizes messages into a standard internal format (stable identity key, content, metadata, attachments). After normalization, everything is platform-agnostic.

**20+ supported channels**: Telegram, WhatsApp, Slack, Discord, Signal, iMessage (via BlueBubbles), Microsoft Teams, Google Chat, Matrix, Nostr, IRC, and more.

Platform-specific protocols:
- **Telegram**: Bot token with polling or webhook delivery
- **WhatsApp**: Reverse-engineered web protocol via Baileys; QR-code auth; local session state
- **Slack**: Socket Mode with two separate token types (via Bolt)
- **Discord**: Gateway WebSocket with heartbeat requirements (via discord.js)

Security policies (allowlists, pairing workflows, mention-gating) apply uniformly across all channels.

### LLM Layer

**Model-agnostic**: Claude, GPT-4/5, Gemini, DeepSeek, Grok, local models via Ollama. Models swappable at runtime without reconfiguration.

**Prompt assembly** per request:
1. Agent's configured personality (SOUL.md -- injected as top-level priority)
2. List of available skills
3. Relevant memory from previous sessions (auto-retrieved)
4. Current conversation history

**Agentic execution loop**: LLM receives assembled context -> decides tool calls -> OpenClaw executes tools -> captures output -> feeds results back -> LLM decides next action or final answer. Loop continues until no further tool requests. Formally: "intake > context assembly > model inference > tool execution > streaming replies > persistence."

**Context compaction**: When conversation nears model's context limit (~90% threshold), automatic summarization replaces raw history with condensed summary. Essential state flushed to filesystem before compaction so nothing is permanently lost. Manual trigger: `/compact`.

### Configuration-First Design (SOUL.md)

Unlike frameworks requiring Python/JS code to define agents, OpenClaw uses a **Markdown file (SOUL.md)** containing: identity, behavior rules, available tools, memory settings, channel connections. This is injected as top-level priority during context construction, not lost in long context windows.

### Plugin & Skills System

Nearly everything outside the core engine is a plugin. Loaded at startup; disabled by changing one config line. Community plugins installed by placing in the correct directory.

**Lifecycle hooks**: before/after tool calls, before/after messages, session start/end. Use cases: audit logging, rate limiting, custom approval flows.

**Skills**: Plain Markdown files (directories containing `SKILL.md`) instructing the agent on specific tasks. **5,400+ skills** available on **ClawHub** (marketplace/registry). Precedence: workspace skills > global > bundled. Skills are injected into every prompt (cost implication).

**Self-extension**: Rather than downloading plugins, the agent writes its own tool code, hot-reloads it, tests it, and iterates. Extensions can register new tools, render TUI components, and persist state into sessions.

### Multi-Agent Orchestration

Does NOT use a centralized orchestrator for task decomposition. Uses **sessions as the primitive**. Each session is an isolated agent with its own conversation history, system prompt, and tool set.

**Session tools for coordination**:
- `sessions_list` -- discover peers
- `sessions_history` -- read another session's transcript
- `sessions_send` -- message another session (with reply-back ping-pong via `REPLY_SKIP` / `ANNOUNCE_SKIP` flags)
- `sessions_spawn` -- create new agent sessions

This is **message-passing, not graph traversal**. Closer to a microservices pattern than an orchestration graph (contrast with LangGraph's pre-defined topology).

**Sandboxing for multi-agent**: Non-main sessions can run inside per-session Docker containers (`agents.defaults.sandbox.mode: "non-main"`). Allowlist of safe tools (`bash, read, write, edit, sessions_*`), denylist of dangerous ones (`browser, canvas, nodes, cron`).

### Tree-Based Sessions

Sessions are **trees, not flat logs**. Users can branch and navigate within a session.

**Failure recovery via branching**: When a tool breaks mid-task, agent branches into a side-quest to diagnose/fix. Once fixed, it rewinds to the main branch and summarizes the side branch. Main session context stays clean.

**MCP tool management**: Agent can branch, update tooling, and return without breaking main session coherence -- solves the problem of reloading tool definitions without trashing cache.

## 2. Token Economics & NFR Metrics

### Cost Structure

Costs are USD per 1M tokens across four buckets: `input`, `output`, `cacheRead`, `cacheWrite`.

**Baseline overhead**: ~8,000 tokens of core instructions and skills sent with every request. Adding skills is cheap per-skill but increases this permanent baseline.

**Relative cost examples** (approximate):
- Code writing task: ~$0.018
- Web page fetch: ~$0.180 (10x code writing, due to large HTML context)
- Image processing: ~$0.011

**Tiered pricing**: When a model publishes tiered pricing, tier selection uses total prompt input (uncached + cache reads + cache writes). Output tokens don't affect tier selection.

### Real-World Cost Ranges

| User Profile | Monthly Cost (USD) |
|---|---|
| Personal / light use | $6--13 |
| Small team | $25--50 |
| Mid-sized / scaling team | $50--100 |
| Heavy automation (thousands of interactions/day) | $100+ |

**Field-reported optimization**: One team cut response time from 23s to 4s and monthly cost from $347 to $68 by fixing context growth and model routing (not hardware changes).

### Context Window Management

**Bootstrap injection limits**:
- Individual large file: `bootstrapMaxChars` default = 20,000 chars
- Total bootstrap: `bootstrapTotalMaxChars` default = 60,000 chars

**Tool result caps** (derived from model window):
- Below 100K tokens: 16,000 chars
- 100K+ tokens: 32,000 chars
- 200K+ tokens: 64,000 chars
- 30% context-share guard caps any single tool result

**Provider-specific budgets**: OpenAI GPT-5.5/5.6 have 1,050,000 total window but OpenClaw defaults runtime budget to 272,000 tokens. Opting into 922,000 input budget triggers higher long-context pricing.

### Prompt Caching

- Cache reads significantly cheaper than standard input tokens
- **Heartbeat cache warming**: Setting heartbeat interval just under cache TTL (e.g., 55 min for 1-hour TTL) keeps cache warm across idle gaps
- Per-agent cache strategy via `agents.entries.*.params.cacheRetention` ("long" for deep sessions, "none" for bursty)
- **April 2026 gotcha**: Anthropic's prompt-cache TTL silently dropped from 1 hour to 5 minutes. Teams assuming 1-hour cache saw bills triple.

### Performance Benchmarks

**WildClawBench harness comparison** (OpenClaw vs. Claude Code, Codex, Hermes Agent):
- Per-task time: OpenClaw faster on every backend model (5.83--9.18 min vs. 6.44--10.30 min)
- Task success rate: OpenClaw wins on 3 of 4 models; Codex leads on GPT-5.4

**Enterprise latency** (AWS EC2 G4dn.xlarge, 500 req/s):
- p50 latency under 8ms with 4 GPU instances [inferred: this refers to local model serving via OpenClaw's inference layer, not the agent orchestration itself]
- 60% lower per-token cost vs. serverless APIs at scale [inferred: at high utilization with self-hosted models]

### Optimization Techniques

- `/compact` to summarize long sessions
- Trim large tool outputs in workflows
- Reduce `imageMaxDimensionPx` (default 1,200px) for screenshot-heavy work
- Keep skill descriptions short (injected into every prompt)
- Route simple tasks to budget models (e.g., Gemini 2.5 Flash at $0.15/1M tokens), reserve premium models for complex reasoning

## 3. Distributed Resilience & State

### Three-Tier Memory System

**Short-term memory**: Each session saved as a file on disk. Only the recent portion loaded into context per message. Prevents full history load on every interaction.

**Long-term memory**: All past sessions indexed into a **local SQLite database**. Before answering any message, a memory indexer searches the database and pulls relevant information. Cross-session recall spanning days/weeks without manual reminding.

**Standing notes**: User-authored Markdown files in `~/.openclaw/workspace/memory/`. Permanent briefing documents (preferences, projects, context). Read by agent on every interaction.

### State Persistence

- Memory and agent knowledge stored as **editable Markdown files on disk** -- inspectable, version-controllable (Git), portable
- For multi-session tasks: agent writes current state to `MEMORY.md` or workspace files, then pauses. Resumes from saved state when user returns.
- **Write-ahead queue**: If agent fails mid-execution, resumes from last saved checkpoint
- Session state persisted via `sessions.patch` WebSocket method -- state survives reconnections and restarts
- SQLite as hard data layer for facts outside LLM context window

### Long-Running Task Model

- Agent acknowledges task, begins work asynchronously, messages user upon completion
- User can disconnect (lock phone, walk away) while Gateway continues running
- Tasks requiring mid-way human input: agent completes what it can, persists state, pauses
- **Cron jobs** run in **isolated session mode**: each run gets own session, completes work, delivers output, exits. Prevents stale context accumulation.
- **Heartbeat system + cron jobs** enable autonomous scheduled actions (check servers, triage email, send summaries)

### Fault Tolerance: Three-Layer Retry

**Model layer**: Automatic failover on 502/503/504 errors. Configurable fallback chain (e.g., Claude -> GPT-4o -> DeepSeek). Auth profile rotation cycles between OAuth tokens and API keys to handle rate limits.

**Channel layer**: Per-channel configurable retry. Adapters handle HTTP 429, ETIMEDOUT, ECONNRESET using exponential backoff. Platform-specific tuning (each messaging platform has different rate limit behaviors).

**Tool execution layer**: Auto-retry of failed tool calls. Leverages deterministic/idempotent tool design (Read, Write, Edit, Bash are all retryable without side effects).

### Infrastructure Recovery

- Gateway daemon as system service for auto-restart
- **`openclaw doctor`**: CLI diagnostic resolving "over 80% of common issues". With `--repair` or `--deep` flags: auto-heals corrupt sessions, fixes environment issues, resolves config problems.
- `openclaw security audit --fix` for permission/security issues
- `openclaw config validate` for pre-runtime configuration checks
- Known trade-off: rapid successive gateway restarts can cause cron job losses

## 4. Enterprise Security & Governance

### Security Model

**Command interception**: Dangerous shell commands require explicit user approval via messaging channel before execution. Agent waits indefinitely if no response.

**Allowlists and pairing workflows**: DM-capable channels pair unknown senders by default. Approval via `openclaw pairing approve <channel> <code>`. Applied uniformly across all channels.

**Mention-gating**: Controls when agent responds.

**Sender authentication**: At Gateway level for every incoming message.

**Data sovereignty**: All memory files stored locally on user's machine. "By default OpenClaw itself phones home for nothing but a daily version check." Anonymous feature statistics are opt-in. Disable all with `update.checkOnStart: false`.

### Sandbox Architecture

- Non-main sessions run in per-session Docker containers
- Tool allowlists/denylists per session type
- Serial command queues per session prevent race conditions

### Governance

- OpenClaw Foundation, independent 501(c)(3)
- No paid tier, hosted service, or token
- Donors: Amazon, Lobster Computer Company, Offline Holdings, OpenAI, Red Hat, University of Michigan
- Infrastructure sponsors: Blacksmith, Convex, GitHub, NVIDIA, Vercel
- After Peter Steinberger joined OpenAI (March 2026), governance shifted to a technical steering committee (Grok Research, Armalo AI, independent maintainers). Development velocity actually increased (47 merged PRs in two weeks post-announcement).

### Critical Security Incidents

**CVE-2026-25253 ("ClawBleed")** -- CVSS 8.8, RCE via cross-site WebSocket hijacking:

1. Malicious webpage silently triggers Control UI, auto-connects and steals auth token
2. Attacker uses stolen token to open direct WebSocket to victim's local instance, bypassing firewall/localhost protections
3. Attacker disables confirmation prompts, escapes container sandbox, achieves full RCE

**Root causes**: WebSocket connections accepted without origin verification. Authentication disabled by default. OAuth credentials in plaintext JSON. mDNS broadcasting configuration across LAN.

**Scale**: 42,000+ publicly exposed instances at disclosure (Feb 3, 2026). 93% with critical authentication bypass. 1.5 million API tokens leaked.

**"ClawHavoc" supply chain attack** (Feb 2026): Snyk discovered 1,184 malicious skills on ClawHub. The #1 ranked skill was performing data exfiltration (Cisco finding).

**Industry response**:
- Microsoft: "It is not appropriate to run it on a standard personal or corporate machine" (Feb 19, 2026)
- Kaspersky: OpenClaw "in its default configuration, is unsuitable for any deployment handling sensitive data"
- Palo Alto Networks: Named it "the potential biggest insider threat of 2026"

**Patched** in v2026.1.29 within 72 hours. Current recommended minimum: v2026.8.1 (fixes two high-severity advisories from Sep 11, 2026).

### The "Lethal Trifecta" + Persistent Memory

Palo Alto Networks' extended threat model for AI agents:
1. Access to private data (emails, files, credentials)
2. Exposure to untrusted content (web browsing, messages, third-party skills)
3. Ability to communicate externally (sending emails, API calls)
4. **Persistent memory** (SOUL.md, MEMORY.md): Malicious payloads can be fragmented across time -- injected on day 1, detonated when state aligns on day N

## 5. Production Failure Modes

### Known Failure Patterns

| Failure Mode | Description | Mitigation |
|---|---|---|
| **Context explosion** | Unrestricted tool output fills context window | Tool result caps (30% guard), `/compact`, truncation limits |
| **Cache TTL surprise** | Provider silently changes cache TTL (Anthropic: 1hr -> 5min, March 2026) | Monitor billing; use heartbeat cache warming; set explicit `cacheRetention` |
| **WebSocket hijacking** | CVE-2026-25253: any website could connect to local Gateway | Origin verification (patched), disable default auth bypass |
| **Supply chain poisoning** | Malicious skills on ClawHub (1,184 found) | Audit skills, use workspace-local skills, monitor skill behavior |
| **Gateway timeout** | Single tool call takes too long | Break large work into smaller batches with progress reporting |
| **Human-in-the-loop deadlock** | Agent waits indefinitely for approval that never comes | Timeout configuration, notification channels |
| **Cron job loss** | Rapid successive gateway restarts | Stable daemon management, avoid frequent restarts |
| **Cross-session state corruption** | Multi-agent sessions sharing mutable state | Per-session Docker sandboxing, serial command queues |
| **Prompt injection via persistent memory** | Malicious payload stored in MEMORY.md, detonated later | Memory sanitization, audit memory files, restrict memory write sources |

### Debugging & Observability

- **Control UI**: Real-time dashboard showing every tool call, model response, approval request, intermediate step
- **`/status`**: Session model, context usage, last I/O tokens, cost
- **`/usage tokens`**: Per-response token/cache details
- **`/usage full`**: Compact model/context/cost details
- **`openclaw doctor`**: Diagnoses and auto-repairs 80%+ of common issues
- **`openclaw config validate`**: Pre-runtime configuration checks
- **`openclaw security audit`**: Security posture assessment
- Standardized telemetry emitted by agents regardless of underlying language runtime (post-March 2026 unified execution model)
- Prometheus exporter for production monitoring (mentioned in enterprise deployments)

### Recovery Strategies

- **Model failover chain**: Automatic fallback across providers on 5xx errors
- **Session branching**: On tool failure, branch into diagnostic side-quest, fix, rewind to main branch
- **Write-ahead queue**: Resume from last checkpoint after mid-execution failure
- **`openclaw doctor --repair --deep`**: Auto-heal corrupt sessions and environment issues
- **Token/credential rotation**: Post-incident remediation for compromised instances

## 6. Enterprise System Design Scenarios

### Deployment Architectures

**Personal deployment** (most common):
- Single Gateway on laptop/Mac Mini
- Direct LLM API keys (Anthropic, OpenAI, etc.)
- Local SQLite for memory
- 1--2 messaging channels
- Cost: $6--13/month in API usage

**Team deployment**:
- Gateway on shared VPS/server
- Remote access via Tailscale
- Multi-channel (Slack + Telegram + Discord)
- Per-agent workspaces with role isolation
- Cost: $25--100/month

**Enterprise deployment** (inferred from documented capabilities):
- Gateway cluster with horizontal scaling via **Hybro network protocol** for unified local/remote agent management
- Per-session Docker sandboxing for untrusted execution
- Multi-agent teams with session-based coordination
- Prometheus metrics + standardized telemetry
- Network segmentation (never expose Control UI)
- Regular `openclaw security audit` runs
- Managed option: **MyClaw** (cloud-hosted OpenClaw with scheduled tasks)

### Trade-Off Matrix

| Dimension | OpenClaw Strength | OpenClaw Weakness |
|---|---|---|
| **Autonomy** | 24/7 persistent daemon, cron jobs, proactive scheduling | Requires always-on host machine |
| **Security** | Local-first data sovereignty, per-session sandboxing | Authentication disabled by default; history of critical CVEs; supply chain risk from ClawHub |
| **Flexibility** | Model-agnostic, 20+ channels, self-extending tools | 2.3x more setup than managed platforms (reported); "3 weeks getting a single model serving pipeline stable" (team report) |
| **Cost** | Free software, self-hosted, budget model routing | API costs scale with usage; 8K token baseline per request; cache TTL surprises |
| **Multi-agent** | Session-based message-passing, dynamic spawning | No formal task decomposition / planning layer; coordination is ad-hoc |
| **State management** | Human-readable Markdown, Git-auditable | No built-in vector store; SQLite may not scale for very large deployments |
| **Ecosystem** | 5,400+ skills, active community (391K stars) | Supply chain attacks on ClawHub; skill quality varies |
| **Enterprise readiness** | Docker sandboxing, Prometheus, telemetry | Major vendors (Microsoft, Kaspersky) warn against default config on corporate machines |

### Comparison with Alternatives

| Aspect | OpenClaw | LangGraph | CrewAI | Claude Code |
|---|---|---|---|---|
| **Execution model** | Persistent daemon, 24/7 | Library, runs in your process | Library, runs in your process | Session-based CLI |
| **Session model** | Tree-based, ad-hoc spawning | Graph-defined, checkpoint persistence | Role-based delegation | Single conversation |
| **State** | Markdown files, Git-auditable | Database-backed | High-level abstractions | In-memory session |
| **Agent definition** | Markdown (SOUL.md) | Python code (graph definition) | Python code (role definitions) | Prompt + CLAUDE.md |
| **Sandboxing** | Per-session Docker | Not native | Not native | Process-level |
| **Self-extension** | Agent writes its own tools | No | No | No |
| **Channel support** | 20+ messaging platforms | None (library) | None (library) | Terminal only |
| **Stars (Sep 2026)** | ~391K | ~55K | ~30K | N/A |

### Key Architectural Insight for Interviews

OpenClaw's design validates a specific architectural thesis: **AI agents are an infrastructure problem, not a prompt engineering problem**. The system deliberately avoids LangChain, CrewAI, or any agent framework. Instead, it builds reliable primitives (session management, memory system, tool sandboxing, daemon lifecycle) and lets the LLM itself handle orchestration by writing code.

The March 2026 unified execution model (replacing `nodes.run` with a containerd interface + namespace isolation) reduced memory overhead by ~300MB per concurrent agent. This is the kind of infrastructure optimization that separates production agent systems from prototypes.

The security history (CVE-2026-25253, ClawHavoc) is equally instructive: when an AI agent has system-level access + persistent memory + exposure to untrusted content + ability to communicate externally, security cannot be an afterthought. The "lethal trifecta" + persistent memory framework from Palo Alto Networks is a useful mental model for evaluating any production agent deployment.

## Sources

1. [System Design Newsletter - OpenClaw Architecture](https://newsletter.systemdesign.one/p/openclaw-architecture)
2. [GitHub - openclaw/openclaw](https://github.com/openclaw/openclaw)
3. [AI Magicx - Best Open-Source AI Agent Frameworks in 2026](https://www.aimagicx.com/blog/best-open-source-ai-agent-frameworks-2026)
4. [Dextra Labs - OpenClaw AI Agent Framework](https://dextralabs.com/blog/openclaw-ai-agent-frameworks/)
5. [clawbot.blog - OpenClaw Explained (2026 Update)](https://www.clawbot.blog/blog/openclaw-the-ai-agent-framework-explained-2026-update/)
6. [Bibek Poudel / Medium - How OpenClaw Works](https://bibek-poudel.medium.com/how-openclaw-works-understanding-ai-agents-through-a-real-architecture-5d59cc7a4764)
7. [SoftmaxData - Deep Dive into OpenClaw's Agentic Orchestration](https://softmaxdata.com/blog/deep-dive-into-openclaws-agentic-orchestrate-design-patterns-philosophy-framework-choices/)
8. [OpenClaw Docs - Token Use and Costs](https://docs.openclaw.ai/reference/token-use)
9. [Markaicode - OpenClaw Benchmarks 2026](https://markaicode.com/benchmarks/openclaw-agent-benchmark/)
10. [Markaicode - OpenClaw for Enterprise AI](https://markaicode.com/usecases/openclaw-for-enterprise-ai/)
11. [adversa.ai - OpenClaw Security 101](https://adversa.ai/blog/openclaw-security-101-vulnerabilities-hardening-2026/)
12. [Broadcom - CVE-2026-25253 Protection Bulletin](https://www.broadcom.com/support/security-center/protection-bulletin/cve-2026-25253-openclaw-rce-vulnerability)
13. [runZero - OpenClaw RCE Vulnerability](https://www.runzero.com/blog/openclaw/)
14. [DEV Community - OpenClaw Security Catastrophe](https://dev.to/tiamatenity/openclaw-security-catastrophe-cve-2026-25253-and-the-largest-ai-privacy-breach-in-history-2ljl)
