"""Tests for P4: freshness gate for section-scoped queued tasks.

Verifies:
- compute_section_freshness produces stable, content-sensitive hashes
- dispatcher rejects tasks whose freshness token no longer matches
- backward compat: tasks without tokens and non-section scopes dispatch normally
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from _paths import DB_SH
from conftest import override_dispatcher_and_guard


# ---------------------------------------------------------------------------
# Helpers (same patterns as test_dispatcher_section_scope.py)
# ---------------------------------------------------------------------------


def _init_db(db_path: Path) -> None:
    subprocess.run(
        ["bash", str(DB_SH), "init", str(db_path)],
        check=True, capture_output=True, text=True,
    )


_planspace_counter = 0


def _setup_planspace(tmp_path: Path) -> Path:
    global _planspace_counter  # noqa: PLW0603
    _planspace_counter += 1
    ps = tmp_path / f"planspace-{_planspace_counter}"
    ps.mkdir()
    artifacts = ps / "artifacts"
    for subdir in ("sections", "proposals", "signals"):
        (artifacts / subdir).mkdir(parents=True)
    _init_db(ps / "run.db")
    return ps


def _create_section_artifacts(planspace: Path, section: str) -> None:
    """Create alignment artifacts for a section."""
    artifacts = planspace / "artifacts"
    (artifacts / "sections" / f"section-{section}-alignment-excerpt.md").write_text(
        f"# Alignment for section {section}\nOriginal content.\n"
    )
    (artifacts / "sections" / f"section-{section}-proposal-excerpt.md").write_text(
        f"# Proposal for section {section}\nOriginal proposal.\n"
    )
    (artifacts / "sections" / f"section-{section}.md").write_text(
        f"# Section {section}\nSpec content.\n"
    )
    (artifacts / "proposals" / f"section-{section}-integration-proposal.md").write_text(
        f"# Integration for section {section}\nOriginal integration.\n"
    )


def _submit_task_with_freshness(
    db_path: str,
    task_type: str = "staleness.alignment_check",
    scope: str | None = None,
    freshness_token: str | None = None,
) -> str:
    """Submit a task with optional freshness token. Returns task ID."""
    cmd = [
        "bash", str(DB_SH), "submit-task", db_path,
        "test-submitter", task_type,
    ]
    if scope:
        cmd.extend(["--scope", scope])
    if freshness_token:
        cmd.extend(["--freshness-token", freshness_token])
    result = subprocess.run(
        cmd, check=True, capture_output=True, text=True,
    )
    # Output format: "task:<id>"
    return result.stdout.strip().split(":")[1]


def _dispatch_and_capture(
    tmp_path: Path,
    task: dict[str, str],
    planspace: Path | None = None,
) -> dict:
    """Run dispatch_task with mocked dispatch_agent, return capture dict.

    Returns {"dispatched": True, "kwargs": {...}} on dispatch, or
    {"dispatched": False, "fail_error": "..."} when the task is failed
    before dispatch.
    """
    ps = planspace or _setup_planspace(tmp_path)
    db_path = str(ps / "run.db")

    task_id = _submit_task_with_freshness(
        db_path,
        task.get("type", "staleness.alignment_check"),
        task.get("scope"),
        task.get("freshness"),
    )
    task["id"] = task_id

    # R80/P1: All dispatched tasks require a payload_path.
    artifacts = ps / "artifacts"
    payload = artifacts / "test-payload.md"
    payload.write_text("# Test payload\n", encoding="utf-8")
    task["payload"] = str(payload)

    # Pre-create output + meta sidecar so the post-dispatch path succeeds
    output_path = artifacts / f"task-{task_id}-output.md"
    output_path.write_text("ok\n", encoding="utf-8")
    meta_path = output_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps({"returncode": 0, "timed_out": False}) + "\n",
        encoding="utf-8",
    )

    captured: dict = {"dispatched": False}

    def fake_dispatch(*args, **kwargs):
        captured["dispatched"] = True
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "ok"

    # Track fail-task calls
    from flow.engine import task_dispatcher as task_dispatcher

    original_db_cmd = task_dispatcher.db_cmd
    fail_errors: list[str] = []

    def tracking_db_cmd(dp, command, *a):
        if command == "fail-task":
            # Extract --error value
            for i, arg in enumerate(a):
                if arg == "--error" and i + 1 < len(a):
                    fail_errors.append(a[i + 1])
        return original_db_cmd(dp, command, *a)

    from containers import Services

    with (
        override_dispatcher_and_guard(fake_dispatch),
        patch.object(task_dispatcher._task_registry, "resolve", return_value=("test-agent.md", "test-model")),
        patch.object(task_dispatcher, "reconcile_task_completion"),
        patch.object(task_dispatcher, "db_cmd", side_effect=tracking_db_cmd),
    ):
        # Use real freshness service — this file tests the freshness gate
        Services.freshness.reset_override()
        task_dispatcher.dispatch_task(db_path, ps, task)

    if fail_errors:
        captured["fail_error"] = fail_errors[-1]

    return captured


# ---------------------------------------------------------------------------
# Tests: compute_section_freshness
# ---------------------------------------------------------------------------

class TestComputeSectionFreshness:
    """Unit tests for the freshness token computation."""

    def test_stable_hash(self, tmp_path: Path) -> None:
        """Same artifacts -> same token on repeated calls."""
        ps = _setup_planspace(tmp_path)
        _create_section_artifacts(ps, "01")

        from staleness.service.freshness_calculator import compute_section_freshness

        t1 = compute_section_freshness(ps, "01")
        t2 = compute_section_freshness(ps, "01")
        assert t1 == t2
        assert len(t1) == 16  # truncated hex

    def test_changes_on_artifact_modification(self, tmp_path: Path) -> None:
        """Modifying an artifact changes the token."""
        ps = _setup_planspace(tmp_path)
        _create_section_artifacts(ps, "02")

        from staleness.service.freshness_calculator import compute_section_freshness

        t_before = compute_section_freshness(ps, "02")

        # Modify one artifact
        (ps / "artifacts" / "sections" / "section-02-alignment-excerpt.md").write_text(
            "# Alignment for section 02\nModified content.\n"
        )

        t_after = compute_section_freshness(ps, "02")
        assert t_before != t_after

    def test_empty_section(self, tmp_path: Path) -> None:
        """No artifacts exist — still returns a valid 16-char hex hash."""
        ps = _setup_planspace(tmp_path)

        from staleness.service.freshness_calculator import compute_section_freshness

        token = compute_section_freshness(ps, "99")
        assert len(token) == 16
        # SHA256 of empty input — should be the truncated empty digest
        assert all(c in "0123456789abcdef" for c in token)

    def test_different_sections_differ(self, tmp_path: Path) -> None:
        """Different sections produce different tokens."""
        ps = _setup_planspace(tmp_path)
        _create_section_artifacts(ps, "01")
        _create_section_artifacts(ps, "02")

        from staleness.service.freshness_calculator import compute_section_freshness

        t1 = compute_section_freshness(ps, "01")
        t2 = compute_section_freshness(ps, "02")
        assert t1 != t2


# ---------------------------------------------------------------------------
# Tests: dispatcher freshness gate
# ---------------------------------------------------------------------------

class TestDispatcherFreshnessGate:
    """Integration tests for the dispatcher's P4 freshness gate."""

    def test_dispatches_when_token_matches(self, tmp_path: Path) -> None:
        """Task with matching freshness token dispatches normally."""
        ps = _setup_planspace(tmp_path)
        _create_section_artifacts(ps, "03")

        from staleness.service.freshness_calculator import compute_section_freshness

        token = compute_section_freshness(ps, "03")

        task = {
            "type": "staleness.alignment_check",
            "by": "test-submitter",
            "scope": "section-03",
            "freshness": token,
        }
        result = _dispatch_and_capture(tmp_path, task, planspace=ps)
        assert result["dispatched"], (
            f"Expected dispatch but got: {result}"
        )

    def test_fails_stale_task(self, tmp_path: Path) -> None:
        """Task with stale freshness token is failed before dispatch."""
        ps = _setup_planspace(tmp_path)
        _create_section_artifacts(ps, "04")

        from staleness.service.freshness_calculator import compute_section_freshness

        old_token = compute_section_freshness(ps, "04")

        # Modify an artifact to make the token stale
        (ps / "artifacts" / "sections" / "section-04-proposal-excerpt.md").write_text(
            "# Proposal for section 04\nCompletely rewritten.\n"
        )

        task = {
            "type": "staleness.alignment_check",
            "by": "test-submitter",
            "scope": "section-04",
            "freshness": old_token,
        }
        result = _dispatch_and_capture(tmp_path, task, planspace=ps)
        assert not result["dispatched"], "Stale task should NOT be dispatched"
        assert "stale alignment" in result.get("fail_error", ""), (
            f"Expected 'stale alignment' error, got: {result.get('fail_error')}"
        )

    def test_dispatches_without_token(self, tmp_path: Path) -> None:
        """Task with no freshness_token dispatches normally (backward compat)."""
        ps = _setup_planspace(tmp_path)
        _create_section_artifacts(ps, "05")

        task = {
            "type": "staleness.alignment_check",
            "by": "test-submitter",
            "scope": "section-05",
            # No freshness token
        }
        result = _dispatch_and_capture(tmp_path, task, planspace=ps)
        assert result["dispatched"], (
            f"Task without freshness token should dispatch, got: {result}"
        )

    def test_dispatches_non_section_scope_with_token(
        self, tmp_path: Path,
    ) -> None:
        """Task with freshness token but non-section scope dispatches normally.

        Freshness is only checked when section_number is parsed from scope.
        """
        ps = _setup_planspace(tmp_path)

        task = {
            "type": "staleness.alignment_check",
            "by": "test-submitter",
            "scope": "coord-group-1",
            "freshness": "deadbeef12345678",  # arbitrary token
        }
        result = _dispatch_and_capture(tmp_path, task, planspace=ps)
        assert result["dispatched"], (
            f"Non-section scope should dispatch regardless of token, got: {result}"
        )


# ---------------------------------------------------------------------------
# Tests: db.sh freshness_token round-trip
# ---------------------------------------------------------------------------

class TestDbShFreshnessToken:
    """Verify db.sh submit-task stores and next-task outputs freshness_token."""

    def test_submit_and_retrieve_freshness_token(
        self, tmp_path: Path,
    ) -> None:
        """freshness_token persists through submit-task -> next-task."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")

        _submit_task_with_freshness(
            db_path, "staleness.alignment_check",
            scope="section-01",
            freshness_token="abc123def4567890",
        )

        result = subprocess.run(
            ["bash", str(DB_SH), "next-task", db_path],
            check=True, capture_output=True, text=True,
        )
        output = result.stdout.strip()
        assert "freshness=abc123def4567890" in output

    def test_next_task_omits_freshness_when_null(
        self, tmp_path: Path,
    ) -> None:
        """next-task output does not include freshness= when token is NULL."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")

        _submit_task_with_freshness(
            db_path, "staleness.alignment_check",
            scope="section-01",
        )

        result = subprocess.run(
            ["bash", str(DB_SH), "next-task", db_path],
            check=True, capture_output=True, text=True,
        )
        output = result.stdout.strip()
        assert "freshness=" not in output
