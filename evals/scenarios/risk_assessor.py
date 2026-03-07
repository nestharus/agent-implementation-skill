"""Risk assessor live-eval scenarios.

Dispatches the real `risk-assessor.md` agent with bounded ROAL prompts and
checks that it returns valid `RiskAssessment` JSON plus conservative signals
when the fixture contains obvious stale/high-risk evidence.
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


def _write_common_artifacts(
    planspace: Path,
    *,
    section: str,
    package_payload: dict[str, object],
    readiness_ready: bool,
    monitor_payloads: dict[str, dict[str, object]],
    proposal_state: dict[str, object],
    consequence_text: str,
    impact_text: str,
    codemap_corrections: dict[str, object] | None = None,
    risk_history_lines: list[dict[str, object]] | None = None,
) -> dict[str, Path]:
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    readiness = artifacts / "readiness"
    signals = artifacts / "signals"
    notes = artifacts / "notes"
    coordination = artifacts / "coordination"
    risk = artifacts / "risk"

    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    readiness.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    notes.mkdir(parents=True, exist_ok=True)
    coordination.mkdir(parents=True, exist_ok=True)
    risk.mkdir(parents=True, exist_ok=True)

    section_num = section.removeprefix("section-")
    section_spec = sections / f"{section}.md"
    proposal_excerpt = sections / f"{section}-proposal-excerpt.md"
    alignment_excerpt = sections / f"{section}-alignment-excerpt.md"
    problem_frame = sections / f"{section}-problem-frame.md"
    microstrategy = proposals / f"{section}-microstrategy.md"
    proposal_state_path = proposals / f"{section}-proposal-state.json"
    readiness_path = readiness / f"{section}-execution-ready.json"
    tool_registry = artifacts / "tool-registry.json"
    codemap = artifacts / "codemap.md"
    corrections = signals / "codemap-corrections.json"
    consequence_note = notes / f"{section}-consequence.md"
    impact_artifact = coordination / f"{section}-impact.md"
    risk_package = risk / f"{section}-risk-package.json"
    risk_history = risk / "risk-history.jsonl"

    section_spec.write_text(textwrap.dedent(f"""\
        # Section {section_num}: ROAL Fixture

        ## Problem
        Update the execution flow for `{section}` while preserving alignment
        and verification fidelity.
    """), encoding="utf-8")
    proposal_excerpt.write_text(
        "Proposal excerpt: tighten risk-managed execution around the approved slice.\n",
        encoding="utf-8",
    )
    alignment_excerpt.write_text(
        "Alignment excerpt: execution must preserve shared contract behavior.\n",
        encoding="utf-8",
    )
    problem_frame.write_text(
        "Problem frame: stale or conflicting execution context would be dangerous.\n",
        encoding="utf-8",
    )
    microstrategy.write_text(
        "- Refresh package understanding\n"
        "- Apply the approved implementation slice\n"
        "- Verify the result and impacted seams\n",
        encoding="utf-8",
    )
    proposal_state_path.write_text(
        json.dumps(proposal_state, indent=2) + "\n",
        encoding="utf-8",
    )
    readiness_path.write_text(
        json.dumps(
            {
                "ready": readiness_ready,
                "blockers": [] if readiness_ready else ["stale upstream context"],
                "rationale": "fixture",
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    tool_registry.write_text(
        json.dumps({"tools": ["pytest", "rg"], "bridge_tools": []}, indent=2) + "\n",
        encoding="utf-8",
    )
    codemap.write_text(textwrap.dedent("""\
        # Project Codemap

        ## app/
        - `app/service.py` - main service behavior
        - `app/contracts.py` - shared contract surface

        ## tests/
        - `tests/test_service.py` - verification surface
    """), encoding="utf-8")
    if codemap_corrections is not None:
        corrections.write_text(
            json.dumps(codemap_corrections, indent=2) + "\n",
            encoding="utf-8",
        )
    consequence_note.write_text(consequence_text, encoding="utf-8")
    impact_artifact.write_text(impact_text, encoding="utf-8")
    risk_package.write_text(
        json.dumps(package_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    if risk_history_lines:
        risk_history.write_text(
            "\n".join(json.dumps(item) for item in risk_history_lines) + "\n",
            encoding="utf-8",
        )
    for name, payload in monitor_payloads.items():
        (signals / name).write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "section_spec": section_spec,
        "proposal_excerpt": proposal_excerpt,
        "alignment_excerpt": alignment_excerpt,
        "problem_frame": problem_frame,
        "microstrategy": microstrategy,
        "proposal_state": proposal_state_path,
        "readiness": readiness_path,
        "tool_registry": tool_registry,
        "codemap": codemap,
        "corrections": corrections,
        "risk_history": risk_history,
        "signals": signals,
        "consequence": consequence_note,
        "impact": impact_artifact,
        "risk_package": risk_package,
    }


def _write_prompt(
    planspace: Path,
    *,
    section: str,
    package_payload: dict[str, object],
    paths: dict[str, Path],
    extra_instructions: str,
) -> Path:
    prompt_path = planspace / "artifacts" / f"{section}-risk-assessor-eval-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # ROAL Risk Assessment

        - Scope: `{section}`
        - Layer: `implementation`
        - Package ID: `{package_payload["package_id"]}`

        ## Risk Package

        ```json
        {json.dumps(package_payload, indent=2)}
        ```

        ## Artifact Paths

        Read these artifacts for context:

        - Section spec: `{paths["section_spec"]}`
        - Proposal excerpt: `{paths["proposal_excerpt"]}`
        - Alignment excerpt: `{paths["alignment_excerpt"]}`
        - Problem frame: `{paths["problem_frame"]}`
        - Microstrategy: `{paths["microstrategy"]}`
        - Proposal state: `{paths["proposal_state"]}`
        - Readiness: `{paths["readiness"]}`
        - Tool registry: `{paths["tool_registry"]}`
        - Codemap: `{paths["codemap"]}`
        - Codemap corrections (authoritative overrides): `{paths["corrections"]}`
        - Risk history: `{paths["risk_history"]}`
        - Monitor signals directory: `{paths["signals"]}`
        - Consequence notes: `{paths["consequence"]}`
        - Impact artifacts: `{paths["impact"]}`

        {extra_instructions}

        Output JSON only. The JSON must match the RiskAssessment schema.
    """), encoding="utf-8")
    return prompt_path


