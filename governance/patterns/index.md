# Pattern Archive

Established solution templates. Each pattern traces to philosophy and has known
instances in the codebase. Known instances are representative and should include
every currently authoritative region using the pattern; they are not required to
enumerate every helper callsite.

## Substrate Invariants

These are foundational invariants, not project-level patterns. They do not
change without a fundamental redesign.

- **Scripts dispatch, agents decide** — scripts do mechanical coordination
  (dispatch, check, log, persist); agents do reasoning (explore, understand,
  decide). Strategic decisions belong to agents.
- **File-path-based prompts** — prompts reference artifacts by file path, not
  inline content. Agents read what they need.
- **Task submission, not direct spawning** — agents request follow-on work via
  structured `TaskSpec`. No direct subprocess spawning.
- **Bounded typed substrate** — typed data flows with schema validation at
  boundaries.
- **Mode is observation, not routing** — greenfield / brownfield / hybrid
  describe what exploration found; they do not justify divergent proposal
  artifact shapes or descent rules.

## PAT-0001: Corruption Preservation

**Problem class**: Structured artifact read/write in a multi-agent system where
any writer may produce malformed output.

**Philosophy**: Fail-closed. Evidence preservation over silent discard. Preserve
debugging evidence instead of hiding it.

**Template**:
1. Use `read_json(path)` or a typed loader built on it for syntax-level parsing.
2. If parse succeeds, validate schema shape and semantic invariants.
3. On malformed JSON or schema mismatch, call `rename_malformed(path)`.
4. Return `None` or a documented fail-closed default.
5. Do not invent local corruption-preservation conventions unless the pattern is
   explicitly updated.

**Canonical instance**: `load_surface_registry()` in
`section_loop/intent/surfaces.py`

**Known instances**:
- `src/scripts/lib/core/artifact_io.py` — `read_json()` / `rename_malformed()`
  primitives
- `src/scripts/section_loop/intent/surfaces.py` — surface registry + research
  surface validation
- `src/scripts/lib/research/orchestrator.py` — research plan / status loaders
- `src/scripts/lib/governance/assessment.py` — post-implementation assessment
  reader
- `src/scripts/lib/repositories/proposal_state_repository.py`,
  `decision_repository.py`, `reconciliation_queue.py`,
  `reconciliation_result_repository.py`, and `strategic_state.py`
- `src/scripts/lib/tools/tool_surface.py` — tool registry and friction signal
  readers
- `src/scripts/section_loop/section_engine/blockers.py` and
  `src/scripts/lib/pipelines/coordination_problem_resolver.py`
- `src/scripts/lib/risk/serialization.py` — ROAL package / assessment / plan
  loaders
- `src/scripts/lib/services/signal_reader.py`,
  `src/scripts/lib/dispatch/dispatch_metadata.py`, and
  `src/scripts/lib/substrate/substrate_policy.py`

**Conformance**: Any new structured artifact reader MUST follow this pattern. No
silent `json.loads()` with bare except; no alternate corruption filename
conventions without catalog approval.

---

## PAT-0002: Prompt Safety

**Problem class**: Prompt injection, malformed prompt content, untrusted dynamic
values in prompt text, and payloadless dispatch drift.

**Philosophy**: Every prompt is a trust boundary. Safety is enforced
mechanically before dispatch.

**Template**:
1. Identify the dynamic / untrusted portion of the prompt surface.
2. Use one sanctioned form:
   - **Direct prompt file**: assemble the full prompt and call
     `write_validated_prompt(content, path)`.
   - **Template-wrapped prompt**: validate the dynamic body with
     `validate_dynamic_content()` before wrapping it in a trusted template, or
     validate the fully rendered prompt before writing when the builder does not
     split trusted vs dynamic content.
   - **Payload-backed dispatch**: validate agent-provided payloads or the
     assembled prompt before dispatch; do not fall back to metadata-only
     prompts.
3. Persist the exact prompt file used for dispatch.
4. On validation failure, fail closed or preserve the documented safe
   mechanical baseline.

**Canonical instance**: `write_research_plan_prompt()` in
`src/scripts/lib/research/prompt_writer.py`

