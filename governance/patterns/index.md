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

**Regions**: all artifact readers, JSON parsing, prompt output consumption

**Solution surfaces**: Corruption preservation, fail-closed defaults, structured
validation, malformed-file renaming.

**Philosophy**: Fail-closed. Evidence preservation over silent discard. Preserve
debugging evidence instead of hiding it.

**Template**:
1. Use `read_json(path)` or a typed loader built on it for syntax-level parsing.
   Typed loaders must delegate malformed-artifact handling to the shared
   primitive rather than re-implementing local rename/copy conventions.
2. If parse succeeds, validate schema shape and semantic invariants.
3. On malformed JSON or schema mismatch, call `rename_malformed(path)` so the
   corrupt canonical artifact stops being authoritative.
4. Return `None` or a documented fail-closed default.
5. Do not restore malformed content back into the live path as an advisory
   convenience, and do not preserve corruption by shadow-copying while leaving
   the corrupt canonical file in place.
6. Do not invent local corruption-preservation conventions unless the pattern is
   explicitly updated.

**Canonical instance**: `load_surface_registry()` in
`src/intent/service/surface_registry.py`

**Known instances**:
- `src/signals/repository/artifact_io.py` — `read_json()` / `rename_malformed()`
  primitives
- `src/intent/service/surface_registry.py` — surface registry + research
  surface validation
- `src/research/engine/orchestrator.py` — research plan / status loaders
- `src/intake/service/assessment_evaluator.py` — post-implementation assessment
  reader
- `src/proposal/repository/state.py`,
  `decisions.py`, `queue.py`,
  `results.py`, and `strategic_state_builder.py`
- `src/dispatch/service/tool_registry_manager.py` — tool registry and friction signal
  readers
- `src/coordination/prompt/writers.py` — tool-registry advisory surface
- `src/signals/service/blocker_manager.py` and
  `src/coordination/service/problem_resolver.py`
- `src/risk/repository/serialization.py` — ROAL package / assessment / plan
  loaders
- `src/signals/repository/signal_reader.py`,
  `src/dispatch/repository/metadata.py`, and
  `src/scan/substrate/policy.py`

**Conformance**: Any new structured artifact reader MUST follow this pattern. No
silent `json.loads()` with bare except; no alternate corruption filename
conventions without catalog approval.

---

## PAT-0002: Prompt Safety

**Problem class**: Prompt injection, malformed prompt content, untrusted dynamic
values in prompt text, and payloadless dispatch drift.

**Regions**: prompt builders, dispatch surfaces, template rendering, agent
dispatch

**Solution surfaces**: write_validated_prompt, validate_dynamic_content,
payload-backed dispatch, prompt file persistence.

**Philosophy**: Every prompt is a trust boundary. Safety is enforced
mechanically before dispatch.

**Template**:
1. Identify the dynamic / untrusted portion of the prompt surface.
   Payload-file **contents** are untrusted dynamic content even when the
   file path arrived through an internal task — the trust boundary is the
   content, not the delivery mechanism.
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
`src/research/prompt/writers.py`

**Known instances**:
- `src/research/prompt/writers.py` and
  `src/research/engine/research_plan_executor.py`
- `src/intake/service/assessment_evaluator.py`
- `src/intent/service/intent_triager.py`, `expanders.py`, and
  `philosophy_bootstrapper.py`
- `src/dispatch/prompt/writers.py` and
  `src/staleness/service/section_alignment_checker.py`
- `src/reconciliation/service/adjudicator.py`,
  `microstrategy_generator.py`, `scope_delta_aggregator.py`,
  `planner.py`, `plan_executor.py`, and `triage_orchestrator.py`
- `src/implementation/service/impact_analyzer.py`
- `src/risk/engine/risk_assessor.py`
- `src/dispatch/service/tool_registry_manager.py`
- `src/scan/substrate/prompt_builder.py`
- `src/scan/codemap/codemap_builder.py`, `section_explorer.py`,
  `feedback_collector.py` — validated scan prompts
- `src/flow/engine/task_dispatcher.py` and `src/qa/service/qa_interceptor.py`

**Conformance**: Every dispatch must be payload-backed and pass prompt-safety
validation through one of the sanctioned forms.

---

## PAT-0003: Path Registry

