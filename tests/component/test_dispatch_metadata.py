"""Component tests for dispatch metadata sidecars."""

from __future__ import annotations

import json

from containers import Services
from dispatch.repository.metadata import Metadata, dispatch_meta_path


def _metadata() -> Metadata:
    return Metadata(artifact_io=Services.artifact_io())


def test_dispatch_meta_path_uses_output_stem(tmp_path) -> None:
    output_path = tmp_path / "task-7-output.md"

    assert dispatch_meta_path(output_path) == tmp_path / "task-7-output.meta.json"


def test_write_dispatch_metadata_creates_sidecar(tmp_path) -> None:
    output_path = tmp_path / "task-1-output.md"

    meta_path = _metadata().write_dispatch_metadata(
        output_path, returncode=0, timed_out=False,
    )

    assert meta_path.exists()
    assert json.loads(meta_path.read_text(encoding="utf-8")) == {
        "returncode": 0,
        "timed_out": False,
    }


def test_read_dispatch_metadata_returns_absent_when_missing(tmp_path) -> None:
    result = _metadata().read_dispatch_metadata(tmp_path / "missing.meta.json")
    assert result.is_absent
    assert result.data is None


def test_read_dispatch_metadata_returns_parsed_dict(tmp_path) -> None:
    meta_path = tmp_path / "task-2-output.meta.json"
    meta_path.write_text(
        json.dumps({"returncode": 1, "timed_out": False}),
        encoding="utf-8",
    )

    result = _metadata().read_dispatch_metadata(meta_path)
    assert not result.is_corrupt
    assert not result.is_absent
    assert result.data == {
        "returncode": 1,
        "timed_out": False,
    }


def test_read_dispatch_metadata_marks_malformed_json_as_corrupt(tmp_path) -> None:
    meta_path = tmp_path / "task-3-output.meta.json"
    meta_path.write_text("{not json", encoding="utf-8")

    result = _metadata().read_dispatch_metadata(meta_path)

    assert result.is_corrupt
    assert result.data is None


def test_read_dispatch_metadata_marks_non_object_json_as_corrupt(tmp_path) -> None:
    meta_path = tmp_path / "task-4-output.meta.json"
    meta_path.write_text(json.dumps(["bad"]), encoding="utf-8")

    result = _metadata().read_dispatch_metadata(meta_path)

    assert result.is_corrupt
    assert not meta_path.exists()
    assert (tmp_path / "task-4-output.meta.malformed.json").exists()
