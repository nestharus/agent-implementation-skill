import json
from pathlib import Path

from ..dispatch import dispatch_agent


def _gather_complexity_signals(
    planspace: Path, section_number: str,
) -> dict[str, str]:
    """Mechanically gather complexity signals from the planspace.

    Returns a dict of signal names to string values suitable for
    embedding in a prompt. No interpretation — just existence checks
    and counts.
    """
    signals: dict[str, str] = {}
    artifacts = planspace / "artifacts"

    # 1. Section mode signal
    mode_signal = artifacts / "signals" / f"section-{section_number}-mode.json"
    if mode_signal.exists():
        try:
            mode_data = json.loads(mode_signal.read_text(encoding="utf-8"))
            signals["section_mode"] = mode_data.get("mode", "unknown")
        except (json.JSONDecodeError, OSError):
            signals["section_mode"] = "unreadable"
    else:
        signals["section_mode"] = "unknown"

    # 2. Related file count from mode signal (if it contains file info)
    if mode_signal.exists():
        try:
            mode_data = json.loads(mode_signal.read_text(encoding="utf-8"))
            file_count = len(mode_data.get("related_files", []))
            signals["related_file_count"] = str(file_count) if file_count else "unknown"
        except (json.JSONDecodeError, OSError):
            signals["related_file_count"] = "unknown"
    else:
        signals["related_file_count"] = "unknown"

    # 3. Cross-section notes (from other sections to this one, or this to others)
    notes_dir = artifacts / "notes"
    cross_notes_inbound = sorted(notes_dir.glob(f"from-*-to-{section_number}.md")) if notes_dir.is_dir() else []
    cross_notes_outbound = sorted(notes_dir.glob(f"from-{section_number}-to-*.md")) if notes_dir.is_dir() else []
    total_notes = len(cross_notes_inbound) + len(cross_notes_outbound)
    signals["cross_section_notes"] = f"yes ({total_notes})" if total_notes else "no"

    # 4. Cross-section decisions
    decisions_dir = artifacts / "decisions"
    decisions = sorted(decisions_dir.glob(f"section-{section_number}*.md")) if decisions_dir.is_dir() else []
    signals["cross_section_decisions"] = f"yes ({len(decisions)})" if decisions else "no"

    # 5. TODO extraction
    todos_path = artifacts / "todos" / f"section-{section_number}-todos.md"
    signals["todo_extraction_exists"] = "yes" if todos_path.exists() else "no"

    # 6. Previous proposal attempts (proposal signals for this section)
    signals_dir = artifacts / "signals"
    prev_proposals = sorted(signals_dir.glob(f"proposal-{section_number}-*.json")) if signals_dir.is_dir() else []
    signals["previous_proposal_attempts"] = str(len(prev_proposals))

    return signals


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
    escalation_model: str = "gpt-5.4-xhigh",
) -> bool:
    """Check if the integration proposal requests a microstrategy.

    Reads the structured signal from the proposal's JSON output.
    Falls back to dispatch to produce the signal if missing.

    If the signal cannot be produced after retries (including escalation),
    defaults to True (fail-closed: prefer more strategy over silent skip).

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
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[MICROSTRATEGY][WARN] Malformed signal at {signal_path} "
                f"({exc}) — renaming and dispatching fresh",
            )
            try:
                signal_path.rename(
                    signal_path.with_suffix(".malformed.json"))
            except OSError:
                pass
            # Fall through to dispatch

    # Fallback: dispatch to produce structured microstrategy signal
    if not proposal_path.exists():
        return False
    artifacts = planspace / "artifacts"
    decider_prompt = artifacts / f"microstrategy-decider-{section_number}-prompt.md"
    decider_output = artifacts / f"microstrategy-decider-{section_number}-output.md"
    complexity = _gather_complexity_signals(planspace, section_number)
    decider_prompt.write_text(f"""# Task: Microstrategy Decision for Section {section_number}

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
""", encoding="utf-8")
    signal_path.parent.mkdir(parents=True, exist_ok=True)

    # First attempt with default model
    dispatch_agent(
        model, decider_prompt, decider_output,
        planspace, parent, codespace=codespace,
        section_number=section_number,
        agent_file="microstrategy-decider.md",
    )
    if signal_path.exists():
        try:
            data = json.loads(signal_path.read_text(encoding="utf-8"))
            return data.get("needs_microstrategy", False) is True
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[MICROSTRATEGY][WARN] Section {section_number}: "
                f"malformed signal after primary attempt ({exc}) "
                f"— retrying with escalation model",
            )

    # Retry with escalation model (R34/V3: fail-closed microstrategy)
    escalation_output = (
        artifacts
        / f"microstrategy-decider-{section_number}-escalation-output.md"
    )
    dispatch_agent(
        escalation_model, decider_prompt, escalation_output,
        planspace, parent, codespace=codespace,
        section_number=section_number,
        agent_file="microstrategy-decider.md",
    )
    if signal_path.exists():
        try:
            data = json.loads(signal_path.read_text(encoding="utf-8"))
            return data.get("needs_microstrategy", False) is True
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[MICROSTRATEGY][WARN] Section {section_number}: "
                f"malformed signal after escalation attempt ({exc}) "
                f"— defaulting to fail-closed (needs microstrategy)",
            )

    # Both attempts failed — fail-closed: default to more strategy
    fallback = {
        "needs_microstrategy": True,
        "reason": (
            "fail-closed: microstrategy decider produced no valid "
            "signal after retries (default + escalation model)"
        ),
    }
    signal_path.write_text(
        json.dumps(fallback, indent=2), encoding="utf-8")
    return True
