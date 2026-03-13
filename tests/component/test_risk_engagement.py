"""Component tests for ROAL engagement mode selection."""

from __future__ import annotations

from risk.service.engagement import determine_engagement
from risk.types import EngagementContext, RiskMode


def test_single_bounded_step_with_high_confidence_returns_light() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        ctx=EngagementContext(),
        triage_confidence="high",
    )

    assert mode == RiskMode.LIGHT


def test_moderate_complexity_returns_light() -> None:
    mode = determine_engagement(
        step_count=2,
        file_count=2,
        ctx=EngagementContext(),
        triage_confidence="high",
    )

    assert mode == RiskMode.LIGHT


def test_shared_seams_trigger_full() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        ctx=EngagementContext(has_shared_seams=True),
        triage_confidence="high",
    )

    assert mode == RiskMode.FULL


def test_risk_mode_hint_full_forces_full() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        ctx=EngagementContext(),
        triage_confidence="high",
        risk_mode_hint="full",
    )

    assert mode == RiskMode.FULL


def test_risk_mode_hint_skip_normalized_to_light() -> None:
    """Incoming skip hint is normalized to light (no skip mode)."""
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        ctx=EngagementContext(),
        triage_confidence="high",
        risk_mode_hint="skip",
    )

    assert mode == RiskMode.LIGHT


def test_risk_mode_hint_skip_respects_safety_floor() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        ctx=EngagementContext(has_shared_seams=True),
        triage_confidence="high",
        risk_mode_hint="skip",
    )

    assert mode == RiskMode.FULL


def test_risk_mode_hint_light_is_first_class() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        ctx=EngagementContext(freshness_changed=True),
        triage_confidence="low",
        risk_mode_hint="light",
    )

    assert mode == RiskMode.LIGHT


def test_risk_mode_hint_light_respects_safety_floor() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        ctx=EngagementContext(has_shared_seams=True),
        triage_confidence="high",
        risk_mode_hint="light",
    )

    assert mode == RiskMode.FULL


def test_boundary_between_light_and_full() -> None:
    light_mode = determine_engagement(
        step_count=3,
        file_count=3,
        ctx=EngagementContext(),
        triage_confidence="medium",
    )
    full_mode = determine_engagement(
        step_count=3,
        file_count=4,
        ctx=EngagementContext(),
        triage_confidence="medium",
    )

    assert light_mode == RiskMode.LIGHT
    assert full_mode == RiskMode.FULL


def test_stale_inputs_trigger_full() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        ctx=EngagementContext(has_stale_inputs=True),
        triage_confidence="high",
    )

    assert mode == RiskMode.FULL
