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
- **Mode is observation, not routing** — greenfield / brownfield / PRD /
  partial-governance describe what bootstrap observed and which starting
  artifacts exist; they do not justify divergent recursive loops, proposal
  artifact shapes, or descent rules.

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
- `src/dispatch/service/tool_surface_writer.py`,
  `src/dispatch/service/tool_bridge.py`, and
  `src/dispatch/service/tool_validator.py` — tool registry / friction
  readers and repair surfaces
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
- `src/dispatch/service/tool_surface_writer.py`,
  `src/dispatch/service/tool_bridge.py`, and
  `src/dispatch/service/tool_validator.py`
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
10. If both absolute-path accessors and relpath helpers publish the same
    durable family, the relpath form must derive from the same canonical naming
    contract rather than duplicating string literals.
11. When authoritative logic needs to discover multiple artifacts in the same
    durable family (for example all section decisions, all recurrence signals,
    or all research-question artifacts), the owning repository or `PathRegistry`
    surface must expose a named iterator/listing helper instead of repeating
    glob patterns at call sites.

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
- `src/scan/related/related_file_resolver.py` — scan validation escalation
  must stay policy-resolved, not local-fallback driven
- `src/implementation/service/microstrategy_generator.py`
- `src/implementation/engine/implementation_cycle.py`
- `src/proposal/service/readiness_resolver.py`
- `src/dispatch/service/context_sidecar.py` — context sidecar
  materialization via `PathRegistry.context_sidecar()` accessor (R109)
- `src/dispatch/prompt/context_builder.py`,
  `src/implementation/service/section_reexplorer.py`,
  `src/implementation/service/traceability_writer.py`, and
  `src/orchestrator/engine/section_pipeline.py`
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
- Tool-surface blocker writers in
  `src/dispatch/service/tool_surface_writer.py`,
  `src/dispatch/service/tool_bridge.py`, and
  `src/dispatch/service/tool_validator.py`
- Freshness/hash consumers in `src/staleness/service/freshness_calculator.py`
  and `src/staleness/service/input_hasher.py`
- `src/scan/substrate/related_files.py` and
  `src/scan/substrate/prompt_builder.py` — substrate-stage
  related-files update signals via `related_files_update_dir()`
- Governance helper file consumers in
  `src/intake/repository/governance_loader.py` and
  `src/intake/service/governance_packet_builder.py`
- Trace-index consumers in `src/intake/service/assessment_evaluator.py`
  and `src/implementation/service/traceability_writer.py`
- Section-decision family consumers in
  `src/orchestrator/service/section_decision_store.py`,
  `src/dispatch/prompt/context_builder.py`,
  `src/staleness/service/freshness_calculator.py`, and
  `src/staleness/service/input_hasher.py`
- Intent-triage family consumers in
  `src/intent/service/intent_triager.py`,
  `src/proposal/engine/proposal_phase.py`, and
  `src/implementation/service/risk_artifacts.py`
- Coordination-family consumers in
  `src/coordination/service/planner.py`,
  `src/coordination/service/stall_detector.py`,
  `src/coordination/engine/plan_executor.py`, and
  `src/implementation/service/scope_delta_aggregator.py`
- Flow family relpath helpers in `src/flow/repository/flow_context_store.py`
- Proposal/alignment output-family consumers in
  `src/proposal/engine/proposal_cycle.py`,
  `src/dispatch/service/context_sidecar.py`, and
  `src/implementation/service/triage_orchestrator.py`
- Scope-delta family consumers in
  `src/proposal/engine/readiness_gate.py`,
  `src/proposal/engine/proposal_cycle.py`,
  `src/proposal/service/excerpt_extractor.py`,
  `src/scan/service/feedback_router.py`,
  `src/implementation/service/scope_delta_parser.py`,
  `src/implementation/service/scope_delta_aggregator.py`, and
  `src/reconciliation/repository/results.py`
- Research-question family consumer in
  `src/proposal/engine/readiness_gate.py`
- Cross-section note family consumers in
  `src/coordination/repository/notes.py`,
  `src/proposal/service/proposal_prep.py`,
  `src/intent/engine/intent_initializer.py`,
  `src/staleness/service/freshness_calculator.py`,
  `src/staleness/service/input_hasher.py`,
  `src/risk/prompt/writers.py`, and
  `src/coordination/prompt/writers.py`
- Decision / proposal-signal discovery consumers in
  `src/orchestrator/repository/decisions.py`,
  `src/implementation/service/section_reexplorer.py`,
  `src/implementation/service/microstrategy_decider.py`, and
  `src/intent/service/philosophy_bootstrapper.py`
