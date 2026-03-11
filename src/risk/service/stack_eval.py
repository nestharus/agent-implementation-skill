"""Technical stack evaluation protocol.

Structures design decisions as ROAL packages for comparative evaluation.
Stack choices remain proposals — never governance. Each option gets a
decision-class step, and ROAL scores them through the unified assessment
pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from signals.repository.artifact_io import read_json, write_json
from orchestrator.path_registry import PathRegistry
from risk.service.package_builder import build_decision_package
from risk.types import RiskPackage


@dataclass
class StackOption:
    """A single stack option to evaluate."""
    option_id: str
    summary: str
    decision_class: str = "component"
    prerequisites: list[str] = field(default_factory=list)
    mutation_surface: list[str] = field(default_factory=list)
    verification_surface: list[str] = field(default_factory=list)
    reversibility: str = "medium"
    governance_fit: dict = field(default_factory=dict)
    value_scale_interactions: list[str] = field(default_factory=list)
    execution_notes: list[str] = field(default_factory=list)
    migration_path: str = ""


@dataclass
class StackEvaluation:
    """Result of evaluating stack options for a decision area."""
    decision_area: str
    scope: str
    governing_problem_ids: list[str] = field(default_factory=list)
    governing_constraint_ids: list[str] = field(default_factory=list)
    options: list[StackOption] = field(default_factory=list)
    recommended_option_ids: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    package_id: str = ""
    status: Literal[
        "pending", "assessed", "decided", "blocked"
    ] = "pending"


def create_stack_evaluation(
    decision_area: str,
    scope: str,
    options: list[StackOption],
    problem_ids: list[str] | None = None,
    constraint_ids: list[str] | None = None,
) -> StackEvaluation:
    """Create a new stack evaluation from options."""
    return StackEvaluation(
        decision_area=decision_area,
        scope=scope,
        governing_problem_ids=problem_ids or [],
        governing_constraint_ids=constraint_ids or [],
        options=list(options),
    )


def build_eval_package(
    evaluation: StackEvaluation,
    source: str = "stack-evaluator",
) -> RiskPackage:
    """Convert a stack evaluation into a ROAL decision package.

    Each option becomes a step in the package with its decision class.
    The risk-assessor agent scores the package through normal ROAL.
    """
    option_dicts = [
        {
            "option_id": opt.option_id,
            "summary": opt.summary,
            "decision_class": opt.decision_class,
            "prerequisites": opt.prerequisites,
            "mutation_surface": opt.mutation_surface,
            "verification_surface": opt.verification_surface,
            "reversibility": opt.reversibility,
        }
        for opt in evaluation.options
    ]
    problem_id = (
        evaluation.governing_problem_ids[0]
        if evaluation.governing_problem_ids
        else f"design:{evaluation.decision_area}"
    )
    package = build_decision_package(
        scope=evaluation.scope,
        decision_area=evaluation.decision_area,
        problem_id=problem_id,
        source=source,
        options=option_dicts,
    )
    evaluation.package_id = package.package_id
    return package


def save_stack_evaluation(
    evaluation: StackEvaluation,
    planspace: Path,
) -> Path:
    """Persist a stack evaluation to the risk directory."""
    paths = PathRegistry(planspace)
    path = paths.stack_eval(evaluation.scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, asdict(evaluation))
    return path


def load_stack_evaluation(
    scope: str,
    planspace: Path,
) -> StackEvaluation | None:
    """Load a stack evaluation from the risk directory."""
    paths = PathRegistry(planspace)
    data = read_json(paths.stack_eval(scope))
    if not isinstance(data, dict):
        return None
    options_data = data.pop("options", [])
    options = [StackOption(**o) for o in options_data] if options_data else []
    return StackEvaluation(**data, options=options)
