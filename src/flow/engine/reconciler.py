"""Flow completion reconciliation helpers."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from flow.engine.bootstrap_coordinator import BootstrapCoordinator
from flow.engine.result_projector import TaskResultProjector
from flow.service.task_db_client import load_task, task_db
from flow.types.context import FlowEnvelope, TaskStatus
from flow.types.schema import ChainAction, FanoutAction, parse_flow_signal
from proposal.engine.proposal_phase import PROPOSAL_GATE_SYNTHESIS_TYPE
from research.engine.orchestrator import ResearchState
from intake.service.assessment_evaluator import (
    AssessmentEvaluator,
    AssessmentVerdict,
)
from implementation.service.traceability_writer import TraceabilityWriter
from flow.repository.gate_repository import (
    cancel_chain_descendants,
    find_gate_for_chain,
    get_gate_member_leaf,
    update_gate_member,
    update_gate_member_leaf,
)
from proposal.service.readiness_resolver import ReadinessResolver
from signals.types import (
    SIGNAL_NEED_DECISION,
    VERIFICATION_STRUCTURAL_FAILURE,
    VERIFICATION_INTEGRATION_FAILURE,
    TEST_BEHAVIORAL_FAILURE,
)

if TYPE_CHECKING:
    from containers import ArtifactIOService, PromptGuard, ResearchOrchestratorService
    from flow.engine.flow_submitter import FlowSubmitter
    from flow.repository.gate_repository import GateRepository

logger = logging.getLogger(__name__)

def build_result_manifest(
    task_id: int,
    instance_id: str,
    flow_id: str,
    chain_id: str,
    task_type: str,
    status: str,
    output_path: str | None,
    error: str | None,
) -> dict:
    """Build result manifest dict for a completed or failed task."""
    return {
        "task_id": task_id,
        "instance_id": instance_id,
        "flow_id": flow_id,
        "chain_id": chain_id,
        "task_type": task_type,
        "status": status,
        "output_path": output_path,
        "error": error,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def build_gate_aggregate_manifest(
    gate_id: str,
    flow_id: str,
    mode: str,
    failure_policy: str,
    origin_refs: list[str],
    members: list[dict],
) -> dict:
    """Build gate aggregate manifest dict."""
    return {
        "gate_id": gate_id,
        "flow_id": flow_id,
        "mode": mode,
        "failure_policy": failure_policy,
        "origin_refs": origin_refs,
        "members": members,
    }


def _research_section_number(task: dict) -> str | None:
    """Extract a section number from a section-scoped research task."""
    return _section_number(task)


def _section_number(task: dict) -> str | None:
    """Extract a section number from a section-scoped task."""
    concern_scope = str(task.get("concern_scope") or "")
    match = re.match(r"^section-(\d+)$", concern_scope)
    if match:
        return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Delta propagation helpers (Piece 5E)
# ---------------------------------------------------------------------------


def _write_codemap_delta(
    paths: PathRegistry,
    section_number: str,
    refined_text: str,
) -> None:
    """Write a delta artifact recording what this refinement added.

    The delta is a JSON file containing the section number and the
    refined text lines.  Parent sections consume these deltas and
    merge them additively into their own fragment.

    Failure here must never block the main refinement path.
    """
    try:
        delta_path = paths.codemap_delta(section_number)
        delta_path.parent.mkdir(parents=True, exist_ok=True)
        delta_payload = json.dumps(
            {
                "section": section_number,
                "lines": refined_text.splitlines(),
            },
            indent=2,
        )
        delta_path.write_text(delta_payload + "\n", encoding="utf-8")
        logger.info(
            "codemap delta written for section %s at %s",
            section_number,
            delta_path,
        )
    except Exception:
        logger.debug(
            "Failed to write codemap delta for section %s — continuing",
            section_number,
            exc_info=True,
        )


def _propagate_delta_to_parent(
    paths: PathRegistry,
    db_path: Path,
    section_number: str,
    refined_text: str,
) -> None:
    """Merge a child's refinement into the parent section's fragment.

    Looks up ``parent_section`` for *section_number* in
    ``section_states``.  If a parent exists and has its own codemap
    fragment, appends new lines from the child that are not already
    present.  If the parent has no fragment yet, one is created from
    the child's contribution.

    This is additive-only: existing parent fragment content is never
    removed.  Failure here must never block the main refinement path.
    """
    try:
        parent = _lookup_parent_section(db_path, section_number)
        if parent is None:
            return

        parent_fragment_path = paths.section_codemap(parent)
        parent_fragment_path.parent.mkdir(parents=True, exist_ok=True)

        existing_lines: list[str] = []
        if parent_fragment_path.is_file():
            try:
                existing_lines = parent_fragment_path.read_text(
                    encoding="utf-8",
                ).splitlines()
            except OSError:
                existing_lines = []

        existing_set = set(existing_lines)
        new_lines = [
            line for line in refined_text.splitlines()
            if line not in existing_set
        ]

        if not new_lines:
            return

        merged = existing_lines + new_lines
        parent_fragment_path.write_text(
            "\n".join(merged) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "codemap delta: merged %d new lines from section %s into "
            "parent %s fragment",
            len(new_lines),
            section_number,
            parent,
        )
    except Exception:
        logger.debug(
            "Failed to propagate codemap delta from section %s to "
            "parent — continuing",
            section_number,
            exc_info=True,
        )


def _lookup_parent_section(db_path: Path, section_number: str) -> str | None:
    """Return the parent_section for *section_number*, or None."""
    try:
        with task_db(db_path) as conn:
            row = conn.execute(
                "SELECT parent_section FROM section_states "
                "WHERE section_number = ?",
                (section_number,),
            ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        logger.debug(
            "Could not look up parent_section for %s",
            section_number,
            exc_info=True,
        )
    return None


class Reconciler:
    def __init__(
        self,
        artifact_io: ArtifactIOService,
        research: ResearchOrchestratorService,
        prompt_guard: PromptGuard,
        flow_submitter: FlowSubmitter,
        gate_repository: GateRepository,
        traceability_writer: TraceabilityWriter,
        bootstrap_coordinator: BootstrapCoordinator | None = None,
        result_projector: TaskResultProjector | None = None,
    ) -> None:
        self._artifact_io = artifact_io
        self._research = research
        self._prompt_guard = prompt_guard
        self._flow_submitter = flow_submitter
        self._gate_repository = gate_repository
        self._traceability_writer = traceability_writer
        self._bootstrap_coordinator = (
            bootstrap_coordinator
            if bootstrap_coordinator is not None
            else BootstrapCoordinator(artifact_io=artifact_io, flow_submitter=flow_submitter)
        )
        self._result_projector = (
            result_projector
            if result_projector is not None
            else TaskResultProjector(artifact_io=artifact_io)
        )

    def _load_continuation(self, planspace: Path, continuation_path: str | None):
        """Try loading a continuation signal. Returns (continuation, is_malformed)."""
        if not continuation_path:
            return None, False
        cont_file = planspace / continuation_path
        if not cont_file.exists():
            return None, False
        try:
            return parse_flow_signal(cont_file), False
        except (ValueError, json.JSONDecodeError) as exc:
            print(
                f"[FLOW][WARN] Malformed continuation at {cont_file} "
                f"({exc}) — renaming to .malformed.json",
            )
            self._artifact_io.rename_malformed(cont_file)
            return None, True

    def _process_continuation_actions(
        self,
        db_path: Path,
        continuation,
        task_id: int,
        flow_id: str,
        chain_id: str,
        origin_refs: list[str],
        planspace: Path,
    ) -> None:
        """Submit chain/fanout actions from a continuation signal."""
        env = FlowEnvelope(
            db_path=db_path,
            submitted_by="reconciler",
            flow_id=flow_id,
            declared_by_task_id=task_id,
            origin_refs=origin_refs,
            planspace=planspace,
        )
        for action in continuation.actions:
            if isinstance(action, ChainAction) and action.steps:
                new_ids = self._flow_submitter.submit_chain(
                    env,
                    action.steps,
                    chain_id=chain_id,
                    initial_dependency_task_id=task_id,
                )
                if new_ids:
                    gate_id = find_gate_for_chain(db_path, chain_id)
                    if gate_id:
                        update_gate_member_leaf(db_path, gate_id, chain_id, new_ids[-1])

            elif isinstance(action, FanoutAction) and action.branches:
                self._flow_submitter.submit_fanout(
                    env,
                    action.branches,
                    gate=action.gate,
                )

    def _fail_chain_gate(
        self,
        db_path: Path,
        planspace: Path,
        chain_id: str,
        task_id: int,
        result_manifest_path: str | None,
        flow_id: str,
        origin_refs: list[str],
    ) -> None:
        """Cancel chain descendants and mark the gate member as failed."""
        cancel_chain_descendants(db_path, chain_id, task_id)
        gate_id = find_gate_for_chain(db_path, chain_id)
        if gate_id:
            update_gate_member(db_path, gate_id, chain_id, TaskStatus.FAILED, result_manifest_path)
            self.check_and_fire_gate(db_path, planspace, gate_id, flow_id, origin_refs)

    def _complete_chain_gate(
        self,
        db_path: Path,
        planspace: Path,
        chain_id: str,
        task_id: int,
        result_manifest_path: str | None,
        flow_id: str,
        origin_refs: list[str],
    ) -> None:
        """Mark the gate member complete if this task is the leaf."""
        gate_id = find_gate_for_chain(db_path, chain_id)
        if not gate_id:
            return
        member_leaf = get_gate_member_leaf(db_path, gate_id, chain_id)
        if member_leaf == task_id:
            update_gate_member(db_path, gate_id, chain_id, TaskStatus.COMPLETE, result_manifest_path)
            self.check_and_fire_gate(db_path, planspace, gate_id, flow_id, origin_refs)

    def _handle_synthesis_completion(
        self,
        db_path: Path,
        planspace: Path,
        section_number: str,
        task: dict,
        output_path: str | None,
        origin_refs: list[str],
        trigger_hash: str,
        cycle_id: str,
    ) -> None:
        """Handle research.synthesis task completion — verify or finalize."""
        plan = self._research.validate_plan(PathRegistry(planspace).research_plan(section_number))
        verify_claims = bool(
            isinstance(plan, dict)
            and isinstance(plan.get("flow"), dict)
            and plan["flow"].get("verify_claims")
        )
        if verify_claims:
            self._research.submit_verify(
                section_number, planspace,
                db_path=db_path,
                declared_by_task_id=int(task["id"]),
                origin_refs=origin_refs + ([output_path] if output_path else []),
            )
        else:
            self._research.write_status(
                section_number, planspace, ResearchState.SYNTHESIZED,
                detail="research synthesis complete",
                trigger_hash=trigger_hash, cycle_id=cycle_id,
            )

    def _handle_research_completion(
        self,
        db_path: Path,
        planspace: Path,
        task: dict,
        status: str,
        output_path: str | None,
        error: str | None,
        origin_refs: list[str],
        codespace: Path | None,
    ) -> None:
        """Apply script-owned research follow-on logic on task completion."""
        task_type = str(task.get("task_type") or "")
        if task_type not in {
            "research.plan",
            "research.synthesis",
            "research.verify",
        }:
            return

        section_number = _research_section_number(task)
        if section_number is None:
            return

        status_data = self._research.load_status(section_number, planspace) or {}
        trigger_hash = str(status_data.get("trigger_hash", ""))
        cycle_id = str(status_data.get("cycle_id", ""))

        if status == TaskStatus.FAILED:
            self._research.write_status(
                section_number,
                planspace,
                ResearchState.FAILED,
                detail=error or f"{task_type} failed",
                trigger_hash=trigger_hash,
                cycle_id=cycle_id,
            )
            return

        if status != TaskStatus.COMPLETE:
            return

        if task_type == "research.plan":
            plan_output = Path(output_path) if output_path else PathRegistry(planspace).research_plan(section_number)
            self._research.execute_plan(
                section_number,
                planspace,
                codespace,
                plan_output,
            )
            return

        if task_type == "research.synthesis":
            self._handle_synthesis_completion(
                db_path, planspace, section_number, task,
                output_path, origin_refs, trigger_hash, cycle_id,
            )
            return

        if task_type == "research.verify":
            self._research.write_status(
                section_number, planspace, ResearchState.VERIFIED,
                detail="research verification complete",
                trigger_hash=trigger_hash, cycle_id=cycle_id,
            )

    def _handle_post_impl_assessment_completion(
        self,
        task: dict,
        status: str,
        planspace: Path,
    ) -> None:
        """Apply post-implementation assessment results on task completion."""

        task_type = str(task.get("task_type") or "")
        if task_type != "implementation.post_assessment" or status != TaskStatus.COMPLETE:
            return

        section_number = _section_number(task)
        if section_number is None:
            return

        evaluator = AssessmentEvaluator(
            artifact_io=self._artifact_io,
            prompt_guard=self._prompt_guard,
        )
        assessment = evaluator.read_post_impl_assessment(section_number, planspace)
        if assessment is None:
            return

        problem_ids = assessment.get("problem_ids_addressed")
        if not isinstance(problem_ids, list):
            problem_ids = []
        pattern_ids = assessment.get("pattern_ids_followed")
        if not isinstance(pattern_ids, list):
            pattern_ids = []
        profile_id = assessment.get("profile_id")
        if not isinstance(profile_id, str):
            profile_id = ""
        self._traceability_writer.update_trace_governance(
            planspace,
            section_number,
            problem_ids=[str(item) for item in problem_ids if str(item).strip()],
            pattern_ids=[str(item) for item in pattern_ids if str(item).strip()],
            profile_id=profile_id,
        )

        verdict = assessment.get("verdict", AssessmentVerdict.ACCEPT)
        if verdict == AssessmentVerdict.ACCEPT_WITH_DEBT:
            self._emit_risk_register_signal(section_number, planspace, assessment)
        elif verdict == AssessmentVerdict.REFACTOR_REQUIRED:
            self._emit_refactor_blocker(section_number, planspace, assessment)

    def _emit_risk_register_signal(
        self,
        section_number: str,
        planspace: Path,
        assessment: dict,
    ) -> None:
        """Emit a debt signal for downstream risk register handling."""
        paths = PathRegistry(planspace)
        payload = {
            "section": section_number,
            "source": "post_impl_assessment",
            "profile_id": assessment.get("profile_id", ""),
            "problem_ids": assessment.get("problem_ids_addressed", []),
            "pattern_ids": assessment.get("pattern_ids_followed", []),
            "debt_items": assessment.get("debt_items", []),
            "verdict": assessment.get("verdict", AssessmentVerdict.ACCEPT_WITH_DEBT),
        }
        self._artifact_io.write_json(paths.risk_register_signal(section_number), payload)

    def _emit_refactor_blocker(
        self,
        section_number: str,
        planspace: Path,
        assessment: dict,
    ) -> None:
        """Emit a blocker signal when post-implementation assessment fails."""
        paths = PathRegistry(planspace)
        reasons = assessment.get("refactor_reasons", [])
        if not isinstance(reasons, list):
            reasons = []
        detail = (
            "; ".join(str(reason).strip() for reason in reasons if str(reason).strip())
            or "post-implementation assessment requires a refactor pass"
        )
        payload = {
            "state": SIGNAL_NEED_DECISION,
            "blocker_type": "post_impl_refactor_required",
            "source": "post_impl_assessment",
            "section": section_number,
            "scope": f"section-{section_number}",
            "detail": detail,
            "why_blocked": detail,
            "needs": "Re-enter proposal/implementation loop with the flagged refactor reasons",
            "refactor_reasons": reasons,
            "profile_id": assessment.get("profile_id", ""),
            "problem_ids": assessment.get("problem_ids_addressed", []),
            "pattern_ids": assessment.get("pattern_ids_followed", []),
        }
        self._artifact_io.write_json(paths.post_impl_blocker_signal(section_number), payload)

    # ------------------------------------------------------------------
    # Verification / testing completion handlers
    # ------------------------------------------------------------------

    def _validate_findings_shape(self, findings: object) -> list[dict] | None:
        """Validate that findings is a list of dicts with required keys.

        Returns the validated list, or None if the shape is invalid.
        """
        if not isinstance(findings, list):
            return None
        required_keys = {"severity", "scope", "description"}
        for entry in findings:
            if not isinstance(entry, dict):
                return None
            if not required_keys.issubset(entry):
                return None
        return findings

    def _validate_test_results_shape(self, results: object) -> list[dict] | None:
        """Validate that test results is a list of dicts with required keys.

        Returns the validated list, or None if the shape is invalid.
        """
        if not isinstance(results, list):
            return None
        required_keys = {"test_name", "status"}
        for entry in results:
            if not isinstance(entry, dict):
                return None
            if not required_keys.issubset(entry):
                return None
        return results

    def _handle_verification_structural_completion(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
    ) -> None:
        """Handle verification.structural task completion.

        Reads structural findings JSON. If malformed (PAT-0001): rename and
        record as inconclusive. If findings with severity=="error" exist:
        queue verification.integration task (Item 27 mechanical gate check).
        Writes verification status signal.
        """
        task_type = str(task.get("task_type") or "")
        if task_type != "verification.structural":
            return

        section_number = _section_number(task)
        if section_number is None:
            return

        paths = PathRegistry(planspace)
        findings_path = paths.verification_structural(section_number)
        data = self._artifact_io.read_json(findings_path)

        # PAT-0001: malformed output = inconclusive (fail-closed)
        if data is None or not isinstance(data, dict):
            if findings_path.exists():
                self._artifact_io.rename_malformed(findings_path)
            self._artifact_io.write_json(
                paths.verification_status(section_number),
                {
                    "section": section_number,
                    "source": "verification.structural",
                    "status": "inconclusive",
                    "detail": "structural findings malformed or missing",
                },
            )
            return

        findings = self._validate_findings_shape(data.get("findings"))
        if findings is None:
            self._artifact_io.rename_malformed(findings_path)
            self._artifact_io.write_json(
                paths.verification_status(section_number),
                {
                    "section": section_number,
                    "source": "verification.structural",
                    "status": "inconclusive",
                    "detail": "structural findings schema invalid",
                },
            )
            return

        has_errors = any(
            f.get("severity") == "error" for f in findings
        )

        if has_errors:
            # Item 27: mechanical gate — queue integration verification
            env = FlowEnvelope(
                db_path=db_path,
                submitted_by="reconciler",
                flow_id=task.get("flow_id") or "",
                declared_by_task_id=int(task["id"]),
                origin_refs=[],
                planspace=planspace,
            )
            from flow.types.schema import TaskSpec

            self._flow_submitter.submit_chain(
                env,
                [
                    TaskSpec(
                        task_type="verification.integration",
                        concern_scope=f"section-{section_number}",
                    ),
                ],
            )

        status_value = "findings_local" if has_errors else "pass"
        self._artifact_io.write_json(
            paths.verification_status(section_number),
            {
                "section": section_number,
                "source": "verification.structural",
                "status": status_value,
                "finding_count": len(findings),
                "error_count": sum(
                    1 for f in findings if f.get("severity") == "error"
                ),
            },
        )

    def _handle_verification_integration_completion(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
    ) -> None:
        """Handle verification.integration task completion.

        Reads integration findings JSON. Writes findings as blocker signals
        with state=need_decision for cross-section issues.
        Advisory: does not block gate firing.
        """
        task_type = str(task.get("task_type") or "")
        if task_type != "verification.integration":
            return

        section_number = _section_number(task)
        if section_number is None:
            return

        paths = PathRegistry(planspace)
        findings_path = paths.verification_integration(section_number)
        data = self._artifact_io.read_json(findings_path)

        if data is None or not isinstance(data, dict):
            # Advisory — malformed integration findings are logged but
            # do not block.
            logger.warning(
                "verification.integration findings malformed for section %s",
                section_number,
            )
            return

        findings = self._validate_findings_shape(data.get("findings"))
        if findings is None:
            logger.warning(
                "verification.integration findings schema invalid for section %s",
                section_number,
            )
            return

        # Write cross-section findings as blocker signals
        cross_section_findings = [
            f for f in findings if f.get("scope") == "cross_section"
        ]
        if cross_section_findings:
            descriptions = [
                str(f.get("description", "")).strip()
                for f in cross_section_findings
                if str(f.get("description", "")).strip()
            ]
            detail = "; ".join(descriptions) or "cross-section integration findings"
            self._artifact_io.write_json(
                paths.verification_blocker_signal(section_number),
                {
                    "state": SIGNAL_NEED_DECISION,
                    "blocker_type": VERIFICATION_INTEGRATION_FAILURE,
                    "source": "verification.integration",
                    "section": section_number,
                    "scope": f"section-{section_number}",
                    "detail": detail,
                    "why_blocked": detail,
                    "needs": "coordination resolution for cross-section integration findings",
                    "finding_count": len(cross_section_findings),
                },
            )

    def _handle_testing_behavioral_completion(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
    ) -> None:
        """Handle testing.behavioral task completion.

        Reads test results JSON. If any test failed: queue testing.rca task.
        Gate: failing tests block.
        """
        task_type = str(task.get("task_type") or "")
        if task_type != "testing.behavioral":
            return

        section_number = _section_number(task)
        if section_number is None:
            return

        paths = PathRegistry(planspace)
        results_path = paths.testing_results(section_number)
        data = self._artifact_io.read_json(results_path)

        if data is None or not isinstance(data, dict):
            if results_path.exists():
                self._artifact_io.rename_malformed(results_path)
            # Fail-closed: malformed test output = blocking
            self._artifact_io.write_json(
                paths.testing_blocker_signal(section_number),
                {
                    "state": SIGNAL_NEED_DECISION,
                    "blocker_type": TEST_BEHAVIORAL_FAILURE,
                    "source": "testing.behavioral",
                    "section": section_number,
                    "scope": f"section-{section_number}",
                    "detail": "test results malformed or missing",
                    "why_blocked": "test results malformed or missing",
                    "needs": "re-run behavioral tests with valid output",
                },
            )
            return

        results = self._validate_test_results_shape(data.get("results"))
        if results is None:
            self._artifact_io.rename_malformed(results_path)
            self._artifact_io.write_json(
                paths.testing_blocker_signal(section_number),
                {
                    "state": SIGNAL_NEED_DECISION,
                    "blocker_type": TEST_BEHAVIORAL_FAILURE,
                    "source": "testing.behavioral",
                    "section": section_number,
                    "scope": f"section-{section_number}",
                    "detail": "test results schema invalid",
                    "why_blocked": "test results schema invalid",
                    "needs": "re-run behavioral tests with valid output",
                },
            )
            return

        failed_tests = [r for r in results if r.get("status") == "failed"]
        if failed_tests:
            # Queue RCA task for test failures
            env = FlowEnvelope(
                db_path=db_path,
                submitted_by="reconciler",
                flow_id=task.get("flow_id") or "",
                declared_by_task_id=int(task["id"]),
                origin_refs=[],
                planspace=planspace,
            )
            from flow.types.schema import TaskSpec

            self._flow_submitter.submit_chain(
                env,
                [
                    TaskSpec(
                        task_type="testing.rca",
                        concern_scope=f"section-{section_number}",
                    ),
                ],
            )

            test_names = [
                str(t.get("test_name", "")).strip()
                for t in failed_tests
                if str(t.get("test_name", "")).strip()
            ]
            detail = f"failing tests: {', '.join(test_names)}" if test_names else "behavioral tests failed"
            self._artifact_io.write_json(
                paths.testing_blocker_signal(section_number),
                {
                    "state": SIGNAL_NEED_DECISION,
                    "blocker_type": TEST_BEHAVIORAL_FAILURE,
                    "source": "testing.behavioral",
                    "section": section_number,
                    "scope": f"section-{section_number}",
                    "detail": detail,
                    "why_blocked": detail,
                    "needs": "fix failing tests or update test expectations",
                    "failed_test_count": len(failed_tests),
                },
            )

    def _handle_testing_rca_completion(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
    ) -> None:
        """Handle testing.rca task completion.

        Reads RCA findings. Routes as impl_problems (section-local) or
        coordination BlockerProblem (cross-section).
        Advisory: does not block.
        """
        task_type = str(task.get("task_type") or "")
        if task_type != "testing.rca":
            return

        section_number = _section_number(task)
        if section_number is None:
            return

        paths = PathRegistry(planspace)
        rca_path = paths.testing_rca_findings(section_number)
        data = self._artifact_io.read_json(rca_path)

        if data is None or not isinstance(data, dict):
            # Advisory — malformed RCA does not block.
            # The original test failure blocker signal remains.
            logger.warning(
                "testing.rca findings malformed for section %s",
                section_number,
            )
            return

        findings = data.get("findings")
        if not isinstance(findings, list):
            logger.warning(
                "testing.rca findings missing 'findings' list for section %s",
                section_number,
            )
            return

        cross_section = [
            f for f in findings
            if isinstance(f, dict) and f.get("scope") == "cross_section"
        ]
        if cross_section:
            descriptions = [
                str(f.get("description", "")).strip()
                for f in cross_section
                if str(f.get("description", "")).strip()
            ]
            detail = "; ".join(descriptions) or "cross-section RCA findings"
            self._artifact_io.write_json(
                paths.verification_blocker_signal(section_number),
                {
                    "state": SIGNAL_NEED_DECISION,
                    "blocker_type": TEST_BEHAVIORAL_FAILURE,
                    "source": "testing.rca",
                    "section": section_number,
                    "scope": f"section-{section_number}",
                    "detail": detail,
                    "why_blocked": detail,
                    "needs": "coordination resolution for cross-section test failure root cause",
                    "finding_count": len(cross_section),
                },
            )

        # Section-local findings written as impl_problems signal
        local = [
            f for f in findings
            if isinstance(f, dict) and f.get("scope") != "cross_section"
        ]
        if local:
            descriptions = [
                str(f.get("description", "")).strip()
                for f in local
                if str(f.get("description", "")).strip()
            ]
            self._artifact_io.write_json(
                paths.testing_rca_findings(section_number),
                {
                    "section": section_number,
                    "source": "testing.rca",
                    "local_findings": [
                        {
                            "description": str(f.get("description", "")),
                            "category": str(f.get("category", "")),
                            "file_paths": f.get("file_paths", []),
                        }
                        for f in local
                        if isinstance(f, dict)
                    ],
                },
            )

    def _handle_proposal_gate_synthesis(
        self,
        task: dict,
        status: str,
        planspace: Path,
        codespace: Path | None,
    ) -> None:
        """Handle proposal.gate_synthesis task completion.

        When the proposal gate fires (all proposal.section tasks complete),
        this handler loads proposal results from disk and writes a gate
        completion signal so the orchestrator can proceed to reconciliation.
        """
        task_type = str(task.get("task_type") or "")
        if task_type != PROPOSAL_GATE_SYNTHESIS_TYPE:
            return
        if status != TaskStatus.COMPLETE:
            return

        # Write a completion signal the orchestrator polls for.
        paths = PathRegistry(planspace)
        self._artifact_io.write_json(
            paths.signals_dir() / "proposal-gate-complete.json",
            {
                "status": "complete",
                "task_id": task.get("id"),
                "flow_id": task.get("flow_id") or "",
            },
        )

    # ------------------------------------------------------------------
    # Per-section fractal pipeline completion handlers
    # ------------------------------------------------------------------

    def _handle_section_propose_complete(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
    ) -> None:
        """Handle section.propose completion.

        Runs readiness resolution as script logic (mechanical check, not
        an agent).  The ReadinessResolver reads the proposal-state
        artifact that the proposer agent just wrote and decides whether
        the section is execution-ready.

        If ready, submits ``section.implement -> section.verify``.
        If blocked, publishes discoveries and routes blockers so
        coordination can resolve them.
        """
        task_type = str(task.get("task_type") or "")
        if task_type != "section.propose":
            return

        section_number = _section_number(task)
        if section_number is None:
            return

        paths = PathRegistry(planspace)

        # Run readiness resolution as script logic
        resolver = ReadinessResolver(artifact_io=self._artifact_io)
        readiness = resolver.resolve_readiness(planspace, section_number)

        if not readiness.ready or readiness.descent_required:
            logger.info(
                "section.propose for section %s: not ready "
                "(rationale=%s, blockers=%d), no follow-on chain submitted",
                section_number,
                readiness.rationale,
                len(readiness.blockers),
            )
            return

        # Section is ready -- submit implementation chain
        from flow.types.schema import TaskSpec as _TS

        concern_scope = f"section-{section_number}"
        payload_path = str(task.get("payload") or task.get("payload_path") or "")
        env = FlowEnvelope(
            db_path=db_path,
            submitted_by="reconciler",
            flow_id=task.get("flow_id") or "",
            declared_by_task_id=int(task["id"]),
            origin_refs=[],
            planspace=planspace,
        )
        self._flow_submitter.submit_chain(
            env,
            [
                _TS(
                    task_type="section.implement",
                    concern_scope=concern_scope,
                    payload_path=payload_path,
                    priority="normal",
                ),
                _TS(
                    task_type="section.verify",
                    concern_scope=concern_scope,
                    payload_path=payload_path,
                    priority="normal",
                ),
            ],
        )
        logger.info(
            "section.propose for section %s: ready, "
            "submitted section.implement -> section.verify chain",
            section_number,
        )

    def _handle_section_implement_complete(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
    ) -> dict:
        """Handle section.implement completion.

        After implementation succeeds, submit a verification task.
        The verification task is already in the chain submitted by
        ``_handle_section_propose_complete`` or
        ``_handle_section_readiness_complete``, so this handler only
        needs to run post-implementation bookkeeping -- specifically,
        writing a completion signal so the orchestrator can track
        progress.
        """
        task_type = str(task.get("task_type") or "")
        if task_type != "section.implement":
            return {}

        section_number = _section_number(task)
        if section_number is None:
            return {}

        paths = PathRegistry(planspace)
        self._artifact_io.write_json(
            paths.signals_dir() / f"section-{section_number}-impl-complete.json",
            {
                "section": section_number,
                "status": "complete",
                "task_id": task.get("id"),
                "flow_id": task.get("flow_id") or "",
            },
        )
        logger.info(
            "section.implement for section %s: complete, "
            "wrote impl-complete signal",
            section_number,
        )
        impl_feedback_path = paths.impl_feedback_surfaces(section_number)
        if not self._impl_feedback_detected_for_task(impl_feedback_path, int(task["id"])):
            return {}

        blocker_detail = (
            "Implementation discovered new value axes that are not yet covered "
            "by the problem definition or pending surfaces"
        )
        self._artifact_io.write_json(
            paths.blocker_signal(section_number),
            {
                "state": SIGNAL_NEED_DECISION,
                "blocker_type": "implementation_feedback",
                "source": "section.implement",
                "section": section_number,
                "scope": f"section-{section_number}",
                "detail": blocker_detail,
                "why_blocked": blocker_detail,
                "needs": "Re-enter proposal expansion with implementation feedback surfaces",
                "evidence": str(impl_feedback_path),
                "task_id": int(task["id"]),
            },
        )
        chain_id = str(task.get("chain_id") or "")
        if chain_id:
            cancel_chain_descendants(db_path, chain_id, int(task["id"]))
        logger.info(
            "section.implement for section %s: implementation feedback detected, "
            "blocked section and cancelled downstream descendants",
            section_number,
        )
        return {
            "implementation_feedback_detected": True,
            "blocker_type": "implementation_feedback",
            "blocked_reason": "implementation_feedback",
            "impl_feedback_surfaces_path": str(impl_feedback_path),
        }

    def _impl_feedback_detected_for_task(
        self,
        impl_feedback_path: Path,
        task_id: int,
    ) -> bool:
        data = self._artifact_io.read_json(impl_feedback_path)
        if not isinstance(data, dict):
            return False
        problem_surfaces = data.get("problem_surfaces")
        if not isinstance(problem_surfaces, list) or not problem_surfaces:
            return False
        task_marker = f"task {task_id}"
        for surface in problem_surfaces:
            if not isinstance(surface, dict):
                continue
            evidence = str(surface.get("evidence") or "")
            if task_marker in evidence:
                return True
        return False

    def _handle_section_readiness_complete(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
    ) -> None:
        """Handle section.readiness_check completion.

        If the section is execution-ready, submit the implementation
        chain: ``section.implement -> section.verify``.
        If blocked, the blocker signals were already emitted by the
        readiness gate during dispatch -- no follow-on submission.
        """
        task_type = str(task.get("task_type") or "")
        if task_type != "section.readiness_check":
            return

        section_number = _section_number(task)
        if section_number is None:
            return

        paths = PathRegistry(planspace)

        # Check if this section passed readiness.
        # The ReadinessResolver writes {"ready": bool, ...} to the artifact.
        readiness_path = paths.execution_ready(section_number)
        data = self._artifact_io.read_json(readiness_path)
        if (
            not isinstance(data, dict)
            or not data.get("ready", False)
            or data.get("descent_required", False)
        ):
            logger.info(
                "section.readiness_check for section %s: not ready, "
                "no follow-on chain submitted",
                section_number,
            )
            return

        # Section is ready -- submit implementation chain
        from flow.types.schema import TaskSpec as _TS

        concern_scope = f"section-{section_number}"
        env = FlowEnvelope(
            db_path=db_path,
            submitted_by="reconciler",
            flow_id=task.get("flow_id") or "",
            declared_by_task_id=int(task["id"]),
            origin_refs=[],
            planspace=planspace,
        )
        self._flow_submitter.submit_chain(
            env,
            [
                _TS(
                    task_type="section.implement",
                    concern_scope=concern_scope,
                    payload_path=str(task.get("payload") or task.get("payload_path") or ""),
                    priority="normal",
                ),
                _TS(
                    task_type="section.verify",
                    concern_scope=concern_scope,
                    payload_path=str(task.get("payload") or task.get("payload_path") or ""),
                    priority="normal",
                ),
            ],
        )
        logger.info(
            "section.readiness_check for section %s: ready, "
            "submitted section.implement -> section.verify chain",
            section_number,
        )

    @staticmethod
    def _read_task_output_json(
        planspace: Path,
        output_path: str | None,
        artifact_io: ArtifactIOService,
    ) -> dict | None:
        """Read a task output JSON file relative to planspace when needed."""
        if not output_path:
            return None
        output_file = Path(output_path)
        if not output_file.is_absolute():
            output_file = planspace / output_file
        data = artifact_io.read_json(output_file)
        return data if isinstance(data, dict) else None

    @staticmethod
    def _valid_child_specs(
        planspace: Path,
        children: list,
    ) -> list[dict[str, str]]:
        """Normalize child-spec entries emitted by section.decompose_children."""
        valid: list[dict[str, str]] = []
        for child in children:
            if not isinstance(child, dict):
                continue
            section_number = str(child.get("section_number") or "").strip()
            scope_grant = str(child.get("scope_grant") or "").strip()
            if not section_number or not scope_grant:
                continue
            spec_path_raw = str(child.get("spec_path") or "").strip()
            spec_path = (
                Path(spec_path_raw)
                if spec_path_raw
                else PathRegistry(planspace).section_spec(section_number)
            )
            if not spec_path.is_absolute():
                spec_path = planspace / spec_path
            if not spec_path.is_file():
                continue
            valid.append({
                "section_number": section_number,
                "scope_grant": scope_grant,
            })
        return valid

    def _handle_section_decompose_complete(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
        output_path: str | None,
    ) -> None:
        """Register child sections created by ``section.decompose_children``."""
        task_type = str(task.get("task_type") or "")
        if task_type != "section.decompose_children":
            return

        parent_number = _section_number(task)
        if parent_number is None:
            return

        data = self._read_task_output_json(
            planspace, output_path, self._artifact_io,
        )
        children = []
        if isinstance(data, dict):
            children = data.get("children", [])
        if not isinstance(children, list):
            children = []

        valid_children = self._valid_child_specs(planspace, children)
        if not valid_children:
            logger.warning(
                "section.decompose_children for section %s completed without "
                "valid child specs; parent remains in decomposing",
                parent_number,
            )
            return

        from orchestrator.engine.section_state_machine import (
            InvalidTransitionError,
            SectionEvent,
            SectionState,
            advance_section,
            get_section_depth,
            set_section_state,
        )

        parent_depth = get_section_depth(db_path, parent_number)
        for child in valid_children:
            set_section_state(
                db_path,
                child["section_number"],
                SectionState.PENDING,
                parent_section=parent_number,
                depth=parent_depth + 1,
                scope_grant=child["scope_grant"],
                spawned_by_state="decomposing",
            )

        try:
            advance_section(
                db_path,
                parent_number,
                SectionEvent.excerpt_complete,
                context={
                    "children_created": [
                        child["section_number"] for child in valid_children
                    ],
                },
            )
        except InvalidTransitionError:
            logger.debug(
                "section.decompose_children for section %s: parent not in "
                "decomposing when excerpt_complete fired",
                parent_number,
                exc_info=True,
            )
            return

        logger.info(
            "section.decompose_children for section %s: registered %d child "
            "section(s) and moved parent to awaiting_children",
            parent_number,
            len(valid_children),
        )

    def _advance_section_state_machine(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
        status: str,
        output_path: str | None,
        completion_context: dict | None = None,
    ) -> None:
        """Advance the section state machine based on task completion.

        Reads the task output to determine the appropriate event
        (success/failure/blocked) and calls ``advance_on_task_completion``.
        Gracefully skips if the state machine tables do not exist.
        """
        task_type = str(task.get("task_type") or "")
        section_number = _section_number(task)
        if section_number is None:
            return

        # Only handle section-scoped task types
        if not task_type.startswith("section."):
            return

        success = status == TaskStatus.COMPLETE
        context: dict = dict(completion_context or {})

        # Build context from task output for stateful section tasks.
        if task_type == "section.assess" and success and output_path:
            output_file = Path(output_path)
            if not output_file.is_absolute():
                output_file = planspace / output_file
            raw_output = self._artifact_io.read_if_exists(output_file)
            if raw_output:
                from staleness.helpers.verdict_parsers import parse_alignment_verdict

                verdict = parse_alignment_verdict(raw_output)
                if isinstance(verdict, dict):
                    context["aligned"] = bool(verdict.get("aligned", False))
                    if verdict.get("vertical_misalignment") is True:
                        context["vertical_misalignment"] = True
                    problems = verdict.get("problems")
                    if isinstance(problems, list) and problems:
                        context["problems"] = "\n".join(str(p) for p in problems)
                    elif isinstance(problems, str) and problems.strip():
                        context["problems"] = problems.strip()

        # Build context from task output for readiness checks
        if task_type == "section.readiness_check" and success:
            paths = PathRegistry(planspace)
            readiness_data = self._artifact_io.read_json(
                paths.execution_ready(section_number),
            )
            if isinstance(readiness_data, dict):
                context["ready"] = readiness_data.get("ready", False)
                context["descent_required"] = readiness_data.get(
                    "descent_required", False,
                )
                blockers = readiness_data.get("blockers", [])
                if blockers:
                    context["blockers"] = blockers

        # Build context from task output for risk evaluation
        if task_type == "section.risk_eval" and success:
            paths = PathRegistry(planspace)
            risk_plan = self._artifact_io.read_json(
                paths.risk_plan(f"section-{section_number}"),
            )
            if isinstance(risk_plan, dict):
                reopen_steps = risk_plan.get("reopen_steps")
                deferred_steps = risk_plan.get("deferred_steps")
                if isinstance(reopen_steps, list) and reopen_steps:
                    context["outcome"] = "reopened"
                elif isinstance(deferred_steps, list) and deferred_steps:
                    context["outcome"] = "deferred"
                else:
                    context["outcome"] = "accepted"
            else:
                context["outcome"] = "deferred"

        try:
            from orchestrator.engine.state_machine_orchestrator import (
                advance_on_task_completion,
            )

            new_state = advance_on_task_completion(
                db_path, section_number, task_type, success, context,
            )
            if new_state is not None:
                logger.info(
                    "State machine: section %s advanced to %s "
                    "(task_type=%s, success=%s)",
                    section_number, new_state, task_type, success,
                )
        except Exception:  # noqa: BLE001 — state machine is advisory
            # If the section_states table doesn't exist (pre-migration
            # runs), or any other error, log and continue.  The state
            # machine advancement is not critical to task completion.
            logger.debug(
                "State machine advance skipped for section %s "
                "(table may not exist yet)",
                section_number,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Scan codemap refinement completion handler (Piece 5D)
    # ------------------------------------------------------------------

    def _handle_codemap_refine_complete(
        self,
        task: dict,
        planspace: Path,
        output_path: str | None,
        db_path: Path | None = None,
    ) -> None:
        """Handle scan.codemap_refine completion.

        Reads the agent output from *output_path* and overwrites the
        section's codemap fragment at
        ``PathRegistry.section_codemap(section_number)``.

        After updating the fragment, writes a delta artifact at
        ``PathRegistry.codemap_delta(section_number)`` and, if the
        section has a ``parent_section`` in ``section_states``, merges
        the new entries additively into the parent's fragment (Piece 5E).

        This is the completion side of the any-state refinement
        mechanism.  The signal is dormant until an agent template
        actually emits the ``codemap-refine-{section}.json`` signal.
        """
        task_type = str(task.get("task_type") or "")
        if task_type != "scan.codemap_refine":
            return

        section_number = _section_number(task)
        if section_number is None:
            return

        if not output_path:
            logger.warning(
                "scan.codemap_refine for section %s completed "
                "without output_path — skipping fragment update",
                section_number,
            )
            return

        output_file = planspace / output_path if not Path(output_path).is_absolute() else Path(output_path)
        if not output_file.is_file():
            logger.warning(
                "scan.codemap_refine output file missing: %s",
                output_file,
            )
            return

        try:
            refined_text = output_file.read_text(encoding="utf-8")
        except OSError:
            logger.warning(
                "scan.codemap_refine could not read output: %s",
                output_file,
                exc_info=True,
            )
            return

        if not refined_text.strip():
            logger.info(
                "scan.codemap_refine for section %s produced empty "
                "output — keeping existing fragment",
                section_number,
            )
            return

        paths = PathRegistry(planspace)
        fragment_path = paths.section_codemap(section_number)
        fragment_path.parent.mkdir(parents=True, exist_ok=True)
        fragment_path.write_text(refined_text, encoding="utf-8")
        logger.info(
            "scan.codemap_refine: updated section %s codemap fragment "
            "at %s",
            section_number,
            fragment_path,
        )

        # --- Delta propagation (Piece 5E) ---
        # Write a delta artifact so parent sections can merge new entries.
        _write_codemap_delta(paths, section_number, refined_text)
        # If the section has a parent, merge the delta into the parent's
        # fragment immediately.  If the parent also has a parent, that
        # propagation happens on the next poll cycle.
        if db_path is not None:
            _propagate_delta_to_parent(paths, db_path, section_number, refined_text)

    # ------------------------------------------------------------------
    # Bootstrap task completion handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_sibling_global_task_complete(
        db_path: Path,
        sibling_task_type: str,
        flow_id: str,
    ) -> bool:
        """Check whether a sibling bootstrap task has completed within the same flow.

        Used for the parallel-join pattern: both ``bootstrap.explore_problems``
        and ``bootstrap.explore_values`` must finish before
        ``bootstrap.confirm_understanding`` can be submitted.
        """
        with task_db(db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM tasks "
                "WHERE task_type = ? AND flow_id = ? AND status = 'complete'",
                (sibling_task_type, flow_id),
            ).fetchone()
            return bool(row and row[0] > 0)

    def reconcile_task_completion(
        self,
        db_path: Path,
        planspace: Path,
        task_id: int,
        status: str,
        output_path: str | None,
        error: str | None = None,
        codespace: Path | None = None,
    ) -> None:
        """Called after a task completes or fails."""
        task = load_task(db_path, task_id)

        if task is None:
            logger.warning(
                "reconcile_task_completion called with unknown task_id=%d, skipping",
                task_id,
            )
            return
        instance_id = task["instance_id"] or ""
        flow_id = task["flow_id"] or ""
        chain_id = task["chain_id"] or ""
        task_type = task["task_type"] or ""
        continuation_path = task["continuation_path"]
        result_manifest_path = task["result_manifest_path"]
        result_envelope_path = task.get("result_envelope_path")
        result_envelope = self._result_projector.project(task, output_path, planspace)
        if error:
            result_envelope.error = error

        manifest = build_result_manifest(
            task_id=task_id,
            instance_id=instance_id,
            flow_id=flow_id,
            chain_id=chain_id,
            task_type=task_type,
            status=status,
            output_path=result_envelope.output_path,
            error=result_envelope.error,
        )

        if result_envelope_path:
            self._artifact_io.write_json(Path(result_envelope_path), result_envelope)
        else:
            self._artifact_io.write_json(
                PathRegistry(planspace).task_result_envelope(task_id),
                result_envelope,
            )
        if result_manifest_path:
            self._artifact_io.write_json(planspace / result_manifest_path, manifest)

        origin_refs = self._gate_repository.read_origin_refs(planspace, task_id)
        completion_context: dict = {}
        self._handle_research_completion(
            db_path, planspace, task, status, output_path, error, origin_refs, codespace,
        )
        self._handle_post_impl_assessment_completion(task, status, planspace)
        self._handle_proposal_gate_synthesis(task, status, planspace, codespace)
        if status == TaskStatus.COMPLETE:
            self._handle_verification_structural_completion(task, db_path, planspace)
            self._handle_verification_integration_completion(task, db_path, planspace)
            self._handle_testing_behavioral_completion(task, db_path, planspace)
            self._handle_testing_rca_completion(task, db_path, planspace)
            self._handle_section_propose_complete(task, db_path, planspace)
            completion_context.update(
                self._handle_section_implement_complete(task, db_path, planspace),
            )
            self._handle_section_readiness_complete(task, db_path, planspace)
            self._handle_section_decompose_complete(
                task, db_path, planspace, output_path,
            )
            self._handle_codemap_refine_complete(task, planspace, output_path, db_path=db_path)
            self._bootstrap_coordinator.handle_completion(task, db_path, planspace)

        # Advance the section state machine on task completion.
        # This is a best-effort update -- if the section_states table
        # does not exist (pre-state-machine runs), the advance is skipped.
        self._advance_section_state_machine(
            task, db_path, planspace, status, output_path, completion_context,
        )

        if status == TaskStatus.FAILED:
            if task_type == "scan.codemap_synthesize":
                self._bootstrap_coordinator.handle_codemap_synthesize_failed(
                    task, db_path, planspace,
                )
            if chain_id:
                self._fail_chain_gate(
                    db_path, planspace, chain_id, task_id,
                    result_manifest_path, flow_id, origin_refs,
                )
            return

        if status != TaskStatus.COMPLETE:
            return

        continuation, is_malformed = self._load_continuation(planspace, continuation_path)
        if is_malformed:
            if chain_id:
                self._fail_chain_gate(
                    db_path, planspace, chain_id, task_id,
                    result_manifest_path, flow_id, origin_refs,
                )
            return

        if continuation is not None and continuation.actions:
            self._process_continuation_actions(
                db_path, continuation, task_id, flow_id, chain_id, origin_refs, planspace,
            )
        elif chain_id:
            self._complete_chain_gate(
                db_path, planspace, chain_id, task_id,
                result_manifest_path, flow_id, origin_refs,
            )

    def check_and_fire_gate(
        self,
        db_path: Path,
        planspace: Path,
        gate_id: str,
        flow_id: str,
        origin_refs: list[str],
    ) -> None:
        """Check if all gate members are terminal and fire the gate if so."""
        self._gate_repository.check_and_fire_gate(
            db_path, planspace, gate_id, flow_id, origin_refs,
            build_gate_aggregate_manifest,
        )
