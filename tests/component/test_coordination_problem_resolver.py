"""Component tests for shared coordination problem helpers."""

from __future__ import annotations

import json

from section_loop.types import Section, SectionResult
from src.scripts.lib.pipelines.coordination_problem_resolver import (
    _collect_outstanding_problems,
    _detect_recurrence_patterns,
    build_file_to_sections,
)


def test_build_file_to_sections_groups_paths_by_section() -> None:
    sections = [
        Section(number="01", path=None, related_files=["src/a.py", "src/shared.py"]),
        Section(number="02", path=None, related_files=["src/shared.py"]),
    ]

    mapping = build_file_to_sections(sections)

    assert mapping == {
        "src/a.py": ["01"],
        "src/shared.py": ["01", "02"],
    }


def test_collect_outstanding_problems_fail_closes_on_malformed_blocker(
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

    problems = _collect_outstanding_problems(
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
    assert problems[0]["type"] == "needs_parent"
    assert problems[0]["needs"] == "Valid blocker signal JSON"
    assert blocker_path.with_suffix(".malformed.json").exists()


def test_collect_outstanding_problems_tracks_notes_and_ack_states(planspace) -> None:
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

    unaddressed = _collect_outstanding_problems(
        {"05": SectionResult(section_number="05", aligned=True)},
        {"05": section},
        planspace,
    )

    assert len(unaddressed) == 1
    assert unaddressed[0]["type"] == "unaddressed_note"
    assert unaddressed[0]["note_id"] == "note-123"

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

    rejected = _collect_outstanding_problems(
        {"05": SectionResult(section_number="05", aligned=True)},
        {"05": section},
        planspace,
    )

    assert len(rejected) == 1
    assert rejected[0]["type"] == "consequence_conflict"
    assert "Conflicts with current API contract." in rejected[0]["description"]


def test_collect_outstanding_problems_surfaces_root_reframing_scope_deltas(
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

    problems = _collect_outstanding_problems(
        {"05": SectionResult(section_number="05", aligned=True)},
        {"05": section},
        planspace,
    )

    assert problems == [
        {
            "section": "05",
            "type": "root_reframing",
            "description": (
                "Pending scope delta delta-root-05 from proposal requires root "
                "reframing: Shared API reframe. Linked sections: 05."
            ),
            "files": ["src/service.py"],
            "delta_id": "delta-root-05",
            "title": "Shared API reframe",
            "source": "proposal",
            "source_sections": ["05"],
            "requires_root_reframing": True,
        },
    ]


def test_detect_recurrence_patterns_writes_report_for_active_problems(
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
        {"section": "01", "type": "misaligned"},
        {"section": "02", "type": "misaligned"},
    ]

    report = _detect_recurrence_patterns(planspace, problems)
    stored = json.loads(
        (
            planspace / "artifacts" / "coordination" / "recurrence.json"
        ).read_text(encoding="utf-8"),
    )

    assert report == stored
    assert report["recurring_sections"] == ["01"]
    assert report["recurring_problem_count"] == 1
    assert report["problem_indices"] == [0]
    assert bad_signal.with_suffix(".malformed.json").exists()
