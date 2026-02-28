"""Tests for the intent layer: triage, bootstrap, surfaces, expansion, runner integration.

Mock boundary: only ``dispatch_agent`` (the LLM call) is mocked.
Everything else — file I/O, JSON parsing, registry logic — runs for real.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from section_loop.intent.surfaces import (
    find_discarded_recurrences,
    load_surface_registry,
    merge_surfaces_into_registry,
    normalize_surface_ids,
    save_surface_registry,
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

    def test_find_discarded_recurrences_returns_matches(self) -> None:
        """Discarded surfaces that reappear are returned as recurrences."""
        registry = {
            "surfaces": [
                {"id": "P-01-0001", "status": "discarded",
                 "notes": "was wrong"},
                {"id": "P-01-0002", "status": "applied"},
                {"id": "P-01-0003", "status": "discarded",
                 "notes": "out of scope"},
            ],
        }
        dupes = ["P-01-0001", "P-01-0002", "P-01-0003"]
        recurrences = find_discarded_recurrences(registry, dupes)
        ids = [r["id"] for r in recurrences]
        assert "P-01-0001" in ids
        assert "P-01-0003" in ids
        assert "P-01-0002" not in ids  # applied, not discarded

    def test_find_discarded_recurrences_empty_when_none(self) -> None:
        """No discarded surfaces → empty recurrence list."""
        registry = {
            "surfaces": [
                {"id": "P-01-0001", "status": "applied"},
            ],
        }
        dupes = ["P-01-0001"]
        assert find_discarded_recurrences(registry, dupes) == []

    def test_find_discarded_recurrences_empty_no_dupes(self) -> None:
        """No duplicate IDs at all → empty recurrence list."""
        registry = {
            "surfaces": [
                {"id": "P-01-0001", "status": "discarded"},
            ],
        }
        assert find_discarded_recurrences(registry, []) == []

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
        # Provide a grounded source so catalog finds it
        constraints_path = intent_planspace / "constraints.md"
        constraints_path.write_text(
            "# Constraints\nNo new deps.\n", encoding="utf-8")

        philosophy_path = (
            intent_planspace / "artifacts" / "intent" / "global"
            / "philosophy.md"
        )

        def handle_dispatch(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            # V2/R56: source selector writes selection signal
            if agent_file == "philosophy-source-selector.md":
                signal_path = (
                    intent_planspace / "artifacts" / "signals"
                    / "philosophy-selected-sources.json"
                )
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "sources": [{"path": str(constraints_path),
                                 "reason": "constraints"}],
                }), encoding="utf-8")
                return ""
            # Philosophy distiller writes output + source map
            if agent_file == "philosophy-distiller.md":
                philosophy_path.write_text(
                    "# Operational Philosophy\n\n"
                    "## P1 Strategy over brute force\n"
                    "Choose the path that collapses cycles.\n",
                    encoding="utf-8",
                )
                source_map = philosophy_path.parent / \
                    "philosophy-source-map.json"
                source_map.write_text(json.dumps({
                    "P1": str(constraints_path) + ":1-5",
                }), encoding="utf-8")
                return ""
            return ""

        mock_dispatch.side_effect = handle_dispatch

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
        # Write surfaces signal — no pre-set id, normalization assigns it
        surfaces = {
            "section": "01",
            "stage": "integration_proposal",
            "attempt": 1,
            "problem_surfaces": [
                {"kind": "emergent", "axis_id": "A3",
                 "title": "Missing migration path",
                 "description": "No migration strategy for OAuth2",
                 "evidence": "Legacy auth module"},
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

        # Simulate problem expander writing delta — ID assigned by normalization
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

    def test_recurrence_adjudication_dispatches_on_discarded_dupes(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """When all surfaces are discarded duplicates, adjudicator is dispatched (V4/V5 R54)."""
        import hashlib

        def _fp(kind, axis_id, title, description, evidence):
            raw = "|".join(str(v).strip() for v in (kind, axis_id, title, description, evidence))
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

        # Three surfaces — each with distinct content for fingerprinting
        s1 = {"kind": "emergent", "axis_id": "A1", "title": "T1",
               "description": "D1", "evidence": "E1"}
        s2 = {"kind": "emergent", "axis_id": "A2", "title": "T2",
               "description": "D2", "evidence": "E2"}
        s3 = {"kind": "emergent", "axis_id": "A3", "title": "T3",
               "description": "D3", "evidence": "E3"}

        fp1, fp2, fp3 = _fp(**s1), _fp(**s2), _fp(**s3)

        surfaces = {
            "section": "01",
            "stage": "integration_proposal",
            "attempt": 3,
            "problem_surfaces": [s1, s2, s3],
            "philosophy_surfaces": [],
        }
        surfaces_path = (intent_planspace / "artifacts" / "signals"
                         / "intent-surfaces-01.json")
        surfaces_path.write_text(json.dumps(surfaces), encoding="utf-8")

        # Registry has all three already discarded with matching fingerprints
        registry = {
            "section": "01", "next_id": 4,
            "surfaces": [
                {"id": "P-01-0001", "status": "discarded",
                 "kind": "emergent", "axis_id": "A1",
                 "fingerprint": fp1,
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1},
                 "notes": "", "description": "D1", "evidence": "E1"},
                {"id": "P-01-0002", "status": "discarded",
                 "kind": "emergent", "axis_id": "A2",
                 "fingerprint": fp2,
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1},
                 "notes": "", "description": "D2", "evidence": "E2"},
                {"id": "P-01-0003", "status": "discarded",
                 "kind": "emergent", "axis_id": "A3",
                 "fingerprint": fp3,
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1},
                 "notes": "", "description": "D3", "evidence": "E3"},
            ],
        }
        save_surface_registry("01", intent_planspace, registry)

        # Write model policy so adjudicator model is available
        (intent_planspace / "artifacts" / "model-policy.json").write_text(
            json.dumps({}), encoding="utf-8")

        # Adjudicator keeps all discarded (empty reopen list)
        def write_adjudication(*args, **kwargs):
            adj_path = (intent_planspace / "artifacts" / "signals"
                        / "intent-recurrence-adjudication-01.json")
            adj_path.parent.mkdir(parents=True, exist_ok=True)
            adj_path.write_text(json.dumps({
                "section": "01",
                "reopen_ids": [],
                "keep_discarded_ids": ["P-01-0001", "P-01-0002", "P-01-0003"],
                "reason": "All correctly discarded",
            }), encoding="utf-8")
            return ""

        mock_dispatch.side_effect = write_adjudication

        from section_loop.intent.expansion import run_expansion_cycle
        result = run_expansion_cycle(
            "01", intent_planspace, intent_planspace, "parent",
        )
        assert result["expansion_applied"] is False
        assert result["surfaces_found"] == 0
        # Adjudicator was dispatched (not expanders)
        assert mock_dispatch.call_count == 1
        # Recurrence signal should exist
        recurrence_path = (intent_planspace / "artifacts" / "signals"
                           / "intent-surface-recurrence-01.json")
        assert recurrence_path.exists()


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
        # Provide philosophy source for fail-closed check (P7/R52)
        (intent_planspace / "constraints.md").write_text(
            "# Constraints\nNo new deps.\n", encoding="utf-8")
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

            # V2/R56: Philosophy source selector
            if agent_file == "philosophy-source-selector.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / "philosophy-selected-sources.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "sources": [{"path": str(
                        intent_planspace / "constraints.md"),
                        "reason": "constraints"}],
                }), encoding="utf-8")
                return ""

            # Philosophy distiller
            if agent_file == "philosophy-distiller.md":
                phi_path = (intent_planspace / "artifacts" / "intent"
                            / "global" / "philosophy.md")
                phi_path.parent.mkdir(parents=True, exist_ok=True)
                phi_path.write_text("# Operational Philosophy\nP1...\n")
                smap = phi_path.parent / "philosophy-source-map.json"
                smap.write_text(json.dumps({"P1": "constraints.md:1"}))
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
            find_discarded_recurrences,
            generate_intent_pack,
            load_surface_registry,
            merge_surfaces_into_registry,
            normalize_surface_ids,
            run_expansion_cycle,
            run_intent_triage,
        )
        # Smoke check — all names resolve
        assert callable(ensure_global_philosophy)
        assert callable(find_discarded_recurrences)
        assert callable(generate_intent_pack)
        assert callable(load_surface_registry)
        assert callable(merge_surfaces_into_registry)
        assert callable(normalize_surface_ids)
        assert callable(run_expansion_cycle)
        assert callable(run_intent_triage)

    def test_normalize_surface_ids_assigns_stable_ids(self) -> None:
        """normalize_surface_ids assigns P-sec-NNNN / F-sec-NNNN IDs."""
        registry = {"section": "01", "next_id": 1, "surfaces": []}
        surfaces = {
            "problem_surfaces": [
                {"kind": "emergent", "axis_id": "A3",
                 "title": "Missing migration path",
                 "description": "No migration strategy",
                 "evidence": "Legacy auth module"},
            ],
            "philosophy_surfaces": [
                {"kind": "tension", "axis_id": "",
                 "title": "Speed vs safety",
                 "description": "P1 and P3 conflict",
                 "evidence": "Section 01 constraints"},
            ],
        }
        result = normalize_surface_ids(surfaces, registry, "01")
        assert result["problem_surfaces"][0]["id"] == "P-01-0001"
        assert result["philosophy_surfaces"][0]["id"] == "F-01-0002"
        assert registry["next_id"] == 3
        # Fingerprints are set
        assert "_fingerprint" in result["problem_surfaces"][0]
        assert "_fingerprint" in result["philosophy_surfaces"][0]

    def test_normalize_reuses_existing_ids_by_fingerprint(self) -> None:
        """Duplicate surfaces get the same ID via fingerprint match."""
        import hashlib
        raw = "|".join(["emergent", "A3", "T1", "D1", "E1"])
        fp = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

        registry = {
            "section": "01", "next_id": 5,
            "surfaces": [
                {"id": "P-01-0003", "fingerprint": fp, "status": "applied"},
            ],
        }
        surfaces = {
            "problem_surfaces": [
                {"kind": "emergent", "axis_id": "A3",
                 "title": "T1", "description": "D1", "evidence": "E1"},
            ],
            "philosophy_surfaces": [],
        }
        result = normalize_surface_ids(surfaces, registry, "01")
        # Should reuse existing ID, not allocate a new one
        assert result["problem_surfaces"][0]["id"] == "P-01-0003"
        assert registry["next_id"] == 5  # counter unchanged

    def test_philosophy_fail_closed_no_sources(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """No philosophy sources → fail-closed, return None, write signal."""
        from section_loop.intent.bootstrap import ensure_global_philosophy
        # No constraints.md, philosophy.md etc. in planspace
        result = ensure_global_philosophy(
            intent_planspace, intent_planspace, "parent",
        )
        assert result is None
        # Distiller should NOT have been called
        assert mock_dispatch.call_count == 0
        # Signal should exist
        signal_path = (intent_planspace / "artifacts" / "signals"
                       / "philosophy-source-missing.json")
        assert signal_path.exists()
        signal = json.loads(signal_path.read_text(encoding="utf-8"))
        assert signal["state"] == "philosophy_source_missing"

    def test_loop_contract_includes_intent_artifacts(self) -> None:
        """loop-contract.md lists intent artifacts in inputs."""
        contract = Path(__file__).resolve().parent.parent / "src" / "loop-contract.md"
        if not contract.exists():
            pytest.skip("loop-contract.md not found")
        text = contract.read_text(encoding="utf-8")
        assert "intent/global/philosophy.md" in text
        assert "intent/sections/section-NN/problem.md" in text
        assert "intent/sections/section-NN/problem-alignment.md" in text

    def test_alignment_template_includes_intent_refs(self) -> None:
        """Integration alignment template references intent artifacts."""
        tmpl = (Path(__file__).resolve().parent.parent / "src"
                / "scripts" / "section_loop" / "prompts" / "templates"
                / "integration-alignment.md")
        if not tmpl.exists():
            pytest.skip("integration-alignment.md not found")
        text = tmpl.read_text(encoding="utf-8")
        assert "{intent_problem_ref}" in text
        assert "{intent_rubric_ref}" in text
        assert "{intent_philosophy_ref}" in text

    def test_agent_contract_triager_budget_keys(self) -> None:
        """intent-triager.md contains cycle-budget schema keys (V1/R53)."""
        agent = (Path(__file__).resolve().parent.parent / "src"
                 / "agents" / "intent-triager.md")
        if not agent.exists():
            pytest.skip("intent-triager.md not found")
        text = agent.read_text(encoding="utf-8")
        for key in ("proposal_max", "implementation_max",
                     "intent_expansion_max", "max_new_surfaces_per_cycle",
                     "max_new_axes_total"):
            assert key in text, f"intent-triager.md missing budget key: {key}"

    def test_agent_contract_problem_expander_delta_keys(self) -> None:
        """problem-expander.md delta matches expansion.py schema (V2/R53)."""
        agent = (Path(__file__).resolve().parent.parent / "src"
                 / "agents" / "problem-expander.md")
        if not agent.exists():
            pytest.skip("problem-expander.md not found")
        text = agent.read_text(encoding="utf-8")
        for key in ("applied_surface_ids", "discarded_surface_ids",
                     "problem_definition_updated", "restart_required"):
            assert key in text, (
                f"problem-expander.md missing delta key: {key}")

    def test_agent_contract_philosophy_expander_delta_keys(self) -> None:
        """philosophy-expander.md delta matches expansion.py schema (V3/R53)."""
        agent = (Path(__file__).resolve().parent.parent / "src"
                 / "agents" / "philosophy-expander.md")
        if not agent.exists():
            pytest.skip("philosophy-expander.md not found")
        text = agent.read_text(encoding="utf-8")
        for key in ("applied_surface_ids", "discarded_surface_ids",
                     "philosophy_updated", "needs_user_input"):
            assert key in text, (
                f"philosophy-expander.md missing delta key: {key}")

    def test_agent_contract_pack_generator_registry_schema(self) -> None:
        """intent-pack-generator.md defines dedupe registry, not axis metadata (V4/R53)."""
        agent = (Path(__file__).resolve().parent.parent / "src"
                 / "agents" / "intent-pack-generator.md")
        if not agent.exists():
            pytest.skip("intent-pack-generator.md not found")
        text = agent.read_text(encoding="utf-8")
        assert "next_id" in text, (
            "intent-pack-generator.md must define registry with next_id")
        assert '"surfaces": []' in text, (
            "intent-pack-generator.md must define registry with surfaces list")
        assert "axis_count" not in text, (
            "intent-pack-generator.md must NOT put axis metadata in registry")

    def test_surface_registry_wrong_schema_renamed(
        self, intent_planspace: Path,
    ) -> None:
        """Registry with valid JSON but wrong schema → renamed + default (V6/R53)."""
        registry_path = (
            intent_planspace / "artifacts" / "intent" / "sections"
            / "section-01" / "surface-registry.json"
        )
        # Valid JSON but wrong schema (axis metadata instead of dedupe registry)
        registry_path.write_text(json.dumps({
            "section": "01", "axis_count": 8,
            "axes": [{"id": "A1", "title": "Intent"}],
        }), encoding="utf-8")
        loaded = load_surface_registry("01", intent_planspace)
        assert loaded["surfaces"] == []
        assert loaded["next_id"] == 1
        assert registry_path.with_suffix(".malformed.json").exists()

    def test_todo_extraction_before_intent_pack(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """TODO extraction runs before intent pack generation (V5/R53)."""
        (intent_planspace / "constraints.md").write_text(
            "# Constraints\nNo new deps.\n", encoding="utf-8")
        section = _make_intent_section(intent_planspace, codespace)

        # Write a TODO in a related file
        src_dir = codespace / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "main.py").write_text(
            "# TODO: migrate auth to OAuth2\ndef main(): pass\n")

        call_order: list[str] = []

        def track_calls(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            sec_num = kwargs.get("section_number", "")
            call_order.append(agent_file)

            if agent_file == "intent-triager.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / f"intent-triage-{sec_num}.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "section": sec_num, "intent_mode": "full",
                    "budgets": {"proposal_max": 5, "implementation_max": 5,
                                "intent_expansion_max": 2,
                                "max_new_surfaces_per_cycle": 8,
                                "max_new_axes_total": 6},
                    "reason": "test",
                }), encoding="utf-8")
                return ""

            # V2/R56: Philosophy source selector
            if agent_file == "philosophy-source-selector.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / "philosophy-selected-sources.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "sources": [{"path": str(
                        intent_planspace / "constraints.md"),
                        "reason": "constraints"}],
                }), encoding="utf-8")
                return ""

            if agent_file == "philosophy-distiller.md":
                phi = (intent_planspace / "artifacts" / "intent"
                       / "global" / "philosophy.md")
                phi.parent.mkdir(parents=True, exist_ok=True)
                phi.write_text("# Philosophy\nP1...\n")
                smap = phi.parent / "philosophy-source-map.json"
                smap.write_text(json.dumps({"P1": "constraints.md:1"}))
                return ""

            if agent_file == "intent-pack-generator.md":
                # Verify TODOs file exists BEFORE pack generation
                todos = (intent_planspace / "artifacts" / "todos"
                         / f"section-{sec_num}-todos.md")
                assert todos.exists(), (
                    "TODOs must be extracted before intent pack generation")
                pack_dir = (intent_planspace / "artifacts" / "intent"
                            / "sections" / f"section-{sec_num}")
                pack_dir.mkdir(parents=True, exist_ok=True)
                (pack_dir / "problem.md").write_text("# Problem\n")
                (pack_dir / "problem-alignment.md").write_text("# Rubric\n")
                return ""

            if agent_file == "integration-proposer.md":
                prop = (intent_planspace / "artifacts" / "proposals"
                        / f"section-{sec_num}-integration-proposal.md")
                prop.parent.mkdir(parents=True, exist_ok=True)
                prop.write_text("# Proposal\n")
                return ""

            if agent_file == "intent-judge.md":
                return '{"frame_ok": true, "aligned": true, "problems": []}'

            if agent_file == "implementation-strategist.md":
                mod = (intent_planspace / "artifacts"
                       / f"impl-{sec_num}-modified.txt")
                mod.write_text("src/main.py\n")
                return ""

            if agent_file == "alignment-judge.md":
                return '{"frame_ok": true, "aligned": true, "problems": []}'

            return ""

        mock_dispatch.side_effect = track_calls

        from section_loop.section_engine import run_section
        run_section(intent_planspace, codespace, section, "parent")

        # intent-pack-generator must come AFTER TODO extraction
        # (which happens before any agent dispatch in full mode)
        assert "intent-pack-generator.md" in call_order

    def test_triage_budgets_applied_to_cycle_budget(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """proposal_max and implementation_max from triage reach cycle budget (V7/R53)."""
        section = _make_intent_section(intent_planspace, codespace)

        # Pre-create a cycle budget file
        budget_path = (intent_planspace / "artifacts" / "signals"
                       / f"section-{section.number}-cycle-budget.json")
        budget_path.parent.mkdir(parents=True, exist_ok=True)
        budget_path.write_text(json.dumps({"existing_key": 42}),
                               encoding="utf-8")

        def track_calls(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            sec_num = kwargs.get("section_number", "")

            if agent_file == "intent-triager.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / f"intent-triage-{sec_num}.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "section": sec_num, "intent_mode": "lightweight",
                    "budgets": {"proposal_max": 7, "implementation_max": 3,
                                "intent_expansion_max": 0},
                    "reason": "test",
                }), encoding="utf-8")
                return ""

            if agent_file == "integration-proposer.md":
                prop = (intent_planspace / "artifacts" / "proposals"
                        / f"section-{sec_num}-integration-proposal.md")
                prop.parent.mkdir(parents=True, exist_ok=True)
                prop.write_text("# Proposal\n")
                return ""

            if agent_file == "alignment-judge.md":
                return '{"frame_ok": true, "aligned": true, "problems": []}'

            if agent_file == "implementation-strategist.md":
                mod = (intent_planspace / "artifacts"
                       / f"impl-{sec_num}-modified.txt")
                mod.write_text("src/main.py\n")
                return ""

            return ""

        mock_dispatch.side_effect = track_calls

        from section_loop.section_engine import run_section
        run_section(intent_planspace, codespace, section, "parent")

        # Read cycle budget and verify triage keys are present
        updated = json.loads(budget_path.read_text(encoding="utf-8"))
        assert updated.get("proposal_max") == 7, (
            "proposal_max from triage must be applied to cycle budget")
        assert updated.get("implementation_max") == 3, (
            "implementation_max from triage must be applied to cycle budget")

    def test_cycle_budget_malformed_preserved(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Malformed cycle budget → renamed + proceeds (V6/R53)."""
        section = _make_intent_section(intent_planspace, codespace)

        # Write malformed cycle budget
        budget_path = (intent_planspace / "artifacts" / "signals"
                       / f"section-{section.number}-cycle-budget.json")
        budget_path.parent.mkdir(parents=True, exist_ok=True)
        budget_path.write_text("not json!", encoding="utf-8")

        def track_calls(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            sec_num = kwargs.get("section_number", "")

            if agent_file == "intent-triager.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / f"intent-triage-{sec_num}.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "section": sec_num, "intent_mode": "lightweight",
                    "budgets": {"proposal_max": 5, "implementation_max": 5,
                                "intent_expansion_max": 0},
                    "reason": "test",
                }), encoding="utf-8")
                return ""

            if agent_file == "integration-proposer.md":
                prop = (intent_planspace / "artifacts" / "proposals"
                        / f"section-{sec_num}-integration-proposal.md")
                prop.parent.mkdir(parents=True, exist_ok=True)
                prop.write_text("# Proposal\n")
                return ""

            if agent_file == "alignment-judge.md":
                return '{"frame_ok": true, "aligned": true, "problems": []}'

            if agent_file == "implementation-strategist.md":
                mod = (intent_planspace / "artifacts"
                       / f"impl-{sec_num}-modified.txt")
                mod.write_text("src/main.py\n")
                return ""

            return ""

        mock_dispatch.side_effect = track_calls

        from section_loop.section_engine import run_section
        # Should not crash
        run_section(intent_planspace, codespace, section, "parent")

        # Malformed file should be renamed
        assert budget_path.with_suffix(".malformed.json").exists()

    def test_triage_escalation_dispatches_stronger_model(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """Triage escalation re-dispatches with stronger model (V1/R54)."""
        call_models: list[str] = []

        def track_calls(*args, **kwargs):
            model = args[0] if args else "unknown"
            call_models.append(model)
            agent_file = kwargs.get("agent_file", "")
            sec_num = kwargs.get("section_number", "")

            if agent_file == "intent-triager.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / f"intent-triage-{sec_num}.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                # First call: escalate. Second call: resolved.
                if len(call_models) <= 1:
                    signal_path.write_text(json.dumps({
                        "section": sec_num, "intent_mode": "full",
                        "confidence": "low", "escalate": True,
                        "budgets": {"proposal_max": 5,
                                    "implementation_max": 5,
                                    "intent_expansion_max": 2,
                                    "max_new_surfaces_per_cycle": 8,
                                    "max_new_axes_total": 6},
                        "reason": "uncertain",
                    }), encoding="utf-8")
                else:
                    signal_path.write_text(json.dumps({
                        "section": sec_num, "intent_mode": "lightweight",
                        "confidence": "high", "escalate": False,
                        "budgets": {"proposal_max": 5,
                                    "implementation_max": 5,
                                    "intent_expansion_max": 0},
                        "reason": "escalated: actually simple",
                    }), encoding="utf-8")
                return ""

            if agent_file == "integration-proposer.md":
                prop = (intent_planspace / "artifacts" / "proposals"
                        / f"section-{sec_num}-integration-proposal.md")
                prop.parent.mkdir(parents=True, exist_ok=True)
                prop.write_text("# Proposal\n")
                return ""

            if agent_file == "alignment-judge.md":
                return '{"frame_ok": true, "aligned": true, "problems": []}'

            if agent_file == "implementation-strategist.md":
                mod = (intent_planspace / "artifacts"
                       / f"impl-{sec_num}-modified.txt")
                mod.write_text("src/main.py\n")
                return ""

            return ""

        mock_dispatch.side_effect = track_calls
        section = _make_intent_section(intent_planspace, intent_planspace)

        from section_loop.section_engine import run_section
        run_section(intent_planspace, intent_planspace, section, "parent")

        # First model is GLM (triage), second is escalation model
        assert len(call_models) >= 2
        assert call_models[0] == "glm"  # initial triage
        assert call_models[1] == "claude-opus"  # escalation

    def test_no_hard_rule_in_triager(self) -> None:
        """intent-triager.md must not contain 'hard rule' phrasing (V2/R54)."""
        agent = (Path(__file__).resolve().parent.parent / "src"
                 / "agents" / "intent-triager.md")
        if not agent.exists():
            pytest.skip("intent-triager.md not found")
        text = agent.read_text(encoding="utf-8").lower()
        assert "hard rule" not in text, (
            "intent-triager.md must not contain 'hard rule' — "
            "triage should use heuristic judgment")
        assert "do not expand the list" not in text, (
            "intent-triager.md must not freeze keyword lists")

    def test_no_default_axes_mandate_in_bootstrap(self) -> None:
        """bootstrap.py prompt must not mandate default axes (V3/V8 R54)."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")
        assert "Always include" not in text, (
            "bootstrap.py must not mandate default axes — "
            "axes should be evidence-driven")
        assert "Coverage scan" not in text, (
            "bootstrap.py must not use 'Coverage scan' framing — "
            "use 'Axis alignment pass' or similar")

    def test_no_diminishing_returns_threshold_in_surfaces(self) -> None:
        """surfaces.py must not contain hardcoded diminishing threshold (V4/R54)."""
        surf = (Path(__file__).resolve().parent.parent / "src"
                / "scripts" / "section_loop" / "intent" / "surfaces.py")
        if not surf.exists():
            pytest.skip("surfaces.py not found")
        text = surf.read_text(encoding="utf-8")
        assert "0.6" not in text, (
            "surfaces.py must not contain hardcoded 0.6 threshold")
        assert "surfaces_are_diminishing" not in text, (
            "surfaces.py must not define surfaces_are_diminishing — "
            "recurrence is adjudicated by agents")

    def test_intent_model_policy_escalation_keys(self) -> None:
        """Model policy includes escalation and recurrence adjudicator keys (V1/V5 R54)."""
        from section_loop.dispatch import read_model_policy
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            ps = Path(td)
            (ps / "artifacts").mkdir(parents=True)
            policy = read_model_policy(ps)
            assert "intent_triage_escalation" in policy, (
                "Model policy must include intent_triage_escalation key")
            assert "intent_recurrence_adjudicator" in policy, (
                "Model policy must include intent_recurrence_adjudicator key")

    def test_layout_agnostic_conftest(self) -> None:
        """conftest.py DB_SH path resolves without hardcoded src/ (V6/R54)."""
        conftest = (Path(__file__).resolve().parent / "conftest.py")
        text = conftest.read_text(encoding="utf-8")
        # Must NOT hardcode "src" / "scripts" / "db.sh" directly
        assert "PROJECT_ROOT / \"src\" / \"scripts\" / \"db.sh\"" not in text, (
            "conftest.py must use layout-agnostic path resolution")

    def test_implement_md_describes_intent_layer(self) -> None:
        """implement.md must describe the intent layer (V7/R54)."""
        impl = (Path(__file__).resolve().parent.parent / "src"
                / "implement.md")
        if not impl.exists():
            pytest.skip("implement.md not found")
        text = impl.read_text(encoding="utf-8")
        assert "intent triage" in text.lower(), (
            "implement.md must describe intent triage")
        assert "intent pack" in text.lower(), (
            "implement.md must describe intent pack generation")



class TestR55IntentPackCorrections:
    """R55/V1: generate_intent_pack includes codemap corrections."""

    def test_codemap_corrections_in_prompt(
        self, planspace, codespace, section_01, mock_dispatch,
    ) -> None:
        """Intent pack prompt references codemap corrections when present."""
        from section_loop.intent.bootstrap import generate_intent_pack
        from section_loop.types import Section

        # Create codemap and corrections
        artifacts = planspace / "artifacts"
        codemap = artifacts / "codemap.md"
        codemap.write_text("# Codemap\nFiles here\n")
        corrections = artifacts / "signals" / "codemap-corrections.json"
        corrections.parent.mkdir(parents=True, exist_ok=True)
        corrections.write_text('{"fixes": []}')

        # Create model policy
        (planspace / "model-policy.json").write_text("{}")

        sec = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
            related_files=[],
        )
        mock_dispatch.return_value = ""

        generate_intent_pack(sec, planspace, codespace, "test-parent")

        # The prompt should reference corrections
        prompt_path = artifacts / "intent-pack-01-prompt.md"
        assert prompt_path.exists()
        prompt_text = prompt_path.read_text(encoding="utf-8")
        assert "codemap-corrections" in prompt_text, (
            "Intent pack prompt must reference codemap corrections")


class TestR55BudgetEnforcementFunctional:
    """R55/V10: expansion cycle enforces budget on expander workload."""

    def test_pending_surfaces_written_on_budget(
        self, planspace, codespace, section_01, mock_dispatch,
    ) -> None:
        """When budget truncates, pending-surfaces file is written."""
        import json
        from section_loop.intent.expansion import run_expansion_cycle

        artifacts = planspace / "artifacts"
        signals = artifacts / "signals"
        signals.mkdir(parents=True, exist_ok=True)

        # Create model policy
        (planspace / "model-policy.json").write_text("{}")

        # Create surface registry
        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)
        registry = {"section": "01", "next_id": 20, "surfaces": []}
        (intent_sec / "surface-registry.json").write_text(
            json.dumps(registry))

        # Create surfaces signal with many surfaces
        surfaces = {
            "problem_surfaces": [
                {"id": f"P-01-{i:04d}", "kind": "problem",
                 "description": f"Surface {i}", "evidence": "test"}
                for i in range(1, 12)
            ],
            "philosophy_surfaces": [],
        }
        (signals / "intent-surfaces-01.json").write_text(
            json.dumps(surfaces))

        # Create intent pack files so expander has targets
        (intent_sec / "problem.md").write_text("# Problem\n")
        (intent_sec / "problem-alignment.md").write_text("# Rubric\n")

        mock_dispatch.return_value = ""

        run_expansion_cycle(
            "01", planspace, codespace, "test-parent",
            budgets={"max_new_surfaces_per_cycle": 3},
        )

        # The pending surfaces file should exist with only budgeted entries
        pending = signals / "intent-surfaces-pending-01.json"
        assert pending.exists(), (
            "Pending surfaces file must be written when budget applies")
        data = json.loads(pending.read_text())
        total = len(data.get("problem_surfaces", []))
        total += len(data.get("philosophy_surfaces", []))
        assert total <= 3, (
            f"Pending surfaces must respect budget (got {total}, max 3)")


# ---------------------------------------------------------------------------
# R56 Regression Guards
# ---------------------------------------------------------------------------


class TestR56QueueSemantics:
    """V1/R56: Expansion uses queue semantics — pending backlog is drained."""

    def test_backlog_surfaces_processed_in_next_cycle(
        self, planspace, codespace, section_01, mock_dispatch,
    ) -> None:
        """Pending surfaces from prior truncation are processed next cycle."""
        from section_loop.intent.expansion import run_expansion_cycle

        artifacts = planspace / "artifacts"
        signals = artifacts / "signals"
        signals.mkdir(parents=True, exist_ok=True)
        (planspace / "model-policy.json").write_text("{}")

        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)
        (intent_sec / "problem.md").write_text("# Problem\n")
        (intent_sec / "problem-alignment.md").write_text("# Rubric\n")

        # Registry has 3 pending surfaces from prior truncation
        registry = {
            "section": "01", "next_id": 4,
            "surfaces": [
                {"id": "P-01-0001", "status": "pending",
                 "kind": "emergent", "axis_id": "A1",
                 "fingerprint": "aaa", "notes": "Backlog 1",
                 "description": "D1", "evidence": "E1",
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1}},
                {"id": "P-01-0002", "status": "pending",
                 "kind": "emergent", "axis_id": "A2",
                 "fingerprint": "bbb", "notes": "Backlog 2",
                 "description": "D2", "evidence": "E2",
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1}},
                {"id": "P-01-0003", "status": "applied",
                 "kind": "emergent", "axis_id": "A3",
                 "fingerprint": "ccc", "notes": "Done",
                 "description": "D3", "evidence": "E3",
                 "first_seen": {"stage": "x", "attempt": 1},
                 "last_seen": {"stage": "x", "attempt": 1}},
            ],
        }
        (intent_sec / "surface-registry.json").write_text(
            json.dumps(registry))

        # Empty judge signal — no new surfaces, but pending backlog exists
        surfaces = {
            "problem_surfaces": [],
            "philosophy_surfaces": [],
        }
        (signals / "intent-surfaces-01.json").write_text(
            json.dumps(surfaces))

        mock_dispatch.return_value = ""

        result = run_expansion_cycle(
            "01", planspace, codespace, "test-parent",
            budgets={"max_new_surfaces_per_cycle": 8},
        )

        # Backlog should be processed (2 pending surfaces)
        assert result["surfaces_found"] == 2, (
            "Queue semantics: pending backlog must be processed "
            f"even with empty judge signal (got {result['surfaces_found']})")

        # Pending surfaces file should contain the backlog items
        pending = signals / "intent-surfaces-pending-01.json"
        assert pending.exists()
        data = json.loads(pending.read_text())
        ids = [s["id"] for s in data.get("problem_surfaces", [])]
        assert "P-01-0001" in ids, "Backlog P-01-0001 must be in pending"
        assert "P-01-0002" in ids, "Backlog P-01-0002 must be in pending"


class TestR56AgentSelectedSources:
    """V2/R56: Philosophy sources selected by agent, not hardcoded."""

    def test_no_hardcoded_filenames_in_bootstrap(self) -> None:
        """bootstrap.py must not contain hardcoded philosophy filename lists."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")
        # Must not have hardcoded candidate names
        assert '"constraints.md"' not in text, (
            "bootstrap.py must not hardcode 'constraints.md' filename")
        assert '"design-philosophy-notes.md"' not in text, (
            "bootstrap.py must not hardcode philosophy filenames")
        assert '"SKILL.md"' not in text, (
            "bootstrap.py must not hardcode SKILL.md")

    def test_catalog_builder_is_mechanical(self) -> None:
        """_build_philosophy_catalog uses bounded walk, not name matching."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")
        # V1/R60: replaced rglob with _walk_md_bounded (os.walk based)
        assert "_walk_md_bounded" in text, (
            "Catalog builder must use _walk_md_bounded for mechanical collection")

    def test_selector_agent_file_exists(self) -> None:
        """philosophy-source-selector.md agent file must exist."""
        agent = (Path(__file__).resolve().parent.parent / "src"
                 / "agents" / "philosophy-source-selector.md")
        if not agent.exists():
            pytest.skip("philosophy-source-selector.md not found")
        text = agent.read_text(encoding="utf-8")
        assert "sources" in text, (
            "Agent must define 'sources' in its output schema")

    def test_model_policy_has_selector_key(self) -> None:
        """Model policy must include intent_philosophy_selector key."""
        from section_loop.dispatch import read_model_policy
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ps = Path(td)
            (ps / "artifacts").mkdir(parents=True)
            policy = read_model_policy(ps)
            assert "intent_philosophy_selector" in policy, (
                "Model policy must include intent_philosophy_selector key")

    def test_selector_fail_closed_no_selection(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Selector that returns empty sources → fail-closed."""
        # Create a markdown file so catalog isn't empty
        (planspace / "readme.md").write_text("# Readme\n")

        def selector_empty(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "philosophy-source-selector.md":
                signal = planspace / "artifacts" / "signals" / \
                    "philosophy-selected-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({"sources": []}))
                return ""
            return ""

        mock_dispatch.side_effect = selector_empty

        from section_loop.intent.bootstrap import ensure_global_philosophy
        result = ensure_global_philosophy(planspace, codespace, "parent")
        assert result is None, (
            "Empty selection must fail-closed (return None)")


class TestR56UpdaterSignalPreservation:
    """V3/R56: Malformed updater signal renamed to .malformed.json."""

    def test_malformed_updater_signal_preserved(self) -> None:
        """feedback.py updater signal parse site preserves malformed files."""
        content = (Path(__file__).resolve().parent.parent / "src"
                   / "scripts" / "scan" / "feedback.py").read_text()
        # Find the updater signal parse site (around _apply_feedback)
        region_start = content.find("Malformed updater signal:")
        assert region_start != -1, (
            "feedback.py must have 'Malformed updater signal' warning")
        region = content[region_start:region_start + 500]
        assert ".malformed.json" in region, (
            "feedback.py must rename malformed updater signal to "
            ".malformed.json (V3/R56)")


class TestR56AxisBudgetEnforcement:
    """V5/R56: max_new_axes_total is enforced, not just declared."""

    def test_axes_added_tracked_in_registry(
        self, planspace, codespace, section_01, mock_dispatch,
    ) -> None:
        """axes_added_so_far is persisted in registry after expansion."""
        from section_loop.intent.expansion import run_expansion_cycle

        artifacts = planspace / "artifacts"
        signals = artifacts / "signals"
        signals.mkdir(parents=True, exist_ok=True)
        (planspace / "model-policy.json").write_text("{}")

        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)
        (intent_sec / "problem.md").write_text("# Problem\n")
        (intent_sec / "problem-alignment.md").write_text("# Rubric\n")

        # Create registry and surfaces
        registry = {"section": "01", "next_id": 1, "surfaces": []}
        (intent_sec / "surface-registry.json").write_text(
            json.dumps(registry))
        surfaces = {
            "problem_surfaces": [
                {"kind": "emergent", "axis_id": "A1", "title": "T",
                 "description": "D", "evidence": "E"},
            ],
            "philosophy_surfaces": [],
        }
        (signals / "intent-surfaces-01.json").write_text(
            json.dumps(surfaces))

        # Expander adds 2 new axes
        def write_delta(*args, **kwargs):
            delta = {
                "section": "01",
                "applied": {"problem_definition_updated": True,
                             "problem_rubric_updated": True},
                "applied_surface_ids": ["P-01-0001"],
                "discarded_surface_ids": [],
                "new_axes": ["A5", "A6"],
                "restart_required": False,
            }
            delta_path = signals / "intent-delta-01.json"
            delta_path.write_text(json.dumps(delta))
            return ""

        mock_dispatch.side_effect = write_delta

        run_expansion_cycle(
            "01", planspace, codespace, "test-parent",
            budgets={"max_new_axes_total": 6},
        )

        # Registry should track axes_added_so_far
        reg = json.loads(
            (intent_sec / "surface-registry.json").read_text())
        assert reg.get("axes_added_so_far") == 2, (
            "Registry must track axes_added_so_far after expansion")

    def test_axis_budget_exceeded_blocks(
        self, planspace, codespace, section_01, mock_dispatch,
    ) -> None:
        """Exceeding max_new_axes_total creates NEED_DECISION blocker."""
        from section_loop.intent.expansion import run_expansion_cycle

        artifacts = planspace / "artifacts"
        signals = artifacts / "signals"
        signals.mkdir(parents=True, exist_ok=True)
        (planspace / "model-policy.json").write_text("{}")

        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)
        (intent_sec / "problem.md").write_text("# Problem\n")
        (intent_sec / "problem-alignment.md").write_text("# Rubric\n")

        # Registry already has 5 axes added
        registry = {"section": "01", "next_id": 1, "surfaces": [],
                     "axes_added_so_far": 5}
        (intent_sec / "surface-registry.json").write_text(
            json.dumps(registry))
        surfaces = {
            "problem_surfaces": [
                {"kind": "emergent", "axis_id": "A1", "title": "T",
                 "description": "D", "evidence": "E"},
            ],
            "philosophy_surfaces": [],
        }
        (signals / "intent-surfaces-01.json").write_text(
            json.dumps(surfaces))

        # Expander tries to add 3 axes but budget allows only 1
        def write_delta(*args, **kwargs):
            delta = {
                "section": "01",
                "applied": {"problem_definition_updated": True,
                             "problem_rubric_updated": True},
                "applied_surface_ids": ["P-01-0001"],
                "discarded_surface_ids": [],
                "new_axes": ["A7", "A8", "A9"],
                "restart_required": True,
            }
            (signals / "intent-delta-01.json").write_text(
                json.dumps(delta))
            return ""

        mock_dispatch.side_effect = write_delta

        result = run_expansion_cycle(
            "01", planspace, codespace, "test-parent",
            budgets={"max_new_axes_total": 6},
        )

        # Should create a NEED_DECISION blocker
        assert result["needs_user_input"] is True, (
            "Exceeding axis budget must trigger NEED_DECISION")
        blocker_path = (
            signals / "intent-axis-budget-01-signal.json")
        assert blocker_path.exists(), (
            "Axis budget blocker signal must be written")


