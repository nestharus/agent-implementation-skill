from __future__ import annotations

from pathlib import Path

import pytest

from conftest import override_dispatcher_and_guard, build_proposal_cycle
from containers import Services
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
    section = Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
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

def _install_common_patches(
    monkeypatch: pytest.MonkeyPatch,
    planspace: Path,
    proposal_path: Path,
) -> None:
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
        "proposal.service.expansion_handler.handle_user_gate",
        lambda *_args, **_kwargs: None,
    )
    def _dispatch(*args, **kwargs):
        if kwargs.get("agent_file") == "integration-proposer.md":
            proposal_path.write_text("proposal", encoding="utf-8")
            return "proposal output"
        return "alignment output"

    return _dispatch

def test_definition_gap_feedback_surfaces_trigger_expansion_on_misaligned_pass(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    _dispatch = _install_common_patches(monkeypatch, planspace, proposal_path)

    problems = iter(["missing constraint", None])
    combined_surfaces = iter([
        {
            "problem_surfaces": [
                {
                    "kind": "gap",
                    "axis_id": "A4",
                    "title": "New constraint surfaced in implementation",
                    "description": "Observed throughput limit is unmodeled",
                    "evidence": "Implementation prototype throttles requests",
                },
            ],
            "philosophy_surfaces": [],
        },
        None,
    ])
    expansion_calls: list[str] = []

    from intent.service.surface_registry import SurfaceRegistry
    monkeypatch.setattr(
        SurfaceRegistry,
        "load_combined_intent_surfaces",
        lambda self, *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "proposal.service.expansion_handler.run_expansion_cycle",
        lambda *args, **kwargs: expansion_calls.append(args[0]) or {
            "needs_user_input": False,
            "restart_required": False,
        },
    )

    triage = {
        "intent_mode": "full",
        "budgets": {"intent_expansion_max": 2},
    }

    with override_dispatcher_and_guard(_dispatch):
        monkeypatch.setattr(
            Services.section_alignment(), "extract_problems",
            lambda *_args, **_kwargs: next(problems),
        )
        cycle = build_proposal_cycle(intent_triager=_StubTriager(triage))
        result = cycle.run_proposal_loop(
            section,
            DispatchContext(planspace=planspace, codespace=codespace),
            {"proposal_max": 3, "implementation_max": 3},
            incoming_notes="",
        )

    assert result == "missing constraint"
    assert expansion_calls == ["01"]

def test_non_definition_gap_surfaces_do_not_trigger_expansion_on_misaligned_pass(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    _dispatch = _install_common_patches(monkeypatch, planspace, proposal_path)

    problems = iter(["proposal drift", None])
    combined_surfaces = iter([
        {
            "problem_surfaces": [
                {
                    "kind": "proposal_quality",
                    "axis_id": "A2",
                    "title": "Proposal needs clearer sequencing",
                    "description": "Quality issue, not a definition gap",
                    "evidence": "Judge requested more explicit steps",
                },
            ],
            "philosophy_surfaces": [],
        },
        None,
    ])
    expansion_calls: list[str] = []

    from intent.service.surface_registry import SurfaceRegistry
    monkeypatch.setattr(
        SurfaceRegistry,
        "load_combined_intent_surfaces",
        lambda self, *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "proposal.service.expansion_handler.run_expansion_cycle",
        lambda *args, **kwargs: expansion_calls.append(args[0]) or {
            "needs_user_input": False,
            "restart_required": False,
        },
    )

    triage = {
        "intent_mode": "full",
        "budgets": {"intent_expansion_max": 2},
    }

    with override_dispatcher_and_guard(_dispatch):
        monkeypatch.setattr(
            Services.section_alignment(), "extract_problems",
            lambda *_args, **_kwargs: next(problems),
        )
        cycle = build_proposal_cycle(intent_triager=_StubTriager(triage))
        result = cycle.run_proposal_loop(
            section,
            DispatchContext(planspace=planspace, codespace=codespace),
            {"proposal_max": 3, "implementation_max": 3},
            incoming_notes="",
        )

    assert result == "proposal drift"
    assert expansion_calls == []

def test_misaligned_definition_gap_expansion_respects_budget(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    _dispatch = _install_common_patches(monkeypatch, planspace, proposal_path)

    problems = iter(["missing axis", None])
    combined_surfaces = iter([
        {
            "problem_surfaces": [
                {
                    "kind": "new_axis",
                    "axis_id": "",
                    "title": "Missing operational axis",
                    "description": "Implementation exposed a new dimension",
                    "evidence": "Prototype required a new scheduling mode",
                },
            ],
            "philosophy_surfaces": [],
        },
        None,
    ])
    expansion_calls: list[str] = []

    from intent.service.surface_registry import SurfaceRegistry
    monkeypatch.setattr(
        SurfaceRegistry,
        "load_combined_intent_surfaces",
        lambda self, *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "proposal.service.expansion_handler.run_expansion_cycle",
        lambda *args, **kwargs: expansion_calls.append(args[0]) or {
            "needs_user_input": False,
            "restart_required": False,
        },
    )

    triage = {
        "intent_mode": "full",
        "budgets": {"intent_expansion_max": 0},
    }

    with override_dispatcher_and_guard(_dispatch):
        monkeypatch.setattr(
            Services.section_alignment(), "extract_problems",
            lambda *_args, **_kwargs: next(problems),
        )
        cycle = build_proposal_cycle(intent_triager=_StubTriager(triage))
        result = cycle.run_proposal_loop(
            section,
            DispatchContext(planspace=planspace, codespace=codespace),
            {"proposal_max": 3, "implementation_max": 3},
            incoming_notes="",
        )

    assert result == "missing axis"
    assert expansion_calls == []
