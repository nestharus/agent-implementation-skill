"""Integration tests for pipeline_control module.

Tests hash computation, alignment flags, and excerpt invalidation.
Uses real file I/O and real db.sh for pipeline state queries.
"""

from pathlib import Path

from orchestrator.service.pipeline_control import (
    _section_inputs_hash,
    alignment_changed_pending,
    check_pipeline_state,
)
from staleness.service.change_tracker import (
    invalidate_excerpts as _invalidate_excerpts,
    make_alignment_checker,
)
from _config import AGENT_NAME, DB_SH
from orchestrator.types import Section

_check_and_clear_alignment_changed = make_alignment_checker(DB_SH, AGENT_NAME)


class TestAlignmentChangedFlag:
    def test_not_pending_by_default(self, planspace: Path) -> None:
        assert alignment_changed_pending(planspace) is False

    def test_set_and_check(self, planspace: Path) -> None:
        flag = planspace / "artifacts" / "alignment-changed-pending"
        flag.write_text("1")
        assert alignment_changed_pending(planspace) is True

    def test_check_and_clear(self, planspace: Path) -> None:
        flag = planspace / "artifacts" / "alignment-changed-pending"
        flag.write_text("1")
        assert _check_and_clear_alignment_changed(planspace) is True
        assert alignment_changed_pending(planspace) is False

    def test_clear_when_not_set(self, planspace: Path) -> None:
        assert _check_and_clear_alignment_changed(planspace) is False


class TestInvalidateExcerpts:
    def test_deletes_excerpt_files(self, planspace: Path) -> None:
        sections = planspace / "artifacts" / "sections"
        (sections / "section-01-proposal-excerpt.md").write_text("excerpt")
        (sections / "section-01-alignment-excerpt.md").write_text("excerpt")
        (sections / "section-01.md").write_text("spec")
        _invalidate_excerpts(planspace)
        assert not (sections / "section-01-proposal-excerpt.md").exists()
        assert not (sections / "section-01-alignment-excerpt.md").exists()
        # Section spec preserved
        assert (sections / "section-01.md").exists()

    def test_no_excerpts_no_error(self, planspace: Path) -> None:
        _invalidate_excerpts(planspace)  # should not raise


class TestSectionInputsHash:
    def _make_sections_by_num(self, planspace: Path) -> dict:
        return {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=["src/main.py", "src/utils.py"],
            ),
        }

    def test_deterministic(self, planspace: Path, codespace: Path) -> None:
        sections = self._make_sections_by_num(planspace)
        h1 = _section_inputs_hash("01", planspace, sections)
        h2 = _section_inputs_hash("01", planspace, sections)
        assert h1 == h2
        assert len(h1) == 64

    def test_changes_when_spec_changes(
        self, planspace: Path, codespace: Path,
    ) -> None:
        sections = self._make_sections_by_num(planspace)
        spec = planspace / "artifacts" / "sections" / "section-01.md"
        spec.write_text("original spec")
        h1 = _section_inputs_hash("01", planspace, sections)
        spec.write_text("modified spec")
        h2 = _section_inputs_hash("01", planspace, sections)
        assert h1 != h2

    def test_changes_when_note_added(
        self, planspace: Path, codespace: Path,
    ) -> None:
        sections = self._make_sections_by_num(planspace)
        h1 = _section_inputs_hash("01", planspace, sections)
        note = planspace / "artifacts" / "notes" / "from-02-to-01.md"
        note.write_text("consequence note from section 02")
        h2 = _section_inputs_hash("01", planspace, sections)
        assert h1 != h2

    def test_changes_when_proposal_added(
        self, planspace: Path, codespace: Path,
    ) -> None:
        sections = self._make_sections_by_num(planspace)
        h1 = _section_inputs_hash("01", planspace, sections)
        proposal = (planspace / "artifacts" / "proposals"
                    / "section-01-integration-proposal.md")
        proposal.write_text("integration proposal content")
        h2 = _section_inputs_hash("01", planspace, sections)
        assert h1 != h2

    def test_changes_when_microstrategy_added(
        self, planspace: Path, codespace: Path,
    ) -> None:
        sections = self._make_sections_by_num(planspace)
        h1 = _section_inputs_hash("01", planspace, sections)
        ms = (planspace / "artifacts" / "proposals"
              / "section-01-microstrategy.md")
        ms.write_text("microstrategy content")
        h2 = _section_inputs_hash("01", planspace, sections)
        assert h1 != h2

    def test_changes_when_todos_added(
        self, planspace: Path, codespace: Path,
    ) -> None:
        sections = self._make_sections_by_num(planspace)
        h1 = _section_inputs_hash("01", planspace, sections)
        todos = planspace / "artifacts" / "todos" / "section-01-todos.md"
        todos.write_text("TODO: implement auth\n")
        h2 = _section_inputs_hash("01", planspace, sections)
        assert h1 != h2

    def test_changes_when_codemap_added(
        self, planspace: Path, codespace: Path,
    ) -> None:
        sections = self._make_sections_by_num(planspace)
        h1 = _section_inputs_hash("01", planspace, sections)
        codemap = planspace / "artifacts" / "codemap.md"
        codemap.write_text("# Codemap\nfile listings...")
        h2 = _section_inputs_hash("01", planspace, sections)
        assert h1 != h2


class TestCheckPipelineState:
    def test_default_is_running(self, planspace: Path) -> None:
        state = check_pipeline_state(planspace)
        assert state == "running"
