"""Tests for post-implementation codemap refresh (Item 30).

Verifies that PipelineOrchestrator._refresh_codemap_after_implementation()
triggers a codemap rebuild when sections produced modified files, skips
when no files were modified, and tolerates a missing codemap_builder.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from orchestrator.types import SectionResult
from pipeline.context import DispatchContext


def _make_orchestrator(codemap_builder=None):
    """Build a minimal PipelineOrchestrator with only the deps needed."""
    from orchestrator.engine.pipeline_orchestrator import PipelineOrchestrator

    return PipelineOrchestrator(
        communicator=MagicMock(),
        logger=MagicMock(),
        config=MagicMock(),
        artifact_io=MagicMock(),
        prompt_guard=MagicMock(),
        section_alignment=MagicMock(),
        change_tracker=MagicMock(),
        pipeline_control=MagicMock(),
        coordination_controller=MagicMock(),
        implementation_phase=MagicMock(),
        reconciliation_phase=MagicMock(),
        codemap_builder=codemap_builder,
    )


def _make_ctx(tmp_path: Path) -> DispatchContext:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir()
    codespace.mkdir()
    (planspace / "artifacts").mkdir()
    (planspace / "artifacts" / "scan-logs").mkdir(parents=True)
    return DispatchContext(
        planspace=planspace,
        codespace=codespace,
        _policies=MagicMock(),
    )


class TestRefreshCodemapAfterImplementation:
    """Post-implementation codemap refresh."""

    def test_triggers_rebuild_when_files_modified(self, tmp_path: Path) -> None:
        """Codemap rebuild is triggered when implementation modified files."""
        builder = MagicMock()
        builder.run_codemap_build.return_value = True
        orch = _make_orchestrator(codemap_builder=builder)
        ctx = _make_ctx(tmp_path)

        section_results = {
            "01": SectionResult(
                section_number="01",
                aligned=True,
                modified_files=["src/main.py", "src/utils.py"],
            ),
            "02": SectionResult(
                section_number="02",
                aligned=True,
                modified_files=["src/db.py"],
            ),
        }

        orch._refresh_codemap_after_implementation(section_results, ctx)

        builder.run_codemap_build.assert_called_once()
        kw = builder.run_codemap_build.call_args.kwargs
        assert kw["codespace"] == ctx.codespace
        assert kw["codemap_path"].name == "codemap.md"
        assert kw["fingerprint_path"].name == "codemap.codespace.fingerprint"
        orch._logger.log.assert_any_call(
            "[CODEMAP] Post-implementation refresh complete",
        )

    def test_skips_when_no_files_modified(self, tmp_path: Path) -> None:
        """No rebuild when implementation produced no modified files."""
        builder = MagicMock()
        orch = _make_orchestrator(codemap_builder=builder)
        ctx = _make_ctx(tmp_path)

        section_results = {
            "01": SectionResult(section_number="01", aligned=False),
        }

        orch._refresh_codemap_after_implementation(section_results, ctx)

        builder.run_codemap_build.assert_not_called()

    def test_skips_when_no_codemap_builder(self, tmp_path: Path) -> None:
        """Graceful no-op when codemap_builder is None."""
        orch = _make_orchestrator(codemap_builder=None)
        ctx = _make_ctx(tmp_path)

        section_results = {
            "01": SectionResult(
                section_number="01",
                aligned=True,
                modified_files=["src/main.py"],
            ),
        }

        # Should not raise
        orch._refresh_codemap_after_implementation(section_results, ctx)

    def test_continues_on_rebuild_failure(self, tmp_path: Path) -> None:
        """Codemap rebuild failure is logged but does not raise."""
        builder = MagicMock()
        builder.run_codemap_build.return_value = False
        orch = _make_orchestrator(codemap_builder=builder)
        ctx = _make_ctx(tmp_path)

        section_results = {
            "01": SectionResult(
                section_number="01",
                aligned=True,
                modified_files=["src/main.py"],
            ),
        }

        orch._refresh_codemap_after_implementation(section_results, ctx)

        builder.run_codemap_build.assert_called_once()
        orch._logger.log.assert_any_call(
            "[CODEMAP] Post-implementation refresh failed "
            "\u2014 continuing with existing codemap",
        )

    def test_skips_when_results_empty(self, tmp_path: Path) -> None:
        """No rebuild when section_results dict is empty."""
        builder = MagicMock()
        orch = _make_orchestrator(codemap_builder=builder)
        ctx = _make_ctx(tmp_path)

        orch._refresh_codemap_after_implementation({}, ctx)

        builder.run_codemap_build.assert_not_called()

    def test_aggregates_files_from_all_sections(self, tmp_path: Path) -> None:
        """Modified file count in log message aggregates across sections."""
        builder = MagicMock()
        builder.run_codemap_build.return_value = True
        orch = _make_orchestrator(codemap_builder=builder)
        ctx = _make_ctx(tmp_path)

        section_results = {
            "01": SectionResult(
                section_number="01",
                aligned=True,
                modified_files=["a.py", "b.py"],
            ),
            "02": SectionResult(
                section_number="02",
                aligned=True,
                modified_files=["c.py"],
            ),
            "03": SectionResult(
                section_number="03",
                aligned=False,
                modified_files=[],
            ),
        }

        orch._refresh_codemap_after_implementation(section_results, ctx)

        # The log message should report 3 files total
        orch._logger.log.assert_any_call(
            "[CODEMAP] Triggering post-implementation refresh "
            "(3 files modified)",
        )
