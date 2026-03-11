"""Intake session persistence and state transitions."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from lib.core.artifact_io import read_json, write_json
from lib.core.path_registry import PathRegistry
from lib.intake.types import (
    GovernanceClaim,
    HypothesisSet,
    IntakeSession,
    MinimumGovernanceContract,
    SourceRecord,
    TensionRecord,
    VerificationReceipt,
)

logger = logging.getLogger(__name__)

# Valid state transitions for the intake convergence flow
_TRANSITIONS: dict[str, list[str]] = {
    "ingest": ["source_inventory_ready"],
    "source_inventory_ready": ["claims_extracted"],
    "claims_extracted": ["hypotheses_grouped"],
    "hypotheses_grouped": ["bootstrap_research", "verification_packet_ready"],
    "bootstrap_research": ["verification_packet_ready"],
    "verification_packet_ready": ["user_verification_pending"],
    "user_verification_pending": ["minimum_governance_ready", "hypotheses_grouped"],
    "minimum_governance_ready": ["promotion"],
    "promotion": ["governance_index_build"],
    "governance_index_build": ["complete"],
    "complete": [],
}


def save_session(session: IntakeSession, planspace: Path) -> Path:
    """Persist an intake session to its session directory."""
    paths = PathRegistry(planspace)
    session_dir = paths.intake_session_dir(session.session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / "session.json"
    write_json(session_path, asdict(session))
    return session_path


def load_session(session_id: str, planspace: Path) -> IntakeSession | None:
    """Load an intake session from disk. Returns None if not found."""
    paths = PathRegistry(planspace)
    session_path = paths.intake_session_dir(session_id) / "session.json"
    data = read_json(session_path)
    if not isinstance(data, dict):
        return None
    contract_data = data.pop("governance_contract", None)
    contract = None
    if isinstance(contract_data, dict):
        contract = MinimumGovernanceContract(**contract_data)
    return IntakeSession(**data, governance_contract=contract)


def advance_session(session: IntakeSession, target_state: str) -> bool:
    """Advance session to target state if the transition is valid.

    Returns True if the transition succeeded, False if invalid.
    """
    valid_targets = _TRANSITIONS.get(session.state, [])
    if target_state not in valid_targets:
        logger.warning(
            "Invalid intake transition: %s -> %s (valid: %s)",
            session.state,
            target_state,
            valid_targets,
        )
        return False
    session.state = target_state
    return True


def save_source_inventory(
    sources: list[SourceRecord],
    session_id: str,
    planspace: Path,
) -> Path:
    """Write source inventory for a session."""
    paths = PathRegistry(planspace)
    path = paths.source_inventory(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, [asdict(s) for s in sources])
    return path


def save_candidate_claims(
    claims: list[GovernanceClaim],
    session_id: str,
    planspace: Path,
) -> Path:
    """Write candidate claims for a session."""
    paths = PathRegistry(planspace)
    path = paths.candidate_claims(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, [asdict(c) for c in claims])
    return path


def save_hypothesis_sets(
    hypotheses: list[HypothesisSet],
    session_id: str,
    planspace: Path,
) -> Path:
    """Write hypothesis sets for a session."""
    paths = PathRegistry(planspace)
    path = paths.hypothesis_sets(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, [asdict(h) for h in hypotheses])
    return path


def append_verification_receipt(
    receipt: VerificationReceipt,
    planspace: Path,
) -> Path:
    """Append a verification receipt to the receipts ledger."""
    paths = PathRegistry(planspace)
    path = paths.verification_receipts()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(receipt)) + "\n")
    return path


def save_tensions(
    tensions: list[TensionRecord],
    session_id: str,
    planspace: Path,
) -> Path:
    """Write tension records for a session."""
    paths = PathRegistry(planspace)
    session_dir = paths.intake_session_dir(session_id)
    path = session_dir / "tensions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, [asdict(t) for t in tensions])
    return path
