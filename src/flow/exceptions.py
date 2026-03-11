"""Flow system exceptions."""

from __future__ import annotations


class FlowCorruptionError(Exception):
    """Raised when a flow artifact is corrupt (malformed JSON)."""
