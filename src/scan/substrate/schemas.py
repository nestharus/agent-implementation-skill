"""Shard and seed-plan JSON validation with fail-closed behavior.

All validation is fail-closed: malformed files are renamed to
``.malformed.json`` and treated as absent rather than silently
accepted.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import ArtifactIOService

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

    if "mode" in data and data["mode"] not in (
        "greenfield", "brownfield", "hybrid", "unknown",
    ):
        errors.append(f"invalid mode: {data['mode']}")

    if "touchpoints" in data:
        if not isinstance(data["touchpoints"], list):
            errors.append("touchpoints must be a list")

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

    if "wire_sections" in data and not isinstance(data["wire_sections"], list):
        errors.append("wire_sections must be a list")

    return errors


class Schemas:
    """Shard and seed-plan JSON reading with fail-closed behavior.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def _read_failclosed(self, path: Path, validator, label: str) -> dict | None:
        """Internal helper: read JSON, validate, rename on failure."""
        data = self._artifact_io.read_json(path)
        if data is None:
            return None

        if not isinstance(data, dict):
            print(
                f"[SUBSTRATE][WARN] {label} at {path} is not a JSON "
                f"object -- renaming to .malformed.json"
            )
            self._artifact_io.rename_malformed(path)
            return None

        errors = validator(data)
        if errors:
            print(
                f"[SUBSTRATE][WARN] {label} at {path} has validation "
                f"errors: {'; '.join(errors)} -- renaming to .malformed.json"
            )
            self._artifact_io.rename_malformed(path)
            return None

        return data

    def read_shard_failclosed(self, path: Path) -> dict | None:
        """Read and validate shard JSON.

        Renames malformed files to ``.malformed.json``.
        Returns ``None`` on failure.
        """
        return self._read_failclosed(path, validate_shard, "Shard")

    def read_seed_plan_failclosed(self, path: Path) -> dict | None:
        """Read and validate seed-plan JSON.

        Renames malformed files to ``.malformed.json``.
        Returns ``None`` on failure.
        """
        return self._read_failclosed(path, validate_seed_plan, "Seed-plan")


# ------------------------------------------------------------------
# Backward-compat free function wrappers
# ------------------------------------------------------------------


def _default_schemas() -> Schemas:
    from containers import Services
    return Schemas(artifact_io=Services.artifact_io())


def read_shard_failclosed(path: Path) -> dict | None:
    """Read and validate shard JSON.

    Renames malformed files to ``.malformed.json``.
    Returns ``None`` on failure.
    """
    return _default_schemas().read_shard_failclosed(path)


def read_seed_plan_failclosed(path: Path) -> dict | None:
    """Read and validate seed-plan JSON.

    Renames malformed files to ``.malformed.json``.
    Returns ``None`` on failure.
    """
    return _default_schemas().read_seed_plan_failclosed(path)
