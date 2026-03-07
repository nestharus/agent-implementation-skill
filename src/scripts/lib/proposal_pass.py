"""Proposal-pass orchestration helpers for the section loop."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from lib.alignment_change_tracker import (
    check_and_clear,
    check_pending as alignment_changed_pending,
)
from lib.section_loader import parse_related_files
from section_loop.communication import AGENT_NAME, DB_SH, log, mailbox_send
from section_loop.pipeline_control import (
    handle_pending_messages,
    requeue_changed_sections,
)
from section_loop.section_engine import _reexplore_section, run_section
from section_loop.types import ProposalPassResult, Section


class ProposalPassExit(Exception):
    """Raised when the proposal pass should stop the outer run."""


def _check_and_clear_alignment_changed(planspace: Path) -> bool:
    return check_and_clear(planspace, db_sh=DB_SH, agent_name=AGENT_NAME)


def run_proposal_pass(
    all_sections: list[Section],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict[str, Any],
) -> dict[str, ProposalPassResult]:
    """Run the proposal pass for all sections and return proposal results."""
    proposal_results: dict[str, ProposalPassResult] = {}
    queue = [section.number for section in all_sections]
    completed: set[str] = set()

    while queue:
        if handle_pending_messages(planspace, queue, completed):
            log("Aborted by parent")
            mailbox_send(planspace, parent, "fail:aborted")
            raise ProposalPassExit

        if alignment_changed_pending(planspace):  # noqa: SIM102
            if _check_and_clear_alignment_changed(planspace):
                requeue_changed_sections(
                    completed,
                    queue,
                    sections_by_num,
                    planspace,
                    codespace,
                )
                continue

        sec_num = queue.pop(0)
        if sec_num in completed:
            continue

        section = sections_by_num[sec_num]
        section.solve_count += 1
        log(
            f"=== Section {sec_num} proposal pass "
            f"({len(queue)} remaining) "
            f"[round {section.solve_count}] ===",
        )
        subprocess.run(  # noqa: S603
            [
                "bash",
                str(DB_SH),  # noqa: S607
                "log",
                str(planspace / "run.db"),
                "lifecycle",
                f"start:section:{sec_num}",
                f"round {section.solve_count}",
                "--agent",
                AGENT_NAME,
            ],
            capture_output=True,
            text=True,
        )

        if not section.related_files:
            log(
                f"Section {sec_num}: no related files — dispatching "
                f"re-explorer agent",
            )
            reexplore_result = _reexplore_section(
                section,
                planspace,
                codespace,
                parent,
                model=policy["setup"],
            )
            if reexplore_result == "ALIGNMENT_CHANGED_PENDING":
                if _check_and_clear_alignment_changed(planspace):
                    requeue_changed_sections(
                        completed,
                        queue,
                        sections_by_num,
                        planspace,
                        codespace,
                        current_section=sec_num,
                    )
                continue

            section.related_files = parse_related_files(section.path)
            if section.related_files:
                log(
                    f"Section {sec_num}: re-explorer found "
                    f"{len(section.related_files)} files — continuing",
                )
            else:
                log(
                    f"Section {sec_num}: re-explorer found no files "
                    f"— continuing with unresolved related_files",
                )

        proposal_result = run_section(
            planspace,
            codespace,
            section,
            parent,
            all_sections=all_sections,
            pass_mode="proposal",
        )

        if _check_and_clear_alignment_changed(planspace):
            requeue_changed_sections(
                completed,
                queue,
                sections_by_num,
                planspace,
                codespace,
                current_section=sec_num,
            )
            continue

        if proposal_result is None:
            log(f"Section {sec_num}: paused during proposal, exiting")
            subprocess.run(  # noqa: S603
                [
                    "bash",
                    str(DB_SH),  # noqa: S607
                    "log",
                    str(planspace / "run.db"),
                    "lifecycle",
                    f"end:section:{sec_num}",
                    "failed",
                    "--agent",
                    AGENT_NAME,
                ],
                capture_output=True,
                text=True,
            )
            raise ProposalPassExit

        completed.add(sec_num)
        if isinstance(proposal_result, ProposalPassResult):
            proposal_results[sec_num] = proposal_result
            status = (
                "ready"
                if proposal_result.execution_ready
                else f"blocked ({len(proposal_result.blockers)} blockers)"
            )
            mailbox_send(planspace, parent, f"proposal-done:{sec_num}:{status}")
            log(f"Section {sec_num}: proposal pass complete — {status}")
        else:
            log(
                f"Section {sec_num}: unexpected proposal result type "
                f"— treating as failed",
            )

        subprocess.run(  # noqa: S603
            [
                "bash",
                str(DB_SH),  # noqa: S607
                "log",
                str(planspace / "run.db"),
                "lifecycle",
                f"end:section:{sec_num}",
                "proposal-done",
                "--agent",
                AGENT_NAME,
            ],
            capture_output=True,
            text=True,
        )

    log(f"=== Phase 1a complete: {len(completed)} sections proposed ===")
    ready_sections = sorted(
        num for num, result in proposal_results.items() if result.execution_ready
    )
    blocked_sections = sorted(
        num
        for num, result in proposal_results.items()
        if not result.execution_ready
    )
    log(f"Proposal summary: {len(ready_sections)} ready, {len(blocked_sections)} blocked")
    if blocked_sections:
        log(f"Blocked sections: {blocked_sections}")
    return proposal_results
