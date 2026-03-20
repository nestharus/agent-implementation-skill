# System Synthesis

A bounded autonomous software-engineering runtime that treats software construction as recursive problem solving under philosophy-level constraints. It turns a codebase, a problem statement, and a set of design principles into aligned implementation work over long horizons with minimal human interruption.

Scripts provide the rails. Agents provide the policy. The human sets philosophy and resolves irreducible tradeoffs.

## Core principles

These are substrate invariants, not project-level patterns. They trace to PHI-global.

1. **Alignment over audit** — check directional coherence between adjacent layers, never feature coverage against a checklist. The system is never "done" in the checklist sense. *(PHI-global: accuracy over shortcuts)*
2. **Problems, not features** — recursive problem decomposition all the way down. Explore → recognize → propose → align → descend only as far as necessary → signal upward if the local layer cannot contain the problem. *(PHI-global: strategy over brute force)*
3. **Scripts dispatch, agents decide** — scripts handle queueing, dispatch, retries, pausing, cleanup, monitoring, artifact persistence, event logging, task routing, fail-closed recovery. Agents handle exploration, classification, strategy, interpretation, grouping, proposal writing, implementation decisions, alignment decisions, coordination decisions, scope escalation. *(Substrate invariant)*
4. **Proportional risk tolerance** — risk scales with evidence, not blanket maximalism. Shortcuts earn trust through confirmation; the system uses process proportional to actual danger. *(PHI-global: accuracy over shortcuts, proportional risk)*
5. **Heuristic exploration, not exhaustive scanning** — the codemap is a routing map, not an index of everything. Downstream agents use it for targeted reads. *(PHI-global: strategy over brute force)*
6. **Sections are concerns, not file bundles** — a section is a problem region. Related files are a working hypothesis. *(Problem-oriented architecture)*
7. **Short-lived agents; persist decisions** — avoid long-lived reasoning sessions. Persist what was learned so fresh agents can resume with bounded context. *(PHI-global: bounded autonomy)*

## Architecture

### Two worlds

- **Planspace** — durable execution memory. Contains run.db, prompts, outputs, codemap, section specs, intent artifacts, proposal/readiness state, section-state history, notes, signals, decisions, risk packages, traceability files, governance packets.
- **Codespace** — the target codebase being changed. Also contains authoritative governance documents (governance/, philosophy/).

The system never confuses these. Planspace is working memory. Codespace is the object of work.

### Wiring and composition roots

`src/containers.py` defines the runtime's cross-cutting service interfaces and composition helpers. Constructor dependency injection is the dominant production wiring pattern: engines, services, repositories, and orchestrators receive collaborators through their constructors, and callers pass fully constructed dependencies downward.

Only CLI entry points / `main()` functions / sanctioned composition helpers touch the container directly. After composition, production code works only with injected collaborators. The old free-function facade pattern — construct from the global container, delegate, return — is retired. The service-locator boundary is formalized as PAT-0019 and tracked in RISK-0008; residue is limited to runtime method-level lookups in staleness services, backward-compat wrappers in signals services, and one quarantined circular-dependency site. Scan-stage adapter surfaces (`scan_dispatcher.py`, `deep_scanner.py`) are explicitly scoped as composition helpers.

### The bounded substrate

The substrate is intentionally typed and bounded because durable restart requires stable mechanics, pause/resume requires known protocols, monitoring requires known task classes, and safety depends on every dispatch having an agent file.

**Bounded** (hardcoded): run.db and mailboxes, task statuses and lifecycle transitions, the routed task vocabulary, agent-file enforcement, artifact schemas, bootstrap stage protocols, the per-section state machine, the section transition table, ROAL posture handling, verification/testing chains, and coordination strategies.

**Not bounded** (discovered by agents): the actual dependency graph, which investigations are needed, which sections need stronger intent handling, which interfaces need bridging, which risks dominate, which blocking questions require research vs human input, and even how many sections exist at a given moment. Section count is dynamic: accepted scope deltas can create new sections during execution.

The task queue is not a workflow ladder. It is a typed blackboard of discovered obligations.

## Regions

### Flow System & Task Routing

- **Problems solved**: PRB-0001 (Safe Multi-Agent Orchestration), PRB-0003 (Stale Artifacts)
- **Philosophy**: PHI-global (bounded autonomy, fail-closed)
- **Patterns**: PAT-0004 (Flow System), PAT-0005 (Policy-Driven Models), PAT-0006 (Freshness), PAT-0019 (Constructor Dependency Injection)

The flow system expresses multi-step agent work as chains (sequential), fanout (parallel branches with gates), and named packages. `src/flow/types/routing.py` maps the typed task vocabulary to agent files and default models. `src/flow/engine/task_dispatcher.py` polls the queue, resolves task types, claims work, dispatches agents, and records completion. `src/flow/engine/reconciler.py` handles task completion hooks — research flow, proposal gate synthesis, post-implementation assessment, verification/testing follow-ons, and best-effort state-machine advancement.

