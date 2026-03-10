# Audit History

Concern-based audit log. 110 rounds completed in the philosophy audit cycle.
Rounds 1-20 are summarized as conclusions. Rounds 21-50 aggregated as settled
concerns. Rounds 52-91 aggregated as settled concern threads with established
patterns. Rounds 92+ are recent with full detail.

Round 51 was a design-document audit (wrong frame — design was already
implemented). Philosophy audit resumed from Round 52 (intent layer wiring).

---

## Rounds 1-20: Conclusions & Patterns

### Architecture Evolution (Rounds 1-4)

The system's core architecture was established in Rounds 1-4:
- **Structured signals over heuristics**: Moved from substring/regex/prefix scanning to structured JSON signals with GLM adjudicator fallback (R1, R4, R7, R8).
- **Filepath references over inline content**: Prompt builders embed artifact paths, not content (R1, R13, R19).
- **Agent-owned decisions**: Scripts dispatch and route; agents decide. No script-side semantic judgment. Established as a permanent design boundary in R8 after cycling between script heuristics and agent delegation.
- **Adaptive loops**: Fixed iteration counts replaced with hash-based convergence detection and stall escalation (R4, R5).
- **Microstrategy layer**: Added between aligned proposal and implementation as a conditional tactical breakdown (R1, R2, R4).
- **Mode-aware prompts**: Greenfield/brownfield/hybrid detection drives prompt context (R1, R4, R8).
- **Codemap routing table**: Structured subsystems/entry-points/interfaces/unknowns/confidence, verified by GLM sampling (R8).

### Convergence Patterns (Rounds 5-14)

Key recurring themes:
- **Artifact path drift**: Glob patterns, output paths, artifact references repeatedly fell out of sync. Root cause: no centralized artifact naming. Each fix was ad-hoc. Class has not recurred since R14.
- **Heuristic removal cascade**: Script-side substring/regex heuristics were removed iteratively across R6-R18. Each round surfaced 1-2 residual heuristics.
- **Note lifecycle**: Note IDs, acknowledgment signals, and triage fast-paths evolved across R4, R9, R13 to become a mechanical system.
- **Fail-closed defaults**: Replaced fail-open patterns with fail-closed behavior across 24+ parse sites (sweep completed R40.5).
- **Brute-force elimination**: Replaced "scan all files" fallback with strategic escalation in R14. Replaced brute-force requeue with hash-based targeted requeue in R21.

### Test Infrastructure (Round 17+)

Test suite bootstrapped in R17 with 113 tests, grew to 402 by R51. Philosophy: mock `dispatch_agent` ONLY; everything else real (file I/O, SQLite, hashing, prompt generation).

### Key Permanent Decisions

- **R9**: Rejected "create dedicated scan agent files" — scan prompts are dynamic templates.
- **R10**: Rejected "centralize artifact naming into artifacts.py" — fix specific bugs instead.
- **R15**: INVALID FRAME collapsed into PROBLEMS verdict.
- **R18**: `execution_order` replaced with structured `batches` field.

### Execution Model (Settled R1-R20)

Two kinds of LLM involvement:
- **Agent definition files** (`agents/*.md`): Reusable REASONING METHOD via `--agent-file`. No runtime paths.
- **Dynamic dispatch prompts**: One-shot TASK with specific runtime context via `--file`. MUST contain file paths.
- Scripts are deliberately thick — absorb all mechanical capacity so LLMs spend tokens only on reasoning.
- Scripts MUST NOT interpret meaning from agent text (no keyword matching, substring detection).

---

## Rounds 21-50: Settled Concerns (Summary)

These concerns generated violations but are now guarded by regression tests:

