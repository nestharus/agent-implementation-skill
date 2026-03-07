"""ArtifactIO: JSON file read/write with corruption preservation.

Foundational service (Tier 1). No domain knowledge, no dependencies
on other project modules. Replaces ad-hoc json.loads/write_text sites
and .malformed.json rename patterns throughout the codebase.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def read_json(path: Path) -> dict | list | None:
    """Read and parse a JSON file. Returns None if missing or corrupt.

    On parse failure, renames the file to .malformed.json (corruption
    preservation protocol) and logs a warning.
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Malformed JSON at %s: %s", path, exc)
        rename_malformed(path)
        return None


def write_json(path: Path, data: object, *, indent: int = 2) -> None:
    """Write data as JSON to a file. Creates parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=indent) + "\n",
        encoding="utf-8",
    )


def rename_malformed(path: Path) -> Path | None:
    """Rename a corrupt file to .malformed.json for forensic preservation.

    Returns the new path, or None if the rename failed.
    """
    if not path.exists():
        return None
    malformed_path = path.with_suffix(".malformed.json")
    try:
        path.rename(malformed_path)
        logger.warning("Preserved malformed file: %s -> %s", path, malformed_path)
        return malformed_path
    except OSError as exc:
        logger.warning("Failed to rename malformed file %s: %s", path, exc)
        return None


def read_json_or_default(path: Path, default: object) -> dict | list:
    """Read JSON, returning default if missing or corrupt."""
    result = read_json(path)
    if result is None:
        return default
    return result
