"""Shared file-system utilities."""

from __future__ import annotations

from pathlib import Path


def read_if_exists(path: Path) -> str:
    """Return file contents as a string, or empty string if the file does not exist."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""
