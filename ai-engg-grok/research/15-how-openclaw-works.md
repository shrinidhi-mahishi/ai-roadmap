# Research: How OpenClaw Works

**Date researched**: 2026-09-30
**Sources consulted**: 16
**Identity note**: This is the OpenClaw Foundation project at [github.com/openclaw/openclaw](https://github.com/openclaw/openclaw) / [docs.openclaw.ai](https://docs.openclaw.ai) — a self-hosted multi-channel AI-agent **Gateway**. It matches Neo Kim’s System Design One #151 definition (Gateway on port `18789`, ClawHub skills, Markdown memory, Heartbeat/cron). Do not confuse with unrelated products that only share a similar name.

## 1. System Topology & Mechanics

### What OpenClaw is

OpenClaw is a **self-hosted orchestration layer** between an LLM (Claude, GPT, Gemini, local models, etc.) and real-world tools/channels. The LLM supplies reasoning; OpenClaw supplies the always-on “hands” — messaging adapters, tool execution, memory, scheduling, and a Control UI ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture); [docs home](https://docs.openclaw.ai/); [README](https://github.com/openclaw/openclaw)).

It is **MIT-licensed**, stewarded by the OpenClaw Foundation (independent 501(c)(3)), with **no paid tier, hosted service, or token** from the Foundation itself ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [README](https://github.com/openclaw/openclaw)). Third-party hosts (e.g. MyClaw, mentioned as a newsletter partner) can run OpenClaw in the cloud; that is not the Foundation product ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)).

Runtime requirement: **Node 26 recommended** (also Node 24.16+ or 26.1+) ([docs home](https://docs.openclaw.ai/)).

### Control plane: the Gateway

A single long-lived **Gateway** process is the control plane and source of truth for sessions, routing, channel connections, tools, and events ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture); [docs home](https://docs.openclaw.ai/)).

| Surface | Role |
| --- | --- |
| **Gateway daemon** | Owns messaging surfaces; exposes typed WebSocket API; validates frames against JSON Schema; emits `agent`, `chat`, `presence`, `health`, `heartbeat`, `cron` events ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)) |
| **Control-plane clients** | macOS app, CLI, web Control UI, automations — one WS connection each; methods like `health`, `status`, `send`, `agent` ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)) |
| **Nodes** | macOS/iOS/Android/headless devices connect with `role: node`, device pairing, and caps such as `camera.*`, `screen.record`, `location.get` ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)) |
| **Channels** | WhatsApp (Baileys), Telegram (grammY), Slack, Discord, Signal, iMessage, WebChat, plus plugin channels (Matrix, Teams, etc.) ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture); [docs home](https://docs.openclaw.ai/)) |

**Default bind**: WebSocket/HTTP on `127.0.0.1:18789` (Control UI at `http://127.0.0.1:18789/`). Port resolution: `--port` → `OPENCLAW_GATEWAY_PORT` → `gateway.port` → **`18789`** ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture); [Gateway CLI](https://docs.openclaw.ai/gateway); [System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)).

**Invariant**: exactly one Gateway per host owns a single Baileys (WhatsApp) session ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)).

### Wire protocol

- Transport: WebSocket, JSON text frames.
- First frame **must** be `connect`; non-JSON / non-connect → hard close.
- After handshake: `{type:"req", id, method, params}` → `{type:"res", id, ok, payload|error}`; server push `{type:"event", event, payload, seq?, stateVersion?}`.
- Side-effecting methods (`send`, `agent`) require **idempotency keys**; server keeps a short-lived dedupe cache.
- Events are **not replayed**; clients refresh on gaps ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)).

Remote access preferred via **Tailscale/VPN** or SSH tunnel (`ssh -N -L 18789:127.0.0.1:18789 …`) ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)).

### Data plane: agent loop (ReAct-style tool loop)

Entry: Gateway RPC `agent` / `agent.wait`, or CLI `openclaw agent` ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).

Sequence ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop); [System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)):

