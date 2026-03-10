"""Runtime readiness resolver for section execution."""

from __future__ import annotations

import logging
from pathlib import Path

from lib.core.artifact_io import read_json, write_json
from lib.core.path_registry import PathRegistry
from lib.repositories.proposal_state_repository import (
    extract_blockers,
    has_blocking_fields,
    load_proposal_state,
)

logger = logging.getLogger(__name__)


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
    governance_blockers: list[dict] = []

    # Check for unresolved pattern deviations
    deviations = state.get("pattern_deviations", [])
    if isinstance(deviations, list) and deviations:
        governance_blockers.append({
            "state": "governance_deviation",
            "detail": (
                f"{len(deviations)} unresolved pattern deviation(s) — "
                "pattern delta must be accepted before descent"
            ),
            "needs": "pattern delta resolution",
            "why_blocked": "PAT-0013: pattern change before code change",
            "source": "governance_identity",
        })

    # Check for unresolved governance questions
    questions = state.get("governance_questions", [])
    if isinstance(questions, list) and questions:
        governance_blockers.append({
            "state": "governance_question",
            "detail": (
                f"{len(questions)} unresolved governance question(s)"
            ),
            "needs": "governance question resolution",
            "why_blocked": "PAT-0013: unresolved governance questions block descent",
            "source": "governance_identity",
        })

    # Load governance packet for validation
    paths = PathRegistry(planspace)
    packet_path = paths.governance_packet(section_number)
    packet = read_json(packet_path)

    problem_ids = state.get("problem_ids", [])
    pattern_ids = state.get("pattern_ids", [])
    profile_id = state.get("profile_id", "")
    if not isinstance(problem_ids, list):
        problem_ids = []
    if not isinstance(pattern_ids, list):
        pattern_ids = []
    if not isinstance(profile_id, str):
        profile_id = ""

    has_declared_ids = bool(problem_ids or pattern_ids or profile_id)

    if isinstance(packet, dict):
        packet_problems = packet.get("candidate_problems", [])
        packet_patterns = packet.get("candidate_patterns", [])
        governing_profile = packet.get("governing_profile", "")
        packet_applicability = packet.get("applicability_state", "")
        packet_questions = packet.get("governance_questions", [])
        if not isinstance(packet_problems, list):
            packet_problems = []
        if not isinstance(packet_patterns, list):
            packet_patterns = []
        if not isinstance(governing_profile, str):
            governing_profile = ""
        if not isinstance(packet_questions, list):
            packet_questions = []

        has_governance_candidates = bool(
            packet_problems or packet_patterns or governing_profile
        )

        # CP-3 (R107): packet ambiguity must be carried in proposal-state
        # or explicitly resolved — it cannot silently vanish before descent.
        if packet_applicability == "ambiguous_applicability" and packet_questions:
            state_questions = state.get("governance_questions", [])
            if not isinstance(state_questions, list):
                state_questions = []
            if not state_questions:
                governance_blockers.append({
                    "state": "governance_ambiguity_unresolved",
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
                })

        # PAT-0013 step 6: empty identity is illegal when packet has candidates
        if has_governance_candidates and not has_declared_ids:
            governance_blockers.append({
                "state": "governance_identity_missing",
                "detail": (
                    "governance packet provides candidates but proposal "
                    "declares no problem_ids, pattern_ids, or profile_id"
                ),
                "needs": "governance identity declaration",
                "why_blocked": "PAT-0013: non-empty identity required when governance applies",
                "source": "governance_identity",
            })

        # Validate profile_id compatibility with governing profile
        if profile_id and governing_profile and profile_id != governing_profile:
            governance_blockers.append({
                "state": "governance_profile_mismatch",
                "detail": (
                    f"profile_id '{profile_id}' does not match "
                    f"governing_profile '{governing_profile}'"
                ),
                "needs": "profile_id correction",
                "why_blocked": "PAT-0013: profile_id must match governing profile",
                "source": "governance_identity",
            })

        # Validate packet membership for declared IDs
        if problem_ids or pattern_ids:
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
                governance_blockers.append({
                    "state": "governance_membership",
                    "detail": "; ".join(details),
                    "needs": "governance ID correction",
                    "why_blocked": "PAT-0013: IDs must reference packet records",
                    "source": "governance_identity",
                })
    elif has_declared_ids:
        # PAT-0013 step 6: declared IDs with missing/malformed packet → block
        governance_blockers.append({
            "state": "governance_packet_missing",
            "detail": (
                "governance IDs declared but governance packet is "
                "missing or malformed"
            ),
            "needs": "governance packet rebuild",
            "why_blocked": "PAT-0013: packet required when IDs are declared",
            "source": "governance_identity",
        })

    return governance_blockers


def resolve_readiness(planspace: Path, section_number: str) -> dict:
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

    result: dict = {
        "ready": ready,
        "blockers": blockers,
        "rationale": rationale,
    }

    readiness_dir = paths.readiness_dir()
    readiness_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = readiness_dir / f"section-{section_number}-execution-ready.json"
    try:
        write_json(artifact_path, result)
    except OSError:
        logger.warning("Could not write readiness artifact to %s", artifact_path)

    result["artifact_path"] = artifact_path
    return result
