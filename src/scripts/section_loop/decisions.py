"""Structured decision artifacts for the section loop.

Provides machine-readable JSON sidecars alongside the existing prose
decision files. Both formats are written from the same in-memory
``Decision`` object to prevent drift.

JSON sidecars live at ``artifacts/decisions/section-NN.json`` (one JSON
array per section). The existing ``section-NN.md`` prose files continue
to be maintained as the human-readable complement.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclasses.dataclass
class Decision:
    """A single structured decision record."""

    id: str                          # unique decision ID (e.g., "d-001")
    scope: str                       # "section" or "global"
    section: str | None              # section number if section-scoped
    problem_id: str | None           # parent problem this addresses
    parent_problem_id: str | None    # for recursive problem structure
    concern_scope: str               # what concern this is about
    proposal_summary: str            # what was decided
    alignment_to_parent: str | None  # how this aligns to parent problem
    status: str                      # "decided" / "superseded" / "partial"
    new_child_problems: list[str] = dataclasses.field(default_factory=list)
    why_unsolved: str | None = None  # if partial, why
    evidence: list[str] = dataclasses.field(default_factory=list)
    next_action: str | None = None   # what should happen next
    timestamp: str = ""              # ISO format, set at write time


def record_decision(decisions_dir: Path, decision: Decision) -> None:
    """Write both JSON sidecar and prose appendix from a single Decision.

    The JSON sidecar is ``decisions_dir/section-NN.json`` (a JSON array
    of decision dicts). The prose appendix is appended to
    ``decisions_dir/section-NN.md``.

    For global-scope decisions (``decision.section is None``), the files
    are named ``global.json`` and ``global.md``.

    This is the **single write path** for decision artifacts. Both
    formats derive from the same in-memory object to prevent drift.
    """
    decisions_dir.mkdir(parents=True, exist_ok=True)

    # Fill timestamp if not set
    if not decision.timestamp:
        decision.timestamp = datetime.now(timezone.utc).isoformat()

    stem = f"section-{decision.section}" if decision.section else "global"

    # --- JSON sidecar (append to array) ---
    json_path = decisions_dir / f"{stem}.json"
    existing: list[dict[str, Any]] = []
    if json_path.exists():
        try:
            existing = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.append(dataclasses.asdict(decision))
    json_path.write_text(
        json.dumps(existing, indent=2) + "\n", encoding="utf-8",
    )

    # --- Prose appendix (append to markdown) ---
    md_path = decisions_dir / f"{stem}.md"
    child_problems = ""
    if decision.new_child_problems:
        items = "\n".join(f"  - {p}" for p in decision.new_child_problems)
        child_problems = f"\n- **New child problems**:\n{items}"
    why_line = ""
    if decision.why_unsolved:
        why_line = f"\n- **Why unsolved**: {decision.why_unsolved}"
    evidence_line = ""
    if decision.evidence:
        items = ", ".join(f"`{e}`" for e in decision.evidence)
        evidence_line = f"\n- **Evidence**: {items}"
    next_line = ""
    if decision.next_action:
        next_line = f"\n- **Next action**: {decision.next_action}"
    alignment_line = ""
    if decision.alignment_to_parent:
        alignment_line = (
            f"\n- **Alignment to parent**: {decision.alignment_to_parent}"
        )

    prose = (
        f"\n## Decision {decision.id} ({decision.status})\n\n"
        f"- **Scope**: {decision.scope}"
        f"{f' (section {decision.section})' if decision.section else ''}\n"
        f"- **Concern**: {decision.concern_scope}\n"
        f"- **Summary**: {decision.proposal_summary}\n"
        f"- **Status**: {decision.status}"
        f"{alignment_line}"
        f"{child_problems}"
        f"{why_line}"
        f"{evidence_line}"
        f"{next_line}\n"
        f"- **Timestamp**: {decision.timestamp}\n"
    )

    with md_path.open("a", encoding="utf-8") as f:
        f.write(prose)


def load_decisions(
    decisions_dir: Path,
    section: str | None = None,
) -> list[Decision]:
    """Load decisions from JSON sidecars.

    If ``section`` is provided, loads only that section's decisions.
    Otherwise loads all decision files found in ``decisions_dir``.

    Returns an empty list if the directory or files do not exist.
    """
    if not decisions_dir.exists():
        return []

    results: list[Decision] = []

    if section is not None:
        paths = [decisions_dir / f"section-{section}.json"]
    else:
        paths = sorted(decisions_dir.glob("*.json"))

    for json_path in paths:
        if not json_path.exists():
            continue
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                results.append(Decision(
                    id=entry.get("id", ""),
                    scope=entry.get("scope", ""),
                    section=entry.get("section"),
                    problem_id=entry.get("problem_id"),
                    parent_problem_id=entry.get("parent_problem_id"),
                    concern_scope=entry.get("concern_scope", ""),
                    proposal_summary=entry.get("proposal_summary", ""),
                    alignment_to_parent=entry.get("alignment_to_parent"),
                    status=entry.get("status", "decided"),
                    new_child_problems=entry.get(
                        "new_child_problems", []),
                    why_unsolved=entry.get("why_unsolved"),
                    evidence=entry.get("evidence", []),
                    next_action=entry.get("next_action"),
                    timestamp=entry.get("timestamp", ""),
                ))
            except (TypeError, KeyError):
                continue

    return results


def build_strategic_state(
    decisions_dir: Path,
    section_results: dict[str, Any],
) -> dict[str, Any]:
    """Derive the current strategic-state snapshot.

    Writes ``artifacts/strategic-state.json`` (sibling of the
    decisions directory) and returns the snapshot dict.

    ``section_results`` maps section numbers to objects with at least
    ``.aligned`` (bool) and ``.problems`` (str | None) attributes, or
    plain dicts with those keys.
    """
    decisions = load_decisions(decisions_dir)

    completed: list[str] = []
    in_progress: str | None = None
    blocked: dict[str, dict[str, str]] = {}
    open_problems: list[dict[str, str]] = []

    for sec_num, result in sorted(section_results.items()):
        # Support both dataclass instances and plain dicts
        if isinstance(result, dict):
            aligned = result.get("aligned", False)
            problems = result.get("problems")
        else:
            aligned = getattr(result, "aligned", False)
            problems = getattr(result, "problems", None)

        if aligned:
            completed.append(sec_num)
        elif problems and "needs_parent" in str(problems):
            # Extract problem ID if present in the problems string
            problem_id = ""
            if ":" in str(problems):
                parts = str(problems).split(":")
                for part in parts:
                    part = part.strip()
                    if part.startswith("p-"):
                        problem_id = part
                        break
            blocked[sec_num] = {
                "problem_id": problem_id,
                "reason": str(problems)[:200],
            }
        elif not aligned:
            if in_progress is None:
                in_progress = sec_num
            open_problems.append({
                "id": f"p-{sec_num}",
                "scope": f"section-{sec_num}",
                "summary": (str(problems)[:200] if problems
                            else "unresolved"),
            })

    # Collect key decision IDs and open child problems from decisions
    key_decision_ids = [
        d.id for d in decisions if d.status == "decided"
    ]
    for d in decisions:
        for child in d.new_child_problems:
            if not any(op["id"] == child for op in open_problems):
                open_problems.append({
                    "id": child,
                    "scope": (f"section-{d.section}"
                              if d.section else "global"),
                    "summary": f"child problem from {d.id}",
                })

    # Count coordination rounds from decision timestamps
    coordination_rounds = 0
    for d in decisions:
        if d.scope == "global":
            coordination_rounds += 1

    snapshot: dict[str, Any] = {
        "completed_sections": sorted(completed),
        "in_progress": in_progress,
        "blocked": blocked,
        "open_problems": open_problems,
        "key_decisions": key_decision_ids,
        "coordination_rounds": coordination_rounds,
        "next_action": _derive_next_action(
            completed, in_progress, blocked, open_problems),
    }

    # Write to artifacts/strategic-state.json (parent of decisions_dir)
    artifacts_dir = decisions_dir.parent
    state_path = artifacts_dir / "strategic-state.json"
    state_path.write_text(
        json.dumps(snapshot, indent=2) + "\n", encoding="utf-8",
    )

    return snapshot


def _derive_next_action(
    completed: list[str],
    in_progress: str | None,
    blocked: dict[str, dict[str, str]],
    open_problems: list[dict[str, str]],
) -> str | None:
    """Derive the next recommended action from strategic state."""
    if blocked:
        first_blocked = next(iter(blocked))
        return f"resolve blocker for section {first_blocked}"
    if in_progress:
        return f"section-{in_progress} alignment check"
    if open_problems:
        return f"address open problem: {open_problems[0]['id']}"
    if completed:
        return "all sections complete — ready for coordination"
    return None
