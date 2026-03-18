"""StrategicStateBuilder: derive and persist strategic-state snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from containers import ArtifactIOService

from orchestrator.path_registry import PathRegistry
from orchestrator.repository.decisions import (
    DECISION_SCOPE_GLOBAL,
    DECISION_STATUS_DECIDED,
    Decisions,
)
from risk.repository.serialization import RiskSerializer
from risk.types import StepDecision
from signals.types import SIGNAL_NEEDS_PARENT, TRUNCATE_DETAIL


@dataclass(frozen=True)
class RiskSummary:
    """Structured result from ``_read_risk_summary``."""

    posture: str | None = None
    mitigations: list[str] = field(default_factory=list)
    has_plan: bool = False


@dataclass
class _SectionTally:
    """Mutable accumulators filled while iterating over section results."""

    completed: list[str] = field(default_factory=list)
    in_progress: str | None = None
    blocked: dict[str, dict[str, str]] = field(default_factory=dict)
    open_problems: list[dict[str, str]] = field(default_factory=list)
    risk_posture: dict[str, str] = field(default_factory=dict)
    dominant_risks_by_section: dict[str, list[str]] = field(default_factory=dict)
    blocked_by_risk: list[str] = field(default_factory=list)


class StrategicStateBuilder:
    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io
        self._decisions = Decisions(artifact_io=artifact_io)
        self._serializer = RiskSerializer(artifact_io=artifact_io)

    def build_strategic_state(
        self,
        decisions_dir: Path,
        section_results: dict[str, Any],
        planspace: Path,
    ) -> dict[str, Any]:
        """Derive the current strategic-state snapshot."""
        decision_warnings: list[str] = []
        decisions = self._decisions.load_decisions(decisions_dir, warnings=decision_warnings)

        research_questions = self._load_research_questions(planspace)

        tally = self._classify_sections(section_results, planspace)
        _append_child_problems(decisions, tally.open_problems)

        key_decision_ids = [
            decision.id for decision in decisions if decision.status == DECISION_STATUS_DECIDED
        ]
        coordination_rounds = sum(
            1 for decision in decisions if decision.scope == DECISION_SCOPE_GLOBAL
        )

        snapshot: dict[str, Any] = {
            "completed_sections": sorted(tally.completed),
            "in_progress": tally.in_progress,
            "blocked": tally.blocked,
            "open_problems": tally.open_problems,
            "research_questions": research_questions,
            "key_decisions": key_decision_ids,
            "coordination_rounds": coordination_rounds,
            "risk_posture": tally.risk_posture,
            "dominant_risks_by_section": tally.dominant_risks_by_section,
            "blocked_by_risk": tally.blocked_by_risk,
            "next_action": _derive_next_action(
                tally.completed,
                tally.in_progress,
                tally.blocked,
                tally.open_problems,
            ),
        }
        if decision_warnings:
            snapshot["warnings"] = decision_warnings

        state_path = PathRegistry(planspace).strategic_state()
        self._artifact_io.write_json(state_path, snapshot)
        return snapshot

    def update_section_completion(
        self,
        planspace: Path,
        section_number: str,
        section_result: Any,
    ) -> dict[str, Any]:
        """Incrementally update the strategic state for a single section completion.

        Reads the current strategic-state snapshot, updates the fields
        affected by *section_number* (adds to completed, removes from
        blocked, updates risk posture), and writes the updated snapshot.

        If no existing snapshot is found, falls back to a minimal
        initial state.  This is called by the flow reconciler's task
        completion handler so strategic state stays fresh after every
        section completion — no global batch rebuild needed.
        """
        paths = PathRegistry(planspace)
        state_path = paths.strategic_state()
        existing = self._artifact_io.read_json(state_path)
        if not isinstance(existing, dict):
            existing = {
                "completed_sections": [],
                "in_progress": None,
                "blocked": {},
                "open_problems": [],
                "research_questions": [],
                "key_decisions": [],
                "coordination_rounds": 0,
                "risk_posture": {},
                "dominant_risks_by_section": {},
                "blocked_by_risk": [],
                "next_action": None,
            }

        # Determine the section's new classification
        if isinstance(section_result, dict):
            aligned = section_result.get("aligned", False)
        else:
            aligned = getattr(section_result, "aligned", False)

        completed = list(existing.get("completed_sections", []))
        blocked = dict(existing.get("blocked", {}))
        risk_posture = dict(existing.get("risk_posture", {}))
        dominant_risks = dict(existing.get("dominant_risks_by_section", {}))
        blocked_by_risk = list(existing.get("blocked_by_risk", []))

        # Remove from blocked if previously blocked
        blocked.pop(section_number, None)
        if section_number in blocked_by_risk:
            blocked_by_risk.remove(section_number)

        if aligned:
            if section_number not in completed:
                completed.append(section_number)
        else:
            # Check if now blocked
            blocker_detected = self._check_blocker(planspace, section_number, _SectionTally())
            if blocker_detected:
                blocker_path = paths.blocker_signal(section_number)
                blocker = self._artifact_io.read_json(blocker_path)
                if blocker is None:
                    blocked[section_number] = {
                        "problem_id": "",
                        "reason": "blocker signal malformed",
                    }
                elif isinstance(blocker, dict) and blocker.get("state") == SIGNAL_NEEDS_PARENT:
                    blocked[section_number] = {
                        "problem_id": blocker.get("problem_id", ""),
                        "reason": blocker.get("detail", "")[:TRUNCATE_DETAIL],
                    }

        # Update risk posture for this section
        risk_summary = _read_risk_summary(planspace, section_number, self._serializer)
        if risk_summary.posture is not None:
            risk_posture[section_number] = risk_summary.posture
        if risk_summary.mitigations:
            dominant_risks[section_number] = risk_summary.mitigations
        if risk_summary.has_plan and section_number not in blocked_by_risk:
            blocked_by_risk.append(section_number)

        existing["completed_sections"] = sorted(completed)
        existing["blocked"] = blocked
        existing["risk_posture"] = risk_posture
        existing["dominant_risks_by_section"] = dominant_risks
        existing["blocked_by_risk"] = blocked_by_risk

        # Recompute in_progress (first non-completed, non-blocked section
        # is not derivable from a single-section update, so preserve
        # the existing value unless it was this section)
        if existing.get("in_progress") == section_number and aligned:
            existing["in_progress"] = None

        existing["next_action"] = _derive_next_action(
            existing["completed_sections"],
            existing.get("in_progress"),
            existing["blocked"],
            existing.get("open_problems", []),
        )

        self._artifact_io.write_json(state_path, existing)
        return existing

    def _classify_sections(
        self,
        section_results: dict[str, Any],
        planspace: Path,
    ) -> _SectionTally:
        """Walk *section_results* and bucket each section."""
        tally = _SectionTally()

        for sec_num, result in sorted(section_results.items()):
            if isinstance(result, dict):
                aligned = result.get("aligned", False)
                problems = result.get("problems")
            else:
                aligned = getattr(result, "aligned", False)
                problems = getattr(result, "problems", None)

            _accumulate_risk(planspace, sec_num, tally, self._serializer)

            if aligned:
                tally.completed.append(sec_num)
                continue

            if self._check_blocker(planspace, sec_num, tally):
                continue

            if tally.in_progress is None:
                tally.in_progress = sec_num
            tally.open_problems.append({
                "id": f"p-{sec_num}",
                "scope": f"section-{sec_num}",
                "summary": (str(problems)[:TRUNCATE_DETAIL] if problems else "unresolved"),
            })

        return tally

    def _check_blocker(
        self,
        planspace: Path,
        sec_num: str,
        tally: _SectionTally,
    ) -> bool:
        """Return ``True`` (and update *tally*) when *sec_num* is blocked."""
        blocker_path = PathRegistry(planspace).blocker_signal(sec_num)
        if not blocker_path.exists():
            return False
        blocker = self._artifact_io.read_json(blocker_path)
        if blocker is None:
            tally.blocked[sec_num] = {
                "problem_id": "",
                "reason": "blocker signal malformed",
            }
            return True
        if blocker.get("state") == SIGNAL_NEEDS_PARENT:
            tally.blocked[sec_num] = {
                "problem_id": blocker.get("problem_id", ""),
                "reason": blocker.get("detail", "")[:TRUNCATE_DETAIL],
            }
            return True
        return False

    @staticmethod
    def _list_research_question_artifacts(open_problems_dir: Path) -> list[Path]:
        """Named listing helper for research-question artifacts (PAT-0003)."""
        if not open_problems_dir.is_dir():
            return []
        return sorted(open_problems_dir.glob("section-*-research-questions.json"))

    def _load_research_questions(self, planspace: Path) -> list[dict[str, Any]]:
        open_problems_dir = PathRegistry(planspace).open_problems_dir()
        aggregated: list[dict[str, Any]] = []
        for artifact_path in self._list_research_question_artifacts(open_problems_dir):
            artifact = self._artifact_io.read_json(artifact_path)
            if not isinstance(artifact, dict):
                continue

            section = str(artifact.get("section", "")).strip()
            source = str(artifact.get("source", "")).strip()
            raw_questions = artifact.get("research_questions", [])
            if not isinstance(raw_questions, list):
                continue

            questions = [str(question) for question in raw_questions]
            if not questions:
                continue

            aggregated.append({
                "section": section,
                "research_questions": questions,
                "source": source,
            })
        return aggregated


# Pure functions — no Services usage

def _accumulate_risk(
    planspace: Path,
    sec_num: str,
    tally: _SectionTally,
    serializer: RiskSerializer,
) -> None:
    """Read risk artifacts for *sec_num* and update *tally* in-place."""
    risk_summary = _read_risk_summary(planspace, sec_num, serializer)
    if risk_summary.posture is not None:
        tally.risk_posture[sec_num] = risk_summary.posture
    if risk_summary.mitigations:
        tally.dominant_risks_by_section[sec_num] = risk_summary.mitigations
    if risk_summary.has_plan:
        tally.blocked_by_risk.append(sec_num)


def _append_child_problems(
    decisions: list[Any],
    open_problems: list[dict[str, str]],
) -> None:
    """Add child problems from decisions that aren't already tracked."""
    for decision in decisions:
        for child in decision.new_child_problems:
            if not any(problem["id"] == child for problem in open_problems):
                open_problems.append({
                    "id": child,
                    "scope": (
                        f"section-{decision.section}"
                        if decision.section else DECISION_SCOPE_GLOBAL
                    ),
                    "summary": f"child problem from {decision.id}",
                })


