"""Shared helpers for the log extraction pipeline."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from lib.hash_service import content_hash

_SECTION_RE = re.compile(r"(?:section[-_]?)(\d{2})\b|[-_:](\d{2})(?:[-_.:,\s]|$)")


def parse_timestamp(value: str | int | float, *, assume_tz: str = "UTC") -> tuple[str, int]:
    """Normalize a timestamp to ``(iso_str, epoch_ms)``.

    Accepts ISO 8601 (with or without ``Z``/offset), Unix seconds, and
    Unix milliseconds.
    """
    if isinstance(value, (int, float)):
        if value > 1e12:
            ms = int(value)
        else:
            ms = int(value * 1000)
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return _fmt(dt), ms

    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")

    # Try numeric string
    try:
        num = float(text)
        return parse_timestamp(num, assume_tz=assume_tz)
    except ValueError:
        pass

    # ISO 8601 parsing
    # Replace trailing Z with +00:00 for fromisoformat
    normalized = text
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"cannot parse timestamp: {value!r}") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    ms = int(dt.timestamp() * 1000)
    return _fmt(dt), ms


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def prompt_signature(text: str) -> str:
    """Stable hash of prompt text for correlation matching."""
    normalized = " ".join(text.split())[:4000]
    return content_hash(normalized)


def infer_section(*texts: str) -> str:
    """Extract a 2-digit section number from candidate strings."""
    for t in texts:
        if not t:
            continue
        m = _SECTION_RE.search(t)
        if m:
            return m.group(1) or m.group(2)
    return ""


def summarize_text(text: str, limit: int = 160) -> str:
    """One-line summary truncated to *limit* chars."""
    line = " ".join(text.split())
    if len(line) <= limit:
        return line
    return line[: limit - 3] + "..."


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
        except Exception:
            continue
        command = data.get("command", "")
        # Extract the actual binary name (last token of the command string)
        backend = command.strip().split()[-1] if command else ""
        family = _BACKEND_FAMILIES.get(backend, "")
        result[model_name] = (backend, family)

    return result


def _find_models_dir(start: Path) -> Path | None:
    current = start.resolve()
    for _ in range(20):
        candidate = current / ".agents" / "models"
        if candidate.is_dir():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None
