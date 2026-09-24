# Research: Multi-Agent Orchestration

**Date researched**: 2026-09-23
**Sources consulted**: 92

Scope: **orchestration semantics across agents** — supervisor / hierarchical / router / swarm topologies; typed state handoffs (OpenAI Agents SDK `handoff()` vs `Agent.as_tool()`, LangGraph `Command`/`Send` + `active_agent`); consensus (voting, debate, majority, judge, disagreement thresholds); HITL (`interrupt`, approval queues, LangGraph HITL); escalation (SLA, confidence, cost, security → human or specialist). Single-agent loop fuses, ReAct/P&E math, and `max_turns`/`recursion_limit` live in [`04-agent-loop-patterns.md`](04-agent-loop-patterns.md). Pregel super-steps, channel reducers, `Command`/`Send` field tables, interrupt node-restart, subgraph checkpointers, and `HumanInTheLoopMiddleware` resume shapes live in [`05-langgraph-state-machines.md`](05-langgraph-state-machines.md). MCP JSON-RPC, OAuth 2.1 / RFC 8707, token passthrough, and Zero-Trust tool-plane rules live in [`08-mcp-integrations.md`](08-mcp-integrations.md). This file does **not** recopy those APIs. It is the **multi-agent control plane**: who may be next, what state crosses the boundary, how agents agree, when a human or a specialist is required. Vendor list prices used in §2 are from [`01-python-llm-foundations.md`](01-python-llm-foundations.md) (2026-09-23). ⚠️ No unpublished production p50/p95/p99 for supervisor-worker loops is invented. `$ / 1k tickets` figures are **[inferred]** from a stated hop skeleton × list prices — not a vendor SKU.

