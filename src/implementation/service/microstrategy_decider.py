from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from coordination.repository.notes import list_notes_from, list_notes_to
from orchestrator.path_registry import PathRegistry
from orchestrator.repository.decisions import list_section_decisions_md

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        PromptGuard,
        TaskRouterService,
    )

_TODO_CONTEXT_BEFORE = 3
_TODO_CONTEXT_AFTER = 4


def _list_proposal_signals(signals_dir: Path, section: str) -> list[Path]:
    """Named listing helper for proposal-attempt signals (PAT-0003)."""
    if not signals_dir.is_dir():
        return []
    return sorted(signals_dir.glob(f"proposal-{section}-*.json"))


def extract_todos_from_files(
    codespace: Path, related_files: list[str],
) -> str:
    """Extract TODO/FIXME/HACK blocks from related files.

    Returns a markdown document with each TODO and its surrounding
    context (+-3 lines), grouped by file. Empty string if no TODOs found.
    """
    parts: list[str] = []
    for rel_path in related_files:
        full_path = codespace / rel_path
        if not full_path.exists():
            continue
        try:
            lines = full_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        file_todos: list[str] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(marker in stripped.upper()
                   for marker in ("TODO", "FIXME", "HACK", "XXX")):
                start = max(0, i - _TODO_CONTEXT_BEFORE)
                end = min(len(lines), i + _TODO_CONTEXT_AFTER)
                context = "\n".join(
                    f"  {j + 1}: {lines[j]}" for j in range(start, end)
                )
                file_todos.append(
                    f"**Line {i + 1}**: `{stripped}`\n\n"
                    f"```\n{context}\n```\n"
                )
        if file_todos:
            parts.append(f"### {rel_path}\n\n" + "\n".join(file_todos))

    if not parts:
        return ""
    return "# TODO Blocks (In-Code Microstrategies)\n\n" + "\n".join(parts)


def _gather_complexity_signals(
    planspace: Path, section_number: str,
    artifact_io: ArtifactIOService,
) -> dict[str, str]:
    """Mechanically gather complexity signals from the planspace.

    Returns a dict of signal names to string values suitable for
    embedding in a prompt. No interpretation -- just existence checks
    and counts.
    """
    signals: dict[str, str] = {}
    paths = PathRegistry(planspace)

    # 1-2. Section mode signal + related file count (single read)
    mode_signal = paths.mode_signal(section_number)
    if mode_signal.exists():
        mode_data = artifact_io.read_json(mode_signal)
        if mode_data is not None:
            signals["section_mode"] = mode_data.get("mode", "unknown")
            file_count = len(mode_data.get("related_files", []))
            signals["related_file_count"] = str(file_count) if file_count else "unknown"
        else:
            signals["section_mode"] = "unreadable"
            signals["related_file_count"] = "unknown"
    else:
        signals["section_mode"] = "unknown"
        signals["related_file_count"] = "unknown"

    # 3. Cross-section notes (from other sections to this one, or this to others)
    cross_notes_inbound = list_notes_to(paths, section_number)
    cross_notes_outbound = list_notes_from(paths, section_number)
    total_notes = len(cross_notes_inbound) + len(cross_notes_outbound)
    signals["cross_section_notes"] = f"yes ({total_notes})" if total_notes else "no"

    # 4. Cross-section decisions
    decisions = list_section_decisions_md(paths.decisions_dir(), section_number)
    signals["cross_section_decisions"] = f"yes ({len(decisions)})" if decisions else "no"

    # 5. TODO extraction
    todos_path = paths.todos(section_number)
    signals["todo_extraction_exists"] = "yes" if todos_path.exists() else "no"

    # 6. Previous proposal attempts (proposal signals for this section)
    signals_dir = paths.signals_dir()
    prev_proposals = _list_proposal_signals(signals_dir, section_number)
    signals["previous_proposal_attempts"] = str(len(prev_proposals))

    return signals


