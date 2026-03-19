# Problem Archive

Persistent record of problems this project exists to solve. Each entry traces to code that addresses it.

## PRB-0001: Safe Multi-Agent Orchestration

**Status**: active
**Provenance**: user-authored
**Regions**: flow system, task dispatch, section state machine, flow reconciler

Coordinating multiple AI agents to work on a codebase introduces risks: race
conditions on shared artifacts, conflicting edits, unbounded agent spawning,
lost work from agent failures, and opaque controller state that cannot resume
cleanly after interruption. The current architecture addresses this with a
DB-backed per-section state machine: sections advance independently, handlers
are single-shot, transition history is recorded in `run.db`, blocked sections
are re-checked from persisted artifacts, and retry is bounded by explicit
circuit breakers rather than hidden `while True` loops. Safe orchestration also
depends on resilient queue infrastructure around dispatch: retry/backoff for
transient failures, outage detection, graceful halt propagation, and
proposal-history-based cycling detection.

**Solution surfaces**: Flow system (chains, fanout, gates), DB-backed section
state machine, task submission protocol, transition-table-as-data (PAT-0020),
poll-and-check unblock (PAT-0021), task-dispatch retry/outage handling,
graceful halt propagation, scripts-dispatch-agents-decide boundary, ROAL.

---

## PRB-0002: Strategic Implementation Over Brute Force

**Status**: active
**Provenance**: user-authored
**Regions**: section loop, integration proposals, alignment checks, consequence propagation

Brute-force implementation (try → fail → retry) wastes tokens, creates churn, and converges slowly. Strategic implementation (understand the problem deeply → design a strategy → implement once) collapses many waves of problems in one pass. The proposal→TODO→code alignment chain ensures implementations solve what they claim. Concern-based strategy replaces feature-based planning.

**Solution surfaces**: Integration proposals, microstrategy, alignment judges, consequence notes, problem framing, proposal/TODO/code alignment chain.

---

## PRB-0003: Stale Artifacts Cause Incorrect Dispatch

**Status**: active
**Provenance**: audit-inferred (multiple rounds)
**Regions**: readiness gate, freshness computation, input hashing

In a resumable multi-pass orchestration, artifacts from earlier states or passes may be stale when later work references them. Stale artifacts lead to incorrect decisions — skipping necessary work or repeating completed work.

**Solution surfaces**: Content-based hashing (PAT-0006), cycle-aware status (PAT-0007), readiness gate freshness checks, per-dispatch policy refresh in long-lived controllers (PAT-0005 R105).

---

## PRB-0004: Agent Output Corruption

**Status**: active — substantially addressed (R114)
**Provenance**: audit-inferred (early rounds)
**Regions**: all artifact readers, JSON parsing, prompt output consumption

AI agents produce structured output (JSON, markdown with frontmatter) that may be syntactically or semantically malformed. Silent discard loses debugging evidence. Silent acceptance propagates bad data. R114 migrated the last known bypass (`scan/service/scan_dispatch_config.py`) to shared `read_json()`.

**Solution surfaces**: Corruption preservation (PAT-0001), fail-closed defaults (PAT-0008), structured validation.

---

## PRB-0005: Research Information Gathering

**Status**: active
**Provenance**: user-authored
**Regions**: research module, readiness gate, intent surfaces

Implementation sections may require information that isn't available in the codebase — domain knowledge, API specifications, design patterns from other systems. The system needs bounded research capabilities that gather information without unbounded exploration.

**Solution surfaces**: Research planner, domain researcher, research synthesizer/verifier, cycle-aware research status, flow-based research orchestration (R100).

---

## PRB-0006: Cross-Section Coherence

**Status**: active
**Provenance**: user-authored
**Regions**: consequence propagation, readiness, coordination, verification

Sections implemented independently may create conflicting interfaces,
duplicate abstractions, or incompatible assumptions. Friction between isolated
islands of concern is a strategic target, but the system no longer waits at a
global reconciliation barrier to discover it. Coherence issues are detected
where they surface — shared seams, readiness checks, consequence notes,
verification findings, test failures, and scope deltas — then only the
affected sections are blocked or routed into coordination. Coordination itself
is specialized: scaffolding creates stubs only, seam repair is limited to
interfaces, spec ambiguity escalates to the parent instead of guessing,
research gaps route back into exploration, and `project-spec.md` remains
read-only user input during repair.

