"""ROAL loop orchestration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from lib.core.artifact_io import read_json
from lib.core.model_policy import load_model_policy
from lib.core.path_registry import PathRegistry
from lib.risk.serialization import (
    deserialize_assessment,
    deserialize_plan,
    serialize_assessment,
    serialize_history_entry,
    serialize_package,
    serialize_plan,
    write_risk_artifact,
)
from lib.risk.threshold import enforce_thresholds, load_default_parameters, validate_risk_plan
from lib.risk.types import (
    PostureProfile,
    RiskAssessment,
    RiskPackage,
    RiskPlan,
    StepAssessment,
    StepDecision,
    StepMitigation,
)
from lib.risk.history import read_history
from lib.risk.package_builder import write_package
from lib.risk.quantifier import risk_to_posture

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def run_risk_loop(
    planspace: Path,
    scope: str,
    layer: str,
    package: RiskPackage,
    dispatch_fn: Callable,
    max_iterations: int = 5,
) -> RiskPlan:
    """Run the full ROAL loop for a package."""
    paths = PathRegistry(planspace)
    write_package(paths, package)
    parameters = _load_parameters(paths)
    last_assessment: RiskAssessment | None = None
    last_plan: RiskPlan | None = None

    for iteration in range(1, max_iterations + 1):
        assessment_prompt = build_risk_assessment_prompt(package, planspace, scope)
        assessment_prompt_path = _write_prompt(
            paths.risk_dir() / f"{scope}-risk-assessment-prompt.md",
            assessment_prompt,
        )
        assessment_output_path = (
            paths.risk_dir() / f"{scope}-risk-assessment-output.md"
        )
        assessment_response = dispatch_fn(
            _risk_assessor_model(planspace),
            assessment_prompt_path,
            assessment_output_path,
            planspace,
            None,
            f"risk-assessor-{scope}",
            agent_file="risk-assessor.md",
        )
        assessment = parse_risk_assessment(assessment_response)
        if assessment is None:
            fallback = _fallback_plan(
                package,
                layer,
                assessment_id=f"{package.package_id}-assessment-fallback",
                reason="fail-closed: risk assessment could not be parsed",
            )
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
            return fallback

        last_assessment = assessment
        write_risk_artifact(paths.risk_assessment(scope), serialize_assessment(assessment))

        optimization_prompt = build_optimization_prompt(
            assessment=assessment,
            package=package,
            parameters=parameters,
            planspace=planspace,
        )
        if iteration > 1 and last_plan is not None:
            optimization_prompt += (
                "\n\n## Previous Enforcement Outcome\n\n"
                "The previous optimizer response failed mechanical enforcement. "
                "Produce a strictly more conservative plan.\n"
            )
        optimization_prompt_path = _write_prompt(
            paths.risk_dir() / f"{scope}-risk-plan-prompt.md",
            optimization_prompt,
        )
        optimization_output_path = paths.risk_dir() / f"{scope}-risk-plan-output.md"
        optimization_response = dispatch_fn(
            _execution_optimizer_model(planspace),
            optimization_prompt_path,
            optimization_output_path,
            planspace,
            None,
            f"execution-optimizer-{scope}",
            agent_file="execution-optimizer.md",
        )
        plan = parse_risk_plan(optimization_response)
        if plan is None:
            fallback = _fallback_plan(
                package,
                layer,
                assessment_id=assessment.assessment_id,
                reason="fail-closed: execution optimizer could not be parsed",
            )
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
            return fallback

        assessments = {
            item.step_id: item
            for item in assessment.step_assessments
        }
        enriched_parameters = dict(parameters)
        enriched_parameters["step_classes"] = {
            step_id: step_assessment.step_class
            for step_id, step_assessment in assessments.items()
        }
        enforced_plan = enforce_thresholds(plan, assessments, enriched_parameters)
        violations = validate_risk_plan(enforced_plan, enriched_parameters)
        if not violations:
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(enforced_plan))
            return enforced_plan

        last_plan = enforced_plan

    fallback_assessment_id = (
        last_assessment.assessment_id
        if last_assessment is not None
        else f"{package.package_id}-assessment-fallback"
    )
    fallback = _fallback_plan(
        package,
        layer,
        assessment_id=fallback_assessment_id,
        reason="fail-closed: risk loop exhausted without a threshold-compliant plan",
    )
    write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
    return fallback


def run_lightweight_risk_check(
    planspace: Path,
    scope: str,
    layer: str,
    package: RiskPackage,
    dispatch_fn: Callable,
) -> RiskPlan:
    """Run a lightweight risk check (single assessment, no full loop)."""
    paths = PathRegistry(planspace)
    write_package(paths, package)
    prompt = build_risk_assessment_prompt(package, planspace, scope)
    prompt_path = _write_prompt(
        paths.risk_dir() / f"{scope}-light-risk-assessment-prompt.md",
        prompt,
    )
    output_path = paths.risk_dir() / f"{scope}-light-risk-assessment-output.md"
    response = dispatch_fn(
        _risk_assessor_model(planspace),
        prompt_path,
        output_path,
        planspace,
        None,
        f"risk-assessor-light-{scope}",
        agent_file="risk-assessor.md",
    )
    assessment = parse_risk_assessment(response)
    if assessment is None:
        fallback = _fallback_plan(
            package,
            layer,
            assessment_id=f"{package.package_id}-assessment-fallback",
            reason="fail-closed: lightweight risk assessment could not be parsed",
        )
        write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
        return fallback

    write_risk_artifact(paths.risk_assessment(scope), serialize_assessment(assessment))
    parameters = _load_parameters(paths)
    thresholds = parameters.get("step_thresholds", {})
    decisions: list[StepMitigation] = []

    for step_assessment in assessment.step_assessments:
        threshold = thresholds.get(step_assessment.step_class.value)
        posture = risk_to_posture(step_assessment.raw_risk)
        if isinstance(threshold, int) and step_assessment.raw_risk <= threshold:
            decisions.append(
                StepMitigation(
                    step_id=step_assessment.step_id,
                    decision=StepDecision.ACCEPT,
                    posture=posture,
                    mitigations=[],
                    residual_risk=step_assessment.raw_risk,
                    reason="mechanical lightweight posture from risk score",
                )
            )
        else:
            decisions.append(
                StepMitigation(
                    step_id=step_assessment.step_id,
                    decision=StepDecision.REJECT_DEFER,
                    posture=posture,
                    mitigations=[],
                    residual_risk=step_assessment.raw_risk,
                    reason="lightweight check kept step above threshold",
                    wait_for=["full-risk-loop"],
                )
            )

    plan = RiskPlan(
        plan_id=f"risk-plan-light-{scope}",
        assessment_id=assessment.assessment_id,
        package_id=package.package_id,
        layer=layer,
        step_decisions=decisions,
        accepted_frontier=[
            decision.step_id
            for decision in decisions
            if decision.decision == StepDecision.ACCEPT
            and decision.step_id in assessment.frontier_candidates
        ],
        deferred_steps=[
            decision.step_id
            for decision in decisions
            if decision.decision == StepDecision.REJECT_DEFER
        ],
        reopen_steps=[],
        expected_reassessment_inputs=[],
    )
    write_risk_artifact(paths.risk_plan(scope), serialize_plan(plan))
    return plan


def build_risk_assessment_prompt(
    package: RiskPackage,
    planspace: Path,
    scope: str,
) -> str:
    """Build the prompt for the Risk Agent."""
    paths = PathRegistry(planspace)
    section_number = _scope_number(scope)
    lines = [
        "# ROAL Risk Assessment",
        "",
        f"- Scope: `{scope}`",
        f"- Layer: `{package.layer}`",
        f"- Package ID: `{package.package_id}`",
        "",
        "## Risk Package",
        "",
        "```json",
        json.dumps(serialize_package(package), indent=2),
        "```",
        "",
    ]

    artifact_specs = [
        ("Section spec", paths.section_spec(section_number), "text"),
        ("Proposal excerpt", paths.proposal_excerpt(section_number), "text"),
        ("Alignment excerpt", paths.alignment_excerpt(section_number), "text"),
        ("Problem frame", paths.problem_frame(section_number), "text"),
        ("Microstrategy", paths.microstrategy(section_number), "text"),
        (
            "Proposal state",
            paths.proposals_dir() / f"{scope}-proposal-state.json",
            "json",
        ),
        (
            "Readiness",
            paths.readiness_dir() / f"{scope}-execution-ready.json",
            "json",
        ),
        ("Tool registry", paths.tool_registry(), "json"),
        ("Codemap", paths.codemap(), "text"),
    ]
    for title, path, kind in artifact_specs:
        lines.extend(_artifact_block(title, path, kind))

    history = [
        serialize_history_entry(entry)
        for entry in read_history(paths.risk_history())
        if entry.package_id == package.package_id or entry.layer == package.layer
    ]
    lines.extend(_json_block("Risk history", paths.risk_history(), history))

    monitor_payloads = _collect_monitor_signals(paths, scope)
    lines.extend(
        _json_block(
            "Monitor signals",
            paths.signals_dir(),
            monitor_payloads,
        )
    )

    consequence_paths = list(paths.notes_dir().glob(f"*{scope}*"))
    impact_paths = list(paths.coordination_dir().glob(f"*{scope}*"))
    for path in sorted(consequence_paths):
        lines.extend(_artifact_block("Consequence notes", path, "text"))
    for path in sorted(impact_paths):
        lines.extend(_artifact_block("Impact artifacts", path, "text"))

    return "\n".join(lines).strip() + "\n"


def build_optimization_prompt(
    assessment: RiskAssessment,
    package: RiskPackage,
    parameters: dict,
    planspace: Path,
) -> str:
    """Build the prompt for the Tool Agent (Execution Optimizer)."""
    paths = PathRegistry(planspace)
    tool_registry = read_json(paths.tool_registry())
    history = [
        serialize_history_entry(entry)
        for entry in read_history(paths.risk_history())
    ]

    lines = [
        "# ROAL Execution Optimization",
        "",
        "## Risk Assessment",
        "",
        "```json",
        json.dumps(serialize_assessment(assessment), indent=2),
        "```",
        "",
        "## Current Package",
        "",
        "```json",
        json.dumps(serialize_package(package), indent=2),
        "```",
        "",
        "## Risk Parameters",
        "",
        "```json",
        json.dumps(parameters, indent=2),
        "```",
        "",
    ]
    lines.extend(_json_block("Tool registry", paths.tool_registry(), tool_registry))
    lines.extend(_json_block("Risk history", paths.risk_history(), history))
    return "\n".join(lines).strip() + "\n"


def parse_risk_assessment(response: str) -> RiskAssessment | None:
    """Parse the Risk Agent's JSON response into a RiskAssessment."""
    payload = _extract_json_payload(response)
    if payload is None:
        return None
    try:
        return deserialize_assessment(payload)
    except (KeyError, TypeError, ValueError):
        return None


