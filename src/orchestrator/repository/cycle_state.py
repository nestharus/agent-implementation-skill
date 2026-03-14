"""Persistent cycle state — crash-recoverable proposal and section results.

Replaces the in-memory dicts that ``pipeline_orchestrator`` previously
built and passed between phases.  Each mutation is flushed to a JSON
file so the pipeline can resume after a crash.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.types import ProposalPassResult, SectionResult

if TYPE_CHECKING:
    from containers import ArtifactIOService


class CycleState:
    """Filesystem-backed store for per-cycle orchestration results.

    Wraps two dicts — ``proposal_results`` and ``section_results`` —
    with transparent JSON persistence.  Every write flushes to disk;
    reads hydrate from disk on construction.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        proposal_path: Path,
        section_path: Path,
    ) -> None:
        self._artifact_io = artifact_io
        self._proposal_path = proposal_path
        self._section_path = section_path
        self._proposal_results: dict[str, ProposalPassResult] = self._load(
            proposal_path, ProposalPassResult,
        )
        self._section_results: dict[str, SectionResult] = self._load(
            section_path, SectionResult,
        )

    # -- proposal results -----------------------------------------------------

    @property
    def proposal_results(self) -> dict[str, ProposalPassResult]:
        return self._proposal_results

    def set_proposal(self, sec_num: str, result: ProposalPassResult) -> None:
        """Record a proposal result and flush to disk."""
        self._proposal_results[sec_num] = result
        self._save_proposals()

    def update_proposals(self, results: dict[str, ProposalPassResult]) -> None:
        """Merge multiple proposal results and flush once."""
        self._proposal_results.update(results)
        self._save_proposals()

    def clear_proposals(self) -> None:
        """Discard all proposal results (new cycle start)."""
        self._proposal_results.clear()
        self._save_proposals()

    # -- section results ------------------------------------------------------

    @property
    def section_results(self) -> dict[str, SectionResult]:
        return self._section_results

    def set_section(self, sec_num: str, result: SectionResult) -> None:
        """Record a section result and flush to disk."""
        self._section_results[sec_num] = result
        self._save_sections()

    def update_sections(self, results: dict[str, SectionResult]) -> None:
        """Merge multiple section results and flush once."""
        self._section_results.update(results)
        self._save_sections()

    def clear_sections(self) -> None:
        """Discard all section results (new cycle start)."""
        self._section_results.clear()
        self._save_sections()

    def clear_all(self) -> None:
        """Discard all state (new cycle start)."""
        self.clear_proposals()
        self.clear_sections()

    def flush(self) -> None:
        """Force-write both stores to disk (after external mutation)."""
        self._save_proposals()
        self._save_sections()

    # -- persistence ----------------------------------------------------------

    def _save_proposals(self) -> None:
        data = {k: asdict(v) for k, v in self._proposal_results.items()}
        self._artifact_io.write_json(self._proposal_path, data)

    def _save_sections(self) -> None:
        data = {k: asdict(v) for k, v in self._section_results.items()}
        self._artifact_io.write_json(self._section_path, data)

    def _load(self, path: Path, cls: type) -> dict:
        raw = self._artifact_io.read_json(path)
        if not isinstance(raw, dict):
            return {}
        known = {f.name for f in fields(cls)}
        result = {}
        for key, value in raw.items():
            if isinstance(value, dict):
                filtered = {k: v for k, v in value.items() if k in known}
                result[key] = cls(**filtered)
        return result
