"""Shared fixtures for integration tests.

Mock boundary: only ``dispatch_agent`` (the LLM call) is mocked.
Everything else — file I/O, db.sh SQLite, hashing — runs for real.
"""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dependency_injector import providers

from _paths import DB_SH
from containers import AgentDispatcher, PromptGuard, Services


class MockDispatcher(AgentDispatcher):
    """Test double for AgentDispatcher that records calls."""

    def __init__(self) -> None:
        self.mock = MagicMock(return_value="")

    def dispatch(self, *args, **kwargs) -> str:
        return self.mock(*args, **kwargs)


@contextlib.contextmanager
def override_dispatcher_and_guard(fake_dispatch_fn):
    """Context manager that overrides Services.dispatcher and prompt_guard.

    Use this in place of ``patch.object(module, "dispatch_agent", ...)`` and
    ``patch.object(module, "validate_dynamic_content", ...)`` for modules
    that were migrated to the container.

    ``fake_dispatch_fn`` receives the same args as ``AgentDispatcher.dispatch``.

    Yields the ``fake_dispatch_fn`` (unchanged) so callers can inspect
    call state if needed.
    """

    class _Dispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return fake_dispatch_fn(*args, **kwargs)

    class _Guard(PromptGuard):
        def validate_dynamic(self, content):
            return []

        def write_validated(self, content, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return True

    Services.dispatcher.override(providers.Object(_Dispatcher()))
    Services.prompt_guard.override(providers.Object(_Guard()))
    try:
        yield fake_dispatch_fn
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()


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
def mock_dispatch() -> MagicMock:
    """Override the Services.dispatcher container provider.

    Returns the inner MagicMock so tests can configure return values::

        mock_dispatch.return_value = '{"aligned": true}'
    """
    mock_disp = MockDispatcher()
    Services.dispatcher.override(providers.Object(mock_disp))
    yield mock_disp.mock
    Services.dispatcher.reset_override()