**Solution surfaces**: Consequence notes, shared-seam / contract checks in the
readiness resolver, specialized coordination routing (`scaffold_create`,
`seam_repair`, `spec_ambiguity`, `research_needed`, `scaffold_assign`),
coordination planner/fixer/scaffolder, substrate discovery, integration
verification findings, behavioral RCA, concern-based interaction routing.

---

## PRB-0007: Execution Risk

**Status**: active
**Provenance**: user-authored
**Regions**: ROAL, alignment checks, readiness gate

The execution pipeline itself introduces risk — proposals that don't solve the stated problem, implementations that diverge from proposals, dispatch to the wrong model. Risk must be quantified and guardrails proportional to the actual danger. The goal is risk below a defined threshold with effort proportional to actual risk, not blanket maximum process. Brute-force regression, optimization feedback, and convergence criteria matter.

**Solution surfaces**: ROAL (Risk-Optimization Adaptive Loop), alignment judges, readiness gates, freshness computation, proportional posture profiles.

---

## PRB-0008: Implementation Risk (Post-Landing)

**Status**: active — substantially expanded
**Provenance**: user-authored (governance gaps analysis)
**Regions**: post-implementation assessment, verification, testing, flow
reconciler, risk register

After code lands, implementation success alone is not enough. R101-R105
established post-implementation assessment plus debt staging/promotion; the
current architecture extends that path with verification/testing gates.
Landed changes can introduce structural breakage, cross-section contract drift,
behavioral regressions, coupling, security surfaces, scalability bottlenecks,
pattern drift, and coherence friction. `verification.structural` gates
section-local structure, `verification.integration` reports cross-section
interface findings, `testing.behavioral` gates on problem-derived behavioral
contracts, and `testing.rca` explains failures without pretending they are
fixed. Verification scope is shaped by ROAL posture, targeted codemap refresh
can be requested for guarded scopes, and a conservative verdict lattice
combines assessment + verification before a section is treated as
governance-aligned / complete. Debt staging and idempotent promotion still
handle `accept_with_debt`.

**Solution surfaces**: Post-implementation assessment agent + prompt writer,
verification chain builder, verification gate + verdict synthesis, flow
reconciler verdict/blocker routing, behavioral testing + RCA, debt signal
staging, bounded stabilization consumer, targeted codemap refresh
(PAT-0012).

---

## PRB-0009: Problem Traceability

**Status**: active — partially implemented (R101-R103)
**Provenance**: user-authored (governance gaps analysis)
**Regions**: governance layer, traceability artifacts, trace indexes, proposal-state

Code exists but we can't trace it back to the problem it solves. When problems evolve or become obsolete, we don't know which code should evolve or be removed with them. R101 added governance enrichment to trace indexes (`trace/section-N.json`). R103 added proposal-time governance identity (PAT-0013) so lineage originates at proposal time, not post-implementation inference. Trace index and trace-map now initialize from proposal-state governance IDs. The `traceability.json` append log also carries governance context.

**Solution surfaces**: This archive, governance packets (PAT-0011), governed proposal identity (PAT-0013), traceability enrichment across all three trace surfaces, update_trace_governance().

---

## PRB-0010: Pattern Governance

**Status**: active — partially implemented (R101-R110, bootstrap/input expansion)
**Provenance**: user-authored (governance gaps analysis)
**Regions**: governance layer, bootstrap, audit process, pattern archive,
proposal-state, model policy, path registry

Established patterns exist and are cataloged, but governance has to attach to
work regardless of what the user brought in. The system no longer assumes a
single "spec file → decompose" bootstrap path. Bootstrap now classifies entry
conditions (`greenfield`, `brownfield`, `prd`, `partial_governance`) as an
observation recorded in `entry-classification.json`, not as permission for
divergent execution rules. PRD/bootstrap inputs can seed doc-derived problem
records with explicit provenance/confidence, and governance packets plus
philosophy distillation thread that governed context into runtime prompts,
freshness hashing, and proposal identity. R103-R110 established packet
threading, proposal-time governance identity, projection fidelity, and
applicability-aware packet narrowing; the new input handling extends those same
governance rules to mixed entry points instead of creating a special-case
bootstrap branch.

