"""Component tests for intent surface orchestration."""

from __future__ import annotations

import json
from pathlib import Path

from src.intent import intent_surface


def test_build_pending_surface_payload_reconstructs_backlog_entries() -> None:
    payload = intent_surface.build_pending_surface_payload(
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
    monkeypatch.setattr(intent_surface, "read_model_policy", lambda _: {})
    monkeypatch.setattr(intent_surface, "load_intent_surfaces", lambda *_: None)

    result = intent_surface.run_expansion_cycle(
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


def test_handle_user_gate_writes_philosophy_specific_blocker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pause_calls: list[str] = []

    def _pause(planspace: Path, parent: str, message: str) -> str:
        pause_calls.append(message)
        return "ack"

    monkeypatch.setattr(intent_surface, "pause_for_parent", _pause)

    response = intent_surface.handle_user_gate(
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
    assert pause_calls == [
        "pause:need_decision:01:Philosophy tension requires user direction",
    ]
