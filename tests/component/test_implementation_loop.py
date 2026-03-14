from __future__ import annotations

import json
from pathlib import Path

import pytest
from dependency_injector import providers

from conftest import NoOpFlow, NoOpSectionAlignment, StubPolicies, make_dispatcher
from containers import DispatchHelperService, Services
from src.implementation.engine.implementation_cycle import run_implementation_loop
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


def test_run_implementation_loop_returns_changed_files_and_trace_map(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    impl_modified = planspace / "artifacts" / "impl-09-modified.txt"

    monkeypatch.setattr(
        "src.implementation.engine.implementation_cycle.write_strategic_impl_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "impl-prompt.md",
    )
    monkeypatch.setattr(
        "src.implementation.engine.implementation_cycle.write_impl_alignment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "impl-align-prompt.md",
    )

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
    monkeypatch.setattr(
        "src.implementation.engine.implementation_cycle.write_traceability_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.engine.implementation_cycle.write_post_impl_assessment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "post-impl-09-prompt.md",
    )

    try:
        result = run_implementation_loop(
            section,
            planspace,
            codespace,
            {"proposal_max": 3, "implementation_max": 3},
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


def test_run_implementation_loop_retries_after_alignment_problems(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    impl_modified = planspace / "artifacts" / "impl-09-modified.txt"
    problems = iter(["fix edge case", None])
    impl_calls = {"count": 0}

    monkeypatch.setattr(
        "src.implementation.engine.implementation_cycle.write_strategic_impl_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "impl-prompt.md",
    )
    monkeypatch.setattr(
        "src.implementation.engine.implementation_cycle.write_impl_alignment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "impl-align-prompt.md",
    )

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
        lambda *_args, **_kwargs: next(problems),
    )
    monkeypatch.setattr(
        "src.implementation.engine.implementation_cycle.write_traceability_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.engine.implementation_cycle.write_post_impl_assessment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "post-impl-09-prompt.md",
    )

    try:
        result = run_implementation_loop(
            section,
            planspace,
            codespace,
            {"proposal_max": 3, "implementation_max": 3},
        )

        assert result == ["src/main.py"]
        assert impl_calls["count"] == 2
    finally:
        Services.dispatcher.reset_override()
        Services.dispatch_helpers.reset_override()
        Services.flow_ingestion.reset_override()
        Services.section_alignment.reset_override()
        Services.policies.reset_override()
