from __future__ import annotations

from pathlib import Path

from staleness.service.change_tracker import check_pending as alignment_changed_pending
from signals.repository.artifact_io import read_json_or_default, write_json
from dispatch.service.model_policy import resolve
from intent.service.triage import load_triage_result
from orchestrator.path_registry import PathRegistry
from staleness.service.section_alignment import _extract_problems
from signals.service.communication import mailbox_send, log
from coordination.service.cross_section import persist_decision
from dispatch.engine.section_dispatch import (
    check_agent_signals,
    dispatch_agent,
    summarize_output,
    write_model_choice_signal,
)
from intent.service.expansion import handle_user_gate, run_expansion_cycle
from intent.service.surfaces import (
    load_combined_intent_surfaces,
    load_surface_registry,
    merge_surfaces_into_registry,
    normalize_surface_ids,
    save_surface_registry,
)
from orchestrator.service.pipeline_control import (
    handle_pending_messages,
    pause_for_parent,
)
from dispatch.prompt.writers import (
    write_integration_alignment_prompt,
    write_integration_proposal_prompt,
)
from reconciliation.engine.loop import load_reconciliation_result
from signals.service.blockers import (
    _append_open_problem,
    _update_blocker_rollup,
)
from implementation.service.reexplore import _write_alignment_surface
from flow.service.section_ingestion import ingest_and_submit


DEFINITION_GAP_KINDS = {
    "new_axis",
    "gap",
    "silence",
    "ungrounded_assumption",
}


def _load_combined_surfaces(section_number: str, planspace: Path) -> dict | None:
    """Load and merge all surfaces that can trigger proposal expansion."""
    return load_combined_intent_surfaces(section_number, planspace)


def _has_definition_gap_surfaces(surfaces: dict | None) -> bool:
    """Return whether any surfaced issue implies definition growth."""
    if not isinstance(surfaces, dict):
        return False
    return any(
        surface.get("kind") in DEFINITION_GAP_KINDS
        for kind_key in ("problem_surfaces", "philosophy_surfaces")
        for surface in surfaces.get(kind_key, [])
        if isinstance(surface, dict)
    )


def _count_surfaces(surfaces: dict | None) -> int:
    """Count all structured surfaces in a payload."""
    if not isinstance(surfaces, dict):
        return 0
    return sum(
        len(surfaces.get(kind_key, []))
        for kind_key in ("problem_surfaces", "philosophy_surfaces")
    )


def _persist_surfaces(section_number: str, planspace: Path, surfaces: dict) -> dict:
    """Normalize and persist discovered surfaces into the section registry."""
    registry = load_surface_registry(section_number, planspace)
    surfaces = normalize_surface_ids(surfaces, registry, section_number)
    merge_surfaces_into_registry(registry, surfaces)
    save_surface_registry(section_number, planspace, registry)
    return surfaces


def _write_intent_escalation_signal(
    artifacts: Path,
    section_number: str,
    reason: str,
    surface_count: int,
) -> None:
    """Record that lightweight intent escalated after structured discoveries."""
    escalation_signal = {
        "section": section_number,
        "reason": reason,
        "surface_count": surface_count,
    }
    write_json(
        artifacts / "signals" / f"intent-escalation-{section_number}.json",
        escalation_signal,
    )


