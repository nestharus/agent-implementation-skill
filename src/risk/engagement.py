"""Risk engagement mode selection."""

from __future__ import annotations

from risk.types import RiskMode


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
    has_decision_classes: bool = False,
    has_unresolved_value_scales: bool = False,
) -> RiskMode:
    """Determine whether ROAL runs lightly or in full."""
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
    # Legacy normalization: stale persisted artifacts may contain "skip".
    if normalized_hint == "skip":
        return RiskMode.FULL if skip_floor_hit else RiskMode.LIGHT

    # Design decisions always require full assessment
    if has_decision_classes or has_unresolved_value_scales:
        return RiskMode.FULL

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
