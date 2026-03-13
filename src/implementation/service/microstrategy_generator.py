from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from pipeline.template import TASK_SUBMISSION_SEMANTICS
from dispatch.prompt.writers import agent_mail_instructions
from implementation.service.microstrategy_decider import _check_needs_microstrategy
from dispatch.types import ALIGNMENT_CHANGED_PENDING


def _build_microstrategy_prompt(
    section,
    paths: PathRegistry,
    codespace: Path,
    planspace: Path,
    integration_proposal: Path,
    microstrategy_path: Path,
    agent_name: str,
) -> str:
    file_list = "\n".join(
        f"- `{codespace / relative_path}`" for relative_path in section.related_files
    )
    todos_ref = ""
    section_todos = paths.todos(section.number)
    if section_todos.exists():
        todos_ref = f"\nRead the TODO extraction: `{section_todos}`"

    governance_ref = ""
    governance_packet = paths.governance_packet(section.number)
    if governance_packet.exists():
        governance_ref = f"\nRead the governance packet: `{governance_packet}`"

    return _compose_microstrategy_text(
        section.number, integration_proposal,
        paths.alignment_excerpt(section.number), todos_ref, governance_ref,
        file_list, microstrategy_path,
        paths.task_request_signal("micro", section.number),
        planspace, agent_name,
    )


def _compose_microstrategy_text(
    section_number: str,
    integration_proposal: Path,
    alignment_excerpt: Path,
    todos_ref: str,
    governance_ref: str,
    file_list: str,
    microstrategy_path: Path,
    task_request_signal: Path,
    planspace: Path,
    agent_name: str,
) -> str:
    """Return the full prompt text for microstrategy generation."""
    return f"""# Task: Microstrategy for Section {section_number}

## Context
Read the integration proposal: `{integration_proposal}`
Read the alignment excerpt: `{alignment_excerpt}`{todos_ref}{governance_ref}

## Related Files
{file_list}

## Instructions

The integration proposal describes the HIGH-LEVEL strategy for this
section. Your job is to produce a MICROSTRATEGY — a tactical per-file
breakdown that an implementation agent can follow directly.

For each file that needs changes, write:
1. **File path** and whether it's new or modified
2. **What changes** — specific functions, classes, or blocks to add/modify
3. **Order** — which file changes depend on which others
4. **Risks** — what could go wrong with this specific change

Write the microstrategy to: `{microstrategy_path}`

Keep it tactical and concrete. The integration proposal already justified
WHY — you're capturing WHAT and WHERE at the file level.

## Task Submission

If you need deeper analysis, submit a task request to:
`{task_request_signal}`

Available task types: scan_deep_analyze, scan_explore

Write a single JSON object (legacy format), or use the v2 envelope
format with chain or fanout actions — see your agent file for the full
v2 format reference. {TASK_SUBMISSION_SEMANTICS}
{agent_mail_instructions(planspace, agent_name, f"{agent_name}-monitor")}
"""


def _dispatch_and_retry(
    section_number: str,
    policy: dict,
    micro_prompt_path: Path,
    artifacts: Path,
    planspace: Path,
    parent: str,
    agent_name: str,
    codespace: Path,
    microstrategy_path: Path,
) -> str | None:
    """Dispatch microstrategy generation with escalation retry. Returns sentinel or None."""
    ctrl = Services.pipeline_control().poll_control_messages(
        planspace, parent, current_section=section_number,
    )
    if ctrl == "alignment_changed":
        return ALIGNMENT_CHANGED_PENDING

    micro_output_path = artifacts / f"microstrategy-{section_number}-output.md"
    micro_result = Services.dispatcher().dispatch(
        Services.policies().resolve(policy, "implementation"),
        micro_prompt_path, micro_output_path,
        planspace, parent, agent_name,
        codespace=codespace, section_number=section_number,
        agent_file=Services.task_router().agent_for("implementation.microstrategy"),
    )
    if micro_result == ALIGNMENT_CHANGED_PENDING:
        return micro_result

    paths = PathRegistry(planspace)
    Services.flow_ingestion().ingest_and_submit(
        planspace, db_path=planspace / "run.db",
        submitted_by=f"microstrategy-{section_number}",
        signal_path=paths.task_request_signal("micro", section_number),
        origin_refs=[str(microstrategy_path)],
    )

    if not microstrategy_path.exists() or microstrategy_path.stat().st_size == 0:
        Services.logger().log(
            f"Section {section_number}: microstrategy missing after "
            f"dispatch — retrying with escalation model"
        )
        escalation_output = artifacts / f"microstrategy-{section_number}-escalation-output.md"
        escalated_result = Services.dispatcher().dispatch(
            Services.policies().resolve(policy, "escalation_model"),
            micro_prompt_path, escalation_output,
            planspace, parent, f"{agent_name}-escalation",
            codespace=codespace, section_number=section_number,
            agent_file=Services.task_router().agent_for("implementation.microstrategy"),
        )
        if escalated_result == ALIGNMENT_CHANGED_PENDING:
            return escalated_result

    return None