def _build_decider_prompt(
    proposal_path: Path,
    section_number: str,
    signal_path: Path,
    complexity: dict[str, str],
) -> str:
    """Build the microstrategy decider prompt."""
    return f"""# Task: Microstrategy Decision for Section {section_number}

## Files to Read
1. Integration proposal: `{proposal_path}`

## Complexity Signals (mechanically gathered)
- Related file count: {complexity["related_file_count"]}
- Cross-section notes: {complexity["cross_section_notes"]}
- Cross-section decisions: {complexity["cross_section_decisions"]}
- TODO extraction exists: {complexity["todo_extraction_exists"]}
- Previous proposal attempts: {complexity["previous_proposal_attempts"]}
- Section mode: {complexity["section_mode"]}

## Instructions
Read the integration proposal and the complexity signals above. Apply your
decision method to determine whether this section needs a microstrategy.

Write a JSON signal to: `{signal_path}`
```json
{{"needs_microstrategy": true, "reason": "..."}}
```
"""


class MicrostrategyDecider:
    """Check if a section needs a microstrategy.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        dispatcher: AgentDispatcher,
        prompt_guard: PromptGuard,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._dispatcher = dispatcher
        self._prompt_guard = prompt_guard
        self._task_router = task_router

    def _read_microstrategy_signal(self, signal_path: Path) -> bool | None:
        """Read the microstrategy signal. Returns True/False if valid, None if missing/malformed."""
        if not signal_path.exists():
            return None
        data = self._artifact_io.read_json(signal_path)
        if data is None:
            print(
                f"[MICROSTRATEGY][WARN] Malformed signal at {signal_path} "
                "— will dispatch fresh",
            )
            return None
        return data.get("needs_microstrategy", False) is True

    def _dispatch_and_read_signal(
        self,
        model: str,
        prompt_path: Path,
        output_path: Path,
        signal_path: Path,
        planspace: Path,
        codespace: Path | None,
        section_number: str,
    ) -> bool | None:
        """Dispatch decider and read signal. Returns True/False or None if failed."""
        self._dispatcher.dispatch(
            model, prompt_path, output_path,
            planspace, codespace=codespace,
            section_number=section_number,
            agent_file=self._task_router.agent_for("implementation.microstrategy_decision"),
        )
        return self._read_microstrategy_signal(signal_path)

    def check_needs_microstrategy(
        self,
        proposal_path: Path, planspace: Path, section_number: str,
        codespace: Path | None = None,
        *,
        model: str,
        escalation_model: str,
    ) -> bool:
        """Check if the microstrategy decider requests a microstrategy.

        Reads the structured signal written by the microstrategy decider.
        Falls back to dispatching the decider to produce the signal if missing.

        If the signal cannot be produced after retries (including escalation),
        defaults to True (fail-closed: prefer more strategy over silent skip).
        """
        paths = PathRegistry(planspace)
        signal_path = paths.microstrategy_signal(section_number)

        cached = self._read_microstrategy_signal(signal_path)
        if cached is not None:
            return cached

        if not proposal_path.exists():
            return False

        artifacts = paths.artifacts
        complexity = _gather_complexity_signals(planspace, section_number, self._artifact_io)
        prompt_text = _build_decider_prompt(
            proposal_path, section_number, signal_path, complexity,
        )
        prompt_path = artifacts / f"microstrategy-decider-{section_number}-prompt.md"
        if not self._prompt_guard.write_validated(prompt_text, prompt_path):
            return False
        signal_path.parent.mkdir(parents=True, exist_ok=True)

        output_path = artifacts / f"microstrategy-decider-{section_number}-output.md"
        result = self._dispatch_and_read_signal(
            model, prompt_path, output_path, signal_path,
            planspace, codespace, section_number,
        )
        if result is not None:
            return result

        print(
            f"[MICROSTRATEGY][WARN] Section {section_number}: "
            "malformed signal after primary attempt — retrying with escalation model",
        )
        escalation_output = artifacts / f"microstrategy-decider-{section_number}-escalation-output.md"
        result = self._dispatch_and_read_signal(
            escalation_model, prompt_path, escalation_output, signal_path,
            planspace, codespace, section_number,
        )
        if result is not None:
            return result

        print(
            f"[MICROSTRATEGY][WARN] Section {section_number}: "
            "malformed signal after escalation — defaulting to fail-closed",
        )
        self._artifact_io.write_json(signal_path, {
            "needs_microstrategy": True,
            "reason": (
                "fail-closed: microstrategy decider produced no valid "
                "signal after retries (default + escalation model)"
            ),
        })
        return True
