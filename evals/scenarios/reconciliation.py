"""Reconciliation scenario evals.

Tests that the reconciliation stage correctly detects cross-section
conflicts: shared seam overlaps, new-section candidate consolidation,
and contract conflicts across multiple sections.

These scenarios pre-seed proposal-state artifacts in setup, run
reconciliation mechanically, and then dispatch a lightweight agent
to inspect the results.  The checks verify artifact writes from the
reconciliation stage, not LLM output quality.

Scenarios:
  reconciliation_shared_seam: Two sections referencing the same seam
  reconciliation_new_section: Duplicate new_section_candidates consolidated
  reconciliation_contract_conflict: Same contract unresolved in multiple sections
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario

# We import reconciliation machinery to run it mechanically during setup.
# The sys.path insertion matches what harness.py does.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "scripts"))
from lib.repositories.proposal_state_repository import save_proposal_state  # noqa: E402
from section_loop.reconciliation import run_reconciliation  # noqa: E402


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _write_section_spec(sections_dir: Path, num: str, title: str,
                        problem: str) -> Path:
    """Write a minimal section specification file."""
    path = sections_dir / f"section-{num}.md"
    path.write_text(textwrap.dedent(f"""\
        # Section {num}: {title}

        ## Problem
        {problem}
    """), encoding="utf-8")
    return path


def _write_inspection_prompt(
    artifacts: Path,
    scenario_tag: str,
    inspect_dir: str,
    inspect_description: str,
) -> Path:
    """Write a prompt that asks the agent to inspect reconciliation results."""
    prompt_path = artifacts / f"reconciliation-inspect-{scenario_tag}-prompt.md"
    signal_path = (artifacts / "signals"
                   / f"reconciliation-inspect-{scenario_tag}.json")
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Inspect Reconciliation Results

        ## Context
        The reconciliation stage has run and produced artifacts. Your job
        is to read and summarize what was found.

        ## Files to Read
        1. Reconciliation directory: `{artifacts / inspect_dir}`

        ## Instructions
        Read the reconciliation result artifacts in the directory above.
        Summarize what conflicts, overlaps, or consolidations were found.

        Write a JSON signal to: `{signal_path}`
        ```json
        {{"inspected": true, "summary": "..."}}
        ```

        {inspect_description}
    """), encoding="utf-8")
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: shared seam conflict
# ---------------------------------------------------------------------------

