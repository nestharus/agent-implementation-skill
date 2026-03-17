"""Verdict synthesis: combine assessment + verification into a single disposition.

Implements the disposition lattice from PRB-0008 (Item 13). The rule is:
take the more conservative of the assessment verdict and verification verdict.

Assessment verdicts: accept, accept_with_debt, refactor_required
Verification verdicts: pass, findings_local, findings_cross_section, inconclusive
"""

from __future__ import annotations

from verification.types import SynthesizedVerdict

# -- Assessment verdict constants (match AssessmentVerdict enum values) -----

_ACCEPT = "accept"
_ACCEPT_WITH_DEBT = "accept_with_debt"
_REFACTOR_REQUIRED = "refactor_required"

# -- Verification verdict constants -----------------------------------------

_PASS = "pass"
_FINDINGS_LOCAL = "findings_local"
_FINDINGS_CROSS_SECTION = "findings_cross_section"
_INCONCLUSIVE = "inconclusive"

# -- Disposition constants ---------------------------------------------------

DISPOSITION_ACCEPT = "accept"
DISPOSITION_ACCEPT_WITH_DEBT = "accept_with_debt"
DISPOSITION_ACCEPT_UNVERIFIED = "accept_unverified"
DISPOSITION_RETRY_LOCAL = "retry_local"
DISPOSITION_ESCALATE_COORDINATION = "escalate_coordination"
DISPOSITION_REFACTOR_REQUIRED = "refactor_required"

# -- Action routing ----------------------------------------------------------

ACTION_PROCEED = "proceed"
ACTION_RETRY = "retry"
ACTION_ESCALATE = "escalate"
ACTION_REOPEN = "reopen"

# -- Disposition lattice (assessment_verdict, verification_verdict) -> disposition

_LATTICE: dict[tuple[str, str], tuple[str, bool, str]] = {
    # (assessment, verification) -> (disposition, advisory_degraded, action)
    #
    # accept + verification variants
    (_ACCEPT, _PASS): (DISPOSITION_ACCEPT, False, ACTION_PROCEED),
    (_ACCEPT, _FINDINGS_LOCAL): (DISPOSITION_RETRY_LOCAL, False, ACTION_RETRY),
    (_ACCEPT, _FINDINGS_CROSS_SECTION): (DISPOSITION_ESCALATE_COORDINATION, False, ACTION_ESCALATE),
    (_ACCEPT, _INCONCLUSIVE): (DISPOSITION_ACCEPT_UNVERIFIED, True, ACTION_PROCEED),
    #
    # accept_with_debt + verification variants
    (_ACCEPT_WITH_DEBT, _PASS): (DISPOSITION_ACCEPT_WITH_DEBT, False, ACTION_PROCEED),
    (_ACCEPT_WITH_DEBT, _FINDINGS_LOCAL): (DISPOSITION_RETRY_LOCAL, False, ACTION_RETRY),
    (_ACCEPT_WITH_DEBT, _FINDINGS_CROSS_SECTION): (DISPOSITION_ESCALATE_COORDINATION, False, ACTION_ESCALATE),
    (_ACCEPT_WITH_DEBT, _INCONCLUSIVE): (DISPOSITION_ACCEPT_UNVERIFIED, True, ACTION_PROCEED),
    #
    # refactor_required + anything = refactor_required
    (_REFACTOR_REQUIRED, _PASS): (DISPOSITION_REFACTOR_REQUIRED, False, ACTION_REOPEN),
    (_REFACTOR_REQUIRED, _FINDINGS_LOCAL): (DISPOSITION_REFACTOR_REQUIRED, False, ACTION_REOPEN),
    (_REFACTOR_REQUIRED, _FINDINGS_CROSS_SECTION): (DISPOSITION_REFACTOR_REQUIRED, False, ACTION_REOPEN),
    (_REFACTOR_REQUIRED, _INCONCLUSIVE): (DISPOSITION_REFACTOR_REQUIRED, False, ACTION_REOPEN),
}


def synthesize_verdict(
    assessment_verdict: str,
    verification_verdict: str,
) -> SynthesizedVerdict:
    """Combine assessment and verification verdicts into a single disposition.

    The disposition lattice always takes the more conservative outcome.
    Unknown verdict values fall through to ``refactor_required`` (fail-closed).
    """
    key = (assessment_verdict, verification_verdict)
    entry = _LATTICE.get(key)
    if entry is None:
        # Unknown combination — fail-closed
        return SynthesizedVerdict(
            disposition=DISPOSITION_REFACTOR_REQUIRED,
            assessment_verdict=assessment_verdict,
            verification_verdict=verification_verdict,
            advisory_degraded=False,
            action=ACTION_REOPEN,
        )

    disposition, advisory_degraded, action = entry
    return SynthesizedVerdict(
        disposition=disposition,
        assessment_verdict=assessment_verdict,
        verification_verdict=verification_verdict,
        advisory_degraded=advisory_degraded,
        action=action,
    )