**Known instances**:
- `src/scripts/lib/research/prompt_writer.py` and
  `src/scripts/lib/research/plan_executor.py`
- `src/scripts/lib/governance/assessment.py`
- `src/scripts/lib/intent/intent_triage.py`, `intent_surface.py`, and
  `philosophy_bootstrap.py`
- `src/scripts/section_loop/prompts/writers.py` and
  `src/scripts/section_loop/alignment.py`
- `src/scripts/lib/pipelines/reconciliation_adjudicator.py`,
  `microstrategy_orchestrator.py`, `scope_delta_aggregator.py`,
  `coordination_planner.py`, `coordination_executor.py`, and `impact_triage.py`
- `src/scripts/lib/services/impact_analyzer.py`
- `src/scripts/lib/risk/loop.py`
- `src/scripts/lib/tools/tool_surface.py`
- `src/scripts/lib/prompts/substrate_prompt_builder.py`
- `src/scripts/scan/codemap.py`, `exploration.py`, `feedback.py`, `lib/scan/*`
  — validated scan prompts
- `src/scripts/task_dispatcher.py` and `src/scripts/qa_interceptor.py`

**Conformance**: Every dispatch must be payload-backed and pass prompt-safety
validation through one of the sanctioned forms.

---

## PAT-0003: Path Registry

**Problem class**: Artifact path proliferation, hardcoded path construction,
path inconsistency across modules, and reader/writer disagreement.

**Philosophy**: Single source of truth for durable artifact locations.

**Template**:
1. Durable artifact paths come from `PathRegistry(planspace)`.
2. New artifact classes get a dedicated accessor before any writer or reader
   uses them.
3. Writers, readers, prompts, and origin refs all use the same accessor.
4. Manual path construction is permitted only for scratch / ephemeral paths or
   when the accessor intentionally returns a parent directory.
5. Every durable-path consumer must declare which root kind it accepts
   (planspace or artifacts_dir) and normalize immediately to one internal
   contract. Mixed root semantics in a single function are a violation.
6. Tests for path-sensitive surfaces must use the runtime directory layout,
   not a simplified hybrid layout that conflates planspace with artifacts.

**Canonical instance**: `PathRegistry` in
`src/scripts/lib/core/path_registry.py`

**Known instances**:
- Research artifact accessors (`research_*`, dossier, addendum, ticket specs,
  verify report)
- Governance artifact accessors (`governance_*`, packet,
  post-implementation assessment, risk-register signal)
- Readiness / proposal-state / trace / signal / input-ref accessors
- Prompt writers and dispatchers across `section_loop/`, `lib/research/`,
  `lib/governance/`, and `task_dispatcher.py`
- `src/scripts/lib/core/model_policy.py`
- `src/scripts/lib/scan/scan_dispatch.py`
- `src/scripts/lib/substrate/substrate_policy.py`
- `src/scripts/lib/pipelines/microstrategy_orchestrator.py`
- `src/scripts/lib/pipelines/implementation_loop.py`
- `src/scripts/lib/services/readiness_resolver.py`

**Conformance**: No durable artifact path may be reconstructed ad hoc. Any new
artifact path MUST be added to `PathRegistry`, and all consumers must use that
accessor.

---

## PAT-0004: Flow System

**Problem class**: Multi-step, multi-agent task orchestration with dependencies,
parallelism, and accumulation.

**Regions**: flow system, task orchestration, research, coordination

**Solution surfaces**: Flow schema, task flow, flow catalog, research plan
executor, coordination planner.

**Philosophy**: Scripts dispatch, agents decide. Structured submission over ad
hoc spawning.

**Template**:
- **Chains** — sequential task lists via `submit_chain()`
- **Fanout** — parallel branches via `submit_fanout()` with `BranchSpec`
- **Gates** — accumulate branch results, fire synthesis via `GateSpec`
- **Named packages** — `_PACKAGE_REGISTRY` in `flow_catalog.py` maps names to
  `TaskSpec` sequences

**Canonical instance**: `execute_research_plan()` in
`src/scripts/lib/research/plan_executor.py`

