"""Unified Related Files block parsing and editing.

Single source of truth for extracting, locating, and modifying
``### <path>`` entries under the ``## Related Files`` header in
section spec files.

All operations are:
- **Block-scoped**: confined to the Related Files block only.
- **Code-fence-safe**: ignore content inside ``` blocks.

Imported by both ``scan`` and ``section_loop`` packages to
eliminate parsing/editing duplication (R33/P9).
"""

from __future__ import annotations

import re


def _find_block_bounds(text: str) -> tuple[int, int] | None:
    """Return ``(header_end, block_end)`` for the Related Files block.

    ``header_end`` is the character position immediately after the
    ``## Related Files`` header line (including its trailing newline).
    ``block_end`` is the position of the next ``## `` header outside
    code fences, or ``len(text)`` if none found.

    Returns ``None`` if no ``## Related Files`` header is present.
    """
    in_fence = False
    pos = 0
    header_end: int | None = None

    for line in text.split("\n"):
        line_start = pos
        pos += len(line) + 1  # +1 for the \n

        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue

        if in_fence:
            continue

        if header_end is None:
            if stripped == "## Related Files":
                header_end = pos  # character after this line's \n
        else:
            # Inside the block — look for the next ## header (not ###)
            if line.startswith("## ") and not line.startswith("### "):
                return (header_end, line_start)

    if header_end is not None:
        return (header_end, len(text))
    return None


def extract_related_files(text: str) -> list[str]:
    """Extract ``### <path>`` entries from the Related Files block.

    Block-scoped and code-fence-safe.
    """
    bounds = _find_block_bounds(text)
    if bounds is None:
        return []
    start, end = bounds
    block = text[start:end]

    in_fence = False
    files: list[str] = []
    for line in block.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("### "):
            path = line[4:].strip()
            if path:
                files.append(path)
    return files


def find_entry_span(
    text: str, entry_path: str,
) -> tuple[int, int] | None:
    """Find the character span of a ``### <entry_path>`` heading.

    Searches only within the Related Files block, ignoring code fences.
    Returns ``(entry_start, entry_end)`` as absolute positions in
    *text*, or ``None`` if the entry is not found.

    ``entry_start`` is the first character of the ``### `` heading line.
    ``entry_end`` is the first character of the next ``### ``/``## ``
    heading (or the block end).
    """
    bounds = _find_block_bounds(text)
    if bounds is None:
        return None
    block_start, block_end = bounds
    block = text[block_start:block_end]

    marker = f"### {entry_path}"
    in_fence = False
    entry_rel_start: int | None = None

    pos = 0
    for line in block.split("\n"):
        line_rel = pos
        pos += len(line) + 1

        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        if entry_rel_start is not None:
            # Look for end boundary
            if line.startswith("### ") or (
                line.startswith("## ") and not line.startswith("### ")
            ):
                return (block_start + entry_rel_start,
                        block_start + line_rel)
        elif line.startswith(marker):
            # Exact heading match (line is "### path" possibly
            # followed by nothing, spaces, or more text)
            rest = line[len(marker):]
            if not rest or rest[0] in (" ", "\t"):
                entry_rel_start = line_rel
            elif rest == "":
                entry_rel_start = line_rel

    if entry_rel_start is not None:
        return (block_start + entry_rel_start, block_end)
    return None


def block_insert_position(text: str) -> int | None:
    """Return the position where new entries should be appended.

    This is the end of the Related Files block (before the next
    ``## `` header or end of text).  Returns ``None`` if no
    Related Files block exists.
    """
    bounds = _find_block_bounds(text)
    if bounds is None:
        return None
    _, end = bounds
    return end
