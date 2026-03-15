"""Cross-section decision persistence with observability logging."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.repository.decisions import Decision, Decisions
from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService, Communicator


class DecisionRecorder:
    """Cross-section decision persistence with observability logging."""

    def __init__(self, *, artifact_io: ArtifactIOService, communicator: Communicator) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator

    def persist_decision(self, planspace, section_number: str, payload: str) -> None:
        """Persist a decision and log the resulting artifact for observability."""
        decisions_repo = Decisions(artifact_io=self._artifact_io)
        decisions_dir = PathRegistry(planspace).decisions_dir()
        existing = decisions_repo.load_decisions(decisions_dir, section=section_number)
        next_num = len(existing) + 1
        decision_id = f"d-{section_number}-{next_num:03d}"
        decision = Decision(
            id=decision_id,
            scope="section",
            section=section_number,
            problem_id=None,
            parent_problem_id=None,
            concern_scope="parent-resume",
            proposal_summary=payload,
            alignment_to_parent=None,
            status="decided",
        )
        decisions_repo.record_decision(decisions_dir, decision)
        self._communicator.log_artifact(planspace, f"decision:section-{section_number}")
