"""Pydantic models and constants for structured agent signals."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

# ── signal / blocking state constants ────────────────────────────────

SIGNAL_NEEDS_PARENT = "needs_parent"
SIGNAL_NEED_DECISION = "need_decision"
SIGNAL_OUT_OF_SCOPE = "out_of_scope"
BLOCKING_NEEDS_PARENT = "NEEDS_PARENT"
BLOCKING_NEED_DECISION = "NEED_DECISION"


class AgentSignal(BaseModel):
    """Structured signal written by agents to communicate state.

    Base fields cover the escalation protocol; agents may write
    additional fields (intent_mode, budgets, acknowledged, etc.)
    which land in ``model_extra`` and are accessible as attributes.

    Implements the ``Mapping`` protocol so existing code that does
    ``isinstance(signal, dict)`` guards, ``signal[key] = value``,
    or ``json.dumps(signal)`` continues to work during migration.
    """

    model_config = ConfigDict(extra="allow")

    state: str = ""
    detail: str = ""
    needs: str = ""
    assumptions_refused: str = ""
    suggested_escalation_target: str = ""

    # ------------------------------------------------------------------
    # dict-compatible helpers so callers that previously received a raw
    # dict can migrate incrementally.
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Dict-style access for backward compatibility."""
        try:
            return getattr(self, key)
        except AttributeError:
            return default

    def __getitem__(self, key: str) -> Any:  # noqa: ANN401
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key) from None

    def __setitem__(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Dict-style mutation for backward compatibility."""
        if key in type(self).model_fields:
            object.__setattr__(self, key, value)
        else:
            # Store in model_extra
            if self.model_extra is None:
                object.__setattr__(self, "__pydantic_extra__", {})
            self.model_extra[key] = value

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def setdefault(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Dict-style setdefault for backward compatibility."""
        try:
            return getattr(self, key)
        except AttributeError:
            self[key] = default
            return default

    def keys(self) -> list[str]:
        """Return all field names including extras."""
        result = list(type(self).model_fields)
        if self.model_extra:
            result.extend(self.model_extra)
        return result

    def __iter__(self) -> Iterator[str]:
        yield from type(self).model_fields
        if self.model_extra:
            yield from self.model_extra

    def __len__(self) -> int:
        n = len(type(self).model_fields)
        if self.model_extra:
            n += len(self.model_extra)
        return n


# Register AgentSignal as a virtual subclass of Mapping so that
# isinstance(signal, Mapping) returns True. This does NOT make
# isinstance(signal, dict) True, but covers the ABC checks.
Mapping.register(AgentSignal)


@dataclass(frozen=True)
class SignalResult:
    """Result of reading a structured signal file.

    Supports tuple destructuring for backward compatibility::

        sig, detail = read_signal_tuple(path)
        # sig is signal_type, detail is detail
    """

    signal_type: str | None  # None means no signal file found
    detail: str = ""

    def __iter__(self):
        return iter((self.signal_type, self.detail))
