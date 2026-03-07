from __future__ import annotations

from pathlib import Path

from lib.artifact_io import read_json_or_default, write_json
from lib.alignment_change_tracker import check_pending as alignment_changed_pending
from lib.excerpt_repository import exists as excerpt_exists
from lib.path_registry import PathRegistry
from section_loop.communication import mailbox_send, log
from section_loop.cross_section import persist_decision
from section_loop.dispatch import (
    check_agent_signals,
    dispatch_agent,
    summarize_output,
)
from section_loop.pipeline_control import pause_for_parent
from section_loop.prompts import write_section_setup_prompt
from section_loop.section_engine.blockers import (
    _append_open_problem,
    _update_blocker_rollup,
)


def extract_excerpts(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
) -> str | None:
    """Run the setup loop until both proposal and alignment excerpts exist."""
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    signal_dir = artifacts / "signals"
    signal_dir.mkdir(parents=True, exist_ok=True)

    while (
        not excerpt_exists(planspace, section.number, "proposal")
        or not excerpt_exists(planspace, section.number, "alignment")
    ):
        log(f"Section {section.number}: setup — extracting excerpts")
        setup_prompt = write_section_setup_prompt(
            section,
            planspace,
            codespace,
            section.global_proposal_path,
            section.global_alignment_path,
        )
        setup_output = artifacts / f"setup-{section.number}-output.md"
        setup_agent = f"setup-{section.number}"
        output = dispatch_agent(
            policy["setup"],
            setup_prompt,
            setup_output,
            planspace,
            parent,
            setup_agent,
            codespace=codespace,
            section_number=section.number,
            agent_file="setup-excerpter.md",
        )
        if output == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(
            planspace,
            parent,
            f"summary:setup:{section.number}:{summarize_output(output)}",
        )

        signal, detail = check_agent_signals(
            output,
            signal_path=signal_dir / f"setup-{section.number}-signal.json",
            output_path=setup_output,
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
                setup_sig_path = signal_dir / f"setup-{section.number}-signal.json"
                signal_payload = read_json_or_default(setup_sig_path, {})
                scope_delta = {
                    "delta_id": f"delta-{section.number}-setup-oos",
                    "section": section.number,
                    "signal": "out_of_scope",
                    "detail": detail,
                    "requires_root_reframing": True,
                    "signal_path": str(setup_sig_path),
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

        if (
            not excerpt_exists(planspace, section.number, "proposal")
            or not excerpt_exists(planspace, section.number, "alignment")
        ):
            log(
                f"Section {section.number}: ERROR — setup failed to create "
                f"excerpt files"
            )
            mailbox_send(
                planspace,
                parent,
                f"fail:{section.number}:setup failed to create excerpt files",
            )
            return None
        break

    return "ok"
