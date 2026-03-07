"""Universal cross-section reconciliation after the initial proposal pass.

Runs once between Phase 1a (proposal) and Phase 1c (implementation).
Loads all proposal-state artifacts and reconciliation requests, detects
overlapping anchors, conflicting contracts, redundant new-section
candidates, and shared seam candidates.  Writes per-section
reconciliation-result artifacts and, when needed, consolidated
scope-delta and substrate-trigger artifacts.

Entry point: ``run_reconciliation(run_dir, proposal_results)``.
"""

import logging
from pathlib import Path

from lib.core.artifact_io import write_json
from lib.core.path_registry import PathRegistry
from lib.repositories.proposal_state_repository import load_proposal_state
from lib.pipelines.reconciliation_adjudicator import adjudicate_ungrouped_candidates
from lib.services.reconciliation_detectors import (
    aggregate_shared_seams,
    consolidate_new_section_candidates,
    detect_anchor_overlaps,
    detect_contract_conflicts,
)
from lib.repositories.reconciliation_result_repository import (
    load_result,
    was_section_affected as repository_was_section_affected,
    write_result,
    write_scope_delta,
    write_substrate_trigger,
)
from lib.repositories.reconciliation_queue import load_reconciliation_requests

logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_reconciliation_result(
    section_dir: Path,
    section_number: str,
) -> dict | None:
    """Load a section's reconciliation result if it exists.

    Parameters
    ----------
    section_dir:
        The ``planspace / "artifacts"`` directory (or equivalent root
        containing a ``reconciliation/`` subdirectory).
    section_number:
        Zero-padded section number (e.g. ``"03"``).

    Returns
    -------
    dict | None
        The reconciliation result dict, or ``None`` if no result file
        exists or the file is malformed.
    """
    planspace = section_dir.parent if section_dir.name == "artifacts" else section_dir
    return load_result(planspace, section_number)


def was_section_affected(run_dir: Path, section_number: str) -> bool:
    """Check whether reconciliation marked a section as affected.

    Convenience wrapper around :func:`load_reconciliation_result` that
    returns ``True`` when a reconciliation result artifact exists for
    *section_number* and its ``affected`` field is truthy.

    Parameters
    ----------
    run_dir:
        The planspace root directory containing ``artifacts/``.
    section_number:
        Zero-padded section number (e.g. ``"03"``).
    """
    return repository_was_section_affected(run_dir, section_number)


