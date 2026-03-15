"""Research orchestrator — script-owned continuation for the research flow."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService, HasherService


class ResearchState(str, Enum):
    """State of a section's research lifecycle."""

    PLANNED = "planned"
    SYNTHESIZED = "synthesized"
    VERIFIED = "verified"
    FAILED = "failed"
    TICKETS_SUBMITTED = "tickets_submitted"
    VERIFYING = "verifying"

    def __str__(self) -> str:  # noqa: D105
        return self.value


_TERMINAL_RESEARCH_STATES = frozenset({
    ResearchState.SYNTHESIZED,
    ResearchState.VERIFIED,
    ResearchState.FAILED,
})


class ResearchOrchestrator:
    """Research lifecycle management with constructor-injected services."""

    def __init__(
        self,
        hasher: HasherService,
        artifact_io: ArtifactIOService,
    ) -> None:
        self._hasher = hasher
        self._artifact_io = artifact_io

    def compute_trigger_hash(self, questions: list[str]) -> str:
        """Hash the current set of blocking research questions."""
        combined = "|".join(sorted(str(question) for question in questions))
        return self._hasher.content_hash(combined)

    def load_research_status(self, section_number: str, planspace: Path) -> dict | None:
        """Load research-status.json with corruption preservation."""
        status_path = PathRegistry(planspace).research_status(section_number)
        data = self._artifact_io.read_json(status_path)
        if data is None:
            return None
        if not isinstance(data, dict):
            self._artifact_io.rename_malformed(status_path)
            return None
        if "section" not in data or "status" not in data:
            self._artifact_io.rename_malformed(status_path)
            return None
        return data

    def validate_research_plan(self, plan_path: Path) -> dict | None:
        """Validate research-plan.json. Preserves corrupt files."""
        plan = self._artifact_io.read_json(plan_path)
        if plan is None:
            return None
        if not isinstance(plan, dict):
            self._artifact_io.rename_malformed(plan_path)
            return None
        required = ("section", "tickets", "flow")
        if not all(k in plan for k in required):
            self._artifact_io.rename_malformed(plan_path)
            return None
        if not isinstance(plan["tickets"], list):
            self._artifact_io.rename_malformed(plan_path)
            return None
        return plan

    def write_research_status(
        self,
        section_number: str,
        planspace: Path,
        status: str,
        *,
        detail: str = "",
        trigger_hash: str = "",
        cycle_id: str = "",
    ) -> Path:
        """Write a cycle-aware research status artifact."""
        paths = PathRegistry(planspace)
        status_path = paths.research_status(section_number)
        self._artifact_io.write_json(
            status_path,
            {
                "section": section_number,
                "status": status,
                "detail": detail,
                "trigger_hash": trigger_hash,
                "cycle_id": cycle_id,
            },
        )
        return status_path

    def is_research_complete_for_trigger(
        self,
        section_number: str,
        planspace: Path,
        trigger_hash: str,
    ) -> bool:
        """Check if the current trigger hash has a terminal research cycle."""
        status = self.load_research_status(section_number, planspace)
        if status is None:
            return False
        return (
            status.get("status") in _TERMINAL_RESEARCH_STATES
            and status.get("trigger_hash", "") == trigger_hash
        )

    def is_research_complete(self, section_number: str, planspace: Path) -> bool:
        """Check if research has reached a terminal state."""
        status = self.load_research_status(section_number, planspace)
        if status is None:
            return False
        return self.is_research_complete_for_trigger(
            section_number,
            planspace,
            str(status.get("trigger_hash", "")),
        )
