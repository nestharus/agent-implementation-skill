"""Integration test: dispatcher thread drains pending tasks on shutdown.

Verifies that tasks submitted just before the pipeline signals stop are
not stuck in 'pending' — the dispatcher processes them during the drain
phase before the thread exits.

This was the root cause for research gate members (sections 08, 09, 12)
remaining in 'pending' after the F4 budget-exit bug.
"""

from __future__ import annotations

import sqlite3
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from _paths import DB_SH
from conftest import override_dispatcher_and_guard
from src.orchestrator.path_registry import PathRegistry

from flow.service.task_db_client import init_db
from flow.types.routing import Task, request_task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_planspace(tmp_path: Path) -> Path:
    ps = tmp_path / "planspace"
    ps.mkdir()
    PathRegistry(ps).ensure_artifacts_tree()
    init_db(ps / "run.db")
    return ps


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


def _submit_payload_task(db_path, planspace, task_type="staleness.alignment_check",
                         scope=None):
    """Submit a task with a real payload file and return (task_id, payload_path)."""
    task = Task(
        task_type=task_type,
        submitted_by="test-submitter",
        concern_scope=scope,
    )
    tid = request_task(db_path, task)
    payload = planspace / "artifacts" / f"task-{tid}-payload.md"
    payload.write_text(f"# Payload for task {tid}\n", encoding="utf-8")
    # Update the payload_path in the DB row.
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET payload_path=? WHERE id=?",
        (str(payload), tid),
    )
    conn.commit()
    conn.close()
    return tid, payload


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDispatcherDrainOnShutdown:
    """Verify that the dispatcher drains pending tasks after stop_event is set."""

    def test_pending_tasks_dispatched_after_stop_signal(
        self, tmp_path: Path,
    ) -> None:
        """Submit tasks, set stop_event immediately, then run the dispatcher.

        The dispatcher loop exits on the first check of stop_event.is_set(),
        but the drain phase should still process all pending tasks.
        """
        ps = _setup_planspace(tmp_path)
        db_path = ps / "run.db"

        # Submit three tasks (simulating research gate members for sections 08, 09, 12).
        tid1, _ = _submit_payload_task(db_path, ps, scope="section-8")
        tid2, _ = _submit_payload_task(db_path, ps, scope="section-9")
        tid3, _ = _submit_payload_task(db_path, ps, scope="section-12")

        # Confirm all three are pending.
        for tid in (tid1, tid2, tid3):
            row = _query_task(str(db_path), tid)
            assert row["status"] == "pending"

        from flow.engine import task_dispatcher as td_mod

        dispatched_ids: list[str] = []

        def fake_dispatch(*args, **kwargs):
            return "test output"

        # Set stop_event BEFORE starting the dispatcher — simulates the
        # orchestrator finishing just as tasks are submitted.
        stop_event = threading.Event()
        stop_event.set()

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(
                td_mod._task_registry, "resolve",
                return_value=("test-agent.md", "test-model"),
            ),
        ):
            from pipeline.runner import _run_task_dispatcher
            _run_task_dispatcher(stop_event, ps, codespace=None, poll_interval=0.1)

        # All three tasks should have been dispatched (not stuck in 'pending').
        tasks = _query_all_tasks(str(db_path))
        for t in tasks:
            assert t["status"] in ("complete", "failed"), (
                f"Task {t['id']} (scope={t['concern_scope']}) stuck in "
                f"'{t['status']}' — drain did not process it"
            )

    def test_drain_handles_empty_queue_gracefully(
        self, tmp_path: Path,
    ) -> None:
        """When no pending tasks exist, the drain exits immediately."""
        ps = _setup_planspace(tmp_path)

        stop_event = threading.Event()
        stop_event.set()

        from pipeline.runner import _run_task_dispatcher
        # Should not raise or hang.
        _run_task_dispatcher(stop_event, ps, codespace=None, poll_interval=0.1)

    def test_dispatcher_thread_processes_tasks_before_join(
        self, tmp_path: Path,
    ) -> None:
        """End-to-end: start dispatcher thread, submit tasks, stop, verify drained.

        This mirrors the real pipeline lifecycle in runner.py._handoff().
        """
        ps = _setup_planspace(tmp_path)
        db_path = ps / "run.db"

        from flow.engine import task_dispatcher as td_mod

        def fake_dispatch(*args, **kwargs):
            return "dispatched"

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(
                td_mod._task_registry, "resolve",
                return_value=("test-agent.md", "test-model"),
            ),
        ):
            from pipeline.runner import _run_task_dispatcher

            stop_event = threading.Event()
            dispatcher_thread = threading.Thread(
                target=_run_task_dispatcher,
                args=(stop_event, ps, None),
                kwargs={"poll_interval": 0.1},
                name="test-dispatcher",
                daemon=True,
            )
            dispatcher_thread.start()

            # Submit tasks while the dispatcher is running.
            tid1, _ = _submit_payload_task(db_path, ps, scope="section-8")
            tid2, _ = _submit_payload_task(db_path, ps, scope="section-9")

            # Signal stop and wait for the thread to finish.
            stop_event.set()
            dispatcher_thread.join(timeout=10)

        # Both tasks should have been processed (either by the main loop
        # or by the drain phase).
        tasks = _query_all_tasks(str(db_path))
        for t in tasks:
            assert t["status"] in ("complete", "failed"), (
                f"Task {t['id']} stuck in '{t['status']}' after dispatcher shutdown"
            )
