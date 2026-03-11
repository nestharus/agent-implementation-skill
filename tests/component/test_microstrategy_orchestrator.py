from __future__ import annotations

from pathlib import Path

import pytest

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
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    micro_path = planspace / "artifacts" / "proposals" / "section-05-microstrategy.md"
    dispatch_calls: list[str] = []

    monkeypatch.setattr(
        "src.implementation.service.microstrategy._check_needs_microstrategy",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "src.implementation.service.microstrategy.validate_dynamic_content",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "src.implementation.service.microstrategy._log_artifact",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.service.microstrategy.poll_control_messages",
        lambda *_args, **_kwargs: "",
    )

    def _dispatch(model, *_args, **_kwargs):
        dispatch_calls.append(model)
        if len(dispatch_calls) == 2:
            micro_path.write_text("micro", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(
        "src.implementation.service.microstrategy.dispatch_agent",
        _dispatch,
    )
    monkeypatch.setattr(
        "src.implementation.service.microstrategy.ingest_and_submit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.service.microstrategy._record_traceability",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.implementation.service.microstrategy.mailbox_send",
        lambda *_args, **_kwargs: None,
    )

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
