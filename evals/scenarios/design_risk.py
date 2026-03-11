"""Design risk live-eval scenarios.

Dispatches the real `risk-assessor.md` agent with design-layer ROAL packages
containing decision classes and checks that design facets are scored
appropriately.
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
    for text in (candidate,):
        try:
            payload = json.loads(text)
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


def _setup_design_risk(planspace: Path, codespace: Path) -> Path:
    """Seed a design-layer ROAL package with two stack options."""
    del codespace
    artifacts = planspace / "artifacts"
    risk = artifacts / "risk"
    risk.mkdir(parents=True, exist_ok=True)

    package_payload = {
        "package_id": "pkg-design-primary-database",
        "layer": "design",
        "scope": "global",
        "origin_problem_id": "PROB-cash-visibility",
        "origin_source": "stack-evaluator",
        "steps": [
            {
                "step_id": "platform-proprietary-db",
                "assessment_class": "platform",
                "summary": "Use a proprietary cloud-native database with vendor-specific APIs and no migration path",
                "prerequisites": [
                    "verified problem frame",
                    "selected reliability scale",
                ],
                "expected_outputs": ["option-evaluation"],
                "expected_resolutions": ["database choice narrowed"],
                "mutation_surface": ["data-platform", "analytics-read-model"],
                "verification_surface": ["operability model", "migration path"],
                "reversibility": "low",
            },
            {
                "step_id": "component-postgres",
                "assessment_class": "component",
                "summary": "Use PostgreSQL as the primary database with standard SQL interfaces",
                "prerequisites": [
                    "verified problem frame",
                ],
                "expected_outputs": ["option-evaluation"],
                "expected_resolutions": ["database choice narrowed"],
                "mutation_surface": ["data-platform"],
                "verification_surface": ["operability model"],
                "reversibility": "medium",
            },
        ],
    }

    risk_package = risk / "global-risk-package.json"
    risk_package.write_text(
        json.dumps(package_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    # Write governance context
    governance = artifacts / "governance"
    governance.mkdir(parents=True, exist_ok=True)
    (governance / "problem-index.json").write_text(
        json.dumps([{
            "problem_id": "PROB-cash-visibility",
            "title": "Real-time cash position visibility across accounts",
            "status": "verified",
        }], indent=2) + "\n",
        encoding="utf-8",
    )
    (governance / "constraint-index.json").write_text(
        json.dumps([{
            "constraint_id": "CON-001",
            "title": "Must support multi-cloud deployment",
            "status": "verified",
            "scope": "global",
        }], indent=2) + "\n",
        encoding="utf-8",
    )

    # Write value scale context
    (risk / "global-value-scales.json").write_text(
        json.dumps([{
            "value_id": "reliability",
            "scope": "global",
            "levels": [
                {"value_id": "reliability", "level": 0, "label": "Best effort"},
                {"value_id": "reliability", "level": 1, "label": "Basic HA"},
                {"value_id": "reliability", "level": 2, "label": "Multi-AZ with failover"},
            ],
            "suggested_level": 2,
            "suggested_rationale": "Financial data requires high availability",
            "selected_level": 2,
            "selected_state": "verified",
        }], indent=2) + "\n",
        encoding="utf-8",
    )

    # Write the prompt
    prompt_path = artifacts / "design-risk-eval-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # ROAL Design Risk Assessment

        - Scope: `global`
        - Layer: `design`
        - Package ID: `pkg-design-primary-database`

        You are assessing **design risk** for a technical stack decision, not
        execution risk. Use the **design facets** (ecosystem_maturity,
        dependency_lock_in, team_capability, scale_fit, integration_fit,
        operability_cost, evolution_flexibility) for decision-class steps.
        Execution facets should be 0 for these steps.

        ## Risk Package

        ```json
        {json.dumps(package_payload, indent=2)}
        ```

        ## Governance Context

        - Verified problem: Real-time cash position visibility across accounts
        - Verified constraint: Must support multi-cloud deployment
        - Selected reliability level: Multi-AZ with failover (level 2, verified)

        The proprietary database option has:
        - Vendor-specific APIs with no standard SQL compatibility
        - No migration path to other platforms
        - Low reversibility
        - Conflicts with multi-cloud deployment constraint

        The PostgreSQL option has:
        - Standard SQL interfaces
        - Multi-cloud compatible
        - Medium reversibility
        - Well-established ecosystem

        ## Artifact Paths

        - Risk package: `{risk_package}`
        - Problem index: `{governance / "problem-index.json"}`
        - Constraint index: `{governance / "constraint-index.json"}`
        - Value scales: `{risk / "global-value-scales.json"}`

        Output JSON only. The JSON must match the RiskAssessment schema.
    """), encoding="utf-8")
    return prompt_path


def _check_design_risk_valid_json(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "Agent output did not contain a JSON object"
    required = {
        "assessment_id", "layer", "package_id",
        "step_assessments", "dominant_risks",
    }
    missing = sorted(required - payload.keys())
    if missing:
        return False, f"Missing required keys: {missing}"
    if not isinstance(payload.get("step_assessments"), list) or not payload["step_assessments"]:
        return False, "step_assessments missing or empty"
    return True, f"layer={payload.get('layer')}"


def _check_design_risk_proprietary_higher(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    """Check that the proprietary option scores higher risk than PostgreSQL."""
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    steps = payload.get("step_assessments", [])
    if not isinstance(steps, list) or len(steps) < 2:
        return False, f"Expected 2 step assessments, got {len(steps) if isinstance(steps, list) else 0}"

    risk_by_id: dict[str, int] = {}
    for step in steps:
        if isinstance(step, dict):
            sid = step.get("step_id", "")
            raw = step.get("raw_risk", 0)
            risk_by_id[sid] = int(raw) if isinstance(raw, (int, float)) else 0

    proprietary = risk_by_id.get("platform-proprietary-db", 0)
    postgres = risk_by_id.get("component-postgres", 0)

    if proprietary > postgres:
        return True, f"proprietary={proprietary} > postgres={postgres}"
    return False, f"Expected proprietary risk > postgres risk, got proprietary={proprietary}, postgres={postgres}"


def _check_design_risk_design_facets(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    """Check that design facets appear in dominant risks."""
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"

    # Check package-level or step-level dominant risks for design facets
    design_facets = {
        "dependency_lock_in", "ecosystem_maturity", "integration_fit",
        "operability_cost", "evolution_flexibility", "scale_fit",
        "team_capability",
    }
    all_dominant: set[str] = set()
    pkg_dominant = payload.get("dominant_risks", [])
    if isinstance(pkg_dominant, list):
        all_dominant.update(pkg_dominant)
    for step in payload.get("step_assessments", []):
        if isinstance(step, dict):
            step_dominant = step.get("dominant_risks", [])
            if isinstance(step_dominant, list):
                all_dominant.update(step_dominant)

    found = all_dominant & design_facets
    if found:
        return True, f"Design facets in dominant_risks: {sorted(found)}"
    return False, f"No design facets in dominant_risks. Found: {sorted(all_dominant)}"


SCENARIOS = [
    Scenario(
        name="design_risk_valid_assessment",
        agent_file="risk-assessor.md",
        model_policy_key="risk_assessor",
        setup=_setup_design_risk,
        checks=[
            Check(
                description="Design risk assessment returns valid JSON",
                verify=_check_design_risk_valid_json,
            ),
            Check(
                description="Proprietary option scores higher risk than PostgreSQL",
                verify=_check_design_risk_proprietary_higher,
            ),
            Check(
                description="Design facets appear in dominant risks",
                verify=_check_design_risk_design_facets,
            ),
        ],
    ),
]
