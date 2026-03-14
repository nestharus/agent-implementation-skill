"""Philosophy grounding validation.

Validates that distilled philosophy principles are traceable to
source files via the philosophy-source-map.json artifact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from containers import Services
from orchestrator.path_registry import PathRegistry

from intent.service.philosophy_bootstrap_state import (
    bootstrap_status_path,
    write_bootstrap_signal,
    write_bootstrap_status,
)
from intent.service.philosophy_classifier import (
    SOURCE_MODE_REPO,
    SOURCE_MODE_USER,
    _invalid_source_map_detail,
)
from intent.service.philosophy_catalog import declared_principle_ids
from signals.types import BLOCKING_NEEDS_PARENT

_MAX_STALE_SOURCES_IN_MESSAGE = 5


def sha256_file(path: Path) -> str:
    """Return hex sha256 of file contents, or empty string on error."""
    return Services.hasher().file_hash(path)


def _grounding_failure_source_mode(
    paths: PathRegistry,
    source_map: dict[str, Any] | None,
) -> str:
    """Infer the correct source_mode for grounding failure metadata."""
    if isinstance(source_map, dict) and source_map:
        source_types = {
            entry.get("source_type")
            for entry in source_map.values()
            if isinstance(entry, dict)
        }
        if source_types == {SOURCE_MODE_USER}:
            return SOURCE_MODE_USER
        if source_types:
            return SOURCE_MODE_REPO

    status = Services.artifact_io().read_json(bootstrap_status_path(paths))
    if isinstance(status, dict):
        mode = status.get("source_mode")
        if mode in {SOURCE_MODE_USER, SOURCE_MODE_REPO}:
            return mode

    return SOURCE_MODE_REPO


def _check_source_map_available(
    source_map_path: Path,
) -> tuple[str, dict[str, Any] | None] | None:
    """Check if source_map_path exists and is non-empty.

    Returns ``(detail, extras)`` on failure, or ``None`` to proceed.
    """
    if not source_map_path.exists() or source_map_path.stat().st_size == 0:
        return (
            "Philosophy source map is missing or empty. Distilled philosophy "
            "cannot be verified as grounded. Section execution will be "
            "blocked until philosophy is available.",
            {},
        )
    return None


def _validate_source_map_content(
    source_map_path: Path,
    philosophy_path: Path,
    paths: PathRegistry,
) -> tuple[str, dict[str, Any] | None, str] | None:
    """Read and validate source map content.

    Checks that the source map is a valid dict, validates its schema,
    verifies principle mapping, and checks for stale sources.

    Returns ``(detail, extras, failure_source_mode)`` on failure, or
    ``None`` if all valid.  When *detail* is empty the caller should
    return ``False`` without writing any signal or status (this covers
    the case where the philosophy file itself is unreadable).
    """
    source_map = Services.artifact_io().read_json(source_map_path)
    if source_map is None:
        Services.logger().log("Intent bootstrap: malformed source map — "
            "preserving as .malformed.json")
        return (
            "Philosophy source map is malformed. Section execution will "
            "be blocked until philosophy is available.",
            {},
            SOURCE_MODE_REPO,
        )
    if not isinstance(source_map, dict):
        return (
            "Philosophy source map is not a JSON object. Section "
            "execution will be blocked until philosophy is available.",
            {},
            SOURCE_MODE_REPO,
        )

    failure_source_mode = _grounding_failure_source_mode(paths, source_map)

    try:
        philosophy_text = philosophy_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ("", None, failure_source_mode)

    principle_ids = declared_principle_ids(philosophy_text)
    if not principle_ids:
        return None

    map_keys = set(source_map.keys())
    unmapped = principle_ids - map_keys
    schema_error = _invalid_source_map_detail(source_map)
    if schema_error is not None:
        return (
            "Philosophy source map has invalid entries "
            f"({schema_error}). Section execution will be blocked "
            "until philosophy is available.",
            {},
            failure_source_mode,
        )
    if unmapped:
        return (
            f"Principle IDs missing from source map: "
            f"{sorted(unmapped)}. Distilled philosophy may contain "
            f"invented principles. Section execution will be blocked.",
            {
                "unmapped_principles": sorted(unmapped),
                "total_principles": len(principle_ids),
                "mapped_principles": len(principle_ids - unmapped),
            },
            failure_source_mode,
        )

    # Verify that each source_file in the map still exists.
    stale_sources = [
        entry.get("source_file", "")
        for entry in source_map.values()
        if isinstance(entry, dict)
        and not Path(entry.get("source_file", "")).exists()
    ]
    if stale_sources:
        return (
            f"Source map references {len(stale_sources)} file(s) "
            f"that no longer exist on disk: {stale_sources[:_MAX_STALE_SOURCES_IN_MESSAGE]}. "
            "Philosophy must be re-distilled from current sources.",
            {"stale_source_files": stale_sources},
            failure_source_mode,
        )

    return None


def validate_philosophy_grounding(
    philosophy_path: Path,
    source_map_path: Path,
    artifacts: Path,
) -> bool:
    """Validate that distilled philosophy is grounded in source files."""
    paths = PathRegistry(artifacts.parent)

    available_failure = _check_source_map_available(source_map_path)
    if available_failure is not None:
        detail, extras = available_failure
        write_bootstrap_signal(
            paths,
            state=BLOCKING_NEEDS_PARENT,
            detail=detail,
            needs=(
                "Repair the philosophy bootstrap artifacts so each principle "
                "is grounded in a valid source map."
            ),
            why_blocked=(
                "The distilled philosophy cannot be trusted until its source "
                "map is valid and complete."
            ),
            extras=extras,
        )
        write_bootstrap_status(
            paths,
            bootstrap_state="failed",
            blocking_state=BLOCKING_NEEDS_PARENT,
            source_mode=SOURCE_MODE_REPO,
            detail=detail,
        )
        return False

    content_failure = _validate_source_map_content(
        source_map_path, philosophy_path, paths,
    )
    if content_failure is not None:
        detail, extras, failure_source_mode = content_failure
        if not detail:
            return False
        write_bootstrap_signal(
            paths,
            state=BLOCKING_NEEDS_PARENT,
            detail=detail,
            needs=(
                "Repair the philosophy bootstrap artifacts so each principle "
                "is grounded in a valid source map."
            ),
            why_blocked=(
                "The distilled philosophy cannot be trusted until its source "
                "map is valid and complete."
            ),
            extras=extras,
        )
        write_bootstrap_status(
            paths,
            bootstrap_state="failed",
            blocking_state=BLOCKING_NEEDS_PARENT,
            source_mode=failure_source_mode,
            detail=detail,
        )
        return False

    return True
