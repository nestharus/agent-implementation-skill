"""Proposal-pass orchestration helpers for the section loop."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Callable

from orchestrator.path_registry import PathRegistry
from orchestrator.repository.section_artifacts import write_section_input_artifact
from implementation.repository.roal_index import refresh_roal_input_index
from proposal.repository.state import load_proposal_state
from risk.service.engagement import determine_engagement
from risk.engine.loop import run_lightweight_risk_check
from risk.service.package_builder import build_package_from_proposal
from risk.repository.serialization import load_risk_assessment
from risk.types import RiskMode, RiskPackage, RiskType
from staleness.service.change_tracker import (
    make_alignment_checker,
)
from scan.service.section_loader import parse_related_files
from _config import AGENT_NAME, DB_SH
from containers import Services
from implementation.service.reexplore import _reexplore_section
from implementation.engine.runner import run_section
from orchestrator.types import ProposalPassResult, Section

logger = logging.getLogger(__name__)


class ProposalPassExit(Exception):
    """Raised when the proposal pass should stop the outer run."""


_check_and_clear_alignment_changed = make_alignment_checker(DB_SH, AGENT_NAME)


def _proposal_risk_severities(assessment: object) -> dict[str, int]:
    severities: dict[str, int] = {}
    for step_assessment in getattr(assessment, "step_assessments", []):
        for risk in getattr(step_assessment, "dominant_risks", []):
            value = getattr(step_assessment.risk_vector, risk.value, 0)
            severities[risk.value] = max(severities.get(risk.value, 0), int(value))
    return severities


def _write_proposal_risk_advisory(
    planspace: Path,
    sec_num: str,
    advisory_scope: str,
    summary: dict[str, Any],
) -> Path:
    return write_section_input_artifact(
        PathRegistry(planspace),
        sec_num,
        f"{advisory_scope}-risk-advisory.json",
        summary,
    )


def _write_proposal_risk_blocker(
    planspace: Path,
    sec_num: str,
    advisory_scope: str,
    dominant_risks: list[str],
    severities: dict[str, int],
    advisory_path: Path,
) -> Path:
    paths = PathRegistry(planspace)
    reasons = [
        f"{risk}={severities[risk]}"
        for risk in ("brute_force_regression", "silent_drift")
        if severities.get(risk, 0) >= 3 and risk in dominant_risks
    ]
    detail = (
        "ROAL recommends additional exploration before implementation due to "
        f"high-risk proposal findings ({', '.join(reasons)})"
    )
    blocker_path = paths.signals_dir() / f"section-{sec_num}-proposal-risk-blocker.json"
    Services.artifact_io().write_json(
        blocker_path,
        {
            "state": "needs_parent",
            "blocker_type": "proposal_risk_advisory",
            "source": "roal",
            "section": sec_num,
            "scope": advisory_scope,
            "detail": detail,
            "why_blocked": detail,
            "needs": "Additional exploration before implementation",
            "dominant_risks": list(dominant_risks),
            "dominant_risk_severities": severities,
            "risk_summary_path": str(advisory_path.resolve()),
        },
    )
    return blocker_path


def _risk_check_proposal(
    planspace: Path,
    sec_num: str,
    dispatch_fn: Callable,
) -> dict | None:
    """Optional risk pre-check on a proposal before finalization.

    Returns a summary dict with risk_mode, dominant_risks, and recommendation,
    or None on failure.
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
        triage_signal = Services.artifact_io().read_json(paths.signals_dir() / f"intent-triage-{sec_num}.json")
        triage_confidence = "low"
        risk_mode_hint = ""
        if isinstance(triage_signal, dict):
            triage_confidence = str(
                triage_signal.get("risk_confidence", triage_signal.get("confidence", "low")),
            )
            risk_mode_hint = str(triage_signal.get("risk_mode", ""))

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
            risk_mode_hint=risk_mode_hint,
        )
        run_lightweight_risk_check(
            planspace,
            advisory_scope,
            "proposal",
            advisory_package,
            dispatch_fn,
        )
        assessment = load_risk_assessment(paths.risk_assessment(advisory_scope))
        if assessment is None:
            refresh_roal_input_index(
                planspace,
                sec_num,
                replace_kinds=frozenset({"proposal_advisory"}),
                new_entries=[],
            )
            return {
                "risk_mode": risk_mode.value,
                "dominant_risks": [],
                "recommendation": "proceed",
            }
        dominant_risks = [risk.value for risk in assessment.dominant_risks]
        recommendation = (
            "recommend additional exploration"
            if _proposal_needs_additional_exploration(assessment)
            else "proceed"
        )
        severities = _proposal_risk_severities(assessment)
        summary = {
            "risk_mode": risk_mode.value,
            "dominant_risks": dominant_risks,
            "dominant_risk_severities": severities,
            "package_raw_risk": assessment.package_raw_risk,
            "recommendation": recommendation,
        }
        advisory_entries: list[dict] = []
        if recommendation == "recommend additional exploration":
            advisory_path = _write_proposal_risk_advisory(
                planspace,
                sec_num,
                advisory_scope,
                summary,
            )
            advisory_entries.append({
                "kind": "proposal_advisory",
                "path": str(advisory_path),
                "produced_by": "proposal_pass",
            })
            high_risk = any(
                severities.get(risk, 0) >= 3
                for risk in ("brute_force_regression", "silent_drift")
                if risk in dominant_risks
            )
            if high_risk:
                _write_proposal_risk_blocker(
                    planspace,
                    sec_num,
                    advisory_scope,
                    dominant_risks,
                    severities,
                    advisory_path,
                )
        refresh_roal_input_index(
            planspace,
            sec_num,
            replace_kinds=frozenset({"proposal_advisory"}),
            new_entries=advisory_entries,
        )
        return summary
    except Exception as exc:  # noqa: BLE001
        refresh_roal_input_index(
            planspace,
            sec_num,
            replace_kinds=frozenset({"proposal_advisory"}),
            new_entries=[],
        )
        logger.warning(
            "Section %s: proposal ROAL pre-check failed — continuing "
            "without advisory risk summary",
            sec_num,
            exc_info=True,
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
        if Services.pipeline_control().handle_pending_messages(planspace, queue, completed):
            Services.logger().log("Aborted by parent")
            Services.communicator().mailbox_send(planspace, parent, "fail:aborted")
            raise ProposalPassExit

        if Services.pipeline_control().alignment_changed_pending(planspace):  # noqa: SIM102
            if _check_and_clear_alignment_changed(planspace):
                Services.pipeline_control().requeue_changed_sections(
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
        Services.logger().log(
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
            Services.logger().log(
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
                    Services.pipeline_control().requeue_changed_sections(
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
                Services.logger().log(
                    f"Section {sec_num}: re-explorer found "
                    f"{len(section.related_files)} files — continuing",
                )
            else:
                Services.logger().log(
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
            Services.pipeline_control().requeue_changed_sections(
                completed,
                queue,
                sections_by_num,
                planspace,
                codespace,
                current_section=sec_num,
            )
            continue

        if proposal_result is None:
            Services.logger().log(f"Section {sec_num}: paused during proposal, exiting")
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
                    Services.dispatcher().dispatch,
                )
                if risk_summary is not None:
                    Services.logger().log(
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
            Services.communicator().mailbox_send(planspace, parent, f"proposal-done:{sec_num}:{status}")
            Services.logger().log(f"Section {sec_num}: proposal pass complete — {status}")
        else:
            Services.logger().log(
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

    Services.logger().log(f"=== Phase 1a complete: {len(completed)} sections proposed ===")
    ready_sections = sorted(
        num for num, result in proposal_results.items() if result.execution_ready
    )
    blocked_sections = sorted(
        num
        for num, result in proposal_results.items()
        if not result.execution_ready
    )
    Services.logger().log(f"Proposal summary: {len(ready_sections)} ready, {len(blocked_sections)} blocked")
    if blocked_sections:
        Services.logger().log(f"Blocked sections: {blocked_sections}")
    return proposal_results
