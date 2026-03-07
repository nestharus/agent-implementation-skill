"""Thin orchestrator for cross-section note and decision helpers."""

from __future__ import annotations

from lib.section_decisions import (
    build_section_number_map,
    extract_section_summary,
    normalize_section_number,
    persist_decision as _persist_decision,
    read_decisions,
)
from lib.section_notes import post_section_completion, read_incoming_notes
from lib.snapshot_service import compute_text_diff

from .communication import _log_artifact


def persist_decision(planspace, section_number: str, payload: str) -> None:
    """Persist a decision and log the resulting artifact for observability."""
    _persist_decision(planspace, section_number, payload)
    _log_artifact(planspace, f"decision:section-{section_number}")