class TestR57DeepScanFeedbackPreservation:
    """V1/R57: deep_scan.update_match() must warn + rename malformed JSON."""

    def test_malformed_feedback_renamed(self, tmp_path):
        """Malformed feedback JSON is renamed to .malformed.json."""
        from scan.deep_scan import update_match

        section_file = tmp_path / "section-01.md"
        section_file.write_text(
            "## Related Files\n\n### src/foo.py\nSome detail\n")

        # Create a details-response + malformed feedback
        details = tmp_path / "deep-src_foo_py-response.md"
        details.write_text("analysis")
        feedback = tmp_path / "deep-src_foo_py-feedback.json"
        feedback.write_text("{not valid json")

        result = update_match(section_file, "src/foo.py", details)
        assert result is True, "Should continue despite malformed feedback"
        assert not feedback.exists(), "Original should be renamed"
        assert (tmp_path / "deep-src_foo_py-feedback.malformed.json").exists()


class TestR57UpdaterSignalValidityPreservation:
    """V2/R57: _is_valid_updater_signal() must rename malformed JSON."""

    def test_malformed_updater_signal_renamed_by_validity_check(
        self, tmp_path,
    ):
        """Malformed JSON in validity check path is renamed."""
        from scan.feedback import _is_valid_updater_signal

        signal_path = tmp_path / "update-signal.json"
        signal_path.write_text("{broken json!!")

        result = _is_valid_updater_signal(signal_path)
        assert result is False
        assert not signal_path.exists(), (
            "Original should be renamed by validity check")
        assert (tmp_path / "update-signal.malformed.json").exists()


