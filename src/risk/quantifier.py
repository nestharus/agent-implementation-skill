"""ROAL risk quantification and posture threshold helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from lib.core.artifact_io import read_json
from lib.risk.types import (
    AssessmentClass,
    DecisionClass,
    PostureProfile,
    RiskModifiers,
    RiskType,
    RiskVector,
    StepClass,
)

RISK_TYPES: tuple[RiskType, ...] = (
    RiskType.CONTEXT_ROT,
    RiskType.SILENT_DRIFT,
    RiskType.SCOPE_CREEP,
    RiskType.BRUTE_FORCE_REGRESSION,
    RiskType.CROSS_SECTION_INCOHERENCE,
    RiskType.TOOL_ISLAND_ISOLATION,
    RiskType.STALE_ARTIFACT_CONTAMINATION,
    RiskType.ECOSYSTEM_MATURITY,
    RiskType.DEPENDENCY_LOCK_IN,
    RiskType.TEAM_CAPABILITY,
    RiskType.SCALE_FIT,
    RiskType.INTEGRATION_FIT,
    RiskType.OPERABILITY_COST,
    RiskType.EVOLUTION_FLEXIBILITY,
)

CLASS_WEIGHTS: dict[AssessmentClass, dict[RiskType, float]] = {
    StepClass.EXPLORE: {
        RiskType.CONTEXT_ROT: 0.5,
        RiskType.SILENT_DRIFT: 0.6,
        RiskType.SCOPE_CREEP: 0.6,
        RiskType.BRUTE_FORCE_REGRESSION: 0.5,
        RiskType.CROSS_SECTION_INCOHERENCE: 0.5,
        RiskType.TOOL_ISLAND_ISOLATION: 0.5,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 0.5,
        RiskType.ECOSYSTEM_MATURITY: 0.0,
        RiskType.DEPENDENCY_LOCK_IN: 0.0,
        RiskType.TEAM_CAPABILITY: 0.0,
        RiskType.SCALE_FIT: 0.0,
        RiskType.INTEGRATION_FIT: 0.0,
        RiskType.OPERABILITY_COST: 0.0,
        RiskType.EVOLUTION_FLEXIBILITY: 0.0,
    },
    StepClass.STABILIZE: {
        RiskType.CONTEXT_ROT: 1.0,
        RiskType.SILENT_DRIFT: 1.4,
        RiskType.SCOPE_CREEP: 1.3,
        RiskType.BRUTE_FORCE_REGRESSION: 0.8,
        RiskType.CROSS_SECTION_INCOHERENCE: 1.0,
        RiskType.TOOL_ISLAND_ISOLATION: 0.9,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 1.1,
        RiskType.ECOSYSTEM_MATURITY: 0.0,
        RiskType.DEPENDENCY_LOCK_IN: 0.0,
        RiskType.TEAM_CAPABILITY: 0.0,
        RiskType.SCALE_FIT: 0.0,
        RiskType.INTEGRATION_FIT: 0.0,
        RiskType.OPERABILITY_COST: 0.0,
        RiskType.EVOLUTION_FLEXIBILITY: 0.0,
    },
    StepClass.EDIT: {
        RiskType.CONTEXT_ROT: 1.0,
        RiskType.SILENT_DRIFT: 1.1,
        RiskType.SCOPE_CREEP: 1.0,
        RiskType.BRUTE_FORCE_REGRESSION: 1.8,
        RiskType.CROSS_SECTION_INCOHERENCE: 1.7,
        RiskType.TOOL_ISLAND_ISOLATION: 1.0,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 1.2,
        RiskType.ECOSYSTEM_MATURITY: 0.0,
        RiskType.DEPENDENCY_LOCK_IN: 0.0,
        RiskType.TEAM_CAPABILITY: 0.0,
        RiskType.SCALE_FIT: 0.0,
        RiskType.INTEGRATION_FIT: 0.0,
        RiskType.OPERABILITY_COST: 0.0,
        RiskType.EVOLUTION_FLEXIBILITY: 0.0,
    },
    StepClass.COORDINATE: {
        RiskType.CONTEXT_ROT: 1.0,
        RiskType.SILENT_DRIFT: 1.2,
        RiskType.SCOPE_CREEP: 1.1,
        RiskType.BRUTE_FORCE_REGRESSION: 1.2,
        RiskType.CROSS_SECTION_INCOHERENCE: 2.0,
        RiskType.TOOL_ISLAND_ISOLATION: 1.3,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 1.8,
        RiskType.ECOSYSTEM_MATURITY: 0.0,
        RiskType.DEPENDENCY_LOCK_IN: 0.0,
        RiskType.TEAM_CAPABILITY: 0.0,
        RiskType.SCALE_FIT: 0.0,
        RiskType.INTEGRATION_FIT: 0.0,
        RiskType.OPERABILITY_COST: 0.0,
        RiskType.EVOLUTION_FLEXIBILITY: 0.0,
    },
    StepClass.VERIFY: {
        RiskType.CONTEXT_ROT: 0.8,
        RiskType.SILENT_DRIFT: 1.0,
        RiskType.SCOPE_CREEP: 0.8,
        RiskType.BRUTE_FORCE_REGRESSION: 0.9,
        RiskType.CROSS_SECTION_INCOHERENCE: 1.0,
        RiskType.TOOL_ISLAND_ISOLATION: 0.8,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 1.0,
        RiskType.ECOSYSTEM_MATURITY: 0.0,
        RiskType.DEPENDENCY_LOCK_IN: 0.0,
        RiskType.TEAM_CAPABILITY: 0.0,
        RiskType.SCALE_FIT: 0.0,
        RiskType.INTEGRATION_FIT: 0.0,
        RiskType.OPERABILITY_COST: 0.0,
        RiskType.EVOLUTION_FLEXIBILITY: 0.0,
    },
    DecisionClass.LOCAL: {
        RiskType.CONTEXT_ROT: 0.0,
        RiskType.SILENT_DRIFT: 0.0,
        RiskType.SCOPE_CREEP: 0.0,
        RiskType.BRUTE_FORCE_REGRESSION: 0.0,
        RiskType.CROSS_SECTION_INCOHERENCE: 0.0,
        RiskType.TOOL_ISLAND_ISOLATION: 0.0,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 0.0,
        RiskType.TEAM_CAPABILITY: 1.5,
        RiskType.INTEGRATION_FIT: 1.3,
        RiskType.OPERABILITY_COST: 1.0,
        RiskType.ECOSYSTEM_MATURITY: 0.8,
        RiskType.DEPENDENCY_LOCK_IN: 0.5,
        RiskType.SCALE_FIT: 0.5,
        RiskType.EVOLUTION_FLEXIBILITY: 0.5,
    },
    DecisionClass.COMPONENT: {
        RiskType.CONTEXT_ROT: 0.0,
        RiskType.SILENT_DRIFT: 0.0,
        RiskType.SCOPE_CREEP: 0.0,
        RiskType.BRUTE_FORCE_REGRESSION: 0.0,
        RiskType.CROSS_SECTION_INCOHERENCE: 0.0,
        RiskType.TOOL_ISLAND_ISOLATION: 0.0,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 0.0,
        RiskType.INTEGRATION_FIT: 1.5,
        RiskType.TEAM_CAPABILITY: 1.2,
        RiskType.ECOSYSTEM_MATURITY: 1.0,
        RiskType.OPERABILITY_COST: 1.0,
        RiskType.SCALE_FIT: 0.8,
        RiskType.DEPENDENCY_LOCK_IN: 0.8,
        RiskType.EVOLUTION_FLEXIBILITY: 0.7,
    },
    DecisionClass.CROSS_CUTTING: {
        RiskType.CONTEXT_ROT: 0.0,
        RiskType.SILENT_DRIFT: 0.0,
        RiskType.SCOPE_CREEP: 0.0,
        RiskType.BRUTE_FORCE_REGRESSION: 0.0,
        RiskType.CROSS_SECTION_INCOHERENCE: 0.0,
        RiskType.TOOL_ISLAND_ISOLATION: 0.0,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 0.0,
        RiskType.INTEGRATION_FIT: 1.8,
        RiskType.ECOSYSTEM_MATURITY: 1.5,
        RiskType.DEPENDENCY_LOCK_IN: 1.3,
        RiskType.SCALE_FIT: 1.2,
        RiskType.TEAM_CAPABILITY: 1.0,
        RiskType.OPERABILITY_COST: 1.0,
        RiskType.EVOLUTION_FLEXIBILITY: 1.0,
    },
    DecisionClass.PLATFORM: {
        RiskType.CONTEXT_ROT: 0.0,
        RiskType.SILENT_DRIFT: 0.0,
        RiskType.SCOPE_CREEP: 0.0,
        RiskType.BRUTE_FORCE_REGRESSION: 0.0,
        RiskType.CROSS_SECTION_INCOHERENCE: 0.0,
        RiskType.TOOL_ISLAND_ISOLATION: 0.0,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 0.0,
        RiskType.ECOSYSTEM_MATURITY: 1.8,
        RiskType.DEPENDENCY_LOCK_IN: 1.6,
        RiskType.SCALE_FIT: 1.5,
        RiskType.INTEGRATION_FIT: 1.3,
        RiskType.OPERABILITY_COST: 1.2,
        RiskType.EVOLUTION_FLEXIBILITY: 1.0,
        RiskType.TEAM_CAPABILITY: 0.8,
    },
    DecisionClass.IRREVERSIBLE: {
        RiskType.CONTEXT_ROT: 0.0,
        RiskType.SILENT_DRIFT: 0.0,
        RiskType.SCOPE_CREEP: 0.0,
        RiskType.BRUTE_FORCE_REGRESSION: 0.0,
        RiskType.CROSS_SECTION_INCOHERENCE: 0.0,
        RiskType.TOOL_ISLAND_ISOLATION: 0.0,
        RiskType.STALE_ARTIFACT_CONTAMINATION: 0.0,
        RiskType.DEPENDENCY_LOCK_IN: 2.0,
        RiskType.EVOLUTION_FLEXIBILITY: 1.8,
        RiskType.ECOSYSTEM_MATURITY: 1.5,
        RiskType.SCALE_FIT: 1.3,
        RiskType.INTEGRATION_FIT: 1.2,
        RiskType.OPERABILITY_COST: 1.0,
        RiskType.TEAM_CAPABILITY: 0.8,
    },
}

DEFAULT_POSTURE_BANDS: tuple[tuple[int, int, PostureProfile], ...] = (
    (0, 19, PostureProfile.P0_DIRECT),
    (20, 39, PostureProfile.P1_LIGHT),
    (40, 59, PostureProfile.P2_STANDARD),
    (60, 79, PostureProfile.P3_GUARDED),
    (80, 100, PostureProfile.P4_REOPEN),
)

DEFAULT_CLASS_THRESHOLDS: dict[AssessmentClass, int] = {
    StepClass.EXPLORE: 60,
    StepClass.STABILIZE: 60,
    StepClass.EDIT: 45,
    StepClass.COORDINATE: 35,
    StepClass.VERIFY: 50,
    DecisionClass.LOCAL: 55,
    DecisionClass.COMPONENT: 45,
    DecisionClass.CROSS_CUTTING: 35,
    DecisionClass.PLATFORM: 30,
    DecisionClass.IRREVERSIBLE: 20,
}

DEFAULT_RISK_PARAMETERS: dict[str, Any] = {
    "posture_bands": [
        {"min": lower, "max": upper, "posture": posture.value}
        for lower, upper, posture in DEFAULT_POSTURE_BANDS
    ],
    "class_thresholds": {
        assessment_class.value: threshold
        for assessment_class, threshold in DEFAULT_CLASS_THRESHOLDS.items()
    },
}

MAX_SEVERITY = 4
RISK_MIN = 0
RISK_MAX = 100
RISK_MIDPOINT = 50.0
BLAST_RADIUS_FACTOR = 4.0
REVERSIBILITY_FACTOR = 5.0
OBSERVABILITY_FACTOR = 4.0
CONFIDENCE_PULL_FACTOR = 0.35
HISTORY_ADJUSTMENT_BOUND = 10.0


def compute_raw_risk(
    risk_vector: RiskVector,
    modifiers: RiskModifiers,
    assessment_class: AssessmentClass,
    history_adjustment: float = 0.0,
) -> int:
    """Compute a 0-100 raw risk score from ROAL risk inputs."""
    weights = CLASS_WEIGHTS[assessment_class]
    weighted_sum = sum(
        _severity_for(risk_vector, risk_type) * weights[risk_type]
        for risk_type in RISK_TYPES
    )
    max_weighted_sum = sum(MAX_SEVERITY * weights[risk_type] for risk_type in RISK_TYPES)
    score = (weighted_sum / max_weighted_sum) * RISK_MAX if max_weighted_sum else 0.0

    score += _modifier_adjustment(modifiers)
    score = _clamp_float(score, RISK_MIN, RISK_MAX)

    confidence = _clamp_float(modifiers.confidence, 0.0, 1.0)
    uncertainty = 1.0 - confidence
    score += (RISK_MIDPOINT - score) * uncertainty * CONFIDENCE_PULL_FACTOR

    score += _clamp_float(
        history_adjustment,
        -HISTORY_ADJUSTMENT_BOUND,
        HISTORY_ADJUSTMENT_BOUND,
    )
    return int(round(_clamp_float(score, RISK_MIN, RISK_MAX)))


def risk_to_posture(raw_risk: int) -> PostureProfile:
    """Map a raw risk score onto the default posture bands."""
    bounded_risk = _clamp_int(raw_risk, RISK_MIN, RISK_MAX)
    for lower, upper, posture in DEFAULT_POSTURE_BANDS:
        if lower <= bounded_risk <= upper:
            return posture
    return DEFAULT_POSTURE_BANDS[-1][2]


def is_acceptable(raw_risk: int, assessment_class: AssessmentClass) -> bool:
    """Return whether the step can execute under default ROAL thresholds."""
    threshold = DEFAULT_CLASS_THRESHOLDS[assessment_class]
    return _clamp_int(raw_risk, RISK_MIN, RISK_MAX) <= threshold


def load_risk_parameters(path: Path) -> dict[str, Any]:
    """Load optional risk parameter overrides from disk."""
    defaults = deepcopy(DEFAULT_RISK_PARAMETERS)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return defaults

    posture_bands = payload.get("posture_bands")
    if isinstance(posture_bands, list):
        defaults["posture_bands"] = posture_bands

    thresholds = payload.get("class_thresholds")
    if not isinstance(thresholds, dict):
        thresholds = payload.get("execution_thresholds")
    if isinstance(thresholds, dict):
        defaults["class_thresholds"].update(
            {
                str(key): value
                for key, value in thresholds.items()
                if isinstance(value, int)
            }
        )
    return defaults


def _modifier_adjustment(modifiers: RiskModifiers) -> float:
    blast_radius = _clamp_int(modifiers.blast_radius, 0, MAX_SEVERITY)
    reversibility = _clamp_int(modifiers.reversibility, 0, MAX_SEVERITY)
    observability = _clamp_int(modifiers.observability, 0, MAX_SEVERITY)
    return (
        blast_radius * BLAST_RADIUS_FACTOR
        + (2 - reversibility) * REVERSIBILITY_FACTOR
        + (2 - observability) * OBSERVABILITY_FACTOR
    )


def _severity_for(risk_vector: RiskVector, risk_type: RiskType) -> int:
    raw_value = getattr(risk_vector, risk_type.value)
    return _clamp_int(int(raw_value), 0, MAX_SEVERITY)


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _clamp_float(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
