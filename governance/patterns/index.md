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
- `src/scripts/lib/repositories/proposal_state_repository.py` — proposal-state
  loader
- `src/scripts/lib/risk/serialization.py` — ROAL package / assessment / plan
  loaders
- `src/scripts/lib/services/signal_reader.py` and
  `src/scripts/lib/dispatch/dispatch_metadata.py`

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
- `src/scripts/section_loop/prompts/writers.py`
- `src/scripts/section_loop/alignment.py` — template-wrapped adjudication prompt
- `src/scripts/lib/pipelines/reconciliation_adjudicator.py` —
  template-wrapped adjudication prompt
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

**Conformance**: No durable artifact path may be reconstructed ad hoc. Any new
artifact path MUST be added to `PathRegistry`, and all consumers must use that
accessor.

---

## PAT-0004: Flow System

**Problem class**: Multi-step, multi-agent task orchestration with dependencies,
parallelism, and accumulation.

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

**Canonical instance**: `TASK_ROUTES` in `src/scripts/task_router.py` plus
`ModelPolicy` in `src/scripts/lib/core/model_policy.py`

**Known instances**:
- `src/scripts/task_router.py`
- `src/scripts/lib/core/model_policy.py`
- `src/scripts/lib/scan/scan_dispatch.py`
- `src/scripts/lib/substrate/substrate_policy.py`
- Policy lookups across `section_loop/`, `lib/pipelines/`, `lib/intent/`, and
  `lib/research/`

**Conformance**: Model literals are allowed only in central routing /
default-policy surfaces. Business logic and prompt builders must not choose
concrete models ad hoc.

---

## PAT-0006: Freshness Computation

**Problem class**: Stale artifacts causing incorrect dispatch, repeated work, or
hidden governance drift.

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

**Canonical instance**: readiness and freshness gating across
`src/scripts/lib/pipelines/readiness_gate.py` and
`src/scripts/lib/services/readiness_resolver.py`

**Conformance**: Any skip, optimization, or early-exit path must have a
fail-closed default path.

---

## PAT-0009: Blocker Taxonomy

**Problem class**: Work that cannot proceed needs structured routing to the
right resolver.

**Philosophy**: Structured signals over freeform text. Route, do not stall
silently.

**Template**:
- Blockers carry: `state`, `section`, `detail`, `needs`, `why_blocked`,
  `source`
- Written as structured JSON to `signals/`
- Rollup / aggregation functions surface them for the next controlling layer

**Canonical instance**: `_emit_not_researchable_signals()` in
`src/scripts/lib/research/plan_executor.py`

**Known instances**:
- Research `needs_parent` / `need_decision` routing
- Readiness-gate blocker emission
- Coordination problem resolver signals
- Post-implementation `refactor_required` blocker emission in
  `flow_reconciler.py`
- Blocker rollup in `section_loop/section_engine/blockers.py`

**Conformance**: Anything that blocks progress must emit a structured blocker
signal, not just log a warning.

---

## PAT-0010: Intent Surfaces

**Problem class**: Agents need context about section state, problem framing,
research, and accumulated decisions without being forced into ad hoc prompt
assembly.

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

**Template**:
1. Parse governance archives into structured indexes rich enough for runtime
   use: problem records, pattern records (including conformance metadata),
   philosophy profiles, and region/profile mappings.
2. Build a section packet during bootstrap that contains the section's
   **applicable or candidate** governance set:
   - candidate/matched problem IDs
   - governing profile(s)
   - applicable pattern IDs and summaries
   - known exceptions / allowed deviations
   - unresolved governance questions
   - references back to the authoritative archive
3. Do **not** mirror the full governance archive into every section packet
   unless the catalog is explicitly updated to allow that behavior.
4. Thread the packet into proposal, microstrategy, implementation, alignment,
   ROAL, post-implementation assessment, sidecars, freshness hashing, and
   section-input hashing.
5. When applicability is ambiguous, fail closed by surfacing governance
   questions or candidate sets rather than silently broadening to the whole
   archive or silently omitting governance.

**Canonical instance**: `build_governance_indexes()` +
`build_section_governance_packet()` in `src/scripts/lib/governance/loader.py`
and `src/scripts/lib/governance/packet.py`

**Known instances**:
- `src/scripts/section_loop/main.py` — builds governance indexes
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
- `src/scripts/lib/dispatch/context_sidecar.py`

**Conformance**: Any runtime stage that materially depends on governance context
must consume the packet (or an accessor derived from it) rather than reparsing
governance markdown ad hoc. A section packet that is only section-labeled but
not section-scoped is a pattern violation.

---

## PAT-0012: Post-Implementation Governance Feedback

**Problem class**: Landed changes introduce governance-visible risk that must be
assessed, traced, and routed into stabilization without inventing a parallel
control loop.

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
   authoritative risk register (or equivalent governed artifact), deduplicates
   entries, and records promotion state so debt signals do not orphan or
   re-promote forever.
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
- `governance/risk-register.md` — authoritative debt/risk target

**Conformance**: Post-implementation assessment is not complete until debt /
refactor outcomes enter a governed stabilization surface and all trace surfaces
carry governance lineage. Orphaned debt signals and assessment-originated
lineage are pattern violations.

---

## PAT-0013: Governed Proposal Identity

**Problem class**: Runtime proposal artifacts need an explicit governance
identity so the system can enforce "pattern change before code change" and
preserve problem→proposal→implementation→assessment lineage.

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
2. These IDs must reference records present in the current governance packet.
3. If the work requires deviating from an established pattern, emit the pattern
   delta first and block structural descent until it is resolved or explicitly
   accepted.
4. Alignment and readiness gates validate the **presence, coherence, and
   packet-membership** of governance identity; they do not replace agent
   judgment about which IDs apply.
5. Downstream traceability and post-implementation assessment inherit and verify
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
preceding pattern delta is a violation.

---

## Health Notes

- **PAT-0001 (Corruption Preservation)**: Healthy.
- **PAT-0002 (Prompt Safety)**: Healthy.
- **PAT-0003 (Path Registry)**: Healthy.
- **PAT-0004 (Flow System)**: Healthy.
- **PAT-0005 (Policy-Driven Models)**: Healthy.
- **PAT-0006 (Freshness Computation)**: Healthy in mechanism, but governance
  packet overscoping currently causes avoidable invalidation pressure.
- **PAT-0007 (Cycle-Aware Status)**: Healthy and intentionally narrow.
- **PAT-0008 (Fail-Closed Defaults)**: Healthy.
- **PAT-0009 (Blocker Taxonomy)**: Healthy.
- **PAT-0010 (Intent Surfaces)**: Healthy.
- **PAT-0011 (Applicable Governance Packet Threading)**: Partial. Packet
  transport exists; packet is section-labeled but not yet fully section-scoped.
  Alignment/microstrategy/ROAL callsites now wired (R103).
- **PAT-0012 (Post-Implementation Governance Feedback)**: Partial.
  Assessment dispatch, signal emission, and bounded debt promotion exist.
  Trace lineage initialized from proposal-time governance identity (R103).
- **PAT-0013 (Governed Proposal Identity)**: New in R103. Proposal-state
  schema, proposer contract, alignment judge, and readiness gate updated.
