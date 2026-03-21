"""Component tests for PathRegistry: centralized artifact path construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.orchestrator.path_registry import PathRegistry


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

    def test_readiness_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.readiness_dir() == tmp_path / "artifacts" / "readiness"

    def test_coordination_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.coordination_dir() == tmp_path / "artifacts" / "coordination"

    def test_reconciliation_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.reconciliation_dir() == tmp_path / "artifacts" / "reconciliation"

    def test_scope_deltas_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.scope_deltas_dir() == tmp_path / "artifacts" / "scope-deltas"

    def test_contracts_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.contracts_dir() == tmp_path / "artifacts" / "contracts"

    def test_inputs_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.inputs_dir() == tmp_path / "artifacts" / "inputs"

    def test_trace_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.trace_dir() == tmp_path / "artifacts" / "trace"

    def test_flows_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.flows_dir() == tmp_path / "artifacts" / "flows"

    def test_results_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.results_dir() == tmp_path / "artifacts" / "results"

    def test_qa_intercepts_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.qa_intercepts_dir() == tmp_path / "artifacts" / "qa-intercepts"

    def test_substrate_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.substrate_dir() == tmp_path / "artifacts" / "substrate"

    def test_substrate_prompts_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.substrate_prompts_dir() == (
            tmp_path / "artifacts" / "substrate" / "prompts"
        )

    def test_intent_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.intent_dir() == tmp_path / "artifacts" / "intent"

    def test_intent_global_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.intent_global_dir() == (
            tmp_path / "artifacts" / "intent" / "global"
        )

    def test_intent_sections_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.intent_sections_dir() == (
            tmp_path / "artifacts" / "intent" / "sections"
        )

    def test_governance_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.governance_dir() == (
            tmp_path / "artifacts" / "governance"
        )

    def test_section_inputs_hashes_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.section_inputs_hashes_dir() == (
            tmp_path / "artifacts" / "section-inputs-hashes"
        )

    def test_phase2_inputs_hashes_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.phase2_inputs_hashes_dir() == (
            tmp_path / "artifacts" / "phase2-inputs-hashes"
        )

    def test_related_files_update_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.related_files_update_dir() == (
            tmp_path / "artifacts" / "signals" / "related-files-update"
        )


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
            tmp_path / "artifacts" / "sections"
            / f"section-{num}-proposal-excerpt.md"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_alignment_excerpt(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.alignment_excerpt(num) == (
            tmp_path / "artifacts" / "sections"
            / f"section-{num}-alignment-excerpt.md"
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
            tmp_path / "artifacts" / "sections"
            / f"section-{num}-problem-frame.md"
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
    def test_scope_expansion_signal(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.scope_expansion_signal(num) == (
            tmp_path / "artifacts" / "signals"
            / f"scope-expansion-{num}.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_microstrategy_signal(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.microstrategy_signal(num) == (
            tmp_path / "artifacts" / "signals"
            / f"proposal-{num}-microstrategy.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_impl_feedback_surfaces(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.impl_feedback_surfaces(num) == (
            tmp_path / "artifacts" / "signals"
            / f"impl-feedback-surfaces-{num}.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_todos(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.todos(num) == (
            tmp_path / "artifacts" / "todos" / f"section-{num}-todos.md"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_trace_map(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.trace_map(num) == (
            tmp_path / "artifacts" / "trace-map" / f"section-{num}.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_impl_modified(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.impl_modified(num) == (
            tmp_path / "artifacts" / f"impl-{num}-modified.txt"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_input_refs_dir(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.input_refs_dir(num) == (
            tmp_path / "artifacts" / "inputs" / f"section-{num}"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_intent_section_dir(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.intent_section_dir(num) == (
            tmp_path / "artifacts" / "intent" / "sections" / f"section-{num}"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_section_input_hash(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.section_input_hash(num) == (
            tmp_path / "artifacts" / "section-inputs-hashes" / f"{num}.hash"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_phase2_input_hash(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.phase2_input_hash(num) == (
            tmp_path / "artifacts" / "phase2-inputs-hashes" / f"{num}.hash"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_governance_packet(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.governance_packet(num) == (
            tmp_path / "artifacts" / "governance"
            / f"section-{num}-governance-packet.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_post_impl_assessment(self, reg: PathRegistry, tmp_path: Path, num: str) -> None:
        assert reg.post_impl_assessment(num) == (
            tmp_path / "artifacts" / "governance"
            / f"section-{num}-post-impl-assessment.json"
        )

    @pytest.mark.parametrize("num", ["01", "12"])
    def test_post_impl_assessment_prompt(
        self,
        reg: PathRegistry,
        tmp_path: Path,
        num: str,
    ) -> None:
        assert reg.post_impl_assessment_prompt(num) == (
            tmp_path / "artifacts" / f"post-impl-{num}-prompt.md"
        )


# ---------------------------------------------------------------------------
# Global file accessors
# ---------------------------------------------------------------------------


class TestGlobalAccessors:
    @pytest.fixture()
    def reg(self, tmp_path: Path) -> PathRegistry:
        return PathRegistry(tmp_path)

    def test_codemap(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.codemap() == tmp_path / "artifacts" / "codemap.md"

    def test_corrections(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.corrections() == (
            tmp_path / "artifacts" / "signals" / "codemap-corrections.json"
        )

    def test_tool_registry(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.tool_registry() == tmp_path / "artifacts" / "tool-registry.json"

    def test_tool_digest(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.tool_digest() == tmp_path / "artifacts" / "tool-digest.md"

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

    def test_parameters(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.parameters() == tmp_path / "artifacts" / "parameters.json"

    def test_traceability(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.traceability() == tmp_path / "artifacts" / "traceability.json"

    def test_governance_problem_index(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.governance_problem_index() == (
            tmp_path / "artifacts" / "governance" / "problem-index.json"
        )

    def test_governance_pattern_index(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.governance_pattern_index() == (
            tmp_path / "artifacts" / "governance" / "pattern-index.json"
        )

    def test_governance_profile_index(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.governance_profile_index() == (
            tmp_path / "artifacts" / "governance" / "profile-index.json"
        )

    def test_governance_region_profile_map(
        self,
        reg: PathRegistry,
        tmp_path: Path,
    ) -> None:
        assert reg.governance_region_profile_map() == (
            tmp_path / "artifacts" / "governance" / "region-profile-map.json"
        )

    def test_alignment_changed_flag(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.alignment_changed_flag() == (
            tmp_path / "artifacts" / "alignment-changed-pending"
        )

    def test_run_db(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.run_db() == tmp_path / "run.db"

    def test_task_result_envelope(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.task_result_envelope(42) == (
            tmp_path / "artifacts" / "results" / "task-42-result.json"
        )


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
