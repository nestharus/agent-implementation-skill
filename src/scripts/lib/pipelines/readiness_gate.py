from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lib.core.artifact_io import write_json
from lib.core.hash_service import content_hash
from lib.core.path_registry import PathRegistry
from lib.repositories.proposal_state_repository import load_proposal_state
from lib.services.readiness_resolver import resolve_readiness
from lib.repositories.reconciliation_queue import queue_reconciliation_request
from section_loop.communication import mailbox_send, log
from section_loop.section_engine.blockers import (
    _append_open_problem,
    _update_blocker_rollup,
)
from section_loop.types import ProposalPassResult


@dataclass
class ReadinessResult:
    ready: bool
    blockers: list[dict]
    proposal_pass_result: ProposalPassResult | None = None


def publish_discoveries(
    section_number: str,
    proposal_state: dict,
    planspace: Path,
) -> None:
    """Publish durable discovery artifacts from proposal state."""
    scope_delta_dir = PathRegistry(planspace).scope_deltas_dir()
    artifacts = PathRegistry(planspace).artifacts

    for candidate in proposal_state.get("new_section_candidates", []):
        scope_delta_dir.mkdir(parents=True, exist_ok=True)
        cand_text = str(candidate)
        cand_hash = content_hash(cand_text)[:8]
        delta_id = f"delta-{section_number}-candidate-{cand_hash}"
        scope_delta = {
            "delta_id": delta_id,
            "section": section_number,
            "signal": "new_section_candidate",
            "detail": cand_text,
            "requires_root_reframing": False,
            "source": "proposal-state:new_section_candidates",
        }
        delta_path = (
            scope_delta_dir
            / f"section-{section_number}-candidate-{cand_hash}-scope-delta.json"
        )
        write_json(delta_path, scope_delta)
        log(
            f"Section {section_number}: wrote scope-delta for "
            f"new_section_candidate ({cand_hash})"
        )

    for question in proposal_state.get("research_questions", []):
        _append_open_problem(
            planspace,
            section_number,
            str(question),
            "proposal-state:research_question",
        )
    rq_list = proposal_state.get("research_questions", [])
    if rq_list:
        open_problems_dir = artifacts / "open-problems"
        open_problems_dir.mkdir(parents=True, exist_ok=True)
        rq_artifact = {
            "section": section_number,
            "research_questions": [str(q) for q in rq_list],
            "source": "proposal-state",
        }
        rq_path = open_problems_dir / f"section-{section_number}-research-questions.json"
        write_json(rq_path, rq_artifact)
        log(
            f"Section {section_number}: wrote {len(rq_list)} "
            f"research questions to open-problems artifact"
        )


