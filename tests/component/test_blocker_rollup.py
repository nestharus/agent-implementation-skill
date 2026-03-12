from __future__ import annotations

import json
from pathlib import Path

from signals.service.blocker_manager import _update_blocker_rollup


def test_blocker_rollup_formats_global_philosophy_heading(
    planspace: Path,
) -> None:
    signal_path = (
        planspace / "artifacts" / "signals" / "philosophy-bootstrap-signal.json"
    )
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(json.dumps({
        "section": "global",
        "state": "NEEDS_PARENT",
        "detail": "Philosophy bootstrap failed.",
        "needs": "Repair the bootstrap.",
        "why_blocked": "Global philosophy is unavailable.",
    }), encoding="utf-8")

    _update_blocker_rollup(planspace)

    rollup_path = planspace / "artifacts" / "decisions" / "needs-input.md"
    content = rollup_path.read_text(encoding="utf-8")
    assert "## Global — philosophy bootstrap" in content
    assert "## Section global" not in content


def test_blocker_rollup_maps_shared_seams_to_needs_parent_category(
    planspace: Path,
) -> None:
    readiness_path = (
        planspace / "artifacts" / "readiness" / "section-03-execution-ready.json"
    )
    readiness_path.write_text(json.dumps({
        "ready": False,
        "blockers": [
            {
                "type": "shared_seam_candidates",
                "description": "shared client cache",
            }
        ],
        "rationale": "blocked",
    }), encoding="utf-8")

    _update_blocker_rollup(planspace)

    rollup_path = planspace / "artifacts" / "decisions" / "needs-input.md"
    content = rollup_path.read_text(encoding="utf-8")
    assert "# Parent Coordination / Decision Required (NEEDS_PARENT)" in content
    assert "## Section 03 — proposal-state:shared_seam_candidates" in content
    assert "- **Detail**: shared client cache" in content
    assert "# Scope Expansion (OUT_OF_SCOPE)" not in content


def test_blocker_rollup_separates_shared_seam_coordination_from_scope_expansion(
    planspace: Path,
) -> None:
    seam_signal = planspace / "artifacts" / "signals" / "section-03-seam-0-signal.json"
    seam_signal.write_text(json.dumps({
        "section": "03",
        "state": "needs_parent",
        "detail": (
            "Shared seam candidate requires cross-section substrate work: "
            "shared client cache"
        ),
        "needs": "SIS/substrate coordination for shared seam",
        "why_blocked": "Shared seam requires substrate-level coordination.",
        "source": "proposal-state:shared_seam_candidates",
    }), encoding="utf-8")

    readiness_path = (
        planspace / "artifacts" / "readiness" / "section-03-execution-ready.json"
    )
    readiness_path.write_text(json.dumps({
        "ready": False,
        "blockers": [
            {
                "type": "shared_seam_candidates",
                "description": "shared client cache",
            }
        ],
        "rationale": "blocked",
    }), encoding="utf-8")

    scope_signal = (
        planspace / "artifacts" / "signals" / "section-04-oos-signal.json"
    )
    scope_signal.write_text(json.dumps({
        "section": "04",
        "state": "out_of_scope",
        "detail": "Add a new admin reporting surface.",
        "needs": "Expand scope or create a new section.",
        "why_blocked": "Concern does not belong to section 04.",
    }), encoding="utf-8")

    _update_blocker_rollup(planspace)

    rollup_path = planspace / "artifacts" / "decisions" / "needs-input.md"
    content = rollup_path.read_text(encoding="utf-8")
    assert "# Parent Coordination / Decision Required (NEEDS_PARENT)" in content
    assert "# Scope Expansion (OUT_OF_SCOPE)" in content
    assert "Shared seam candidate requires cross-section substrate work: shared client cache" in content
    assert "## Section 04 — out_of_scope" in content
    assert "- **Detail**: Add a new admin reporting surface." in content
    assert content.count("shared client cache") == 1
    assert "## Section 03 — proposal-state:shared_seam_candidates" not in content
