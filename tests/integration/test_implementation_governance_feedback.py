"""Integration tests for the Implementation -> Intake/Assessment -> Risk boundary.

After implementation completes, the governance assessment evaluator produces
risk-register-signal files.  ``promote_debt_signals()`` consumes these signals
and writes consolidated debt entries to ``risk-register-staging.json``.  This
file verifies the end-to-end data flow through real filesystem I/O.

Mock boundary: none -- all services (ArtifactIOService, PromptGuard) are the
real production implementations.  Only filesystem paths are ephemeral (tmp_path).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from containers import ArtifactIOService, PromptGuard
from intake.service.assessment_evaluator import AssessmentEvaluator
from orchestrator.path_registry import PathRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evaluator() -> AssessmentEvaluator:
    """Construct an AssessmentEvaluator with real services."""
    return AssessmentEvaluator(
        artifact_io=ArtifactIOService(),
        prompt_guard=PromptGuard(),
    )


def _make_planspace(tmp_path: Path) -> Path:
    """Create a planspace with initialized artifact tree."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    PathRegistry(ps).ensure_artifacts_tree()
    return ps


def _write_risk_register_signal(
    planspace: Path,
    section: str,
    debt_items: list[dict],
    *,
    verdict: str = "accept_with_debt",
    problem_ids: list[str] | None = None,
    pattern_ids: list[str] | None = None,
    profile_id: str = "PHI-global",
) -> Path:
    """Write a risk-register-signal.json at the canonical PathRegistry location."""
    paths = PathRegistry(planspace)
    signal_path = paths.risk_register_signal(section)
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal = {
        "section": section,
        "verdict": verdict,
        "debt_items": debt_items,
        "problem_ids": problem_ids or ["PRB-0001"],
        "pattern_ids": pattern_ids or ["PAT-0001"],
        "profile_id": profile_id,
    }
    signal_path.write_text(json.dumps(signal, indent=2), encoding="utf-8")
    return signal_path


# ---------------------------------------------------------------------------
# C6: Implementation -> Assessment -> Risk staging
# ---------------------------------------------------------------------------

class TestDebtSignalPromotion:
    """promote_debt_signals() reads risk-register-signal files and writes
    consolidated entries to risk-register-staging.json."""

    def test_single_signal_promotes_debt_to_staging(self, tmp_path: Path) -> None:
        """A single risk-register-signal with one debt item produces
        exactly one entry in risk-register-staging.json."""
        planspace = _make_planspace(tmp_path)

        _write_risk_register_signal(planspace, "01", [
            {
                "category": "coupling",
                "region": "src/auth.py",
                "description": "Tight coupling between auth and session modules",
                "severity": "medium",
                "acceptance_rationale": "Acceptable for initial release",
                "mitigation": "Planned refactor in next cycle",
            },
        ])

        promoted = _evaluator().promote_debt_signals(planspace)

        assert len(promoted) == 1
        assert promoted[0]["section"] == "01"
        assert promoted[0]["category"] == "coupling"
        assert promoted[0]["severity"] == "medium"
        assert promoted[0]["source"] == "post_impl_assessment"
        assert "debt_key" in promoted[0]

        # Verify staging file is written
        staging_path = PathRegistry(planspace).risk_register_staging()
        assert staging_path.exists()
        staging = json.loads(staging_path.read_text(encoding="utf-8"))
        assert isinstance(staging, list)
        assert len(staging) == 1
        assert staging[0]["category"] == "coupling"

    def test_no_signal_produces_no_staging(self, tmp_path: Path) -> None:
        """When no risk-register-signal files exist, promote_debt_signals
        returns an empty list and does not create a staging file."""
        planspace = _make_planspace(tmp_path)

        promoted = _evaluator().promote_debt_signals(planspace)

        assert promoted == []
        staging_path = PathRegistry(planspace).risk_register_staging()
        assert not staging_path.exists()

    def test_multiple_debt_items_all_promoted(self, tmp_path: Path) -> None:
        """A signal with multiple debt items promotes all of them."""
        planspace = _make_planspace(tmp_path)

        _write_risk_register_signal(planspace, "03", [
            {
                "category": "security",
                "region": "src/api.py",
                "description": "Missing rate limiting on public endpoints",
                "severity": "high",
                "acceptance_rationale": "Behind internal network",
                "mitigation": "Rate limiter scheduled for sprint 5",
            },
            {
                "category": "scalability",
                "region": "src/db.py",
                "description": "N+1 query pattern in batch processor",
                "severity": "medium",
                "acceptance_rationale": "Low traffic for now",
                "mitigation": "Batch query optimization planned",
            },
        ])

        promoted = _evaluator().promote_debt_signals(planspace)

        assert len(promoted) == 2
        categories = sorted(e["category"] for e in promoted)
        assert categories == ["scalability", "security"]

    def test_accept_with_debt_verdict_items_appear_in_staging(
        self, tmp_path: Path,
    ) -> None:
        """Debt items from an accept_with_debt signal are present in
        the staging file with all fields preserved."""
        planspace = _make_planspace(tmp_path)

        _write_risk_register_signal(
            planspace, "05",
            [
                {
                    "category": "pattern-drift",
                    "region": "src/handlers/",
                    "description": "Handler bypasses middleware pattern",
                    "severity": "low",
                    "acceptance_rationale": "Edge case handler",
                    "mitigation": "Document exception in ADR",
                },
            ],
            verdict="accept_with_debt",
            problem_ids=["PRB-0042"],
            pattern_ids=["PAT-0007"],
            profile_id="PHI-strict",
        )

        promoted = _evaluator().promote_debt_signals(planspace)
        assert len(promoted) == 1

        entry = promoted[0]
        assert entry["category"] == "pattern-drift"
        assert entry["region"] == "src/handlers/"
        assert entry["description"] == "Handler bypasses middleware pattern"
        assert entry["severity"] == "low"
        assert entry["acceptance_rationale"] == "Edge case handler"
        assert entry["mitigation"] == "Document exception in ADR"
        assert entry["source"] == "post_impl_assessment"
        assert entry["problem_ids"] == ["PRB-0042"]
        assert entry["pattern_ids"] == ["PAT-0007"]
        assert entry["profile_id"] == "PHI-strict"

        # Verify persisted staging
        staging_path = PathRegistry(planspace).risk_register_staging()
        staging = json.loads(staging_path.read_text(encoding="utf-8"))
        assert len(staging) == 1
        assert staging[0]["category"] == "pattern-drift"

    def test_multi_section_signals_consolidated(self, tmp_path: Path) -> None:
        """Signals from multiple sections are consolidated into a single
        staging file."""
        planspace = _make_planspace(tmp_path)

        _write_risk_register_signal(planspace, "01", [
            {
                "category": "coupling",
                "region": "src/auth.py",
                "description": "Auth-session coupling",
                "severity": "medium",
                "acceptance_rationale": "Short term",
                "mitigation": "Decouple next cycle",
            },
        ])
        _write_risk_register_signal(planspace, "02", [
            {
                "category": "operability",
                "region": "src/logging.py",
                "description": "Missing structured logging",
                "severity": "low",
                "acceptance_rationale": "Non-critical path",
                "mitigation": "Add structured logging",
            },
        ])

        promoted = _evaluator().promote_debt_signals(planspace)

        assert len(promoted) == 2
        sections = sorted(e["section"] for e in promoted)
        assert sections == ["01", "02"]

        staging_path = PathRegistry(planspace).risk_register_staging()
        staging = json.loads(staging_path.read_text(encoding="utf-8"))
        assert len(staging) == 2