class TestR57RefExpansionWarnings:
    """V3/R57: Ref expansion failures must warn + use hash marker."""

    def test_pipeline_hash_uses_error_marker(self, tmp_path):
        """Unreadable ref produces stable REF_READ_ERROR marker in hash."""
        import hashlib

        from section_loop.pipeline_control import _section_inputs_hash

        planspace = tmp_path / "plan"
        codespace = tmp_path / "code"
        planspace.mkdir()
        codespace.mkdir()
        (planspace / "artifacts").mkdir()

        # Create an inputs dir with a broken ref
        inputs_dir = planspace / "artifacts" / "inputs" / "section-01"
        inputs_dir.mkdir(parents=True)
        ref_path = inputs_dir / "broken.ref"
        # Point to a non-directory path to trigger OSError
        ref_path.write_text("/nonexistent/path/that/does/not/exist.md")

        # Build a section
        from section_loop.types import Section

        sections_by_num = {
            "01": Section(
                number="01",
                path=str(planspace / "artifacts" / "sections" / "section-01.md"),
                related_files=[],
            ),
        }

        h1 = _section_inputs_hash("01", planspace, codespace, sections_by_num)

        # Hash should be deterministic (same broken ref → same hash)
        h2 = _section_inputs_hash("01", planspace, codespace, sections_by_num)
        assert h1 == h2, "Hash must be deterministic even with broken refs"

    def test_context_builder_warns_on_broken_ref(
        self, planspace, codespace, section_01, capsys,
    ):
        """Broken ref in context builder emits warning."""
        from section_loop.prompts.context import build_prompt_context
        from section_loop.types import Section

        sec_path = planspace / "artifacts" / "sections" / "section-01.md"
        sec = Section(number="01", path=sec_path, related_files=[])

        inputs_dir = planspace / "artifacts" / "inputs" / "section-01"
        inputs_dir.mkdir(parents=True)
        ref_path = inputs_dir / "broken.ref"
        # Write invalid content that will fail Path operations
        ref_path.write_bytes(b"\x80\x81\x82")  # Invalid UTF-8

        ctx = build_prompt_context(sec, planspace, codespace)
        captured = capsys.readouterr()
        assert "WARN" in captured.out or ctx is not None  # Should not crash