Flow wiring is explicit: `TaskDispatcher` is constructed with its `Reconciler` and `FlowContextStore` collaborators, and the retired `src/flow/service/flow_facade.py` layer no longer mediates task execution.

Infrastructure inside the flow system is also bounded and durable. The task dispatcher retries transient failures with exponential backoff and detects outages, pausing with escalating delays instead of thrashing the queue. Orchestrators can share a `HaltWatcher` `threading.Event` so an abort can halt work gracefully without losing mailbox state.

Agents say what they need. The substrate decides how that need is executed. Task submission (not direct spawning) keeps agents short-lived, ensures every dispatch uses an approved agent file, keeps execution observable and resumable, and prevents arbitrary social behavior between agents.

**Key modules**: `src/flow/`, `src/taskrouter/`

### Section Loop

- **Problems solved**: PRB-0002 (Strategic Implementation), PRB-0006 (Cross-Section Coherence)
- **Philosophy**: PHI-global (strategy over brute force, alignment over audit)
- **Patterns**: PAT-0010 (Intent Surfaces), PAT-0009 (Blocker Taxonomy)

The section loop is no longer a phase-gated batch pipeline. The old `pipeline_orchestrator.py` shape is legacy compatibility residue, not the governing runtime model. The active architecture treats each section as an independently persisted state machine in `run.db`. `src/orchestrator/engine/state_machine_orchestrator.py` polls for actionable sections, submits the single task implied by each current state, checks blocked sections for unblock conditions, runs starvation detection on blocked work, and repeats until all sections are terminal. Sections therefore advance as independent work units instead of waiting for global proposal / reconciliation / implementation batches.

Per-section execution is more granular than the older pipeline description: excerpt extraction → problem frame → intent triage → philosophy bootstrap → intent pack → proposal → proposal assessment → readiness → ROAL gate → microstrategy → implementation → implementation assessment → verification → post-completion. `BLOCKED`, `ESCALATED`, and `FAILED` are explicit side states, not ad hoc exceptions.

Handlers are single-shot by design. `ProposalCycle` and `ImplementationCycle` perform one bounded dispatch / evaluation step per call. Bootstrap uses the same principle through task-driven agents, each performing one bounded operation. Retrying is not encoded as `while True` inside domain handlers; loops emerge from durable state transitions and re-invocation by orchestration. This keeps retries resumable, observable, and circuit-breakable.

Integration proposals are problem-state artifacts, not file-change plans. They emit resolved / unresolved anchors, contracts, research questions, user questions, new-section candidates, shared seam candidates, and execution-readiness declarations. Per-section proposal history is persisted so fresh agents can detect cycling rather than restarting from scratch.

The execution-readiness gate remains fail-closed: if blocking fields remain unresolved, implementation dispatch is blocked. Non-blocking unknowns do not hold the gate. Structural unknowns do. Blocking is demand-based — research, coordination, verification, and readiness failures push only the affected section to `BLOCKED`.

Sections are not fixed at decompose time. When accepted scope deltas create new work regions, the runtime creates real `sections/section-NN.md` section-spec files in planspace and registers the new section in `run.db` as `PENDING`, so the state machine can absorb it as ordinary work.

**Key modules**: `src/orchestrator/engine/state_machine_orchestrator.py`, `src/orchestrator/engine/section_state_machine.py`, `src/section/`, `src/proposal/`, `src/implementation/`, `src/verification/`, `src/testing/`

### State Machine Engine

- **Problems solved**: PRB-0001 (Safe Multi-Agent Orchestration), PRB-0002 (Strategic Implementation), PRB-0003 (Stale Artifacts)
- **Philosophy**: PHI-global (bounded autonomy, fail-closed)
- **Patterns**: PAT-0004 (Flow System), PAT-0008 (Fail-Closed), PAT-0019 (Constructor Dependency Injection)

The state machine is a first-class architectural component, not just an implementation detail. `src/orchestrator/engine/section_state_machine.py` defines the authoritative `SectionState` enum: `PENDING`, `EXCERPT_EXTRACTION`, `PROBLEM_FRAME`, `INTENT_TRIAGE`, `PHILOSOPHY_BOOTSTRAP`, `INTENT_PACK`, `PROPOSING`, `ASSESSING`, `READINESS`, `RISK_EVAL`, `MICROSTRATEGY`, `IMPLEMENTING`, `IMPL_ASSESSING`, `VERIFYING`, `POST_COMPLETION`, `COMPLETE`, `BLOCKED`, `ESCALATED`, `FAILED`.

Events are also typed: stage completions (`excerpt_complete`, `triage_complete`, `proposal_complete`, `implementation_complete`, `post_completion_done`), gate results (`problem_frame_valid/invalid`, `alignment_pass/fail`, `readiness_pass/blocked`, `risk_accepted/deferred/reopened`, `impl_alignment_pass/fail`, `verification_pass/fail`), and generic `info_available`, `timeout`, and `error`.

