"""Tests for task_flow.py — flow submission engine.

Covers:
- submit_chain: linear dependency wiring, flow context files, ID allocation
- submit_fanout: parallel branches, gate creation, gate members, chain_ref
- Integration with task_router.submit_task and flow_catalog.resolve_chain_ref
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from _paths import DB_SH
from src.orchestrator.path_registry import PathRegistry

from flow.repository.catalog import KNOWN_PACKAGES, resolve_chain_ref
from flow.types.context import FlowEnvelope
from flow.types.schema import BranchSpec, GateSpec, TaskSpec
from containers import Services


def submit_chain(env, steps, **kwargs):
    return Services.flow_ingestion().submit_chain(env, steps, **kwargs)


def submit_fanout(env, branches, **kwargs):
    return Services.flow_ingestion().submit_fanout(env, branches, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_db(db_path: Path) -> None:
    """Initialize a fresh database via db.sh."""
    subprocess.run(
        ["bash", str(DB_SH), "init", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _query_task(db_path: Path, task_id: int) -> dict:
    """Read a task row as a dict."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"Task {task_id} not found")
    return dict(row)


def _query_gate(db_path: Path, gate_id: str) -> dict:
    """Read a gate row as a dict."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM gates WHERE gate_id = ?", (gate_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"Gate {gate_id} not found")
    return dict(row)


def _query_gate_members(db_path: Path, gate_id: str) -> list[dict]:
    """Read all gate members for a gate as list of dicts."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM gate_members WHERE gate_id = ? ORDER BY chain_id",
        (gate_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create and initialize a test database."""
    p = tmp_path / "test.db"
    _init_db(p)
    return p


@pytest.fixture()
def flow_planspace(tmp_path: Path) -> Path:
    """Create a planspace directory for flow context files."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    PathRegistry(ps).ensure_artifacts_tree()
    return ps


# ---------------------------------------------------------------------------
# submit_chain
# ---------------------------------------------------------------------------

class TestSubmitChain:
    """Tests for submit_chain()."""

    def test_empty_steps_returns_empty(self, db_path: Path) -> None:
        result = submit_chain(FlowEnvelope(db_path=db_path, submitted_by="test-agent"), [])
        assert result == []

    def test_single_step_chain(self, db_path: Path) -> None:
        steps = [TaskSpec(task_type="staleness.alignment_check")]
        ids = submit_chain(FlowEnvelope(db_path=db_path, submitted_by="test-agent"), steps)
        assert len(ids) == 1

        task = _query_task(db_path, ids[0])
        assert task["task_type"] == "staleness.alignment_check"
        assert task["submitted_by"] == "test-agent"
        assert task["depends_on"] is None
        assert task["status"] == "pending"

    def test_three_step_chain_dependency_wiring(self, db_path: Path) -> None:
        """3-step chain: step[1] depends on step[0], step[2] on step[1]."""
        steps = [
            TaskSpec(task_type="staleness.alignment_check"),
            TaskSpec(task_type="signals.impact_analysis"),
            TaskSpec(task_type="coordination.fix"),
        ]
        ids = submit_chain(FlowEnvelope(db_path=db_path, submitted_by="test-agent"), steps)
        assert len(ids) == 3

        t0 = _query_task(db_path, ids[0])
        t1 = _query_task(db_path, ids[1])
        t2 = _query_task(db_path, ids[2])

        # First task has no dependency
        assert t0["depends_on"] is None

        # Second depends on first
        assert t1["depends_on"] == str(ids[0])

        # Third depends on second
        assert t2["depends_on"] == str(ids[1])

    def test_shared_chain_id(self, db_path: Path) -> None:
        """All steps in a chain share the same chain_id."""
        steps = [
            TaskSpec(task_type="staleness.alignment_check"),
            TaskSpec(task_type="signals.impact_analysis"),
        ]
        ids = submit_chain(FlowEnvelope(db_path=db_path, submitted_by="test-agent"), steps)

        t0 = _query_task(db_path, ids[0])
        t1 = _query_task(db_path, ids[1])
        assert t0["chain_id"] == t1["chain_id"]
        assert t0["chain_id"].startswith("chain_")

    def test_shared_flow_id(self, db_path: Path) -> None:
        """All steps in a chain share the same flow_id."""
        steps = [
            TaskSpec(task_type="staleness.alignment_check"),
            TaskSpec(task_type="signals.impact_analysis"),
        ]
        ids = submit_chain(FlowEnvelope(db_path=db_path, submitted_by="test-agent"), steps)

        t0 = _query_task(db_path, ids[0])
        t1 = _query_task(db_path, ids[1])
        assert t0["flow_id"] == t1["flow_id"]
        assert t0["flow_id"].startswith("flow_")

    def test_unique_instance_ids(self, db_path: Path) -> None:
        """Each step gets a unique instance_id."""
        steps = [
            TaskSpec(task_type="staleness.alignment_check"),
            TaskSpec(task_type="signals.impact_analysis"),
        ]
        ids = submit_chain(FlowEnvelope(db_path=db_path, submitted_by="test-agent"), steps)

        t0 = _query_task(db_path, ids[0])
        t1 = _query_task(db_path, ids[1])
        assert t0["instance_id"] != t1["instance_id"]
        assert t0["instance_id"].startswith("inst_")
        assert t1["instance_id"].startswith("inst_")

    def test_explicit_flow_and_chain_ids(self, db_path: Path) -> None:
        """Caller-provided flow_id and chain_id are used."""
        steps = [TaskSpec(task_type="staleness.alignment_check")]
        ids = submit_chain(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_custom"),
            steps,
            chain_id="chain_custom",
        )

        task = _query_task(db_path, ids[0])
        assert task["flow_id"] == "flow_custom"
        assert task["chain_id"] == "chain_custom"

    def test_declared_by_task_id_propagated(self, db_path: Path) -> None:
        steps = [TaskSpec(task_type="staleness.alignment_check")]
        ids = submit_chain(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         declared_by_task_id=42),
            steps,
        )

        task = _query_task(db_path, ids[0])
        assert task["declared_by_task_id"] == 42

    def test_flow_context_paths_set(self, db_path: Path) -> None:
        """Each task has flow_context_path, continuation_path, result_manifest_path."""
        steps = [TaskSpec(task_type="staleness.alignment_check")]
        ids = submit_chain(FlowEnvelope(db_path=db_path, submitted_by="test-agent"), steps)
        tid = ids[0]

        task = _query_task(db_path, tid)
        assert task["flow_context_path"] == f"artifacts/flows/task-{tid}-context.json"
        assert task["continuation_path"] == f"artifacts/flows/task-{tid}-continuation.json"
        assert task["result_manifest_path"] == f"artifacts/flows/task-{tid}-result.json"

    def test_flow_context_json_written(
        self, db_path: Path, flow_planspace: Path
    ) -> None:
        """Flow context JSON file is written when planspace provided."""
        steps = [
            TaskSpec(task_type="staleness.alignment_check"),
            TaskSpec(task_type="signals.impact_analysis"),
        ]
        ids = submit_chain(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         origin_refs=["ref-1"], planspace=flow_planspace),
            steps,
        )

        # First task context
        ctx_path = flow_planspace / "artifacts" / "flows" / f"task-{ids[0]}-context.json"
        assert ctx_path.exists()
        ctx = json.loads(ctx_path.read_text())
        assert ctx["task"]["task_id"] == ids[0]
        assert ctx["task"]["task_type"] == "staleness.alignment_check"
        assert ctx["task"]["depends_on"] is None
        assert ctx["origin_refs"] == ["ref-1"]
        assert ctx["previous_result_manifest"] is None
        assert ctx["continuation_path"] == f"artifacts/flows/task-{ids[0]}-continuation.json"
        assert ctx["result_manifest_path"] == f"artifacts/flows/task-{ids[0]}-result.json"

        # Second task context — should reference first task's result
        ctx2_path = flow_planspace / "artifacts" / "flows" / f"task-{ids[1]}-context.json"
        assert ctx2_path.exists()
        ctx2 = json.loads(ctx2_path.read_text())
        assert ctx2["task"]["task_id"] == ids[1]
        assert ctx2["task"]["depends_on"] == ids[0]
        assert ctx2["previous_result_manifest"] == f"artifacts/flows/task-{ids[0]}-result.json"

    def test_no_context_files_without_planspace(self, db_path: Path, tmp_path: Path) -> None:
        """No flow context files created when planspace is None."""
        steps = [TaskSpec(task_type="staleness.alignment_check")]
        submit_chain(FlowEnvelope(db_path=db_path, submitted_by="test-agent"), steps)
        # No artifacts directory should exist in tmp_path
        assert not (tmp_path / "artifacts").exists()

    def test_task_spec_fields_propagated(self, db_path: Path) -> None:
        """TaskSpec fields (concern_scope, payload, priority, problem_id) propagate."""
        steps = [
            TaskSpec(
                task_type="signals.impact_analysis",
                concern_scope="payments",
                payload_path="/tmp/prompt.md",
                priority="high",
                problem_id="P-99",
            ),
        ]
        ids = submit_chain(FlowEnvelope(db_path=db_path, submitted_by="test-agent"), steps)

        task = _query_task(db_path, ids[0])
        assert task["task_type"] == "signals.impact_analysis"
        assert task["concern_scope"] == "payments"
        assert task["payload_path"] == "/tmp/prompt.md"
        assert task["priority"] == "high"
        assert task["problem_id"] == "P-99"