**Problem class**: Artifact path proliferation, hardcoded path construction,
path inconsistency across modules, and reader/writer disagreement.

**Regions**: artifact paths, path construction, planspace layout, readers and
writers

**Solution surfaces**: PathRegistry, planspace-rooted accessors, family-complete
consumer migration, runtime-shape tests.

**Philosophy**: Single source of truth for durable artifact locations.

**Template**:
1. Durable artifact paths come from `PathRegistry(planspace)`.
2. New artifact classes — including repeated section-scoped durable
   filename families — get a dedicated accessor before any writer or
   reader uses them.
3. Writers, readers, prompts, origin refs, and freshness/hash inputs all use
   the same accessor for a durable family.
4. Manual path construction is permitted only for scratch / ephemeral paths or
   when the accessor intentionally returns a parent directory.
5. Every durable-path consumer must declare which root kind it accepts
   (planspace or artifacts_dir) and normalize immediately to one internal
   contract. Mixed root semantics in a single function are a violation.
6. Tests for path-sensitive surfaces must use the runtime directory layout,
   not a simplified hybrid layout that conflates planspace with artifacts.
7. Semantically related but distinct durable signal families must have
   explicit, separately named accessor methods. Two families using the same
   conceptual label but different directory layouts are a migration-ambiguity
   risk and must be registry-distinguished.
8. Adding an accessor does **not** complete the migration by itself. The
   pattern is healthy only when all authoritative consumers for that durable
   family have moved to the accessor or the remaining exceptions are explicitly
   documented and justified.
9. If a durable family is consumed by more than one authoritative reader or
   writer and no accessor exists yet, the accessor must be added before the
   family spreads further.

**Canonical instance**: `PathRegistry` in
`src/orchestrator/path_registry.py`

**Known instances**:
- Research artifact accessors (`research_*`, dossier, addendum, ticket specs,
  verify report)
- Governance artifact accessors (`governance_*`, packet,
  post-implementation assessment, risk-register signal)
- Readiness / proposal-state / trace / signal / input-ref accessors
- `src/dispatch/service/model_policy.py`
- `src/scan/service/scan_dispatch_config.py`
- `src/scan/substrate/policy.py`,
  `src/scan/substrate/related_files.py`, and
  `src/scan/substrate/schemas.py`
- `src/implementation/service/microstrategy_generator.py`
- `src/implementation/engine/implementation_cycle.py`
- `src/proposal/service/readiness_resolver.py`
- `src/dispatch/service/context_sidecar.py` — context sidecar
  materialization via `PathRegistry.context_sidecar()` accessor (R109)
- `src/dispatch/prompt/context_builder.py`,
  `src/implementation/service/section_reexplorer.py`,
  `src/implementation/service/traceability_writer.py`, and
  `src/implementation/engine/section_pipeline.py`
- `src/proposal/engine/readiness_gate.py`,
  `src/orchestrator/engine/strategic_state_builder.py`, and
  `src/scan/codemap/codemap_builder.py`
- Scan-stage durable-path consumers in
  `src/scan/related/related_file_resolver.py`,
  `src/scan/codemap/codemap_builder.py`,
  `src/scan/explore/section_explorer.py`,
  `src/scan/explore/deep_scanner.py`, and
  `src/scan/service/feedback_collector.py`
- Intent/prompt durable-path consumers in
  `src/intent/service/intent_triager.py`,
  `src/dispatch/prompt/context_assembler.py`,
  `src/intent/engine/intent_initializer.py`, and
  `src/dispatch/prompt/writers.py`
- Coordination / proposal / problem-frame prompt surfaces in
  `src/coordination/service/planner.py`,
  `src/coordination/engine/plan_executor.py`,
  `src/proposal/service/problem_frame_gate.py`, and
  `src/implementation/engine/implementation_cycle.py`
- Tool-surface blocker writers in `src/dispatch/service/tool_registry_manager.py`
- Freshness/hash consumers in `src/staleness/service/freshness_calculator.py`
  and `src/staleness/service/input_hasher.py`
- `src/scan/substrate/related_files.py` and
  `src/scan/substrate/prompt_builder.py` — substrate-stage
  related-files update signals via `related_files_update_dir()`

