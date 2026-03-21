"""Intent bootstrap pipeline for section-loop runner.

Decomposes the former ``run_intent_bootstrap`` god function into
single-concern steps composed via the pipeline engine.  Alignment
guards and logging are handled by middleware — not inlined.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from coordination.repository.notes import list_notes_to
from orchestrator.path_registry import PathRegistry
from signals.service.blocker_manager import update_blocker_rollup
from orchestrator.types import PauseType, Section

from pipeline import AlignmentGuard, Pipeline, PipelineContext, Step
from intent.service.philosophy_bootstrap_state import BOOTSTRAP_READY
from signals.types import BLOCKING_NEED_DECISION, BLOCKING_NEED_DECISION, INTENT_MODE_FULL, INTENT_MODE_LIGHTWEIGHT

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        Communicator,
        LogService,
        ModelPolicyService,
        PipelineControlService,
    )
    from intake.service.governance_packet_builder import GovernancePacketBuilder
    from intent.service.intent_pack_generator import IntentPackGenerator
    from intent.service.intent_triager import IntentTriager
    from intent.service.philosophy_bootstrapper import PhilosophyBootstrapper

class IntentInitializer:
    """Intent bootstrap pipeline for section-loop runner."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        governance_packet_builder: GovernancePacketBuilder,
        intent_pack_generator: IntentPackGenerator,
        intent_triager: IntentTriager,
        logger: LogService,
        philosophy_bootstrapper: PhilosophyBootstrapper,
        pipeline_control: PipelineControlService,
        policies: ModelPolicyService,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._governance_packet_builder = governance_packet_builder
        self._intent_pack_generator = intent_pack_generator
        self._intent_triager = intent_triager
        self._logger = logger
        self._philosophy_bootstrapper = philosophy_bootstrapper
        self._pipeline_control = pipeline_control
        self._policies = policies

    def _step_triage(self, ctx: PipelineContext) -> dict:
        """Run intent triage to determine intent mode."""
        paths = ctx.paths
        pf_path = paths.problem_frame(ctx.section.number)
        pf_content = (
            pf_path.read_text(encoding="utf-8").strip()
            if pf_path.exists()
            else ""
        )
        ctx.state["pf_content"] = pf_content

        notes_count = len(list_notes_to(paths, ctx.section.number))

        result = self._intent_triager.run_intent_triage(
            ctx.section.number,
            ctx.planspace,
            ctx.codespace,
            related_files_count=len(ctx.section.related_files),
            incoming_notes_count=notes_count,
            solve_count=ctx.section.solve_count,
            section_summary=pf_content if pf_content else "",
        )
        ctx.state["intent_mode"] = result.get("intent_mode", INTENT_MODE_LIGHTWEIGHT)
        return result

    def _step_extract_todos(self, ctx: PipelineContext) -> str:
        """Extract TODO comments from related files and record traceability."""
        paths = ctx.paths
        todos_path = paths.todos(ctx.section.number)

        todo_entries = extract_todos_from_files(
            ctx.codespace, ctx.section.related_files,
        )
        artifact_name = f"section-{ctx.section.number}-todos.md"

        if todo_entries:
            todos_path.write_text(todo_entries, encoding="utf-8")
            self._logger.log(f"Section {ctx.section.number}: extracted TODOs from related files")
            self._communicator.record_traceability(
                ctx.planspace, ctx.section.number, artifact_name,
                "related files TODO extraction",
                "in-code microstrategies for alignment",
            )
        elif todos_path.exists():
            todos_path.unlink()
            self._logger.log(
                f"Section {ctx.section.number}: removed stale TODO extraction "
                "(no TODOs remaining)",
            )
            self._communicator.record_traceability(
                ctx.planspace, ctx.section.number, artifact_name,
                "related files TODO extraction",
                "in-code microstrategies for alignment",
            )
        else:
            self._logger.log(f"Section {ctx.section.number}: no TODOs found in related files")

        return todo_entries or ""

    def _step_philosophy(self, ctx: PipelineContext) -> dict:
        """Ensure global philosophy is bootstrapped.

        If philosophy needs user input, pauses for parent and retries
        once after resume. Non-NEED_DECISION blockers halt immediately.
        """
        sec = ctx.section.number
        max_pause_retries = 3

        for attempt in range(1, max_pause_retries + 1):
            result = self._philosophy_bootstrapper.ensure_global_philosophy(
                ctx.planspace, ctx.codespace,
            )

            if result["status"] == BOOTSTRAP_READY:
                return result

            blocking_state = result.get("blocking_state")

            if blocking_state == BLOCKING_NEED_DECISION:
                self._logger.log(
                    f"Section {sec}: philosophy bootstrap needs "
                    f"user input — {result['detail']}",
                )
                update_blocker_rollup(ctx.planspace)
                self._pipeline_control.pause_for_parent(
                    ctx.planspace,
                    f"pause:{PauseType.NEED_DECISION}:global:philosophy bootstrap requires user input",
                )
                self._logger.log(
                    f"Section {sec}: resumed after philosophy input "
                    f"(attempt {attempt}/{max_pause_retries})",
                )
                continue  # retry after parent responds

            if blocking_state == BLOCKING_NEED_DECISION:
                self._logger.log(
                    f"Section {sec}: philosophy bootstrap needs "
                    f"parent intervention — {result['detail']}",
                )
            else:
                self._logger.log(
                    f"Section {sec}: philosophy unavailable — "
                    f"{result['detail']}",
                )
            return None  # halt pipeline for non-resumable blockers

        self._logger.log(
            f"Section {sec}: philosophy still not ready after "
            f"{max_pause_retries} pause/resume attempts",
        )
        return None

    def _step_governance(self, ctx: PipelineContext) -> str:
        """Build the section governance packet."""
        pf_content = ctx.state.get("pf_content", "")
        self._governance_packet_builder.build_section_governance_packet(
            ctx.section.number,
            ctx.planspace,
            pf_content if pf_content else "",
        )
        return "ok"

    def _step_intent_pack(self, ctx: PipelineContext) -> str:
        """Generate full intent pack (only in full mode)."""
        self._intent_pack_generator.generate_intent_pack(
            ctx.section,
            ctx.planspace,
            ctx.codespace,
            incoming_notes=ctx.state.get("incoming_notes", ""),
        )
        self._logger.log(f"Section {ctx.section.number}: intent bootstrap complete (full mode)")
        return "ok"

    def run_intent_bootstrap(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
        incoming_notes: str | None,
    ) -> object | None:
        """Run intent triage, TODO surfacing, philosophy, and intent pack setup."""
        ctx = PipelineContext(
            section=section,
            planspace=planspace,
            codespace=codespace,
            policy=self._policies.load(planspace),
            paths=PathRegistry(planspace),
            state={"incoming_notes": incoming_notes or ""},
        )

        steps = [
            Step("triage", self._step_triage),
            Step("extract-todos", self._step_extract_todos, guard=_has_related_files),
            Step("philosophy", self._step_philosophy),
            Step("governance", self._step_governance),
            Step("intent-pack", self._step_intent_pack, guard=_is_full_mode),
        ]

        pipe = Pipeline(
            "intent-bootstrap",
            steps=steps,
            middleware=[
                AlignmentGuard(
                    self._pipeline_control.alignment_changed_pending,
                    after_steps={"philosophy", "intent-pack"},
                ),
            ],
        )
        return pipe.run(ctx)


# -- Guards ----------------------------------------------------------------

def _has_related_files(ctx: PipelineContext) -> bool:
    return bool(ctx.section.related_files)


def _is_full_mode(ctx: PipelineContext) -> bool:
    return ctx.state.get("intent_mode") == INTENT_MODE_FULL


# -- Helpers ---------------------------------------------------------------

def extract_todos_from_files(codespace: Path, related_files: list[str]) -> str:
    from implementation.service.microstrategy_decider import (
        extract_todos_from_files as extract_todos,
    )

    return extract_todos(codespace, related_files)