def run_proposal_loop(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    cycle_budget: dict,
    incoming_notes: str | None,
) -> str | None:
    """Run the integration proposal loop until aligned or aborted."""
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    integration_proposal = (
        artifacts / "proposals" / f"section-{section.number}-integration-proposal.md"
    )
    cycle_budget_path = artifacts / "signals" / f"section-{section.number}-cycle-budget.json"
    triage_result = load_triage_result(section.number, planspace) or {}
    intent_mode = triage_result.get("intent_mode", "lightweight")
    intent_budgets = triage_result.get("budgets", {})
    proposal_problems: str | None = None
    proposal_attempt = 0

    while True:
        if handle_pending_messages(planspace, [], set()):
            mailbox_send(planspace, parent, f"fail:{section.number}:aborted")
            return None

        if alignment_changed_pending(planspace):
            log(
                f"Section {section.number}: alignment changed — "
                "aborting section to restart Phase 1"
            )
            return None

        proposal_attempt += 1

        if proposal_attempt > cycle_budget["proposal_max"]:
            log(
                f"Section {section.number}: proposal cycle budget exhausted "
                f"({cycle_budget['proposal_max']} attempts)"
            )
            budget_signal = {
                "section": section.number,
                "loop": "proposal",
                "attempts": proposal_attempt - 1,
                "budget": cycle_budget["proposal_max"],
                "escalate": True,
            }
            budget_signal_path = (
                artifacts
                / "signals"
                / f"section-{section.number}-proposal-budget-exhausted.json"
            )
            write_json(budget_signal_path, budget_signal)
            mailbox_send(
                planspace,
                parent,
                f"budget-exhausted:{section.number}:proposal:{proposal_attempt - 1}",
            )
            response = pause_for_parent(
                planspace,
                parent,
                f"pause:budget_exhausted:{section.number}:proposal loop exceeded "
                f"{cycle_budget['proposal_max']} attempts",
            )
            if not response.startswith("resume"):
                return None
            reloaded = read_json(cycle_budget_path)
            if reloaded is not None:
                cycle_budget.update(reloaded)

        tag = "revise " if proposal_problems else ""
        log(
            f"Section {section.number}: {tag}integration proposal "
            f"(attempt {proposal_attempt})"
        )

        proposal_model = resolve(policy, "proposal")
        notes_count = 0
        notes_dir = paths.notes_dir()
        if notes_dir.exists():
            notes_count = len(list(notes_dir.glob(f"from-*-to-{section.number}.md")))
        escalated_from = None
        triggers = policy.get("escalation_triggers", {})
        max_attempts = triggers.get("max_attempts_before_escalation", 3)
        stall_threshold = triggers.get("stall_count", 2)
        if proposal_attempt >= max_attempts or notes_count >= stall_threshold:
            escalated_from = proposal_model
            proposal_model = resolve(policy, "escalation_model")
            log(
                f"Section {section.number}: escalating to "
                f"{proposal_model} (attempt={proposal_attempt}, notes={notes_count})"
            )

        reason = (
            f"attempt={proposal_attempt}, notes={notes_count}"
            if escalated_from
            else "first attempt, default model"
        )
        write_model_choice_signal(
            planspace,
            section.number,
            "integration-proposal",
            proposal_model,
            reason,
            escalated_from,
        )

        intg_prompt = write_integration_proposal_prompt(
            section,
            planspace,
            codespace,
            proposal_problems,
            incoming_notes=incoming_notes,
            model_policy=policy,
        )
        if intg_prompt is None:
            log(
                f"Section {section.number}: integration proposal prompt "
                f"blocked by template safety — skipping dispatch"
            )
            return None

        recon_result = load_reconciliation_result(artifacts, section.number)
        if recon_result and recon_result.get("affected"):
            recon_path = (
                artifacts
                / "reconciliation"
                / f"section-{section.number}-reconciliation-result.json"
            )
            with intg_prompt.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n## Reconciliation Context\n\n"
                    "This section was affected by cross-section "
                    "reconciliation during Phase 1b. The reconciliation "
                    "analysis found overlapping anchors, contract "
                    "conflicts, or shared seams involving this section.\n\n"
                    "Read the reconciliation result and adjust your "
                    "proposal to account for shared anchors, resolved "
                    "conflicts, and seam decisions:\n"
                    f"`{recon_path}`\n"
                )
            log(
                f"Section {section.number}: appended reconciliation "
                f"context to proposal prompt"
            )

        intg_output = artifacts / f"intg-proposal-{section.number}-output.md"
        intg_agent = f"intg-proposal-{section.number}"
        intg_result = dispatch_agent(
            proposal_model,
            intg_prompt,
            intg_output,
            planspace,
            parent,
            intg_agent,
            codespace=codespace,
            section_number=section.number,
            agent_file="integration-proposer.md",
        )
        if intg_result == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(
            planspace,
            parent,
            f"summary:proposal:{section.number}:{summarize_output(intg_result)}",
        )

        if intg_result.startswith("TIMEOUT:"):
            log(
                f"Section {section.number}: integration proposal agent "
                f"timed out"
            )
            mailbox_send(
                planspace,
                parent,
                f"fail:{section.number}:integration proposal agent timed out",
            )
            return None

        ingest_and_submit(
            planspace,
            db_path=planspace / "run.db",
            submitted_by=f"proposal-{section.number}",
            signal_path=artifacts / "signals" / f"task-requests-proposal-{section.number}.json",
            origin_refs=[str(integration_proposal)],
        )

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            intg_result,
            signal_path=signal_dir / f"proposal-{section.number}-signal.json",
            output_path=intg_output,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
        )
        if signal:
            if signal in ("needs_parent", "out_of_scope"):
                _append_open_problem(planspace, section.number, detail, signal)
                mailbox_send(
                    planspace,
                    parent,
                    f"open-problem:{section.number}:{signal}:{detail[:200]}",
                )
            if signal == "out_of_scope":
                scope_delta_dir = paths.scope_deltas_dir()
                scope_delta_dir.mkdir(parents=True, exist_ok=True)
                proposal_sig_path = signal_dir / f"proposal-{section.number}-signal.json"
                signal_payload = read_json_or_default(proposal_sig_path, {})
                scope_delta = {
                    "delta_id": f"delta-{section.number}-proposal-oos",
                    "section": section.number,
                    "signal": "out_of_scope",
                    "detail": detail,
                    "requires_root_reframing": True,
                    "signal_path": str(proposal_sig_path),
                    "signal_payload": signal_payload,
                }
                write_json(
                    scope_delta_dir / f"section-{section.number}-scope-delta.json",
                    scope_delta,
                )
            _update_blocker_rollup(planspace)
            response = pause_for_parent(
                planspace,
                parent,
                f"pause:{signal}:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue

        if not integration_proposal.exists():
            log(
                f"Section {section.number}: ERROR — integration proposal "
                f"not written"
            )
            mailbox_send(
                planspace,
                parent,
                f"fail:{section.number}:integration proposal not written",
            )
            return None

        log(f"Section {section.number}: proposal alignment check")
        align_prompt = write_integration_alignment_prompt(
            section,
            planspace,
            codespace,
        )
        align_output = artifacts / f"intg-align-{section.number}-output.md"
        intent_sec_dir = artifacts / "intent" / "sections" / f"section-{section.number}"
        has_intent_artifacts = (
            intent_sec_dir.exists() and (intent_sec_dir / "problem.md").exists()
        )
        alignment_agent_file = (
            "intent-judge.md" if has_intent_artifacts else "alignment-judge.md"
        )
        alignment_model = (
            resolve(policy, "intent_judge")
            if has_intent_artifacts
            else resolve(policy, "alignment")
        )
        align_result = dispatch_agent(
            alignment_model,
            align_prompt,
            align_output,
            planspace,
            parent,
            codespace=codespace,
            section_number=section.number,
            agent_file=alignment_agent_file,
        )
        if align_result == "ALIGNMENT_CHANGED_PENDING":
            return None

        if align_result.startswith("TIMEOUT:"):
            log(
                f"Section {section.number}: proposal alignment check "
                f"timed out — retrying"
            )
            proposal_problems = "Previous alignment check timed out."
            continue

        problems = _extract_problems(
            align_result,
            output_path=align_output,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
            adjudicator_model=resolve(policy, "adjudicator"),
        )

        signal, detail = check_agent_signals(
            align_result,
            signal_path=signal_dir / f"proposal-align-{section.number}-signal.json",
            output_path=align_output,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
        )
        if signal == "underspec":
            response = pause_for_parent(
                planspace,
                parent,
                f"pause:underspec:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue

        if problems is None:
            surfaces = _load_combined_surfaces(section.number, planspace)
            surface_count = _count_surfaces(surfaces)
            if surface_count:
                if intent_mode != "full":
                    _persist_surfaces(section.number, planspace, surfaces)
                    log(
                        f"Section {section.number}: lightweight mode discovered "
                        f"{surface_count} structured surfaces — escalating to "
                        "full intent"
                    )
                    _write_intent_escalation_signal(
                        artifacts,
                        section.number,
                        "structured_surfaces_on_lightweight",
                        surface_count,
                    )
                    proposal_problems = (
                        "Lightweight section discovered structured surfaces; "
                        "re-propose under full intent mode."
                    )
                    intent_mode = "full"
                    continue

                if intent_mode == "full":
                    expansion_max = intent_budgets.get("intent_expansion_max", 2)
                    expansion_count = getattr(
                        run_proposal_loop,
                        "_expansion_counts",
                        {},
                    ).get(section.number, 0)
                    if expansion_count >= expansion_max:
                        log(
                            f"Section {section.number}: intent expansion "
                            f"budget exhausted ({expansion_count}/{expansion_max}) "
                            f"— pausing for decision"
                        )
                        stalled_signal = {
                            "section": section.number,
                            "reason": "expansion budget exhausted",
                            "cycles": expansion_count,
                        }
                        write_json(
                            artifacts / "signals" / f"intent-stalled-{section.number}.json",
                            stalled_signal,
                        )
                        response = pause_for_parent(
                            planspace,
                            parent,
                            f"pause:intent-stalled:{section.number}:"
                            f"expansion budget exhausted ({expansion_count}/{expansion_max})",
                        )
                        if not response.startswith("resume"):
                            return None
                    else:
                        log(
                            f"Section {section.number}: surfaces found — "
                            f"running expansion cycle"
                        )
                        mailbox_send(
                            planspace,
                            parent,
                            f"summary:intent-expand:{section.number}:cycle-{expansion_count + 1}",
                        )
                        delta_result = run_expansion_cycle(
                            section.number,
                            planspace,
                            codespace,
                            parent,
                            budgets=intent_budgets,
                        )
                        if not hasattr(run_proposal_loop, "_expansion_counts"):
                            run_proposal_loop._expansion_counts = {}
                        run_proposal_loop._expansion_counts[section.number] = (
                            expansion_count + 1
                        )
                        if delta_result.get("needs_user_input"):
                            gate_response = handle_user_gate(
                                section.number,
                                planspace,
                                parent,
                                delta_result,
                            )
                            if gate_response and not gate_response.startswith("resume"):
                                return None
                            payload = gate_response.partition(":")[2].strip()
                            if payload:
                                persist_decision(planspace, section.number, payload)
                            if alignment_changed_pending(planspace):
                                return None
                        if delta_result.get("restart_required"):
                            proposal_problems = (
                                "Intent expanded; re-propose against "
                                "updated problem/philosophy definitions."
                            )
                            log(
                                f"Section {section.number}: intent "
                                f"expanded — re-proposing"
                            )
                            continue

            log(f"Section {section.number}: integration proposal ALIGNED")
            mailbox_send(
                planspace,
                parent,
                f"summary:proposal-align:{section.number}:ALIGNED",
            )
            _write_alignment_surface(planspace, section)
            break

        misaligned_surfaces = _load_combined_surfaces(
            section.number, planspace,
        )
        misaligned_surface_count = _count_surfaces(misaligned_surfaces)
        if misaligned_surface_count:
            misaligned_surfaces = _persist_surfaces(
                section.number,
                planspace,
                misaligned_surfaces,
            )
            log(
                f"Section {section.number}: persisted intent "
                f"surfaces from misaligned pass"
            )
            if intent_mode != "full":
                log(
                    f"Section {section.number}: lightweight mode discovered "
                    f"{misaligned_surface_count} structured surfaces on "
                    "misaligned pass — upgrading to full"
                )
                _write_intent_escalation_signal(
                    artifacts,
                    section.number,
                    "structured_surfaces_on_lightweight_misaligned",
                    misaligned_surface_count,
                )
                intent_mode = "full"

            if intent_mode == "full" and _has_definition_gap_surfaces(
                misaligned_surfaces,
            ):
                expansion_max = intent_budgets.get("intent_expansion_max", 2)
                expansion_count = getattr(
                    run_proposal_loop,
                    "_expansion_counts",
                    {},
                ).get(section.number, 0)
                if expansion_count < expansion_max:
                    log(
                        f"Section {section.number}: definition-gap surfaces "
                        f"found on misaligned pass — running expansion"
                    )
                    delta_result = run_expansion_cycle(
                        section.number,
                        planspace,
                        codespace,
                        parent,
                        budgets=intent_budgets,
                    )
                    if not hasattr(run_proposal_loop, "_expansion_counts"):
                        run_proposal_loop._expansion_counts = {}
                    run_proposal_loop._expansion_counts[section.number] = (
                        expansion_count + 1
                    )
                    if delta_result.get("needs_user_input"):
                        gate_response = handle_user_gate(
                            section.number,
                            planspace,
                            parent,
                            delta_result,
                        )
                        if gate_response and gate_response.startswith("resume"):
                            payload = gate_response.partition(":")[2].strip()
                            if payload:
                                persist_decision(
                                    planspace, section.number, payload,
                                )
                else:
                    log(
                        f"Section {section.number}: definition-gap surfaces "
                        f"found on misaligned pass but expansion budget is "
                        f"exhausted ({expansion_count}/{expansion_max})"
                    )

        proposal_problems = problems
        short = problems[:200]
        log(
            f"Section {section.number}: integration proposal problems "
            f"(attempt {proposal_attempt}): {short}"
        )
        mailbox_send(
            planspace,
            parent,
            f"summary:proposal-align:{section.number}:"
            f"PROBLEMS-attempt-{proposal_attempt}:{short}",
        )

    return proposal_problems or ""
