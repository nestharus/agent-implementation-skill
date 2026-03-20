from __future__ import annotations

from pathlib import Path

from scan.scan_dispatcher import _infer_planspace


def test_infer_planspace_from_artifacts_prompt(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    prompt = planspace / "artifacts" / "scan-logs" / "codemap-prompt.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("prompt", encoding="utf-8")

    assert _infer_planspace(prompt) == planspace.resolve()


def test_infer_planspace_from_root_scan_logs_prompt(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    prompt = planspace / "scan-logs" / "section-01" / "related-files-updater-prompt.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("prompt", encoding="utf-8")

    assert _infer_planspace(prompt) == planspace.resolve()
