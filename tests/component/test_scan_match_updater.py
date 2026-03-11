from __future__ import annotations

from src.scan.related.match_updater import (
    SUMMARY_BEGIN,
    SUMMARY_END,
    deep_scan_related_files,
    update_match,
)


def test_deep_scan_related_files_reads_related_entries(tmp_path) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text(
        "# Section\n\n## Related Files\n\n### src/main.py\n\n### src/util.py\n",
        encoding="utf-8",
    )

    assert deep_scan_related_files(section_file) == ["src/main.py", "src/util.py"]


def test_update_match_inserts_summary_block(tmp_path) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text(
        "# Section\n\n## Related Files\n\n### src/main.py\nOriginal\n",
        encoding="utf-8",
    )
    details_file = tmp_path / "deep-main-response.md"
    details_file.write_text("analysis", encoding="utf-8")
    feedback_file = tmp_path / "deep-main-feedback.json"
    feedback_file.write_text(
        '{"summary_lines":["Line A","Line B","Line C","Line D"]}',
        encoding="utf-8",
    )

    assert update_match(section_file, "src/main.py", details_file) is True

    text = section_file.read_text(encoding="utf-8")
    assert SUMMARY_BEGIN in text
    assert SUMMARY_END in text
    assert "> Line A" in text
    assert "> Line C" in text
    assert "> Line D" not in text


def test_update_match_renames_malformed_feedback(tmp_path) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text(
        "## Related Files\n\n### src/foo.py\nSome detail\n",
        encoding="utf-8",
    )
    details_file = tmp_path / "deep-src_foo_py-response.md"
    details_file.write_text("analysis", encoding="utf-8")
    feedback_file = tmp_path / "deep-src_foo_py-feedback.json"
    feedback_file.write_text("{bad json", encoding="utf-8")

    assert update_match(section_file, "src/foo.py", details_file) is True
    assert not feedback_file.exists()
    assert feedback_file.with_suffix(".malformed.json").exists()
