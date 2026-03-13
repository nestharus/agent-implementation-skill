"""Component tests for MonitorService lifecycle extraction."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from _paths import DB_SH
from src.signals.service.database_client import DatabaseClient
from src.dispatch.service import monitor_service as monitor_service_mod
from src.dispatch.service.monitor_service import MonitorHandle, MonitorService


class _FakeProcess:
    def __init__(self, *, pid: int = 4321, timeout: bool = False) -> None:
        self.pid = pid
        self._timeout = timeout
        self.wait_calls: list[int] = []
        self.terminated = False

    def wait(self, timeout: int) -> None:
        self.wait_calls.append(timeout)
        if self._timeout:
            raise subprocess.TimeoutExpired(cmd="agents", timeout=timeout)

    def terminate(self) -> None:
        self.terminated = True


def _monitor_service(tmp_path: Path) -> tuple[MonitorService, DatabaseClient, Path]:
    db_path = tmp_path / "run.db"
    client = DatabaseClient(DB_SH, db_path)
    client.execute("init")
    service = MonitorService(client, "section-loop")
    return service, client, db_path


def test_start_registers_agent_and_spawns_monitor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, client, db_path = _monitor_service(tmp_path)
    prompt_path = tmp_path / "monitor-prompt.md"
    prompt_path.write_text("# monitor\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agents_path = bin_dir / "agents"
    agents_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    agents_path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    # resolve_agent_path returns the real discovered path
    from src.taskrouter.agents import resolve_agent_path
    resolved_monitor_path = resolve_agent_path("agent-monitor.md")

    handle = service.start("impl-01", prompt_path)

    assert handle.agent_name == "impl-01"
    assert handle.monitor_name == "impl-01-monitor"
    assert handle.dispatch_start_id is not None
    assert handle.process.args == [
        "agents",
        "--agent-file",
        str(resolved_monitor_path),
        "--file",
        str(prompt_path),
    ]
    handle.process.wait(timeout=5)

    conn = sqlite3.connect(db_path)
    status = conn.execute(
        "SELECT status FROM agents WHERE name = ? ORDER BY id DESC LIMIT 1",
        ("impl-01",),
    ).fetchone()
    conn.close()
    assert status == ("running",)

    rows = client.query(
        "lifecycle",
        tag="dispatch:impl-01",
        agent="section-loop",
        check=False,
    )
    assert "start" in rows


def test_stop_collects_signals_and_cleans_up(tmp_path: Path) -> None:
    service, client, db_path = _monitor_service(tmp_path)
    handle = MonitorHandle(
        agent_name="impl-02",
        monitor_name="impl-02-monitor",
        process=_FakeProcess(),
        dispatch_start_id="0",
    )
    client.log_event(
        "signal",
        "impl-02",
        "LOOP_DETECTED:impl-02:repeat action",
        agent="impl-02-monitor",
    )

    output = service.stop(handle, "base output")

    assert "LOOP_DETECTED: LOOP_DETECTED:impl-02:repeat action" in output
    assert handle.process.wait_calls == [30]

    relogged = client.query(
        "signal",
        tag="loop_detected:impl-02",
        agent="section-loop",
        check=False,
    )
    assert "LOOP_DETECTED:impl-02:repeat action" in relogged

    conn = sqlite3.connect(db_path)
    statuses = conn.execute(
        "SELECT name, status FROM agents WHERE name IN (?, ?) ORDER BY id ASC",
        ("impl-02", "impl-02-monitor"),
    ).fetchall()
    conn.close()
    assert statuses == [
        ("impl-02", "cleaned"),
        ("impl-02", "exited"),
        ("impl-02-monitor", "cleaned"),
        ("impl-02-monitor", "exited"),
    ]


def test_stop_terminates_monitor_on_wait_timeout(tmp_path: Path) -> None:
    service, _, _ = _monitor_service(tmp_path)
    process = _FakeProcess(timeout=True)
    handle = MonitorHandle(
        agent_name="impl-03",
        monitor_name="impl-03-monitor",
        process=process,
        dispatch_start_id=None,
    )

    service.stop(handle, "base output")

    assert process.wait_calls == [30]
    assert process.terminated is True
