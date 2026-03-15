"""Pure helpers shared by section-loop dispatch callers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from signals.types import SignalResult

if TYPE_CHECKING:
    from containers import ArtifactIOService, SignalReader

_DEFAULT_SUMMARY_MAX_LENGTH = 200


def summarize_output(output: str, max_len: int = _DEFAULT_SUMMARY_MAX_LENGTH) -> str:
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


def extract_fenced_block(text: str, marker: str) -> str | None:
    """Return the first markdown-fenced block whose content contains *marker*.

    Scans ``text`` for triple-backtick fences and returns the raw content
    (without the fence delimiters) of the first block containing *marker*.
    Returns ``None`` if no matching block is found.
    """
    in_fence = False
    fence_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") and not in_fence:
            in_fence = True
            fence_lines = []
            continue
        if stripped.startswith("```") and in_fence:
            candidate = "\n".join(fence_lines)
            if marker in candidate:
                return candidate
            in_fence = False
            continue
        if in_fence:
            fence_lines.append(line)
    return None


class SignalChecker:
    """Helpers that require service dependencies."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        signals: SignalReader,
    ) -> None:
        self._artifact_io = artifact_io
        self._signals = signals

    def write_model_choice_signal(
        self,
        planspace: Path,
        section: str,
        step: str,
        model: str,
        reason: str,
        escalated_from: str | None = None,
    ) -> None:
        """Write a structured model-choice signal for auditability."""
        signals_dir = PathRegistry(planspace).signals_dir()
        signal = {
            "section": section,
            "step": step,
            "model": model,
            "reason": reason,
            "escalated_from": escalated_from,
        }
        signal_path = signals_dir / f"model-choice-{section}-{step}.json"
        self._artifact_io.write_json(signal_path, signal)

    def check_agent_signals(
        self,
        signal_path: Path | None = None,
    ) -> SignalResult:
        """Check for agent signals via the structured JSON file."""
        if signal_path:
            result = self._signals.read_tuple(signal_path)
            if result.signal_type:
                return result
        return SignalResult(signal_type=None, detail="")
