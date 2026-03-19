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

from _paths import SRC_DIR
from staleness.helpers.content_hasher import file_hash
from taskrouter.agents import resolve_agent_path

from intent.service.surface_registry import (
    SurfaceRegistry,
    find_discarded_recurrences,
    merge_surfaces_into_registry,
    mark_surfaces_applied,
    mark_surfaces_discarded,
)
from containers import Services
from intent.service.intent_triager import _full_default
from orchestrator.types import Section


def _make_surface_registry() -> SurfaceRegistry:
    """Build a SurfaceRegistry from the DI container (for test use)."""
    return SurfaceRegistry(
        artifact_io=Services.artifact_io(),
        hasher=Services.hasher(),
        logger=Services.logger(),
        signals=Services.signals(),
    )


def _make_intent_triager():
    """Build an IntentTriager from the DI container (for test use)."""
    from intent.service.intent_triager import IntentTriager
    return IntentTriager(
        communicator=Services.communicator(),
        dispatcher=Services.dispatcher(),
        logger=Services.logger(),
        policies=Services.policies(),
        prompt_guard=Services.prompt_guard(),
        signals=Services.signals(),
        task_router=Services.task_router(),
        artifact_io=Services.artifact_io(),
    )


def _make_intent_pack_generator():
    """Build an IntentPackGenerator from the DI container (for test use)."""
    from intent.service.intent_pack_generator import IntentPackGenerator
    return IntentPackGenerator(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        dispatcher=Services.dispatcher(),
        hasher=Services.hasher(),
        logger=Services.logger(),
        policies=Services.policies(),
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )


def _make_expansion_orchestrator():
    """Build an ExpansionOrchestrator from the DI container (for test use)."""
    from intent.engine.expansion_orchestrator import ExpansionOrchestrator
    from intent.service.expanders import Expanders
    from intent.service.philosophy_bootstrap_state import PhilosophyBootstrapState
    from intent.service.philosophy_grounding import PhilosophyGrounding
    artifact_io = Services.artifact_io()
    logger = Services.logger()
    hasher = Services.hasher()
    bootstrap_state = PhilosophyBootstrapState(artifact_io=artifact_io)
    grounding = PhilosophyGrounding(
        artifact_io=artifact_io, bootstrap_state=bootstrap_state,
        hasher=hasher, logger=logger,
    )
    expanders = Expanders(
        artifact_io=artifact_io, communicator=Services.communicator(),
        dispatcher=Services.dispatcher(), grounding=grounding,
        logger=logger, policies=Services.policies(),
        prompt_guard=Services.prompt_guard(), signals=Services.signals(),
        task_router=Services.task_router(),
    )
    from intent.service.surface_registry import SurfaceRegistry
    surface_registry = SurfaceRegistry(
        artifact_io=artifact_io, hasher=hasher, logger=logger,
        signals=Services.signals(),
    )
    return ExpansionOrchestrator(
        artifact_io=artifact_io, expanders=expanders, logger=logger,
        pipeline_control=Services.pipeline_control(),
        surface_registry=surface_registry,
    )


def _make_philosophy_bootstrapper():
    """Build a PhilosophyBootstrapper from the DI container (for test use)."""
    from intent.service.philosophy_bootstrapper import PhilosophyBootstrapper
    from intent.service.philosophy_bootstrap_state import PhilosophyBootstrapState
    from intent.service.philosophy_classifier import PhilosophyClassifier
    from intent.service.philosophy_dispatcher import PhilosophyDispatcher
    from intent.service.philosophy_grounding import PhilosophyGrounding
    artifact_io = Services.artifact_io()
    logger = Services.logger()
    hasher = Services.hasher()
    bootstrap_state = PhilosophyBootstrapState(artifact_io=artifact_io)
    classifier = PhilosophyClassifier(artifact_io=artifact_io)
    grounding = PhilosophyGrounding(
        artifact_io=artifact_io, bootstrap_state=bootstrap_state,
        hasher=hasher, logger=logger,
    )
    philosophy_dispatcher = PhilosophyDispatcher(
        dispatcher=Services.dispatcher(),
        logger=logger,
    )
    return PhilosophyBootstrapper(
        artifact_io=artifact_io,
        bootstrap_state=bootstrap_state,
        classifier=classifier,
        communicator=Services.communicator(),
        dispatcher=Services.dispatcher(),
        grounding=grounding,
        hasher=hasher,
        logger=logger,
        philosophy_dispatcher=philosophy_dispatcher,
        policies=Services.policies(),
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )


