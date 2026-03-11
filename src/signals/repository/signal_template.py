"""Standardized signal template for agent output classification."""

from typing import Any


def create_signal_template(section: str, state: str, detail: str = "",
                           **extra: Any) -> dict[str, Any]:
    """Create a standardized signal dict for agent output.

    Agents should include this JSON in their output for reliable
    state classification. Scripts read JSON — agents decide semantics.
    """
    signal: dict[str, Any] = {
        "state": state,
        "section": section,
        "detail": detail,
    }
    signal.update(extra)
    return signal
