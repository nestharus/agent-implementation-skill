"""Component tests for shared coordination problem helpers."""

from __future__ import annotations

import json

from containers import Services
from coordination.problem_types import MisalignedProblem, Problem
from coordination.service.problem_resolver import ProblemResolver
from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section, SectionResult
from signals.types import SIGNAL_NEEDS_PARENT


def _make_resolver() -> ProblemResolver:
    return ProblemResolver(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        logger=Services.logger(),
        signals=Services.signals(),
    )


# ---------------------------------------------------------------------------
# collect_outstanding_problems — blocker signals
# ---------------------------------------------------------------------------


def test_collect_outstanding_problems_with_blocker_signal(planspace) -> None:
    """A valid needs_parent blocker signal is surfaced as a BlockerProblem."""
    section = Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
        related_files=["src/auth.py"],
    )
    section.path.write_text("# Section 01\n", encoding="utf-8")

    paths = PathRegistry(planspace)
    blocker_path = paths.blocker_signal("01")
    blocker_path.parent.mkdir(parents=True, exist_ok=True)
    blocker_path.write_text(
        json.dumps({
            "state": SIGNAL_NEEDS_PARENT,
            "detail": "Missing parent config",
            "needs": "Parent configuration value",
        }),
        encoding="utf-8",
    )

    resolver = _make_resolver()
    problems = resolver.collect_outstanding_problems(
        {"01": SectionResult(section_number="01", aligned=False)},
        {"01": section},
        planspace,
    )

    assert len(problems) == 1
    p = problems[0]
    assert p.type == "needs_parent"
    assert p.needs == "Parent configuration value"
    assert "Missing parent config" in p.description
    assert p.section == "01"
    assert p.files == ["src/auth.py"]


# ---------------------------------------------------------------------------
# collect_outstanding_problems — consequence notes
# ---------------------------------------------------------------------------


def test_collect_outstanding_problems_with_consequence_notes(planspace) -> None:
    """An unacknowledged consequence note becomes an unaddressed_note problem."""
    section = Section(
        number="04",
        path=planspace / "artifacts" / "sections" / "section-04.md",
        related_files=["src/handler.py"],
    )
    section.path.write_text("# Section 04\n", encoding="utf-8")

    notes_dir = planspace / "artifacts" / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "from-01-to-04.md").write_text(
        "**Note ID**: `note-abc`\n\nPlease reconcile shared config.\n",
        encoding="utf-8",
    )

    resolver = _make_resolver()
    problems = resolver.collect_outstanding_problems(
        {"04": SectionResult(section_number="04", aligned=True)},
        {"04": section},
        planspace,
    )

    assert len(problems) == 1
    p = problems[0]
    assert p.type == "unaddressed_note"
    assert p.note_id == "note-abc"
    assert p.section == "04"


# ---------------------------------------------------------------------------
# collect_outstanding_problems — scope deltas
# ---------------------------------------------------------------------------


def test_collect_outstanding_problems_with_scope_deltas(planspace) -> None:
    """A root-reframing scope delta is surfaced as a ScopeDeltaProblem."""
    section = Section(
        number="02",
        path=planspace / "artifacts" / "sections" / "section-02.md",
        related_files=["src/db.py"],
    )
    section.path.write_text("# Section 02\n", encoding="utf-8")

    paths = PathRegistry(planspace)
    scope_deltas_dir = paths.scope_deltas_dir()
    scope_deltas_dir.mkdir(parents=True, exist_ok=True)
    (scope_deltas_dir / "section-02-scope-delta.json").write_text(
        json.dumps({
            "delta_id": "delta-002",
            "title": "Database schema change",
            "source": "implementation",
            "source_sections": ["02"],
            "requires_root_reframing": True,
        }),
        encoding="utf-8",
    )

    resolver = _make_resolver()
    problems = resolver.collect_outstanding_problems(
        {"02": SectionResult(section_number="02", aligned=True)},
        {"02": section},
        planspace,
    )

    assert len(problems) == 1
    p = problems[0]
    assert p.type == "root_reframing"
    assert p.delta_id == "delta-002"
    assert p.title == "Database schema change"
    assert p.source == "implementation"
    assert p.source_sections == ["02"]
    assert p.section == "02"
    assert p.files == ["src/db.py"]
    assert "requires root reframing" in p.description