def parse_risk_plan(response: str) -> RiskPlan | None:
    """Parse the Tool Agent's JSON response into a RiskPlan."""
    payload = _extract_json_payload(response)
    if payload is None:
        return None
    try:
        return deserialize_plan(payload)
    except (KeyError, TypeError, ValueError):
        return None


def _extract_json_payload(response: str) -> dict | None:
    candidate = response.strip()
    if not candidate:
        return None

    direct = _loads_object(candidate)
    if direct is not None:
        return direct

    fenced = _JSON_FENCE_RE.search(candidate)
    if fenced is not None:
        parsed = _loads_object(fenced.group(1))
        if parsed is not None:
            return parsed

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return _loads_object(candidate[start : end + 1])


def _loads_object(candidate: str) -> dict | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _artifact_block(title: str, path: Path, kind: str) -> list[str]:
    if kind == "json":
        payload = read_json(path)
        return _json_block(title, path, payload)

    content = _read_text(path)
    lines = [
        f"## {title}",
        "",
        f"- Path: `{path}`",
    ]
    if not content:
        lines.extend(["- Status: missing or empty", ""])
        return lines
    lines.extend(["", content, ""])
    return lines


def _json_block(title: str, path: Path, payload: object) -> list[str]:
    lines = [
        f"## {title}",
        "",
        f"- Path: `{path}`",
    ]
    if payload is None:
        lines.extend(["- Status: missing or empty", ""])
        return lines
    lines.extend(
        [
            "",
            "```json",
            json.dumps(payload, indent=2),
            "```",
            "",
        ]
    )
    return lines


