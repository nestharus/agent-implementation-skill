"""Risk engagement mode selection."""

from __future__ import annotations

from risk.types import EngagementContext, RiskMode

# Minimum step/file counts that trigger full risk assessment
_FULL_ASSESSMENT_FILE_THRESHOLD = 3
_FULL_ASSESSMENT_STEP_THRESHOLD = 3


def determine_engagement(
    step_count: int,
    file_count: int,
    ctx: EngagementContext,
    triage_confidence: str,
    risk_mode_hint: str = "",
) -> RiskMode:
    """Determine whether ROAL runs lightly or in full."""
    normalized_hint = risk_mode_hint.strip().lower()

    if normalized_hint == RiskMode.FULL.value:
        return RiskMode.FULL
    if normalized_hint == RiskMode.LIGHT.value:
        return RiskMode.FULL if ctx.skip_floor_hit else RiskMode.LIGHT
    # Legacy normalization: stale persisted artifacts may contain "skip".
    if normalized_hint == "skip":
        return RiskMode.FULL if ctx.skip_floor_hit else RiskMode.LIGHT

    # Design decisions always require full assessment
    if ctx.has_decision_classes or ctx.has_unresolved_value_scales:
        return RiskMode.FULL

    should_run_full = (
        ctx.has_shared_seams
        or ctx.has_consequence_notes
        or ctx.has_stale_inputs
        or ctx.has_recent_failures
        or file_count > _FULL_ASSESSMENT_FILE_THRESHOLD
        or step_count > _FULL_ASSESSMENT_STEP_THRESHOLD
        or triage_confidence.strip().lower() == "low"
    )
    if should_run_full:
        return RiskMode.FULL

    return RiskMode.LIGHT
