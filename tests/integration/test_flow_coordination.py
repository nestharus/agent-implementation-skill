"""Tests for flow-based coordination fix migration (Task 7).

Covers:
- coordination_fix_package resolution via flow_catalog
- build_coordination_branches() helper
- Concurrent instance isolation: same package, two concurrent fanouts,
  distinct chain_ids and flow_ids
- Gate firing when all coordination fixes complete
- Gate accumulation: result manifests separated by chain_id
- Edge cases: empty groups, single-item groups
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from _paths import DB_SH

from flow_catalog import (
    KNOWN_PACKAGES,
    build_coordination_branches,
    resolve_chain_ref,
)
from flow_schema import BranchSpec, GateSpec, TaskSpec
from task_flow import (
    reconcile_task_completion,
    submit_chain,
    submit_fanout,
)


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


def _query_all_tasks(db_path: Path) -> list[dict]:
    """Read all task rows as list of dicts."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


def _mark_task_running(db_path: Path, task_id: int) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='running', claimed_by='test' WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _mark_task_complete(db_path: Path, task_id: int) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='complete', completed_at=datetime('now') WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    _init_db(p)
    return p


@pytest.fixture()
def planspace(tmp_path: Path) -> Path:
    ps = tmp_path / "planspace"
    ps.mkdir()
    (ps / "artifacts" / "flows").mkdir(parents=True)
    (ps / "artifacts" / "coordination").mkdir(parents=True)
    return ps


# ---------------------------------------------------------------------------
# coordination_fix_package resolution
# ---------------------------------------------------------------------------

class TestCoordinationFixPackage:
    """Tests for the coordination_fix_package in flow_catalog."""

    def test_package_registered(self) -> None:
        assert "coordination_fix_package" in KNOWN_PACKAGES

    def test_resolves_to_single_step(self) -> None:
        steps = resolve_chain_ref(
            "coordination_fix_package",
            {"concern_scope": "coord-group-1", "payload_path": "/tmp/p.md"},
            [],
        )
        assert len(steps) == 1
        assert steps[0].task_type == "coordination_fix"
        assert steps[0].concern_scope == "coord-group-1"
        assert steps[0].payload_path == "/tmp/p.md"

    def test_args_propagated(self) -> None:
        steps = resolve_chain_ref(
            "coordination_fix_package",
            {
                "concern_scope": "section-3",
                "payload_path": "/plan/fix.md",
                "priority": "high",
                "problem_id": "P-42",
            },
            ["ref-a"],
        )
        assert steps[0].priority == "high"
        assert steps[0].problem_id == "P-42"

    def test_defaults_when_no_args(self) -> None:
        steps = resolve_chain_ref("coordination_fix_package", {}, [])
        assert steps[0].concern_scope == ""
        assert steps[0].payload_path == ""
        assert steps[0].priority == "normal"


# ---------------------------------------------------------------------------
# build_coordination_branches
# ---------------------------------------------------------------------------

class TestBuildCoordinationBranches:
    """Tests for build_coordination_branches()."""

    def test_empty_groups_returns_empty(self, planspace: Path) -> None:
        branches = build_coordination_branches({}, planspace)
        assert branches == []

    def test_single_group(self, planspace: Path) -> None:
        groups = {
            0: [{"section": "01", "type": "misaligned",
                 "description": "drift", "files": ["a.py"]}],
        }
        branches = build_coordination_branches(groups, planspace)
        assert len(branches) == 1
        assert branches[0].label == "coord-fix-0"
        assert branches[0].chain_ref == "coordination_fix_package"
        assert branches[0].args["concern_scope"] == "coord-group-0"
        assert "fix-0-prompt.md" in branches[0].args["payload_path"]

    def test_multiple_groups_sorted(self, planspace: Path) -> None:
        groups = {
            2: [{"section": "02"}],
            0: [{"section": "01"}],
            1: [{"section": "03"}],
        }
        branches = build_coordination_branches(groups, planspace)
        assert len(branches) == 3
        # Should be sorted by group_id
        assert branches[0].label == "coord-fix-0"
        assert branches[1].label == "coord-fix-1"
        assert branches[2].label == "coord-fix-2"

    def test_payload_path_uses_planspace(self, planspace: Path) -> None:
        groups = {5: [{"section": "01"}]}
        branches = build_coordination_branches(groups, planspace)
        expected = str(
            planspace / "artifacts" / "coordination" / "fix-5-prompt.md"
        )
        assert branches[0].args["payload_path"] == expected


# ---------------------------------------------------------------------------
# Concurrent instance isolation
# ---------------------------------------------------------------------------