def route_blockers(
    section_number: str,
    proposal_state: dict,
    planspace: Path,
    parent: str,
) -> None:
    """Route proposal blockers to their downstream consumers."""
    del parent
    artifacts = PathRegistry(planspace).artifacts
    signal_dir = artifacts / "signals"
    signal_dir.mkdir(parents=True, exist_ok=True)

    for i, question in enumerate(proposal_state.get("user_root_questions", [])):
        q_signal = {
            "state": "need_decision",
            "section": section_number,
            "detail": str(question),
            "needs": "User/parent decision on this question",
            "why_blocked": (
                "Proposal has an unresolved user-root question "
                "that must be answered before implementation"
            ),
            "source": "proposal-state:user_root_questions",
        }
        sig_path = signal_dir / f"section-{section_number}-proposal-q{i}-signal.json"
        write_json(sig_path, q_signal)
        log(
            f"Section {section_number}: emitted NEED_DECISION "
            f"signal for user_root_question[{i}]"
        )

    for i, question in enumerate(
        proposal_state.get("blocking_research_questions", [])
    ):
        research_signal = {
            "state": "needs_parent",
            "section": section_number,
            "detail": str(question),
            "needs": "Parent/coordination answer to this blocking research question",
            "why_blocked": (
                "Architectural direction depends on resolving this "
                "blocking research question before implementation"
            ),
            "source": "proposal-state:blocking_research_questions",
        }
        sig_path = (
            signal_dir
            / f"section-{section_number}-blocking-research-{i}-signal.json"
        )
        write_json(sig_path, research_signal)
        log(
            f"Section {section_number}: emitted NEEDS_PARENT "
            f"signal for blocking_research_question[{i}]"
        )

    for i, seam in enumerate(proposal_state.get("shared_seam_candidates", [])):
        trigger = {
            "section": section_number,
            "seam": str(seam),
            "source": "proposal-state:shared_seam_candidates",
            "trigger_type": "shared_seam",
        }
        trigger_path = signal_dir / f"substrate-trigger-{section_number}-{i:02d}.json"
        write_json(trigger_path, trigger)
        log(
            f"Section {section_number}: wrote substrate-trigger "
            f"for shared_seam_candidate[{i}]"
        )

    for i, seam in enumerate(proposal_state.get("shared_seam_candidates", [])):
        seam_signal = {
            "state": "needs_parent",
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
        write_json(sig_path, seam_signal)

    unresolved_contracts = [
        str(contract) for contract in proposal_state.get("unresolved_contracts", [])
    ]
    unresolved_anchors = [
        str(anchor) for anchor in proposal_state.get("unresolved_anchors", [])
    ]
    if unresolved_contracts or unresolved_anchors:
        queue_reconciliation_request(
            artifacts,
            section_number,
            unresolved_contracts,
            unresolved_anchors,
        )
        log(
            f"Section {section_number}: queued reconciliation "
            f"request ({len(unresolved_contracts)} contracts, "
            f"{len(unresolved_anchors)} anchors)"
        )

    _update_blocker_rollup(planspace)


def resolve_and_route(
    section,
    planspace: Path,
    parent: str,
    pass_mode: str,
) -> ReadinessResult:
    """Resolve readiness, publish discoveries, and route blockers."""
    artifacts = PathRegistry(planspace).artifacts
    proposal_state_path = (
        artifacts / "proposals" / f"section-{section.number}-proposal-state.json"
    )
    proposal_state = load_proposal_state(proposal_state_path)

    publish_discoveries(section.number, proposal_state, planspace)

    readiness = resolve_readiness(artifacts, section.number)
    if not readiness.get("ready"):
        blockers = readiness.get("blockers", [])
        rationale = readiness.get("rationale", "unknown")
        log(
            f"Section {section.number}: execution blocked — "
            f"readiness=false, rationale={rationale}, blockers={len(blockers)}"
        )
        for blocker in blockers:
            log(f"  blocker: {blocker.get('type')}: {blocker.get('description')}")
        mailbox_send(
            planspace,
            parent,
            f"fail:{section.number}:readiness gate blocked ({rationale})",
        )

        route_blockers(section.number, proposal_state, planspace, parent)

        proposal_pass_result = None
        if pass_mode == "proposal":
            unresolved_contracts = proposal_state.get("unresolved_contracts", [])
            unresolved_anchors = proposal_state.get("unresolved_anchors", [])
            proposal_pass_result = ProposalPassResult(
                section_number=section.number,
                proposal_aligned=True,
                execution_ready=False,
                blockers=blockers,
                needs_reconciliation=bool(
                    unresolved_contracts or unresolved_anchors
                ),
                proposal_state_path=str(proposal_state_path),
            )
        return ReadinessResult(
            ready=False,
            blockers=blockers,
            proposal_pass_result=proposal_pass_result,
        )

    proposal_pass_result = None
    if pass_mode == "proposal":
        log(
            f"Section {section.number}: proposal pass complete — "
            f"execution_ready=true, deferring implementation"
        )
        proposal_pass_result = ProposalPassResult(
            section_number=section.number,
            proposal_aligned=True,
            execution_ready=True,
            blockers=[],
            needs_reconciliation=False,
            proposal_state_path=str(proposal_state_path),
        )
    return ReadinessResult(
        ready=True,
        blockers=[],
        proposal_pass_result=proposal_pass_result,
    )
