"""Intent expansion handling for the proposal loop.

Manages the expansion cycle when structured surfaces are discovered,
including budget tracking, user gate handling, and escalation signals.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.path_registry import PathRegistry
from containers import Services
from intent.service.expansion_facade import handle_user_gate, run_expansion_cycle
from signals.types import ACTION_CONTINUE


def _handle_budget_exhaustion(
    section_number: str, planspace: Path, parent: str,
    expansion_count: int, expansion_max: int,
) -> str | None:
    """Handle the case where expansion budget is exhausted.

    Returns ``"break"`` to accept alignment, or ``None`` to abort.
    """
    Services.logger().log(
        f"Section {section_number}: intent expansion "
        f"budget exhausted ({expansion_count}/{expansion_max}) "
        f"— pausing for decision"
    )
    stalled_signal = {
        "section": section_number,
        "reason": "expansion budget exhausted",
        "cycles": expansion_count,
    }
    Services.artifact_io().write_json(
        PathRegistry(planspace).intent_stalled_signal(section_number),
        stalled_signal,
    )
    response = Services.pipeline_control().pause_for_parent(
        planspace, parent,
        f"pause:intent-stalled:{section_number}:"
        f"expansion budget exhausted ({expansion_count}/{expansion_max})",
    )
    if not response.startswith("resume"):
        return None
    return "break"


def run_aligned_expansion(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    intent_budgets: dict,
    expansion_counts: dict[str, int],
) -> str | None:
    """Handle intent expansion when the proposal is aligned but surfaces exist.

    Returns:
        "continue" — caller should re-propose
        "break" — caller should accept alignment
        None — caller should abort (return None)
    """
    paths = PathRegistry(planspace)
    expansion_max = intent_budgets.get("intent_expansion_max", 2)
    expansion_count = expansion_counts.get(section_number, 0)

    if expansion_count >= expansion_max:
        return _handle_budget_exhaustion(
            section_number, planspace, parent,
            expansion_count, expansion_max,
        )

    Services.logger().log(
        f"Section {section_number}: surfaces found — "
        f"running expansion cycle"
    )
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"summary:intent-expand:{section_number}:cycle-{expansion_count + 1}",
    )
    delta_result = run_expansion_cycle(
        section_number,
        planspace,
        codespace,
        parent,
        budgets=intent_budgets,
    )
    expansion_counts[section_number] = expansion_count + 1

    if delta_result.get("needs_user_input"):
        gate_response = handle_user_gate(
            section_number,
            planspace,
            parent,
            delta_result,
        )
        if gate_response and not gate_response.startswith("resume"):
            return None
        if gate_response:
            payload = gate_response.partition(":")[2].strip()
            if payload:
                Services.cross_section().persist_decision(planspace, section_number, payload)
        if Services.pipeline_control().alignment_changed_pending(planspace):
            return None

    if delta_result.get("restart_required"):
        Services.logger().log(
            f"Section {section_number}: intent "
            f"expanded — re-proposing"
        )
        return ACTION_CONTINUE

    return "break"


def run_misaligned_expansion(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    intent_budgets: dict,
    expansion_counts: dict[str, int],
) -> None:
    """Handle intent expansion on a misaligned pass with definition-gap surfaces.

    Runs the expansion cycle if budget allows, persisting decisions from
    user gates.  This is fire-and-forget — the caller always continues
    the proposal loop regardless.
    """
    expansion_max = intent_budgets.get("intent_expansion_max", 2)
    expansion_count = expansion_counts.get(section_number, 0)

    if expansion_count >= expansion_max:
        Services.logger().log(
            f"Section {section_number}: definition-gap surfaces "
            f"found on misaligned pass but expansion budget is "
            f"exhausted ({expansion_count}/{expansion_max})"
        )
        return

    Services.logger().log(
        f"Section {section_number}: definition-gap surfaces "
        f"found on misaligned pass — running expansion"
    )
    delta_result = run_expansion_cycle(
        section_number,
        planspace,
        codespace,
        parent,
        budgets=intent_budgets,
    )
    expansion_counts[section_number] = expansion_count + 1

    if delta_result.get("needs_user_input"):
        gate_response = handle_user_gate(
            section_number,
            planspace,
            parent,
            delta_result,
        )
        if gate_response and not gate_response.startswith("resume"):
            return
        if gate_response:
            payload = gate_response.partition(":")[2].strip()
            if payload:
                Services.cross_section().persist_decision(planspace, section_number, payload)
