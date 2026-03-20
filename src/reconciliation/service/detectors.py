"""Pure reconciliation analysis helpers.

These helpers analyze ProposalState objects without performing
any I/O or agent dispatch.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from proposal.repository.state import ProposalState

_CONSTRAINT_TERMS = (
    "must",
    "must not",
    "cannot",
    "can't",
    "should not",
    "without",
    "preserve",
    "keep",
    "avoid",
    "backward compatible",
    "non-negotiable",
)
_CHANGE_TERMS = (
    "change",
    "modify",
    "replace",
    "remove",
    "rename",
    "migrate",
    "rewrite",
    "refactor",
    "introduce",
    "update",
)
_ORDERING_TERMS = (
    "before",
    "after",
    "depends on",
    "blocked on",
    "wait for",
    "requires",
    "until",
    "once",
    "prerequisite",
)
_PRESERVE_TERMS = (
    "preserve",
    "keep",
    "avoid",
    "must not",
    "cannot",
    "backward compatible",
)
_PATH_TOKEN_RE = re.compile(r"(?<!\w)([A-Za-z0-9_./:-]+(?:\.[A-Za-z0-9_:-]+|::[A-Za-z0-9_./:-]+))(?!\w)")
_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when",
    "where", "must", "should", "need", "needs", "have", "has", "into",
    "their", "they", "them", "then", "than", "while", "because", "which",
    "what", "will", "would", "about", "already", "still", "under", "across",
    "section", "sections", "current", "code", "problem", "frame",
}


@dataclass(frozen=True)
class _SectionContext:
    section: str
    frame_text: str
    problem_statement: str
    constraints: list[str]
    resource_hints: set[str]
    resolved_resources: set[str]
    unresolved_resources: set[str]
    resolved_contracts: set[str]
    unresolved_contracts: set[str]
    topic_tokens: set[str]


def detect_problem_interactions(
    states: dict[str, ProposalState],
    problem_frames: dict[str, Any] | None = None,
) -> list[dict]:
    """Detect cross-section problem interactions.

    Problem frames provide the primary problem/constraint context.
    Shared files and anchors remain hints only. When no problem-frame
    material is available for a pair, detection falls back to the prior
    anchor-overlap behavior and emits resource-contention interactions.
    """
    problem_frames = problem_frames or {}
    contexts = {
        sec_num: _build_section_context(sec_num, state, problem_frames.get(sec_num))
        for sec_num, state in states.items()
    }

    interactions: list[dict] = []
    for left, right in combinations(sorted(contexts), 2):
        interaction = _infer_problem_interaction(contexts[left], contexts[right])
        if interaction is not None:
            interactions.append(interaction)
    return interactions


def detect_anchor_overlaps(states: dict[str, ProposalState]) -> list[dict]:
    """Backward-compatible anchor-overlap view over problem interactions."""
    interactions = detect_problem_interactions(states)
    overlaps: list[dict] = []
    for interaction in interactions:
        hints = interaction.get("hints", [])
        if not hints:
            continue
        overlaps.append({
            "anchor": hints[0],
            "sections": interaction["sections"],
            "type": "anchor_overlap",
        })
    return overlaps


def detect_contract_conflicts(states: dict[str, ProposalState]) -> list[dict]:
    """Find contracts referenced by multiple sections with differing expectations."""
    contract_resolved: dict[str, list[str]] = defaultdict(list)
    contract_unresolved: dict[str, list[str]] = defaultdict(list)

    for sec_num, state in states.items():
        for contract in state.resolved_contracts:
            key = _contract_key(contract)
            if key:
                contract_resolved[key].append(sec_num)
        for contract in state.unresolved_contracts:
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


def consolidate_new_section_candidates(states: dict[str, ProposalState]) -> tuple[list[dict], list[dict]]:
    """Group exact-match new-section candidates across sections.

    Returns ``(consolidated, ungrouped)`` where ``consolidated`` contains
    candidates that already span multiple sections and ``ungrouped``
    contains singleton candidates for optional semantic adjudication.
    """
    all_candidates: list[tuple[str, dict | str]] = []
    for sec_num, state in states.items():
        for cand in state.new_section_candidates:
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


def aggregate_shared_seams(states: dict[str, ProposalState]) -> tuple[list[dict], list[dict]]:
    """Aggregate exact-match shared seam candidates across sections.

    Returns ``(aggregated, ungrouped)`` where ``aggregated`` contains the
    baseline seam entries and ``ungrouped`` contains singleton seams for
    optional semantic adjudication.
    """
    seam_to_sections: dict[str, list[str]] = defaultdict(list)

    for sec_num, state in states.items():
        for seam in state.shared_seam_candidates:
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


def _build_section_context(
    sec_num: str,
    state: ProposalState,
    problem_frame: Any,
) -> _SectionContext:
    frame_text = _normalize_problem_frame(problem_frame)
    resolved_resources = {
        key for anchor in state.resolved_anchors
        if (key := _anchor_key(anchor))
    }
    unresolved_resources = {
        key for anchor in state.unresolved_anchors
        if (key := _anchor_key(anchor))
    }
    frame_hints = _extract_frame_resource_hints(frame_text)
    resource_hints = resolved_resources | unresolved_resources | frame_hints
    resolved_contracts = {
        key for contract in state.resolved_contracts
        if (key := _contract_key(contract))
    }
    unresolved_contracts = {
        key for contract in state.unresolved_contracts
        if (key := _contract_key(contract))
    }
    return _SectionContext(
        section=sec_num,
        frame_text=frame_text,
        problem_statement=_extract_problem_statement(frame_text),
        constraints=_extract_constraints(frame_text),
        resource_hints=resource_hints,
        resolved_resources=resolved_resources,
        unresolved_resources=unresolved_resources,
        resolved_contracts=resolved_contracts,
        unresolved_contracts=unresolved_contracts,
        topic_tokens=_extract_topic_tokens(frame_text),
    )


def _normalize_problem_frame(problem_frame: Any) -> str:
    if problem_frame is None:
        return ""
    if isinstance(problem_frame, str):
        return problem_frame.strip()
    if isinstance(problem_frame, dict):
        parts: list[str] = []
        for key in (
            "problem",
            "problem_statement",
            "summary",
            "description",
            "constraints",
            "goals",
            "content",
        ):
            value = problem_frame.get(key)
            if isinstance(value, list):
                parts.extend(str(item).strip() for item in value if str(item).strip())
            elif value:
                parts.append(str(value).strip())
        if parts:
            return "\n".join(parts)
    return str(problem_frame).strip()


def _extract_problem_statement(frame_text: str) -> str:
    for line in frame_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("open factual questions"):
            continue
        return stripped
    return ""


def _extract_constraints(frame_text: str) -> list[str]:
    constraints: list[str] = []
    for line in frame_text.splitlines():
        stripped = line.strip(" -*\t")
        lowered = stripped.lower()
        if stripped and any(term in lowered for term in _CONSTRAINT_TERMS):
            constraints.append(stripped)
    return constraints[:3]


def _extract_frame_resource_hints(frame_text: str) -> set[str]:
    hints: set[str] = set()
    for match in _PATH_TOKEN_RE.findall(frame_text):
        token = match.strip().lower()
        if "/" in token or "::" in token or "." in token:
            hints.add(token)
    return hints


def _extract_topic_tokens(frame_text: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", frame_text.lower()):
        if token not in _STOP_WORDS:
            tokens.add(token)
    return tokens


def _infer_problem_interaction(
    left: _SectionContext,
    right: _SectionContext,
) -> dict | None:
    shared_resource_hints = left.resource_hints & right.resource_hints
    shared_contracts = (
        left.resolved_contracts | left.unresolved_contracts
    ) & (
        right.resolved_contracts | right.unresolved_contracts
    )
    shared_hints = sorted(shared_resource_hints | shared_contracts)
    frames_available = bool(left.frame_text or right.frame_text)

    dep_on_right = (
        (left.unresolved_resources & right.resolved_resources)
        | (left.unresolved_contracts & right.resolved_contracts)
    )
    dep_on_left = (
        (right.unresolved_resources & left.resolved_resources)
        | (right.unresolved_contracts & left.resolved_contracts)
    )
    if frames_available and shared_hints and _looks_like_constraint_violation(left, right):
        return _interaction(
            left.section,
            right.section,
            "constraint_violation",
            _constraint_reason(left, right, shared_hints),
            shared_hints,
        )

    if frames_available and (dep_on_right or dep_on_left):
        reason = _ordering_reason(left, right, dep_on_right, dep_on_left)
        return _interaction(
            left.section,
            right.section,
            "ordering_dependency",
            reason,
            shared_hints or sorted(dep_on_right | dep_on_left),
        )

    if shared_hints:
        return _interaction(
            left.section,
            right.section,
            "resource_contention",
            _resource_contention_reason(left, right, shared_hints, fallback=not frames_available),
            shared_hints,
        )

    if frames_available and _has_semantic_overlap(left, right) and _has_ordering_language(left, right):
        return _interaction(
            left.section,
            right.section,
            "ordering_dependency",
            (
                f"Sections {left.section} and {right.section} describe the same "
                f"problem surface with sequencing language in their problem frames, "
                "so coordination should order the work instead of treating it as "
                "parallel."
            ),
            [],
        )

    return None


def _interaction(
    left_section: str,
    right_section: str,
    interaction_type: str,
    reason: str,
    hints: list[str],
) -> dict:
    return {
        "sections": [left_section, right_section],
        "interaction_type": interaction_type,
        "reason": reason,
        "hints": hints,
        "type": "problem_interaction",
    }


def _ordering_reason(
    left: _SectionContext,
    right: _SectionContext,
    dep_on_right: set[str],
    dep_on_left: set[str],
) -> str:
    if dep_on_right and dep_on_left:
        shared = ", ".join(sorted(dep_on_right | dep_on_left))
        return (
            f"Sections {left.section} and {right.section} depend on each other's "
            f"resolved surfaces ({shared}), so the work has to be sequenced rather "
            "than planned as independent fixes."
        )
    if dep_on_right:
        shared = ", ".join(sorted(dep_on_right))
        return (
            f"Section {left.section} still depends on surfaces section "
            f"{right.section} has already resolved ({shared}), so {right.section} "
            f"must land first."
        )
    shared = ", ".join(sorted(dep_on_left))
    return (
        f"Section {right.section} still depends on surfaces section "
        f"{left.section} has already resolved ({shared}), so {left.section} "
        f"must land first."
    )


def _looks_like_constraint_violation(
    left: _SectionContext,
    right: _SectionContext,
) -> bool:
    left_text = left.frame_text.lower()
    right_text = right.frame_text.lower()
    left_preserve = any(term in left_text for term in _PRESERVE_TERMS)
    right_preserve = any(term in right_text for term in _PRESERVE_TERMS)
    left_change = any(term in left_text for term in _CHANGE_TERMS)
    right_change = any(term in right_text for term in _CHANGE_TERMS)
    return (left_preserve and right_change) or (right_preserve and left_change)


def _constraint_reason(
    left: _SectionContext,
    right: _SectionContext,
    shared_hints: list[str],
) -> str:
    shared = ", ".join(shared_hints)
    left_constraint = _preferred_constraint(left)
    right_constraint = _preferred_constraint(right)
    return (
        f"Both sections touch {shared}, but their problem frames pull in different "
        f"directions: section {left.section} says '{left_constraint}' while section "
        f"{right.section} says '{right_constraint}'. This is a constraint clash, "
        "not just a shared-file overlap."
    )


def _resource_contention_reason(
    left: _SectionContext,
    right: _SectionContext,
    shared_hints: list[str],
    *,
    fallback: bool,
) -> str:
    shared = ", ".join(shared_hints)
    if fallback:
        return (
            f"Sections {left.section} and {right.section} both reference {shared}. "
            "No problem-frame context was available, so this falls back to the "
            "legacy shared-resource overlap signal."
        )
    left_problem = left.problem_statement or "its own problem frame"
    right_problem = right.problem_statement or "its own problem frame"
    return (
        f"Sections {left.section} and {right.section} are solving adjacent problems "
        f"('{left_problem}' vs. '{right_problem}') on the same shared surface "
        f"({shared}). The file overlap is only a hint; the interaction is contention "
        "over the same resource."
    )


def _has_semantic_overlap(left: _SectionContext, right: _SectionContext) -> bool:
    shared_topics = left.topic_tokens & right.topic_tokens
    return len(shared_topics) >= 2


def _has_ordering_language(left: _SectionContext, right: _SectionContext) -> bool:
    combined = f"{left.frame_text}\n{right.frame_text}".lower()
    return any(term in combined for term in _ORDERING_TERMS)


def _preferred_constraint(context: _SectionContext) -> str:
    for constraint in context.constraints:
        lowered = constraint.lower()
        if "must" in lowered or "cannot" in lowered or "should not" in lowered:
            return constraint
    if context.constraints:
        return context.constraints[0]
    return context.problem_statement
