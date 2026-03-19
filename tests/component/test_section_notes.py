from __future__ import annotations

from pathlib import Path

import pytest
from dependency_injector import providers

from conftest import NoOpChangeTracker
from containers import ChangeTrackerService, Services
from coordination.service.completion_handler import CompletionHandler
from implementation.service.impact_analyzer import ImpactAnalyzer
from orchestrator.types import Section

from src.coordination.service import completion_handler as section_notes
from src.orchestrator.path_registry import PathRegistry


def _make_completion_handler() -> CompletionHandler:
    return CompletionHandler(
        artifact_io=Services.artifact_io(),
        change_tracker=Services.change_tracker(),
        communicator=Services.communicator(),
        flow_submitter=Services.flow_ingestion()._get_submitter(),
        hasher=Services.hasher(),
        impact_analyzer=ImpactAnalyzer(
            communicator=Services.communicator(),
            config=Services.config(),
            context_assembly=Services.context_assembly(),
            cross_section=Services.cross_section(),
            dispatcher=Services.dispatcher(),
            logger=Services.logger(),
            policies=Services.policies(),
            prompt_guard=Services.prompt_guard(),
            task_router=Services.task_router(),
        ),
        logger=Services.logger(),
    )


def _make_section(tmp_path: Path, number: str = "01") -> tuple[Path, Path, Section]:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir(exist_ok=True)
    PathRegistry(planspace).ensure_artifacts_tree()
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text("# Section\n\nAuth summary\n", encoding="utf-8")
    return planspace, codespace, Section(
        number=number,
        path=section_path,
        related_files=["src/app.py"],
    )


def test_read_incoming_notes_filters_resolved_notes_and_includes_diff(
    tmp_path: Path,
) -> None:
    planspace, codespace, section = _make_section(tmp_path)
    notes_dir = planspace / "artifacts" / "notes"
    (notes_dir / "from-02-to-01.md").write_text(
        "**Note ID**: `keep-me`\n\nActive note",
        encoding="utf-8",
    )
    (notes_dir / "from-03-to-01.md").write_text(
        "**Note ID**: `skip-me`\n\nResolved note",
        encoding="utf-8",
    )
    ack_path = planspace / "artifacts" / "signals" / "note-ack-01.json"
    ack_path.write_text(
        '{"acknowledged": [{"note_id": "skip-me", "action": "accepted"}]}',
        encoding="utf-8",
    )
    snapshot_file = (
        planspace / "artifacts" / "snapshots" / "section-02" / "src" / "app.py"
    )
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot_file.write_text("old line\n", encoding="utf-8")
    current_file = codespace / "src" / "app.py"
    current_file.parent.mkdir(parents=True, exist_ok=True)
    current_file.write_text("new line\n", encoding="utf-8")

    notes = _make_completion_handler().read_incoming_notes(section, planspace, codespace)

    assert "Active note" in notes
    assert "Resolved note" not in notes
    assert "### File Diffs Since Section 02" in notes
    assert "+new line" in notes


def test_read_incoming_notes_renames_malformed_ack_file(
    tmp_path: Path,
) -> None:
    planspace, codespace, section = _make_section(tmp_path)
    notes_dir = planspace / "artifacts" / "notes"
    (notes_dir / "from-02-to-01.md").write_text(
        "**Note ID**: `keep-me`\n\nActive note",
        encoding="utf-8",
    )
    ack_path = planspace / "artifacts" / "signals" / "note-ack-01.json"
    ack_path.write_text("not json", encoding="utf-8")

    notes = _make_completion_handler().read_incoming_notes(section, planspace, codespace)

    assert "Active note" in notes
    assert not ack_path.exists()
    assert ack_path.with_suffix(".malformed.json").exists()


def test_post_section_completion_writes_note_and_contract_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, codespace, section = _make_section(tmp_path)
    target = Section(
        number="02",
        path=planspace / "artifacts" / "sections" / "section-02.md",
        related_files=["src/app.py"],
    )
    target.path.write_text("# Section\n\nBilling summary\n", encoding="utf-8")
    source_file = codespace / "src" / "app.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("content\n", encoding="utf-8")
    baseline_hash = planspace / "artifacts" / "section-inputs-hashes" / "02.hash"
    baseline_hash.parent.mkdir(parents=True, exist_ok=True)
    baseline_hash.write_text("hash\n", encoding="utf-8")
    flag_calls: list[Path] = []

    class _CapturingChangeTracker(ChangeTrackerService):
        def set_flag(self, planspace_arg):
            flag_calls.append(planspace_arg)

    monkeypatch.setattr(
        section_notes,
        "snapshot_modified_files",
        lambda *args, **kwargs: planspace / "artifacts" / "snapshots" / "section-01",
    )
    monkeypatch.setattr(
        ImpactAnalyzer,
        "analyze_impacts",
        lambda self, *args, **kwargs: [
            ("02", "Shared contract changed", True, "## Contract Delta\nChanged")
        ],
    )
    Services.change_tracker.override(providers.Object(_CapturingChangeTracker()))

    try:
        _make_completion_handler().post_section_completion(
            section,
            ["src/app.py"],
            [section, target],
            planspace,
            codespace,
        )

        note_path = planspace / "artifacts" / "notes" / "from-01-to-02.md"
        contract_path = planspace / "artifacts" / "contracts" / "contract-01-02.md"
        assert note_path.exists()
        assert "Shared contract changed" in note_path.read_text(encoding="utf-8")
        assert contract_path.exists()
        assert "src/app.py" in contract_path.read_text(encoding="utf-8")
        assert flag_calls == [planspace]
    finally:
        Services.change_tracker.reset_override()
