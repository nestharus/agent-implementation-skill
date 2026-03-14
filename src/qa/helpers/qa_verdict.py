"""Parse structured QA verdicts from agent output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    """QA verdict outcome.

    Inherits from ``str`` so ``==`` comparisons with plain strings work.
    """

    PASS = "PASS"
    REJECT = "REJECT"
    DEGRADED = "DEGRADED"

    def __str__(self) -> str:  # noqa: D105
        return self.value


@dataclass(frozen=True)
class QaVerdict:
    """Structured result from QA verdict parsing."""

    verdict: Verdict
    rationale: str
    violations: list[str] = field(default_factory=list)


def parse_qa_verdict(output: str) -> QaVerdict:
    """Return a ``QaVerdict`` parsed from QA output.

    Parsing is fail-open. Any malformed or unrecognised output yields a
    DEGRADED verdict (PAT-0014) so downstream consumers can distinguish
    genuine approval from parse-failure fallback.
    """
    try:
        json_start = output.find("{")
        json_end = output.rfind("}")
        if json_start >= 0 and json_end > json_start:
            data = json.loads(output[json_start:json_end + 1])
            verdict = str(data.get("verdict", "")).upper()
            rationale = data.get("rationale", "")
            violations = data.get("violations", [])
            if verdict in (Verdict.PASS, Verdict.REJECT):
                return QaVerdict(verdict=Verdict(verdict), rationale=rationale, violations=violations)
            return QaVerdict(verdict=Verdict.DEGRADED, rationale=f"Unknown verdict '{verdict}' — failing open")
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return QaVerdict(verdict=Verdict.DEGRADED, rationale="QA agent output could not be parsed — failing open")
