"""Intake trust boundary and governance candidate data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SourceRecord:
    """Tracks the provenance and trust level of an information source."""
    source_id: str
    locator: str
    provenance: Literal[
        "governance_declared",
        "user_asserted",
        "repo_document",
        "code_observation",
        "external_research",
        "system_inference",
    ]
    authority_domain: Literal[
        "project_governance",
        "current_behavior",
        "conversation_intent",
        "external_fact",
    ]
    declaration_intent: Literal[
        "explicit_governance",
        "mixed_design",
        "descriptive_observation",
        "implementation_detail",
        "unknown",
    ]
    project_authority: Literal[
        "authoritative",
        "candidate_only",
        "evidence_only",
    ]
    confidence: float = 0.5
    trust_rationale: list[str] = field(default_factory=list)


@dataclass
class GovernanceClaim:
    """A single governance-relevant claim extracted from any source."""
    claim_id: str
    claim_kind: Literal[
        "philosophy", "problem", "constraint",
        "proposal", "implementation_detail",
        "risk", "ambiguous",
    ]
    statement: str
    scope: Literal["global", "region", "section", "decision"]
    source_refs: list[str] = field(default_factory=list)
    confidence: float = 0.5
    claim_state: Literal[
        "observed", "candidate", "verified",
        "rejected", "superseded",
    ] = "observed"
    promotable: bool = False
    promotion_target: str | None = None
    verification_question: str = ""
    contradiction_ids: list[str] = field(default_factory=list)
    hypothesis_set_id: str | None = None


@dataclass
class HypothesisSet:
    """A group of competing alternative claims for the same dimension."""
    set_id: str
    dimension: Literal[
        "problem_frame", "philosophy_posture",
        "constraint_interpretation", "value_scale",
        "codebase_value_inference",
    ]
    resolution_mode: Literal["select_one", "select_many", "merge", "rewrite"]
    member_claim_ids: list[str] = field(default_factory=list)
    state: Literal["open", "awaiting_user", "resolved", "reopened"] = "open"
    recommended_claim_ids: list[str] = field(default_factory=list)
    tradeoff_summary: str = ""


@dataclass
class VerificationReceipt:
    """Records a user's verification decision on governance candidates."""
    receipt_id: str
    actor: Literal["user"] = "user"
    claim_ids: list[str] = field(default_factory=list)
    decision: Literal["confirm", "confirm_with_edit", "reject", "defer"] = "confirm"
    notes: str = ""
    promoted_record_ids: list[str] = field(default_factory=list)


@dataclass
class TensionRecord:
    """Records a contradiction or tension between claims."""
    tension_id: str
    claim_ids: list[str] = field(default_factory=list)
    description: str = ""
    resolution_state: Literal["unresolved", "resolved", "deferred"] = "unresolved"
    resolution_notes: str = ""


@dataclass
class MinimumGovernanceContract:
    """Agent-produced floor for governance readiness before work begins."""
    scope: Literal["local", "regional", "global"] = "global"
    required_verified_elements: dict = field(default_factory=lambda: {
        "philosophy": 1,
        "problems": 1,
        "constraints": 0,
        "value_scales": [],
        "risk_acknowledgements": 0,
    })
    status: Literal["ready", "blocked", "advisory"] = "blocked"
    blockers: list[str] = field(default_factory=list)


@dataclass
class IntakeSession:
    """Tracks an intake session through the convergence state machine."""
    session_id: str
    entry_point: Literal["vague_idea", "spec_document", "existing_codebase"]
    state: Literal[
        "ingest",
        "source_inventory_ready",
        "claims_extracted",
        "hypotheses_grouped",
        "bootstrap_research",
        "verification_packet_ready",
        "user_verification_pending",
        "minimum_governance_ready",
        "promotion",
        "governance_index_build",
        "complete",
    ] = "ingest"
    source_records: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    hypothesis_set_ids: list[str] = field(default_factory=list)
    tension_ids: list[str] = field(default_factory=list)
    receipt_ids: list[str] = field(default_factory=list)
    governance_contract: MinimumGovernanceContract | None = None
