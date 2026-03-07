"""Component tests for dispatch metadata sidecars."""

from __future__ import annotations

import json

from src.scripts.lib.dispatch.dispatch_metadata import (
    DISPATCH_META_CORRUPT,
    dispatch_meta_path,
    read_dispatch_metadata,
    write_dispatch_metadata,
)


def test_dispatch_meta_path_uses_output_stem(tmp_path) -> None:
    output_path = tmp_path / "task-7-output.md"

    assert dispatch_meta_path(output_path) == tmp_path / "task-7-output.meta.json"


def test_write_dispatch_metadata_creates_sidecar(tmp_path) -> None:
    output_path = tmp_path / "task-1-output.md"

    meta_path = write_dispatch_metadata(
        output_path, returncode=0, timed_out=False,
    )

    assert meta_path.exists()
    assert json.loads(meta_path.read_text(encoding="utf-8")) == {
        "returncode": 0,
        "timed_out": False,
    }


def test_read_dispatch_metadata_returns_none_when_missing(tmp_path) -> None:
    assert read_dispatch_metadata(tmp_path / "missing.meta.json") is None


def test_read_dispatch_metadata_returns_parsed_dict(tmp_path) -> None:
    meta_path = tmp_path / "task-2-output.meta.json"
    meta_path.write_text(
        json.dumps({"returncode": 1, "timed_out": False}),
        encoding="utf-8",
    )

    assert read_dispatch_metadata(meta_path) == {
        "returncode": 1,
        "timed_out": False,
    }


def test_read_dispatch_metadata_marks_malformed_json_as_corrupt(tmp_path) -> None:
    meta_path = tmp_path / "task-3-output.meta.json"
    meta_path.write_text("{not json", encoding="utf-8")

    result = read_dispatch_metadata(meta_path)

    assert result is DISPATCH_META_CORRUPT
    assert not meta_path.exists()
    assert (tmp_path / "task-3-output.meta.malformed.json").exists()


def test_read_dispatch_metadata_marks_non_object_json_as_corrupt(tmp_path) -> None:
    meta_path = tmp_path / "task-4-output.meta.json"
    meta_path.write_text(json.dumps(["bad"]), encoding="utf-8")

    result = read_dispatch_metadata(meta_path)

    assert result is DISPATCH_META_CORRUPT
    assert not meta_path.exists()
    assert (tmp_path / "task-4-output.meta.malformed.json").exists()