**Known instances**:
- Research flow (plan → tickets → synthesis → verify)
- Implementation follow-on chain for post-implementation assessment
- Coordination and reconciliation follow-on chains
- Named packages in `src/scripts/flow_catalog.py`

**Conformance**: New multi-step workflows MUST use the flow system. No direct
multi-agent orchestration outside the flow primitives.

---

## PAT-0005: Policy-Driven Models

**Problem class**: Model selection drifting into arbitrary callsites, making
behavior hard to rotate and hard to audit.

**Philosophy**: Agent files define method-of-thinking; model choice is resolved
centrally from policy.

**Template**:
1. Agent/task surfaces bind to an `agent_file` plus a policy key (or task
   type).
2. Central registries own default model fallbacks (`task_router.py`,
   `model_policy.py`, specialized policy modules).
3. Operational callsites request models via policy lookup rather than embedding
   per-call business logic about model choice.
4. Prompt text and agent instructions do not tell runtime code which concrete
   model to use.
5. Long-lived controllers (dispatchers, orchestrators) must refresh policy per
   dispatch cycle or use content-hash invalidation. A startup-only policy
   snapshot that goes stale during a long poll loop is a violation.
6. Local fallback literals must be sourced from the authoritative default
   module (e.g., `DEFAULT_SCAN_MODELS` in `scan_dispatch.py`), not retyped
   ad hoc at the callsite.

**Canonical instance**: `TASK_ROUTES` in `src/scripts/task_router.py` plus
`ModelPolicy` in `src/scripts/lib/core/model_policy.py`

**Known instances**:
- `src/scripts/task_router.py`
- `src/scripts/lib/core/model_policy.py`
- `src/scripts/lib/scan/scan_dispatch.py`
- `src/scripts/lib/substrate/substrate_policy.py`
- `src/scripts/task_dispatcher.py` — long-lived dispatcher poll loop
- `src/scripts/section_loop/main.py` — outer orchestration loop
- Policy lookups across `section_loop/`, `lib/pipelines/`, `lib/intent/`, and
  `lib/research/`

**Conformance**: Model literals are allowed only in central routing /
default-policy surfaces. Business logic and prompt builders must not choose
concrete models ad hoc. Long-lived controllers must not cache policy at
startup and reuse it indefinitely.

---

## PAT-0006: Freshness Computation

**Problem class**: Stale artifacts causing incorrect dispatch, repeated work, or
hidden governance drift.

**Regions**: freshness computation, change detection, hash computation, section
loop

**Solution surfaces**: Freshness service, section-input hasher, research trigger
hashing, codemap freshness.

**Philosophy**: Change detection must be deterministic and content-based.

**Template**:
1. Use `content_hash()` / `file_hash()` for authoritative input
   fingerprinting.
2. Compare current hash against stored hash or trigger hash.
3. Include every authoritative upstream input that can change the decision,
   including governance packet inputs when they affect semantics.

**Canonical instance**: `compute_section_freshness()` in
`src/scripts/lib/services/freshness_service.py`

**Known instances**:
- `src/scripts/lib/services/freshness_service.py`
- `src/scripts/lib/services/section_input_hasher.py`
- Research trigger hashing in `src/scripts/lib/research/orchestrator.py`
- Codemap freshness flow

**Conformance**: Any "should we redo this?" decision MUST use content-based
hashing, not timestamps or file existence.

---

## PAT-0007: Cycle-Aware Status

**Problem class**: Re-triggering workflows when inputs change, while avoiding
re-triggering when inputs have not changed.

**Regions**: research, retriggerable workflows, status tracking

**Solution surfaces**: Research orchestration status, research trigger artifacts.

**Philosophy**: Precision over coarseness. Do not repeat work that is still
valid for the same trigger.

**Template**:
1. Compute `trigger_hash` from the inputs that would cause re-triggering.
2. Store `trigger_hash` plus `cycle_id` in the status artifact.
3. Gate re-entry on both terminal state and hash match.

**Canonical instance**: `is_research_complete_for_trigger()` in
`src/scripts/lib/research/orchestrator.py`