def run_reconciliation(
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
    artifacts_dir = run_dir / "artifacts"
    proposals_dir = artifacts_dir / "proposals"

    # ------------------------------------------------------------------
    # 1. Load all proposal-state artifacts
    # ------------------------------------------------------------------
    section_numbers: list[str] = []
    for pr in proposal_results:
        sec_num = (
            pr.section_number if hasattr(pr, "section_number")
            else pr.get("section_number", "")
        )
        if sec_num:
            section_numbers.append(sec_num)

    states: dict[str, dict] = {}
    for sec_num in section_numbers:
        state_path = proposals_dir / f"section-{sec_num}-proposal-state.json"
        states[sec_num] = load_proposal_state(state_path)

    # ------------------------------------------------------------------
    # 2. Load reconciliation requests (from Task 7 queue)
    # ------------------------------------------------------------------
    recon_requests = load_reconciliation_requests(run_dir)
    logger.info(
        "Reconciliation: loaded %d proposal states, %d reconciliation "
        "requests",
        len(states), len(recon_requests),
    )

    # Merge reconciliation request data into states so that the
    # detection helpers see everything.
    for req in recon_requests:
        sec = req.get("section", "")
        if sec and sec in states:
            state = states[sec]
            # Append unresolved items from requests that are not already
            # present in the proposal state.
            for contract in req.get("unresolved_contracts", []):
                if contract not in state.get("unresolved_contracts", []):
                    state.setdefault("unresolved_contracts", []).append(
                        contract)
            for anchor in req.get("unresolved_anchors", []):
                if anchor not in state.get("unresolved_anchors", []):
                    state.setdefault("unresolved_anchors", []).append(anchor)

    # ------------------------------------------------------------------
    # 3. Detect overlaps, conflicts, consolidations
    # ------------------------------------------------------------------
    anchor_overlaps = detect_anchor_overlaps(states)
    contract_conflicts = detect_contract_conflicts(states)
    consolidated_sections, ungrouped_titles = consolidate_new_section_candidates(
        states
    )
    if len(ungrouped_titles) >= 2:
        merged_groups = adjudicate_ungrouped_candidates(
            ungrouped_titles, run_dir, "new_section",
        )
        for merged_group in merged_groups or []:
            members = merged_group.get("members", [])
            canonical = merged_group.get("canonical_title", "")
            if not members or not canonical:
                continue
            member_set = {member.strip().lower() for member in members}
            merged_candidates: list[dict] = []
            merged_sections: set[str] = set()
            for ungrouped in ungrouped_titles:
                if ungrouped["title"] in member_set:
                    merged_sections.add(ungrouped["source_section"])
                    merged_candidates.append({
                        "section": ungrouped["source_section"],
                        "candidate": ungrouped["title"],
                    })
            if merged_sections:
                consolidated_sections.append({
                    "title": canonical.strip().lower(),
                    "source_sections": sorted(merged_sections),
                    "candidates": merged_candidates,
                    "type": "consolidated_new_section",
                    "adjudicated": True,
                    "rationale": merged_group.get("rationale", ""),
                })

    shared_seams, ungrouped_seams = aggregate_shared_seams(states)
    if len(ungrouped_seams) >= 2:
        merged_groups = adjudicate_ungrouped_candidates(
            ungrouped_seams, run_dir, "shared_seam",
        )
        for merged_group in merged_groups or []:
            members = merged_group.get("members", [])
            canonical = merged_group.get("canonical_title", "")
            if not members or not canonical:
                continue
            member_set = {member.strip().lower() for member in members}
            merged_sections: set[str] = set()
            for ungrouped in ungrouped_seams:
                if ungrouped["title"] in member_set:
                    merged_sections.add(ungrouped["source_section"])
            if len(merged_sections) > 1:
                shared_seams.append({
                    "seam": canonical.strip().lower(),
                    "sections": sorted(merged_sections),
                    "needs_substrate": True,
                    "type": "shared_seam",
                    "adjudicated": True,
                    "rationale": merged_group.get("rationale", ""),
                })

    # Seams that involve multiple sections need substrate work
    substrate_seams = [s for s in shared_seams if s.get("needs_substrate")]

    # ------------------------------------------------------------------
    # 4. Determine affected sections
    # ------------------------------------------------------------------
    affected_sections: set[str] = set()
    for overlap in anchor_overlaps:
        affected_sections.update(overlap.get("sections", []))
    for conflict in contract_conflicts:
        affected_sections.update(conflict.get("sections", []))
    for consolidated in consolidated_sections:
        affected_sections.update(consolidated.get("source_sections", []))
    for seam in substrate_seams:
        affected_sections.update(seam.get("sections", []))

    # ------------------------------------------------------------------
    # 5. Write per-section reconciliation result artifacts
    # ------------------------------------------------------------------
    for sec_num in section_numbers:
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

        result = {
            "section": sec_num,
            "anchor_overlaps": sec_overlaps,
            "contract_conflicts": sec_conflicts,
            "consolidated_new_sections": sec_consolidations,
            "substrate_seams": sec_seams,
            "affected": sec_num in affected_sections,
        }
        write_result(run_dir, sec_num, result)

    # ------------------------------------------------------------------
    # 6. Write consolidated scope-delta artifacts for new sections
    # ------------------------------------------------------------------
    for consolidated in consolidated_sections:
        write_scope_delta(run_dir, consolidated)

    # ------------------------------------------------------------------
    # 7. Write substrate-trigger artifacts for shared seams
    # ------------------------------------------------------------------
    for seam in substrate_seams:
        write_substrate_trigger(run_dir, seam)

    # ------------------------------------------------------------------
    # 8. Build and return summary
    # ------------------------------------------------------------------
    summary = {
        "sections_affected": sorted(affected_sections),
        "new_sections_proposed": len(consolidated_sections),
        "substrate_needed": len(substrate_seams) > 0,
        "conflicts_found": len(anchor_overlaps) + len(contract_conflicts),
        "anchor_overlaps": len(anchor_overlaps),
        "contract_conflicts": len(contract_conflicts),
        "shared_seams": len(shared_seams),
        "substrate_seams": len(substrate_seams),
    }
    logger.info("Reconciliation summary: %s", summary)

    # Write summary artifact
    summary_path = (
        run_dir / "artifacts" / "reconciliation"
        / "reconciliation-summary.json"
    )
    write_json(summary_path, summary)

    return summary
