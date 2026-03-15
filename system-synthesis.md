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

- **Planspace** — durable execution memory. Contains run.db, prompts, outputs, codemap, section specs, intent artifacts, proposal/readiness/reconciliation state, notes, signals, decisions, risk packages, traceability files, governance packets.
- **Codespace** — the target codebase being changed. Also contains authoritative governance documents (governance/, philosophy/).

The system never confuses these. Planspace is working memory. Codespace is the object of work.

### Wiring and composition roots

`src/containers.py` defines the runtime's cross-cutting service interfaces and
composition helpers. Constructor dependency injection is the dominant
production wiring pattern: engines, services, repositories, and orchestrators
receive collaborators through their constructors, and callers pass fully
constructed dependencies downward.

Only CLI entry points / `main()` functions / sanctioned composition helpers
touch the container directly. After composition, production code works only
with injected collaborators. The old free-function facade pattern — construct
from the global container, delegate, return — is retired. Service-locator
residue persists in constructor fallbacks, backward-compat factory methods,
and a small number of helper-level container lookups (documented in PAT-0019
known instances and RISK-0008). Scan-stage adapter surfaces
(`scan_dispatcher.py`, `deep_scanner.py`) are explicitly scoped as
composition helpers.

### The bounded substrate

The substrate is intentionally typed and bounded because durable restart requires stable mechanics, pause/resume requires known protocols, monitoring requires known task classes, and safety depends on every dispatch having an agent file.

**Bounded** (hardcoded): run.db and mailboxes, task statuses and lifecycle transitions, the routed task vocabulary, agent-file enforcement, artifact schemas, macro-cycle orchestration (scan, SIS, proposal, reconciliation, implementation, ROAL, coordination).

**Not bounded** (discovered by agents): the actual dependency graph, which investigations are needed, which sections need stronger intent handling, which interfaces need bridging, which risks dominate, which blocking questions require research vs human input, which follow-on tasks an agent will request.

The task queue is not a workflow ladder. It is a typed blackboard of discovered obligations.

## Regions

### Flow System & Task Routing

- **Problems solved**: PRB-0001 (Safe Multi-Agent Orchestration), PRB-0003 (Stale Artifacts)
- **Philosophy**: PHI-global (bounded autonomy, fail-closed)
- **Patterns**: PAT-0004 (Flow System), PAT-0005 (Policy-Driven Models), PAT-0006 (Freshness), PAT-0019 (Constructor Dependency Injection)

The flow system expresses multi-step agent work as chains (sequential), fanout (parallel branches with gates), and named packages. ``src/flow/types/routing.py`` maps the typed task vocabulary to agent files and default models. ``src/flow/engine/task_dispatcher.py`` polls the queue, resolves task types, claims work, dispatches agents, and records completion. ``src/flow/engine/reconciler.py`` handles task completion hooks — research flow, post-implementation assessment, gate synthesis.

Flow wiring is explicit: `TaskDispatcher` is constructed with its
`Reconciler` and `FlowContextStore` collaborators, and the retired
`src/flow/service/flow_facade.py` layer no longer mediates task execution.

Agents say what they need. The substrate decides how that need is executed. Task submission (not direct spawning) keeps agents short-lived, ensures every dispatch uses an approved agent file, keeps execution observable and resumable, and prevents arbitrary social behavior between agents.

**Key modules**: `src/flow/`, `src/taskrouter/`

### Section Loop

- **Problems solved**: PRB-0002 (Strategic Implementation), PRB-0006 (Cross-Section Coherence)
- **Philosophy**: PHI-global (strategy over brute force, alignment over audit)
- **Patterns**: PAT-0010 (Intent Surfaces), PAT-0009 (Blocker Taxonomy)

The section loop is a multi-pass orchestrator. `src/orchestrator/engine/pipeline_orchestrator.py` orders: proposal pass → reconciliation → implementation pass → global alignment recheck → coordination. Per-section execution sequences: impact triage → excerpt extraction → problem-frame validation → intent bootstrap → proposal writing → readiness routing → microstrategy → implementation → tool validation → post-completion work.

Composition inside the loop is explicit as well: section decisions are now
persisted through `src/coordination/service/decision_recorder.py`, which
receives artifact and communication collaborators via constructor injection.
`src/orchestrator/service/section_decision_store.py` is now a read /
normalization helper rather than a write facade.

Integration proposals are problem-state artifacts, not file-change plans. They emit resolved/unresolved anchors, contracts, research questions, user questions, new-section candidates, shared seam candidates, and execution readiness declarations.

