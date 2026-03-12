from __future__ import annotations

from pathlib import Path

import pytest
from dependency_injector import providers

from containers import AgentDispatcher, PromptGuard, Services
from src.implementation.service.microstrategy import run_microstrategy
from src.orchestrator.types import Section

def _section(planspace: Path) -> Section:
    artifacts = planspace / "artifacts"
    section = Section(
        number="05",
        path=artifacts / "sections" / "section-05.md",
        related_files=["src/main.py"],
    )
    section.path.parent.mkdir(parents=True, exist_ok=True)
    section.path.write_text("# Section 05\n", encoding="utf-8")
    return section

@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    for path in (
        planspace / "artifacts" / "sections",
        planspace / "artifacts" / "signals",
        planspace / "artifacts" / "proposals",
        planspace / "artifacts" / "todos",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (planspace / "artifacts" / "proposals" / "section-05-integration-proposal.md").write_text(
        "proposal",
        encoding="utf-8",
    )
    (planspace / "artifacts" / "sections" / "section-05-alignment-excerpt.md").write_text(
        "alignment",
        encoding="utf-8",
    )
    (codespace / "src").mkdir(parents=True, exist_ok=True)
    (codespace / "src" / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    return planspace, codespace

def test_run_microstrategy_returns_none_when_decider_skips(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = env
    section = _section(planspace)

    monkeypatch.setattr(
        "src.implementation.service.microstrategy._check_needs_microstrategy",
        lambda *_args, **_kwargs: False,
    )

    result = run_microstrategy(
        section,
        planspace,
        codespace,
        "parent",
        {"escalation_model": "stronger"},
    )

    assert result is None

def test_run_microstrategy_retries_with_escalation_and_returns_path(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control) -> None:
    planspace, codespace = env
    section = _section(planspace)
    micro_path = planspace / "artifacts" / "proposals" / "section-05-microstrategy.md"
    dispatch_calls: list[str] = []

    monkeypatch.setattr(
        "src.implementation.service.microstrategy._check_needs_microstrategy",
        lambda *_args, **_kwargs: True,
    )

    class _MockGuard(PromptGuard):
        def validate_dynamic(self, content):
            return []
        def write_validated(self, content, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return True

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, model, *_args, **_kwargs):
            dispatch_calls.append(model)
            if len(dispatch_calls) == 2:
                micro_path.write_text("micro", encoding="utf-8")
            return "ok"

    Services.prompt_guard.override(providers.Object(_MockGuard()))
    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    monkeypatch.setattr(
        "src.implementation.service.microstrategy.ingest_and_submit",
        lambda *_args, **_kwargs: None,
    )

    try:
        result = run_microstrategy(
            section,
            planspace,
            codespace,
            "parent",
            {
                "implementation": "primary",
                "microstrategy_decider": "decider",
                "escalation_model": "fallback",
            },
        )

        assert result == micro_path
        assert dispatch_calls == ["primary", "fallback"]
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
