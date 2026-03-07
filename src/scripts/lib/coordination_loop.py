"""Adaptive coordination loop helpers for the section loop."""

from __future__ import annotations

from pathlib import Path

from lib.artifact_io import write_json
from lib.coordination_problem_resolver import _collect_outstanding_problems
from lib.path_registry import PathRegistry
from lib.strategic_state import build_strategic_state
from section_loop.communication import log, mailbox_send
from section_loop.coordination import (
    MAX_COORDINATION_ROUNDS,
    MIN_COORDINATION_ROUNDS,
    run_global_coordination,
)
from section_loop.pipeline_control import poll_control_messages
from section_loop.types import Section, SectionResult


def run_coordination_loop(
    all_sections: list[Section],
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
) -> str:
    """Run the adaptive coordination loop until completion or exhaustion."""
    paths = PathRegistry(planspace)
    decisions_dir = paths.decisions_dir()

    misaligned = [result for result in section_results.values() if not result.aligned]
    outstanding: list[dict] = []
    if not misaligned:
        outstanding = _collect_outstanding_problems(
            section_results,
            sections_by_num,
            planspace,
        )
        if outstanding:
            outstanding_types = [problem["type"] for problem in outstanding]
            log(
                f"{len(outstanding)} outstanding cross-section problems remain "
                f"(types: {outstanding_types}) — cannot declare completion",
            )
        else:
            ctrl = poll_control_messages(planspace, parent)
            if ctrl == "alignment_changed":
                log("Alignment changed just before completion — restarting from Phase 1")
                return "restart_phase1"
            log("=== All sections ALIGNED after initial pass ===")
            build_strategic_state(decisions_dir, section_results, planspace)
            mailbox_send(planspace, parent, "complete")
            return "complete"

    outstanding_count = len(outstanding) if not misaligned else 0
    if misaligned:
        log(
            f"{len(misaligned)} sections need coordination: "
            f"{sorted(result.section_number for result in misaligned)}",
        )
    else:
        log(
            "All sections aligned but "
            f"{outstanding_count} outstanding cross-section problems need coordination",
        )

    prev_unresolved = len(misaligned) + outstanding_count
    stall_count = 0
    round_num = 0
    termination_reason = "exhausted"
    while round_num < MAX_COORDINATION_ROUNDS:
        round_num += 1
        ctrl = poll_control_messages(planspace, parent)
        if ctrl == "alignment_changed":
            log("Alignment changed during coordination — restarting from Phase 1")
            return "restart_phase1"

        log(f"=== Coordination round {round_num} (prev unresolved: {prev_unresolved}) ===")
        mailbox_send(planspace, parent, f"status:coordination:round-{round_num}")

        all_done = run_global_coordination(
            all_sections,
            section_results,
            sections_by_num,
            planspace,
            codespace,
            parent,
        )

        if _check_and_clear_alignment_changed(planspace):
            log("Alignment changed during coordination — restarting from Phase 1")
            return "restart_phase1"

        if all_done:
            ctrl = poll_control_messages(planspace, parent)
            if ctrl == "alignment_changed":
                log("Alignment changed just before completion — restarting from Phase 1")
                return "restart_phase1"
            log(f"=== All sections ALIGNED after coordination round {round_num} ===")
            build_strategic_state(decisions_dir, section_results, planspace)
            mailbox_send(planspace, parent, "complete")
            return "complete"

        remaining = [result for result in section_results.values() if not result.aligned]
        remaining_outstanding = (
            _collect_outstanding_problems(
                section_results,
                sections_by_num,
                planspace,
            )
            if not remaining
            else []
        )
        cur_unresolved = len(remaining) + len(remaining_outstanding)
        log(
            f"Coordination round {round_num}: {cur_unresolved} unresolved "
            f"({len(remaining)} misaligned, {len(remaining_outstanding)} outstanding) "
            f"(was {prev_unresolved})",
        )

        if cur_unresolved >= prev_unresolved:
            stall_count += 1
            escalation_threshold = policy.get("escalation_triggers", {}).get(
                "stall_count",
                2,
            )
            if stall_count == escalation_threshold:
                log(
                    f"Coordination churning ({stall_count} rounds without "
                    "improvement) — escalating model",
                )
                escalation_file = paths.coordination_dir() / "model-escalation.txt"
                escalation_file.write_text(
                    policy["escalation_model"],
                    encoding="utf-8",
                )
                mailbox_send(
                    planspace,
                    parent,
                    f"escalation:coordination:round-{round_num}:stall_count={stall_count}",
                )
            if round_num >= MIN_COORDINATION_ROUNDS and stall_count >= 3:
                log(
                    f"Coordination stalled ({stall_count} rounds without "
                    "improvement) — stopping",
                )
                termination_reason = "stalled"
                break
        else:
            stall_count = 0

        prev_unresolved = cur_unresolved

    remaining = [result for result in section_results.values() if not result.aligned]
    if remaining:
        log(
            f"=== Coordination finished after {round_num} rounds, "
            f"{len(remaining)} sections still unresolved ===",
        )
        build_strategic_state(decisions_dir, section_results, planspace)
        for result in remaining:
            summary = (result.problems or "unknown")[:120]
            log(f"  - Section {result.section_number}: {summary}")
            mailbox_send(
                planspace,
                parent,
                f"fail:{result.section_number}:coordination_exhausted:{summary}",
            )
        return termination_reason

    outstanding = _collect_outstanding_problems(
        section_results,
        sections_by_num,
        planspace,
    )
    if outstanding:
        log(
            f"=== Coordination exhausted after {round_num} rounds: all sections "
            f"aligned but {len(outstanding)} outstanding problems remain ===",
        )
        build_strategic_state(decisions_dir, section_results, planspace)
        rollup_dir = paths.coordination_dir()
        rollup_dir.mkdir(parents=True, exist_ok=True)
        rollup_path = rollup_dir / "coordination-exhausted.json"
        write_json(
            rollup_path,
            [
                {
                    "type": problem["type"],
                    "section": problem["section"],
                    "description": problem["description"][:200],
                }
                for problem in outstanding
            ],
        )
        mailbox_send(
            planspace,
            parent,
            f"fail:coordination_exhausted:outstanding:{len(outstanding)}",
        )
        return termination_reason

    return termination_reason


def _check_and_clear_alignment_changed(planspace: Path) -> bool:
    from section_loop.main import _check_and_clear_alignment_changed as check_and_clear

    return check_and_clear(planspace)
