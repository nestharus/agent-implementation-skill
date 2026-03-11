"""DecisionRepository: structured decision artifact persistence."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signals.repository.artifact_io import read_json, rename_malformed, write_json


@dataclasses.dataclass
class Decision:
    """A single structured decision record."""

    id: str
    scope: str
    section: str | None
    problem_id: str | None
    parent_problem_id: str | None
    concern_scope: str
    proposal_summary: str
    alignment_to_parent: str | None
    status: str
    new_child_problems: list[str] = dataclasses.field(default_factory=list)
    why_unsolved: str | None = None
    evidence: list[str] = dataclasses.field(default_factory=list)
    next_action: str | None = None
    timestamp: str = ""


def record_decision(decisions_dir: Path, decision: Decision) -> None:
    """Write both JSON sidecar and prose appendix from a single Decision."""
    decisions_dir.mkdir(parents=True, exist_ok=True)

    if not decision.timestamp:
        decision.timestamp = datetime.now(timezone.utc).isoformat()

    stem = f"section-{decision.section}" if decision.section else "global"
    json_path = decisions_dir / f"{stem}.json"
    existed = json_path.exists()
    loaded = read_json(json_path)
    if loaded is None:
        if existed:
            print(
                f"[DECISIONS][WARN] Malformed decision JSON at {json_path} "
                f"— renaming to .malformed.json"
            )
        existing: list[dict[str, Any]] = []
    else:
        existing = loaded
    existing.append(dataclasses.asdict(decision))
    write_json(json_path, existing)

    md_path = decisions_dir / f"{stem}.md"
    with md_path.open("a", encoding="utf-8") as handle:
        handle.write(_format_prose_entry(decision))


def load_decisions(
    decisions_dir: Path,
    section: str | None = None,
    warnings: list[str] | None = None,
) -> list[Decision]:
    """Load decisions from JSON sidecars."""
    if not decisions_dir.exists():
        return []

    results: list[Decision] = []
    paths = (
        [decisions_dir / f"section-{section}.json"]
        if section is not None
        else sorted(decisions_dir.glob("*.json"))
    )

    for json_path in paths:
        if not json_path.exists():
            continue
        raw = read_json(json_path)
        if raw is None:
            msg = (
                f"Malformed decision JSON at {json_path} "
                f"— renaming to .malformed.json"
            )
            print(f"[DECISIONS][WARN] {msg}")
            if warnings is not None:
                warnings.append(msg)
            continue
        if not isinstance(raw, list):
            msg = (
                f"Decision JSON at {json_path} is not a list "
                f"— renaming to .malformed.json"
            )
            print(f"[DECISIONS][WARN] {msg}")
            if warnings is not None:
                warnings.append(msg)
            rename_malformed(json_path)
            continue
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                results.append(_decision_from_entry(entry))
            except (TypeError, KeyError):
                continue

    return results


def _decision_from_entry(entry: dict[str, Any]) -> Decision:
    return Decision(
        id=entry.get("id", ""),
        scope=entry.get("scope", ""),
        section=entry.get("section"),
        problem_id=entry.get("problem_id"),
        parent_problem_id=entry.get("parent_problem_id"),
        concern_scope=entry.get("concern_scope", ""),
        proposal_summary=entry.get("proposal_summary", ""),
        alignment_to_parent=entry.get("alignment_to_parent"),
        status=entry.get("status", "decided"),
        new_child_problems=entry.get("new_child_problems", []),
        why_unsolved=entry.get("why_unsolved"),
        evidence=entry.get("evidence", []),
        next_action=entry.get("next_action"),
        timestamp=entry.get("timestamp", ""),
    )


def _format_prose_entry(decision: Decision) -> str:
    child_problems = ""
    if decision.new_child_problems:
        items = "\n".join(f"  - {problem}" for problem in decision.new_child_problems)
        child_problems = f"\n- **New child problems**:\n{items}"

    why_line = ""
    if decision.why_unsolved:
        why_line = f"\n- **Why unsolved**: {decision.why_unsolved}"

    evidence_line = ""
    if decision.evidence:
        items = ", ".join(f"`{item}`" for item in decision.evidence)
        evidence_line = f"\n- **Evidence**: {items}"

    next_line = ""
    if decision.next_action:
        next_line = f"\n- **Next action**: {decision.next_action}"

    alignment_line = ""
    if decision.alignment_to_parent:
        alignment_line = (
            f"\n- **Alignment to parent**: {decision.alignment_to_parent}"
        )

    return (
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
