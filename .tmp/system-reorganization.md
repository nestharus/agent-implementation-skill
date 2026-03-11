# System Reorganization: File-by-File Mapping

## Principle

Each system **owns its data**. It exposes a public API surface. Other
systems interact through that API, never bypassing it to touch internal
state. Each system has its own `paths.py` defining artifact locations.

## Target Structure

```
src/
  dispatch/              # 1. Agent execution, model policy, prompts, tools
  signals/               # 2. Signal files, mailbox, DB client, blocker rollup
  flow/                  # 3. Task queue, flow declarations, routing, continuations
  staleness/             # 4. Freshness tokens, input hashes, alignment flags
  intent/                # 5. Problem definitions, philosophy, surfaces
  research/              # 6. Research triggers, dossiers, claims, status
  risk/                  # 7. ROAL packages, assessments, plans, history, postures
  proposal/              # 8. Proposal state, readiness, excerpts, problem frames
  reconciliation/        # 9. Reconciliation queue, results, conflict detection
  implementation/        # 10. Microstrategy, impl reports, snapshots, impact
  coordination/          # 11. Coordination state, consequence notes, cross-section
  intake/                # 12. Intake sessions, governance, verification packets
  scan/                  # 13. Codemap, related-files, tiers, project mode, substrate
  orchestrator/          # 14. Phase state, section lifecycle, decisions, main loop
  agents/                # Agent prompt files (stays)
```

---

## 1. DISPATCH (owns: agent execution, model resolution, prompts, QA gate)

Data owned:
- dispatch metadata (returncode, timed_out)
- model policy resolution
- prompt templates and safety validation
- tool registry/discovery
- QA interception state

```
lib/dispatch/agent_executor.py
lib/dispatch/context_sidecar.py
lib/dispatch/dispatch_helpers.py
lib/dispatch/dispatch_metadata.py
lib/dispatch/monitor_service.py
lib/core/model_policy.py
lib/prompts/prompt_template.py
lib/prompts/prompt_helpers.py
lib/prompts/prompt_context_assembler.py
lib/tools/tool_surface.py
lib/tools/log_extract_utils.py
prompt_safety.py
qa_interceptor.py
qa-harness.sh
section_loop/dispatch.py
section_loop/agent_templates.py
section_loop/prompts/context.py
section_loop/prompts/renderer.py
section_loop/prompts/writers.py
```

PathRegistry methods → dispatch/paths.py:
- model_policy()
- context_sidecar(agent_stem)
- tool_registry()
- tool_digest()
- qa_intercepts_dir()

---

## 2. SIGNALS (owns: signal files, mailbox messages, DB, blocker rollup, events)

Data owned:
- all signal JSON files (artifacts/signals/)
- mailbox messages (run.db messages table)
- lifecycle events (run.db events table)
- blocker rollup (needs-input.md)
- agent registry (run.db agents table)

```
lib/dispatch/mailbox_service.py
lib/dispatch/message_poller.py
lib/services/signal_reader.py
lib/core/database_client.py
lib/core/communication.py
lib/core/artifact_io.py              # JSON read/write protocol (used by all)
section_loop/communication.py
section_loop/section_engine/blockers.py
db.sh
```

PathRegistry methods → signals/paths.py:
- signals_dir()
- run_db()
- alignment_changed_flag()          # signal file, owned by signals
- blocker_signal(num)

Note: artifact_io.py lives here because signals owns the malformed-file
quarantine protocol. All systems import `read_json`/`write_json` from
signals.

---

## 3. FLOW (owns: task queue, flow declarations, routing, continuations)

Data owned:
- flow declarations (v1/v2 envelopes)
- task queue entries (run.db tasks table)
- flow context sidecars
- flow continuations and result manifests
- task routing table (task_type → agent_file + model)
- flow catalog (named chains)

```
lib/flow/flow_context.py
lib/flow/flow_submitter.py
lib/flow/flow_reconciler.py
lib/tasks/task_db_client.py
lib/tasks/task_ingestion.py
lib/tasks/task_notifier.py
lib/tasks/task_parser.py
flow_schema.py
flow_catalog.py
task_router.py
task_flow.py
task_dispatcher.py
section_loop/task_ingestion.py
```