def _derive_next_action(
    completed: list[str],
    in_progress: str | None,
    blocked: dict[str, dict[str, str]],
    open_problems: list[dict[str, str]],
) -> str | None:
    if blocked:
        first_blocked = next(iter(blocked))
        return f"resolve blocker for section {first_blocked}"
    if in_progress:
        return f"section-{in_progress} alignment check"
    if open_problems:
        return f"address open problem: {open_problems[0]['id']}"
    if completed:
        return "all sections complete — ready for coordination"
    return None


def _read_risk_summary(
    planspace: Path,
    sec_num: str,
    serializer: RiskSerializer,
) -> RiskSummary:
    paths = PathRegistry(planspace)
    scope = f"section-{sec_num}"
    assessment = serializer.load_risk_assessment(paths.risk_assessment(scope))
    plan = serializer.load_risk_plan(paths.risk_plan(scope))

    posture = None
    blocked_by_risk = False
    if plan is not None:
        postures = [
            decision.posture
            for decision in plan.step_decisions
            if decision.posture is not None
        ]
        if postures:
            posture = max(postures, key=lambda p: p.rank).value
        blocked_by_risk = bool(plan.step_decisions) and not any(
            decision.decision == StepDecision.ACCEPT
            for decision in plan.step_decisions
        )

    dominant_risks = (
        [risk.value for risk in assessment.dominant_risks]
        if assessment is not None
        else []
    )
    return RiskSummary(posture=posture, mitigations=dominant_risks, has_plan=blocked_by_risk)
