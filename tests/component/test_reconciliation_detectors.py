"""Component tests for pure reconciliation detector helpers."""

from __future__ import annotations

from src.scripts.lib.reconciliation_detectors import (
    aggregate_shared_seams,
    consolidate_new_section_candidates,
    detect_anchor_overlaps,
    detect_contract_conflicts,
)


def test_detect_anchor_overlaps_normalizes_strings_and_dicts() -> None:
    overlaps = detect_anchor_overlaps({
        "01": {
            "resolved_anchors": [{"path": " src/api.py "}],
            "unresolved_anchors": [],
        },
        "02": {
            "resolved_anchors": [],
            "unresolved_anchors": ["SRC/API.PY"],
        },
    })

    assert overlaps == [{
        "anchor": "src/api.py",
        "sections": ["01", "02"],
        "type": "anchor_overlap",
    }]


def test_detect_contract_conflicts_reports_resolved_vs_unresolved() -> None:
    conflicts = detect_contract_conflicts({
        "01": {"resolved_contracts": [{"name": "Auth"}]},
        "02": {"unresolved_contracts": [{"interface": " auth "}], "resolved_contracts": []},
        "03": {"unresolved_contracts": ["billing"], "resolved_contracts": []},
        "04": {"unresolved_contracts": ["Billing"], "resolved_contracts": []},
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
        "01": {
            "new_section_candidates": [
                {"title": "Shared Cache", "description": "cache seams"},
                {"title": "Metrics"},
            ],
        },
        "02": {
            "new_section_candidates": [
                {"scope": " shared cache ", "description": "same idea"},
            ],
        },
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
        "01": {"shared_seam_candidates": ["Shared Auth", "Solo Concern"]},
        "02": {"shared_seam_candidates": [" shared auth "]},
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