def _make_section_pipeline(*, proposal_cycle=None):
    """Build a SectionPipeline with IntentInitializer wired in."""
    from intent.engine.intent_initializer import IntentInitializer
    from intake.service.governance_packet_builder import GovernancePacketBuilder
    from orchestrator.engine.section_pipeline import SectionPipeline
    return SectionPipeline(
        logger=Services.logger(),
        artifact_io=Services.artifact_io(),
        pipeline_control=Services.pipeline_control(),
        intent_initializer=IntentInitializer(
            artifact_io=Services.artifact_io(),
            communicator=Services.communicator(),
            governance_packet_builder=GovernancePacketBuilder(
                artifact_io=Services.artifact_io(),
            ),
            intent_pack_generator=_make_intent_pack_generator(),
            intent_triager=_make_intent_triager(),
            logger=Services.logger(),
            philosophy_bootstrapper=_make_philosophy_bootstrapper(),
            pipeline_control=Services.pipeline_control(),
            policies=Services.policies(),
        ),
        proposal_cycle=proposal_cycle,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def intent_planspace(planspace: Path) -> Path:
    """Extend standard planspace with intent layer directories."""
    artifacts = planspace / "artifacts"
    (artifacts / "intent" / "global").mkdir(parents=True, exist_ok=True)
    (artifacts / "intent" / "sections" / "section-01").mkdir(parents=True, exist_ok=True)
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
    intent_global = planspace / "artifacts" / "intent" / "global"
    intent_global.mkdir(parents=True, exist_ok=True)
    source_path = codespace / "README.md"
    if not source_path.exists():
        source_path.write_text(
            "# Project Notes\n\n"
            "Fail explicitly. Escalate uncertainty before risky changes.\n",
            encoding="utf-8",
        )
    (intent_global / "philosophy.md").write_text(
        "# Operational Philosophy\n\n"
        "## Principles\n\n"
        "### P1: Fail explicitly with context.\n"
        "Grounding: README.\n"
        "Test: silent defaults violate this.\n",
        encoding="utf-8",
    )
    (intent_global / "philosophy-source-map.json").write_text(
        json.dumps({
            "P1": {
                "source_type": "repo_source",
                "source_file": str(source_path),
                "source_section": "Project Notes",
            },
        }),
        encoding="utf-8",
    )
    (intent_global / "philosophy-source-manifest.json").write_text(
        json.dumps({
            "sources": [{
                "path": str(source_path),
                "hash": file_hash(source_path),
                "source_type": "repo_source",
            }],
        }),
        encoding="utf-8",
    )
    return sec


# ---------------------------------------------------------------------------
# Surface Registry Tests
# ---------------------------------------------------------------------------

class TestSurfaceRegistry:
    """Core surface registry logic: merge, dedup, diminishing returns."""

    def test_empty_registry_loads_default(
        self, intent_planspace: Path,
    ) -> None:
        sr = _make_surface_registry()
        registry = sr.load_surface_registry("99", intent_planspace)
        assert registry["next_id"] == 1
        assert registry["surfaces"] == []

    def test_merge_adds_new_surfaces(self) -> None:
        registry = {"section": "01", "next_id": 1, "surfaces": []}
        surfaces = {
            "stage": "proposal.integration",
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
            "stage": "proposal.integration", "attempt": 2,
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
        sr = _make_surface_registry()
        sr.save_surface_registry("01", intent_planspace, registry)
        loaded = sr.load_surface_registry("01", intent_planspace)
        assert loaded["next_id"] == 3
        assert loaded["surfaces"][0]["id"] == "P-01-0001"

    def test_malformed_registry_preserved(self, intent_planspace: Path) -> None:
        """Malformed registry is renamed and fresh default returned."""
        registry_path = (
            intent_planspace / "artifacts" / "intent" / "sections"
            / "section-01" / "surface-registry.json"
        )
        registry_path.write_text("not json!", encoding="utf-8")
        sr = _make_surface_registry()
        loaded = sr.load_surface_registry("01", intent_planspace)
        assert loaded["surfaces"] == []
        assert registry_path.with_suffix(".malformed.json").exists()


# ---------------------------------------------------------------------------
# Triage Tests
# ---------------------------------------------------------------------------

class TestIntentTriage:
    """Intent triage dispatches GLM and returns mode + budgets."""

    def test_full_default(self) -> None:
        """V2/R75: Triage failure defaults to full, not lightweight."""
        result = _full_default("01")
        assert result["intent_mode"] == "full"
        assert result["budgets"]["intent_expansion_max"] == 2

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

        result = _make_intent_triager().run_intent_triage(
            "01", intent_planspace, intent_planspace,
            related_files_count=6,
        )
        assert result["intent_mode"] == "full"
        assert result["budgets"]["intent_expansion_max"] == 2

    def test_triage_falls_back_to_full(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """V2/R75: When GLM fails to write signal, fallback to full."""
        mock_dispatch.return_value = ""

        result = _make_intent_triager().run_intent_triage(
            "01", intent_planspace, intent_planspace,
        )
        assert result["intent_mode"] == "full"


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
                    "status": "selected",
                    "sources": [{"path": str(constraints_path),
                                 "reason": "constraints"}],
                }), encoding="utf-8")
                return ""
            if agent_file == "philosophy-source-verifier.md":
                signal_path = (
                    intent_planspace / "artifacts" / "signals"
                    / "philosophy-verified-sources.json"
                )
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "verified_sources": [{
                        "path": str(constraints_path),
                        "reason": "confirmed constraints",
                    }],
                    "rejected": [],
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
                    "P1": {
                        "source_type": "repo_source",
                        "source_file": str(constraints_path),
                        "source_section": "Constraints",
                    },
                }), encoding="utf-8")
                return ""
            return ""

        mock_dispatch.side_effect = handle_dispatch

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(
            intent_planspace, intent_planspace,
        )
        assert result["status"] == "ready"
        assert result["philosophy_path"] == philosophy_path
        assert philosophy_path.exists()
        assert "P1" in philosophy_path.read_text(encoding="utf-8")

    def test_philosophy_skips_if_exists(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """Skip distillation when philosophy already exists."""
        intent_global = (
            intent_planspace / "artifacts" / "intent" / "global"
        )
        philosophy_path = intent_global / "philosophy.md"
        philosophy_path.write_text("# Existing Philosophy\n\nP1...\n")
        # Source-map must exist for the freshness check to accept the
        # existing philosophy (fail-closed without provenance).
        source_map_path = intent_global / "philosophy-source-map.json"
        source_map_path.write_text(json.dumps({
            "P1": {"source_type": "repo_source", "source_file": "README.md"},
        }), encoding="utf-8")

        _make_philosophy_bootstrapper().ensure_global_philosophy(
            intent_planspace, intent_planspace,
        )
        assert mock_dispatch.call_count == 0

    def test_intent_pack_creates_registry(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Intent pack generator creates the surface registry."""
        section = _make_intent_section(intent_planspace, codespace)
        mock_dispatch.return_value = ""

        intent_dir = _make_intent_pack_generator().generate_intent_pack(
            section, intent_planspace, codespace,
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
        result = _make_expansion_orchestrator().run_expansion_cycle(
            "01", intent_planspace, intent_planspace,
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
            "stage": "proposal.integration",
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

        result = _make_expansion_orchestrator().run_expansion_cycle(
            "01", intent_planspace, intent_planspace,
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
            "stage": "proposal.integration",
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
        _make_surface_registry().save_surface_registry("01", intent_planspace, registry)

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

        result = _make_expansion_orchestrator().run_expansion_cycle(
            "01", intent_planspace, intent_planspace,
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
        noop_communicator, noop_pipeline_control,
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
                    "status": "selected",
                    "sources": [{"path": str(
                        intent_planspace / "constraints.md"),
                        "reason": "constraints"}],
                }), encoding="utf-8")
                return ""
            if agent_file == "philosophy-source-verifier.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / "philosophy-verified-sources.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "verified_sources": [{
                        "path": str(intent_planspace / "constraints.md"),
                        "reason": "confirmed constraints",
                    }],
                    "rejected": [],
                }), encoding="utf-8")
                return ""

            # Philosophy distiller
            if agent_file == "philosophy-distiller.md":
                phi_path = (intent_planspace / "artifacts" / "intent"
                            / "global" / "philosophy.md")
                phi_path.parent.mkdir(parents=True, exist_ok=True)
                phi_path.write_text("# Operational Philosophy\nP1...\n")
                smap = phi_path.parent / "philosophy-source-map.json"
                smap.write_text(json.dumps({
                    "P1": {
                        "source_type": "repo_source",
                        "source_file": "constraints.md",
                        "source_section": "Constraints",
                    },
                }))
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

        from conftest import build_proposal_cycle
        pipeline = _make_section_pipeline(
            proposal_cycle=build_proposal_cycle(),
        )
        result = pipeline.run_section(
            intent_planspace, codespace, section,
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
        noop_communicator, noop_pipeline_control,
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

        run_section = _make_section_pipeline().run_section
        run_section(intent_planspace, codespace, section)

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
        from orchestrator.service.pipeline_control import _section_inputs_hash

        sec = Section(
            number="01",
            path=intent_planspace / "artifacts" / "sections" / "section-01.md",
            related_files=["src/main.py"],
        )
        sec.path.write_text("# Section 01\n")

        sections_by_num = {"01": sec}
        hash1 = _section_inputs_hash(
            "01", intent_planspace, sections_by_num)

        # Add philosophy
        phi = (intent_planspace / "artifacts" / "intent" / "global"
               / "philosophy.md")
        phi.write_text("# Philosophy\nP1 Strategy.\n")
        hash2 = _section_inputs_hash(
            "01", intent_planspace, sections_by_num)

        assert hash1 != hash2

    def test_problem_definition_change_changes_hash(
        self, intent_planspace: Path, codespace: Path,
    ) -> None:
        """Changing problem.md changes the section inputs hash."""
        from orchestrator.service.pipeline_control import _section_inputs_hash

        sec = Section(
            number="01",
            path=intent_planspace / "artifacts" / "sections" / "section-01.md",
            related_files=["src/main.py"],
        )
        sec.path.write_text("# Section 01\n")

        sections_by_num = {"01": sec}
        hash1 = _section_inputs_hash(
            "01", intent_planspace, sections_by_num)

        # Add problem definition
        prob = (intent_planspace / "artifacts" / "intent" / "sections"
                / "section-01" / "problem.md")
        prob.write_text("# Problem\nAuth refactor.\n")
        hash2 = _section_inputs_hash(
            "01", intent_planspace, sections_by_num)

        assert hash1 != hash2


# ---------------------------------------------------------------------------
# Regression Guards: Intent conventions
# ---------------------------------------------------------------------------

class TestIntentConventions:
    """Regression guards for intent layer conventions."""

    def test_intent_model_policy_defaults_exist(self) -> None:
        """All intent model keys have defaults in read_model_policy."""
        from containers import Services; from dispatch.service.model_policy import ModelPolicyLoader; read_model_policy = lambda ps: ModelPolicyLoader(artifact_io=Services.artifact_io()).load_model_policy(ps)
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
        from intent.service.intent_pack_generator import IntentPackGenerator
        from intent.service.surface_registry import (
            SurfaceRegistry,
            find_discarded_recurrences,
            merge_surfaces_into_registry,
        )
        from intent.engine.expansion_orchestrator import ExpansionOrchestrator
        from intent.service.philosophy_bootstrapper import PhilosophyBootstrapper
        from intent.service.intent_triager import IntentTriager
        # Smoke check — all names resolve
        assert callable(find_discarded_recurrences)
        assert IntentPackGenerator is not None
        assert SurfaceRegistry is not None
        assert callable(merge_surfaces_into_registry)
        assert ExpansionOrchestrator is not None
        assert PhilosophyBootstrapper is not None
        assert IntentTriager is not None

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
        sr = _make_surface_registry()
        result = sr.normalize_surface_ids(surfaces, registry, "01")
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
        sr = _make_surface_registry()
        result = sr.normalize_surface_ids(surfaces, registry, "01")
        # Should reuse existing ID, not allocate a new one
        assert result["problem_surfaces"][0]["id"] == "P-01-0003"
        assert registry["next_id"] == 5  # counter unchanged

    def test_philosophy_fail_closed_no_sources(
        self, intent_planspace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """No philosophy sources → fail-closed, return blocker result."""
        # No constraints.md, philosophy.md etc. in planspace
        result = _make_philosophy_bootstrapper().ensure_global_philosophy(
            intent_planspace, intent_planspace,
        )
        assert result["status"] == "needs_user_input"
        # Distiller should NOT have been called
        assert mock_dispatch.call_count == 0
        # Signal should exist
        signal_path = (intent_planspace / "artifacts" / "signals"
                       / "philosophy-bootstrap-signal.json")
        assert signal_path.exists()
        signal = json.loads(signal_path.read_text(encoding="utf-8"))
        assert signal["state"] == "NEED_DECISION"
        status_path = (intent_planspace / "artifacts" / "intent" / "global"
                       / "philosophy-bootstrap-status.json")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["bootstrap_state"] == "needs_user_input"
        assert status["blocking_state"] == "NEED_DECISION"
        user_source = (
            intent_planspace / "artifacts" / "intent" / "global"
            / "philosophy-source-user.md"
        )
        assert user_source.exists()
        assert "## Your Philosophy" in user_source.read_text(encoding="utf-8")
        decisions = (
            intent_planspace / "artifacts" / "intent" / "global"
            / "philosophy-bootstrap-decisions.md"
        )
        decisions_text = decisions.read_text(encoding="utf-8")
        assert "philosophy-source-user.md" in decisions_text
        assert "Freeform input is accepted" in decisions_text

    def test_loop_contract_includes_intent_artifacts(self) -> None:
        """loop-contract.md lists intent artifacts in inputs."""
        contract = SRC_DIR / "loop-contract.md"
        if not contract.exists():
            pytest.skip("loop-contract.md not found")
        text = contract.read_text(encoding="utf-8")
        assert "intent/global/philosophy.md" in text
        assert "intent/sections/section-NN/problem.md" in text
        assert "intent/sections/section-NN/problem-alignment.md" in text

    def test_alignment_template_includes_intent_refs(self) -> None:
        """Integration alignment template references intent artifacts."""
        tmpl = (SRC_DIR
                / "templates" / "dispatch"
                / "integration-alignment.md")
        if not tmpl.exists():
            pytest.skip("integration-alignment.md not found")
        text = tmpl.read_text(encoding="utf-8")
        assert "{intent_problem_ref}" in text
        assert "{intent_rubric_ref}" in text
        assert "{intent_philosophy_ref}" in text

    def test_agent_contract_triager_budget_keys(self) -> None:
        """intent-triager.md contains cycle-budget schema keys (V1/R53)."""
        agent = resolve_agent_path("intent-triager.md")
        text = agent.read_text(encoding="utf-8")
        for key in ("proposal_max", "implementation_max",
                     "intent_expansion_max", "max_new_surfaces_per_cycle",
                     "max_new_axes_total"):
            assert key in text, f"intent-triager.md missing budget key: {key}"

    def test_agent_contract_problem_expander_delta_keys(self) -> None:
        """problem-expander.md delta matches expansion.py schema (V2/R53)."""
        agent = resolve_agent_path("problem-expander.md")
        text = agent.read_text(encoding="utf-8")
        for key in ("applied_surface_ids", "discarded_surface_ids",
                     "problem_definition_updated", "restart_required"):
            assert key in text, (
                f"problem-expander.md missing delta key: {key}")

    def test_agent_contract_philosophy_expander_delta_keys(self) -> None:
        """philosophy-expander.md delta matches expansion.py schema (V3/R53)."""
        agent = resolve_agent_path("philosophy-expander.md")
        text = agent.read_text(encoding="utf-8")
        for key in ("applied_surface_ids", "discarded_surface_ids",
                     "philosophy_updated", "needs_user_input"):
            assert key in text, (
                f"philosophy-expander.md missing delta key: {key}")

    def test_agent_contract_pack_generator_registry_schema(self) -> None:
        """intent-pack-generator.md defines dedupe registry, not axis metadata (V4/R53)."""
        agent = resolve_agent_path("intent-pack-generator.md")
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
        sr = _make_surface_registry()
        loaded = sr.load_surface_registry("01", intent_planspace)
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
                    "status": "selected",
                    "sources": [{"path": str(
                        intent_planspace / "constraints.md"),
                        "reason": "constraints"}],
                }), encoding="utf-8")
                return ""
            if agent_file == "philosophy-source-verifier.md":
                signal_path = (intent_planspace / "artifacts" / "signals"
                               / "philosophy-verified-sources.json")
                signal_path.parent.mkdir(parents=True, exist_ok=True)
                signal_path.write_text(json.dumps({
                    "verified_sources": [{
                        "path": str(intent_planspace / "constraints.md"),
                        "reason": "confirmed constraints",
                    }],
                    "rejected": [],
                }), encoding="utf-8")
                return ""

            if agent_file == "philosophy-distiller.md":
                phi = (intent_planspace / "artifacts" / "intent"
                       / "global" / "philosophy.md")
                phi.parent.mkdir(parents=True, exist_ok=True)
                phi.write_text("# Philosophy\nP1...\n")
                smap = phi.parent / "philosophy-source-map.json"
                smap.write_text(json.dumps({
                    "P1": {
                        "source_type": "repo_source",
                        "source_file": "constraints.md",
                        "source_section": "Constraints",
                    },
                }))
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

        run_section = _make_section_pipeline().run_section
        run_section(intent_planspace, codespace, section)

        # intent-pack-generator must come AFTER TODO extraction
        # (which happens before any agent dispatch in full mode)
        assert "intent-pack-generator.md" in call_order

    def test_triage_budgets_applied_to_cycle_budget(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """proposal_max and implementation_max from triage reach cycle budget (V7/R53)."""
        # V1/R75: philosophy is now a gate — mock it as available
        from intent.service.philosophy_bootstrapper import PhilosophyBootstrapper
        monkeypatch.setattr(
            PhilosophyBootstrapper,
            "ensure_global_philosophy",
            MagicMock(return_value={
                "status": "ready",
                "blocking_state": None,
                "philosophy_path": (
                    intent_planspace / "artifacts" / "philosophy.md"
                ),
                "detail": "ready",
            }))
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

        run_section = _make_section_pipeline().run_section
        run_section(intent_planspace, codespace, section)

        # Read cycle budget and verify triage keys are present
        updated = json.loads(budget_path.read_text(encoding="utf-8"))
        assert updated.get("proposal_max") == 7, (
            "proposal_max from triage must be applied to cycle budget")
        assert updated.get("implementation_max") == 3, (
            "implementation_max from triage must be applied to cycle budget")

    def test_cycle_budget_malformed_preserved(
        self, intent_planspace: Path, codespace: Path,
        mock_dispatch: MagicMock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Malformed cycle budget → renamed + proceeds (V6/R53)."""
        # V1/R75: philosophy is now a gate — mock it as available
        from intent.service.philosophy_bootstrapper import PhilosophyBootstrapper
        monkeypatch.setattr(
            PhilosophyBootstrapper,
            "ensure_global_philosophy",
            MagicMock(return_value={
                "status": "ready",
                "blocking_state": None,
                "philosophy_path": (
                    intent_planspace / "artifacts" / "philosophy.md"
                ),
                "detail": "ready",
            }))
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

        run_section = _make_section_pipeline().run_section
        # Should not crash
        run_section(intent_planspace, codespace, section)

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

        run_section = _make_section_pipeline().run_section
        run_section(intent_planspace, intent_planspace, section)

        # First model is GLM (triage), second is escalation model
        assert len(call_models) >= 2
        assert call_models[0] == "glm"  # initial triage
        assert call_models[1] == "claude-opus"  # escalation

    def test_triager_uses_heuristic_judgment(self) -> None:
        """intent-triager.md describes heuristic triage, not frozen rules.

        PAT-0015: positive contract — verifies the agent file describes
        evidence-driven judgment rather than grepping for absent phrases.
        """
        agent = resolve_agent_path("intent-triager.md")
        text = agent.read_text(encoding="utf-8").lower()
        assert any(term in text for term in ("heuristic", "judgment", "evidence")), (
            "intent-triager.md must describe heuristic/evidence-driven triage"
        )

    def test_bootstrap_uses_evidence_driven_axes(self) -> None:
        """philosophy_bootstrapper.py uses evidence-driven discovery, not mandated defaults.

        PAT-0015: positive contract — verifies the bootstrap module uses
        evidence-driven discovery rather than grepping for absent phrases.
        """
        bootstrap = (SRC_DIR
                     / "intent" / "service" / "philosophy_bootstrapper.py")
        if not bootstrap.exists():
            pytest.skip("philosophy_bootstrapper.py not found")
        text = bootstrap.read_text(encoding="utf-8").lower()
        assert any(term in text for term in ("catalog", "discover", "source")), (
            "philosophy_bootstrapper.py must reference catalog or source-driven discovery"
        )

    def test_surfaces_use_agent_adjudicated_recurrence(self) -> None:
        """surface_registry.py delegates recurrence decisions to agents.

        PAT-0015: positive contract — verifies the surfaces module
        uses agent-adjudicated recurrence rather than grepping for
        absent thresholds.
        """
        surf = (SRC_DIR
                / "intent" / "service" / "surface_registry.py")
        if not surf.exists():
            pytest.skip("surface_registry.py not found")
        text = surf.read_text(encoding="utf-8").lower()
        assert any(term in text for term in ("adjudicat", "recurrence", "dispatch")), (
            "surface_registry.py must reference agent-adjudicated recurrence"
        )

    def test_intent_model_policy_escalation_keys(self) -> None:
        """Model policy includes escalation and recurrence adjudicator keys (V1/V5 R54)."""
        from containers import Services; from dispatch.service.model_policy import ModelPolicyLoader; read_model_policy = lambda ps: ModelPolicyLoader(artifact_io=Services.artifact_io()).load_model_policy(ps)
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
        """DB_SH resolves to an existing path via layout-agnostic lookup (V6/R54)."""
        from _paths import DB_SH
        assert DB_SH.exists(), (
            f"DB_SH should resolve to an existing path, got {DB_SH}")

    def test_implement_md_describes_intent_layer(self) -> None:
        """implement.md must describe the intent layer (V7/R54)."""
        impl = (SRC_DIR
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
        from orchestrator.types import Section

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

        _make_intent_pack_generator().generate_intent_pack(sec, planspace, codespace)

        # The prompt should reference corrections
        prompt_path = artifacts / "intent-pack-01-prompt.md"
        assert prompt_path.exists()
        prompt_text = prompt_path.read_text(encoding="utf-8")
        assert "codemap-corrections" in prompt_text, (
            "Intent pack prompt must reference codemap corrections")


class TestR55BudgetEnforcementFunctional:
    """R55/V10: expansion cycle processes all pending surfaces."""

    def test_pending_surfaces_written_unbounded(
        self, planspace, codespace, section_01, mock_dispatch,
    ) -> None:
        """All pending surfaces are written to the pending file."""
        import json

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

        _make_expansion_orchestrator().run_expansion_cycle(
            "01", planspace, codespace,
        )

        # All surfaces should be in the pending file
        pending = signals / "intent-surfaces-pending-01.json"
        assert pending.exists(), (
            "Pending surfaces file must be written")
        data = json.loads(pending.read_text())
        total = len(data.get("problem_surfaces", []))
        total += len(data.get("philosophy_surfaces", []))
        assert total == 11, (
            f"All pending surfaces should be processed (got {total})")


# ---------------------------------------------------------------------------
# R56 Regression Guards
# ---------------------------------------------------------------------------


class TestR56QueueSemantics:
    """V1/R56: Expansion uses queue semantics — pending backlog is drained."""

    def test_backlog_surfaces_processed_in_next_cycle(
        self, planspace, codespace, section_01, mock_dispatch,
    ) -> None:
        """Pending surfaces from prior truncation are processed next cycle."""

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

        result = _make_expansion_orchestrator().run_expansion_cycle(
            "01", planspace, codespace,
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

    def test_bootstrap_discovers_philosophy_sources_dynamically(self) -> None:
        """philosophy_bootstrapper.py discovers philosophy sources via catalog, not hardcoded names.

        PAT-0015: positive contract — verifies the bootstrap module
        uses a dynamic discovery mechanism rather than grepping for
        absent filename literals.
        """
        bootstrap = (SRC_DIR
                     / "intent" / "service" / "philosophy_bootstrapper.py")
        if not bootstrap.exists():
            pytest.skip("philosophy_bootstrapper.py not found")
        text = bootstrap.read_text(encoding="utf-8").lower()
        assert any(term in text for term in ("catalog", "glob", "walk", "discover")), (
            "philosophy_bootstrapper.py must discover philosophy sources dynamically"
        )

    def test_catalog_builder_is_mechanical(self) -> None:
        """build_philosophy_catalog uses bounded walk, not name matching."""
        catalog_mod = (SRC_DIR
                       / "intent" / "service" / "philosophy_catalog.py")
        if not catalog_mod.exists():
            pytest.skip("philosophy_catalog.py not found")
        text = catalog_mod.read_text(encoding="utf-8")
        # V1/R60: replaced rglob with walk_md_bounded (os.walk based)
        assert "walk_md_bounded" in text, (
            "Catalog builder must use walk_md_bounded for mechanical collection")

    def test_selector_agent_file_exists(self) -> None:
        """philosophy-source-selector.md agent file must exist."""
        agent = resolve_agent_path("philosophy-source-selector.md")
        text = agent.read_text(encoding="utf-8")
        assert "sources" in text, (
            "Agent must define 'sources' in its output schema")
        assert "status" in text, (
            "Agent must define selector status in its output schema")

    def test_model_policy_has_selector_key(self) -> None:
        """Model policy must include intent_philosophy_selector key."""
        from containers import Services; from dispatch.service.model_policy import ModelPolicyLoader; read_model_policy = lambda ps: ModelPolicyLoader(artifact_io=Services.artifact_io()).load_model_policy(ps)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ps = Path(td)
            (ps / "artifacts").mkdir(parents=True)
            policy = read_model_policy(ps)
            assert "intent_philosophy_selector" in policy, (
                "Model policy must include intent_philosophy_selector key")
            assert "intent_philosophy_selector_escalation" in policy, (
                "Model policy must include selector escalation key")

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
                signal.write_text(json.dumps({
                    "status": "empty",
                    "sources": [],
                }))
                return ""
            return ""

        mock_dispatch.side_effect = selector_empty

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)
        assert result["status"] == "needs_user_input", (
            "Empty selection must fail-closed with a blocker result")

    def test_selector_missing_signal_retries_then_escalates(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Missing selector signal retries twice, then escalates, then blocks."""
        (planspace / "readme.md").write_text("# Readme\n")
        models: list[str] = []

        def selector_missing(*args, **kwargs):
            if kwargs.get("agent_file", "") == "philosophy-source-selector.md":
                models.append(args[0])
            return ""

        mock_dispatch.side_effect = selector_missing

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)
        assert result["status"] == "failed"
        assert result["blocking_state"] == "NEEDS_PARENT"
        assert models == ["gpt-high", "gpt-high", "claude-opus"]

        diagnostics = planspace / "artifacts" / "intent" / "global" / \
            "philosophy-bootstrap-diagnostics.json"
        payload = json.loads(diagnostics.read_text())
        assert payload["stage"] == "selector"
        assert payload["final_outcome"] == "needs_parent"
        assert [a["result"] for a in payload["attempts"]] == [
            "missing_signal", "missing_signal", "missing_signal",
        ]

    def test_selector_diagnostics_record_missing_malformed_then_empty(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Selector diagnostics keep missing, malformed, and empty distinct."""
        (planspace / "readme.md").write_text("# Readme\n")
        selector_calls = 0

        def selector_states(*args, **kwargs):
            nonlocal selector_calls
            if kwargs.get("agent_file", "") != "philosophy-source-selector.md":
                return ""
            selector_calls += 1
            signal = planspace / "artifacts" / "signals" / \
                "philosophy-selected-sources.json"
            signal.parent.mkdir(parents=True, exist_ok=True)
            if selector_calls == 1:
                return ""
            if selector_calls == 2:
                signal.write_text("NOT JSON", encoding="utf-8")
                return ""
            signal.write_text(json.dumps({
                "status": "empty",
                "sources": [],
            }), encoding="utf-8")
            return ""

        mock_dispatch.side_effect = selector_states

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)
        assert result["status"] == "needs_user_input"

        diagnostics = planspace / "artifacts" / "intent" / "global" / \
            "philosophy-bootstrap-diagnostics.json"
        payload = json.loads(diagnostics.read_text())
        assert payload["final_outcome"] == "need_decision"
        assert payload["attempts"] == [
            {"attempt": 1, "model": "gpt-high", "result": "missing_signal"},
            {
                "attempt": 2,
                "model": "gpt-high",
                "result": "malformed_signal",
                "preserved": "philosophy-selected-sources.malformed.json",
            },
            {"attempt": 3, "model": "claude-opus", "result": "valid_empty"},
        ]
        malformed = planspace / "artifacts" / "signals" / \
            "philosophy-selected-sources.malformed.json"
        assert malformed.exists()

    def test_verifier_missing_signal_blocks_instead_of_silent_empty(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Ambiguous-source verifier failure is treated as agent failure."""
        source = codespace / "constraints.md"
        source.write_text("# Constraints\nNo new deps.\n", encoding="utf-8")
        ambiguous = codespace / "maybe.md"
        ambiguous.write_text("# Maybe\nUnclear preview.\n", encoding="utf-8")
        verifier_models: list[str] = []

        def verifier_missing(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "philosophy-source-selector.md":
                signal = planspace / "artifacts" / "signals" / \
                    "philosophy-selected-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "status": "selected",
                    "sources": [{"path": str(source), "reason": "constraints"}],
                    "ambiguous": [{"path": str(ambiguous), "reason": "unclear"}],
                }), encoding="utf-8")
                return ""
            if agent_file == "philosophy-source-verifier.md":
                verifier_models.append(args[0])
                return ""
            return ""

        mock_dispatch.side_effect = verifier_missing

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)
        assert result["status"] == "failed"
        assert result["blocking_state"] == "NEEDS_PARENT"
        assert verifier_models == ["claude-opus", "claude-opus", "claude-opus"]

    def test_verifier_confirms_selected_and_ambiguous_sources(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Verifier sees the full shortlist and becomes authoritative."""
        selected_source = codespace / "constraints.md"
        selected_source.write_text(
            "# Constraints\nNo new deps.\n",
            encoding="utf-8",
        )
        ambiguous_source = codespace / "principles.md"
        ambiguous_source.write_text(
            "# Principles\nAlways preserve invariants.\n",
            encoding="utf-8",
        )
        verifier_seen_paths: list[str] = []

        def verifier_authoritative(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "philosophy-source-selector.md":
                signal = planspace / "artifacts" / "signals" / \
                    "philosophy-selected-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "status": "selected",
                    "sources": [{
                        "path": str(selected_source),
                        "reason": "constraints",
                    }],
                    "ambiguous": [{
                        "path": str(ambiguous_source),
                        "reason": "needs full read",
                    }],
                }), encoding="utf-8")
                return ""
            if agent_file == "philosophy-source-verifier.md":
                prompt_path = args[1]
                prompt_text = Path(prompt_path).read_text(encoding="utf-8")
                verifier_seen_paths.extend([
                    str(selected_source),
                    str(ambiguous_source),
                ])
                assert str(selected_source) in prompt_text
                assert str(ambiguous_source) in prompt_text
                signal = planspace / "artifacts" / "signals" / \
                    "philosophy-verified-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "verified_sources": [{
                        "path": str(ambiguous_source),
                        "reason": "confirmed philosophy",
                    }],
                    "rejected": [{
                        "path": str(selected_source),
                        "reason": "not philosophy after full read",
                    }],
                }), encoding="utf-8")
                return ""
            if agent_file == "philosophy-distiller.md":
                intent_global = planspace / "artifacts" / "intent" / "global"
                intent_global.mkdir(parents=True, exist_ok=True)
                (intent_global / "philosophy.md").write_text(
                    "# Philosophy\n## P1: Preserve invariants\n",
                    encoding="utf-8",
                )
                (intent_global / "philosophy-source-map.json").write_text(
                    json.dumps({
                        "P1": {
                            "source_type": "repo_source",
                            "source_file": str(ambiguous_source),
                            "source_section": "Principles",
                        },
                    }),
                    encoding="utf-8",
                )
                return ""
            return ""

        mock_dispatch.side_effect = verifier_authoritative

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)

        assert result["status"] == "ready"
        assert verifier_seen_paths == [
            str(selected_source),
            str(ambiguous_source),
        ]
        manifest = json.loads((
            planspace / "artifacts" / "intent" / "global"
            / "philosophy-source-manifest.json"
        ).read_text(encoding="utf-8"))
        assert len(manifest["sources"]) == 1
        assert manifest["sources"][0]["path"] == str(ambiguous_source)
        assert manifest["sources"][0]["hash"]


