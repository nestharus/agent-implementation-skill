"""Pure helpers shared by section-loop dispatch callers."""

from __future__ import annotations

from pathlib import Path

from lib.core.artifact_io import write_json
from lib.core.path_registry import PathRegistry
from lib.services.signal_reader import read_signal_tuple


def summarize_output(output: str, max_len: int = 200) -> str:
    """Extract a brief summary from agent output for status messages."""
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("summary:"):
            return stripped[len("summary:"):].strip()[:max_len]
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped[:max_len]
    return "(no output)"


def write_model_choice_signal(
    planspace: Path,
    section: str,
    step: str,
    model: str,
    reason: str,
    escalated_from: str | None = None,
) -> None:
    """Write a structured model-choice signal for auditability."""
    signals_dir = PathRegistry(planspace).signals_dir()
    signals_dir.mkdir(parents=True, exist_ok=True)
    signal = {
        "section": section,
        "step": step,
        "model": model,
        "reason": reason,
        "escalated_from": escalated_from,
    }
    signal_path = signals_dir / f"model-choice-{section}-{step}.json"
    write_json(signal_path, signal)


def check_agent_signals(
    output: str,
    signal_path: Path | None = None,
    output_path: Path | None = None,
    planspace: Path | None = None,
    parent: str | None = None,
    codespace: Path | None = None,
) -> tuple[str | None, str]:
    """Check for agent signals via the structured JSON file."""
    del output, output_path, planspace, parent, codespace
    if signal_path:
        sig, detail = read_signal_tuple(signal_path)
        if sig:
            return sig, detail
    return None, ""
