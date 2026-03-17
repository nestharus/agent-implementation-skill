from __future__ import annotations

from pathlib import Path

import pytest
from dependency_injector import providers

from conftest import StubPolicies, NoOpSectionAlignment
from containers import Services
from coordination.service.completion_handler import CompletionHandler
from signals.repository.artifact_io import write_json
from src.orchestrator.path_registry import PathRegistry
from src.staleness.service import global_alignment_rechecker
from src.staleness.service.global_alignment_rechecker import run_global_alignment_recheck
from orchestrator.types import Section, SectionResult


@pytest.fixture(autouse=True)
def _stub_policies():
    Services.policies.override(providers.Object(StubPolicies()))
    yield
    Services.policies.reset_override()


def _make_section(planspace: Path, number: str) -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=section_path)


def test_run_global_alignment_recheck_skips_unchanged_aligned_sections(
    planspace: Path,
    codespace: Path,
    noop_pipeline_control,
) -> None:
    section = _make_section(planspace, "01")
    section_results = {"01": SectionResult(section_number="01", aligned=True)}
    hash_path = planspace / "artifacts" / "phase2-inputs-hashes" / "01.hash"
    hash_path.parent.mkdir(parents=True, exist_ok=True)
    hash_path.write_text("hash-1", encoding="utf-8")

    noop_pipeline_control.section_inputs_hash = lambda *_args, **_kwargs: "hash-1"

    class _FailAlignment(NoOpSectionAlignment):
        def run_alignment_check(self, *_args, **_kwargs):
            pytest.fail("alignment check should not run")

    Services.section_alignment.override(providers.Object(_FailAlignment()))
    try:
        status = run_global_alignment_recheck(
            {"01": section},
            section_results,
            planspace,
            codespace,
        )
    finally:
        Services.section_alignment.reset_override()

    assert status == "all_aligned"


def test_run_global_alignment_recheck_marks_invalid_frame_and_preserves_files(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    capturing_communicator,
) -> None:
    from src.containers import SectionAlignmentService

    section = _make_section(planspace, "01")
    section_results = {
        "01": SectionResult(
            section_number="01",
            aligned=True,
            modified_files=["src/main.py"],
        ),
    }

    noop_pipeline_control.section_inputs_hash = lambda *_args, **_kwargs: "hash-1"
    monkeypatch.setattr(
        CompletionHandler,
        "read_incoming_notes",
        lambda self, *_args, **_kwargs: "",
    )

    class _InvalidFrameChecker:
        def run_alignment_check_with_retries(self, *_args, **_kwargs):
            return "INVALID_FRAME"
        def extract_problems(self, *_args, **_kwargs):
            return None

    class _InvalidFrameAlignment(SectionAlignmentService):
        def _get_checker(self):
            return _InvalidFrameChecker()

    Services.section_alignment.override(providers.Object(_InvalidFrameAlignment()))
    try:
        status = run_global_alignment_recheck(
            {"01": section},
            section_results,
            planspace,
            codespace,
        )
    finally:
        Services.section_alignment.reset_override()

    assert status == "has_problems"
    assert capturing_communicator.messages == ["fail:invalid_alignment_frame:01"]
    assert section_results["01"].aligned is False
    assert section_results["01"].modified_files == ["src/main.py"]


def test_run_global_alignment_recheck_restarts_when_control_message_arrives(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capturing_pipeline_control,
) -> None:
    section = _make_section(planspace, "01")

    capturing_pipeline_control._section_inputs_hash_return = "hash-1"

    capturing_pipeline_control._poll_return = "alignment_changed"

    status = run_global_alignment_recheck(
        {"01": section},
        {},
        planspace,
        codespace,
    )

    assert status == "restart_phase1"


# ---------------------------------------------------------------------------
# Verification gate integration in Phase 2 (PRB-0008 Item 15)
# ---------------------------------------------------------------------------


