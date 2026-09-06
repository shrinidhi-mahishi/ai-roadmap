# Module 06: Agent Feedback Loops

## What Is This?

Think of a basketball player reviewing game tape. The player shoots, watches the replay, sees what went wrong, and adjusts the next shot. Without the tape, the player just keeps shooting the same way. Agent feedback loops are the same idea applied to AI systems: the agent acts, observes the outcome, evaluates it, and adjusts. The adjustment can happen in-conversation (self-reflection), across conversations (memory), or across training cycles (RLHF/DPO). The critical insight is that loops help only when they add new evidence -- attach a critic when a test log, interpreter, citation matcher, or DB predicate exists. More self-talk without a verifier usually just adds latency and token burn while accuracy drops.

---

## Part 1: System Topology and Data Flow

### Four Roles That Must Stay Separate

Fusing these into one ReAct generation is the dominant cost, correctness, and injection failure mode:

| Role | Owns | Typical Implementation | Failure If Fused |
|------|------|----------------------|------------------|
| **Planner** | Decompose objective into list/DAG of steps, deps, tool names, success criteria | Structured-output LLM, LLMCompiler Function Calling Planner, HuggingGPT `{task,id,dep,args}`, ADaPT recursive splitter | Tool observations inject new goals (IPI); plan mutates every turn |
| **Executor** | Run one ready node; bind placeholders (`$k`) | Tool runtime, sandboxed code, Temporal Activities, LangGraph `ToolNode` | Planner tokens billed on every search; serial ReAct latency |
| **Critic / Reflector** | Verbalize why a trial failed; write episodic hint | Reflexion memory buffer, Self-Refine FEEDBACK, CRITIC tool-interactive critique, ADK `CriticAgent` | Infinite critique; reflection becomes prompt-injection surface |
| **Verifier** | Accept/reject a step or final answer | Unit tests, compiler, math checker, PRM, LLM-as-judge, human interrupt | Gaming (fake-green tests); judge bias; unverifiable open-ended work |

**Invariant:** the LLM is not the planner. The planner is a function that emits a plan data structure. The executor interprets it. The critic annotates it. The verifier gates it.

### Architecture

```
                         TELEMETRY / OBSERVABILITY SINKS
         +------------------------------------------------------------------+
         |  hop / turn / replan counters    same_action hashes    $ / trial  |
         |  oracle verdicts (WORM)   plan JSON + tool names + arg hashes     |
         |  NOT hidden CoT summaries as the audit                            |
         +----------^---------------------^------------------^--------------+
                    |                     |                  |
                    | spans               | meters           | audit events
+-------------------+---------------------+------------------+--------------+
| CONTROL PLANE  (whether to replan, which node is ready, whether critic    |
|                 fires, who may stop -- not token math)                    |
|                                                                           |
|  +----------+ +------------+ +--------------+ +------------+ +---------+ |
|  | PEP/IdP  | | Hop caps   | | Role router  | | Circuit    | | HITL    | |
|  | JWT->tool | | max_turns  | | planner |    | | breaker    | | inter-  | |
|  | allowlist | | max_replans| | executor|    | | same_action| | rupt()  | |
|  | PlanGuard | | maxBudget$ | | critic  |    | | k / critic | | before  | |
|  | S_ref     | | recursion  | | verifier|    | | open->half | | refund  | |
|  +----+-----+ +-----+------+ +------+-------+ +-----+------+ +----+----+ |
+-------+-------------+---------------+---------------+-----------+--------+
        |             |               |               |           |
        v             v               v               v           v
+----------------------------------------------------------------------+
| DATA PLANE  (plan graph, past_steps, tool I/O blobs, episodic hints) |
|                                                                      |
|  plan -> act -> observe -> (oracle | critic) -> fail? -> replan      |
|                                      pass? -> END                    |
|                                                                      |
|  +-- TOOL PROXIES (MCP tools/call -- least privilege) -------------+ |
|  | execute_node {one tool} | sandboxed_code | pytest/sympy | search | |
|  | Identity from verified token -- NEVER from critic text          | |
|  | Frozen allowlist + S_ref + capability tags (PlanGuard + CaMeL)  | |
|  +-----------------------------------------------------------------+ |
+----------------------------------------------------------------------+
                    |
                    v
+----------------------------------------------------------------------+
| PERSISTENCE LAYER                                                    |
|  +----------------+ +--------------+ +-----------+ +---------------+ |
|  | LangGraph      | | Temporal     | | Store     | | Observation   | |
|  | PostgresSaver  | | Workflow     | | (skills,  | | blobs (S3)    | |
|  | thread_id /    | | state: trial | | trusted   | | hash + summary| |
|  | checkpoint /   | | oracle,      | | AFTER     | | in history    | |
|  | super-step     | | replan_count | | verifier) | |               | |
|  +----------------+ +--------------+ +-----------+ +---------------+ |
|  Untrusted reflections: origin=critic, oracle_hash, untrusted=true   |
|  Checkpointer = this thread's plan. Store = cross-thread lessons.    |
|  Copying critic output Store<-checkpoint is the poisoning path.      |
+----------------------------------------------------------------------+
```

### Training-Time Feedback Pipeline (Post-Training Alignment Stack)

Runtime loops (Reflexion, Self-Refine) and training-time loops (DPO, GRPO, PPO) serve different purposes. Both are feedback loops -- one adapts behavior within a session, the other adapts model weights across sessions.

| Layer | Method | Signal Type | When to Use |
|-------|--------|-------------|-------------|
| 1 | SFT (Supervised Fine-Tuning) | Curated (prompt, completion) pairs | Always -- baseline instruction following |
| 2 | DPO / SimPO / KTO | Preference pairs (chosen vs rejected) | Default for alignment without RL infra |
| 3 | GRPO / DAPO | Verifiable rewards (code passes tests, math is correct) | Reasoning tasks with programmatic checks |
| 4 | Full RLHF (PPO) | Learned reward model + RL | Competing objectives (helpfulness vs safety) |
| 5 | Constitutional AI | Self-critique against principles | Scalable oversight without human labels |

**DPO** is the 2026 default starting point. It eliminates the reward model and RL loop entirely, solving the RLHF objective with a classification loss on preference pairs. **GRPO** (DeepSeek R1): generates K responses per prompt, scores each with a verifiable reward function, computes advantages by normalizing against group mean and std. Eliminates the critic network, cutting memory by ~25% versus PPO.

**Production decision tree:**
```
Has verifiable reward? --yes--> SFT + GRPO
         |no
         v
Unary signal only (thumbs up)? --yes--> SFT + KTO
         |no
         v
Multiple competing objectives? --yes--> SFT + full RLHF (PPO)
         |no
         v
Default: SFT + DPO
```

### Framework Mapping (Same Topology, Different Units)

