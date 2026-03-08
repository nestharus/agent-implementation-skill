"""Component tests for the intent triage service."""

from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.intent import intent_triage
from src.scripts.lib.intent.intent_triage import (
    _augment_risk_hints,
    _full_default,
    load_triage_result,
    run_intent_triage,
)


def test_full_default_is_fail_closed() -> None:
    result = _full_default("01")

    assert result["intent_mode"] == "full"
    assert result["risk_mode"] == "full"
    assert result["risk_budget_hint"] == 4
    assert result["budgets"]["intent_expansion_max"] == 2


def test_load_triage_result_reads_signal_from_planspace(tmp_path: Path) -> None:
    signal_path = tmp_path / "artifacts" / "signals" / "intent-triage-01.json"
    signal_path.parent.mkdir(parents=True)
    signal_path.write_text(
        json.dumps({
            "intent_mode": "lightweight",
            "confidence": "high",
        }),
        encoding="utf-8",
    )

    assert load_triage_result("01", tmp_path) == {
        "intent_mode": "lightweight",
        "confidence": "high",
        "risk_mode": "full",
        "risk_confidence": "high",
        "risk_budget_hint": 0,
        "posture_floor": None,
    }


def test_augment_risk_hints_passes_through_agent_risk_fields(tmp_path: Path) -> None:
    result = _augment_risk_hints(
        {
            "intent_mode": "lightweight",
            "confidence": "medium",
            "risk_mode": "light",
            "risk_budget_hint": 3,
        },
        "01",
        tmp_path,
        related_files_count=8,
        incoming_notes_count=4,
        solve_count=2,
    )

    assert result["risk_mode"] == "light"
    assert result["risk_confidence"] == "medium"
    assert result["risk_budget_hint"] == 3
    assert result["posture_floor"] is None


def test_augment_risk_hints_defaults_fail_closed_without_heuristics(
    tmp_path: Path,
) -> None:
    result = _augment_risk_hints(
        {
            "intent_mode": "lightweight",
            "confidence": "high",
        },
        "01",
        tmp_path,
        related_files_count=0,
        incoming_notes_count=0,
        solve_count=0,
    )

    assert result["risk_mode"] == "full"
    assert result["risk_confidence"] == "high"
    assert result["risk_budget_hint"] == 0


def test_run_intent_triage_returns_signal_from_agent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    planspace = tmp_path / "planspace"
    artifacts = planspace / "artifacts"
    (artifacts / "sections").mkdir(parents=True)
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    monkeypatch.setattr(
        intent_triage,
        "read_model_policy",
        lambda _: {"intent_triage": "glm"},
    )
    monkeypatch.setattr(intent_triage, "write_validated_prompt", lambda *_: True)
    artifact_events: list[str] = []
    monkeypatch.setattr(
        intent_triage,
        "_log_artifact",
        lambda _planspace, name: artifact_events.append(name),
    )

    def fake_dispatch(*args, **kwargs):
        signal_path = artifacts / "signals" / "intent-triage-01.json"
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.write_text(
            json.dumps({
                "section": "01",
                "intent_mode": "lightweight",
                "confidence": "medium",
                "risk_mode": "light",
                "risk_budget_hint": 2,
                "budgets": {"proposal_max": 3},
                "reason": "narrow surface",
            }),
            encoding="utf-8",
        )
        return ""

    monkeypatch.setattr(intent_triage, "dispatch_agent", fake_dispatch)

    result = run_intent_triage("01", planspace, codespace, "parent")

    assert result["intent_mode"] == "lightweight"
    assert result["risk_mode"] == "light"
    assert result["risk_confidence"] == "medium"
    assert result["risk_budget_hint"] == 2
    assert artifact_events == ["prompt:intent-triage-01"]