**Conformance**: No durable artifact path may be reconstructed ad hoc. Any new
artifact path MUST be added to `PathRegistry`, and all authoritative consumers
must use that accessor. A pattern round is not complete if it adds the accessor
but leaves the primary readers/writers on hand-built paths.

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
- **Named packages** — `_PACKAGE_REGISTRY` in ``src/flow/repository/catalog.py`` maps names to
  `TaskSpec` sequences

**Canonical instance**: `execute_research_plan()` in
`src/research/engine/research_plan_executor.py`

**Known instances**:
- Research flow (plan → tickets → synthesis → verify)
- Implementation follow-on chain for post-implementation assessment
- Coordination and reconciliation follow-on chains
- Named packages in `src/flow/repository/catalog.py`

**Conformance**: New multi-step workflows MUST use the flow system. No direct
multi-agent orchestration outside the flow primitives.

---

## PAT-0005: Policy-Driven Models

**Problem class**: Model selection drifting into arbitrary callsites, making
behavior hard to rotate and hard to audit.

**Regions**: model selection, dispatch, task routing, policy loading

**Solution surfaces**: ModelPolicy dataclass, task_router, scan_dispatch,
substrate_policy, resolve().

**Philosophy**: Agent files define method-of-thinking; model choice is resolved
centrally from policy.

**Template**:
1. Agent/task surfaces bind to an `agent_file` plus a policy key (or task
   type).
2. Central registries own default model fallbacks (``src/flow/types/routing.py``,
   `model_policy.py`, specialized policy modules).
3. Operational callsites request models via policy lookup rather than embedding
   per-call business logic about model choice.
4. Prompt text and agent instructions do not tell runtime code which concrete
   model to use.
5. Long-lived controllers (dispatchers, orchestrators) must refresh policy per
   dispatch cycle or use content-hash invalidation. A startup-only policy
   snapshot that goes stale during a long poll loop is a violation.
6. Local fallback literals must be sourced from the authoritative default
   module (e.g., `DEFAULT_SCAN_MODELS` in `scan_dispatch_config.py`), not retyped
   ad hoc at the callsite. Operational callsites must not use
   `policy.get("key", "literal")` with a retyped literal that duplicates
   the authoritative default — use `policy["key"]` or `resolve(policy, key)`
   instead. The authoritative default lives in `ModelPolicy` (for main
   policy) or `DEFAULT_SCAN_MODELS` (for scan policy), not at the callsite.
7. Helper functions must not carry concrete model defaults in their
   signatures (e.g., `model: str = "glm"`). Callers must pass
   policy-resolved values; helpers must require the parameter. This applies
   to helper functions, eval/dev harnesses that dispatch real agents, and
   any function that ultimately feeds a model name into `dispatch_agent()`.

**Canonical instance**: `src/taskrouter/` registry plus
`ModelPolicy` in `src/dispatch/service/model_policy.py`

**Known instances**:
- `src/taskrouter/` — centralized task routing with per-route policy keys
- `src/dispatch/service/model_policy.py`
- `src/scan/service/scan_dispatch_config.py`
- `src/scan/substrate/policy.py`,
  `src/scan/substrate/related_files.py`, and
  `src/scan/substrate/schemas.py`
- `src/flow/engine/task_dispatcher.py` — long-lived dispatcher poll loop
- `src/orchestrator/engine/pipeline_orchestrator.py` — outer orchestration loop
- Policy lookups across `src/intent/`, `src/research/`, `src/coordination/`
- `evals/harness.py` — live scenario eval model lookup

**Conformance**: Model literals are allowed only in central routing /
default-policy surfaces. Business logic, prompt builders, helper functions,
and eval/dev harnesses must not choose concrete models ad hoc. Long-lived
controllers must not cache policy at startup and reuse it indefinitely.
Helper function signatures must not carry concrete model defaults.

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
`src/staleness/service/freshness_calculator.py`

**Known instances**:
- `src/staleness/service/freshness_calculator.py`
- `src/staleness/service/input_hasher.py`
- Research trigger hashing in `src/research/engine/orchestrator.py`
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
`src/research/engine/orchestrator.py`

**Known instances**:
- Research orchestration status
- Research-plan readiness routing / trigger artifacts

**Conformance**: Any retriggerable workflow should adopt this pattern rather
than coarse "is it done?" checks.

---

## PAT-0008: Fail-Closed Defaults (Authoritative Surfaces)