class TestConcurrentInstanceIsolation:
    """Two concurrent fanouts of the same package get separate identifiers.

    This is the core instance isolation guarantee: if two sections both
    submit coordination_fix_package fanouts at the same time, their
    tasks must be in separate chain_ids with separate flow_ids.
    """

    def test_two_concurrent_fanouts_distinct_flow_ids(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Two fanouts with different flow_ids are fully isolated."""
        branches_a = [
            BranchSpec(
                label="a-fix-0",
                chain_ref="coordination_fix_package",
                args={"concern_scope": "group-0", "payload_path": "/a/0.md"},
            ),
            BranchSpec(
                label="a-fix-1",
                chain_ref="coordination_fix_package",
                args={"concern_scope": "group-1", "payload_path": "/a/1.md"},
            ),
        ]
        branches_b = [
            BranchSpec(
                label="b-fix-0",
                chain_ref="coordination_fix_package",
                args={"concern_scope": "group-0", "payload_path": "/b/0.md"},
            ),
        ]

        gate_a = submit_fanout(
            db_path, "section-01", branches_a,
            flow_id="flow_section_01",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )
        gate_b = submit_fanout(
            db_path, "section-02", branches_b,
            flow_id="flow_section_02",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        # Gates are distinct
        assert gate_a != gate_b

        # Query all tasks
        all_tasks = _query_all_tasks(db_path)
        # 2 branches * 1 step + 1 branch * 1 step = 3 tasks
        assert len(all_tasks) == 3

        # Tasks are partitioned by flow_id
        flow_a_tasks = [t for t in all_tasks if t["flow_id"] == "flow_section_01"]
        flow_b_tasks = [t for t in all_tasks if t["flow_id"] == "flow_section_02"]
        assert len(flow_a_tasks) == 2
        assert len(flow_b_tasks) == 1

        # Each task in flow A has a distinct chain_id
        chain_ids_a = {t["chain_id"] for t in flow_a_tasks}
        assert len(chain_ids_a) == 2

        # Flow B chain_id is different from all flow A chain_ids
        chain_b = flow_b_tasks[0]["chain_id"]
        assert chain_b not in chain_ids_a

    def test_same_package_concurrent_no_cross_talk(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Same chain_ref used in two fanouts — gate members are separate."""
        branches = [
            BranchSpec(
                label="fix-0",
                chain_ref="coordination_fix_package",
                args={"concern_scope": "group-0"},
            ),
        ]

        gate_1 = submit_fanout(
            db_path, "caller-1", branches,
            flow_id="flow_1",
            gate=GateSpec(),
            planspace=planspace,
        )
        gate_2 = submit_fanout(
            db_path, "caller-2", branches,
            flow_id="flow_2",
            gate=GateSpec(),
            planspace=planspace,
        )

        members_1 = _query_gate_members(db_path, gate_1)
        members_2 = _query_gate_members(db_path, gate_2)

        # Each gate has exactly 1 member
        assert len(members_1) == 1
        assert len(members_2) == 1

        # Members point to different chain_ids
        assert members_1[0]["chain_id"] != members_2[0]["chain_id"]

        # Members point to different leaf task IDs
        assert members_1[0]["leaf_task_id"] != members_2[0]["leaf_task_id"]


# ---------------------------------------------------------------------------
# Gate accumulation and firing
# ---------------------------------------------------------------------------

class TestCoordinationGateFiring:
    """Gate fires correctly when all coordination fix branches complete."""

    def test_gate_fires_when_all_coord_fixes_complete(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Three coordination fix branches — gate fires after all three complete."""
        branches = [
            BranchSpec(
                label=f"fix-{i}",
                chain_ref="coordination_fix_package",
                args={"concern_scope": f"group-{i}"},
            )
            for i in range(3)
        ]
        gate_id = submit_fanout(
            db_path, "coordinator", branches,
            flow_id="flow_coord",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 3

        # Complete all tasks in sequence
        for t in all_tasks:
            _mark_task_running(db_path, t["id"])
            _mark_task_complete(db_path, t["id"])
            reconcile_task_completion(
                db_path, planspace, t["id"],
                "complete", f"artifacts/fix-{t['id']}-output.md",
            )

        # Gate should be ready
        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "ready"
        assert gate["aggregate_manifest_path"] is not None

        # Aggregate manifest should have all 3 members
        agg = json.loads(
            (planspace / gate["aggregate_manifest_path"]).read_text()
        )
        assert len(agg["members"]) == 3
        assert all(m["status"] == "complete" for m in agg["members"])

        # Each member should have a distinct chain_id
        chain_ids = {m["chain_id"] for m in agg["members"]}
        assert len(chain_ids) == 3

    def test_gate_with_synthesis_fires_synthesis_task(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Coordination gate with synthesis task submits it on completion."""
        branches = [
            BranchSpec(
                label="fix-0",
                chain_ref="coordination_fix_package",
                args={"concern_scope": "group-0"},
            ),
        ]
        gate_id = submit_fanout(
            db_path, "coordinator", branches,
            flow_id="flow_syn",
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(
                    task_type="alignment_check",
                    concern_scope="post-coordination",
                ),
            ),
            planspace=planspace,
        )

        tasks = _query_all_tasks(db_path)
        assert len(tasks) == 1
        tid = tasks[0]["id"]

        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)
        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/output.md",
        )

        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "fired"
        assert gate["fired_task_id"] is not None

        syn_task = _query_task(db_path, gate["fired_task_id"])
        assert syn_task["task_type"] == "alignment_check"
        assert syn_task["concern_scope"] == "post-coordination"
        assert syn_task["trigger_gate_id"] == gate_id

    def test_gate_does_not_fire_until_all_branches_done(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Gate stays open until the last branch completes."""
        branches = [
            BranchSpec(
                label=f"fix-{i}",
                chain_ref="coordination_fix_package",
                args={"concern_scope": f"group-{i}"},
            )
            for i in range(2)
        ]
        gate_id = submit_fanout(
            db_path, "coordinator", branches,
            flow_id="flow_partial",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        tasks = _query_all_tasks(db_path)
        assert len(tasks) == 2

        # Complete only the first task
        _mark_task_running(db_path, tasks[0]["id"])
        _mark_task_complete(db_path, tasks[0]["id"])
        reconcile_task_completion(
            db_path, planspace, tasks[0]["id"],
            "complete", "artifacts/output.md",
        )

        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "open"

        # Now complete the second
        _mark_task_running(db_path, tasks[1]["id"])
        _mark_task_complete(db_path, tasks[1]["id"])
        reconcile_task_completion(
            db_path, planspace, tasks[1]["id"],
            "complete", "artifacts/output2.md",
        )

        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "ready"


# ---------------------------------------------------------------------------
# Result separation by chain_id
# ---------------------------------------------------------------------------

class TestResultSeparation:
    """Result manifests are separated by chain_id within a fanout."""

    def test_result_manifests_per_branch(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Each branch's result manifest is stored at a distinct path."""
        branches = [
            BranchSpec(
                label=f"fix-{i}",
                chain_ref="coordination_fix_package",
                args={"concern_scope": f"group-{i}"},
            )
            for i in range(2)
        ]
        gate_id = submit_fanout(
            db_path, "coordinator", branches,
            flow_id="flow_results",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        tasks = _query_all_tasks(db_path)
        output_paths = {}

        for t in tasks:
            _mark_task_running(db_path, t["id"])
            _mark_task_complete(db_path, t["id"])
            out = f"artifacts/fix-{t['id']}-output.md"
            reconcile_task_completion(
                db_path, planspace, t["id"],
                "complete", out,
            )
            output_paths[t["chain_id"]] = out

        # Each chain_id produced a distinct result manifest
        gate = _query_gate(db_path, gate_id)
        agg = json.loads(
            (planspace / gate["aggregate_manifest_path"]).read_text()
        )
        result_paths = {
            m["chain_id"]: m["result_manifest_path"]
            for m in agg["members"]
        }

        # Paths are distinct
        assert len(set(result_paths.values())) == 2

        # Each result manifest contains the correct task details
        for chain_id, rpath in result_paths.items():
            manifest = json.loads(
                (planspace / rpath).read_text()
            )
            assert manifest["chain_id"] == chain_id
            assert manifest["status"] == "complete"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases for coordination flow submission."""

    def test_empty_branches_returns_no_gate(
        self, db_path: Path,
    ) -> None:
        """Empty branch list returns None (no gate created)."""
        result = submit_fanout(
            db_path, "coordinator", [],
            flow_id="flow_empty",
            gate=GateSpec(),
        )
        assert result is None

    def test_single_branch_fanout(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Single-branch fanout still creates a proper gate."""
        branches = [
            BranchSpec(
                label="only-fix",
                chain_ref="coordination_fix_package",
                args={"concern_scope": "solo"},
            ),
        ]
        gate_id = submit_fanout(
            db_path, "coordinator", branches,
            flow_id="flow_single",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )
        assert gate_id is not None

        gate = _query_gate(db_path, gate_id)
        assert gate["expected_count"] == 1

        members = _query_gate_members(db_path, gate_id)
        assert len(members) == 1
        assert members[0]["slot_label"] == "only-fix"

    def test_build_branches_then_submit(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """End-to-end: build_coordination_branches -> submit_fanout."""
        groups = {
            0: [{"section": "01", "type": "misaligned",
                 "description": "drift", "files": ["a.py"]}],
            1: [{"section": "02", "type": "misaligned",
                 "description": "stale", "files": ["b.py"]}],
        }
        branches = build_coordination_branches(groups, planspace)
        assert len(branches) == 2

        gate_id = submit_fanout(
            db_path, "coordinator", branches,
            flow_id="flow_e2e",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )
        assert gate_id is not None

        tasks = _query_all_tasks(db_path)
        assert len(tasks) == 2
        assert all(t["task_type"] == "coordination_fix" for t in tasks)

        # Concern scopes match
        scopes = {t["concern_scope"] for t in tasks}
        assert scopes == {"coord-group-0", "coord-group-1"}

        # Gate members match
        members = _query_gate_members(db_path, gate_id)
        assert len(members) == 2
        labels = {m["slot_label"] for m in members}
        assert labels == {"coord-fix-0", "coord-fix-1"}
