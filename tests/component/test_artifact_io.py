"""Component tests for ArtifactIO: JSON file read/write with corruption preservation."""

from __future__ import annotations

import json

from src.signals.artifact_io import (
    read_json,
    read_json_or_default,
    rename_malformed,
    write_json,
)


# --- read_json ---


def test_read_json_valid_file(tmp_path):
    """read_json returns parsed data from a valid JSON file."""
    path = tmp_path / "data.json"
    expected = {"key": "value", "count": 42}
    path.write_text(json.dumps(expected), encoding="utf-8")

    result = read_json(path)

    assert result == expected


def test_read_json_missing_file(tmp_path):
    """read_json returns None for a file that does not exist."""
    path = tmp_path / "nonexistent.json"

    result = read_json(path)

    assert result is None


def test_read_json_corrupt_json_renames(tmp_path):
    """read_json returns None for corrupt JSON and renames to .malformed.json."""
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")

    result = read_json(path)

    assert result is None
    assert not path.exists()
    malformed = tmp_path / "bad.malformed.json"
    assert malformed.exists()
    assert malformed.read_text(encoding="utf-8") == "{not valid json"


def test_read_json_html_content(tmp_path):
    """read_json returns None for a file containing HTML instead of JSON."""
    path = tmp_path / "page.json"
    path.write_text("<html><body>Not JSON</body></html>", encoding="utf-8")

    result = read_json(path)

    assert result is None
    assert not path.exists()
    assert (tmp_path / "page.malformed.json").exists()


def test_read_json_plain_text(tmp_path):
    """read_json returns None for a file containing plain text."""
    path = tmp_path / "notes.json"
    path.write_text("Just some plain text, no JSON here.", encoding="utf-8")

    result = read_json(path)

    assert result is None
    assert not path.exists()
    assert (tmp_path / "notes.malformed.json").exists()


def test_read_json_empty_file(tmp_path):
    """read_json returns None for an empty file and renames it."""
    path = tmp_path / "empty.json"
    path.write_text("", encoding="utf-8")

    result = read_json(path)

    assert result is None
    assert not path.exists()
    assert (tmp_path / "empty.malformed.json").exists()


def test_read_json_array(tmp_path):
    """read_json handles JSON arrays (not just dicts)."""
    path = tmp_path / "list.json"
    expected = [1, "two", {"three": 3}]
    path.write_text(json.dumps(expected), encoding="utf-8")

    result = read_json(path)

    assert result == expected


# --- write_json ---


def test_write_json_creates_file(tmp_path):
    """write_json creates a file with correct JSON content."""
    path = tmp_path / "output.json"
    data = {"status": "ok", "items": [1, 2, 3]}

    write_json(path, data)

    assert path.exists()
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed == data


def test_write_json_creates_parent_dirs(tmp_path):
    """write_json creates parent directories if they don't exist."""
    path = tmp_path / "deep" / "nested" / "dir" / "output.json"
    data = {"nested": True}

    write_json(path, data)

    assert path.exists()
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed == data


def test_write_json_overwrites_existing(tmp_path):
    """write_json overwrites an existing file."""
    path = tmp_path / "data.json"
    path.write_text(json.dumps({"old": True}), encoding="utf-8")

    write_json(path, {"new": True})

    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed == {"new": True}


# --- rename_malformed ---


def test_rename_malformed_renames_file(tmp_path):
    """rename_malformed renames file and returns the new path."""
    path = tmp_path / "broken.json"
    path.write_text("corrupt", encoding="utf-8")

    result = rename_malformed(path)

    expected_path = tmp_path / "broken.malformed.json"
    assert result == expected_path
    assert expected_path.exists()
    assert not path.exists()
    assert expected_path.read_text(encoding="utf-8") == "corrupt"


def test_rename_malformed_missing_file(tmp_path):
    """rename_malformed returns None for a file that doesn't exist."""
    path = tmp_path / "ghost.json"

    result = rename_malformed(path)

    assert result is None


# --- read_json_or_default ---


def test_read_json_or_default_returns_data(tmp_path):
    """read_json_or_default returns parsed data when file exists and is valid."""
    path = tmp_path / "config.json"
    expected = {"setting": "on"}
    path.write_text(json.dumps(expected), encoding="utf-8")

    result = read_json_or_default(path, {"setting": "off"})

    assert result == expected


def test_read_json_or_default_missing_returns_default(tmp_path):
    """read_json_or_default returns default when file is missing."""
    path = tmp_path / "missing.json"
    default = {"fallback": True}

    result = read_json_or_default(path, default)

    assert result == default


def test_read_json_or_default_corrupt_returns_default(tmp_path):
    """read_json_or_default returns default when file is corrupt, renames to .malformed.json."""
    path = tmp_path / "bad_config.json"
    path.write_text("not json at all", encoding="utf-8")
    default = {"safe": "default"}

    result = read_json_or_default(path, default)

    assert result == default
    assert not path.exists()
    assert (tmp_path / "bad_config.malformed.json").exists()
