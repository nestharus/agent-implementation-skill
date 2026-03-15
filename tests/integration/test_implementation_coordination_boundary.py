"""Integration tests for the Implementation -> Coordination boundary.

After implementation produces scope-delta artifacts and consequence notes,
coordination's ProblemResolver discovers them and routes them correctly.

Mock boundary: only LogService (to suppress console output) is stubbed.
Everything else — file I/O, PathRegistry, artifact reading — runs for real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from containers import ArtifactIOService, LogService, SignalReader
from conftest import NoOpCommunicator
from coordination.problem_types import (
    ConflictProblem,
    NegotiationProblem,
    ScopeDeltaProblem,
    UnaddressedNoteProblem,
)
from coordination.repository.notes import write_consequence_note
from coordination.service.problem_resolver import ProblemResolver
from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section, SectionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _QuietLogger(LogService):
    """Logger that captures messages without printing."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def log(self, msg: str) -> None:
        self.messages.append(msg)

    def log_lifecycle(self, planspace, event: str, status: str) -> None:
        pass


def _make_resolver() -> tuple[ProblemResolver, _QuietLogger]:
    """Construct a ProblemResolver with real artifact I/O."""
    logger = _QuietLogger()
    return ProblemResolver(
        artifact_io=ArtifactIOService(),
        communicator=NoOpCommunicator(),
        logger=logger,
        signals=SignalReader(),
    ), logger


def _make_section(
    planspace: Path,
    number: str = "01",
    *,
    related_files: list[str] | None = None,
) -> Section:
    """Create a Section with minimal planspace artifacts."""
    paths = PathRegistry(planspace)
    sec_path = paths.sections_dir() / f"section-{number}.md"
    sec_path.write_text(
        f"# Section {number}: Feature {number}\n\nImplement feature {number}.\n",
        encoding="utf-8",
    )
    return Section(
        number=number,
        path=sec_path,
        related_files=related_files or [f"src/feature_{number}.py"],
    )


def _write_scope_delta(
    planspace: Path,
    delta: dict,
    *,
    filename: str | None = None,
) -> Path:
    """Write a scope-delta JSON artifact at the canonical PathRegistry location."""
    paths = PathRegistry(planspace)
    scope_deltas_dir = paths.scope_deltas_dir()
    scope_deltas_dir.mkdir(parents=True, exist_ok=True)
    if filename:
        delta_path = scope_deltas_dir / filename
    else:
        section = delta.get("section", "00")
        delta_path = paths.scope_delta_section(section)
    delta_path.write_text(json.dumps(delta, indent=2), encoding="utf-8")
    return delta_path


def _aligned_result(num: str) -> SectionResult:
    return SectionResult(section_number=num, aligned=True)


def _misaligned_result(num: str, problems: str = "drift") -> SectionResult:
    return SectionResult(section_number=num, aligned=False, problems=problems)


# ---------------------------------------------------------------------------
# 1. Scope delta discovery
# ---------------------------------------------------------------------------