**Problem class**: Parse failures, missing data, unexpected states, and
uncertain optimization boundaries at **authoritative** control, verification,
and parsing surfaces in a multi-agent pipeline.

**Regions**: readiness gate, freshness computation, artifact parsing, eval
harness, optimization boundaries, governance index loading

**Solution surfaces**: Fail-closed defaults, conservative baselines, eval
harness exit codes, readiness gating, governance index status.

**Philosophy**: Conservative behavior on uncertainty at authoritative
boundaries. Fail closed at decision boundaries; scale process by risk only
when the system actually has enough evidence to do so safely.

**Scope**: This pattern governs **authoritative** surfaces — those whose
failure changes the correctness of downstream decisions. Advisory surfaces
that are deliberately fail-open (e.g., QA interception, reconciliation
adjudication) are governed by PAT-0014, not this pattern. The distinction
matters: authoritative fail-closed prevents silent corruption; advisory
fail-open preserves liveness. Treating advisory surfaces as PAT-0008
violations erases the intentional design boundary.

**Template**:
- On parse failure: default to the conservative / safe behavior.
- On missing artifact: treat as "not yet done" rather than "already done".
- On unexpected state: fall through to fuller processing rather than
  short-circuiting.
- On uncertain optimization: prefer a documented safe baseline over an
  unverified shortcut.
- On declared eval-scenario or registry import failure: fail the harness,
  do not silently narrow coverage.
- On authoritative governance index parse failure: record the failure in a
  structured status artifact and surface it to downstream consumers (packet
  builder, readiness resolver) rather than writing empty indexes and
  returning success.

**Canonical instance**: readiness and freshness gating across
`src/proposal/engine/readiness_gate.py` and
`src/proposal/service/readiness_resolver.py`

**Known instances**:
- Readiness and freshness gating
- `evals/harness.py` — live-eval scenario loading boundary
- `src/intake/repository/governance_loader.py` — governance index build with
  structured parse-failure status

**Conformance**: Any skip, optimization, or early-exit path at an
authoritative surface must have a fail-closed default path. A declared
verification surface that silently degrades on import failure is a violation.
Authoritative governance parsing that swallows errors and writes empty
indexes without surfacing the failure is a violation.

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
`src/research/engine/research_plan_executor.py`

**Known instances**:
- Research `needs_parent` / `need_decision` routing
- Readiness-gate blocker emission and logging
- Coordination problem resolver signals
- Post-implementation `refactor_required` blocker emission in
  ``src/flow/engine/reconciler.py``
- Blocker rollup in `src/signals/service/blocker_manager.py`
- Readiness resolver governance blockers in
  `src/proposal/service/readiness_resolver.py`

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
`src/intent/service/surface_registry.py`

**Known instances**:
- `src/intent/service/surface_registry.py`
- Intent bootstrap and expansion flow
- Research-derived surfaces and implementation-feedback surfaces
- Prompt context assembly in `src/dispatch/prompt/context_assembler.py`

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
     is operating in — synthesis cues **must be consumed** when available
     (e.g., `synthesis-cues.json` parsed from `system-synthesis.md` Regions
     block)
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
4. Do **not** mirror the full governance archive into any section packet.
   When no records match by region or keyword, the packet must contain empty
   candidate lists plus explicit governance questions and archive references
   — not the full record set. The distinction between "nothing matched" and
   "governance doesn't apply" must be preserved: archives exist but nothing
   matched is `ambiguous_applicability`, not `no_applicable_governance`.
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
`build_section_governance_packet()` in `src/intake/repository/governance_loader.py`
and `src/intake/service/governance_packet_builder.py`

**Known instances**:
- `src/orchestrator/engine/pipeline_orchestrator.py` — builds governance indexes and packets
- `src/intake/repository/governance_loader.py`
- `src/intake/service/governance_packet_builder.py`
- `src/intent/engine/intent_initializer.py`
- `src/dispatch/prompt/context_builder.py` and
  `src/dispatch/prompt/context_assembler.py`
