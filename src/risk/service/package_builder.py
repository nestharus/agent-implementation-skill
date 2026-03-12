"""Risk package construction and persistence helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from signals.repository.artifact_io import read_json
from orchestrator.path_registry import PathRegistry
from proposal.repository.state import load_proposal_state
from risk.repository.serialization import (
    load_risk_package,
    serialize_package,
    write_risk_artifact,
)
from risk.types import AssessmentClass, DecisionClass, PackageStep, RiskPackage, StepClass

_MICROSTRATEGY_LINE_RE = re.compile(r"^(?:[-*+]|\d+\.)\s+(.+)$")
_MICROSTRATEGY_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")
_MICROSTRATEGY_JSON_BLOCK_RE = re.compile(
    r"```json\s*(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def build_package(
    scope: str,
    layer: str,
    problem_id: str,
    source: str,
    steps: list[PackageStep],
) -> RiskPackage:
    """Create a new risk package from explicit step definitions."""
    return RiskPackage(
        package_id=_package_id(scope, layer),
        layer=layer,
        scope=scope,
        origin_problem_id=problem_id,
        origin_source=source,
        steps=list(steps),
    )


def build_package_from_proposal(
    scope: str,
    planspace: Path,
) -> RiskPackage:
    """Build a package from proposal-state and microstrategy artifacts."""
    paths = PathRegistry(planspace)
    section_number = scope_number(scope)
    proposal_excerpt_path = paths.proposal_excerpt(section_number)
    microstrategy_path = paths.microstrategy(section_number)
    problem_frame_path = paths.problem_frame(section_number)
    proposal_state_path = (
        paths.proposals_dir() / f"{scope}-proposal-state.json"
    )
    readiness_path = (
        paths.readiness_dir() / f"{scope}-execution-ready.json"
    )

    proposal_excerpt = read_text(proposal_excerpt_path)
    problem_frame = read_text(problem_frame_path)
    microstrategy = read_text(microstrategy_path)
    proposal_state = load_proposal_state(proposal_state_path)
    readiness = read_json(readiness_path)

    microstrategy_steps = (
        _extract_microstrategy_steps(microstrategy) if microstrategy else []
    )
    step_summaries = [
        _microstrategy_summary(step)
        for step in microstrategy_steps
        if _microstrategy_summary(step)
    ]
    assessment_classes = {}
    if microstrategy_steps:
        for i, step in enumerate(microstrategy_steps, start=1):
            if isinstance(step, dict) and "assessment_class" in step:
                assessment_classes[i] = step["assessment_class"]

    if not step_summaries:
        step_summaries = _default_step_summaries(
            proposal_excerpt=proposal_excerpt,
            problem_frame=problem_frame,
            readiness=readiness if isinstance(readiness, dict) else None,
        )

    steps = _materialize_steps(
        step_summaries=step_summaries,
        proposal_state=proposal_state,
        assessment_classes=assessment_classes or None,
    )
    return build_package(
        scope=scope,
        layer="implementation",
        problem_id=f"{scope}:proposal",
        source="proposal",
        steps=steps,
    )


def refresh_package(
    existing: RiskPackage,
    completed_steps: list[str],
    new_evidence: dict,
) -> RiskPackage:
    """Refresh a package after accepted steps complete."""
    completed = set(completed_steps)
    refreshed_steps = [
        PackageStep(
            step_id=step.step_id,
            assessment_class=step.assessment_class,
            summary=step.summary,
            prerequisites=[
                prerequisite
                for prerequisite in step.prerequisites
                if prerequisite not in completed
            ],
            expected_outputs=list(step.expected_outputs),
            expected_resolutions=list(step.expected_resolutions),
            mutation_surface=list(step.mutation_surface),
            verification_surface=list(step.verification_surface),
            reversibility=step.reversibility,
        )
        for step in existing.steps
        if step.step_id not in completed
    ]

    for extra in new_evidence.get("new_steps", []):
        parsed = _coerce_package_step(extra)
        if parsed is not None:
            refreshed_steps.append(parsed)

    return RiskPackage(
        package_id=existing.package_id,
        layer=existing.layer,
        scope=existing.scope,
        origin_problem_id=existing.origin_problem_id,
        origin_source=existing.origin_source,
        steps=refreshed_steps,
    )


def write_package(paths: PathRegistry, package: RiskPackage) -> Path:
    """Persist a package to the risk directory."""
    path = paths.risk_package(package.scope)
    write_risk_artifact(path, serialize_package(package))
    return path


def read_package(paths: PathRegistry, scope: str) -> RiskPackage | None:
    """Read an existing package from the risk directory."""
    return load_risk_package(paths.risk_package(scope))


def _package_id(scope: str, layer: str) -> str:
    return f"pkg-{layer}-{scope}"


def scope_number(scope: str) -> str:
    match = re.search(r"section-(\d+)", scope)
    if match is not None:
        return match.group(1)
    return scope


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _extract_microstrategy_steps(text: str) -> list[str | dict[str, str]]:
    steps: list[str | dict[str, str]] = []
    seen_summaries: set[str] = set()

    for block_match in _MICROSTRATEGY_JSON_BLOCK_RE.finditer(text):
        payload = _parse_microstrategy_json(block_match.group(1))
        if payload is None:
            continue
        for item in _normalize_microstrategy_payload(payload):
            summary = _microstrategy_summary(item)
            if not summary or summary in seen_summaries:
                continue
            seen_summaries.add(summary)
            steps.append(item)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _MICROSTRATEGY_LINE_RE.match(line) or _MICROSTRATEGY_HEADING_RE.match(
            line
        )
        if match is None:
            continue
        candidate = match.group(1).strip()
        if not candidate:
            continue
        structured = _parse_microstrategy_json(candidate)
        item: str | dict[str, str]
        if structured is None:
            item = candidate
        else:
            normalized = _normalize_microstrategy_payload(structured)
            if len(normalized) != 1:
                continue
            item = normalized[0]
        summary = _microstrategy_summary(item)
        if summary and summary not in seen_summaries:
            seen_summaries.add(summary)
            steps.append(item)
    return steps


def _parse_microstrategy_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _normalize_microstrategy_payload(
    payload: object,
) -> list[str | dict[str, str]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("steps"), list):
        items = payload["steps"]
    else:
        items = [payload]

    normalized: list[str | dict[str, str]] = []
    for item in items:
        coerced = _coerce_microstrategy_step(item)
        if coerced is not None:
            normalized.append(coerced)
    return normalized


def _coerce_microstrategy_step(
    value: object,
) -> str | dict[str, str] | None:
    if isinstance(value, str):
        summary = value.strip()
        return summary or None
    if not isinstance(value, dict):
        return None

    raw_summary = value.get("summary")
    if not isinstance(raw_summary, str):
        return None
    summary = raw_summary.strip()
    if not summary:
        return None

    step: dict[str, str] = {"summary": summary}
    raw_class = value.get("assessment_class")
    if isinstance(raw_class, str) and raw_class.strip():
        step["assessment_class"] = raw_class.strip()
    return step


def _microstrategy_summary(step: str | dict[str, str]) -> str:
    if isinstance(step, str):
        return step.strip()
    return str(step.get("summary", "")).strip()


def _default_step_summaries(
    *,
    proposal_excerpt: str,
    problem_frame: str,
    readiness: dict | None,
) -> list[str]:
    focus = _first_content_line(proposal_excerpt) or _first_content_line(problem_frame)
    suffix = f" for {focus}" if focus else ""
    if isinstance(readiness, dict) and readiness.get("ready") is False:
        return [
            f"Refresh understanding and constraints{suffix}",
            "Stabilize missing readiness inputs",
            "Implement the approved change slice",
            "Verify alignment and execution results",
        ]
    return [
        f"Refresh understanding and constraints{suffix}",
        "Implement the approved change slice",
        "Verify alignment and execution results",
    ]


def _first_content_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""


def _materialize_steps(
    *,
    step_summaries: list[str],
    proposal_state: dict,
    assessment_classes: dict[int, str] | None = None,
) -> list[PackageStep]:
    mutation_surface = [str(item) for item in proposal_state.get("resolved_contracts", [])]
    verification_surface = [
        str(item) for item in proposal_state.get("resolved_anchors", [])
    ]
    total = len(step_summaries)
    steps: list[PackageStep] = []

    for index, summary in enumerate(step_summaries, start=1):
        if assessment_classes and index in assessment_classes:
            raw_class = assessment_classes[index]
            try:
                ac = StepClass(raw_class)
            except ValueError:
                ac = _positional_assessment_class(index=index, total=total)
        else:
            ac = _positional_assessment_class(index=index, total=total)
        step_id = f"{ac.value}-{index:02d}"
        prerequisites = [] if not steps else [steps[-1].step_id]
        steps.append(
            PackageStep(
                step_id=step_id,
                assessment_class=ac,
                summary=summary,
                prerequisites=prerequisites,
                expected_outputs=_default_expected_outputs(ac),
                expected_resolutions=_default_expected_resolutions(ac),
                mutation_surface=list(mutation_surface) if ac == StepClass.EDIT else [],
                verification_surface=(
                    list(verification_surface)
                    if ac in {StepClass.VERIFY, StepClass.STABILIZE}
                    else []
                ),
                reversibility=_default_reversibility(ac),
            )
        )
    return steps


def _positional_assessment_class(*, index: int, total: int) -> StepClass:
    if total == 1:
        return StepClass.EDIT
    if index == 1 and total > 1:
        return StepClass.EXPLORE
    if index == total and total > 1:
        return StepClass.VERIFY
    return StepClass.EDIT


def _default_expected_outputs(assessment_class: StepClass) -> list[str]:
    mapping = {
        StepClass.EXPLORE: ["refreshed-understanding"],
        StepClass.STABILIZE: ["stabilized-inputs"],
        StepClass.EDIT: ["code-or-artifact-update"],
        StepClass.COORDINATE: ["coordination-decision"],
        StepClass.VERIFY: ["verification-result"],
    }
    return [mapping[assessment_class]]


def _default_expected_resolutions(assessment_class: StepClass) -> list[str]:
    mapping = {
        StepClass.EXPLORE: ["unknowns reduced"],
        StepClass.STABILIZE: ["blocking state resolved"],
        StepClass.EDIT: ["approved change applied"],
        StepClass.COORDINATE: ["shared seam resolved"],
        StepClass.VERIFY: ["alignment confirmed"],
    }
    return [mapping[assessment_class]]


def _default_reversibility(assessment_class: StepClass) -> str:
    if assessment_class in {StepClass.EXPLORE, StepClass.VERIFY}:
        return "high"
    if assessment_class == StepClass.EDIT:
        return "medium"
    return "low"


def _coerce_package_step(value: object) -> PackageStep | None:
    if isinstance(value, PackageStep):
        return value
    if not isinstance(value, dict):
        return None
    try:
        raw_class = str(value.get("assessment_class", value.get("step_class", "edit")))
        try:
            ac: AssessmentClass = StepClass(raw_class)
        except ValueError:
            ac = DecisionClass(raw_class)
        return PackageStep(
            step_id=str(value["step_id"]),
            assessment_class=ac,
            summary=str(value["summary"]),
            prerequisites=[str(item) for item in value.get("prerequisites", [])],
            expected_outputs=[str(item) for item in value.get("expected_outputs", [])],
            expected_resolutions=[
                str(item) for item in value.get("expected_resolutions", [])
            ],
            mutation_surface=[str(item) for item in value.get("mutation_surface", [])],
            verification_surface=[
                str(item) for item in value.get("verification_surface", [])
            ],
            reversibility=str(value.get("reversibility", "medium")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def build_decision_package(
    scope: str,
    decision_area: str,
    problem_id: str,
    source: str,
    options: list[dict],
) -> RiskPackage:
    """Create a ROAL package for design decision evaluation."""
    steps = []
    for option in options:
        decision_class_str = option.get("decision_class", "component")
        try:
            decision_class = DecisionClass(decision_class_str)
        except ValueError:
            decision_class = DecisionClass.COMPONENT
        steps.append(
            PackageStep(
                step_id=option.get("option_id", f"option-{len(steps)+1}"),
                assessment_class=decision_class,
                summary=option.get("summary", ""),
                prerequisites=list(option.get("prerequisites", [])),
                expected_outputs=["option-evaluation"],
                expected_resolutions=["decision narrowed"],
                mutation_surface=list(option.get("mutation_surface", [])),
                verification_surface=list(option.get("verification_surface", [])),
                reversibility=option.get("reversibility", "medium"),
            )
        )
    return RiskPackage(
        package_id=f"pkg-design-{decision_area}",
        layer="design",
        scope=scope,
        origin_problem_id=problem_id,
        origin_source=source,
        steps=steps,
    )
