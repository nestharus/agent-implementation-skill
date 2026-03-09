"""Research orchestrator — script-owned continuation for the research flow."""

from __future__ import annotations

from pathlib import Path

from lib.core.artifact_io import read_json, write_json
from lib.core.path_registry import PathRegistry


def validate_research_plan(plan_path: Path) -> dict | None:
    """Validate research-plan.json structure. Returns plan or None."""
    plan = read_json(plan_path)
    if not isinstance(plan, dict):
        return None
    required = ("section", "tickets", "flow")
    if not all(k in plan for k in required):
        return None
    if not isinstance(plan["tickets"], list):
        return None
    return plan


def write_research_status(
    section_number: str,
    planspace: Path,
    status: str,
    *,
    detail: str = "",
) -> Path:
    """Write a typed research status artifact."""
    paths = PathRegistry(planspace)
    research_dir = paths.research_section_dir(section_number)
    research_dir.mkdir(parents=True, exist_ok=True)
    status_path = paths.research_status(section_number)
    write_json(status_path, {
        "section": section_number,
        "status": status,
        "detail": detail,
    })
    return status_path


def is_research_complete(section_number: str, planspace: Path) -> bool:
    """Check if research has reached a terminal state."""
    paths = PathRegistry(planspace)
    status_path = paths.research_status(section_number)
    if not status_path.exists():
        return False
    status = read_json(status_path)
    if not isinstance(status, dict):
        return False
    return status.get("status") in ("synthesized", "verified", "failed")
