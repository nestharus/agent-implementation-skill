from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from coordination.problem_types import Problem
from coordination.types import BridgeDirective, CoordinationStrategy, ProblemGroup, RecurrenceReport
from pipeline.context import DispatchContext
from coordination.engine.plan_executor import (
    CoordinationExecutionExit,
    PlanExecutor,
)
from coordination.service.planner import Planner
from coordination.service.problem_resolver import ProblemResolver
from orchestrator.path_registry import PathRegistry
from implementation.service.scope_delta_aggregator import (
    ScopeDeltaAggregationExit,
    ScopeDeltaAggregator,
)


from coordination.service.completion_handler import CompletionHandler
from orchestrator.types import Section, SectionResult, ControlSignal
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import ALIGNMENT_INVALID_FRAME

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        Communicator,
        DispatchHelperService,
        LogService,
        ModelPolicyService,
        PipelineControlService,
        SectionAlignmentService,
        TaskRouterService,
    )


# Stall detector warm-up: always try at least this many rounds before
# allowing the stall detector to terminate the loop.
MIN_COORDINATION_ROUNDS = 2


class GlobalCoordinator:
    """Global coordination engine across sections."""

    def __init__(
        self,
        *,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        completion_handler: CompletionHandler,
        dispatch_helpers: DispatchHelperService,
        dispatcher: AgentDispatcher,
        logger: LogService,
        plan_executor: PlanExecutor,
        planner: Planner,
        pipeline_control: PipelineControlService,
        policies: ModelPolicyService,
        problem_resolver: ProblemResolver,
        scope_delta_aggregator: ScopeDeltaAggregator,
        section_alignment: SectionAlignmentService,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._completion_handler = completion_handler
        self._dispatch_helpers = dispatch_helpers
        self._dispatcher = dispatcher
        self._logger = logger
        self._plan_executor = plan_executor
        self._planner = planner
        self._pipeline_control = pipeline_control
        self._policies = policies
        self._problem_resolver = problem_resolver
        self._scope_delta_aggregator = scope_delta_aggregator
        self._section_alignment = section_alignment
        self._task_router = task_router

    # ---------------------------------------------------------------------------
    # Phase 1: Collect problems + escalate recurring patterns
    # ---------------------------------------------------------------------------

    def _collect_and_persist_problems(
        self,
        section_results: dict[str, SectionResult],
        sections_by_num: dict[str, Section],
        planspace: Path,
    ) -> tuple[list[Problem], RecurrenceReport | None] | None:
        """Collect outstanding problems, detect recurrence, persist state.

        Returns ``(problems, recurrence)`` or ``None`` if no problems exist.
        """
        problems = self._problem_resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )

        if not problems:
            self._logger.log("  coordinator: no outstanding problems — all ALIGNED")
            return None

        self._logger.log(f"  coordinator: {len(problems)} outstanding problems across "
            f"{len({p.section for p in problems})} sections")

        paths = PathRegistry(planspace)
        policy = self._policies.load(planspace)
        recurrence = self._problem_resolver.detect_recurrence_patterns(planspace, problems)
        if recurrence:
            escalation_file = paths.coordination_model_escalation()
            escalation_file.write_text(
                self._policies.resolve(policy, "escalation_model"), encoding="utf-8")
            self._logger.log(f"  coordinator: recurrence escalation — setting model to "
                f"{self._policies.resolve(policy, 'escalation_model')} for "
                f"{recurrence.recurring_problem_count} recurring problems "
                f"across sections {recurrence.recurring_sections}")

        state_path = paths.coordination_problems()
        self._artifact_io.write_json(state_path, problems)
        self._communicator.log_artifact(planspace,"coordination:problems")

        return problems, recurrence

    # ---------------------------------------------------------------------------
    # Phase 2: Build coordination plan via planner agent
    # ---------------------------------------------------------------------------

    def _dispatch_and_parse_plan(
        self,
        problems: list[Problem],
        planspace: Path,
        policy: dict,
    ) -> dict | None:
        """Dispatch planner agent with retry, return parsed plan or None."""
        coord_dir = PathRegistry(planspace).coordination_dir()
        plan_prompt = self._planner.write_coordination_plan_prompt(problems, planspace)
        plan_output = coord_dir / "coordination-plan-output.md"
        self._logger.log("  coordinator: dispatching coordination-planner agent")
        plan_result = self._dispatcher.dispatch(
            self._policies.resolve(policy, "coordination_plan"), plan_prompt, plan_output,
            planspace, agent_file=self._task_router.agent_for("coordination.plan"),
        )
        if plan_result == ALIGNMENT_CHANGED_PENDING:
            return None

        coord_plan = self._planner._parse_coordination_plan(plan_result.output, problems)
        if coord_plan is None:
            self._logger.log("  coordinator: plan parse failed — retrying with "
                "escalation model")
            plan_output_retry = coord_dir / "coordination-plan-output-retry.md"
            retry_result = self._dispatcher.dispatch(
                self._policies.resolve(policy, "escalation_model"), plan_prompt, plan_output_retry,
                planspace, agent_file=self._task_router.agent_for("coordination.plan"),
            )
            if retry_result == ALIGNMENT_CHANGED_PENDING:
                return None
            coord_plan = self._planner._parse_coordination_plan(retry_result.output, problems)

        if coord_plan is None:
            self._logger.log("  coordinator: plan parse failed after retry — fail closed")
            failure_path = coord_dir / "coordination-plan-failure.md"
            self._artifact_io.write_json(failure_path, {
                "reason": "unparseable_plan_json",
                "attempts": 2,
            })
            self._communicator.send_to_parent(
                planspace, "fail:coordination:unparseable_plan_json",
            )
        return coord_plan

    def _build_coordination_plan(
        self,
        problems: list[Problem],
        planspace: Path,
    ) -> tuple[list[ProblemGroup], list[list[int]] | None] | None:
        """Dispatch planner agent, parse plan, build problem groups.

        Returns ``(groups, agent_batches)`` or ``None`` on failure
        (alignment changed, parse failures).
        """
        policy = self._policies.load(planspace)

        ctrl = self._pipeline_control.poll_control_messages(planspace)
        if ctrl == ControlSignal.ALIGNMENT_CHANGED:
            return None

        coord_dir = PathRegistry(planspace).coordination_dir()
        coord_plan = self._dispatch_and_parse_plan(
            problems, planspace, policy,
        )
        if coord_plan is None:
            return None

        groups: list[ProblemGroup] = []
        for g in coord_plan["groups"]:
            group_problems = [problems[i] for i in g["problems"]]
            bridge_dict = g.get("bridge", {})
            if not isinstance(bridge_dict, dict):
                bridge_dict = {}
            group = ProblemGroup(
                problems=group_problems,
                strategy=g.get("strategy", CoordinationStrategy.SEQUENTIAL),
                reason=g.get("reason", ""),
                bridge=BridgeDirective(
                    needed=bridge_dict.get("needed", False),
                    reason=bridge_dict.get("reason", ""),
                ),
            )
            groups.append(group)
            self._logger.log(f"  coordinator: group {len(groups) - 1} — "
                f"{len(group_problems)} problems, "
                f"strategy={group.strategy}, "
                f"reason={group.reason or '(none)'}")

        self._logger.log(f"  coordinator: {len(groups)} problem groups from "
            f"coordination plan")

        # Save plan and groups for debugging
        plan_path = coord_dir / "coordination-plan.json"
        self._artifact_io.write_json(plan_path, coord_plan)
        self._communicator.log_artifact(planspace,"coordination:plan")

        groups_path = coord_dir / "groups.json"
        groups_data = []
        for i, group in enumerate(groups):
            groups_data.append({
                "group_id": i,
                "problem_count": len(group.problems),
                "strategy": str(group.strategy),
                "sections": sorted({p.section for p in group.problems}),
                "files": sorted({f for p in group.problems for f in p.files}),
            })
        self._artifact_io.write_json(groups_path, groups_data)
        self._communicator.log_artifact(planspace,"coordination:groups")

        agent_batches = coord_plan.get("batches")
        return groups, agent_batches

    # ---------------------------------------------------------------------------
    # Phase 3: Execute plan + collect modified files
    # ---------------------------------------------------------------------------

    def _execute_plan(
        self,
        groups: list[ProblemGroup],
        sections_by_num: dict[str, Section],
        ctx: DispatchContext,
        agent_batches: list[list[int]] | None = None,
    ) -> tuple[list[str], set[str]] | None:
        """Execute coordination plan. Returns (affected_sections, all_modified) or None."""
        try:
            affected_sections = self._plan_executor.execute_coordination_plan(
                groups, sections_by_num, ctx,
                agent_batches=agent_batches,
            )
        except CoordinationExecutionExit:
            return None
        all_modified = self._plan_executor.read_execution_modified_files(ctx.planspace)
        return affected_sections, all_modified

    # ---------------------------------------------------------------------------
    # Phase 4: Re-check alignment on affected sections
    # ---------------------------------------------------------------------------

    def _classify_alignment_result(
        self,
        sec_num: str,
        align_problems: str | None,
        signal: str | None,
        detail: str | None,
        section_results: dict[str, SectionResult],
        problems: list[Problem],
        recurrence: RecurrenceReport | None,
        planspace: Path,
    ) -> bool:
        """Classify alignment check outcome and record result.

        Returns ``True`` if aligned, ``False`` if still has problems.
        """
        if align_problems is None and signal is None:
            self._logger.log(f"  coordinator: section {sec_num} now ALIGNED")
            section_results[sec_num] = SectionResult(
                section_number=sec_num, aligned=True,
            )
            self._record_recurrence_resolution(
                sec_num, problems, recurrence, planspace,
            )
            return True

        self._logger.log(f"  coordinator: section {sec_num} still has problems")
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
        self,
        section: Section,
        section_results: dict[str, SectionResult],
        problems: list[Problem],
        recurrence: RecurrenceReport | None,
        ctx: DispatchContext,
    ) -> bool | None:
        """Re-run alignment check on one section after coordination fixes.

        Returns ``True`` if aligned, ``False`` if still has problems,
        or ``None`` if alignment changed (caller should abort).
        """
        sec_num = section.number

        notes = self._completion_handler.read_incoming_notes(section, ctx.planspace, ctx.codespace)
        if notes:
            self._logger.log(f"  coordinator: section {sec_num} has incoming notes "
                f"from other sections")

        align_result = self._section_alignment.run_alignment_check(
            section, ctx.planspace, ctx.codespace,
            output_prefix="coord-align",
            model=ctx.resolve_model("alignment"),
        )
        if align_result == ALIGNMENT_CHANGED_PENDING:
            return None
        if align_result == ALIGNMENT_INVALID_FRAME:
            self._logger.log(f"  coordinator: section {sec_num} invalid alignment "
                f"frame — requires parent intervention")
            self._communicator.send_to_parent(
                ctx.planspace,
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
            self._logger.log(f"  coordinator: section {sec_num} alignment check "
                f"timed out after retries")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems="alignment check timed out after retries",
            )
            return False

        coord_align_output = ctx.paths.coordination_align_output(sec_num)
        align_problems = self._section_alignment.extract_problems(
            align_result, output_path=coord_align_output,
            planspace=ctx.planspace, codespace=ctx.codespace,
            adjudicator_model=ctx.resolve_model("adjudicator"),
        )
        signal, detail = self._dispatch_helpers.check_agent_signals(
            signal_path=ctx.paths.coordination_align_signal(sec_num),
        )

        return self._classify_alignment_result(
            sec_num, align_problems, signal, detail,
            section_results, problems, recurrence, ctx.planspace,
        )

    def _record_recurrence_resolution(
        self,
        sec_num: str,
        problems: list[Problem],
        recurrence: RecurrenceReport | None,
        planspace: Path,
    ) -> None:
        """Write resolution artifact if this section had a recurring problem."""
        if not recurrence:
            return
        if sec_num not in recurrence.recurring_sections:
            return
        prev_problem = next(
            (p for p in problems if p.section == sec_num),
            None,
        )
        if not prev_problem:
            return
        policy = self._policies.load(planspace)
        coord_dir = PathRegistry(planspace).coordination_dir()
        resolution_path = coord_dir / f"resolution-{sec_num}.md"
        resolution_path.write_text(
            _compose_recurrence_text(
                sec_num,
                prev_problem.description,
                self._policies.resolve(policy, 'escalation_model'),
                prev_problem.files,
            ),
            encoding="utf-8",
        )
        self._logger.log(f"  coordinator: recorded resolution for "
            f"recurring section {sec_num}")

    def _recheck_affected_sections(
        self,
        affected_sections: list[str],
        all_modified: set[str],
        sections_by_num: dict[str, Section],
        section_results: dict[str, SectionResult],
        problems: list[Problem],
        recurrence: RecurrenceReport | None,
        ctx: DispatchContext,
    ) -> bool | None:
        """Re-check alignment for all affected sections.

        Returns ``True`` if all aligned, ``False`` if some remain,
        or ``None`` if alignment changed (caller should return False).
        """
        coord_dir = ctx.paths.coordination_dir()
        inputs_hash_dir = coord_dir / "inputs-hashes"
        inputs_hash_dir.mkdir(parents=True, exist_ok=True)

        self._logger.log(f"  coordinator: re-checking alignment for sections "
            f"{affected_sections}")

        for sec_num in affected_sections:
            section = sections_by_num.get(sec_num)
            if not section:
                continue

            current_hash = self._pipeline_control.coordination_recheck_hash(
                sec_num, ctx.planspace, ctx.codespace, sections_by_num,
                list(all_modified),
            )
            prev_hash_file = inputs_hash_dir / f"section-{sec_num}.hash"
            if prev_hash_file.exists():
                prev_hash = prev_hash_file.read_text(encoding="utf-8").strip()
                if prev_hash == current_hash:
                    self._logger.log(f"  coordinator: section {sec_num} inputs unchanged "
                        f"— skipping alignment recheck")
                    continue
            prev_hash_file.write_text(current_hash, encoding="utf-8")

            ctrl = self._pipeline_control.poll_control_messages(
                ctx.planspace, sec_num,
            )
            if ctrl == ControlSignal.ALIGNMENT_CHANGED:
                self._logger.log("  coordinator: alignment changed — aborting re-checks")
                return None

            result = self._recheck_section_alignment(
                section, section_results, problems, recurrence, ctx,
            )
            if result is None:
                return None

        # Check if everything is now aligned
        remaining = [r for r in section_results.values() if not r.aligned]
        if not remaining:
            outstanding_after = self._problem_resolver.collect_outstanding_problems(
                section_results, sections_by_num, ctx.planspace,
            )
            if outstanding_after:
                outstanding_types = [p.type for p in outstanding_after]
                self._logger.log(f"  coordinator: all sections aligned but "
                    f"{len(outstanding_after)} outstanding problems "
                    f"remain (types: {outstanding_types})")
                return False
            self._logger.log("  coordinator: all sections now ALIGNED")
            return True

        self._logger.log(f"  coordinator: {len(remaining)} sections still not aligned")
        return False

    # ---------------------------------------------------------------------------
    # Entry point
    # ---------------------------------------------------------------------------

    def run_global_coordination(
        self,
        section_results: dict[str, SectionResult],
        sections_by_num: dict[str, Section],
        ctx: DispatchContext,
    ) -> bool:
        """Run the global problem coordinator.

        Collects outstanding problems across all sections, groups related
        problems, dispatches coordinated fixes, and re-runs alignment on
        affected sections.

        Returns True if all sections are ALIGNED (or no problems remain).
        """
        # Phase 1: Collect problems + detect recurrence
        collected = self._collect_and_persist_problems(
            section_results, sections_by_num, ctx.planspace,
        )
        if collected is None:
            return True
        problems, recurrence = collected

        # Phase 1b: Aggregate scope deltas
        try:
            self._scope_delta_aggregator.aggregate_scope_deltas(ctx.planspace)
        except ScopeDeltaAggregationExit:
            return False

        # Phase 2: Build coordination plan via planner agent
        plan_result = self._build_coordination_plan(
            problems, ctx.planspace,
        )
        if plan_result is None:
            return False
        groups, agent_batches = plan_result

        # Phase 3: Execute the coordination plan
        exec_result = self._execute_plan(
            groups, sections_by_num, ctx,
            agent_batches=agent_batches,
        )
        if exec_result is None:
            return False
        affected_sections, all_modified = exec_result

        # Phase 4: Re-check alignment on affected sections
        recheck = self._recheck_affected_sections(
            affected_sections, all_modified, sections_by_num,
            section_results, problems, recurrence, ctx,
        )
        if recheck is None:
            return False
        return recheck


# ---------------------------------------------------------------------------
# Pure helpers (no Services usage)
# ---------------------------------------------------------------------------

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

