"""Component tests for shared scan feedback routing helpers."""

from __future__ import annotations

import json

from src.scripts.lib.scan_feedback_router import (
    _append_to_log,
    _extract_section_number,
    _is_valid_updater_signal,
    _route_scope_deltas,
    _validate_feedback_schema,
)


def test_is_valid_updater_signal_accepts_status_string(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(json.dumps({"status": "stale"}), encoding="utf-8")

    assert _is_valid_updater_signal(signal_path) is True


def test_is_valid_updater_signal_renames_malformed_json(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text("{bad json", encoding="utf-8")

    assert _is_valid_updater_signal(signal_path) is False
    assert not signal_path.exists()
    assert signal_path.with_suffix(".malformed.json").exists()


def test_validate_feedback_schema_logs_missing_required_fields(tmp_path) -> None:
    scan_log_dir = tmp_path / "scan-logs"
    scan_log_dir.mkdir()
    data = {"relevant": "yes"}
    fb_file = tmp_path / "feedback.json"

    valid = _validate_feedback_schema(data, fb_file, "section-02", scan_log_dir)
    failure_log = (scan_log_dir / "failures.log").read_text(encoding="utf-8")

    assert valid is False
    assert "source_file (must be str)" in failure_log


def test_validate_feedback_schema_coerces_optional_non_lists(tmp_path) -> None:
    data = {
        "relevant": True,
        "source_file": "src/main.py",
        "missing_files": "src/extra.py",
        "out_of_scope": "cross-cutting issue",
    }

    valid = _validate_feedback_schema(
        data,
        tmp_path / "feedback.json",
        "section-03",
        tmp_path,
    )

    assert valid is True
    assert data["missing_files"] == []
    assert data["out_of_scope"] == []


def test_extract_section_number_and_append_to_log(tmp_path) -> None:
    log_path = tmp_path / "failures.log"

    _append_to_log(log_path, "line one")
    _append_to_log(log_path, "line two")

    assert _extract_section_number("section-17") == "17"
    assert log_path.read_text(encoding="utf-8") == "line one\nline two\n"


def test_route_scope_deltas_writes_pending_items(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    scan_log_dir = tmp_path / "scan-logs"
    section_file = artifacts_dir / "sections" / "section-04.md"
    section_file.parent.mkdir(parents=True, exist_ok=True)
    section_file.write_text("# Section 04\n", encoding="utf-8")
    sec_log_dir = scan_log_dir / "section-04"
    sec_log_dir.mkdir(parents=True, exist_ok=True)
    (sec_log_dir / "deep-a-feedback.json").write_text(
        json.dumps({"out_of_scope": ["needs parent", "  cross-section  "]}),
        encoding="utf-8",
    )

    _route_scope_deltas(
        section_files=[section_file],
        artifacts_dir=artifacts_dir,
        scan_log_dir=scan_log_dir,
    )

    delta_path = artifacts_dir / "scope-deltas" / "section-04-scope-delta.json"
    data = json.loads(delta_path.read_text(encoding="utf-8"))

    assert data["delta_id"] == "delta-04-scan-deep"
    assert data["items"] == ["needs parent", "cross-section"]
    assert data["adjudicated"] is False


def test_route_scope_deltas_skips_adjudicated_and_preserves_malformed(
    tmp_path,
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    scan_log_dir = tmp_path / "scan-logs"

    adjudicated_section = artifacts_dir / "sections" / "section-05.md"
    malformed_section = artifacts_dir / "sections" / "section-06.md"
    adjudicated_section.parent.mkdir(parents=True, exist_ok=True)
    adjudicated_section.write_text("# Section 05\n", encoding="utf-8")
    malformed_section.write_text("# Section 06\n", encoding="utf-8")

    sec5_log = scan_log_dir / "section-05"
    sec6_log = scan_log_dir / "section-06"
    sec5_log.mkdir(parents=True, exist_ok=True)
    sec6_log.mkdir(parents=True, exist_ok=True)
    (sec5_log / "deep-a-feedback.json").write_text(
        json.dumps({"out_of_scope": ["already handled"]}),
        encoding="utf-8",
    )
    (sec6_log / "deep-a-feedback.json").write_text(
        json.dumps({"out_of_scope": ["new item"]}),
        encoding="utf-8",
    )

    scope_dir = artifacts_dir / "scope-deltas"
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / "section-05-scope-delta.json").write_text(
        json.dumps({"adjudicated": True}),
        encoding="utf-8",
    )
    malformed_delta = scope_dir / "section-06-scope-delta.json"
    malformed_delta.write_text("{bad json", encoding="utf-8")

    _route_scope_deltas(
        section_files=[adjudicated_section, malformed_section],
        artifacts_dir=artifacts_dir,
        scan_log_dir=scan_log_dir,
    )

    rewritten = json.loads(malformed_delta.read_text(encoding="utf-8"))

    assert json.loads(
        (scope_dir / "section-05-scope-delta.json").read_text(encoding="utf-8"),
    ) == {"adjudicated": True}
    assert (scope_dir / "section-06-scope-delta.malformed.json").exists()
    assert rewritten["items"] == ["new item"]
