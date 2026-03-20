"""Component tests for pure reconciliation detector helpers."""

from __future__ import annotations

from src.proposal.repository.state import ProposalState
from src.reconciliation.service.detectors import (
    aggregate_shared_seams,
    consolidate_new_section_candidates,
    detect_contract_conflicts,
    detect_problem_interactions,
)


def test_detect_problem_interactions_falls_back_to_shared_resource_hints() -> None:
    interactions = detect_problem_interactions({
        "01": ProposalState(
            resolved_anchors=[{"path": " src/api.py "}],
            unresolved_anchors=[],
        ),
        "02": ProposalState(
            resolved_anchors=[],
            unresolved_anchors=["SRC/API.PY"],
        ),
    })

    assert interactions == [{
        "hints": ["src/api.py"],
        "interaction_type": "resource_contention",
        "reason": (
            "Sections 01 and 02 both reference src/api.py. No problem-frame "
            "context was available, so this falls back to the legacy "
            "shared-resource overlap signal."
        ),
        "sections": ["01", "02"],
        "type": "problem_interaction",
    }]


def test_detect_problem_interactions_identifies_ordering_dependency() -> None:
    interactions = detect_problem_interactions(
        {
            "01": ProposalState(unresolved_contracts=["auth-api"]),
            "02": ProposalState(resolved_contracts=[{"name": " auth-api "}]),
        },
        problem_frames={
            "01": "Section 01 depends on the auth contract before it can proceed.",
            "02": "Section 02 defines the auth contract and migrates callers.",
        },
    )

    assert interactions == [{
        "hints": ["auth-api"],
        "interaction_type": "ordering_dependency",
        "reason": (
            "Section 01 still depends on surfaces section 02 has already "
            "resolved (auth-api), so 02 must land first."
        ),
        "sections": ["01", "02"],
        "type": "problem_interaction",
    }]


def test_detect_problem_interactions_identifies_constraint_violation() -> None:
    interactions = detect_problem_interactions(
        {
            "01": ProposalState(resolved_anchors=["src/api.py"]),
            "02": ProposalState(unresolved_anchors=["src/api.py"]),
        },
        problem_frames={
            "01": (
                "Keep src/api.py backward compatible.\n"
                "- Must not break external callers."
            ),
            "02": (
                "Replace src/api.py response shape.\n"
                "- Update every caller to the new schema."
            ),
        },
    )

    assert interactions == [{
        "hints": ["src/api.py"],
        "interaction_type": "constraint_violation",
        "reason": (
            "Both sections touch src/api.py, but their problem frames pull in "
            "different directions: section 01 says 'Must not break external "
            "callers.' while section 02 says 'Replace src/api.py response "
            "shape.'. This is a constraint clash, not just a shared-file "
            "overlap."
        ),
        "sections": ["01", "02"],
        "type": "problem_interaction",
    }]


def test_detect_contract_conflicts_reports_resolved_vs_unresolved() -> None:
    conflicts = detect_contract_conflicts({
        "01": ProposalState(resolved_contracts=[{"name": "Auth"}]),
        "02": ProposalState(unresolved_contracts=[{"interface": " auth "}]),
        "03": ProposalState(unresolved_contracts=["billing"]),
        "04": ProposalState(unresolved_contracts=["Billing"]),
    })

    assert conflicts == [
        {
            "contract": "auth",
            "sections": ["01", "02"],
            "resolved_in": ["01"],
            "unresolved_in": ["02"],
            "type": "contract_conflict",
        },
        {
            "contract": "billing",
            "sections": ["03", "04"],
            "resolved_in": [],
            "unresolved_in": ["03", "04"],
            "type": "contract_conflict",
        },
    ]


def test_consolidate_new_section_candidates_returns_exact_matches_and_singletons() -> None:
    consolidated, ungrouped = consolidate_new_section_candidates({
        "01": ProposalState(
            new_section_candidates=[
                {"title": "Shared Cache", "description": "cache seams"},
                {"title": "Metrics"},
            ],
        ),
        "02": ProposalState(
            new_section_candidates=[
                {"scope": " shared cache ", "description": "same idea"},
            ],
        ),
    })

    assert consolidated == [{
        "title": "shared cache",
        "source_sections": ["01", "02"],
        "candidates": [
            {
                "section": "01",
                "candidate": {"title": "Shared Cache", "description": "cache seams"},
            },
            {
                "section": "02",
                "candidate": {"scope": " shared cache ", "description": "same idea"},
            },
        ],
        "type": "consolidated_new_section",
    }]
    assert ungrouped == [{
        "title": "metrics",
        "source_section": "01",
        "description": "",
    }]


def test_aggregate_shared_seams_marks_multi_section_entries_for_substrate() -> None:
    aggregated, ungrouped = aggregate_shared_seams({
        "01": ProposalState(shared_seam_candidates=["Shared Auth", "Solo Concern"]),
        "02": ProposalState(shared_seam_candidates=[" shared auth "]),
    })

    assert aggregated == [
        {
            "seam": "shared auth",
            "sections": ["01", "02"],
            "needs_substrate": True,
            "type": "shared_seam",
        },
        {
            "seam": "solo concern",
            "sections": ["01"],
            "needs_substrate": False,
            "type": "shared_seam",
        },
    ]
    assert ungrouped == [{
        "title": "solo concern",
        "source_section": "01",
        "description": "",
    }]
