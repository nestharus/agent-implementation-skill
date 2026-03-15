"""Post-implementation assessor live-eval scenarios.

Dispatches the real `post-implementation-assessor.md` agent with seeded
section artifacts and checks that it returns a structured assessment JSON
with the correct verdict given clean vs. risky implementation fixtures.

Scenarios:
  post_assessment_clean: Clean implementation -> verdict="accept"
  post_assessment_risky: Risky implementation -> verdict="accept_with_debt"|"refactor_required"
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_object(agent_output: str) -> dict[str, object] | None:
    """Extract the first JSON object from agent output.

    Tries: raw JSON, fenced JSON block, brace-delimited substring.
    """
    candidate = agent_output.strip()
    if not candidate:
        return None
    # Try raw JSON
    try:
        payload = json.loads(candidate)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    # Try fenced block
    fenced = _JSON_FENCE_RE.search(candidate)
    if fenced is not None:
        try:
            payload = json.loads(fenced.group(1))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    # Try brace-delimited substring
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(candidate[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_GOVERNANCE_PACKET = textwrap.dedent("""\
    # Governance Packet

    ## Problem IDs
    - PRB-0042: API response handler does not validate output schema

    ## Pattern IDs
    - PAT-0003: All service outputs must pass schema validation
    - PAT-0011: Error responses use the standard ErrorEnvelope type

    ## Profile
    - PHI-global
""")

_TRACE_MAP_CLEAN = textwrap.dedent("""\
    # Trace Map: Section 09

    ## Modified Files
    - `api/handlers/response_validator.py` (NEW)
    - `api/handlers/base.py` (MODIFIED)
    - `tests/test_response_validator.py` (NEW)

    ## Seam Impact
    - api/handlers/base.py -> api/handlers/response_validator.py (new call)
    - No cross-section seams affected
""")

_TRACE_MAP_RISKY = textwrap.dedent("""\
    # Trace Map: Section 10

    ## Modified Files
    - `core/auth/session_manager.py` (MODIFIED)
    - `core/auth/token_store.py` (MODIFIED)
    - `api/middleware/auth_middleware.py` (MODIFIED)
    - `core/config/secrets.py` (MODIFIED)
    - `api/routes/admin.py` (MODIFIED)
    - `tests/test_auth.py` (MODIFIED)

    ## Seam Impact
    - core/auth/session_manager.py -> api/middleware/auth_middleware.py (tight coupling)
    - core/config/secrets.py -> core/auth/token_store.py (credential flow)
    - api/routes/admin.py -> core/auth/session_manager.py (direct import)
    - Cross-section: section-03 shares auth_middleware dependency