**Known instances**:
- Research orchestration status
- Research-plan readiness routing / trigger artifacts

**Conformance**: Any retriggerable workflow should adopt this pattern rather
than coarse "is it done?" checks.

---

## PAT-0008: Fail-Closed Defaults

**Problem class**: Parse failures, missing data, unexpected states, and
uncertain optimization boundaries in a multi-agent pipeline.

**Philosophy**: Conservative behavior on uncertainty. Fail closed at decision
boundaries; scale process by risk only when the system actually has enough
evidence to do so safely.

**Template**:
- On parse failure: default to the conservative / safe behavior.
- On missing artifact: treat as "not yet done" rather than "already done".
- On unexpected state: fall through to fuller processing rather than
  short-circuiting.
- On uncertain optimization: prefer a documented safe baseline over an
  unverified shortcut.
- On declared eval-scenario or registry import failure: fail the harness,
  do not silently narrow coverage.

**Canonical instance**: readiness and freshness gating across
`src/scripts/lib/pipelines/readiness_gate.py` and
`src/scripts/lib/services/readiness_resolver.py`

**Known instances**:
- Readiness and freshness gating
- `evals/harness.py` — live-eval scenario loading boundary

**Conformance**: Any skip, optimization, or early-exit path must have a
fail-closed default path. A declared verification surface that silently
degrades on import failure is a violation.

---

## PAT-0009: Blocker Taxonomy

**Problem class**: Work that cannot proceed needs structured routing to the
right resolver.

**Regions**: readiness gate, coordination, blocker signals, section loop

**Solution surfaces**: Research plan executor, readiness gate, coordination
problem resolver, flow reconciler, blocker rollup.

**Philosophy**: Structured signals over freeform text. Route, do not stall
silently.

**Template**:
- Blockers carry: `state`, `section`, `detail`, `needs`, `why_blocked`,
  `source`
- Written as structured JSON to `signals/`
- Rollup / aggregation functions surface them for the next controlling layer
- Any local blocker representation persisted to a durable artifact must be
  normalized back to canonical blocker keys (`type`/`description` for
  proposal-state origin, `state`/`detail`/`needs`/`why_blocked`/`source`
  for governance origin) before logging, rollup, or downstream routing.

**Canonical instance**: `_emit_not_researchable_signals()` in
`src/scripts/lib/research/plan_executor.py`

**Known instances**:
- Research `needs_parent` / `need_decision` routing
- Readiness-gate blocker emission and logging
- Coordination problem resolver signals
- Post-implementation `refactor_required` blocker emission in
  `flow_reconciler.py`
- Blocker rollup in `section_loop/section_engine/blockers.py`
- Readiness resolver governance blockers in
  `src/scripts/lib/services/readiness_resolver.py`

**Conformance**: Anything that blocks progress must emit a structured blocker
signal, not just log a warning. Downstream consumers that read blockers must
handle both proposal-state and governance blocker shapes, not silently degrade
governance blockers to `unknown`.

---

## PAT-0010: Intent Surfaces

**Problem class**: Agents need context about section state, problem framing,
research, and accumulated decisions without being forced into ad hoc prompt
assembly.

**Regions**: intent, surfaces, context assembly, section loop

**Solution surfaces**: Intent surfaces loader, intent bootstrap, research-derived
surfaces, prompt context assembly.

**Philosophy**: Agents decide based on rich context, not raw artifact sprawl.

**Template**:
1. Load and merge surfaces from multiple sources (problem frame, research,
   prior decisions, feedback surfaces).
2. Package the merged view as a durable surface accessible by file path.
3. Reference that surface from downstream prompts rather than hand-splicing ad
   hoc context blocks everywhere.

**Canonical instance**: `load_combined_intent_surfaces()` in
`src/scripts/section_loop/intent/surfaces.py`

**Known instances**:
- `src/scripts/section_loop/intent/surfaces.py`
- Intent bootstrap and expansion flow
- Research-derived surfaces and implementation-feedback surfaces
- Prompt context assembly in `section_loop/prompts/context.py`

**Conformance**: New context sources should merge into the intent-surface system
unless they are truly cross-cutting and deserve their own pattern.

