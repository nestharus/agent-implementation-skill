"""Shared loader for scan prompt templates."""

from __future__ import annotations

from pathlib import Path

_SCAN_TEMPLATES = Path(__file__).resolve().parent.parent.parent / "templates" / "scan"


def load_scan_template(name: str) -> str:
    """Load a scan prompt template by filename."""
    return (_SCAN_TEMPLATES / name).read_text()
