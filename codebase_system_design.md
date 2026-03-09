# Codebase.zip System Design and Operating Model

## What this system is in one paragraph

Codebase.zip is a bounded autonomous software engineering runtime. It is built to turn a difficult specification, a codebase, and a small amount of value-level human input into aligned implementation work over long time horizons. It does this by separating mechanical control from reasoning. Scripts own the execution substrate: persistence, task routing, pause/resume, monitoring, and fail-closed behavior. Agents own the solve graph: what to explore, what problems exist, what structure is missing, what needs coordination, and when new work should be requested. The result is a system that is slower in wall-clock terms than an interactive coding assistant, but lower-interruption and more scalable in human time because it asks humans mostly for philosophy and tradeoff decisions rather than feature-level steering.

## What this system is not

Codebase.zip is not:
- a rigid waterfall
- a precomputed dependency DAG
- a static manager-worker tree
- a coding copilot that expects constant feature-level guidance
- a general-purpose product strategy, UX, or marketing system

It is software-engineering specific. Its ontology, task vocabulary, and agent roles are optimized for understanding a codebase, defining problems, shaping integration, implementing changes, coordinating cross-cutting effects, and converging on aligned software behavior.

## Explain it in one minute

Most coding systems either:
1. move fast and rely on the human to constantly correct them, or
2. pre-plan a workflow and then force the work through that structure.

Codebase.zip does neither.

Instead, it runs on a bounded substrate with durable memory, typed tasks, and short-lived agents. The agents are not organized around domains like frontend or backend. They are organized around epistemic operations: understanding, distilling, judging, expanding, adjudicating, coordinating, pruning, seeding, bridging, and monitoring. The system does not assume the full dependency graph up front. It discovers structure by trying to solve the problem, noticing insufficiency, and requesting more work only where needed. Humans mainly answer high-level philosophy and tradeoff questions. The system handles feature-level ambiguity internally.

## Design goals

The system is designed to optimize for a very specific operating model:

1. High correctness under ambiguous specifications
2. Low feature-level user interruption
3. Parallel scaling of many long-running tasks per human
4. Recovery from partial progress without long-lived agent drift
5. Cross-section coherence in systems with passive side effects, pipelines, and hidden invariants
6. Strategic adaptation without surrendering mechanical safety

Everything in the repository supports those goals.

# 1. Core philosophy

## 1.1 Alignment over audit

The system rejects feature audit as the primary way to judge progress. Plans do not define a complete feature checklist. They define problems and strategies. As work gets decomposed, more detail appears in lower layers, such as TODO extractions and implementation artifacts. The system therefore checks alignment between adjacent layers rather than audit against a static list.

Typical alignment boundaries are:

- global proposal -> section proposal
- section proposal -> microstrategy or TODO layer
- TODO or microstrategy -> code
- problem definition -> work product
- philosophy -> work product

This matters because the system is never "done" in the checklist sense. It is coherent or incoherent at each boundary.

## 1.2 Problems, not features

The system treats software work as recursive problem decomposition. It does not begin with "implement feature X." It begins with "what is the problem, what are its constraints, and what sub-problems appear when we try to solve it?"

At every scale, the same pattern appears:

1. Explore
2. Recognize the real problem
3. Propose a strategy
4. Align the proposal against the problem
5. Descend only as far as necessary
6. Signal upward if the local layer cannot contain the problem

This is why the philosophy documents describe the system as recursive rather than phase-driven. The repository still contains macro stages, but the solve pattern inside those stages is fractal.

## 1.3 Scripts dispatch, agents decide

This is the most important architectural rule.

Scripts handle:
- queueing
- dispatch
- retries
- pausing
- cleanup
- monitoring
- artifact persistence
- event logging
- task routing
- fail-closed recovery

Agents handle:
- exploration
- classification
- strategy
- interpretation
- grouping
- proposal writing
- implementation decisions
- alignment decisions
- coordination decisions
- scope escalation

The scripts provide the rails. The agents provide the policy.

## 1.4 Zero-risk tolerance

The system is intentionally conservative. It assumes that every shortcut introduces risk. That principle shows up repeatedly in the agent files and philosophy notes. The system is allowed to stay light when the work is genuinely narrow and obvious, but it does not assume simplicity based on appearance alone. It earns simplicity through confirmation.

## 1.5 Heuristic exploration, not exhaustive scanning

The codemap is not an index of everything. It is a routing map. The goal is not to understand every file. The goal is to gain enough structural understanding to route future exploration cheaply and accurately. Downstream agents use that map to perform targeted reads and deeper exploration only where the current problem demands it.

## 1.6 Sections are concerns, not file bundles

A section is a problem region. Related files are a working hypothesis, not the identity of the section. This is a crucial distinction. It allows the system to coordinate on problem interaction rather than simple file overlap.

## 1.7 Avoid long-running agents; persist decisions instead

One of the strongest design decisions in the philosophy docs is to avoid long-lived reasoning sessions. Long-running agents compact, forget, or re-derive too much. Codebase.zip therefore prefers short-lived agents, durable artifacts, and resumable task state. The system persists what was learned so that fresh agents can resume with bounded context.

# 2. System overview

## 2.1 Positioning statement

Codebase.zip is a bounded autonomous SWE runtime that turns:
- a codebase
- a problem statement
- a set of design and execution principles
- and occasional value-level user decisions

into aligned implementation work that can run for long periods with minimal user interruption.

## 2.2 High-level architecture

The system has two worlds:

- Planspace: durable execution memory
- Codespace: the target codebase being changed

Planspace contains:
- run.db
- schedule and lifecycle state
- prompts and outputs
- codemap
- section specs and excerpts
- intent artifacts
- proposal, readiness, and reconciliation state
- notes, signals, and decisions
- tool registry
- risk packages, assessments, plans, summaries, parameters, and risk history
- snapshots and traceability files

Codespace contains:
- the actual application or library under change

The system never confuses these. Planspace is the working memory and control substrate. Codespace is the object of work.

## 2.3 Main runtime components

In the current snapshot, the runtime is no longer concentrated in a few monolithic runner files. The operating model is unchanged, but the script entrypoints are now thin orchestrators: `section_loop/main.py` (221 lines), `section_loop/section_engine/runner.py` (428 lines), `section_loop/coordination/runner.py` (393 lines), `scan/deep_scan.py` (110 lines), and `substrate/runner.py` (433 lines). They mainly own CLI setup, phase ordering, pause/resume behavior, lifecycle logging, fail-closed restart behavior, and handoff to named helpers in `src/scripts/lib/`.

### SKILL.md and stage documents
These define the official operating doctrine, terminology, and stage references. They are the top-level contract of the system.

### workflow.sh
A lightweight schedule driver for the outer schedule file. It tracks coarse schedule state such as wait/run/done/fail at the task level.