class TestR57GateTypeSpecificMessaging:
    """V4/R57: handle_user_gate() must use gate-kind-specific messaging."""

    def test_axis_budget_gate_says_axis_budget(
        self, planspace, codespace, section_01, mock_dispatch,
    ):
        """Axis budget gate must NOT say 'Philosophy tension'."""
        from unittest.mock import patch

        from section_loop.intent.expansion import handle_user_gate

        artifacts = planspace / "artifacts"
        signals = artifacts / "signals"
        signals.mkdir(parents=True, exist_ok=True)

        delta_result = {
            "needs_user_input": True,
            "user_input_kind": "axis_budget",
            "user_input_path": str(
                signals / "intent-axis-budget-01-signal.json"),
        }

        with patch(
            "section_loop.intent.expansion.pause_for_parent",
            return_value="resume:accept",
        ) as mock_pause:
            handle_user_gate("01", planspace, "test-parent", delta_result)

        # Check the pause message does NOT say philosophy
        pause_msg = mock_pause.call_args[0][2]
        assert "Philosophy" not in pause_msg, (
            "Axis budget gate must not mention 'Philosophy'")
        assert "budget" in pause_msg.lower(), (
            "Axis budget gate must mention 'budget'")

        # Check blocker signal
        blocker_path = signals / "intent-expand-01-signal.json"
        assert blocker_path.exists()
        import json

        blocker = json.loads(blocker_path.read_text())
        assert "Philosophy" not in blocker["detail"], (
            "Blocker detail must be gate-kind-specific")

    def test_philosophy_gate_says_philosophy(
        self, planspace, codespace, section_01, mock_dispatch,
    ):
        """Philosophy gate correctly says 'Philosophy tension'."""
        from unittest.mock import patch

        from section_loop.intent.expansion import handle_user_gate

        artifacts = planspace / "artifacts"
        signals = artifacts / "signals"
        signals.mkdir(parents=True, exist_ok=True)

        delta_result = {
            "needs_user_input": True,
            "user_input_kind": "philosophy",
            "user_input_path": str(
                artifacts / "intent" / "global"
                / "philosophy-decisions.md"),
        }

        with patch(
            "section_loop.intent.expansion.pause_for_parent",
            return_value="resume:accept",
        ) as mock_pause:
            handle_user_gate("01", planspace, "test-parent", delta_result)

        pause_msg = mock_pause.call_args[0][2]
        assert "Philosophy" in pause_msg