Lifecycle is defined by a transition table, not by imperative loops. `advance_section()` reads the current state from the database, resolves `(state, event)` through the transition table, applies the circuit breaker, writes the new `section_states` row, appends a `section_transitions` history record, and returns the new state. Wildcard transitions map `error → FAILED` and `timeout → ESCALATED` for any non-terminal state.

The circuit breaker bounds unproductive re-entry. Re-entry into `PROPOSING` is capped at 5 total entries and re-entry into `IMPLEMENTING` is capped at 3. When the threshold is exceeded, the state machine escalates instead of silently cycling.

The engine's durable schema lives in `run.db`: `section_states(section_number, state, updated_at, error, retry_count, blocked_reason, context_json)` stores the current snapshot, and `section_transitions(section_number, from_state, to_state, event, context_json, attempt_number, created_at)` stores append-only history. Resume after crash is therefore a normal path, not a recovery hack.

Actionable states map to the `section.*` task package, while `READINESS` remains script-only. `advance_on_task_completion()` turns completed `section.*` tasks back into typed events, so task completion and state progression stay decoupled.

**Key modules**: `src/orchestrator/engine/section_state_machine.py`, `src/orchestrator/engine/state_machine_orchestrator.py`, `src/flow/service/task_db_client.py`, `src/section/routes.py`

### Bootstrap & Entry Assessment

- **Problems solved**: PRB-0002 (Strategic Implementation — starting-state assessment), PRB-0009 (Problem Traceability)
- **Philosophy**: PHI-global (strategy over brute force, problems not features)
- **Patterns**: PAT-0004 (Flow System), PAT-0011 (Applicable Governance Packet Threading)

Bootstrap is task-driven. Entry classification observes what the user brought and classifies the repo as `greenfield`, `brownfield`, `prd`, or `partial_governance`. The result is persisted as `entry-classification.json`. Classification changes starting conditions, not the philosophy or downstream operating model.

The bootstrap workflow is expressed as 14 task types in the `bootstrap` namespace (`src/bootstrap/routes.py`), each dispatched to a dedicated agent file under `src/bootstrap/agents/`. `src/pipeline/runner.py` orchestrates the bootstrap flow through the same task-submission and flow primitives used by per-section execution. There is no monolithic orchestrator controller; iteration emerges from task completion routing and flow reconciliation, matching the single-shot principle used everywhere else.

Bootstrap stages include: entry classification, problem extraction and exploration, value extraction and exploration, user-facing confirmation, reliability assessment, decomposition, proposal alignment and expansion, factor exploration, codemap building, section exploration, and substrate discovery. Each stage is a bounded agent dispatch, not a step inside a controller loop.

Bootstrap also feeds governance seeding. For PRD entries, successful decompose can seed governance from spec-derived alignment and call `GovernanceLoader.extract_problems_from_spec()` to extract candidate problem records with `provenance="doc-derived"`, `confidence="medium"`, and inferred regions. Brownfield-with-spec is still treated as brownfield, and existing governance / philosophy docs dominate classification as `partial_governance`.

**Key modules**: `src/bootstrap/routes.py`, `src/bootstrap/agents/`, `src/pipeline/runner.py`, `src/intake/repository/governance_loader.py`

### Scan & Codemap

- **Problems solved**: PRB-0002 (Strategic Implementation — routing surface for targeted investigation)
- **Philosophy**: PHI-global (heuristic exploration)

The scan stage builds the codemap (a structured routing map of subsystems, entry points, interfaces, unknowns, and confidence levels), per-section related-files hypotheses, and project-mode signals (brownfield/greenfield/hybrid). Mode is an observation, not a routing key — the same proposer, artifact shape, and gates apply regardless.

The codemap remains a routing surface rather than an exhaustive index. High-risk post-implementation verification can request a targeted codemap refresh, but refresh is still driven by scoped need rather than full rescans.

**Key modules**: `src/scan/`

### Intent & Philosophy

- **Problems solved**: PRB-0002 (Strategic Implementation — problem framing), PRB-0007 (Execution Risk — philosophy constraints)
- **Philosophy**: PHI-global (problems not features, alignment over audit)
- **Patterns**: PAT-0010 (Intent Surfaces), PAT-0019 (Constructor Dependency Injection)

The intent layer centers on living problem-definition and philosophy artifacts. Full intent mode gives each section a problem.md (axes of concern) and problem-alignment.md (rubric). The intent triager decides lightweight vs full handling based on structural signals.

Philosophy bootstrap is a gated workflow: it scaffolds user input for greenfield repos, pauses on NEED_DECISION, and resumes after user-authored philosophy passes distillation. The selector, verifier, and distiller share a strict definition of philosophy as cross-cutting reasoning doctrine.

Intent wiring is direct: `IntentInitializer` receives `PhilosophyBootstrapper` via constructor injection, and proposal expansion uses `ExpansionHandler` with an injected `ExpansionOrchestrator`. The retired `src/intent/service/expansion_facade.py` and dead `src/intent/service/philosophy.py` surfaces are gone.