def _handle_microstrategy_failure(
    section_number: str,
    paths: PathRegistry,
    planspace: Path,
    parent: str,
) -> None:
    Services.logger().log(
        f"Section {section_number}: microstrategy generation "
        f"failed — emitting blocker signal"
    )
    blocker = {
        "state": "NEEDS_PARENT",
        "section": str(section_number),
        "detail": "Microstrategy generation failed after primary + escalation attempts",
        "needs": "Tactical breakdown from upstream or decision to proceed without microstrategy",
    }
    Services.artifact_io().write_json(
        paths.microstrategy_blocker_signal(section_number), blocker,
    )
    Services.communicator().record_traceability(
        planspace, section_number,
        f"microstrategy-blocker-{section_number}.json",
        f"section-{section_number}-integration-proposal.md",
        "microstrategy generation failed — blocker emitted",
    )
    Services.communicator().mailbox_send(
        planspace, parent, f"summary:microstrategy:{section_number}:blocked",
    )


def run_microstrategy(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
) -> Path | None:
    """Run the microstrategy decider and generation flow when needed."""
    policy = Services.policies().load(planspace)
    paths = PathRegistry(planspace)
    integration_proposal = paths.proposal(section.number)
    microstrategy_path = paths.microstrategy(section.number)

    needs_microstrategy = (
        _check_needs_microstrategy(
            integration_proposal, planspace, section.number, parent,
            codespace=codespace,
            model=Services.policies().resolve(policy, "microstrategy_decider"),
            escalation_model=Services.policies().resolve(policy, "escalation_model"),
        )
        and not microstrategy_path.exists()
    )
    if not needs_microstrategy and not microstrategy_path.exists():
        Services.logger().log(
            f"Section {section.number}: microstrategy decider did not "
            f"request microstrategy — skipping"
        )
        return None
    if not needs_microstrategy:
        return microstrategy_path if microstrategy_path.exists() else None

    Services.logger().log(f"Section {section.number}: generating microstrategy")
    agent_name = f"microstrategy-{section.number}"
    micro_prompt_path = paths.artifacts / f"microstrategy-{section.number}-prompt.md"

    rendered = _build_microstrategy_prompt(
        section, paths, codespace, planspace,
        integration_proposal, microstrategy_path, agent_name,
    )
    violations = Services.prompt_guard().validate_dynamic(rendered)
    if violations:
        Services.logger().log(
            f"  ERROR: prompt {micro_prompt_path.name} blocked — "
            f"template violations: {violations}"
        )
        return None
    micro_prompt_path.write_text(rendered, encoding="utf-8")
    Services.communicator().log_artifact(planspace, f"prompt:microstrategy-{section.number}")

    sentinel = _dispatch_and_retry(
        section.number, policy, micro_prompt_path, paths.artifacts,
        planspace, parent, agent_name, codespace, microstrategy_path,
    )
    if sentinel:
        return None

    if microstrategy_path.exists() and microstrategy_path.stat().st_size > 0:
        Services.logger().log(f"Section {section.number}: microstrategy generated")
        Services.communicator().record_traceability(
            planspace, section.number,
            f"section-{section.number}-microstrategy.md",
            f"section-{section.number}-integration-proposal.md",
            "tactical breakdown from integration proposal",
        )
        Services.communicator().mailbox_send(
            planspace, parent, f"summary:microstrategy:{section.number}:generated",
        )
        return microstrategy_path

    _handle_microstrategy_failure(section.number, paths, planspace, parent)
    return None
