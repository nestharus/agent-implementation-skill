from __future__ import annotations

from pathlib import Path

from proposal.repository.excerpts import exists as excerpt_exists
from orchestrator.path_registry import PathRegistry
from containers import Services
from dispatch.prompt.writers import write_section_setup_prompt
from signals.service.blocker_manager import (
    _append_open_problem,
    _update_blocker_rollup,
)
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import ACTION_CONTINUE, SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE, TRUNCATE_DETAIL


def _write_scope_delta(
    paths: PathRegistry, signal_dir: Path, section_number: str, detail: str,
) -> None:
    """Write a scope delta artifact for an out-of-scope signal."""
    scope_delta_dir = paths.scope_deltas_dir()
    scope_delta_dir.mkdir(parents=True, exist_ok=True)
    setup_sig_path = signal_dir / f"setup-{section_number}-signal.json"
    signal_payload = Services.artifact_io().read_json_or_default(setup_sig_path, {})
    scope_delta = {
        "delta_id": f"delta-{section_number}-setup-oos",
        "section": section_number,
        "signal": SIGNAL_OUT_OF_SCOPE,
        "detail": detail,
        "requires_root_reframing": True,
        "signal_path": str(setup_sig_path),
        "signal_payload": signal_payload,
    }
    Services.artifact_io().write_json(
        paths.scope_delta_section(section_number),
        scope_delta,
    )


def _handle_setup_signal(
    signal: str, detail: str, planspace: Path, parent: str,
    section_number: str,
) -> str | None:
    """Handle a setup agent signal. Returns None to abort, 'continue' to retry."""
    if signal in (SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE):
        _append_open_problem(planspace, section_number, detail, signal)
        Services.communicator().mailbox_send(
            planspace, parent,
            f"open-problem:{section_number}:{signal}:{detail[:TRUNCATE_DETAIL]}",
        )
    if signal == SIGNAL_OUT_OF_SCOPE:
        paths = PathRegistry(planspace)
        _write_scope_delta(paths, paths.signals_dir(), section_number, detail)
    _update_blocker_rollup(planspace)
    response = Services.pipeline_control().pause_for_parent(
        planspace, parent,
        f"pause:{signal}:{section_number}:{detail}",
    )
    if not response.startswith("resume"):
        return None
    payload = response.partition(":")[2].strip()
    if payload:
        Services.cross_section().persist_decision(planspace, section_number, payload)
    if Services.pipeline_control().alignment_changed_pending(planspace):
        return None
    return ACTION_CONTINUE


def extract_excerpts(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
) -> str | None:
    """Run the setup loop until both proposal and alignment excerpts exist."""
    policy = Services.policies().load(planspace)
    paths = PathRegistry(planspace)
    signal_dir = paths.signals_dir()
    signal_dir.mkdir(parents=True, exist_ok=True)

    while (
        not excerpt_exists(planspace, section.number, "proposal")
        or not excerpt_exists(planspace, section.number, "alignment")
    ):
        Services.logger().log(f"Section {section.number}: setup — extracting excerpts")
        setup_prompt = write_section_setup_prompt(
            section, planspace, codespace,
            section.global_proposal_path, section.global_alignment_path,
        )
        setup_output = paths.artifacts / f"setup-{section.number}-output.md"
        setup_agent = f"setup-{section.number}"
        output = Services.dispatcher().dispatch(
            policy["setup"], setup_prompt, setup_output,
            planspace, parent, setup_agent,
            codespace=codespace, section_number=section.number,
            agent_file=Services.task_router().agent_for("proposal.section_setup"),
        )
        if output == ALIGNMENT_CHANGED_PENDING:
            return None
        Services.communicator().mailbox_send(
            planspace, parent,
            f"summary:setup:{section.number}:{Services.dispatch_helpers().summarize_output(output)}",
        )

        signal, detail = Services.dispatch_helpers().check_agent_signals(
            signal_path=signal_dir / f"setup-{section.number}-signal.json",
        )
        if signal:
            result = _handle_setup_signal(
                signal, detail, planspace, parent,
                section.number,
            )
            if result is None:
                return None
            continue

        if (
            not excerpt_exists(planspace, section.number, "proposal")
            or not excerpt_exists(planspace, section.number, "alignment")
        ):
            Services.logger().log(
                f"Section {section.number}: ERROR — setup failed to create "
                f"excerpt files"
            )
            Services.communicator().mailbox_send(
                planspace, parent,
                f"fail:{section.number}:setup failed to create excerpt files",
            )
            return None
        break

    return "ok"
