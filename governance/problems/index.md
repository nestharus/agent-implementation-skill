# Problem Archive

Persistent record of problems this project exists to solve. Each entry traces to code that addresses it.

## PRB-0001: Safe Multi-Agent Orchestration

**Status**: active
**Provenance**: user-authored
**Regions**: flow system, task dispatch, flow reconciler, section loop

Coordinating multiple AI agents to work on a codebase introduces risks: race conditions on shared artifacts, conflicting edits, unbounded agent spawning, lost work from agent failures. The system needs structured orchestration that bounds agent behavior while preserving their reasoning autonomy.

**Solution surfaces**: Flow system (chains, fanout, gates), task submission protocol, scripts-dispatch-agents-decide boundary, ROAL.

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

In a multi-pass pipeline, artifacts from earlier passes may be stale when later passes reference them. Stale artifacts lead to incorrect decisions — skipping necessary work or repeating completed work.

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
**Regions**: consequence propagation, reconciliation, coordination

Sections implemented independently may create conflicting interfaces, duplicate abstractions, or incompatible assumptions. Friction between isolated islands of concern is a strategic target — coordination must detect and resolve these tensions without centralizing all decisions. Concern interaction and shared seam management drive the coordination layer.

**Solution surfaces**: Consequence notes, reconciliation adjudicator, coordination planner/fixer, substrate discovery, concern-based interaction routing.

---

## PRB-0007: Execution Risk

**Status**: active
**Provenance**: user-authored
**Regions**: ROAL, alignment checks, readiness gate

The execution pipeline itself introduces risk — proposals that don't solve the stated problem, implementations that diverge from proposals, dispatch to the wrong model. Risk must be quantified and guardrails proportional to the actual danger. The goal is risk below a defined threshold with effort proportional to actual risk, not blanket maximum process. Brute-force regression, optimization feedback, and convergence criteria matter.

**Solution surfaces**: ROAL (Risk-Optimization Adaptive Loop), alignment judges, readiness gates, freshness computation, proportional posture profiles.

---

## PRB-0008: Implementation Risk (Post-Landing)

**Status**: active — partially implemented (R101-R105)
**Provenance**: user-authored (governance gaps analysis)
**Regions**: post-implementation assessment, governance assessment, flow reconciler, risk register

After code lands, we don't systematically assess what risks the implementation introduced: coupling, security surfaces, scalability bottlenecks, pattern drift, coherence friction. Post-implementation assessment was implemented in R101: queues assessment after implementation, validates result, routes verdict (accept/accept_with_debt/refactor_required) through structured signals. R102 added debt signal staging. R103 wired bounded stabilization consumer (`promote_debt_signals()` called after implementation pass in `pipeline_orchestrator.py`). R104 made debt promotion idempotent. R105 made dedup material-payload-aware: key now covers severity, mitigation, acceptance_rationale, and governance lineage so changed risk re-promotes while unchanged debt stays idempotent.

**Solution surfaces**: Post-implementation assessment agent + prompt writer, flow reconciler verdict routing, debt signal staging, bounded stabilization consumer (R103), material-payload-aware idempotent debt promotion (R105, PAT-0012).

---

## PRB-0009: Problem Traceability

**Status**: active — partially implemented (R101-R103)
**Provenance**: user-authored (governance gaps analysis)
**Regions**: governance layer, traceability artifacts, trace indexes, proposal-state

Code exists but we can't trace it back to the problem it solves. When problems evolve or become obsolete, we don't know which code should evolve or be removed with them. R101 added governance enrichment to trace indexes (`trace/section-N.json`). R103 added proposal-time governance identity (PAT-0013) so lineage originates at proposal time, not post-implementation inference. Trace index and trace-map now initialize from proposal-state governance IDs. The `traceability.json` append log also carries governance context.

**Solution surfaces**: This archive, governance packets (PAT-0011), governed proposal identity (PAT-0013), traceability enrichment across all three trace surfaces, update_trace_governance().

---

## PRB-0010: Pattern Governance

**Status**: active — partially implemented (R101-R110)
**Provenance**: user-authored (governance gaps analysis)
**Regions**: governance layer, audit process, pattern archive, proposal-state, model policy, path registry

