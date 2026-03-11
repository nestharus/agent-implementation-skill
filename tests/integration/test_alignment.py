"""Integration tests for alignment module.

Tests verdict parsing (pure logic) and problem extraction
(needs dispatch_agent mock for adjudicator fallback).
"""

import json
from pathlib import Path
from unittest.mock import patch

from staleness.section_alignment import (
    _extract_problems,
    _parse_alignment_verdict,
    collect_modified_files,
)
from orchestrator.types import Section


class TestCollectModifiedFiles:
    def _make_section(self, planspace: Path) -> Section:
        return Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
        )

    def test_no_report_file(self, planspace: Path, codespace: Path) -> None:
        section = self._make_section(planspace)
        result = collect_modified_files(planspace, section, codespace)
        assert result == []

    def test_relative_paths(self, planspace: Path, codespace: Path) -> None:
        section = self._make_section(planspace)
        report = planspace / "artifacts" / "impl-01-modified.txt"
        report.write_text("src/main.py\nsrc/utils.py\n")
        result = collect_modified_files(planspace, section, codespace)
        assert sorted(result) == ["src/main.py", "src/utils.py"]

    def test_absolute_path_under_codespace(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = self._make_section(planspace)
        report = planspace / "artifacts" / "impl-01-modified.txt"
        abs_path = str(codespace / "src" / "main.py")
        report.write_text(f"{abs_path}\n")
        result = collect_modified_files(planspace, section, codespace)
        assert result == ["src/main.py"]

    def test_path_outside_codespace_rejected(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = self._make_section(planspace)
        report = planspace / "artifacts" / "impl-01-modified.txt"
        report.write_text("/etc/passwd\n")
        result = collect_modified_files(planspace, section, codespace)
        assert result == []

    def test_dotdot_escape_rejected(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = self._make_section(planspace)
        report = planspace / "artifacts" / "impl-01-modified.txt"
        report.write_text("src/../../etc/passwd\n")
        result = collect_modified_files(planspace, section, codespace)
        assert result == []

    def test_empty_lines_skipped(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = self._make_section(planspace)
        report = planspace / "artifacts" / "impl-01-modified.txt"
        report.write_text("\n  \nsrc/main.py\n\n")
        result = collect_modified_files(planspace, section, codespace)
        assert result == ["src/main.py"]

    def test_deduplication(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = self._make_section(planspace)
        report = planspace / "artifacts" / "impl-01-modified.txt"
        report.write_text("src/main.py\nsrc/main.py\n")
        result = collect_modified_files(planspace, section, codespace)
        assert result == ["src/main.py"]


class TestParseAlignmentVerdict:
    def test_inline_json(self) -> None:
        result = '{"frame_ok": true, "aligned": true, "problems": []}'
        verdict = _parse_alignment_verdict(result)
        assert verdict is not None
        assert verdict["frame_ok"] is True
        assert verdict["aligned"] is True

    def test_code_fenced_json(self) -> None:
        result = (
            "Analysis complete.\n"
            "```json\n"
            '{"frame_ok": true, "aligned": false, '
            '"problems": ["Missing error handling"]}\n'
            "```\n"
            "End of analysis."
        )
        verdict = _parse_alignment_verdict(result)
        assert verdict is not None
        assert verdict["aligned"] is False
        assert "Missing error handling" in verdict["problems"]

    def test_no_verdict_returns_none(self) -> None:
        result = "Some general text without any JSON verdict."
        assert _parse_alignment_verdict(result) is None

    def test_json_without_frame_ok_ignored(self) -> None:
        result = '{"aligned": true, "problems": []}'
        assert _parse_alignment_verdict(result) is None

    def test_invalid_frame(self) -> None:
        result = (
            '{"frame_ok": false, "aligned": false, '
            '"problems": ["Invalid frame: feature coverage audit"]}'
        )
        verdict = _parse_alignment_verdict(result)
        assert verdict is not None
        assert verdict["frame_ok"] is False

    def test_json_embedded_in_prose(self) -> None:
        result = (
            "After careful review, the alignment verdict is:\n"
            '{"frame_ok": true, "aligned": true, "problems": []}\n'
            "No further action needed."
        )
        verdict = _parse_alignment_verdict(result)
        assert verdict is not None
        assert verdict["aligned"] is True


class TestExtractProblems:
    def test_aligned_verdict_returns_none(self) -> None:
        result = '{"frame_ok": true, "aligned": true, "problems": []}'
        assert _extract_problems(result, adjudicator_model="glm") is None

    def test_misaligned_verdict_returns_problems(self) -> None:
        result = (
            '{"frame_ok": true, "aligned": false, '
            '"problems": ["auth bypass", "missing validation"]}'
        )
        problems = _extract_problems(result, adjudicator_model="glm")
        assert problems is not None
        assert "auth bypass" in problems
        assert "missing validation" in problems

    def test_misaligned_string_problems(self) -> None:
        result = (
            '{"frame_ok": true, "aligned": false, '
            '"problems": "Single problem description"}'
        )
        problems = _extract_problems(result, adjudicator_model="glm")
        assert problems == "Single problem description"

    def test_misaligned_empty_problems_list(self) -> None:
        """Empty problems list falls through to descriptive message."""
        result = '{"frame_ok": true, "aligned": false, "problems": []}'
        problems = _extract_problems(result, adjudicator_model="glm")
        assert problems is not None
        assert "misaligned" in problems.lower()

    def test_misaligned_no_problems_field(self) -> None:
        """Missing problems field falls through to descriptive message."""
        result = '{"frame_ok": true, "aligned": false}'
        problems = _extract_problems(result, adjudicator_model="glm")
        assert problems is not None
        assert "misaligned" in problems.lower()

    def test_no_verdict_dispatches_adjudicator(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """When no JSON verdict, falls back to GLM adjudicator."""
        output_path = planspace / "artifacts" / "align-output.md"
        output_path.write_text("Some non-JSON alignment output")

        adjudicator_response = json.dumps({
            "aligned": False,
            "problems": ["Adjudicator found divergence"],
            "reason": "output indicates misalignment",
        })

        with patch(
            "staleness.section_alignment.dispatch_agent",
            return_value=adjudicator_response,
        ):
            problems = _extract_problems(
                "Some non-JSON alignment output",
                output_path=output_path,
                planspace=planspace,
                parent="orchestrator",
                codespace=codespace,
                adjudicator_model="glm",
            )
        assert problems is not None
        assert "Adjudicator found divergence" in problems

    def test_no_verdict_adjudicator_says_aligned(
        self, planspace: Path, codespace: Path,
    ) -> None:
        output_path = planspace / "artifacts" / "align-output.md"
        output_path.write_text("Looks good")

        adjudicator_response = json.dumps({
            "aligned": True,
            "problems": [],
            "reason": "output indicates alignment",
        })

        with patch(
            "staleness.section_alignment.dispatch_agent",
            return_value=adjudicator_response,
        ):
            problems = _extract_problems(
                "Looks good",
                output_path=output_path,
                planspace=planspace,
                parent="orchestrator",
                codespace=codespace,
                adjudicator_model="glm",
            )
        assert problems is None

    def test_no_verdict_no_adjudicator_returns_missing(self) -> None:
        """Without output_path/planspace, can't dispatch adjudicator."""
        problems = _extract_problems("No JSON output here", adjudicator_model="glm")
        assert problems is not None
        assert "MISSING_JSON_VERDICT" in problems

    def test_frame_ok_false_returns_problem(self) -> None:
        """P1 regression: frame_ok=false must surface as a problem."""
        result = (
            '{"frame_ok": false, "aligned": false, '
            '"problems": ["Invalid frame: treated as feature coverage audit"]}'
        )
        problems = _extract_problems(result, adjudicator_model="glm")
        assert problems is not None
        assert "feature coverage" in problems.lower() or isinstance(problems, list)
