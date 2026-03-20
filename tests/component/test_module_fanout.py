"""Unit tests for build_module_fanout (Piece 4g).

Tests the pure function that constructs fanout flow declarations from
parsed skeleton module entries.
"""

from __future__ import annotations

from scan.codemap.codemap_builder import build_module_fanout
from scan.codemap.skeleton_parser import ModuleEntry
from flow.types.schema import BranchSpec, GateSpec, TaskSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MODULES = [
    ModuleEntry(name="api", path="src/api", description="HTTP API layer"),
    ModuleEntry(name="core", path="src/core", description="Business logic"),
    ModuleEntry(name="db", path="src/db", description="Database access"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildModuleFanout:
    """build_module_fanout constructs BranchSpec + GateSpec from modules."""

    def test_returns_one_branch_per_module(self) -> None:
        branches, _gate = build_module_fanout(MODULES)
        assert len(branches) == 3

    def test_branch_labels_include_module_name(self) -> None:
        branches, _gate = build_module_fanout(MODULES)
        labels = {b.label for b in branches}
        assert labels == {"module-api", "module-core", "module-db"}

    def test_branch_steps_dispatch_module_explore(self) -> None:
        branches, _gate = build_module_fanout(MODULES)
        for branch in branches:
            assert len(branch.steps) == 1
            assert branch.steps[0].task_type == "scan.module_explore"

    def test_branch_payload_path_matches_module_path(self) -> None:
        branches, _gate = build_module_fanout(MODULES)
        by_label = {b.label: b for b in branches}
        assert by_label["module-api"].steps[0].payload_path == "src/api"
        assert by_label["module-core"].steps[0].payload_path == "src/core"
        assert by_label["module-db"].steps[0].payload_path == "src/db"

    def test_branch_concern_scope_includes_module_name(self) -> None:
        branches, _gate = build_module_fanout(MODULES)
        by_label = {b.label: b for b in branches}
        assert by_label["module-api"].steps[0].concern_scope == "module-api"

    def test_gate_mode_is_all(self) -> None:
        _branches, gate = build_module_fanout(MODULES)
        assert gate.mode == "all"

    def test_gate_failure_policy_is_include(self) -> None:
        _branches, gate = build_module_fanout(MODULES)
        assert gate.failure_policy == "include"

    def test_gate_synthesis_dispatches_codemap_synthesize(self) -> None:
        _branches, gate = build_module_fanout(MODULES)
        assert gate.synthesis is not None
        assert gate.synthesis.task_type == "scan.codemap_synthesize"

    def test_returns_branchspec_and_gatespec_types(self) -> None:
        branches, gate = build_module_fanout(MODULES)
        for branch in branches:
            assert isinstance(branch, BranchSpec)
        assert isinstance(gate, GateSpec)

    def test_empty_modules_returns_empty_branches(self) -> None:
        branches, gate = build_module_fanout([])
        assert branches == []
        # Gate is still constructed (synthesis point exists regardless)
        assert gate.synthesis is not None

    def test_single_module(self) -> None:
        modules = [ModuleEntry(name="mono", path="src", description="All code")]
        branches, gate = build_module_fanout(modules)
        assert len(branches) == 1
        assert branches[0].label == "module-mono"
        assert branches[0].steps[0].payload_path == "src"

    def test_branches_have_no_chain_ref(self) -> None:
        """Branches use inline steps, not chain_ref packages."""
        branches, _gate = build_module_fanout(MODULES)
        for branch in branches:
            assert branch.chain_ref == ""

    def test_pure_function_no_side_effects(self) -> None:
        """Calling twice with same input produces equal output."""
        result1 = build_module_fanout(MODULES)
        result2 = build_module_fanout(MODULES)
        b1, g1 = result1
        b2, g2 = result2
        assert len(b1) == len(b2)
        for a, b in zip(b1, b2):
            assert a.label == b.label
            assert a.steps[0].task_type == b.steps[0].task_type
        assert g1.mode == g2.mode
        assert g1.synthesis.task_type == g2.synthesis.task_type