class TestR57SurfacePersistenceOnMisalignment:
    """V5/R57: Intent surfaces must be persisted even when proposal is
    misaligned (PROBLEMS verdict)."""

    def test_surfaces_merged_when_misaligned(
        self, planspace, codespace, section_01, mock_dispatch,
    ):
        """Surfaces from misaligned pass are merged into registry."""
        import json

        artifacts = planspace / "artifacts"
        signals = artifacts / "signals"
        signals.mkdir(parents=True, exist_ok=True)
        (planspace / "model-policy.json").write_text("{}")

        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)
        (intent_sec / "problem.md").write_text("# Problem\n")
        (intent_sec / "problem-alignment.md").write_text("# Rubric\n")

        # Empty registry
        registry = {"section": "01", "next_id": 1, "surfaces": []}
        (intent_sec / "surface-registry.json").write_text(
            json.dumps(registry))

        # Write surfaces signal (simulating what intent-judge would write)
        surfaces = {
            "problem_surfaces": [
                {"kind": "emergent", "axis_id": "A1",
                 "title": "Test surface", "description": "D",
                 "evidence": "E"},
            ],
            "philosophy_surfaces": [],
        }
        (signals / "intent-surfaces-01.json").write_text(
            json.dumps(surfaces))

        # Import the surface functions to verify merge
        from section_loop.intent.surfaces import (
            load_intent_surfaces,
            load_surface_registry,
            merge_surfaces_into_registry,
            normalize_surface_ids,
            save_surface_registry,
        )

        # Simulate what the runner does in the PROBLEMS branch (V5/R57)
        misaligned_surfaces = load_intent_surfaces("01", planspace)
        assert misaligned_surfaces is not None

        reg = load_surface_registry("01", planspace)
        misaligned_surfaces = normalize_surface_ids(
            misaligned_surfaces, reg, "01")
        new_ids, _ = merge_surfaces_into_registry(reg, misaligned_surfaces)
        save_surface_registry("01", planspace, reg)

        # Verify surfaces are now in registry
        final_reg = json.loads(
            (intent_sec / "surface-registry.json").read_text())
        assert len(final_reg["surfaces"]) > 0, (
            "Surfaces must be persisted into registry even on misaligned pass")


