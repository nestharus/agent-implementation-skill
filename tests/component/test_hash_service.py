"""Component tests for HashService."""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.scripts.lib.hash_service import content_hash, file_hash, fingerprint


class TestFileHash:
    """Tests for file_hash()."""

    def test_correct_sha256_for_known_content(self, tmp_path: Path) -> None:
        p = tmp_path / "hello.txt"
        p.write_text("hello world", encoding="utf-8")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert file_hash(p) == expected

    def test_returns_empty_string_for_missing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "does-not-exist.txt"
        assert file_hash(p) == ""

    def test_handles_binary_files(self, tmp_path: Path) -> None:
        p = tmp_path / "binary.bin"
        data = bytes(range(256))
        p.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert file_hash(p) == expected

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.txt"
        p.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert file_hash(p) == expected


class TestContentHash:
    """Tests for content_hash()."""

    def test_hashes_string_correctly(self) -> None:
        expected = hashlib.sha256(b"test string").hexdigest()
        assert content_hash("test string") == expected

    def test_hashes_bytes_correctly(self) -> None:
        data = b"\x00\x01\x02\xff"
        expected = hashlib.sha256(data).hexdigest()
        assert content_hash(data) == expected

    def test_str_and_bytes_same_content_produce_same_hash(self) -> None:
        text = "identical content"
        assert content_hash(text) == content_hash(text.encode("utf-8"))


class TestFingerprint:
    """Tests for fingerprint()."""

    def test_deterministic_hash(self) -> None:
        items = ["alpha", "beta", "gamma"]
        h1 = fingerprint(items)
        h2 = fingerprint(items)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest length

    def test_order_does_not_matter(self) -> None:
        assert fingerprint(["b", "a", "c"]) == fingerprint(["c", "a", "b"])

    def test_empty_list_produces_consistent_hash(self) -> None:
        h1 = fingerprint([])
        h2 = fingerprint([])
        assert h1 == h2
        # Empty join produces empty string; hash of empty string is well-defined
        assert h1 == content_hash("")

    def test_duplicate_items_preserved(self) -> None:
        # Duplicates are NOT deduplicated — ["a", "a"] differs from ["a"]
        assert fingerprint(["a", "a"]) != fingerprint(["a"])
