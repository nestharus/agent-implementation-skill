"""Component tests for readiness resolution."""

from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.services.readiness_resolver import resolve_readiness


def test_resolve_readiness_writes_ready_artifact(tmp_path: Path) -> None:
    proposal_state = tmp_path / "proposals" / "section-03-proposal-state.json"
    proposal_state.parent.mkdir(parents=True)
    proposal_state.write_text(json.dumps({
        "resolved_anchors": ["cache.store"],
        "unresolved_anchors": [],
        "resolved_contracts": ["CacheProtocol"],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": True,
        "readiness_rationale": "ready",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }), encoding="utf-8")

    result = resolve_readiness(tmp_path, "03")

    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["artifact_path"].exists()
    assert json.loads(result["artifact_path"].read_text(encoding="utf-8")) == {
        "ready": True,
        "blockers": [],
        "rationale": "ready",
    }


def test_resolve_readiness_fails_closed_when_artifact_missing(tmp_path: Path) -> None:
    result = resolve_readiness(tmp_path, "04")

    assert result["ready"] is False
    assert result["blockers"] == []
    assert result["rationale"] == "proposal-state artifact missing"


def _make_proposal_state(tmp_path: Path, section: str, **overrides) -> None:
    """Write a ready proposal-state with optional field overrides."""
    state = {
        "resolved_anchors": ["a.store"],
        "unresolved_anchors": [],
        "resolved_contracts": ["Proto"],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": True,
        "readiness_rationale": "ready",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    state.update(overrides)
    proposal = tmp_path / "proposals" / f"section-{section}-proposal-state.json"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(json.dumps(state), encoding="utf-8")


def _make_packet(tmp_path: Path, section: str, **overrides) -> None:
    """Write a governance packet with optional field overrides."""
    packet = {
        "section": section,
        "candidate_problems": [{"problem_id": "PRB-0001"}],
        "candidate_patterns": [{"pattern_id": "PAT-0001"}],
        "governing_profile": "PHI-global",
        "profiles": [{"profile_id": "PHI-global"}],
        "governance_questions": [],
        "applicability_state": "matched",
    }
    packet.update(overrides)
    packet_path = (
        tmp_path / "artifacts" / "governance"
        / f"section-{section}-governance-packet.json"
    )
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(json.dumps(packet), encoding="utf-8")


def test_empty_identity_with_populated_packet_blocks(tmp_path: Path) -> None:
    """PAT-0013: empty governance identity when packet has candidates → blocked."""
    _make_proposal_state(tmp_path, "10")
    _make_packet(tmp_path, "10")

    result = resolve_readiness(tmp_path, "10")

    assert result["ready"] is False
    blocker_states = [b["state"] for b in result["blockers"]]
    assert "governance_identity_missing" in blocker_states


def test_wrong_profile_id_blocks(tmp_path: Path) -> None:
    """PAT-0013: profile_id mismatch with governing_profile → blocked."""
    _make_proposal_state(
        tmp_path, "11",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-wrong",
    )
    _make_packet(tmp_path, "11")

    result = resolve_readiness(tmp_path, "11")

    assert result["ready"] is False
    blocker_states = [b["state"] for b in result["blockers"]]
    assert "governance_profile_mismatch" in blocker_states


def test_declared_ids_with_missing_packet_blocks(tmp_path: Path) -> None:
    """PAT-0013: governance IDs declared but no packet → blocked."""
    _make_proposal_state(
        tmp_path, "12",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-global",
    )
    # No packet written

    result = resolve_readiness(tmp_path, "12")

    assert result["ready"] is False
    blocker_states = [b["state"] for b in result["blockers"]]
    assert "governance_packet_missing" in blocker_states


def test_correct_identity_with_packet_passes(tmp_path: Path) -> None:
    """PAT-0013: matching governance identity with valid packet → ready."""
    _make_proposal_state(
        tmp_path, "13",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-global",
    )
    _make_packet(tmp_path, "13")

    result = resolve_readiness(tmp_path, "13")

    assert result["ready"] is True
    assert result["blockers"] == []
