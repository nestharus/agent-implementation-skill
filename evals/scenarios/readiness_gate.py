"""Readiness gate scenario evals.

Tests that the execution-readiness gate correctly blocks or allows
implementation dispatch based on proposal-state artifacts.

These scenarios pre-seed proposal-state artifacts, run the readiness
resolver mechanically during setup, and then check the resulting
readiness artifacts.  The agent dispatch is a lightweight inspection
step -- the core behavior under test is the mechanical readiness gate.

Scenarios:
  readiness_gate_blocked: Blocking fields present -> ready=false, no dispatch
  readiness_gate_user_decision: user_root_questions present -> blocked
  readiness_gate_stale_reopen: Reconciliation changes reopen readiness
  readiness_gate_missing_artifact: Missing proposal-state -> fail closed
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario

# Import the readiness and proposal-state machinery for mechanical setup.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "scripts"))
from section_loop.proposal_state import save_proposal_state  # noqa: E402
from section_loop.readiness import resolve_readiness  # noqa: E402
from section_loop.reconciliation import run_reconciliation  # noqa: E402


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _write_inspection_prompt(
    artifacts: Path,
    scenario_tag: str,
    section_number: str,
    extra_context: str,
) -> Path:
    """Write a prompt asking the agent to inspect readiness results."""
    prompt_path = artifacts / f"readiness-inspect-{scenario_tag}-prompt.md"
    signal_path = (artifacts / "signals"
                   / f"readiness-inspect-{scenario_tag}.json")
    readiness_path = (
        artifacts / "readiness"
        / f"section-{section_number}-execution-ready.json"
    )
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Inspect Readiness Gate for Section {section_number}

        ## Context
        The readiness gate has been evaluated for section {section_number}.
        Your job is to read the readiness artifact and summarize the result.

        ## Files to Read
        1. Readiness artifact: `{readiness_path}`

        ## Instructions
        Read the readiness artifact. Report whether the section is ready
        for implementation and list any blockers found.

        Write a JSON signal to: `{signal_path}`
        ```json
        {{"inspected": true, "ready": <bool>, "blocker_count": <int>}}
        ```

        {extra_context}
    """), encoding="utf-8")
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: blocked by unresolved fields
# ---------------------------------------------------------------------------

def _setup_blocked(planspace: Path, codespace: Path) -> Path:
    """Section with blocking fields -> execution_ready=false."""
    artifacts = planspace / "artifacts"
    proposals = artifacts / "proposals"
    signals = artifacts / "signals"
    proposals.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Pre-seed a proposal-state with blocking fields
    state = {
        "resolved_anchors": ["cache.store"],
        "unresolved_anchors": ["message_broker.connection"],
        "resolved_contracts": [],
        "unresolved_contracts": ["MessageBrokerProtocol"],
        "research_questions": ["What message format does the broker use?"],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": False,
        "readiness_rationale": "Unresolved anchors and contracts remain",
    }
    save_proposal_state(state,
                        proposals / "section-20-proposal-state.json")

    # Run readiness resolver mechanically
    resolve_readiness(artifacts, "20")

    # Codespace (minimal)
    (codespace / "cache").mkdir(parents=True, exist_ok=True)
    (codespace / "cache" / "__init__.py").write_text("", encoding="utf-8")

    return _write_inspection_prompt(
        artifacts, "blocked", "20",
        "This section has unresolved anchors and contracts. "
        "Readiness should be false.",
    )


# ---------------------------------------------------------------------------
# Setup: user-decision blocking
# ---------------------------------------------------------------------------

def _setup_user_decision(planspace: Path, codespace: Path) -> Path:
    """Section with user_root_questions -> blocked with NEED_DECISION."""
    artifacts = planspace / "artifacts"
    proposals = artifacts / "proposals"
    signals = artifacts / "signals"
    proposals.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Pre-seed a proposal-state with user_root_questions
    state = {
        "resolved_anchors": ["api.endpoint", "db.connection"],
        "unresolved_anchors": [],
        "resolved_contracts": ["DatabaseProtocol"],
        "unresolved_contracts": [],
        "research_questions": [],
        "user_root_questions": [
            "Should the API support both REST and GraphQL?",
            "What is the expected SLA for this endpoint?",
        ],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": False,
        "readiness_rationale": (
            "User must answer root questions before implementation "
            "can proceed"
        ),
    }
    save_proposal_state(state,
                        proposals / "section-21-proposal-state.json")

    # Run readiness resolver mechanically
    resolve_readiness(artifacts, "21")

    # Codespace (minimal)
    (codespace / "api").mkdir(parents=True, exist_ok=True)
    (codespace / "api" / "__init__.py").write_text("", encoding="utf-8")

    return _write_inspection_prompt(
        artifacts, "user-decision", "21",
        "This section has user_root_questions. Readiness should be false "
        "and blockers should mention user questions.",
    )


