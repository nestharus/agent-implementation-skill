from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.proposal.proposal_loop import run_proposal_loop
from src.orchestrator.types import Section


def _section(planspace: Path, number: str = "01") -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.parent.mkdir(parents=True, exist_ok=True)
    section_path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=section_path, related_files=["src/main.py"])


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


@pytest.fixture(autouse=True)
def clear_expansion_counts() -> None:
    if hasattr(run_proposal_loop, "_expansion_counts"):
        delattr(run_proposal_loop, "_expansion_counts")
    yield
    if hasattr(run_proposal_loop, "_expansion_counts"):
        delattr(run_proposal_loop, "_expansion_counts")


def _common_policy() -> dict:
    return {
        "proposal": "gpt",
        "alignment": "claude",
        "intent_judge": "claude",
        "escalation_model": "stronger",
    }


def _registry_path(planspace: Path, number: str = "01") -> Path:
    return (
        planspace
        / "artifacts"
        / "intent"
        / "sections"
        / f"section-{number}"
        / "surface-registry.json"
    )


def _escalation_path(planspace: Path, number: str = "01") -> Path:
    return planspace / "artifacts" / "signals" / f"intent-escalation-{number}.json"


def _install_common_patches(
    monkeypatch: pytest.MonkeyPatch,
    planspace: Path,
    *,
    intent_mode: str,
    proposal_args: list[str | None],
) -> None:
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )

    monkeypatch.setattr(
        "src.proposal.proposal_loop.load_triage_result",
        lambda *_args, **_kwargs: {
            "intent_mode": intent_mode,
            "budgets": {"intent_expansion_max": 2},
        },
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.handle_pending_messages",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.write_model_choice_signal",
        lambda *_args, **_kwargs: None,
    )

    def _proposal_prompt(_section, _planspace, _codespace, proposal_problems, **_kwargs):
        proposal_args.append(proposal_problems)
        return planspace / "artifacts" / "proposal-prompt.md"

    monkeypatch.setattr(
        "src.proposal.proposal_loop.write_integration_proposal_prompt",
        _proposal_prompt,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.write_integration_alignment_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "align-prompt.md",
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.mailbox_send",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.ingest_and_submit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.load_reconciliation_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop._write_alignment_surface",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.persist_decision",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.handle_user_gate",
        lambda *_args, **_kwargs: None,
    )

    def _dispatch(*args, **kwargs):
        output_path = args[2]
        if kwargs.get("agent_file") == "integration-proposer.md":
            proposal_path.write_text("proposal", encoding="utf-8")
            output_path.write_text("proposal output", encoding="utf-8")
            return "proposal output"
        output_path.write_text("alignment output", encoding="utf-8")
        return "alignment output"

    monkeypatch.setattr(
        "src.proposal.proposal_loop.dispatch_agent",
        _dispatch,
    )