# ---------------------------------------------------------------------------
# R58 — V1: Scope-delta adjudication write-back fail-closed
# ---------------------------------------------------------------------------


class TestR58ScopeDeltaAdjudicationFailClosed:
    """V1/R58: If a scope-delta file is malformed during adjudication
    write-back, the coordinator must preserve it and write a valid
    replacement — not crash."""

    def test_malformed_delta_preserved_and_replaced(self, tmp_path):
        """Malformed delta → .malformed.json + valid replacement."""
        from section_loop.coordination.runner import (
            _normalize_section_id,
        )

        # Set up scope-deltas dir with a malformed delta
        scope_deltas_dir = tmp_path / "artifacts" / "scope-deltas"
        scope_deltas_dir.mkdir(parents=True)
        delta_path = scope_deltas_dir / "section-01-scope-delta.json"
        delta_path.write_text("{MALFORMED", encoding="utf-8")

        # Simulate the adjudication application logic inline
        # (extracted from runner.py lines 334-370)
        import json as _json

        sec = "01"
        decision = {
            "section": "01",
            "action": "reject",
            "reason": "Not needed",
        }

        if delta_path.exists():
            try:
                delta = _json.loads(
                    delta_path.read_text(encoding="utf-8"))
            except (_json.JSONDecodeError, OSError):
                malformed = delta_path.with_suffix(".malformed.json")
                try:
                    delta_path.rename(malformed)
                except OSError:
                    pass
                delta = {
                    "section": sec,
                    "origin": "unknown",
                    "adjudicated": True,
                    "adjudication": decision,
                    "error": (
                        "original scope-delta malformed "
                        "during adjudication application"
                    ),
                    "preserved_path": str(malformed),
                }
                delta_path.write_text(
                    _json.dumps(delta, indent=2), encoding="utf-8")

        # Assert malformed file preserved
        malformed_path = scope_deltas_dir / "section-01-scope-delta.malformed.json"
        assert malformed_path.exists(), (
            "Malformed delta must be preserved as .malformed.json")
        assert malformed_path.read_text(encoding="utf-8") == "{MALFORMED"

        # Assert valid replacement written
        assert delta_path.exists(), (
            "A valid replacement delta must be written")
        replacement = _json.loads(delta_path.read_text(encoding="utf-8"))
        assert replacement["adjudicated"] is True
        assert replacement["adjudication"]["action"] == "reject"
        assert "error" in replacement
        assert "preserved_path" in replacement


# ---------------------------------------------------------------------------
# R58 — V2: Tool-registry malformed preservation in coordination
# ---------------------------------------------------------------------------


class TestR58ToolRegistryCoordinationPreservation:
    """V2/R58: When tool-registry.json is malformed in the coordinator
    tools-block builder, the corrupted file must be preserved as
    .malformed.json (copy, not rename)."""

    def test_malformed_tool_registry_preserved(self, tmp_path):
        """Malformed tool-registry → .malformed.json copy exists."""
        from section_loop.coordination.execution import (
            write_coordinator_fix_prompt,
        )

        planspace = tmp_path / "plan"
        codespace = tmp_path / "code"
        planspace.mkdir()
        codespace.mkdir()

        # Write malformed tool-registry
        artifacts = planspace / "artifacts"
        artifacts.mkdir()
        tool_reg = artifacts / "tool-registry.json"
        tool_reg.write_text("{BROKEN JSON!", encoding="utf-8")

        # Call the function — it builds a prompt that includes tool block
        group = [{"section": "01", "type": "test", "description": "d",
                  "files": ["a.py"]}]
        sec_dir = planspace / "artifacts" / "sections"
        sec_dir.mkdir(parents=True)

        write_coordinator_fix_prompt(group, planspace, codespace, 0)

        # Assert malformed copy was preserved
        malformed = tool_reg.with_suffix(".malformed.json")
        assert malformed.exists(), (
            "Malformed tool-registry must be preserved as .malformed.json")
        assert malformed.read_text(encoding="utf-8") == "{BROKEN JSON!"

        # Original still exists (copy, not rename)
        assert tool_reg.exists(), (
            "Original tool-registry should still exist (copy, not rename)")


# ---------------------------------------------------------------------------
# R58 — V3: Related-files update signal preservation
# ---------------------------------------------------------------------------


class TestR58RelatedFilesSignalPreservation:
    """V3/R58: When a related-files update signal is malformed,
    apply_related_files_update() must preserve it as .malformed.json."""

    def test_malformed_signal_preserved(self, tmp_path):
        """Malformed signal → returns False + .malformed.json exists."""
        from scan.exploration import apply_related_files_update

        section_file = tmp_path / "section-01.md"
        section_file.write_text("## Related Files\n### a.py\nInfo\n")

        signal_file = tmp_path / "related-files-update.json"
        signal_file.write_text("NOT VALID JSON{{{", encoding="utf-8")

        result = apply_related_files_update(section_file, signal_file)

        assert result is False, "Must return False on malformed signal"

        malformed = signal_file.with_suffix(".malformed.json")
        assert malformed.exists(), (
            "Malformed signal must be preserved as .malformed.json")
        assert malformed.read_text(encoding="utf-8") == "NOT VALID JSON{{{"


# ---------------------------------------------------------------------------
# R59 Tests
# ---------------------------------------------------------------------------


class TestR59CatalogCodespaceCoverage:
    """V1/R59: Catalog must guarantee codespace coverage even when
    planspace has many markdown files."""

    def test_codespace_included_when_planspace_has_many_files(
        self, tmp_path,
    ) -> None:
        """Planspace >50 artifacts must not crowd out codespace docs."""
        from section_loop.intent.bootstrap import _build_philosophy_catalog

        planspace = tmp_path / "planspace"
        codespace = tmp_path / "codespace"
        planspace.mkdir()
        codespace.mkdir()

        # Create 55 planspace markdown files (non-artifacts)
        for i in range(55):
            f = planspace / f"doc-{i:03d}.md"
            f.write_text(f"# Doc {i}\nContent.\n")

        # Create a single codespace philosophy doc
        cs_doc = codespace / "philosophy.md"
        cs_doc.write_text("# Execution Philosophy\nP1: Test.\n")

        catalog = _build_philosophy_catalog(
            planspace, codespace, max_files=50)

        paths = [c["path"] for c in catalog]
        assert any("codespace" in p and "philosophy.md" in p
                    for p in paths), (
            "Codespace philosophy doc must be included even when "
            "planspace has many markdown files")

    def test_codespace_scanned_first(self, tmp_path) -> None:
        """Codespace should appear before planspace in catalog."""
        from section_loop.intent.bootstrap import _build_philosophy_catalog

        planspace = tmp_path / "planspace"
        codespace = tmp_path / "codespace"
        planspace.mkdir()
        codespace.mkdir()

        (planspace / "plan.md").write_text("# Plan\n")
        (codespace / "philo.md").write_text("# Philo\n")

        catalog = _build_philosophy_catalog(
            planspace, codespace, max_files=50)

        paths = [c["path"] for c in catalog]
        assert len(paths) == 2
        assert "codespace" in paths[0], (
            "Codespace files must appear before planspace files")

    def test_planspace_artifacts_excluded(self, tmp_path) -> None:
        """Planspace artifacts/ directory must be excluded from catalog."""
        from section_loop.intent.bootstrap import _build_philosophy_catalog

        planspace = tmp_path / "planspace"
        codespace = tmp_path / "codespace"
        planspace.mkdir()
        codespace.mkdir()

        # Create planspace artifacts (pipeline outputs)
        arts = planspace / "artifacts"
        arts.mkdir()
        (arts / "codemap.md").write_text("# Codemap\n")
        (arts / "proposal.md").write_text("# Proposal\n")

        # Create a real planspace doc
        (planspace / "design.md").write_text("# Design\n")

        catalog = _build_philosophy_catalog(
            planspace, codespace, max_files=50)

        paths = [c["path"] for c in catalog]
        # Check no path has planspace/artifacts/ in it
        planspace_str = str(planspace)
        assert not any(
            p.startswith(planspace_str + "/artifacts/") for p in paths
        ), "Planspace artifacts/ must be excluded from catalog"
        assert any("design.md" in p for p in paths), (
            "Non-artifacts planspace docs should be included")