- **Alignment-not-audit naming** (R21)
- **Operational/pipeline agent placeholders** (R20-R27): Banned `<planspace>`, `<codespace>` from agent files
- **Greenfield pause label consistency** (R21)
- **Targeted requeue** (R21): Hash-based, not brute-force
- **Distribution integrity / manifest guard** (R11/R24)
- **Doc/template drift lint** (R23)
- **Non-git fingerprinting** (R23/R26)
- **Scan dispatch boundary** (R25): scan/dispatch.py separate from section_loop/dispatch.py (different stage)
- **Bridge note propagation** (R27-R28-R48): End-to-end wired with note-triggered requeue
- **Mode inputs in hash** (R27)
- **Scan loop closure** (R29): Bounded to `_MAX_SCAN_PASSES` (2)
- **Scan summary idempotency** (R29/R42)
- **Bridge directive type safety** (R29)
- **Adjudicator tax removal** (R30)
- **Problem frame threading** (R31/R33): In alignment surface, prompts, convergence hash
- **Scope-delta payload preservation** (R31)
- **Related files unified parsing** (R33)
- **Signal taxonomy synchronized** (R36)
- **Invocation style** (R36): `--file "$PROMPT"` not inline
- **Tool registry schema** (R37)
- **Scan templates extension-neutral** (R37)
- **Note content by path** (R38)
- **Stale tool surface removal** (R29)
- **Tool friction signal** (R34)
- **Loop contract completeness** (R33/R36)
- **Scope-delta adjudication parsing** (R37)
- **Alignment template JSON verdict** (R28)
- **Coordination plan retry+fail-closed** (R32)
- **Invalid frame as structural failure** (R32)
- **Deep scan tier ranking failure propagation** (R41)
- **Extractor tools fail-closed** (R41)
- **Audit bundle contract: deployed layout** (R46-R47)
- **Convergence gate completeness** (R46/R48): Outstanding problems block completion
- **Tool surface after repair** (R46)
- **Bridge-tools loop closure** (R43-R45): Structured signal + verification + downstream wiring
- **Microstrategy output enforcement** (R43)
- **Codemap prompt coherence** (R42)

---

## Rounds 52-91: Settled Concern Threads

These concern threads tracked violations across R52-R91. Each is now guarded by established patterns and regression tests. The **Pattern** line is the settled rule.

- **Codemap Corrections Propagation** (R21-R83): Codemap authority is a single bundled surface; _resolve_codemap() in context_assembly.py bundles corrections automatically. **Pattern**: new consumers get corrections through context_assembly, not per-surface wiring.

- **Fail-Closed Parsing** (R40.5-R99): All 24+ JSON parse sites use warn+rename+fail-closed. Extended through decisions.py, blocker signals, proposal-state, ROAL serialization, readiness gate, research orchestrator. **Pattern**: new code must not introduce `except: pass`.

- **Corruption Preservation** (R49-R97): rename `.malformed.json` + log warning + continue. Applied across all signal readers, inline parse sites, ROAL typed loaders. **Pattern**: any JSON reader encountering corrupt data must rename, log, and continue — never silently overwrite or discard.

- **Model Policy Propagation** (R75-R99): All dispatch callsites use policy-driven model names. task_router policy_key fields, scan.* namespace, 4 research policy keys. Sweep guards: TestNoHardcodedModelInPromptSurfaces, TestModelPolicyCompleteness. **Pattern**: task_router.py policy_key and agent frontmatter must agree with read_model_policy() defaults.

- **Execution Philosophy Migration** (R70-R98): Task submission replaces direct dispatch on all strategic surfaces. All split-brain surfaces retired (state-detector, exception-handler, pre-run.db control plane). **Pattern**: new prompt surfaces must use task submission and template safety, not direct dispatch.

- **Per-Surface Contract Synchronization** (R72-R88): Agent files must match runtime prompt/parser contracts. Synchronized across 15+ agent files, proposal-state schema, microstrategy ownership, blocking_research_questions as fifth blocker class. **Pattern**: when runtime contract evolves, agent file Output section and task vocabulary must be updated atomically; fallback prompts pass signals not competing criteria.

- **Context Sidecar Wiring** (R73-R74): Ordering bug fixed — sidecars materialized before prompt rendering via materialize_context_sidecar(). **Pattern**: sidecar creation → prompt rendering → dispatch (which refreshes sidecar).

- **Concern-Based Routing** (R73-R85): Related-files gates removed from impact routing and Phase 2 coordination. **Pattern**: sections are concerns, not file bundles; related files are a routing hypothesis, not an identity gate.

- **Intent Failure Semantics** (R75-R96): Philosophy absence blocks execution; triage failure defaults to full mode. Philosophy bootstrap redesigned as gated workflow with NEED_DECISION/NEEDS_PARENT. **Pattern**: uncertainty → more strategy; missing root frame → block, not degrade; missing philosophy → ask user.

