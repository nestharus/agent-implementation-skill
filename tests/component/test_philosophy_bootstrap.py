"""Component tests for philosophy bootstrap helpers."""

from __future__ import annotations

import json
from pathlib import Path

from containers import ArtifactIOService, HasherService, LogService
from src.intent.service.philosophy_grounding import PhilosophyGrounding
from src.intent.service.philosophy_bootstrap_state import PhilosophyBootstrapState
from src.intent.service.philosophy_catalog import (
    build_philosophy_catalog,
    walk_md_bounded,
)
from src.orchestrator.path_registry import PathRegistry


def _make_grounding() -> PhilosophyGrounding:
    artifact_io = ArtifactIOService()
    return PhilosophyGrounding(
        artifact_io=artifact_io,
        bootstrap_state=PhilosophyBootstrapState(artifact_io=artifact_io),
        hasher=HasherService(),
        logger=LogService(),
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
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
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

    result = _make_grounding().validate_philosophy_grounding(
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
    assert signal["state"] == "NEED_DECISION"


def test_validate_philosophy_grounding_rejects_stale_source_files(
    tmp_path: Path,
) -> None:
    """Source map with nonexistent source_file paths blocks bootstrap."""
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
        json.dumps({
            "P1": {
                "source_type": "repo_source",
                "source_file": "/nonexistent/path/philosophy.md",
                "source_section": "## Values",
            },
        }),
        encoding="utf-8",
    )

    result = _make_grounding().validate_philosophy_grounding(
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
    assert signal["state"] == "NEED_DECISION"
    assert "no longer exist" in signal["detail"]


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

    result = _make_grounding().validate_philosophy_grounding(
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
    assert signal["state"] == "NEED_DECISION"
    assert "invalid entries" in signal["detail"]