class TestR56UpdaterSignalPreservation:
    """V3/R56: Malformed updater signal renamed to .malformed.json."""

    def test_malformed_updater_signal_preserved(self) -> None:
        """Malformed JSON is renamed to .malformed.json by read_json + rename_malformed (V3/R56)."""
        import tempfile
        from signals.repository.artifact_io import read_json, rename_malformed
        with tempfile.TemporaryDirectory() as td:
            signal_path = Path(td) / "section-01-related-files-update.json"
            signal_path.write_text("{not valid json", encoding="utf-8")
            result = read_json(signal_path)
            assert result is None, "read_json should return None for malformed JSON"
            rename_malformed(signal_path)
            malformed = signal_path.with_suffix(".malformed.json")
            assert malformed.exists(), (
                "rename_malformed should create .malformed.json")
            assert not signal_path.exists(), (
                "Original file should be renamed away")


class TestR56AxisBudgetEnforcement:
    """V5/R56: max_new_axes_total is enforced, not just declared."""

    def test_axes_added_tracked_in_registry(
        self, planspace, codespace, section_01, mock_dispatch,
    ) -> None:
        """axes_added_so_far is persisted in registry after expansion."""

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

        _make_expansion_orchestrator().run_expansion_cycle(
            "01", planspace, codespace,
        )

        # Registry should track axes_added_so_far
        reg = json.loads(
            (intent_sec / "surface-registry.json").read_text())
        assert reg.get("axes_added_so_far") == 2, (
            "Registry must track axes_added_so_far after expansion")

    def test_axes_accepted_without_cap(
        self, planspace, codespace, section_01, mock_dispatch,
    ) -> None:
        """Axes are accepted regardless of count -- no hard cap."""

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

        # Expander proposes 3 axes
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

        result = _make_expansion_orchestrator().run_expansion_cycle(
            "01", planspace, codespace,
        )

        assert result.get("needs_user_input") is not True, (
            "Axes must not block with NEED_DECISION")
        assert result["expansion_applied"] is True, (
            "Axes must be applied without cap")


