# Module 13: Deep Agents -- Steering & Human-in-the-Loop

## What Is This?

Human-in-the-loop (HITL) in Deep Agents is a durable pause, not a policy decision point (PDP). When the agent proposes a high-risk action -- sending an email, deleting a file, modifying a database -- execution pauses, state serializes to a checkpoint, and the system waits indefinitely for a human to approve, edit, reject, or respond. Think of it like a manager approving expense reports above a threshold: the agent works autonomously on routine tasks but stops for human review on anything irreversible. The mechanism is `interrupt_on` (which tools pause) plus `permissions` (which filesystem paths pause), backed by a checkpointer that makes the pause durable across process restarts. When the human responds via `Command(resume=...)`, the agent resumes from the exact checkpoint -- same state, same context, same thread. The critical design insight is that HITL is fail-open: unnamed tools auto-approve, unmatched permission paths auto-allow, and there is no built-in timeout. You must build expire-deny, circuit breakers, and PII redaction yourself.

---

## 1. System Topology & Data Flow

### 1.1 HITL Architecture

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  MODEL PROPOSES tool_calls                                          │
  │    write_file("/secrets/api_key.txt", ...)                         │
  │    notify_email("alice@company.com", "Q3 Report", ...)             │
  │    read_file("/workspace/report.md")                               │
  └──────────────────────────┬─────────────────────────────────────────┘
                             │
                    ┌────────▼─────────┐
                    │  AFTER_MODEL     │  HumanInTheLoopMiddleware
                    │  (slot 14)       │  inspects each tool_call
                    │                  │  against interrupt_on map
                    │  Filter:         │  + permission when predicates
                    │  - Named in map? │
                    │  - when() True?  │
                    │  - Permission    │
                    │    mode=interrupt?│
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       ┌────────────┐ ┌──────────┐  ┌──────────────┐
       │ AUTO-APPROVE│ │ INTERRUPT │  │ AUTO-APPROVE │
       │ read_file   │ │ write_file│  │ notify_email │
       │ (not in map │ │ (when=    │  │ (in map,     │
       │  or False)  │ │  /secrets)│  │  True)       │
       └────────────┘ │           │  └──────┬───────┘
                      │ ┌─────────┘         │
                      │ │                    │
                      ▼ ▼                    ▼
              ┌──────────────────────────────────────┐
              │  BATCH: HITLRequest                   │
              │  action_requests = [write_file,       │
              │                     notify_email]     │
              │                                       │
              │  interrupt() → checkpoint to Postgres │
              │  API returns result.interrupts (v2)   │
              │  Graph WAITS FOREVER (no built-in TTL)│
              └──────────────┬───────────────────────┘
                             │
                    ┌────────▼─────────┐
                    │  HUMAN REVIEWS   │
                    │  via UI / Slack  │
                    │  / email queue   │
                    │  / API           │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       ┌──────────┐  ┌──────────┐  ┌──────────┐
       │ APPROVE   │  │ EDIT     │  │ REJECT   │
       │ Execute   │  │ Modify   │  │ Skip     │
       │ original  │  │ args,    │  │ tool,    │
       │ args      │  │ then     │  │ error    │
       │           │  │ execute  │  │ ToolMsg  │
       └──────────┘  └──────────┘  └──────────┘
                                        ┌──────────┐
                                        │ RESPOND  │
                                        │ Human    │
                                        │ message  │
                                        │ becomes  │
                                        │ SUCCESS  │
                                        │ ToolMsg  │
                                        └──────────┘
