"""Output formatters for the timeline."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable

from log_extract.models import TimelineEvent

_CSV_COLUMNS = [
    "ts", "source", "kind", "agent", "session_id",
    "model", "backend", "section", "detail",
]

_COLOR_BY_KIND = {
    "dispatch": "\033[1;33m",   # bold yellow
    "response": "\033[1;32m",   # bold green
    "tool_call": "\033[0;36m",  # cyan
    "tool_result": "\033[0;36m",
    "signal": "\033[1;31m",     # bold red
    "summary": "\033[1;35m",    # bold magenta
    "lifecycle": "\033[0;37m",  # gray
    "message": "\033[0;34m",    # blue
    "task": "\033[0;33m",       # yellow
    "gate": "\033[0;33m",
    "artifact": "\033[0;90m",   # dark gray
    "session": "\033[1;34m",    # bold blue
}
_RESET = "\033[0m"


def format_jsonl(events: Iterable[TimelineEvent]) -> Iterable[str]:
    """Yield one JSON line per event.  Excludes internal ``ts_ms``."""
    for ev in events:
        obj = {
            "ts": ev.ts,
            "source": ev.source,
            "kind": ev.kind,
            "detail": ev.detail,
        }
        if ev.agent:
            obj["agent"] = ev.agent
        if ev.session_id:
            obj["session_id"] = ev.session_id
        if ev.model:
            obj["model"] = ev.model
        if ev.backend:
            obj["backend"] = ev.backend
        if ev.section:
            obj["section"] = ev.section
        if ev.raw:
            obj["raw"] = ev.raw
        yield json.dumps(obj, separators=(",", ":"))


def format_csv(events: Iterable[TimelineEvent]) -> Iterable[str]:
    """Yield CSV rows (header first)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    buf.seek(0)
    yield buf.read().rstrip("\r\n")

    for ev in events:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            ev.ts, ev.source, ev.kind, ev.agent, ev.session_id,
            ev.model, ev.backend, ev.section, ev.detail,
        ])
        buf.seek(0)
        yield buf.read().rstrip("\r\n")


def format_text(events: Iterable[TimelineEvent], *, use_color: bool = True) -> Iterable[str]:
    """Yield human-readable lines."""
    for ev in events:
        parts = [ev.ts, _pad(ev.source, 9), _pad(ev.kind, 12)]
        if ev.agent:
            parts.append(_pad(ev.agent, 16))
        else:
            parts.append(_pad("", 16))
        if ev.section:
            parts.append(f"S{ev.section}")
        else:
            parts.append("   ")
        if ev.model:
            parts.append(_pad(ev.model, 20))
        line = "  ".join(parts) + "  " + ev.detail

        if use_color:
            color = _COLOR_BY_KIND.get(ev.kind, "")
            if color:
                line = color + line + _RESET

        yield line


def _pad(s: str, width: int) -> str:
    if len(s) >= width:
        return s[:width]
    return s + " " * (width - len(s))
