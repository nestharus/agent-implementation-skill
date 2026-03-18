"""Tests for BootstrapAssessor."""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.path_registry import PathRegistry
from orchestrator.service.bootstrap_assessor import (
    ENTRY_BROWNFIELD,
    ENTRY_GREENFIELD,
    ENTRY_PARTIAL_GOVERNANCE,
    ENTRY_PRD,
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


class TestEntryClassification:
    """Tests for classify_entry — mechanical observation of codespace state."""

    def test_greenfield_empty_codespace(self, tmp_path: Path) -> None:
        """Empty codespace with no spec -> greenfield."""
        codespace = tmp_path / "code"
        codespace.mkdir()
        result = BootstrapAssessor().classify_entry(codespace, spec_path=None)
        assert result.path == ENTRY_GREENFIELD
        assert not result.has_code
        assert not result.has_spec
        assert not result.has_governance
        assert not result.has_philosophy

    def test_greenfield_nonexistent_codespace(self, tmp_path: Path) -> None:
        """Nonexistent codespace -> greenfield."""
        codespace = tmp_path / "does-not-exist"
        result = BootstrapAssessor().classify_entry(codespace, spec_path=None)
        assert result.path == ENTRY_GREENFIELD

    def test_prd_with_spec_file(self, tmp_path: Path) -> None:
        """Spec file present, no code -> prd."""
        codespace = tmp_path / "code"
        codespace.mkdir()
        spec = tmp_path / "spec.md"
        spec.write_text("# Requirements\n\nBuild a thing.\n", encoding="utf-8")
        result = BootstrapAssessor().classify_entry(codespace, spec_path=spec)
        assert result.path == ENTRY_PRD
        assert result.has_spec
        assert not result.has_code

    def test_brownfield_with_code_files(self, tmp_path: Path) -> None:
        """Code files present, no governance -> brownfield."""
        codespace = tmp_path / "code"
        codespace.mkdir()
        (codespace / "main.py").write_text("print('hello')\n", encoding="utf-8")
        result = BootstrapAssessor().classify_entry(codespace, spec_path=None)
        assert result.path == ENTRY_BROWNFIELD
        assert result.has_code
        assert not result.has_governance

    def test_brownfield_code_in_subdir(self, tmp_path: Path) -> None:
        """Code files one level deep -> still detected as brownfield."""
        codespace = tmp_path / "code"
        src = codespace / "src"
        src.mkdir(parents=True)
        (src / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
        result = BootstrapAssessor().classify_entry(codespace, spec_path=None)
        assert result.path == ENTRY_BROWNFIELD
        assert result.has_code

    def test_brownfield_with_spec_still_brownfield(self, tmp_path: Path) -> None:
        """Code files + spec -> brownfield (code dominates)."""
        codespace = tmp_path / "code"
        codespace.mkdir()
        (codespace / "server.go").write_text("package main\n", encoding="utf-8")
        spec = tmp_path / "spec.md"
        spec.write_text("# Spec\n", encoding="utf-8")
        result = BootstrapAssessor().classify_entry(codespace, spec_path=spec)
        assert result.path == ENTRY_BROWNFIELD
        assert result.has_code
        assert result.has_spec
        assert "code_with_spec_treated_as_brownfield" in result.evidence

    def test_partial_governance_with_real_content(self, tmp_path: Path) -> None:
        """Governance docs with real records -> partial_governance."""
        codespace = tmp_path / "code"
        gov_dir = codespace / "governance" / "problems"
        gov_dir.mkdir(parents=True)
        (gov_dir / "index.md").write_text(
            "# Problem Archive\n\n"
            "## PRB-0001: Test Problem\n\n"
            "**Status**: active\n"
            "**Provenance**: user-authored\n",
            encoding="utf-8",
        )
        result = BootstrapAssessor().classify_entry(codespace, spec_path=None)
        assert result.path == ENTRY_PARTIAL_GOVERNANCE
        assert result.has_governance

    def test_scaffold_governance_not_counted(self, tmp_path: Path) -> None:
        """Scaffold-only governance docs -> not counted as real governance."""
        codespace = tmp_path / "code"
        gov_dir = codespace / "governance" / "problems"
        gov_dir.mkdir(parents=True)
        (gov_dir / "index.md").write_text(
            "# Problem Archive\n\n"
            "Problems discovered during development are documented here.\n",
            encoding="utf-8",
        )
        result = BootstrapAssessor().classify_entry(codespace, spec_path=None)
        assert not result.has_governance
        assert result.path == ENTRY_GREENFIELD

    def test_philosophy_profiles_detected(self, tmp_path: Path) -> None:
        """Philosophy profiles -> partial_governance."""
        codespace = tmp_path / "code"
        profiles_dir = codespace / "philosophy" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "PHI-global.md").write_text(
            "# Global Profile\n\n## Values\n- Correctness\n",
            encoding="utf-8",
        )
        result = BootstrapAssessor().classify_entry(codespace, spec_path=None)
        assert result.path == ENTRY_PARTIAL_GOVERNANCE
        assert result.has_philosophy

    def test_hidden_dirs_skipped(self, tmp_path: Path) -> None:
        """Code inside hidden dirs (.git) should not trigger brownfield."""
        codespace = tmp_path / "code"
        codespace.mkdir()
        git_dir = codespace / ".git" / "objects"
        git_dir.mkdir(parents=True)
        (git_dir / "pack.py").write_text("# git internal\n", encoding="utf-8")
        result = BootstrapAssessor().classify_entry(codespace, spec_path=None)
        assert not result.has_code
        assert result.path == ENTRY_GREENFIELD

    def test_evidence_list_populated(self, tmp_path: Path) -> None:
        """Evidence list contains relevant signals."""
        codespace = tmp_path / "code"
        codespace.mkdir()
        (codespace / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")
        spec = tmp_path / "spec.md"
        spec.write_text("# Spec\n", encoding="utf-8")
        result = BootstrapAssessor().classify_entry(codespace, spec_path=spec)
        assert "code_files_present" in result.evidence
        assert any("spec_file=" in e for e in result.evidence)
