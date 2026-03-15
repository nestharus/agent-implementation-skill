"""Tests for dispatch metadata sidecar and task_dispatcher failure routing.

Verifies that:
- dispatch_agent writes a .meta.json sidecar next to the output file
- The sidecar records returncode and timed_out correctly
- task_dispatcher reads the sidecar and routes to fail-task on nonzero rc
- Output artifacts are preserved for forensics regardless of pass/fail
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from _paths import DB_SH
from src.orchestrator.path_registry import PathRegistry
from conftest import override_dispatcher_and_guard

import dispatch.engine.agent_executor as executor_mod
import dispatch.engine.section_dispatcher as dispatch_mod
from dispatch.engine.section_dispatcher import SectionDispatcher
from containers import Services, TaskRouterService


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


def _make_dispatcher() -> SectionDispatcher:
    """Create a SectionDispatcher via the container."""
    return SectionDispatcher(
        config=Services.config(),
        pipeline_control=Services.pipeline_control(),
        logger=Services.logger(),
        communicator=Services.communicator(),
        task_router=Services.task_router(),
        prompt_guard=Services.prompt_guard(),
        artifact_io=Services.artifact_io(),
    )


@pytest.fixture()
def dispatch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a minimal dispatch environment.

    Patches resolve_agent_path in the dispatch module to find test-agent.md
    in a tmp_path agents/ dir.
    """
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    agent_path = agents_dir / "test-agent.md"
    agent_path.write_text("# Test Agent\nDo nothing.\n")
    resolver = lambda self, name: agent_path if name == "test-agent.md" else (_ for _ in ()).throw(FileNotFoundError(name))
    monkeypatch.setattr(TaskRouterService, "resolve_agent_path", resolver)
    return tmp_path


# ---------------------------------------------------------------------------
# Test: dispatch_agent writes .meta.json sidecar
# ---------------------------------------------------------------------------