- Research-question / recurrence discovery consumers in
  `src/orchestrator/engine/strategic_state_builder.py` and
  `src/coordination/service/problem_resolver.py`
- Input-ref family discovery consumers in
  `src/dispatch/prompt/context_builder.py`,
  `src/staleness/service/input_hasher.py`,
  `src/implementation/repository/roal_index.py`, and
  `src/implementation/service/impact_analyzer.py`
- Section/proposal listing consumers in
  `src/intent/service/philosophy_bootstrapper.py`,
  `src/scan/service/section_loader.py`, and
  `src/proposal/repository/excerpts.py`
- Section-input risk-artifact family consumers in
  `src/implementation/service/risk_artifacts.py`,
  `src/risk/prompt/writers.py`, and
  `src/implementation/engine/implementation_phase.py`

**Conformance**: No durable artifact path may be reconstructed ad hoc. Any new
artifact path MUST be added to `PathRegistry`, and all authoritative consumers
must use that accessor. A pattern round is not complete if it adds the accessor
but leaves the primary readers/writers on hand-built paths. Durable relpath
helpers that mirror registry-governed families must stay mechanically aligned
with the registry naming contract.

---

## PAT-0004: Flow System

**Problem class**: Multi-step, multi-agent task orchestration with
parallelism, resumable follow-on work, and accumulated results.

**Regions**: flow system, task orchestration, section state orchestration,
research, coordination, verification, testing

**Solution surfaces**: Flow schema, task flow, flow catalog, state-machine
submission layer, research plan executor, verification chain builder,
completion routing.

**Philosophy**: Scripts dispatch, agents decide. Structured submission over ad
hoc spawning. The state machine decides *which* work to submit next; the flow
system owns *how* multi-step work is packaged and delivered.

**Template**:
- **State-driven submission** — higher-level controllers select the next
  `TaskSpec` / chain / fanout from persisted runtime state rather than hardcode
  nested controller loops
- **Chains** — sequential task lists via `submit_chain()`
- **Fanout** — parallel branches via `submit_fanout()` with `BranchSpec`
- **Gates** — accumulate branch results, fire synthesis via `GateSpec`
- **Named packages** — `_PACKAGE_REGISTRY` in `src/flow/repository/catalog.py`
  maps names to `TaskSpec` sequences
- **Completion routing** — reconciler/follow-on handlers may submit additional
  flow work (verification, testing, coordination) from structured results
  instead of running ad hoc inline control loops

**Canonical instance**: `StateMachineOrchestrator.run()` in
`src/orchestrator/engine/state_machine_orchestrator.py` paired with direct
flow execution in `execute_research_plan()` from
`src/research/engine/research_plan_executor.py`

**Known instances**:
- `src/research/engine/research_plan_executor.py` — research flow
  (plan → tickets → synthesis → verify)
- `src/orchestrator/engine/state_machine_orchestrator.py` — per-section state
  drives flow submission
- `src/implementation/engine/implementation_cycle.py` and
  `src/verification/service/chain_builder.py` — post-implementation
  verification/testing follow-on chain
- `src/coordination/engine/plan_executor.py` and
  `src/flow/engine/reconciler.py` — reactive coordination / blocker-routing
  follow-ons
- Named packages in `src/flow/repository/catalog.py`

**Conformance**: New multi-step workflows MUST use the flow system. Higher-
level controllers may decide when to submit the next flow work item, but they
may not bypass flow primitives with direct multi-agent orchestration, nested
agent choreography, or hidden retry loops.

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
6. Local fallback defaults or policy-key fallback chains must be sourced
   from the authoritative default module or centralized resolver, not
   improvised at the callsite. Operational callsites must not use
   `policy.get(...)` to choose between policy keys or literals (for example
   `policy.get("exploration", policy["validation"])`) — use
   `resolve(policy, key)`, direct required-key access, or explicit policy
   normalization upstream instead. The authoritative default lives in
   `ModelPolicy` (for main policy) or `DEFAULT_SCAN_MODELS` (for scan policy),
   not at the callsite.
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

**Solution surfaces**: Bootstrap assessor, governance loader, governance packet
builder, prompt context assembly, freshness service, section-input hasher.

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
   - bootstrap observations such as entry classification (`greenfield`,
     `brownfield`, `prd`, `partial_governance`) and spec-derived governance
     seeds when they exist
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
- `src/orchestrator/service/bootstrap_assessor.py` and
  `src/orchestrator/engine/bootstrap_orchestrator.py` — classify entry
  conditions, persist `entry-classification.json`, and seed PRD-derived
  governance inputs without routing away from the main recursive loop
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
an accessor derived from it) rather than reparsing governance markdown ad hoc.
A section packet that is only section-labeled but not section-scoped is a
pattern violation. Entry classification and spec-derived governance seeds are
observational packet inputs, not a license for divergent runtime packet shapes
or bypassed governance steps. Pattern records truncated to shallow single-line
summaries such that conformance/change-policy data is unavailable at runtime
are also a pattern violation.