**Solution surfaces**: Pattern archive, BootstrapAssessor entry classification,
governance loader (projection fidelity + spec-derived problem extraction),
governance packets (PAT-0011 applicability-aware packeting), governed proposal
identity (PAT-0013), philosophy distillation / NEED_DECISION pauses,
centralized model policy resolver (PAT-0005), audit process pattern alignment
phases, registry-distinguished signal families (PAT-0003).

---

## PRB-0011: Heuristic Exploration Over Exhaustive Scan

**Status**: active
**Provenance**: user-authored
**Regions**: scan/codemap, scan/exploration, lib/scan, substrate

Full-codebase reading is wasteful and misses the point. The system needs just enough skeleton understanding to route deeper work. The codemap is a routing map, not an index of everything. Downstream agents use it for targeted reads, not exhaustive catalogs.

**Solution surfaces**: Scan codemap builder, heuristic exploration, tier ranking, substrate discovery.

---

## PRB-0012: Upward Scope Reframing and New-Ground Escalation

**Status**: active
**Provenance**: user-authored
**Regions**: readiness gate, coordination, research routing, intent surfaces

Agents can discover new problems, new tools, or greenfield territory that cannot be solved locally. These must bubble upward instead of being solved locally out of scope. Blocking research questions, root reframing signals, and shared seam candidates all need upward routing to the appropriate resolver.

**Solution surfaces**: Readiness gate blocker taxonomy, blocking_research_questions routing, requires_root_reframing threading, research-first intent layer, coordination problem resolver.

---

## PRB-0013: Proposal-State Readiness Before Descent

**Status**: active — partially implemented (R104-R106)
**Provenance**: user-authored
**Regions**: proposal-state repository, readiness resolver, readiness gate, implementation prompts

Descent into implementation before anchors, contracts, and research are resolved produces brute-force behavior and reopen cycles. The execution-readiness gate is fail-closed: if any blocking field remains unresolved, implementation dispatch is blocked. R104 added initial governance identity validation. R105 made it genuinely fail-closed: empty governance identity with a populated packet now blocks descent, profile_id mismatch with governing_profile blocks, declared governance IDs with a missing/malformed packet block. R106 fixed the mixed-root contract: resolve_readiness now takes planspace (not artifacts), uses PathRegistry for all path construction, and all 4 callsites updated. Tests now use runtime-shape layout. Alignment-judge contract narrowed to require non-empty identity when governance applies. Proposals are problem-state artifacts, not file-change plans.

**Solution surfaces**: Proposal-state repository, readiness resolver (planspace root contract R106, full fail-closed governance validation R105, PAT-0013), readiness gate, alignment-judge contract, integration proposals as problem-state artifacts.

---

## PRB-0014: Governance Context Dilution / Packet Overscoping

**Status**: active — substantially addressed (R103-R108)
**Provenance**: audit-inferred (R103)
**Regions**: governance packets, freshness computation, section-input hashing, prompt context

Governance packets mirror the full problem/pattern/profile archives into every section, regardless of which problems and patterns are actually applicable to that section. This causes: (1) packet-membership checks become vacuous because every ID is in every packet, (2) any governance edit invalidates freshness for all sections instead of only affected ones, (3) context optimization is violated because agents receive irrelevant governance material. R103 added region-based candidate filtering. R104 expanded to multi-signal applicability. R105 added explicit applicability states (`matched`/`ambiguous_applicability`/`no_applicable_governance`), narrowed profile scope to governing profile only, and populated `governance_questions` when applicability is ambiguous rather than silently broadening. R106 fixed the pattern side: `parse_pattern_index()` now extracts `regions` and `solution_surfaces` fields, so `_filter_by_regions()` can actually scope patterns instead of treating all as universal. R108 removed the full-archive no-match fallback: `_filter_by_regions()` now returns empty candidates when nothing matches, and `build_section_governance_packet()` emits governance questions distinguishing "nothing matched" from "governance doesn't apply."

**Solution surfaces**: Multi-signal section-scoped applicability with explicit ambiguity states (PAT-0011 R105), pattern applicability metadata extraction (R106), bounded profile scope, archive refs instead of full duplication, governance_questions populated on ambiguity, no-match bounded to empty candidates with explicit questions (PAT-0011 R108).

---

## PRB-0015: Evaluation Surface Drift / Silent Coverage Loss

**Status**: reopened (R111), corrected R112
**Provenance**: audit-inferred (R106), reopened R111, corrected R112
**Regions**: evals harness, eval scenarios, import validation, trigger adapters

