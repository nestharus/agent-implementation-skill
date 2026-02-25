"""Tests for the intent layer: triage, bootstrap, surfaces, expansion, runner integration.

Mock boundary: only ``dispatch_agent`` (the LLM call) is mocked.
Everything else — file I/O, JSON parsing, registry logic — runs for real.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from section_loop.intent.surfaces import (
    load_surface_registry,
    merge_surfaces_into_registry,
    save_surface_registry,
    surfaces_are_diminishing,
    mark_surfaces_applied,
    mark_surfaces_discarded,
)
from section_loop.intent.triage import _lightweight_default
from section_loop.types import Section


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def intent_planspace(planspace: Path) -> Path:
    """Extend standard planspace with intent layer directories."""
    artifacts = planspace / "artifacts"
    (artifacts / "intent" / "global").mkdir(parents=True)
    (artifacts / "intent" / "sections" / "section-01").mkdir(parents=True)
    return planspace


def _make_intent_section(planspace: Path, codespace: Path) -> Section:
    """Create a section with all prerequisites for intent testing."""
    sec = Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
        related_files=["src/main.py", "src/utils.py", "src/api.py",
                        "src/db.py", "src/auth.py"],
    )
    sec.global_proposal_path = planspace / "artifacts" / "global-proposal.md"
    sec.global_alignment_path = planspace / "artifacts" / "global-alignment.md"
    sec.global_proposal_path.write_text("# Global Proposal\nAll sections...")
    sec.global_alignment_path.write_text("# Global Alignment\nConstraints...")
    sec.path.write_text("# Section 01\n\nAuthentication + API refactor.\n")
    sections_dir = planspace / "artifacts" / "sections"
    (sections_dir / "section-01-proposal-excerpt.md").write_text(
        "Refactor authentication to use OAuth2 with migration path")
    (sections_dir / "section-01-alignment-excerpt.md").write_text(
        "Must preserve backward compat, no new deps without justification")
    (sections_dir / "section-01-problem-frame.md").write_text(
        "# Problem Statement\nAuth refactor.\n# Evidence\nLegacy.\n"
        "# Constraints\nBackward compat.\n# Success Criteria\nOAuth2.\n"
        "# Out of Scope\nUI changes.\n")
    return sec


# ---------------------------------------------------------------------------
# Surface Registry Tests
# ---------------------------------------------------------------------------

class TestSurfaceRegistry:
    """Core surface registry logic: merge, dedup, diminishing returns."""

    def test_empty_registry_loads_default(
        self, intent_planspace: Path,
    ) -> None:
        registry = load_surface_registry("99", intent_planspace)
        assert registry["next_id"] == 1
        assert registry["surfaces"] == []

    def test_merge_adds_new_surfaces(self) -> None:
        registry = {"section": "01", "next_id": 1, "surfaces": []}
        surfaces = {
            "stage": "integration_proposal",
            "attempt": 1,
            "problem_surfaces": [
                {"id": "P-01-0001", "kind": "emergent", "axis_id": "A3",
                 "title": "Missing migration path"},
            ],
            "philosophy_surfaces": [],
        }
        new, dupes = merge_surfaces_into_registry(registry, surfaces)
        assert len(new) == 1
        assert len(dupes) == 0
        assert registry["surfaces"][0]["id"] == "P-01-0001"
        assert registry["surfaces"][0]["status"] == "pending"

    def test_merge_detects_duplicates(self) -> None:
        registry = {
            "section": "01", "next_id": 2,
            "surfaces": [
                {"id": "P-01-0001", "kind": "emergent", "axis_id": "A3",
                 "status": "applied",
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1}},
            ],
        }
        surfaces = {
            "stage": "integration_proposal", "attempt": 2,
            "problem_surfaces": [
                {"id": "P-01-0001", "kind": "emergent"},
            ],
            "philosophy_surfaces": [],
        }
        new, dupes = merge_surfaces_into_registry(registry, surfaces)
        assert len(new) == 0
        assert dupes == ["P-01-0001"]
        # last_seen should be updated
        assert registry["surfaces"][0]["last_seen"]["attempt"] == 2

    def test_diminishing_returns_no_surfaces(self) -> None:
        """No surfaces at all = diminishing."""
        registry = {"surfaces": []}
        assert surfaces_are_diminishing(registry, [], []) is True

    def test_diminishing_returns_mostly_discarded_dupes(self) -> None:
        """Over 60% discarded duplicates = diminishing."""
        registry = {
            "surfaces": [
                {"id": "P-01-0001", "status": "discarded"},
                {"id": "P-01-0002", "status": "discarded"},
                {"id": "P-01-0003", "status": "discarded"},
            ],
        }
        new = [{"id": "P-01-0004"}]
        dupes = ["P-01-0001", "P-01-0002", "P-01-0003"]
        # 3 discarded dupes / 4 total = 75% > 60%
        assert surfaces_are_diminishing(registry, new, dupes) is True

    def test_not_diminishing_with_fresh_surfaces(self) -> None:
        """Mostly new surfaces = not diminishing."""
        registry = {"surfaces": []}
        new = [{"id": "P-01-0004"}, {"id": "P-01-0005"},
               {"id": "P-01-0006"}]
        dupes = ["P-01-0001"]
        assert surfaces_are_diminishing(registry, new, dupes) is False

    def test_mark_applied_and_discarded(self) -> None:
        registry = {
            "surfaces": [
                {"id": "P-01-0001", "status": "pending"},
                {"id": "P-01-0002", "status": "pending"},
            ],
        }
        mark_surfaces_applied(registry, ["P-01-0001"])
        mark_surfaces_discarded(registry, ["P-01-0002"])
        assert registry["surfaces"][0]["status"] == "applied"
        assert registry["surfaces"][1]["status"] == "discarded"

    def test_save_and_reload_registry(self, intent_planspace: Path) -> None:
        registry = {
            "section": "01", "next_id": 3,
            "surfaces": [
                {"id": "P-01-0001", "status": "applied"},
            ],
        }
        save_surface_registry("01", intent_planspace, registry)
        loaded = load_surface_registry("01", intent_planspace)
        assert loaded["next_id"] == 3
        assert loaded["surfaces"][0]["id"] == "P-01-0001"

    def test_malformed_registry_preserved(self, intent_planspace: Path) -> None:
        """Malformed registry is renamed and fresh default returned."""
        registry_path = (
            intent_planspace / "artifacts" / "intent" / "sections"
            / "section-01" / "surface-registry.json"
        )
        registry_path.write_text("not json!", encoding="utf-8")
        loaded = load_surface_registry("01", intent_planspace)
        assert loaded["surfaces"] == []
        assert registry_path.with_suffix(".malformed.json").exists()


# ---------------------------------------------------------------------------
# Triage Tests
# ---------------------------------------------------------------------------

class TestIntentTriage:
    """Intent triage dispatches GLM and returns mode + budgets."""

    def test_lightweight_default(self) -> None:
        result = _lightweight_default("01")
        assert result["intent_mode"] == "lightweight"
        assert result["budgets"]["intent_expansion_max"] == 0

    def test_triage_returns_full_mode_from_signal(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """When GLM writes a full-mode signal, triage returns it."""
        # Simulate GLM writing the triage signal
        signal = {
            "section": "01",
            "intent_mode": "full",
            "budgets": {
                "proposal_max": 5,
                "implementation_max": 5,
                "intent_expansion_max": 2,
                "max_new_surfaces_per_cycle": 8,
                "max_new_axes_total": 6,
            },
            "reason": "5+ related files",
        }
        signal_path = (intent_planspace / "artifacts" / "signals"
                       / "intent-triage-01.json")

        def write_triage_signal(*args, **kwargs):
            signal_path.parent.mkdir(parents=True, exist_ok=True)
            signal_path.write_text(json.dumps(signal), encoding="utf-8")
            return ""

        mock_dispatch.side_effect = write_triage_signal

        from section_loop.intent.triage import run_intent_triage
        result = run_intent_triage(
            "01", intent_planspace, intent_planspace, "parent",
            related_files_count=6, mode="brownfield",
        )
        assert result["intent_mode"] == "full"
        assert result["budgets"]["intent_expansion_max"] == 2

    def test_triage_falls_back_to_lightweight(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """When GLM fails to write signal, fallback to lightweight."""
        mock_dispatch.return_value = ""

        from section_loop.intent.triage import run_intent_triage
        result = run_intent_triage(
            "01", intent_planspace, intent_planspace, "parent",
        )
        assert result["intent_mode"] == "lightweight"


# ---------------------------------------------------------------------------
# Bootstrap Tests
# ---------------------------------------------------------------------------

class TestIntentBootstrap:
    """Philosophy distillation and intent pack generation."""

    def test_philosophy_distill_creates_file(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """Philosophy distiller creates the operational philosophy."""
        philosophy_path = (
            intent_planspace / "artifacts" / "intent" / "global"
            / "philosophy.md"
        )

        def write_philosophy(*args, **kwargs):
            philosophy_path.write_text(
                "# Operational Philosophy\n\n"
                "## P1 Strategy over brute force\n"
                "Choose the path that collapses cycles.\n",
                encoding="utf-8",
            )
            return ""

        mock_dispatch.side_effect = write_philosophy

        from section_loop.intent.bootstrap import ensure_global_philosophy
        result = ensure_global_philosophy(
            intent_planspace, intent_planspace, "parent",
        )
        assert result == philosophy_path
        assert philosophy_path.exists()
        assert "P1" in philosophy_path.read_text(encoding="utf-8")

    def test_philosophy_skips_if_exists(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """Skip distillation when philosophy already exists."""
        philosophy_path = (
            intent_planspace / "artifacts" / "intent" / "global"
            / "philosophy.md"
        )
        philosophy_path.write_text("# Existing Philosophy\n\nP1...\n")

        from section_loop.intent.bootstrap import ensure_global_philosophy
        ensure_global_philosophy(
            intent_planspace, intent_planspace, "parent",
        )
        assert mock_dispatch.call_count == 0

    def test_intent_pack_creates_registry(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Intent pack generator creates the surface registry."""
        section = _make_intent_section(intent_planspace, codespace)
        mock_dispatch.return_value = ""

        from section_loop.intent.bootstrap import generate_intent_pack
        intent_dir = generate_intent_pack(
            section, intent_planspace, codespace, "parent",
        )
        registry_path = intent_dir / "surface-registry.json"
        assert registry_path.exists()
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        assert data["section"] == "01"
        assert data["surfaces"] == []


