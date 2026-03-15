from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dependency_injector import providers

from conftest import make_dispatcher, WritingGuard
from containers import CrossSectionService, Services
from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section
from proposal.service.excerpt_extractor import ExcerptExtractor
from proposal.service.cycle_control import CycleControl


def _section(planspace: Path) -> Section:
    artifacts = planspace / "artifacts"
    section = Section(
        number="01",
        path=artifacts / "sections" / "section-01.md",
        global_proposal_path=artifacts / "global-proposal.md",
        global_alignment_path=artifacts / "global-alignment.md",
    )
    section.path.write_text("# Section 01\n", encoding="utf-8")
    section.global_proposal_path.write_text("# Global Proposal\n", encoding="utf-8")
    section.global_alignment_path.write_text("# Global Alignment\n", encoding="utf-8")
    return section

def _write_excerpts(planspace: Path, section_number: str) -> None:
    sections_dir = planspace / "artifacts" / "sections"
    (sections_dir / f"section-{section_number}-proposal-excerpt.md").write_text(
        "proposal",
        encoding="utf-8",
    )
    (sections_dir / f"section-{section_number}-alignment-excerpt.md").write_text(
        "alignment",
        encoding="utf-8",
    )

def _make_cycle_control() -> CycleControl:
    return CycleControl(
        logger=Services.logger(),
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        pipeline_control=Services.pipeline_control(),
        cross_section=Services.cross_section(),
        dispatcher=Services.dispatcher(),
        dispatch_helpers=Services.dispatch_helpers(),
        task_router=Services.task_router(),
        flow_ingestion=Services.flow_ingestion(),
    )


def _make_extractor(prompt_writers=None) -> ExcerptExtractor:
    return ExcerptExtractor(
        logger=Services.logger(),
        policies=Services.policies(),
        dispatcher=Services.dispatcher(),
        dispatch_helpers=Services.dispatch_helpers(),
        communicator=Services.communicator(),
        pipeline_control=Services.pipeline_control(),
        task_router=Services.task_router(),
        cycle_control=_make_cycle_control(),
        prompt_writers=prompt_writers or MagicMock(),
    )


@pytest.fixture()
def base_dirs(tmp_path: Path) -> tuple[Path, Path]:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    codespace.mkdir()
    return planspace, codespace

def test_extract_excerpts_returns_ok_when_setup_creates_files(
    base_dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator) -> None:
    planspace, codespace = base_dirs
    section = _section(planspace)
    prompt_path = planspace / "artifacts" / "setup-prompt.md"

    mock_pw = MagicMock()
    mock_pw.write_section_setup_prompt.return_value = prompt_path

    def _dispatch(*_args, **_kwargs):
        _write_excerpts(planspace, section.number)
        return "ok"

    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )

    try:
        result = _make_extractor(prompt_writers=mock_pw).extract_excerpts(
            section,
            planspace,
            codespace,
        )

        assert result == "ok"
    finally:
        Services.dispatcher.reset_override()

def test_extract_excerpts_routes_out_of_scope_then_retries(
    base_dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    capturing_pipeline_control) -> None:
    planspace, codespace = base_dirs
    section = _section(planspace)
    signal_path = planspace / "artifacts" / "signals" / "setup-01-signal.json"
    signal_path.write_text(json.dumps({"state": "out_of_scope", "detail": "needs root"}))
    persisted: list[str] = []

    mock_pw = MagicMock()
    mock_pw.write_section_setup_prompt.return_value = planspace / "artifacts" / "setup-prompt.md"

    calls = {"count": 0}

    def _dispatch_fn(*_args, **_kwargs) -> str:
        calls["count"] += 1
        if calls["count"] == 2:
            _write_excerpts(planspace, section.number)
        return "out"

    def _check(*_args, **_kwargs):
        if calls["count"] == 1:
            return ("out_of_scope", "needs root")
        return (None, "")

    class _CapturingCrossSection(CrossSectionService):
        def persist_decision(self, _planspace, _section_number, payload):
            persisted.append(payload)

    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch_fn)))
    Services.cross_section.override(providers.Object(_CapturingCrossSection()))
    monkeypatch.setattr(Services.dispatch_helpers(), "check_agent_signals", _check)
    capturing_pipeline_control._pause_return = "resume:accept root decision"
    monkeypatch.setattr(
        "proposal.service.excerpt_extractor.append_open_problem",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "proposal.service.excerpt_extractor.update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )

    try:
        result = _make_extractor(prompt_writers=mock_pw).extract_excerpts(
            section,
            planspace,
            codespace,
        )

        scope_delta_path = (
            planspace / "artifacts" / "scope-deltas" / "section-01-scope-delta.json"
        )
        assert result == "ok"
        assert persisted == ["accept root decision"]
        assert scope_delta_path.exists()
    finally:
        Services.dispatcher.reset_override()
        Services.cross_section.reset_override()

def test_extract_excerpts_returns_none_when_parent_does_not_resume(
    base_dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    capturing_pipeline_control) -> None:
    planspace, codespace = base_dirs
    section = _section(planspace)

    mock_pw = MagicMock()
    mock_pw.write_section_setup_prompt.return_value = planspace / "artifacts" / "setup-prompt.md"

    Services.dispatcher.override(providers.Object(make_dispatcher(lambda *_a, **_kw: "out")))
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "check_agent_signals",
        lambda *_args, **_kwargs: ("needs_parent", "blocked"),
    )
    capturing_pipeline_control._pause_return = "stop"
    monkeypatch.setattr(
        "proposal.service.excerpt_extractor.append_open_problem",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "proposal.service.excerpt_extractor.update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )

    try:
        result = _make_extractor(prompt_writers=mock_pw).extract_excerpts(
            section,
            planspace,
            codespace,
        )

        assert result is None
    finally:
        Services.dispatcher.reset_override()
