from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.implementation.service import triage_orchestrator
from src.implementation.service.triage_orchestrator import run_impact_triage
from orchestrator.types import Section


def _make_section(planspace: Path) -> Section:
    section_path = planspace / "artifacts" / "sections" / "section-01.md"
    section_path.write_text("# Section 01\n", encoding="utf-8")
    return Section(number="01", path=section_path, solve_count=1)


def test_run_impact_triage_skips_when_notes_are_acknowledged_and_aligned(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace)
    triage_signal = planspace / "artifacts" / "signals" / "triage-01.json"
    triage_signal.write_text(
        json.dumps({
            "needs_replan": False,
            "needs_code_change": False,
            "acknowledge": [
                {
                    "note_id": "note-1",
                    "action": "accepted",
                    "reason": "already handled",
                },
            ],
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(triage_orchestrator, "dispatch_agent", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        triage_orchestrator,
        "_run_alignment_check_with_retries",
        lambda *args, **kwargs: "aligned",
    )
    monkeypatch.setattr(
        triage_orchestrator,
        "_parse_alignment_verdict",
        lambda *_args, **_kwargs: {"aligned": True, "frame_ok": True},
    )
    monkeypatch.setattr(
        triage_orchestrator,
        "collect_modified_files",
        lambda *_args, **_kwargs: ["src/main.py"],
    )

    status, modified_files = run_impact_triage(
        section,
        planspace,
        codespace,
        "parent",
        {"alignment": "align-model"},
        "**Note ID**: `note-1`\n",
    )

    assert status == "skip"
    assert modified_files == ["src/main.py"]
    ack_path = planspace / "artifacts" / "signals" / "note-ack-01.json"
    assert json.loads(ack_path.read_text(encoding="utf-8")) == {
        "acknowledged": [
            {
                "note_id": "note-1",
                "action": "accepted",
                "reason": "already handled",
            },
        ],
    }


def test_run_impact_triage_continues_when_not_all_notes_are_acknowledged(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace)
    triage_signal = planspace / "artifacts" / "signals" / "triage-01.json"
    triage_signal.write_text(
        json.dumps({
            "needs_replan": False,
            "needs_code_change": False,
            "acknowledge": [],
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(triage_orchestrator, "dispatch_agent", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        triage_orchestrator,
        "_run_alignment_check_with_retries",
        lambda *args, **kwargs: pytest.fail("alignment check should not run"),
    )

    status, modified_files = run_impact_triage(
        section,
        planspace,
        codespace,
        "parent",
        {"alignment": "align-model"},
        "**Note ID**: `note-1`\n",
    )

    assert status == "continue"
    assert modified_files is None


def test_run_impact_triage_aborts_when_alignment_changes_mid_check(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace)
    triage_signal = planspace / "artifacts" / "signals" / "triage-01.json"
    triage_signal.write_text(
        json.dumps({
            "needs_replan": False,
            "needs_code_change": False,
            "acknowledge": [
                {
                    "note_id": "note-1",
                    "action": "accepted",
                    "reason": "ok",
                },
            ],
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(triage_orchestrator, "dispatch_agent", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        triage_orchestrator,
        "_run_alignment_check_with_retries",
        lambda *args, **kwargs: "ALIGNMENT_CHANGED_PENDING",
    )

    status, modified_files = run_impact_triage(
        section,
        planspace,
        codespace,
        "parent",
        {"alignment": "align-model"},
        "**Note ID**: `note-1`\n",
    )

    assert status == "abort"
    assert modified_files is None