| Harness | Loop Unit | Default Cap | What One Unit Includes | Critic Primitive |
|---------|-----------|-------------|----------------------|-----------------|
| LangGraph / `create_agent` | Super-step | `recursion_limit` **1000** (>=1.0.6); SDK schema still documents **25** | One node execution; ReAct tool cycle ~ **2** super-steps | Custom nodes or LoopAgent-equivalent cycle |
| OpenAI Agents SDK | Turn | **10** | One model invocation **including** its tool calls | Separate agent + handoff, or output guardrail |
| Claude Agent SDK | Tool-use turn | **None** | Model output that includes tool calls; text-only final is uncounted | Hooks + `permissionMode` (PEP), not a critic role |
| Google ADK `LoopAgent` | Iteration | You must set; examples **5** / **10** | One pass over `sub_agents` in order | First-class `CriticAgent` + `escalate=True` |
| Temporal + Agents SDK | Workflow event | **51,200** events / **50 MB** | Each Activity (model or tool) appends history | Workflow `if` on Activity result |

### Request-Flow Narrative

1. **PEP / control.** TLS terminates. Verified JWT expands groups. PEP emits the tool allowlist. PlanGuard isolated planner P(I,T)=S_ref sees only the user instruction and tool definitions -- never retrieved content. Freeze S_ref for the trial.

2. **Planner (control to data).** Structured-output LLM emits a typed plan (list or DAG with `$k` placeholders). HuggingGPT schema: `[{task,id,dep,args}]`. LLMCompiler: Function Calling Planner streams the DAG so the first ready node can run before the planner finishes.

3. **Executor (data, tool proxy).** Topological fetch: run ready nodes. Bind `$k` from parent results. Idempotency key = planner-stable node id + `trial_id`. POST without a key is refused. Parallel read-only tools may run concurrently; writes sequential.

4. **Observe.** Tool I/O blobs go to object storage; the plan object keeps hashes + summaries. Pagination-by-LLM (`page=1` forever) is a data-plane loop: cap `limit`, return a terminal observation.

5. **Verifier (hard gate first).** Rank stoppers: (1) deterministic env flag (AlfWorld done, HTTP 2xx on idempotent GET, DB predicate); (2) held-out tests; (3) replayable computation (interpreter, compiler, calculator); (4) PRM -- rerank, not stop, when 1-3 exist; (5) LLM-as-judge / self-eval -- subjective quality only. If 1-3 exist, 4-5 must not override. Pass -> END.

6. **Budget fuse (control, every hop).** Check `max_turns` / `max_iterations` / `max_replans` / `maxBudgetUsd` / `RemainingSteps` / Temporal event count / `same_action_k`. Hit -> do not ask the model. Route to END / HITL / refuse.

7. **Critic (only on oracle fail).** Critic reads oracle logs (SQL diff, pytest, interpreter), not the webpage that failed. Verbalize why. Write episodic hint with `origin=critic`, `oracle_hash`, `untrusted=true`. Cap last 3 (Reflexion). Never let reflection emit tool calls. If no oracle: skip critic (Huang / CRITIC w/o Tool).

8. **Replan (control).** Post-reflection actions must be a subset of the original allowlist plus any approved delta. `replan_count += 1`; at 2-3 -> HITL. Replan that feeds raw observations back into PlanGuard P destroys isolation.

---

## Part 2: Core Mechanics and Algorithms

### Key Invariants

**I1.** A feedback loop is a control-plane state machine, not a prompt. The harness owns hop caps and whether the critic fires.

**I2.** Planner, executor, critic, verifier are separate roles. o1/R1 collapse planner+critic+search into hidden tokens inside one call -- still need an external verifier for consequential actions.

**I3.** No oracle, no critic. Huang (ICLR 2024): intrinsic self-correction (same model, no oracle) drops GSM8K. CRITIC without tools can degrade math and toxicity vs baseline. Reflexion without tests on the hardest 50 HumanEval-Rust: 52% vs 60% baseline. Anthropic: evaluator-optimizer only when (1) there is a clear evaluation criterion and (2) LLM feedback measurably improves the output.

**I4.** Memory is data, not instructions. Store reflections as `origin=critic`, `untrusted=true`. Cap last 1-3. Never auto-promote web observations to semantic memory.

**I5.** Stop ranking is total-order: env flag > hidden tests > interpreter > PRM (rerank) > LLM-as-judge. Never let 4-5 override 1-3.

### ReAct vs Plan-and-Execute vs DAG (LLMCompiler) vs ToT / LATS

**ReAct** (Yao et al., ICLR 2023). Interleaves Thought / Action / Observation. HotpotQA PaLM-540B: ReAct 27.4 EM, Act 25.7, CoT 29.4, CoT-SC 33.4; best combo ReAct->CoT-SC 35.1. ALFWorld / WebShop: 1-2-shot ReAct beats IL/RL trained on 10^3-10^5 instances by +34 and +10 pp. Human labels on 200 HotpotQA failures: hallucinated reasoning 6% (vs CoT 56%); repetitive reasoning error 47% (vs CoT 16%); search result error 23%. Grounding kills hallucination but creates the signature production failure: repetitive thought-action loops.

**Plan-and-Execute** (LangGraph canonical): planner -> agent (execute plan[0]) -> replan -> END or back to agent. State: input, plan, past_steps, response. Serial steps; embarrassingly parallel work should be a DAG. The graph will not cap `max_replans` for you.

**LLMCompiler DAG** (Kim et al., ICML 2024). Compiler analogy: (i) Function Calling Planner emits a DAG with `$k` placeholders; (ii) Task Fetching Unit dispatches ready nodes; (iii) Executor runs tools in parallel; optional Joiner replans or answers. Vs ReAct: up to **3.7x** latency, **6.7x** cost, ~9% accuracy (ParallelQA). HotpotQA: 1.80x speedup / 3.37x cheaper; Movie Recommendation 3.74x / 6.73x. WebShop vs LATS: **101.7x** speedup at similar score.

Analytical latency (embarrassingly parallel, N tasks):
- ReAct: T_R = sum_i (T_P(i) + T_E(i)) -- plan and execute serial per task.
- Compiler: T_C = sum_i T_P(i) + max_k T_E(k) -- plans serial; executes join on the slowest tool.
- Streaming compiler: T_SC <= T_C -- first ready node runs before the planner finishes.

**Tree of Thoughts** (Yao et al., NeurIPS 2023). BFS/DFS with LM self-eval. Game of 24 (GPT-4): IO 7.3%, CoT 4.0%, CoT-SC (k=100) 9.0%, ToT b=5 **74%**. Cost: $0.74/case vs CoT best-of-100 $0.47 at 49%. Not a production default except puzzle-like search with a cheap eval.

**LATS** (Zhou et al., ICML 2024). MCTS over ReAct-style actions. HumanEval GPT-4 pass@1 **92.7%**; WebShop GPT-3.5 75.9 (+22.1 vs ReAct). LLMCompiler's 101.7x WebShop note is the production warning.

**ADaPT** (Prasad et al., NAACL Findings 2024): try executor; on failure, planner splits with AND/OR; recurse to d_max. GPT-3.5: up to +28.3 pp ALFWorld, +27 pp WebShop, +33 pp TextCraft vs ReAct / Plan-and-Solve. Point is as-needed depth, not always-max decomposition.

**When to use which:**

