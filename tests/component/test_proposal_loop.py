from __future__ import annotations

import json
from pathlib import Path

import pytest
from dependency_injector import providers

from containers import AgentDispatcher, CrossSectionService, FlowIngestionService, Services
from src.proposal.engine.loop import run_proposal_loop
from src.orchestrator.types import Section

def _section(planspace: Path) -> Section:
    artifacts = planspace / "artifacts"
    section = Section(
        number="01",
        path=artifacts / "sections" / "section-01.md",
        related_files=["src/main.py"],
    )
    section.path.parent.mkdir(parents=True, exist_ok=True)
    section.path.write_text("# Section 01\n", encoding="utf-8")
    return section

@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    for path in (
        planspace / "artifacts" / "sections",
        planspace / "artifacts" / "signals",
        planspace / "artifacts" / "proposals",
        planspace / "artifacts" / "notes",
    ):
        path.mkdir(parents=True, exist_ok=True)
    codespace.mkdir()
    return planspace, codespace

def test_run_proposal_loop_returns_empty_string_on_first_pass_alignment(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    prompt_path = planspace / "artifacts" / "proposal-prompt.md"
    align_prompt_path = planspace / "artifacts" / "align-prompt.md"
    alignment_written: list[str] = []

    monkeypatch.setattr(
        "src.proposal.engine.loop.load_triage_result",
        lambda *_args, **_kwargs: {"intent_mode": "lightweight", "budgets": {}},
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "write_model_choice_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.write_integration_proposal_prompt",
        lambda *_args, **_kwargs: prompt_path,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.write_integration_alignment_prompt",
        lambda *_args, **_kwargs: align_prompt_path,
    )

    def _dispatch(*args, **kwargs):
        output_path = args[2]
        if kwargs.get("agent_file") == "integration-proposer.md":
            proposal_path.write_text("proposal", encoding="utf-8")
            output_path.write_text("proposal output", encoding="utf-8")
            return "proposal output"
        output_path.write_text("aligned", encoding="utf-8")
        return '{"aligned": true}'

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return _dispatch(*args, **kwargs)

    class _NoopFlow(FlowIngestionService):
        def ingest_and_submit(self, *_args, **_kwargs):
            return None

    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    Services.flow_ingestion.override(providers.Object(_NoopFlow()))
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop._extract_problems",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.load_reconciliation_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop._write_alignment_surface",
        lambda _planspace, _section: alignment_written.append("done"),
    )

    try:
        result = run_proposal_loop(
            section,
            planspace,
            codespace,
            "parent",
            {
                "proposal": "gpt",
                "alignment": "claude",
                "escalation_model": "stronger",
            },
            {"proposal_max": 3, "implementation_max": 3},
            incoming_notes="",
        )

        assert result == ""
        assert alignment_written == ["done"]
    finally:
        Services.dispatcher.reset_override()
        Services.flow_ingestion.reset_override()

def test_run_proposal_loop_returns_previous_problems_after_retry_alignment(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    problems = iter(["missing anchor", None])

    monkeypatch.setattr(
        "src.proposal.engine.loop.load_triage_result",
        lambda *_args, **_kwargs: {"intent_mode": "lightweight", "budgets": {}},
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "write_model_choice_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.write_integration_proposal_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "proposal-prompt.md",
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.write_integration_alignment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "align-prompt.md",
    )

    def _dispatch(*_args, **kwargs):
        if kwargs.get("agent_file") == "integration-proposer.md":
            proposal_path.write_text("proposal", encoding="utf-8")
            return "proposal output"
        return "alignment output"

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return _dispatch(*args, **kwargs)

    class _NoopFlow(FlowIngestionService):
        def ingest_and_submit(self, *_args, **_kwargs):
            return None

    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    Services.flow_ingestion.override(providers.Object(_NoopFlow()))
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop._extract_problems",
        lambda *_args, **_kwargs: next(problems),
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.load_reconciliation_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop._write_alignment_surface",
        lambda *_args, **_kwargs: None,
    )

    try:
        result = run_proposal_loop(
            section,
            planspace,
            codespace,
            "parent",
            {
                "proposal": "gpt",
                "alignment": "claude",
                "escalation_model": "stronger",
            },
            {"proposal_max": 3, "implementation_max": 3},
            incoming_notes="",
        )

        assert result == "missing anchor"
    finally:
        Services.dispatcher.reset_override()
        Services.flow_ingestion.reset_override()

def test_run_proposal_loop_routes_out_of_scope_and_retries(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    capturing_pipeline_control) -> None:
    planspace, codespace = env
    section = _section(planspace)
    signal_path = planspace / "artifacts" / "signals" / "proposal-01-signal.json"
    signal_path.write_text(json.dumps({"state": "out_of_scope"}), encoding="utf-8")
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    persisted: list[str] = []
    call_count = {"value": 0}

    capturing_pipeline_control._pause_return = "resume:use new direction"

    monkeypatch.setattr(
        "src.proposal.engine.loop.load_triage_result",
        lambda *_args, **_kwargs: {"intent_mode": "lightweight", "budgets": {}},
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "write_model_choice_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.write_integration_proposal_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "proposal-prompt.md",
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.write_integration_alignment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "align-prompt.md",
    )

    def _dispatch(*args, **kwargs):
        call_count["value"] += 1
        if kwargs.get("agent_file") == "integration-proposer.md" and call_count["value"] > 1:
            proposal_path.write_text("proposal", encoding="utf-8")
        return "output"

    def _signals(*args, **kwargs):
        if kwargs["signal_path"].name == "proposal-01-signal.json" and call_count["value"] == 1:
            return ("out_of_scope", "new work")
        return (None, "")

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return _dispatch(*args, **kwargs)

    class _NoopFlow(FlowIngestionService):
        def ingest_and_submit(self, *_args, **_kwargs):
            return None

    class _CapturingCrossSection(CrossSectionService):
        def persist_decision(self, _planspace, _section_number, payload):
            persisted.append(payload)

    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    Services.flow_ingestion.override(providers.Object(_NoopFlow()))
    Services.cross_section.override(providers.Object(_CapturingCrossSection()))
    monkeypatch.setattr(Services.dispatch_helpers(), "check_agent_signals", _signals)
    monkeypatch.setattr(
        "src.proposal.engine.loop._extract_problems",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop.load_reconciliation_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop._append_open_problem",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop._update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.loop._write_alignment_surface",
        lambda *_args, **_kwargs: None,
    )

    try:
        result = run_proposal_loop(
            section,
            planspace,
            codespace,
            "parent",
            {
                "proposal": "gpt",
                "alignment": "claude",
                "escalation_model": "stronger",
            },
            {"proposal_max": 3, "implementation_max": 3},
            incoming_notes="",
        )

        assert result == ""
        assert persisted == ["use new direction"]
        assert (
            planspace / "artifacts" / "scope-deltas" / "section-01-scope-delta.json"
        ).exists()
    finally:
        Services.dispatcher.reset_override()
        Services.flow_ingestion.reset_override()
        Services.cross_section.reset_override()
