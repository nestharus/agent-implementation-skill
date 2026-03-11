"""ROAL posture selection with hysteresis and oscillation control."""

from __future__ import annotations

from risk.quantifier import DEFAULT_POSTURE_BANDS, risk_to_posture
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
HYSTERESIS_WINDOW = 5
RELAX_SUCCESS_THRESHOLD = 3
DEFAULT_FAILURE_COOLDOWN = 2


def select_posture(
    raw_risk: int,
    current_posture: PostureProfile | None,
    recent_outcomes: list[str],
    cooldown_remaining: int = 0,
) -> PostureProfile:
    """Select a posture while resisting score-boundary oscillation."""
    nominal = risk_to_posture(raw_risk)
    if current_posture is None:
        return nominal

    current_level = POSTURE_LEVELS[current_posture]
    nominal_level = POSTURE_LEVELS[nominal]
    recent_failure = bool(recent_outcomes) and _is_failure(recent_outcomes[-1])
    consecutive_successes = _count_trailing_successes(recent_outcomes)
    target = current_posture

    if recent_failure:
        target = _posture_for_level(min(current_level + 1, len(POSTURE_SEQUENCE) - 1))
        if nominal_level > POSTURE_LEVELS[target]:
            target = nominal
    elif nominal_level > current_level:
        if raw_risk >= _tighten_threshold(current_posture):
            target = nominal
    elif nominal_level < current_level:
        if raw_risk <= _relax_threshold(current_posture) and can_relax_posture(
            current_posture,
            consecutive_successes,
            cooldown_remaining,
        ):
            target = nominal

    return apply_one_step_rule(current_posture, target)


def can_relax_posture(
    current_posture: PostureProfile,
    consecutive_successes: int,
    cooldown_remaining: int,
) -> bool:
    """Return whether posture relaxation is currently allowed."""
    return (
        current_posture != PostureProfile.P0_DIRECT
        and cooldown_remaining <= 0
        and consecutive_successes >= RELAX_SUCCESS_THRESHOLD
    )


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
    return _posture_for_level(current_level + direction)


def _tighten_threshold(posture: PostureProfile) -> int:
    _, upper = POSTURE_BOUNDS[posture]
    if posture == POSTURE_SEQUENCE[-1]:
        return upper
    return upper + HYSTERESIS_WINDOW


def _relax_threshold(posture: PostureProfile) -> int:
    lower, _ = POSTURE_BOUNDS[posture]
    if posture == POSTURE_SEQUENCE[0]:
        return lower
    return lower - HYSTERESIS_WINDOW


def _posture_for_level(level: int) -> PostureProfile:
    return POSTURE_SEQUENCE[level]


def _count_trailing_successes(recent_outcomes: list[str]) -> int:
    count = 0
    for outcome in reversed(recent_outcomes):
        if _is_success(outcome):
            count += 1
            continue
        break
    return count


def _is_failure(outcome: str) -> bool:
    normalized = outcome.strip().lower()
    return normalized in {"failure", "failed", "error", "reopen", "blocked"}


def _is_success(outcome: str) -> bool:
    normalized = outcome.strip().lower()
    return normalized in {"success", "passed", "pass", "accepted"}
