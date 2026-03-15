"""Persistence helpers for reconciliation artifacts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService, HasherService

logger = logging.getLogger(__name__)

_TITLE_SLUG_MAX_LENGTH = 40
_TITLE_HASH_LENGTH = 8


class Results:
    """Handles reading and writing reconciliation result artifacts."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        hasher: HasherService,
    ) -> None:
        self._artifact_io = artifact_io
        self._hasher = hasher

    def write_result(self, planspace: Path, section_number: str, result: dict) -> Path:
        """Write a per-section reconciliation result artifact."""
        path = PathRegistry(planspace).reconciliation_result(section_number)
        self._artifact_io.write_json(path, result)
        return path

    def write_scope_delta(self, planspace: Path, scope_delta: dict) -> Path:
        """Write a consolidated scope-delta artifact from reconciliation."""
        sources = "-".join(scope_delta.get("source_sections", ["unknown"]))
        title_slug = scope_delta.get("title", "unknown")[:_TITLE_SLUG_MAX_LENGTH].replace(" ", "_")
        path = PathRegistry(planspace).scope_delta_reconciliation(sources, title_slug)
        title_hash = self._hasher.content_hash(scope_delta.get("title", ""))[:_TITLE_HASH_LENGTH]
        delta_id = f"delta-recon-{sources}-{title_hash}"
        delta = {
            "delta_id": delta_id,
            "source": "reconciliation",
            "title": scope_delta.get("title", ""),
            "source_sections": scope_delta.get("source_sections", []),
            "candidates": scope_delta.get("candidates", []),
            "requires_root_reframing": bool(
                scope_delta.get("requires_root_reframing", False),
            ),
            "adjudicated": bool(scope_delta.get("adjudicated", False)),
        }
        self._artifact_io.write_json(path, delta)
        return path

    def write_substrate_trigger(self, planspace: Path, trigger: dict) -> Path:
        """Write a substrate-trigger artifact from reconciliation."""
        sections_tag = "-".join(trigger.get("sections", ["unknown"]))
        filename = f"substrate-trigger-reconciliation-{sections_tag}.json"
        path = PathRegistry(planspace).signals_dir() / filename
        payload = {
            "source": "reconciliation",
            "seam": trigger.get("seam", ""),
            "sections": trigger.get("sections", []),
            "trigger_type": "shared_seam_reconciliation",
        }
        self._artifact_io.write_json(path, payload)
        return path

    def load_result(self, planspace: Path, section_number: str) -> dict | None:
        """Load a section reconciliation result if present and well-formed."""
        path = PathRegistry(planspace).reconciliation_result(section_number)
        data = self._artifact_io.read_json(path)
        if data is None:
            return None
        if isinstance(data, dict):
            return data
        logger.warning(
            "Reconciliation result at %s is not a dict "
            "— renaming to .malformed.json",
            path,
        )
        self._artifact_io.rename_malformed(path)
        return None

    def was_section_affected(self, planspace: Path, section_number: str) -> bool:
        """Return whether reconciliation marked a section as affected."""
        result = self.load_result(planspace, section_number)
        if result is None:
            return False
        return bool(result.get("affected"))
