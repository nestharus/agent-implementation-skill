from __future__ import annotations

from pathlib import Path

import pytest
from dependency_injector import providers

from containers import AgentDispatcher, ModelPolicyService, PromptGuard, Services
from src.reconciliation.service import adjudicator


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
) -> None:
    planspace = tmp_path / "planspace"
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

    class _MockGuard(PromptGuard):
        def validate_dynamic(self, content):
            return []
        def write_validated(self, content, path):
            return True

    class _MockPolicies(ModelPolicyService):
        def load(self, planspace):
            return {"reconciliation_adjudicate": "policy-model"}
        def resolve(self, policy, key):
            return policy.get(key, "test-model")

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return fake_dispatch(*args, **kwargs)

    Services.prompt_guard.override(providers.Object(_MockGuard()))
    Services.policies.override(providers.Object(_MockPolicies()))
    Services.dispatcher.override(providers.Object(_MockDispatcher()))
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
    candidates = [
        {"title": "shared auth", "source_section": "01"},
        {"title": "auth seam", "source_section": "02"},
    ]

    class _MockGuard(PromptGuard):
        def validate_dynamic(self, content):
            return []
        def write_validated(self, content, path):
            return True

    class _MockPolicies(ModelPolicyService):
        def load(self, planspace):
            return {}
        def resolve(self, policy, key):
            return "test-model"

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return "not json"

    Services.prompt_guard.override(providers.Object(_MockGuard()))
    Services.policies.override(providers.Object(_MockPolicies()))
    Services.dispatcher.override(providers.Object(_MockDispatcher()))
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
