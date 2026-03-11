"""Component tests for proposal_state_repository."""

from __future__ import annotations

import json

from src.proposal.repository.state import (
    _fail_closed_default,
    extract_blockers,
    has_blocking_fields,
    load_proposal_state,
    save_proposal_state,
    validate_proposal_state,
)


def test_validate_proposal_state_fills_missing_and_invalid_fields() -> None:
    state = validate_proposal_state({
        "resolved_anchors": "bad",
        "execution_ready": "yes",
        "readiness_rationale": None,
    })

    assert state["resolved_anchors"] == []
    assert state["execution_ready"] is False
    assert state["readiness_rationale"] == ""
    assert state["unresolved_contracts"] == []


def test_load_proposal_state_returns_fail_closed_default_when_missing(tmp_path) -> None:
    assert load_proposal_state(tmp_path / "missing.json") == _fail_closed_default()


def test_load_proposal_state_renames_non_object_json_and_fails_closed(tmp_path) -> None:
    path = tmp_path / "proposal-state.json"
    path.write_text(json.dumps(["bad"]), encoding="utf-8")

    result = load_proposal_state(path)

    assert result == _fail_closed_default()
    assert not path.exists()
    assert (tmp_path / "proposal-state.malformed.json").exists()


def test_load_proposal_state_renames_missing_required_keys_and_fails_closed(tmp_path) -> None:
    path = tmp_path / "proposal-state.json"
    valid = _fail_closed_default()
    del valid["execution_ready"]
    path.write_text(json.dumps(valid), encoding="utf-8")

    result = load_proposal_state(path)

    assert result == _fail_closed_default()
    assert not path.exists()
    assert (tmp_path / "proposal-state.malformed.json").exists()


def test_save_proposal_state_writes_json(tmp_path) -> None:
    path = tmp_path / "nested" / "proposal-state.json"
    state = _fail_closed_default()
    state["resolved_contracts"] = ["auth"]

    save_proposal_state(state, path)

    assert json.loads(path.read_text(encoding="utf-8"))["resolved_contracts"] == ["auth"]


def test_blocking_helpers_report_blockers() -> None:
    state = _fail_closed_default()
    state["user_root_questions"] = ["Need user decision"]
    state["shared_seam_candidates"] = ["Shared interface"]

    assert has_blocking_fields(state) is True
    assert extract_blockers(state) == [
        {
            "type": "user_root_questions",
            "description": "Need user decision",
        },
        {
            "type": "shared_seam_candidates",
            "description": "Shared interface",
        },
    ]
