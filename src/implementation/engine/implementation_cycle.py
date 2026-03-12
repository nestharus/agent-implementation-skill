from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from intake.service.assessment import write_post_impl_assessment_prompt
from dispatch.prompt.writers import write_impl_alignment_prompt, write_strategic_impl_prompt
from implementation.service.traceability import _write_traceability_index
from implementation.service.change_verifier import verify_changed_files
from implementation.service.trace_map import build_trace_map
from flow.types.schema import TaskSpec


# ---------------------------------------------------------------------------
# Loop-control sentinels (private to this module)
# ---------------------------------------------------------------------------

_ABORT = "ABORT"       # return None from loop
_CONTINUE = "CONTINUE" # continue to next iteration
_PROCEED = "PROCEED"   # fall through, keep going in current iteration


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
    pre_hashes = Services.staleness().snapshot_files(codespace, all_known_paths)

    impl_problems: str | None = None
    impl_attempt = 0

    while True:
        if _should_abort(planspace, parent, section.number):
            return None

        impl_attempt += 1

        budget_action = _check_budget(
            impl_attempt, cycle_budget, paths, planspace, parent,
            section.number, cycle_budget_path,
        )
        if budget_action == _ABORT:
            return None

        _log_attempt(section.number, impl_attempt, impl_problems)

        impl_result = _dispatch_implementation(
            section, planspace, codespace, parent, policy, paths, artifacts,
            impl_problems,
        )
        if impl_result is None:
            return None

        dispatch_action = _handle_post_dispatch(
            impl_result, section.number, planspace, parent, paths, artifacts,
            codespace,
        )
        if dispatch_action == _ABORT:
            return None
        if dispatch_action == _CONTINUE:
            continue

        align_result = _dispatch_alignment_check(
            section, planspace, codespace, parent, policy, artifacts,
        )
        if align_result is None:
            return None

        timeout_action = _handle_alignment_timeout(
            align_result, section.number,
        )
        if timeout_action == _CONTINUE:
            impl_problems = "Previous alignment check timed out."
            continue

        problems = _extract_alignment_problems(
            align_result, section.number, planspace, parent, codespace,
            policy, artifacts,
        )

        underspec_action = _handle_underspec_signal(
            align_result, section.number, planspace, parent, codespace,
            artifacts,
        )
        if underspec_action == _ABORT:
            return None
        if underspec_action == _CONTINUE:
            continue

        if problems is None:
            Services.logger().log(f"Section {section.number}: implementation ALIGNED")
            Services.communicator().mailbox_send(
                planspace,
                parent,
                f"summary:impl-align:{section.number}:ALIGNED",
            )
            break

        impl_problems = problems
        _log_alignment_problems(
            section.number, impl_attempt, problems, planspace, parent,
        )

    return _finalize(
        planspace, codespace, section, pre_hashes,
    )


# ---------------------------------------------------------------------------
# Pre-loop guard checks
# ---------------------------------------------------------------------------


def _should_abort(
    planspace: Path, parent: str, section_number: str,
) -> bool:
    """Return True if a pending message or alignment change requires abort."""
    if Services.pipeline_control().handle_pending_messages(planspace, [], set()):
        Services.communicator().mailbox_send(planspace, parent, f"fail:{section_number}:aborted")
        return True

    if Services.pipeline_control().alignment_changed_pending(planspace):
        Services.logger().log(
            f"Section {section_number}: alignment changed — "
            "aborting section to restart Phase 1"
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------


def _check_budget(
    impl_attempt: int,
    cycle_budget: dict,
    paths: PathRegistry,
    planspace: Path,
    parent: str,
    section_number: str,
    cycle_budget_path: Path,
) -> str:
    """Enforce the implementation cycle budget.

    Returns ``_ABORT`` if the parent declines to resume, ``_PROCEED``
    otherwise (including after a successful budget reload).
    """
    if impl_attempt <= cycle_budget["implementation_max"]:
        return _PROCEED

    Services.logger().log(
        f"Section {section_number}: implementation cycle budget "
        f"exhausted ({cycle_budget['implementation_max']} attempts)"
    )
    budget_signal = {
        "section": section_number,
        "loop": "implementation",
        "attempts": impl_attempt - 1,
        "budget": cycle_budget["implementation_max"],
        "escalate": True,
    }
    budget_signal_path = paths.impl_budget_exhausted_signal(section_number)
    Services.artifact_io().write_json(budget_signal_path, budget_signal)
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"budget-exhausted:{section_number}:implementation:{impl_attempt - 1}",
    )
    response = Services.pipeline_control().pause_for_parent(
        planspace,
        parent,
        f"pause:budget_exhausted:{section_number}:implementation loop exceeded "
        f"{cycle_budget['implementation_max']} attempts",
    )
    if not response.startswith("resume"):
        return _ABORT
    reloaded = Services.artifact_io().read_json(cycle_budget_path)
    if reloaded is not None:
        cycle_budget.update(reloaded)
    return _PROCEED


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _log_attempt(
    section_number: str, impl_attempt: int, impl_problems: str | None,
) -> None:
    """Log the start of an implementation attempt."""
    tag = "fix " if impl_problems else ""
    Services.logger().log(
        f"Section {section_number}: {tag}strategic implementation "
        f"(attempt {impl_attempt})"
    )