Established patterns exist and are cataloged. The governance loader parses the archive into planspace indexes, builds per-section governance packets, and threads them into prompt context and freshness hashing. R103 added proposal-time governance identity (PAT-0013) so proposals declare which patterns they follow and which deviations they require. R104 deepened the loader to parse `template` and `conformance` fields from patterns with multiline continuation support, so runtime pattern records carry actionable conformance criteria — not just titles and instances. Governance packets now thread into microstrategy, alignment, and ROAL prompts (PAT-0011 coverage expansion). R107 completed catalog metadata (all patterns carry Regions/Solution surfaces), made runtime packet filtering applicability-aware (missing metadata = ambiguity), collapsed ~47 duplicated model-policy fallback literals into centralized `resolve()` calls, and bridged packet ambiguity to descent readiness gating. R110 fixed archive→runtime projection fidelity: wrapped bullet continuation lines are now preserved, numbered template lists are parsed as individual array entries, and representative contract tests cover the real catalog shape. R110 also made scan-stage and substrate-stage related-files signal families explicitly registry-distinguished (PAT-0003) and completed the last two local model-policy fallback sites (PAT-0005). CP-1 saturation sweep migrated authoritative consumers for existing registered durable families across scan, intent/prompt, tool-surface, and freshness/hash modules. Accessor/pattern completion requires **authoritative-consumer saturation and truthful health notes**, not just accessor addition. Runtime advisory presence is becoming structural through pattern_ids and pattern_deviations in proposal-state. Full enforcement at proposal time remains advisory.

**Solution surfaces**: Pattern archive, governance loader (projection fidelity R110), governance packets (PAT-0011 applicability-aware R107), governed proposal identity (PAT-0013), centralized model policy resolver (PAT-0005 R110), audit process pattern alignment phases, registry-distinguished signal families (PAT-0003 R110).

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

**Status**: active — substantially addressed (R109-R110), scope expanded R112
**Provenance**: audit-inferred (R109)
**Regions**: integration tests, regression tests, component tests, authoritative runtime surfaces

Tests written as source-text archaeology create fragile regressions. R110 extended PAT-0015 to require representative round-trip contract tests. The suite has converged on positive contract testing. R112: PAT-0015 scope expanded to require that executable audit/eval surfaces themselves have positive current-layout contracts. Missing inventory-truth, retirement-boundary, and eval-entrypoint contracts are now explicitly scoped as gaps.

**Solution surfaces**: PAT-0015 (Positive Contract Testing), positive behavioral assertions over source-grep absence tests, output-shape contracts, representative round-trip contract tests for high-risk handoffs (R110), executable eval/audit surface contracts (R112).

---

## PRB-0018: Legacy Surface Residue / Incomplete Surface Retirement

**Status**: resolved (R112)
**Provenance**: audit-inferred (R111), resolved R112
**Regions**: live agent inventory, legacy scripts, migration docs

Legacy execution surfaces remain under live discovery trees after migration. R111 deleted 3 dead files (`orchestrator.md`, `exception-handler.md`, `state-detector.md`). R112 deleted `src/dispatch/agents/monitor.md` — the last unrouted agent in the live discovery tree. Runtime inventory now matches: 48 agents / 48 routes / 12 namespaces.

**Solution surfaces**: PAT-0016 (Runtime Inventory Truth & Surface Retirement), dead file deletion, discovery tree hygiene.

---

## PRB-0019: Runtime Inventory Drift / Authoritative Interface Mismatch

**Status**: active — substantially addressed (R111-R116)
**Provenance**: audit-inferred (R111)
**Regions**: system-synthesis.md, governance/audit/prompt.md, operator docs, eval adapters, pyproject.toml, runtime-facing templates, agent definitions