---

## PAT-0011: Applicable Governance Packet Threading

**Problem class**: Governance archives exist, but runtime work needs
section-scoped governance context that is both relevant enough to preserve
decision quality and structured enough to guide proposal, implementation,
alignment, risk assessment, and assessment.

**Philosophy**: Problems, philosophy, and patterns must shape execution at
runtime, not only at audit time. Sections are concerns, not file bundles.
Context should be minimal but sufficient.

**Regions**: governance, packets, readiness, freshness, section-input hashing

**Solution surfaces**: Governance loader, governance packet builder, prompt
context assembly, freshness service, section-input hasher.

**Template**:
1. Parse governance archives into structured indexes rich enough for runtime
   use: problem records, pattern records (including regions, solution surfaces,
   template/conformance/change policy summaries), philosophy profiles, and
   region/profile mappings. Every pattern record must carry `regions` and
   `solution_surfaces` (or equivalent explicit applicability cues); missing
   metadata in a scoped-packet regime counts as ambiguity, not universal
   applicability.
2. Build a section packet during bootstrap from **multiple applicability
   signals**, not section-number string matching alone. Inputs may include:
   - region labels from the archives
   - section concern summaries / problem-frame language
   - codespace or synthesis cues that indicate which runtime region the section
     is operating in
   - already matched governance IDs when they exist
3. The packet must contain the section's **applicable or candidate** governance
   set plus the basis for that judgment:
   - candidate/matched problem IDs
   - governing profile(s)
   - applicable pattern IDs and summaries
   - known exceptions / allowed deviations
   - unresolved governance questions
   - applicability basis / ambiguity notes
   - references back to the authoritative archive
4. Do **not** mirror the full governance archive into every section packet
   unless the catalog is explicitly updated to allow that behavior. Broad
   fallback, when unavoidable, must be explicit and justified in the packet
   with an `applicability_basis` of `broad_fallback` and a reason.
5. Thread the packet into proposal, microstrategy, implementation, alignment,
   ROAL, post-implementation assessment, sidecars, freshness hashing, and
   section-input hashing.
6. When applicability is ambiguous, fail closed by surfacing governance
   questions or bounded candidate sets rather than silently broadening to the
   whole archive or silently omitting governance. The packet must distinguish
   three explicit states: `matched` (signals resolved), `ambiguous_applicability`
   (signals inconclusive — emit `governance_questions`), and
   `no_applicable_governance` (explicit no-match after signal evaluation).
7. Narrow profile scope: the packet should carry the section's governing
   profile (or bounded candidate set) rather than mirroring all profiles from
   the archive.

**Canonical instance**: `build_governance_indexes()` +
`build_section_governance_packet()` in `src/scripts/lib/governance/loader.py`
and `src/scripts/lib/governance/packet.py`

**Known instances**:
- `src/scripts/section_loop/main.py` — builds governance indexes and packets
- `src/scripts/lib/governance/loader.py`
- `src/scripts/lib/governance/packet.py`
- `src/scripts/lib/intent/intent_bootstrap.py`
- `src/scripts/section_loop/prompts/context.py` and
  `src/scripts/lib/prompts/prompt_context_assembler.py`
- `src/scripts/section_loop/prompts/templates/integration-proposal.md`
- `src/scripts/lib/pipelines/microstrategy_orchestrator.py`
- `src/scripts/section_loop/prompts/templates/strategic-implementation.md`
- `src/scripts/section_loop/prompts/templates/integration-alignment.md`
- `src/scripts/section_loop/prompts/templates/implementation-alignment.md`
- `src/scripts/lib/risk/loop.py`
- `src/scripts/lib/governance/assessment.py`
- `src/scripts/lib/services/freshness_service.py`
- `src/scripts/lib/services/section_input_hasher.py`
- `src/scripts/lib/services/readiness_resolver.py`
- `src/scripts/lib/dispatch/context_sidecar.py`

**Conformance**: Any runtime stage that materially depends on governance context
must consume the packet (or an accessor derived from it) rather than reparsing
governance markdown ad hoc. A section packet that is only section-labeled but
not section-scoped is a pattern violation. Pattern records truncated to shallow
single-line summaries such that conformance/change-policy data is unavailable at
runtime are also a pattern violation.

