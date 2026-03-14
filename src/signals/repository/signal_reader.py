"""Structured agent signal readers."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from signals.repository.artifact_io import read_json, rename_malformed
from signals.types import (
    AgentSignal,
    SignalResult,
    SIGNAL_DEPENDENCY,
    SIGNAL_LOOP_DETECTED,
    SIGNAL_NEED_DECISION,
    SIGNAL_NEEDS_PARENT,
    SIGNAL_OUT_OF_SCOPE,
    SIGNAL_UNDERSPEC,
)

_SIGNAL_STATE_MAP: dict[str, str] = {
    SIGNAL_UNDERSPEC: SIGNAL_UNDERSPEC,
    "underspecified": SIGNAL_UNDERSPEC,
    SIGNAL_NEED_DECISION: SIGNAL_NEED_DECISION,
    SIGNAL_DEPENDENCY: SIGNAL_DEPENDENCY,
    SIGNAL_LOOP_DETECTED: SIGNAL_LOOP_DETECTED,
    SIGNAL_OUT_OF_SCOPE: SIGNAL_OUT_OF_SCOPE,
    "out-of-scope": SIGNAL_OUT_OF_SCOPE,
    SIGNAL_NEEDS_PARENT: SIGNAL_NEEDS_PARENT,
}


def read_signal_tuple(signal_path: Path) -> SignalResult:
    """Read a structured signal file written by an agent.

    Returns a ``SignalResult`` whose ``signal_type`` is ``None`` when the
    file does not exist, or one of the recognised signal states otherwise.
    Unknown / malformed signals fail closed as ``SIGNAL_NEEDS_PARENT``.
    """
    if not signal_path.exists():
        return SignalResult(signal_type=None, detail="")
    data = read_json(signal_path)
    if isinstance(data, dict):
        try:
            signal = AgentSignal.model_validate(data)
        except ValidationError:
            return SignalResult(
                signal_type=SIGNAL_NEEDS_PARENT,
                detail=(
                    f"Signal at {signal_path} failed validation — "
                    f"failing closed"
                ),
            )

        state = signal.state.lower()
        detail = signal.detail
        extras = []
        if signal.needs:
            extras.append(f"Needs: {signal.needs}")
        if signal.assumptions_refused:
            extras.append(f"Refused assumptions: {signal.assumptions_refused}")
        if signal.suggested_escalation_target:
            extras.append(
                f"Escalation target: {signal.suggested_escalation_target}",
            )
        if extras:
            detail = f"{detail} [{'; '.join(extras)}]"
        mapped = _SIGNAL_STATE_MAP.get(state)
        if mapped is not None:
            return SignalResult(signal_type=mapped, detail=detail)
        return SignalResult(
            signal_type=SIGNAL_NEEDS_PARENT,
            detail=(
                f"Unknown signal state '{state}' in {signal_path} — "
                f"failing closed. Original detail: {detail}"
            ),
        )

    exc = "invalid JSON"
    if data is not None:
        exc = "non-object JSON"
        print(
            f"[SIGNAL][WARN] Malformed signal JSON at {signal_path} "
            f"({exc}) — renaming to .malformed.json",
        )
        rename_malformed(signal_path)
    return SignalResult(
        signal_type=SIGNAL_NEEDS_PARENT,
        detail=(
            f"Malformed signal JSON at {signal_path} ({exc}) — "
            f"failing closed"
        ),
    )


def read_agent_signal(signal_path: Path) -> AgentSignal | None:
    """Read a structured JSON signal artifact written by an agent.

    Returns an ``AgentSignal`` instance on success, or ``None`` when the
    file is missing, corrupt, or not a JSON object.  Extra fields beyond
    the base schema are preserved and accessible via attribute access or
    the dict-compatible ``.get()`` helper.
    """
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
    try:
        return AgentSignal.model_validate(data)
    except ValidationError:
        print(
            f"[SIGNAL][WARN] Signal at {signal_path} failed validation "
            f"— renaming to .malformed.json",
        )
        rename_malformed(signal_path)
        return None
