"""Verify which files were actually changed during implementation.

Compares pre-implementation snapshots against post-implementation state
to produce a verified list of modified files, filtering out false
positives from agent-reported file lists.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.types import Section

if TYPE_CHECKING:
    from containers import (
        LogService,
        SectionAlignmentService,
        StalenessDetectionService,
    )


class ChangeVerifier:
    """Verify which files were actually changed during implementation.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        logger: LogService,
        section_alignment: SectionAlignmentService,
        staleness: StalenessDetectionService,
    ) -> None:
        self._logger = logger
        self._section_alignment = section_alignment
        self._staleness = staleness

    def verify_changed_files(
        self,
        planspace: Path,
        codespace: Path,
        section: Section,
        pre_hashes: dict[str, str],
    ) -> list[str]:
        """Return sorted list of files that actually changed during implementation.

        Compares agent-reported modified files against pre-implementation
        snapshots.  Files outside the snapshot set that exist on disk are
        trusted (they were created by the agent).
        """
        reported = self._section_alignment.collect_modified_files(planspace, section, codespace)
        snapshotted_set = set(section.related_files)
        snapshotted_candidates = sorted(
            snapshotted_set | (set(reported) & set(pre_hashes))
        )
        verified_changed = self._staleness.diff_files(codespace, pre_hashes, snapshotted_candidates)

        unsnapshotted_reported = [
            relative_path
            for relative_path in reported
            if relative_path not in pre_hashes and (codespace / relative_path).exists()
        ]
        if unsnapshotted_reported:
            self._logger.log(
                f"Section {section.number}: {len(unsnapshotted_reported)} "
                f"reported files were outside the pre-snapshot set (trusted)"
            )
        actually_changed = sorted(set(verified_changed) | set(unsnapshotted_reported))
        if len(reported) != len(actually_changed):
            self._logger.log(
                f"Section {section.number}: {len(reported)} reported, "
                f"{len(actually_changed)} actually changed (detected via diff)"
            )
        return actually_changed
