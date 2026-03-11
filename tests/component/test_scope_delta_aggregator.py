import json

import pytest

from implementation.service.scope_delta_aggregator import (
    ScopeDeltaAggregationExit,
    aggregate_scope_deltas,
)


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
    monkeypatch.setattr(
        "implementation.service.scope_delta_aggregator.dispatch_agent",
        lambda *args, **kwargs: (
            '{"decisions":[{"delta_id":"delta-01","section":"01",'
            '"action":"reject","reason":"defer"}]}'
        ),
    )

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

    def fake_dispatch(model, *args, **kwargs):
        calls.append(model)
        return "not json"

    monkeypatch.setattr("implementation.service.scope_delta_aggregator.dispatch_agent", fake_dispatch)
    monkeypatch.setattr(
        "implementation.service.scope_delta_aggregator.mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
    )

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

    def fake_dispatch(_model, prompt_path, _output_path, *_args, **_kwargs):
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

    monkeypatch.setattr(
        "implementation.service.scope_delta_aggregator.dispatch_agent",
        fake_dispatch,
    )

    aggregate_scope_deltas(
        planspace,
        "parent",
        {"coordination_plan": "model-a", "escalation_model": "model-b"},
    )
