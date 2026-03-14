"""Research orchestrator — script-owned continuation for the research flow."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry


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


def compute_trigger_hash(questions: list[str]) -> str:
    """Hash the current set of blocking research questions."""
    combined = "|".join(sorted(str(question) for question in questions))
    return Services.hasher().content_hash(combined)


def load_research_status(section_number: str, planspace: Path) -> dict | None:
    """Load research-status.json with corruption preservation."""
    status_path = PathRegistry(planspace).research_status(section_number)
    data = Services.artifact_io().read_json(status_path)
    if data is None:
        return None
    if not isinstance(data, dict):
        Services.artifact_io().rename_malformed(status_path)
        return None
    if "section" not in data or "status" not in data:
        Services.artifact_io().rename_malformed(status_path)
        return None
    return data


def validate_research_plan(plan_path: Path) -> dict | None:
    """Validate research-plan.json. Preserves corrupt files."""
    plan = Services.artifact_io().read_json(plan_path)
    if plan is None:
        return None
    if not isinstance(plan, dict):
        Services.artifact_io().rename_malformed(plan_path)
        return None
    required = ("section", "tickets", "flow")
    if not all(k in plan for k in required):
        Services.artifact_io().rename_malformed(plan_path)
        return None
    if not isinstance(plan["tickets"], list):
        Services.artifact_io().rename_malformed(plan_path)
        return None
    return plan


def write_research_status(
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
    research_dir = paths.research_section_dir(section_number)
    research_dir.mkdir(parents=True, exist_ok=True)
    status_path = paths.research_status(section_number)
    Services.artifact_io().write_json(
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
    section_number: str,
    planspace: Path,
    trigger_hash: str,
) -> bool:
    """Check if the current trigger hash has a terminal research cycle."""
    status = load_research_status(section_number, planspace)
    if status is None:
        return False
    return (
        status.get("status") in _TERMINAL_RESEARCH_STATES
        and status.get("trigger_hash", "") == trigger_hash
    )


def is_research_complete(section_number: str, planspace: Path) -> bool:
    """Check if research has reached a terminal state."""
    status = load_research_status(section_number, planspace)
    if status is None:
        return False
    return is_research_complete_for_trigger(
        section_number,
        planspace,
        str(status.get("trigger_hash", "")),
    )