Intent surfaces are passively discovered during alignment: missing axes, tensions, ungrounded assumptions, philosophy silence. These surfaces are normalized, registered, expanded, or reopened through recurrence adjudication. Research-derived and implementation-feedback surfaces feed the same expansion cycle.

**Key modules**: `src/intent/`

### Research

- **Problems solved**: PRB-0005 (Research Information Gathering)
- **Philosophy**: PHI-global (bounded autonomy)
- **Patterns**: PAT-0001 (Corruption Preservation), PAT-0002 (Prompt Safety), PAT-0004 (Flow System), PAT-0007 (Cycle-Aware Status)

When proposals or readiness checks emit blocking research questions, the runtime dispatches a research flow: planner decomposes questions into tickets → domain researchers execute web/code research → synthesizer merges results into dossier + research-derived surfaces + proposal addendum → verifier checks citation integrity. Only questions research cannot resolve escalate to `needs_parent`.

Research orchestration uses the flow system: `research_plan_executor.py` translates semantic plans into fanout branches with gates. Status is cycle-aware (`trigger_hash` + `cycle_id`) so new questions trigger new cycles without re-running stale ones.

**Key modules**: `src/research/`, `src/intake/` (assessment), agents: research-planner, domain-researcher, research-synthesizer, research-verifier

### Reactive Reconciliation

- **Problems solved**: PRB-0006 (Cross-Section Coherence)
- **Philosophy**: PHI-global (alignment over audit, strategy over brute force)
- **Patterns**: PAT-0009 (Blocker Taxonomy)

Global batch reconciliation is retired as an orchestration shape. The old `ReconciliationPhase`, `ResolutionPhase`, and `CrossSectionReconciler` batch loop are no longer the governing model. Reconciliation now runs reactively when an individual section reaches readiness or when accepted scope deltas need normalization.

The pure detection functions survive in `src/reconciliation/service/detectors.py`: `detect_anchor_overlaps`, `detect_contract_conflicts`, `consolidate_new_section_candidates`, and `aggregate_shared_seams`. They perform analysis only; they no longer imply a global phase barrier.

`ReadinessResolver` scopes reconciliation to the current section and its seam-sharing neighbors. It derives seam-sharing sections from substrate shard `provides` / `needs`, overlays substrate seed plans and scaffold assignments so already-resolved seams do not re-block descent, aggregates shared seams, and emits blockers only when the current section still participates in a live contract or shared-seam conflict.

Scope-delta aggregation is now wired through the same reconciliation logic. Accepted new-section candidates are consolidated, real section files are created, and any new section is registered in `run.db` as `PENDING` so it enters the same state machine as every other section.

**Key modules**: `src/reconciliation/service/detectors.py`, `src/proposal/service/readiness_resolver.py`, `src/implementation/service/scope_delta_aggregator.py`

### ROAL (Risk-Optimization Adaptive Loop)

- **Problems solved**: PRB-0007 (Execution Risk)
- **Philosophy**: PHI-global (proportional risk, accuracy over shortcuts)
- **Patterns**: PAT-0008 (Fail-Closed)

ROAL scales execution guardrails to actual local risk. It packages work as `RiskPackage`s with typed steps (explore / stabilize / edit / coordinate / verify), assesses seven risk types scored 0-4 (context rot, silent drift, scope creep, brute-force regression, cross-section incoherence, tool island isolation, stale artifact contamination), and selects posture profiles P0-P4.

ROAL is now an explicit `RISK_EVAL` state transition gate between readiness and descent. Readiness decides whether implementation may be considered. ROAL decides how cautiously to proceed. Accepted sections advance to `MICROSTRATEGY`; deferred or reopened sections move to `BLOCKED` and request coordination or reproposal.

The loop is still bounded: build package → dispatch risk-assessor → dispatch execution-optimizer → enforce thresholds → persist artifacts → return accepted frontier or fail-closed. Oscillation prevention uses hysteresis bands, one-step movement, asymmetric evidence, and cooldown. The resulting posture also scopes downstream verification intensity.

**Key modules**: `src/risk/`, `src/orchestrator/engine/section_state_machine.py`, agents: risk-assessor, execution-optimizer

### Post-Implementation Assessment

- **Problems solved**: PRB-0008 (Implementation Risk)
- **Philosophy**: PHI-global (proportional risk, accuracy over shortcuts)
- **Patterns**: PAT-0001 (Corruption Preservation), PAT-0002 (Prompt Safety), PAT-0009 (Blocker Taxonomy)

After implementation, a bounded assessment inspects landed code through coupling/cohesion, pattern conformance, coherence with neighbors, security surface, scalability, and operability. Verdicts remain `accept`, `accept_with_debt` (→ risk register signal), or `refactor_required` (→ structured blocker signal re-entering the proposal path).

Assessment records governance IDs (`problem_ids`, `pattern_ids`, `profile_id`) into traceability, closing the loop from “what problems/patterns governed this work” to “what was actually addressed.” Assessment is distinct from verification: it judges landed design risk, while verification judges post-implementation correctness and seam behavior.

