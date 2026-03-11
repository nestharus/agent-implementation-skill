from __future__ import annotations

from pathlib import Path

import pytest

from src.reconciliation import reconciliation_adjudicator as adjudicator


def test_adjudicate_ungrouped_candidates_returns_empty_for_singleton(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"

    result = adjudicator.adjudicate_ungrouped_candidates(
        [{"title": "solo", "source_section": "01"}],
        planspace,
        "new_section",
    )

    assert result == []


def test_adjudicate_ungrouped_candidates_writes_artifact_and_parses_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    candidates = [
        {"title": "shared auth", "source_section": "01"},
        {"title": "auth seam", "source_section": "02"},
    ]

    monkeypatch.setattr(adjudicator, "validate_dynamic_content", lambda _: [])
    monkeypatch.setattr(
        adjudicator,
        "read_model_policy",
        lambda _planspace: {"reconciliation_adjudicate": "policy-model"},
    )

    captured: dict[str, object] = {}

    def fake_dispatch(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return (
            '{"merged_groups": [{"canonical_title": "auth seam", '
            '"members": ["shared auth", "auth seam"], "rationale": "same"}]}'
        )

    monkeypatch.setattr(adjudicator, "dispatch_agent", fake_dispatch)

    result = adjudicator.adjudicate_ungrouped_candidates(
        candidates,
        planspace,
        "shared_seam",
    )

    artifact = (
        planspace
        / "artifacts"
        / "reconciliation"
        / "ungrouped-shared_seam.json"
    )
    assert artifact.exists()
    assert result == [{
        "canonical_title": "auth seam",
        "members": ["shared auth", "auth seam"],
        "rationale": "same",
    }]
    assert captured["args"][0] == "policy-model"


def test_adjudicate_ungrouped_candidates_returns_empty_on_bad_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    candidates = [
        {"title": "shared auth", "source_section": "01"},
        {"title": "auth seam", "source_section": "02"},
    ]

    monkeypatch.setattr(adjudicator, "validate_dynamic_content", lambda _: [])
    monkeypatch.setattr(adjudicator, "read_model_policy", lambda _planspace: {})
    monkeypatch.setattr(adjudicator, "dispatch_agent", lambda *args, **kwargs: "not json")

    result = adjudicator.adjudicate_ungrouped_candidates(
        candidates,
        planspace,
        "shared_seam",
    )

    assert result == []