Declared eval scenario modules can drift to stale import paths without detection. R106 fixed specific stale imports and made the harness fail-closed. R111: eval-surface drift recurred after the Phase B package reorganization. R112: corrected all stale imports in `evals/harness.py` (legacy `src/scripts` bootstrap → `src/`, `dispatch.model_policy` → `dispatch.service.model_policy`, `dispatch.section_dispatch` → `dispatch.engine.section_dispatcher`) and `evals/agentic/trigger_adapters.py` (all subprocess PYTHONPATH, module imports, and script entrypoints updated to current layout).

**Solution surfaces**: Eval harness fail-closed on import errors (PAT-0008 R106), corrected scenario imports, harness exit code enforcement, centralized layout adapter (R112).

---

## PRB-0016: Advisory Surface Degradation Visibility

**Status**: active — resolved (R109)
**Provenance**: audit-inferred (R108)
**Regions**: QA interceptor, QA verdict parser, task dispatcher, lifecycle logging, reconciliation adjudicator

Advisory surfaces (QA interception, reconciliation adjudication) are deliberately fail-open: when they encounter internal errors, missing targets, or unparseable output, they fall back to baseline dispatch behavior. This is correct — advisory gates should not block execution. However, the degraded outcome is currently logged identically to genuine approval: QA parse failures map to PASS, QA exceptions are logged as `qa:passed`, and reconciliation fallback is invisible in lifecycle events. This erases the distinction between "the advisory surface evaluated and approved" and "the advisory surface failed, so dispatch fell back to baseline." Evidence preservation requires this distinction to be visible.

**Solution surfaces**: PAT-0014 (Advisory Gate Transparency), structured 3-tuple advisory result with reason_codes (`unparseable`/`dispatch_error`/`target_unavailable`/`safety_blocked`), DEGRADED verdict in QA parser, distinct lifecycle logging (`qa:degraded` vs `qa:passed`), reconciliation fallback PAT-0014 references.

---

## PRB-0017: Testing Philosophy Drift / Historical Regression Oracles

**Status**: active — substantially addressed (R109-R110), scope expanded R112, R120-R121
**Provenance**: audit-inferred (R109)
**Regions**: integration tests, regression tests, component tests, authoritative runtime surfaces

Tests written as source-text archaeology create fragile regressions. R110 extended PAT-0015 to require representative round-trip contract tests. The suite has converged on positive contract testing. R112: PAT-0015 scope expanded to require that executable audit/eval surfaces themselves have positive current-layout contracts. R120 added governance archive reference-integrity checks and `Services.*` boundary allowlisting. R121 added PAT-0005 centralization locks. R122 added derivation-based self-report truth locks (PAT-0015 rule 13): live Services-import inventory verification, DI boundary prose truthfulness, and philosophy projection presence checks. Remaining gap: governance self-report truth still requires manual refresh during audits; derivation tests reduce but do not eliminate the drift surface.

**Solution surfaces**: PAT-0015 (Positive Contract Testing), positive behavioral assertions over source-grep absence tests, output-shape contracts, representative round-trip contract tests for high-risk handoffs (R110), executable eval/audit surface contracts (R112), governance reference-integrity and Services boundary allowlisting (R120), PAT-0005 centralization lock (R121), derivation-based self-report truth locks (R122).

---

## PRB-0018: Legacy Surface Residue / Incomplete Surface Retirement

**Status**: resolved (R112)
**Provenance**: audit-inferred (R111), resolved R112
**Regions**: live agent inventory, legacy scripts, migration docs

Legacy execution surfaces remain under live discovery trees after migration. R111 deleted 3 dead files (`orchestrator.md`, `exception-handler.md`, `state-detector.md`). R112 deleted `src/dispatch/agents/monitor.md` — the last unrouted agent in the live discovery tree. Runtime inventory now matches the live registries: 58 agent files / 68 task types / 15 namespaces.

Constructor-DI completion also retired three more legacy production surfaces
that no longer belonged in the live tree:
`src/intent/service/expansion_facade.py`,
`src/flow/service/flow_facade.py`, and the dead 64-name re-export facade
`src/intent/service/philosophy.py`.

**Solution surfaces**: PAT-0016 (Runtime Inventory Truth & Surface Retirement), dead file deletion, discovery tree hygiene, constructor-DI facade retirement.

