"""Tests for P3: section identity recovery from queued task scope.

Verifies that dispatch_task() parses section-NN from the task's scope
field and passes the extracted section_number to dispatch_agent.
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


_planspace_counter = 0


def _setup_planspace(tmp_path: Path) -> Path:
    """Create a planspace with initialized DB.

    Uses a counter to allow multiple planspaces under the same tmp_path
    (needed for parameterized/loop tests).
    """
    global _planspace_counter  # noqa: PLW0603
    _planspace_counter += 1
    ps = tmp_path / f"planspace-{_planspace_counter}"
    ps.mkdir()
    artifacts = ps / "artifacts"
    artifacts.mkdir(parents=True)
    _init_db(ps / "run.db")
    return ps


def _submit_task(
    db_path: str, task_type: str = "test-task", scope: str | None = None,
) -> str:
    """Submit a task and return its ID."""
    cmd = [
        "bash", str(_DB_SH), "submit-task", db_path,
        task_type, "--by", "test-submitter",
    ]
    if scope:
        cmd.extend(["--scope", scope])
    result = subprocess.run(
        cmd, check=True, capture_output=True, text=True,
    )
    # Output format: "submitted:<id>"
    return result.stdout.strip().split(":")[1]


def _dispatch_with_captured_kwargs(
    tmp_path: Path,
    task: dict[str, str],
) -> dict:
    """Run dispatch_task with mocked dispatch_agent, return its kwargs.

    Sets up the standard mocks (resolve_task, validate_dynamic_content,
    render_template, reconcile_task_completion) and captures the kwargs
    passed to dispatch_agent.
    """
    ps = _setup_planspace(tmp_path)
    db_path = str(ps / "run.db")

    # Submit the task to get a real DB row (needed for claim-task / complete-task)
    task_id = _submit_task(db_path, task.get("type", "test-task"), task.get("scope"))
    task["id"] = task_id

    # Pre-create output + meta sidecar so the post-dispatch path succeeds
    artifacts = ps / "artifacts"
    output_path = artifacts / f"task-{task_id}-output.md"
    output_path.write_text("ok\n", encoding="utf-8")
    meta_path = output_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps({"returncode": 0, "timed_out": False}) + "\n",
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_dispatch(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "ok"

    import task_dispatcher

    with (
        patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
        patch.object(task_dispatcher, "resolve_task", return_value=("test-agent.md", "test-model")),
        patch.object(task_dispatcher, "reconcile_task_completion"),
        patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        patch.object(task_dispatcher, "render_template", side_effect=lambda name, body, **kw: body),
    ):
        task_dispatcher.dispatch_task(db_path, ps, task)

    return captured


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSectionScopeRecovery:
    """P3: Verify section identity is recovered from task scope."""

    def test_section_scoped_task_passes_section_number(
        self, tmp_path: Path,
    ) -> None:
        """scope=section-03 -> section_number='03' in dispatch_agent call."""
        task = {"type": "test-task", "by": "test-submitter", "scope": "section-03"}
        captured = _dispatch_with_captured_kwargs(tmp_path, task)

        assert "kwargs" in captured, "dispatch_agent was not called"
        assert captured["kwargs"]["section_number"] == "03"

    def test_non_section_scope_passes_none(
        self, tmp_path: Path,
    ) -> None:
        """scope=coord-group-1 -> section_number=None."""
        task = {"type": "test-task", "by": "test-submitter", "scope": "coord-group-1"}
        captured = _dispatch_with_captured_kwargs(tmp_path, task)

        assert "kwargs" in captured, "dispatch_agent was not called"
        assert captured["kwargs"]["section_number"] is None

    def test_no_scope_passes_none(
        self, tmp_path: Path,
    ) -> None:
        """No scope field -> section_number=None."""
        task = {"type": "test-task", "by": "test-submitter"}
        captured = _dispatch_with_captured_kwargs(tmp_path, task)

        assert "kwargs" in captured, "dispatch_agent was not called"
        assert captured["kwargs"]["section_number"] is None

    def test_section_scope_regex_only_matches_exact_pattern(
        self, tmp_path: Path,
    ) -> None:
        """Only exact section-NN matches, not partial/embedded patterns."""
        non_matching_scopes = [
            "section-03-extra",
            "my-section-03",
            "section-",
            "section-abc",
            "SECTION-03",
            "scope-delta",
            "parent-resume",
        ]
        for scope in non_matching_scopes:
            task = {"type": "test-task", "by": "test-submitter", "scope": scope}
            captured = _dispatch_with_captured_kwargs(tmp_path, task)
            assert "kwargs" in captured, f"dispatch_agent not called for scope={scope}"
            assert captured["kwargs"]["section_number"] is None, (
                f"scope={scope!r} should NOT match, but got "
                f"section_number={captured['kwargs']['section_number']!r}"
            )

    def test_section_scope_multidigit(
        self, tmp_path: Path,
    ) -> None:
        """scope=section-123 -> section_number='123'."""
        task = {"type": "test-task", "by": "test-submitter", "scope": "section-123"}
        captured = _dispatch_with_captured_kwargs(tmp_path, task)

        assert "kwargs" in captured, "dispatch_agent was not called"
        assert captured["kwargs"]["section_number"] == "123"