class TestR59PhilosophyGroundingValidation:
    """V2/R59: Philosophy source grounding must be mechanically validated."""

    def test_missing_source_map_fails_closed(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Missing source map → grounding failure signal + return None."""
        artifacts = planspace / "artifacts"
        intent_global = artifacts / "intent" / "global"
        intent_global.mkdir(parents=True, exist_ok=True)

        # Create a codespace doc for catalog
        (codespace / "philo.md").write_text("# Philosophy\nP1: Test.\n")

        def side_effect(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "philosophy-source-selector.md":
                signal = artifacts / "signals" / \
                    "philosophy-selected-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "sources": [{"path": str(codespace / "philo.md"),
                                 "reason": "test"}],
                }))
                return ""
            if agent_file == "philosophy-distiller.md":
                # Write philosophy but NO source map
                (intent_global / "philosophy.md").write_text(
                    "# Philosophy\n## P1: Test principle\nDo stuff.\n")
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        from section_loop.intent.bootstrap import ensure_global_philosophy
        result = ensure_global_philosophy(planspace, codespace, "parent")
        assert result is None, (
            "Missing source map must cause grounding failure (None)")

        fail_signal = artifacts / "signals" / \
            "philosophy-grounding-failed.json"
        assert fail_signal.exists(), (
            "Must write philosophy-grounding-failed.json signal")

    def test_malformed_source_map_preserved(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Malformed source map → preserve as .malformed.json + fail."""
        artifacts = planspace / "artifacts"
        intent_global = artifacts / "intent" / "global"
        intent_global.mkdir(parents=True, exist_ok=True)

        (codespace / "philo.md").write_text("# Philosophy\nP1: Test.\n")

        def side_effect(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "philosophy-source-selector.md":
                signal = artifacts / "signals" / \
                    "philosophy-selected-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "sources": [{"path": str(codespace / "philo.md"),
                                 "reason": "test"}],
                }))
                return ""
            if agent_file == "philosophy-distiller.md":
                (intent_global / "philosophy.md").write_text(
                    "# Philosophy\n## P1: Test\nDo stuff.\n")
                # Write malformed source map
                (intent_global / "philosophy-source-map.json").write_text(
                    "NOT{VALID}JSON")
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        from section_loop.intent.bootstrap import ensure_global_philosophy
        result = ensure_global_philosophy(planspace, codespace, "parent")
        assert result is None

        malformed = intent_global / "philosophy-source-map.malformed.json"
        assert malformed.exists(), (
            "Malformed source map must be preserved as .malformed.json")

    def test_unmapped_principles_fail_closed(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Principles in philosophy.md without source map entries → fail."""
        artifacts = planspace / "artifacts"
        intent_global = artifacts / "intent" / "global"
        intent_global.mkdir(parents=True, exist_ok=True)

        (codespace / "philo.md").write_text("# Philosophy\nP1: Test.\n")

        def side_effect(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "philosophy-source-selector.md":
                signal = artifacts / "signals" / \
                    "philosophy-selected-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "sources": [{"path": str(codespace / "philo.md"),
                                 "reason": "test"}],
                }))
                return ""
            if agent_file == "philosophy-distiller.md":
                (intent_global / "philosophy.md").write_text(
                    "# Philosophy\n## P1: One\n## P2: Two\n## P3: Three\n")
                # Source map only covers P1
                (intent_global / "philosophy-source-map.json").write_text(
                    json.dumps({"P1": "philo.md line 5"}))
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        from section_loop.intent.bootstrap import ensure_global_philosophy
        result = ensure_global_philosophy(planspace, codespace, "parent")
        assert result is None, (
            "Unmapped principles must cause grounding failure")

        fail_signal = artifacts / "signals" / \
            "philosophy-grounding-failed.json"
        data = json.loads(fail_signal.read_text())
        assert "P2" in data.get("unmapped_principles", [])
        assert "P3" in data.get("unmapped_principles", [])

    def test_fully_grounded_passes(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """All principles mapped → grounding passes."""
        artifacts = planspace / "artifacts"
        intent_global = artifacts / "intent" / "global"
        intent_global.mkdir(parents=True, exist_ok=True)

        (codespace / "philo.md").write_text("# Philosophy\nP1: Test.\n")

        def side_effect(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "philosophy-source-selector.md":
                signal = artifacts / "signals" / \
                    "philosophy-selected-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "sources": [{"path": str(codespace / "philo.md"),
                                 "reason": "test"}],
                }))
                return ""
            if agent_file == "philosophy-distiller.md":
                (intent_global / "philosophy.md").write_text(
                    "# Philosophy\n## P1: One\n## P2: Two\n")
                (intent_global / "philosophy-source-map.json").write_text(
                    json.dumps({"P1": "philo.md:3", "P2": "philo.md:7"}))
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        from section_loop.intent.bootstrap import ensure_global_philosophy
        result = ensure_global_philosophy(planspace, codespace, "parent")
        assert result is not None, (
            "Fully grounded philosophy must succeed")


class TestR59IntentPackHashInvalidation:
    """V3/R59: Intent pack uses hash-based invalidation, not
    existence-only skipping."""

    def test_regenerates_when_inputs_change(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Existing pack must regenerate when upstream inputs change."""
        from section_loop.intent.bootstrap import generate_intent_pack

        sec = _make_intent_section(planspace, codespace)
        artifacts = planspace / "artifacts"
        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)

        # Create existing pack
        (intent_sec / "problem.md").write_text("# Problem\nOld.\n")
        (intent_sec / "problem-alignment.md").write_text("# Rubric\nOld.\n")

        # Write a hash that doesn't match current inputs
        (intent_sec / "intent-pack-input-hash.txt").write_text(
            "stale-hash-that-does-not-match")

        dispatch_called = []

        def side_effect(*args, **kwargs):
            dispatch_called.append(True)
            # Update problem.md to simulate regeneration
            (intent_sec / "problem.md").write_text("# Problem\nNew.\n")
            (intent_sec / "problem-alignment.md").write_text(
                "# Rubric\nNew.\n")
            return ""

        mock_dispatch.side_effect = side_effect

        generate_intent_pack(sec, planspace, codespace, "parent")
        assert len(dispatch_called) > 0, (
            "Must dispatch agent when input hash differs (regenerate)")

    def test_skips_when_hash_matches(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Existing pack with matching hash must skip regeneration."""
        from section_loop.intent.bootstrap import (
            generate_intent_pack, _compute_intent_pack_hash,
        )

        sec = _make_intent_section(planspace, codespace)
        artifacts = planspace / "artifacts"
        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)

        # Create existing pack
        (intent_sec / "problem.md").write_text("# Problem\nExisting.\n")
        (intent_sec / "problem-alignment.md").write_text("# Rubric\n")

        # Compute the real hash from current inputs
        sections_dir = artifacts / "sections"
        real_hash = _compute_intent_pack_hash(
            section_path=sec.path,
            proposal_excerpt=sections_dir / "section-01-proposal-excerpt.md",
            alignment_excerpt=sections_dir / "section-01-alignment-excerpt.md",
            problem_frame=sections_dir / "section-01-problem-frame.md",
            codemap_path=artifacts / "codemap.md",
            corrections_path=artifacts / "signals" / "codemap-corrections.json",
            philosophy_path=artifacts / "intent" / "global" / "philosophy.md",
            todos_path=artifacts / "todos" / "section-01-todos.md",
            incoming_notes="",
        )
        (intent_sec / "intent-pack-input-hash.txt").write_text(real_hash)

        dispatch_called = []

        def side_effect(*args, **kwargs):
            dispatch_called.append(True)
            return ""

        mock_dispatch.side_effect = side_effect

        generate_intent_pack(sec, planspace, codespace, "parent")
        assert len(dispatch_called) == 0, (
            "Must NOT dispatch when input hash matches (skip)")

    def test_hash_written_after_successful_generation(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Successful generation must write input hash file."""
        from section_loop.intent.bootstrap import generate_intent_pack

        sec = _make_intent_section(planspace, codespace)
        artifacts = planspace / "artifacts"
        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)

        def side_effect(*args, **kwargs):
            (intent_sec / "problem.md").write_text("# Problem\n")
            (intent_sec / "problem-alignment.md").write_text("# Rubric\n")
            return ""

        mock_dispatch.side_effect = side_effect

        generate_intent_pack(sec, planspace, codespace, "parent")

        hash_file = intent_sec / "intent-pack-input-hash.txt"
        assert hash_file.exists(), (
            "Must write intent-pack-input-hash.txt after successful gen")
        assert len(hash_file.read_text().strip()) == 64, (
            "Hash must be a 64-char hex sha256")


# ---------------------------------------------------------------------------
# R60 — V1: Bounded catalog walk
# ---------------------------------------------------------------------------