### db.sh and run.db
The durable control substrate. This is the operational heart of the system. It provides:
- mailboxes
- task queue
- lifecycle events
- agent registration and status
- append-only event logging

### task_router.py
The typed vocabulary of routed runtime work. It maps queue task types to agent files and default models.

### task_dispatcher.py
A long-lived infrastructure loop that polls the queue, resolves task types, claims work, dispatches the corresponding agent with its agent file, and records completion or failure.

### scan stage
The scan stage still builds the codemap, per-section related-files hypotheses, and project-mode signals. The difference is organizational: `scan/deep_scan.py` is now a pass controller, while tier ranking, per-file analysis, section iteration, feedback routing, template loading, phase logging, and related-file updates live in `lib.scan.*`.

### SIS (Shared Integration Substrate)
SIS still activates for vacuum regions and unresolved shared seams. `substrate/runner.py` now acts as a three-phase shell that decides whether SIS should run and then sequences shard exploration, pruning, seeding, and related-files wiring. Dispatch, policy, and helper logic live in `lib.substrate.*`, and the SIS prompt builders live in `lib.prompts.substrate_prompt_builder`.

### section loop
The section loop is now explicitly a multi-pass orchestrator instead of one giant inline control block. `section_loop/main.py` owns the outer phase order — proposal pass, reconciliation phase, implementation pass, global alignment recheck, and coordination. Per-section execution lives in `section_loop/section_engine/runner.py`, which delegates impact triage, excerpt extraction, problem-frame validation, intent bootstrap, proposal writing, readiness routing, microstrategy generation, implementation, tool validation, and post-completion work to named `lib.pipelines.*`, `lib.intent.*`, `lib.services.*`, `lib.repositories.*`, and `lib.tools.*` helpers.

### ROAL (Risk-Optimization Adaptive Loop)
ROAL is a new bounded parallel review that sits beside the existing section passes rather than replacing them. `lib.pipelines.proposal_pass` can run an advisory risk pre-check on execution-ready proposals. `lib.pipelines.implementation_pass` runs ROAL after the normal readiness gate and before implementation dispatch, persisting risk packages, assessments, plans, and history under `artifacts/risk/`. The thin orchestrator still owns pass order; the new `lib.risk.*` modules own package construction, scoring, posture and threshold logic, history, and prompt/response handling for the risk agents.

### intent layer
The intent layer centers on living problem-definition and philosophy artifacts, decides between lightweight and full intent handling, discovers surfaces passively during alignment, and gates the user only when irreducible value-level choices appear. Bootstrap and triage logic lives in `lib.intent.*` and is called from the section runner. The philosophy bootstrap is an explicit gated workflow: it can emit standard bootstrap states (`NEED_DECISION` and `NEEDS_PARENT`), scaffold user-authored philosophy input in greenfield repositories, and pause the solve loop until an authoritative philosophy source exists. The triage artifact also carries risk-facing hints used by ROAL.

The intent layer now includes a research-first flow for resolving `blocking_research_questions` discovered during proposals. When the readiness gate encounters blocking research questions, it writes a research trigger, builds a proper prompt via `lib.research.prompt_writer`, and submits a `research_plan` task through the queue with a freshness token and concern scope. The research planner (Opus) decomposes questions into bounded tickets; domain researchers (GPT-high) execute web/code research via Firecrawl; the synthesizer (GPT-high) merges ticket results into a dossier, research-derived surfaces, and a proposal addendum; the verifier (GLM) checks citation integrity. Research artifacts feed into both proposal (integration-proposer gets addendum/dossier context) and expansion (research-derived surfaces merge into the existing surface registry). Only questions that research cannot resolve escalate to `needs_parent` for external handling.

Implementation feedback surfaces are a companion mechanism: alignment judges can write problem and philosophy surfaces when implementation reveals unexpected constraints. These surfaces, along with research-derived surfaces, participate in the expansion cycle even on misaligned passes — definition-gap surfaces (new_axis, gap, silence, ungrounded_assumption) trigger expansion while proposals are still being revised. On lightweight sections, the discovery of non-empty structured surfaces triggers escalation to full intent mode with a forced reproposal. All research and feedback artifacts participate uniformly in freshness computation and section-input hashing.

### coordination layer
The coordination layer still collects unresolved cross-section issues, groups them by root cause, decides execution strategy, dispatches fix groups, and rechecks affected sections. `section_loop/coordination/runner.py` now owns round-level control flow while problem collection, scope-delta aggregation, plan parsing, execution batching, and modified-file tracking are delegated to `lib.pipelines.coordination_*` helpers.

### monitors and safety wrappers
Each dispatch can still be paired with a monitor. Dynamic prompts are still wrapped in immutable constraints. Ambiguous outputs are still adjudicated. Structured signals still fail closed. The difference is that these concerns now live in explicit dispatch and parsing services such as `lib.dispatch.agent_executor`, `lib.dispatch.context_sidecar`, `lib.dispatch.monitor_service`, `lib.dispatch.message_poller`, `lib.services.signal_reader`, `lib.services.verdict_parsers`, and the ROAL threshold/validation helpers in `lib.risk.threshold`.

## 2.4 Internal `lib/` organization

The current snapshot makes `src/scripts/lib/` the runtime's internal service layer. The refactor changed organization, not the system's philosophy, agent roles, or artifact model. The same stages still run and the same families of artifacts still exist. What changed is that the runtime now expresses those behaviors as small domain modules instead of concentrating them inside a handful of giant runners.

In the current codebase, the non-risk service layer spans 86 focused modules across 14 domain subpackages, ROAL adds a dedicated 9-module `risk/` package, and research adds a 3-module `research/` package. The live `lib/` tree therefore spans 98 domain modules across 16 subpackages. The separation is intentional:
- control-flow entrypoints stay in the script-facing packages (`section_loop/`, `scan/`, `substrate/`)
- reusable business logic, persistence rules, and bounded analyses live in `lib/`
- stage orchestration is expressed as named pipeline modules instead of inline code blocks
- risk review is a first-class sibling of proposal and implementation rather than hidden inside the section runner

The subpackages divide ownership as follows:

