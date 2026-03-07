"""Universal cross-section reconciliation after the initial proposal pass.

Runs once between Phase 1a (proposal) and Phase 1c (implementation).
Loads all proposal-state artifacts and reconciliation requests, detects
overlapping anchors, conflicting contracts, redundant new-section
candidates, and shared seam candidates.  Writes per-section
reconciliation-result artifacts and, when needed, consolidated
scope-delta and substrate-trigger artifacts.

Entry point: ``run_reconciliation(run_dir, proposal_results)``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from lib.artifact_io import read_json, rename_malformed, write_json
from lib.path_registry import PathRegistry
from lib.proposal_state_repository import load_proposal_state
from lib.reconciliation_detectors import (
    aggregate_shared_seams,
    consolidate_new_section_candidates,
    detect_anchor_overlaps,
    detect_contract_conflicts,
)
from lib.reconciliation_result_repository import (
    load_result,
    was_section_affected as repository_was_section_affected,
    write_result,
    write_scope_delta,
    write_substrate_trigger,
)
from .agent_templates import render_template
from .dispatch import dispatch_agent, read_model_policy
from prompt_safety import validate_dynamic_content
from lib.reconciliation_queue import load_reconciliation_requests

logger = logging.getLogger(__name__)


def _adjudicate_ungrouped_candidates(
    ungrouped: list[dict],
    planspace: Path,
    candidate_type: str,
) -> list[dict]:
    """Dispatch an adjudicator agent to merge semantically similar candidates.

    Parameters
    ----------
    ungrouped:
        List of dicts, each with ``title``, ``source_section``, and
        optionally ``description``.
    planspace:
        The planspace root directory (for artifact writes and dispatch).
    candidate_type:
        Either ``"new_section"`` or ``"shared_seam"`` — used for
        artifact naming and prompt context.

    Returns
    -------
    list[dict]
        Merged groups from the agent verdict. Each dict has
        ``canonical_title``, ``members`` (list of original titles),
        and ``rationale``. Returns empty list on failure (fail-open).
    """
    if len(ungrouped) < 2:
        return []

    recon_dir = PathRegistry(planspace).reconciliation_dir()

    # Write ungrouped candidates to a JSON artifact so that raw candidate
    # text never appears inline in the dynamic prompt body.  This prevents
    # candidate titles/descriptions from tripping prompt-safety and aligns
    # with the filepath-over-inline prompt pattern.
    candidates_path = recon_dir / f"ungrouped-{candidate_type}.json"
    write_json(candidates_path, ungrouped)

    dynamic_body = f"""# Reconciliation Adjudication: {candidate_type}

## Candidate Type
{candidate_type.replace("_", " ").title()} candidates

## Ungrouped Candidates

Read the ungrouped candidates from: `{candidates_path}`

The candidates were NOT matched by exact title comparison.
Decide which ones describe the same underlying concern and should be
merged, and which should remain separate.

## Instructions

Return a JSON verdict with merged groups and separate candidates.
Every candidate title must appear exactly once — either in a merged
group's `members` array or in the `separate` array.

```json
{{
  "merged_groups": [
    {{"canonical_title": "...", "members": ["title-a", "title-b"], "rationale": "..."}}
  ],
  "separate": ["title-c"]
}}
```
"""

    prompt_path = recon_dir / f"adjudicate-{candidate_type}-prompt.md"
    output_path = recon_dir / f"adjudicate-{candidate_type}-output.md"

    # Validate dynamic body before wrapping in template
    violations = validate_dynamic_content(dynamic_body)
    if violations:
        logger.warning(
            "Reconciliation adjudicate prompt safety violation: %s "
            "— skipping dispatch (fail-open: returning empty list)",
            violations,
        )
        return []

    prompt_path.write_text(
        render_template("reconciliation-adjudicate", dynamic_body),
        encoding="utf-8",
    )

    policy = read_model_policy(planspace)
    model = policy.get("reconciliation_adjudicate", "claude-opus")

    try:
        result = dispatch_agent(
            model, prompt_path, output_path,
            planspace=planspace,
            agent_file="reconciliation-adjudicator.md",
        )
    except Exception:
        logger.warning(
            "Reconciliation adjudication dispatch failed for %s "
            "— falling back to exact-match only",
            candidate_type,
            exc_info=True,
        )
        return []

    # Parse JSON verdict from agent output (fail-open on parse errors)
    try:
        json_start = result.find("{")
        json_end = result.rfind("}")
        if json_start >= 0 and json_end > json_start:
            data = json.loads(result[json_start:json_end + 1])
            merged = data.get("merged_groups", [])
            if isinstance(merged, list):
                return merged
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning(
            "Reconciliation adjudication returned malformed JSON for "
            "%s — falling back to exact-match only",
            candidate_type,
        )
    return []

def _detect_anchor_overlaps(states: dict[str, dict]) -> list[dict]:
    return detect_anchor_overlaps(states)


def _detect_contract_conflicts(states: dict[str, dict]) -> list[dict]:
    return detect_contract_conflicts(states)


def _consolidate_new_section_candidates(
    states: dict[str, dict],
    planspace: Path | None = None,
) -> list[dict]:
    consolidated, ungrouped_titles = consolidate_new_section_candidates(states)

    if planspace and len(ungrouped_titles) >= 2:
        merged_groups = _adjudicate_ungrouped_candidates(
            ungrouped_titles, planspace, "new_section",
        )
        for mg in (merged_groups or []):
            members = mg.get("members", [])
            canonical = mg.get("canonical_title", "")
            if not members or not canonical:
                continue
            member_set = {m.strip().lower() for m in members}
            merged_candidates: list[dict] = []
            merged_sections: set[str] = set()
            for ug in ungrouped_titles:
                if ug["title"] in member_set:
                    merged_sections.add(ug["source_section"])
                    merged_candidates.append({
                        "section": ug["source_section"],
                        "candidate": ug["title"],
                    })
            if merged_sections:
                consolidated.append({
                    "title": canonical.strip().lower(),
                    "source_sections": sorted(merged_sections),
                    "candidates": merged_candidates,
                    "type": "consolidated_new_section",
                    "adjudicated": True,
                    "rationale": mg.get("rationale", ""),
                })

    return consolidated


def _aggregate_shared_seams(
    states: dict[str, dict],
    planspace: Path | None = None,
) -> list[dict]:
    aggregated, ungrouped_seams = aggregate_shared_seams(states)

    if planspace and len(ungrouped_seams) >= 2:
        merged_groups = _adjudicate_ungrouped_candidates(
            ungrouped_seams, planspace, "shared_seam",
        )
        for mg in (merged_groups or []):
            members = mg.get("members", [])
            canonical = mg.get("canonical_title", "")
            if not members or not canonical:
                continue
            member_set = {m.strip().lower() for m in members}
            merged_sections: set[str] = set()
            for ug in ungrouped_seams:
                if ug["title"] in member_set:
                    merged_sections.add(ug["source_section"])
            if len(merged_sections) > 1:
                aggregated.append({
                    "seam": canonical.strip().lower(),
                    "sections": sorted(merged_sections),
                    "needs_substrate": True,
                    "type": "shared_seam",
                    "adjudicated": True,
                    "rationale": mg.get("rationale", ""),
                })

    return aggregated


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
    anchor_overlaps = _detect_anchor_overlaps(states)
    contract_conflicts = _detect_contract_conflicts(states)
    consolidated_sections = _consolidate_new_section_candidates(
        states, planspace=run_dir,
    )
    shared_seams = _aggregate_shared_seams(states, planspace=run_dir)

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