""")


# ---------------------------------------------------------------------------
# Setup: clean scenario
# ---------------------------------------------------------------------------

def _setup_clean(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for a clean, straightforward implementation."""
    del codespace
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_spec = sections / "section-09.md"
    section_spec.write_text(textwrap.dedent("""\
        # Section 09: Response Validation

        ## Problem
        API handlers return raw dicts without schema validation.  Clients
        receive inconsistent shapes on error paths.  Add a response
        validator that enforces the output schema before serialization.

        ## Requirements
        - REQ-01: Validate all handler responses against the declared schema
        - REQ-02: Return a standard ErrorEnvelope on validation failure
        - REQ-03: Log validation failures with request context
    """), encoding="utf-8")

    # Problem frame
    problem_frame = sections / "section-09-problem-frame.md"
    problem_frame.write_text(textwrap.dedent("""\
        # Problem Frame: Section 09

        Root cause: handlers bypass schema checks.  The fix is a
        decorator-based validator in the handler base class.  Bounded
        scope: no cross-section impact expected.
    """), encoding="utf-8")

    # Integration proposal
    proposal = proposals / "section-09-integration-proposal.md"
    proposal.write_text(textwrap.dedent("""\
        # Integration Proposal: Section 09

        ## Proposed Changes
        - Add `ResponseValidator` class to `api/handlers/response_validator.py`
        - Modify `api/handlers/base.py` to call the validator in `finalize()`
        - Add tests for validation pass-through and rejection

        ## Integration Points
        - Resolved anchor: `api.handlers.base.finalize`
        - Resolved contract: `ErrorEnvelope` schema
    """), encoding="utf-8")

    # Implementation output (clean, well-scoped)
    impl_output = proposals / "section-09-implementation-output.md"
    impl_output.write_text(textwrap.dedent("""\
        # Implementation Output: Section 09

        ## Changes Made

        ### api/handlers/response_validator.py (NEW)
        Created `ResponseValidator` that validates handler return values
        against the declared output schema.  On validation failure, it
        returns a standard `ErrorEnvelope` with field-level error details
        and logs the validation error with request context.

        ### api/handlers/base.py (MODIFIED)
        Added a call to `ResponseValidator.validate()` in the `finalize()`
        method.  All subclass handlers now pass through validation before
        the response is serialized.

        ### tests/test_response_validator.py (NEW)
        Unit tests covering:
        - Valid response passes through unchanged
        - Invalid response returns ErrorEnvelope with correct fields
        - Validation failure is logged with request ID

        ## Verification
        - All existing handler tests pass unchanged
        - New validator tests cover happy path and error paths
        - No cross-section seams were affected
    """), encoding="utf-8")

    # Governance packet
    governance = sections / "section-09-governance.md"
    governance.write_text(_GOVERNANCE_PACKET, encoding="utf-8")

    # Trace map
    trace_map = sections / "section-09-trace-map.md"
    trace_map.write_text(_TRACE_MAP_CLEAN, encoding="utf-8")

    # Build prompt
    prompt_path = artifacts / "post-assessment-09-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Post-Implementation Assessment: Section 09

        You are the post-implementation assessor.  Evaluate the landed
        changes for governance-visible debt, pattern drift, coupling
        issues, and security concerns.

        ## Section Spec

        {section_spec.read_text(encoding="utf-8")}

        ## Implementation Output

        {impl_output.read_text(encoding="utf-8")}

        ## Trace Map

        {trace_map.read_text(encoding="utf-8")}

        ## Problem Frame

        {problem_frame.read_text(encoding="utf-8")}

        ## Integration Proposal

        {proposal.read_text(encoding="utf-8")}

        ## Governance Packet

        {governance.read_text(encoding="utf-8")}

        ## Instructions

        Produce a JSON assessment matching the schema from your agent
        instructions.  The "section" field should be "09".  Reference
        governance IDs (PRB-*, PAT-*) where applicable.

        Output JSON only.
    """), encoding="utf-8")
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: risky scenario
# ---------------------------------------------------------------------------

def _setup_risky(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for an implementation with coupling and security concerns."""
    del codespace
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_spec = sections / "section-10.md"
    section_spec.write_text(textwrap.dedent("""\
        # Section 10: Auth Session Hardening

        ## Problem
        Session tokens are stored in plain text and the session manager is
        tightly coupled to the HTTP middleware layer.  Harden the token
        store and decouple the session manager from transport concerns.

        ## Requirements
        - REQ-01: Encrypt session tokens at rest
        - REQ-02: Decouple session manager from HTTP middleware
        - REQ-03: No credential literals in source files
        - REQ-04: Admin routes must not bypass session validation
    """), encoding="utf-8")

    # Problem frame
    problem_frame = sections / "section-10-problem-frame.md"
    problem_frame.write_text(textwrap.dedent("""\
        # Problem Frame: Section 10

        Root cause: session tokens stored in cleartext, session logic
        entangled with HTTP middleware.  High security surface.
        Cross-section: section-03 shares the auth middleware.
    """), encoding="utf-8")

    # Integration proposal
    proposal = proposals / "section-10-integration-proposal.md"
    proposal.write_text(textwrap.dedent("""\
        # Integration Proposal: Section 10

        ## Proposed Changes
        - Encrypt tokens in `core/auth/token_store.py` using envelope encryption
        - Extract session logic from middleware into `core/auth/session_manager.py`
        - Remove credential literals from config; use env-var injection
        - Add session validation guard to admin routes

        ## Integration Points
        - Resolved anchor: `core.auth.session_manager`
        - Shared contract: `AuthMiddleware` (also used by section-03)

        ## Failure Modes
        - Credential exposure if secrets.py still contains literals
        - Silent auth bypass if admin routes skip validation
    """), encoding="utf-8")

    # Implementation output (risky: tight coupling + exposed credentials)
    impl_output = proposals / "section-10-implementation-output.md"
    impl_output.write_text(textwrap.dedent("""\
        # Implementation Output: Section 10

        ## Changes Made

        ### core/auth/session_manager.py (MODIFIED)
        Refactored session logic but introduced direct imports from
        `api.middleware.auth_middleware` for convenience, creating a
        circular dependency between the session layer and the HTTP layer.
        The session manager now calls `auth_middleware.get_current_user()`
        directly instead of accepting an abstract interface.

        ### core/auth/token_store.py (MODIFIED)
        Added AES-256 encryption for tokens at rest.  However, the
        encryption key is derived from a hardcoded salt in the file:
        `ENCRYPTION_SALT = "d3adb33f-static-salt-value"`.

        ### core/config/secrets.py (MODIFIED)
        Moved most credentials to environment variables, but left a
        fallback that reads `DB_PASSWORD` from a hardcoded default:
        `DB_PASSWORD = os.getenv("DB_PASSWORD", "admin123")`.

        ### api/middleware/auth_middleware.py (MODIFIED)
        Updated to call the new token_store encryption API.  Added a
        `skip_auth` parameter that admin routes use to bypass session
        validation entirely.

        ### api/routes/admin.py (MODIFIED)
        Admin routes now pass `skip_auth=True` to the middleware,
        effectively disabling session validation for all admin endpoints.

        ### tests/test_auth.py (MODIFIED)
        Updated tests for new encryption flow.  No tests for the
        `skip_auth` bypass path.

        ## Verification
        - Existing auth tests pass
        - Manual test of token encryption/decryption
        - No test coverage for admin bypass behavior
    """), encoding="utf-8")

    # Governance packet
    governance = sections / "section-10-governance.md"
    governance.write_text(textwrap.dedent("""\
        # Governance Packet

        ## Problem IDs
        - PRB-0051: Session tokens stored in cleartext
        - PRB-0052: Session manager coupled to HTTP transport

        ## Pattern IDs
        - PAT-0003: All service outputs must pass schema validation
        - PAT-0007: No credential literals in source files
        - PAT-0009: Security-sensitive changes require explicit bypass audit

        ## Profile
        - PHI-global
    """), encoding="utf-8")

    # Trace map
    trace_map = sections / "section-10-trace-map.md"
    trace_map.write_text(_TRACE_MAP_RISKY, encoding="utf-8")

    # Build prompt
    prompt_path = artifacts / "post-assessment-10-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Post-Implementation Assessment: Section 10

        You are the post-implementation assessor.  Evaluate the landed
        changes for governance-visible debt, pattern drift, coupling
        issues, and security concerns.

        ## Section Spec

        {section_spec.read_text(encoding="utf-8")}

        ## Implementation Output

        {impl_output.read_text(encoding="utf-8")}

        ## Trace Map

        {trace_map.read_text(encoding="utf-8")}

        ## Problem Frame

        {problem_frame.read_text(encoding="utf-8")}

        ## Integration Proposal

        {proposal.read_text(encoding="utf-8")}

        ## Governance Packet

        {governance.read_text(encoding="utf-8")}

        ## Instructions

        Produce a JSON assessment matching the schema from your agent
        instructions.  The "section" field should be "10".  Reference
        governance IDs (PRB-*, PAT-*) where applicable.

        This implementation has intentional problems: tight coupling
        between session manager and middleware, hardcoded credential
        material, and an auth bypass in admin routes.  Reflect those
        in your assessment.

        Output JSON only.
    """), encoding="utf-8")
    return prompt_path


# ---------------------------------------------------------------------------
# Check functions: clean scenario
# ---------------------------------------------------------------------------

def _check_clean_has_assessment_json(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains a structured assessment JSON."""
    data = _extract_json_object(agent_output)
    if data is None:
        return False, "Agent output did not contain a JSON object"
    if "verdict" not in data:
        return False, f"JSON missing 'verdict' key. Keys found: {sorted(data.keys())}"
    return True, f"Assessment JSON found with verdict={data.get('verdict')}"