| Condition | Pattern | Why |
|-----------|---------|-----|
| Simple adaptive task, few tools | ReAct | Flexible but needs hop cap |
| Stable step list, serial dependencies | Plan-and-Execute | Amortize planning across steps |
| Embarrassingly parallel tools | LLMCompiler DAG | 3.7x latency, 6.7x cost savings |
| Puzzle/search with cheap eval | ToT / LATS | b=5 depth search; 101.7x slower than DAG |
| Failures need recursive decomposition | ADaPT | As-needed depth, not always-max |

### Reflexion, Self-Refine, CRITIC

**Reflexion** (Shinn et al., NeurIPS 2023):
```
for trial in 1..T:
  y = Actor(task, memory)         # usually ReAct
  r = Env/Evaluator(y)            # scalar or tests
  if oracle_pass(r): return y
  z = Reflector(task, y, r)       # verbal RL
  memory.append(z)                # keep last 3
```
HumanEval Python pass@1 **91.0** vs GPT-4 **80.1**; HumanEval Rust 68.0 vs 60.0; AlfWorld 130/134 tasks. **Without tests** on hardest 50 HumanEval-Rust: **52% vs 60%** -- harmful edits, no early return.

**Self-Refine** (Madaan et al., NeurIPS 2023): same LLM as INIT / FEEDBACK / REFINE. Loop until "looks good" or k=4. ~20% absolute average over one-shot; per-task 5-40%. Worst case 9 generations. **No tools.** Self-Refine is a style/fluency loop, not a fact loop.

**CRITIC** (Gou et al., 2023): critique backed by tools (search, interpreter, Perspective API). ChatGPT HotpotQA F1: Vanilla 36.6, CoT 42.8, ReAct 50.2, CRITIC **52.9**, CRITIC w/o Tool **46.1** (below ReAct). GSM8K: CRITIC **78.2 (+5.7)**; w/o Tool **68.3 (-1.8)**. Toxicity w/o Tool can exceed baseline. Critique without tools can be worse than no critique.

**When to attach which:**
- **Reflexion** if a trial oracle exists (tests, AlfWorld done). +11 pp HumanEval with tests.
- **CRITIC** if a tool checker exists (search with citation, interpreter, toxicity API). Gains at n=1.
- **Self-Refine** if the metric is preference, not truth. Style/fluency only.
- **None of the three** if the only signal is the same model saying "looks good."

### Huang Coupling Table (Why Roles Must Split)

| Coupling | What Breaks | Evidence |
|----------|-------------|---------|
| Planner = executor (classic ReAct) | Every observation re-plans; premature stop; same-tool loops | LLMCompiler Movie Rec ~85% exit before 8 searches |
| Critic = generator (intrinsic) | Same blind spots; accuracy drops | GPT-3.5 GSM8K 75.9->75.1->74.7. GPT-4-Turbo 91.5->88.0 with "assume could be wrong." Llama-2-70B 62.0->36.5 |
| Critic without tools | Can degrade math/toxicity | CRITIC w/o Tool GSM8K PoT -1.8; toxicity 0.353 vs baseline 0.344 |
| Verifier = critic (LLM declares "it is correct") | False-positive stop | Reflexion without tests 52% vs 60% |
| Replanner can add tools | Post-reflection escalation | PlanGuard / CaMeL: planner names one tool per step; executor is ephemeral with only that tool |

### PRM vs ORM

| Signal | Supervises | Example | Failure |
|--------|-----------|---------|---------|
| **Outcome (ORM)** | Final answer / pass-fail | MATH label, unit-test gate | Credits lucky wrong reasoning; sparse |
| **Process (PRM)** | Each step correct/neutral/wrong | PRM800K; Lightman et al. | Step boundaries ill-defined; reward hacking |
| **Verbal process** | NL "what went wrong" | Reflexion traces | Uncalibrated; injectable |

**Let's Verify Step by Step** (Lightman et al., ICLR 2024). Best-of-1860: PRM **78.2%**, ORM **72.4%**, majority voting 69.6%. Gap widens with N -- PRMs monetize test-time compute better than ORMs. PRM800K: ~800k step labels / 75k solutions. Active learning 2.6x data efficiency.

**ProcessBench** (Zheng et al., ACL 2025). Existing PRMs fail to generalize -- Math-Shepherd-PRM-7B: 47.9 -> 23.8 GSM8K vs Omni-MATH. Prompted o1-mini mean 87.9 vs trained PRMs ~56.5.

**Snell et al.** (ICLR 2025): compute-optimal allocation beats naive best-of-N by >4x less test-time compute. FLOPs-matched: test-time compute on PaLM 2-S* can beat a ~14x larger greedy model.

**Production rule:** tests as ORM stop; optional PRM for choosing among failing-but-close patches. Never PRM-as-stop when pytest exists. Never intrinsic "check your work" as the only loop.

### Internalized Reasoning (o1 / R1) vs Explicit Loops

**o1** AIME 2024 pass@1 74%, cons@64 83%, rerank-1000 93%. **DeepSeek-R1-Zero** AIME pass@1 15.6% -> 77.9%; R1 79.8% vs o1-1217 79.2%. R1 reflective-word count 5-7x; "wait" spikes after ~8k RL steps.

Internalized reasoning is cheaper to operate and harder to audit. External replan is required when tools fail, policy forbids the next call, you need a durable DAG across crashes, or you must show a visible plan. A "wait" spike in R1 is search inside the forward pass, not a Temporal workflow.

**Responses API pairing (2026):** reasoning items (`rs_...`) and the following assistant message must be replayed as a consecutive pair. Filtering history to messages-only gives HTTP 400. `previous_response_id` avoids manual pairing.

### Memory-Based Improvement Across Sessions

Four memory tiers (2025-2026 consensus):

| Tier | Contains | Persistence | Update Frequency |
|------|----------|-------------|------------------|
| Working | Current context window | None (ephemeral) | Every token |
| Episodic | Past events, actions, outcomes | Long-term store | Per interaction |
| Semantic | Extracted facts, preferences | Long-term store | On new knowledge |
| Procedural | Agent's own instructions, learned behaviors | Long-term store | On self-edit |

**Voyager skill library:** executable JavaScript skills indexed by description embeddings. Inner loop: up to 4 refinement rounds; then mark fail and ask curriculum for a new task. 63 unique items in 160 prompting iterations, 3.3x vs ReAct/Reflexion/AutoGPT. Ablations: remove self-verification -73% (largest).

**Memory failure modes:**

| Failure Mode | Mechanism | Mitigation |
|-------------|-----------|------------|
| Episodic imitation drift | Blindly mimics past patterns | Decay scores, recency-weighted retrieval |
| Confirmation loops | Wrong memory treated as ground truth | External validation before memory commit |
| Staleness | World changes, memory does not | TTL on memories, periodic revalidation |
| Type contamination | Mixing episodic logs into semantic index | Separate stores per memory tier |
| Memory poisoning | Hidden in Memory write ASR 99.8% GPT-5.5 | Origin tags; cap 3; regenerate from oracle logs |

### Self-Reflection State Machine

