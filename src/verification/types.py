"""Verification data types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerificationFinding:
    """A single finding from a verification pass.

    ``scope`` is one of ``section_local``, ``cross_section``, ``system``.
    ``severity`` is one of ``error``, ``warning``.
    """

    finding_id: str
    scope: str
    category: str
    sections: list[str]
    file_paths: list[str]
    description: str
    severity: str
    evidence_snippet: str
    suggested_resolution: str


@dataclass(frozen=True)
class VerificationVerdict:
    """Aggregate verdict from a verification pass.

    ``status`` is one of ``pass``, ``findings_local``,
    ``findings_cross_section``, ``inconclusive``.
    """

    status: str
    findings: list[VerificationFinding] = field(default_factory=list)
    trigger_hash: str = ""


@dataclass(frozen=True)
class SynthesizedVerdict:
    """Combined assessment + verification verdict driving action selection."""

    disposition: str
    assessment_verdict: str
    verification_verdict: str
    advisory_degraded: bool
    action: str
