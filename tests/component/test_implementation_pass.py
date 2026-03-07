from pathlib import Path

import pytest

from lib.pipelines.implementation_pass import (
    ImplementationPassExit,
    ImplementationPassRestart,
    run_implementation_pass,
)
from section_loop.types import ProposalPassResult, Section


def _make_section(planspace: Path, number: str) -> Section:
    path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=path, related_files=["src/app.py"])


def test_run_implementation_pass_records_results_and_hashes(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")
    messages: list[str] = []

    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.handle_pending_messages",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.alignment_changed_pending",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._check_and_clear_alignment_changed",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.run_section",
        lambda *args, **kwargs: ["src/app.py"],
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._section_inputs_hash",
        lambda *args: "hash-123",
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.subprocess.run",
        lambda *args, **kwargs: None,
    )

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert results["01"].modified_files == ["src/app.py"]
    assert messages == ["done:01:1 files modified"]
    assert (planspace / "artifacts" / "section-inputs-hashes" / "01.hash").read_text(
        encoding="utf-8",
    ) == "hash-123"
    assert (planspace / "artifacts" / "phase2-inputs-hashes" / "01.hash").read_text(
        encoding="utf-8",
    ) == "hash-123"


def test_run_implementation_pass_restarts_on_alignment_change(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")

    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.handle_pending_messages",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.alignment_changed_pending",
        lambda *args: True,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._check_and_clear_alignment_changed",
        lambda *args: True,
    )

    with pytest.raises(ImplementationPassRestart):
        run_implementation_pass(
            {"01": ProposalPassResult(section_number="01", execution_ready=True)},
            {"01": section},
            planspace,
            codespace,
            "parent",
        )


def test_run_implementation_pass_exits_when_parent_aborts(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")
    messages: list[str] = []

    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.handle_pending_messages",
        lambda *args: True,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
    )

    with pytest.raises(ImplementationPassExit):
        run_implementation_pass(
            {"01": ProposalPassResult(section_number="01", execution_ready=True)},
            {"01": section},
            planspace,
            codespace,
            "parent",
        )

    assert messages == ["fail:aborted"]