class TestImplementationToCoordinationScopeDeltas:
    """ProblemResolver discovers scope-delta artifacts written by
    the implementation layer's ScopeDeltaAggregator."""

    def test_discovers_single_scope_delta_requiring_reframing(
        self, planspace: Path,
    ) -> None:
        """A scope delta with requires_root_reframing=True is collected."""
        sec = _make_section(planspace, "03")
        sections_by_num = {"03": sec}
        section_results: dict[str, SectionResult] = {
            "03": _aligned_result("03"),
        }

        _write_scope_delta(planspace, {
            "delta_id": "delta-03-oos-auth",
            "title": "Out-of-scope auth module",
            "source": "implementation-03",
            "section": "03",
            "source_sections": ["03"],
            "requires_root_reframing": True,
            "adjudicated": False,
        })

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        assert len(scope_problems) == 1
        assert scope_problems[0].delta_id == "delta-03-oos-auth"
        assert scope_problems[0].section == "03"
        assert scope_problems[0].source == "implementation-03"
        assert scope_problems[0].title == "Out-of-scope auth module"
        assert "03" in scope_problems[0].source_sections

    def test_skips_adjudicated_scope_delta(self, planspace: Path) -> None:
        """Already-adjudicated deltas are not collected as problems."""
        sec = _make_section(planspace, "01")
        sections_by_num = {"01": sec}
        section_results = {"01": _aligned_result("01")}

        _write_scope_delta(planspace, {
            "delta_id": "delta-01-done",
            "title": "Already handled",
            "source": "implementation-01",
            "section": "01",
            "source_sections": ["01"],
            "requires_root_reframing": True,
            "adjudicated": True,
        })

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        assert scope_problems == []

    def test_skips_delta_without_root_reframing(self, planspace: Path) -> None:
        """Deltas with requires_root_reframing=False are not collected."""
        sec = _make_section(planspace, "02")
        sections_by_num = {"02": sec}
        section_results = {"02": _aligned_result("02")}

        _write_scope_delta(planspace, {
            "delta_id": "delta-02-minor",
            "title": "Minor scope tweak",
            "source": "implementation-02",
            "section": "02",
            "source_sections": ["02"],
            "requires_root_reframing": False,
            "adjudicated": False,
        })

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        assert scope_problems == []

    def test_multi_section_scope_delta_creates_problem_per_section(
        self, planspace: Path,
    ) -> None:
        """A delta linking multiple sections creates one problem per section."""
        sec1 = _make_section(planspace, "01")
        sec2 = _make_section(planspace, "04")
        sections_by_num = {"01": sec1, "04": sec2}
        section_results = {
            "01": _aligned_result("01"),
            "04": _aligned_result("04"),
        }

        _write_scope_delta(planspace, {
            "delta_id": "delta-cross-01-04",
            "title": "Cross-cutting concern",
            "source": "implementation-01",
            "source_sections": ["01", "04"],
            "requires_root_reframing": True,
            "adjudicated": False,
        }, filename="cross-01-04-scope-delta.json")

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        assert len(scope_problems) == 2
        affected_sections = sorted(p.section for p in scope_problems)
        assert affected_sections == ["01", "04"]
        # Both problems share the same delta_id
        assert all(p.delta_id == "delta-cross-01-04" for p in scope_problems)
        # Both problems list both linked sections
        assert all(
            sorted(p.source_sections) == ["01", "04"]
            for p in scope_problems
        )

    def test_scope_delta_files_include_related_files(
        self, planspace: Path,
    ) -> None:
        """Scope-delta problems carry the section's related_files."""
        sec = _make_section(
            planspace, "05",
            related_files=["src/api.py", "src/routes.py"],
        )
        sections_by_num = {"05": sec}
        section_results = {"05": _aligned_result("05")}

        _write_scope_delta(planspace, {
            "delta_id": "delta-05-api",
            "title": "API boundary change",
            "source": "implementation-05",
            "section": "05",
            "source_sections": ["05"],
            "requires_root_reframing": True,
            "adjudicated": False,
        })

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        assert len(scope_problems) == 1
        assert sorted(scope_problems[0].files) == ["src/api.py", "src/routes.py"]

    def test_empty_scope_deltas_dir_produces_no_problems(
        self, planspace: Path,
    ) -> None:
        """When no scope-delta files exist, no problems are returned."""
        sec = _make_section(planspace, "01")
        sections_by_num = {"01": sec}
        section_results = {"01": _aligned_result("01")}

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        assert scope_problems == []

    def test_delta_with_section_fallback_when_no_source_sections(
        self, planspace: Path,
    ) -> None:
        """When source_sections is empty, falls back to the 'section' field."""
        sec = _make_section(planspace, "07")
        sections_by_num = {"07": sec}
        section_results = {"07": _aligned_result("07")}

        _write_scope_delta(planspace, {
            "delta_id": "delta-07-fallback",
            "title": "Fallback test",
            "source": "implementation-07",
            "section": "07",
            "source_sections": [],
            "requires_root_reframing": True,
            "adjudicated": False,
        })

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        assert len(scope_problems) == 1
        assert scope_problems[0].section == "07"

    def test_malformed_scope_delta_logged_but_not_collected(
        self, planspace: Path,
    ) -> None:
        """A non-dict scope-delta is renamed to .malformed.json and skipped."""
        sec = _make_section(planspace, "01")
        sections_by_num = {"01": sec}
        section_results = {"01": _aligned_result("01")}

        paths = PathRegistry(planspace)
        sd_dir = paths.scope_deltas_dir()
        sd_dir.mkdir(parents=True, exist_ok=True)
        bad_path = sd_dir / "bad-delta.json"
        bad_path.write_text('"just a string"', encoding="utf-8")

        resolver, logger = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        assert scope_problems == []
        # The malformed file should have been renamed
        assert bad_path.with_suffix(".malformed.json").exists()
        # A warning should have been logged
        assert any("malformed" in m.lower() or "invalid" in m.lower() for m in logger.messages)