# ---------------------------------------------------------------------------
# submit_fanout
# ---------------------------------------------------------------------------

class TestSubmitFanout:
    """Tests for submit_fanout()."""

    def test_empty_branches_returns_none(self, db_path: Path) -> None:
        result = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_test"),
            [],
        )
        assert result is None

    def test_two_branches_distinct_chain_ids(self, db_path: Path) -> None:
        """Each branch gets its own chain_id under the same flow_id."""
        branches = [
            BranchSpec(
                label="branch-a",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
            BranchSpec(
                label="branch-b",
                steps=[TaskSpec(task_type="signals.impact_analysis")],
            ),
        ]
        submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_fan"),
            branches,
        )

        # Query all tasks
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks ORDER BY id")
        tasks = [dict(r) for r in cur.fetchall()]
        conn.close()

        assert len(tasks) == 2
        # Both share the same flow_id
        assert tasks[0]["flow_id"] == "flow_fan"
        assert tasks[1]["flow_id"] == "flow_fan"
        # Each has a distinct chain_id
        assert tasks[0]["chain_id"] != tasks[1]["chain_id"]
        assert tasks[0]["chain_id"].startswith("chain_")
        assert tasks[1]["chain_id"].startswith("chain_")

    def test_gate_created_when_specified(self, db_path: Path) -> None:
        """Gate row is created with correct attributes."""
        branches = [
            BranchSpec(
                label="a",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
            BranchSpec(
                label="b",
                steps=[TaskSpec(task_type="signals.impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_gated"),
            branches,
            gate=GateSpec(mode="all", failure_policy="include"),
        )

        assert gate_id is not None
        assert gate_id.startswith("gate_")

        gate = _query_gate(db_path, gate_id)
        assert gate["flow_id"] == "flow_gated"
        assert gate["mode"] == "all"
        assert gate["failure_policy"] == "include"
        assert gate["expected_count"] == 2
        assert gate["status"] == "open"

    def test_gate_members_registered(self, db_path: Path) -> None:
        """Each branch is registered as a gate member."""
        branches = [
            BranchSpec(
                label="branch-x",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
            BranchSpec(
                label="branch-y",
                steps=[TaskSpec(task_type="signals.impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_members"),
            branches,
            gate=GateSpec(),
        )

        members = _query_gate_members(db_path, gate_id)
        assert len(members) == 2

        # Each member should have the gate_id, a chain_id, and a leaf_task_id
        for m in members:
            assert m["gate_id"] == gate_id
            assert m["chain_id"].startswith("chain_")
            assert m["leaf_task_id"] is not None
            assert m["status"] == "pending"

        # Labels should be present
        labels = {m["slot_label"] for m in members}
        assert labels == {"branch-x", "branch-y"}

    def test_no_gate_returns_none(self, db_path: Path) -> None:
        """Without a gate spec, no gate is created."""
        branches = [
            BranchSpec(steps=[TaskSpec(task_type="staleness.alignment_check")]),
        ]
        result = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_nogate"),
            branches,
        )
        assert result is None

    def test_gate_synthesis_fields(self, db_path: Path) -> None:
        """Gate synthesis task fields are stored correctly."""
        branches = [
            BranchSpec(steps=[TaskSpec(task_type="staleness.alignment_check")]),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_syn"),
            branches,
            gate=GateSpec(
                synthesis=TaskSpec(
                    task_type="signals.impact_analysis",
                    problem_id="P-syn",
                    concern_scope="payments",
                    payload_path="/tmp/syn.md",
                    priority="high",
                ),
            ),
        )

        gate = _query_gate(db_path, gate_id)
        assert gate["synthesis_task_type"] == "signals.impact_analysis"
        assert gate["synthesis_problem_id"] == "P-syn"
        assert gate["synthesis_concern_scope"] == "payments"
        assert gate["synthesis_payload_path"] == "/tmp/syn.md"
        assert gate["synthesis_priority"] == "high"

    def test_chain_ref_resolution(self, db_path: Path) -> None:
        """chain_ref branches are expanded via flow_catalog."""
        branches = [
            BranchSpec(
                label="proposal",
                chain_ref="proposal_alignment_package",
                args={"concern_scope": "auth"},
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_ref"),
            branches,
            gate=GateSpec(),
        )

        # proposal_alignment_package expands to 2 steps
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks ORDER BY id")
        tasks = [dict(r) for r in cur.fetchall()]
        conn.close()

        assert len(tasks) == 2
        assert tasks[0]["task_type"] == "proposal.integration"
        assert tasks[1]["task_type"] == "staleness.alignment_check"
        # Both in same chain
        assert tasks[0]["chain_id"] == tasks[1]["chain_id"]
        # Second depends on first
        assert tasks[1]["depends_on"] == str(tasks[0]["id"])

    def test_fanout_with_multi_step_branches(self, db_path: Path) -> None:
        """Branches can have multi-step chains."""
        branches = [
            BranchSpec(
                label="long-branch",
                steps=[
                    TaskSpec(task_type="staleness.alignment_check"),
                    TaskSpec(task_type="signals.impact_analysis"),
                    TaskSpec(task_type="coordination.fix"),
                ],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_multi"),
            branches,
            gate=GateSpec(),
        )

        members = _query_gate_members(db_path, gate_id)
        assert len(members) == 1
        # leaf_task_id should be the last task in the branch
        leaf_tid = members[0]["leaf_task_id"]

        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks ORDER BY id")
        tasks = [dict(r) for r in cur.fetchall()]
        conn.close()

        assert len(tasks) == 3
        assert tasks[2]["id"] == leaf_tid
        assert tasks[2]["task_type"] == "coordination.fix"

    def test_fanout_flow_context_written(
        self, db_path: Path, flow_planspace: Path
    ) -> None:
        """Flow context files are written for fanout branch tasks."""
        branches = [
            BranchSpec(
                label="a",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
        ]
        submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_ctx", planspace=flow_planspace),
            branches,
        )

        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id FROM tasks ORDER BY id")
        tasks = [dict(r) for r in cur.fetchall()]
        conn.close()

        for t in tasks:
            ctx_path = flow_planspace / "artifacts" / "flows" / f"task-{t['id']}-context.json"
            assert ctx_path.exists()

    def test_declared_by_propagated_to_gate(self, db_path: Path) -> None:
        """declared_by_task_id is stored on the gate row."""
        branches = [
            BranchSpec(steps=[TaskSpec(task_type="staleness.alignment_check")]),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent",
                         flow_id="flow_decl", declared_by_task_id=99),
            branches,
            gate=GateSpec(),
        )

        gate = _query_gate(db_path, gate_id)
        assert gate["created_by_task_id"] == 99


# ---------------------------------------------------------------------------
# flow_catalog integration
# ---------------------------------------------------------------------------

class TestFlowCatalog:
    """Tests for flow_catalog.py resolution."""

    def test_known_packages_non_empty(self) -> None:
        assert len(KNOWN_PACKAGES) >= 2

    def test_proposal_alignment_package(self) -> None:
        steps = resolve_chain_ref(
            "proposal_alignment_package",
            {"concern_scope": "auth"},
            ["ref-1"],
        )
        assert len(steps) == 2
        assert steps[0].task_type == "proposal.integration"
        assert steps[1].task_type == "staleness.alignment_check"
        assert steps[0].concern_scope == "auth"
        assert steps[1].concern_scope == "auth"

    def test_implementation_alignment_package(self) -> None:
        steps = resolve_chain_ref(
            "implementation_alignment_package",
            {"concern_scope": "payments"},
            [],
        )
        assert len(steps) == 2
        assert steps[0].task_type == "implementation.strategic"
        assert steps[1].task_type == "staleness.alignment_check"

    def test_research_ticket_package(self) -> None:
        steps = resolve_chain_ref(
            "research_ticket_package",
            {
                "concern_scope": "section-03",
                "payload_path": "/tmp/research-ticket.md",
                "problem_id": "research-03-T-01",
            },
            [],
        )
        assert len(steps) == 1
        assert steps[0].task_type == "research.domain_ticket"
        assert steps[0].payload_path == "/tmp/research-ticket.md"
        assert steps[0].problem_id == "research-03-T-01"

    def test_research_code_ticket_package(self) -> None:
        steps = resolve_chain_ref(
            "research_code_ticket_package",
            {
                "concern_scope": "section-03",
                "scan_payload_path": "/tmp/research-scan.md",
                "payload_path": "/tmp/research-ticket.md",
                "problem_id": "research-03-T-02",
            },
            [],
        )
        assert len(steps) == 2
        assert steps[0].task_type == "scan.explore"
        assert steps[0].payload_path == "/tmp/research-scan.md"
        assert steps[1].task_type == "research.domain_ticket"
        assert steps[1].payload_path == "/tmp/research-ticket.md"

    def test_unknown_package_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown chain_ref"):
            resolve_chain_ref("nonexistent_package", {}, [])


# ---------------------------------------------------------------------------
# ID uniqueness
# ---------------------------------------------------------------------------

class TestIdUniqueness:
    """Verify that generated IDs are globally unique."""

    def test_multiple_chains_unique_ids(self, db_path: Path) -> None:
        """Two separate chains get distinct flow/chain IDs."""
        ids1 = submit_chain(
            FlowEnvelope(db_path=db_path, submitted_by="agent-1"),
            [TaskSpec(task_type="staleness.alignment_check")],
        )
        ids2 = submit_chain(
            FlowEnvelope(db_path=db_path, submitted_by="agent-2"),
            [TaskSpec(task_type="signals.impact_analysis")],
        )

        t1 = _query_task(db_path, ids1[0])
        t2 = _query_task(db_path, ids2[0])

        assert t1["flow_id"] != t2["flow_id"]
        assert t1["chain_id"] != t2["chain_id"]
        assert t1["instance_id"] != t2["instance_id"]
