"""Append-only ROAL risk history utilities."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from containers import Services
from risk.repository.serialization import deserialize_history_entry, serialize_history_entry
from risk.types import AssessmentClass, RiskHistoryEntry, RiskType, clamp_float, clamp_int

logger = logging.getLogger(__name__)

SIMILARITY_ADJUSTMENT_SCALE = 0.2
HISTORY_ADJUSTMENT_BOUND = 10.0

# Per-surprise score contribution in _actual_outcome_score
_SURPRISE_SCORE_WEIGHT = 5
_SURPRISE_SCORE_CAP = 10

# Outcome string → base score for _actual_outcome_score
_OUTCOME_SCORE: dict[str, int] = {
    "failure": 85,
    "failed": 85,
    "blocked": 85,
    "reopen": 85,
    "reopened": 85,
    "risk_review_failure": 90,
    "mixed": 60,
    "partial": 60,
    "warning": 60,
    "deferred": 55,
    "over_guarded": 15,
    "success": 20,
    "passed": 20,
    "accepted": 20,
}

# Verification outcome → score adjustment
_VERIFICATION_ADJUSTMENT: dict[str, int] = {
    "failure": 10,
    "failed": 10,
    "blocked": 10,
    "success": -5,
    "passed": -5,
}


def append_history_entry(history_path: Path, entry: RiskHistoryEntry) -> None:
    """Append a single entry to the JSONL history file."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(serialize_history_entry(entry), separators=(",", ":")))
        handle.write("\n")


def _parse_history_line(
    stripped: str, history_path: Path, line_number: int,
) -> RiskHistoryEntry | None:
    """Parse a single JSONL line into a RiskHistoryEntry, or None on error."""
    try:
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError("History line must be a JSON object")
        return deserialize_history_entry(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Skipping malformed risk history line %s:%s: %s",
            history_path, line_number, exc,
        )
        return None


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
                entry = _parse_history_line(stripped, history_path, line_number)
                if entry is not None:
                    entries.append(entry)
        return entries
    except OSError as exc:
        logger.warning("Malformed risk history at %s: %s", history_path, exc)
        Services.artifact_io().rename_malformed(history_path)
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
    return clamp_float(
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

    score = _OUTCOME_SCORE.get(outcome, 50)
    score += _VERIFICATION_ADJUSTMENT.get(verification, 0)

    score += min(len(entry.surfaced_surprises) * _SURPRISE_SCORE_WEIGHT, _SURPRISE_SCORE_CAP)
    return clamp_int(score, 0, 100)



