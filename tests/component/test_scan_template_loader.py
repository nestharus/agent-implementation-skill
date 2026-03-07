"""Component tests for shared scan template loading."""

from __future__ import annotations

from pathlib import Path

from src.scripts.lib import scan_template_loader
from src.scripts.lib.scan_template_loader import load_scan_template


def test_load_scan_template_reads_from_scan_templates_directory() -> None:
    expected_path = (
        Path(__file__).resolve().parents[2]
        / "src" / "scripts" / "scan" / "templates" / "codemap_build.md"
    )

    assert load_scan_template("codemap_build.md") == expected_path.read_text()


def test_load_scan_template_uses_shared_template_root(tmp_path, monkeypatch) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "custom.md").write_text("Hello from scan template.\n")
    monkeypatch.setattr(scan_template_loader, "_SCAN_TEMPLATES", template_dir)

    assert load_scan_template("custom.md") == "Hello from scan template.\n"
