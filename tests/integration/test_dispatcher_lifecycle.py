"""Integration tests for task_dispatcher lifecycle.

Tests the full claim->dispatch->reconcile->continue cycle with real SQLite,
real filesystem artifacts, and mocked dispatch_agent only.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from dependency_injector import providers

from _paths import DB_SH
from conftest import override_dispatcher_and_guard
from containers import FreshnessService, Services

from flow.types.schema import TaskSpec
from flow.service.flow_facade import (
    reconcile_task_completion,
    submit_chain,
)
from staleness.service.freshness_calculator import compute_section_freshness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_db(db_path: Path) -> None:
    subprocess.run(
        ["bash", str(DB_SH), "init", str(db_path)],
        check=True, capture_output=True, text=True,
    )


def _query_task(db_path: str, task_id: int) -> dict | None:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _query_all_tasks(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _setup_planspace(tmp_path: Path) -> Path:
    ps = tmp_path / "planspace"
    ps.mkdir()
    artifacts = ps / "artifacts"
    for subdir in ("sections", "proposals", "signals", "flows"):
        (artifacts / subdir).mkdir(parents=True)
    _init_db(ps / "run.db")
    return ps


def _submit_simple_task(db_path: str, planspace: Path) -> tuple[str, Path]:
    """Submit a simple task via db.sh and create its payload file.

    Returns (task_id, payload_path).
    """
    result = subprocess.run(
        ["bash", str(DB_SH), "submit-task", db_path,
         "test-submitter", "staleness.alignment_check"],
        check=True, capture_output=True, text=True,
    )
    task_id = result.stdout.strip().split(":")[1]
    payload = planspace / "artifacts" / f"task-{task_id}-payload.md"
    payload.write_text("# Test payload\n", encoding="utf-8")
    return task_id, payload


def _build_task_dict_from_db(db_path: str, task_id: int) -> dict[str, str]:
    """Build a task dict matching the format produced by parse_next_task.

    The dispatcher's dispatch_task expects string-valued dicts with keys
    matching the next-task output format.
    """
    row = _query_task(db_path, task_id)
    if row is None:
        raise ValueError(f"Task {task_id} not found")

    d: dict[str, str] = {
        "id": str(row["id"]),
        "type": row["task_type"],
        "by": row["submitted_by"],
        "prio": row["priority"] or "normal",
    }
    if row.get("problem_id"):
        d["problem"] = row["problem_id"]
    if row.get("concern_scope"):
        d["scope"] = row["concern_scope"]
    if row.get("payload_path"):
        d["payload"] = row["payload_path"]
    if row.get("depends_on"):
        d["depends_on"] = str(row["depends_on"])
    if row.get("instance_id"):
        d["instance"] = row["instance_id"]
    if row.get("flow_id"):
        d["flow"] = row["flow_id"]
    if row.get("chain_id"):
        d["chain"] = row["chain_id"]
    if row.get("declared_by_task_id"):
        d["declared_by_task"] = str(row["declared_by_task_id"])
    if row.get("trigger_gate_id"):
        d["trigger_gate"] = row["trigger_gate_id"]
    if row.get("flow_context_path"):
        d["flow_context"] = row["flow_context_path"]
    if row.get("continuation_path"):
        d["continuation"] = row["continuation_path"]
    if row.get("freshness_token"):
        d["freshness"] = row["freshness_token"]
    return d


def _mark_task_running(db_path: str, task_id: int) -> None:
    """Mark a task as running (simulate the claim step)."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='running', claimed_by='test-dispatcher' WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _mark_task_complete(db_path: str, task_id: int) -> None:
    """Mark a task as complete via DB (simulates db.sh complete-task)."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='complete', completed_at=datetime('now') WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDispatchTaskClaimsDispatchesCompletes:
    """Test 1: happy path claim -> dispatch -> complete lifecycle."""

    def test_dispatch_task_claims_dispatches_completes(
        self, tmp_path: Path,
    ) -> None:
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")

        # Submit a task via db.sh to get a real DB row.
        task_id, payload = _submit_simple_task(db_path, ps)

        from flow.engine import dispatcher as task_dispatcher

        def fake_dispatch(*args, **kwargs):
            # args: model, prompt_path, output_path, planspace, parent
            out = args[2] if len(args) > 2 else None
            if out is not None:
                Path(str(out)).write_text(
                    "agent produced valid output", encoding="utf-8",
                )
            return "agent produced valid output"

        task = {
            "id": task_id,
            "type": "staleness.alignment_check",
            "by": "test-submitter",
            "payload": str(payload),
        }

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(
                task_dispatcher._task_registry, "resolve",
                return_value=("alignment-judge.md", "test-model"),
            ),
        ):
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify: task status is "complete" in DB.
        row = _query_task(db_path, int(task_id))
        assert row is not None
        assert row["status"] == "complete", f"Expected 'complete', got '{row['status']}'"

        # Verify: output file exists at artifacts/task-{id}-output.md.
        output_path = ps / "artifacts" / f"task-{task_id}-output.md"
        assert output_path.exists(), f"Output file not found: {output_path}"

        # Verify: reconcile_task_completion was called for real (not mocked).
        # The result manifest should have been written if the task had flow metadata.
        # For a simple db.sh-submitted task without flow columns, the reconciler
        # still runs safely (no crash, no manifest since no result_manifest_path).


class TestDispatchTaskAgentTimeoutFails:
    """Test 2: agent timeout -> fail + reconcile."""

    def test_dispatch_task_agent_timeout_fails_and_reconciles(
        self, tmp_path: Path,
    ) -> None:
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id, payload = _submit_simple_task(db_path, ps)

        from flow.engine import dispatcher as task_dispatcher

        artifacts = ps / "artifacts"
        output_path = artifacts / f"task-{task_id}-output.md"
        meta_path = output_path.with_suffix(".meta.json")

        def fake_dispatch(*args, **kwargs):
            # Write the output file and the meta sidecar before returning.
            output_path.write_text("TIMEOUT: agent exceeded 600s", encoding="utf-8")
            meta_path.write_text(
                json.dumps({"timed_out": True, "returncode": 1}),
                encoding="utf-8",
            )
            return "TIMEOUT: agent exceeded 600s"

        task = {
            "id": task_id,
            "type": "staleness.alignment_check",
            "by": "test-submitter",
            "payload": str(payload),
        }

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(
                task_dispatcher._task_registry, "resolve",
                return_value=("alignment-judge.md", "test-model"),
            ),
        ):
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify: task status is "failed" in DB.
        row = _query_task(db_path, int(task_id))
        assert row is not None
        assert row["status"] == "failed", f"Expected 'failed', got '{row['status']}'"
        assert "timeout" in (row["error"] or "").lower()


class TestDispatchTaskAgentNonzeroExitFails:
    """Test 3: nonzero return code via meta sidecar -> fail."""

    def test_dispatch_task_agent_nonzero_exit_fails(
        self, tmp_path: Path,
    ) -> None:
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id, payload = _submit_simple_task(db_path, ps)

        from flow.engine import dispatcher as task_dispatcher

        artifacts = ps / "artifacts"
        output_path = artifacts / f"task-{task_id}-output.md"
        meta_path = output_path.with_suffix(".meta.json")

        def fake_dispatch(*args, **kwargs):
            # Write meta sidecar with nonzero returncode before returning.
            meta_path.write_text(
                json.dumps({"returncode": 1, "timed_out": False}),
                encoding="utf-8",
            )
            return "agent produced some output"

        task = {
            "id": task_id,
            "type": "staleness.alignment_check",
            "by": "test-submitter",
            "payload": str(payload),
        }

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(
                task_dispatcher._task_registry, "resolve",
                return_value=("alignment-judge.md", "test-model"),
            ),
        ):
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify: task status is "failed" in DB.
        row = _query_task(db_path, int(task_id))
        assert row is not None
        assert row["status"] == "failed", f"Expected 'failed', got '{row['status']}'"
        assert "return code" in (row["error"] or "").lower()


class TestDispatchChainContinuation:
    """Test 4: 2-step chain -> dispatch step 1 -> step 2 becomes runnable."""

    def test_dispatch_chain_continuation_after_completion(
        self, tmp_path: Path,
    ) -> None:
        ps = _setup_planspace(tmp_path)
        db_path_obj = ps / "run.db"
        db_path = str(db_path_obj)

        # Create payload files for both steps.
        artifacts = ps / "artifacts"
        payload1 = artifacts / "step1-payload.md"
        payload1.write_text("# Step 1 payload\n", encoding="utf-8")
        payload2 = artifacts / "step2-payload.md"
        payload2.write_text("# Step 2 payload\n", encoding="utf-8")

        # Submit a 2-step chain via submit_chain.
        task_ids = submit_chain(
            db_path_obj,
            "test-submitter",
            [
                TaskSpec(
                    task_type="staleness.alignment_check",
                    payload_path=str(payload1),
                ),
                TaskSpec(
                    task_type="signals.impact_analysis",
                    payload_path=str(payload2),
                ),
            ],
            planspace=ps,
        )
        assert len(task_ids) == 2
        tid1, tid2 = task_ids

        # Verify: both tasks are pending, second depends on first.
        t1 = _query_task(db_path, tid1)
        t2 = _query_task(db_path, tid2)
        assert t1["status"] == "pending"
        assert t2["status"] == "pending"
        assert t2["depends_on"] == str(tid1)

        # Dispatch the first task (let reconcile run for real).
        from flow.engine import dispatcher as task_dispatcher

        def fake_dispatch_1(*args, **kwargs):
            return "step 1 output"

        task1_dict = _build_task_dict_from_db(db_path, tid1)
        with (
            override_dispatcher_and_guard(fake_dispatch_1),
            patch.object(
                task_dispatcher._task_registry, "resolve",
                return_value=("alignment-judge.md", "test-model"),
            ),
        ):
            task_dispatcher.dispatch_task(db_path, ps, task1_dict)

        # Verify: first task is complete.
        t1_after = _query_task(db_path, tid1)
        assert t1_after["status"] == "complete"

        # Verify: second task is still pending (waiting to be dispatched).
        t2_after = _query_task(db_path, tid2)
        assert t2_after["status"] == "pending"

        # Mark first task as complete in DB so dependency check passes for step 2.
        # (dispatch_task already called complete-task via _db_cmd, so this is a no-op
        # safety net for test clarity.)

        # Now dispatch the second task.
        def fake_dispatch_2(*args, **kwargs):
            return "step 2 output"

        task2_dict = _build_task_dict_from_db(db_path, tid2)
        with (
            override_dispatcher_and_guard(fake_dispatch_2),
            patch.object(
                task_dispatcher._task_registry, "resolve",
                return_value=("impact-analyzer.md", "test-model"),
            ),
        ):
            task_dispatcher.dispatch_task(db_path, ps, task2_dict)

        # Verify: second task is complete.
        t2_final = _query_task(db_path, tid2)
        assert t2_final["status"] == "complete"


class TestDispatchChainFailureCancelsDescendants:
    """Test 5: 3-step chain, first fails -> remaining cancelled."""

    def test_dispatch_chain_failure_cancels_descendants(
        self, tmp_path: Path,
    ) -> None:
        ps = _setup_planspace(tmp_path)
        db_path_obj = ps / "run.db"
        db_path = str(db_path_obj)

        artifacts = ps / "artifacts"
        payloads = []
        for i in range(1, 4):
            p = artifacts / f"step{i}-payload.md"
            p.write_text(f"# Step {i}\n", encoding="utf-8")
            payloads.append(p)

        # Submit a 3-step chain.
        task_ids = submit_chain(
            db_path_obj,
            "test-submitter",
            [
                TaskSpec(task_type="staleness.alignment_check", payload_path=str(payloads[0])),
                TaskSpec(task_type="signals.impact_analysis", payload_path=str(payloads[1])),
                TaskSpec(task_type="coordination.fix", payload_path=str(payloads[2])),
            ],
            planspace=ps,
        )
        assert len(task_ids) == 3
        tid1, tid2, tid3 = task_ids

        # Dispatch the first task with a nonzero exit code (failure).
        from flow.engine import dispatcher as task_dispatcher

        output_path = artifacts / f"task-{tid1}-output.md"
        meta_path = output_path.with_suffix(".meta.json")

        def fake_dispatch_fail(*args, **kwargs):
            meta_path.write_text(
                json.dumps({"returncode": 1, "timed_out": False}),
                encoding="utf-8",
            )
            return "agent failed"

        task1_dict = _build_task_dict_from_db(db_path, tid1)
        with (
            override_dispatcher_and_guard(fake_dispatch_fail),
            patch.object(
                task_dispatcher._task_registry, "resolve",
                return_value=("alignment-judge.md", "test-model"),
            ),
        ):
            task_dispatcher.dispatch_task(db_path, ps, task1_dict)

        # Verify: first task is failed.
        t1 = _query_task(db_path, tid1)
        assert t1["status"] == "failed"

        # Verify: remaining tasks in chain are cancelled.
        t2 = _query_task(db_path, tid2)
        t3 = _query_task(db_path, tid3)
        assert t2["status"] == "cancelled", f"Expected 'cancelled', got '{t2['status']}'"
        assert t3["status"] == "cancelled", f"Expected 'cancelled', got '{t3['status']}'"
        assert t2["error"] == "chain ancestor failed"
        assert t3["error"] == "chain ancestor failed"


class TestDispatchMissingPayload:
    """Test 6: missing payload path -> task fails closed."""

    def test_dispatch_missing_payload_fails_closed(
        self, tmp_path: Path,
    ) -> None:
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id, _ = _submit_simple_task(db_path, ps)

        from flow.engine import dispatcher as task_dispatcher

        # Point to a payload that doesn't exist.
        nonexistent_payload = ps / "artifacts" / "does-not-exist.md"

        task = {
            "id": task_id,
            "type": "staleness.alignment_check",
            "by": "test-submitter",
            "payload": str(nonexistent_payload),
        }

        with (
            override_dispatcher_and_guard(lambda *a, **kw: ""),
            patch.object(
                task_dispatcher._task_registry, "resolve",
                return_value=("alignment-judge.md", "test-model"),
            ),
        ):
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify: task is failed with payload-related error.
        row = _query_task(db_path, int(task_id))
        assert row is not None
        assert row["status"] == "failed", f"Expected 'failed', got '{row['status']}'"
        assert "payload" in (row["error"] or "").lower()


class TestDispatchUnresolvableTaskType:
    """Test 7: unknown task type -> ValueError from _task_registry.resolve -> fail."""

    def test_dispatch_unresolvable_task_type_fails(
        self, tmp_path: Path,
    ) -> None:
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")

        # Submit via db.sh with an unknown type (db.sh doesn't validate types).
        result = subprocess.run(
            ["bash", str(DB_SH), "submit-task", db_path,
             "test-submitter", "nonexistent_task_type_xyz"],
            check=True, capture_output=True, text=True,
        )
        task_id = result.stdout.strip().split(":")[1]

        from flow.engine import dispatcher as task_dispatcher

        task = {
            "id": task_id,
            "type": "nonexistent_task_type_xyz",
            "by": "test-submitter",
            "payload": str(ps / "artifacts" / "some-payload.md"),
        }

        with patch.object(
            task_dispatcher._task_registry, "resolve",
            side_effect=ValueError("Unknown task type: 'nonexistent_task_type_xyz'"),
        ):
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify: task is failed in DB.
        row = _query_task(db_path, int(task_id))
        assert row is not None
        assert row["status"] == "failed", f"Expected 'failed', got '{row['status']}'"
        assert "unknown task type" in (row["error"] or "").lower()


class TestDispatchStaleFreshnessToken:
    """Test 8: stale freshness token -> task fails before dispatch."""

    def test_dispatch_stale_freshness_token_fails(
        self, tmp_path: Path,
    ) -> None:
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")

        # Create section artifacts and compute the real freshness token.
        sections = ps / "artifacts" / "sections"
        (sections / "section-01.md").write_text("# Section 01\n", encoding="utf-8")
        (sections / "section-01-alignment-excerpt.md").write_text(
            "Original alignment\n", encoding="utf-8",
        )

        # Submit a task with a stale (fabricated) freshness token.
        task_id, payload = _submit_simple_task(db_path, ps)

        from flow.engine import dispatcher as task_dispatcher

        # Patch compute_section_freshness to return a DIFFERENT token,
        # simulating that section inputs have changed since submission.
        stale_token = "aaaa111122223333"
        current_token = "bbbb444455556666"

        task = {
            "id": task_id,
            "type": "staleness.alignment_check",
            "by": "test-submitter",
            "payload": str(payload),
            "scope": "section-01",
            "freshness": stale_token,
        }

        class _StubFreshness(FreshnessService):
            def compute(self, planspace, section_number):
                return current_token

        Services.freshness.override(providers.Object(_StubFreshness()))
        try:
            with (
                override_dispatcher_and_guard(lambda *a, **kw: ""),
                patch.object(
                    task_dispatcher._task_registry, "resolve",
                    return_value=("alignment-judge.md", "test-model"),
                ),
            ):
                task_dispatcher.dispatch_task(db_path, ps, task)
        finally:
            Services.freshness.reset_override()

        # Verify: task is failed with stale alignment error.
        row = _query_task(db_path, int(task_id))
        assert row is not None
        assert row["status"] == "failed", f"Expected 'failed', got '{row['status']}'"
        assert "stale alignment" in (row["error"] or "").lower()
