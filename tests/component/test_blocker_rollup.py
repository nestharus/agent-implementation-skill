from __future__ import annotations

import json
from pathlib import Path

from section_loop.section_engine.blockers import _update_blocker_rollup


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