```

### 1.2 Request Flow: Model -> HITL -> Resume -> Execute

1. **Model proposes** `tool_calls` in its completion output.
2. **`after_model` hook** in HumanInTheLoopMiddleware filters by `interrupt_on` map and `when` predicates. Tool calls not in the map or where `when` returns False are auto-approved. Remaining calls become one `HITLRequest`.
3. **`interrupt()` fires**: checkpoints the thread to PostgreSQL and the API returns `result.interrupts` on `version="v2"`.
4. **Human reviews** via UI, Slack, or API. The system waits **forever** -- there is no built-in timeout. Expire-deny is your timer.
5. **Human sends** `Command(resume={"decisions": [...]})` with the same `thread_id`. Decisions must be positional (match `action_requests` order and length).
6. **For each decision:** approve executes original in-memory args; edit modifies args then executes; reject returns error ToolMessage; respond returns human message as success ToolMessage.
7. **FS deny still binds after edit** -- even an edited write_file to /secrets will be blocked by permissions.
8. **Worker releases slot** during HITL pause. A 48-hour Slack approval does not hold a worker.

---

## 2. Core Mechanics & Algorithms

### 2.1 Four Decision Types

| Decision | What Happens | ToolMessage Status | Danger If Misused |
|----------|-------------|-------------------|-------------------|
| **approve** | Tool executes with **original in-memory args** (not a re-parse of the card) | Success | None -- safe default |
| **edit** | Args modified, then tool executes | Success | Can rename the tool (`edited_action.name` unrestricted); large edits trigger model re-evaluation; TOCTOU if display != execute args |
| **reject** | Tool skipped, rejection feedback returned | **Error** | Vague rejections cause 2-3 retry LLM calls |
| **respond** | Human message becomes tool result | **Success** | If used to "deny" a side-effecting tool, the agent treats the denial as success and may report "email sent" |

**Critical rule:** Never use `respond` to deny a side-effecting tool. The model will believe the action succeeded. Use `reject` for denial.

### 2.2 `interrupt_on` Configuration

```python
interrupt_on = {
    # True = interrupt with all 4 decisions
    "delete_file": True,

    # False = never interrupt (even if permission mode="interrupt" would)
    "read_file": False,

    # InterruptOnConfig with options
    "send_email": {
        "allowed_decisions": ["approve", "edit", "reject"],  # no respond
        "description": "Review this email before sending",
        "when": lambda request: request.tool_call["args"].get("to", "").endswith("@external.com"),
    },

    # Permission-based interrupt (deepagents >= 0.6.8)
    # mode="interrupt" on FilesystemPermission synthesizes a when predicate
}
```

**`when` predicate mechanics:**
- Receives `ToolCallRequest` (note: `tool=None` at this hook -- the tool object is not available)
- Returns `True` (interrupt) or `False` (auto-approve, removed from batch)
- A buggy predicate that raises an exception is a **silent hole** -- the tool auto-approves
- `path` vs `file_path` mismatch is a common bug (check the actual arg name)

**User map wins per tool name:** If you set both `interrupt_on={"write_file": True}` AND `permissions` `mode="interrupt"` with a `when` predicate on paths, the `interrupt_on` value (`True`) **overwrites** the permission's `when` -- every write pauses. Setting `False` disables the permission interrupt entirely.

### 2.3 Permission Model (First-Match-Wins, Fail-Open)

Permissions are declared as ordered `FilesystemPermission` rules. Evaluation stops at the first match. **If no rule matches, the operation is ALLOWED** (permissive default -- fail-open).

```python
permissions = [
    # Rule 1: Hard block secrets (deny matches first)
    FilesystemPermission(operations=["read", "write"], paths=["/workspace/.env"], mode="deny"),
    # Rule 2: Pause for human on sensitive config (interrupt)
    FilesystemPermission(operations=["write"], paths=["/config/**"], mode="interrupt"),
    # Rule 3: Allow workspace (matches after deny and interrupt)
    FilesystemPermission(operations=["read", "write"], paths=["/workspace/**"], mode="allow"),
    # Rule 4: Block everything else (catch-all)
    FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
]
```

**Scope limitations:**
- Permissions apply ONLY to built-in filesystem tools (`read_file`, `write_file`, `delete_file`)
- Custom tools, MCP tools, `execute`, and `backend.*` are NOT covered
- Sandbox backends bypass permissions entirely
- `mode="interrupt"` requires deepagents >= 0.6.8

**Permission vs interrupt_on interaction:**

| `interrupt_on` | `permissions` | Result |
|---------------|--------------|--------|
| Not set | `mode="interrupt"` with `when` | `when` predicate on matching paths |
| `True` | `mode="interrupt"` with `when` | **True wins** -- interrupts every call (drops `when`) |
| `False` | `mode="interrupt"` with `when` | **False wins** -- never interrupts (disables permission interrupt) |
| `InterruptOnConfig` with `when` | `mode="interrupt"` with `when` | User `when` replaces permission `when` |

### 2.4 Four-Tier Action Risk Classification

| Tier | Category | Examples | Approval Policy |
|------|----------|---------|-----------------|
| 1 | Read-only | Queries, retrievals, analysis | Fully autonomous |
| 2 | Reversible writes | Draft creation, internal state | Autonomous with audit logging |
| 3 | External side effects | API calls, emails, Slack posts | Conditional interrupt or staging queue |
| 4 | High-risk/Irreversible | Production deploys, payments, data deletion | Mandatory human approval, no exceptions |

**Enforcement must happen at the workflow execution layer, not negotiated by the AI at runtime.** The agent should never decide its own oversight level.

### 2.5 Subagent HITL Inheritance

| Subagent Form | Inherits HITL? | Notes |
|---------------|---------------|-------|
| **GP (default)** | Yes | Parent `interrupt_on` + `permissions` apply |
| **Declarative SubAgent** | Yes, but **replaces entirely** if spec sets it (PR #2334) | No merge -- setting spec HITL drops all parent gates |
| **CompiledSubAgent** | **No** | Must wire HITL inside the child graph |
| **AsyncSubAgent** | **No** | Own deployment, own gates |
| **Dynamic (QuickJS eval)** | **Bypasses** parent `interrupt_on` | Gate `eval`; `interrupt_on={"task": True}` does NOT catch JS `task()` |

### 2.6 Checkpointer Requirement

**A checkpointer is REQUIRED for HITL.** Without one:
- `interrupt()` fails or the graph crashes mid-turn
- Resume cannot find the paused state
- Tools may run without pause

| Checkpointer | Production Use |
|--------------|---------------|
| `PostgresSaver` (sync/async) | **Required for production** -- durable across restarts |
| `MemorySaver` | **Tests only** -- dies on process restart |
| Agent Server injected | **Default for deployed agents** -- do not pass checkpointer= in graph code |
| None (default) | **HITL is silently broken** |

**Durability modes:**
- `sync`: checkpoint before next step -- highest durability, extra latency
- `async` (Agent Server default): checkpoint after each step, async -- small crash window
- `exit`: checkpoint only on graph exit -- no mid-run HITL resume

**Resume mechanics:**
- Same `thread_id` required
- `Command(resume={"decisions": [...]})` with `version="v2"`
- Do NOT pass a new input dict or `Command(update=)` to continue a pause
- Time travel always re-triggers interrupts and forks -- it will NOT un-send an email

---

## 3. Token Economics & NFR Analysis

### 3.1 HITL Cost Impact

HITL adds no model tokens if the wait stays within the 5-minute cache TTL. The cost driver is human FTE and p99, not LLM dollars.

| Scenario | Per Run | Per 1k Runs | Notes |
|----------|---------|-------------|-------|
| 0% interrupt rate | $0.2229 | **$223** | Baseline 10-call cached |
| 10% interrupt, waits <5m | $0.2229 | **$223** | Cache stays warm; tokens unchanged |
| 10% interrupt, waits >5m | $0.2270 | **$227** | Cache-miss tax on prefix rewrite |
| 7% reject on the 10% slice | +$0.00021/run | negligible | Extra model call for retry |

**The bill that moves is reviewer FTE, not LLM tokens:**

| Interrupt Rate | Reviewer Load (1k runs/day, 30s/card) | Load (3 min/card) |
|---------------|---------------------------------------|-------------------|
| **10%** | **0.8 hours/day** | **5 hours/day** |
| **Every write (8 cards/run)** | **~40 hours/day** | Collapse |

**Approval fatigue benchmark (Anthropic analog):** ~93% of users approve permission prompts. Sandbox reduces prompts by 84%. This means if every write pauses, reviewers rubber-stamp 93% -- HITL becomes security theater.

### 3.2 Rejection Overhead

Vague rejections ("no, do something else") trigger 2-3 additional LLM calls as the model explores alternatives. Well-crafted rejections with domain-specific guidance resolve in a single retry:

- **Bad:** "No, try something else" -> 2-3 extra calls -> ~$0.07 extra
- **Good:** "Do not retry. Ask the user which file to archive." -> 1 extra call -> ~$0.02 extra

### 3.3 Latency SLA Targets

| Path | p50 | p95 | p99 | Notes |
|------|-----|-----|-----|-------|
| **HITL middleware CPU** | **1 ms** | **5 ms** | **20 ms** | [inferred] Microsecond-class, rounded |
| **Resume checkpoint load/write (sync)** | **10 ms** | **50 ms** | **200 ms** | [inferred policy] Postgres sync |
| **Command -> ToolNode start (no human)** | **15 ms** | **80 ms** | **400 ms** | [inferred] After human sends Command |
| **Human clock (interactive)** | **30,000 ms** | **180,000 ms** | **600,000 ms** | [inferred policy] 30s interactive / 3m Slack / 10m expire-deny |

**Gateway timeout risk:** AWS API Gateway closes connections at 29s. LLM calls routinely take 30-60s. Without streaming or `--timeout-keep-alive 65`, requests drop silently at p95+. HITL waits are entirely off the request thread (worker releases slot).

### 3.4 Stale Execution Risk

If an agent waits days for approval, its context may be invalid:
- OAuth tokens expire (HubSpot ~30min, Google ~1hr, Salesforce ~2hr)
- Pagination cursors go stale
- Database state changes

**Mitigation:** Verify action hash on resume. Hash the proposed action arguments at interrupt time; re-verify on resume. If the hash doesn't match (args were modified by time travel or replay), reject automatically.

---

## 4. Distributed Resilience & Security

### 4.1 HITL is NOT Zero-Trust

This is a critical interview point. HITL does NOT:
- Authenticate the reviewer (no identity verification)
- Evaluate `(principal, action, resource)` as a PDP would
- Bind args to the approval (no hash, no nonce, no TTL in-tree -- AISVS 9.2.3 gap)
- Cover unnamed tools (they auto-approve)
- Cover MCP tools beyond naming them in `interrupt_on`

**What Zero-Trust requires for MCP (still needed even with HITL):**
- Gateway PEP: OAuth 2.1 + PKCE
- RFC 8707 audience = canonical MCP server URI on authorize AND token
- No token passthrough (RFC 8693 exchange for upstream APIs)
- Hash-pin tool JSON on every `tools/call` (CVE-2025-54136 MCPoison, CVSS 8.8)
- Elicitation is a different human pause; it does not authorize the client to the server

**A HITL click does not mint an OAuth token.**

### 4.2 TOCTOU and Double-Execute

**TOCTOU (Time-of-check/Time-of-use, CWE-367):** Approve executes original in-memory args, not a re-parse of the card shown to the reviewer. If display args differ from execute args (e.g., due to a race), the reviewer approved something different from what runs.

**Double-execute:** Two resume Commands on the same interrupt can cause duplicate side effects. The library has no nonce or CAS (compare-and-swap) ticket in-tree.

**Mitigations (all yours to build):**
- CAS ticket: only the first resume succeeds; second gets "refused"
- Arg-digest bind: hash the args shown to the reviewer; verify on execute
- Idempotent tools: upserts instead of inserts; deduplication keys
- Actor identity: log approver_id + arg_digest off the checkpointer (WORM)

### 4.3 Circuit Breaker for HITL Service

The library waits forever. Build your own breaker:

```
  HITL Service Breaker: closed -> open -> half-open
  
  Trip: reviewer service down / queue overflow / consecutive timeouts
  Half-open probe: reject Command or health GET -- NEVER Approve
  Fallback: HITL -> deny -> refuse
  
  EXPIRE-DENY at 10 minutes (your timer)
  Circuit OPEN = deny, NEVER approve
  Expire-approve turns TTL into an attacker-controlled delay
