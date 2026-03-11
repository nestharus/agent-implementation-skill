"""Compatibility re-export for proposal-state helpers."""

from proposal.proposal_state_repository import (
    PROPOSAL_STATE_SCHEMA,
    _BLOCKING_FIELDS,
    _fail_closed_default,
    extract_blockers,
    has_blocking_fields,
    load_proposal_state,
    save_proposal_state,
    validate_proposal_state,
)

__all__ = [
    "PROPOSAL_STATE_SCHEMA",
    "_BLOCKING_FIELDS",
    "_fail_closed_default",
    "extract_blockers",
    "has_blocking_fields",
    "load_proposal_state",
    "save_proposal_state",
    "validate_proposal_state",
]
