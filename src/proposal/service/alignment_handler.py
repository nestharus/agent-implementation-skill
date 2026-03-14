"""Alignment checking and signal handling for the proposal loop.

Extracted from proposal_cycle.py to isolate alignment dispatch
and signal interpretation from the main loop orchestration.
"""

from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from dispatch.prompt.writers import write_integration_alignment_prompt
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from proposal.service.cycle_control import handle_pause_response


def run_alignment_check(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
) -> tuple[str, Path] | None:
    """Dispatch the alignment judge and return (result, output_path).

    Returns None if the caller should abort (ALIGNMENT_CHANGED_PENDING).
    """
    paths = PathRegistry(planspace)
    policy = Services.policies().load(planspace)
    section_number = section.number
    artifacts = paths.artifacts
    Services.logger().log(f"Section {section_number}: proposal alignment check")
    align_prompt = write_integration_alignment_prompt(
        section,
        planspace,
        codespace,
    )
    align_output = artifacts / f"intg-align-{section_number}-output.md"
    intent_sec_dir = paths.intent_section_dir(section_number)
    has_intent_artifacts = (
        intent_sec_dir.exists() and (intent_sec_dir / "problem.md").exists()
    )
    alignment_agent_file = (
        "intent-judge.md" if has_intent_artifacts else "alignment-judge.md"
    )
    alignment_model = (
        Services.policies().resolve(policy, "intent_judge")
        if has_intent_artifacts
        else Services.policies().resolve(policy, "alignment")
    )
    align_result = Services.dispatcher().dispatch(
        alignment_model,
        align_prompt,
        align_output,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file=alignment_agent_file,
    )
    if align_result == ALIGNMENT_CHANGED_PENDING:
        return None

    return align_result, align_output


def handle_alignment_signals(
    section_number: str,
    planspace: Path,
    parent: str,
) -> str | None:
    """Check alignment-judge signals for underspec.

    Returns:
        "continue" — underspec handled, caller should retry
        "abort" — caller should return None
        None — no underspec signal, proceed normally
    """
    paths = PathRegistry(planspace)
    signal, detail = Services.dispatch_helpers().check_agent_signals(
        signal_path=paths.signals_dir() / f"proposal-align-{section_number}-signal.json",
    )
    if signal != "underspec":
        return None

    response = Services.pipeline_control().pause_for_parent(
        planspace,
        parent,
        f"pause:underspec:{section_number}:{detail}",
    )
    return handle_pause_response(planspace, section_number, response)
