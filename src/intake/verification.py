"""Verification packet builder for intake sessions.

Constructs a structured verification packet from extracted claims,
hypothesis sets, tensions, and gaps. The packet groups items into
five sections for user review:

1. Promotable governance candidates (philosophy, problems, constraints, risks)
2. Alternative hypothesis sets
3. Contradictions and tensions
4. Gaps (implied problems, missing constraints, missing value decisions)
5. Proposal-only items (strategies, implementation details, stack choices)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from lib.core.artifact_io import write_json
from lib.core.path_registry import PathRegistry
from lib.intake.types import (
    GovernanceClaim,
    HypothesisSet,
    TensionRecord,
)


@dataclass
class VerificationItem:
    """A single item in the verification packet."""
    claim_id: str
    statement: str
    claim_kind: str
    rationale: str = ""
    evidence_sources: list[str] = field(default_factory=list)
    confidence: float = 0.5
    tradeoffs: list[str] = field(default_factory=list)
    verification_question: str = ""
    action: Literal[
        "confirm", "confirm_with_edit", "reject", "defer"
    ] = "confirm"


@dataclass
class GapItem:
    """An identified gap in the governance coverage."""
    gap_id: str
    gap_kind: Literal[
        "implied_problem", "missing_constraint",
        "missing_value_decision", "assumed_philosophy",
        "unacknowledged_risk",
    ]
    description: str
    evidence_sources: list[str] = field(default_factory=list)
    suggested_claim: str = ""


@dataclass
class VerificationPacket:
    """Structured verification packet for user review."""
    session_id: str
    promotable_candidates: list[VerificationItem] = field(default_factory=list)
    hypothesis_sets: list[dict] = field(default_factory=list)
    tensions: list[dict] = field(default_factory=list)
    gaps: list[GapItem] = field(default_factory=list)
    proposal_only_items: list[VerificationItem] = field(default_factory=list)


def build_verification_packet(
    session_id: str,
    claims: list[GovernanceClaim],
    hypotheses: list[HypothesisSet],
    tensions: list[TensionRecord],
    gaps: list[GapItem] | None = None,
) -> VerificationPacket:
    """Build a verification packet from intake artifacts.

    Separates claims into promotable governance candidates and
    proposal-only items based on claim kind and promotability.
    """
    promotable: list[VerificationItem] = []
    proposal_only: list[VerificationItem] = []

    for claim in claims:
        item = VerificationItem(
            claim_id=claim.claim_id,
            statement=claim.statement,
            claim_kind=claim.claim_kind,
            evidence_sources=claim.source_refs,
            confidence=claim.confidence,
            verification_question=claim.verification_question,
        )
        if claim.promotable and claim.claim_kind in (
            "philosophy", "problem", "constraint", "risk",
        ):
            promotable.append(item)
        else:
            proposal_only.append(item)

    return VerificationPacket(
        session_id=session_id,
        promotable_candidates=promotable,
        hypothesis_sets=[asdict(h) for h in hypotheses],
        tensions=[asdict(t) for t in tensions],
        gaps=gaps or [],
        proposal_only_items=proposal_only,
    )


def save_verification_packet(
    packet: VerificationPacket,
    planspace: Path,
) -> tuple[Path, Path]:
    """Save verification packet as both JSON and human-readable markdown.

    Returns (json_path, md_path).
    """
    paths = PathRegistry(planspace)
    json_path = paths.verification_packet_json(packet.session_id)
    md_path = paths.verification_packet_md(packet.session_id)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    write_json(json_path, asdict(packet))
    md_path.write_text(
        _render_markdown(packet),
        encoding="utf-8",
    )
    return json_path, md_path


def _render_markdown(packet: VerificationPacket) -> str:
    """Render a verification packet as human-readable markdown."""
    lines: list[str] = [
        f"# Verification Packet — {packet.session_id}",
        "",
    ]

    if packet.promotable_candidates:
        lines.append("## 1. Promotable Governance Candidates")
        lines.append("")
        for item in packet.promotable_candidates:
            lines.append(f"### {item.claim_id} ({item.claim_kind})")
            lines.append("")
            lines.append(f"**Statement:** {item.statement}")
            lines.append(f"**Confidence:** {item.confidence:.2f}")
            if item.verification_question:
                lines.append(f"**Verification question:** {item.verification_question}")
            if item.evidence_sources:
                lines.append(f"**Evidence:** {', '.join(item.evidence_sources)}")
            if item.tradeoffs:
                lines.append("**Tradeoffs:**")
                for t in item.tradeoffs:
                    lines.append(f"- {t}")
            lines.append("")

    if packet.hypothesis_sets:
        lines.append("## 2. Alternative Hypothesis Sets")
        lines.append("")
        for hs in packet.hypothesis_sets:
            lines.append(f"### {hs.get('set_id', 'unknown')} ({hs.get('dimension', '')})")
            lines.append("")
            lines.append(f"**Resolution mode:** {hs.get('resolution_mode', '')}")
            lines.append(f"**State:** {hs.get('state', '')}")
            if hs.get("tradeoff_summary"):
                lines.append(f"**Tradeoffs:** {hs['tradeoff_summary']}")
            lines.append(f"**Members:** {', '.join(hs.get('member_claim_ids', []))}")
            if hs.get("recommended_claim_ids"):
                lines.append(f"**Recommended:** {', '.join(hs['recommended_claim_ids'])}")
            lines.append("")

    if packet.tensions:
        lines.append("## 3. Contradictions and Tensions")
        lines.append("")
        for t in packet.tensions:
            lines.append(f"### {t.get('tension_id', 'unknown')}")
            lines.append("")
            lines.append(f"**Description:** {t.get('description', '')}")
            lines.append(f"**Claims:** {', '.join(t.get('claim_ids', []))}")
            lines.append(f"**Status:** {t.get('resolution_state', 'unresolved')}")
            lines.append("")

    if packet.gaps:
        lines.append("## 4. Governance Gaps")
        lines.append("")
        for gap in packet.gaps:
            if isinstance(gap, dict):
                lines.append(f"### {gap.get('gap_id', 'unknown')} ({gap.get('gap_kind', '')})")
                lines.append("")
                lines.append(f"**Description:** {gap.get('description', '')}")
                if gap.get("suggested_claim"):
                    lines.append(f"**Suggested claim:** {gap['suggested_claim']}")
            else:
                lines.append(f"### {gap.gap_id} ({gap.gap_kind})")
                lines.append("")
                lines.append(f"**Description:** {gap.description}")
                if gap.suggested_claim:
                    lines.append(f"**Suggested claim:** {gap.suggested_claim}")
            lines.append("")

    if packet.proposal_only_items:
        lines.append("## 5. Proposal-Only Items")
        lines.append("")
        for item in packet.proposal_only_items:
            lines.append(f"### {item.claim_id} ({item.claim_kind})")
            lines.append("")
            lines.append(f"**Statement:** {item.statement}")
            lines.append(f"**Confidence:** {item.confidence:.2f}")
            lines.append("")

    return "\n".join(lines) + "\n"
