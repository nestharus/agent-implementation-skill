"""Shared helpers for the log extraction pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

from dispatch.helpers.log_extract_helpers import (
    infer_section,
    parse_timestamp,
    prompt_signature,
    summarize_text,
)
# ------------------------------------------------------------------
# Model / backend map
# ------------------------------------------------------------------

_BACKEND_FAMILIES: dict[str, str] = {
    "claude2": "claude",
    "claude": "claude",
    "codex2": "codex",
    "opencode": "opencode",
    "gemini": "gemini",
}


def load_model_backend_map(planspace: Path) -> dict[str, tuple[str, str]]:
    """Walk upward from *planspace* to find ``.agents/models/`` and parse TOMLs.

    Returns ``{model_name: (backend_cli, source_family)}``.
    """
    import tomllib

    models_dir = _find_models_dir(planspace)
    if models_dir is None:
        return {}

    result: dict[str, tuple[str, str]] = {}
    for toml_path in sorted(models_dir.glob("*.toml")):
        model_name = toml_path.stem
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — best-effort config parsing
            print(f"Warning: skipping malformed config {toml_path}: {exc}", file=sys.stderr)
            continue
        command = data.get("command", "")
        # Extract the actual binary name (last token of the command string)
        backend = command.strip().split()[-1] if command else ""
        family = _BACKEND_FAMILIES.get(backend, "")
        result[model_name] = (backend, family)

    return result


_MAX_PARENT_TRAVERSAL_DEPTH = 20


def _find_models_dir(start: Path) -> Path | None:
    current = start.resolve()
    for _ in range(_MAX_PARENT_TRAVERSAL_DEPTH):
        candidate = current / ".agents" / "models"
        if candidate.is_dir():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None
