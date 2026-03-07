"""Risk engagement mode selection."""

from __future__ import annotations

from lib.risk.types import RiskMode


def determine_engagement(
    step_count: int,
    file_count: int,
    has_shared_seams: bool,
    has_consequence_notes: bool,
    has_stale_inputs: bool,
    has_recent_failures: bool,
    has_tool_changes: bool,
    triage_confidence: str,
    freshness_changed: bool,
) -> RiskMode:
    """Determine whether ROAL can be skipped or must run in full."""
    should_skip = (
        step_count == 1
        and file_count <= 1
        and not has_shared_seams
        and not has_consequence_notes
        and not has_stale_inputs
        and not has_recent_failures
        and not has_tool_changes
        and triage_confidence.strip().lower() == "high"
        and not freshness_changed
    )
    if should_skip:
        return RiskMode.SKIP
    return RiskMode.FULL