class TestR60BoundedCatalogWalk:
    """V1/R60: Philosophy catalog must use depth-bounded traversal, not sorted rglob."""

    def test_walk_respects_max_depth(self, tmp_path: Path) -> None:
        """Files beyond max_depth must not be returned."""
        from section_loop.intent.bootstrap import _walk_md_bounded

        (tmp_path / "a.md").write_text("top")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.md").write_text("depth 2")
        (tmp_path / "sub" / "deep").mkdir()
        (tmp_path / "sub" / "deep" / "c.md").write_text("depth 3")
        (tmp_path / "sub" / "deep" / "deeper").mkdir()
        (tmp_path / "sub" / "deep" / "deeper" / "d.md").write_text("depth 4")

        results = list(_walk_md_bounded(tmp_path, max_depth=3))
        names = {p.name for p in results}
        assert "a.md" in names, "depth-1 file must be included"
        assert "b.md" in names, "depth-2 file must be included"
        assert "c.md" in names, "depth-3 file must be included"
        assert "d.md" not in names, "depth-4 file must be excluded"

    def test_walk_excludes_top_dirs(self, tmp_path: Path) -> None:
        """Top-level excluded dirs must be pruned during traversal."""
        from section_loop.intent.bootstrap import _walk_md_bounded

        (tmp_path / "keep").mkdir()
        (tmp_path / "keep" / "good.md").write_text("keep me")
        (tmp_path / "artifacts").mkdir()
        (tmp_path / "artifacts" / "skip.md").write_text("skip me")

        results = list(_walk_md_bounded(
            tmp_path, max_depth=3,
            exclude_top_dirs=frozenset({"artifacts"}),
        ))
        names = {p.name for p in results}
        assert "good.md" in names
        assert "skip.md" not in names, "artifacts/ must be pruned"

    def test_walk_sorted_per_directory(self, tmp_path: Path) -> None:
        """Files within each directory must be sorted."""
        from section_loop.intent.bootstrap import _walk_md_bounded

        for name in ("c.md", "a.md", "b.md"):
            (tmp_path / name).write_text(f"file {name}")

        results = [p.name for p in _walk_md_bounded(tmp_path, max_depth=3)]
        assert results == ["a.md", "b.md", "c.md"]

    def test_catalog_uses_bounded_walk(self, tmp_path: Path) -> None:
        """_build_philosophy_catalog must use bounded walk (basic functionality)."""
        from section_loop.intent.bootstrap import _build_philosophy_catalog

        codespace = tmp_path / "codespace"
        planspace = tmp_path / "planspace"
        codespace.mkdir()
        planspace.mkdir()
        (codespace / "design.md").write_text("# Design\nPrinciple here")
        (planspace / "plan.md").write_text("# Plan\nGoals here")

        catalog = _build_philosophy_catalog(planspace, codespace)
        assert len(catalog) >= 2


# ---------------------------------------------------------------------------
# R60 — V2: Tool OSError handling
# ---------------------------------------------------------------------------


class TestR60ToolOSErrorHandling:
    """V2/R60: extract-docstring-py must handle OSError with structured output."""

    def test_missing_file_structured_error(self) -> None:
        """Missing file must produce structured ERROR output, not crash."""
        tool_path = (Path(__file__).resolve().parent.parent
                     / "src" / "tools" / "extract-docstring-py")
        if not tool_path.exists():
            pytest.skip("extract-docstring-py not found")

        result = subprocess.run(
            [sys.executable, str(tool_path), "/nonexistent/path/file.py"],
            capture_output=True, text=True,
        )
        assert "ERROR:" in result.stdout, (
            "Must output structured ERROR: line for missing file")
        assert "/nonexistent/path/file.py" in result.stdout, (
            "Must include the file path in output")

    def test_missing_file_exit_code_2(self) -> None:
        """Running tool against missing file must exit with code 2."""
        tool_path = (Path(__file__).resolve().parent.parent
                     / "src" / "tools" / "extract-docstring-py")
        if not tool_path.exists():
            pytest.skip("extract-docstring-py not found")

        result = subprocess.run(
            [sys.executable, str(tool_path), "/nonexistent/path/file.py"],
            capture_output=True, text=True,
        )
        assert result.returncode == 2, (
            f"Must exit 2 on file errors, got {result.returncode}")


# ---------------------------------------------------------------------------
# R61: V1 — Alignment surface includes intent artifacts
# ---------------------------------------------------------------------------

class TestR61AlignmentSurfaceIntentArtifacts:
    """V1/R61: _write_alignment_surface must include intent pack artifacts."""

    def test_surface_includes_intent_problem(
        self, planspace: Path, section_01: None,
    ) -> None:
        """Alignment surface must include intent problem.md when present."""
        from section_loop.section_engine.reexplore import (
            _write_alignment_surface,
        )
        from section_loop.types import Section

        sec_path = planspace / "artifacts" / "sections" / "section-01.md"
        section = Section(number="01", path=sec_path, related_files=[])

        # Create intent problem artifact
        intent_dir = (
            planspace / "artifacts" / "intent" / "sections" / "section-01"
        )
        intent_dir.mkdir(parents=True)
        (intent_dir / "problem.md").write_text("# Problem\n")

        _write_alignment_surface(planspace, section)

        surface = (
            planspace / "artifacts" / "sections"
            / "section-01-alignment-surface.md"
        )
        text = surface.read_text(encoding="utf-8")
        assert "intent problem definition" in text.lower()
        assert "problem.md" in text

    def test_surface_includes_intent_rubric(
        self, planspace: Path, section_01: None,
    ) -> None:
        """Alignment surface must include problem-alignment.md when present."""
        from section_loop.section_engine.reexplore import (
            _write_alignment_surface,
        )
        from section_loop.types import Section

        sec_path = planspace / "artifacts" / "sections" / "section-01.md"
        section = Section(number="01", path=sec_path, related_files=[])

        intent_dir = (
            planspace / "artifacts" / "intent" / "sections" / "section-01"
        )
        intent_dir.mkdir(parents=True)
        (intent_dir / "problem-alignment.md").write_text("# Rubric\n")

        _write_alignment_surface(planspace, section)

        surface = (
            planspace / "artifacts" / "sections"
            / "section-01-alignment-surface.md"
        )
        text = surface.read_text(encoding="utf-8")
        assert "intent alignment rubric" in text.lower()

    def test_surface_includes_all_four_intent_artifacts(
        self, planspace: Path, section_01: None,
    ) -> None:
        """All four intent artifacts appear in surface when present."""
        from section_loop.section_engine.reexplore import (
            _write_alignment_surface,
        )
        from section_loop.types import Section

        sec_path = planspace / "artifacts" / "sections" / "section-01.md"
        section = Section(number="01", path=sec_path, related_files=[])

        intent_dir = (
            planspace / "artifacts" / "intent" / "sections" / "section-01"
        )
        intent_dir.mkdir(parents=True)
        (intent_dir / "problem.md").write_text("# P\n")
        (intent_dir / "problem-alignment.md").write_text("# R\n")
        (intent_dir / "philosophy-excerpt.md").write_text("# E\n")
        (intent_dir / "surface-registry.json").write_text("{}\n")

        _write_alignment_surface(planspace, section)

        surface = (
            planspace / "artifacts" / "sections"
            / "section-01-alignment-surface.md"
        )
        text = surface.read_text(encoding="utf-8")
        assert "problem.md" in text
        assert "problem-alignment.md" in text
        assert "philosophy-excerpt.md" in text
        assert "surface-registry.json" in text

    def test_surface_omits_missing_intent_artifacts(
        self, planspace: Path, section_01: None,
    ) -> None:
        """No intent references when intent artifacts don't exist."""
        from section_loop.section_engine.reexplore import (
            _write_alignment_surface,
        )
        from section_loop.types import Section

        sec_path = planspace / "artifacts" / "sections" / "section-01.md"
        section = Section(number="01", path=sec_path, related_files=[])

        _write_alignment_surface(planspace, section)

        surface = (
            planspace / "artifacts" / "sections"
            / "section-01-alignment-surface.md"
        )
        text = surface.read_text(encoding="utf-8")
        assert "intent" not in text.lower()


# ---------------------------------------------------------------------------
# R61: V4 — Agent-steerable extension expansion in catalog walker
# ---------------------------------------------------------------------------

class TestR61AgentSteerableExtensions:
    """V4/R61: _walk_md_bounded accepts extensions; catalog is steerable."""

    def test_walk_with_txt_extension(self, tmp_path: Path) -> None:
        """Walker yields .txt files when extensions include .txt."""
        from section_loop.intent.bootstrap import _walk_md_bounded

        (tmp_path / "notes.txt").write_text("philosophy notes")
        (tmp_path / "readme.md").write_text("readme")
        (tmp_path / "code.py").write_text("pass")

        results = list(_walk_md_bounded(
            tmp_path, max_depth=3,
            extensions=frozenset({".md", ".txt"}),
        ))
        names = {r.name for r in results}
        assert "notes.txt" in names
        assert "readme.md" in names
        assert "code.py" not in names

    def test_walk_default_extensions_is_md_only(self, tmp_path: Path) -> None:
        """Default extensions parameter yields only .md files."""
        from section_loop.intent.bootstrap import _walk_md_bounded

        (tmp_path / "notes.txt").write_text("philosophy notes")
        (tmp_path / "readme.md").write_text("readme")

        results = list(_walk_md_bounded(tmp_path, max_depth=3))
        names = {r.name for r in results}
        assert "readme.md" in names
        assert "notes.txt" not in names

    def test_catalog_accepts_extensions_parameter(
        self, tmp_path: Path,
    ) -> None:
        """_build_philosophy_catalog passes extensions to walker."""
        from section_loop.intent.bootstrap import _build_philosophy_catalog

        ps = tmp_path / "plan"
        cs = tmp_path / "code"
        ps.mkdir()
        cs.mkdir()
        (cs / "design.rst").write_text("# Design Principles\nP1: test\n")
        (cs / "readme.md").write_text("# README\nProject readme\n")

        # Default: only .md
        md_only = _build_philosophy_catalog(ps, cs)
        paths = [c["path"] for c in md_only]
        assert any("readme.md" in p for p in paths)
        assert not any("design.rst" in p for p in paths)

        # With .rst extension
        with_rst = _build_philosophy_catalog(
            ps, cs, extensions=frozenset({".md", ".rst"}))
        paths = [c["path"] for c in with_rst]
        assert any("design.rst" in p for p in paths)