The execution-readiness gate is fail-closed: if any blocking field remains unresolved, implementation dispatch is blocked. Non-blocking unknowns don't hold the gate. Structural unknowns do.

**Key modules**: `src/orchestrator/`, `src/proposal/`, `src/implementation/`, `src/reconciliation/`

### Scan & Codemap

- **Problems solved**: PRB-0002 (Strategic Implementation — routing surface for targeted investigation)
- **Philosophy**: PHI-global (heuristic exploration)

The scan stage builds the codemap (a structured routing map of subsystems, entry points, interfaces, unknowns, and confidence levels), per-section related-files hypotheses, and project-mode signals (brownfield/greenfield/hybrid). Mode is an observation, not a routing key — the same proposer, artifact shape, and gates apply regardless.

**Key modules**: `src/scan/`

### Intent & Philosophy

- **Problems solved**: PRB-0002 (Strategic Implementation — problem framing), PRB-0007 (Execution Risk — philosophy constraints)
- **Philosophy**: PHI-global (problems not features, alignment over audit)
- **Patterns**: PAT-0010 (Intent Surfaces), PAT-0019 (Constructor Dependency Injection)

The intent layer centers on living problem-definition and philosophy artifacts. Full intent mode gives each section a problem.md (axes of concern) and problem-alignment.md (rubric). The intent triager decides lightweight vs full handling based on structural signals.

Philosophy bootstrap is a gated workflow: it scaffolds user input for greenfield repos, pauses on NEED_DECISION, and resumes after user-authored philosophy passes distillation. The selector, verifier, and distiller share a strict definition of philosophy as cross-cutting reasoning doctrine.

Intent wiring is direct: `IntentInitializer` receives
`PhilosophyBootstrapper` via constructor injection, and proposal expansion uses
`ExpansionHandler` with an injected `ExpansionOrchestrator`. The retired
`src/intent/service/expansion_facade.py` and dead
`src/intent/service/philosophy.py` surfaces are gone.

Intent surfaces are passively discovered during alignment: missing axes, tensions, ungrounded assumptions, philosophy silence. These surfaces are normalized, registered, expanded, or reopened through recurrence adjudication. Research-derived and implementation-feedback surfaces feed the same expansion cycle.

**Key modules**: `src/intent/`

### Research

- **Problems solved**: PRB-0005 (Research Information Gathering)
- **Philosophy**: PHI-global (bounded autonomy)
- **Patterns**: PAT-0001 (Corruption Preservation), PAT-0002 (Prompt Safety), PAT-0004 (Flow System), PAT-0007 (Cycle-Aware Status)

When proposals emit blocking_research_questions, the readiness gate dispatches a research flow: planner decomposes questions into tickets → domain researchers execute web/code research → synthesizer merges results into dossier + research-derived surfaces + proposal addendum → verifier checks citation integrity. Only questions research cannot resolve escalate to needs_parent.

Research orchestration uses the flow system: research_plan_executor.py translates semantic plans into fanout branches with gates. Status is cycle-aware (trigger_hash + cycle_id) so new questions trigger new cycles without re-running stale ones.

**Key modules**: `src/research/`, `src/intake/` (assessment), agents: research-planner, domain-researcher, research-synthesizer, research-verifier

### ROAL (Risk-Optimization Adaptive Loop)

- **Problems solved**: PRB-0007 (Execution Risk)
- **Philosophy**: PHI-global (proportional risk, accuracy over shortcuts)
- **Patterns**: PAT-0008 (Fail-Closed)

ROAL scales execution guardrails to actual local risk. It packages work as RiskPackages with typed steps (explore/stabilize/edit/coordinate/verify), assesses seven risk types scored 0-4 (context rot, silent drift, scope creep, brute-force regression, cross-section incoherence, tool island isolation, stale artifact contamination), and selects posture profiles P0-P4.

The loop: build package → dispatch risk-assessor → dispatch execution-optimizer → enforce thresholds → persist artifacts → return accepted frontier or fail-closed. Oscillation prevention uses hysteresis bands, one-step movement, asymmetric evidence, and cooldown.

ROAL is additive — it sits beside the existing readiness gate, not replacing it. Readiness decides whether implementation may be considered. ROAL decides how cautiously to proceed.

**Key modules**: `src/risk/`, agents: risk-assessor, execution-optimizer

### Post-Implementation Assessment