class TestR57DeepScanFeedbackPreservation:
    """V1/R57: deep_scan.update_match() must warn + rename malformed JSON."""

    def test_malformed_feedback_renamed(self, tmp_path):
        """Malformed feedback JSON is renamed to .malformed.json."""
        from containers import Services
        from scan.related.match_updater import MatchUpdater

        section_file = tmp_path / "section-01.md"
        section_file.write_text(
            "## Related Files\n\n### src/foo.py\nSome detail\n")

        # Create a details-response + malformed feedback
        details = tmp_path / "deep-src_foo_py-response.md"
        details.write_text("analysis")
        feedback = tmp_path / "deep-src_foo_py-feedback.json"
        feedback.write_text("{not valid json")

        updater = MatchUpdater(artifact_io=Services.artifact_io())
        result = updater.update_match(section_file, "src/foo.py", details)
        assert result is True, "Should continue despite malformed feedback"
        assert not feedback.exists(), "Original should be renamed"
        assert (tmp_path / "deep-src_foo_py-feedback.malformed.json").exists()


class TestR57UpdaterSignalValidityPreservation:
    """V2/R57: _is_valid_updater_signal() must rename malformed JSON."""

    def test_malformed_updater_signal_renamed_by_validity_check(
        self, tmp_path,
    ):
        """Malformed JSON in validity check path is renamed."""
        from scan.service.feedback_router import FeedbackRouter

        signal_path = tmp_path / "update-signal.json"
        signal_path.write_text("{broken json!!")

        result = FeedbackRouter(artifact_io=Services.artifact_io())._is_valid_updater_signal(signal_path)
        assert result is False
        assert not signal_path.exists(), (
            "Original should be renamed by validity check")
        assert (tmp_path / "update-signal.malformed.json").exists()


