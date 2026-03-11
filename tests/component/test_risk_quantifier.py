"""Component tests for ROAL risk quantification."""

from __future__ import annotations

from risk.service.quantifier import (
    CLASS_WEIGHTS,
    compute_raw_risk,
    is_acceptable,
    risk_to_posture,
)
from risk.types import PostureProfile, RiskModifiers, RiskType, RiskVector, StepClass


def test_compute_raw_risk_zero_vector_returns_low_score() -> None:
    score = compute_raw_risk(
        RiskVector(),
        RiskModifiers(confidence=1.0),
        StepClass.EXPLORE,
    )

    assert 0 <= score <= 10


def test_compute_raw_risk_max_vector_returns_high_score() -> None:
    score = compute_raw_risk(
        RiskVector(
            context_rot=4,
            silent_drift=4,
            scope_creep=4,
            brute_force_regression=4,
            cross_section_incoherence=4,
            tool_island_isolation=4,
            stale_artifact_contamination=4,
        ),
        RiskModifiers(
            blast_radius=4,
            reversibility=0,
            observability=0,
            confidence=1.0,
        ),
        StepClass.EDIT,
    )

    assert score >= 90


def test_class_weights_change_scoring() -> None:
    vector = RiskVector(
        brute_force_regression=4,
        cross_section_incoherence=4,
    )
    modifiers = RiskModifiers(confidence=1.0)

    explore_score = compute_raw_risk(vector, modifiers, StepClass.EXPLORE)
    edit_score = compute_raw_risk(vector, modifiers, StepClass.EDIT)
    coordinate_score = compute_raw_risk(vector, modifiers, StepClass.COORDINATE)

    assert explore_score < edit_score
    assert edit_score != coordinate_score


def test_modifier_penalties_amplify_and_good_reversibility_reduces() -> None:
    vector = RiskVector(
        context_rot=2,
        silent_drift=2,
        scope_creep=2,
    )

    safer = compute_raw_risk(
        vector,
        RiskModifiers(
            blast_radius=0,
            reversibility=4,
            observability=4,
            confidence=1.0,
        ),
        StepClass.STABILIZE,
    )
    riskier = compute_raw_risk(
        vector,
        RiskModifiers(
            blast_radius=4,
            reversibility=0,
            observability=0,
            confidence=1.0,
        ),
        StepClass.STABILIZE,
    )

    assert safer < riskier


def test_single_dominant_risk_uses_step_specific_weights() -> None:
    edit_weights = CLASS_WEIGHTS[StepClass.EDIT]
    strongest_risk = max(edit_weights, key=edit_weights.get)
    weakest_risk = min(edit_weights, key=edit_weights.get)

    strongest_vector = RiskVector()
    setattr(strongest_vector, strongest_risk.value, 4)
    weakest_vector = RiskVector()
    setattr(weakest_vector, weakest_risk.value, 4)

    strongest_score = compute_raw_risk(
        strongest_vector,
        RiskModifiers(confidence=1.0),
        StepClass.EDIT,
    )
    weakest_score = compute_raw_risk(
        weakest_vector,
        RiskModifiers(confidence=1.0),
        StepClass.EDIT,
    )

    assert strongest_risk == RiskType.BRUTE_FORCE_REGRESSION
    assert strongest_score > weakest_score


def test_confidence_penalty_moves_score_toward_middle() -> None:
    vector = RiskVector(context_rot=1)

    confident = compute_raw_risk(
        vector,
        RiskModifiers(confidence=1.0),
        StepClass.EXPLORE,
    )
    uncertain = compute_raw_risk(
        vector,
        RiskModifiers(confidence=0.0),
        StepClass.EXPLORE,
    )

    assert abs(uncertain - 50) < abs(confident - 50)


def test_history_adjustment_shifts_score_within_bounds() -> None:
    vector = RiskVector(
        silent_drift=2,
        scope_creep=2,
        stale_artifact_contamination=1,
    )
    modifiers = RiskModifiers(confidence=1.0)

    baseline = compute_raw_risk(vector, modifiers, StepClass.STABILIZE)
    increased = compute_raw_risk(
        vector,
        modifiers,
        StepClass.STABILIZE,
        history_adjustment=10.0,
    )
    decreased = compute_raw_risk(
        vector,
        modifiers,
        StepClass.STABILIZE,
        history_adjustment=-10.0,
    )

    assert decreased < baseline < increased
    assert 0 <= decreased <= 100
    assert 0 <= increased <= 100


def test_risk_to_posture_maps_default_bands() -> None:
    assert risk_to_posture(0) == PostureProfile.P0_DIRECT
    assert risk_to_posture(19) == PostureProfile.P0_DIRECT
    assert risk_to_posture(20) == PostureProfile.P1_LIGHT
    assert risk_to_posture(39) == PostureProfile.P1_LIGHT
    assert risk_to_posture(40) == PostureProfile.P2_STANDARD
    assert risk_to_posture(59) == PostureProfile.P2_STANDARD
    assert risk_to_posture(60) == PostureProfile.P3_GUARDED
    assert risk_to_posture(79) == PostureProfile.P3_GUARDED
    assert risk_to_posture(80) == PostureProfile.P4_REOPEN
    assert risk_to_posture(100) == PostureProfile.P4_REOPEN


def test_is_acceptable_uses_class_thresholds() -> None:
    assert is_acceptable(50, StepClass.EXPLORE) is True
    assert is_acceptable(50, StepClass.VERIFY) is True
    assert is_acceptable(50, StepClass.EDIT) is False
    assert is_acceptable(50, StepClass.COORDINATE) is False
