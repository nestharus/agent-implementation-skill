from __future__ import annotations

from pathlib import Path

import pytest

from src.staleness import global_alignment_recheck
from src.staleness.global_alignment_recheck import run_global_alignment_recheck
from orchestrator.types import Section, SectionResult


def _make_section(planspace: Path, number: str) -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=section_path)


def test_run_global_alignment_recheck_skips_unchanged_aligned_sections(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")
    section_results = {"01": SectionResult(section_number="01", aligned=True)}
    hash_path = planspace / "artifacts" / "phase2-inputs-hashes" / "01.hash"
    hash_path.parent.mkdir(parents=True, exist_ok=True)
    hash_path.write_text("hash-1", encoding="utf-8")

    monkeypatch.setattr(
        global_alignment_recheck,
        "_section_inputs_hash",
        lambda *_args, **_kwargs: "hash-1",
    )
    monkeypatch.setattr(
        global_alignment_recheck,
        "_run_alignment_check_with_retries",
        lambda *_args, **_kwargs: pytest.fail("alignment check should not run"),
    )

    status = run_global_alignment_recheck(
        {"01": section},
        section_results,
        planspace,
        codespace,
        "parent",
        {"alignment": "align-model"},
    )

    assert status == "all_aligned"


def test_run_global_alignment_recheck_marks_invalid_frame_and_preserves_files(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")
    section_results = {
        "01": SectionResult(
            section_number="01",
            aligned=True,
            modified_files=["src/main.py"],
        ),
    }
    messages: list[str] = []

    monkeypatch.setattr(
        global_alignment_recheck,
        "_section_inputs_hash",
        lambda *_args, **_kwargs: "hash-1",
    )
    monkeypatch.setattr(
        global_alignment_recheck,
        "poll_control_messages",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        global_alignment_recheck,
        "read_incoming_notes",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        global_alignment_recheck,
        "_run_alignment_check_with_retries",
        lambda *_args, **_kwargs: "INVALID_FRAME",
    )
    monkeypatch.setattr(
        global_alignment_recheck,
        "mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
    )

    status = run_global_alignment_recheck(
        {"01": section},
        section_results,
        planspace,
        codespace,
        "parent",
        {"alignment": "align-model"},
    )

    assert status == "has_problems"
    assert messages == ["fail:invalid_alignment_frame:01"]
    assert section_results["01"].aligned is False
    assert section_results["01"].modified_files == ["src/main.py"]


def test_run_global_alignment_recheck_restarts_when_control_message_arrives(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")

    monkeypatch.setattr(
        global_alignment_recheck,
        "_section_inputs_hash",
        lambda *_args, **_kwargs: "hash-1",
    )
    monkeypatch.setattr(
        global_alignment_recheck,
        "poll_control_messages",
        lambda *_args, **_kwargs: "alignment_changed",
    )

    status = run_global_alignment_recheck(
        {"01": section},
        {},
        planspace,
        codespace,
        "parent",
        {"alignment": "align-model"},
    )

    assert status == "restart_phase1"