def _check_clean_verdict_accept(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the verdict is 'accept' (not accept_with_debt or refactor_required)."""
    data = _extract_json_object(agent_output)
    if data is None:
        return False, "No JSON payload found"
    verdict = data.get("verdict")
    if verdict == "accept":
        return True, "verdict='accept' (correct for clean implementation)"
    return False, (
        f"Expected verdict='accept', got '{verdict}'. "
        f"Clean implementation should not produce debt or refactor verdicts."
    )


def _check_clean_references_governance_ids(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the assessment references governance IDs from the packet."""
    data = _extract_json_object(agent_output)
    if data is None:
        return False, "No JSON payload found"
    # Check for PRB or PAT references in the output
    output_str = json.dumps(data)
    has_prb = "PRB-" in output_str
    has_pat = "PAT-" in output_str
    problem_ids = data.get("problem_ids_addressed", [])
    pattern_ids = data.get("pattern_ids_followed", [])
    if problem_ids or pattern_ids or has_prb or has_pat:
        return True, (
            f"Governance IDs referenced: "
            f"problem_ids_addressed={problem_ids}, "
            f"pattern_ids_followed={pattern_ids}"
        )
    return False, "No governance IDs (PRB-* or PAT-*) found in assessment"


# ---------------------------------------------------------------------------
# Check functions: risky scenario
# ---------------------------------------------------------------------------

def _check_risky_has_assessment_json(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains a structured assessment JSON."""
    data = _extract_json_object(agent_output)
    if data is None:
        return False, "Agent output did not contain a JSON object"
    if "verdict" not in data:
        return False, f"JSON missing 'verdict' key. Keys found: {sorted(data.keys())}"
    return True, f"Assessment JSON found with verdict={data.get('verdict')}"


def _check_risky_verdict_not_accept(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the verdict is 'accept_with_debt' or 'refactor_required'."""
    data = _extract_json_object(agent_output)
    if data is None:
        return False, "No JSON payload found"
    verdict = data.get("verdict")
    if verdict in ("accept_with_debt", "refactor_required"):
        return True, f"verdict='{verdict}' (correct for risky implementation)"
    return False, (
        f"Expected verdict='accept_with_debt' or 'refactor_required', "
        f"got '{verdict}'. Implementation has coupling, credential, and "
        f"bypass issues that should not be silently accepted."
    )


def _check_risky_identifies_risk_categories(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the assessment identifies specific risk categories.

    The fixture has: tight coupling (circular dep), exposed credentials
    (hardcoded salt, fallback password), and a security bypass (skip_auth).
    The agent should flag at least two of these categories.
    """
    data = _extract_json_object(agent_output)
    if data is None:
        return False, "No JSON payload found"
    output_lower = json.dumps(data).lower()
    categories_found = []
    # Coupling signals
    coupling_terms = ["coupling", "circular", "dependency", "entangle", "cohesion"]
    if any(t in output_lower for t in coupling_terms):
        categories_found.append("coupling")
    # Security/credential signals
    security_terms = [
        "credential", "hardcod", "secret", "password", "salt",
        "security", "exposure", "cleartext", "plaintext",
    ]
    if any(t in output_lower for t in security_terms):
        categories_found.append("security")
    # Bypass signals
    bypass_terms = ["bypass", "skip_auth", "skip auth", "admin"]
    if any(t in output_lower for t in bypass_terms):
        categories_found.append("bypass")
    if len(categories_found) >= 2:
        return True, f"Risk categories identified: {categories_found}"
    return False, (
        f"Expected at least 2 risk categories from [coupling, security, bypass], "
        f"found: {categories_found}"
    )


def _check_risky_includes_debt_items(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the assessment includes debt_items for the risky implementation."""
    data = _extract_json_object(agent_output)
    if data is None:
        return False, "No JSON payload found"
    debt_items = data.get("debt_items", [])
    refactor_reasons = data.get("refactor_reasons", [])
    # Either debt_items or refactor_reasons should be non-empty
    if (isinstance(debt_items, list) and debt_items) or (
        isinstance(refactor_reasons, list) and refactor_reasons
    ):
        return True, (
            f"debt_items={len(debt_items) if isinstance(debt_items, list) else debt_items}, "
            f"refactor_reasons={len(refactor_reasons) if isinstance(refactor_reasons, list) else refactor_reasons}"
        )
    # Also check lenses for ok=false as a secondary signal
    lenses = data.get("lenses", {})
    if isinstance(lenses, dict):
        failing = [k for k, v in lenses.items() if isinstance(v, dict) and not v.get("ok", True)]
        if failing:
            return True, f"No debt_items but lenses flagged issues: {failing}"
    return False, "No debt_items, refactor_reasons, or failing lenses found"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="post_assessment_clean",
        agent_file="post-implementation-assessor.md",
        model_policy_key="adjudicator",
        setup=_setup_clean,
        checks=[
            Check(
                description="Clean implementation produces structured assessment JSON",
                verify=_check_clean_has_assessment_json,
            ),
            Check(
                description="Clean implementation verdict is 'accept'",
                verify=_check_clean_verdict_accept,
            ),
            Check(
                description="Assessment references governance IDs from packet",
                verify=_check_clean_references_governance_ids,
            ),
        ],
    ),
    Scenario(
        name="post_assessment_risky",
        agent_file="post-implementation-assessor.md",
        model_policy_key="adjudicator",
        setup=_setup_risky,
        checks=[
            Check(
                description="Risky implementation produces structured assessment JSON",
                verify=_check_risky_has_assessment_json,
            ),
            Check(
                description="Risky implementation verdict is 'accept_with_debt' or 'refactor_required'",
                verify=_check_risky_verdict_not_accept,
            ),
            Check(
                description="Assessment identifies specific risk categories (coupling, security, bypass)",
                verify=_check_risky_identifies_risk_categories,
            ),
            Check(
                description="Assessment includes debt_items or refactor_reasons",
                verify=_check_risky_includes_debt_items,
            ),
        ],
    ),
]
