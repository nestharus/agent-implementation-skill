"""Proposal-pass orchestration helpers for the section loop."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from lib.core.artifact_io import read_json
from lib.core.path_registry import PathRegistry
from lib.repositories.proposal_state_repository import load_proposal_state
from lib.risk.engagement import determine_engagement
from lib.risk.loop import run_lightweight_risk_check
from lib.risk.package_builder import build_package_from_proposal
from lib.risk.serialization import deserialize_assessment, read_risk_artifact
from lib.risk.types import RiskMode, RiskPackage, RiskType
from lib.services.alignment_change_tracker import (
    check_and_clear,
    check_pending as alignment_changed_pending,
)
from lib.sections.section_loader import parse_related_files
from section_loop.communication import AGENT_NAME, DB_SH, log, mailbox_send
from section_loop.dispatch import dispatch_agent
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


def _risk_check_proposal(
    planspace: Path,
    sec_num: str,
    dispatch_fn: Callable,
) -> dict | None:
    """Optional risk pre-check on a proposal before finalization.

    Returns a summary dict with risk_mode, dominant_risks, and recommendation,
    or None if ROAL is skipped.
    """
    scope = f"section-{sec_num}"
    advisory_scope = f"{scope}-proposal"
    paths = PathRegistry(planspace)

    try:
        package = build_package_from_proposal(scope, planspace)
        advisory_package = RiskPackage(
            package_id=f"{package.package_id}-proposal",
            layer="proposal",
            scope=advisory_scope,
            origin_problem_id=package.origin_problem_id,
            origin_source=package.origin_source,
            steps=list(package.steps),
        )
        proposal_state = load_proposal_state(
            paths.proposals_dir() / f"{scope}-proposal-state.json"
        )
        triage_signal = read_json(paths.signals_dir() / f"intent-triage-{sec_num}.json")
        triage_confidence = "low"
        if isinstance(triage_signal, dict):
            triage_confidence = str(triage_signal.get("confidence", "low"))

        risk_mode = determine_engagement(
            step_count=len(advisory_package.steps),
            file_count=max(len(proposal_state.get("resolved_contracts", [])), 1),
            has_shared_seams=bool(proposal_state.get("shared_seam_candidates")),
            has_consequence_notes=False,
            has_stale_inputs=False,
            has_recent_failures=False,
            has_tool_changes=False,
            triage_confidence=triage_confidence,
            freshness_changed=False,
        )
        if risk_mode != RiskMode.FULL:
            return None

        run_lightweight_risk_check(
            planspace,
            advisory_scope,
            "proposal",
            advisory_package,
            dispatch_fn,
        )
        assessment_payload = read_risk_artifact(paths.risk_assessment(advisory_scope))
        if not isinstance(assessment_payload, dict):
            return {
                "risk_mode": risk_mode.value,
                "dominant_risks": [],
                "recommendation": "proceed",
            }

        assessment = deserialize_assessment(assessment_payload)
        dominant_risks = [risk.value for risk in assessment.dominant_risks]
        return {
            "risk_mode": risk_mode.value,
            "dominant_risks": dominant_risks,
            "recommendation": (
                "recommend additional exploration"
                if _proposal_needs_additional_exploration(assessment)
                else "proceed"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        log(
            f"Section {sec_num}: proposal ROAL pre-check failed ({exc}) "
            "— continuing without advisory risk summary",
        )
        return None


def _proposal_needs_additional_exploration(assessment: object) -> bool:
    risky = {RiskType.BRUTE_FORCE_REGRESSION, RiskType.SILENT_DRIFT}
    if any(risk in risky for risk in getattr(assessment, "dominant_risks", [])):
        if getattr(assessment, "package_raw_risk", 0) >= 60:
            return True
    for step_assessment in getattr(assessment, "step_assessments", []):
        if step_assessment.raw_risk < 60:
            continue
        if any(risk in risky for risk in step_assessment.dominant_risks):
            return True
    return False


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
            if proposal_result.execution_ready:
                risk_summary = _risk_check_proposal(
                    planspace,
                    sec_num,
                    dispatch_agent,
                )
                if risk_summary is not None:
                    log(
                        f"Section {sec_num}: proposal ROAL pre-check "
                        f"(mode={risk_summary['risk_mode']}, "
                        f"dominant={risk_summary['dominant_risks']}, "
                        f"recommendation={risk_summary['recommendation']})",
                    )
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
