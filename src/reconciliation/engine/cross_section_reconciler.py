"""Universal cross-section reconciliation after the initial proposal pass.

Runs once between Phase 1a (proposal) and Phase 1c (implementation).
Loads all proposal-state artifacts and reconciliation requests, detects
overlapping anchors, conflicting contracts, redundant new-section
candidates, and shared seam candidates.  Writes per-section
reconciliation-result artifacts and, when needed, consolidated
scope-delta and substrate-trigger artifacts.

Entry point: ``run_reconciliation_loop(run_dir, proposal_results)``.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.path_registry import PathRegistry

from containers import Services
from proposal.repository.state import ProposalState, load_proposal_state
from reconciliation.service.adjudicator import adjudicate_ungrouped_candidates
from reconciliation.service.detectors import (
    aggregate_shared_seams,
    consolidate_new_section_candidates,
    detect_anchor_overlaps,
    detect_contract_conflicts,
)
from reconciliation.repository.results import (
    load_result,
    write_result,
    write_scope_delta,
    write_substrate_trigger,
)
from reconciliation.repository.queue import load_reconciliation_requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adjudication helpers
# ---------------------------------------------------------------------------

def _adjudicate_ungrouped(ungrouped, run_dir, kind):
    """Run adjudicator over ungrouped candidates and return merged groups.

    Each merged group is a dict with ``canonical_title``, ``members``,
    ``source_sections``, and ``rationale``.
    """
    if len(ungrouped) < 2:
        return []
    merged_groups = adjudicate_ungrouped_candidates(ungrouped, run_dir, kind)
    if not merged_groups:
        return []

    results = []
    for merged_group in merged_groups:
        members = merged_group.get("members", [])
        canonical = merged_group.get("canonical_title", "")
        if not members or not canonical:
            continue
        member_set = {m.strip().lower() for m in members}
        merged_sections: set[str] = set()
        matched_ungrouped: list[dict] = []
        for item in ungrouped:
            if item["title"] in member_set:
                merged_sections.add(item["source_section"])
                matched_ungrouped.append(item)
        if merged_sections:
            results.append({
                "canonical": canonical.strip().lower(),
                "sections": sorted(merged_sections),
                "matched": matched_ungrouped,
                "rationale": merged_group.get("rationale", ""),
            })
    return results


def _merge_new_section_adjudications(adjudicated, consolidated_sections):
    """Convert adjudicated groups into consolidated new-section entries."""
    for group in adjudicated:
        consolidated_sections.append({
            "title": group["canonical"],
            "source_sections": group["sections"],
            "candidates": [
                {"section": item["source_section"], "candidate": item["title"]}
                for item in group["matched"]
            ],
            "type": "consolidated_new_section",
            "adjudicated": True,
            "rationale": group["rationale"],
        })


def _merge_seam_adjudications(adjudicated, shared_seams):
    """Convert adjudicated groups into shared-seam entries."""
    for group in adjudicated:
        if len(group["sections"]) > 1:
            shared_seams.append({
                "seam": group["canonical"],
                "sections": group["sections"],
                "needs_substrate": True,
                "type": "shared_seam",
                "adjudicated": True,
                "rationale": group["rationale"],
            })


def _collect_affected_sections(anchor_overlaps, contract_conflicts,
                                consolidated_sections, substrate_seams):
    """Gather all section numbers affected by reconciliation findings."""
    affected: set[str] = set()
    for overlap in anchor_overlaps:
        affected.update(overlap.get("sections", []))
    for conflict in contract_conflicts:
        affected.update(conflict.get("sections", []))
    for consolidated in consolidated_sections:
        affected.update(consolidated.get("source_sections", []))
    for seam in substrate_seams:
        affected.update(seam.get("sections", []))
    return affected


def _extract_section_numbers(proposal_results: list) -> list[str]:
    section_numbers: list[str] = []
    for pr in proposal_results:
        sec_num = (
            pr.section_number if hasattr(pr, "section_number")
            else pr.get("section_number", "")
        )
        if sec_num:
            section_numbers.append(sec_num)
    return section_numbers


def _load_proposal_states(
    run_dir: Path,
    section_numbers: list[str],
) -> dict[str, ProposalState]:
    states: dict[str, ProposalState] = {}
    for sec_num in section_numbers:
        state_path = PathRegistry(run_dir).proposal_state(sec_num)
        states[sec_num] = load_proposal_state(state_path)
    return states


def _merge_recon_requests_into_states(
    recon_requests: list[dict],
    states: dict[str, ProposalState],
) -> None:
    for req in recon_requests:
        sec = req.get("section", "")
        if sec and sec in states:
            state = states[sec]
            for contract in req.get("unresolved_contracts", []):
                if contract not in state.unresolved_contracts:
                    state.unresolved_contracts.append(contract)
            for anchor in req.get("unresolved_anchors", []):
                if anchor not in state.unresolved_anchors:
                    state.unresolved_anchors.append(anchor)


@dataclass
class CrossSectionIssues:
    """Detected cross-section reconciliation issues."""

    anchor_overlaps: list[dict] = field(default_factory=list)
    contract_conflicts: list[dict] = field(default_factory=list)
    consolidated_sections: list[dict] = field(default_factory=list)
    shared_seams: list[dict] = field(default_factory=list)
    substrate_seams: list[dict] = field(default_factory=list)


def _detect_cross_section_issues(
    states: dict[str, ProposalState],
    run_dir: Path,
) -> CrossSectionIssues:
    anchor_overlaps = detect_anchor_overlaps(states)
    contract_conflicts = detect_contract_conflicts(states)
    consolidated_sections, ungrouped_titles = consolidate_new_section_candidates(
        states
    )
    adjudicated = _adjudicate_ungrouped(ungrouped_titles, run_dir, "new_section")
    _merge_new_section_adjudications(adjudicated, consolidated_sections)

    shared_seams, ungrouped_seams = aggregate_shared_seams(states)
    adjudicated = _adjudicate_ungrouped(ungrouped_seams, run_dir, "shared_seam")
    _merge_seam_adjudications(adjudicated, shared_seams)

    substrate_seams = [s for s in shared_seams if s.get("needs_substrate")]

    return CrossSectionIssues(
        anchor_overlaps=anchor_overlaps,
        contract_conflicts=contract_conflicts,
        consolidated_sections=consolidated_sections,
        shared_seams=shared_seams,
        substrate_seams=substrate_seams,
    )


def _build_section_result(
    sec_num: str,
    anchor_overlaps: list[dict],
    contract_conflicts: list[dict],
    consolidated_sections: list[dict],
    substrate_seams: list[dict],
    affected_sections: set[str],
) -> dict:
    sec_overlaps = [
        o for o in anchor_overlaps if sec_num in o.get("sections", [])
    ]
    sec_conflicts = [
        c for c in contract_conflicts
        if sec_num in c.get("sections", [])
    ]
    sec_consolidations = [
        c for c in consolidated_sections
        if sec_num in c.get("source_sections", [])
    ]
    sec_seams = [
        s for s in substrate_seams if sec_num in s.get("sections", [])
    ]
    return {
        "section": sec_num,
        "anchor_overlaps": sec_overlaps,
        "contract_conflicts": sec_conflicts,
        "consolidated_new_sections": sec_consolidations,
        "substrate_seams": sec_seams,
        "affected": sec_num in affected_sections,
    }


def _build_summary(
    affected_sections: set[str],
    consolidated_sections: list[dict],
    substrate_seams: list[dict],
    anchor_overlaps: list[dict],
    contract_conflicts: list[dict],
    shared_seams: list[dict],
) -> dict:
    return {
        "sections_affected": sorted(affected_sections),
        "new_sections_proposed": len(consolidated_sections),
        "substrate_needed": len(substrate_seams) > 0,
        "conflicts_found": len(anchor_overlaps) + len(contract_conflicts),
        "anchor_overlaps": len(anchor_overlaps),
        "contract_conflicts": len(contract_conflicts),
        "shared_seams": len(shared_seams),
        "substrate_seams": len(substrate_seams),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_reconciliation_result(
    planspace: Path,
    section_number: str,
) -> dict | None:
    """Load a section's reconciliation result if it exists.

    Parameters
    ----------
    planspace:
        The planspace root directory containing ``artifacts/``.
    section_number:
        Zero-padded section number (e.g. ``"03"``).

    Returns
    -------
    dict | None
        The reconciliation result dict, or ``None`` if no result file
        exists or the file is malformed.
    """
    return load_result(planspace, section_number)



def run_reconciliation_loop(
    run_dir: Path,
    proposal_results: list,
) -> dict:
    """Run universal cross-section reconciliation.

    Called once after Phase 1a (proposal pass) completes for all
    sections and before Phase 1c (implementation pass) begins.

    Parameters
    ----------
    run_dir:
        The planspace root directory containing ``artifacts/``.
    proposal_results:
        List of ``ProposalPassResult`` instances (or dicts with at least
        a ``section_number`` key) from the proposal pass.

    Returns
    -------
    dict
        Summary with keys ``sections_affected``, ``new_sections_proposed``,
        ``substrate_needed``, ``conflicts_found``.
    """
    section_numbers = _extract_section_numbers(proposal_results)
    states = _load_proposal_states(run_dir, section_numbers)

    recon_requests = load_reconciliation_requests(run_dir)
    logger.info(
        "Reconciliation: loaded %d proposal states, %d reconciliation "
        "requests",
        len(states), len(recon_requests),
    )
    _merge_recon_requests_into_states(recon_requests, states)

    issues = _detect_cross_section_issues(states, run_dir)

    affected_sections = _collect_affected_sections(
        issues.anchor_overlaps, issues.contract_conflicts,
        issues.consolidated_sections, issues.substrate_seams,
    )

    for sec_num in section_numbers:
        result = _build_section_result(
            sec_num, issues.anchor_overlaps, issues.contract_conflicts,
            issues.consolidated_sections, issues.substrate_seams,
            affected_sections,
        )
        write_result(run_dir, sec_num, result)

    for consolidated in issues.consolidated_sections:
        write_scope_delta(run_dir, consolidated)

    for seam in issues.substrate_seams:
        write_substrate_trigger(run_dir, seam)

    summary = _build_summary(
        affected_sections, issues.consolidated_sections, issues.substrate_seams,
        issues.anchor_overlaps, issues.contract_conflicts, issues.shared_seams,
    )
    logger.info("Reconciliation summary: %s", summary)

    Services.artifact_io().write_json(
        PathRegistry(run_dir).reconciliation_summary(), summary,
    )

    return summary