PathRegistry methods → flow/paths.py:
- flows_dir()
- parameters()

---

## 4. STALENESS (owns: freshness tokens, input hashes, alignment flags, hash computation)

Data owned:
- freshness tokens (per-section, per-task)
- section input hashes
- phase2 input hashes
- alignment-changed flag lifecycle
- content/file hashes

```
lib/services/freshness_service.py
lib/services/section_input_hasher.py
lib/services/alignment_change_tracker.py
lib/services/alignment_service.py
lib/core/hash_service.py
lib/pipelines/global_alignment_recheck.py
section_loop/alignment.py
section_loop/change_detection.py
```

PathRegistry methods → staleness/paths.py:
- section_inputs_hashes_dir()
- section_input_hash(num)
- phase2_inputs_hashes_dir()
- phase2_input_hash(num)

---

## 5. INTENT (owns: problem definitions, philosophy, surfaces, registry)

Data owned:
- problem.md (per-section axes)
- problem-alignment.md (rubric)
- philosophy.md (global principles)
- philosophy-source-map.json
- surface registry (per-section dedup state)
- intent pack input hash
- intent triage signals
- intent delta signals
- recurrence signals

```
lib/intent/intent_bootstrap.py
lib/intent/intent_surface.py
lib/intent/intent_triage.py
lib/intent/philosophy_bootstrap.py
lib/pipelines/recurrence_emitter.py
section_loop/intent/bootstrap.py
section_loop/intent/expansion.py
section_loop/intent/surfaces.py
section_loop/intent/triage.py
```

PathRegistry methods → intent/paths.py:
- intent_dir()
- intent_global_dir()
- intent_sections_dir()
- intent_section_dir(num)
- intent_surfaces_signal(num)
- impl_feedback_surfaces(num)

---

## 6. RESEARCH (owns: research triggers, dossiers, claims, status, tickets)

Data owned:
- research-trigger.json (per-section)
- research-status.json (per-section)
- research-plan.json
- research dossier (markdown synthesis)
- research claims (structured JSON)
- research-derived surfaces
- research addendum
- research verification report
- research ticket specs, prompts, results

```
lib/research/orchestrator.py
lib/research/plan_executor.py
lib/research/prompt_writer.py
```

PathRegistry methods → research/paths.py:
- research_dir()
- research_sections_dir()
- research_global_dir()
- research_section_dir(num)
- research_plan(num)
- research_trigger(num)
- research_status(num)
- research_dossier(num)
- research_claims(num)
- research_derived_surfaces(num)
- research_addendum(num)
- research_verify_report(num)
- research_tickets_dir(num)
- research_plan_prompt(num)
- research_synthesis_prompt(num)
- research_verify_prompt(num)
- research_ticket_spec(num, idx)
- research_ticket_prompt(num, idx)
- research_ticket_result(num, idx)
- research_scan_prompt(num, idx)

---

## 7. RISK (owns: packages, assessments, plans, history, postures, values, stack evals)

Data owned:
- risk packages (per-scope)
- risk assessments (per-scope)
- risk plans (per-scope)
- risk history (append-only JSONL)
- risk summaries
- risk parameters
- value scales (per-scope)
- stack evaluations (per-scope)

```
lib/risk/types.py
lib/risk/engagement.py
lib/risk/history.py
lib/risk/loop.py
lib/risk/package_builder.py
lib/risk/posture.py
lib/risk/quantifier.py
lib/risk/serialization.py
lib/risk/stack_eval.py
lib/risk/threshold.py
lib/risk/value_scales.py
```

PathRegistry methods → risk/paths.py:
- risk_dir()
- risk_package(scope)
- risk_assessment(scope)
- risk_plan(scope)
- risk_history()
- risk_summary(scope)
- risk_parameters()
- value_scales(scope)
- stack_eval(scope)

---

## 8. PROPOSAL (owns: proposal state, readiness, excerpts, problem frames)

Data owned:
- proposal-state.json (per-section)
- execution-ready.json (per-section)
- proposal excerpts (per-section)
- alignment excerpts (per-section)
- problem frame (per-section)
- cycle budget (per-section)
- microstrategy signal (per-section)