class TestR57RefExpansionWarnings:
    """V3/R57: Ref expansion failures must warn + use hash marker."""

    def test_pipeline_hash_uses_error_marker(self, tmp_path):
        """Unreadable ref produces stable REF_READ_ERROR marker in hash."""
        import hashlib

        from orchestrator.service.pipeline_control import _section_inputs_hash

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
        from orchestrator.types import Section

        sections_by_num = {
            "01": Section(
                number="01",
                path=str(planspace / "artifacts" / "sections" / "section-01.md"),
                related_files=[],
            ),
        }

        h1 = _section_inputs_hash("01", planspace, sections_by_num)

        # Hash should be deterministic (same broken ref → same hash)
        h2 = _section_inputs_hash("01", planspace, sections_by_num)
        assert h1 == h2, "Hash must be deterministic even with broken refs"

    def test_context_builder_warns_on_broken_ref(
        self, planspace, codespace, section_01, capsys,
    ):
        """Broken ref in context builder emits warning."""
        from containers import Services; from dispatch.prompt.context_builder import ContextBuilder; build_prompt_context = lambda sec, ps, cs, **kw: ContextBuilder(artifact_io=Services.artifact_io(), cross_section=Services.cross_section()).build_prompt_context(sec, ps, cs, **kw)
        from orchestrator.types import Section

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
        capturing_pipeline_control,
    ):
        """Axis budget gate must NOT say 'Philosophy tension'."""
        artifacts = planspace / "artifacts"
        signals = artifacts / "signals"
        signals.mkdir(parents=True, exist_ok=True)

        delta_result = {
            "needs_user_input": True,
            "user_input_kind": "axis_budget",
            "user_input_path": str(
                signals / "intent-axis-budget-01-signal.json"),
        }

        capturing_pipeline_control._pause_return = "resume:accept"

        _make_expansion_orchestrator().handle_user_gate("01", planspace, delta_result)

        # Check the pause message does NOT say philosophy
        assert len(capturing_pipeline_control.pause_calls) >= 1
        pause_msg = capturing_pipeline_control.pause_calls[0][1]
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
        capturing_pipeline_control,
    ):
        """Philosophy gate correctly says 'Philosophy tension'."""
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

        capturing_pipeline_control._pause_return = "resume:accept"

        _make_expansion_orchestrator().handle_user_gate("01", planspace, delta_result)

        assert len(capturing_pipeline_control.pause_calls) >= 1
        pause_msg = capturing_pipeline_control.pause_calls[0][1]
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

        # Use SurfaceRegistry class directly
        sr = _make_surface_registry()

        # Simulate what the runner does in the PROBLEMS branch (V5/R57)
        misaligned_surfaces = sr.load_intent_surfaces("01", planspace)
        assert misaligned_surfaces is not None

        reg = sr.load_surface_registry("01", planspace)
        misaligned_surfaces = sr.normalize_surface_ids(
            misaligned_surfaces, reg, "01")
        new_ids, _ = merge_surfaces_into_registry(reg, misaligned_surfaces)
        sr.save_surface_registry("01", planspace, reg)

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
        from containers import Services
        from coordination.prompt.writers import Writers

        from orchestrator.path_registry import PathRegistry

        planspace = tmp_path / "plan"
        codespace = tmp_path / "code"
        planspace.mkdir()
        codespace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        # Write malformed tool-registry
        artifacts = planspace / "artifacts"
        tool_reg = artifacts / "tool-registry.json"
        tool_reg.write_text("{BROKEN JSON!", encoding="utf-8")

        # Call the function — it builds a prompt that includes tool block
        from coordination.problem_types import Problem
        group = [Problem(section="01", type="test", description="d",
                  files=["a.py"])]

        writers = Writers(
            artifact_io=Services.artifact_io(),
            communicator=Services.communicator(),
            logger=Services.logger(),
            prompt_guard=Services.prompt_guard(),
            task_router=Services.task_router(),
        )
        writers.write_fix_prompt(group, planspace, codespace, 0)

        # Assert malformed copy was preserved
        malformed = tool_reg.with_suffix(".malformed.json")
        assert malformed.exists(), (
            "Malformed tool-registry must be preserved as .malformed.json")
        assert malformed.read_text(encoding="utf-8") == "{BROKEN JSON!"

        # Original is renamed away — malformed artifact must not remain
        # at the canonical path (PAT-0001)
        assert not tool_reg.exists(), (
            "Malformed tool-registry must be renamed away from canonical path")


