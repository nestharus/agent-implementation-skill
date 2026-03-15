"""Section decision and section-number helper utilities."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from signals.types import TRUNCATE_DETAIL

if TYPE_CHECKING:
    from orchestrator.types import Section


def read_decisions(planspace: Path, section_number: str) -> str:
    """Read accumulated decisions for a section."""
    decisions_file = PathRegistry(planspace).decision_md(section_number)
    if decisions_file.exists():
        return decisions_file.read_text(encoding="utf-8")
    return ""


def extract_section_summary(section_path: Path) -> str:
    """Extract summary from YAML frontmatter of a section file."""
    text = section_path.read_text(encoding="utf-8")
    match = re.search(
        r"^---\s*\n.*?^summary:\s*(.+?)$.*?^---",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("---") and not line.startswith("#"):
            return line[:TRUNCATE_DETAIL]
    return "(no summary available)"


def normalize_section_number(value: str, sec_num_map: dict[int, str]) -> str:
    """Normalize a parsed section number to its canonical form."""
    try:
        return sec_num_map.get(int(value), value)
    except ValueError:
        return value


def build_section_number_map(sections: list[Section]) -> dict[int, str]:
    """Build a mapping from int section number to canonical string form."""
    return {int(section.number): section.number for section in sections}