- `core/` — cross-cutting infrastructure primitives: artifact I/O, hashing, typed path construction via `PathRegistry`, database access, shared communication helpers, model-policy loading, and pipeline-state queries.
- `repositories/` — durable artifact and state persistence. Proposal-state files, notes, decisions, excerpts, reconciliation requests/results, and strategic-state snapshots are normalized here instead of being hand-managed in orchestrators.
- `services/` — mostly stateless business logic and analyzers: readiness resolution, alignment/problem extraction, freshness tokens, snapshotting, impact analysis, reconciliation detection, signal reading, verdict parsing, scope-delta parsing, and section-input hashing.
- `dispatch/` — agent execution mechanics: raw agent launch, scoped context sidecars, dispatch metadata, mailbox services, message polling, and per-agent monitors.
- `prompts/` — prompt templates, prompt helpers, context assembly, and substrate prompt builders.
- `pipelines/` — named orchestration steps used by the thin runners: proposal pass/loop, implementation pass/loop, reconciliation, coordination, global alignment recheck, excerpt extraction, problem-frame gating, readiness routing, microstrategy orchestration, impact triage, recurrence emission, and scope-delta aggregation.
- `intent/` — intent triage, intent bootstrap, intent-surface handling, and philosophy bootstrap.
- `scan/` — deep-scan analysis, tier ranking, section iteration, feedback routing, phase logging, related-files updates, scan dispatch, and scan template loading.
- `substrate/` — SIS-specific dispatch wrappers, helper logic, and policy/config readers.
- `sections/` — section loading, project-mode resolution, decision helpers, and cross-section note helpers.
- `tasks/` — task queue DB access, task ingestion, task parsing, and task notifications.
- `flow/` — task-flow context, branching and gate reconciliation, and flow submission helpers for multi-step task envelopes.
- `tools/` — tool-surface and log-extraction utilities.
- `risk/` — ROAL data types, serialization, package building, engagement selection, scoring, posture selection, threshold enforcement, history, and loop orchestration.
- `research/` — in-runtime research prompt construction, research status tracking, and research plan validation. Bridges the readiness gate to the task queue for bounded research flows.

This separation exists so that pause/resume mechanics, mailbox semantics, and fail-closed stage transitions remain legible in a small number of orchestrators, while the reusable rules become independently testable and composable. It also makes module-level testing practical: extracted services and the risk loop can be covered directly instead of only through end-to-end runner behavior. A few wrapper modules still live beside the script entrypoints for compatibility and stage-local prompt writing, but the heavy solve logic is no longer concentrated there.

# 3. The bounded substrate

## 3.1 Why boundedness exists

The system is not open-ended orchestration. Its substrate is intentionally typed and bounded because:
- durable restart requires stable mechanics
- pause/resume requires known message protocols
- monitoring requires known task classes
- safety depends on every dispatch having an agent file
- dynamic behavior must still be observable and debuggable

So the system does not let agents invent new execution primitives at runtime.

## 3.2 How the current code expresses boundedness

The refactor made the boundary legible in both the filesystem and the call graph.

The thin script orchestrators now own control flow:
- `section_loop/main.py` orders proposal pass, reconciliation phase, implementation pass, global alignment recheck, and coordination
- `section_loop/section_engine/runner.py` orders section-local steps and pass-mode transitions
- `section_loop/coordination/runner.py` orders coordination rounds and affected-section rechecks
- `scan/deep_scan.py` orders deep-scan passes
- `substrate/runner.py` orders shard exploration, pruning, seeding, and related-files application

The `lib/` layer owns the bounded operations that those orchestrators are allowed to invoke:
- `lib.pipelines/` composes named passes, gates, and coordination phases
- `lib.repositories/` and `lib.core/` own durable artifacts, schemas, paths, database access, and model-policy resolution
- `lib.services/` owns readiness, alignment parsing, freshness, snapshots, impact analysis, reconciliation detection, and structured signal/verdict parsing
- `lib.dispatch/` owns agent launch, mailbox polling, monitor lifecycle, metadata, and scoped context sidecars
- `lib.intent/`, `lib.scan/`, `lib.substrate/`, `lib.tasks/`, `lib.flow/`, and `lib.tools/` own their respective domain logic within the same bounded runtime
- `lib.risk/` owns ROAL: risk package construction, assessment and optimization prompt handling, serialization, threshold enforcement, posture logic, and append-only history
- `lib.research/` owns in-runtime research: prompt construction for the research planner, research status tracking, and research plan validation

This matters because boundedness is no longer only a philosophy rule. It is also visible in the code layout: the entrypoints express the allowed control surfaces, and the service layer expresses the reusable operations that implement those surfaces.

## 3.3 What is bounded

The substrate hardcodes:
- the existence of `run.db`, mailboxes, lifecycle events, and the task queue
- task statuses, lifecycle transitions, the routed task vocabulary, and task-submission/dispatch rules
- agent-file enforcement, scoped context resolution, monitoring, timeout behavior, and fail-closed signal handling
- artifact schemas and persistence conventions for proposal state, readiness, notes, reconciliation requests/results, strategic state, tool registry, traceability artifacts, the `artifacts/risk/` family, and research artifacts (dossier, addendum, derived surfaces, research status, verification reports)
- macro-cycle orchestration like scan, SIS, proposal pass, reconciliation phase, implementation pass, ROAL risk review, global alignment recheck, coordination, and verification
- the named ROAL work kinds of risk assessment and execution optimization, each backed by a dedicated agent file, fixed JSON schemas, and mechanical enforcement

In design terms, ROAL adds two new typed bounded operations — `risk_assessment` and `risk_optimization`. In the current snapshot they are invoked inline from `lib.risk.loop` rather than submitted through `task_router.py`, but they are still explicit substrate primitives rather than open-ended agent improvisation.

## 3.4 What is not hardcoded

The substrate does not hardcode:
- the actual dependency graph of the problem
- the exact order of discovered sub-work inside proposal, implementation, or coordination
- which deeper investigations are needed
- which sections need stronger intent handling
- which interfaces need bridging
- which shared seams need substrate seeding
- which risks dominate a local execution slice
- which posture a section will actually require once ROAL inspects current evidence
- which open problems must bubble upward or recur strongly enough to be reopened
- which blocking research questions require in-runtime research vs external human input
- which typed follow-on tasks or flow branches an agent will request

Those are discovered by agents through execution.

## 3.5 The meaning of the task queue

The queue is not a workflow ladder. It is not a full DAG. It is a typed blackboard of discovered obligations.

A task in the queue means:
- an agent discovered that some work exists
- the work can be expressed in the system's task vocabulary
- the substrate can run it safely and durably

A task may be emitted as a single request or as a typed chain, fanout, or gate envelope, but it still lands inside the same bounded task substrate. That is a very different idea from preplanning the full solve graph. Codebase.zip uses the queue to externalize discovered work as the run unfolds.

ROAL does not change this. It adds an inline guardrail loop around already-claimed section work, not a second general workflow engine.

## 3.6 Why task submission matters

The philosophy docs are explicit about this: agents should submit tasks, not spawn agents directly.

That design achieves four things:
1. It keeps agents short-lived.
2. It ensures every dispatch uses an approved agent file.
3. It keeps execution observable and resumable.
4. It prevents arbitrary social behavior between agents from contaminating the system.