- **Problems solved**: PRB-0008 (Implementation Risk)
- **Philosophy**: PHI-global (proportional risk, accuracy over shortcuts)
- **Patterns**: PAT-0001 (Corruption Preservation), PAT-0002 (Prompt Safety), PAT-0009 (Blocker Taxonomy)

After implementation, a bounded assessment inspects landed code through: coupling/cohesion, pattern conformance, coherence with neighbors, security surface, scalability, operability. Verdicts: accept, accept_with_debt (→ risk register signal), refactor_required (→ structured blocker signal re-entering the proposal loop).

Assessment records governance IDs (problem_ids, pattern_ids, profile_id) into traceability, closing the loop from "what problems/patterns governed this work" to "what was actually addressed."

**Key modules**: `src/intake/service/assessment_evaluator.py`, agents: post-implementation-assessor

### Coordination

- **Problems solved**: PRB-0006 (Cross-Section Coherence)
- **Philosophy**: PHI-global (strategy over brute force)

The coordination layer handles non-locality: contracts, side effects, shared interfaces, consequence propagation, grouped problem batches, cross-section repair. After implementation, the system snapshots changes, runs impact analysis, and writes consequence notes to affected sections.

Reconciliation runs after all proposals before implementation begins. It normalizes shared anchors, contracts, section boundaries, and shared seam candidates. It prevents independent proposals from silently diverging on shared assumptions.

**Key modules**: `src/coordination/engine/`, `src/coordination/service/`, `src/intent/service/intent_triager.py`, `src/intent/service/recurrence_emitter.py`

### SIS (Shared Integration Substrate)

- **Problems solved**: PRB-0006 (Cross-Section Coherence — vacuum regions)

When sections lack enough shared structure for meaningful proposals, SIS activates: per-section shards describe needs/provides/seams → pruner identifies convergence/contradictions → seed plan defines minimal shared anchors → seeder creates anchor files. Sections then propose against real seams instead of inventing independent local structure.

**Key modules**: `src/scan/substrate/substrate_discoverer.py`, `src/scan/substrate/`

### Artifact Infrastructure

- **Problems solved**: PRB-0004 (Agent Output Corruption), PRB-0003 (Stale Artifacts)
- **Philosophy**: PHI-global (evidence preservation, fail-closed)
- **Patterns**: PAT-0001 (Corruption Preservation), PAT-0003 (Path Registry), PAT-0006 (Freshness), PAT-0008 (Fail-Closed), PAT-0019 (Constructor Dependency Injection)

Cross-cutting infrastructure: `artifact_io.py` (read_json/write_json/rename_malformed), `path_registry.py` (all artifact paths from a single source), `content_hasher.py` (content-based hashing), `freshness_calculator.py` (section freshness tokens from 18+ input categories), `input_hasher.py` (full section input hash including governance).

**Key modules**: `src/containers.py`, `src/signals/repository/artifact_io.py`, `src/orchestrator/path_registry.py`, `src/staleness/service/freshness_calculator.py`, `src/staleness/service/input_hasher.py`

### Governance Layer

- **Problems solved**: PRB-0008 (Implementation Risk), PRB-0009 (Problem Traceability), PRB-0010 (Pattern Governance)
- **Philosophy**: PHI-global
- **Patterns**: PAT-0001, PAT-0002, PAT-0003, PAT-0005, PAT-0008, PAT-0011 (Applicable Governance Packet Threading), PAT-0012 (Post-Implementation Governance Feedback), PAT-0013 (Governed Proposal Identity), PAT-0014 (Advisory Gate Transparency), PAT-0015 (Positive Contract Testing), PAT-0016 (Runtime Inventory Truth & Surface Retirement), PAT-0017 (Proposal-State Contract Projection), PAT-0018 (Behavioral Doctrine Projection), PAT-0019 (Constructor Dependency Injection)

