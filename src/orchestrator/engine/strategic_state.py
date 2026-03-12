"""StrategicStateBuilder: derive and persist strategic-state snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from signals.repository.artifact_io import read_json, write_json
from orchestrator.path_registry import PathRegistry
from orchestrator.repository.decisions import load_decisions
from risk.repository.serialization import (
    load_risk_assessment,
    load_risk_plan,
)
from risk.types import PostureProfile, StepDecision


def build_strategic_state(
    decisions_dir: Path,
    section_results: dict[str, Any],
    planspace: Path | None = None,
) -> dict[str, Any]:
    """Derive the current strategic-state snapshot."""
    decision_warnings: list[str] = []
    decisions = load_decisions(decisions_dir, warnings=decision_warnings)

    completed: list[str] = []
    in_progress: str | None = None
    blocked: dict[str, dict[str, str]] = {}
    open_problems: list[dict[str, str]] = []
    research_questions: list[dict[str, Any]] = []
    risk_posture: dict[str, str] = {}
    dominant_risks_by_section: dict[str, list[str]] = {}
    blocked_by_risk: list[str] = []

    if planspace is not None:
        research_questions = _load_research_questions(planspace)

    for sec_num, result in sorted(section_results.items()):
        if isinstance(result, dict):
            aligned = result.get("aligned", False)
            problems = result.get("problems")
        else:
            aligned = getattr(result, "aligned", False)
            problems = getattr(result, "problems", None)

        if planspace is not None:
            posture, dominant_risks, risk_blocked = _read_risk_summary(
                planspace,
                sec_num,
            )
            if posture is not None:
                risk_posture[sec_num] = posture
            if dominant_risks:
                dominant_risks_by_section[sec_num] = dominant_risks
            if risk_blocked:
                blocked_by_risk.append(sec_num)

        if aligned:
            completed.append(sec_num)
            continue

        if planspace is not None:
            blocker_path = PathRegistry(planspace).blocker_signal(sec_num)
            if blocker_path.exists():
                blocker = read_json(blocker_path)
                if blocker is None:
                    blocked[sec_num] = {
                        "problem_id": "",
                        "reason": "blocker signal malformed",
                    }
                    continue
                if blocker.get("state") == "needs_parent":
                    blocked[sec_num] = {
                        "problem_id": blocker.get("problem_id", ""),
                        "reason": blocker.get("detail", "")[:200],
                    }
                    continue

        if in_progress is None:
            in_progress = sec_num
        open_problems.append({
            "id": f"p-{sec_num}",
            "scope": f"section-{sec_num}",
            "summary": (str(problems)[:200] if problems else "unresolved"),
        })

    key_decision_ids = [
        decision.id for decision in decisions if decision.status == "decided"
    ]
    for decision in decisions:
        for child in decision.new_child_problems:
            if not any(problem["id"] == child for problem in open_problems):
                open_problems.append({
                    "id": child,
                    "scope": (
                        f"section-{decision.section}"
                        if decision.section else "global"
                    ),
                    "summary": f"child problem from {decision.id}",
                })

    coordination_rounds = sum(
        1 for decision in decisions if decision.scope == "global"
    )

    snapshot: dict[str, Any] = {
        "completed_sections": sorted(completed),
        "in_progress": in_progress,
        "blocked": blocked,
        "open_problems": open_problems,
        "research_questions": research_questions,
        "key_decisions": key_decision_ids,
        "coordination_rounds": coordination_rounds,
        "risk_posture": risk_posture,
        "dominant_risks_by_section": dominant_risks_by_section,
        "blocked_by_risk": blocked_by_risk,
        "next_action": _derive_next_action(
            completed,
            in_progress,
            blocked,
            open_problems,
        ),
    }
    if decision_warnings:
        snapshot["warnings"] = decision_warnings

    state_path = decisions_dir.parent / "strategic-state.json"
    write_json(state_path, snapshot)
    return snapshot


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
) -> tuple[str | None, list[str], bool]:
    paths = PathRegistry(planspace)
    scope = f"section-{sec_num}"
    assessment = load_risk_assessment(paths.risk_assessment(scope))
    plan = load_risk_plan(paths.risk_plan(scope))

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
    return posture, dominant_risks, blocked_by_risk


def _load_research_questions(planspace: Path) -> list[dict[str, Any]]:
    open_problems_dir = PathRegistry(planspace).open_problems_dir()
    if not open_problems_dir.exists():
        return []

    aggregated: list[dict[str, Any]] = []
    for artifact_path in sorted(
        open_problems_dir.glob("section-*-research-questions.json")
    ):
        artifact = read_json(artifact_path)
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