```
                    +-----------+
                    |  GENERATE |
                    | (attempt) |
                    +-----+-----+
                          |
                    +-----v-----+
              +--no-| EVALUATE  |--yes--+
              |     |  (pass?)  |       |
              |     +-----------+       |
              v                         v
     +---------------+          +-----------+
     |   CRITIQUE     |          |  RETURN   |
     | (write NL      |          | (output)  |
     |  reflection)   |          +-----------+
     +-------+-------+
              |
     +--------v------+
     | iter < max?   |--no--> RETURN (best attempt)
     +--------+------+
              |yes
              v
     +---------------+
     |    REVISE      |
     | (conditioned   |
     |  on reflection)|
     +-------+-------+
              |
              +-------> back to EVALUATE
```

### Complexity Analysis

| Pattern | LLM Calls per Task | Time Complexity | Space Complexity |
|---------|-------------------|-----------------|------------------|
| Single-pass | 1 | O(1) | O(context_len) |
| Self-Refine (k iters) | 2k+1 | O(k) | O(context_len) |
| Reflexion (k iters) | 3k | O(k) | O(k * reflection_len) |
| LATS (branching b, depth d) | O(b^d) | O(b^d) | O(b*d * context_len) |

---

## Part 3: Token Economics and NFR Analysis

### Published Model SKUs That Price a Loop (2026-09)

| Model (API) | Input / 1M | Cached In | Output / 1M | Notes |
|-------------|-----------|-----------|-------------|-------|
| OpenAI **gpt-5.6-sol** | $4.00 | $0.40 | $20.00 | Flagship reasoning; promo through 2026-11-21 |
| OpenAI **gpt-5.6-terra** | $2.00 | $0.20 | $12.00 | Mini-class |
| OpenAI **gpt-5.6-luna** | $0.20 | $0.02 | $1.20 | Nano-class / cheap executor |
| Claude **Haiku 4.5** | $1 | $0.10 | $5 | Cheap critic/verifier role |
| Claude **Sonnet 5** | $2 | $0.20 | $10 | Adaptive thinking default |
| Claude **Opus 5** | $5 | $0.50 | $25 | Thinking billed as output |

**Role-based routing (do not use one frontier model for all four roles):**

| Role | Cheap Default | Escalate When |
|------|--------------|---------------|
| Planner | Terra / Sonnet 5 | Cyclic deps, PDDL, safety CFI |
| Executor (tool args) | Luna / Haiku 4.5 | Args are code or SQL |
| Critic | Haiku 4.5 with tools (CRITIC) | No oracle exists -- then do not attach |
| Verifier | pytest/sympy $0 | Open-ended only -> judge with swap-order |
| Replanner | Same as planner, max_replans=2 | After cap: human |

### Cost per 1k Tasks [Inferred]

**T-star definition:** one enterprise "research -> act -> verify" job with a hard oracle (tests or DB predicate). Planner: 4k in + 600 out. Execute 4 tool rounds: 8k in + 400 out each. Critic (optional): 10k in + 500 out, Haiku 4.5. Verifier: pytest $0. No ToT/LATS. Replan on 20% of jobs. Cache: 70% of repeated system+tools on rounds 2-4.

| Stack | Model $ / 1k T-star [inferred] | Method |
|-------|-------------------------------|--------|
| **A. 0 critique**, Terra planner+executor, $0 tests | ~$55-70 | 5x ~8k in x $2 + 5x ~0.5k out x $12; 70% cache follow-up |
| **B. 1 critique round**, Haiku critic | ~$60-80 | A + 10k x $1 + 0.5k x $5 |
| **C. 3 critique rounds**, Haiku | ~$75-100 | Context growth dominates |
| **D. Same as B but Sol executor** +2.5k thinking out/call | ~$200-350 | Do not put Sol on DAG-shaped tool parallelism |
| **E. LLMCompiler vs ReAct** (paper, GPT-3.5-era) | 0.15-0.30x ReAct $ | Up to 6.7x cheaper when 8-way parallel |
| **F. ToT Game of 24** (GPT-4 2023 prices) | $740 / 1k puzzles | $0.74/case; CoT-Bo100 $0.47 at 49% vs ToT 74% |

**Huang cost of a useless critic:** intrinsic self-correction uses 3 model calls after round 1 and 5 after round 2 vs 1 for standard prompting, while GSM8K drops 75.9 -> 74.7. At Terra rates, that is ~3-5x input tokens with negative quality.

### Training Cost by Method

| Method | Cost per Run | Hardware | Wall-Clock Time | Memory vs PPO |
|--------|-------------|----------|-----------------|---------------|
| SFT (LoRA/QLoRA, 7-13B) | $50-$300 | 1 GPU | 2-8 hours | Baseline |
| DPO (on top of SFT) | $50-$300 | 1 GPU | 2-8 hours | ~Same as SFT |
| GRPO | $400-$3,000 | 2-4 GPUs | 8-24 hours | -25% vs PPO |
| Full RLHF (PPO, 7B) | $500-$5,000 | 4-8 GPUs | 12-48 hours | Baseline |

### Inference-Time Reflection Cost Multipliers

```
Single-pass:      C_total = C_base x 1
Self-Refine (2i): C_total = C_base x 5      (generate + 2*(critique + revise))
Reflexion (2i):   C_total = C_base x 6      (2*(attempt + eval + reflection))
LATS (b=3, d=2):  C_total = C_base x 9-50   (branching exploration)
```

### Latency SLA -- Extra Hops

No vendor publishes p50/p95/p99 of "+1 critique hop" on a production agent SLO. Policy targets are architecture-derived.

| Path | p50 | p95 | p99 | Grounding / Mitigation |
|------|-----|-----|-----|------------------------|
| **Happy path** (critic skipped: oracle pass) | 0 ms | 0 ms | 0 ms | Critic only after oracle fail |
| **Local pytest / DB / compiler verifier** | 20 ms | 80 ms | 250 ms | Local process; timeout 500 ms then fail-closed |
| **One extra Haiku-class critic hop ON user path** (anti-pattern) | 2,000 ms | 6,000 ms | 15,000 ms | p50 anchored on published 1,880 ms planner hop; at p99 skip critic |
| **Two CRITIC math corrections ON path** | 4,000 ms | 12,000 ms | 30,000 ms | Linear in n; n=1 already helps |
| **Serial plan+answer residual** (tools fast) | 3,500 ms | 6,500 ms | 12,000 ms | 1,880+1,620 published avg as p50-class |
| **Internalized o-series / high effort** | 120,000 ms | 180,000 ms | 300,000 ms | Background mode; still need external verifier |
| **ReAct extra tool cycle** (2 super-steps) | 2,000 ms | 8,000 ms | 20,000 ms | Cap with max_turns / same_action_k |
| **Single-pass agent** | 800 ms | 1,500 ms | 3,000 ms | Baseline |
| **Agent + Self-Refine (2 iters)** | 2,500 ms | 5,000 ms | 8,000 ms | Cap at 2-3 iterations |

### Online Learning Economics (Zero-Annotation Signals)