This remains true after the refactor. Agents write task-request artifacts and flow declarations. The dispatcher remains the only mechanical launch point. Agents say what they need. The substrate decides how that need is executed.

ROAL is the additive inline exception, and it is narrow by design. Its risk-assessment and execution-optimization agents are not free-standing discovered work items. They are bounded substeps inside proposal and implementation passes around an already selected section package. Even there, the same discipline holds: named agent files, typed artifacts, validated outputs, and fail-closed enforcement.

# 4. The agent system

## 4.1 Active agent inventory

This snapshot contains 47 current agent definitions. The set is still not organized around application domains. It is organized around reasoning operations and governance functions.

## 4.2 Agent categories

### A. Structural understanding and routing
- scan-codemap-builder
- scan-codemap-freshness-judge
- scan-codemap-verifier
- scan-related-files-explorer
- scan-related-files-adjudicator
- scan-tier-ranker
- scan-file-analyzer
- section-re-explorer

These agents create and refine the system's navigation surface. They tell the rest of the runtime where to look and what confidence to place in that routing.

### B. Framing, intent, and research
- setup-excerpter
- intent-triager
- intent-pack-generator
- intent-judge
- problem-expander
- philosophy-bootstrap-prompter
- philosophy-distiller
- philosophy-expander
- philosophy-source-selector
- philosophy-source-verifier
- recurrence-adjudicator
- research-planner
- domain-researcher
- research-synthesizer
- research-verifier

These agents define what the section is actually trying to solve, what philosophical constraints govern the work, and when those definitions need to expand or be reopened. The research agents handle in-runtime bounded research: when a proposal emits `blocking_research_questions`, the readiness gate dispatches a research flow that decomposes questions into tickets, executes web/code research, synthesizes results into a dossier with research-derived surfaces and a proposal addendum, and optionally verifies citation integrity. Only questions that research cannot resolve escalate to `needs_parent` for external handling.

### C. Strategy and implementation
- integration-proposer
- implementation-strategist
- microstrategy-decider
- microstrategy-writer

These agents convert problems into implementation shape. They do not merely write code. They decide how the work should be wired into the codebase, whether a tactical microstrategy is needed, and how to execute the proposal coherently. The implementation strategist cannot silently absorb structural omissions from upstream proposals. When it encounters missing anchors, unresolved contracts, or structural gaps that were not addressed by the proposal or reconciliation stage, it must emit blockers or reopen signals rather than inventing local fixes. This prevents the implementation layer from quietly papering over problems that belong at the proposal or intent layer.

### D. Alignment, reconciliation, and adjudication
- alignment-judge
- alignment-output-adjudicator
- state-adjudicator
- consequence-note-triager
- reconciliation-adjudicator

These agents prevent directional drift. They operate at the layer boundaries and turn ambiguous outputs into structured verdicts, including cross-section reconciliation outcomes.

### E. Coordination and cross-section governance
- coordination-planner
- coordination-fixer
- bridge-agent
- impact-analyzer
- impact-output-normalizer

These agents handle non-locality: contracts, side effects, shared interfaces, consequence propagation, grouped problem batches, and cross-section repair.

### F. Substrate shaping
- substrate-shard-explorer
- substrate-pruner
- substrate-seeder

These agents are activated when the target system lacks enough shared structure to support meaningful integration proposals.

### G. Runtime hygiene and safety
- agent-monitor
- tool-registrar
- bridge-tools
- qa-interceptor

These agents keep the runtime disciplined: detect loops, intercept malformed QA outputs, register tools, and bridge tool islands.

### H. ROAL risk assessment and execution posture
- risk-assessor
- execution-optimizer

These agents implement the new parallel risk loop. The risk assessor externalizes what is confirmed, assumed, missing, or stale and quantifies per-step/package risk. The execution optimizer converts that assessment into the minimum effective posture, choosing accept, defer, or reopen decisions under hard thresholds.

## 4.3 Why this organization matters

This organization is the system's deepest differentiator.

Most multi-agent systems group agents by:
- domain ownership
- planner vs executor
- writer vs reviewer
- manager vs worker

Codebase.zip groups agents by operations on understanding:
- distill
- expand
- propose
- judge
- adjudicate
- re-explore
- detect
- research
- synthesize
- verify
- prune
- seed
- bridge
- monitor
- assess risk
- optimize guardrails

That changes how the system steers itself. The agents are not narrow laborers. They are operators over the reasoning substrate itself.

# 5. How the system organizes understanding

## 5.1 Problem frame

Section setup extracts proposal and alignment excerpts and produces a mandatory problem frame. This is a gate. Integration work does not begin without it. The problem frame is the local statement of "what problem is this section actually responsible for solving?"

## 5.2 Proposal excerpt and alignment excerpt

These are local projections of global intent into section scope. They let each section reason in context without loading the full world.

## 5.3 Global philosophy

The system still distills freeform execution philosophy into an operational philosophy: numbered principles, interactions, and expansion guidance. This is not style guidance. It is a constraint system that can be checked against work.

What changed in the current snapshot is the bootstrap around that distillation. `ensure_global_philosophy()` is now a gated workflow with explicit bootstrap status, standard signal states, and fail-closed pause behavior instead of a silent `Path | None` discovery step. The bootstrap writes a single `philosophy-bootstrap-signal.json` gate that uses `NEED_DECISION` when the runtime needs user philosophy input and `NEEDS_PARENT` when the selector, verifier, or distiller flow is malformed and requires repair.

Greenfield repositories now have a user-interaction bootstrap path instead of silently stalling. When no repository source survives discovery, selection, or full-read verification, bootstrap scaffolds `philosophy-source-user.md`, writes `philosophy-bootstrap-decisions.md`, optionally asks the `philosophy-bootstrap-prompter` agent for project-shaped prompts grounded in existing artifacts, emits `NEED_DECISION`, and pauses. On resume, substantive user-authored philosophy is treated as an authorized source and sent through the same distillation path, with `user_source` provenance preserved in the source map.

The selector and verifier are also stricter. Selector output is classified into missing, malformed, valid-empty, and valid-nonempty states with retry and escalation, and its catalog now includes richer excerpts and headings rather than relying on a tiny preview. The verifier is authoritative: every shortlisted file gets a full-read confirmation pass, not just ambiguous cases. All three bootstrap agents share the same definition of philosophy as cross-cutting reasoning doctrine, so specs, architecture plans, and tactics do not qualify unless a mixed document has named philosophy-bearing sections. If distillation still cannot extract stable principles, bootstrap re-pauses for clarification instead of inventing filler.

## 5.4 Per-section intent pack

In full intent mode, each section receives:
- problem.md
- problem-alignment.md

The intent pack defines axes of concern. It describes the dimensions along which the section can succeed or fail without prescribing the exact solution.

