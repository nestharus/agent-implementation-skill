"""Tests for R80/P1: payload-backed runtime context is mandatory.

Covers:
- task_dispatcher.dispatch_task rejects tasks with no payload_path
- task_dispatcher.dispatch_task accepts tasks with valid payload_path
- flow_schema.validate_flow_declaration rejects TaskSpec without payload_path
- flow_schema.validate_flow_declaration accepts TaskSpec with payload_path
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from _paths import DB_SH
from conftest import override_dispatcher_and_guard
from flow.engine.reconciler import Reconciler
from src.orchestrator.path_registry import PathRegistry

from flow.types.schema import (
    BranchSpec,
    ChainAction,
    FanoutAction,
    FlowDeclaration,
    TaskSpec,
    validate_flow_declaration,
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


def _setup_planspace(tmp_path: Path) -> Path:
    """Create a planspace with initialized DB."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    PathRegistry(ps).ensure_artifacts_tree()
    _init_db(ps / "run.db")
    return ps


def _submit_task(db_path: str, task_type: str = "test-task") -> str:
    """Submit a task and return its ID."""
    result = subprocess.run(
        ["bash", str(DB_SH), "submit-task", db_path,
         task_type, "--by", "test-submitter"],
        check=True, capture_output=True, text=True,
    )
    # Output format: "submitted:<id>"
    return result.stdout.strip().split(":")[1]


# ---------------------------------------------------------------------------
# Test: dispatcher rejects no payload
# ---------------------------------------------------------------------------

class TestDispatcherPayloadRequired:
    """R80/P1: dispatch_task fails tasks that have no payload_path."""

    def test_dispatcher_rejects_no_payload(self, tmp_path: Path) -> None:
        """dispatch_task with no payload_path fails the task."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        from flow.engine import task_dispatcher as task_dispatcher

        dispatch_called = False

        def fake_dispatch(*args, **kwargs):
            nonlocal dispatch_called
            dispatch_called = True
            return "should not be called"

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(task_dispatcher._task_registry, "resolve", return_value=("test-agent.md", "test-model")),
        ):
            task = {"id": task_id, "type": "test-task", "by": "test-submitter"}
            task_dispatcher.dispatch_task(db_path, ps, task)

            # dispatch_agent should NOT have been called
            assert not dispatch_called

        # Verify the task is failed in the DB with the expected error
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, error FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "failed", f"Expected 'failed', got '{row[0]}'"
        assert "no payload_path" in (row[1] or "")

    def test_dispatcher_accepts_valid_payload(self, tmp_path: Path) -> None:
        """dispatch_task with valid payload_path succeeds."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        # Create a valid payload file
        payload = ps / "artifacts" / "test-payload.md"
        payload.write_text("# Test Payload\n\nDo the thing.\n", encoding="utf-8")

        # Pre-create output + meta sidecar so the post-dispatch path succeeds
        artifacts = ps / "artifacts"
        output_path = artifacts / f"task-{task_id}-output.md"
        output_path.write_text("ok\n", encoding="utf-8")
        meta_path = output_path.with_suffix(".meta.json")
        meta_path.write_text(
            json.dumps({"returncode": 0, "timed_out": False}) + "\n",
            encoding="utf-8",
        )

        from flow.engine import task_dispatcher as task_dispatcher

        dispatch_called = False

        def fake_dispatch(*args, **kwargs):
            nonlocal dispatch_called
            dispatch_called = True
            return "ok"

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(task_dispatcher._task_registry, "resolve", return_value=("test-agent.md", "test-model")),
            patch.object(Reconciler, "reconcile_task_completion"),
        ):
            task = {
                "id": task_id,
                "type": "test-task",
                "by": "test-submitter",
                "payload": str(payload),
            }
            task_dispatcher.dispatch_task(db_path, ps, task)

            # dispatch_agent SHOULD have been called
            assert dispatch_called

        # Verify the task completed
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "complete", f"Expected 'complete', got '{row[0]}'"


# ---------------------------------------------------------------------------
# Test: flow_schema validation rejects missing payload_path
# ---------------------------------------------------------------------------

class TestFlowValidationPayloadRequired:
    """R80/P1: validate_flow_declaration errors on missing payload_path."""

    def test_flow_validation_rejects_missing_payload(self) -> None:
        """Chain step without payload_path triggers validation error."""
        decl = FlowDeclaration(
            version=1,
            actions=[ChainAction(steps=[
                TaskSpec(task_type="staleness.alignment_check"),
            ])],
        )
        errors = validate_flow_declaration(decl)
        assert any("missing payload_path" in e for e in errors), (
            f"Expected 'missing payload_path' error, got: {errors}"
        )

    def test_flow_validation_accepts_payload(self) -> None:
        """Chain step with payload_path passes validation."""
        decl = FlowDeclaration(
            version=1,
            actions=[ChainAction(steps=[
                TaskSpec(
                    task_type="staleness.alignment_check",
                    payload_path="artifacts/test-prompt.md",
                ),
            ])],
        )
        errors = validate_flow_declaration(decl)
        assert not any("missing payload_path" in e for e in errors), (
            f"Unexpected payload_path error: {errors}"
        )

    def test_fanout_branch_step_rejects_missing_payload(self) -> None:
        """Fanout branch step without payload_path triggers error."""
        decl = FlowDeclaration(
            version=2,
            actions=[FanoutAction(
                branches=[
                    BranchSpec(steps=[
                        TaskSpec(task_type="staleness.alignment_check"),
                    ]),
                ],
            )],
        )
        errors = validate_flow_declaration(decl)
        payload_errors = [e for e in errors if "missing payload_path" in e]
        assert len(payload_errors) >= 1, (
            f"Expected payload_path error for fanout branch, got: {errors}"
        )

    def test_fanout_branch_step_accepts_payload(self) -> None:
        """Fanout branch step with payload_path passes validation."""
        decl = FlowDeclaration(
            version=2,
            actions=[FanoutAction(
                branches=[
                    BranchSpec(steps=[
                        TaskSpec(
                            task_type="staleness.alignment_check",
                            payload_path="artifacts/prompt.md",
                        ),
                    ]),
                    BranchSpec(steps=[
                        TaskSpec(
                            task_type="signals.impact_analysis",
                            payload_path="artifacts/prompt2.md",
                        ),
                    ]),
                ],
                gate=None,
            )],
        )
        errors = validate_flow_declaration(decl)
        payload_errors = [e for e in errors if "missing payload_path" in e]
        assert len(payload_errors) == 0, (
            f"Unexpected payload_path errors: {payload_errors}"
        )
