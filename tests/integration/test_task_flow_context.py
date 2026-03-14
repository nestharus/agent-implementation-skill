"""Tests for flow context threading into dispatched tasks (Task 5).

Covers:
- build_flow_context: reading and enriching flow context for dispatch
- write_dispatch_prompt: creating wrapper prompts without mutating originals
- task_dispatcher flow context integration: wrapper prompt is used for dispatch
- context_assembly flow_context category: resolves flow context JSON
- Non-flow tasks work unchanged (no regression)
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from _paths import DB_SH
from conftest import override_dispatcher_and_guard

from flow.types.schema import TaskSpec
from flow.exceptions import FlowCorruptionError
from flow.service.flow_facade import (
    build_flow_context,
    submit_chain,
    submit_fanout,
    write_dispatch_prompt,
)
from flow.types.schema import BranchSpec, GateSpec


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


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create and initialize a test database."""
    p = tmp_path / "test.db"
    _init_db(p)
    return p


@pytest.fixture()
def planspace(tmp_path: Path) -> Path:
    """Create a planspace directory for flow context files."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    (ps / "artifacts" / "flows").mkdir(parents=True)
    return ps


# ---------------------------------------------------------------------------
# build_flow_context
# ---------------------------------------------------------------------------

class TestBuildFlowContext:
    """Tests for build_flow_context()."""

    def test_returns_none_without_flow_context_path(
        self, planspace: Path,
    ) -> None:
        """No flow_context_path means no context to build."""
        result = build_flow_context(planspace, 1, flow_context_path=None)
        assert result is None

    def test_raises_on_missing_file(
        self, planspace: Path,
    ) -> None:
        """Missing flow context file raises FlowCorruptionError."""
        with pytest.raises(FlowCorruptionError, match="missing"):
            build_flow_context(
                planspace, 1,
                flow_context_path="artifacts/flows/task-999-context.json",
            )

    def test_reads_existing_flow_context(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """build_flow_context reads the JSON file and returns its contents."""
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="staleness.alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        task = _query_task(db_path, tid)

        result = build_flow_context(
            planspace, tid,
            flow_context_path=task["flow_context_path"],
        )

        assert result is not None
        assert result.task.task_id == tid
        assert result.task.task_type == "staleness.alignment_check"
        assert result.continuation_path is not None
        assert result.result_manifest_path is not None

    def test_chain_predecessor_result_available(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Second task in a chain can discover predecessor result manifest."""
        ids = submit_chain(
            db_path, "test-agent",
            [
                TaskSpec(task_type="staleness.alignment_check"),
                TaskSpec(task_type="signals.impact_analysis"),
            ],
            planspace=planspace,
        )
        second_task = _query_task(db_path, ids[1])

        result = build_flow_context(
            planspace, ids[1],
            flow_context_path=second_task["flow_context_path"],
        )

        assert result is not None
        assert result.previous_result_manifest is not None
        assert f"task-{ids[0]}-result.json" in result.previous_result_manifest

    def test_enriches_continuation_path(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """build_flow_context fills in continuation_path from DB row."""
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="staleness.alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        task = _query_task(db_path, tid)

        result = build_flow_context(
            planspace, tid,
            flow_context_path=task["flow_context_path"],
            continuation_path=task["continuation_path"],
        )

        assert result is not None
        assert result.continuation_path == task["continuation_path"]

    def test_enriches_gate_aggregate_for_synthesis(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Synthesis task flow context includes gate aggregate manifest."""
        # Create a gate aggregate file at the expected path.
        gate_id = "gate_test_123"
        agg_relpath = f"artifacts/flows/{gate_id}-aggregate.json"
        agg_file = planspace / agg_relpath
        agg_file.parent.mkdir(parents=True, exist_ok=True)
        agg_file.write_text(json.dumps({
            "gate_id": gate_id,
            "members": [],
        }))

        # Create a flow context file for a synthesis task.
        ctx_relpath = "artifacts/flows/task-42-context.json"
        ctx_file = planspace / ctx_relpath
        ctx_file.write_text(json.dumps({
            "task": {
                "task_id": 42,
                "trigger_gate_id": gate_id,
            },
            "gate_aggregate_manifest": None,
            "continuation_path": "artifacts/flows/task-42-continuation.json",
        }))

        result = build_flow_context(
            planspace, 42,
            flow_context_path=ctx_relpath,
            trigger_gate_id=gate_id,
        )

        assert result is not None
        assert result.gate_aggregate_manifest == agg_relpath

    def test_raises_on_malformed_json(
        self, planspace: Path,
    ) -> None:
        """Malformed JSON in the context file raises FlowCorruptionError."""
        ctx_relpath = "artifacts/flows/task-99-context.json"
        ctx_file = planspace / ctx_relpath
        ctx_file.write_text("{broken json not valid")

        with pytest.raises(FlowCorruptionError, match="corrupt"):
            build_flow_context(
                planspace, 99,
                flow_context_path=ctx_relpath,
            )
        # Original file should be renamed to .malformed.json
        assert not ctx_file.exists()
        assert ctx_file.with_suffix(".malformed.json").exists()


# ---------------------------------------------------------------------------
# write_dispatch_prompt
# ---------------------------------------------------------------------------

class TestWriteDispatchPrompt:
    """Tests for write_dispatch_prompt()."""

    def test_creates_wrapper_at_deterministic_path(
        self, planspace: Path, tmp_path: Path,
    ) -> None:
        """Wrapper prompt is written to artifacts/flows/task-{id}-dispatch.md."""
        original = tmp_path / "original-prompt.md"
        original.write_text("# Original task\n\nDo stuff.\n")

        result = write_dispatch_prompt(
            planspace, 42, original,
            flow_context_path="artifacts/flows/task-42-context.json",
            continuation_path="artifacts/flows/task-42-continuation.json",
        )

        expected = planspace / "artifacts" / "flows" / "task-42-dispatch.md"
        assert result == expected
        assert expected.exists()

    def test_wrapper_contains_flow_context_block(
        self, planspace: Path, tmp_path: Path,
    ) -> None:
        """Wrapper prompt includes <flow-context> with paths."""
        original = tmp_path / "prompt.md"
        original.write_text("# My task\n")

        ctx_path = "artifacts/flows/task-7-context.json"
        cont_path = "artifacts/flows/task-7-continuation.json"

        result = write_dispatch_prompt(
            planspace, 7, original,
            flow_context_path=ctx_path,
            continuation_path=cont_path,
        )

        content = result.read_text(encoding="utf-8")
        assert "<flow-context>" in content
        assert "</flow-context>" in content
        assert ctx_path in content
        assert cont_path in content

    def test_wrapper_includes_original_content(
        self, planspace: Path, tmp_path: Path,
    ) -> None:
        """Wrapper prompt includes the original prompt content verbatim."""
        original_text = "# Task: alignment_check\n\nDo something important.\n"
        original = tmp_path / "prompt.md"
        original.write_text(original_text)

        result = write_dispatch_prompt(
            planspace, 5, original,
            flow_context_path="artifacts/flows/task-5-context.json",
        )

        content = result.read_text(encoding="utf-8")
        assert original_text in content

    def test_original_prompt_not_mutated(
        self, planspace: Path, tmp_path: Path,
    ) -> None:
        """The original prompt file is NOT modified."""
        original_text = "# Original\n\nUntouched.\n"
        original = tmp_path / "prompt.md"
        original.write_text(original_text)

        write_dispatch_prompt(
            planspace, 10, original,
            flow_context_path="artifacts/flows/task-10-context.json",
        )

        assert original.read_text() == original_text

    def test_wrapper_without_continuation_path(
        self, planspace: Path, tmp_path: Path,
    ) -> None:
        """Wrapper prompt works without a continuation path."""
        original = tmp_path / "prompt.md"
        original.write_text("# Task\n")

        result = write_dispatch_prompt(
            planspace, 3, original,
            flow_context_path="artifacts/flows/task-3-context.json",
            continuation_path=None,
        )

        content = result.read_text(encoding="utf-8")
        assert "<flow-context>" in content
        assert "task-3-context.json" in content
        assert "follow-up task declarations" not in content

    def test_wrapper_with_missing_original(
        self, planspace: Path, tmp_path: Path,
    ) -> None:
        """Wrapper prompt handles missing original gracefully."""
        nonexistent = tmp_path / "does-not-exist.md"

        result = write_dispatch_prompt(
            planspace, 99, nonexistent,
            flow_context_path="artifacts/flows/task-99-context.json",
        )

        content = result.read_text(encoding="utf-8")
        assert "<flow-context>" in content
        # Original content is empty but the file still exists.
        assert result.exists()


# ---------------------------------------------------------------------------
# task_dispatcher integration
# ---------------------------------------------------------------------------

class TestDispatcherFlowIntegration:
    """Verify task_dispatcher.dispatch_task threads flow context."""

    def test_non_flow_task_dispatches_unchanged(
        self, db_path: Path, planspace: Path, tmp_path: Path,
    ) -> None:
        """A task with no flow_context dispatches using the original prompt."""
        # Create a payload prompt.
        prompt = planspace / "artifacts" / "test-prompt.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("# Test\n\nDo the thing.\n")

        task = {
            "id": "1",
            "type": "staleness.alignment_check",
            "by": "test-agent",
            "prio": "normal",
            "payload": str(prompt),
        }

        from flow.engine import task_dispatcher as task_dispatcher

        captured: dict = {}

        def fake_dispatch(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return "done"

        with override_dispatcher_and_guard(fake_dispatch), \
             patch.object(task_dispatcher._task_registry, "resolve") as mock_resolve, \
             patch("flow.engine.task_dispatcher._db_claim_task"), \
             patch("flow.engine.task_dispatcher._db_complete_task") as mock_db, \
             patch("flow.engine.task_dispatcher.notify_task_result"):
            mock_resolve.return_value = ("alignment-judge.md", "glm")

            task_dispatcher.dispatch_task(str(db_path), planspace, task)

            # dispatch should be called with the original prompt path
            assert "args" in captured
            dispatched_prompt = captured["args"][1]  # second positional arg
            assert dispatched_prompt == prompt

    def test_flow_task_dispatches_with_wrapper(
        self, db_path: Path, planspace: Path, tmp_path: Path,
    ) -> None:
        """A task with flow_context dispatches using a wrapper prompt."""
        # Create flow context file.
        ctx_relpath = "artifacts/flows/task-1-context.json"
        ctx_file = planspace / ctx_relpath
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text(json.dumps({
            "task": {"task_id": 1, "task_type": "signals.impact_analysis"},
            "previous_result_manifest": "artifacts/flows/task-0-result.json",
            "continuation_path": "artifacts/flows/task-1-continuation.json",
        }))

        # Create a payload prompt.
        prompt = planspace / "artifacts" / "test-prompt.md"
        prompt.write_text("# Impact Analysis\n\nAnalyze impact.\n")

        task = {
            "id": "1",
            "type": "signals.impact_analysis",
            "by": "test-agent",
            "prio": "normal",
            "payload": str(prompt),
            "flow_context": ctx_relpath,
            "continuation": "artifacts/flows/task-1-continuation.json",
        }

        from flow.engine import task_dispatcher as task_dispatcher

        captured: dict = {}

        def fake_dispatch(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return "done"

        with override_dispatcher_and_guard(fake_dispatch), \
             patch.object(task_dispatcher._task_registry, "resolve") as mock_resolve, \
             patch("flow.engine.task_dispatcher._db_claim_task"), \
             patch("flow.engine.task_dispatcher._db_complete_task") as mock_db, \
             patch("flow.engine.task_dispatcher.notify_task_result"):
            mock_resolve.return_value = ("impact-analyzer.md", "glm")

            task_dispatcher.dispatch_task(str(db_path), planspace, task)

            # dispatch should be called with a wrapper prompt
            assert "args" in captured
            dispatched_prompt = captured["args"][1]
            # The wrapper prompt should be a different file
            assert dispatched_prompt != prompt
            assert "dispatch.md" in dispatched_prompt.name

            # Read the wrapper and verify it has flow context + original
            wrapper_content = dispatched_prompt.read_text()
            assert "<flow-context>" in wrapper_content
            assert ctx_relpath in wrapper_content
            assert "Impact Analysis" in wrapper_content

    def test_flow_task_original_prompt_preserved(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Flow-dispatched task does not mutate the original payload prompt."""
        # Create flow context file.
        ctx_relpath = "artifacts/flows/task-2-context.json"
        ctx_file = planspace / ctx_relpath
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text(json.dumps({
            "task": {"task_id": 2},
            "continuation_path": "artifacts/flows/task-2-continuation.json",
        }))

        original_text = "# Original prompt content\n\nNot modified.\n"
        prompt = planspace / "artifacts" / "original.md"
        prompt.write_text(original_text)

        task = {
            "id": "2",
            "type": "staleness.alignment_check",
            "by": "test-agent",
            "prio": "normal",
            "payload": str(prompt),
            "flow_context": ctx_relpath,
            "continuation": "artifacts/flows/task-2-continuation.json",
        }

        from flow.engine import task_dispatcher as task_dispatcher

        with override_dispatcher_and_guard(lambda *a, **kw: "done"), \
             patch.object(task_dispatcher._task_registry, "resolve") as mock_resolve, \
             patch("flow.engine.task_dispatcher._db_claim_task"), \
             patch("flow.engine.task_dispatcher._db_complete_task"), \
             patch("flow.engine.task_dispatcher.notify_task_result"):
            mock_resolve.return_value = ("alignment-judge.md", "glm")

            task_dispatcher.dispatch_task(str(db_path), planspace, task)

        # Original file must be unchanged.
        assert prompt.read_text() == original_text


# ---------------------------------------------------------------------------
# context_assembly flow_context category
# ---------------------------------------------------------------------------

class TestContextAssemblyFlowContext:
    """Verify flow_context is a valid context category."""

    def test_flow_context_in_valid_categories(self) -> None:
        from dispatch.service.context_sidecar import VALID_CATEGORIES
        assert "flow_context" in VALID_CATEGORIES

    def test_flow_context_resolver_returns_empty_without_files(
        self, planspace: Path,
    ) -> None:
        """No flow context files -> empty string."""
        from dispatch.service.context_sidecar import _resolve_flow_context
        result = _resolve_flow_context(planspace, None)
        assert result == ""

    def test_flow_context_resolver_returns_content_for_single_file(
        self, planspace: Path,
    ) -> None:
        """Single flow context file -> returns its content."""
        from dispatch.service.context_sidecar import _resolve_flow_context

        flows_dir = planspace / "artifacts" / "flows"
        flows_dir.mkdir(parents=True, exist_ok=True)

        ctx_data = {"task": {"task_id": 5}, "origin_refs": ["ref-1"]}
        (flows_dir / "task-5-context.json").write_text(
            json.dumps(ctx_data)
        )

        result = _resolve_flow_context(planspace, None)
        assert result != ""
        parsed = json.loads(result)
        assert parsed["task"]["task_id"] == 5

    def test_flow_context_resolver_returns_empty_for_multiple_files(
        self, planspace: Path,
    ) -> None:
        """Multiple flow context files -> empty (ambiguous)."""
        from dispatch.service.context_sidecar import _resolve_flow_context

        flows_dir = planspace / "artifacts" / "flows"
        flows_dir.mkdir(parents=True, exist_ok=True)

        (flows_dir / "task-1-context.json").write_text('{"task": {"task_id": 1}}')
        (flows_dir / "task-2-context.json").write_text('{"task": {"task_id": 2}}')

        result = _resolve_flow_context(planspace, None)
        assert result == ""

    def test_flow_context_in_resolve_context(
        self, planspace: Path, tmp_path: Path,
    ) -> None:
        """An agent file declaring flow_context gets it resolved."""
        from dispatch.service.context_sidecar import resolve_context

        # Create an agent file with flow_context in its context list.
        agent_file = tmp_path / "test-agent.md"
        agent_file.write_text(
            "---\ncontext:\n  - flow_context\n---\n\n# Test Agent\n"
        )

        # Create a single flow context file.
        flows_dir = planspace / "artifacts" / "flows"
        flows_dir.mkdir(parents=True, exist_ok=True)
        ctx_data = {"task": {"task_id": 7}}
        (flows_dir / "task-7-context.json").write_text(json.dumps(ctx_data))

        result = resolve_context(str(agent_file), planspace)
        assert "flow_context" in result
        parsed = json.loads(result["flow_context"])
        assert parsed["task"]["task_id"] == 7


# ---------------------------------------------------------------------------
# End-to-end: submit chain + verify context readable
# ---------------------------------------------------------------------------

class TestEndToEndFlowContext:
    """End-to-end tests: submit flow, verify context is readable."""

    def test_chain_task_flow_context_is_readable(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """A chain task's flow context can be read via build_flow_context."""
        ids = submit_chain(
            db_path, "test-agent",
            [
                TaskSpec(task_type="staleness.alignment_check"),
                TaskSpec(task_type="signals.impact_analysis"),
            ],
            planspace=planspace,
            origin_refs=["section-01"],
        )

        # Second task should be able to discover predecessor
        task2 = _query_task(db_path, ids[1])
        ctx = build_flow_context(
            planspace, ids[1],
            flow_context_path=task2["flow_context_path"],
            continuation_path=task2["continuation_path"],
        )

        assert ctx is not None
        assert ctx.task.task_id == ids[1]
        assert ctx.previous_result_manifest is not None
        assert f"task-{ids[0]}-result.json" in ctx.previous_result_manifest
        assert ctx.origin_refs == ["section-01"]
        assert ctx.continuation_path is not None

    def test_dispatch_prompt_for_chain_task(
        self, db_path: Path, planspace: Path, tmp_path: Path,
    ) -> None:
        """Chain task gets a wrapper prompt with flow context paths."""
        ids = submit_chain(
            db_path, "test-agent",
            [
                TaskSpec(task_type="staleness.alignment_check"),
                TaskSpec(task_type="signals.impact_analysis"),
            ],
            planspace=planspace,
        )

        task2 = _query_task(db_path, ids[1])
        original = tmp_path / "original.md"
        original.write_text("# Impact Analysis\n\nDo analysis.\n")

        wrapper = write_dispatch_prompt(
            planspace, ids[1], original,
            flow_context_path=task2["flow_context_path"],
            continuation_path=task2["continuation_path"],
        )

        content = wrapper.read_text()
        # Has flow context header
        assert "<flow-context>" in content
        assert task2["flow_context_path"] in content
        assert task2["continuation_path"] in content
        # Has original content
        assert "Impact Analysis" in content
        assert "Do analysis." in content
        # Original is untouched
        assert original.read_text() == "# Impact Analysis\n\nDo analysis.\n"

    def test_synthesis_task_flow_context_has_gate_aggregate(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Synthesis task can discover its gate aggregate manifest."""
        from flow.service.flow_facade import reconcile_task_completion

        # Create a fanout with a synthesis gate.
        branches = [
            BranchSpec(
                label="only",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_syn_ctx",
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(
                    task_type="signals.impact_analysis",
                    problem_id="P-syn",
                ),
            ),
            planspace=planspace,
        )

        # Complete the branch task to fire the gate.
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks ORDER BY id")
        tasks = [dict(r) for r in cur.fetchall()]
        conn.close()

        branch_task = tasks[0]
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "UPDATE tasks SET status='running', claimed_by='test' WHERE id=?",
            (branch_task["id"],),
        )
        conn.execute(
            "UPDATE tasks SET status='complete', completed_at=datetime('now') WHERE id=?",
            (branch_task["id"],),
        )
        conn.commit()
        conn.close()

        reconcile_task_completion(
            db_path, planspace, branch_task["id"],
            "complete", "artifacts/output.md",
        )

        # Find the synthesis task.
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tasks WHERE trigger_gate_id = ?", (gate_id,)
        )
        syn_row = cur.fetchone()
        conn.close()

        assert syn_row is not None
        syn_task = dict(syn_row)

        # Build flow context for the synthesis task.
        ctx = build_flow_context(
            planspace, syn_task["id"],
            flow_context_path=syn_task["flow_context_path"],
            trigger_gate_id=syn_task["trigger_gate_id"],
        )

        assert ctx is not None
        assert ctx.gate_aggregate_manifest is not None
        assert gate_id in ctx.gate_aggregate_manifest
