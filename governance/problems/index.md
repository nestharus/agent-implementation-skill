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

**Status**: active
**Provenance**: audit-inferred (early rounds)
**Regions**: all artifact readers, JSON parsing, prompt output consumption

AI agents produce structured output (JSON, markdown with frontmatter) that may be syntactically or semantically malformed. Silent discard loses debugging evidence. Silent acceptance propagates bad data.

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

After code lands, we don't systematically assess what risks the implementation introduced: coupling, security surfaces, scalability bottlenecks, pattern drift, coherence friction. Post-implementation assessment was implemented in R101: queues assessment after implementation, validates result, routes verdict (accept/accept_with_debt/refactor_required) through structured signals. R102 added debt signal staging. R103 wired bounded stabilization consumer (`promote_debt_signals()` called after implementation pass in section-loop main). R104 made debt promotion idempotent. R105 made dedup material-payload-aware: key now covers severity, mitigation, acceptance_rationale, and governance lineage so changed risk re-promotes while unchanged debt stays idempotent.

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

**Status**: active — partially implemented (R101-R107)
**Provenance**: user-authored (governance gaps analysis)
**Regions**: governance layer, audit process, pattern archive, proposal-state, model policy

Established patterns exist and are cataloged. The governance loader parses the archive into planspace indexes, builds per-section governance packets, and threads them into prompt context and freshness hashing. R103 added proposal-time governance identity (PAT-0013) so proposals declare which patterns they follow and which deviations they require. R104 deepened the loader to parse `template` and `conformance` fields from patterns with multiline continuation support, so runtime pattern records carry actionable conformance criteria — not just titles and instances. Governance packets now thread into microstrategy, alignment, and ROAL prompts (PAT-0011 coverage expansion). R107 completed catalog metadata (all patterns carry Regions/Solution surfaces), made runtime packet filtering applicability-aware (missing metadata = ambiguity), collapsed ~47 duplicated model-policy fallback literals into centralized `resolve()` calls, and bridged packet ambiguity to descent readiness gating. Runtime advisory presence is becoming structural through pattern_ids and pattern_deviations in proposal-state. Full enforcement at proposal time remains advisory.

**Solution surfaces**: Pattern archive, governance loader (richer records R104), governance packets (PAT-0011 applicability-aware R107), governed proposal identity (PAT-0013), centralized model policy resolver (PAT-0005 R107), audit process pattern alignment phases.

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

Governance packets mirror the full problem/pattern/profile archives into every section, regardless of which problems and patterns are actually applicable to that section. This causes: (1) packet-membership checks become vacuous because every ID is in every packet, (2) any governance edit invalidates freshness for all sections instead of only affected ones, (3) context optimization is violated because agents receive irrelevant governance material. R103 added region-based candidate filtering. R104 expanded to multi-signal applicability. R105 added explicit applicability states (`matched`/`ambiguous_applicability`/`no_applicable_governance`), narrowed profile scope to governing profile only, and populated `governance_questions` when applicability is ambiguous rather than silently broadening. R106 fixed the pattern side: `parse_pattern_index()` now extracts `regions` and `solution_surfaces` fields, so `_filter_by_regions()` can actually scope patterns instead of treating all as universal. R108 removed the full-archive no-match fallback: `_filter_by_regions()` now returns empty candidates when nothing matches, and `build_section_governance_packet()` emits governance questions distinguishing "nothing matched" from "governance doesn't apply." Remaining gap: packet builder does not yet consume synthesis/runtime-region cues from `system-synthesis.md`.

**Solution surfaces**: Multi-signal section-scoped applicability with explicit ambiguity states (PAT-0011 R105), pattern applicability metadata extraction (R106), bounded profile scope, archive refs instead of full duplication, governance_questions populated on ambiguity, no-match bounded to empty candidates with explicit questions (PAT-0011 R108).

---

## PRB-0015: Evaluation Surface Drift / Silent Coverage Loss

**Status**: resolved (R106)
**Provenance**: audit-inferred (R106)
**Regions**: evals harness, eval scenarios, import validation

Declared eval scenario modules can drift to stale import paths without detection. The eval harness caught ImportError and continued with a warning, silently narrowing scenario coverage. Two scenario modules (readiness_gate, reconciliation) imported from a dead path (`lib.proposal_state_repository`). R106 fixed the stale imports, and made the harness fail-closed: `_load_all_scenarios()` now accumulates import failures and `main()` exits nonzero when any declared scenario fails to load.

**Solution surfaces**: Eval harness fail-closed on import errors (PAT-0008 R106), corrected scenario imports, harness exit code enforcement.

---

## PRB-0016: Advisory Surface Degradation Visibility

**Status**: active — pattern established (R108), code changes pending
**Provenance**: audit-inferred (R108)
**Regions**: QA interceptor, QA verdict parser, task dispatcher, lifecycle logging, reconciliation adjudicator

Advisory surfaces (QA interception, reconciliation adjudication) are deliberately fail-open: when they encounter internal errors, missing targets, or unparseable output, they fall back to baseline dispatch behavior. This is correct — advisory gates should not block execution. However, the degraded outcome is currently logged identically to genuine approval: QA parse failures map to PASS, QA exceptions are logged as `qa:passed`, and reconciliation fallback is invisible in lifecycle events. This erases the distinction between "the advisory surface evaluated and approved" and "the advisory surface failed, so dispatch fell back to baseline." Evidence preservation requires this distinction to be visible.

**Solution surfaces**: PAT-0014 (Advisory Gate Transparency), advisory status taxonomy (PASS/REJECT/ADVISORY_ERROR/UNPARSEABLE/TARGET_UNAVAILABLE), distinct lifecycle logging for degraded outcomes, reconciliation fallback visibility.