---

## PRB-0019: Runtime Inventory Drift / Authoritative Interface Mismatch

**Status**: active — substantially addressed (R111-R119)
**Provenance**: audit-inferred (R111)
**Regions**: system-synthesis.md, governance/audit/prompt.md, operator docs, eval adapters, pyproject.toml, runtime-facing templates, agent definitions

Authoritative path/count/entrypoint claims are hand-maintained and diverge from live runtime registries after structural migrations. R111 corrected system-synthesis.md and governance/audit/prompt.md (paths/counts). R112 corrected governance/audit/prompt.md region paths to the current layout at that point (48 agents / 12 namespaces), fixed pyproject.toml (stale pythonpath entries), and updated eval harness + trigger adapter imports. R113 fixed `src/flow/engine/task_dispatcher.py` docstring, `src/models.md` stale reference, and `system-synthesis.md` problem count. R114 fixed stale runtime substrate references across SKILL.md, implement.md, models.md, rca.md, templates (`implementation-alignment.md`, `rca-cycle.md`), and agent definitions (`risk-assessor.md`, `execution-optimizer.md`) — removing references to retired `scan.sh`, `substrate.sh`, `section-loop.py`, worktree model, and stale `agents/` paths. R116 corrected `system-synthesis.md` stale counts (21→23 problems, 16→18 patterns) and added PAT-0017/PAT-0018 to the governance-layer pattern list. R119 formalized PAT-0019 (Constructor DI / Composition-Root Boundary) in the pattern catalog, resolving the `system-synthesis.md` phantom reference and bringing the 19-pattern count into agreement with the live catalog at that point. Subsequent state-machine and verification/testing expansion raised the current live baseline to 58 agent files / 68 task types / 15 namespaces / 23 problems / 21 patterns.

The current authoritative architecture description records the runtime wiring
boundary: `src/containers.py` defines service interfaces, production code
receives collaborators via constructors, and only CLI `main()` composition
roots touch the container directly. The boundary is formalized as PAT-0019.
Service-locator residue remains in compat wrappers and a small number of
runtime method-level lookups (documented in PAT-0019 known instances).
R120 identified uncataloged residue and classified scan-stage adapters.
R121 removed constructor fallbacks from `cache.py`, `pipeline/context.py`,
and `substrate_discoverer.py`; extracted QaGate wiring from
`task_dispatcher.py` into constructor injection; injected advisory writer
dependency in `proposal_phase.py`. Remaining residue: `section_dispatcher.py`
QaGate construction (circular dep — genuinely quarantined), staleness service
method-level lookups, and backward-compat wrappers in signals services.

**Solution surfaces**: PAT-0016 (Runtime Inventory Truth & Surface Retirement), PAT-0019 (Constructor DI boundary formalization), registry-derived inventory, atomic doc updates with code changes, authoritative wiring-contract updates.

---

## PRB-0020: Governance Self-Report Drift / False Health Reporting

**Status**: active — substantially addressed (R112-R122)
**Provenance**: audit-inferred (R112)
**Regions**: governance/patterns/index.md, governance/risk-register.md, governance/problems/index.md, governance/audit/history.md, tests/component/test_positive_contracts.py, system-synthesis.md, philosophy/profiles/PHI-global.md

Governance self-report surfaces (pattern health notes, risk register status, problem archive status, audit history counts, test allowlists, system-synthesis prose, compressed philosophy profiles) diverge from actual codebase state. R112-R119 corrected pattern health notes, risk register status, dead-path references, and stale counts. R120 repaired the philosophy projection gap (proposal-evaluation rule now in both analysis and PHI-global). R121: the active drift class is now false present-tense reporting — governance surfaces (PRB-0019, RISK-0008, test allowlists) still named cleaned files as residue. R121 refreshed all stale inventories atomically with the code changes that resolved them. R122: pattern health notes (PAT-0003, PAT-0005, PAT-0015, PAT-0016, PAT-0019) refreshed to post-R121 truth; `system-synthesis.md` DI boundary prose corrected to reference PAT-0019/RISK-0008 instead of restating volatile residue; `PHI-global.md` expanded to preserve 6 missing governing constraint bands; derivation-based truth locks added to positive contract suite.

