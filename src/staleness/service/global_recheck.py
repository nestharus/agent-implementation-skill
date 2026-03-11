"""Phase 2 global alignment recheck helpers."""

from __future__ import annotations

from pathlib import Path

from dispatch.service.model_policy import resolve
from orchestrator.path_registry import PathRegistry
from staleness.service.section_alignment import (
    _extract_problems,
    _run_alignment_check_with_retries,
)
from signals.service.communication import log, mailbox_send
from coordination.service.cross_section import read_incoming_notes
from dispatch.helpers.utils import check_agent_signals
from orchestrator.service.pipeline_control import _section_inputs_hash, poll_control_messages
from orchestrator.types import Section, SectionResult


def run_global_alignment_recheck(
    sections_by_num: dict[str, Section],
    section_results: dict[str, SectionResult],
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
) -> str:
    """Refresh per-section alignment results for Phase 2."""
    paths = PathRegistry(planspace)
    log("=== Phase 2: global coordination ===")
    log("Re-checking alignment across all sections...")

    phase2_hash_dir = paths.phase2_inputs_hashes_dir()
    phase2_hash_dir.mkdir(parents=True, exist_ok=True)

    for sec_num, section in sections_by_num.items():
        cur_hash = _section_inputs_hash(
            sec_num,
            planspace,
            codespace,
            sections_by_num,
        )
        prev_hash_file = paths.phase2_input_hash(sec_num)
        prev_hash = (
            prev_hash_file.read_text(encoding="utf-8").strip()
            if prev_hash_file.exists()
            else ""
        )
        prev_result = section_results.get(sec_num)
        if prev_hash == cur_hash and prev_result and prev_result.aligned:
            log(
                f"Section {sec_num}: inputs unchanged since ALIGNED — skipping "
                "Phase 2 recheck",
            )
            continue
        prev_hash_file.write_text(cur_hash, encoding="utf-8")

        ctrl = poll_control_messages(planspace, parent, sec_num)
        if ctrl == "alignment_changed":
            log("Alignment changed during Phase 2 — restarting from Phase 1")
            return "restart_phase1"

        notes = read_incoming_notes(section, planspace, codespace)
        if notes:
            log(f"Section {sec_num}: has incoming notes for global alignment check")

        align_result = _run_alignment_check_with_retries(
            section,
            planspace,
            codespace,
            parent,
            sec_num,
            output_prefix="global-align",
            model=resolve(policy, "alignment"),
            adjudicator_model=resolve(policy, "adjudicator"),
        )
        if align_result == "ALIGNMENT_CHANGED_PENDING":
            return "restart_phase1"
        if align_result == "INVALID_FRAME":
            log(
                f"Section {sec_num}: invalid alignment frame — requires parent "
                "intervention",
            )
            mailbox_send(planspace, parent, f"fail:invalid_alignment_frame:{sec_num}")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems="invalid alignment frame — requires parent intervention",
                modified_files=section_results.get(
                    sec_num,
                    SectionResult(sec_num),
                ).modified_files,
            )
            continue
        if align_result is None:
            log(f"Section {sec_num}: global alignment check timed out after retries")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems="alignment check timed out after retries",
                modified_files=section_results.get(
                    sec_num,
                    SectionResult(sec_num),
                ).modified_files,
            )
            continue

        global_align_output = paths.artifacts / f"global-align-{sec_num}-output.md"
        problems = _extract_problems(
            align_result,
            output_path=global_align_output,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
            adjudicator_model=resolve(policy, "adjudicator"),
        )
        main_signal_dir = paths.signals_dir()
        main_signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            align_result,
            signal_path=main_signal_dir / f"global-align-{sec_num}-signal.json",
            output_path=global_align_output,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
        )

        if problems is None and signal is None:
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=True,
                modified_files=section_results.get(
                    sec_num,
                    SectionResult(sec_num),
                ).modified_files,
            )
            continue

        log(f"Section {sec_num}: global alignment found problems")
        combined_problems = problems or ""
        if signal:
            combined_problems += (
                f"\n[signal:{signal}] {detail}"
                if combined_problems
                else f"[signal:{signal}] {detail}"
            )
        section_results[sec_num] = SectionResult(
            section_number=sec_num,
            aligned=False,
            problems=combined_problems or None,
            modified_files=section_results.get(
                sec_num,
                SectionResult(sec_num),
            ).modified_files,
        )

    misaligned = [result for result in section_results.values() if not result.aligned]
    return "all_aligned" if not misaligned else "has_problems"
