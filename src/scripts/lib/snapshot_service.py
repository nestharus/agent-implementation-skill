"""Snapshot service for cross-section file snapshots and file diffs."""

from __future__ import annotations

import difflib
import shutil
from collections.abc import Callable
from pathlib import Path

from .path_registry import PathRegistry


def snapshot_modified_files(
    planspace: Path,
    section_number: str,
    codespace: Path,
    modified_files: list[str],
    *,
    warn: Callable[[str], None] | None = None,
) -> Path:
    """Copy modified files into the section snapshot directory.

    Files are copied to ``artifacts/snapshots/section-NN/`` while preserving
    relative paths. Missing files are skipped. Paths that escape either the
    codespace root or snapshot root are skipped and optionally warned.
    """
    snapshot_dir = (
        PathRegistry(planspace).artifacts
        / "snapshots"
        / f"section-{section_number}"
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    codespace_resolved = codespace.resolve()
    snapshot_resolved = snapshot_dir.resolve()
    for rel_path in modified_files:
        src = (codespace / rel_path).resolve()
        if not src.exists():
            continue
        if not src.is_relative_to(codespace_resolved):
            if warn is not None:
                warn(f"snapshot path escapes codespace, skipping: {rel_path}")
            continue
        dest = (snapshot_dir / rel_path).resolve()
        if not dest.is_relative_to(snapshot_resolved):
            if warn is not None:
                warn(f"dest path escapes snapshot dir, skipping: {rel_path}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))

    return snapshot_dir


def compute_text_diff(old_path: Path, new_path: Path) -> str:
    """Compute a unified text diff between two files."""
    if not old_path.exists() and not new_path.exists():
        return ""
    if not old_path.exists():
        old_lines: list[str] = []
        old_label = "(did not exist)"
    else:
        old_lines = old_path.read_text(encoding="utf-8").splitlines(keepends=True)
        old_label = str(old_path)
    if not new_path.exists():
        new_lines: list[str] = []
        new_label = "(deleted)"
    else:
        new_lines = new_path.read_text(encoding="utf-8").splitlines(keepends=True)
        new_label = str(new_path)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=old_label,
        tofile=new_label,
        lineterm="",
    )
    return "\n".join(diff)