# ---------------------------------------------------------------------------
# Setup: stale readiness reopening
# ---------------------------------------------------------------------------

def _setup_stale_reopen(planspace: Path, codespace: Path) -> Path:
    """Previously-ready section is reopened after reconciliation changes."""
    artifacts = planspace / "artifacts"
    proposals = artifacts / "proposals"
    signals = artifacts / "signals"
    proposals.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Step 1: Section 22 starts as fully ready
    state_22_initial = {
        "resolved_anchors": ["report.generator", "export.engine"],
        "unresolved_anchors": [],
        "resolved_contracts": ["ReportFormat"],
        "unresolved_contracts": [],
        "research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": True,
        "readiness_rationale": "All integration points resolved",
    }
    save_proposal_state(state_22_initial,
                        proposals / "section-22-proposal-state.json")

    # First readiness check: should be ready
    readiness_1 = resolve_readiness(artifacts, "22")
    # Persist the initial readiness result for later comparison
    initial_ready = readiness_1.get("ready")

    # Step 2: Section 23 introduces a shared seam conflict with section 22
    state_23 = {
        "resolved_anchors": ["dashboard.renderer"],
        "unresolved_anchors": [],
        "resolved_contracts": [],
        "unresolved_contracts": ["ReportFormat"],
        "research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": ["report generation seam"],
        "execution_ready": False,
        "readiness_rationale": "ReportFormat contract needs coordination",
    }
    save_proposal_state(state_23,
                        proposals / "section-23-proposal-state.json")

    # Also add shared_seam_candidates to section 22 to simulate
    # reconciliation discovering the seam
    state_22_updated = {
        "resolved_anchors": ["report.generator", "export.engine"],
        "unresolved_anchors": [],
        "resolved_contracts": ["ReportFormat"],
        "unresolved_contracts": [],
        "research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": ["report generation seam"],
        "execution_ready": False,
        "readiness_rationale": (
            "Reopened: reconciliation found shared seam with section 23"
        ),
    }
    save_proposal_state(state_22_updated,
                        proposals / "section-22-proposal-state.json")

    # Step 3: Run reconciliation
    proposal_results = [
        {"section_number": "22"},
        {"section_number": "23"},
    ]
    run_reconciliation(planspace, proposal_results)

    # Step 4: Re-resolve readiness for section 22
    readiness_2 = resolve_readiness(artifacts, "22")

    # Store the before/after for the check to inspect
    check_meta = {
        "initial_ready": initial_ready,
        "post_reconciliation_ready": readiness_2.get("ready"),
    }
    meta_path = artifacts / "readiness" / "section-22-check-meta.json"
    meta_path.write_text(
        json.dumps(check_meta, indent=2) + "\n", encoding="utf-8",
    )

    # Codespace (minimal)
    (codespace / "reports").mkdir(parents=True, exist_ok=True)
    (codespace / "reports" / "__init__.py").write_text("", encoding="utf-8")

    return _write_inspection_prompt(
        artifacts, "stale-reopen", "22",
        "This section was initially ready but was reopened after "
        "reconciliation discovered a shared seam with section 23. "
        "Readiness should now be false.",
    )


# ---------------------------------------------------------------------------
# Setup: missing proposal-state artifact
# ---------------------------------------------------------------------------

def _setup_missing_artifact(planspace: Path, codespace: Path) -> Path:
    """No proposal-state artifact exists -> readiness fails closed."""
    artifacts = planspace / "artifacts"
    proposals = artifacts / "proposals"
    signals = artifacts / "signals"
    proposals.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Intentionally do NOT create a proposal-state artifact for section 24
    # Run readiness resolver -- it should fail closed
    resolve_readiness(artifacts, "24")

    # Codespace (minimal)
    (codespace / "src").mkdir(parents=True, exist_ok=True)
    (codespace / "src" / "__init__.py").write_text("", encoding="utf-8")

    return _write_inspection_prompt(
        artifacts, "missing", "24",
        "No proposal-state artifact exists for this section. "
        "Readiness should fail closed (ready=false).",
    )


