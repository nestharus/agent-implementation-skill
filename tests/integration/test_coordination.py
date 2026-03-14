"""Integration tests for coordination.py (P2+P4/R19).

P2: Blocker signal detection in _collect_outstanding_problems
P4: Coordination prompt writes to artifact file (no inline JSON)
"""

import json
from pathlib import Path

from coordination.problem_types import MisalignedProblem
from coordination.service.problem_resolver import _collect_outstanding_problems
from coordination.service.planner import write_coordination_plan_prompt
from orchestrator.types import Section, SectionResult


class TestCollectOutstandingProblemsBlockerSignal:
    """P2/R19: _collect_outstanding_problems detects blocker.json and
    routes as needs_parent type (not misaligned)."""

    def test_blocker_signal_routes_as_needs_parent(
        self, planspace: Path,
    ) -> None:
        section = Section(
            number="03",
            path=planspace / "artifacts" / "sections" / "section-03.md",
            related_files=["src/api.py"],
        )
        section.path.write_text("# Section 03\n\nAPI layer.\n")
        sections_by_num = {"03": section}

        # Write blocker signal
        signal_dir = planspace / "artifacts" / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        blocker = {
            "state": "needs_parent",
            "section": "03",
            "detail": "Greenfield — no existing code.",
            "needs": "Parent must provide seed code.",
            "why_blocked": "Cannot produce integration proposal.",
        }
        (signal_dir / "section-03-blocker.json").write_text(
            json.dumps(blocker), encoding="utf-8",
        )

        # Section result is not aligned (has problems)
        results = {
            "03": SectionResult(
                section_number="03",
                aligned=False,
                problems="needs_parent:greenfield",
            ),
        }

        problems = _collect_outstanding_problems(
            results, sections_by_num, planspace,
        )

        assert len(problems) == 1
        assert problems[0].type == "needs_parent"
        assert problems[0].section == "03"
        assert "Greenfield" in problems[0].description
        assert problems[0].needs == "Parent must provide seed code."

    def test_blocker_signal_skips_misaligned_handling(
        self, planspace: Path,
    ) -> None:
        """When a blocker signal is present, the misaligned path should
        NOT produce a separate 'misaligned' problem entry."""
        section = Section(
            number="05",
            path=planspace / "artifacts" / "sections" / "section-05.md",
            related_files=["src/worker.py"],
        )
        section.path.write_text("# Section 05\n\nWorker.\n")
        sections_by_num = {"05": section}

        signal_dir = planspace / "artifacts" / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        blocker = {
            "state": "needs_parent",
            "detail": "No code to integrate with.",
        }
        (signal_dir / "section-05-blocker.json").write_text(
            json.dumps(blocker), encoding="utf-8",
        )

        results = {
            "05": SectionResult(
                section_number="05",
                aligned=False,
                problems="needs_parent:greenfield — section 05 requires research/seed decision",
            ),
        }

        problems = _collect_outstanding_problems(
            results, sections_by_num, planspace,
        )

        # Should only have one problem (needs_parent), not also misaligned
        assert len(problems) == 1
        assert problems[0].type == "needs_parent"
        # Specifically NOT "misaligned"
        types = [p.type for p in problems]
        assert "misaligned" not in types

    def test_no_blocker_signal_falls_through_to_misaligned(
        self, planspace: Path,
    ) -> None:
        """Without a blocker signal, standard misaligned handling applies."""
        section = Section(
            number="02",
            path=planspace / "artifacts" / "sections" / "section-02.md",
            related_files=["src/db.py"],
        )
        section.path.write_text("# Section 02\n\nDatabase layer.\n")
        sections_by_num = {"02": section}

        results = {
            "02": SectionResult(
                section_number="02",
                aligned=False,
                problems="Schema migration is incorrect",
            ),
        }

        problems = _collect_outstanding_problems(
            results, sections_by_num, planspace,
        )

        assert len(problems) == 1
        assert problems[0].type == "misaligned"
        assert "Schema migration" in problems[0].description

    def test_blocker_signal_invalid_state_falls_through(
        self, planspace: Path,
    ) -> None:
        """Blocker signal with state != needs_parent falls through."""
        section = Section(
            number="04",
            path=planspace / "artifacts" / "sections" / "section-04.md",
            related_files=["src/cache.py"],
        )
        section.path.write_text("# Section 04\n\nCache.\n")
        sections_by_num = {"04": section}

        signal_dir = planspace / "artifacts" / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        blocker = {
            "state": "underspecified",  # Not needs_parent
            "detail": "Missing spec details.",
        }
        (signal_dir / "section-04-blocker.json").write_text(
            json.dumps(blocker), encoding="utf-8",
        )

        results = {
            "04": SectionResult(
                section_number="04",
                aligned=False,
                problems="Cache eviction policy unclear",
            ),
        }

        problems = _collect_outstanding_problems(
            results, sections_by_num, planspace,
        )

        # Should fall through to misaligned handling
        assert len(problems) == 1
        assert problems[0].type == "misaligned"


class TestCoordinationPlanPromptArtifactFile:
    """P4/R19: coordination plan prompt writes problems to artifact file
    instead of embedding inline JSON."""

    def test_writes_problems_to_artifact_file(
        self, planspace: Path,
    ) -> None:
        problems = [
            MisalignedProblem(section="01", description="Auth module drift", files=["src/auth.py"]),
            MisalignedProblem(section="02", description="DB schema mismatch", files=["src/db.py"]),
        ]

        write_coordination_plan_prompt(problems, planspace)

        problems_path = (planspace / "artifacts" / "coordination"
                         / "problems.json")
        assert problems_path.exists()
        written = json.loads(problems_path.read_text())
        assert len(written) == 2
        assert written[0]["section"] == "01"
        assert written[1]["section"] == "02"

    def test_prompt_references_artifact_path(
        self, planspace: Path,
    ) -> None:
        problems = [
            MisalignedProblem(section="01", description="drift", files=["f.py"]),
        ]

        prompt_path = write_coordination_plan_prompt(problems, planspace)
        content = prompt_path.read_text()

        # Prompt should reference the artifact file path
        assert "problems.json" in content

    def test_prompt_does_not_embed_problem_json_inline(
        self, planspace: Path,
    ) -> None:
        problems = [
            MisalignedProblem(section="01", description="Auth module drift", files=["src/auth.py"]),
        ]

        prompt_path = write_coordination_plan_prompt(problems, planspace)
        content = prompt_path.read_text()

        # The prompt should NOT contain the actual problem descriptions
        # inline — they should be in the artifact file only
        assert "Auth module drift" not in content
