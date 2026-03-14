"""Agent file resolver — maps agent basenames to their system-owned paths.

Each system owns its agent .md files under ``<system>/agents/``.
This module builds a lookup index at import time by scanning all
system agent directories, so any caller can resolve an agent basename
to its full path without knowing which system owns it.

Usage::

    from taskrouter.agents import resolve_agent_path

    path = resolve_agent_path("alignment-judge.md")
    # -> Path(".../src/staleness/agents/alignment-judge.md")
"""

from __future__ import annotations

from pathlib import Path

# src/ directory — parent of all system packages.
_SRC_DIR = Path(__file__).resolve().parent.parent

# Basename -> absolute Path, built once at import time.
_INDEX: dict[str, Path] = {}


def _build_index() -> None:
    """Scan all src/*/agents/ directories and index agent files."""
    for agents_dir in sorted(_SRC_DIR.glob("*/agents")):
        if not agents_dir.is_dir():
            continue
        for agent_file in sorted(agents_dir.glob("*.md")):
            basename = agent_file.name
            if basename in _INDEX:
                raise RuntimeError(
                    f"Duplicate agent file {basename!r}: "
                    f"found in {_INDEX[basename].parent} and {agents_dir}"
                )
            _INDEX[basename] = agent_file


_build_index()


def resolve_agent_path(agent_file: str) -> Path:
    """Resolve an agent basename to its full filesystem path.

    Raises ``FileNotFoundError`` if the agent is not found in any
    system's agents directory.
    """
    path = _INDEX.get(agent_file)
    if path is None:
        raise FileNotFoundError(
            f"Agent file {agent_file!r} not found in any system's "
            f"agents/ directory. Known agents: "
            f"{sorted(_INDEX.keys())}"
        )
    return path


