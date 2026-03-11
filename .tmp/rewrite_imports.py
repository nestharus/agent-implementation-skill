"""Bulk import path rewriter for system reorganization."""

import re
from pathlib import Path

# Old module path → new module path
MAPPINGS = {
    # === SIGNALS ===
    "lib.core.artifact_io": "signals.artifact_io",
    "lib.core.database_client": "signals.database_client",
    "lib.core.communication": "signals.communication",
    "lib.dispatch.mailbox_service": "signals.mailbox_service",
    "lib.dispatch.message_poller": "signals.message_poller",
    "lib.services.signal_reader": "signals.signal_reader",

    # === DISPATCH ===
    "lib.dispatch.agent_executor": "dispatch.agent_executor",
    "lib.dispatch.context_sidecar": "dispatch.context_sidecar",
    "lib.dispatch.dispatch_helpers": "dispatch.dispatch_helpers",
    "lib.dispatch.dispatch_metadata": "dispatch.dispatch_metadata",
    "lib.dispatch.monitor_service": "dispatch.monitor_service",
    "lib.core.model_policy": "dispatch.model_policy",
    "lib.prompts.prompt_template": "dispatch.prompt_template",
    "lib.prompts.prompt_helpers": "dispatch.prompt_helpers",
    "lib.prompts.prompt_context_assembler": "dispatch.prompt_context_assembler",
    "lib.tools.tool_surface": "dispatch.tool_surface",
    "lib.tools.log_extract_utils": "dispatch.log_extract_utils",

    # === FLOW ===
    "lib.flow.flow_context": "flow.flow_context",
    "lib.flow.flow_submitter": "flow.flow_submitter",
    "lib.flow.flow_reconciler": "flow.flow_reconciler",
    "lib.tasks.task_db_client": "flow.task_db_client",
    "lib.tasks.task_ingestion": "flow.task_ingestion",
    "lib.tasks.task_notifier": "flow.task_notifier",
    "lib.tasks.task_parser": "flow.task_parser",

    # === STALENESS ===
    "lib.services.freshness_service": "staleness.freshness_service",
    "lib.services.section_input_hasher": "staleness.section_input_hasher",
    "lib.services.alignment_change_tracker": "staleness.alignment_change_tracker",
    "lib.services.alignment_service": "staleness.alignment_service",
    "lib.core.hash_service": "staleness.hash_service",
    "lib.pipelines.global_alignment_recheck": "staleness.global_alignment_recheck",

    # === INTENT ===
    "lib.intent.intent_bootstrap": "intent.intent_bootstrap",
    "lib.intent.intent_surface": "intent.intent_surface",
    "lib.intent.intent_triage": "intent.intent_triage",
    "lib.intent.philosophy_bootstrap": "intent.philosophy_bootstrap",
    "lib.pipelines.recurrence_emitter": "intent.recurrence_emitter",

    # === RESEARCH ===
    "lib.research.orchestrator": "research.orchestrator",
    "lib.research.plan_executor": "research.plan_executor",
    "lib.research.prompt_writer": "research.prompt_writer",

    # === RISK ===
    "lib.risk.types": "risk.types",
    "lib.risk.engagement": "risk.engagement",
    "lib.risk.history": "risk.history",
    "lib.risk.loop": "risk.loop",
    "lib.risk.package_builder": "risk.package_builder",
    "lib.risk.posture": "risk.posture",
    "lib.risk.quantifier": "risk.quantifier",
    "lib.risk.serialization": "risk.serialization",
    "lib.risk.stack_eval": "risk.stack_eval",
    "lib.risk.threshold": "risk.threshold",
    "lib.risk.value_scales": "risk.value_scales",

    # === PROPOSAL ===
    "lib.pipelines.proposal_pass": "proposal.proposal_pass",
    "lib.pipelines.proposal_loop": "proposal.proposal_loop",
    "lib.pipelines.readiness_gate": "proposal.readiness_gate",
    "lib.pipelines.problem_frame_gate": "proposal.problem_frame_gate",
    "lib.pipelines.excerpt_extractor": "proposal.excerpt_extractor",
    "lib.repositories.proposal_state_repository": "proposal.proposal_state_repository",
    "lib.repositories.excerpt_repository": "proposal.excerpt_repository",
    "lib.services.readiness_resolver": "proposal.readiness_resolver",
    "lib.services.qa_verdict_parser": "proposal.qa_verdict_parser",
    "lib.services.verdict_parsers": "proposal.verdict_parsers",

    # === RECONCILIATION ===
    "lib.pipelines.reconciliation_phase": "reconciliation.reconciliation_phase",
    "lib.pipelines.reconciliation_adjudicator": "reconciliation.reconciliation_adjudicator",
    "lib.repositories.reconciliation_queue": "reconciliation.reconciliation_queue",
    "lib.repositories.reconciliation_result_repository": "reconciliation.reconciliation_result_repository",
    "lib.services.reconciliation_detectors": "reconciliation.reconciliation_detectors",

    # === IMPLEMENTATION ===
    "lib.pipelines.implementation_pass": "implementation.implementation_pass",
    "lib.pipelines.implementation_loop": "implementation.implementation_loop",
    "lib.pipelines.microstrategy_orchestrator": "implementation.microstrategy_orchestrator",
    "lib.pipelines.impact_triage": "implementation.impact_triage",
    "lib.pipelines.scope_delta_aggregator": "implementation.scope_delta_aggregator",
    "lib.services.impact_analyzer": "implementation.impact_analyzer",
    "lib.services.scope_delta_parser": "implementation.scope_delta_parser",
    "lib.services.snapshot_service": "implementation.snapshot_service",

    # === COORDINATION ===
    "lib.pipelines.coordination_loop": "coordination.coordination_loop",
    "lib.pipelines.coordination_executor": "coordination.coordination_executor",
    "lib.pipelines.coordination_planner": "coordination.coordination_planner",
    "lib.pipelines.coordination_problem_resolver": "coordination.coordination_problem_resolver",
    "lib.repositories.note_repository": "coordination.note_repository",

    # === INTAKE ===
    "lib.intake.types": "intake.types",
    "lib.intake.session": "intake.session",
    "lib.intake.verification": "intake.verification",
    "lib.governance.loader": "intake.governance_loader",
    "lib.governance.assessment": "intake.governance_assessment",
    "lib.governance.packet": "intake.governance_packet",

    # === SCAN ===
    "lib.scan.deep_scan_analyzer": "scan.deep_scan_analyzer",
    "lib.scan.scan_dispatch": "scan.scan_dispatch",
    "lib.scan.scan_feedback_router": "scan.scan_feedback_router",
    "lib.scan.scan_match_updater": "scan.scan_match_updater",
    "lib.scan.scan_phase_logger": "scan.scan_phase_logger",
    "lib.scan.scan_related_files": "scan.scan_related_files",
    "lib.scan.scan_section_iterator": "scan.scan_section_iterator",
    "lib.scan.scan_template_loader": "scan.scan_template_loader",
    "lib.scan.tier_ranking": "scan.tier_ranking",
    "lib.substrate.substrate_dispatch": "scan.substrate_dispatch",
    "lib.substrate.substrate_helpers": "scan.substrate_helpers",
    "lib.substrate.substrate_policy": "scan.substrate_policy",
    "lib.prompts.substrate_prompt_builder": "scan.substrate_prompt_builder",
    "lib.sections.project_mode": "scan.project_mode",
    "lib.sections.section_loader": "scan.section_loader",
    "lib.sections.section_notes": "scan.section_notes",

    # === ORCHESTRATOR ===
    "lib.core.path_registry": "orchestrator.path_registry",
    "lib.core.pipeline_state": "orchestrator.pipeline_state",
    "lib.repositories.decision_repository": "orchestrator.decision_repository",
    "lib.repositories.strategic_state": "orchestrator.strategic_state",
    "lib.sections.section_decisions": "orchestrator.section_decisions",
}

