"""StrategicStateBuilder: derive and persist strategic-state snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.core.artifact_io import read_json, write_json
from lib.repositories.decision_repository import load_decisions
from lib.core.path_registry import PathRegistry


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

    for sec_num, result in sorted(section_results.items()):
        if isinstance(result, dict):
            aligned = result.get("aligned", False)
            problems = result.get("problems")
        else:
            aligned = getattr(result, "aligned", False)
            problems = getattr(result, "problems", None)

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
        "key_decisions": key_decision_ids,
        "coordination_rounds": coordination_rounds,
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
