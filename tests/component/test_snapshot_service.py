"""Component tests for snapshot_service."""

from __future__ import annotations

from src.implementation.service.snapshot import (
    compute_text_diff,
    snapshot_modified_files,
)


def test_snapshot_modified_files_copies_nested_paths(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    nested = codespace / "pkg" / "module.py"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("print('ok')\n", encoding="utf-8")

    snapshot_dir = snapshot_modified_files(
        planspace,
        "07",
        codespace,
        ["pkg/module.py"],
    )

    copied = snapshot_dir / "pkg" / "module.py"
    assert copied.exists()
    assert copied.read_text(encoding="utf-8") == "print('ok')\n"


def test_snapshot_modified_files_skips_escaping_paths_and_warns(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")

    warnings: list[str] = []
    snapshot_dir = snapshot_modified_files(
        planspace,
        "03",
        codespace,
        ["../outside.txt"],
        warn=warnings.append,
    )

    assert warnings == ["snapshot path escapes codespace, skipping: ../outside.txt"]
    assert list(snapshot_dir.rglob("*")) == []


def test_compute_text_diff_returns_empty_when_both_missing(tmp_path) -> None:
    assert compute_text_diff(tmp_path / "a.txt", tmp_path / "b.txt") == ""


def test_compute_text_diff_returns_unified_diff_for_changes(tmp_path) -> None:
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("line one\nline two\n", encoding="utf-8")
    new.write_text("line one\nline three\n", encoding="utf-8")

    diff = compute_text_diff(old, new)

    assert "-line two" in diff
    assert "+line three" in diff