**Key modules**: `src/intake/service/assessment_evaluator.py`, agents: post-implementation-assessor

### Verification & Testing

- **Problems solved**: PRB-0008 (Implementation Risk), PRB-0006 (Cross-Section Coherence)
- **Philosophy**: PHI-global (alignment over audit, proportional risk)
- **Patterns**: PAT-0004 (Flow System), PAT-0008 (Fail-Closed), PAT-0009 (Blocker Taxonomy), PAT-0012 (Post-Impl Assessment), PAT-0015 (Positive Contract Testing), PAT-0016 (Runtime Inventory Truth)

Verification and testing are first-class post-implementation task families, queued by the flow system after implementation completes. They are not a separate global phase. The section state machine exposes a coarse `section.verify` lifecycle step, while the verification subsystem adds finer-grained `verification.*` and `testing.*` tasks for posture-scoped follow-up. `VerificationChainBuilder` builds a ROAL-scoped chain per section, writing a dedicated verification context file for each queued task that includes section spec, problem frame, proposal state, implementation-modified files, consequence-note paths, optional risk context, codemap refresh intent, and behavioral test caps.

Posture drives chain shape. P0 queues structural verification only with `scope=imports_only`. P1 queues structural verification and adds behavioral testing only when incoming consequence notes exist, with a 2-test cap. P2 queues structural + integration verification plus behavioral testing with a 5-test cap. P3 expands integration scope, keeps the 5-test cap, adds risk context, and can request targeted codemap refresh. P4 does not descend into a verification chain; the section is expected to reopen rather than continue as-if safe.

Authority is intentionally split. `verification.structural` is a gate authority: import resolution, registration completeness, schema shape, and other local structural correctness can block the section. `verification.integration` is advisory authority: it checks cross-section seam correctness and writes coordination blocker signals when findings cross section boundaries, but does not by itself mark the section aligned. `testing.behavioral` is a gate authority and is constrained by PAT-0015's positive-contract doctrine and bounded test-count rules. `testing.rca` is advisory authority: it explains failures and routes local vs cross-section consequences.

`VerificationGate` combines post-implementation assessment verdicts with verification verdicts through `verdict_synthesis`. `accept` / `accept_with_debt` plus `pass` stay aligned. Local findings reopen local implementation. Cross-section findings escalate to coordination. `inconclusive` degrades to `accept_unverified`. `refactor_required` remains `refactor_required` regardless of verification. A section cannot remain aligned when the synthesized disposition fails the gate.

**Key modules**: `src/verification/`, `src/testing/`, `src/verification/service/chain_builder.py`, `src/verification/service/verification_gate.py`, `src/verification/service/verdict_synthesis.py`, agents: structural-verifier, integration-verifier, behavioral-tester, test-rca

### Coordination

- **Problems solved**: PRB-0006 (Cross-Section Coherence)
- **Philosophy**: PHI-global (strategy over brute force)

The coordination layer handles non-locality: contracts, side effects, shared interfaces, consequence propagation, grouped problem batches, cross-section repair, and execution pauses that cannot be resolved locally. After completion, the system snapshots changed files, runs impact analysis, and writes consequence notes to affected sections. Consequence depth is tracked in note metadata for observability, but it is not mechanically capped; strategic intervention is an agent decision, not a hardcoded depth limit.

Coordination routing is now specialized. The planner can choose `sequential`, `parallel`, `scaffold_assign`, `scaffold_create`, `seam_repair`, `spec_ambiguity`, or `research_needed`. `scaffold_assign` writes scaffold-assignment signals consumed by readiness. `scaffold_create` dispatches the new `scaffolder.md` agent to create stubs with TODOs and correct interfaces. `seam_repair` uses the fixer for real interface repair. `spec_ambiguity` emits `NEEDS_PARENT` blockers instead of guessing through contradictory specs. `research_needed` submits `scan.explore` work instead of forcing a premature fix.

The fixer's scope is intentionally narrower than before: it performs seam repair and coordination-local fixes, but it does not create files and does not modify the specification. File creation belongs to the scaffolder. When the planner needs bridging, the bridge agent still writes contract deltas and consequence-note seeds before fix dispatch.

**Key modules**: `src/coordination/engine/plan_executor.py`, `src/coordination/types.py`, `src/coordination/problem_types.py`, `src/coordination/service/completion_handler.py`

### SIS (Shared Integration Substrate)

- **Problems solved**: PRB-0006 (Cross-Section Coherence — vacuum regions)

When sections lack enough shared structure for meaningful proposals, SIS activates: per-section shards describe needs / provides / seams → pruner identifies convergence / contradictions → seed plan defines minimal shared anchors → seeder creates anchor files. Sections then propose against real seams instead of inventing independent local structure.

SIS now feeds downstream readiness directly. Substrate seed plans and shard data are used as an overlay during readiness so seam obligations already satisfied by the substrate do not reappear as false blockers.

