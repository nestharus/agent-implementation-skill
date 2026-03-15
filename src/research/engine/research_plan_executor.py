"""Script-owned research plan execution into flow fanout submissions."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from flow.types.schema import BranchSpec, GateSpec, TaskSpec
from orchestrator.path_registry import PathRegistry
from flow.types.context import FlowEnvelope
from research.engine.orchestrator import ResearchState
from research.engine.research_branch_builder import ordered_ticket_ids

if TYPE_CHECKING:
    from containers import FlowIngestionService, FreshnessService
    from research.engine.orchestrator import ResearchOrchestrator
    from research.engine.research_branch_builder import ResearchBranchBuilder
    from research.prompt.writers import ResearchPromptWriter


class ResearchPlanExecutor:
    """Translates semantic research plans into flow submissions."""

    def __init__(
        self,
        freshness: FreshnessService,
        flow_ingestion: FlowIngestionService,
        orchestrator: ResearchOrchestrator,
        branch_builder: ResearchBranchBuilder,
        prompt_writer: ResearchPromptWriter,
    ) -> None:
        self._freshness = freshness
        self._flow_ingestion = flow_ingestion
        self._orchestrator = orchestrator
        self._branch_builder = branch_builder
        self._prompt_writer = prompt_writer

    def execute_research_plan(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path | None,
        plan_output_path: Path,
    ) -> bool:
        """Translate semantic research plan into flow submissions."""
        status = self._orchestrator.load_research_status(section_number, planspace) or {}
        trigger_hash = str(status.get("trigger_hash", ""))
        cycle_id = str(status.get("cycle_id", ""))

        plan = self._validate_plan(section_number, trigger_hash, cycle_id, planspace)
        if plan is None:
            return False

        not_researchable = [
            item for item in plan.get("not_researchable", []) if isinstance(item, dict)
        ]
        self._branch_builder.emit_not_researchable_signals(section_number, planspace, not_researchable)

        branches = self._collect_branches(
            plan, section_number, planspace, codespace, trigger_hash, cycle_id,
        )
        if branches is None:
            return False

        if not branches:
            self._fail_status(section_number, planspace, trigger_hash, cycle_id,
                         "planner returned no researchable tickets")
            return bool(not_researchable)

        synthesis_prompt = self._write_synthesis(
            section_number, planspace, len(branches), trigger_hash, cycle_id,
        )
        if synthesis_prompt is None:
            return False

        self._submit_fanout(
            section_number, planspace, branches, synthesis_prompt,
            plan_output_path, trigger_hash, cycle_id,
        )
        return True

    def _submit_fanout(
        self,
        section_number: str,
        planspace: Path,
        branches: list[BranchSpec],
        synthesis_prompt: Path,
        plan_output_path: Path,
        trigger_hash: str,
        cycle_id: str,
    ) -> None:
        """Write submission status, compute freshness, and submit the fanout."""
        paths = PathRegistry(planspace)
        self._orchestrator.write_research_status(
            section_number, planspace, ResearchState.TICKETS_SUBMITTED,
            detail=f"submitted {len(branches)} research ticket branches",
            trigger_hash=trigger_hash, cycle_id=cycle_id,
        )

        post_write_freshness = self._freshness.compute(planspace, section_number)

        flow_id = self._flow_ingestion.new_flow_id()
        gate = GateSpec(
            mode="all",
            failure_policy="include",
            synthesis=TaskSpec(
                task_type="research.synthesis",
                concern_scope=f"section-{section_number}",
                payload_path=str(synthesis_prompt),
                problem_id=f"research-{section_number}",
            ),
        )
        origin_refs = [str(paths.research_plan(section_number)), str(plan_output_path)]
        self._flow_ingestion.submit_fanout(
            FlowEnvelope(
                db_path=paths.run_db(),
                submitted_by=f"research-{section_number}",
                flow_id=flow_id,
                origin_refs=origin_refs,
                planspace=planspace,
                freshness_token=post_write_freshness,
            ),
            branches,
            gate=gate,
        )

    def submit_research_verify(
        self,
        section_number: str,
        planspace: Path,
        *,
        db_path: Path,
        declared_by_task_id: int | None,
        origin_refs: list[str] | None = None,
    ) -> bool:
        """Submit the research verifier as a follow-on task."""
        status = self._orchestrator.load_research_status(section_number, planspace) or {}
        verify_prompt = self._prompt_writer.write_research_verify_prompt(section_number, planspace)
        if verify_prompt is None:
            self._orchestrator.write_research_status(
                section_number,
                planspace,
                ResearchState.FAILED,
                detail="failed to write research verification prompt",
                trigger_hash=str(status.get("trigger_hash", "")),
                cycle_id=str(status.get("cycle_id", "")),
            )
            return False

        self._flow_ingestion.submit_chain(
            FlowEnvelope(
                db_path=db_path,
                submitted_by=f"research-{section_number}",
                declared_by_task_id=declared_by_task_id,
                origin_refs=origin_refs or [str(PathRegistry(planspace).research_claims(section_number))],
                planspace=planspace,
            ),
            [
                TaskSpec(
                    task_type="research.verify",
                    concern_scope=f"section-{section_number}",
                    payload_path=str(verify_prompt),
                    problem_id=f"research-{section_number}",
                )
            ],
        )
        self._orchestrator.write_research_status(
            section_number,
            planspace,
            ResearchState.VERIFYING,
            detail="submitted research verification",
            trigger_hash=str(status.get("trigger_hash", "")),
            cycle_id=str(status.get("cycle_id", "")),
        )
        return True

    # -------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------

    def _fail_status(
        self,
        section_number: str,
        planspace: Path,
        trigger_hash: str,
        cycle_id: str,
        detail: str,
    ) -> None:
        """Write a failure status entry."""
        self._orchestrator.write_research_status(
            section_number, planspace, ResearchState.FAILED,
            detail=detail, trigger_hash=trigger_hash, cycle_id=cycle_id,
        )

    def _validate_plan(
        self,
        section_number: str,
        trigger_hash: str,
        cycle_id: str,
        planspace: Path,
    ) -> dict | None:
        """Validate the research plan, writing a failure status if invalid."""
        plan = self._orchestrator.validate_research_plan(PathRegistry(planspace).research_plan(section_number))
        if plan is None:
            self._fail_status(section_number, planspace, trigger_hash, cycle_id,
                         "research-plan.json missing or malformed")
        return plan

    def _collect_branches(
        self,
        plan: dict,
        section_number: str,
        planspace: Path,
        codespace: Path | None,
        trigger_hash: str,
        cycle_id: str,
    ) -> list[BranchSpec] | None:
        """Build branch specs from ordered tickets."""
        tickets_by_id = {
            str(ticket.get("ticket_id", "")): ticket
            for ticket in plan.get("tickets", [])
            if isinstance(ticket, dict) and str(ticket.get("ticket_id", ""))
        }
        branches: list[BranchSpec] = []

        for ticket_index, ticket_id in enumerate(ordered_ticket_ids(plan), start=1):
            ticket = tickets_by_id.get(ticket_id)
            if ticket is None:
                continue
            branch = self._branch_builder.build_branch(
                section_number=section_number,
                planspace=planspace,
                codespace=codespace,
                ticket=ticket,
                ticket_index=ticket_index,
            )
            if branch is None:
                self._fail_status(section_number, planspace, trigger_hash, cycle_id,
                             f"failed to build research branch for {ticket_id}")
                return None
            branches.append(branch)

        return branches

    def _write_synthesis(
        self,
        section_number: str,
        planspace: Path,
        branch_count: int,
        trigger_hash: str,
        cycle_id: str,
    ) -> Path | None:
        """Write the synthesis prompt, returning ``None`` on failure."""
        synthesis_prompt = self._prompt_writer.write_research_synthesis_prompt(
            section_number, planspace, branch_count,
        )
        if synthesis_prompt is None:
            self._fail_status(section_number, planspace, trigger_hash, cycle_id,
                         "failed to write research synthesis prompt")
        return synthesis_prompt
