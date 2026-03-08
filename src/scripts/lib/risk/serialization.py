"""JSON serialization helpers for ROAL risk artifacts."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import logging
from pathlib import Path
from typing import Any, Callable, TypeVar, cast

from lib.core.artifact_io import read_json, rename_malformed, write_json

from .types import (
    PackageStep,
    PostureProfile,
    RiskAssessment,
    RiskHistoryEntry,
    RiskModifiers,
    RiskPackage,
    RiskPlan,
    RiskType,
    RiskVector,
    StepAssessment,
    StepClass,
    StepDecision,
    StepMitigation,
    UnderstandingInventory,
)

logger = logging.getLogger(__name__)
_ArtifactT = TypeVar("_ArtifactT")


def serialize_assessment(assessment: RiskAssessment) -> dict[str, Any]:
    return _serialize_dataclass(assessment)


def deserialize_assessment(data: dict[str, Any]) -> RiskAssessment:
    return RiskAssessment(
        assessment_id=data["assessment_id"],
        layer=data["layer"],
        package_id=data["package_id"],
        assessment_scope=data["assessment_scope"],
        understanding_inventory=_deserialize_understanding_inventory(
            data["understanding_inventory"]
        ),
        package_raw_risk=data["package_raw_risk"],
        assessment_confidence=data["assessment_confidence"],
        dominant_risks=_deserialize_risk_types(data["dominant_risks"]),
        step_assessments=[
            _deserialize_step_assessment(item)
            for item in data["step_assessments"]
        ],
        frontier_candidates=list(data["frontier_candidates"]),
        reopen_recommendations=list(data.get("reopen_recommendations", [])),
        notes=list(data.get("notes", [])),
    )


def serialize_plan(plan: RiskPlan) -> dict[str, Any]:
    return _serialize_dataclass(plan)


def deserialize_plan(data: dict[str, Any]) -> RiskPlan:
    return RiskPlan(
        plan_id=data["plan_id"],
        assessment_id=data["assessment_id"],
        package_id=data["package_id"],
        layer=data["layer"],
        step_decisions=[
            _deserialize_step_mitigation(item)
            for item in data["step_decisions"]
        ],
        accepted_frontier=list(data["accepted_frontier"]),
        deferred_steps=list(data["deferred_steps"]),
        reopen_steps=list(data["reopen_steps"]),
        expected_reassessment_inputs=list(
            data.get("expected_reassessment_inputs", [])
        ),
    )


def serialize_package(package: RiskPackage) -> dict[str, Any]:
    return _serialize_dataclass(package)


def deserialize_package(data: dict[str, Any]) -> RiskPackage:
    return RiskPackage(
        package_id=data["package_id"],
        layer=data["layer"],
        scope=data["scope"],
        origin_problem_id=data["origin_problem_id"],
        origin_source=data["origin_source"],
        steps=[_deserialize_package_step(item) for item in data["steps"]],
    )


def serialize_history_entry(entry: RiskHistoryEntry) -> dict[str, Any]:
    return _serialize_dataclass(entry)


def deserialize_history_entry(data: dict[str, Any]) -> RiskHistoryEntry:
    return RiskHistoryEntry(
        package_id=data["package_id"],
        step_id=data["step_id"],
        layer=data["layer"],
        step_class=StepClass(data["step_class"]),
        posture=PostureProfile(data["posture"]),
        predicted_risk=data["predicted_risk"],
        actual_outcome=data["actual_outcome"],
        surfaced_surprises=list(data.get("surfaced_surprises", [])),
        verification_outcome=data.get("verification_outcome"),
        dominant_risks=_deserialize_risk_types(data.get("dominant_risks", [])),
        blast_radius_band=data.get("blast_radius_band", 0),
    )


def write_risk_artifact(path: Path, data: dict[str, Any]) -> None:
    write_json(path, data)


def read_risk_artifact(path: Path) -> dict[str, Any] | None:
    data = read_json(path)
    if isinstance(data, dict):
        return data
    return None


def load_risk_package(path: Path) -> RiskPackage | None:
    return _load_risk_artifact(path, deserialize_package, "risk package")


def load_risk_assessment(path: Path) -> RiskAssessment | None:
    return _load_risk_artifact(path, deserialize_assessment, "risk assessment")


def load_risk_plan(path: Path) -> RiskPlan | None:
    return _load_risk_artifact(path, deserialize_plan, "risk plan")


def _load_risk_artifact(
    path: Path,
    loader: Callable[[dict[str, Any]], _ArtifactT],
    artifact_name: str,
) -> _ArtifactT | None:
    data = read_json(path)
    if data is None:
        return None
    try:
        return loader(cast(dict[str, Any], data))
    except (KeyError, TypeError, ValueError) as exc:
        rename_malformed(path)
        logger.warning("Malformed %s at %s: %s", artifact_name, path, exc)
        return None


def _serialize_dataclass(value: Any) -> dict[str, Any]:
    return _serialize_value(asdict(value))


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _serialize_dataclass(value)
    if isinstance(value, dict):
        return {
            str(key): _serialize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


def _deserialize_package_step(data: dict[str, Any]) -> PackageStep:
    return PackageStep(
        step_id=data["step_id"],
        step_class=StepClass(data["step_class"]),
        summary=data["summary"],
        prerequisites=list(data.get("prerequisites", [])),
        expected_outputs=list(data.get("expected_outputs", [])),
        expected_resolutions=list(data.get("expected_resolutions", [])),
        mutation_surface=list(data.get("mutation_surface", [])),
        verification_surface=list(data.get("verification_surface", [])),
        reversibility=data.get("reversibility", "medium"),
    )


def _deserialize_step_assessment(data: dict[str, Any]) -> StepAssessment:
    return StepAssessment(
        step_id=data["step_id"],
        step_class=StepClass(data["step_class"]),
        summary=data["summary"],
        prerequisites=list(data["prerequisites"]),
        risk_vector=_deserialize_risk_vector(data["risk_vector"]),
        modifiers=_deserialize_risk_modifiers(data["modifiers"]),
        raw_risk=data["raw_risk"],
        dominant_risks=_deserialize_risk_types(data["dominant_risks"]),
    )


def _deserialize_step_mitigation(data: dict[str, Any]) -> StepMitigation:
    posture = data.get("posture")
    return StepMitigation(
        step_id=data["step_id"],
        decision=StepDecision(data["decision"]),
        posture=PostureProfile(posture) if posture is not None else None,
        mitigations=list(data.get("mitigations", [])),
        residual_risk=data.get("residual_risk"),
        reason=data.get("reason"),
        wait_for=list(data.get("wait_for", [])),
        route_to=data.get("route_to"),
        dispatch_shape=data.get("dispatch_shape"),
    )


def _deserialize_risk_vector(data: dict[str, Any]) -> RiskVector:
    return RiskVector(
        context_rot=data.get("context_rot", 0),
        silent_drift=data.get("silent_drift", 0),
        scope_creep=data.get("scope_creep", 0),
        brute_force_regression=data.get("brute_force_regression", 0),
        cross_section_incoherence=data.get("cross_section_incoherence", 0),
        tool_island_isolation=data.get("tool_island_isolation", 0),
        stale_artifact_contamination=data.get("stale_artifact_contamination", 0),
    )


def _deserialize_risk_modifiers(data: dict[str, Any]) -> RiskModifiers:
    return RiskModifiers(
        blast_radius=data.get("blast_radius", 0),
        reversibility=data.get("reversibility", 4),
        observability=data.get("observability", 4),
        confidence=data.get("confidence", 0.5),
    )


def _deserialize_understanding_inventory(
    data: dict[str, Any],
) -> UnderstandingInventory:
    return UnderstandingInventory(
        confirmed=list(data.get("confirmed", [])),
        assumed=list(data.get("assumed", [])),
        missing=list(data.get("missing", [])),
        stale=list(data.get("stale", [])),
    )


def _deserialize_risk_types(values: list[str]) -> list[RiskType]:
    return [RiskType(value) for value in values]
