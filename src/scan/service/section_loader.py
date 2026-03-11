"""Helpers for loading section specs from the planspace."""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.types import Section


def parse_related_files(section_path: Path) -> list[str]:
    """Extract file paths from a section spec's related-files block."""
    from scan.related.cli_handler import extract_related_files

    return extract_related_files(section_path.read_text(encoding="utf-8"))


def load_sections(sections_dir: Path) -> list[Section]:
    """Load section specs and their related file maps."""
    sections: list[Section] = []
    for path in sorted(sections_dir.glob("section-*.md")):
        match = re.match(r"^section-(\d+)\.md$", path.name)
        if not match:
            continue
        sections.append(
            Section(
                number=match.group(1),
                path=path,
                related_files=parse_related_files(path),
            ),
        )
    return sections
