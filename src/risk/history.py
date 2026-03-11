"""Append-only ROAL risk history utilities."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from risk.serialization import deserialize_history_entry, serialize_history_entry
from risk.types import AssessmentClass, RiskHistoryEntry, RiskType

logger = logging.getLogger(__name__)

SIMILARITY_ADJUSTMENT_SCALE = 0.2
HISTORY_ADJUSTMENT_BOUND = 10.0


def append_history_entry(history_path: Path, entry: RiskHistoryEntry) -> None:
    """Append a single entry to the JSONL history file."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(serialize_history_entry(entry), separators=(",", ":")))
        handle.write("\n")


def read_history(history_path: Path) -> list[RiskHistoryEntry]:
    """Read all history entries."""
    if not history_path.exists():
        return []

    try:
        entries: list[RiskHistoryEntry] = []
        with history_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                    if not isinstance(payload, dict):
                        raise ValueError("History line must be a JSON object")
                    entries.append(deserialize_history_entry(payload))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "Skipping malformed risk history line %s:%s: %s",
                        history_path,
                        line_number,
                        exc,
                    )
        return entries
    except OSError as exc:
        logger.warning("Malformed risk history at %s: %s", history_path, exc)
        _rename_malformed_history(history_path)
        return []


def compute_history_adjustment(
    history_path: Path,
    assessment_class: AssessmentClass,
    dominant_risks: list[RiskType],
    blast_radius_band: int,
) -> float:
    """Compute a bounded risk adjustment from similar historical outcomes."""
    history = read_history(history_path)
    if not history:
        return 0.0

    requested_risks = set(dominant_risks)
    deltas = [
        _actual_outcome_score(entry) - entry.predicted_risk
        for entry in history
        if entry.assessment_class == assessment_class
        and entry.blast_radius_band == blast_radius_band
        and _has_overlap(requested_risks, set(entry.dominant_risks))
    ]
    if not deltas:
        return 0.0

    average_delta = sum(deltas) / len(deltas)
    return _clamp_float(
        average_delta * SIMILARITY_ADJUSTMENT_SCALE,
        -HISTORY_ADJUSTMENT_BOUND,
        HISTORY_ADJUSTMENT_BOUND,
    )


def pattern_signature(
    assessment_class: AssessmentClass,
    dominant_risks: list[RiskType],
    blast_radius_band: int,
) -> str:
    """Create a stable pattern key for history matching."""
    ordered_risks = ",".join(sorted({risk.value for risk in dominant_risks}))
    return f"{assessment_class.value}|{blast_radius_band}|{ordered_risks}"


def _has_overlap(requested: set[RiskType], candidate: set[RiskType]) -> bool:
    if not requested and not candidate:
        return True
    return bool(requested & candidate)


def _actual_outcome_score(entry: RiskHistoryEntry) -> int:
    outcome = entry.actual_outcome.strip().lower()
    verification = (entry.verification_outcome or "").strip().lower()

    if outcome in {"failure", "failed", "blocked", "reopen", "reopened"}:
        score = 85
    elif outcome == "risk_review_failure":
        score = 90
    elif outcome in {"mixed", "partial", "warning"}:
        score = 60
    elif outcome == "deferred":
        score = 55
    elif outcome == "over_guarded":
        score = 15
    elif outcome in {"success", "passed", "accepted"}:
        score = 20
    else:
        score = 50

    if verification in {"failure", "failed", "blocked"}:
        score += 10
    elif verification in {"success", "passed"}:
        score -= 5

    score += min(len(entry.surfaced_surprises) * 5, 10)
    return _clamp_int(score, 0, 100)


def _rename_malformed_history(history_path: Path) -> Path | None:
    if not history_path.exists():
        return None

    malformed_path = history_path.with_suffix(".malformed.json")
    try:
        history_path.rename(malformed_path)
        logger.warning(
            "Preserved malformed risk history: %s -> %s",
            history_path,
            malformed_path,
        )
        return malformed_path
    except OSError as exc:
        logger.warning(
            "Failed to preserve malformed risk history %s: %s",
            history_path,
            exc,
        )
        return None


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _clamp_float(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
