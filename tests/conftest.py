"""Shared fixtures for integration tests.

Mock boundary: only ``dispatch_agent`` (the LLM call) is mocked.
Everything else — file I/O, db.sh SQLite, hashing — runs for real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Resolve project root from this file's location (tests/ -> project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_SH = PROJECT_ROOT / "src" / "scripts" / "db.sh"


@pytest.fixture()
def planspace(tmp_path: Path) -> Path:
    """Create a realistic planspace directory with initialized SQLite DB.

    Mirrors the directory structure that section_engine.py and
    coordination.py expect at runtime.
    """
    ps = tmp_path / "planspace"
    ps.mkdir()
    artifacts = ps / "artifacts"
    for subdir in (
        "sections",
        "proposals",
        "signals",
        "notes",
        "decisions",
        "todos",
        "coordination",
    ):
        (artifacts / subdir).mkdir(parents=True)

    # Initialize the coordination database via db.sh
    subprocess.run(
        ["bash", str(DB_SH), "init", str(ps / "run.db")],
        check=True,
        capture_output=True,
        text=True,
    )
    return ps


@pytest.fixture()
def codespace(tmp_path: Path) -> Path:
    """Create a minimal codespace with mock source files."""
    cs = tmp_path / "codespace"
    cs.mkdir()
    (cs / "src").mkdir()
    (cs / "src" / "main.py").write_text("def main():\n    pass\n")
    (cs / "src" / "utils.py").write_text("def helper():\n    return 42\n")
    return cs


@pytest.fixture()
def section_01(planspace: Path) -> None:
    """Create a minimal section-01 spec in the planspace."""
    sec = planspace / "artifacts" / "sections" / "section-01.md"
    sec.write_text(
        "# Section 01: Authentication\n\n"
        "Handle user login and session management.\n"
    )


@pytest.fixture()
def mock_dispatch(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock dispatch_agent at the canonical location AND all import sites.

    Python caches ``from X import Y`` at import time, so patching only
    the definition module doesn't affect modules that already imported
    the name.  We patch everywhere dispatch_agent is used.

    Returns the mock so tests can configure return values per-call::

        mock_dispatch.return_value = '{"aligned": true}'
    """
    mock = MagicMock(return_value="")
    monkeypatch.setattr("section_loop.dispatch.dispatch_agent", mock)
    monkeypatch.setattr("section_loop.section_engine.runner.dispatch_agent", mock)
    monkeypatch.setattr("section_loop.section_engine.reexplore.dispatch_agent", mock)
    monkeypatch.setattr("section_loop.section_engine.todos.dispatch_agent", mock)
    monkeypatch.setattr("section_loop.coordination.execution.dispatch_agent", mock)
    monkeypatch.setattr("section_loop.coordination.runner.dispatch_agent", mock)
    monkeypatch.setattr("section_loop.alignment.dispatch_agent", mock)
    monkeypatch.setattr("section_loop.main.dispatch_agent", mock)
    return mock
