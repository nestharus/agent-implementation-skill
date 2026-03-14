"""Philosophy signal classifiers.

Functions that classify JSON signal artifacts into semantic states
(missing / malformed / valid_empty / valid_nonempty) used by the
bootstrap pipeline to decide the next action.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from containers import Services

# ── Valid source types (shared with bootstrap) ────────────────────────
VALID_SOURCE_TYPES = frozenset({"repo_source", "user_source"})

# ── Source modes ──────────────────────────────────────────────────────
SOURCE_MODE_USER = "user_source"
SOURCE_MODE_REPO = "repo_sources"
SOURCE_MODE_NONE = "none"


# ── Classification states ────────────────────────────────────────────
class ClassifierState(str, Enum):
    """Semantic state of a philosophy signal classification."""

    VALID_NONEMPTY = "valid_nonempty"
    VALID_EMPTY = "valid_empty"
    MALFORMED_SIGNAL = "malformed_signal"
    MISSING_SIGNAL = "missing_signal"

    def __str__(self) -> str:  # noqa: D105
        return self.value


# Backward-compatible aliases
STATE_VALID_NONEMPTY = ClassifierState.VALID_NONEMPTY
STATE_VALID_EMPTY = ClassifierState.VALID_EMPTY

# ── Minimum byte threshold for user-provided philosophy source ────────
MIN_USER_SOURCE_BYTES = 100


# ── low-level malformed helpers ───────────────────────────────────────

def _preserve_malformed_signal(signal_path: Path) -> str | None:
    malformed_path = Services.artifact_io().rename_malformed(signal_path)
    if malformed_path is None:
        return None
    return str(malformed_path)


def _malformed_signal_result(
    signal_path: Path,
    *,
    data: Any = None,
    preserve_existing: bool = False,
) -> dict[str, Any]:
    preserved_path: str | None = None
    if preserve_existing:
        preserved_path = _preserve_malformed_signal(signal_path)
    else:
        candidate = signal_path.with_suffix(".malformed.json")
        if candidate.exists():
            preserved_path = str(candidate)
    return {
        "state": ClassifierState.MALFORMED_SIGNAL,
        "data": data,
        "preserved": preserved_path,
    }


# ── generic list-signal classifier ────────────────────────────────────

def _classify_list_signal_result(
    signal_path: Path,
    *,
    list_field: str,
    required_fields: tuple[str, ...] = (),
    require_status: bool = False,
    empty_status: str = "empty",
    nonempty_status: str = "selected",
) -> dict[str, Any]:
    """Classify a JSON signal artifact into missing/malformed/empty/non-empty."""
    if not signal_path.exists():
        return {"state": ClassifierState.MISSING_SIGNAL, "data": None}

    data = Services.artifact_io().read_json(signal_path)
    if data is None:
        return _malformed_signal_result(signal_path)
    if not isinstance(data, dict):
        return _malformed_signal_result(
            signal_path,
            data=data,
            preserve_existing=True,
        )

    if require_status:
        status = data.get("status")
        if not isinstance(status, str):
            return _malformed_signal_result(
                signal_path,
                data=data,
                preserve_existing=True,
            )
        normalized = status.strip().lower()
        if normalized not in {empty_status, nonempty_status}:
            return _malformed_signal_result(
                signal_path,
                data=data,
                preserve_existing=True,
            )
    else:
        normalized = None

    for field_name in required_fields + (list_field,):
        if field_name not in data:
            return _malformed_signal_result(
                signal_path,
                data=data,
                preserve_existing=True,
            )
        if not isinstance(data[field_name], list):
            return _malformed_signal_result(
                signal_path,
                data=data,
                preserve_existing=True,
            )

    items = data[list_field]
    if require_status:
        if normalized == empty_status and items:
            return _malformed_signal_result(
                signal_path,
                data=data,
                preserve_existing=True,
            )
        if normalized == nonempty_status and not items:
            return _malformed_signal_result(
                signal_path,
                data=data,
                preserve_existing=True,
            )

    if not items:
        return {"state": STATE_VALID_EMPTY, "data": data}
    return {"state": STATE_VALID_NONEMPTY, "data": data}


# ── specialised classifiers ───────────────────────────────────────────

def _classify_selector_result(signal_path: Path) -> dict[str, Any]:
    """Classify selector signal into one of 4 states."""
    return _classify_list_signal_result(
        signal_path,
        list_field="sources",
        require_status=True,
    )


def _classify_verifier_result(signal_path: Path) -> dict[str, Any]:
    """Classify verifier signal into one of 4 states."""
    return _classify_list_signal_result(
        signal_path,
        list_field="verified_sources",
        required_fields=("rejected",),
    )


def _invalid_source_map_detail(source_map: dict[str, Any]) -> str | None:
    """Return a schema error for source_map, or None when valid."""
    for principle_id, entry in source_map.items():
        if not isinstance(principle_id, str) or not principle_id.startswith("P"):
            return "source map keys must be principle IDs like P1"
        if not isinstance(entry, dict):
            return f"{principle_id} must map to an object"
        source_type = entry.get("source_type")
        source_file = entry.get("source_file")
        source_section = entry.get("source_section")
        if source_type not in VALID_SOURCE_TYPES:
            allowed = ", ".join(sorted(VALID_SOURCE_TYPES))
            return (
                f"{principle_id}.source_type must be one of: {allowed}"
            )
        if not isinstance(source_file, str) or not source_file.strip():
            return f"{principle_id}.source_file must be a non-empty string"
        if not isinstance(source_section, str) or not source_section.strip():
            return (
                f"{principle_id}.source_section must be a non-empty string"
            )
    return None


def _classify_distiller_result(
    philosophy_path: Path,
    source_map_path: Path,
) -> dict[str, Any]:
    """Classify distiller outputs into missing/malformed/empty/non-empty."""
    if not philosophy_path.exists() or not source_map_path.exists():
        return {"state": ClassifierState.MISSING_SIGNAL, "data": None}

    try:
        philosophy_text = philosophy_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"state": ClassifierState.MALFORMED_SIGNAL, "data": None}

    if not philosophy_text.strip():
        source_map = Services.artifact_io().read_json(source_map_path)
        if source_map is None:
            return _malformed_signal_result(source_map_path)
        if not isinstance(source_map, dict):
            return _malformed_signal_result(
                source_map_path,
                data=source_map,
                preserve_existing=True,
            )
        if source_map:
            return _malformed_signal_result(
                source_map_path,
                data=source_map,
                preserve_existing=True,
            )
        return {
            "state": STATE_VALID_EMPTY,
            "data": {
                "philosophy_path": str(philosophy_path),
                "source_map_path": str(source_map_path),
            },
        }

    source_map = Services.artifact_io().read_json(source_map_path)
    if source_map is None:
        return _malformed_signal_result(source_map_path)
    if not isinstance(source_map, dict):
        return _malformed_signal_result(
            source_map_path,
            data=source_map,
            preserve_existing=True,
        )
    if not source_map:
        return _malformed_signal_result(
            source_map_path,
            data=source_map,
            preserve_existing=True,
        )
    schema_error = _invalid_source_map_detail(source_map)
    if schema_error is not None:
        return _malformed_signal_result(
            source_map_path,
            data={"schema_error": schema_error, "source_map": source_map},
            preserve_existing=True,
        )
    return {
        "state": STATE_VALID_NONEMPTY,
        "data": {
            "philosophy_path": str(philosophy_path),
            "source_map_path": str(source_map_path),
            "source_map": source_map,
        },
    }


def _guidance_schema_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "guidance must be a JSON object"
    project_frame = payload.get("project_frame")
    prompts = payload.get("prompts")
    notes = payload.get("notes")
    if not isinstance(project_frame, str) or not project_frame.strip():
        return "project_frame must be a non-empty string"
    if not isinstance(prompts, list):
        return "prompts must be a list"
    for index, entry in enumerate(prompts, start=1):
        if not isinstance(entry, dict):
            return f"prompts[{index}] must be an object"
        prompt = entry.get("prompt")
        why = entry.get("why_this_matters")
        if not isinstance(prompt, str) or not prompt.strip():
            return f"prompts[{index}].prompt must be a non-empty string"
        if not isinstance(why, str) or not why.strip():
            return (
                f"prompts[{index}].why_this_matters must be a non-empty string"
            )
    if not isinstance(notes, list):
        return "notes must be a list"
    for index, note in enumerate(notes, start=1):
        if not isinstance(note, str) or not note.strip():
            return f"notes[{index}] must be a non-empty string"
    return None


def _classify_guidance_result(guidance_path: Path) -> dict[str, Any]:
    if not guidance_path.exists():
        return {"state": ClassifierState.MISSING_SIGNAL, "data": None}
    data = Services.artifact_io().read_json(guidance_path)
    if data is None:
        return _malformed_signal_result(guidance_path)
    schema_error = _guidance_schema_error(data)
    if schema_error is not None:
        return _malformed_signal_result(
            guidance_path,
            data={"schema_error": schema_error, "guidance": data},
            preserve_existing=True,
        )
    return {"state": STATE_VALID_NONEMPTY, "data": data}


def _user_source_is_substantive(user_source: Path) -> bool:
    return (
        user_source.exists()
        and user_source.is_file()
        and user_source.stat().st_size > MIN_USER_SOURCE_BYTES
    )


def _manifest_source_mode(manifest: dict[str, Any] | None) -> str:
    if not isinstance(manifest, dict):
        return SOURCE_MODE_REPO
    source_types = {
        entry.get("source_type", "repo_source")
        for entry in manifest.get("sources", [])
        if isinstance(entry, dict)
    }
    if source_types == {SOURCE_MODE_USER}:
        return SOURCE_MODE_USER
    if not source_types:
        return SOURCE_MODE_REPO
    return SOURCE_MODE_REPO
