"""Component tests for verification gate (PRB-0008 Item 15)."""

from __future__ import annotations

from pathlib import Path

import pytest

from containers import ArtifactIOService, Services
from orchestrator.path_registry import PathRegistry
from signals.repository.artifact_io import write_json
from verification.service.verification_gate import (
    VerificationGateResult,
    check_verification_gate,
)


# -- Gate behaviour with no verification_status artifact ---------------------

def test_gate_open_when_no_verification_status(tmp_path: Path) -> None:
    """When no verification_status artifact exists, the gate is open."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    result = check_verification_gate(ArtifactIOService(), planspace, "01")
    assert result.passed is True


# -- Gate passes for accept disposition --------------------------------------

def test_gate_passes_for_accept(tmp_path: Path) -> None:
    """verification status=pass + assessment=accept -> disposition=accept -> gate passes."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    paths = PathRegistry(planspace)

    write_json(paths.verification_status("01"), {
        "section": "01",
        "source": "verification.structural",
        "status": "pass",
    })

    result = check_verification_gate(ArtifactIOService(), planspace, "01")
    assert result.passed is True
    assert result.disposition == "accept"


def test_gate_passes_for_accept_with_debt(tmp_path: Path) -> None:
    """verification status=pass + assessment=accept_with_debt -> accept_with_debt -> passes."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    paths = PathRegistry(planspace)

    write_json(paths.verification_status("02"), {
        "section": "02",
        "source": "verification.structural",
        "status": "pass",
    })
    write_json(paths.post_impl_assessment("02"), {
        "section": "02",
        "verdict": "accept_with_debt",
        "problem_ids_addressed": [],
        "pattern_ids_followed": [],
        "debt_items": [{"category": "test", "description": "needs tests"}],
        "refactor_reasons": [],
        "profile_id": "",
        "lenses": {},
    })

    result = check_verification_gate(ArtifactIOService(), planspace, "02")
    assert result.passed is True
    assert result.disposition == "accept_with_debt"


# -- Gate blocks for findings_local ------------------------------------------

def test_gate_blocks_for_findings_local(tmp_path: Path) -> None:
    """verification status=findings_local -> disposition=retry_local -> gate blocks."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    paths = PathRegistry(planspace)

    write_json(paths.verification_status("03"), {
        "section": "03",
        "source": "verification.structural",
        "status": "findings_local",
        "error_count": 2,
    })

    result = check_verification_gate(ArtifactIOService(), planspace, "03")
    assert result.passed is False
    assert result.disposition == "retry_local"
    assert "findings_local" in result.detail


# -- Gate blocks for findings_cross_section ----------------------------------

def test_gate_blocks_for_findings_cross_section(tmp_path: Path) -> None:
    """verification status=findings_cross_section -> escalate_coordination -> blocks."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    paths = PathRegistry(planspace)

    write_json(paths.verification_status("04"), {
        "section": "04",
        "source": "verification.structural",
        "status": "findings_cross_section",
    })

    result = check_verification_gate(ArtifactIOService(), planspace, "04")
    assert result.passed is False
    assert result.disposition == "escalate_coordination"


# -- Gate blocks for inconclusive (accept_unverified not in passing set) -----

def test_gate_blocks_for_inconclusive(tmp_path: Path) -> None:
    """verification status=inconclusive -> accept_unverified -> gate blocks.

    accept_unverified is NOT in the passing dispositions per PRB-0008 Item 15.
    """
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    paths = PathRegistry(planspace)

    write_json(paths.verification_status("05"), {
        "section": "05",
        "source": "verification.structural",
        "status": "inconclusive",
    })

    result = check_verification_gate(ArtifactIOService(), planspace, "05")
    assert result.passed is False
    assert result.disposition == "accept_unverified"


# -- Gate blocks for refactor_required assessment + pass verification --------

def test_gate_blocks_when_assessment_is_refactor_required(tmp_path: Path) -> None:
    """assessment=refactor_required overrides verification=pass -> blocks."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    paths = PathRegistry(planspace)

    write_json(paths.verification_status("06"), {
        "section": "06",
        "source": "verification.structural",
        "status": "pass",
    })
    write_json(paths.post_impl_assessment("06"), {
        "section": "06",
        "verdict": "refactor_required",
        "problem_ids_addressed": [],
        "pattern_ids_followed": [],
        "debt_items": [],
        "refactor_reasons": ["poor structure"],
        "profile_id": "",
        "lenses": {},
    })

    result = check_verification_gate(ArtifactIOService(), planspace, "06")
    assert result.passed is False
    assert result.disposition == "refactor_required"


# -- Gate fails closed on malformed verification_status ----------------------

def test_gate_fails_closed_on_malformed_status(tmp_path: Path) -> None:
    """Malformed verification_status (missing status field) -> gate fails closed."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    paths = PathRegistry(planspace)

    write_json(paths.verification_status("07"), {
        "section": "07",
        "source": "verification.structural",
        # "status" key intentionally missing
    })

    result = check_verification_gate(ArtifactIOService(), planspace, "07")
    assert result.passed is False
    assert "missing or invalid" in result.detail


# -- Gate defaults assessment to accept when assessment not yet available -----

def test_gate_uses_default_assessment_when_missing(tmp_path: Path) -> None:
    """When post-impl assessment has not yet run, default to accept.

    verification=pass + assessment=accept (default) -> disposition=accept -> passes.
    """
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    paths = PathRegistry(planspace)

    write_json(paths.verification_status("08"), {
        "section": "08",
        "source": "verification.structural",
        "status": "pass",
    })
    # No post_impl_assessment written -- should default to "accept"

    result = check_verification_gate(ArtifactIOService(), planspace, "08")
    assert result.passed is True
    assert result.disposition == "accept"
