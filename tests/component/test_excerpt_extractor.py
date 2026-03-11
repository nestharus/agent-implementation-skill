from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.proposal.service.excerpt_extractor import extract_excerpts
from src.orchestrator.types import Section


def _section(planspace: Path) -> Section:
    artifacts = planspace / "artifacts"
    section = Section(
        number="01",
        path=artifacts / "sections" / "section-01.md",
        global_proposal_path=artifacts / "global-proposal.md",
        global_alignment_path=artifacts / "global-alignment.md",
    )
    section.path.parent.mkdir(parents=True, exist_ok=True)
    section.path.write_text("# Section 01\n", encoding="utf-8")
    section.global_proposal_path.write_text("# Global Proposal\n", encoding="utf-8")
    section.global_alignment_path.write_text("# Global Alignment\n", encoding="utf-8")
    return section


def _write_excerpts(planspace: Path, section_number: str) -> None:
    sections_dir = planspace / "artifacts" / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    (sections_dir / f"section-{section_number}-proposal-excerpt.md").write_text(
        "proposal",
        encoding="utf-8",
    )
    (sections_dir / f"section-{section_number}-alignment-excerpt.md").write_text(
        "alignment",
        encoding="utf-8",
    )


@pytest.fixture()
def base_dirs(tmp_path: Path) -> tuple[Path, Path]:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    (planspace / "artifacts" / "sections").mkdir(parents=True)
    (planspace / "artifacts" / "signals").mkdir(parents=True)
    (planspace / "artifacts" / "decisions").mkdir(parents=True)
    codespace.mkdir()
    return planspace, codespace


def test_extract_excerpts_returns_ok_when_setup_creates_files(
    base_dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = base_dirs
    section = _section(planspace)
    prompt_path = planspace / "artifacts" / "setup-prompt.md"

    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.write_section_setup_prompt",
        lambda *_args, **_kwargs: prompt_path,
    )

    def _dispatch(*_args, **_kwargs) -> str:
        _write_excerpts(planspace, section.number)
        return "ok"

    monkeypatch.setattr("src.proposal.service.excerpt_extractor.dispatch_agent", _dispatch)
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.check_agent_signals",
        lambda *_args, **_kwargs: (None, ""),
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.mailbox_send",
        lambda *_args, **_kwargs: None,
    )

    result = extract_excerpts(
        section,
        planspace,
        codespace,
        "parent",
        {"setup": "glm"},
    )

    assert result == "ok"


def test_extract_excerpts_routes_out_of_scope_then_retries(
    base_dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = base_dirs
    section = _section(planspace)
    signal_path = planspace / "artifacts" / "signals" / "setup-01-signal.json"
    signal_path.write_text(json.dumps({"state": "out_of_scope", "detail": "needs root"}))
    persisted: list[str] = []

    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.write_section_setup_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "setup-prompt.md",
    )

    calls = {"count": 0}

    def _dispatch(*_args, **_kwargs) -> str:
        calls["count"] += 1
        if calls["count"] == 2:
            _write_excerpts(planspace, section.number)
        return "out"

    def _check(*_args, **_kwargs):
        if calls["count"] == 1:
            return ("out_of_scope", "needs root")
        return (None, "")

    monkeypatch.setattr("src.proposal.service.excerpt_extractor.dispatch_agent", _dispatch)
    monkeypatch.setattr("src.proposal.service.excerpt_extractor.check_agent_signals", _check)
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.mailbox_send",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.pause_for_parent",
        lambda *_args, **_kwargs: "resume:accept root decision",
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.persist_decision",
        lambda _planspace, _section_number, payload: persisted.append(payload),
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor._append_open_problem",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor._update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.alignment_changed_pending",
        lambda *_args, **_kwargs: False,
    )

    result = extract_excerpts(
        section,
        planspace,
        codespace,
        "parent",
        {"setup": "glm"},
    )

    scope_delta_path = (
        planspace / "artifacts" / "scope-deltas" / "section-01-scope-delta.json"
    )
    assert result == "ok"
    assert persisted == ["accept root decision"]
    assert scope_delta_path.exists()


def test_extract_excerpts_returns_none_when_parent_does_not_resume(
    base_dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace = base_dirs
    section = _section(planspace)

    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.write_section_setup_prompt",
        lambda *_args, **_kwargs: planspace / "artifacts" / "setup-prompt.md",
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.dispatch_agent",
        lambda *_args, **_kwargs: "out",
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.check_agent_signals",
        lambda *_args, **_kwargs: ("needs_parent", "blocked"),
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.mailbox_send",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor.pause_for_parent",
        lambda *_args, **_kwargs: "stop",
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor._append_open_problem",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.service.excerpt_extractor._update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )

    result = extract_excerpts(
        section,
        planspace,
        codespace,
        "parent",
        {"setup": "glm"},
    )

    assert result is None
