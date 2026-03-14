"""Component tests for proposal_state_repository."""

from __future__ import annotations

import json

from src.proposal.repository.state import (
    ProposalState,
    extract_blockers,
    has_blocking_fields,
    load_proposal_state,
    save_proposal_state,
)


def test_load_proposal_state_returns_default_when_missing(tmp_path) -> None:
    result = load_proposal_state(tmp_path / "missing.json")
    assert isinstance(result, ProposalState)
    assert result.execution_ready is False
    assert result.resolved_contracts == []


def test_load_proposal_state_renames_non_object_json_and_fails_closed(tmp_path) -> None:
    path = tmp_path / "proposal-state.json"
    path.write_text(json.dumps(["bad"]), encoding="utf-8")

    result = load_proposal_state(path)

    assert isinstance(result, ProposalState)
    assert result.execution_ready is False
    assert not path.exists()
    assert (tmp_path / "proposal-state.malformed.json").exists()


def test_load_proposal_state_renames_missing_required_keys_and_fails_closed(tmp_path) -> None:
    path = tmp_path / "proposal-state.json"
    incomplete = ProposalState().to_dict()
    del incomplete["execution_ready"]
    path.write_text(json.dumps(incomplete), encoding="utf-8")

    result = load_proposal_state(path)

    assert isinstance(result, ProposalState)
    assert result.execution_ready is False
    assert not path.exists()
    assert (tmp_path / "proposal-state.malformed.json").exists()


def test_save_proposal_state_writes_json(tmp_path) -> None:
    path = tmp_path / "nested" / "proposal-state.json"
    state = ProposalState(resolved_contracts=["auth"])

    save_proposal_state(state, path)

    assert json.loads(path.read_text(encoding="utf-8"))["resolved_contracts"] == ["auth"]


def test_save_proposal_state_accepts_dict(tmp_path) -> None:
    path = tmp_path / "nested" / "proposal-state.json"
    state = ProposalState().to_dict()
    state["resolved_contracts"] = ["auth"]

    save_proposal_state(state, path)

    assert json.loads(path.read_text(encoding="utf-8"))["resolved_contracts"] == ["auth"]


def test_blocking_helpers_report_blockers() -> None:
    state = ProposalState(
        user_root_questions=["Need user decision"],
        shared_seam_candidates=["Shared interface"],
    )

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


def test_from_dict_ignores_unknown_keys() -> None:
    state = ProposalState.from_dict({
        "execution_ready": True,
        "readiness_rationale": "ready",
        "unknown_key": "ignored",
    })
    assert state.execution_ready is True
    assert state.readiness_rationale == "ready"


def test_to_dict_round_trips() -> None:
    state = ProposalState(
        resolved_anchors=["a.store"],
        execution_ready=True,
        readiness_rationale="ready",
    )
    d = state.to_dict()
    restored = ProposalState.from_dict(d)
    assert restored == state
