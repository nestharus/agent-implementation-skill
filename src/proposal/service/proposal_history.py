"""Per-thread proposal history recorder (audit pattern).

Records round-by-round summaries of proposal outcomes so the proposal
agent can read its own history and detect cycling vs. convergence.
Append-only markdown file per section, modeled after audit-history.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import ArtifactIOService

from orchestrator.path_registry import PathRegistry


class ProposalHistoryRecorder:
    """Append-only proposal history per section.

    Constructor DI (PAT-0019): receives ``artifact_io`` at construction.
    File reads use PAT-0001 (handle missing/malformed gracefully).
    """

    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def _history_path(self, planspace: Path, section_number: str) -> Path:
        return PathRegistry(planspace).proposal_history(section_number)

    def append_round(
        self,
        planspace: Path,
        section_number: str,
        round_data: dict,
    ) -> None:
        """Append a round summary to the section's proposal history file.

        *round_data* keys:
            round_number, intent_mode, execution_ready, blockers (list of
            blocker summaries), verification_findings (list), disposition
            (``"blocked"``, ``"implemented"``, ``"escalated"``).
        """
        path = self._history_path(planspace, section_number)
        path.parent.mkdir(parents=True, exist_ok=True)

        round_number = round_data.get("round_number", "?")
        intent_mode = round_data.get("intent_mode", "unknown")
        execution_ready = round_data.get("execution_ready", False)
        blockers = round_data.get("blockers", [])
        verification_findings = round_data.get("verification_findings", [])
        disposition = round_data.get("disposition", "unknown")

        lines: list[str] = [
            f"## Round {round_number}",
            f"- Mode: {intent_mode}",
            f"- Ready: {execution_ready}",
            f"- Blockers: {len(blockers)}",
        ]
        for blocker in blockers:
            lines.append(f"  - {blocker}")
        if verification_findings:
            lines.append(f"- Verification findings: {len(verification_findings)}")
            for finding in verification_findings:
                lines.append(f"  - {finding}")
        lines.append(f"- Disposition: {disposition}")
        lines.append("")  # trailing newline

        block = "\n".join(lines) + "\n"

        existing = self._artifact_io.read_if_exists(path)
        with open(path, "w", encoding="utf-8") as fh:
            if existing:
                fh.write(existing)
                if not existing.endswith("\n"):
                    fh.write("\n")
            fh.write(block)

    def read_history(self, planspace: Path, section_number: str) -> str:
        """Read the full proposal history file.

        Returns empty string if the file does not exist (PAT-0001).
        """
        path = self._history_path(planspace, section_number)
        return self._artifact_io.read_if_exists(path)
