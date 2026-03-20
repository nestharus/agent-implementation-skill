"""PathRegistry: Centralized artifact path construction.

Foundational service (Tier 1). No domain knowledge beyond directory layout.
Initialized with a planspace Path, provides typed accessors for all known
artifact locations. Replaces 142+ ad-hoc path constructions.

Directory accessors decorated with ``@_artifact_dir`` are automatically
included in ``ensure_artifacts_tree()``.  Adding a new directory accessor
requires only the decorator — no manual list maintenance.
"""

from __future__ import annotations

from pathlib import Path


def _artifact_dir(fn):
    """Mark a method as an artifact directory to be created at startup."""
    fn._is_artifact_dir = True
    return fn


class PathRegistry:
    """Single source of truth for artifact directory layout."""

    def __init__(self, planspace: Path) -> None:
        self._planspace = planspace
        self._artifacts = planspace / "artifacts"

    @property
    def planspace(self) -> Path:
        return self._planspace

    @property
    def artifacts(self) -> Path:
        return self._artifacts

    def ensure_artifacts_tree(self) -> None:
        """Create all ``@_artifact_dir``-decorated directories.

        Call once during pipeline initialization to eliminate the need
        for every downstream function to defensively ``mkdir(parents=True)``.
        """
        for name in dir(self):
            method = getattr(type(self), name, None)
            if callable(method) and getattr(method, "_is_artifact_dir", False):
                getattr(self, name)().mkdir(parents=True, exist_ok=True)

    # --- Directory accessors ---

    @_artifact_dir
    def sections_dir(self) -> Path:
        return self._artifacts / "sections"

    @_artifact_dir
    def proposals_dir(self) -> Path:
        return self._artifacts / "proposals"

    @_artifact_dir
    def signals_dir(self) -> Path:
        return self._artifacts / "signals"

    @_artifact_dir
    def notes_dir(self) -> Path:
        return self._artifacts / "notes"

    @_artifact_dir
    def decisions_dir(self) -> Path:
        return self._artifacts / "decisions"

    @_artifact_dir
    def todos_dir(self) -> Path:
        return self._artifacts / "todos"

    @_artifact_dir
    def readiness_dir(self) -> Path:
        return self._artifacts / "readiness"

    @_artifact_dir
    def coordination_dir(self) -> Path:
        return self._artifacts / "coordination"

    @_artifact_dir
    def reconciliation_dir(self) -> Path:
        return self._artifacts / "reconciliation"

    @_artifact_dir
    def reconciliation_requests_dir(self) -> Path:
        return self._artifacts / "reconciliation-requests"

    def reconciliation_request(self, section_number: str) -> Path:
        return self.reconciliation_requests_dir() / f"section-{section_number}-reconciliation.json"

    def reconciliation_summary(self) -> Path:
        return self.reconciliation_dir() / "reconciliation-summary.json"

    @_artifact_dir
    def scope_deltas_dir(self) -> Path:
        return self._artifacts / "scope-deltas"

    def scope_delta_section(self, num: str) -> Path:
        return self.scope_deltas_dir() / f"section-{num}-scope-delta.json"

    def scope_delta_candidate(self, num: str, cand_hash: str) -> Path:
        return self.scope_deltas_dir() / f"section-{num}-candidate-{cand_hash}-scope-delta.json"

    def scope_delta_reconciliation(self, sources: str, title_slug: str) -> Path:
        return self.scope_deltas_dir() / f"reconciliation-{sources}-{title_slug}.json"

    @_artifact_dir
    def contracts_dir(self) -> Path:
        return self._artifacts / "contracts"

    @_artifact_dir
    def inputs_dir(self) -> Path:
        return self._artifacts / "inputs"

    @_artifact_dir
    def trace_dir(self) -> Path:
        return self._artifacts / "trace"

    @_artifact_dir
    def flows_dir(self) -> Path:
        return self._artifacts / "flows"

    def flow_context(self, task_id: int) -> Path:
        return self.flows_dir() / f"task-{task_id}-context.json"

    def flow_dispatch_prompt(self, task_id: int) -> Path:
        return self.flows_dir() / f"task-{task_id}-dispatch.md"

    def flow_gate_aggregate(self, gate_id: str) -> Path:
        return self.flows_dir() / f"{gate_id}-aggregate.json"

    @_artifact_dir
    def qa_intercepts_dir(self) -> Path:
        return self._artifacts / "qa-intercepts"

    @_artifact_dir
    def substrate_dir(self) -> Path:
        return self._artifacts / "substrate"

    @_artifact_dir
    def substrate_prompts_dir(self) -> Path:
        return self.substrate_dir() / "prompts"

    def substrate_status(self) -> Path:
        return self.substrate_dir() / "status.json"

    def substrate_seed_plan(self) -> Path:
        return self.substrate_dir() / "seed-plan.json"

    def substrate_shard(self, section_number: str) -> Path:
        return self.substrate_dir() / "shards" / f"shard-{section_number}.json"

    @_artifact_dir
    def intent_dir(self) -> Path:
        return self._artifacts / "intent"

    @_artifact_dir
    def intent_global_dir(self) -> Path:
        return self.intent_dir() / "global"

    @_artifact_dir
    def intent_sections_dir(self) -> Path:
        return self.intent_dir() / "sections"

    @_artifact_dir
    def risk_dir(self) -> Path:
        return self._artifacts / "risk"

    @_artifact_dir
    def governance_dir(self) -> Path:
        return self._artifacts / "governance"

    @_artifact_dir
    def section_inputs_hashes_dir(self) -> Path:
        return self._artifacts / "section-inputs-hashes"

    @_artifact_dir
    def phase2_inputs_hashes_dir(self) -> Path:
        return self._artifacts / "phase2-inputs-hashes"

    def related_files_update_dir(self) -> Path:
        """Substrate-stage related-files update signals directory."""
        return self.signals_dir() / "related-files-update"

    def scan_related_files_update_signal(self, section_name: str) -> Path:
        """Scan-stage related-files update signal for a section."""
        return self.signals_dir() / f"{section_name}-related-files-update.json"

    # --- Section-scoped file accessors ---

    def section_spec(self, num: str) -> Path:
        return self.sections_dir() / f"section-{num}.md"

    def proposal(self, num: str) -> Path:
        return self.proposals_dir() / f"section-{num}-integration-proposal.md"

    def proposal_excerpt(self, num: str) -> Path:
        return self.sections_dir() / f"section-{num}-proposal-excerpt.md"

    def alignment_excerpt(self, num: str) -> Path:
        return self.sections_dir() / f"section-{num}-alignment-excerpt.md"

    def microstrategy(self, num: str) -> Path:
        return self.proposals_dir() / f"section-{num}-microstrategy.md"

    def problem_frame(self, num: str) -> Path:
        return self.sections_dir() / f"section-{num}-problem-frame.md"

    def cycle_budget(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-cycle-budget.json"

    def mode_signal(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-mode.json"

    def section_mode_txt(self, num: str) -> Path:
        return self.sections_dir() / f"section-{num}-mode.txt"

    def blocker_signal(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-blocker.json"

    def microstrategy_signal(self, num: str) -> Path:
        return self.signals_dir() / f"proposal-{num}-microstrategy.json"

    def impl_feedback_surfaces(self, num: str) -> Path:
        return self.signals_dir() / f"impl-feedback-surfaces-{num}.json"

    def todos(self, num: str) -> Path:
        return self.todos_dir() / f"section-{num}-todos.md"

    def trace_map(self, num: str) -> Path:
        return self._artifacts / "trace-map" / f"section-{num}.json"

    def impl_modified(self, num: str) -> Path:
        return self._artifacts / f"impl-{num}-modified.txt"

    def input_refs_dir(self, num: str) -> Path:
        return self.inputs_dir() / f"section-{num}"

    def risk_accepted_steps(self, num: str) -> Path:
        return self.input_refs_dir(num) / f"section-{num}-risk-accepted-steps.json"

    def risk_deferred(self, num: str) -> Path:
        return self.input_refs_dir(num) / f"section-{num}-risk-deferred.json"

    def modified_file_manifest(self, num: str) -> Path:
        return self.input_refs_dir(num) / f"section-{num}-modified-file-manifest.json"

    def intent_section_dir(self, num: str) -> Path:
        return self.intent_sections_dir() / f"section-{num}"

    def proposal_history(self, num: str) -> Path:
        return self.intent_section_dir(num) / "proposal-history.md"

    def section_input_hash(self, num: str) -> Path:
        return self.section_inputs_hashes_dir() / f"{num}.hash"

    def phase2_input_hash(self, num: str) -> Path:
        return self.phase2_inputs_hashes_dir() / f"{num}.hash"

    def governance_problem_index(self) -> Path:
        return self.governance_dir() / "problem-index.json"

    def governance_pattern_index(self) -> Path:
        return self.governance_dir() / "pattern-index.json"

    def governance_profile_index(self) -> Path:
        return self.governance_dir() / "profile-index.json"

    def governance_region_profile_map(self) -> Path:
        return self.governance_dir() / "region-profile-map.json"

    def governance_constraint_index(self) -> Path:
        return self.governance_dir() / "constraint-index.json"

    def governance_packet(self, section_number: str) -> Path:
        return (
            self.governance_dir()
            / f"section-{section_number}-governance-packet.json"
        )

    def post_impl_assessment(self, section_number: str) -> Path:
        return (
            self.governance_dir()
            / f"section-{section_number}-post-impl-assessment.json"
        )

    def post_impl_assessment_prompt(self, section_number: str) -> Path:
        return self._artifacts / f"post-impl-{section_number}-prompt.md"

    def post_impl_blocker_signal(self, section_number: str) -> Path:
        return (
            self.signals_dir()
            / f"section-{section_number}-post-impl-blocker.json"
        )

    def risk_register_signal(self, section_number: str) -> Path:
        return (
            self.signals_dir()
            / f"section-{section_number}-risk-register-signal.json"
        )

    def risk_register_staging(self) -> Path:
        return self._artifacts / "risk-register-staging.json"

    # --- Risk artifact accessors ---

    def risk_package(self, scope: str) -> Path:
        return self.risk_dir() / f"{scope}-risk-package.json"

    def risk_assessment(self, scope: str) -> Path:
        return self.risk_dir() / f"{scope}-risk-assessment.json"

    def risk_plan(self, scope: str) -> Path:
        return self.risk_dir() / f"{scope}-risk-plan.json"

    def risk_history(self) -> Path:
        return self.risk_dir() / "risk-history.jsonl"

    def risk_summary(self, scope: str) -> Path:
        return self.risk_dir() / f"{scope}-risk-summary.md"

    def risk_parameters(self) -> Path:
        return self.risk_dir() / "risk-parameters.json"

    # --- Research artifact accessors ---

    @_artifact_dir
    def research_dir(self) -> Path:
        return self._artifacts / "research"

    @_artifact_dir
    def research_sections_dir(self) -> Path:
        return self.research_dir() / "sections"

    @_artifact_dir
    def research_global_dir(self) -> Path:
        return self.research_dir() / "global"

    # --- Global-scope (bootstrap) artifact accessors ---

    @_artifact_dir
    def global_problems_dir(self) -> Path:
        return self._artifacts / "global" / "problems"

    @_artifact_dir
    def global_values_dir(self) -> Path:
        return self._artifacts / "global" / "values"

    @_artifact_dir
    def global_research_dir(self) -> Path:
        return self._artifacts / "global" / "research"

    def global_research_status(self) -> Path:
        return self.global_research_dir() / "research-status.json"

    def global_research_dossier(self) -> Path:
        return self.global_research_dir() / "research-dossier.json"

    def bootstrap_execution_log(self) -> Path:
        """Points to run.db; the bootstrap execution log is a table within it."""
        return self.run_db()

    def research_section_dir(self, num: str) -> Path:
        return self.research_sections_dir() / f"section-{num}"

    def research_plan(self, num: str) -> Path:
        return self.research_section_dir(num) / "research-plan.json"

    def research_trigger(self, num: str) -> Path:
        return self.research_section_dir(num) / "research-trigger.json"

    def research_status(self, num: str) -> Path:
        return self.research_section_dir(num) / "research-status.json"

    def research_dossier(self, num: str) -> Path:
        return self.research_section_dir(num) / "dossier.md"

    def research_claims(self, num: str) -> Path:
        return self.research_section_dir(num) / "dossier-claims.json"

    def research_derived_surfaces(self, num: str) -> Path:
        return self.research_section_dir(num) / "research-derived-surfaces.json"

    def research_addendum(self, num: str) -> Path:
        return self.research_section_dir(num) / "proposal-addendum.md"

    def research_verify_report(self, num: str) -> Path:
        return self.research_section_dir(num) / "research-verify.json"

    def research_tickets_dir(self, num: str) -> Path:
        return self.research_section_dir(num) / "tickets"

    def research_plan_prompt(self, num: str) -> Path:
        return self._artifacts / f"research-plan-{num}-prompt.md"

    def research_synthesis_prompt(self, num: str) -> Path:
        return self._artifacts / f"research-synthesis-{num}-prompt.md"

    def research_verify_prompt(self, num: str) -> Path:
        return self._artifacts / f"research-verify-{num}-prompt.md"

    def research_ticket_spec(
        self,
        num: str,
        ticket_index: int,
        phase: str = "",
    ) -> Path:
        suffix = f"-{phase}" if phase else ""
        return (
            self.research_tickets_dir(num)
            / f"ticket-{ticket_index:02d}{suffix}-spec.json"
        )

    def research_ticket_prompt(
        self,
        num: str,
        ticket_index: int,
        phase: str = "",
    ) -> Path:
        suffix = f"-{phase}" if phase else ""
        return (
            self.research_tickets_dir(num)
            / f"ticket-{ticket_index:02d}{suffix}-prompt.md"
        )

    def research_ticket_result(
        self,
        num: str,
        ticket_index: int,
        phase: str = "",
    ) -> Path:
        suffix = f"-{phase}" if phase else ""
        return (
            self.research_tickets_dir(num)
            / f"ticket-{ticket_index:02d}{suffix}-result.json"
        )

    def research_scan_prompt(self, num: str, ticket_index: int) -> Path:
        return self.research_tickets_dir(num) / f"ticket-{ticket_index:02d}-scan-prompt.md"

    def proposal_state(self, num: str) -> Path:
        return self.proposals_dir() / f"section-{num}-proposal-state.json"

    def reconciliation_result(self, num: str) -> Path:
        return self.reconciliation_dir() / f"section-{num}-reconciliation-result.json"

    def execution_ready(self, num: str) -> Path:
        return self.readiness_dir() / f"section-{num}-execution-ready.json"

    def intent_surfaces_signal(self, num: str) -> Path:
        return self.signals_dir() / f"intent-surfaces-{num}.json"

    def impl_budget_exhausted_signal(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-impl-budget-exhausted.json"

    def setup_signal(self, num: str) -> Path:
        return self.signals_dir() / f"setup-{num}-signal.json"

    def impl_signal(self, num: str) -> Path:
        return self.signals_dir() / f"impl-{num}-signal.json"

    def proposal_signal(self, num: str) -> Path:
        return self.signals_dir() / f"proposal-{num}-signal.json"

    def problem_frame_hash(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-problem-frame-hash.txt"

    def tool_friction_signal(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-tool-friction.json"

    def bridge_tools_failure_signal(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-bridge-tools-failure.json"

    def tool_bridge_signal(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-tool-bridge.json"

    def intent_escalation_signal(self, num: str) -> Path:
        return self.signals_dir() / f"intent-escalation-{num}.json"

    def intent_stalled_signal(self, num: str) -> Path:
        return self.signals_dir() / f"intent-stalled-{num}.json"

    def intent_delta_signal(self, num: str) -> Path:
        return self.signals_dir() / f"intent-delta-{num}.json"

    def triage_signal(self, num: str) -> Path:
        return self.signals_dir() / f"triage-{num}.json"

    def note_ack_signal(self, num: str) -> Path:
        return self.signals_dir() / f"note-ack-{num}.json"

    def microstrategy_blocker_signal(self, num: str) -> Path:
        return self.signals_dir() / f"microstrategy-blocker-{num}.json"

    def recurrence_signal(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-recurrence.json"

    def related_files_signal(self, num: str) -> Path:
        return self.signals_dir() / f"related-files-{num}.json"

    def task_request_signal(self, type_: str, num: str) -> Path:
        return self.signals_dir() / f"task-requests-{type_}-{num}.json"

    def tools_available(self, num: str) -> Path:
        return self.sections_dir() / f"section-{num}-tools-available.md"

    # --- Decision artifact accessors ---

    def decision_md(self, num: str) -> Path:
        return self.decisions_dir() / f"section-{num}.md"

    def decision_json(self, num: str) -> Path:
        return self.decisions_dir() / f"section-{num}.json"

    def global_decision_json(self) -> Path:
        return self.decisions_dir() / "global.json"

    # --- Governance helper accessors ---

    def governance_synthesis_cues(self) -> Path:
        return self.governance_dir() / "synthesis-cues.json"

    def governance_index_status(self) -> Path:
        return self.governance_dir() / "index-status.json"

    # --- Trace index accessor ---

    def trace_index(self, num: str) -> Path:
        return self.trace_dir() / f"section-{num}.json"

    # --- Intent-triage accessors ---

    def intent_triage_signal(self, num: str) -> Path:
        return self.signals_dir() / f"intent-triage-{num}.json"

    def intent_triage_prompt(self, num: str) -> Path:
        return self._artifacts / f"intent-triage-{num}-prompt.md"

    def intent_triage_output(self, num: str) -> Path:
        return self._artifacts / f"intent-triage-{num}-output.md"

    # --- Coordination artifact accessors ---

    @_artifact_dir
    def coordination_signals_dir(self) -> Path:
        return self.coordination_dir() / "signals"

    def coordination_model_escalation(self) -> Path:
        return self.coordination_dir() / "model-escalation.txt"

    def coordination_recurrence(self) -> Path:
        return self.coordination_dir() / "recurrence.json"

    def coordination_problems(self) -> Path:
        return self.coordination_dir() / "problems.json"

    def coordination_fix_prompt(self, group_id: int) -> Path:
        return self.coordination_dir() / f"fix-{group_id}-prompt.md"

    def coordination_fix_modified(self, group_id: int) -> Path:
        return self.coordination_dir() / f"fix-{group_id}-modified.txt"

    def coordination_task_request(self, group_id: int) -> Path:
        return self.coordination_signals_dir() / f"task-requests-coord-{group_id}.json"

    def coordination_bridge_prompt(self, group_index: int) -> Path:
        return self.coordination_dir() / f"bridge-{group_index}-prompt.md"

    def coordination_bridge_output(self, group_index: int) -> Path:
        return self.coordination_dir() / f"bridge-{group_index}-output.md"

    def coordination_contract_patch(self, group_index: int) -> Path:
        return self.coordination_dir() / f"contract-patch-{group_index}.md"

    def coordination_align_signal(self, num: str) -> Path:
        return self.coordination_signals_dir() / f"coord-align-{num}-signal.json"

    def coordination_align_output(self, num: str) -> Path:
        return self.coordination_signals_dir() / f"coord-align-{num}-output.md"

    def scaffold_assignments(self) -> Path:
        return self.coordination_signals_dir() / "scaffold-assignments.json"

    # --- Bridge-tools accessors ---

    def bridge_tools_prompt(self, num: str) -> Path:
        return self._artifacts / f"bridge-tools-{num}-prompt.md"

    def bridge_tools_output(self, num: str) -> Path:
        return self._artifacts / f"bridge-tools-{num}-output.md"

    def bridge_tools_escalation_output(self, num: str) -> Path:
        return self._artifacts / f"bridge-tools-{num}-escalation-output.md"

    def alignment_surface(self, num: str) -> Path:
        return self.sections_dir() / f"section-{num}-alignment-surface.md"

    def tool_bridge_proposal(self, num: str) -> Path:
        return self.proposals_dir() / f"section-{num}-tool-bridge.md"

    def philosophy(self) -> Path:
        return self.intent_global_dir() / "philosophy.md"

    def philosophy_decisions(self) -> Path:
        return self.intent_global_dir() / "philosophy-decisions.md"

    # --- Directory accessors (additional) ---

    @_artifact_dir
    def snapshots_dir(self) -> Path:
        return self._artifacts / "snapshots"

    def snapshot_section(self, num: str) -> Path:
        return self.snapshots_dir() / f"section-{num}"

    @_artifact_dir
    def scan_logs_dir(self) -> Path:
        return self._artifacts / "scan-logs"

    @_artifact_dir
    def bootstrap_logs_dir(self) -> Path:
        return self._artifacts / "bootstrap-logs"

    @_artifact_dir
    def open_problems_dir(self) -> Path:
        return self._artifacts / "open-problems"

    def research_questions_artifact(self, num: str) -> Path:
        return self.open_problems_dir() / f"section-{num}-research-questions.json"

    @_artifact_dir
    def triage_dir(self) -> Path:
        return self._artifacts / "triage"

    # --- Global file accessors ---

    def codemap(self) -> Path:
        return self._artifacts / "codemap.md"

    def codemap_fingerprint(self) -> Path:
        return self._artifacts / "codemap.codespace.fingerprint"

    # --- Hierarchical codemap accessors ---

    @_artifact_dir
    def codemap_modules_dir(self) -> Path:
        return self._artifacts / "module-maps"

    def codemap_module(self, module_name: str) -> Path:
        return self.codemap_modules_dir() / f"{module_name}.md"

    # --- Continuous exploration codemap fragment accessors ---

    @_artifact_dir
    def codemap_fragments_dir(self) -> Path:
        return self._artifacts / "codemap-fragments"

    def section_codemap(self, num: str) -> Path:
        return self.codemap_fragments_dir() / f"section-{num}-codemap.md"

    def codemap_delta(self, num: str) -> Path:
        return self.codemap_fragments_dir() / f"section-{num}-codemap-delta.json"

    def codemap_refine_signal(self, section: str) -> Path:
        return self.signals_dir() / f"codemap-refine-{section}.json"

    def corrections(self) -> Path:
        return self.signals_dir() / "codemap-corrections.json"

    def tool_registry(self) -> Path:
        return self._artifacts / "tool-registry.json"

    def tool_digest(self) -> Path:
        return self._artifacts / "tool-digest.md"

    def project_mode_json(self) -> Path:
        return self.signals_dir() / "project-mode.json"

    def entry_classification_json(self) -> Path:
        return self.signals_dir() / "entry-classification.json"

    def project_mode_txt(self) -> Path:
        return self._artifacts / "project-mode.txt"

    def mode_contract(self) -> Path:
        return self._artifacts / "mode-contract.json"

    def model_policy(self) -> Path:
        return self._artifacts / "model-policy.json"

    def strategic_state(self) -> Path:
        return self._artifacts / "strategic-state.json"

    def proposal_results(self) -> Path:
        return self._artifacts / "proposal-results.json"

    def section_results(self) -> Path:
        return self._artifacts / "section-results.json"

    def parameters(self) -> Path:
        return self._artifacts / "parameters.json"

    def traceability(self) -> Path:
        return self._artifacts / "traceability.json"

    def context_sidecar(self, agent_stem: str) -> Path:
        return self._artifacts / f"context-{agent_stem}.json"

    def global_proposal(self) -> Path:
        return self._artifacts / "proposal.md"

    def global_alignment(self) -> Path:
        return self._artifacts / "alignment.md"

    def adjudicate_prompt(self) -> Path:
        return self._artifacts / "adjudicate-prompt.md"

    def adjudicate_output(self) -> Path:
        return self._artifacts / "adjudicate-output.md"

    def alignment_adjudicate_prompt(self) -> Path:
        return self._artifacts / "alignment-adjudicate-prompt.md"

    def alignment_adjudicate_output(self) -> Path:
        return self._artifacts / "alignment-adjudicate-output.md"

    # --- Philosophy artifact accessors ---

    def philosophy_bootstrap_guidance_prompt(self) -> Path:
        return self._artifacts / "philosophy-bootstrap-guidance-prompt.md"

    def philosophy_bootstrap_guidance_output(self) -> Path:
        return self._artifacts / "philosophy-bootstrap-guidance-output.md"

    def philosophy_candidate_catalog(self) -> Path:
        return self._artifacts / "philosophy-candidate-catalog.json"

    def philosophy_select_prompt(self) -> Path:
        return self._artifacts / "philosophy-select-prompt.md"

    def philosophy_select_output(self) -> Path:
        return self._artifacts / "philosophy-select-output.md"

    def philosophy_select_output_extensions(self) -> Path:
        return self._artifacts / "philosophy-select-output-extensions.md"

    def philosophy_verify_prompt(self) -> Path:
        return self._artifacts / "philosophy-verify-prompt.md"

    def philosophy_verify_output(self) -> Path:
        return self._artifacts / "philosophy-verify-output.md"

    def philosophy_distill_prompt(self) -> Path:
        return self._artifacts / "philosophy-distill-prompt.md"

    def philosophy_distill_output(self) -> Path:
        return self._artifacts / "philosophy-distill-output.md"

    def alignment_changed_flag(self) -> Path:
        return self._artifacts / "alignment-changed-pending"

    def run_db(self) -> Path:
        return self._planspace / "run.db"

    # --- Verification artifact accessors ---

    @_artifact_dir
    def verification_dir(self) -> Path:
        return self._artifacts / "verification"

    def verification_structural(self, section_number: str) -> Path:
        return self.verification_dir() / f"section-{section_number}-structural.json"

    def verification_integration(self, section_pair: str) -> Path:
        return self.verification_dir() / f"integration-{section_pair}.json"

    def verification_prompt(self, section_number: str, task_type: str) -> Path:
        return self._artifacts / f"verification-{task_type}-{section_number}-prompt.md"

    def proposal_history(self, section_number: str) -> Path:
        return self.intent_section_dir(section_number) / "proposal-history.md"

    def testing_results(self, section_number: str) -> Path:
        return self.verification_dir() / f"section-{section_number}-testing.json"

    def verification_status(self, section_number: str) -> Path:
        return self.verification_dir() / f"section-{section_number}-verification-status.json"

    def testing_rca_findings(self, section_number: str) -> Path:
        return self.verification_dir() / f"section-{section_number}-rca-findings.json"

    def verification_blocker_signal(self, section_number: str) -> Path:
        return (
            self.signals_dir()
            / f"section-{section_number}-verification-blocker.json"
        )

    def testing_blocker_signal(self, section_number: str) -> Path:
        return (
            self.signals_dir()
            / f"section-{section_number}-testing-blocker.json"
        )

    def verification_context(self, section_number: str, task_type: str) -> Path:
        return self.verification_dir() / f"section-{section_number}-{task_type}-context.json"

    # --- Reactive coordination signal accessors ---

    def root_reframe_signal(self) -> Path:
        """Global root-reframe signal (file-existence check)."""
        return self.signals_dir() / "root-reframe-active.json"

    def starvation_signal(self, section_number: str) -> Path:
        return self.signals_dir() / f"section-{section_number}-starvation.json"

    def section_chain_submission(self, section_number: str) -> Path:
        """Tracks the most recent task submission time for a section chain."""
        return self.signals_dir() / f"section-{section_number}-chain-submission.json"