class TestDebtSignalDeduplication:
    """promote_debt_signals() deduplicates against already-staged entries."""

    def test_identical_signal_not_re_promoted(self, tmp_path: Path) -> None:
        """Running promote_debt_signals twice with the same signal
        does not create duplicate entries in staging."""
        planspace = _make_planspace(tmp_path)

        item = {
            "category": "coupling",
            "region": "section-01",
            "description": "tight coupling to cache",
            "severity": "medium",
            "mitigation": "planned refactor",
            "acceptance_rationale": "acceptable for now",
        }
        _write_risk_register_signal(planspace, "01", [item])

        first = _evaluator().promote_debt_signals(planspace)
        assert len(first) == 1

        # Write the same signal again (simulate re-run)
        _write_risk_register_signal(planspace, "01", [item])
        second = _evaluator().promote_debt_signals(planspace)
        assert len(second) == 0

        # Staging should still have exactly one entry
        staging_path = PathRegistry(planspace).risk_register_staging()
        staging = json.loads(staging_path.read_text(encoding="utf-8"))
        assert len(staging) == 1

    def test_changed_severity_repromotes(self, tmp_path: Path) -> None:
        """A materially changed debt item (e.g., severity change) is
        re-promoted as a new entry."""
        planspace = _make_planspace(tmp_path)

        item = {
            "category": "coupling",
            "region": "section-01",
            "description": "tight coupling to cache",
            "severity": "medium",
            "mitigation": "planned refactor",
            "acceptance_rationale": "acceptable for now",
        }
        _write_risk_register_signal(planspace, "01", [item])
        first = _evaluator().promote_debt_signals(planspace)
        assert len(first) == 1

        # Change severity -- material change per PAT-0012
        item["severity"] = "high"
        _write_risk_register_signal(planspace, "01", [item])
        second = _evaluator().promote_debt_signals(planspace)
        assert len(second) == 1
        assert second[0]["severity"] == "high"

        # Staging now has both versions
        staging_path = PathRegistry(planspace).risk_register_staging()
        staging = json.loads(staging_path.read_text(encoding="utf-8"))
        assert len(staging) == 2


class TestSignalWithNoDebtItems:
    """Edge cases: signals with empty or invalid debt_items."""

    def test_signal_with_empty_debt_items_produces_nothing(
        self, tmp_path: Path,
    ) -> None:
        """A signal file with an empty debt_items list results in no
        promoted entries and no staging file."""
        planspace = _make_planspace(tmp_path)

        _write_risk_register_signal(planspace, "01", [])

        promoted = _evaluator().promote_debt_signals(planspace)
        assert promoted == []

    def test_signal_with_non_dict_debt_items_skipped(
        self, tmp_path: Path,
    ) -> None:
        """Non-dict entries in debt_items are silently skipped."""
        planspace = _make_planspace(tmp_path)

        # Write a signal where debt_items contains a string instead of dict
        paths = PathRegistry(planspace)
        signal_path = paths.risk_register_signal("01")
        signal = {
            "section": "01",
            "debt_items": ["not a dict", 42],
            "problem_ids": [],
            "pattern_ids": [],
            "profile_id": "",
        }
        signal_path.write_text(json.dumps(signal), encoding="utf-8")

        promoted = _evaluator().promote_debt_signals(planspace)
        assert promoted == []