```
lib/pipelines/proposal_pass.py
lib/pipelines/proposal_loop.py
lib/pipelines/readiness_gate.py
lib/pipelines/problem_frame_gate.py
lib/pipelines/excerpt_extractor.py
lib/repositories/proposal_state_repository.py
lib/repositories/excerpt_repository.py
lib/services/readiness_resolver.py
lib/services/qa_verdict_parser.py
lib/services/verdict_parsers.py
section_loop/proposal_state.py
section_loop/readiness.py
```

PathRegistry methods → proposal/paths.py:
- proposals_dir()
- readiness_dir()
- proposal(num)
- proposal_excerpt(num)
- alignment_excerpt(num)
- problem_frame(num)
- proposal_state(num)
- cycle_budget(num)
- microstrategy_signal(num)

---

## 9. RECONCILIATION (owns: reconciliation queue, results, conflict state)

Data owned:
- reconciliation queue entries
- reconciliation results (per-section)
- contract conflict detection state

```
lib/pipelines/reconciliation_phase.py
lib/pipelines/reconciliation_adjudicator.py
lib/repositories/reconciliation_queue.py
lib/repositories/reconciliation_result_repository.py
lib/services/reconciliation_detectors.py
section_loop/reconciliation.py
section_loop/reconciliation_queue.py
```

PathRegistry methods → reconciliation/paths.py:
- reconciliation_dir()
- contracts_dir()

---

## 10. IMPLEMENTATION (owns: microstrategy, impl reports, snapshots, impact, todos)

Data owned:
- microstrategy (per-section)
- implementation output / modified-file manifest
- file snapshots (before/after)
- impact analysis artifacts
- scope deltas
- TODOs extracted from code
- post-implementation assessment
- risk register staging

```
lib/pipelines/implementation_pass.py
lib/pipelines/implementation_loop.py
lib/pipelines/microstrategy_orchestrator.py
lib/pipelines/impact_triage.py
lib/pipelines/scope_delta_aggregator.py
lib/services/impact_analyzer.py
lib/services/scope_delta_parser.py
lib/services/snapshot_service.py
section_loop/section_engine/runner.py
section_loop/section_engine/reexplore.py
section_loop/section_engine/todos.py
section_loop/section_engine/traceability.py
```

PathRegistry methods → implementation/paths.py:
- microstrategy(num)
- todos_dir()
- todos(num)
- impl_modified(num)
- scope_deltas_dir()
- post_impl_assessment(num)
- post_impl_assessment_prompt(num)
- post_impl_blocker_signal(num)
- risk_register_signal(num)
- risk_register_staging()
- trace_dir()
- trace_map(num)

---

## 11. COORDINATION (owns: coordination state, consequence notes, cross-section problems)

Data owned:
- coordination round state
- consequence notes (from-X-to-Y.md)
- cross-section problem detection
- coordination fix prompts/outputs

```
lib/pipelines/coordination_loop.py
lib/pipelines/coordination_executor.py
lib/pipelines/coordination_planner.py
lib/pipelines/coordination_problem_resolver.py
lib/repositories/note_repository.py
section_loop/coordination/execution.py
section_loop/coordination/planning.py
section_loop/coordination/problems.py
section_loop/coordination/runner.py
section_loop/cross_section.py
```

PathRegistry methods → coordination/paths.py:
- coordination_dir()
- notes_dir()

---

## 12. INTAKE (owns: intake sessions, governance claims, verification packets, indexes)

Data owned:
- intake sessions (per-session state machine)
- source inventory
- candidate claims
- hypothesis sets
- verification packets (JSON + markdown)
- verification receipts
- governance indexes (problem, pattern, profile, constraint, region)
- governance packets (per-section)
- post-implementation governance assessment

```
lib/intake/types.py
lib/intake/session.py
lib/intake/verification.py
lib/governance/loader.py
lib/governance/assessment.py
lib/governance/packet.py
```

