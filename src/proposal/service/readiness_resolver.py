"""Runtime readiness resolver for section execution."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from containers import Services


class GovernanceBlockerState(str, Enum):
    """State code for governance readiness blockers."""

    DEVIATION = "governance_deviation"
    QUESTION = "governance_question"
    AMBIGUITY_UNRESOLVED = "governance_ambiguity_unresolved"
    IDENTITY_MISSING = "governance_identity_missing"
    PROFILE_MISMATCH = "governance_profile_mismatch"
    MEMBERSHIP = "governance_membership"
    PACKET_MISSING = "governance_packet_missing"

    def __str__(self) -> str:  # noqa: D105
        return self.value
from orchestrator.path_registry import PathRegistry
from proposal.repository.state import (
    extract_blockers,
    has_blocking_fields,
    load_proposal_state,
)


@dataclass(frozen=True)
class ReadinessResult:
    """Structured result from :func:`resolve_readiness`.

    Supports dict-style ``[]`` and ``.get()`` access for backward
    compatibility during migration.  Prefer attribute access
    (``.ready``, ``.blockers``, ``.rationale``, ``.artifact_path``).
    """

    ready: bool
    blockers: list[dict] = field(default_factory=list)
    rationale: str = ""
    artifact_path: Path | None = None

    # -- backward-compat dict-style access ---------------------------------

    _FIELDS = frozenset({"ready", "blockers", "rationale", "artifact_path"})

    def __getitem__(self, key: str) -> Any:
        if key in self._FIELDS:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._FIELDS:
            return getattr(self, key)
        return default

logger = logging.getLogger(__name__)


def _check_pattern_deviations(state: dict) -> list[dict]:
    """Return blockers for unresolved pattern deviations."""
    deviations = state.get("pattern_deviations", [])
    if isinstance(deviations, list) and deviations:
        return [{
            "state": GovernanceBlockerState.DEVIATION,
            "detail": (
                f"{len(deviations)} unresolved pattern deviation(s) — "
                "pattern delta must be accepted before descent"
            ),
            "needs": "pattern delta resolution",
            "why_blocked": "PAT-0013: pattern change before code change",
            "source": "governance_identity",
        }]
    return []


def _check_governance_questions(state: dict) -> list[dict]:
    """Return blockers for unresolved governance questions."""
    questions = state.get("governance_questions", [])
    if isinstance(questions, list) and questions:
        return [{
            "state": GovernanceBlockerState.QUESTION,
            "detail": (
                f"{len(questions)} unresolved governance question(s)"
            ),
            "needs": "governance question resolution",
            "why_blocked": "PAT-0013: unresolved governance questions block descent",
            "source": "governance_identity",
        }]
    return []


@dataclass
class GovernanceIds:
    """Validated governance IDs extracted from proposal state."""

    problem_ids: list[str] = field(default_factory=list)
    pattern_ids: list[str] = field(default_factory=list)
    profile_id: str = ""

    def has_declared_ids(self) -> bool:
        return bool(self.problem_ids or self.pattern_ids or self.profile_id)


def _validate_declared_ids_types(
    state: dict, section_number: str,
) -> GovernanceIds:
    """Extract and type-check declared governance IDs from *state*."""
    problem_ids = state.get("problem_ids", [])
    pattern_ids = state.get("pattern_ids", [])
    profile_id = state.get("profile_id", "")
    if not isinstance(problem_ids, list):
        logger.warning(
            "Section %s: problem_ids has unexpected type %s, defaulting to []",
            section_number, type(problem_ids).__name__,
        )
        problem_ids = []
    if not isinstance(pattern_ids, list):
        logger.warning(
            "Section %s: pattern_ids has unexpected type %s, defaulting to []",
            section_number, type(pattern_ids).__name__,
        )
        pattern_ids = []
    if not isinstance(profile_id, str):
        logger.warning(
            "Section %s: profile_id has unexpected type %s, defaulting to ''",
            section_number, type(profile_id).__name__,
        )
        profile_id = ""
    return GovernanceIds(problem_ids, pattern_ids, profile_id)


def _check_packet_ambiguity(packet: dict, state: dict) -> list[dict]:
    """CP-3 (R107): packet ambiguity must be carried in proposal-state."""
    packet_applicability = packet.get("applicability_state", "")
    packet_questions = packet.get("governance_questions", [])
    if not isinstance(packet_questions, list):
        packet_questions = []
    if packet_applicability == "ambiguous_applicability" and packet_questions:
        state_questions = state.get("governance_questions", [])
        if not isinstance(state_questions, list):
            state_questions = []
        if not state_questions:
            return [{
                "state": GovernanceBlockerState.AMBIGUITY_UNRESOLVED,
                "detail": (
                    f"governance packet has {len(packet_questions)} "
                    "ambiguity question(s) but proposal-state does not "
                    "carry or resolve them"
                ),
                "needs": "governance question resolution or narrowed selection",
                "why_blocked": (
                    "PAT-0011: packet ambiguity must be resolved or "
                    "carried forward before descent"
                ),
                "source": "governance_identity",
            }]
    return []


def _check_empty_identity(packet: dict, has_declared_ids: bool) -> list[dict]:
    """PAT-0013 step 6: empty identity is illegal when packet has candidates."""
    packet_problems = packet.get("candidate_problems", [])
    packet_patterns = packet.get("candidate_patterns", [])
    governing_profile = packet.get("governing_profile", "")
    if not isinstance(packet_problems, list):
        packet_problems = []
    if not isinstance(packet_patterns, list):
        packet_patterns = []
    if not isinstance(governing_profile, str):
        governing_profile = ""
    has_governance_candidates = bool(
        packet_problems or packet_patterns or governing_profile
    )
    if has_governance_candidates and not has_declared_ids:
        return [{
            "state": GovernanceBlockerState.IDENTITY_MISSING,
            "detail": (
                "governance packet provides candidates but proposal "
                "declares no problem_ids, pattern_ids, or profile_id"
            ),
            "needs": "governance identity declaration",
            "why_blocked": "PAT-0013: non-empty identity required when governance applies",
            "source": "governance_identity",
        }]
    return []


def _check_profile_mismatch(
    profile_id: str, governing_profile: str,
) -> list[dict]:
    """Return blockers when profile_id does not match the governing profile."""
    if profile_id and governing_profile and profile_id != governing_profile:
        return [{
            "state": GovernanceBlockerState.PROFILE_MISMATCH,
            "detail": (
                f"profile_id '{profile_id}' does not match "
                f"governing_profile '{governing_profile}'"
            ),
            "needs": "profile_id correction",
            "why_blocked": "PAT-0013: profile_id must match governing profile",
            "source": "governance_identity",
        }]
    return []


def _check_packet_membership(
    problem_ids: list[str],
    pattern_ids: list[str],
    packet_problems: list,
    packet_patterns: list,
) -> list[dict]:
    """Validate that declared IDs reference records present in the packet."""
    if not (problem_ids or pattern_ids):
        return []
    packet_problem_ids = {
        str(p.get("problem_id", ""))
        for p in packet_problems
        if isinstance(p, dict)
    }
    packet_pattern_ids = {
        str(p.get("pattern_id", ""))
        for p in packet_patterns
        if isinstance(p, dict)
    }
    orphan_problems = [
        pid for pid in problem_ids
        if isinstance(pid, str) and pid and pid not in packet_problem_ids
    ]
    orphan_patterns = [
        pid for pid in pattern_ids
        if isinstance(pid, str) and pid and pid not in packet_pattern_ids
    ]
    if orphan_problems or orphan_patterns:
        details = []
        if orphan_problems:
            details.append(f"problem_ids {orphan_problems} not in packet")
        if orphan_patterns:
            details.append(f"pattern_ids {orphan_patterns} not in packet")
        return [{
            "state": GovernanceBlockerState.MEMBERSHIP,
            "detail": "; ".join(details),
            "needs": "governance ID correction",
            "why_blocked": "PAT-0013: IDs must reference packet records",
            "source": "governance_identity",
        }]
    return []


def _check_missing_packet(has_declared_ids: bool, packet: Any) -> list[dict]:
    """PAT-0013 step 6: declared IDs with missing/malformed packet -> block."""
    if has_declared_ids and not isinstance(packet, dict):
        return [{
            "state": GovernanceBlockerState.PACKET_MISSING,
            "detail": (
                "governance IDs declared but governance packet is "
                "missing or malformed"
            ),
            "needs": "governance packet rebuild",
            "why_blocked": "PAT-0013: packet required when IDs are declared",
            "source": "governance_identity",
        }]
    return []


def _validate_governance_identity(
    state: dict,
    planspace: Path,
    section_number: str,
) -> list[dict]:
    """Validate governance identity fields against the governance packet.

    *planspace* is the root planspace directory.  PathRegistry is used for
    all artifact path construction (PAT-0003).

    Returns a list of governance blockers (empty if valid).
    """
    blockers: list[dict] = []

    blockers.extend(_check_pattern_deviations(state))
    blockers.extend(_check_governance_questions(state))

    # Load governance packet for validation
    paths = PathRegistry(planspace)
    packet_path = paths.governance_packet(section_number)
    packet = Services.artifact_io().read_json(packet_path)

    gov_ids = _validate_declared_ids_types(state, section_number)
    has_declared_ids = gov_ids.has_declared_ids()

    if isinstance(packet, dict):
        blockers.extend(_check_packet_ambiguity(packet, state))
        blockers.extend(_check_empty_identity(packet, has_declared_ids))

        governing_profile = packet.get("governing_profile", "")
        if not isinstance(governing_profile, str):
            governing_profile = ""
        blockers.extend(_check_profile_mismatch(gov_ids.profile_id, governing_profile))

        packet_problems = packet.get("candidate_problems", [])
        packet_patterns = packet.get("candidate_patterns", [])
        if not isinstance(packet_problems, list):
            packet_problems = []
        if not isinstance(packet_patterns, list):
            packet_patterns = []
        blockers.extend(_check_packet_membership(
            gov_ids.problem_ids, gov_ids.pattern_ids,
            packet_problems, packet_patterns,
        ))
    else:
        blockers.extend(_check_missing_packet(has_declared_ids, packet))

    return blockers


def resolve_readiness(planspace: Path, section_number: str) -> ReadinessResult:
    """Resolve whether *section_number* is ready for implementation.

    *planspace* is the root planspace directory (NOT the artifacts subdirectory).
    PathRegistry is used for all artifact path construction (PAT-0003).
    """
    paths = PathRegistry(planspace)
    proposal_state_path = paths.proposal_state(section_number)
    state = load_proposal_state(proposal_state_path)

    ready = state.get("execution_ready") is True and not has_blocking_fields(state)
    blockers = extract_blockers(state)

    # Validate governance identity (PAT-0013)
    governance_blockers = _validate_governance_identity(
        state, planspace, section_number,
    )
    if governance_blockers:
        blockers.extend(governance_blockers)
        ready = False

    rationale = state.get("readiness_rationale", "")

    if not ready and not blockers:
        if not proposal_state_path.exists():
            rationale = rationale or "proposal-state artifact missing"
        elif not state.get("execution_ready"):
            rationale = rationale or "execution_ready is false"

    serializable: dict = {
        "ready": ready,
        "blockers": blockers,
        "rationale": rationale,
    }

    readiness_dir = paths.readiness_dir()
    readiness_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = paths.execution_ready(section_number)
    try:
        Services.artifact_io().write_json(artifact_path, serializable)
    except OSError:
        logger.warning("Could not write readiness artifact to %s", artifact_path)

    return ReadinessResult(
        ready=ready,
        blockers=blockers,
        rationale=rationale,
        artifact_path=artifact_path,
    )
