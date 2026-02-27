"""Shard and seed-plan JSON validation with fail-closed behavior.

All validation is fail-closed: malformed files are renamed to
``.malformed.json`` and treated as absent rather than silently
accepted.
"""

from __future__ import annotations

import json
from pathlib import Path

# ---- Enumerations ----

TOUCHPOINTS_ENUM = [
    "types", "errors", "config", "auth", "db", "api", "events",
    "logging", "routing", "ui", "cli", "testing", "build", "deploy",
    "docs",
]

KIND_ENUM = [
    "api", "service", "type", "db", "event", "job", "ui", "config",
    "lib", "test",
]

# ---- Shard schema v1 ----

_SHARD_REQUIRED = [
    "schema_version",
    "section_number",
    "mode",
    "touchpoints",
    "provides",
    "needs",
    "shared_seams",
    "open_questions",
]

# ---- Seed-plan schema v1 ----

_SEED_PLAN_REQUIRED = [
    "schema_version",
    "anchors",
    "wire_sections",
]


def validate_shard(data: dict) -> list[str]:
    """Validate shard JSON against schema v1.

    Returns a list of error strings.  An empty list means the shard
    is valid.
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["shard is not a JSON object"]

    for field in _SHARD_REQUIRED:
        if field not in data:
            errors.append(f"missing required field: {field}")

    if "schema_version" in data and data["schema_version"] != 1:
        errors.append(
            f"unsupported schema_version: {data['schema_version']} "
            f"(expected 1)"
        )

    if "mode" in data and data["mode"] not in ("greenfield", "brownfield", "hybrid"):
        errors.append(f"invalid mode: {data['mode']}")

    if "touchpoints" in data:
        if not isinstance(data["touchpoints"], list):
            errors.append("touchpoints must be a list")
        else:
            for tp in data["touchpoints"]:
                if tp not in TOUCHPOINTS_ENUM:
                    errors.append(f"unknown touchpoint: {tp}")

    for list_field in ("provides", "needs", "shared_seams", "open_questions"):
        if list_field in data and not isinstance(data[list_field], list):
            errors.append(f"{list_field} must be a list")

    return errors


def validate_seed_plan(data: dict) -> list[str]:
    """Validate seed-plan JSON against schema v1.

    Returns a list of error strings.  An empty list means the seed
    plan is valid.
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["seed-plan is not a JSON object"]

    for field in _SEED_PLAN_REQUIRED:
        if field not in data:
            errors.append(f"missing required field: {field}")

    if "schema_version" in data and data["schema_version"] != 1:
        errors.append(
            f"unsupported schema_version: {data['schema_version']} "
            f"(expected 1)"
        )

    if "anchors" in data:
        if not isinstance(data["anchors"], list):
            errors.append("anchors must be a list")
        else:
            for i, anchor in enumerate(data["anchors"]):
                if not isinstance(anchor, dict):
                    errors.append(f"anchors[{i}] must be a dict")
                    continue
                if "path" not in anchor:
                    errors.append(f"anchors[{i}] missing 'path'")
                if "kind" in anchor and anchor["kind"] not in KIND_ENUM:
                    errors.append(
                        f"anchors[{i}] unknown kind: {anchor['kind']}"
                    )

    if "wire_sections" in data and not isinstance(data["wire_sections"], list):
        errors.append("wire_sections must be a list")

    return errors


def _read_failclosed(path: Path, validator, label: str) -> dict | None:
    """Internal helper: read JSON, validate, rename on failure."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[SUBSTRATE][WARN] {label} at {path} is malformed "
            f"({exc}) -- renaming to .malformed.json"
        )
        try:
            path.rename(path.with_suffix(".malformed.json"))
        except OSError:
            pass
        return None

    if not isinstance(data, dict):
        print(
            f"[SUBSTRATE][WARN] {label} at {path} is not a JSON "
            f"object -- renaming to .malformed.json"
        )
        try:
            path.rename(path.with_suffix(".malformed.json"))
        except OSError:
            pass
        return None

    errors = validator(data)
    if errors:
        print(
            f"[SUBSTRATE][WARN] {label} at {path} has validation "
            f"errors: {'; '.join(errors)} -- renaming to .malformed.json"
        )
        try:
            path.rename(path.with_suffix(".malformed.json"))
        except OSError:
            pass
        return None

    return data


def read_shard_failclosed(path: Path) -> dict | None:
    """Read and validate shard JSON.

    Renames malformed files to ``.malformed.json``.
    Returns ``None`` on failure.
    """
    return _read_failclosed(path, validate_shard, "Shard")


def read_seed_plan_failclosed(path: Path) -> dict | None:
    """Read and validate seed-plan JSON.

    Renames malformed files to ``.malformed.json``.
    Returns ``None`` on failure.
    """
    return _read_failclosed(path, validate_seed_plan, "Seed-plan")
