"""Canonical proposal-state schema and repository helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import ArtifactIOService

logger = logging.getLogger(__name__)


@dataclass
class ProposalState:
    """Typed proposal state — replaces raw dict contract."""

    resolved_anchors: list = field(default_factory=list)
    unresolved_anchors: list = field(default_factory=list)
    resolved_contracts: list = field(default_factory=list)
    unresolved_contracts: list = field(default_factory=list)
    research_questions: list = field(default_factory=list)
    blocking_research_questions: list = field(default_factory=list)
    user_root_questions: list = field(default_factory=list)
    new_section_candidates: list = field(default_factory=list)
    shared_seam_candidates: list = field(default_factory=list)
    execution_ready: bool = False
    readiness_rationale: str = ""
    problem_ids: list = field(default_factory=list)
    pattern_ids: list = field(default_factory=list)
    profile_id: str = ""
    pattern_deviations: list = field(default_factory=list)
    governance_questions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, raw: dict) -> ProposalState:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


_BLOCKING_FIELDS: tuple[str, ...] = (
    "unresolved_anchors",
    "unresolved_contracts",
    "blocking_research_questions",
    "user_root_questions",
    "shared_seam_candidates",
)


class State:
    def __init__(
        self,
        artifact_io: ArtifactIOService,
    ) -> None:
        self._artifact_io = artifact_io

    def load_proposal_state(self, path: Path) -> ProposalState:
        """Load and validate a proposal state JSON file."""
        if not path.exists():
            return ProposalState()

        raw = self._artifact_io.read_json(path)
        if raw is None:
            logger.warning(
                "Malformed proposal state at %s — returning fail-closed default",
                path,
            )
            return ProposalState()

        if not isinstance(raw, dict):
            logger.warning(
                "Proposal state at %s is not a dict — renaming to "
                ".malformed.json",
                path,
            )
            self._artifact_io.rename_malformed(path)
            return ProposalState()

        expected_fields = {f.name: f.type for f in fields(ProposalState)}
        for key in expected_fields:
            if key not in raw:
                logger.warning(
                    "Proposal state at %s missing required key '%s' "
                    "— renaming to .malformed.json",
                    path,
                    key,
                )
                self._artifact_io.rename_malformed(path)
                return ProposalState()

        return ProposalState.from_dict(raw)

    def save_proposal_state(self, state: ProposalState | dict, path: Path) -> None:
        """Write a proposal state to JSON."""
        data = state.to_dict() if isinstance(state, ProposalState) else state
        self._artifact_io.write_json(path, data)


_GREENFIELD_BLOCKING_FIELDS: tuple[str, ...] = (
    "blocking_research_questions",
    "shared_seam_candidates",
)

# Regex for user_root_questions that are repo-confusion noise in greenfield
_GREENFIELD_QUESTION_NOISE_RE = (
    r"spec.only|documentation.first|different.checkout"
    r"|another.workspace|supposed.to.be.empty"
)


def has_blocking_fields(state: ProposalState) -> bool:
    """Return True if any blocking fields contain items."""
    return bool(
        state.unresolved_anchors
        or state.unresolved_contracts
        or state.blocking_research_questions
        or state.user_root_questions
        or state.shared_seam_candidates
    )


def has_blocking_fields_for_mode(state: ProposalState, project_mode: str) -> bool:
    """Return True if any blocking fields contain items, filtered by *project_mode*.

    For greenfield projects, ``unresolved_anchors``, ``unresolved_contracts``,
    and repo-confusion ``user_root_questions`` are not blocking because the
    code does not exist yet (it will be created).
    """
    if project_mode != "greenfield":
        return has_blocking_fields(state)
    import re
    for field_name in _GREENFIELD_BLOCKING_FIELDS:
        items = getattr(state, field_name, [])
        if isinstance(items, list) and items:
            return True
    # user_root_questions: only block on genuine (non-confusion) questions
    for q in (state.user_root_questions or []):
        if not re.search(_GREENFIELD_QUESTION_NOISE_RE, str(q), re.IGNORECASE):
            return True
    return False


def extract_blockers(state: ProposalState) -> list[dict]:
    """Return a list of blocker dicts with ``type`` and ``description``."""
    blockers: list[dict] = []
    for field_name in _BLOCKING_FIELDS:
        items = getattr(state, field_name, [])
        if not isinstance(items, list):
            continue
        for item in items:
            blockers.append({
                "type": field_name,
                "description": str(item),
            })
    return blockers


def extract_blockers_for_mode(
    state: ProposalState, project_mode: str,
) -> list[dict]:
    """Like :func:`extract_blockers` but filtered by *project_mode*.

    For greenfield projects, ``unresolved_anchors`` and
    ``unresolved_contracts`` are omitted.  Repo-confusion
    ``user_root_questions`` are also omitted.
    """
    if project_mode != "greenfield":
        return extract_blockers(state)
    import re
    blockers: list[dict] = []
    for field_name in _GREENFIELD_BLOCKING_FIELDS:
        items = getattr(state, field_name, [])
        if not isinstance(items, list):
            continue
        for item in items:
            blockers.append({
                "type": field_name,
                "description": str(item),
            })
    # user_root_questions: keep only genuine (non-confusion) questions
    for q in (state.user_root_questions or []):
        if not re.search(_GREENFIELD_QUESTION_NOISE_RE, str(q), re.IGNORECASE):
            blockers.append({
                "type": "user_root_questions",
                "description": str(q),
            })
    return blockers
