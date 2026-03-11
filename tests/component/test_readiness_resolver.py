"""Component tests for readiness resolution."""

from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.services.readiness_resolver import resolve_readiness


def test_resolve_readiness_writes_ready_artifact(tmp_path: Path) -> None:
    """Tests use runtime layout: planspace → artifacts/ → proposals/."""
    planspace = tmp_path / "planspace"
    proposal_state = (
        planspace / "artifacts" / "proposals"
        / "section-03-proposal-state.json"
    )
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
        "constraint_ids": [],
        "governance_candidate_refs": [],
        "design_decision_refs": [],
    }), encoding="utf-8")

    result = resolve_readiness(planspace, "03")

    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["artifact_path"].exists()
    assert json.loads(result["artifact_path"].read_text(encoding="utf-8")) == {
        "ready": True,
        "blockers": [],
        "rationale": "ready",
    }


def test_resolve_readiness_fails_closed_when_artifact_missing(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    result = resolve_readiness(planspace, "04")

    assert result["ready"] is False
    assert result["blockers"] == []
    assert result["rationale"] == "proposal-state artifact missing"


def _make_proposal_state(planspace: Path, section: str, **overrides) -> None:
    """Write a ready proposal-state using runtime layout (planspace/artifacts/proposals/)."""
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
        "constraint_ids": [],
        "governance_candidate_refs": [],
        "design_decision_refs": [],
    }
    state.update(overrides)
    proposal = (
        planspace / "artifacts" / "proposals"
        / f"section-{section}-proposal-state.json"
    )
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(json.dumps(state), encoding="utf-8")


def _make_packet(planspace: Path, section: str, **overrides) -> None:
    """Write a governance packet using runtime layout (planspace/artifacts/governance/)."""
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
        planspace / "artifacts" / "governance"
        / f"section-{section}-governance-packet.json"
    )
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(json.dumps(packet), encoding="utf-8")


def test_empty_identity_with_populated_packet_blocks(tmp_path: Path) -> None:
    """PAT-0013: empty governance identity when packet has candidates → blocked."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(planspace, "10")
    _make_packet(planspace, "10")

    result = resolve_readiness(planspace, "10")

    assert result["ready"] is False
    blocker_states = [b["state"] for b in result["blockers"]]
    assert "governance_identity_missing" in blocker_states


def test_wrong_profile_id_blocks(tmp_path: Path) -> None:
    """PAT-0013: profile_id mismatch with governing_profile → blocked."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "11",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-wrong",
    )
    _make_packet(planspace, "11")

    result = resolve_readiness(planspace, "11")

    assert result["ready"] is False
    blocker_states = [b["state"] for b in result["blockers"]]
    assert "governance_profile_mismatch" in blocker_states


def test_declared_ids_with_missing_packet_blocks(tmp_path: Path) -> None:
    """PAT-0013: governance IDs declared but no packet → blocked."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "12",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-global",
    )
    # No packet written

    result = resolve_readiness(planspace, "12")

    assert result["ready"] is False
    blocker_states = [b["state"] for b in result["blockers"]]
    assert "governance_packet_missing" in blocker_states


def test_correct_identity_with_packet_passes(tmp_path: Path) -> None:
    """PAT-0013: matching governance identity with valid packet → ready."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "13",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-global",
    )
    _make_packet(planspace, "13")

    result = resolve_readiness(planspace, "13")

    assert result["ready"] is True
    assert result["blockers"] == []


def test_packet_ambiguity_without_proposal_questions_blocks(tmp_path: Path) -> None:
    """PAT-0011/R107: packet ambiguity must be carried in proposal-state."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "14",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-global",
        governance_questions=[],  # empty — ambiguity not carried
    )
    _make_packet(
        planspace, "14",
        applicability_state="ambiguous_applicability",
        governance_questions=["Which patterns apply?"],
    )

    result = resolve_readiness(planspace, "14")

    assert result["ready"] is False
    blocker_states = [b["state"] for b in result["blockers"]]
    assert "governance_ambiguity_unresolved" in blocker_states


def test_packet_ambiguity_with_proposal_questions_passes(tmp_path: Path) -> None:
    """PAT-0011/R107: carried ambiguity does not block (questions route upward)."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "15",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-global",
        governance_questions=["Which patterns apply?"],  # carried forward
    )
    _make_packet(
        planspace, "15",
        applicability_state="ambiguous_applicability",
        governance_questions=["Which patterns apply?"],
    )

    result = resolve_readiness(planspace, "15")

    # Governance questions in proposal-state DO block via the existing
    # _validate_governance_identity check, but that's the correct behavior —
    # the ambiguity is surfaced structurally, not silently dropped.
    blocker_states = [b["state"] for b in result["blockers"]]
    assert "governance_ambiguity_unresolved" not in blocker_states
    assert "governance_question" in blocker_states