def test_lightweight_aligned_surfaces_force_reproposal_under_full_intent(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_args: list[str | None] = []
    combined_surfaces = iter([
        {
            "problem_surfaces": [
                {
                    "kind": "proposal_quality",
                    "axis_id": "A1",
                    "title": "Need a sharper constraint split",
                    "description": "Implementation feedback exposed a new surface",
                    "evidence": "prototype review",
                },
            ],
            "philosophy_surfaces": [],
        },
        None,
    ])

    _install_common_patches(
        monkeypatch,
        planspace,
        intent_mode="lightweight",
        proposal_args=proposal_args,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop._extract_problems",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.load_combined_intent_surfaces",
        lambda *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.run_expansion_cycle",
        lambda *_args, **_kwargs: {
            "restart_required": False,
            "needs_user_input": False,
        },
    )

    result = run_proposal_loop(
        section,
        planspace,
        codespace,
        "parent",
        _common_policy(),
        {"proposal_max": 3, "implementation_max": 3},
        incoming_notes="",
    )

    escalation_payload = json.loads(_escalation_path(planspace).read_text(encoding="utf-8"))
    expected_reproposal = (
        "Lightweight section discovered structured surfaces; "
        "re-propose under full intent mode."
    )

    assert result == expected_reproposal
    assert proposal_args == [
        None,
        expected_reproposal,
    ]
    assert escalation_payload == {
        "section": "01",
        "reason": "structured_surfaces_on_lightweight",
        "surface_count": 1,
    }


def test_lightweight_aligned_surfaces_persist_registry_entries(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_args: list[str | None] = []
    payload = {
        "problem_surfaces": [
            {
                "kind": "proposal_quality",
                "axis_id": "A7",
                "title": "Keep a registry trail",
                "description": "Lightweight mode still needs persistence",
                "evidence": "review notes",
            },
        ],
        "philosophy_surfaces": [],
    }
    combined_surfaces = iter([payload, None])

    _install_common_patches(
        monkeypatch,
        planspace,
        intent_mode="lightweight",
        proposal_args=proposal_args,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop._extract_problems",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.load_combined_intent_surfaces",
        lambda *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.run_expansion_cycle",
        lambda *_args, **_kwargs: {
            "restart_required": False,
            "needs_user_input": False,
        },
    )

    run_proposal_loop(
        section,
        planspace,
        codespace,
        "parent",
        _common_policy(),
        {"proposal_max": 3, "implementation_max": 3},
        incoming_notes="",
    )

    registry = json.loads(_registry_path(planspace).read_text(encoding="utf-8"))

    assert registry["section"] == "01"
    assert registry["next_id"] == 2
    assert len(registry["surfaces"]) == 1
    assert registry["surfaces"][0]["id"] == "P-01-0001"
    assert registry["surfaces"][0]["notes"] == "Keep a registry trail"


def test_lightweight_empty_surface_payload_does_not_escalate(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_args: list[str | None] = []

    _install_common_patches(
        monkeypatch,
        planspace,
        intent_mode="lightweight",
        proposal_args=proposal_args,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop._extract_problems",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.load_combined_intent_surfaces",
        lambda *_args, **_kwargs: {
            "problem_surfaces": [],
            "philosophy_surfaces": [],
        },
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.run_expansion_cycle",
        lambda *_args, **_kwargs: {
            "restart_required": False,
            "needs_user_input": False,
        },
    )

    result = run_proposal_loop(
        section,
        planspace,
        codespace,
        "parent",
        _common_policy(),
        {"proposal_max": 3, "implementation_max": 3},
        incoming_notes="",
    )

    assert result == ""
    assert proposal_args == [None]
    assert not _escalation_path(planspace).exists()
    assert not _registry_path(planspace).exists()


def test_lightweight_misaligned_surfaces_persist_and_upgrade_to_full(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_args: list[str | None] = []
    problems = iter(["missing constraint", None])
    combined_surfaces = iter([
        {
            "problem_surfaces": [
                {
                    "kind": "gap",
                    "axis_id": "A4",
                    "title": "Throughput constraint missing",
                    "description": "Prototype exposed an unmodeled limit",
                    "evidence": "load test",
                },
            ],
            "philosophy_surfaces": [],
        },
        None,
    ])
    expansion_calls: list[str] = []

    _install_common_patches(
        monkeypatch,
        planspace,
        intent_mode="lightweight",
        proposal_args=proposal_args,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop._extract_problems",
        lambda *_args, **_kwargs: next(problems),
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.load_combined_intent_surfaces",
        lambda *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.run_expansion_cycle",
        lambda section_number, *_args, **_kwargs: expansion_calls.append(section_number)
        or {
            "restart_required": False,
            "needs_user_input": False,
        },
    )

    result = run_proposal_loop(
        section,
        planspace,
        codespace,
        "parent",
        _common_policy(),
        {"proposal_max": 3, "implementation_max": 3},
        incoming_notes="",
    )

    escalation_payload = json.loads(_escalation_path(planspace).read_text(encoding="utf-8"))
    registry = json.loads(_registry_path(planspace).read_text(encoding="utf-8"))

    assert result == "missing constraint"
    assert proposal_args == [None, "missing constraint"]
    assert expansion_calls == ["01"]
    assert escalation_payload == {
        "section": "01",
        "reason": "structured_surfaces_on_lightweight_misaligned",
        "surface_count": 1,
    }
    assert len(registry["surfaces"]) == 1
    assert registry["surfaces"][0]["id"] == "P-01-0001"


def test_full_mode_surfaces_do_not_emit_lightweight_escalation_signal(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = env
    section = _section(planspace)
    proposal_args: list[str | None] = []
    combined_surfaces = iter([
        {
            "problem_surfaces": [
                {
                    "kind": "gap",
                    "axis_id": "A5",
                    "title": "Definition needs another axis",
                    "description": "Full intent should expand, not escalate",
                    "evidence": "judge output",
                },
            ],
            "philosophy_surfaces": [],
        },
    ])
    expansion_calls: list[str] = []

    _install_common_patches(
        monkeypatch,
        planspace,
        intent_mode="full",
        proposal_args=proposal_args,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop._extract_problems",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.load_combined_intent_surfaces",
        lambda *_args, **_kwargs: next(combined_surfaces),
    )
    monkeypatch.setattr(
        "src.proposal.proposal_loop.run_expansion_cycle",
        lambda section_number, *_args, **_kwargs: expansion_calls.append(section_number)
        or {
            "restart_required": False,
            "needs_user_input": False,
        },
    )

    result = run_proposal_loop(
        section,
        planspace,
        codespace,
        "parent",
        _common_policy(),
        {"proposal_max": 3, "implementation_max": 3},
        incoming_notes="",
    )

    assert result == ""
    assert proposal_args == [None]
    assert expansion_calls == ["01"]
    assert not _escalation_path(planspace).exists()
