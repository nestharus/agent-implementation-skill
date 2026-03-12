"""Component tests for shared scan related-files helpers."""

from __future__ import annotations

from types import SimpleNamespace

from dependency_injector import providers

from containers import PromptGuard, Services
from src.signals.repository.artifact_io import write_json
from src.staleness.helpers.hashing import content_hash, file_hash
from src.scan.related.discovery import (
    apply_related_files_update,
    list_section_files,
    validate_existing_related_files,
)
from src.scan.codemap.cache import strip_scan_summaries


def test_list_section_files_filters_and_sorts(tmp_path) -> None:
    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    (sections_dir / "section-10.md").write_text("ten\n", encoding="utf-8")
    (sections_dir / "section-02.md").write_text("two\n", encoding="utf-8")
    (sections_dir / "notes.md").write_text("ignore\n", encoding="utf-8")
    (sections_dir / "section-x.md").write_text("ignore\n", encoding="utf-8")

    files = list_section_files(sections_dir)

    assert [path.name for path in files] == ["section-02.md", "section-10.md"]


def test_apply_related_files_update_updates_related_files_block(tmp_path) -> None:
    section_file = tmp_path / "section-03.md"
    section_file.write_text(
        "# Section 03\n\n"
        "## Related Files\n\n"
        "### src/keep.py\n"
        "Keep this entry.\n\n"
        "### src/remove.py\n"
        "Remove this entry.\n\n"
        "## Notes\n\n"
        "Tail content.\n",
        encoding="utf-8",
    )
    signal_file = tmp_path / "section-03-related-files-update.json"
    write_json(
        signal_file,
        {
            "status": "stale",
            "removals": ["src/remove.py"],
            "additions": ["src/add.py"],
        },
    )

    applied = apply_related_files_update(section_file, signal_file)
    updated = section_file.read_text(encoding="utf-8")

    assert applied is True
    assert "### src/remove.py" not in updated
    assert "### src/add.py" in updated
    assert "## Notes" in updated


def test_apply_related_files_update_returns_false_for_malformed_signal(
    tmp_path,
) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text("# Section 01\n\n## Related Files\n", encoding="utf-8")
    signal_file = tmp_path / "bad-signal.json"
    signal_file.write_text("{not json", encoding="utf-8")

    applied = apply_related_files_update(section_file, signal_file)

    assert applied is False
    assert not signal_file.exists()
    assert signal_file.with_suffix(".malformed.json").exists()


def test_validate_existing_related_files_skips_when_inputs_unchanged(
    tmp_path, monkeypatch,
) -> None:
    # Use proper planspace/artifacts layout so PathRegistry works
    planspace = tmp_path / "planspace"
    artifacts_dir = planspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    section_file = artifacts_dir / "section-07.md"
    section_file.write_text(
        "# Section 07\n\n## Related Files\n\n### src/app.py\nBody.\n",
        encoding="utf-8",
    )
    codemap_path = artifacts_dir / "codemap.md"
    codemap_path.write_text("codemap\n", encoding="utf-8")
    corrections_file = artifacts_dir / "signals" / "codemap-corrections.json"
    corrections_file.parent.mkdir(parents=True, exist_ok=True)
    write_json(corrections_file, {"fixes": []})
    scan_log_dir = tmp_path / "scan-logs"
    section_log = scan_log_dir / "section-07"
    section_log.mkdir(parents=True, exist_ok=True)

    # Create the codespace file so it passes existence check
    codespace = tmp_path / "codespace"
    (codespace / "src").mkdir(parents=True, exist_ok=True)
    (codespace / "src" / "app.py").write_text("# app\n", encoding="utf-8")

    combined_hash = content_hash(
        ":".join(
            [
                file_hash(codemap_path),
                file_hash(corrections_file),
                content_hash(strip_scan_summaries(section_file.read_text())),
            ],
        ),
    )
    (section_log / "codemap-hash.txt").write_text(combined_hash, encoding="utf-8")

    # Seed a valid cached signal so skip gate accepts the cached hash
    signals_dir = artifacts_dir / "signals"
    write_json(
        signals_dir / "section-07-related-files-update.json",
        {"status": "current", "additions": [], "removals": [], "reason": "ok"},
    )

    def fail_dispatch(**kwargs):
        raise AssertionError("dispatch_agent should not run when hash matches")

    monkeypatch.setattr(
        "src.scan.related.discovery.dispatch_agent",
        fail_dispatch,
    )

    validate_existing_related_files(
        section_file=section_file,
        section_name="section-07",
        codemap_path=codemap_path,
        codespace=codespace,
        artifacts_dir=artifacts_dir,
        scan_log_dir=scan_log_dir,
        corrections_file=corrections_file,
        model_policy={"validation": "test-model"},
    )

    assert (
        section_log / "codemap-hash.txt"
    ).read_text(encoding="utf-8").strip() == combined_hash


def test_validate_existing_related_files_applies_stale_signal_and_updates_hash(
    tmp_path, monkeypatch,
) -> None:
    # Use proper planspace/artifacts layout so PathRegistry works
    planspace = tmp_path / "planspace"
    artifacts_dir = planspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    section_file = artifacts_dir / "section-08.md"
    section_file.write_text(
        "# Section 08\n\n"
        "## Related Files\n\n"
        "### src/old.py\n"
        "Old entry.\n",
        encoding="utf-8",
    )
    codemap_path = artifacts_dir / "codemap.md"
    codemap_path.write_text("codemap\n", encoding="utf-8")
    (artifacts_dir / "signals").mkdir(parents=True, exist_ok=True)
    scan_log_dir = tmp_path / "scan-logs"
    codespace = tmp_path / "codespace"
    # Create the addition target so normalizer accepts it
    (codespace / "src").mkdir(parents=True, exist_ok=True)
    (codespace / "src" / "new.py").write_text("# new\n", encoding="utf-8")
    update_signal = (
        artifacts_dir / "signals" / "section-08-related-files-update.json"
    )
    write_json(
        update_signal,
        {
            "status": "stale",
            "removals": ["src/old.py"],
            "additions": ["src/new.py"],
        },
    )

    monkeypatch.setattr(
        "src.scan.related.discovery.load_scan_template",
        lambda name: (
            "Section: {section_file}\n"
            "Codemap: {codemap_path}\n"
            "{corrections_ref}\n"
            "{missing_existing_section}\n"
            "Signal: {update_signal}\n"
        ),
    )
    class _NoopGuard(PromptGuard):
        def validate_dynamic(self, content):
            return []
        def write_validated(self, content, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return True

    Services.prompt_guard.override(providers.Object(_NoopGuard()))

    def mock_dispatch(**kwargs):
        # Simulate agent writing the signal file
        write_json(
            update_signal,
            {
                "status": "stale",
                "removals": ["src/old.py"],
                "additions": ["src/new.py"],
            },
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "src.scan.related.discovery.dispatch_agent",
        mock_dispatch,
    )

    try:
        validate_existing_related_files(
            section_file=section_file,
            section_name="section-08",
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            corrections_file=artifacts_dir / "signals" / "codemap-corrections.json",
            model_policy={"validation": "test-model"},
        )

        updated_text = section_file.read_text(encoding="utf-8")
        saved_signal = update_signal.read_text(encoding="utf-8")
        section_log = scan_log_dir / "section-08"

        assert "### src/old.py" not in updated_text
        assert "### src/new.py" in updated_text
        assert '"status": "applied"' in saved_signal
        assert (section_log / "codemap-hash.txt").exists()
    finally:
        Services.prompt_guard.reset_override()
