"""Component tests for readiness resolution."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.orchestrator.path_registry import PathRegistry
from src.containers import ArtifactIOService
from src.proposal.service.readiness_resolver import (
    ReadinessResolver,
    ReadinessResult,
    _collect_substrate_paths,
    _item_resolved_by_substrate,
)


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


# ---------------------------------------------------------------------------
# Substrate overlay helpers
# ---------------------------------------------------------------------------

def _make_seed_plan(planspace: Path, anchors: list[dict]) -> None:
    """Write a substrate seed-plan.json."""
    seed_plan_path = planspace / "artifacts" / "substrate" / "seed-plan.json"
    seed_plan_path.parent.mkdir(parents=True, exist_ok=True)
    seed_plan_path.write_text(json.dumps({
        "schema_version": 1,
        "anchors": anchors,
        "wire_sections": [],
        "open_questions": [],
    }), encoding="utf-8")


def _make_shard(
    planspace: Path,
    section: str,
    provides: list[dict] | None = None,
    shared_seams: list[dict] | None = None,
) -> None:
    """Write a substrate shard JSON."""
    shard_path = (
        planspace / "artifacts" / "substrate" / "shards" / f"shard-{section}.json"
    )
    shard_path.parent.mkdir(parents=True, exist_ok=True)
    shard_path.write_text(json.dumps({
        "schema_version": 1,
        "section_number": int(section),
        "mode": "greenfield",
        "touchpoints": [],
        "provides": provides or [],
        "needs": [],
        "shared_seams": shared_seams or [],
        "open_questions": [],
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# Unit tests for _collect_substrate_paths
# ---------------------------------------------------------------------------

def test_collect_substrate_paths_from_seed_plan() -> None:
    """Seed plan anchors contribute their path field."""
    paths = _collect_substrate_paths(
        seed_plan={
            "anchors": [
                {"path": "backend/app/core/config.py"},
                {"path": "frontend/src/main.tsx"},
            ],
        },
        shard=None,
    )
    assert "backend/app/core/config.py" in paths
    assert "frontend/src/main.tsx" in paths


def test_collect_substrate_paths_from_shard() -> None:
    """Shard provides IDs and shared_seam path_candidates contribute."""
    paths = _collect_substrate_paths(
        seed_plan=None,
        shard={
            "provides": [
                {"id": "auth.register", "kind": "api"},
                {"id": "token.verify", "kind": "service"},
            ],
            "shared_seams": [
                {"topic": "auth", "path_candidates": [
                    "backend/app/core/security.py",
                    "backend/app/api/deps.py",
                ]},
            ],
        },
    )
    assert "auth.register" in paths
    assert "token.verify" in paths
    assert "backend/app/core/security.py" in paths
    assert "backend/app/api/deps.py" in paths


def test_collect_substrate_paths_empty_on_none() -> None:
    """Both None -> empty set."""
    assert _collect_substrate_paths(None, None) == set()


def test_collect_substrate_paths_malformed_entries() -> None:
    """Malformed entries are silently skipped."""
    paths = _collect_substrate_paths(
        seed_plan={"anchors": [42, {"no_path": True}, {"path": ""}]},
        shard={"provides": [None, {"id": ""}], "shared_seams": ["bad"]},
    )
    assert paths == set()


# ---------------------------------------------------------------------------
# Unit tests for _item_resolved_by_substrate
# ---------------------------------------------------------------------------

def test_item_resolved_by_path_match() -> None:
    """A candidate mentioning a substrate path is resolved."""
    paths = {"backend/app/core/config.py", "auth.register"}
    assert _item_resolved_by_substrate(
        "shared config boundary at backend/app/core/config.py", paths,
    )


def test_item_resolved_case_insensitive() -> None:
    """Matching is case-insensitive."""
    paths = {"backend/app/core/config.py"}
    assert _item_resolved_by_substrate(
        "Backend/App/Core/Config.py shared boundary", paths,
    )


def test_item_not_resolved_no_match() -> None:
    """Non-matching candidate is not resolved."""
    paths = {"backend/app/core/config.py"}
    assert not _item_resolved_by_substrate(
        "database session factory missing", paths,
    )


def test_item_resolved_by_provides_id() -> None:
    """A candidate mentioning a shard provides ID is resolved."""
    paths = {"auth.register", "token.verify"}
    assert _item_resolved_by_substrate(
        "auth.register API surface unresolved", paths,
    )


# ---------------------------------------------------------------------------
# Integration tests: substrate overlay in resolve_readiness
# ---------------------------------------------------------------------------

def test_substrate_overlay_resolves_shared_seam_candidates(tmp_path: Path) -> None:
    """PRB-0006: shared_seam_candidates referencing substrate paths are filtered."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "03",
        shared_seam_candidates=[
            "backend/app/core/security.py shared auth boundary",
            "database session factory missing",
        ],
        execution_ready=True,
    )
    _make_seed_plan(planspace, anchors=[
        {"path": "backend/app/core/security.py", "purpose": "auth surface"},
    ])
    _make_shard(planspace, "03")

    result = _resolve_readiness(planspace, "03")

    # One candidate resolved by substrate, one remains -> still blocked
    assert result.ready is False
    descriptions = [b["description"] for b in result.blockers]
    assert any("database session" in d for d in descriptions)
    assert not any("security.py" in d for d in descriptions)


