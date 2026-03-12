from __future__ import annotations

from pathlib import Path

from staleness.service.change_tracker import check_pending as alignment_changed_pending
from signals.repository.artifact_io import read_json, write_json
from dispatch.service.model_policy import resolve
from orchestrator.path_registry import PathRegistry
from flow.engine.submitter import submit_chain
from intake.service.assessment import write_post_impl_assessment_prompt
from staleness.service.section_alignment import _extract_problems
from staleness.helpers.detection import snapshot_files
from signals.service.communication import _record_traceability, log, mailbox_send
from coordination.service.cross_section import persist_decision
from dispatch.engine.section_dispatch import dispatch_agent
from dispatch.helpers.utils import check_agent_signals, summarize_output
from orchestrator.service.pipeline_control import handle_pending_messages, pause_for_parent
from dispatch.prompt.writers import write_impl_alignment_prompt, write_strategic_impl_prompt
from flow.service.section_ingestion import ingest_and_submit
from implementation.service.traceability import _write_traceability_index
from implementation.service.change_verifier import verify_changed_files
from implementation.service.trace_map import build_trace_map
from flow.types.schema import TaskSpec
from taskrouter import agent_for


def run_implementation_loop(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    cycle_budget: dict,
) -> list[str] | None:
    """Run strategic implementation until aligned, then return changed files."""
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    cycle_budget_path = paths.cycle_budget(section.number)

    all_known_paths = list(section.related_files)
    pre_hashes = snapshot_files(codespace, all_known_paths)

    impl_problems: str | None = None
    impl_attempt = 0

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

        impl_attempt += 1

        if impl_attempt > cycle_budget["implementation_max"]:
            log(
                f"Section {section.number}: implementation cycle budget "
                f"exhausted ({cycle_budget['implementation_max']} attempts)"
            )
            budget_signal = {
                "section": section.number,
                "loop": "implementation",
                "attempts": impl_attempt - 1,
                "budget": cycle_budget["implementation_max"],
                "escalate": True,
            }
            budget_signal_path = paths.impl_budget_exhausted_signal(section.number)
            write_json(budget_signal_path, budget_signal)
            mailbox_send(
                planspace,
                parent,
                f"budget-exhausted:{section.number}:implementation:{impl_attempt - 1}",
            )
            response = pause_for_parent(
                planspace,
                parent,
                f"pause:budget_exhausted:{section.number}:implementation loop exceeded "
                f"{cycle_budget['implementation_max']} attempts",
            )
            if not response.startswith("resume"):
                return None
            reloaded = read_json(cycle_budget_path)
            if reloaded is not None:
                cycle_budget.update(reloaded)

        tag = "fix " if impl_problems else ""
        log(
            f"Section {section.number}: {tag}strategic implementation "
            f"(attempt {impl_attempt})"
        )

        impl_prompt = write_strategic_impl_prompt(
            section,
            planspace,
            codespace,
            impl_problems,
            model_policy=policy,
        )
        if impl_prompt is None:
            log(
                f"Section {section.number}: strategic impl prompt "
                f"blocked by template safety — skipping dispatch"
            )
            return None
        impl_output = artifacts / f"impl-{section.number}-output.md"
        impl_agent = f"impl-{section.number}"
        impl_result = dispatch_agent(
            resolve(policy, "implementation"),
            impl_prompt,
            impl_output,
            planspace,
            parent,
            impl_agent,
            codespace=codespace,
            section_number=section.number,
            agent_file=agent_for("implementation.strategic"),
        )
        if impl_result == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(
            planspace,
            parent,
            f"summary:impl:{section.number}:{summarize_output(impl_result)}",
        )

        if impl_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: implementation agent timed out")
            mailbox_send(
                planspace,
                parent,
                f"fail:{section.number}:implementation agent timed out",
            )
            return None

        ingest_and_submit(
            planspace,
            db_path=paths.run_db(),
            submitted_by=f"implementation-{section.number}",
            signal_path=paths.task_request_signal("impl", section.number),
            origin_refs=[str(artifacts / f"impl-{section.number}-output.md")],
        )

        paths.signals_dir().mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            impl_result,
            signal_path=paths.impl_signal(section.number),
            output_path=artifacts / f"impl-{section.number}-output.md",
            planspace=planspace,
            parent=parent,
            codespace=codespace,
        )
        if signal:
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

        log(f"Section {section.number}: implementation alignment check")
        impl_align_prompt = write_impl_alignment_prompt(
            section,
            planspace,
            codespace,
        )
        impl_align_output = artifacts / f"impl-align-{section.number}-output.md"
        impl_align_result = dispatch_agent(
            resolve(policy, "alignment"),
            impl_align_prompt,
            impl_align_output,
            planspace,
            parent,
            codespace=codespace,
            section_number=section.number,
            agent_file=agent_for("staleness.alignment_check"),
        )
        if impl_align_result == "ALIGNMENT_CHANGED_PENDING":
            return None

        if impl_align_result.startswith("TIMEOUT:"):
            log(
                f"Section {section.number}: implementation alignment check "
                f"timed out — retrying"
            )
            impl_problems = "Previous alignment check timed out."
            continue

        problems = _extract_problems(
            impl_align_result,
            output_path=impl_align_output,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
            adjudicator_model=resolve(policy, "adjudicator"),
        )

        signal, detail = check_agent_signals(
            impl_align_result,
            signal_path=paths.signals_dir() / f"impl-align-{section.number}-signal.json",
            output_path=impl_align_output,
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
            log(f"Section {section.number}: implementation ALIGNED")
            mailbox_send(
                planspace,
                parent,
                f"summary:impl-align:{section.number}:ALIGNED",
            )
            break

        impl_problems = problems
        short = problems[:200]
        log(
            f"Section {section.number}: implementation problems "
            f"(attempt {impl_attempt}): {short}"
        )
        mailbox_send(
            planspace,
            parent,
            f"summary:impl-align:{section.number}:PROBLEMS-attempt-{impl_attempt}:{short}",
        )

    actually_changed = verify_changed_files(
        planspace, codespace, section, pre_hashes,
    )

    for changed_file in actually_changed:
        _record_traceability(
            planspace,
            section.number,
            changed_file,
            f"section-{section.number}-integration-proposal.md",
            "implementation change",
        )

    _write_traceability_index(planspace, section, codespace, actually_changed)

    build_trace_map(
        planspace, codespace, section.number,
        actually_changed, list(section.related_files),
    )
    _dispatch_post_impl_assessment(section.number, planspace, codespace)

    return actually_changed


def _dispatch_post_impl_assessment(
    section_number: str,
    planspace: Path,
    codespace: Path,
) -> None:
    """Queue a post-implementation governance assessment for a section."""
    paths = PathRegistry(planspace)
    prompt_path = write_post_impl_assessment_prompt(
        section_number,
        planspace,
        codespace,
    )
    if prompt_path is None:
        log(
            f"Section {section_number}: post-implementation assessment "
            "prompt blocked — skipping dispatch"
        )
        return

    submit_chain(
        paths.run_db(),
        f"post-impl-{section_number}",
        [
            TaskSpec(
                task_type="implementation.post_assessment",
                concern_scope=f"section-{section_number}",
                payload_path=str(prompt_path),
                problem_id=f"post-impl-{section_number}",
            )
        ],
        origin_refs=[
            str(paths.trace_dir() / f"section-{section_number}.json"),
            str(paths.trace_map(section_number)),
            str(paths.proposal(section_number)),
        ],
        planspace=planspace,
    )