1. Authenticate sender / resolve session; `agent` returns `{ runId, acceptedAt }` immediately.
2. Assemble system prompt: base prompt + skills + bootstrap files (`SOUL.md` / `AGENTS.md` / `USER.md` / memory) + conversation history.
3. Call the model; on tool calls, execute tools, feed results back; loop until a final reply (no further tools).
4. Stream `assistant` / `tool` / `lifecycle` events to Control UI and channel adapters.
5. Persist transcript; apply compaction when context limits are hit.

This is a classic **tool-using agent loop** (plan → act → observe), not a static DAG. Vendor harnesses (Codex app-server, Claude Code stdio, Copilot SDK) can replace the embedded loop while OpenClaw still owns channels, sessions, policy, and state ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).

### Channel layer (normalized ingress)

Each messaging platform is a **plugin/adapter** that translates into a stable internal message (identity key, content, metadata, attachments) before the Gateway sees it — so allowlists, pairing, and mention-gating apply uniformly ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture); [Channels](https://docs.openclaw.ai/channels)).

DM policies per channel include `pairing` (default), `allowlist`, `open`, `disabled` ([Telegram channel docs](https://docs.openclaw.ai/channels/telegram); [Getting started](https://docs.openclaw.ai/start/openclaw)).

### Multi-agent routing

One Gateway can host **many isolated agents**, each with separate workspace, `agentDir`, auth profiles, and session store under `~/.openclaw/agents/<id>/…`. Inbound routing uses deterministic **bindings** (most-specific wins): peer → parentPeer → guild/roles → guild → team → accountId → channel → default agent ([Multi-agent](https://docs.openclaw.ai/concepts/multi-agent)).

Config/state defaults ([Multi-agent](https://docs.openclaw.ai/concepts/multi-agent)):

- Config: `~/.openclaw/openclaw.json` (or `OPENCLAW_CONFIG_PATH`)
- State: `~/.openclaw` (or `OPENCLAW_STATE_DIR`)
- Workspace: `~/.openclaw/workspace` (or per-agent workspace)

### Skills, plugins, standards

- **Skills**: Markdown/`SKILL.md` procedure bundles; install via ClawHub (`openclaw skills install …`) ([Skills](https://docs.openclaw.ai/tools/skills); [ClawHub](https://docs.openclaw.ai/clawhub)).
- **Plugins**: channels, model providers, tools, hooks, media — install with `openclaw plugins install clawhub:<package>`; compatibility checks `pluginApi` / `minGatewayVersion` ([Building plugins](https://docs.openclaw.ai/plugins/building-plugins); [ClawHub](https://docs.openclaw.ai/clawhub)).
- Open standards: MCP client **and** server; Linux Foundation **A2A 1.0**; Agent Client Protocol; AgentSkills; optional OpenAI-compatible Gateway API (`/v1/chat/completions`, etc., disabled by default); OpenTelemetry / Prometheus ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).

The newsletter cites **5,400+** skills on ClawHub ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)). Official ClawHub docs describe the registry but do not publish a live catalog size in the pages reviewed — treat **5,400+ as secondary/newsletter quantification**, not a Foundation SLA.

### Proactive scheduling

- **Automations / cron**: durable scheduler for one-shot and recurring jobs; can deliver to channels or webhooks ([Automation](https://docs.openclaw.ai/automation/tasks)).
- **Heartbeat**: system-owned ambient monitor automation; default cadence **every 30 minutes** (API-key auth) or **1h** (OAuth); set `heartbeat.every: "0m"` to disable recurring cadence. Scheduled heartbeats require cron enabled; busy deferral waits for main/automation/session work ([Heartbeat](https://docs.openclaw.ai/gateway/heartbeat); [Getting started](https://docs.openclaw.ai/start/openclaw)).
- Gateway owns one `GatewayScheduler` for maintenance and cron wakeups; durable deadlines reconstruct on startup ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)).

### Long-running work

Newsletter guidance: acknowledge, work asynchronously, message when done; persist mid-task state to `MEMORY.md` / workspace files; prefer isolated cron sessions for recurring long jobs; break slow tool calls into batches because a single tool call can time out waiting ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)). Docs support this with separate `cron` / `cron-nested` queue lanes so background work does not block inbound replies ([Queue](https://docs.openclaw.ai/concepts/queue)).

---

## 2. Token Economics & NFR Metrics

> ⚠️ **Limited public data available for this dimension.** OpenClaw does **not** publish p50/p95/p99 end-to-end latency SLAs, RPM/TPM product limits, or a `$ per 1k executions` formula. Token spend is almost entirely the **model provider’s** pricing plus optional embedding providers for memory search. Numbers below are **timeouts, concurrency caps, and compaction knobs** from official docs — not production latency benchmarks.

### Latency & timeout budgets (documented)

| Knob | Default | Semantics |
| --- | --- | --- |
| `agent.wait` | **30s** | Client wait only; does **not** cancel the run ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)) |
| `agents.defaults.timeoutSeconds` | **172800s (48h)** | Whole-run elapsed budget; progress does not reset it; `0` = unlimited (provider idle watchdogs still apply) ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)) |
| Model idle timeout | Cloud **120s**; self-hosted **300s** | Abort model request if no response chunks ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)) |
| Cron cloud stream stall (with explicit cron timeout) | Cap **60s** | Allows model fallback before outer cron deadline ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)) |
| Queue notice | **~2s** wait | Verbose logs emit “queued for …ms” if wait exceeds ~2s ([Queue](https://docs.openclaw.ai/concepts/queue)) |
| Queue debounce | **500ms** built-in | Steer / followup / collect batching ([Queue](https://docs.openclaw.ai/concepts/queue)) |
| Telegram pairing code TTL | **1 hour** | ([Telegram channel](https://docs.openclaw.ai/channels/telegram)) |
| Sub-agent announce timeout | **120000 ms** default | ([Config: sessions/subagents](https://docs.openclaw.ai/gateway/config-tools/sessions-and-subagents)) |

Example getting-started config uses `timeoutSeconds: 1800` (30 minutes) for a personal WhatsApp assistant ([Getting started](https://docs.openclaw.ai/start/openclaw)) — an operator override, not a published SLA.

### Throughput & back-pressure

- Inbound auto-replies serialize through an **in-process lane-aware FIFO queue** ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Per-session: **one agent run at a time** (session lane).
- Global `main` lane: capped by `agents.defaults.maxConcurrent`; default **`min(16, max(8, available CPU parallelism))`** ([Config agents](https://docs.openclaw.ai/gateway/config-agents); [Queue](https://docs.openclaw.ai/concepts/queue)).
- Sub-agents: `agents.defaults.subagents.maxConcurrent` default **8** per spawning session; Swarm collector lane default **32** (`tools.swarm.maxConcurrent`) ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Queue modes: `steer` (default), `followup`, `collect`, `interrupt`; default `cap: 20`, `drop: "summarize"` ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Background maintenance (Skill Workshop + plugin completions including dreaming): shared budget of **3** concurrent runs ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Heartbeat runs use bounded `cron-nested` admission so they do not starve inbound replies ([Queue](https://docs.openclaw.ai/concepts/queue)).

### Model routing & cost controls

- Primary + fallbacks: `agents.defaults.model.primary` / `.fallbacks` (e.g. Sonnet primary, GPT fallback) ([Configuration](https://docs.openclaw.ai/gateway/configuration); [Model failover](https://docs.openclaw.ai/concepts/model-failover)).
- Failover stages: bounded same-model recovery for rate limits → **auth-profile rotation** within provider → **model fallback** chain. Fallback is **turn-local** (does not rewrite the session’s selected model) ([Model failover](https://docs.openclaw.ai/concepts/model-failover)).
- Explicit user session model selections are **strict** (no automatic fallback unless configured) ([Model failover](https://docs.openclaw.ai/concepts/model-failover)).
- Compaction can use a cheaper/local model override (`compaction.model`, `memoryFlush.model` e.g. `ollama/qwen3:8b`) ([Heartbeat/compaction config](https://docs.openclaw.ai/gateway/config-agents/heartbeat-compaction-and-streaming)).
- Provider params can set `cacheRetention: "long"` as a global default in config examples ([Config agents](https://docs.openclaw.ai/gateway/config-agents)) — this is a **provider-side prompt-cache hint**, not an OpenClaw semantic cache with published hit rates.

### Compaction / context cost mechanics

When conversation hits model limits, **auto-compaction** summarizes history (default enabled). Notable knobs ([Heartbeat/compaction config](https://docs.openclaw.ai/gateway/config-agents/heartbeat-compaction-and-streaming); [Memory](https://docs.openclaw.ai/concepts/memory)):

- `keepRecentTokens: 50000` (example/default in config docs)
- `recentTurnsPreserve: 3`
- `timeoutSeconds: 180` for compaction turn
- `memoryFlush` soft threshold **6000** tokens; `forceFlushTranscriptBytes: "2mb"`
- Optional `maxActiveTranscriptBytes: "20mb"` preflight local compaction

Before compaction, a silent **memory flush** turn writes durable facts to Markdown (default on) ([Memory](https://docs.openclaw.ai/concepts/memory)).

**[inferred]** Per-message token cost ≈ (system/skills/memory bootstrap + recent session window + tool payloads) × provider $/MTok; Heartbeat every 30m adds recurring baseline spend even with no user traffic.

---

## 3. Distributed Resilience & State

### State model (local-first, durable files + SQLite)

| Store | Location / mechanism | Role |
| --- | --- | --- |
| Config | `~/.openclaw/openclaw.json` | Gateway, channels, agents, tools ([Multi-agent](https://docs.openclaw.ai/concepts/multi-agent)) |
| Workspace memory | `MEMORY.md`, `USER.md`, `memory/YYYY-MM-DD.md`, optional `DREAMS.md` | Human-editable durable memory ([Memory](https://docs.openclaw.ai/concepts/memory)) |
| Sessions | `~/.openclaw/agents/<id>/sessions` + per-agent DB | Chat history, routing; `chat.send` input durably stored before ack ([Multi-agent](https://docs.openclaw.ai/concepts/multi-agent); [Queue](https://docs.openclaw.ai/concepts/queue)) |
| Memory index | Builtin **SQLite** hybrid search (vector + keyword); optional Honcho / LanceDB plugins ([Memory](https://docs.openclaw.ai/concepts/memory)) |
| Scheduler | GatewayScheduler + durable job deadlines | Cron/heartbeat wakeups reconstruct at startup ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)) |

There is **no hidden in-model state**: “the model only remembers what gets saved to disk” ([Memory](https://docs.openclaw.ai/concepts/memory)).

### Checkpointing / writer fencing

- Before streaming, a run records durable `activeWriterRunId`; transcript commits verify `expectedWriterRunId` so a superseded run cannot commit stale data ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).
- SQLite writer queue orders per-agent mutations; a **Gateway state-directory lock** prevents two Gateway / `openclaw agent --local` processes from owning the same state dir ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).
- Compaction / truncation use the same in-transaction writer-claim fence ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).

### Queue durability limits

- Ordinary Control UI / TUI / CLI / RPC input is stored in the per-agent DB before acknowledgment ([Queue](https://docs.openclaw.ai/concepts/queue)).
- The **in-memory queue is not replayed** after Gateway stop; interrupted queued input requires explicit resend ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Channel messages retained by durable ingress remain retryable if abandoned before agent-turn adoption; adopted/consumed messages keep duplicate suppression ([Queue](https://docs.openclaw.ai/concepts/queue)).

### Failover & circuit-style breaks

- Model failover chain with cooldowns; exhaustion surfaces structured per-attempt details ([Model failover](https://docs.openclaw.ai/concepts/model-failover)).
- Terminal stops for `agent_run_terminal_timeout` and **`idle_timeout_circuit_breaker`** (cost-runaway / idle breaker) halt the fallback chain ([Model failover](https://docs.openclaw.ai/concepts/model-failover)).
- Session diagnostics classify `session.long_running` / `session.stalled` / `session.stuck` with abort/recovery on heartbeat ticks ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Retryable chat errors keep a **15-second** grace window for fallback/restart of the same run ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).
- Supervision: launchd/systemd with example `Restart=always`, `RestartSec=5`, `TimeoutStopSec=30` ([Gateway docs](https://docs.openclaw.ai/gateway)).

### Hot reload & ops

Newsletter: Gateway supports **hot configuration reloads** (model/permissions/channels without full restart) ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)). Official architecture emphasizes supervision + doctor migrations for versioned state; treat hot-reload breadth as **newsletter-confirmed**, verify against installed version for exact surfaces.

### What this is not

OpenClaw’s default topology is **one Gateway process per host/trust domain**, not Kafka/Temporal-backed multi-region orchestration. Cloud workers / nodes can move *execution*; the Gateway remains the session authority ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)).

> ⚠️ **Limited public data**: no published Temporal/Kafka reference architecture, distributed lock service beyond the local state-dir lock, or multi-region RPO/RTO numbers.

---

## 4. Enterprise Security & Governance

### Trust model (critical)

OpenClaw’s documented security model is a **personal-assistant / single trust boundary per Gateway**. It is **not** a hostile multi-tenant boundary for adversarial users sharing one agent ([Security](https://docs.openclaw.ai/gateway/security); [Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).

- Multi-tenant hosting guidance: **one isolated Gateway cell per tenant** ([Security](https://docs.openclaw.ai/gateway/security)).
- Fleet multi-tenancy is described as **still experimental**; roles/session ownership are collaboration guardrails ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).
- Sandboxing and exec approvals are **off by default**; defaults favor a trusted single-operator setup ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)).

### Gateway auth & pairing

- Auth modes: `token` (default), `password`, `trusted-proxy`, or private-ingress `none` (keep off public ingress) ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture); [Tailscale](https://docs.openclaw.ai/gateway/tailscale)).
- All WS clients present **device identity**; new devices need pairing; loopback can auto-approve; Tailnet/LAN require explicit approval; connects sign `connect.challenge` (v3 binds platform/deviceFamily) ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)).
- Tailscale Serve can authenticate Control UI/WS via identity headers when `gateway.auth.allowTailscale` is true (default for Serve); operators wanting shared-secret everywhere should disable it ([Security](https://docs.openclaw.ai/gateway/security); [GitHub discussion #57110](https://github.com/openclaw/openclaw/issues/57110)).
- Preferred remote pattern: **loopback Gateway + Tailscale Serve**; avoid Funnel/public exposure ([Exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook)).

### Channel admission (RBAC-ish)

- DM: pairing / allowlist / open / disabled; groups: mention-gating and per-group allowlists ([Telegram](https://docs.openclaw.ai/channels/telegram); [Security](https://docs.openclaw.ai/gateway/security)).
- Built-in: non-owner senders cannot use `cron` or `gateway` tools regardless of config ([Security](https://docs.openclaw.ai/gateway/security)).
- `tools.toolsBySender` / owner tools reduce **direct** capability per requester but **do not** sanitize quoted history, forwards, or tool results in the prompt — not hostile multi-user isolation ([Security](https://docs.openclaw.ai/gateway/security)).

### Tool policy, exec approvals, sandbox

Three layers ([Sandboxing](https://docs.openclaw.ai/gateway/sandboxing); [Security](https://docs.openclaw.ai/gateway/security); [Exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook)):

1. **Tool allow/deny** (`tools.profile`, deny groups like `group:runtime` / `group:fs`).
2. **Exec security**: `deny` | allowlist/ask | `full` (default trusted-personal posture warns in audit).
3. **Sandbox backends** (off by default): Docker, Podman, SSH, OpenShell, Crabbox — Gateway stays on host; tool execution moves into sandbox. Modes include `non-main` / `all`; `workspaceAccess: none|ro|…`.

`tools.elevated` is an explicit **host escape hatch** for `exec` outside the sandbox — keep `allowFrom` tight ([Security](https://docs.openclaw.ai/gateway/security)).

Exec approvals bind exact request context; they are **operator intent guardrails**, not semantic modeling of every interpreter loader path ([Security](https://docs.openclaw.ai/gateway/security)).

Hardened baseline (docs): loopback + token auth, `dmScope: per-channel-peer`, messaging tool profile, `exec.security: "deny"`, `elevated.enabled: false`, pairing DMs ([Security](https://docs.openclaw.ai/gateway/security); [Exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook)).

### Supply chain (ClawHub / plugins)

- ClawHub shows scan state (VirusTotal, ClawScan, static analysis); **pending/stale scans can still install with a warning** — install ≠ proof every scan finished ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [ClawHub](https://docs.openclaw.ai/clawhub)).
- `openclaw skills verify` retrieves ClawHub’s verification envelope; it does **not** re-hash current local files by default ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).
- Native plugins run **in-process and are not sandboxed**; mitigate with allow-lists, install-policy hooks, pinned versions ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).
- Plugin packages validate digests / ClawPack artifacts on ClawHub install ([ClawHub](https://docs.openclaw.ai/clawhub)).

### Audit, secrets, compliance posture

- `openclaw security audit` / `--deep` / `--fix` with structured `checkId`s (e.g. `gateway.bind_no_auth`, `gateway.tailscale_funnel`, `tools.exec.security_full_configured`) ([Security](https://docs.openclaw.ai/gateway/security); [Audit checks](https://docs.openclaw.ai/gateway/security/audit-checks)).
- Metadata-only audit ledger for lifecycle/tool start/terminal — **without** copying prompts, tool args/results, or raw errors ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).
- Protected secrets via SecretRefs / handles; `openclaw secrets audit --check` for CI ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).
- Observability: OpenTelemetry + Prometheus export for SIEM ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).
- Public advisory record: Foundation cites **647** OpenClaw repository advisories as of **2026-08-27** snapshot — disclosure count, **not** a comparative safety score ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).

> ⚠️ **Limited public data for PII redaction / SOC2-as-a-product**: no first-party PII NER pipeline or SOC2/HIPAA certification package documented as a product feature. Compliance is operator-owned (self-host, audit export, retention, deletion limits via `openclaw memory forget` / provenance rules).

### MCP / zero-trust angle

OpenClaw is an MCP client (Streamable HTTP, SSE, stdio + OAuth) and MCP server; plugins can ship MCP servers ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)). Gateway WS auth + channel pairing are the primary trust gates; MCP adds tool surfaces that inherit tool policy/sandbox — **[inferred]** treat MCP servers like any other high-privilege plugin.

---

## 5. Production Failure Modes

### Context window degradation

- Symptoms: overflow / expensive full-history loads ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture); [Memory](https://docs.openclaw.ai/concepts/memory)).
- Mitigations: session windowing (recent turns only), auto-compaction + memory flush, hybrid `memory_search`, dreaming promotion into `MEMORY.md`, bootstrap truncation when `MEMORY.md` exceeds budget (`/context list`, `openclaw doctor`) ([Memory](https://docs.openclaw.ai/concepts/memory); [Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).

### Infinite / runaway execution

- Whole-run `timeoutSeconds` (default 48h, often lowered); model idle watchdogs (120s/300s); idle-timeout **cost-runaway circuit breaker** stops fallback ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop); [Model failover](https://docs.openclaw.ai/concepts/model-failover)).
- Queue `interrupt` aborts active run; `/stop` / `chat.abort` cancel queued+active work with ownership checks ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Heartbeat should stay disabled (`every: "0m"`) until the setup is trusted ([Getting started](https://docs.openclaw.ai/start/openclaw)).

### State drift / partial writes

- Writer-claim fencing prevents superseded runs from committing ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).
- Collect-mode coalescing commits combined turn + marks sources consumed in one transaction ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Versioned state + `openclaw doctor --fix` migrations; upgrades check compatibility ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).
- Failure mode: Gateway crash loses **in-memory** queue (not DB-acked input) ([Queue](https://docs.openclaw.ai/concepts/queue)).

### Cascading API timeouts / provider outages

- Bounded same-model retry → auth-profile rotation → model fallbacks ([Model failover](https://docs.openclaw.ai/concepts/model-failover)).
- Cron isolates timeouts: scheduler aborts at deadline, then bounded cleanup so stale children cannot pin lanes ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).
- Newsletter: break long scrapes/API calls into batched tool calls with progress reports ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)).

### Hallucinated / dangerous tool parameters

- Tool schemas + `before_tool_call` plugin hooks can `{ block: true }` ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).
- Shell/exec confirmation UX (newsletter: Gateway intercepts destructive shell and asks approval over Telegram) ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)); docs: exec ask/allowlist + sandbox ([Security](https://docs.openclaw.ai/gateway/security)).
- Prompt injection alone is **not** treated as an auth bypass in the trust model; isolation requires sandbox + separate gateways for adversarial users ([Security](https://docs.openclaw.ai/gateway/security)).
- Memory does **not** enforce policy — approvals/sandbox/scheduled tasks do ([Memory](https://docs.openclaw.ai/concepts/memory)).

### Exposure / misconfiguration incidents (documented risk classes)

Audit priorities ([Security](https://docs.openclaw.ai/gateway/security); [Exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook)):

1. Open DMs/groups **+** tools/exec enabled → prompt injection becomes shell/file actions.
2. LAN bind / Tailscale Funnel / missing auth.
3. Browser-control remote exposure.
4. World-readable `~/.openclaw` credentials.
5. Unallowlisted plugins / ClawHub skills with host privileges.

Incident response sketch from exposure runbook: remove public forwarding, rotate tokens, scrub `"*"` allowlists, review audit/tool history, re-run `openclaw security audit --deep` ([Exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook)).

> ⚠️ **Limited public data**: the System Design One post advertises a paid “what breaks and why” section; only the free teaser was available for this research. Prefer official security/queue/agent-loop docs for failure mechanics.

---

## 6. Enterprise System Design Scenarios

### Deployment patterns (documented)

| Pattern | Shape | When |
| --- | --- | --- |
| **Personal laptop assistant** | Gateway loopback, one agent, WhatsApp/Telegram, tools on host | Default getting-started path ([Getting started](https://docs.openclaw.ai/start/openclaw)) |
| **Always-on home/VPS** | `openclaw gateway install` (launchd/systemd), Heartbeat/cron, Tailscale to Control UI | Proactive monitoring & multi-channel ([Gateway](https://docs.openclaw.ai/gateway); [System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)) |
| **Hardened team cell** | Sandbox `mode: "all"` (Docker/OpenShell), deny/ask exec, SecretRefs, OTel→SIEM, pairing DMs, identity-aware proxy | Enterprise evaluation checklist ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [Exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook)) |
| **Multi-agent one Gateway** | Isolated workspaces + bindings (personal vs work WhatsApp accounts) | Same trust domain, different personas ([Multi-agent](https://docs.openclaw.ai/concepts/multi-agent)) |
| **Multi-tenant** | **One Gateway per tenant** (separate OS user/host preferred) | Adversarial or org isolation ([Security](https://docs.openclaw.ai/gateway/security)) |
| **Cloud execution** | Gateway local; tools/sessions on sandboxes, nodes, or disposable cloud workers | Shrink blast radius without moving session authority ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)) |

NVIDIA **NemoClaw** is cited as a distribution hardening OpenClaw with OpenShell kernel-level sandboxing ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).

### Trade-off matrix

| Approach | Cost | Latency | Ops complexity | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| Browser ChatGPT/Claude tab | Provider $ only | Interactive | None | Vendor SaaS boundary | N/A (no local tools) |
| OpenClaw default (host exec, sandbox off) | Provider $ + always-on host | Tool-loop + queue wait | Low (single process) | High blast radius if channels open | Single host / trust domain |
| OpenClaw + sandbox + deny exec | Same + container overhead | Higher tool latency | Medium | Stronger execution boundary | Still one Gateway cell |
| LangChain/CrewAI DIY | Eng time + infra | You choose | High | You design | You design |
| Per-tenant Gateway fleet | N × host/VPS | Same per cell | High (cells) | Isolation by cell | Horizontal by tenant, not shared process |

([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture); [Security](https://docs.openclaw.ai/gateway/security))

### Capacity planning (what is / isn’t published)

**Published / configurable:**

- `maxConcurrent` default formula from CPU parallelism (cap 16) ([Config agents](https://docs.openclaw.ai/gateway/config-agents))
- Sub-agent 8 / swarm 32 defaults ([Queue](https://docs.openclaw.ai/concepts/queue))
- Queue cap 20 with summarize-drop ([Queue](https://docs.openclaw.ai/concepts/queue))
- Heartbeat 30m default ambient turns ([Heartbeat](https://docs.openclaw.ai/gateway/heartbeat))
- Memory engines: SQLite builtin; optional LanceDB/Honcho ([Memory](https://docs.openclaw.ai/concepts/memory))

**Not published (do not invent):** tokens/sec, max concurrent end-users per Gateway, memory footprint budgets, multi-region QPS case studies.

**[inferred]** Capacity plan for interview design: size **Gateway cells by trust boundary**, size **model RPM** by provider quotas × (interactive QPS + heartbeat/cron baseline + compaction/dreaming), and size **exec** by sandbox pool concurrency — not by a single OpenClaw cloud RPM number.

### Interview talking points

1. OpenClaw = **trusted Gateway + untrusted/movable execution + policy-as-code**, not “another chatbot UI” ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).
2. Persistence of the **local daemon** is what unlocks Heartbeat, multi-channel shared memory, and async jobs ([System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)).
3. Enterprise readiness is **configuration**, not an enterprise edition ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).
4. Correct isolation unit under adversarial users is **Gateway cell**, not `sessionKey` ([Security](https://docs.openclaw.ai/gateway/security)).

---

## Sources

- [1] https://newsletter.systemdesign.one/p/openclaw-architecture — System Design One #151 (Neo Kim, Jun 08, 2026); primary narrative definition (partially paywalled teaser used)
- [2] https://docs.openclaw.ai/ — Official docs home / product definition
- [3] https://github.com/openclaw/openclaw — Official repository / README
- [4] https://docs.openclaw.ai/concepts/architecture — Gateway architecture & WS protocol
- [5] https://docs.openclaw.ai/concepts/agent-loop — Agent run sequence, streams, timeouts
- [6] https://docs.openclaw.ai/concepts/queue — In-process lanes, steer modes, durability limits
- [7] https://docs.openclaw.ai/concepts/memory — Markdown memory, search, dreaming, flush
- [8] https://docs.openclaw.ai/concepts/multi-agent — Isolated agents & binding precedence
- [9] https://docs.openclaw.ai/concepts/model-failover — Auth rotation, fallbacks, idle circuit breaker
- [10] https://docs.openclaw.ai/start/why-openclaw — Trust boundary, standards, enterprise properties
- [11] https://docs.openclaw.ai/gateway/security — Threat model, audit, hardened baseline
- [12] https://docs.openclaw.ai/gateway/security/exposure-runbook — Exposure patterns & incident steps
- [13] https://docs.openclaw.ai/gateway/sandboxing — Sandbox backends & defaults
- [14] https://docs.openclaw.ai/clawhub — ClawHub skills/plugins registry
- [15] https://docs.openclaw.ai/gateway/heartbeat — Heartbeat / automation scheduling
- [16] https://docs.openclaw.ai/start/openclaw — Personal-assistant getting started (ports, heartbeat, allowlists)
