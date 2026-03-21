from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        Communicator,
        FreshnessService,
        HasherService,
        LogService,
        ResearchOrchestratorService,
    )
    from research.prompt.writers import ResearchPromptWriter

from orchestrator.path_registry import PathRegistry
from proposal.repository.state import ProposalState, State as ProposalStateRepo
from proposal.service.proposal_history import ProposalHistoryRecorder
from proposal.service.readiness_resolver import ReadinessResolver
from reconciliation.repository.queue import Queue as ReconciliationQueue
from research.engine.orchestrator import ResearchState
from risk.service.readiness_risk_bridge import ReadinessRiskBridge
from signals.service.blocker_manager import (
    append_open_problem,
    update_blocker_rollup,
)
from orchestrator.types import ProposalPassResult
from flow.types.routing import Task, request_task
from flow.service.task_db_client import request_user_input, subscribe_to_task
from signals.types import PASS_MODE_PROPOSAL, SIGNAL_NEED_DECISION

_CANDIDATE_HASH_LENGTH = 8
_TRIGGER_HASH_LENGTH = 12


@dataclass
class GateResult:
    ready: bool
    blockers: list[dict]
    proposal_pass_result: ProposalPassResult | None = None


class ReadinessGate:
    def __init__(
        self,
        logger: LogService,
        artifact_io: ArtifactIOService,
        hasher: HasherService,
        communicator: Communicator,
        research: ResearchOrchestratorService,
        freshness: FreshnessService,
        prompt_writer: ResearchPromptWriter,
        reconciliation_queue: ReconciliationQueue,
        readiness_resolver: ReadinessResolver | None = None,
        readiness_risk_bridge: ReadinessRiskBridge | None = None,
        proposal_history: ProposalHistoryRecorder | None = None,
    ) -> None:
        self._logger = logger
        self._artifact_io = artifact_io
        self._hasher = hasher
        self._communicator = communicator
        self._research = research
        self._freshness = freshness
        self._prompt_writer = prompt_writer
        self._reconciliation_queue = reconciliation_queue
        self._readiness_resolver = readiness_resolver or ReadinessResolver(artifact_io=artifact_io)
        self._readiness_risk_bridge = readiness_risk_bridge or ReadinessRiskBridge(
            artifact_io=artifact_io, logger=logger,
        )
        self._proposal_history = proposal_history or ProposalHistoryRecorder(artifact_io=artifact_io)

    def _emit_need_decision_research_signals(
        self,
        signal_dir: Path,
        section_number: str,
        research_questions: list[str],
        *,
        needs: str,
        why_blocked: str,
        detail_log: str,
    ) -> None:
        """Emit one need_decision signal per blocking research question."""
        for i, question in enumerate(research_questions):
            research_signal = {
                "state": SIGNAL_NEED_DECISION,
                "section": section_number,
                "detail": question,
                "needs": needs,
                "why_blocked": why_blocked,
                "source": "proposal-state:blocking_research_questions",
            }
            sig_path = (
                signal_dir
                / f"section-{section_number}-blocking-research-{i}-signal.json"
            )
            self._artifact_io.write_json(sig_path, research_signal)
            self._logger.log(
                f"Section {section_number}: {detail_log} "
                f"for blocking_research_question[{i}]"
            )

    def publish_discoveries(
        self,
        section_number: str,
        proposal_state: ProposalState,
        planspace: Path,
    ) -> None:
        """Publish durable discovery artifacts from proposal state."""
        registry = PathRegistry(planspace)
        scope_delta_dir = registry.scope_deltas_dir()

        for candidate in proposal_state.new_section_candidates:
            cand_text = str(candidate)
            cand_hash = self._hasher.content_hash(cand_text)[:_CANDIDATE_HASH_LENGTH]
            delta_id = f"delta-{section_number}-candidate-{cand_hash}"
            requires_reframe = (
                isinstance(candidate, dict)
                and candidate.get("requires_root_reframing") is True
            )
            scope_delta = {
                "delta_id": delta_id,
                "section": section_number,
                "signal": "new_section_candidate",
                "detail": cand_text,
                "requires_root_reframing": requires_reframe,
                "source": "proposal-state:new_section_candidates",
            }
            delta_path = registry.scope_delta_candidate(section_number, cand_hash)
            self._artifact_io.write_json(delta_path, scope_delta)
            self._logger.log(
                f"Section {section_number}: wrote scope-delta for "
                f"new_section_candidate ({cand_hash})"
            )

            # Gap 1: root-reframe signal — when a scope expansion
            # requires root reframing, write the global signal so
            # all section dispatches pause until coordination handles it.
            if requires_reframe:
                reframe_path = registry.root_reframe_signal()
                self._artifact_io.write_json(reframe_path, {
                    "source_section": section_number,
                    "delta_id": delta_id,
                    "detail": cand_text,
                    "reason": "scope expansion requires root reframing",
                })
                self._logger.log(
                    f"Section {section_number}: root-reframe signal "
                    f"emitted for scope delta {delta_id}"
                )

        for question in proposal_state.research_questions:
            append_open_problem(
                planspace,
                section_number,
                str(question),
                "proposal-state:research_question",
            )
        rq_list = proposal_state.research_questions
        if rq_list:
            open_problems_dir = registry.open_problems_dir()
            rq_artifact = {
                "section": section_number,
                "research_questions": [str(q) for q in rq_list],
                "source": "proposal-state",
            }
            rq_path = registry.research_questions_artifact(section_number)
            self._artifact_io.write_json(rq_path, rq_artifact)
            self._logger.log(
                f"Section {section_number}: wrote {len(rq_list)} "
                f"research questions to open-problems artifact"
            )

    def _route_blocking_research(
        self,
        registry: PathRegistry,
        signal_dir: Path,
        section_number: str,
        planspace: Path,
        codespace: Path | None,
        questions: list[str],
    ) -> None:
        """Dispatch research or escalate blocking research questions."""
        trigger_hash = self._research.compute_trigger_hash(questions)
        cycle_id = f"research-{section_number}-{trigger_hash[:_TRIGGER_HASH_LENGTH]}"

        if self._research.is_complete_for_trigger(section_number, planspace, trigger_hash):
            self._emit_need_decision_research_signals(
                signal_dir, section_number, questions,
                needs="Parent/coordination answer — research could not resolve",
                why_blocked="Research completed but blocking question remains unresolved",
                detail_log="research complete but question unresolved — emitting NEED_DECISION signal",
            )
            return

        research_section_dir = registry.research_section_dir(section_number)
        trigger_path = registry.research_trigger(section_number)
        trigger = {
            "section": section_number,
            "trigger_source": "proposal-state:blocking_research_questions",
            "questions": questions,
            "trigger_hash": trigger_hash,
            "cycle_id": cycle_id,
        }
        self._artifact_io.write_json(trigger_path, trigger)
        prompt_path = self._prompt_writer.write_research_plan_prompt(
            section_number, planspace, codespace, trigger_path,
        )
        if prompt_path is None:
            self._emit_need_decision_research_signals(
                signal_dir, section_number, questions,
                needs="Parent/coordination answer to this blocking research question",
                why_blocked="Research prompt generation failed validation and cannot be dispatched safely",
                detail_log="research prompt blocked by validation — emitting NEED_DECISION signal",
            )
            self._research.write_status(
                section_number, planspace, ResearchState.FAILED,
                detail="research plan prompt blocked by validation",
                trigger_hash=trigger_hash, cycle_id=cycle_id,
            )
            return

        # Write status BEFORE computing freshness so the hash includes
        # research-status.json at both submission and dispatch time.
        self._research.write_status(
            section_number, planspace, ResearchState.PLANNED,
            trigger_hash=trigger_hash, cycle_id=cycle_id,
        )
        freshness = self._freshness.compute(planspace, section_number)
        task_id = request_task(
            registry.run_db(),
            Task(
                task_type="research.plan",
                submitted_by=f"readiness-{section_number}",
                concern_scope=f"section-{section_number}",
                payload_path=str(prompt_path),
                problem_id=f"research-{section_number}",
                freshness_token=freshness,
            ),
        )
        self._logger.log(
            f"Section {section_number}: dispatched research_plan "
            f"task {task_id} with prompt and freshness token"
        )

    def _route_user_root_questions(
        self,
        _signal_dir: Path, section_number: str, proposal_state: ProposalState,
        planspace: Path,
    ) -> None:
        """Submit user-input research tasks for user-root questions."""
        registry = PathRegistry(planspace)
        for i, question in enumerate(proposal_state.user_root_questions):
            prompt_path = (
                registry.research_section_dir(section_number)
                / f"user-input-{i:02d}-prompt.md"
            )
            spec_path = (
                registry.research_section_dir(section_number)
                / f"user-input-{i:02d}-spec.json"
            )
            response_path = (
                registry.research_section_dir(section_number)
                / f"user-input-{i:02d}-response.json"
            )
            prompt_lines = [
                "# Readiness User Input Prompt",
                "",
                f"## Section: {section_number}",
                "",
                "## Question",
                "",
                str(question),
                "",
                "## Response Artifact",
                "",
                f"`{response_path}`",
                "",
                "## Instructions",
                "",
                "This task is released only after the user response is validated and recorded.",
                "Read the response artifact, interpret the answer in the context of readiness blocking, and produce a structured response for downstream proposal handling.",
                "Fail closed if the response artifact is missing or malformed.",
            ]
            if not self._prompt_writer._prompt_guard.write_validated(  # noqa: SLF001
                "\n".join(prompt_lines),
                prompt_path,
            ):
                continue
            self._artifact_io.write_json(
                spec_path,
                {
                    "question_text": str(question),
                    "response_schema_json": {
                        "type": "object",
                    },
                },
            )
            task_id = request_task(
                registry.run_db(),
                Task(
                    task_type="research.user_input",
                    submitted_by=f"readiness-{section_number}",
                    concern_scope=f"section-{section_number}",
                    payload_path=str(prompt_path),
                    problem_id=f"readiness-user-input-{section_number}-{i:02d}",
                ),
                dedupe_key=json.dumps(
                    {
                        "task_type": "research.user_input",
                        "section": section_number,
                        "question": str(question),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            request_user_input(
                registry.run_db(),
                task_id,
                str(question),
                response_schema_json={"type": "object"},
            )
            subscribe_to_task(
                registry.run_db(),
                task_id,
                f"section-{section_number}",
                verification_mode="validated_user_input",
            )
            self._logger.log(
                f"Section {section_number}: submitted research.user_input "
                f"task {task_id} for user_root_question[{i}]"
            )

    def _route_shared_seams(
        self,
        signal_dir: Path, section_number: str, proposal_state: ProposalState,
    ) -> None:
        """Emit substrate triggers and need_decision signals for shared seams."""
        for i, seam in enumerate(proposal_state.shared_seam_candidates):
            trigger = {
                "section": section_number,
                "seam": str(seam),
                "source": "proposal-state:shared_seam_candidates",
                "trigger_type": "shared_seam",
            }
            trigger_path = signal_dir / f"substrate-trigger-{section_number}-{i:02d}.json"
            self._artifact_io.write_json(trigger_path, trigger)
            self._logger.log(
                f"Section {section_number}: wrote substrate-trigger "
                f"for shared_seam_candidate[{i}]"
            )

            seam_signal = {
                "state": SIGNAL_NEED_DECISION,
                "section": section_number,
                "detail": (
                    "Shared seam candidate requires cross-section "
                    f"substrate work: {str(seam)}"
                ),
                "needs": "SIS/substrate coordination for shared seam",
                "why_blocked": (
                    "Shared seam cannot be resolved within a single "
                    "section — requires substrate-level coordination"
                ),
                "source": "proposal-state:shared_seam_candidates",
            }
            sig_path = signal_dir / f"section-{section_number}-seam-{i}-signal.json"
            self._artifact_io.write_json(sig_path, seam_signal)

    def _route_unresolved_contracts(
        self,
        section_number: str, proposal_state: ProposalState, planspace: Path,
    ) -> None:
        """Queue reconciliation for unresolved contracts/anchors."""
        unresolved_contracts = [
            str(c) for c in proposal_state.unresolved_contracts
        ]
        unresolved_anchors = [
            str(a) for a in proposal_state.unresolved_anchors
        ]
        if not unresolved_contracts and not unresolved_anchors:
            return
        self._reconciliation_queue.queue_reconciliation_request(
            planspace, section_number,
            unresolved_contracts, unresolved_anchors,
        )
        self._logger.log(
            f"Section {section_number}: queued reconciliation "
            f"request ({len(unresolved_contracts)} contracts, "
            f"{len(unresolved_anchors)} anchors)"
        )

    def route_blockers(
        self,
        section_number: str,
        proposal_state: ProposalState,
        planspace: Path,
        codespace: Path | None = None,
    ) -> None:
        """Route proposal blockers to their downstream consumers."""
        registry = PathRegistry(planspace)
        signal_dir = registry.signals_dir()

        self._route_user_root_questions(signal_dir, section_number, proposal_state, planspace)

        blocking_research_questions = [
            str(question)
            for question in proposal_state.blocking_research_questions
        ]
        if blocking_research_questions:
            self._route_blocking_research(
                registry, signal_dir, section_number, planspace,
                codespace, blocking_research_questions,
            )

        self._route_shared_seams(signal_dir, section_number, proposal_state)
        self._route_unresolved_contracts(section_number, proposal_state, planspace)
        update_blocker_rollup(planspace)

    def resolve_and_route(
        self,
        section,
        planspace: Path,
        pass_mode: str,
        codespace: Path | None = None,
    ) -> GateResult:
        """Resolve readiness, publish discoveries, and route blockers."""
        registry = PathRegistry(planspace)
        proposal_state_path = registry.proposal_state(section.number)
        proposal_state = ProposalStateRepo(artifact_io=self._artifact_io).load_proposal_state(proposal_state_path)

        self.publish_discoveries(section.number, proposal_state, planspace)

        readiness = self._readiness_resolver.resolve_readiness(planspace, section.number)
        if not readiness.ready:
            blockers = readiness.blockers
            rationale = readiness.rationale or "unknown"
            self._logger.log(
                f"Section {section.number}: execution blocked — "
                f"readiness=false, rationale={rationale}, blockers={len(blockers)}"
            )
            for blocker in blockers:
                # PAT-0009: normalize both proposal-state (type/description) and
                # governance (state/detail) blocker shapes for logging
                btype = blocker.get("type") or blocker.get("state", "unknown")
                bdesc = blocker.get("description") or blocker.get("detail", "")
                self._logger.log(f"  blocker: {btype}: {bdesc}")
            self._communicator.log_summary(
                planspace,
                f"fail:{section.number}:readiness gate blocked ({rationale})",
            )

            self.route_blockers(
                section.number,
                proposal_state,
                planspace,
                codespace=codespace,
            )

            # I5: Persist advisory risk package for blocked sections so
            # artifacts/risk/ has visibility even before execution_ready.
            self._readiness_risk_bridge.persist_readiness_risk(
                section.number, blockers, planspace,
            )

            self._append_proposal_history(
                planspace, section.number, pass_mode,
                execution_ready=False, blockers=blockers,
                disposition="blocked",
            )

            proposal_pass_result = None
            if pass_mode == PASS_MODE_PROPOSAL:
                proposal_pass_result = _build_proposal_pass_result(
                    section.number, str(proposal_state_path), proposal_state,
                    execution_ready=False, blockers=blockers,
                )
            return GateResult(
                ready=False,
                blockers=blockers,
                proposal_pass_result=proposal_pass_result,
            )

        self._append_proposal_history(
            planspace, section.number, pass_mode,
            execution_ready=True, blockers=[],
            disposition="implemented",
        )

        proposal_pass_result = None
        if pass_mode == PASS_MODE_PROPOSAL:
            self._logger.log(
                f"Section {section.number}: proposal pass complete — "
                f"execution_ready=true, deferring implementation"
            )
            proposal_pass_result = _build_proposal_pass_result(
                section.number, str(proposal_state_path), proposal_state,
                execution_ready=True, blockers=[],
            )
        return GateResult(
            ready=True,
            blockers=[],
            proposal_pass_result=proposal_pass_result,
        )

    def _append_proposal_history(
        self,
        planspace: Path,
        section_number: str,
        pass_mode: str,
        *,
        execution_ready: bool,
        blockers: list[dict],
        disposition: str,
    ) -> None:
        """Append the current round's summary to proposal history."""
        # Read existing history to determine the round number
        existing = self._proposal_history.read_history(planspace, section_number)
        round_number = existing.count("## Round ") + 1

        blocker_summaries = []
        for b in blockers:
            btype = b.get("type") or b.get("state", "unknown")
            bdesc = b.get("description") or b.get("detail", "")
            blocker_summaries.append(f"{btype}: {bdesc}")

        round_data = {
            "round_number": round_number,
            "intent_mode": pass_mode,
            "execution_ready": execution_ready,
            "blockers": blocker_summaries,
            "verification_findings": [],
            "disposition": disposition,
        }
        self._proposal_history.append_round(planspace, section_number, round_data)


def _build_proposal_pass_result(
    section_number: str,
    proposal_state_path: str,
    proposal_state: ProposalState,
    *,
    execution_ready: bool,
    blockers: list[dict],
) -> ProposalPassResult:
    """Build a ProposalPassResult for either the ready or blocked path."""
    needs_reconciliation = False
    if not execution_ready:
        needs_reconciliation = bool(
            proposal_state.unresolved_contracts
            or proposal_state.unresolved_anchors,
        )
    return ProposalPassResult(
        section_number=section_number,
        proposal_aligned=True,
        execution_ready=execution_ready,
        blockers=blockers,
        needs_reconciliation=needs_reconciliation,
        proposal_state_path=str(proposal_state_path),
    )
