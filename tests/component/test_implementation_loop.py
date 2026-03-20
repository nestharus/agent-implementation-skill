from __future__ import annotations

import json
from pathlib import Path

import pytest
from dependency_injector import providers

from conftest import NoOpFlow, NoOpSectionAlignment, StubPolicies, make_dispatcher
from containers import DispatchHelperService, Services
from implementation.engine.implementation_cycle import ImplementationCycle
from implementation.service.change_verifier import ChangeVerifier
from implementation.service.trace_map_builder import TraceMapBuilder
from implementation.service.traceability_writer import TraceabilityWriter
from proposal.service.cycle_control import CycleControl
from src.orchestrator.path_registry import PathRegistry
from src.orchestrator.types import Section


def _section(planspace: Path) -> Section:
    section = Section(
        number="09",
        path=planspace / "artifacts" / "sections" / "section-09.md",
        related_files=["src/main.py"],
    )
    section.path.write_text("# Section 09\n", encoding="utf-8")
    return section


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    (planspace / "artifacts" / "trace-map").mkdir(parents=True, exist_ok=True)
    (planspace / "artifacts" / "sections" / "section-09-problem-frame.md").write_text(
        "- fix auth\n",
        encoding="utf-8",
    )
    (codespace / "src").mkdir(parents=True, exist_ok=True)
    (codespace / "src" / "main.py").write_text(
        "def main():\n    pass  # TODO[A1]\n",
        encoding="utf-8",
    )
    return planspace, codespace


class _StubPromptWriters:
    """Stub prompt writers that return fixed paths."""

    def __init__(self, planspace: Path) -> None:
        self._planspace = planspace

    def write_strategic_impl_prompt(self, *_args, **_kwargs):
        return self._planspace / "artifacts" / "impl-prompt.md"

    def write_impl_alignment_prompt(self, *_args, **_kwargs):
        return self._planspace / "artifacts" / "impl-align-prompt.md"


class _StubAssessmentEvaluator:
    """Stub assessment evaluator that returns a fixed path."""

    def __init__(self, planspace: Path) -> None:
        self._planspace = planspace

    def write_post_impl_assessment_prompt(self, *_args, **_kwargs):
        return self._planspace / "artifacts" / "post-impl-09-prompt.md"


class _NoopTraceabilityWriter:
    """Stub traceability writer that does nothing."""

    def write_traceability_index(self, *_args, **_kwargs):
        return None


def _make_cycle(planspace: Path) -> ImplementationCycle:
    return ImplementationCycle(
        artifact_io=Services.artifact_io(),
        assessment_evaluator=_StubAssessmentEvaluator(planspace),
        change_verifier=ChangeVerifier(
            logger=Services.logger(),
            section_alignment=Services.section_alignment(),
            staleness=Services.staleness(),
        ),
        communicator=Services.communicator(),
        cycle_control=CycleControl(
            logger=Services.logger(),
            artifact_io=Services.artifact_io(),
            communicator=Services.communicator(),
            pipeline_control=Services.pipeline_control(),
            cross_section=Services.cross_section(),
            dispatcher=Services.dispatcher(),
            dispatch_helpers=Services.dispatch_helpers(),
            task_router=Services.task_router(),
            flow_ingestion=Services.flow_ingestion(),
        ),
        dispatcher=Services.dispatcher(),
        dispatch_helpers=Services.dispatch_helpers(),
        flow_ingestion=Services.flow_ingestion(),
        logger=Services.logger(),
        pipeline_control=Services.pipeline_control(),
        policies=Services.policies(),
        section_alignment=Services.section_alignment(),
        staleness=Services.staleness(),
        task_router=Services.task_router(),
        prompt_writers=_StubPromptWriters(planspace),
        trace_map_builder=TraceMapBuilder(
            artifact_io=Services.artifact_io(),
            hasher=Services.hasher(),
            logger=Services.logger(),
        ),
        traceability_writer=_NoopTraceabilityWriter(),
    )


def test_run_implementation_loop_returns_changed_files_and_trace_map(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    impl_modified = planspace / "artifacts" / "impl-09-modified.txt"

    def _dispatch(*args, **kwargs):
        if kwargs.get("agent_file") == "implementation-strategist.md":
            (codespace / "src" / "main.py").write_text(
                "def main():\n    return 1  # TODO[A1]\n",
                encoding="utf-8",
            )
            impl_modified.write_text(str(codespace / "src" / "main.py"), encoding="utf-8")
            return "implementation output"
        return "alignment output"

    class _NoopHelpers(DispatchHelperService):
        def check_agent_signals(self, *_args, **_kwargs):
            return (None, "")

    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))
    Services.dispatch_helpers.override(providers.Object(_NoopHelpers()))
    Services.flow_ingestion.override(providers.Object(NoOpFlow()))
    Services.section_alignment.override(providers.Object(NoOpSectionAlignment()))
    Services.policies.override(providers.Object(StubPolicies()))

    try:
        cycle = _make_cycle(planspace)
        result = cycle.run_implementation_loop(
            section,
            planspace,
            codespace,
        )

        trace_map = json.loads(
            (planspace / "artifacts" / "trace-map" / "section-09.json").read_text(
                encoding="utf-8"
            )
        )
        assert result == ["src/main.py"]
        assert trace_map["files"] == ["src/main.py"]
        assert trace_map["todo_ids"] == [{"id": "A1", "file": "src/main.py"}]
    finally:
        Services.dispatcher.reset_override()
        Services.dispatch_helpers.reset_override()
        Services.flow_ingestion.reset_override()
        Services.section_alignment.reset_override()
        Services.policies.reset_override()


def test_run_implementation_loop_returns_result_on_alignment_problems(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control,
) -> None:
    """Single-shot: returns changed files even when misaligned.

    The state machine handles retry via IMPL_ASSESSING -> IMPLEMENTING.
    """
    planspace, codespace = env
    section = _section(planspace)
    impl_modified = planspace / "artifacts" / "impl-09-modified.txt"
    impl_calls = {"count": 0}

    def _dispatch(*args, **kwargs):
        if kwargs.get("agent_file") == "implementation-strategist.md":
            impl_calls["count"] += 1
            (codespace / "src" / "main.py").write_text(
                f"def main():\n    return {impl_calls['count']}  # TODO[A1]\n",
                encoding="utf-8",
            )
            impl_modified.write_text(str(codespace / "src" / "main.py"), encoding="utf-8")
            return "implementation output"
        return "alignment output"

    class _NoopHelpers(DispatchHelperService):
        def check_agent_signals(self, *_args, **_kwargs):
            return (None, "")

    sa = NoOpSectionAlignment()
    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))
    Services.dispatch_helpers.override(providers.Object(_NoopHelpers()))
    Services.flow_ingestion.override(providers.Object(NoOpFlow()))
    Services.section_alignment.override(providers.Object(sa))
    Services.policies.override(providers.Object(StubPolicies()))
    monkeypatch.setattr(
        sa, "extract_problems",
        lambda *_args, **_kwargs: "fix edge case",
    )

    try:
        cycle = _make_cycle(planspace)
        result = cycle.run_implementation_loop(
            section,
            planspace,
            codespace,
        )

        # Single-shot: exactly one implementation dispatch
        assert result == ["src/main.py"]
        assert impl_calls["count"] == 1
    finally:
        Services.dispatcher.reset_override()
        Services.dispatch_helpers.reset_override()
        Services.flow_ingestion.reset_override()
        Services.section_alignment.reset_override()
        Services.policies.reset_override()
