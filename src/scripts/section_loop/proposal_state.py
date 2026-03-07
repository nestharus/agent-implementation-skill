"""Canonical proposal-state schema and helpers.

Provides a machine-readable schema for proposal state artifacts.  These
artifacts record the current problem-state of a section's proposal — what's
resolved, what's unresolved, and whether the section is ready for
implementation.

State files are JSON dicts conforming to ``PROPOSAL_STATE_SCHEMA``.  Every
load path is fail-closed: missing files, malformed JSON, or absent required
keys all yield ``execution_ready = False``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.artifact_io import read_json, rename_malformed, write_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema constant — documents the expected shape of a proposal state dict.
# Keys whose values are lists default to ``[]``; ``execution_ready`` defaults
# to ``False``; ``readiness_rationale`` defaults to ``""``.
# ---------------------------------------------------------------------------
PROPOSAL_STATE_SCHEMA: dict[str, type] = {
    "resolved_anchors": list,
    "unresolved_anchors": list,
    "resolved_contracts": list,
    "unresolved_contracts": list,
    "research_questions": list,
    "blocking_research_questions": list,
    "user_root_questions": list,
    "new_section_candidates": list,
    "shared_seam_candidates": list,
    "execution_ready": bool,
    "readiness_rationale": str,
}

# Fields whose presence blocks execution_ready
_BLOCKING_FIELDS: tuple[str, ...] = (
    "unresolved_anchors",
    "unresolved_contracts",
    "blocking_research_questions",
    "user_root_questions",
    "shared_seam_candidates",
)


def _fail_closed_default() -> dict:
    """Return a default proposal state with execution_ready = False."""
    return {
        "resolved_anchors": [],
        "unresolved_anchors": [],
        "resolved_contracts": [],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": False,
        "readiness_rationale": "",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_proposal_state(state: dict) -> dict:
    """Validate and normalize a proposal state dict.

    - Missing list fields are filled with empty lists.
    - If ``execution_ready`` is missing or not a bool, it is set to ``False``.
    - If ``readiness_rationale`` is missing or not a string, it is set to ``""``.

    Returns the (possibly mutated) state dict.
    """
    for key, expected_type in PROPOSAL_STATE_SCHEMA.items():
        if expected_type is list:
            if key not in state or not isinstance(state[key], list):
                state[key] = []
        elif expected_type is bool:
            if key not in state or not isinstance(state[key], bool):
                state[key] = False
        elif expected_type is str:
            if key not in state or not isinstance(state[key], str):
                state[key] = ""
    return state


def load_proposal_state(path: Path) -> dict:
    """Load and validate a proposal state JSON file.

    Fail-closed: returns a default with ``execution_ready = False`` when
    the file is missing, contains malformed JSON, or lacks required keys.

    On malformed JSON the file is renamed to ``.malformed.json``
    (best-effort) for forensic preservation.
    """
    if not path.exists():
        return _fail_closed_default()

    raw = read_json(path)
    if raw is None:
        logger.warning(
            "Malformed proposal state at %s — returning fail-closed default",
            path,
        )
        return _fail_closed_default()

    if not isinstance(raw, dict):
        logger.warning(
            "Proposal state at %s is not a dict — renaming to "
            ".malformed.json",
            path,
        )
        rename_malformed(path)
        return _fail_closed_default()

    # Strict validation for loaded artifacts: missing required keys or
    # wrong types are structural corruption, not "fill in the blanks".
    for key, expected_type in PROPOSAL_STATE_SCHEMA.items():
        if key not in raw:
            logger.warning(
                "Proposal state at %s missing required key '%s' "
                "— renaming to .malformed.json",
                path, key,
            )
            rename_malformed(path)
            return _fail_closed_default()
        if not isinstance(raw[key], expected_type):
            logger.warning(
                "Proposal state at %s has wrong type for '%s' "
                "(expected %s, got %s) — renaming to .malformed.json",
                path, key, expected_type.__name__,
                type(raw[key]).__name__,
            )
            rename_malformed(path)
            return _fail_closed_default()

    return raw


def save_proposal_state(state: dict, path: Path) -> None:
    """Write a proposal state dict to JSON.

    Parent directories are created if they do not exist.
    """
    write_json(path, state)


def has_blocking_fields(state: dict) -> bool:
    """Return True if any blocking fields contain items.

    Blocking fields: ``unresolved_anchors``, ``unresolved_contracts``,
    ``blocking_research_questions``, ``user_root_questions``,
    ``shared_seam_candidates``.
    """
    for key in _BLOCKING_FIELDS:
        items = state.get(key, [])
        if isinstance(items, list) and items:
            return True
    return False


def extract_blockers(state: dict) -> list[dict]:
    """Return a list of blocker dicts with ``type`` and ``description``.

    Each item in a blocking field produces one entry.
    """
    blockers: list[dict] = []
    for key in _BLOCKING_FIELDS:
        items = state.get(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            blockers.append({
                "type": key,
                "description": str(item),
            })
    return blockers