def test_substrate_overlay_resolves_all_seam_candidates_unblocks(
    tmp_path: Path,
) -> None:
    """PRB-0006: when all shared_seam_candidates are substrate-resolved, section unblocks."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "05",
        shared_seam_candidates=[
            "backend/app/core/security.py auth boundary",
            "backend/app/api/deps.py dependency injection seam",
        ],
        execution_ready=True,
    )
    _make_seed_plan(planspace, anchors=[
        {"path": "backend/app/core/security.py", "purpose": "auth"},
        {"path": "backend/app/api/deps.py", "purpose": "deps"},
    ])
    _make_shard(planspace, "05")

    result = _resolve_readiness(planspace, "05")

    assert result.ready is True
    assert result.blockers == []


def test_substrate_overlay_resolves_unresolved_anchors(tmp_path: Path) -> None:
    """PRB-0006: unresolved_anchors referencing substrate paths are filtered."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "10",
        unresolved_anchors=[
            "backend/app/core/config.py settings contract",
            "unknown_service.py needs investigation",
        ],
        execution_ready=True,
    )
    _make_seed_plan(planspace, anchors=[
        {"path": "backend/app/core/config.py", "purpose": "config"},
    ])

    result = _resolve_readiness(planspace, "10")

    assert result.ready is False
    descriptions = [b["description"] for b in result.blockers]
    assert any("unknown_service" in d for d in descriptions)
    assert not any("config.py" in d for d in descriptions)


def test_substrate_overlay_shard_provides_resolves_seam(tmp_path: Path) -> None:
    """PRB-0006: shard provides IDs resolve matching shared_seam_candidates."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "03",
        shared_seam_candidates=[
            "auth.register API surface unresolved",
        ],
        execution_ready=True,
    )
    _make_shard(planspace, "03", provides=[
        {"id": "auth.register", "kind": "api", "summary": "register endpoint"},
    ])

    result = _resolve_readiness(planspace, "03")

    assert result.ready is True
    assert result.blockers == []


def test_substrate_overlay_shard_path_candidates_resolve_seam(
    tmp_path: Path,
) -> None:
    """PRB-0006: shard shared_seam path_candidates resolve matching candidates."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "03",
        shared_seam_candidates=[
            "backend/app/api/deps.py shared dependency boundary",
        ],
        execution_ready=True,
    )
    _make_shard(planspace, "03", shared_seams=[
        {"topic": "auth", "path_candidates": ["backend/app/api/deps.py"]},
    ])

    result = _resolve_readiness(planspace, "03")

    assert result.ready is True
    assert result.blockers == []


def test_substrate_overlay_noop_when_no_substrate_files(tmp_path: Path) -> None:
    """PRB-0006: missing substrate files -> overlay does nothing (fail-open)."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "07",
        shared_seam_candidates=["some unresolved seam"],
        execution_ready=True,
    )
    # No seed plan or shard written

    result = _resolve_readiness(planspace, "07")

    assert result.ready is False
    assert len(result.blockers) == 1
    assert "some unresolved seam" in result.blockers[0]["description"]


def test_substrate_overlay_noop_when_malformed_seed_plan(tmp_path: Path) -> None:
    """PRB-0006: malformed seed plan -> overlay does nothing (fail-open)."""
    planspace = tmp_path / "planspace"
    PathRegistry(planspace).ensure_artifacts_tree()
    _make_proposal_state(
        planspace, "08",
        shared_seam_candidates=["backend/app/core/config.py boundary"],
        execution_ready=True,
    )
    # Write malformed JSON
    seed_plan_path = planspace / "artifacts" / "substrate" / "seed-plan.json"
    seed_plan_path.write_text("not valid json {{{", encoding="utf-8")

    result = _resolve_readiness(planspace, "08")

    # Malformed seed plan -> overlay returns empty -> candidates not filtered
    assert result.ready is False
    assert len(result.blockers) == 1


def test_substrate_overlay_logs_resolved_items(
    tmp_path: Path, caplog,
) -> None:
    """PRB-0006: resolved items are logged at INFO level."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "03",
        shared_seam_candidates=[
            "backend/app/core/security.py auth boundary",
        ],
        execution_ready=True,
    )
    _make_seed_plan(planspace, anchors=[
        {"path": "backend/app/core/security.py", "purpose": "auth"},
    ])
    _make_shard(planspace, "03")

    with caplog.at_level(logging.INFO):
        result = _resolve_readiness(planspace, "03")

    assert result.ready is True
    assert any(
        "shared_seam_candidate" in rec.message and "resolved by substrate" in rec.message
        for rec in caplog.records
    )


def test_substrate_overlay_does_not_affect_other_blocking_fields(
    tmp_path: Path,
) -> None:
    """PRB-0006: overlay only touches shared_seam_candidates and unresolved_anchors."""
    planspace = tmp_path / "planspace"
    _make_proposal_state(
        planspace, "03",
        shared_seam_candidates=[
            "backend/app/core/security.py auth boundary",
        ],
        blocking_research_questions=["How should auth tokens be refreshed?"],
        execution_ready=True,
    )
    _make_seed_plan(planspace, anchors=[
        {"path": "backend/app/core/security.py", "purpose": "auth"},
    ])

    result = _resolve_readiness(planspace, "03")

    # shared_seam resolved but blocking_research_questions remains -> blocked
    assert result.ready is False
    descriptions = [b["description"] for b in result.blockers]
    assert any("auth tokens" in d for d in descriptions)
