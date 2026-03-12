"""Thin orchestrator for cross-section note and decision helpers."""

from __future__ import annotations

from orchestrator.service.section_decisions import (
    build_section_number_map,
    extract_section_summary,
    normalize_section_number,
    persist_decision as _persist_decision,
    read_decisions,
)
from scan.service.section_notes import post_section_completion, read_incoming_notes
from implementation.service.snapshot import compute_text_diff

from containers import Services


def persist_decision(planspace, section_number: str, payload: str) -> None:
    """Persist a decision and log the resulting artifact for observability."""
    _persist_decision(planspace, section_number, payload)
    Services.communicator().log_artifact(planspace, f"decision:section-{section_number}")
