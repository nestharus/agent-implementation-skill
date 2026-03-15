"""Component tests for debt promotion materiality and idempotence."""

from __future__ import annotations

import json
from pathlib import Path

from src.orchestrator.path_registry import PathRegistry
from src.containers import ArtifactIOService, PromptGuard
from src.intake.service.assessment_evaluator import AssessmentEvaluator


def _evaluator() -> AssessmentEvaluator:
    return AssessmentEvaluator(
        artifact_io=ArtifactIOService(),
        prompt_guard=PromptGuard(),
    )


def _write_signal(signals_dir: Path, section: str, items: list[dict]) -> None:
    signal = {
        "section": section,
        "debt_items": items,
        "problem_ids": ["PRB-0001"],
        "pattern_ids": ["PAT-0001"],
        "profile_id": "PHI-global",
    }
    path = signals_dir / f"section-{section}-risk-register-signal.json"
    path.write_text(json.dumps(signal), encoding="utf-8")


def test_unchanged_debt_is_idempotent(tmp_path: Path) -> None:
    """PAT-0012: identical debt signal does not re-promote."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    signals = planspace / "artifacts" / "signals"

    item = {
        "category": "coupling",
        "region": "section-01",
        "description": "tight coupling to cache",
        "severity": "medium",
        "mitigation": "planned refactor",
        "acceptance_rationale": "acceptable for now",
    }
    _write_signal(signals, "01", [item])

    first = _evaluator().promote_debt_signals(planspace)
    assert len(first) == 1

    # Write the same signal again
    _write_signal(signals, "01", [item])
    second = _evaluator().promote_debt_signals(planspace)
    assert len(second) == 0


def test_materially_changed_debt_repromotes(tmp_path: Path) -> None:
    """PAT-0012: changed severity/mitigation triggers re-promotion."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    signals = planspace / "artifacts" / "signals"

    item = {
        "category": "coupling",
        "region": "section-01",
        "description": "tight coupling to cache",
        "severity": "medium",
        "mitigation": "planned refactor",
        "acceptance_rationale": "acceptable for now",
    }
    _write_signal(signals, "01", [item])

    first = _evaluator().promote_debt_signals(planspace)
    assert len(first) == 1

    # Change severity — material change
    item["severity"] = "high"
    _write_signal(signals, "01", [item])
    second = _evaluator().promote_debt_signals(planspace)
    assert len(second) == 1
    assert second[0]["severity"] == "high"