# ---------------------------------------------------------------------------
# Expansion Tests
# ---------------------------------------------------------------------------

class TestExpansionCycle:
    """Expansion cycle: dispatch expanders, interpret deltas."""

    def test_no_surfaces_means_no_expansion(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """No surfaces signal → no expansion, no restart."""
        from section_loop.intent.expansion import run_expansion_cycle
        result = run_expansion_cycle(
            "01", intent_planspace, intent_planspace, "parent",
        )
        assert result["restart_required"] is False
        assert result["expansion_applied"] is False
        assert result["surfaces_found"] == 0

    def test_expansion_with_problem_surfaces(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Problem surfaces trigger problem expander and restart."""
        # Write surfaces signal
        surfaces = {
            "section": "01",
            "stage": "integration_proposal",
            "attempt": 1,
            "problem_surfaces": [
                {"id": "P-01-0001", "kind": "emergent", "axis_id": "A3",
                 "title": "Missing migration path"},
            ],
            "philosophy_surfaces": [],
        }
        surfaces_path = (intent_planspace / "artifacts" / "signals"
                         / "intent-surfaces-01.json")
        surfaces_path.write_text(json.dumps(surfaces), encoding="utf-8")

        # Write initial registry
        registry_path = (
            intent_planspace / "artifacts" / "intent" / "sections"
            / "section-01" / "surface-registry.json"
        )
        registry_path.write_text(
            json.dumps({"section": "01", "next_id": 1, "surfaces": []}),
            encoding="utf-8",
        )

        # Simulate problem expander writing delta
        delta = {
            "section": "01",
            "applied": {
                "problem_definition_updated": True,
                "problem_rubric_updated": True,
            },
            "applied_surface_ids": ["P-01-0001"],
            "discarded_surface_ids": [],
            "new_axes": ["A7"],
            "restart_required": True,
            "restart_reason": "New axis A7 added",
        }

        def write_delta(*args, **kwargs):
            delta_path = (intent_planspace / "artifacts" / "signals"
                          / "intent-delta-01.json")
            delta_path.write_text(json.dumps(delta), encoding="utf-8")
            return ""

        mock_dispatch.side_effect = write_delta

        from section_loop.intent.expansion import run_expansion_cycle
        result = run_expansion_cycle(
            "01", intent_planspace, intent_planspace, "parent",
        )
        assert result["restart_required"] is True
        assert result["expansion_applied"] is True
        assert result["surfaces_found"] == 1

    def test_diminishing_returns_skips_expansion(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """When >60% surfaces are discarded duplicates, skip expansion."""
        # Write surfaces with all-duplicate IDs
        surfaces = {
            "section": "01",
            "stage": "integration_proposal",
            "attempt": 3,
            "problem_surfaces": [
                {"id": "P-01-0001", "kind": "emergent"},
                {"id": "P-01-0002", "kind": "emergent"},
                {"id": "P-01-0003", "kind": "emergent"},
            ],
            "philosophy_surfaces": [],
        }
        surfaces_path = (intent_planspace / "artifacts" / "signals"
                         / "intent-surfaces-01.json")
        surfaces_path.write_text(json.dumps(surfaces), encoding="utf-8")

        # Registry has all three already discarded
        registry = {
            "section": "01", "next_id": 4,
            "surfaces": [
                {"id": "P-01-0001", "status": "discarded",
                 "kind": "emergent", "axis_id": "",
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1},
                 "notes": ""},
                {"id": "P-01-0002", "status": "discarded",
                 "kind": "emergent", "axis_id": "",
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1},
                 "notes": ""},
                {"id": "P-01-0003", "status": "discarded",
                 "kind": "emergent", "axis_id": "",
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1},
                 "notes": ""},
            ],
        }
        save_surface_registry("01", intent_planspace, registry)

        from section_loop.intent.expansion import run_expansion_cycle
        result = run_expansion_cycle(
            "01", intent_planspace, intent_planspace, "parent",
        )
        assert result.get("diminishing") is True
        assert result["expansion_applied"] is False
        # Expanders should NOT have been dispatched
        assert mock_dispatch.call_count == 0


# ---------------------------------------------------------------------------
# Runner Integration Tests
# ---------------------------------------------------------------------------

class TestRunnerIntentIntegration:
    """Integration: intent layer wired into runner.py correctly."""

    def test_full_mode_uses_intent_judge(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """In full mode, alignment uses intent-judge.md not alignment-judge.md."""
        section = _make_intent_section(intent_planspace, codespace)

        call_log: list[dict] = []

        def track_calls(*args, **kwargs):
            call_log.append({"args": args, "kwargs": kwargs})
            agent_file = kwargs.get("agent_file", "")
            sec_num = kwargs.get("section_number", "")

            # Intent triager → full mode
            if agent_file == "intent-triager.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / f"intent-triage-{sec_num}.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "section": sec_num,
                    "intent_mode": "full",
                    "budgets": {
                        "proposal_max": 5,
                        "implementation_max": 5,
                        "intent_expansion_max": 2,
                        "max_new_surfaces_per_cycle": 8,
                        "max_new_axes_total": 6,
                    },
                    "reason": "test",
                }), encoding="utf-8")
                return ""

            # Philosophy distiller
            if agent_file == "philosophy-distiller.md":
                phi_path = (intent_planspace / "artifacts" / "intent"
                            / "global" / "philosophy.md")
                phi_path.parent.mkdir(parents=True, exist_ok=True)
                phi_path.write_text("# Operational Philosophy\nP1...\n")
                return ""

            # Intent pack generator
            if agent_file == "intent-pack-generator.md":
                pack_dir = (intent_planspace / "artifacts" / "intent"
                            / "sections" / f"section-{sec_num}")
                pack_dir.mkdir(parents=True, exist_ok=True)
                (pack_dir / "problem.md").write_text("# Problem\nAuth.\n")
                (pack_dir / "problem-alignment.md").write_text(
                    "# Rubric\n| A1 | Intent |\n")
                return ""

            # Integration proposer → writes proposal
            if agent_file == "integration-proposer.md":
                prop_path = (intent_planspace / "artifacts" / "proposals"
                             / f"section-{sec_num}-integration-proposal.md")
                prop_path.parent.mkdir(parents=True, exist_ok=True)
                prop_path.write_text("# Integration Proposal\nOAuth2.\n")
                return ""

            # Intent judge (alignment) → ALIGNED, no surfaces
            if agent_file == "intent-judge.md":
                return '{"frame_ok": true, "aligned": true, "problems": []}'

            # Implementation strategist
            if agent_file == "implementation-strategist.md":
                mod_path = (intent_planspace / "artifacts"
                            / f"impl-{sec_num}-modified.txt")
                mod_path.write_text("src/main.py\n")
                return ""

            # Implementation alignment
            if agent_file == "alignment-judge.md":
                return '{"frame_ok": true, "aligned": true, "problems": []}'

            return ""

        mock_dispatch.side_effect = track_calls

        from section_loop.section_engine import run_section
        result = run_section(
            intent_planspace, codespace, section, "parent",
        )

        # Verify intent-judge.md was used (not alignment-judge.md)
        # for the proposal alignment step
        intent_judge_calls = [
            c for c in call_log
            if c["kwargs"].get("agent_file") == "intent-judge.md"
        ]
        assert len(intent_judge_calls) >= 1, (
            "Intent judge should be used in full mode for proposal alignment"
        )

    def test_lightweight_mode_uses_alignment_judge(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """In lightweight mode, alignment uses alignment-judge.md."""
        section = _make_intent_section(intent_planspace, codespace)

        call_log: list[dict] = []

        def track_calls(*args, **kwargs):
            call_log.append({"args": args, "kwargs": kwargs})
            agent_file = kwargs.get("agent_file", "")
            sec_num = kwargs.get("section_number", "")

            # Intent triager → lightweight
            if agent_file == "intent-triager.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / f"intent-triage-{sec_num}.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "section": sec_num,
                    "intent_mode": "lightweight",
                    "budgets": {"proposal_max": 5, "implementation_max": 5,
                                "intent_expansion_max": 0},
                    "reason": "simple change",
                }), encoding="utf-8")
                return ""

            # Integration proposer
            if agent_file == "integration-proposer.md":
                prop_path = (intent_planspace / "artifacts" / "proposals"
                             / f"section-{sec_num}-integration-proposal.md")
                prop_path.parent.mkdir(parents=True, exist_ok=True)
                prop_path.write_text("# Integration Proposal\nSimple.\n")
                return ""

            # Alignment judge → ALIGNED
            if agent_file == "alignment-judge.md":
                return '{"frame_ok": true, "aligned": true, "problems": []}'

            # Implementation
            if agent_file == "implementation-strategist.md":
                mod_path = (intent_planspace / "artifacts"
                            / f"impl-{sec_num}-modified.txt")
                mod_path.write_text("src/main.py\n")
                return ""

            return ""

        mock_dispatch.side_effect = track_calls

        from section_loop.section_engine import run_section
        run_section(intent_planspace, codespace, section, "parent")

        # Verify alignment-judge.md was used (not intent-judge.md)
        intent_judge_calls = [
            c for c in call_log
            if c["kwargs"].get("agent_file") == "intent-judge.md"
        ]
        assert len(intent_judge_calls) == 0, (
            "Intent judge should NOT be used in lightweight mode"
        )


# ---------------------------------------------------------------------------
# Pipeline Control: Intent artifacts in hash
# ---------------------------------------------------------------------------

class TestIntentInputsHash:
    """Intent artifacts affect section inputs hash."""

    def test_philosophy_change_changes_hash(
        self, intent_planspace: Path, codespace: Path,
    ) -> None:
        """Changing philosophy.md changes the section inputs hash."""
        from section_loop.pipeline_control import _section_inputs_hash

        sec = Section(
            number="01",
            path=intent_planspace / "artifacts" / "sections" / "section-01.md",
            related_files=["src/main.py"],
        )
        sec.path.write_text("# Section 01\n")

        sections_by_num = {"01": sec}
        hash1 = _section_inputs_hash(
            "01", intent_planspace, codespace, sections_by_num)

        # Add philosophy
        phi = (intent_planspace / "artifacts" / "intent" / "global"
               / "philosophy.md")
        phi.write_text("# Philosophy\nP1 Strategy.\n")
        hash2 = _section_inputs_hash(
            "01", intent_planspace, codespace, sections_by_num)

        assert hash1 != hash2

    def test_problem_definition_change_changes_hash(
        self, intent_planspace: Path, codespace: Path,
    ) -> None:
        """Changing problem.md changes the section inputs hash."""
        from section_loop.pipeline_control import _section_inputs_hash

        sec = Section(
            number="01",
            path=intent_planspace / "artifacts" / "sections" / "section-01.md",
            related_files=["src/main.py"],
        )
        sec.path.write_text("# Section 01\n")

        sections_by_num = {"01": sec}
        hash1 = _section_inputs_hash(
            "01", intent_planspace, codespace, sections_by_num)

        # Add problem definition
        prob = (intent_planspace / "artifacts" / "intent" / "sections"
                / "section-01" / "problem.md")
        prob.write_text("# Problem\nAuth refactor.\n")
        hash2 = _section_inputs_hash(
            "01", intent_planspace, codespace, sections_by_num)

        assert hash1 != hash2


# ---------------------------------------------------------------------------
# Regression Guards: Intent conventions
# ---------------------------------------------------------------------------

class TestIntentConventions:
    """Regression guards for intent layer conventions."""

    def test_intent_model_policy_defaults_exist(self) -> None:
        """All intent model keys have defaults in read_model_policy."""
        from section_loop.dispatch import read_model_policy
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ps = Path(td)
            (ps / "artifacts").mkdir(parents=True)
            policy = read_model_policy(ps)
            assert "intent_triage" in policy
            assert "intent_judge" in policy
            assert "intent_pack" in policy
            assert "intent_philosophy" in policy
            assert "intent_problem_expander" in policy
            assert "intent_philosophy_expander" in policy

    def test_intent_module_imports(self) -> None:
        """Intent module public API is importable."""
        from section_loop.intent import (
            ensure_global_philosophy,
            generate_intent_pack,
            load_surface_registry,
            merge_surfaces_into_registry,
            run_expansion_cycle,
            run_intent_triage,
            surfaces_are_diminishing,
        )
        # Smoke check — all names resolve
        assert callable(ensure_global_philosophy)
        assert callable(generate_intent_pack)
        assert callable(load_surface_registry)
        assert callable(merge_surfaces_into_registry)
        assert callable(run_expansion_cycle)
        assert callable(run_intent_triage)
        assert callable(surfaces_are_diminishing)