- **Dispatch Boundary Enforcement** (R77-R87): Every dispatch requires agent_file and validated prompt. Prompt safety via shared prompt_safety.py module covers all 13 dispatch sites across section-loop and scan stages. **Pattern**: every prompt must be payload-backed and pass validation; missing payloads fail closed.

- **Canonical Doc Verification Frame** (R76-R81): Workflow docs teach alignment, not completeness auditing. lint-doc-contracts.sh (primary positive-contract checks) + lint-doc-drift.sh/lint-audit-language.sh (secondary blacklist guards). **Pattern**: alignment/divergence/constraint language; correct binary invocation; concern-based coordination framing.

- **Flow System** (be544f0-R99): Task chains, fan-out accumulation gates, completion reconciliation. Flow artifacts fail-closed; dispatch uses process exit status; freshness tokens checked before dispatch; all tasks require payload-backed context. **Pattern**: agents declare next steps in structured v2 JSON; scripts reconcile mechanically; freshness fingerprint must hash all load-bearing artifacts uniformly.

- **Live Scenario Eval** (R81): Optional dev/audit tool — 6 scenarios across 4 strategic surfaces with real model + real agent file + real prompt. **Pattern**: scenario evals verify intended decision class; NOT a CI dependency.

- **QA Dispatch Interceptor** (R84): Optional contract compliance layer. Context-blind by design. Enabled via parameters.json. **Pattern**: fail-open on QA errors; agent contracts are the complete truth.

- **Reconciliation Adjudication** (R84-R88): Agent-adjudicated semantic grouping after exact-match first pass. Candidate payloads in artifact files, not inline. **Pattern**: scripts collect and dispatch; agents do semantic judgment; fail-open on non-success exits.

- **Unconditional Discovery Publication** (R85): Discoveries published regardless of readiness outcome. **Pattern**: readiness gates implementation; publication gates nothing.

- **Scope-Delta Correlation Identity** (R85-R86): All scope-delta producers include stable delta_id. Coordination keyed by delta_id with path map for write-back. **Pattern**: every scope delta carries unique delta_id; decisions applied to exact originating artifact.

- **Mode-Agnostic SIS** (R85-R88): SIS trigger is structural evidence, not project mode. Mode is telemetry only (greenfield/brownfield/hybrid/unknown). **Pattern**: mode is observation, not routing key; absent observations must not be synthesized from defaults.

- **ROAL Script/Agent Boundary** (R97): LIGHT mode dispatches execution-optimizer, not script-computed posture. **Pattern**: scripts enforce mechanically; agents decide strategically — even in lightweight paths.

- **Upward Routing Completeness** (R98): blocking_research_questions and research_questions now routed/consumed. **Pattern**: every typed discovery field must have both a writer and at least one live consumer.

- **Root-Reframing Semantics** (R98): requires_root_reframing preserved end-to-end through reconciliation, adjudication, and coordination. **Pattern**: semantic fields emitted by multiple writers must be preserved atomically by all downstream consumers.

- **Seam vs Scope Taxonomy** (R98): Shared seams mapped to needs_parent, not scope_expansion. **Pattern**: blocker rollup taxonomy must match readiness gate routing taxonomy.

- **Research-First Intent Layer** (41fedb3-R99): Research as first-class discovered work in the task queue. Planner → domain researchers → synthesizer → verifier. Research artifacts feed proposals and expansion. Implementation feedback surfaces as new source type. **Pattern**: research is discovered work, not implicit model knowledge; research artifacts must participate in all canonical computations (freshness, hashing) uniformly.

---

## Intent Layer (R51 — implemented, not separately audited)

Implemented from design.md in commit 740baa1 (19 files, 6 agents, 4 Python
modules, 24 tests). Design-document audit attempted (R51) but was wrong frame.
Intent layer audited normally from R52 onward as part of codebase.

---

## Per-Round Index