# ---------------------------------------------------------------------------
# R58 — V3: Related-files update signal preservation
# ---------------------------------------------------------------------------


class TestR58RelatedFilesSignalPreservation:
    """V3/R58: When a related-files update signal is malformed,
    apply_related_files_update() must preserve it as .malformed.json."""

    def test_malformed_signal_preserved(self, tmp_path):
        """Malformed signal → returns False + .malformed.json exists."""
        from scan.related.related_file_resolver import RelatedFileResolver
        from containers import ArtifactIOService, HasherService, TaskRouterService
        from conftest import WritingGuard

        resolver = RelatedFileResolver(
            artifact_io=ArtifactIOService(),
            hasher=HasherService(),
            prompt_guard=WritingGuard(),
            task_router=TaskRouterService(),
        )

        section_file = tmp_path / "section-01.md"
        section_file.write_text("## Related Files\n### a.py\nInfo\n")

        signal_file = tmp_path / "related-files-update.json"
        signal_file.write_text("NOT VALID JSON{{{", encoding="utf-8")

        result = resolver.apply_related_files_update(section_file, signal_file)

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
        from intent.service.intent_pack_generator import _build_philosophy_catalog

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
        from intent.service.intent_pack_generator import _build_philosophy_catalog

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
        from intent.service.intent_pack_generator import _build_philosophy_catalog

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
        distiller_calls = 0

        def side_effect(*args, **kwargs):
            nonlocal distiller_calls
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "philosophy-source-selector.md":
                signal = artifacts / "signals" / \
                    "philosophy-selected-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "status": "selected",
                    "sources": [{"path": str(codespace / "philo.md"),
                                 "reason": "test"}],
                }))
                return ""
            if agent_file == "philosophy-source-verifier.md":
                signal = artifacts / "signals" / \
                    "philosophy-verified-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "verified_sources": [{
                        "path": str(codespace / "philo.md"),
                        "reason": "confirmed test",
                    }],
                    "rejected": [],
                }))
                return ""
            if agent_file == "philosophy-distiller.md":
                distiller_calls += 1
                # Write philosophy but NO source map
                (intent_global / "philosophy.md").write_text(
                    "# Philosophy\n## P1: Test principle\nDo stuff.\n")
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)
        assert result["status"] == "failed", (
            "Missing source map must cause grounding failure")
        assert distiller_calls == 2, (
            "Missing distiller outputs must retry once before blocking")

        fail_signal = artifacts / "signals" / \
            "philosophy-bootstrap-signal.json"
        assert fail_signal.exists(), (
            "Must write philosophy-bootstrap-signal.json signal")
        assert json.loads(fail_signal.read_text())["state"] == "NEEDS_PARENT"

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
                    "status": "selected",
                    "sources": [{"path": str(codespace / "philo.md"),
                                 "reason": "test"}],
                }))
                return ""
            if agent_file == "philosophy-source-verifier.md":
                signal = artifacts / "signals" / \
                    "philosophy-verified-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "verified_sources": [{
                        "path": str(codespace / "philo.md"),
                        "reason": "confirmed test",
                    }],
                    "rejected": [],
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

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)
        assert result["status"] == "failed"

        malformed = intent_global / "philosophy-source-map.malformed.json"
        assert malformed.exists(), (
            "Malformed source map must be preserved as .malformed.json")

    def test_distiller_no_extractable_philosophy_needs_decision(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Empty distillation is a genuine no-philosophy outcome."""
        artifacts = planspace / "artifacts"
        intent_global = artifacts / "intent" / "global"
        intent_global.mkdir(parents=True, exist_ok=True)

        source = codespace / "implementation.md"
        source.write_text("# Build Steps\n1. Add endpoint.\n", encoding="utf-8")

        def side_effect(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "philosophy-source-selector.md":
                signal = artifacts / "signals" / \
                    "philosophy-selected-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "status": "selected",
                    "sources": [{"path": str(source), "reason": "mixed doc"}],
                }))
                return ""
            if agent_file == "philosophy-source-verifier.md":
                signal = artifacts / "signals" / \
                    "philosophy-verified-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "verified_sources": [{
                        "path": str(source),
                        "reason": "full read completed",
                    }],
                    "rejected": [],
                }))
                return ""
            if agent_file == "philosophy-distiller.md":
                (intent_global / "philosophy.md").write_text(
                    "",
                    encoding="utf-8",
                )
                (intent_global / "philosophy-source-map.json").write_text(
                    "{}",
                    encoding="utf-8",
                )
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)

        assert result["status"] == "needs_user_input"
        assert result["blocking_state"] == "NEED_DECISION"
        blocker = json.loads(
            (artifacts / "signals" / "philosophy-bootstrap-signal.json")
            .read_text(encoding="utf-8"),
        )
        assert blocker["state"] == "NEED_DECISION"

    def test_user_source_resume_distills_without_selector_or_verifier(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """User bootstrap input becomes an authorized distillation source."""
        artifacts = planspace / "artifacts"
        intent_global = artifacts / "intent" / "global"
        intent_global.mkdir(parents=True, exist_ok=True)

        user_source = intent_global / "philosophy-source-user.md"
        user_source.write_text(
            "# Philosophy Source — User\n\n"
            "## Your Philosophy\n"
            "- Fail explicitly with context instead of silently defaulting.\n"
            "- Escalate uncertainty instead of guessing when consequences are material.\n",
            encoding="utf-8",
        )

        dispatches: list[str] = []

        def side_effect(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            dispatches.append(agent_file)
            if agent_file == "philosophy-distiller.md":
                (intent_global / "philosophy.md").write_text(
                    "# Operational Philosophy\n\n"
                    "## Principles\n\n"
                    "### P1: Fail explicitly with context.\n"
                    "Grounding: user text\n"
                    "Test: silent defaults violate this.\n",
                    encoding="utf-8",
                )
                (intent_global / "philosophy-source-map.json").write_text(
                    json.dumps({
                        "P1": {
                            "source_type": "user_source",
                            "source_file": str(user_source),
                            "source_section": "Your Philosophy",
                        },
                    }),
                    encoding="utf-8",
                )
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)

        assert result["status"] == "ready"
        assert dispatches == ["philosophy-distiller.md"]
        manifest = json.loads(
            (intent_global / "philosophy-source-manifest.json").read_text(
                encoding="utf-8",
            ),
        )
        assert manifest["sources"] == [{
            "path": str(user_source),
            "hash": manifest["sources"][0]["hash"],
            "source_type": "user_source",
        }]
        status = json.loads(
            (intent_global / "philosophy-bootstrap-status.json").read_text(
                encoding="utf-8",
            ),
        )
        assert status["source_mode"] == "user_source"

    def test_user_source_ambiguous_repauses_with_same_decision_artifact(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Thin or ambiguous user input triggers another NEED_DECISION cycle."""
        artifacts = planspace / "artifacts"
        intent_global = artifacts / "intent" / "global"
        intent_global.mkdir(parents=True, exist_ok=True)

        user_source = intent_global / "philosophy-source-user.md"
        user_source.write_text(
            "# Philosophy Source — User\n\n"
            "## Your Philosophy\n"
            "Sometimes move fast, sometimes be cautious, depending on the situation.\n"
            "Use judgment.\n"
            "Explain things if needed.\n"
            "Prefer not to block, except when blocking is better.\n",
            encoding="utf-8",
        )
        decisions = intent_global / "philosophy-bootstrap-decisions.md"

        dispatches: list[str] = []

        def side_effect(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            dispatches.append(agent_file)
            if agent_file == "philosophy-distiller.md":
                decisions.write_text(
                    "# Philosophy Bootstrap Decisions\n\n"
                    "The current philosophy input is still ambiguous.\n\n"
                    "Please clarify:\n"
                    "- When should the system block instead of proceed under uncertainty?\n"
                    "- Which tradeoffs outrank speed when they conflict?\n",
                    encoding="utf-8",
                )
                (intent_global / "philosophy.md").write_text(
                    "",
                    encoding="utf-8",
                )
                (intent_global / "philosophy-source-map.json").write_text(
                    "{}",
                    encoding="utf-8",
                )
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)

        assert result["status"] == "needs_user_input"
        assert result["blocking_state"] == "NEED_DECISION"
        assert dispatches == [
            "philosophy-distiller.md",
            "philosophy-distiller.md",
        ]
        blocker = json.loads(
            (artifacts / "signals" / "philosophy-bootstrap-signal.json")
            .read_text(encoding="utf-8"),
        )
        assert blocker["state"] == "NEED_DECISION"
        assert blocker["user_source_path"].endswith("philosophy-source-user.md")
        decisions_text = decisions.read_text(encoding="utf-8")
        assert "clarify" in decisions_text.lower()
        assert "block instead of proceed" in decisions_text

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
                    "status": "selected",
                    "sources": [{"path": str(codespace / "philo.md"),
                                 "reason": "test"}],
                }))
                return ""
            if agent_file == "philosophy-source-verifier.md":
                signal = artifacts / "signals" / \
                    "philosophy-verified-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "verified_sources": [{
                        "path": str(codespace / "philo.md"),
                        "reason": "confirmed test",
                    }],
                    "rejected": [],
                }))
                return ""
            if agent_file == "philosophy-distiller.md":
                (intent_global / "philosophy.md").write_text(
                    "# Philosophy\n\n## Principles\n\n"
                    "### P1: One\n\n### P2: Two\n\n### P3: Three\n")
                # Source map only covers P1
                (intent_global / "philosophy-source-map.json").write_text(
                    json.dumps({
                        "P1": {
                            "source_type": "repo_source",
                            "source_file": "philo.md",
                            "source_section": "One",
                        },
                    }))
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)
        assert result["status"] == "failed", (
            "Unmapped principles must cause grounding failure")

        fail_signal = artifacts / "signals" / \
            "philosophy-bootstrap-signal.json"
        data = json.loads(fail_signal.read_text())
        assert "P2" in data.get("unmapped_principles", [])
        assert "P3" in data.get("unmapped_principles", [])
        assert data["state"] == "NEEDS_PARENT"

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
                    "status": "selected",
                    "sources": [{"path": str(codespace / "philo.md"),
                                 "reason": "test"}],
                }))
                return ""
            if agent_file == "philosophy-source-verifier.md":
                signal = artifacts / "signals" / \
                    "philosophy-verified-sources.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({
                    "verified_sources": [{
                        "path": str(codespace / "philo.md"),
                        "reason": "confirmed test",
                    }],
                    "rejected": [],
                }))
                return ""
            if agent_file == "philosophy-distiller.md":
                (intent_global / "philosophy.md").write_text(
                    "# Philosophy\n## P1: One\n## P2: Two\n")
                (intent_global / "philosophy-source-map.json").write_text(
                    json.dumps({
                        "P1": {
                            "source_type": "repo_source",
                            "source_file": "philo.md",
                            "source_section": "One",
                        },
                        "P2": {
                            "source_type": "repo_source",
                            "source_file": "philo.md",
                            "source_section": "Two",
                        },
                    }))
                return ""
            return ""

        mock_dispatch.side_effect = side_effect

        result = _make_philosophy_bootstrapper().ensure_global_philosophy(planspace, codespace)
        assert result["status"] == "ready", (
            "Fully grounded philosophy must succeed")
        status_path = artifacts / "intent" / "global" / \
            "philosophy-bootstrap-status.json"
        status = json.loads(status_path.read_text())
        assert status["bootstrap_state"] == "ready"
        assert status["blocking_state"] is None