Invariant: **the model never routes, never hands off, never grants authority, never counts a vote, never times out HITL**. It emits a structured action (`transfer_to_*`, `Command(goto=…)`, A2A `SendMessage`, a vote JSON). A **runtime** mutates durable state, enforces hop / $ / tool allowlists, and decides the next node. Collapsing “who may act” into the prompt is the dominant enterprise failure ([LangChain multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent); [AISVS C9.5.3](https://github.com/OWASP/AISVS/blob/main/1.0/en/0x10-C09-Orchestration-and-Agentic-Action.md)).

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane (multi-agent)

An in-process graph, a hosted runner, and a cross-org protocol all split the same way. The **control plane** owns next-agent, hop budget, kill-switch, HITL gates, and vote aggregation. The **data plane** owns tool HTTP, MCP `tools/call` (08), A2A artifacts, and blackboard blobs. Persistence identity (`thread_id`, OpenAI `RunState` / `session_id`, A2A `contextId`+`taskId`) is control, not chat text.

| Plane | Owns in a multi-agent system | Failure if fused into the LLM |
| --- | --- | --- |
| **Control** | Next agent, max hops, parallel fan-out cap, HITL timeout-deny, disagreement → escalate | Ping-pong; 50-subagent fan-out; spend unbounded |
| **Data** | Worker tools, MCP, A2A `Artifact`/`Part`, filesystem refs | PII copied on every hop; confused-deputy token passthrough |
| **Persistence** | Resume identity after crash or approval wait | Restart from scratch after a 500; rainbow-deploy kills in-flight research |
| **Policy** | Per-agent tool allowlist, principal, reversibility class | Worker inherits supervisor’s OAuth cookie |

LangChain’s 2026 framing: teams ask for “multi-agent” when they actually need **context management**, **distributed development**, or **parallelization**. If context were infinite and latency zero, a single agent with all tools would dominate. Skills (progressive disclosure) are often the cheaper substitute for a second agent ([multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent); [Agent Skills](https://agentskills.io/)).

Microsoft Learn (updated 2026): **prefer platform-native orchestration for internal subagents**; **MCP for tools/data**; **A2A for opaque, cross-platform, cross-org agents**. That is the control/data split in protocol form: MCP is the tool bus (08); A2A is the agent bus ([multi-agent patterns](https://learn.microsoft.com/en-us/agents/architecture/multi-agent-patterns); [A2A and MCP](https://a2a-protocol.org/latest/topics/a2a-and-mcp/)).

Each specialist’s **inner** loop is still a 04 ReAct/P&E cycle. Orchestration adds a **second clock**: who is allowed to run that loop next, and what of their state is visible to the next hop.

### 1.2 Five topologies (and what they serialize)

LangGraph JS concepts, LangChain 1.x patterns, and Microsoft Agent Framework 1.0 (Apr 2026) converge on the same five shapes ([multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent); [MAF 1.0](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/); [Azure agent design patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)):

| Topology | Who picks the next hop | User-facing owner | Parallelism | Typical product |
| --- | --- | --- | --- | --- |
| **Router** | One classification step, then specialist(s) | Synthesizer or the specialist | `Send` fan-out | LangChain Router + `Send` |
| **Supervisor / orchestrator-worker** | Central LLM (or ledger) every round | Supervisor synthesizes | Optional (`parallel_tool_calls`) | LangGraph `create_supervisor`; Anthropic Research; Magentic-One |
| **Hierarchical supervisors** | Supervisor of compiled supervisors | Top-level only | Per-team | `create_supervisor([research_team, writing_team])` |
| **Swarm / mesh / handoff** | Currently active agent | Whoever is `active_agent` | Sequential by default | LangGraph swarm; OpenAI `handoffs`; MAF handoff |
| **Custom / blackboard / Network** | State schema, Hub, or blackboard controller | Defined by workflow | Mixed | LangGraph custom graph; AG2 Hub+channels |

**Router vs supervisor vs orchestrator are not synonyms.** They are three **control-plane clocks**:

| Role | Clock | Decision | When it wins |
| --- | --- | --- | --- |
| **Router** | Once per user turn | Classify → 1..K specialists | Known domains, parallel retrieval, no sticky ownership |
| **Orchestrator (lead)** | Every round until “enough” | Decompose, spawn, synthesize, re-spawn | Breadth-first research, unknown search DAG |
| **Supervisor (LangGraph)** | Every worker return | Which worker tool next, or FINISH | Tool isolation + centralized reply |
| **Hierarchical supervisor** | Per level | Which *team* next | Org/IAM boundaries, not token savings |

### 1.3 Supervisor: `create_supervisor`, subagents-as-tools, hierarchical

`langgraph-supervisor.create_supervisor(agents, model, …)` compiles a `StateGraph` whose supervisor LLM is bound to **handoff tools**. Defaults that change production topology: `output_mode='last_message'` (not `full_history`); `parallel_tool_calls=False` (OpenAI/Anthropic only if you flip it); `add_handoff_messages=True`; `handoff_tool_prefix` optional ([`create_supervisor`](https://reference.langchain.com/python/langgraph-supervisor/supervisor/create_supervisor); [source](https://github.com/langchain-ai/langgraph-supervisor-py/blob/main/langgraph_supervisor/supervisor.py)).

LangChain 1.x **recommends implementing the supervisor as ordinary tools** rather than the `langgraph-supervisor` package — more control over context engineering; the package is kept for 1.0 compatibility and is **no longer actively maintained** for new work ([migrate](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor); [repo README](https://github.com/langchain-ai/langgraph-supervisor-py)). Replacement: `create_agent` with workers wrapped as `@tool` (the **subagents** pattern). Nested supervisors become a middle-tier agent that is itself a tool.

**Hierarchical.** A compiled supervisor is a Pregel object and can sit in another supervisor’s `agents=` list. Library example: `research_team` (researcher+math) and `writing_team` (writer+publisher) under `top_level_supervisor` ([README](https://github.com/langchain-ai/langgraph-supervisor-py)). This is **not** free: each level adds at least one model call and a context splice. Use it when **teams have separate checkpointers, tool IAM, and release cadences**, not because the org chart looks like a tree. Cite **05** for nested-subgraph `checkpointer=` semantics; the multi-agent claim is **IAM + SLO isolation**, not Pregel trivia.

**Subagents vs handoffs (LangChain call table).** Subagents: main agent keeps the user-facing reply; workers are tools. Handoffs: worker becomes the user-facing owner ([multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent); [architecture blog](https://www.langchain.com/blog/choosing-the-right-multi-agent-architecture)):

| Workload | Subagents | Handoffs | Skills | Router |
| --- | --- | --- | --- | --- |
| One-shot “buy coffee” | **4** calls | **3** | **3** | **3** |
| Repeat same request | **4+4=8** | **3+2=5** | **3+2=5** | **3+3=6** |
| Multi-domain (3× ~2k-token specialists, parallel OK) | **5** calls, **~9K** tokens | **7+** calls, **~14K+** (sequential, growing history) | **3** calls, **~15K** | **5** calls, **~9K** |

Subagents win isolation + parallel. Handoffs win sticky conversations (40–50% fewer calls on repeats). Skills win “one agent, many playbooks.” Router wins explicit classification + parallel without a sticky specialist. `parallel_tool_calls=True` on a supervisor turns one tick into a **fan-out orchestrator**.

LangSmith’s earlier architecture note: give the supervisor a `forward_message` tool so it does **not** paraphrase the specialist (telephone-game mitigation); strip supervisor routing messages from the worker’s view; tool-name framing (`delegate_to_*` vs `transfer_to_*`) is an eval surface ([benchmarking multi-agent](https://www.langchain.com/blog/benchmarking-multi-agent-architectures)).

### 1.4 Swarm / OpenAI typed handoffs / shared vs partitioned state

**LangGraph swarm.** `create_handoff_tool(agent_name=…)` returns `Command(goto=agent_name, graph=Command.PARENT, update={messages, active_agent})`. `create_swarm(..., default_active_agent=…)` plus `add_active_agent_router` makes the **next user turn skip the router** and resume the last specialist ([`create_handoff_tool`](https://reference.langchain.com/python/langgraph-swarm/handoff/create_handoff_tool); [source](https://github.com/langchain-ai/langgraph-swarm-py/blob/main/langgraph_swarm/handoff.py); [`create_swarm`](https://reference.langchain.com/python/langgraph-swarm/swarm/create_swarm)). This is why LangChain’s repeat-request table shows handoffs at **2 calls** on turn 2 vs subagents’ **4**. Failure mode: two specialists with reciprocal `transfer_to_*` and no hop cap. Disable parallel tool calls on swarms so two handoffs cannot fire in one tick (LastValue race on `active_agent` — reducer rules in **05**).

**OpenAI Agents SDK — two official patterns** ([handoffs](https://openai.github.io/openai-agents-python/handoffs/); [orchestration](https://openai.github.io/openai-agents-python/multi_agent/); [tools](https://openai.github.io/openai-agents-python/tools/)):

| Pattern | Primitive | Who owns the next user-visible token | Guardrails | Use |
| --- | --- | --- | --- | --- |
| **Handoff** | `handoffs=[billing, handoff(refund)]`; tool name `transfer_to_<agent>` | Specialist | Input guardrails = **first** agent only; output = **last** agent only | Conversation ownership changes |
| **Agent-as-tool** | `specialist.as_tool(...)` | Manager | Nested run; `needs_approval` supported on `as_tool` | Bounded subtask; manager synthesizes |

`handoff()` knobs that are **typed-state**, not prompt: `tool_name_override`, `tool_description_override`, `on_handoff` (side effects at the instant of transfer — log, prefetch, **downscope token**), `input_type` (Pydantic metadata: `reason`, `priority` — **does not** choose destination and **does not** replace the next agent’s input), `input_filter` / `RunConfig.handoff_input_filter`, `is_enabled` (predicate), `nest_handoff_history` (opt-in beta compaction). Helper `handoff_filters.remove_all_tools` strips structured tool I/O so the specialist does not drown in prior function calls. **Register one handoff per destination**; a custom `Handoff` object is only for code that picks the target at invocation time. Combine: triage **hands off** to refund; refund **calls** a policy agent as a tool.

`input_type` is **not** authorization. `is_enabled` is evaluated **before** the model returns arguments, so it cannot gate on parsed fields. If authorization depends on `EscalationData.reason`, check at the start of `on_handoff` and **raise** — the SDK continues the transfer after `on_handoff` returns successfully. Tool input guardrails **do not wrap handoffs**. Nested handoff history **does not redact PII**; tool args can remain inside generated summaries ([handoffs](https://openai.github.io/openai-agents-python/handoffs/)). Server-managed `conversation_id` / `previous_response_id` **do not support** handoff input filters.

**LangChain independently adopted the same word.** Two implementations ([handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs)): (1) **single agent + middleware** (`@wrap_model_call` swaps prompt/tools — recommended default); (2) **subgraph agents** + `Command.PARENT`. Subgraph handoffs **must** pass the triggering `AIMessage` **and** a `ToolMessage` with matching `tool_call_id` or the next model sees a malformed transcript. Passing the full subagent history is optional and usually wrong (token bloat + internal reasoning leak). Command/Send field tables: **05**. Multi-agent rule: the parent `messages` channel needs `add_messages`; `active_agent` is LastValue; parallel `Send` workers writing a scalar without a reducer raise `InvalidUpdateError` (**05**).

**Shared vs partitioned state.**

| State style | What is shared | Isolation | Typical |
| --- | --- | --- | --- |
| **Shared transcript** | One `messages` reducer | Weak; every hop sees prior tools unless filtered | Swarm, OpenAI default handoff |
| **Partitioned / isolated windows** | Brief + summary / filesystem ref | Strong; Anthropic subagents | Research orchestrator |
| **Shared blackboard, private scratch** | Public board + private debate spaces | Medium | LbMAS (arXiv:2507.01701) |
| **A2A artifacts** | Opaque callee; caller sees Task + Artifact | Strongest across orgs | Cross-company |

Anthropic’s appendix: **write subagent output to a filesystem** and pass **references** to the lead — avoids the telephone game and the cost of copying large artifacts through the coordinator ([Research system](https://www.anthropic.com/engineering/multi-agent-research-system)). OpenAI `as_tool` nested runs **do not inherit** parent conversation state unless you pass the same `session` ([tools](https://openai.github.io/openai-agents-python/tools/)).

### 1.5 Orchestrator-worker (Anthropic Research, Magentic-One) vs CrewAI hierarchical vs CAMEL vs GroupChat

**Anthropic Research (published 2025-06-13).** Orchestrator-worker, not a chat swarm. LeadResearcher writes a plan to **Memory** because a 200k window will truncate. It spawns specialized Subagents with: objective, output format, tool list, stop boundary. Subagents search in **isolated context windows**, call **3+ tools in parallel**, return **condensed summaries**. Lead decides whether to spawn another wave. A separate **CitationAgent** attributes claims to URLs. Official numbers: multi-agent Opus-lead + Sonnet-subs **+90.2%** vs single-agent Opus 4 on an **internal** research eval; token usage alone explains **80%** of BrowseComp variance (tool-call count + model choice are the other two factors in a three-factor model covering **95%**); agents use **~4×** chat tokens; multi-agent **~15×** chat; parallel 3–5 subagents × 3+ tools cut research wall-clock **up to 90%**. Coding is a **poor** fit today (few truly parallelizable subtasks; agents are weak at real-time coordination). Early failure: lead spawned **50 subagents** for simple queries; vague “research the semiconductor shortage” caused three subs to duplicate 2025 supply-chain search while one wandered into 2021 auto chips. Mitigation: scale-effort rules (simple: **1** agent, **3–10** tool calls; comparison: **2–4** subs, **10–15** calls each; complex: **>10** subs with disjoint responsibilities). Better MCP tool descriptions **−40%** completion time. Execution today is **synchronous** waves: the lead cannot steer in-flight subs ([Research system](https://www.anthropic.com/engineering/multi-agent-research-system)).

**Magentic-One (Fourney et al., arXiv:2411.04468).** Outer loop: **Task Ledger** (facts, guesses, plan). Inner loop: **Progress Ledger** (is it done? looping? progress? who next? instruction?). Stall detector: paper threshold **≤2**; AutoGen `MagenticOneGroupChat` default `max_stalls=3`, `max_turns=20`. Workers are **tool-shaped**, not domain-shaped: WebSurfer, FileSurfer, Coder, ComputerTerminal. Ablations on GAIA validation: **removing full ledgers −31%**; removing any one worker **−21%** (Coder/Executor) to **−39%** (FileSurfer). Published task-completion (GPT-4o era, tests 2024-08..10, leaderboards as of 2024-10-21): **38% GAIA**, **32.8% WebArena**, **27.7% AssistantBench** (exact match). GPT-4o+o1-preview improved GAIA more than AssistantBench; o1 **refused 26%** of WebArena Gitlab tasks and **12%** of Shopping Admin — a “smarter” orchestrator model can **shrink** coverage ([paper](https://arxiv.org/abs/2411.04468); [AgentChat](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html)). Microsoft (2026): AutoGen is **maintenance mode**; new work should use **Microsoft Agent Framework 1.0** (sequential, concurrent, handoff, group chat, Magentic — all with streaming, checkpointing, HITL, pause/resume). Magentic remains the least hand-wired: goal + manager + specialists; manager owns plan/assign/stall/replan ([MAF 1.0](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/); [Magentic orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic)).

**CrewAI.** `Process.sequential` (default): task list order. `Process.hierarchical`: **requires** `manager_llm` or `manager_agent`; tasks are **not** pre-assigned; manager plans, delegates, validates. Manager **must not** sit in `agents=` and **must not** hold ordinary tools (source raises). `allow_delegation=True` on a custom manager is necessary but **not sufficient** — “coworker not found” is a recurring class of bugs when the delegation tool is populated with the manager’s own role or when `task`/`context` are dicts instead of strings ([hierarchical](https://docs.crewai.com/edge/en/learn/hierarchical-process); [crews](https://docs.crewai.com/edge/en/concepts/crews); [crew.py](https://github.com/crewAIInc/crewAI/blob/main/lib/crewai/src/crewai/crew.py); [#1823](https://github.com/crewAIInc/crewAI/issues/1823); [#1399](https://github.com/crewAIInc/crewAI/issues/1399); [#2606](https://github.com/crewAIInc/crewAI/issues/2606)).

**CAMEL (Li et al., NeurIPS 2023).** Role-playing: AI User instructs, AI Assistant executes, optional task-specifier. Inception prompting + `<CAMEL_TASK_DONE>`. Observed failure modes that are still production-relevant: **role flipping**, assistant repeating instructions, flake replies, **infinite thank-you loops** (agents can *know* they are looping and still fail to stop). Runtime fuses: user no-instruct for **3** rounds; assistant emitting `Instruction:`; repeat “goodbye/thank you”; max messages ([arXiv:2303.17760](https://arxiv.org/abs/2303.17760); [repo](https://github.com/camel-ai/camel)).

**AutoGen / AG2 GroupChat.** Broadcast every utterance to all members; speaker selection `auto` (LLM), `round_robin`, `random`, `manual`, or `allowed_or_disallowed_speaker_transitions`. Cost: **N−1 extra context injections per turn**. `allow_repeat_speaker` and the transition graph are mutually exclusive ([GroupChat](https://docs.ag2.ai/latest/docs/user-guide/advanced-concepts/groupchat/groupchat/); [groupchat.py](https://github.com/ag2ai/ag2/blob/main/autogen/agentchat/groupchat.py)). AG2 Network (2026): Hub owns registry, WAL, audit; typed channels `conversation`, `consulting` (one-question-one-reply, auto-close), `discussion` (round-robin), `workflow` (`TransitionGraph`). HumanClient is a first-class HITL participant ([Network overview](https://docs.ag2.ai/docs/user-guide/network/overview/)).

### 1.6 Consensus: voting, debate, majority, judge, disagreement thresholds

Consensus is **not** a topology. It is a **join function** you attach to a fan-in: `Send` workers, debate rounds, or self-consistency samples. The runtime — not the model — must decide: accept, continue another round, or **escalate**.

| Join | Mechanism | Token shape | Known numbers | Failure |
| --- | --- | --- | --- | --- |
| **Self-consistency / majority** | Sample k paths, vote on parsed answer | k × one CoT | PaLM-540B GSM8K greedy CoT **56.5%** → majority **74.4%** (+17.9 pp) at **k=40**; unweighted vote ≈ length-normalized weighted sum because path probs are similar ([Wang et al. ICLR 2023](https://arxiv.org/abs/2203.11171)) | Correlated errors lock a wrong majority |
| **Multi-agent majority (no debate)** | Independent agents, vote once | N × one generation | Du et al.: arithmetic **69.0%** vs single **67.0%** — weak ([Du et al.](https://arxiv.org/abs/2305.14325)) | Same as above |
| **Multi-agent debate (Du 2023)** | N agents propose, then critique others for R rounds; majority if still split | ≈ N × (R+1) × context | **3 agents × 2 rounds**: arithmetic **81.8%** vs reflection **72.1%** vs majority **69.0%**; GSM8K **85.0%** vs **75.0 / 81.0**; biographies **73.8** vs **66.0**; MMLU **71.1** vs **63.9**. Convergence is **empirical**, not guaranteed. Stubborn prompts → longer debates, better answers. Summarizing other agents’ replies helps at N≥5 ([Du et al. ICML 2024](https://proceedings.mlr.press/v235/du24e.html)) | Agreeable RLHF agents collapse disagreement; shared hallucination survives |
| **MAD + judge (Liang et al. EMNLP 2024)** | Two debaters, tit-for-tat; judge in **discriminative** mode (stop when a solution exists) or **extractive** at round cap | 2 × rounds + judge | Adaptive break + modest disagreement required; LLM judges are **unfair** if debaters use different models ([arXiv:2305.19118](https://arxiv.org/abs/2305.19118)) | Judge self-preference (Zheng bias family) |
| **Mixture-of-Agents (Wang et al. ICLR 2025)** | Layered: each layer reads prior layer outputs | layers × width | Open-source MoA **65.1%** AlpacaEval 2.0 LC vs GPT-4o **57.5%** ([arXiv:2406.04692](https://arxiv.org/abs/2406.04692)) | Latency = sequential layers |
| **LLM-as-judge (Zheng et al. 2023)** | Pairwise or rubric score | 1–2 judge calls (swap positions) | GPT-4 judge **>80%** agreement with humans, matching human–human; position, verbosity, self-enhancement biases ([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)) | Verbosity wins; same-family bias |
| **Anthropic research judge** | One LLM call, 0.0–1.0 + pass/fail on factuality/citation/completeness/source quality/tool efficiency | 1 call | Multiple judges were **worse**; one judge aligned better with humans ([Research system](https://www.anthropic.com/engineering/multi-agent-research-system)) | Rubric injection |

**Disagreement as a first-class signal, not a bug.** Du: agents **omit** facts they disagree on — useful for biographies, lethal if the omitted fact was the true one. Estornell & Liu (NeurIPS 2024): **tyranny of the majority** — similar models / similar responses yield static debate that converges to the majority, including a **shared misconception** from overlapping pretraining. Diversity pruning (keep k=5 distinct responses) reduces the echo chamber ([Estornell & Liu](https://proceedings.neurips.cc/paper_files/paper/2024/hash/32e07a110c6c6acf1afbf2bf82b614ad-Abstract.html)). Minority Sentinel (2026): majority is already correct in **74.5%** of divergent cases; overturn only when a calibrated P(minority-truth) exceeds a per-dataset τ with a **≥95%** majority-correct preservation constraint ([arXiv:2606.29270](https://arxiv.org/html/2606.29270)). Voting-ensemble abstention (arXiv:2510.04048): raising the vote threshold can lift precision (example: **73.1% → 93.9%** with two models on one domain) at the cost of yield — the production mapping is **abstain → escalate**, not “guess anyway.” Selective self-reference for LLM-as-judge: 3/5 agreement → **35.5%** majority correctness; 4/5 → **57.7%**; 5/5 → **78.7%** on that paper’s task — τ is **task-specific**, not a universal constant.

**Production join policy [inferred productization of the papers]:**

1. Parse votes into a closed answer set (schema, not prose).
2. If agreement ≥ τ_high (often unanimous or k−1) **and** answers are independently checkable → accept.
3. If agreement < τ_low → **do not average**. Escalate to a judge with **swapped positions**, then to a human if the judge also disagrees or if the domain is irreversible (AISVS 9.2.10).
4. Never let the same model family both propose and judge without a position-swap + second family, or you re-introduce Zheng self-enhancement.

Cite **04** for CoT-SC as a *single-agent* hybrid (ReAct → CoT-SC when the step cap fires). Multi-agent debate is the **cross-instance** version of that vote.

### 1.7 Human-in-the-loop (interrupt vs approval queues)

HITL is **not** “ask the model to be careful.” It is a **durable wait** with an authenticated resume. Primitive tables for `interrupt()` vs `interrupt_before` and node-restart rules: **05**. Multi-agent additions:

| Mechanism | Pause | Resume | Durable wait? | Multi-agent note |
| --- | --- | --- | --- | --- |
| LangGraph `interrupt()` | GraphInterrupt | `Command(resume=…)` + checkpointer | Only if Agent Server / Temporal, not a laptop `invoke()` | Interrupt **inside a worker** must propagate to the parent; `langgraph-supervisor` migration docs: interrupt in a subagent tool still surfaces on the outermost graph ([migrate](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor)) |
| `HumanInTheLoopMiddleware` | After model, before listed tools | Decisions: `approve` / `edit` / `reject` / `respond` | Same checkpointer rule | Per-tool `interrupt_on`; missing key = **auto-approve**. `when` predicate gates on args ([HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop); [`HumanInTheLoopMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/human_in_the_loop/HumanInTheLoopMiddleware)) |
| OpenAI `needs_approval` | `result.interruptions` | `state.approve()/reject()` + same session | Process-held unless you persist `RunState` | Nested `as_tool` and post-handoff approvals **still surface on the outer run**. Resume the **top-level** agent. Default `_max_turns=10` is **not** paused by wall-clock HITL — a long approval can still raise `MaxTurnsExceeded` ([HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/); [`RunState`](https://openai.github.io/openai-agents-python/ref/run_state/)) |
| A2A `INPUT_REQUIRED` / `AUTH_REQUIRED` | Task interrupted | Client `SendMessage` on same `taskId` | Yes, by spec | Client-agent may **re-delegate** `AUTH_REQUIRED` up a chain of tasks ([spec](https://a2a-protocol.org/v1.0.0/specification/)) |
| Temporal Signal + `wait_condition` | Workflow parks (zero compute) | Signal | Yes | Documented approval pattern: wait with timeout → escalate → second wait → auto-reject ([approval](https://docs.temporal.io/design-patterns/approval); [HITL cookbook](https://docs.temporal.io/ai/cookbook/human-in-the-loop-python); [LangGraph plugin](https://docs.temporal.io/develop/python/integrations/langgraph)) |
| CrewAI / AG2 `manual` speaker / `HumanClient` | Console / UI | Human types | No unless you add a Hub WAL | Not an SLA |

LangGraph **does not** put a TTL on `interrupt()`. Threads wait until resume or you add an **external** timer (Temporal, queue TTL, Agent Server sweeper). AISVS **9.6.2**: if the approval time is not met, **block** the pending action — timeout-deny, not timeout-allow ([AISVS C9](https://github.com/OWASP/AISVS/blob/main/1.0/en/0x10-C09-Orchestration-and-Agentic-Action.md)). Node restart means “send email then interrupt” is a footgun unless the send is behind an idempotent `@task` (**05**).

**Approval queues (enterprise, not a LangGraph primitive).** Treat `__interrupt__` / `ToolApprovalItem` as **messages on a work queue**: ticket id = `thread_id` + interrupt id; SLA timer outside the graph; capacity limit; overflow policy (shed new low-priority interrupts, never auto-approve high-impact). OWASP Agentic Top 10 **ASI09** (Human-Agent Trust Exploitation): HITL is an **attack surface** (automation bias, authority deference, confirmation fatigue). Mitigations: friction-by-design for irreversible actions, approval-budget per session, structured risk badges, out-of-band confirm ([OWASP Top 10 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)). EU AI Act Art. 14 is the legal twin; it does not specify the UI ([EU 2024/1689 Art. 14](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)). AISVS **9.2.2**: show canonicalized full parameters (diffs, amounts, recipients) — not a chat paraphrase the supervisor invented.

### 1.8 Escalation: SLA, confidence, cost, security → human or specialist

Escalation is a **control-plane edge**, not a prompt. Destinations: another **specialist** (OpenAI-style `handoff` with `input_type=EscalationData(reason=...)` + `on_handoff` audit row) or a **human** (`interrupt` / `needs_approval` / A2A `INPUT_REQUIRED`). Do not collapse them: a specialist hop is still an agent with tools; a human hop is a durable wait.

| Trigger | Detector (runtime, not LLM) | Typical action | Source |
| --- | --- | --- | --- |
| **SLA / hop budget** | Hop counter, `max_turns` (OpenAI default **10**; Magentic **20**), LangGraph `recursion_limit` (**04**/**05**) | Force `escalate_to_human` after N transfers; Magentic stall → replan not re-handoff | SDK defaults; AISVS 9.1.2 |
| **Confidence / disagreement** | Vote fraction < τ; judge score < threshold; Magentic “looping?” | Extra debate round **or** human; do not silently majority | Du; Estornell; §1.6 |
| **Cost** | Runtime $ / token counter | Cap subagents (Anthropic effort rules); AISVS 9.1.2 monetary budget | Anthropic; AISVS |
| **Security / reversibility** | Tool manifest class (read-only / reversible / irreversible); worst class in the **chain** wins (AISVS **9.2.10**) | HITL before write tools; `AUTH_REQUIRED` for user gesture | AISVS C9.2 |
| **Capability miss** | Router/supervisor cannot bind a skill; A2A `REJECTED` | Specialist handoff or human | OpenAI orchestration guide |
| **HITL timeout** | Durable timer | **Block** (9.6.2); optional escalate-to-manager then second timer then auto-reject | Temporal approval pattern |

OpenAI: `is_enabled` hides refund unless `order_id` in state; `EscalationData` logs reason **before** the escalation agent speaks. A2A: `TASK_STATE_AUTH_REQUIRED` / `INPUT_REQUIRED` as protocol interrupts. Classification of reversibility must live in the **tool manifest**, not in the agent’s self-description (AISVS 9.2.3–9.2.4).

**Cost-based escalation** is a budget check on the **control plane** (AISVS 9.1.2), not “the lead decides it has spent enough.” Implement: remaining_usd on the thread; each worker Activity records actual tokens; crossing 80% of the ticket budget **forbids** new `Send` / handoffs and routes to a cheap summarizer or a human. Anthropic’s prompt-side effort rules (1 vs 2–4 vs >10 subs) are the **soft** twin; without the hard cap you get Loop D.

**Security-based escalation** is a **class promotion**: if any hop in the chain is irreversible, the whole chain requires the irreversible gate (AISVS **9.2.10**). A FAQ swarm that later calls `issue_refund` must not inherit FAQ’s auto-approve. Downscope cannot be reversed by the specialist asking the supervisor to “just run it.”

### 1.9 A2A as the inter-process agent bus (cite 08 for MCP)

A2A originated at Google; donated to the Linux Foundation; TSC includes AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP, ServiceNow. Spec **1.0.0** is the first production-stable version. Normative model is `spec/a2a.proto` ([home](https://a2a-protocol.org/latest/); [spec](https://a2a-protocol.org/v1.0.0/specification/); [LF donation](https://developers.googleblog.com/google-cloud-donates-a2a-to-linux-foundation/)). Complementary to MCP by design — **do not flatten a partner agent into `tools/call`** if you need their orchestration opacity ([A2A and MCP](https://a2a-protocol.org/latest/topics/a2a-and-mcp/)). MCP depth (08); A2A reach.

Task states: `SUBMITTED`, `WORKING`, `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`, `INPUT_REQUIRED`, `AUTH_REQUIRED`. Blocking `SendMessage` (`returnImmediately=false`, default) waits until terminal or interrupted. **Task immutability:** terminal tasks never restart; refinements create a **new** `taskId` in the same `contextId`, optionally with `referenceTaskIds`. Parallel follow-ups are first-class (flight + hotel + activity as sibling tasks). Artifact mutation tracking is **client-side** (same `artifact-name`, new `artifactId`) ([life of a task](https://a2a-protocol.org/latest/topics/life-of-a-task/)). Auth: OpenAPI-style `securitySchemes` (API key, HTTP, OAuth2, OIDC, **mTLS**); skill-level `securityRequirements`; signed cards (JWS). Extended Agent Card is auth-gated.

---

## 2. Token Economics & NFR Metrics

### 2.1 Published multipliers (do not invent others)

| Source | Claim | Caveat |
| --- | --- | --- |
| Anthropic Research | Chat → agent **~4×** tokens; chat → multi-agent **~15×**; token use explains **80%** of BrowseComp variance; **+90.2%** vs single Opus 4 on **internal** research eval; parallelization **≤90%** wall-clock; better MCP descriptions **−40%** completion time | Internal eval; 2025 Opus 4 / Sonnet 4 generation |
| LangChain multi-agent docs | Call/token table in §1.3 (4 vs 3 calls; 9K vs 14K vs 15K) | Pedagogical “buy coffee” / 2k-doc specialists |
| Magentic-One | GAIA **38%**, WebArena **32.8%**, AssistantBench **27.7%**; ledger ablation **−31%**; worker ablation **−21..−39%** | GPT-4o / o1-preview, 2024 runs |
| Du debate | 3×2 debate beats majority and reflection on six tasks (Table 1–2 in §1.6) | chatGPT-era; debate is **more costly** by design |
| Wang self-consistency | **k=40** paths, +17.9 pp GSM8K | Single model, not multi-agent IAM |
| MoA | 65.1% vs 57.5% AlpacaEval 2.0 LC | Layered sequential latency |

⚠️ **p50/p95/p99:** no vendor publishes agent-loop latency percentiles for supervisor-worker systems as of 2026-09-23. Bound them from architecture (**§2.5**). Temporal waiting for HITL is **not** a latency SLO (parked workflows consume no worker CPU).

### 2.2 Official token SKUs used below (cite 01; 2026-09-23)

Do not recopy the full 01 table. Working set: Claude Sonnet 5 **$2 / $10** per MTok (cache hit **$0.20**); Claude Opus 5 **$5 / $25** (cache **$0.50**); GPT-5.4 **$2.50 / $15** (cache **$0.25**) ([01](01-python-llm-foundations.md); [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing); [GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)).

### 2.3 `$ per 1k tickets` — supervisor + 2 specialists **[inferred]**

All figures are **model tokens only** (no web-search SKU, no Managed Agents session-hour, no LangSmith seat). Reference call: **2,000 input + 400 output** (short tool-using turn). Sonnet 5: \(2000\times\$2 + 400\times\$10\) per 1M = **$0.008 / call**.

**Loop S — L1 ticket, supervisor + 2 specialists (calendar + email, or billing + refund).**

| Pattern | Calls (LangChain table) | $/ticket | **$/1k tickets** |
| --- | --- | --- | --- |
| Subagents (supervisor join) | 4 | $0.032 | **$32** |
| Handoffs / sticky specialist | 3 | $0.024 | **$24** |
| Router + parallel two specialists + synth | 5 (multi-domain analogue) | $0.040 | **$40** |
| Same 4-call subagents, GPT-5.4 | 4 × (2000×$2.50 + 400×$15)/1e6 = $0.011/call | $0.044 | **$44** |
| Repeat turn 2, handoffs | 2 | $0.016 | **$16 extra** |
| Repeat turn 2, subagents | 4 | $0.032 | **$32 extra** |

Coordination tax of “always return to supervisor” is **+$8 / 1k / turn** on this loop vs a sticky specialist **[inferred]**. That tax **buys** centralized policy and a single user-facing owner.

**Loop R — Anthropic 15× research.** Chat baseline 2,000 in + 500 out on Sonnet 5 = **$0.009 / chat**. Single-agent research **4×** = **$0.036** → **$36 / 1k**. Multi-agent **15×** = **$0.135** → **$135 / 1k**. Mix **30% Opus 5 + 70% Sonnet 5** on the 15× pile **[inferred]**: ~**$240 / 1k**. Anthropic: task value must exceed this; they do not publish a break-even. Claude web search **$10 / 1K searches**: 3 subs × 8 searches = **$0.24 / task** — often **larger than Sonnet tokens** on Loop S. Count it.

**Loop D — fan-out catastrophe.** 50 subagents × 10 calls × $0.008 = **$4 / ticket** → **$4,000 / 1k** plus the lead. AISVS 9.1.2 is an NFR, not a nice-to-have.

### 2.4 Debate extra tokens **[inferred from Du’s 3×2 skeleton]**

Assume each debate utterance is **1,500 in + 400 out** on Sonnet 5 = **$0.007 / utterance**.

| Consensus recipe | Utterances | Extra vs 1 greedy CoT | **$/1k extra** |
| --- | --- | --- | --- |
| Majority N=3, no debate | 3 | 2 | **$14** |
| Du debate 3 agents propose + 2 critique rounds | 3 + 6 = **9** | 8 | **$56** |
| MAD 2 debaters × 4 rounds + judge each round (adaptive, say 3) | ~2×3 + 3 = **9** | 8 | **$56** |
| Self-consistency k=5 (not 40) | 5 | 4 | **$28** |
| Self-consistency k=40 (Wang) | 40 | 39 | **$273** |

Use debate as a **verifier role** on high-value, non-parallelizable answers (legal memo, medical differential), not as a default topology. Combine with Anthropic’s CitationAgent: debate on claims, then a citation pass — two different workers. MoA layers are sequential: p99 adds **per layer**, unlike `Send` fan-out.

### 2.5 Latency: sequential vs parallel specialists

No published p99. Architecture bounds:

| Pattern | p99 (conceptual) | When it hurts |
| --- | --- | --- |
| Sequential pipeline / swarm handoff | Σ stage p99 | Sticky support is OK; three-domain research is not |
| Supervisor, `parallel_tool_calls=False` (default) | Σ worker p99 + supervisor ticks | Default `create_supervisor` **serializes** workers |
| Parallel `Send` / Anthropic wave | max(worker p99) + join + lead | Vague briefs duplicate work; join waits on the slowest |
| Debate / MoA layers | rounds × (max speaker or sequential layer) | Verification tax on the critical path |
| A2A blocking | inherits callee p99 + auth | Cold Agent Card / OAuth dominates LLM time |
| HITL | **not** an LLM SLO | Parked wait; measure **decision latency** separately |

Anthropic: two kinds of parallelization (3–5 subs, 3+ tools each) cut research time **up to 90%**. They also state the current Research product is **synchronous** — the lead cannot mid-course-correct a wave. M1-Parallel (arXiv:2507.08944) claims **up to 2.2×** with early termination — multiplies **cost** unless cancelled. Openlayer 2026 commentary (not a lab result): supervisor-style parallelism helped some Google-reported parallel tasks (~**80%**) and **hurt** sequential reasoning (~**70%**). Direction matches Anthropic: **do not multi-agent a tightly coupled chain**.

GPT-5.4 Fast mode published **99% of 5-min windows > 50 tok/s** is a **decode** SLO, not a multi-agent loop SLO ([01](01-python-llm-foundations.md); [Fast mode](https://openai.com/api-fast-mode/)).

**Worked p99 envelope for Loop S [inferred, not measured].** Assume independent specialist stages with published-style decode plus tool I/O: supervisor route **800 ms p99**, specialist (LLM+tools) **3.5 s p99**, join/synth **1.2 s p99**. Sequential supervisor→S1→S2→join: **800+3500+3500+1200 ≈ 9.0 s**. Parallel `Send` of S1∥S2: **800+3500+1200 ≈ 5.5 s**. Swarm sticky turn 2 (no router): **3.5 s**. Debate 3×2 with sequential speakers adds **~6 × 2.0 s ≈ 12 s** of extra wall-clock if not parallelized. HITL is orthogonal: a 15-minute approval SLA is a **queue SLO**, not added to these numbers. If your specialists are **not** independent (S2 needs S1’s artifact), parallelization is a bug, not a speedup — use a typed handoff of the artifact id, then S2.

Throughput: provider RPM/ITPM live in **01**. Multi-agent multiplies **in-flight** requests: a supervisor + 2 parallel specialists is **3 concurrent completions per ticket** during the wave. Scale-tier Anthropic Start OTPM can bind before CPU. Admission-control the `Send` length, not just RPM per worker.

### 2.6 Coordination overhead and cache

What you actually pay extra for: (1) router/supervisor tokens — 1 extra call per hop; (2) history splicing — `full_history` vs `last_message`; missing `input_filter` copies every tool payload; (3) duplicate work from vague briefs; (4) join/synthesize (CitationAgent, Magentic final-answer, LangChain synthesizer); (5) retries without a fleet-level breaker; (6) protocol wrappers (Agent Card, OAuth) — usually << LLM $ but dominate **p99** if the callee is cold.

Cache: supervisor system prompt + worker playbooks should be **prompt-cached**. Sonnet 5 cache hit **$0.20 / MTok** vs **$2** is a **10×** input discount for the static prefix (**01**). Hierarchical supervisors with shared team prompts are the best cache shape; swarms that rewrite `active_agent` prompts every hop cache worse. Prefix-stability rules: **02**.

---

## 3. Distributed Resilience & State

### 3.1 Supervisor as SPOF

Symptoms: p99 ≈ lead think time + max(slowest worker); lead context fills with summaries; cannot steer in-flight subs (Anthropic: **synchronous** waves). Hierarchical: top-level waits on entire teams. Magentic inner loop assigns **one** worker — slower but bounded.

Mitigations: effort-scaling in the prompt **and** a hard cap in the **runtime** (prompts are advisory); Haiku/cheap **router** + Opus **lead** only when a complexity score fires; `last_message` + filesystem artifacts; A2A/Temporal **async** tasks with progress; split citation to async post-process; Magentic `max_stalls` then **replan**, not re-handoff.

A dead supervisor with `parallel_tool_calls=False` stalls the whole ticket. Subgraph isolation (**05**) lets a **team** fail without poisoning the parent checkpointer **if** the parent does not join on that team’s channel. A2A: one remote `FAILED` does not kill `contextId`; spawn a refinement task.

### 3.2 Subgraph isolation and worker failure

| Failure | Supervisor-worker (sync wave) | Handoff swarm | A2A remote | Hierarchical team |
| --- | --- | --- | --- | --- |
| One worker 500s | Whole wave blocks | Conversation stuck on that agent | Task `FAILED`; context continues | Other teams proceed if top-level does not join |
| Infinite tool loop | `max_turns` / AISVS budget | Same | Server-side timeout | Team-level `max_turns` |
| Poisoned context | Isolated if sub has own window (Anthropic win) | **Contaminates** sticky history | Opaque — callee’s problem; you see artifacts | Team checkpointer isolates |
| Kill one worker identity | Remaining workers + replan | Need a handoff off the dead agent | New Agent Card version | Replace compiled subgraph |

Anthropic: tell the model the tool is failing — it adapts. Necessary and insufficient; pair with Activity retries and a breaker so the lead is not spending Opus tokens narrating a dead search API. Rainbow deploys so in-flight graphs are not cut over mid-plan ([Research system](https://www.anthropic.com/engineering/multi-agent-research-system)). Durable mapping of graph nodes → Temporal Activities: **05** + Temporal LangGraph plugin — LLM calls must **not** re-run on workflow replay.

### 3.3 HITL timeout, deadlock, hop caps

**HITL timeout.** LangGraph interrupt = infinite wait. Production: Temporal `wait_condition(..., timeout=)` → escalate to manager (notify Activity) → second timeout → **auto-reject**. AISVS 9.6.2 = block, not proceed. OpenAI: serialize `RunState`; do not let `_current_turn` burn the default 10 turns while a human is at lunch — raise `max_turns` or `None` for approval-gated runs, and put the **SLA** on the queue, not on the model loop.

**Deadlock patterns unique to multi-agent:**

1. Reciprocal handoffs (`transfer_to_sales` ↔ `transfer_to_support`) with no hop cap.
2. GroupChat `auto` speaker with a fully connected graph and `allow_repeat_speaker=True` — no progress predicate (Magentic’s “are we looping?” is the missing control).
3. Parallel `Send` workers waiting on each other’s unwritten keys (no reducer / cyclic data dependence).
4. A2A client blocking on `AUTH_REQUIRED` while the human queue is full — **queue overflow**, not a graph cycle.
5. CAMEL thank-you loop — agents aware, still stuck; fuse on repeat tokens.

Mitigations: `is_enabled` predicates; hop counter in state; after **N** transfers, force human; Magentic stall counter; AG2 `allowed_or_disallowed_speaker_transitions`; AISVS **9.1.3** swarm kill-switch (out-of-band, **9.6.3**).

Circuit breakers: Temporal RetryPolicy is **not** a breaker (05/04 retry tables). Fleet-level consecutive-failure counter per provider; 429/500 retry; 400/401/422 **do not** retry. CrewAI “coworker not found” is a **control-plane** page, not an LLM retry.

### 3.4 A2A as a distributed state machine

Treat remote agents as **sagas with a public state enum**. `AUTH_REQUIRED` is an interrupt, not an error. Terminal tasks are **immutable** — do not implement “restart completed task”; spawn a refinement in the same `contextId`. List-tasks **must** be authorization-scoped. Push-notification capability must be declared. Parallel sibling tasks (hotel + activity) need client-side artifact version maps.

---

## 4. Enterprise Security & Governance

### 4.1 Confused deputy between agents

Two layers. **OAuth proxy deputy** (MCP spec, **08**): static IdP `client_id` + DCR + consent cookie. **Agent deputy (this file):** supervisor has GitHub admin; user asks a worker to “update the README”; worker issues a tool call that the supervisor **executes with supervisor credentials**. CSA research note (2026-03): most documented multi-agent architectures **propagate authority implicitly** — sub-agents inherit orchestrator permissions or receive credentials in the invocation. A single injection into the orchestrator becomes **lateral authority propagation** across every worker ([CSA confused deputy](https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/03/CSA_research_note_ai-agent-confused-deputy-prompt-injection-chains_20260323-csa-styled.pdf)). Magentic-One appendix: a login misconfig led agents to **reset the account password** after lockout — alignment that works on a single turn failed across a multi-agent escalation chain; they also flag **crescendo**-style multi-turn jailbreaks as more dangerous when one agent’s compliance teaches the next ([arXiv:2411.04468](https://arxiv.org/abs/2411.04468)).

Fix: **downscope at handoff** — `on_handoff` mints a token whose audience is the worker’s MCP servers and whose scopes match the brief. A2A `AUTH_REQUIRED` when the callee needs a user gesture. Never “the lead calls all tools on behalf of workers.” AISVS **9.5.1** per-agent tool+parameter policy; **9.5.2** integrity-protected, scope-limited user token at every hop; **9.5.3** policy engine, never the model; **9.5.5** explicit inter-agent delegation policy; **9.4.1** cryptographic agent identity. MCP **MUST NOT** passthrough the client token (**08**).

### 4.2 Privilege inheritance and Zero-Trust per-agent allowlists

| Principal | May | Must not |
| --- | --- | --- |
| Router / lead | Spawn workers, read summaries, write plan Memory | Hold production write tools (Stripe, email send) |
| Domain specialist | Its tool allowlist | Other specialists’ tools; raw user refresh tokens |
| Citation / critic / judge | Read artifacts | Mutate source systems |
| Human approver | Approve/reject high-impact | Be the only audit trail (ASI09) |
| A2A callee | Skills on its Agent Card | Your VPC except via published artifacts |

CrewAI/LangGraph “give the manager all tools so it can help” **destroys** isolation. Hierarchical IAM: team supervisor has **delegation** rights, not **union of worker tools**. A2A skill-level `securityRequirements` is the protocol’s RBAC hook. Extended Agent Cards hide sensitive skills until authenticated. `HumanInTheLoopMiddleware`: tools **absent** from `interrupt_on` auto-approve — that is a privilege bug if a new write tool is registered and forgotten.

Zero-Trust: short-lived, per-agent, per-session credentials. Least privilege on **every** MCP `tools/call` (**08**). AISVS **9.5.4**: secrets not in the model-observable context (forum: `Runtime.context` / `UntrackedValue`, not checkpointed state — **05**).

### 4.3 PII in handoff payloads

Every extra hop is a **copy**. Subagents with isolated windows are **better** for PII minimization if the brief strips identifiers and the sub returns aggregates. OpenAI default handoff passes **full history** — prior-turn PII lands in the refund agent. Filters: `input_filter`, LangChain “pass only the handoff pair,” Anthropic filesystem refs. Nested handoff history can **re-embed** tool args in summaries. Blackboards are **worse** unless partitioned. A2A artifacts may be files — classify before crossing org boundaries. Handoffs that **filter history** must log **what was dropped** (hash of omitted items), or IR cannot reconstruct why the specialist lacked context.

### 4.4 Audit of delegation

Minimum viable audit row (append-only):

`timestamp, trace_id, parent_span, from_agent, to_agent, mechanism (handoff|as_tool|A2A|Send|vote), input_type metadata, principal_id, token_jti, tools_enabled, policy_version, human_gate, artifact_ids, vote_vector, disagreement_τ`

OpenAI: `handoff` spans; `on_handoff` for business metadata. LangSmith: graph node + tool spans. A2A: `taskId`+`contextId`+status transitions. Temporal: Event History **is** the audit. AG2 Network: Hub WAL. AISVS **9.4.2**: bind actions cryptographically along the chain.

### 4.5 OWASP Agentic Top 10 mapped here

Official list: [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/). Multi-agent systems concentrate **ASI07** Insecure Inter-Agent Communication (A2A/MCP without mTLS/audience; unsigned envelopes), **ASI08** Cascading Agent Failures (fan-out, ping-pong, retry storms), **ASI03** Identity/Privilege Abuse (delegation without downscope), **ASI09** HITL exploitation. AISVS C9 is the control catalog: budgets (9.1), kill-switch (9.1.3 / 9.6), approval manifests (9.2), timeout-deny (9.6.2), out-of-band kill (9.6.3), delegation policy (9.5.5).

---

## 5. Production Failure Modes

### 5.1 Ping-pong handoffs

**Symptoms:** `transfer_to_sales` ↔ `transfer_to_support`; hop count explodes; user sees “let me transfer you” loops; token burn without a final `AIMessage`.

**Causes:** overlapping prompts; reciprocal handoff tools always enabled; no `max_turns`; swarm without hop cap; CrewAI manager delegating to itself; CAMEL role flip (`Instruction:` in assistant output).

**Mitigations:** `is_enabled` predicates; hop counter; after **N** transfers, force human; OpenAI `max_turns=10`; Magentic `max_stalls` then replan; allowed-transition graph; disable parallel tool calls so two handoffs cannot fire in one tick.

### 5.2 Context loss at the boundary

**Symptoms:** specialist asks questions the user already answered; refund agent missing `order_id`; subgraph `InvalidUpdateError` / malformed tool-call pairing; A2A refinement that cannot find the artifact.

**Causes:** `input_filter` too aggressive; subgraph handoff missing `AIMessage`+`ToolMessage` pair ([handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs)); `output_mode=last_message` dropping the only fact the next team needed; A2A client not passing `referenceTaskIds` / `artifactId`; Anthropic telephone game through the lead.

**Mitigations:** typed `input_type` for **metadata** (reason, order_id) **plus** a brief; filesystem refs; log hashes of dropped items; parent reducers for `Command.PARENT` shared keys (**05**); A2A `INPUT_REQUIRED` when artifact identity is ambiguous ([life of a task](https://a2a-protocol.org/latest/topics/life-of-a-task/)).

### 5.3 Majority voting of a shared hallucination

**Symptoms:** three agents agree; answer is confidently wrong; debate “converges” in one round.

**Causes:** same base model, same prompt, overlapping pretraining (Estornell tyranny of the majority); Du’s agreeable RLHF agents; judge from the same family (Liang unfairness; Zheng self-enhancement); treating disagreement as a bug to be suppressed.

**Mitigations:** diversity pruning; cross-family proposers; position-swapped judge; τ_high / τ_low with **escalate on gray**; citation/tool checks that do not depend on the vote; Minority Sentinel before overturning a majority. **Do not** use debate to “confirm” a single retrieved claim that all agents saw.

### 5.4 HITL queue overflow

**Symptoms:** interrupt backlog grows; p99 decision time exceeds SLA; operators rubber-stamp (ASI09); OpenAI runs die with `MaxTurnsExceeded` while waiting; Temporal workflows pile up in `wait_condition`.

**Causes:** every tool on `interrupt_on=True`; no `when` predicate; no TTL; no admission control; supervisor paraphrases so humans cannot scan diffs (violates AISVS 9.2.2).

**Mitigations:** interrupt **write** tools only; `when` on amount / ACL; approval budget per session; structured cards; overflow = **shed or coarsen**, never auto-approve irreversible class; timeout-deny (9.6.2); separate **decision SLO** from **model SLO**.

### 5.5 Other named modes

| Mode | Source | Detection | Fix |
| --- | --- | --- | --- |
| 50 subs on a trivia question | Anthropic | Subagent-count metric | Effort rules + hard cap |
| Vague briefs → duplicate search | Anthropic | Overlap of query embeddings across subs | Brief template: objective, sources, **out of scope** |
| Telephone game through the lead | Anthropic appendix | Artifact hash ≠ cited content | Filesystem refs + CitationAgent |
| SEO-farm sources | Anthropic human eval | Source-quality rubric | Prompt heuristics + judge |
| Rainbow-unsafe deploys | Anthropic | In-flight graph schema mismatch | Dual-run old/new; pin prompt versions on the thread |
| o1/policy refusals shrink coverage | Magentic-One WebArena | Refusal rate by site | Don’t put the policy-heavy model on write tools |
| Password-reset spiral | Magentic-One appendix | Repeated auth failures | Circuit on auth tools; human on account mutation |
| Hierarchical “manager does all work” | CrewAI #1823/#1399 | `task.delegations==0`; coworker list = manager | Fix coworker injection; don’t trust YAML manager |
| GroupChat broadcast cost | AG2 Classic | Tokens ∝ N² | Network channels / supervisor |
| Guardrail gap on handoffs | OpenAI docs | Bypass via transfer | Policy at the **worker**; guardrails don’t wrap handoffs |
| Node re-executes send-email on resume | LangGraph interrupt rules | Duplicate side effects | Idempotent `@task` (**05**) |
| CAMEL thank-you loop | Li et al. 2023 | Repeat-token counter | `<CAMEL_TASK_DONE>` + max rounds |
| ASI09 rubber-stamp HITL | OWASP | Approval time <1s, high volume | Approval budgets, friction, structured diffs |

---

## 6. Enterprise System Design Scenarios

Decision rule used below: **start with one agent + skills**. Add a second agent only when (a) tool/policy isolation is a compliance requirement, (b) parallel isolated context is the product, or (c) two teams ship independently. OpenAI, LangChain, and Anthropic independently say the same thing ([orchestration](https://openai.github.io/openai-agents-python/multi_agent/); [multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent); [Research system](https://www.anthropic.com/engineering/multi-agent-research-system)).

### 6.1 Trade-off matrix

| Requirement | Single + Skills | Router + parallel | Supervisor-worker | Hierarchical | Swarm/handoff | A2A mesh | Debate/judge |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Lowest $ (Loop S) | **Best** | Good | Extra join call | Worst | Good on repeats | Protocol tax | High $ (§2.4) |
| Parallel breadth research | Weak | **Best** | **Best** if parallel tools | OK per team | Poor (sequential) | Good (parallel tasks) | Wrong tool |
| Sticky UX (support) | Good | Re-routes every turn | User talks to lead | Heavy | **Best** | `contextId` sticky | Poor |
| Team autonomy / IAM | Weak | Medium | Strong | **Strongest** | Medium | **Strongest** (opaque) | N/A |
| Cross-company | No | No | No | No | No | **Yes** | No |
| High-stakes factuality | Self-consistency k | Weak | CitationAgent | Nested judges | Weak | Callee’s problem | **Best** as verifier |
| HITL high-impact | Middleware interrupt | After join | Lead gate | Top-level gate | Easy to skip | `INPUT_REQUIRED` | Judge then human |
| Sequential coding | **Best** (Anthropic) | Harmful if oversplit | Harmful | Harmful | Harmful | Harmful | Harmful |

### 6.2 Scenario A — L1/L2 support with HITL escalation

**Choose:** OpenAI-style **triage handoff** (or LangChain middleware handoffs) to billing / refund / FAQ; **not** a research orchestrator. `handoffDescription` one sentence each. `input_filter=remove_all_tools`. `is_enabled` hides refund unless `order_id` in state. L1 owns the conversation (`active_agent`). L2 is a **specialist handoff** with `EscalationData(reason, priority)` logged in `on_handoff`. Human: `needs_approval` / `HumanInTheLoopMiddleware` on refund **write** tools; ASI09 friction (amount in a structured card). NFR: hop cap **3** → human; HITL SLA e.g. 15 min L1 / 4 h L2 with Temporal timeout-deny; cost Loop S handoff **~$24 / 1k** Sonnet 5 **[inferred]**; p95 dominated by the specialist, not triage. ⚠️ Measure your own p95.

**Avoid:** GroupChat of 8 personas; CrewAI hierarchical until delegation telemetry is green; spawning web-research subs for “where is my laptop”; majority vote on a refund decision (shared policy hallucination).

Escalation ladder: confidence (classifier < τ) → L2 specialist → write-tool HITL → security/PII → security agent with **narrower** tools, not the supervisor’s union set.

### 6.3 Scenario B — Parallel research agents + judge

**Choose:** Anthropic-shaped **orchestrator-worker**: Opus (or GPT-5.4) lead, Sonnet/Haiku subs, Memory plan, filesystem artifacts, **hard** subagent cap, effort rules. Parallel wave of 3–5. Join: **not** majority of free-form summaries. Use a **CitationAgent / judge** with a rubric (factuality, citation, completeness, source quality) — Anthropic: **one** judge 0–1 beat multi-judge. If judge score < τ or citations missing → one more wave **or** human analyst, never a silent majority. Token budget runtime-enforced (Loop R **~$135–240 / 1k** before search SKUs). Web search SKU can exceed tokens — cap searches. Deploy: rainbow + tracing of **structures** not contents.

Optional: Du-style debate **only** on the final contested claims (3×2 on the join set), not on every search hop. Estornell diversity pruning if proposers are the same family.

**Avoid:** Handoff swarm (cannot parallelize domains — LangChain 14K+ sequential). Skills-only (15K context sludge). Unbounded `Send`. Using the lead model as the only judge without position swap.

### 6.4 Scenario C — When **not** to multi-agent

- Tight sequential coding / refactor (Anthropic: agents not yet good at live delegation).
- <10 tools in one domain (LangChain: tool overload is the trigger; below that, split is net loss).
- Task value < Loop R cost (research-style 15×).
- You cannot name the **principal** for each worker’s writes.
- You cannot cap fan-out in **code**.
- You wanted consensus but all voters share one prompt and one corpus — that is self-consistency, not a second team.

### 6.5 Interview control-plane checklist

1. Who owns the user-visible token after hop 1? (handoff vs as_tool vs supervisor join)
2. Where is the hop cap / $ cap enforced? (runtime > prompt)
3. What identity is on the wire for worker writes? (downscoped token)
4. Shared or partitioned state — what did `input_filter` drop, and is that hashed in the audit?
5. Join function: majority, judge, or escalate? What is τ?
6. HITL: timeout-deny, approval budget, ASI09 friction, queue overflow policy?
7. Escalation: SLA vs confidence vs cost vs security — four different edges
8. MCP vs A2A: which bus is this hop on? (08 vs this file)
9. Dead worker: fail closed without killing the saga?
10. Deploy: can an in-flight graph survive a prompt change (rainbow / pin)?

If the candidate cannot answer (3), (5), and (6), they have a demo, not a system.

---

## Sources

1. https://docs.langchain.com/oss/python/langchain/multi-agent
2. https://docs.langchain.com/oss/python/langchain/supervisor
3. https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs
4. https://docs.langchain.com/oss/python/langchain/multi-agent/skills
5. https://docs.langchain.com/oss/python/langchain/multi-agent/router
6. https://docs.langchain.com/oss/python/migrate/langgraph-supervisor
7. https://docs.langchain.com/oss/python/langchain/human-in-the-loop
8. https://docs.langchain.com/oss/python/langgraph/interrupts
9. https://docs.langchain.com/oss/python/langgraph/graph-api
10. https://reference.langchain.com/python/langgraph-supervisor/supervisor/create_supervisor
11. https://github.com/langchain-ai/langgraph-supervisor-py
12. https://reference.langchain.com/python/langgraph-swarm/handoff/create_handoff_tool
13. https://github.com/langchain-ai/langgraph-swarm-py/blob/main/langgraph_swarm/handoff.py
14. https://reference.langchain.com/python/langgraph-swarm/swarm/create_swarm
15. https://reference.langchain.com/python/langchain/agents/middleware/human_in_the_loop/HumanInTheLoopMiddleware
16. https://www.langchain.com/blog/choosing-the-right-multi-agent-architecture
17. https://www.langchain.com/blog/benchmarking-multi-agent-architectures
18. https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt
19. https://openai.github.io/openai-agents-python/handoffs/
20. https://openai.github.io/openai-agents-python/multi_agent/
21. https://openai.github.io/openai-agents-python/tools/
22. https://openai.github.io/openai-agents-python/human_in_the_loop/
23. https://openai.github.io/openai-agents-python/running_agents/
24. https://openai.github.io/openai-agents-python/ref/run_state/
25. https://developers.openai.com/api/docs/guides/agents/orchestration
26. https://developers.openai.com/api/docs/guides/agents/guardrails-approvals
27. https://docs.crewai.com/edge/en/learn/hierarchical-process
28. https://docs.crewai.com/edge/en/concepts/crews
29. https://github.com/crewAIInc/crewAI/blob/main/lib/crewai/src/crewai/crew.py
30. https://github.com/crewAIInc/crewAI/issues/1823
31. https://github.com/crewAIInc/crewAI/issues/1399
32. https://github.com/crewAIInc/crewAI/issues/2606
33. https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html
34. https://arxiv.org/abs/2411.04468
35. https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic
36. https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/
37. https://learn.microsoft.com/en-us/agents/architecture/multi-agent-patterns
38. https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns
39. https://docs.ag2.ai/latest/docs/user-guide/advanced-concepts/groupchat/groupchat/
40. https://github.com/ag2ai/ag2/blob/main/autogen/agentchat/groupchat.py
41. https://docs.ag2.ai/docs/user-guide/network/overview/
42. https://a2a-protocol.org/latest/
43. https://a2a-protocol.org/v1.0.0/specification/
44. https://a2a-protocol.org/latest/topics/a2a-and-mcp/
45. https://a2a-protocol.org/latest/topics/life-of-a-task/
46. https://developers.googleblog.com/google-cloud-donates-a2a-to-linux-foundation/
47. https://github.com/a2aproject/A2A
48. https://www.anthropic.com/engineering/multi-agent-research-system
49. https://platform.claude.com/docs/en/about-claude/pricing
50. https://arxiv.org/abs/2305.14325
51. https://proceedings.mlr.press/v235/du24e.html
52. https://arxiv.org/abs/2305.19118
53. https://aclanthology.org/2024.emnlp-main.992/
54. https://arxiv.org/abs/2203.11171
55. https://arxiv.org/abs/2406.04692
56. https://arxiv.org/abs/2306.05685
57. https://proceedings.neurips.cc/paper_files/paper/2024/hash/32e07a110c6c6acf1afbf2bf82b614ad-Abstract.html
58. https://arxiv.org/html/2606.29270
59. https://arxiv.org/abs/2510.04048
60. https://arxiv.org/abs/2303.17760
61. https://github.com/camel-ai/camel
62. https://github.com/OWASP/AISVS/blob/main/1.0/en/0x10-C09-Orchestration-and-Agentic-Action.md
63. https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
64. https://eur-lex.europa.eu/eli/reg/2024/1689/oj
65. https://docs.temporal.io/design-patterns/approval
66. https://docs.temporal.io/ai/cookbook/human-in-the-loop-python
67. https://docs.temporal.io/guides/reliable-document-approvals
68. https://docs.temporal.io/develop/python/integrations/langgraph
69. https://github.com/temporal-community/durable-hitl-agents
70. https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/03/CSA_research_note_ai-agent-confused-deputy-prompt-injection-chains_20260323-csa-styled.pdf
71. https://agentskills.io/
72. https://developers.openai.com/api/docs/models/gpt-5.4
73. https://openai.com/api-fast-mode/
74. https://arxiv.org/abs/2507.01701
75. https://arxiv.org/pdf/2507.08944
76. https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md
77. https://reference.langchain.com/python/langgraph/types/interrupt
78. https://reference.langchain.com/python/langgraph/types/Command
79. https://docs.langchain.com/oss/python/langgraph/use-subgraphs
80. https://learn.microsoft.com/en-us/agent-framework/overview/
81. https://devblogs.microsoft.com/agent-framework/agent-frameworks-orchestration-patterns-reach-1-0/
82. https://devblogs.microsoft.com/agent-framework/from-specialist-agents-to-distributed-skills-over-mcp/
83. https://github.com/MicrosoftDocs/azure-ai-docs/blob/main/agent-framework/migration-guide/from-autogen/index.md
84. https://community.crewai.com/t/issue-only-manager-agent-appears-in-self-agents-when-using-process-hierarchical/7010
85. https://docs.ag2.ai/latest/docs/user-guide/advanced-concepts/groupchat/custom-group-chat/
86. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices
87. https://cornucopia.owasp.org/edition/companion/AAIJ
88. https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf
89. https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/
90. https://proceedings.neurips.cc/paper_files/paper/2023/file/a3621ee907def47c1b952ade25c67698-Paper-Conference.pdf
91. https://openai.github.io/openai-agents-python/ref/handoffs/
92. https://docs.langchain.com/oss/python/langchain/middleware/built-in

---

*End of research. No unpublished multi-agent loop SLOs. `$ / 1k tickets` tables are **[inferred]** from the stated supervisor+2-specialist and debate skeletons and list prices dated 2026-09-23. ReAct fuses live in 04; Pregel/`Command`/`interrupt` restart live in 05; MCP OAuth/passthrough live in 08.*
