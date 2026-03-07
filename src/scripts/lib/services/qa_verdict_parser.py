"""Parse structured QA verdicts from agent output."""

from __future__ import annotations

import json


def parse_qa_verdict(output: str) -> tuple[str, str, list[str]]:
    """Return ``(verdict, rationale, violations)`` from QA output.

    Parsing is fail-open. Any malformed output yields a PASS verdict with
    a parse-failure rationale and no violations.
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
            return "PASS", f"Unknown verdict '{verdict}' — defaulting to PASS", []
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return "PASS", "QA agent output could not be parsed — defaulting to PASS", []
