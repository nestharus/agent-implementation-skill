"""Repository helpers for section input-ref artifacts."""

from __future__ import annotations

from pathlib import Path


def list_input_refs(inputs_dir: Path) -> list[Path]:
    """Sorted ``.ref`` files in a section inputs directory."""
    if not inputs_dir.is_dir():
        return []
    return sorted(inputs_dir.glob("*.ref"))
