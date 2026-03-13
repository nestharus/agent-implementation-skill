from pathlib import Path
from typing import Any

from containers import Services
from coordination.engine.plan_executor import (
    CoordinationExecutionExit,
    execute_coordination_plan,
    read_execution_modified_files,
)
from coordination.service.planner import (
    _parse_coordination_plan,
    write_coordination_plan_prompt,
)
from coordination.service.problem_resolver import (
    _collect_outstanding_problems,
    _detect_recurrence_patterns,
)
from orchestrator.path_registry import PathRegistry
from implementation.service.scope_delta_aggregator import (
    ScopeDeltaAggregationExit,
    aggregate_scope_deltas,
)


from coordination.service.completion_handler import read_incoming_notes
from orchestrator.types import Section, SectionResult, ControlSignal
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import ALIGNMENT_INVALID_FRAME


# Coordination round limits: hard cap to prevent runaway, but rounds
# continue adaptively while problem count decreases.
MAX_COORDINATION_ROUNDS = 10  # hard safety cap
MIN_COORDINATION_ROUNDS = 2   # always try at least this many


# ---------------------------------------------------------------------------
# Phase 1: Collect problems + escalate recurring patterns
# ---------------------------------------------------------------------------

def _collect_and_persist_problems(
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    paths: PathRegistry,
) -> tuple[list[dict], dict | None] | None:
    """Collect outstanding problems, detect recurrence, persist state.

    Returns ``(problems, recurrence)`` or ``None`` if no problems exist.
    """
    problems = _collect_outstanding_problems(
        section_results, sections_by_num, planspace,
    )

    if not problems:
        Services.logger().log("  coordinator: no outstanding problems — all ALIGNED")
        return None

    Services.logger().log(f"  coordinator: {len(problems)} outstanding problems across "
        f"{len({p['section'] for p in problems})} sections")

    policy = Services.policies().load(planspace)
    recurrence = _detect_recurrence_patterns(planspace, problems)
    if recurrence:
        escalation_file = paths.coordination_model_escalation()
        escalation_file.write_text(
            Services.policies().resolve(policy, "escalation_model"), encoding="utf-8")
        Services.logger().log(f"  coordinator: recurrence escalation — setting model to "
            f"{Services.policies().resolve(policy, 'escalation_model')} for "
            f"{recurrence['recurring_problem_count']} recurring problems "
            f"across sections {recurrence['recurring_sections']}")

    state_path = paths.coordination_problems()
    Services.artifact_io().write_json(state_path, problems)
    Services.communicator().log_artifact(planspace,"coordination:problems")

    return problems, recurrence


# ---------------------------------------------------------------------------
# Phase 2: Build coordination plan via planner agent
# ---------------------------------------------------------------------------

def _dispatch_and_parse_plan(
    problems: list[dict],
    planspace: Path,
    parent: str,
    paths: PathRegistry,
    policy: dict,
) -> dict | None:
    """Dispatch planner agent with retry, return parsed plan or None."""
    coord_dir = paths.coordination_dir()
    plan_prompt = write_coordination_plan_prompt(problems, planspace)
    plan_output = coord_dir / "coordination-plan-output.md"
    Services.logger().log("  coordinator: dispatching coordination-planner agent")
    plan_result = Services.dispatcher().dispatch(
        Services.policies().resolve(policy, "coordination_plan"), plan_prompt, plan_output,
        planspace, parent, agent_file=Services.task_router().agent_for("coordination.plan"),
    )
    if plan_result == ALIGNMENT_CHANGED_PENDING:
        return None

    coord_plan = _parse_coordination_plan(plan_result, problems)
    if coord_plan is None:
        Services.logger().log("  coordinator: plan parse failed — retrying with "
            "escalation model")
        plan_output_retry = coord_dir / "coordination-plan-output-retry.md"
        retry_result = Services.dispatcher().dispatch(
            Services.policies().resolve(policy, "escalation_model"), plan_prompt, plan_output_retry,
            planspace, parent, agent_file=Services.task_router().agent_for("coordination.plan"),
        )
        if retry_result == ALIGNMENT_CHANGED_PENDING:
            return None
        coord_plan = _parse_coordination_plan(retry_result, problems)

    if coord_plan is None:
        Services.logger().log("  coordinator: plan parse failed after retry — fail closed")
        failure_path = coord_dir / "coordination-plan-failure.md"
        Services.artifact_io().write_json(failure_path, {
            "reason": "unparseable_plan_json",
            "attempts": 2,
        })
        Services.communicator().mailbox_send(
            planspace, parent, "fail:coordination:unparseable_plan_json",
        )
    return coord_plan