---

## PAT-0012: Post-Implementation Governance Feedback

**Problem class**: Landed changes introduce governance-visible risk that must
be verified, assessed, traced, and routed into stabilization without
inventing a parallel control loop.

**Regions**: governance, assessment, verification, testing, trace, flow
reconciler, stabilization

**Solution surfaces**: Post-implementation assessment, verification chain
builder, verification gate, verdict synthesis, flow reconciler, debt signal
staging, risk register, traceability enrichment.

**Philosophy**: Governance continues after code lands. Implementation success
is not section closure: required verification/testing gates, assessment, and
stabilization must align with the same problem / pattern / philosophy
hierarchy that shaped implementation.

**Template**:
1. After successful implementation, queue a post-implementation follow-on path
   with references to governance packet, trace artifacts, proposal, problem
   frame, and current modified-file evidence.
2. Refresh structural context when the posture requires it (for example,
   targeted codemap refresh for guarded scopes) before asking downstream
   verifiers/tests to reason about the landed shape.
3. Run required verification/testing through the flow system:
   - `verification.structural` — gate on section-local structural integrity
   - `verification.integration` — advisory cross-section interface checks when
     warranted
   - `testing.behavioral` — gate on problem-derived behavioral contracts
   - `testing.rca` — advisory root-cause analysis on behavioral failures
4. Scope verification by posture rather than blanket policy. Low-risk sections
   may get imports-only structural checks; higher postures expand interface and
   behavioral coverage.
5. Validate assessment and verification/test artifacts with PAT-0001.
6. Merge or validate governance identity across **all authoritative trace
   surfaces**:
   - `trace/section-N.json`
   - `trace-map/section-N.json`
   - `traceability.json`
7. Synthesize outcomes conservatively: a section is not governance-aligned /
   complete until required verification gates pass and the combined assessment +
   verification disposition permits proceeding.
8. Route outcomes mechanically:
   - `accept` → confirm governance lineage
   - `accept_with_debt` → emit debt / risk-register promotion signal
   - `refactor_required` → emit structured blocker signal
   - cross-section verification / RCA findings → emit coordination-facing
     blocker or escalation signals without reopening unrelated sections
9. A bounded stabilization consumer promotes accepted debt into the
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
10. Post-implementation assessment may enrich, challenge, or append to
    proposal-time governance identity; it must not be the first place
    governance lineage appears.

**Canonical instance**: `ImplementationCycle._finalize()` in
`src/implementation/engine/implementation_cycle.py` with gate checks in
`src/verification/service/verification_gate.py` and completion handling in
`src/flow/engine/reconciler.py`

**Known instances**:
- `src/implementation/engine/implementation_cycle.py` — queues assessment and
  verification chain, writes trace artifacts
- `src/intake/service/assessment_evaluator.py`
- `src/verification/service/chain_builder.py`,
  `src/verification/service/verification_gate.py`, and
  `src/verification/service/verdict_synthesis.py`
- `src/flow/engine/reconciler.py` — assessment / verification / testing
  completion routing
- `src/implementation/service/traceability_writer.py`
- `src/signals/service/section_communicator.py` — append-log traceability
  surface
- `governance/risk-register.md` — authoritative debt/risk target

**Conformance**: Post-implementation follow-on is not complete until required
verification/test gates have either passed or produced governed retry /
escalation signals, debt / refactor outcomes enter a governed stabilization
surface, and all trace surfaces carry governance lineage. Orphaned debt
signals, duplicate re-promotion of unchanged debt, verification-blind section
closure, and assessment-originated lineage are pattern violations.

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

**Regions**: integration tests, regression tests, component tests, authoritative
prompt/template/eval contracts

**Solution surfaces**: Positive behavioral assertions, output-shape contracts,
round-trip fixture tests, presence tests over absence tests, cross-surface
schema sync checks, family-saturation checks, reference-integrity checks.

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
9. When a machine artifact schema is published in multiple active surfaces
   (repository schema, agent file, dispatch template, validator, scenario
   prompt, fixture), include a positive contract that compares those surfaces to
   the canonical schema.
10. When a recurring durable family is mid-migration, add at least one positive
    family-saturation check proving that authoritative readers/writers use the
    canonical accessor or that any exceptions are explicitly cataloged.
