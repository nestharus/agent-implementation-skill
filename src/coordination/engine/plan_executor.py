"""Coordination-plan execution helpers."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from coordination.problem_types import Problem
from coordination.types import CoordinationStrategy, ProblemGroup
from orchestrator.path_registry import PathRegistry
from orchestrator.types import PauseType
from pipeline.context import DispatchContext
from coordination.prompt.writers import Writers
from orchestrator.types import Section, ControlSignal
from dispatch.types import ALIGNMENT_CHANGED_PENDING

_MAX_PARALLEL_FIX_WORKERS = 4
from signals.types import SIGNAL_NEEDS_PARENT

_NOTE_FINGERPRINT_LENGTH = 12

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        Communicator,
        DispatchHelperService,
        FlowIngestionService,
        HasherService,
        LogService,
        ModelPolicyService,
        PipelineControlService,
        TaskRouterService,
    )


class CoordinationExecutionExit(Exception):
    """Raised when coordination execution must stop early."""


class PlanExecutor:
    """Coordination-plan execution engine."""

    def __init__(
        self,
        *,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        dispatch_helpers: DispatchHelperService,
        dispatcher: AgentDispatcher,
        flow_ingestion: FlowIngestionService,
        hasher: HasherService,
        logger: LogService,
        pipeline_control: PipelineControlService,
        task_router: TaskRouterService,
        writers: Writers,
        halt_event: threading.Event | None = None,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._dispatch_helpers = dispatch_helpers
        self._dispatcher = dispatcher
        self._flow_ingestion = flow_ingestion
        self._hasher = hasher
        self._logger = logger
        self._pipeline_control = pipeline_control
        self._task_router = task_router
        self._writers = writers
        self._halt_event = halt_event

    def _build_execution_batches(
        self,
        groups: list[ProblemGroup],
        agent_batches: list[list[int]] | None = None,
    ) -> list[list[int]]:
        group_file_sets = [
            set(file_path for problem in group.problems for file_path in problem.files)
            for group in groups
        ]

        if agent_batches is not None:
            batches: list[list[int]] = []
            for agent_batch in agent_batches:
                allowed = set(agent_batch)
                for group_index in agent_batch:
                    _try_place_in_batch(
                        group_index, group_file_sets[group_index],
                        batches, group_file_sets, allowed_indices=allowed,
                    )
            self._logger.log(
                f"  coordinator: using agent-specified batch ordering "
                f"({len(agent_batches)} agent batches \u2192 "
                f"{len(batches)} execution batches with file-safety)",
            )
            return batches

        batches = []
        for group_index, files in enumerate(group_file_sets):
            _try_place_in_batch(group_index, files, batches, group_file_sets)
        return batches

    def _write_overlap_stats(
        self,
        coord_dir: Path,
        group_index: int,
        group: list[Problem],
    ) -> None:
        group_section_nums = sorted({problem.section for problem in group})
        if len(group_section_nums) < 2:
            return

        section_file_sets: dict[str, set[str]] = {}
        for problem in group:
            section_file_sets.setdefault(problem.section, set()).update(
                problem.files,
            )

        section_numbers = list(section_file_sets.keys())
        overlap_count = 0
        for idx in range(len(section_numbers)):
            for jdx in range(idx + 1, len(section_numbers)):
                overlap_count += len(
                    section_file_sets[section_numbers[idx]]
                    & section_file_sets[section_numbers[jdx]],
                )

        if overlap_count <= 0:
            return

        self._logger.log(
            f"  coordinator: group {group_index} has "
            f"{overlap_count} overlapping files across "
            f"sections \u2014 bridge decision deferred to "
            f"coordination planner",
        )
        overlap_signal = {
            "group": group_index,
            "sections": group_section_nums,
            "overlap_count": overlap_count,
            "overlapping_files": sorted(
                file_path
                for file_set in section_file_sets.values()
                for file_path in file_set
                if sum(1 for candidate in section_file_sets.values() if file_path in candidate) > 1
            ),
        }
        (coord_dir / f"overlap-stats-group-{group_index}.json").write_text(
            json.dumps(overlap_signal, indent=2),
            encoding="utf-8",
        )

    def _inject_bridge_note_ids(
        self,
        notes_dir: Path,
        group_index: int,
        group_sections: list[str],
        contract_delta_path: Path,
    ) -> None:
        delta_bytes = contract_delta_path.read_bytes()
        for section_num in group_sections:
            note_path = notes_dir / f"from-bridge-{group_index}-to-{section_num}.md"
            if not note_path.exists():
                continue
            note_text = note_path.read_text(encoding="utf-8")
            if "**Note ID**:" in note_text:
                continue
            fingerprint = self._hasher.content_hash(delta_bytes + section_num.encode("utf-8"))[:_NOTE_FINGERPRINT_LENGTH]
            note_path.write_text(
                f"**Note ID**: `bridge-{group_index}-to-{section_num}-{fingerprint}`\n\n"
                f"{note_text}",
                encoding="utf-8",
            )

    def _ensure_contract_delta(
        self,
        contract_delta_path: Path,
        bridge_model: str,
        bridge_prompt: Path,
        bridge_output: Path,
        ctx: DispatchContext,
        group_index: int,
        group_sections: list[str],
        bridge_reason: str,
    ) -> bool:
        """Retry bridge dispatch if contract delta missing. Returns True on success."""
        if contract_delta_path.exists():
            return True

        self._logger.log(
            f"  coordinator: bridge didn't write contract "
            f"delta \u2014 retrying (group {group_index})",
        )
        self._dispatcher.dispatch(
            bridge_model, bridge_prompt, bridge_output,
            ctx.planspace, codespace=ctx.codespace,
            agent_file=self._task_router.agent_for("coordination.bridge"),
        )
        if contract_delta_path.exists():
            return True

        self._logger.log(
            f"  coordinator: bridge failed to write contract "
            f"delta after retry \u2014 pausing for parent "
            f"(group {group_index})",
        )
        blocker_signal = {
            "state": SIGNAL_NEEDS_PARENT,
            "why_blocked": (
                f"Bridge agent for group {group_index} failed to "
                f"produce contract delta after retry. "
                f"Sections: {group_sections}. "
                f"Reason: {bridge_reason}"
            ),
        }
        blocker_path = ctx.paths.signals_dir() / f"blocker-bridge-{group_index}.json"
        blocker_path.parent.mkdir(parents=True, exist_ok=True)
        blocker_path.write_text(json.dumps(blocker_signal, indent=2), encoding="utf-8")
        self._communicator.mailbox_send(
            ctx.planspace,
            f"pause:{PauseType.NEEDS_PARENT}:bridge-{group_index}:contract delta missing after retry",
            "coordinator",
        )
        return False

    def _run_bridge_for_group(
        self,
        *,
        group_index: int,
        group: list[Problem],
        ctx: DispatchContext,
        bridge_reason: str,
    ) -> None:
        group_sections = sorted({problem.section for problem in group})
        contract_delta_path = ctx.paths.contracts_dir() / f"contract-delta-group-{group_index}.md"
        notes_dir = ctx.paths.notes_dir()
        bridge_output = ctx.paths.coordination_bridge_output(group_index)

        bridge_prompt = self._writers.write_bridge_prompt(
            group, group_index, group_sections,
            ctx.planspace, bridge_reason,
        )
        if bridge_prompt is None:
            return

        self._logger.log(
            f"  coordinator: dispatching bridge agent for group "
            f"{group_index} ({group_sections}) \u2014 reason: {bridge_reason}",
        )

        bridge_model = ctx.resolve_model("coordination_bridge")
        self._dispatcher.dispatch(
            bridge_model,
            bridge_prompt,
            bridge_output,
            ctx.planspace,
            codespace=ctx.codespace,
            agent_file=self._task_router.agent_for("coordination.bridge"),
        )

        if not self._ensure_contract_delta(
            contract_delta_path, bridge_model, bridge_prompt, bridge_output,
            ctx,
            group_index, group_sections, bridge_reason,
        ):
            return

        self._inject_bridge_note_ids(notes_dir, group_index, group_sections, contract_delta_path)
        for section_num in group_sections:
            input_ref_dir = ctx.paths.input_refs_dir(section_num)
            input_ref_dir.mkdir(parents=True, exist_ok=True)
            (input_ref_dir / f"contract-delta-group-{group_index}.ref").write_text(
                str(contract_delta_path),
                encoding="utf-8",
            )
        self._logger.log(
            f"  coordinator: bridge complete for group {group_index}, "
            f"contract delta at {contract_delta_path}",
        )

    def _dispatch_fix_group(
        self,
        group: list[Problem], group_id: int,
        ctx: DispatchContext,
        default_fix_model: str = "",
    ) -> tuple[int, list[str] | None]:
        """Dispatch an agent to fix a single problem group.

        Returns (group_id, list_of_modified_files) on success.
        Returns (group_id, None) if ALIGNMENT_CHANGED_PENDING sentinel received.
        """
        coord_dir = ctx.paths.coordination_dir()
        fix_prompt = self._writers.write_fix_prompt(group, ctx.planspace, ctx.codespace, group_id)
        if fix_prompt is None:
            self._logger.log(f"  coordinator: fix group {group_id} prompt blocked "
                f"by template safety \u2014 skipping dispatch")
            return group_id, None
        fix_output = coord_dir / f"fix-{group_id}-output.md"
        modified_report = ctx.paths.coordination_fix_modified(group_id)

        if not default_fix_model:
            default_fix_model = ctx.resolve_model("coordination_fix")
        fix_model = default_fix_model
        coord_escalated_from = None
        escalation_file = ctx.paths.coordination_model_escalation()
        if escalation_file.exists():
            coord_escalated_from = fix_model
            fix_model = escalation_file.read_text(encoding="utf-8").strip()
            self._logger.log(f"  coordinator: using escalated model {fix_model}")

        self._dispatch_helpers.write_model_choice_signal(
            ctx.planspace, f"coord-{group_id}", "coordination-fix",
            fix_model,
            "escalated due to coordination churn" if coord_escalated_from
            else "default model",
            coord_escalated_from,
        )

        self._logger.log(f"  coordinator: dispatching fix for group {group_id} "
            f"({len(group)} problems)")
        result = self._dispatcher.dispatch(
            fix_model, fix_prompt, fix_output,
            ctx.planspace, codespace=ctx.codespace,
            agent_file=self._task_router.agent_for("coordination.fix"),
        )
        if result == ALIGNMENT_CHANGED_PENDING:
            return group_id, None

        self._flow_ingestion.ingest_and_submit(
            ctx.planspace,
            submitted_by=f"coordination-fix-{group_id}",
            signal_path=ctx.paths.coordination_task_request(group_id),
            origin_refs=[str(fix_prompt)],
        )

        return group_id, self._collect_modified_files(modified_report, ctx.codespace)

    def _dispatch_scaffold_group(
        self,
        group: list[Problem], group_id: int,
        ctx: DispatchContext,
    ) -> tuple[int, list[str] | None]:
        """Dispatch the scaffolder agent to create stub files for a group.

        Returns (group_id, list_of_created_files) on success.
        Returns (group_id, None) if ALIGNMENT_CHANGED_PENDING sentinel received.
        """
        coord_dir = ctx.paths.coordination_dir()
        scaffold_prompt = self._writers.write_scaffold_prompt(
            group, ctx.planspace, ctx.codespace, group_id,
        )
        if scaffold_prompt is None:
            self._logger.log(f"  coordinator: scaffold group {group_id} prompt blocked "
                f"by template safety — skipping dispatch")
            return group_id, []
        scaffold_output = coord_dir / f"scaffold-{group_id}-output.md"
        modified_report = ctx.paths.coordination_fix_modified(group_id)

        scaffold_model = ctx.resolve_model("coordination_scaffold")
        self._dispatch_helpers.write_model_choice_signal(
            ctx.planspace, f"coord-scaffold-{group_id}", "coordination-scaffold",
            scaffold_model, "default model", None,
        )

        self._logger.log(f"  coordinator: dispatching scaffolder for group {group_id} "
            f"({len(group)} problems)")
        result = self._dispatcher.dispatch(
            scaffold_model, scaffold_prompt, scaffold_output,
            ctx.planspace, codespace=ctx.codespace,
            agent_file=self._task_router.agent_for("coordination.scaffold"),
        )
        if result == ALIGNMENT_CHANGED_PENDING:
            return group_id, None

        return group_id, self._collect_modified_files(modified_report, ctx.codespace)

    def _handle_spec_ambiguity_group(
        self,
        group: list[Problem], group_id: int,
        ctx: DispatchContext,
    ) -> set[str]:
        """Write NEEDS_PARENT signal for spec-ambiguity groups.

        Returns set of affected section numbers.
        """
        sections = sorted({p.section for p in group})
        descriptions = "; ".join(p.description for p in group)
        blocker_signal = {
            "state": SIGNAL_NEEDS_PARENT,
            "why_blocked": (
                f"Spec ambiguity in coordination group {group_id}: "
                f"{descriptions}"
            ),
            "sections": sections,
        }
        blocker_path = ctx.paths.signals_dir() / f"blocker-spec-ambiguity-{group_id}.json"
        blocker_path.parent.mkdir(parents=True, exist_ok=True)
        blocker_path.write_text(json.dumps(blocker_signal, indent=2), encoding="utf-8")
        self._communicator.mailbox_send(
            ctx.planspace,
            f"pause:{PauseType.NEEDS_PARENT}:spec-ambiguity-{group_id}:spec contradicts itself or is underspecified",
            "coordinator",
        )
        self._logger.log(
            f"  coordinator: spec ambiguity in group {group_id} — "
            f"wrote NEEDS_PARENT signal, skipping dispatch",
        )
        return set(sections)

    def _handle_research_needed_group(
        self,
        group: list[Problem], group_id: int,
        ctx: DispatchContext,
    ) -> set[str]:
        """Submit scan.explore task for research-needed groups.

        Returns set of affected section numbers.
        """
        from flow.types.context import FlowEnvelope
        from flow.types.schema import TaskSpec

        sections = sorted({p.section for p in group})
        scope = f"coord-group-{group_id}"

        # Write an exploration prompt describing what needs researching.
        explore_prompt_path = ctx.paths.coordination_dir() / f"research-explore-{group_id}-prompt.md"
        descriptions = "\n".join(
            f"- Section {p.section}: {p.description}" for p in group
        )
        explore_prompt_path.write_text(
            f"# Exploration: Coordination Group {group_id}\n\n"
            f"## Problems Requiring Research\n\n{descriptions}\n\n"
            f"## Files Involved\n\n"
            + "\n".join(f"- `{f}`" for p in group for f in p.files)
            + "\n\nInvestigate these problems and produce findings that "
            "the coordination planner can use to formulate a fix plan.\n",
            encoding="utf-8",
        )

        step = TaskSpec(
            task_type="scan.explore",
            concern_scope=scope,
            payload_path=str(explore_prompt_path),
            priority="normal",
        )
        env = FlowEnvelope(
            db_path=ctx.paths.run_db(),
            submitted_by=f"coordination-research-{group_id}",
            planspace=ctx.planspace,
        )
        self._flow_ingestion.submit_chain(env, [step])

        self._logger.log(
            f"  coordinator: research needed for group {group_id} — "
            f"submitted scan.explore task, skipping fix dispatch",
        )
        return set(sections)

    def _collect_modified_files(self, modified_report: Path, codespace: Path) -> list[str]:
        """Parse the modified-files report, validating paths stay within codespace."""
        if not modified_report.exists():
            return []
        codespace_resolved = codespace.resolve()
        modified: list[str] = []
        for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            pp = Path(line)
            if pp.is_absolute():
                try:
                    rel = pp.resolve().relative_to(codespace_resolved)
                except ValueError:
                    self._logger.log(f"  coordinator: WARNING \u2014 fix path outside "
                        f"codespace, skipping: {line}")
                    continue
            else:
                full = (codespace / pp).resolve()
                try:
                    rel = full.relative_to(codespace_resolved)
                except ValueError:
                    self._logger.log(f"  coordinator: WARNING \u2014 fix path escapes "
                        f"codespace, skipping: {line}")
                    continue
            modified.append(str(rel))
        return modified

    def _persist_modified_files(self, planspace: Path, modified_files: list[str]) -> None:
        self._artifact_io.write_json(
            PathRegistry(planspace).coordination_dir() / "execution-modified-files.json",
            {"files": modified_files},
        )

    def read_execution_modified_files(self, planspace: Path) -> list[str]:
        """Read the persisted list of files modified during coordination."""
        data = self._artifact_io.read_json(
            PathRegistry(planspace).coordination_dir() / "execution-modified-files.json",
        )
        if not isinstance(data, dict):
            return []
        files = data.get("files", [])
        return [str(file_path) for file_path in files] if isinstance(files, list) else []

    def _write_scaffold_assignments(
        self,
        groups: list[ProblemGroup],
        ctx: DispatchContext,
    ) -> set[str]:
        """Write scaffold-assignment signal for scaffold_assign groups.

        Returns the set of section numbers covered by scaffold assignments.
        """
        assignments: list[dict[str, object]] = []
        for group in groups:
            if group.strategy != CoordinationStrategy.SCAFFOLD_ASSIGN:
                continue
            section_files: dict[str, list[str]] = {}
            for problem in group.problems:
                section_files.setdefault(problem.section, [])
                for f in problem.files:
                    if f not in section_files[problem.section]:
                        section_files[problem.section].append(f)
            for section, files in sorted(section_files.items()):
                assignments.append({"section": section, "files": files})

        if not assignments:
            return set()

        signal_path = ctx.paths.scaffold_assignments()
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        self._artifact_io.write_json(signal_path, {"assignments": assignments})
        covered_sections = {a["section"] for a in assignments}
        self._logger.log(
            f"  coordinator: scaffold assignments written for "
            f"{len(assignments)} section(s) — {signal_path.name}",
        )
        return covered_sections

    def _run_bridges_and_overlaps_for_batch(
        self,
        batch: list[int],
        groups: list[ProblemGroup],
        coord_dir: Path,
        ctx: DispatchContext,
    ) -> None:
        for group_index in batch:
            ctrl = self._pipeline_control.poll_control_messages(ctx.planspace)
            if ctrl == ControlSignal.ALIGNMENT_CHANGED:
                raise CoordinationExecutionExit
            group = groups[group_index]
            if group.bridge.needed:
                self._run_bridge_for_group(
                    group_index=group_index,
                    group=group.problems,
                    ctx=ctx,
                    bridge_reason=group.bridge.reason or "planner-requested",
                )
            else:
                self._write_overlap_stats(coord_dir, group_index, group.problems)

    def _dispatch_batch_parallel(
        self,
        batch: list[int],
        batch_num: int,
        groups: list[ProblemGroup],
        ctx: DispatchContext,
        fix_model_default: str,
    ) -> list[str]:
        self._logger.log(f"  coordinator: batch {batch_num} \u2014 {len(batch)} groups in parallel")
        modified: list[str] = []
        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_FIX_WORKERS) as pool:
            futures = {
                pool.submit(
                    self._dispatch_fix_group,
                    groups[group_index].problems,
                    group_index,
                    ctx,
                    fix_model_default,
                ): group_index
                for group_index in batch
            }
            sentinel_hit = False
            for future in as_completed(futures):
                group_index = futures[future]
                try:
                    _, group_modified = future.result()
                    if group_modified is None:
                        sentinel_hit = True
                        for pending in futures:
                            pending.cancel()
                        break
                    modified.extend(group_modified)
                    self._logger.log(
                        f"  coordinator: group {group_index} fix "
                        f"complete ({len(group_modified)} files modified)",
                    )
                except Exception as exc:  # noqa: BLE001 — fail-open: individual group failures must not crash coordination
                    self._logger.log(f"  coordinator: group {group_index} fix FAILED: {exc}")
            if sentinel_hit:
                raise CoordinationExecutionExit
        return modified

    def _dispatch_group_by_strategy(
        self,
        group_index: int,
        groups: list[ProblemGroup],
        ctx: DispatchContext,
        fix_model_default: str,
    ) -> list[str]:
        """Dispatch a single group based on its strategy. Returns modified files."""
        group = groups[group_index]
        strategy = group.strategy

        if strategy == CoordinationStrategy.SCAFFOLD_CREATE:
            _, modified = self._dispatch_scaffold_group(
                group.problems, group_index, ctx,
            )
            if modified is None:
                raise CoordinationExecutionExit
            return modified

        # SEAM_REPAIR and SEQUENTIAL/PARALLEL all go through the fixer.
        # SEAM_REPAIR is functionally identical to the existing bridge+fixer
        # path — the strategy label is used by the planner for intent, and
        # the bridge directive on the group controls bridge dispatch.
        _, modified = self._dispatch_fix_group(
            group.problems,
            group_index,
            ctx,
            default_fix_model=fix_model_default,
        )
        if modified is None:
            raise CoordinationExecutionExit
        return modified

    def execute_coordination_plan(
        self,
        groups: list[ProblemGroup],
        sections_by_num: dict[str, Section],
        ctx: DispatchContext,
        agent_batches: list[list[int]] | None = None,
    ) -> list[str]:
        """Execute the coordination plan and return affected section numbers."""
        # Write scaffold assignments before batching — scaffold_assign groups
        # do not need fix dispatch.
        scaffold_sections = self._write_scaffold_assignments(groups, ctx)

        affected_sections: set[str] = {
            problem.section
            for group in groups
            for problem in group.problems
        }

        # ── Handle non-dispatch strategies before batching ──────────
        # These strategies produce signals/tasks but do NOT dispatch an
        # agent for the group itself.
        _NO_DISPATCH_STRATEGIES = {
            CoordinationStrategy.SCAFFOLD_ASSIGN,
            CoordinationStrategy.SPEC_AMBIGUITY,
            CoordinationStrategy.RESEARCH_NEEDED,
        }
        skip_group_indices: set[int] = set()

        for i, g in enumerate(groups):
            if g.strategy == CoordinationStrategy.SCAFFOLD_ASSIGN:
                skip_group_indices.add(i)
            elif g.strategy == CoordinationStrategy.SPEC_AMBIGUITY:
                skip_group_indices.add(i)
                affected_sections.update(
                    self._handle_spec_ambiguity_group(g.problems, i, ctx),
                )
            elif g.strategy == CoordinationStrategy.RESEARCH_NEEDED:
                skip_group_indices.add(i)
                affected_sections.update(
                    self._handle_research_needed_group(g.problems, i, ctx),
                )

        batches = self._build_execution_batches(groups, agent_batches)
        self._logger.log(f"  coordinator: {len(batches)} execution batches")

        all_modified: list[str] = []
        coord_dir = ctx.paths.coordination_dir()

        for batch_num, batch in enumerate(batches):
            # Filter out non-dispatch groups.
            batch = [gi for gi in batch if gi not in skip_group_indices]
            if not batch:
                continue
            if self._halt_event and self._halt_event.is_set():
                self._logger.log("  coordinator: halt event set — aborting execution")
                raise CoordinationExecutionExit
            ctrl = self._pipeline_control.poll_control_messages(ctx.planspace)
            if ctrl == ControlSignal.ALIGNMENT_CHANGED:
                raise CoordinationExecutionExit

            self._run_bridges_and_overlaps_for_batch(
                batch, groups, coord_dir, ctx,
            )

            ctrl = self._pipeline_control.poll_control_messages(ctx.planspace)
            if ctrl == ControlSignal.ALIGNMENT_CHANGED:
                raise CoordinationExecutionExit

            fix_model_default = ctx.resolve_model("coordination_fix")
            if len(batch) == 1:
                group_index = batch[0]
                all_modified.extend(
                    self._dispatch_group_by_strategy(
                        group_index, groups, ctx, fix_model_default,
                    ),
                )
                continue

            all_modified.extend(
                self._dispatch_batch_parallel(
                    batch, batch_num, groups,
                    ctx, fix_model_default,
                ),
            )

        self._logger.log(f"  coordinator: fixes complete, {len(all_modified)} total files modified")

        file_to_sections: dict[str, set[str]] = {}
        for section_num, section in sections_by_num.items():
            for file_path in section.related_files:
                file_to_sections.setdefault(file_path, set()).add(section_num)
        for modified_file in all_modified:
            affected_sections.update(file_to_sections.get(modified_file, set()))

        self._persist_modified_files(ctx.planspace, all_modified)
        return sorted(affected_sections)


# ---------------------------------------------------------------------------
# Pure helpers (no Services usage)
# ---------------------------------------------------------------------------

def _try_place_in_batch(
    group_index: int,
    files: set[str],
    batches: list[list[int]],
    group_file_sets: list[set[str]],
    *,
    allowed_indices: set[int] | None = None,
) -> None:
    """Place group_index into an existing compatible batch, or create a new one."""
    if not files:
        batches.append([group_index])
        return
    for batch in batches:
        if allowed_indices is not None and any(i not in allowed_indices for i in batch):
            continue
        batch_files = set().union(*(group_file_sets[i] for i in batch))
        if not batch_files or not (files & batch_files):
            batch.append(group_index)
            return
    batches.append([group_index])
