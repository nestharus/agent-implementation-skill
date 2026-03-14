"""Phase 2 global alignment recheck helpers."""

from __future__ import annotations

from pathlib import Path

from containers import Services
from coordination.types import CoordinationStatus
from orchestrator.path_registry import PathRegistry
from staleness.service.section_alignment_checker import (
    _extract_problems,
    _run_alignment_check_with_retries,
)
from coordination.service.completion_handler import read_incoming_notes
from orchestrator.types import Section, SectionResult, ControlSignal
from signals.types import ALIGNMENT_INVALID_FRAME
from dispatch.types import ALIGNMENT_CHANGED_PENDING


def _update_result(
    section_results: dict[str, SectionResult],
    sec_num: str,
    *,
    aligned: bool,
    problems: str | None = None,
) -> None:
    """Update section_results preserving modified_files from any prior result."""
    prior = section_results.get(sec_num, SectionResult(sec_num))
    section_results[sec_num] = SectionResult(
        section_number=sec_num,
        aligned=aligned,
        problems=problems,
        modified_files=prior.modified_files,
    )


def _recheck_section(
    section: Section,
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> str | None:
    """Recheck a single section's alignment. Returns a CoordinationStatus to abort, or None to continue."""
    sec_num = section.number
    paths = PathRegistry(planspace)
    policy = Services.policies().load(planspace)
    cur_hash = Services.pipeline_control().section_inputs_hash(
        sec_num, planspace, sections_by_num,
    )
    prev_hash_file = paths.phase2_input_hash(sec_num)
    prev_hash = (
        prev_hash_file.read_text(encoding="utf-8").strip()
        if prev_hash_file.exists()
        else ""
    )
    prev_result = section_results.get(sec_num)
    if prev_hash == cur_hash and prev_result and prev_result.aligned:
        Services.logger().log(
            f"Section {sec_num}: inputs unchanged since ALIGNED — skipping "
            "Phase 2 recheck",
        )
        return None
    prev_hash_file.write_text(cur_hash, encoding="utf-8")

    ctrl = Services.pipeline_control().poll_control_messages(planspace, parent, sec_num)
    if ctrl == ControlSignal.ALIGNMENT_CHANGED:
        Services.logger().log("Alignment changed during Phase 2 — restarting from Phase 1")
        return CoordinationStatus.RESTART_PHASE1

    notes = read_incoming_notes(section, planspace, codespace)
    if notes:
        Services.logger().log(f"Section {sec_num}: has incoming notes for global alignment check")

    align_result = _run_alignment_check_with_retries(
        section, planspace, codespace, parent,
        output_prefix="global-align",
        model=Services.policies().resolve(policy, "alignment"),
    )
    if align_result == ALIGNMENT_CHANGED_PENDING:
        return CoordinationStatus.RESTART_PHASE1
    if align_result == ALIGNMENT_INVALID_FRAME:
        Services.logger().log(
            f"Section {sec_num}: invalid alignment frame — requires parent intervention",
        )
        Services.communicator().mailbox_send(planspace, parent, f"fail:invalid_alignment_frame:{sec_num}")
        _update_result(section_results, sec_num, aligned=False,
                       problems="invalid alignment frame — requires parent intervention")
        return None
    if align_result is None:
        Services.logger().log(f"Section {sec_num}: global alignment check timed out after retries")
        _update_result(section_results, sec_num, aligned=False,
                       problems="alignment check timed out after retries")
        return None

    _apply_alignment_outcome(
        align_result, sec_num, planspace, parent, codespace,
        section_results,
    )
    return None


def _apply_alignment_outcome(
    align_result,
    sec_num: str,
    planspace: Path,
    parent: str,
    codespace: Path,
    section_results: dict[str, SectionResult],
) -> None:
    """Extract problems and signals from alignment output, update results."""
    paths = PathRegistry(planspace)
    policy = Services.policies().load(planspace)
    global_align_output = paths.artifacts / f"global-align-{sec_num}-output.md"
    problems = _extract_problems(
        align_result, output_path=global_align_output,
        planspace=planspace, parent=parent, codespace=codespace,
        adjudicator_model=Services.policies().resolve(policy, "adjudicator"),
    )
    main_signal_dir = paths.signals_dir()
    main_signal_dir.mkdir(parents=True, exist_ok=True)
    signal, detail = Services.dispatch_helpers().check_agent_signals(
        signal_path=main_signal_dir / f"global-align-{sec_num}-signal.json",
    )

    if problems is None and signal is None:
        _update_result(section_results, sec_num, aligned=True)
        return

    Services.logger().log(f"Section {sec_num}: global alignment found problems")
    combined = problems or ""
    if signal:
        combined += f"\n[signal:{signal}] {detail}" if combined else f"[signal:{signal}] {detail}"
    _update_result(section_results, sec_num, aligned=False, problems=combined or None)


def run_global_alignment_recheck(
    sections_by_num: dict[str, Section],
    section_results: dict[str, SectionResult],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> str:
    """Refresh per-section alignment results for Phase 2."""
    paths = PathRegistry(planspace)
    Services.logger().log("=== Phase 2: global coordination ===")
    Services.logger().log("Re-checking alignment across all sections...")

    phase2_hash_dir = paths.phase2_inputs_hashes_dir()
    phase2_hash_dir.mkdir(parents=True, exist_ok=True)

    for sec_num, section in sections_by_num.items():
        abort_status = _recheck_section(
            section, section_results, sections_by_num,
            planspace, codespace, parent,
        )
        if abort_status is not None:
            return abort_status

    misaligned = [result for result in section_results.values() if not result.aligned]
    return CoordinationStatus.ALL_ALIGNED if not misaligned else CoordinationStatus.HAS_PROBLEMS