11. When authoritative docs or governance archives cite live problem IDs,
    pattern IDs, or runtime file paths, add positive contracts that resolve
    those references against the corresponding archive and repository.
12. When architecture doctrine claims that only explicit composition roots may
    touch the service container, add a positive contract that enforces a narrow
    allowlist of sanctioned container-touch sites.
13. When governance self-report surfaces or test-maintained allowlists publish
    current residue / health inventories, add a positive contract that derives
    the live inventory from code and compares it to the published set.
14. When model-selection policy is supposed to be centralized, add a positive
    contract that fails if operational callsites perform local
    `policy.get(...)` fallback logic instead of using the authoritative
    resolver or required-key contract.

**Canonical instance**: `run_structural_checks()` in
`evals/agentic/structural_checks.py`

**Known instances**:
- `tests/component/test_positive_contracts.py` — component-level positive
  contract suite for doctrine, inventory truth, boundary enforcement, and
  PAT-0005 callsite centralization
- `tests/component/test_model_policy.py` and
  `tests/component/test_scan_policy_coherence.py` — policy/default contract
  surfaces complementary to the broader positive-contract suite
- `evals/agentic/structural_checks.py` — positive assertions over current
  collected outputs (existence, JSON validity, keys, headings, DB rows,
  signal states)
- `evals/agentic/fixtures/readiness-triggers-research-planner/scenario.yaml`
  — writer→reader / flow-submission contract
- `evals/agentic/fixtures/philosophy-stale-source-blocks/scenario.yaml`
  — fail-closed bootstrap status contract
- `evals/agentic/fixtures/qa-intercept-on-dispatch/scenario.yaml`
  — advisory interception artifact contract
- `evals/scenarios/proposal_state.py`,
  `evals/scenarios/readiness_gate.py`, and
  `evals/scenarios/risk_assessor.py` — proposal-state contract surfaces
- Proposal-state fixture artifacts under
  `evals/agentic/fixtures/*/planspace/artifacts/proposals/section-01-proposal-state.json`
  — canonical schema examples used by agentic scenarios

**Conformance**: New regression tests MUST express invariants as positive
assertions about current behavior. Source-text grep for absent strings is a
violation unless the invariant genuinely requires source-level verification.
Converting existing source-grep tests to positive contracts is expected during
audit rounds. High-risk archive→runtime projection contracts,
writer→reader handoff contracts, authoritative runtime-inventory
contracts, cross-surface schema-projection contracts, and recurring
family-saturation contracts should each have at least one representative
positive test with realistic fixture or registry shapes.

---


## PAT-0016: Runtime Inventory Truth & Surface Retirement

**Problem class**: Authoritative docs, audit prompts, governance/philosophy
self-reports, and inventory claims drift away from the live runtime or the
current governing intent, while retired execution surfaces remain in active
discovery trees and silently reintroduce split-brain instructions.

**Regions**: architecture docs, philosophy profiles/analysis, audit prompts,
governance self-reports, agent inventory, route inventory,
live-vs-legacy execution surfaces

**Solution surfaces**: live registry derivation, generated or contract-checked
inventory summaries, archive quarantine for retired surfaces,
discovery-boundary enforcement, audit-time self-verification of present-tense
runtime claims, faithful projection from verbatim philosophy into distilled
profiles and synthesis surfaces.

**Philosophy**: Accuracy over shortcuts. Context optimization. Migration must be
atomic per surface. A retired path that remains in a live discovery tree is not
retired. Governance cannot steer the system if its own state reports are stale,
and compressed philosophy profiles cannot safely guide the system if they omit
material governing constraints.

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
8. Any authoritative reference to a live problem ID or pattern ID in synthesis,
   audit, or governance docs must resolve to an entry that exists in the
   corresponding archive.
9. Pattern-archive `Known instances` must point to live paths unless they are
   explicitly marked historical/retired; dead paths are false inventory.
10. Test-maintained allowlists or quarantine inventories that sanction live
    residue (for example container-touch site sets) are themselves
    authoritative self-report surfaces and must be updated atomically when the
    underlying code inventory changes.
11. Distilled philosophy profiles, philosophy analyses, and architecture
    syntheses that compress verbatim governing notes are authoritative
    projection surfaces when they are used at runtime or audit time; they must
    preserve priority ordering and materially active constraints or explicitly
    scope what is omitted and point back to the fuller source.

**Canonical instance**: `src/taskrouter/agents.py` +
`src/taskrouter/discovery.py`

**Known instances**:
- `src/taskrouter/agents.py` — live agent inventory
- `src/taskrouter/discovery.py` and all `src/*/routes.py` — live task
  vocabulary
