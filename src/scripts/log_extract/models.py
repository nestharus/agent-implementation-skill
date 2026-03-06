"""Frozen data contracts for the log extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Source = Literal[
    "run.db", "claude", "codex", "opencode", "gemini", "artifact", "signal",
]

Kind = Literal[
    "lifecycle",
    "summary",
    "signal",
    "message",
    "dispatch",
    "response",
    "tool_call",
    "tool_result",
    "task",
    "gate",
    "artifact",
    "session",
]


@dataclass(slots=True)
class TimelineEvent:
    ts: str
    ts_ms: int
    source: Source
    kind: Kind
    detail: str

    agent: str = ""
    session_id: str = ""
    model: str = ""
    backend: str = ""
    section: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DispatchCandidate:
    dispatch_id: str
    ts: str
    ts_ms: int
    backend: str
    source_family: str

    model: str = ""
    cwd: str = ""
    agent: str = ""
    section: str = ""
    prompt_signature: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionCandidate:
    session_id: str
    ts: str
    ts_ms: int
    backend: str
    source_family: str

    model: str = ""
    cwd: str = ""
    prompt_signature: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CorrelationLink:
    session_id: str
    dispatch_id: str
    score: int
    reasons: list[str]
