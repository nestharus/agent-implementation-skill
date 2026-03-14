"""Helpers for section-file related-file parsing and scan summary updates."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import ArtifactIOService

SUMMARY_BEGIN = "<!-- scan-summary:begin -->"
_MAX_SUMMARY_LINES = 3
SUMMARY_END = "<!-- scan-summary:end -->"


def deep_scan_related_files(section_file: Path) -> list[str]:
    """Parse ``### <path>`` entries under ``## Related Files``."""
    from scan.related.cli_handler import extract_related_files

    return extract_related_files(section_file.read_text(encoding="utf-8"))


class MatchUpdater:
    """Annotate section files with summary lines from feedback JSON.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def update_match(
        self,
        section_file: Path,
        source_file: str,
        details_file: Path,
    ) -> bool:
        """Annotate section file with summary lines from feedback JSON."""
        feedback_name = details_file.name.replace("-response.md", "-feedback.json")
        feedback_file = details_file.parent / feedback_name

        if not feedback_file.exists():
            return True

        feedback = self._artifact_io.read_json(feedback_file)
        if feedback is None:
            print(
                f"[DEEP][WARN] Malformed feedback JSON: {feedback_file}",
                file=sys.stderr,
            )
            return True

        lines = feedback.get("summary_lines")
        if not isinstance(lines, list) or not lines:
            return True

        lines = [str(line) for line in lines if isinstance(line, str) and line.strip()][:_MAX_SUMMARY_LINES]
        if not lines:
            return True

        from scan.related.cli_handler import find_entry_span

        section = section_file.read_text(encoding="utf-8")
        span = find_entry_span(section, source_file)
        if span is None:
            return True

        idx, block_end = span
        block_text = section[idx:block_end]
        begin_pos = block_text.find(SUMMARY_BEGIN)
        if begin_pos != -1:
            end_pos = block_text.find(SUMMARY_END, begin_pos)
            if end_pos != -1:
                end_pos += len(SUMMARY_END)
                if end_pos < len(block_text) and block_text[end_pos] == "\n":
                    end_pos += 1
                block_text = block_text[:begin_pos] + block_text[end_pos:]
                section = section[:idx] + block_text + section[block_end:]
                span = find_entry_span(section, source_file)
                if span is None:
                    return True
                idx, block_end = span

        summary_lines = "\n".join(f"> {line}" for line in lines)
        summary_block = f"\n{SUMMARY_BEGIN}\n{summary_lines}\n{SUMMARY_END}"
        new_section = (
            section[:block_end].rstrip() + summary_block + "\n" + section[block_end:]
        )
        section_file.write_text(new_section, encoding="utf-8")
        return True


# ------------------------------------------------------------------
# Backward-compat free function wrappers
# ------------------------------------------------------------------


def _default_updater() -> MatchUpdater:
    from containers import Services
    return MatchUpdater(artifact_io=Services.artifact_io())


def update_match(
    section_file: Path,
    source_file: str,
    details_file: Path,
) -> bool:
    """Annotate section file with summary lines from feedback JSON."""
    return _default_updater().update_match(section_file, source_file, details_file)
