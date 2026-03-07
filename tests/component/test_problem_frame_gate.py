from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scripts.lib import problem_frame_gate
from src.scripts.lib.problem_frame_gate import validate_problem_frame
from section_loop.types import Section


def _make_section(planspace: Path) -> Section:
    section_path = planspace / "artifacts" / "sections" / "section-01.md"
    section_path.write_text("# Section 01\n", encoding="utf-8")
    proposal = planspace / "artifacts" / "global-proposal.md"
    alignment = planspace / "artifacts" / "global-alignment.md"
    proposal.write_text("# Proposal\n", encoding="utf-8")
    alignment.write_text("# Alignment\n", encoding="utf-8")
    return Section(
        number="01",
        path=section_path,
        global_proposal_path=proposal,
        global_alignment_path=alignment,
    )


def test_validate_problem_frame_blocks_when_retry_still_does_not_create_frame(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace)
    messages: list[str] = []
    blocker_updates: list[Path] = []

    monkeypatch.setattr(
        problem_frame_gate,
        "dispatch_agent",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        problem_frame_gate,
        "_update_blocker_rollup",
        lambda planspace_arg: blocker_updates.append(planspace_arg),
    )
    monkeypatch.setattr(
        problem_frame_gate,
        "mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
    )

    result = validate_problem_frame(
        section,
        planspace,
        codespace,
        "parent",
        {"setup": "setup-model"},
    )

    assert result is None
    assert blocker_updates == [planspace]
    assert messages == ["pause:needs_parent:01:problem frame missing after retry"]
    signal = json.loads(
        (planspace / "artifacts" / "signals" / "setup-01-signal.json").read_text(
            encoding="utf-8",
        ),
    )
    assert signal["state"] == "needs_parent"


def test_validate_problem_frame_invalidates_existing_proposal_when_hash_changes(
    planspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace)
    problem_frame = (
        planspace / "artifacts" / "sections" / "section-01-problem-frame.md"
    )
    problem_frame.write_text("Problem frame", encoding="utf-8")
    proposal = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    proposal.write_text("existing proposal", encoding="utf-8")
    hash_path = (
        planspace
        / "artifacts"
        / "signals"
        / "section-01-problem-frame-hash.txt"
    )
    hash_path.write_text("old-hash", encoding="utf-8")

    monkeypatch.setattr(problem_frame_gate, "_record_traceability", lambda *args: None)
    monkeypatch.setattr(
        problem_frame_gate,
        "_write_alignment_surface",
        lambda *args: None,
    )

    result = validate_problem_frame(section, planspace, planspace, "parent", {})

    assert result == "ok"
    assert not proposal.exists()
    assert hash_path.read_text(encoding="utf-8") != "old-hash"


def test_validate_problem_frame_records_traceability_when_excerpts_exist(
    planspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace)
    problem_frame = (
        planspace / "artifacts" / "sections" / "section-01-problem-frame.md"
    )
    problem_frame.write_text("Problem frame", encoding="utf-8")
    (planspace / "artifacts" / "sections" / "section-01-proposal-excerpt.md").write_text(
        "proposal excerpt",
        encoding="utf-8",
    )
    (planspace / "artifacts" / "sections" / "section-01-alignment-excerpt.md").write_text(
        "alignment excerpt",
        encoding="utf-8",
    )
    traceability_calls: list[tuple] = []
    surface_calls: list[str] = []

    monkeypatch.setattr(
        problem_frame_gate,
        "_record_traceability",
        lambda *args: traceability_calls.append(args),
    )
    monkeypatch.setattr(
        problem_frame_gate,
        "_write_alignment_surface",
        lambda _planspace, sec: surface_calls.append(sec.number),
    )

    result = validate_problem_frame(section, planspace, planspace, "parent", {})

    assert result == "ok"
    assert len(traceability_calls) == 2
    assert surface_calls == ["01"]
