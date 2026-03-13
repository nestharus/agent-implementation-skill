from __future__ import annotations

import json
import re
from pathlib import Path

from orchestrator.path_registry import PathRegistry

_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
_VALID_ACTIONS = {"accept", "reject", "absorb"}


def parse_scope_delta_adjudication(output_text: str) -> dict | None:
    """Parse scope-delta adjudication JSON from agent output."""
    candidates: list[str] = []

    for match in _FENCE_RE.finditer(output_text):
        candidates.append(match.group(1).strip())

    for line in output_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("{") and "decisions" in stripped:
            candidates.append(stripped)

    start = output_text.find("{")
    end = output_text.rfind("}")
    if start >= 0 and end > start:
        candidate = output_text[start:end + 1]
        if "decisions" in candidate:
            candidates.append(candidate)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(data, dict):
            continue
        decisions = data.get("decisions")
        if not isinstance(decisions, list):
            continue

        valid = True
        for decision in decisions:
            if not isinstance(decision, dict):
                valid = False
                break
            if not all(key in decision for key in ("action", "reason")):
                valid = False
                break
            if "delta_id" not in decision and "section" not in decision:
                valid = False
                break
            if decision["action"] not in _VALID_ACTIONS:
                valid = False
                break
            if decision["action"] == "accept" and "new_sections" not in decision:
                valid = False
                break
            if decision["action"] == "absorb" and (
                "absorb_into_section" not in decision
                or "scope_addition" not in decision
            ):
                valid = False
                break

        if valid:
            return data

    return None


def normalize_section_id(sec_str: str, paths: PathRegistry) -> str:
    """Normalize a section ID to match existing delta filenames."""
    sec_str = str(sec_str).strip()

    if paths.scope_delta_section(sec_str).exists():
        return sec_str

    try:
        num = int(sec_str)
        padded = f"{num:02d}"
        if paths.scope_delta_section(padded).exists():
            return padded
    except ValueError:
        pass

    return sec_str
