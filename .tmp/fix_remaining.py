"""Fix remaining old-style imports."""
import re
from pathlib import Path

root = Path("/home/nes/projects/agent-implementation-skill")

# section_loop subpackage imports
SECTION_LOOP_SUBS = {
    "section_loop.section_engine": "implementation.engine_runner",
    "section_loop.prompts": "dispatch.prompts_writers",
    "section_loop.coordination": "coordination.loop_runner",
    "section_loop.intent": "intent.loop_bootstrap",
}

# src.scripts.lib.X module imports in tests
SRC_SCRIPTS_LIB = {
    "src.scripts.lib.pipelines.reconciliation_adjudicator": "src.reconciliation.reconciliation_adjudicator",
    "src.scripts.lib.pipelines.reconciliation_phase": "src.reconciliation.reconciliation_phase",
    "src.scripts.lib.pipelines.coordination_loop": "src.coordination.coordination_loop",
    "src.scripts.lib.pipelines.coordination_executor": "src.coordination.coordination_executor",
    "src.scripts.lib.pipelines.coordination_planner": "src.coordination.coordination_planner",
    "src.scripts.lib.pipelines.coordination_problem_resolver": "src.coordination.coordination_problem_resolver",
    "src.scripts.lib.pipelines.implementation_loop": "src.implementation.implementation_loop",
    "src.scripts.lib.pipelines.implementation_pass": "src.implementation.implementation_pass",
    "src.scripts.lib.pipelines.microstrategy_orchestrator": "src.implementation.microstrategy_orchestrator",
    "src.scripts.lib.pipelines.impact_triage": "src.implementation.impact_triage",
    "src.scripts.lib.pipelines.scope_delta_aggregator": "src.implementation.scope_delta_aggregator",
    "src.scripts.lib.pipelines.proposal_pass": "src.proposal.proposal_pass",
    "src.scripts.lib.pipelines.proposal_loop": "src.proposal.proposal_loop",
    "src.scripts.lib.pipelines.readiness_gate": "src.proposal.readiness_gate",
    "src.scripts.lib.pipelines.problem_frame_gate": "src.proposal.problem_frame_gate",
    "src.scripts.lib.pipelines.excerpt_extractor": "src.proposal.excerpt_extractor",
    "src.scripts.lib.pipelines.recurrence_emitter": "src.intent.recurrence_emitter",
    "src.scripts.lib.pipelines.global_alignment_recheck": "src.staleness.global_alignment_recheck",
    "src.scripts.lib.services.impact_analyzer": "src.implementation.impact_analyzer",
    "src.scripts.lib.services.scope_delta_parser": "src.implementation.scope_delta_parser",
    "src.scripts.lib.services.snapshot_service": "src.implementation.snapshot_service",
    "src.scripts.lib.services.reconciliation_detectors": "src.reconciliation.reconciliation_detectors",
    "src.scripts.lib.services.readiness_resolver": "src.proposal.readiness_resolver",
    "src.scripts.lib.services.qa_verdict_parser": "src.proposal.qa_verdict_parser",
    "src.scripts.lib.services.verdict_parsers": "src.proposal.verdict_parsers",
    "src.scripts.lib.services.freshness_service": "src.staleness.freshness_service",
    "src.scripts.lib.services.section_input_hasher": "src.staleness.section_input_hasher",
    "src.scripts.lib.services.alignment_change_tracker": "src.staleness.alignment_change_tracker",
    "src.scripts.lib.services.alignment_service": "src.staleness.alignment_service",
    "src.scripts.lib.services.signal_reader": "src.signals.signal_reader",
    "src.scripts.lib.core.artifact_io": "src.signals.artifact_io",
    "src.scripts.lib.core.database_client": "src.signals.database_client",
    "src.scripts.lib.core.communication": "src.signals.communication",
    "src.scripts.lib.core.hash_service": "src.staleness.hash_service",
    "src.scripts.lib.core.model_policy": "src.dispatch.model_policy",
    "src.scripts.lib.core.path_registry": "src.orchestrator.path_registry",
    "src.scripts.lib.core.pipeline_state": "src.orchestrator.pipeline_state",
    "src.scripts.lib.dispatch.mailbox_service": "src.signals.mailbox_service",
    "src.scripts.lib.dispatch.message_poller": "src.signals.message_poller",
    "src.scripts.lib.dispatch.agent_executor": "src.dispatch.agent_executor",
    "src.scripts.lib.dispatch.context_sidecar": "src.dispatch.context_sidecar",
    "src.scripts.lib.dispatch.dispatch_helpers": "src.dispatch.dispatch_helpers",
    "src.scripts.lib.dispatch.dispatch_metadata": "src.dispatch.dispatch_metadata",
    "src.scripts.lib.dispatch.monitor_service": "src.dispatch.monitor_service",
    "src.scripts.lib.prompts.prompt_template": "src.dispatch.prompt_template",
    "src.scripts.lib.prompts.prompt_helpers": "src.dispatch.prompt_helpers",
    "src.scripts.lib.prompts.prompt_context_assembler": "src.dispatch.prompt_context_assembler",
    "src.scripts.lib.prompts.substrate_prompt_builder": "src.scan.substrate_prompt_builder",
    "src.scripts.lib.tools.tool_surface": "src.dispatch.tool_surface",
    "src.scripts.lib.tools.log_extract_utils": "src.dispatch.log_extract_utils",
    "src.scripts.lib.flow.flow_context": "src.flow.flow_context",
    "src.scripts.lib.flow.flow_submitter": "src.flow.flow_submitter",
    "src.scripts.lib.flow.flow_reconciler": "src.flow.flow_reconciler",
    "src.scripts.lib.tasks.task_db_client": "src.flow.task_db_client",
    "src.scripts.lib.tasks.task_ingestion": "src.flow.task_ingestion",
    "src.scripts.lib.tasks.task_notifier": "src.flow.task_notifier",
    "src.scripts.lib.tasks.task_parser": "src.flow.task_parser",
    "src.scripts.lib.intent.intent_bootstrap": "src.intent.intent_bootstrap",
    "src.scripts.lib.intent.intent_surface": "src.intent.intent_surface",
    "src.scripts.lib.intent.intent_triage": "src.intent.intent_triage",
    "src.scripts.lib.intent.philosophy_bootstrap": "src.intent.philosophy_bootstrap",
    "src.scripts.lib.research.orchestrator": "src.research.orchestrator",
    "src.scripts.lib.research.plan_executor": "src.research.plan_executor",
    "src.scripts.lib.research.prompt_writer": "src.research.prompt_writer",
    "src.scripts.lib.risk.types": "src.risk.types",
    "src.scripts.lib.risk.engagement": "src.risk.engagement",
    "src.scripts.lib.risk.history": "src.risk.history",
    "src.scripts.lib.risk.loop": "src.risk.loop",
    "src.scripts.lib.risk.package_builder": "src.risk.package_builder",
    "src.scripts.lib.risk.posture": "src.risk.posture",
    "src.scripts.lib.risk.quantifier": "src.risk.quantifier",
    "src.scripts.lib.risk.serialization": "src.risk.serialization",
    "src.scripts.lib.risk.stack_eval": "src.risk.stack_eval",
    "src.scripts.lib.risk.threshold": "src.risk.threshold",
    "src.scripts.lib.risk.value_scales": "src.risk.value_scales",
    "src.scripts.lib.intake.types": "src.intake.types",
    "src.scripts.lib.intake.session": "src.intake.session",
    "src.scripts.lib.intake.verification": "src.intake.verification",
    "src.scripts.lib.governance.loader": "src.intake.governance_loader",
    "src.scripts.lib.governance.assessment": "src.intake.governance_assessment",
    "src.scripts.lib.governance.packet": "src.intake.governance_packet",
    "src.scripts.lib.scan.deep_scan_analyzer": "src.scan.deep_scan_analyzer",
    "src.scripts.lib.scan.scan_dispatch": "src.scan.scan_dispatch",
    "src.scripts.lib.scan.scan_feedback_router": "src.scan.scan_feedback_router",
    "src.scripts.lib.scan.scan_match_updater": "src.scan.scan_match_updater",
    "src.scripts.lib.scan.scan_phase_logger": "src.scan.scan_phase_logger",
    "src.scripts.lib.scan.scan_related_files": "src.scan.scan_related_files",
    "src.scripts.lib.scan.scan_section_iterator": "src.scan.scan_section_iterator",
    "src.scripts.lib.scan.scan_template_loader": "src.scan.scan_template_loader",
    "src.scripts.lib.scan.tier_ranking": "src.scan.tier_ranking",
    "src.scripts.lib.substrate.substrate_dispatch": "src.scan.substrate_dispatch",
    "src.scripts.lib.substrate.substrate_helpers": "src.scan.substrate_helpers",
    "src.scripts.lib.substrate.substrate_policy": "src.scan.substrate_policy",
    "src.scripts.lib.sections.project_mode": "src.scan.project_mode",
    "src.scripts.lib.sections.section_loader": "src.scan.section_loader",
    "src.scripts.lib.sections.section_notes": "src.scan.section_notes",
    "src.scripts.lib.sections.section_decisions": "src.orchestrator.section_decisions",
    "src.scripts.lib.repositories.proposal_state_repository": "src.proposal.proposal_state_repository",
    "src.scripts.lib.repositories.excerpt_repository": "src.proposal.excerpt_repository",
    "src.scripts.lib.repositories.reconciliation_queue": "src.reconciliation.reconciliation_queue",
    "src.scripts.lib.repositories.reconciliation_result_repository": "src.reconciliation.reconciliation_result_repository",
    "src.scripts.lib.repositories.note_repository": "src.coordination.note_repository",
    "src.scripts.lib.repositories.decision_repository": "src.orchestrator.decision_repository",
    "src.scripts.lib.repositories.strategic_state": "src.orchestrator.strategic_state",
    # section_loop in test strings
    "src.scripts.section_loop.section_engine.runner": "src.implementation.engine_runner",
    "src.scripts.section_loop.section_engine.reexplore": "src.implementation.engine_reexplore",
    "src.scripts.section_loop.section_engine.todos": "src.implementation.engine_todos",
    "src.scripts.section_loop.section_engine.traceability": "src.implementation.engine_traceability",
    "src.scripts.section_loop.section_engine.blockers": "src.signals.blockers",
    "src.scripts.section_loop.coordination.execution": "src.coordination.loop_execution",
    "src.scripts.section_loop.coordination.planning": "src.coordination.loop_planning",
    "src.scripts.section_loop.coordination.problems": "src.coordination.loop_problems",
    "src.scripts.section_loop.coordination.runner": "src.coordination.loop_runner",
    "src.scripts.section_loop.intent.bootstrap": "src.intent.loop_bootstrap",
    "src.scripts.section_loop.intent.expansion": "src.intent.loop_expansion",
    "src.scripts.section_loop.intent.surfaces": "src.intent.loop_surfaces",
    "src.scripts.section_loop.intent.triage": "src.intent.loop_triage",
    "src.scripts.section_loop.prompts.context": "src.dispatch.prompts_context",
    "src.scripts.section_loop.prompts.renderer": "src.dispatch.prompts_renderer",
    "src.scripts.section_loop.prompts.writers": "src.dispatch.prompts_writers",
    "src.scripts.section_loop.types": "src.orchestrator.types",
    "src.scripts.section_loop.dispatch": "src.dispatch.section_dispatch",
    "src.scripts.section_loop.communication": "src.signals.section_loop_communication",
    "src.scripts.section_loop.alignment": "src.staleness.section_alignment",
    "src.scripts.section_loop.change_detection": "src.staleness.change_detection",
    "src.scripts.section_loop.context_assembly": "src.orchestrator.context_assembly",
    "src.scripts.section_loop.decisions": "src.orchestrator.decisions",
    "src.scripts.section_loop.pipeline_control": "src.orchestrator.pipeline_control",
    "src.scripts.section_loop.proposal_state": "src.proposal.loop_proposal_state",
    "src.scripts.section_loop.readiness": "src.proposal.loop_readiness",
    "src.scripts.section_loop.reconciliation_queue": "src.reconciliation.loop_reconciliation_queue",
    "src.scripts.section_loop.reconciliation": "src.reconciliation.loop_reconciliation",
    "src.scripts.section_loop.cross_section": "src.coordination.cross_section",
    "src.scripts.section_loop.task_ingestion": "src.flow.section_task_ingestion",
    "src.scripts.section_loop.agent_templates": "src.dispatch.agent_templates",
    "src.scripts.section_loop.main": "src.orchestrator.main",
}

count = 0
files_changed = 0

for py in sorted(root.rglob("*.py")):
    if "__pycache__" in str(py) or ".tmp/" in str(py):
        continue
    text = py.read_text(encoding="utf-8")
    original = text

    # Fix section_loop subpackage imports in src files
    for old, new in sorted(SECTION_LOOP_SUBS.items(), key=lambda x: -len(x[0])):
        text = text.replace(f"from {old} import", f"from {new} import")
        text = text.replace(f"import {old}", f"import {new}")

    # Fix src.scripts.lib and src.scripts.section_loop references (longest first)
    for old, new in sorted(SRC_SCRIPTS_LIB.items(), key=lambda x: -len(x[0])):
        text = text.replace(old, new)

    if text != original:
        py.write_text(text, encoding="utf-8")
        n = sum(1 for a, b in zip(original.splitlines(), text.splitlines()) if a != b)
        files_changed += 1
        count += n
        print(f"  [{n:3d}] {py.relative_to(root)}")

print(f"\nTotal: {count} line changes in {files_changed} files")