**Key modules**: `src/scan/substrate/substrate_discoverer.py`, `src/scan/substrate/`, `src/proposal/service/readiness_resolver.py`

### Artifact Infrastructure

- **Problems solved**: PRB-0004 (Agent Output Corruption), PRB-0003 (Stale Artifacts)
- **Philosophy**: PHI-global (evidence preservation, fail-closed)
- **Patterns**: PAT-0001 (Corruption Preservation), PAT-0003 (Path Registry), PAT-0006 (Freshness), PAT-0008 (Fail-Closed), PAT-0019 (Constructor Dependency Injection)

Cross-cutting infrastructure: `artifact_io.py` (read_json / write_json / rename_malformed), `path_registry.py` (all artifact paths from a single source), `content_hasher.py` (content-based hashing), `freshness_calculator.py` (section freshness tokens from 18+ input categories), `input_hasher.py` (full section input hash including governance).

The same infrastructure also gives the state machine and verification layers stable surfaces: state tables in `run.db`, proposal histories, starvation signals, verification contexts, scaffold assignments, and entry-classification artifacts all travel through the same path-registry + corruption-preservation discipline.

**Key modules**: `src/containers.py`, `src/signals/repository/artifact_io.py`, `src/orchestrator/path_registry.py`, `src/staleness/service/freshness_calculator.py`, `src/staleness/service/input_hasher.py`

### Governance Layer

- **Problems solved**: PRB-0008 (Implementation Risk), PRB-0009 (Problem Traceability), PRB-0010 (Pattern Governance)
- **Philosophy**: PHI-global
- **Patterns**: PAT-0001, PAT-0002, PAT-0003, PAT-0005, PAT-0008, PAT-0011 (Applicable Governance Packet Threading), PAT-0012 (Post-Implementation Governance Feedback), PAT-0013 (Governed Proposal Identity), PAT-0014 (Advisory Gate Transparency), PAT-0015 (Positive Contract Testing), PAT-0016 (Runtime Inventory Truth & Surface Retirement), PAT-0017 (Proposal-State Contract Projection), PAT-0018 (Behavioral Doctrine Projection), PAT-0019 (Constructor Dependency Injection)

