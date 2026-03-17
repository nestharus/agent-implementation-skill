"""Component tests for proposal_state_repository."""

from __future__ import annotations

import json

from containers import Services
from src.proposal.repository.state import (
    ProposalState,
    State,
    extract_blockers,
    extract_blockers_for_mode,
    has_blocking_fields,
    has_blocking_fields_for_mode,
)


def _make_repo() -> State:
    return State(artifact_io=Services.artifact_io())


def test_load_proposal_state_returns_default_when_missing(tmp_path) -> None:
    repo = _make_repo()
    result = repo.load_proposal_state(tmp_path / "missing.json")
    assert isinstance(result, ProposalState)
    assert result.execution_ready is False
    assert result.resolved_contracts == []


def test_load_proposal_state_renames_non_object_json_and_fails_closed(tmp_path) -> None:
    path = tmp_path / "proposal-state.json"
    path.write_text(json.dumps(["bad"]), encoding="utf-8")

    repo = _make_repo()
    result = repo.load_proposal_state(path)

    assert isinstance(result, ProposalState)
    assert result.execution_ready is False
    assert not path.exists()
    assert (tmp_path / "proposal-state.malformed.json").exists()


def test_load_proposal_state_renames_missing_required_keys_and_fails_closed(tmp_path) -> None:
    path = tmp_path / "proposal-state.json"
    incomplete = ProposalState().to_dict()
    del incomplete["execution_ready"]
    path.write_text(json.dumps(incomplete), encoding="utf-8")

    repo = _make_repo()
    result = repo.load_proposal_state(path)

    assert isinstance(result, ProposalState)
    assert result.execution_ready is False
    assert not path.exists()
    assert (tmp_path / "proposal-state.malformed.json").exists()


def test_save_proposal_state_writes_json(tmp_path) -> None:
    path = tmp_path / "nested" / "proposal-state.json"
    state = ProposalState(resolved_contracts=["auth"])

    repo = _make_repo()
    repo.save_proposal_state(state, path)

    assert json.loads(path.read_text(encoding="utf-8"))["resolved_contracts"] == ["auth"]


def test_save_proposal_state_accepts_dict(tmp_path) -> None:
    path = tmp_path / "nested" / "proposal-state.json"
    state = ProposalState().to_dict()
    state["resolved_contracts"] = ["auth"]

    repo = _make_repo()
    repo.save_proposal_state(state, path)

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


# -- greenfield mode-aware helpers -----------------------------------------

def test_greenfield_ignores_unresolved_anchors_and_contracts() -> None:
    """Greenfield projects should not block on missing code anchors/contracts."""
    state = ProposalState(
        unresolved_anchors=["client.cache"],
        unresolved_contracts=["CacheProtocol"],
    )

    assert has_blocking_fields(state) is True
    assert has_blocking_fields_for_mode(state, "greenfield") is False
    assert extract_blockers_for_mode(state, "greenfield") == []


def test_greenfield_still_blocks_on_research_questions() -> None:
    """Design ambiguities block even in greenfield mode."""
    state = ProposalState(
        blocking_research_questions=["Should we use gRPC or REST?"],
    )

    assert has_blocking_fields_for_mode(state, "greenfield") is True
    blockers = extract_blockers_for_mode(state, "greenfield")
    assert len(blockers) == 1
    assert blockers[0]["type"] == "blocking_research_questions"


def test_greenfield_still_blocks_on_shared_seam_candidates() -> None:
    """Shared seams need substrate coordination even in greenfield."""
    state = ProposalState(
        shared_seam_candidates=["shared client cache"],
    )

    assert has_blocking_fields_for_mode(state, "greenfield") is True
    blockers = extract_blockers_for_mode(state, "greenfield")
    assert len(blockers) == 1
    assert blockers[0]["type"] == "shared_seam_candidates"


def test_greenfield_filters_repo_confusion_questions() -> None:
    """Repo-confusion user_root_questions are noise in greenfield."""
    state = ProposalState(
        user_root_questions=[
            "Is this spec only?",
            "Is this a different checkout?",
            "Is this supposed to be empty?",
        ],
    )

    assert has_blocking_fields(state) is True
    assert has_blocking_fields_for_mode(state, "greenfield") is False
    assert extract_blockers_for_mode(state, "greenfield") == []


def test_greenfield_keeps_genuine_user_questions() -> None:
    """Genuine user questions still block in greenfield mode."""
    state = ProposalState(
        user_root_questions=[
            "Should we support pagination?",
            "Is this supposed to be empty?",  # noise -- filtered
        ],
    )

    assert has_blocking_fields_for_mode(state, "greenfield") is True
    blockers = extract_blockers_for_mode(state, "greenfield")
    assert len(blockers) == 1
    assert blockers[0]["description"] == "Should we support pagination?"


def test_brownfield_delegates_to_original_helpers() -> None:
    """Non-greenfield modes use the full blocking checks."""
    state = ProposalState(
        unresolved_anchors=["client.cache"],
        unresolved_contracts=["CacheProtocol"],
    )

    assert has_blocking_fields_for_mode(state, "brownfield") is True
    blockers = extract_blockers_for_mode(state, "brownfield")
    assert len(blockers) == 2
    assert {b["type"] for b in blockers} == {
        "unresolved_anchors",
        "unresolved_contracts",
    }
