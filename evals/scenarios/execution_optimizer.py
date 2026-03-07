"""Execution optimizer live-eval scenarios.

Dispatches the real `execution-optimizer.md` agent with bounded ROAL prompts and
checks that it returns valid `RiskPlan` JSON plus a conservative posture when
the assessment remains materially risky.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _extract_json_object(agent_output: str) -> dict[str, object] | None:
    candidate = agent_output.strip()
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(payload, dict):
            return payload
    fenced = _JSON_FENCE_RE.search(candidate)
    if fenced is not None:
        try:
            payload = json.loads(fenced.group(1))
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _write_optimizer_fixture(
    planspace: Path,
    *,
    section: str,
    assessment_payload: dict[str, object],
    package_payload: dict[str, object],
    parameters_payload: dict[str, object],
    extra_instructions: str,
) -> Path:
    artifacts = planspace / "artifacts"
    risk = artifacts / "risk"
    risk.mkdir(parents=True, exist_ok=True)
    tool_registry = artifacts / "tool-registry.json"
    risk_history = risk / "risk-history.jsonl"
    risk_parameters = risk / "risk-parameters.json"

    tool_registry.write_text(
        json.dumps({"tools": ["pytest", "rg"], "bridge_tools": []}, indent=2) + "\n",
        encoding="utf-8",
    )
    risk_history.write_text(
        json.dumps(
            {
                "package_id": package_payload["package_id"],
                "step_id": package_payload["steps"][0]["step_id"],
                "layer": "implementation",
                "step_class": package_payload["steps"][0]["step_class"],
                "posture": "P2",
                "predicted_risk": 58,
                "actual_outcome": "warning",
                "surfaced_surprises": ["verification surface wider than expected"],
                "verification_outcome": "passed",
                "dominant_risks": assessment_payload["dominant_risks"],
                "blast_radius_band": 2,
            }
        ) + "\n",
        encoding="utf-8",
    )
    risk_parameters.write_text(
        json.dumps(parameters_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    prompt_path = artifacts / f"{section}-execution-optimizer-eval-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # ROAL Execution Optimization

        ## Risk Assessment

        ```json
        {json.dumps(assessment_payload, indent=2)}
        ```

        ## Current Package

        ```json
        {json.dumps(package_payload, indent=2)}
        ```

        ## Artifact Paths

        Read these artifacts for context:

        - Risk parameters: `{risk_parameters}`
        - Tool registry: `{tool_registry}`
        - Risk history: `{risk_history}`

        {extra_instructions}

        Output JSON only. The JSON must match the RiskPlan schema.
    """), encoding="utf-8")
    return prompt_path


def _setup_valid_plan(planspace: Path, codespace: Path) -> Path:
    del codespace
    assessment_payload = {
        "assessment_id": "assessment-section-41",
        "layer": "implementation",
        "package_id": "pkg-implementation-section-41",
        "assessment_scope": "section-41",
        "understanding_inventory": {
            "confirmed": ["[step:edit-01] proposal-state and readiness are fresh"],
            "assumed": [],
            "missing": [],
            "stale": [],
        },
        "package_raw_risk": 24,
        "assessment_confidence": 0.88,
        "dominant_risks": ["context_rot"],
        "step_assessments": [
            {
                "step_id": "edit-01",
                "step_class": "edit",
                "summary": "Apply a bounded local implementation fix",
                "prerequisites": [],
                "risk_vector": {
                    "context_rot": 1,
                    "silent_drift": 0,
                    "scope_creep": 0,
                    "brute_force_regression": 0,
                    "cross_section_incoherence": 0,
                    "tool_island_isolation": 0,
                    "stale_artifact_contamination": 0,
                },
                "modifiers": {
                    "blast_radius": 1,
                    "reversibility": 3,
                    "observability": 4,
                    "confidence": 0.88,
                },
                "raw_risk": 24,
                "dominant_risks": ["context_rot"],
            }
        ],
        "frontier_candidates": ["edit-01"],
        "reopen_recommendations": [],
        "notes": ["bounded fixture"],
    }
    package_payload = {
        "package_id": "pkg-implementation-section-41",
        "layer": "implementation",
        "scope": "section-41",
        "origin_problem_id": "section-41:proposal",
        "origin_source": "proposal",
        "steps": [
            {
                "step_id": "edit-01",
                "step_class": "edit",
                "summary": "Apply a bounded local implementation fix",
                "prerequisites": [],
                "expected_outputs": ["code-or-artifact-update"],
                "expected_resolutions": ["approved change applied"],
                "mutation_surface": ["LocalContract"],
                "verification_surface": [],
                "reversibility": "medium",
            }
        ],
    }
    return _write_optimizer_fixture(
        planspace,
        section="section-41",
        assessment_payload=assessment_payload,
        package_payload=package_payload,
        parameters_payload={
            "step_thresholds": {"edit": 45},
            "execution_thresholds": {"edit": 45},
        },
        extra_instructions=(
            "This is a low-risk fixture. Choose the minimum effective posture "
            "that keeps residual risk below threshold."
        ),
    )