def _setup_valid_assessment(planspace: Path, codespace: Path) -> Path:
    del codespace
    package_payload = {
        "package_id": "pkg-implementation-section-31",
        "layer": "implementation",
        "scope": "section-31",
        "origin_problem_id": "section-31:proposal",
        "origin_source": "proposal",
        "steps": [
            {
                "step_id": "edit-01",
                "step_class": "edit",
                "summary": "Apply the approved execution fix",
                "prerequisites": [],
                "expected_outputs": ["code-or-artifact-update"],
                "expected_resolutions": ["approved change applied"],
                "mutation_surface": ["ServiceContract"],
                "verification_surface": [],
                "reversibility": "medium",
            }
        ],
    }
    paths = _write_common_artifacts(
        planspace,
        section="section-31",
        package_payload=package_payload,
        readiness_ready=True,
        monitor_payloads={"section-31-monitor.json": {"signal": "OK"}},
        proposal_state={
            "resolved_anchors": ["service.handler"],
            "unresolved_anchors": [],
            "resolved_contracts": ["ServiceContract"],
            "unresolved_contracts": [],
            "research_questions": [],
            "blocking_research_questions": [],
            "user_root_questions": [],
            "new_section_candidates": [],
            "shared_seam_candidates": [],
            "execution_ready": True,
            "readiness_rationale": "ready",
        },
        consequence_text="Consequence note: bounded local change.\n",
        impact_text="Impact artifact: no cross-section coordination expected.\n",
    )
    return _write_prompt(
        planspace,
        section="section-31",
        package_payload=package_payload,
        paths=paths,
        extra_instructions=(
            "Treat this as a bounded single-step execution package with fresh "
            "inputs and no known structural conflicts."
        ),
    )


