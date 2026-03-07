"""Component tests for shared communication helpers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _paths import DB_SH
from src.scripts.lib.core.communication import (
    AGENT_NAME,
    DB_PATH,
    _record_traceability,
    log,
    mailbox_drain,
    mailbox_register,
    mailbox_send,
)


def test_exported_constants_remain_stable() -> None:
    assert AGENT_NAME == "section-loop"
    assert DB_PATH == Path("run.db")


def test_log_prints_prefixed_message(capsys) -> None:
    log("hello")

    assert capsys.readouterr().out.strip() == "[section-loop] hello"


def test_record_traceability_recovers_from_corrupt_json(tmp_path: Path) -> None:
    trace_path = tmp_path / "artifacts" / "traceability.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text("not valid json {{{", encoding="utf-8")

    _record_traceability(tmp_path, "03", "artifact-c", "source-c")

    entries = json.loads(trace_path.read_text(encoding="utf-8"))
    assert entries == [{
        "section": "03",
        "artifact": "artifact-c",
        "source": "source-c",
        "detail": "",
    }]


def test_mailbox_helpers_round_trip(tmp_path: Path) -> None:
    subprocess.run(
        ["bash", str(DB_SH), "init", str(tmp_path / "run.db")],
        check=True,
        capture_output=True,
        text=True,
    )

    mailbox_register(tmp_path)
    mailbox_send(tmp_path, AGENT_NAME, "test message")

    assert mailbox_drain(tmp_path) == ["test message"]
