from __future__ import annotations

import json
from pathlib import Path

from src.scan.related.section_iterator import scan_sections
from src.scan.codemap.cache import FileCardCache


def _write_tier_file(path: Path, files: list[str]) -> None:
    path.write_text(
        json.dumps({"tiers": {"critical": files}, "scan_now": ["critical"]}),
        encoding="utf-8",
    )


def test_scan_sections_returns_failure_when_tier_ranking_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text("# Section\n", encoding="utf-8")
    scan_log_dir = tmp_path / "scan-logs"

    monkeypatch.setattr(
        "src.scan.related.section_iterator.deep_scan_related_files",
        lambda _section_file: ["src/main.py"],
    )
    monkeypatch.setattr(
        "src.scan.related.section_iterator.run_tier_ranking",
        lambda *_args, **_kwargs: None,
    )

    failed = scan_sections(
        [section_file],
        tmp_path / "codemap.md",
        tmp_path / "codespace",
        tmp_path / "artifacts",
        scan_log_dir,
        FileCardCache(tmp_path / "file-cards"),
        tmp_path / "corrections.json",
        {"deep_analysis": "glm"},
        {},
    )

    assert failed is True
    assert "tier ranking unavailable" in (
        scan_log_dir / "failures.log"
    ).read_text(encoding="utf-8")


def test_scan_sections_skips_already_scanned_files(
    tmp_path,
    monkeypatch,
) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text("# Section\n", encoding="utf-8")
    tier_file = tmp_path / "section-01-file-tiers.json"
    _write_tier_file(tier_file, ["src/main.py"])

    monkeypatch.setattr(
        "src.scan.related.section_iterator.deep_scan_related_files",
        lambda _section_file: ["src/main.py"],
    )
    monkeypatch.setattr(
        "src.scan.related.section_iterator.run_tier_ranking",
        lambda *_args, **_kwargs: tier_file,
    )
    monkeypatch.setattr(
        "src.scan.related.section_iterator.analyze_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("analyze_file should not run for already-scanned files"),
        ),
    )

    failed = scan_sections(
        [section_file],
        tmp_path / "codemap.md",
        tmp_path / "codespace",
        tmp_path / "artifacts",
        tmp_path / "scan-logs",
        FileCardCache(tmp_path / "file-cards"),
        tmp_path / "corrections.json",
        {"deep_analysis": "glm"},
        {"section-01": {"src/main.py"}},
    )

    assert failed is False


def test_scan_sections_analyzes_new_files_and_updates_state(
    tmp_path,
    monkeypatch,
) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text("# Section\n", encoding="utf-8")
    tier_file = tmp_path / "section-01-file-tiers.json"
    _write_tier_file(tier_file, ["src/main.py"])
    already_scanned: dict[str, set[str]] = {}
    calls: list[str] = []

    monkeypatch.setattr(
        "src.scan.related.section_iterator.deep_scan_related_files",
        lambda _section_file: ["src/main.py"],
    )
    monkeypatch.setattr(
        "src.scan.related.section_iterator.run_tier_ranking",
        lambda *_args, **_kwargs: tier_file,
    )
    monkeypatch.setattr(
        "src.scan.related.section_iterator.analyze_file",
        lambda _section_file, _section_name, source_file, *_args, **_kwargs: calls.append(source_file) or True,
    )

    failed = scan_sections(
        [section_file],
        tmp_path / "codemap.md",
        tmp_path / "codespace",
        tmp_path / "artifacts",
        tmp_path / "scan-logs",
        FileCardCache(tmp_path / "file-cards"),
        tmp_path / "corrections.json",
        {"deep_analysis": "glm"},
        already_scanned,
    )

    assert failed is False
    assert calls == ["src/main.py"]
    assert already_scanned == {"section-01": {"src/main.py"}}
