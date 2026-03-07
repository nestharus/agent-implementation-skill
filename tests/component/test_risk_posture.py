"""Component tests for ROAL posture control."""

from __future__ import annotations

from lib.risk.posture import (
    apply_one_step_rule,
    can_relax_posture,
    select_posture,
)
from lib.risk.types import PostureProfile


def test_select_posture_returns_nominal_posture_without_current_state() -> None:
    posture = select_posture(
        raw_risk=45,
        current_posture=None,
        recent_outcomes=[],
    )

    assert posture == PostureProfile.P2_STANDARD


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


def test_cooldown_prevents_relaxation_after_failure() -> None:
    posture = select_posture(
        raw_risk=35,
        current_posture=PostureProfile.P3_GUARDED,
        recent_outcomes=["success", "success"],
        cooldown_remaining=1,
    )

    assert posture == PostureProfile.P3_GUARDED


def test_asymmetric_evidence_tightens_on_one_failure_and_relaxes_after_successes() -> None:
    tightened = select_posture(
        raw_risk=22,
        current_posture=PostureProfile.P1_LIGHT,
        recent_outcomes=["failure"],
    )
    held = select_posture(
        raw_risk=35,
        current_posture=PostureProfile.P3_GUARDED,
        recent_outcomes=["success", "success"],
        cooldown_remaining=0,
    )
    relaxed = select_posture(
        raw_risk=35,
        current_posture=PostureProfile.P3_GUARDED,
        recent_outcomes=["success", "success", "success"],
        cooldown_remaining=0,
    )

    assert tightened == PostureProfile.P2_STANDARD
    assert held == PostureProfile.P3_GUARDED
    assert relaxed == PostureProfile.P2_STANDARD


def test_can_relax_posture_various_inputs() -> None:
    assert can_relax_posture(PostureProfile.P2_STANDARD, 3, 0) is True
    assert can_relax_posture(PostureProfile.P2_STANDARD, 2, 0) is False
    assert can_relax_posture(PostureProfile.P2_STANDARD, 3, 1) is False
    assert can_relax_posture(PostureProfile.P0_DIRECT, 3, 0) is False