## 5.5 Intent surfaces

The intent judge does not just say aligned or misaligned. While checking work, it passively notices problem surfaces and philosophy surfaces:
- missing problem axes
- tensions between axes
- ungrounded assumptions
- philosophy silence
- philosophy tension
- contradictions

These surfaces are then normalized, registered, expanded, discarded, or reopened through recurrence adjudication.

In addition to intent-judge and alignment-judge discovered surfaces, the system now recognizes two additional surface sources: **research-derived surfaces** (produced by the research synthesizer when resolving blocking research questions) and **implementation feedback surfaces** (written by alignment judges when implementation reveals unexpected constraints). Both feed into the same expansion cycle. Definition-gap surfaces (new_axis, gap, silence, ungrounded_assumption) trigger expansion even on misaligned passes, allowing problem and philosophy definitions to grow while proposals are still being revised. On lightweight sections, the discovery of non-empty structured surfaces triggers escalation to full intent mode.

## 5.6 Proposal as problem-state artifact

The integration proposal is not a plan of which files change. It is a problem-state artifact that captures what the proposer learned about the section's problem and what remains unresolved. A proposal emits:

- resolved anchors: codebase locations whose role in the solution is confirmed
- unresolved anchors: locations that appear relevant but whose role is uncertain
- resolved contracts: interface obligations that are fully specified
- unresolved contracts: interface obligations that still need detail or negotiation
- research questions: technical unknowns that require deeper exploration
- user questions and root questions: value-level or scope-level ambiguities that may need human input
- new-section candidates: problem regions that do not belong to this section
- shared seam candidates: integration surfaces that affect other sections
- execution readiness: a structured declaration of whether the proposal is ready for implementation dispatch

The implementation strategist executes the resolved portion of that shape, and alignment checks test whether the hypothesis remains coherent. Unresolved fields gate execution rather than being silently absorbed downstream.

## 5.7 Execution-readiness gate

Every proposal carries an execution-readiness declaration derived from its unresolved fields. The gate is fail-closed: if any blocking field remains unresolved — unresolved anchors that affect the critical path, unresolved contracts that other sections depend on, blocking research questions that determine structural direction — implementation dispatch is blocked.

The gate does not require perfection. It requires that no unresolved item would force the implementation strategist to silently invent answers. Non-blocking unknowns (cosmetic naming, internal ordering, deferred optimizations) do not hold the gate. Structural unknowns do.

When the readiness gate encounters `blocking_research_questions`, it first attempts in-runtime bounded research: it writes a research trigger, builds a prompt, and submits a `research_plan` task through the queue. Research results (dossier, addendum, derived surfaces) feed back into the proposal on the next pass. Only when research has already run and the questions remain unresolved does the gate escalate to `needs_parent`. Prompt validation failures also fail closed to `needs_parent` rather than queueing unsafe work.

In the current snapshot, readiness is still the admission gate, not the final word on execution shape. Once a section clears readiness, ROAL can run a second, narrower check: given that the work is locally legitimate, what level of guardrail does the current slice require? Readiness decides whether implementation may be considered. ROAL decides how cautiously the accepted slice should proceed.

This is one of the system's strongest correctness mechanisms. Without it, the implementation strategist would be forced to absorb ambiguity that belongs at the proposal or intent layer, leading to silent drift.

## 5.8 Reconciliation stage

After initial proposals are written across all sections, a universal reconciliation stage runs before implementation begins. Reconciliation normalizes:

- shared anchors: if two sections resolved the same anchor differently, reconciliation detects and resolves the conflict
- contracts: interface obligations that span sections are checked for consistency and completeness
- section boundaries: new-section candidates emitted by proposals are evaluated and either absorbed, merged, or promoted to real sections
- shared seam candidates: seam candidates from multiple proposals are deduplicated, prioritized, and routed to SIS or coordination as appropriate

Reconciliation is not optional and is not mode-dependent. It runs the same way for brownfield, greenfield, and hybrid projects. Its purpose is to prevent independent section proposals from quietly diverging on shared structural assumptions before any code is written.

## 5.9 Microstrategy and TODO extraction

When warranted, the system inserts a microstrategy layer: a tactical per-file or per-surface breakdown between section proposal and code. It can also extract TODOs from relevant files as in-code microstrategies. This supports the philosophy that lower layers should still align to higher problem statements.

# 6. Risk-Optimization Adaptive Loop (ROAL)

## 6.1 Purpose

ROAL is an additive parallel loop that sits beside proposal, reconciliation, and implementation. It does not redefine the system's philosophy or replace the existing readiness gate. Its job is narrower: scale execution guardrails to actual local risk so the runtime does not treat every implementation slice as equally simple or equally dangerous.

The failure mode it primarily targets is brute-force regression: pushing a section through implementation because it is nominally ready even though the current slice still carries stale understanding, hidden cross-section coupling, or ambiguous mutation surfaces. Readiness answers "is this section locally legitimate to consider for implementation?" ROAL answers "given that legitimacy, what is the minimum effective posture for executing it safely?"

## 6.2 Core model

ROAL expresses candidate work as a `RiskPackage` built from proposal-state, problem frame, readiness artifacts, and microstrategy when present. Each package is decomposed into typed `PackageStep`s with one of five step classes:
- explore
- stabilize
- edit
- coordinate
- verify

Risk is then assessed against seven primary risk types scored 0-4:
- context rot
- silent drift
- scope creep
- brute-force regression
- cross-section incoherence
- tool island isolation
- stale artifact contamination

Those primary risks are modulated by cross-cutting factors — blast radius, reversibility, observability, and confidence — plus append-only historical evidence from similar past outcomes. Raw risk maps to five posture profiles:
- P0 direct
- P1 light
- P2 standard
- P3 guarded
- P4 reopen or block

Each step class has its own execution threshold. This is important because a low-confidence coordination step and a low-confidence edit step should not tolerate the same residual risk.

## 6.3 Loop shape

The full ROAL loop in `lib.risk.loop` is bounded and explicit:
1. build a risk package from the current proposal and microstrategy surface
2. dispatch `risk-assessor.md` to produce an understanding inventory plus quantified `RiskAssessment`
3. dispatch `execution-optimizer.md` to produce a `RiskPlan` with per-step posture and accept, defer, or reopen decisions
4. mechanically enforce thresholds and schema rules in `lib.risk.threshold`
5. persist typed artifacts under `artifacts/risk/`
6. either return a threshold-compliant plan or fail closed with reopen or block decisions

The proposal pass also has a lightweight advisory path. Once a proposal is execution-ready, it can run a risk pre-check before finalization and recommend more exploration when risks like silent drift or brute-force regression still dominate. The implementation pass uses ROAL as a pre-dispatch review after the normal readiness gate.

