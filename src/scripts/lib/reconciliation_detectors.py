"""Pure reconciliation analysis helpers.

These helpers analyze proposal-state dictionaries without performing
any I/O or agent dispatch.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def detect_anchor_overlaps(states: dict[str, dict]) -> list[dict]:
    """Find anchors claimed by multiple sections."""
    anchor_to_sections: dict[str, list[str]] = defaultdict(list)

    for sec_num, state in states.items():
        for anchor in state.get("resolved_anchors", []):
            key = _anchor_key(anchor)
            if key:
                anchor_to_sections[key].append(sec_num)
        for anchor in state.get("unresolved_anchors", []):
            key = _anchor_key(anchor)
            if key:
                anchor_to_sections[key].append(sec_num)

    overlaps: list[dict] = []
    for anchor_key, sections in anchor_to_sections.items():
        if len(sections) > 1:
            overlaps.append({
                "anchor": anchor_key,
                "sections": sorted(set(sections)),
                "type": "anchor_overlap",
            })
    return overlaps


def detect_contract_conflicts(states: dict[str, dict]) -> list[dict]:
    """Find contracts referenced by multiple sections with differing expectations."""
    contract_resolved: dict[str, list[str]] = defaultdict(list)
    contract_unresolved: dict[str, list[str]] = defaultdict(list)

    for sec_num, state in states.items():
        for contract in state.get("resolved_contracts", []):
            key = _contract_key(contract)
            if key:
                contract_resolved[key].append(sec_num)
        for contract in state.get("unresolved_contracts", []):
            key = _contract_key(contract)
            if key:
                contract_unresolved[key].append(sec_num)

    conflicts: list[dict] = []
    all_contract_keys = set(contract_resolved) | set(contract_unresolved)
    for key in sorted(all_contract_keys):
        resolved_in = contract_resolved.get(key, [])
        unresolved_in = contract_unresolved.get(key, [])
        all_sections = sorted(set(resolved_in + unresolved_in))

        if len(unresolved_in) > 1 or (resolved_in and unresolved_in):
            conflicts.append({
                "contract": key,
                "sections": all_sections,
                "resolved_in": sorted(set(resolved_in)),
                "unresolved_in": sorted(set(unresolved_in)),
                "type": "contract_conflict",
            })

    return conflicts


def consolidate_new_section_candidates(states: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Group exact-match new-section candidates across sections.

    Returns ``(consolidated, ungrouped)`` where ``consolidated`` contains
    candidates that already span multiple sections and ``ungrouped``
    contains singleton candidates for optional semantic adjudication.
    """
    all_candidates: list[tuple[str, dict | str]] = []
    for sec_num, state in states.items():
        for cand in state.get("new_section_candidates", []):
            all_candidates.append((sec_num, cand))

    if not all_candidates:
        return [], []

    title_groups: dict[str, list[tuple[str, dict | str]]] = defaultdict(list)
    for sec_num, cand in all_candidates:
        title = _candidate_title(cand)
        title_groups[title].append((sec_num, cand))

    consolidated: list[dict] = []
    ungrouped: list[dict] = []

    for title, group in title_groups.items():
        source_sections = sorted({sec_num for sec_num, _ in group})
        if len(source_sections) > 1:
            consolidated.append({
                "title": title,
                "source_sections": source_sections,
                "candidates": [
                    {"section": sec, "candidate": cand}
                    for sec, cand in group
                ],
                "type": "consolidated_new_section",
            })
            continue

        sec_num, cand = group[0]
        description = ""
        if isinstance(cand, dict):
            description = cand.get("description", "") or cand.get("scope", "")
        ungrouped.append({
            "title": title,
            "source_section": sec_num,
            "description": description,
        })

    return consolidated, ungrouped


def aggregate_shared_seams(states: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Aggregate exact-match shared seam candidates across sections.

    Returns ``(aggregated, ungrouped)`` where ``aggregated`` contains the
    baseline seam entries and ``ungrouped`` contains singleton seams for
    optional semantic adjudication.
    """
    seam_to_sections: dict[str, list[str]] = defaultdict(list)

    for sec_num, state in states.items():
        for seam in state.get("shared_seam_candidates", []):
            key = str(seam).strip().lower()
            if key:
                seam_to_sections[key].append(sec_num)

    aggregated: list[dict] = []
    ungrouped: list[dict] = []

    for seam_key, sections in seam_to_sections.items():
        unique_sections = sorted(set(sections))
        needs_substrate = len(unique_sections) > 1
        aggregated.append({
            "seam": seam_key,
            "sections": unique_sections,
            "needs_substrate": needs_substrate,
            "type": "shared_seam",
        })
        if not needs_substrate:
            ungrouped.append({
                "title": seam_key,
                "source_section": unique_sections[0],
                "description": "",
            })

    return aggregated, ungrouped


def _anchor_key(anchor: Any) -> str:
    if isinstance(anchor, dict):
        raw = anchor.get("path") or anchor.get("name") or str(anchor)
    else:
        raw = str(anchor)
    return raw.strip().lower()


def _contract_key(contract: Any) -> str:
    if isinstance(contract, dict):
        raw = contract.get("name") or contract.get("interface") or str(contract)
    else:
        raw = str(contract)
    return raw.strip().lower()


def _candidate_title(candidate: Any) -> str:
    if isinstance(candidate, dict):
        raw = candidate.get("title") or candidate.get("scope") or str(candidate)
    else:
        raw = str(candidate)
    return raw.strip().lower()
