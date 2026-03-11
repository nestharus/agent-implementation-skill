"""Structural assertions for collected agentic eval outputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .collectors import CollectedOutput
from .scenario_loader import StructuralCheck


@dataclass
class CheckResult:
    type: str
    target: str
    verdict: str
    detail: str
    required: bool


def _pass(check: StructuralCheck, target: str, detail: str) -> CheckResult:
    return CheckResult(
        type=check.type,
        target=target,
        verdict="PASS",
        detail=detail,
        required=check.required,
    )


def _fail(check: StructuralCheck, target: str, detail: str) -> CheckResult:
    return CheckResult(
        type=check.type,
        target=target,
        verdict="FAIL",
        detail=detail,
        required=check.required,
    )


def _filter_rows(rows: list[dict], where: dict | None) -> list[dict]:
    if not where:
        return rows
    filtered = []
    for row in rows:
        if all(row.get(key) == value for key, value in where.items()):
            filtered.append(row)
    return filtered


def run_structural_checks(
    checks: list[StructuralCheck],
    collected: CollectedOutput,
    planspace: Path,
) -> list[CheckResult]:
    """Run structural checks against collected outputs."""
    results: list[CheckResult] = []
    for check in checks:
        target = check.path or check.query or check.type
        if check.type == "exists":
            exists = (planspace / str(check.path)).exists()
            results.append(
                _pass(check, target, "path exists") if exists else _fail(check, target, "path missing")
            )
            continue

        if check.type == "path_absent":
            exists = (planspace / str(check.path)).exists()
            results.append(
                _pass(check, target, "path absent") if not exists else _fail(check, target, "path exists")
            )
            continue

        if check.type == "json_valid":
            parseable = collected.parseability_map.get(str(check.path), False)
            results.append(
                _pass(check, target, "valid JSON") if parseable else _fail(check, target, "invalid or missing JSON")
            )
            continue

        if check.type == "json_has_keys":
            data = collected.files_json.get(str(check.path))
            if not isinstance(data, dict):
                results.append(_fail(check, target, "JSON object missing or unparsable"))
                continue
            missing = [key for key in check.keys or [] if key not in data]
            results.append(
                _pass(check, target, "required keys present")
                if not missing
                else _fail(check, target, f"missing keys: {', '.join(missing)}")
            )
            continue

        if check.type == "markdown_has_heading":
            content = collected.files.get(str(check.path), "")
            heading = check.heading or ""
            found = any(
                re.match(r"^\s{0,3}#{1,6}\s+", line)
                and line.lstrip("# ").strip() == heading.lstrip("# ").strip()
                for line in content.splitlines()
            )
            results.append(
                _pass(check, target, f"heading present: {heading}")
                if found
                else _fail(check, target, f"heading missing: {heading}")
            )
            continue

        if check.type == "db_min_rows":
            rows = collected.db.get(str(check.query), [])
            matched = _filter_rows(rows, check.where)
            minimum = int(check.min or 0)
            results.append(
                _pass(check, target, f"{len(matched)} matching rows")
                if len(matched) >= minimum
                else _fail(check, target, f"expected at least {minimum} rows, found {len(matched)}")
            )
            continue

        if check.type == "glob_min_count":
            matched = [path for path in planspace.glob(str(check.path)) if path.is_file()]
            minimum = int(check.min or 0)
            results.append(
                _pass(check, target, f"{len(matched)} files matched")
                if len(matched) >= minimum
                else _fail(check, target, f"expected at least {minimum} matches, found {len(matched)}")
            )
            continue

        if check.type == "text_contains":
            content = collected.files.get(str(check.path), "")
            pattern = check.pattern or ""
            matches = bool(content) and re.search(pattern, content, flags=re.MULTILINE) is not None
            results.append(
                _pass(check, target, f"matched /{pattern}/")
                if matches
                else _fail(check, target, f"pattern not found: /{pattern}/")
            )
            continue

        if check.type == "signal_state":
            data = collected.files_json.get(str(check.path))
            allowed = set(check.allowed_states or [])
            if not isinstance(data, dict):
                results.append(_fail(check, target, "signal JSON missing or unparsable"))
                continue
            state = data.get("state")
            results.append(
                _pass(check, target, f"state={state}")
                if state in allowed
                else _fail(check, target, f"expected one of {sorted(allowed)}, found {state!r}")
            )
            continue

        results.append(_fail(check, target, "unsupported structural check type"))
    return results
