from __future__ import annotations

import json
from pathlib import Path

import pytest
from dependency_injector import providers

from containers import AgentDispatcher, Services
from src.implementation.engine.loop import run_implementation_loop
from src.orchestrator.types import Section


def _section(planspace: Path) -> Section:
    section = Section(
        number="09",
        path=planspace / "artifacts" / "sections" / "section-09.md",
        related_files=["src/main.py"],
    )
    section.path.parent.mkdir(parents=True, exist_ok=True)
    section.path.write_text("# Section 09\n", encoding="utf-8")
    return section


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    for path in (
        planspace / "artifacts" / "sections",
        planspace / "artifacts" / "signals",
        planspace / "artifacts" / "trace-map",
    ):
        path.mkdir(parents=True, exist_ok=True)
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
        "src.implementation.engine.loop.alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.write_strategic_impl_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "impl-prompt.md",
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.write_impl_alignment_prompt",
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

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return _dispatch(*args, **kwargs)

    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    monkeypatch.setattr(
        "src.implementation.engine.loop.check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop._extract_problems",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.ingest_and_submit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop._write_traceability_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.write_post_impl_assessment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "post-impl-09-prompt.md",
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.submit_chain",
        lambda *_args, **_kwargs: [1],
    )

    try:
        result = run_implementation_loop(
            section,
            planspace,
            codespace,
            "parent",
            {"implementation": "gpt", "alignment": "judge"},
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
        "src.implementation.engine.loop.alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.write_strategic_impl_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "impl-prompt.md",
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.write_impl_alignment_prompt",
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

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return _dispatch(*args, **kwargs)

    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    monkeypatch.setattr(
        "src.implementation.engine.loop.check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop._extract_problems",
        lambda *_args, **_kwargs: next(problems),
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.ingest_and_submit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop._write_traceability_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.write_post_impl_assessment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "post-impl-09-prompt.md",
    )
    monkeypatch.setattr(
        "src.implementation.engine.loop.submit_chain",
        lambda *_args, **_kwargs: [1],
    )

    try:
        result = run_implementation_loop(
            section,
            planspace,
            codespace,
            "parent",
            {"implementation": "gpt", "alignment": "judge"},
            {"proposal_max": 3, "implementation_max": 3},
        )

        assert result == ["src/main.py"]
        assert impl_calls["count"] == 2
    finally:
        Services.dispatcher.reset_override()
