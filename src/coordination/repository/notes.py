"""Repository helpers for cross-section note artifacts."""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.path_registry import PathRegistry


def _note_path(planspace: Path, from_section: str, to_section: str) -> Path:
    return PathRegistry(planspace).notes_dir() / (
        f"from-{from_section}-to-{to_section}.md"
    )


def list_notes_to(paths: PathRegistry, section: str) -> list[Path]:
    """Sorted inbound notes targeting *section*."""
    d = paths.notes_dir()
    return sorted(d.glob(f"from-*-to-{section}.md")) if d.is_dir() else []


def list_notes_from(paths: PathRegistry, section: str) -> list[Path]:
    """Sorted outbound notes originating from *section*."""
    d = paths.notes_dir()
    return sorted(d.glob(f"from-{section}-to-*.md")) if d.is_dir() else []


def list_all_notes(paths: PathRegistry) -> list[Path]:
    """All note markdown files, sorted."""
    d = paths.notes_dir()
    return sorted(d.glob("*.md")) if d.is_dir() else []


def read_incoming_notes(planspace: Path, section_number: str) -> list[dict]:
    """Read note files targeting a section."""
    paths = PathRegistry(planspace)
    notes: list[dict] = []
    for note_path in list_notes_to(paths, section_number):
        match = re.match(r"from-(.+)-to-(\d+)\.md$", note_path.name)
        if not match:
            continue
        notes.append({
            "path": note_path,
            "source": match.group(1),
            "target": match.group(2),
            "content": note_path.read_text(encoding="utf-8"),
        })
    return notes


def write_consequence_note(
    planspace: Path,
    from_section: str,
    to_section: str,
    content: str,
) -> Path:
    """Write a note file and return its path."""
    note_path = _note_path(planspace, from_section, to_section)
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(content, encoding="utf-8")
    return note_path