# ---------------------------------------------------------------------------
# collect_outstanding_problems — empty planspace
# ---------------------------------------------------------------------------


def test_collect_outstanding_problems_empty_planspace(planspace) -> None:
    """No artifacts seeded — returns an empty list."""
    section = Section(
        number="07",
        path=planspace / "artifacts" / "sections" / "section-07.md",
        related_files=["src/empty.py"],
    )
    section.path.write_text("# Section 07\n", encoding="utf-8")

    resolver = _make_resolver()
    problems = resolver.collect_outstanding_problems(
        {"07": SectionResult(section_number="07", aligned=True)},
        {"07": section},
        planspace,
    )

    assert problems == []


# ---------------------------------------------------------------------------
# collect_outstanding_problems — malformed blocker
# ---------------------------------------------------------------------------


def test_collect_outstanding_problems_malformed_blocker(planspace) -> None:
    """A malformed blocker JSON is handled gracefully, renamed, and surfaced."""
    section = Section(
        number="06",
        path=planspace / "artifacts" / "sections" / "section-06.md",
        related_files=["src/broken.py"],
    )
    section.path.write_text("# Section 06\n", encoding="utf-8")

    paths = PathRegistry(planspace)
    blocker_path = paths.blocker_signal("06")
    blocker_path.parent.mkdir(parents=True, exist_ok=True)
    blocker_path.write_text("{bad json", encoding="utf-8")

    resolver = _make_resolver()
    problems = resolver.collect_outstanding_problems(
        {"06": SectionResult(section_number="06", aligned=False, problems="some issue")},
        {"06": section},
        planspace,
    )

    # Should still produce a problem (needs_parent for manual repair)
    assert len(problems) == 1
    assert problems[0].type == "needs_parent"
    assert problems[0].needs == "Valid blocker signal JSON"
    # Malformed file should be renamed
    assert blocker_path.with_suffix(".malformed.json").exists()


# ---------------------------------------------------------------------------
# detect_recurrence_patterns
# ---------------------------------------------------------------------------


def test_detect_recurrence_patterns(planspace) -> None:
    """Recurrence signals are detected and reported for active problems."""
    signals_dir = planspace / "artifacts" / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    (signals_dir / "section-03-recurrence.json").write_text(
        json.dumps({"section": "03", "recurring": True, "attempt": 2}),
        encoding="utf-8",
    )
    (signals_dir / "section-04-recurrence.json").write_text(
        json.dumps({"section": "04", "recurring": True, "attempt": 5}),
        encoding="utf-8",
    )

    problems = [
        MisalignedProblem(section="03", description="misaligned"),
        MisalignedProblem(section="04", description="also misaligned"),
        MisalignedProblem(section="99", description="unrelated"),
    ]

    resolver = _make_resolver()
    report = resolver.detect_recurrence_patterns(planspace, problems)

    assert report is not None
    assert sorted(report.recurring_sections) == ["03", "04"]
    assert report.recurring_problem_count == 2
    assert report.max_attempt == 5
    assert report.problem_indices == [0, 1]

    # Report should also be persisted to disk
    recurrence_path = planspace / "artifacts" / "coordination" / "recurrence.json"
    assert recurrence_path.exists()
    stored = json.loads(recurrence_path.read_text(encoding="utf-8"))
    assert stored == report.to_dict()


def test_detect_recurrence_patterns_empty(planspace) -> None:
    """No recurrence signals → returns None."""
    problems = [
        MisalignedProblem(section="01", description="misaligned"),
    ]

    resolver = _make_resolver()
    report = resolver.detect_recurrence_patterns(planspace, problems)

    assert report is None


def testcollect_outstanding_problems_fail_closes_on_malformed_blocker(
    planspace,
) -> None:
    section = Section(
        number="03",
        path=planspace / "artifacts" / "sections" / "section-03.md",
        related_files=["src/api.py"],
    )
    section.path.write_text("# Section 03\n", encoding="utf-8")
    blocker_path = planspace / "artifacts" / "signals" / "section-03-blocker.json"
    blocker_path.write_text("{bad json", encoding="utf-8")

    resolver = _make_resolver()
    problems = resolver.collect_outstanding_problems(
        {
            "03": SectionResult(
                section_number="03",
                aligned=False,
                problems="normal misalignment that should be suppressed",
            ),
        },
        {"03": section},
        planspace,
    )

    assert len(problems) == 1
    assert problems[0].type == "needs_parent"
    assert problems[0].needs == "Valid blocker signal JSON"
    assert blocker_path.with_suffix(".malformed.json").exists()