class TestDispatchWritesMeta:
    """Verify dispatch_agent writes .meta.json next to output."""

    def test_dispatch_writes_meta_sidecar(
        self, tmp_path: Path, dispatch_env: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Normal exit (rc=0) produces sidecar with returncode=0."""
        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("# Test\n")
        output_path = tmp_path / "output.md"

        fake_result = SimpleNamespace(
            stdout="hello", stderr="", returncode=0,
        )
        monkeypatch.setattr(executor_mod.subprocess, "run", lambda *a, **kw: fake_result)

        _make_dispatcher().dispatch_agent(
            "test-model", prompt_path, output_path,
            agent_file="test-agent.md",
        )

        meta_path = output_path.with_suffix(".meta.json")
        assert meta_path.exists(), ".meta.json sidecar not written"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["returncode"] == 0
        assert meta["timed_out"] is False

    def test_dispatch_nonzero_rc_meta(
        self, tmp_path: Path, dispatch_env: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Nonzero exit code is recorded in the sidecar."""
        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("# Test\n")
        output_path = tmp_path / "output.md"

        fake_result = SimpleNamespace(
            stdout="error output", stderr="stack trace", returncode=1,
        )
        monkeypatch.setattr(executor_mod.subprocess, "run", lambda *a, **kw: fake_result)

        _make_dispatcher().dispatch_agent(
            "test-model", prompt_path, output_path,
            agent_file="test-agent.md",
        )

        meta_path = output_path.with_suffix(".meta.json")
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["returncode"] == 1
        assert meta["timed_out"] is False

    def test_dispatch_timeout_meta(
        self, tmp_path: Path, dispatch_env: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Timeout produces sidecar with returncode=None, timed_out=True."""
        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("# Test\n")
        output_path = tmp_path / "output.md"

        def _raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="agents", timeout=600)

        monkeypatch.setattr(executor_mod.subprocess, "run", _raise_timeout)

        _make_dispatcher().dispatch_agent(
            "test-model", prompt_path, output_path,
            agent_file="test-agent.md",
        )

        meta_path = output_path.with_suffix(".meta.json")
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["returncode"] is None
        assert meta["timed_out"] is True

        # Output file should contain TIMEOUT prefix for backward compat
        assert output_path.read_text(encoding="utf-8").startswith("TIMEOUT:")


# ---------------------------------------------------------------------------
# Test: task_dispatcher reads sidecar and routes correctly
# ---------------------------------------------------------------------------

class TestDispatcherReadsMetaSidecar:
    """Verify task_dispatcher fails tasks on nonzero rc via sidecar."""

    def _setup_planspace(self, tmp_path: Path) -> Path:
        """Create a planspace with initialized DB."""
        ps = tmp_path / "planspace"
        ps.mkdir()
        PathRegistry(ps).ensure_artifacts_tree()
        _init_db(ps / "run.db")
        return ps

    def _submit_task(self, db_path: str, task_type: str = "test-task") -> str:
        """Submit a task and return its ID."""
        result = subprocess.run(
            ["bash", str(DB_SH), "submit-task", db_path,
             task_type, "--by", "test-submitter"],
            check=True, capture_output=True, text=True,
        )
        # Output format: "submitted:<id>"
        return result.stdout.strip().split(":")[1]

    def test_dispatcher_fails_task_on_nonzero_rc(
        self, tmp_path: Path,
    ) -> None:
        """When sidecar shows rc!=0, task_dispatcher calls fail-task."""
        ps = self._setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = self._submit_task(db_path)

        # R80/P1: create a payload file for the task
        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        # Write output artifact and sidecar as dispatch_agent would
        output_path = artifacts / f"task-{task_id}-output.md"
        output_path.write_text("error output from agent\n", encoding="utf-8")
        meta_path = output_path.with_suffix(".meta.json")
        meta_path.write_text(
            json.dumps({"returncode": 1, "timed_out": False}) + "\n",
            encoding="utf-8",
        )

        from flow.engine import task_dispatcher as task_dispatcher

        # Mock dispatch_agent to return the output (files already written)
        def fake_dispatch(*args, **kwargs):
            return "error output from agent"

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(task_dispatcher._task_registry, "resolve", return_value=("test-agent.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
        ):
            task = {"id": task_id, "type": "test-task", "by": "test-submitter", "payload": str(payload)}
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify the task is now failed in the DB
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, error FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "failed", f"Expected 'failed', got '{row[0]}'"
        assert "return code 1" in (row[1] or "")

    def test_dispatcher_completes_task_on_zero_rc(
        self, tmp_path: Path,
    ) -> None:
        """When sidecar shows rc=0, task_dispatcher calls complete-task."""
        ps = self._setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = self._submit_task(db_path)

        # R80/P1: create a payload file for the task
        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        # Write output artifact and sidecar as dispatch_agent would
        output_path = artifacts / f"task-{task_id}-output.md"
        output_path.write_text("success output\n", encoding="utf-8")
        meta_path = output_path.with_suffix(".meta.json")
        meta_path.write_text(
            json.dumps({"returncode": 0, "timed_out": False}) + "\n",
            encoding="utf-8",
        )

        from flow.engine import task_dispatcher as task_dispatcher

        def fake_dispatch(*args, **kwargs):
            return "success output"

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(task_dispatcher._task_registry, "resolve", return_value=("test-agent.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
        ):
            task = {"id": task_id, "type": "test-task", "by": "test-submitter", "payload": str(payload)}
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify the task is complete
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "complete", f"Expected 'complete', got '{row[0]}'"

    def test_dispatcher_preserves_output_on_failure(
        self, tmp_path: Path,
    ) -> None:
        """Output artifact file still exists after a failed dispatch."""
        ps = self._setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = self._submit_task(db_path)

        # R80/P1: create a payload file for the task
        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        output_path = artifacts / f"task-{task_id}-output.md"
        output_path.write_text("error output preserved\n", encoding="utf-8")
        meta_path = output_path.with_suffix(".meta.json")
        meta_path.write_text(
            json.dumps({"returncode": 1, "timed_out": False}) + "\n",
            encoding="utf-8",
        )

        from flow.engine import task_dispatcher as task_dispatcher

        def fake_dispatch(*args, **kwargs):
            return "error output preserved"

        with (
            override_dispatcher_and_guard(fake_dispatch),
            patch.object(task_dispatcher._task_registry, "resolve", return_value=("test-agent.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
        ):
            task = {"id": task_id, "type": "test-task", "by": "test-submitter", "payload": str(payload)}
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Output artifact must still exist for forensics
        assert output_path.exists(), "Output artifact was deleted on failure"
        content = output_path.read_text(encoding="utf-8")
        assert "error output preserved" in content
