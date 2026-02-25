import json
import re
from pathlib import Path
from typing import Any

from ..communication import _log_artifact, log
from ..dispatch import read_agent_signal
from ..types import Section, SectionResult


def build_file_to_sections(sections: list[Section]) -> dict[str, list[str]]:
    """Map each file path to the section numbers that reference it."""
    mapping: dict[str, list[str]] = {}
    for sec in sections:
        for f in sec.related_files:
            mapping.setdefault(f, []).append(sec.number)
    return mapping


def _collect_outstanding_problems(
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
) -> list[dict[str, Any]]:
    """Collect all outstanding problems across sections.

    Includes both misaligned sections AND unaddressed consequence notes
    from the cross-section communication system.

    Returns a list of problem dicts, each with:
      - section: section number
      - type: "misaligned" | "unaddressed_note"
      - description: the problem text
      - files: list of files related to this section
    """
    problems = []
    for sec_num, result in section_results.items():
        if result.aligned:
            continue
        section = sections_by_num.get(sec_num)
        files = list(section.related_files) if section else []

        # Check for structured blocker signal — routes as "needs_parent"
        # instead of "misaligned", excluded from code-fix dispatch.
        blocker_path = (planspace / "artifacts" / "signals"
                        / f"section-{sec_num}-blocker.json")
        if blocker_path.exists():
            try:
                blocker = json.loads(
                    blocker_path.read_text(encoding="utf-8"))
                if blocker.get("state") == "needs_parent":
                    problems.append({
                        "section": sec_num,
                        "type": "needs_parent",
                        "description": blocker.get("detail", ""),
                        "needs": blocker.get("needs", ""),
                        "files": files,
                    })
                    continue
            except (json.JSONDecodeError, OSError) as exc:
                # Fail-closed: malformed blocker → needs_parent, not
                # misaligned (which would trigger code-fix dispatch).
                problems.append({
                    "section": sec_num,
                    "type": "needs_parent",
                    "description": (
                        f"Blocker signal at {blocker_path} is malformed "
                        f"({exc}); cannot determine blocker state — "
                        f"routing as needs_parent for manual repair."
                    ),
                    "needs": "Valid blocker signal JSON",
                    "files": files,
                })
                continue

        if result.problems:
            problems.append({
                "section": sec_num,
                "type": "misaligned",
                "description": result.problems,
                "files": files,
            })

    # Scan for unaddressed consequence notes.  Parse the canonical note
    # ID from the note content (do NOT recompute the hash — the original
    # was derived from a draft subset that differs from the full content).
    # Target sections acknowledge notes via signals/note-ack-<target>.json.
    notes_dir = planspace / "artifacts" / "notes"
    if notes_dir.exists():
        for note_path in sorted(notes_dir.glob("from-*-to-*.md")):
            name_match = re.match(
                r'from-(.+)-to-(\d+)\.md', note_path.name,
            )
            if not name_match:
                continue
            target_num = name_match.group(2)
            source_label = name_match.group(1)
            target_result = section_results.get(target_num)
            if not target_result or not target_result.aligned:
                continue  # target isn't aligned yet — will see note

            # Parse note ID from the note content (canonical)
            note_content = note_path.read_text(encoding="utf-8")
            note_id_match = re.search(
                r'\*\*Note ID\*\*:\s*`([^`]+)`', note_content)
            if not note_id_match:
                continue  # malformed note — skip
            note_id = note_id_match.group(1)

            # Check acknowledgment via structured signal
            ack_path = (planspace / "artifacts" / "signals"
                        / f"note-ack-{target_num}.json")
            ack_signal = read_agent_signal(ack_path)
            if ack_signal:
                acks = ack_signal.get("acknowledged", [])
                matching_ack = next(
                    (a for a in acks if a.get("note_id") == note_id),
                    None,
                )
                if matching_ack:
                    action = matching_ack.get("action", "accepted")
                    if action == "accepted":
                        continue  # resolved

                    section = sections_by_num.get(target_num)
                    files = list(section.related_files) if section else []
                    if action == "rejected":
                        # Genuine disagreement — escalate to coordinator
                        problems.append({
                            "section": target_num,
                            "type": "consequence_conflict",
                            "note_id": note_id,
                            "description": (
                                f"Section {target_num} REJECTED note "
                                f"{note_id} from {source_label}. "
                                f"Reason: "
                                f"{matching_ack.get('reason', '(none)')}. "
                                f"This conflict needs coordinator "
                                f"resolution."
                            ),
                            "files": files,
                        })
                        continue
                    if action == "deferred":
                        # Pending — track but don't force full requeue
                        problems.append({
                            "section": target_num,
                            "type": "pending_negotiation",
                            "note_id": note_id,
                            "description": (
                                f"Section {target_num} deferred note "
                                f"{note_id} from section {source_label}. "
                                f"Reason: "
                                f"{matching_ack.get('reason', '(none)')}. "
                                f"Will re-evaluate when blocking "
                                f"conditions resolve."
                            ),
                            "files": files,
                        })
                        continue

            # No ack at all — note is unaddressed
            section = sections_by_num.get(target_num)
            files = list(section.related_files) if section else []
            problems.append({
                "section": target_num,
                "type": "unaddressed_note",
                "note_id": note_id,
                "note_path": str(note_path),
                "description": (
                    f"Consequence note {note_id} from section "
                    f"{source_label} has not been acknowledged by "
                    f"section {target_num}. "
                    f"See note file: `{note_path}`"
                ),
                "files": files,
            })
    return problems


def _detect_recurrence_patterns(
    planspace: Path,
    problems: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Detect recurring problem signatures across coordination rounds.

    Reads recurrence signals and previous coordination state to identify
    systemic patterns. Returns a recurrence report dict, or None if no
    patterns found.
    """
    signals_dir = planspace / "artifacts" / "signals"
    if not signals_dir.exists():
        return None

    recurring_sections: list[dict[str, Any]] = []
    for sig_path in sorted(signals_dir.glob("section-*-recurrence.json")):
        try:
            data = json.loads(sig_path.read_text(encoding="utf-8"))
            if data.get("recurring"):
                recurring_sections.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            log(f"Recurrence signal malformed at {sig_path} ({exc}) "
                "— preserving as .malformed.json")
            try:
                sig_path.rename(sig_path.with_suffix(".malformed.json"))
            except OSError:
                pass  # Best-effort preserve
            continue

    if not recurring_sections:
        return None

    # Cross-reference with current problems
    recurring_section_nums = {
        str(r["section"]) for r in recurring_sections
    }
    recurring_problems = [
        p for p in problems if p["section"] in recurring_section_nums
    ]

    if not recurring_problems:
        return None

    report = {
        "recurring_sections": [r["section"] for r in recurring_sections],
        "recurring_problem_count": len(recurring_problems),
        "max_attempt": max(r.get("attempt", 0) for r in recurring_sections),
        "problem_indices": [
            i for i, p in enumerate(problems)
            if p["section"] in recurring_section_nums
        ],
    }

    # Persist recurrence report
    coord_dir = planspace / "artifacts" / "coordination"
    coord_dir.mkdir(parents=True, exist_ok=True)
    recurrence_path = coord_dir / "recurrence.json"
    recurrence_path.write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    _log_artifact(planspace, "coordination:recurrence")

    log(f"  coordinator: recurrence detected — "
        f"{len(recurring_sections)} sections with "
        f"{len(recurring_problems)} recurring problems")

    return report
