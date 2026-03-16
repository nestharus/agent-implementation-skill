"""Posture hysteresis logic for ROAL risk decisions.

Mechanical hysteresis has been removed — agent posture decisions are
authoritative.  ``apply_posture_hysteresis`` is now a no-op kept for
call-site compatibility.
"""

from __future__ import annotations

from risk.repository.history import pattern_signature
from risk.types import (
    PostureProfile,
    RiskAssessment,
    RiskHistoryEntry,
    RiskPlan,
    StepAssessment,
)


def apply_posture_hysteresis(
    plan: RiskPlan,
    assessment: RiskAssessment,
    history_entries: list[RiskHistoryEntry],
    parameters: dict,
    *,
    posture_floor: PostureProfile | str | None,
) -> None:
    """No-op: agent posture decisions are authoritative.

    Previously this mechanically adjusted postures via hysteresis windows,
    cooldown, and one-step rules.  Now the agent's posture stands as-is.
    """


def history_signature(entry: RiskHistoryEntry) -> str:
    return pattern_signature(
        entry.assessment_class,
        entry.dominant_risks,
        entry.blast_radius_band,
    )
