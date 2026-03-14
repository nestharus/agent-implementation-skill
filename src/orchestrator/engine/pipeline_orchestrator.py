from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        ChangeTrackerService,
        Communicator,
        ConfigService,
        LogService,
        PipelineControlService,
        PromptGuard,
        SectionAlignmentService,
    )

from intake.service.assessment_evaluator import AssessmentEvaluator
from intake.repository.governance_loader import bootstrap_governance_if_missing, GovernanceLoader
from coordination.engine.coordination_controller import run_coordination_loop
from coordination.types import CoordinationStatus
from implementation.engine.implementation_phase import (
    ImplementationPassExit,
    ImplementationPassRestart,
    run_implementation_pass,
)
from orchestrator.path_registry import PathRegistry
from scan.service.project_mode import resolve_project_mode, write_mode_contract
from proposal.engine.proposal_phase import ProposalPassExit, run_proposal_pass
from reconciliation.engine.reconciliation_phase import ReconciliationPhaseExit, run_reconciliation_phase
from scan.service.section_loader import load_sections
from flow.service.task_db_client import init_db
from pipeline.context import DispatchContext
from signals.types import TRUNCATE_SUMMARY
from orchestrator.engine.strategic_state_builder import build_strategic_state
from orchestrator.repository.cycle_state import CycleState
from orchestrator.types import PipelineAbortError, SectionResult

logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(message)s",
    stream=sys.stderr,
)

_MAX_BLOCKERS_IN_SUMMARY = 3