- `src/templates/dispatch/integration-proposal.md`
- `src/implementation/service/microstrategy_generator.py`
- `src/templates/dispatch/strategic-implementation.md`
- `src/templates/dispatch/integration-alignment.md`
- `src/templates/dispatch/implementation-alignment.md`
- `src/risk/engine/risk_assessor.py`
- `src/intake/service/assessment_evaluator.py`
- `src/staleness/service/freshness_calculator.py`
- `src/staleness/service/input_hasher.py`
- `src/proposal/service/readiness_resolver.py`
- `src/dispatch/service/context_sidecar.py`
- `src/dispatch/prompt/writers.py`

**Conformance**: Every pattern record in the catalog must carry `Regions` and
`Solution surfaces` (or equivalent explicit applicability cues). Missing
applicability metadata must be treated as ambiguity or catalog defect by packet
builders, fixtures, and tests — never as universal applicability. Any runtime
stage that materially depends on governance context must consume the packet (or
an accessor derived from it) rather than reparsing
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
`read_post_impl_assessment()` in `src/intake/service/assessment_evaluator.py`
with completion handling in `src/flow/engine/reconciler.py`

**Known instances**:
- `src/implementation/engine/implementation_cycle.py` — queues assessment and
  writes trace artifacts
- `src/intake/service/assessment_evaluator.py`
- `src/implementation/service/traceability_writer.py`
- `src/signals/service/section_communicator.py` — append-log traceability surface
- `src/flow/engine/reconciler.py` — assessment completion routing
- `src/orchestrator/engine/pipeline_orchestrator.py` — stabilization consumer invocation
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
contract in `src/proposal/agents/integration-proposer.md`

**Known instances**:
- `src/proposal/repository/state.py`
- `src/proposal/agents/integration-proposer.md`
- `src/templates/dispatch/integration-proposal.md`
- `src/staleness/agents/alignment-judge.md`
- `src/proposal/engine/readiness_gate.py`
- `src/proposal/service/readiness_resolver.py`
- `src/implementation/engine/implementation_cycle.py`
- `src/implementation/service/traceability_writer.py`

**Conformance**: Structural work cannot descend with empty governance identity
unless the governance packet explicitly records that no governing problem or
pattern applies and alignment accepts that state. Pattern deviation without a
preceding pattern delta is a violation. Runtime gates that treat non-empty
`pattern_deviations` or unresolved `governance_questions` as advisory-only are
also a violation.

---

## PAT-0014: Advisory Gate Transparency

**Problem class**: Advisory surfaces (QA interception, reconciliation
adjudication) that are deliberately fail-open may silently degrade — merging
internal errors, missing targets, and parse failures into a PASS outcome
that is observationally identical to genuine approval.

**Regions**: QA interceptor, QA verdict parser, task dispatcher QA lifecycle,
reconciliation adjudicator

**Solution surfaces**: Advisory status taxonomy, distinct lifecycle logging,
degraded-outcome preservation.

**Philosophy**: Evidence preservation. Partial solutions must explain why.
Unresolved states bubble upward rather than collapsing into success. Advisory
authority must be explicit — fail-open-to-baseline is allowed; evidence
erasure is not.

**Template**:
1. Advisory surfaces must declare their authority level explicitly. Fail-open
   behavior must be documented and intentional, not a side effect of bare
   exception handling.
2. Advisory outcomes must distinguish at minimum:
   - genuine approval (the advisory surface evaluated and approved) —
     `reason_code: None`
   - genuine rejection (the advisory surface evaluated and rejected) —
     `reason_code: None`
   - degraded/error (the advisory surface failed internally — dispatch fell
     back to baseline behavior) — `reason_code` carries the specific
     degradation cause: `unparseable`, `dispatch_error`,
     `target_unavailable`, `safety_blocked`
3. Degraded advisory outcomes must be logged with a distinct status (not
   merged into PASS). Lifecycle events, telemetry, and audit surfaces must
   preserve the distinction between approved and degraded. The `reason_code`
   must flow through all layers: parser → interceptor → dispatcher →
   notifier.
4. Advisory surfaces must not become sole authoritative readiness or coverage
   gates. If an advisory surface's output is later consumed as an
   authoritative signal, it must be promoted to PAT-0008 governance or the
   consuming surface must handle the degraded case explicitly.

**Canonical instance**: `src/qa/service/qa_interceptor.py` — QA dispatch
interception with deliberate fail-open behavior.

