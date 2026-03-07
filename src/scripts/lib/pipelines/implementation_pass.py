"""Implementation-pass orchestration helpers for the section loop."""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.services.alignment_change_tracker import (
    check_and_clear,
    check_pending as alignment_changed_pending,
)
from lib.core.path_registry import PathRegistry
from section_loop.communication import AGENT_NAME, DB_SH, log, mailbox_send
from section_loop.pipeline_control import (
    _section_inputs_hash,
    handle_pending_messages,
)
from section_loop.section_engine import run_section
from section_loop.types import ProposalPassResult, Section, SectionResult


class ImplementationPassExit(Exception):
    """Raised when the implementation pass should stop the outer run."""


class ImplementationPassRestart(Exception):
    """Raised when Phase 1 should restart after an alignment change."""


def _check_and_clear_alignment_changed(planspace: Path) -> bool:
    return check_and_clear(planspace, db_sh=DB_SH, agent_name=AGENT_NAME)


def run_implementation_pass(
    proposal_results: dict[str, ProposalPassResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> dict[str, SectionResult]:
    """Run the implementation pass for execution-ready sections."""
    paths = PathRegistry(planspace)
    ready_sections = sorted(
        sec_num
        for sec_num, proposal_result in proposal_results.items()
        if proposal_result.execution_ready
    )
    impl_completed: set[str] = set()
    section_results: dict[str, SectionResult] = {}

    for sec_num in ready_sections:
        if handle_pending_messages(planspace, [], impl_completed):
            log("Aborted by parent during implementation pass")
            mailbox_send(planspace, parent, "fail:aborted")
            raise ImplementationPassExit

        if alignment_changed_pending(planspace):
            if _check_and_clear_alignment_changed(planspace):
                log("Alignment changed during implementation pass "
                    "— restarting from Phase 1")
                raise ImplementationPassRestart

        section = sections_by_num[sec_num]
        log(f"=== Section {sec_num} implementation pass ===")
        subprocess.run(  # noqa: S603
            [
                "bash",
                str(DB_SH),  # noqa: S607
                "log",
                str(planspace / "run.db"),
                "lifecycle",
                f"start:section:{sec_num}:impl",
                f"round {section.solve_count}",
                "--agent",
                AGENT_NAME,
            ],
            capture_output=True,
            text=True,
        )

        modified_files = run_section(
            planspace,
            codespace,
            section,
            parent,
            all_sections=list(sections_by_num.values()),
            pass_mode="implementation",
        )

        if _check_and_clear_alignment_changed(planspace):
            log("Alignment changed during implementation — "
                "restarting from Phase 1")
            raise ImplementationPassRestart

        if modified_files is None:
            log(f"Section {sec_num}: implementation returned None")
            subprocess.run(  # noqa: S603
                [
                    "bash",
                    str(DB_SH),  # noqa: S607
                    "log",
                    str(planspace / "run.db"),
                    "lifecycle",
                    f"end:section:{sec_num}:impl",
                    "failed",
                    "--agent",
                    AGENT_NAME,
                ],
                capture_output=True,
                text=True,
            )
            continue

        impl_completed.add(sec_num)
        mailbox_send(
            planspace,
            parent,
            f"done:{sec_num}:{len(modified_files)} files modified",
        )

        section_results[sec_num] = SectionResult(
            section_number=sec_num,
            aligned=True,
            modified_files=modified_files,
        )

        baseline_hash_dir = paths.section_inputs_hashes_dir()
        baseline_hash_dir.mkdir(parents=True, exist_ok=True)
        paths.section_input_hash(sec_num).write_text(
            _section_inputs_hash(sec_num, planspace, codespace, sections_by_num),
            encoding="utf-8",
        )

        phase2_hash_dir = paths.phase2_inputs_hashes_dir()
        phase2_hash_dir.mkdir(parents=True, exist_ok=True)
        paths.phase2_input_hash(sec_num).write_text(
            _section_inputs_hash(sec_num, planspace, codespace, sections_by_num),
            encoding="utf-8",
        )

        log(f"Section {sec_num}: implementation done")
        subprocess.run(  # noqa: S603
            [
                "bash",
                str(DB_SH),  # noqa: S607
                "log",
                str(planspace / "run.db"),
                "lifecycle",
                f"end:section:{sec_num}:impl",
                "done",
                "--agent",
                AGENT_NAME,
            ],
            capture_output=True,
            text=True,
        )

    return section_results
