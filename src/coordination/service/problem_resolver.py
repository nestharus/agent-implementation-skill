"""Shared helpers for coordination problem aggregation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coordination.problem_types import (
    BlockerProblem,
    ConflictProblem,
    MisalignedProblem,
    NegotiationProblem,
    Problem,
    ScopeDeltaProblem,
    UnaddressedNoteProblem,
)
from coordination.repository.notes import read_incoming_notes as load_incoming_notes
from coordination.repository.scope_deltas import list_scope_delta_files
from orchestrator.path_registry import PathRegistry
from coordination.types import NoteAction, RecurrenceReport
from orchestrator.types import Section, SectionResult
from signals.types import SIGNAL_NEEDS_PARENT

if TYPE_CHECKING:
    from containers import ArtifactIOService, Communicator, LogService, SignalReader

_SKIP_ACCEPTED = object()
"""Sentinel: note ack was accepted, skip without appending a problem."""


def _list_recurrence_signals(signals_dir: Path) -> list[Path]:
    """Named listing helper for recurrence signal files (PAT-0003)."""
    if not signals_dir.is_dir():
        return []
    return sorted(signals_dir.glob("section-*-recurrence.json"))


class ProblemResolver:
    """Coordination problem aggregation and recurrence detection."""

    def __init__(
        self,
        *,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        logger: LogService,
        signals: SignalReader,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._logger = logger
        self._signals = signals

    def _collect_blocker_and_misalignment_problems(
        self, section_results, sections_by_num, paths,
    ) -> list[Problem]:
        problems: list[Problem] = []
        for sec_num, result in section_results.items():
            if result.aligned:
                continue
            files = _section_files(sections_by_num, sec_num)

            blocker_path = paths.blocker_signal(sec_num)
            if blocker_path.exists():
                blocker = self._artifact_io.read_json(blocker_path)
                if blocker is not None:
                    if blocker.get("state") == SIGNAL_NEEDS_PARENT:
                        problems.append(BlockerProblem(
                            section=sec_num,
                            description=blocker.get("detail", ""),
                            needs=blocker.get("needs", ""),
                            files=files,
                        ))
                        continue
                else:
                    problems.append(BlockerProblem(
                        section=sec_num,
                        description=(
                            f"Blocker signal at {blocker_path} is malformed "
                            "or unreadable; cannot determine blocker state — "
                            f"routing as needs_parent for manual repair."
                        ),
                        needs="Valid blocker signal JSON",
                        files=files,
                    ))
                    self._artifact_io.rename_malformed(blocker_path)
                    continue

            if result.problems:
                problems.append(MisalignedProblem(
                    section=sec_num,
                    description=result.problems,
                    files=files,
                ))
        return problems

    def _collect_note_problems(
        self, section_results, sections_by_num, paths,
    ) -> list[Problem]:
        problems: list[Problem] = []
        note_entries: list[dict[str, Any]] = []
        for target_num in sorted(section_results):
            note_entries.extend(load_incoming_notes(paths.planspace, target_num))
        for note in sorted(note_entries, key=lambda entry: entry["path"].name):
            note_path = note["path"]
            target_num = note["target"]
            source_label = note["source"]
            target_result = section_results.get(target_num)
            if not target_result or not target_result.aligned:
                continue

            note_id_match = re.search(
                r'\*\*Note ID\*\*:\s*`([^`]+)`', note["content"],
            )
            if not note_id_match:
                continue
            note_id = note_id_match.group(1)

            files = _section_files(sections_by_num, target_num)
            ack_signal = self._signals.read(paths.note_ack_signal(target_num))
            ack_result = _classify_note_ack(
                note_id, target_num, source_label, ack_signal, files,
            )
            if ack_result is not None:
                if ack_result is not _SKIP_ACCEPTED:
                    problems.append(ack_result)
                continue

            problems.append(UnaddressedNoteProblem(
                section=target_num,
                note_id=note_id,
                note_path=str(note_path),
                description=(
                    f"Consequence note {note_id} from section "
                    f"{source_label} has not been acknowledged by "
                    f"section {target_num}. "
                    f"See note file: `{note_path}`"
                ),
                files=files,
            ))
        return problems

    def _collect_scope_delta_problems(self, sections_by_num, paths) -> list[Problem]:
        problems: list[Problem] = []
        scope_deltas_dir = paths.scope_deltas_dir()
        if not scope_deltas_dir.exists():
            return problems

        for delta_path in list_scope_delta_files(scope_deltas_dir):
            delta = self._artifact_io.read_json(delta_path)
            if delta is None:
                self._logger.log(
                    f"  coordinator: WARNING — malformed scope-delta "
                    f"{delta_path.name}, preserving as .malformed.json",
                )
                continue
            if not isinstance(delta, dict):
                self._logger.log(
                    f"  coordinator: WARNING — invalid scope-delta "
                    f"{delta_path.name} (expected object), preserving as .malformed.json",
                )
                self._artifact_io.rename_malformed(delta_path)
                continue

            linked_sections = self._resolve_delta_linked_sections(delta, delta_path)
            if linked_sections is None:
                continue

            delta_id = str(delta.get("delta_id", delta_path.stem))
            title = str(delta.get("title") or delta.get("summary") or delta_id)
            source = str(delta.get("source") or delta.get("origin") or "unknown")
            source_sections = ", ".join(linked_sections)
            for sec_num in linked_sections:
                files = _section_files(sections_by_num, sec_num)
                problems.append(ScopeDeltaProblem(
                    section=sec_num,
                    description=(
                        f"Pending scope delta {delta_id} from {source} "
                        f"requires root reframing: {title}. "
                        f"Linked sections: {source_sections}."
                    ),
                    files=files,
                    delta_id=delta_id,
                    title=title,
                    source=source,
                    source_sections=linked_sections,
                ))
        return problems

    def _resolve_delta_linked_sections(
        self, delta: dict, delta_path: Path,
    ) -> list[str] | None:
        """Validate a scope delta and resolve its linked sections.

        Returns sorted section list, or None if the delta should be skipped.
        """
        if delta.get("adjudicated") or not delta.get("requires_root_reframing", False):
            return None

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
            self._logger.log(
                f"  coordinator: WARNING — root-reframing scope-delta "
                f"{delta_path.name} has no linked sections; leaving it pending",
            )
            return None
        return linked_sections

    def collect_outstanding_problems(
        self,
        section_results: dict[str, SectionResult],
        sections_by_num: dict[str, Section],
        planspace: Path,
    ) -> list[Problem]:
        """Collect all outstanding problems across sections."""
        paths = PathRegistry(planspace)
        problems = self._collect_blocker_and_misalignment_problems(
            section_results, sections_by_num, paths,
        )
        problems.extend(self._collect_note_problems(
            section_results, sections_by_num, paths,
        ))
        problems.extend(self._collect_scope_delta_problems(sections_by_num, paths))
        return problems

    def detect_recurrence_patterns(
        self,
        planspace: Path,
        problems: list[Problem],
    ) -> RecurrenceReport | None:
        """Detect recurring problem signatures across coordination rounds."""
        paths = PathRegistry(planspace)
        signals_dir = paths.signals_dir()
        if not signals_dir.exists():
            return None

        recurring_sections_data: list[dict[str, Any]] = []
        for sig_path in _list_recurrence_signals(signals_dir):
            data = self._artifact_io.read_json(sig_path)
            if data is not None:
                if data.get("recurring"):
                    recurring_sections_data.append(data)
            else:
                self._logger.log(
                    f"Recurrence signal malformed at {sig_path} "
                    "— preserving as .malformed.json",
                )
                self._artifact_io.rename_malformed(sig_path)
                continue

        if not recurring_sections_data:
            return None

        recurring_section_nums = {
            str(entry["section"]) for entry in recurring_sections_data
        }
        recurring_problems = [
            problem for problem in problems
            if problem.section in recurring_section_nums
        ]

        if not recurring_problems:
            return None

        report = RecurrenceReport(
            recurring_sections=[str(entry["section"]) for entry in recurring_sections_data],
            recurring_problem_count=len(recurring_problems),
            max_attempt=max(entry.get("attempt", 0) for entry in recurring_sections_data),
            problem_indices=[
                idx for idx, problem in enumerate(problems)
                if problem.section in recurring_section_nums
            ],
        )

        recurrence_path = paths.coordination_recurrence()
        recurrence_path.parent.mkdir(parents=True, exist_ok=True)
        self._artifact_io.write_json(recurrence_path, report.to_dict())
        self._communicator.log_artifact(planspace, "coordination:recurrence")

        self._logger.log(
            f"  coordinator: recurrence detected — "
            f"{len(recurring_sections_data)} sections with "
            f"{len(recurring_problems)} recurring problems",
        )

        return report


# ---------------------------------------------------------------------------
# Pure helpers (no Services usage)
# ---------------------------------------------------------------------------

def _section_files(sections_by_num, sec_num):
    section = sections_by_num.get(sec_num)
    return list(section.related_files) if section else []


def _classify_note_ack(
    note_id: str, target_num: str, source_label: str,
    ack_signal: dict | None, files: list[str],
) -> Problem | object | None:
    """Classify a note's ack status.

    Returns a ``Problem``, ``_SKIP_ACCEPTED`` sentinel, or ``None``.
    """
    if not ack_signal:
        return None
    acks = ack_signal.get("acknowledged", [])
    matching_ack = next(
        (ack for ack in acks if ack.get("note_id") == note_id),
        None,
    )
    if not matching_ack:
        return None

    action = matching_ack.get("action", NoteAction.ACCEPTED)
    reason = matching_ack.get("reason", "(none)")
    if action == NoteAction.ACCEPTED:
        return _SKIP_ACCEPTED
    if action == NoteAction.REJECTED:
        return ConflictProblem(
            section=target_num,
            note_id=note_id,
            description=(
                f"Section {target_num} REJECTED note "
                f"{note_id} from {source_label}. "
                f"Reason: {reason}. "
                f"This conflict needs coordinator resolution."
            ),
            files=files,
        )
    if action == NoteAction.DEFERRED:
        return NegotiationProblem(
            section=target_num,
            note_id=note_id,
            description=(
                f"Section {target_num} deferred note "
                f"{note_id} from section {source_label}. "
                f"Reason: {reason}. "
                f"Will re-evaluate when blocking conditions resolve."
            ),
            files=files,
        )
    return None