def testcollect_outstanding_problems_tracks_notes_and_ack_states(planspace) -> None:
    section = Section(
        number="05",
        path=planspace / "artifacts" / "sections" / "section-05.md",
        related_files=["src/service.py"],
    )
    section.path.write_text("# Section 05\n", encoding="utf-8")
    notes_dir = planspace / "artifacts" / "notes"
    note_path = notes_dir / "from-02-to-05.md"
    note_path.write_text(
        "**Note ID**: `note-123`\n\nPlease reconcile shared API.\n",
        encoding="utf-8",
    )

    resolver = _make_resolver()
    unaddressed = resolver.collect_outstanding_problems(
        {"05": SectionResult(section_number="05", aligned=True)},
        {"05": section},
        planspace,
    )

    assert len(unaddressed) == 1
    assert unaddressed[0].type == "unaddressed_note"
    assert unaddressed[0].note_id == "note-123"

    ack_path = planspace / "artifacts" / "signals" / "note-ack-05.json"
    ack_path.write_text(
        json.dumps(
            {
                "acknowledged": [
                    {
                        "note_id": "note-123",
                        "action": "rejected",
                        "reason": "Conflicts with current API contract.",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    rejected = resolver.collect_outstanding_problems(
        {"05": SectionResult(section_number="05", aligned=True)},
        {"05": section},
        planspace,
    )

    assert len(rejected) == 1
    assert rejected[0].type == "consequence_conflict"
    assert "Conflicts with current API contract." in rejected[0].description


def testcollect_outstanding_problems_surfaces_root_reframing_scope_deltas(
    planspace,
) -> None:
    section = Section(
        number="05",
        path=planspace / "artifacts" / "sections" / "section-05.md",
        related_files=["src/service.py"],
    )
    section.path.write_text("# Section 05\n", encoding="utf-8")
    scope_deltas_dir = planspace / "artifacts" / "scope-deltas"
    scope_deltas_dir.mkdir(parents=True, exist_ok=True)
    (scope_deltas_dir / "section-05-scope-delta.json").write_text(
        json.dumps(
            {
                "delta_id": "delta-root-05",
                "title": "Shared API reframe",
                "source": "proposal",
                "source_sections": ["05"],
                "requires_root_reframing": True,
            },
        ),
        encoding="utf-8",
    )

    resolver = _make_resolver()
    problems = resolver.collect_outstanding_problems(
        {"05": SectionResult(section_number="05", aligned=True)},
        {"05": section},
        planspace,
    )

    assert len(problems) == 1
    p = problems[0]
    assert p.type == "root_reframing"
    assert p.section == "05"
    assert p.files == ["src/service.py"]
    assert p.delta_id == "delta-root-05"
    assert p.title == "Shared API reframe"
    assert p.source == "proposal"
    assert p.source_sections == ["05"]
    assert "requires root reframing" in p.description


def testdetect_recurrence_patterns_writes_report_for_active_problems(
    planspace,
) -> None:
    signals_dir = planspace / "artifacts" / "signals"
    (signals_dir / "section-01-recurrence.json").write_text(
        json.dumps({"section": "01", "recurring": True, "attempt": 3}),
        encoding="utf-8",
    )
    bad_signal = signals_dir / "section-09-recurrence.json"
    bad_signal.write_text("{bad json", encoding="utf-8")
    problems = [
        MisalignedProblem(section="01", description=""),
        MisalignedProblem(section="02", description=""),
    ]

    resolver = _make_resolver()
    report = resolver.detect_recurrence_patterns(planspace, problems)
    stored = json.loads(
        (
            planspace / "artifacts" / "coordination" / "recurrence.json"
        ).read_text(encoding="utf-8"),
    )

    assert report.to_dict() == stored
    assert report.recurring_sections == ["01"]
    assert report.recurring_problem_count == 1
    assert report.problem_indices == [0]
    assert bad_signal.with_suffix(".malformed.json").exists()
