"""ROAL loop orchestration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from lib.core.artifact_io import read_json
from lib.core.model_policy import load_model_policy, resolve
from lib.core.path_registry import PathRegistry
from lib.risk.history import compute_history_adjustment, pattern_signature, read_history
from lib.risk.posture import apply_one_step_rule, can_relax_posture, select_posture
from lib.risk.serialization import (
    deserialize_assessment,
    deserialize_plan,
    serialize_assessment,
    serialize_plan,
    write_risk_artifact,
)
from lib.risk.threshold import enforce_thresholds, load_default_parameters, validate_risk_plan
from lib.risk.types import (
    PostureProfile,
    RiskAssessment,
    RiskHistoryEntry,
    RiskPackage,
    RiskPlan,
    StepAssessment,
    StepDecision,
    StepMitigation,
)
from lib.risk.package_builder import write_package
from lib.risk.quantifier import is_step_acceptable, risk_to_posture
from prompt_safety import write_validated_prompt

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def run_risk_loop(
    planspace: Path,
    scope: str,
    layer: str,
    package: RiskPackage,
    dispatch_fn: Callable,
    max_iterations: int = 5,
    posture_floor: PostureProfile | str | None = None,
) -> RiskPlan:
    """Run the full ROAL loop for a package."""
    paths = PathRegistry(planspace)
    write_package(paths, package)
    parameters = _load_parameters(paths)
    history_entries = read_history(paths.risk_history())
    last_assessment: RiskAssessment | None = None
    last_plan: RiskPlan | None = None

    for iteration in range(1, max_iterations + 1):
        assessment_prompt = build_risk_assessment_prompt(package, planspace, scope)
        assessment_prompt_path = (
            paths.risk_dir() / f"{scope}-risk-assessment-prompt.md"
        )
        if not write_validated_prompt(assessment_prompt, assessment_prompt_path):
            fallback = _fallback_plan(
                package,
                layer,
                assessment_id=f"{package.package_id}-assessment-fallback",
                reason="fail-closed: risk assessment prompt failed safety validation",
            )
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
            return fallback
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

        _apply_history_adjustment(assessment, paths.risk_history(), history_entries, parameters)
        last_assessment = assessment
        write_risk_artifact(paths.risk_assessment(scope), serialize_assessment(assessment))

        optimization_prompt = build_optimization_prompt(
            assessment=assessment,
            package=package,
            parameters=parameters,
            planspace=planspace,
            scope=scope,
        )
        if iteration > 1 and last_plan is not None:
            optimization_prompt += (
                "\n\n## Previous Enforcement Outcome\n\n"
                "The previous optimizer response failed mechanical enforcement. "
                "Produce a strictly more conservative plan.\n"
            )
        optimization_prompt_path = paths.risk_dir() / f"{scope}-risk-plan-prompt.md"
        if not write_validated_prompt(optimization_prompt, optimization_prompt_path):
            fallback = _fallback_plan(
                package,
                layer,
                assessment_id=assessment.assessment_id,
                reason="fail-closed: execution optimizer prompt failed safety validation",
            )
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
            return fallback
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

        _apply_posture_hysteresis(
            plan,
            assessment,
            history_entries,
            parameters,
            posture_floor=posture_floor,
        )
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
    posture_floor: PostureProfile | str | None = None,
) -> RiskPlan:
    """Run a lightweight risk check (single assessment, no full loop)."""
    paths = PathRegistry(planspace)
    write_package(paths, package)
    prompt = build_risk_assessment_prompt(package, planspace, scope)
    prompt_path = paths.risk_dir() / f"{scope}-light-risk-assessment-prompt.md"
    if not write_validated_prompt(prompt, prompt_path):
        fallback = _fallback_plan(
            package,
            layer,
            assessment_id=f"{package.package_id}-assessment-fallback",
            reason="fail-closed: lightweight risk assessment prompt failed safety validation",
        )
        write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
        return fallback
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

    parameters = _load_parameters(paths)
    history_entries = read_history(paths.risk_history())
    _apply_history_adjustment(assessment, paths.risk_history(), history_entries, parameters)
    write_risk_artifact(paths.risk_assessment(scope), serialize_assessment(assessment))
    thresholds = parameters.get("step_thresholds", {})
    decisions: list[StepMitigation] = []

    for step_assessment in assessment.step_assessments:
        threshold = thresholds.get(step_assessment.step_class.value)
        posture = risk_to_posture(step_assessment.raw_risk)
        acceptable = (
            step_assessment.raw_risk <= threshold
            if isinstance(threshold, int)
            else is_step_acceptable(step_assessment.raw_risk, step_assessment.step_class)
        )
        if acceptable:
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
    _apply_posture_hysteresis(
        plan,
        assessment,
        history_entries,
        parameters,
        posture_floor=posture_floor,
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
        f"- Risk package: `{paths.risk_package(scope)}`",
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
    lines.extend(["## Artifact Paths", "", "Read these artifacts for context:", ""])
    for title, path, kind in artifact_specs:
        if kind == "json":
            lines.extend(_json_block(title, path, read_json(path)))
        else:
            lines.extend(_artifact_block(title, path, kind))

    corrections_path = paths.corrections()
    if corrections_path.exists():
        lines.extend(
            _artifact_block(
                "Codemap corrections (authoritative overrides)",
                corrections_path,
                "json",
            )
        )

    lines.extend(_json_block("Risk history", paths.risk_history(), None))
    lines.extend(_artifact_block("Monitor signals directory", paths.signals_dir(), "dir"))

    consequence_paths = sorted(
        paths.notes_dir().glob(f"from-*-to-{section_number}.md")
    )
    outgoing_paths = sorted(
        paths.notes_dir().glob(f"from-{section_number}-to-*.md")
    )
    impact_paths = sorted(paths.coordination_dir().glob(f"*{scope}*"))
    lines.extend(_path_list_block("Incoming consequence notes", consequence_paths))
    lines.extend(_path_list_block("Outgoing consequence notes", outgoing_paths))
    lines.extend(_path_list_block("Impact artifacts", impact_paths))
    evidence = _collect_roal_evidence(paths, scope, section_number)
    if evidence:
        lines.extend(["", "## Reassessment Evidence", ""])
        for title, path in evidence:
            lines.append(f"- {title}: `{path}`")

    return "\n".join(lines).strip() + "\n"


def build_optimization_prompt(
    assessment: RiskAssessment,
    package: RiskPackage,
    parameters: dict,
    planspace: Path,
    scope: str,
) -> str:
    """Build the prompt for the Tool Agent (Execution Optimizer)."""
    paths = PathRegistry(planspace)
    lines = [
        "# ROAL Execution Optimization",
        "",
        f"- Risk assessment: `{paths.risk_assessment(scope)}`",
        f"- Risk package: `{paths.risk_package(scope)}`",
        "## Artifact Paths",
        "",
        "Read these artifacts for context:",
        "",
    ]
    lines.extend(_json_block("Risk parameters", paths.risk_parameters(), read_json(paths.risk_parameters())))
    lines.extend(_json_block("Tool registry", paths.tool_registry(), read_json(paths.tool_registry())))
    lines.extend(_json_block("Risk history", paths.risk_history(), None))
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
    content = _read_text(path)
    lines = [
        f"- {title}: `{path}`",
    ]
    if kind == "dir":
        if not path.exists():
            lines[-1] += " (missing)"
        return lines
    if not path.exists():
        lines[-1] += " (missing)"
        return lines
    if kind == "text" and not content:
        lines[-1] += " (empty)"
    return lines


def _json_block(title: str, path: Path, payload: object) -> list[str]:
    del payload
    lines = [f"- {title}: `{path}`"]
    if not path.exists():
        lines[-1] += " (missing)"
        return lines
    try:
        if path.is_file() and path.stat().st_size == 0:
            lines[-1] += " (empty)"
    except OSError:
        lines[-1] += " (unreadable)"
    return lines


def _inline_json_block(title: str, payload: object) -> list[str]:
    return [
        f"## {title}",
        "",
        "```json",
        json.dumps(payload, indent=2),
        "```",
        "",
    ]


def _path_list_block(title: str, paths: list[Path]) -> list[str]:
    if not paths:
        return [f"- {title}: none"]
    if len(paths) == 1:
        return [f"- {title}: `{paths[0]}`"]
    return [f"- {title}: " + ", ".join(f"`{path}`" for path in paths)]


def _collect_roal_evidence(
    paths: PathRegistry,
    scope: str,
    section_number: str,
) -> list[tuple[str, Path]]:
    """Collect section-scoped evidence artifacts for ROAL prompts."""
    evidence: list[tuple[str, Path]] = []

    manifest_path = (
        paths.input_refs_dir(section_number)
        / f"section-{section_number}-modified-file-manifest.json"
    )
    if manifest_path.exists():
        evidence.append(("Modified-file manifest", manifest_path))

    align_result = paths.artifacts / f"impl-align-{section_number}-output.md"
    if align_result.exists():
        evidence.append(("Alignment check result", align_result))

    for recon in sorted(paths.reconciliation_dir().glob(f"*{scope}*")):
        evidence.append(("Reconciliation result", recon))

    for risk_artifact_name in (
        f"section-{section_number}-risk-accepted-steps.json",
        f"section-{section_number}-risk-deferred.json",
    ):
        risk_path = paths.input_refs_dir(section_number) / risk_artifact_name
        if risk_path.exists():
            evidence.append(("Previous risk artifact", risk_path))

    return evidence


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


def _apply_history_adjustment(
    assessment: RiskAssessment,
    history_path: Path,
    history_entries: list[RiskHistoryEntry],
    parameters: dict,
) -> None:
    primary_step = _primary_step_assessment(assessment)
    if primary_step is None:
        return

    signature = pattern_signature(
        primary_step.step_class,
        primary_step.dominant_risks,
        primary_step.modifiers.blast_radius,
    )
    matching_entries = [
        entry
        for entry in history_entries
        if _history_signature(entry) == signature
    ]
    adjustment = compute_history_adjustment(
        history_path,
        primary_step.step_class,
        primary_step.dominant_risks,
        primary_step.modifiers.blast_radius,
    )
    bound = _coerce_float(parameters.get("history_adjustment_bound"), 10.0)
    bounded_adjustment = _clamp_float(adjustment, -bound, bound)
    assessment.package_raw_risk = _clamp_int(
        assessment.package_raw_risk + int(round(bounded_adjustment)),
        0,
        100,
    )
    if matching_entries:
        assessment.notes.append(
            "history-adjustment "
            f"{signature} {bounded_adjustment:+.1f} from {len(matching_entries)} "
            "similar outcomes"
        )


def _apply_posture_hysteresis(
    plan: RiskPlan,
    assessment: RiskAssessment,
    history_entries: list[RiskHistoryEntry],
    parameters: dict,
    *,
    posture_floor: PostureProfile | str | None,
) -> None:
    assessment_steps = {
        step.step_id: step
        for step in assessment.step_assessments
    }
    floor = _coerce_posture(posture_floor)
    cooldown_iterations = _coerce_int(
        parameters.get("cooldown_iterations"),
        default=2,
    )

    for decision in plan.step_decisions:
        if decision.posture is None:
            continue

        step_assessment = assessment_steps.get(decision.step_id)
        raw_risk = _decision_raw_risk(decision, step_assessment, assessment.package_raw_risk)
        previous_posture: PostureProfile | None = None
        recent_outcomes: list[str] = []
        cooldown_remaining = 0

        if step_assessment is not None:
            matching_history = _matching_history(history_entries, step_assessment)
            if matching_history:
                previous_posture = matching_history[-1].posture
                recent_outcomes = [
                    _normalize_history_outcome(entry.actual_outcome)
                    for entry in matching_history
                ]
                cooldown_remaining = _cooldown_remaining(
                    recent_outcomes,
                    cooldown_iterations,
                )

        hysteresis_posture = (
            select_posture(
                raw_risk,
                previous_posture,
                recent_outcomes,
                cooldown_remaining,
            )
            if previous_posture is not None
            else risk_to_posture(raw_risk)
        )
        target = _stricter_posture(decision.posture, hysteresis_posture)

        if previous_posture is not None:
            consecutive_successes = _count_trailing_successes(recent_outcomes)
            if (
                _posture_rank(target) < _posture_rank(previous_posture)
                and not can_relax_posture(
                    previous_posture,
                    consecutive_successes,
                    cooldown_remaining,
                )
            ):
                target = previous_posture
            target = apply_one_step_rule(previous_posture, target)

        if floor is not None:
            target = _stricter_posture(target, floor)

        decision.posture = target


def _primary_step_assessment(assessment: RiskAssessment) -> StepAssessment | None:
    if not assessment.step_assessments:
        return None
    return max(
        assessment.step_assessments,
        key=lambda item: (item.raw_risk, item.modifiers.blast_radius, len(item.dominant_risks)),
    )


def _matching_history(
    history_entries: list[RiskHistoryEntry],
    step_assessment: StepAssessment,
) -> list[RiskHistoryEntry]:
    signature = pattern_signature(
        step_assessment.step_class,
        step_assessment.dominant_risks,
        step_assessment.modifiers.blast_radius,
    )
    return [
        entry
        for entry in history_entries
        if _history_signature(entry) == signature
    ]


def _history_signature(entry: RiskHistoryEntry) -> str:
    return pattern_signature(
        entry.step_class,
        entry.dominant_risks,
        entry.blast_radius_band,
    )


def _decision_raw_risk(
    decision: StepMitigation,
    step_assessment: StepAssessment | None,
    package_raw_risk: int,
) -> int:
    if decision.residual_risk is not None:
        return decision.residual_risk
    if step_assessment is not None:
        return step_assessment.raw_risk
    return package_raw_risk


def _normalize_history_outcome(outcome: str) -> str:
    normalized = outcome.strip().lower()
    if normalized in {"reopened", "reopen"}:
        return "reopen"
    if normalized in {"risk_review_failure", "failed"}:
        return "failure"
    if normalized == "over_guarded":
        return "success"
    return normalized


def _cooldown_remaining(outcomes: list[str], cooldown_iterations: int) -> int:
    last_failure_index = -1
    for index, outcome in enumerate(outcomes):
        if outcome in {"failure", "error", "reopen", "blocked"}:
            last_failure_index = index
    if last_failure_index < 0:
        return 0
    iterations_since_failure = len(outcomes) - last_failure_index - 1
    return max(cooldown_iterations - iterations_since_failure, 0)


def _count_trailing_successes(outcomes: list[str]) -> int:
    count = 0
    for outcome in reversed(outcomes):
        if outcome == "success":
            count += 1
            continue
        break
    return count


def _coerce_posture(value: PostureProfile | str | None) -> PostureProfile | None:
    if isinstance(value, PostureProfile):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return PostureProfile(value.strip())
        except ValueError:
            return None
    return None


def _stricter_posture(
    left: PostureProfile,
    right: PostureProfile,
) -> PostureProfile:
    if _posture_rank(left) >= _posture_rank(right):
        return left
    return right


def _posture_rank(posture: PostureProfile) -> int:
    return {
        PostureProfile.P0_DIRECT: 0,
        PostureProfile.P1_LIGHT: 1,
        PostureProfile.P2_STANDARD: 2,
        PostureProfile.P3_GUARDED: 3,
        PostureProfile.P4_REOPEN: 4,
    }[posture]


def _coerce_int(value: object, *, default: int) -> int:
    return int(value) if isinstance(value, int) else default


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _clamp_float(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _risk_assessor_model(planspace: Path) -> str:
    policy = load_model_policy(planspace)
    return resolve(policy, "risk_assessor")


def _execution_optimizer_model(planspace: Path) -> str:
    policy = load_model_policy(planspace)
    return resolve(policy, "execution_optimizer")


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
