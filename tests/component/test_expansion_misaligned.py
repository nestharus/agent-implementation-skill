from __future__ import annotations

from pathlib import Path

import pytest

from src.scripts.lib.pipelines.proposal_loop import run_proposal_loop
from src.scripts.section_loop.types import Section


def _section(planspace: Path) -> Section:
    section = Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
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


def _install_common_patches(
    monkeypatch: pytest.MonkeyPatch,
    planspace: Path,
    proposal_path: Path,
) -> None:
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.load_triage_result",
        lambda *_args, **_kwargs: {
            "intent_mode": "full",
            "budgets": {"intent_expansion_max": 2},
        },
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.handle_pending_messages",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.write_model_choice_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.write_integration_proposal_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "proposal-prompt.md",
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.write_integration_alignment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "align-prompt.md",
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.mailbox_send",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.ingest_and_submit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.load_reconciliation_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop._write_alignment_surface",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.persist_decision",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.handle_user_gate",
        lambda *_args, **_kwargs: None,
    )

    def _dispatch(*args, **kwargs):
        if kwargs.get("agent_file") == "integration-proposer.md":
            proposal_path.write_text("proposal", encoding="utf-8")
            return "proposal output"
        return "alignment output"

    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.dispatch_agent",
        _dispatch,
    )


def test_definition_gap_feedback_surfaces_trigger_expansion_on_misaligned_pass(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    _install_common_patches(monkeypatch, planspace, proposal_path)

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

    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop._extract_problems",
        lambda *_args, **_kwargs: next(problems),
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.load_combined_intent_surfaces",
        lambda *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.run_expansion_cycle",
        lambda *args, **kwargs: expansion_calls.append(args[0]) or {
            "needs_user_input": False,
            "restart_required": False,
        },
    )

    if hasattr(run_proposal_loop, "_expansion_counts"):
        delattr(run_proposal_loop, "_expansion_counts")

    result = run_proposal_loop(
        section,
        planspace,
        codespace,
        "parent",
        {
            "proposal": "gpt",
            "alignment": "claude",
            "intent_judge": "claude",
            "escalation_model": "stronger",
        },
        {"proposal_max": 3, "implementation_max": 3},
        incoming_notes="",
    )

    assert result == "missing constraint"
    assert expansion_calls == ["01"]


def test_non_definition_gap_surfaces_do_not_trigger_expansion_on_misaligned_pass(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    _install_common_patches(monkeypatch, planspace, proposal_path)

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

    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop._extract_problems",
        lambda *_args, **_kwargs: next(problems),
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.load_combined_intent_surfaces",
        lambda *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.run_expansion_cycle",
        lambda *args, **kwargs: expansion_calls.append(args[0]) or {
            "needs_user_input": False,
            "restart_required": False,
        },
    )

    if hasattr(run_proposal_loop, "_expansion_counts"):
        delattr(run_proposal_loop, "_expansion_counts")

    result = run_proposal_loop(
        section,
        planspace,
        codespace,
        "parent",
        {
            "proposal": "gpt",
            "alignment": "claude",
            "intent_judge": "claude",
            "escalation_model": "stronger",
        },
        {"proposal_max": 3, "implementation_max": 3},
        incoming_notes="",
    )

    assert result == "proposal drift"
    assert expansion_calls == []


def test_misaligned_definition_gap_expansion_respects_budget(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    _install_common_patches(monkeypatch, planspace, proposal_path)

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

    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.load_triage_result",
        lambda *_args, **_kwargs: {
            "intent_mode": "full",
            "budgets": {"intent_expansion_max": 1},
        },
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop._extract_problems",
        lambda *_args, **_kwargs: next(problems),
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.load_combined_intent_surfaces",
        lambda *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "src.scripts.lib.pipelines.proposal_loop.run_expansion_cycle",
        lambda *args, **kwargs: expansion_calls.append(args[0]) or {
            "needs_user_input": False,
            "restart_required": False,
        },
    )

    run_proposal_loop._expansion_counts = {"01": 1}

    result = run_proposal_loop(
        section,
        planspace,
        codespace,
        "parent",
        {
            "proposal": "gpt",
            "alignment": "claude",
            "intent_judge": "claude",
            "escalation_model": "stronger",
        },
        {"proposal_max": 3, "implementation_max": 3},
        incoming_notes="",
    )

    assert result == "missing axis"
    assert expansion_calls == []
