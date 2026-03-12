"""Cross-section decision persistence with observability logging."""

from __future__ import annotations

from orchestrator.service.section_decisions import (
    persist_decision as _persist_decision,
)
from containers import Services


def persist_decision(planspace, section_number: str, payload: str) -> None:
    """Persist a decision and log the resulting artifact for observability."""
    _persist_decision(planspace, section_number, payload)
    Services.communicator().log_artifact(planspace, f"decision:section-{section_number}")
