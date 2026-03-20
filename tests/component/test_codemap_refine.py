"""Unit tests for any-state codemap refinement wiring (Piece 5D).

Tests the signal detection in ContextSidecar and the completion handler
in Reconciler for scan.codemap_refine tasks.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from containers import Services
from dispatch.service.context_sidecar import ContextSidecar
from orchestrator.path_registry import PathRegistry


# ---------------------------------------------------------------------------
# ContextSidecar.check_codemap_refine_signal tests
# ---------------------------------------------------------------------------


class TestCheckCodemapRefineSignal:
    """ContextSidecar detects and consumes codemap-refine signals."""

    def _make_sidecar(self) -> ContextSidecar:
        return ContextSidecar(artifact_io=Services.artifact_io())

    def test_returns_false_when_no_section(self, tmp_path: Path) -> None:
        sidecar = self._make_sidecar()
        assert sidecar.check_codemap_refine_signal(tmp_path, None) is False

    def test_returns_false_when_no_signal_file(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()
        sidecar = self._make_sidecar()
        assert sidecar.check_codemap_refine_signal(tmp_path, "01") is False

    def test_returns_true_when_signal_exists(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        signal_path = paths.codemap_refine_signal("01")
        signal_path.write_text(
            json.dumps({"reason": "incomplete coverage"}),
            encoding="utf-8",
        )
        assert signal_path.is_file()

        sidecar = self._make_sidecar()
        assert sidecar.check_codemap_refine_signal(tmp_path, "01") is True

    def test_consumes_signal_file(self, tmp_path: Path) -> None:
        """Signal file is deleted after detection (consume-once)."""
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        signal_path = paths.codemap_refine_signal("02")
        signal_path.write_text("{}", encoding="utf-8")

        sidecar = self._make_sidecar()
        sidecar.check_codemap_refine_signal(tmp_path, "02")

        assert not signal_path.exists()

    def test_second_call_returns_false_after_consumption(
        self, tmp_path: Path,
    ) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        signal_path = paths.codemap_refine_signal("03")
        signal_path.write_text("{}", encoding="utf-8")

        sidecar = self._make_sidecar()
        assert sidecar.check_codemap_refine_signal(tmp_path, "03") is True
        assert sidecar.check_codemap_refine_signal(tmp_path, "03") is False


class TestResolveContextCodemapRefineMarker:
    """resolve_context sets _codemap_refine_needed when signal is present."""

    def test_sets_marker_when_signal_present(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        # Write signal file
        signal_path = paths.codemap_refine_signal("01")
        signal_path.write_text("{}", encoding="utf-8")

        # Agent file with at least one context category
        agent_file = tmp_path / "agent.md"
        agent_file.write_text(
            "---\ncontext:\n  - section_spec\n---\n",
            encoding="utf-8",
        )

        sidecar = ContextSidecar(artifact_io=Services.artifact_io())
        result = sidecar.resolve_context(
            str(agent_file), tmp_path, section="01",
        )

        assert result.get("_codemap_refine_needed") == "01"

    def test_no_marker_when_no_signal(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        agent_file = tmp_path / "agent.md"
        agent_file.write_text(
            "---\ncontext:\n  - section_spec\n---\n",
            encoding="utf-8",
        )

        sidecar = ContextSidecar(artifact_io=Services.artifact_io())
        result = sidecar.resolve_context(
            str(agent_file), tmp_path, section="01",
        )

        assert "_codemap_refine_needed" not in result

    def test_no_marker_when_no_section(self, tmp_path: Path) -> None:
        agent_file = tmp_path / "agent.md"
        agent_file.write_text(
            "---\ncontext:\n  - codemap\n---\n",
            encoding="utf-8",
        )

        sidecar = ContextSidecar(artifact_io=Services.artifact_io())
        result = sidecar.resolve_context(str(agent_file), tmp_path)

        assert "_codemap_refine_needed" not in result


# ---------------------------------------------------------------------------
# Reconciler._handle_codemap_refine_complete tests
# ---------------------------------------------------------------------------


class TestHandleCodemapRefineComplete:
    """Reconciler writes refined codemap fragment on task completion."""

    def _make_reconciler(self):
        from flow.engine.reconciler import Reconciler
        from implementation.service.traceability_writer import TraceabilityWriter

        artifact_io = Services.artifact_io()
        return Reconciler(
            artifact_io=artifact_io,
            research=MagicMock(),
            prompt_guard=MagicMock(),
            flow_submitter=MagicMock(),
            gate_repository=MagicMock(),
            traceability_writer=MagicMock(spec=TraceabilityWriter),
        )

    def _make_task(
        self, section: str = "01", task_type: str = "scan.codemap_refine",
    ) -> dict:
        return {
            "id": 42,
            "task_type": task_type,
            "concern_scope": f"section-{section}",
            "flow_id": "flow-1",
            "chain_id": "chain-1",
            "instance_id": "inst-1",
        }

    def test_writes_fragment_on_success(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        # Write agent output
        output_file = tmp_path / "output.md"
        output_file.write_text("# Refined Codemap\nBetter coverage.", encoding="utf-8")

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("01"),
            tmp_path,
            str(output_file),
        )

        fragment = paths.section_codemap("01")
        assert fragment.is_file()
        assert "Refined Codemap" in fragment.read_text(encoding="utf-8")

    def test_skips_non_codemap_refine_task(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("01", task_type="section.propose"),
            tmp_path,
            "some/output.md",
        )

        assert not paths.section_codemap("01").exists()

    def test_skips_missing_section_number(self, tmp_path: Path) -> None:
        reconciler = self._make_reconciler()
        task = {
            "id": 1,
            "task_type": "scan.codemap_refine",
            "concern_scope": "global",
        }
        # Should not raise
        reconciler._handle_codemap_refine_complete(task, tmp_path, None)

    def test_skips_when_no_output_path(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("01"), tmp_path, None,
        )

        assert not paths.section_codemap("01").exists()

    def test_skips_when_output_file_missing(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("01"),
            tmp_path,
            str(tmp_path / "nonexistent.md"),
        )

        assert not paths.section_codemap("01").exists()

    def test_skips_empty_output(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        output_file = tmp_path / "empty.md"
        output_file.write_text("   \n  \n", encoding="utf-8")

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("01"), tmp_path, str(output_file),
        )

        assert not paths.section_codemap("01").exists()

    def test_overwrites_existing_fragment(self, tmp_path: Path) -> None:
        """Refinement replaces the existing fragment, not appends."""
        paths = PathRegistry(tmp_path)
        paths.ensure_artifacts_tree()

        # Write an initial fragment
        fragment = paths.section_codemap("01")
        fragment.parent.mkdir(parents=True, exist_ok=True)
        fragment.write_text("Old content", encoding="utf-8")

        # Write refined output
        output_file = tmp_path / "refined.md"
        output_file.write_text("New refined content", encoding="utf-8")

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("01"), tmp_path, str(output_file),
        )

        content = fragment.read_text(encoding="utf-8")
        assert content == "New refined content"
        assert "Old content" not in content

    def test_creates_fragments_dir_if_missing(self, tmp_path: Path) -> None:
        paths = PathRegistry(tmp_path)
        # Only create artifacts dir, not the full tree
        paths.artifacts.mkdir(parents=True, exist_ok=True)

        output_file = tmp_path / "output.md"
        output_file.write_text("# Fragment", encoding="utf-8")

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("01"), tmp_path, str(output_file),
        )

        assert paths.section_codemap("01").is_file()
