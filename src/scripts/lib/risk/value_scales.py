"""Value scale model with bounded cascade trees."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CascadeNode:
    """A single node in a cost cascade tree, bounded to depth 2-3."""
    effect_id: str
    description: str
    severity: int = 0  # 0-4
    children: list[CascadeNode] = field(default_factory=list)


@dataclass
class ValueScaleLevel:
    """One level on a value scale ladder."""
    value_id: str
    level: int
    label: str
    intended_outcomes: list[str] = field(default_factory=list)
    direct_costs: list[str] = field(default_factory=list)
    cascades: list[CascadeNode] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    reassessment_triggers: list[str] = field(default_factory=list)


@dataclass
class ValueScale:
    """A complete value scale with agent-enumerated levels."""
    value_id: str
    scope: str = "global"
    levels: list[ValueScaleLevel] = field(default_factory=list)
    suggested_level: int | None = None
    suggested_rationale: str = ""
    selected_level: int | None = None
    selected_state: Literal["candidate", "verified"] = "candidate"
