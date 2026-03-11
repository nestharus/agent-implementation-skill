"""Shared helpers for coordination problem aggregation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from signals.repository.artifact_io import read_json, rename_malformed, write_json
from coordination.repository.notes import read_incoming_notes as load_incoming_notes
from orchestrator.path_registry import PathRegistry
from signals.service.communication import _log_artifact, log
from signals.repository.signal_reader import read_agent_signal
from orchestrator.types import Section, SectionResult


def build_file_to_sections(sections: list[Section]) -> dict[str, list[str]]:
    """Map each file path to the section numbers that reference it."""
    mapping: dict[str, list[str]] = {}
    for sec in sections:
        for file_path in sec.related_files:
            mapping.setdefault(file_path, []).append(sec.number)
    return mapping


def _collect_outstanding_problems(
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
) -> list[dict[str, Any]]:
    """Collect all outstanding problems across sections."""
    paths = PathRegistry(planspace)
    problems = []
    for sec_num, result in section_results.items():
        if result.aligned:
            continue
        section = sections_by_num.get(sec_num)
        files = list(section.related_files) if section else []

        blocker_path = paths.blocker_signal(sec_num)
        if blocker_path.exists():
            blocker = read_json(blocker_path)
            if blocker is not None:
                if blocker.get("state") == "needs_parent":
                    problems.append({
                        "section": sec_num,
                        "type": "needs_parent",
                        "description": blocker.get("detail", ""),
                        "needs": blocker.get("needs", ""),
                        "files": files,
                    })
                    continue
            else:
                problems.append({
                    "section": sec_num,
                    "type": "needs_parent",
                    "description": (
                        f"Blocker signal at {blocker_path} is malformed "
                        "or unreadable; cannot determine blocker state — "
                        f"routing as needs_parent for manual repair."
                    ),
                    "needs": "Valid blocker signal JSON",
                    "files": files,
                })
                rename_malformed(blocker_path)
                continue

        if result.problems:
            problems.append({
                "section": sec_num,
                "type": "misaligned",
                "description": result.problems,
                "files": files,
            })

    note_entries: list[dict[str, Any]] = []
    for target_num in sorted(section_results):
        note_entries.extend(load_incoming_notes(planspace, target_num))
    for note in sorted(note_entries, key=lambda entry: entry["path"].name):
        note_path = note["path"]
        target_num = note["target"]
        source_label = note["source"]
        target_result = section_results.get(target_num)
        if not target_result or not target_result.aligned:
            continue

        note_content = note["content"]
        note_id_match = re.search(
            r'\*\*Note ID\*\*:\s*`([^`]+)`', note_content,
        )
        if not note_id_match:
            continue
        note_id = note_id_match.group(1)

        ack_path = paths.signals_dir() / f"note-ack-{target_num}.json"
        ack_signal = read_agent_signal(ack_path)
        if ack_signal:
            acks = ack_signal.get("acknowledged", [])
            matching_ack = next(
                (ack for ack in acks if ack.get("note_id") == note_id),
                None,
            )
            if matching_ack:
                action = matching_ack.get("action", "accepted")
                if action == "accepted":
                    continue

                section = sections_by_num.get(target_num)
                files = list(section.related_files) if section else []
                if action == "rejected":
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

    scope_deltas_dir = paths.scope_deltas_dir()
    if not scope_deltas_dir.exists():
        return problems

    for delta_path in sorted(scope_deltas_dir.iterdir()):
        if delta_path.suffix != ".json" or delta_path.name.endswith(".malformed.json"):
            continue

        delta = read_json(delta_path)
        if delta is None:
            log(
                f"  coordinator: WARNING — malformed scope-delta "
                f"{delta_path.name}, preserving as .malformed.json",
            )
            continue
        if not isinstance(delta, dict):
            log(
                f"  coordinator: WARNING — invalid scope-delta "
                f"{delta_path.name} (expected object), preserving as .malformed.json",
            )
            rename_malformed(delta_path)
            continue
        if delta.get("adjudicated") or not delta.get("requires_root_reframing", False):
            continue

        linked_sections = [
            str(section)
            for section in delta.get("source_sections", [])
            if str(section).strip()
        ]
        if not linked_sections:
            section = str(delta.get("section", "")).strip()
            if section:
                linked_sections.append(section)
        linked_sections = sorted(set(linked_sections))
        if not linked_sections:
            log(
                f"  coordinator: WARNING — root-reframing scope-delta "
                f"{delta_path.name} has no linked sections; leaving it pending",
            )
            continue

        delta_id = str(delta.get("delta_id", delta_path.stem))
        title = str(delta.get("title") or delta.get("summary") or delta_id)
        source = str(delta.get("source") or delta.get("origin") or "unknown")
        source_sections = ", ".join(linked_sections)
        for sec_num in linked_sections:
            section = sections_by_num.get(sec_num)
            files = list(section.related_files) if section else []
            problems.append({
                "section": sec_num,
                "type": "root_reframing",
                "description": (
                    f"Pending scope delta {delta_id} from {source} "
                    f"requires root reframing: {title}. "
                    f"Linked sections: {source_sections}."
                ),
                "files": files,
                "delta_id": delta_id,
                "title": title,
                "source": source,
                "source_sections": linked_sections,
                "requires_root_reframing": True,
            })
    return problems


def _detect_recurrence_patterns(
    planspace: Path,
    problems: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Detect recurring problem signatures across coordination rounds."""
    paths = PathRegistry(planspace)
    signals_dir = paths.signals_dir()
    if not signals_dir.exists():
        return None

    recurring_sections: list[dict[str, Any]] = []
    for sig_path in sorted(signals_dir.glob("section-*-recurrence.json")):
        data = read_json(sig_path)
        if data is not None:
            if data.get("recurring"):
                recurring_sections.append(data)
        else:
            log(
                f"Recurrence signal malformed at {sig_path} "
                "— preserving as .malformed.json",
            )
            rename_malformed(sig_path)
            continue

    if not recurring_sections:
        return None

    recurring_section_nums = {
        str(recurring["section"]) for recurring in recurring_sections
    }
    recurring_problems = [
        problem for problem in problems
        if problem["section"] in recurring_section_nums
    ]

    if not recurring_problems:
        return None

    report = {
        "recurring_sections": [recurring["section"] for recurring in recurring_sections],
        "recurring_problem_count": len(recurring_problems),
        "max_attempt": max(recurring.get("attempt", 0) for recurring in recurring_sections),
        "problem_indices": [
            idx for idx, problem in enumerate(problems)
            if problem["section"] in recurring_section_nums
        ],
    }

    coord_dir = paths.coordination_dir()
    coord_dir.mkdir(parents=True, exist_ok=True)
    recurrence_path = coord_dir / "recurrence.json"
    write_json(recurrence_path, report)
    _log_artifact(planspace, "coordination:recurrence")

    log(
        f"  coordinator: recurrence detected — "
        f"{len(recurring_sections)} sections with "
        f"{len(recurring_problems)} recurring problems",
    )

    return report


__all__ = [
    "_collect_outstanding_problems",
    "_detect_recurrence_patterns",
    "build_file_to_sections",
]
