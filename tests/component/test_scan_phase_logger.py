"""Component tests for shared scan failure logging."""

from __future__ import annotations

from src.scan.service.phase_failure_logger import log_phase_failure


def test_log_phase_failure_appends_failure_line_and_stderr(capsys, tmp_path) -> None:
    log_phase_failure("quick-deep", "section-03", "feedback schema invalid", tmp_path)

    failure_log = tmp_path / "failures.log"
    line = failure_log.read_text(encoding="utf-8")
    captured = capsys.readouterr()

    assert "phase=quick-deep" in line
    assert "context=section-03" in line
    assert "message=feedback schema invalid" in line
    assert (
        captured.err.strip()
        == "[FAIL] phase=quick-deep context=section-03 message=feedback schema invalid"
    )
