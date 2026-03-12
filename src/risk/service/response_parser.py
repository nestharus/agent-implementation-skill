"""Parse risk agent JSON responses into typed domain objects."""

from __future__ import annotations

import json
import re

from risk.repository.serialization import deserialize_assessment, deserialize_plan
from risk.types import RiskAssessment, RiskPlan

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_risk_assessment(response: str) -> RiskAssessment | None:
    """Parse the Risk Agent's JSON response into a RiskAssessment."""
    payload = _extract_json_payload(response)
    if payload is None:
        return None
    try:
        return deserialize_assessment(payload)
    except (KeyError, TypeError, ValueError):
        return None


def parse_risk_plan(response: str) -> RiskPlan | None:
    """Parse the Tool Agent's JSON response into a RiskPlan."""
    payload = _extract_json_payload(response)
    if payload is None:
        return None
    try:
        return deserialize_plan(payload)
    except (KeyError, TypeError, ValueError):
        return None


def _extract_json_payload(response: str) -> dict | None:
    candidate = response.strip()
    if not candidate:
        return None

    direct = _loads_object(candidate)
    if direct is not None:
        return direct

    fenced = _JSON_FENCE_RE.search(candidate)
    if fenced is not None:
        parsed = _loads_object(fenced.group(1))
        if parsed is not None:
            return parsed

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return _loads_object(candidate[start : end + 1])


def _loads_object(candidate: str) -> dict | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None