**Solution surfaces**: PAT-0016 scope expansion to governance self-reports (R112), truthful pattern health notes, audit-time verification of present-tense claims, dead-path and phantom-reference correction (R119), philosophy projection repair (R120), atomic truth-surface refresh (R121), pattern health/synthesis/philosophy refresh and derivation-based truth locks (R122).

---

## PRB-0021: PathRegistry Consumer Saturation / File-Level Accessor Incompleteness

**Status**: substantially addressed (R113-R121)
**Provenance**: audit-inferred (R113), reopened R114, substantially addressed R115, reconciliation family R116, residual sweep R117, 3-family sweep R118, note/decision helpers R120, remaining family helpers R121
**Regions**: PathRegistry, freshness/hashing, reconciliation, readiness, dispatch prompts, proposal cycle, flow system, traceability, coordination, signals, intent

Durable artifact families used at multiple authoritative sites had only directory-level accessors or no accessors at all. R113-R118 added file-level accessors and migrated ~50 consumer sites across 6 sweeps. R120 added domain-repository listing helpers for note-family (`coordination/repository/notes.py`) and decision-family (`orchestrator/repository/decisions.py`) and migrated 12 consumers. R121 added helpers for the remaining 7 family islands: scope-delta listing (`coordination/repository/scope_deltas.py`), input-ref listing (`orchestrator/repository/input_refs.py`), plus local named helpers for research-question, proposal-attempt, recurrence, section-spec/proposal, and scoped impact/reconciliation evidence families. Consumers migrated atomically. Remaining: `decisions.py` repository functions with raw `decisions_dir` Path parameter (by-design — the repository receives its dir from callers). Flow relpath helpers remain a documented by-design exception for DB storage.

**Solution surfaces**: PAT-0003 file-level accessor requirement (rules 1-10) and discovery/listing helper requirement (rule 11), family-level accessor addition + atomic consumer migration, flow family accessors (R114), 6-family saturation sweep (R115), reconciliation family migration (R116), residual sweep (R117), 3-family sweep with 7 accessors and 12 consumer migrations (R118).

---

## PRB-0022: Proposal-State Contract Split-Brain

**Status**: resolved (R115)
**Provenance**: audit-inferred (R115)
**Regions**: proposal-state schema, agent definitions, dispatch templates, readiness gate, eval fixtures

The canonical proposal-state schema in `src/proposal/repository/state.py` required three fields (`constraint_ids`, `governance_candidate_refs`, `design_decision_refs`) that no active agent definition, dispatch template, or eval surface knew to produce. The runtime's `load_proposal_state()` treated missing keys as corruption and renamed the file to `.malformed.json`, causing sections to fail-closed when agents produced valid-looking output that happened to lack the ungoverned fields. The three fields had no traceable problem or pattern justification and were never consumed by any downstream surface (readiness resolver, readiness gate, reconciliation).

R115 rolled back the three ungoverned fields from the schema, fail-closed default, and all test/eval fixtures. PAT-0017 was added to prevent recurrence.

**Solution surfaces**: PAT-0017 (Proposal-State Contract Projection), schema rollback, fixture cleanup.

---

## PRB-0023: Behavioral Doctrine Projection Drift / Method-of-Thinking Split-Brain

**Status**: resolved (R116-R117)
**Provenance**: audit-inferred (R116)
**Regions**: agent definitions (proposal, implementation, coordination, risk, scan), dispatch templates, SKILL.md, implement.md

Authoritative execution doctrine in `src/SKILL.md` and `src/implement.md` evolved from literal "accept zero risk" language (R62 anti-shortcut hardening) to proportional ROAL risk with zero tolerance for fabrication/bypasses. Multiple routed agent files and dispatch templates continued publishing the older wording — including "accept zero risk," "do not accept any risk," and "trivially small shortcuts are permitted" — creating a split-brain between the authoritative doctrine and the live method-of-thinking surfaces that agents actually consume at runtime.

R116 synchronized 10 affected agent/template surfaces. R117 fixed the 11th surface (`microstrategy-writer.md`, missed in R116's hand-maintained sweep) and added it to the PAT-0018 known instances and positive contract test list. All 11 live routed doctrine surfaces now match authoritative wording.

**Solution surfaces**: PAT-0018 (Behavioral Doctrine Projection), atomic doctrine sweep across 11 agent/template surfaces, positive contract tests for doctrine heading and "trivially small" absence (R116-R117).
