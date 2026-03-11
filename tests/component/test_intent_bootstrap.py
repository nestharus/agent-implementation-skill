from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.intent import intent_bootstrap
from src.intent.intent_bootstrap import run_intent_bootstrap
from orchestrator.types import Section


def _make_section(planspace: Path) -> Section:
    section_path = planspace / "artifacts" / "sections" / "section-01.md"
    section_path.write_text("# Section 01\n", encoding="utf-8")
    problem_frame = (
        planspace / "artifacts" / "sections" / "section-01-problem-frame.md"
    )
    problem_frame.write_text("Problem frame summary", encoding="utf-8")
    return Section(number="01", path=section_path, related_files=["src/main.py"])


def test_run_intent_bootstrap_full_mode_generates_pack_and_merges_budget(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace)
    cycle_budget_path = (
        planspace / "artifacts" / "signals" / "section-01-cycle-budget.json"
    )
    cycle_budget_path.write_text(
        json.dumps({"proposal_max": 1, "implementation_max": 1}),
        encoding="utf-8",
    )
    traceability_calls: list[tuple] = []
    intent_pack_calls: list[str] = []
    governance_calls: list[tuple[str, Path, Path, str]] = []

    monkeypatch.setattr(
        intent_bootstrap,
        "run_intent_triage",
        lambda *args, **kwargs: {
            "intent_mode": "full",
            "budgets": {
                "proposal_max": 6,
                "implementation_max": 7,
                "intent_expansion_max": 2,
                "max_new_surfaces_per_cycle": 3,
                "ignored": 99,
            },
        },
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "_extract_todos_from_files",
        lambda *_args, **_kwargs: "- TODO: preserve invariant\n",
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "_record_traceability",
        lambda *args: traceability_calls.append(args),
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "ensure_global_philosophy",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "blocking_state": None,
            "philosophy_path": (
                planspace / "artifacts" / "intent" / "global" / "philosophy.md"
            ),
            "detail": "ready",
        },
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "build_section_governance_packet",
        lambda sec_num, ps, cs, summary="": governance_calls.append(
            (sec_num, ps, cs, summary)
        ),
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "generate_intent_pack",
        lambda _section, _planspace, _codespace, _parent, *, incoming_notes: intent_pack_calls.append(incoming_notes),
    )

    cycle_budget = run_intent_bootstrap(
        section,
        planspace,
        codespace,
        "parent",
        {},
        "incoming note",
    )

    assert cycle_budget == {
        "proposal_max": 6,
        "implementation_max": 7,
        "intent_expansion_max": 2,
        "max_new_surfaces_per_cycle": 3,
    }
    assert traceability_calls
    assert governance_calls == [("01", planspace, codespace, "Problem frame summary")]
    assert intent_pack_calls == ["incoming note"]
    assert (
        planspace / "artifacts" / "todos" / "section-01-todos.md"
    ).read_text(encoding="utf-8") == "- TODO: preserve invariant\n"


def test_run_intent_bootstrap_blocks_when_philosophy_is_unavailable(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace)

    monkeypatch.setattr(
        intent_bootstrap,
        "run_intent_triage",
        lambda *args, **kwargs: {"intent_mode": "lightweight", "budgets": {}},
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "_extract_todos_from_files",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "ensure_global_philosophy",
        lambda *_args, **_kwargs: {
            "status": "needs_user_input",
            "blocking_state": "NEED_DECISION",
            "philosophy_path": None,
            "detail": "philosophy bootstrap needs user input",
        },
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )
    blocker_rollups: list[Path] = []
    pauses: list[tuple[Path, str, str]] = []
    monkeypatch.setattr(
        intent_bootstrap,
        "_update_blocker_rollup",
        lambda current_planspace: blocker_rollups.append(current_planspace),
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "pause_for_parent",
        lambda current_planspace, parent, signal: pauses.append(
            (current_planspace, parent, signal),
        ) or "resume",
    )

    result = run_intent_bootstrap(
        section,
        planspace,
        codespace,
        "parent",
        {},
        None,
    )

    assert result is None
    assert blocker_rollups == [planspace]
    assert pauses == [(
        planspace,
        "parent",
        "pause:need_decision:global:philosophy bootstrap requires user input",
    )]
    assert not (
        planspace / "artifacts" / "signals" / "philosophy-blocker-01.json"
    ).exists()


def test_run_intent_bootstrap_aborts_when_alignment_changes_after_philosophy(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace)

    monkeypatch.setattr(
        intent_bootstrap,
        "run_intent_triage",
        lambda *args, **kwargs: {"intent_mode": "full", "budgets": {}},
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "_extract_todos_from_files",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "ensure_global_philosophy",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "blocking_state": None,
            "philosophy_path": (
                planspace / "artifacts" / "intent" / "global" / "philosophy.md"
            ),
            "detail": "ready",
        },
    )
    alignment_states = iter([True])
    monkeypatch.setattr(
        intent_bootstrap,
        "alignment_changed_pending",
        lambda *_args, **_kwargs: next(alignment_states),
    )
    monkeypatch.setattr(
        intent_bootstrap,
        "generate_intent_pack",
        lambda *_args, **_kwargs: pytest.fail("intent pack should not run"),
    )

    result = run_intent_bootstrap(
        section,
        planspace,
        codespace,
        "parent",
        {},
        None,
    )

    assert result is None
