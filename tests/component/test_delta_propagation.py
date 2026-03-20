"""Unit tests for codemap delta propagation and fragment reads (Piece 5E).

Tests cover:
- Reconciler writes delta artifact on codemap_refine completion
- Reconciler merges delta into parent section's fragment
- Context sidecar prefers section fragment over global codemap
- Context builder prefers section fragment over global codemap
- Orchestrator merges child deltas into parent fragment
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from containers import Services
from dispatch.prompt.context_builder import (
    ContextBuilder,
    _resolve_codemap_path,
    _build_strategic_context,
    _build_alignment_context,
)
from dispatch.service.context_sidecar import ContextSidecar, _resolve_codemap
from flow.engine.reconciler import (
    _write_codemap_delta,
    _propagate_delta_to_parent,
    _lookup_parent_section,
)
from flow.service.task_db_client import init_db
from orchestrator.engine.state_machine_orchestrator import _merge_child_deltas
from orchestrator.path_registry import PathRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def planspace(tmp_path: Path) -> Path:
    ps = tmp_path / "planspace"
    paths = PathRegistry(ps)
    paths.ensure_artifacts_tree()
    return ps


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "run.db"
    init_db(db)
    return db


def _insert_section(db_path: Path, num: str, parent: str | None = None) -> None:
    """Insert a section_states row for testing."""
    from orchestrator.engine.section_state_machine import SectionState, set_section_state

    set_section_state(
        db_path, num, SectionState.PENDING,
        parent_section=parent,
    )


# ---------------------------------------------------------------------------
# _write_codemap_delta tests
# ---------------------------------------------------------------------------


class TestWriteCodemapDelta:
    """Delta artifact is written alongside the fragment update."""

    def test_writes_delta_json(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)
        _write_codemap_delta(paths, "01", "line1\nline2\nline3")

        delta_path = paths.codemap_delta("01")
        assert delta_path.is_file()
        data = json.loads(delta_path.read_text(encoding="utf-8"))
        assert data["section"] == "01"
        assert data["lines"] == ["line1", "line2", "line3"]

    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        ps = tmp_path / "new-planspace"
        paths = PathRegistry(ps)
        # Don't call ensure_artifacts_tree -- directory doesn't exist yet.
        _write_codemap_delta(paths, "02", "content")
        assert paths.codemap_delta("02").is_file()

    def test_overwrites_previous_delta(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)
        _write_codemap_delta(paths, "03", "old content")
        _write_codemap_delta(paths, "03", "new content")

        data = json.loads(
            paths.codemap_delta("03").read_text(encoding="utf-8"),
        )
        assert data["lines"] == ["new content"]


# ---------------------------------------------------------------------------
# _lookup_parent_section tests
# ---------------------------------------------------------------------------


class TestLookupParentSection:
    """Parent section lookup from section_states."""

    def test_returns_parent_when_exists(
        self, db_path: Path, planspace: Path,
    ) -> None:
        _insert_section(db_path, "01")
        _insert_section(db_path, "0101", parent="01")
        assert _lookup_parent_section(db_path, "0101") == "01"

    def test_returns_none_for_root_section(
        self, db_path: Path, planspace: Path,
    ) -> None:
        _insert_section(db_path, "01")
        assert _lookup_parent_section(db_path, "01") is None

    def test_returns_none_for_missing_section(
        self, db_path: Path,
    ) -> None:
        assert _lookup_parent_section(db_path, "99") is None


# ---------------------------------------------------------------------------
# _propagate_delta_to_parent tests
# ---------------------------------------------------------------------------


class TestPropagateDeltaToParent:
    """Delta propagation merges child refinement into parent fragment."""

    def test_creates_parent_fragment_from_child(
        self, db_path: Path, planspace: Path,
    ) -> None:
        _insert_section(db_path, "01")
        _insert_section(db_path, "0101", parent="01")

        paths = PathRegistry(planspace)
        refined = "child line 1\nchild line 2"
        _propagate_delta_to_parent(paths, db_path, "0101", refined)

        parent_frag = paths.section_codemap("01")
        assert parent_frag.is_file()
        content = parent_frag.read_text(encoding="utf-8")
        assert "child line 1" in content
        assert "child line 2" in content

    def test_appends_new_lines_to_existing_parent_fragment(
        self, db_path: Path, planspace: Path,
    ) -> None:
        _insert_section(db_path, "01")
        _insert_section(db_path, "0101", parent="01")

        paths = PathRegistry(planspace)
        parent_frag = paths.section_codemap("01")
        parent_frag.parent.mkdir(parents=True, exist_ok=True)
        parent_frag.write_text("existing line\n", encoding="utf-8")

        _propagate_delta_to_parent(
            paths, db_path, "0101", "existing line\nnew line",
        )

        content = parent_frag.read_text(encoding="utf-8")
        assert "existing line" in content
        assert "new line" in content
        # "existing line" should not be duplicated
        assert content.count("existing line") == 1

    def test_no_op_for_root_section(
        self, db_path: Path, planspace: Path,
    ) -> None:
        _insert_section(db_path, "01")
        paths = PathRegistry(planspace)
        # Should not raise or create any file
        _propagate_delta_to_parent(paths, db_path, "01", "some content")
        assert not paths.section_codemap("01").exists()

    def test_no_op_when_no_new_lines(
        self, db_path: Path, planspace: Path,
    ) -> None:
        _insert_section(db_path, "01")
        _insert_section(db_path, "0101", parent="01")

        paths = PathRegistry(planspace)
        parent_frag = paths.section_codemap("01")
        parent_frag.parent.mkdir(parents=True, exist_ok=True)
        parent_frag.write_text("already present\n", encoding="utf-8")

        _propagate_delta_to_parent(
            paths, db_path, "0101", "already present",
        )

        # Content should not change -- no new lines to add.
        content = parent_frag.read_text(encoding="utf-8")
        assert content == "already present\n"


# ---------------------------------------------------------------------------
# Reconciler integration: delta on codemap_refine_complete
# ---------------------------------------------------------------------------


class TestReconcilerDeltaPropagation:
    """Reconciler writes delta and propagates to parent on refine complete."""

    def _make_reconciler(self):
        from flow.engine.reconciler import Reconciler
        from implementation.service.traceability_writer import TraceabilityWriter

        return Reconciler(
            artifact_io=Services.artifact_io(),
            research=MagicMock(),
            prompt_guard=MagicMock(),
            flow_submitter=MagicMock(),
            gate_repository=MagicMock(),
            traceability_writer=MagicMock(spec=TraceabilityWriter),
        )

    def _make_task(self, section: str = "0101") -> dict:
        return {
            "id": 42,
            "task_type": "scan.codemap_refine",
            "concern_scope": f"section-{section}",
            "flow_id": "flow-1",
            "chain_id": "chain-1",
            "instance_id": "inst-1",
        }

    def test_writes_delta_on_completion(
        self, planspace: Path,
    ) -> None:
        paths = PathRegistry(planspace)
        output_file = planspace / "output.md"
        output_file.write_text("# Refined\nnew stuff", encoding="utf-8")

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("01"), planspace, str(output_file),
        )

        delta = paths.codemap_delta("01")
        assert delta.is_file()
        data = json.loads(delta.read_text(encoding="utf-8"))
        assert data["section"] == "01"
        assert "# Refined" in data["lines"]

    def test_propagates_to_parent_when_db_provided(
        self, db_path: Path, planspace: Path,
    ) -> None:
        _insert_section(db_path, "01")
        _insert_section(db_path, "0101", parent="01")

        paths = PathRegistry(planspace)
        output_file = planspace / "output.md"
        output_file.write_text("child content", encoding="utf-8")

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("0101"), planspace, str(output_file),
            db_path=db_path,
        )

        parent_frag = paths.section_codemap("01")
        assert parent_frag.is_file()
        assert "child content" in parent_frag.read_text(encoding="utf-8")

    def test_no_parent_propagation_when_db_not_provided(
        self, planspace: Path,
    ) -> None:
        paths = PathRegistry(planspace)
        output_file = planspace / "output.md"
        output_file.write_text("content", encoding="utf-8")

        reconciler = self._make_reconciler()
        reconciler._handle_codemap_refine_complete(
            self._make_task("0101"), planspace, str(output_file),
        )

        # Fragment for 01a should exist, but no parent propagation
        assert paths.section_codemap("0101").is_file()
        # No parent fragment created (no db to look up parent)
        assert not paths.section_codemap("01").exists()


# ---------------------------------------------------------------------------
# Context sidecar fragment reads
# ---------------------------------------------------------------------------


class TestContextSidecarFragmentReads:
    """_resolve_codemap prefers section fragment over global codemap."""

    def test_returns_section_fragment_when_exists(
        self, planspace: Path,
    ) -> None:
        paths = PathRegistry(planspace)
        paths.codemap().write_text("global codemap", encoding="utf-8")

        frag = paths.section_codemap("01")
        frag.parent.mkdir(parents=True, exist_ok=True)
        frag.write_text("section fragment", encoding="utf-8")

        result = _resolve_codemap(planspace, "01")
        assert "section fragment" in result
        assert "global codemap" not in result

    def test_falls_back_to_global_codemap(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)
        paths.codemap().write_text("global codemap", encoding="utf-8")

        result = _resolve_codemap(planspace, "01")
        assert "global codemap" in result

    def test_falls_back_when_no_section(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)
        paths.codemap().write_text("global codemap", encoding="utf-8")

        result = _resolve_codemap(planspace, None)
        assert "global codemap" in result

    def test_appends_corrections_to_section_fragment(
        self, planspace: Path,
    ) -> None:
        paths = PathRegistry(planspace)
        paths.codemap().write_text("global", encoding="utf-8")

        frag = paths.section_codemap("01")
        frag.parent.mkdir(parents=True, exist_ok=True)
        frag.write_text("section fragment", encoding="utf-8")

        corrections = paths.corrections()
        corrections.parent.mkdir(parents=True, exist_ok=True)
        corrections.write_text(
            json.dumps({"fix": "data"}), encoding="utf-8",
        )

        result = _resolve_codemap(planspace, "01")
        assert "section fragment" in result
        assert "Codemap Corrections (authoritative)" in result

    def test_returns_empty_when_nothing_exists(self, planspace: Path) -> None:
        result = _resolve_codemap(planspace, "01")
        assert result == ""

    def test_integration_via_resolve_context(self, planspace: Path) -> None:
        """Full integration: resolve_context returns section fragment."""
        paths = PathRegistry(planspace)
        paths.codemap().write_text("global codemap", encoding="utf-8")

        frag = paths.section_codemap("05")
        frag.parent.mkdir(parents=True, exist_ok=True)
        frag.write_text("scoped fragment for 05", encoding="utf-8")

        agent_file = planspace / "agent.md"
        agent_file.write_text(
            "---\ncontext:\n  - codemap\n---\n",
            encoding="utf-8",
        )

        sidecar = ContextSidecar(artifact_io=Services.artifact_io())
        result = sidecar.resolve_context(
            str(agent_file), planspace, section="05",
        )

        assert "scoped fragment for 05" in result["codemap"]
        assert "global codemap" not in result["codemap"]


# ---------------------------------------------------------------------------
# Context builder fragment reads
# ---------------------------------------------------------------------------


class TestContextBuilderFragmentReads:
    """_resolve_codemap_path prefers section fragment over global codemap."""

    def test_returns_section_fragment_path(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)
        paths.codemap().write_text("global", encoding="utf-8")

        frag = paths.section_codemap("01")
        frag.parent.mkdir(parents=True, exist_ok=True)
        frag.write_text("fragment", encoding="utf-8")

        result = _resolve_codemap_path(paths, "01")
        assert result == frag

    def test_falls_back_to_global_codemap(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)
        result = _resolve_codemap_path(paths, "01")
        assert result == paths.codemap()

    def test_falls_back_when_no_section(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)
        result = _resolve_codemap_path(paths)
        assert result == paths.codemap()

    def test_strategic_context_uses_fragment(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)
        frag = paths.section_codemap("02")
        frag.parent.mkdir(parents=True, exist_ok=True)
        frag.write_text("section 02 fragment", encoding="utf-8")

        ctx = _build_strategic_context(paths, "02")
        assert str(frag) in ctx["codemap_ref"]

    def test_alignment_context_uses_fragment(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)
        frag = paths.section_codemap("03")
        frag.parent.mkdir(parents=True, exist_ok=True)
        frag.write_text("section 03 fragment", encoding="utf-8")

        ctx = _build_alignment_context(paths, "03")
        assert str(frag) in ctx["codemap_line"]


# ---------------------------------------------------------------------------
# Orchestrator _merge_child_deltas tests
# ---------------------------------------------------------------------------


class TestMergeChildDeltas:
    """Orchestrator merges child deltas into parent fragment."""

    def test_merges_single_child_delta(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)

        # Write child delta.
        delta = paths.codemap_delta("0101")
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps({"section": "0101", "lines": ["child line"]}),
            encoding="utf-8",
        )

        _merge_child_deltas(planspace, "01", ["0101"])

        parent_frag = paths.section_codemap("01")
        assert parent_frag.is_file()
        assert "child line" in parent_frag.read_text(encoding="utf-8")

    def test_consumes_delta_after_merge(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)

        delta = paths.codemap_delta("0101")
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps({"section": "0101", "lines": ["data"]}),
            encoding="utf-8",
        )

        _merge_child_deltas(planspace, "01", ["0101"])
        assert not delta.exists()

    def test_appends_without_duplicating(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)

        # Existing parent fragment.
        parent_frag = paths.section_codemap("01")
        parent_frag.parent.mkdir(parents=True, exist_ok=True)
        parent_frag.write_text("existing\n", encoding="utf-8")

        # Child delta with overlap.
        delta = paths.codemap_delta("0101")
        delta.write_text(
            json.dumps({
                "section": "0101",
                "lines": ["existing", "new line"],
            }),
            encoding="utf-8",
        )

        _merge_child_deltas(planspace, "01", ["0101"])

        content = parent_frag.read_text(encoding="utf-8")
        assert "new line" in content
        assert content.count("existing") == 1

    def test_merges_multiple_children(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)

        for child, line in [("0101", "from child a"), ("0102", "from child b")]:
            delta = paths.codemap_delta(child)
            delta.parent.mkdir(parents=True, exist_ok=True)
            delta.write_text(
                json.dumps({"section": child, "lines": [line]}),
                encoding="utf-8",
            )

        _merge_child_deltas(planspace, "01", ["0101", "0102"])

        content = paths.section_codemap("01").read_text(encoding="utf-8")
        assert "from child a" in content
        assert "from child b" in content

    def test_skips_children_without_deltas(self, planspace: Path) -> None:
        """No error when a child has no delta artifact."""
        _merge_child_deltas(planspace, "01", ["0101", "0102"])
        assert not PathRegistry(planspace).section_codemap("01").exists()

    def test_skips_empty_delta(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)

        delta = paths.codemap_delta("0101")
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps({"section": "0101", "lines": []}),
            encoding="utf-8",
        )

        _merge_child_deltas(planspace, "01", ["0101"])
        # Empty delta consumed, no parent fragment created.
        assert not delta.exists()
        assert not paths.section_codemap("01").exists()

    def test_tolerates_malformed_delta(self, planspace: Path) -> None:
        paths = PathRegistry(planspace)

        delta = paths.codemap_delta("0101")
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text("not json", encoding="utf-8")

        # Should not raise.
        _merge_child_deltas(planspace, "01", ["0101"])
