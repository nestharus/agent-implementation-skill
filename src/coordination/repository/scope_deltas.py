"""Repository helpers for scope-delta artifacts."""

from __future__ import annotations

from pathlib import Path


def list_scope_delta_files(scope_deltas_dir: Path) -> list[Path]:
    """Sorted JSON scope-delta files, excluding malformed markers."""
    if not scope_deltas_dir.is_dir():
        return []
    return sorted(
        p for p in scope_deltas_dir.iterdir()
        if p.suffix == ".json" and not p.name.endswith(".malformed.json")
    )