---

## PAT-0012: Post-Implementation Governance Feedback

**Problem class**: Landed changes introduce governance-visible risk that must be
assessed, traced, and routed into stabilization without inventing a parallel
control loop.

**Regions**: governance, assessment, trace, flow reconciler, stabilization

**Solution surfaces**: Post-implementation assessment, flow reconciler, debt
signal staging, risk register, traceability enrichment.

**Philosophy**: Governance continues after code lands. Assessment, risk capture,
and stabilization must align with the same problem / pattern / philosophy
hierarchy that shaped implementation.

**Template**:
1. After successful implementation, queue a post-implementation assessment with
   references to governance packet, trace artifacts, proposal, and problem
   frame.
2. Validate the assessment result with PAT-0001.
3. Merge or validate governance identity across **all authoritative trace
   surfaces**:
   - `trace/section-N.json`
   - `trace-map/section-N.json`
   - `traceability.json`
4. Route outcomes mechanically:
   - `accept` → confirm governance lineage
   - `accept_with_debt` → emit debt / risk-register promotion signal
   - `refactor_required` → emit structured blocker signal
5. A bounded stabilization consumer promotes accepted debt into the
   authoritative risk register (or equivalent governed artifact) and MUST:
   - deduplicate entries with a stable debt key computed from a **normalized
     material-payload hash** that includes all fields whose change should
     trigger re-promotion (identity fields like section/category/region/
     description, plus materiality fields like severity, mitigation,
     acceptance_rationale, and governance lineage)
   - record promotion state or receipts per source signal
   - only re-promote when the material-payload hash changes (unchanged
     signals remain idempotent)
   - preserve the signal/promotion trail needed for auditability
6. Post-implementation assessment may enrich, challenge, or append to
   proposal-time governance identity; it must not be the first place governance
   lineage appears.

**Canonical instance**: `write_post_impl_assessment_prompt()` /
`read_post_impl_assessment()` in `src/scripts/lib/governance/assessment.py`
with completion handling in `src/scripts/lib/flow/flow_reconciler.py`

**Known instances**:
- `src/scripts/lib/pipelines/implementation_loop.py` — queues assessment and
  writes trace artifacts
- `src/scripts/lib/governance/assessment.py`
- `src/scripts/section_loop/section_engine/traceability.py`
- `src/scripts/lib/core/communication.py` — append-log traceability surface
- `src/scripts/lib/flow/flow_reconciler.py` — assessment completion routing
- `src/scripts/section_loop/main.py` — stabilization consumer invocation
- `governance/risk-register.md` — authoritative debt/risk target

**Conformance**: Post-implementation assessment is not complete until debt /
refactor outcomes enter a governed stabilization surface and all trace surfaces
carry governance lineage. Orphaned debt signals, duplicate re-promotion of
unchanged debt, and assessment-originated lineage are pattern violations.

---

## PAT-0013: Governed Proposal Identity

**Problem class**: Runtime proposal artifacts need an explicit governance
identity so the system can enforce "pattern change before code change" and
preserve problem→proposal→implementation→assessment lineage.

**Regions**: proposals, readiness gate, alignment, governance

**Solution surfaces**: Proposal-state repository, readiness resolver, readiness
gate, alignment judge, integration proposer, traceability.

**Philosophy**: Proposals are problem-state artifacts, not file-change plans.
Patterns operationalize philosophy. If code needs a pattern change, that pattern
delta must be recognized before descent. Scripts validate structure; agents
decide the actual governance claims.

**Template**:
1. Proposal-time machine artifacts declare governance identity fields:
   - `problem_ids`
   - `pattern_ids`
   - `profile_id`
   - `pattern_deviations` and/or `governance_questions` when needed
2. These IDs must reference records present in the current governance packet,
   unless the packet explicitly records a bounded no-applicable-governance
   state.
3. If the work requires deviating from an established pattern, emit the pattern
   delta first and block structural descent until it is resolved or explicitly
   accepted.