Authoritative path/count/entrypoint claims are hand-maintained and diverge from live runtime registries after structural migrations. R111 corrected system-synthesis.md and governance/audit/prompt.md (paths/counts). R112 corrected governance/audit/prompt.md region paths to current layout (48 agents / 12 namespaces), fixed pyproject.toml (stale pythonpath entries), and updated eval harness + trigger adapter imports. R113 fixed `src/flow/engine/task_dispatcher.py` docstring, `src/models.md` stale reference, and `system-synthesis.md` problem count. R114 fixed stale runtime substrate references across SKILL.md, implement.md, models.md, rca.md, templates (`implementation-alignment.md`, `rca-cycle.md`), and agent definitions (`risk-assessor.md`, `execution-optimizer.md`) — removing references to retired `scan.sh`, `substrate.sh`, `section-loop.py`, worktree model, and stale `agents/` paths. R116 corrected `system-synthesis.md` stale counts (21→23 problems, 16→18 patterns) and added PAT-0017/PAT-0018 to the governance-layer pattern list.

**Solution surfaces**: PAT-0016 (Runtime Inventory Truth & Surface Retirement), registry-derived inventory, atomic doc updates with code changes.

---

## PRB-0020: Governance Self-Report Drift / False Health Reporting

**Status**: active — substantially addressed (R112-R117)
**Provenance**: audit-inferred (R112)
**Regions**: governance/patterns/index.md, governance/risk-register.md, governance/problems/index.md, governance/audit/history.md

Governance self-report surfaces (pattern health notes, risk register status, problem archive status, audit history counts) diverge from actual codebase state. R112-R113 corrected pattern health notes and risk register status, but R113 falsely claimed PAT-0001/PAT-0003 as "Healthy" and RISK-0007 as "resolved" while bypasses and unsaturated families remained. R114 corrected all four pattern health notes to reflect actual code state: PAT-0001 now genuinely healthy (last bypass fixed), PAT-0003 truthfully reported as "improved but not converged," PAT-0015 as "improved," PAT-0016 as "improved." R116 updated PAT-0003 health notes to include reconciliation family migration, PAT-0015 health notes to include doctrine-projection and reconciliation-family positive contract tests, and added PAT-0018 health note. R117 corrected PAT-0018 known instances (11th surface was missing) and PAT-0003 health note (4 new accessors + 7 consumer migrations).

**Solution surfaces**: PAT-0016 scope expansion to governance self-reports (R112), truthful pattern health notes, audit-time verification of present-tense claims.

---

## PRB-0021: PathRegistry Consumer Saturation / File-Level Accessor Incompleteness

**Status**: substantially addressed (R113-R117)
**Provenance**: audit-inferred (R113), reopened R114, substantially addressed R115, reconciliation family R116, residual sweep R117
**Regions**: PathRegistry, freshness/hashing, reconciliation, readiness, dispatch prompts, proposal cycle, flow system, traceability, coordination, signals, intent

Durable artifact families used at multiple authoritative sites had only directory-level accessors or no accessors at all. R113 added `reconciliation_result()` and `execution_ready()` file-level accessors. R114 added 5 flow family accessors and fixed 4 existing-accessor bypasses. R115 added accessors for 6 remaining families (decision md/json, governance synthesis-cues/index-status, trace-index, intent-triage signal/prompt/output, coordination problems/escalation/fix/bridge/align/task-request, bridge-tools prompt/output/escalation) and migrated ~30 consumer sites. R116 added 3 reconciliation family accessors (`reconciliation_requests_dir()`, `reconciliation_request()`, `reconciliation_summary()`), migrated `queue.py` and `cross_section_reconciler.py` consumers, and normalized `load_reconciliation_result()` to accept planspace root exclusively (eliminating mixed-root semantics). R117 added 4 more accessors (`recurrence_signal()`, `coordination_recurrence()`, `related_files_signal()`, `global_decision_json()`) and migrated 7 consumer sites: proposal_state() bypasses (traceability_writer, section_communicator), note_ack_signal() bypass (problem_resolver), recurrence family (recurrence_emitter, problem_resolver, planner), context_sidecar (global decisions, related-files signals). Remaining: glob-pattern consumers for decisions and recurrence discovery, `decisions.py` repository functions with raw Path parameter, flow relpath helpers (kept by design for DB storage).

**Solution surfaces**: PAT-0003 file-level accessor requirement, family-level accessor addition + atomic consumer migration, flow family accessors (R114), 6-family saturation sweep (R115), reconciliation family migration (R116), residual sweep (R117).

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
