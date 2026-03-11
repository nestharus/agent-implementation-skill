"""Shared fixtures for integration tests.

Mock boundary: only ``dispatch_agent`` (the LLM call) is mocked.
Everything else — file I/O, db.sh SQLite, hashing — runs for real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from _paths import DB_SH


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
        "readiness",
        "risk",
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
    monkeypatch.setattr("dispatch.engine.section_dispatch.dispatch_agent", mock)
    monkeypatch.setattr("implementation.engine.runner.dispatch_agent", mock)
    monkeypatch.setattr("implementation.service.reexplore.dispatch_agent", mock)
    monkeypatch.setattr("implementation.service.todos.dispatch_agent", mock)
    monkeypatch.setattr("coordination.engine.fix_dispatch.dispatch_agent", mock)
    monkeypatch.setattr("coordination.engine.global_coordinator.dispatch_agent", mock)
    monkeypatch.setattr("coordination.engine.plan_executor.dispatch_agent", mock)
    monkeypatch.setattr("implementation.service.scope_delta_aggregator.dispatch_agent", mock)
    monkeypatch.setattr("staleness.service.section_alignment.dispatch_agent", mock)
    monkeypatch.setattr("orchestrator.engine.main.dispatch_agent", mock)
    monkeypatch.setattr("intent.service.loop_bootstrap.dispatch_agent", mock)
    monkeypatch.setattr("intent.service.triage.dispatch_agent", mock)
    monkeypatch.setattr("implementation.service.impact_triage.dispatch_agent", mock)
    monkeypatch.setattr("proposal.service.problem_frame_gate.dispatch_agent", mock)
    monkeypatch.setattr("proposal.engine.proposal_pass.dispatch_agent", mock)
    monkeypatch.setattr("implementation.engine.implementation_pass.dispatch_agent", mock)
    monkeypatch.setattr("intent.service.expansion.dispatch_agent", mock)
    monkeypatch.setattr("proposal.service.excerpt_extractor.dispatch_agent", mock)
    monkeypatch.setattr("proposal.engine.loop.dispatch_agent", mock)
    monkeypatch.setattr("implementation.service.microstrategy.dispatch_agent", mock)
    monkeypatch.setattr("implementation.engine.loop.dispatch_agent", mock)
    return mock
