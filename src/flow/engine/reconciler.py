"""Flow completion reconciliation helpers."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from flow.service.task_db_client import load_task, log_bootstrap_stage, log_event, task_db, update_task_dependency
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
    SIGNAL_NEEDS_PARENT,
    VERIFICATION_STRUCTURAL_FAILURE,
    VERIFICATION_INTEGRATION_FAILURE,
    TEST_BEHAVIORAL_FAILURE,
)

if TYPE_CHECKING:
    from containers import ArtifactIOService, PromptGuard, ResearchOrchestratorService
    from flow.engine.flow_submitter import FlowSubmitter
    from flow.repository.gate_repository import GateRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bootstrap task completion constants
# ---------------------------------------------------------------------------

# Maps each bootstrap task type to its follow-on task(s).
# A single string means one follow-on chain step; a list means parallel
# fan-out (each entry becomes an independent chain).  The special value
# ``_JOIN`` indicates a convergence point where *both* sibling tasks
# must complete before the follow-on fires.
_JOIN = "__join__"

_GLOBAL_FOLLOW_ON: dict[str, str | list[str] | tuple[str, str]] = {
    "bootstrap.classify_entry": ["bootstrap.extract_problems", "bootstrap.extract_values"],
    "bootstrap.extract_problems": "bootstrap.explore_problems",
    "bootstrap.extract_values": "bootstrap.explore_values",
    "bootstrap.explore_problems": (_JOIN, "bootstrap.confirm_understanding"),
    "bootstrap.explore_values": (_JOIN, "bootstrap.confirm_understanding"),
    "bootstrap.confirm_understanding": "bootstrap.assess_reliability",
    # assess_reliability branches on risk — handled with special logic
    "bootstrap.decompose": "bootstrap.align_proposal",
    # align_proposal branches on alignment — handled with special logic
    "bootstrap.expand_proposal": "bootstrap.align_proposal",
    "bootstrap.explore_factors": "bootstrap.align_proposal",
    "bootstrap.build_codemap": "bootstrap.explore_sections",
    "bootstrap.explore_sections": "bootstrap.discover_substrate",
    # discover_substrate is terminal — it initialises per-section state
}

# Sibling pairs for the join: when one finishes, check whether the other
# is already complete before submitting the join target.
_JOIN_SIBLINGS: dict[str, str] = {
    "bootstrap.explore_problems": "bootstrap.explore_values",
    "bootstrap.explore_values": "bootstrap.explore_problems",
}

# Maximum number of expand_proposal -> align_proposal loops before the
# circuit breaker trips and we fall through to build_codemap anyway.
_EXPANSION_LOOP_MAX = 3

# Maps bootstrap task types to their structured artifact paths (relative to planspace).
_BOOTSTRAP_ARTIFACT_PATHS: dict[str, str] = {
    "bootstrap.assess_reliability": "artifacts/global/reliability-assessment.json",
    "bootstrap.align_proposal": "artifacts/global/proposal-alignment.json",
}


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


class Reconciler:
    def __init__(
        self,
        artifact_io: ArtifactIOService,
        research: ResearchOrchestratorService,
        prompt_guard: PromptGuard,
        flow_submitter: FlowSubmitter,
        gate_repository: GateRepository,
        traceability_writer: TraceabilityWriter,
    ) -> None:
        self._artifact_io = artifact_io
        self._research = research
        self._prompt_guard = prompt_guard
        self._flow_submitter = flow_submitter
        self._gate_repository = gate_repository
        self._traceability_writer = traceability_writer

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
                )
                if new_ids:
                    update_task_dependency(db_path, new_ids[0], task_id)
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
            "state": SIGNAL_NEEDS_PARENT,
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
        with state=needs_parent for cross-section issues.
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
                    "state": SIGNAL_NEEDS_PARENT,
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
                    "state": SIGNAL_NEEDS_PARENT,
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
                    "state": SIGNAL_NEEDS_PARENT,
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
                    "state": SIGNAL_NEEDS_PARENT,
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
                    "state": SIGNAL_NEEDS_PARENT,
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

        if not readiness.ready:
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
    ) -> None:
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
            return

        section_number = _section_number(task)
        if section_number is None:
            return

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
        if not isinstance(data, dict) or not data.get("ready", False):
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

    def _advance_section_state_machine(
        self,
        task: dict,
        db_path: Path,
        planspace: Path,
        status: str,
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
        context: dict = {}

        # Build context from task output for readiness checks
        if task_type == "section.readiness_check" and success:
            paths = PathRegistry(planspace)
            readiness_data = self._artifact_io.read_json(
                paths.execution_ready(section_number),
            )
            if isinstance(readiness_data, dict):
                context["ready"] = readiness_data.get("ready", False)
                blockers = readiness_data.get("blockers", [])
                if blockers:
                    context["blockers"] = blockers

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

    @staticmethod
    def _count_expansion_loops(db_path: Path, flow_id: str) -> int:
        """Count how many ``bootstrap.expand_proposal`` tasks have completed
        in this flow, to enforce the circuit breaker."""
        with task_db(db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM tasks "
                "WHERE task_type = 'bootstrap.expand_proposal' "
                "AND flow_id = ? AND status = 'complete'",
                (flow_id,),
            ).fetchone()
            return row[0] if row else 0

    def _submit_global_follow_on(
        self,
        db_path: Path,
        planspace: Path | None,
        task: dict,
        follow_on_type: str,
    ) -> None:
        """Submit a single follow-on bootstrap task as a new chain step.

        Includes a dedup guard: if a pending or running task of the same
        task_type + flow_id already exists, the submission is skipped.
        """
        from flow.types.schema import TaskSpec as _TS

        flow_id = task.get("flow_id") or ""

        # Dedup guard: skip if an active task of this type already exists
        with task_db(db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM tasks "
                "WHERE task_type = ? AND flow_id = ? "
                "AND status IN ('pending', 'running')",
                (follow_on_type, flow_id),
            ).fetchone()
            if row and row[0] > 0:
                logger.info(
                    "dedup: skipping %s — already pending/running in flow %s",
                    follow_on_type, flow_id,
                )
                return

        env = FlowEnvelope(
            db_path=db_path,
            submitted_by="reconciler",
            flow_id=flow_id,
            declared_by_task_id=int(task["id"]),
            origin_refs=[],
            planspace=planspace,
        )
        payload = task.get("payload_path") or ""
        self._flow_submitter.submit_chain(
            env,
            [_TS(
                task_type=follow_on_type,
                concern_scope="bootstrap",
                payload_path=payload,
                priority="normal",
            )],
        )

    def _submit_global_fanout(
        self,
        db_path: Path,
        planspace: Path | None,
        task: dict,
        task_types: list[str],
    ) -> None:
        """Submit parallel bootstrap tasks as independent chain branches.

        Includes a dedup guard: any task_type that already has a pending
        or running instance in the same flow is excluded from the fanout.
        If all types are already active, nothing is submitted.
        """
        from flow.types.schema import BranchSpec as _BS, TaskSpec as _TS

        flow_id = task.get("flow_id") or ""

        # Dedup guard: filter out types that already have active tasks
        with task_db(db_path) as conn:
            filtered_types: list[str] = []
            for tt in task_types:
                row = conn.execute(
                    "SELECT COUNT(*) FROM tasks "
                    "WHERE task_type = ? AND flow_id = ? "
                    "AND status IN ('pending', 'running')",
                    (tt, flow_id),
                ).fetchone()
                if row and row[0] > 0:
                    logger.info(
                        "dedup: skipping %s — already pending/running in flow %s",
                        tt, flow_id,
                    )
                else:
                    filtered_types.append(tt)
        if not filtered_types:
            return

        env = FlowEnvelope(
            db_path=db_path,
            submitted_by="reconciler",
            flow_id=flow_id,
            declared_by_task_id=int(task["id"]),
            origin_refs=[],
            planspace=planspace,
        )
        payload = task.get("payload_path") or ""
        branches = [
            _BS(
                label=tt.split(".")[-1],
                steps=[_TS(
                    task_type=tt,
                    concern_scope="bootstrap",
                    payload_path=payload,
                    priority="normal",
                )],
            )
            for tt in task_types
        ]
        self._flow_submitter.submit_fanout(env, branches)

    def _handle_global_task_completion(
        self,
        task: dict,
        db_path: Path,
        planspace: Path | None,
    ) -> None:
        """Handle completion of a ``bootstrap.*`` task.

        Looks up the follow-on task(s) for the completed task type and
        submits them through the FlowSubmitter.  Handles three patterns:

        1. **Linear chain**: one task follows another.
        2. **Fan-out**: one task spawns multiple parallel tasks
           (classify_entry -> extract_problems + extract_values).
        3. **Join**: a follow-on fires only when *both* parallel siblings
           have completed (explore_problems + explore_values ->
           confirm_understanding).

        Special branching logic applies to ``assess_reliability``
        (high risk -> decompose, low risk -> align_proposal) and
        ``align_proposal`` (aligned -> build_codemap, misaligned ->
        expand_proposal with circuit breaker).
        """
        task_type = str(task.get("task_type") or "")
        if not task_type.startswith("bootstrap."):
            return

        flow_id = task.get("flow_id") or ""
        output_path = task.get("output_path") or ""

        log_event(
            db_path,
            kind="global_task_complete",
            tag=task_type,
            body=json.dumps({"task_id": task.get("id"), "flow_id": flow_id}),
        )

        # --- assess_reliability: branch on recommendation ---
        if task_type == "bootstrap.assess_reliability":
            recommendation = self._read_global_output_field(
                planspace, task_type, "recommendation",
            )
            if recommendation == "decompose":
                self._submit_global_follow_on(db_path, planspace, task, "bootstrap.decompose")
            else:
                self._submit_global_follow_on(db_path, planspace, task, "bootstrap.align_proposal")
            return

        # --- align_proposal: branch on alignment verdict ---
        if task_type == "bootstrap.align_proposal":
            aligned = self._read_global_output_field(
                planspace, task_type, "aligned",
            )
            if aligned is True or aligned == "true":
                self._submit_global_follow_on(db_path, planspace, task, "bootstrap.build_codemap")
            else:
                # Circuit breaker: cap expand -> align loops
                loop_count = self._count_expansion_loops(db_path, flow_id)
                if loop_count >= _EXPANSION_LOOP_MAX:
                    logger.warning(
                        "bootstrap.align_proposal: expansion loop circuit breaker "
                        "tripped after %d loops (flow_id=%s), proceeding to "
                        "build_codemap",
                        loop_count, flow_id,
                    )
                    self._submit_global_follow_on(db_path, planspace, task, "bootstrap.build_codemap")
                else:
                    self._submit_global_follow_on(db_path, planspace, task, "bootstrap.expand_proposal")
            return

        # --- discover_substrate: terminal — initialize section states ---
        if task_type == "bootstrap.discover_substrate":
            self._initialize_section_states_from_artifacts(db_path, planspace)
            log_bootstrap_stage(db_path, "bootstrap", "completed")
            log_event(
                db_path,
                kind="global_bootstrap_complete",
                tag="discover_substrate",
                body=json.dumps({"flow_id": flow_id}),
            )
            return

        # --- Standard follow-on lookup ---
        follow_on = _GLOBAL_FOLLOW_ON.get(task_type)
        if follow_on is None:
            logger.debug(
                "bootstrap task %s has no follow-on mapping, skipping",
                task_type,
            )
            return

        # Fan-out: list of task types to submit in parallel
        if isinstance(follow_on, list):
            self._submit_global_fanout(db_path, planspace, task, follow_on)
            return

        # Join: tuple of (_JOIN, target_type)
        if isinstance(follow_on, tuple) and follow_on[0] == _JOIN:
            target_type = follow_on[1]
            sibling_type = _JOIN_SIBLINGS.get(task_type)
            if sibling_type and not self._is_sibling_global_task_complete(
                db_path, sibling_type, flow_id,
            ):
                logger.info(
                    "bootstrap join: %s complete but sibling %s not yet done, "
                    "deferring %s",
                    task_type, sibling_type, target_type,
                )
                return
            self._submit_global_follow_on(db_path, planspace, task, target_type)
            return

        # Simple linear follow-on
        if isinstance(follow_on, str):
            self._submit_global_follow_on(db_path, planspace, task, follow_on)

    def _read_global_output_field(
        self,
        planspace: Path | None,
        task_type: str,
        field: str,
    ) -> object:
        """Read a single field from a bootstrap task's structured artifact JSON.

        Looks up the artifact path from ``_BOOTSTRAP_ARTIFACT_PATHS`` by
        *task_type*, prepends *planspace*, and reads the JSON.
        Returns ``None`` if the artifact path is unknown, the file cannot be
        read, or the field is absent.
        """
        rel_path = _BOOTSTRAP_ARTIFACT_PATHS.get(task_type)
        if not rel_path or planspace is None:
            return None
        full_path = planspace / rel_path
        data = self._artifact_io.read_json(full_path)
        if isinstance(data, dict):
            return data.get(field)
        return None

    def _initialize_section_states_from_artifacts(
        self,
        db_path: Path,
        planspace: Path | None,
    ) -> None:
        """Read section files and create ``section_states`` rows.

        Called when ``bootstrap.discover_substrate`` completes (the
        terminal bootstrap task).  Each ``section-NN.md`` found in the
        artifacts sections directory gets a row in ``section_states``
        with state ``pending`` so the ``StateMachineOrchestrator`` can
        pick up.

        Idempotent: existing rows are not overwritten.
        """
        from orchestrator.engine.section_state_machine import (
            SectionState,
            set_section_state,
            get_section_state,
        )

        if planspace is None:
            logger.warning(
                "Cannot initialize section states: planspace is None"
            )
            return

        paths = PathRegistry(planspace)
        sections_dir = paths.sections_dir()

        if not sections_dir.is_dir():
            logger.warning(
                "Cannot initialize section states: sections dir not found: %s",
                sections_dir,
            )
            return

        section_files = sorted(sections_dir.glob("section-*.md"))
        if not section_files:
            logger.warning(
                "No section files found in %s — skipping section state init",
                sections_dir,
            )
            return

        initialized = 0
        for section_file in section_files:
            # Extract section number: "section-01.md" -> "01"
            stem = section_file.stem  # "section-01"
            parts = stem.split("-", 1)
            if len(parts) < 2:
                continue
            section_number = parts[1]

            # Idempotent: only create if row does not exist
            current = get_section_state(db_path, section_number)
            with task_db(db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM section_states WHERE section_number=?",
                    (section_number,),
                ).fetchone()
            if row is None:
                set_section_state(db_path, section_number, SectionState.PENDING)
                initialized += 1

        logger.info(
            "Bootstrap complete: initialized %d section state(s) from %d "
            "section file(s)",
            initialized,
            len(section_files),
        )

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

        manifest = build_result_manifest(
            task_id=task_id,
            instance_id=instance_id,
            flow_id=flow_id,
            chain_id=chain_id,
            task_type=task_type,
            status=status,
            output_path=output_path,
            error=error,
        )

        if result_manifest_path:
            self._artifact_io.write_json(planspace / result_manifest_path, manifest)

        origin_refs = self._gate_repository.read_origin_refs(planspace, task_id)
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
            self._handle_section_implement_complete(task, db_path, planspace)
            self._handle_section_readiness_complete(task, db_path, planspace)
            self._handle_global_task_completion(task, db_path, planspace)

        # Advance the section state machine on task completion.
        # This is a best-effort update -- if the section_states table
        # does not exist (pre-state-machine runs), the advance is skipped.
        self._advance_section_state_machine(
            task, db_path, planspace, status,
        )

        if status == TaskStatus.FAILED:
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
