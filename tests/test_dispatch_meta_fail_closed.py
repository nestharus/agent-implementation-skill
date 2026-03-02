"""Tests for fail-closed dispatch metadata sidecar reading (Proposal 2).

Covers:
- _read_dispatch_meta(): returns None when file absent
- _read_dispatch_meta(): returns parsed dict when file is valid JSON
- _read_dispatch_meta(): renames malformed file to .malformed.json, returns sentinel
- Full dispatcher integration: malformed .meta.json causes task to fail
- Full dispatcher integration: absent .meta.json still allows timeout-prefix fallback
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Walk upward from this file to find the project root."""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "SKILL.md").exists():
            return current
        if (current / "scripts").is_dir() and (current / "agents").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path(__file__).resolve().parent.parent


_PROJECT_ROOT = _find_project_root()
_WORKFLOW_HOME = (
    _PROJECT_ROOT / "src"
    if (_PROJECT_ROOT / "src" / "scripts").exists()
    else _PROJECT_ROOT
)
_DB_SH = _WORKFLOW_HOME / "scripts" / "db.sh"


def _init_db(db_path: Path) -> None:
    """Initialize a fresh database via db.sh."""
    subprocess.run(
        ["bash", str(_DB_SH), "init", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _setup_planspace(tmp_path: Path) -> Path:
    """Create a planspace with initialized DB."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    artifacts = ps / "artifacts"
    artifacts.mkdir(parents=True)
    _init_db(ps / "run.db")
    return ps


def _submit_task(db_path: str, task_type: str = "test-task") -> str:
    """Submit a task and return its ID."""
    result = subprocess.run(
        ["bash", str(_DB_SH), "submit-task", db_path,
         task_type, "--by", "test-submitter"],
        check=True, capture_output=True, text=True,
    )
    # Output format: "submitted:<id>"
    return result.stdout.strip().split(":")[1]


# ---------------------------------------------------------------------------
# Unit tests: _read_dispatch_meta
# ---------------------------------------------------------------------------

class TestReadDispatchMeta:
    """Tests for the 3-state dispatch meta reader."""

    def test_returns_none_when_file_absent(self, tmp_path: Path) -> None:
        """Non-existent file returns None (not an error)."""
        from task_dispatcher import _read_dispatch_meta

        result = _read_dispatch_meta(tmp_path / "nonexistent.meta.json")
        assert result is None

    def test_returns_dict_on_valid_json(self, tmp_path: Path) -> None:
        """Valid JSON file returns parsed dict."""
        from task_dispatcher import _read_dispatch_meta

        meta_path = tmp_path / "task-1-output.meta.json"
        meta_path.write_text(
            json.dumps({"returncode": 0, "timed_out": False}),
            encoding="utf-8",
        )

        result = _read_dispatch_meta(meta_path)
        assert isinstance(result, dict)
        assert result["returncode"] == 0
        assert result["timed_out"] is False

    def test_renames_malformed_to_dotmalformed(self, tmp_path: Path) -> None:
        """Malformed JSON is renamed to .malformed.json and sentinel returned."""
        from task_dispatcher import _DISPATCH_META_CORRUPT, _read_dispatch_meta

        meta_path = tmp_path / "task-2-output.meta.json"
        meta_path.write_text("{not valid json at all", encoding="utf-8")

        result = _read_dispatch_meta(meta_path)
        assert result is _DISPATCH_META_CORRUPT

        # Original file gone, .meta.malformed.json preserved
        assert not meta_path.exists()
        malformed_path = meta_path.with_suffix(".malformed.json")
        assert malformed_path.exists()

    def test_malformed_preserves_content(self, tmp_path: Path) -> None:
        """Renamed file preserves the original corrupt content."""
        from task_dispatcher import _read_dispatch_meta

        meta_path = tmp_path / "task-3-output.meta.json"
        corrupt_content = "{this is definitely not valid json"
        meta_path.write_text(corrupt_content, encoding="utf-8")

        _read_dispatch_meta(meta_path)

        malformed_path = meta_path.with_suffix(".malformed.json")
        assert malformed_path.read_text(encoding="utf-8") == corrupt_content

    def test_malformed_logs_warning(self, tmp_path: Path, capsys) -> None:
        """Malformed file logs a warning mentioning the path."""
        from task_dispatcher import _read_dispatch_meta

        meta_path = tmp_path / "task-4-output.meta.json"
        meta_path.write_text("{bad", encoding="utf-8")

        _read_dispatch_meta(meta_path)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "Malformed dispatch meta" in captured.out


# ---------------------------------------------------------------------------
# Integration: malformed .meta.json fails the task
# ---------------------------------------------------------------------------

class TestDispatcherMetaCorruptionFailsClosed:
    """Malformed .meta.json causes the task to fail (not silently succeed)."""

    def test_malformed_meta_fails_task(self, tmp_path: Path) -> None:
        """When .meta.json is corrupt, task_dispatcher fails the task."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        # R80/P1: create a payload file
        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        output_path = artifacts / f"task-{task_id}-output.md"
        output_path.write_text("agent produced output\n", encoding="utf-8")

        # Write a corrupt .meta.json
        meta_path = output_path.with_suffix(".meta.json")
        meta_path.write_text("{broken json here", encoding="utf-8")

        import task_dispatcher

        def fake_dispatch(*args, **kwargs):
            return "agent produced output"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("test-agent.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {"id": task_id, "type": "test-task", "by": "test-submitter", "payload": str(payload)}
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify the task is failed in the DB
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, error FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "failed", f"Expected 'failed', got '{row[0]}'"
        assert "corrupt" in (row[1] or "").lower()

    def test_malformed_meta_renames_sidecar(self, tmp_path: Path) -> None:
        """Corrupt .meta.json is renamed to .malformed.json by dispatcher."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        # R80/P1: create a payload file
        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        output_path = artifacts / f"task-{task_id}-output.md"
        output_path.write_text("ok\n", encoding="utf-8")

        meta_path = output_path.with_suffix(".meta.json")
        meta_path.write_text("{not json}", encoding="utf-8")

        import task_dispatcher

        def fake_dispatch(*args, **kwargs):
            return "ok"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("test-agent.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {"id": task_id, "type": "test-task", "by": "test-submitter", "payload": str(payload)}
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Original .meta.json should be gone
        assert not meta_path.exists()
        # .meta.malformed.json should exist (with_suffix replaces last suffix)
        assert meta_path.with_suffix(".malformed.json").exists()

    def test_malformed_meta_calls_reconcile_as_failure(
        self, tmp_path: Path,
    ) -> None:
        """Corrupt .meta.json triggers reconcile_task_completion with 'failed'."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        # R80/P1: create a payload file
        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        output_path = artifacts / f"task-{task_id}-output.md"
        output_path.write_text("ok\n", encoding="utf-8")

        meta_path = output_path.with_suffix(".meta.json")
        meta_path.write_text("{{{{", encoding="utf-8")

        import task_dispatcher

        def fake_dispatch(*args, **kwargs):
            return "ok"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("test-agent.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion") as mock_reconcile,
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {"id": task_id, "type": "test-task", "by": "test-submitter", "payload": str(payload)}
            task_dispatcher.dispatch_task(db_path, ps, task)

            # reconcile_task_completion must have been called with "failed"
            assert mock_reconcile.called
            call_args = mock_reconcile.call_args
            # positional: (db_path, planspace, task_id, status, output_path)
            assert call_args[0][3] == "failed"
            assert "corrupt" in (call_args[1].get("error", "") or "").lower()


# ---------------------------------------------------------------------------
# Integration: absent .meta.json allows timeout-prefix fallback
# ---------------------------------------------------------------------------

class TestAbsentMetaAllowsFallback:
    """When .meta.json is absent, dispatcher falls through to timeout-prefix."""

    def test_absent_meta_timeout_prefix_detected(
        self, tmp_path: Path,
    ) -> None:
        """No .meta.json + TIMEOUT: prefix -> task fails as timeout."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        # R80/P1: create a payload file
        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        # Do NOT write a .meta.json — simulate legacy dispatch

        import task_dispatcher

        def fake_dispatch(*args, **kwargs):
            # dispatch_agent returns TIMEOUT: prefix (legacy behavior)
            return "TIMEOUT: Agent exceeded 600s"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("test-agent.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {"id": task_id, "type": "test-task", "by": "test-submitter", "payload": str(payload)}
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify the task is failed (timeout) in the DB
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, error FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "failed", f"Expected 'failed', got '{row[0]}'"
        assert "timeout" in (row[1] or "").lower()

    def test_absent_meta_normal_output_completes(
        self, tmp_path: Path,
    ) -> None:
        """No .meta.json + normal output -> task completes (fallback path)."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        # R80/P1: create a payload file
        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        import task_dispatcher

        def fake_dispatch(*args, **kwargs):
            return "normal agent output"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("test-agent.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {"id": task_id, "type": "test-task", "by": "test-submitter", "payload": str(payload)}
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Verify the task completed successfully
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "complete", f"Expected 'complete', got '{row[0]}'"