- `system-synthesis.md` — published architecture + inventory summary
- `philosophy/design-philosophy-analysis.md` and
  `philosophy/profiles/PHI-global.md` — distilled philosophy-projection
  surfaces
- `governance/patterns/index.md` — authoritative pattern archive and health
  self-report surface
- `governance/audit/prompt.md` — audit-facing codebase map
- `src/SKILL.md`, `src/implement.md`, and `src/models.md` — operator-facing
  runtime docs
- `src/dispatch/agents/` legacy residues — retirement-boundary surfaces
- `evals/harness.py` and `evals/agentic/trigger_adapters.py` — executable
  runtime-entry surfaces
- `tests/component/test_positive_contracts.py` — positive contract surface
  for inventory truth, reference integrity, and boundary enforcement
- `evals/agentic/structural_checks.py` — agentic structural contract surface
- `governance/problems/index.md`, `governance/risk-register.md`, and
  `governance/audit/history.md` — governance self-report surfaces when they
  make present-tense runtime or migration claims

**Conformance**: No authoritative runtime document, philosophy profile, or
governance self-report may hand-wave counts, paths, health/status claims, or
governing constraints that disagree with live registries, code, or the current
verbatim philosophy set. No retired surface may remain discoverable by live
agent/path resolution. Migration rounds are incomplete until inventories, docs,
executable adapters, and discovery boundaries agree.

---


## PAT-0017: Proposal-State Contract Projection

**Problem class**: The canonical proposal-state schema can drift away from the
active agent/template/eval surfaces that publish, validate, or exemplify it,
creating split-brain instructions and false confidence.

**Regions**: proposal-state repository, proposal prompts, alignment validation,
readiness, eval scenarios, eval fixtures

**Solution surfaces**: Canonical schema ownership, atomic cross-surface
projection, fail-closed runtime validation, schema-sync contract tests,
fixture-shape checks.

**Philosophy**: Proposals are problem-state artifacts. Migration must be atomic
per surface. Scripts validate structure. Do not add constraints that the user
did not ask for without first grounding them in a problem and pattern change.

**Template**:
1. One authoritative surface owns the machine-readable proposal-state schema.
2. Any required-field change must update every active surface that enumerates,
   examples, or validates the schema in the same change: agent file, dispatch
   template, validating agent, scenario prompt, and fixture.
3. Runtime validation remains fail-closed on missing keys or wrong types.
4. Example fixtures must include every canonical key, even when the value is an
   empty list or empty string.
5. Positive contract tests must compare canonical schema keys against all active
   prompt/template/eval surfaces that publish that schema.
6. New required schema fields must trace to an archived problem or accepted
   pattern change before they become part of the canonical contract.

**Canonical instance**: `src/proposal/repository/state.py` +
`src/proposal/agents/integration-proposer.md`

**Known instances**:
- `src/proposal/repository/state.py`
- `src/proposal/agents/integration-proposer.md`
- `src/templates/dispatch/integration-proposal.md`
- `src/staleness/agents/alignment-judge.md`
- `src/proposal/service/readiness_resolver.py`
- `src/proposal/engine/readiness_gate.py`
- `evals/scenarios/proposal_state.py`
- `evals/scenarios/readiness_gate.py`
- `evals/scenarios/risk_assessor.py`
- `evals/agentic/fixtures/readiness-triggers-research-planner/planspace/artifacts/proposals/section-01-proposal-state.json`
- `evals/agentic/fixtures/research-branch-stale-after-input-change/planspace/artifacts/proposals/section-01-proposal-state.json`
- `evals/agentic/fixtures/research-flow-synthesizes-dossier/planspace/artifacts/proposals/section-01-proposal-state.json`
- `evals/agentic/fixtures/research-planner-routes-value-choice-upward/planspace/artifacts/proposals/section-01-proposal-state.json`

**Conformance**: Proposal-state producers, validators, examples, and fixtures
must agree on the exact required key set. A runtime schema change that is not
atomically reflected across those surfaces is a violation. Requiring fields with
no traced problem/pattern basis is also a violation.

---

## PAT-0018: Behavioral Doctrine Projection

**Problem class**: Behavioral doctrine projection drift / method-of-thinking
split-brain.

**Regions**: proposal, implementation, coordination, risk, scan

**Solution surfaces**: agent files, dispatch templates, operator docs,
positive doctrine-projection contracts

**Philosophy**: Agent files carry the method of thinking. When execution
doctrine changes, the routed agent/template surfaces that embody that doctrine
must change atomically.

**Template**:
1. One bounded set of authoritative doctrine surfaces owns the live method
   contract (`src/SKILL.md`, `src/implement.md`).
2. All routed agent files, dispatch templates, and validating agents must be
   atomically updated when that doctrine changes.
