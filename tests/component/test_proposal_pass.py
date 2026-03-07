from __future__ import annotations

from pathlib import Path

import pytest

from src.scripts.lib.pipelines import proposal_pass
from src.scripts.lib.pipelines.proposal_pass import ProposalPassExit, run_proposal_pass
from section_loop.types import ProposalPassResult, Section


def _planspace(tmp_path: Path) -> Path:
    planspace = tmp_path / "planspace"
    (planspace / "artifacts").mkdir(parents=True)
    return planspace


def test_run_proposal_pass_reexplores_then_records_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = _planspace(tmp_path)
    section_path = planspace / "artifacts" / "section-01.md"
    section_path.write_text("# Section 01\n", encoding="utf-8")
    section = Section(number="01", path=section_path, related_files=[])
    messages: list[tuple[Path, str, str]] = []

    monkeypatch.setattr(proposal_pass, "handle_pending_messages", lambda *args: False)
    monkeypatch.setattr(proposal_pass, "alignment_changed_pending", lambda *args: False)
    monkeypatch.setattr(
        proposal_pass,
        "_check_and_clear_alignment_changed",
        lambda *args: False,
    )
    monkeypatch.setattr(
        proposal_pass,
        "_reexplore_section",
        lambda *args, **kwargs: "ok",
    )
    monkeypatch.setattr(
        proposal_pass,
        "parse_related_files",
        lambda path: ["src/app.py"],
    )
    monkeypatch.setattr(
        proposal_pass,
        "run_section",
        lambda *args, **kwargs: ProposalPassResult(
            section_number="01",
            execution_ready=True,
        ),
    )
    monkeypatch.setattr(
        proposal_pass,
        "mailbox_send",
        lambda planspace_arg, parent, message: messages.append(
            (planspace_arg, parent, message),
        ),
    )
    monkeypatch.setattr(proposal_pass.subprocess, "run", lambda *args, **kwargs: None)

    results = run_proposal_pass(
        [section],
        {"01": section},
        planspace,
        tmp_path / "codespace",
        "parent",
        {"setup": "test-model"},
    )

    assert results["01"].execution_ready is True
    assert section.related_files == ["src/app.py"]
    assert messages == [(planspace, "parent", "proposal-done:01:ready")]


def test_run_proposal_pass_raises_on_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = _planspace(tmp_path)
    section = Section(number="01", path=planspace / "artifacts" / "section-01.md")
    sent: list[str] = []

    monkeypatch.setattr(proposal_pass, "handle_pending_messages", lambda *args: True)
    monkeypatch.setattr(
        proposal_pass,
        "mailbox_send",
        lambda _planspace, _parent, message: sent.append(message),
    )

    with pytest.raises(ProposalPassExit):
        run_proposal_pass(
            [section],
            {"01": section},
            planspace,
            tmp_path / "codespace",
            "parent",
            {"setup": "test-model"},
        )

    assert sent == ["fail:aborted"]
