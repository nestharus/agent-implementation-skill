from __future__ import annotations

import json
from pathlib import Path

import pytest
from dependency_injector import providers

from conftest import NoOpFlow, NoOpSectionAlignment, build_proposal_cycle, make_dispatcher
from containers import CrossSectionService, Services
from dispatch.prompt.writers import Writers as PromptWriters
from pipeline.context import DispatchContext
from reconciliation.repository.results import Results
from src.orchestrator.path_registry import PathRegistry
from src.orchestrator.types import Section


class _StubTriager:
    """Minimal IntentTriager stub returning injected triage result."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def load_triage_result(self, *_args, **_kwargs):
        return self._result

    def run_intent_triage(self, *_args, **_kwargs):
        return self._result


def _section(planspace: Path) -> Section:
    artifacts = planspace / "artifacts"
    section = Section(
        number="01",
        path=artifacts / "sections" / "section-01.md",
        related_files=["src/main.py"],
    )
    section.path.write_text("# Section 01\n", encoding="utf-8")
    return section

@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
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
        Services.dispatch_helpers(),
        "write_model_choice_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        PromptWriters,
        "write_integration_proposal_prompt",
        lambda self, *_args, **_kwargs: prompt_path,
    )
    monkeypatch.setattr(
        PromptWriters,
        "write_integration_alignment_prompt",
        lambda self, *_args, **_kwargs: align_prompt_path,
    )

    def _dispatch(*args, **kwargs):
        output_path = args[2]
        if kwargs.get("agent_file") == "integration-proposer.md":
            proposal_path.write_text("proposal", encoding="utf-8")
            output_path.write_text("proposal output", encoding="utf-8")
            return "proposal output"
        output_path.write_text("aligned", encoding="utf-8")
        return '{"aligned": true}'

    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))
    Services.flow_ingestion.override(providers.Object(NoOpFlow()))
    Services.section_alignment.override(providers.Object(NoOpSectionAlignment()))
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    monkeypatch.setattr(
        Results,
        "load_result",
        lambda self, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "proposal.engine.proposal_cycle.write_alignment_surface",
        lambda _planspace, _section: alignment_written.append("done"),
    )

    triage = {"intent_mode": "lightweight"}

    try:
        cycle = build_proposal_cycle(intent_triager=_StubTriager(triage))
        result = cycle.run_proposal_loop(
            section,
            DispatchContext(planspace=planspace, codespace=codespace, _policies=Services.policies()),
            incoming_notes="",
        )

        assert result == ""
        assert alignment_written == ["done"]
    finally:
        Services.dispatcher.reset_override()
        Services.flow_ingestion.reset_override()
        Services.section_alignment.reset_override()

def test_run_proposal_loop_returns_problems_on_misalignment(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control) -> None:
    """Single-shot: returns problems string when alignment check finds issues."""
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )

    triage = {"intent_mode": "lightweight"}

    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "write_model_choice_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        PromptWriters,
        "write_integration_proposal_prompt",
        lambda self, *_args, **_kwargs: planspace / "artifacts" / "proposal-prompt.md",
    )
    monkeypatch.setattr(
        PromptWriters,
        "write_integration_alignment_prompt",
        lambda self, *_args, **_kwargs: planspace / "artifacts" / "align-prompt.md",
    )

    def _dispatch(*_args, **kwargs):
        if kwargs.get("agent_file") == "integration-proposer.md":
            proposal_path.write_text("proposal", encoding="utf-8")
            return "proposal output"
        return "alignment output"

    sa = NoOpSectionAlignment()
    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))
    Services.flow_ingestion.override(providers.Object(NoOpFlow()))
    Services.section_alignment.override(providers.Object(sa))
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    monkeypatch.setattr(
        sa, "extract_problems",
        lambda *_args, **_kwargs: "missing anchor",
    )
    monkeypatch.setattr(
        Results,
        "load_result",
        lambda self, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "proposal.engine.proposal_cycle.write_alignment_surface",
        lambda *_args, **_kwargs: None,
    )

    try:
        cycle = build_proposal_cycle(intent_triager=_StubTriager(triage))
        result = cycle.run_proposal_loop(
            section,
            DispatchContext(planspace=planspace, codespace=codespace, _policies=Services.policies()),
            incoming_notes="",
        )

        assert result == "missing anchor"
    finally:
        Services.dispatcher.reset_override()
        Services.flow_ingestion.reset_override()
        Services.section_alignment.reset_override()

def test_run_proposal_loop_returns_none_on_out_of_scope_signal(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    capturing_pipeline_control) -> None:
    """Single-shot handler returns None when dispatch signals out_of_scope.

    The state machine handles retry via ASSESSING -> PROPOSING transition.
    """
    planspace, codespace = env
    section = _section(planspace)
    signal_path = planspace / "artifacts" / "signals" / "proposal-01-signal.json"
    signal_path.write_text(json.dumps({"state": "out_of_scope"}), encoding="utf-8")

    persisted: list[str] = []

    capturing_pipeline_control._pause_return = "resume:use new direction"

    triage = {"intent_mode": "lightweight"}

    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "write_model_choice_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        PromptWriters,
        "write_integration_proposal_prompt",
        lambda self, *_args, **_kwargs: planspace / "artifacts" / "proposal-prompt.md",
    )

    def _dispatch(*args, **kwargs):
        return "output"

    def _signals(*args, **kwargs):
        if kwargs["signal_path"].name == "proposal-01-signal.json":
            return ("out_of_scope", "new work")
        return (None, "")

    class _CapturingCrossSection(CrossSectionService):
        def persist_decision(self, _planspace, _section_number, payload):
            persisted.append(payload)

    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))
    Services.flow_ingestion.override(providers.Object(NoOpFlow()))
    Services.cross_section.override(providers.Object(_CapturingCrossSection()))
    Services.section_alignment.override(providers.Object(NoOpSectionAlignment()))
    monkeypatch.setattr(Services.dispatch_helpers(), "check_agent_signals", _signals)
    monkeypatch.setattr(
        Results,
        "load_result",
        lambda self, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "proposal.service.cycle_control.append_open_problem",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "proposal.service.cycle_control.update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )

    try:
        cycle = build_proposal_cycle(intent_triager=_StubTriager(triage))
        result = cycle.run_proposal_loop(
            section,
            DispatchContext(planspace=planspace, codespace=codespace, _policies=Services.policies()),
            incoming_notes="",
        )

        # Single-shot: out_of_scope signal causes dispatch to return
        # ACTION_CONTINUE, which maps to None (state machine retries).
        assert result is None
        assert persisted == ["use new direction"]
    finally:
        Services.dispatcher.reset_override()
        Services.flow_ingestion.reset_override()
        Services.cross_section.reset_override()
        Services.section_alignment.reset_override()


def test_proposal_loop_single_shot_dispatches_once(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control) -> None:
    """Single-shot handler dispatches exactly one proposal attempt.

    The state machine handles iteration -- the handler itself never loops.
    """
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    prompt_path = planspace / "artifacts" / "proposal-prompt.md"
    align_prompt_path = planspace / "artifacts" / "align-prompt.md"

    dispatch_count = {"value": 0}

    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "write_model_choice_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        PromptWriters,
        "write_integration_proposal_prompt",
        lambda self, *_args, **_kwargs: prompt_path,
    )
    monkeypatch.setattr(
        PromptWriters,
        "write_integration_alignment_prompt",
        lambda self, *_args, **_kwargs: align_prompt_path,
    )

    def _dispatch(*args, **kwargs):
        if kwargs.get("agent_file") == "integration-proposer.md":
            dispatch_count["value"] += 1
            proposal_path.write_text("proposal", encoding="utf-8")
            output_path = args[2]
            output_path.write_text("proposal output", encoding="utf-8")
            return "proposal output"
        output_path = args[2]
        output_path.write_text("alignment output", encoding="utf-8")
        return "alignment output"

    sa = NoOpSectionAlignment()
    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))
    Services.flow_ingestion.override(providers.Object(NoOpFlow()))
    Services.section_alignment.override(providers.Object(sa))
    monkeypatch.setattr(
        Services.dispatch_helpers(),
        "check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    # First call returns problems (misaligned)
    monkeypatch.setattr(sa, "extract_problems", lambda *_args, **_kwargs: "needs fix")
    monkeypatch.setattr(
        Results,
        "load_result",
        lambda self, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "proposal.engine.proposal_cycle.write_alignment_surface",
        lambda *_args, **_kwargs: None,
    )

    triage = {"intent_mode": "lightweight"}

    try:
        cycle = build_proposal_cycle(intent_triager=_StubTriager(triage))
        result = cycle.run_proposal_loop(
            section,
            DispatchContext(planspace=planspace, codespace=codespace, _policies=Services.policies()),
            incoming_notes="",
        )

        # Single-shot: exactly one proposal dispatch per call
        assert dispatch_count["value"] == 1
        # Returns problems string (non-empty) so state machine knows to retry
        assert result == "needs fix"
    finally:
        Services.dispatcher.reset_override()
        Services.flow_ingestion.reset_override()
        Services.section_alignment.reset_override()
