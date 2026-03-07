"""Tests for flow_schema.py — flow declaration parsing and validation.

Covers:
- Legacy single-task JSON normalization
- Legacy JSON array normalization
- Legacy JSONL normalization
- v2 envelope parsing (chain and fanout actions)
- Validation rules (version, chain count, task_type, chain_ref, gate)
- Malformed input rejection (fail-closed)
- Integration with task_ingestion.py (legacy passthrough, v2 skip)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flow_schema import (
    BranchSpec,
    ChainAction,
    FanoutAction,
    FlowDeclaration,
    GateSpec,
    TaskSpec,
    normalize_flow_declaration,
    parse_flow_signal,
    validate_flow_declaration,
)


# ---------------------------------------------------------------------------
# Data structure construction
# ---------------------------------------------------------------------------

class TestDataStructures:
    """Basic data structure construction and defaults."""

    def test_task_spec_defaults(self) -> None:
        ts = TaskSpec(task_type="alignment_check")
        assert ts.task_type == "alignment_check"
        assert ts.concern_scope == ""
        assert ts.payload_path == ""
        assert ts.priority == "normal"
        assert ts.problem_id == ""

    def test_chain_action_defaults(self) -> None:
        ca = ChainAction()
        assert ca.kind == "chain"
        assert ca.steps == []

    def test_fanout_action_defaults(self) -> None:
        fa = FanoutAction()
        assert fa.kind == "fanout"
        assert fa.branches == []
        assert fa.gate is None

    def test_gate_spec_defaults(self) -> None:
        gs = GateSpec()
        assert gs.mode == "all"
        assert gs.failure_policy == "include"
        assert gs.synthesis is None

    def test_branch_spec_defaults(self) -> None:
        bs = BranchSpec()
        assert bs.label == ""
        assert bs.chain_ref == ""
        assert bs.args == {}
        assert bs.steps == []

    def test_flow_declaration_construction(self) -> None:
        fd = FlowDeclaration(version=2, actions=[])
        assert fd.version == 2
        assert fd.actions == []


# ---------------------------------------------------------------------------
# normalize_flow_declaration
# ---------------------------------------------------------------------------

class TestNormalizeFlowDeclaration:
    """Tests for normalize_flow_declaration()."""

    def test_legacy_single_dict(self) -> None:
        raw = {"task_type": "alignment_check", "concern_scope": "auth"}
        decl = normalize_flow_declaration(raw)
        assert decl.version == 1
        assert len(decl.actions) == 1
        assert isinstance(decl.actions[0], ChainAction)
        assert len(decl.actions[0].steps) == 1
        assert decl.actions[0].steps[0].task_type == "alignment_check"
        assert decl.actions[0].steps[0].concern_scope == "auth"

    def test_legacy_array(self) -> None:
        raw = [
            {"task_type": "alignment_check"},
            {"task_type": "impact_analysis"},
        ]
        decl = normalize_flow_declaration(raw)
        assert decl.version == 1
        assert len(decl.actions) == 1
        assert len(decl.actions[0].steps) == 2
        assert decl.actions[0].steps[0].task_type == "alignment_check"
        assert decl.actions[0].steps[1].task_type == "impact_analysis"

    def test_legacy_array_skips_non_task_entries(self) -> None:
        """Array entries without task_type are silently dropped."""
        raw = [
            {"task_type": "alignment_check"},
            {"not_a_task": True},
        ]
        decl = normalize_flow_declaration(raw)
        assert len(decl.actions[0].steps) == 1

    def test_legacy_empty_array_raises(self) -> None:
        with pytest.raises(ValueError, match="no valid task entries"):
            normalize_flow_declaration([])

    def test_v2_envelope_chain(self) -> None:
        raw = {
            "version": 2,
            "actions": [
                {
                    "kind": "chain",
                    "steps": [
                        {"task_type": "alignment_check"},
                        {"task_type": "impact_analysis"},
                    ],
                }
            ],
        }
        decl = normalize_flow_declaration(raw)
        assert decl.version == 2
        assert len(decl.actions) == 1
        assert isinstance(decl.actions[0], ChainAction)
        assert len(decl.actions[0].steps) == 2

    def test_v2_envelope_fanout(self) -> None:
        raw = {
            "version": 2,
            "actions": [
                {
                    "kind": "fanout",
                    "branches": [
                        {
                            "label": "branch-a",
                            "steps": [{"task_type": "alignment_check"}],
                        },
                        {
                            "label": "branch-b",
                            "steps": [{"task_type": "impact_analysis"}],
                        },
                    ],
                    "gate": {"mode": "all"},
                }
            ],
        }
        decl = normalize_flow_declaration(raw)
        assert decl.version == 2
        assert len(decl.actions) == 1
        action = decl.actions[0]
        assert isinstance(action, FanoutAction)
        assert len(action.branches) == 2
        assert action.branches[0].label == "branch-a"
        assert action.gate is not None
        assert action.gate.mode == "all"

    def test_v2_envelope_missing_version_raises(self) -> None:
        raw = {"actions": [{"kind": "chain", "steps": []}]}
        with pytest.raises(ValueError, match="must include 'version'"):
            normalize_flow_declaration(raw)

    def test_v2_envelope_unknown_action_kind_preserved(self) -> None:
        """Unknown action kinds are kept as raw dicts for validation."""
        raw = {
            "version": 2,
            "actions": [{"kind": "unknown_thing", "data": 42}],
        }
        decl = normalize_flow_declaration(raw)
        assert len(decl.actions) == 1
        # Should be the raw dict, not a typed action
        assert isinstance(decl.actions[0], dict)

    def test_unexpected_type_raises(self) -> None:
        with pytest.raises(ValueError, match="unexpected type"):
            normalize_flow_declaration(42)

    def test_legacy_preserves_all_fields(self) -> None:
        raw = {
            "task_type": "impact_analysis",
            "concern_scope": "payments",
            "payload_path": "/tmp/prompt.md",
            "priority": "high",
            "problem_id": "P-123",
        }
        decl = normalize_flow_declaration(raw)
        step = decl.actions[0].steps[0]
        assert step.task_type == "impact_analysis"
        assert step.concern_scope == "payments"
        assert step.payload_path == "/tmp/prompt.md"
        assert step.priority == "high"
        assert step.problem_id == "P-123"

    def test_fanout_gate_with_synthesis(self) -> None:
        raw = {
            "version": 2,
            "actions": [
                {
                    "kind": "fanout",
                    "branches": [
                        {"steps": [{"task_type": "alignment_check"}]},
                    ],
                    "gate": {
                        "mode": "all",
                        "synthesis": {"task_type": "impact_analysis"},
                    },
                }
            ],
        }
        decl = normalize_flow_declaration(raw)
        gate = decl.actions[0].gate
        assert gate is not None
        assert gate.synthesis is not None
        assert gate.synthesis.task_type == "impact_analysis"


# ---------------------------------------------------------------------------
# parse_flow_signal (file-based)
# ---------------------------------------------------------------------------

class TestParseFlowSignal:
    """Tests for parse_flow_signal() — file I/O layer."""

    def test_legacy_single_json(self, tmp_path: Path) -> None:
        p = tmp_path / "task.json"
        p.write_text(json.dumps({"task_type": "alignment_check"}))
        decl = parse_flow_signal(p)
        assert decl.version == 1
        assert decl.actions[0].steps[0].task_type == "alignment_check"

    def test_legacy_json_array(self, tmp_path: Path) -> None:
        p = tmp_path / "tasks.json"
        p.write_text(json.dumps([
            {"task_type": "alignment_check"},
            {"task_type": "impact_analysis"},
        ]))
        decl = parse_flow_signal(p)
        assert len(decl.actions[0].steps) == 2

    def test_jsonl_format(self, tmp_path: Path) -> None:
        p = tmp_path / "tasks.jsonl"
        lines = [
            json.dumps({"task_type": "alignment_check"}),
            json.dumps({"task_type": "impact_analysis"}),
        ]
        p.write_text("\n".join(lines))
        decl = parse_flow_signal(p)
        assert decl.version == 1
        assert len(decl.actions[0].steps) == 2

    def test_v2_envelope_file(self, tmp_path: Path) -> None:
        p = tmp_path / "flow.json"
        p.write_text(json.dumps({
            "version": 2,
            "actions": [
                {"kind": "chain", "steps": [{"task_type": "alignment_check"}]},
            ],
        }))
        decl = parse_flow_signal(p)
        assert decl.version == 2

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            parse_flow_signal(tmp_path / "nonexistent.json")

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text("")
        with pytest.raises(ValueError, match="empty"):
            parse_flow_signal(p)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        with pytest.raises(ValueError, match="Malformed JSONL"):
            parse_flow_signal(p)

    def test_whitespace_only_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "ws.json"
        p.write_text("   \n  \n  ")
        with pytest.raises(ValueError, match="empty"):
            parse_flow_signal(p)


# ---------------------------------------------------------------------------
# validate_flow_declaration
# ---------------------------------------------------------------------------

class TestValidateFlowDeclaration:
    """Tests for validate_flow_declaration()."""

    def test_valid_legacy_declaration(self) -> None:
        decl = FlowDeclaration(
            version=1,
            actions=[ChainAction(steps=[
                TaskSpec(task_type="alignment_check", payload_path="artifacts/prompt.md"),
            ])],
        )
        errors = validate_flow_declaration(decl)
        assert errors == []

    def test_valid_v2_chain(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[ChainAction(steps=[
                TaskSpec(task_type="alignment_check", payload_path="artifacts/p1.md"),
                TaskSpec(task_type="impact_analysis", payload_path="artifacts/p2.md"),
            ])],
        )
        errors = validate_flow_declaration(decl)
        assert errors == []

    def test_unsupported_version(self) -> None:
        decl = FlowDeclaration(version=99, actions=[])
        errors = validate_flow_declaration(decl)
        assert any("Unsupported version" in e for e in errors)

    def test_empty_actions_rejected(self) -> None:
        decl = FlowDeclaration(version=2, actions=[])
        errors = validate_flow_declaration(decl)
        assert any("empty" in e for e in errors)

    def test_multiple_chains_rejected(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[
                ChainAction(steps=[TaskSpec(task_type="alignment_check")]),
                ChainAction(steps=[TaskSpec(task_type="impact_analysis")]),
            ],
        )
        errors = validate_flow_declaration(decl)
        assert any("At most one" in e for e in errors)

    def test_unknown_task_type_rejected(self) -> None:
        decl = FlowDeclaration(
            version=1,
            actions=[ChainAction(steps=[
                TaskSpec(task_type="nonexistent_task_xyz"),
            ])],
        )
        errors = validate_flow_declaration(decl)
        assert any("unknown task_type" in e for e in errors)
        assert any("nonexistent_task_xyz" in e for e in errors)

    def test_missing_task_type_rejected(self) -> None:
        decl = FlowDeclaration(
            version=1,
            actions=[ChainAction(steps=[TaskSpec(task_type="")])],
        )
        errors = validate_flow_declaration(decl)
        assert any("missing task_type" in e for e in errors)

    def test_chain_no_steps_rejected(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[ChainAction(steps=[])],
        )
        errors = validate_flow_declaration(decl)
        assert any("no steps" in e for e in errors)

    def test_fanout_branch_both_steps_and_ref_rejected(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[FanoutAction(branches=[
                BranchSpec(
                    chain_ref="some-package",
                    steps=[TaskSpec(task_type="alignment_check")],
                ),
            ])],
        )
        errors = validate_flow_declaration(decl)
        assert any("not both" in e for e in errors)

    def test_fanout_branch_neither_steps_nor_ref_rejected(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[FanoutAction(branches=[
                BranchSpec(label="empty"),
            ])],
        )
        errors = validate_flow_declaration(decl)
        assert any("either steps or chain_ref" in e for e in errors)

    def test_fanout_no_branches_rejected(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[FanoutAction(branches=[])],
        )
        errors = validate_flow_declaration(decl)
        assert any("no branches" in e for e in errors)

    def test_unknown_chain_ref_rejected(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[FanoutAction(branches=[
                BranchSpec(chain_ref="nonexistent-package"),
            ])],
        )
        errors = validate_flow_declaration(decl)
        assert any("unknown chain_ref" in e for e in errors)

    def test_unsupported_gate_mode_rejected(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[FanoutAction(
                branches=[
                    BranchSpec(steps=[TaskSpec(task_type="alignment_check")]),
                ],
                gate=GateSpec(mode="any"),
            )],
        )
        errors = validate_flow_declaration(decl)
        assert any("unsupported mode" in e for e in errors)

    def test_gate_synthesis_unknown_task_type(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[FanoutAction(
                branches=[
                    BranchSpec(steps=[TaskSpec(task_type="alignment_check")]),
                ],
                gate=GateSpec(
                    synthesis=TaskSpec(task_type="bogus_synthesis_task"),
                ),
            )],
        )
        errors = validate_flow_declaration(decl)
        assert any("synthesis" in e and "unknown" in e for e in errors)

    def test_unknown_action_kind_reported(self) -> None:
        """Raw dict actions (from unknown kind) are caught in validation."""
        decl = FlowDeclaration(
            version=2,
            actions=[{"kind": "mysterious", "data": 1}],
        )
        errors = validate_flow_declaration(decl)
        assert any("unknown action kind" in e for e in errors)

    def test_valid_fanout_with_gate_all(self) -> None:
        decl = FlowDeclaration(
            version=2,
            actions=[FanoutAction(
                branches=[
                    BranchSpec(steps=[TaskSpec(task_type="alignment_check", payload_path="p1.md")]),
                    BranchSpec(steps=[TaskSpec(task_type="impact_analysis", payload_path="p2.md")]),
                ],
                gate=GateSpec(mode="all"),
            )],
        )
        errors = validate_flow_declaration(decl)
        assert errors == []

    def test_chain_plus_fanout_valid(self) -> None:
        """One chain + one fanout is valid (at most one chain)."""
        decl = FlowDeclaration(
            version=2,
            actions=[
                ChainAction(steps=[TaskSpec(task_type="alignment_check", payload_path="p1.md")]),
                FanoutAction(branches=[
                    BranchSpec(steps=[TaskSpec(task_type="impact_analysis", payload_path="p2.md")]),
                ]),
            ],
        )
        errors = validate_flow_declaration(decl)
        assert errors == []


# ---------------------------------------------------------------------------
# Integration: task_ingestion.py uses flow_schema
# ---------------------------------------------------------------------------

class TestTaskIngestionIntegration:
    """Verify task_ingestion.py still works for legacy and rejects v2."""

    def test_legacy_single_task_ingest(self, tmp_path: Path) -> None:
        """Legacy single-task JSON still returns a valid task list."""
        from section_loop.task_ingestion import ingest_task_requests

        sig = tmp_path / "task.json"
        sig.write_text(json.dumps({
            "task_type": "alignment_check",
            "concern_scope": "auth",
        }))
        tasks = ingest_task_requests(sig)
        assert len(tasks) == 1
        assert tasks[0]["task_type"] == "alignment_check"
        assert tasks[0]["concern_scope"] == "auth"
        # Signal file should be deleted after ingestion
        assert not sig.exists()

    def test_legacy_jsonl_ingest(self, tmp_path: Path) -> None:
        """Legacy JSONL still returns valid task list."""
        from section_loop.task_ingestion import ingest_task_requests

        sig = tmp_path / "tasks.jsonl"
        lines = [
            json.dumps({"task_type": "alignment_check"}),
            json.dumps({"task_type": "impact_analysis"}),
        ]
        sig.write_text("\n".join(lines))
        tasks = ingest_task_requests(sig)
        assert len(tasks) == 2

    def test_legacy_json_array_ingest(self, tmp_path: Path) -> None:
        """Legacy JSON array still returns valid task list."""
        from section_loop.task_ingestion import ingest_task_requests

        sig = tmp_path / "tasks.json"
        sig.write_text(json.dumps([
            {"task_type": "alignment_check"},
            {"task_type": "impact_analysis"},
        ]))
        tasks = ingest_task_requests(sig)
        assert len(tasks) == 2

    def test_v2_declaration_skipped(self, tmp_path: Path) -> None:
        """v2 flow declarations are validated but not dispatched."""
        from section_loop.task_ingestion import ingest_task_requests

        sig = tmp_path / "flow.json"
        sig.write_text(json.dumps({
            "version": 2,
            "actions": [
                {
                    "kind": "chain",
                    "steps": [{"task_type": "alignment_check", "payload_path": "artifacts/p.md"}],
                },
            ],
        }))
        tasks = ingest_task_requests(sig)
        assert tasks == []
        # Signal file should be cleaned up
        assert not sig.exists()

    def test_v2_invalid_declaration_rejected(self, tmp_path: Path) -> None:
        """Invalid v2 declarations are rejected and renamed."""
        from section_loop.task_ingestion import ingest_task_requests

        sig = tmp_path / "bad-flow.json"
        sig.write_text(json.dumps({
            "version": 2,
            "actions": [
                {
                    "kind": "chain",
                    "steps": [{"task_type": "nonexistent_xyz"}],
                },
            ],
        }))
        tasks = ingest_task_requests(sig)
        assert tasks == []
        # Should be renamed to .malformed.json
        assert (tmp_path / "bad-flow.malformed.json").exists()

    def test_malformed_json_rejected(self, tmp_path: Path) -> None:
        """Malformed JSON is renamed to .malformed.json."""
        from section_loop.task_ingestion import ingest_task_requests

        sig = tmp_path / "broken.json"
        sig.write_text("{not valid json at all")
        tasks = ingest_task_requests(sig)
        assert tasks == []
        assert (tmp_path / "broken.malformed.json").exists()

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Non-existent file returns empty list."""
        from section_loop.task_ingestion import ingest_task_requests

        tasks = ingest_task_requests(tmp_path / "nonexistent.json")
        assert tasks == []

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        """Empty file returns empty list and is cleaned up."""
        from section_loop.task_ingestion import ingest_task_requests

        sig = tmp_path / "empty.json"
        sig.write_text("")
        tasks = ingest_task_requests(sig)
        assert tasks == []

    def test_legacy_preserves_optional_fields(self, tmp_path: Path) -> None:
        """Legacy tasks with priority and problem_id are preserved."""
        from section_loop.task_ingestion import ingest_task_requests

        sig = tmp_path / "task.json"
        sig.write_text(json.dumps({
            "task_type": "alignment_check",
            "concern_scope": "payments",
            "payload_path": "/tmp/prompt.md",
            "priority": "high",
            "problem_id": "P-42",
        }))
        tasks = ingest_task_requests(sig)
        assert len(tasks) == 1
        t = tasks[0]
        assert t["task_type"] == "alignment_check"
        assert t["concern_scope"] == "payments"
        assert t["payload_path"] == "/tmp/prompt.md"
        assert t["priority"] == "high"
        assert t["problem_id"] == "P-42"

    def test_legacy_normal_priority_not_included(self, tmp_path: Path) -> None:
        """Normal priority is the default and not explicitly included."""
        from section_loop.task_ingestion import ingest_task_requests

        sig = tmp_path / "task.json"
        sig.write_text(json.dumps({
            "task_type": "alignment_check",
            "priority": "normal",
        }))
        tasks = ingest_task_requests(sig)
        assert len(tasks) == 1
        # priority=normal is the default, so it's omitted from the dict
        assert "priority" not in tasks[0]
