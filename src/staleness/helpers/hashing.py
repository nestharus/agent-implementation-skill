"""HashService: Canonical hashing for content and files.

Foundational service (Tier 1). No domain knowledge, no dependencies
on other project modules. Replaces scattered hashlib.sha256 usage.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def file_hash(path: Path) -> str:
    """SHA-256 hash of a file's contents. Returns empty string if missing."""
    if not path.exists():
        return ""
    try:
        return hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    except OSError:
        return ""


def content_hash(data: str | bytes) -> str:
    """SHA-256 hash of string or bytes content."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def fingerprint(items: list[str]) -> str:
    """SHA-256 hash of sorted, concatenated items.

    Used for computing canonical fingerprints from multiple
    values (e.g., multiple file hashes combined into one token).
    """
    combined = "\n".join(sorted(items))
    return content_hash(combined)
