from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from containers import Services
from src.scan.related.section_iterator import SectionIterator
from src.scan.codemap.cache import FileCardCache
from src.scan.scan_context import ScanContext


def _write_tier_file(path: Path, files: list[str]) -> None:
    path.write_text(
        json.dumps({"tiers": {"critical": files}, "scan_now": ["critical"]}),
        encoding="utf-8",
    )


def _ctx(tmp_path: Path, **overrides) -> ScanContext:
    return ScanContext(
        codespace=overrides.get("codespace", tmp_path / "codespace"),
        codemap_path=overrides.get("codemap_path", tmp_path / "codemap.md"),
        corrections_path=overrides.get("corrections_path", tmp_path / "corrections.json"),
        scan_log_dir=overrides.get("scan_log_dir", tmp_path / "scan-logs"),
        model_policy=overrides.get("model_policy", {"deep_analysis": "glm"}),
    )


def test_scan_sections_returns_failure_when_tier_ranking_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text("# Section\n", encoding="utf-8")

    monkeypatch.setattr(
        "src.scan.related.section_iterator.deep_scan_related_files",
        lambda _section_file: ["src/main.py"],
    )

    # Tier ranker returns None (failure)
    mock_tier_ranker = MagicMock()
    mock_tier_ranker.run_tier_ranking = MagicMock(return_value=None)

    iterator = SectionIterator(
        artifact_io=Services.artifact_io(),
        tier_ranker=mock_tier_ranker,
    )

    failed = iterator.scan_sections(
        [section_file],
        _ctx(tmp_path),
        tmp_path / "artifacts",
        FileCardCache(tmp_path / "file-cards", hasher=Services.hasher(), artifact_io=Services.artifact_io()),
        {},
    )

    assert failed is True
    assert "tier ranking unavailable" in (
        tmp_path / "scan-logs" / "failures.log"
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

    mock_tier_ranker = MagicMock()
    mock_tier_ranker.run_tier_ranking = MagicMock(return_value=tier_file)

    mock_analyzer = MagicMock()
    mock_analyzer.analyze_file = MagicMock(
        side_effect=AssertionError("analyze_file should not run for already-scanned files"),
    )

    iterator = SectionIterator(
        artifact_io=Services.artifact_io(),
        analyzer=mock_analyzer,
        tier_ranker=mock_tier_ranker,
    )

    failed = iterator.scan_sections(
        [section_file],
        _ctx(tmp_path),
        tmp_path / "artifacts",
        FileCardCache(tmp_path / "file-cards", hasher=Services.hasher(), artifact_io=Services.artifact_io()),
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

    mock_tier_ranker = MagicMock()
    mock_tier_ranker.run_tier_ranking = MagicMock(return_value=tier_file)

    mock_analyzer = MagicMock()
    mock_analyzer.analyze_file = MagicMock(
        side_effect=lambda _sf, _sn, source_file, *_a, **_kw: calls.append(source_file) or True,
    )

    iterator = SectionIterator(
        artifact_io=Services.artifact_io(),
        analyzer=mock_analyzer,
        tier_ranker=mock_tier_ranker,
    )

    failed = iterator.scan_sections(
        [section_file],
        _ctx(tmp_path),
        tmp_path / "artifacts",
        FileCardCache(tmp_path / "file-cards", hasher=Services.hasher(), artifact_io=Services.artifact_io()),
        already_scanned,
    )

    assert failed is False
    assert calls == ["src/main.py"]
    assert already_scanned == {"section-01": {"src/main.py"}}