class PipelineOrchestrator:
    def __init__(
        self,
        communicator: Communicator,
        logger: LogService,
        config: ConfigService,
        artifact_io: ArtifactIOService,
        prompt_guard: PromptGuard,
        section_alignment: SectionAlignmentService,
        change_tracker: ChangeTrackerService,
        pipeline_control: PipelineControlService,
    ) -> None:
        self._communicator = communicator
        self._logger = logger
        self._config = config
        self._artifact_io = artifact_io
        self._prompt_guard = prompt_guard
        self._section_alignment = section_alignment
        self._change_tracker = change_tracker
        self._pipeline_control = pipeline_control
        self._check_and_clear_alignment_changed = change_tracker.make_alignment_checker()

    def main(self) -> None:
        """Run the section loop orchestrator CLI."""
        import argparse

        parser = argparse.ArgumentParser(
            description="Section loop orchestrator for the implementation pipeline.",
        )
        parser.add_argument("planspace", type=Path,
                            help="Path to the planspace directory")
        parser.add_argument("codespace", type=Path,
                            help="Path to the codespace directory")
        parser.add_argument("--global-proposal", type=Path, required=True,
                            dest="global_proposal",
                            help="Path to the global proposal document")
        parser.add_argument("--global-alignment", type=Path, required=True,
                            dest="global_alignment",
                            help="Path to the global alignment document")
        parser.add_argument("--parent", type=str, default="orchestrator",
                            help="Parent agent mailbox name (default: orchestrator)")

        args = parser.parse_args()

        # Validate paths
        if not args.global_proposal.exists():
            print(f"Error: global proposal not found: {args.global_proposal}")
            sys.exit(1)
        if not args.global_alignment.exists():
            print(f"Error: global alignment not found: {args.global_alignment}")
            sys.exit(1)

        paths = PathRegistry(args.planspace)
        paths.ensure_artifacts_tree()
        sections_dir = paths.sections_dir()

        # Initialize coordination DB (idempotent) and register
        init_db(paths.run_db())
        self._communicator.mailbox_register(args.planspace)
        self._communicator.set_parent(args.parent)
        self._pipeline_control.set_parent(args.parent)
        self._logger.log(f"Registered: {self._config.agent_name} (parent: {args.parent})")

        ctx = DispatchContext(planspace=args.planspace, codespace=args.codespace)

        try:
            self._run_loop(ctx, sections_dir,
                      args.global_proposal, args.global_alignment)
        except PipelineAbortError:
            self._logger.log("Pipeline aborted")
        finally:
            self._communicator.mailbox_cleanup(args.planspace)
            self._logger.log("Mailbox cleaned up")

    def _run_phase2(
        self,
        sections_by_num: dict,
        cycle: CycleState,
        ctx: DispatchContext,
    ) -> CoordinationStatus | str:
        """Run Phase 2: strategic state, global recheck, and coordination."""
        section_results = cycle.section_results
        build_strategic_state(PathRegistry(ctx.planspace).decisions_dir(), section_results, ctx.planspace)

        evaluator = AssessmentEvaluator(
            artifact_io=self._artifact_io,
            prompt_guard=self._prompt_guard,
        )
        promoted = evaluator.promote_debt_signals(ctx.planspace)
        if promoted:
            self._logger.log(f"Stabilization: promoted {len(promoted)} debt entries to staging")

        phase2_status = self._section_alignment.run_global_recheck(
            sections_by_num, section_results, ctx.planspace, ctx.codespace,
        )
        if phase2_status == CoordinationStatus.RESTART_PHASE1:
            return CoordinationStatus.RESTART_PHASE1

        coordination_status = run_coordination_loop(
            section_results, sections_by_num, ctx,
        )
        return coordination_status or CoordinationStatus.COMPLETE

    def _run_loop(self, ctx: DispatchContext,
                  sections_dir: Path, global_proposal: Path,
                  global_alignment: Path) -> None:
        bootstrap_governance_if_missing(ctx.codespace)
        governance_loader = GovernanceLoader(artifact_io=self._artifact_io)
        governance_loader.build_governance_indexes(ctx.codespace, ctx.planspace)
        project_mode, mode_constraints = resolve_project_mode(ctx.planspace)
        write_mode_contract(ctx.planspace, project_mode, mode_constraints)

        all_sections = load_sections(sections_dir)
        for sec in all_sections:
            sec.global_proposal_path = global_proposal
            sec.global_alignment_path = global_alignment
        sections_by_num = {s.number: s for s in all_sections}
        self._logger.log(f"Loaded {len(all_sections)} sections")

        paths = PathRegistry(ctx.planspace)
        cycle = CycleState(
            artifact_io=self._artifact_io,
            proposal_path=paths.proposal_results(),
            section_path=paths.section_results(),
        )

        while True:
            cycle.clear_all()
            try:
                proposal_results = run_proposal_pass(
                    all_sections, sections_by_num, ctx.planspace, ctx.codespace,
                )
            except ProposalPassExit:
                return
            cycle.update_proposals(proposal_results)

            try:
                reconciliation = run_reconciliation_phase(
                    cycle.proposal_results, sections_by_num, all_sections,
                    ctx.planspace, ctx.codespace,
                )
            except ReconciliationPhaseExit:
                return

            cycle.flush()  # reconciliation mutates proposal_results in-place

            blocked_sections = reconciliation.removed_section_numbers
            if reconciliation.alignment_changed:
                continue

            try:
                cycle.update_sections(
                    run_implementation_pass(
                        cycle.proposal_results, sections_by_num,
                        ctx.planspace, ctx.codespace,
                    ),
                )
            except ImplementationPassRestart:
                continue
            except ImplementationPassExit:
                return

            _record_blocked_sections(
                blocked_sections, cycle.proposal_results, cycle.section_results,
            )
            cycle.flush()

            implemented_sections = [
                sec_num for sec_num, result in cycle.section_results.items()
                if result.aligned
            ]
            self._logger.log(f"=== Phase 1 complete: {len(implemented_sections)} sections "
                f"implemented, {len(blocked_sections)} blocked ===")

            status = self._run_phase2(
                sections_by_num, cycle, ctx,
            )
            if status == CoordinationStatus.RESTART_PHASE1:
                continue
            return


# Pure function -- no Services usage

def _record_blocked_sections(
    blocked_sections: list[str],
    proposal_results: dict,
    section_results: dict[str, SectionResult],
) -> None:
    """Record blocked sections as non-aligned results for Phase 2."""
    for sec_num in blocked_sections:
        pr = proposal_results[sec_num]
        blocker_summary = "; ".join(
            b.get("description", "unknown")[:TRUNCATE_SUMMARY]
            for b in pr.blockers[:_MAX_BLOCKERS_IN_SUMMARY]
        ) or "execution not ready"
        section_results.setdefault(sec_num, SectionResult(
            section_number=sec_num,
            aligned=False,
            problems=f"readiness blocked: {blocker_summary}",
        ))


# Backward-compat wrappers

def _get_orchestrator() -> PipelineOrchestrator:
    from containers import Services
    return PipelineOrchestrator(
        communicator=Services.communicator(),
        logger=Services.logger(),
        config=Services.config(),
        artifact_io=Services.artifact_io(),
        prompt_guard=Services.prompt_guard(),
        section_alignment=Services.section_alignment(),
        change_tracker=Services.change_tracker(),
        pipeline_control=Services.pipeline_control(),
    )


def main() -> None:
    _get_orchestrator().main()


if __name__ == "__main__":
    main()