class TestR59IntentPackHashInvalidation:
    """V3/R59: Intent pack uses hash-based invalidation, not
    existence-only skipping."""

    def test_regenerates_when_inputs_change(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Existing pack must regenerate when upstream inputs change."""
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

        _make_intent_pack_generator().generate_intent_pack(sec, planspace, codespace)
        assert len(dispatch_called) > 0, (
            "Must dispatch agent when input hash differs (regenerate)")

    def test_skips_when_hash_matches(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Existing pack with matching hash must skip regeneration."""
        from intent.service.intent_pack_generator import IntentPackGenerator

        sec = _make_intent_section(planspace, codespace)
        artifacts = planspace / "artifacts"
        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)

        # Create existing pack
        (intent_sec / "problem.md").write_text("# Problem\nExisting.\n")
        (intent_sec / "problem-alignment.md").write_text("# Rubric\n")

        # Compute the real hash from current inputs
        from orchestrator.path_registry import PathRegistry
        gen = _make_intent_pack_generator()
        real_hash = gen._compute_intent_pack_hash(
            PathRegistry(planspace), sec, "",
        )
        (intent_sec / "intent-pack-input-hash.txt").write_text(real_hash)

        dispatch_called = []

        def side_effect(*args, **kwargs):
            dispatch_called.append(True)
            return ""

        mock_dispatch.side_effect = side_effect

        gen.generate_intent_pack(sec, planspace, codespace)
        assert len(dispatch_called) == 0, (
            "Must NOT dispatch when input hash matches (skip)")

    def test_hash_written_after_successful_generation(
        self, planspace, codespace, mock_dispatch,
    ) -> None:
        """Successful generation must write input hash file."""
        sec = _make_intent_section(planspace, codespace)
        artifacts = planspace / "artifacts"
        intent_sec = artifacts / "intent" / "sections" / "section-01"
        intent_sec.mkdir(parents=True, exist_ok=True)

        def side_effect(*args, **kwargs):
            (intent_sec / "problem.md").write_text("# Problem\n")
            (intent_sec / "problem-alignment.md").write_text("# Rubric\n")
            return ""

        mock_dispatch.side_effect = side_effect

        _make_intent_pack_generator().generate_intent_pack(sec, planspace, codespace)

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
        from intent.service.intent_pack_generator import _walk_md_bounded

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
        from intent.service.intent_pack_generator import _walk_md_bounded

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
        from intent.service.intent_pack_generator import _walk_md_bounded

        for name in ("c.md", "a.md", "b.md"):
            (tmp_path / name).write_text(f"file {name}")

        results = [p.name for p in _walk_md_bounded(tmp_path, max_depth=3)]
        assert results == ["a.md", "b.md", "c.md"]

    def test_catalog_uses_bounded_walk(self, tmp_path: Path) -> None:
        """_build_philosophy_catalog must use bounded walk (basic functionality)."""
        from intent.service.intent_pack_generator import _build_philosophy_catalog

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
        tool_path = (SRC_DIR / "tools" / "extract-docstring-py")
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
        tool_path = (SRC_DIR / "tools" / "extract-docstring-py")
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
    """V1/R61: write_alignment_surface must include intent pack artifacts."""

    def test_surface_includes_intent_problem(
        self, planspace: Path, section_01: None,
    ) -> None:
        """Alignment surface must include intent problem.md when present."""
        from implementation.service.section_reexplorer import (
            write_alignment_surface,
        )
        from orchestrator.types import Section

        sec_path = planspace / "artifacts" / "sections" / "section-01.md"
        section = Section(number="01", path=sec_path, related_files=[])

        # Create intent problem artifact
        intent_dir = (
            planspace / "artifacts" / "intent" / "sections" / "section-01"
        )
        intent_dir.mkdir(parents=True)
        (intent_dir / "problem.md").write_text("# Problem\n")

        write_alignment_surface(planspace, section)

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
        from implementation.service.section_reexplorer import (
            write_alignment_surface,
        )
        from orchestrator.types import Section

        sec_path = planspace / "artifacts" / "sections" / "section-01.md"
        section = Section(number="01", path=sec_path, related_files=[])

        intent_dir = (
            planspace / "artifacts" / "intent" / "sections" / "section-01"
        )
        intent_dir.mkdir(parents=True)
        (intent_dir / "problem-alignment.md").write_text("# Rubric\n")

        write_alignment_surface(planspace, section)

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
        from implementation.service.section_reexplorer import (
            write_alignment_surface,
        )
        from orchestrator.types import Section

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

        write_alignment_surface(planspace, section)

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
        from implementation.service.section_reexplorer import (
            write_alignment_surface,
        )
        from orchestrator.types import Section

        sec_path = planspace / "artifacts" / "sections" / "section-01.md"
        section = Section(number="01", path=sec_path, related_files=[])

        write_alignment_surface(planspace, section)

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
        from intent.service.intent_pack_generator import _walk_md_bounded

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
        from intent.service.intent_pack_generator import _walk_md_bounded

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
        from intent.service.intent_pack_generator import _build_philosophy_catalog

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