4. Alignment and readiness gates validate the **presence, coherence, and
   packet-membership** of governance identity; they do not replace agent
   judgment about which IDs apply.
5. Unresolved `governance_questions` or unresolved `pattern_deviations` must
   block descent or route upward explicitly; they are not informational-only
   fields.
6. When the governance packet provides candidate problems, patterns, or a
   governing profile, empty governance identity (`problem_ids`, `pattern_ids`,
   `profile_id` all empty) is illegal unless the packet explicitly records a
   `no_applicable_governance` state. Declared `profile_id` must be compatible
   with the packet's `governing_profile` or bounded candidate set. Declared
   governance IDs with a missing or malformed packet must fail closed.
7. Downstream traceability and post-implementation assessment inherit and verify
   this identity rather than inventing it from empty state.

**Canonical instance**: `proposal-state.json` plus the integration-proposer
contract in `src/agents/integration-proposer.md`

**Known instances**:
- `src/scripts/lib/repositories/proposal_state_repository.py`
- `src/agents/integration-proposer.md`
- `src/scripts/section_loop/prompts/templates/integration-proposal.md`
- `src/agents/alignment-judge.md`
- `src/scripts/lib/pipelines/readiness_gate.py`
- `src/scripts/lib/services/readiness_resolver.py`
- `src/scripts/lib/pipelines/implementation_loop.py`
- `src/scripts/section_loop/section_engine/traceability.py`

**Conformance**: Structural work cannot descend with empty governance identity
unless the governance packet explicitly records that no governing problem or
pattern applies and alignment accepts that state. Pattern deviation without a
preceding pattern delta is a violation. Runtime gates that treat non-empty
`pattern_deviations` or unresolved `governance_questions` as advisory-only are
also a violation.

---

## Health Notes

- **PAT-0001 (Corruption Preservation)**: Healthy. Instance list expanded to
  reflect current authoritative readers.
- **PAT-0002 (Prompt Safety)**: Healthy. Instance list expanded to reflect
  current authoritative prompt-safety sites.
- **PAT-0003 (Path Registry)**: Unhealthy. Durable-path islands remain in
  model_policy.py, scan_dispatch.py, substrate_policy.py,
  microstrategy_orchestrator.py, and implementation_loop.py. Readiness resolver
  mixed planspace/artifacts root semantics fixed in R106. Template updated to
  require declared root semantics and runtime-shape tests.
- **PAT-0004 (Flow System)**: Healthy.
- **PAT-0005 (Policy-Driven Models)**: Healthy. Scan fallback and per-dispatch
  refresh fixed in R105.
- **PAT-0006 (Freshness Computation)**: Healthy in mechanism, but governance
  packet overscoping currently causes avoidable invalidation pressure.
- **PAT-0007 (Cycle-Aware Status)**: Healthy and intentionally narrow.
- **PAT-0008 (Fail-Closed Defaults)**: Unhealthy. Eval harness fails open on
  scenario import failure. Template updated (R106) to include declared eval
  coverage.
- **PAT-0009 (Blocker Taxonomy)**: Unhealthy. Governance blockers degrade to
  `unknown` type in blocker rollup because blockers.py only reads
  `type`/`description` keys while governance emits `state`/`detail`. Template
  updated (R106) to require blocker normalization at readiness-artifact
  boundaries.
- **PAT-0010 (Intent Surfaces)**: Healthy.
- **PAT-0011 (Applicable Governance Packet Threading)**: Unhealthy. Pattern
  applicability metadata was not parsed by the loader — all patterns were
  universal regardless of section context. R106 adds regions/solution_surfaces
  parsing and treats missing metadata as ambiguity. Template updated to require
  explicit pattern applicability metadata.
- **PAT-0012 (Post-Implementation Governance Feedback)**: Healthy. Debt
  promotion is idempotent with material-payload-aware dedup (R105).
- **PAT-0013 (Governed Proposal Identity)**: Unhealthy. Governance identity
  validation exists but R104/R105 implementation mixed path-root semantics,
  producing false `governance_packet_missing` blockers under the real runtime
  layout. Fixed in R106. Runtime gate logic is now correct.
