"""Intake and trust boundary live-eval scenarios.

Dispatches intake agents and checks that governance candidates are correctly
classified, hypotheses coexist without premature promotion, and gaps/tensions
are surfaced.
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
# Scenario 1: Vague idea produces governance candidates
# ---------------------------------------------------------------------------

def _setup_vague_idea(planspace: Path, codespace: Path) -> Path:
    """Seed with a vague product idea and ask intake-triage to classify."""
    del codespace
    artifacts = planspace / "artifacts"
    (artifacts / "intake").mkdir(parents=True, exist_ok=True)

    prompt_path = artifacts / "intake-vague-idea-prompt.md"
    prompt_path.write_text(textwrap.dedent("""\
        # Intake Triage — Vague Product Idea

        ## Entry Point

        The user said: "I want to build a treasury management system."

        ## Instructions

        This is a **vague product idea** intake. The user has provided a
        product concept, not a problem frame. You must:

        1. Decompose this into candidate **problem** claims (not features)
        2. Identify implicit **philosophy** candidates
        3. Note any **value scale** questions (security, reliability, etc.)
        4. Create **hypothesis sets** for alternative problem framings
        5. Surface **gaps** — what governance is missing?
        6. Do NOT promote anything — everything stays as candidates

        The user said only: "I want to build a treasury management system."

        There are no governance documents. No philosophy. No constraints.
        No problem archive. This is a greenfield intake.

        ## Output

        Write JSON with:
        - `source_records`: list of source provenance records
        - `claims`: list of GovernanceClaim objects
        - `tensions`: list of TensionRecord objects (if any contradictions)
        - `gaps`: list of identified governance gaps
        - `hypothesis_sets`: list of HypothesisSet objects
    """), encoding="utf-8")
    return prompt_path


def _check_vague_idea_has_claims(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    claims = payload.get("claims", [])
    if not isinstance(claims, list) or len(claims) < 1:
        return False, f"Expected at least 1 claim, got {len(claims) if isinstance(claims, list) else 0}"
    return True, f"Found {len(claims)} claims"


def _check_vague_idea_no_promotion(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    claims = payload.get("claims", [])
    if not isinstance(claims, list):
        return False, "claims is not a list"
    for claim in claims:
        if isinstance(claim, dict):
            state = claim.get("claim_state", "")
            if state == "verified":
                return False, f"Claim {claim.get('claim_id')} was auto-promoted to verified"
    return True, "No claims auto-promoted"


def _check_vague_idea_has_problems(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    claims = payload.get("claims", [])
    if not isinstance(claims, list):
        return False, "claims is not a list"
    problem_claims = [
        c for c in claims
        if isinstance(c, dict) and c.get("claim_kind") == "problem"
    ]
    if not problem_claims:
        return False, "No problem-type claims extracted from vague idea"
    return True, f"Found {len(problem_claims)} problem claims"


# ---------------------------------------------------------------------------
# Scenario 2: Spec triage separates governance from strategy
# ---------------------------------------------------------------------------

def _setup_spec_triage(planspace: Path, codespace: Path) -> Path:
    """Seed with a mixed spec containing governance and strategy claims."""
    del codespace
    artifacts = planspace / "artifacts"
    (artifacts / "intake").mkdir(parents=True, exist_ok=True)

    prompt_path = artifacts / "intake-spec-triage-prompt.md"
    prompt_path.write_text(textwrap.dedent("""\
        # Claim Extraction — Mixed Spec Document

        ## Instructions

        Extract atomic governance claims from the following spec document.
        Classify each claim by type and determine promotability.

        ## Spec Document

        ```
        # Treasury Management System Design

        ## Philosophy
        Developer experience is our top priority. We believe in moving fast
        and shipping frequently.

        ## Requirements
        Organizations need real-time visibility into cash positions across
        multiple bank accounts. The system must handle at least 500
        concurrent users during peak hours.

        ## Architecture
        We should use microservices because they scale better. Each service
        will be deployed as a Docker container on Kubernetes. Use PostgreSQL
        for the primary database and Redis for caching.

        ## Security
        It needs to be secure. All API endpoints must require authentication.

        ## Performance
        Maximum response time of 200ms for dashboard queries. Use read
        replicas for analytics queries.
        ```

        ## Output

        Write JSON with:
        - `claims`: list of GovernanceClaim objects
        - `gaps`: list of identified gaps
        - `tensions`: list of TensionRecord objects
        - `source_records`: list of SourceRecord objects
    """), encoding="utf-8")
    return prompt_path


def _check_spec_has_promotable_and_nonpromotable(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    claims = payload.get("claims", [])
    if not isinstance(claims, list):
        return False, "claims is not a list"
    promotable = [c for c in claims if isinstance(c, dict) and c.get("promotable") is True]
    non_promotable = [c for c in claims if isinstance(c, dict) and c.get("promotable") is False]
    if not promotable:
        return False, "No promotable claims found"
    if not non_promotable:
        return False, "No non-promotable claims found (spec has both governance and strategy)"
    return True, f"promotable={len(promotable)}, non_promotable={len(non_promotable)}"


def _check_spec_proposals_not_promotable(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    claims = payload.get("claims", [])
    if not isinstance(claims, list):
        return False, "claims is not a list"
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        kind = claim.get("claim_kind", "")
        promotable = claim.get("promotable", False)
        if kind in ("proposal", "implementation_detail") and promotable:
            return False, f"Claim {claim.get('claim_id')} is {kind} but marked promotable"
    return True, "No proposals or implementation details marked as promotable"


# ---------------------------------------------------------------------------
# Scenario 3: Spec triage surfaces contradictions
# ---------------------------------------------------------------------------

def _setup_spec_contradictions(planspace: Path, codespace: Path) -> Path:
    """Seed with a spec containing explicit contradictions."""
    del codespace
    artifacts = planspace / "artifacts"
    (artifacts / "intake").mkdir(parents=True, exist_ok=True)

    prompt_path = artifacts / "intake-spec-contradictions-prompt.md"
    prompt_path.write_text(textwrap.dedent("""\
        # Claim Extraction — Spec with Contradictions

        ## Instructions

        Extract atomic governance claims from the following spec. Pay special
        attention to contradictions and gaps.

        ## Spec Document

        ```
        # Platform Design

        ## Values
        Developer experience is our number one priority. We optimize for
        developer velocity above all else.

        Maximum performance is required. Every microsecond counts. We will
        not tolerate any performance overhead from developer tooling or
        abstractions.

        ## Requirements
        The system must support 10 million daily active users.

        ## Notes
        We have a team of 2 junior developers. Timeline is 3 months.
        ```

        The spec contains a clear contradiction: "developer experience first"
        vs "maximum performance required" are in tension. Also note the gap:
        no security or compliance statement for a system serving 10M users.

        ## Output

        Write JSON with:
        - `claims`: list of GovernanceClaim objects
        - `gaps`: list of identified gaps
        - `tensions`: list of TensionRecord objects
        - `source_records`: list of SourceRecord objects
    """), encoding="utf-8")
    return prompt_path


def _check_contradictions_surfaced(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    tensions = payload.get("tensions", [])
    if not isinstance(tensions, list) or not tensions:
        return False, "No tensions/contradictions surfaced"
    return True, f"Found {len(tensions)} tension(s)"


def _check_gaps_identified(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    gaps = payload.get("gaps", [])
    if not isinstance(gaps, list) or not gaps:
        return False, "No governance gaps identified"
    return True, f"Found {len(gaps)} gap(s)"


# ---------------------------------------------------------------------------
# Scenario 4: Codebase governance assessment
# ---------------------------------------------------------------------------

def _setup_codebase_assessment(planspace: Path, codespace: Path) -> Path:
    """Seed with codebase observations for governance assessment."""
    del codespace
    artifacts = planspace / "artifacts"
    (artifacts / "intake").mkdir(parents=True, exist_ok=True)

    prompt_path = artifacts / "intake-codebase-prompt.md"
    prompt_path.write_text(textwrap.dedent("""\
        # Codebase Governance Assessment

        ## Instructions

        You are assessing an existing codebase for governance debt. Based on
        the observations below, produce governance candidate hypotheses and
        identify what governance is missing.

        ## Codebase Observations

        - **Language**: Python 3.12
        - **Framework**: FastAPI
        - **Database**: PostgreSQL with SQLAlchemy ORM
        - **Testing**: 847 pytest tests, 92% line coverage, contract tests
          for all API endpoints
        - **Architecture**: Monolith with clear module boundaries
        - **Dependencies**: 23 direct deps, all within 1 major version of latest
        - **Auth**: OAuth2 + JWT, role-based access control
        - **CI/CD**: GitHub Actions, automated deployment to AWS ECS
        - **No governance documents exist** — no philosophy, no problem
          archive, no constraints, no risk register

        ## Output

        Write JSON with:
        - `observations`: list of codebase observations with evidence
        - `hypotheses`: list of GovernanceClaim objects (all as candidates)
        - `governance_debt`: list of identified governance gaps
        - `minimum_governance_contract`: recommended MinimumGovernanceContract
    """), encoding="utf-8")
    return prompt_path


def _check_codebase_has_hypotheses(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    hypotheses = payload.get("hypotheses", [])
    if not isinstance(hypotheses, list) or not hypotheses:
        return False, "No governance hypotheses produced"
    return True, f"Found {len(hypotheses)} hypotheses"


def _check_codebase_hypotheses_are_candidates(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    hypotheses = payload.get("hypotheses", [])
    if not isinstance(hypotheses, list):
        return False, "hypotheses is not a list"
    for h in hypotheses:
        if isinstance(h, dict):
            state = h.get("claim_state", "")
            if state == "verified":
                return False, f"Hypothesis {h.get('claim_id')} auto-promoted to verified"
    return True, "All hypotheses remain as candidates"


def _check_codebase_governance_debt(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    del planspace, codespace
    payload = _extract_json_object(agent_output)
    if payload is None:
        return False, "No JSON payload found"
    debt = payload.get("governance_debt", [])
    if not isinstance(debt, list) or not debt:
        return False, "No governance debt identified (codebase has zero governance docs)"
    return True, f"Found {len(debt)} governance debt items"


# ---------------------------------------------------------------------------
# SCENARIOS
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="intake_vague_idea_produces_candidates",
        agent_file="intake-triage.md",
        model_policy_key="intake_triage",
        setup=_setup_vague_idea,
        checks=[
            Check(
                description="Vague idea produces governance candidate claims",
                verify=_check_vague_idea_has_claims,
            ),
            Check(
                description="No claims are auto-promoted to verified",
                verify=_check_vague_idea_no_promotion,
            ),
            Check(
                description="At least one problem-type claim is extracted",
                verify=_check_vague_idea_has_problems,
            ),
        ],
    ),
    Scenario(
        name="spec_triage_separates_governance_from_strategy",
        agent_file="claim-extractor.md",
        model_policy_key="claim_extractor",
        setup=_setup_spec_triage,
        checks=[
            Check(
                description="Spec produces both promotable and non-promotable claims",
                verify=_check_spec_has_promotable_and_nonpromotable,
            ),
            Check(
                description="Proposals and implementation details are not promotable",
                verify=_check_spec_proposals_not_promotable,
            ),
        ],
    ),
    Scenario(
        name="spec_triage_surfaces_contradictions",
        agent_file="claim-extractor.md",
        model_policy_key="claim_extractor",
        setup=_setup_spec_contradictions,
        checks=[
            Check(
                description="Contradictions between claims are surfaced as tensions",
                verify=_check_contradictions_surfaced,
            ),
            Check(
                description="Governance gaps are identified",
                verify=_check_gaps_identified,
            ),
        ],
    ),
    Scenario(
        name="codebase_governance_assessment",
        agent_file="codebase-governance-assessor.md",
        model_policy_key="codebase_governance_assessor",
        setup=_setup_codebase_assessment,
        checks=[
            Check(
                description="Codebase analysis produces governance hypotheses",
                verify=_check_codebase_has_hypotheses,
            ),
            Check(
                description="All hypotheses remain as candidates, not verified",
                verify=_check_codebase_hypotheses_are_candidates,
            ),
            Check(
                description="Governance debt is identified for codebase with no governance",
                verify=_check_codebase_governance_debt,
            ),
        ],
    ),
]