def test_global_recheck_blocks_aligned_section_when_verification_findings(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
) -> None:
    """Phase 2 recheck: alignment passes but verification_status=findings_local -> aligned=False."""
    section = _make_section(planspace, "01")
    section_results = {"01": SectionResult(section_number="01", aligned=True)}

    noop_pipeline_control.section_inputs_hash = lambda *_args, **_kwargs: "hash-new"

    monkeypatch.setattr(
        CompletionHandler,
        "read_incoming_notes",
        lambda self, *_args, **_kwargs: "",
    )

    class _AlignedChecker:
        def run_alignment_check_with_retries(self, *_args, **_kwargs):
            return "all aligned"
        def extract_problems(self, *_args, **_kwargs):
            return None

    class _AlignedAlignment:
        def _get_checker(self):
            return _AlignedChecker()

    Services.section_alignment.override(providers.Object(_AlignedAlignment()))

    # Write verification_status with findings_local
    paths = PathRegistry(planspace)
    write_json(paths.verification_status("01"), {
        "section": "01",
        "source": "verification.structural",
        "status": "findings_local",
        "error_count": 1,
    })

    try:
        status = run_global_alignment_recheck(
            {"01": section},
            section_results,
            planspace,
            codespace,
        )
    finally:
        Services.section_alignment.reset_override()

    assert status == "has_problems"
    assert section_results["01"].aligned is False
    assert "verification gate" in (section_results["01"].problems or "")


def test_global_recheck_allows_aligned_when_verification_passes(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
) -> None:
    """Phase 2 recheck: alignment passes and verification_status=pass -> aligned=True."""
    section = _make_section(planspace, "01")
    section_results = {"01": SectionResult(section_number="01", aligned=True)}

    noop_pipeline_control.section_inputs_hash = lambda *_args, **_kwargs: "hash-new2"

    monkeypatch.setattr(
        CompletionHandler,
        "read_incoming_notes",
        lambda self, *_args, **_kwargs: "",
    )

    class _AlignedChecker:
        def run_alignment_check_with_retries(self, *_args, **_kwargs):
            return "all aligned"
        def extract_problems(self, *_args, **_kwargs):
            return None

    class _AlignedAlignment:
        def _get_checker(self):
            return _AlignedChecker()

    Services.section_alignment.override(providers.Object(_AlignedAlignment()))

    paths = PathRegistry(planspace)
    write_json(paths.verification_status("01"), {
        "section": "01",
        "source": "verification.structural",
        "status": "pass",
    })

    try:
        status = run_global_alignment_recheck(
            {"01": section},
            section_results,
            planspace,
            codespace,
        )
    finally:
        Services.section_alignment.reset_override()

    assert status == "all_aligned"
    assert section_results["01"].aligned is True


def test_global_recheck_preserves_files_when_verification_gate_blocks(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
) -> None:
    """Verification gate block preserves modified_files from prior result."""
    section = _make_section(planspace, "01")
    section_results = {
        "01": SectionResult(
            section_number="01",
            aligned=True,
            modified_files=["src/main.py"],
        ),
    }

    noop_pipeline_control.section_inputs_hash = lambda *_args, **_kwargs: "hash-new3"

    monkeypatch.setattr(
        CompletionHandler,
        "read_incoming_notes",
        lambda self, *_args, **_kwargs: "",
    )

    class _AlignedChecker:
        def run_alignment_check_with_retries(self, *_args, **_kwargs):
            return "all aligned"
        def extract_problems(self, *_args, **_kwargs):
            return None

    class _AlignedAlignment:
        def _get_checker(self):
            return _AlignedChecker()

    Services.section_alignment.override(providers.Object(_AlignedAlignment()))

    paths = PathRegistry(planspace)
    write_json(paths.verification_status("01"), {
        "section": "01",
        "source": "verification.structural",
        "status": "findings_local",
        "error_count": 1,
    })

    try:
        status = run_global_alignment_recheck(
            {"01": section},
            section_results,
            planspace,
            codespace,
        )
    finally:
        Services.section_alignment.reset_override()

    assert section_results["01"].aligned is False
    assert section_results["01"].modified_files == ["src/main.py"]
