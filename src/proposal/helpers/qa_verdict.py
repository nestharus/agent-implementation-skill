"""Parse structured QA verdicts from agent output."""

from __future__ import annotations

import json


def parse_qa_verdict(output: str) -> tuple[str, str, list[str]]:
    """Return ``(verdict, rationale, violations)`` from QA output.

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
            if verdict in ("PASS", "REJECT"):
                return verdict, rationale, violations
            return "DEGRADED", f"Unknown verdict '{verdict}' — failing open", []
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return "DEGRADED", "QA agent output could not be parsed — failing open", []
