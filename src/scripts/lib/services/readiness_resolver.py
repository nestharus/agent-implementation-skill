"""Runtime readiness resolver for section execution."""

from __future__ import annotations

import logging
from pathlib import Path

from lib.core.artifact_io import write_json
from lib.repositories.proposal_state_repository import (
    extract_blockers,
    has_blocking_fields,
    load_proposal_state,
)

logger = logging.getLogger(__name__)


def resolve_readiness(section_dir: Path, section_number: str) -> dict:
    """Resolve whether *section_number* is ready for implementation."""
    proposal_state_path = (
        section_dir / "proposals" / f"section-{section_number}-proposal-state.json"
    )
    state = load_proposal_state(proposal_state_path)

    ready = state.get("execution_ready") is True and not has_blocking_fields(state)
    blockers = extract_blockers(state)
    rationale = state.get("readiness_rationale", "")

    if not ready and not blockers:
        if not proposal_state_path.exists():
            rationale = rationale or "proposal-state artifact missing"
        elif not state.get("execution_ready"):
            rationale = rationale or "execution_ready is false"

    result: dict = {
        "ready": ready,
        "blockers": blockers,
        "rationale": rationale,
    }

    readiness_dir = section_dir / "readiness"
    readiness_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = readiness_dir / f"section-{section_number}-execution-ready.json"
    try:
        write_json(artifact_path, result)
    except OSError:
        logger.warning("Could not write readiness artifact to %s", artifact_path)

    result["artifact_path"] = artifact_path
    return result
