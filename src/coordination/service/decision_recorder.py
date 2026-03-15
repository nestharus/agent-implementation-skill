"""Cross-section decision persistence with observability logging."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.service.section_decision_store import (
    persist_decision as _persist_decision,
)

if TYPE_CHECKING:
    from containers import Communicator


class DecisionRecorder:
    """Cross-section decision persistence with observability logging."""

    def __init__(self, communicator: Communicator) -> None:
        self._communicator = communicator

    def persist_decision(self, planspace, section_number: str, payload: str) -> None:
        """Persist a decision and log the resulting artifact for observability."""
        _persist_decision(planspace, section_number, payload)
        self._communicator.log_artifact(planspace, f"decision:section-{section_number}")
