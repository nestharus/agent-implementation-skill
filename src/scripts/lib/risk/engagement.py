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
    risk_mode_hint: str = "",
) -> RiskMode:
    """Determine whether ROAL can be skipped, run lightly, or run in full."""
    normalized_hint = risk_mode_hint.strip().lower()
    skip_floor_hit = (
        has_shared_seams
        or has_stale_inputs
        or has_recent_failures
    )

    if normalized_hint == RiskMode.FULL.value:
        return RiskMode.FULL
    if normalized_hint == RiskMode.LIGHT.value:
        return RiskMode.FULL if skip_floor_hit else RiskMode.LIGHT
    if normalized_hint == "skip":
        return RiskMode.FULL if skip_floor_hit else RiskMode.LIGHT

    should_run_full = (
        has_shared_seams
        or has_consequence_notes
        or has_stale_inputs
        or has_recent_failures
        or file_count > 3
        or step_count > 3
        or triage_confidence.strip().lower() == "low"
    )
    if should_run_full:
        return RiskMode.FULL

    return RiskMode.LIGHT