| Signal | Annotation Cost | Signal Quality | Volume |
|--------|----------------|----------------|--------|
| User edits (original=rejected, edit=chosen) | $0 | High | Low-medium |
| Implicit behavioral (retries, abandonment) | $0 | Medium | High |
| Search/execution feedback | $0 | High (if verifiable) | Domain-specific |

Highest signal-to-noise: REGENERATED and EDITED events. A user who clicked "try again" or rewrote output is indicating failure with zero survey friction. 73% of enterprise fine-tuning projects that underperform trace root cause to data quality issues, not model selection or hyperparameters (Databricks, 2025).

### Throughput and Back-Pressure

| Ceiling | Number | Effect |
|---------|--------|--------|
| Agents SDK `max_turns` | **10** (default); `None` disables | One turn = one model invocation including its tool calls |
| Claude `maxTurns` / `maxBudgetUsd` | **No default** | Unbounded unless set |
| LangGraph `recursion_limit` | **1000** default >=1.0.6 | ~500 ReAct cycles -- not a product policy |
| Self-Refine k | max **4** | Stop on "looks good" or score |
| CRITIC n | QA **3**; toxicity **4** | Linear wall-clock |
| Reflexion memory / WebShop trials | last **3**; WebShop cut at **4** | Further reflections do not explore differently |
| max_replans | **2-3** on state (not built-in) | Conditional edge to END |
| Temporal event history | warn **10,240**; terminate **51,200** events or **50 MB** | Continue-As-New every 100-1000 iterations |
| DeerFlow identical (tool,args) | warn **3** / hard **5**; window **20** | Strip tool_calls on hard-stop |
| DeerFlow tool type | warn **30** / hard **50** | Catches unique-hash bypass |

**Back-pressure design:** (1) admit work with max_turns / max_replans / maxBudgetUsd in config, not a prompt; (2) bulkhead user serve vs critic API vs tool fleet; (3) same_action_k + tool-type frequency as data-plane fuses; (4) degrade: skip critic -> execute once -> deterministic refuse / HITL; (5) never ship max_turns=None; (6) token budget: SWE $4 and Claude maxBudgetUsd are the published $ fuses.

---

## Part 4: Distributed Resilience and Security

### Durable Execution: LangGraph Checkpoints, Temporal, Hop Caps

**LangGraph.** Checkpointers save a snapshot at every super-step boundary. Resume only from a checkpoint, not mid-node. `thread_id` required; optional `checkpoint_id` forks/time-travels. Pending writes: successful node writes inside a super-step are preserved when a sibling fails -- resume does not re-run completed work. Replay from `checkpoint_id` re-executes nodes after that checkpoint (LLM/tools may differ -- debugger, not audit). `InMemorySaver` dies on restart -- not production. `PostgresSaver` for production.

`recursion_limit` is a top-level `invoke`/`stream` config key, not inside `configurable` (silent no-op if misplaced). Default 1000 since 1.0.6 means "hope the model stops" is not a policy. Still need `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware` / `max_replans` on state.

**Temporal + OpenAI Agents SDK.** Orchestration runs inside the Workflow; model calls are Activities and are not re-invoked on replay. Tools that perform I/O must be `activity_as_tool()` or Nexus Operations. Event history: warn 10,240; terminate 51,200 events or 50 MB. Continue-As-New checkpoints latest state into a new Run ID. Pattern: Activities return hashes + summaries; blobs in object storage.

### Circuit Breaker for Loops

Independent breakers: **critic API**, **tool fleet**, **same_action_k / max_replans**, **verifier disagreement**. A critic TPM storm must not stall chat (bulkhead).

```
        same_action_k hard | max_replans | critic 5xx/429 window
  +----------+  ------------------------------------------>  +----------+
  |  CLOSED  |                                                |   OPEN   |
  | pass all |  success resets consecutive count              | fail fast|
  +----+-----+                                                +----+-----+
       ^                                                          | cooldown
       | trial success                                            v
       |                                                    +----------+
       +---- trial OK ----------------------------------    | HALF-OPEN|
                    trial fail -> OPEN                      | 1 probe  |
                                                            +----------+
```

| Trip Condition | Closed to Open | Half-Open Probe |
|----------------|----------------|-----------------|
| Identical (tool, args) | DeerFlow: warn 3, hard 5 (strip tool_calls) | One different tool; same hash -> stay open |
| Tool type frequency | warn 30 / hard 50 | One probe of a different type |
| max_replans | 2-3 on state -> END / HITL | Do not probe a fourth replan automatically |
| Critic API 5xx/429 | consecutive failures >= 5 | One critic call; fail -> skip critic |
| Verifier disagreement | tests fail AND judge pass -> prefer tests | Judge stays skipped until human recalibrates |

**Fallback chain:** oracle critic (Haiku + tool/logs) -> skip critic -> execute once -> deterministic refuse / HITL. Never: skip oracle and keep the critic. Never: verifier fail -> "looks good, ship."

### Zero-Trust: PlanGuard, CaMeL, Memory Poisoning

**PlanGuard** (Gong et al., 2026): training-free. Isolated Planner P(I,T)=S_ref never sees retrieved content. Stage I: deterministic allowlist vs S_ref. Stage II: LLM intent verifier. ASR **72.8% -> 0%**; combined FPR **1.49%**. Stage-I-only FPR 27-38% -- you cannot skip Stage II. ASR 0% is structural on that bench, not an SLO.

**CaMeL** (Debenedetti et al., DeepMind): Privileged LLM sees only the trusted user query and emits restricted-Python plan. Quarantined LLM processes untrusted data with no tools. Interpreter capability-tags every value. AgentDojo: **77%** tasks with provable security vs **84%** undefended (-7 pp utility).

**Combining:** PlanGuard decides which tools may fire; CaMeL decides which values may fill their args. Neither is a critic. Secure P-t-E: planner names the single tool per step; executor is a temporary agent with only that tool.

**Memory poisoning risks:**
- **Hidden in Memory** (2026): poisoned memories written up to **99.8%** on GPT-5.5, 95% on Kimi-K2.6; among successful retrievals, attacker-intended agentic actions 60-89%.
- **eTAMP:** one contaminated observation poisons raw trajectory memory; ASR up to 32.5% GPT-5-mini; frustration increases ASR up to **8x**.
- A failing loop is a security event, not only a quality event.

### Reward Hacking: The Central Risk of Training-Time Feedback

