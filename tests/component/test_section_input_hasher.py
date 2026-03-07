from __future__ import annotations

from pathlib import Path

from src.scripts.lib.services.section_input_hasher import (
    coordination_recheck_hash,
    section_inputs_hash,
)
from src.scripts.section_loop.types import Section


class TestSectionInputsHash:
    def _make_sections_by_num(self, planspace: Path) -> dict[str, Section]:
        return {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=["src/main.py", "src/utils.py"],
            ),
        }

    def test_is_deterministic(self, planspace: Path, codespace: Path) -> None:
        sections = self._make_sections_by_num(planspace)

        h1 = section_inputs_hash("01", planspace, codespace, sections)
        h2 = section_inputs_hash("01", planspace, codespace, sections)

        assert h1 == h2
        assert len(h1) == 64

    def test_changes_when_section_artifact_changes(
        self,
        planspace: Path,
        codespace: Path,
    ) -> None:
        sections = self._make_sections_by_num(planspace)
        problem_frame = (
            planspace / "artifacts" / "sections" / "section-01-problem-frame.md"
        )

        h1 = section_inputs_hash("01", planspace, codespace, sections)
        problem_frame.write_text("summarized problem frame", encoding="utf-8")
        h2 = section_inputs_hash("01", planspace, codespace, sections)

        assert h1 != h2

    def test_hashes_input_refs_and_referenced_files(
        self,
        planspace: Path,
        codespace: Path,
    ) -> None:
        sections = self._make_sections_by_num(planspace)
        inputs_dir = planspace / "artifacts" / "inputs" / "section-01"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        referenced = codespace / "src" / "config.py"
        referenced.parent.mkdir(parents=True, exist_ok=True)
        referenced.write_text("value = 1\n", encoding="utf-8")
        (inputs_dir / "config.ref").write_text(str(referenced), encoding="utf-8")

        h1 = section_inputs_hash("01", planspace, codespace, sections)
        referenced.write_text("value = 2\n", encoding="utf-8")
        h2 = section_inputs_hash("01", planspace, codespace, sections)

        assert h1 != h2


class TestCoordinationRecheckHash:
    def test_includes_modified_files(self, planspace: Path, codespace: Path) -> None:
        sections = {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
            ),
        }
        modified = codespace / "src" / "worker.py"
        modified.parent.mkdir(parents=True, exist_ok=True)
        modified.write_text("VALUE = 1\n", encoding="utf-8")

        h1 = coordination_recheck_hash(
            "01",
            planspace,
            codespace,
            sections,
            ["src/worker.py"],
        )
        modified.write_text("VALUE = 2\n", encoding="utf-8")
        h2 = coordination_recheck_hash(
            "01",
            planspace,
            codespace,
            sections,
            ["src/worker.py"],
        )

        assert h1 != h2
