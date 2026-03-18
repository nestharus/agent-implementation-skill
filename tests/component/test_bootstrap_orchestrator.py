"""Tests for BootstrapOrchestrator."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.path_registry import PathRegistry
from orchestrator.engine.bootstrap_orchestrator import (
    BootstrapOrchestrator,
    MAX_RETRIES,
)
from orchestrator.service.bootstrap_assessor import (
    ENTRY_PRD,
    STAGE_CODEMAP,
    STAGE_DECOMPOSE,
    STAGE_EXPLORE,
    STAGE_SUBSTRATE,
    BootstrapAssessor,
    BootstrapStatus,
    EntryClassification,
)

_DEFAULT_CLASSIFICATION = EntryClassification(
    path=ENTRY_PRD,
    has_spec=True,
    evidence=["spec_file=test"],
)


def _make_planspace(tmp_path: Path) -> Path:
    planspace = tmp_path / "planspace"
    registry = PathRegistry(planspace)
    registry.ensure_artifacts_tree()
    # Write a minimal spec
    spec = registry.artifacts / "spec.md"
    spec.write_text("# Test Spec\n\nBuild a thing.\n", encoding="utf-8")
    return planspace


def _write_all_artifacts(planspace: Path, with_related: bool = True) -> None:
    """Write all bootstrap artifacts to make the assessor return ready."""
    import json

    registry = PathRegistry(planspace)
    sections_dir = registry.sections_dir()
    sections_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, 3):
        content = f"# Section {i:02d}\n\nContent.\n"
        if with_related:
            content += "\n## Related Files\n\n- src/foo.py\n"
        (sections_dir / f"section-{i:02d}.md").write_text(content, encoding="utf-8")
    registry.global_proposal().write_text("# Proposal\n", encoding="utf-8")
    registry.global_alignment().write_text("# Alignment\n", encoding="utf-8")
    codemap = registry.codemap()
    codemap.parent.mkdir(parents=True, exist_ok=True)
    codemap.write_text("# Codemap\n", encoding="utf-8")
    substrate_dir = registry.substrate_dir()
    substrate_dir.mkdir(parents=True, exist_ok=True)
    (substrate_dir / "status.json").write_text(
        json.dumps({"state": "skipped"}), encoding="utf-8",
    )


class TestConvergenceLoop:
    """Test the convergence loop with a real assessor and mocked stages."""

    def test_already_ready(self, tmp_path: Path) -> None:
        """All artifacts present -> returns True immediately."""
        planspace = _make_planspace(tmp_path)
        _write_all_artifacts(planspace)

        orchestrator = BootstrapOrchestrator(
            assessor=BootstrapAssessor(),
            codemap_builder=MagicMock(),
            section_explorer=MagicMock(),
        )
        assert orchestrator.run_bootstrap(
            planspace, tmp_path / "code", tmp_path / "spec.md",
        )

    def test_decompose_then_ready(self, tmp_path: Path) -> None:
        """Decompose dispatched, produces artifacts, then ready."""
        planspace = _make_planspace(tmp_path)

        def fake_decompose(**kwargs):
            # Simulate the agent writing artifacts
            _write_all_artifacts(planspace)
            return subprocess.CompletedProcess(args=[], returncode=0)

        codemap_builder = MagicMock()
        section_explorer = MagicMock()

        with patch("scan.scan_dispatcher.dispatch_agent", side_effect=fake_decompose) as mock_dispatch, \
             patch("scan.scan_dispatcher.read_scan_model_policy", return_value={}):
            orchestrator = BootstrapOrchestrator(
                assessor=BootstrapAssessor(),
                codemap_builder=codemap_builder,
                section_explorer=section_explorer,
            )
            assert orchestrator.run_bootstrap(planspace, tmp_path / "code", tmp_path / "spec.md")
            mock_dispatch.assert_called_once()

    def test_stage_transitions(self, tmp_path: Path) -> None:
        """Mock assessor that transitions through all stages."""
        planspace = _make_planspace(tmp_path)
        codespace = tmp_path / "code"
        codespace.mkdir()
        spec_path = tmp_path / "spec.md"
        spec_path.write_text("# Spec", encoding="utf-8")

        # Assessor returns stages in order, then ready
        statuses = [
            BootstrapStatus(ready=False, next_stage=STAGE_DECOMPOSE, completed=[], missing=["sections"]),
            BootstrapStatus(ready=False, next_stage=STAGE_CODEMAP, completed=[STAGE_DECOMPOSE], missing=["codemap"]),
            BootstrapStatus(ready=False, next_stage=STAGE_EXPLORE, completed=[STAGE_DECOMPOSE, STAGE_CODEMAP], missing=["related files"]),
            BootstrapStatus(ready=False, next_stage=STAGE_SUBSTRATE, completed=[STAGE_DECOMPOSE, STAGE_CODEMAP, STAGE_EXPLORE], missing=["substrate artifacts"]),
            BootstrapStatus(ready=True, completed=[STAGE_DECOMPOSE, STAGE_CODEMAP, STAGE_EXPLORE, STAGE_SUBSTRATE], missing=[]),
        ]
        mock_assessor = MagicMock()
        mock_assessor.assess.side_effect = statuses
        mock_assessor.classify_entry.return_value = _DEFAULT_CLASSIFICATION

        codemap_builder = MagicMock()
        codemap_builder.run_codemap_build.return_value = True
        section_explorer = MagicMock()

        with patch("scan.scan_dispatcher.dispatch_agent") as mock_dispatch, \
             patch("scan.scan_dispatcher.read_scan_model_policy", return_value={}), \
             patch("scan.substrate.substrate_discoverer.run_substrate_discovery", return_value=True) as mock_substrate:
            mock_dispatch.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            # Simulate decompose writing artifacts
            registry = PathRegistry(planspace)
            registry.sections_dir().mkdir(parents=True, exist_ok=True)
            (registry.sections_dir() / "section-01.md").write_text("# S01\n", encoding="utf-8")
            registry.global_proposal().write_text("# P\n", encoding="utf-8")
            registry.global_alignment().write_text("# A\n", encoding="utf-8")

            orchestrator = BootstrapOrchestrator(
                assessor=mock_assessor,
                codemap_builder=codemap_builder,
                section_explorer=section_explorer,
            )
            assert orchestrator.run_bootstrap(planspace, codespace, spec_path)

        assert mock_assessor.assess.call_count == 5
        mock_dispatch.assert_called_once()  # decompose
        codemap_builder.run_codemap_build.assert_called_once()
        section_explorer.run_section_exploration.assert_called_once()
        mock_substrate.assert_called_once_with(planspace, codespace)

    def test_retry_limit_aborts(self, tmp_path: Path) -> None:
        """Stage fails repeatedly -> returns False after MAX_RETRIES."""
        planspace = _make_planspace(tmp_path)

        # Assessor always returns decompose (never progresses)
        mock_assessor = MagicMock()
        mock_assessor.assess.return_value = BootstrapStatus(
            ready=False, next_stage=STAGE_DECOMPOSE,
            completed=[], missing=["sections"],
        )
        mock_assessor.classify_entry.return_value = _DEFAULT_CLASSIFICATION

        with patch("scan.scan_dispatcher.dispatch_agent") as mock_dispatch, \
             patch("scan.scan_dispatcher.read_scan_model_policy", return_value={}):
            mock_dispatch.return_value = subprocess.CompletedProcess(args=[], returncode=1)

            orchestrator = BootstrapOrchestrator(
                assessor=mock_assessor,
                codemap_builder=MagicMock(),
                section_explorer=MagicMock(),
            )
            assert not orchestrator.run_bootstrap(
                planspace, tmp_path / "code", tmp_path / "spec.md",
            )

        assert mock_dispatch.call_count == MAX_RETRIES

    def test_codemap_failure_retries(self, tmp_path: Path) -> None:
        """Codemap fails once, succeeds on retry."""
        planspace = _make_planspace(tmp_path)
        _write_all_artifacts(planspace, with_related=False)
        # Remove codemap to trigger codemap stage
        PathRegistry(planspace).codemap().unlink()

        call_count = [0]
        def codemap_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return False  # First attempt fails
            # Second attempt succeeds — write codemap
            PathRegistry(planspace).codemap().write_text("# Codemap\n", encoding="utf-8")
            return True

        codemap_builder = MagicMock()
        codemap_builder.run_codemap_build.side_effect = codemap_side_effect

        section_explorer = MagicMock()

        orchestrator = BootstrapOrchestrator(
            assessor=BootstrapAssessor(),
            codemap_builder=codemap_builder,
            section_explorer=section_explorer,
        )
        # Need to add related files for all sections to pass explore check
        for sf in PathRegistry(planspace).sections_dir().glob("section-*.md"):
            content = sf.read_text(encoding="utf-8")
            if "## Related Files" not in content:
                sf.write_text(content + "\n## Related Files\n\n- src/x.py\n", encoding="utf-8")

        # Remove codemap again (was written by _write_all_artifacts without related)
        PathRegistry(planspace).codemap().unlink(missing_ok=True)

        assert orchestrator.run_bootstrap(
            planspace, tmp_path / "code", tmp_path / "spec.md",
        )
        assert codemap_builder.run_codemap_build.call_count == 2

    def test_substrate_stage_calls_discovery(self, tmp_path: Path) -> None:
        """Substrate stage dispatches to run_substrate_discovery."""
        planspace = _make_planspace(tmp_path)
        codespace = tmp_path / "code"
        codespace.mkdir()
        spec_path = tmp_path / "spec.md"
        spec_path.write_text("# Spec", encoding="utf-8")

        # Assessor returns substrate needed, then ready
        statuses = [
            BootstrapStatus(
                ready=False, next_stage=STAGE_SUBSTRATE,
                completed=[STAGE_DECOMPOSE, STAGE_CODEMAP, STAGE_EXPLORE],
                missing=["substrate artifacts"],
            ),
            BootstrapStatus(
                ready=True,
                completed=[STAGE_DECOMPOSE, STAGE_CODEMAP, STAGE_EXPLORE, STAGE_SUBSTRATE],
                missing=[],
            ),
        ]
        mock_assessor = MagicMock()
        mock_assessor.assess.side_effect = statuses
        mock_assessor.classify_entry.return_value = _DEFAULT_CLASSIFICATION

        with patch("scan.substrate.substrate_discoverer.run_substrate_discovery", return_value=True) as mock_discovery:
            orchestrator = BootstrapOrchestrator(
                assessor=mock_assessor,
                codemap_builder=MagicMock(),
                section_explorer=MagicMock(),
            )
            assert orchestrator.run_bootstrap(planspace, codespace, spec_path)

        mock_discovery.assert_called_once_with(planspace, codespace)


class TestEntryClassificationSignal:
    """Tests for entry classification signal file written during bootstrap."""

    def test_signal_file_written_on_bootstrap(self, tmp_path: Path) -> None:
        """Bootstrap writes entry-classification.json to signals dir."""
        import json

        planspace = _make_planspace(tmp_path)
        _write_all_artifacts(planspace)

        orchestrator = BootstrapOrchestrator(
            assessor=BootstrapAssessor(),
            codemap_builder=MagicMock(),
            section_explorer=MagicMock(),
        )
        spec_path = tmp_path / "spec.md"
        spec_path.write_text("# Spec\n", encoding="utf-8")

        assert orchestrator.run_bootstrap(planspace, tmp_path / "code", spec_path)

        signal_path = PathRegistry(planspace).entry_classification_json()
        assert signal_path.is_file()

        data = json.loads(signal_path.read_text(encoding="utf-8"))
        assert "path" in data
        assert data["path"] in ("greenfield", "brownfield", "prd", "partial_governance")
        assert "has_code" in data
        assert "has_spec" in data
        assert "evidence" in data
        assert isinstance(data["evidence"], list)

    def test_signal_file_idempotent_on_resume(self, tmp_path: Path) -> None:
        """Existing signal file is read back, not overwritten."""
        import json

        planspace = _make_planspace(tmp_path)
        _write_all_artifacts(planspace)

        # Pre-write a signal file with a known classification
        registry = PathRegistry(planspace)
        signal_path = registry.entry_classification_json()
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        original_data = {
            "path": "brownfield",
            "has_code": True,
            "has_spec": False,
            "has_governance": False,
            "has_philosophy": False,
            "evidence": ["code_files_present"],
        }
        signal_path.write_text(json.dumps(original_data, indent=2) + "\n", encoding="utf-8")

        orchestrator = BootstrapOrchestrator(
            assessor=BootstrapAssessor(),
            codemap_builder=MagicMock(),
            section_explorer=MagicMock(),
        )
        assert orchestrator.run_bootstrap(
            planspace, tmp_path / "code", tmp_path / "spec.md",
        )

        # Signal file should still contain the original data
        data = json.loads(signal_path.read_text(encoding="utf-8"))
        assert data["path"] == "brownfield"
        assert data["has_code"] is True

    def test_prd_entry_triggers_problem_extraction(self, tmp_path: Path) -> None:
        """PRD entry path triggers governance seeding after decompose."""
        planspace = _make_planspace(tmp_path)
        codespace = tmp_path / "code"
        codespace.mkdir()
        spec_path = tmp_path / "spec.md"
        spec_path.write_text("# Spec\n\n## Constraints\n\n- Must validate input\n", encoding="utf-8")

        def fake_decompose(**kwargs):
            _write_all_artifacts(planspace)
            return subprocess.CompletedProcess(args=[], returncode=0)

        with patch("scan.scan_dispatcher.dispatch_agent", side_effect=fake_decompose), \
             patch("scan.scan_dispatcher.read_scan_model_policy", return_value={}), \
             patch(
                 "orchestrator.engine.bootstrap_orchestrator.BootstrapOrchestrator._run_problem_extraction",
             ) as mock_extract:
            orchestrator = BootstrapOrchestrator(
                assessor=BootstrapAssessor(),
                codemap_builder=MagicMock(),
                section_explorer=MagicMock(),
            )
            assert orchestrator.run_bootstrap(planspace, codespace, spec_path)
            # Should have been called because entry is PRD
            mock_extract.assert_called_once_with(codespace, planspace)
