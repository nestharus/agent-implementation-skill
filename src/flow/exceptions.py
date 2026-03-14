"""Flow system exceptions."""


class FlowCorruptionError(Exception):
    """Raised when a flow artifact is corrupt (malformed JSON)."""