**Known instances**:
- `src/qa/service/qa_interceptor.py` — QA interception
- `src/qa/helpers/qa_verdict.py` — QA verdict parsing
- `src/flow/engine/task_dispatcher.py` — QA lifecycle event logging
- `src/flow/service/notifier.py` — QA result notification
- `src/reconciliation/service/adjudicator.py` — reconciliation
  fail-open behavior

**Conformance**: Advisory fail-open behavior is permitted only when the
degraded outcome is recorded distinctly from genuine approval. Logging a
QA error as `qa:passed` or mapping a malformed verdict to PASS are violations.
The fail-open/fail-closed distinction must be explicit in the code and
documented in the agent file or function docstring.

---

## PAT-0015: Positive Contract Testing

**Problem class**: Test regressions expressed as source-text archaeology
(grepping codebase files to confirm deleted strings are absent) rather than
positive assertions about current system behavior.

**Regions**: integration tests, regression tests, component tests

**Solution surfaces**: Positive behavioral assertions, output-shape contracts,
round-trip fixture tests, presence tests over absence tests.

**Philosophy**: Tests assert what the system *does*, not what it *used to
contain*. Grepping source files for absent strings is repository archaeology,
not a behavioral contract. When source text changes, the grep breaks or becomes
a false pass — either way it says nothing about whether the behavior is correct.

**Template**:
1. Express the desired invariant as a **positive assertion** about current
   system outputs, return values, or observable behavior.
2. If verifying that a routing/branching path was removed, test the positive
   contract that replaced it (e.g., "mode is observation" means assert
   `resolve_project_mode` and `write_mode_contract` are present — not that
   `if mode == 'greenfield'` is absent).
3. If verifying that a heuristic was replaced by agent judgment, assert the
   current mechanism's keywords (e.g., "heuristic", "judgment", "evidence")
   rather than grepping for the old rule text.
4. Source-text grep tests are permitted only when the invariant genuinely
   requires source-level verification (e.g., a central hardcoded-literal sweep
   or a published static contract whose text is itself the artifact under
   review). Even then, prefer positive contract tests where possible.
5. When the file itself is the published contract artifact (agent skeleton,
   schema, or template), positive presence assertions are acceptable; absence
   assertions still need strong justification and should usually be replaced by
   shape or behavior checks.
6. Corruption-preservation, path-identity, and writer→reader handoff invariants
   should be tested with realistic fixture round-trips rather than grepping
   implementation source for warning text or filename suffixes.
7. When human-facing docs publish live runtime inventories (agents, routes,
   entrypoints, authoritative paths), test the live registry or generated
   artifact that authorizes those claims rather than grepping for stale
   literals.
8. When an eval harness, trigger adapter, or other executable audit surface is
   meant to exercise the live runtime, include at least one positive contract
   that imports or executes that surface against the current package layout.
   Import/bootstrap failure is a broken contract, not incidental drift.

**Canonical instance**: `run_structural_checks()` in
`evals/agentic/structural_checks.py`

**Known instances**:
- `evals/agentic/structural_checks.py` — positive assertions over current
  collected outputs (existence, JSON validity, keys, headings, DB rows,
  signal states)
- `evals/agentic/fixtures/readiness-triggers-research-planner/scenario.yaml`
  — writer→reader / flow-submission contract
- `evals/agentic/fixtures/philosophy-stale-source-blocks/scenario.yaml`
  — fail-closed bootstrap status contract
- `evals/agentic/fixtures/qa-intercept-on-dispatch/scenario.yaml`
  — advisory interception artifact contract

**Conformance**: New regression tests MUST express invariants as positive
assertions about current behavior. Source-text grep for absent strings is a
violation unless the invariant genuinely requires source-level verification.
Converting existing source-grep tests to positive contracts is expected during
audit rounds. High-risk archive→runtime projection contracts,
writer→reader handoff contracts, and authoritative runtime-inventory
contracts should have at least one representative positive test with
realistic fixture or registry shapes.

---


## PAT-0016: Runtime Inventory Truth & Surface Retirement

**Problem class**: Authoritative docs, audit prompts, governance self-reports,
and inventory claims drift away from the live runtime, while retired execution
surfaces remain in active discovery trees and silently reintroduce split-brain
instructions.

**Regions**: architecture docs, audit prompts, governance self-reports, agent
inventory, route inventory, live-vs-legacy execution surfaces

