"""Parse a skeleton codemap to extract top-level module entries.

Pure function, no I/O, no side effects.  Takes skeleton markdown text
and returns structured ``ModuleEntry`` records for each module listed
in the Routing Table's ``Subsystems`` section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleEntry:
    """A top-level module extracted from a skeleton codemap."""

    name: str
    path: str
    description: str


def parse_skeleton_modules(skeleton_text: str) -> list[ModuleEntry]:
    """Extract module entries from the Routing Table in *skeleton_text*.

    Parses the ``### Subsystems`` block within the ``## Routing Table``
    section.  Each bullet line is expected to follow the format::

        - <name>: <path-or-glob> -- <description>

    The separator between path and description can be ``--``, ``—``
    (em-dash), or ``—`` literally.  Lines that do not match are silently
    skipped.

    Returns a list of :class:`ModuleEntry` sorted by *name*.
    """
    subsystems_block = _extract_subsystems_block(skeleton_text)
    if not subsystems_block:
        return []

    entries: list[ModuleEntry] = []
    for line in subsystems_block.splitlines():
        entry = _parse_subsystem_line(line)
        if entry is not None:
            entries.append(entry)

    return sorted(entries, key=lambda e: e.name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Matches: "- name: path/glob — description"
# Allows em-dash (—), double-hyphen (--), or en-dash (–) as separator.
_SUBSYSTEM_RE = re.compile(
    r"^\s*[-*]\s+"           # bullet
    r"(?P<name>[^:]+)"      # subsystem name (up to first colon)
    r":\s*"                  # colon separator
    r"(?P<path>\S+)"        # path/glob (no whitespace)
    r"\s+(?:--|—|–)\s+"     # dash separator
    r"(?P<desc>.+)$",       # description (rest of line)
)


def _parse_subsystem_line(line: str) -> ModuleEntry | None:
    """Parse a single Subsystems bullet line into a ModuleEntry."""
    m = _SUBSYSTEM_RE.match(line.strip())
    if not m:
        return None
    return ModuleEntry(
        name=m.group("name").strip(),
        path=m.group("path").strip(),
        description=m.group("desc").strip(),
    )


def _extract_subsystems_block(text: str) -> str:
    """Return the text between ``### Subsystems`` and the next ``###`` header.

    Searches only within the ``## Routing Table`` section so that identically
    named headers elsewhere in the document are ignored.
    """
    routing_block = _extract_routing_table(text)
    if not routing_block:
        return ""

    # Find ### Subsystems within the routing block
    sub_header = re.search(
        r"^###\s+Subsystems\s*$", routing_block, re.MULTILINE,
    )
    if not sub_header:
        return ""

    start = sub_header.end()

    # Find the next ### header (or end of routing block)
    next_header = re.search(r"^###\s+", routing_block[start:], re.MULTILINE)
    end = start + next_header.start() if next_header else len(routing_block)

    return routing_block[start:end]


def _extract_routing_table(text: str) -> str:
    """Return the text of the ``## Routing Table`` section."""
    header = re.search(r"^##\s+Routing Table\s*$", text, re.MULTILINE)
    if not header:
        return ""

    start = header.end()

    # Find the next ## header (or end of text)
    next_section = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + next_section.start() if next_section else len(text)

    return text[start:end]
