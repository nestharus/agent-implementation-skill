"""Component tests for HaltWatcher and the halt_event guard in SectionDispatcher."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.service.halt_watcher import HaltWatcher


# ---------------------------------------------------------------------------
# HaltWatcher unit tests (no real mailbox needed)
# ---------------------------------------------------------------------------


class _StubConfig:
    """Minimal config stub for HaltWatcher."""

    db_sh = Path("/dev/null")
    agent_name = "section-loop"


def test_halt_event_starts_unset() -> None:
    """The halt_event passed to HaltWatcher is not set at construction time."""
    event = threading.Event()
    watcher = HaltWatcher(
        planspace=Path("/tmp/fake"),
        config=_StubConfig(),
        halt_event=event,
    )
    assert not watcher.halt_event.is_set()


def test_halt_event_is_same_object_as_injected() -> None:
    """halt_event property returns the exact object injected via constructor."""
    event = threading.Event()
    watcher = HaltWatcher(
        planspace=Path("/tmp/fake"),
        config=_StubConfig(),
        halt_event=event,
    )
    assert watcher.halt_event is event


def test_stop_sets_halt_event() -> None:
    """Calling stop() sets the halt_event so all holders unblock."""
    event = threading.Event()
    watcher = HaltWatcher(
        planspace=Path("/tmp/fake"),
        config=_StubConfig(),
        halt_event=event,
    )
    assert not event.is_set()
    watcher.stop()
    assert event.is_set()


def test_poll_loop_sets_event_on_abort() -> None:
    """When the mailbox drain returns an abort message, halt_event is set."""
    event = threading.Event()
    watcher = HaltWatcher(
        planspace=Path("/tmp/fake"),
        config=_StubConfig(),
        halt_event=event,
        poll_interval=0.05,
    )

    mock_mailbox = MagicMock()
    mock_mailbox.drain.return_value = ["abort"]
    mock_mailbox.send = MagicMock()

    with patch(
        "signals.service.mailbox_service.MailboxService.for_planspace",
        return_value=mock_mailbox,
    ):
        watcher.start()
        # Wait for the thread to process the abort
        event.wait(timeout=2.0)

    assert event.is_set()


def test_poll_loop_requeues_non_abort_messages() -> None:
    """Non-abort messages are re-sent to our own mailbox (not lost)."""
    event = threading.Event()
    watcher = HaltWatcher(
        planspace=Path("/tmp/fake"),
        config=_StubConfig(),
        halt_event=event,
        poll_interval=0.05,
    )

    call_count = 0

    def drain_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ["status:running"]
        # Second call: return abort to stop the loop
        return ["abort"]

    mock_mailbox = MagicMock()
    mock_mailbox.drain.side_effect = drain_side_effect
    mock_mailbox.send = MagicMock()

    with patch(
        "signals.service.mailbox_service.MailboxService.for_planspace",
        return_value=mock_mailbox,
    ):
        watcher.start()
        event.wait(timeout=2.0)

    # The non-abort message should have been re-queued
    mock_mailbox.send.assert_any_call("section-loop", "status:running")


def test_poll_loop_survives_exception() -> None:
    """Exceptions in the drain loop do not crash the thread."""
    event = threading.Event()
    watcher = HaltWatcher(
        planspace=Path("/tmp/fake"),
        config=_StubConfig(),
        halt_event=event,
        poll_interval=0.05,
    )

    call_count = 0

    def drain_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("db error")
        return ["abort"]

    mock_mailbox = MagicMock()
    mock_mailbox.drain.side_effect = drain_side_effect

    with patch(
        "signals.service.mailbox_service.MailboxService.for_planspace",
        return_value=mock_mailbox,
    ):
        watcher.start()
        event.wait(timeout=2.0)

    assert event.is_set()


def test_stop_without_start_is_safe() -> None:
    """Calling stop() before start() does not raise."""
    event = threading.Event()
    watcher = HaltWatcher(
        planspace=Path("/tmp/fake"),
        config=_StubConfig(),
        halt_event=event,
    )
    watcher.stop()
    assert event.is_set()


# ---------------------------------------------------------------------------
# SectionDispatcher halt guard tests
# ---------------------------------------------------------------------------


def test_section_dispatcher_aborts_when_halt_event_set_before_dispatch() -> None:
    """SectionDispatcher returns early when halt_event is set pre-dispatch."""
    from dispatch.types import DispatchResult, DispatchStatus

    event = threading.Event()
    event.set()

    from dispatch.engine.section_dispatcher import SectionDispatcher

    dispatcher = SectionDispatcher(
        config=MagicMock(),
        pipeline_control=MagicMock(
            wait_if_paused=MagicMock(),
            alignment_changed_pending=MagicMock(return_value=False),
        ),
        logger=MagicMock(),
        communicator=MagicMock(),
        task_router=MagicMock(resolve_agent_path=MagicMock(return_value=Path("/fake"))),
        prompt_guard=MagicMock(),
        artifact_io=MagicMock(),
        halt_event=event,
    )

    result = dispatcher.dispatch_agent(
        model="test-model",
        prompt_path=Path("/tmp/prompt.md"),
        output_path=Path("/tmp/output.md"),
        planspace=Path("/tmp/planspace"),
        agent_file="test.md",
    )

    assert isinstance(result, DispatchResult)
    assert result.status == DispatchStatus.ALIGNMENT_CHANGED


def test_section_dispatcher_runs_normally_without_halt_event() -> None:
    """SectionDispatcher proceeds normally when no halt_event is provided."""
    from dispatch.engine.section_dispatcher import SectionDispatcher

    # The dispatcher should not fail construction when halt_event is None
    dispatcher = SectionDispatcher(
        config=MagicMock(),
        pipeline_control=MagicMock(),
        logger=MagicMock(),
        communicator=MagicMock(),
        task_router=MagicMock(),
        prompt_guard=MagicMock(),
        artifact_io=MagicMock(),
        halt_event=None,
    )
    assert dispatcher._halt_event is None
