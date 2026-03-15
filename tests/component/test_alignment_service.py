"""Component tests for shared alignment helpers."""

from __future__ import annotations

from pathlib import Path

from src.staleness.service.alignment_collector import AlignmentCollector, extract_problems
from src.containers import LogService
from src.orchestrator.types import Section


class _NoOpLogger(LogService):
    def log(self, msg: str) -> None:
        pass


def _collect_modified_files(planspace, section, codespace):
    return AlignmentCollector(logger=_NoOpLogger()).collect_modified_files(
        planspace, section, codespace,
    )


def _section(planspace: Path) -> Section:
    return Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
    )


def test_collect_modified_files_normalizes_relative_and_absolute_paths(
    planspace: Path,
    codespace: Path,
) -> None:
    report = planspace / "artifacts" / "impl-01-modified.txt"
    report.write_text(
        "\n".join(
            [
                "src/main.py",
                str(codespace / "src" / "util.py"),
                "src/main.py",
            ],
        ),
        encoding="utf-8",
    )

    result = sorted(
        _collect_modified_files(planspace, _section(planspace), codespace),
    )

    assert result == ["src/main.py", "src/util.py"]


def test_collect_modified_files_rejects_paths_outside_codespace(
    planspace: Path,
    codespace: Path,
) -> None:
    report = planspace / "artifacts" / "impl-01-modified.txt"
    report.write_text("/etc/passwd\nsrc/../../etc/shadow\n", encoding="utf-8")

    result = _collect_modified_files(
        planspace,
        _section(planspace),
        codespace,
    )

    assert result == []


def test_extract_problems_returns_none_for_aligned_verdict() -> None:
    assert extract_problems({"aligned": True, "problems": []}) is None


def test_extract_problems_formats_problem_lists_and_strings() -> None:
    assert extract_problems({"aligned": False, "problems": ["a", "b"]}) == "a\nb"
    assert extract_problems({"aligned": False, "problems": "single"}) == "single"


def test_extract_problems_supplies_default_message_when_details_missing() -> None:
    problems = extract_problems({"aligned": False, "problems": []})

    assert problems == "Alignment judge reported misaligned (no details in verdict)"
