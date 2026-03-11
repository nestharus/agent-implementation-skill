"""Scenario discovery and filtering for agentic eval fixtures."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SeedSpec:
    init_db: bool
    planspace_files: list[dict]
    codespace_files: list[dict]
    sql: list[str]
    pre_signals: list[dict]


@dataclass
class TriggerStep:
    adapter: str
    kwargs: dict


@dataclass
class TriggerSpec:
    kind: str
    steps: list[TriggerStep]


@dataclass
class StructuralCheck:
    type: str
    required: bool
    path: str | None = None
    keys: list[str] | None = None
    query: str | None = None
    where: dict | None = None
    min: int | None = None
    heading: str | None = None
    pattern: str | None = None
    allowed_states: list[str] | None = None


@dataclass
class SemanticCheck:
    id: str
    assertion: str


@dataclass
class AbsenceCheck:
    id: str
    path_glob: str
    should_exist: bool


@dataclass
class SignalCheck:
    id: str
    path: str
    expected_state: str
    required_fields: list[str]


@dataclass
class ChecksSpec:
    structural: list[StructuralCheck]
    semantic: list[SemanticCheck]
    absence: list[AbsenceCheck]
    signals: list[SignalCheck]


@dataclass
class CollectSpec:
    file_globs: list[str]
    db_queries: list[dict]


@dataclass
class ScenarioSpec:
    id: str
    name: str
    category: str
    system: str
    cost_tier: str
    wave: int
    tags: list[str]
    seed: SeedSpec
    trigger: TriggerSpec
    collect: CollectSpec
    checks: ChecksSpec
    fixture_dir: Path = field(repr=False, compare=False)


def _default_list(data: object) -> list:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    raise ValueError(f"Expected list, got {type(data).__name__}")


def _default_dict(data: object) -> dict:
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    raise ValueError(f"Expected dict, got {type(data).__name__}")


def _parse_seed(data: dict) -> SeedSpec:
    return SeedSpec(
        init_db=bool(data.get("init_db", False)),
        planspace_files=_default_list(data.get("planspace_files")),
        codespace_files=_default_list(data.get("codespace_files")),
        sql=[str(item) for item in _default_list(data.get("sql"))],
        pre_signals=_default_list(data.get("pre_signals")),
    )


def _parse_trigger(data: dict) -> TriggerSpec:
    steps: list[TriggerStep] = []
    for raw in _default_list(data.get("steps")):
        step = _default_dict(raw)
        steps.append(
            TriggerStep(
                adapter=str(step["adapter"]),
                kwargs=_default_dict(step.get("kwargs")),
            )
        )
    return TriggerSpec(kind=str(data["kind"]), steps=steps)


def _parse_checks(data: dict) -> ChecksSpec:
    structural = [
        StructuralCheck(
            type=str(item["type"]),
            required=bool(item.get("required", True)),
            path=item.get("path"),
            keys=list(item["keys"]) if item.get("keys") is not None else None,
            query=item.get("query"),
            where=_default_dict(item.get("where")),
            min=int(item["min"]) if item.get("min") is not None else None,
            heading=item.get("heading"),
            pattern=item.get("pattern"),
            allowed_states=(
                list(item["allowed_states"])
                if item.get("allowed_states") is not None
                else None
            ),
        )
        for item in _default_list(data.get("structural"))
    ]
    semantic = [
        SemanticCheck(id=str(item["id"]), assertion=str(item["assertion"]))
        for item in _default_list(data.get("semantic"))
    ]
    absence = [
        AbsenceCheck(
            id=str(item["id"]),
            path_glob=str(item["path_glob"]),
            should_exist=bool(item["should_exist"]),
        )
        for item in _default_list(data.get("absence"))
    ]
    signals = [
        SignalCheck(
            id=str(item["id"]),
            path=str(item["path"]),
            expected_state=str(item["expected_state"]),
            required_fields=[str(field) for field in item.get("required_fields", [])],
        )
        for item in _default_list(data.get("signals"))
    ]
    return ChecksSpec(
        structural=structural,
        semantic=semantic,
        absence=absence,
        signals=signals,
    )


def _parse_collect(data: dict) -> CollectSpec:
    db_queries = []
    for item in _default_list(data.get("db_queries")):
        query = _default_dict(item)
        db_queries.append({"name": str(query["name"]), "sql": str(query["sql"])})
    return CollectSpec(
        file_globs=[str(item) for item in _default_list(data.get("file_globs"))],
        db_queries=db_queries,
    )


def _parse_scenario(path: Path) -> ScenarioSpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Scenario at {path} must be a mapping")

    return ScenarioSpec(
        id=str(raw["id"]),
        name=str(raw["name"]),
        category=str(raw["category"]),
        system=str(raw["system"]),
        cost_tier=str(raw["cost_tier"]),
        wave=int(raw["wave"]),
        tags=[str(tag) for tag in raw.get("tags", [])],
        seed=_parse_seed(_default_dict(raw.get("seed"))),
        trigger=_parse_trigger(_default_dict(raw.get("trigger"))),
        collect=_parse_collect(_default_dict(raw.get("collect"))),
        checks=_parse_checks(_default_dict(raw.get("checks"))),
        fixture_dir=path.parent,
    )


def discover_scenarios(fixtures_root: Path) -> list[ScenarioSpec]:
    """Discover and parse all scenario fixtures under ``fixtures_root``."""
    specs: list[ScenarioSpec] = []
    for scenario_path in sorted(fixtures_root.glob("*/scenario.yaml")):
        specs.append(_parse_scenario(scenario_path))
    return specs


def filter_scenarios(
    specs: list[ScenarioSpec],
    *,
    scenario_id: str | None = None,
    category: str | None = None,
    wave: int | None = None,
    cost_tier: str | None = None,
    tags: list[str] | None = None,
) -> list[ScenarioSpec]:
    """Filter scenarios by common CLI selectors."""
    selected = specs
    if scenario_id is not None:
        selected = [spec for spec in selected if spec.id == scenario_id]
    if category is not None:
        selected = [spec for spec in selected if spec.category == category]
    if wave is not None:
        selected = [spec for spec in selected if spec.wave == wave]
    if cost_tier is not None:
        selected = [spec for spec in selected if spec.cost_tier == cost_tier]
    if tags:
        tag_set = set(tags)
        selected = [spec for spec in selected if tag_set.issubset(set(spec.tags))]
    return selected


def load_scenarios(
    project_root: Path,
    *,
    scenario_id: str | None = None,
    category: str | None = None,
    wave: int | None = None,
    cost_tier: str | None = None,
    tags: list[str] | None = None,
) -> list[ScenarioSpec]:
    """Discover scenarios and apply optional filters."""
    fixtures_root = project_root / "evals" / "agentic" / "fixtures"
    specs = discover_scenarios(fixtures_root)
    return filter_scenarios(
        specs,
        scenario_id=scenario_id,
        category=category,
        wave=wave,
        cost_tier=cost_tier,
        tags=tags,
    )