ROAL's own artifact family is now part of planspace:
- `artifacts/risk/{scope}-risk-package.json`
- `artifacts/risk/{scope}-risk-assessment.json`
- `artifacts/risk/{scope}-risk-plan.json`
- `artifacts/risk/risk-history.jsonl`
- `artifacts/risk/{scope}-risk-summary.md`
- `artifacts/risk/risk-parameters.json`

## 6.4 Integration with the existing loops

ROAL is additive. The existing loops still discover problems, write proposals, reconcile sections, implement changes, and coordinate fallout. ROAL inserts a risk-scaled control loop around those same operations.

The current snapshot integrates it in four places:
- `lib.intent.intent_triage` enriches triage artifacts with `risk_mode`, `risk_confidence`, `risk_budget_hint`, and `posture_floor`
- `lib.pipelines.proposal_pass` can run a lightweight advisory pre-check on execution-ready proposals
- `lib.pipelines.implementation_pass` runs ROAL after readiness and before `run_section(..., pass_mode="implementation")`, and appends risk history after implementation completes
- `lib.repositories.strategic_state` now surfaces `risk_posture`, `dominant_risks_by_section`, and `blocked_by_risk` in the strategic-state snapshot

ROAL expresses execution in terms of an accepted frontier, deferred steps, and reopened steps. In the current implementation-pass integration, that frontier primarily acts as a bounded pre-dispatch gate and reporting surface: if the review returns no accepted frontier, implementation is skipped and the plan records whether the blocked work is deferred or reopened. The plan schema already persists the finer-grained frontier, mitigation, and dispatch-shape decisions that later passes can consume without changing the substrate.

## 6.5 Oscillation prevention and minimum effective guardrails

ROAL is not just a static score. It is a control loop that tries to avoid both overreaction and complacency.

The dedicated posture logic in `lib.risk.posture` uses:
- hysteresis bands around posture boundaries
- a one-step movement rule by default
- asymmetric evidence, where one failure tightens faster than several successes relax
- cooldown after failure before relaxation is allowed again

The append-only history layer in `lib.risk.history` records predicted risk, actual outcome, verification outcome, dominant risks, and blast-radius bands for accepted steps. That history supports similarity matching and bounded adjustment so the system can become more conservative when repeated patterns go badly, without letting historical noise dominate the present package.

This is why ROAL is best understood as a parallel loop rather than a static checklist. It continually tries to choose the minimum effective guardrail: enough structure to keep the work safe, but no more ceremony than the actual risk requires.

# 7. Runtime adaptation

## 7.1 The system does not precompute the solve graph

This is the critical point.

The runtime does not assume it knows all dependencies up front. It starts with a bounded set of execution primitives and then discovers structure by trying to solve the problem. When friction appears, it externalizes that friction as new obligations.

That is why the queue is so important: it is the surface where discovered structure becomes durable work.

## 7.2 Project mode: brownfield, greenfield, hybrid

The scan stage and section re-exploration logic classify work as:
- brownfield
- greenfield
- hybrid

Mode is an observation from exploration, not a routing key. The same proposer, the same artifact shape, and the same execution-readiness gate apply regardless of mode. A greenfield section does not follow a different proposer or skip alignment stages. It simply produces a proposal with more unresolved anchors and more shared seam candidates, which the standard gate and reconciliation machinery handle.

This means there are no mode-specific short-circuits. The system does not assume that greenfield work is simpler or that brownfield work can skip structural discovery. Mode informs the content of the proposal but does not change the process that produces or gates it.

## 7.3 SIS for vacuum regions

When sections have nothing meaningful to integrate against, SIS activates. This applies regardless of project mode — any section whose proposals contain enough unresolved anchors or shared seam candidates can trigger SIS work.

It works as:
1. Per-section substrate shards describe what each section needs, provides, and what seams it touches.
2. The pruner identifies convergence and contradictions across shards.
3. A seed plan defines the minimal shared anchors to create.
4. The seeder creates minimal anchor files and related-file update signals.
5. Sections can now propose against real seams instead of inventing independent local structure.

This is a major cycle reducer for vacuum situations, whether they appear in greenfield projects or as pockets inside brownfield codebases.

## 7.4 Intent triage: full vs lightweight

Not every section gets the same level of intent machinery.

The intent triager still decides between lightweight and full intent handling by looking at local structural signals such as related-files breadth, incoming notes, prior solve attempts, section summary, and other signs of architectural uncertainty.

In the current snapshot, the triage artifact also carries risk-facing hints:
- `risk_mode`
- `risk_confidence`
- `risk_budget_hint`
- `posture_floor`

Those fields do not replace readiness or ROAL. They give the later implementation pass a grounded expectation about how much guardrail the section is likely to need, using both current section complexity and prior risk-history outcomes.

This is one of the main places where the system adapts to local complexity without giving up the overall philosophy.

## 7.5 Agent-submitted task requests

Proposal and implementation agents can request more work by writing task-request JSON. Common requested work includes:
- scan_explore
- scan_deep_analyze
- impact_analysis
- strategic_implementation
- research_plan

The agent declares what it needs. The dispatcher resolves how that work runs. This preserves autonomy without losing substrate control. The readiness gate can also submit research tasks directly when it encounters blocking research questions — research is discovered work in the queue, not implicit model knowledge.

## 7.6 Recurrence and reopening

If a section keeps looping, recurrence signals are emitted. Discarded intent surfaces that resurface are sent to a recurrence adjudicator rather than ignored or brute-forced. This lets the system distinguish:
- a resolved issue
- a temporarily hidden issue
- a prematurely discarded issue

## 7.7 Consequence propagation

After a section completes, the system snapshots its changes, runs impact analysis, and writes consequence notes to affected sections. This is one of the main reasons the architecture works well for passive side effects and cross-section invariants. Downstream sections do not need to accidentally rediscover those consequences. They receive explicit, triaged signals.

## 7.8 Tool adaptation

The system can create and register tools during execution. Those tools are cataloged in a registry and validated after implementation. Tool friction can trigger bridge-tools proposals when the capability graph contains disconnected islands.

This is how the bottom layer feeds capability back into upper layers. The system is not restricted to code edits alone; it can improve its own tactical tool surface while staying within substrate rules.

# 8. Steering model

## 8.1 The human's job

The system is built so that the human mostly answers:
- philosophy questions
- tradeoff questions
- root scope questions
- irreducible conflicts

It should not need frequent help on:
- local endpoint behavior
- file-level changes
- narrow implementation details
- basic integration steps

## 8.2 When the system asks for input

The runtime should escalate to the user when:
- philosophy tensions require priority decisions
- a new root candidate appears that is not grounded in the existing philosophy
- the section is blocked by irreducible ambiguity or dependency
- the requested work is genuinely out of scope
- a blocking open question prevents substrate seeding or coordinated progress

