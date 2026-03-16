"""Tests for BootstrapAssessor."""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.path_registry import PathRegistry
from orchestrator.service.bootstrap_assessor import (
    STAGE_CODEMAP,
    STAGE_DECOMPOSE,
    STAGE_EXPLORE,
    STAGE_SUBSTRATE,
    BootstrapAssessor,
    BootstrapStatus,
)


def _make_planspace(tmp_path: Path) -> Path:
    """Create a minimal planspace structure."""
    planspace = tmp_path / "planspace"
    registry = PathRegistry(planspace)
    registry.ensure_artifacts_tree()
    return planspace


def _write_sections(planspace: Path, count: int = 3, with_related: bool = False) -> None:
    """Write section files to the planspace."""
    sections_dir = PathRegistry(planspace).sections_dir()
    sections_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        content = f"---\nsummary: Section {i:02d}\nkeywords: test\n---\n\n# Section {i:02d}\n\nContent here.\n"
        if with_related:
            content += "\n## Related Files\n\n- src/foo.py\n"
        (sections_dir / f"section-{i:02d}.md").write_text(content, encoding="utf-8")


def _write_proposal(planspace: Path) -> None:
    PathRegistry(planspace).global_proposal().write_text("# Proposal\n\nStrategy here.\n", encoding="utf-8")


def _write_alignment(planspace: Path) -> None:
    PathRegistry(planspace).global_alignment().write_text("# Alignment\n\nConstraints here.\n", encoding="utf-8")


def _write_codemap(planspace: Path) -> None:
    codemap = PathRegistry(planspace).codemap()
    codemap.parent.mkdir(parents=True, exist_ok=True)
    codemap.write_text("# Codemap\n\n## src/\n- main.py\n", encoding="utf-8")


def _write_substrate_md(planspace: Path) -> None:
    substrate_dir = PathRegistry(planspace).substrate_dir()
    substrate_dir.mkdir(parents=True, exist_ok=True)
    (substrate_dir / "substrate.md").write_text("# Substrate\n\nShared seams.\n", encoding="utf-8")


def _write_substrate_status(planspace: Path, state: str = "skipped") -> None:
    import json
    substrate_dir = PathRegistry(planspace).substrate_dir()
    substrate_dir.mkdir(parents=True, exist_ok=True)
    (substrate_dir / "status.json").write_text(
        json.dumps({"state": state}), encoding="utf-8",
    )


class TestBootstrapAssessor:
    def test_empty_planspace_needs_decompose(self, tmp_path: Path) -> None:
        planspace = _make_planspace(tmp_path)
        status = BootstrapAssessor().assess(planspace)
        assert not status.ready
        assert status.next_stage == STAGE_DECOMPOSE
        assert "sections" in status.missing

    def test_sections_only_needs_decompose(self, tmp_path: Path) -> None:
        """Sections exist but proposal/alignment missing -> still decompose."""
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace)
        status = BootstrapAssessor().assess(planspace)
        assert not status.ready
        assert status.next_stage == STAGE_DECOMPOSE
        assert "proposal.md" in status.missing

    def test_decompose_complete_needs_codemap(self, tmp_path: Path) -> None:
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace)
        _write_proposal(planspace)
        _write_alignment(planspace)
        status = BootstrapAssessor().assess(planspace)
        assert not status.ready
        assert status.next_stage == STAGE_CODEMAP
        assert STAGE_DECOMPOSE in status.completed

    def test_codemap_complete_needs_explore(self, tmp_path: Path) -> None:
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace)
        _write_proposal(planspace)
        _write_alignment(planspace)
        _write_codemap(planspace)
        status = BootstrapAssessor().assess(planspace)
        assert not status.ready
        assert status.next_stage == STAGE_EXPLORE
        assert STAGE_CODEMAP in status.completed

    def test_explore_complete_needs_substrate(self, tmp_path: Path) -> None:
        """All explore artifacts present but no substrate -> needs substrate."""
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace, with_related=True)
        _write_proposal(planspace)
        _write_alignment(planspace)
        _write_codemap(planspace)
        status = BootstrapAssessor().assess(planspace)
        assert not status.ready
        assert status.next_stage == STAGE_SUBSTRATE
        assert STAGE_EXPLORE in status.completed

    def test_substrate_complete_via_substrate_md(self, tmp_path: Path) -> None:
        """substrate.md present -> ready=True."""
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace, with_related=True)
        _write_proposal(planspace)
        _write_alignment(planspace)
        _write_codemap(planspace)
        _write_substrate_md(planspace)
        status = BootstrapAssessor().assess(planspace)
        assert status.ready
        assert STAGE_SUBSTRATE in status.completed

    def test_substrate_complete_via_status_json(self, tmp_path: Path) -> None:
        """status.json with terminal state -> ready=True."""
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace, with_related=True)
        _write_proposal(planspace)
        _write_alignment(planspace)
        _write_codemap(planspace)
        _write_substrate_status(planspace, state="skipped")
        status = BootstrapAssessor().assess(planspace)
        assert status.ready
        assert STAGE_SUBSTRATE in status.completed

    def test_all_present_ready(self, tmp_path: Path) -> None:
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace, with_related=True)
        _write_proposal(planspace)
        _write_alignment(planspace)
        _write_codemap(planspace)
        _write_substrate_md(planspace)
        status = BootstrapAssessor().assess(planspace)
        assert status.ready
        assert status.next_stage is None
        assert len(status.missing) == 0
        assert len(status.completed) == 4

    def test_partial_explore_needs_explore(self, tmp_path: Path) -> None:
        """Some sections explored, some not -> still needs explore."""
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace, count=2, with_related=True)
        _write_proposal(planspace)
        _write_alignment(planspace)
        _write_codemap(planspace)
        # Add one more section without related files
        sections_dir = PathRegistry(planspace).sections_dir()
        (sections_dir / "section-03.md").write_text("# Section 03\n\nNo related files.\n", encoding="utf-8")
        status = BootstrapAssessor().assess(planspace)
        assert not status.ready
        assert status.next_stage == STAGE_EXPLORE

    def test_empty_proposal_needs_decompose(self, tmp_path: Path) -> None:
        """Empty proposal file treated as missing."""
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace)
        PathRegistry(planspace).global_proposal().write_text("", encoding="utf-8")
        _write_alignment(planspace)
        status = BootstrapAssessor().assess(planspace)
        assert not status.ready
        assert status.next_stage == STAGE_DECOMPOSE

    def test_empty_codemap_needs_codemap(self, tmp_path: Path) -> None:
        """Empty codemap file treated as missing."""
        planspace = _make_planspace(tmp_path)
        _write_sections(planspace)
        _write_proposal(planspace)
        _write_alignment(planspace)
        codemap = PathRegistry(planspace).codemap()
        codemap.parent.mkdir(parents=True, exist_ok=True)
        codemap.write_text("", encoding="utf-8")
        status = BootstrapAssessor().assess(planspace)
        assert not status.ready
        assert status.next_stage == STAGE_CODEMAP