def _log_alignment_problems(
    section_number: str,
    impl_attempt: int,
    problems: str,
    planspace: Path,
    parent: str,
) -> None:
    """Log and notify parent about alignment problems found."""
    short = problems[:200]
    Services.logger().log(
        f"Section {section_number}: implementation problems "
        f"(attempt {impl_attempt}): {short}"
    )
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"summary:impl-align:{section_number}:PROBLEMS-attempt-{impl_attempt}:{short}",
    )


# ---------------------------------------------------------------------------
# Implementation dispatch
# ---------------------------------------------------------------------------


def _dispatch_implementation(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    paths: PathRegistry,
    artifacts: Path,
    impl_problems: str | None,
) -> str | None:
    """Write the implementation prompt, dispatch to the agent, return result.

    Returns ``None`` when the caller should ``return None`` (prompt
    blocked, alignment changed, or timeout).
    """
    impl_prompt = write_strategic_impl_prompt(
        section,
        planspace,
        codespace,
        impl_problems,
        model_policy=policy,
    )
    if impl_prompt is None:
        Services.logger().log(
            f"Section {section.number}: strategic impl prompt "
            f"blocked by template safety — skipping dispatch"
        )
        return None

    impl_output = artifacts / f"impl-{section.number}-output.md"
    impl_agent = f"impl-{section.number}"
    impl_result = Services.dispatcher().dispatch(
        Services.policies().resolve(policy, "implementation"),
        impl_prompt,
        impl_output,
        planspace,
        parent,
        impl_agent,
        codespace=codespace,
        section_number=section.number,
        agent_file=Services.task_router().agent_for("implementation.strategic"),
    )
    if impl_result == "ALIGNMENT_CHANGED_PENDING":
        return None

    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"summary:impl:{section.number}:{Services.dispatch_helpers().summarize_output(impl_result)}",
    )

    if impl_result.startswith("TIMEOUT:"):
        Services.logger().log(f"Section {section.number}: implementation agent timed out")
        Services.communicator().mailbox_send(
            planspace,
            parent,
            f"fail:{section.number}:implementation agent timed out",
        )
        return None

    return impl_result


# ---------------------------------------------------------------------------
# Post-dispatch: task ingestion + signal handling
# ---------------------------------------------------------------------------


def _handle_post_dispatch(
    impl_result: str,
    section_number: str,
    planspace: Path,
    parent: str,
    paths: PathRegistry,
    artifacts: Path,
    codespace: Path,
) -> str:
    """Ingest tasks and check agent signals after implementation dispatch.

    Returns ``_ABORT``, ``_CONTINUE``, or ``_PROCEED``.
    """
    Services.flow_ingestion().ingest_and_submit(
        planspace,
        db_path=paths.run_db(),
        submitted_by=f"implementation-{section_number}",
        signal_path=paths.task_request_signal("impl", section_number),
        origin_refs=[str(artifacts / f"impl-{section_number}-output.md")],
    )

    paths.signals_dir().mkdir(parents=True, exist_ok=True)
    signal, detail = Services.dispatch_helpers().check_agent_signals(
        impl_result,
        signal_path=paths.impl_signal(section_number),
        output_path=artifacts / f"impl-{section_number}-output.md",
        planspace=planspace,
        parent=parent,
        codespace=codespace,
    )
    if signal:
        return _handle_signal_pause(
            signal, detail, section_number, planspace, parent,
        )
    return _PROCEED