**Solution surfaces**: live registry derivation, generated or contract-checked
inventory summaries, archive quarantine for retired surfaces, discovery-boundary
enforcement, audit-time self-verification of present-tense runtime claims.

**Philosophy**: Accuracy over shortcuts. Context optimization. Migration must be
atomic per surface. A retired path that remains in a live discovery tree is not
retired. Governance cannot steer the system if its own state reports are stale.

**Template**:
1. Any document that claims live agent counts, task counts, namespaces,
   entrypoints, authoritative path layout, or current migration/health status
   for those surfaces must derive those claims from live runtime registries
   (`taskrouter.agents`, `taskrouter.discovery`, `src/*/routes.py`) or be
   covered by positive contract tests against those registries.
2. Hand-maintained inventory counts or health/status summaries may appear in
   human docs only when a generator or positive contract keeps them
   synchronized with runtime.
3. Retired execution surfaces must be removed from live discovery trees
   (`src/*/agents`, live scripts, active templates) or moved to an explicit
   archive/legacy location excluded from runtime discovery.
4. Migration of an execution surface is incomplete until the new runtime path,
   its agent/template contracts, and the human-facing authoritative docs all
   agree.
5. Historical material kept for archaeology must be clearly marked non-runtime
   and must not be scanned by live resolvers such as `all_agent_files()` or
   live route discovery.
6. Eval and audit prompts that direct humans or agents into key runtime surfaces
   are authoritative contracts and must follow the same synchronization rule.
7. Governance self-reports that summarize current system state (pattern health
   notes, problem statuses, risk statuses, audit history summaries) are
   authoritative surfaces when they make present-tense claims about runtime or
   migration state, and must be updated atomically with the corresponding code
   or explicitly scoped as historical context.

**Canonical instance**: `src/taskrouter/agents.py` +
`src/taskrouter/discovery.py`

**Known instances**:
- `src/taskrouter/agents.py` — live agent inventory
- `src/taskrouter/discovery.py` and all `src/*/routes.py` — live task
  vocabulary
- `system-synthesis.md` — published architecture + inventory summary
- `governance/audit/prompt.md` — audit-facing codebase map
- `src/SKILL.md`, `src/implement.md`, and `src/models.md` — operator-facing
  runtime docs
- `src/dispatch/agents/` legacy residues — retirement-boundary surfaces
- `evals/harness.py` and `evals/agentic/trigger_adapters.py` — executable
  runtime-entry surfaces
- `evals/agentic/structural_checks.py` — positive contract surface that should
  enforce inventory truth once wired
- `governance/problems/index.md`, `governance/risk-register.md`, and
  `governance/audit/history.md` — governance self-report surfaces when they
  make present-tense runtime or migration claims

**Conformance**: No authoritative runtime document or governance self-report
may hand-wave counts, paths, or health/status claims that disagree with live
registries and code. No retired surface may remain discoverable by live
agent/path resolution. Migration rounds are incomplete until inventories,
docs, executable adapters, and discovery boundaries agree.

---

## Health Notes

- **PAT-0001 (Corruption Preservation)**: **Unhealthy.** R111 correctly
  migrated the last two known scan/substrate local malformed-artifact
  conventions (`scan/substrate/related_files.py` and
  `scan/substrate/schemas.py`) to shared `read_json()`/`rename_malformed()`
  primitives, but the delivered snapshot still has live violations in
  `src/dispatch/service/tool_registry_manager.py` (local `json.loads()` +
  `.copy2()` preservation that leaves the corrupt canonical file in place) and
  `src/coordination/prompt/writers.py` (copy-back restoration from
  `.malformed.json`). The pattern remains correct; conformance regressed or was
  overstated in the catalog.
- **PAT-0002 (Prompt Safety)**: Healthy. R109 clarified that payload-file
  contents are untrusted dynamic content even when delivered through internal
  tasks. QA interceptor now validates payload content before dispatch.