def _setup_high_risk_assessment(planspace: Path, codespace: Path) -> Path:
    del codespace
    package_payload = {
        "package_id": "pkg-implementation-section-32",
        "layer": "implementation",
        "scope": "section-32",
        "origin_problem_id": "section-32:proposal",
        "origin_source": "proposal",
        "steps": [
            {
                "step_id": "explore-01",
                "step_class": "explore",
                "summary": "Refresh the stale execution context",
                "prerequisites": [],
                "expected_outputs": ["refreshed-understanding"],
                "expected_resolutions": ["unknowns reduced"],
                "mutation_surface": [],
                "verification_surface": [],
                "reversibility": "high",
            },
            {
                "step_id": "edit-02",
                "step_class": "edit",
                "summary": "Mutate the shared contract implementation across multiple files",
                "prerequisites": ["explore-01"],
                "expected_outputs": ["code-or-artifact-update"],
                "expected_resolutions": ["approved change applied"],
                "mutation_surface": ["SharedContract", "RouterBinding"],
                "verification_surface": [],
                "reversibility": "medium",
            },
            {
                "step_id": "verify-03",
                "step_class": "verify",
                "summary": "Verify all impacted contract consumers",
                "prerequisites": ["edit-02"],
                "expected_outputs": ["verification-result"],
                "expected_resolutions": ["alignment confirmed"],
                "mutation_surface": [],
                "verification_surface": ["tests/test_service.py", "tests/test_router.py"],
                "reversibility": "high",
            },
        ],
    }
    paths = _write_common_artifacts(
        planspace,
        section="section-32",
        package_payload=package_payload,
        readiness_ready=False,
        monitor_payloads={
            "section-32-monitor-loop.json": {"signal": "LOOP_DETECTED"},
            "section-32-monitor-stalled.json": {"signal": "STALLED"},
        },
        proposal_state={
            "resolved_anchors": ["service.handler"],
            "unresolved_anchors": ["router.binding"],
            "resolved_contracts": [],
            "unresolved_contracts": ["SharedContract"],
            "research_questions": ["Which downstream consumers still rely on the old contract?"],
            "blocking_research_questions": ["Is the readiness artifact stale after reconciliation?"],
            "user_root_questions": [],
            "new_section_candidates": [],
            "shared_seam_candidates": ["router/service boundary"],
            "execution_ready": False,
            "readiness_rationale": "stale and cross-section-sensitive",
        },
        consequence_text=(
            "Consequence note: this section now affects two downstream sections "
            "sharing the same contract.\n"
        ),
        impact_text="Impact artifact: coordination needed before shared-contract mutation.\n",
        codemap_corrections={
            "router.py": "authoritative owner of RouterBinding; codemap body is stale",
        },
        risk_history_lines=[
            {
                "package_id": "pkg-implementation-section-32",
                "step_id": "edit-02",
                "layer": "implementation",
                "step_class": "edit",
                "posture": "P2",
                "predicted_risk": 55,
                "actual_outcome": "failure",
                "surfaced_surprises": ["stale readiness artifact"],
                "verification_outcome": "failed",
                "dominant_risks": [
                    "stale_artifact_contamination",
                    "cross_section_incoherence",
                ],
                "blast_radius_band": 3,
            }
        ],
    )
    return _write_prompt(
        planspace,
        section="section-32",
        package_payload=package_payload,
        paths=paths,
        extra_instructions=(
            "High-risk indicators are intentional in this fixture: multiple "
            "steps, stale readiness, loop/stall signals, and a shared contract "
            "that spans sections. Reflect those signals in the assessment."
        ),
    )


def _check_assessment_json(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "Agent output did not contain a JSON object"
    required = {
        "assessment_id",
        "layer",
        "package_id",
        "assessment_scope",
        "understanding_inventory",
        "package_raw_risk",
        "assessment_confidence",
        "dominant_risks",
        "step_assessments",
        "frontier_candidates",
    }
    missing = sorted(required - payload.keys())
    if missing:
        return False, f"Missing required keys: {missing}"
    if not isinstance(payload.get("step_assessments"), list) or not payload["step_assessments"]:
        return False, "step_assessments missing or empty"
    return True, f"assessment_scope={payload.get('assessment_scope')}"


def _check_assessment_scope(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    if payload.get("assessment_scope") != "section-31":
        return False, f"Unexpected scope: {payload.get('assessment_scope')}"
    if payload.get("package_id") != "pkg-implementation-section-31":
        return False, f"Unexpected package_id: {payload.get('package_id')}"
    return True, "scope and package_id preserved"


def _check_high_risk_flags(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    dominant = set(payload.get("dominant_risks", []))
    expected = {
        "stale_artifact_contamination",
        "cross_section_incoherence",
        "brute_force_regression",
        "context_rot",
    }
    if dominant & expected:
        return True, f"dominant_risks={sorted(dominant)}"
    return False, f"Expected dominant risks to intersect {sorted(expected)}, got {sorted(dominant)}"


def _check_high_risk_is_nontrivial(
    planspace: Path,
    codespace: Path,
    agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    package_raw_risk = payload.get("package_raw_risk")
    if isinstance(package_raw_risk, int) and package_raw_risk >= 50:
        return True, f"package_raw_risk={package_raw_risk}"
    reopen = payload.get("reopen_recommendations", [])
    if isinstance(reopen, list) and reopen:
        return True, f"reopen_recommendations={len(reopen)}"
    return False, f"Expected package_raw_risk>=50 or reopen recommendations, got {package_raw_risk}"


SCENARIOS = [
    Scenario(
        name="risk_assessor_valid_json",
        agent_file="risk-assessor.md",
        model_policy_key="risk_assessor",
        setup=_setup_valid_assessment,
        checks=[
            Check(
                description="Risk assessor emits valid RiskAssessment JSON",
                verify=_check_assessment_json,
            ),
            Check(
                description="Risk assessor preserves package scope and package_id",
                verify=_check_assessment_scope,
            ),
        ],
    ),
    Scenario(
        name="risk_assessor_high_risk",
        agent_file="risk-assessor.md",
        model_policy_key="risk_assessor",
        setup=_setup_high_risk_assessment,
        checks=[
            Check(
                description="High-risk fixture produces dominant risk flags",
                verify=_check_high_risk_flags,
            ),
            Check(
                description="High-risk fixture does not collapse to trivial risk",
                verify=_check_high_risk_is_nontrivial,
            ),
        ],
    ),
]