The governance layer makes per-run artifacts cumulative. Codespace holds authoritative documents (problem archive with 23 problems, pattern catalog with 19 patterns, philosophy profiles, risk register). The runtime parses these into planspace JSON indexes (including synthesis cues extracted from this document's Regions block), builds per-section governance packets with section-scoped candidate filtering (including applicability-aware pattern scoping, bounded no-match behavior, and synthesis cue boosting), and threads them into prompt context, sidecars, freshness hashing, section-input hashing, and traceability.

Governance runtime surfaces follow the same wiring rule as the rest of the
system: `src/containers.py` defines the service interfaces, production modules
receive collaborators via constructors, and only CLI / composition-root entry
points wire concrete instances from the container. The same PAT-0019 residue
caveats noted above apply here.

Post-implementation assessment queues after successful implementation, validates results with PAT-0001, merges governance IDs into trace artifacts, and routes verdicts mechanically: `accept` → record governance IDs, `accept_with_debt` → emit debt signal for risk-register staging, `refactor_required` → emit structured blocker signal.

The governance hierarchy: problems (why) → philosophy (values) → patterns (how) → synthesis (connections) → proposals (changes under constraints) → implementation (bounded execution) → assessment (what risks landed) → stabilization (remove risks, re-align).

**Key modules**: `governance/`, `philosophy/`, `src/intake/service/governance_packet_builder.py`, `src/intake/repository/governance_loader.py`, `src/implementation/service/traceability_writer.py`

## Agent system

48 agents organized by epistemic operations, not engineering domains.

| Category | Agents | Function |
|----------|--------|----------|
| Structural understanding | scan-codemap-builder, scan-codemap-freshness-judge, scan-codemap-verifier, scan-related-files-explorer, scan-related-files-adjudicator, scan-tier-ranker, scan-file-analyzer, section-re-explorer | Create and refine the navigation surface |
| Framing & intent | setup-excerpter, intent-triager, intent-pack-generator, intent-judge, problem-expander, philosophy-bootstrap-prompter, philosophy-distiller, philosophy-expander, philosophy-source-selector, philosophy-source-verifier, recurrence-adjudicator | Define what the section is solving and what constraints govern it |
| Research | research-planner, domain-researcher, research-synthesizer, research-verifier | Bounded in-runtime information gathering |
| Strategy & implementation | integration-proposer, implementation-strategist, microstrategy-decider, microstrategy-writer | Convert problems into implementation shape |
| Alignment & adjudication | alignment-judge, alignment-output-adjudicator, state-adjudicator, consequence-note-triager, reconciliation-adjudicator | Prevent directional drift at layer boundaries |
| Coordination | coordination-planner, coordination-fixer, bridge-agent, impact-analyzer, impact-output-normalizer | Handle non-locality and cross-section repair |
| Substrate shaping | substrate-shard-explorer, substrate-pruner, substrate-seeder | Seed shared structure for vacuum regions |
| Runtime hygiene | agent-monitor, tool-registrar, bridge-tools, qa-interceptor | Detect loops, validate tools, bridge capability gaps |
| Risk assessment | risk-assessor, execution-optimizer, stack-evaluator | Scale guardrails to actual local risk |
| Governance | post-implementation-assessor | Assess landed-code risks against governance |

This organization is the system's deepest differentiator. Agents are operators over the reasoning substrate itself: distill, expand, propose, judge, adjudicate, research, synthesize, verify, prune, seed, bridge, monitor, assess.

## Steering model

The human answers: philosophy questions, tradeoff questions, root scope questions, irreducible conflicts. The system handles internally: local endpoint behavior, file-level changes, narrow implementation details, basic integration, bounded research.

The system spends more wall-clock time internally — exploring, aligning, propagating consequences, reopening recurring issues — so the human is not trapped in feature-level steering. One person can supervise many long-running tasks in parallel.

## Runtime safety

- Every dispatch requires an agent file
- Dynamic prompts inherit immutable constraints (PAT-0002)
- Context is scoped to each agent's declared needs (context sidecars)
- Per-agent monitors detect loops and stalls
- 600-second default timeout per dispatch
- Structured pause/resume signals: UNDERSPECIFIED, NEED_DECISION, DEPENDENCY, LOOP_DETECTED, OUT_OF_SCOPE, NEEDS_PARENT
- Malformed or unknown signals fail closed

## Task vocabulary

48 routed tasks across 12 system namespaces, using qualified names (`namespace.task`):

- **coordination** (5): `coordination.bridge`, `coordination.consequence_triage`, `coordination.fix`, `coordination.plan`, `coordination.recurrence_adjudication`
- **dispatch** (2): `dispatch.bridge_tools`, `dispatch.tool_registry_repair`
- **implementation** (5): `implementation.microstrategy`, `implementation.microstrategy_decision`, `implementation.post_assessment`, `implementation.reexplore`, `implementation.strategic`
- **intent** (10): `intent.pack_generator`, `intent.philosophy_bootstrap`, `intent.philosophy_distiller`, `intent.philosophy_expander`, `intent.philosophy_selector`, `intent.philosophy_verifier`, `intent.problem_expander`, `intent.recurrence_adjudicator`, `intent.triage`, `intent.triage_escalation`
- **proposal** (2): `proposal.integration`, `proposal.section_setup`
- **qa** (1): `qa.qa_intercept`
- **reconciliation** (1): `reconciliation.adjudicate`
- **research** (4): `research.domain_ticket`, `research.plan`, `research.synthesis`, `research.verify`
- **risk** (3): `risk.assess`, `risk.optimize`, `risk.stack_eval`
- **scan** (10): `scan.adjudicate`, `scan.codemap_build`, `scan.codemap_freshness`, `scan.codemap_verify`, `scan.deep_analyze`, `scan.explore`, `scan.substrate_prune`, `scan.substrate_seed`, `scan.substrate_shard`, `scan.tier_rank`
- **signals** (2): `signals.impact_analysis`, `signals.impact_normalize`
- **staleness** (3): `staleness.alignment_adjudicate`, `staleness.alignment_check`, `staleness.state_adjudicate`

Routes are declared per-system in `<system>/routes.py` and collected by `taskrouter.discovery.discover()`. Each route specifies agent file, default model, and optional policy key for model overrides.

ROAL adds two inline bounded operations: risk_assessment and risk_optimization.

Agents expand work inside this vocabulary without inventing new execution primitives. That keeps the runtime both adaptive and bounded.

## Open tensions

- **PRB-0009 (Problem Traceability)**: Governance enrichment covers all three trace surfaces (trace index, trace map, traceability.json) with problem_ids, pattern_ids, and profile_id. R103 added proposal-time governance identity (PAT-0013) so lineage originates at proposal time rather than post-implementation inference. Full round-trip traceability from problem → proposal → code → assessment is now wired; post-implementation assessment validates and enriches proposal-time lineage.
- **PRB-0010 (Pattern Governance)**: Pattern archive (19 patterns) is loaded into governance packets and threaded into prompts, freshness hashing, microstrategy, alignment, and ROAL. R103 added proposal-time pattern_ids and pattern_deviations to proposal-state. R104 deepened the loader to parse template and conformance fields. R105 updated PAT-0005 (long-lived policy refresh), PAT-0011 (explicit ambiguity states), PAT-0012 (material-payload dedup), and PAT-0013 (profile compatibility, non-empty identity requirement). R109 added PAT-0014 (advisory gate transparency — structured reason_codes) and PAT-0015 (positive contract testing). PAT-0019 now records the universal constructor-DI / composition-root boundary. Runtime pattern governance is increasingly structural — proposals must declare governance identity when packets provide candidates, and conformance criteria are now available at runtime.
- **PRB-0014 (Governance Context Dilution)**: R103 added region-based candidate filtering. R104 expanded to multi-signal applicability. R105 added explicit applicability states (matched/ambiguous_applicability/no_applicable_governance), narrowed profile scope to governing profile, and populated governance_questions on ambiguity rather than silently broadening. Packets now carry section-scoped candidate sets with explicit applicability state.
- **Stabilization loop**: Post-impl assessment emits `accept_with_debt` → risk-register staging signal and `refactor_required` → structured blocker signal. R103 wired bounded stabilization consumer. R104 made promotion idempotent. R105 made dedup material-payload-aware: severity, mitigation, rationale, and governance lineage now affect the dedup key so changed risk re-promotes while unchanged debt stays idempotent.
- **Per-region philosophy**: Region-profile-map exists but all regions currently use PHI-global. The infrastructure supports overrides when materially different values emerge.
- **Governance bootstrap for new projects**: The governance design describes four entry paths (greenfield, brownfield, PRD, partial governance) but the bootstrap workflow isn't implemented yet.

## Glossary

| Term | Definition |
|------|------------|
| Planspace | Durable working memory and control substrate for a task |
| Codespace | The target repository being changed |
| Section | A concern or problem region, not a file bundle |
| Problem frame | Local statement of what a section is solving |
| Intent pack | Per-section problem definition and axis rubric |
| Surface | A discovered gap, tension, silence, or conflict |
| SIS | Shared Integration Substrate — seeds shared structure for vacuum regions |
| ROAL | Risk-Optimization Adaptive Loop — scales guardrails to local risk |
| Accepted frontier | The subset of steps ROAL judges safe to execute |
| Posture profile | P0-P4 execution guardrail level |
| Consequence note | Durable signal: "my completed work affects you" |
| Microstrategy | Tactical breakdown between proposal and implementation |
| Alignment | Directional coherence between adjacent layers |
| Governance packet | Per-section bundle of applicable problems, patterns, and philosophy |