def _handle_signal_pause(
    signal: str,
    detail: str,
    section_number: str,
    planspace: Path,
    parent: str,
) -> str:
    """Pause for parent after an agent signal; return loop action."""
    response = Services.pipeline_control().pause_for_parent(
        planspace,
        parent,
        f"pause:{signal}:{section_number}:{detail}",
    )
    if not response.startswith("resume"):
        return _ABORT
    payload = response.partition(":")[2].strip()
    if payload:
        Services.cross_section().persist_decision(planspace, section_number, payload)
    if Services.pipeline_control().alignment_changed_pending(planspace):
        return _ABORT
    return _CONTINUE


# ---------------------------------------------------------------------------
# Alignment check dispatch
# ---------------------------------------------------------------------------


def _dispatch_alignment_check(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    artifacts: Path,
) -> str | None:
    """Dispatch the alignment check agent. Return result or None to abort."""
    Services.logger().log(f"Section {section.number}: implementation alignment check")
    impl_align_prompt = write_impl_alignment_prompt(
        section,
        planspace,
        codespace,
    )
    impl_align_output = artifacts / f"impl-align-{section.number}-output.md"
    impl_align_result = Services.dispatcher().dispatch(
        Services.policies().resolve(policy, "alignment"),
        impl_align_prompt,
        impl_align_output,
        planspace,
        parent,
        codespace=codespace,
        section_number=section.number,
        agent_file=Services.task_router().agent_for("staleness.alignment_check"),
    )
    if impl_align_result == "ALIGNMENT_CHANGED_PENDING":
        return None

    return impl_align_result


def _handle_alignment_timeout(
    impl_align_result: str, section_number: str,
) -> str:
    """Return ``_CONTINUE`` if the alignment check timed out, else ``_PROCEED``."""
    if impl_align_result.startswith("TIMEOUT:"):
        Services.logger().log(
            f"Section {section_number}: implementation alignment check "
            f"timed out — retrying"
        )
        return _CONTINUE
    return _PROCEED


def _extract_alignment_problems(
    impl_align_result: str,
    section_number: str,
    planspace: Path,
    parent: str,
    codespace: Path,
    policy: dict,
    artifacts: Path,
) -> str | None:
    """Extract alignment problems from the alignment check result."""
    impl_align_output = artifacts / f"impl-align-{section_number}-output.md"
    return Services.section_alignment().extract_problems(
        impl_align_result,
        output_path=impl_align_output,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
        adjudicator_model=Services.policies().resolve(policy, "adjudicator"),
    )


def _handle_underspec_signal(
    impl_align_result: str,
    section_number: str,
    planspace: Path,
    parent: str,
    codespace: Path,
    artifacts: Path,
) -> str:
    """Check for underspec signal after alignment; return loop action."""
    paths = PathRegistry(planspace)
    impl_align_output = artifacts / f"impl-align-{section_number}-output.md"
    signal, detail = Services.dispatch_helpers().check_agent_signals(
        impl_align_result,
        signal_path=paths.signals_dir() / f"impl-align-{section_number}-signal.json",
        output_path=impl_align_output,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
    )
    if signal == "underspec":
        return _handle_signal_pause(
            signal, detail, section_number, planspace, parent,
        )
    return _PROCEED


# ---------------------------------------------------------------------------
# Post-loop: verification, traceability, assessment
# ---------------------------------------------------------------------------


def _finalize(
    planspace: Path,
    codespace: Path,
    section,
    pre_hashes: dict[str, str],
) -> list[str]:
    """Verify changes, record traceability, build trace map, queue assessment."""
    actually_changed = verify_changed_files(
        planspace, codespace, section, pre_hashes,
    )

    for changed_file in actually_changed:
        Services.communicator().record_traceability(
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
        Services.logger().log(
            f"Section {section_number}: post-implementation assessment "
            "prompt blocked — skipping dispatch"
        )
        return

    Services.flow_ingestion().submit_chain(
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
