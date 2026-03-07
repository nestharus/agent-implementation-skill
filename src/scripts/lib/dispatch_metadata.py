"""Dispatch metadata sidecar helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact_io import read_json, rename_malformed, write_json

DISPATCH_META_CORRUPT = object()


def dispatch_meta_path(output_path: Path) -> Path:
    """Return the sidecar path for a dispatch output artifact."""
    return output_path.with_suffix(".meta.json")


def write_dispatch_metadata(
    output_path: Path, *, returncode: int | None, timed_out: bool,
) -> Path:
    """Write the dispatch metadata sidecar next to the output file."""
    meta_path = dispatch_meta_path(output_path)
    write_json(
        meta_path,
        {
            "returncode": returncode,
            "timed_out": timed_out,
        },
        indent=None,
    )
    return meta_path


def read_dispatch_metadata(meta_path: Path) -> dict[str, Any] | None | object:
    """Read a dispatch metadata sidecar with fail-closed semantics."""
    if not meta_path.exists():
        return None

    data = read_json(meta_path)
    if data is None:
        return DISPATCH_META_CORRUPT
    if not isinstance(data, dict):
        rename_malformed(meta_path)
        return DISPATCH_META_CORRUPT
    return data
