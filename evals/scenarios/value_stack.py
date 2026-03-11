"""Value scale and stack evaluation live-eval scenarios.

Dispatches value-scale-enumerator and stack-evaluator agents and checks
that value ladders are properly enumerated and stack options are compared
with governance fit.
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


# ---------------------------------------------------------------------------
# Scenario: Value scale expands vague security requirement
# ---------------------------------------------------------------------------

def _setup_value_scale(planspace: Path, codespace: Path) -> Path:
    """Seed with a vague security requirement for value scale expansion."""
    del codespace
    artifacts = planspace / "artifacts"
    (artifacts / "risk").mkdir(parents=True, exist_ok=True)
    (artifacts / "governance").mkdir(parents=True, exist_ok=True)

    # Write governance context
    (artifacts / "governance" / "problem-index.json").write_text(
        json.dumps([{
            "problem_id": "PROB-treasury",
            "title": "Organizations need real-time cash position visibility",
            "status": "verified",
        }], indent=2) + "\n",
        encoding="utf-8",
    )
    (artifacts / "governance" / "profile-index.json").write_text(
        json.dumps([{
            "profile_id": "correctness-first",
            "values": ["Correctness", "Reliability", "Security"],
            "failure_mode": "Prefer safe failure over data corruption",
            "risk_posture": "Conservative — verify before acting",
        }], indent=2) + "\n",
        encoding="utf-8",
    )

    prompt_path = artifacts / "value-scale-eval-prompt.md"
    prompt_path.write_text(textwrap.dedent("""\
        # Value Scale Enumeration — Security

        ## Instructions

        The user said: "It needs to be secure." This is a vague value
        requirement. Expand it into an explicit scale ladder.

        ## Context

        - **Problem**: Organizations need real-time cash position visibility
          across multiple bank accounts (financial domain)
        - **Philosophy**: Correctness-first — prefer safe failure over data
          corruption, conservative risk posture
        - **No security level has been selected yet**

        ## Task

        Enumerate a security value scale with 3-5 levels. For each level:
        - Label and intended outcomes
        - Direct costs
        - Cascading side-effect costs (bounded depth 2-3)
        - Required capabilities
        - Reassessment triggers

        Suggest a level based on the problem and philosophy, but do NOT
        auto-select it as verified.

        ## Output

        Write JSON matching the ValueScale schema:
        ```json
        {
          "value_id": "security",
          "scope": "global",
          "levels": [
            {
              "value_id": "security",
              "level": 0,
              "label": "...",
              "intended_outcomes": ["..."],
              "direct_costs": ["..."],
              "cascades": [{"effect_id": "...", "description": "...", "severity": 0, "children": []}],
              "required_capabilities": ["..."],
              "reassessment_triggers": ["..."]
            }
          ],
          "suggested_level": null,
          "suggested_rationale": "...",
          "selected_level": null,
          "selected_state": "candidate"
        }
        ```
    """), encoding="utf-8")
    return prompt_path


def _check_value_scale_has_levels(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    levels = payload.get("levels", [])
    if not isinstance(levels, list) or len(levels) < 3:
        return False, f"Expected at least 3 levels, got {len(levels) if isinstance(levels, list) else 0}"
    return True, f"Found {len(levels)} levels"


def _check_value_scale_has_costs(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    levels = payload.get("levels", [])
    if not isinstance(levels, list):
        return False, "levels is not a list"
    for level in levels:
        if not isinstance(level, dict):
            continue
        costs = level.get("direct_costs", [])
        if isinstance(costs, list) and costs:
            return True, "At least one level has direct costs"
    return False, "No levels have direct costs populated"


def _check_value_scale_not_auto_verified(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    selected_state = payload.get("selected_state", "")
    if selected_state == "verified":
        return False, "Value scale was auto-selected as verified"
    selected = payload.get("selected_level")
    if selected is not None:
        return False, f"selected_level should be null, got {selected}"
    return True, "Value scale remains as candidate, not auto-verified"


# ---------------------------------------------------------------------------
# Scenario: Stack eval compares options against value scale
# ---------------------------------------------------------------------------

def _setup_stack_eval(planspace: Path, codespace: Path) -> Path:
    """Seed with verified governance and ask for stack comparison."""
    del codespace
    artifacts = planspace / "artifacts"
    (artifacts / "risk").mkdir(parents=True, exist_ok=True)
    (artifacts / "governance").mkdir(parents=True, exist_ok=True)

    # Write governance context
    (artifacts / "governance" / "problem-index.json").write_text(
        json.dumps([{
            "problem_id": "PROB-treasury",
            "title": "Organizations need real-time cash position visibility",
            "status": "verified",
        }], indent=2) + "\n",
        encoding="utf-8",
    )
    (artifacts / "governance" / "constraint-index.json").write_text(
        json.dumps([
            {
                "constraint_id": "CON-001",
                "title": "Must support multi-cloud deployment",
                "status": "verified",
            },
            {
                "constraint_id": "CON-002",
                "title": "Team has strong PostgreSQL expertise, no DynamoDB experience",
                "status": "verified",
            },
        ], indent=2) + "\n",
        encoding="utf-8",
    )

    # Write selected value scales
    (artifacts / "risk" / "global-value-scales.json").write_text(
        json.dumps([
            {
                "value_id": "security",
                "scope": "global",
                "levels": [
                    {"value_id": "security", "level": 0, "label": "No auth"},
                    {"value_id": "security", "level": 1, "label": "Basic auth"},
                    {"value_id": "security", "level": 2, "label": "OAuth + RBAC"},
                    {"value_id": "security", "level": 3, "label": "Zero-trust + MFA"},
                ],
                "suggested_level": 2,
                "selected_level": 2,
                "selected_state": "verified",
            },
            {
                "value_id": "reliability",
                "scope": "global",
                "levels": [
                    {"value_id": "reliability", "level": 0, "label": "Best effort"},
                    {"value_id": "reliability", "level": 1, "label": "Basic HA"},
                    {"value_id": "reliability", "level": 2, "label": "Multi-AZ failover"},
                ],
                "suggested_level": 2,
                "selected_level": 2,
                "selected_state": "verified",
            },
        ], indent=2) + "\n",
        encoding="utf-8",
    )

    prompt_path = artifacts / "stack-eval-prompt.md"
    prompt_path.write_text(textwrap.dedent("""\
        # Stack Evaluation — Primary Database

        ## Instructions

        Evaluate 3 database options for the primary database decision area.
        Compare them against the verified governance context.

        ## Governance Context

        - **Problem**: Real-time cash position visibility (financial domain)
        - **Constraints**:
          - Must support multi-cloud deployment
          - Team has strong PostgreSQL expertise, no DynamoDB experience
        - **Selected value scales**:
          - Security: OAuth + RBAC (level 2, verified)
          - Reliability: Multi-AZ failover (level 2, verified)

        ## Options to Evaluate

        1. **PostgreSQL** — Open-source relational database, strong SQL,
           multi-cloud compatible, team has deep expertise
        2. **Amazon DynamoDB** — AWS-proprietary NoSQL, single-cloud lock-in,
           team has no experience, eventual consistency model
        3. **CockroachDB** — Distributed SQL, multi-cloud compatible,
           PostgreSQL wire protocol, team would need some training

        ## Task

        For each option, evaluate:
        - Governance fit (does it serve verified problems and constraints?)
        - Design risk profile
        - Value-scale compatibility
        - Migration/exit path
        - Execution implications

        ## Output

        Write JSON matching the StackEvaluation schema:
        ```json
        {
          "decision_area": "primary-database",
          "scope": "global",
          "governing_problem_ids": ["PROB-treasury"],
          "governing_constraint_ids": ["CON-001", "CON-002"],
          "options": [
            {
              "option_id": "O1",
              "summary": "...",
              "decision_class": "platform",
              "governance_fit": {},
              "value_scale_interactions": [],
              "migration_path": ""
            }
          ],
          "recommended_option_ids": [],
          "blocked_reasons": [],
          "status": "assessed"
        }
        ```

        Stack choices are proposals, NOT governance. The recommended option
        should be stored as a design decision, not promoted to governance.
    """), encoding="utf-8")
    return prompt_path


def _check_stack_eval_has_options(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    options = payload.get("options", [])
    if not isinstance(options, list) or len(options) < 2:
        return False, f"Expected at least 2 options, got {len(options) if isinstance(options, list) else 0}"
    return True, f"Found {len(options)} options"


def _check_stack_eval_has_recommendation(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    recommended = payload.get("recommended_option_ids", [])
    if not isinstance(recommended, list) or not recommended:
        return False, "No recommendations made"
    return True, f"Recommended: {recommended}"


def _check_stack_eval_dynamodb_not_recommended(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """DynamoDB violates multi-cloud constraint and team capability."""
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    recommended = payload.get("recommended_option_ids", [])
    if not isinstance(recommended, list):
        return False, "recommended_option_ids is not a list"
    # Check that DynamoDB-related option is not recommended
    blocked = payload.get("blocked_reasons", [])
    for oid in recommended:
        if isinstance(oid, str) and "dynamo" in oid.lower():
            return False, f"DynamoDB option {oid} was recommended despite constraint violations"
    # Also check: if DynamoDB is in options but NOT recommended, that's good
    options = payload.get("options", [])
    dynamo_options = [
        o for o in options
        if isinstance(o, dict) and "dynamo" in o.get("summary", "").lower()
    ]
    if dynamo_options:
        dynamo_ids = {o.get("option_id", "") for o in dynamo_options}
        if dynamo_ids & set(recommended):
            return False, "DynamoDB recommended despite constraint violations"
    return True, "DynamoDB not in recommended options"


SCENARIOS = [
    Scenario(
        name="value_scale_expands_vague_security",
        agent_file="value-scale-enumerator.md",
        model_policy_key="value_scale_enumerator",
        setup=_setup_value_scale,
        checks=[
            Check(
                description="Value scale has at least 3 levels",
                verify=_check_value_scale_has_levels,
            ),
            Check(
                description="At least one level has direct costs",
                verify=_check_value_scale_has_costs,
            ),
            Check(
                description="No level is auto-selected as verified",
                verify=_check_value_scale_not_auto_verified,
            ),
        ],
    ),
    Scenario(
        name="stack_eval_compares_options",
        agent_file="stack-evaluator.md",
        model_policy_key="stack_evaluator",
        setup=_setup_stack_eval,
        checks=[
            Check(
                description="Stack evaluation produces at least 2 options",
                verify=_check_stack_eval_has_options,
            ),
            Check(
                description="At least one option is recommended",
                verify=_check_stack_eval_has_recommendation,
            ),
            Check(
                description="DynamoDB is not recommended (violates constraints)",
                verify=_check_stack_eval_dynamodb_not_recommended,
            ),
        ],
    ),
]
