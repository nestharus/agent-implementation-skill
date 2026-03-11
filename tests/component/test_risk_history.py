"""Component tests for ROAL risk history services."""

from __future__ import annotations

import json
from pathlib import Path

from lib.risk.history import (
    append_history_entry,
    compute_history_adjustment,
    pattern_signature,
    read_history,
)
from lib.risk.types import PostureProfile, RiskHistoryEntry, RiskType, StepClass


def _entry(
    *,
    predicted_risk: int = 40,
    actual_outcome: str = "success",
    dominant_risks: list[RiskType] | None = None,
    blast_radius_band: int = 2,
) -> RiskHistoryEntry:
    return RiskHistoryEntry(
        package_id="pkg-1",
        step_id="step-1",
        layer="section",
        assessment_class=StepClass.EDIT,
        posture=PostureProfile.P2_STANDARD,
        predicted_risk=predicted_risk,
        actual_outcome=actual_outcome,
        dominant_risks=dominant_risks or [RiskType.BRUTE_FORCE_REGRESSION],
        blast_radius_band=blast_radius_band,
    )


def test_append_and_read_round_trip(tmp_path: Path) -> None:
    history_path = tmp_path / "risk-history.jsonl"
    first = _entry()
    second = _entry(
        predicted_risk=65,
        actual_outcome="failure",
        dominant_risks=[RiskType.CROSS_SECTION_INCOHERENCE],
        blast_radius_band=3,
    )

    append_history_entry(history_path, first)
    append_history_entry(history_path, second)

    assert read_history(history_path) == [first, second]


def test_compute_history_adjustment_with_no_history_returns_zero(tmp_path: Path) -> None:
    adjustment = compute_history_adjustment(
        tmp_path / "missing.jsonl",
        StepClass.EDIT,
        [RiskType.BRUTE_FORCE_REGRESSION],
        2,
    )

    assert adjustment == 0.0


def test_read_history_empty_file_returns_empty_list(tmp_path: Path) -> None:
    history_path = tmp_path / "risk-history.jsonl"
    history_path.write_text("", encoding="utf-8")

    assert read_history(history_path) == []


def test_read_history_skips_corrupted_jsonl_lines(tmp_path: Path) -> None:
    history_path = tmp_path / "risk-history.jsonl"
    first = _entry()
    second = _entry(
        predicted_risk=52,
        actual_outcome="warning",
        dominant_risks=[RiskType.SILENT_DRIFT],
    )

    with history_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "package_id": first.package_id,
            "step_id": first.step_id,
            "layer": first.layer,
            "assessment_class": first.assessment_class.value,
            "posture": first.posture.value,
            "predicted_risk": first.predicted_risk,
            "actual_outcome": first.actual_outcome,
            "surfaced_surprises": first.surfaced_surprises,
            "verification_outcome": first.verification_outcome,
            "dominant_risks": [risk.value for risk in first.dominant_risks],
            "blast_radius_band": first.blast_radius_band,
        }))
        handle.write("\n")
        handle.write("{not valid json}\n")
        handle.write(json.dumps({
            "package_id": second.package_id,
            "step_id": second.step_id,
            "layer": second.layer,
            "assessment_class": second.assessment_class.value,
            "posture": second.posture.value,
            "predicted_risk": second.predicted_risk,
            "actual_outcome": second.actual_outcome,
            "surfaced_surprises": second.surfaced_surprises,
            "verification_outcome": second.verification_outcome,
            "dominant_risks": [risk.value for risk in second.dominant_risks],
            "blast_radius_band": second.blast_radius_band,
        }))
        handle.write("\n")

    assert read_history(history_path) == [first, second]


def test_compute_history_adjustment_positive_for_underestimated_history(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "risk-history.jsonl"
    append_history_entry(
        history_path,
        _entry(
            predicted_risk=20,
            actual_outcome="failure",
            dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
            blast_radius_band=2,
        ),
    )

    adjustment = compute_history_adjustment(
        history_path,
        StepClass.EDIT,
        [RiskType.BRUTE_FORCE_REGRESSION],
        2,
    )

    assert adjustment > 0


def test_compute_history_adjustment_negative_for_overestimated_history(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "risk-history.jsonl"
    append_history_entry(
        history_path,
        _entry(
            predicted_risk=90,
            actual_outcome="success",
            dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
            blast_radius_band=2,
        ),
    )

    adjustment = compute_history_adjustment(
        history_path,
        StepClass.EDIT,
        [RiskType.BRUTE_FORCE_REGRESSION],
        2,
    )

    assert adjustment < 0


def test_pattern_signature_is_stable_and_deterministic() -> None:
    first = pattern_signature(
        StepClass.COORDINATE,
        [
            RiskType.STALE_ARTIFACT_CONTAMINATION,
            RiskType.CROSS_SECTION_INCOHERENCE,
        ],
        3,
    )
    second = pattern_signature(
        StepClass.COORDINATE,
        [
            RiskType.CROSS_SECTION_INCOHERENCE,
            RiskType.STALE_ARTIFACT_CONTAMINATION,
        ],
        3,
    )

    assert first == second
    assert first == (
        "coordinate|3|cross_section_incoherence,stale_artifact_contamination"
    )


def test_history_adjustment_is_bounded(tmp_path: Path) -> None:
    history_path = tmp_path / "risk-history.jsonl"
    for index in range(4):
        append_history_entry(
            history_path,
            RiskHistoryEntry(
                package_id=f"pkg-{index}",
                step_id=f"step-{index}",
                layer="section",
                assessment_class=StepClass.EDIT,
                posture=PostureProfile.P1_LIGHT,
                predicted_risk=0,
                actual_outcome="failure",
                dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
                blast_radius_band=1,
                surfaced_surprises=["surprise-a", "surprise-b"],
            ),
        )

    adjustment = compute_history_adjustment(
        history_path,
        StepClass.EDIT,
        [RiskType.BRUTE_FORCE_REGRESSION],
        1,
    )

    assert adjustment == 10.0


def test_read_history_handles_large_files(tmp_path: Path) -> None:
    history_path = tmp_path / "risk-history.jsonl"
    for index in range(250):
        append_history_entry(
            history_path,
            RiskHistoryEntry(
                package_id=f"pkg-{index}",
                step_id=f"step-{index}",
                layer="section",
                assessment_class=StepClass.EDIT,
                posture=PostureProfile.P2_STANDARD,
                predicted_risk=40 + (index % 5),
                actual_outcome="success" if index % 2 == 0 else "warning",
                dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
                blast_radius_band=index % 4,
            ),
        )

    history = read_history(history_path)

    assert len(history) == 250
    assert history[0].package_id == "pkg-0"
    assert history[-1].package_id == "pkg-249"
