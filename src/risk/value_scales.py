"""Value scale model with bounded cascade trees."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
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


def save_value_scales(
    scales: list[ValueScale],
    scope: str,
    planspace: Path,
) -> Path:
    """Persist value scales to the risk directory."""
    from signals.artifact_io import write_json
    from orchestrator.path_registry import PathRegistry

    paths = PathRegistry(planspace)
    path = paths.value_scales(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, [asdict(s) for s in scales])
    return path


def load_value_scales(
    scope: str,
    planspace: Path,
) -> list[ValueScale]:
    """Load value scales from the risk directory."""
    from signals.artifact_io import read_json
    from orchestrator.path_registry import PathRegistry

    paths = PathRegistry(planspace)
    data = read_json(paths.value_scales(scope))
    if not isinstance(data, list):
        return []
    scales: list[ValueScale] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        levels_data = item.pop("levels", [])
        levels = []
        for lv in levels_data:
            if isinstance(lv, dict):
                cascades_data = lv.pop("cascades", [])
                cascades = [_deserialize_cascade(c) for c in cascades_data if isinstance(c, dict)]
                levels.append(ValueScaleLevel(**lv, cascades=cascades))
        scales.append(ValueScale(**item, levels=levels))
    return scales


def _deserialize_cascade(data: dict) -> CascadeNode:
    """Recursively deserialize a cascade node."""
    children_data = data.pop("children", [])
    children = [
        _deserialize_cascade(c) for c in children_data if isinstance(c, dict)
    ]
    return CascadeNode(**data, children=children)
