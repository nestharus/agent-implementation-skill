"""Integration tests for change_detection module.

Pure file I/O — no mocks needed.
"""

from pathlib import Path

from section_loop.change_detection import diff_files, hash_file, snapshot_files


class TestHashFile:
    def test_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        h = hash_file(f)
        assert len(h) == 64  # SHA-256 hex digest
        assert h == hash_file(f)  # deterministic

    def test_missing_file(self, tmp_path: Path) -> None:
        assert hash_file(tmp_path / "nonexistent.txt") == ""

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("content A")
        b.write_text("content B")
        assert hash_file(a) != hash_file(b)

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("identical")
        b.write_text("identical")
        assert hash_file(a) == hash_file(b)


class TestSnapshotFiles:
    def test_snapshot_captures_hashes(self, codespace: Path) -> None:
        snap = snapshot_files(codespace, ["src/main.py", "src/utils.py"])
        assert "src/main.py" in snap
        assert "src/utils.py" in snap
        assert len(snap["src/main.py"]) == 64

    def test_snapshot_missing_file_returns_empty(self, codespace: Path) -> None:
        snap = snapshot_files(codespace, ["nonexistent.py"])
        assert snap["nonexistent.py"] == ""


class TestDiffFiles:
    def test_unchanged_files_excluded(self, codespace: Path) -> None:
        before = snapshot_files(codespace, ["src/main.py"])
        # File not modified
        changed = diff_files(codespace, before, ["src/main.py"])
        assert changed == []

    def test_changed_file_included(self, codespace: Path) -> None:
        before = snapshot_files(codespace, ["src/main.py"])
        (codespace / "src" / "main.py").write_text("modified content")
        changed = diff_files(codespace, before, ["src/main.py"])
        assert changed == ["src/main.py"]

    def test_new_file_included(self, codespace: Path) -> None:
        before = snapshot_files(codespace, ["src/new.py"])
        (codespace / "src" / "new.py").write_text("brand new")
        changed = diff_files(codespace, before, ["src/new.py"])
        assert changed == ["src/new.py"]

    def test_mixed_changed_and_unchanged(self, codespace: Path) -> None:
        before = snapshot_files(
            codespace, ["src/main.py", "src/utils.py"],
        )
        (codespace / "src" / "main.py").write_text("changed!")
        # utils.py stays the same
        changed = diff_files(
            codespace, before, ["src/main.py", "src/utils.py"],
        )
        assert changed == ["src/main.py"]
