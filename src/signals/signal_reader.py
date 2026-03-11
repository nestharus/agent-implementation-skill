"""Structured agent signal readers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from signals.artifact_io import read_json, rename_malformed


def read_signal_tuple(signal_path: Path) -> tuple[str | None, str]:
    """Read a structured signal file written by an agent."""
    if not signal_path.exists():
        return None, ""
    data = read_json(signal_path)
    if isinstance(data, dict):
        state = data.get("state", "").lower()
        detail = data.get("detail", "")
        needs = data.get("needs", "")
        refused = data.get("assumptions_refused", "")
        target = data.get("suggested_escalation_target", "")
        extras = []
        if needs:
            extras.append(f"Needs: {needs}")
        if refused:
            extras.append(f"Refused assumptions: {refused}")
        if target:
            extras.append(f"Escalation target: {target}")
        if extras:
            detail = f"{detail} [{'; '.join(extras)}]"
        if state in ("underspec", "underspecified"):
            return "underspec", detail
        if state in ("need_decision",):
            return "need_decision", detail
        if state in ("dependency",):
            return "dependency", detail
        if state in ("loop_detected",):
            return "loop_detected", detail
        if state in ("out_of_scope", "out-of-scope"):
            return "out_of_scope", detail
        if state in ("needs_parent",):
            return "needs_parent", detail
        return "needs_parent", (
            f"Unknown signal state '{state}' in {signal_path} — "
            f"failing closed. Original detail: {detail}"
        )

    exc = "invalid JSON"
    if data is not None:
        exc = "non-object JSON"
        print(
            f"[SIGNAL][WARN] Malformed signal JSON at {signal_path} "
            f"({exc}) — renaming to .malformed.json",
        )
        rename_malformed(signal_path)
    return "needs_parent", (
        f"Malformed signal JSON at {signal_path} ({exc}) — "
        f"failing closed"
    )


def read_agent_signal(
    signal_path: Path, expected_fields: list[str] | None = None,
) -> dict[str, Any] | None:
    """Read a structured JSON signal artifact written by an agent."""
    if not signal_path.exists():
        return None
    data = read_json(signal_path)
    if data is None:
        print(
            f"[SIGNAL][WARN] Malformed JSON in {signal_path} "
            f"— renaming to .malformed.json",
        )
        return None
    if not isinstance(data, dict):
        print(
            f"[SIGNAL][WARN] Signal at {signal_path} is not a JSON object "
            f"— renaming to .malformed.json",
        )
        rename_malformed(signal_path)
        return None
    if expected_fields:
        for field_name in expected_fields:
            if field_name not in data:
                return None
    return data
