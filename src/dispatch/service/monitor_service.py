"""MonitorService: per-agent monitor lifecycle extracted from dispatch."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from signals.service.database_client import DatabaseClient

if TYPE_CHECKING:
    from containers import LogService, TaskRouterService

_MONITOR_WAIT_TIMEOUT = 30
_MIN_SIGNAL_LOG_FIELDS = 5
_SIGNAL_BODY_COLUMN_INDEX = 4


@dataclass
class MonitorHandle:
    """State needed to stop and collect from a running monitor."""

    agent_name: str
    monitor_name: str
    process: Any
    dispatch_start_id: str | None


class MonitorService:
    """Manage start/stop/cleanup for one agent monitor."""

    def __init__(
        self,
        db: DatabaseClient,
        controller_name: str,
        task_router: TaskRouterService,
        logger: LogService,
    ) -> None:
        self._db = db
        self._controller_name = controller_name
        self._task_router = task_router
        self._logger = logger

    def start(self, agent_name: str, prompt_path: Path) -> MonitorHandle:
        """Register the agent mailbox, log dispatch start, and spawn monitor."""
        monitor_name = f"{agent_name}-monitor"
        self._db.register(agent_name)
        start_out = self._db.log_event(
            "lifecycle",
            f"dispatch:{agent_name}",
            "start",
            agent=self._controller_name,
            check=False,
        )
        dispatch_start_id = None
        if start_out.startswith("logged:"):
            dispatch_start_id = start_out.split(":")[1]

        process = subprocess.Popen(  # noqa: S603
            [
                "agents",
                "--agent-file",
                str(self._task_router.resolve_agent_path("agent-monitor.md")),
                "--file",
                str(prompt_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._log(f"  agent-monitor started (pid={process.pid})")
        return MonitorHandle(
            agent_name=agent_name,
            monitor_name=monitor_name,
            process=process,
            dispatch_start_id=dispatch_start_id,
        )

    def stop(self, handle: MonitorHandle, output: str) -> str:
        """Signal the monitor to stop, collect any signals, then clean up."""
        self._db.send(
            handle.monitor_name,
            "agent-finished",
            sender=self._controller_name,
            check=False,
        )
        try:
            handle.process.wait(timeout=_MONITOR_WAIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            handle.process.terminate()

        if handle.dispatch_start_id:
            signal_rows = self._db.query(
                "signal",
                tag=handle.agent_name,
                since=handle.dispatch_start_id,
                check=False,
            )
            for signal_line in signal_rows.splitlines():
                parts = signal_line.split("|")
                if len(parts) >= _MIN_SIGNAL_LOG_FIELDS and parts[_SIGNAL_BODY_COLUMN_INDEX]:
                    signal_body = parts[_SIGNAL_BODY_COLUMN_INDEX]
                    self._log(f"  SIGNAL from monitor: {signal_body}")
                    output += "\nLOOP_DETECTED: " + signal_body
                    self._db.log_event(
                        "signal",
                        f"loop_detected:{handle.agent_name}",
                        signal_body,
                        agent=self._controller_name,
                        check=False,
                    )

        self._db.cleanup(handle.agent_name, check=False)
        self._db.unregister(handle.agent_name, check=False)
        self._db.cleanup(handle.monitor_name, check=False)
        self._db.unregister(handle.monitor_name, check=False)
        return output

    def _log(self, message: str) -> None:
        self._logger.log(message)
