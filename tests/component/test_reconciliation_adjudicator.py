from __future__ import annotations

from pathlib import Path

import pytest
from dependency_injector import providers

from conftest import StubPolicies, WritingGuard, make_dispatcher
from containers import Services
from src.orchestrator.path_registry import PathRegistry
from src.reconciliation.service import adjudicator


def test_adjudicate_ungrouped_candidates_returns_empty_for_singleton(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    result = adjudicator.adjudicate_ungrouped_candidates(
        [{"title": "solo", "source_section": "01"}],
        planspace,
        "new_section",
    )

    assert result == []


def test_adjudicate_ungrouped_candidates_writes_artifact_and_parses_json(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    candidates = [
        {"title": "shared auth", "source_section": "01"},
        {"title": "auth seam", "source_section": "02"},
    ]

    captured: dict[str, object] = {}

    def fake_dispatch(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return (
            '{"merged_groups": [{"canonical_title": "auth seam", '
            '"members": ["shared auth", "auth seam"], "rationale": "same"}]}'
        )

    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.policies.override(providers.Object(StubPolicies({"reconciliation_adjudicate": "policy-model"})))
    Services.dispatcher.override(providers.Object(make_dispatcher(fake_dispatch)))
    try:
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
    finally:
        Services.dispatcher.reset_override()
        Services.policies.reset_override()
        Services.prompt_guard.reset_override()


def test_adjudicate_ungrouped_candidates_returns_empty_on_bad_json(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    candidates = [
        {"title": "shared auth", "source_section": "01"},
        {"title": "auth seam", "source_section": "02"},
    ]

    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.policies.override(providers.Object(StubPolicies()))
    Services.dispatcher.override(providers.Object(make_dispatcher(lambda *_a, **_kw: "not json")))
    try:
        result = adjudicator.adjudicate_ungrouped_candidates(
            candidates,
            planspace,
            "shared_seam",
        )

        assert result == []
    finally:
        Services.dispatcher.reset_override()
        Services.policies.reset_override()
        Services.prompt_guard.reset_override()
