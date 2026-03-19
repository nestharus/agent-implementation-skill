"""Proposal-pass orchestration helpers for the section loop."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        ChangeTrackerService,
        Communicator,
        LogService,
        ModelPolicyService,
        PipelineControlService,
        RiskAssessmentService,
    )
    from flow.engine.flow_submitter import FlowSubmitter

from orchestrator.path_registry import PathRegistry
from orchestrator.repository.section_artifacts import SectionArtifacts
from implementation.repository.roal_index import RoalIndex
from proposal.repository.state import ProposalState, State as ProposalStateRepo
from risk.service.engagement import determine_engagement
from risk.service.package_builder import PackageBuilder

_RAW_RISK_EXPLORATION_THRESHOLD = 60
_RISK_SEVERITY_BLOCKER_THRESHOLD = 3
from risk.repository.serialization import RiskSerializer
from risk.types import EngagementContext, RiskAssessment, RiskMode, RiskPackage, RiskType
from scan.service.section_loader import parse_related_files
from implementation.service.section_reexplorer import SectionReexplorer
from orchestrator.engine.section_pipeline import SectionPipeline, build_section_pipeline
from orchestrator.types import ProposalPassResult, Section
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import PASS_MODE_PROPOSAL, SIGNAL_NEEDS_PARENT

# .. deprecated:: Retained only for import by flow/engine/reconciler.py.
#    The state machine replaces the old parallel fanout model.
PROPOSAL_GATE_SYNTHESIS_TYPE = "proposal.gate_synthesis"

logger = logging.getLogger(__name__)


class ProposalPassExit(Exception):
    """Raised when the proposal pass should stop the outer run."""


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
    artifact_io: ArtifactIOService,
) -> Path:
    return SectionArtifacts(
        artifact_io=artifact_io,
    ).write_section_input_artifact(
        PathRegistry(planspace),
        sec_num,
        f"{advisory_scope}-risk-advisory.json",
        summary,
    )


def _build_advisory_package(
    package: RiskPackage,
    advisory_scope: str,
) -> RiskPackage:
    return RiskPackage(
        package_id=f"{package.package_id}-proposal",
        layer="proposal",
        scope=advisory_scope,
        origin_problem_id=package.origin_problem_id,
        origin_source=package.origin_source,
        steps=list(package.steps),
    )


def _proposal_needs_additional_exploration(assessment: object) -> bool:
    risky = {RiskType.BRUTE_FORCE_REGRESSION, RiskType.SILENT_DRIFT}
    if any(risk in risky for risk in getattr(assessment, "dominant_risks", [])):
        if getattr(assessment, "package_raw_risk", 0) >= _RAW_RISK_EXPLORATION_THRESHOLD:
            return True
    for step_assessment in getattr(assessment, "step_assessments", []):
        if step_assessment.raw_risk < _RAW_RISK_EXPLORATION_THRESHOLD:
            continue
        if any(risk in risky for risk in step_assessment.dominant_risks):
            return True
    return False


def _build_risk_summary(
    assessment: RiskAssessment,
    risk_mode: RiskMode,
) -> dict[str, Any]:
    dominant_risks = [risk.value for risk in assessment.dominant_risks]
    recommendation = (
        "recommend additional exploration"
        if _proposal_needs_additional_exploration(assessment)
        else "proceed"
    )
    severities = _proposal_risk_severities(assessment)
    return {
        "risk_mode": risk_mode.value,
        "dominant_risks": dominant_risks,
        "dominant_risk_severities": severities,
        "package_raw_risk": assessment.package_raw_risk,
        "recommendation": recommendation,
    }


class ProposalPhase:
    def __init__(
        self,
        logger_svc: LogService,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        pipeline_control: PipelineControlService,
        policies: ModelPolicyService,
        risk_assessment: RiskAssessmentService,
        change_tracker: ChangeTrackerService,
        roal_index: RoalIndex,
        section_reexplorer: SectionReexplorer,
        section_pipeline: SectionPipeline | None = None,
    ) -> None:
        self._logger = logger_svc
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._pipeline_control = pipeline_control
        self._policies = policies
        self._risk_assessment = risk_assessment
        self._roal_index = roal_index
        self._section_reexplorer = section_reexplorer
        self._package_builder = PackageBuilder(artifact_io=artifact_io)
        self._serializer = RiskSerializer(artifact_io=artifact_io)
        self._section_pipeline = section_pipeline if section_pipeline is not None else build_section_pipeline()
        self._check_and_clear = change_tracker.make_alignment_checker()

    def _write_proposal_risk_blocker(
        self,
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
            if severities.get(risk, 0) >= _RISK_SEVERITY_BLOCKER_THRESHOLD and risk in dominant_risks
        ]
        detail = (
            "ROAL recommends additional exploration before implementation due to "
            f"high-risk proposal findings ({', '.join(reasons)})"
        )
        blocker_path = paths.signals_dir() / f"section-{sec_num}-proposal-risk-blocker.json"
        self._artifact_io.write_json(
            blocker_path,
            {
                "state": SIGNAL_NEEDS_PARENT,
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

    def _resolve_triage_engagement(
        self,
        paths: PathRegistry,
        sec_num: str,
        advisory_package: RiskPackage,
        proposal_state: ProposalState,
    ) -> RiskMode:
        triage_signal = self._artifact_io.read_json(paths.intent_triage_signal(sec_num))
        triage_confidence = "low"
        risk_mode_hint = ""
        if isinstance(triage_signal, dict):
            triage_confidence = str(
                triage_signal.get("risk_confidence", triage_signal.get("confidence", "low")),
            )
            risk_mode_hint = str(triage_signal.get("risk_mode", ""))

        return determine_engagement(
            step_count=len(advisory_package.steps),
            file_count=max(len(proposal_state.resolved_contracts), 1),
            ctx=EngagementContext(
                has_shared_seams=bool(proposal_state.shared_seam_candidates),
            ),
            triage_confidence=triage_confidence,
            risk_mode_hint=risk_mode_hint,
        )

    def _write_advisory_artifacts(
        self,
        planspace: Path,
        sec_num: str,
        advisory_scope: str,
        summary: dict[str, Any],
    ) -> list[dict]:
        advisory_entries: list[dict] = []
        dominant_risks = summary["dominant_risks"]
        severities = summary["dominant_risk_severities"]
        if summary["recommendation"] == "recommend additional exploration":
            advisory_path = _write_proposal_risk_advisory(
                planspace,
                sec_num,
                advisory_scope,
                summary,
                artifact_io=self._artifact_io,
            )
            advisory_entries.append({
                "kind": "proposal_advisory",
                "path": str(advisory_path),
                "produced_by": "proposal_pass",
            })
            high_risk = any(
                severities.get(risk, 0) >= _RISK_SEVERITY_BLOCKER_THRESHOLD
                for risk in ("brute_force_regression", "silent_drift")
                if risk in dominant_risks
            )
            if high_risk:
                self._write_proposal_risk_blocker(
                    planspace,
                    sec_num,
                    advisory_scope,
                    dominant_risks,
                    severities,
                    advisory_path,
                )
        return advisory_entries

    def _risk_check_proposal(
        self,
        planspace: Path,
        sec_num: str,
    ) -> dict | None:
        """Optional risk pre-check on a proposal before finalization.

        Returns a summary dict with risk_mode, dominant_risks, and recommendation,
        or None on failure.
        """
        scope = f"section-{sec_num}"
        advisory_scope = f"{scope}-proposal"
        paths = PathRegistry(planspace)

        try:
            package = self._package_builder.build_package_from_proposal(scope, planspace)
            advisory_package = _build_advisory_package(package, advisory_scope)
            proposal_state = ProposalStateRepo(
                artifact_io=self._artifact_io,
            ).load_proposal_state(paths.proposal_state(sec_num))
            risk_mode = self._resolve_triage_engagement(
                paths, sec_num, advisory_package, proposal_state,
            )
            self._risk_assessment.run_lightweight_check(
                planspace,
                advisory_scope,
                "proposal",
                advisory_package,
            )
            assessment = self._serializer.load_risk_assessment(paths.risk_assessment(advisory_scope))
            if assessment is None:
                self._roal_index.refresh_roal_input_index(
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
            summary = _build_risk_summary(assessment, risk_mode)
            advisory_entries = self._write_advisory_artifacts(
                planspace, sec_num, advisory_scope, summary,
            )
            self._roal_index.refresh_roal_input_index(
                planspace,
                sec_num,
                replace_kinds=frozenset({"proposal_advisory"}),
                new_entries=advisory_entries,
            )
            return summary
        except Exception as exc:  # noqa: BLE001
            self._roal_index.refresh_roal_input_index(
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

    def _check_alignment_and_requeue(
        self,
        planspace: Path,
        completed: set[str],
        queue: list[str],
        sections_by_num: dict[str, Section],
        *,
        current_section: str | None = None,
    ) -> bool:
        if self._check_and_clear(planspace):
            kwargs: dict[str, Any] = {}
            if current_section is not None:
                kwargs["current_section"] = current_section
            self._pipeline_control.requeue_changed_sections(
                completed,
                queue,
                sections_by_num,
                planspace,
                **kwargs,
            )
            return True
        return False

    def _reexplore_missing_files(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
        completed: set[str],
        queue: list[str],
        sections_by_num: dict[str, Section],
    ) -> bool:
        """Dispatch re-explorer when a section has no related files.

        Returns True if the caller should ``continue`` the loop iteration.
        """
        policy = self._policies.load(planspace)
        sec_num = section.number
        self._logger.log(
            f"Section {sec_num}: no related files — dispatching "
            f"re-explorer agent",
        )
        reexplore_result = self._section_reexplorer.reexplore_section(
            section,
            planspace,
            codespace,
            model=policy["setup"],
        )
        if reexplore_result == ALIGNMENT_CHANGED_PENDING:
            self._check_alignment_and_requeue(
                planspace,
                completed,
                queue,
                sections_by_num,
                current_section=sec_num,
            )
            return True

        section.related_files = parse_related_files(section.path)
        if section.related_files:
            self._logger.log(
                f"Section {sec_num}: re-explorer found "
                f"{len(section.related_files)} files — continuing",
            )
        else:
            self._logger.log(
                f"Section {sec_num}: re-explorer found no files "
                f"— continuing with unresolved related_files",
            )
        return False

    def _process_proposal_result(
        self,
        sec_num: str,
        proposal_result: ProposalPassResult,
        proposal_results: dict[str, ProposalPassResult],
        planspace: Path,
    ) -> None:
        if proposal_result.execution_ready:
            risk_summary = self._risk_check_proposal(
                planspace,
                sec_num,
            )
            if risk_summary is not None:
                self._logger.log(
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
        self._communicator.send_to_parent(planspace, f"proposal-done:{sec_num}:{status}")
        self._logger.log(f"Section {sec_num}: proposal pass complete — {status}")

    def _log_proposal_summary(
        self,
        proposal_results: dict[str, ProposalPassResult],
        completed: set[str],
    ) -> None:
        self._logger.log(f"=== Phase 1a complete: {len(completed)} sections proposed ===")
        ready_sections = sorted(
            num for num, result in proposal_results.items() if result.execution_ready
        )
        blocked_sections = sorted(
            num
            for num, result in proposal_results.items()
            if not result.execution_ready
        )
        self._logger.log(f"Proposal summary: {len(ready_sections)} ready, {len(blocked_sections)} blocked")
        if blocked_sections:
            self._logger.log(f"Blocked sections: {blocked_sections}")

    def run_proposal_pass(
        self,
        all_sections: list[Section],
        sections_by_num: dict[str, Section],
        planspace: Path,
        codespace: Path,
    ) -> dict[str, ProposalPassResult]:
        """Run the proposal pass for all sections and return proposal results.

        .. deprecated::
            DEAD CODE -- the per-section state machine (StateMachineOrchestrator)
            replaces this sequential ``while queue:`` loop.  Each section now
            progresses independently through ``section.propose -> section.assess``
            transitions driven by the state machine.  This method and its module-
            level wrapper are retained only because existing tests reference them.
            Do not add new callers.
        """
        proposal_results: dict[str, ProposalPassResult] = {}
        queue = [section.number for section in all_sections]
        completed: set[str] = set()

        while queue:
            if self._pipeline_control.handle_pending_messages(planspace):
                self._logger.log("Aborted by parent")
                self._communicator.send_to_parent(planspace, "fail:aborted")
                raise ProposalPassExit

            if self._pipeline_control.alignment_changed_pending(planspace):  # noqa: SIM102
                if self._check_alignment_and_requeue(
                    planspace, completed, queue, sections_by_num,
                ):
                    continue

            sec_num = queue.pop(0)
            if sec_num in completed:
                continue

            section = sections_by_num[sec_num]
            section.solve_count += 1
            self._logger.log(
                f"=== Section {sec_num} proposal pass "
                f"({len(queue)} remaining) "
                f"[round {section.solve_count}] ===",
            )
            self._logger.log_lifecycle(planspace, f"start:section:{sec_num}", f"round {section.solve_count}")

            if not section.related_files:
                if self._reexplore_missing_files(
                    section, planspace, codespace,
                    completed, queue, sections_by_num,
                ):
                    continue

            proposal_result = self._section_pipeline.run_section(
                planspace,
                codespace,
                section,
                all_sections=all_sections,
                pass_mode=PASS_MODE_PROPOSAL,
            )

            if self._check_alignment_and_requeue(
                planspace, completed, queue, sections_by_num,
                current_section=sec_num,
            ):
                continue

            if proposal_result is None:
                self._logger.log(
                    f"Section {sec_num}: proposal returned None "
                    f"(paused or aborted) — recording as blocked",
                )
                self._logger.log_lifecycle(planspace, f"end:section:{sec_num}", "paused-blocked")
                completed.add(sec_num)
                proposal_results[sec_num] = ProposalPassResult(
                    section_number=sec_num,
                    proposal_aligned=False,
                    execution_ready=False,
                    blockers=[{
                        "type": "paused",
                        "description": f"Section {sec_num} proposal paused or aborted",
                    }],
                )
                self._communicator.send_to_parent(
                    planspace,
                    f"proposal-done:{sec_num}:blocked (paused)",
                )
                continue

            completed.add(sec_num)
            if isinstance(proposal_result, ProposalPassResult):
                self._process_proposal_result(
                    sec_num, proposal_result, proposal_results, planspace,
                )
            else:
                self._logger.log(
                    f"Section {sec_num}: unexpected proposal result type "
                    f"— treating as failed",
                )

            self._logger.log_lifecycle(planspace, f"end:section:{sec_num}", "proposal-done")

        self._log_proposal_summary(proposal_results, completed)
        return proposal_results


def _get_proposal_phase(
    section_pipeline: SectionPipeline | None = None,
) -> ProposalPhase:
    from containers import Services
    return ProposalPhase(
        logger_svc=Services.logger(),
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        pipeline_control=Services.pipeline_control(),
        policies=Services.policies(),
        risk_assessment=Services.risk_assessment(),
        change_tracker=Services.change_tracker(),
        roal_index=RoalIndex(artifact_io=Services.artifact_io()),
        section_reexplorer=SectionReexplorer(
            communicator=Services.communicator(),
            cross_section=Services.cross_section(),
            dispatcher=Services.dispatcher(),
            flow_ingestion=Services.flow_ingestion(),
            logger=Services.logger(),
            prompt_guard=Services.prompt_guard(),
            task_router=Services.task_router(),
        ),
        section_pipeline=section_pipeline,
    )


def run_proposal_pass(
    all_sections: list[Section],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    section_pipeline: SectionPipeline | None = None,
) -> dict[str, ProposalPassResult]:
    """Run the proposal pass for all sections and return proposal results."""
    return _get_proposal_phase(section_pipeline=section_pipeline).run_proposal_pass(
        all_sections, sections_by_num, planspace, codespace,
    )


def submit_proposal_fanout(
    all_sections: list[Section],
    planspace: Path,
    flow_submitter: FlowSubmitter,
) -> tuple[str | None, str]:
    """Submit one task per section as a fanout into run.db.

    .. deprecated::
        DEAD CODE -- the state machine replaces the old parallel fanout
        model.  The per-section state machine dispatches proposal tasks
        individually.  This function and ``PROPOSAL_GATE_SYNTHESIS_TYPE``
        exist only for reference and will be deleted in the next cleanup.

    Returns ``(gate_id, flow_id)`` so the caller can poll for completion.
    """
    from flow.types.context import FlowEnvelope, new_flow_id
    from flow.types.schema import BranchSpec, GateSpec, TaskSpec

    paths = PathRegistry(planspace)
    db_path = paths.run_db()
    flow_id = new_flow_id()

    branches: list[BranchSpec] = []
    for section in all_sections:
        sec_num = section.number
        branches.append(
            BranchSpec(
                label=f"proposal-section-{sec_num}",
                steps=[
                    TaskSpec(
                        task_type="proposal.section",
                        concern_scope=f"section-{sec_num}",
                        payload_path=str(section.path),
                        priority="normal",
                    ),
                ],
            ),
        )

    gate = GateSpec(
        mode="all",
        failure_policy="include",
        synthesis=TaskSpec(
            task_type=PROPOSAL_GATE_SYNTHESIS_TYPE,
            concern_scope="proposal-gate",
        ),
    )

    env = FlowEnvelope(
        db_path=db_path,
        submitted_by="proposal_phase",
        flow_id=flow_id,
        planspace=planspace,
    )

    gate_id = flow_submitter.submit_fanout(env, branches, gate=gate)
    return gate_id, flow_id
