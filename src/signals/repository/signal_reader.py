"""Structured agent signal readers."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from signals.repository.artifact_io import read_json, rename_malformed
from signals.types import AgentSignal, SignalResult


def read_signal_tuple(signal_path: Path) -> SignalResult:
    """Read a structured signal file written by an agent.

    Returns a ``SignalResult`` whose ``signal_type`` is ``None`` when the
    file does not exist, or one of the recognised signal states otherwise.
    Unknown / malformed signals fail closed as ``"needs_parent"``.
    """
    if not signal_path.exists():
        return SignalResult(signal_type=None, detail="")
    data = read_json(signal_path)
    if isinstance(data, dict):
        try:
            signal = AgentSignal.model_validate(data)
        except ValidationError:
            return SignalResult(
                signal_type="needs_parent",
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
        if state in ("underspec", "underspecified"):
            return SignalResult(signal_type="underspec", detail=detail)
        if state in ("need_decision",):
            return SignalResult(signal_type="need_decision", detail=detail)
        if state in ("dependency",):
            return SignalResult(signal_type="dependency", detail=detail)
        if state in ("loop_detected",):
            return SignalResult(signal_type="loop_detected", detail=detail)
        if state in ("out_of_scope", "out-of-scope"):
            return SignalResult(signal_type="out_of_scope", detail=detail)
        if state in ("needs_parent",):
            return SignalResult(signal_type="needs_parent", detail=detail)
        return SignalResult(
            signal_type="needs_parent",
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
        signal_type="needs_parent",
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