3. No surface may reintroduce literal "zero risk" claims, "trivially small"
   shortcut exceptions, or "simple enough to skip a step" reasoning when the
   authoritative doctrine says otherwise.
4. The authoritative doctrine heading is **"Accuracy First — Zero Tolerance
   for Fabrication"** with body text asserting zero tolerance for fabricated
   understanding or bypassed safeguards, and proportional operational risk via
   ROAL.

**Canonical instance**: `src/SKILL.md` +
`src/proposal/agents/integration-proposer.md`

**Known instances**:
- `src/proposal/agents/integration-proposer.md`
- `src/templates/dispatch/integration-proposal.md`
- `src/implementation/agents/implementation-strategist.md`
- `src/templates/dispatch/strategic-implementation.md`
- `src/coordination/agents/coordination-planner.md`
- `src/coordination/agents/bridge-agent.md`
- `src/risk/agents/risk-assessor.md`
- `src/scan/agents/substrate-shard-explorer.md`
- `src/scan/agents/substrate-pruner.md`
- `src/scan/agents/substrate-seeder.md`
- `src/implementation/agents/microstrategy-writer.md`
- `tests/component/test_positive_contracts.py` — doctrine heading / wording
  contract surface

**Conformance**: All listed surfaces must use the authoritative heading and
must not contain the stale "Zero Risk Tolerance" heading, "trivially small"
exception language, or "accept zero risk" phrasing. Positive contract tests
enforce this (PAT-0015).

---


## PAT-0019: Constructor Dependency Injection / Composition-Root Boundary

**Problem class**: Hidden service-locator lookups inside runtime modules make
dependencies invisible, keep old and new wiring models alive at the same time,
and create split-brain between published architecture and actual execution.

**Regions**: `src/containers.py`, composition helpers, engine/service/repository
constructors, legacy compatibility wrappers, authoritative architecture docs

**Solution surfaces**: explicit constructor/function parameters, narrow
composition roots, compatibility-shim quarantine, boundary contract tests,
retirement of service-locator residue

**Philosophy**: Explicit structure beats hidden global state. Bounded autonomy
depends on visible interfaces. Migration must be atomic per surface, and the
docs must describe the same wiring rule the runtime actually follows.

**Template**:
1. Production engines, services, repositories, and helpers declare required
   collaborators explicitly in constructors or function parameters.
2. Only composition roots (`main()`, CLI entry points, or clearly named
   `build_*` / `_default_*` adapter functions whose sole job is object wiring)
   may call `Services.*()`.
3. Runtime methods and constructors may not silently fall back to `Services.*()`
   when dependencies are omitted. Missing collaborators are a construction-time
   error, not a runtime convenience lookup.
4. Backward-compat wrappers are permitted only in quarantined adapter surfaces
   that delegate immediately to fully constructed objects and are explicitly
   documented as migration residue.
5. The service container owns wiring; production business logic owns behavior.
   The same module should not do both unless it is the designated composition
   root for that surface.
6. Published architecture docs, docstrings, and examples must describe the same
   boundary the code actually enforces.
7. Tests may use container overrides freely, but production code may not import
   the container for convenience inside business logic.

**Canonical instance**: `src/orchestrator/engine/pipeline_orchestrator.py`
(`main()` + composition helpers) paired with constructor-injected runtime
classes such as `src/orchestrator/engine/state_machine_orchestrator.py`

**Known instances**:
- `src/containers.py` — service-container definition and wiring root
- `src/orchestrator/engine/pipeline_orchestrator.py`,
  `src/orchestrator/engine/section_pipeline.py`,
  `src/risk/engine/risk_assessor.py`,
  `src/scan/cli.py`, and
  `src/flow/engine/task_dispatcher.py` — sanctioned composition roots / entry
  surfaces
- `src/scan/scan_dispatcher.py`,
  `src/scan/explore/deep_scanner.py`,
  `src/scan/substrate/substrate_discoverer.py`, and
  `src/proposal/engine/proposal_phase.py` — explicitly scoped build/helper
  surfaces that may touch `Services` only for wiring
- `src/coordination/engine/global_coordinator.py`,
  `src/orchestrator/engine/state_machine_orchestrator.py`,
  `src/implementation/engine/implementation_phase.py`, and
  `src/coordination/engine/coordination_controller.py` — constructor-injected
  runtime classes
- `src/staleness/service/section_alignment_checker.py` and
  `src/staleness/service/global_alignment_rechecker.py` — runtime method-level
  container lookups (accepted residue)
- `src/dispatch/engine/section_dispatcher.py` and
  `src/flow/service/task_request_ingestor.py` — quarantined helper-level
  residue