def _collect_monitor_signals(paths: PathRegistry, scope: str) -> list[dict]:
    payloads: list[dict] = []
    for path in sorted(paths.signals_dir().glob(f"*{scope}*")):
        data = read_json(path)
        if isinstance(data, dict):
            payloads.append({"path": str(path), "payload": data})
    return payloads


def _load_parameters(paths: PathRegistry) -> dict:
    parameters = load_default_parameters()
    raw = read_json(paths.risk_parameters())
    if not isinstance(raw, dict):
        return parameters

    posture_bands = raw.get("posture_bands")
    if isinstance(posture_bands, dict):
        parameters["posture_bands"] = posture_bands

    step_thresholds = raw.get("step_thresholds")
    if isinstance(step_thresholds, dict):
        parameters["step_thresholds"].update(
            {
                str(key): int(value)
                for key, value in step_thresholds.items()
                if isinstance(value, int)
            }
        )

    execution_thresholds = raw.get("execution_thresholds")
    if isinstance(execution_thresholds, dict):
        parameters["step_thresholds"].update(
            {
                str(key): int(value)
                for key, value in execution_thresholds.items()
                if isinstance(value, int)
            }
        )

    for scalar_key in (
        "cooldown_iterations",
        "relaxation_required_successes",
        "history_adjustment_bound",
    ):
        if scalar_key in raw:
            parameters[scalar_key] = raw[scalar_key]

    parameters["execution_thresholds"] = dict(parameters["step_thresholds"])
    return parameters