```

### 4.4 PII on HITL Cards

Default `description` embeds full tool args into the checkpoint, the UI, and traces. This is a GDPR/HIPAA incident waiting to happen.

**Pipeline (three steps, all required):**

1. **Detect** with regex (email, PAN, SSN, phones) + optional ML NER before render
2. **Redact/mask/hash** in a custom `description` factory; keep raw args server-side for the digest bind; block PAN onto Slack/MCP
3. **Audit WORM** of approver_id, arg_digest, decision, correlation_id, thread_id -- not raw PAN

**`PIIMiddleware` on the agent does NOT redact interrupt payloads.** You must add a custom description factory.

### 4.5 Regulatory Drivers

| Regulation | Date | HITL Requirement |
|-----------|------|-----------------|
| **EU AI Act Article 14** | August 2, 2026 | Mandates human ability to "intervene, stop, or override" high-risk AI |
| **NIST AI Agent Standards** | February 2026 | Infrastructure requirements for agent oversight |
| **California SB-833** | July 1, 2026 | State-level agent oversight requirements |
| **OWASP LLM Top 10** | Ongoing | "Excessive Agency" as dedicated risk class |

---

## 5. Common Failure Modes

| Failure | Cause | Detection | Mitigation |
|---------|-------|-----------|------------|
| HITL silently broken | `checkpointer=None` (default) or no `thread_id` | Tools run without pause, or graph crashes | Always pass Postgres checkpointer + `thread_id` |
| Approval fatigue -> auto-approve | `True` on high-frequency tools; 93% rubber-stamp rate | Approve-all patterns; unanchored bulk interrupt | Sandbox + deny PDP; `when` on dest/amount; no respond on irreversible |
| `when` predicate silent hole | `path` vs `file_path` mismatch; exception in predicate; stale allowlist | Destructive call absent from `action_requests` | Wrap predicates; golden skip tests; fail-closed to interrupt |
| Interpreter bypass | `task()` inside `eval`; PTC from QuickJS | Child/MCP ran with no card | Gate `eval`; `interrupt_on={"task": True}` does NOT catch JS `task()` |
| Compiled/async child ungoverned | No HITL inherit for these forms | Child `write_file` executes without review | Wire HITL inside the child; restate parent gates |
| Permission vs `interrupt_on` mismatch | User map wins per tool name; True drops `when`; False disables interrupt | /secrets never pauses, or every write pauses | Do not set `write_file` True/False beside interrupt-mode `when` |
| `respond` used as reject | Status is "success"; model thinks action succeeded | "Email sent" when it wasn't | Forbid `respond` on side-effecting tools |
| `edit` renames tool | `edited_action.name` is unrestricted | `notify_email` edited to `delete_customer` | Validate name in resume handler |
| `try/except` swallows interrupt | Bare `except Exception` in graph code | No pause; tool runs | Never wrap `interrupt()` in bare except |
| Expire-approve | TTL fires `{"type": "approve"}` | Attacker waits out the human | **Never.** Always expire-deny |
| Double-execute | Two resume Commands; time-travel fork | Duplicate side effects | CAS ticket; idempotent tools |
| Checkpoint GC of paused threads | Retention deletes `next` waiting on interrupt | Accidental expire without ToolMessage | Filter pending interrupts from GC |
| MCP without gateway | `interrupt_on` on `mail.send` only; other MCP auto-runs | Unnamed MCP auto-approves | Gateway PEP + hash-pin; `permissions=` does not see MCP |
| PII on the HITL card | Default `description` embeds full args | GDPR/HIPAA incident | detect -> redact -> audit; custom description factory |
| LocalShell + FS deny, no execute HITL | Shell ignores `permissions=` | Host RCE | Sandbox; or HITL on ALL ops (docs), laptop only |
| Wrong invoke version | Check `.interrupts` on v1 or miss `stream.interrupted` | UI thinks done while paused | `version="v2"` / stream v3 |
| `allowed_decisions=["approve"]` + TTL reject | TTL sends disallowed `reject` | `ValueError`; thread stuck forever | Allow `reject` for timeouts or send an allowed type |

---

## 6. Architectural System Design Scenarios

### Scenario A -- Destructive FS + Email-Send HITL

**Problem.** A coding/research agent may write workspace files freely. Writing to `/secrets` or `/memories` and sending email require human approval. MCP mail still cannot skip the gateway. Reviewers rubber-stamp if every `write_file` pauses (93% approve analog).

**Architecture (recommended: A1 -- permission interrupt on secrets + named email HITL + catch-all FS deny + MCP gateway):**

```
  ┌─────────┐   ┌──────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: create_deep_agent                           │
  │ JWT ->  │   │   permissions: interrupt /secrets/** /memories/**    │
  │ reviewer│   │                allow /workspace/**                   │
  │ role != │   │                deny /**  (fail-closed catch-all)     │
  │ chat    │   │   interrupt_on: notify_email + mcp mail.send         │
  │ user    │   │     allowed=["approve","edit","reject"]  (no respond)│
  │         │   │   DO NOT set write_file True/False (clobbers when)   │
  │         │   │   PostgresSaver sync; thread_id uuid7; TTL 600s deny│
  │         │   │   CAS ticket + arg digest + actor_id WORM            │
  │         │   │   PII detect->redact->audit on HITL cards            │
  │         │   │   Gateway PEP + DLP + dest allowlist (RFC 8707)      │
  └─────────┘   └──────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Axis | A1: Permission-interrupt + named email + gateway (recommended) | A2: HITL on every write_file | A3: No HITL, fail-closed FS + gateway only |
|------|-------------------------------------------------------------|------------------------------|-------------------------------------------|
| **Cost** | ~$223/1k if waits <5m; FTE ~0.8h/day at 1k runs x 10% x 30s | Same LLM $ until fatigue; ~40 reviewer-hours/day at 8 cards/run | Lowest LLM ($223/1k); no FTE; no human for policy exceptions |
| **Latency** | Human clock 30s/3m/10m on rare cards; resume 15/80/400ms | Fatigue-dominated p99; cache miss if >5m | Model+tool only (2s/8s/20s ReAct); no HITL clock |
| **Security** | Best in-tree FS story + LLM03 #6 on email; MCP still needs gateway. HITL is NOT Zero-Trust | Looks strict; 93% rubber-stamp; TOCTOU unmitigated | Strong for FS; zero human for irreversible send if gateway miss |
| **Scalability** | Reviewer staffing on rare cards | Reviewer collapse | Horizontal PEP |

### Scenario B -- Enterprise Document Processing with Tiered Human Review

**Problem.** A financial services firm processes 5,000 loan applications daily. Each requires document extraction, validation, credit scoring, and approval. Regulatory requirements (CFPB, ECOA) mandate human review for any denial and for applications above $500K. Target: 80% fully automated, 20% human review, 30-minute average processing time.

**Architecture:**

```
  ┌─────────────┐     ┌──────────────────────────────────────────────┐
  │  Document    │     │            Agent Pipeline (LangGraph)        │
  │  Ingestion   │────>│                                              │
  │  (S3 upload) │     │  Extract --> Validate --> Risk Score          │
  └─────────────┘     │                              |                │
                      │           ┌──────────────────┤                │
                      │           v                  v                │
                      │    ┌──────────────┐  ┌────────────┐          │
                      │    │ Auto-Approve  │  │ HITL Queue │          │
                      │    │ (Score>700,   │  │ (Tier 2-3) │          │
                      │    │  <$500K)      │  └─────┬──────┘          │
                      │    └──────┬───────┘        │                  │
                      │           v                v                  │
                      │    ┌─────────────────────────────┐            │
                      │    │     Decision + Audit Log     │            │
                      │    └─────────────────────────────┘            │
                      └──────────────────────────────────────────────┘
```

**Interrupt routing logic:**
- Score > 700 AND amount < $500K: auto-approve (Tier 1)
- Score 600-700 OR amount $500K-$2M: async review queue, 4-hour SLA (Tier 2)
- Score < 600 OR denial: mandatory senior review, 1-hour SLA (Tier 3)
- Any ECOA-flagged demographic correlation: compliance officer, 15-min SLA (Tier 3)

**Stale execution guard:** Hash the credit score + application data at interrupt time. On resume, re-verify the hash. If the applicant's credit score changed during the review period, auto-reject with "Action context has changed since original proposal. Re-evaluate."

---

## Interview Q&A

**Q1. What is Deep Agents steering, in one minute?**
I treat HITL as a durable pause, not a PDP. `interrupt_on` names tools that `HumanInTheLoopMiddleware` batches after the model proposes `tool_calls`. Unnamed tools auto-approve -- HITL is fail-open. A checkpointer is required; LangGraph waits forever; expire-deny is my timer sending a `reject` Command. `permissions` `mode="interrupt"` synthesizes the same middleware for FS paths, still fail-open, still not covering MCP or `execute`. The model proposes; middleware plus my resume handler dispose.

**Q2. Walk model tool_calls -> HITL -> resume -> execute.**
After the completion, `after_model` filters by `interrupt_on` and `when`. Remaining calls become one `HITLRequest`. `interrupt()` checkpoints the thread and the API returns `result.interrupts` on `version="v2"`. I show cards from `action_requests` (display copies). I resume the same `thread_id` with `Command(resume={"decisions": [...]})` positional. Approve keeps a ToolCall with original args; edit modifies args; reject/respond inject a ToolMessage. ToolNode runs; FS deny still binds after edit. I never pass a new input dict or `Command(update=)` to continue a pause.

**Q3. What are the four decisions and their dangers?**
Approve executes original args -- safe default. Edit modifies args then executes -- danger is `edited_action.name` is unrestricted (can rename the tool) and TOCTOU between display and execute args. Reject returns error ToolMessage -- danger is vague rejections causing 2-3 retry calls. Respond returns human message as SUCCESS ToolMessage -- danger is using it to deny a side-effecting tool; the model believes the action succeeded. I never use respond on irreversible tools.

**Q4. Is HITL Zero-Trust? What about MCP?**
No. HITL does not authenticate, does not evaluate `(principal, action, resource)`, does not bind args, does not cover unnamed tools. `permissions=` is a fail-open FS path PDP. MCP tools can be named in `interrupt_on` -- that is still a review queue, not authorization. Zero-Trust is a gateway PEP: OAuth 2.1, RFC 8707 audience = canonical server URI, no token passthrough (RFC 8693 exchange), hash-pin tool JSON on every `tools/call`. A HITL click does not mint that token.

**Q5. Give me the cost math at 0% vs 10% interrupt.**
Same 10-call Sonnet 4.6 shape: $223/1k at 0%. At 10% interrupt, tokens stay $223 if waits stay inside the 5m cache. If every wait exceeds 5m, prefix cache-miss tax is $227/1k. LLM dollars are not the story -- reviewer FTE is. At 1k runs/day with 10% interrupt and 30s/card, that is 0.8 hours/day. At 3 min/card, 5 hours/day. At every-write (8 cards/run), approximately 40 hours/day.

**Q6. What about the inheritance and the interpreter hole?**
Declarative specs and auto GP inherit parent `interrupt_on` / `permissions`; a spec replaces entirely if set (PR #2334). Compiled and async do not inherit -- I wire HITL inside the child. Interpreter `task()` from `eval` skips parent `interrupt_on` per dispatch -- I gate `eval`. `interrupt_on={"task": True}` does not catch JS `task()`. Two resume dialects exist: HITLRequest `decisions` vs raw `interrupt()` values -- the UI must branch.

**Q7. PII on the HITL card -- what do you do?**
Default description embeds full tool args into the checkpoint, the UI, and traces. I detect with regex plus optional ML before render; redact/mask/hash in a custom `description` factory; keep raw args server-side for the digest bind; block PAN onto Slack/MCP. I audit WORM of approver_id, arg_digest, decision, correlation_id, thread_id -- not raw PAN. `PIIMiddleware` on the agent does NOT redact interrupt payloads.

**Q8. Circuit breaker and timeout -- what happens?**
The library waits forever. My HITL-service breaker is closed -> open -> half-open. Half-open probe is a reject Command or health GET -- never Approve. Fallback is HITL -> deny -> refuse. Expire-deny at 10 minutes. Circuit open is deny, not approve. Expire-approve turns TTL into an attacker-controlled delay. I CAS the ticket so double-click cannot double-send.

**Q9. Permission first-match-wins -- walk me through the failure mode.**
Rules evaluate top-down and stop at first match. If I put `allow /workspace/**` before `deny /workspace/.env`, the .env file is allowed because the allow matches first. Correct order: specific denies first, then allows, then catch-all deny. If no rule matches at all, the operation is ALLOWED (fail-open). This means a missing catch-all `deny /**` is an open door.

**Q10. How do you prevent approval fatigue?**
Sandbox bounds blast radius without a human; HITL bounds when to ask. The 93% approve rate analog tells me that if I gate every write, humans will rubber-stamp. I use `when` predicates to interrupt only on high-risk paths (dest/amount thresholds), never on read operations, and keep the interrupt rate under 10% of runs. I remove `respond` from side-effecting tools so reviewers cannot accidentally "approve" by responding. For coding agents, sandbox + HITL only on escapes (network enable, path outside workspace).

**Q11. How do subagents inherit HITL, and what are the gaps?**
GP and declarative SubAgents inherit parent `interrupt_on` and `permissions`. But declarative specs REPLACE (not merge) if they set their own -- accidentally dropping parent safety gates. Compiled and async SubAgents inherit nothing; I must wire HITL inside each child graph. The interpreter hole: `eval` creating a `task()` call bypasses parent `interrupt_on`. PTC from QuickJS also bypasses. Mitigation: gate `eval`, restate parent gates in compiled children.

**Q12. Checkpointer durability and lost resume.**
`sync` writes before next step -- highest durability. `async` (Agent Server default) has a small crash window where the last step may be lost. `exit` only writes on graph exit -- no mid-run HITL resume if the process dies. InMemory dies on restart. Resume needs the same `thread_id` and a `Command`, not a new dict. Time travel always re-triggers interrupts and forks -- it will not un-send email. Retention must not GC pending interrupts. AISVS is right that checkpoints are not durable execution; I add Temporal or a queue TTL if I need that guarantee.

---

## Key Numbers to Memorize

### HITL Mechanics
| Number | What |
|--------|------|
| **4** | Decision types: approve, edit, reject, respond |
| **fail-open** | Unnamed `interrupt_on` keys AND unmatched `permissions=` paths |
| **first-match** | `permissions=` evaluation order; deny-before-interrupt wins |
| **positional** | `decisions` must match `action_requests` order and length |
| **"error" / "success"** | reject vs respond synthetic ToolMessage status |
| **Slot 14** | HumanInTheLoopMiddleware position in stack |
| **>=0.5.2** | `permissions=` available |
| **>=0.6.8** | Permission `mode="interrupt"` |
| **>=0.7** / **>=0.7.3** | `delete` tool / exact-match file `delete` |
| **PR #2334** | Declarative specs inherit parent `interrupt_on` |
| **255** | Postgres `thread_id` max chars |

### Cost [inferred]
| Number | What |
|--------|------|
| **$223 / 1k** | 0% interrupt, cached |
| **$223 / 1k** | 10% interrupt, waits <5m (unchanged) |
| **$227 / 1k** | 10% interrupt, waits >5m (cache-miss tax) |
| **~93%** | Users approve permission prompts (Anthropic analog) |
| **84%** | Sandbox reduction in prompts |
| **0.8h / 5h / ~40h per day** | Reviewer load at 1k runs: 10%x30s / 10%x3min / every-write 8 cards |

### Latency [inferred policy]
| Number | What |
|--------|------|
| **30,000 / 180,000 / 600,000 ms** | Human clock p50/p95/p99; p99 = expire-deny 10 min |
| **1 / 5 / 20 ms** | HITL middleware CPU |
| **10 / 50 / 200 ms** | Resume checkpoint load/write (sync) |
| **15 / 80 / 400 ms** | Command -> ToolNode start, no human |

### Security
| Number | What |
|--------|------|
| **detect -> redact -> audit** | PII on HITL UI, checkpoints, traces before persist |
| **RFC 8707 / 8693** | MCP resource indicator / token exchange -- HITL click does not mint these |
| **CWE-367** | TOCTOU on approved args / FS paths / edits |
| **AISVS C9.2 / 9.2.3** | Interrupt is not approval workflow (no TTL/notify/nonce in-tree) |

---

## Quick Reference

```
HITL = DURABLE PAUSE, NOT PDP
  interrupt_on names tools -> HumanInTheLoopMiddleware batches -> interrupt()
  Unnamed tools AUTO-APPROVE (fail-open)
  Checkpointer REQUIRED (Postgres, not MemorySaver in prod)
  Graph waits FOREVER -- expire-deny is YOUR timer

FOUR DECISIONS
  approve  -> execute original args          (status: success)
  edit     -> modify args, then execute      (status: success)  PIN THE NAME
  reject   -> skip, error feedback           (status: error)
  respond  -> human message as tool result   (status: SUCCESS)  NEVER for deny

PERMISSIONS
  First-match-wins, FAIL-OPEN if no match
  Covers ONLY built-in FS tools (not MCP, execute, custom)
  mode="deny" | "allow" | "interrupt"
  Order: specific deny -> interrupt -> general allow -> catch-all deny

INHERITANCE
  GP/Declarative: inherit HITL (declarative REPLACES if set)
  Compiled/Async: DO NOT inherit -- wire your own
  Interpreter eval: BYPASSES parent interrupt_on

EXPIRE POLICY
  NEVER expire-approve
  ALWAYS expire-deny
  CAS ticket for double-click prevention
  Arg-digest bind for TOCTOU
```