These are high-leverage decisions. They are the right abstraction layer for a human in a long-running autonomous system.

## 8.3 Why this reduces human time

The system spends more wall-clock time internally because it:
- explores instead of guessing
- aligns instead of checklist-auditing
- propagates consequences explicitly
- reopens recurring issues
- escalates only when philosophy or scope truly needs human arbitration

That extra machine effort is the trade that lowers human interruption. It is a deliberate choice.

## 8.4 Parallel scaling

Because the human is not trapped in feature-level steering, one person can supervise many long-running tasks in parallel. The human becomes a source of values and priority decisions, not a micro-manager of implementation.

# 9. How the runtime keeps agents on track

## 9.1 Every dispatch requires an agent file

The system never launches a free-roaming agent. The dispatch layer requires an agent file for every dispatch. This means every run is constrained by a named role definition.

## 9.2 Dynamic prompt templates inherit immutable constraints

When the runtime generates prompts dynamically, those prompts are wrapped in immutable system constraints. This prevents dynamic prompt generation from quietly reintroducing rogue behavior like agent spawning or scope invention.

## 9.3 Scoped context sidecars

Agents do not receive the whole system context by default. Context is resolved from each agent file's declared needs. This is one of the ways the runtime reduces context rot and role confusion.

## 9.4 Per-agent monitors

When agents are run under section-loop control, they can be paired with monitors that watch for repeated plan messages and looping behavior. That lets the system intervene before a long-running agent wastes cycles or loses the thread.

## 9.5 Timeboxing

Dispatches are timeboxed. In this snapshot, the default timeout path in the dispatch layer is 600 seconds per dispatch. This limits single-agent drift and reinforces the design preference for short-lived bounded runs.

## 9.6 Pause/resume and fail-closed signaling

Agents can signal:
- UNDERSPECIFIED
- NEED_DECISION
- DEPENDENCY
- LOOP_DETECTED
- OUT_OF_SCOPE
- NEEDS_PARENT

Malformed or unknown signals fail closed rather than disappearing silently. This is essential for a low-interruption autonomous system because silent corruption is worse than explicit pause.

# 10. Greenfield, brownfield, and hidden structure

Mode is an observation, not a routing key. The proposer, artifact shape, execution-readiness gate, and reconciliation stage are identical regardless of mode. What changes is the distribution of resolved vs unresolved fields in the proposal.

## 10.1 Brownfield
The section can map its concern onto existing code. Proposals tend to have more resolved anchors and fewer shared seam candidates. The system works against the real structure of the application.

## 10.2 Greenfield
There is no matching code yet. Proposals tend to have more unresolved anchors, more shared seam candidates, and more research questions. The execution-readiness gate will typically block until SIS or reconciliation resolves enough structural unknowns for implementation to proceed without inventing answers.

## 10.3 Hybrid
Some of the work lands in existing code, but some of it requires new structural ground. The same proposal artifact captures both resolved and unresolved regions. Reconciliation and SIS handle the vacuum portions while the resolved portions proceed through the standard gate.

# 11. Why this system behaves differently from common coding agents

## 11.1 It optimizes human time, not just wall-clock speed

A common coding assistant is fast because it asks the human to continuously steer and correct. Codebase.zip spends more machine effort to preserve user intent and reduce interruptions.

## 11.2 It discovers dependencies through execution

Precomputed workflows and DAGs assume dependency structure. This system treats dependency structure as something to discover. That reduces wasted cycles on speculative decomposition and makes it better suited to specs with hidden non-local invariants.

## 11.3 It steers on two axes

The system does not only ask "does this solve the problem?" It also asks "does this solve it in a way that preserves the project's operating philosophy?" That dual-axis steering is central to how it reduces drift in long-running autonomous work.

## 11.4 It organizes agents around epistemic operations

This is one of the system's strongest differentiators. It gives the runtime a way to expand understanding, not just labor.

# 12. Strengths and tradeoffs

## Strengths

- Strong fit for ambiguous specifications
- Good handling of passive side effects and cross-section coupling
- Low feature-level human interruption
- Durable recovery and resumability
- Better preservation of intent than ad hoc interactive steering
- Useful for running many long tasks in parallel
- Clear boundary between mechanical safety and reasoning freedom

## Tradeoffs

- More wall-clock time than speed-first copilots
- More machinery and artifacts to understand
- Requires disciplined philosophy sources and section definitions
- Domain-bounded to software engineering
- Finite task vocabulary means the substrate must evolve intentionally, not accidentally

# 13. Scope and limits

Codebase.zip is expressive enough for general software-engineering work inside its domain:
- bug fixes
- cross-module changes
- new subsystems
- integration-heavy application work
- pipeline and side-effect sensitive systems

It is intentionally not a system for:
- product strategy
- UI/UX ideation
- marketing
- broad business planning

Its task vocabulary is finite, but rich. That boundedness is a safety feature. The cost is that new execution primitives must be added intentionally at the substrate level rather than improvised during a run.

# 14. Short explanation for external audiences

## Positioning statement

Codebase.zip is an autonomous software-engineering runtime designed for hard specs, long horizons, and minimal human interruption. It turns high-level intent and operating philosophy into aligned implementation by combining durable execution state, typed task dispatch, and a large set of specialized reasoning agents.

## Why it is different

Instead of asking the human constant feature-level questions, it does most of the ambiguity handling internally — including bounded in-runtime research when proposals surface blocking questions. Instead of assuming a dependency graph up front, it discovers structure as it works. Instead of organizing agents by engineering domain, it organizes them around how understanding changes: distill, expand, judge, adjudicate, research, synthesize, coordinate, prune, seed, and bridge.

## What it optimizes for

- Accuracy over shortcuts
- Human time over raw wall-clock speed
- Long-running autonomy over interactive hand-holding
- Risk-scaled guardrails over one-size-fits-all execution
- Coherence over checklist completion

# 15. Glossary

## Planspace
The durable working memory and control substrate for a task.

## Codespace
The target repository or worktree being changed.

## Section
A concern or problem region, not a file bundle.

## Problem frame
The local statement of what a section is actually trying to solve.

## Proposal excerpt
A local extract of higher-level strategy relevant to one section.

## Alignment excerpt
A local extract of higher-level constraints and anti-patterns relevant to one section.

## Intent pack
The per-section problem definition and axis rubric used to steer deeper alignment work.

## Surface
A discovered gap, tension, silence, or conflict in the current problem or philosophy understanding.

## SIS
Shared Integration Substrate. A conditional stage that discovers and seeds shared anchor structure when proposals contain enough unresolved anchors or shared seam candidates to warrant it, regardless of project mode.