# ---------------------------------------------------------------------------
# Check functions: blocked
# ---------------------------------------------------------------------------

def _check_blocked_readiness_artifact(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify readiness artifact exists and ready=false."""
    path = (planspace / "artifacts" / "readiness"
            / "section-20-execution-ready.json")
    if not path.exists():
        return False, f"Readiness artifact not written: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Readiness artifact is not valid JSON: {exc}"
    ready = data.get("ready")
    if ready is False:
        return True, "ready=false (correct for blocked section)"
    return False, f"Expected ready=false, got {ready}"


def _check_blocked_has_blockers(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify readiness artifact has blockers listed."""
    path = (planspace / "artifacts" / "readiness"
            / "section-20-execution-ready.json")
    if not path.exists():
        return False, "Readiness artifact not written"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Readiness artifact is not valid JSON"
    blockers = data.get("blockers", [])
    if blockers:
        return True, f"Found {len(blockers)} blocker(s)"
    return False, "Expected blockers but found none"


def _check_blocked_no_dispatch(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify no implementation dispatch artifacts were written.

    When execution_ready is false, no microstrategy or implementation
    artifacts should exist.
    """
    proposals_dir = planspace / "artifacts" / "proposals"
    microstrategy = proposals_dir / "section-20-microstrategy.md"
    implementation = proposals_dir / "section-20-implementation-output.md"
    if microstrategy.exists():
        return False, "Microstrategy artifact exists (should not for blocked section)"
    if implementation.exists():
        return False, "Implementation artifact exists (should not for blocked section)"
    return True, "No implementation dispatch artifacts written"


# ---------------------------------------------------------------------------
# Check functions: user decision
# ---------------------------------------------------------------------------

def _check_user_decision_not_ready(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify readiness is false when user_root_questions present."""
    path = (planspace / "artifacts" / "readiness"
            / "section-21-execution-ready.json")
    if not path.exists():
        return False, f"Readiness artifact not written: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Readiness artifact is not valid JSON: {exc}"
    ready = data.get("ready")
    if ready is False:
        return True, "ready=false (correct for user-decision blocked)"
    return False, f"Expected ready=false, got {ready}"


def _check_user_decision_blockers_type(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify blockers include user_root_questions type."""
    path = (planspace / "artifacts" / "readiness"
            / "section-21-execution-ready.json")
    if not path.exists():
        return False, "Readiness artifact not written"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Readiness artifact is not valid JSON"
    blockers = data.get("blockers", [])
    user_q_blockers = [
        b for b in blockers
        if b.get("type") == "user_root_questions"
    ]
    if user_q_blockers:
        return True, f"Found {len(user_q_blockers)} user_root_questions blocker(s)"
    blocker_types = [b.get("type") for b in blockers]
    return False, (
        f"Expected user_root_questions blocker, found types: {blocker_types}"
    )


def _check_user_decision_no_dispatch(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify no implementation dispatch for user-blocked section."""
    proposals_dir = planspace / "artifacts" / "proposals"
    microstrategy = proposals_dir / "section-21-microstrategy.md"
    implementation = proposals_dir / "section-21-implementation-output.md"
    if microstrategy.exists():
        return False, "Microstrategy artifact exists (should not)"
    if implementation.exists():
        return False, "Implementation artifact exists (should not)"
    return True, "No implementation dispatch artifacts written"


# ---------------------------------------------------------------------------
# Check functions: stale reopen
# ---------------------------------------------------------------------------

def _check_stale_reopen_was_initially_ready(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify section 22 was initially ready before reconciliation."""
    meta_path = (planspace / "artifacts" / "readiness"
                 / "section-22-check-meta.json")
    if not meta_path.exists():
        return False, "Check metadata not found"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Check metadata is not valid JSON: {exc}"
    initial = meta.get("initial_ready")
    if initial is True:
        return True, "Section was initially ready=true"
    return False, f"Expected initial_ready=true, got {initial}"


def _check_stale_reopen_now_blocked(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify section 22 is now blocked after reconciliation changes."""
    path = (planspace / "artifacts" / "readiness"
            / "section-22-execution-ready.json")
    if not path.exists():
        return False, "Readiness artifact not written"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Readiness artifact is not valid JSON: {exc}"
    ready = data.get("ready")
    if ready is False:
        return True, "ready=false (correct after reconciliation reopened it)"
    return False, f"Expected ready=false after reopening, got {ready}"


def _check_stale_reopen_reconciliation_exists(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify reconciliation result marks section 22 as affected."""
    recon_path = (planspace / "artifacts" / "reconciliation"
                  / "section-22-reconciliation-result.json")
    if not recon_path.exists():
        return False, "Reconciliation result for section 22 not found"
    try:
        data = json.loads(recon_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Reconciliation result is not valid JSON: {exc}"
    affected = data.get("affected")
    if affected:
        return True, "Section 22 marked as affected by reconciliation"
    return False, "Section 22 not marked as affected"


# ---------------------------------------------------------------------------
# Check functions: missing artifact
# ---------------------------------------------------------------------------

def _check_missing_fails_closed(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify readiness fails closed when proposal-state is missing."""
    path = (planspace / "artifacts" / "readiness"
            / "section-24-execution-ready.json")
    if not path.exists():
        return False, f"Readiness artifact not written: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Readiness artifact is not valid JSON: {exc}"
    ready = data.get("ready")
    if ready is False:
        return True, "ready=false (correct fail-closed behavior)"
    return False, f"Expected ready=false (fail-closed), got {ready}"


def _check_missing_rationale(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify rationale mentions missing artifact."""
    path = (planspace / "artifacts" / "readiness"
            / "section-24-execution-ready.json")
    if not path.exists():
        return False, "Readiness artifact not written"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Readiness artifact is not valid JSON"
    rationale = data.get("rationale", "")
    if "missing" in rationale.lower() or "false" in rationale.lower():
        return True, f"Rationale mentions missing/false: '{rationale}'"
    return False, (
        f"Expected rationale to mention missing artifact, got: '{rationale}'"
    )


def _check_missing_no_dispatch(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify no implementation dispatch for missing-artifact section."""
    proposals_dir = planspace / "artifacts" / "proposals"
    microstrategy = proposals_dir / "section-24-microstrategy.md"
    implementation = proposals_dir / "section-24-implementation-output.md"
    if microstrategy.exists():
        return False, "Microstrategy artifact exists (should not)"
    if implementation.exists():
        return False, "Implementation artifact exists (should not)"
    return True, "No implementation dispatch artifacts written"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="readiness_gate_blocked",
        agent_file="state-adjudicator.md",
        model_policy_key="setup",
        setup=_setup_blocked,
        checks=[
            Check(
                description="Readiness artifact exists with ready=false",
                verify=_check_blocked_readiness_artifact,
            ),
            Check(
                description="Blockers listed in readiness artifact",
                verify=_check_blocked_has_blockers,
            ),
            Check(
                description="No implementation dispatch artifacts written",
                verify=_check_blocked_no_dispatch,
            ),
        ],
    ),
    Scenario(
        name="readiness_gate_user_decision",
        agent_file="state-adjudicator.md",
        model_policy_key="setup",
        setup=_setup_user_decision,
        checks=[
            Check(
                description="Readiness is false when user_root_questions present",
                verify=_check_user_decision_not_ready,
            ),
            Check(
                description="Blockers include user_root_questions type",
                verify=_check_user_decision_blockers_type,
            ),
            Check(
                description="No implementation dispatch artifacts written",
                verify=_check_user_decision_no_dispatch,
            ),
        ],
    ),
    Scenario(
        name="readiness_gate_stale_reopen",
        agent_file="state-adjudicator.md",
        model_policy_key="setup",
        setup=_setup_stale_reopen,
        checks=[
            Check(
                description="Section was initially ready=true",
                verify=_check_stale_reopen_was_initially_ready,
            ),
            Check(
                description="Section now blocked after reconciliation",
                verify=_check_stale_reopen_now_blocked,
            ),
            Check(
                description="Reconciliation result marks section as affected",
                verify=_check_stale_reopen_reconciliation_exists,
            ),
        ],
    ),
    Scenario(
        name="readiness_gate_missing_artifact",
        agent_file="state-adjudicator.md",
        model_policy_key="setup",
        setup=_setup_missing_artifact,
        checks=[
            Check(
                description="Readiness fails closed (ready=false)",
                verify=_check_missing_fails_closed,
            ),
            Check(
                description="Rationale mentions missing artifact",
                verify=_check_missing_rationale,
            ),
            Check(
                description="No implementation dispatch artifacts written",
                verify=_check_missing_no_dispatch,
            ),
        ],
    ),
]
