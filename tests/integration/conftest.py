"""Integration-test conftest: patches that apply to all integration tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_dispatcher_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eliminate the real sleep in task_dispatcher retry/outage paths.

    ``_dispatcher_sleep`` is a module-level alias for ``time.sleep``.
    ``TaskDispatcher.__init__`` copies it into ``self._sleep``, so patching
    the module attribute before each test prevents any integration test from
    blocking on the 30-240 second retry delays.
    """
    monkeypatch.setattr(
        "flow.engine.task_dispatcher._dispatcher_sleep", lambda _seconds: None,
    )