# section_loop.* → new locations
SECTION_LOOP_MAPPINGS = {
    "section_loop.types": "orchestrator.types",
    "section_loop.dispatch": "dispatch.section_dispatch",
    "section_loop.communication": "signals.section_loop_communication",
    "section_loop.alignment": "staleness.section_alignment",
    "section_loop.change_detection": "staleness.change_detection",
    "section_loop.context_assembly": "orchestrator.context_assembly",
    "section_loop.decisions": "orchestrator.decisions",
    "section_loop.pipeline_control": "orchestrator.pipeline_control",
    "section_loop.main": "orchestrator.main",
    "section_loop.proposal_state": "proposal.loop_proposal_state",
    "section_loop.readiness": "proposal.loop_readiness",
    "section_loop.reconciliation_queue": "reconciliation.loop_reconciliation_queue",
    "section_loop.reconciliation": "reconciliation.loop_reconciliation",
    "section_loop.cross_section": "coordination.cross_section",
    "section_loop.task_ingestion": "flow.section_task_ingestion",
    "section_loop.agent_templates": "dispatch.agent_templates",
    # Subpackages
    "section_loop.coordination.execution": "coordination.loop_execution",
    "section_loop.coordination.planning": "coordination.loop_planning",
    "section_loop.coordination.problems": "coordination.loop_problems",
    "section_loop.coordination.runner": "coordination.loop_runner",
    "section_loop.intent.bootstrap": "intent.loop_bootstrap",
    "section_loop.intent.expansion": "intent.loop_expansion",
    "section_loop.intent.surfaces": "intent.loop_surfaces",
    "section_loop.intent.triage": "intent.loop_triage",
    "section_loop.prompts.context": "dispatch.prompts_context",
    "section_loop.prompts.renderer": "dispatch.prompts_renderer",
    "section_loop.prompts.writers": "dispatch.prompts_writers",
    "section_loop.section_engine.runner": "implementation.engine_runner",
    "section_loop.section_engine.reexplore": "implementation.engine_reexplore",
    "section_loop.section_engine.todos": "implementation.engine_todos",
    "section_loop.section_engine.traceability": "implementation.engine_traceability",
    "section_loop.section_engine.blockers": "signals.blockers",
}


