"""Reusable helpers shared by log extraction modules."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from staleness.hash_service import content_hash

_SECTION_RE = re.compile(r"(?:section[-_]?)(\d{2})\b|[-_:](\d{2})(?:[-_.:,\s]|$)")


def parse_timestamp(value: str | int | float, *, assume_tz: str = "UTC") -> tuple[str, int]:
    """Normalize a timestamp to ``(iso_str, epoch_ms)``."""
    del assume_tz
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

    try:
        num = float(text)
        return parse_timestamp(num)
    except ValueError:
        pass

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


def prompt_signature(text: str) -> str:
    """Stable hash of prompt text for correlation matching."""
    normalized = " ".join(text.split())[:4000]
    return content_hash(normalized)


def infer_section(*texts: str) -> str:
    """Extract a 2-digit section number from candidate strings."""
    for text in texts:
        if not text:
            continue
        match = _SECTION_RE.search(text)
        if match:
            return match.group(1) or match.group(2)
    return ""


def summarize_text(text: str, limit: int = 160) -> str:
    """One-line summary truncated to *limit* chars."""
    line = " ".join(text.split())
    if len(line) <= limit:
        return line
    return line[: limit - 3] + "..."


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