# ---------------------------------------------------------------------------
# 2. Consequence note discovery
# ---------------------------------------------------------------------------

class TestImplementationToCoordinationNotes:
    """ProblemResolver discovers consequence notes written by the
    implementation layer's cross-section service."""

    def test_unaddressed_note_becomes_problem(self, planspace: Path) -> None:
        """A note with no ack signal is collected as UnaddressedNoteProblem."""
        sec_from = _make_section(planspace, "01")
        sec_to = _make_section(planspace, "02")
        sections_by_num = {"01": sec_from, "02": sec_to}
        # Target section must be aligned for note problems to be collected
        section_results = {
            "01": _aligned_result("01"),
            "02": _aligned_result("02"),
        }

        note_content = (
            "# Consequence Note\n\n"
            "**Note ID**: `note-01-to-02-auth`\n\n"
            "Section 01 changed the auth interface.\n"
        )
        write_consequence_note(planspace, "01", "02", note_content)

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        note_problems = [p for p in problems if isinstance(p, UnaddressedNoteProblem)]
        assert len(note_problems) == 1
        assert note_problems[0].section == "02"
        assert note_problems[0].note_id == "note-01-to-02-auth"

    def test_accepted_note_not_collected(self, planspace: Path) -> None:
        """A note that was acknowledged as 'accepted' is skipped."""
        sec_from = _make_section(planspace, "01")
        sec_to = _make_section(planspace, "02")
        sections_by_num = {"01": sec_from, "02": sec_to}
        section_results = {
            "01": _aligned_result("01"),
            "02": _aligned_result("02"),
        }

        note_content = (
            "# Consequence Note\n\n"
            "**Note ID**: `note-01-to-02-ok`\n\n"
            "Section 01 refactored helper.\n"
        )
        write_consequence_note(planspace, "01", "02", note_content)

        # Write an ack signal for the target section
        paths = PathRegistry(planspace)
        ack_path = paths.note_ack_signal("02")
        ack_path.parent.mkdir(parents=True, exist_ok=True)
        ack_data = {
            "acknowledged": [
                {"note_id": "note-01-to-02-ok", "action": "accepted"},
            ],
        }
        ack_path.write_text(json.dumps(ack_data), encoding="utf-8")

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        note_problems = [
            p for p in problems
            if isinstance(p, (UnaddressedNoteProblem, ConflictProblem, NegotiationProblem))
        ]
        assert note_problems == []

    def test_rejected_note_becomes_conflict_problem(
        self, planspace: Path,
    ) -> None:
        """A rejected note is collected as ConflictProblem."""
        sec_from = _make_section(planspace, "03")
        sec_to = _make_section(planspace, "04")
        sections_by_num = {"03": sec_from, "04": sec_to}
        section_results = {
            "03": _aligned_result("03"),
            "04": _aligned_result("04"),
        }

        note_content = (
            "# Consequence Note\n\n"
            "**Note ID**: `note-03-to-04-rejected`\n\n"
            "Section 03 wants to change API contract.\n"
        )
        write_consequence_note(planspace, "03", "04", note_content)

        paths = PathRegistry(planspace)
        ack_path = paths.note_ack_signal("04")
        ack_path.parent.mkdir(parents=True, exist_ok=True)
        ack_data = {
            "acknowledged": [
                {
                    "note_id": "note-03-to-04-rejected",
                    "action": "rejected",
                    "reason": "Breaks backward compatibility",
                },
            ],
        }
        ack_path.write_text(json.dumps(ack_data), encoding="utf-8")

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        conflict_problems = [p for p in problems if isinstance(p, ConflictProblem)]
        assert len(conflict_problems) == 1
        assert conflict_problems[0].section == "04"
        assert conflict_problems[0].note_id == "note-03-to-04-rejected"
        assert "Breaks backward compatibility" in conflict_problems[0].description

    def test_deferred_note_becomes_negotiation_problem(
        self, planspace: Path,
    ) -> None:
        """A deferred note is collected as NegotiationProblem."""
        sec_from = _make_section(planspace, "05")
        sec_to = _make_section(planspace, "06")
        sections_by_num = {"05": sec_from, "06": sec_to}
        section_results = {
            "05": _aligned_result("05"),
            "06": _aligned_result("06"),
        }

        note_content = (
            "# Consequence Note\n\n"
            "**Note ID**: `note-05-to-06-deferred`\n\n"
            "Section 05 needs database schema change.\n"
        )
        write_consequence_note(planspace, "05", "06", note_content)

        paths = PathRegistry(planspace)
        ack_path = paths.note_ack_signal("06")
        ack_path.parent.mkdir(parents=True, exist_ok=True)
        ack_data = {
            "acknowledged": [
                {
                    "note_id": "note-05-to-06-deferred",
                    "action": "deferred",
                    "reason": "Waiting for migration framework",
                },
            ],
        }
        ack_path.write_text(json.dumps(ack_data), encoding="utf-8")

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        negotiation_problems = [p for p in problems if isinstance(p, NegotiationProblem)]
        assert len(negotiation_problems) == 1
        assert negotiation_problems[0].section == "06"
        assert negotiation_problems[0].note_id == "note-05-to-06-deferred"
        assert "Waiting for migration framework" in negotiation_problems[0].description

    def test_note_to_misaligned_section_not_collected(
        self, planspace: Path,
    ) -> None:
        """Notes targeting a misaligned section are skipped."""
        sec_from = _make_section(planspace, "01")
        sec_to = _make_section(planspace, "02")
        sections_by_num = {"01": sec_from, "02": sec_to}
        section_results = {
            "01": _aligned_result("01"),
            "02": _misaligned_result("02"),
        }

        note_content = (
            "# Consequence Note\n\n"
            "**Note ID**: `note-01-to-02-skip`\n\n"
            "Should be ignored because section 02 is misaligned.\n"
        )
        write_consequence_note(planspace, "01", "02", note_content)

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        note_problems = [
            p for p in problems
            if isinstance(p, (UnaddressedNoteProblem, ConflictProblem, NegotiationProblem))
        ]
        assert note_problems == []


