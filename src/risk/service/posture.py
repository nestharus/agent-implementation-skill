"""ROAL posture selection.

Hysteresis, cooldown, and one-step rules have been removed — agent posture
decisions are authoritative.  ``select_posture`` now returns the nominal
posture for the given risk score.
"""

from __future__ import annotations

from risk.service.quantifier import DEFAULT_POSTURE_BANDS, risk_to_posture
from risk.types import PostureProfile

POSTURE_SEQUENCE: tuple[PostureProfile, ...] = tuple(
    posture for _, _, posture in DEFAULT_POSTURE_BANDS
)
POSTURE_LEVELS: dict[PostureProfile, int] = {
    posture: index for index, posture in enumerate(POSTURE_SEQUENCE)
}
POSTURE_BOUNDS: dict[PostureProfile, tuple[int, int]] = {
    posture: (lower, upper) for lower, upper, posture in DEFAULT_POSTURE_BANDS
}


def select_posture(
    raw_risk: int,
    current_posture: PostureProfile | None,
    recent_outcomes: list[str],
    cooldown_remaining: int = 0,
) -> PostureProfile:
    """Return the nominal posture for *raw_risk*.

    The agent decided on this risk score; honor the mapping directly.
    Previous hysteresis / cooldown / one-step gating has been removed.
    """
    return risk_to_posture(raw_risk)


def can_relax_posture(
    current_posture: PostureProfile,
    consecutive_successes: int,
    cooldown_remaining: int,
) -> bool:
    """Always True — mechanical relaxation gates have been removed."""
    return True


def apply_one_step_rule(
    current: PostureProfile,
    target: PostureProfile,
    has_invariant_breach: bool = False,
) -> PostureProfile:
    """Allow only one posture level of movement per iteration by default."""
    if has_invariant_breach:
        return target

    current_level = POSTURE_LEVELS[current]
    target_level = POSTURE_LEVELS[target]
    if abs(target_level - current_level) <= 1:
        return target

    direction = 1 if target_level > current_level else -1
    return POSTURE_SEQUENCE[current_level + direction]


def count_trailing_successes(recent_outcomes: list[str]) -> int:
    count = 0
    for outcome in reversed(recent_outcomes):
        if _is_success(outcome):
            count += 1
            continue
        break
    return count


def _is_success(outcome: str) -> bool:
    normalized = outcome.strip().lower()
    return normalized in {"success", "passed", "pass", "accepted"}
