"""Tests for the pipeline engine, context, and middleware."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from pipeline.context import PipelineContext
from pipeline.engine import HALT, Pipeline, Step
from pipeline.middleware import AlignmentGuard, StepLogger


def _make_ctx(tmp_path: Path) -> PipelineContext:
    section = MagicMock()
    section.number = "03"
    section.related_files = []
    return PipelineContext(
        section=section,
        planspace=tmp_path / "planspace",
        codespace=tmp_path / "codespace",
        parent="parent",
        policy={},
        paths=MagicMock(),
    )


class TestPipeline:
    def test_runs_all_steps_in_order(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        order: list[str] = []

        def step_a(c: PipelineContext) -> str:
            order.append("a")
            return "ok"

        def step_b(c: PipelineContext) -> str:
            order.append("b")
            c.state["result"] = "final"
            return "ok"

        pipe = Pipeline("test", [Step("a", step_a), Step("b", step_b)])
        result = pipe.run(ctx)
        assert order == ["a", "b"]
        assert result == "final"

    def test_halts_on_none(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        order: list[str] = []

        def step_a(c: PipelineContext) -> None:
            order.append("a")
            return None

        def step_b(c: PipelineContext) -> str:
            order.append("b")
            return "ok"

        pipe = Pipeline("test", [Step("a", step_a), Step("b", step_b)])
        result = pipe.run(ctx)
        assert order == ["a"]
        assert result is None

    def test_halts_on_halt_sentinel(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)

        def step_a(c: PipelineContext):
            return HALT

        def step_b(c: PipelineContext) -> str:
            return "should not run"

        pipe = Pipeline("test", [Step("a", step_a), Step("b", step_b)])
        assert pipe.run(ctx) is None

    def test_guard_skips_step(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        order: list[str] = []

        def step_a(c: PipelineContext) -> str:
            order.append("a")
            return "ok"

        def step_b(c: PipelineContext) -> str:
            order.append("b")
            return "ok"

        pipe = Pipeline("test", [
            Step("a", step_a, guard=lambda c: False),
            Step("b", step_b),
        ])
        pipe.run(ctx)
        assert order == ["b"]

    def test_returns_state_dict_when_no_result_key(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)

        def step(c: PipelineContext) -> str:
            c.state["foo"] = "bar"
            return "ok"

        pipe = Pipeline("test", [Step("a", step)])
        result = pipe.run(ctx)
        assert result == {"foo": "bar"}


class TestAlignmentGuard:
    def test_halts_when_alignment_changed(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)

        def step(c: PipelineContext) -> str:
            return "should not run"

        guard = AlignmentGuard(lambda planspace: True)
        pipe = Pipeline("test", [Step("a", step)], middleware=[guard])
        assert pipe.run(ctx) is None

    def test_after_steps_checks_only_named_steps(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        order: list[str] = []
        check_calls: list[str] = []

        def step_a(c: PipelineContext) -> str:
            order.append("a")
            return "ok"

        def step_b(c: PipelineContext) -> str:
            order.append("b")
            return "ok"

        def step_c(c: PipelineContext) -> str:
            order.append("c")
            return "should not reach"

        def check_fn(planspace):
            check_calls.append("checked")
            return True  # always fires

        # Only check after step "b"
        guard = AlignmentGuard(check_fn, after_steps={"b"})
        pipe = Pipeline("test", [
            Step("a", step_a),
            Step("b", step_b),
            Step("c", step_c),
        ], middleware=[guard])
        result = pipe.run(ctx)

        assert result is None  # halted after step b
        assert order == ["a", "b"]  # a and b ran, c did not
        assert len(check_calls) == 1  # only checked once (after b)

    def test_after_steps_does_not_check_before(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)

        def step(c: PipelineContext) -> str:
            c.state["result"] = "ran"
            return "ok"

        # after_steps set but step name not in the set — no check at all
        guard = AlignmentGuard(lambda p: True, after_steps={"other"})
        pipe = Pipeline("test", [Step("a", step)], middleware=[guard])
        result = pipe.run(ctx)
        assert result == "ran"  # step ran, no halt

    def test_continues_when_no_change(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)

        def step(c: PipelineContext) -> str:
            c.state["result"] = "done"
            return "ok"

        guard = AlignmentGuard(lambda planspace: False)
        pipe = Pipeline("test", [Step("a", step)], middleware=[guard])
        assert pipe.run(ctx) == "done"


class TestStepLogger:
    def test_logs_before_and_after(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        messages: list[str] = []

        def step(c: PipelineContext) -> str:
            return "ok"

        logger = StepLogger(messages.append)
        pipe = Pipeline("test", [Step("my-step", step)], middleware=[logger])
        pipe.run(ctx)
        assert len(messages) == 2
        assert "starting" in messages[0]
        assert "my-step" in messages[0]
        assert "done" in messages[1]


class TestPipelineContext:
    def test_for_section_creates_context(self, tmp_path: Path) -> None:
        section = MagicMock()
        section.number = "01"
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        codespace = tmp_path / "code"

        ctx = PipelineContext(
            section=section,
            planspace=planspace,
            codespace=codespace,
            parent="parent",
            policy={"key": "val"},
            paths=MagicMock(),
        )
        assert ctx.section.number == "01"
        assert ctx.policy == {"key": "val"}
        assert ctx.state == {}

    def test_state_is_mutable(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.state["foo"] = 1
        ctx.state["foo"] += 1
        assert ctx.state["foo"] == 2
