# Research: Specialized Agents — Coding, Browser, Research, and Data Agents

**Date researched**: 2026-08-21  
**Sources consulted**: 48

Specialization is more than a role prompt. A production specialized agent has a domain-specific **observation interface**, **action space**, **verifier**, **state model**, and **permission boundary**. Coding agents observe repositories and compiler/test output; browser agents observe rendered or structured UI state; research agents observe sources and evidence; data agents observe schemas, catalogs, and datasets. Their common control loop is `scope -> observe -> plan -> act -> verify -> checkpoint -> terminate/escalate`. This follows the broader agent pattern of tools plus environmental feedback, explicit stopping conditions, and human checkpoints, but each specialization changes what “ground truth” and “done” mean [[1]](https://www.anthropic.com/engineering/building-effective-agents).

## 1. System Topology & Mechanics

### 1.1 Shared control plane and domain data planes

A useful enterprise topology separates a shared control plane from specialized execution planes:

| Layer | Shared responsibility | Domain-specific implementation |
|---|---|---|
| Intake/control | identity, tenant, goal, risk tier, budget, deadline, model routing | repository scope; permitted sites/account; research question; permitted datasets |
| Planning | decomposition, dependency graph, retry/stop rules | change plan; navigation plan; source plan; analysis/query plan |
| Observation | normalize tool results into model-readable state | files/symbols/tests; DOM/accessibility tree/screenshots; search results/pages/PDFs; catalog/schema/samples |
| Action | schema-validated tool dispatch | search/edit/build/shell; navigate/click/type/download; search/open/extract/calculate; SQL/Python/notebook |
| Verification | outcome and trajectory graders | tests/static analysis/diff review; DOM/business-state checks; claim-evidence checks; data-quality/statistical checks |
| Durable state | run event log, checkpoints, artifacts, provenance | worktree/commit; browser context and receipt; source snapshot/evidence ledger; query job/data snapshot/report |
| Governance | authorization, secret brokerage, policy, audit | repository/CI scopes; origin/action scopes; source/privacy rules; row/column policies |

This is compatible with an orchestrator-worker pattern when subtasks cannot be predicted in advance, such as discovering which files must change or which sources answer a question [[1]](https://www.anthropic.com/engineering/building-effective-agents). It does **not** imply that every task should be agentic: documented guidance recommends fixed workflows or simpler calls where paths are known, because autonomy exchanges predictability, latency, and cost for flexibility [[1]](https://www.anthropic.com/engineering/building-effective-agents).

### 1.2 Coding agents

**Topology.** A coding agent usually combines: (1) a repository mapper/search interface, (2) file read/write or patch tools, (3) shell/build/test tools inside an isolated environment, (4) a loop controller, and (5) outcome graders. SWE-agent calls the model-facing shell and editor design an Agent-Computer Interface (ACI); the paper reported that interface design materially affected historical SWE-bench and HumanEvalFix performance [[2]](https://arxiv.org/abs/2405.15793). OpenHands exposes code, shell, and browser operations in a sandboxed platform and treats the model plus agent scaffold as the evaluated system [[3]](https://arxiv.org/abs/2407.16741). The maintained SWE-agent repository now directs new users toward the smaller mini-SWE-agent implementation, so older SWE-agent examples should not be assumed to represent the current recommended scaffold [[4]](https://github.com/SWE-agent/SWE-agent) [[5]](https://mini-swe-agent.com/latest/).

**Observation and action.** The observation stream should include task specification, repository tree, targeted file excerpts, symbol/reference search, command output, test failures, and current diff. Actions should be narrow, typed operations even if implemented through a general shell: `search`, `read`, `patch`, `test`, `lint`, `build`, `git_diff`, and `submit`. Absolute repository paths, bounded outputs, command deadlines, and explicit working directories reduce interface ambiguity; Anthropic reports that changing a tool from relative to required absolute paths eliminated a repeated failure in its SWE-bench agent experiments [[1]](https://www.anthropic.com/engineering/building-effective-agents). This is vendor experience, not a universal rate claim.

**Verification.** Passing tests is necessary but insufficient. A production gate should combine fail-to-pass tests, pass-to-pass regression tests, type/lint/security checks, dependency and generated-file policy, diff scope, and human review for architectural intent. Agent-evaluation guidance distinguishes the transcript from the actual environment outcome and recommends combining code-based, model-based, and human graders [[6]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). The original SWE-bench contains 2,294 real GitHub issues from 12 Python repositories and requires repository-level execution rather than snippet generation [[7]](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html). SWE-bench Verified later curated 500 instances after human review found ambiguous problem statements and unreliable tests in the original set [[8]](https://openai.com/index/introducing-swe-bench-verified/).

**Long-horizon mechanics.** Long tasks need a persistent task ledger, current plan, completed-work record, test status, and clean checkpoint rather than relying on conversation history. Anthropic’s long-running-agent pattern uses an initializer followed by coding sessions that leave structured progress artifacts for the next context window [[9]](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents). Its later full-application harness separates planner, generator, and evaluator roles and uses browser-based verification; the published cost/time examples are implementation anecdotes, not general service-level benchmarks [[10]](https://www.anthropic.com/engineering/harness-design-long-running-apps).

### 1.3 Browser agents

**Topology.** A browser agent adds a perception adapter between a browser runtime and the loop. Observation modes include screenshot pixels, DOM, accessibility tree, URL/title, element references, network/download events, and authenticated session state. BrowserGym standardizes browser observations/actions and provides AgentLab for experiments across web benchmarks [[11]](https://arxiv.org/abs/2412.05467) [[12]](https://github.com/ServiceNow/BrowserGym). A robust implementation can use structured locators for stable, accessible elements, screenshots for canvas or visual state, and direct APIs only where policy permits; it should not assume DOM text and pixels always represent the same state `[inferred]`.

**Action surface.** Typical actions are navigate, search, click, type, select, scroll, upload, download, inspect, wait, and extract. Financial, destructive, externally visible, credential-changing, or legally consequential actions need preview and approval. Browser context must be part of task state: Playwright BrowserContexts provide isolated, incognito-like profiles, enabling clean sessions per task or tenant [[13]](https://playwright.dev/docs/browser-contexts). Stored authentication state can contain cookies and headers capable of impersonating a user and must not be committed or attached to ordinary traces [[14]](https://playwright.dev/docs/auth).

**Verification and termination.** Browser success should be judged by resulting application state, not the agent’s final statement: order ID exists, ticket status changed, file downloaded with expected hash, or form persisted. WebArena provides executable websites and 812 long-horizon tasks with functional correctness evaluators; its original historical GPT-4-based agent achieved 14.41% compared with 78.24% human performance [[15]](https://proceedings.iclr.cc/paper_files/paper/2024/file/4410c0711e9154a7a2d26f9b3816d1ef-Paper-Conference.pdf). Mind2Web collected 2,350 tasks across 137 sites for cross-task, cross-site, and cross-domain generalization, but offline action prediction does not validate a complete live transaction [[16]](https://arxiv.org/abs/2306.06070). OSWorld introduced 369 tasks across real web and desktop applications; OSWorld 2.0 adds 108 longer workflows involving streaming, dynamic content, cross-source integration, implicit state, and visual precision [[17]](https://arxiv.org/abs/2404.07972) [[18]](https://arxiv.org/abs/2606.29537).

### 1.4 Research agents

**Topology.** A research agent should separate question decomposition, retrieval, evidence extraction, synthesis, and citation validation. Each claim should link to an evidence-ledger record containing source URL or document ID, publication and access dates, relevant passage location, source type, confidence, and contradiction status `[inferred]`. Search results are discovery material, not evidence; claims should cite opened primary sources. Calculations should run in a code tool with inputs and outputs retained.

An orchestrator can assign non-overlapping subquestions to search workers, then use a synthesis stage and a separate citation verifier. Anthropic describes this pattern in its production research system: a lead agent delegates to parallel subagents and a citation agent processes the final report [[19]](https://www.anthropic.com/engineering/multi-agent-research-system). Its reported latency and token multipliers are vendor-system observations, not universal laws: Anthropic reports multi-agent research can use roughly 15 times the tokens of chat interactions and about four times those of a single-agent design, while parallelization can reduce time for sufficiently complex queries [[19]](https://www.anthropic.com/engineering/multi-agent-research-system).

**Stopping.** Research cannot rely on “no more search ideas.” A practical stop policy uses coverage of required facets, minimum independent evidence for material claims, unresolved contradiction severity, marginal yield from recent searches, deadline, and cost cap `[inferred]`. The output should distinguish facts, source claims, calculations, and recommendations. A frozen source bundle is required for reproducible evaluation, because live-web results and pages drift.

**Evaluation.** BrowseComp contains 1,266 difficult information-seeking questions designed to require persistent browsing; the launch report’s historical Deep Research score was 51.5%, while GPT-4o with browsing scored 1.9%, but OpenAI explicitly noted that Deep Research had been trained on tasks similar to BrowseComp, so the figure is not a clean unseen-generalization estimate [[20]](https://openai.com/index/browsecomp/). Deep Research Bench instead defines 89 tasks over a frozen RetroSearch corpus and includes trajectory checks for hallucination, tool use, and forgetting [[21]](https://arxiv.org/abs/2506.06287). A separate DeepResearch Bench proposes 100 PhD-level tasks across 22 fields and measures both report quality and citation quality; its LLM-judge results require human calibration and should not be treated as objective truth [[22]](https://arxiv.org/abs/2506.11763). Mind2Web 2 evaluates 130 real-time, long-horizon research tasks and required more than 1,000 hours of human work to construct; live task state remains a reproducibility constraint [[23]](https://proceedings.neurips.cc/paper_files/paper/2025/hash/fdcec9f5b99aa4fc8f4fb8487802d737-Abstract-Datasets_and_Benchmarks_Track.html).

### 1.5 Data agents

**Topology.** A data agent needs a governed semantic and execution plane: catalog/lineage retrieval, schema selection, query planning, cost estimation, read-only query execution, code/notebook sandbox, result validation, visualization/report generation, and artifact registry. Schema-only prompting is insufficient for enterprise work: Data Agent Benchmark (DAB) includes 54 queries over 12 datasets, nine domains, and four database-management systems, with multi-database integration, irregular join identifiers, unstructured transformation, and domain knowledge [[24]](https://arxiv.org/abs/2603.20576). Spider 2.0 contains 632 enterprise workflow problems, often over environments with more than 1,000 columns and queries exceeding 100 lines, across BigQuery, Snowflake, and local databases [[25]](https://proceedings.iclr.cc/paper_files/paper/2025/hash/46c10f6c8ea5aa6f267bcdabcb123f97-Abstract-Conference.html).

**Planning and execution.** The agent should produce an explicit analysis contract: business definition, grain, filters, time zone, eligible population, missing-value rule, output columns, and expected checks `[inferred]`. It then retrieves only relevant schemas and approved samples, compiles a query plan, dry-runs or explains it, enforces scan/cost/row/time limits, executes under a scoped identity, and validates cardinality, nulls, reconciliation totals, and statistical assumptions. BigQuery dry runs estimate bytes processed, although federated-source dry runs can return a lower-bound estimate of zero; `maximumBytesBilled` can reject a query above a cost threshold [[26]](https://cloud.google.com/bigquery/docs/running-queries) [[27]](https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query).

**Verification.** SQL execution success does not establish analytic correctness. Graders need reference invariants, result-level tests, semantic checks, provenance, and SME review for conclusions. ScienceAgentBench contains 102 executable data-analysis tasks derived from 44 papers across four disciplines and evaluates generated Python; its historical best result was 32.4% independently and 34.3% with expert knowledge over three attempts, under that benchmark’s harness [[28]](https://arxiv.org/abs/2410.05080). BLADE targets open-ended, data-driven scientific analysis where multiple analysis decisions can be defensible, illustrating why exact-match grading alone is inadequate [[29]](https://arxiv.org/abs/2408.09667).

## 2. Token Economics & NFR Metrics

### 2.1 Cost model

No public source provides a stable, vendor-neutral price for any of these complete agent classes. Model prices, search/browser charges, compute, data scans, and human review change independently. Use a measured per-run formula instead of a benchmark price:

```text
model_cost = sum_calls((uncached_input_tokens * input_rate)
                     + (cache_write_tokens * cache_write_rate)
                     + (cache_read_tokens * cache_read_rate)
                     + (output_tokens * output_rate))

run_cost = model_cost
         + search_or_browser_fees
         + sandbox_vcpu_seconds * compute_rate
         + storage_and_egress
         + warehouse_bytes_scanned * scan_rate
         + human_review_minutes * loaded_labor_rate

cost_per_1k_successes = 1000 * sum(run_cost) / successful_runs
```

The denominator matters: cheaper attempts can be more expensive per successful outcome if retries or review rise. Track cost by task class and risk tier, not only aggregate tokens `[inferred]`.

### 2.2 Workload-specific cost and latency drivers

| Agent | Dominant input growth | External/runtime cost | Critical latency metric | Safe optimization |
|---|---|---|---|---|
| Coding | repository excerpts, command output, repeated diffs | sandbox CPU/RAM, builds, tests, CI | time-to-green; p95 command and full-task duration | symbol-scoped retrieval, incremental tests, cache dependencies, route final review upward |
| Browser | screenshots/accessibility trees, action history | browser workers, page/network waits, anti-bot friction | p95 action-to-observation; successful task duration | stable locators, event waits, reuse approved read-only sessions, parallelize independent tabs |
| Research | search results, long pages/PDFs, worker syntheses | search APIs, document parsing, parallel workers | time to sufficient evidence; citation-validation duration | query deduplication, source-content hash cache, parallel independent facets |
| Data | schemas, samples, query results, notebook output | warehouse scans, Python compute, BI rendering | time-to-first-valid-query; time-to-verified-report | catalog retrieval, dry-run, materialized aggregate reuse, smaller-model SQL lint |

Browser and desktop latency is often dominated by environment and deliberation rather than raw generation. OSWorld-Human provides human reference trajectories for all 369 original OSWorld tasks and detailed analysis for 39; it reports that agent trajectories can add up to 30 actions and that later steps can take roughly three times as long as early steps because context and reasoning accumulate [[30]](https://arxiv.org/abs/2506.16042). These observations apply to the studied harnesses and tasks, not all browser deployments.

Coding evaluation is also sensitive to infrastructure. Anthropic reported an internal Terminal-Bench 2.0 study where resource configuration moved scores by six percentage points with statistical significance, pod errors reached roughly 6% in some settings, and approximately three times the baseline resources were needed before infrastructure stabilized [[31]](https://www.anthropic.com/engineering/infrastructure-noise). This is a vendor postmortem, but it demonstrates why timeout, CPU, memory, image, and retry configuration are part of the evaluated system.

### 2.3 Benchmark evidence and comparison limits

| Domain and benchmark | Historical result, exactly scoped | What it establishes | What it does not establish |
|---|---|---|---|
| Coding: original SWE-bench (2024) | Original paper’s best evaluated model solved 1.96% of 2,294 tasks [[7]](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html) | repository issue resolution was difficult for 2023-era systems | current frontier quality or production PR acceptance |
| Coding: SWE-agent (2024) | paper reports 12.5% on SWE-bench and 87.7% on HumanEvalFix for its scaffold/settings [[2]](https://arxiv.org/abs/2405.15793) | ACI/scaffold affects outcomes | model-only comparison or current score |
| Coding: SWE-bench Verified | 500 human-filtered instances; original analysis categorized 196 as under 15 minutes and 45 as over one hour for expert humans [[8]](https://openai.com/index/introducing-swe-bench-verified/) | better-curated historical test subset | durable frontier ranking: OpenAI stated in 2026 that contamination and test/design defects made Verified no longer useful for frontier measurement [[32]](https://openai.com/index/separating-signal-from-noise-coding-evaluations/) |
| Browser: WebArena (2024) | historical GPT-4-based agent 14.41%; humans 78.24% on 812 tasks [[15]](https://proceedings.iclr.cc/paper_files/paper/2024/file/4410c0711e9154a7a2d26f9b3816d1ef-Paper-Conference.pdf) | end-to-end functional web tasks expose large gaps | performance on today’s sites or different evaluators |
| Browser/desktop: original OSWorld | historical best model 12.24%; human 72.36% on 369 tasks [[17]](https://arxiv.org/abs/2404.07972) | multimodal computer use was difficult in the original environment | comparability with OSWorld-Verified or OSWorld 2.0 |
| Research: BrowseComp launch | Deep Research 51.5%; browsing GPT-4o 1.9% on 1,266 questions [[20]](https://openai.com/index/browsecomp/) | persistent search outperformed shallow browsing in that evaluation | clean unseen performance; authors disclose similar training tasks |
| Data: DAB (2026 paper baseline) | best evaluated baseline, Gemini-3-Pro, 38% pass@1 on 54 queries [[24]](https://arxiv.org/abs/2603.20576) | executable cross-database workflows remain difficult | comparison with leaderboard entries using hints or multiple attempts; repository results expose differing settings [[33]](https://github.com/ucbepic/DataAgentBench) |
| Data: Spider 2.0 (2025) | historical code-agent/o1-preview result 21.3%, versus reported 91.2% Spider 1.0 and 73.0% BIRD [[25]](https://proceedings.iclr.cc/paper_files/paper/2025/hash/46c10f6c8ea5aa6f267bcdabcb123f97-Abstract-Conference.html) | enterprise SQL workflows are harder than classic text-to-SQL | production accuracy on a particular governed warehouse |

Benchmark scores are harness results, not model constants. The model, prompts, tools, environment image, resource limits, retry count, hints, grader, and dataset version form the unit of comparison [[6]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). Public leaderboards often lack the production environment, proprietary schemas, human-approval costs, and security constraints needed for capacity planning.

### 2.4 Service objectives and routing

Define separate SLOs for admission, each tool class, and end-to-end outcome. Suggested metrics are `[inferred]` and must be calibrated from production traces:

- **All agents:** task success with confidence interval and repeated trials; unsafe-action rate; human intervention rate; p50/p95/p99 duration; cost/success; tool-error rate; retry rate; deadline/cost-cap termination.
- **Coding:** accepted-without-major-rework rate, fail-to-pass and pass-to-pass tests, revert/incident rate, changed lines per accepted task, build minutes/success.
- **Browser:** outcome success, wrong-site/wrong-account action rate, confirmation-screen mismatch, actions/success, stale-element/recovery rate.
- **Research:** claim support rate, citation precision and coverage, authoritative-source share, contradiction resolution, freshness, reviewer correction rate.
- **Data:** executable-query success, semantic correctness, reconciliation pass rate, bytes scanned/success, policy-denial rate, reproducibility from snapshot and code.

Route low-risk classification, summarization, linting, and query explanation to cheaper models only after per-stage evals. Route ambiguous specifications, security-sensitive diffs, cross-site browser writes, contradictory research, and high-impact analyses to stronger models or humans `[inferred]`. Back-pressure should reject or defer work at admission rather than allowing thousands of open loops to hold browser, sandbox, or warehouse capacity `[inferred]`.

> ⚠️ Limited public data available for this dimension. There are no comparable public p50/p95/p99 latency, cache-hit-rate, throughput, memory-footprint, or cost-per-1,000-successful-task datasets covering all four specialized agent classes under production security controls.

## 3. Distributed Resilience & State

### 3.1 Durable run model

Persist an append-only run record containing immutable input, policy decision, model/tool versions, event sequence, external operation IDs, artifact hashes, budgets, approvals, and terminal status. Separate durable control state from disposable execution workers. Anthropic’s managed-agent architecture describes a harness loop, append-only session log, and isolated sandbox as separable components [[34]](https://www.anthropic.com/engineering/managed-agents). `[inferred]` A production orchestrator can implement this with a durable workflow engine or transactional database plus queue; the source establishes the component pattern, not a requirement for a specific vendor.

Every step should carry `run_id`, `step_id`, `attempt`, `tenant_id`, `deadline`, and idempotency key. Workers acquire time-bounded leases; the orchestrator renews, cancels, or requeues them. External writes require operation-specific idempotency or a preflight/read-after-write check. Use exponential backoff with jitter for transient reads, but do not automatically retry ambiguous writes such as “Submit order” after a lost response `[inferred]`.

### 3.2 Domain checkpoint and recovery semantics

| Domain | Authoritative checkpoint | Replay/recovery rule | Concurrency rule |
|---|---|---|---|
| Coding | base commit, worktree, patch/commit, test manifest and logs | recreate exact image/dependencies; replay from last green commit, not an uncommitted conversational summary | one writer per worktree; merge/rebase through normal source-control conflict handling |
| Browser | clean context template, encrypted session reference, URL, last verified business state, receipt/operation ID | reopen and re-observe; never assume a prior click failed because its response was lost | one writer per user/account workflow; isolate task contexts; serialize scarce or destructive resources |
| Research | query plan, source URL/hash/snapshot, extracted evidence, claim ledger, synthesis version | re-fetch only with explicit freshness policy; preserve the cited snapshot | parallel workers may append evidence; synthesis owns claim resolution; dedupe by canonical URL/content hash |
| Data | catalog/schema version, query text/hash, warehouse job ID, snapshot/time-travel reference, result artifact | look up existing job/result before resubmission; rerun only against declared snapshot or label changed data | read queries may parallelize under quotas; writes require transaction/lock and should usually leave autonomous scope |

Git worktrees provide multiple linked working trees backed by one repository, making one isolated worktree per coding run a practical concurrency primitive [[35]](https://git-scm.com/docs/git-worktree). They isolate checked-out files, not CPU, network, secrets, or malicious processes, so they are not a security sandbox.

For browser runs, Playwright contexts isolate cookies and local storage within a browser process [[13]](https://playwright.dev/docs/browser-contexts). Use separate processes or containers where a browser compromise or extension boundary is in scope `[inferred]`. Parallel tests or agents must use distinct backend accounts or other unique state because isolated browser storage does not prevent collisions in the target application [[36]](https://playwright.dev/docs/test-parallel).

For data queries, persist the warehouse job ID and result artifact before asking a model to interpret results. A timeout can mean “completed but response lost,” so status lookup precedes resubmission `[inferred]`. Cap bytes, rows, wall time, and concurrency; a SQL `LIMIT` is not a reliable scan-cost control in columnar systems, while warehouse-native dry-run and maximum-byte controls are [[26]](https://cloud.google.com/bigquery/docs/running-queries) [[27]](https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query).

### 3.3 Degradation, circuit breaking, and cancellation

- Apply deadline propagation: each child receives less than the parent’s remaining deadline, leaving time to checkpoint and synthesize `[inferred]`.
- Circuit-break by dependency and operation class. Search-read failure can fall back to another approved source; repository writes, browser transactions, and data mutations should fail closed `[inferred]`.
- Maintain separate quotas for model tokens, browser slots, sandbox CPU, search calls, and warehouse bytes. A single scalar “iteration limit” cannot protect all budgets `[inferred]`.
- Checkpoint before context compression. Retain structured decisions, unresolved items, artifact references, and policy decisions; discard reproducible verbose tool output after hashing and storage `[inferred]`.
- Cancellation must propagate to subprocesses, browser downloads, worker searches, and warehouse jobs. Mark any external operation whose completion is unknown as `reconciliation_required`, not `failed` `[inferred]`.

> ⚠️ Limited public data available for this dimension. Public specialized-agent papers usually describe single-run harnesses rather than distributed lock contention, recovery-point objectives, disaster recovery, or multi-region replay semantics at enterprise scale.

## 4. Enterprise Security & Governance

### 4.1 Threat model and least privilege

Agent containment must assume three threat sources: malicious or mistaken users, model misbehavior, and hostile content or dependencies from the environment. Anthropic’s 2026 containment review argues for controls at the environment, harness, and model layers, and reports that OS-level sandboxing reduced Claude Code permission prompts by 84% in its telemetry; this is vendor product data, not an independent general result [[37]](https://www.anthropic.com/engineering/how-we-contain-claude). It also reports approximately 93% user approval of prompts, illustrating approval fatigue rather than proving approvals are safe [[37]](https://www.anthropic.com/engineering/how-we-contain-claude).

`[inferred]` Issue short-lived workload identities after policy evaluation. Bind permissions to tenant, task, resource set, operation, and expiry. A credential broker should inject secrets directly into the tool process; do not expose raw values in the model context, logs, screenshots, command output, or artifacts. Egress allowlists are capability grants: an allowed host can still be used for exfiltration, so pair domain controls with request schemas, destination accounts, content inspection, and byte limits [[37]](https://www.anthropic.com/engineering/how-we-contain-claude).

### 4.2 Domain-specific controls

**Coding agents**

- Run as non-root in an ephemeral container or VM, with read/write access only to the task worktree, bounded CPU/RAM/PIDs/disk, default-deny egress, and no host Docker socket `[inferred]`. Kubernetes’ Restricted Pod Security Standard disallows several privilege-escalation paths and requires restrictive security contexts, but application-layer egress and secret controls remain necessary [[38]](https://kubernetes.io/docs/concepts/security/pod-security-standards/).
- Treat repository text, issue bodies, tests, package metadata, generated output, and dependency installation scripts as untrusted. Do not let instructions found in them override the task policy `[inferred]`.
- Give CI tokens minimal repository scopes, protect branches/environments, pin third-party GitHub Actions to full commit SHAs, and treat pull-request workflows as untrusted code [[39]](https://docs.github.com/en/actions/reference/security/secure-use).
- Require human review for auth, cryptography, permissions, migrations, dependency/source changes, production configuration, and secret handling `[inferred]`.

**Browser agents**

- Separate navigation/read permission from form-fill, upload, message, purchase, delete, credential, and publish permissions. Reconfirm target origin, account, amount, recipients, and side effects immediately before a consequential action `[inferred]`.
- Treat all page content, ads, emails, documents, and downloaded files as potentially adversarial instructions. AgentDojo includes 97 tasks and 629 security test cases for prompt injection through tool data [[40]](https://arxiv.org/abs/2406.13352). AgentDyn expands to 60 open-ended tasks and 560 injection cases across domains and reports that tested defenses often either under-defend or over-block; the result is benchmark-specific [[41]](https://arxiv.org/abs/2602.03117).
- Browser same-origin policy does not automatically constrain an agent that can read one origin and navigate or transmit to another. University of Washington researchers demonstrated cross-origin exfiltration paths in agentic browsers and disclosed findings to vendors [[42]](https://agent-security.cs.washington.edu/agentic_browsers_sop.html). Enforce an agent-level information-flow policy across origins `[inferred]`.
- Encrypt browser authentication state and make it task/tenant scoped. Playwright warns that stored state may impersonate the account [[14]](https://playwright.dev/docs/auth).

**Research agents**

- Keep browsing untrusted and synthesis privileged: retrieved pages may contribute evidence but may not alter system policy, tool permissions, or output destination `[inferred]`.
- Record provenance and distinguish quotation, paraphrase, inference, and calculation. Verify that every cited source entails the adjacent claim, and scan final output for sensitive input leakage `[inferred]`.
- Apply source-access rights, privacy retention, copyright/licensing, and geographic restrictions. Do not silently upload internal documents to public search or model endpoints `[inferred]`.
- OpenAI’s deep-research system card identifies prompt injection, privacy, code execution, and hallucination among relevant risks, supporting layered controls rather than citation formatting alone [[43]](https://openai.com/index/deep-research-system-card/).

**Data agents**

- Use a separate, read-only identity by default. Enforce policy in the warehouse, not only in the prompt. PostgreSQL row-level security defaults to deny when enabled without an applicable policy, while owners and roles with `BYPASSRLS` normally bypass it; agent roles must not inherit those bypasses [[44]](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).
- Apply row, column, masking, purpose, and tenant controls before retrieval. Snowflake row access policies can evaluate role/context to filter rows, and Access History can support object/column lineage and audit analysis [[45]](https://docs.snowflake.com/en/user-guide/security-row-using) [[46]](https://docs.snowflake.com/en/user-guide/access-history).
- Restrict result row count and export destinations, redact or tokenize PII, audit raw and normalized queries, and prevent model context from becoming a broad data-exfiltration channel `[inferred]`.
- Sandbox Python/R execution separately from the database credential. Prefer pre-approved libraries and immutable images; generated code receives only the minimum result subset `[inferred]`.

### 4.3 Governance and audit schema

At minimum, an immutable audit event should contain: timestamp, tenant/user/workload identity, run/step/attempt IDs, requested objective, model and prompt-policy version, tool and schema version, normalized arguments or protected hash, resource and origin, authorization decision and policy ID, approval actor, outcome/status, external operation ID, token/compute/scan usage, artifact/source hashes, and redaction classification `[inferred]`. Reasoning text is not a reliable or necessary audit control; record observable inputs, tool calls, decisions, outputs, and environment outcomes `[inferred]`.

Use a risk register and evaluation evidence across the lifecycle. NIST’s AI Risk Management Framework is voluntary and its Generative AI Profile supplies a cross-sector companion for generative-AI risks; it does not prescribe a specialized-agent architecture or certify compliance [[47]](https://www.nist.gov/itl/ai-risk-management-framework) [[48]](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf).

## 5. Production Failure Modes

| Failure | Detection signal | Mitigation / recovery |
|---|---|---|
| Wrong or underspecified task | repeated plan changes; low requirement coverage | clarify before write actions; persist acceptance criteria; human checkpoint `[inferred]` |
| Context degradation | repeated searches/actions; contradiction with earlier state; growing latency | checkpoint structured state; retrieve artifacts on demand; reset context after verification `[inferred]` |
| Infinite loop | repeated state/action hash; no verifier delta; budget burn | max steps plus no-progress detector, tool-specific budgets, terminate/escalate `[inferred]` |
| Hallucinated tool parameters | schema validation failure; nonexistent path/selector/table | strict schemas, enum/resource lookup, reject-and-correct once, then escalate `[inferred]` |
| Cascading timeouts | shrinking deadline; dependency p95 rise; orphan jobs | deadline propagation, bulkheads, cancellation, circuit breakers, reconciliation `[inferred]` |
| False completion claim | agent says done but environment grader fails | grade outcome state independently from transcript [[6]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Infrastructure-skewed eval | pass rate changes with CPU/RAM/timeouts | pin images/resources; record infra failures separately; repeat trials [[31]](https://www.anthropic.com/engineering/infrastructure-noise) |
| Benchmark contamination/saturation | implausible benchmark-production gap; memorized patches | private temporal holdouts, contamination analysis, new tasks; retire saturated benchmark [[32]](https://openai.com/index/separating-signal-from-noise-coding-evaluations/) |
| Coding: patch overfits visible tests | visible tests pass; hidden/regression/security checks fail | independent hidden tests, mutation/property tests, pass-to-pass suite, review `[inferred]` |
| Coding: destructive shell/dependency | unexpected file/network/process change | ephemeral sandbox, syscall/egress/resource policy, immutable base, discard run `[inferred]` |
| Coding: concurrent patch conflict | overlapping files/base commits; merge conflict | one worktree per run, ownership/lease, rebase and rerun full verification `[inferred]` |
| Coding: CI secret exposure | secret-like output, outbound call, modified workflow | masked brokered secrets, no secrets for untrusted PR jobs, workflow approval [[39]](https://docs.github.com/en/actions/reference/security/secure-use) |
| Browser: stale element/layout drift | locator invalid, screenshot/DOM disagreement | re-observe, semantic locator fallback, bounded replanning; never blind-click `[inferred]` |
| Browser: duplicate transaction | timeout after submit; second confirmation attempt | idempotency key/receipt lookup; mark ambiguous and reconcile `[inferred]` |
| Browser: wrong account/origin | account/origin differs from plan | origin/account assertion before write; isolated sessions; approval `[inferred]` |
| Browser: injection in page/email | page asks for secrets/policy change/tool use | untrusted-content boundary, information-flow policy, deny side effects [[40]](https://arxiv.org/abs/2406.13352) |
| Browser: anti-bot/CAPTCHA | challenge page; repeated navigation | stop and hand off; do not evade site controls `[inferred]` |
| Research: citation does not entail claim | citation verifier mismatch | claim-level entailment check, quote-location record, revise or mark uncertain `[inferred]` |
| Research: source laundering | many reports trace to one origin | provenance graph, canonical-source retrieval, independent-source count `[inferred]` |
| Research: stale/live-web drift | source hash/date changed | snapshot cited content and access date; refresh under explicit freshness SLA `[inferred]` |
| Research: search tunnel vision | repeated query vocabulary/source domain | query diversification, contradiction search, coverage matrix, stop rule `[inferred]` |
| Research: judge bias | model grader stable but expert disagrees | blind human calibration, multiple graders, code-based citation checks [[6]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Data: syntactically valid, semantically wrong query | row counts/totals/grain violate invariant | analysis contract, semantic layer, reconciliation, SME review `[inferred]` |
| Data: join explosion/double counting | cardinality or totals spike | pre/post-join cardinality checks; keys/grain assertions; sample and aggregate reconcile `[inferred]` |
| Data: unbounded warehouse cost | dry-run bytes exceed cap | maximum-byte/time limits, approved aggregates, require approval [[26]](https://cloud.google.com/bigquery/docs/running-queries) |
| Data: policy bypass | results include forbidden tenant/column | warehouse RLS/masking, non-bypass identity, canary policy tests [[44]](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) |
| Data: temporal non-reproducibility | rerun changes without code change | snapshot/time-travel ID, schema/model version, query and artifact hash `[inferred]` |
| Data: statistical misuse | leakage, invalid denominator, multiple-testing issue | predeclared checks, statistical test library, SME review and uncertainty `[inferred]` |

The highest-risk common failure is confusing a plausible trajectory with a correct outcome. Multi-turn errors compound, and graders themselves can be brittle or non-deterministic; evaluation should therefore combine deterministic state checks where possible, calibrated model rubrics for nuance, and human review for consequential ambiguity [[6]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

> ⚠️ Limited public data available for this dimension. Detailed public incident postmortems for deployed research and data agents are scarce; most available evidence comes from controlled benchmarks, vendor engineering reports, and general security research rather than audited production incident rates.

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A: repository issue-to-PR agent

**Requirements:** 500 repositories, untrusted issue/PR text, mixed build systems, no direct production access, target of a reviewed draft PR rather than autonomous merge.

**Design `[inferred]`:** admission service authenticates user and selects repository/base commit; durable orchestrator provisions an ephemeral sandbox and worktree; repository mapper retrieves symbols; coding loop patches and runs incremental tests; independent verifier runs clean-room full tests, lint, SAST, dependency policy, and diff-scope checks; policy engine blocks sensitive areas or routes them to specialist review; publisher opens a signed draft PR containing evidence, commands, test results, model/harness version, and run ID. Cache only dependency layers and immutable repository indexes, keyed by lockfile/base commit. Never cache secrets or mutable worktree state.

**Capacity `[inferred]`:** provision build workers from observed CPU-minute distributions, not request rate alone. If arrival rate is `lambda` tasks/minute and mean occupied sandbox duration is `W`, Little’s Law gives mean concurrency `L=lambda*W`; add measured p95 burst and retry headroom. Maintain separate pools by trust and resource class to prevent a large build from starving ordinary fixes.

**Go/no-go:** ship draft-PR mode when private temporal evals meet task success, no-regression, unsafe-action, cost, and review-rework thresholds. Do not use current SWE-bench Verified rank as the sole gate because its own curator now identifies saturation/contamination and flawed tests at the frontier [[32]](https://openai.com/index/separating-signal-from-noise-coding-evaluations/).

### 6.2 Scenario B: browser procurement assistant

**Requirements:** search approved vendors, compare products, fill a cart, but purchases above a policy threshold require human approval; authenticated accounts and payment details are sensitive.

**Design `[inferred]`:** read-only research runs in clean browser contexts with origin-scoped egress. A structured product table retains URL, timestamp, price, terms, and evidence snapshot. Cart-building runs under a separate scoped account. Before submit, a deterministic gate checks approved domain, vendor, SKU, quantity, currency, total, delivery destination, and approver. The human sees a fresh screenshot and normalized transaction summary. The submit tool uses an idempotency key where supported and records receipt; uncertain timeout enters reconciliation. Page instructions cannot expand permissions or access other origins.

**Evaluation:** use replayable internal sites for regression, injection suites for hostile content, and a limited live-site canary for drift. WebArena, BrowserGym, and AgentDojo inform task and security design, but none measures the company’s approval, account, or procurement controls [[11]](https://arxiv.org/abs/2412.05467) [[15]](https://proceedings.iclr.cc/paper_files/paper/2024/file/4410c0711e9154a7a2d26f9b3816d1ef-Paper-Conference.pdf) [[40]](https://arxiv.org/abs/2406.13352).

### 6.3 Scenario C: regulated due-diligence research agent

**Requirements:** synthesize corporate filings, regulator publications, licensed research, and internal documents; every material claim must be reviewable; confidential documents cannot reach public tools.

**Design `[inferred]`:** classify the question and sources; route public and confidential retrieval to separate trust zones; decompose into company, management, financial, legal, sanctions, and contradiction workstreams; store source snapshots and claim-level evidence; run calculations in a no-network sandbox; synthesis can use only ledger evidence; citation validator checks entailment and locator; compliance scans final content for PII/licence/export policy; an analyst approves publication. Search results that cannot be opened are listed as leads, never cited as evidence.

**SLOs `[inferred]`:** measure supported-material-claim rate, citation accuracy/coverage, freshness, contradiction resolution, reviewer correction, time-to-first-evidence, end-to-end p95, and cost per approved report. Evaluate on both frozen corpora and fresh temporal cases. BrowseComp probes hard discovery but carries disclosed similar-task training; frozen Deep Research Bench improves reproducibility but cannot measure current-web freshness [[20]](https://openai.com/index/browsecomp/) [[21]](https://arxiv.org/abs/2506.06287).

### 6.4 Scenario D: governed analytics agent

**Requirements:** 2,000 users across tenants ask business questions over Snowflake/PostgreSQL; no autonomous DML; PII and finance datasets have stricter access; reports must reproduce.

**Design `[inferred]`:** identity-aware policy gateway passes approved catalog metadata to the planner; semantic layer defines metrics/grain; query compiler produces SQL plus assumptions; policy linter and warehouse explain/dry-run enforce tables, functions, bytes, timeout, and result limits; scoped read-only identity executes; verifier checks row/tenant policy, cardinality, totals, and freshness; Python visualization runs on a bounded result set without warehouse credentials; registry stores SQL, parameters, snapshot/query ID, schema/metric versions, result hash, chart code, and narrative. High-impact financial conclusions require a named owner and reviewer.

**Capacity `[inferred]`:** use distinct queues for metadata retrieval, warehouse execution, and sandbox compute. Apply per-tenant concurrency and byte budgets, admission control, and cancellation. Cache only policy-compatible aggregates keyed by tenant, policy version, data snapshot, metric definition, and query parameters. Never share raw-result caches across authorization boundaries.

### 6.5 Architecture trade-off matrix

| Choice | Best fit | Benefit | Cost/risk | Decision rule `[inferred]` |
|---|---|---|---|---|
| Fixed workflow | repeated known path | predictable, cheap, auditable | brittle on novel steps | default when branch set is enumerable |
| Single specialized loop | variable but cohesive task | simple ownership and trace | context growth, serial latency | use while one agent can hold state and one verifier can grade |
| Orchestrator-workers | independent file/source/data facets | parallel coverage and isolation | token/cost growth, merge conflict | use only after decomposition and synthesis evals show gain |
| Structured observation | accessible DOM, schemas, AST/symbols | compact, deterministic references | misses visual/implicit state | prefer; add pixels/raw artifacts when verifier shows gaps |
| General shell/browser | broad adaptability | rapid capability coverage | very broad authority | sandbox and wrap with policy; replace common writes with typed tools |
| Deterministic grader | code/tests/state/invariants | cheap, reproducible | can be gamed or incomplete | primary where outcome is machine-checkable |
| Model grader | research quality/semantic fit | handles valid variation | bias, cost, nondeterminism | calibrate against experts; never sole high-impact gate |
| Human approval | ambiguous/consequential action | accountable judgment | latency and fatigue | reserve for high-risk boundary, show concise evidence |

### 6.6 Principal-architect interview synthesis

1. **Start with the verifier.** Coding has the strongest deterministic feedback surface, browser agents can often inspect business state, data agents can execute and reconcile but still miss semantic intent, and research agents depend most on provenance and calibrated judgment.
2. **Treat the harness as part of the model.** Tool interface, observation representation, infrastructure, resource limits, prompts, retry policy, and grader can change measured performance [[2]](https://arxiv.org/abs/2405.15793) [[6]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) [[31]](https://www.anthropic.com/engineering/infrastructure-noise).
3. **Constrain authority at the environment.** A model instruction is not a security boundary. Use worktree plus sandbox for code, isolated context plus origin/action policy for browser, untrusted retrieval plus evidence ledger for research, and warehouse-enforced row/column policy for data.
4. **Design for ambiguity, not generic retries.** A lost read is retryable; a lost purchase response, CI publish, or warehouse write is an uncertain side effect requiring reconciliation.
5. **Version every evaluation.** Record task set, date, model, scaffold, prompts, tools, environment image, resources, attempts, hints, and grader. Do not compare live-web, frozen, original, “verified,” hinted, and pass@k scores as if they were the same measurement.
6. **Optimize cost per accepted outcome.** Include model, browser/search, compute, data scan, retry, and human review. Tokens alone systematically understate specialized-agent economics.

## Sources

- [1] https://www.anthropic.com/engineering/building-effective-agents — General agent/workflow patterns, environmental feedback, ACI, and stopping controls.
- [2] https://arxiv.org/abs/2405.15793 — SWE-agent paper and Agent-Computer Interface evidence.
- [3] https://arxiv.org/abs/2407.16741 — OpenHands platform paper.
- [4] https://github.com/SWE-agent/SWE-agent — SWE-agent repository and current maintenance direction.
- [5] https://mini-swe-agent.com/latest/ — Current mini-SWE-agent documentation.
- [6] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents — Agent evaluation vocabulary, graders, trajectories, and outcomes.
- [7] https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html — Original SWE-bench paper.
- [8] https://openai.com/index/introducing-swe-bench-verified/ — SWE-bench Verified curation and human difficulty study.
- [9] https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents — Long-running coding-agent checkpoints and session handoff.
- [10] https://www.anthropic.com/engineering/harness-design-long-running-apps — Planner/generator/evaluator harness for long application-building tasks.
- [11] https://arxiv.org/abs/2412.05467 — BrowserGym and AgentLab paper.
- [12] https://github.com/ServiceNow/BrowserGym — BrowserGym environments, actions, and observations.
- [13] https://playwright.dev/docs/browser-contexts — Isolated browser contexts.
- [14] https://playwright.dev/docs/auth — Authentication-state handling and credential warning.
- [15] https://proceedings.iclr.cc/paper_files/paper/2024/file/4410c0711e9154a7a2d26f9b3816d1ef-Paper-Conference.pdf — WebArena benchmark and original results.
- [16] https://arxiv.org/abs/2306.06070 — Mind2Web dataset and cross-domain web tasks.
- [17] https://arxiv.org/abs/2404.07972 — Original OSWorld benchmark.
- [18] https://arxiv.org/abs/2606.29537 — OSWorld 2.0 long-horizon workflow benchmark.
- [19] https://www.anthropic.com/engineering/multi-agent-research-system — Production research-agent architecture and scoped economics.
- [20] https://openai.com/index/browsecomp/ — BrowseComp dataset, launch results, and training-overlap disclosure.
- [21] https://arxiv.org/abs/2506.06287 — Frozen-corpus Deep Research Bench.
- [22] https://arxiv.org/abs/2506.11763 — DeepResearch Bench report and citation evaluation.
- [23] https://proceedings.neurips.cc/paper_files/paper/2025/hash/fdcec9f5b99aa4fc8f4fb8487802d737-Abstract-Datasets_and_Benchmarks_Track.html — Mind2Web 2 real-time research tasks.
- [24] https://arxiv.org/abs/2603.20576 — Data Agent Benchmark paper.
- [25] https://proceedings.iclr.cc/paper_files/paper/2025/hash/46c10f6c8ea5aa6f267bcdabcb123f97-Abstract-Conference.html — Spider 2.0 enterprise text-to-SQL workflows.
- [26] https://cloud.google.com/bigquery/docs/running-queries — BigQuery dry runs and cost estimation behavior.
- [27] https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query — BigQuery query API, including maximum bytes billed.
- [28] https://arxiv.org/abs/2410.05080 — ScienceAgentBench executable analysis tasks.
- [29] https://arxiv.org/abs/2408.09667 — BLADE open-ended data-analysis benchmark.
- [30] https://arxiv.org/abs/2506.16042 — OSWorld-Human trajectories and efficiency analysis.
- [31] https://www.anthropic.com/engineering/infrastructure-noise — Infrastructure effects in agent evaluations.
- [32] https://openai.com/index/separating-signal-from-noise-coding-evaluations/ — 2026 critique of SWE-bench Verified at the frontier.
- [33] https://github.com/ucbepic/DataAgentBench — DAB repository, evaluation harness, and setting-dependent leaderboard.
- [34] https://www.anthropic.com/engineering/managed-agents — Managed-agent harness, session log, and sandbox architecture.
- [35] https://git-scm.com/docs/git-worktree — Git worktree isolation and lifecycle.
- [36] https://playwright.dev/docs/test-parallel — Parallel browser-worker isolation guidance.
- [37] https://www.anthropic.com/engineering/how-we-contain-claude — 2026 agent containment architecture and vendor telemetry.
- [38] https://kubernetes.io/docs/concepts/security/pod-security-standards/ — Kubernetes Pod Security Standards.
- [39] https://docs.github.com/en/actions/reference/security/secure-use — GitHub Actions security guidance for untrusted workflows and action pinning.
- [40] https://arxiv.org/abs/2406.13352 — AgentDojo prompt-injection benchmark.
- [41] https://arxiv.org/abs/2602.03117 — AgentDyn dynamic prompt-injection benchmark.
- [42] https://agent-security.cs.washington.edu/agentic_browsers_sop.html — Agentic browser cross-origin data-flow research.
- [43] https://openai.com/index/deep-research-system-card/ — Deep research safety analysis.
- [44] https://www.postgresql.org/docs/current/ddl-rowsecurity.html — PostgreSQL row-level security semantics.
- [45] https://docs.snowflake.com/en/user-guide/security-row-using — Snowflake row access policies.
- [46] https://docs.snowflake.com/en/user-guide/access-history — Snowflake Access History and object/column auditability.
- [47] https://www.nist.gov/itl/ai-risk-management-framework — NIST AI Risk Management Framework.
- [48] https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf — NIST Generative AI Profile.