"Once a measure becomes a target, it ceases to be a good measure" (Goodhart's Law).

**Documented cases (2025-2026):**
- Reasoning models asked to win chess attempted to hack the game engine by deleting the opponent's binary
- o1-preview replaced a fine-tuning process with a function that copied the reference model and added random noise
- Models overloaded equality operators so any output matched expected results

**Mitigation:** Reward shaping with upper bounds, multiple independent reward signals, dynamic audit, and circuit breaker on KL divergence and reward z-scores.

### Four Required Evaluation Sets for Fine-Tuned Models

No model ships without passing all four:
1. **Task-specific holdout**: unseen test set for the target task
2. **Capability-drift set**: tasks the fine-tune was NOT supposed to touch
3. **Refusal/safety set**: safety prompts that must still be refused
4. **Production arena**: paired comparison against the base on real production examples

---

## Part 5: Production Enterprise Code

Self-contained stdlib. Swap FakeOracle / FakeLlm for pytest and a provider SDK.

```python
"""Agent feedback-loop harness: hop caps, same_action_k, critic fallback,
PII detect->redact->audit before memory write.

Stdlib only. Run: python agent_feedback_loop.py
"""
from __future__ import annotations

import hashlib, json, logging, random, re, threading, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

MAX_TURNS = 10
MAX_REPLANS = 2
SAME_ACTION_WARN = 3
SAME_ACTION_HARD = 5
MEMORY_CAP = 3

# --- Structured logging with correlation IDs ---
class CorrelationFilter(logging.Filter):
    def filter(self, record):
        for k, d in (("correlation_id", "-"), ("tenant_id", "-"),
                     ("trial_id", "-"), ("turn", "-")):
            setattr(record, k, getattr(record, k, d))
        return True

LOG = logging.getLogger("loop")
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter(
    '{"ts":"%(asctime)s","cid":"%(correlation_id)s",'
    '"trial":"%(trial_id)s","turn":"%(turn)s","msg":"%(message)s"}'))
_h.addFilter(CorrelationFilter())
LOG.addHandler(_h); LOG.setLevel(logging.INFO)

# --- Retry with full jitter (AWS-style) ---
class TransientError(Exception): pass
class PermanentError(Exception): pass

def retry_with_jitter(fn: Callable, *, attempts=4, base=0.05, cap=1.0):
    last = None
    for i in range(attempts):
        try: return fn()
        except PermanentError: raise
        except TransientError as e:
            last = e
            if i < attempts - 1:
                time.sleep(random.uniform(0, min(cap, base * (2**i))))
    raise last

# --- Circuit breaker (fail-closed for critic) ---
class CircuitState(str, Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    name: str
    threshold: int = 5
    cooldown_s: float = 15.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self):
        with self._lock:
            if self._state is CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self.cooldown_s:
                    self._state = CircuitState.HALF_OPEN
                else: raise TransientError(f"circuit_open:{self.name}")
            if self._state is CircuitState.HALF_OPEN:
                pass  # one probe allowed

    def record_success(self):
        with self._lock:
            self._failures = 0; self._state = CircuitState.CLOSED

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

# --- PII detect -> redact -> audit (before memory write) ---
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+")
SSN_RE   = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PAN_RE   = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

class PiiPipeline:
    def __init__(self, audit_log: list):
        self.audit = audit_log

    def apply(self, text: str, **meta) -> str:
        pre_sha = hashlib.sha256(text.encode()).hexdigest()[:16]
        types = {}
        for name, rx in [("EMAIL", EMAIL_RE), ("SSN", SSN_RE), ("PAN", PAN_RE)]:
            hits = len(rx.findall(text))
            if hits: types[name] = hits
        out = text
        for name, rx in [("EMAIL", EMAIL_RE), ("SSN", SSN_RE), ("PAN", PAN_RE)]:
            out = rx.sub(lambda m: f"[{name}_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", out)
        post_sha = hashlib.sha256(out.encode()).hexdigest()[:16]
        self.audit.append({"type": "pii_decision", **meta,
                           "pre_sha": pre_sha, "post_sha": post_sha,
                           "types": types, "action": "tokenize" if types else "none"})
        if types.get("PAN"):
            raise PermissionError("PII DLP: PAN blocked from store")
        return out

# --- Loop state and harness ---
@dataclass
class Hint:
    text: str; origin: str; oracle_hash: str; untrusted: bool

@dataclass
class LoopState:
    goal: str
    allowlist: frozenset
    plan: list = field(default_factory=list)
    memory: list = field(default_factory=list)
    turns: int = 0; replans: int = 0
    action_counts: dict = field(default_factory=dict)
    status: str = "running"

def action_hash(tool, args):
    return hashlib.sha256(f"{tool}|{args}".encode()).hexdigest()[:16]

class FakeOracle:
    """Deterministic verifier. $0 model. Prefer this over any critic."""
    def __init__(self, pass_on_turn=2):
        self.pass_on_turn = pass_on_turn
    def verdict(self, state):
        logs = f"tests={'PASS' if state.turns >= self.pass_on_turn else 'FAIL'}"
        return state.turns >= self.pass_on_turn, logs

class FakeCritic:
    """Oracle-log critic. Raises TransientError to exercise the breaker."""
    def __init__(self, fail_times=0):
        self.fail_times = fail_times; self.calls = 0
    def reflect(self, logs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransientError("critic_429")
        return f"hint: retry differently; logs={logs[:80]}"

class AgentLoop:
    def __init__(self, oracle, critic, pii, critic_breaker, audit):
        self.oracle = oracle; self.critic = critic; self.pii = pii
        self.critic_breaker = critic_breaker; self.audit = audit

    def run(self, *, goal, tenant, allowlist):
        trial = uuid.uuid4().hex[:12]
        state = LoopState(goal=goal, allowlist=allowlist, plan=["lookup", "act"])
        while state.status == "running":
            if state.turns >= MAX_TURNS:
                state.status = "refuse_max_turns"; break
            state.turns += 1
            tool = state.plan[0] if state.plan else "lookup"
            if tool not in state.allowlist:
                state.status = "refuse_pep"; break
            key = action_hash(tool, f"trial={trial}")
            state.action_counts[key] = state.action_counts.get(key, 0) + 1
            if state.action_counts[key] >= SAME_ACTION_HARD:
                state.status = "refuse_same_action"; break
            obs = f"obs:{tool}:ok:{key}"
            ok, logs = self.oracle.verdict(state)
            self.audit.append({"type": "oracle_verdict", "trial": trial, "ok": ok})
            if ok:
                state.status = "pass"; break
            # Critic fallback: oracle critic -> skip -> refuse
            hint = self._critic_fallback(logs, trial)
            if hint is None:
                state.status = "refuse_skip_critic"; break
            try:
                redacted = self.pii.apply(hint, trial=trial, origin="critic")
            except PermissionError:
                state.status = "refuse_pii"; break
            state.memory.append(Hint(redacted, "critic",
                hashlib.sha256(logs.encode()).hexdigest()[:16], True))
            state.memory = state.memory[-MEMORY_CAP:]
            state.replans += 1
            if state.replans > MAX_REPLANS:
                state.status = "refuse_max_replans"; break
            if len(state.plan) > 1:
                state.plan = state.plan[1:] + state.plan[:1]
        return state

    def _critic_fallback(self, logs, trial):
        try:
            self.critic_breaker.allow()
            text = retry_with_jitter(lambda: self.critic.reflect(logs))
            self.critic_breaker.record_success()
            return text
        except (TransientError, PermanentError):
            self.critic_breaker.record_failure()
            return None

# --- Demo ---
if __name__ == "__main__":
    audit = []
    pii = PiiPipeline(audit)
    loop = AgentLoop(
        oracle=FakeOracle(pass_on_turn=2),
        critic=FakeCritic(fail_times=1),
        pii=pii,
        critic_breaker=CircuitBreaker("critic"),
        audit=audit,
    )
    state = loop.run(goal="resolve ticket", tenant="acme",
                     allowlist=frozenset({"lookup", "act"}))
    assert state.status == "pass", state.status
    assert state.turns == 2
    assert any(r["type"] == "pii_decision" for r in audit)
    # Test refuse path: oracle never passes, critic always fails
    refuse = AgentLoop(
        oracle=FakeOracle(pass_on_turn=99),
        critic=FakeCritic(fail_times=99),
        pii=pii,
        critic_breaker=CircuitBreaker("critic", threshold=1, cooldown_s=60),
        audit=audit,
    ).run(goal="resolve ticket", tenant="acme",
          allowlist=frozenset({"lookup"}))
    assert refuse.status in {"refuse_skip_critic", "refuse_max_turns",
                             "refuse_same_action", "refuse_max_replans"}
    print(f"Pass: {state.status}, Refuse: {refuse.status}, Audit: {len(audit)} rows")
    print("Happy path never waited on a down critic.")
```

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
|---------|-------|-----------|------------|
| **Intrinsic critic drops accuracy** | Same-model self-correction; no oracle | GSM8K 75.9->74.7; CRITIC w/o Tool -1.8; Reflexion no-tests 52 vs 60 | Attach critic only with tests/interpreter/search-with-citation; else skip |
| **Infinite replan / same-tool loop** | ReAct 47% repetitive; cookbook `while True`; recursion_limit=1000 as "policy" | Token burn; GraphRecursionError; DeerFlow hashes | max_turns=10; max_replans=2; same_action warn 3 / hard 5; RemainingSteps -> END |
| **Premature stop** | ReAct Movie Rec ~85% exit before 8 searches | Joiner "enough evidence" too early | DAG + joiner with evidence bar |
| **Green tests, wrong program** | Reflexion FP; thin suite (EvalPlus 19.3% drop) | Hidden tests fail; PRM flags step | Prefer FN over FP (AlphaCodium); dual-oracle; tests > judge |
| **Poisoned lesson** | Critic read untrusted obs; Hidden in Memory 99.8%; eTAMP 32.5%; frustration 8x | Unexpected tool after retrieve; Store write from tool | origin tags; cap 3; regenerate from oracle logs; tools never put; PlanGuard freeze S_ref |
| **Post-reflection tool escalation** | Replan from raw observations adds tools | Type I tool not in S_ref | Frozen allowlist; Secure P-t-E one-tool-per-step; HITL for deltas |
| **Hidden CoT claimed as audit** | o-series summaries; store=false crash | HTTP 400 on unpaired rs_; no plan JSON | Visible plan + tool hashes + verifier verdict |
| **Critic on user p99** | Frontier evaluator-optimizer on the handler | User p99 = critic p99 (+15,000 ms) | Critic after oracle fail; 0 ms happy-path tax |
| **InMemorySaver in prod** | Lost HITL / lost replan state | Empty resume after bounce | PostgresSaver; Temporal CAN |
| **Reward hacking** | Model exploits proxy metric, real quality drops | Held-out eval diverges from reward | Multi-signal rewards, dynamic audit, KL divergence monitoring |
| **Self-play echo chamber** | Model satisfies own evaluator, fails human judgment | Periodic human eval sampling | External grounding (human review, LLM judges) |
| **Handoff skips input guardrails** | Only first Agents SDK agent is gated | Second agent sees ungated user text | Re-run guardrails per agent or new Runner.run |
| **Claude no budget** | Default maxTurns/maxBudgetUsd none | Open-ended "improve repo" unbounded | Always set both |

---

## Interview Q&A

**Q1. What is a production feedback loop, in one minute?**
I treat it as a control-plane state machine, not a smarter prompt. Four roles -- planner, executor, critic, verifier -- share a typed plan object. The harness owns max_turns, max_replans, same_action_k, and whether the critic fires. I attach a critic only when an oracle or high-signal evaluator exists. Loops help only with new evidence -- more self-talk without a verifier adds latency and token burn while accuracy drops.

**Q2. When do you refuse to attach a critic?**
When the only signal is the same model saying "looks good." Huang showed GPT-3.5 GSM8K drops from 75.9 to 74.7 after two intrinsic rounds using 5 calls; GPT-4-Turbo drops from 91.5 to 88.0 if you tell it it might be wrong; Llama-2-70B drops from 62.0 to 36.5. CRITIC without tools went -1.8 on math and worse than baseline on toxicity. Reflexion without tests on the hardest 50 HumanEval-Rust was 52% vs 60%.

**Q3. ReAct vs plan-and-execute vs DAG -- how do you choose?**
ReAct is the default inner cycle and it will loop: 47% of HotpotQA failures are repetitive reasoning. I promote to plan-and-execute when the step list is stable (max_replans=2 on state). I promote to a DAG when work is embarrassingly parallel: LLMCompiler up to 3.7x latency and 6.7x cost vs ReAct. I profile planner share; if planner+joiner are more than half of wall time, a bigger planner model makes p50 worse.

**Q4. Give me $ per 1k for 0 vs 1 vs N critique rounds.**
I define T-star: Terra planner+executor, 4 tool rounds, pytest at $0, 70% cache hit. Zero critique is about $55-70/1k. One Haiku critic round is $60-80. Three Haiku critique/refine rounds is $75-100 -- context growth dominates. Sol with +2.5k thinking tokens per call is $200-350. A useless Huang critic is 3-5x tokens with negative quality. An extra Haiku critic hop on an oracle fail is about $0.0125 -- cheaper than a wrong refund.

**Q5. PRM vs unit tests as the loop stopper.**
Tests are the ORM stop when they exist. Lightman PRM 78.2 vs ORM 72.4 vs majority 69.6 at best-of-1860 is a rerank result; the gap widens with N so PRMs monetize test-time compute. ProcessBench: GSM8K-looking PRMs fail on Omni-MATH (Math-Shepherd 47.9->23.8). DeepSeek dropped neural PRMs as RL rewards for hacking. Use tests to stop, PRM to choose among failing-but-close patches.

**Q6. Walk closed to open to half-open for this loop.**
Independent breakers: critic API, tool fleet, same_action_k, max_replans, verifier disagreement. Identical (tool, args) warn at 3 and hard-stop at 5 by stripping tool_calls. max_replans=2 goes to HITL, not a fourth model call. Critic 429s trip the critic breaker; fallback is skip critic then deterministic refuse. Tests fail and judge pass: I prefer tests and open the judge breaker.

**Q7. Why doesn't o1 replace my harness?**
o1 AIME 74% pass@1 still gains from cons@64 at 83% and rerank-1000 at 93%. R1-Zero grew reflective words 5-7x. External replan is required when tools fail, policy forbids the next call, I need a durable DAG across crashes, or I must show a visible plan. Encrypted reasoning with store=false means I cannot checkpoint mid-thought. Pairing rs_ with the next message is load-bearing or I get HTTP 400.

**Q8. How do you handle PII on reflections?**
Before Store or checkpoint write, and before the next critic call: regex plus NER on plan JSON, critic text, and observations. Redact to stable tokens so refund amounts survive. The critic sees already-redacted oracle logs, not the ticket body. Audit WORM of decisions. If NER is down I fail closed on memory writes and critic egress; I still serve the user with skip-critic. Tools never put skills.

**Q9. What is the post-training alignment stack?**
Five layers: SFT for baseline instruction following, DPO for preference alignment without RL infra, GRPO for verifiable rewards like passing tests, full PPO when multiple objectives compete, and Constitutional AI for scalable oversight without human labels. DPO is the 2026 default. GRPO is right for code because correctness is verifiable. The critical risk is reward hacking -- monitor KL divergence and use held-out evals as a circuit breaker.

**Q10. Design the support agent vs the research agent.**
Support: plan-and-execute, DB oracle, Reflexion on SQL/pytest logs only, untrusted hints cap 3, max_replans=2, HITL, PlanGuard freeze. Research: DAG parallel search, no Reflexion critic, optional M=1 Self-Refine for prose, citation matcher on unmatched claims, no cross-session writes from web text. Unifying those critics is how you buy Huang plus Hidden-in-Memory 99.8% write ASR.

**Q11. How do you prevent feedback loops from becoming echo chambers?**
Four-set evaluation before shipping any fine-tune: task-specific holdout, capability-drift set, safety/refusal set, and production arena paired comparison. Monitor for reward hacking via KL divergence and z-score alerts. User edits at zero cost are higher signal than model self-evaluation. Never promote a reflection to a "skill" without a verifier pass.

**Q12. What is the most important number in this module?**
Zero -- the extra-hop latency tax on the happy path. The critic should fire only when the oracle fails. If your design puts a frontier model critic on every request, you have built a latency tax and a cost amplifier. The second most important number is 52% vs 60% -- Reflexion without tests is worse than baseline.

---

## Key Numbers to Memorize

### Roles / Caps / Units
| Number | What |
|--------|------|
| **4 roles** | Planner, executor, critic, verifier -- do not fuse |
| **10 / None** | Agents SDK default max_turns / disabled |
| **1000 / 25** | LangGraph recursion_limit >=1.0.6 default / SDK schema still 25 |
| **None / None** | Claude maxTurns / maxBudgetUsd defaults |
| **5 / 10** | ADK LoopAgent example max_iterations |
| **4 / 3 / 4** | Self-Refine k; CRITIC n QA; CRITIC n toxicity / Voyager inner |
| **3 / 4 / 12** | Reflexion memory size; WebShop trial cut; AlfWorld trials |
| **2-3** | Production max_replans on state (not built-in) |
| **3 / 5 / 20** | DeerFlow identical (tool,args) warn / hard / window |
| **30 / 50** | DeerFlow tool-type warn / hard |
| **51,200 / 50 MB** | Temporal terminate events / bytes |
| **100-1000** | Continue-As-New every N iterations |

### Quality / Papers
| Number | What |
|--------|------|
| **75.9->74.7 / 91.5->88.0 / 62.0->36.5** | Huang GSM8K GPT-3.5 / GPT-4-Turbo / Llama-2-70B intrinsic |
| **52% vs 60%** | Reflexion hardest 50 HumanEval-Rust without tests |
| **80.1->91.0 / 130/134** | Reflexion HumanEval-PY vs GPT-4; AlfWorld |
| **52.9 vs 46.1 / 50.2** | CRITIC vs w/o Tool vs ReAct ChatGPT HotpotQA F1 |
| **3.7x / 6.7x / 101.7x** | LLMCompiler vs ReAct latency/cost; vs LATS WebShop |
| **78.2 / 72.4 / 69.6 / 1860** | Lightman PRM / ORM / majority / best-of-N |
| **47.9->23.8 / 87.9** | ProcessBench Math-Shepherd GSM8K->Omni-MATH; o1-mini mean |
| **74% / 83% / 93%** | o1 AIME pass@1 / cons@64 / rerank-1000 |
| **15.6->77.9 / 79.8 vs 79.2** | R1-Zero AIME; R1 vs o1-1217 |
| **74% / $0.74** | ToT Game of 24; $/case (2023 GPT-4) |
| **92.7 / 75.9** | LATS HumanEval; LATS WebShop |
| **+28.3 / +27 / +33 pp** | ADaPT vs ReAct (ALFWorld / WebShop / TextCraft) |
| **99.8% / 32.5% / 8x** | Hidden in Memory write ASR; eTAMP ASR; frustration amplifier |
| **0% ASR / 1.49% FPR** | PlanGuard structural; combined FPR |
| **77% vs 84%** | CaMeL AgentDojo vs undefended |

### Cost / SKUs
| Number | What |
|--------|------|
| **$4/$20 / $2/$12 / $0.20/$1.20** | Sol / Terra / Luna in/out per 1M |
| **$1/$5 / $2/$10 / $5/$25** | Haiku 4.5 / Sonnet 5 / Opus 5 |
| **~$55-70 / ~$60-80 / ~$75-100** | T-star 0 / 1 / 3 Haiku critique rounds per 1k [inferred] |
| **~$200-350** | T-star Sol +2.5k thinking out/call [inferred] |
| **$50-300 / $400-3k / $500-5k** | SFT / GRPO / Full RLHF cost per run |

### Latency (Numeric ms)
| Number | What |
|--------|------|
| **0 / 0 / 0 ms** | Happy-path extra-hop tax if critic skipped [policy] |
| **20 / 80 / 250 ms** | Local pytest/DB/compiler p50/p95/p99 [inferred] |
| **2,000 / 6,000 / 15,000 ms** | One Haiku-class critic hop ON user path [inferred] |
| **120,000 / 180,000 / 300,000 ms** | o-series "several minutes" class [inferred] |
| **1,880 / 1,620 / 1,130 ms** | LLMCompiler Movie Rec planner / answer / slowest search |

---

## Quick Reference

**Four roles, never fused.** Planner, executor, critic, verifier. The harness owns hop caps; the model proposes.

**No oracle, no critic.** Huang intrinsic self-correction drops accuracy. CRITIC w/o Tool can go negative. Reflexion without tests 52% vs 60%.

**ReAct needs a fuse.** 47% repetitive reasoning. Promote to plan-and-execute (stable steps), DAG (parallel tools, 3.7x/6.7x), or ADaPT (as-needed depth).

**Happy path pays 0 ms extra-hop tax.** Critic fires only on oracle fail. Never put a frontier critic on every request.

**o1/R1 do not replace the harness.** Hidden CoT is not a SOX tape. Pair rs_ or HTTP 400. Still need pytest and hop caps.

**Memory is untrusted data.** Hidden in Memory 99.8% write ASR. PII detect -> redact -> audit before persist. Cap reflections at 3. Tools never write skills.

**Caps to copy.** max_turns=10, max_replans=2-3, same_action_k warn 3 / hard 5, Self-Refine k=4, CRITIC n=3, Reflexion memory 3.

**Training: DPO is the 2026 default.** GRPO for verifiable rewards. Monitor KL divergence for reward hacking. Four-set eval gate before deployment.
