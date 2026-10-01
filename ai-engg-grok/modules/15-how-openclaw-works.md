# Module 15 — How OpenClaw Works

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 15 (self-hosted multi-channel agent Gateway after agent/MCP/memory foundations)  
**Grounded in**: `research/15-how-openclaw-works.md` (16 sources, 2026-09-30)

OpenClaw (OpenClaw Foundation, MIT) is a **self-hosted orchestration layer** between an LLM and real-world tools/channels. The model supplies reasoning; the long-lived **Gateway** supplies always-on hands — messaging adapters, tool execution, Markdown/SQLite memory, Heartbeat/cron, and a Control UI ([docs home](https://docs.openclaw.ai/); [Gateway architecture](https://docs.openclaw.ai/concepts/architecture); [System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)). Default bind: `127.0.0.1:18789`. Runtime: Node 26 recommended (24.16+ / 26.1+). There is **no Foundation paid tier, hosted product, or token** — third-party hosts may wrap it, but the product under study is the local Gateway ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     CONTROL PLANE (Gateway)                                 │
│  Single long-lived daemon · WS/HTTP :18789 · session/routing authority      │
│  Channel ownership · JSON Schema frame validation · device pairing          │
│  Events: agent · chat · presence · health · heartbeat · cron                │
│  Clients: Control UI · CLI · macOS app · automations (one WS each)          │
│  Nodes (role:node): camera.* · screen.record · location.get                 │
│                                                                             │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────┐ │
│  │ WhatsApp   │ │ Telegram   │ │ Slack/     │ │ Signal /   │ │ WebChat / │ │
│  │ (Baileys)  │ │ (grammY)   │ │ Discord    │ │ iMessage   │ │ plugins   │ │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬─────┘ │
└────────┼──────────────┼──────────────┼──────────────┼──────────────┼───────┘
         │              │   normalized ingress (identity · content · meta)
         └──────────────┴──────────────┬──────────────┴──────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     DATA PLANE (agent loop)                                 │
│  RPC agent / agent.wait → {runId, acceptedAt}                               │
│  Assemble prompt (base + skills + SOUL/AGENTS/USER/memory + history)        │
│  ReAct-style: model → tool calls → observe → loop → final reply             │
│  Stream assistant / tool / lifecycle · lane-aware FIFO (session + main)     │
│  Optional vendor harness (Codex / Claude Code / Copilot) still under Gateway│
└───┬─────────────────────────────┬─────────────────────────────┬─────────────┘
    │                             │                             │
    ▼                             ▼                             ▼
┌───────────────────┐   ┌───────────────────────┐   ┌─────────────────────────┐
│   TOOL PROXIES    │   │     PERSISTENCE       │   │      TELEMETRY          │
│  (skills / exec)  │   │  (Markdown / SQLite)  │   │                         │
├───────────────────┤   ├───────────────────────┤   ├─────────────────────────┤
│ Skills (SKILL.md) │   │ openclaw.json config  │   │ OTel + Prometheus       │
│ ClawHub plugins   │   │ MEMORY.md · USER.md   │   │ health / heartbeat      │
│ exec + approvals  │   │ memory/YYYY-MM-DD.md  │   │ metadata audit ledger   │
│ sandbox backends  │   │ sessions + agent DB   │   │ security audit checkIds │
│ MCP client/server │   │ SQLite hybrid index   │   │ corr IDs · runId        │
│ A2A / ACP hooks   │   │ GatewayScheduler jobs │   │ presence / cron events  │
└───────────────────┘   └───────────────────────┘   └─────────────────────────┘
```

**Plane responsibilities**

| Plane | What lives here | Truth source |
| --- | --- | --- |
| **CONTROL PLANE (Gateway)** | One daemon per host/trust domain; WS API; channel sessions; bindings; scheduler | [Gateway architecture](https://docs.openclaw.ai/concepts/architecture) |
| **DATA PLANE (agent loop)** | Authenticate → assemble → model/tool loop → stream → persist/compact | [Agent loop](https://docs.openclaw.ai/concepts/agent-loop) |
| **PERSISTENCE (Markdown/SQLite)** | Config, workspace memory files, per-agent sessions DB, hybrid search index, durable cron deadlines | [Memory](https://docs.openclaw.ai/concepts/memory); [Multi-agent](https://docs.openclaw.ai/concepts/multi-agent) |
| **TOOL PROXIES (skills/exec)** | Skills, plugins, exec/sandbox, MCP, optional nodes | [Skills](https://docs.openclaw.ai/tools/skills); [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing) |
| **TELEMETRY** | OTel/Prometheus, health/heartbeat/cron events, metadata-only audit ledger, `openclaw security audit` | [Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [Security](https://docs.openclaw.ai/gateway/security) |

### End-to-end request-flow narrative

1. **Channel message** — WhatsApp/Telegram/Slack/… adapter normalizes ingress (identity key, content, metadata, attachments). DM policy (`pairing` default / allowlist / open / disabled) and mention-gating apply before the Gateway accepts work ([Channels](https://docs.openclaw.ai/channels); [Telegram](https://docs.openclaw.ai/channels/telegram)).
2. **Gateway (control plane)** — Deterministic **bindings** pick an agent (peer → parentPeer → guild/roles → … → default). Side-effecting methods (`send`, `agent`) require **idempotency keys**; short-lived dedupe cache drops duplicates. `agent` returns `{ runId, acceptedAt }` immediately ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture); [Multi-agent](https://docs.openclaw.ai/concepts/multi-agent)).
3. **Lane admission (data plane back-pressure)** — Inbound auto-replies enter an in-process lane-aware FIFO: per-session one run at a time; global `main` capped by `maxConcurrent` (default `min(16, max(8, CPU parallelism))`); cron/heartbeat use `cron` / `cron-nested` so they do not starve replies ([Queue](https://docs.openclaw.ai/concepts/queue)).
4. **Agent loop** — Assemble system prompt (base + skills + bootstrap files + memory + history) → call model → on tool calls, execute via **tool proxies** → feed observations → loop until final reply. Stream `assistant` / `tool` / `lifecycle` to Control UI and channel adapters ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).
5. **Tools → effects** — Skills/plugins/exec/MCP/nodes run under tool allow/deny, optional exec approvals, optional sandbox. Results return to the loop; channel adapter delivers the **reply** to the originating surface ([Sandboxing](https://docs.openclaw.ai/gateway/sandboxing); [Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).
6. **Persist & telemetry** — Transcript commits under writer-claim fencing; memory flush before compaction; OTel/Prometheus + metadata audit ledger record lifecycle without copying raw prompts by default ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop); [Memory](https://docs.openclaw.ai/concepts/memory)).

**Invariant**: exactly one Gateway per host owns a single Baileys (WhatsApp) session ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)).

---

## Part 2 — Core Mechanics & Algorithms

### Wire protocol (control-plane contract)

Transport: WebSocket, JSON text frames ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)).

1. First frame **must** be `connect`; non-JSON / non-connect → hard close.
2. After handshake: `{type:"req", id, method, params}` → `{type:"res", id, ok, payload|error}`; push `{type:"event", event, payload, seq?, stateVersion?}`.
3. Events are **not replayed**; clients refresh on sequence gaps.
4. Remote access preferred via Tailscale/VPN or SSH tunnel to loopback (`ssh -N -L 18789:127.0.0.1:18789 …`).

### Agent-loop state machine

```
                    ┌─────────────┐
                    │  accepted   │  {runId, acceptedAt}
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │  assemble   │  skills + bootstrap + memory + history
                    └──────┬──────┘
                           ▼
              ┌────────────┴────────────┐
              ▼                         │
       ┌─────────────┐                  │
       │ model_turn  │◄─────────────────┤
       └──────┬──────┘                  │
              │                         │
       ┌──────▼──────┐     tool calls   │
       │  tool_exec  │──────────────────┘
       └──────┬──────┘
              │ final (no tools)
              ▼
       ┌─────────────┐     context pressure
       │  persist    │──────────────────► memory_flush → compact
       └──────┬──────┘
              ▼
       ┌─────────────┐
       │   reply     │  channel + Control UI streams
       └─────────────┘
```

Complexity (interview framing): with \(N\) tool rounds and context size \(C_i\) at turn \(i\), model work is \(\Theta(\sum_i C_i)\) tokens — OpenClaw does not change that asymptotics; it owns channels, sessions, policy, and disk truth (“the model only remembers what gets saved to disk”) ([Memory](https://docs.openclaw.ai/concepts/memory); [Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).

### Multi-agent binding precedence

One Gateway hosts many isolated agents (`~/.openclaw/agents/<id>/…` workspaces, auth profiles, session stores). Inbound routing is deterministic **most-specific wins**: peer → parentPeer → guild/roles → guild → team → accountId → channel → default agent ([Multi-agent](https://docs.openclaw.ai/concepts/multi-agent)).

### Queue modes & lanes

| Mode / lane | Behavior |
| --- | --- |
| `steer` (default) | Batch/steer follow-ups into active work |
| `followup` / `collect` / `interrupt` | Alternate coalescing / abort semantics |
| Session lane | One agent run at a time per session |
| `main` | Global concurrency `maxConcurrent` |
| `cron` / `cron-nested` | Background / heartbeat admission (nested bound so inbound is not starved) |
| Defaults | Queue `cap: 20`, `drop: "summarize"`; debounce **500 ms**; notice log if wait ≳ **2 s** |

([Queue](https://docs.openclaw.ai/concepts/queue))

### Model failover stages

Bounded same-model recovery (rate limits) → **auth-profile rotation** within provider → **model fallback** chain (`agents.defaults.model.primary` / `.fallbacks`). Fallback is **turn-local** (does not rewrite the session’s selected model). Explicit user session model selections are **strict** unless fallbacks configured. Terminal stops: `agent_run_terminal_timeout`, **`idle_timeout_circuit_breaker`** (cost-runaway / idle) halt the chain ([Model failover](https://docs.openclaw.ai/concepts/model-failover)).

### Compaction invariants

Auto-compaction summarizes when context limits hit (`keepRecentTokens` example **50000**, `recentTurnsPreserve: 3`). Before compaction, a silent **memory flush** writes durable facts to Markdown (default on). Soft flush ~**6000** tokens; `forceFlushTranscriptBytes: "2mb"` ([Memory](https://docs.openclaw.ai/concepts/memory); [Heartbeat/compaction config](https://docs.openclaw.ai/gateway/config-agents/heartbeat-compaction-and-streaming)).

### Proactive scheduling

- **Cron**: durable one-shot/recurring jobs to channels or webhooks ([Automation](https://docs.openclaw.ai/automation/tasks)).
- **Heartbeat**: ambient monitor; default **every 30 minutes** (API-key) or **1h** (OAuth); `heartbeat.every: "0m"` disables. Scheduled heartbeats need cron enabled; busy deferral waits for main/automation/session work ([Heartbeat](https://docs.openclaw.ai/gateway/heartbeat)).
- `GatewayScheduler` reconstructs durable deadlines on startup ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)).

---

## Part 3 — Token Economics & NFR Analysis

> ⚠️ **Gap**: OpenClaw does **not** publish vendor p50/p95/p99 end-to-end latency SLAs, RPM/TPM product limits, or a Foundation `$ per 1k executions` meter. The Gateway itself is **not metered**. Token spend is almost entirely the **model provider’s** pricing (+ optional embedding providers for memory search). Documented numbers below are timeouts, concurrency caps, and compaction knobs — not production latency benchmarks ([research §2](../research/15-how-openclaw-works.md)).

### Cost formulas — $ per 1k runs

**Assumptions (labeled; substitute your contract rates)**

| Symbol | Assumed value | Role |
| --- | --- | --- |
| \(P_{\text{in}}\) | **$3.00 / 1M** input | Frontier list-class placeholder — not an OpenClaw or vendor SLA |
| \(P_{\text{out}}\) | **$15.00 / 1M** output | Same |
| \(P_{\text{in}}^{\text{cheap}}\) | **$0.40 / 1M** | Compaction / memory-flush override example (local/cheap tier) |
| Interactive trajectory | \(N=6\) model turns; avg **4,000** input + **200** output tokens/turn | Bootstrap (skills + MEMORY + history) heavier than bare chat |
| Heartbeat | 1 ambient turn / **30 min** = **48**/day when enabled | [Heartbeat](https://docs.openclaw.ai/gateway/heartbeat) |
| OpenClaw Gateway fee | **$0** | Foundation product is self-hosted, not usage-billed ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)) |

#### A. Interactive ReAct-style run (no cache)

\[
c_{\text{turn}} = \frac{4000}{10^6}P_{\text{in}} + \frac{200}{10^6}P_{\text{out}}
= 0.012 + 0.003 = \$0.015
\]

\[
\text{Cost}_{1\text{run}} = N\cdot c_{\text{turn}} = 6\times 0.015 = \$0.090
\qquad
\text{Cost}_{1\text{k runs}} = \$90
\]

#### B. Prompt-cache hint impact (`cacheRetention: "long"`)

Config examples can set provider `cacheRetention: "long"` — a **provider-side** prompt-cache hint, not an OpenClaw semantic cache with published hit rates ([Config agents](https://docs.openclaw.ai/gateway/config-agents)). **[inferred]** If a stable skills+tools prefix \(S=3{,}000\) tokens is written once and read on 5 subsequent turns at **0.1×** input (illustrative GPT-class cache read; verify against your provider):

\[
\text{Prefix}_{6} = P_{\text{in}}\cdot\frac{S}{10^6}\bigl(1 + 5\cdot 0.1\bigr)
= 3\cdot\frac{3000}{10^6}\cdot 1.5 = \$0.0135
\]

vs 6× full uncached prefix \(= 3\cdot 6\cdot 3000/10^6 = \$0.054\). Net **[inferred]** interactive 1k-run bill can drop tens of dollars when the bootstrap prefix is stable — still **provider** economics, not OpenClaw metering.

#### C. Compaction / memory-flush on cheap model

Compaction may override to `compaction.model` / `memoryFlush.model` (e.g. local `ollama/qwen3:8b`) ([Heartbeat/compaction config](https://docs.openclaw.ai/gateway/config-agents/heartbeat-compaction-and-streaming)). **[inferred]** One flush+compact pass at **8k in / 1k out** on cheap tier:

\[
c_{\text{compact}} \approx \frac{8000}{10^6}(0.40) + \frac{1000}{10^6}(0) \approx \$0.0032
\]

(local out ≈ $0; cloud cheap-out substitute your rate). Negligible vs interactive $0.09/run; **does** prevent full-history reloads that would otherwise multiply Part A.

#### D. Heartbeat baseline (always-on tax)

**[inferred]** 48 heartbeat turns/day × $0.015 ≈ **$0.72/day** ≈ **$22/month** per agent with default 30m cadence and Part A turn size — even with **zero** user messages. Disable (`every: "0m"`) until trusted ([Getting started](https://docs.openclaw.ai/start/openclaw)).

### Latency SLA targets

> ⚠️ **Gap**: No vendor-published p50/p95/p99 for OpenClaw end-to-end channel→reply. Use documented knobs + the **[inferred]** budget below for interview sizing only.

**Documented timeouts (not percentiles)**

| Knob | Default | Semantics |
| --- | --- | --- |
| `agent.wait` | **30 s** | Client wait only; does **not** cancel the run |
| `agents.defaults.timeoutSeconds` | **172800 s (48 h)** | Whole-run elapsed; `0` = unlimited (provider idle watchdogs still apply) |
| Model idle timeout | Cloud **120 s**; self-hosted **300 s** | Abort if no response chunks |
| Cron cloud stream stall | Cap **60 s** (with explicit cron timeout) | Allows model fallback before outer cron deadline |
| Queue debounce | **500 ms** | Steer/followup/collect batching |
| Sub-agent announce | **120000 ms** default | Nested announce wait |

([Agent loop](https://docs.openclaw.ai/concepts/agent-loop); [Queue](https://docs.openclaw.ai/concepts/queue))

#### [inferred] Latency budget (ms) with arithmetic

| Component | Symbol | p50 | p95 | p99 | Notes (assumption, not a citation) |
| --- | --- | --- | --- | --- | --- |
| Channel → Gateway normalize + bind | \(T_{\text{ingress}}\) | **40** | **120** | **400** | Adapter + policy |
| Lane queue wait | \(T_{\text{q}}\) | **50** | **2{,}000** | **15{,}000** | Notice log ~2 s; cap/drop under load |
| Prompt assemble + disk read | \(T_{\text{asm}}\) | **30** | **150** | **500** | Markdown + session DB |
| Model TTFT | \(T_{\text{TTFT}}\) | **400** | **1{,}200** | **3{,}000** | Provider path |
| Decode (200 out @ 40/25 tok/s) | \(T_{\text{out}}\) | **5{,}000** | **5{,}000** | **8{,}000** | p99 slower decode |
| Tool / exec / MCP RTT | \(T_{\text{tool}}\) | **200** | **1{,}500** | **8{,}000** | Host exec or sandbox |
| Persist + reply fan-out | \(T_{\text{persist}}\) | **40** | **200** | **800** | Writer fence + channel send |
| Tool rounds | \(N\) | 6 | 6 | 6 | Same \(N\) as cost model |

**First-token to user (after dequeue), single model turn [inferred]**:

\[
L_{\text{first}} = T_{\text{asm}} + T_{\text{TTFT}}
\]

| Tier | Arithmetic | Target |
| --- | --- | --- |
| **p50** | \(30 + 400\) | **430 ms** |
| **p95** | \(150 + 1{,}200\) | **1{,}350 ms** |
| **p99** | \(500 + 3{,}000\) | **3{,}500 ms** |

**Full \(N\)-step channel→reply [inferred]** (queue once + \(N\) model/tool cycles):

\[
L_{\text{e2e}} = T_{\text{ingress}} + T_{\text{q}} + N\cdot(T_{\text{asm}} + T_{\text{TTFT}} + T_{\text{out}} + T_{\text{tool}}) + T_{\text{persist}}
\]

| Tier | Arithmetic | Target |
| --- | --- | --- |
| **p50** | \(40+50+6\cdot(30+400+5000+200)+40\) | **≈ 33.9 s** |
| **p95** | \(120+2000+6\cdot(150+1200+5000+1500)+200\) | **≈ 49.2 s** |
| **p99** | \(400+15000+6\cdot(500+3000+8000+8000)+800\) | **≈ 132.7 s** |

**Mitigations by tier**

| Tier | Mitigations |
| --- | --- |
| **p50** | Stream tokens after TTFT; 500 ms debounce to coalesce bursts; keep bootstrap files lean (`/context list`, `openclaw doctor`) |
| **p95** | Raise `maxConcurrent` only with CPU headroom; isolate cron on `cron-nested`; `steer`/`collect` to avoid stacked full runs; provider prompt cache on stable skills prefix |
| **p99** | Model fallback + auth rotation; idle/cost circuit breaker; `/stop` / `chat.abort`; break long scrapes into batched tool calls; acknowledge async then message when done ([Queue](https://docs.openclaw.ai/concepts/queue); [Model failover](https://docs.openclaw.ai/concepts/model-failover); [System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture)) |

### Throughput & back-pressure (lane queues)

- Inbound auto-replies: **in-process lane-aware FIFO** ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Per-session: **one agent run at a time**.
- Global `main`: `agents.defaults.maxConcurrent` default **`min(16, max(8, available CPU parallelism))`** ([Config agents](https://docs.openclaw.ai/gateway/config-agents)).
- Sub-agents: default **8** concurrent per spawning session; Swarm collector default **32** (`tools.swarm.maxConcurrent`).
- Queue `cap: 20`, `drop: "summarize"` under overflow.
- Background maintenance (Skill Workshop + dreaming/plugin completions): shared budget **3**.
- Heartbeat uses bounded `cron-nested` admission so ambient work does not starve inbound replies.

**[inferred] Capacity planning**: size **Gateway cells by trust boundary**, size **model RPM** by provider quotas × (interactive QPS + heartbeat/cron + compaction), size **exec** by sandbox pool concurrency — not by a single OpenClaw cloud RPM number. Do not invent unpublished tokens/sec or max end-users/Gateway.

### Non-functional requirements & trade-offs

| NFR | OpenClaw posture | Explicit trade-off |
| --- | --- | --- |
| **Availability** | Single Gateway process per host is session authority; supervise with launchd/systemd (`Restart=always`, `RestartSec=5`, `TimeoutStopSec=30`) ([Gateway](https://docs.openclaw.ai/gateway)) | Self-host isolation and one Baileys owner **vs** ops burden of process supervision, upgrades, and `openclaw doctor` migrations — not multi-region active-active |
| **RPO (Markdown memory)** | Durable files + session DB; “model only remembers what gets saved to disk” ([Memory](https://docs.openclaw.ai/concepts/memory)) | **[inferred] RPO ≈ last successful flush/commit** on disk/backup cadence. In-memory queue is **not** replayed after Gateway stop — abandoned queued input needs resend ([Queue](https://docs.openclaw.ai/concepts/queue)) |
| **RTO** | Restart reconstructs scheduler deadlines; sessions reload from disk | **[inferred] RTO ≈ process restart + channel reconnect** (seconds–minutes under systemd), not a published multi-AZ RTO |
| **Compliance** | Operator-owned: SecretRefs, OTel→SIEM, `security audit`, `memory forget` / provenance | No first-party SOC2/HIPAA product package or built-in PII NER documented as a Foundation feature ([research §4](../research/15-how-openclaw-works.md)) |
| **Security defaults** | Sandboxing and exec approvals **off by default**; trusted single-operator posture ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)) | Fast personal setup **vs** high blast radius if channels open with host exec — enterprise cells must turn sandbox/`exec.security` on deliberately |

---

## Part 4 — Distributed Resilience & Security

### Local durability (not Temporal/Kafka by default)

OpenClaw’s default topology is **one Gateway process per host/trust domain**, not Kafka/Temporal multi-region orchestration. Cloud workers/nodes can move *execution*; the Gateway remains session authority ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).

| Mechanism | Behavior |
| --- | --- |
| **Writer-claim fencing** | Before streaming, run records durable `activeWriterRunId`; transcript commits verify `expectedWriterRunId` so a superseded run cannot commit stale data. Compaction/truncation use the same in-transaction fence ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)) |
| **State-dir lock** | Gateway / `openclaw agent --local` lock prevents two processes owning the same state directory; SQLite writer queue orders per-agent mutations ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)) |
| **Memory flush before compaction** | Silent flush turn writes durable facts to Markdown before history summarize ([Memory](https://docs.openclaw.ai/concepts/memory)) |
| **Ingress ack** | Ordinary Control UI/TUI/CLI/RPC input stored in per-agent DB before acknowledgment ([Queue](https://docs.openclaw.ai/concepts/queue)) |
| **Queue limit** | In-memory queue **not** replayed after stop; channel messages retained by durable ingress remain retryable until agent-turn adoption; adopted messages keep duplicate suppression ([Queue](https://docs.openclaw.ai/concepts/queue)) |

> ⚠️ **Gap**: No published Temporal/Kafka reference architecture, distributed lock service beyond the local state-dir lock, or multi-region RPO/RTO numbers.

### Failure taxonomy

| Class | Examples | Handling |
| --- | --- | --- |
| **Transient** | Provider rate limits, short network blips, retryable chat errors | Bounded same-model retry; **15 s** grace for fallback/restart of same run ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop); [Model failover](https://docs.openclaw.ai/concepts/model-failover)) |
| **Degraded / stall** | `session.long_running` / `stalled` / `stuck` | Heartbeat-tick abort/recovery; idle timeout watchdogs (120 s cloud / 300 s self-hosted) ([Queue](https://docs.openclaw.ai/concepts/queue); [Agent loop](https://docs.openclaw.ai/concepts/agent-loop)) |
| **Permanent / terminal** | Auth hard-fail after rotation, schema-invalid tools, owner-denied tools | Stop chain; structured per-attempt details; do not infinite-fallback |
| **Poison / runaway** | Cost-runaway idle, whole-run timeout, open DMs + exec | `idle_timeout_circuit_breaker`; `timeoutSeconds`; `/stop`; sandbox + pairing ([Model failover](https://docs.openclaw.ai/concepts/model-failover); [Security](https://docs.openclaw.ai/gateway/security)) |
| **State partial write** | Crash mid-stream | Writer fence drops superseded commits; collect-mode coalesces in one transaction ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop); [Queue](https://docs.openclaw.ai/concepts/queue)) |

### IDEMPOTENCY (channel / RPC message ids)

- Side-effecting Gateway methods (`send`, `agent`) require **idempotency keys**; server keeps a short-lived dedupe cache ([Gateway architecture](https://docs.openclaw.ai/concepts/architecture)).
- After adoption, channel message duplicate suppression prevents double agent-turns for the same ingress id ([Queue](https://docs.openclaw.ai/concepts/queue)).
- Interview invariant: **at-most-once agent adoption per message id** inside the dedupe/adoption window; clients must resend only work that never left the non-durable in-memory queue after a crash.

### Circuit breaker & fallback model chain

Map OpenClaw’s idle/cost breaker + failover onto the classic states:

```
     success                    recovery timer elapsed
  ┌──────────┐  failures≥N   ┌──────┐  probe          ┌───────────┐
  │ CLOSED   │──────────────►│ OPEN │───────────────►│ HALF-OPEN │
  └──────────┘               └──────┘                 └─────┬─────┘
       ▲                         ▲                          │
       │                         │         probe fail       │
       └────── probe success ────┴──────────────────────────┘
```

| State | OpenClaw mapping |
| --- | --- |
| **CLOSED** | Primary model + auth profile serving turns |
| **OPEN** | After repeated rate-limit/idle failures or `idle_timeout_circuit_breaker` — stop burning the same path; cooldown |
| **HALF-OPEN** | Bounded retry / next auth profile / next fallback model as a probe turn |
| **Fallback chain** | same-model recovery → auth-profile rotation → `model.fallbacks` (turn-local) ([Model failover](https://docs.openclaw.ai/concepts/model-failover)) |

### Enterprise security

**Single trust boundary.** OpenClaw is a personal-assistant / **single trust boundary per Gateway**. It is **not** a hostile multi-tenant boundary for adversarial users sharing one agent. Fleet multi-tenancy is still described as experimental ([Security](https://docs.openclaw.ai/gateway/security); [Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).

**One Gateway cell per tenant.** Multi-tenant hosting guidance: isolate with **one Gateway cell per tenant** (separate OS user/host preferred) ([Security](https://docs.openclaw.ai/gateway/security)).

**Exec approvals.** Three layers: (1) tool allow/deny (`tools.profile`, deny `group:runtime` / `group:fs`); (2) exec security `deny` | allowlist/ask | `full`; (3) sandbox backends (Docker/Podman/SSH/OpenShell/Crabbox) — **off by default**. `tools.elevated` is an explicit host escape hatch — keep `allowFrom` tight ([Sandboxing](https://docs.openclaw.ai/gateway/sandboxing); [Security](https://docs.openclaw.ai/gateway/security)).

**Tool RBAC.** `tools.toolsBySender` / owner tools reduce **direct** capability per requester; non-owners cannot use `cron` or `gateway` tools. These do **not** sanitize quoted history, forwards, or tool results in the prompt — not hostile multi-user isolation ([Security](https://docs.openclaw.ai/gateway/security)).

**Zero-Trust when skills call MCP.** OpenClaw is MCP client (Streamable HTTP, SSE, stdio + OAuth) and MCP server ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)). Treat MCP servers like high-privilege plugins: Gateway WS auth + channel pairing as outer gates; inherit tool policy/sandbox; prefer least-privilege allowlists; pin versions; do not equate ClawHub install with completed scans (pending/stale scans can still install with warning) ([ClawHub](https://docs.openclaw.ai/clawhub); [Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)).

**PII in Markdown memory (detect → redact → audit).** No first-party PII NER product pipeline is documented. Operator pattern:

1. **Detect** — scan `MEMORY.md` / daily memory / session exports for regulated patterns before sync/backup.  
2. **Redact** — rewrite or `openclaw memory forget` / provenance rules; avoid pasting secrets into bootstrap files.  
3. **Audit** — OTel/SIEM + `openclaw security audit` / secrets audit in CI; metadata-only agent ledger does not copy raw prompts by default ([Memory](https://docs.openclaw.ai/concepts/memory); [Security](https://docs.openclaw.ai/gateway/security); [Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).

**Immutable session logs.** Prefer append-only session transcripts + metadata audit ledger + external SIEM/WORM export of OTel spans for chain-of-custody; Gateway crash must not rewrite adopted transcript rows protected by writer-claim fencing ([Agent loop](https://docs.openclaw.ai/concepts/agent-loop)).

Hardened baseline (docs): loopback + token auth, `dmScope: per-channel-peer`, messaging tool profile, `exec.security: "deny"`, `elevated.enabled: false`, pairing DMs; prefer Tailscale Serve over Funnel/public bind ([Security](https://docs.openclaw.ai/gateway/security); [Exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook)).

---

## Part 5 — Production Enterprise Code

Runnable, self-contained Python: tiny **Gateway lane queue** + one **agent step**, with **message-id idempotency**, retries + full jitter, circuit breaker (**closed → open → half-open**), **model fallback** chain, and structured logs with **correlation IDs**. Deterministic fake models — no API keys, no TODOs.

```python
#!/usr/bin/env python3
"""OpenClaw-shaped gateway lane queue + resilient agent step (no API keys)."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
            "message_id": getattr(record, "message_id", None),
            "run_id": getattr(record, "run_id", None),
            "lane": getattr(record, "lane", None),
            "attempt": getattr(record, "attempt", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "model": getattr(record, "model", None),
        }
        return json.dumps(payload, sort_keys=True)


def build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


LOG = build_logger("openclaw.gateway")


# ---------------------------------------------------------------------------
# Circuit breaker: closed → open → half-open
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 2
    recovery_timeout_ticks: int = 2
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at_tick: int | None = None
    clock_tick: int = 0

    def tick(self) -> None:
        self.clock_tick += 1

    def allow(self) -> bool:
        if self.state is BreakerState.OPEN:
            assert self.opened_at_tick is not None
            if self.clock_tick - self.opened_at_tick >= self.recovery_timeout_ticks:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED
        self.opened_at_tick = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.state is BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at_tick = self.clock_tick


# ---------------------------------------------------------------------------
# Retries: exponential backoff + full jitter (deterministic RNG)
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_ms: int = 10
    max_delay_ms: int = 80
    rng: random.Random = field(default_factory=lambda: random.Random(0))

    def delay_ms(self, attempt: int) -> int:
        ceiling = min(self.max_delay_ms, self.base_delay_ms * (2**attempt))
        return self.rng.randint(0, ceiling)


# ---------------------------------------------------------------------------
# Models + fallback chain
# ---------------------------------------------------------------------------

class ModelError(Exception):
    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass
class FakeModel:
    name: str
    fail_times: int = 0
    _calls: int = 0

    def complete(self, prompt: str) -> str:
        self._calls += 1
        if self._calls <= self.fail_times:
            raise ModelError(f"{self.name} transient", transient=True)
        return f"{self.name}:ok:{prompt}"


@dataclass
class ModelRouter:
    chain: list[FakeModel]
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        for m in self.chain:
            self.breakers.setdefault(m.name, CircuitBreaker())

    def complete(self, prompt: str, *, correlation_id: str, run_id: str) -> str:
        errors: list[str] = []
        for model in self.chain:
            br = self.breakers[model.name]
            br.tick()
            for attempt in range(self.retry.max_attempts):
                if not br.allow():
                    LOG.info(
                        "breaker_open_skip",
                        extra={
                            "correlation_id": correlation_id,
                            "run_id": run_id,
                            "model": model.name,
                            "breaker_state": br.state.value,
                        },
                    )
                    errors.append(f"{model.name}:breaker_{br.state.value}")
                    break
                try:
                    text = model.complete(prompt)
                    br.record_success()
                    LOG.info(
                        "model_ok",
                        extra={
                            "correlation_id": correlation_id,
                            "run_id": run_id,
                            "model": model.name,
                            "attempt": attempt,
                            "breaker_state": br.state.value,
                        },
                    )
                    return text
                except ModelError as exc:
                    br.record_failure()
                    delay = self.retry.delay_ms(attempt)
                    LOG.info(
                        "model_fail",
                        extra={
                            "correlation_id": correlation_id,
                            "run_id": run_id,
                            "model": model.name,
                            "attempt": attempt,
                            "breaker_state": br.state.value,
                        },
                    )
                    errors.append(f"{model.name}:a{attempt}:{exc}:sleep{delay}ms")
                    if not exc.transient or br.state is BreakerState.OPEN:
                        break
            # exhausted retries on this model → next fallback
        raise ModelError("fallback_exhausted:" + "|".join(errors), transient=False)


# ---------------------------------------------------------------------------
# Lane-aware FIFO + message-id idempotency
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    session_id: str
    text: str
    lane: str = "main"


@dataclass
class LaneQueue:
    """In-process lane FIFO with per-session single-flight + idempotency."""

    max_concurrent_main: int = 2
    pending: list[InboundMessage] = field(default_factory=list)
    inflight_sessions: set[str] = field(default_factory=set)
    inflight_main: int = 0
    seen_message_ids: set[str] = field(default_factory=set)
    results: dict[str, str] = field(default_factory=dict)

    def enqueue(self, msg: InboundMessage) -> str:
        if msg.message_id in self.seen_message_ids:
            LOG.info(
                "idempotent_hit",
                extra={
                    "correlation_id": msg.message_id,
                    "message_id": msg.message_id,
                    "lane": msg.lane,
                },
            )
            return self.results.get(msg.message_id, "duplicate_in_flight")
        self.seen_message_ids.add(msg.message_id)
        self.pending.append(msg)
        return "accepted"

    def _admit(self, msg: InboundMessage) -> bool:
        if msg.session_id in self.inflight_sessions:
            return False
        if msg.lane == "main" and self.inflight_main >= self.max_concurrent_main:
            return False
        return True

    def drain(self, step: Callable[[InboundMessage], str]) -> list[tuple[str, str]]:
        completed: list[tuple[str, str]] = []
        progress = True
        while progress:
            progress = False
            for i, msg in enumerate(list(self.pending)):
                if not self._admit(msg):
                    continue
                self.pending.pop(i)
                self.inflight_sessions.add(msg.session_id)
                if msg.lane == "main":
                    self.inflight_main += 1
                run_id = f"run-{msg.message_id}"
                LOG.info(
                    "agent_start",
                    extra={
                        "correlation_id": msg.message_id,
                        "message_id": msg.message_id,
                        "run_id": run_id,
                        "lane": msg.lane,
                    },
                )
                try:
                    reply = step(msg)
                finally:
                    self.inflight_sessions.discard(msg.session_id)
                    if msg.lane == "main":
                        self.inflight_main -= 1
                self.results[msg.message_id] = reply
                completed.append((msg.message_id, reply))
                progress = True
                break
        return completed


# ---------------------------------------------------------------------------
# Agent step (one tool-less turn for clarity)
# ---------------------------------------------------------------------------

def make_agent_step(router: ModelRouter) -> Callable[[InboundMessage], str]:
    def step(msg: InboundMessage) -> str:
        run_id = f"run-{msg.message_id}"
        return router.complete(
            msg.text,
            correlation_id=msg.message_id,
            run_id=run_id,
        )

    return step


def demo() -> None:
    # Primary fails more times than retry budget → fallback chain wins.
    primary = FakeModel("sonnet-primary", fail_times=5)
    fallback = FakeModel("gpt-fallback", fail_times=0)
    router = ModelRouter(
        chain=[primary, fallback],
        retry=RetryPolicy(max_attempts=2, rng=random.Random(42)),
    )
    queue = LaneQueue(max_concurrent_main=1)
    step = make_agent_step(router)

    m1 = InboundMessage("msg-1", "sess-a", "ping")
    assert queue.enqueue(m1) == "accepted"
    assert queue.enqueue(m1) == "duplicate_in_flight"  # idempotency before completion

    done = queue.drain(step)
    assert done == [("msg-1", "gpt-fallback:ok:ping")], done
    assert queue.enqueue(m1) == "gpt-fallback:ok:ping"  # idempotent replay of result

    # Back-pressure: second session waits while first holds the single main slot
    queue2 = LaneQueue(max_concurrent_main=1)
    blocker = InboundMessage("msg-block", "sess-b", "hold")
    waiter = InboundMessage("msg-wait", "sess-c", "later")
    queue2.enqueue(blocker)
    queue2.enqueue(waiter)
    queue2.inflight_sessions.add("sess-b")
    queue2.inflight_main = 1
    assert queue2.drain(step) == []  # cannot admit waiter
    queue2.inflight_sessions.clear()
    queue2.inflight_main = 0
    queue2.pending = [m for m in queue2.pending if m.message_id != "msg-block"]
    done2 = queue2.drain(step)
    assert done2[0][0] == "msg-wait"

    # failure_threshold=2 → OPEN; recovery ticks → HALF_OPEN; fallback still serves
    primary2 = FakeModel("sonnet-primary", fail_times=100)
    fallback2 = FakeModel("gpt-fallback", fail_times=0)
    router2 = ModelRouter(
        chain=[primary2, fallback2],
        retry=RetryPolicy(max_attempts=2, rng=random.Random(7)),
    )
    out = router2.complete("x", correlation_id="c1", run_id="r1")
    assert out.startswith("gpt-fallback:"), out
    br = router2.breakers["sonnet-primary"]
    assert br.state is BreakerState.OPEN
    br.tick()
    br.tick()
    assert br.allow()  # half-open probe window
    assert br.state is BreakerState.HALF_OPEN

    print(json.dumps({"ok": True, "reply": done[0][1], "breaker": br.state.value}, sort_keys=True))


if __name__ == "__main__":
    demo()
```

Copy the block to a `.py` file and run with `python3`. Expected final stdout: `{"breaker": "half_open", "ok": true, "reply": "gpt-fallback:ok:ping"}`.

---

## Part 6 — Architectural System Design Scenarios

### Scenario A — Always-on personal multi-channel assistant on a home VPS

**Problem statement**  
A founder wants one OpenClaw Gateway on a small VPS: WhatsApp + Telegram, Heartbeat every 30 minutes, Markdown memory, ClawHub skills for calendar/email, Control UI over Tailscale. Constraints: Foundation Gateway is free/self-hosted; model spend must stay near Part 3 interactive economics (~**$90 / 1k** tool-heavy turns under labeled rates, plus heartbeat baseline); sandbox/exec were left at defaults during setup; crash must not double-send on channel retries; no published OpenClaw p99 — design to documented idle/run timeouts and lane back-pressure ([Getting started](https://docs.openclaw.ai/start/openclaw); [Queue](https://docs.openclaw.ai/concepts/queue); [Heartbeat](https://docs.openclaw.ai/gateway/heartbeat)).

**Proposed architecture**

```
┌──────────────┐  WA/TG msg   ┌─────────────────────────┐  agent RPC   ┌──────────────┐
│ Channel      │─────────────►│ CONTROL PLANE           │─────────────►│ DATA PLANE   │
│ adapters     │  normalize   │ Gateway :18789          │  lane FIFO   │ agent loop   │
└──────┬───────┘              │ pairing · bindings      │              │ N tool turns │
       │                      │ idempotency cache       │              └──────┬───────┘
       │                      └───────────┬─────────────┘                     │
       │                                  │                                   ▼
       │                      ┌───────────▼─────────────┐              ┌──────────────┐
       │                      │ PERSISTENCE             │◄─────────────│ TOOL PROXIES │
       │                      │ MEMORY.md · session DB  │  flush/fence │ skills/exec  │
       │                      │ SQLite index · cron     │              │ (ask/deny)   │
       │                      └───────────┬─────────────┘              └──────────────┘
       ▼                                  ▼
┌──────────────┐              ┌─────────────────────────┐
│ Reply to     │◄─────────────│ TELEMETRY               │
│ channel      │   streams    │ OTel · health · audit   │
└──────────────┘              │ Tailscale Serve UI/WS   │
                              └─────────────────────────┘
```

Technology: `openclaw gateway install` (systemd), loopback + Tailscale Serve, Heartbeat + cron lanes, writer-claim fencing, `exec.security` at least `ask`/`deny` before enabling host skills, optional Docker sandbox when skills grow ([Gateway](https://docs.openclaw.ai/gateway); [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing); [Exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook)).

**Trade-off matrix**

| Dimension | Alt 1: Browser ChatGPT/Claude tab | Alt 2: OpenClaw default (host exec, sandbox off) | Alt 3: OpenClaw + sandbox + deny/ask exec (recommended) |
| --- | --- | --- | --- |
| **Cost** | Provider $ only; no heartbeat tax | Provider $ + always-on host + Heartbeat baseline ([Part 3](#part-3--token-economics--nfr-analysis)) | Same model $ + small container overhead |
| **Latency** | Interactive chat only | Tool-loop + queue wait; best UX for async “message when done” | Higher tool RTT through sandbox |
| **Ops** | None | Low (single process) but you own upgrades/backups | Medium (images, allowlists, audit cadence) |
| **Security** | Vendor SaaS boundary; no local shell | High blast radius if DMs open + exec ([Security](https://docs.openclaw.ai/gateway/security)) | Stronger execution boundary; still one trust domain |
| **Scalability** | N/A (no local tools) | Single host / trust domain | Still one Gateway cell; scale by skills policy not shared multi-tenant process |

**Decision rationale**  
Recommend **Alt 3** for any VPS that is reachable from messaging networks: defaults favor a trusted laptop, not an exposed always-on host ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw)). Keep Heartbeat off until allowlists/pairing are proven. Prefer Alt 1 only when local tools/memory are unnecessary. Reject long-term Alt 2 on a public-ish channel surface.

---

### Scenario B — Multi-tenant internal “team assistant” platform

**Problem statement**  
An enterprise wants Slack/Teams assistants for many departments (HR, IT, Sales), each with different MCP servers and exec needs. Adversarial prompt injection from shared channels is in-scope. Constraints: OpenClaw documents **one Gateway cell per tenant**, not hostile users on one agent; fleet multi-tenancy still experimental; sandbox off by default; compliance is operator-owned (no Foundation SOC2 package); must export auditability to SIEM; model failover required for provider outages ([Security](https://docs.openclaw.ai/gateway/security); [Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [Model failover](https://docs.openclaw.ai/concepts/model-failover)).

**Proposed architecture**

```
                    ┌────────────────────────────────────────┐
                    │         Tenant catalog / IdP           │
                    └─────────────────┬──────────────────────┘
                                      │ provision cell
          ┌───────────────────────────┼───────────────────────────┐
          ▼                           ▼                           ▼
┌───────────────────┐     ┌───────────────────┐     ┌───────────────────┐
│ Gateway cell A    │     │ Gateway cell B    │     │ Gateway cell C    │
│ (HR trust domain) │     │ (IT trust domain) │     │ (Sales domain)    │
│ loopback+token    │     │ sandbox mode:all  │     │ SecretRefs        │
│ pairing DMs       │     │ exec deny/ask     │     │ OTel→SIEM         │
└─────────┬─────────┘     └─────────┬─────────┘     └─────────┬─────────┘
          │                         │                         │
          ▼                         ▼                         ▼
┌───────────────────┐     ┌───────────────────┐     ┌───────────────────┐
│ MCP allowlist A   │     │ MCP allowlist B   │     │ MCP allowlist C   │
│ + Markdown memory │     │ + nodes / exec    │     │ + read-only tools │
└───────────────────┘     └───────────────────┘     └───────────────────┘
```

Technology: per-tenant OS user or VM/container cell; sandbox `mode: "all"`; `exec.security: "deny"` or ask; SecretRefs; OTel/Prometheus→SIEM; `openclaw security audit --deep` in CI; primary+fallback models with idle circuit breaker; optional NVIDIA NemoClaw/OpenShell hardening distribution ([Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw); [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)).

**Trade-off matrix**

| Dimension | Alt 1: One shared Gateway, many agents/bindings | Alt 2: Per-tenant Gateway cells (recommended) | Alt 3: Abandon OpenClaw for LangChain/CrewAI DIY mesh |
| --- | --- | --- | --- |
| **Cost** | One host; lowest infra $ | N × host/VPS; still $0 Foundation fee | Eng time + shared infra you design |
| **Latency** | Same process; risk of lane contention across depts | Same per cell; noisy-neighbor isolation | You choose (often adds orchestrator hops) |
| **Ops** | Lowest cell count; hottest blast-radius incidents | High (cells, images, audits) — explicit ops burden | Highest greenfield platform ops |
| **Security** | Violates documented trust model for adversarial users ([Security](https://docs.openclaw.ai/gateway/security)) | Isolation unit = Gateway cell; MCP/tool RBAC per cell | You design Zero-Trust; no OpenClaw channel glue |
| **Scalability** | Vertical only; experimental fleet multi-tenant | Horizontal **by tenant**, not shared process | Arbitrary — you own every failure mode |

**Decision rationale**  
Recommend **Alt 2**: the correct isolation unit under adversarial channel users is the **Gateway cell**, not `sessionKey` or multi-agent bindings on one daemon ([Security](https://docs.openclaw.ai/gateway/security)). Accept higher ops burden as the price of self-host isolation. Use Alt 1 only inside a single trust domain (e.g. one exec’s personal vs work WhatsApp accounts) ([Multi-agent](https://docs.openclaw.ai/concepts/multi-agent)). Choose Alt 3 when you need Kafka/Temporal multi-region workflows OpenClaw does not publish as a reference architecture.

### Interview prompts

1. Draw control plane vs data plane for OpenClaw: what does the Gateway own that a vendor coding harness does not?
2. Why are sandbox and exec approvals off by default, and what hardened baseline would you ship before enabling Heartbeat on a VPS?
3. Given no published p99, how do you assemble an **[inferred]** latency budget from `agent.wait`, idle timeouts, queue debounce, and \(N\) tool rounds?
4. Explain writer-claim fencing + memory flush before compaction — what failure do they prevent on crash mid-stream?
5. Design Zero-Trust for a skill that calls MCP: auth gates, tool RBAC, sandbox, and why ClawHub install ≠ completed scan.
6. When would you pick per-tenant Gateway cells over multi-agent bindings on one Gateway?

---

## Sources (from research)

- [System Design One #151](https://newsletter.systemdesign.one/p/openclaw-architecture) — narrative definition (free teaser; paywalled failure deep-dive not used)
- [docs.openclaw.ai](https://docs.openclaw.ai/) — product home
- [Gateway architecture](https://docs.openclaw.ai/concepts/architecture) · [Agent loop](https://docs.openclaw.ai/concepts/agent-loop) · [Queue](https://docs.openclaw.ai/concepts/queue) · [Memory](https://docs.openclaw.ai/concepts/memory) · [Multi-agent](https://docs.openclaw.ai/concepts/multi-agent) · [Model failover](https://docs.openclaw.ai/concepts/model-failover)
- [Why OpenClaw](https://docs.openclaw.ai/start/why-openclaw) · [Security](https://docs.openclaw.ai/gateway/security) · [Exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook) · [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)
- [Heartbeat](https://docs.openclaw.ai/gateway/heartbeat) · [ClawHub](https://docs.openclaw.ai/clawhub) · [Getting started](https://docs.openclaw.ai/start/openclaw)