def _setup_shared_seam(planspace: Path, codespace: Path) -> Path:
    """Create fixtures where two sections reference the same seam candidate."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section specs
    _write_section_spec(
        sections, "11", "Payment Gateway Adapter",
        "Integrate with external payment gateway. Shares a common "
        "transaction interface seam with the billing module.",
    )
    _write_section_spec(
        sections, "12", "Billing Engine",
        "Calculate invoices and trigger payments. Shares a common "
        "transaction interface seam with the payment gateway.",
    )

    # Pre-seed proposal-state artifacts with the same seam candidate
    state_11 = {
        "resolved_anchors": ["payment_gateway.client"],
        "unresolved_anchors": [],
        "resolved_contracts": [],
        "unresolved_contracts": ["transaction_result_schema"],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": ["transaction interface seam"],
        "execution_ready": False,
        "readiness_rationale": "Shared seam needs coordination",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    state_12 = {
        "resolved_anchors": ["billing.calculator"],
        "unresolved_anchors": [],
        "resolved_contracts": [],
        "unresolved_contracts": ["transaction_result_schema"],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": ["transaction interface seam"],
        "execution_ready": False,
        "readiness_rationale": "Shared seam needs coordination",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }

    save_proposal_state(state_11,
                        proposals / "section-11-proposal-state.json")
    save_proposal_state(state_12,
                        proposals / "section-12-proposal-state.json")

    # Run reconciliation mechanically
    proposal_results = [
        {"section_number": "11"},
        {"section_number": "12"},
    ]
    run_reconciliation(planspace, proposal_results)

    # Codespace (minimal)
    (codespace / "payments").mkdir(parents=True, exist_ok=True)
    (codespace / "payments" / "__init__.py").write_text("", encoding="utf-8")
    (codespace / "billing").mkdir(parents=True, exist_ok=True)
    (codespace / "billing" / "__init__.py").write_text("", encoding="utf-8")

    return _write_inspection_prompt(
        artifacts, "shared-seam", "reconciliation",
        "Focus on shared seam detection between sections 11 and 12.",
    )


# ---------------------------------------------------------------------------
# Setup: new section candidate consolidation
# ---------------------------------------------------------------------------

def _setup_new_section(planspace: Path, codespace: Path) -> Path:
    """Create fixtures where multiple sections propose the same new section."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section specs
    _write_section_spec(
        sections, "13", "User Onboarding Flow",
        "Guide new users through account setup. Discovers a need for "
        "an email template engine.",
    )
    _write_section_spec(
        sections, "14", "Notification Preferences",
        "Manage per-user notification settings. Also discovers a need "
        "for an email template engine.",
    )

    # Pre-seed proposal-state artifacts with overlapping new_section_candidates
    state_13 = {
        "resolved_anchors": ["onboarding.wizard"],
        "unresolved_anchors": [],
        "resolved_contracts": ["onboarding_steps_protocol"],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [
            {"title": "email template engine",
             "scope": "Shared email rendering and template management"},
        ],
        "shared_seam_candidates": [],
        "execution_ready": True,
        "readiness_rationale": "All integration points resolved",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    state_14 = {
        "resolved_anchors": ["preferences.store"],
        "unresolved_anchors": [],
        "resolved_contracts": ["notification_settings_schema"],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [
            {"title": "email template engine",
             "scope": "Shared email rendering for notifications"},
        ],
        "shared_seam_candidates": [],
        "execution_ready": True,
        "readiness_rationale": "All integration points resolved",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }

    save_proposal_state(state_13,
                        proposals / "section-13-proposal-state.json")
    save_proposal_state(state_14,
                        proposals / "section-14-proposal-state.json")

    # Run reconciliation mechanically
    proposal_results = [
        {"section_number": "13"},
        {"section_number": "14"},
    ]
    run_reconciliation(planspace, proposal_results)

    # Codespace (minimal)
    (codespace / "onboarding").mkdir(parents=True, exist_ok=True)
    (codespace / "onboarding" / "__init__.py").write_text("", encoding="utf-8")

    return _write_inspection_prompt(
        artifacts, "new-section", "reconciliation",
        "Focus on consolidated new-section candidates from sections 13 and 14.",
    )


# ---------------------------------------------------------------------------
# Setup: contract conflict
# ---------------------------------------------------------------------------

def _setup_contract_conflict(planspace: Path, codespace: Path) -> Path:
    """Create fixtures where the same contract is unresolved in two sections."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section specs
    _write_section_spec(
        sections, "15", "Order Validation",
        "Validate incoming orders against business rules. Needs the "
        "InventoryAvailability contract to check stock levels.",
    )
    _write_section_spec(
        sections, "16", "Inventory Sync",
        "Synchronize inventory across warehouses. Also needs the "
        "InventoryAvailability contract to report current levels.",
    )

    # Pre-seed proposal states: both have the same contract unresolved
    state_15 = {
        "resolved_anchors": ["order.validator"],
        "unresolved_anchors": [],
        "resolved_contracts": [],
        "unresolved_contracts": ["InventoryAvailability"],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": False,
        "readiness_rationale": "InventoryAvailability contract unresolved",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    state_16 = {
        "resolved_anchors": ["inventory.sync_engine"],
        "unresolved_anchors": [],
        "resolved_contracts": [],
        "unresolved_contracts": ["InventoryAvailability"],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": False,
        "readiness_rationale": "InventoryAvailability contract unresolved",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }

    save_proposal_state(state_15,
                        proposals / "section-15-proposal-state.json")
    save_proposal_state(state_16,
                        proposals / "section-16-proposal-state.json")

    # Run reconciliation mechanically
    proposal_results = [
        {"section_number": "15"},
        {"section_number": "16"},
    ]
    run_reconciliation(planspace, proposal_results)

    # Codespace (minimal)
    (codespace / "orders").mkdir(parents=True, exist_ok=True)
    (codespace / "orders" / "__init__.py").write_text("", encoding="utf-8")
    (codespace / "inventory").mkdir(parents=True, exist_ok=True)
    (codespace / "inventory" / "__init__.py").write_text("", encoding="utf-8")

    return _write_inspection_prompt(
        artifacts, "contract-conflict", "reconciliation",
        "Focus on the InventoryAvailability contract conflict between "
        "sections 15 and 16.",
    )


# ---------------------------------------------------------------------------
# Check functions: shared seam
# ---------------------------------------------------------------------------

def _check_seam_reconciliation_result_exists(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify reconciliation result artifacts exist for both sections."""
    recon_dir = planspace / "artifacts" / "reconciliation"
    result_11 = recon_dir / "section-11-reconciliation-result.json"
    result_12 = recon_dir / "section-12-reconciliation-result.json"
    missing = []
    if not result_11.exists():
        missing.append("section-11")
    if not result_12.exists():
        missing.append("section-12")
    if missing:
        return False, f"Missing reconciliation results for: {missing}"
    return True, "Both reconciliation result artifacts exist"


def _check_seam_detected(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify shared seam was detected between sections 11 and 12."""
    recon_dir = planspace / "artifacts" / "reconciliation"
    result_path = recon_dir / "section-11-reconciliation-result.json"
    if not result_path.exists():
        return False, "Reconciliation result for section 11 not found"
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Reconciliation result is not valid JSON: {exc}"
    seams = data.get("substrate_seams", [])
    if seams:
        return True, f"Found {len(seams)} substrate seam(s)"
    return False, "No substrate seams detected"


def _check_seam_substrate_trigger(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify a substrate-trigger signal was written for the shared seam."""
    signals_dir = planspace / "artifacts" / "signals"
    triggers = list(signals_dir.glob("substrate-trigger-reconciliation-*.json"))
    if triggers:
        return True, f"Found {len(triggers)} substrate trigger artifact(s)"
    return False, "No substrate-trigger artifacts found"


def _check_seam_both_affected(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify both sections are marked as affected."""
    summary_path = (planspace / "artifacts" / "reconciliation"
                    / "reconciliation-summary.json")
    if not summary_path.exists():
        return False, "Reconciliation summary not found"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Summary is not valid JSON: {exc}"
    affected = summary.get("sections_affected", [])
    if "11" in affected and "12" in affected:
        return True, f"Both sections affected: {affected}"
    return False, f"Expected both 11 and 12 in affected, got: {affected}"


# ---------------------------------------------------------------------------
# Check functions: new section consolidation
# ---------------------------------------------------------------------------

def _check_new_section_consolidated(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify duplicate new-section candidates were consolidated."""
    summary_path = (planspace / "artifacts" / "reconciliation"
                    / "reconciliation-summary.json")
    if not summary_path.exists():
        return False, "Reconciliation summary not found"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Summary is not valid JSON: {exc}"
    proposed = summary.get("new_sections_proposed", 0)
    if proposed >= 1:
        return True, f"new_sections_proposed={proposed}"
    return False, f"Expected new_sections_proposed >= 1, got {proposed}"


def _check_new_section_scope_delta(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify a scope-delta artifact was written for the consolidated section."""
    delta_dir = planspace / "artifacts" / "scope-deltas"
    if not delta_dir.exists():
        return False, "scope-deltas directory not found"
    deltas = list(delta_dir.glob("reconciliation-*.json"))
    if deltas:
        # Verify the delta has source_sections from both sections
        try:
            data = json.loads(deltas[0].read_text(encoding="utf-8"))
            sources = data.get("source_sections", [])
            if "13" in sources and "14" in sources:
                return True, (
                    f"Scope delta written with source sections: {sources}"
                )
            return False, (
                f"Scope delta source_sections missing 13 or 14: {sources}"
            )
        except json.JSONDecodeError as exc:
            return False, f"Scope delta is not valid JSON: {exc}"
    return False, "No scope-delta artifacts found"


def _check_new_section_both_affected(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify both source sections are marked as affected."""
    summary_path = (planspace / "artifacts" / "reconciliation"
                    / "reconciliation-summary.json")
    if not summary_path.exists():
        return False, "Reconciliation summary not found"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Summary is not valid JSON: {exc}"
    affected = summary.get("sections_affected", [])
    if "13" in affected and "14" in affected:
        return True, f"Both sections affected: {affected}"
    return False, f"Expected both 13 and 14 in affected, got: {affected}"


# ---------------------------------------------------------------------------
# Check functions: contract conflict
# ---------------------------------------------------------------------------

def _check_contract_conflict_detected(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the InventoryAvailability contract conflict was detected."""
    summary_path = (planspace / "artifacts" / "reconciliation"
                    / "reconciliation-summary.json")
    if not summary_path.exists():
        return False, "Reconciliation summary not found"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Summary is not valid JSON: {exc}"
    conflicts = summary.get("contract_conflicts", 0)
    if conflicts >= 1:
        return True, f"contract_conflicts={conflicts}"
    return False, f"Expected contract_conflicts >= 1, got {conflicts}"


def _check_contract_conflict_in_result(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the per-section reconciliation result contains the conflict."""
    recon_dir = planspace / "artifacts" / "reconciliation"
    result_path = recon_dir / "section-15-reconciliation-result.json"
    if not result_path.exists():
        return False, "Reconciliation result for section 15 not found"
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Reconciliation result is not valid JSON: {exc}"
    conflicts = data.get("contract_conflicts", [])
    if not conflicts:
        return False, "No contract_conflicts in section 15 result"
    # Check that the conflict mentions the right contract
    for c in conflicts:
        contract_name = c.get("contract", "")
        if "inventoryavailability" in contract_name.lower():
            sections = c.get("sections", [])
            if "15" in sections and "16" in sections:
                return True, (
                    f"InventoryAvailability conflict found between "
                    f"sections: {sections}"
                )
    return False, (
        f"InventoryAvailability conflict not found in: "
        f"{[c.get('contract') for c in conflicts]}"
    )


def _check_contract_conflict_both_affected(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify both sections are marked as affected by the conflict."""
    summary_path = (planspace / "artifacts" / "reconciliation"
                    / "reconciliation-summary.json")
    if not summary_path.exists():
        return False, "Reconciliation summary not found"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Summary is not valid JSON: {exc}"
    affected = summary.get("sections_affected", [])
    if "15" in affected and "16" in affected:
        return True, f"Both sections affected: {affected}"
    return False, f"Expected both 15 and 16 in affected, got: {affected}"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="reconciliation_shared_seam",
        agent_file="state-adjudicator.md",
        model_policy_key="setup",
        setup=_setup_shared_seam,
        checks=[
            Check(
                description="Reconciliation result artifacts exist for both sections",
                verify=_check_seam_reconciliation_result_exists,
            ),
            Check(
                description="Shared seam detected between sections 11 and 12",
                verify=_check_seam_detected,
            ),
            Check(
                description="Substrate-trigger artifact written",
                verify=_check_seam_substrate_trigger,
            ),
            Check(
                description="Both sections marked as affected",
                verify=_check_seam_both_affected,
            ),
        ],
    ),
    Scenario(
        name="reconciliation_new_section",
        agent_file="state-adjudicator.md",
        model_policy_key="setup",
        setup=_setup_new_section,
        checks=[
            Check(
                description="Duplicate new-section candidates consolidated",
                verify=_check_new_section_consolidated,
            ),
            Check(
                description="Scope-delta artifact written with both source sections",
                verify=_check_new_section_scope_delta,
            ),
            Check(
                description="Both source sections marked as affected",
                verify=_check_new_section_both_affected,
            ),
        ],
    ),
    Scenario(
        name="reconciliation_contract_conflict",
        agent_file="state-adjudicator.md",
        model_policy_key="setup",
        setup=_setup_contract_conflict,
        checks=[
            Check(
                description="Contract conflict detected in summary",
                verify=_check_contract_conflict_detected,
            ),
            Check(
                description="Per-section result contains InventoryAvailability conflict",
                verify=_check_contract_conflict_in_result,
            ),
            Check(
                description="Both sections marked as affected",
                verify=_check_contract_conflict_both_affected,
            ),
        ],
    ),
]