- **PAT-0003 (Path Registry)**: **Unhealthy.** The template remains correct,
  but saturation is still incomplete for several durable families. Confirmed
  live islands in this snapshot include manual construction of
  `tools-available` and `alignment-surface` in
  `src/dispatch/prompt/context_builder.py`, manual `alignment-surface` and
  `proposal-state` references in
  `src/implementation/service/section_reexplorer.py`, manual
  `strategic-state.json` in
  `src/orchestrator/engine/strategic_state_builder.py`, and manual
  `codemap-corrections.json` in `src/scan/codemap/codemap_builder.py`.
  The next round must finish consumer saturation for these confirmed families
  and then re-audit any remaining repeated section-scoped durable families
  before declaring PAT-0003 healthy.
- **PAT-0004 (Flow System)**: Healthy.
- **PAT-0005 (Policy-Driven Models)**: Healthy. R110 replaced the last two
  local `policy.get()` fallback sites (`proposal_cycle.py` intent_judge,
  `related_file_resolver.py` validation) with `resolve()` / direct key access.
  Stale GPT/Opus docstrings rewritten to policy-based language.
- **PAT-0006 (Freshness Computation)**: Healthy in mechanism. Governance packet
  overscoping fixed in R108 (no-match returns empty candidates, not full
  archive), reducing avoidable invalidation pressure.
- **PAT-0007 (Cycle-Aware Status)**: Healthy and intentionally narrow.
- **PAT-0008 (Fail-Closed Defaults)**: Healthy. Narrowed to authoritative
  surfaces in R108. Governance index loading now writes structured
  parse-failure status (R108). Advisory surfaces governed by PAT-0014.
- **PAT-0009 (Blocker Taxonomy)**: Healthy. Governance blocker normalization
  fixed in R106 — blocker_manager.py handles both proposal-state and governance blocker
  shapes.
- **PAT-0010 (Intent Surfaces)**: Healthy.
- **PAT-0011 (Applicable Governance Packet Threading)**: Healthy. R110 fixed
  governance loader to preserve wrapped bullet continuation lines and parse
  numbered template items as individual array entries. Runtime pattern records
  now carry full template/conformance/instance data from the real catalog.
- **PAT-0012 (Post-Implementation Governance Feedback)**: Healthy. Debt
  promotion is idempotent with material-payload-aware dedup (R105).
- **PAT-0013 (Governed Proposal Identity)**: Healthy. Root semantics fixed in
  R106, runtime gate logic correct. Packet ambiguity bridged to readiness in
  R107.
- **PAT-0014 (Advisory Gate Transparency)**: Healthy. R109 implemented
  structured advisory status model: QA verdict parser returns DEGRADED (not
  PASS) for malformed/unknown output; interceptor returns 3-tuple with
  reason_code (`unparseable`, `dispatch_error`, `target_unavailable`,
  `safety_blocked`); dispatcher logs `qa:degraded` distinctly from
  `qa:passed`; notifier carries reason_code through lifecycle events;
  reconciliation adjudicator references PAT-0014 degraded states in warnings.
- **PAT-0015 (Positive Contract Testing)**: **Unhealthy.** The philosophy is
  settled, and the current snapshot's structural checks are positive assertions
  over current outputs. However, no current positive contract checks
  authoritative runtime-inventory truth, retirement-boundary correctness, or
  the executable eval-entry surfaces themselves (`evals/harness.py`,
  `evals/agentic/trigger_adapters.py`). `pyproject.toml` also still points
  pytest at nonexistent legacy paths, reinforcing that the contract surface is
  incomplete in the delivered codebase.
- **PAT-0016 (Runtime Inventory Truth & Surface Retirement)**:
  **Unhealthy.** R111 correctly updated `system-synthesis.md` to
  `49 agents / 48 routes / 12 namespaces`, but the delivered snapshot still
  contains stale authoritative surfaces: `governance/audit/prompt.md` still
  claims `50 agents across 11 systems` and points auditors to nonexistent
  `src/scripts/*` regions; `src/SKILL.md`, `src/implement.md`, and
  `src/models.md` still describe the legacy layout; `evals/harness.py` and
  `evals/agentic/trigger_adapters.py` still target pre-migration import/script
  paths; `src/flow/engine/task_dispatcher.py` and two risk agent files still
  publish stale paths; `src/pyproject.toml` still declares legacy test/import
  roots; `src/dispatch/agents/monitor.md` remains in live discovery despite no
  runtime route; and `governance/problems/index.md`,
  `governance/risk-register.md`, and `governance/audit/history.md` contain
  present-tense claims that no longer match the code.
