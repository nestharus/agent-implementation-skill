import json

import pytest
from dependency_injector import providers

from containers import AgentDispatcher, PromptGuard, Services
from implementation.service.scope_delta_aggregator import (
    ScopeDeltaAggregationExit,
    aggregate_scope_deltas,
)


class _NoOpGuard(PromptGuard):
    def write_validated(self, content, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True

    def validate_dynamic(self, content):
        return []


def test_aggregate_scope_deltas_adjudicates_and_records_decisions(
    planspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope_dir = planspace / "artifacts" / "scope-deltas"
    scope_dir.mkdir(parents=True, exist_ok=True)
    delta_path = scope_dir / "section-01-scope-delta.json"
    delta_path.write_text(
        json.dumps(
            {
                "delta_id": "delta-01",
                "section": "01",
                "origin": "proposal",
                "summary": "Need auth middleware",
            },
        ),
        encoding="utf-8",
    )

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return (
                '{"decisions":[{"delta_id":"delta-01","section":"01",'
                '"action":"reject","reason":"defer"}]}'
            )

    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    Services.prompt_guard.override(providers.Object(_NoOpGuard()))
    try:
        decisions = aggregate_scope_deltas(
            planspace,
            "parent",
            {"coordination_plan": "model-a", "escalation_model": "model-b"},
        )

        assert decisions == [
            {
                "delta_id": "delta-01",
                "section": "01",
                "action": "reject",
                "reason": "defer",
            },
        ]
        written = json.loads(delta_path.read_text(encoding="utf-8"))
        assert written["adjudicated"] is True
        assert written["adjudication"]["action"] == "reject"
        rollup = json.loads(
            (
                planspace / "artifacts" / "coordination" / "scope-delta-decisions.json"
            ).read_text(encoding="utf-8"),
        )
        assert rollup["decisions"][0]["delta_id"] == "delta-01"
        decisions_json = json.loads(
            (planspace / "artifacts" / "decisions" / "section-01.json").read_text(
                encoding="utf-8",
            ),
        )
        assert decisions_json[0]["concern_scope"] == "scope-delta"
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()


def test_aggregate_scope_deltas_retries_then_fails_closed_on_bad_output(
    planspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope_dir = planspace / "artifacts" / "scope-deltas"
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / "section-01-scope-delta.json").write_text(
        json.dumps({"delta_id": "delta-01", "section": "01"}),
        encoding="utf-8",
    )
    calls: list[str] = []
    messages: list[str] = []

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, model, *args, **kwargs):
            calls.append(model)
            return "not json"

    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    Services.prompt_guard.override(providers.Object(_NoOpGuard()))
    monkeypatch.setattr(
        "implementation.service.scope_delta_aggregator.mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
    )

    try:
        with pytest.raises(ScopeDeltaAggregationExit):
            aggregate_scope_deltas(
                planspace,
                "parent",
                {"coordination_plan": "model-a", "escalation_model": "model-b"},
            )

        assert calls == ["model-a", "model-b"]
        assert messages == ["fail:coordination:unparseable_scope_delta_adjudication"]
        assert (
            planspace
            / "artifacts"
            / "coordination"
            / "scope-delta-adjudication-failure.json"
        ).exists()
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()


def test_aggregate_scope_deltas_includes_root_reframing_in_prompt_payload(
    planspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope_dir = planspace / "artifacts" / "scope-deltas"
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / "section-01-scope-delta.json").write_text(
        json.dumps(
            {
                "delta_id": "delta-01",
                "section": "01",
                "origin": "proposal",
                "summary": "Need auth middleware",
                "requires_root_reframing": True,
            },
        ),
        encoding="utf-8",
    )

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, _model, prompt_path, _output_path, *_args, **_kwargs):
            prompt = prompt_path.read_text(encoding="utf-8")
            pending = json.loads(
                (
                    planspace / "artifacts" / "coordination" / "scope-deltas-pending.json"
                ).read_text(encoding="utf-8"),
            )
            assert "requires_root_reframing" in prompt
            assert pending == [
                {
                    "delta_id": "delta-01",
                    "section": "01",
                    "origin": "proposal",
                    "summary": "Need auth middleware",
                    "requires_root_reframing": True,
                },
            ]
            return (
                '{"decisions":[{"delta_id":"delta-01","section":"01",'
                '"action":"reject","reason":"defer"}]}'
            )

    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    Services.prompt_guard.override(providers.Object(_NoOpGuard()))
    try:
        aggregate_scope_deltas(
            planspace,
            "parent",
            {"coordination_plan": "model-a", "escalation_model": "model-b"},
        )
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
