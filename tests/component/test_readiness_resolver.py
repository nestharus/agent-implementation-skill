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
