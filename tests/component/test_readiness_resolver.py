"""Component tests for readiness resolution."""

from __future__ import annotations

import json
from pathlib import Path

from src.orchestrator.path_registry import PathRegistry
from src.containers import ArtifactIOService
from src.proposal.service.readiness_resolver import ReadinessResolver, ReadinessResult


def _resolve_readiness(planspace, section):
    """Test helper — create a ReadinessResolver and resolve."""
    return ReadinessResolver(artifact_io=ArtifactIOService()).resolve_readiness(planspace, section)


def test_resolve_readiness_writes_ready_artifact(tmp_path: Path) -> None:
    """Tests use runtime layout: planspace -> artifacts/ -> proposals/."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    proposal_state = (
        planspace / "artifacts" / "proposals"
        / "section-03-proposal-state.json"
    )
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

    result = _resolve_readiness(planspace, "03")

    assert isinstance(result, ReadinessResult)
    assert result.ready is True
    assert result.blockers == []
    assert result.artifact_path is not None
    assert result.artifact_path.exists()
    assert json.loads(result.artifact_path.read_text(encoding="utf-8")) == {
        "ready": True,
        "blockers": [],
        "rationale": "ready",
    }
    # Backward-compat dict-style access
    assert result["ready"] is True
    assert result["blockers"] == []


def test_resolve_readiness_fails_closed_when_artifact_missing(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    result = _resolve_readiness(planspace, "04")

    assert isinstance(result, ReadinessResult)
    assert result.ready is False
    assert result.blockers == []
    assert result.rationale == "proposal-state artifact missing"


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
    """PAT-0013: empty governance identity when packet has candidates -> blocked."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(planspace, "10")
    _make_packet(planspace, "10")

    result = _resolve_readiness(planspace, "10")

    assert result.ready is False
    blocker_states = [b["state"] for b in result.blockers]
    assert "governance_identity_missing" in blocker_states


def test_wrong_profile_id_blocks(tmp_path: Path) -> None:
    """PAT-0013: profile_id mismatch with governing_profile -> blocked."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "11",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-wrong",
    )
    _make_packet(planspace, "11")

    result = _resolve_readiness(planspace, "11")

    assert result.ready is False
    blocker_states = [b["state"] for b in result.blockers]
    assert "governance_profile_mismatch" in blocker_states


def test_declared_ids_with_missing_packet_blocks(tmp_path: Path) -> None:
    """PAT-0013: governance IDs declared but no packet -> blocked."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "12",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-global",
    )
    # No packet written

    result = _resolve_readiness(planspace, "12")

    assert result.ready is False
    blocker_states = [b["state"] for b in result.blockers]
    assert "governance_packet_missing" in blocker_states


def test_correct_identity_with_packet_passes(tmp_path: Path) -> None:
    """PAT-0013: matching governance identity with valid packet -> ready."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "13",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-global",
    )
    _make_packet(planspace, "13")

    result = _resolve_readiness(planspace, "13")

    assert result.ready is True
    assert result.blockers == []


def test_packet_ambiguity_without_proposal_questions_blocks(tmp_path: Path) -> None:
    """PAT-0011/R107: packet ambiguity must be carried in proposal-state."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "14",
        problem_ids=["PRB-0001"],
        pattern_ids=["PAT-0001"],
        profile_id="PHI-global",
        governance_questions=[],  # empty -- ambiguity not carried
    )
    _make_packet(
        planspace, "14",
        applicability_state="ambiguous_applicability",
        governance_questions=["Which patterns apply?"],
    )

    result = _resolve_readiness(planspace, "14")

    assert result.ready is False
    blocker_states = [b["state"] for b in result.blockers]
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

    result = _resolve_readiness(planspace, "15")

    # Governance questions in proposal-state DO block via the existing
    # _validate_governance_identity check, but that's the correct behavior --
    # the ambiguity is surfaced structurally, not silently dropped.
    blocker_states = [b["state"] for b in result.blockers]
    assert "governance_ambiguity_unresolved" not in blocker_states
    assert "governance_question" in blocker_states


# -- greenfield-aware readiness resolution ---------------------------------

def _write_project_mode(planspace: Path, mode: str) -> None:
    """Write a project-mode.json signal to planspace."""
    signals_dir = planspace / "artifacts" / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    (signals_dir / "project-mode.json").write_text(
        json.dumps({"mode": mode}),
        encoding="utf-8",
    )


def test_greenfield_unresolved_anchors_do_not_block(tmp_path: Path) -> None:
    """In greenfield mode, unresolved_anchors are expected and not blocking."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "20",
        unresolved_anchors=["app.main", "app.config"],
        unresolved_contracts=["AppProtocol"],
        execution_ready=True,
    )
    _write_project_mode(planspace, "greenfield")

    result = _resolve_readiness(planspace, "20")

    assert result.ready is True
    blocker_types = [b.get("type") for b in result.blockers]
    assert "unresolved_anchors" not in blocker_types
    assert "unresolved_contracts" not in blocker_types


def test_greenfield_blocking_research_still_blocks(tmp_path: Path) -> None:
    """Design ambiguities block even in greenfield mode."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "21",
        blocking_research_questions=["gRPC vs REST?"],
        execution_ready=True,
    )
    _write_project_mode(planspace, "greenfield")

    result = _resolve_readiness(planspace, "21")

    assert result.ready is False
    blocker_types = [b.get("type") for b in result.blockers]
    assert "blocking_research_questions" in blocker_types


def test_greenfield_shared_seams_still_block(tmp_path: Path) -> None:
    """Shared seam candidates block even in greenfield mode."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "22",
        shared_seam_candidates=["shared cache layer"],
        execution_ready=True,
    )
    _write_project_mode(planspace, "greenfield")

    result = _resolve_readiness(planspace, "22")

    assert result.ready is False
    blocker_types = [b.get("type") for b in result.blockers]
    assert "shared_seam_candidates" in blocker_types


def test_greenfield_filters_repo_confusion_user_questions(tmp_path: Path) -> None:
    """Repo-confusion user questions are noise in greenfield."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "23",
        user_root_questions=["Is this supposed to be empty?"],
        execution_ready=True,
    )
    _write_project_mode(planspace, "greenfield")

    result = _resolve_readiness(planspace, "23")

    assert result.ready is True
    assert result.blockers == []


def test_brownfield_unresolved_anchors_still_block(tmp_path: Path) -> None:
    """In brownfield mode, unresolved_anchors remain blocking."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "24",
        unresolved_anchors=["client.cache"],
        execution_ready=True,
    )
    _write_project_mode(planspace, "brownfield")

    result = _resolve_readiness(planspace, "24")

    assert result.ready is False
    blocker_types = [b.get("type") for b in result.blockers]
    assert "unresolved_anchors" in blocker_types


def test_no_project_mode_defaults_to_blocking(tmp_path: Path) -> None:
    """Without project-mode.json, default to full blocking (fail-closed)."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "25",
        unresolved_anchors=["client.cache"],
        execution_ready=True,
    )
    # No _write_project_mode call -- signal missing

    result = _resolve_readiness(planspace, "25")

    assert result.ready is False
    blocker_types = [b.get("type") for b in result.blockers]
    assert "unresolved_anchors" in blocker_types