- `src/signals/service/section_communicator.py`,
  `src/signals/service/message_poller.py`, and
  `src/signals/service/blocker_manager.py` — compatibility-wrapper /
  backward-compat residue
- `system-synthesis.md`, `governance/risk-register.md`, and
  `tests/component/test_positive_contracts.py` — authoritative boundary-report
  surfaces that must agree with runtime

**Conformance**: Production runtime code may not touch `Services` outside
sanctioned composition roots or explicitly quarantined compatibility adapters.
Hidden container fallbacks are a violation. Authoritative docs and docstrings
must match the live boundary.

---

## PAT-0020: Transition Table as Data

**Problem class**: Workflow structure hidden inside controller code becomes
hard to audit, hard to resume, and easy to fork accidentally when retries,
reopens, and escalation rules live in ad hoc loops.

**Regions**: section state machine, orchestration, completion routing,
persisted execution state

**Solution surfaces**: authoritative transition table, persisted section state,
transition history, single-shot handlers, circuit breakers, transition context

**Philosophy**: Workflow structure should be inspectable data, not buried in
controller code. Scripts submit and persist; handlers do one bounded piece of
work and report the event that happened.

**Template**:
1. Define legal `(state, event) → transition` records in one authoritative
   table or equivalent data structure.
2. Persist current state and transition history in authoritative runtime
   storage so resume reads state instead of replaying controller assumptions.
3. Handlers are single-shot: dispatch one agent or perform one mechanical
   check, then return an event/context. Internal convergence loops are a
   violation.
4. Retry, reopen, and backtracking behavior must be expressed as explicit
   self-transitions or back-edges rather than handler-owned `while True`
   logic.
5. Circuit breakers / attempt caps belong next to transition evaluation and
   escalate via explicit states or events.
6. Side effects and decision context needed for audit or resume must be
   recorded in transition context/history, not held only in memory.

**Canonical instance**: `TRANSITIONS` + `advance_section()` in
`src/orchestrator/engine/section_state_machine.py`

**Known instances**:
- `src/orchestrator/engine/section_state_machine.py` — authoritative
  transition table, persisted `section_states` / `section_transitions`, and
  circuit breakers
- `src/orchestrator/engine/state_machine_orchestrator.py` — state-driven task
  submission and event-based advancement
- `src/section/routes.py` — one actionable route per single-shot state handler
- `src/flow/engine/reconciler.py` — task completion advances persisted state
  instead of re-entering hidden controller loops

**Conformance**: Legal transitions must be represented in the authoritative
transition table. Handlers must be single-shot and return events / persisted
artifacts for later completion routing, not spin internally. Side effects that
matter to resume or audit must be recorded in transition context/history.

---

## PAT-0021: Poll and Check Unblock

**Problem class**: Blocked work needs a simple, resumable way to wake up when
missing information arrives without maintaining a brittle dependency graph.

**Regions**: state-machine orchestration, blocker management, readiness,
coordination, verification

**Solution surfaces**: blocked-state polling, blocker artifacts, unblock
checks, readiness overlays, starvation detection

**Philosophy**: Prefer simple observable re-checking over speculative graph
maintenance. The system should wake work by re-reading authoritative artifacts,
not by trusting remembered dependency edges.

**Template**:
1. When work blocks on missing information, persist the blocker reason/scope in
   authoritative state or artifacts and move the item to `BLOCKED`.
2. Each orchestration pass enumerates blocked items and re-runs bounded
   unblock checks against authoritative artifacts/signals.
3. If the information now exists, emit an explicit unblock event and return the
   item to the appropriate actionable state.
4. Coordination, research, verification, or other subsystems may resolve the
   blocker indirectly; unblock logic must check produced evidence rather than
   special-case which subsystem created it.
5. Checks must be idempotent and cheap enough to run every pass.
6. Complexity is intentionally `O(blocked_items × checks)`; do **not** replace
   this with a dependency graph unless the pattern archive itself changes.

**Canonical instance**: `_check_unblock()` in
`src/orchestrator/engine/state_machine_orchestrator.py`

**Known instances**:
- `src/orchestrator/engine/state_machine_orchestrator.py` — per-pass blocked
  section polling and unblock transitions
- `src/proposal/service/readiness_resolver.py` — blocker evaluation against
  live artifacts, shared seams, and substrate overlays
- `src/flow/service/starvation_detector.py` together with
  `record_chain_submission()` wiring — observes stalled sections without
  introducing graph ownership
- `src/coordination/engine/plan_executor.py` — coordination outputs resolve
  blockers through artifacts/signals that the orchestrator later re-checks