The governance layer makes per-run artifacts cumulative. Codespace holds authoritative documents (problem archive with 23 problems, pattern catalog with 21 patterns, philosophy profiles, risk register). The runtime parses these into planspace JSON indexes (including synthesis cues extracted from this document's Regions block), builds per-section governance packets with section-scoped candidate filtering (including applicability-aware pattern scoping, bounded no-match behavior, and synthesis cue boosting), and threads them into prompt context, sidecars, freshness hashing, section-input hashing, and traceability.

Governance runtime surfaces follow the same wiring rule as the rest of the system: `src/containers.py` defines the service interfaces, production modules receive collaborators via constructors, and only CLI / composition-root entry points wire concrete instances from the container. The same PAT-0019 residue caveats noted above apply here.

Bootstrap now feeds governance as well. For PRD entry paths, spec-derived problem extraction and alignment seeding can establish a first governance surface before deep implementation begins.

Post-implementation assessment queues after successful implementation, validates results with PAT-0001, merges governance IDs into trace artifacts, and routes verdicts mechanically: `accept` → record governance IDs, `accept_with_debt` → emit debt signal for risk-register staging, `refactor_required` → emit structured blocker signal.

The governance hierarchy: problems (why) → philosophy (values) → patterns (how) → synthesis (connections) → proposals (changes under constraints) → implementation (bounded execution) → assessment (what risks landed) → stabilization (remove risks, re-align).

**Key modules**: `governance/`, `philosophy/`, `src/intake/service/governance_packet_builder.py`, `src/intake/repository/governance_loader.py`, `src/implementation/service/traceability_writer.py`

## Agent system

76 total agent files (68 routed) organized by epistemic operations, not engineering domains.

| Category | Agents | Function |
|----------|--------|----------|
| Structural understanding | scan-codemap-builder, scan-codemap-skeleton-builder, scan-module-explorer, scan-codemap-synthesizer, scan-codemap-freshness-judge, scan-codemap-verifier, scan-related-files-explorer, scan-related-files-adjudicator, scan-tier-ranker, scan-file-analyzer, section-re-explorer | Create and refine the navigation surface |
| Framing & intent | setup-excerpter, intent-triager, intent-pack-generator, intent-judge, problem-expander, philosophy-bootstrap-prompter, philosophy-distiller, philosophy-expander, philosophy-source-selector, philosophy-source-verifier, recurrence-adjudicator | Define what the section is solving and what constraints govern it |
| Research | research-planner, domain-researcher, research-synthesizer, research-verifier | Bounded in-runtime information gathering |
| Strategy & implementation | integration-proposer, implementation-strategist, microstrategy-decider, microstrategy-writer | Convert problems into implementation shape |
| Alignment & adjudication | alignment-judge, alignment-output-adjudicator, state-adjudicator, consequence-note-triager, reconciliation-adjudicator | Prevent directional drift at layer boundaries |
| Coordination | coordination-planner, coordination-fixer, bridge-agent, impact-analyzer, impact-output-normalizer, scaffolder | Handle non-locality, cross-section repair, and scaffold creation |
| Substrate shaping | substrate-shard-explorer, substrate-pruner, substrate-seeder | Seed shared structure for vacuum regions |
| Runtime hygiene | agent-monitor, tool-registrar, bridge-tools, qa-interceptor | Detect loops, validate tools, bridge capability gaps |
| Risk assessment | risk-assessor, execution-optimizer, stack-evaluator | Scale guardrails to actual local risk |
| Governance | post-implementation-assessor | Assess landed-code risks against governance |
| Verification & testing | structural-verifier, integration-verifier, behavioral-tester, test-rca | Post-implementation correctness verification and behavioral testing |
| Bootstrap | entry-classifier, problem-extractor, problem-explorer, value-extractor, value-explorer, user-researcher, reliability-assessor, decomposer, proposal-aligner, proposal-expander, factor-explorer, codemap-builder, section-explorer, substrate-discoverer | Classify entry, extract/explore problems and values, decompose, build codemap, discover substrate |

This organization is the system's deepest differentiator. Agents are operators over the reasoning substrate itself: distill, expand, propose, judge, adjudicate, research, synthesize, verify, prune, seed, bridge, monitor, assess.

## Steering model

The human answers: philosophy questions, tradeoff questions, root scope questions, irreducible conflicts. The system handles internally: local endpoint behavior, file-level changes, narrow implementation details, basic integration, bounded research.

The system spends more wall-clock time internally — exploring, aligning, propagating consequences, reopening recurring issues — so the human is not trapped in feature-level steering. One person can supervise many long-running tasks in parallel.

## Runtime safety

- Every dispatch requires an agent file
- Dynamic prompts inherit immutable constraints (PAT-0002)
- Context is scoped to each agent's declared needs (context sidecars)
- Per-agent monitors detect loops and stalls
- Per-section proposal histories preserve cycling evidence across fresh agents
- Task dispatch uses exponential retry and outage detection with escalating pauses
- State-machine circuit breakers bound repeated proposal / implementation re-entry
- HaltWatcher + shared `threading.Event` enable graceful halt
- Starvation detection escalates blocked sections that stall too long
- 600-second default timeout per dispatch
- Structured pause/resume signals: UNDERSPECIFIED, NEED_DECISION, DEPENDENCY, LOOP_DETECTED, OUT_OF_SCOPE, NEEDS_PARENT
- Malformed or unknown signals fail closed

## Task vocabulary

91 routed tasks across 16 system namespaces, using qualified names (`namespace.task`):

- **bootstrap** (15): `bootstrap.classify_entry`, `bootstrap.extract_problems`, `bootstrap.explore_problems`, `bootstrap.extract_values`, `bootstrap.explore_values`, `bootstrap.confirm_understanding`, `bootstrap.interpret_response`, `bootstrap.assess_reliability`, `bootstrap.decompose`, `bootstrap.align_proposal`, `bootstrap.expand_proposal`, `bootstrap.explore_factors`, `bootstrap.build_codemap`, `bootstrap.explore_sections`, `bootstrap.discover_substrate`
- **coordination** (6): `coordination.bridge`, `coordination.consequence_triage`, `coordination.fix`, `coordination.plan`, `coordination.recurrence_adjudication`, `coordination.scaffold`
- **dispatch** (2): `dispatch.bridge_tools`, `dispatch.tool_registry_repair`
- **implementation** (5): `implementation.microstrategy`, `implementation.microstrategy_decision`, `implementation.post_assessment`, `implementation.reexplore`, `implementation.strategic`
- **intent** (10): `intent.pack_generator`, `intent.philosophy_bootstrap`, `intent.philosophy_distiller`, `intent.philosophy_expander`, `intent.philosophy_selector`, `intent.philosophy_verifier`, `intent.problem_expander`, `intent.recurrence_adjudicator`, `intent.triage`, `intent.triage_escalation`
- **proposal** (4): `proposal.gate_synthesis`, `proposal.integration`, `proposal.section`, `proposal.section_setup`
- **qa** (1): `qa.qa_intercept`
- **reconciliation** (1): `reconciliation.adjudicate`
- **research** (4): `research.domain_ticket`, `research.plan`, `research.synthesis`, `research.verify`
- **risk** (3): `risk.assess`, `risk.optimize`, `risk.stack_eval`
- **scan** (13): `scan.adjudicate`, `scan.codemap_build`, `scan.codemap_freshness`, `scan.codemap_refine`, `scan.codemap_synthesize`, `scan.codemap_verify`, `scan.deep_analyze`, `scan.explore`, `scan.module_explore`, `scan.substrate_prune`, `scan.substrate_seed`, `scan.substrate_shard`, `scan.tier_rank`
- **section** (13): `section.assess`, `section.excerpt`, `section.impl_assess`, `section.implement`, `section.intent_pack`, `section.intent_triage`, `section.microstrategy`, `section.philosophy`, `section.post_complete`, `section.problem_frame`, `section.propose`, `section.risk_eval`, `section.verify`
- **signals** (2): `signals.impact_analysis`, `signals.impact_normalize`
- **staleness** (3): `staleness.alignment_adjudicate`, `staleness.alignment_check`, `staleness.state_adjudicate`
- **testing** (2): `testing.behavioral`, `testing.rca`
- **verification** (2): `verification.integration`, `verification.structural`

Routes are declared per-system in `<system>/routes.py` and collected by `taskrouter.discovery.discover()`. Each route specifies agent file, default model, and optional policy key for model overrides.

The `section` namespace is the state machine's first-class task package. It covers all agent-dispatching section states. `READINESS` is intentionally script-only, so legacy `section.readiness_check` compatibility exists outside the routed vocabulary rather than as an active state-machine route.

ROAL is entered through the `RISK_EVAL` state rather than as an inline implementation-phase loop. Agents expand work inside this vocabulary without inventing new execution primitives. That keeps the runtime both adaptive and bounded.

## Open tensions

- **PRB-0009 (Problem Traceability)**: Governance enrichment covers all three trace surfaces (trace index, trace map, traceability.json) with problem_ids, pattern_ids, and profile_id. R103 added proposal-time governance identity (PAT-0013) so lineage originates at proposal time rather than post-implementation inference. Full round-trip traceability from problem → proposal → code → assessment is now wired; post-implementation assessment validates and enriches proposal-time lineage.
- **PRB-0010 (Pattern Governance)**: Pattern archive (21 patterns) is loaded into governance packets and threaded into prompts, freshness hashing, microstrategy, alignment, and ROAL. R103 added proposal-time pattern_ids and pattern_deviations to proposal-state. R104 deepened the loader to parse template and conformance fields. R105 updated PAT-0005 (long-lived policy refresh), PAT-0011 (explicit ambiguity states), PAT-0012 (material-payload dedup), and PAT-0013 (profile compatibility, non-empty identity requirement). R109 added PAT-0014 (advisory gate transparency — structured reason_codes) and PAT-0015 (positive contract testing). PAT-0019 now records the universal constructor-DI / composition-root boundary. Runtime pattern governance is increasingly structural — proposals must declare governance identity when packets provide candidates, and conformance criteria are now available at runtime.
- **PRB-0014 (Governance Context Dilution)**: R103 added region-based candidate filtering. R104 expanded to multi-signal applicability. R105 added explicit applicability states (matched/ambiguous_applicability/no_applicable_governance), narrowed profile scope to governing profile, and populated governance_questions on ambiguity rather than silently broadening. Packets now carry section-scoped candidate sets with explicit applicability state.
- **Stabilization loop**: Post-impl assessment emits `accept_with_debt` → risk-register staging signal and `refactor_required` → structured blocker signal. R103 wired bounded stabilization consumer. R104 made promotion idempotent. R105 made dedup material-payload-aware: severity, mitigation, rationale, and governance lineage now affect the dedup key so changed risk re-promotes while unchanged debt stays idempotent.
- **Per-region philosophy**: Region-profile-map exists but all regions currently use PHI-global. The infrastructure supports overrides when materially different values emerge.
- **Governance bootstrap quality**: Entry classification, spec-derived problem extraction, and alignment seeding now exist. The remaining tension is heuristic quality — especially how aggressively doc-derived records should seed governance in noisy PRDs or partial-governance repos.
- **PRB-0020 (Governance Self-Report Drift)**: Pattern health notes, problem archive status, `system-synthesis.md` prose, and `PHI-global.md` compression drift faster than the runtime they describe. Derivation-based positive contracts (PAT-0015 rule 13) reduce but do not eliminate the manual refresh surface.

## Glossary

| Term | Definition |
|------|------------|
| Planspace | Durable working memory and control substrate for a task |
| Codespace | The target repository being changed |
| Section | A concern or problem region, not a file bundle |
| Section state machine | The per-section durable lifecycle tracked in `run.db` and advanced by typed events |
| Problem frame | Local statement of what a section is solving |
| Intent pack | Per-section problem definition and axis rubric |
| Surface | A discovered gap, tension, silence, or conflict |
| SIS | Shared Integration Substrate — seeds shared structure for vacuum regions |
| ROAL | Risk-Optimization Adaptive Loop — a state-gated risk posture and accepted-frontier selector |
| Accepted frontier | The subset of steps ROAL judges safe to execute |
| Posture profile | P0-P4 execution guardrail level |
| Consequence note | Durable signal: "my completed work affects you" |
| Consequence depth | Observability metadata carried on consequence notes; tracked but not mechanically capped |
| Microstrategy | Tactical breakdown between proposal and implementation |
| Alignment | Directional coherence between adjacent layers |
| Governance packet | Per-section bundle of applicable problems, patterns, and philosophy |