def _risk_assessor_model(planspace: Path) -> str:
    policy = load_model_policy(planspace)
    return policy.get("risk_assessor", policy.implementation)


def _execution_optimizer_model(planspace: Path) -> str:
    policy = load_model_policy(planspace)
    return policy.get("execution_optimizer", policy.bridge_tools)


def _fallback_plan(
    package: RiskPackage,
    layer: str,
    *,
    assessment_id: str,
    reason: str,
) -> RiskPlan:
    decisions = [
        StepMitigation(
            step_id=step.step_id,
            decision=StepDecision.REJECT_REOPEN,
            posture=PostureProfile.P4_REOPEN,
            mitigations=[],
            residual_risk=100,
            reason=reason,
            route_to="parent",
        )
        for step in package.steps
    ]
    return RiskPlan(
        plan_id=f"risk-plan-{package.scope}-fallback",
        assessment_id=assessment_id,
        package_id=package.package_id,
        layer=layer,
        step_decisions=decisions,
        accepted_frontier=[],
        deferred_steps=[],
        reopen_steps=[decision.step_id for decision in decisions],
        expected_reassessment_inputs=[],
    )


def _write_prompt(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _scope_number(scope: str) -> str:
    match = re.search(r"section-(\d+)", scope)
    if match is not None:
        return match.group(1)
    return scope


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
