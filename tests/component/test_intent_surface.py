"""Component tests for intent surface orchestration."""

from __future__ import annotations

import json
from pathlib import Path

from dependency_injector import providers

from conftest import StubPolicies
from containers import Services
from src.intent.engine import expansion_orchestrator


def test_build_pending_surface_payload_reconstructs_backlog_entries() -> None:
    payload = expansion_orchestrator.build_pending_surface_payload(
        [
            {"id": "P-01-0001", "kind": "gap"},
            {
                "id": "F-01-0002",
                "kind": "tension",
                "axis_id": "A2",
                "notes": "Clarify principle",
                "description": "Need provenance",
                "evidence": "source doc",
            },
        ],
        {
            "problem_surfaces": [
                {
                    "id": "P-01-0001",
                    "kind": "gap",
                    "axis_id": "A1",
                    "title": "Missing constraint",
                    "description": "constraint detail",
                    "evidence": "excerpt",
                },
            ],
            "philosophy_surfaces": [],
        },
    )

    assert payload["problem_surfaces"] == [{
        "id": "P-01-0001",
        "kind": "gap",
        "axis_id": "A1",
        "title": "Missing constraint",
        "description": "constraint detail",
        "evidence": "excerpt",
    }]
    assert payload["philosophy_surfaces"] == [{
        "id": "F-01-0002",
        "kind": "tension",
        "axis_id": "A2",
        "title": "Clarify principle",
        "description": "Need provenance",
        "evidence": "source doc",
    }]


def test_run_expansion_cycle_returns_no_work_when_no_surfaces(
    monkeypatch,
    tmp_path: Path,
) -> None:
    Services.policies.override(providers.Object(StubPolicies()))
    monkeypatch.setattr(expansion_orchestrator, "load_combined_intent_surfaces", lambda *_: None)

    try:
        result = expansion_orchestrator.run_expansion_cycle(
            "01",
            tmp_path,
            tmp_path / "codespace",
            "parent",
        )

        assert result == {
            "restart_required": False,
            "needs_user_input": False,
            "expansion_applied": False,
            "surfaces_found": 0,
        }
    finally:
        Services.policies.reset_override()


def test_handle_user_gate_writes_philosophy_specific_blocker(
    tmp_path: Path,
    capturing_pipeline_control,
) -> None:
    capturing_pipeline_control._pause_return = "ack"

    response = expansion_orchestrator.handle_user_gate(
        "01",
        tmp_path,
        "parent",
        {
            "needs_user_input": True,
            "user_input_kind": "philosophy",
            "user_input_path": "/tmp/philosophy-decisions.md",
        },
    )

    blocker = json.loads(
        (
            tmp_path
            / "artifacts"
            / "signals"
            / "intent-expand-01-signal.json"
        ).read_text(encoding="utf-8"),
    )
    assert response == "ack"
    assert blocker["state"] == "NEED_DECISION"
    assert "Philosophy tension" in blocker["detail"]
    assert len(capturing_pipeline_control.pause_calls) == 1
    assert capturing_pipeline_control.pause_calls[0] == (
        tmp_path,
        "parent",
        "pause:need_decision:01:Philosophy tension requires user direction",
    )
