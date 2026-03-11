"""Component tests for philosophy bootstrap helpers."""

from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.intent.philosophy_bootstrap import (
    build_philosophy_catalog,
    validate_philosophy_grounding,
    walk_md_bounded,
)


def test_walk_md_bounded_respects_depth_and_excluded_top_dirs(tmp_path: Path) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "one.md").write_text("# one\n", encoding="utf-8")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "two.md").write_text("# two\n", encoding="utf-8")
    deep = tmp_path / "keep" / "nested" / "deeper"
    deep.mkdir(parents=True)
    (deep / "three.md").write_text("# three\n", encoding="utf-8")

    results = list(
        walk_md_bounded(
            tmp_path,
            max_depth=2,
            exclude_top_dirs=frozenset({"skip"}),
        ),
    )

    assert results == [tmp_path / "keep" / "one.md"]


def test_build_philosophy_catalog_prefers_codespace_and_excludes_artifacts(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    (planspace / "artifacts").mkdir(parents=True)
    codespace.mkdir()

    (planspace / "notes.md").write_text("# Plan notes\n", encoding="utf-8")
    (planspace / "artifacts" / "ignored.md").write_text("# ignored\n", encoding="utf-8")
    (codespace / "philosophy.md").write_text("# Philosophy\nP1\n", encoding="utf-8")

    catalog = build_philosophy_catalog(planspace, codespace, max_files=4)
    paths = {entry["path"] for entry in catalog}
    philosophy_entry = next(
        entry for entry in catalog
        if entry["path"] == str(codespace / "philosophy.md")
    )

    assert str(codespace / "philosophy.md") in paths
    assert str(planspace / "notes.md") in paths
    assert str(planspace / "artifacts" / "ignored.md") not in paths
    assert philosophy_entry["preview_start"] == "# Philosophy\nP1"
    assert philosophy_entry["preview_middle"] == "# Philosophy\nP1"
    assert philosophy_entry["headings"] == ["Philosophy"]


def test_validate_philosophy_grounding_renames_malformed_source_map(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    intent_global = artifacts / "intent" / "global"
    intent_global.mkdir(parents=True)
    philosophy_path = intent_global / "philosophy.md"
    source_map_path = intent_global / "philosophy-source-map.json"
    philosophy_path.write_text(
        "# Philosophy\n\n## Principles\n\n### P1: Test\n",
        encoding="utf-8",
    )
    source_map_path.write_text("{not json", encoding="utf-8")

    result = validate_philosophy_grounding(
        philosophy_path,
        source_map_path,
        artifacts,
    )

    assert result is False
    assert not source_map_path.exists()
    assert source_map_path.with_suffix(".malformed.json").exists()
    signal = json.loads(
        (artifacts / "signals" / "philosophy-bootstrap-signal.json").read_text(
            encoding="utf-8",
        ),
    )
    assert signal["state"] == "NEEDS_PARENT"


def test_validate_philosophy_grounding_rejects_legacy_source_map_shape(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    intent_global = artifacts / "intent" / "global"
    intent_global.mkdir(parents=True)
    philosophy_path = intent_global / "philosophy.md"
    source_map_path = intent_global / "philosophy-source-map.json"
    philosophy_path.write_text(
        "# Philosophy\n\n## Principles\n\n### P1: Test\n",
        encoding="utf-8",
    )
    source_map_path.write_text(
        json.dumps({"P1": "legacy string"}),
        encoding="utf-8",
    )

    result = validate_philosophy_grounding(
        philosophy_path,
        source_map_path,
        artifacts,
    )

    assert result is False
    signal = json.loads(
        (artifacts / "signals" / "philosophy-bootstrap-signal.json").read_text(
            encoding="utf-8",
        ),
    )
    assert signal["state"] == "NEEDS_PARENT"
    assert "invalid entries" in signal["detail"]
