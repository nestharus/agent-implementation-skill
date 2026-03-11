"""Tests for the QA dispatch interceptor module and dispatcher integration.

Covers:
- read_qa_parameters(): absent, valid, malformed, non-dict files
- intercept_task(): PASS flow, REJECT flow, rationale file creation
- _parse_verdict(): various output formats
- Dispatcher integration: QA disabled, QA PASS, QA REJECT, fail-open
- DB event logging for QA intercepts
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from _paths import DB_SH, WORKFLOW_HOME


# ---------------------------------------------------------------------------
# Helpers — same pattern as test_dispatch_meta_fail_closed.py
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
    artifacts = ps / "artifacts"
    artifacts.mkdir(parents=True)
    _init_db(ps / "run.db")
    return ps


def _submit_task(db_path: str, task_type: str = "test-task") -> str:
    """Submit a task and return its ID."""
    result = subprocess.run(
        ["bash", str(DB_SH), "submit-task", db_path,
         task_type, "--by", "test-submitter"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip().split(":")[1]


# ---------------------------------------------------------------------------
# Unit tests: read_qa_parameters
# ---------------------------------------------------------------------------

class TestReadQaParameters:
    """Tests for the QA parameter file reader."""

    def test_returns_default_when_file_absent(self, tmp_path: Path) -> None:
        """No parameters.json -> qa_mode False."""
        from dispatch.service.qa_interceptor import read_qa_parameters

        ps = tmp_path / "planspace"
        ps.mkdir()
        (ps / "artifacts").mkdir()

        result = read_qa_parameters(ps)
        assert result == {"qa_mode": False}

    def test_returns_parsed_dict_when_valid(self, tmp_path: Path) -> None:
        """Valid parameters.json returns parsed content."""
        from dispatch.service.qa_interceptor import read_qa_parameters

        ps = tmp_path / "planspace"
        ps.mkdir()
        artifacts = ps / "artifacts"
        artifacts.mkdir()
        (artifacts / "parameters.json").write_text(
            json.dumps({"qa_mode": True, "extra": "value"}),
            encoding="utf-8",
        )

        result = read_qa_parameters(ps)
        assert result["qa_mode"] is True
        assert result["extra"] == "value"

    def test_renames_malformed_to_dotmalformed(self, tmp_path: Path) -> None:
        """Malformed JSON is renamed and defaults returned."""
        from dispatch.service.qa_interceptor import read_qa_parameters

        ps = tmp_path / "planspace"
        ps.mkdir()
        artifacts = ps / "artifacts"
        artifacts.mkdir()
        params_path = artifacts / "parameters.json"
        params_path.write_text("{not valid json", encoding="utf-8")

        result = read_qa_parameters(ps)
        assert result == {"qa_mode": False}
        assert not params_path.exists()
        assert (artifacts / "parameters.malformed.json").exists()

    def test_renames_non_dict_to_dotmalformed(self, tmp_path: Path) -> None:
        """Non-dict JSON (e.g., array) is treated as malformed."""
        from dispatch.service.qa_interceptor import read_qa_parameters

        ps = tmp_path / "planspace"
        ps.mkdir()
        artifacts = ps / "artifacts"
        artifacts.mkdir()
        params_path = artifacts / "parameters.json"
        params_path.write_text("[1, 2, 3]", encoding="utf-8")

        result = read_qa_parameters(ps)
        assert result == {"qa_mode": False}
        assert not params_path.exists()

    def test_missing_qa_mode_key_defaults_to_false(
        self, tmp_path: Path,
    ) -> None:
        """Valid JSON without qa_mode key gets default False."""
        from dispatch.service.qa_interceptor import read_qa_parameters

        ps = tmp_path / "planspace"
        ps.mkdir()
        artifacts = ps / "artifacts"
        artifacts.mkdir()
        (artifacts / "parameters.json").write_text(
            json.dumps({"other_param": 42}),
            encoding="utf-8",
        )

        result = read_qa_parameters(ps)
        assert result["qa_mode"] is False
        assert result["other_param"] == 42


# ---------------------------------------------------------------------------
# Unit tests: _parse_verdict
# ---------------------------------------------------------------------------

class TestParseVerdict:
    """Tests for QA verdict parsing."""

    def test_pass_verdict(self) -> None:
        from dispatch.service.qa_interceptor import _parse_verdict

        output = '{"verdict": "PASS", "rationale": "All good"}'
        verdict, rationale, violations = _parse_verdict(output)
        assert verdict == "PASS"
        assert rationale == "All good"
        assert violations == []

    def test_reject_verdict(self) -> None:
        from dispatch.service.qa_interceptor import _parse_verdict

        output = json.dumps({
            "verdict": "REJECT",
            "rationale": "Scope violation",
            "violations": ["v1", "v2"],
        })
        verdict, rationale, violations = _parse_verdict(output)
        assert verdict == "REJECT"
        assert "Scope violation" in rationale
        assert violations == ["v1", "v2"]

    def test_verdict_in_code_fences(self) -> None:
        from dispatch.service.qa_interceptor import _parse_verdict

        output = (
            "Here is my verdict:\n"
            '```json\n{"verdict": "PASS", "rationale": "OK"}\n```'
        )
        verdict, rationale, violations = _parse_verdict(output)
        assert verdict == "PASS"

    def test_garbage_output_degrades(self) -> None:
        """PAT-0014: garbage output maps to DEGRADED, not PASS."""
        from dispatch.service.qa_interceptor import _parse_verdict

        output = "This is total garbage with no JSON at all"
        verdict, rationale, violations = _parse_verdict(output)
        assert verdict == "DEGRADED"
        assert "could not be parsed" in rationale

    def test_unknown_verdict_degrades(self) -> None:
        """PAT-0014: unknown verdict maps to DEGRADED, not PASS."""
        from dispatch.service.qa_interceptor import _parse_verdict

        output = '{"verdict": "MAYBE", "rationale": "dunno"}'
        verdict, rationale, violations = _parse_verdict(output)
        assert verdict == "DEGRADED"
        assert "Unknown verdict" in rationale

    def test_empty_output_degrades(self) -> None:
        """PAT-0014: empty output maps to DEGRADED, not PASS."""
        from dispatch.service.qa_interceptor import _parse_verdict

        verdict, rationale, violations = _parse_verdict("")
        assert verdict == "DEGRADED"


# ---------------------------------------------------------------------------
# Unit tests: intercept_task (mocked dispatch_agent)
# ---------------------------------------------------------------------------

class TestInterceptTask:
    """Tests for the intercept_task function with mocked agent dispatch."""

    def test_pass_returns_true_none_none(self, tmp_path: Path) -> None:
        """PASS verdict returns (True, None, None)."""
        from dispatch.service.qa_interceptor import intercept_task

        ps = _setup_planspace(tmp_path)

        # Create a dummy target agent file.
        agents_dir = WORKFLOW_HOME / "agents"
        assert agents_dir.exists(), f"agents dir not found: {agents_dir}"

        task = {
            "id": "99",
            "type": "alignment_check",
            "by": "section-loop",
            "payload": str(ps / "artifacts" / "test-payload.md"),
        }
        (ps / "artifacts" / "test-payload.md").write_text(
            "# Test\n", encoding="utf-8",
        )

        mock_output = json.dumps({
            "verdict": "PASS",
            "rationale": "Contract compliant",
        })

        with patch("dispatch.service.qa_interceptor.dispatch_agent", return_value=mock_output):
            passed, rationale_path, reason_code = intercept_task(
                task, "alignment-judge.md", ps,
            )

        assert passed is True
        assert rationale_path is None
        assert reason_code is None

    def test_dispatch_uses_model_policy_key(self, tmp_path: Path) -> None:
        """QA dispatch resolves its model through model-policy.json."""
        from dispatch.service.qa_interceptor import intercept_task

        ps = _setup_planspace(tmp_path)
        artifacts = ps / "artifacts"
        (artifacts / "model-policy.json").write_text(
            json.dumps({"qa_interceptor": "policy-qa-model"}),
            encoding="utf-8",
        )

        task = {
            "id": "99b",
            "type": "alignment_check",
            "by": "section-loop",
            "payload": str(ps / "artifacts" / "test-payload.md"),
        }
        (ps / "artifacts" / "test-payload.md").write_text(
            "# Test\n", encoding="utf-8",
        )

        mock_output = json.dumps({
            "verdict": "PASS",
            "rationale": "Contract compliant",
        })

        with patch("dispatch.service.qa_interceptor.dispatch_agent", return_value=mock_output) as mock_dispatch:
            passed, rationale_path, reason_code = intercept_task(
                task, "alignment-judge.md", ps,
            )

        assert passed is True
        assert rationale_path is None
        assert mock_dispatch.call_args.args[0] == "policy-qa-model"

    def test_reject_returns_false_with_rationale(
        self, tmp_path: Path,
    ) -> None:
        """REJECT verdict returns (False, rationale_path) and writes file."""
        from dispatch.service.qa_interceptor import intercept_task

        ps = _setup_planspace(tmp_path)

        task = {
            "id": "100",
            "type": "alignment_check",
            "by": "section-loop",
            "payload": str(ps / "artifacts" / "test-payload.md"),
        }
        (ps / "artifacts" / "test-payload.md").write_text(
            "# Test\n", encoding="utf-8",
        )

        mock_output = json.dumps({
            "verdict": "REJECT",
            "rationale": "Scope violation detected",
            "violations": ["v1"],
        })

        with patch("dispatch.service.qa_interceptor.dispatch_agent", return_value=mock_output):
            passed, rationale_path, reason_code = intercept_task(
                task, "alignment-judge.md", ps,
            )

        assert passed is False
        assert rationale_path is not None
        assert Path(rationale_path).exists()

        # Verify rationale file contents.
        rationale = json.loads(Path(rationale_path).read_text(encoding="utf-8"))
        assert rationale["task_id"] == "100"
        assert rationale["verdict"] == "REJECT"
        assert rationale["violations"] == ["v1"]
        assert rationale["target_agent"] == "alignment-judge.md"

    def test_dispatch_error_fails_open_with_degraded(self, tmp_path: Path) -> None:
        """Exception during dispatch -> passes with degraded reason_code."""
        from dispatch.service.qa_interceptor import intercept_task

        ps = _setup_planspace(tmp_path)

        task = {
            "id": "101",
            "type": "alignment_check",
            "by": "section-loop",
            "payload": str(ps / "artifacts" / "test-payload.md"),
        }
        (ps / "artifacts" / "test-payload.md").write_text(
            "# Test\n", encoding="utf-8",
        )

        with patch(
            "dispatch.service.qa_interceptor.dispatch_agent",
            side_effect=RuntimeError("agent crashed"),
        ):
            passed, rationale_path, reason_code = intercept_task(
                task, "alignment-judge.md", ps,
            )

        assert passed is True
        assert rationale_path is None
        assert reason_code == "dispatch_error"

    def test_garbage_output_fails_open_with_degraded(self, tmp_path: Path) -> None:
        """Unparseable QA output -> passes with degraded reason_code."""
        from dispatch.service.qa_interceptor import intercept_task

        ps = _setup_planspace(tmp_path)

        task = {
            "id": "102",
            "type": "alignment_check",
            "by": "section-loop",
            "payload": str(ps / "artifacts" / "test-payload.md"),
        }
        (ps / "artifacts" / "test-payload.md").write_text(
            "# Test\n", encoding="utf-8",
        )

        with patch(
            "dispatch.service.qa_interceptor.dispatch_agent",
            return_value="This is not JSON at all",
        ):
            passed, rationale_path, reason_code = intercept_task(
                task, "alignment-judge.md", ps,
            )

        assert passed is True
        assert rationale_path is not None  # degraded writes rationale
        assert reason_code == "unparseable"

    def test_missing_target_agent_fails_open_with_degraded(self, tmp_path: Path) -> None:
        """Missing target agent file -> passes with degraded reason_code."""
        from dispatch.service.qa_interceptor import intercept_task

        ps = _setup_planspace(tmp_path)

        task = {
            "id": "103",
            "type": "test-task",
            "by": "section-loop",
            "payload": str(ps / "artifacts" / "test-payload.md"),
        }
        (ps / "artifacts" / "test-payload.md").write_text(
            "# Test\n", encoding="utf-8",
        )

        with patch("dispatch.service.qa_interceptor.dispatch_agent") as mock_da:
            passed, rationale_path, reason_code = intercept_task(
                task, "nonexistent-agent-xyz.md", ps,
            )

        assert passed is True
        assert rationale_path is None
        assert reason_code == "target_unavailable"
        # dispatch_agent should NOT have been called.
        mock_da.assert_not_called()

    def test_prompt_written_to_qa_intercepts(self, tmp_path: Path) -> None:
        """QA prompt file is written to artifacts/qa-intercepts/."""
        from dispatch.service.qa_interceptor import intercept_task

        ps = _setup_planspace(tmp_path)

        task = {
            "id": "104",
            "type": "alignment_check",
            "by": "section-loop",
            "payload": str(ps / "artifacts" / "test-payload.md"),
        }
        (ps / "artifacts" / "test-payload.md").write_text(
            "# Test payload content\n", encoding="utf-8",
        )

        mock_output = '{"verdict": "PASS", "rationale": "OK"}'
        with patch("dispatch.service.qa_interceptor.dispatch_agent", return_value=mock_output):
            intercept_task(task, "alignment-judge.md", ps)

        prompt_path = ps / "artifacts" / "qa-intercepts" / "qa-104-prompt.md"
        assert prompt_path.exists()
        content = prompt_path.read_text(encoding="utf-8")
        assert "Target Agent Contract" in content
        assert "Test payload content" in content


# ---------------------------------------------------------------------------
# Integration tests: dispatcher + QA interceptor
# ---------------------------------------------------------------------------

class TestDispatcherQaIntegration:
    """Integration tests for QA intercept in task_dispatcher.dispatch_task."""

    def test_qa_disabled_dispatches_normally(self, tmp_path: Path) -> None:
        """When qa_mode is false, task dispatches without QA evaluation."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        # No parameters.json -> qa_mode defaults to False.
        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        import flow.engine.dispatcher as task_dispatcher

        def fake_dispatch(*args, **kwargs):
            return "normal output"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("test-agent.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {
                "id": task_id, "type": "test-task",
                "by": "test-submitter", "payload": str(payload),
            }
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Task should complete normally.
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "complete"

    def test_qa_enabled_pass_dispatches(self, tmp_path: Path) -> None:
        """When qa_mode is true and QA passes, task dispatches normally."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        # Enable QA mode.
        (artifacts / "parameters.json").write_text(
            json.dumps({"qa_mode": True}), encoding="utf-8",
        )

        from dispatch.service import qa_interceptor
        import flow.engine.dispatcher as task_dispatcher

        call_count = {"n": 0}

        def fake_dispatch(*args, **kwargs):
            call_count["n"] += 1
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "qa-interceptor.md":
                # QA agent passes.
                return '{"verdict": "PASS", "rationale": "OK"}'
            return "normal agent output"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(qa_interceptor, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("alignment-judge.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {
                "id": task_id, "type": "test-task",
                "by": "test-submitter", "payload": str(payload),
            }
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Task should complete normally with 2 dispatch calls (QA + actual).
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "complete"
        assert call_count["n"] == 2

    def test_qa_enabled_reject_fails_task(self, tmp_path: Path) -> None:
        """When qa_mode is true and QA rejects, task is failed in DB."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        (artifacts / "parameters.json").write_text(
            json.dumps({"qa_mode": True}), encoding="utf-8",
        )

        from dispatch.service import qa_interceptor
        import flow.engine.dispatcher as task_dispatcher

        def fake_dispatch(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "qa-interceptor.md":
                return json.dumps({
                    "verdict": "REJECT",
                    "rationale": "Contract violation",
                    "violations": ["scope exceeded"],
                })
            return "should not reach here"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(qa_interceptor, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("alignment-judge.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {
                "id": task_id, "type": "test-task",
                "by": "test-submitter", "payload": str(payload),
            }
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Task should be failed.
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, error FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "failed"
        assert "QA interceptor rejected" in (row[1] or "")

    def test_qa_reject_writes_rationale_file(self, tmp_path: Path) -> None:
        """QA rejection creates a rationale file in qa-intercepts."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        (artifacts / "parameters.json").write_text(
            json.dumps({"qa_mode": True}), encoding="utf-8",
        )

        from dispatch.service import qa_interceptor
        import flow.engine.dispatcher as task_dispatcher

        def fake_dispatch(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "qa-interceptor.md":
                return json.dumps({
                    "verdict": "REJECT",
                    "rationale": "Bad task",
                    "violations": ["v1"],
                })
            return ""

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(qa_interceptor, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("alignment-judge.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {
                "id": task_id, "type": "test-task",
                "by": "test-submitter", "payload": str(payload),
            }
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Rationale file should exist.
        rationale_path = (
            artifacts / "qa-intercepts" / f"qa-{task_id}-rationale.json"
        )
        assert rationale_path.exists()
        data = json.loads(rationale_path.read_text(encoding="utf-8"))
        assert data["verdict"] == "REJECT"
        assert data["violations"] == ["v1"]

    def test_qa_error_fails_open(self, tmp_path: Path) -> None:
        """When QA interceptor raises, task still dispatches (fail-open)."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        (artifacts / "parameters.json").write_text(
            json.dumps({"qa_mode": True}), encoding="utf-8",
        )

        from dispatch.service import qa_interceptor
        import flow.engine.dispatcher as task_dispatcher

        call_count = {"n": 0}

        def fake_dispatch(*args, **kwargs):
            call_count["n"] += 1
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "qa-interceptor.md":
                raise RuntimeError("QA agent crashed")
            return "normal output"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(qa_interceptor, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("alignment-judge.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {
                "id": task_id, "type": "test-task",
                "by": "test-submitter", "payload": str(payload),
            }
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Task should still complete (QA failure -> fail-open).
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (int(task_id),),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "complete"

    def test_qa_intercept_event_logged(self, tmp_path: Path) -> None:
        """QA intercept events are logged to the DB events table."""
        ps = _setup_planspace(tmp_path)
        db_path = str(ps / "run.db")
        task_id = _submit_task(db_path)

        artifacts = ps / "artifacts"
        payload = artifacts / "test-payload.md"
        payload.write_text("# Test\n", encoding="utf-8")

        (artifacts / "parameters.json").write_text(
            json.dumps({"qa_mode": True}), encoding="utf-8",
        )

        from dispatch.service import qa_interceptor
        import flow.engine.dispatcher as task_dispatcher

        def fake_dispatch(*args, **kwargs):
            agent_file = kwargs.get("agent_file", "")
            if agent_file == "qa-interceptor.md":
                return '{"verdict": "PASS", "rationale": "OK"}'
            return "output"

        with (
            patch.object(task_dispatcher, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(qa_interceptor, "dispatch_agent", side_effect=fake_dispatch),
            patch.object(task_dispatcher, "resolve_task", return_value=("alignment-judge.md", "test-model")),
            patch.object(task_dispatcher, "reconcile_task_completion"),
            patch.object(task_dispatcher, "validate_dynamic_content", return_value=[]),
        ):
            task = {
                "id": task_id, "type": "test-task",
                "by": "test-submitter", "payload": str(payload),
            }
            task_dispatcher.dispatch_task(db_path, ps, task)

        # Check for lifecycle event in DB.
        result = subprocess.run(
            ["bash", str(DB_SH), "query", db_path, "lifecycle",
             "--tag", f"qa-intercept:{task_id}"],
            capture_output=True, text=True,
        )
        assert f"qa:passed:{task_id}" in result.stdout


# ---------------------------------------------------------------------------
# Unit tests: intercept_dispatch
# ---------------------------------------------------------------------------

class TestInterceptDispatch:
    """Tests for the dispatch-level QA interception entry point."""

    def test_intercept_dispatch_creates_synthetic_task(
        self, tmp_path: Path,
    ) -> None:
        """intercept_dispatch creates a task dict and delegates to intercept_task."""
        from dispatch.service.qa_interceptor import intercept_dispatch

        ps = _setup_planspace(tmp_path)
        prompt = ps / "artifacts" / "test-prompt.md"
        prompt.write_text("# Test prompt\n", encoding="utf-8")

        mock_output = '{"verdict": "PASS", "rationale": "OK"}'
        with patch("dispatch.service.qa_interceptor.dispatch_agent", return_value=mock_output):
            passed, rationale_path, reason_code = intercept_dispatch(
                agent_file="alignment-judge.md",
                prompt_path=prompt,
                planspace=ps,
                submitted_by="section-loop",
            )

        assert passed is True
        assert reason_code is None

    def test_intercept_dispatch_reject_returns_false(
        self, tmp_path: Path,
    ) -> None:
        """intercept_dispatch returns (False, path, None) on REJECT."""
        from dispatch.service.qa_interceptor import intercept_dispatch

        ps = _setup_planspace(tmp_path)
        prompt = ps / "artifacts" / "test-prompt.md"
        prompt.write_text("# Test prompt\n", encoding="utf-8")

        mock_output = json.dumps({
            "verdict": "REJECT",
            "rationale": "Contract violation",
            "violations": ["scope"],
        })
        with patch("dispatch.service.qa_interceptor.dispatch_agent", return_value=mock_output):
            passed, rationale_path, reason_code = intercept_dispatch(
                agent_file="alignment-judge.md",
                prompt_path=prompt,
                planspace=ps,
            )

        assert passed is False
        assert rationale_path is not None
        assert Path(rationale_path).exists()

    def test_intercept_dispatch_missing_agent_fails_open(
        self, tmp_path: Path,
    ) -> None:
        """Missing agent file fails open with target_unavailable reason."""
        from dispatch.service.qa_interceptor import intercept_dispatch

        ps = _setup_planspace(tmp_path)
        prompt = ps / "artifacts" / "test-prompt.md"
        prompt.write_text("# Test\n", encoding="utf-8")

        with patch("dispatch.service.qa_interceptor.dispatch_agent") as mock_da:
            passed, rationale_path, reason_code = intercept_dispatch(
                agent_file="nonexistent-agent-xyz.md",
                prompt_path=prompt,
                planspace=ps,
            )

        assert passed is True
        assert reason_code == "target_unavailable"
        mock_da.assert_not_called()


# ---------------------------------------------------------------------------
# Static validation: agent definition file
# ---------------------------------------------------------------------------

class TestQaAgentDefinition:
    """Validate the QA interceptor agent definition file."""

    def test_agent_file_exists(self) -> None:
        """qa-interceptor.md exists in agents directory."""
        agent_path = WORKFLOW_HOME / "agents" / "qa-interceptor.md"
        assert agent_path.exists(), f"Agent file not found: {agent_path}"

    def test_agent_file_has_frontmatter(self) -> None:
        """qa-interceptor.md has YAML frontmatter with required fields."""
        agent_path = WORKFLOW_HOME / "agents" / "qa-interceptor.md"
        content = agent_path.read_text(encoding="utf-8")
        assert content.startswith("---")
        # Extract frontmatter.
        end = content.index("---", 3)
        frontmatter = content[3:end].strip()
        assert "description:" in frontmatter
        assert "model: claude-opus" in frontmatter
