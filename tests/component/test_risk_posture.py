"""Component tests for ROAL posture control.

Hysteresis and mechanical gates have been removed — select_posture now
returns the nominal posture for the given risk score regardless of history.
"""

from __future__ import annotations

from risk.service.posture import (
    apply_one_step_rule,
    can_relax_posture,
    select_posture,
)
from risk.types import PostureProfile


def test_select_posture_returns_nominal_posture_without_current_state() -> None:
    posture = select_posture(
        raw_risk=45,
        current_posture=None,
        recent_outcomes=[],
    )

    assert posture == PostureProfile.P2_STANDARD


def test_select_posture_returns_nominal_posture_ignoring_history() -> None:
    """select_posture always returns the nominal posture for the risk score."""
    posture = select_posture(
        raw_risk=45,
        current_posture=PostureProfile.P0_DIRECT,
        recent_outcomes=["success", "success", "success"],
    )

    assert posture == PostureProfile.P2_STANDARD


def test_select_posture_ignores_cooldown() -> None:
    """Cooldown no longer prevents posture changes."""
    posture = select_posture(
        raw_risk=35,
        current_posture=PostureProfile.P3_GUARDED,
        recent_outcomes=["success", "success"],
        cooldown_remaining=1,
    )

    # Nominal for risk=35 is P1_LIGHT, not held at P3
    assert posture == PostureProfile.P1_LIGHT


def test_one_step_rule_prevents_large_jumps() -> None:
    assert (
        apply_one_step_rule(
            PostureProfile.P0_DIRECT,
            PostureProfile.P3_GUARDED,
        )
        == PostureProfile.P1_LIGHT
    )
    assert (
        apply_one_step_rule(
            PostureProfile.P0_DIRECT,
            PostureProfile.P3_GUARDED,
            has_invariant_breach=True,
        )
        == PostureProfile.P3_GUARDED
    )


def test_can_relax_posture_always_true() -> None:
    """Mechanical relaxation gates have been removed."""
    assert can_relax_posture(PostureProfile.P2_STANDARD, 3, 0) is True
    assert can_relax_posture(PostureProfile.P2_STANDARD, 2, 0) is True
    assert can_relax_posture(PostureProfile.P2_STANDARD, 3, 1) is True
    assert can_relax_posture(PostureProfile.P0_DIRECT, 3, 0) is True
