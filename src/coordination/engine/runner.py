from pathlib import Path
from typing import Any

from signals.repository.artifact_io import write_json
from dispatch.service.model_policy import resolve
from coordination.engine.executor import (
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

from staleness.service.section_alignment import (
    _extract_problems,
    _parse_alignment_verdict,
    _run_alignment_check_with_retries,
)
from signals.service.communication import (
    _log_artifact,
    log,
    mailbox_send,
)
from coordination.service.cross_section import read_incoming_notes
from dispatch.engine.section_dispatch import (
    check_agent_signals,
    dispatch_agent,
    read_model_policy,
)
from orchestrator.service.pipeline_control import coordination_recheck_hash, poll_control_messages
from orchestrator.types import Section, SectionResult


def _normalize_section_id(value: str, scope_deltas_dir: Path) -> str:
    """Backward-compatible private alias used by older tests."""
    from implementation.service.scope_delta_parser import normalize_section_id

    return normalize_section_id(value, scope_deltas_dir)


# Coordination round limits: hard cap to prevent runaway, but rounds
# continue adaptively while problem count decreases.
MAX_COORDINATION_ROUNDS = 10  # hard safety cap
MIN_COORDINATION_ROUNDS = 2   # always try at least this many


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
    policy = read_model_policy(planspace)

    # -----------------------------------------------------------------
    # Step 1: Collect all outstanding problems
    # -----------------------------------------------------------------
    problems = _collect_outstanding_problems(
        section_results, sections_by_num, planspace,
    )

    if not problems:
        log("  coordinator: no outstanding problems — all ALIGNED")
        return True

    log(f"  coordinator: {len(problems)} outstanding problems across "
        f"{len({p['section'] for p in problems})} sections")

    # Detect recurrence patterns for escalated handling
    recurrence = _detect_recurrence_patterns(planspace, problems)

    # If recurrence detected, escalate model for affected groups
    if recurrence:
        escalation_file = coord_dir / "model-escalation.txt"
        escalation_file.write_text(
            resolve(policy, "escalation_model"), encoding="utf-8")
        log(f"  coordinator: recurrence escalation — setting model to "
            f"{resolve(policy, 'escalation_model')} for "
            f"{recurrence['recurring_problem_count']} recurring problems "
            f"across sections {recurrence['recurring_sections']}")

    # Save coordination state for debugging / inspection
    state_path = coord_dir / "problems.json"
    write_json(state_path, problems)
    _log_artifact(planspace, "coordination:problems")

    # -----------------------------------------------------------------
    # Step 1b: Aggregate scope deltas for coordinator adjudication
    # -----------------------------------------------------------------
    try:
        aggregate_scope_deltas(planspace, parent, policy)
    except ScopeDeltaAggregationExit:
        return False

    # -----------------------------------------------------------------
    # Step 2: Dispatch coordination-planner agent to group problems
    # -----------------------------------------------------------------
    ctrl = poll_control_messages(planspace, parent)
    if ctrl == "alignment_changed":
        return False

    plan_prompt = write_coordination_plan_prompt(problems, planspace)
    plan_output = coord_dir / "coordination-plan-output.md"
    log("  coordinator: dispatching coordination-planner agent")
    plan_result = dispatch_agent(
        resolve(policy, "coordination_plan"), plan_prompt, plan_output,
        planspace, parent, agent_file="coordination-planner.md",
    )
    if plan_result == "ALIGNMENT_CHANGED_PENDING":
        return False

    # Parse the JSON coordination plan from agent output
    coord_plan = _parse_coordination_plan(plan_result, problems)
    if coord_plan is None:
        # Retry once with escalation model — scripts must not decide
        # problem grouping (that is a strategic agent decision).
        log("  coordinator: plan parse failed — retrying with "
            "escalation model")
        plan_output_retry = coord_dir / "coordination-plan-output-retry.md"
        retry_result = dispatch_agent(
            resolve(policy, "escalation_model"), plan_prompt, plan_output_retry,
            planspace, parent, agent_file="coordination-planner.md",
        )
        if retry_result == "ALIGNMENT_CHANGED_PENDING":
            return False
        coord_plan = _parse_coordination_plan(retry_result, problems)

    if coord_plan is None:
        # Fail closed: write failure artifact + mailbox, return False.
        # Scripts must not invent grouping — only the agent decides.
        log("  coordinator: plan parse failed after retry — fail closed")
        failure_path = coord_dir / "coordination-plan-failure.md"
        write_json(failure_path, {
            "reason": "unparseable_plan_json",
            "attempts": 2,
        })
        mailbox_send(
            planspace, parent,
            "fail:coordination:unparseable_plan_json",
        )
        return False

    # Build confirmed groups from the plan
    confirmed_groups: list[list[dict[str, Any]]] = []
    group_strategies: list[str] = []
    for g in coord_plan["groups"]:
        group_problems = [problems[i] for i in g["problems"]]
        confirmed_groups.append(group_problems)
        group_strategies.append(g.get("strategy", "sequential"))
        log(f"  coordinator: group {len(confirmed_groups) - 1} — "
            f"{len(group_problems)} problems, "
            f"strategy={group_strategies[-1]}, "
            f"reason={g.get('reason', '(none)')}")

    log(f"  coordinator: {len(confirmed_groups)} problem groups from "
        f"coordination plan")

    # Save plan and groups for debugging
    plan_path = coord_dir / "coordination-plan.json"
    write_json(plan_path, coord_plan)
    _log_artifact(planspace, "coordination:plan")

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
    write_json(groups_path, groups_data)
    _log_artifact(planspace, "coordination:groups")

    # -----------------------------------------------------------------
    # Step 3: Execute the coordination plan
    # -----------------------------------------------------------------
    # Bridge directive type-safety still applies after extraction:
    # execute_coordination_plan defensively checks
    # isinstance(bridge_directive, dict) before reading bridge fields.
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
            policy,
        )
    except CoordinationExecutionExit:
        return False
    all_modified = read_execution_modified_files(planspace)

    # -----------------------------------------------------------------
    # Step 4: Re-run per-section alignment on affected sections
    # -----------------------------------------------------------------
    log(f"  coordinator: re-checking alignment for sections "
        f"{affected_sections}")

    # Incremental alignment: track per-section input hashes to skip
    # unchanged sections
    inputs_hash_dir = coord_dir / "inputs-hashes"
    inputs_hash_dir.mkdir(parents=True, exist_ok=True)

    for sec_num in affected_sections:
        section = sections_by_num.get(sec_num)
        if not section:
            continue

        # Canonical section-input hash + coordinator-modified files
        current_hash = coordination_recheck_hash(
            sec_num, planspace, codespace, sections_by_num,
            list(all_modified),
        )

        prev_hash_file = inputs_hash_dir / f"section-{sec_num}.hash"
        if prev_hash_file.exists():
            prev_hash = prev_hash_file.read_text(encoding="utf-8").strip()
            if prev_hash == current_hash:
                log(f"  coordinator: section {sec_num} inputs unchanged "
                    f"— skipping alignment recheck")
                continue
        prev_hash_file.write_text(current_hash, encoding="utf-8")

        # Poll for control messages before each re-check
        ctrl = poll_control_messages(planspace, parent, sec_num)
        if ctrl == "alignment_changed":
            log("  coordinator: alignment changed — aborting re-checks")
            return False

        # Read any incoming notes for this section (cross-section context)
        notes = read_incoming_notes(section, planspace, codespace)
        if notes:
            log(f"  coordinator: section {sec_num} has incoming notes "
                f"from other sections")

        # Re-run implementation alignment check with TIMEOUT retry
        align_result = _run_alignment_check_with_retries(
            section, planspace, codespace, parent, sec_num,
            output_prefix="coord-align",
            model=resolve(policy, "alignment"),
            adjudicator_model=resolve(policy, "adjudicator"),
        )
        if align_result == "ALIGNMENT_CHANGED_PENDING":
            return False  # Let outer loop restart Phase 1
        if align_result == "INVALID_FRAME":
            # Structural failure — alignment prompt frame is wrong.
            # Surface upward, don't continue with broken evaluation.
            log(f"  coordinator: section {sec_num} invalid alignment "
                f"frame — requires parent intervention")
            mailbox_send(
                planspace, parent,
                f"fail:invalid_alignment_frame:{sec_num}",
            )
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems="invalid alignment frame — requires "
                         "parent intervention",
            )
            continue
        if align_result is None:
            # All retries timed out
            log(f"  coordinator: section {sec_num} alignment check "
                f"timed out after retries")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems="alignment check timed out after retries",
            )
            continue

        coord_align_output = (
            paths.artifacts / f"coord-align-{sec_num}-output.md"
        )
        align_problems = _extract_problems(
            align_result, output_path=coord_align_output,
            planspace=planspace, parent=parent, codespace=codespace,
            adjudicator_model=resolve(policy, "adjudicator"),
        )
        coord_signal_dir = coord_dir / "signals"
        coord_signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            align_result,
            signal_path=(coord_signal_dir
                         / f"coord-align-{sec_num}-signal.json"),
            output_path=coord_dir / f"coord-align-{sec_num}-output.md",
            planspace=planspace, parent=parent, codespace=codespace,
        )

        if align_problems is None and signal is None:
            log(f"  coordinator: section {sec_num} now ALIGNED")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=True,
            )

            # Record resolution if this section had a recurring problem
            if recurrence and sec_num in [
                str(s) for s in recurrence.get("recurring_sections", [])
            ]:
                # Find what the previous problem was
                prev_problem = next(
                    (p for p in problems if p["section"] == sec_num),
                    None,
                )
                if prev_problem:
                    resolution_dir = coord_dir
                    resolution_dir.mkdir(parents=True, exist_ok=True)
                    resolution_path = (
                        resolution_dir
                        / f"resolution-{sec_num}.md"
                    )
                    resolution_path.write_text(
                        f"# Resolution: Section {sec_num}\n\n"
                        f"## Recurring Problem\n\n"
                        f"{prev_problem.get('description', 'unknown')}\n\n"
                        f"## Resolution\n\n"
                        f"Resolved during coordination round via "
                        f"coordinated fix with escalated model "
                        f"({resolve(policy, 'escalation_model')}). Section is now ALIGNED.\n\n"
                        f"## Files Involved\n\n"
                        + "\n".join(
                            f"- `{f}`"
                            for f in prev_problem.get("files", [])
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    log(f"  coordinator: recorded resolution for "
                        f"recurring section {sec_num}")
        else:
            log(f"  coordinator: section {sec_num} still has problems")
            # Fold signal info into problems string (SectionResult has
            # no signal fields — only problems)
            combined_problems = align_problems or ""
            if signal:
                combined_problems += (
                    f"\n[signal:{signal}] {detail}" if combined_problems
                    else f"[signal:{signal}] {detail}"
                )
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems=combined_problems or None,
            )

    # Check if everything is now aligned
    remaining = [r for r in section_results.values() if not r.aligned]
    if not remaining:
        # Re-check outstanding problems (notes may have been generated
        # during coordination fixes).
        outstanding_after = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        if outstanding_after:
            outstanding_types = [p["type"] for p in outstanding_after]
            log(f"  coordinator: all sections aligned but "
                f"{len(outstanding_after)} outstanding problems "
                f"remain (types: {outstanding_types})")
            return False
        log("  coordinator: all sections now ALIGNED")
        return True

    log(f"  coordinator: {len(remaining)} sections still not aligned")
    return False