def _setup_high_risk_plan(planspace: Path, codespace: Path) -> Path:
    del codespace
    assessment_payload = {
        "assessment_id": "assessment-section-42",
        "layer": "implementation",
        "package_id": "pkg-implementation-section-42",
        "assessment_scope": "section-42",
        "understanding_inventory": {
            "confirmed": [],
            "assumed": ["[step:edit-02] codemap may still reflect the old contract owner"],
            "missing": ["[step:verify-03] verification path for downstream consumer section"],
            "stale": ["[step:edit-02] readiness artifact predates reconciliation"],
        },
        "package_raw_risk": 82,
        "assessment_confidence": 0.63,
        "dominant_risks": [
            "cross_section_incoherence",
            "stale_artifact_contamination",
            "brute_force_regression",
        ],
        "step_assessments": [
            {
                "step_id": "edit-02",
                "step_class": "edit",
                "summary": "Mutate a shared contract with stale prerequisites",
                "prerequisites": ["fresh readiness artifact", "coordinated contract owner"],
                "risk_vector": {
                    "context_rot": 2,
                    "silent_drift": 3,
                    "scope_creep": 1,
                    "brute_force_regression": 3,
                    "cross_section_incoherence": 4,
                    "tool_island_isolation": 0,
                    "stale_artifact_contamination": 4,
                },
                "modifiers": {
                    "blast_radius": 4,
                    "reversibility": 1,
                    "observability": 2,
                    "confidence": 0.63,
                },
                "raw_risk": 84,
                "dominant_risks": [
                    "cross_section_incoherence",
                    "stale_artifact_contamination",
                ],
            }
        ],
        "frontier_candidates": ["edit-02"],
        "reopen_recommendations": [
            "Shared contract owner is stale relative to reconciliation outputs"
        ],
        "notes": ["Conservative posture required"],
    }
    package_payload = {
        "package_id": "pkg-implementation-section-42",
        "layer": "implementation",
        "scope": "section-42",
        "origin_problem_id": "section-42:proposal",
        "origin_source": "proposal",
        "steps": [
            {
                "step_id": "edit-02",
                "step_class": "edit",
                "summary": "Mutate a shared contract with stale prerequisites",
                "prerequisites": ["explore-01"],
                "expected_outputs": ["code-or-artifact-update"],
                "expected_resolutions": ["approved change applied"],
                "mutation_surface": ["SharedContract", "DownstreamBinding"],
                "verification_surface": ["tests/test_router.py"],
                "reversibility": "low",
            }
        ],
    }
    return _write_optimizer_fixture(
        planspace,
        section="section-42",
        assessment_payload=assessment_payload,
        package_payload=package_payload,
        parameters_payload={
            "step_thresholds": {"edit": 45},
            "execution_thresholds": {"edit": 45},
        },
        extra_instructions=(
            "This fixture is intentionally above threshold. Recommend P3 or "
            "P4, or reject/reopen the step if local execution is still unsafe."
        ),
    )


def _check_plan_json(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "Agent output did not contain a JSON object"
    required = {
        "plan_id",
        "assessment_id",
        "package_id",
        "layer",
        "step_decisions",
        "accepted_frontier",
        "deferred_steps",
        "reopen_steps",
    }
    missing = sorted(required - payload.keys())
    if missing:
        return False, f"Missing required keys: {missing}"
    decisions = payload.get("step_decisions")
    if not isinstance(decisions, list) or not decisions:
        return False, "step_decisions missing or empty"
    return True, f"assessment_id={payload.get('assessment_id')}"


def _check_plan_scope(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    if payload.get("package_id") != "pkg-implementation-section-41":
        return False, f"Unexpected package_id: {payload.get('package_id')}"
    decisions = payload.get("step_decisions")
    if not isinstance(decisions, list):
        return False, "step_decisions missing"
    if not decisions or decisions[0].get("step_id") != "edit-01":
        return False, f"Unexpected step_decisions payload: {decisions}"
    return True, "plan references the expected step"


def _check_high_risk_conservative_posture(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    decisions = payload.get("step_decisions")
    if not isinstance(decisions, list) or not decisions:
        return False, "step_decisions missing or empty"
    decision = decisions[0]
    posture = decision.get("posture")
    verdict = decision.get("decision")
    if posture in {"P3", "P4"}:
        return True, f"posture={posture}"
    if verdict == "reject_reopen":
        return True, "decision=reject_reopen"
    return False, f"Expected posture P3/P4 or reject_reopen, got posture={posture}, decision={verdict}"


def _check_high_risk_blocks_or_reopens(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    reopen = payload.get("reopen_steps")
    deferred = payload.get("deferred_steps")
    if isinstance(reopen, list) and reopen:
        return True, f"reopen_steps={reopen}"
    if isinstance(deferred, list) and deferred:
        return True, f"deferred_steps={deferred}"
    return False, "Expected at least one step to be deferred or reopened"


SCENARIOS = [
    Scenario(
        name="execution_optimizer_valid_plan",
        agent_file="execution-optimizer.md",
        model_policy_key="execution_optimizer",
        setup=_setup_valid_plan,
        checks=[
            Check(
                description="Execution optimizer emits valid RiskPlan JSON",
                verify=_check_plan_json,
            ),
            Check(
                description="Execution optimizer preserves the expected package step",
                verify=_check_plan_scope,
            ),
        ],
    ),
    Scenario(
        name="execution_optimizer_high_risk",
        agent_file="execution-optimizer.md",
        model_policy_key="execution_optimizer",
        setup=_setup_high_risk_plan,
        checks=[
            Check(
                description="High-risk assessment yields conservative posture",
                verify=_check_high_risk_conservative_posture,
            ),
            Check(
                description="High-risk assessment blocks or reopens work",
                verify=_check_high_risk_blocks_or_reopens,
            ),
        ],
    ),
]
