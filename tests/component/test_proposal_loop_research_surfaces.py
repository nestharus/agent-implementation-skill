from __future__ import annotations

import json
from pathlib import Path

import pytest
from dependency_injector import providers

from conftest import NoOpFlow, NoOpSectionAlignment, StubPolicies, build_proposal_cycle, make_dispatcher
from containers import CrossSectionService, Services
from dispatch.prompt.writers import Writers as PromptWriters
from pipeline.context import DispatchContext
from reconciliation.repository.results import Results
from src.intent.service.expansion_facade import run_expansion_cycle
from src.orchestrator.types import Section


class _StubTriager:
    """Minimal IntentTriager stub returning injected triage result."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def load_triage_result(self, *_args, **_kwargs):
        return self._result

    def run_intent_triage(self, *_args, **_kwargs):
        return self._result


def _section(planspace: Path, number: str = "01") -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=section_path, related_files=["src/main.py"])

def _write_research_surfaces(
    planspace: Path,
    payload: dict,
    number: str = "01",
) -> Path:
    research_dir = (
        planspace / "artifacts" / "research" / "sections" / f"section-{number}"
    )
    research_dir.mkdir(parents=True, exist_ok=True)
    research_path = research_dir / "research-derived-surfaces.json"
    research_path.write_text(json.dumps(payload), encoding="utf-8")
    return research_path

def test_run_proposal_loop_uses_research_surfaces_to_trigger_expansion(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator,
    noop_pipeline_control) -> None:
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    _write_research_surfaces(
        planspace,
        {
            "problem_surfaces": [
                {
                    "kind": "gap",
                    "axis_id": "A1",
                    "title": "Missing integration edge",
                    "description": "Research found a missing seam",
                    "evidence": "dossier",
                }
            ],
            "philosophy_surfaces": [],
        },
    )
    expansion_calls: list[str] = []

    triage = {"intent_mode": "full", "budgets": {}}

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

    def _dispatch(*args, **kwargs):
        output_path = args[2]
        if kwargs.get("agent_file") == "integration-proposer.md":
            proposal_path.write_text("proposal", encoding="utf-8")
            output_path.write_text("proposal output", encoding="utf-8")
            return "proposal output"
        output_path.write_text("aligned", encoding="utf-8")
        return '{"aligned": true}'

    class _NoopCrossSection(CrossSectionService):
        def persist_decision(self, *_args, **_kwargs):
            return None

    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))
    Services.flow_ingestion.override(providers.Object(NoOpFlow()))
    Services.cross_section.override(providers.Object(_NoopCrossSection()))
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
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "proposal.service.expansion_handler.run_expansion_cycle",
        lambda section_number, *_args, **_kwargs: (
            expansion_calls.append(section_number)
            or {
                "restart_required": False,
                "needs_user_input": False,
            }
        ),
    )
    monkeypatch.setattr(
        "proposal.service.expansion_handler.handle_user_gate",
        lambda *_args, **_kwargs: None,
    )

    try:
        cycle = build_proposal_cycle(intent_triager=_StubTriager(triage))
        result = cycle.run_proposal_loop(
            section,
            DispatchContext(planspace=planspace, codespace=codespace),
            {"proposal_max": 3, "implementation_max": 3},
            incoming_notes="",
        )

        assert result == ""
        assert expansion_calls == ["01"]
    finally:
        Services.dispatcher.reset_override()
        Services.flow_ingestion.reset_override()
        Services.cross_section.reset_override()
        Services.section_alignment.reset_override()

def test_run_expansion_cycle_merges_research_surfaces_into_pending_payload(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator) -> None:
    _section(planspace)
    _write_research_surfaces(
        planspace,
        {
            "problem_surfaces": [
                {
                    "kind": "gap",
                    "axis_id": "A2",
                    "title": "Research-only surface",
                    "description": "Derived from dossier",
                    "evidence": "research dossier",
                }
            ],
            "philosophy_surfaces": [],
        },
    )

    from intent.service.expanders import Expanders

    Services.policies.override(providers.Object(StubPolicies()))
    monkeypatch.setattr(
        Expanders,
        "run_problem_expander",
        lambda self, *_args, **_kwargs: {
            "applied": {
                "problem_definition_updated": False,
                "problem_rubric_updated": False,
            },
            "applied_surface_ids": [],
            "discarded_surface_ids": [],
            "new_axes": [],
            "restart_required": False,
        },
    )

    try:
        result = run_expansion_cycle("01", planspace, codespace)

        pending_payload = json.loads(
            (
                planspace / "artifacts" / "signals" / "intent-surfaces-pending-01.json"
            ).read_text(encoding="utf-8")
        )
        assert result["surfaces_found"] == 1
        assert pending_payload["problem_surfaces"][0]["title"] == "Research-only surface"
    finally:
        Services.policies.reset_override()
