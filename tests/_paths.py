"""Shared path constants for all test files.

Import from here instead of reimplementing _find_project_root() in every
test file. This module is layout-agnostic and resolves paths once at import.
"""

from __future__ import annotations

from pathlib import Path


def _find_project_root() -> Path:
    """Walk upward to find the project root using stable anchors.

    Stable anchors (in priority order):
    1. SKILL.md file (may be in src/)
    2. scripts/ + agents/ directories co-located (may be in src/)
    3. src/ directory with scripts/ inside

    Layout-agnostic: works from tests/, tests/integration/, tests/component/.
    """
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "SKILL.md").exists():
            return current
        if (current / "scripts").is_dir() and (current / "agents").is_dir():
            return current
        if (current / "src" / "scripts").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # Fallback: standard tests/ → project root layout
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _find_project_root()

WORKFLOW_HOME = (
    PROJECT_ROOT / "src"
    if (PROJECT_ROOT / "src" / "scripts").exists()
    else PROJECT_ROOT
)

SRC_DIR = WORKFLOW_HOME

DB_SH = WORKFLOW_HOME / "scripts" / "db.sh"
