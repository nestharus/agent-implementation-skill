"""Verification gate: read verification_status and determine if a section passes.

Implements the post-implementation gate from PRB-0008 (Item 15).  A section's
``aligned`` status requires BOTH the alignment check to pass AND the
verification synthesis disposition to be ``accept`` or ``accept_with_debt``.

Verification tasks run asynchronously.  When no verification_status artifact
exists for a section, the gate is **open** (fail-open for absent verification)
so that sections without verification tasks are not blocked.  Once a
verification_status artifact is written, the gate becomes **closed** unless
the synthesized disposition permits proceeding.

Integration verification findings are advisory and do not gate the section.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from verification.service.verdict_synthesis import (
    DISPOSITION_ACCEPT,
    DISPOSITION_ACCEPT_WITH_DEBT,
    synthesize_verdict,
)

if TYPE_CHECKING:
    from containers import ArtifactIOService

logger = logging.getLogger(__name__)

# Dispositions that allow the section to remain aligned.
_PASSING_DISPOSITIONS = frozenset({DISPOSITION_ACCEPT, DISPOSITION_ACCEPT_WITH_DEBT})

# Default assessment verdict when the post-impl assessment has not yet run.
_DEFAULT_ASSESSMENT_VERDICT = "accept"


@dataclass(frozen=True)
class VerificationGateResult:
    """Result of the verification gate check."""

    passed: bool
    disposition: str = ""
    detail: str = ""


def check_verification_gate(
    artifact_io: ArtifactIOService,
    planspace: Path,
    section_number: str,
) -> VerificationGateResult:
    """Check whether the verification gate allows a section to be marked aligned.

    Returns a ``VerificationGateResult``.  When ``passed`` is ``True``, the
    section may be marked aligned (assuming the alignment check also passed).

    Gate logic:
    - If no verification_status artifact exists, the gate is **open** (passed=True).
      Verification tasks may not have been submitted or completed yet.
    - If a verification_status artifact exists, read its ``status`` field and
      synthesize a verdict with the post-impl assessment verdict (defaulting
      to ``accept`` if the assessment has not yet run).
    - The gate passes only when the synthesized disposition is ``accept`` or
      ``accept_with_debt``.
    """
    paths = PathRegistry(planspace)
    status_path = paths.verification_status(section_number)

    status_data = artifact_io.read_json(status_path)
    if status_data is None or not isinstance(status_data, dict):
        # No verification status yet -- gate is open.
        return VerificationGateResult(passed=True)

    verification_verdict = status_data.get("status", "")
    if not isinstance(verification_verdict, str) or not verification_verdict:
        logger.warning(
            "Section %s: verification_status has missing/invalid status field",
            section_number,
        )
        # Malformed status -- fail-closed.
        return VerificationGateResult(
            passed=False,
            detail="verification_status artifact has missing or invalid status field",
        )

    # Read the post-impl assessment verdict.  If the assessment has not yet
    # run, default to ``accept`` so that verification alone can gate.
    assessment_path = paths.post_impl_assessment(section_number)
    assessment_data = artifact_io.read_json(assessment_path)
    if isinstance(assessment_data, dict):
        assessment_verdict = assessment_data.get("verdict", _DEFAULT_ASSESSMENT_VERDICT)
        if not isinstance(assessment_verdict, str) or not assessment_verdict:
            assessment_verdict = _DEFAULT_ASSESSMENT_VERDICT
    else:
        assessment_verdict = _DEFAULT_ASSESSMENT_VERDICT

    synthesized = synthesize_verdict(assessment_verdict, verification_verdict)

    passed = synthesized.disposition in _PASSING_DISPOSITIONS
    detail = (
        f"disposition={synthesized.disposition} "
        f"(assessment={assessment_verdict}, verification={verification_verdict})"
    )
    if not passed:
        logger.info(
            "Section %s: verification gate BLOCKED -- %s",
            section_number,
            detail,
        )

    return VerificationGateResult(
        passed=passed,
        disposition=synthesized.disposition,
        detail=detail,
    )
