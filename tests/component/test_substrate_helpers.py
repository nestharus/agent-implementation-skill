"""Component tests for substrate runner helpers."""

from __future__ import annotations

import json
from pathlib import Path

from scan.related.related_file_resolver import list_section_files
from src.orchestrator.path_registry import PathRegistry
from src.scan.substrate.substrate_state_reader import (
    count_existing_related,
    read_project_mode,
    section_number,
    write_status,
)


def test_read_project_mode_prefers_json_signal(tmp_path: Path) -> None:
    PathRegistry(tmp_path).ensure_artifacts_tree()
    artifacts_dir = tmp_path / "artifacts"
    signals_dir = artifacts_dir / "signals"
    (signals_dir / "project-mode.json").write_text(
        json.dumps({"mode": "Brownfield"}),
        encoding="utf-8",
    )
    (artifacts_dir / "project-mode.txt").write_text("greenfield", encoding="utf-8")

    assert read_project_mode(artifacts_dir) == "brownfield"


def test_read_project_mode_falls_back_to_text_after_malformed_json(tmp_path: Path) -> None:
    PathRegistry(tmp_path).ensure_artifacts_tree()
    artifacts_dir = tmp_path / "artifacts"
    signals_dir = artifacts_dir / "signals"
    json_path = signals_dir / "project-mode.json"
    json_path.write_text("{bad json", encoding="utf-8")
    (artifacts_dir / "project-mode.txt").write_text("hybrid", encoding="utf-8")

    assert read_project_mode(artifacts_dir) == "hybrid"
    assert not json_path.exists()
    assert json_path.with_suffix(".malformed.json").exists()


def test_list_section_files_filters_and_sorts(tmp_path: Path) -> None:
    (tmp_path / "section-10.md").write_text("", encoding="utf-8")
    (tmp_path / "section-02.md").write_text("", encoding="utf-8")
    (tmp_path / "notes.md").write_text("", encoding="utf-8")
    (tmp_path / "section-a.md").write_text("", encoding="utf-8")

    assert list_section_files(tmp_path) == [
        tmp_path / "section-02.md",
        tmp_path / "section-10.md",
    ]


def test_section_number_extracts_digits_and_fallbacks() -> None:
    assert section_number(Path("section-03.md")) == "03"
    assert section_number(Path("section-custom.md")) == "custom"


def test_count_existing_related_counts_only_existing_files(tmp_path: Path) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text(
        "# Section\n\n## Related Files\n\n### src/exists.py\n\n### src/missing.py\n",
        encoding="utf-8",
    )
    codespace = tmp_path / "codespace"
    (codespace / "src").mkdir(parents=True)
    (codespace / "src" / "exists.py").write_text("pass\n", encoding="utf-8")

    assert count_existing_related(section_file, codespace) == 1


def test_write_status_writes_expected_payload(tmp_path: Path) -> None:
    PathRegistry(tmp_path).ensure_artifacts_tree()
    artifacts_dir = tmp_path / "artifacts"

    write_status(
        artifacts_dir,
        state="SKIPPED",
        project_mode="greenfield",
        total_sections=4,
        vacuum_sections=["01", "03"],
        notes="below threshold",
        threshold=5,
    )

    status = json.loads(
        (artifacts_dir / "substrate" / "status.json").read_text(encoding="utf-8"),
    )
    assert status == {
        "state": "SKIPPED",
        "project_mode": "greenfield",
        "total_sections": 4,
        "vacuum_sections": [1, 3],
        "threshold": 5,
        "notes": "below threshold",
    }
