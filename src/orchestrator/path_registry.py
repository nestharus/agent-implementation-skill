"""PathRegistry: Centralized artifact path construction.

Foundational service (Tier 1). No domain knowledge beyond directory layout.
Initialized with a planspace Path, provides typed accessors for all known
artifact locations. Replaces 142+ ad-hoc path constructions.
"""

from __future__ import annotations

from pathlib import Path


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

    # --- Directory accessors ---

    def sections_dir(self) -> Path:
        return self._artifacts / "sections"

    def proposals_dir(self) -> Path:
        return self._artifacts / "proposals"

    def signals_dir(self) -> Path:
        return self._artifacts / "signals"

    def notes_dir(self) -> Path:
        return self._artifacts / "notes"

    def decisions_dir(self) -> Path:
        return self._artifacts / "decisions"

    def todos_dir(self) -> Path:
        return self._artifacts / "todos"

    def readiness_dir(self) -> Path:
        return self._artifacts / "readiness"

    def coordination_dir(self) -> Path:
        return self._artifacts / "coordination"

    def reconciliation_dir(self) -> Path:
        return self._artifacts / "reconciliation"

    def scope_deltas_dir(self) -> Path:
        return self._artifacts / "scope-deltas"

    def contracts_dir(self) -> Path:
        return self._artifacts / "contracts"

    def inputs_dir(self) -> Path:
        return self._artifacts / "inputs"

    def trace_dir(self) -> Path:
        return self._artifacts / "trace"

    def flows_dir(self) -> Path:
        return self._artifacts / "flows"

    def flow_context(self, task_id: int) -> Path:
        return self.flows_dir() / f"task-{task_id}-context.json"

    def flow_continuation(self, task_id: int) -> Path:
        return self.flows_dir() / f"task-{task_id}-continuation.json"

    def flow_result_manifest(self, task_id: int) -> Path:
        return self.flows_dir() / f"task-{task_id}-result.json"

    def flow_dispatch_prompt(self, task_id: int) -> Path:
        return self.flows_dir() / f"task-{task_id}-dispatch.md"

    def flow_gate_aggregate(self, gate_id: str) -> Path:
        return self.flows_dir() / f"{gate_id}-aggregate.json"

    def qa_intercepts_dir(self) -> Path:
        return self._artifacts / "qa-intercepts"

    def substrate_dir(self) -> Path:
        return self._artifacts / "substrate"

    def substrate_prompts_dir(self) -> Path:
        return self.substrate_dir() / "prompts"

    def intent_dir(self) -> Path:
        return self._artifacts / "intent"

    def intent_global_dir(self) -> Path:
        return self.intent_dir() / "global"

    def intent_sections_dir(self) -> Path:
        return self.intent_dir() / "sections"

    def risk_dir(self) -> Path:
        return self._artifacts / "risk"

    def governance_dir(self) -> Path:
        return self._artifacts / "governance"

    def section_inputs_hashes_dir(self) -> Path:
        return self._artifacts / "section-inputs-hashes"

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

    def intent_section_dir(self, num: str) -> Path:
        return self.intent_sections_dir() / f"section-{num}"

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

    # --- Intake artifact accessors ---

    def intake_dir(self) -> Path:
        return self._artifacts / "intake"

    def intake_session_dir(self, session_id: str) -> Path:
        return self.intake_dir() / "sessions" / session_id

    def source_inventory(self, session_id: str) -> Path:
        return self.intake_session_dir(session_id) / "source-inventory.json"

    def candidate_claims(self, session_id: str) -> Path:
        return self.intake_session_dir(session_id) / "candidate-claims.json"

    def hypothesis_sets(self, session_id: str) -> Path:
        return self.intake_session_dir(session_id) / "hypothesis-sets.json"

    def verification_packet_json(self, session_id: str) -> Path:
        return self.intake_session_dir(session_id) / "verification-packet.json"

    def verification_packet_md(self, session_id: str) -> Path:
        return self.intake_session_dir(session_id) / "verification-packet.md"

    def verification_receipts(self) -> Path:
        return self.intake_dir() / "verification-receipts.jsonl"

    # --- Value scale and stack eval accessors ---

    def value_scales(self, scope: str) -> Path:
        return self.risk_dir() / f"{scope}-value-scales.json"

    def stack_eval(self, scope: str) -> Path:
        return self.risk_dir() / f"{scope}-stack-eval.json"

    # --- Research artifact accessors ---

    def research_dir(self) -> Path:
        return self._artifacts / "research"

    def research_sections_dir(self) -> Path:
        return self.research_dir() / "sections"

    def research_global_dir(self) -> Path:
        return self.research_dir() / "global"

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

    def task_request_signal(self, type_: str, num: str) -> Path:
        return self.signals_dir() / f"task-requests-{type_}-{num}.json"

    def tools_available(self, num: str) -> Path:
        return self.sections_dir() / f"section-{num}-tools-available.md"

    # --- Decision artifact accessors ---

    def decision_md(self, num: str) -> Path:
        return self.decisions_dir() / f"section-{num}.md"

    def decision_json(self, num: str) -> Path:
        return self.decisions_dir() / f"section-{num}.json"

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

    def coordination_signals_dir(self) -> Path:
        return self.coordination_dir() / "signals"

    def coordination_model_escalation(self) -> Path:
        return self.coordination_dir() / "model-escalation.txt"

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

    def snapshots_dir(self) -> Path:
        return self._artifacts / "snapshots"

    def snapshot_section(self, num: str) -> Path:
        return self.snapshots_dir() / f"section-{num}"

    def scan_logs_dir(self) -> Path:
        return self._artifacts / "scan-logs"

    def open_problems_dir(self) -> Path:
        return self._artifacts / "open-problems"

    def triage_dir(self) -> Path:
        return self._artifacts / "triage"

    # --- Global file accessors ---

    def codemap(self) -> Path:
        return self._artifacts / "codemap.md"

    def codemap_fingerprint(self) -> Path:
        return self._artifacts / "codemap.codespace.fingerprint"

    def corrections(self) -> Path:
        return self.signals_dir() / "codemap-corrections.json"

    def tool_registry(self) -> Path:
        return self._artifacts / "tool-registry.json"

    def tool_digest(self) -> Path:
        return self._artifacts / "tool-digest.md"

    def project_mode_json(self) -> Path:
        return self.signals_dir() / "project-mode.json"

    def project_mode_txt(self) -> Path:
        return self._artifacts / "project-mode.txt"

    def mode_contract(self) -> Path:
        return self._artifacts / "mode-contract.json"

    def model_policy(self) -> Path:
        return self._artifacts / "model-policy.json"

    def strategic_state(self) -> Path:
        return self._artifacts / "strategic-state.json"

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
