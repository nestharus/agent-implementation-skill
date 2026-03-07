"""Component tests for PathRegistry: centralized artifact path construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scripts.lib.path_registry import PathRegistry


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_stores_planspace(self, tmp_path: Path) -> None:
        reg = PathRegistry(tmp_path)
        assert reg.planspace == tmp_path

    def test_artifacts_property(self, tmp_path: Path) -> None:
        reg = PathRegistry(tmp_path)
        assert reg.artifacts == tmp_path / "artifacts"


# ---------------------------------------------------------------------------
# Directory accessors
# ---------------------------------------------------------------------------


class TestDirectoryAccessors:
    @pytest.fixture()
    def reg(self, tmp_path: Path) -> PathRegistry:
        return PathRegistry(tmp_path)

    def test_sections_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.sections_dir() == tmp_path / "artifacts" / "sections"

    def test_proposals_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.proposals_dir() == tmp_path / "artifacts" / "proposals"

    def test_signals_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.signals_dir() == tmp_path / "artifacts" / "signals"

    def test_notes_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.notes_dir() == tmp_path / "artifacts" / "notes"

    def test_decisions_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.decisions_dir() == tmp_path / "artifacts" / "decisions"

    def test_todos_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.todos_dir() == tmp_path / "artifacts" / "todos"

    def test_coordination_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.coordination_dir() == tmp_path / "artifacts" / "coordination"

    def test_reconciliation_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.reconciliation_dir() == tmp_path / "artifacts" / "reconciliation"

    def test_scope_deltas_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.scope_deltas_dir() == tmp_path / "artifacts" / "scope-deltas"


# ---------------------------------------------------------------------------
# Section-scoped file accessors
# ---------------------------------------------------------------------------


class TestSectionScopedAccessors:
    @pytest.fixture()
    def reg(self, tmp_path: Path) -> PathRegistry:
        return PathRegistry(tmp_path)

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_section_spec(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.section_spec(num) == (
            tmp_path / "artifacts" / "sections" / f"section-{num}.md"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_proposal(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.proposal(num) == (
            tmp_path / "artifacts" / "proposals"
            / f"section-{num}-integration-proposal.md"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_proposal_excerpt(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.proposal_excerpt(num) == (
            tmp_path / "artifacts" / "proposals"
            / f"section-{num}-proposal-excerpt.md"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_alignment_excerpt(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.alignment_excerpt(num) == (
            tmp_path / "artifacts" / f"alignment-excerpt-{num}.md"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_microstrategy(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.microstrategy(num) == (
            tmp_path / "artifacts" / "proposals"
            / f"section-{num}-microstrategy.md"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_problem_frame(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.problem_frame(num) == (
            tmp_path / "artifacts" / "signals"
            / f"section-{num}-problem-frame.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_cycle_budget(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.cycle_budget(num) == (
            tmp_path / "artifacts" / "signals"
            / f"section-{num}-cycle-budget.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_mode_signal(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.mode_signal(num) == (
            tmp_path / "artifacts" / "signals" / f"section-{num}-mode.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_blocker_signal(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.blocker_signal(num) == (
            tmp_path / "artifacts" / "signals"
            / f"section-{num}-blocker.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_microstrategy_signal(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.microstrategy_signal(num) == (
            tmp_path / "artifacts" / "signals"
            / f"proposal-{num}-microstrategy.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_todos(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.todos(num) == (
            tmp_path / "artifacts" / "todos" / f"section-{num}-todos.md"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_trace_map(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.trace_map(num) == (
            tmp_path / "artifacts" / f"trace-map-{num}.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_impl_modified(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.impl_modified(num) == (
            tmp_path / "artifacts" / f"impl-{num}-modified.txt"
        )


# ---------------------------------------------------------------------------
# Global file accessors
# ---------------------------------------------------------------------------


class TestGlobalAccessors:
    @pytest.fixture()
    def reg(self, tmp_path: Path) -> PathRegistry:
        return PathRegistry(tmp_path)

    def test_codemap(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.codemap() == tmp_path / "artifacts" / "codemap.json"

    def test_corrections(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.corrections() == tmp_path / "artifacts" / "codemap-corrections.json"

    def test_tool_registry(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.tool_registry() == tmp_path / "artifacts" / "tool-registry.json"

    def test_project_mode_json(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.project_mode_json() == (
            tmp_path / "artifacts" / "signals" / "project-mode.json"
        )

    def test_project_mode_txt(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.project_mode_txt() == tmp_path / "artifacts" / "project-mode.txt"

    def test_mode_contract(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.mode_contract() == tmp_path / "artifacts" / "mode-contract.json"

    def test_model_policy(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.model_policy() == tmp_path / "artifacts" / "model-policy.json"

    def test_strategic_state(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.strategic_state() == tmp_path / "artifacts" / "strategic-state.json"

    def test_run_db(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.run_db() == tmp_path / "run.db"


# ---------------------------------------------------------------------------
# Composability and isolation
# ---------------------------------------------------------------------------


class TestComposabilityAndIsolation:
    def test_paths_are_composable(self, tmp_path: Path) -> None:
        """Directory accessors return Path objects that support further /."""
        reg = PathRegistry(tmp_path)
        custom = reg.signals_dir() / "custom-signal.json"
        assert custom == tmp_path / "artifacts" / "signals" / "custom-signal.json"

    def test_different_planspaces_produce_different_paths(self, tmp_path: Path) -> None:
        ps_a = tmp_path / "plan-a"
        ps_b = tmp_path / "plan-b"
        reg_a = PathRegistry(ps_a)
        reg_b = PathRegistry(ps_b)

        assert reg_a.artifacts != reg_b.artifacts
        assert reg_a.codemap() != reg_b.codemap()
        assert reg_a.section_spec("01") != reg_b.section_spec("01")
        assert reg_a.run_db() != reg_b.run_db()

    def test_artifacts_is_child_of_planspace(self, tmp_path: Path) -> None:
        reg = PathRegistry(tmp_path)
        assert reg.artifacts.parent == reg.planspace
