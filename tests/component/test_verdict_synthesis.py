"""Component tests for verdict synthesis (PRB-0008, Item 13)."""

from __future__ import annotations

import pytest

from verification.service.verdict_synthesis import (
    ACTION_ESCALATE,
    ACTION_PROCEED,
    ACTION_REOPEN,
    ACTION_RETRY,
    DISPOSITION_ACCEPT,
    DISPOSITION_ACCEPT_UNVERIFIED,
    DISPOSITION_ACCEPT_WITH_DEBT,
    DISPOSITION_ESCALATE_COORDINATION,
    DISPOSITION_REFACTOR_REQUIRED,
    DISPOSITION_RETRY_LOCAL,
    synthesize_verdict,
)
from verification.types import SynthesizedVerdict


# -- accept + verification variants -----------------------------------------


def test_accept_pass_yields_accept() -> None:
    v = synthesize_verdict("accept", "pass")
    assert v.disposition == DISPOSITION_ACCEPT
    assert v.action == ACTION_PROCEED
    assert v.advisory_degraded is False


def test_accept_findings_local_yields_retry() -> None:
    v = synthesize_verdict("accept", "findings_local")
    assert v.disposition == DISPOSITION_RETRY_LOCAL
    assert v.action == ACTION_RETRY
    assert v.advisory_degraded is False


def test_accept_findings_cross_section_yields_escalate() -> None:
    v = synthesize_verdict("accept", "findings_cross_section")
    assert v.disposition == DISPOSITION_ESCALATE_COORDINATION
    assert v.action == ACTION_ESCALATE
    assert v.advisory_degraded is False


def test_accept_inconclusive_yields_unverified_with_degraded() -> None:
    v = synthesize_verdict("accept", "inconclusive")
    assert v.disposition == DISPOSITION_ACCEPT_UNVERIFIED
    assert v.advisory_degraded is True
    assert v.action == ACTION_PROCEED


# -- accept_with_debt + verification variants --------------------------------


def test_accept_with_debt_pass_yields_accept_with_debt() -> None:
    v = synthesize_verdict("accept_with_debt", "pass")
    assert v.disposition == DISPOSITION_ACCEPT_WITH_DEBT
    assert v.action == ACTION_PROCEED
    assert v.advisory_degraded is False


def test_accept_with_debt_findings_local_yields_retry() -> None:
    v = synthesize_verdict("accept_with_debt", "findings_local")
    assert v.disposition == DISPOSITION_RETRY_LOCAL
    assert v.action == ACTION_RETRY


def test_accept_with_debt_findings_cross_section_yields_escalate() -> None:
    v = synthesize_verdict("accept_with_debt", "findings_cross_section")
    assert v.disposition == DISPOSITION_ESCALATE_COORDINATION
    assert v.action == ACTION_ESCALATE


def test_accept_with_debt_inconclusive_yields_unverified() -> None:
    v = synthesize_verdict("accept_with_debt", "inconclusive")
    assert v.disposition == DISPOSITION_ACCEPT_UNVERIFIED
    assert v.advisory_degraded is True


# -- refactor_required dominates everything ----------------------------------


@pytest.mark.parametrize(
    "verification_verdict",
    ["pass", "findings_local", "findings_cross_section", "inconclusive"],
)
def test_refactor_required_always_yields_refactor(verification_verdict: str) -> None:
    v = synthesize_verdict("refactor_required", verification_verdict)
    assert v.disposition == DISPOSITION_REFACTOR_REQUIRED
    assert v.action == ACTION_REOPEN
    assert v.advisory_degraded is False


# -- Unknown inputs fall through to fail-closed ------------------------------


def test_unknown_assessment_verdict_yields_refactor() -> None:
    v = synthesize_verdict("some_new_verdict", "pass")
    assert v.disposition == DISPOSITION_REFACTOR_REQUIRED
    assert v.action == ACTION_REOPEN


def test_unknown_verification_verdict_yields_refactor() -> None:
    v = synthesize_verdict("accept", "some_new_status")
    assert v.disposition == DISPOSITION_REFACTOR_REQUIRED
    assert v.action == ACTION_REOPEN


# -- Return type is SynthesizedVerdict dataclass ----------------------------


def test_return_type_is_synthesized_verdict() -> None:
    v = synthesize_verdict("accept", "pass")
    assert isinstance(v, SynthesizedVerdict)
    assert v.assessment_verdict == "accept"
    assert v.verification_verdict == "pass"


# -- Symmetry: debt + findings is same disposition as accept + findings ------


def test_findings_local_same_disposition_regardless_of_debt() -> None:
    v1 = synthesize_verdict("accept", "findings_local")
    v2 = synthesize_verdict("accept_with_debt", "findings_local")
    assert v1.disposition == v2.disposition == DISPOSITION_RETRY_LOCAL


def test_findings_cross_section_same_disposition_regardless_of_debt() -> None:
    v1 = synthesize_verdict("accept", "findings_cross_section")
    v2 = synthesize_verdict("accept_with_debt", "findings_cross_section")
    assert v1.disposition == v2.disposition == DISPOSITION_ESCALATE_COORDINATION