**Conformance**: Blocked items must be re-checked from authoritative state on
subsequent passes. Unblock checks must be bounded and idempotent. Dependency
graphs, subscription tables, or remembered in-memory edges may not become the
source of truth for waking blocked work.

---

## Health Notes

- **PAT-0001 (Corruption Preservation)**: Healthy. R114 migrated the last
  known bypass (`scan/service/scan_dispatch_config.py`) from local
  `json.loads()` to `Services.artifact_io().read_json()`. All known
  authoritative JSON readers now use shared corruption preservation primitives.
- **PAT-0002 (Prompt Safety)**: Healthy. R109 clarified that payload-file
  contents are untrusted dynamic content even when delivered through internal
  tasks. QA interceptor now validates payload content before dispatch.
- **PAT-0003 (Path Registry)**: Healthy at runtime. R121 added the
  remaining rule-11 helper surfaces (scope-delta, input-ref,
  proposal-attempt, research-question, recurrence, section-spec/proposal,
  scoped evidence) and migrated consumers atomically. Remaining exceptions are
  by-design: `decisions.py` receives its directory from callers, and flow
  relpath helpers remain for DB storage.
- **PAT-0004 (Flow System)**: Healthy. Per-section state-machine
  orchestration now sits above the flow primitives; reactive coordination and
  post-implementation verification/testing still route through flow submission
  instead of ad hoc controller loops.
- **PAT-0005 (Policy-Driven Models)**: Healthy. Runtime callsites resolve
  models centrally or use the required-key contract.
  `src/scan/related/related_file_resolver.py` now uses `resolve_model(...)`,
  and `tests/component/test_positive_contracts.py` fails if operational
  callsites reintroduce local `policy.get(...)` fallback chains.
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
  numbered template items as individual array entries. Bootstrap entry
  classification and PRD-derived governance seeding now feed packet relevance
  without changing the packet contract or broadening packet scope.
- **PAT-0012 (Post-Implementation Governance Feedback)**: Healthy. The
  post-implementation path now combines assessment with structural
  verification / behavioral testing gates and conservative verdict synthesis.
  Debt promotion remains idempotent with material-payload-aware dedup (R105).
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
- **PAT-0015 (Positive Contract Testing)**: Improved but still incomplete.
  The suite now covers doctrine projection, archive reference integrity,
  system-synthesis count truth, bounded `Services` allowlisting, and
  PAT-0005 callsite centralization. The remaining gap is rule-13 coverage:
  no positive contract yet derives a live governance/self-report inventory and
  compares it to the published summary surface that claims to describe it.
- **PAT-0016 (Runtime Inventory Truth & Surface Retirement)**: Improved.
  Live registry counts and governance summaries have been refreshed to the
  current state-machine / verification / testing architecture (58 agent files /
  68 task types / 15 namespaces; 23 problems / 21 patterns). The remaining
  risk is future summary drift, not a known live inventory mismatch.
- **PAT-0017 (Proposal-State Contract Projection)**: Improved. R115 rolled
  back the three ungoverned required fields (`constraint_ids`,
  `governance_candidate_refs`, `design_decision_refs`) from the canonical
  schema, fail-closed default, and all test/eval fixtures. The runtime schema
  now matches the active agent file, dispatch template, and known eval
  surfaces, but PAT-0015 still lacks the positive projection locks that would
  keep summary drift from reappearing elsewhere.
- **PAT-0018 (Behavioral Doctrine Projection)**: Healthy. R116 synchronized
  10 live agent/template doctrine surfaces. R117 fixed the 11th surface
  (microstrategy-writer.md, missed in R116) and added it to the known
  instances and positive contract test list. All 11 live routed doctrine
  surfaces now match the authoritative wording in SKILL.md and implement.md.
- **PAT-0019 (Constructor Dependency Injection / Composition-Root Boundary)**:
  Partially converged with bounded accepted residue. Constructor fallbacks are
  eliminated. Live `Services` imports are limited to sanctioned composition
  roots/build helpers plus quarantined residue in staleness services,
  `section_dispatcher.py`, `task_request_ingestor.py`, and the signals
  compatibility wrappers. Remaining work is limited to extracting runtime
  method lookups from staleness services and retiring backward-compat wrappers
  when compatibility is no longer required.

- **PAT-0020 (Transition Table as Data)**: Healthy. Section orchestration is
  now driven by an authoritative transition table with persisted state/history;
  retry and backtracking behavior lives in data instead of controller loops.
- **PAT-0021 (Poll and Check Unblock)**: Healthy. Blocked sections are
  re-checked each orchestration pass from authoritative artifacts/signals;
  starvation detection and readiness overlays reduce false stalls without
  introducing a dependency graph.