# ---------------------------------------------------------------------------
# 3. Combined: scope deltas + notes in one collection pass
# ---------------------------------------------------------------------------

class TestCombinedScopeDeltaAndNoteDiscovery:
    """ProblemResolver aggregates both scope deltas and notes in a single
    pass through collect_outstanding_problems."""

    def test_both_scope_delta_and_note_collected(
        self, planspace: Path,
    ) -> None:
        """A planspace with both scope deltas and notes yields both types."""
        sec1 = _make_section(planspace, "01")
        sec2 = _make_section(planspace, "02")
        sections_by_num = {"01": sec1, "02": sec2}
        section_results = {
            "01": _aligned_result("01"),
            "02": _aligned_result("02"),
        }

        # Scope delta from section 01
        _write_scope_delta(planspace, {
            "delta_id": "delta-01-oos",
            "title": "Out-of-scope work",
            "source": "implementation-01",
            "section": "01",
            "source_sections": ["01"],
            "requires_root_reframing": True,
            "adjudicated": False,
        })

        # Consequence note from 01 to 02
        note_content = (
            "# Consequence Note\n\n"
            "**Note ID**: `note-01-to-02-combined`\n\n"
            "Section 01 changed shared interface.\n"
        )
        write_consequence_note(planspace, "01", "02", note_content)

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        note_problems = [p for p in problems if isinstance(p, UnaddressedNoteProblem)]
        assert len(scope_problems) == 1
        assert len(note_problems) == 1
        assert scope_problems[0].delta_id == "delta-01-oos"
        assert note_problems[0].note_id == "note-01-to-02-combined"

    def test_multiple_deltas_across_sections(self, planspace: Path) -> None:
        """Multiple scope deltas from different sections are all collected."""
        sec1 = _make_section(planspace, "01")
        sec2 = _make_section(planspace, "02")
        sec3 = _make_section(planspace, "03")
        sections_by_num = {"01": sec1, "02": sec2, "03": sec3}
        section_results = {
            "01": _aligned_result("01"),
            "02": _aligned_result("02"),
            "03": _aligned_result("03"),
        }

        _write_scope_delta(planspace, {
            "delta_id": "delta-01-first",
            "title": "First OOS",
            "source": "implementation-01",
            "section": "01",
            "source_sections": ["01"],
            "requires_root_reframing": True,
            "adjudicated": False,
        })
        _write_scope_delta(planspace, {
            "delta_id": "delta-02-second",
            "title": "Second OOS",
            "source": "implementation-02",
            "section": "02",
            "source_sections": ["02"],
            "requires_root_reframing": True,
            "adjudicated": False,
        })

        resolver, _ = _make_resolver()
        problems = resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        scope_problems = [p for p in problems if isinstance(p, ScopeDeltaProblem)]
        delta_ids = sorted(p.delta_id for p in scope_problems)
        assert delta_ids == ["delta-01-first", "delta-02-second"]