def rewrite_file(path: Path) -> int:
    """Rewrite imports in a single file. Returns count of replacements."""
    text = path.read_text(encoding="utf-8")
    original = text
    count = 0

    # Sort by length descending to match longer paths first
    all_mappings = {**MAPPINGS, **SECTION_LOOP_MAPPINGS}
    for old, new in sorted(all_mappings.items(), key=lambda x: -len(x[0])):
        # Handle: from X import ... / import X
        old_escaped = re.escape(old)

        # "from old.module import ..." and "import old.module"
        for pattern in [
            rf"from {old_escaped}( import)",
            rf"(import ){old_escaped}",
        ]:
            replacement = text
            text = re.sub(pattern, lambda m: m.group(0).replace(old, new), text)
            if text != replacement:
                count += text.count(new) - replacement.count(new)

        # Also handle string references (monkeypatch, etc.)
        # "src.scripts.lib.X.Y" → "src.X.Y"  (test monkeypatches)
        old_test = f"src.scripts.{old}"
        new_test = f"src.{new}"
        if old_test in text:
            text = text.replace(old_test, new_test)
            count += 1

    # Handle section_loop string refs in tests
    for old, new in sorted(SECTION_LOOP_MAPPINGS.items(), key=lambda x: -len(x[0])):
        old_test = f"src.scripts.{old}"
        new_test = f"src.{new}"
        if old_test in text:
            text = text.replace(old_test, new_test)
            count += 1

    if text != original:
        path.write_text(text, encoding="utf-8")
    return count


def main():
    root = Path("/home/nes/projects/agent-implementation-skill")
    dirs = [root / "src", root / "tests", root / "evals"]

    total_files = 0
    total_replacements = 0

    for d in dirs:
        if not d.exists():
            continue
        for py in sorted(d.rglob("*.py")):
            if "__pycache__" in str(py):
                continue
            n = rewrite_file(py)
            if n > 0:
                total_files += 1
                total_replacements += n
                print(f"  [{n:3d}] {py.relative_to(root)}")

    print(f"\nTotal: {total_replacements} replacements in {total_files} files")


if __name__ == "__main__":
    main()