def _build_coordination_plan(
    problems: list[dict],
    planspace: Path,
    parent: str,
    paths: PathRegistry,
) -> tuple[list[list[dict[str, Any]]], list[str], dict] | None:
    """Dispatch planner agent, parse plan, build confirmed groups.

    Returns ``(confirmed_groups, group_strategies, coord_plan)`` or
    ``None`` on failure (alignment changed, parse failures).
    """
    policy = Services.policies().load(planspace)

    ctrl = Services.pipeline_control().poll_control_messages(planspace, parent)
    if ctrl == ControlSignal.ALIGNMENT_CHANGED:
        return None

    coord_plan = _dispatch_and_parse_plan(
        problems, planspace, parent, paths, policy,
    )
    if coord_plan is None:
        return None

    confirmed_groups: list[list[dict[str, Any]]] = []
    group_strategies: list[str] = []
    for g in coord_plan["groups"]:
        group_problems = [problems[i] for i in g["problems"]]
        confirmed_groups.append(group_problems)
        group_strategies.append(g.get("strategy", "sequential"))
        Services.logger().log(f"  coordinator: group {len(confirmed_groups) - 1} — "
            f"{len(group_problems)} problems, "
            f"strategy={group_strategies[-1]}, "
            f"reason={g.get('reason', '(none)')}")

    Services.logger().log(f"  coordinator: {len(confirmed_groups)} problem groups from "
        f"coordination plan")

    # Save plan and groups for debugging
    plan_path = coord_dir / "coordination-plan.json"
    Services.artifact_io().write_json(plan_path, coord_plan)
    Services.communicator().log_artifact(planspace,"coordination:plan")

    groups_path = coord_dir / "groups.json"
    groups_data = []
    for i, g in enumerate(confirmed_groups):
        groups_data.append({
            "group_id": i,
            "problem_count": len(g),
            "strategy": group_strategies[i],
            "sections": sorted({p["section"] for p in g}),
            "files": sorted({f for p in g for f in p.get("files", [])}),
        })
    Services.artifact_io().write_json(groups_path, groups_data)
    Services.communicator().log_artifact(planspace,"coordination:groups")

    return confirmed_groups, group_strategies, coord_plan


# ---------------------------------------------------------------------------
# Phase 3: Execute plan + collect modified files
# ---------------------------------------------------------------------------