PathRegistry methods → intake/paths.py:
- intake_dir()
- intake_session_dir(session_id)
- source_inventory(session_id)
- candidate_claims(session_id)
- hypothesis_sets(session_id)
- verification_packet_json(session_id)
- verification_packet_md(session_id)
- verification_receipts()
- governance_dir()
- governance_problem_index()
- governance_pattern_index()
- governance_profile_index()
- governance_region_profile_map()
- governance_constraint_index()
- governance_packet(num)

---

## 13. SCAN (owns: codemap, related-files, tiers, project mode, substrate, section specs)

Data owned:
- codemap.md
- corrections.md
- per-section related files lists
- per-section tier files
- project mode signal (greenfield/brownfield)
- section mode signals
- substrate artifacts (shards, prune/seed signals, substrate.md)
- section spec files
- related-files update signals

```
lib/scan/deep_scan_analyzer.py
lib/scan/scan_dispatch.py
lib/scan/scan_feedback_router.py
lib/scan/scan_match_updater.py
lib/scan/scan_phase_logger.py
lib/scan/scan_related_files.py
lib/scan/scan_section_iterator.py
lib/scan/scan_template_loader.py
lib/scan/tier_ranking.py
lib/substrate/substrate_dispatch.py
lib/substrate/substrate_helpers.py
lib/substrate/substrate_policy.py
lib/prompts/substrate_prompt_builder.py
lib/sections/project_mode.py
lib/sections/section_loader.py
lib/sections/section_notes.py
scripts/scan/ (entire directory — CLI + codemap + exploration)
scripts/substrate/ (entire directory — runner + prompts + schemas)
scan.sh
substrate.sh
```

PathRegistry methods → scan/paths.py:
- sections_dir()
- section_spec(num)
- codemap()
- corrections()
- project_mode_json()
- project_mode_txt()
- mode_contract()
- mode_signal(num)
- section_mode_txt(num)
- substrate_dir()
- substrate_prompts_dir()
- related_files_update_dir()
- scan_related_files_update_signal(section_name)
- input_refs_dir(num)

---

## 14. ORCHESTRATOR (owns: phase state, section lifecycle, decisions, main loop)

Data owned:
- pipeline state (paused/running)
- section results (ProposalPassResult, aligned, etc.)
- section types (Section dataclass)
- decisions (per-section, per-scope)
- strategic state (accumulated decisions + outcomes)
- traceability entries
- context assembly for each phase

```
lib/core/pipeline_state.py
lib/repositories/decision_repository.py
lib/repositories/strategic_state.py
lib/sections/section_decisions.py
section_loop/main.py
section_loop/pipeline_control.py
section_loop/context_assembly.py
section_loop/types.py
section_loop/decisions.py
section-loop.py (entry point)
workflow.sh
```

PathRegistry methods → orchestrator/paths.py:
- planspace()
- artifacts()
- decisions_dir()
- inputs_dir()
- strategic_state()
- traceability()

---

## STANDALONE (not a system — separate CLI tool)

```
log_extract/    (entire directory — logex CLI)
```

---

## PathRegistry Decomposition

Current: 1 god class with ~100 methods
Target: each system has its own `paths.py` with a class or functions
         that take `planspace: Path` and return system-specific paths.

All path modules share the same pattern:
```python
class RiskPaths:
    def __init__(self, planspace: Path) -> None:
        self._root = planspace / "artifacts"

    def risk_dir(self) -> Path: ...
    def package(self, scope: str) -> Path: ...
    def assessment(self, scope: str) -> Path: ...
```

Systems import each other's path modules when they need to read
(not write) another system's artifacts. Writing goes through the
owning system's API.

---

## Dependency Direction (systems import from →)

```
orchestrator → proposal, reconciliation, implementation, coordination,
               staleness, signals, flow, dispatch, scan
proposal     → dispatch, signals, staleness, research, intent, risk,
               reconciliation, scan, intake
implementation → dispatch, signals, staleness, risk, proposal, scan,
                 coordination
coordination → dispatch, signals, proposal, implementation
reconciliation → signals, proposal
research     → dispatch, flow, signals, scan, intent
intent       → dispatch, signals, scan
risk         → dispatch, proposal, signals
scan         → dispatch, signals
intake       → signals
flow         → dispatch, signals, staleness
staleness    → signals
dispatch     → signals
signals      → (no system dependencies — leaf)
```
