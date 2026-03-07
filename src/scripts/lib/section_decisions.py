"""Section decision and section-number helper utilities."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .decision_repository import Decision, load_decisions, record_decision
from .path_registry import PathRegistry

if TYPE_CHECKING:
    from section_loop.types import Section


def read_decisions(planspace: Path, section_number: str) -> str:
    """Read accumulated decisions for a section."""
    decisions_file = (
        PathRegistry(planspace).decisions_dir() / f"section-{section_number}.md"
    )
    if decisions_file.exists():
        return decisions_file.read_text(encoding="utf-8")
    return ""


def persist_decision(planspace: Path, section_number: str, decision_text: str) -> None:
    """Persist a resume payload as a decision for a section."""
    decisions_dir = PathRegistry(planspace).decisions_dir()
    decisions_dir.mkdir(parents=True, exist_ok=True)

    existing = load_decisions(decisions_dir, section=section_number)
    next_num = len(existing) + 1
    decision_id = f"d-{section_number}-{next_num:03d}"

    decision = Decision(
        id=decision_id,
        scope="section",
        section=section_number,
        problem_id=None,
        parent_problem_id=None,
        concern_scope="parent-resume",
        proposal_summary=decision_text,
        alignment_to_parent=None,
        status="decided",
    )
    record_decision(decisions_dir, decision)


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
            return line[:200]
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