def _execute_plan(
    coord_plan: dict,
    confirmed_groups: list[list[dict[str, Any]]],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> tuple[list[str], set[str]] | None:
    """Execute coordination plan. Returns (affected_sections, all_modified) or None."""
    try:
        affected_sections = execute_coordination_plan(
            {
                "coord_plan": coord_plan,
                "confirmed_groups": confirmed_groups,
            },
            sections_by_num,
            planspace,
            codespace,
            parent,
        )
    except CoordinationExecutionExit:
        return None
    all_modified = read_execution_modified_files(planspace)
    return affected_sections, all_modified


# ---------------------------------------------------------------------------
# Phase 4: Re-check alignment on affected sections
# ---------------------------------------------------------------------------


def _classify_alignment_result(
    sec_num: str,
    align_problems: str | None,
    signal: str | None,
    detail: str | None,
    section_results: dict[str, SectionResult],
    problems: list[dict],
    recurrence: dict | None,
    coord_dir: Path,
    planspace: Path,
) -> bool:
    """Classify alignment check outcome and record result.

    Returns ``True`` if aligned, ``False`` if still has problems.
    """
    if align_problems is None and signal is None:
        Services.logger().log(f"  coordinator: section {sec_num} now ALIGNED")
        section_results[sec_num] = SectionResult(
            section_number=sec_num, aligned=True,
        )
        _record_recurrence_resolution(
            sec_num, problems, recurrence, coord_dir, planspace,
        )
        return True

    Services.logger().log(f"  coordinator: section {sec_num} still has problems")
    combined_problems = align_problems or ""
    if signal:
        combined_problems += (
            f"\n[signal:{signal}] {detail}" if combined_problems
            else f"[signal:{signal}] {detail}"
        )
    section_results[sec_num] = SectionResult(
        section_number=sec_num, aligned=False,
        problems=combined_problems or None,
    )
    return False


def _recheck_section_alignment(
    sec_num: str,
    section: Section,
    section_results: dict[str, SectionResult],
    problems: list[dict],
    recurrence: dict | None,
    planspace: Path,
    codespace: Path,
    parent: str,
    paths: PathRegistry,
) -> bool | None:
    """Re-run alignment check on one section after coordination fixes.

    Returns ``True`` if aligned, ``False`` if still has problems,
    or ``None`` if alignment changed (caller should abort).
    """
    coord_dir = paths.coordination_dir()
    policy = Services.policies().load(planspace)

    notes = read_incoming_notes(section, planspace, codespace)
    if notes:
        Services.logger().log(f"  coordinator: section {sec_num} has incoming notes "
            f"from other sections")

    align_result = Services.section_alignment().run_alignment_check(
        section, planspace, codespace, parent, sec_num,
        output_prefix="coord-align",
        model=Services.policies().resolve(policy, "alignment"),
        adjudicator_model=Services.policies().resolve(policy, "adjudicator"),
    )
    if align_result == ALIGNMENT_CHANGED_PENDING:
        return None
    if align_result == ALIGNMENT_INVALID_FRAME:
        Services.logger().log(f"  coordinator: section {sec_num} invalid alignment "
            f"frame — requires parent intervention")
        Services.communicator().mailbox_send(
            planspace, parent,
            f"fail:invalid_alignment_frame:{sec_num}",
        )
        section_results[sec_num] = SectionResult(
            section_number=sec_num,
            aligned=False,
            problems="invalid alignment frame — requires "
                     "parent intervention",
        )
        return False
    if align_result is None:
        Services.logger().log(f"  coordinator: section {sec_num} alignment check "
            f"timed out after retries")
        section_results[sec_num] = SectionResult(
            section_number=sec_num,
            aligned=False,
            problems="alignment check timed out after retries",
        )
        return False

    coord_align_output = paths.coordination_align_output(sec_num)
    align_problems = Services.section_alignment().extract_problems(
        align_result, output_path=coord_align_output,
        planspace=planspace, parent=parent, codespace=codespace,
        adjudicator_model=Services.policies().resolve(policy, "adjudicator"),
    )
    coord_signal_dir = paths.coordination_signals_dir()
    coord_signal_dir.mkdir(parents=True, exist_ok=True)
    signal, detail = Services.dispatch_helpers().check_agent_signals(
        align_result,
        signal_path=paths.coordination_align_signal(sec_num),
        output_path=paths.coordination_align_output(sec_num),
        planspace=planspace, parent=parent, codespace=codespace,
    )

    return _classify_alignment_result(
        sec_num, align_problems, signal, detail,
        section_results, problems, recurrence, coord_dir, planspace,
    )


def _compose_recurrence_text(
    sec_num: str,
    description: str,
    escalation_model: str,
    files: list[str],
) -> str:
    """Return the full prose text for a recurrence resolution artifact."""
    files_block = "\n".join(f"- `{f}`" for f in files)
    return (
        f"# Resolution: Section {sec_num}\n\n"
        f"## Recurring Problem\n\n"
        f"{description}\n\n"
        f"## Resolution\n\n"
        f"Resolved during coordination round via "
        f"coordinated fix with escalated model "
        f"({escalation_model}). Section is now ALIGNED.\n\n"
        f"## Files Involved\n\n"
        f"{files_block}\n"
    )


def _record_recurrence_resolution(
    sec_num: str,
    problems: list[dict],
    recurrence: dict | None,
    coord_dir: Path,
    planspace: Path,
) -> None:
    """Write resolution artifact if this section had a recurring problem."""
    if not recurrence:
        return
    if sec_num not in [str(s) for s in recurrence.get("recurring_sections", [])]:
        return
    prev_problem = next(
        (p for p in problems if p["section"] == sec_num),
        None,
    )
    if not prev_problem:
        return
    policy = Services.policies().load(planspace)
    resolution_path = coord_dir / f"resolution-{sec_num}.md"
    resolution_path.write_text(
        _compose_recurrence_text(
            sec_num,
            prev_problem.get('description', 'unknown'),
            Services.policies().resolve(policy, 'escalation_model'),
            prev_problem.get("files", []),
        ),
        encoding="utf-8",
    )
    Services.logger().log(f"  coordinator: recorded resolution for "
        f"recurring section {sec_num}")


def _recheck_affected_sections(
    affected_sections: list[str],
    all_modified: set[str],
    sections_by_num: dict[str, Section],
    section_results: dict[str, SectionResult],
    problems: list[dict],
    recurrence: dict | None,
    planspace: Path,
    codespace: Path,
    parent: str,
    paths: PathRegistry,
) -> bool | None:
    """Re-check alignment for all affected sections.

    Returns ``True`` if all aligned, ``False`` if some remain,
    or ``None`` if alignment changed (caller should return False).
    """
    coord_dir = paths.coordination_dir()
    inputs_hash_dir = coord_dir / "inputs-hashes"
    inputs_hash_dir.mkdir(parents=True, exist_ok=True)

    Services.logger().log(f"  coordinator: re-checking alignment for sections "
        f"{affected_sections}")

    for sec_num in affected_sections:
        section = sections_by_num.get(sec_num)
        if not section:
            continue

        current_hash = Services.pipeline_control().coordination_recheck_hash(
            sec_num, planspace, codespace, sections_by_num,
            list(all_modified),
        )
        prev_hash_file = inputs_hash_dir / f"section-{sec_num}.hash"
        if prev_hash_file.exists():
            prev_hash = prev_hash_file.read_text(encoding="utf-8").strip()
            if prev_hash == current_hash:
                Services.logger().log(f"  coordinator: section {sec_num} inputs unchanged "
                    f"— skipping alignment recheck")
                continue
        prev_hash_file.write_text(current_hash, encoding="utf-8")

        ctrl = Services.pipeline_control().poll_control_messages(planspace, parent, sec_num)
        if ctrl == ControlSignal.ALIGNMENT_CHANGED:
            Services.logger().log("  coordinator: alignment changed — aborting re-checks")
            return None

        result = _recheck_section_alignment(
            sec_num, section, section_results, problems, recurrence,
            planspace, codespace, parent, paths,
        )
        if result is None:
            return None

    # Check if everything is now aligned
    remaining = [r for r in section_results.values() if not r.aligned]
    if not remaining:
        outstanding_after = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        if outstanding_after:
            outstanding_types = [p["type"] for p in outstanding_after]
            Services.logger().log(f"  coordinator: all sections aligned but "
                f"{len(outstanding_after)} outstanding problems "
                f"remain (types: {outstanding_types})")
            return False
        Services.logger().log("  coordinator: all sections now ALIGNED")
        return True

    Services.logger().log(f"  coordinator: {len(remaining)} sections still not aligned")
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_global_coordination(
    sections: list[Section],
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> bool:
    """Run the global problem coordinator.

    Collects outstanding problems across all sections, groups related
    problems, dispatches coordinated fixes, and re-runs alignment on
    affected sections.

    Returns True if all sections are ALIGNED (or no problems remain).
    """
    paths = PathRegistry(planspace)
    coord_dir = paths.coordination_dir()
    coord_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: Collect problems + detect recurrence
    collected = _collect_and_persist_problems(
        section_results, sections_by_num, planspace, paths,
    )
    if collected is None:
        return True
    problems, recurrence = collected

    # Phase 1b: Aggregate scope deltas
    try:
        aggregate_scope_deltas(planspace, parent)
    except ScopeDeltaAggregationExit:
        return False

    # Phase 2: Build coordination plan via planner agent
    plan_result = _build_coordination_plan(
        problems, planspace, parent, paths,
    )
    if plan_result is None:
        return False
    confirmed_groups, _group_strategies, coord_plan = plan_result

    # Phase 3: Execute the coordination plan
    # Bridge directive type-safety: execute_coordination_plan defensively
    # checks isinstance(bridge_directive, dict) before reading bridge fields.
    exec_result = _execute_plan(
        coord_plan, confirmed_groups, sections_by_num,
        planspace, codespace, parent,
    )
    if exec_result is None:
        return False
    affected_sections, all_modified = exec_result

    # Phase 4: Re-check alignment on affected sections
    recheck = _recheck_affected_sections(
        affected_sections, all_modified, sections_by_num,
        section_results, problems, recurrence,
        planspace, codespace, parent, paths,
    )
    if recheck is None:
        return False
    return recheck
