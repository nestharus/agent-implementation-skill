import json
from pathlib import Path

from ..dispatch import dispatch_agent


def _extract_todos_from_files(
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
                start = max(0, i - 3)
                end = min(len(lines), i + 4)
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


def _check_needs_microstrategy(
    proposal_path: Path, planspace: Path, section_number: str,
    parent: str = "", codespace: Path | None = None,
    model: str = "glm",
) -> bool:
    """Check if the integration proposal requests a microstrategy.

    Reads the structured signal from the proposal's JSON output.
    Falls back to dispatch to produce the signal if missing.

    The ``model`` parameter defaults to ``"glm"`` but callers should
    pass ``policy["microstrategy_decider"]`` for policy-driven selection.
    """
    # Primary: structured JSON signal
    signal_path = (planspace / "artifacts" / "signals"
                   / f"proposal-{section_number}-microstrategy.json")
    if signal_path.exists():
        try:
            data = json.loads(signal_path.read_text(encoding="utf-8"))
            return data.get("needs_microstrategy", False) is True
        except (json.JSONDecodeError, OSError):
            pass  # Fall through to GLM dispatch

    # Fallback: dispatch GLM to produce structured microstrategy signal
    if not proposal_path.exists():
        return False
    artifacts = planspace / "artifacts"
    decider_prompt = artifacts / f"microstrategy-decider-{section_number}-prompt.md"
    decider_output = artifacts / f"microstrategy-decider-{section_number}-output.md"
    decider_prompt.write_text(f"""# Task: Microstrategy Decision for Section {section_number}

## Files to Read
1. Integration proposal: `{proposal_path}`

## Instructions
Read the integration proposal and determine whether this section needs a
microstrategy (a tactical per-file breakdown between the proposal and
implementation).

A microstrategy is needed when:
- The proposal touches 5+ files
- The changes involve complex cross-file dependencies
- The order of changes matters

Write a JSON signal to: `{signal_path}`
```json
{{"needs_microstrategy": true, "reason": "..."}}
```
""", encoding="utf-8")
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    dispatch_agent(
        model, decider_prompt, decider_output,
        planspace, parent, codespace=codespace,
        section_number=section_number,
    )
    # Re-read the signal file
    if signal_path.exists():
        try:
            data = json.loads(signal_path.read_text(encoding="utf-8"))
            return data.get("needs_microstrategy", False) is True
        except (json.JSONDecodeError, OSError):
            pass
    return False
