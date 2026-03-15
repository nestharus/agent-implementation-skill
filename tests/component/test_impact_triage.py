from __future__ import annotations

import json
from pathlib import Path

import pytest
from dependency_injector import providers

from containers import AgentDispatcher, PromptGuard, SectionAlignmentService, Services
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from implementation.service.triage_orchestrator import TriageOrchestrator
from orchestrator.types import Section


def _make_section(planspace: Path) -> Section:
    section_path = planspace / "artifacts" / "sections" / "section-01.md"
    section_path.write_text("# Section 01\n", encoding="utf-8")
    return Section(number="01", path=section_path, solve_count=1)


class _NoOpDispatcher(AgentDispatcher):
    def dispatch(self, *args, **kwargs):
        return ""


class _NoOpGuard(PromptGuard):
    def write_validated(self, content, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True

    def validate_dynamic(self, content):
        return []


def _make_triage_orchestrator() -> TriageOrchestrator:
    return TriageOrchestrator(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        dispatcher=Services.dispatcher(),
        logger=Services.logger(),
        policies=Services.policies(),
        prompt_guard=Services.prompt_guard(),
        section_alignment=Services.section_alignment(),
        task_router=Services.task_router(),
    )


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

    class _StubAlignment(SectionAlignmentService):
        def run_alignment_check(self, *_args, **_kwargs):
            return "aligned"

        def parse_alignment_verdict(self, *_args, **_kwargs):
            return {"aligned": True, "frame_ok": True}

        def collect_modified_files(self, *_args, **_kwargs):
            return ["src/main.py"]

    Services.dispatcher.override(providers.Object(_NoOpDispatcher()))
    Services.prompt_guard.override(providers.Object(_NoOpGuard()))
    Services.section_alignment.override(providers.Object(_StubAlignment()))

    try:
        status, modified_files = _make_triage_orchestrator().run_impact_triage(
            section,
            planspace,
            codespace,
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
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.section_alignment.reset_override()


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

    class _FailAlignment(SectionAlignmentService):
        def run_alignment_check(self, *_args, **_kwargs):
            pytest.fail("alignment check should not run")

    Services.dispatcher.override(providers.Object(_NoOpDispatcher()))
    Services.prompt_guard.override(providers.Object(_NoOpGuard()))
    Services.section_alignment.override(providers.Object(_FailAlignment()))

    try:
        status, modified_files = _make_triage_orchestrator().run_impact_triage(
            section,
            planspace,
            codespace,
            "**Note ID**: `note-1`\n",
        )

        assert status == "continue"
        assert modified_files is None
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.section_alignment.reset_override()


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

    class _AbortAlignment(SectionAlignmentService):
        def run_alignment_check(self, *_args, **_kwargs):
            return ALIGNMENT_CHANGED_PENDING

    Services.dispatcher.override(providers.Object(_NoOpDispatcher()))
    Services.prompt_guard.override(providers.Object(_NoOpGuard()))
    Services.section_alignment.override(providers.Object(_AbortAlignment()))

    try:
        status, modified_files = _make_triage_orchestrator().run_impact_triage(
            section,
            planspace,
            codespace,
            "**Note ID**: `note-1`\n",
        )

        assert status == "abort"
        assert modified_files is None
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.section_alignment.reset_override()