## ROAL
Risk-Optimization Adaptive Loop. A bounded parallel review that packages local work, quantifies per-step and package risk, selects a minimum effective posture, and persists accepted, deferred, or reopened execution decisions before implementation proceeds.

## Accepted frontier
The subset of packaged steps that ROAL currently judges safe enough to execute under the active thresholds and posture.

## Posture profile
One of P0-P4, representing how much execution structure, monitoring, or blocking a step requires.

## Consequence note
A durable signal from one section to another saying "my completed work materially affects you."

## Microstrategy
A tactical breakdown layer between a section proposal and implementation, used when local complexity warrants more structure.

## Alignment
Directional coherence between adjacent layers.

## Audit
Feature coverage against a static checklist. The system explicitly rejects this as its primary notion of correctness.

# 16. Final synthesis

The simplest accurate description of Codebase.zip is this:

It is a bounded autonomous SWE control system that treats software construction as recursive problem solving under philosophy-level constraints.

The scripts do not solve the work. They keep the runtime safe, durable, and observable.
The agents do not merely execute steps. They progressively discover what the real work is.
ROAL adds a second control surface that scales execution guardrails to the actual local risk instead of assuming one posture fits every implementation slice.
The human does not micromanage implementation. The human sets philosophy and resolves irreducible tradeoffs.

That is why the system can be slower in wall-clock time while still being dramatically more scalable in human time.

And that is why it feels different from both interactive coding assistants and fixed workflow engines.

# Appendix A. Runtime task vocabulary

The general routed task vocabulary in this snapshot includes the following task types:

- alignment_check
- alignment_adjudicate
- impact_analysis
- coordination_fix
- consequence_triage
- microstrategy_decision
- recurrence_adjudication
- tool_registry_repair
- integration_proposal
- strategic_implementation
- section_setup
- scan_codemap_build
- scan_codemap_freshness
- scan_codemap_verify
- scan_explore
- scan_adjudicate
- scan_tier_rank
- scan_deep_analyze
- research_plan
- research_domain_ticket
- research_synthesis
- research_verify
- substrate_shard
- substrate_prune
- substrate_seed
- reconciliation_adjudicate

ROAL adds two additional internal bounded operations — risk assessment and execution optimization. In design terms these are the `risk_assessment` and `risk_optimization` work kinds. In the current implementation they are run inline inside `lib.risk.loop` via `risk-assessor.md` and `execution-optimizer.md` rather than being submitted through `task_router.TASK_ROUTES`, but they are still named, typed, schema-bound execution surfaces.

The important point is not the names themselves. It is that agents can expand work inside this vocabulary without inventing new execution primitives. That is what keeps the runtime both adaptive and bounded.

# Appendix B. Representative control loops

## Per-section solve loop
In the current code organization, the section loop is split between an outer pass orchestrator, an implementation-pass ROAL gate, and a section-local runner.

- Outer orchestration in `section_loop/main.py::_run_loop()`:
  - `run_proposal_pass(...)` iterates sections and calls `run_section(..., pass_mode="proposal")`
  - `run_reconciliation_phase(...)` normalizes shared anchors, contracts, section boundaries, and seam candidates, and can force re-proposal
  - `run_implementation_pass(...)` iterates only execution-ready sections, rechecks readiness, runs ROAL, and then calls `run_section(..., pass_mode="implementation")`
  - `run_global_alignment_recheck(...)` performs the post-Phase-1 global recheck
  - `run_coordination_loop(...)` handles non-local fixes and can restart Phase 1 when alignment changes invalidate earlier work

- ROAL gating in `lib.pipelines.implementation_pass` and `lib.pipelines.proposal_pass`:
  - `build_package_from_proposal()` converts proposal-state, problem frame, readiness, and microstrategy into a typed `RiskPackage`
  - `determine_engagement()` decides whether to skip the implementation review or run the full loop; the proposal pass uses the lightweight advisory path when warranted
  - `run_risk_loop()` dispatches `risk-assessor.md` and `execution-optimizer.md`, enforces thresholds mechanically, and persists `risk-assessment.json` and `risk-plan.json`
  - sections with no accepted frontier are skipped rather than forced through implementation, and the plan records whether blocked work is deferred or reopened
  - `_append_risk_history()` records accepted-step outcomes in `risk-history.jsonl` after implementation finishes

- Section-local orchestration in `section_loop/section_engine/runner.py::run_section()`:
  - `read_incoming_notes()` and `run_impact_triage()` read cross-section consequences and can skip or route lightweight work
  - `surface_tool_registry()` publishes the section-relevant tool surface
  - `extract_excerpts()` materializes proposal and alignment excerpts
  - `validate_problem_frame()` enforces the problem-frame gate
  - `run_intent_bootstrap()` decides lightweight vs full intent handling, refreshes intent artifacts when needed, and can pause the entire section pipeline when global philosophy bootstrap emits `NEED_DECISION` for user input or `NEEDS_PARENT` for bootstrap repair
  - `run_proposal_loop()` writes and aligns the integration proposal
  - `resolve_and_route()` together with `resolve_readiness()` publishes discoveries, queues reconciliation work, routes blockers, and fail-closes on execution readiness
  - `run_microstrategy()` inserts the tactical breakdown layer when warranted
  - `run_implementation_loop()` performs strategic implementation under the existing proposal and readiness constraints
  - `validate_tool_registry_after_implementation()` and `handle_tool_friction()` validate tool changes and route tool-bridge work
  - `post_section_completion()` snapshots changes, runs impact analysis, and emits consequence notes

This is the same logical solve loop as before. The difference is that control flow, business logic, and risk review now have explicit named boundaries instead of living inline inside two giant runner files.

## Global coordination loop
- `run_coordination_loop()` in `lib.pipelines.coordination_loop` decides whether coordination is needed, tracks stall and exhaustion behavior, and keeps strategic-state snapshots current
- `run_global_coordination()` in `section_loop/coordination/runner.py` owns one coordination round at a time
- `_collect_outstanding_problems()` aggregates unresolved section problems and incoming cross-section notes
- `_detect_recurrence_patterns()` decides whether recurrence should escalate the coordination model
- `aggregate_scope_deltas()` normalizes scope-delta artifacts for coordinator adjudication
- `write_coordination_plan_prompt()` and `_parse_coordination_plan()` let the coordination-planner group problems and choose execution strategy
- `execute_coordination_plan()` runs batch-safe coordinated fixes and writes modified-file manifests
- incremental rechecks use `coordination_recheck_hash()` plus per-section alignment re-runs, and the loop repeats until all sections are aligned or coordination stalls or exhausts

# Appendix C. What to tell a technical buyer

If you need one sentence:

Codebase.zip is a low-interruption autonomous SWE runtime that uses a bounded execution substrate and a large epistemic agent set to turn philosophy, problem definitions, and codebase structure into aligned implementation over long horizons.
