"""HaltWatcher: background thread that polls the orchestrator mailbox for abort signals.

Sets a ``threading.Event`` when an abort message is received, allowing
all holders of the event to check-and-bail without requiring their own
mailbox polling.  Non-abort messages are left in the mailbox (drained
and re-queued) so they are not lost.

PAT-0019: The ``halt_event`` is constructor-injected, not globally constructed.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.types import ControlSignal

if TYPE_CHECKING:
    from containers import ConfigService


class HaltWatcher:
    """Polls the orchestrator mailbox and sets ``halt_event`` on abort.

    Parameters
    ----------
    planspace:
        Path to the planspace directory (used to locate run.db).
    config:
        Injected config service providing ``db_sh`` and ``agent_name``.
    halt_event:
        The event to set when an abort message arrives.  Shared with
        other components so they can check ``halt_event.is_set()``
        before expensive work.
    poll_interval:
        Seconds between mailbox polls (default 2.0).
    """

    def __init__(
        self,
        planspace: Path,
        config: ConfigService,
        halt_event: threading.Event,
        poll_interval: float = 2.0,
    ) -> None:
        self._planspace = planspace
        self._config = config
        self._halt_event = halt_event
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def halt_event(self) -> threading.Event:
        """The shared halt event — set when an abort message is received."""
        return self._halt_event

    def start(self) -> None:
        """Launch the daemon polling thread."""
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="halt-watcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the polling loop to stop and wait for it to finish."""
        self._stop_event.set()
        # Also set halt_event so any waiters unblock.
        self._halt_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval * 2)

    def _poll_loop(self) -> None:
        """Drain the mailbox each cycle; set halt_event on abort."""
        from signals.service.mailbox_service import MailboxService

        mailbox = MailboxService.for_planspace(
            self._planspace,
            db_sh=self._config.db_sh,
            agent_name=self._config.agent_name,
        )

        while not self._stop_event.is_set():
            try:
                messages = mailbox.drain()
                for msg in messages:
                    if msg.startswith(ControlSignal.ABORT):
                        self._halt_event.set()
                        return
                    # Re-queue non-abort messages so they are not lost.
                    mailbox.send(self._config.agent_name, msg)
            except Exception:  # noqa: BLE001 — daemon thread, must not crash
                pass
            self._stop_event.wait(timeout=self._poll_interval)
