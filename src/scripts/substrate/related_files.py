"""Mechanical updater for related-files signals from the seeder agent.

Reads ``artifacts/signals/related-files-update/section-*.json`` signals
and appends new ``### <path>`` entries to the ``## Related Files``
section in each section spec.

Uses fail-closed behavior: malformed signals are renamed to
``.malformed.json``, logged, and skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Reuse the shared related_files parser from the scan package.
# Both scan and substrate need identical parsing logic.
from scan.related_files import block_insert_position, extract_related_files


def _read_signal_failclosed(path: Path) -> dict | None:
    """Read a related-files-update signal JSON.

    Expected format::

        {"additions": ["path1", ...], "removals": []}

    Returns ``None`` and renames the file to ``.malformed.json`` if
    the signal is missing, malformed, or structurally invalid.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[SUBSTRATE][WARN] Malformed related-files signal at "
            f"{path} ({exc}) -- renaming to .malformed.json"
        )
        try:
            path.rename(path.with_suffix(".malformed.json"))
        except OSError:
            pass
        return None

    if not isinstance(data, dict):
        print(
            f"[SUBSTRATE][WARN] Related-files signal at {path} is not "
            f"a JSON object -- renaming to .malformed.json"
        )
        try:
            path.rename(path.with_suffix(".malformed.json"))
        except OSError:
            pass
        return None

    if "additions" not in data or not isinstance(data["additions"], list):
        print(
            f"[SUBSTRATE][WARN] Related-files signal at {path} missing "
            f"or invalid 'additions' -- renaming to .malformed.json"
        )
        try:
            path.rename(path.with_suffix(".malformed.json"))
        except OSError:
            pass
        return None

    return data


def apply_related_files_updates(planspace: Path, codespace: Path) -> int:
    """Apply all related-files-update signals to section specs.

    Reads ``artifacts/signals/related-files-update/section-*.json``.
    Each signal has ``{"additions": ["path1", ...], "removals": []}``.

    Updates section specs by appending ``### <path>`` lines after the
    last entry under ``## Related Files``.

    Parameters
    ----------
    planspace:
        Root of the planspace directory.
    codespace:
        Root of the codespace directory (currently unused but passed
        for consistency with the runner API).

    Returns
    -------
    int
        Count of sections that were actually updated.
    """
    signals_dir = (
        planspace / "artifacts" / "signals" / "related-files-update"
    )
    sections_dir = planspace / "artifacts" / "sections"

    if not signals_dir.is_dir():
        return 0

    updated = 0

    for signal_path in sorted(signals_dir.glob("section-*.json")):
        # Extract section number from filename: section-03.json -> 03
        match = re.match(r"section-(\d+)\.json$", signal_path.name)
        if not match:
            continue
        section_num = match.group(1)

        data = _read_signal_failclosed(signal_path)
        if data is None:
            continue

        additions: list[str] = data["additions"]
        if not additions:
            continue

        # Find the corresponding section spec
        section_path = sections_dir / f"section-{section_num}.md"
        if not section_path.is_file():
            print(
                f"[SUBSTRATE][WARN] Signal for section-{section_num} "
                f"but no spec found at {section_path} -- skipping"
            )
            continue

        text = section_path.read_text(encoding="utf-8")

        # Get existing related files to avoid duplicates
        existing = set(extract_related_files(text))

        # Filter to genuinely new paths
        new_paths = [p for p in additions if p not in existing]
        if not new_paths:
            continue

        # Find insert position (end of Related Files block)
        insert_pos = block_insert_position(text)
        if insert_pos is None:
            # No Related Files section -- append one
            suffix = "\n## Related Files\n\n"
            for p in new_paths:
                suffix += f"### {p}\n"
            text = text.rstrip("\n") + "\n" + suffix
        else:
            # Insert new entries before the block boundary
            new_block = ""
            for p in new_paths:
                new_block += f"### {p}\n"
            text = text[:insert_pos] + new_block + text[insert_pos:]

        section_path.write_text(text, encoding="utf-8")
        print(
            f"[SUBSTRATE] Updated section-{section_num}: "
            f"+{len(new_paths)} related files"
        )
        updated += 1

    return updated