| Round | Commit | Tests | Violations | Summary |
|-------|--------|-------|------------|---------|
| 21 | a5c9a6b | 154 | 6 | Recursion guardrail, audit->alignment rename, corrections, greenfield label, targeted requeue, fail-closed mode, placeholders |
| 22 | a50125d | 155 | 5 | Lint audit language, corrections in alignment, project-mode fail-closed, friction fail-closed, placeholders |
| 23 | 5aa9f8c | 18 RG | 4 | Corrections in validation/hash/coordination/re-explore, doc drift lint, non-git fingerprint, feedback fail-closed |
| 24 | 99a2ece | 150 | 3 | Distribution integrity, scan corrections cache, manifest guard |
| 25 | 2ff7d15 | 161 | 7 | Scan Python refactor: PYTHONPATH, markers, tier ranking, corrections, scan dispatch boundary |
| 26 | 575a3db | 169 | 5 | Feedback schema, corrections in updater/cache, codemap reuse, scan integration tests |
| 27 | 38c0fa7 | 174 | 3 | Bridge note propagation, codespace ban in pipeline agents, mode in hash |
| 28 | 3461aa6 | 178 | 3 | Bridge Note ID format, coordination regex, alignment template verdict |
| 29 | db84d5b | 190 | 6 | Scan summary idempotency, bounded scan loop, cached feedback, bridge type safety, scan models, stale tools |
| 30 | 8a66c89 | 194 | 3 | Model policy in section-loop, adjudicator tax removal, scan policy |
| 31 | 56875e3 | 202 | 4 | Problem frame threading, fail-closed signals, scope-delta payloads, model policy (16 keys) |
| 32 | 1653354 | 213 | 4 | Coordination plan fail-closed, escalation/fix model policy, INVALID_FRAME structural, feedback ack |
| 33 | 16161cf | 225 | 4 | Unified related-files parser, signal instructions, problem frame in hash, loop-contract |
| 34 | 5abf031 | 238 | 4 | Tool registry fail-closed, microstrategy fail-closed, template models, tool friction |
| 35 | 4d19bf7 | 247 | 2 | Reexplore/coordination model parameterization, sweep guard |
| 36 | 5d2c220 | 253 | 2 | Codex dispatch --file, signal taxonomy synchronized |
| 37 | abacb91 | 264 | 4 | Scope-delta parsing, escalation policy-driven, tool registry schema, scan templates neutral |
| 38 | 7a42e9e | 275 | 4 | Blocker fail-closed, tool registry warning, note content by path, note-ack preservation |
| 39 | 85a88e1 | 284 | 3 | Schedule template model, blocker rollup malformed, traceability preservation |
| 40 | 031079c | 293 | 3 | Scope-delta preservation, note-ack read-path, related-files warning |
| 40.5 | 4f9eb5d | 293 | 0 | Proactive sweep: 11 silent handlers fixed, 13 justified |
| 41 | 0d2dbd4 | 303 | 2 | Deep scan tier ranking failure, extractor tools fail-closed |
| — | 3698305 | 303 | — | Structural: src/ directory + CI/CD deploy (not an audit round) |
| 42 | e7f3caa | 313 | 4 | Skip-hash conditional, section hash normalization, exploration appends, codemap coherence |
| 43 | 266b3c2 | 321 | 2 | Bridge-tools loop closure, microstrategy output enforcement |
| 44 | 18b6857 | 337 | 2 | Bridge-tools downstream wiring, scan validation fail-closed |
| 45 | 45c0b56 | 348 | 4 | Escalation verification, digest regen, read_agent_signal preservation, microstrategy preservation |
| 46 | 483372a | 357 | 3 | Completion gate, tool surface after repair, read_signal_tuple preservation |
| 47 | 8100b98 | 360 | 2 | Lint scripts portable, pyproject removed from bundle |
| 48 | 4ac3d5d | 365 | 3 | Note-triggered requeue, stall termination outstanding, impact comment drift |
| 49 | 672d0d1 | 372 | 3 | Corruption-preservation at 6 inline parse sites |
| 50 | 70934eb | 378 | 4 | Corrections in freshness, cycle-budget/recurrence/tier-ranking preservation |
| 51 | 740baa1 | 402 | — | Intent layer implemented (design audit — wrong frame, skipped) |
| 52 | a9a548e | 407 | 6 | Surface ID normalization, fail-closed philosophy, intent refs in alignment, regression guards |
| 53 | 26dc43d | 415 | 7 | Contract drift (4 agent files), TODO ordering, fail-closed parsing, budget propagation |
| 54 | a1c9bae | 423 | 8 | Heuristic triage (remove hard rules), recurrence adjudication (remove diminishing threshold), layout-agnostic tests, intent docs |
| 55 | cc303f5 | 441 | 10 | Corruption preservation (7 sites), codemap corrections in intent pack, cache warning, budget enforcement on expander workload, layout-agnostic regression guards |
| 56 | 8b39e9f | 460 | 5/4 | Queue semantics for pending surfaces, agent-selected philosophy sources, updater signal preservation, axis budget enforcement |
| 57 | 6eb1ea5 | 478 | 6 | Deep scan preservation, updater validity rename, ref expansion warnings, gate-type messaging, surface persistence on misalignment, signal taxonomy docs |
| 58 | 7445ffa | 485 | 3 | Scope-delta adjudication write-back fail-closed, tool-registry coordination preservation (copy), related-files update signal preservation |
| 59 | 95bd450 | 502 | 3 | Catalog per-root quotas (codespace-first, artifacts excluded), philosophy source map grounding validation (fail-closed), intent pack hash-based invalidation |
| 60 | 1f9bbdb | 514 | 3 | Bounded catalog walk (os.walk replaces sorted rglob), tool OSError contract (extract-docstring-py), layout-agnostic project root resolution (conftest anchor walk) |
| 61 | 48de8c4 | 530 | 4 | Alignment surface intent propagation (4 artifacts), heuristic reading replaces "read each one", intent refs in generation templates (proposal+impl), agent-steerable catalog extensions |
| 62 | f37893f | 527 | 0 | Accuracy-first zero-risk-tolerance across all agents (principle #7 in SKILL.md, accuracy sections in 12 agent/template files) |
| 63 | af8ca99 | 527 | 3 | Rename gpt-5.3-codex models to gpt-codex (model didn't exist in agents binary), list all agents in SKILL.md, fix mock_dispatch in microstrategy tests, remove content-grep test class |
| 64 | — | 561 | — | Stage 3.5 SIS (Shared Integration Substrate): 3 agent files, substrate/ Python module, substrate.sh shim, schedule step 3.5, model policy keys, prompt template substrate awareness, 34 new tests |
| 64.5 | — | 568 | 6 | SIS audit fixes: workflow.sh decimal step parsing, implement.md Stage 3.5 docs, prompts.py contract-boundary rewrite, runner substrate.md verification + prune-signal + seed-signal, config-driven trigger threshold |
| 65 | — | 572 | 2/8 | Codemap corrections for SIS prompts, _read_project_mode corruption preservation. 6 of 8 already fixed in R64.5. |
| 66 | — | 576 | 4/13 | SKILL.md agent listing removed, inline dispatch → --file in 7 surfaces, impact prefilter seam awareness. 9 of 13 already fixed. |
| 67 | — | 584 | 10 | Lightweight intent uses cached artifacts + intent-judge, expansion budget pauses for parent, philosophy-distiller per-project, SIS mode "unknown" accepted, model-policy fail-closed rename |
| 68 | — | 595 | 8 | Global philosophy unconditional, triage reads artifacts, problem frame flexible, semantic caps advisory, SIS signal-driven trigger, triager anti-pattern updated |
| 69 | — | 608 | 4 | Philosophy expander source-grounded semantics, grounding revalidation after expansion, catalog fingerprint, ambiguous candidate full-read verification |
| 70 | — | 636 | 8 | Execution philosophy migration: task submission in 5 prompt surfaces, template safety, context sidecar consumption, retired control plane |
| 71 | 4b3f008 | 654 | 7 | Ratify task-submission model: conversation-state supersession, implement.md edits, SKILL.md dispatch, orchestrator archived, task_ingestion closes loop, task_dispatcher routes through dispatch_agent, agent file descriptions normalized |
| 72 | 084a642 | 674 | 9 | Contract sync: 4 agent files updated, philosophy-source-verifier split, rca.md dispatch, "sub-agent" removed, cross-section prompt seam framing |
| 73 | 98be15d | 690 | 5 | Contract vocab aligned, template safety on 3 prompt builders, context frontmatter + sidecar refs, concern-only sections reachable |
| 74 | d305762 | 708 | 4/3 | Sidecar ordering fix, task_ingestion validates, prompt templates clarify task timing, implement.md corrects mechanism descriptions |
| 75 | 4f5bb10 | 726 | 3 | Philosophy absence blocks execution, triage failure defaults to full, task_router policy_key field + 3 drifted defaults fixed |
| 76 | aec2a33 | 749 | 5 | research.md divergence review, baseline.md constraint traceability, models.md fixes, implement.md script-owned control plane, lint guardrails broadened |
| 77 | 1afb761, 9e238a3 | 771 | 6+user | Impact normalizer agent file, template safety fail-closed, payload validation, stale delegation language, `agents` binary across 6 files, haiku removed |
| 78 | 344c887 | 780 | 5/7 | audit.md rewritten to concern-based problem decomposition, SKILL.md references updated, lint extended, bare MagicMock fixed, runtime dispatch to `agents` binary |
| — | be544f0 | 962 | — | **Flow system**: chains, gates, accumulation. flow_schema.py, task_flow.py, flow_catalog.py, DB gates + gate_members tables, ingest_and_submit replaces ingest_and_dispatch, 10 regression guards |
| 79 | a210d87 | 1025 | 6 | Flow fail-closed, dispatch truthfulness (.meta.json sidecar), section identity, freshness gate, vocabulary sync |
| 80 | 942bdd9 | 1054 | 5 | Mandatory payload context, dispatch meta fail-closed, doc sync (models.md, implement.md, implementation-strategist) |
| 81 | e6c0c2d | 1069 | 4 | Classification-only re-explorer, single-method microstrategy, lint-doc-contracts.sh, live scenario eval harness (6 scenarios) |
| 82 | 14c230e | 1086 | 7 | Canonical policy_key on all task types, structured blocker signals, freshness unification, corruption preservation on decisions |
| 83 | 2fbb107 | 1099 | 5 | Scan prompt normalization, shared prompt_safety.py, codemap authority bundling |
| 84 | b21bf0f | 1324 | 3/5 | Proposal-state contracts, execution-readiness gate, two-pass section loop, QA dispatch interceptor, reconciliation adjudicator |
| 84.5 | ca818d4 | 768 | 0 | Cleanup: deleted regression guards (556 tests), 3 lint scripts, archive folder. Moved evals/ to repo root. |
| 85 | 6466c10 | 768 | 7 | Fail-closed proposal-state, unconditional discovery publication, delta_id, substrate-trigger path, reconciliation corruption preservation, concern-based Phase 2, mode-agnostic SIS |
| 86 | 4704b97 | 768 | 6 | Proposal-oos delta_id, coordination adjudication keyed by delta_id, hybrid root scan, intent-triage surface sync |
| 87 | e63699c | 768 | 3 | blocking_research_questions fifth blocker class, prompt safety propagation to 13 sites, canonical coordination recheck hash |
| 88 | 013b1ac | 768 | 3 | Microstrategy ownership to sole decider, reconciliation fail-open fix, mode telemetry brownfield→unknown |
| — | 5f312b5 | 1302 | — | **ROAL implementation**: Risk-Optimization Adaptive Loop. lib/risk/ subpackage, risk-assessor + execution-optimizer agents, integration hooks, 534 new tests |
| 89 | 6f5b923 | 1327 | 10 | ROAL audit (3 waves): contract truthfulness, execution semantics (RiskPlan mechanically executable), adaptive refinement (LIGHT mode, hysteresis, history adjustment) |
| 90 | 61d2dfb | 1338 | 7 | ROAL completion: prompt/evidence/policy alignment, frontier artifacts in implementation prompts, typed step classes (STABILIZE/COORDINATE reachable) |
| 91 | d9e856a | 1348 | 6 | ROAL integration: typed contracts for intent-triager risk hints, LIGHT hint as authoritative preference, roal-input-index.json, bounded multi-slice frontier-execution loop |
| 92 | a0401e3 | 1348 | 4 | Doc/boundary cleanup: runtime paths removed from 3 agent files, archive deleted, "Task tool" phrasing replaced, models.md xhigh heading corrected |
| 93 | 985d42f | 1349 | 7 | ROAL skip removal (SKIP→LIGHT normalization), 12 bare-prompt surfaces migrated to write_validated_prompt, doctrine sync (models.md, SKILL.md, implement.md) |
| 94 | 41818d8 | 1352 | 3 | Complete skip removal at live triage surface, scan_dispatch defaults fixed (claude-opus→glm), models.md decision tree corrected |
| 95 | 291de18 | 1352 | 2 | Implementation-strategist contract repair: mechanical/structural distinction for proposal fidelity. 7 hold-the-line proposals (no code change) |
| 96 | 40fabca | 1361 | 5 | Philosophy bootstrap redesign: standard signals, selector failure separation (4 states), content quality contracts, user-interaction bootstrap path |
| 97 | d8a308e | 1372 | 5 | Stale surface retirement (state-detector + exception-handler deleted), ROAL light agent boundary (optimizer dispatch), ROAL corruption preservation (typed loaders) |
| 98 | d959087 | 1379 | 7 | Upward routing (blocking_research_questions routed, research_questions consumed), root-reframing threading, seam vs scope taxonomy, stale execution-model surfaces cleaned |
| — | 41fedb3 | 1419 | — | **Research-first intent layer**: 4 task routes, 4 agents, 8 PathRegistry accessors, readiness routing, proposal/implementation prompt wiring, research-derived surfaces, implementation feedback surfaces, 40 new tests |
| 99 | f7090c4 | 1444 | 6 | Research layer instantiation: prompt/status substrate, canonical freshness extension (5 research artifacts), lightweight escalation on structured surfaces, 4 research policy keys + doctrine sync |
| 100 | 163f163 | 1463 | — | Research flow integration: plan executor bridge, reconciler hooks, cycle-aware status (trigger_hash + cycle_id), flow catalog packages, corruption preservation (3 typed loaders), agent contracts updated, prompt authority (7 prompt writers) |
| 101 | 4964c6d | 1489 | — | Governance layer: loader (markdown→JSON indexes), per-section packets, prompt/freshness/traceability threading, post-implementation assessment (agent + reconciler hooks + risk-register/blocker signals) |
| 102 | 826355d | 1489 | 8 | Governance closure: pattern catalog (PAT-0011/0012 added, PAT-0002/0003/0005 refined), trace_map path fix, traceability enrichment, debt signal promotion, corruption preservation normalization, eval contract sync, PHI-global proportional risk, PRB-0011/0012/0013 added |
| 103 | d8cb68a | 1489 | 5 | Governance control-loop closure: PAT-0013 (Governed Proposal Identity), PAT-0011 updated (Applicable Governance Packet Threading with section-scoped candidates), PAT-0012 updated (full trace enrichment + bounded stabilization consumer), CP-1 proposal-state governance fields, CP-2 governance packet candidate filtering + downstream wiring (microstrategy/alignment/ROAL), CP-3 trace lineage from proposal-time, CP-4 bounded debt promotion consumer, PRB-0014 added |
| 104 | c6756f0 | 1489 | 6 | Governance depth: CP-1 multi-signal packet applicability (keyword overlap + problem-frame text + applicability_basis tracking), CP-2 multiline field parsing + template/conformance extraction in loader, CP-3 governance identity validation in readiness resolver (deviations/questions block, orphan IDs caught), CP-4 idempotent debt promotion (content-hash dedup + receipts), CP-5 tool_surface.py NameError fix (fail-closed), pattern catalog full rewrite (PAT-0001/0002 instance lists expanded, PAT-0011/0012/0013 templates deepened) |
| 105 | 6ac9a3a | 1496 | 8 | Governance enforcement: TP-1 PAT-0005 (long-lived policy refresh + authoritative fallback sourcing), TP-2 PAT-0011 (explicit ambiguity states + bounded profile scope), TP-3 PAT-0012 (material-payload-aware dedup), TP-4 PAT-0013 (profile compatibility + non-empty identity when governance applies), CP-1 scan fallback regression + per-dispatch policy refresh in dispatcher/section-loop, CP-2 fail-closed readiness governance (empty identity/profile mismatch/missing packet all block) + alignment-judge contract, CP-3 packet applicability states + bounded profiles + governance_questions on ambiguity, CP-4 material-payload dedup key, CP-5 governance contract component tests (7 new), agent count corrected 48→47 |
| 106 | da09b8f | 1497 | 8 | Path root contracts and governance runtime: CP-1 resolve_readiness mixed-root fix (planspace not artifacts, 4 callsites + tests), CP-2 parse_pattern_index regions/solution_surfaces extraction (PAT-0011 scoping), CP-3 eval scenario stale imports fixed (readiness_gate + reconciliation), CP-4 eval harness fail-closed on import errors (PAT-0008), CP-5 PathRegistry path-island elimination in 5 modules (model_policy, scan_dispatch, substrate_policy, microstrategy_orchestrator, implementation_loop), CP-6 blocker normalization (dual-schema field access in readiness_gate + blockers.py) + system-synthesis proportional-risk alignment, TP-1 PAT-0003 root-semantics template steps + 6 instances, TP-2 PAT-0008 eval-scenario import failure bullet, TP-3 PAT-0009 blocker normalization bullet, TP-4 PAT-0011 applicability metadata requirement + Regions/Solution surfaces on 7 patterns |

| 107 | e5381fd | 1499 | 5 | Governance applicability and policy centralization: TP-1 PAT-0011 conformance (missing metadata = ambiguity not universal, catalog completeness required), TP-2 PAT-0005 conformance (ban retyped `.get("key", "literal")` fallbacks), CP-1 catalog metadata completion (Regions/Solution surfaces on PAT-0001/0002/0003/0005/0008), CP-2 packet.py `_filter_by_regions()` treats no-regions as ambiguous (not universal), CP-3 readiness resolver bridges packet ambiguity to descent gating, CP-4 ~47 duplicated model-policy fallbacks collapsed into `resolve()` with authoritative ModelPolicy defaults across 17 production files, CP-5 health notes synced (PAT-0003/0008/0009/0013 → healthy, PAT-0005/0011 → unhealthy), system-synthesis problem count 14→15, audit prompt agent count 48→47 |

| 108 | 4e58d53 | 1499 | 5 | Governance applicability, advisory transparency, model policy completion: TP-1 PAT-0005 updated (helper signature defaults, eval harness), TP-2 PAT-0008 narrowed to authoritative surfaces (advisory → PAT-0014), TP-3 PAT-0011 updated (forbid full-archive no-match hydration), TP-4 PAT-0014 added (Advisory Gate Transparency — QA/reconciliation degradation visibility), CP-1 packet.py `_filter_by_regions()` no-match returns empty candidates with governance questions (not full archive), CP-2 `build_governance_indexes()` returns False on parse failure + writes `index-status.json` + packet builder checks status, CP-3 removed 11 helper signature model defaults across 6 files + converted 3 `policy.get()` fallbacks to `resolve()` + evals/harness.py resolve(), CP-4 implement.md typo fix, PRB-0014 updated (R108 bounded no-match), PRB-0016 added (advisory degradation visibility), RISK-0003 (governance parse-failure degradation), RISK-0004 (QA degradation misreported as pass) |

| 109 | 930b611 | 1499 | 6 | Advisory transparency and testing philosophy: TP-1 PAT-0002 (payload content trust boundary clarified), TP-2 PAT-0003 (context_sidecar accessor), TP-3 PAT-0011 (synthesis cues "must include"), TP-4 PAT-0014 (structured reason_code taxonomy), TP-5 PAT-0015 added (Positive Contract Testing), CP-1 QA verdict parser DEGRADED (not PASS) for malformed/unknown + interceptor 3-tuple with reason_codes + dispatcher distinct qa:degraded logging + notifier reason_code flow + reconciliation PAT-0014 refs, CP-2 QA interceptor payload content validation (PAT-0002), CP-3 PathRegistry.context_sidecar() accessor + context_sidecar.py uses it, CP-4 synthesis cue extraction from system-synthesis.md Regions + packet builder consumption, CP-5 5 source-grep tests → positive contract tests (PAT-0015), CP-6 governance doc sync (PRB-0017 added, RISK-0004 resolved, system-synthesis pattern count 15), circular import fix (task_ingestion lazy task_flow import) |
| 110 | f12e10d | 1520 | 4 | Projection fidelity and contract coverage: TP-1 PAT-0003 (rule 7: distinct accessor names for related signal families, scan/substrate instances), TP-2 PAT-0015 (representative round-trip contract tests required, governance-loader and signal-path instances), CP-1 governance loader `_extract_bullets` fixed (continuation lines joined, numbered template items parsed as individual entries), CP-2 PathRegistry `scan_related_files_update_signal()` accessor + `related_files_update_dir()` docstring (scan/substrate families registry-distinguished), CP-3 representative contract tests (wrapped-bullet/numbered-template fixture, signal-path distinctness), CP-4 last two `policy.get()` fallbacks replaced (`proposal_loop.py` intent_judge → `resolve()`, `scan_related_files.py` validation → direct key access) + 4 stale GPT/Opus docstrings rewritten, governance docs (PRB-0010 R110, PRB-0017 R110, RISK-0005 resolved, RISK-0006 resolved) |

**Current state**: 1499 tests, ~380 files in codebase zip.
