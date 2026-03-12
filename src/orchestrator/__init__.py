"""Orchestrator system: pipeline control, path registry, core types.

Public API (import from submodules):
    section_decision_store: Decision, load_decisions, record_decision
    path_registry: PathRegistry
    pipeline_control: handle_pending_messages, pause_for_parent,
        poll_control_messages, wait_if_paused
    strategic_state_builder: build_strategic_state
    types: ProposalPassResult, Section, SectionResult
"""
